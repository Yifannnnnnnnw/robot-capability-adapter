"""Runtime validators for critical artifacts and JSON-shaped library data."""

from __future__ import annotations

import hashlib
import keyword
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError


CAPABILITY_ID_RE = re.compile(r"^G([123])\.([a-z][a-z0-9_]*)$")
FUNCTION_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str
    # Optional machine-readable context is intentionally limited to metadata.
    # In particular, rejected source strings are never copied into an issue:
    # callers can give an LLM the exact size/constraint without repeating the
    # potentially very large value in its next request.
    keyword: str | None = None
    constraint: Any = None
    observed: Mapping[str, Any] | None = None


class ArtifactSchemaError(ValueError):
    def __init__(self, issues: Iterable[ValidationIssue]):
        self.issues = list(issues)
        super().__init__("; ".join(f"{item.path}: {item.message}" for item in self.issues))


def non_finite_json_issues(value: Any, path: str = "$") -> list[ValidationIssue]:
    """Return paths containing NaN or +/-Infinity.

    Python's standard JSON decoder and encoder accept these non-standard values
    by default.  They are unsuitable for hashed, cross-language artifacts, so
    every runtime ingestion boundary rejects them explicitly.
    """

    issues: list[ValidationIssue] = []
    if isinstance(value, float) and not math.isfinite(value):
        issues.append(ValidationIssue(path, "non-finite numbers are not valid JSON"))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}" if isinstance(key, str) else f"{path}[{key!r}]"
            issues.extend(non_finite_json_issues(item, child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            issues.extend(non_finite_json_issues(item, f"{path}[{index}]"))
    return issues


def validate_json_schema(
    value: Any,
    schema: Mapping[str, Any],
    *,
    instance_path: str = "$",
) -> list[ValidationIssue]:
    """Validate one value with Draft 2020-12 and deterministic issue paths."""

    issues = non_finite_json_issues(value, instance_path)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"invalid runtime JSON Schema: {exc.message}") from exc
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(value),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )
    for error in errors:
        path = instance_path
        for part in error.absolute_path:
            if isinstance(part, int):
                path += f"[{part}]"
            else:
                path += f".{part}"
        observed: dict[str, Any] | None = None
        if isinstance(error.instance, str):
            encoded = error.instance.encode("utf-8")
            observed = {
                "type": "string",
                "characters": len(error.instance),
                "utf8_bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        issues.append(
            ValidationIssue(
                path,
                error.message,
                keyword=str(error.validator) if error.validator is not None else None,
                constraint=error.validator_value,
                observed=observed,
            )
        )
    return issues


def _reject_unknown_properties(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    for field in sorted(set(value) - allowed, key=repr):
        issues.append(ValidationIssue(f"{path}.{field}", "additional property is not allowed"))


def validate_stage1(value: Any) -> dict[str, Any]:
    issues: list[ValidationIssue] = []
    if not isinstance(value, dict):
        raise ArtifactSchemaError([ValidationIssue("$", "must be an object")])
    issues.extend(non_finite_json_issues(value))
    _reject_unknown_properties(
        value,
        allowed={
            "schema_version",
            "target",
            "layers",
            "assumptions",
            "unresolved_evidence_gaps",
            "evidence_refs",
        },
        path="$",
        issues=issues,
    )
    if value.get("schema_version") != "robot_capability.stage1.v2":
        issues.append(ValidationIssue("$.schema_version", "unexpected schema version"))
    target = value.get("target")
    if not isinstance(target, dict):
        issues.append(ValidationIssue("$.target", "must be an object"))
    else:
        _reject_unknown_properties(
            target,
            allowed={"robot_id", "runtime_id"},
            path="$.target",
            issues=issues,
        )
        if target.get("robot_id") != "soarm101":
            issues.append(ValidationIssue("$.target.robot_id", "must equal soarm101"))
        if target.get("runtime_id") != "lerobot_soarm101_0_6_0":
            issues.append(ValidationIssue("$.target.runtime_id", "must use pinned runtime"))

    layers = value.get("layers")
    seen_ids: set[str] = set()
    seen_functions_by_layer: dict[str, set[str]] = {name: set() for name in ("G1", "G2", "G3")}
    if not isinstance(layers, dict):
        issues.append(ValidationIssue("$.layers", "must be an object"))
    else:
        _reject_unknown_properties(
            layers,
            allowed={"G1", "G2", "G3"},
            path="$.layers",
            issues=issues,
        )
        for layer_name in ("G1", "G2", "G3"):
            layer = layers.get(layer_name)
            layer_path = f"$.layers.{layer_name}"
            if not isinstance(layer, dict):
                issues.append(ValidationIssue(layer_path, "missing layer"))
                continue
            _reject_unknown_properties(
                layer,
                allowed={"rationale", "capabilities"},
                path=layer_path,
                issues=issues,
            )
            if (
                not isinstance(layer.get("rationale"), str)
                or len(layer["rationale"]) < 10
            ):
                issues.append(ValidationIssue(f"{layer_path}.rationale", "must contain a rationale"))
            capabilities = layer.get("capabilities")
            if not isinstance(capabilities, list) or not capabilities:
                issues.append(ValidationIssue(f"{layer_path}.capabilities", "must be a non-empty list"))
                continue
            maximum = {"G1": 1, "G2": 1, "G3": 3}[layer_name]
            if len(capabilities) > maximum:
                issues.append(
                    ValidationIssue(
                        f"{layer_path}.capabilities",
                        f"minimal P0 permits at most {maximum} capabilities in {layer_name}",
                    )
                )
            for index, capability in enumerate(capabilities):
                path = f"{layer_path}.capabilities[{index}]"
                if not isinstance(capability, dict):
                    issues.append(ValidationIssue(path, "must be an object"))
                    continue
                _reject_unknown_properties(
                    capability,
                    allowed={
                        "capability_id",
                        "function_name",
                        "intended_outcome",
                        "validation_effect",
                        "implementation_family",
                        "implementation_evidence",
                    },
                    path=path,
                    issues=issues,
                )
                capability_id = capability.get("capability_id")
                function_name = capability.get("function_name")
                outcome = capability.get("intended_outcome")
                validation_effect = capability.get("validation_effect")
                implementation_family = capability.get("implementation_family")
                implementation_evidence = capability.get("implementation_evidence")
                match = CAPABILITY_ID_RE.fullmatch(capability_id or "")
                if not match or f"G{match.group(1)}" != layer_name:
                    issues.append(ValidationIssue(f"{path}.capability_id", "invalid or wrong granularity"))
                elif capability_id in seen_ids:
                    issues.append(ValidationIssue(f"{path}.capability_id", "duplicate capability ID"))
                else:
                    seen_ids.add(capability_id)
                if (
                    not isinstance(function_name, str)
                    or not FUNCTION_RE.fullmatch(function_name)
                    or keyword.iskeyword(function_name)
                    or function_name.startswith(("get_", "read_"))
                ):
                    issues.append(
                        ValidationIssue(
                            f"{path}.function_name",
                            "must be an action capability with a valid snake_case identifier; get_/read_ helpers are not public P0 capabilities",
                        )
                    )
                elif function_name in seen_functions_by_layer[layer_name]:
                    issues.append(ValidationIssue(f"{path}.function_name", "duplicate within layer"))
                else:
                    seen_functions_by_layer[layer_name].add(function_name)
                if not isinstance(outcome, str) or len(outcome) < 10:
                    issues.append(ValidationIssue(f"{path}.intended_outcome", "must describe an outcome"))
                permitted_effects = {
                    "G1": {"joint_targets"},
                    "G2": {"cartesian_target"},
                    "G3": {
                        "object_source_to_target",
                        "object_source_plus_height_delta",
                        "object_move_sequence",
                    },
                }[layer_name]
                if validation_effect not in permitted_effects:
                    issues.append(
                        ValidationIssue(
                            f"{path}.validation_effect",
                            f"must be one of {sorted(permitted_effects)} for {layer_name}",
                        )
                    )
                permitted_families = {
                    "G1": {"joint_motion"},
                    "G2": {"cartesian_motion"},
                    "G3": {
                        "push",
                        "pick_place",
                        "lift",
                        "ordered_pick_place_sequence",
                    },
                }[layer_name]
                if implementation_family not in permitted_families:
                    issues.append(
                        ValidationIssue(
                            f"{path}.implementation_family",
                            f"must be one of {sorted(permitted_families)} for {layer_name}",
                        )
                    )
                if not isinstance(implementation_evidence, dict):
                    issues.append(
                        ValidationIssue(
                            f"{path}.implementation_evidence",
                            "must be a verified implementation-evidence object",
                        )
                    )
                else:
                    _reject_unknown_properties(
                        implementation_evidence,
                        allowed={"status", "basis", "refs"},
                        path=f"{path}.implementation_evidence",
                        issues=issues,
                    )
                    if implementation_evidence.get("status") != "verified":
                        issues.append(
                            ValidationIssue(
                                f"{path}.implementation_evidence.status",
                                "selected capabilities require verified evidence",
                            )
                        )
                    permitted_bases = {
                        "G1": {"pinned_runtime_contract", "orchestration_fixture"},
                        "G2": {"compiled_kinematics", "orchestration_fixture"},
                        "G3": {"verified_physical_baseline", "orchestration_fixture"},
                    }[layer_name]
                    if implementation_evidence.get("basis") not in permitted_bases:
                        issues.append(
                            ValidationIssue(
                                f"{path}.implementation_evidence.basis",
                                f"must be one of {sorted(permitted_bases)} for {layer_name}",
                            )
                        )
                    refs = implementation_evidence.get("refs")
                    if (
                        not isinstance(refs, list)
                        or not refs
                        or not all(isinstance(item, str) and len(item) >= 3 for item in refs)
                        or len(set(refs)) != len(refs)
                    ):
                        issues.append(
                            ValidationIssue(
                                f"{path}.implementation_evidence.refs",
                                "must be a non-empty unique list of evidence references",
                            )
                        )
                    elif (
                        layer_name == "G3"
                        and implementation_evidence.get("basis")
                        == "verified_physical_baseline"
                        and any(item.startswith("tasks/") for item in refs)
                    ):
                        issues.append(
                            ValidationIssue(
                                f"{path}.implementation_evidence.refs",
                                "task demand is not G3 implementation evidence",
                            )
                        )

    for field in ("assumptions", "unresolved_evidence_gaps", "evidence_refs"):
        if not isinstance(value.get(field), list) or not all(isinstance(item, str) for item in value.get(field, [])):
            issues.append(ValidationIssue(f"$.{field}", "must be a list of strings"))
    if issues:
        raise ArtifactSchemaError(issues)
    return value
