"""The small, public ``capability-v2`` contract.

The framework deliberately keeps this contract boring.  TGCD writes the
capability description and the many-to-many task-support relation; it does not
write a task plan.  A capability request is a closed, task-neutral JSON
object.  Validation in this module is structural and deterministic so the
model cannot turn a task wrapper into a runtime tool by changing a schema.

The older ``primitive_v1`` draft used a package-owned primitive-family
catalogue.  Capability-v2 is intentionally different: every capability is
authored by TGCD and carries its own source-backed criteria.  This module is
the only protocol implementation used by the new stages.
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any


CAPABILITY_PROTOCOL_VERSION = "capability-v2"
CAPABILITY_INVOCATION_ABI = {
    "kind": "capability_request",
    "method_call": "method(request=request)",
    "request_required": ["request"],
}


class CapabilityProtocolError(ValueError):
    """Raised when a capability-v2 artifact is malformed."""


class CapabilitySchemaError(CapabilityProtocolError):
    """Raised when a candidate request schema is not task-neutral."""


_SCHEMA_KEYS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "minItems",
    "maxItems",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "enum",
    "const",
    "description",
    "unit",
    "frame",
    "evidence_refs",
}
_SCHEMA_TYPES = {"object", "array", "number", "integer", "string", "boolean"}
_FORBIDDEN_NAMES = {
    "task",
    "task_id",
    "task_ids",
    "task_name",
    "task_type",
    "task_parameters",
    "task_params",
    "scene",
    "scene_id",
    "scene_name",
    "reset",
    "reset_id",
    "private",
    "private_case",
    "private_instance",
    "criterion",
    "criteria",
    "oracle",
    "oracle_plan",
    "plan",
    "plans",
    "macro",
    "macros",
    "sequence",
    "sequences",
    "steps",
    "calls",
    "capability_calls",
}
_FORBIDDEN_TOKENS = {
    "task",
    "tasks",
    "scene",
    "reset",
    "private",
    "criterion",
    "criteria",
    "oracle",
    "macro",
    "plan",
    "sequence",
    "call",
}


def json_copy(value: Any, *, label: str = "value") -> Any:
    """Return a finite JSON copy, keeping protocol errors deterministic."""

    try:
        return json.loads(
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityProtocolError(f"{label} must be finite JSON: {exc}") from None


def _text(value: Mapping[str, Any], field: str, *, where: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise CapabilityProtocolError(f"{where}.{field} must be non-empty text")
    return item.strip()


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _tokens(name: str) -> set[str]:
    return {part for part in re.split(r"[^A-Za-z0-9]+", name.lower()) if part}


def _assert_task_neutral_name(name: str, *, where: str) -> None:
    lowered = name.strip().lower()
    tokens = _tokens(lowered)
    if lowered in _FORBIDDEN_NAMES or tokens & _FORBIDDEN_TOKENS:
        raise CapabilitySchemaError(
            f"{where} uses a task/scene/private/macro field {name!r}"
        )


def _validate_evidence_refs(value: Any, *, where: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise CapabilitySchemaError(f"{where} must be a non-empty array")
    result: list[dict[str, Any]] = []
    for index, ref in enumerate(value):
        ref_where = f"{where}[{index}]"
        if not isinstance(ref, Mapping):
            raise CapabilitySchemaError(f"{ref_where} must be an object")
        if set(ref) - {"source_id", "specific_reference", "support", "adaptation"}:
            raise CapabilitySchemaError(f"{ref_where} has unsupported fields")
        source_id = ref.get("source_id")
        specific = ref.get("specific_reference")
        if not isinstance(source_id, str) or not source_id.strip():
            raise CapabilitySchemaError(f"{ref_where}.source_id must be non-empty text")
        if not isinstance(specific, str) or not specific.strip():
            raise CapabilitySchemaError(
                f"{ref_where}.specific_reference must be non-empty text"
            )
        support = ref.get("support", "direct")
        if support not in {"direct", "adapted"}:
            raise CapabilitySchemaError(f"{ref_where}.support is invalid")
        if support == "adapted" and (
            not isinstance(ref.get("adaptation"), str) or not ref["adaptation"].strip()
        ):
            raise CapabilitySchemaError(f"{ref_where}.adaptation is required for adapted evidence")
        result.append(json_copy(dict(ref), label=ref_where))
    return result


def validate_schema_definition(
    schema: Mapping[str, Any],
    *,
    path: str = "request_schema",
) -> dict[str, Any]:
    """Validate a closed task-neutral request schema and return a copy."""

    if not isinstance(schema, Mapping):
        raise CapabilitySchemaError(f"{path} must be an object")
    unknown = set(schema) - _SCHEMA_KEYS
    if unknown:
        raise CapabilitySchemaError(f"{path} uses unsupported schema keywords: {sorted(unknown)}")
    kind = schema.get("type")
    if kind not in _SCHEMA_TYPES:
        raise CapabilitySchemaError(f"{path}.type must be one of {sorted(_SCHEMA_TYPES)}")
    result = json_copy(dict(schema), label=path)

    if "evidence_refs" in result:
        result["evidence_refs"] = _validate_evidence_refs(
            result["evidence_refs"], where=f"{path}.evidence_refs"
        )

    if kind == "object":
        properties = result.get("properties")
        required = result.get("required")
        if not isinstance(properties, dict):
            raise CapabilitySchemaError(f"{path}.properties must be an object")
        if result.get("additionalProperties") is not False:
            raise CapabilitySchemaError(f"{path} must set additionalProperties=false")
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise CapabilitySchemaError(f"{path}.required must be a string array")
        if len(required) != len(set(required)) or set(required) != set(properties):
            raise CapabilitySchemaError(f"{path}.required must require every property")
        for name, child in properties.items():
            if not isinstance(name, str) or not isinstance(child, Mapping):
                raise CapabilitySchemaError(f"{path}.properties is invalid")
            _assert_task_neutral_name(name, where=f"{path}.properties")
            validate_schema_definition(child, path=f"{path}.properties.{name}")
    elif kind == "array":
        items = result.get("items")
        if not isinstance(items, Mapping):
            raise CapabilitySchemaError(f"{path}.items must be an object")
        validate_schema_definition(items, path=f"{path}.items")
        if "minItems" not in result or "maxItems" not in result:
            raise CapabilitySchemaError(f"{path} arrays require minItems and maxItems bounds")
        for key in ("minItems", "maxItems"):
            if key in result and (
                not isinstance(result[key], int)
                or isinstance(result[key], bool)
                or result[key] < 0
            ):
                raise CapabilitySchemaError(f"{path}.{key} must be a non-negative integer")
        if result.get("minItems", 0) > result.get("maxItems", result.get("minItems", 0)):
            raise CapabilitySchemaError(f"{path} has minItems > maxItems")

    if kind in {"number", "integer"}:
        bound_keys = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
        if not any(key in result for key in bound_keys):
            raise CapabilitySchemaError(f"{path} numeric fields require bounds")
        for key in bound_keys:
            if key in result and not _finite_number(result[key]):
                raise CapabilitySchemaError(f"{path}.{key} must be finite numeric")
        if "minimum" in result and "maximum" in result and result["minimum"] > result["maximum"]:
            raise CapabilitySchemaError(f"{path} has minimum > maximum")
        if any(
            key in result
            for key in bound_keys
        ) and "evidence_refs" not in result:
            raise CapabilitySchemaError(
                f"{path} numeric bounds require evidence_refs"
            )

    # Numeric/array leaves have physical metadata.  An object root is
    # unitless/none by convention and may omit it.
    if kind != "object":
        for field in ("unit", "frame"):
            if not isinstance(result.get(field), str) or not result[field].strip():
                raise CapabilitySchemaError(f"{path}.{field} must be non-empty text")
    return result


def validate_schema_value(
    value: Any,
    schema: Mapping[str, Any],
    *,
    path: str = "request",
) -> None:
    """Validate one native request against a capability-v2 schema."""

    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise CapabilityProtocolError(f"{path} must be an object")
        properties = schema.get("properties", {})
        missing = [key for key in schema.get("required", []) if key not in value]
        extra = sorted(set(value) - set(properties))
        if missing:
            raise CapabilityProtocolError(f"{path} is missing required fields {missing}")
        if extra:
            raise CapabilityProtocolError(f"{path} has unexpected fields {extra}")
        for key, child in value.items():
            validate_schema_value(child, properties[key], path=f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, list):
            raise CapabilityProtocolError(f"{path} must be an array")
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise CapabilityProtocolError(f"{path} has fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise CapabilityProtocolError(f"{path} has more than maxItems")
        for index, child in enumerate(value):
            validate_schema_value(child, schema["items"], path=f"{path}[{index}]")
    elif kind == "number":
        if not _finite_number(value):
            raise CapabilityProtocolError(f"{path} must be a finite number")
        _validate_bounds(value, schema, path=path)
    elif kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise CapabilityProtocolError(f"{path} must be an integer")
        _validate_bounds(value, schema, path=path)
    elif kind == "string":
        if not isinstance(value, str):
            raise CapabilityProtocolError(f"{path} must be text")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise CapabilityProtocolError(f"{path} must be boolean")
    else:
        raise CapabilitySchemaError(f"{path}.type is unsupported")
    if "enum" in schema and value not in schema["enum"]:
        raise CapabilityProtocolError(f"{path} is not in enum")
    if "const" in schema and value != schema["const"]:
        raise CapabilityProtocolError(f"{path} does not equal const")


def _validate_bounds(value: int | float, schema: Mapping[str, Any], *, path: str) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        raise CapabilityProtocolError(f"{path} is below minimum")
    if "maximum" in schema and value > schema["maximum"]:
        raise CapabilityProtocolError(f"{path} is above maximum")
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        raise CapabilityProtocolError(f"{path} is not above exclusiveMinimum")
    if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
        raise CapabilityProtocolError(f"{path} is not below exclusiveMaximum")


def capability_records(design: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    records = design.get("capabilities")
    if not isinstance(records, list) or not records or not all(isinstance(item, Mapping) for item in records):
        raise CapabilityProtocolError("capabilities must be a non-empty object array")
    return tuple(records)


def capability_methods(design: Mapping[str, Any]) -> tuple[str, ...]:
    methods: list[str] = []
    for index, capability in enumerate(capability_records(design)):
        method = capability.get("method_name", capability.get("method"))
        if not isinstance(method, str) or not method.strip():
            raise CapabilityProtocolError(f"capabilities[{index}].method_name is required")
        methods.append(method)
    return tuple(methods)


def _package_ids(package: Any) -> dict[str, str]:
    if isinstance(package, Mapping):
        values = {
            "robot_configuration_id": package.get("robot_configuration_id"),
            "package_version": package.get("package_version"),
            "task_snapshot_id": package.get("task_snapshot_id", package.get("snapshot_id")),
        }
    else:
        values = {
            "robot_configuration_id": getattr(package, "robot_configuration_id", None),
            "package_version": getattr(package, "package_version", None),
            "task_snapshot_id": getattr(package, "snapshot_id", None),
        }
    result: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise CapabilityProtocolError(f"package.{key} must be non-empty text")
        result[key] = value
    return result


def _package_tasks(package: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(package, Mapping):
        raw = package.get("tasks", package.get("task_library", {}).get("tasks", []))
    else:
        raw = getattr(package, "tasks", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise CapabilityProtocolError("package.tasks must be a non-empty array")
    ids: set[str] = set()
    result: list[Mapping[str, Any]] = []
    for index, task in enumerate(raw):
        if not isinstance(task, Mapping):
            raise CapabilityProtocolError(f"tasks[{index}] must be an object")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or task_id in ids:
            raise CapabilityProtocolError(f"tasks[{index}].task_id is invalid or duplicated")
        ids.add(task_id)
        result.append(task)
    return tuple(result)


def _required_text(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityProtocolError(f"{where} must be non-empty text")
    return value.strip()


def _validate_structured_criterion(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CapabilityProtocolError(f"{where} must be an object")
    required = {"metric", "unit", "comparator", "threshold", "temporal", "aggregation", "source_refs"}
    missing = required - set(value)
    if missing:
        raise CapabilityProtocolError(f"{where} is missing criterion fields {sorted(missing)}")
    _required_text(value.get("metric"), where=f"{where}.metric")
    _required_text(value.get("unit"), where=f"{where}.unit")
    comparator = value.get("comparator")
    if comparator not in {"<", "<=", ">", ">=", "==", "between"}:
        raise CapabilityProtocolError(f"{where}.comparator is unsupported")
    threshold = value.get("threshold")
    if not _finite_number(threshold) and not (
        isinstance(threshold, list)
        and len(threshold) == 2
        and all(_finite_number(item) for item in threshold)
    ):
        raise CapabilityProtocolError(f"{where}.threshold must be finite numeric or a range")
    for field in ("temporal", "aggregation"):
        if not isinstance(value.get(field), Mapping) or not value[field]:
            raise CapabilityProtocolError(f"{where}.{field} must be a non-empty object")
    refs = _validate_evidence_refs(value.get("source_refs"), where=f"{where}.source_refs")
    result = json_copy(dict(value), label=where)
    result["source_refs"] = refs
    return result


def validate_structured_criterion(
    value: Any,
    *,
    path: str = "criterion",
) -> dict[str, Any]:
    """Validate one model-authored structured criterion."""

    return _validate_structured_criterion(value, where=path)


def _normalise_task_support(value: Any, *, capability_ids: set[str], task_ids: set[str]) -> list[dict[str, str]]:
    """Normalise the pair relation while refusing plans and call sequences."""

    entries: list[Any]
    if isinstance(value, Mapping):
        entries = []
        for task_id, targets in value.items():
            if not isinstance(targets, list):
                raise CapabilityProtocolError("task_support mapping values must be capability arrays")
            for target in targets:
                if isinstance(target, Mapping):
                    entries.append({"task_id": task_id, **dict(target)})
                else:
                    entries.append({"task_id": task_id, "capability_id": target, "rationale": ""})
    elif isinstance(value, list):
        entries = list(value)
    else:
        raise CapabilityProtocolError("task_support must be an array or pair mapping")

    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        where = f"task_support[{index}]"
        if not isinstance(entry, Mapping) or set(entry) != {"task_id", "capability_id", "rationale"}:
            raise CapabilityProtocolError(
                f"{where} must contain only task_id, capability_id, and rationale"
            )
        task_id = _required_text(entry.get("task_id"), where=f"{where}.task_id")
        capability_id = _required_text(entry.get("capability_id"), where=f"{where}.capability_id")
        rationale = _required_text(entry.get("rationale"), where=f"{where}.rationale")
        if task_id not in task_ids:
            raise CapabilityProtocolError(f"{where} references unknown task {task_id!r}")
        if capability_id not in capability_ids:
            raise CapabilityProtocolError(f"{where} references unknown capability {capability_id!r}")
        key = (task_id, capability_id)
        if key in seen:
            raise CapabilityProtocolError(f"{where} duplicates a task/capability pair")
        seen.add(key)
        result.append({"task_id": task_id, "capability_id": capability_id, "rationale": rationale})
    if not result:
        raise CapabilityProtocolError("task_support must contain at least one relation")
    return result


def validate_capability_design(
    design: Mapping[str, Any],
    package: Any,
    *,
    require_task_support: bool = True,
) -> dict[str, Any]:
    """Audit TGCD output and return its canonical capability-v2 form."""

    if not isinstance(design, Mapping):
        raise CapabilityProtocolError("capability design must be an object")
    ids = _package_ids(package)
    for field, expected in {
        "artifact_type": "capability_design",
        "schema_version": "2.0",
        "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
        **ids,
    }.items():
        if design.get(field) != expected:
            raise CapabilityProtocolError(f"{field} must equal {expected!r}")
    if design.get("invocation_abi") != CAPABILITY_INVOCATION_ABI:
        raise CapabilityProtocolError("invocation_abi must use capability-v2 request ABI")
    raw_capabilities = design.get("capabilities")
    if not isinstance(raw_capabilities, list) or not 3 <= len(raw_capabilities) <= 10:
        raise CapabilityProtocolError("capabilities must contain between 3 and 10 items")

    seen_ids: set[str] = set()
    seen_methods: set[str] = set()
    canonical: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_capabilities):
        where = f"capabilities[{index}]"
        if not isinstance(raw, Mapping):
            raise CapabilityProtocolError(f"{where} must be an object")
        capability_id = _required_text(raw.get("capability_id", raw.get("id")), where=f"{where}.capability_id")
        method_name = _required_text(raw.get("method_name", raw.get("method")), where=f"{where}.method_name")
        if not method_name.isidentifier() or method_name in {"build", "finish_task_demo"}:
            raise CapabilityProtocolError(f"{where}.method_name must be a non-reserved identifier")
        if capability_id in seen_ids or method_name in seen_methods:
            raise CapabilityProtocolError(f"{where} capability ID and method names must be unique")
        description = _required_text(raw.get("description"), where=f"{where}.description")
        effect = _required_text(raw.get("effect"), where=f"{where}.effect")
        schema = validate_schema_definition(raw.get("request_schema"), path=f"{where}.request_schema")
        criteria = raw.get("criteria", raw.get("validation_contract"))
        if not isinstance(criteria, list) or not criteria:
            raise CapabilityProtocolError(f"{where}.criteria must be a non-empty array")
        canonical_criteria = [
            _validate_structured_criterion(item, where=f"{where}.criteria[{criterion_index}]")
            for criterion_index, item in enumerate(criteria)
        ]
        for field in ("preconditions", "invariants"):
            values = raw.get(field)
            if not isinstance(values, list) or not values or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise CapabilityProtocolError(f"{where}.{field} must be a non-empty text array")
        temporal = raw.get("temporal_semantics")
        if not isinstance(temporal, Mapping) or not temporal:
            raise CapabilityProtocolError(f"{where}.temporal_semantics must be a non-empty object")
        failure = raw.get("failure_behavior")
        if not isinstance(failure, (str, Mapping)) or not failure:
            raise CapabilityProtocolError(f"{where}.failure_behavior must be non-empty text or object")
        evidence_refs = raw.get("evidence_refs")
        if evidence_refs is None:
            evidence_refs = []
        if evidence_refs:
            evidence_refs = _validate_evidence_refs(evidence_refs, where=f"{where}.evidence_refs")
        else:
            evidence_refs = []
        canonical.append(
            {
                "capability_id": capability_id,
                "method_name": method_name,
                "description": description,
                "effect": effect,
                "request_schema": schema,
                "preconditions": json_copy(raw["preconditions"]),
                "temporal_semantics": json_copy(dict(temporal)),
                "invariants": json_copy(raw["invariants"]),
                "failure_behavior": json_copy(failure),
                "criteria": canonical_criteria,
                "evidence_refs": evidence_refs,
            }
        )
        seen_ids.add(capability_id)
        seen_methods.add(method_name)

    task_ids = {str(task.get("task_id")) for task in _package_tasks(package)}
    support = _normalise_task_support(
        design.get("task_support"), capability_ids=seen_ids, task_ids=task_ids
    ) if require_task_support else []
    support_by_capability = {item["capability_id"] for item in support}
    support_by_task = {item["task_id"] for item in support}
    if require_task_support:
        missing_caps = sorted(seen_ids - support_by_capability)
        missing_tasks = sorted(task_ids - support_by_task)
        if missing_caps or missing_tasks:
            raise CapabilityProtocolError(
                f"task_support must cover every capability and task; missing_capabilities={missing_caps}, missing_tasks={missing_tasks}"
            )
    return {
        "artifact_type": "capability_design",
        "schema_version": "2.0",
        "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
        **ids,
        "invocation_abi": copy.deepcopy(CAPABILITY_INVOCATION_ABI),
        "capabilities": canonical,
        "task_support": support,
    }


__all__ = [
    "CAPABILITY_INVOCATION_ABI",
    "CAPABILITY_PROTOCOL_VERSION",
    "CapabilityProtocolError",
    "CapabilitySchemaError",
    "capability_methods",
    "capability_records",
    "json_copy",
    "validate_structured_criterion",
    "validate_schema_definition",
    "validate_schema_value",
    "validate_capability_design",
]
