"""Minimal experimental Validation A and opaque candidate handles.

The profile intentionally admits a tiny Python subset.  It is a contract
checker plus a Framework-owned fixture probe, not a process sandbox.
"""

from __future__ import annotations

import ast
import copy
import math
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Mapping

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash, is_content_hash
from ..foundation.seals import create_seal, verify_seal
from ..generation.stage1 import check_capability_design


_SCHEMA_VERSION = "1.0.0"
_HANDLE_TOKEN = object()


@dataclass(frozen=True)
class ValidationAProfile:
    """Framework-owned fixtures and the per-capability injected SDK facade."""

    sdk_facade_members: Mapping[str, tuple[str, ...]]
    fixture_probes: Mapping[str, Mapping[str, Any]]
    profile_id: str = "experimental-python-a-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ContractError("Validation A profile_id must be public text")

    @property
    def profile_hash(self) -> str:
        body = {
            "profile_id": self.profile_id,
            "sdk_facade_members": {
                key: list(value) for key, value in sorted(self.sdk_facade_members.items())
            },
            "fixture_probes": copy.deepcopy(dict(self.fixture_probes)),
        }
        return content_hash(canonical_bytes(body))


@dataclass(frozen=True)
class BindingOverlay:
    overlay: dict[str, Any]
    overlay_hash: str
    seal: dict[str, Any]


class ValidatedCandidateHandle:
    """A-produced opaque binding of exact source, manifest, module, and overlay."""

    __slots__ = (
        "_source_hash", "_implementation_manifest_hash", "_implementation_bundle_hash", "_module", "_contracts",
        "_overlay", "_a_report_hash",
    )

    def __init__(
        self,
        token: object,
        *,
        source_hash: str,
        implementation_manifest_hash: str,
        implementation_bundle_hash: str,
        module: ModuleType,
        contracts: Mapping[str, Mapping[str, Any]],
        a_report_hash: str,
        overlay: BindingOverlay | None = None,
    ):
        if token is not _HANDLE_TOKEN:
            raise ContractError("ValidatedCandidateHandle is Framework-created only")
        self._source_hash = source_hash
        self._implementation_manifest_hash = implementation_manifest_hash
        self._implementation_bundle_hash = implementation_bundle_hash
        self._module = module
        self._contracts = copy.deepcopy(dict(contracts))
        self._a_report_hash = a_report_hash
        self._overlay = overlay

    @property
    def source_hash(self) -> str:
        return self._source_hash

    @property
    def implementation_manifest_hash(self) -> str:
        return self._implementation_manifest_hash

    @property
    def implementation_bundle_hash(self) -> str:
        return self._implementation_bundle_hash

    @property
    def overlay_hash(self) -> str | None:
        return self._overlay.overlay_hash if self._overlay is not None else None

    def _with_overlay(self, overlay: BindingOverlay) -> "ValidatedCandidateHandle":
        return ValidatedCandidateHandle(
            _HANDLE_TOKEN,
            source_hash=self._source_hash,
            implementation_manifest_hash=self._implementation_manifest_hash,
            implementation_bundle_hash=self._implementation_bundle_hash,
            module=self._module,
            contracts=self._contracts,
            a_report_hash=self._a_report_hash,
            overlay=overlay,
        )

    def _invoke(self, capability_id: str, inputs: Mapping[str, Any], sdk: object) -> Any:
        contract = self._contracts.get(capability_id)
        if not isinstance(contract, Mapping):
            raise ContractError("candidate handle does not bind this capability")
        values = _runtime_inputs(inputs, contract["inputs"])
        function = getattr(self._module, contract["function_name"])
        arguments = {
            parameter["parameter"]: values[parameter["public_name"]]
            for parameter in contract["parameters"]
        }
        result = function(**arguments, _sdk=sdk)
        _runtime_result(result, contract["outputs"])
        return result


@dataclass(frozen=True)
class ValidationAResult:
    status: str
    report: dict[str, Any]
    report_hash: str
    report_seal: dict[str, Any]
    binding_result: dict[str, Any]
    source_hash: str | None
    implementation_manifest_hash: str
    implementation_bundle_hash: str
    candidate_handle: ValidatedCandidateHandle | None
    diagnostics: tuple[dict[str, str], ...]


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _verified_design(design: Mapping[str, Any], seal: Mapping[str, Any]) -> tuple[dict[str, Any], str, dict[str, dict[str, Any]]]:
    frozen = copy.deepcopy(dict(design))
    design_hash = content_hash(canonical_bytes(frozen))
    try:
        if not verify_seal(dict(seal)) or seal.get("artifact_type") != "capability_design" or seal.get("artifact_hash") != design_hash:
            raise ContractError("Validation A requires the exact sealed Capability Design")
    except Exception as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Validation A requires the exact sealed Capability Design") from exc
    issues = check_capability_design(frozen)
    if issues:
        raise ContractError("Validation A requires a conformed Capability Design")
    capabilities = frozen.get("capabilities")
    assert isinstance(capabilities, list)
    return frozen, design_hash, {item["capability_id"]: item for item in capabilities}


def _verified_binding(contract: Mapping[str, Any], seal: Mapping[str, Any], design_hash: str) -> tuple[dict[str, Any], str]:
    frozen = copy.deepcopy(dict(contract))
    contract_hash = content_hash(canonical_bytes(frozen))
    try:
        if not verify_seal(dict(seal)) or seal.get("artifact_type") != "python_binding_contract" or seal.get("artifact_hash") != contract_hash:
            raise ContractError("Validation A requires the exact sealed Python Binding Contract")
    except Exception as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Validation A requires the exact sealed Python Binding Contract") from exc
    if (
        set(frozen) != {"artifact_type", "schema_version", "design_hash", "bindings"}
        or frozen.get("artifact_type") != "python_binding_contract"
        or frozen.get("schema_version") != _SCHEMA_VERSION
        or frozen.get("design_hash") != design_hash
        or not isinstance(frozen.get("bindings"), list)
    ):
        raise ContractError("Validation A received a malformed Python Binding Contract")
    return frozen, contract_hash


def _capability_contracts(
    design_capabilities: Mapping[str, Mapping[str, Any]],
    binding_contract: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    bindings = binding_contract["bindings"]
    assert isinstance(bindings, list)
    contracts: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        required = {"capability_id", "function_name", "parameters", "result_envelope"}
        if not isinstance(binding, Mapping) or set(binding) != required or not _text(binding.get("capability_id")) or not _text(binding.get("function_name")):
            raise ContractError("Validation A received a malformed Python Binding Contract")
        capability_id = binding["capability_id"]
        semantic = design_capabilities.get(capability_id)
        if semantic is None or capability_id in contracts:
            raise ContractError("Validation A requires one Binding entry per Design capability")
        parameters = binding.get("parameters")
        envelope = binding.get("result_envelope")
        if not isinstance(parameters, list) or not isinstance(envelope, Mapping) or set(envelope) != {"kind", "fields"} or envelope.get("kind") != "mapping" or not isinstance(envelope.get("fields"), list):
            raise ContractError("Validation A received a malformed Python Binding Contract")
        inputs = semantic["inputs"]
        outputs = semantic["outputs"]
        if len(parameters) != len(inputs) or len(envelope["fields"]) != len(outputs):
            raise ContractError("Validation A Binding does not match Design envelopes")
        checked_parameters: list[dict[str, str]] = []
        for parameter, field in zip(parameters, inputs, strict=True):
            if not isinstance(parameter, Mapping) or set(parameter) != {"public_name", "parameter"} or parameter.get("public_name") != field["name"] or not _text(parameter.get("parameter")) or parameter["parameter"] == "_sdk":
                raise ContractError("Validation A Binding does not match Design inputs")
            checked_parameters.append({"public_name": parameter["public_name"], "parameter": parameter["parameter"]})
        for result_field, field in zip(envelope["fields"], outputs, strict=True):
            if not isinstance(result_field, Mapping) or set(result_field) != {"public_name", "result_key"} or result_field.get("public_name") != field["name"] or result_field.get("result_key") != field["name"]:
                raise ContractError("Validation A Binding does not match Design outputs")
        contracts[capability_id] = {
            "function_name": binding["function_name"],
            "parameters": checked_parameters,
            "inputs": copy.deepcopy(inputs),
            "outputs": copy.deepcopy(outputs),
        }
    if set(contracts) != set(design_capabilities):
        raise ContractError("Validation A requires one-to-one Design and Binding coverage")
    return contracts


def _source_from_submission(submission: Any) -> tuple[str | None, list[dict[str, str]]]:
    if not isinstance(submission, Mapping) or set(submission) != {"capability.py"}:
        return None, [_issue("ONE_FILE_SUBMISSION", "Validation A accepts exactly one capability.py source file")]
    source = submission.get("capability.py")
    if not isinstance(source, str) or not source.strip():
        return None, [_issue("CAPABILITY_SOURCE", "capability.py must be a non-empty source string")]
    return source, []


def _manifest_issues(
    manifest: Mapping[str, Any],
    manifest_seal: Mapping[str, Any],
    *,
    design_hash: str,
    binding_hash: str,
    implementation_bundle_hash: str,
    contracts: Mapping[str, Mapping[str, Any]],
    source_hash: str | None,
) -> tuple[str, list[dict[str, str]]]:
    frozen = copy.deepcopy(dict(manifest))
    manifest_hash = content_hash(canonical_bytes(frozen))
    expected_symbols = [
        {"capability_id": capability_id, "function_name": contracts[capability_id]["function_name"]}
        for capability_id in contracts
    ]
    expected = {
        "artifact_type": "implementation_manifest",
        "schema_version": _SCHEMA_VERSION,
        "design_hash": design_hash,
        "binding_contract_hash": binding_hash,
        "implementation_bundle_hash": implementation_bundle_hash,
        "source_file": "capability.py",
        "source_hash": source_hash,
        "symbols": expected_symbols,
    }
    issues: list[dict[str, str]] = []
    if frozen != expected:
        issues.append(_issue("IMPLEMENTATION_MANIFEST", "Implementation Manifest does not match sealed Design, Binding, and source"))
    try:
        expected_parents = sorted(
            {design_hash, binding_hash, implementation_bundle_hash}
            | ({source_hash} if is_content_hash(source_hash) else set())
        )
        if (
            not verify_seal(dict(manifest_seal))
            or manifest_seal.get("artifact_type") != "implementation_manifest"
            or manifest_seal.get("artifact_hash") != manifest_hash
            or manifest_seal.get("parents") != expected_parents
        ):
            issues.append(_issue("IMPLEMENTATION_MANIFEST_SEAL", "Implementation Manifest seal is invalid"))
    except Exception:
        issues.append(_issue("IMPLEMENTATION_MANIFEST_SEAL", "Implementation Manifest seal is invalid"))
    return manifest_hash, issues


def _profile_issues(profile: ValidationAProfile, contracts: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if set(profile.sdk_facade_members) != set(contracts) or set(profile.fixture_probes) != set(contracts):
        return [_issue("A_PROFILE_COVERAGE", "Validation A profile must cover exactly every bound capability")]
    for capability_id in contracts:
        members = profile.sdk_facade_members[capability_id]
        if not isinstance(members, (tuple, list)) or not members or not all(_text(member) and "__" not in member for member in members) or len(set(members)) != len(members):
            issues.append(_issue("A_PROFILE_FACADE", f"{capability_id} has an invalid SDK facade"))
        probe = profile.fixture_probes[capability_id]
        if not isinstance(probe, Mapping) or set(probe) != {"inputs"} or not isinstance(probe.get("inputs"), Mapping):
            issues.append(_issue("A_PROFILE_PROBE", f"{capability_id} has an invalid fixture probe"))
    return issues


def _dunder(value: str) -> bool:
    return "__" in value


def _static_issues(tree: ast.Module, contracts: Mapping[str, Mapping[str, Any]], profile: ValidationAProfile) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    functions: dict[str, list[ast.FunctionDef]] = {}
    expected_symbols = {item["function_name"] for item in contracts.values()}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            issues.append(_issue("EXPERIMENTAL_PROFILE", "only bound function definitions are allowed at module scope"))
            continue
        functions.setdefault(node.name, []).append(node)
    if any(len(nodes) != 1 for nodes in functions.values()) or set(functions) != expected_symbols:
        issues.append(_issue("PUBLIC_SYMBOLS", "capability.py must define exactly the bound public functions"))
    symbol_to_capability = {item["function_name"]: capability_id for capability_id, item in contracts.items()}
    for symbol, capability_id in symbol_to_capability.items():
        nodes = functions.get(symbol, [])
        if len(nodes) != 1:
            continue
        function = nodes[0]
        arguments = function.args
        expected_parameters = [parameter["parameter"] for parameter in contracts[capability_id]["parameters"]]
        if (
            function.decorator_list
            or function.returns is not None
            or any(argument.annotation is not None for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs])
            or arguments.defaults
            or any(default is not None for default in arguments.kw_defaults)
            or arguments.posonlyargs
            or [argument.arg for argument in arguments.args] != expected_parameters
            or [argument.arg for argument in arguments.kwonlyargs] != ["_sdk"]
            or arguments.vararg is not None
            or arguments.kwarg is not None
        ):
            issues.append(_issue("PUBLIC_SIGNATURE", f"{symbol} does not exactly match the Framework Binding"))
        for node in ast.walk(function):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                issues.append(_issue("FORBIDDEN_IMPORT", "imports are forbidden in the experimental profile"))
            if isinstance(node, ast.Name) and _dunder(node.id):
                issues.append(_issue("FORBIDDEN_DUNDER", "dunder names are forbidden in the experimental profile"))
            if isinstance(node, ast.Attribute) and _dunder(node.attr):
                issues.append(_issue("FORBIDDEN_DUNDER", "dunder attributes are forbidden in the experimental profile"))
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.Global, ast.Nonlocal)):
                issues.append(_issue("EXPERIMENTAL_PROFILE", "assignment and namespace mutation are forbidden"))
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Attribute) or not isinstance(node.func.value, ast.Name) or node.func.value.id != "_sdk":
                    issues.append(_issue("FORBIDDEN_CALL", "only calls to the injected _sdk facade are allowed"))
                elif node.func.attr not in profile.sdk_facade_members[capability_id]:
                    issues.append(_issue("SDK_FACADE", f"{node.func.attr} is not in the bound SDK facade"))
        if not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_sdk"
            for node in ast.walk(function)
        ):
            issues.append(_issue("SDK_INJECTION", f"{symbol} must call the injected _sdk facade"))
    return issues


def _descriptor_match(value: Any, field: Mapping[str, Any]) -> bool:
    field_type = field["type"]
    shape = field["shape"]
    vector_length: int | None = None
    if (
        isinstance(shape, str)
        and shape.startswith("vector:")
        and shape.removeprefix("vector:").isdigit()
        and int(shape.removeprefix("vector:")) > 0
    ):
        vector_length = int(shape.removeprefix("vector:"))
    if vector_length is not None and field_type == "number":
        type_ok = isinstance(value, list) and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in value
        )
    elif field_type == "number":
        type_ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
    elif field_type == "integer":
        type_ok = isinstance(value, int) and not isinstance(value, bool)
    elif field_type == "boolean":
        type_ok = isinstance(value, bool)
    elif field_type == "string":
        type_ok = isinstance(value, str)
    elif field_type == "object":
        type_ok = isinstance(value, Mapping)
    elif field_type == "array":
        type_ok = isinstance(value, list)
    else:
        return False
    if shape == "scalar":
        shape_ok = not isinstance(value, (list, tuple, Mapping))
    elif shape == "vector":
        shape_ok = isinstance(value, list)
    elif vector_length is not None:
        shape_ok = isinstance(value, list) and len(value) == vector_length
    elif shape == "mapping":
        shape_ok = isinstance(value, Mapping)
    else:
        return False
    if not type_ok or not shape_ok:
        return False
    try:
        canonical_bytes(value)
    except ContractError:
        return False
    return True


def _fixture_inputs(probe: Mapping[str, Any], fields: list[Mapping[str, Any]]) -> dict[str, Any]:
    values = probe["inputs"]
    expected_names = {field["name"] for field in fields}
    if set(values) != expected_names:
        raise ContractError("fixture probe input envelope is not closed")
    result: dict[str, Any] = {}
    for field in fields:
        envelope = values[field["name"]]
        expected = {"value", "type", "shape", "unit", "frame"}
        if not isinstance(envelope, Mapping) or set(envelope) != expected or any(envelope[key] != field[key] for key in ("type", "shape", "unit", "frame")):
            raise ContractError("fixture probe input metadata does not match the sealed Design")
        if not _descriptor_match(envelope["value"], field):
            raise ContractError("fixture probe input value does not match the sealed Design")
        result[field["name"]] = envelope["value"]
    return result


def _runtime_inputs(inputs: Mapping[str, Any], fields: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(inputs, Mapping) or set(inputs) != {field["name"] for field in fields}:
        raise ContractError("candidate invocation inputs do not match the sealed Design")
    result = dict(inputs)
    if any(not _descriptor_match(result[field["name"]], field) for field in fields):
        raise ContractError("candidate invocation input type, shape, or serialization is invalid")
    return result


def _runtime_result(result: Any, fields: list[Mapping[str, Any]]) -> None:
    if not isinstance(result, Mapping) or set(result) != {field["name"] for field in fields}:
        raise ContractError("candidate result does not match the closed public result envelope")
    if any(not _descriptor_match(result[field["name"]], field) for field in fields):
        raise ContractError("candidate result type, shape, or serialization is invalid")
    canonical_bytes(dict(result))


class _FixtureSdkFacade:
    def __init__(self, members: tuple[str, ...] | list[str]):
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._members = members

    def __getattr__(self, name: str):
        if name not in self._members:
            raise AttributeError(name)

        def invoke(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return invoke


def _isolated_module(source: str) -> ModuleType:
    module = ModuleType("validated_capability")
    module.__dict__.update({"__name__": module.__name__, "__builtins__": {}})
    exec(compile(source, "capability.py", "exec"), module.__dict__, module.__dict__)
    return module


class ValidationARunner:
    def __init__(self, profile: ValidationAProfile):
        self.profile = profile

    def run(
        self,
        capability_design: Mapping[str, Any],
        design_seal: Mapping[str, Any],
        binding_contract: Mapping[str, Any],
        binding_seal: Mapping[str, Any],
        submission: Mapping[str, Any],
        implementation_manifest: Mapping[str, Any],
        implementation_manifest_seal: Mapping[str, Any],
        implementation_bundle_hash: str,
    ) -> ValidationAResult:
        if not is_content_hash(implementation_bundle_hash):
            raise ContractError("Validation A requires an Implementation Bundle hash")
        _design, design_hash, design_capabilities = _verified_design(capability_design, design_seal)
        contract, binding_hash = _verified_binding(binding_contract, binding_seal, design_hash)
        contracts = _capability_contracts(design_capabilities, contract)
        source, diagnostics = _source_from_submission(submission)
        source_hash = content_hash(source.encode("utf-8")) if source is not None else None
        manifest_hash, manifest_issues = _manifest_issues(
            implementation_manifest,
            implementation_manifest_seal,
            design_hash=design_hash,
            binding_hash=binding_hash,
            implementation_bundle_hash=implementation_bundle_hash,
            contracts=contracts,
            source_hash=source_hash,
        )
        diagnostics.extend(manifest_issues)
        diagnostics.extend(_profile_issues(self.profile, contracts))
        module: ModuleType | None = None
        if source is not None and not diagnostics:
            try:
                tree = ast.parse(source, filename="capability.py")
            except SyntaxError as exc:
                diagnostics.append(_issue("SYNTAX", f"capability.py does not parse: {exc.msg}"))
            else:
                diagnostics.extend(_static_issues(tree, contracts, self.profile))
                if not diagnostics:
                    try:
                        module = _isolated_module(source)
                        for capability_id, item in contracts.items():
                            fixture_values = _fixture_inputs(self.profile.fixture_probes[capability_id], item["inputs"])
                            facade = _FixtureSdkFacade(self.profile.sdk_facade_members[capability_id])
                            function = getattr(module, item["function_name"])
                            arguments = {
                                parameter["parameter"]: fixture_values[parameter["public_name"]]
                                for parameter in item["parameters"]
                            }
                            _runtime_result(function(**arguments, _sdk=facade), item["outputs"])
                    except Exception as exc:
                        diagnostics.append(_issue("FIXTURE_PROBE", f"Framework fixture probe failed: {type(exc).__name__}"))
        symbols = [
            {
                "capability_id": capability_id,
                "function_name": item["function_name"],
                "signature": f"({', '.join(parameter['parameter'] for parameter in item['parameters'])}{', ' if item['parameters'] else ''}*, _sdk)",
            }
            for capability_id, item in contracts.items()
        ]
        binding_result = {
            "artifact_type": "validation_a_binding_result",
            "schema_version": _SCHEMA_VERSION,
            "design_hash": design_hash,
            "binding_contract_hash": binding_hash,
            "implementation_manifest_hash": manifest_hash,
            "implementation_bundle_hash": implementation_bundle_hash,
            "source_hash": source_hash,
            "profile_hash": self.profile.profile_hash,
            "symbols": symbols,
        }
        status = "PASS" if not diagnostics else "FAIL"
        report = {
            "artifact_type": "validation_a_report",
            "schema_version": _SCHEMA_VERSION,
            "status": status,
            "binding_result": binding_result,
            "diagnostics": copy.deepcopy(diagnostics),
        }
        report_hash = content_hash(canonical_bytes(report))
        parents = [design_hash, binding_hash, implementation_bundle_hash] + ([manifest_hash] if is_content_hash(manifest_hash) else []) + ([source_hash] if source_hash else [])
        handle = None
        if status == "PASS" and module is not None and source_hash is not None:
            handle = ValidatedCandidateHandle(
                _HANDLE_TOKEN,
                source_hash=source_hash,
                implementation_manifest_hash=manifest_hash,
                implementation_bundle_hash=implementation_bundle_hash,
                module=module,
                contracts=contracts,
                a_report_hash=report_hash,
            )
        return ValidationAResult(
            status=status,
            report=report,
            report_hash=report_hash,
            report_seal=create_seal("validation_a_report", report_hash, parents),
            binding_result=binding_result,
            source_hash=source_hash,
            implementation_manifest_hash=manifest_hash,
            implementation_bundle_hash=implementation_bundle_hash,
            candidate_handle=handle,
            diagnostics=tuple(diagnostics),
        )

    validate = run


def bind_candidate_to_suite(result: ValidationAResult, validation_suite_hash: str) -> ValidatedCandidateHandle:
    """Create and attach the only B-acceptable overlay to an A-created handle."""

    if result.status != "PASS" or result.candidate_handle is None or not is_content_hash(validation_suite_hash):
        raise ContractError("only a Validation A PASS may be bound to a sealed suite")
    symbols = result.binding_result.get("symbols")
    if not isinstance(symbols, list):
        raise ContractError("Validation A binding result is malformed")
    bindings = [
        {"capability_id": item.get("capability_id"), "function_name": item.get("function_name")}
        for item in symbols
    ]
    if not all(_text(item["capability_id"]) and _text(item["function_name"]) for item in bindings):
        raise ContractError("Validation A binding result is malformed")
    overlay = {
        "artifact_type": "validation_execution_binding_overlay",
        "schema_version": _SCHEMA_VERSION,
        "suite_hash": validation_suite_hash,
        "implementation_manifest_hash": result.implementation_manifest_hash,
        "implementation_bundle_hash": result.implementation_bundle_hash,
        "capability_bindings": bindings,
    }
    overlay_hash = content_hash(canonical_bytes(overlay))
    sealed = BindingOverlay(
        overlay=overlay,
        overlay_hash=overlay_hash,
        seal=create_seal(
            "validation_execution_binding_overlay",
            overlay_hash,
            [validation_suite_hash, result.implementation_manifest_hash, result.implementation_bundle_hash],
        ),
    )
    return result.candidate_handle._with_overlay(sealed)


# Kept as a small compatibility name; it returns the opaque B handle, never a module.
create_execution_binding_overlay = bind_candidate_to_suite
