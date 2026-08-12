"""Experimental Validation A and opaque candidate handles.

The profile checks one-file source structure, importability, and the narrow
injected SDK boundary.  Robot-specific physical behavior remains Validation B.
"""

from __future__ import annotations

import ast
import builtins
import copy
import math
import re
import time
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Mapping
from weakref import WeakKeyDictionary

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


@dataclass(frozen=True)
class _CandidatePayload:
    """Authoritative candidate state held outside the user-visible handle."""

    source: str
    source_hash: str
    implementation_manifest_hash: str
    implementation_bundle_hash: str
    contracts: dict[str, dict[str, Any]]
    validation_a_report_hash: str
    overlay: BindingOverlay | None


def _payload_registry() -> tuple[Any, Any]:
    """Keep payload ownership inside a Framework-private closure.

    Reads always return a deep copy.  Even code which deliberately calls the
    private snapshot hook therefore cannot mutate the authoritative payload.
    """

    payloads: WeakKeyDictionary[object, _CandidatePayload] = WeakKeyDictionary()

    def register(handle: object, payload: _CandidatePayload) -> None:
        if handle in payloads:
            raise ContractError("candidate handle is already registered")
        payloads[handle] = copy.deepcopy(payload)

    def snapshot(handle: object) -> _CandidatePayload:
        try:
            payload = payloads[handle]
        except (KeyError, TypeError) as exc:
            raise ContractError("candidate handle is not Framework-registered") from exc
        return copy.deepcopy(payload)

    return register, snapshot


_register_candidate_payload, _candidate_payload_snapshot = _payload_registry()
del _payload_registry


class ValidatedCandidateHandle:
    """A-produced immutable binding of exact source, manifest, and overlay."""

    # The handle intentionally contains no authority-bearing fields.  Its
    # identity selects a Framework-private payload, and the weak-reference slot
    # merely lets that payload disappear with the handle.
    __slots__ = ("__weakref__",)

    def __init__(
        self,
        token: object,
        *,
        source_hash: str,
        source: str,
        implementation_manifest_hash: str,
        implementation_bundle_hash: str,
        contracts: Mapping[str, Mapping[str, Any]],
        a_report_hash: str,
        overlay: BindingOverlay | None = None,
    ):
        if token is not _HANDLE_TOKEN:
            raise ContractError("ValidatedCandidateHandle is Framework-created only")
        if content_hash(source.encode("utf-8")) != source_hash:
            raise ContractError("candidate source no longer matches its Validation A hash")
        _register_candidate_payload(
            self,
            _CandidatePayload(
                source=source,
                source_hash=source_hash,
                implementation_manifest_hash=implementation_manifest_hash,
                implementation_bundle_hash=implementation_bundle_hash,
                contracts=copy.deepcopy(dict(contracts)),
                validation_a_report_hash=a_report_hash,
                overlay=copy.deepcopy(overlay),
            ),
        )

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("ValidatedCandidateHandle is immutable")

    def __copy__(self) -> "ValidatedCandidateHandle":
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> "ValidatedCandidateHandle":
        return self

    def _framework_payload_snapshot(self) -> _CandidatePayload:
        """Return a non-authoritative copy for Framework validation modules."""

        return _candidate_payload_snapshot(self)

    @property
    def source_hash(self) -> str:
        return _candidate_payload_snapshot(self).source_hash

    @property
    def implementation_manifest_hash(self) -> str:
        return _candidate_payload_snapshot(self).implementation_manifest_hash

    @property
    def implementation_bundle_hash(self) -> str:
        return _candidate_payload_snapshot(self).implementation_bundle_hash

    @property
    def validation_a_report_hash(self) -> str:
        return _candidate_payload_snapshot(self).validation_a_report_hash

    @property
    def overlay_hash(self) -> str | None:
        overlay = _candidate_payload_snapshot(self).overlay
        return overlay.overlay_hash if overlay is not None else None

    def _with_overlay(self, overlay: BindingOverlay) -> "ValidatedCandidateHandle":
        payload = _candidate_payload_snapshot(self)
        return ValidatedCandidateHandle(
            _HANDLE_TOKEN,
            source=payload.source,
            source_hash=payload.source_hash,
            implementation_manifest_hash=payload.implementation_manifest_hash,
            implementation_bundle_hash=payload.implementation_bundle_hash,
            contracts=payload.contracts,
            a_report_hash=payload.validation_a_report_hash,
            overlay=overlay,
        )

    def _invoke(
        self,
        capability_id: str,
        inputs: Mapping[str, Any],
        sdk: object,
        *,
        time_module: object | None = None,
    ) -> Any:
        payload = _candidate_payload_snapshot(self)
        contract = payload.contracts.get(capability_id)
        if not isinstance(contract, Mapping):
            raise ContractError("candidate handle does not bind this capability")
        values = _runtime_inputs(inputs, contract["inputs"])
        if content_hash(payload.source.encode("utf-8")) != payload.source_hash:
            raise ContractError("candidate source changed after Validation A")
        module = _isolated_module(payload.source, time_module=time_module)
        function = getattr(module, contract["function_name"])
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


_CANONICAL_DEFAULT_FACTORY_MEMBER = "unitree_go_msg_dds__LowCmd_"


def _profile_issues(profile: ValidationAProfile, contracts: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if set(profile.sdk_facade_members) != set(contracts) or set(profile.fixture_probes) != set(contracts):
        return [_issue("A_PROFILE_COVERAGE", "Validation A profile must cover exactly every bound capability")]
    for capability_id in contracts:
        members = profile.sdk_facade_members[capability_id]
        if not isinstance(members, (tuple, list)) or not members or not all(
            _text(member)
            and ("__" not in member or member == _CANONICAL_DEFAULT_FACTORY_MEMBER)
            for member in members
        ) or len(set(members)) != len(members):
            issues.append(_issue("A_PROFILE_FACADE", f"{capability_id} has an invalid SDK facade"))
        probe = profile.fixture_probes[capability_id]
        if not isinstance(probe, Mapping) or set(probe) != {"inputs"} or not isinstance(probe.get("inputs"), Mapping):
            issues.append(_issue("A_PROFILE_PROBE", f"{capability_id} has an invalid fixture probe"))
    return issues


def _dunder(value: str) -> bool:
    return "__" in value and value != _CANONICAL_DEFAULT_FACTORY_MEMBER


_SAFE_BUILTIN_NAMES = frozenset({
    "range", "len", "min", "max", "abs", "sum", "enumerate", "zip",
    "float", "int", "bool", "str", "list", "tuple", "dict", "set",
    "round", "all", "any", "isinstance", "RuntimeError", "ValueError",
    "TypeError", "IndexError",
})
_SAFE_BUILTINS = {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES}
_ALLOWED_MODULE_IMPORTS = frozenset({"math", "time", "numpy"})
_ALLOWED_TIME_MEMBERS = frozenset({"monotonic", "perf_counter", "process_time", "sleep", "time"})
_ALLOWED_NUMPY_MEMBERS = frozenset({
    "abs", "absolute", "arange", "array", "asarray", "clip", "concatenate", "cos",
    "dot", "empty", "exp", "eye", "float32", "float64", "hstack", "isfinite", "isnan",
    "linalg", "linspace", "maximum", "mean", "minimum", "nan_to_num", "ndarray", "ones",
    "ones_like", "pi", "reshape", "sin", "sqrt", "stack", "sum", "tan", "transpose",
    "vstack", "where", "zeros", "zeros_like",
})
_ALLOWED_NUMPY_PATHS = frozenset({
    ("linalg", "norm"),
    ("linalg", "solve"),
    ("linalg", "lstsq"),
})
_RELAXED_NUMPY_BLOCKED_ROOTS = frozenset({
    "load", "save", "savez", "savez_compressed", "memmap", "loadtxt", "savetxt",
    "genfromtxt", "recfromtxt", "fromfile", "tofile", "ctypeslib", "lib",
})
_ALLOWED_DERIVED_OBJECT_MEMBERS = frozenset({"Init", "Read", "Write", "Crc", "get"})
_ALLOWED_DERIVED_OBJECT_FIELDS = frozenset({
    "mode", "q", "dq", "kp", "kd", "tau", "motor_cmd", "crc", "motor_state", "position",
})
_ALLOWED_DERIVED_SEQUENCE_FIELDS = frozenset({"motor_state", "position"})
_ALLOWED_LOCAL_ARRAY_ATTRIBUTES = frozenset({"T"})
_ALLOWED_LOCAL_CONVERSION_METHODS = frozenset({"tolist"})

# Validation A is deliberately a generic artifact/safety gate.  It does not
# attempt to infer the type or provenance of ordinary local values.  These
# names are kept closed because they are direct escape hatches rather than
# ordinary Python data processing.
_FORBIDDEN_CANDIDATE_NAME_TOKENS = frozenset({
    "translation", "mujoco", "harness", "criterion", "subprocess", "socket",
    "requests", "urllib", "http", "ftplib", "ctypes", "importlib", "pickle",
    "marshal", "shell", "dynamic", "reflection",
})
_FORBIDDEN_CANDIDATE_NAMES = frozenset({
    "os", "sys", "typing", "pathlib", "shutil", "glob", "signal", "multiprocessing",
    "asyncio", "builtins", "secrets", "random",
})
_FORBIDDEN_CALL_NAMES = frozenset({
    "__import__", "eval", "exec", "open", "compile", "globals", "locals", "vars",
    "dir", "getattr", "setattr", "delattr", "hasattr", "help", "input", "breakpoint",
    "system", "popen", "Popen", "check_output", "check_call", "import_module",
    "load_module", "find_spec", "exec_module",
})


@dataclass(frozen=True)
class _HelperReturnProvenance:
    """Conservative provenance for one reachable helper's return value."""

    scalar: tuple[bool, bool, bool] | None = None
    scalar_member_checked: bool = False
    elements: tuple[tuple[bool, bool, bool], ...] | None = None
    element_member_checked: tuple[bool, ...] | None = None


def _merge_return_classifications(
    classifications: list[tuple[bool, bool, bool]],
) -> tuple[bool, bool, bool]:
    if not classifications:
        return True, False, False
    return tuple(all(item[index] for item in classifications) for index in range(3))  # type: ignore[return-value]


def _merge_helper_return_provenance(
    returns: list[_HelperReturnProvenance],
) -> _HelperReturnProvenance:
    if not returns:
        return _HelperReturnProvenance(scalar=(True, False, False))
    if all(item.elements is None for item in returns):
        return _HelperReturnProvenance(
            scalar=_merge_return_classifications([
                item.scalar or (False, False, False)
                for item in returns
            ]),
            scalar_member_checked=all(item.scalar_member_checked for item in returns),
        )
    if all(item.elements is not None for item in returns):
        element_lists = [item.elements or () for item in returns]
        if len({len(elements) for elements in element_lists}) == 1:
            member_checks = [
                item.element_member_checked or (False,) * len(element_lists[0])
                for item in returns
            ]
            return _HelperReturnProvenance(
                elements=tuple(
                    _merge_return_classifications([elements[index] for elements in element_lists])
                    for index in range(len(element_lists[0]))
                ),
                element_member_checked=tuple(
                    all(checks[index] for checks in member_checks)
                    for index in range(len(element_lists[0]))
                ),
            )
    return _HelperReturnProvenance(scalar=(True, False, False))


def _module_path(value: ast.AST, aliases: Mapping[str, str]) -> tuple[str, tuple[str | None, ...]] | None:
    segments: list[str | None] = []
    current = value
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        if isinstance(current, ast.Attribute):
            segments.append(current.attr)
        else:
            segments.append(None)
        current = current.value
    if not isinstance(current, ast.Name) or current.id not in aliases:
        return None
    segments.reverse()
    return aliases[current.id], tuple(segments)


def _module_member_allowed(module: str, path: tuple[str | None, ...]) -> bool:
    if not path or any(segment is None for segment in path):
        return False
    if module == "math":
        return len(path) == 1 and path[0] in {name for name in dir(math) if not name.startswith("_")}
    if module == "time":
        return len(path) == 1 and path[0] in _ALLOWED_TIME_MEMBERS
    if module == "numpy":
        return (
            (len(path) == 1 and path[0] in _ALLOWED_NUMPY_MEMBERS)
            or path in _ALLOWED_NUMPY_PATHS
        )
    return False


def _relaxed_module_member_allowed(module: str, path: tuple[str | None, ...]) -> bool:
    """Allow ordinary public numerical module members in the relaxed gate."""

    if module not in _ALLOWED_MODULE_IMPORTS or not path or any(
        segment is None or _dunder(segment) for segment in path
    ):
        return False
    first = path[0]
    if module == "numpy":
        return first not in _RELAXED_NUMPY_BLOCKED_ROOTS
    public_members = {
        name for name in dir(math if module == "math" else time)
        if not name.startswith("_")
    }
    return first in public_members


def _expression_root(value: ast.AST) -> str | None:
    current = value
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _sdk_path_root(value: ast.AST, roots: set[str] | frozenset[str] = frozenset({"_sdk"})) -> tuple[bool, str | None]:
    """Return whether an expression is rooted at an injected SDK name."""

    path = _sdk_member_path(value, roots)
    if path is None:
        return False, None
    if not path or path[0] is None:
        return True, None
    return True, path[0]


def _sdk_member_path(value: ast.AST, roots: set[str] | frozenset[str]) -> tuple[str | None, ...] | None:
    """Return the attribute/subscript path after an injected SDK name."""

    segments: list[tuple[str, str | None]] = []
    current = value
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        if isinstance(current, ast.Attribute):
            segments.append(("attribute", current.attr))
        else:
            segments.append(("subscript", None))
        current = current.value
    if not isinstance(current, ast.Name) or current.id not in roots:
        return None
    segments.reverse()
    return tuple(segment for _kind, segment in segments)


class _SdkStaticAnalyzer(ast.NodeVisitor):
    """Ordered analysis for approved modules, helpers, and SDK locals."""

    def __init__(
        self,
        function: ast.FunctionDef,
        public_inputs: list[str],
        members: tuple[str, ...] | list[str],
        module_aliases: Mapping[str, str],
        module_constants: set[str],
        local_functions: set[str],
        sdk_names: set[str] | frozenset[str],
        sdk_forwarded_helpers: set[str],
        sdk_derived_parameters: set[str] | frozenset[str] = frozenset(),
        helper_return_provenance: Mapping[str, _HelperReturnProvenance] | None = None,
        expected_result_names: set[str] | None = None,
    ):
        self.function = function
        self.public_inputs = set(public_inputs)
        self.members = set(members)
        self.module_aliases = dict(module_aliases)
        self.module_constants = set(module_constants)
        self.local_functions = set(local_functions)
        self.sdk_names = set(sdk_names)
        self.sdk_forwarded_targets = set(sdk_forwarded_helpers)
        self.expected_result_names = expected_result_names
        self.helper_return_provenance = dict(helper_return_provenance or {})
        self.sdk_readable_locals: set[str] = set()
        self.mutable_sdk_locals: set[str] = set(sdk_derived_parameters)
        self.derived_sdk_locals: set[str] = set(sdk_derived_parameters)
        self.read_only_sdk_locals: set[str] = set()
        self.derived_sequence_locals: set[str] = set()
        self.derived_element_locals: set[str] = set()
        self.safe_locals: set[str] = set()
        self.issues: list[dict[str, str]] = []
        self._issue_keys: set[tuple[str, str]] = set()
        self.approved_sdk_use = False
        self.forwarded_sdk_helpers: set[str] = set()
        self.return_provenance: list[_HelperReturnProvenance] = []

    def _add_issue(self, code: str, message: str) -> None:
        key = (code, message)
        if key not in self._issue_keys:
            self._issue_keys.add(key)
            self.issues.append(_issue(code, message))

    def analyze(self) -> list[dict[str, str]]:
        for statement in self.function.body:
            self.visit(statement)
        return self.issues

    def _sdk_expression_allowed(self, value: ast.AST) -> bool:
        path = _sdk_member_path(value, self.sdk_names)
        if path is None or not path or any(segment is None for segment in path):
            return False
        if path[0] not in self.members:
            return False
        return len(path) == 1 or (len(path) == 2 and path[1] in _ALLOWED_DERIVED_OBJECT_MEMBERS)

    def _derived_expression_allowed(self, value: ast.AST) -> bool:
        if isinstance(value, ast.Attribute) and self._derived_element_expression(value.value):
            return value.attr in _ALLOWED_DERIVED_OBJECT_FIELDS
        if self._derived_element_expression(value):
            return True
        sequence_path = _sdk_member_path(value, self.derived_sequence_locals)
        if sequence_path is not None:
            return isinstance(value, ast.Subscript) and self._sequence_selector_allowed(value.slice)
        path = _sdk_member_path(value, self.derived_sdk_locals)
        if path is None:
            return True
        root = _expression_root(value)
        if root in self.read_only_sdk_locals:
            return all(
                segment is None or segment in _ALLOWED_DERIVED_OBJECT_FIELDS
                for segment in path
            )
        return all(
            segment is None
            or segment in _ALLOWED_DERIVED_OBJECT_FIELDS
            or segment in _ALLOWED_DERIVED_OBJECT_MEMBERS
            for segment in path
        )

    def _sequence_selector_allowed(self, value: ast.AST) -> bool:
        if isinstance(value, ast.Slice):
            return all(
                part is None or _safe_literal_ast(part)
                for part in (value.lower, value.upper, value.step)
            )
        return _safe_literal_ast(value)

    def _derived_sequence_expression(self, value: ast.AST) -> bool:
        if isinstance(value, ast.Name):
            return value.id in self.derived_sequence_locals
        if isinstance(value, ast.Attribute):
            path = _sdk_member_path(value, self.derived_sdk_locals)
            return bool(
                path
                and path[-1] in _ALLOWED_DERIVED_SEQUENCE_FIELDS
                and self._derived_expression_allowed(value)
            )
        if isinstance(value, ast.Subscript):
            return (
                self._derived_sequence_expression(value.value)
                and isinstance(value.slice, ast.Slice)
                and self._sequence_selector_allowed(value.slice)
            )
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in {"list", "tuple"}:
            return len(value.args) == 1 and self._derived_sequence_expression(value.args[0])
        return False

    def _derived_element_expression(self, value: ast.AST) -> bool:
        if isinstance(value, ast.Name):
            return value.id in self.derived_element_locals
        if isinstance(value, ast.Subscript):
            return (
                self._derived_sequence_expression(value.value)
                and not isinstance(value.slice, ast.Slice)
                and self._sequence_selector_allowed(value.slice)
            )
        return False

    def _read_only_derived_expression(self, value: ast.AST) -> bool:
        return (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "Read"
            and self._call_origin(value.func) == "sdk-derived-method"
        )

    def _call_origin(self, function: ast.AST) -> str | None:
        sdk_root, member = _sdk_path_root(function, self.sdk_names)
        if sdk_root:
            if self._sdk_expression_allowed(function):
                return "sdk-root"
            return None
        module_path = _module_path(function, self.module_aliases)
        if module_path is not None:
            module, path = module_path
            if _module_member_allowed(module, path):
                return "module"
            return None
        root = _expression_root(function)
        if root in self.derived_sdk_locals and isinstance(function, ast.Attribute) and function.attr in _ALLOWED_DERIVED_OBJECT_MEMBERS:
            return "sdk-derived-method"
        if root in self.sdk_readable_locals | self.mutable_sdk_locals and isinstance(function, ast.Attribute) and function.attr == "get":
            return "sdk-local"
        if isinstance(function, ast.Attribute) and function.attr in {"append", "extend", "get"} and root in self.safe_locals | self.public_inputs:
            return "local-container"
        if isinstance(function, ast.Attribute) and function.attr in _ALLOWED_LOCAL_CONVERSION_METHODS and root in self.safe_locals | self.public_inputs:
            return "local-conversion"
        if root in self.local_functions or root in _SAFE_BUILTIN_NAMES:
            return "local-function"
        return None

    def _helper_return(self, value: ast.AST) -> _HelperReturnProvenance | None:
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            return self.helper_return_provenance.get(value.func.id)
        return None

    def _member_checked(self, value: ast.AST) -> bool:
        if isinstance(value, ast.Call):
            origin = self._call_origin(value.func)
            if origin == "sdk-root":
                return True
            helper_return = self._helper_return(value)
            if helper_return is not None:
                return helper_return.scalar_member_checked
        return _expression_root(value) in self.derived_sdk_locals or (
            isinstance(value, ast.Name) and value.id in self.derived_sdk_locals
        )

    def _classify_expression(self, value: ast.AST) -> tuple[bool, bool, bool]:
        """Return ``(allowed, sdk_derived, mutable)`` without widening the language."""

        if isinstance(value, ast.Constant):
            return True, False, False
        if isinstance(value, ast.Name):
            if value.id in self.derived_element_locals:
                return True, True, False
            if value.id in self.derived_sequence_locals:
                return True, True, False
            if value.id in self.read_only_sdk_locals:
                return True, True, False
            if value.id in self.sdk_names:
                return True, True, True
            if value.id in self.mutable_sdk_locals:
                return True, True, True
            if value.id in self.sdk_readable_locals:
                return True, True, False
            if (
                value.id in self.public_inputs
                or value.id in self.safe_locals
                or value.id in self.module_aliases
                or value.id in self.module_constants
                or value.id in _SAFE_BUILTIN_NAMES
            ):
                return True, False, False
            return False, False, False
        if isinstance(value, (ast.Attribute, ast.Subscript)):
            module_path = _module_path(value, self.module_aliases)
            if module_path is not None:
                module, path = module_path
                return _module_member_allowed(module, path), False, False
            sdk_root, member = _sdk_path_root(value, self.sdk_names)
            if sdk_root:
                allowed = self._sdk_expression_allowed(value)
                return allowed, allowed, allowed
            if isinstance(value, ast.Attribute) and self._derived_element_expression(value.value):
                return value.attr in _ALLOWED_DERIVED_OBJECT_FIELDS, True, False
            if self._derived_element_expression(value):
                return True, True, False
            if self._derived_sequence_expression(value):
                return True, True, False
            root = _expression_root(value)
            if root in self.read_only_sdk_locals or root in self.derived_sdk_locals:
                return (
                    self._derived_expression_allowed(value),
                    True,
                    root in self.mutable_sdk_locals and root not in self.read_only_sdk_locals,
                )
            if root in self.mutable_sdk_locals:
                return True, True, True
            if root in self.sdk_readable_locals:
                return True, True, False
            if root in self.module_constants:
                if isinstance(value, ast.Subscript):
                    allowed, sdk_derived, _mutable = self._classify_expression(value.slice)
                    return allowed and not sdk_derived, False, False
                return False, False, False
            if root in self.public_inputs or root in self.safe_locals:
                if isinstance(value, ast.Subscript):
                    return True, False, False
                if isinstance(value, ast.Attribute) and value.attr in _ALLOWED_LOCAL_ARRAY_ATTRIBUTES:
                    base_allowed, base_sdk_derived, _base_mutable = self._classify_expression(value.value)
                    if base_allowed and not base_sdk_derived:
                        return True, False, False
                return False, False, False
            if isinstance(value, ast.Attribute) and value.attr in _ALLOWED_LOCAL_ARRAY_ATTRIBUTES:
                base_allowed, base_sdk_derived, _base_mutable = self._classify_expression(value.value)
                if base_allowed and not base_sdk_derived:
                    return True, False, False
            return False, False, False
        if isinstance(value, ast.Call):
            origin = self._call_origin(value.func)
            if origin is None:
                return False, False, False
            if origin == "local-function":
                if (
                    isinstance(value.func, ast.Name)
                    and value.func.id in {"list", "tuple"}
                    and len(value.args) == 1
                    and self._derived_sequence_expression(value.args[0])
                ):
                    return True, True, False
                helper_return = self._helper_return(value)
                if helper_return is not None:
                    if helper_return.elements is not None:
                        return all(item[0] for item in helper_return.elements), False, False
                    return helper_return.scalar or (False, False, False)
            sdk_derived = origin in {"sdk-root", "sdk-local", "sdk-derived-method"}
            return True, sdk_derived, sdk_derived
        if isinstance(value, ast.Starred):
            return self._classify_expression(value.value)
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            results = [self._classify_expression(item) for item in value.elts]
            return all(item[0] for item in results), False, False
        if isinstance(value, ast.Dict):
            results = [self._classify_expression(item) for item in [*value.keys, *value.values] if item is not None]
            return all(item[0] for item in results), False, False
        if isinstance(value, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            saved_locals = set(self.safe_locals)
            saved_elements = set(self.derived_element_locals)
            expressions: list[ast.AST] = []
            for generator in value.generators:
                expressions.append(generator.iter)
                expressions.extend(generator.ifs)
                if self._derived_sequence_expression(generator.iter):
                    self._bind_derived_element_target(generator.target)
                else:
                    self._bind_safe_target(generator.target)
            if isinstance(value, ast.DictComp):
                expressions.extend((value.key, value.value))
            else:
                expressions.append(value.elt)
            allowed = all(self._classify_expression(expression)[0] for expression in expressions)
            self.safe_locals = saved_locals
            self.derived_element_locals = saved_elements
            return allowed, False, False
        if isinstance(value, ast.UnaryOp):
            return self._classify_expression(value.operand)[0], False, False
        if isinstance(value, ast.BinOp):
            left = self._classify_expression(value.left)
            right = self._classify_expression(value.right)
            return left[0] and right[0], False, False
        if isinstance(value, ast.BoolOp):
            results = [self._classify_expression(item) for item in value.values]
            return all(item[0] for item in results), False, False
        if isinstance(value, ast.Compare):
            results = [self._classify_expression(value.left)] + [
                self._classify_expression(item) for item in value.comparators
            ]
            return all(item[0] for item in results), False, False
        if isinstance(value, ast.IfExp):
            results = [
                self._classify_expression(value.test),
                self._classify_expression(value.body),
                self._classify_expression(value.orelse),
            ]
            return all(item[0] for item in results), False, False
        return False, False, False

    def _assignment_issue(self, message: str = "only approved local assignments and SDK-derived mutations are allowed") -> None:
        self._add_issue("EXPERIMENTAL_PROFILE", message)

    def _bind_local_name(
        self,
        name: str,
        value: ast.AST,
        classification: tuple[bool, bool, bool] | None = None,
        member_checked: bool | None = None,
    ) -> bool:
        if _dunder(name) or name == "_sdk":
            self._assignment_issue("assignment to a reserved or dunder name is forbidden")
            return False
        self.read_only_sdk_locals.discard(name)
        self.derived_sequence_locals.discard(name)
        self.derived_element_locals.discard(name)
        allowed, sdk_derived, mutable = classification or self._classify_expression(value)
        if not allowed:
            self._assignment_issue()
            return False
        if mutable:
            self.mutable_sdk_locals.add(name)
            self.sdk_readable_locals.discard(name)
            derived_value = (
                (isinstance(value, ast.Call) and self._call_origin(value.func) == "sdk-root")
                or (not isinstance(value, ast.Call) and self._sdk_expression_allowed(value))
            )
            if member_checked is None and isinstance(value, ast.Call):
                helper_return = self._helper_return(value)
                member_checked = (
                    helper_return.scalar_member_checked
                    if helper_return is not None
                    else self._member_checked(value)
                )
            if member_checked is None:
                member_checked = derived_value or (isinstance(value, ast.Name) and value.id in self.derived_sdk_locals)
            if sdk_derived and member_checked:
                self.derived_sdk_locals.add(name)
            else:
                self.derived_sdk_locals.discard(name)
            self.safe_locals.discard(name)
        elif sdk_derived:
            self.sdk_readable_locals.add(name)
            self.mutable_sdk_locals.discard(name)
            self.derived_sdk_locals.discard(name)
            self.safe_locals.discard(name)
        else:
            self.safe_locals.add(name)
            self.sdk_readable_locals.discard(name)
            self.mutable_sdk_locals.discard(name)
            self.derived_sdk_locals.discard(name)
        if self._read_only_derived_expression(value):
            self.read_only_sdk_locals.add(name)
            self.derived_sdk_locals.add(name)
        if self._derived_sequence_expression(value):
            self.derived_sequence_locals.add(name)
            self.safe_locals.discard(name)
            self.sdk_readable_locals.add(name)
        if self._derived_element_expression(value):
            self.derived_element_locals.add(name)
            self.safe_locals.discard(name)
        if isinstance(value, ast.Name) and value.id in self.sdk_names:
            self.sdk_names.add(name)
        return True

    def _bind_destructured_assignment(self, target: ast.AST, value: ast.AST) -> bool:
        if not isinstance(target, (ast.Tuple, ast.List)):
            return False
        names = [element.id if isinstance(element, ast.Name) else None for element in target.elts]
        real_names = [name for name in names if name is not None and name != "_"]
        if any(name is None for name in names) or len(set(real_names)) != len(real_names):
            self._assignment_issue("destructuring targets must be unique non-dunder local names")
            return True
        if isinstance(value, (ast.Tuple, ast.List)):
            if len(target.elts) != len(value.elts):
                self._assignment_issue("destructuring requires a same-shape approved tuple or list")
                return True
            values = list(value.elts)
            classifications = [self._classify_expression(element) for element in values]
        else:
            helper_return = self._helper_return(value)
            if helper_return is not None and helper_return.elements is not None:
                if len(target.elts) != len(helper_return.elements):
                    self._assignment_issue("destructuring requires a same-shape approved tuple or list")
                    return True
                values = [value] * len(target.elts)
                classifications = list(helper_return.elements)
                member_checks = list(helper_return.element_member_checked or (False,) * len(target.elts))
            else:
                classification = self._classify_expression(value)
                if not classification[0]:
                    self._assignment_issue("destructuring RHS must be fully approved by the expression policy")
                    return True
                values = [value] * len(target.elts)
                classifications = [classification] * len(target.elts)
                member_checks = [None] * len(target.elts)
        if isinstance(value, (ast.Tuple, ast.List)):
            member_checks = [None] * len(target.elts)
        if not all(classification[0] for classification in classifications):
            self._assignment_issue("destructuring RHS must be fully approved by the expression policy")
            return True
        for name, element, classification, member_checked in zip(names, values, classifications, member_checks, strict=True):
            assert name is not None
            self._bind_local_name(name, element, classification, member_checked)
        return True

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        if len(node.targets) != 1:
            self._assignment_issue()
            return
        target = node.targets[0]
        if isinstance(target, ast.Name):
            self._bind_local_name(target.id, node.value)
            return
        if self._bind_destructured_assignment(target, node.value):
            return
        self.visit(target)
        root = _expression_root(target)
        if root in self.read_only_sdk_locals or root not in self.mutable_sdk_locals and root not in self.safe_locals | self.public_inputs:
            self._assignment_issue("only a local value derived from the injected SDK may be mutated")

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            if _dunder(node.target.id) or node.target.id == "_sdk":
                self._assignment_issue("assignment to a reserved or dunder name is forbidden")
            elif node.target.id not in self.safe_locals and node.target.id not in self.mutable_sdk_locals:
                self._assignment_issue("augmented assignment requires an approved local")
            return
        self.visit(node.target)
        root = _expression_root(node.target)
        if root in self.read_only_sdk_locals or root not in self.mutable_sdk_locals and root not in self.safe_locals | self.public_inputs:
            self._assignment_issue("only a local value derived from the injected SDK may be mutated")

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._assignment_issue("annotated assignment is forbidden")
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._assignment_issue("named expressions are forbidden")
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        self._assignment_issue("deletion is forbidden")
        self.generic_visit(node)

    def _bind_safe_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            if _dunder(target.id) or target.id == "_sdk":
                self._assignment_issue("assignment to a reserved or dunder name is forbidden")
            else:
                self.derived_element_locals.discard(target.id)
                self.safe_locals.add(target.id)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind_safe_target(element)
            return
        self._assignment_issue("loop targets must be local names")

    def _bind_derived_element_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            if _dunder(target.id) or target.id == "_sdk":
                self._assignment_issue("assignment to a reserved or dunder name is forbidden")
            else:
                self.derived_element_locals.add(target.id)
                self.safe_locals.discard(target.id)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind_derived_element_target(element)
            return
        self._assignment_issue("loop targets must be local names")

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        if not self._classify_expression(node.iter)[0]:
            self._assignment_issue("for-loop iterables must be approved safe ranges or literal local values")
        saved_elements = set(self.derived_element_locals)
        if self._derived_sequence_expression(node.iter):
            self._bind_derived_element_target(node.target)
        else:
            self._bind_safe_target(node.target)
        for statement in [*node.body, *node.orelse]:
            self.visit(statement)
        self.derived_element_locals = saved_elements

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        for statement in [*node.body, *node.orelse]:
            self.visit(statement)

    def visit_Call(self, node: ast.Call) -> None:
        sdk_root, member = _sdk_path_root(node.func, self.sdk_names)
        origin = self._call_origin(node.func)
        sdk_path = _sdk_member_path(node.func, self.sdk_names)
        if origin in {"sdk-root", "sdk-local", "sdk-derived-method"}:
            self.approved_sdk_use = True
        elif origin == "local-function" and isinstance(node.func, ast.Name) and node.func.id in self.sdk_forwarded_targets:
            self.forwarded_sdk_helpers.add(node.func.id)
        elif origin is None and sdk_root:
            self._add_issue("SDK_FACADE", f"{member} is not in the bound SDK facade")
        elif origin is None:
            self._add_issue("FORBIDDEN_CALL", "only calls rooted in the injected _sdk facade are allowed")
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        value = node.value
        if value is None:
            self.return_provenance.append(_HelperReturnProvenance(scalar=(True, False, False)))
        elif isinstance(value, (ast.Tuple, ast.List)):
            self.return_provenance.append(_HelperReturnProvenance(
                elements=tuple(self._classify_expression(element) for element in value.elts),
                element_member_checked=tuple(self._member_checked(element) for element in value.elts),
            ))
        else:
            helper_return = self._helper_return(value)
            self.return_provenance.append(
                helper_return
                if helper_return is not None
                else _HelperReturnProvenance(
                    scalar=self._classify_expression(value),
                    scalar_member_checked=self._member_checked(value),
                )
            )
        if self.expected_result_names is not None and isinstance(node.value, ast.Dict):
            names: list[str] = []
            for key in node.value.keys:
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    names.append("")
                else:
                    names.append(key.value)
            if len(names) != len(set(names)) or set(names) != self.expected_result_names:
                self._add_issue("RESULT_FIELDS", "literal result mapping does not match the sealed output fields")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        module_path = _module_path(node, self.module_aliases)
        if module_path is not None and not _module_member_allowed(*module_path):
            self._add_issue("MODULE_MEMBER", "module member is not in the approved runtime allowlist")
        sdk_root, member = _sdk_path_root(node, self.sdk_names)
        if sdk_root:
            if self._sdk_expression_allowed(node):
                self.approved_sdk_use = True
            else:
                self._add_issue("SDK_FACADE", f"{member} is not in the bound SDK facade")
        if _dunder(node.attr):
            self._add_issue("FORBIDDEN_DUNDER", "dunder attributes are forbidden in the experimental profile")
        if not self._derived_expression_allowed(node):
            self._add_issue("SDK_DERIVED_MEMBER", "SDK-derived object member is not in the approved object surface")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        module_path = _module_path(node, self.module_aliases)
        if module_path is not None and not _module_member_allowed(*module_path):
            self._add_issue("MODULE_MEMBER", "module member is not in the approved runtime allowlist")
        sdk_root, member = _sdk_path_root(node, self.sdk_names)
        if sdk_root:
            if self._sdk_expression_allowed(node):
                self.approved_sdk_use = True
            else:
                self._add_issue("SDK_FACADE", f"{member} is not in the bound SDK facade")
        if not self._derived_expression_allowed(node):
            self._add_issue("SDK_DERIVED_MEMBER", "SDK-derived object member is not in the approved object surface")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if _dunder(node.id):
            self._add_issue("FORBIDDEN_DUNDER", "dunder names are forbidden in the experimental profile")

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg is not None and _dunder(node.arg):
            self._add_issue("FORBIDDEN_DUNDER", "dunder names are forbidden in the experimental profile")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self._add_issue("FORBIDDEN_IMPORT", "imports are allowed only at module scope")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._add_issue("FORBIDDEN_IMPORT", "imports are forbidden in the experimental profile")

    def visit_Global(self, node: ast.Global) -> None:
        self._add_issue("EXPERIMENTAL_PROFILE", "global and namespace mutation are forbidden")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self._add_issue("EXPERIMENTAL_PROFILE", "global and namespace mutation are forbidden")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add_issue("EXPERIMENTAL_PROFILE", "nested functions are forbidden")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._add_issue("EXPERIMENTAL_PROFILE", "nested functions are forbidden")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._add_issue("EXPERIMENTAL_PROFILE", "nested classes are forbidden")

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._add_issue("EXPERIMENTAL_PROFILE", "nested functions are forbidden")


def _forbidden_candidate_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in _FORBIDDEN_CANDIDATE_NAMES or any(
        token in lowered for token in _FORBIDDEN_CANDIDATE_NAME_TOKENS
    )


class _RelaxedStaticAnalyzer(ast.NodeVisitor):
    """Generic Validation A safety checks without value/provenance inference."""

    def __init__(
        self,
        function: ast.FunctionDef,
        members: tuple[str, ...] | list[str] | set[str] | frozenset[str],
        module_aliases: Mapping[str, str],
        module_constants: set[str],
        local_functions: set[str],
        sdk_names: set[str] | frozenset[str],
    ):
        self.function = function
        self.members = set(members)
        self.module_aliases = dict(module_aliases)
        self.module_constants = set(module_constants)
        self.local_functions = set(local_functions)
        self.sdk_names = set(sdk_names)
        self.issues: list[dict[str, str]] = []
        self._issue_keys: set[tuple[str, str]] = set()
        self.approved_sdk_use = False
        self.called_helpers: set[str] = set()

    def _add_issue(self, code: str, message: str) -> None:
        key = (code, message)
        if key not in self._issue_keys:
            self._issue_keys.add(key)
            self.issues.append(_issue(code, message))

    def _visit_signature(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in function.decorator_list:
            self.visit(decorator)
        if function.returns is not None:
            self.visit(function.returns)
        arguments = function.args
        for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
            if _dunder(argument.arg):
                self._add_issue("FORBIDDEN_DUNDER", "dunder names are forbidden in the experimental profile")
            elif _forbidden_candidate_name(argument.arg):
                self._add_issue("FORBIDDEN_NAME", "direct privileged, reflection, shell, network, or dynamic-loading names are forbidden")
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if arguments.vararg is not None:
            if _dunder(arguments.vararg.arg):
                self._add_issue("FORBIDDEN_DUNDER", "dunder names are forbidden in the experimental profile")
            elif _forbidden_candidate_name(arguments.vararg.arg):
                self._add_issue("FORBIDDEN_NAME", "direct privileged, reflection, shell, network, or dynamic-loading names are forbidden")
            if arguments.vararg.annotation is not None:
                self.visit(arguments.vararg.annotation)
        if arguments.kwarg is not None:
            if _dunder(arguments.kwarg.arg):
                self._add_issue("FORBIDDEN_DUNDER", "dunder names are forbidden in the experimental profile")
            elif _forbidden_candidate_name(arguments.kwarg.arg):
                self._add_issue("FORBIDDEN_NAME", "direct privileged, reflection, shell, network, or dynamic-loading names are forbidden")
            if arguments.kwarg.annotation is not None:
                self.visit(arguments.kwarg.annotation)
        for default in [*arguments.defaults, *(item for item in arguments.kw_defaults if item is not None)]:
            self.visit(default)

    def analyze(self) -> list[dict[str, str]]:
        self._visit_signature(self.function)
        for statement in self.function.body:
            self.visit(statement)
        return self.issues

    def _check_module_member(self, value: ast.AST) -> None:
        module_path = _module_path(value, self.module_aliases)
        if module_path is not None and not _relaxed_module_member_allowed(*module_path):
            self._add_issue("MODULE_MEMBER", "module member is not in the approved runtime allowlist")

    def _check_sdk_member(self, value: ast.AST) -> None:
        path = _sdk_member_path(value, self.sdk_names)
        if path is None:
            return
        if not path or path[0] not in self.members:
            member = path[0] if path and path[0] is not None else None
            self._add_issue("SDK_FACADE", f"{member} is not in the bound SDK facade")
            return
        self.approved_sdk_use = True

    def _is_sdk_target(self, target: ast.AST) -> bool:
        if _sdk_member_path(target, self.sdk_names) is not None:
            return True
        if isinstance(target, (ast.Tuple, ast.List)):
            return any(self._is_sdk_target(item) for item in target.elts)
        return False

    def _check_name(self, name: str) -> None:
        if _dunder(name):
            self._add_issue("FORBIDDEN_DUNDER", "dunder names are forbidden in the experimental profile")
        elif _forbidden_candidate_name(name):
            self._add_issue("FORBIDDEN_NAME", "direct privileged, reflection, shell, network, or dynamic-loading names are forbidden")

    def visit_Name(self, node: ast.Name) -> None:
        self._check_name(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._check_name(node.attr)
        self._check_module_member(node)
        self._check_sdk_member(node)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self._check_module_member(node)
        self._check_sdk_member(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        called_name: str | None = None
        if isinstance(node.func, ast.Name):
            called_name = node.func.id
            if called_name in self.local_functions:
                self.called_helpers.add(called_name)
        elif isinstance(node.func, ast.Attribute):
            called_name = node.func.attr
        if called_name in _FORBIDDEN_CALL_NAMES:
            self._add_issue("FORBIDDEN_CALL", "reflection, shell, network, or dynamic-loading calls are forbidden")
        elif called_name is not None and _forbidden_candidate_name(called_name):
            self._add_issue("FORBIDDEN_NAME", "direct privileged, reflection, shell, network, or dynamic-loading names are forbidden")
        self._check_sdk_member(node.func)
        self.generic_visit(node)

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg is not None:
            self._check_name(node.arg)
        self.generic_visit(node)

    def visit_Import(self, _node: ast.Import) -> None:
        self._add_issue("FORBIDDEN_IMPORT", "imports are allowed only at module scope")

    def visit_ImportFrom(self, _node: ast.ImportFrom) -> None:
        self._add_issue("FORBIDDEN_IMPORT", "imports are forbidden in the experimental profile")

    def visit_Global(self, _node: ast.Global) -> None:
        self._add_issue("EXPERIMENTAL_PROFILE", "global and namespace mutation are forbidden")

    def visit_Nonlocal(self, _node: ast.Nonlocal) -> None:
        self._add_issue("EXPERIMENTAL_PROFILE", "global and namespace mutation are forbidden")

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)
            if self._is_sdk_target(target):
                self._add_issue("SDK_FACADE", "the injected SDK facade is read-only")

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.annotation is not None:
            self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self.visit(node.target)
        if self._is_sdk_target(node.target):
            self._add_issue("SDK_FACADE", "the injected SDK facade is read-only")

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self.visit(node.target)
        if self._is_sdk_target(node.target):
            self._add_issue("SDK_FACADE", "the injected SDK facade is read-only")

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self.visit(target)
            if self._is_sdk_target(target):
                self._add_issue("SDK_FACADE", "the injected SDK facade is read-only")

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self.visit(node.target)
        if self._is_sdk_target(node.target):
            self._add_issue("SDK_FACADE", "the injected SDK facade is read-only")
        for statement in [*node.body, *node.orelse]:
            self.visit(statement)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Local helper definitions are ordinary Python control flow.  Their
        # bodies still receive the same generic safety checks.
        self._check_name(node.name)
        self._visit_signature(node)
        for statement in node.body:
            self.visit(statement)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_name(node.name)
        self._visit_signature(node)
        for statement in node.body:
            self.visit(statement)


def _safe_literal_ast(value: ast.AST) -> bool:
    if isinstance(value, ast.Constant):
        return value.value is None or isinstance(value.value, (bool, int, float, str, bytes))
    if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
        return all(_safe_literal_ast(item) for item in value.elts)
    if isinstance(value, ast.Dict):
        return all(
            key is not None and _safe_literal_ast(key) and _safe_literal_ast(item)
            for key, item in zip(value.keys, value.values, strict=True)
        )
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, (ast.UAdd, ast.USub)):
        return _safe_literal_ast(value.operand)
    if isinstance(value, ast.BinOp) and isinstance(value.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)):
        return _safe_literal_ast(value.left) and _safe_literal_ast(value.right)
    return False


_SAFE_MATH_CONSTANT_NAMES = frozenset({"e", "pi", "tau"})
_SAFE_ARITHMETIC_NODES = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)


def _safe_module_constant_ast(
    value: ast.AST,
    module_aliases: Mapping[str, str],
    module_constants: set[str],
) -> bool:
    if _safe_literal_ast(value):
        return True
    if isinstance(value, ast.Name):
        return value.id in module_constants
    if isinstance(value, ast.Attribute):
        module_path = _module_path(value, module_aliases)
        if module_path != ("math", (value.attr,)) or value.attr not in _SAFE_MATH_CONSTANT_NAMES:
            return False
        constant = getattr(math, value.attr, None)
        return isinstance(constant, (int, float)) and not isinstance(constant, bool) and math.isfinite(float(constant))
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, (ast.UAdd, ast.USub)):
        return _safe_module_constant_ast(value.operand, module_aliases, module_constants)
    if isinstance(value, ast.BinOp) and isinstance(value.op, _SAFE_ARITHMETIC_NODES):
        return (
            _safe_module_constant_ast(value.left, module_aliases, module_constants)
            and _safe_module_constant_ast(value.right, module_aliases, module_constants)
        )
    return False


def _module_import_issues(
    node: ast.Import,
    aliases: dict[str, str],
    symbols: set[str],
    issues: list[dict[str, str]],
) -> None:
    for imported in node.names:
        module_name = imported.name
        binding_name = imported.asname or module_name
        if module_name not in _ALLOWED_MODULE_IMPORTS or "." in module_name:
            issues.append(_issue("FORBIDDEN_IMPORT", f"{module_name} is not an approved absolute import"))
            continue
        expected_alias = {
            "math": {None, "math"},
            "time": {None, "time"},
            "numpy": {None, "np"},
        }[module_name]
        if imported.asname not in expected_alias:
            issues.append(_issue("FORBIDDEN_IMPORT", f"{module_name} has an unapproved alias"))
        if _dunder(binding_name) or _forbidden_candidate_name(binding_name) or binding_name in symbols:
            issues.append(_issue("EXPERIMENTAL_PROFILE", "module imports must bind unique non-dunder names"))
        symbols.add(binding_name)
        aliases[binding_name] = module_name


def _function_shape_issues(function: ast.FunctionDef, *, public: bool, symbol: str) -> list[dict[str, str]]:
    arguments = function.args
    all_arguments = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    if any(_dunder(argument.arg) and argument.arg != "_sdk" for argument in all_arguments):
        return [_issue("PUBLIC_SIGNATURE", f"{symbol} has a dunder parameter")]
    return []


def _function_parameters(function: ast.FunctionDef) -> list[str]:
    return [
        argument.arg
        for argument in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
    ]


def _call_parameter_values(call: ast.Call, function: ast.FunctionDef) -> list[tuple[str, ast.AST]]:
    positional_parameters = [*function.args.posonlyargs, *function.args.args]
    values: list[tuple[str, ast.AST]] = [
        (parameter.arg, value)
        for parameter, value in zip(positional_parameters, call.args)
    ]
    parameter_names = {argument.arg for argument in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]}
    values.extend(
        (keyword.arg, keyword.value)
        for keyword in call.keywords
        if keyword.arg is not None and keyword.arg in parameter_names
    )
    return values


def _sdk_alias_names(function: ast.FunctionDef, initial: set[str]) -> set[str]:
    aliases = set(initial)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in aliases:
                targets = node.targets
            elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Name) and node.value.id in aliases:
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
    return aliases


def _sdk_parameter_names(
    functions: Mapping[str, list[ast.FunctionDef]],
    public_symbols: set[str],
) -> dict[str, set[str]]:
    parameters = {
        symbol: ({"_sdk"} if symbol in public_symbols else set())
        for symbol in functions
    }
    changed = True
    while changed:
        changed = False
        for caller, nodes in functions.items():
            if len(nodes) != 1:
                continue
            caller_aliases = _sdk_alias_names(nodes[0], parameters[caller])
            for call in ast.walk(nodes[0]):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                    continue
                target = functions.get(call.func.id)
                if target is None or len(target) != 1:
                    continue
                for parameter, value in _call_parameter_values(call, target[0]):
                    if isinstance(value, ast.Name) and value.id in caller_aliases and parameter not in parameters[call.func.id]:
                        parameters[call.func.id].add(parameter)
                        changed = True
    return parameters


def _sdk_forwarded_calls(
    functions: Mapping[str, list[ast.FunctionDef]],
    sdk_parameters: Mapping[str, set[str]],
) -> dict[str, set[str]]:
    forwarded: dict[str, set[str]] = {symbol: set() for symbol in functions}
    for caller, nodes in functions.items():
        if len(nodes) != 1:
            continue
        caller_aliases = _sdk_alias_names(nodes[0], sdk_parameters.get(caller, set()))
        for call in ast.walk(nodes[0]):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                continue
            target = functions.get(call.func.id)
            if target is None or len(target) != 1:
                continue
            if any(
                isinstance(value, ast.Name) and value.id in caller_aliases
                for _parameter, value in _call_parameter_values(call, target[0])
            ):
                forwarded[caller].add(call.func.id)
    return forwarded


def _assignment_target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for element in target.elts:
            names.update(_assignment_target_names(element))
        return names
    return set()


def _sdk_call_result(
    value: ast.AST,
    sdk_names: set[str],
    sdk_members: set[str] | frozenset[str],
) -> bool:
    if not isinstance(value, ast.Call):
        return False
    path = _sdk_member_path(value.func, sdk_names)
    return bool(path) and all(segment is not None for segment in path) and path[0] in sdk_members


def _sdk_derived_value(
    value: ast.AST,
    sdk_names: set[str],
    derived_locals: set[str],
    sdk_members: set[str] | frozenset[str],
    helper_return_provenance: Mapping[str, _HelperReturnProvenance] | None = None,
) -> bool:
    path = _sdk_member_path(value, sdk_names)
    sdk_member = (
        path is not None
        and len(path) == 1
        and path[0] is not None
        and path[0] in sdk_members
    )
    helper_return = (
        helper_return_provenance.get(value.func.id)
        if (
            helper_return_provenance is not None
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
        )
        else None
    )
    helper_return_scalar = helper_return.scalar if helper_return is not None else None
    return (
        sdk_member
        or _sdk_call_result(value, sdk_names, sdk_members)
        or (
            helper_return_scalar is not None
            and helper_return_scalar[0]
            and helper_return_scalar[1]
            and helper_return.scalar_member_checked
        )
        or (
            isinstance(value, ast.Name)
            and value.id in derived_locals
            and value.id not in sdk_names
        )
    )


def _sdk_derived_helper_calls(
    function: ast.FunctionDef,
    functions: Mapping[str, list[ast.FunctionDef]],
    sdk_names: set[str],
    initial: set[str],
    sdk_members: set[str] | frozenset[str],
    helper_return_provenance: Mapping[str, _HelperReturnProvenance] | None = None,
) -> list[tuple[ast.Call, set[str]]]:
    """Return reachable helper calls with the SDK-derived locals at each call site."""

    derived_locals = set(initial)
    calls: list[tuple[ast.Call, set[str]]] = []
    ordered_nodes = sorted(
        ast.walk(function),
        key=lambda node: (getattr(node, "lineno", 0), getattr(node, "col_offset", 0)),
    )
    for node in ordered_nodes:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            targets = []
            value = None
        if targets and value is not None and _sdk_derived_value(
            value,
            sdk_names,
            derived_locals,
            sdk_members,
            helper_return_provenance,
        ):
            for target in targets:
                derived_locals.update(_assignment_target_names(target))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions:
            calls.append((node, set(derived_locals)))
    return calls


def _reachable_function_symbols(
    functions: Mapping[str, list[ast.FunctionDef]],
    public_symbols: set[str],
) -> set[str]:
    reachable = {symbol for symbol in public_symbols if symbol in functions}
    changed = True
    while changed:
        changed = False
        for caller in tuple(reachable):
            nodes = functions.get(caller, [])
            if len(nodes) != 1:
                continue
            for call in ast.walk(nodes[0]):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                    continue
                if call.func.id in functions and call.func.id not in reachable:
                    reachable.add(call.func.id)
                    changed = True
    return reachable


def _sdk_derived_parameter_names(
    functions: Mapping[str, list[ast.FunctionDef]],
    sdk_parameters: Mapping[str, set[str]],
    sdk_members: set[str] | frozenset[str],
    helper_return_provenance: Mapping[str, _HelperReturnProvenance] | None = None,
) -> dict[str, set[str]]:
    derived_parameters = {symbol: set() for symbol in functions}
    changed = True
    while changed:
        changed = False
        for caller, nodes in functions.items():
            if len(nodes) != 1:
                continue
            function = nodes[0]
            sdk_names = _sdk_alias_names(function, sdk_parameters.get(caller, set()))
            helper_calls = _sdk_derived_helper_calls(
                function,
                functions,
                sdk_names,
                derived_parameters.get(caller, set()),
                sdk_members,
                helper_return_provenance,
            )
            for call, derived_locals in helper_calls:
                target = functions.get(call.func.id)
                if target is None or len(target) != 1:
                    continue
                for parameter, value in _call_parameter_values(call, target[0]):
                    if _sdk_derived_value(
                        value,
                        sdk_names,
                        derived_locals,
                        sdk_members,
                        helper_return_provenance,
                    ):
                        if parameter not in derived_parameters[call.func.id]:
                            derived_parameters[call.func.id].add(parameter)
                            changed = True
    return derived_parameters


def _sdk_derived_argument_issues(
    functions: Mapping[str, list[ast.FunctionDef]],
    sdk_parameters: Mapping[str, set[str]],
    derived_parameters: Mapping[str, set[str]],
    sdk_members: set[str] | frozenset[str],
    helper_return_provenance: Mapping[str, _HelperReturnProvenance] | None = None,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for caller, nodes in functions.items():
        if len(nodes) != 1:
            continue
        function = nodes[0]
        sdk_names = _sdk_alias_names(function, sdk_parameters.get(caller, set()))
        helper_calls = _sdk_derived_helper_calls(
            function,
            functions,
            sdk_names,
            derived_parameters.get(caller, set()),
            sdk_members,
            helper_return_provenance,
        )
        for call, derived_locals in helper_calls:
            target = functions.get(call.func.id)
            if target is None or len(target) != 1:
                continue
            for parameter, value in _call_parameter_values(call, target[0]):
                if (
                    parameter in derived_parameters.get(call.func.id, set())
                    and not _sdk_derived_value(
                        value,
                        sdk_names,
                        derived_locals,
                        sdk_members,
                        helper_return_provenance,
                    )
                ):
                    issues.append(_issue(
                        "EXPERIMENTAL_PROFILE",
                        "helper SDK-derived parameters must receive an SDK-derived value",
                    ))
    return issues


def _static_issues(tree: ast.Module, contracts: Mapping[str, Mapping[str, Any]], profile: ValidationAProfile) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    functions: dict[str, list[ast.FunctionDef]] = {}
    expected_symbols = {item["function_name"] for item in contracts.values()}
    module_aliases: dict[str, str] = {}
    module_constants: set[str] = set()
    module_symbols: set[str] = set()
    for node in tree.body:
        if (
            node is tree.body[0]
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if isinstance(node, ast.Import):
            _module_import_issues(node, module_aliases, module_symbols, issues)
            continue
        if isinstance(node, ast.ImportFrom):
            issues.append(_issue("FORBIDDEN_IMPORT", "relative, star, and from-imports are not allowed"))
            continue
        if isinstance(node, ast.FunctionDef):
            if _dunder(node.name):
                issues.append(_issue("FORBIDDEN_DUNDER", "dunder names are forbidden in the experimental profile"))
            elif _forbidden_candidate_name(node.name):
                issues.append(_issue("FORBIDDEN_NAME", "direct privileged or dunder helper names are forbidden"))
            if node.name in module_symbols:
                issues.append(_issue("PUBLIC_SYMBOLS", "module symbols must not be redefined"))
            functions.setdefault(node.name, []).append(node)
            module_symbols.add(node.name)
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
            if (
                len(targets) != 1
                or not isinstance(targets[0], ast.Name)
                or _dunder(targets[0].id)
                or _forbidden_candidate_name(targets[0].id)
                or targets[0].id in module_symbols
                or value is None
                or not _safe_module_constant_ast(value, module_aliases, module_constants)
                or (isinstance(node, ast.AnnAssign) and node.annotation is not None)
            ):
                issues.append(_issue("EXPERIMENTAL_PROFILE", "module scope allows only safe literal constants"))
            else:
                module_symbols.add(targets[0].id)
                module_constants.add(targets[0].id)
            continue
        if isinstance(node, ast.ClassDef):
            issues.append(_issue("PUBLIC_SYMBOLS", "classes are not allowed in capability.py"))
            continue
        issues.append(_issue("EXPERIMENTAL_PROFILE", "module scope allows only a docstring, approved imports, constants, and functions"))
    public_symbols = {name for name in functions if name in expected_symbols}
    if any(len(nodes) != 1 for nodes in functions.values()) or public_symbols != expected_symbols:
        issues.append(_issue("PUBLIC_SYMBOLS", "capability.py must define exactly the bound public functions"))
    # Extra module-local helpers are ordinary implementation code.  They do
    # not change the required public binding and are checked by the same
    # generic safety visitor below.
    symbol_to_capability = {item["function_name"]: capability_id for capability_id, item in contracts.items()}
    all_sdk_members = frozenset(
        member
        for members in profile.sdk_facade_members.values()
        for member in members
    )
    # The old analyzer tracked SDK-derived value provenance and rejected
    # ordinary Python data processing.  Validation A intentionally does not
    # infer those types: the source-coupled Sandbox and Validation B own
    # runtime/type/physics correctness.  Every declared function is still
    # scanned for generic escape hatches, including extra helpers.
    sdk_parameter_names = _sdk_parameter_names(functions, public_symbols)
    analyzers: dict[str, _RelaxedStaticAnalyzer] = {}
    for symbol, nodes in functions.items():
        if len(nodes) != 1:
            continue
        public = symbol in symbol_to_capability
        capability_id = symbol_to_capability.get(symbol)
        function = nodes[0]
        issues.extend(_function_shape_issues(function, public=public, symbol=symbol))
        if public:
            assert capability_id is not None
            arguments = function.args
            expected_parameters = [parameter["parameter"] for parameter in contracts[capability_id]["parameters"]]
            if (
                function.decorator_list
                or any(_dunder(argument.arg) for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs])
                or arguments.defaults
                or any(default is not None for default in arguments.kw_defaults)
                or arguments.posonlyargs
                or [argument.arg for argument in arguments.args] != expected_parameters
                or [argument.arg for argument in arguments.kwonlyargs] != ["_sdk"]
                or arguments.vararg is not None
                or arguments.kwarg is not None
            ):
                issues.append(_issue("PUBLIC_SIGNATURE", f"{symbol} does not exactly match the Framework Binding"))
            members = profile.sdk_facade_members[capability_id]
        else:
            members = all_sdk_members
        analyzer = _RelaxedStaticAnalyzer(
            function,
            members,
            module_aliases,
            module_constants,
            set(functions),
            _sdk_alias_names(function, sdk_parameter_names.get(symbol, set())),
        )
        issues.extend(analyzer.analyze())
        analyzers[symbol] = analyzer

    def _uses_sdk(symbol: str, seen: set[str]) -> bool:
        if symbol in seen:
            return False
        seen.add(symbol)
        analyzer = analyzers.get(symbol)
        if analyzer is None:
            return False
        return analyzer.approved_sdk_use or any(
            _uses_sdk(child, seen) for child in analyzer.called_helpers
        )

    for symbol in symbol_to_capability:
        if symbol in analyzers and not _uses_sdk(symbol, set()):
            issues.append(_issue("SDK_INJECTION", f"{symbol} must use the injected _sdk facade"))
    return issues


def _descriptor_match(value: Any, field: Mapping[str, Any]) -> bool:
    field_type = field["type"]
    shape = field["shape"]
    vector_length: int | None = None
    array_length: int | None = None
    if (
        isinstance(shape, str)
        and shape.startswith("vector:")
        and shape.removeprefix("vector:").isdigit()
        and int(shape.removeprefix("vector:")) > 0
    ):
        vector_length = int(shape.removeprefix("vector:"))
    if isinstance(shape, str):
        match = re.fullmatch(r"\[([0-9]+)\]", shape)
        if match is not None:
            length = int(match.group(1))
            if length > 0:
                array_length = length
    if vector_length is not None and field_type == "number":
        type_ok = isinstance(value, list) and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in value
        )
    elif field_type in {"number", "float"}:
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
    elif array_length is not None:
        shape_ok = field_type == "array" and isinstance(value, list) and len(value) == array_length
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


class _FixtureSdkProxy:
    """Non-executing object proxy for the approved SDK surface."""

    __slots__ = ("_facade", "_path")

    def __init__(self, facade: "_FixtureSdkFacade", path: str):
        object.__setattr__(self, "_facade", facade)
        object.__setattr__(self, "_path", path)

    def __getattr__(self, name: str) -> "_FixtureSdkProxy":
        if _dunder(name):
            raise AttributeError(name)
        return _FixtureSdkProxy(self._facade, f"{self._path}.{name}")

    def __getitem__(self, index: Any) -> "_FixtureSdkProxy":
        return _FixtureSdkProxy(self._facade, f"{self._path}[{index!r}]")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_facade", "_path"}:
            object.__setattr__(self, name, value)
            return
        self._facade.mutations.append((f"{self._path}.{name}", value))

    def __call__(self, *args: Any, **kwargs: Any) -> "_FixtureSdkProxy":
        self._facade.calls.append((self._path, args, kwargs))
        return _FixtureSdkProxy(self._facade, f"{self._path}()")


class _FixtureSdkFacade:
    def __init__(self, members: tuple[str, ...] | list[str]):
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.mutations: list[tuple[str, Any]] = []
        self._members = members

    def __getattr__(self, name: str) -> _FixtureSdkProxy:
        if name not in self._members:
            raise AttributeError(name)
        return _FixtureSdkProxy(self, name)


def _allowed_import(
    name: str,
    _globals: Mapping[str, Any] | None = None,
    _locals: Mapping[str, Any] | None = None,
    fromlist: tuple[str, ...] | list[str] = (),
    level: int = 0,
    time_module: object | None = None,
) -> ModuleType:
    if level != 0 or name not in _ALLOWED_MODULE_IMPORTS or fromlist:
        raise ImportError(f"candidate import is not allowed: {name}")
    if name == "math":
        return math
    if name == "time":
        return time if time_module is None else time_module  # type: ignore[return-value]
    try:
        import numpy as np
    except Exception as exc:
        raise ImportError("approved numpy dependency is unavailable") from exc
    return np


def _isolated_module(source: str, *, time_module: object | None = None) -> ModuleType:
    module = ModuleType("validated_capability")
    safe_builtins = dict(_SAFE_BUILTINS)

    def allowed_import(
        name: str,
        globals_: Mapping[str, Any] | None = None,
        locals_: Mapping[str, Any] | None = None,
        fromlist: tuple[str, ...] | list[str] = (),
        level: int = 0,
    ) -> ModuleType:
        return _allowed_import(
            name,
            globals_,
            locals_,
            fromlist,
            level,
            time_module=time_module,
        )

    safe_builtins["__import__"] = allowed_import
    module.__dict__.update({"__name__": module.__name__, "__builtins__": safe_builtins})
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
            for capability_id, item in contracts.items():
                try:
                    _fixture_inputs(self.profile.fixture_probes[capability_id], item["inputs"])
                except ContractError as exc:
                    diagnostics.append(_issue("A_PROFILE_PROBE", str(exc)))

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
                        for item in contracts.values():
                            function = getattr(module, item["function_name"], None)
                            if not callable(function):
                                raise ContractError("bound capability symbol is not callable")
                    except Exception as exc:
                        diagnostics.append(_issue("IMPORTABILITY", f"capability.py import or binding failed: {type(exc).__name__}"))
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
                source=source,
                source_hash=source_hash,
                implementation_manifest_hash=manifest_hash,
                implementation_bundle_hash=implementation_bundle_hash,
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
    handle = result.candidate_handle
    if not isinstance(handle, ValidatedCandidateHandle):
        raise ContractError("only a Framework Validation A candidate may be bound")
    payload = handle._framework_payload_snapshot()
    if (
        result.source_hash != payload.source_hash
        or result.implementation_manifest_hash != payload.implementation_manifest_hash
        or result.implementation_bundle_hash != payload.implementation_bundle_hash
        or result.report_hash != payload.validation_a_report_hash
        or content_hash(canonical_bytes(result.report)) != result.report_hash
        or result.report.get("status") != "PASS"
        or result.report.get("binding_result") != result.binding_result
        or not verify_seal(dict(result.report_seal))
        or result.report_seal.get("artifact_type") != "validation_a_report"
        or result.report_seal.get("artifact_hash") != result.report_hash
    ):
        raise ContractError("Validation A result no longer matches its Framework candidate payload")
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
        "source_hash": payload.source_hash,
        "implementation_manifest_hash": result.implementation_manifest_hash,
        "implementation_bundle_hash": result.implementation_bundle_hash,
        "validation_a_report_hash": result.report_hash,
        "capability_bindings": bindings,
    }
    overlay_hash = content_hash(canonical_bytes(overlay))
    sealed = BindingOverlay(
        overlay=overlay,
        overlay_hash=overlay_hash,
        seal=create_seal(
            "validation_execution_binding_overlay",
            overlay_hash,
            [
                validation_suite_hash,
                payload.source_hash,
                result.implementation_manifest_hash,
                result.implementation_bundle_hash,
                result.report_hash,
            ],
        ),
    )
    return handle._with_overlay(sealed)


# Kept as a small compatibility name; it returns the opaque B handle, never a module.
create_execution_binding_overlay = bind_candidate_to_suite
