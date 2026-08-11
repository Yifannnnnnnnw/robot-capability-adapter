from __future__ import annotations

import re
from typing import Any

from ..foundation.errors import SchemaValidationError


def _type_ok(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "null": value is None,
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }.get(expected, False)


def _passes(value: Any, schema: dict[str, Any]) -> bool:
    try:
        _validate(value, schema, "$")
        return True
    except SchemaValidationError:
        return False


def _validate(value: Any, schema: dict[str, Any] | bool, path: str) -> None:
    if schema is True:
        return
    if schema is False:
        raise SchemaValidationError(f"{path}: rejected by schema")
    if not isinstance(schema, dict):
        raise SchemaValidationError(f"{path}: schema must be an object")
    if "const" in schema and value != schema["const"]:
        raise SchemaValidationError(f"{path}: const mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path}: enum mismatch")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, item) for item in types):
            raise SchemaValidationError(f"{path}: type mismatch")
    for item in schema.get("allOf", []):
        _validate(value, item, path)
    if "anyOf" in schema and not any(_passes(value, item) for item in schema["anyOf"]):
        raise SchemaValidationError(f"{path}: anyOf failed")
    if "oneOf" in schema and sum(_passes(value, item) for item in schema["oneOf"]) != 1:
        raise SchemaValidationError(f"{path}: oneOf failed")
    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                raise SchemaValidationError(f"{path}: missing {required}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties", True) is False:
                raise SchemaValidationError(f"{path}: unexpected {key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                _validate(item, schema["additionalProperties"], f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise SchemaValidationError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaValidationError(f"{path}: too many items")
        if schema.get("uniqueItems") and len({repr(item) for item in value}) != len(value):
            raise SchemaValidationError(f"{path}: duplicate items")
        if isinstance(schema.get("items"), (dict, bool)):
            for index, item in enumerate(value):
                _validate(item, schema["items"], f"{path}[{index}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise SchemaValidationError(f"{path}: string too short")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise SchemaValidationError(f"{path}: pattern mismatch")


def validate_json(instance: Any, schema: dict[str, Any] | bool) -> Any:
    _validate(instance, schema, "$")
    return instance
