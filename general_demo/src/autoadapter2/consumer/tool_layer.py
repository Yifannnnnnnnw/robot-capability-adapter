"""Typed, promotion-bound capability routing for experiment Consumers.

This module deliberately does not implement discovery, a registry, signatures, or
worker isolation.  It only keeps a promoted entrypoint inseparable from the public
schemas and evidence hashes that authorized it, then enforces those schemas at the
Consumer boundary.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable


class ToolLayerError(RuntimeError):
    """Base error for invalid promoted tools and router configuration."""


class PromotionError(ToolLayerError):
    """Raised when a framework-created promoted tool is structurally invalid."""


class SchemaError(ToolLayerError):
    """Raised when the supported closed-schema contract is invalid."""


class ValueSchemaError(ToolLayerError):
    """Raised when a value is outside JSON or violates a public schema."""


Entrypoint = Callable[[dict[str, Any]], Any]
_CAPABILITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_JSON_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}
_COMMON_SCHEMA_KEYS = {"type", "description", "enum"}
_TYPE_SCHEMA_KEYS = {
    "object": {"properties", "required", "additionalProperties"},
    "array": {"items", "minItems", "maxItems"},
    "string": {"minLength", "maxLength"},
    "number": {"minimum", "maximum"},
    "integer": {"minimum", "maximum"},
    "boolean": set(),
    "null": set(),
}


def _copy_json(value: Any, *, path: str = "$") -> Any:
    """Return a detached JSON-domain value or reject it without stringifying it."""

    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueSchemaError(f"{path}: number must be finite")
        return value
    if isinstance(value, list):
        return [_copy_json(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueSchemaError(f"{path}: object keys must be strings")
            copied[key] = _copy_json(item, path=f"{path}.{key}")
        return copied
    raise ValueSchemaError(f"{path}: value is outside the JSON domain")


def copy_json_value(value: Any) -> Any:
    """Public helper used by the Consumer loop to detach JSON-domain values."""

    return _copy_json(value)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return copy.deepcopy(value)


def _schema_copy(value: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaError(f"{path}: schema must be an object")
    ordinary = dict(value)
    try:
        copied = _copy_json(ordinary, path=path)
    except ValueSchemaError as exc:
        raise SchemaError(str(exc)) from None
    assert isinstance(copied, dict)
    return copied


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and (
        not isinstance(value, float) or math.isfinite(value)
    )


def _value_has_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return _is_number(value)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _validate_schema(schema: dict[str, Any], *, path: str) -> None:
    schema_type = schema.get("type")
    if not isinstance(schema_type, str) or schema_type not in _JSON_TYPES:
        raise SchemaError(f"{path}.type: one supported JSON type is required")
    unknown = set(schema) - _COMMON_SCHEMA_KEYS - _TYPE_SCHEMA_KEYS[schema_type]
    if unknown:
        raise SchemaError(f"{path}: unsupported schema keyword")
    description = schema.get("description")
    if description is not None and not isinstance(description, str):
        raise SchemaError(f"{path}.description: must be text")

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            raise SchemaError(f"{path}.enum: must be a non-empty array")
        for index, item in enumerate(enum):
            if not _value_has_type(item, schema_type):
                raise SchemaError(f"{path}.enum[{index}]: does not match declared type")

    if schema_type == "object":
        if schema.get("additionalProperties") is not False:
            raise SchemaError(f"{path}.additionalProperties: must be false")
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise SchemaError(f"{path}.properties: must be an object")
        required = schema.get("required", [])
        if (
            not isinstance(required, list)
            or any(not isinstance(item, str) for item in required)
            or len(required) != len(set(required))
        ):
            raise SchemaError(f"{path}.required: must contain unique property names")
        if not set(required).issubset(properties):
            raise SchemaError(f"{path}.required: names must exist in properties")
        for name, child in properties.items():
            if not isinstance(name, str):
                raise SchemaError(f"{path}.properties: names must be strings")
            child_schema = _schema_copy(child, path=f"{path}.properties.{name}")
            properties[name] = child_schema
            _validate_schema(child_schema, path=f"{path}.properties.{name}")
        return

    if schema_type == "array":
        if "items" not in schema:
            raise SchemaError(f"{path}.items: is required")
        items = _schema_copy(schema["items"], path=f"{path}.items")
        schema["items"] = items
        _validate_schema(items, path=f"{path}.items")
        _validate_nonnegative_bound_pair(schema, path, "minItems", "maxItems", integer=True)
        return

    if schema_type == "string":
        _validate_nonnegative_bound_pair(schema, path, "minLength", "maxLength", integer=True)
        return

    if schema_type in {"number", "integer"}:
        _validate_nonnegative_bound_pair(schema, path, "minimum", "maximum", integer=False)


def _validate_nonnegative_bound_pair(
    schema: Mapping[str, Any],
    path: str,
    lower_name: str,
    upper_name: str,
    *,
    integer: bool,
) -> None:
    lower = schema.get(lower_name)
    upper = schema.get(upper_name)
    for name, value in ((lower_name, lower), (upper_name, upper)):
        if value is None:
            continue
        valid = isinstance(value, int) and not isinstance(value, bool) if integer else _is_number(value)
        if not valid or (integer and value < 0):
            raise SchemaError(f"{path}.{name}: invalid bound")
    if lower is not None and upper is not None and lower > upper:
        raise SchemaError(f"{path}: lower bound exceeds upper bound")


def validate_closed_object_schema(schema: Mapping[str, Any], *, label: str) -> Mapping[str, Any]:
    """Validate the supported schema subset and return an immutable deep copy."""

    copied = _schema_copy(schema, path=label)
    if copied.get("type") != "object":
        raise SchemaError(f"{label}: root schema type must be object")
    _validate_schema(copied, path=label)
    return _freeze_json(copied)


def _validate_value(value: Any, schema: Mapping[str, Any], *, path: str) -> None:
    expected = schema["type"]
    if not _value_has_type(value, expected):
        raise ValueSchemaError(f"{path}: expected {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueSchemaError(f"{path}: value is not allowed")

    if expected == "object":
        assert isinstance(value, dict)
        properties = schema["properties"]
        extras = set(value) - set(properties)
        if extras:
            raise ValueSchemaError(f"{path}: additional properties are not allowed")
        missing = set(schema.get("required", ())) - set(value)
        if missing:
            raise ValueSchemaError(f"{path}: a required property is missing")
        for name, item in value.items():
            _validate_value(item, properties[name], path=f"{path}.{name}")
        return
    if expected == "array":
        assert isinstance(value, list)
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ValueSchemaError(f"{path}: array is too short")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueSchemaError(f"{path}: array is too long")
        for index, item in enumerate(value):
            _validate_value(item, schema["items"], path=f"{path}[{index}]")
        return
    if expected == "string":
        assert isinstance(value, str)
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueSchemaError(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueSchemaError(f"{path}: string is too long")
        return
    if expected in {"number", "integer"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueSchemaError(f"{path}: number is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueSchemaError(f"{path}: number is above maximum")


def validate_json_object(
    value: Any,
    schema: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    """Detach and validate a JSON object against a prevalidated closed schema."""

    copied = _copy_json(value, path=label)
    if not isinstance(copied, dict):
        raise ValueSchemaError(f"{label}: expected object")
    _validate_value(copied, schema, path=label)
    return copied


@dataclass(frozen=True)
class PromotedTool:
    """Framework-created, indivisible promoted capability binding."""

    capability_id: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    entrypoint: Entrypoint
    implementation_manifest_sha256: str
    validation_report_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.capability_id, str) or not _CAPABILITY_ID.fullmatch(self.capability_id):
            raise PromotionError("capability_id is invalid")
        if not callable(self.entrypoint):
            raise PromotionError("entrypoint must be callable")
        for name in ("implementation_manifest_sha256", "validation_report_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise PromotionError(f"{name} must be a lowercase SHA-256 digest")
        try:
            input_schema = validate_closed_object_schema(self.input_schema, label="input_schema")
            output_schema = validate_closed_object_schema(self.output_schema, label="output_schema")
        except SchemaError as exc:
            raise PromotionError(str(exc)) from None
        object.__setattr__(self, "input_schema", input_schema)
        object.__setattr__(self, "output_schema", output_schema)


@runtime_checkable
class CapabilityRouter(Protocol):
    """Common typed route used by ReAct and future non-LLM Consumers."""

    def visible_tools(self, allowed_capability_ids: Sequence[str]) -> list[dict[str, Any]]: ...

    def invoke(
        self,
        capability_id: str,
        arguments: Mapping[str, Any],
        *,
        allowed_capability_ids: Sequence[str],
    ) -> dict[str, Any]: ...


def _error_envelope(capability_id: Any, code: str, message: str, *, detail: str | None = None) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "status": "ERROR",
        "capability_id": capability_id if isinstance(capability_id, str) else None,
        "error": {"code": code, "message": message},
    }
    if detail is not None:
        envelope["error"]["detail"] = detail
    return envelope


class CapabilityToolLayer:
    """In-memory router built only from framework-created promoted bindings."""

    def __init__(self, promoted_tools: Sequence[PromotedTool]) -> None:
        if isinstance(promoted_tools, (str, bytes)) or not isinstance(promoted_tools, Sequence):
            raise PromotionError("promoted_tools must be a sequence")
        if not promoted_tools:
            raise PromotionError("at least one promoted tool is required")
        tools: dict[str, PromotedTool] = {}
        for item in promoted_tools:
            if type(item) is not PromotedTool:
                raise PromotionError("ToolLayer accepts only framework-created PromotedTool objects")
            if item.capability_id in tools:
                raise PromotionError("capability_id values must be unique")
            tools[item.capability_id] = item
        self._tools = MappingProxyType(tools)

    def _allowed(self, values: Sequence[str]) -> tuple[str, ...]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ToolLayerError("allowed_capability_ids must be a sequence")
        allowed = tuple(values)
        if any(not isinstance(item, str) for item in allowed) or len(allowed) != len(set(allowed)):
            raise ToolLayerError("allowed_capability_ids must contain unique strings")
        return allowed

    def visible_tools(self, allowed_capability_ids: Sequence[str]) -> list[dict[str, Any]]:
        allowed = self._allowed(allowed_capability_ids)
        return [
            {
                "name": capability_id,
                "parameters": _thaw_json(self._tools[capability_id].input_schema),
                "returns": _thaw_json(self._tools[capability_id].output_schema),
            }
            for capability_id in allowed
            if capability_id in self._tools
        ]

    def invoke(
        self,
        capability_id: str,
        arguments: Mapping[str, Any],
        *,
        allowed_capability_ids: Sequence[str],
    ) -> dict[str, Any]:
        try:
            allowed = self._allowed(allowed_capability_ids)
        except ToolLayerError:
            return _error_envelope(capability_id, "ROUTER_CONFIGURATION", "Capability route is unavailable.")
        if capability_id not in allowed or capability_id not in self._tools:
            return _error_envelope(capability_id, "UNAUTHORIZED_TOOL", "Capability is not authorized for this task.")

        tool = self._tools[capability_id]
        try:
            public_arguments = validate_json_object(arguments, tool.input_schema, label="arguments")
        except ValueSchemaError as exc:
            return _error_envelope(
                capability_id,
                "ARGUMENT_SCHEMA",
                "Capability arguments failed public schema validation.",
                detail=str(exc),
            )

        try:
            raw_result = tool.entrypoint(public_arguments)
        except Exception:
            return _error_envelope(capability_id, "TOOL_EXCEPTION", "Capability execution failed.")
        try:
            public_result = validate_json_object(raw_result, tool.output_schema, label="result")
        except ValueSchemaError:
            return _error_envelope(capability_id, "OUTPUT_SCHEMA", "Capability result failed public schema validation.")
        return {"status": "OK", "capability_id": capability_id, "result": public_result}
