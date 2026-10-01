# SPDX-License-Identifier: Apache-2.0
"""Bounded task-grounded capability design preparation for AA1.

The original AA1 study is completed before this module is called.  TGCD sees
that public study, the public task catalogue/source records, and metadata from
the actual MJCF.  It writes one capability design through a validating
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

from .agent.react_loop import ReactLoop, ToolSpec
from .agent.tools import (
    make_local_exec_tool,
    make_read_file_tool,
    make_write_file_tool,
)


TASK_LIBRARY_ROOT = Path(__file__).resolve().parent / "task_libraries"
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
        if not isinstance(criteria, list) or not criteria:
            raise ValueError(f"{where}.criteria must contain at least one criterion")
        for criterion_index, criterion in enumerate(criteria):
            _validate_criterion(
                criterion,
                where=f"{where}.criteria[{criterion_index}]",
                generated=generated,
            )
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
    # These were labels on the legacy fixed capability contract, not inputs
    # needed by the dynamic executor. Existing authored files remain loadable.
    result.pop("capability_protocol_version", None)
    result.pop("schema_version", None)
    if result.get("artifact_type", "capability_design") != "capability_design":
        raise ValueError("artifact_type must equal 'capability_design'")
    result.setdefault("artifact_type", "capability_design")
    if "invocation_abi" in result and result["invocation_abi"] != CAPABILITY_INVOCATION_ABI:
        raise ValueError("invocation_abi must use method(request=request)")
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
    scene_paths: Mapping[str, Any] | Sequence[Any] | None = None,
    scene_cases_path: Path | None = None,
    probe_report_path: Path | None = None,
) -> None:
    metadata: dict[str, Any] = {
        "token_usage": dict(token_usage or {}),
        "duration_sec": float(duration_sec),
        "trace_path": str(trace_path),
        "error": error,
    }
    if scene_paths is not None:
        metadata["scene_paths"] = _json_safe(scene_paths)
    if scene_cases_path is not None:
        metadata["scene_cases_path"] = str(scene_cases_path)
    if probe_report_path is not None:
        metadata["probe_report_path"] = str(probe_report_path)
    _write_json(output_dir / "capability_preparation.json", metadata)


def _json_safe(value: Any) -> Any:
    """Convert runtime reports and paths into finite JSON values."""

    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json_safe(child) for child in value]
    if isinstance(value, tuple):
        return [_json_safe(child) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise CapabilityPreparationError("runtime report contains a non-finite number")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise CapabilityPreparationError(
        f"runtime report contains unsupported value type {type(value).__name__}"
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


def _load_design_runtime() -> tuple[Any, Any, Any, str]:
    """Load the real scene runtime and measurement docs only in DESIGN mode."""

    try:
        from . import scene_runtime
        from .design_measurements import measurement_catalog
    except (ImportError, ModuleNotFoundError) as exc:
        raise CapabilityPreparationError(
            "prepare_scene_cases requires AA1 scene_runtime.py and "
            "design_measurements.py"
        ) from exc
    try:
        load_cases = scene_runtime.load_scene_cases
        prepare = scene_runtime.prepare_scenes
        probe = scene_runtime.probe_case
        docs = measurement_catalog()
    except AttributeError as exc:
        raise CapabilityPreparationError(
            f"AA1 scene runtime interface is incomplete: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - surface catalog contract errors
        raise CapabilityPreparationError(
            f"cannot load the AA1 measurement/operator catalog: {exc}"
        ) from exc
    if not all(callable(value) for value in (load_cases, prepare, probe)):
        raise CapabilityPreparationError(
            "AA1 scene runtime must expose callable load_scene_cases, "
            "prepare_scenes, and probe_case"
        )
    if not isinstance(docs, str) or not docs.strip():
        raise CapabilityPreparationError(
            "AA1 measurement/operator catalog must be non-empty text"
        )
    return load_cases, prepare, probe, docs.strip()


def _scene_case_ids(suite: Mapping[str, Any]) -> list[str]:
    """Return case IDs from the normalized scene-suite object."""

    cases = suite.get("cases")
    if isinstance(cases, list):
        ids = []
        for index, case in enumerate(cases):
            if not isinstance(case, Mapping):
                raise CapabilityPreparationError(f"scene_cases.cases[{index}] must be an object")
            case_id = case.get("case_id")
            if not isinstance(case_id, str) or not case_id.strip():
                raise CapabilityPreparationError(
                    f"scene_cases.cases[{index}].case_id must be non-empty text"
                )
            ids.append(case_id)
    else:
        raise CapabilityPreparationError("scene_cases.cases must be a list")
    if not ids or len(set(ids)) != len(ids):
        raise CapabilityPreparationError("scene_cases must contain unique non-empty cases")
    return ids


TGCD_SYSTEM_PROMPT = """You are AA1 Task-Grounded Capability Design (TGCD).
The original public STUDY completed before this turn. Use only the supplied
study, the corresponding public robot catalog's task descriptions and scoring
source_refs, and optional public skeleton context. Do not open private task
instances, validation suites,
IVC/evaluation files, reference contracts/drivers, or archived experiment data.

Read the supplied study and catalog paths with read_file before writing. Then write one compact
JSON object to draft/capability_design.json with write_file.
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
failure_behavior, and one or more criteria items. Each request schema is a
task-neutral object with additionalProperties=false. Declare EVERY input needed
for the effect in properties and mark mandatory targets in required. Positions
and orientations may use bounded numeric arrays with explicit length and frame.
Never omit target position/quaternion fields or hide them in prose to shorten
the output. Timing inputs alone cannot specify a requested position/orientation.
Keep descriptions concise; per-node evidence is unnecessary. Each criterion
needs metric, unit, comparator, finite threshold, non-empty temporal and
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
Preserve the supplied identity and method(request=request) calling convention.
Do not add legacy capability_protocol_version or schema_version labels. Keep
prose short and target a complete JSON object below 6000 output tokens. Start
with append=false; append=true may add remaining chunks (each write is at most
150 lines), and Python reports incomplete JSON until the final chunk. Read the
current draft when correcting it. In capability-only mode, do not write any
other path or narrative."""


TGCD_DESIGN_MODE_PROMPT = TGCD_SYSTEM_PROMPT.replace(
    "You are AA1 Task-Grounded Capability Design (TGCD).",
    "You are AA1 DESIGN: task-grounded capability and validation design.",
) + """

This turn is DESIGN mode. After the capability design is valid, author one
scene_cases.yaml draft at draft/scene_cases.yaml. The scene suite uses this
exact top-level shape:

Budget the responses deliberately. The user message supplies the actual
max_iters value and you have at most that many responses. Start by batching
read_file calls for every supplied study, catalog, and optional skeleton path.
Use bounded local_exec exploration only when it answers a concrete question
about the actual MJCF. Write both current drafts early enough to leave ample
responses for probe_case calls and corrections based on real reports, then end
with the current drafts and a clean end_turn. Choose the number of capabilities
from the supplied evidence; do not spend the budget inventing a fixed
capability list.

scenes:
  <scene_id>:
    objects:
      - name: <entity_name>
        motion: fixed | free
        shape: box | sphere | cylinder
        size_m: [x, y, z] (box), [r] (sphere), or [r, half_length] (cylinder)
        position_m: [x, y, z]
        quaternion_wxyz: [w, x, y, z]
        mass_kg: <positive number for free objects>
        friction: [sliding, torsional, rolling]
cases:
  - case_id: <case_id>
    scene: <scene_id>
    capability_id: <capability_id>
    request: <object satisfying that capability request_schema>
    initial_state:
      robot:
        keyframe: <public keyframe name>
        qpos_by_joint: <joint name to scalar mapping>
      free_bodies: <free object name to pose mapping>
      ctrl_by_actuator: <actuator name to scalar mapping>
      settle_s: <non-negative number>
    execution:
      max_sim_time_s: <positive number>
      wall_timeout_s: <positive number>
    measurements:
      - criterion_index: <zero-based integer>
        operator: <supported runtime operator>
        bindings: <operator-specific physical bindings>

When the source MJCF has unnamed base robot geoms, the prepared scene export
gives them runtime-only aliases named aa1_robot_geom_<base_compiled_geom_id>.
The real probe report exposes these under model_inventory.unnamed_base_geoms
as {base_geom_id, generated_name, body_name}; original named geoms keep their
names. Use an alias only in prepared-scene bindings (for example Piper base
geom 81 may be aa1_robot_geom_81 on link6); do not edit or rename the source
MJCF.

Use only the supported scene object and observation/operator forms documented in
the runtime catalog supplied below. Every capability criterion must be covered
by at least one case measurement; each measurement names its zero-based
criterion_index, executable operator, and physical bindings.
Facts taken from the completed study must be labelled actual_study in concise
prose or rationale fields. Derived thresholds and other unsupported standards
must be labelled proposed; do not present a proposed tolerance as an observed
study fact.
Requests stay robot-effect based and may refer to no task object, fixture,
handle, body, site, or private goal. A scene entity belongs in the scene and
measurement binding, never in a capability request schema. Keep proposed
tolerances labelled proposed in the criterion rationale or source reference.
Any effect essential to a capability, including a wrist target, waypoint order,
contact displacement, or continuous hold, must have a matching measurable
criterion and runtime measurement. Prose alone is not a verified criterion.

Write both drafts with write_file. Start each file with append=false; append=true
is allowed only after that same file has received a replacement write. You may
call probe_case(scene_cases_path=\"draft/scene_cases.yaml\", case_id=\"...\") after
both current drafts are valid. The probe result is a real framework report; use
it to correct the current design or suite. Do not author, edit, or claim probe
reports yourself. At the end leave both complete current drafts in place.

Exact supported measurement and operator documentation follows:
"""


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
    max_iters: int = 30,
    max_tokens_per_turn: int = 8000,
    skeleton_context: Any = None,
    study_path: str | Path | None = None,
    prepare_scene_cases: bool = False,
) -> dict[str, Any]:
    """Run bounded TGCD and return the design accepted from the draft file.

    ``prepare_scene_cases`` extends the same bounded ReAct turn with a second
    draft and real scene-runtime probes.  Its default remains the historical
    capability-only behavior.
    """

    started = time.time()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    trace_path = output / "trace.jsonl"
    token_usage: Mapping[str, Any] = {}
    accepted_design: dict[str, Any] | None = None
    accepted_suite: dict[str, Any] | None = None
    scene_paths: Mapping[str, Any] | Sequence[Any] | None = None
    scene_cases_path: Path | None = None
    probe_report_path: Path | None = None
    result: Any = None
    try:
        if not isinstance(robot_id, str) or not robot_id.strip():
            raise CapabilityPreparationError("robot_id must be non-empty text")
        if not isinstance(study, Mapping):
            raise CapabilityPreparationError(
                "study must be the completed public study object"
            )
        if not isinstance(prepare_scene_cases, bool):
            raise CapabilityPreparationError("prepare_scene_cases must be a boolean")
        if (
            not isinstance(max_iters, int)
            or isinstance(max_iters, bool)
            or max_iters <= 0
        ):
            raise CapabilityPreparationError("max_iters must be a positive integer")
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
        scene_draft_path = output / "draft" / "scene_cases.yaml"
        scene_cases_path = output / "scene_cases.yaml" if prepare_scene_cases else None
        probe_report_path = output / "probe_report.json" if prepare_scene_cases else None
        read_paths = {catalog_path, study_input_path, draft_path}
        if skeleton_path is not None:
            read_paths.add(skeleton_path)
        if prepare_scene_cases:
            read_paths.add(scene_draft_path)
            # These paths are produced by this run and may be inspected by the
            # model while it repairs a probe failure.  They are never treated
            # as success inputs; current-write flags and final re-probing below
            # provide the success boundary.
            read_paths.update(
                {
                    output / "capability_design.json",
                    output / "scene_cases.yaml",
                    output / "probe_report.json",
                }
            )
        study_read = False
        catalog_read = False
        draft_written = False
        scene_draft_written = False
        runtime_load_cases: Any = None
        runtime_prepare_scenes: Any = None
        runtime_probe_case: Any = None
        runtime_docs: str | None = None
        if prepare_scene_cases:
            (
                runtime_load_cases,
                runtime_prepare_scenes,
                runtime_probe_case,
                runtime_docs,
            ) = _load_design_runtime()

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
            if prepare_scene_cases and not Path(raw_path).is_absolute():
                aliases = {
                    Path("capability_design.json"): output / "capability_design.json",
                    Path("scene_cases.yaml"): output / "scene_cases.yaml",
                    Path("probe_report.json"): output / "probe_report.json",
                }
                path = aliases.get(Path(raw_path), path).resolve()
            if path not in read_paths:
                raise ValueError(
                    "read_file may read only the supplied study.json, exact catalog.json, "
                    "optional skeleton_context.json, current drafts, or reports produced "
                    "by this DESIGN run"
                )
            content = read_handler({**payload, "path": str(path)})
            if path == study_input_path:
                study_read = True
            if path == catalog_path:
                catalog_read = True
            return content

        def _write_accepted(validated: Mapping[str, Any]) -> None:
            _write_json(output / "capability_design.json", validated)
            criteria_rows: list[dict[str, Any]] = []
            for capability in validated["capabilities"]:
                for criterion_index, criterion in enumerate(capability["criteria"]):
                    row = copy.deepcopy(criterion)
                    row.update(
                        {
                            "capability_id": capability["capability_id"],
                            "method_name": capability["method_name"],
                            "criterion_index": criterion_index,
                        }
                    )
                    criteria_rows.append(row)
            _write_json(
                output / "criteria.json",
                {
                    "robot_configuration_id": validated["robot_configuration_id"],
                    "criteria": criteria_rows,
                },
            )

        def write_draft(payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal accepted_design, accepted_suite, draft_written, scene_draft_written
            if not study_read or not catalog_read:
                raise ValueError(
                    "read study.json and the exact catalog.json with read_file before writing a design"
                )
            if not isinstance(payload, Mapping):
                raise ValueError("write_file needs a draft path and string content")
            draft_name = payload.get("path")
            allowed_names = {"draft/capability_design.json"}
            if prepare_scene_cases:
                allowed_names.add("draft/scene_cases.yaml")
            if draft_name not in allowed_names:
                raise ValueError(
                    "write_file may write only "
                    + " or ".join(sorted(allowed_names))
                )
            content = payload.get("content")
            if not isinstance(content, str):
                raise ValueError("write_file content must be a text string")
            append = bool(payload.get("append", False))
            current_written = (
                draft_written
                if draft_name == "draft/capability_design.json"
                else scene_draft_written
            )
            if append and not current_written:
                raise ValueError(
                    "write a replacement draft first for this file; append cannot extend a stale draft"
                )
            # Every accepted output must follow a write made during this run.
            # A later replacement or append must validate the complete current
            # file; stale canonical artifacts are never consulted.
            if draft_name == "draft/capability_design.json":
                accepted_design = None
            else:
                accepted_suite = None
            write_handler(dict(payload))
            if draft_name == "draft/capability_design.json":
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
                if not prepare_scene_cases:
                    _write_accepted(validated)
                return {
                    "accepted": True,
                    "capability_count": len(validated["capabilities"]),
                    "path": str(draft_path),
                }

            scene_draft_written = True
            if accepted_design is None:
                raise ValueError(
                    "write a valid current capability design before writing scene_cases.yaml"
                )
            try:
                accepted_suite = runtime_load_cases(
                    scene_draft_path,
                    design=accepted_design,
                )
            except Exception as exc:  # noqa: BLE001 - runtime schema feedback to TGCD
                raise ValueError(
                    "scene_cases draft is incomplete or violates the runtime schema; "
                    f"correct it and write it again (append only after the replacement write): {exc}"
                ) from None
            if not isinstance(accepted_suite, Mapping):
                accepted_suite = None
                raise ValueError("scene runtime returned a non-object scene suite")
            return {
                "accepted": True,
                "case_count": len(_scene_case_ids(accepted_suite)),
                "path": str(scene_draft_path),
            }

        probe_reports: dict[str, Any] = {}

        def probe_case_tool(payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal accepted_design, accepted_suite
            if not prepare_scene_cases:
                raise ValueError("probe_case is available only in DESIGN mode")
            if not isinstance(payload, Mapping):
                raise ValueError(
                    "probe_case needs scene_cases_path and case_id"
                )
            raw_path = payload.get("scene_cases_path")
            case_id = payload.get("case_id")
            raw_scene_path = Path(raw_path) if isinstance(raw_path, str) else None
            current_scene_path = (
                raw_scene_path.resolve()
                if raw_scene_path is not None and raw_scene_path.is_absolute()
                else output / raw_scene_path
                if raw_scene_path is not None
                else None
            )
            if raw_path != "draft/scene_cases.yaml" and current_scene_path != scene_draft_path:
                raise ValueError(
                    "probe_case may read only the current draft/scene_cases.yaml"
                )
            if not isinstance(case_id, str) or not case_id.strip():
                raise ValueError("probe_case.case_id must be non-empty text")
            if not draft_written or accepted_design is None:
                raise ValueError(
                    "probe_case requires a valid current capability design draft"
                )
            if not scene_draft_written:
                raise ValueError("probe_case requires a current scene_cases draft")

            # Reload both current drafts at every probe call.  This prevents a
            # successful probe from being reused after the model edits either
            # the capability criteria or the scene/case YAML.
            try:
                current_design = json.loads(draft_path.read_text(encoding="utf-8"))
                current_design = _validate_generated_design(
                    current_design,
                    expected_robot_id=robot_id,
                    task_ids=task_ids,
                    package_version=package_version,
                    task_snapshot_id=task_snapshot_id,
                )
            except (CapabilityPreparationError, ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                accepted_design = None
                raise ValueError(f"current capability design is invalid: {exc}") from None
            try:
                current_suite = runtime_load_cases(
                    scene_draft_path,
                    design=current_design,
                )
            except Exception as exc:  # noqa: BLE001 - runtime feedback to TGCD
                accepted_suite = None
                raise ValueError(f"current scene_cases draft is invalid: {exc}") from None
            if not isinstance(current_suite, Mapping):
                accepted_suite = None
                raise ValueError("scene runtime returned a non-object scene suite")
            if case_id not in _scene_case_ids(current_suite):
                raise ValueError(f"probe_case references unknown case_id {case_id!r}")
            accepted_design = current_design
            accepted_suite = dict(current_suite)
            report = runtime_probe_case(
                mjcf_path=mjcf,
                suite=current_suite,
                design=current_design,
                output_dir=output,
                case_id=case_id,
            )
            if not isinstance(report, Mapping):
                raise ValueError("scene runtime probe_case returned a non-object report")
            report_value = _json_safe(dict(report))
            probe_reports[case_id] = report_value
            _write_json(
                probe_report_path,
                {
                    "robot_configuration_id": robot_id,
                    "reports": probe_reports,
                },
            )
            read_paths.add(probe_report_path)
            return {
                "case_id": case_id,
                "report": report_value,
                "probe_report_path": str(probe_report_path),
            }

        reader.description = (
            "Read the supplied study input, exact robot catalog.json, optional "
            "skeleton_context.json, current drafts, or real reports/canonical "
            "outputs produced by this run."
        )
        if prepare_scene_cases:
            writer.description = (
                "Write or append exactly draft/capability_design.json or "
                "draft/scene_cases.yaml. Start each file with append=false; "
                "append=true is allowed only after a replacement write to that "
                "same file."
            )
        else:
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

        probe_tool = None
        tools = [reader, writer, local_exec]
        if prepare_scene_cases:
            probe_tool = ToolSpec(
                name="probe_case",
                description=(
                    "Run the real AA1 scene runtime against the current "
                    "draft/scene_cases.yaml case using the actual MJCF and "
                    "current capability design. Returns and saves the real "
                    "framework probe report; it does not accept authored reports."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "scene_cases_path": {
                            "type": "string",
                            "description": "Must be draft/scene_cases.yaml",
                        },
                        "case_id": {
                            "type": "string",
                            "description": "One case_id from the current draft",
                        },
                    },
                    "required": ["scene_cases_path", "case_id"],
                    "additionalProperties": False,
                },
                handler=probe_case_tool,
            )
            tools.append(probe_tool)

        loop = ReactLoop(
            tools=tools,
            system=(
                TGCD_DESIGN_MODE_PROMPT + "\n" + runtime_docs
                if prepare_scene_cases
                else TGCD_SYSTEM_PROMPT
            ),
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
            ]
        )
        if prepare_scene_cases:
            prompt_lines.extend(
                [
                    "This is DESIGN mode: write a complete JSON design with write_file at "
                    "draft/capability_design.json, then write the complete scene/case "
                    "suite at draft/scene_cases.yaml. Start append=false for each file; "
                    "use append=true only for remaining chunks of that same file.",
                    f"Response budget: max_iters={max_iters}. Batch-read the supplied "
                    "study, catalog, and optional skeleton_context early; use bounded "
                    "local_exec exploration only for concrete MJCF questions. Budget "
                    "enough responses for both draft files, probe_case reports, "
                    "corrections, and a clean end_turn.",
                    "Use the exact scene/case schema and supported operators in the "
                    "system documentation. Cover every criterion of every capability "
                    "with at least one case, then probe each case with probe_case.",
                    "Keep the root robot_configuration_id equal to the AA1 ID above. "
                    "Do not write reports or canonical outputs yourself.",
                ]
            )
        else:
            prompt_lines.append(
                "Write the complete JSON design with write_file at "
                "draft/capability_design.json. Start append=false; use append=true "
                "only for remaining chunks. Keep the root robot_configuration_id "
                "equal to the AA1 ID above."
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
        if prepare_scene_cases:
            if not draft_written or not scene_draft_written:
                raise CapabilityPreparationError(
                    "DESIGN completed without valid current capability and scene drafts; "
                    "stale outputs are ignored"
                )

            # Read and validate the final bytes after ReactLoop returns.  A
            # probe from an earlier version of either draft cannot certify the
            # final design.
            try:
                final_design = json.loads(draft_path.read_text(encoding="utf-8"))
                final_design = _validate_generated_design(
                    final_design,
                    expected_robot_id=robot_id,
                    task_ids=task_ids,
                    package_version=package_version,
                    task_snapshot_id=task_snapshot_id,
                )
            except (CapabilityPreparationError, ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CapabilityPreparationError(
                    f"final capability draft is invalid: {exc}"
                ) from None
            try:
                final_suite = runtime_load_cases(
                    scene_draft_path,
                    design=final_design,
                )
            except Exception as exc:  # noqa: BLE001 - runtime schema boundary
                raise CapabilityPreparationError(
                    f"final scene_cases draft is invalid: {exc}"
                ) from None
            if not isinstance(final_suite, Mapping):
                raise CapabilityPreparationError(
                    "scene runtime returned a non-object final scene suite"
                )
            final_case_ids = _scene_case_ids(final_suite)
            try:
                prepared_paths = runtime_prepare_scenes(
                    mjcf_path=mjcf,
                    suite=final_suite,
                    output_dir=output,
                )
            except Exception as exc:  # noqa: BLE001 - real runtime error
                raise CapabilityPreparationError(
                    f"scene preparation failed: {exc}"
                ) from None
            if not isinstance(prepared_paths, Mapping):
                raise CapabilityPreparationError(
                    "scene runtime prepare_scenes must return a path mapping"
                )
            scene_paths = dict(prepared_paths)

            # Rerun every final case against the final suite.  The runtime
            # report is the only source of probe success; this wrapper never
            # turns an authored claim into a pass.
            probe_reports.clear()
            for case_id in final_case_ids:
                try:
                    report = runtime_probe_case(
                        mjcf_path=mjcf,
                        suite=final_suite,
                        design=final_design,
                        output_dir=output,
                        case_id=case_id,
                    )
                except Exception as exc:  # noqa: BLE001 - real runtime error
                    raise CapabilityPreparationError(
                        f"final probe failed for case {case_id!r}: {exc}"
                    ) from None
                if not isinstance(report, Mapping):
                    raise CapabilityPreparationError(
                        f"final probe for case {case_id!r} returned a non-object report"
                    )
                report_value = _json_safe(dict(report))
                probe_reports[case_id] = report_value
                _write_json(
                    probe_report_path,
                    {
                        "robot_configuration_id": robot_id,
                        "reports": probe_reports,
                    },
                )
                if report.get("ok") is not True:
                    raise CapabilityPreparationError(
                        f"final probe for case {case_id!r} did not report ok=true"
                    )

            _write_accepted(final_design)
            scene_cases_path.parent.mkdir(parents=True, exist_ok=True)
            scene_cases_path.write_text(
                scene_draft_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            accepted_design = final_design
            accepted_suite = dict(final_suite)
        _write_preparation_metadata(
            output,
            token_usage=token_usage,
            duration_sec=time.time() - started,
            trace_path=trace_path,
            error=None,
            scene_paths=scene_paths,
            scene_cases_path=scene_cases_path,
            probe_report_path=probe_report_path,
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
    "CapabilityPreparationError",
    "TGCD_SYSTEM_PROMPT",
    "TGCD_DESIGN_MODE_PROMPT",
    "generate_capability_design",
    "load_capability_design_file",
    "task_library_for_robot",
]
