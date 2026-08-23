"""Mechanical B2 pre-formal evidence checks.

The validator reuses the retained two-robot interface calibration and the
bounded SO-101 pick-place v6 delta canary.  It reads existing evidence only;
it never rewrites a historical record or launches MuJoCo or a model.
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


_EXPECTED_PICK_PARAMETERS: dict[str, Any] = {
    "start_position": [0.34, 0.08, 0.18],
    "grasp_position": [0.34, 0.08, 0.195],
    "grasp_wrist_roll": -1.5707963267948966,
    "grasp_gripper": 0.26,
    "target_position": [0.38, -0.08, 0.26],
    "release_position": [0.371, -0.078, 0.272],
    "tool_target_position": [0.371, -0.078, 0.272],
}
_EXPECTED_PICK_RESET = {
    "kind": "default",
    "joint_positions": {"shoulder_pan": -1.9},
    "actuator_controls": {"shoulder_pan": -1.9},
}
_EXPECTED_PICK_SCENE = "assets/pick_place_scene.xml"
_EXPECTED_PICK_BUDGET = {
    "timeout_sim_s": 40.0,
    "max_steps": 10000,
    "sample_hz": 20.0,
}
_EXPECTED_PICK_CLAUSE = {
    "clause_id": "placed_object_distance",
    "metric": "object_goal_distance",
    "unit": "m",
    "comparator": "<=",
    "threshold": 0.07,
}
_EXPECTED_PENETRATION_M = 0.005


def _object(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _objects(value: Any, *, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ValueError(f"{label} must be an object list")
    return list(value)


def _string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _integer(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _number(value: Any, *, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _read(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value


def _manifest_file(manifest_path: Path, value: Any, *, label: str) -> Path:
    raw = _string(value, label=label)
    candidate = Path(raw)
    path = candidate.resolve() if candidate.is_absolute() else (manifest_path.parent / candidate).resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not resolve to a file: {path}")
    return path


def _inside(path: Path, root: Path, *, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes its pinned evidence root: {resolved}") from exc
    return resolved


def _index_by(values: Sequence[Mapping[str, Any]], key: str, *, label: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for value in values:
        identifier = value.get(key)
        if not isinstance(identifier, str) or not identifier or identifier in result:
            raise ValueError(f"{label} contains an invalid or duplicate {key}")
        result[identifier] = value
    return result


def _current_reference_contract(
    *,
    repository_root: Path,
    selection: Mapping[str, Any],
    pin: Mapping[str, Any],
) -> tuple[
    dict[str, str],
    dict[str, str],
    tuple[str, ...],
    dict[str, dict[str, Any]],
]:
    resolved_dir = repository_root / _string(
        selection.get("resolved_bundle_dir"), label="resolved_bundle_dir"
    )
    driver_path = repository_root / _string(
        selection.get("driver_path"), label="reference driver_path"
    )
    if not driver_path.is_file():
        raise ValueError(f"current fixed reference driver is absent: {driver_path}")
    design = _read(resolved_dir / "capability_design.json", label="current capability design")
    suite = _read(
        resolved_dir / "capability_validation_suite.json",
        label="current capability validation suite",
    )
    robot_id = _string(pin.get("robot_configuration_id"), label="readiness robot ID")
    if design.get("robot_configuration_id") != robot_id or suite.get("robot_configuration_id") != robot_id:
        raise ValueError(f"current reference snapshots do not belong to {robot_id}")
    identity = {
        "capability_design_id": _string(
            pin.get("capability_design_id"), label=f"{robot_id}.capability_design_id"
        ),
        "capability_validation_suite_id": _string(
            pin.get("capability_validation_suite_id"),
            label=f"{robot_id}.capability_validation_suite_id",
        ),
        "source_capability_validation_suite_id": _string(
            pin.get("source_capability_validation_suite_id"),
            label=f"{robot_id}.source_capability_validation_suite_id",
        ),
        "pass_standard_id": _string(
            pin.get("pass_standard_id"), label=f"{robot_id}.pass_standard_id"
        ),
    }
    if design.get("capability_design_id") != identity["capability_design_id"]:
        raise ValueError(f"current {robot_id} capability design does not match the evidence pin")
    if suite.get("suite_id") != identity["capability_validation_suite_id"]:
        raise ValueError(f"current {robot_id} capability suite does not match the evidence pin")
    if suite.get("source_suite_id") != identity["source_capability_validation_suite_id"]:
        raise ValueError(f"current {robot_id} source suite does not match the evidence pin")
    if suite.get("pass_standard_id") != identity["pass_standard_id"]:
        raise ValueError(f"current {robot_id} pass standard does not match the evidence pin")

    capability_ids = tuple(
        _string(value, label=f"{robot_id}.capability_ids")
        for value in pin.get("capability_ids", ())
    )
    capabilities = _objects(design.get("capabilities"), label=f"{robot_id} capabilities")
    if tuple(value.get("capability_id") for value in capabilities) != capability_ids:
        raise ValueError(f"current {robot_id} capability IDs do not match the evidence pin")
    expected_cases = tuple(
        f"{capability_id}-H{case_number}"
        for capability_id in capability_ids
        for case_number in (1, 2, 3)
    )
    cases = _objects(suite.get("cases"), label=f"{robot_id} validation cases")
    if tuple(value.get("case_id") for value in cases) != expected_cases:
        raise ValueError(f"current {robot_id} case set is not the exact pinned suite")
    if _integer(pin.get("private_case_count"), label=f"{robot_id}.private_case_count") != len(expected_cases):
        raise ValueError(f"{robot_id} pinned case count is inconsistent")
    methods = {
        _string(value.get("capability_id"), label=f"{robot_id} capability ID"): _string(
            value.get("method_name"), label=f"{robot_id} method name"
        )
        for value in capabilities
    }
    case_contracts: dict[str, dict[str, Any]] = {}
    for case, case_id in zip(cases, expected_cases, strict=True):
        capability_id = case_id.split("-", 1)[0]
        if (
            case.get("capability_id") != capability_id
            or case.get("method_name") != methods.get(capability_id)
        ):
            raise ValueError(f"current {robot_id} suite method does not match its design")
        sequence = case.get("validation_request_sequence")
        request_count = len(sequence) if isinstance(sequence, list) else 1
        case_contracts[case_id] = {
            "scene_entrypoint": _string(
                case.get("scene_entrypoint"), label=f"{robot_id}/{case_id}.scene"
            ),
            "typed_request_count": request_count,
        }
    return identity, methods, expected_cases, case_contracts


def _resolved_legacy_video(
    *,
    repository_root: Path,
    reported_path: Any,
    expected_path: Path,
) -> Path:
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    from experiment.path_layout import resolve_run_path

    mapped = resolve_run_path(
        _string(reported_path, label="historical video path"),
        repository_root=repository_root,
    )
    resolved = Path(mapped)
    if not resolved.is_absolute():
        resolved = repository_root / resolved
    resolved = resolved.resolve()
    if resolved != expected_path.resolve():
        raise ValueError("historical video path does not map to its pinned archive location")
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise ValueError(f"historical video is absent or empty: {resolved}")
    return resolved


def _validate_reference_trial(
    *,
    trial: Mapping[str, Any],
    robot_id: str,
    case_id: str,
    expected_method: str,
    expected_scene: str,
    expected_request_count: int,
    evidence_root: Path,
    repository_root: Path,
) -> None:
    if trial.get("case_id") != case_id:
        raise ValueError(f"{robot_id} calibration trial order/identity changed")
    capability_id = case_id.split("-", 1)[0]
    if (
        trial.get("capability_id") != capability_id
        or trial.get("method_name") != expected_method
        or trial.get("scene_entrypoint") != expected_scene
    ):
        raise ValueError(f"{robot_id}/{case_id} does not match the current capability contract")
    for field in (
        "adapter_path_passed",
        "task_metric_passed",
        "physical_execution_passed",
        "physical_integrity_passed",
        "diagnostic_execution_passed",
        "trial_passed",
    ):
        if trial.get(field) is not True:
            raise ValueError(f"{robot_id}/{case_id} lacks {field}=true")
    if trial.get("measurement_error") is not None:
        raise ValueError(f"{robot_id}/{case_id} contains a measurement error")
    if trial.get("worker_error") is not None or trial.get("candidate_exception") is not None:
        raise ValueError(f"{robot_id}/{case_id} contains a worker/candidate error")
    if trial.get("capability_errors") not in (None, []):
        raise ValueError(f"{robot_id}/{case_id} contains capability errors")
    controller = _object(trial.get("controller"), label=f"{robot_id}/{case_id} controller")
    if controller.get("status") != "CONTROLLER_FINISHED" or controller.get("invalid_outputs") != 0:
        raise ValueError(f"{robot_id}/{case_id} controller did not finish cleanly")
    request_count = _integer(
        trial.get("typed_request_count"), label=f"{robot_id}/{case_id}.typed_request_count"
    )
    if request_count != expected_request_count:
        raise ValueError(f"{robot_id}/{case_id} typed request count changed")
    invocations = _objects(
        trial.get("capability_invocations"), label=f"{robot_id}/{case_id} invocations"
    )
    if len(invocations) != request_count or any(value.get("success") is not True for value in invocations):
        raise ValueError(f"{robot_id}/{case_id} did not retain every successful typed invocation")
    guards = _object(trial.get("guard_outcomes"), label=f"{robot_id}/{case_id} guards")
    if not guards or any(value is not True for value in guards.values()):
        raise ValueError(f"{robot_id}/{case_id} has a failed physical guard")
    integrity = _object(
        trial.get("contact_integrity"), label=f"{robot_id}/{case_id} contact integrity"
    )
    if integrity.get("passed") is not True:
        raise ValueError(f"{robot_id}/{case_id} contact integrity failed")
    video = _object(trial.get("video"), label=f"{robot_id}/{case_id} video")
    if (
        video.get("requested") is not True
        or video.get("complete") is not True
        or video.get("decodable") is not True
        or _integer(video.get("frame_count"), label=f"{robot_id}/{case_id}.frame_count") <= 0
        or video.get("robot_configuration_id") != robot_id
        or video.get("case_id") != case_id
    ):
        raise ValueError(f"{robot_id}/{case_id} video evidence is incomplete")
    expected_video = evidence_root / robot_id / "videos" / f"{case_id}.mp4"
    _resolved_legacy_video(
        repository_root=repository_root,
        reported_path=video.get("path"),
        expected_path=expected_video,
    )


def _validate_interface_calibration(
    *,
    manifest_path: Path,
    pin: Mapping[str, Any],
    selections: Mapping[str, Mapping[str, Any]],
    repository_root: Path,
) -> dict[str, Any]:
    index_path = _manifest_file(
        manifest_path, pin.get("index_path"), label="interface calibration index_path"
    )
    evidence_root = index_path.parent
    index = _read(index_path, label="interface calibration index")
    run_id = _string(pin.get("run_id"), label="interface calibration run_id")
    code_revision = _string(
        pin.get("code_revision"), label="interface calibration code_revision"
    )
    if (
        index.get("artifact_type") != "b2_typed_reference_calibration_index"
        or index.get("schema_version") != "1.0"
        or index.get("run_id") != run_id
        or index.get("code_revision") != code_revision
        or index.get("calibration_inputs_clean") is not True
        or index.get("calibration_input_changes") != []
        or index.get("record_video") is not True
        or index.get("diagnostic_only") is not True
        or index.get("complete_robot_cohort") is not True
        or index.get("diagnostic_execution_passed") is not True
        or index.get("prerequisite_passed") is not True
    ):
        raise ValueError("interface calibration index is not the complete clean video cohort")

    robot_pins = _objects(pin.get("robots"), label="interface calibration robots")
    pinned_by_robot = _index_by(robot_pins, "robot_configuration_id", label="interface pins")
    if tuple(pinned_by_robot) != tuple(selections):
        raise ValueError("interface calibration pins do not cover the exact robot cohort")
    summaries = _objects(index.get("robots"), label="interface calibration summaries")
    summary_by_robot = _index_by(summaries, "robot_configuration_id", label="calibration summaries")
    if tuple(summary_by_robot) != tuple(selections):
        raise ValueError("interface calibration index does not cover the exact robot cohort")

    total_cases = 0
    total_videos = 0
    for robot_id, selection in selections.items():
        robot_pin = pinned_by_robot[robot_id]
        summary = summary_by_robot[robot_id]
        identity, methods, expected_cases, case_contracts = _current_reference_contract(
            repository_root=repository_root,
            selection=selection,
            pin=robot_pin,
        )
        report_relative = Path(_string(robot_pin.get("report"), label=f"{robot_id}.report"))
        if report_relative.is_absolute():
            raise ValueError(f"{robot_id}.report must be relative to the calibration index")
        report_path = _inside(evidence_root / report_relative, evidence_root, label=f"{robot_id}.report")
        if not report_path.is_file():
            raise ValueError(f"{robot_id} calibration report is absent: {report_path}")
        if summary.get("report") != report_relative.as_posix():
            raise ValueError(f"{robot_id} index report path differs from the manifest pin")
        report = _read(report_path, label=f"{robot_id} calibration report")
        expected_count = len(expected_cases)
        if (
            report.get("artifact_type") != "b2_typed_reference_calibration_report"
            or report.get("schema_version") != "1.0"
            or report.get("robot_configuration_id") != robot_id
            or report.get("run_id") != run_id
            or report.get("code_revision") != code_revision
            or report.get("calibration_inputs_clean") is not True
            or report.get("calibration_input_changes") != []
            or report.get("condition") != selection.get("condition")
            or report.get("reference_driver_path") != selection.get("driver_path")
            or report.get("capability_design_id") != identity["capability_design_id"]
            or report.get("capability_validation_suite_id")
            != identity["capability_validation_suite_id"]
            or report.get("source_capability_validation_suite_id")
            != identity["source_capability_validation_suite_id"]
            or report.get("pass_standard_id") != identity["pass_standard_id"]
            or report.get("typed_adapter") != pin.get("typed_adapter", "autoadapter2.b2.capability_adapter.CapabilityAdapter")
            or report.get("public_state_profile_revision")
            != pin.get("public_state_profile_revision", "b2-public-state-v2")
        ):
            raise ValueError(f"{robot_id} calibration report identity does not match its pin")
        for field in (
            "typed_adapter_path_executed",
            "persistent_session_per_case",
            "pipeline_completed",
            "physical_validation_executed",
            "physical_integrity_passed",
            "diagnostic_execution_passed",
            "suite_aggregation_passed",
            "all_cases_passed",
            "validation_passed",
            "video_complete",
        ):
            if report.get(field) is not True:
                raise ValueError(f"{robot_id} calibration report lacks {field}=true")
        if (
            report.get("passed_private_case_count") != expected_count
            or report.get("private_case_count") != expected_count
            or report.get("passed_capability_count") != len(robot_pin["capability_ids"])
            or report.get("capability_count") != len(robot_pin["capability_ids"])
            or summary.get("validation_passed") is not True
            or summary.get("diagnostic_execution_passed") is not True
            or summary.get("video_complete") is not True
            or summary.get("passed_private_case_count") != expected_count
            or summary.get("private_case_count") != expected_count
        ):
            raise ValueError(f"{robot_id} calibration counts are incomplete")
        capability_results = _objects(
            report.get("capability_results"), label=f"{robot_id} capability results"
        )
        if tuple(value.get("capability_id") for value in capability_results) != tuple(
            robot_pin["capability_ids"]
        ):
            raise ValueError(f"{robot_id} capability aggregation changed")
        if any(
            value.get("passed_case_count") != 3
            or value.get("case_count") != 3
            or value.get("passed") is not True
            for value in capability_results
        ):
            raise ValueError(f"{robot_id} capability aggregation is incomplete")
        trials = _objects(report.get("trials"), label=f"{robot_id} calibration trials")
        if len(trials) != expected_count:
            raise ValueError(f"{robot_id} calibration trial count is incomplete")
        for trial, case_id in zip(trials, expected_cases, strict=True):
            capability_id = case_id.split("-", 1)[0]
            _validate_reference_trial(
                trial=trial,
                robot_id=robot_id,
                case_id=case_id,
                expected_method=methods[capability_id],
                expected_scene=case_contracts[case_id]["scene_entrypoint"],
                expected_request_count=case_contracts[case_id]["typed_request_count"],
                evidence_root=evidence_root,
                repository_root=repository_root,
            )
        total_cases += expected_count
        total_videos += expected_count
    if total_cases != 33 or total_videos != 33:
        raise ValueError("interface calibration is not exactly 33 retained cases/videos")
    return {
        "index_path": str(index_path),
        "run_id": run_id,
        "code_revision": code_revision,
        "case_count": total_cases,
        "video_count": total_videos,
    }


def _pick_task(task_suite: Mapping[str, Any], *, robot_id: str, task_id: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    robot_suites = _objects(task_suite.get("robot_suites"), label="B2 robot suites")
    robots = _index_by(robot_suites, "robot_configuration_id", label="B2 robot suites")
    robot = robots.get(robot_id)
    if robot is None:
        raise ValueError("pick-place evidence robot is absent from the task suite")
    tasks = _objects(robot.get("tasks"), label=f"{robot_id} task suite")
    selected = _index_by(tasks, "task_id", label=f"{robot_id} tasks").get(task_id)
    if selected is None:
        raise ValueError("pick-place evidence task is absent from the task suite")
    return robot, selected


def _validate_pick_suite(
    *,
    task_suite: Mapping[str, Any],
    pin: Mapping[str, Any],
    selections: Mapping[str, Mapping[str, Any]],
    repository_root: Path,
) -> Mapping[str, Any]:
    robot_id = _string(pin.get("robot_configuration_id"), label="pick_place_v6.robot_configuration_id")
    task_id = _string(pin.get("task_id"), label="pick_place_v6.task_id")
    robot, task = _pick_task(task_suite, robot_id=robot_id, task_id=task_id)
    snapshot_id = _string(pin.get("task_snapshot_id"), label="pick_place_v6.task_snapshot_id")
    if robot.get("task_snapshot_id") != snapshot_id:
        raise ValueError("pick-place task suite snapshot is not v6")
    public = _object(task.get("public_projection"), label="pick-place public projection")
    request = _object(public.get("request"), label="pick-place public request")
    parameters = _object(request.get("task_parameters"), label="pick-place task parameters")
    if request.get("task_id") != task_id or dict(parameters) != _EXPECTED_PICK_PARAMETERS:
        raise ValueError("pick-place v6 public coordinates or grasp settings changed")
    if task.get("private_instance_id") != "so101-mw_pick_place":
        raise ValueError("pick-place v6 private instance changed")
    if task.get("episode_budget") != _EXPECTED_PICK_BUDGET:
        raise ValueError("pick-place v6 episode budget is not 40 s / 10000 steps")
    inputs = _objects(task.get("replicate_inputs"), label="pick-place replicate inputs")
    if len(inputs) != 3 or any(
        value.get("scene_entrypoint") != _EXPECTED_PICK_SCENE
        or value.get("reset") != _EXPECTED_PICK_RESET
        or value.get("reset_seed") is not None
        or value.get("reset_seed_applied") is not False
        for value in inputs
    ):
        raise ValueError("pick-place v6 scene/reset is not fixed across R1-R3")
    clauses = _objects(task.get("private_scoring_clauses"), label="pick-place scoring clauses")
    if len(clauses) != 1 or any(
        clauses[0].get(key) != expected for key, expected in _EXPECTED_PICK_CLAUSE.items()
    ):
        raise ValueError("pick-place v6 0.07 m scoring clause changed")
    selection = selections.get(robot_id)
    if selection is None:
        raise ValueError("pick-place robot is absent from the reference selection")
    scene_path = repository_root / str(selection["package_root"]) / _EXPECTED_PICK_SCENE
    try:
        xml = ElementTree.parse(scene_path)
    except (OSError, ElementTree.ParseError) as exc:
        raise ValueError(f"cannot read pick-place v6 scene: {scene_path}") from exc
    workpiece = xml.find(".//body[@name='workpiece']")
    goal = xml.find(".//body[@name='pick_place_goal']")
    if workpiece is None or goal is None:
        raise ValueError("pick-place v6 scene lacks the workpiece or goal body")
    try:
        start = [float(value) for value in workpiece.get("pos", "").split()]
        target = [float(value) for value in goal.get("pos", "").split()]
    except ValueError as exc:
        raise ValueError("pick-place v6 scene contains a non-numeric body position") from exc
    if any(not math.isfinite(value) for value in (*start, *target)):
        raise ValueError("pick-place v6 scene contains a non-finite body position")
    if start != _EXPECTED_PICK_PARAMETERS["start_position"] or target != _EXPECTED_PICK_PARAMETERS["target_position"]:
        raise ValueError("pick-place v6 scene coordinates differ from the task request")
    return task


def _validate_pick_place_v6(
    *,
    manifest_path: Path,
    pin: Mapping[str, Any],
    task_suite: Mapping[str, Any],
    selections: Mapping[str, Mapping[str, Any]],
    repository_root: Path,
) -> dict[str, Any]:
    _validate_pick_suite(
        task_suite=task_suite,
        pin=pin,
        selections=selections,
        repository_root=repository_root,
    )
    report_path = _manifest_file(manifest_path, pin.get("report_path"), label="pick-place v6 report_path")
    video_path = _manifest_file(manifest_path, pin.get("video_path"), label="pick-place v6 video_path")
    plan_path = _manifest_file(manifest_path, pin.get("oracle_plan_path"), label="pick-place v6 oracle_plan_path")
    if video_path.stat().st_size <= 0:
        raise ValueError("pick-place v6 video is empty")
    robot_id = str(pin["robot_configuration_id"])
    task_id = str(pin["task_id"])
    replicate_id = _string(pin.get("replicate_id"), label="pick_place_v6.replicate_id")

    plans = _read(plan_path, label="pick-place oracle plans")
    if (
        plans.get("artifact_type") != "b2_recap_scripted_oracle_plans"
        or plans.get("schema_version") != "1.0"
        or plans.get("task_suite_identity")
        != {
            "artifact_type": "b2_recap_task_suite",
            "schema_version": "1.0",
            "task_count": 10,
        }
    ):
        raise ValueError("pick-place oracle plan artifact identity is invalid")
    design_ids = _object(plans.get("capability_design_ids"), label="oracle design IDs")
    if design_ids != {
        "robotstudio_so101": "experiment1-b1-fixed-interface::robotstudio_so101::v3",
        "unitree-go2-stock-12dof": "experiment1-b1-fixed-interface::unitree-go2-stock-12dof::v2",
    }:
        raise ValueError("oracle plans do not identify the fixed capability designs")
    selected_plans = [
        value
        for value in _objects(plans.get("plans"), label="oracle plans")
        if value.get("robot_configuration_id") == robot_id and value.get("task_id") == task_id
    ]
    if len(selected_plans) != 1:
        raise ValueError("oracle plans do not contain exactly one pick-place v6 plan")
    leaves = _objects(selected_plans[0].get("ordered_leaves"), label="pick-place oracle leaves")
    if len(leaves) != 7:
        raise ValueError("pick-place v6 oracle must contain exactly seven capability leaves")
    grasp_leaves = [
        value
        for value in leaves
        if value.get("capability_name") == "set_gripper_opening"
        and isinstance(value.get("request"), Mapping)
        and value["request"].get("opening_fraction") == 0.26
    ]
    if len(grasp_leaves) != 1:
        raise ValueError("pick-place v6 oracle does not contain the fixed 0.26 grasp")

    report = _read(report_path, label="pick-place v6 oracle report")
    if (
        report.get("artifact_type") != "b2_recap_scripted_oracle_task_canary"
        or report.get("formal_episode") is not False
        or report.get("formal_denominator_entry") is not False
        or report.get("video_requested") is not True
        or report.get("robot_configuration_id") != robot_id
        or report.get("task_id") != task_id
        or report.get("replicate_id") != replicate_id
        or report.get("error") is not None
    ):
        raise ValueError("pick-place v6 oracle report identity is invalid")
    summary = _object(report.get("summary"), label="pick-place v6 summary")
    expected_summary = {
        "diagnostic_status": "EXECUTED",
        "controller_status": "CONTROLLER_FINISHED",
        "planned_leaf_count": 7,
        "executed_capability_calls": 7,
        "worker_completed": True,
        "task_metric_passed": True,
        "physical_execution_passed": True,
        "physical_integrity_passed": True,
        "video_complete": True,
        "independent_harness_verdict": "PASS",
        "oracle_canary_passed": True,
        "scripted_model_calls": 8,
    }
    if any(summary.get(key) != value for key, value in expected_summary.items()):
        raise ValueError("pick-place v6 oracle summary is not a complete PASS")
    episode = _object(report.get("episode"), label="pick-place v6 episode")
    episode_identity = _object(
        episode.get("episode"), label="pick-place v6 episode identity"
    )
    if (
        episode_identity.get("robot_configuration_id") != robot_id
        or episode_identity.get("task_id") != task_id
        or episode_identity.get("instance_id") != "so101-mw_pick_place"
        or episode_identity.get("replicate_id") != replicate_id
        or Path(str(episode_identity.get("scene_path"))).name != "pick_place_scene.xml"
        or Path(str(episode_identity.get("driver_path"))).name
        != "fixed_capability_driver.py"
        or Path(str(episode_identity.get("capability_design_path"))).name
        != "capability_design.json"
        or Path(str(episode_identity.get("video_path"))).resolve()
        != video_path.resolve()
    ):
        raise ValueError("pick-place v6 episode fixed-input identity is invalid")
    controller = _object(episode.get("controller"), label="pick-place v6 controller")
    if (
        controller.get("status") != "CONTROLLER_FINISHED"
        or controller.get("model_calls") != 8
        or controller.get("capability_calls") != 7
        or controller.get("invalid_outputs") != 0
    ):
        raise ValueError("pick-place v6 controller did not finish the fixed plan")
    observed_leaves = [
        {
            "capability_name": value.get("capability_name"),
            "request": value.get("public_arguments"),
        }
        for value in _objects(controller.get("trace"), label="pick-place v6 controller trace")
        if value.get("action_kind") == "capability"
    ]
    if observed_leaves != [dict(value) for value in leaves]:
        raise ValueError("pick-place v6 controller trace differs from the pinned oracle plan")
    worker = _object(episode.get("worker"), label="pick-place v6 worker")
    if (
        worker.get("worker_completed") is not True
        or worker.get("controller_protocol_completed") is not True
        or worker.get("method_invoked") is not True
        or worker.get("successful_method_invocations") != 7
        or worker.get("candidate_exception") is not None
    ):
        raise ValueError("pick-place v6 worker evidence is incomplete")
    harness = _object(episode.get("harness"), label="pick-place v6 Harness")
    if (
        harness.get("robot_configuration_id") != robot_id
        or harness.get("task_id") != task_id
        or harness.get("instance_id") != "so101-mw_pick_place"
        or harness.get("replicate_id") != replicate_id
        or harness.get("task_snapshot_id") != pin.get("task_snapshot_id")
        or harness.get("scene_entrypoint") != _EXPECTED_PICK_SCENE
        or harness.get("reset_seed") is not None
        or harness.get("reset_seed_applied") is not False
        or harness.get("task_metric_passed") is not True
        or harness.get("physical_execution_passed") is not True
        or harness.get("physical_integrity_passed") is not True
        or harness.get("video_complete") is not True
        or harness.get("physical_harness_verdict") != "PASS"
    ):
        raise ValueError("pick-place v6 Harness is not an exact complete PASS")
    guards = _object(harness.get("guard_outcomes"), label="pick-place v6 guards")
    if not guards or any(value is not True for value in guards.values()):
        raise ValueError("pick-place v6 Harness contains a failed guard")
    clauses = _objects(harness.get("task_clause_results"), label="pick-place v6 clauses")
    if len(clauses) != 1:
        raise ValueError("pick-place v6 Harness must contain one task clause")
    clause = clauses[0]
    distance = _number(clause.get("measurement_value"), label="pick-place v6 object-goal distance")
    if (
        clause.get("clause_id") != "placed_object_distance"
        or clause.get("binding_id") != "binding-mw_pick_place"
        or clause.get("measurement_error") is not None
        or clause.get("temporal_passed") is not True
        or clause.get("aggregation_passed") is not True
        or clause.get("task_metric_passed") is not True
        or distance > _EXPECTED_PICK_CLAUSE["threshold"]
    ):
        raise ValueError("pick-place v6 object-goal clause did not pass 0.07 m")
    integrity = _object(harness.get("contact_integrity"), label="pick-place v6 contact integrity")
    minimum_contact = _number(
        integrity.get("minimum_contact_distance_m"), label="pick-place v6 minimum contact"
    )
    if (
        integrity.get("passed") is not True
        or integrity.get("maximum_allowed_penetration_m") != _EXPECTED_PENETRATION_M
        or minimum_contact < -_EXPECTED_PENETRATION_M
    ):
        raise ValueError("pick-place v6 contact integrity did not pass 0.005 m")
    video = _object(harness.get("video"), label="pick-place v6 video metadata")
    frame_count = _integer(video.get("frame_count"), label="pick-place v6 frame_count")
    if (
        video.get("requested") is not True
        or video.get("complete") is not True
        or video.get("decodable") is not True
        or frame_count <= 0
        or video.get("width") != 800
        or video.get("height") != 600
    ):
        raise ValueError("pick-place v6 video metadata is incomplete")
    reported_video = Path(_string(video.get("path"), label="pick-place v6 reported video"))
    if reported_video.resolve() != video_path.resolve():
        raise ValueError("pick-place v6 report does not point to the manifest-pinned video")
    return {
        "report_path": str(report_path),
        "video_path": str(video_path),
        "task_snapshot_id": str(pin["task_snapshot_id"]),
        "object_goal_distance_m": distance,
        "minimum_contact_distance_m": minimum_contact,
        "video_frame_count": frame_count,
    }


def validate_readiness_evidence(
    *,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    task_suite: Mapping[str, Any],
    selections: Mapping[str, Mapping[str, Any]],
    repository_root: Path,
) -> dict[str, Any]:
    """Validate the exact retained B2 evidence pair and return a small summary."""

    readiness = _object(manifest.get("readiness_evidence"), label="readiness_evidence")
    interface = _object(
        readiness.get("interface_calibration"), label="readiness_evidence.interface_calibration"
    )
    pick_place = _object(
        readiness.get("pick_place_v6"), label="readiness_evidence.pick_place_v6"
    )
    interface_summary = _validate_interface_calibration(
        manifest_path=manifest_path,
        pin=interface,
        selections=selections,
        repository_root=repository_root,
    )
    pick_summary = _validate_pick_place_v6(
        manifest_path=manifest_path,
        pin=pick_place,
        task_suite=task_suite,
        selections=selections,
        repository_root=repository_root,
    )
    return {
        "interface_calibration": interface_summary,
        "pick_place_v6": pick_summary,
    }


__all__ = ["validate_readiness_evidence"]
