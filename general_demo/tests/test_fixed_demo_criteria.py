from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from autoadapter2.demo import evaluate_fixed_demo_criterion


ROOT = Path(__file__).resolve().parents[1]
SO_PRIVATE = ROOT / "libraries/tasks/so-arm101-follower-stock-gripper/1.0.0/evaluation_private.json"
GO2_PRIVATE = ROOT / "libraries/tasks/unitree-go2-stock-12dof/1.0.0/evaluation_private.json"


def _criteria() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for path in (SO_PRIVATE, GO2_PRIVATE):
        for criterion in json.loads(path.read_text(encoding="utf-8"))["criteria"]:
            if criterion["task_id"] in {"T01", "T02", "T03", "T08", "T20", "G01", "G02", "G03", "G04", "G05"}:
                result[criterion["task_id"]] = criterion
    return result


CRITERIA = _criteria()
GUARDS = {
    task_id: {guard_id: True for guard_id in criterion["guard_ids"]}
    for task_id, criterion in CRITERIA.items()
}


def _sample(task_id: str, values: dict[str, object], time_s: float, phase: str | None = None) -> dict[str, object]:
    item: dict[str, object] = {"time_s": time_s, "metrics": values}
    if phase is not None:
        item["phase"] = phase
    return item


def _positive_evidence(task_id: str) -> dict[str, object]:
    values: dict[str, object]
    duration = 1.0
    if task_id == "T01":
        values = {"tip_position_error_m": 0.010, "tip_speed_m_s": 0.005}
        duration = 0.5
    elif task_id == "T02":
        values = {
            "intended_tip_face_contact_dwell_s": 0.31,
            "target_object_displacement_m": 0.005,
            "other_object_contact_count": 0,
        }
        duration = 0.3
    elif task_id == "T03":
        values = {
            "cube_center_planar_goal_error_m": 0.010,
            "cube_table_supported": True,
            "cube_linear_speed_m_s": 0.005,
            "cube_angular_speed_rad_s": 0.05,
        }
    elif task_id == "T08":
        values = {
            "cube_height_increase_m": 0.025,
            "gripper_relative_cube_slip_m": 0.002,
            "cube_held": True,
        }
    elif task_id == "T20":
        values = {
            "specified_button_displacement_m": 0.004,
            "specified_button_activation_dwell_s": 0.30,
            "other_button_activation_count": 0,
        }
        duration = 0.25
    elif task_id == "G01":
        values = {"body_height_m": 0.20, "upright_score": 0.9, "planar_speed_m_s": 0.01}
    elif task_id == "G02":
        values = {"body_height_to_standing_height_ratio": 0.70, "upright_score": 0.9, "planar_speed_m_s": 0.01}
    elif task_id == "G03":
        values = {"body_height_m": 0.20, "upright_score": 0.9, "horizontal_drift_m": 0.02}
        duration = 2.0
    elif task_id == "G05":
        values = {"absolute_body_height_error_m": 0.01, "upright_score": 0.9, "horizontal_drift_m": 0.02}
    else:
        raise AssertionError(task_id)
    return {
        "task_id": task_id,
        "samples": [_sample(task_id, values, 0.0), _sample(task_id, values, duration)],
        "duration_s": duration,
        "guard_results": GUARDS[task_id],
        "candidate_return": {"success": False},
        "sdk_ack": False,
    }


def _g04_evidence() -> dict[str, object]:
    motion = {
        "forward_displacement_m": 0.04,
        "body_height_m": 0.20,
        "upright_score": 0.9,
        "absolute_lateral_displacement_m": 0.02,
    }
    terminal = {"planar_speed_m_s": 0.01}
    return {
        "task_id": "G04",
        "samples": [
            _sample("G04", motion, 0.0, "motion"),
            _sample("G04", motion, 0.6, "motion"),
            _sample("G04", terminal, 1.0, "terminal"),
            _sample("G04", terminal, 1.5, "terminal"),
        ],
        "duration_s": 1.5,
        "motion_duration_s": 0.6,
        "terminal_duration_s": 0.5,
        "guard_results": GUARDS["G04"],
        "sdk_ack": True,
    }


@pytest.mark.parametrize("task_id", sorted(CRITERIA))
def test_each_fixed_task_accepts_private_positive_samples(task_id: str) -> None:
    evidence = _g04_evidence() if task_id == "G04" else _positive_evidence(task_id)
    assert evaluate_fixed_demo_criterion(CRITERIA[task_id], evidence)


@pytest.mark.parametrize(
    ("task_id", "metric", "bad_value"),
    [
        ("T01", "tip_position_error_m", 0.021),
        ("T02", "target_object_displacement_m", 0.011),
        ("T03", "cube_table_supported", False),
        ("T08", "cube_held", False),
        ("T20", "other_button_activation_count", 1),
        ("G01", "body_height_m", 0.14),
        ("G02", "body_height_to_standing_height_ratio", 0.81),
        ("G03", "horizontal_drift_m", 0.051),
        ("G04", "planar_speed_m_s", 0.06),
        ("G05", "absolute_body_height_error_m", 0.031),
    ],
)
def test_each_fixed_task_rejects_one_decisive_private_failure(
    task_id: str, metric: str, bad_value: object
) -> None:
    evidence = _g04_evidence() if task_id == "G04" else _positive_evidence(task_id)
    broken = copy.deepcopy(evidence)
    if task_id == "G04":
        for sample in broken["samples"]:
            if sample["phase"] == "terminal":
                sample["metrics"][metric] = bad_value
    else:
        for sample in broken["samples"]:
            sample["metrics"][metric] = bad_value
    assert not evaluate_fixed_demo_criterion(CRITERIA[task_id], broken)


@pytest.mark.parametrize("task_id", sorted(CRITERIA))
def test_missing_duration_evidence_fails_for_both_robot_collections(task_id: str) -> None:
    evidence = _g04_evidence() if task_id == "G04" else _positive_evidence(task_id)
    for key in ("duration_s", "motion_duration_s", "terminal_duration_s"):
        evidence.pop(key, None)
    assert not evaluate_fixed_demo_criterion(CRITERIA[task_id], evidence)


def test_nan_and_false_guard_cannot_pass() -> None:
    evidence = _positive_evidence("T01")
    evidence["samples"][0]["metrics"]["tip_speed_m_s"] = math.nan
    assert not evaluate_fixed_demo_criterion(CRITERIA["T01"], evidence)

    evidence = _positive_evidence("G01")
    evidence["guard_results"][next(iter(evidence["guard_results"]))] = False
    assert not evaluate_fixed_demo_criterion(CRITERIA["G01"], evidence)


def test_task_id_and_comparator_are_part_of_the_private_contract() -> None:
    evidence = _positive_evidence("T01")
    mismatch = copy.deepcopy(evidence)
    mismatch["task_id"] = "T02"
    assert not evaluate_fixed_demo_criterion(CRITERIA["T01"], mismatch)

    criterion = copy.deepcopy(CRITERIA["T01"])
    criterion["checks"][0]["comparator"] = "approximately"
    assert not evaluate_fixed_demo_criterion(criterion, evidence)
