#!/usr/bin/env python3
"""Calibrate both fixed reference drivers through the B2 typed-adapter path."""

from __future__ import annotations

import argparse
import copy
import json
import math
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from build_suites import (
    EXPECTED_CAPABILITY_COUNTS,
    EXPECTED_CASE_COUNTS,
    REPOSITORY_ROOT,
    load_selection,
    validate_resolved,
)


def _mainline() -> dict[str, Any]:
    source_root = REPOSITORY_ROOT / "autoadapter" / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

    from autoadapter2.b2.recap import RecapBudgets
    from autoadapter2.b2.public_observation import PUBLIC_STATE_PROFILE_REVISION
    from autoadapter2.b2.session_runner import (
        RecapWorkerSessionConfig,
        run_recap_worker_session,
    )
    from autoadapter2.driver_synthesis import audit_driver_source
    from autoadapter2.harness.measurements import evaluate_guards, evaluate_temporal
    from autoadapter2.harness.runner import (
        _contact_integrity,
        _physical_execution_completed,
        _resolve_b1_body_geom_symbols,
    )
    from autoadapter2.libraries import load_robot_package

    return locals()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _git_state(selections: Sequence[Mapping[str, Any]]) -> tuple[str, list[str]]:
    revision_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    revision = revision_result.stdout.strip()
    if revision_result.returncode != 0 or not revision:
        raise RuntimeError("cannot resolve the calibration code revision")
    relevant_paths = [
        "experiment/b2_recap/reference_validation",
        "autoadapter/src/autoadapter2/b2",
        "autoadapter/src/autoadapter2/harness",
        "autoadapter/src/autoadapter2/trusted_skeletons",
    ]
    for selection in selections:
        relevant_paths.extend(
            [
                str(selection["driver_path"]),
                f"{selection['package_root']}/assets",
            ]
        )
    status_result = subprocess.run(
        ["git", "status", "--porcelain", "--", *relevant_paths],
        cwd=REPOSITORY_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if status_result.returncode != 0:
        raise RuntimeError("cannot inspect the calibration input state")
    changes = [line for line in status_result.stdout.splitlines() if line.strip()]
    return revision, changes


def _leaf(method_name: str, request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reasoning_summary": "Execute the next fixed calibration request.",
        "subtasks": [
            {
                "kind": "capability",
                "capability_name": method_name,
                "request": copy.deepcopy(dict(request)),
            }
        ],
    }


class _ScriptedCalibrationModel:
    """Deterministically transports fixed requests through the ReCAP adapter."""

    def __init__(self, method_name: str, requests: Sequence[Mapping[str, Any]]) -> None:
        self.responses = [_leaf(method_name, request) for request in requests]
        self.responses.append(
            {"reasoning_summary": "Calibration sequence complete.", "subtasks": []}
        )
        self.call_count = 0

    def generate_recap_json(self, **_: Any) -> dict[str, Any]:
        if self.call_count >= len(self.responses):
            raise RuntimeError("unexpected extra calibration model call")
        response = copy.deepcopy(self.responses[self.call_count])
        self.call_count += 1
        return response


def _requests(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    sequence = case.get("validation_request_sequence")
    values: Any = sequence if sequence is not None else [case.get("request")]
    if not isinstance(values, list) or not values or not all(
        isinstance(value, Mapping) for value in values
    ):
        raise ValueError(f"{case.get('case_id')} has an invalid request sequence")
    return [copy.deepcopy(dict(value)) for value in values]


def _video_evidence_complete(video: Mapping[str, Any]) -> bool:
    return video.get("requested") is True and video.get("complete") is True


def _prerequisite_passed(
    *,
    record_video: bool,
    complete_robot_cohort: bool,
    calibration_inputs_clean: bool,
    summaries: Sequence[Mapping[str, Any]],
) -> bool:
    return (
        record_video
        and complete_robot_cohort
        and calibration_inputs_clean
        and bool(summaries)
        and all(value.get("validation_passed") is True for value in summaries)
    )


def _invocations(
    worker: Mapping[str, Any], method_name: str, expected_count: int
) -> list[dict[str, Any]]:
    values = worker.get("capability_invocations")
    if not isinstance(values, list) or len(values) != expected_count:
        raise ValueError("worker did not retain every invocation boundary")
    result: list[dict[str, Any]] = []
    prior_end = -math.inf
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise ValueError(f"invocation {index} is invalid")
        start = value.get("start_sim_time_s")
        end = value.get("end_sim_time_s")
        if (
            value.get("method_name") != method_name
            or value.get("success") is not True
            or not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not isinstance(end, (int, float))
            or isinstance(end, bool)
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or float(start) < prior_end - 1.0e-12
            or float(end) <= float(start)
        ):
            raise ValueError(f"invocation {index} did not complete cleanly")
        result.append(dict(value))
        prior_end = float(end)
    return result


def _segment(
    evidence: Mapping[str, Any], start_time_s: float, end_time_s: float
) -> dict[str, Any]:
    raw_samples = evidence.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples or not all(
        isinstance(sample, Mapping) for sample in raw_samples
    ):
        raise ValueError("worker evidence has no trusted samples")
    before = [
        sample for sample in raw_samples if float(sample["time"]) <= start_time_s
    ]
    within = [
        sample
        for sample in raw_samples
        if start_time_s < float(sample["time"]) <= end_time_s + 1.0e-9
    ]
    selected = ([before[-1]] if before else []) + within
    if len(selected) < 2:
        raise ValueError("invocation has insufficient sampled evidence")
    result = dict(evidence)
    result["samples"] = selected
    return result


def _a3_directions(
    evidence: Mapping[str, Any], parameters: Mapping[str, Any]
) -> dict[str, Any]:
    samples = evidence.get("samples")
    joint_name = str(parameters["joint_name"])
    closed = float(parameters["closed_position"])
    opened = float(parameters["open_position"])
    if not isinstance(samples, list) or len(samples) < 2 or opened == closed:
        raise ValueError("A3 trusted samples or aperture limits are invalid")
    fractions = []
    for sample in samples:
        positions = sample.get("joint_positions") if isinstance(sample, Mapping) else None
        if not isinstance(positions, Mapping) or joint_name not in positions:
            raise ValueError("A3 gripper joint is absent from trusted evidence")
        fractions.append((float(positions[joint_name]) - closed) / (opened - closed))
    low = high = fractions[0]
    rise = fall = 0.0
    for value in fractions[1:]:
        rise = max(rise, value - low)
        fall = max(fall, high - value)
        low = min(low, value)
        high = max(high, value)
    return {
        "opening_excursion_fraction": rise,
        "closing_excursion_fraction": fall,
        "minimum_each_direction_fraction": 0.5,
        "passed": rise + 1.0e-9 >= 0.5 and fall + 1.0e-9 >= 0.5,
    }


def _metric(
    *,
    case: Mapping[str, Any],
    binding: Mapping[str, Any],
    worker: Mapping[str, Any],
    controller: Mapping[str, Any],
    requests: Sequence[Mapping[str, Any]],
    mainline: Mapping[str, Any],
) -> tuple[bool, list[dict[str, Any]], dict[str, Any] | None]:
    evidence = worker.get("physical_evidence")
    criterion = case.get("criterion")
    if not isinstance(evidence, Mapping) or not isinstance(criterion, Mapping):
        raise ValueError("case has no criterion or physical evidence")
    boundaries = _invocations(worker, str(case["method_name"]), len(requests))
    temporal_results = []
    for request, boundary in zip(requests, boundaries):
        result = dict(
            mainline["evaluate_temporal"](
                binding,
                criterion=criterion,
                evidence=_segment(
                    evidence,
                    float(boundary["start_sim_time_s"]),
                    float(boundary["end_sim_time_s"]),
                ),
                public_arguments={"request": dict(request)},
            )
        )
        duration_s = float(boundary["end_sim_time_s"]) - float(
            boundary["start_sim_time_s"]
        )
        request_budget_s = request.get("max_duration_s")
        duration_passed = True
        if request_budget_s is not None:
            duration_passed = duration_s <= float(request_budget_s) + 1.0e-9
        result.update(
            {
                "invocation_duration_s": duration_s,
                "request_max_duration_s": request_budget_s,
                "invocation_duration_passed": duration_passed,
            }
        )
        temporal_results.append(result)
    directions = None
    if case.get("capability_id") == "A3":
        parameters = binding.get("parameters")
        if not isinstance(parameters, Mapping):
            raise ValueError("A3 binding parameters are invalid")
        directions = _a3_directions(evidence, parameters)
    controller_passed = (
        controller.get("status") == "CONTROLLER_FINISHED"
        and controller.get("capability_calls") == len(requests)
        and controller.get("model_calls") == len(requests) + 1
    )
    passed = (
        controller_passed
        and all(
            bool(result.get("passed"))
            and result.get("value") == 1.0
            and bool(result.get("invocation_duration_passed"))
            for result in temporal_results
        )
        and (directions is None or bool(directions["passed"]))
    )
    return passed, temporal_results, directions


def _run_robot(
    *,
    selection: Mapping[str, Any],
    output_dir: Path,
    record_video: bool,
    wall_timeout_s: float,
    run_id: str,
    attempt: int,
    code_revision: str,
    calibration_inputs_clean: bool,
    calibration_input_changes: Sequence[str],
    mainline: Mapping[str, Any],
) -> dict[str, Any]:
    robot_id = str(selection["robot_configuration_id"])
    package = mainline["load_robot_package"](
        REPOSITORY_ROOT / str(selection["package_root"])
    )
    if package.robot_configuration_id != robot_id:
        raise ValueError(f"canonical package identity does not match {robot_id}")
    resolved = REPOSITORY_ROOT / str(selection["resolved_bundle_dir"])
    design = _read_json(resolved / "capability_design.json")
    suite = _read_json(resolved / "capability_validation_suite.json")
    driver_path = (REPOSITORY_ROOT / str(selection["driver_path"])).resolve()
    methods = tuple(str(value["method_name"]) for value in design["capabilities"])
    audit = mainline["audit_driver_source"](
        driver_path.read_text(encoding="utf-8"),
        condition=str(selection["condition"]),
        capability_methods=methods,
    )
    cases = suite.get("cases")
    expected_case_count = EXPECTED_CASE_COUNTS[robot_id]
    expected_capability_count = EXPECTED_CAPABILITY_COUNTS[robot_id]
    if not isinstance(cases, list) or len(cases) != expected_case_count:
        raise ValueError(
            f"{robot_id} suite must contain {expected_case_count} cases"
        )

    trials = []
    pipeline_completed = True
    for raw_case in cases:
        if not isinstance(raw_case, Mapping):
            raise ValueError("suite contains an invalid case")
        case = dict(raw_case)
        case_id = str(case["case_id"])
        requests = _requests(case)
        scene_path = (package.root / str(case["scene_entrypoint"])).resolve()
        scene_path.relative_to((package.root / "assets").resolve())
        binding = mainline["_resolve_b1_body_geom_symbols"](
            case["binding"], scene_path
        )
        guards = case["guards"]
        video_path = output_dir / "videos" / f"{case_id}.mp4"
        model = _ScriptedCalibrationModel(str(case["method_name"]), requests)
        controller: dict[str, Any] = {}
        try:
            result = mainline["run_recap_worker_session"](
                config=mainline["RecapWorkerSessionConfig"](
                    driver_path=driver_path,
                    scene_path=scene_path,
                    robot_configuration_id=robot_id,
                    reset=case.get("reset", {"kind": "default"}),
                    max_steps=int(case.get("max_steps", 10_000)),
                    max_sim_time_s=float(
                        case.get("validation_timeout_sim_s", case["timeout_sim_s"])
                    ),
                    sample_hz=float(case.get("sample_hz", 20.0)),
                    wall_timeout_s=wall_timeout_s,
                    render={
                        "enabled": record_video,
                        "width": int(case.get("video_width", 800)),
                        "height": int(case.get("video_height", 600)),
                        "fps": float(case.get("video_fps", 10.0)),
                        "camera": case.get("camera", -1),
                    },
                    video_path=video_path if record_video else None,
                ),
                capability_design=design,
                public_task={
                    "objective": "Execute the fixed typed reference calibration sequence."
                },
                model=model,
                budgets=mainline["RecapBudgets"](
                    max_model_calls=len(requests) + 1,
                    max_capability_calls=len(requests),
                    max_depth=1,
                    max_subtasks_per_plan=1,
                    max_invalid_outputs=1,
                    max_history_chars=20_000,
                ),
            )
            controller = dict(result["controller"])
            worker = dict(result["worker"])
        except Exception as exc:
            pipeline_completed = False
            worker = {
                "worker_completed": False,
                "worker_error": {"type": type(exc).__name__, "message": str(exc)[:1000]},
                "physical_evidence": {},
                "video": {"requested": record_video, "complete": False},
                "capability_invocations": [],
            }

        metric_passed = False
        temporal_results: list[dict[str, Any]] = []
        directions = None
        measurement_error = None
        guard_outcomes: dict[str, bool] = {}
        try:
            guard_outcomes = mainline["evaluate_guards"](
                guards, worker_result=worker
            )
            metric_passed, temporal_results, directions = _metric(
                case=case,
                binding=binding,
                worker=worker,
                controller=controller,
                requests=requests,
                mainline=mainline,
            )
        except Exception as exc:
            measurement_error = f"{type(exc).__name__}: {exc}"

        physical_execution = bool(mainline["_physical_execution_completed"](worker))
        contact_integrity = dict(mainline["_contact_integrity"](worker))
        physical_integrity = (
            physical_execution
            and bool(contact_integrity.get("passed"))
            and bool(guard_outcomes)
            and all(guard_outcomes.values())
        )
        video_value = worker.get("video")
        video = dict(video_value) if isinstance(video_value, Mapping) else {}
        video_complete = _video_evidence_complete(video)
        adapter_passed = (
            controller.get("status") == "CONTROLLER_FINISHED"
            and worker.get("worker_completed") is True
            and worker.get("controller_protocol_completed") is True
            and worker.get("successful_method_invocations") == len(requests)
            and not worker.get("capability_errors")
        )
        diagnostic_execution_passed = (
            adapter_passed and metric_passed and physical_integrity
        )
        trial_passed = diagnostic_execution_passed and video_complete
        video.update(
            {
                "robot_configuration_id": robot_id,
                "run_id": run_id,
                "attempt": attempt,
                "case_id": case_id,
            }
        )
        trials.append(
            {
                "case_id": case_id,
                "capability_id": case["capability_id"],
                "method_name": case["method_name"],
                "scene_entrypoint": case["scene_entrypoint"],
                "typed_request_count": len(requests),
                "adapter_path_passed": adapter_passed,
                "controller": controller,
                "capability_invocations": worker.get("capability_invocations", []),
                "temporal_results": temporal_results,
                "bidirectional_a3": directions,
                "measurement_error": measurement_error,
                "task_metric_passed": metric_passed,
                "guard_outcomes": guard_outcomes,
                "physical_execution_passed": physical_execution,
                "contact_integrity": contact_integrity,
                "physical_integrity_passed": physical_integrity,
                "diagnostic_execution_passed": diagnostic_execution_passed,
                "video": video,
                "trial_passed": trial_passed,
                "worker_error": worker.get("worker_error"),
                "candidate_exception": worker.get("candidate_exception"),
                "capability_errors": worker.get("capability_errors", []),
                "candidate_log": worker.get("candidate_log", ""),
                "physical_evidence": worker.get("physical_evidence", {}),
            }
        )
        print(
            f"{robot_id}/{case_id}: adapter={adapter_passed} metric={metric_passed} "
            f"integrity={physical_integrity} video={video_complete}"
        )

    capability_results = []
    for capability_id in sorted({str(value["capability_id"]) for value in trials}):
        values = [value for value in trials if value["capability_id"] == capability_id]
        passed_count = sum(bool(value["trial_passed"]) for value in values)
        capability_results.append(
            {
                "capability_id": capability_id,
                "passed_case_count": passed_count,
                "case_count": len(values),
                "minimum_passed_case_count": 2,
                "passed": len(values) == 3 and passed_count >= 2,
            }
        )
    all_cases_passed = len(trials) == expected_case_count and all(
        value["trial_passed"] for value in trials
    )
    suite_passed = len(capability_results) == expected_capability_count and all(
        value["passed"] for value in capability_results
    )
    physical_integrity = bool(trials) and all(
        value["physical_integrity_passed"] for value in trials
    )
    video_complete = bool(trials) and all(
        _video_evidence_complete(value["video"])
        for value in trials
    )
    diagnostic_execution_passed = pipeline_completed and bool(trials) and all(
        value["diagnostic_execution_passed"] for value in trials
    )
    validation_passed = (
        pipeline_completed
        and calibration_inputs_clean
        and all_cases_passed
        and suite_passed
        and physical_integrity
        and video_complete
    )
    return {
        "artifact_type": "b2_typed_reference_calibration_report",
        "schema_version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "robot_configuration_id": robot_id,
        "run_id": run_id,
        "attempt": attempt,
        "code_revision": code_revision,
        "calibration_inputs_clean": calibration_inputs_clean,
        "calibration_input_changes": list(calibration_input_changes),
        "condition": selection["condition"],
        "reference_driver_path": selection["driver_path"],
        "capability_design_path": str(
            Path(str(selection["resolved_bundle_dir"])) / "capability_design.json"
        ),
        "capability_validation_suite_path": str(
            Path(str(selection["resolved_bundle_dir"]))
            / "capability_validation_suite.json"
        ),
        "capability_design_id": design["capability_design_id"],
        "capability_validation_suite_id": suite["suite_id"],
        "source_capability_validation_suite_id": suite["source_suite_id"],
        "pass_standard_id": suite["pass_standard_id"],
        "typed_adapter": "autoadapter2.b2.capability_adapter.CapabilityAdapter",
        "public_state_profile_revision": mainline["PUBLIC_STATE_PROFILE_REVISION"],
        "typed_adapter_path_executed": True,
        "persistent_session_per_case": True,
        "pipeline_completed": pipeline_completed,
        "physical_validation_executed": bool(trials)
        and all(value["physical_execution_passed"] for value in trials),
        "physical_integrity_passed": physical_integrity,
        "diagnostic_execution_passed": diagnostic_execution_passed,
        "suite_aggregation_passed": suite_passed,
        "all_cases_passed": all_cases_passed,
        "validation_passed": validation_passed,
        "video_complete": video_complete,
        "passed_private_case_count": sum(value["trial_passed"] for value in trials),
        "private_case_count": len(trials),
        "passed_capability_count": sum(value["passed"] for value in capability_results),
        "capability_count": len(capability_results),
        "capability_results": capability_results,
        "source_audit": asdict(audit),
        "video_manifest": [value["video"] for value in trials],
        "trials": trials,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--robot", action="append")
    parser.add_argument(
        "--no-video",
        action="store_true",
        help="diagnostic smoke only; blocker-clearing calibration requires video",
    )
    parser.add_argument("--wall-timeout-s", type=float, default=120.0)
    parser.add_argument("--run-id", default="b2-reference-calibration")
    parser.add_argument("--attempt", type=int, default=0)
    args = parser.parse_args()
    if args.wall_timeout_s <= 0.0 or args.attempt < 0:
        parser.error("wall timeout must be positive and attempt nonnegative")

    validate_resolved()
    mainline = _mainline()
    selections = load_selection()
    known = {str(value["robot_configuration_id"]): value for value in selections}
    selected = list(known) if args.robot is None else args.robot
    if len(selected) != len(set(selected)):
        parser.error("each robot may be selected only once")
    unknown = sorted(set(selected) - set(known))
    if unknown:
        parser.error(f"unknown robot_configuration_id: {', '.join(unknown)}")

    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    summaries = []
    code_revision, calibration_input_changes = _git_state(selections)
    calibration_inputs_clean = not calibration_input_changes
    for robot_id in selected:
        report = _run_robot(
            selection=known[robot_id],
            output_dir=output_root / robot_id,
            record_video=not args.no_video,
            wall_timeout_s=float(args.wall_timeout_s),
            run_id=args.run_id,
            attempt=args.attempt,
            code_revision=code_revision,
            calibration_inputs_clean=calibration_inputs_clean,
            calibration_input_changes=calibration_input_changes,
            mainline=mainline,
        )
        path = output_root / robot_id / "reference_calibration_report.json"
        _write_json(path, report)
        summaries.append(
            {
                "robot_configuration_id": robot_id,
                "report": str(path.relative_to(output_root)),
                "validation_passed": report["validation_passed"],
                "diagnostic_execution_passed": report[
                    "diagnostic_execution_passed"
                ],
                "video_complete": report["video_complete"],
                "passed_private_case_count": report["passed_private_case_count"],
                "private_case_count": report["private_case_count"],
            }
        )
        print(
            f"{robot_id}: validation_passed={report['validation_passed']} "
            f"video_complete={report['video_complete']}"
        )
    complete_robot_cohort = set(selected) == set(known)
    prerequisite_passed = _prerequisite_passed(
        record_video=not args.no_video,
        complete_robot_cohort=complete_robot_cohort,
        calibration_inputs_clean=calibration_inputs_clean,
        summaries=summaries,
    )
    _write_json(
        output_root / "reference_calibration_index.json",
        {
            "artifact_type": "b2_typed_reference_calibration_index",
            "schema_version": "1.0",
            "created_at": datetime.now(UTC).isoformat(),
            "run_id": args.run_id,
            "attempt": args.attempt,
            "code_revision": code_revision,
            "calibration_inputs_clean": calibration_inputs_clean,
            "calibration_input_changes": calibration_input_changes,
            "record_video": not args.no_video,
            "diagnostic_only": True,
            "complete_robot_cohort": complete_robot_cohort,
            "diagnostic_execution_passed": all(
                value["diagnostic_execution_passed"] for value in summaries
            ),
            "prerequisite_passed": prerequisite_passed,
            "robots": summaries,
        },
    )
    return 0 if prerequisite_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
