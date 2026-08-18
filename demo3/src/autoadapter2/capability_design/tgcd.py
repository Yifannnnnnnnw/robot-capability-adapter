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


TGCD_SYSTEM_PROMPT = """You perform Task-Grounded Capability Design, not code generation.
Using only the supplied public Morphology, complete source-backed Task Library, and eligible
Experience, design 5 to 10 reusable robot capability contracts. Do not select from or infer a
pre-authored effect catalog. Every task must be covered by exactly one capability, and tasks may
be grouped only when they share a genuine reusable physical robot effect.

Return one JSON object with artifact_type='capability_design', schema_version='1.0', the supplied
robot_configuration_id, package_version and task_snapshot_id, invocation_abi exactly equal to
{'kind':'keyword_request','method_call':'method(request=request)','request_required':['task_id',
'task_parameters']}, and capabilities[]. Each capability
must contain: capability_id, effect, method_name, description, covered_task_ids,
abstraction_rationale, interface{inputs,outputs}, preconditions, temporal_semantics, invariants,
required_affordances{actions,observations}, failure_behavior, and validation_contract[].
method_name must be a valid public Python identifier authored by you.

interface.inputs and interface.outputs MUST each be a JSON array, never a keyed object. Every item
must have string name, type, unit, and frame fields. For example:
"interface":{"inputs":[{"name":"request","type":"object","unit":"unitless",
"frame":"none"}],"outputs":[{"name":"completed","type":"bool","unit":"unitless",
"frame":"none"}]}.
preconditions, invariants, required_affordances.actions, and required_affordances.observations MUST
be JSON arrays. temporal_semantics MUST be a JSON object, for example
{"kind":"bounded","description":"Complete within the request duration"}.

The method name and semantic interface are model-authored, but the Python transport ABI is fixed:
every generated public method receives one keyword argument named request. request.task_id selects
the covered task and request.task_parameters follows that task's public invocation_schema. Describe
only declared task-parameter fields in the capability interface; do not invent alternate Python
argument names.

For every scoring clause of every covered task, validation_contract must contain exactly one item
with source_task_id, source_clause_id, metric, unit, comparator, threshold, temporal, aggregation,
and source_refs copied without weakening or omission. Do not return implementation code, simulator
bindings, private cases, reset values, guards, expected trajectories, or a success verdict."""


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


def _validate_interface(capability: Mapping[str, Any], *, where: str) -> None:
    interface = capability.get("interface")
    if not isinstance(interface, Mapping):
        raise CapabilityDesignError(f"{where}.interface must be an object")
    for direction in ("inputs", "outputs"):
        values = interface.get(direction)
        if not isinstance(values, list):
            raise CapabilityDesignError(f"{where}.interface.{direction} must be a list")
        for index, item in enumerate(values):
            item_where = f"{where}.interface.{direction}[{index}]"
            if not isinstance(item, Mapping):
                raise CapabilityDesignError(f"{item_where} must be an object")
            for field in ("name", "type", "unit", "frame"):
                _text(item, field, where=item_where)


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


def validate_capability_design(
    design: Mapping[str, Any],
    package: RobotPackage,
) -> dict[str, Any]:
    """Reject missing coverage, invalid names, and weakened source standards."""

    design = _canonicalize_design(design)
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
        raise CapabilityDesignError("capabilities must contain between 5 and 10 items")

    task_ids = {str(task["task_id"]) for task in package.tasks}
    source_clauses = _source_clauses(package.tasks)
    task_coverage: Counter[str] = Counter()
    clause_coverage: Counter[tuple[str, str]] = Counter()
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
        _validate_interface(capability, where=where)
        _list(capability, "preconditions", where=where)
        if not isinstance(capability.get("temporal_semantics"), Mapping):
            raise CapabilityDesignError(f"{where}.temporal_semantics must be an object")
        _list(capability, "invariants", where=where)
        affordances = capability.get("required_affordances")
        if not isinstance(affordances, Mapping):
            raise CapabilityDesignError(f"{where}.required_affordances must be an object")
        _list(affordances, "actions", where=f"{where}.required_affordances")
        _list(affordances, "observations", where=f"{where}.required_affordances")

        covered = _list(capability, "covered_task_ids", where=where)
        for task_id in covered:
            if not isinstance(task_id, str) or task_id not in task_ids:
                raise CapabilityDesignError(f"{where} covers unknown task {task_id!r}")
            task_coverage[task_id] += 1

        contracts = _list(capability, "validation_contract", where=where)
        for clause_index, clause in enumerate(contracts):
            clause_where = f"{where}.validation_contract[{clause_index}]"
            if not isinstance(clause, Mapping):
                raise CapabilityDesignError(f"{clause_where} must be an object")
            task_id = _text(clause, "source_task_id", where=clause_where)
            clause_id = _text(clause, "source_clause_id", where=clause_where)
            key = (task_id, clause_id)
            source = source_clauses.get(key)
            if source is None:
                raise CapabilityDesignError(f"{clause_where} references unknown source clause {key}")
            if task_id not in covered:
                raise CapabilityDesignError(
                    f"{clause_where} belongs to a task outside this capability"
                )
            if not _same_public_standard(clause, source):
                raise CapabilityDesignError(f"{clause_where} changes a source pass standard")
            clause_coverage[key] += 1

    try:
        validate_capability_names(method_names)
    except SkeletonContractError as exc:
        raise CapabilityDesignError(str(exc)) from exc
    if set(task_coverage) != task_ids or any(count != 1 for count in task_coverage.values()):
        raise CapabilityDesignError("every Task Library task must be covered exactly once")
    if set(clause_coverage) != set(source_clauses) or any(
        count != 1 for count in clause_coverage.values()
    ):
        raise CapabilityDesignError("every source scoring clause must be preserved exactly once")
    return dict(design)


def run_tgcd(
    client: JsonGenerator,
    package: RobotPackage,
    *,
    experience: Sequence[Mapping[str, Any]] = (),
    max_model_attempts: int = 2,
) -> dict[str, Any]:
    """Invoke the configured model with only TGCD-visible public inputs."""

    if not 1 <= max_model_attempts <= 2:
        raise CapabilityDesignError("max_model_attempts must be one or two")

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
        design = client.generate_json(
            stage="tgcd" if attempt == 0 else "tgcd-structure-correction",
            prompt=prompt,
            inputs=inputs,
        )
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
                + "\nCorrect the previous JSON only enough to satisfy the deterministic audit. "
                "Do not change or weaken any source standard."
            )
    raise AssertionError("unreachable")


def write_capability_design(path: str | Path, design: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(dict(design), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
