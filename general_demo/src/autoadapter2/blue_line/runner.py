"""Simplified, isolated Blue Line specification and suite-sealing gate."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Mapping
from weakref import WeakKeyDictionary

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash
from ..foundation.seals import create_seal, verify_seal
from ..generation.llm import JsonGenerator


BLUE_LINE_PROMPT = (
    "Produce a private validation specification only. Cover every sealed semantic "
    "capability, use catalog measurements, state false-pass guards, and never use "
    "candidate, Stage 2, Sandbox, Repair, or execution outcomes."
)
_FORBIDDEN_KEYS = ("candidate", "stage2", "sandbox", "repair", "validation_result", "demo_result")
_AUTHORIZATION_TOKEN = object()
_READY_BUNDLE_TOKEN = object()


class Stage2Authorization:
    """Opaque, non-sensitive authorization issued only by a READY Blue Line run."""

    __slots__ = ("__weakref__",)

    def __init__(self, token: object, design_hash: str, manifest_hash: str):
        if token is not _AUTHORIZATION_TOKEN:
            raise ContractError("Stage2Authorization is Framework-created only")
        _AUTHORIZATION_PAYLOADS[self] = (
            design_hash,
            content_hash(
                canonical_bytes({"design_hash": design_hash, "blue_line_manifest_hash": manifest_hash})
            ),
        )

    def __copy__(self) -> "Stage2Authorization":
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> "Stage2Authorization":
        return self

    def _verified_payload(self, design_hash: str) -> dict[str, Any]:
        payload = _AUTHORIZATION_PAYLOADS.get(self)
        if payload is None or payload[0] != design_hash:
            raise ContractError("Blue Line authorization does not bind this Capability Design")
        return {"status": "READY", "authorized": True, "receipt_id": payload[1]}


class BlueLineReadyBundle:
    """Framework-private proof that these exact artifacts came from one READY run."""

    __slots__ = ("__weakref__",)

    def __init__(
        self,
        token: object,
        design_hash: str,
        spec_hash: str,
        manifest_hash: str,
        suite_hash: str,
    ) -> None:
        if token is not _READY_BUNDLE_TOKEN:
            raise ContractError("BlueLineReadyBundle is Framework-created only")
        _READY_BUNDLE_PAYLOADS[self] = (design_hash, spec_hash, manifest_hash, suite_hash)

    def __copy__(self) -> "BlueLineReadyBundle":
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> "BlueLineReadyBundle":
        return self

    def _verified_payload(self, design_hash: str) -> dict[str, str]:
        payload = _READY_BUNDLE_PAYLOADS.get(self)
        if payload is None or payload[0] != design_hash:
            raise ContractError("Blue Line READY bundle does not bind this Capability Design")
        return {
            "design_hash": payload[0],
            "spec_hash": payload[1],
            "manifest_hash": payload[2],
            "suite_hash": payload[3],
        }


# The handles carry no authoritative fields themselves.  Their immutable payloads
# live only as long as the corresponding Framework-created handle does.
_AUTHORIZATION_PAYLOADS: WeakKeyDictionary[Stage2Authorization, tuple[str, str]] = WeakKeyDictionary()
_READY_BUNDLE_PAYLOADS: WeakKeyDictionary[
    BlueLineReadyBundle, tuple[str, str, str, str]
] = WeakKeyDictionary()


@dataclass(frozen=True)
class BlueLineResult:
    status: str
    validation_spec: dict[str, Any]
    spec_hash: str
    spec_seal: dict[str, Any]
    validation_suite: dict[str, Any] | None
    suite_hash: str | None
    suite_seal: dict[str, Any] | None
    manifest: dict[str, Any]
    manifest_hash: str
    manifest_seal: dict[str, Any]
    stage2_authorization: Stage2Authorization | None
    validation_authorization: BlueLineReadyBundle | None
    call_log: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, str], ...]


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _closed(value: Any, fields: set[str], location: str, issues: list[dict[str, str]]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        issues.append(_issue("TYPE", f"{location} must be an object"))
        return None
    missing = fields - set(value)
    extra = set(value) - fields
    if missing:
        issues.append(_issue("MISSING_FIELD", f"{location} missing {sorted(missing)}"))
    if extra:
        issues.append(_issue("EXTRA_FIELD", f"{location} has unexpected {sorted(extra)}"))
    return value


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _finite(value: Any, *, positive: bool = False) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and (value > 0 if positive else True)


def _forbidden_issues(value: Any, location: str = "$") -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if any(term in str(key).lower() for term in _FORBIDDEN_KEYS):
                issues.append(_issue("FORBIDDEN_CONTEXT", f"{location}.{key} is forbidden in Blue Line output"))
            issues.extend(_forbidden_issues(item, f"{location}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issues.extend(_forbidden_issues(item, f"{location}[{index}]"))
    return issues


def _standards(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records = snapshot.get("standards", snapshot.get("records"))
    if not isinstance(records, list):
        raise ContractError("standards snapshot needs a standards array")
    legacy_required = {
        "standard_id", "measurement_id", "metric", "comparator", "threshold_value",
        "dwell_s", "timeout_s", "aggregation",
    }
    result: dict[str, dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict) or not _text(item.get("standard_id")):
            raise ContractError("standards snapshot has an invalid standard")
        if item["standard_id"] in result:
            raise ContractError("standards snapshot needs unique standard_id values")
        if "criteria" not in item:
            if set(item) != legacy_required:
                raise ContractError("standards snapshot has an invalid standard")
            criterion = _validate_standard_criterion(
                dict(item) | {"criterion_id": item["standard_id"]},
                item["standard_id"],
            )
            result[item["standard_id"]] = {
                "standard_id": item["standard_id"],
                "_criteria": (criterion,),
                "_project_record": False,
                "_required_guard_ids": (),
                "_false_pass_requirements": (),
            }
            continue
        required = {
            "standard_id", "robot_configuration_id", "effect_id", "intended_use",
            "version", "record_status", "criteria", "required_guard_ids",
            "false_pass_requirements", "approval_lineage",
        }
        if not required.issubset(set(item)):
            raise ContractError("project validation standard is incomplete")
        if item.get("intended_use") != "capability_validation_b":
            raise ContractError("project validation standard intended_use is invalid")
        if not _text(item.get("version")):
            raise ContractError("project validation standard version is invalid")
        status = item.get("record_status")
        if status not in {"APPROVED", "HUMAN_APPROVED", "REVIEWED"}:
            raise ContractError("project validation standard is not approved")
        if not _text(item.get("robot_configuration_id")) or not _text(item.get("effect_id")):
            raise ContractError("project validation standard scope is incomplete")
        required_guards = item.get("required_guard_ids")
        if (
            not isinstance(required_guards, list)
            or not required_guards
            or not all(_text(guard_id) for guard_id in required_guards)
            or len(set(required_guards)) != len(required_guards)
        ):
            raise ContractError("project validation standard required guards are invalid")
        false_pass_requirements = item.get("false_pass_requirements")
        if (
            not isinstance(false_pass_requirements, list)
            or not false_pass_requirements
            or not all(_text(requirement) for requirement in false_pass_requirements)
            or len(set(false_pass_requirements)) != len(false_pass_requirements)
        ):
            raise ContractError("project validation standard needs false-pass requirements")
        approval_lineage = item.get("approval_lineage")
        if (
            not isinstance(approval_lineage, Mapping)
            or not approval_lineage
            or any(not _text(key) for key in approval_lineage)
            or any(value is None or (isinstance(value, str) and not value.strip()) for value in approval_lineage.values())
        ):
            raise ContractError("project validation standard approval lineage is invalid")
        criteria = item.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise ContractError("project validation standard needs criteria")
        normalized: list[dict[str, Any]] = []
        criterion_ids: set[str] = set()
        for criterion in criteria:
            if not isinstance(criterion, Mapping) or not _text(criterion.get("criterion_id")):
                raise ContractError("project validation standard criterion is invalid")
            if criterion["criterion_id"] in criterion_ids:
                raise ContractError("project validation standard criterion IDs must be unique")
            criterion_ids.add(criterion["criterion_id"])
            normalized.append(_validate_standard_criterion(criterion, criterion["criterion_id"]))
        result[item["standard_id"]] = {
            "standard_id": item["standard_id"],
            "robot_configuration_id": item["robot_configuration_id"],
            "effect_id": item["effect_id"],
            "intended_use": item["intended_use"],
            "_criteria": tuple(normalized),
            "_project_record": True,
            "_required_guard_ids": tuple(required_guards),
            "_false_pass_requirements": tuple(false_pass_requirements),
            "_approval_lineage": copy.deepcopy(dict(approval_lineage)),
        }
    if not result:
        raise ContractError("standards snapshot must not be empty")
    return result


def _validate_standard_criterion(value: Mapping[str, Any], identifier: str) -> dict[str, Any]:
    required = {
        "criterion_id", "measurement_id", "metric", "comparator", "threshold_value",
        "dwell_s", "timeout_s", "aggregation",
    }
    if not required.issubset(set(value)) or not _text(value.get("criterion_id")):
        raise ContractError(f"standard criterion {identifier} is incomplete")
    if (
        not _text(value.get("measurement_id"))
        or not _text(value.get("metric"))
        or value.get("comparator") not in {"<", "<=", ">", ">=", "=="}
        or not _finite(value.get("threshold_value"))
        or not _finite(value.get("dwell_s"))
        or value["dwell_s"] < 0
        or not _finite(value.get("timeout_s"), positive=True)
        or value.get("aggregation") not in {"ALL", "ANY", "MEAN"}
    ):
        raise ContractError(f"standard criterion {identifier} is invalid")
    return {
        key: value[key]
        for key in (
            "criterion_id", "measurement_id", "metric", "comparator", "threshold_value",
            "dwell_s", "timeout_s", "aggregation",
        )
    }


def _measurements(catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records = catalog.get("measurements")
    if not isinstance(records, list):
        raise ContractError("measurement catalog needs a measurements array")
    result: dict[str, dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict) or not _text(item.get("measurement_id")):
            raise ContractError("measurement catalog has an invalid measurement")
        identifier = item["measurement_id"]
        if identifier in result:
            raise ContractError("measurement catalog has duplicate measurement_id")
        if not all(_text(item.get(field)) for field in ("entity", "unit", "frame", "adapter_id", "truth_source")):
            raise ContractError("measurement catalog measurement is incomplete")
        metrics = item.get("metrics")
        if not isinstance(metrics, list) or not metrics or not all(_text(metric) for metric in metrics):
            raise ContractError("measurement catalog measurement needs metrics")
        result[identifier] = item
    if not result:
        raise ContractError("measurement catalog must not be empty")
    return result


def _guards(catalog: Mapping[str, Any]) -> set[str]:
    records = catalog.get("guards")
    if not isinstance(records, list) or not records:
        raise ContractError("measurement catalog needs a guards array")
    result: set[str] = set()
    for item in records:
        if not isinstance(item, dict) or set(item) != {"guard_id", "adapter_id"}:
            raise ContractError("measurement catalog has an invalid guard")
        if not _text(item.get("guard_id")) or not _text(item.get("adapter_id")) or item["guard_id"] in result:
            raise ContractError("measurement catalog guards need unique IDs and adapters")
        result.add(item["guard_id"])
    return result


def _policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    required = {"policy_id", "model_id", "prompt_id", "max_cases_per_capability", "repetitions"}
    if set(policy) != required or not all(_text(policy.get(name)) for name in ("policy_id", "model_id", "prompt_id")):
        raise ContractError("Blue Line policy must be a closed fixed-model policy")
    maximum = policy.get("max_cases_per_capability")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 8:
        raise ContractError("Blue Line policy max_cases_per_capability must be 1..8")
    repetitions = policy.get("repetitions")
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or not 1 <= repetitions <= 16:
        raise ContractError("Blue Line policy repetitions must be 1..16")
    return dict(policy)


def _criterion_from_spec(
    value: Any,
    location: str,
    issues: list[dict[str, str]],
    *,
    fallback_id: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        issues.append(_issue("CRITERION", f"{location} must be an object"))
        return None
    measurement = value.get("measurement")
    threshold = value.get("threshold")
    measurement_id = value.get("measurement_id")
    if measurement_id is None and isinstance(measurement, Mapping):
        measurement_id = measurement.get("measurement_id")
    comparator = value.get("comparator")
    if comparator is None and isinstance(threshold, Mapping):
        comparator = threshold.get("comparator")
    threshold_value = value.get("threshold_value")
    if threshold_value is None and isinstance(threshold, Mapping):
        threshold_value = threshold.get("value")
    criterion_id = value.get("criterion_id", fallback_id)
    criterion = {
        "criterion_id": criterion_id,
        "measurement_id": measurement_id,
        "metric": value.get("metric"),
        "comparator": comparator,
        "threshold_value": threshold_value,
        "dwell_s": value.get("dwell_s"),
        "timeout_s": value.get("timeout_s"),
        "aggregation": value.get("aggregation"),
        "guard_ids": copy.deepcopy(value.get("guard_ids", [])),
    }
    if isinstance(measurement, Mapping):
        criterion["measurement"] = copy.deepcopy(dict(measurement))
    if (
        not _text(criterion["criterion_id"])
        or not _text(criterion["measurement_id"])
        or not _text(criterion["metric"])
        or criterion["comparator"] not in {"<", "<=", ">", ">=", "=="}
        or not _finite(criterion["threshold_value"])
        or not _finite(criterion["dwell_s"])
        or criterion["dwell_s"] < 0
        or not _finite(criterion["timeout_s"], positive=True)
        or criterion["aggregation"] not in {"ALL", "ANY", "MEAN"}
    ):
        issues.append(_issue("CRITERION", f"{location} has invalid criterion fields"))
        return None
    guard_ids = criterion["guard_ids"]
    if guard_ids is None:
        guard_ids = []
        criterion["guard_ids"] = guard_ids
    if not isinstance(guard_ids, list) or any(not _text(guard_id) for guard_id in guard_ids):
        issues.append(_issue("FALSE_PASS_GUARD", f"{location}.guard_ids must be a string array"))
        criterion["guard_ids"] = []
    elif len(set(guard_ids)) != len(guard_ids):
        issues.append(_issue("FALSE_PASS_GUARD", f"{location}.guard_ids contains duplicates"))
    return criterion


def _false_pass_analysis(
    value: Any,
    location: str,
    issues: list[dict[str, str]],
    guards: set[str],
    *,
    required: bool,
) -> list[dict[str, str]]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        issues.append(_issue("FALSE_PASS_ANALYSIS", f"{location} must be a non-empty array"))
        return []
    result: list[dict[str, str]] = []
    seen_risks: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            issues.append(_issue("FALSE_PASS_ANALYSIS", f"{location}[{index}] must be an object"))
            continue
        risk_id = item.get("risk_id", item.get("risk"))
        guard_id = item.get("guard_id", item.get("guard"))
        if not _text(risk_id) or not _text(guard_id) or risk_id in seen_risks or guard_id not in guards:
            issues.append(_issue("FALSE_PASS_ANALYSIS", f"{location}[{index}] has an invalid risk-to-guard mapping"))
            continue
        seen_risks.add(risk_id)
        result.append({"risk_id": risk_id, "guard_id": guard_id})
    return result


def _normalized_capability_spec(
    entry: Any,
    location: str,
    issues: list[dict[str, str]],
    guards: set[str],
) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        issues.append(_issue("TYPE", f"{location} must be an object"))
        return None
    capability_id = entry.get("capability_id")
    if not _text(capability_id):
        issues.append(_issue("CAPABILITY_COVERAGE", f"{location}.capability_id is invalid"))
        return None
    is_multi = "criteria" in entry
    if is_multi:
        required = {"capability_id", "criteria", "cases", "lineage", "false_pass_analysis"}
        allowed = required | {"guard_ids"}
        missing = required - set(entry)
        extra = set(entry) - allowed
        if missing:
            issues.append(_issue("MISSING_FIELD", f"{location} missing {sorted(missing)}"))
        if extra:
            issues.append(_issue("EXTRA_FIELD", f"{location} has unexpected {sorted(extra)}"))
        raw_criteria = entry.get("criteria")
        if not isinstance(raw_criteria, list) or not raw_criteria:
            issues.append(_issue("CRITERION_COVERAGE", f"{location}.criteria must be a non-empty array"))
            criteria: list[dict[str, Any]] = []
        else:
            criteria = []
            criterion_ids: set[str] = set()
            for index, raw in enumerate(raw_criteria):
                criterion = _criterion_from_spec(raw, f"{location}.criteria[{index}]", issues)
                if criterion is None:
                    continue
                if criterion["criterion_id"] in criterion_ids:
                    issues.append(_issue("CRITERION_COVERAGE", f"{location} has duplicate criterion_id"))
                criterion_ids.add(criterion["criterion_id"])
                criteria.append(criterion)
    else:
        legacy_fields = {
            "capability_id", "measurement", "threshold", "dwell_s", "timeout_s",
            "aggregation", "metric", "guard_ids", "cases", "lineage",
        }
        if set(entry) != legacy_fields:
            missing = legacy_fields - set(entry)
            extra = set(entry) - legacy_fields
            if missing:
                issues.append(_issue("MISSING_FIELD", f"{location} missing {sorted(missing)}"))
            if extra:
                issues.append(_issue("EXTRA_FIELD", f"{location} has unexpected {sorted(extra)}"))
        criterion = _criterion_from_spec(
            entry,
            f"{location}.criterion",
            issues,
            fallback_id=f"{capability_id}:default",
        )
        criteria = [criterion] if criterion is not None else []
    analysis = _false_pass_analysis(
        entry.get("false_pass_analysis"),
        f"{location}.false_pass_analysis",
        issues,
        guards,
        required=is_multi,
    )
    top_guard_ids = entry.get("guard_ids")
    if top_guard_ids is not None:
        if not isinstance(top_guard_ids, list) or any(not _text(guard_id) for guard_id in top_guard_ids):
            issues.append(_issue("FALSE_PASS_GUARD", f"{location}.guard_ids is invalid"))
            top_guard_ids = []
        elif len(set(top_guard_ids)) != len(top_guard_ids):
            issues.append(_issue("FALSE_PASS_GUARD", f"{location}.guard_ids contains duplicates"))
    analysis_guards = [item["guard_id"] for item in analysis]
    default_guards = list(top_guard_ids or analysis_guards)
    for criterion in criteria:
        if not criterion["guard_ids"]:
            criterion["guard_ids"] = list(default_guards)
        unknown = set(criterion["guard_ids"]) - guards
        if unknown:
            issues.append(_issue("FALSE_PASS_GUARD", f"{location} has unknown guard IDs {sorted(unknown)}"))
    return {
        "capability_id": capability_id,
        "criteria": criteria,
        "cases": copy.deepcopy(entry.get("cases")),
        "lineage": copy.deepcopy(entry.get("lineage")),
        "false_pass_analysis": analysis,
        "is_multi": is_multi,
    }


def _criterion_signature(criterion: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: criterion.get(key)
        for key in (
            "measurement_id", "metric", "comparator", "threshold_value", "dwell_s",
            "timeout_s", "aggregation",
        )
    }


def check_validation_spec(
    spec: Mapping[str, Any],
    design: Mapping[str, Any],
    standards_snapshot: Mapping[str, Any],
    measurement_catalog: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Check structure, exact standards coverage, physical truth, and review disposition."""

    issues = _forbidden_issues(spec)
    artifact = _closed(
        spec,
        {"artifact_type", "schema_version", "design_hash", "capability_specs"},
        "blue_line_validation_spec",
        issues,
    )
    if artifact is None:
        return issues
    if artifact.get("artifact_type") != "blue_line_validation_spec" or artifact.get("schema_version") != "1.0.0":
        issues.append(_issue("ARTIFACT_IDENTITY", "invalid Blue Line spec identity"))
    expected_design_hash = content_hash(canonical_bytes(design))
    if artifact.get("design_hash") != expected_design_hash:
        issues.append(_issue("DESIGN_BINDING", "spec does not bind the sealed design"))
    standards = _standards(standards_snapshot)
    measurements = _measurements(measurement_catalog)
    guards = _guards(measurement_catalog)
    fixed_policy = _policy(policy)
    capabilities = design.get("capabilities")
    design_capabilities = {
        item.get("capability_id"): item
        for item in capabilities
        if isinstance(item, Mapping) and _text(item.get("capability_id"))
    } if isinstance(capabilities, list) else {}
    design_ids = set(design_capabilities)
    projection = design.get("robot_public_projection")
    projection = projection if isinstance(projection, Mapping) else {}
    project_standards = [item for item in standards.values() if item.get("_project_record")]
    entries = artifact.get("capability_specs")
    if not isinstance(entries, list):
        issues.append(_issue("CAPABILITY_COVERAGE", "capability_specs must be an array"))
        return issues
    seen: set[str] = set()
    normalized_entries: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        location = f"capability_specs[{index}]"
        normalized = _normalized_capability_spec(entry, location, issues, guards)
        if normalized is None:
            continue
        capability_id = normalized["capability_id"]
        if capability_id in seen:
            issues.append(_issue("CAPABILITY_COVERAGE", f"duplicate capability specification {capability_id}"))
        seen.add(capability_id)
        normalized_entries.append(normalized)
        capability = design_capabilities.get(capability_id)
        if capability is None:
            issues.append(_issue("CAPABILITY_COVERAGE", f"{location} is not in the sealed Design"))

        for criterion_index, criterion in enumerate(normalized["criteria"]):
            criterion_location = f"{location}.criteria[{criterion_index}]"
            measurement_id = criterion.get("measurement_id")
            catalogued = measurements.get(measurement_id)
            measurement_ref = criterion.get("measurement")
            if catalogued is None:
                issues.append(_issue("MEASUREMENT_REFERENCE", f"{criterion_location} does not resolve a catalog measurement"))
            else:
                if isinstance(measurement_ref, Mapping) and any(
                    measurement_ref.get(key) != catalogued.get(key)
                    for key in ("entity", "unit", "frame")
                ):
                    issues.append(_issue("MEASUREMENT_REFERENCE", f"{criterion_location} changes the catalog measurement scope"))
                if catalogued.get("truth_source") in {"sdk_receipt", "candidate_self_report"}:
                    issues.append(_issue("PHYSICAL_TRUTH", f"{criterion_location} uses an ineligible sole truth source"))
                if criterion.get("metric") not in catalogued.get("metrics", []):
                    issues.append(_issue("MEASUREMENT_METRIC", f"{criterion_location}.metric is not in the catalog"))
            unknown = set(criterion.get("guard_ids", [])) - guards
            if unknown:
                issues.append(_issue("FALSE_PASS_GUARD", f"{criterion_location} has unknown guard IDs {sorted(unknown)}"))

        cases = normalized.get("cases")
        if not isinstance(cases, list) or not cases or len(cases) > fixed_policy["max_cases_per_capability"]:
            issues.append(_issue("CASES", f"{location} violates case limits"))
        else:
            case_ids: set[str] = set()
            for case_index, case in enumerate(cases):
                checked = _closed(
                    case,
                    {"case_id", "initial_state", "inputs"},
                    f"{location}.cases[{case_index}]",
                    issues,
                )
                if checked and (
                    not _text(checked.get("case_id"))
                    or not isinstance(checked.get("initial_state"), dict)
                    or not isinstance(checked.get("inputs"), dict)
                ):
                    issues.append(_issue("CASES", f"{location}.cases[{case_index}] is invalid"))
                elif checked:
                    if checked["case_id"] in case_ids:
                        issues.append(_issue("CASES", f"{location} has duplicate case_id"))
                    case_ids.add(checked["case_id"])

        lineage = _closed(
            normalized.get("lineage"),
            {"kind", "standard_id", "material"},
            f"{location}.lineage",
            issues,
        )
        analysis = normalized["false_pass_analysis"]
        if normalized["is_multi"]:
            analysis_risks = {item["risk_id"] for item in analysis}
            analysis_guards = {item["guard_id"] for item in analysis}
            if not analysis:
                issues.append(_issue("FALSE_PASS_ANALYSIS", f"{location} needs explicit false-pass analysis"))
            if not any(criterion.get("guard_ids") for criterion in normalized["criteria"]):
                issues.append(_issue("FALSE_PASS_GUARD", f"{location} needs guards for every physical criterion"))
        else:
            analysis_risks = set()
            analysis_guards = set()

        if not isinstance(lineage, Mapping):
            continue
        kind = lineage.get("kind")
        standard_id = lineage.get("standard_id")
        material = lineage.get("material")
        if kind not in {"COPIED", "ADAPTED", "PROPOSED"} or not isinstance(material, bool):
            issues.append(_issue("LINEAGE", f"{location}.lineage is invalid"))
            continue
        if kind == "PROPOSED":
            if standard_id is not None:
                issues.append(_issue("LINEAGE", f"{location} proposed lineage must not claim a standard"))
            continue
        standard = standards.get(standard_id)
        if standard is None:
            issues.append(_issue("STANDARD_REFERENCE", f"{location} does not resolve a frozen standard"))
            continue
        if project_standards:
            if (
                not isinstance(capability, Mapping)
                or not _text(projection.get("robot_configuration_id"))
                or not _text(capability.get("effect"))
                or not standard.get("_project_record")
                or standard.get("robot_configuration_id") != projection.get("robot_configuration_id")
                or standard.get("effect_id") != capability.get("effect")
                or standard.get("intended_use") != "capability_validation_b"
            ):
                issues.append(_issue("STANDARD_SCOPE", f"{location} standard scope does not match robot and effect"))
        if kind != "COPIED":
            continue
        expected_criteria = list(standard["_criteria"])
        for expected in expected_criteria:
            catalogued = measurements.get(expected["measurement_id"])
            if catalogued is None:
                issues.append(_issue("STANDARD_MEASUREMENT_REFERENCE", f"{location} standard measurement is absent from the catalog"))
            elif expected["metric"] not in catalogued.get("metrics", []):
                issues.append(_issue("STANDARD_MEASUREMENT_METRIC", f"{location} standard metric is absent from the catalog"))
        missing_standard_guards = set(standard.get("_required_guard_ids", ())) - guards
        if missing_standard_guards:
            issues.append(_issue("STANDARD_GUARD_REFERENCE", f"{location} standard guards are absent from the catalog"))
        actual_by_id = {criterion.get("criterion_id"): criterion for criterion in normalized["criteria"]}
        if standard.get("_project_record"):
            expected_by_id = {criterion["criterion_id"]: criterion for criterion in expected_criteria}
            if set(actual_by_id) != set(expected_by_id):
                issues.append(_issue("COPIED_STANDARD_COVERAGE", f"{location} must reproduce every standard criterion"))
            for criterion_id, expected in expected_by_id.items():
                actual = actual_by_id.get(criterion_id)
                if actual is not None and _criterion_signature(actual) != _criterion_signature(expected):
                    issues.append(_issue("COPIED_STANDARD_BINDING", f"{location} changes criterion {criterion_id}"))
            supplied_guards = set()
            for criterion in normalized["criteria"]:
                supplied_guards.update(criterion.get("guard_ids", []))
            required_guards = set(standard.get("_required_guard_ids", ()))
            if supplied_guards != required_guards:
                issues.append(_issue("COPIED_STANDARD_GUARD_BINDING", f"{location} changes required standard guards"))
            if any(set(criterion.get("guard_ids", [])) != required_guards for criterion in normalized["criteria"]):
                issues.append(_issue("COPIED_STANDARD_GUARD_BINDING", f"{location} must bind every criterion to every required guard"))
            required_risks = set(standard.get("_false_pass_requirements", ()))
            analysis_by_risk = {item["risk_id"]: item["guard_id"] for item in analysis}
            risk_mapping_is_exact = (
                required_risks == required_guards
                and analysis_by_risk == {risk: risk for risk in required_risks}
            )
            if (
                analysis_risks != required_risks
                or analysis_guards != required_guards
                or (required_risks == required_guards and not risk_mapping_is_exact)
            ):
                issues.append(_issue("FALSE_PASS_ANALYSIS", f"{location} does not map every required risk to its catalog guard"))
        else:
            expected = expected_criteria[0]
            actual = normalized["criteria"][0] if normalized["criteria"] else {}
            if material is not False or _criterion_signature(actual) != _criterion_signature(expected):
                issues.append(_issue("COPIED_STANDARD_BINDING", f"{location} changes a copied frozen standard"))
    if seen != design_ids:
        issues.append(_issue("CAPABILITY_COVERAGE", "every sealed Design capability must have exactly one spec"))
    return issues


class BlueLineRunner:
    """One fixed injected LLM, three calls maximum, no execution capability."""

    def __init__(self, generator: JsonGenerator):
        self.generator = generator

    def run(
        self,
        capability_design: Mapping[str, Any],
        design_seal: Mapping[str, Any],
        standards_snapshot: Mapping[str, Any],
        measurement_catalog: Mapping[str, Any],
        policy: Mapping[str, Any],
    ) -> BlueLineResult:
        design = copy.deepcopy(dict(capability_design))
        design_hash = content_hash(canonical_bytes(design))
        try:
            if not verify_seal(dict(design_seal)) or design_seal.get("artifact_hash") != design_hash:
                raise ContractError("Blue Line requires a valid sealed Capability Design")
        except Exception as exc:
            if isinstance(exc, ContractError):
                raise
            raise ContractError("Blue Line requires a valid sealed Capability Design") from exc
        _standards(standards_snapshot)
        _measurements(measurement_catalog)
        _guards(measurement_catalog)
        fixed_policy = _policy(policy)
        base_inputs = {
            "capability_design": design,
            "standards_snapshot": copy.deepcopy(dict(standards_snapshot)),
            "measurement_catalog": copy.deepcopy(dict(measurement_catalog)),
            "policy": fixed_policy,
        }
        calls: list[dict[str, Any]] = []
        diagnostics: list[dict[str, str]] = []
        working: dict[str, Any] | None = None
        spec: dict[str, Any] = {}
        for attempt in range(3):
            inputs: dict[str, Any] = dict(base_inputs)
            if working is not None:
                inputs["working_spec"] = working
                inputs["diagnostics"] = diagnostics
            output = self.generator.generate_json("blue_line", BLUE_LINE_PROMPT, inputs)
            output_issues: list[dict[str, str]] = []
            if set(output) != {"capability_specs"}:
                output_issues.append(_issue("MODEL_OUTPUT_FIELDS", "Blue Line output may contain only capability_specs"))
            spec = {
                "artifact_type": "blue_line_validation_spec",
                "schema_version": "1.0.0",
                "design_hash": design_hash,
                "capability_specs": output.get("capability_specs"),
            }
            diagnostics = output_issues + check_validation_spec(spec, design, standards_snapshot, measurement_catalog, fixed_policy)
            calls.append({
                "call": attempt + 1,
                "stage": "blue_line",
                "input_hash": content_hash(canonical_bytes(inputs)),
                "output_hash": content_hash(canonical_bytes(output)),
                "diagnostics": copy.deepcopy(diagnostics),
            })
            if not diagnostics:
                break
            working = output
        spec_hash = content_hash(canonical_bytes(spec))
        spec_seal = create_seal("blue_line_validation_spec", spec_hash, [design_hash])
        review_required = not diagnostics and self._requires_review(spec)
        if diagnostics or review_required:
            reason = self._review_reason(diagnostics, review_required)
            manifest, manifest_hash, manifest_seal = self._manifest(
                design_hash, fixed_policy, standards_snapshot, measurement_catalog, calls,
                spec_hash, "NEEDS_REVIEW", reason, None,
            )
            return BlueLineResult(
                "NEEDS_REVIEW", spec, spec_hash, spec_seal, None, None, None,
                manifest, manifest_hash, manifest_seal, None, None,
                tuple(calls), tuple(diagnostics),
            )
        suite = self._compile_suite(
            spec,
            design_hash,
            spec_hash,
            fixed_policy["repetitions"],
            measurement_catalog,
        )
        suite_hash = content_hash(canonical_bytes(suite))
        suite_seal = create_seal("validation_b_suite", suite_hash, [spec_hash, design_hash])
        manifest, manifest_hash, manifest_seal = self._manifest(
            design_hash, fixed_policy, standards_snapshot, measurement_catalog, calls,
            spec_hash, "READY", None, suite_hash,
        )
        return BlueLineResult(
            "READY", spec, spec_hash, spec_seal, suite, suite_hash, suite_seal,
            manifest, manifest_hash, manifest_seal,
            Stage2Authorization(_AUTHORIZATION_TOKEN, design_hash, manifest_hash),
            BlueLineReadyBundle(
                _READY_BUNDLE_TOKEN, design_hash, spec_hash, manifest_hash, suite_hash
            ),
            tuple(calls), (),
        )

    @staticmethod
    def _requires_review(spec: Mapping[str, Any]) -> bool:
        return any(
            item.get("lineage", {}).get("kind") == "PROPOSED"
            or (item.get("lineage", {}).get("kind") == "ADAPTED" and item.get("lineage", {}).get("material") is True)
            for item in spec.get("capability_specs", [])
            if isinstance(item, dict)
        )

    @staticmethod
    def _review_reason(diagnostics: list[dict[str, str]], review_required: bool) -> str:
        if review_required:
            return "PENDING_STANDARD_REVIEW"
        if any(item["code"] in {"MEASUREMENT_REFERENCE", "STANDARD_REFERENCE"} for item in diagnostics):
            return "MISSING_STANDARD_OR_MEASUREMENT"
        return "EXHAUSTED_CORRECTION"

    @staticmethod
    def _compile_suite(
        spec: Mapping[str, Any],
        design_hash: str,
        spec_hash: str,
        repetitions: int,
        measurement_catalog: Mapping[str, Any],
    ) -> dict[str, Any]:
        measurements = _measurements(measurement_catalog)
        capability_cases: list[dict[str, Any]] = []
        for item in spec["capability_specs"]:
            if "criteria" not in item:
                capability_cases.append({
                    "capability_id": item["capability_id"],
                    "criterion_id": f"{item['capability_id']}:default",
                    "measurement": item["measurement"],
                    "threshold": item["threshold"],
                    "dwell_s": item["dwell_s"],
                    "timeout_s": item["timeout_s"],
                    "aggregation": item["aggregation"],
                    "metric": item["metric"],
                    "guard_ids": item["guard_ids"],
                    "cases": item["cases"],
                    "lineage": item["lineage"],
                })
                continue
            analysis = item.get("false_pass_analysis", [])
            analysis_guards = [
                mapping.get("guard_id")
                for mapping in analysis
                if isinstance(mapping, Mapping) and _text(mapping.get("guard_id"))
            ]
            compiled_criteria: list[dict[str, Any]] = []
            for criterion in item["criteria"]:
                measurement_id = criterion["measurement_id"]
                catalogued = measurements[measurement_id]
                measurement = criterion.get("measurement") or {
                    "measurement_id": measurement_id,
                    "entity": catalogued["entity"],
                    "unit": catalogued["unit"],
                    "frame": catalogued["frame"],
                }
                guard_ids = criterion.get("guard_ids") or analysis_guards
                compiled_criteria.append({
                    "criterion_id": criterion["criterion_id"],
                    "measurement_id": measurement_id,
                    "measurement": copy.deepcopy(dict(measurement)),
                    "metric": criterion["metric"],
                    "comparator": criterion["comparator"],
                    "threshold_value": criterion["threshold_value"],
                    "threshold": {
                        "comparator": criterion["comparator"],
                        "value": criterion["threshold_value"],
                    },
                    "dwell_s": criterion["dwell_s"],
                    "timeout_s": criterion["timeout_s"],
                    "aggregation": criterion["aggregation"],
                    "guard_ids": list(guard_ids),
                })
            capability_cases.append({
                "capability_id": item["capability_id"],
                "criteria": compiled_criteria,
                "cases": copy.deepcopy(item["cases"]),
                "lineage": copy.deepcopy(item["lineage"]),
                "false_pass_analysis": copy.deepcopy(item["false_pass_analysis"]),
            })
        return {
            "artifact_type": "validation_b_suite",
            "schema_version": "1.0.0",
            "design_hash": design_hash,
            "spec_hash": spec_hash,
            "repetitions": repetitions,
            "capability_cases": capability_cases,
        }

    @staticmethod
    def _manifest(
        design_hash: str,
        policy: Mapping[str, Any],
        standards_snapshot: Mapping[str, Any],
        measurement_catalog: Mapping[str, Any],
        calls: list[dict[str, Any]],
        spec_hash: str,
        status: str,
        reason_code: str | None,
        suite_hash: str | None,
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        manifest = {
            "artifact_type": "blue_line_manifest",
            "schema_version": "1.0.0",
            "design_hash": design_hash,
            "policy_hash": content_hash(canonical_bytes(policy)),
            "standards_snapshot_hash": content_hash(canonical_bytes(standards_snapshot)),
            "measurement_catalog_hash": content_hash(canonical_bytes(measurement_catalog)),
            "call_log": calls,
            "spec_hash": spec_hash,
            "status": status,
            "reason_code": reason_code,
            "suite_hash": suite_hash,
        }
        manifest_hash = content_hash(canonical_bytes(manifest))
        return manifest, manifest_hash, create_seal("blue_line_manifest", manifest_hash, [design_hash, spec_hash])
