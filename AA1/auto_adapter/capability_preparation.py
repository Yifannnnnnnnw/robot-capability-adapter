# SPDX-License-Identifier: Apache-2.0
"""Bounded task-grounded capability design preparation for AA1.

The original AA1 study is completed before this module is called.  TGCD sees
that public study, the public task catalogue/source records, and metadata from
the actual MJCF.  It submits one capability-v2 design through a validating
AA1 ReAct tool.  This module intentionally has no AA2 imports.
"""

from __future__ import annotations

import copy
import json
import keyword
import math
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .agent.react_loop import ReactLoop
from .agent.tools import (
    make_local_exec_tool,
    make_read_file_tool,
    make_write_file_tool,
)


TASK_LIBRARY_ROOT = Path(__file__).resolve().parent / "task_libraries"
CAPABILITY_PROTOCOL_VERSION = "capability-v2"
CAPABILITY_INVOCATION_ABI = {
    "kind": "capability_request",
    "method_call": "method(request=request)",
    "request_required": ["request"],
}
MIN_CAPABILITIES = 3
MAX_CAPABILITIES = 10


class CapabilityPreparationError(ValueError):
    """Raised when a capability preparation run cannot produce a design."""


def task_library_for_robot(robot_id: str) -> Path:
    """Return the versioned public task-library directory for an AA1 robot."""

    if not isinstance(robot_id, str) or not robot_id.strip():
        raise ValueError("robot_id must be non-empty text")
    index_path = TASK_LIBRARY_ROOT / "index.yaml"
    try:
        index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read public task-library index: {exc}") from None
    if not isinstance(index, Mapping):
        raise ValueError("public task-library index must be an object")
    libraries = index.get("libraries")
    robots = index.get("robots")
    if not isinstance(libraries, Mapping) or not isinstance(robots, Mapping):
        raise ValueError("public task-library index needs libraries and robots mappings")
    source_id = robots.get(robot_id)
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError(f"no public task library is indexed for robot {robot_id!r}")
    relative = libraries.get(source_id)
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError(f"public task library {source_id!r} has no versioned path")
    root = TASK_LIBRARY_ROOT.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError("public task-library path escapes its root") from None
    if not path.is_dir():
        raise ValueError(f"public task-library directory does not exist: {relative!r}")
    return path


def _json_copy(value: Any, *, label: str) -> Any:
    try:
        return json.loads(
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityPreparationError(f"{label} must be finite JSON: {exc}") from None


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityPreparationError(f"cannot read public {label}: {exc}") from None
    if not isinstance(value, dict):
        raise CapabilityPreparationError(f"public {label} must contain one JSON object")
    return value


def _public_file(task_library_dir: Path, name: str) -> Path:
    """Resolve one of the two allowed public files in a task package."""

    direct = task_library_dir / name
    if direct.is_file():
        return direct
    nested = task_library_dir / "tasks" / name
    if nested.is_file():
        return nested
    raise CapabilityPreparationError(
        f"public task library is missing {name} (looked only in its package root and tasks/)"
    )


def _nonempty_text(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityPreparationError(f"{where} must be non-empty text")
    return value.strip()


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _validate_request_schema(schema: Any, *, where: str) -> None:
    """Check only the request-schema boundary needed by the design stage."""

    if not isinstance(schema, Mapping):
        raise ValueError(f"{where} must be an object")
    if "type" in schema and schema["type"] != "object":
        raise ValueError(f"{where}.type must be object")
    if schema.get("additionalProperties") is not False:
        raise ValueError(
            f"{where}.additionalProperties must be false; declare all effect "
            "inputs in properties instead of leaving targets implicit"
        )
    # The Piper canary exposed task macros addressed to private scene bodies.
    # Reject these observed scene-entity arguments at the public ABI boundary.
    properties = schema.get("properties", {})
    scene_fields = {"object_body", "handle_body", "target_body"}
    if isinstance(properties, Mapping) and scene_fields.intersection(properties):
        raise ValueError(
            f"{where} must not select a scene entity; use robot-owned physical "
            "targets such as end-effector pose, gripper aperture, or contact force"
        )


def _validate_evidence_refs(value: Any, *, where: str, required: bool = True) -> None:
    if not isinstance(value, list) or (required and not value):
        raise ValueError(f"{where} must be a {('non-empty ' if required else '')}array")
    for index, item in enumerate(value):
        ref_where = f"{where}[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{ref_where} must be an object")
        _nonempty_text(item.get("source_id"), where=f"{ref_where}.source_id")
        _nonempty_text(item.get("specific_reference"), where=f"{ref_where}.specific_reference")


def _validate_rule(value: Any, *, where: str) -> None:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{where} must be a non-empty object")


def _validate_criterion(value: Any, *, where: str, generated: bool) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where} must be an object")
    required = {
        "metric",
        "unit",
        "comparator",
        "threshold",
        "temporal",
        "aggregation",
    }
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"{where} is missing criterion fields {missing}")
    _nonempty_text(value.get("metric"), where=f"{where}.metric")
    _nonempty_text(value.get("unit"), where=f"{where}.unit")
    comparator = value.get("comparator")
    if comparator not in {"<", "<=", ">", ">=", "==", "between"}:
        raise ValueError(f"{where}.comparator is unsupported")
    threshold = value.get("threshold")
    if _finite_number(threshold):
        if comparator == "between":
            raise ValueError(f"{where}.between requires a two-number threshold")
    elif (
        isinstance(threshold, list)
        and len(threshold) == 2
        and all(_finite_number(item) for item in threshold)
    ):
        if threshold[0] > threshold[1]:
            raise ValueError(f"{where}.threshold range must be ordered")
        if comparator != "between":
            raise ValueError(f"{where}.a threshold range requires comparator 'between'")
    else:
        raise ValueError(
            f"{where}.threshold must be finite numeric or a finite two-number range"
        )
    _validate_rule(value.get("temporal"), where=f"{where}.temporal")
    _validate_rule(value.get("aggregation"), where=f"{where}.aggregation")
    if generated:
        _validate_evidence_refs(value.get("source_refs"), where=f"{where}.source_refs")
    elif "source_refs" in value:
        _validate_evidence_refs(value["source_refs"], where=f"{where}.source_refs", required=False)


def _method_name(value: Any, *, where: str) -> str:
    method = _nonempty_text(value, where=where)
    if not method.isidentifier() or keyword.iskeyword(method) or method == "finish":
        raise ValueError(f"{where} must be a non-reserved Python identifier")
    return method


def _task_ids(catalog: Mapping[str, Any]) -> list[str]:
    tasks = catalog.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise CapabilityPreparationError("public task catalogue has no tasks")
    ids: list[str] = []
    for index, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            raise CapabilityPreparationError(f"catalog.tasks[{index}] must be an object")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or task_id in ids:
            raise CapabilityPreparationError(
                f"catalog.tasks[{index}].task_id is invalid or duplicated"
            )
        ids.append(task_id)
    return ids


def _validate_task_support(
    value: Any,
    *,
    task_ids: Sequence[str],
    capability_ids: set[str],
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("task_support must be a non-empty array of pairs")
    known_tasks = set(task_ids)
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for index, item in enumerate(value):
        where = f"task_support[{index}]"
        if (
            not isinstance(item, Mapping)
            or set(item) != {"task_id", "capability_id", "rationale"}
        ):
            raise ValueError(
                f"{where} must contain only task_id, capability_id, and rationale"
            )
        task_id = _nonempty_text(item.get("task_id"), where=f"{where}.task_id")
        capability_id = _nonempty_text(
            item.get("capability_id"), where=f"{where}.capability_id"
        )
        rationale = _nonempty_text(item.get("rationale"), where=f"{where}.rationale")
        if task_id not in known_tasks:
            raise ValueError(f"{where} references unknown task {task_id!r}")
        if capability_id not in capability_ids:
            raise ValueError(f"{where} references unknown capability {capability_id!r}")
        pair = (task_id, capability_id)
        if pair in seen:
            raise ValueError(f"{where} duplicates task/capability pair {pair!r}")
        seen.add(pair)
        result.append(
            {
                "task_id": task_id,
                "capability_id": capability_id,
                "rationale": rationale,
            }
        )
    missing_tasks = sorted(known_tasks - {item["task_id"] for item in result})
    missing_caps = sorted(capability_ids - {item["capability_id"] for item in result})
    if missing_tasks or missing_caps:
        raise ValueError(
            "task_support must cover every task and capability; "
            f"missing_tasks={missing_tasks}, missing_capabilities={missing_caps}"
        )
    return result


def _validate_capabilities(capabilities: Any, *, generated: bool) -> set[str]:
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("capabilities must be a non-empty array")
    if generated and not MIN_CAPABILITIES <= len(capabilities) <= MAX_CAPABILITIES:
        raise ValueError("capabilities must contain between three and ten items")
    seen_ids: set[str] = set()
    seen_methods: set[str] = set()
    required = (
        "description",
        "effect",
        "preconditions",
        "temporal_semantics",
        "invariants",
        "failure_behavior",
    )
    for index, capability in enumerate(capabilities):
        where = f"capabilities[{index}]"
        if not isinstance(capability, Mapping):
            raise ValueError(f"{where} must be an object")
        capability_id = _nonempty_text(capability.get("capability_id"), where=f"{where}.capability_id")
        method_name = _method_name(capability.get("method_name"), where=f"{where}.method_name")
        if capability_id in seen_ids:
            raise ValueError(f"duplicate capability_id {capability_id!r}")
        if method_name in seen_methods:
            raise ValueError(f"duplicate method_name {method_name!r}")
        seen_ids.add(capability_id)
        seen_methods.add(method_name)
        if generated:
            for field in required:
                if field in {"preconditions", "invariants"}:
                    values = capability.get(field)
                    if not isinstance(values, list) or not values:
                        raise ValueError(f"{where}.{field} must be a non-empty array")
                elif field == "temporal_semantics":
                    _validate_rule(capability.get(field), where=f"{where}.{field}")
                elif field == "failure_behavior":
                    if not isinstance(capability.get(field), (str, Mapping)) or not capability.get(field):
                        raise ValueError(f"{where}.failure_behavior must be non-empty text or object")
                else:
                    _nonempty_text(capability.get(field), where=f"{where}.{field}")
        _validate_request_schema(capability.get("request_schema"), where=f"{where}.request_schema")
        criteria = capability.get("criteria")
        if not isinstance(criteria, list) or len(criteria) != 1:
            raise ValueError(f"{where}.criteria must contain exactly one criterion")
        _validate_criterion(criteria[0], where=f"{where}.criteria[0]", generated=generated)
    return seen_ids


def _validate_generated_design(
    design: Any,
    *,
    expected_robot_id: str,
    task_ids: Sequence[str],
    package_version: str | None = None,
    task_snapshot_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(design, Mapping):
        raise ValueError("capability design must be one JSON object")
    result = _json_copy(dict(design), label="capability design")
    if result.get("robot_configuration_id") != expected_robot_id:
        raise ValueError(
            "capability design robot_configuration_id does not match "
            f"expected robot {expected_robot_id!r}"
        )
    expected_headers = {
        "artifact_type": "capability_design",
        "schema_version": "2.0",
        "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
    }
    for field, expected in expected_headers.items():
        if field in result and result[field] != expected:
            raise ValueError(f"{field} must equal {expected!r}")
        result.setdefault(field, expected)
    if "invocation_abi" in result and result["invocation_abi"] != CAPABILITY_INVOCATION_ABI:
        raise ValueError("invocation_abi must be the capability-v2 request ABI")
    result.setdefault("invocation_abi", copy.deepcopy(CAPABILITY_INVOCATION_ABI))
    if package_version is not None:
        if result.get("package_version", package_version) != package_version:
            raise ValueError("package_version does not match the public task package")
        result.setdefault("package_version", package_version)
    if task_snapshot_id is not None:
        if result.get("task_snapshot_id", task_snapshot_id) != task_snapshot_id:
            raise ValueError("task_snapshot_id does not match the public task snapshot")
        result.setdefault("task_snapshot_id", task_snapshot_id)
    capability_ids = _validate_capabilities(result.get("capabilities"), generated=True)
    result["task_support"] = _validate_task_support(
        result.get("task_support"), task_ids=task_ids, capability_ids=capability_ids
    )
    # Keep the public task IDs with generated output so the loader can check
    # links when the same design is loaded later.
    result["task_ids"] = list(task_ids)
    return result


def load_capability_design_file(
    path: str | Path,
    *,
    expected_robot_id: str,
) -> dict[str, Any]:
    """Load an authored design while preserving legacy optional metadata."""

    if not isinstance(expected_robot_id, str) or not expected_robot_id.strip():
        raise ValueError("expected_robot_id must be non-empty text")
    design = _read_object(Path(path), label="capability design")
    result = _json_copy(design, label="capability design")
    if result.get("robot_configuration_id") != expected_robot_id:
        raise ValueError(
            "capability design robot_configuration_id does not match "
            f"expected robot {expected_robot_id!r}"
        )
    capability_ids = _validate_capabilities(result.get("capabilities"), generated=False)
    declared_tasks = result.get("task_ids")
    if declared_tasks is not None:
        if (
            not isinstance(declared_tasks, list)
            or not declared_tasks
            or any(not isinstance(item, str) or not item.strip() for item in declared_tasks)
            or len(set(declared_tasks)) != len(declared_tasks)
        ):
            raise ValueError("task_ids must be a unique non-empty string array")
        result["task_support"] = _validate_task_support(
            result.get("task_support"),
            task_ids=declared_tasks,
            capability_ids=capability_ids,
        )
    return result


def _public_study(study: Mapping[str, Any]) -> dict[str, Any]:
    copied = _json_copy(dict(study), label="study")
    forbidden = {
        "candidate_driver",
        "candidate_source",
        "driver_code",
        "driver_source",
        "private_bindings",
        "private_cases",
        "private_guards",
        "repair_history",
        "validation_verdict",
    }

    def clean(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): clean(child)
                for key, child in value.items()
                if str(key).lower() not in forbidden
            }
        if isinstance(value, list):
            return [clean(child) for child in value]
        return value

    return clean(copied)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_preparation_metadata(
    output_dir: Path,
    *,
    token_usage: Mapping[str, Any] | None,
    duration_sec: float,
    trace_path: Path,
    error: str | None,
) -> None:
    _write_json(
        output_dir / "capability_preparation.json",
        {
            "token_usage": dict(token_usage or {}),
            "duration_sec": float(duration_sec),
            "trace_path": str(trace_path),
            "error": error,
        },
    )


def _trace_stop_reasons(result: Any) -> list[str]:
    reasons: list[str] = []
    for step in getattr(result, "trace", ()) or ():
        reason = step.get("stop_reason") if isinstance(step, Mapping) else getattr(
            step, "stop_reason", None
        )
        if isinstance(reason, str):
            reasons.append(reason)
    return reasons


def _ensure_trace_file(trace_path: Path) -> None:
    # ReactLoop writes its own full trace. An error before loop construction
    # still gets an empty trace alongside the failure metadata.
    trace_path.touch(exist_ok=True)


TGCD_SYSTEM_PROMPT = """You are AA1 Task-Grounded Capability Design (TGCD).
The original public STUDY completed before this turn. Use only the supplied
study, the corresponding public robot catalog's task descriptions and scoring
source_refs, and optional public skeleton context. Do not open private task
instances, validation suites,
IVC/evaluation files, reference contracts/drivers, or archived experiment data.

Read the supplied study and catalog paths with read_file before writing. Then write one compact
capability-v2 JSON object to draft/capability_design.json with write_file.
You may use local_exec for a short, read-only public MuJoCo/robot-model check
against the supplied actual_mjcf_path. It runs in the TGCD output workspace with
the orchestrator's AA1 virtual environment and PYTHONPATH, so `python` can import
MuJoCo. Do not modify source robot assets and do not read private tests, reference
contracts or drivers, or archived experiment data. local_exec is an ordinary shell
and is not a sandbox or a process-permission boundary.
Prefer three to six compact reusable package-bound, task-neutral single-effect
capabilities (the allowed range is three to ten when task coverage needs more).
Each needs a unique capability_id and Python method_name for method(request),
and must not shadow a low-level skeleton method. Include description, effect,
request_schema, preconditions, temporal_semantics, invariants,
failure_behavior, and exactly one criteria item. Each request schema is a
task-neutral object with additionalProperties=false. Declare EVERY input needed
for the effect in properties and mark mandatory targets in required. Positions
and orientations may use bounded numeric arrays with explicit length and frame.
Never omit target position/quaternion fields or hide them in prose to shorten
the output. Timing inputs alone cannot specify a requested position/orientation.
Keep descriptions concise; per-node evidence is unnecessary. Criteria
need metric, unit, comparator, finite threshold, non-empty temporal and
aggregation objects, and source_refs.

Capabilities control the ROBOT, not a complete task or an external scene
entity. Requests must not name object bodies, handles, fixtures, private sites,
or task goals. Do not author grasp-and-place, move-object-to-goal, open-fixture,
or press-target macros. Decompose such requirements into reusable robot effects
(for example end-effector positioning, orientation, gripper aperture, or bounded
contact force/displacement); choose the actual set from this robot and its tasks.
Task object-to-goal scoring is motivation, not automatically a robot capability
criterion. Measure the robot effect; label a newly derived tolerance proposed.
This is not a task executor and cannot assume a private scene or task evaluator.

Set the ROOT robot_configuration_id to the AA1 robot ID supplied in the user
message; do not use the catalog's source robot ID. Python supplies and checks
package_version and task_snapshot_id from the catalog. preconditions
and invariants are arrays; temporal_semantics is a non-empty object (e.g.
{"kind":"bounded_terminal_effect"}); failure_behavior is text or an object.

task_support contains only task_id, capability_id, rationale pairs and covers
every task and capability. It has no ordered calls, waypoints, macros, plans,
reset data, or task-specific request fields. Cite public scoring for known
numeric standards. If a standard is unknown, make a finite bounded proposal and
say proposed in the rationale; this stage does not establish calibration.
Preserve the supplied identity and exact capability-v2 request ABI. Keep
prose short and target a complete JSON object below 6000 output tokens. Start
with append=false; append=true may add remaining chunks (each write is at most
150 lines), and Python reports incomplete JSON until the final chunk. Read the
current draft when correcting it. Do not write any other path or narrative."""


def generate_capability_design(
    *,
    robot_id: str,
    study: Mapping[str, Any],
    mjcf_path: str | Path,
    task_library_dir: str | Path,
    output_dir: str | Path,
    model: str,
    provider: str,
    region: str,
    max_iters: int = 6,
    max_tokens_per_turn: int = 8000,
    skeleton_context: Any = None,
    study_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run bounded TGCD and return the design accepted from the draft file."""

    started = time.time()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    trace_path = output / "trace.jsonl"
    token_usage: Mapping[str, Any] = {}
    accepted_design: dict[str, Any] | None = None
    result: Any = None
    try:
        if not isinstance(robot_id, str) or not robot_id.strip():
            raise CapabilityPreparationError("robot_id must be non-empty text")
        if not isinstance(study, Mapping):
            raise CapabilityPreparationError(
                "study must be the completed public study object"
            )
        if (
            not isinstance(max_iters, int)
            or isinstance(max_iters, bool)
            or not 1 <= max_iters <= 6
        ):
            raise CapabilityPreparationError("max_iters must be between one and six")
        if (
            not isinstance(max_tokens_per_turn, int)
            or isinstance(max_tokens_per_turn, bool)
            or max_tokens_per_turn <= 0
        ):
            raise CapabilityPreparationError(
                "max_tokens_per_turn must be a positive integer"
            )
        mjcf = Path(mjcf_path).resolve()
        if not mjcf.is_file():
            raise CapabilityPreparationError(f"MJCF path does not exist: {mjcf_path}")
        library = Path(task_library_dir).resolve()
        catalog_path = _public_file(library, "catalog.json").resolve()
        catalog = _read_object(catalog_path, label="task catalogue")
        task_ids = _task_ids(catalog)
        package_version = _nonempty_text(
            catalog.get("package_version"), where="catalog.package_version"
        )
        task_snapshot_id = _nonempty_text(
            catalog.get("snapshot_id", catalog.get("task_snapshot_id")),
            where="catalog.snapshot_id",
        )
        study_input_path = (
            Path(study_path).expanduser().resolve()
            if study_path is not None
            else output / "study.json"
        )
        if study_path is None:
            _write_json(study_input_path, _public_study(study))
        else:
            if not study_input_path.is_file():
                raise CapabilityPreparationError(
                    f"study_path does not exist: {study_path}"
                )
            supplied_study = _read_object(study_input_path, label="study")
            if _public_study(supplied_study) != _public_study(study):
                raise CapabilityPreparationError(
                    "study_path does not match the completed study supplied to TGCD"
                )
        skeleton_path: Path | None = None
        if skeleton_context is not None:
            skeleton_path = output / "skeleton_context.json"
            _write_json(skeleton_path, _json_copy(skeleton_context, label="skeleton_context"))

        reader_roots = [library]
        if study_input_path.parent != output:
            reader_roots.append(study_input_path.parent)
        reader = make_read_file_tool(output, extra_roots=reader_roots)
        writer = make_write_file_tool(output)
        aa1_root = Path(__file__).resolve().parents[1]
        local_exec = make_local_exec_tool(
            output,
            python_path_prepend=[aa1_root],
        )
        read_handler = reader.handler
        write_handler = writer.handler
        draft_path = output / "draft" / "capability_design.json"
        read_paths = {catalog_path, study_input_path, draft_path}
        if skeleton_path is not None:
            read_paths.add(skeleton_path)
        study_read = False
        catalog_read = False
        draft_written = False

        def read_public_input(payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal study_read, catalog_read
            raw_path = payload.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ValueError("read_file needs an input path")
            path = Path(raw_path)
            if path.is_absolute():
                path = path.resolve()
            elif path == Path("catalog.json"):
                path = catalog_path
            elif path == Path("study.json") and study_input_path.name == "study.json":
                path = study_input_path
            else:
                path = (output / path).resolve()
            if path not in read_paths:
                raise ValueError(
                    "read_file may read only the supplied study.json, the exact "
                    "catalog.json, optional skeleton_context.json, or the current draft"
                )
            content = read_handler({**payload, "path": str(path)})
            if path == study_input_path:
                study_read = True
            if path == catalog_path:
                catalog_read = True
            return content

        def _write_accepted(validated: Mapping[str, Any]) -> None:
            _write_json(output / "capability_design.json", validated)
            _write_json(
                output / "criteria.json",
                {
                    "robot_configuration_id": validated["robot_configuration_id"],
                    "capability_protocol_version": validated.get(
                        "capability_protocol_version", CAPABILITY_PROTOCOL_VERSION
                    ),
                    "criteria": [
                        {
                            "capability_id": capability["capability_id"],
                            "method_name": capability["method_name"],
                            **copy.deepcopy(capability["criteria"][0]),
                        }
                        for capability in validated["capabilities"]
                    ],
                },
            )

        def write_draft(payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal accepted_design, draft_written
            if not study_read or not catalog_read:
                raise ValueError(
                    "read study.json and the exact catalog.json with read_file before writing a design"
                )
            if not isinstance(payload, Mapping):
                raise ValueError("write_file needs a draft path and string content")
            if payload.get("path") != "draft/capability_design.json":
                raise ValueError("write_file may write only draft/capability_design.json")
            content = payload.get("content")
            if not isinstance(content, str):
                raise ValueError("write_file content must be a JSON text string")
            append = bool(payload.get("append", False))
            if append and not draft_written:
                raise ValueError(
                    "write a replacement draft first; append cannot extend a stale draft"
                )
            # Every accepted design must follow a write made during this run.
            # A later replacement or append must validate the complete current file.
            accepted_design = None
            write_handler(dict(payload))
            draft_written = True
            try:
                candidate = json.loads(draft_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "draft is incomplete or malformed JSON; write a complete replacement "
                    f"or append the remaining JSON chunk: {exc}"
                ) from None
            try:
                validated = _validate_generated_design(
                    candidate,
                    expected_robot_id=robot_id,
                    task_ids=task_ids,
                    package_version=package_version,
                    task_snapshot_id=task_snapshot_id,
                )
            except (CapabilityPreparationError, ValueError) as exc:
                raise ValueError(
                    "draft validation failed; correct the draft and write it again "
                    f"(append only after the replacement write): {exc}"
                ) from None
            accepted_design = validated
            _write_accepted(validated)
            return {
                "accepted": True,
                "capability_count": len(validated["capabilities"]),
                "path": str(draft_path),
            }

        reader.description = (
            "Read the supplied study input, exact robot catalog.json, optional "
            "skeleton_context.json, or current draft/capability_design.json."
        )
        writer.description = (
            "Write or append the TGCD draft at exactly "
            "draft/capability_design.json. Other paths are rejected."
        )
        local_exec.description = (
            f"Run a short shell command for a public MuJoCo/robot-model check with "
            f"cwd={output} using the orchestrator's AA1 virtual environment and "
            f"PYTHONPATH. Inspect only the supplied actual_mjcf_path ({mjcf}); do "
            "not modify source robot assets or read private tests, reference "
            "contracts/drivers, or archived experiment data. This is an ordinary "
            "shell, not a sandbox or a process-permission boundary."
        )
        reader.handler = read_public_input
        writer.handler = write_draft

        loop = ReactLoop(
            tools=[reader, writer, local_exec],
            system=TGCD_SYSTEM_PROMPT,
            model=model,
            provider=provider,
            region=region,
            max_iters=max_iters,
            max_tokens_per_turn=max_tokens_per_turn,
            trace_path=trace_path,
        )
        prompt_lines = [
            "Read both inputs with read_file before writing:",
            f"robot_configuration_id: {robot_id}",
            f"study_path: {study_input_path}",
            f"catalog_path: {catalog_path}",
            f"actual_mjcf_path: {mjcf}",
        ]
        if skeleton_path is not None:
            prompt_lines.append(f"skeleton_context_path: {skeleton_path}")
        prompt_lines.extend(
            [
                "Use the catalog task descriptions, scoring, and task-local source_refs.",
                "Ignore legacy task invocation_schema and request_envelope when defining the capability interface.",
                "Write the complete JSON design with write_file at "
                "draft/capability_design.json. Start append=false; use append=true "
                "only for remaining chunks. Keep the root robot_configuration_id "
                "equal to the AA1 ID above.",
            ]
        )
        user_prompt = "\n".join(prompt_lines)
        result = loop.run(user_prompt)
        token_usage = getattr(result, "total_tokens", {}) or {}
        _ensure_trace_file(trace_path)
        if "invoke_error" in _trace_stop_reasons(result):
            raise CapabilityPreparationError(
                "TGCD transport ended with invoke_error; a written design is "
                "not a successful run"
            )
        if not bool(getattr(result, "ok", False)):
            raise CapabilityPreparationError(
                getattr(result, "error", None)
                or "TGCD ReactLoop did not complete successfully"
            )
        if accepted_design is None:
            raise CapabilityPreparationError(
                "TGCD completed without a valid current draft write; "
                "stale output is ignored"
            )
        _write_preparation_metadata(
            output,
            token_usage=token_usage,
            duration_sec=time.time() - started,
            trace_path=trace_path,
            error=None,
        )
        return accepted_design
    except Exception as exc:
        _ensure_trace_file(trace_path)
        if not trace_path.exists():
            trace_path.touch()
        error = str(exc)
        _write_preparation_metadata(
            output,
            token_usage=token_usage,
            duration_sec=time.time() - started,
            trace_path=trace_path,
            error=error,
        )
        if isinstance(exc, CapabilityPreparationError):
            raise
        raise CapabilityPreparationError(error) from exc


__all__ = [
    "CAPABILITY_INVOCATION_ABI",
    "CAPABILITY_PROTOCOL_VERSION",
    "CapabilityPreparationError",
    "TGCD_SYSTEM_PROMPT",
    "generate_capability_design",
    "load_capability_design_file",
    "task_library_for_robot",
]
