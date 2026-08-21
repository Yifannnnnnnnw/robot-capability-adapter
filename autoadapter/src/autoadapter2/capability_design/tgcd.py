"""Real-model Task-Grounded Capability Design and its structural audit."""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from autoadapter2.driver_synthesis import (
    SkeletonContractError,
    validate_capability_names,
)
from autoadapter2.libraries import RobotPackage
from autoadapter2.model_api import ModelInvocationError


TGCD_SYSTEM_PROMPT = """You perform Task-Grounded Capability Design, not code generation.
Using only the supplied public Morphology, complete source-backed Task Library, and eligible
Experience, design 5 to 10 reusable robot capability contracts. Do not select from or infer a
pre-authored effect catalog. Every task must be covered by exactly one capability, and tasks may
be grouped only when they share a genuine reusable physical robot effect.

Keep the complete JSON comfortably below 16,000 tokens. Prefer the smallest genuine grouping,
use one concise sentence for each narrative field, and do not repeat source or task prose outside
the fields that must copy it exactly. The 5-to-10 capability count is a hard output constraint.

Return one JSON object with artifact_type='capability_design', schema_version='1.0', the supplied
robot_configuration_id, package_version and task_snapshot_id, invocation_abi exactly equal to
{'kind':'keyword_request','method_call':'method(request=request)','request_required':['task_id',
'task_parameters']}, and capabilities[]. Each capability
must contain: capability_id, effect, method_name, description, covered_task_ids,
abstraction_rationale, interface{inputs,outputs}, preconditions, temporal_semantics, invariants,
required_affordances{actions,observations}, failure_behavior, and validation_contract[].
method_name must be a valid public Python identifier authored by you.

interface.inputs and interface.outputs MUST each be a non-empty JSON array, never a keyed object.
Every item must have string name, type, unit, and frame fields. Parameter inputs must also copy the
public task-schema description whenever one is declared. inputs must contain the fixed item
{"name":"request","type":"object","unit":"unitless","frame":"none"} plus exactly one item for
each distinct required task parameter of the covered tasks. A parameter input is a semantic path,
not another Python argument, and has this exact form:
{"name":"request.task_parameters.target_position","type":"array","unit":"m","frame":"world",
"description":"Desired terminal state of the task entity, not generally an end-effector waypoint",
"required_for_task_ids":["task-a","task-b"]}. Copy type, unit, frame, and description from the
public task schema, and list exactly the covered tasks for which that parameter is required. outputs are
model-authored and contain exactly name, type, unit, and frame; for example
{"name":"completed","type":"bool","unit":"unitless","frame":"none"}.
The Framework mechanically canonicalizes required_for_task_ids from the covered public task
schemas; concentrate on selecting the correct capability grouping and semantic parameter paths.
preconditions, invariants, required_affordances.actions, and required_affordances.observations MUST
be JSON arrays. temporal_semantics MUST be a JSON object, for example
{"kind":"bounded","description":"Complete within the request duration"}.

The method name and semantic grouping are model-authored, but the Python transport ABI is fixed:
every generated public method receives one keyword argument named request. request.task_id selects
the covered task and request.task_parameters follows that task's public invocation_schema. Describe
only declared task-parameter fields in the capability interface; do not invent alternate Python
argument names. required_affordances actions and observations must be selected only from the exact
vocabularies in morphology.public_affordances.

validation_contract must contain exactly one primary item and may contain at most one robustness
item. Each item contains case_role ('primary' or 'robustness'), selection_rationale,
source_task_id, source_clause_id, metric, unit, comparator, threshold, temporal, aggregation, and
source_refs. Copy the selected source clause without weakening it. The primary item must represent
the capability's shared physical effect. Add a robustness item only when a different covered task
has a materially different scene, metric, or temporal obligation that the primary item cannot
exercise; explain that difference in selection_rationale. Do not mechanically copy every task or
scoring clause into validation_contract. Unselected task clauses remain in the Task Library for
the separate Task Demo. Do not return implementation code, simulator bindings, private cases,
reset values, guards, expected trajectories, or a success verdict."""


class CapabilityDesignError(ValueError):
    """Raised when model-authored capability design violates the public contract."""


INVOCATION_ABI = {
    "kind": "keyword_request",
    "method_call": "method(request=request)",
    "request_required": ["task_id", "task_parameters"],
}


class JsonGenerator(Protocol):
    def generate_json(
        self,
        *,
        stage: str,
        prompt: str,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]: ...


def _text(value: Mapping[str, Any], field: str, *, where: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise CapabilityDesignError(f"{where}.{field} must be a non-empty string")
    return item.strip()


def _list(value: Mapping[str, Any], field: str, *, where: str) -> list[Any]:
    item = value.get(field)
    if not isinstance(item, list) or not item:
        raise CapabilityDesignError(f"{where}.{field} must be a non-empty list")
    return item


def _typed_item(item: Mapping[str, Any], *, where: str) -> dict[str, str]:
    return {field: _text(item, field, where=where) for field in ("name", "type", "unit", "frame")}


def _required_parameter_inputs(
    package: RobotPackage,
    covered_task_ids: Sequence[str],
    *,
    where: str,
) -> list[dict[str, Any]]:
    covered = set(covered_task_ids)
    inputs: dict[str, dict[str, Any]] = {}
    for task in package.tasks:
        task_id = str(task["task_id"])
        if task_id not in covered:
            continue
        parameters = task["invocation_schema"]["request"]["task_parameters"]
        properties = parameters["properties"]
        for parameter_name in parameters["required"]:
            schema = properties[parameter_name]
            name = f"request.task_parameters.{parameter_name}"
            typed = {
                "name": name,
                "type": str(schema["type"]),
                "unit": str(schema["unit"]),
                "frame": str(schema["frame"]),
            }
            description = schema.get("description")
            if isinstance(description, str) and description.strip():
                typed["description"] = description.strip()
            existing = inputs.get(name)
            if existing is None:
                inputs[name] = {**typed, "required_for_task_ids": [task_id]}
            elif any(
                existing.get(field) != typed.get(field)
                for field in ("type", "unit", "frame", "description")
            ):
                raise CapabilityDesignError(
                    f"{where} groups incompatible schemas for task parameter {parameter_name!r}"
                )
            else:
                existing["required_for_task_ids"].append(task_id)
    return list(inputs.values())


def _validate_interface(
    capability: Mapping[str, Any],
    package: RobotPackage,
    covered_task_ids: Sequence[str],
    *,
    where: str,
) -> None:
    interface = capability.get("interface")
    if not isinstance(interface, Mapping):
        raise CapabilityDesignError(f"{where}.interface must be an object")
    inputs = interface.get("inputs")
    outputs = interface.get("outputs")
    if not isinstance(inputs, list) or not inputs:
        raise CapabilityDesignError(f"{where}.interface.inputs must be a non-empty list")
    if not isinstance(outputs, list) or not outputs:
        raise CapabilityDesignError(f"{where}.interface.outputs must be a non-empty list")

    expected_inputs = {
        "request": {
            "name": "request",
            "type": "object",
            "unit": "unitless",
            "frame": "none",
        }
    }
    expected_inputs.update(
        {
            item["name"]: item
            for item in _required_parameter_inputs(
                package,
                covered_task_ids,
                where=f"{where}.interface.inputs",
            )
        }
    )
    actual_inputs: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(inputs):
        item_where = f"{where}.interface.inputs[{index}]"
        if not isinstance(item, Mapping):
            raise CapabilityDesignError(f"{item_where} must be an object")
        name = _typed_item(item, where=item_where)["name"]
        if name in actual_inputs:
            raise CapabilityDesignError(f"{where}.interface.inputs duplicates {name!r}")
        actual_inputs[name] = item
    if set(actual_inputs) != set(expected_inputs):
        missing = sorted(set(expected_inputs) - set(actual_inputs))
        unsupported = sorted(set(actual_inputs) - set(expected_inputs))
        raise CapabilityDesignError(
            f"{where}.interface.inputs must exactly expose required task parameters; "
            f"missing={missing}, unsupported={unsupported}"
        )
    for name, expected in expected_inputs.items():
        actual = actual_inputs[name]
        expected_fields = set(expected)
        if set(actual) != expected_fields or any(
            actual.get(key) != value
            for key, value in expected.items()
            if key != "required_for_task_ids"
        ):
            raise CapabilityDesignError(
                f"{where}.interface input {name!r} must copy its public task parameter contract"
            )
        if "required_for_task_ids" in expected:
            required_for = actual.get("required_for_task_ids")
            if (
                not isinstance(required_for, list)
                or any(not isinstance(task_id, str) for task_id in required_for)
                or len(required_for) != len(set(required_for))
                or set(required_for) != set(expected["required_for_task_ids"])
            ):
                raise CapabilityDesignError(
                    f"{where}.interface input {name!r} must identify exactly the tasks "
                    "that require it"
                )

    output_names: set[str] = set()
    for index, item in enumerate(outputs):
        item_where = f"{where}.interface.outputs[{index}]"
        if not isinstance(item, Mapping):
            raise CapabilityDesignError(f"{item_where} must be an object")
        typed = _typed_item(item, where=item_where)
        if set(item) != set(typed):
            raise CapabilityDesignError(f"{item_where} may contain only name, type, unit, and frame")
        if typed["name"] in output_names:
            raise CapabilityDesignError(
                f"{where}.interface.outputs duplicates {typed['name']!r}"
            )
        output_names.add(typed["name"])


def _canonical_interface_items(value: Any) -> Any:
    """Canonicalize the two common JSON spellings without changing semantics."""

    if not isinstance(value, Mapping):
        return value
    if all(field in value for field in ("name", "type", "unit", "frame")):
        return [dict(value)]
    items: list[dict[str, Any]] = []
    for name, description in value.items():
        if not isinstance(description, Mapping):
            return value
        item = dict(description)
        item.setdefault("name", str(name))
        items.append(item)
    return items


def _canonicalize_design(design: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(design))
    capabilities = result.get("capabilities")
    if not isinstance(capabilities, list):
        return result
    for capability in capabilities:
        if not isinstance(capability, dict):
            continue
        interface = capability.get("interface")
        if not isinstance(interface, dict):
            continue
        for direction in ("inputs", "outputs"):
            interface[direction] = _canonical_interface_items(interface.get(direction))
        for field in ("preconditions", "invariants"):
            if isinstance(capability.get(field), str):
                capability[field] = [capability[field]]
        temporal = capability.get("temporal_semantics")
        if isinstance(temporal, str) and temporal.strip():
            capability["temporal_semantics"] = {"description": temporal}
        affordances = capability.get("required_affordances")
        if isinstance(affordances, dict):
            for field in ("actions", "observations"):
                if isinstance(affordances.get(field), str):
                    affordances[field] = [affordances[field]]
    return result


def _canonicalize_parameter_task_coverage(
    design: dict[str, Any], package: RobotPackage
) -> dict[str, Any]:
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list):
        return design
    for index, capability in enumerate(capabilities):
        if not isinstance(capability, dict):
            continue
        covered = capability.get("covered_task_ids")
        interface = capability.get("interface")
        if not isinstance(covered, list) or not isinstance(interface, dict):
            continue
        inputs = interface.get("inputs")
        if not isinstance(inputs, list):
            continue
        expected = {
            item["name"]: item
            for item in _required_parameter_inputs(
                package,
                [task_id for task_id in covered if isinstance(task_id, str)],
                where=f"capabilities[{index}].interface.inputs",
            )
        }
        for item in inputs:
            if not isinstance(item, dict):
                continue
            expected_item = expected.get(item.get("name"))
            if expected_item is not None:
                for field, value in expected_item.items():
                    item[field] = copy.deepcopy(value)
    return design


def _source_clauses(tasks: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    clauses: dict[tuple[str, str], Mapping[str, Any]] = {}
    for task in tasks:
        task_id = str(task["task_id"])
        for clause in task["scoring"]:
            key = (task_id, str(clause["clause_id"]))
            clauses[key] = clause
    return clauses


def _same_public_standard(
    designed: Mapping[str, Any],
    source: Mapping[str, Any],
) -> bool:
    fields = (
        "metric",
        "unit",
        "comparator",
        "threshold",
        "temporal",
        "aggregation",
        "source_refs",
    )
    return all(designed.get(field) == source.get(field) for field in fields)


def _materially_distinct_contract(
    primary: Mapping[str, Any],
    robustness: Mapping[str, Any],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
) -> bool:
    if primary.get("source_task_id") == robustness.get("source_task_id"):
        return False
    standard_fields = (
        "metric",
        "unit",
        "comparator",
        "threshold",
        "temporal",
        "aggregation",
    )
    if any(primary.get(field) != robustness.get(field) for field in standard_fields):
        return True
    primary_task = tasks_by_id.get(str(primary.get("source_task_id")), {})
    robustness_task = tasks_by_id.get(str(robustness.get("source_task_id")), {})
    primary_scene = primary_task.get("scene_assumptions")
    robustness_scene = robustness_task.get("scene_assumptions")
    return (
        isinstance(primary_scene, list)
        and isinstance(robustness_scene, list)
        and primary_scene != robustness_scene
    )


def validate_capability_design(
    design: Mapping[str, Any],
    package: RobotPackage,
) -> dict[str, Any]:
    """Reject missing coverage, invalid names, and weakened source standards."""

    design = _canonicalize_parameter_task_coverage(
        _canonicalize_design(design), package
    )
    expected_root = {
        "artifact_type": "capability_design",
        "schema_version": "1.0",
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
    }
    for field, expected in expected_root.items():
        if design.get(field) != expected:
            raise CapabilityDesignError(f"{field} must equal {expected!r}")
    if design.get("invocation_abi") != INVOCATION_ABI:
        raise CapabilityDesignError("invocation_abi must use the fixed public request envelope")
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list) or not 5 <= len(capabilities) <= 10:
        count = len(capabilities) if isinstance(capabilities, list) else None
        raise CapabilityDesignError(
            f"capabilities must contain between 5 and 10 items; received {count}"
        )

    task_ids = {str(task["task_id"]) for task in package.tasks}
    source_clauses = _source_clauses(package.tasks)
    tasks_by_id = {str(task["task_id"]): task for task in package.tasks}
    task_coverage: Counter[str] = Counter()
    capability_ids: set[str] = set()
    effects: set[str] = set()
    method_names: list[str] = []

    for index, capability in enumerate(capabilities):
        where = f"capabilities[{index}]"
        if not isinstance(capability, Mapping):
            raise CapabilityDesignError(f"{where} must be an object")
        capability_id = _text(capability, "capability_id", where=where)
        effect = _text(capability, "effect", where=where)
        method_name = _text(capability, "method_name", where=where)
        if capability_id in capability_ids:
            raise CapabilityDesignError(f"duplicate capability_id {capability_id!r}")
        if effect in effects:
            raise CapabilityDesignError(f"duplicate effect {effect!r}")
        capability_ids.add(capability_id)
        effects.add(effect)
        method_names.append(method_name)
        for field in ("description", "abstraction_rationale", "failure_behavior"):
            _text(capability, field, where=where)
        covered = _list(capability, "covered_task_ids", where=where)
        for task_id in covered:
            if not isinstance(task_id, str) or task_id not in task_ids:
                raise CapabilityDesignError(f"{where} covers unknown task {task_id!r}")
            task_coverage[task_id] += 1
        _validate_interface(capability, package, covered, where=where)
        _list(capability, "preconditions", where=where)
        if not isinstance(capability.get("temporal_semantics"), Mapping):
            raise CapabilityDesignError(f"{where}.temporal_semantics must be an object")
        _list(capability, "invariants", where=where)
        affordances = capability.get("required_affordances")
        if not isinstance(affordances, Mapping):
            raise CapabilityDesignError(f"{where}.required_affordances must be an object")
        public_affordances = package.morphology.get("public_affordances")
        if not isinstance(public_affordances, Mapping):
            raise CapabilityDesignError("morphology.public_affordances must be an object")
        for direction in ("actions", "observations"):
            required = _list(
                affordances,
                direction,
                where=f"{where}.required_affordances",
            )
            if any(not isinstance(item, str) or not item.strip() for item in required):
                raise CapabilityDesignError(
                    f"{where}.required_affordances.{direction} must contain non-empty strings"
                )
            if len(required) != len(set(required)):
                raise CapabilityDesignError(
                    f"{where}.required_affordances.{direction} must not contain duplicates"
                )
            declared = public_affordances.get(direction)
            declared_set = set(declared) if isinstance(declared, list) else set()
            if not set(required) <= declared_set:
                unsupported = sorted(set(required) - declared_set)
                raise CapabilityDesignError(
                    f"{where}.required_affordances.{direction} contains unsupported "
                    f"affordances: {unsupported}"
                )

        contracts = _list(capability, "validation_contract", where=where)
        if len(contracts) > 2:
            raise CapabilityDesignError(
                f"{where}.validation_contract must contain one primary and at most one robustness item"
            )
        roles: Counter[str] = Counter()
        selected_keys: set[tuple[str, str]] = set()
        contracts_by_role: dict[str, Mapping[str, Any]] = {}
        for clause_index, clause in enumerate(contracts):
            clause_where = f"{where}.validation_contract[{clause_index}]"
            if not isinstance(clause, Mapping):
                raise CapabilityDesignError(f"{clause_where} must be an object")
            role = _text(clause, "case_role", where=clause_where)
            if role not in {"primary", "robustness"}:
                raise CapabilityDesignError(
                    f"{clause_where}.case_role must be 'primary' or 'robustness'"
                )
            _text(clause, "selection_rationale", where=clause_where)
            task_id = _text(clause, "source_task_id", where=clause_where)
            clause_id = _text(clause, "source_clause_id", where=clause_where)
            key = (task_id, clause_id)
            if key in selected_keys:
                raise CapabilityDesignError(
                    f"{where}.validation_contract duplicates source clause {key}"
                )
            selected_keys.add(key)
            source = source_clauses.get(key)
            if source is None:
                raise CapabilityDesignError(f"{clause_where} references unknown source clause {key}")
            if task_id not in covered:
                raise CapabilityDesignError(
                    f"{clause_where} belongs to a task outside this capability"
                )
            if not _same_public_standard(clause, source):
                raise CapabilityDesignError(f"{clause_where} changes a source pass standard")
            roles[role] += 1
            contracts_by_role[role] = clause
        if roles["primary"] != 1 or roles["robustness"] > 1:
            raise CapabilityDesignError(
                f"{where}.validation_contract must contain exactly one primary and at most one robustness item"
            )
        robustness = contracts_by_role.get("robustness")
        if robustness is not None and not _materially_distinct_contract(
            contracts_by_role["primary"], robustness, tasks_by_id
        ):
            raise CapabilityDesignError(
                f"{where}.validation_contract robustness item is not materially distinct"
            )

    try:
        validate_capability_names(method_names)
    except SkeletonContractError as exc:
        raise CapabilityDesignError(str(exc)) from exc
    if set(task_coverage) != task_ids or any(count != 1 for count in task_coverage.values()):
        raise CapabilityDesignError("every Task Library task must be covered exactly once")
    return dict(design)


def run_tgcd(
    client: JsonGenerator,
    package: RobotPackage,
    *,
    experience: Sequence[Mapping[str, Any]] = (),
    max_model_attempts: int = 3,
) -> dict[str, Any]:
    """Invoke the configured model with only TGCD-visible public inputs."""

    if not 1 <= max_model_attempts <= 3:
        raise CapabilityDesignError("max_model_attempts must be between one and three")

    inputs = {
        "morphology": package.morphology,
        "task_library": {
            "robot_configuration_id": package.robot_configuration_id,
            "package_version": package.package_version,
            "snapshot_id": package.snapshot_id,
            "sources": list(package.sources),
            "tasks": list(package.tasks),
        },
        "experience": list(experience),
    }
    prompt = TGCD_SYSTEM_PROMPT
    for attempt in range(max_model_attempts):
        stage = "tgcd"
        if attempt == 1:
            stage = "tgcd-structure-correction"
        elif attempt == 2:
            stage = "tgcd-structure-correction-2"
        try:
            design = client.generate_json(
                stage=stage,
                prompt=prompt,
                inputs=inputs,
            )
        except ModelInvocationError as exc:
            if attempt + 1 >= max_model_attempts:
                raise CapabilityDesignError(
                    f"model JSON generation failed after {max_model_attempts} attempts: {exc}"
                ) from exc
            inputs = {
                **inputs,
                "deterministic_audit_error": str(exc),
            }
            prompt = (
                TGCD_SYSTEM_PROMPT
                + "\nThe previous response was not a complete parseable JSON object. Return the "
                "entire replacement object more compactly; never emit commentary, omit required "
                "fields, or exceed 10 capabilities."
            )
            continue
        try:
            return validate_capability_design(design, package)
        except CapabilityDesignError as exc:
            if attempt + 1 >= max_model_attempts:
                raise
            inputs = {
                **inputs,
                "previous_invalid_design": design,
                "deterministic_audit_error": str(exc),
            }
            prompt = (
                TGCD_SYSTEM_PROMPT
                + "\nThe prior rejected public JSON object and deterministic audit error are "
                "included. Edit that object into one complete replacement instead of starting "
                "over. If the capability count is outside 5 to 10, merge the physically closest "
                "groups while preserving exact one-time task coverage. Never return fewer than 5 "
                "or more than 10 capabilities. Do not change or weaken any source standard."
            )
    raise AssertionError("unreachable")


def write_capability_design(path: str | Path, design: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(dict(design), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
