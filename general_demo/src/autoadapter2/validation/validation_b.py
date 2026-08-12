"""Framework-owned Validation B evaluator over a sealed Blue Line lineage."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from ..evaluation import ClosedEvaluationVideo, verify_closed_evaluation_video
from ..blue_line import BlueLineReadyBundle
from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash, is_content_hash
from ..foundation.seals import create_seal, verify_seal
from .validation_a import ValidatedCandidateHandle


_SCHEMA_VERSION = "1.0.0"


class HarnessInfrastructureError(Exception):
    """The sole Harness condition that produces a B infrastructure outcome."""

    def __init__(
        self,
        message: str,
        *,
        video_evidence: ClosedEvaluationVideo | None = None,
    ) -> None:
        super().__init__(message)
        self.video_evidence = video_evidence


@dataclass(frozen=True)
class MeasurementSample:
    time_s: float
    value: float


@dataclass(frozen=True)
class HarnessCriterionMeasurement:
    """One criterion's private scalar trace from a shared execution episode."""

    measurement_id: str
    metric: str
    entity: str
    unit: str
    frame: str
    samples: tuple[MeasurementSample, ...]
    elapsed_s: float


@dataclass(frozen=True)
class HarnessMeasurement:
    measurement_id: str
    metric: str
    entity: str
    unit: str
    frame: str
    samples: tuple[MeasurementSample, ...]
    elapsed_s: float
    guard_results: Mapping[str, bool]
    sdk_route_evidence: Mapping[str, Any]
    video_evidence: ClosedEvaluationVideo
    criterion_measurements: Mapping[str, HarnessCriterionMeasurement] | None = None


@dataclass(frozen=True)
class HarnessInvocation:
    capability_id: str
    case_id: str
    inputs: Mapping[str, Any]
    initial_state: Mapping[str, Any]
    repetition: int
    measurement: Mapping[str, Any]
    metric: str
    threshold: Mapping[str, Any]
    dwell_s: float
    timeout_s: float
    aggregation: str
    guard_ids: tuple[str, ...]
    run_snapshot_hash: str
    candidate_source_hash: str
    suite_hash: str
    execution_attempt: int
    criterion_id: str = ""
    criterion_ids: tuple[str, ...] = ()
    criteria: tuple[Mapping[str, Any], ...] = ()


@runtime_checkable
class TypedHarnessSession(Protocol):
    def invoke(
        self,
        candidate: ValidatedCandidateHandle,
        capability_id: str,
        inputs: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def collect(self) -> HarnessMeasurement: ...


@runtime_checkable
class TypedHarness(Protocol):
    @property
    def config_hash(self) -> str: ...

    def open(self, invocation: HarnessInvocation) -> TypedHarnessSession: ...


@dataclass(frozen=True)
class ValidationContext:
    capability_design: Mapping[str, Any]
    design_seal: Mapping[str, Any]
    blue_line_spec: Mapping[str, Any]
    spec_seal: Mapping[str, Any]
    blue_line_manifest: Mapping[str, Any]
    manifest_seal: Mapping[str, Any]
    validation_suite: Mapping[str, Any]
    suite_seal: Mapping[str, Any]
    blue_line_ready_bundle: BlueLineReadyBundle
    run_snapshot: Mapping[str, Any]


@dataclass(frozen=True)
class FrozenValidationContext:
    context: ValidationContext
    design_hash: str
    spec_hash: str
    manifest_hash: str
    suite_hash: str
    run_snapshot_hash: str
    implementation_bundle_hash: str
    suite_entries: tuple[dict[str, Any], ...]
    repetitions: int


@dataclass(frozen=True)
class ValidationBResult:
    status: str
    report: dict[str, Any]
    report_hash: str
    report_seal: dict[str, Any]
    suite_hash: str
    overlay_hash: str
    run_snapshot_hash: str
    executions: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, str], ...]


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _finite(value: Any, *, positive: bool = False) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and (value > 0 if positive else True)


def _sealed_hash(value: Mapping[str, Any], seal: Mapping[str, Any], artifact_type: str) -> tuple[dict[str, Any], str]:
    frozen = copy.deepcopy(dict(value))
    digest = content_hash(canonical_bytes(frozen))
    try:
        if not verify_seal(dict(seal)) or seal.get("artifact_type") != artifact_type or seal.get("artifact_hash") != digest:
            raise ContractError(f"Validation B requires the exact sealed {artifact_type}")
    except Exception as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError(f"Validation B requires the exact sealed {artifact_type}") from exc
    return frozen, digest


def _rule_entries(suite: Mapping[str, Any], spec: Mapping[str, Any], design_ids: set[str]) -> tuple[tuple[dict[str, Any], ...], int]:
    if suite.get("artifact_type") != "validation_b_suite" or suite.get("schema_version") != _SCHEMA_VERSION:
        raise ContractError("Validation B suite identity is invalid")
    repetitions = suite.get("repetitions")
    entries = suite.get("capability_cases")
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1 or not isinstance(entries, list):
        raise ContractError("Validation B suite is incomplete")
    spec_entries = spec.get("capability_specs")
    if not isinstance(spec_entries, list):
        raise ContractError("Blue Line spec is incomplete")

    def criterion_rule(
        group: Mapping[str, Any],
        criterion: Mapping[str, Any],
        fallback_id: str,
    ) -> dict[str, Any]:
        measurement = criterion.get("measurement")
        if measurement is None and isinstance(group.get("measurement"), Mapping):
            measurement = group["measurement"]
        threshold = criterion.get("threshold")
        if threshold is None and isinstance(group.get("threshold"), Mapping):
            threshold = group["threshold"]
        criterion_id = criterion.get("criterion_id", fallback_id)
        guard_ids = criterion.get("guard_ids")
        if not guard_ids:
            guard_ids = group.get("guard_ids")
        if not guard_ids:
            guard_ids = [
                item.get("guard_id")
                for item in group.get("false_pass_analysis", [])
                if isinstance(item, Mapping) and _text(item.get("guard_id"))
            ]
        return {
            "capability_id": group.get("capability_id"),
            "criterion_id": criterion_id,
            "measurement": copy.deepcopy(dict(measurement)) if isinstance(measurement, Mapping) else measurement,
            "measurement_id": criterion.get(
                "measurement_id",
                measurement.get("measurement_id") if isinstance(measurement, Mapping) else None,
            ),
            "metric": criterion.get("metric", group.get("metric")),
            "threshold": copy.deepcopy(dict(threshold)) if isinstance(threshold, Mapping) else threshold,
            "comparator": criterion.get(
                "comparator",
                threshold.get("comparator") if isinstance(threshold, Mapping) else None,
            ),
            "threshold_value": criterion.get(
                "threshold_value",
                threshold.get("value") if isinstance(threshold, Mapping) else None,
            ),
            "dwell_s": criterion.get("dwell_s", group.get("dwell_s")),
            "timeout_s": criterion.get("timeout_s", group.get("timeout_s")),
            "aggregation": criterion.get("aggregation", group.get("aggregation")),
            "guard_ids": copy.deepcopy(guard_ids),
            "cases": copy.deepcopy(group.get("cases")),
            "lineage": copy.deepcopy(group.get("lineage")),
            "false_pass_analysis": copy.deepcopy(group.get("false_pass_analysis", [])),
        }

    def spec_rules() -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for entry in spec_entries:
            if not isinstance(entry, Mapping) or not _text(entry.get("capability_id")):
                raise ContractError("Blue Line spec has an invalid capability entry")
            capability_id = entry["capability_id"]
            if isinstance(entry.get("criteria"), list):
                for criterion in entry["criteria"]:
                    if not isinstance(criterion, Mapping):
                        raise ContractError("Blue Line spec has an invalid criterion")
                    result.append(criterion_rule(entry, criterion, f"{capability_id}:default"))
            else:
                result.append(criterion_rule(entry, entry, f"{capability_id}:default"))
        return result

    def checked_cases(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise ContractError("Validation B suite has no cases")
        result: list[dict[str, Any]] = []
        case_ids: set[str] = set()
        for case in value:
            if (
                not isinstance(case, Mapping)
                or set(case) != {"case_id", "initial_state", "inputs"}
                or not _text(case.get("case_id"))
                or not isinstance(case.get("initial_state"), Mapping)
                or not isinstance(case.get("inputs"), Mapping)
                or case["case_id"] in case_ids
            ):
                raise ContractError("Validation B suite has an invalid case")
            case_ids.add(case["case_id"])
            result.append(copy.deepcopy(dict(case)))
        return result

    def validate_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
        measurement = rule.get("measurement")
        threshold = rule.get("threshold")
        guards = rule.get("guard_ids")
        if (
            not _text(rule.get("criterion_id"))
            or not isinstance(measurement, Mapping)
            or set(measurement) != {"measurement_id", "entity", "unit", "frame"}
            or not all(_text(measurement.get(key)) for key in measurement)
            or rule.get("measurement_id") != measurement.get("measurement_id")
            or not _text(rule.get("metric"))
            or not isinstance(threshold, Mapping)
            or set(threshold) != {"comparator", "value"}
            or threshold.get("comparator") not in {"<", "<=", ">", ">=", "=="}
            or not _finite(threshold.get("value"))
            or rule.get("comparator") != threshold.get("comparator")
            or rule.get("threshold_value") != threshold.get("value")
            or not _finite(rule.get("dwell_s"))
            or rule.get("dwell_s") < 0
            or not _finite(rule.get("timeout_s"), positive=True)
            or rule.get("aggregation") not in {"ALL", "ANY", "MEAN"}
            or not isinstance(guards, list)
            or not guards
            or not all(_text(item) for item in guards)
            or len(set(guards)) != len(guards)
        ):
            raise ContractError("Validation B suite has an incomplete evaluation rule")
        checked = copy.deepcopy(dict(rule))
        checked["cases"] = checked_cases(rule.get("cases"))
        return checked

    spec_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    spec_capabilities: set[str] = set()
    for rule in spec_rules():
        key = (rule["capability_id"], rule["criterion_id"])
        if key in spec_by_key:
            raise ContractError("Blue Line spec has duplicate criterion coverage")
        spec_by_key[key] = rule
        spec_capabilities.add(rule["capability_id"])

    suite_rules: list[dict[str, Any]] = []
    suite_keys: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, Mapping) or not _text(entry.get("capability_id")):
            raise ContractError("Validation B suite has an invalid capability rule")
        capability_id = entry["capability_id"]
        entry_rules: list[dict[str, Any]] = []
        if "criteria" in entry:
            allowed = {"capability_id", "criteria", "cases", "lineage", "false_pass_analysis", "guard_ids"}
            required = allowed - {"guard_ids"}
            if (
                not required.issubset(set(entry))
                or set(entry) - allowed
                or not isinstance(entry.get("criteria"), list)
                or not entry["criteria"]
            ):
                raise ContractError("Validation B suite has an invalid criterion list")
            for criterion in entry["criteria"]:
                if not isinstance(criterion, Mapping):
                    raise ContractError("Validation B suite has an invalid criterion")
                rule = validate_rule(criterion_rule(entry, criterion, f"{capability_id}:default"))
                rule["false_pass_analysis"] = copy.deepcopy(entry["false_pass_analysis"])
                rule["lineage"] = copy.deepcopy(entry["lineage"])
                entry_rules.append(rule)
        else:
            required = {
                "capability_id", "criterion_id", "measurement", "threshold", "dwell_s", "timeout_s",
                "aggregation", "metric", "guard_ids", "cases", "lineage",
            }
            legacy_required = required - {"criterion_id"}
            if set(entry) not in (required, legacy_required):
                raise ContractError("Validation B suite has an invalid capability rule")
            rule = validate_rule(criterion_rule(entry, entry, f"{capability_id}:default"))
            entry_rules.append(rule)
        suite_rules.extend(entry_rules)
        for rule in entry_rules:
            key = (rule["capability_id"], rule["criterion_id"])
            if key in suite_keys:
                raise ContractError("Validation B suite has duplicate criterion coverage")
            suite_keys.add(key)

    if spec_capabilities != design_ids or {key[0] for key in suite_keys} != design_ids:
        raise ContractError("Validation B must cover every sealed Design capability exactly once")
    if suite_keys != set(spec_by_key):
        raise ContractError("Validation B must cover every sealed criterion exactly once")
    for rule in suite_rules:
        key = (rule["capability_id"], rule["criterion_id"])
        expected = spec_by_key[key]
        for field in (
            "measurement_id", "metric", "comparator", "threshold_value", "dwell_s", "timeout_s",
            "aggregation", "guard_ids", "cases", "lineage", "false_pass_analysis",
        ):
            expected_value = expected.get(field)
            actual_value = rule.get(field)
            if field == "measurement_id" and expected_value is None:
                expected_value = expected.get("measurement", {}).get("measurement_id") if isinstance(expected.get("measurement"), Mapping) else None
            if field == "false_pass_analysis" and expected_value is None:
                expected_value = []
            if field == "cases":
                expected_value = checked_cases(expected_value)
            if actual_value != expected_value:
                raise ContractError("Validation B suite rule does not match its sealed Blue Line spec")
        expected_measurement = expected.get("measurement")
        if isinstance(expected_measurement, Mapping):
            for field in ("measurement_id", "entity", "unit", "frame"):
                if field in expected_measurement and rule["measurement"].get(field) != expected_measurement[field]:
                    raise ContractError("Validation B suite measurement does not match its sealed Blue Line spec")
    return tuple(suite_rules), repetitions


def freeze_validation_context(context: ValidationContext) -> FrozenValidationContext:
    if not isinstance(context, ValidationContext):
        raise ContractError("Validation B requires a Framework ValidationContext")
    design, design_hash = _sealed_hash(context.capability_design, context.design_seal, "capability_design")
    spec, spec_hash = _sealed_hash(context.blue_line_spec, context.spec_seal, "blue_line_validation_spec")
    manifest, manifest_hash = _sealed_hash(context.blue_line_manifest, context.manifest_seal, "blue_line_manifest")
    suite, suite_hash = _sealed_hash(context.validation_suite, context.suite_seal, "validation_b_suite")
    if not isinstance(context.blue_line_ready_bundle, BlueLineReadyBundle):
        raise ContractError("Validation B requires the Framework Blue Line READY bundle")
    ready_payload = context.blue_line_ready_bundle._verified_payload(design_hash)
    if (
        spec.get("design_hash") != design_hash
        or manifest.get("status") != "READY"
        or manifest.get("design_hash") != design_hash
        or manifest.get("spec_hash") != spec_hash
        or manifest.get("suite_hash") != suite_hash
        or suite.get("design_hash") != design_hash
        or suite.get("spec_hash") != spec_hash
        or not {design_hash, spec_hash}.issubset(set(context.suite_seal.get("parents", [])))
        or design_hash not in set(context.spec_seal.get("parents", []))
        or not {design_hash, spec_hash}.issubset(set(context.manifest_seal.get("parents", [])))
        or ready_payload != {
            "design_hash": design_hash,
            "spec_hash": spec_hash,
            "manifest_hash": manifest_hash,
            "suite_hash": suite_hash,
        }
    ):
        raise ContractError("Validation B Blue Line lineage is not sealed and READY")
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list):
        raise ContractError("Validation B Design is invalid")
    design_ids = {item.get("capability_id") for item in capabilities if isinstance(item, Mapping) and _text(item.get("capability_id"))}
    entries, repetitions = _rule_entries(suite, spec, design_ids)
    snapshot = copy.deepcopy(dict(context.run_snapshot))
    expected_snapshot = {
        "artifact_type": "validation_run_snapshot",
        "schema_version": _SCHEMA_VERSION,
        "design_hash": design_hash,
        "blue_line_spec_hash": spec_hash,
        "blue_line_manifest_hash": manifest_hash,
        "suite_hash": suite_hash,
        "implementation_bundle_hash": snapshot.get("implementation_bundle_hash"),
        "rim_hash": snapshot.get("rim_hash"),
        "sdk_entry_hash": snapshot.get("sdk_entry_hash"),
        "runtime_hash": snapshot.get("runtime_hash"),
        "harness_config_hash": snapshot.get("harness_config_hash"),
    }
    if snapshot != expected_snapshot or not all(is_content_hash(snapshot[key]) for key in ("implementation_bundle_hash", "rim_hash", "sdk_entry_hash", "runtime_hash", "harness_config_hash")):
        raise ContractError("Validation B run_snapshot must freeze lineage, RIM, SDK, runtime, and Harness")
    frozen_context = ValidationContext(
        capability_design=design,
        design_seal=copy.deepcopy(dict(context.design_seal)),
        blue_line_spec=spec,
        spec_seal=copy.deepcopy(dict(context.spec_seal)),
        blue_line_manifest=manifest,
        manifest_seal=copy.deepcopy(dict(context.manifest_seal)),
        validation_suite=suite,
        suite_seal=copy.deepcopy(dict(context.suite_seal)),
        blue_line_ready_bundle=context.blue_line_ready_bundle,
        run_snapshot=snapshot,
    )
    return FrozenValidationContext(
        context=frozen_context,
        design_hash=design_hash,
        spec_hash=spec_hash,
        manifest_hash=manifest_hash,
        suite_hash=suite_hash,
        run_snapshot_hash=content_hash(canonical_bytes(snapshot)),
        implementation_bundle_hash=snapshot["implementation_bundle_hash"],
        suite_entries=entries,
        repetitions=repetitions,
    )


def _verify_handle(handle: ValidatedCandidateHandle, frozen: FrozenValidationContext) -> tuple[dict[str, Any], str]:
    if not isinstance(handle, ValidatedCandidateHandle):
        raise ContractError("Validation B only accepts an A-produced overlay-bound candidate handle")
    payload = handle._framework_payload_snapshot()
    binding_overlay = payload.overlay
    if binding_overlay is None:
        raise ContractError("Validation B only accepts an A-produced overlay-bound candidate handle")
    if (
        content_hash(payload.source.encode("utf-8")) != payload.source_hash
        or not all(
            is_content_hash(value)
            for value in (
                payload.source_hash,
                payload.implementation_manifest_hash,
                payload.implementation_bundle_hash,
                payload.validation_a_report_hash,
                frozen.suite_hash,
            )
        )
    ):
        raise ContractError("Validation B candidate source lineage is invalid")
    overlay = copy.deepcopy(binding_overlay.overlay)
    overlay_hash = content_hash(canonical_bytes(overlay))
    try:
        expected_parents = sorted({
            frozen.suite_hash,
            payload.source_hash,
            payload.implementation_manifest_hash,
            payload.implementation_bundle_hash,
            payload.validation_a_report_hash,
        })
        if (
            binding_overlay.overlay_hash != overlay_hash
            or not verify_seal(dict(binding_overlay.seal))
            or binding_overlay.seal.get("artifact_type") != "validation_execution_binding_overlay"
            or binding_overlay.seal.get("artifact_hash") != overlay_hash
            or binding_overlay.seal.get("parents") != expected_parents
        ):
            raise ContractError("Validation B candidate overlay seal is invalid")
    except Exception as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Validation B candidate overlay seal is invalid") from exc
    expected = {
        "artifact_type", "schema_version", "suite_hash", "source_hash", "implementation_manifest_hash",
        "implementation_bundle_hash", "validation_a_report_hash", "capability_bindings",
    }
    if (
        set(overlay) != expected
        or overlay.get("artifact_type") != "validation_execution_binding_overlay"
        or overlay.get("schema_version") != _SCHEMA_VERSION
        or overlay.get("suite_hash") != frozen.suite_hash
        or overlay.get("source_hash") != payload.source_hash
        or overlay.get("implementation_manifest_hash") != payload.implementation_manifest_hash
        or overlay.get("implementation_bundle_hash") != payload.implementation_bundle_hash
        or overlay.get("implementation_bundle_hash") != frozen.implementation_bundle_hash
        or overlay.get("validation_a_report_hash") != payload.validation_a_report_hash
    ):
        raise ContractError(
            "Validation B candidate overlay does not bind this exact source, manifest, "
            "Validation A report, bundle, and suite"
        )
    expected_symbols = {
        capability_id: payload.contracts[capability_id]["function_name"]
        for capability_id in payload.contracts
    }
    actual_symbols: dict[str, str] = {}
    bindings = overlay.get("capability_bindings")
    if not isinstance(bindings, list):
        raise ContractError("Validation B candidate overlay is invalid")
    for binding in bindings:
        if not isinstance(binding, Mapping) or set(binding) != {"capability_id", "function_name"} or not _text(binding.get("capability_id")) or not _text(binding.get("function_name")) or binding["capability_id"] in actual_symbols:
            raise ContractError("Validation B candidate overlay is invalid")
        actual_symbols[binding["capability_id"]] = binding["function_name"]
    if actual_symbols != expected_symbols:
        raise ContractError("Validation B candidate overlay no longer matches the A-verified module")
    return overlay, overlay_hash


def _comparison(value: float, comparator: str, threshold: float) -> bool:
    return {
        "<": value < threshold,
        "<=": value <= threshold,
        ">": value > threshold,
        ">=": value >= threshold,
        "==": value == threshold,
    }[comparator]


def _route_evidence_is_valid(
    route: Any,
    snapshot: Mapping[str, Any],
) -> bool:
    if not isinstance(route, Mapping):
        return False
    expected = {
        "verified": True,
        "rim_hash": snapshot["rim_hash"],
        "sdk_entry_hash": snapshot["sdk_entry_hash"],
        "runtime_hash": snapshot["runtime_hash"],
    }
    if any(route.get(key) != value for key, value in expected.items()):
        return False
    detail = route.get("route_evidence")
    if not isinstance(detail, Mapping) or not detail:
        return False
    try:
        if route.get("route_evidence_hash") != content_hash(canonical_bytes(detail)):
            return False
    except Exception:
        return False
    detail_expected = {
        "rim_hash": snapshot["rim_hash"],
        "integration_manifest_hash": snapshot["rim_hash"],
        "sdk_entry_hash": snapshot["sdk_entry_hash"],
        "runtime_hash": snapshot["runtime_hash"],
    }
    if any(key in detail and detail[key] != value for key, value in detail_expected.items()):
        return False
    required_detail = {
        "candidate_invocation_observed",
        "accepted_command_count",
        "simulation_time_progressed",
        "state_route_observed",
        "verified",
    }
    if not required_detail.issubset(detail):
        return False
    if any(
        not isinstance(detail[key], bool) or detail[key] is not True
        for key in (
            "candidate_invocation_observed",
            "simulation_time_progressed",
            "state_route_observed",
            "verified",
        )
    ):
        return False
    accepted_command_count = detail["accepted_command_count"]
    if (
        isinstance(accepted_command_count, bool)
        or not isinstance(accepted_command_count, (int, float))
        or not math.isfinite(accepted_command_count)
        or accepted_command_count <= 0
    ):
        return False
    if "physics_progress" in detail and detail["physics_progress"] is not True:
        return False
    is_so_route = any(
        str(detail.get(key, "")).startswith("so-arm101")
        for key in ("robot_model_id", "robot_configuration_id")
    ) or "present_position_qpos_consistent" in detail
    if is_so_route:
        max_error = detail.get("present_position_qpos_max_error_ticks")
        if (
            detail.get("present_position_qpos_consistent") is not True
            or isinstance(max_error, bool)
            or not isinstance(max_error, (int, float))
            or not math.isfinite(max_error)
            or max_error > 1
        ):
            return False
    if detail.get("verified") is not True:
        return False
    return True


_CANONICAL_GUARD_ALIASES: dict[str, tuple[str, ...]] = {
    "sdk_receipt_not_completion": ("sdk-route-verified",),
    "candidate_self_report_not_truth": ("trusted-external-verdict",),
    "finite_fresh_physical_state": ("finite-physical-state",),
    "no_forbidden_collision_or_safety_violation": ("so-safety-gate",),
    "no_body_or_head_ground_contact": (
        "no-body-or-head-ground-contact",
        "go2-ground-contact-free",
        "ground-contact-free",
    ),
}


def _measurement_for_criterion(
    observation: HarnessMeasurement,
    rule: Mapping[str, Any],
) -> HarnessCriterionMeasurement | None:
    criterion_measurements = observation.criterion_measurements
    if isinstance(criterion_measurements, Mapping):
        measurement = criterion_measurements.get(rule["criterion_id"])
        return measurement if isinstance(measurement, HarnessCriterionMeasurement) else None
    return HarnessCriterionMeasurement(
        measurement_id=observation.measurement_id,
        metric=observation.metric,
        entity=observation.entity,
        unit=observation.unit,
        frame=observation.frame,
        samples=observation.samples,
        elapsed_s=observation.elapsed_s,
    )


def _guard_passes(
    guard_id: str,
    guard_results: Mapping[str, bool],
    *,
    measurement_reference_ok: bool,
) -> bool:
    if guard_id == "entity_unit_frame_match":
        if guard_id in guard_results:
            return guard_results[guard_id] is True and measurement_reference_ok
        return measurement_reference_ok
    if guard_id in guard_results:
        return guard_results[guard_id] is True
    aliases = _CANONICAL_GUARD_ALIASES.get(guard_id, ())
    return bool(aliases) and all(guard_results.get(alias) is True for alias in aliases)


def _evaluate_criterion(
    observation: HarnessMeasurement,
    rule: Mapping[str, Any],
) -> tuple[bool, list[str], bool]:
    failures: list[str] = []
    measurement = rule["measurement"]
    criterion_measurement = _measurement_for_criterion(observation, rule)
    if (
        criterion_measurement is None
        or criterion_measurement.measurement_id != measurement["measurement_id"]
        or criterion_measurement.metric != rule["metric"]
        or criterion_measurement.entity != measurement["entity"]
        or criterion_measurement.unit != measurement["unit"]
        or criterion_measurement.frame != measurement["frame"]
    ):
        failures.append("MEASUREMENT_REFERENCE")
    measurement_reference_ok = "MEASUREMENT_REFERENCE" not in failures
    if criterion_measurement is None or not _finite(criterion_measurement.elapsed_s) or criterion_measurement.elapsed_s > rule["timeout_s"]:
        failures.append("TIMEOUT")
    samples = () if criterion_measurement is None else criterion_measurement.samples
    if not isinstance(samples, tuple) or not samples:
        failures.append("MEASUREMENT_SAMPLES")
        checked: list[MeasurementSample] = []
    else:
        checked = list(samples)
        if any(
            not isinstance(sample, MeasurementSample)
            or not _finite(sample.time_s)
            or sample.time_s < 0
            or not _finite(sample.value)
            for sample in checked
        ):
            failures.append("MEASUREMENT_SAMPLES")
        checked.sort(key=lambda item: item.time_s)
    if checked:
        values = [sample.value for sample in checked]
        threshold = rule["threshold"]
        comparisons = [_comparison(value, threshold["comparator"], threshold["value"]) for value in values]
        aggregation = rule["aggregation"]
        if aggregation == "ALL":
            aggregate_ok = all(comparisons)
        elif aggregation == "ANY":
            aggregate_ok = any(comparisons)
        else:
            aggregate_ok = _comparison(sum(values) / len(values), threshold["comparator"], threshold["value"])
        passing_times = [sample.time_s for sample, passed in zip(checked, comparisons, strict=True) if passed]
        dwell_ok = rule["dwell_s"] == 0 or (len(passing_times) >= 2 and passing_times[-1] - passing_times[0] >= rule["dwell_s"])
        if not aggregate_ok:
            failures.append("THRESHOLD")
        if not dwell_ok:
            failures.append("DWELL")
    return not failures, failures, measurement_reference_ok


def _evaluate_episode(
    observation: HarnessMeasurement,
    rules: tuple[Mapping[str, Any], ...],
    snapshot: Mapping[str, Any],
) -> tuple[bool, list[str], list[dict[str, Any]]]:
    route_ok = _route_evidence_is_valid(observation.sdk_route_evidence, snapshot)
    guards = observation.guard_results if isinstance(observation.guard_results, Mapping) else {}
    overall_failures: list[str] = []
    criterion_results: list[dict[str, Any]] = []
    for rule in rules:
        _passed, failures, measurement_reference_ok = _evaluate_criterion(observation, rule)
        if any(
            not _guard_passes(
                guard_id,
                guards,
                measurement_reference_ok=measurement_reference_ok,
            )
            for guard_id in rule["guard_ids"]
        ):
            failures.append("FALSE_PASS_GUARD")
        if not route_ok:
            failures.append("SDK_ROUTE_EVIDENCE")
        criterion_failures = list(dict.fromkeys(failures))
        criterion_results.append({
            "criterion_id": rule["criterion_id"],
            "verdict": "PASS" if not criterion_failures else "FAIL",
            "failure_codes": criterion_failures,
        })
        for code in criterion_failures:
            if code not in overall_failures:
                overall_failures.append(code)
    return not overall_failures, overall_failures, criterion_results


def _evaluate_measurement(
    observation: HarnessMeasurement,
    rule: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    passed, failures, _criterion_results = _evaluate_episode(observation, (rule,), snapshot)
    return passed, failures


def _verify_video_evidence(
    video: Any,
    invocation: HarnessInvocation,
) -> None:
    if not isinstance(video, ClosedEvaluationVideo):
        raise HarnessInfrastructureError("Validation B video evidence is missing")
    if not verify_closed_evaluation_video(video):
        raise HarnessInfrastructureError(
            "Validation B video evidence was not issued by the Framework recorder",
            video_evidence=video,
        )
    if (
        content_hash(video.manifest_bytes) != video.manifest_content_hash
        or video.completion_status != "COMPLETE"
        or video.execution_disposition != "EVIDENCE_COMPLETE"
        or video.media_content_hash is None
        or video.handle is None
        or video.failures
    ):
        raise HarnessInfrastructureError(
            "Validation B video evidence is incomplete", video_evidence=video
        )
    try:
        manifest = video.manifest
    except Exception as exc:
        raise HarnessInfrastructureError(
            "Validation B video manifest is invalid", video_evidence=video
        ) from exc
    media = manifest.get("media")
    expected_bindings = {
        "phase": "VALIDATION_B",
        "capability_id": invocation.capability_id,
        "criterion_id": invocation.criterion_id,
        "case_id": invocation.case_id,
        "repetition": str(invocation.repetition),
        "run_snapshot_hash": invocation.run_snapshot_hash,
        "candidate_source_hash": invocation.candidate_source_hash,
        "suite_hash": invocation.suite_hash,
        "execution_attempt": str(invocation.execution_attempt),
    }
    criterion_ids = invocation.criterion_ids or tuple(
        item["criterion_id"]
        for item in invocation.criteria
        if isinstance(item, Mapping) and isinstance(item.get("criterion_id"), str)
    )
    if len(criterion_ids) > 1:
        expected_bindings["criterion_ids"] = "|".join(criterion_ids)
    bindings = manifest.get("bindings")
    if (
        manifest.get("manifest_type") != "framework_evaluation_video"
        or manifest.get("closed") is not True
        or not isinstance(media, Mapping)
        or media.get("content_hash") != video.media_content_hash
        or not isinstance(bindings, Mapping)
        or any(bindings.get(key) != value for key, value in expected_bindings.items())
    ):
        raise HarnessInfrastructureError(
            "Validation B video lineage is invalid", video_evidence=video
        )


def _episode_groups(
    suite_entries: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    groups: list[dict[str, Any]] = []
    by_capability: dict[str, dict[str, Any]] = {}
    for rule in suite_entries:
        capability_id = rule["capability_id"]
        group = by_capability.get(capability_id)
        if group is None:
            group = {
                "capability_id": capability_id,
                "rules": [],
                "cases": copy.deepcopy(rule["cases"]),
            }
            by_capability[capability_id] = group
            groups.append(group)
        elif group["cases"] != rule["cases"]:
            raise ContractError(
                "Validation B criteria for one capability must share the same cases"
            )
        group["rules"].append(rule)
    return tuple(groups)


class ValidationBRunner:
    """Production B accepts only a narrow typed Harness, never a verdict callback."""

    def __init__(self, harness: TypedHarness):
        if not isinstance(harness, TypedHarness):
            raise ContractError("Validation B requires a TypedHarness, not a verdict callback")
        self._harness = harness
        self._execution_attempt = 0

    def run(self, candidate: ValidatedCandidateHandle, context: ValidationContext) -> ValidationBResult:
        frozen = freeze_validation_context(context)
        if self._harness.config_hash != frozen.context.run_snapshot["harness_config_hash"]:
            raise ContractError("Validation B Harness config does not match the frozen run_snapshot")
        _overlay, overlay_hash = _verify_handle(candidate, frozen)
        self._execution_attempt += 1
        execution_attempt = self._execution_attempt
        executions: list[dict[str, Any]] = []
        diagnostics: list[dict[str, str]] = []
        infrastructure_error = False
        candidate_failure = False
        episode_groups = _episode_groups(frozen.suite_entries)
        for group in episode_groups:
            rules = tuple(group["rules"])
            primary_rule = rules[0]
            criterion_ids = tuple(rule["criterion_id"] for rule in rules)
            for case in group["cases"]:
                for repetition in range(1, frozen.repetitions + 1):
                    invocation = HarnessInvocation(
                        capability_id=group["capability_id"],
                        criterion_id=criterion_ids[0],
                        case_id=case["case_id"],
                        inputs=copy.deepcopy(case["inputs"]),
                        initial_state=copy.deepcopy(case["initial_state"]),
                        repetition=repetition,
                        measurement=copy.deepcopy(primary_rule["measurement"]),
                        metric=primary_rule["metric"],
                        threshold=copy.deepcopy(primary_rule["threshold"]),
                        dwell_s=max(rule["dwell_s"] for rule in rules),
                        timeout_s=min(rule["timeout_s"] for rule in rules),
                        aggregation=primary_rule["aggregation"],
                        guard_ids=tuple(sorted({guard_id for rule in rules for guard_id in rule["guard_ids"]})),
                        run_snapshot_hash=frozen.run_snapshot_hash,
                        candidate_source_hash=candidate.source_hash,
                        suite_hash=frozen.suite_hash,
                        execution_attempt=execution_attempt,
                        criterion_ids=criterion_ids,
                        criteria=tuple(copy.deepcopy(rule) for rule in rules),
                    )
                    try:
                        session = self._harness.open(invocation)
                        if not isinstance(session, TypedHarnessSession):
                            raise ContractError("TypedHarness returned an invalid session")
                    except HarnessInfrastructureError as exc:
                        executions.append(_execution(
                            group["capability_id"], criterion_ids[0], case["case_id"], repetition,
                            "INFRASTRUCTURE_ERROR", ["HARNESS_INFRASTRUCTURE"], exc.video_evidence,
                            criterion_ids=criterion_ids,
                        ))
                        infrastructure_error = True
                        break
                    except Exception:
                        executions.append(_execution(
                            group["capability_id"], criterion_ids[0], case["case_id"], repetition,
                            "FAIL", ["HARNESS_PROTOCOL"], criterion_ids=criterion_ids,
                        ))
                        candidate_failure = True
                        continue
                    candidate_exception = False
                    candidate_infrastructure_error: HarnessInfrastructureError | None = None
                    try:
                        session.invoke(candidate, group["capability_id"], case["inputs"])
                    except HarnessInfrastructureError as exc:
                        candidate_infrastructure_error = exc
                    except Exception:
                        candidate_exception = True
                    try:
                        observation = session.collect()
                    except HarnessInfrastructureError as exc:
                        executions.append(_execution(
                            group["capability_id"], criterion_ids[0], case["case_id"], repetition,
                            "INFRASTRUCTURE_ERROR", ["HARNESS_INFRASTRUCTURE"], exc.video_evidence,
                            criterion_ids=criterion_ids,
                        ))
                        infrastructure_error = True
                        break
                    except Exception:
                        executions.append(_execution(
                            group["capability_id"], criterion_ids[0], case["case_id"], repetition,
                            "FAIL", ["HARNESS_PROTOCOL"], criterion_ids=criterion_ids,
                        ))
                        candidate_failure = True
                        continue
                    if candidate_infrastructure_error is not None:
                        executions.append(_execution(
                            group["capability_id"],
                            criterion_ids[0],
                            case["case_id"],
                            repetition,
                            "INFRASTRUCTURE_ERROR",
                            ["HARNESS_INFRASTRUCTURE"],
                            observation.video_evidence if isinstance(observation, HarnessMeasurement) else candidate_infrastructure_error.video_evidence,
                            observation.sdk_route_evidence if isinstance(observation, HarnessMeasurement) else None,
                            criterion_ids=criterion_ids,
                        ))
                        infrastructure_error = True
                        break
                    if not isinstance(observation, HarnessMeasurement):
                        failures = ["HARNESS_PROTOCOL"]
                        criterion_results = []
                    elif len(rules) > 1 and (
                        not isinstance(observation.criterion_measurements, Mapping)
                        or any(
                            criterion_id not in observation.criterion_measurements
                            for criterion_id in criterion_ids
                        )
                    ):
                        executions.append(_execution(
                            group["capability_id"], criterion_ids[0], case["case_id"], repetition,
                            "INFRASTRUCTURE_ERROR", ["HARNESS_INFRASTRUCTURE"],
                            observation.video_evidence,
                            observation.sdk_route_evidence,
                            criterion_ids=criterion_ids,
                        ))
                        infrastructure_error = True
                        break
                    else:
                        try:
                            _verify_video_evidence(observation.video_evidence, invocation)
                        except HarnessInfrastructureError as exc:
                            executions.append(_execution(
                                group["capability_id"],
                                criterion_ids[0],
                                case["case_id"],
                                repetition,
                                "INFRASTRUCTURE_ERROR",
                                ["HARNESS_INFRASTRUCTURE"],
                                exc.video_evidence,
                                observation.sdk_route_evidence,
                                criterion_ids=criterion_ids,
                            ))
                            infrastructure_error = True
                            break
                        _passed, failures, criterion_results = _evaluate_episode(
                            observation, rules, frozen.context.run_snapshot
                        )
                    if candidate_exception:
                        failures = [*failures, "CANDIDATE_EXCEPTION"]
                        for criterion_result in criterion_results:
                            if "CANDIDATE_EXCEPTION" not in criterion_result["failure_codes"]:
                                criterion_result["failure_codes"].append("CANDIDATE_EXCEPTION")
                            criterion_result["verdict"] = "FAIL"
                    verdict = "PASS" if not failures else "FAIL"
                    executions.append(_execution(
                        group["capability_id"], criterion_ids[0], case["case_id"], repetition,
                        verdict, list(dict.fromkeys(failures)),
                        observation.video_evidence if isinstance(observation, HarnessMeasurement) else None,
                        observation.sdk_route_evidence if isinstance(observation, HarnessMeasurement) else None,
                        criterion_ids=criterion_ids,
                        criterion_results=criterion_results,
                    ))
                    candidate_failure |= verdict == "FAIL"
                if infrastructure_error:
                    break
            if infrastructure_error:
                break
        if infrastructure_error:
            status = "INFRASTRUCTURE_ERROR"
        elif candidate_failure:
            status = "FAIL"
        else:
            expected_count = sum(len(group["cases"]) for group in episode_groups) * frozen.repetitions
            if len(executions) != expected_count:
                raise ContractError("Validation B did not cover every sealed case and repetition")
            status = "PASS"
        diagnostics.extend(
            _issue(code, code.replace("_", " ").lower())
            for execution in executions
            for code in execution["failure_codes"]
        )
        report = {
            "artifact_type": "validation_b_report",
            "schema_version": _SCHEMA_VERSION,
            "status": status,
            "design_hash": frozen.design_hash,
            "blue_line_spec_hash": frozen.spec_hash,
            "blue_line_manifest_hash": frozen.manifest_hash,
            "suite_hash": frozen.suite_hash,
            "overlay_hash": overlay_hash,
            "source_hash": candidate.source_hash,
            "implementation_manifest_hash": candidate.implementation_manifest_hash,
            "implementation_bundle_hash": candidate.implementation_bundle_hash,
            "validation_a_report_hash": candidate.validation_a_report_hash,
            "run_snapshot_hash": frozen.run_snapshot_hash,
            "executions": executions,
            "diagnostics": copy.deepcopy(diagnostics),
        }
        report_hash = content_hash(canonical_bytes(report))
        return ValidationBResult(
            status=status,
            report=report,
            report_hash=report_hash,
            report_seal=create_seal(
                "validation_b_report",
                report_hash,
                [
                    frozen.design_hash,
                    frozen.spec_hash,
                    frozen.manifest_hash,
                    frozen.suite_hash,
                    frozen.implementation_bundle_hash,
                    candidate.validation_a_report_hash,
                    overlay_hash,
                ],
            ),
            suite_hash=frozen.suite_hash,
            overlay_hash=overlay_hash,
            run_snapshot_hash=frozen.run_snapshot_hash,
            executions=tuple(executions),
            diagnostics=tuple(diagnostics),
        )

    validate = run


def _execution(
    capability_id: str,
    criterion_id: str,
    case_id: str,
    repetition: int,
    verdict: str,
    failures: list[str],
    video: ClosedEvaluationVideo | None = None,
    sdk_route_evidence: Mapping[str, Any] | None = None,
    *,
    criterion_ids: tuple[str, ...] = (),
    criterion_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    all_criterion_ids = criterion_ids or ((criterion_id,) if criterion_id else ())
    return {
        "capability_id": capability_id,
        "criterion_id": criterion_id,
        "criterion_ids": list(all_criterion_ids),
        "criterion_results": copy.deepcopy(criterion_results or []),
        "case_id": case_id,
        "repetition": repetition,
        "verdict": verdict,
        "failure_codes": failures,
        "video_manifest_hash": video.manifest_content_hash if video is not None else None,
        "video_media_hash": video.media_content_hash if video is not None else None,
        "sdk_route_evidence": copy.deepcopy(dict(sdk_route_evidence)) if isinstance(sdk_route_evidence, Mapping) else None,
    }
