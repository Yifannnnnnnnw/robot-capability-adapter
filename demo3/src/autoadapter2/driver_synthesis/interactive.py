"""Public-only development tools for interactive Driver Synthesis."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from autoadapter2.libraries import RobotPackage
from autoadapter2.react import ToolSpec

from .probe import (
    ProbeBudget,
    ProbeError,
    PublicProbeWorkspace,
    audit_public_source,
    prepare_public_probe_workspace,
    run_probes,
)
from .skeleton_contract import validate_capability_names
from .source_check import DriverSourceAudit, audit_driver_source


class DevelopmentSessionError(RuntimeError):
    """Raised when a model tool request is outside its public development session."""


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
        '"""Interface-only stub generated from the sealed Capability Design."""',
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
        capability_task_ids: Mapping[str, Sequence[str]] | None = None,
        seed_interface_stub: bool = False,
        initial_driver_source: str | None = None,
    ) -> None:
        if condition not in {"skeleton-assisted", "from-scratch"}:
            raise DevelopmentSessionError(f"unknown generation condition {condition!r}")
        self.package = package
        self.condition = condition
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        self.source_root = Path(source_root).resolve()
        self.public_workspace: PublicProbeWorkspace = prepare_public_probe_workspace(
            package,
            self.workspace / "staged",
            condition=condition,
            framework_source_root=self.source_root,
        )
        self.candidate_path = self.workspace / "driver.py"
        self.capability_methods = (
            validate_capability_names(capability_methods)
            if capability_methods
            else ()
        )
        self.capability_task_ids = {
            str(name): frozenset(str(task_id) for task_id in task_ids)
            for name, task_ids in (capability_task_ids or {}).items()
        }
        self.probe_requests: list[dict[str, str]] = []
        self.probe_results: list[dict[str, Any]] = []
        self._probe_calls = 0
        self._revision = 0
        self._audited_revision: int | None = None
        self._smoked_revision: dict[str, int] = {}
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
        elif initial_driver_source is not None:
            if not isinstance(initial_driver_source, str) or not initial_driver_source.strip():
                raise DevelopmentSessionError("initial_driver_source must be non-empty")
            self.candidate_path.write_text(initial_driver_source, encoding="utf-8")

    @property
    def revision(self) -> int:
        return self._revision

    def has_successful_physics_probe(self) -> bool:
        return any(_successful_probe(result, require_physics=True) for result in self.probe_results)

    def _development_status(self) -> dict[str, Any]:
        missing = [
            method
            for method in self.capability_methods
            if self._smoked_revision.get(method) != self._revision
        ]
        return {
            "revision": self._revision,
            "probe_calls_used": self._probe_calls,
            "probe_calls_remaining": max(0, self.budget.max_requests - self._probe_calls),
            "missing_current_revision_smokes": missing,
        }

    def _reserve_required_smokes(self) -> None:
        status = self._development_status()
        missing = status["missing_current_revision_smokes"]
        remaining = int(status["probe_calls_remaining"])
        if missing and remaining <= len(missing):
            raise ProbeError(
                f"the remaining {remaining} development probe calls are reserved for "
                f"one smoke_driver call per missing capability: {missing}"
            )

    def _candidate_source(self) -> str:
        if not self.candidate_path.is_file():
            raise DevelopmentSessionError("driver.py has not been written")
        source = self.candidate_path.read_text(encoding="utf-8")
        if not source.strip():
            raise DevelopmentSessionError("driver.py is empty")
        return source

    def list_public_files(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        files = []
        for path in sorted(self.public_workspace.root.rglob("*")):
            if path.is_file():
                files.append(
                    {
                        "path": path.relative_to(self.public_workspace.root).as_posix(),
                        "size_bytes": path.stat().st_size,
                    }
                )
        return {"root": "public_package", "files": files}

    def read_public_file(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        relative = arguments.get("path")
        if not isinstance(relative, str) or not relative.strip():
            raise DevelopmentSessionError("path must be a non-empty relative public path")
        candidate = (self.public_workspace.root / relative).resolve()
        try:
            candidate.relative_to(self.public_workspace.root.resolve())
        except ValueError as exc:
            raise DevelopmentSessionError("public file path escapes the staged package") from exc
        if not candidate.is_file():
            raise DevelopmentSessionError(f"public file does not exist: {relative}")
        if candidate.stat().st_size > 200_000:
            raise DevelopmentSessionError("public file is too large for the model tool")
        try:
            content = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise DevelopmentSessionError("public file is not UTF-8 text") from exc
        return {"path": relative, "content": content}

    def _run_probe(
        self,
        *,
        probe_id: str,
        script: str,
        candidate_source: str | None,
    ) -> dict[str, Any]:
        if self._probe_calls >= self.budget.max_requests:
            raise ProbeError(
                f"development probe budget exhausted at {self.budget.max_requests} calls"
            )
        self._probe_calls += 1
        request = {"probe_id": probe_id, "script": script}
        result = run_probes(
            (request,),
            package=self.package,
            workspace=self.workspace / "probe-runtime",
            condition=self.condition,
            budget=replace(self.budget, max_requests=1),
            source_root=self.source_root,
            candidate_source=candidate_source,
        )[0]
        self.probe_requests.append(request)
        result = dict(result)
        result["development_status"] = self._development_status()
        self.probe_results.append(result)
        return result

    def run_mujoco_probe(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        probe_id = arguments.get("probe_id")
        script = arguments.get("script")
        if not isinstance(probe_id, str) or not probe_id.strip():
            raise DevelopmentSessionError("probe_id must be a non-empty string")
        if not isinstance(script, str) or not script.strip():
            raise DevelopmentSessionError("script must be non-empty Python source")
        self._reserve_required_smokes()
        source = self._candidate_source() if self.candidate_path.is_file() else None
        return self._run_probe(
            probe_id=probe_id.strip(),
            script=script,
            candidate_source=source,
        )

    def write_driver(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        source = arguments.get("source")
        if not isinstance(source, str) or not source.strip():
            raise DevelopmentSessionError("source must contain a complete driver.py")
        if len(source) > 200_000:
            raise DevelopmentSessionError("driver.py exceeds the 200000-character limit")
        remaining = self.budget.max_requests - self._probe_calls
        if self.capability_methods and remaining < len(self.capability_methods):
            raise DevelopmentSessionError(
                f"cannot create a new revision with {remaining} probe calls remaining; "
                f"{len(self.capability_methods)} capability smokes would be required"
            )
        self.candidate_path.write_text(source, encoding="utf-8")
        self._revision += 1
        self._audited_revision = None
        self._smoked_revision.clear()
        return {
            "path": "driver.py",
            "revision": self._revision,
            "characters": len(source),
            "development_status": self._development_status(),
        }

    def read_driver(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "path": "driver.py",
            "revision": self._revision,
            "source": self._candidate_source(),
            "development_status": self._development_status(),
        }

    def _audit_candidate(self) -> DriverSourceAudit:
        source = self._candidate_source()
        audit = audit_driver_source(
            source,
            condition=self.condition,  # type: ignore[arg-type]
            capability_methods=self.capability_methods,
        )
        audit_public_source(source, condition=self.condition)
        compile(source, "driver.py", "exec")
        self._audited_revision = self._revision
        return audit

    def audit_driver(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "revision": self._revision,
            "audit": asdict(self._audit_candidate()),
            "development_status": self._development_status(),
        }

    def import_driver(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        self._audit_candidate()
        self._reserve_required_smokes()
        script = (
            "import os\n"
            "import mujoco\n"
            "import driver\n"
            "model = mujoco.MjModel.from_xml_path(os.environ['AUTOADAPTER_PROBE_SCENE'])\n"
            "data = mujoco.MjData(model)\n"
            "candidate = driver.build(model=model, data=data)\n"
            "if candidate.model is not model or candidate.data is not data:\n"
            "    raise RuntimeError('driver did not retain canonical model/data')\n"
            "print('candidate_import_and_build_ok')\n"
        )
        result = self._run_probe(
            probe_id=f"import-r{self._revision}-{self._probe_calls + 1}",
            script=script,
            candidate_source=self._candidate_source(),
        )
        return {
            "revision": self._revision,
            "successful": _successful_probe(result, require_physics=False),
            "result": result,
        }

    def smoke_driver(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        method_name = arguments.get("method_name")
        request = arguments.get("request")
        if not isinstance(method_name, str) or method_name not in self.capability_methods:
            raise DevelopmentSessionError("method_name must be one sealed capability method")
        if not isinstance(request, Mapping):
            raise DevelopmentSessionError("request must be one public request object")
        task_id = request.get("task_id")
        task_parameters = request.get("task_parameters")
        if not isinstance(task_id, str) or not isinstance(task_parameters, Mapping):
            raise DevelopmentSessionError(
                "request requires string task_id and object task_parameters"
            )
        allowed_tasks = self.capability_task_ids.get(method_name, frozenset())
        if allowed_tasks and task_id not in allowed_tasks:
            raise DevelopmentSessionError(
                f"task_id {task_id!r} is not covered by capability {method_name!r}"
            )
        self._audit_candidate()
        request_json = json.dumps(dict(request), ensure_ascii=True, sort_keys=True)
        script = (
            "import json\n"
            "import os\n"
            "import mujoco\n"
            "import driver\n"
            "model = mujoco.MjModel.from_xml_path(os.environ['AUTOADAPTER_PROBE_SCENE'])\n"
            "data = mujoco.MjData(model)\n"
            "candidate = driver.build(model=model, data=data)\n"
            "if candidate.model is not model or candidate.data is not data:\n"
            "    raise RuntimeError('driver did not retain canonical model/data')\n"
            f"request = json.loads({request_json!r})\n"
            f"returned = candidate.{method_name}(request=request)\n"
            "print('candidate_return_type=' + type(returned).__name__)\n"
            "print('candidate_time_s=' + str(data.time))\n"
        )
        result = self._run_probe(
            probe_id=f"smoke-{method_name}-r{self._revision}-{self._probe_calls + 1}",
            script=script,
            candidate_source=self._candidate_source(),
        )
        successful = _successful_probe(result, require_physics=True)
        if successful:
            self._smoked_revision[method_name] = self._revision
        result["development_status"] = self._development_status()
        return {
            "revision": self._revision,
            "method_name": method_name,
            "successful": successful,
            "result": result,
            "development_status": self._development_status(),
        }

    def submit_driver(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        note = arguments.get("note", "")
        if not isinstance(note, str):
            raise DevelopmentSessionError("submission note must be text")
        audit = self._audit_candidate()
        missing = [
            method
            for method in self.capability_methods
            if self._smoked_revision.get(method) != self._revision
        ]
        if missing:
            status = self._development_status()
            raise DevelopmentSessionError(
                f"current driver revision lacks successful public physics smoke for {missing}; "
                f"development status: {status}"
            )
        return {
            "driver_filename": "driver.py",
            "driver_source": self._candidate_source(),
            "note": note,
            "development_revision": self._revision,
            "source_audit": asdict(audit),
            "smoked_methods": list(self.capability_methods),
            "development_status": self._development_status(),
        }

    def public_tools(self) -> tuple[ToolSpec, ...]:
        return (
            ToolSpec(
                "list_public_files",
                "List files in the condition-local staged public robot package.",
                _object_schema(),
                self.list_public_files,
            ),
            ToolSpec(
                "read_public_file",
                "Read one UTF-8 file by path relative to the staged public package.",
                _object_schema(
                    {"path": {"type": "string"}},
                    required=("path",),
                ),
                self.read_public_file,
            ),
            ToolSpec(
                "run_mujoco_probe",
                "Run one complete Python script against the staged canonical public MuJoCo scene. A current model-authored driver.py is importable as driver when present.",
                _object_schema(
                    {
                        "probe_id": {"type": "string"},
                        "script": {"type": "string"},
                    },
                    required=("probe_id", "script"),
                ),
                self.run_mujoco_probe,
            ),
        )

    def driver_tools(self) -> tuple[ToolSpec, ...]:
        request_schema = _object_schema(
            {
                "task_id": {"type": "string"},
                "task_parameters": {"type": "object"},
            },
            required=("task_id", "task_parameters"),
        )
        return (
            *self.public_tools(),
            ToolSpec(
                "write_driver",
                "Write or completely replace driver.py. The initial file is an interface-only stub with no control implementation. Every write creates a new revision and invalidates all prior capability smokes.",
                _object_schema(
                    {"source": {"type": "string"}},
                    required=("source",),
                ),
                self.write_driver,
            ),
            ToolSpec(
                "read_driver",
                "Read the current model-authored driver.py and its revision.",
                _object_schema(),
                self.read_driver,
            ),
            ToolSpec(
                "audit_driver",
                "Compile and source-audit the current driver, including all exact sealed capability names and the (self, request) ABI.",
                _object_schema(),
                self.audit_driver,
            ),
            ToolSpec(
                "import_driver",
                "Import and build the current driver against a fresh canonical public MuJoCo model/data session.",
                _object_schema(),
                self.import_driver,
            ),
            ToolSpec(
                "smoke_driver",
                "Import, build, and invoke one exact sealed capability with a public request. Success requires actuator-driven real MuJoCo physics steps. Run this for every capability on the current revision; each result reports the remaining probe budget and missing smokes.",
                _object_schema(
                    {
                        "method_name": {
                            "type": "string",
                            "enum": list(self.capability_methods),
                        },
                        "request": request_schema,
                    },
                    required=("method_name", "request"),
                ),
                self.smoke_driver,
            ),
            ToolSpec(
                "submit_driver",
                "Explicitly submit the current model-authored driver revision. Submission is accepted only after source audit and successful public physics smoke of every sealed capability.",
                _object_schema({"note": {"type": "string"}}),
                self.submit_driver,
                terminal=True,
            ),
        )


def capability_task_ids(design: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list):
        return result
    for capability in capabilities:
        if not isinstance(capability, Mapping):
            continue
        method = capability.get("method_name")
        task_ids = capability.get("covered_task_ids", [])
        if isinstance(method, str) and isinstance(task_ids, list):
            result[method] = tuple(str(task_id) for task_id in task_ids)
    return result


__all__ = [
    "DevelopmentSessionError",
    "PublicDevelopmentSession",
    "capability_task_ids",
    "render_interface_stub",
]
