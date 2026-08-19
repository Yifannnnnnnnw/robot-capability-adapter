"""Small terminal Evolution sidecar for the Demo3 experiment.

Evolution is deliberately kept outside the validation path.  It consumes one
already terminal, candidate-facing report and can return at most one proposal
for a later Experience record.  The proposal is data only; this module never
writes a candidate, suite, criterion, verdict, retry decision, or run input.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
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

Return exactly one JSON object with this shape:
{"proposal": null} or
{"proposal": {"observation": "...", "lesson": "...", "recommendation": "...",
"scope": "...", "evidence": ["..."]}}

The proposal must describe a possible future Experience record only.  It must never contain an
instruction to mutate the current run.  Keep evidence tied to facts present in the report."""


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
    # older Harness name is accepted so the sidecar can consume the current Demo3 report while
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


def _compact_trial(trial: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        str(key): copy.deepcopy(value)
        for key, value in trial.items()
        if key not in {"physical_evidence", "video"}
    }
    physical = trial.get("physical_evidence")
    if isinstance(physical, Mapping):
        samples = physical.get("samples")
        result["physical_evidence"] = {
            str(key): copy.deepcopy(value)
            for key, value in physical.items()
            if key != "samples"
        }
        result["physical_evidence"]["sample_count"] = (
            len(samples) if isinstance(samples, list) else 0
        )
    video = trial.get("video")
    if isinstance(video, Mapping):
        result["video"] = {
            str(key): copy.deepcopy(value)
            for key, value in video.items()
            if key != "path"
        }
    return result


def _compact_validation(validation: Mapping[str, Any]) -> dict[str, Any]:
    trials = validation.get("trials")
    trial_items = (
        [item for item in trials if isinstance(item, Mapping)]
        if isinstance(trials, list)
        else []
    )
    result = {
        str(key): copy.deepcopy(value)
        for key, value in validation.items()
        if key
        not in {
            "attempts",
            "trials",
            "video_manifest",
            "outcomes",
            "evolution",
            "capability_validation",
            "task_demo",
            "task_demo_trials",
            "task_demo_video_manifest",
        }
    }
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
    """Keep terminal facts and failed diagnostics without repeated frame samples."""

    result = {
        str(key): copy.deepcopy(value)
        for key, value in report.items()
        if key not in {"attempts", "trials", "video_manifest", "outcomes", "evolution"}
    }
    attempts = report.get("attempts")
    compact_attempts: list[dict[str, Any]] = []
    if isinstance(attempts, list):
        for attempt in attempts:
            if not isinstance(attempt, Mapping):
                continue
            compact = {
                str(key): copy.deepcopy(value)
                for key, value in attempt.items()
                if key not in {"validation", "capability_validation"}
            }
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
    outcomes = report.get("outcomes")
    if isinstance(outcomes, Mapping):
        result["stage_outcomes"] = {
            str(key): copy.deepcopy(value)
            for key, value in outcomes.items()
            if key not in {"Validation", "CapabilityValidation", "TaskDemo"}
        }
    return result


def _validate_proposal(response: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(response, Mapping):
        raise EvolutionError("Evolution model response must be an object")
    if "proposal" not in response:
        raise EvolutionError("Evolution model response must contain proposal")
    proposal = response["proposal"]
    if proposal is None:
        return None
    if not isinstance(proposal, Mapping):
        raise EvolutionError("Evolution proposal must be an object or null")
    field_names = set(_walk_field_names(proposal))
    forbidden = sorted(field_names & _CURRENT_RUN_MUTATION_FIELDS)
    if forbidden:
        raise EvolutionError(
            "Evolution proposal attempts to mutate the current run: " + ", ".join(forbidden)
        )
    return copy.deepcopy(dict(proposal))


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
        result["evolution_completed"] = True
    except Exception as exc:
        result["proposal"] = None
        result["proposal_created"] = False
        result["failure"] = _error(exc)
    result["model_calls"] = _call_evidence(client, call_start)
    return result


# A descriptive alias for callers that prefer the phase name in orchestration code.
evolve_terminal_report = run_evolution


__all__ = [
    "EVOLUTION_SYSTEM_PROMPT",
    "EvolutionError",
    "JsonGenerator",
    "evolve_terminal_report",
    "run_evolution",
]
