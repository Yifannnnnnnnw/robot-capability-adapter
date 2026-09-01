"""Condition-local bounded Repair for model-generated ``driver.py``."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from autoadapter2.react import ReactLoopError, run_artifact_react

from .generation import (
    _artifact_turn_budget,
    REPAIR_SCRATCH_MAX_TURNS,
    REPAIR_SKELETON_MAX_TURNS,
    IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT,
    DriverSourceAuditError,
    GenerationCondition,
    GenerationError,
    JsonGenerator,
    ModelCallEvidence,
    _react_evidence,
    _react_user_prompt,
    _source_root,
    _supports_react,
    _validate_public_invocation_abi,
    _invoke,
)
from .interactive import PublicDevelopmentSession
from .probe import ProbeBudget, ProbeSourceError, audit_public_source, run_probes
from .source_check import DriverSourceAudit, DriverSourceError, audit_driver_source


MAX_TOTAL_ATTEMPTS = 3


class RepairError(RuntimeError):
    """Raised when a Repair request is outside the bounded public contract."""

    def __init__(
        self,
        message: str,
        *,
        react_trace: Sequence[Mapping[str, Any]] = (),
        probe_results: Sequence[Mapping[str, Any]] = (),
        candidate_path: str | Path | None = None,
        model_turns: int = 0,
        tool_calls: int = 0,
    ) -> None:
        super().__init__(message)
        self.react_trace = tuple(copy.deepcopy(dict(item)) for item in react_trace)
        self.probe_results = tuple(copy.deepcopy(dict(item)) for item in probe_results)
        self.candidate_path = (
            Path(candidate_path) if candidate_path is not None else None
        )
        self.model_turns = int(model_turns)
        self.tool_calls = int(tool_calls)


class RepairLimitError(RepairError):
    """Raised when a condition has already used its three total attempts."""


REPAIR_PROMPT = """You are the condition-local Auto-Adapter Repair stage.
Repair only the previous model-authored driver.py using the complete candidate-facing report and
media manifest from the immediately preceding attempt. Public capability interfaces, source-derived
standards, actual invocation arguments, measured values, exceptions, logs, guard outcomes, and
trajectory diagnostics are available. Private validation definitions remain unavailable: do not infer
or request private suite files, hidden criteria, measurement bindings, private guards, hidden expected
values/trajectories, Harness source, or other condition artifacts.

Return exactly one JSON object with driver_filename='driver.py', driver_source, and repair_note.
Preserve the fixed public invocation ABI: each sealed capability method keeps its exact model-authored
name and is an instance method on the object returned by build(), with exact signature
``def <method_name>(self, request)``. Top-level functions do not satisfy the ABI. ``request`` is the
closed mapping declared by that capability's sealed request_schema; read only its declared fields and
do not add task, scene, reset, private-criteria, or whole-task fields.
Candidate imports are closed to __future__, math, json, typing, collections, dataclasses, numpy,
mujoco, and, only in skeleton-assisted mode, autoadapter2.trusted_skeletons.
Change only driver.py. Keep the requested generation condition boundary: skeleton-assisted may use
the supplied trusted skeleton family; from-scratch must not import or call it. The Framework still
owns canonical model/data and trial reset. Use direct, statically auditable syntax: mapping fields
via subscripts and object APIs via normal attributes; do not use getattr, setattr, eval, exec, or
dynamic binding. Do not return a verdict.""" + (
    "\n\n" + IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT
)

REPAIR_PROBE_PROMPT = """You are the preparation half of the condition-local Auto-Adapter Repair
stage. Read the previous driver and complete candidate-facing report/media supplied here. You may
request a bounded local Python/MuJoCo development probe to investigate the observed failure. Return
one JSON object with probe_requests (each containing probe_id and complete Python script) and a repair
plan. Do not return driver source in this preparation response. Probe scripts receive only the staged
public package and allowed runtime; they cannot inspect private validation definitions or Framework
modules. Public measured values, invocation arguments, failures, logs, guards, and opaque case IDs
remain available."""


REPAIR_REACT_SYSTEM = """You are the interactive, condition-local Auto-Adapter Repair stage.
The complete candidate-facing report and media manifest from the immediately preceding attempt are
in the public input; only private IVC/Harness definitions and secrets have been removed. The current
driver.py is the previous model-authored source. Diagnose the report and revise that source directly;
both are already complete in the initial public input. Use read_file, write_file, and one persistent
credential-free public Python/MuJoCo execute_python session. Skeleton discovery is available only in
skeleton-assisted Repair; from-scratch must not read or import skeleton source. Preserve sealed
method names and the exact (self, request) ABI; request is always the closed mapping declared by the
sealed request_schema. Read only schema-declared fields and do not add task, scene, reset,
private-criteria, or whole-task fields. Candidate imports are closed to __future__, math, json,
typing, collections, dataclasses, numpy, mujoco, and, only in skeleton-assisted mode,
autoadapter2.trusted_skeletons. End a turn after writing a corrected driver.py; the
Framework validates its source and public import/build boundary. Never access or infer private suite
construction, reference code, the other condition, credentials, or a final Harness verdict.""" + (
    "\n\n" + IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT
) + """

Repair previous_driver_source from the complete supplied report. Start with
repair_focus_summary, then consult candidate_report for the complete per-trial evidence. Use
write_file to replace only driver.py and execute_python for bounded public checks. Leave a complete
corrected driver.py artifact in the workspace and end the turn; do not merely print or return source
in a JSON answer."""


_PRIVATE_DEFINITION_KEYS = frozenset(
    {
        "suite",
        "private_suite",
        "validation_suite",
        "suite_source",
        "private_suite_source",
        "criterion",
        "criterion_definition",
        "executable_criterion",
        "measurement_binding",
        "binding_definition",
        "private_binding",
        "private_bindings",
        "guard_definition",
        "guard_definitions",
        "private_guard",
        "private_guards",
        "expected",
        "expected_value",
        "expected_values",
        "expected_trajectory",
        "hidden_expected_trajectory",
        "private_threshold",
        "private_thresholds",
        "private_path",
        "private_dir",
        "internal_private_path",
        "reference_driver",
        "reference_driver_source",
        "calibration_reference_source",
        "reference_path",
        "harness_source",
        "harness_configuration",
        "credentials",
        "api_key",
        "token",
        "secret",
    }
)

_PRIVATE_CONTEXT_KEYS = _PRIVATE_DEFINITION_KEYS


def _normal_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_")


def _looks_private_path(value: str) -> bool:
    normal = value.replace("\\", "/").lower()
    return (
        "/tasks/private/" in normal
        or normal.endswith("/tasks/private")
        or "/private/" in normal
        or normal.endswith("/private")
        or "/.env" in normal
        or normal.endswith("/.env")
    )


def _redact(value: Any, *, key: str | None = None) -> Any:
    normal = _normal_key(key) if key is not None else ""
    if normal in _PRIVATE_DEFINITION_KEYS:
        return None
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for child_key, child_value in value.items():
            child_normal = _normal_key(child_key)
            if child_normal in _PRIVATE_DEFINITION_KEYS:
                continue
            redacted = _redact(child_value, key=child_normal)
            if redacted is not None:
                result[str(child_key)] = redacted
        return result
    if isinstance(value, list):
        return [_redact(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, key=key) for item in value]
    if isinstance(value, str) and _looks_private_path(value):
        return "<private path redacted>"
    return copy.deepcopy(value)


def redact_candidate_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Remove only private validation definitions from a candidate-facing report."""

    if not isinstance(report, Mapping):
        raise RepairError("candidate report must be a JSON object")
    redacted = _redact(report)
    if not isinstance(redacted, dict):  # pragma: no cover - _redact preserves mappings
        raise RepairError("redacted candidate report must be an object")
    return redacted


_TRAJECTORY_ENDPOINT_KEYS = frozenset(
    {
        "time",
        "qpos",
        "qvel",
        "ctrl",
        "joint_positions",
        "body_positions",
        "site_positions",
    }
)


def _compact_trajectory_sample(sample: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        str(key): copy.deepcopy(value)
        for key, value in sample.items()
        if str(key) in _TRAJECTORY_ENDPOINT_KEYS
    }
    contacts = sample.get("contacts")
    if isinstance(contacts, list):
        result["contact_count"] = len(contacts)
    return result


def _compact_physical_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        str(key): copy.deepcopy(value)
        for key, value in evidence.items()
        if key != "samples"
    }
    samples = evidence.get("samples")
    if not isinstance(samples, list):
        return result
    sample_items = [item for item in samples if isinstance(item, Mapping)]
    result["dense_samples_compacted"] = True
    result["sample_count"] = len(samples)
    if sample_items:
        result["initial_sample"] = _compact_trajectory_sample(sample_items[0])
        result["final_sample"] = _compact_trajectory_sample(sample_items[-1])
    return result


def _compact_candidate_report(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if key == "physical_evidence" and isinstance(child, Mapping):
                result[str(key)] = _compact_physical_evidence(child)
            else:
                result[str(key)] = _compact_candidate_report(child)
        return result
    if isinstance(value, list):
        return [_compact_candidate_report(item) for item in value]
    if isinstance(value, tuple):
        return [_compact_candidate_report(item) for item in value]
    return copy.deepcopy(value)


_REPAIR_SUMMARY_TOP_KEYS = (
    "validation_passed",
    "physical_validation_executed",
    "video_complete",
    "private_case_count",
    "passed_private_case_count",
    "task_count",
    "passed_task_count",
    "source_clause_count",
    "passed_source_clause_count",
)

_REPAIR_SUMMARY_TRIAL_KEYS = (
    "case_id",
    "capability_id",
    "task_id",
    "source_clause_id",
    "trial_passed",
    "criterion_passed",
    "measurement_value",
    "aggregation_value",
    "aggregation_passed",
    "temporal_passed",
    "physical_execution_passed",
    "worker_completed",
    "candidate_exception",
    "measurement_error",
)

_REPAIR_SUMMARY_PHYSICAL_KEYS = (
    "step_count",
    "canonical_model_data",
    "ctrl_changed_from_reset",
    "ctrl_observed_before_step",
    "direct_state_write_detected",
    "contact_pair_step_counts",
)


def _selected_fields(value: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value[key])
        for key in keys
        if key in value
    }


def _numeric_delta(initial: Any, final: Any) -> float | None:
    if (
        isinstance(initial, (int, float))
        and not isinstance(initial, bool)
        and isinstance(final, (int, float))
        and not isinstance(final, bool)
    ):
        return abs(float(final) - float(initial))
    if (
        isinstance(initial, (list, tuple))
        and isinstance(final, (list, tuple))
        and len(initial) == len(final)
        and all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (*initial, *final)
        )
    ):
        return sum(
            (float(after) - float(before)) ** 2
            for before, after in zip(initial, final, strict=True)
        ) ** 0.5
    return None


def _rounded_state_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (list, tuple)):
        return [_rounded_state_value(item) for item in value]
    return copy.deepcopy(value)


def _named_state_changes(initial: Any, final: Any) -> dict[str, Any] | None:
    if not isinstance(initial, Mapping) or not isinstance(final, Mapping):
        return None
    delta_norms: dict[str, Any] = {}
    unchanged_names: list[str] = []
    for name in sorted(set(initial) | set(final), key=str):
        before = initial.get(name)
        after = final.get(name)
        delta = _numeric_delta(before, after)
        if delta is not None and delta <= 1e-12:
            unchanged_names.append(str(name))
            continue
        delta_norms[str(name)] = round(delta, 6) if delta is not None else None
    return {"delta_norms": delta_norms, "unchanged_names": unchanged_names}


def _endpoint_state_summary(physical: Mapping[str, Any]) -> dict[str, Any] | None:
    initial = physical.get("initial_sample")
    final = physical.get("final_sample")
    if not isinstance(initial, Mapping) or not isinstance(final, Mapping):
        return None
    result: dict[str, Any] = {}
    for key in ("time", "ctrl", "contact_count"):
        if key in initial or key in final:
            result[key] = {
                "initial": _rounded_state_value(initial.get(key)),
                "final": _rounded_state_value(final.get(key)),
            }
    if "joint_positions" in initial or "joint_positions" in final:
        result["joint_positions"] = {
            "initial": _rounded_state_value(initial.get("joint_positions")),
            "final": _rounded_state_value(final.get("joint_positions")),
        }
    for key in ("body_positions", "site_positions"):
        changes = _named_state_changes(initial.get(key), final.get(key))
        if changes is not None:
            result[key] = changes
    return result


def _repair_focus_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    """Create a small deterministic index into the complete candidate report."""

    summary = _selected_fields(report, _REPAIR_SUMMARY_TOP_KEYS)
    passed_trials: list[dict[str, Any]] = []
    failed_trials: list[dict[str, Any]] = []
    unresolved_trials: list[dict[str, Any]] = []
    trials = report.get("trials")
    for value in trials if isinstance(trials, list) else ():
        if not isinstance(value, Mapping):
            continue
        focus = _selected_fields(value, _REPAIR_SUMMARY_TRIAL_KEYS)
        status = value.get("trial_passed")
        if not isinstance(status, bool):
            status = value.get("criterion_passed")
        if status is False:
            for key in ("public_arguments", "guard_outcomes"):
                if key in value:
                    focus[key] = copy.deepcopy(value[key])
            physical = value.get("physical_evidence")
            if isinstance(physical, Mapping):
                focus["physical_evidence"] = _selected_fields(
                    physical, _REPAIR_SUMMARY_PHYSICAL_KEYS
                )
                endpoint_state = _endpoint_state_summary(physical)
                if endpoint_state is not None:
                    focus["physical_evidence"]["endpoint_state"] = endpoint_state
            failed_trials.append(focus)
        elif status is True:
            passed_trials.append(focus)
        else:
            unresolved_trials.append(focus)
    summary["failed_trials"] = failed_trials
    summary["passed_trials"] = passed_trials
    if unresolved_trials:
        summary["unresolved_trials"] = unresolved_trials
    summary["usage"] = (
        "Use this as an index; candidate_report remains the complete authoritative "
        "candidate-facing report. Preserve behavior for passed trials."
    )
    return summary


def _assert_public_context(
    value: Any,
    *,
    where: str = "public_context",
    allow_public_study_criterion: bool = False,
) -> None:
    public_study_roots = (
        ("study", "findings"),
        ("study", "implementation_plan"),
        ("public_context", "study", "findings"),
        ("public_context", "study", "implementation_plan"),
    )

    def visit(node: Any, *, display_path: str, key_path: tuple[str, ...]) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                normal = _normal_key(key)
                criterion_is_public_study_output = (
                    allow_public_study_criterion
                    and normal == "criterion"
                    and any(
                        key_path[: len(root)] == root
                        for root in public_study_roots
                    )
                )
                if normal in _PRIVATE_CONTEXT_KEYS and not criterion_is_public_study_output:
                    raise RepairError(f"{display_path} contains private field {key!r}")
                visit(
                    child,
                    display_path=f"{display_path}.{key}",
                    key_path=(*key_path, normal),
                )
        elif isinstance(node, (list, tuple)):
            for index, child in enumerate(node):
                visit(
                    child,
                    display_path=f"{display_path}[{index}]",
                    key_path=key_path,
                )

    visit(value, display_path=where, key_path=())


def build_repair_inputs(
    *,
    previous_driver_source: str,
    candidate_report: Mapping[str, Any],
    media_manifest: Any,
    public_inputs: Mapping[str, Any],
    condition: GenerationCondition | str,
    previous_attempt: int,
    max_total_attempts: int = MAX_TOTAL_ATTEMPTS,
) -> dict[str, Any]:
    """Compose the complete candidate-facing Repair context after redaction."""

    if condition not in {"skeleton-assisted", "from-scratch"}:
        raise RepairError(f"unknown generation condition {condition!r}")
    if not isinstance(previous_driver_source, str) or not previous_driver_source.strip():
        raise RepairError("previous_driver_source must be non-empty")
    _assert_public_context(public_inputs, allow_public_study_criterion=True)
    compact_report = _compact_candidate_report(
        redact_candidate_report(candidate_report)
    )
    focus_summary = _repair_focus_summary(compact_report)
    _assert_public_context(focus_summary, where="repair_focus_summary")
    return {
        "generation_condition": condition,
        "previous_attempt": previous_attempt,
        "max_total_attempts": max_total_attempts,
        "previous_driver_source": previous_driver_source,
        "candidate_report": compact_report,
        "media_manifest": _redact(media_manifest),
        "public_context": copy.deepcopy(dict(public_inputs)),
        "repair_focus_summary": focus_summary,
    }


def _validate_attempt_budget(previous_attempt: int, max_total_attempts: int) -> None:
    if max_total_attempts < 1 or max_total_attempts > MAX_TOTAL_ATTEMPTS:
        raise RepairError("max_total_attempts must be between 1 and 3")
    if previous_attempt < 0:
        raise RepairError("previous_attempt cannot be negative")
    if previous_attempt >= max_total_attempts - 1:
        raise RepairLimitError(
            f"attempt {previous_attempt} is the final permitted attempt "
            f"for max_total_attempts={max_total_attempts}"
        )


def _read_previous_source(previous_driver_source: str | Path) -> str:
    if isinstance(previous_driver_source, Path):
        try:
            return previous_driver_source.read_text(encoding="utf-8")
        except OSError as exc:
            raise RepairError(f"cannot read previous driver: {previous_driver_source}") from exc
    return previous_driver_source


def _probe_requests(output: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    requests = output.get("probe_requests", [])
    if requests is None:
        return ()
    if not isinstance(requests, list):
        raise RepairError("repair probe_requests must be a list")
    result: list[dict[str, Any]] = []
    for index, request in enumerate(requests):
        if not isinstance(request, Mapping):
            raise RepairError(f"repair probe_requests[{index}] must be an object")
        probe_id = request.get("probe_id")
        script = request.get("script")
        if not isinstance(probe_id, str) or not probe_id.strip():
            raise RepairError(f"repair probe_requests[{index}].probe_id is required")
        if not isinstance(script, str) or not script.strip():
            raise RepairError(f"repair probe_requests[{index}].script is required")
        result.append({"probe_id": probe_id.strip(), "script": script})
    return tuple(result)


def _method_names(public_inputs: Mapping[str, Any], explicit: Sequence[str] | None) -> tuple[str, ...]:
    if explicit is not None:
        names = tuple(str(item) for item in explicit)
    else:
        design = public_inputs.get("sealed_capability_design")
        capabilities = design.get("capabilities") if isinstance(design, Mapping) else None
        if not isinstance(capabilities, list):
            raise RepairError("public_inputs lacks sealed capability method names")
        names = tuple(
            str(capability["method_name"])
            for capability in capabilities
            if isinstance(capability, Mapping) and isinstance(capability.get("method_name"), str)
        )
    if not names:
        raise RepairError("at least one capability method is required for Repair audit")
    return names


@dataclass(frozen=True)
class RepairPreparation:
    previous_attempt: int
    attempt: int
    output: dict[str, Any]
    repair_inputs: dict[str, Any]
    call_evidence: ModelCallEvidence


@dataclass(frozen=True)
class RepairResult:
    previous_attempt: int
    attempt: int
    output: dict[str, Any]
    driver_source: str
    driver_path: Path
    source_audit: DriverSourceAudit
    repair_inputs: dict[str, Any]
    call_evidence: ModelCallEvidence
    probe_results: tuple[dict[str, Any], ...] = ()
    prepare_call_evidence: ModelCallEvidence | None = None


def prepare_repair(
    client: JsonGenerator,
    *,
    previous_driver_source: str | Path,
    candidate_report: Mapping[str, Any],
    media_manifest: Any,
    public_inputs: Mapping[str, Any],
    condition: GenerationCondition | str,
    previous_attempt: int,
    max_total_attempts: int = MAX_TOTAL_ATTEMPTS,
) -> RepairPreparation:
    """Ask the model for a bounded local probe plan before a Repair."""

    _validate_attempt_budget(previous_attempt, max_total_attempts)
    source = _read_previous_source(previous_driver_source)
    repair_inputs = build_repair_inputs(
        previous_driver_source=source,
        candidate_report=candidate_report,
        media_manifest=media_manifest,
        public_inputs=public_inputs,
        condition=condition,
        previous_attempt=previous_attempt,
        max_total_attempts=max_total_attempts,
    )
    output, evidence = _invoke(
        client,
        stage="repair_prepare",
        prompt=REPAIR_PROBE_PROMPT,
        inputs=repair_inputs,
    )
    if "driver_source" in output:
        raise RepairError("repair_prepare must not return driver_source")
    _assert_public_context(output, where="repair_prepare_output")
    requests = _probe_requests(output)
    valid_requests: list[dict[str, Any]] = []
    rejected_requests: list[dict[str, Any]] = []
    for request in requests:
        try:
            audit_public_source(
                request["script"],
                condition=condition,
                allow_probe_utilities=True,
            )
        except ProbeSourceError as exc:
            rejected_requests.append(
                {
                    "probe_id": request["probe_id"],
                    "type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }
            )
        else:
            valid_requests.append(request)
    if rejected_requests:
        output["probe_requests"] = valid_requests
        output["rejected_probe_requests"] = rejected_requests
    return RepairPreparation(
        previous_attempt=previous_attempt,
        attempt=previous_attempt + 1,
        output=output,
        repair_inputs=repair_inputs,
        call_evidence=evidence,
    )


def _materialize_driver(
    *,
    output: Mapping[str, Any],
    evidence: ModelCallEvidence,
    repair_inputs: Mapping[str, Any],
    public_inputs: Mapping[str, Any],
    condition: GenerationCondition | str,
    previous_attempt: int,
    workspace: str | Path,
    capability_methods: Sequence[str] | None,
    probe_results: Sequence[Mapping[str, Any]],
    prepare_call_evidence: ModelCallEvidence | None,
) -> RepairResult:
    driver_source = output.get("driver_source")
    if not isinstance(driver_source, str) or not driver_source.strip():
        raise RepairError("REPAIR output must contain non-empty driver_source")
    if output.get("driver_filename", "driver.py") != "driver.py":
        raise RepairError("REPAIR may change only driver.py")
    try:
        audit = audit_driver_source(
            driver_source,
            condition=condition,  # type: ignore[arg-type]
            capability_methods=_method_names(public_inputs, capability_methods),
            candidate_request_boundary=True,
        )
        _validate_public_invocation_abi(
            driver_source,
            _method_names(public_inputs, capability_methods),
        )
        compile(driver_source, "driver.py", "exec")
        audit_public_source(driver_source, condition=condition)
    except (DriverSourceError, GenerationError, ProbeSourceError, SyntaxError) as exc:
        raise DriverSourceAuditError(
            f"repaired driver failed source audit: {exc}",
            driver_source=driver_source,
            model_output=output,
        ) from exc

    destination = Path(workspace).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    driver_path = destination / "driver.py"
    driver_path.write_text(driver_source, encoding="utf-8")
    return RepairResult(
        previous_attempt=previous_attempt,
        attempt=previous_attempt + 1,
        output=copy.deepcopy(dict(output)),
        driver_source=driver_source,
        driver_path=driver_path,
        source_audit=audit,
        repair_inputs=copy.deepcopy(dict(repair_inputs)),
        call_evidence=evidence,
        probe_results=tuple(copy.deepcopy(dict(item)) for item in probe_results),
        prepare_call_evidence=prepare_call_evidence,
    )


def _finalize_driver(
    client: JsonGenerator,
    *,
    repair_inputs: Mapping[str, Any],
    public_inputs: Mapping[str, Any],
    condition: GenerationCondition | str,
    previous_attempt: int,
    workspace: str | Path,
    capability_methods: Sequence[str] | None,
    probe_results: Sequence[Mapping[str, Any]],
    prepare_call_evidence: ModelCallEvidence | None,
) -> RepairResult:
    output, evidence = _invoke(
        client,
        stage="repair",
        prompt=REPAIR_PROMPT,
        inputs=repair_inputs,
    )
    return _materialize_driver(
        output=output,
        evidence=evidence,
        repair_inputs=repair_inputs,
        public_inputs=public_inputs,
        condition=condition,
        previous_attempt=previous_attempt,
        workspace=workspace,
        capability_methods=capability_methods,
        probe_results=probe_results,
        prepare_call_evidence=prepare_call_evidence,
    )


def _interactive_repair(
    client: Any,
    *,
    package: Any,
    previous_driver_source: str | Path,
    candidate_report: Mapping[str, Any],
    media_manifest: Any,
    public_inputs: Mapping[str, Any],
    condition: GenerationCondition | str,
    previous_attempt: int,
    workspace: str | Path,
    max_total_attempts: int,
    capability_methods: Sequence[str] | None,
    probe_budget: ProbeBudget,
    source_root: str | Path | None,
) -> RepairResult:
    _validate_attempt_budget(previous_attempt, max_total_attempts)
    source = _read_previous_source(previous_driver_source)
    repair_inputs = build_repair_inputs(
        previous_driver_source=source,
        candidate_report=candidate_report,
        media_manifest=media_manifest,
        public_inputs=public_inputs,
        condition=condition,
        previous_attempt=previous_attempt,
        max_total_attempts=max_total_attempts,
    )
    methods = _method_names(public_inputs, capability_methods)
    session = PublicDevelopmentSession(
        package=package,
        condition=str(condition),
        workspace=Path(workspace).resolve(),
        budget=probe_budget,
        source_root=_source_root(source_root),
        capability_methods=methods,
        initial_driver_source=source,
    )
    calls = getattr(client, "calls", ())
    start = (
        len(calls)
        if isinstance(calls, Sequence) and not isinstance(calls, (str, bytes))
        else 0
    )

    driver_path = session.workspace / "driver.py"

    def validate_driver_file(path: Path) -> dict[str, Any]:
        try:
            driver_source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise RepairError(f"driver.py cannot be read: {exc}") from exc
        if not driver_source.strip():
            raise RepairError("driver.py is empty")
        if session.revision <= 0 or driver_source == source:
            raise RepairError(
                "driver.py is unchanged from the previous frozen driver; "
                "Repair must revise it with write_file"
            )
        try:
            source_audit = audit_driver_source(
                driver_source,
                condition=condition,  # type: ignore[arg-type]
                capability_methods=methods,
                candidate_request_boundary=True,
            )
            _validate_public_invocation_abi(driver_source, methods)
            audit_public_source(driver_source, condition=condition)
            compile(driver_source, "driver.py", "exec")
        except (
            DriverSourceError,
            GenerationError,
            ProbeSourceError,
            SyntaxError,
            ValueError,
        ) as exc:
            raise RepairError(f"driver.py source boundary failed: {exc}") from exc
        import_result = session.validate_driver_artifact(path)
        return {
            "driver_filename": "driver.py",
            "driver_source": driver_source,
            "source_audit": asdict(source_audit),
            "import": import_result.get("import", {}),
        }

    try:
        react_result = run_artifact_react(
            client=client,
            stage="repair",
            system_prompt=REPAIR_REACT_SYSTEM,
            user_prompt=_react_user_prompt(repair_inputs),
            tools=session.artifact_tools(
                include_skeleton=str(condition) == "skeleton-assisted"
            ),
            artifact_name="driver.py",
            artifact_path=driver_path,
            validate_artifact=validate_driver_file,
            max_turns=_artifact_turn_budget("repair", str(condition)),
        )
    except ReactLoopError as exc:
        raise RepairError(
            f"interactive Repair did not produce a valid driver.py: {exc}",
            react_trace=exc.trace,
            probe_results=session.probe_results,
            candidate_path=session.candidate_path,
            model_turns=exc.model_turns,
            tool_calls=exc.tool_calls,
        ) from exc
    finally:
        interactive_probe_results = tuple(
            copy.deepcopy(dict(item)) for item in session.probe_results
        )
        session.close()
    if not isinstance(react_result.artifact, Mapping):
        raise RepairError("driver.py validation did not return an artifact record")
    output = copy.deepcopy(dict(react_result.artifact))
    output.setdefault("repair_note", "")
    evidence = _react_evidence(
        client,
        start=start,
        stage="repair",
        prompt=REPAIR_REACT_SYSTEM,
        inputs=repair_inputs,
        output=output,
        trace=react_result.trace,
    )
    return _materialize_driver(
        output=output,
        evidence=evidence,
        repair_inputs=repair_inputs,
        public_inputs=public_inputs,
        condition=condition,
        previous_attempt=previous_attempt,
        workspace=workspace,
        capability_methods=methods,
        probe_results=interactive_probe_results,
        prepare_call_evidence=None,
    )


def finalize_repair(
    client: JsonGenerator,
    *,
    preparation: RepairPreparation,
    probe_results: Sequence[Mapping[str, Any]],
    public_inputs: Mapping[str, Any],
    condition: GenerationCondition | str,
    workspace: str | Path,
    capability_methods: Sequence[str] | None = None,
) -> RepairResult:
    """Return the repaired driver after the model has received local probe facts."""

    final_inputs = copy.deepcopy(dict(preparation.repair_inputs))
    final_inputs["repair_prepare"] = copy.deepcopy(preparation.output)
    final_inputs["probe_results"] = copy.deepcopy(list(probe_results))
    _assert_public_context(final_inputs, allow_public_study_criterion=True)
    return _finalize_driver(
        client,
        repair_inputs=final_inputs,
        public_inputs=public_inputs,
        condition=condition,
        previous_attempt=preparation.previous_attempt,
        workspace=workspace,
        capability_methods=capability_methods,
        probe_results=probe_results,
        prepare_call_evidence=preparation.call_evidence,
    )


def repair_with_probes(
    client: JsonGenerator,
    *,
    package: Any,
    previous_driver_source: str | Path,
    candidate_report: Mapping[str, Any],
    media_manifest: Any,
    public_inputs: Mapping[str, Any],
    condition: GenerationCondition | str,
    previous_attempt: int,
    workspace: str | Path,
    max_total_attempts: int = MAX_TOTAL_ATTEMPTS,
    capability_methods: Sequence[str] | None = None,
    probe_budget: ProbeBudget = ProbeBudget(max_requests=None),
    source_root: str | Path | None = None,
) -> RepairResult:
    """Run one interactive Repair, with a one-shot path retained for test fakes."""

    if _supports_react(client):
        return _interactive_repair(
            client,
            package=package,
            previous_driver_source=previous_driver_source,
            candidate_report=candidate_report,
            media_manifest=media_manifest,
            public_inputs=public_inputs,
            condition=condition,
            previous_attempt=previous_attempt,
            workspace=workspace,
            max_total_attempts=max_total_attempts,
            capability_methods=capability_methods,
            probe_budget=probe_budget,
            source_root=source_root,
        )

    preparation = prepare_repair(
        client,
        previous_driver_source=previous_driver_source,
        candidate_report=candidate_report,
        media_manifest=media_manifest,
        public_inputs=public_inputs,
        condition=condition,
        previous_attempt=previous_attempt,
        max_total_attempts=max_total_attempts,
    )
    probe_results = run_probes(
        _probe_requests(preparation.output),
        package=package,
        workspace=workspace,
        condition=condition,
        budget=probe_budget,
        source_root=source_root,
    )
    return finalize_repair(
        client,
        preparation=preparation,
        probe_results=probe_results,
        public_inputs=public_inputs,
        condition=condition,
        workspace=workspace,
        capability_methods=capability_methods,
    )


def repair(
    client: JsonGenerator,
    *,
    previous_driver_source: str | Path,
    candidate_report: Mapping[str, Any],
    media_manifest: Any,
    public_inputs: Mapping[str, Any],
    condition: GenerationCondition | str,
    previous_attempt: int,
    workspace: str | Path,
    max_total_attempts: int = MAX_TOTAL_ATTEMPTS,
    capability_methods: Sequence[str] | None = None,
) -> RepairResult:
    """Call the final Repair model directly when no probe round is needed."""

    if _supports_react(client):
        raise RepairError(
            "interactive model clients must use repair_with_probes with a public package"
        )
    _validate_attempt_budget(previous_attempt, max_total_attempts)
    source = _read_previous_source(previous_driver_source)
    repair_inputs = build_repair_inputs(
        previous_driver_source=source,
        candidate_report=candidate_report,
        media_manifest=media_manifest,
        public_inputs=public_inputs,
        condition=condition,
        previous_attempt=previous_attempt,
        max_total_attempts=max_total_attempts,
    )
    repair_inputs["probe_results"] = []
    return _finalize_driver(
        client,
        repair_inputs=repair_inputs,
        public_inputs=public_inputs,
        condition=condition,
        previous_attempt=previous_attempt,
        workspace=workspace,
        capability_methods=capability_methods,
        probe_results=(),
        prepare_call_evidence=None,
    )


__all__ = [
    "MAX_TOTAL_ATTEMPTS",
    "REPAIR_SCRATCH_MAX_TURNS",
    "REPAIR_SKELETON_MAX_TURNS",
    "REPAIR_PROMPT",
    "REPAIR_PROBE_PROMPT",
    "REPAIR_REACT_SYSTEM",
    "RepairPreparation",
    "RepairError",
    "RepairLimitError",
    "RepairResult",
    "build_repair_inputs",
    "finalize_repair",
    "prepare_repair",
    "redact_candidate_report",
    "repair",
    "repair_with_probes",
]
