"""Typed B1 capability-interface adapter for the prospective B2 extension.

The adapter deliberately stops at the worker transport boundary.  Its invoker
must call a persistent, credential-free worker and return only the Framework's
bounded public operation observation.  Physical success remains a Harness
decision outside this module.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


PublicCapabilityInvoker = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


class CapabilityAdapterError(RuntimeError):
    """Raised when the fixed interface or a proposed leaf is invalid."""


class CapabilityInvocationError(CapabilityAdapterError):
    """Raised when a valid leaf cannot cross the public worker boundary."""


@dataclass(frozen=True)
class CapabilityContract:
    """The public, controller-visible part of one fixed B1 capability."""

    capability_id: str
    method_name: str
    description: str
    request_schema: Mapping[str, Any]

    def public_definition(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "capability_name": self.method_name,
            "description": self.description,
            "request_schema": _json_copy(
                self.request_schema,
                label=f"{self.method_name} request schema",
            ),
            "invocation_abi": "driver.<capability_name>(request=<request>)",
        }


class CapabilityAdapter:
    """Expose a sealed capability design as validated, typed ReCAP leaves.

    ``invoke`` receives ``(method_name, {"request": native_request})``.  This
    transport shape is exactly compatible with the fixed B1 ABI
    ``driver.<method>(request=<capability-native object>)``; no task-level
    wrapper fields are inserted.
    """

    def __init__(
        self,
        capability_design: Mapping[str, Any],
        invoke: PublicCapabilityInvoker,
    ) -> None:
        if not callable(invoke):
            raise CapabilityAdapterError("invoke must be callable")
        design = _json_copy(capability_design, label="capability design")
        if not isinstance(design, dict):
            raise CapabilityAdapterError("capability design must be an object")

        abi = design.get("invocation_abi")
        if not isinstance(abi, dict) or abi.get("kind") != "capability_request":
            raise CapabilityAdapterError(
                "capability design must declare the capability_request ABI"
            )
        if abi.get("method_call") != "method(request=request)":
            raise CapabilityAdapterError(
                "capability design must declare method(request=request)"
            )

        self.capability_design_id = _required_string(
            design.get("capability_design_id"), "capability_design_id"
        )
        self.robot_configuration_id = _required_string(
            design.get("robot_configuration_id"), "robot_configuration_id"
        )

        raw_capabilities = design.get("capabilities")
        if not isinstance(raw_capabilities, list) or not raw_capabilities:
            raise CapabilityAdapterError("capabilities must be a non-empty array")

        contracts: list[CapabilityContract] = []
        seen_ids: set[str] = set()
        seen_methods: set[str] = set()
        for index, raw in enumerate(raw_capabilities):
            if not isinstance(raw, dict):
                raise CapabilityAdapterError(f"capabilities[{index}] must be an object")
            capability_id = _required_string(
                raw.get("capability_id"), f"capabilities[{index}].capability_id"
            )
            method_name = _required_string(
                raw.get("method_name"), f"capabilities[{index}].method_name"
            )
            if not method_name.isidentifier():
                raise CapabilityAdapterError(
                    f"capabilities[{index}].method_name must be a Python identifier"
                )
            description = _required_string(
                raw.get("description"), f"capabilities[{index}].description"
            )
            if capability_id in seen_ids:
                raise CapabilityAdapterError(f"duplicate capability_id {capability_id!r}")
            if method_name in seen_methods:
                raise CapabilityAdapterError(f"duplicate method_name {method_name!r}")

            schema = raw.get("request_schema")
            if not isinstance(schema, dict):
                raise CapabilityAdapterError(
                    f"{method_name} request_schema must be an object"
                )
            _validate_schema_definition(schema, path=f"{method_name}.request_schema")
            if schema.get("type") != "object":
                raise CapabilityAdapterError(
                    f"{method_name} request_schema must have type object"
                )
            properties = schema.get("properties")
            required = schema.get("required")
            if schema.get("additionalProperties") is not False:
                raise CapabilityAdapterError(
                    f"{method_name} request_schema must be closed"
                )
            if not isinstance(properties, dict) or set(required or []) != set(properties):
                raise CapabilityAdapterError(
                    f"{method_name} request_schema must require every property"
                )

            contracts.append(
                CapabilityContract(
                    capability_id=capability_id,
                    method_name=method_name,
                    description=description,
                    request_schema=schema,
                )
            )
            seen_ids.add(capability_id)
            seen_methods.add(method_name)

        self._contracts = tuple(contracts)
        self._by_method = {contract.method_name: contract for contract in contracts}
        self._invoke = invoke

    @property
    def contracts(self) -> tuple[CapabilityContract, ...]:
        return self._contracts

    def public_catalog(self) -> list[dict[str, Any]]:
        """Return only interface-derived tool information visible to a controller."""

        return [contract.public_definition() for contract in self._contracts]

    def validate_request(
        self,
        capability_name: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(capability_name, str):
            raise CapabilityAdapterError("capability_name must be a string")
        contract = self._by_method.get(capability_name)
        if contract is None:
            raise CapabilityAdapterError(
                f"unknown capability_name {capability_name!r}"
            )
        copied = _json_copy(request, label=f"{capability_name} request")
        _validate_value(copied, contract.request_schema, path="request")
        return copied

    def execute(
        self,
        capability_name: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        native_request = self.validate_request(capability_name, request)
        try:
            observation = self._invoke(
                capability_name,
                {"request": native_request},
            )
        except Exception as exc:
            raise CapabilityInvocationError(
                f"{capability_name} invocation failed ({type(exc).__name__})"
            ) from None

        try:
            copied = _json_copy(
                observation,
                label=f"{capability_name} public operation observation",
            )
        except CapabilityAdapterError:
            raise CapabilityInvocationError(
                f"{capability_name} public operation observation is invalid"
            ) from None
        if not isinstance(copied, dict):
            raise CapabilityInvocationError(
                f"{capability_name} public operation observation must be an object"
            )
        return copied


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
    "description",
    "unit",
    "frame",
}
_SUPPORTED_TYPES = {"object", "array", "number"}


def _validate_schema_definition(schema: Mapping[str, Any], *, path: str) -> None:
    unknown = set(schema) - _SCHEMA_KEYS
    if unknown:
        raise CapabilityAdapterError(
            f"{path} uses unsupported schema keywords: {sorted(unknown)}"
        )
    schema_type = schema.get("type")
    if schema_type not in _SUPPORTED_TYPES:
        raise CapabilityAdapterError(f"{path}.type is unsupported")

    if schema_type == "object":
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, dict):
            raise CapabilityAdapterError(f"{path}.properties must be an object")
        if not isinstance(required, list) or any(
            not isinstance(item, str) for item in required
        ):
            raise CapabilityAdapterError(f"{path}.required must be a string array")
        if len(required) != len(set(required)) or not set(required) <= set(properties):
            raise CapabilityAdapterError(f"{path}.required is invalid")
        if schema.get("additionalProperties") is not False:
            raise CapabilityAdapterError(f"{path} must set additionalProperties=false")
        for name, child in properties.items():
            if not isinstance(name, str) or not isinstance(child, dict):
                raise CapabilityAdapterError(f"{path}.properties is invalid")
            _validate_schema_definition(child, path=f"{path}.properties.{name}")

    if schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            raise CapabilityAdapterError(f"{path}.items must be an object")
        _validate_schema_definition(items, path=f"{path}.items")
        _validate_nonnegative_integer(schema, "minItems", path)
        _validate_nonnegative_integer(schema, "maxItems", path)
        if (
            "minItems" in schema
            and "maxItems" in schema
            and schema["minItems"] > schema["maxItems"]
        ):
            raise CapabilityAdapterError(f"{path} has minItems > maxItems")
    if schema_type == "number":
        for keyword in (
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
        ):
            if keyword in schema and not _is_finite_number(schema[keyword]):
                raise CapabilityAdapterError(f"{path}.{keyword} must be finite")


def _validate_value(value: Any, schema: Mapping[str, Any], *, path: str) -> None:
    schema_type = schema["type"]
    if schema_type == "object":
        if not isinstance(value, dict):
            raise CapabilityAdapterError(f"{path} must be an object")
        properties = schema["properties"]
        missing = [name for name in schema["required"] if name not in value]
        extra = sorted(set(value) - set(properties))
        if missing:
            raise CapabilityAdapterError(f"{path} is missing required fields {missing}")
        if extra:
            raise CapabilityAdapterError(f"{path} has unexpected fields {extra}")
        for name, child_value in value.items():
            _validate_value(
                child_value,
                properties[name],
                path=f"{path}.{name}",
            )
    elif schema_type == "array":
        if not isinstance(value, list):
            raise CapabilityAdapterError(f"{path} must be an array")
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise CapabilityAdapterError(f"{path} has fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise CapabilityAdapterError(f"{path} has more than maxItems")
        for index, child_value in enumerate(value):
            _validate_value(child_value, schema["items"], path=f"{path}[{index}]")
    elif schema_type == "number":
        if not _is_finite_number(value):
            raise CapabilityAdapterError(f"{path} must be a finite number")
        _validate_numeric_bounds(value, schema, path=path)


def _validate_numeric_bounds(
    value: int | float,
    schema: Mapping[str, Any],
    *,
    path: str,
) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        raise CapabilityAdapterError(f"{path} is below minimum")
    if "maximum" in schema and value > schema["maximum"]:
        raise CapabilityAdapterError(f"{path} is above maximum")
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        raise CapabilityAdapterError(f"{path} is not above exclusiveMinimum")
    if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
        raise CapabilityAdapterError(f"{path} is not below exclusiveMaximum")


def _validate_nonnegative_integer(
    schema: Mapping[str, Any],
    keyword: str,
    path: str,
) -> None:
    if keyword not in schema:
        return
    value = schema[keyword]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CapabilityAdapterError(f"{path}.{keyword} must be a nonnegative integer")


def _json_copy(value: Any, *, label: str) -> Any:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise CapabilityAdapterError(f"{label} must be finite JSON: {exc}") from None


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityAdapterError(f"{label} must be a non-empty string")
    return value


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


__all__ = [
    "CapabilityAdapter",
    "CapabilityAdapterError",
    "CapabilityContract",
    "CapabilityInvocationError",
    "PublicCapabilityInvoker",
]
