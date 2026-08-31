from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiment.experiment3 import runner


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MAINLINE_ROOT = REPOSITORY_ROOT / "autoadapter"
INDEX_PATH = REPOSITORY_ROOT / runner.FIXED_INPUT_INDEX
SMOKE_PATH = REPOSITORY_ROOT / runner.FIXED_INPUT_SMOKE_REPORT
CALIBRATION_PATH = REPOSITORY_ROOT / runner.FIXED_INPUT_CALIBRATION_REPORT


def test_checked_in_fixed_input_index_audits_exactly_eleven_current_pairs() -> None:
    checked = runner._check_fixed_input_set(
        MAINLINE_ROOT,
        index_path=INDEX_PATH,
        smoke_report_path=SMOKE_PATH,
        calibration_report_path=CALIBRATION_PATH,
    )

    assert checked["passed"] is True
    assert checked["smoke_report"]["passed"] is True
    assert checked["smoke_report"]["input_set_id"] == checked["input_set_id"]
    assert tuple(checked["smoke_report"]["robots"]) == runner.ROBOT_CONFIGURATIONS
    assert checked["calibration_report"]["passed"] is True
    assert checked["calibration_report"]["input_set_id"] == checked["input_set_id"]
    assert tuple(checked["robots"]) == runner.ROBOT_CONFIGURATIONS
    assert checked["root_directory"] == str(INDEX_PATH.parent.resolve())
    for robot, item in checked["robots"].items():
        assert item["robot_configuration_id"] == robot
        assert item["validated_before_model_calls"] is True
        assert item["capability_count"] >= 5
        assert item["validation_case_count"] == 2 * item["capability_count"]
    assert checked["capability_count"] == sum(
        item["capability_count"] for item in checked["robots"].values()
    )
    assert checked["validation_case_count"] == 2 * checked["capability_count"]


def test_fixed_input_index_rejects_a_decorative_or_escaping_artifact_path(
    tmp_path: Path,
) -> None:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    index.setdefault("smoke_report", "smoke_report.json")
    index.setdefault("calibration_report", "calibration_report.json")
    robot = runner.ROBOT_CONFIGURATIONS[0]
    index["robots"][robot]["capability_design"] = "../outside.json"
    bad_index = tmp_path / "fixed_inputs" / "index.json"
    bad_index.parent.mkdir()
    bad_index.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(runner.Experiment3RunnerError, match="escapes its root"):
        runner._check_fixed_input_set(
            MAINLINE_ROOT,
            index_path=bad_index,
            smoke_report_path=bad_index.parent / "smoke_report.json",
            calibration_report_path=bad_index.parent / "calibration_report.json",
            package_loader=lambda *_args, **_kwargs: None,
            design_validator=lambda *_args, **_kwargs: {},
            suite_validator=lambda *_args, **_kwargs: {},
        )


def test_design_manifest_rejects_a_different_fixed_input_index() -> None:
    manifest = copy.deepcopy(runner.load_manifest())
    manifest["fixed_input_set"] = "experiment/experiment3/other/index.json"

    with pytest.raises(runner.Experiment3RunnerError, match="fixed_input_set"):
        runner.validate_design_manifest(manifest)


def test_smoke_report_requires_exact_eleven_finite_real_wiring_records(
    tmp_path: Path,
) -> None:
    input_set_id = "test-fixed-input-set"
    report = {
        "artifact_type": "experiment3_fixed_input_mujoco_smoke",
        "schema_version": "1.0",
        "input_set_id": input_set_id,
        "passed": True,
        "robots": {
            robot: {
                "instance_id": f"{robot}-instance",
                "scene_entrypoint": f"{robot}/scene.xml",
                "case_id": f"{robot}-nominal",
                "operator_kind": "final_joint_position_error",
                "scene_loaded": True,
                "reset_applied": True,
                "trusted_operator_executed": True,
                "measurement_finite": True,
                "measurement_value": 0.0,
                "passed": True,
            }
            for robot in runner.ROBOT_CONFIGURATIONS
        },
    }
    path = tmp_path / "smoke_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    checked = runner._check_fixed_input_smoke_report(
        path,
        input_set_id=input_set_id,
    )
    assert checked["passed"] is True
    assert tuple(checked["robots"]) == runner.ROBOT_CONFIGURATIONS

    report["robots"][runner.ROBOT_CONFIGURATIONS[-1]]["measurement_value"] = float(
        "nan"
    )
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(runner.Experiment3RunnerError, match="finite numeric"):
        runner._check_fixed_input_smoke_report(path, input_set_id=input_set_id)


def _calibrated_joint_fixture(tmp_path: Path) -> tuple[dict, list[dict], dict, object]:
    package_root = tmp_path / "package"
    private_dir = package_root / "tasks" / "private"
    private_dir.mkdir(parents=True)
    (package_root / "scene.xml").write_text("<mujoco/>", encoding="utf-8")
    (private_dir / "instances.json").write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "instance_id": "joint-instance",
                        "scene_entrypoint": "scene.xml",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    numeric_tolerance = 64.0 * sys.float_info.epsilon * 2.0
    sealed_threshold = 0.01 + numeric_tolerance
    source_id = "exp3-calibration-test_robot-c1"
    criterion = {
        "metric": "terminal joint error",
        "unit": "rad",
        "threshold": sealed_threshold,
        "source_refs": [{"source_id": source_id, "specific_reference": "report"}],
    }
    capability = {
        "capability_id": "C1",
        "method_name": "set_joint_position",
        "request_schema": {
            "properties": {
                "target_position": {"minimum": 0.5, "maximum": 1.0}
            }
        },
        "criteria": [criterion],
    }
    cases = []
    report_cases = {}
    for role, target in (("nominal", 0.5), ("calibrated_boundary", 1.0)):
        case_id = f"test-{role}"
        cases.append(
            {
                "case_id": case_id,
                "case_role": role,
                "capability_id": "C1",
                "instance_id": "joint-instance",
                "request": {"target_position": target, "max_duration_s": 2.0},
                "measurement_binding": {
                    "metric": "terminal joint error",
                    "unit": "rad",
                    "kind": "final_joint_position_error",
                    "parameters": {
                        "joint_name": "joint",
                        "target_argument": "request.target_position",
                    },
                },
            }
        )
        report_cases[role] = {
            "case_id": case_id,
            "target": target,
            "max_duration_s": 2.0,
            "terminal_errors": [0.01, 0.009, 0.008],
            "max_terminal_error": 0.01,
            "trusted_actuator_executed": True,
            "physics_stepped": True,
        }
    record = {
        "instance_id": "joint-instance",
        "scene_entrypoint": "scene.xml",
        "method_name": "set_joint_position",
        "criterion_source_id": source_id,
        "metric": "terminal joint error",
        "unit": "rad",
        "joint_name": "joint",
        "actuator_name": "joint_actuator",
        "controller_kind": "trusted_constant_position_control",
        "timestep_s": 0.002,
        "repetitions": 3,
        "numeric_tolerance_rule": runner.NUMERIC_TOLERANCE_RULE,
        "reset_position": 0.0,
        "joint_lower": -1.0,
        "joint_upper": 1.0,
        "boundary_candidates": [
            {
                "target": -1.0,
                "control": -1.0,
                "terminal": -1.0,
                "error": 0.0,
                "requested_displacement": 1.0,
                "actual_displacement": 1.0,
                "executable": True,
            },
            {
                "target": 1.0,
                "control": 1.0,
                "terminal": 1.0,
                "error": 0.0,
                "requested_displacement": 1.0,
                "actual_displacement": 1.0,
                "executable": True,
            },
        ],
        "selected_boundary": 1.0,
        "cases": report_cases,
        "numeric_tolerance": numeric_tolerance,
        "sealed_threshold": sealed_threshold,
    }
    package = SimpleNamespace(root=package_root, private_dir=private_dir)
    return capability, cases, record, package


def test_real_threshold_gate_rejects_a_noop_passing_tolerance(tmp_path: Path) -> None:
    capability, cases, record, package = _calibrated_joint_fixture(tmp_path)
    checked = runner._check_calibrated_capability(
        robot="test_robot",
        capability=capability,
        cases=cases,
        record=record,
        package=package,
        criterion_source_id=record["criterion_source_id"],
    )
    assert checked["sealed_threshold"] < 0.5

    numeric_tolerance = record["numeric_tolerance"]
    no_op_threshold = 0.5
    for report_case in record["cases"].values():
        error = no_op_threshold - numeric_tolerance
        report_case["terminal_errors"] = [error, error, error]
        report_case["max_terminal_error"] = error
    record["sealed_threshold"] = no_op_threshold
    capability["criteria"][0]["threshold"] = no_op_threshold
    with pytest.raises(runner.Experiment3RunnerError, match="no-op"):
        runner._check_calibrated_capability(
            robot="test_robot",
            capability=capability,
            cases=cases,
            record=record,
            package=package,
            criterion_source_id=record["criterion_source_id"],
        )


def test_fixed_threshold_gate_recognises_the_retired_span_protocol() -> None:
    assert runner._forbidden_threshold_protocol(
        {
            "source_id": "robot_joint_mjcf_range_protocol",
            "specific_reference": "sealed as 0.01 * span",
        }
    )


def test_directional_gate_rejects_measuring_a_site_endpoint_by_parent_body() -> None:
    design = {
        "capabilities": [
            {
                "capability_id": "C1",
                "method_name": "move_observed_tool_to_position",
            },
            {
                "capability_id": "C2",
                "method_name": "move_observed_tool_along_direction",
                "preconditions": ["request.direction is unit norm within 1e-6"],
                "invariants": ["direction remains unit norm within 1e-6"],
            },
        ]
    }
    reach_binding = {
        "kind": "final_site_frame_xyz_position_error",
        "parameters": {"site_name": "tool_site"},
    }
    wrong_direction_binding = {
        "kind": "final_body_directional_displacement_error",
        "parameters": {
            "body_name": "tool_parent",
            "direction_x_argument": "request.direction.x",
            "direction_y_argument": "request.direction.y",
            "direction_z_argument": "request.direction.z",
            "target_distance_argument": "request.distance_m",
        },
    }
    suite = {
        "cases": [
            *[
                {
                    "capability_id": "C1",
                    "measurement_binding": reach_binding,
                }
                for _ in range(2)
            ],
            *[
                {
                    "capability_id": "C2",
                    "measurement_binding": wrong_direction_binding,
                    "request": {
                        "direction": {"x": 1.0, "y": 0.0, "z": 0.0},
                        "distance_m": 0.1,
                    },
                }
                for _ in range(2)
            ],
        ]
    }
    with pytest.raises(runner.Experiment3RunnerError, match="site endpoint"):
        runner._check_directional_endpoint_contract(
            robot="test_robot", design=design, suite=suite
        )
