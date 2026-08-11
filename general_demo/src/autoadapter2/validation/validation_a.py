"""Small static/import gate for a submitted ``capability.py`` module.

This is intentionally an experiment-grade AST gate, not a full Python
sandbox.  It verifies the sealed framework binding, the framework-derived
manifest, the public symbols, and a compact deny-list before Validation B.
"""

from __future__ import annotations

import ast
import builtins
import copy
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Mapping

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash, is_content_hash
from ..foundation.seals import create_seal, verify_seal


_SCHEMA_VERSION = "1.0.0"
_SAFE_IMPORT_ROOTS = {"__future__", "collections", "dataclasses", "math", "typing"}
_FORBIDDEN_IMPORT_ROOTS = {
    "builtins", "ctypes", "http", "importlib", "os", "pathlib", "requests",
    "shutil", "socket", "subprocess", "urllib",
}
_FORBIDDEN_NAMES = {
    "__import__", "compile", "delattr", "eval", "exec", "getattr", "globals",
    "input", "locals", "open", "setattr", "vars",
}
_FORBIDDEN_TERMS = ("mujoco", "translation", "private", "harness")
_LIFECYCLE_METHODS = {"close", "connect", "disconnect", "shutdown"}


@dataclass(frozen=True)
class BindingOverlay:
    """Candidate-specific names only; it cannot carry or alter suite content."""

    overlay: dict[str, Any]
    overlay_hash: str
    seal: dict[str, Any]


@dataclass(frozen=True)
class ValidationAResult:
    status: str
    report: dict[str, Any]
    report_hash: str
    report_seal: dict[str, Any]
    binding_result: dict[str, Any]
    source_hash: str | None
    implementation_manifest_hash: str
    candidate_module: ModuleType | None
    diagnostics: tuple[dict[str, str], ...]


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _binding_entries(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    if set(contract) != {"artifact_type", "schema_version", "design_hash", "bindings"}:
        raise ContractError("Validation A received a malformed Python Binding Contract")
    if contract.get("artifact_type") != "python_binding_contract" or contract.get("schema_version") != _SCHEMA_VERSION:
        raise ContractError("Validation A received a malformed Python Binding Contract")
    if not is_content_hash(contract.get("design_hash")):
        raise ContractError("Validation A received a malformed Python Binding Contract")
    bindings = contract.get("bindings")
    if not isinstance(bindings, list) or not bindings:
        raise ContractError("Validation A received a malformed Python Binding Contract")
    identifiers: set[str] = set()
    symbols: set[str] = set()
    result: list[dict[str, Any]] = []
    for binding in bindings:
        required = {"capability_id", "function_name", "parameters", "result_envelope"}
        if not isinstance(binding, Mapping) or set(binding) != required or not _text(binding.get("capability_id")) or not _text(binding.get("function_name")):
            raise ContractError("Validation A received a malformed Python Binding Contract")
        if binding["capability_id"] in identifiers or binding["function_name"] in symbols:
            raise ContractError("Validation A requires one-to-one Binding symbols")
        identifiers.add(binding["capability_id"])
        symbols.add(binding["function_name"])
        parameters = binding.get("parameters")
        envelope = binding.get("result_envelope")
        if not isinstance(parameters, list) or not isinstance(envelope, Mapping) or set(envelope) != {"kind", "fields"} or envelope.get("kind") != "mapping" or not isinstance(envelope.get("fields"), list):
            raise ContractError("Validation A received a malformed Python Binding Contract")
        parameter_names: set[str] = set()
        checked_parameters: list[dict[str, str]] = []
        for parameter in parameters:
            if not isinstance(parameter, Mapping) or set(parameter) != {"public_name", "parameter"} or not _text(parameter.get("public_name")) or not _text(parameter.get("parameter")):
                raise ContractError("Validation A received a malformed Python Binding Contract")
            if parameter["parameter"] in parameter_names or parameter["parameter"] == "_sdk":
                raise ContractError("Validation A requires distinct public and injected parameters")
            parameter_names.add(parameter["parameter"])
            checked_parameters.append({"public_name": parameter["public_name"], "parameter": parameter["parameter"]})
        result.append({
            "capability_id": binding["capability_id"],
            "function_name": binding["function_name"],
            "parameters": checked_parameters,
        })
    return result


def _verified_binding(contract: Mapping[str, Any], seal: Mapping[str, Any]) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    frozen = copy.deepcopy(dict(contract))
    contract_hash = content_hash(canonical_bytes(frozen))
    try:
        if not verify_seal(dict(seal)) or seal.get("artifact_type") != "python_binding_contract" or seal.get("artifact_hash") != contract_hash:
            raise ContractError("Validation A requires the exact sealed Python Binding Contract")
    except Exception as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Validation A requires the exact sealed Python Binding Contract") from exc
    return frozen, contract_hash, _binding_entries(frozen)


def _source_from_submission(submission: Any) -> tuple[str | None, list[dict[str, str]]]:
    if isinstance(submission, str):
        return (submission, []) if submission.strip() else (None, [_issue("CAPABILITY_SOURCE", "capability.py must be a non-empty source string")])
    if not isinstance(submission, Mapping) or set(submission) != {"capability.py"}:
        return None, [_issue("ONE_FILE_SUBMISSION", "Validation A accepts exactly one capability.py source file")]
    source = submission.get("capability.py")
    if not isinstance(source, str) or not source.strip():
        return None, [_issue("CAPABILITY_SOURCE", "capability.py must be a non-empty source string")]
    return source, []


def _manifest_issues(
    manifest: Any,
    manifest_seal: Any,
    *,
    contract: Mapping[str, Any],
    contract_hash: str,
    entries: list[Mapping[str, Any]],
    source_hash: str | None,
) -> tuple[str, list[dict[str, str]]]:
    if not isinstance(manifest, Mapping):
        return "", [_issue("IMPLEMENTATION_MANIFEST", "Stage 2 Implementation Manifest must be an object")]
    frozen = copy.deepcopy(dict(manifest))
    manifest_hash = content_hash(canonical_bytes(frozen))
    expected_symbols = [
        {"capability_id": entry["capability_id"], "function_name": entry["function_name"]}
        for entry in entries
    ]
    expected = {
        "artifact_type": "implementation_manifest",
        "schema_version": _SCHEMA_VERSION,
        "design_hash": contract["design_hash"],
        "binding_contract_hash": contract_hash,
        "source_file": "capability.py",
        "source_hash": source_hash,
        "symbols": expected_symbols,
    }
    issues: list[dict[str, str]] = []
    if frozen != expected:
        issues.append(_issue("IMPLEMENTATION_MANIFEST", "Implementation Manifest does not match the sealed Binding and submitted source"))
    try:
        if not isinstance(manifest_seal, Mapping) or not verify_seal(dict(manifest_seal)) or manifest_seal.get("artifact_type") != "implementation_manifest" or manifest_seal.get("artifact_hash") != manifest_hash:
            issues.append(_issue("IMPLEMENTATION_MANIFEST_SEAL", "Implementation Manifest seal is invalid"))
    except Exception:
        issues.append(_issue("IMPLEMENTATION_MANIFEST_SEAL", "Implementation Manifest seal is invalid"))
    return manifest_hash, issues


def _is_docstring(node: ast.AST) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _root_name(value: ast.AST) -> str | None:
    current = value
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _static_issues(tree: ast.Module, entries: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for index, node in enumerate(tree.body):
        if index == 0 and _is_docstring(node):
            continue
        if not isinstance(node, (ast.FunctionDef, ast.Import, ast.ImportFrom)):
            issues.append(_issue("IMPORT_SIDE_EFFECT", "capability.py may only define functions and approved imports at module scope"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _FORBIDDEN_IMPORT_ROOTS or root not in _SAFE_IMPORT_ROOTS:
                    issues.append(_issue("FORBIDDEN_IMPORT", f"import {alias.name} is not approved"))
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if node.level or root in _FORBIDDEN_IMPORT_ROOTS or root not in _SAFE_IMPORT_ROOTS:
                issues.append(_issue("FORBIDDEN_IMPORT", f"import from {node.module!r} is not approved"))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                name = node.func.id
                lowered = name.lower()
                if name in _FORBIDDEN_NAMES or any(term in lowered for term in _FORBIDDEN_TERMS):
                    issues.append(_issue("FORBIDDEN_CALL", f"{name} is forbidden in capability.py"))
                if name != "_sdk" and any(term in lowered for term in ("sdk", "robot", "client")):
                    issues.append(_issue("SDK_LIFECYCLE", "capability.py may not construct a robot SDK or client"))
            elif isinstance(node.func, ast.Attribute):
                attribute = node.func.attr.lower()
                root = _root_name(node.func.value)
                if attribute in _LIFECYCLE_METHODS:
                    issues.append(_issue("SDK_LIFECYCLE", f"{node.func.attr} is not candidate-owned"))
                if any(term in attribute for term in _FORBIDDEN_TERMS) or (root and any(term in root.lower() for term in _FORBIDDEN_IMPORT_ROOTS)):
                    issues.append(_issue("FORBIDDEN_CALL", f"{node.func.attr} is forbidden in capability.py"))
                if root and root != "_sdk" and any(term in root.lower() for term in ("sdk", "robot", "client")):
                    issues.append(_issue("SDK_LIFECYCLE", "capability.py may only use the injected _sdk object"))
        elif isinstance(node, ast.Name) and node.id == "__builtins__":
            issues.append(_issue("FORBIDDEN_NAMESPACE", "__builtins__ is forbidden in capability.py"))
        elif isinstance(node, ast.Attribute) and any(term in node.attr.lower() for term in _FORBIDDEN_TERMS):
            issues.append(_issue("FORBIDDEN_NAMESPACE", f"{node.attr} is forbidden in capability.py"))

    functions: dict[str, list[ast.FunctionDef]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            functions.setdefault(node.name, []).append(node)
        elif isinstance(node, ast.AsyncFunctionDef):
            functions.setdefault(node.name, [])
            issues.append(_issue("PUBLIC_SIGNATURE", f"{node.name} must be a synchronous public function"))
    if any(len(nodes) != 1 for nodes in functions.values()):
        issues.append(_issue("DUPLICATE_SYMBOL", "capability.py has duplicate top-level public symbols"))
    expected_symbols = {entry["function_name"] for entry in entries}
    for symbol in functions:
        if symbol.startswith("capability_") and symbol not in expected_symbols:
            issues.append(_issue("UNBOUND_SYMBOL", f"{symbol} is not in the Framework Binding"))
    for entry in entries:
        symbol = entry["function_name"]
        nodes = functions.get(symbol, [])
        if len(nodes) != 1:
            issues.append(_issue("MISSING_SYMBOL", f"capability.py must define exactly one {symbol}"))
            continue
        function = nodes[0]
        expected_parameters = [parameter["parameter"] for parameter in entry["parameters"]]
        arguments = function.args
        actual_parameters = [argument.arg for argument in arguments.args]
        actual_kwonly = [argument.arg for argument in arguments.kwonlyargs]
        if (
            arguments.posonlyargs
            or actual_parameters != expected_parameters
            or actual_kwonly != ["_sdk"]
            or arguments.vararg is not None
            or arguments.kwarg is not None
            or arguments.defaults
            or any(default is not None for default in arguments.kw_defaults)
        ):
            issues.append(_issue("PUBLIC_SIGNATURE", f"{symbol} does not exactly match the Framework Binding"))
        if not any(isinstance(item, ast.Name) and item.id == "_sdk" and isinstance(item.ctx, ast.Load) for item in ast.walk(function)):
            issues.append(_issue("SDK_INJECTION", f"{symbol} must use the injected _sdk parameter"))
        if any(isinstance(item, ast.Name) and item.id == "_sdk" and isinstance(item.ctx, ast.Store) for item in ast.walk(function)):
            issues.append(_issue("SDK_INJECTION", f"{symbol} may not replace the injected _sdk parameter"))
    return issues


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    root = name.split(".", 1)[0]
    if level or root not in _SAFE_IMPORT_ROOTS:
        raise ImportError(f"{name} is not in the Validation A import allowlist")
    return builtins.__import__(name, globals, locals, fromlist, level)


def _isolated_import(source: str) -> ModuleType:
    safe_builtins = {
        "Exception": Exception,
        "RuntimeError": RuntimeError,
        "ValueError": ValueError,
        "bool": bool,
        "dict": dict,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "__import__": _safe_import,
    }
    module = ModuleType("validated_capability")
    module.__dict__.update({"__name__": module.__name__, "__builtins__": safe_builtins})
    exec(compile(source, "capability.py", "exec"), module.__dict__, module.__dict__)
    return module


class ValidationARunner:
    """Validate one Stage 2 submission before any behavioral harness execution."""

    def run(
        self,
        binding_contract: Mapping[str, Any],
        binding_seal: Mapping[str, Any],
        submission: Mapping[str, Any] | str,
        implementation_manifest: Mapping[str, Any],
        implementation_manifest_seal: Mapping[str, Any],
    ) -> ValidationAResult:
        contract, contract_hash, entries = _verified_binding(binding_contract, binding_seal)
        source, diagnostics = _source_from_submission(submission)
        source_hash = content_hash(source.encode("utf-8")) if source is not None else None
        manifest_hash, manifest_issues = _manifest_issues(
            implementation_manifest,
            implementation_manifest_seal,
            contract=contract,
            contract_hash=contract_hash,
            entries=entries,
            source_hash=source_hash,
        )
        diagnostics.extend(manifest_issues)
        module: ModuleType | None = None
        if source is not None and not diagnostics:
            try:
                tree = ast.parse(source, filename="capability.py")
            except SyntaxError as exc:
                diagnostics.append(_issue("SYNTAX", f"capability.py does not parse: {exc.msg}"))
            else:
                diagnostics.extend(_static_issues(tree, entries))
                if not diagnostics:
                    try:
                        module = _isolated_import(source)
                    except Exception as exc:
                        diagnostics.append(_issue("ISOLATED_IMPORT", f"capability.py cannot be imported: {type(exc).__name__}"))
        symbols = [
            {
                "capability_id": entry["capability_id"],
                "function_name": entry["function_name"],
                "signature": f"({', '.join(parameter['parameter'] for parameter in entry['parameters'])}{', ' if entry['parameters'] else ''}*, _sdk)",
            }
            for entry in entries
        ]
        binding_result = {
            "artifact_type": "validation_a_binding_result",
            "schema_version": _SCHEMA_VERSION,
            "binding_contract_hash": contract_hash,
            "implementation_manifest_hash": manifest_hash,
            "source_hash": source_hash,
            "symbols": symbols,
        }
        status = "PASS" if not diagnostics else "FAIL"
        report = {
            "artifact_type": "validation_a_report",
            "schema_version": _SCHEMA_VERSION,
            "status": status,
            "binding_contract_hash": contract_hash,
            "implementation_manifest_hash": manifest_hash,
            "source_hash": source_hash,
            "binding_result": binding_result,
            "diagnostics": copy.deepcopy(diagnostics),
        }
        report_hash = content_hash(canonical_bytes(report))
        parents = [contract_hash] + ([manifest_hash] if is_content_hash(manifest_hash) else []) + ([source_hash] if source_hash else [])
        return ValidationAResult(
            status=status,
            report=report,
            report_hash=report_hash,
            report_seal=create_seal("validation_a_report", report_hash, parents),
            binding_result=binding_result,
            source_hash=source_hash,
            implementation_manifest_hash=manifest_hash,
            candidate_module=module if status == "PASS" else None,
            diagnostics=tuple(diagnostics),
        )

    validate = run


def create_execution_binding_overlay(result: ValidationAResult, validation_suite_hash: str) -> BindingOverlay:
    """Bind an A-passing candidate to a suite by hash without exposing suite content."""

    if result.status != "PASS" or result.candidate_module is None:
        raise ContractError("only a Validation A PASS may receive an execution binding overlay")
    if not is_content_hash(validation_suite_hash):
        raise ContractError("execution binding overlay requires a sealed suite hash")
    symbols = result.binding_result.get("symbols")
    if not isinstance(symbols, list):
        raise ContractError("Validation A binding result is malformed")
    capability_bindings = [
        {"capability_id": symbol.get("capability_id"), "function_name": symbol.get("function_name")}
        for symbol in symbols
    ]
    if not all(isinstance(item["capability_id"], str) and isinstance(item["function_name"], str) for item in capability_bindings):
        raise ContractError("Validation A binding result is malformed")
    overlay = {
        "artifact_type": "validation_execution_binding_overlay",
        "schema_version": _SCHEMA_VERSION,
        "suite_hash": validation_suite_hash,
        "implementation_manifest_hash": result.implementation_manifest_hash,
        "capability_bindings": capability_bindings,
    }
    overlay_hash = content_hash(canonical_bytes(overlay))
    return BindingOverlay(
        overlay=overlay,
        overlay_hash=overlay_hash,
        seal=create_seal("validation_execution_binding_overlay", overlay_hash, [validation_suite_hash, result.implementation_manifest_hash]),
    )
