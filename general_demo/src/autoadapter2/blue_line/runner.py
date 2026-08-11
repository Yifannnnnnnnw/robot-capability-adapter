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
    records = snapshot.get("standards")
    if not isinstance(records, list):
        raise ContractError("standards snapshot needs a standards array")
    required = {
        "standard_id", "measurement_id", "metric", "comparator", "threshold_value",
        "dwell_s", "timeout_s", "aggregation",
    }
    result: dict[str, dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict) or set(item) != required or not _text(item.get("standard_id")):
            raise ContractError("standards snapshot has an invalid standard")
        if item["standard_id"] in result:
            raise ContractError("standards snapshot needs unique standard_id values")
        if (
            not _text(item.get("measurement_id"))
            or not _text(item.get("metric"))
            or item.get("comparator") not in {"<", "<=", ">", ">=", "=="}
            or not _finite(item.get("threshold_value"))
            or not _finite(item.get("dwell_s"))
            or item["dwell_s"] < 0
            or not _finite(item.get("timeout_s"), positive=True)
            or item.get("aggregation") not in {"ALL", "ANY", "MEAN"}
        ):
            raise ContractError("standards snapshot standard is incomplete")
        result[item["standard_id"]] = item
    if not result:
        raise ContractError("standards snapshot must not be empty")
    return result


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


def check_validation_spec(
    spec: Mapping[str, Any],
    design: Mapping[str, Any],
    standards_snapshot: Mapping[str, Any],
    measurement_catalog: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Check closed structure, frozen references, coverage, and review disposition."""

    issues = _forbidden_issues(spec)
    artifact = _closed(spec, {"artifact_type", "schema_version", "design_hash", "capability_specs"}, "blue_line_validation_spec", issues)
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
    design_ids = {item.get("capability_id") for item in capabilities if isinstance(item, dict)} if isinstance(capabilities, list) else set()
    entries = artifact.get("capability_specs")
    if not isinstance(entries, list):
        issues.append(_issue("CAPABILITY_COVERAGE", "capability_specs must be an array"))
        return issues
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        fields = {
            "capability_id", "measurement", "threshold", "dwell_s", "timeout_s",
            "metric", "aggregation", "guard_ids", "cases", "lineage",
        }
        item = _closed(entry, fields, f"capability_specs[{index}]", issues)
        if item is None:
            continue
        capability_id = item.get("capability_id")
        if not _text(capability_id):
            issues.append(_issue("CAPABILITY_COVERAGE", f"capability_specs[{index}] has invalid capability_id"))
        elif capability_id in seen:
            issues.append(_issue("CAPABILITY_COVERAGE", f"duplicate capability specification {capability_id}"))
        else:
            seen.add(capability_id)
        measurement = _closed(item.get("measurement"), {"measurement_id", "entity", "unit", "frame"}, f"capability_specs[{index}].measurement", issues)
        if measurement:
            catalogued = measurements.get(measurement.get("measurement_id"))
            if catalogued is None or any(measurement.get(key) != catalogued.get(key) for key in ("entity", "unit", "frame")):
                issues.append(_issue("MEASUREMENT_REFERENCE", f"capability_specs[{index}] does not resolve a catalog measurement"))
            elif catalogued.get("truth_source") in {"sdk_receipt", "candidate_self_report"}:
                issues.append(_issue("PHYSICAL_TRUTH", f"capability_specs[{index}] uses an ineligible sole truth source"))
            elif item.get("metric") not in catalogued.get("metrics", []):
                issues.append(_issue("MEASUREMENT_METRIC", f"capability_specs[{index}] metric is not in the catalog"))
        if not _text(item.get("metric")):
            issues.append(_issue("MEASUREMENT_METRIC", f"capability_specs[{index}].metric is invalid"))
        threshold = _closed(item.get("threshold"), {"comparator", "value"}, f"capability_specs[{index}].threshold", issues)
        if threshold and (threshold.get("comparator") not in {"<", "<=", ">", ">=", "=="} or not _finite(threshold.get("value"))):
            issues.append(_issue("THRESHOLD", f"capability_specs[{index}] threshold is invalid"))
        if not _finite(item.get("dwell_s")) or item.get("dwell_s") < 0:
            issues.append(_issue("TEMPORAL_RULE", f"capability_specs[{index}].dwell_s is invalid"))
        if not _finite(item.get("timeout_s"), positive=True):
            issues.append(_issue("TEMPORAL_RULE", f"capability_specs[{index}].timeout_s is invalid"))
        if item.get("aggregation") not in {"ALL", "ANY", "MEAN"}:
            issues.append(_issue("AGGREGATION", f"capability_specs[{index}].aggregation is invalid"))
        guard_ids = item.get("guard_ids")
        if not isinstance(guard_ids, list) or not guard_ids or not all(_text(guard_id) for guard_id in guard_ids):
            issues.append(_issue("FALSE_PASS_GUARD", f"capability_specs[{index}] needs guard_ids"))
        elif len(set(guard_ids)) != len(guard_ids) or set(guard_ids) - guards:
            issues.append(_issue("FALSE_PASS_GUARD", f"capability_specs[{index}] has unknown or duplicate guard_ids"))
        cases = item.get("cases")
        if not isinstance(cases, list) or not cases or len(cases) > fixed_policy["max_cases_per_capability"]:
            issues.append(_issue("CASES", f"capability_specs[{index}] violates case limits"))
        else:
            case_ids: set[str] = set()
            for case_index, case in enumerate(cases):
                checked = _closed(case, {"case_id", "initial_state", "inputs"}, f"capability_specs[{index}].cases[{case_index}]", issues)
                if checked and (
                    not _text(checked.get("case_id"))
                    or not isinstance(checked.get("initial_state"), dict)
                    or not isinstance(checked.get("inputs"), dict)
                ):
                    issues.append(_issue("CASES", f"capability_specs[{index}].cases[{case_index}] is invalid"))
                elif checked:
                    if checked["case_id"] in case_ids:
                        issues.append(_issue("CASES", f"capability_specs[{index}] has duplicate case_id"))
                    case_ids.add(checked["case_id"])
        lineage = _closed(item.get("lineage"), {"kind", "standard_id", "material"}, f"capability_specs[{index}].lineage", issues)
        if lineage:
            kind = lineage.get("kind")
            standard_id = lineage.get("standard_id")
            material = lineage.get("material")
            if kind not in {"COPIED", "ADAPTED", "PROPOSED"} or not isinstance(material, bool):
                issues.append(_issue("LINEAGE", f"capability_specs[{index}] lineage is invalid"))
            elif kind in {"COPIED", "ADAPTED"} and standard_id not in standards:
                issues.append(_issue("STANDARD_REFERENCE", f"capability_specs[{index}] does not resolve a frozen standard"))
            elif kind == "PROPOSED" and standard_id is not None:
                issues.append(_issue("LINEAGE", f"capability_specs[{index}] proposed lineage must not claim a standard"))
            elif kind == "COPIED":
                standard = standards[standard_id]
                copied_values = {
                    "measurement_id": item.get("measurement", {}).get("measurement_id") if isinstance(item.get("measurement"), dict) else None,
                    "metric": item.get("metric"),
                    "comparator": item.get("threshold", {}).get("comparator") if isinstance(item.get("threshold"), dict) else None,
                    "threshold_value": item.get("threshold", {}).get("value") if isinstance(item.get("threshold"), dict) else None,
                    "dwell_s": item.get("dwell_s"),
                    "timeout_s": item.get("timeout_s"),
                    "aggregation": item.get("aggregation"),
                }
                if material is not False or any(copied_values[key] != standard[key] for key in copied_values):
                    issues.append(_issue("COPIED_STANDARD_BINDING", f"capability_specs[{index}] changes a copied frozen standard"))
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
        suite = self._compile_suite(spec, design_hash, spec_hash, fixed_policy["repetitions"])
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
    def _compile_suite(spec: Mapping[str, Any], design_hash: str, spec_hash: str, repetitions: int) -> dict[str, Any]:
        return {
            "artifact_type": "validation_b_suite",
            "schema_version": "1.0.0",
            "design_hash": design_hash,
            "spec_hash": spec_hash,
            "repetitions": repetitions,
            "capability_cases": [
                {
                    "capability_id": item["capability_id"],
                    "measurement": item["measurement"],
                    "threshold": item["threshold"],
                    "dwell_s": item["dwell_s"],
                    "timeout_s": item["timeout_s"],
                    "aggregation": item["aggregation"],
                    "metric": item["metric"],
                    "guard_ids": item["guard_ids"],
                    "cases": item["cases"],
                    "lineage": item["lineage"],
                }
                for item in spec["capability_specs"]
            ],
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
