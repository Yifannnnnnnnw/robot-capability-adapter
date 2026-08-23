"""Small terminal Evolution sidecar for the mainline experiment.

Evolution is deliberately kept outside the validation path.  It consumes one
already terminal, candidate-facing report and can return at most one proposal
for a later Experience record.  The proposal is data only; this module never
writes a candidate, suite, criterion, verdict, retry decision, or run input.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol


class EvolutionError(ValueError):
    """Raised when the terminal report or proposal is not usable."""


class JsonGenerator(Protocol):
    """The small interface implemented by :class:`JsonModelClient`."""

    def generate_json(
        self,
        *,
        stage: str,
        prompt: str,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]: ...


EVOLUTION_SYSTEM_PROMPT = """You are the terminal Evolution sidecar for an experiment.
Read the supplied terminal candidate-facing Capability Validation and Task Demo report and, if useful, propose one
small Experience record for a future run.  You are not allowed to repair or judge the current
run.  Do not change its candidate, private suite, criterion, verdict, retry decision, or run
inputs.  Do not invent private validation definitions or secrets.  A proposal is optional.

Return exactly one JSON object.  Return {} when the retained public report contains no reusable
lesson.  Otherwise return exactly these five fields and no Framework wrapper or labels:
{"observation": "...", "lesson": "...", "recommendation": "...",
"scope": "...", "evidence": ["..."]}

The proposal must describe a possible future Experience record only.  It must never contain an
instruction to mutate the current run.  Keep observations tied to facts present in the report.
Lessons, recommendations, and any root-cause explanation are bounded hypotheses unless the
report directly establishes them; do not present an inferred cause as a measured fact."""


# These are definition-bearing fields that must never cross the candidate-facing boundary into
# Evolution.  Opaque private case identifiers are intentionally allowed.
_PRIVATE_FIELDS = {
    "private_suite",
    "private_validation_suite",
    "capability_validation_suite",
    "task_demo_suite",
    "private_instances",
    "private_bindings",
    "private_guards",
    "measurement_bindings",
    "executable_criteria",
    "expected_trajectories",
    "harness_source",
    "harness_config",
    "api_key",
    "credentials",
    "secret",
}

# A future proposal may discuss an observed outcome, but it cannot be an encoded mutation of
# anything that controls the current evaluation.
_CURRENT_RUN_MUTATION_FIELDS = {
    "candidate",
    "candidate_path",
    "driver",
    "driver_path",
    "suite",
    "criterion",
    "criteria",
    "verdict",
    "retry",
    "retry_decision",
    "run_inputs",
    "current_run",
    "validation_passed",
    "final_validation_passed",
    "final_capability_validation_passed",
    "task_demo_passed",
}

_PUBLIC_EVOLUTION_FIELDS = {
    "non_blocking",
    "terminal_report_read",
    "evolution_attempted",
    "evolution_completed",
    "proposal_created",
    "no_reusable_lesson",
    "proposal",
    "model_call_count",
    "model_calls",
    "model_input_compacted",
    "model_input_chars",
    "current_run_unchanged",
    "failure",
}

_PUBLIC_PROPOSAL_FIELDS = {
    "observation",
    "lesson",
    "recommendation",
    "scope",
    "evidence",
}

_SNAPSHOT_FRAMEWORK_FIELDS = frozenset(
    {
        "experience_id",
        "reviewed",
        "review_decision",
        "review_reason",
        "source_run_id",
        "source_robot",
        "generation_condition",
        "terminal_outcome_label",
        # Read-compatible aliases retained on newly generated records so older
        # consumers can continue to inspect the same Framework labels.
        "source_condition",
        "source_outcome",
        "outcome_label",
        "source_robot_configuration_id",
        "source_generation_condition",
    }
)

_COMPACT_VALIDATION_FIELDS = frozenset(
    {
        "pipeline_completed",
        "physical_validation_executed",
        "validation_passed",
        "video_complete",
        "video_required",
        "video_complete_count",
        "video_total_count",
        "complete_video_count",
        "total_video_count",
        "evaluation_role",
        "robot_configuration_id",
        "condition",
        "attempt",
        "passed_task_count",
        "task_count",
        "passed_source_clause_count",
        "source_clause_count",
        "passed_clause_count",
        "clause_count",
        "passed_private_case_count",
        "private_case_count",
        "passed_case_count",
        "case_count",
        "skipped",
    }
)

_COMPACT_VALIDATION_TEXT_FIELDS = frozenset(
    {"evaluation_role", "robot_configuration_id", "condition"}
)

_COMPACT_VALIDATION_BOOL_FIELDS = frozenset(
    {
        "pipeline_completed",
        "physical_validation_executed",
        "validation_passed",
        "video_complete",
        "video_required",
        "skipped",
    }
)

_COMPACT_VALIDATION_COUNT_FIELDS = _COMPACT_VALIDATION_FIELDS - (
    _COMPACT_VALIDATION_TEXT_FIELDS | _COMPACT_VALIDATION_BOOL_FIELDS
)

_COMPACT_TERMINAL_FIELDS = frozenset(
    {
        "cell_id",
        "run_id",
        "robot_configuration_id",
        "robot_package_version",
        "task_snapshot_id",
        "admitted_task_count",
        "designed_capability_count",
        "covered_task_count",
        "condition",
        "experience_input_ids",
        "experience_input_count",
        "pipeline_completed",
        "dynamic_model_called",
        "driver_generated_in_run",
        "capability_validation_executed",
        "initial_capability_validation_passed",
        "final_capability_validation_passed",
        "task_demo_executed",
        "task_demo_passed",
        "task_demo_pipeline_completed",
        "task_demo_video_complete",
        "physical_validation_executed",
        "initial_validation_passed",
        "final_validation_passed",
        "video_required",
        "video_complete",
        "attempt_count",
        "passed_task_count",
        "total_task_count",
        "passed_clause_count",
        "total_clause_count",
        "passed_case_count",
        "total_case_count",
        "infrastructure_failure",
    }
)

_COMPACT_TERMINAL_TEXT_FIELDS = frozenset(
    {
        "cell_id",
        "run_id",
        "robot_configuration_id",
        "robot_package_version",
        "task_snapshot_id",
        "condition",
    }
)

_COMPACT_TERMINAL_BOOL_FIELDS = frozenset(
    {
        "pipeline_completed",
        "dynamic_model_called",
        "driver_generated_in_run",
        "capability_validation_executed",
        "initial_capability_validation_passed",
        "final_capability_validation_passed",
        "task_demo_executed",
        "task_demo_passed",
        "task_demo_pipeline_completed",
        "task_demo_video_complete",
        "physical_validation_executed",
        "initial_validation_passed",
        "final_validation_passed",
        "video_required",
        "video_complete",
        "infrastructure_failure",
    }
)

_COMPACT_TERMINAL_COUNT_FIELDS = frozenset(
    {
        "admitted_task_count",
        "designed_capability_count",
        "covered_task_count",
        "experience_input_count",
        "attempt_count",
        "passed_task_count",
        "total_task_count",
        "passed_clause_count",
        "total_clause_count",
        "passed_case_count",
        "total_case_count",
    }
)

_COMPACT_TRIAL_FIELDS = frozenset(
    {
        "trial_id",
        "case_id",
        "capability_id",
        "task_id",
        "source_clause_id",
        "worker_completed",
        "method_invoked",
        "criterion_passed",
        "temporal_passed",
        "aggregation_passed",
        "guard_outcomes",
        "controller_completed",
        "physical_execution_passed",
        "physical_integrity_passed",
        "task_metric_passed",
        "trial_passed",
    }
)

_COMPACT_TRIAL_TEXT_FIELDS = frozenset(
    {"trial_id", "case_id", "capability_id", "task_id", "source_clause_id"}
)

_COMPACT_TRIAL_BOOL_FIELDS = _COMPACT_TRIAL_FIELDS - (
    _COMPACT_TRIAL_TEXT_FIELDS | {"guard_outcomes"}
)

_COMPACT_VIDEO_FIELDS = frozenset(
    {
        "requested",
        "complete",
        "frame_count",
        "duration_s",
        "sim_start_s",
        "sim_end_s",
    }
)

_COMPACT_VIDEO_BOOL_FIELDS = frozenset({"requested", "complete"})
_COMPACT_VIDEO_COUNT_FIELDS = frozenset({"frame_count"})
_COMPACT_VIDEO_TIME_FIELDS = _COMPACT_VIDEO_FIELDS - (
    _COMPACT_VIDEO_BOOL_FIELDS | _COMPACT_VIDEO_COUNT_FIELDS
)

_COMPACT_FAILURE_FIELDS = frozenset(
    {
        "stage",
        "type",
        "category",
        "failure_class",
        "infrastructure_failure",
    }
)

# Queue projection is intentionally stricter than the model-input check: a custom Evolution
# hook must not be able to smuggle private definitions into a run-level reviewer artifact.
_QUEUE_PRIVATE_FIELD_MARKERS = (
    "private",
    "suite",
    "binding",
    "guard",
    "criterion",
    "criteria",
    "trajectory",
    "harness",
    "reference",
    "candidate",
    "driver",
    "credential",
    "secret",
    "api_key",
)


def _error(exc: BaseException) -> dict[str, str]:
    message = str(exc).strip() or type(exc).__name__
    return {"type": type(exc).__name__, "message": message[:1000]}


def _call_count(client: JsonGenerator) -> int | None:
    calls = getattr(client, "calls", None)
    return len(calls) if isinstance(calls, list) else None


def _call_evidence(client: JsonGenerator, start: int | None) -> list[dict[str, Any]]:
    calls = getattr(client, "calls", None)
    if start is None or not isinstance(calls, list):
        return []
    allowed = {
        "stage",
        "provider",
        "api_protocol",
        "requested_model",
        "returned_model",
        "usage",
    }
    return [
        {str(key): copy.deepcopy(value) for key, value in call.items() if key in allowed}
        for call in calls[start:]
        if isinstance(call, Mapping)
    ]


def _walk_field_names(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str):
                names.append(key.strip().lower().replace("-", "_"))
            names.extend(_walk_field_names(child))
    elif isinstance(value, list):
        for child in value:
            names.extend(_walk_field_names(child))
    return names


def _validate_candidate_facing_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the minimum evidence shape without adding a lifecycle state machine."""

    if not isinstance(report, Mapping):
        raise EvolutionError("terminal report must be an object")

    # The terminal report must expose the two independent execution facts and a verdict.  The
    # The older Harness name remains accepted for historical report compatibility while
    # the integrated pipeline adds the explicit final_validation_passed alias.
    if not isinstance(report.get("pipeline_completed"), bool):
        raise EvolutionError("terminal report.pipeline_completed must be boolean")
    if not any(
        isinstance(report.get(field), bool)
        for field in (
            "capability_validation_executed",
            "physical_validation_executed",
        )
    ):
        raise EvolutionError(
            "terminal report has no capability-validation execution fact"
        )
    if not any(
        isinstance(report.get(field), bool)
        for field in (
            "final_capability_validation_passed",
            "final_validation_passed",
            "validation_passed",
            "terminal_validation_passed",
        )
    ):
        raise EvolutionError("terminal report has no terminal validation verdict")

    field_names = set(_walk_field_names(report))
    leaked = sorted(field_names & _PRIVATE_FIELDS)
    if leaked:
        raise EvolutionError(
            "terminal report contains private validation definitions: " + ", ".join(leaked)
        )
    return copy.deepcopy(dict(report))


def _safe_public_label(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 160:
        return None
    if not all(character.isalnum() or character in "_-.:@" for character in value):
        return None
    return value


def _compact_failure(value: Any) -> dict[str, Any] | None:
    """Retain a failure class without forwarding raw messages, paths, or definitions."""

    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for key, child in value.items():
        name = str(key)
        if name not in _COMPACT_FAILURE_FIELDS:
            continue
        if name == "infrastructure_failure" and isinstance(child, bool):
            result[name] = child
            continue
        label = _safe_public_label(child)
        if label is not None:
            result[name] = label
    return result or None


def _compact_terminal_facts(report: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in report.items():
        name = str(key)
        if name not in _COMPACT_TERMINAL_FIELDS:
            continue
        if name in _COMPACT_TERMINAL_TEXT_FIELDS:
            label = _safe_public_label(value)
            if label is not None:
                result[name] = label
        elif name in _COMPACT_TERMINAL_BOOL_FIELDS and isinstance(value, bool):
            result[name] = value
        elif (
            name in _COMPACT_TERMINAL_COUNT_FIELDS
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        ):
            result[name] = value
        elif name == "experience_input_ids" and isinstance(value, list):
            labels = [_safe_public_label(item) for item in value]
            if all(item is not None for item in labels):
                result[name] = labels
    return result


def _compact_trial(trial: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in trial.items():
        name = str(key)
        if name in _COMPACT_TRIAL_TEXT_FIELDS:
            label = _safe_public_label(value)
            if label is not None:
                result[name] = label
        elif name in _COMPACT_TRIAL_BOOL_FIELDS and isinstance(value, bool):
            result[name] = value
        elif name == "guard_outcomes" and isinstance(value, Mapping):
            guards: dict[str, bool] = {}
            valid = True
            for guard_key, guard_value in value.items():
                label = _safe_public_label(guard_key)
                if label is None or not isinstance(guard_value, bool):
                    valid = False
                    break
                guards[label] = guard_value
            if valid:
                result[name] = guards
    candidate_exception = _compact_failure(trial.get("candidate_exception"))
    if candidate_exception is not None:
        result["candidate_exception"] = candidate_exception
    physical = trial.get("physical_evidence")
    if isinstance(physical, Mapping):
        samples = physical.get("samples")
        result["physical_evidence"] = {}
        result["physical_evidence"]["sample_count"] = (
            len(samples) if isinstance(samples, list) else 0
        )
    video = trial.get("video")
    if isinstance(video, Mapping):
        compact_video: dict[str, Any] = {}
        for key, value in video.items():
            name = str(key)
            if name in _COMPACT_VIDEO_BOOL_FIELDS and isinstance(value, bool):
                compact_video[name] = value
            elif (
                name in _COMPACT_VIDEO_COUNT_FIELDS
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            ):
                compact_video[name] = value
            elif (
                name in _COMPACT_VIDEO_TIME_FIELDS
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and not (
                    isinstance(value, float)
                    and (value != value or value in {float("inf"), float("-inf")})
                )
            ):
                compact_video[name] = value
        result["video"] = compact_video
    contact_integrity = trial.get("contact_integrity")
    if isinstance(contact_integrity, Mapping) and isinstance(
        contact_integrity.get("passed"), bool
    ):
        result["contact_integrity_passed"] = contact_integrity["passed"]
    return result


def _compact_validation(validation: Mapping[str, Any]) -> dict[str, Any]:
    trials = validation.get("trials")
    trial_items = (
        [item for item in trials if isinstance(item, Mapping)]
        if isinstance(trials, list)
        else []
    )
    result: dict[str, Any] = {}
    for key, value in validation.items():
        name = str(key)
        if name in _COMPACT_VALIDATION_TEXT_FIELDS:
            label = _safe_public_label(value)
            if label is not None:
                result[name] = label
        elif name in _COMPACT_VALIDATION_BOOL_FIELDS and isinstance(value, bool):
            result[name] = value
        elif (
            name in _COMPACT_VALIDATION_COUNT_FIELDS
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        ):
            result[name] = value
    failure = _compact_failure(validation.get("failure"))
    if failure is not None:
        result["failure"] = failure
    if validation.get("skipped") is True:
        reason = validation.get("skip_reason")
        result["skip_reason_present"] = isinstance(reason, str) and bool(
            reason.strip()
        )
    result["trial_count"] = len(trial_items)
    result["passed_trial_count"] = sum(
        bool(item.get("trial_passed")) for item in trial_items
    )
    result["failed_trials"] = [
        _compact_trial(item)
        for item in trial_items
        if not bool(item.get("trial_passed"))
    ]
    manifest = validation.get("video_manifest")
    video_items = (
        [item for item in manifest if isinstance(item, Mapping)]
        if isinstance(manifest, list)
        else []
    )
    result["video_count"] = len(video_items)
    result["complete_video_count"] = sum(
        bool(item.get("complete")) for item in video_items
    )
    return result


def _compact_terminal_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only public terminal facts and bounded failed diagnostics."""

    result = _compact_terminal_facts(report)
    failure = _compact_failure(report.get("failure"))
    if failure is not None:
        result["failure"] = failure
    attempts = report.get("attempts")
    compact_attempts: list[dict[str, Any]] = []
    if isinstance(attempts, list):
        for attempt in attempts:
            if not isinstance(attempt, Mapping):
                continue
            compact: dict[str, Any] = {}
            attempt_number = attempt.get("attempt")
            if (
                isinstance(attempt_number, int)
                and not isinstance(attempt_number, bool)
                and attempt_number >= 0
            ):
                compact["attempt"] = attempt_number
            if isinstance(attempt.get("driver_generated"), bool):
                compact["driver_generated"] = attempt["driver_generated"]
            validation = attempt.get("capability_validation", attempt.get("validation"))
            if isinstance(validation, Mapping):
                compact["capability_validation"] = _compact_validation(validation)
            compact_attempts.append(compact)
    result["attempts"] = compact_attempts
    terminal_capability = report.get("capability_validation")
    if not isinstance(terminal_capability, Mapping):
        terminal_capability = report
    result["terminal_capability_validation"] = _compact_validation(
        terminal_capability
    )
    task_demo = report.get("task_demo")
    if isinstance(task_demo, Mapping):
        result["task_demo"] = _compact_validation(task_demo)
    return result


def _validate_public_proposal(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact public proposal shape used by model and snapshot boundaries."""

    unexpected = sorted(str(key) for key in proposal if key not in _PUBLIC_PROPOSAL_FIELDS)
    missing = sorted(_PUBLIC_PROPOSAL_FIELDS - set(proposal))
    if missing or unexpected:
        detail: list[str] = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if unexpected:
            detail.append("unexpected " + ", ".join(unexpected))
        raise EvolutionError("Evolution proposal has invalid fields: " + "; ".join(detail))
    for field in ("observation", "lesson", "recommendation", "scope"):
        value = proposal[field]
        if not isinstance(value, str) or not value.strip():
            raise EvolutionError(
                f"Evolution proposal.{field} must be a non-empty string"
            )
    evidence = proposal["evidence"]
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        raise EvolutionError(
            "Evolution proposal.evidence must be a non-empty public string list"
        )
    return copy.deepcopy(dict(proposal))


def validate_experience_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Validate the new global snapshot's lineage and exact public content."""

    if not isinstance(snapshot, Mapping):
        raise EvolutionError("Experience snapshot must be an object")
    source_run_id = snapshot.get("source_run_id")
    if not isinstance(source_run_id, str) or not source_run_id.strip():
        raise EvolutionError("Experience snapshot.source_run_id must be non-empty text")
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise EvolutionError("Experience snapshot.records must be a list")
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise EvolutionError(f"Experience snapshot.records[{index}] must be an object")
        unexpected = sorted(
            str(key)
            for key in record
            if str(key) not in _SNAPSHOT_FRAMEWORK_FIELDS | _PUBLIC_PROPOSAL_FIELDS
        )
        if unexpected:
            raise EvolutionError(
                f"Experience snapshot.records[{index}] contains unsupported fields: "
                + ", ".join(unexpected)
            )
        required_lineage = (
            "experience_id",
            "source_run_id",
            "source_robot",
            "generation_condition",
            "terminal_outcome_label",
        )
        if any(
            not isinstance(record.get(field), str) or not record[field].strip()
            for field in required_lineage
        ):
            raise EvolutionError(
                f"Experience snapshot.records[{index}] is missing Framework lineage labels"
            )
        if record["source_run_id"] != source_run_id:
            raise EvolutionError(
                f"Experience snapshot.records[{index}] has mismatched source_run_id"
            )
        if record["terminal_outcome_label"] not in {"positive", "negative"}:
            raise EvolutionError(
                f"Experience snapshot.records[{index}] has invalid terminal_outcome_label"
            )
        aliases = {
            "source_condition": record["generation_condition"],
            "source_outcome": record["terminal_outcome_label"],
            "outcome_label": record["terminal_outcome_label"],
            "source_generation_condition": record["generation_condition"],
            "source_robot_configuration_id": record["source_robot"],
        }
        for field, expected in aliases.items():
            if field in record and record[field] != expected:
                raise EvolutionError(
                    f"Experience snapshot.records[{index}] has conflicting {field}"
                )
        if record.get("reviewed") is not True or record.get("review_decision") != "accept":
            raise EvolutionError(
                f"Experience snapshot.records[{index}] is not an accepted review"
            )
        reason = record.get("review_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise EvolutionError(
                f"Experience snapshot.records[{index}] requires a review reason"
            )
        _validate_public_proposal(
            {field: record.get(field) for field in _PUBLIC_PROPOSAL_FIELDS}
        )


def _validate_proposal(response: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(response, Mapping):
        raise EvolutionError("Evolution model response must be an object")
    if not response:
        return None
    field_names = set(_walk_field_names(response))
    forbidden = sorted(field_names & _CURRENT_RUN_MUTATION_FIELDS)
    if forbidden:
        raise EvolutionError(
            "Evolution proposal attempts to mutate the current run: " + ", ".join(forbidden)
        )
    return _validate_public_proposal(response)


def _normalise_field_name(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_").replace(".", "_")


def _is_queue_private_field(key: Any) -> bool:
    name = _normalise_field_name(key)
    return any(marker in name for marker in _QUEUE_PRIVATE_FIELD_MARKERS)


def _public_queue_value(value: Any, *, proposal: bool = False) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            name = _normalise_field_name(key)
            if _is_queue_private_field(name):
                continue
            if proposal and name not in _PUBLIC_PROPOSAL_FIELDS:
                continue
            result[str(key)] = _public_queue_value(child)
        return result
    if isinstance(value, list):
        return [_public_queue_value(child) for child in value]
    if isinstance(value, tuple):
        return [_public_queue_value(child) for child in value]
    return copy.deepcopy(value)


def project_public_evolution_outcome(outcome: Any) -> dict[str, Any] | None:
    """Return the small public Evolution result suitable for human review."""

    if not isinstance(outcome, Mapping):
        return None
    selected = {
        str(key): value
        for key, value in outcome.items()
        if str(key) in _PUBLIC_EVOLUTION_FIELDS
    }
    projected = _public_queue_value(selected)
    raw_proposal = outcome.get("proposal")
    if raw_proposal is not None:
        try:
            if not isinstance(raw_proposal, Mapping):
                raise EvolutionError("Evolution proposal must be an object or null")
            proposal = _validate_public_proposal(raw_proposal)
        except Exception as exc:
            projected["proposal"] = None
            projected["proposal_created"] = False
            projected["no_reusable_lesson"] = False
            projected["evolution_completed"] = False
            projected["failure"] = {
                "type": type(exc).__name__,
                "message": "Evolution proposal failed the exact public schema",
            }
        else:
            projected["proposal"] = proposal
            projected["proposal_created"] = True
            projected["no_reusable_lesson"] = False
    elif projected.get("evolution_completed") is True:
        # Only the canonical runner authors this explicit interpretation of an
        # empty direct response.  Legacy/custom outcomes remain readable but do
        # not gain a no-lesson claim unless they retained it themselves.
        projected["no_reusable_lesson"] = outcome.get(
            "no_reusable_lesson"
        ) is True
    return projected


def _has_infrastructure_failure(*reports: Mapping[str, Any]) -> bool:
    return any(report.get("infrastructure_failure") is True for report in reports)


def _video_evidence_valid(
    report: Mapping[str, Any],
    *,
    fallback: Mapping[str, Any],
    fallback_complete_field: str = "video_complete",
) -> bool:
    required = report.get("video_required")
    if not isinstance(required, bool):
        required = fallback.get("video_required")
    if not isinstance(required, bool):
        return False
    if not required:
        return True
    complete = report.get("video_complete")
    if not isinstance(complete, bool):
        complete = fallback.get(fallback_complete_field)
    return complete is True


def _truthful_task_demo_not_run(
    cell: Mapping[str, Any], task_demo: Mapping[str, Any]
) -> bool:
    executed = cell.get("task_demo_executed")
    if not isinstance(executed, bool):
        executed = task_demo.get(
            "physical_validation_executed", task_demo.get("executed")
        )
    passed = cell.get("task_demo_passed")
    if not isinstance(passed, bool):
        passed = task_demo.get("validation_passed", task_demo.get("passed"))
    reason = task_demo.get("skip_reason")
    return (
        executed is False
        and passed is False
        and task_demo.get("skipped") is True
        and isinstance(reason, str)
        and bool(reason.strip())
    )


def _source_outcome_label(cell: Mapping[str, Any]) -> str:
    """Return a label only from complete, infrastructure-valid terminal evidence."""

    if cell.get("pipeline_completed") is not True:
        return "indeterminate"

    capability = cell.get("capability_validation")
    if not isinstance(capability, Mapping):
        return "indeterminate"
    task_demo = cell.get("task_demo")
    if not isinstance(task_demo, Mapping):
        return "indeterminate"
    if _has_infrastructure_failure(cell, capability, task_demo):
        return "indeterminate"
    if capability.get("pipeline_completed") is not True:
        return "indeterminate"
    if not _video_evidence_valid(capability, fallback=cell):
        return "indeterminate"
    if (
        cell.get("capability_validation_executed") is not True
        or capability.get("physical_validation_executed") is not True
    ):
        return "indeterminate"

    final_capability: bool | None = None
    for field in (
        "final_capability_validation_passed",
        "final_validation_passed",
        "validation_passed",
        "terminal_validation_passed",
    ):
        value = cell.get(field)
        if isinstance(value, bool):
            final_capability = value
            break
    if (
        not isinstance(capability.get("validation_passed"), bool)
        or capability["validation_passed"] is not final_capability
    ):
        return "indeterminate"
    if final_capability is False:
        return (
            "negative"
            if _truthful_task_demo_not_run(cell, task_demo)
            else "indeterminate"
        )
    if final_capability is not True:
        return "indeterminate"

    task_demo_executed = cell.get("task_demo_executed")
    if not isinstance(task_demo_executed, bool):
        task_demo_executed = task_demo.get(
            "physical_validation_executed", task_demo.get("executed")
        )
    task_demo_passed = cell.get("task_demo_passed")
    if not isinstance(task_demo_passed, bool):
        task_demo_passed = task_demo.get(
            "validation_passed", task_demo.get("passed")
        )
    task_demo_pipeline_completed = cell.get("task_demo_pipeline_completed")
    if not isinstance(task_demo_pipeline_completed, bool):
        task_demo_pipeline_completed = task_demo.get("pipeline_completed")
    if task_demo_executed is not True or task_demo_pipeline_completed is not True:
        return "indeterminate"
    if (
        task_demo.get("physical_validation_executed") is not True
        or task_demo.get("pipeline_completed") is not True
        or task_demo.get("validation_passed") is not task_demo_passed
    ):
        return "indeterminate"
    if not _video_evidence_valid(
        task_demo,
        fallback=cell,
        fallback_complete_field="task_demo_video_complete",
    ):
        return "indeterminate"
    if task_demo_executed is True and task_demo_passed is True:
        return "positive"
    if task_demo_executed is True and task_demo_passed is False:
        return "negative"
    # A passing capability check without an executed, determinate Task Demo is
    # not eligible for an accepted snapshot.
    return "indeterminate"


def _validate_review_queue_record(
    record: Mapping[str, Any], *, queue_run_id: str, index: int
) -> None:
    required = (
        "source_run_id",
        "cell_id",
        "robot_configuration_id",
        "source_robot",
        "generation_condition",
        "terminal_outcome_label",
    )
    if any(
        not isinstance(record.get(field), str) or not record[field].strip()
        for field in required
    ):
        raise EvolutionError(
            f"review queue.records[{index}] is missing canonical Framework labels"
        )
    if record["source_run_id"] != queue_run_id:
        raise EvolutionError(
            f"review queue.records[{index}] has mismatched source_run_id"
        )
    if record["source_robot"] != record["robot_configuration_id"]:
        raise EvolutionError(
            f"review queue.records[{index}] has conflicting source_robot"
        )
    expected_cell_id = (
        f"{record['robot_configuration_id']}::{record['generation_condition']}"
    )
    if record["cell_id"] != expected_cell_id:
        raise EvolutionError(
            f"review queue.records[{index}] has inconsistent cell_id"
        )
    if record["terminal_outcome_label"] not in {
        "positive",
        "negative",
        "indeterminate",
    }:
        raise EvolutionError(
            f"review queue.records[{index}] has invalid terminal_outcome_label"
        )
    aliases = {
        "source_condition": record["generation_condition"],
        "source_outcome": record["terminal_outcome_label"],
        "outcome_label": record["terminal_outcome_label"],
        "source_robot_configuration_id": record["robot_configuration_id"],
        "source_generation_condition": record["generation_condition"],
    }
    for field, expected in aliases.items():
        if field in record and record[field] != expected:
            raise EvolutionError(
                f"review queue.records[{index}] has conflicting {field}"
            )
    evolution = record.get("evolution")
    if isinstance(evolution, Mapping) and evolution.get("proposal") is not None:
        proposal = evolution.get("proposal")
        if not isinstance(proposal, Mapping):
            raise EvolutionError(
                f"review queue.records[{index}] has invalid Evolution proposal"
            )
        _validate_public_proposal(proposal)


def _validate_review_queue(queue: Mapping[str, Any]) -> tuple[str, list[Any]]:
    if (
        not isinstance(queue, Mapping)
        or queue.get("artifact_type") != "experience_review_queue"
    ):
        raise EvolutionError("queue must be an experience_review_queue object")
    run_id = queue.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise EvolutionError("review queue.run_id must be non-empty text")
    records = queue.get("records")
    if not isinstance(records, list):
        raise EvolutionError("queue.records must be a list")
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise EvolutionError(f"queue.records[{index}] must be an object")
        _validate_review_queue_record(record, queue_run_id=run_id, index=index)
        cell_id = str(record["cell_id"])
        if cell_id in seen:
            raise EvolutionError(f"queue contains duplicate cell {cell_id!r}")
        seen.add(cell_id)
    return run_id, records


def _validate_dispositioned_record(record: Mapping[str, Any]) -> None:
    disposition = record.get("disposition")
    if disposition not in _REVIEW_DECISIONS:
        raise EvolutionError(
            "every review queue record requires accept or reject disposition"
        )
    reason = record.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise EvolutionError("every Experience disposition requires a reason")


def build_experience_review_queue(
    *,
    run_id: str,
    experiment_id: str,
    expected_robots: Sequence[str],
    expected_conditions: Sequence[str],
    cells: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one deterministic, review-only queue from terminal public cell outcomes."""

    expected = [
        (str(robot), str(condition))
        for robot in expected_robots
        for condition in expected_conditions
    ]
    by_id: dict[str, Mapping[str, Any]] = {}
    for cell in cells:
        if not isinstance(cell, Mapping):
            continue
        cell_id = cell.get("cell_id")
        if isinstance(cell_id, str) and cell_id not in by_id:
            by_id[cell_id] = cell

    records: list[dict[str, Any]] = []
    retained = 0
    all_cells_present = True
    all_cells_completed = True
    for robot, condition in expected:
        cell_id = f"{robot}::{condition}"
        cell = by_id.get(cell_id)
        source_outcome = _source_outcome_label(cell) if cell is not None else "indeterminate"
        if cell is None:
            all_cells_present = False
            all_cells_completed = False
            evolution: dict[str, Any] | None = None
        else:
            all_cells_completed = all_cells_completed and bool(
                cell.get("pipeline_completed")
            )
            outcomes = cell.get("outcomes")
            raw_evolution = (
                outcomes.get("Evolution")
                if isinstance(outcomes, Mapping) and "Evolution" in outcomes
                else cell.get("evolution")
            )
            evolution = project_public_evolution_outcome(raw_evolution)
            retained += int(evolution is not None)
        records.append(
            {
                "source_run_id": run_id,
                "cell_id": cell_id,
                "robot_configuration_id": robot,
                # These labels are assigned from the Framework cell identity and terminal
                # verdict.  They are never copied from model proposal content.
                "source_robot": robot,
                "generation_condition": condition,
                "terminal_outcome_label": source_outcome,
                "source_condition": condition,
                "source_outcome": source_outcome,
                "outcome_label": source_outcome,
                "evolution": evolution,
                "disposition": None,
                "reason": None,
            }
        )

    expected_count = len(expected)
    return {
        "artifact_type": "experience_review_queue",
        "experiment_id": experiment_id,
        "run_id": run_id,
        "cell_pipeline_completed": all_cells_present and all_cells_completed,
        "expected_evolution_outcome_count": expected_count,
        "retained_evolution_outcome_count": retained,
        "all_evolution_outcomes_retained": retained == expected_count,
        "reviewed_disposition_count": 0,
        "dispositions_complete": False,
        "records": records,
    }


_REVIEW_FIELDS = frozenset({"disposition", "decision", "reason"})
_REVIEW_DECISIONS = frozenset({"accept", "reject"})


def _review_map(
    reviews: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Normalize the deliberately small human review input."""

    if isinstance(reviews, Mapping):
        items = []
        for cell_id, value in reviews.items():
            if not isinstance(cell_id, str) or not isinstance(value, Mapping):
                raise EvolutionError("review entries must map cell IDs to objects")
            items.append((cell_id, value))
    elif isinstance(reviews, Sequence) and not isinstance(reviews, (str, bytes)):
        items = []
        for index, value in enumerate(reviews):
            if not isinstance(value, Mapping):
                raise EvolutionError(f"reviews[{index}] must be an object")
            cell_id = value.get("cell_id")
            if not isinstance(cell_id, str) or not cell_id.strip():
                raise EvolutionError(f"reviews[{index}].cell_id must be non-empty text")
            items.append((cell_id, value))
    else:
        raise EvolutionError("reviews must be a cell-ID mapping or list")

    result: dict[str, Mapping[str, Any]] = {}
    for cell_id, value in items:
        if cell_id in result:
            raise EvolutionError(f"duplicate review for {cell_id!r}")
        unknown = sorted(str(key) for key in value if str(key) not in _REVIEW_FIELDS and str(key) != "cell_id")
        if unknown:
            raise EvolutionError(
                "human review accepts only disposition/decision and reason: "
                + ", ".join(unknown)
            )
        disposition = value.get("disposition", value.get("decision"))
        if "disposition" in value and "decision" in value and value["disposition"] != value["decision"]:
            raise EvolutionError("human review disposition and decision disagree")
        if not isinstance(disposition, str) or disposition.strip().lower() not in _REVIEW_DECISIONS:
            raise EvolutionError("human review disposition must be accept or reject")
        reason = value.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise EvolutionError("human review reason must be non-empty text when supplied")
        if reason is None:
            raise EvolutionError("every Experience disposition requires a reason")
        result[cell_id] = value
    return result


def apply_experience_review(
    queue: Mapping[str, Any],
    reviews: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply only human accept/reject decisions to a pending review queue.

    Proposal fields are intentionally not accepted in ``reviews``.  The proposal and the
    Framework-owned source labels remain unchanged while only ``disposition`` and ``reason``
    are filled in.
    """

    _, records = _validate_review_queue(queue)
    review_map = _review_map(reviews)
    result = copy.deepcopy(dict(queue))
    reviewed_records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_record in enumerate(records):
        if not isinstance(raw_record, Mapping):
            raise EvolutionError(f"queue.records[{index}] must be an object")
        cell_id = raw_record.get("cell_id")
        if not isinstance(cell_id, str) or not cell_id:
            raise EvolutionError(f"queue.records[{index}].cell_id must be non-empty text")
        seen.add(cell_id)
        record = copy.deepcopy(dict(raw_record))
        review = review_map.get(cell_id)
        existing_disposition = record.get("disposition")
        existing_reason = record.get("reason")
        if existing_disposition is not None:
            if existing_disposition not in _REVIEW_DECISIONS:
                raise EvolutionError(f"queue record {cell_id!r} has invalid disposition")
            if not isinstance(existing_reason, str) or not existing_reason.strip():
                raise EvolutionError(
                    f"queue record {cell_id!r} has a disposition without a reason"
                )
        elif existing_reason is not None:
            raise EvolutionError(
                f"queue record {cell_id!r} has a reason without a disposition"
            )
        if review is not None:
            disposition = str(review.get("disposition", review.get("decision"))).strip().lower()
            reason = review.get("reason")
            if existing_disposition is not None and (
                disposition != existing_disposition or reason != existing_reason
            ):
                raise EvolutionError(f"human disposition for {cell_id!r} is already frozen")
            if existing_disposition is None:
                record["disposition"] = disposition
                record["reason"] = copy.deepcopy(reason)
        reviewed_records.append(record)
    unknown_cells = sorted(set(review_map) - seen)
    if unknown_cells:
        raise EvolutionError("review references unknown cells: " + ", ".join(unknown_cells))
    result["records"] = reviewed_records
    reviewed_count = sum(record.get("disposition") in _REVIEW_DECISIONS for record in reviewed_records)
    result["reviewed_disposition_count"] = reviewed_count
    result["dispositions_complete"] = reviewed_count == len(reviewed_records)
    return result


def _snapshot_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    _validate_dispositioned_record(record)
    disposition = record.get("disposition")
    if disposition != "accept":
        return None
    evolution = record.get("evolution")
    proposal = evolution.get("proposal") if isinstance(evolution, Mapping) else None
    if not isinstance(proposal, Mapping):
        raise EvolutionError("an accepted cell must contain an Evolution proposal")
    source_run_id = record.get("source_run_id")
    cell_id = record.get("cell_id")
    source_robot = record.get("source_robot", record.get("robot_configuration_id"))
    generation_condition = record.get(
        "generation_condition",
        record.get("source_condition", record.get("source_generation_condition")),
    )
    terminal_outcome_label = record.get(
        "terminal_outcome_label",
        record.get("source_outcome", record.get("outcome_label")),
    )
    if not all(
        isinstance(value, str) and value.strip()
        for value in (
            source_run_id,
            cell_id,
            source_robot,
            generation_condition,
            terminal_outcome_label,
        )
    ):
        raise EvolutionError("accepted queue records must retain Framework source labels")
    if terminal_outcome_label not in {"positive", "negative"}:
        raise EvolutionError("accepted queue records require a determinate terminal outcome")
    # Private definition-bearing keys may be removed at the review-queue boundary, but
    # any remaining model-authored key is an exact-schema error rather than an editable
    # field that the Framework silently carries forward.
    public_proposal = {
        str(key): copy.deepcopy(value)
        for key, value in proposal.items()
        if not _is_queue_private_field(key)
    }
    public_proposal = _validate_public_proposal(public_proposal)
    result: dict[str, Any] = {
        "experience_id": f"{source_run_id}:{cell_id}",
        "reviewed": True,
        "review_decision": "accept",
        "review_reason": copy.deepcopy(record.get("reason")),
        "source_run_id": source_run_id,
        "source_robot": source_robot,
        "generation_condition": generation_condition,
        "terminal_outcome_label": terminal_outcome_label,
        "source_condition": generation_condition,
        "source_outcome": terminal_outcome_label,
        "source_robot_configuration_id": source_robot,
        "source_generation_condition": generation_condition,
        "outcome_label": terminal_outcome_label,
    }
    result.update(public_proposal)
    return result


def build_experience_snapshot(
    queue: Mapping[str, Any],
    reviews: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    *,
    snapshot_id: str | None = None,
    version: str = "1.0.0",
) -> dict[str, Any]:
    """Freeze accepted public proposals for the next independent run only."""

    reviewed_queue = (
        apply_experience_review(queue, reviews)
        if reviews is not None
        else copy.deepcopy(dict(queue))
    )
    run_id, records = _validate_review_queue(reviewed_queue)
    for record in records:
        assert isinstance(record, Mapping)
        _validate_dispositioned_record(record)
    if snapshot_id is None:
        snapshot_id = f"{run_id}-experience-v1"
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise EvolutionError("snapshot_id must be non-empty text")
    if not isinstance(version, str) or not version.strip():
        raise EvolutionError("snapshot version must be non-empty text")
    accepted: list[dict[str, Any]] = []
    for record in records:
        if record.get("disposition") == "accept" and record.get("source_run_id") != run_id:
            raise EvolutionError(
                "accepted queue record source_run_id must match review queue.run_id"
            )
        snapshot_record = _snapshot_record(record)
        if snapshot_record is not None:
            accepted.append(snapshot_record)
    snapshot = {
        "artifact_type": "autoadapter_experience_snapshot",
        "snapshot_id": snapshot_id,
        "version": version,
        "review_status": "reviewed",
        "usage_scope": "next_independent_run_only",
        "b1_input": False,
        "benefit_claim": False,
        "source_run_id": run_id,
        "records": accepted,
    }
    validate_experience_snapshot(snapshot)
    return snapshot


def load_experience_snapshot(path: str | Path) -> dict[str, Any]:
    """Read a frozen snapshot without turning it into a mutable review queue."""

    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvolutionError(f"cannot read Experience snapshot {source}") from exc
    if not isinstance(value, Mapping):
        raise EvolutionError("Experience snapshot must be an object")
    if value.get("artifact_type") != "autoadapter_experience_snapshot":
        raise EvolutionError("invalid Experience snapshot artifact type")
    if value.get("review_status") not in {"reviewed", "project_owner_reviewed"}:
        raise EvolutionError("Experience snapshot is not reviewed")
    usage_scope = value.get("usage_scope")
    if usage_scope not in {
        "next_independent_run_only",
        # Historical snapshots remain readable, but all new snapshots use the
        # explicit next-independent-run scope above.
        "later_matched_run_only",
    }:
        raise EvolutionError("Experience snapshot has invalid usage scope")
    if usage_scope == "next_independent_run_only" and value.get("review_status") != "reviewed":
        raise EvolutionError("Experience snapshot is not reviewed")
    snapshot_id = value.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise EvolutionError("Experience snapshot.snapshot_id must be non-empty text")
    records = value.get("records")
    if records is None and usage_scope == "later_matched_run_only":
        # Historical snapshots used one list per robot and remain readable for
        # compatibility; new snapshots always use the global records list.
        return copy.deepcopy(dict(value))
    if not isinstance(records, list):
        raise EvolutionError("Experience snapshot.records must be a list")
    if usage_scope == "next_independent_run_only":
        validate_experience_snapshot(value)
    return copy.deepcopy(dict(value))


def run_evolution(
    client: JsonGenerator,
    terminal_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Run one non-blocking Evolution proposal after a terminal verdict.

    The function always returns an outcome record for model, input, or proposal failures.  A
    caller can therefore attach the result to a terminal run without turning that run into a
    synthesis failure.  The supplied report is deep-copied before it reaches the model client.
    """

    result: dict[str, Any] = {
        "non_blocking": True,
        "terminal_report_read": False,
        "evolution_attempted": False,
        "evolution_completed": False,
        "proposal_created": False,
        "no_reusable_lesson": False,
        "proposal": None,
        "model_call_count": 0,
        "model_calls": [],
        "model_input_compacted": False,
        "model_input_chars": 0,
        "current_run_unchanged": True,
        "failure": None,
    }
    try:
        report = _validate_candidate_facing_report(terminal_report)
        result["terminal_report_read"] = True
    except Exception as exc:
        result["failure"] = _error(exc)
        return result

    original_report = copy.deepcopy(dict(terminal_report))
    model_report = _compact_terminal_report(report)
    result["model_input_compacted"] = True
    result["model_input_chars"] = len(
        json.dumps(model_report, ensure_ascii=True, sort_keys=True)
    )
    result["evolution_attempted"] = True
    result["model_call_count"] = 1
    call_start = _call_count(client)
    try:
        response = client.generate_json(
            stage="evolution",
            prompt=EVOLUTION_SYSTEM_PROMPT,
            inputs={"terminal_report": model_report},
        )
        proposal = _validate_proposal(response)
        # The client receives a copy, but this check makes the non-mutation guarantee explicit if
        # a custom test client is stateful or unexpectedly touches its caller's object.
        result["current_run_unchanged"] = dict(terminal_report) == original_report
        if not result["current_run_unchanged"]:
            raise EvolutionError("Evolution changed the terminal report")
        result["proposal"] = proposal
        result["proposal_created"] = proposal is not None
        result["no_reusable_lesson"] = proposal is None
        result["evolution_completed"] = True
    except Exception as exc:
        result["proposal"] = None
        result["proposal_created"] = False
        result["no_reusable_lesson"] = False
        result["failure"] = _error(exc)
    result["model_calls"] = _call_evidence(client, call_start)
    return result


# A descriptive alias for callers that prefer the phase name in orchestration code.
evolve_terminal_report = run_evolution

# Descriptive aliases keep the review boundary discoverable without adding another workflow
# abstraction.  All aliases retain the same accept/reject-only semantics above.
review_experience_queue = apply_experience_review
freeze_experience_snapshot = build_experience_snapshot


__all__ = [
    "build_experience_review_queue",
    "build_experience_snapshot",
    "EVOLUTION_SYSTEM_PROMPT",
    "EvolutionError",
    "JsonGenerator",
    "apply_experience_review",
    "evolve_terminal_report",
    "freeze_experience_snapshot",
    "load_experience_snapshot",
    "project_public_evolution_outcome",
    "review_experience_queue",
    "run_evolution",
    "validate_experience_snapshot",
]
