"""Compatibility adapter for the canonical capability-v2 ReCAP runtime.

The adapter exposes only the sealed capability method names and closed native
request schemas.  Criteria, task support, private inputs, and Harness verdicts
never enter the public catalogue.  The implementation remains under ``b2``
only because existing session/worker callers import this path; Task Demo's
canonical controller lives in :mod:`autoadapter2.task_demo.recap`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from autoadapter2.capability_design.protocol import (
    CAPABILITY_INVOCATION_ABI,
    CAPABILITY_PROTOCOL_VERSION,
    CapabilityProtocolError,
    json_copy,
    validate_schema_definition,
    validate_schema_value,
)


PublicCapabilityInvoker = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


class CapabilityAdapterError(RuntimeError):
    """Raised when a sealed interface or proposed capability leaf is invalid."""


class CapabilityInvocationError(CapabilityAdapterError):
    """Raised when a valid leaf cannot cross the public worker boundary."""


@dataclass(frozen=True)
class CapabilityContract:
    capability_id: str
    method_name: str
    description: str
    request_schema: Mapping[str, Any]
    effect: str | None = None

    def public_definition(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "capability_name": self.method_name,
            "method_name": self.method_name,
            "description": self.description,
            "request_schema": _public_request_schema(self.request_schema),
            "invocation_abi": "driver.<capability_name>(request=<request>)",
        }


class CapabilityAdapter:
    """Expose typed tools dynamically from one sealed capability design."""

    def __init__(self, capability_design: Mapping[str, Any], invoke: PublicCapabilityInvoker) -> None:
        if not callable(invoke):
            raise CapabilityAdapterError("invoke must be callable")
        design = json_copy(dict(capability_design), label="capability design")
        if not isinstance(design, dict):
            raise CapabilityAdapterError("capability design must be an object")
        protocol_version = design.get("capability_protocol_version")
        abi = design.get("invocation_abi")
        if protocol_version == CAPABILITY_PROTOCOL_VERSION:
            if abi != CAPABILITY_INVOCATION_ABI:
                raise CapabilityAdapterError("capability-v2 design has an invalid invocation ABI")
        elif not isinstance(abi, Mapping) or abi.get("kind") != "capability_request" or abi.get("method_call") != "method(request=request)":
            # Retain the old B2 fixture/import shape without creating another
            # runtime protocol.  It is accepted only at this compatibility
            # edge; public v2 artifacts always take the branch above.
            raise CapabilityAdapterError("capability design must declare method(request=request)")

        self.capability_design_id = str(
            design.get("capability_design_id", f"{design.get('robot_configuration_id', 'unknown')}::capability-v2")
        )
        if not self.capability_design_id.strip():
            raise CapabilityAdapterError("capability_design_id must be non-empty")
        self.robot_configuration_id = _required_string(
            design.get("robot_configuration_id"), "robot_configuration_id"
        )
        raw_capabilities = design.get("capabilities")
        if not isinstance(raw_capabilities, list) or not raw_capabilities:
            raise CapabilityAdapterError("capabilities must be a non-empty array")
        contracts: list[CapabilityContract] = []
        seen_ids: set[str] = set()
        seen_methods: set[str] = set()
        strict_v2 = protocol_version == CAPABILITY_PROTOCOL_VERSION
        for index, raw in enumerate(raw_capabilities):
            if not isinstance(raw, Mapping):
                raise CapabilityAdapterError(f"capabilities[{index}] must be an object")
            capability_id = _required_string(
                raw.get("capability_id", raw.get("id")), f"capabilities[{index}].capability_id"
            )
            method_name = _required_string(
                raw.get("method_name", raw.get("method")), f"capabilities[{index}].method_name"
            )
            if not method_name.isidentifier() or method_name in {"build", "finish_task_demo"}:
                raise CapabilityAdapterError(f"capabilities[{index}].method_name must be a Python identifier")
            if capability_id in seen_ids or method_name in seen_methods:
                raise CapabilityAdapterError(f"duplicate capability ID or method name {capability_id!r}/{method_name!r}")
            description = _required_string(raw.get("description"), f"capabilities[{index}].description")
            schema = raw.get("request_schema")
            if not isinstance(schema, Mapping):
                raise CapabilityAdapterError(f"{method_name} request_schema must be an object")
            try:
                if strict_v2:
                    schema_copy = validate_schema_definition(schema, path=f"{method_name}.request_schema")
                else:
                    schema_copy = _legacy_schema_copy(schema, path=f"{method_name}.request_schema")
            except (CapabilityProtocolError, CapabilityAdapterError) as exc:
                raise CapabilityAdapterError(str(exc)) from None
            if schema_copy.get("type") != "object" or schema_copy.get("additionalProperties") is not False:
                raise CapabilityAdapterError(f"{method_name} request_schema must be a closed object")
            properties = schema_copy.get("properties")
            required = schema_copy.get("required")
            if not isinstance(properties, dict) or not isinstance(required, list) or set(required) != set(properties):
                raise CapabilityAdapterError(f"{method_name} request_schema must require every property")
            contracts.append(
                CapabilityContract(
                    capability_id=capability_id,
                    method_name=method_name,
                    description=description,
                    request_schema=schema_copy,
                    effect=raw.get("effect") if isinstance(raw.get("effect"), str) else None,
                )
            )
            seen_ids.add(capability_id)
            seen_methods.add(method_name)
        self._design = design
        self._contracts = tuple(contracts)
        self._by_method = {contract.method_name: contract for contract in contracts}
        self._invoke = invoke

    @property
    def contracts(self) -> tuple[CapabilityContract, ...]:
        return self._contracts

    @property
    def capability_design(self) -> Mapping[str, Any]:
        return json_copy(self._design, label="capability design")

    def public_catalog(self) -> list[dict[str, Any]]:
        """Return only interface-derived typed tool definitions."""

        return [contract.public_definition() for contract in self._contracts]

    def validate_request(self, capability_name: str, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(capability_name, str):
            raise CapabilityAdapterError("capability_name must be text")
        contract = self._by_method.get(capability_name)
        if contract is None:
            raise CapabilityAdapterError(f"unknown capability_name {capability_name!r}")
        copied = json_copy(request, label=f"{capability_name} request")
        try:
            validate_schema_value(copied, contract.request_schema)
        except CapabilityProtocolError as exc:
            raise CapabilityAdapterError(str(exc)) from None
        return copied

    def execute(self, capability_name: str, request: Mapping[str, Any]) -> dict[str, Any]:
        native_request = self.validate_request(capability_name, request)
        try:
            observation = self._invoke(capability_name, {"request": native_request})
        except Exception as exc:
            raise CapabilityInvocationError(
                f"{capability_name} invocation failed ({type(exc).__name__})"
            ) from None
        try:
            copied = json_copy(observation, label=f"{capability_name} public operation observation")
        except CapabilityProtocolError:
            raise CapabilityInvocationError(
                f"{capability_name} public operation observation is invalid"
            ) from None
        if not isinstance(copied, dict):
            raise CapabilityInvocationError(
                f"{capability_name} public operation observation must be an object"
            )
        return copied


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityAdapterError(f"{label} must be a non-empty string")
    return value.strip()


def _public_request_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Remove design-time source refs while retaining typed bounds/units."""

    result = {
        key: json_copy(value, label=f"public request schema.{key}")
        for key, value in schema.items()
        if key != "evidence_refs"
    }
    if result.get("type") == "object":
        result["properties"] = {
            name: _public_request_schema(child)
            for name, child in result.get("properties", {}).items()
        }
    elif result.get("type") == "array" and isinstance(result.get("items"), Mapping):
        result["items"] = _public_request_schema(result["items"])
    return result


def _legacy_schema_copy(schema: Mapping[str, Any], *, path: str) -> dict[str, Any]:
    """Small relaxed validator for pre-v2 B2 fixtures only."""

    copied = json_copy(dict(schema), label=path)
    if not isinstance(copied, dict) or copied.get("type") not in {"object", "array", "number", "integer", "string", "boolean"}:
        raise CapabilityAdapterError(f"{path}.type is unsupported")
    kind = copied["type"]
    if kind == "object":
        properties = copied.get("properties")
        required = copied.get("required")
        if not isinstance(properties, dict) or not isinstance(required, list) or copied.get("additionalProperties") is not False:
            raise CapabilityAdapterError(f"{path} must be a closed object schema")
        if set(required) != set(properties):
            raise CapabilityAdapterError(f"{path}.required must require every property")
        for name, child in properties.items():
            if not isinstance(name, str) or not isinstance(child, Mapping):
                raise CapabilityAdapterError(f"{path}.properties is invalid")
            _legacy_schema_copy(child, path=f"{path}.properties.{name}")
    elif kind == "array":
        if not isinstance(copied.get("items"), Mapping):
            raise CapabilityAdapterError(f"{path}.items must be an object")
        _legacy_schema_copy(copied["items"], path=f"{path}.items")
    elif kind in {"number", "integer"}:
        for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            if key in copied and not _finite_number(copied[key]):
                raise CapabilityAdapterError(f"{path}.{key} must be finite")
    return copied


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


__all__ = [
    "CapabilityAdapter",
    "CapabilityAdapterError",
    "CapabilityContract",
    "CapabilityInvocationError",
    "PublicCapabilityInvoker",
]
