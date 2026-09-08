"""Public-only development tools for interactive Driver Synthesis."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from autoadapter2.libraries import RobotPackage
from autoadapter2.react import ToolSpec

from .probe import (
    ProbeBudget,
    ProbeError,
    PublicProbeWorkspace,
    PersistentPythonSession,
    audit_public_source,
    prepare_public_probe_workspace,
)
from .skeleton_contract import validate_capability_names
from .source_check import DriverSourceAudit, audit_driver_source


class DevelopmentSessionError(RuntimeError):
    """Raised when a model tool request is outside its public development session."""


MAX_FILE_CHARS = 200_000
MAX_EXECUTE_PYTHON_CHARS = 200_000


@dataclass
class DriverDevelopmentConversation:
    """Caller-owned development history and tools, closed after the last trial."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    session: PublicDevelopmentSession | None = None

    def __enter__(self) -> DriverDevelopmentConversation:
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.session is not None:
            self.session.close()


def _object_schema(
    properties: Mapping[str, Any] | None = None,
    *,
    required: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties or {}),
        "required": list(required),
        "additionalProperties": False,
    }


def _successful_probe(result: Mapping[str, Any], *, require_physics: bool) -> bool:
    return (
        result.get("exit_code") == 0
        and not bool(result.get("timed_out"))
        and result.get("spawn_error") in {None, ""}
        and (
            not require_physics
            or (
                isinstance(result.get("physics_steps"), int)
                and int(result["physics_steps"]) > 0
            )
        )
    )


def render_interface_stub(capability_methods: Sequence[str]) -> str:
    """Render only the sealed callable surface, with no control implementation."""

    methods = validate_capability_names(capability_methods)
    lines = [
        '"""Interface-only stub generated from the sealed Capability Design.',
        "",
        "Every request is a plain Python mapping. Access sealed fields with",
        'request["field"] or mapping read methods, never request.field.',
        '"""',
        "",
        "",
        "class CapabilityDriver:",
    ]
    for method in methods:
        lines.extend(
            (
                f"    def {method}(self, request):",
                f'        raise NotImplementedError("implement {method}")',
                "",
            )
        )
    lines.extend(
        (
            "",
            "def build(model, data):",
            '    raise NotImplementedError("implement build")',
            "",
        )
    )
    return "\n".join(lines)


class PublicDevelopmentSession:
    """One condition-local, budgeted view of public files and candidate source."""

    def __init__(
        self,
        *,
        package: RobotPackage,
        condition: str,
        workspace: str | Path,
        budget: ProbeBudget,
        source_root: str | Path,
        capability_methods: Sequence[str] = (),
        seed_interface_stub: bool = False,
        initial_driver_source: str | None = None,
    ) -> None:
        if condition not in {"skeleton-assisted", "from-scratch"}:
            raise DevelopmentSessionError(f"unknown generation condition {condition!r}")
        self.package = package
        self.condition = condition
        self.public_file_paging = False
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        # Keep Framework-staged public inputs outside the model-writable
        # artifact workspace.  The file tool, Python audit hook, and Seatbelt
        # profile can therefore share one simple writable root without an
        # overlapping read-only subtree.
        self._staging_directory = TemporaryDirectory(
            prefix=f".{self.workspace.name}-public-",
            dir=self.workspace.parent,
        )
        try:
            self.public_workspace: PublicProbeWorkspace = prepare_public_probe_workspace(
                package,
                Path(self._staging_directory.name),
                condition=condition,
                framework_source_root=Path(source_root).resolve(),
            )
        except BaseException:
            self._staging_directory.cleanup()
            raise
        self.candidate_path = self.workspace / "driver.py"
        self.capability_methods = (
            validate_capability_names(capability_methods)
            if capability_methods
            else ()
        )
        self.budget = budget
        self.probe_requests: list[dict[str, str]] = []
        self.probe_results: list[dict[str, Any]] = []
        self._python_session: PersistentPythonSession | None = None
        self._driver_dirty = False
        self._probe_calls = 0
        self._revision = 0
        if seed_interface_stub and initial_driver_source is not None:
            raise DevelopmentSessionError(
                "seed_interface_stub and initial_driver_source are mutually exclusive"
            )
        if seed_interface_stub:
            if not self.capability_methods:
                raise DevelopmentSessionError(
                    "an interface stub requires sealed capability methods"
                )
            self.candidate_path.write_text(
                render_interface_stub(self.capability_methods),
                encoding="utf-8",
            )
            self._sync_candidate_to_public()
        elif initial_driver_source is not None:
            if not isinstance(initial_driver_source, str) or not initial_driver_source.strip():
                raise DevelopmentSessionError("initial_driver_source must be non-empty")
            self.candidate_path.write_text(initial_driver_source, encoding="utf-8")
            self._sync_candidate_to_public()

    def close(self) -> None:
        """Close the development execute_python process, if started."""

        python_session = getattr(self, "_python_session", None)
        if python_session is not None:
            python_session.close()
            self._python_session = None
        staging_directory = getattr(self, "_staging_directory", None)
        if staging_directory is not None:
            staging_directory.cleanup()
            self._staging_directory = None

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        self.close()

    def _sync_candidate_to_public(self, source: str | None = None) -> None:
        """Expose only the current candidate to the public probe process."""

        destination = self.public_workspace.root / "driver.py"
        if source is not None:
            destination.write_text(source, encoding="utf-8")
        elif self.candidate_path.is_file():
            destination.write_text(
                self.candidate_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        else:
            destination.unlink(missing_ok=True)
        self._driver_dirty = True

    def _require_writable_path(self, relative: str, destination: Path) -> None:
        """Reject model writes into the Framework-owned public projection."""

        if Path(relative).parts[0] == "staged":
            raise DevelopmentSessionError(
                "public package, scene, and trusted skeleton inputs are read-only"
            )
        protected_roots = [self.public_workspace.root]
        if self.public_workspace.python_root is not None:
            protected_roots.append(self.public_workspace.python_root)
        for protected_root in protected_roots:
            try:
                destination.resolve().relative_to(protected_root.resolve())
            except ValueError:
                continue
            raise DevelopmentSessionError(
                "public package, scene, and trusted skeleton inputs are read-only"
            )
        if relative != "driver.py":
            public_candidate = self._under(
                self.public_workspace.root,
                relative,
                label="public file path",
            )
            if public_candidate.exists():
                raise DevelopmentSessionError(
                    "public package, scene, and trusted skeleton inputs are read-only"
                )

    def _ensure_python_session(self) -> PersistentPythonSession:
        if self._python_session is None:
            self._python_session = PersistentPythonSession(
                public_workspace=self.public_workspace,
                workspace=self.workspace,
                condition=self.condition,
                budget=self.budget,
            )
        return self._python_session

    @property
    def revision(self) -> int:
        return self._revision

    def has_successful_physics_probe(self) -> bool:
        return any(_successful_probe(result, require_physics=True) for result in self.probe_results)

    def _development_status(self) -> dict[str, Any]:
        limit = self.budget.max_requests
        return {
            "revision": self._revision,
            "probe_calls_used": self._probe_calls,
            "probe_calls_limit": limit,
            "probe_calls_remaining": (
                None if limit is None else max(0, limit - self._probe_calls)
            ),
        }

    def _candidate_source(self) -> str:
        if not self.candidate_path.is_file():
            raise DevelopmentSessionError("driver.py has not been written")
        source = self.candidate_path.read_text(encoding="utf-8")
        if not source.strip():
            raise DevelopmentSessionError("driver.py is empty")
        return source

    @staticmethod
    def _safe_relative_path(value: Any, *, label: str = "path") -> str:
        if not isinstance(value, str) or not value.strip():
            raise DevelopmentSessionError(f"{label} must be a non-empty relative path")
        relative = value.strip().replace("\\", "/")
        path = Path(relative)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise DevelopmentSessionError(f"{label} must remain relative to the allowed root")
        return "/".join(path.parts)

    @staticmethod
    def _under(root: Path, relative: str, *, label: str) -> Path:
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise DevelopmentSessionError(f"{label} escapes its allowed root") from exc
        return candidate

    def _read_file_path(self, relative: str) -> tuple[Path, str]:
        """Resolve a model path against public projection or stage workspace."""

        if self.condition == "from-scratch" and "skeleton" in Path(relative).parts:
            raise DevelopmentSessionError("from-scratch cannot read skeleton files")
        workspace_candidate = self._under(self.workspace, relative, label="file path")
        if workspace_candidate.is_file():
            return workspace_candidate, "workspace"
        public_candidate = self._under(
            self.public_workspace.root, relative, label="public file path"
        )
        if public_candidate.is_file():
            return public_candidate, "public_package"
        raise DevelopmentSessionError(f"file does not exist in the public package or workspace: {relative}")

    def read_file(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Read a UTF-8 file only from the public projection or stage workspace."""

        relative = self._safe_relative_path(arguments.get("path"))
        path, root_name = self._read_file_path(relative)
        if path.stat().st_size > MAX_FILE_CHARS:
            raise DevelopmentSessionError("file is too large for the model tool")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise DevelopmentSessionError("file is not UTF-8 text") from exc
        if self.public_file_paging and root_name == "public_package":
            offset = arguments.get("offset", 0)
            if type(offset) is not int or not 0 <= offset <= len(content):
                raise DevelopmentSessionError("offset must be a character position inside the file")
            page = content[offset:offset + 16000]
            # Leave room for the tool envelope under ReAct's existing 24000-char
            # observation cap, including JSON escapes in non-ASCII material.
            while len(json.dumps(page, ensure_ascii=True)) > 18000:
                page = page[:len(page) // 2]
            end = offset + len(page)
            return {
                "path": relative, "root": root_name, "content": page,
                "offset": offset, "total_chars": len(content),
                "next_offset": end if end < len(content) else None,
            }
        return {"path": relative, "root": root_name, "content": content}

    def write_file(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Write a UTF-8 file strictly inside the condition-local workspace."""

        relative = self._safe_relative_path(arguments.get("path"))
        content = arguments.get("content")
        append = arguments.get("append", False)
        if not isinstance(content, str):
            raise DevelopmentSessionError("content must be text")
        if not isinstance(append, bool):
            raise DevelopmentSessionError("append must be boolean")
        if len(content) > MAX_FILE_CHARS:
            raise DevelopmentSessionError("file exceeds the 200000-character limit")
        destination = self._under(self.workspace, relative, label="workspace file path")
        self._require_writable_path(relative, destination)
        before = destination.read_text(encoding="utf-8") if destination.is_file() else None
        combined = (before or "") + content if append else content
        if len(combined) > MAX_FILE_CHARS:
            raise DevelopmentSessionError("file exceeds the 200000-character limit")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(combined, encoding="utf-8")
        if relative == "driver.py":
            self.candidate_path = destination
            self._revision += 1 if before != combined else 0
            self._sync_candidate_to_public()
        return {
            "path": relative,
            "bytes_written": len(content.encode("utf-8")),
            "file_chars": len(combined),
            "append": append,
            "source_changed": before != combined,
            "revision": self._revision if relative == "driver.py" else None,
        }

    def execute_python(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Execute public-only Python/MuJoCo code in the persistent phase session."""

        code = arguments.get("code")
        if not isinstance(code, str) or not code.strip():
            raise DevelopmentSessionError("code must be non-empty Python source")
        if len(code) > MAX_EXECUTE_PYTHON_CHARS:
            raise DevelopmentSessionError("execute_python code exceeds the character limit")
        if (
            self.budget.max_requests is not None
            and self._probe_calls >= self.budget.max_requests
        ):
            raise ProbeError(
                f"execute_python probe budget exhausted at {self.budget.max_requests} calls"
            )
        self._probe_calls += 1
        session = self._ensure_python_session()
        result = dict(session.execute(code, invalidate_driver=self._driver_dirty))
        if not bool(result.get("session_lost")):
            self._driver_dirty = False
        result.update(
            {
                "probe_id": f"execute-python-{self._probe_calls}",
                "script_relpath": f"execute_python/{self._probe_calls}.py",
                "development_status": self._development_status(),
            }
        )
        self.probe_requests.append(
            {"probe_id": result["probe_id"], "script": code}
        )
        self.probe_results.append(result)
        return result

    def list_skeletons(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self.condition != "skeleton-assisted":
            raise DevelopmentSessionError("skeleton tools are unavailable in from-scratch mode")
        files = []
        root = self.public_workspace.skeleton_root
        if root is None or not root.is_dir():
            raise DevelopmentSessionError("skeleton projection is unavailable")
        for path in sorted(root.rglob("*.py")):
            if path.is_file():
                files.append({"name": path.relative_to(root).as_posix(), "path": f"skeleton/{path.relative_to(root).as_posix()}"})
        return {"root": "skeleton", "skeletons": files}

    def inspect_skeleton(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self.condition != "skeleton-assisted":
            raise DevelopmentSessionError("skeleton tools are unavailable in from-scratch mode")
        name = arguments.get("name", arguments.get("skeleton_name"))
        relative = self._safe_relative_path(name, label="skeleton name")
        if not relative.endswith(".py"):
            relative += ".py"
        root = self.public_workspace.skeleton_root
        if root is None:
            raise DevelopmentSessionError("skeleton projection is unavailable")
        path = self._under(root, relative, label="skeleton path")
        if not path.is_file():
            raise DevelopmentSessionError(f"skeleton does not exist: {relative}")
        if path.stat().st_size > MAX_FILE_CHARS:
            raise DevelopmentSessionError("skeleton source is too large for the model tool")
        if self.public_file_paging:
            page = self.read_file({"path": "skeleton/" + relative})
            return {"name": relative, "source": page.pop("content"), **page}
        return {"name": relative, "source": path.read_text(encoding="utf-8")}

    def _audit_candidate(self) -> tuple[DriverSourceAudit, str]:
        source = self._candidate_source()
        audit = audit_driver_source(
            source,
            condition=self.condition,  # type: ignore[arg-type]
            capability_methods=self.capability_methods,
            candidate_request_boundary=True,
        )
        audit_public_source(source, condition=self.condition)
        compile(source, "driver.py", "exec")
        return audit, source

    def validate_driver_artifact(self, _path: Path | str | None = None) -> dict[str, Any]:
        """Apply source and public import/build checks to ``driver.py``.

        This is the narrow boundary required before Generate/Repair hands the
        artifact back to the caller.  It intentionally does not invoke a
        private Harness, a validation suite, or capability verdict logic.
        """

        audit, audited_source = self._audit_candidate()
        # ``execute_python`` may have edited the canonical workspace file
        # without going through ``write_file``.  Synchronise immediately before
        # the import/build boundary and force the persistent worker to discard
        # any cached ``driver`` module so the audited and imported bytes match.
        self._sync_candidate_to_public(audited_source)
        result = self.execute_python(
            {
                "code": (
                    "import os\n"
                    "import mujoco\n"
                    "import driver\n"
                    "model = mujoco.MjModel.from_xml_path(os.environ['AUTOADAPTER_PROBE_SCENE'])\n"
                    "data = mujoco.MjData(model)\n"
                    "candidate = driver.build(model=model, data=data)\n"
                    f"required_methods = {self.capability_methods!r}\n"
                    "driver_type = object.__getattribute__(candidate, '__class__')\n"
                    "driver_namespace = type.__getattribute__(driver_type, '__dict__')\n"
                    "function_type = type(lambda: None)\n"
                    "for method_name in required_methods:\n"
                    "    member = driver_namespace.get(method_name)\n"
                    "    if type(member) is not function_type:\n"
                    "        raise TypeError('built driver does not explicitly define capability method ' + repr(method_name))\n"
                    "    code = object.__getattribute__(member, '__code__')\n"
                    "    defaults = object.__getattribute__(member, '__defaults__')\n"
                    "    kwdefaults = object.__getattribute__(member, '__kwdefaults__')\n"
                    "    exact_abi = (code.co_argcount == 2 and code.co_posonlyargcount == 0 and code.co_kwonlyargcount == 0 and not (code.co_flags & 12) and code.co_varnames[:2] == ('self', 'request') and defaults is None and kwdefaults is None)\n"
                    "    if not exact_abi:\n"
                    "        raise TypeError('built capability method must have exact signature (self, request): ' + repr(method_name))\n"
                    "print('candidate_import_and_build_ok=' + type(candidate).__name__)\n"
                )
            }
        )
        if not bool(result.get("successful")):
            # The worker's structured error contains the actual terminal
            # exception. Prefer it to a long traceback whose useful tail can
            # be lost again when the artifact loop bounds its observation.
            detail = result.get("error") or result.get("stderr") or result.get("stdout")
            if result.get("timed_out") or result.get("session_lost") or not detail:
                detail = {
                    "detail": detail or "Worker returned no diagnostic text",
                    **{key: result[key] for key in (
                        "timed_out", "session_lost", "elapsed_wall_s",
                        "physics_steps_total", "physics_step_budget_exhausted",
                    ) if key in result},
                }
            raise DevelopmentSessionError(
                f"driver.py public import/build failed: {str(detail)[:2000]}"
            )
        return {
            "valid": True,
            "source_audit": asdict(audit),
            "import": {
                "successful": True,
                "stdout": result.get("stdout", ""),
            },
        }

    def artifact_tools(self, *, include_skeleton: bool = False) -> tuple[ToolSpec, ...]:
        """Return the AA1 file/artifact tool surface for this phase.

        ``read_file``, ``write_file`` and ``execute_python`` are always
        available.  Skeleton discovery is deliberately an explicit opt-in so
        STUDY and from-scratch phases cannot accidentally inspect trusted
        implementation source.
        """

        read_properties: dict[str, Any] = {"path": {"type": "string"}}
        read_description = "Read one UTF-8 file from the public package projection or this condition workspace. Paths are relative and cannot escape either root."
        if self.public_file_paging:
            read_properties["offset"] = {"type": "integer", "minimum": 0}
            read_description += " Public files are paginated: if next_offset is not null, call read_file with that offset to continue."
        tools: list[ToolSpec] = [
            ToolSpec(
                "read_file",
                read_description,
                _object_schema(read_properties, required=("path",)),
                self.read_file,
            ),
            ToolSpec(
                "write_file",
                "Write one UTF-8 file under the condition workspace. For a large canonical artifact, write a first bounded chunk with append=false, then later chunks with append=true; the combined file remains capped at 200000 characters. Framework-staged public package, scene, and trusted skeleton inputs are read-only. Use study.json and driver.py for canonical artifacts.",
                _object_schema(
                    {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "append": {"type": "boolean"},
                    },
                    required=("path", "content"),
                ),
                self.write_file,
            ),
            ToolSpec(
                "execute_python",
                "Execute credential-free public-only Python/MuJoCo code in one persistent development session. Its current directory is this same condition workspace, so relative paths match write_file paths; public scene/package paths are available through the supplied environment. State survives across successful calls. After a timeout or worker exit, the next call uses a clean session and reports session_restarted=true; the session control-step budget is not replenished. Time, output, and control-step budgets remain bounded.",
                _object_schema({"code": {"type": "string"}}, required=("code",)),
                self.execute_python,
            ),
        ]
        if include_skeleton:
            if self.condition != "skeleton-assisted":
                raise DevelopmentSessionError(
                    "skeleton tools may only be enabled for skeleton-assisted stages"
                )
            tools.extend(
                (
                    ToolSpec(
                        "list_skeletons",
                        "List the trusted skeleton sources projected into this skeleton-assisted condition.",
                        _object_schema(),
                        self.list_skeletons,
                    ),
                    ToolSpec(
                        "inspect_skeleton",
                        "Inspect one trusted skeleton source by its relative skeleton name.",
                        _object_schema(
                            {"name": {"type": "string"}}, required=("name",)
                        ),
                        self.inspect_skeleton,
                    ),
                )
            )
        return tuple(tools)


class IsolatedArtifactSession:
    """Workspace-only file tools plus a credential-free Python/MuJoCo session.

    This is the narrower IVC boundary.  Unlike ``PublicDevelopmentSession``
    It never exposes private tasks, reference code, skeletons, or Driver
    source.  IVC may explicitly supply one admitted package, in which case
    ``execute_python`` receives read-only access to that package's public
    ``assets/`` tree; other artifact phases retain the empty MuJoCo scene.
    """

    def __init__(
        self,
        *,
        workspace: str | Path,
        budget: ProbeBudget,
        package: RobotPackage | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        sandbox = self.workspace / ".python_sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        scene_path = sandbox / "empty_scene.xml"
        scene_path.write_text(
            "<mujoco model=\"ivc_calculation_sandbox\"><worldbody/></mujoco>\n",
            encoding="utf-8",
        )
        if package is None:
            self.public_workspace = PublicProbeWorkspace(
                root=sandbox,
                scene_path=scene_path,
                skeleton_root=None,
                python_root=None,
            )
        else:
            assets_root = (package.root / "assets").resolve()
            scene = package.mjcf_path.resolve()
            try:
                scene.relative_to(assets_root)
            except ValueError as exc:
                raise DevelopmentSessionError(
                    "IVC package scene must remain inside public assets"
                ) from exc
            if not assets_root.is_dir() or not scene.is_file():
                raise DevelopmentSessionError(
                    "IVC package public assets are unavailable"
                )
            self.public_workspace = PublicProbeWorkspace(
                root=assets_root,
                scene_path=scene,
                skeleton_root=None,
                python_root=None,
            )
        self.budget = budget
        self._python_session: PersistentPythonSession | None = None
        self._calls = 0

    def close(self) -> None:
        if self._python_session is not None:
            self._python_session.close()
            self._python_session = None

    def _path(self, value: Any) -> tuple[str, Path]:
        relative = PublicDevelopmentSession._safe_relative_path(value)
        path = PublicDevelopmentSession._under(
            self.workspace, relative, label="workspace file path"
        )
        return relative, path

    def read_file(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        relative, path = self._path(arguments.get("path"))
        if not path.is_file():
            raise DevelopmentSessionError(f"workspace file does not exist: {relative}")
        if path.stat().st_size > MAX_FILE_CHARS:
            raise DevelopmentSessionError("file is too large for the model tool")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise DevelopmentSessionError("file is not UTF-8 text") from exc
        return {"path": relative, "root": "workspace", "content": content}

    def write_file(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        relative, path = self._path(arguments.get("path"))
        content = arguments.get("content")
        append = arguments.get("append", False)
        if not isinstance(content, str):
            raise DevelopmentSessionError("content must be text")
        if not isinstance(append, bool):
            raise DevelopmentSessionError("append must be boolean")
        if len(content) > MAX_FILE_CHARS:
            raise DevelopmentSessionError("file exceeds the 200000-character limit")
        before = path.read_text(encoding="utf-8") if path.is_file() else None
        combined = (before or "") + content if append else content
        if len(combined) > MAX_FILE_CHARS:
            raise DevelopmentSessionError("file exceeds the 200000-character limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(combined, encoding="utf-8")
        return {
            "path": relative,
            "bytes_written": len(content.encode("utf-8")),
            "file_chars": len(combined),
            "append": append,
            "source_changed": before != combined,
        }

    def execute_python(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        code = arguments.get("code")
        if not isinstance(code, str) or not code.strip():
            raise DevelopmentSessionError("code must be non-empty Python source")
        if len(code) > MAX_EXECUTE_PYTHON_CHARS:
            raise DevelopmentSessionError("execute_python code exceeds the character limit")
        if self._python_session is None:
            self._python_session = PersistentPythonSession(
                public_workspace=self.public_workspace,
                workspace=self.workspace,
                condition="from-scratch",
                budget=self.budget,
            )
        self._calls += 1
        result = dict(self._python_session.execute(code))
        result["execute_python_call"] = self._calls
        return result

    def artifact_tools(self) -> tuple[ToolSpec, ...]:
        return (
            ToolSpec(
                "read_file",
                "Read one UTF-8 file from this isolated phase workspace.",
                _object_schema({"path": {"type": "string"}}, required=("path",)),
                self.read_file,
            ),
            ToolSpec(
                "write_file",
                "Write one UTF-8 file inside this isolated phase workspace. For a large canonical artifact, use append=false for the first bounded chunk and append=true for later chunks; the combined file remains capped at 200000 characters.",
                _object_schema(
                    {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "append": {"type": "boolean"},
                    },
                    required=("path", "content"),
                ),
                self.write_file,
            ),
            ToolSpec(
                "execute_python",
                "Execute credential-free Python/MuJoCo calculations in one persistent isolated session. Its current directory is this same phase workspace, so relative paths match read_file/write_file paths. Time, output and physics remain bounded.",
                _object_schema({"code": {"type": "string"}}, required=("code",)),
                self.execute_python,
            ),
        )

__all__ = [
    "DevelopmentSessionError",
    "IsolatedArtifactSession",
    "PublicDevelopmentSession",
    "render_interface_stub",
]
