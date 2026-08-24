#!/usr/bin/env python3
"""Zero-model preflight and evidence gate for the DeepSeek SO-101 canary.

This file deliberately does not run the pipeline.  ``python -m autoadapter2
full`` remains the only execution entry point; this checker only verifies the
dedicated source-run configuration or reads artifacts after that run ends.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from autoadapter2.libraries import load_indexed_robot_package
from autoadapter2.pipeline import ExperimentConfig, check_packages
from autoadapter2.validation_compiler import load_sanitized_ivc_examples


ROBOT_ID = "robotstudio_so101"
PACKAGE_VERSION = "1.0.4"
CONDITION = "skeleton-assisted"
MODEL_ID = "deepseek-v4-pro"
EXPERIMENT_ID = "autoadapter2-diagnostic-deepseek-so101-1.0.4"
EXPECTED_PHASE_TURNS = {
    "study": 16,
    "tgcd": 6,
    "ivc": 6,
    "generate_skeleton": 22,
    "generate_from_scratch": 40,
    "repair_skeleton": 22,
    "repair_from_scratch": 20,
}
EXPECTED_PROPOSAL_FIELDS = {
    "observation",
    "lesson",
    "recommendation",
    "scope",
    "public_evidence",
}
SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_ROOT = SCRIPT_PATH.parents[1]
DEFAULT_CONFIG = (
    DEFAULT_ROOT / "configs" / "experiments" / "deepseek-so101-1.0.4-canary.json"
)


class CanaryCheckError(RuntimeError):
    """Raised when a diagnostic canary requirement lacks concrete evidence."""


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise CanaryCheckError(message)


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanaryCheckError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise CanaryCheckError(f"{label} must contain one JSON object: {path}")
    return value


def _records(document: Mapping[str, Any], field: str) -> list[Mapping[str, Any]]:
    value = document.get(field)
    _require(
        isinstance(value, list) and all(isinstance(item, Mapping) for item in value),
        f"{field} must be an object array",
    )
    return list(value)


def _validate_source_config(path: Path) -> ExperimentConfig:
    raw = _read_object(path, label="DeepSeek canary config")
    config = ExperimentConfig.from_path(path)
    _require(config.experiment_id == EXPERIMENT_ID, "unexpected canary experiment_id")
    _require(config.robots == (ROBOT_ID,), "canary must select only SO-101")
    _require(
        config.generation_conditions == (CONDITION,),
        "canary must be skeleton-assisted only",
    )
    _require(config.formal is False, "DeepSeek canary must remain formal=false")
    _require(
        dict(config.phase_turn_budgets) == EXPECTED_PHASE_TURNS,
        "canary phase-turn budgets differ from the approved workflow",
    )
    _require(
        config.max_driver_attempts_per_condition == 3,
        "canary must allow at most three frozen Driver attempts",
    )
    _require(
        config.recap_max_planning_turns_per_task == 16
        and config.recap_max_capability_calls_per_task == 12,
        "canary ReCAP budget must be 16 planning turns / 12 capability calls",
    )
    _require(config.record_video is True, "canary must record Task Demo video")
    _require(config.experience_declared, "canary must explicitly declare Experience")
    _require(not config.experience_input, "source canary must start with empty Experience")
    _require(
        config.experience_snapshot_input is None,
        "source canary must not load a reviewed later-run snapshot",
    )
    _require(
        config.evolution_declared
        and config.evolution_enabled
        and config.evolution_max_attempts == 1,
        "source canary must run exactly one terminal Evolution attempt",
    )
    _require(
        config.evolution_model_manifest is None,
        "source canary must use the same DeepSeek client for Evolution",
    )
    model = config.model_manifest
    _require(isinstance(model, Mapping), "canary must pin one DeepSeek model manifest")
    _require(str(model.get("vendor", "")).lower() == "deepseek", "vendor must be DeepSeek")
    _require(model.get("model_id") == MODEL_ID, "unexpected DeepSeek deployment ID")
    _require(
        model.get("base_url") == "https://api.deepseek.com",
        "canary must use the configured DeepSeek endpoint",
    )
    _require(
        model.get("api_protocol") == "openai-compatible"
        and model.get("tool_history_mode") == "native",
        "canary requires the unified native tool-use model path",
    )
    _require(raw.get("formal") is False, "config must explicitly record formal=false")
    return config


def check_preflight(*, root: Path, config_path: Path) -> dict[str, Any]:
    """Validate the config, indexed package and private metric closure without a model."""

    config = _validate_source_config(config_path)
    package_check = check_packages(root, config=config)
    robot = package_check.get("robots", {}).get(ROBOT_ID, {})
    _require(robot.get("package_version") == PACKAGE_VERSION, "index must resolve SO-101 1.0.4")

    index = _read_object(root / "libraries" / "robots" / "index.json", label="robot index")
    _require(
        index.get("robots", {}).get(ROBOT_ID) == f"{ROBOT_ID}/{PACKAGE_VERSION}",
        "SO-101 index entry does not point to 1.0.4",
    )
    package = load_indexed_robot_package(root, ROBOT_ID)
    observations = package.morphology.get("public_observations")
    _require(isinstance(observations, Mapping) and observations, "SO-101 has no public observations")
    _require(package.reference_driver.is_file(), "SO-101 reference Driver is missing")
    _require(any(package.skeleton_dir.glob("*.py")), "SO-101 skeleton is missing")

    bindings_document = _read_object(
        package.private_dir / "bindings.json", label="SO-101 private bindings"
    )
    bindings = _records(bindings_document, "bindings")
    binding_metrics = {
        str(item["metric"])
        for item in bindings
        if isinstance(item.get("metric"), str) and item.get("metric")
    }
    task_metrics = {
        str(clause["metric"])
        for task in package.tasks
        for clause in task.get("scoring", [])
        if isinstance(clause, Mapping)
        and isinstance(clause.get("metric"), str)
        and clause.get("metric")
    }
    missing_metrics = sorted(task_metrics - binding_metrics)
    _require(
        not missing_metrics,
        "public task criteria lack private metric bindings: " + ", ".join(missing_metrics),
    )
    examples = load_sanitized_ivc_examples()
    _require(bool(examples), "sanitized IVC examples are missing")

    return {
        "check": "deepseek_so101_source_canary_preflight",
        "passed": True,
        "formal": False,
        "model_client_constructed": False,
        "model_requests": 0,
        "experiment_id": config.experiment_id,
        "robot_configuration_id": ROBOT_ID,
        "indexed_package_version": package.package_version,
        "generation_condition": CONDITION,
        "phase_turn_budgets": dict(config.phase_turn_budgets),
        "frozen_driver_attempt_limit": config.max_driver_attempts_per_condition,
        "recap_budget": {
            "planning_turns_per_task": config.recap_max_planning_turns_per_task,
            "capability_calls_per_task": config.recap_max_capability_calls_per_task,
        },
        "evolution_attempt_limit": config.evolution_max_attempts,
        "empty_experience": not config.experience_input,
        "task_metric_count": len(task_metrics),
        "private_binding_metric_count": len(binding_metrics),
        "sanitized_ivc_example_count": len(examples),
        "credential_environment": "AUTOADAPTER_MODEL_API_KEY",
    }


def _successful_call_models(values: Sequence[Mapping[str, Any]]) -> list[str]:
    models: list[str] = []
    for call in values:
        requested = call.get("requested_model")
        _require(requested == MODEL_ID, "a canary call requested a non-DeepSeek deployment")
        returned = call.get("returned_model")
        if call.get("status") == "success" or "status" not in call:
            _require(returned == MODEL_ID, "a successful canary call returned a different model")
            models.append(str(returned))
    return models


def _passed_capabilities(
    suite: Mapping[str, Any], validation: Mapping[str, Any]
) -> tuple[str, ...]:
    trials_by_case: dict[str, list[bool]] = defaultdict(list)
    for trial in validation.get("trials", []):
        if isinstance(trial, Mapping) and isinstance(trial.get("case_id"), str):
            trials_by_case[str(trial["case_id"])].append(trial.get("trial_passed") is True)
    roles: dict[str, dict[str, bool]] = defaultdict(dict)
    for case in suite.get("cases", []):
        if not isinstance(case, Mapping):
            continue
        case_id = case.get("case_id")
        capability_id = case.get("capability_id")
        role = case.get("case_role")
        if (
            isinstance(case_id, str)
            and isinstance(capability_id, str)
            and role in {"nominal", "calibrated_boundary"}
        ):
            outcomes = trials_by_case.get(case_id, [])
            roles[capability_id][str(role)] = bool(outcomes) and all(outcomes)
    return tuple(
        sorted(
            capability_id
            for capability_id, outcomes in roles.items()
            if outcomes.get("nominal") is True
            and outcomes.get("calibrated_boundary") is True
        )
    )


def _validate_video(video: Mapping[str, Any], *, label: str) -> None:
    _require(video.get("requested") is True, f"{label} video was not requested")
    _require(video.get("complete") is True, f"{label} video is incomplete")
    path = video.get("path")
    _require(isinstance(path, str) and path, f"{label} video path is missing")
    video_path = Path(path)
    _require(video_path.is_file() and video_path.stat().st_size > 0, f"{label} video file is missing")


def check_evidence(*, run_dir: Path, config_path: Path) -> dict[str, Any]:
    """Require the approved partial-closure canary evidence and return its proposal."""

    config = _validate_source_config(config_path)
    report = _read_object(run_dir / "experiment_report.json", label="experiment report")
    _require(report.get("experiment_id") == config.experiment_id, "report uses another experiment")
    configuration = report.get("configuration")
    _require(isinstance(configuration, Mapping), "report has no frozen configuration")
    _require(configuration.get("formal") is False, "reported run is not diagnostic")
    _require(configuration.get("robots") == [ROBOT_ID], "reported run uses another robot")
    _require(
        configuration.get("generation_conditions") == [CONDITION],
        "reported run uses another generation condition",
    )
    _require(report.get("reference_calibration_skipped") is False, "IVC positive control was skipped")
    _require(report.get("reference_calibration_passed") is True, "IVC positive control did not pass")
    _require(report.get("pipeline_completed") is True, "source canary did not complete its physical pipeline")
    for field in ("producer_model", "evolution_model"):
        identity = report.get(field)
        _require(isinstance(identity, Mapping), f"report.{field} identity is missing")
        _require(
            str(identity.get("provider", "")).lower() == "deepseek"
            and identity.get("model") == MODEL_ID,
            f"report.{field} is not the pinned DeepSeek deployment",
        )

    cells = report.get("cells")
    _require(isinstance(cells, list) and len(cells) == 1, "canary must contain exactly one cell")
    public_cell = cells[0]
    _require(isinstance(public_cell, Mapping), "canary cell is malformed")
    _require(public_cell.get("robot_package_version") == PACKAGE_VERSION, "cell did not use SO-101 1.0.4")
    _require(public_cell.get("dynamic_model_called") is True, "model path did not execute")

    workspace = run_dir / "cells" / ROBOT_ID / CONDITION
    cell = _read_object(workspace / "cell_report.json", label="raw canary cell report")
    _require(cell.get("robot_package_version") == PACKAGE_VERSION, "raw cell package mismatch")
    attempts = cell.get("attempts")
    _require(isinstance(attempts, list) and 1 <= len(attempts) <= 3, "expected one to three frozen Drivers")
    _require(
        all(isinstance(item, Mapping) and item.get("driver_frozen") is True for item in attempts),
        "an invalid Driver was counted as a frozen attempt",
    )

    stage_records = [
        item for item in report.get("stage_evidence", []) if isinstance(item, Mapping)
    ]
    stage_names = [str(item.get("stage")) for item in stage_records]
    ordered = ["study", "tgcd", "ivc", "generate", "task_demo_recap"]
    positions: list[int] = []
    for stage in ordered:
        _require(stage in stage_names, f"missing real {stage} stage evidence")
        positions.append(stage_names.index(stage))
    _require(positions == sorted(positions), "canary stages are out of order")

    observed_models: list[str] = []
    for stage in stage_records:
        calls = stage.get("model_calls", [])
        if isinstance(calls, list):
            observed_models.extend(
                _successful_call_models([item for item in calls if isinstance(item, Mapping)])
            )
    private_ivc_trace = _read_object(
        workspace / "private" / "ivc_artifact_trace.json",
        label="private IVC artifact trace",
    )
    private_ivc_calls = private_ivc_trace.get("model_calls")
    _require(isinstance(private_ivc_calls, list) and private_ivc_calls, "private IVC model calls are missing")
    observed_models.extend(
        _successful_call_models(
            [item for item in private_ivc_calls if isinstance(item, Mapping)]
        )
    )

    design = _read_object(
        workspace / "design" / "capability_design.json", label="sealed capability design"
    )
    design_capabilities = design.get("capabilities")
    _require(
        isinstance(design_capabilities, list) and 3 <= len(design_capabilities) <= 10,
        "TGCD capability count is invalid",
    )
    suite = _read_object(
        workspace / "private" / "capability_validation_suite.json",
        label="sealed IVC suite",
    )
    suite_cases = suite.get("cases")
    _require(
        isinstance(suite_cases, list)
        and len(suite_cases) == 2 * len(design_capabilities),
        "IVC suite does not contain exactly nominal+boundary per capability",
    )
    references = report.get("references")
    _require(isinstance(references, Mapping), "cell reference evidence is missing")
    reference = references.get(f"{ROBOT_ID}::{CONDITION}", {})
    _require(isinstance(reference, Mapping) and reference.get("passed") is True, "cell reference control failed")

    validation = cell.get("capability_validation")
    _require(isinstance(validation, Mapping), "candidate capability Harness report is missing")
    passed = _passed_capabilities(suite, validation)
    whitelist = tuple(sorted(str(item) for item in cell.get("passed_capability_whitelist", [])))
    _require(passed, "no capability passed both nominal and calibrated-boundary cases")
    _require(whitelist == passed, "ReCAP whitelist differs from the double-passed capability set")

    task_design = _read_object(
        workspace / "task-demo" / "capability_design.json", label="ReCAP capability design"
    )
    task_capabilities = [
        item for item in task_design.get("capabilities", []) if isinstance(item, Mapping)
    ]
    task_ids = tuple(sorted(str(item.get("capability_id")) for item in task_capabilities))
    _require(task_ids == passed, "ReCAP was exposed to capabilities outside the whitelist")
    allowed_methods = {
        str(item.get("method_name"))
        for item in task_capabilities
        if isinstance(item.get("method_name"), str)
    }

    task_demo = _read_object(
        workspace / "task-demo" / "task_demo_report.json", label="Task Demo report"
    )
    _require(task_demo.get("pipeline_completed") is True, "Task Demo did not reach a Harness verdict")
    _require(
        task_demo.get("physical_validation_executed") is True,
        "Task Demo lacks complete real-physics execution",
    )
    _require(task_demo.get("video_complete") is True, "Task Demo video evidence is incomplete")
    controller_summary = task_demo.get("high_level_controller")
    _require(isinstance(controller_summary, Mapping), "ReCAP controller summary is missing")
    _require(controller_summary.get("kind") == "task_demo_recap", "Task Demo did not use canonical ReCAP")
    _require(
        isinstance(controller_summary.get("capability_call_count"), int)
        and int(controller_summary["capability_call_count"]) >= 1,
        "ReCAP did not make a real capability call",
    )

    real_calls = 0
    task_trials = task_demo.get("trials")
    _require(isinstance(task_trials, list) and task_trials, "Task Demo has no trusted trials")
    for index, trial in enumerate(task_trials):
        _require(isinstance(trial, Mapping), f"Task Demo trial {index} is malformed")
        _require(trial.get("physical_execution_passed") is True, f"Task Demo trial {index} did not execute")
        harness = trial.get("harness")
        _require(isinstance(harness, Mapping), f"Task Demo trial {index} lacks trusted Harness output")
        _require(
            harness.get("physical_harness_verdict") in {"PASS", "FAIL"},
            f"Task Demo trial {index} lacks a determinate Harness verdict",
        )
        video = trial.get("video")
        _require(isinstance(video, Mapping), f"Task Demo trial {index} lacks video metadata")
        _validate_video(video, label=f"Task Demo trial {index}")
        controller = trial.get("controller")
        _require(isinstance(controller, Mapping), f"Task Demo trial {index} lacks ReCAP trace")
        planning_turns = controller.get("planning_turns", controller.get("model_turns"))
        capability_calls = controller.get("capability_calls", controller.get("tool_calls"))
        _require(isinstance(planning_turns, int) and 1 <= planning_turns <= 16, "ReCAP turn budget violated")
        _require(isinstance(capability_calls, int) and 0 <= capability_calls <= 12, "ReCAP call budget violated")
        for event in controller.get("trace", []):
            if not isinstance(event, Mapping) or event.get("action_kind") != "capability":
                continue
            method = event.get("capability_name")
            _require(method in allowed_methods, "ReCAP trace called a non-whitelisted capability")
            if event.get("capability_execution_outcome") == "EXECUTED":
                real_calls += 1
    _require(real_calls >= 1, "ReCAP trace has no successfully executed capability invocation")

    outcomes = cell.get("outcomes")
    evolution = outcomes.get("Evolution") if isinstance(outcomes, Mapping) else cell.get("evolution")
    _require(isinstance(evolution, Mapping), "terminal Evolution evidence is missing")
    _require(evolution.get("evolution_attempted") is True, "Evolution was not attempted")
    _require(evolution.get("evolution_completed") is True, "Evolution did not complete")
    proposal = evolution.get("proposal")
    _require(isinstance(proposal, Mapping), "Evolution produced no reviewable proposal")
    _require(set(proposal) == EXPECTED_PROPOSAL_FIELDS, "Evolution proposal schema is not exact")
    _require(
        all(isinstance(proposal.get(field), str) and str(proposal[field]).strip() for field in EXPECTED_PROPOSAL_FIELDS - {"public_evidence"}),
        "Evolution proposal contains an empty text field",
    )
    public_evidence = proposal.get("public_evidence")
    _require(
        isinstance(public_evidence, list)
        and public_evidence
        and all(isinstance(item, str) and item.strip() for item in public_evidence),
        "Evolution public_evidence must be a non-empty text list",
    )
    evolution_calls = evolution.get("model_calls", [])
    _require(isinstance(evolution_calls, list) and len(evolution_calls) == 1, "Evolution must use one model call")
    observed_models.extend(
        _successful_call_models([item for item in evolution_calls if isinstance(item, Mapping)])
    )
    _require(observed_models and set(observed_models) == {MODEL_ID}, "canary evidence includes another LLM")

    queue = _read_object(run_dir / "experience_review_queue.json", label="Experience review queue")
    queue_records = queue.get("records")
    _require(isinstance(queue_records, list) and len(queue_records) == 1, "review queue must have one source cell")
    queue_record = queue_records[0]
    _require(isinstance(queue_record, Mapping), "review queue record is malformed")
    _require(
        queue_record.get("disposition") is None and queue_record.get("reason") is None,
        "source-run Experience was dispositioned without the user's decision",
    )
    _require(queue.get("dispositions_complete") is False, "source review queue must remain pending")
    _require(
        not (run_dir / "experience_snapshot.json").exists(),
        "a later-run Experience snapshot was created before human disposition",
    )

    return {
        "check": "deepseek_so101_source_canary_evidence",
        "passed": True,
        "formal": False,
        "run_id": report.get("run_id"),
        "robot_configuration_id": ROBOT_ID,
        "package_version": PACKAGE_VERSION,
        "generation_condition": CONDITION,
        "frozen_driver_attempt_count": len(attempts),
        "nominal_boundary_passed_capabilities": list(passed),
        "recap_real_capability_calls": real_calls,
        "task_demo_verdict": "PASS" if task_demo.get("validation_passed") is True else "FAIL",
        "task_demo_video_complete": True,
        "evolution_proposal": dict(proposal),
        "human_disposition_required": True,
        "later_run_started": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight", help="run the zero-model config/package gate")
    preflight.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    preflight.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    evidence = subparsers.add_parser("evidence", help="audit a completed source-run directory")
    evidence.add_argument("--run", type=Path, required=True)
    evidence.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "preflight":
            result = check_preflight(root=args.root.resolve(), config_path=args.config.resolve())
        else:
            result = check_evidence(run_dir=args.run.resolve(), config_path=args.config.resolve())
    except (CanaryCheckError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"passed": False, "error": {"type": type(exc).__name__, "message": str(exc)}},
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
