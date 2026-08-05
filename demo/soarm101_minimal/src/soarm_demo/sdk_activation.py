"""Research-minimal activation gate for the pinned SOARM101 LeRobot SDK.

The gate answers one small question before any model call: do the committed
source records and the hardware-free probe support the exact API contract used
by this demo?  A run stores one compact JSON decision plus SHA-256 bindings to
the four source records.  It deliberately does not copy those records into
every run or persist a large matrix of redundant boolean checks.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .audit import atomic_write_json, sha256_file
from .libraries import load_structured
from .schema_validation import validate_json_schema


class SDKActivationError(RuntimeError):
    """The pinned SDK evidence cannot authorize the selected run mode."""


_FILES = {
    "manifest": "manifest.yaml",
    "api_surface": "api_surface.yaml",
    "runtime_contract": "runtime_contract.yaml",
    "api_probe": "api_probe.json",
}
_SCHEMAS = {
    "manifest": "library_manifest.schema.json",
    "api_surface": "sdk_runtime.schema.json",
    "runtime_contract": "runtime_contract.schema.json",
    "api_probe": "sdk_api_probe_result.schema.json",
}
_PACKAGE = "lerobot"
_VERSION = "0.6.0"
_RUNTIME_ID = "lerobot_soarm101_0_6_0"
_CANONICAL_IMPORT = {
    "module": "lerobot.robots.so_follower",
    "symbols": ["SO101Follower", "SO101FollowerConfig"],
}
_ALIASES = {
    "SO101Follower": "SOFollower",
    "SO101FollowerConfig": "SOFollowerRobotConfig",
}
_REQUIRED_METHODS = [
    "connect",
    "disconnect",
    "send_action",
    "get_observation",
]
_FEATURE_KEYS = [
    "shoulder_pan.pos",
    "shoulder_lift.pos",
    "elbow_flex.pos",
    "wrist_flex.pos",
    "wrist_roll.pos",
    "gripper.pos",
]
_UNITS = {
    "shoulder_pan.pos": "deg",
    "shoulder_lift.pos": "deg",
    "elbow_flex.pos": "deg",
    "wrist_flex.pos": "deg",
    "wrist_roll.pos": "deg",
    "gripper.pos": "normalized_0_100",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return []
    return list(value)


def _named_items(value: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        return {}
    return {
        str(item["name"]): item
        for item in value
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }


def _relative_run_path(path: Path, run_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(run_root.resolve()).as_posix()
    except ValueError as exc:
        raise SDKActivationError("SDK activation evidence escaped the run root") from exc
    parts = PurePosixPath(relative).parts
    if not relative or PurePosixPath(relative).is_absolute() or ".." in parts:
        raise SDKActivationError("SDK activation evidence path is not run-relative")
    return relative


def evaluate_sdk_activation(
    *, sdk_root: str | Path, schemas_root: str | Path, mode: str
) -> dict[str, Any]:
    """Verify the small, explicit LeRobot contract used by this research demo.

    AWS and offline modes verify the same source/probe facts.  Only AWS may
    satisfy the live-provider gate; offline remains a reproducible reference
    run even when every SDK fact passes.
    """

    if mode not in {"aws", "offline"}:
        raise SDKActivationError(f"unsupported SDK activation mode {mode!r}")

    root = Path(sdk_root).resolve()
    schema_root = Path(schemas_root).resolve()
    documents: dict[str, Mapping[str, Any]] = {}
    paths: dict[str, Path] = {}
    errors: list[str] = []

    def require(name: str, condition: bool, detail: str) -> None:
        if not condition:
            errors.append(f"{name}: {detail}")

    for name, relative in _FILES.items():
        path = root / relative
        paths[name] = path
        if not path.is_file() or path.is_symlink():
            errors.append(f"{name}_file: {relative} must be a non-symlink regular file")
            documents[name] = {}
            continue
        try:
            document = load_structured(path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{name}_parse: {type(exc).__name__}")
            documents[name] = {}
            continue
        if not isinstance(document, Mapping):
            errors.append(f"{name}_parse: {relative} must contain an object")
            documents[name] = {}
            continue
        documents[name] = document
        schema_path = schema_root / _SCHEMAS[name]
        if not schema_path.is_file() or schema_path.is_symlink():
            errors.append(f"{name}_schema: {_SCHEMAS[name]} is unavailable")
            continue
        try:
            schema = load_structured(schema_path)
            issues = validate_json_schema(document, schema, instance_path=f"${name}")
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"{name}_schema: {type(exc).__name__}")
            continue
        if issues:
            errors.append(
                f"{name}_schema: "
                + "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
            )

    manifest = documents.get("manifest", {})
    api = documents.get("api_surface", {})
    runtime = documents.get("runtime_contract", {})
    probe = documents.get("api_probe", {})
    compatibility = _mapping(manifest.get("compatibility"))
    declared_hashes = _mapping(manifest.get("content_hashes"))

    hashes_match = True
    for name in ("api_surface", "runtime_contract", "api_probe"):
        path = paths[name]
        relative = _FILES[name]
        hashes_match = hashes_match and path.is_file() and not path.is_symlink()
        if path.is_file() and not path.is_symlink():
            hashes_match = hashes_match and declared_hashes.get(relative) == sha256_file(path)
    require(
        "manifest_payload_hashes",
        hashes_match,
        "manifest hashes must bind api_surface, runtime_contract, and api_probe",
    )

    require(
        "pinned_lerobot_target",
        manifest.get("library_type") == "sdk_runtime"
        and manifest.get("entry_id") == "lerobot_soarm101"
        and manifest.get("status") == "ready"
        and manifest.get("version") == _VERSION
        and compatibility.get("package") == _PACKAGE
        and compatibility.get("package_version") == _VERSION
        and api.get("distribution") == _PACKAGE
        and api.get("version") == _VERSION
        and _mapping(probe.get("target")).get("package") == _PACKAGE
        and _mapping(probe.get("target")).get("distribution") == _PACKAGE
        and _mapping(probe.get("target")).get("version") == _VERSION,
        "all accepted records must identify LeRobot 0.6.0",
    )

    commit = compatibility.get("package_commit")
    require(
        "pinned_source_commit",
        isinstance(commit, str)
        and len(commit) == 40
        and api.get("commit") == commit
        and _mapping(probe.get("target")).get("commit") == commit,
        "manifest, API surface, and probe must identify one 40-hex source commit",
    )
    require(
        "runtime_identity",
        api.get("runtime_id") == _RUNTIME_ID
        and runtime.get("runtime_id") == _RUNTIME_ID
        and probe.get("runtime_id") == _RUNTIME_ID
        and runtime.get("target_class")
        == "lerobot.robots.so_follower.SO101Follower",
        "all records must identify the pinned SOARM101 runtime",
    )

    api_import = _mapping(api.get("canonical_import"))
    probe_import = _mapping(probe.get("canonical_import"))
    canonical_import_ok = (
        api_import.get("module") == _CANONICAL_IMPORT["module"]
        and _string_list(api_import.get("symbols")) == _CANONICAL_IMPORT["symbols"]
        and probe_import.get("module") == _CANONICAL_IMPORT["module"]
        and _string_list(probe_import.get("symbols")) == _CANONICAL_IMPORT["symbols"]
    )
    api_aliases = _mapping(api.get("aliases_at_v0_6_0"))
    probe_aliases = _mapping(probe.get("alias_targets"))
    alias_identity = _mapping(probe.get("canonical_aliases"))
    aliases_ok = (
        all(api_aliases.get(name) == target for name, target in _ALIASES.items())
        and dict(probe_aliases) == _ALIASES
        and alias_identity.get("SO101Follower_is_SOFollower") is True
        and alias_identity.get("SO101FollowerConfig_is_SOFollowerRobotConfig") is True
    )
    require(
        "canonical_import_and_aliases",
        canonical_import_ok and aliases_ok,
        "canonical imports and their v0.6.0 alias identities must match",
    )

    api_methods = _named_items(api.get("methods"))
    probe_methods = set(_string_list(probe.get("public_methods")))
    signatures = _mapping(probe.get("signatures"))
    require(
        "required_public_methods",
        all(name in api_methods and name in probe_methods for name in _REQUIRED_METHODS)
        and all(isinstance(signatures.get(name), str) and signatures[name] for name in _REQUIRED_METHODS),
        "connect, disconnect, send_action, and get_observation must be present and probed",
    )

    motors = api.get("motors")
    motor_keys = (
        [item.get("feature_key") for item in motors if isinstance(item, Mapping)]
        if isinstance(motors, list)
        else []
    )
    properties = _named_items(api.get("properties"))
    api_action_features = list(_mapping(properties.get("action_features", {}).get("p0_value")))
    api_observation_features = list(
        _mapping(properties.get("observation_features", {}).get("p0_value"))
    )
    api_allowed_fields = _string_list(
        _mapping(api.get("action_details")).get("p0_allowed_fields")
    )
    p0 = _mapping(runtime.get("p0_configuration"))
    action_keys = _string_list(probe.get("action_keys"))
    observation_keys = _string_list(probe.get("observation_keys"))
    require(
        "six_feature_keys",
        all(
            keys == _FEATURE_KEYS
            for keys in (
                motor_keys,
                api_action_features,
                api_observation_features,
                api_allowed_fields,
                _string_list(p0.get("action_keys")),
                _string_list(p0.get("observation_keys")),
                action_keys,
                observation_keys,
            )
        ),
        "action and observation contracts must use exactly the six SOARM101 keys",
    )

    p0_profile = _mapping(_mapping(api.get("unit_profiles")).get("p0_degrees"))
    api_units = {key: p0_profile.get(key) for key in _FEATURE_KEYS}
    probe_unit_modes = _mapping(probe.get("unit_modes"))
    units_ok = (
        p0.get("use_degrees") is True
        and dict(_mapping(p0.get("units"))) == _UNITS
        and api_units == _UNITS
        and probe_unit_modes.get("use_degrees_true_arm") == "degrees"
        and probe_unit_modes.get("use_degrees_false_arm") == "range_m100_100"
        and probe_unit_modes.get("gripper_in_both_profiles") == "range_0_100"
    )
    require(
        "unit_semantics",
        units_ok,
        "five joints must use degrees and the gripper normalized_0_100 in P0",
    )

    send_action = api_methods.get("send_action", {})
    command_trace = _mapping(probe.get("command_trace"))
    command_semantics = _mapping(runtime.get("command_semantics"))
    require(
        "nonblocking_send_action",
        send_action.get("waits_for_physical_arrival") is False
        and command_trace.get("waits_for_arrival") is False
        and command_semantics.get("polling_required_for_completion") is True,
        "send_action must return after target write; callers poll for convergence",
    )
    require(
        "hardware_free_probe_passed",
        probe.get("status") == "pass"
        and probe.get("hardware_or_serial_opened") is False,
        "the committed hardware-free API probe must pass without serial access",
    )

    if errors:
        raise SDKActivationError("SDK activation contract rejected: " + "; ".join(errors))

    bindings = {
        name: {"path": relative, "sha256": sha256_file(paths[name])}
        for name, relative in _FILES.items()
    }
    return {
        "schema_version": "robot_capability.sdk_activation.v2",
        "mode": mode,
        "activation_scope": (
            "live_aws_provider_gate" if mode == "aws" else "offline_reference_only"
        ),
        "live_gate_satisfied": mode == "aws",
        "probe_status": "pass",
        "target": {"package": _PACKAGE, "version": _VERSION, "commit": commit},
        "runtime_id": _RUNTIME_ID,
        "verified": {
            "canonical_import": copy.deepcopy(_CANONICAL_IMPORT),
            "aliases": copy.deepcopy(_ALIASES),
            "required_methods": list(_REQUIRED_METHODS),
            "action_keys": list(_FEATURE_KEYS),
            "observation_keys": list(_FEATURE_KEYS),
            "units": dict(_UNITS),
            "send_action_nonblocking": True,
            "hardware_or_serial_opened": False,
        },
        "bindings": bindings,
    }


def freeze_sdk_activation(
    *,
    sdk_root: str | Path,
    run_root: str | Path,
    decision: Mapping[str, Any],
    schema_path: str | Path,
) -> dict[str, Any]:
    """Write one run-local activation record; source bytes stay in the library.

    The source hashes are checked once at the hand-off.  The run-wide
    ``run_inputs.json`` records the same hashes for reproducibility, so copying
    four identical files into every run provides no additional research value.
    """

    source_root = Path(sdk_root).resolve()
    target_root = Path(run_root).resolve()
    record_path = target_root / "framework/sdk_activation.json"
    if record_path.exists():
        raise SDKActivationError("SDK activation evidence destination must be new")

    bindings = _mapping(decision.get("bindings"))
    for name, relative in _FILES.items():
        source = source_root / relative
        binding = _mapping(bindings.get(name))
        if (
            not source.is_file()
            or source.is_symlink()
            or binding.get("path") != relative
            or binding.get("sha256") != sha256_file(source)
        ):
            raise SDKActivationError(
                f"SDK activation source changed after evaluation: {relative}"
            )

    record = copy.deepcopy(dict(decision))
    schema = load_structured(schema_path)
    issues = validate_json_schema(record, schema, instance_path="$sdk_activation")
    if issues:
        raise SDKActivationError(
            "SDK activation record failed schema validation: "
            + "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        )
    atomic_write_json(record_path, record)
    return {
        "decision": record,
        "evidence": {
            "path": _relative_run_path(record_path, target_root),
            "sha256": sha256_file(record_path),
            "bytes": record_path.stat().st_size,
        },
    }


def frozen_sdk_activation_semantic_errors(
    activation: Any,
    *,
    run_root: str | Path,
    input_hashes: Any,
    artifact_hashes: Any,
    expected_mode: Any = None,
) -> tuple[str, ...]:
    """Check only the sealed decision file, mode semantics, and core API facts."""

    del input_hashes  # Source bindings are recorded once by run_inputs.json.
    value = _mapping(activation)
    decision = _mapping(value.get("decision"))
    evidence = _mapping(value.get("evidence"))
    artifacts = _mapping(artifact_hashes)
    root = Path(run_root).resolve()
    errors: list[str] = []

    if evidence.get("path") != "framework/sdk_activation.json":
        return ("sdk_activation evidence path must be framework/sdk_activation.json",)
    record_path = root / "framework/sdk_activation.json"
    if not record_path.is_file() or record_path.is_symlink():
        return ("sdk_activation evidence is missing or is a symlink",)
    actual_hash = sha256_file(record_path)
    if evidence.get("sha256") != actual_hash:
        errors.append("sdk_activation evidence hash is stale")
    if evidence.get("bytes") != record_path.stat().st_size:
        errors.append("sdk_activation evidence byte count is stale")
    if artifacts.get("sdk_activation") != actual_hash:
        errors.append("artifact_hashes.sdk_activation must bind sdk_activation evidence")
    try:
        recorded = load_structured(record_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return tuple(errors + ["sdk_activation evidence is unreadable"])
    if recorded != decision:
        errors.append("sealed sdk_activation decision differs from its evidence file")

    mode = decision.get("mode")
    if expected_mode is not None and mode != expected_mode:
        errors.append("sdk_activation mode differs from the run report mode")
    if mode == "aws":
        if decision.get("activation_scope") != "live_aws_provider_gate" or decision.get(
            "live_gate_satisfied"
        ) is not True:
            errors.append("AWS sdk_activation must be a satisfied live provider gate")
    elif mode == "offline":
        if decision.get("activation_scope") != "offline_reference_only" or decision.get(
            "live_gate_satisfied"
        ) is not False:
            errors.append("offline sdk_activation must remain reference-only")
    else:
        errors.append("sdk_activation mode is invalid")

    verified = _mapping(decision.get("verified"))
    if (
        decision.get("schema_version") != "robot_capability.sdk_activation.v2"
        or decision.get("probe_status") != "pass"
        or _mapping(decision.get("target")).get("package") != _PACKAGE
        or _mapping(decision.get("target")).get("version") != _VERSION
        or verified.get("canonical_import") != _CANONICAL_IMPORT
        or verified.get("aliases") != _ALIASES
        or verified.get("required_methods") != _REQUIRED_METHODS
        or verified.get("action_keys") != _FEATURE_KEYS
        or verified.get("observation_keys") != _FEATURE_KEYS
        or verified.get("units") != _UNITS
        or verified.get("send_action_nonblocking") is not True
        or verified.get("hardware_or_serial_opened") is not False
    ):
        errors.append("sdk_activation core verified facts are inconsistent")
    return tuple(errors)
