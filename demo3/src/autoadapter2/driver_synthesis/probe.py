"""Bounded local Python/MuJoCo development probes for Driver Synthesis.

Probe processes run against a public-only package copy in the condition
workspace.  The admitted robot package root is intentionally never placed in
the child environment or passed as a script argument: it may contain private
validation inputs and calibration references.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
import xml.etree.ElementTree as ET

from autoadapter2.libraries import RobotPackage


ProbeCondition = Literal["skeleton-assisted", "from-scratch"]


class ProbeError(RuntimeError):
    """Raised when a development probe request is invalid or cannot be staged."""


class ProbeSourceError(ProbeError):
    """Raised when probe or candidate source crosses the public code boundary."""


@dataclass(frozen=True)
class ProbeBudget:
    """Hard limits shared by one condition's local probe batch."""

    max_requests: int = 12
    timeout_s: float = 30.0
    max_output_chars: int = 24000
    max_steps: int = 4000
    max_sim_time_s: float = 20.0

    def __post_init__(self) -> None:
        if self.max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if self.timeout_s <= 0.0:
            raise ValueError("timeout_s must be positive")
        if self.max_output_chars <= 0:
            raise ValueError("max_output_chars must be positive")
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if self.max_sim_time_s <= 0.0:
            raise ValueError("max_sim_time_s must be positive")


@dataclass(frozen=True)
class ProbeRequest:
    probe_id: str
    script: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, index: int = 0) -> ProbeRequest:
        probe_id = value.get("probe_id")
        script = value.get("script")
        if not isinstance(probe_id, str) or not probe_id.strip():
            raise ProbeError(f"probe_requests[{index}].probe_id must be non-empty")
        if not isinstance(script, str) or not script.strip():
            raise ProbeError(f"probe_requests[{index}].script must be non-empty")
        return cls(probe_id=probe_id.strip(), script=script)


@dataclass(frozen=True)
class PublicProbeWorkspace:
    root: Path
    scene_path: Path
    skeleton_root: Path | None
    python_root: Path | None


_NETWORK_IMPORT_ROOTS = frozenset(
    {
        "socket",
        "urllib",
        "requests",
        "httpx",
        "ftplib",
        "telnetlib",
        "subprocess",
        "importlib",
        "pkgutil",
        "runpy",
        "ctypes",
    }
)
_FILESYSTEM_INTROSPECTION_ROOTS = frozenset(
    {"os", "pathlib", "glob", "shutil", "tempfile", "fileinput", "sys", "inspect", "types"}
)
_DYNAMIC_CALLS = frozenset({"eval", "exec", "compile", "__import__"})
_FORBIDDEN_PATH_LITERALS = (
    "/tasks/private",
    "\\tasks\\private",
    "/reference/",
    "\\reference\\",
    "autoadapter2.harness",
    "autoadapter2/harness",
)
_RESOURCE_FILE_TAGS = frozenset({"mesh", "texture", "hfield", "skin", "model", "sdf"})
_TEXT_SUFFIXES = frozenset({".xml", ".json", ".txt", ".md", ".py", ".yaml", ".yml"})
_PUBLIC_OBSERVATION_PREFIX = "__AUTOADAPTER_PUBLIC_OBSERVATION__="


def _module_path(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def audit_public_source(
    source: str,
    *,
    condition: ProbeCondition | str,
    allow_probe_utilities: bool = False,
) -> None:
    """Reject probe/candidate source that can reach private Framework code."""

    if condition not in {"skeleton-assisted", "from-scratch"}:
        raise ProbeSourceError(f"unknown source-audit condition {condition!r}")
    try:
        tree = ast.parse(source, filename="probe_or_driver.py")
    except SyntaxError as exc:
        raise ProbeSourceError(f"source is not valid Python: {exc.msg}") from exc

    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
            if node.level:
                errors.append("relative imports are forbidden")
        else:
            modules = []
        for module in modules:
            root = module.split(".", 1)[0]
            normal = module.lower()
            if root in _NETWORK_IMPORT_ROOTS:
                errors.append(f"network/process import is forbidden: {module}")
            if root in _FILESYSTEM_INTROSPECTION_ROOTS and not (
                allow_probe_utilities and root in {"os", "pathlib"}
            ):
                errors.append(f"filesystem/introspection import is forbidden: {module}")
            if root == "autoadapter2":
                allowed = condition == "skeleton-assisted" and normal.startswith(
                    "autoadapter2.trusted_skeletons"
                )
                if not allowed:
                    errors.append(
                        "autoadapter2 imports are limited to trusted_skeletons in skeleton-assisted mode"
                    )
                if any(part in normal.split(".") for part in ("harness", "reference", "private")):
                    errors.append(f"private Framework import is forbidden: {module}")
            if any(marker in normal for marker in ("harness", "reference")):
                errors.append(f"private/reference module access is forbidden: {module}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _DYNAMIC_CALLS:
                errors.append(f"dynamic execution is forbidden: {node.func.id}")
        if isinstance(node, ast.Attribute):
            path = _module_path(node).lower()
            if any(part in path.split(".") for part in ("harness", "reference", "private_dir")):
                errors.append(f"private/reference attribute access is forbidden: {path}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.lower()
            if any(marker in value for marker in _FORBIDDEN_PATH_LITERALS):
                errors.append("private/reference path literal is forbidden")
            if value.strip().lower() in {"private", "reference", "harness"}:
                errors.append("private/reference directory literal is forbidden")
    if errors:
        raise ProbeSourceError("; ".join(dict.fromkeys(errors)))


def _inside_assets(path: Path, assets_root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(assets_root.resolve())
    except ValueError as exc:
        raise ProbeError(f"MJCF closure escapes assets/: {path}") from exc
    if not resolved.is_file():
        raise ProbeError(f"MJCF closure file is missing: {resolved}")
    return resolved


def _referenced_file(
    value: str,
    *,
    xml_path: Path,
    assets_root: Path,
    resource_dir: str | None = None,
) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise ProbeError(f"absolute MJCF resource path is forbidden: {value}")
    candidates: list[Path] = []
    if resource_dir:
        resource_path = Path(resource_dir)
        if resource_path.is_absolute():
            raise ProbeError(f"absolute MJCF resource directory is forbidden: {resource_dir}")
        candidates.append(xml_path.parent / resource_path / relative)
    candidates.extend((xml_path.parent / relative, assets_root / relative))
    for candidate in candidates:
        if candidate.exists():
            return _inside_assets(candidate, assets_root)
    raise ProbeError(f"MJCF resource does not resolve inside assets/: {value}")


def resolve_asset_closure(package: RobotPackage) -> tuple[Path, ...]:
    """Resolve only the canonical MJCF include/resource transitive closure."""

    assets_root = (package.root / "assets").resolve()
    entrypoint = _inside_assets(package.mjcf_path, assets_root)
    pending = [entrypoint]
    visited: set[Path] = set()
    ordered: list[Path] = []
    while pending:
        current = _inside_assets(pending.pop(0), assets_root)
        if current in visited:
            continue
        visited.add(current)
        ordered.append(current)
        if current.suffix.lower() != ".xml":
            continue
        try:
            root = ET.fromstring(current.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ET.ParseError) as exc:
            raise ProbeError(f"cannot parse canonical MJCF XML {current}") from exc
        compiler = next(
            (element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "compiler"),
            None,
        )
        meshdir = compiler.get("meshdir") if compiler is not None else None
        texturedir = compiler.get("texturedir") if compiler is not None else None
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            value = element.get("file")
            if not value:
                continue
            if tag == "include":
                referenced = _referenced_file(value, xml_path=current, assets_root=assets_root)
            elif tag in _RESOURCE_FILE_TAGS:
                directory = texturedir if tag == "texture" else meshdir
                referenced = _referenced_file(
                    value,
                    xml_path=current,
                    assets_root=assets_root,
                    resource_dir=directory,
                )
            else:
                continue
            if referenced not in visited:
                pending.append(referenced)
    return tuple(ordered)


def public_asset_closure_manifest(package: RobotPackage) -> dict[str, Any]:
    """Return a JSON-safe manifest containing only the canonical asset closure."""

    assets_root = (package.root / "assets").resolve()
    closure = resolve_asset_closure(package)
    files: list[dict[str, Any]] = []
    for path in closure:
        relative = path.relative_to(assets_root).as_posix()
        item: dict[str, Any] = {"path": relative, "size_bytes": path.stat().st_size}
        if path.suffix.lower() in _TEXT_SUFFIXES:
            try:
                item["text"] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                item["text_unavailable"] = True
        files.append(item)
    entrypoint = _inside_assets(package.mjcf_path, assets_root)
    return {
        "root": "assets",
        "entrypoint": entrypoint.relative_to(assets_root).as_posix(),
        "entrypoint_text": entrypoint.read_text(encoding="utf-8"),
        "files": files,
    }


_BOOTSTRAP = r'''
import json
import os
import runpy
import sys

import mujoco


max_steps = int(os.environ["AUTOADAPTER_PROBE_MAX_STEPS"])
max_sim_time_s = float(os.environ["AUTOADAPTER_PROBE_MAX_SIM_TIME_S"])
step_count = [0]
original_mj_step = mujoco.mj_step
python_root = os.environ.get("AUTOADAPTER_PROBE_PYTHON_ROOT")
if python_root:
    sys.path.insert(0, python_root)
candidate_root = os.environ.get("AUTOADAPTER_PROBE_CANDIDATE_ROOT")
if candidate_root:
    sys.path.insert(0, candidate_root)


def requested_steps(args, kwargs):
    value = kwargs.get("nstep", args[0] if args else 1)
    try:
        steps = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("probe nstep must be an integer") from exc
    if steps < 0:
        raise RuntimeError("probe nstep must not be negative")
    return steps


def bounded_mj_step(model, data, *args, **kwargs):
    steps = requested_steps(args, kwargs)
    if step_count[0] + steps > max_steps:
        raise RuntimeError("probe control-step budget exceeded")
    result = original_mj_step(model, data, *args, **kwargs)
    step_count[0] += steps
    if float(data.time) > max_sim_time_s:
        raise RuntimeError("probe simulated-time budget exceeded")
    return result


mujoco.mj_step = bounded_mj_step
try:
    runpy.run_path(sys.argv[1], run_name="__main__")
finally:
    print("__AUTOADAPTER_PROBE_FACTS__=" + json.dumps({"physics_steps": step_count[0]}))
'''


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise ProbeError(f"public probe input is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _stage_trusted_python_root(workspace: Path, framework_source_root: Path) -> Path:
    """Stage only the trusted skeleton modules and their tiny contract dependency."""

    source_package = framework_source_root / "autoadapter2"
    trusted_source = source_package / "trusted_skeletons"
    contract_source = source_package / "driver_synthesis" / "skeleton_contract.py"
    if not trusted_source.is_dir() or not contract_source.is_file():
        raise ProbeError("trusted skeleton source is unavailable for skeleton-assisted probe")
    destination = workspace / "public_python"
    destination.mkdir(parents=True, exist_ok=True)
    package_root = destination / "autoadapter2"
    (package_root / "trusted_skeletons").mkdir(parents=True, exist_ok=True)
    (package_root / "driver_synthesis").mkdir(parents=True, exist_ok=True)
    (package_root / "__init__.py").write_text(
        '"""Minimal probe-visible AutoAdapter namespace."""\n',
        encoding="utf-8",
    )
    (package_root / "driver_synthesis" / "__init__.py").write_text(
        "from .skeleton_contract import (\n"
        "    INFRASTRUCTURE_NAMES, PrimitiveDescription, SessionBoundSkeleton,\n"
        "    SkeletonContractError, discover_primitives, validate_capability_names,\n"
        "    validate_explicit_capability_methods,\n"
        ")\n",
        encoding="utf-8",
    )
    _copy_file(contract_source, package_root / "driver_synthesis" / "skeleton_contract.py")
    for source in sorted(trusted_source.glob("*.py")):
        if source.is_file():
            _copy_file(source, package_root / "trusted_skeletons" / source.name)
    for forbidden in (
        package_root / "harness",
        package_root / "reference",
        package_root / "libraries",
        package_root / "validation_compiler",
    ):
        if forbidden.exists():
            raise ProbeError(f"forbidden Framework module was staged: {forbidden}")
    return destination


def _asset_entrypoint(package: RobotPackage) -> str:
    assets_root = (package.root / "assets").resolve()
    try:
        return package.mjcf_path.resolve().relative_to(assets_root).as_posix()
    except ValueError as exc:
        raise ProbeError("canonical MJCF entrypoint must be inside package assets") from exc


def prepare_public_probe_workspace(
    package: RobotPackage,
    workspace: str | Path,
    *,
    condition: ProbeCondition | str,
    framework_source_root: str | Path | None = None,
) -> PublicProbeWorkspace:
    """Stage only public package files for a condition-local probe.

    ``workspace`` is owned by the generation condition.  Existing files are
    never treated as an admitted package root; in particular a pre-existing
    ``tasks/private`` or ``reference`` directory makes staging fail closed.
    """

    if condition not in {"skeleton-assisted", "from-scratch"}:
        raise ProbeError(f"unknown probe condition {condition!r}")
    workspace_path = Path(workspace).resolve()
    package_root = package.root.resolve()
    try:
        workspace_path.relative_to(package_root)
        raise ProbeError("probe workspace must not be inside the admitted package root")
    except ValueError:
        pass
    try:
        package_root.relative_to(workspace_path)
        raise ProbeError("probe workspace must not contain the admitted package root")
    except ValueError:
        pass
    if framework_source_root is not None:
        framework_root = Path(framework_source_root).resolve()
        try:
            framework_root.relative_to(package_root)
            raise ProbeError("trusted staging source must not be inside the admitted package root")
        except ValueError:
            pass
        try:
            package_root.relative_to(framework_root)
            raise ProbeError("trusted staging source must not contain the admitted package root")
        except ValueError:
            pass
    destination = workspace_path / "public_package"
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "tasks" / "private").exists() or (destination / "reference").exists():
        raise ProbeError("public probe workspace already contains a private/reference directory")

    assets_source = (package.root / "assets").resolve()
    if not assets_source.is_dir():
        raise ProbeError(f"package assets directory is missing: {assets_source}")
    for source in resolve_asset_closure(package):
        relative = source.relative_to(assets_source)
        _copy_file(source, destination / "assets" / relative)

    _copy_file(package.root / "morphology.json", destination / "morphology.json")
    _copy_file(package.root / "tasks" / "catalog.json", destination / "tasks" / "catalog.json")
    _copy_file(package.root / "tasks" / "sources.json", destination / "tasks" / "sources.json")

    skeleton_root: Path | None = None
    python_root: Path | None = None
    if condition == "skeleton-assisted":
        skeleton_root = destination / "skeleton"
        if not package.skeleton_dir.is_dir():
            raise ProbeError("skeleton-assisted probe requires package skeleton source")
        for source in sorted(package.skeleton_dir.rglob("*")):
            if source.is_file():
                _copy_file(source, skeleton_root / source.relative_to(package.skeleton_dir))
        if framework_source_root is None:
            raise ProbeError("skeleton-assisted probe needs trusted skeleton source root")
        python_root = _stage_trusted_python_root(
            destination.parent,
            Path(framework_source_root).resolve(),
        )

    if (destination / "tasks" / "private").exists() or (destination / "reference").exists():
        raise ProbeError("private/reference files leaked into public probe workspace")
    scene_path = destination / "assets" / Path(_asset_entrypoint(package))
    if not scene_path.is_file():
        raise ProbeError("staged public canonical scene is missing")
    return PublicProbeWorkspace(
        root=destination,
        scene_path=scene_path,
        skeleton_root=skeleton_root,
        python_root=python_root,
    )


def _normalise_requests(
    requests: Sequence[ProbeRequest | Mapping[str, Any]],
    *,
    budget: ProbeBudget,
) -> tuple[ProbeRequest, ...]:
    if len(requests) > budget.max_requests:
        raise ProbeError(
            f"probe request count {len(requests)} exceeds budget {budget.max_requests}"
        )
    normalised: list[ProbeRequest] = []
    seen: set[str] = set()
    for index, value in enumerate(requests):
        request = value if isinstance(value, ProbeRequest) else ProbeRequest.from_mapping(value, index=index)
        if request.probe_id in seen:
            raise ProbeError(f"duplicate probe_id {request.probe_id!r}")
        if not request.probe_id.replace("_", "").replace("-", "").isalnum():
            raise ProbeError(f"probe_id {request.probe_id!r} contains unsafe filename characters")
        seen.add(request.probe_id)
        normalised.append(request)
    return tuple(normalised)


def _bounded_text(value: bytes, limit: int) -> tuple[str, bool, int]:
    decoded = value.decode("utf-8", errors="replace")
    if len(decoded) <= limit:
        return decoded, False, len(decoded)
    return decoded[:limit] + "\n<probe output truncated>", True, len(decoded)


def _probe_environment(
    *,
    public_workspace: PublicProbeWorkspace,
    budget: ProbeBudget,
) -> dict[str, str]:
    environment: dict[str, str] = {}
    for name in ("PATH", "TMPDIR", "MUJOCO_GL", "DYLD_LIBRARY_PATH"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "AUTOADAPTER_PROBE_PUBLIC_PACKAGE": str(public_workspace.root),
            "AUTOADAPTER_PROBE_SCENE": str(public_workspace.scene_path),
            "AUTOADAPTER_PROBE_MAX_STEPS": str(budget.max_steps),
            "AUTOADAPTER_PROBE_MAX_SIM_TIME_S": str(budget.max_sim_time_s),
        }
    )
    if public_workspace.skeleton_root is not None:
        environment["AUTOADAPTER_PROBE_SKELETON_ROOT"] = str(public_workspace.skeleton_root)
    if public_workspace.python_root is not None:
        environment["AUTOADAPTER_PROBE_PYTHON_ROOT"] = str(public_workspace.python_root)
    if (public_workspace.root / "driver.py").is_file():
        environment["AUTOADAPTER_PROBE_CANDIDATE_ROOT"] = str(public_workspace.root)
    return environment


def _run_one(
    request: ProbeRequest,
    *,
    public_workspace: PublicProbeWorkspace,
    workspace: Path,
    budget: ProbeBudget,
) -> dict[str, Any]:
    audit_public_source(
        request.script,
        condition="skeleton-assisted" if public_workspace.python_root else "from-scratch",
        allow_probe_utilities=True,
    )
    probe_dir = workspace / "probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    script_path = probe_dir / f"{request.probe_id}.py"
    script_path.write_text(request.script, encoding="utf-8")
    command = [sys.executable, "-I", "-c", _BOOTSTRAP, str(script_path)]
    started = time.monotonic()
    timed_out = False
    spawn_error: str | None = None
    stdout_bytes = b""
    stderr_bytes = b""
    exit_code: int | None = None
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=str(public_workspace.root),
            env=_probe_environment(
                public_workspace=public_workspace,
                budget=budget,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = process.communicate(timeout=budget.timeout_s)
        exit_code = process.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        if process is not None:
            process.kill()
            stdout_bytes, stderr_bytes = process.communicate()
        else:
            stdout_bytes = exc.stdout or b""
            stderr_bytes = exc.stderr or b""
    except OSError as exc:
        spawn_error = f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started
    stdout, stdout_truncated, stdout_total = _bounded_text(stdout_bytes, budget.max_output_chars)
    remaining = max(0, budget.max_output_chars - len(stdout))
    stderr, stderr_truncated, stderr_total = _bounded_text(stderr_bytes, remaining)
    physics_steps: int | None = None
    public_observation: dict[str, Any] | None = None
    clean_stdout_lines: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("__AUTOADAPTER_PROBE_FACTS__="):
            try:
                facts = json.loads(line.split("=", 1)[1])
                if isinstance(facts, Mapping) and isinstance(facts.get("physics_steps"), int):
                    physics_steps = int(facts["physics_steps"])
            except json.JSONDecodeError:
                pass
        elif line.startswith(_PUBLIC_OBSERVATION_PREFIX):
            try:
                observation = json.loads(line.split("=", 1)[1])
                if isinstance(observation, dict):
                    public_observation = observation
            except json.JSONDecodeError:
                pass
        else:
            clean_stdout_lines.append(line)
    return {
        "probe_id": request.probe_id,
        "script_relpath": f"probes/{request.probe_id}.py",
        "exit_code": exit_code,
        "timed_out": timed_out,
        "spawn_error": spawn_error,
        "stdout": "\n".join(clean_stdout_lines),
        "stderr": stderr,
        "stdout_chars": stdout_total,
        "stderr_chars": stderr_total,
        "output_truncated": stdout_truncated or stderr_truncated,
        "elapsed_wall_s": elapsed,
        "step_budget": budget.max_steps,
        "simulated_time_budget_s": budget.max_sim_time_s,
        "physics_steps": physics_steps,
        "public_observation": public_observation,
    }


def run_probes(
    requests: Sequence[ProbeRequest | Mapping[str, Any]],
    *,
    package: RobotPackage,
    workspace: str | Path,
    condition: ProbeCondition | str,
    budget: ProbeBudget = ProbeBudget(),
    source_root: str | Path | None = None,
    candidate_source: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Execute bounded model-requested scripts in a public-only workspace."""

    normalised = _normalise_requests(requests, budget=budget)
    if not normalised:
        return ()
    condition_name = str(condition)
    workspace_path = Path(workspace).resolve()
    package_root = package.root.resolve()
    try:
        workspace_path.relative_to(package_root)
        raise ProbeError("probe workspace must not be inside the admitted package root")
    except ValueError:
        pass
    try:
        package_root.relative_to(workspace_path)
        raise ProbeError("probe workspace must not contain the admitted package root")
    except ValueError:
        pass
    root = Path(source_root).resolve() if source_root is not None else Path(__file__).resolve().parents[2]
    try:
        root.relative_to(package_root)
        raise ProbeError("probe staging source must not point inside the admitted package root")
    except ValueError:
        pass
    try:
        package_root.relative_to(root)
        raise ProbeError("probe staging source must not contain the admitted package root")
    except ValueError:
        pass
    public_workspace = prepare_public_probe_workspace(
        package,
        workspace_path,
        condition=condition_name,
        framework_source_root=root,
    )
    candidate_path = public_workspace.root / "driver.py"
    if candidate_source is None:
        candidate_path.unlink(missing_ok=True)
    else:
        if not isinstance(candidate_source, str) or not candidate_source.strip():
            raise ProbeError("candidate_source must be non-empty Python source")
        audit_public_source(candidate_source, condition=condition_name)
        candidate_path.write_text(candidate_source, encoding="utf-8")
    return tuple(
        _run_one(
            request,
            public_workspace=public_workspace,
            workspace=workspace_path,
            budget=budget,
        )
        for request in normalised
    )


def probe(
    request: ProbeRequest | Mapping[str, Any],
    *,
    package: RobotPackage,
    workspace: str | Path,
    condition: ProbeCondition | str,
    budget: ProbeBudget = ProbeBudget(),
    source_root: str | Path | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for one local development probe."""

    return run_probes(
        [request],
        package=package,
        workspace=workspace,
        condition=condition,
        budget=budget,
        source_root=source_root,
    )[0]


__all__ = [
    "ProbeBudget",
    "ProbeCondition",
    "ProbeError",
    "ProbeRequest",
    "ProbeSourceError",
    "PublicProbeWorkspace",
    "audit_public_source",
    "prepare_public_probe_workspace",
    "probe",
    "run_probes",
]
