"""TEST_FIXTURE_SCHEMA_SUBSET: deliberately not a full JSON Schema engine."""

from __future__ import annotations

import re
from typing import Any

from ..foundation.errors import ContractError


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


def _validate(value: Any, schema: dict[str, Any] | bool, path: str) -> None:
    if schema is True:
        return
    if schema is False or not isinstance(schema, dict):
        raise ContractError(f"{path}: rejected by TEST_FIXTURE_SCHEMA_SUBSET")
    if "const" in schema and value != schema["const"]:
        raise ContractError(f"{path}: const mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise ContractError(f"{path}: enum mismatch")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, item) for item in types):
            raise ContractError(f"{path}: type mismatch")
    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                raise ContractError(f"{path}: missing {required}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties", True) is False:
                raise ContractError(f"{path}: unexpected {key}")
    if isinstance(value, list) and isinstance(schema.get("items"), (dict, bool)):
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]")
    if isinstance(value, str) and "pattern" in schema:
        if re.search(schema["pattern"], value) is None:
            raise ContractError(f"{path}: pattern mismatch")


def validate_fixture_schema(instance: Any, schema: dict[str, Any] | bool) -> Any:
    _validate(instance, schema, "$")
    return instance
