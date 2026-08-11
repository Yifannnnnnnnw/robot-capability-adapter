"""Framework-owned Validation B evaluator over a sealed Blue Line lineage."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash, is_content_hash
from ..foundation.seals import create_seal, verify_seal
from .validation_a import ValidatedCandidateHandle


_SCHEMA_VERSION = "1.0.0"


class HarnessInfrastructureError(Exception):
    """The sole Harness condition that produces a B infrastructure outcome."""


@dataclass(frozen=True)
class MeasurementSample:
    time_s: float
    value: float


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
    video_artifact_ref: Mapping[str, Any]


@dataclass(frozen=True)
class HarnessInvocation:
    capability_id: str
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


@runtime_checkable
class TypedHarnessSession(Protocol):
    @property
    def sdk(self) -> object: ...

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
    run_snapshot: Mapping[str, Any]


@dataclass(frozen=True)
class FrozenValidationContext:
    context: ValidationContext
    design_hash: str
    spec_hash: str
    manifest_hash: str
    suite_hash: str
    run_snapshot_hash: str
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
    spec_by_capability = {
        entry.get("capability_id"): entry for entry in spec_entries if isinstance(entry, Mapping) and _text(entry.get("capability_id"))
    }
    seen: set[str] = set()
    checked: list[dict[str, Any]] = []
    required = {
        "capability_id", "measurement", "threshold", "dwell_s", "timeout_s", "aggregation",
        "metric", "guard_ids", "cases", "lineage",
    }
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != required or not _text(entry.get("capability_id")):
            raise ContractError("Validation B suite has an invalid capability rule")
        capability_id = entry["capability_id"]
        if capability_id in seen or capability_id not in design_ids or capability_id not in spec_by_capability:
            raise ContractError("Validation B suite has invalid capability coverage")
        seen.add(capability_id)
        spec_entry = spec_by_capability[capability_id]
        for key in required - {"capability_id"}:
            if entry.get(key) != spec_entry.get(key):
                raise ContractError("Validation B suite rule does not match its sealed Blue Line spec")
        measurement = entry.get("measurement")
        threshold = entry.get("threshold")
        guards = entry.get("guard_ids")
        cases = entry.get("cases")
        if (
            not isinstance(measurement, Mapping)
            or set(measurement) != {"measurement_id", "entity", "unit", "frame"}
            or not all(_text(measurement.get(key)) for key in measurement)
            or not _text(entry.get("metric"))
            or not isinstance(threshold, Mapping)
            or set(threshold) != {"comparator", "value"}
            or threshold.get("comparator") not in {"<", "<=", ">", ">=", "=="}
            or not _finite(threshold.get("value"))
            or not _finite(entry.get("dwell_s"))
            or entry.get("dwell_s") < 0
            or not _finite(entry.get("timeout_s"), positive=True)
            or entry.get("aggregation") not in {"ALL", "ANY", "MEAN"}
            or not isinstance(guards, list)
            or not guards
            or not all(_text(item) for item in guards)
            or len(set(guards)) != len(guards)
            or not isinstance(cases, list)
            or not cases
        ):
            raise ContractError("Validation B suite has an incomplete evaluation rule")
        checked_cases: list[dict[str, Any]] = []
        case_ids: set[str] = set()
        for case in cases:
            if not isinstance(case, Mapping) or set(case) != {"case_id", "initial_state", "inputs"} or not _text(case.get("case_id")) or not isinstance(case.get("initial_state"), Mapping) or not isinstance(case.get("inputs"), Mapping) or case["case_id"] in case_ids:
                raise ContractError("Validation B suite has an invalid case")
            case_ids.add(case["case_id"])
            checked_cases.append(copy.deepcopy(dict(case)))
        checked.append(copy.deepcopy(dict(entry)) | {"cases": checked_cases})
    if seen != design_ids or set(spec_by_capability) != design_ids:
        raise ContractError("Validation B must cover every sealed Design capability exactly once")
    return tuple(checked), repetitions


def freeze_validation_context(context: ValidationContext) -> FrozenValidationContext:
    if not isinstance(context, ValidationContext):
        raise ContractError("Validation B requires a Framework ValidationContext")
    design, design_hash = _sealed_hash(context.capability_design, context.design_seal, "capability_design")
    spec, spec_hash = _sealed_hash(context.blue_line_spec, context.spec_seal, "blue_line_validation_spec")
    manifest, manifest_hash = _sealed_hash(context.blue_line_manifest, context.manifest_seal, "blue_line_manifest")
    suite, suite_hash = _sealed_hash(context.validation_suite, context.suite_seal, "validation_b_suite")
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
        "rim_hash": snapshot.get("rim_hash"),
        "sdk_entry_hash": snapshot.get("sdk_entry_hash"),
        "runtime_hash": snapshot.get("runtime_hash"),
        "harness_config_hash": snapshot.get("harness_config_hash"),
    }
    if snapshot != expected_snapshot or not all(is_content_hash(snapshot[key]) for key in ("rim_hash", "sdk_entry_hash", "runtime_hash", "harness_config_hash")):
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
        run_snapshot=snapshot,
    )
    return FrozenValidationContext(
        context=frozen_context,
        design_hash=design_hash,
        spec_hash=spec_hash,
        manifest_hash=manifest_hash,
        suite_hash=suite_hash,
        run_snapshot_hash=content_hash(canonical_bytes(snapshot)),
        suite_entries=entries,
        repetitions=repetitions,
    )


def _verify_handle(handle: ValidatedCandidateHandle, frozen: FrozenValidationContext) -> tuple[dict[str, Any], str]:
    if not isinstance(handle, ValidatedCandidateHandle) or handle._overlay is None:  # Framework-private fields are intentional here.
        raise ContractError("Validation B only accepts an A-produced overlay-bound candidate handle")
    overlay = copy.deepcopy(handle._overlay.overlay)
    overlay_hash = content_hash(canonical_bytes(overlay))
    try:
        if not verify_seal(dict(handle._overlay.seal)) or handle._overlay.seal.get("artifact_type") != "validation_execution_binding_overlay" or handle._overlay.seal.get("artifact_hash") != overlay_hash:
            raise ContractError("Validation B candidate overlay seal is invalid")
    except Exception as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Validation B candidate overlay seal is invalid") from exc
    expected = {
        "artifact_type", "schema_version", "suite_hash", "implementation_manifest_hash", "capability_bindings",
    }
    if set(overlay) != expected or overlay.get("artifact_type") != "validation_execution_binding_overlay" or overlay.get("schema_version") != _SCHEMA_VERSION or overlay.get("suite_hash") != frozen.suite_hash or overlay.get("implementation_manifest_hash") != handle.implementation_manifest_hash:
        raise ContractError("Validation B candidate overlay does not bind this exact suite and manifest")
    expected_symbols = {
        capability_id: handle._contracts[capability_id]["function_name"] for capability_id in handle._contracts
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


def _evaluate_measurement(
    observation: HarnessMeasurement,
    rule: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    measurement = rule["measurement"]
    if (
        observation.measurement_id != measurement["measurement_id"]
        or observation.metric != rule["metric"]
        or observation.entity != measurement["entity"]
        or observation.unit != measurement["unit"]
        or observation.frame != measurement["frame"]
    ):
        failures.append("MEASUREMENT_REFERENCE")
    if not _finite(observation.elapsed_s) or observation.elapsed_s > rule["timeout_s"]:
        failures.append("TIMEOUT")
    samples = observation.samples
    if not isinstance(samples, tuple) or not samples:
        failures.append("MEASUREMENT_SAMPLES")
        checked: list[MeasurementSample] = []
    else:
        checked = list(samples)
        if any(not isinstance(sample, MeasurementSample) or not _finite(sample.time_s) or sample.time_s < 0 or not _finite(sample.value) for sample in checked):
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
    guards = observation.guard_results
    if not isinstance(guards, Mapping) or any(guards.get(guard_id) is not True for guard_id in rule["guard_ids"]):
        failures.append("FALSE_PASS_GUARD")
    route = observation.sdk_route_evidence
    expected_route = {
        "verified": True,
        "rim_hash": snapshot["rim_hash"],
        "sdk_entry_hash": snapshot["sdk_entry_hash"],
        "runtime_hash": snapshot["runtime_hash"],
    }
    if not isinstance(route, Mapping) or dict(route) != expected_route:
        failures.append("SDK_ROUTE_EVIDENCE")
    video = observation.video_artifact_ref
    if not isinstance(video, Mapping) or set(video) != {"artifact_id", "content_hash", "complete"} or not _text(video.get("artifact_id")) or not is_content_hash(video.get("content_hash")) or video.get("complete") is not True:
        failures.append("VIDEO_ARTIFACT")
    return not failures, failures


class ValidationBRunner:
    """Production B accepts only a narrow typed Harness, never a verdict callback."""

    def __init__(self, harness: TypedHarness):
        if not isinstance(harness, TypedHarness):
            raise ContractError("Validation B requires a TypedHarness, not a verdict callback")
        self._harness = harness

    def run(self, candidate: ValidatedCandidateHandle, context: ValidationContext) -> ValidationBResult:
        frozen = freeze_validation_context(context)
        if self._harness.config_hash != frozen.context.run_snapshot["harness_config_hash"]:
            raise ContractError("Validation B Harness config does not match the frozen run_snapshot")
        _overlay, overlay_hash = _verify_handle(candidate, frozen)
        executions: list[dict[str, Any]] = []
        diagnostics: list[dict[str, str]] = []
        infrastructure_error = False
        candidate_failure = False
        for rule in frozen.suite_entries:
            for case in rule["cases"]:
                for repetition in range(1, frozen.repetitions + 1):
                    invocation = HarnessInvocation(
                        capability_id=rule["capability_id"],
                        inputs=copy.deepcopy(case["inputs"]),
                        initial_state=copy.deepcopy(case["initial_state"]),
                        repetition=repetition,
                        measurement=copy.deepcopy(rule["measurement"]),
                        metric=rule["metric"],
                        threshold=copy.deepcopy(rule["threshold"]),
                        dwell_s=rule["dwell_s"],
                        timeout_s=rule["timeout_s"],
                        aggregation=rule["aggregation"],
                        guard_ids=tuple(rule["guard_ids"]),
                        run_snapshot_hash=frozen.run_snapshot_hash,
                    )
                    try:
                        session = self._harness.open(invocation)
                        if not isinstance(session, TypedHarnessSession):
                            raise ContractError("TypedHarness returned an invalid session")
                    except HarnessInfrastructureError:
                        executions.append(_execution(rule["capability_id"], case["case_id"], repetition, "INFRASTRUCTURE_ERROR", ["HARNESS_INFRASTRUCTURE"]))
                        infrastructure_error = True
                        break
                    except Exception:
                        executions.append(_execution(rule["capability_id"], case["case_id"], repetition, "FAIL", ["HARNESS_PROTOCOL"]))
                        candidate_failure = True
                        continue
                    candidate_exception = False
                    try:
                        candidate._invoke(rule["capability_id"], case["inputs"], session.sdk)
                    except Exception:
                        candidate_exception = True
                    try:
                        observation = session.collect()
                    except HarnessInfrastructureError:
                        executions.append(_execution(rule["capability_id"], case["case_id"], repetition, "INFRASTRUCTURE_ERROR", ["HARNESS_INFRASTRUCTURE"]))
                        infrastructure_error = True
                        break
                    except Exception:
                        executions.append(_execution(rule["capability_id"], case["case_id"], repetition, "FAIL", ["HARNESS_PROTOCOL"]))
                        candidate_failure = True
                        continue
                    if not isinstance(observation, HarnessMeasurement):
                        failures = ["HARNESS_PROTOCOL"]
                    else:
                        _passed, failures = _evaluate_measurement(observation, rule, frozen.context.run_snapshot)
                    if candidate_exception:
                        failures = [*failures, "CANDIDATE_EXCEPTION"]
                    verdict = "PASS" if not failures else "FAIL"
                    executions.append(_execution(rule["capability_id"], case["case_id"], repetition, verdict, failures))
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
            expected_count = sum(len(rule["cases"]) for rule in frozen.suite_entries) * frozen.repetitions
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
                [frozen.design_hash, frozen.spec_hash, frozen.manifest_hash, frozen.suite_hash, overlay_hash],
            ),
            suite_hash=frozen.suite_hash,
            overlay_hash=overlay_hash,
            run_snapshot_hash=frozen.run_snapshot_hash,
            executions=tuple(executions),
            diagnostics=tuple(diagnostics),
        )

    validate = run


def _execution(capability_id: str, case_id: str, repetition: int, verdict: str, failures: list[str]) -> dict[str, Any]:
    return {
        "capability_id": capability_id,
        "case_id": case_id,
        "repetition": repetition,
        "verdict": verdict,
        "failure_codes": failures,
    }
