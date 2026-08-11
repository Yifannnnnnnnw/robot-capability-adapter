"""Deterministic Python bindings for one sealed Capability Design.

This deliberately stays below Validation A: it derives names and verifies that a
submitted source parses and exposes every required function, but it does not
attempt to judge implementation semantics or SDK isolation.
"""

from __future__ import annotations

import ast
import copy
import keyword
import re
from dataclasses import dataclass
from typing import Any, Mapping

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash
from ..foundation.seals import create_seal, verify_seal
from ..generation.stage1 import check_capability_design


_BINDING_SCHEMA_VERSION = "1.0.0"
_IDENTIFIER_PARTS = re.compile(r"[^A-Za-z0-9_]+")


@dataclass(frozen=True)
class PythonBinding:
    """Framework-owned binding artifacts derived from an immutable Design."""

    contract: dict[str, Any]
    contract_hash: str
    contract_seal: dict[str, Any]
    starter_skeleton: str
    starter_skeleton_hash: str


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _python_name(value: str, *, prefix: str) -> str:
    """Make one deterministic, conservative Python identifier from public text."""

    normalized = _IDENTIFIER_PARTS.sub("_", value.strip()).strip("_").lower()
    if not normalized:
        normalized = "value"
    if normalized[0].isdigit() or keyword.iskeyword(normalized):
        normalized = f"value_{normalized}"
    return f"{prefix}{normalized}"


def _sealed_design(design: Mapping[str, Any], seal: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if not isinstance(design, Mapping):
        raise ContractError("Python Binding requires a Capability Design object")
    frozen_design = copy.deepcopy(dict(design))
    design_hash = content_hash(canonical_bytes(frozen_design))
    try:
        if not verify_seal(dict(seal)) or seal.get("artifact_type") != "capability_design" or seal.get("artifact_hash") != design_hash:
            raise ContractError("Python Binding requires the exact sealed Capability Design")
    except Exception as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Python Binding requires the exact sealed Capability Design") from exc
    issues = check_capability_design(frozen_design)
    if issues:
        raise ContractError(f"Python Binding requires a conformant Capability Design: {issues[0]['message']}")
    return frozen_design, design_hash


def _binding_entries(design: Mapping[str, Any]) -> list[dict[str, Any]]:
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list):  # Defensive: the Design checker above already rejects this.
        raise ContractError("Capability Design capabilities must be an array")
    symbols: set[str] = set()
    entries: list[dict[str, Any]] = []
    for capability in capabilities:
        if not isinstance(capability, Mapping) or not _text(capability.get("capability_id")):
            raise ContractError("Capability Design has an invalid capability")
        capability_id = str(capability["capability_id"])
        function_name = _python_name(capability_id, prefix="capability_")
        if function_name in symbols:
            raise ContractError("Capability IDs collide in the deterministic Python Binding")
        symbols.add(function_name)

        inputs = capability.get("inputs")
        outputs = capability.get("outputs")
        if not isinstance(inputs, list) or not isinstance(outputs, list):
            raise ContractError("Capability public interfaces must be arrays")
        parameter_names: set[str] = {"_sdk"}
        parameters: list[dict[str, str]] = []
        for field in inputs:
            if not isinstance(field, Mapping) or not _text(field.get("name")):
                raise ContractError("Capability inputs need public names")
            public_name = str(field["name"])
            parameter = _python_name(public_name, prefix="arg_")
            if parameter in parameter_names:
                raise ContractError("Capability public input names collide in the Python Binding")
            parameter_names.add(parameter)
            parameters.append({"public_name": public_name, "parameter": parameter})
        result_names: set[str] = set()
        result_fields: list[dict[str, str]] = []
        for field in outputs:
            if not isinstance(field, Mapping) or not _text(field.get("name")):
                raise ContractError("Capability outputs need public names")
            public_name = str(field["name"])
            if public_name in result_names:
                raise ContractError("Capability public output names must be unique")
            result_names.add(public_name)
            result_fields.append({"public_name": public_name, "result_key": public_name})
        entries.append({
            "capability_id": capability_id,
            "function_name": function_name,
            "parameters": parameters,
            "result_envelope": {"kind": "mapping", "fields": result_fields},
        })
    return entries


def _skeleton(entries: list[Mapping[str, Any]]) -> str:
    lines = [
        '"""Framework-generated Stage 2 starter; edit only this capability module."""',
        "",
    ]
    for entry in entries:
        arguments = [str(item["parameter"]) for item in entry["parameters"]]
        signature = ", ".join(arguments + ["*", "_sdk"])
        lines.extend([
            f"def {entry['function_name']}({signature}):",
            "    \"\"\"Implement one sealed public capability.\"\"\"",
            "    raise NotImplementedError('Implement this sealed capability binding.')",
            "",
        ])
    return "\n".join(lines)


def derive_python_binding(design: Mapping[str, Any], design_seal: Mapping[str, Any]) -> PythonBinding:
    """Deterministically create the only Python API Stage 2 is allowed to fill in."""

    frozen_design, design_hash = _sealed_design(design, design_seal)
    entries = _binding_entries(frozen_design)
    contract = {
        "artifact_type": "python_binding_contract",
        "schema_version": _BINDING_SCHEMA_VERSION,
        "design_hash": design_hash,
        "bindings": entries,
    }
    contract_hash = content_hash(canonical_bytes(contract))
    starter_skeleton = _skeleton(entries)
    return PythonBinding(
        contract=contract,
        contract_hash=contract_hash,
        contract_seal=create_seal("python_binding_contract", contract_hash, [design_hash]),
        starter_skeleton=starter_skeleton,
        starter_skeleton_hash=content_hash(starter_skeleton.encode("utf-8")),
    )


def verify_capability_source(source: Any, binding_contract: Mapping[str, Any]) -> list[dict[str, str]]:
    """Perform the intentionally small pre-Validation-A source sanity check."""

    if not isinstance(source, str) or not source.strip():
        raise ContractError("capability.py must be one non-empty source string")
    bindings = binding_contract.get("bindings")
    if not isinstance(bindings, list):
        raise ContractError("Python Binding Contract is malformed")
    try:
        tree = ast.parse(source, filename="capability.py")
    except SyntaxError as exc:
        raise ContractError(f"capability.py does not parse: {exc.msg}") from exc
    definitions = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if len(definitions) != len(set(definitions)):
        raise ContractError("capability.py has duplicate top-level function definitions")
    expected: list[tuple[str, str]] = []
    for binding in bindings:
        if not isinstance(binding, Mapping) or not _text(binding.get("capability_id")) or not _text(binding.get("function_name")):
            raise ContractError("Python Binding Contract is malformed")
        expected.append((str(binding["capability_id"]), str(binding["function_name"])))
    missing = [symbol for _, symbol in expected if symbol not in definitions]
    if missing:
        raise ContractError(f"capability.py is missing bound function(s): {sorted(missing)}")
    return [
        {"capability_id": capability_id, "function_name": function_name}
        for capability_id, function_name in expected
    ]


def derive_implementation_manifest(
    *,
    design_hash: str,
    binding_hash: str,
    implementation_bundle_hash: str,
    source_hash: str,
    symbols: list[Mapping[str, str]],
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Make a Framework-owned manifest; it never accepts a model-authored one."""

    manifest = {
        "artifact_type": "implementation_manifest",
        "schema_version": _BINDING_SCHEMA_VERSION,
        "design_hash": design_hash,
        "binding_contract_hash": binding_hash,
        "implementation_bundle_hash": implementation_bundle_hash,
        "source_file": "capability.py",
        "source_hash": source_hash,
        "symbols": [dict(symbol) for symbol in symbols],
    }
    manifest_hash = content_hash(canonical_bytes(manifest))
    return (
        manifest,
        manifest_hash,
        create_seal(
            "implementation_manifest",
            manifest_hash,
            [design_hash, binding_hash, implementation_bundle_hash, source_hash],
        ),
    )
