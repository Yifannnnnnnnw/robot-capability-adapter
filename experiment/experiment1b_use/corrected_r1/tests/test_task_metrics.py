from __future__ import annotations

from pathlib import Path
import sys


CORRECTED_ROOT = Path(__file__).resolve().parents[1]
if str(CORRECTED_ROOT) not in sys.path:
    sys.path.insert(0, str(CORRECTED_ROOT))

from runtime.task_metrics import (
    evaluate_edge_completion,
    evaluate_t06_course,
    evaluate_t16_table_transfer,
    evaluate_t17_weave,
    evaluate_terminal_body_goal,
    evaluate_terminal_site_goal,
)
from runtime.verdict import compose_corrected_harness_verdict


FEET = ("FL_foot", "FR_foot", "RL_foot", "RR_foot")


def _sample(
    time_s: float,
    *,
    bodies: dict[str, tuple[float, float, float]] | None = None,
    sites: dict[str, tuple[float, float, float]] | None = None,
) -> dict[str, object]:
    return {
        "time": time_s,
        "body_positions": bodies or {},
        "site_positions": sites or {},
    }


def _foot_sample(x_m: float) -> dict[str, object]:
    return _sample(0.0, bodies={name: (x_m, 0.0, 0.02) for name in FEET})


def test_t02_t03_corrected_edge_boundary_is_inclusive() -> None:
    assert evaluate_edge_completion([_foot_sample(0.422)])["passed"] is True
    assert evaluate_edge_completion([_foot_sample(0.421999)])["passed"] is False


def test_t06_requires_ordered_far_edges_and_each_contact_family() -> None:
    gates = [
        {"axis": 0, "coordinate": coordinate, "direction": 1}
        for coordinate in (0.962, 1.82, 2.4915, 4.28, 4.8)
    ]
    samples = [
        _sample(float(index), bodies={"base_link": (x_m, 0.0, 0.3)})
        for index, x_m in enumerate((0.0, 0.962, 1.82, 2.4915, 4.28, 4.8))
    ]
    groups = [
        {"family_id": family, "geom_names": [geom]}
        for family, geom in (
            ("inclined_platform", "incline"),
            ("raised_platform", "raised"),
            ("stairs", "stair_1"),
            ("block_family", "block_1"),
        )
    ]
    contacts = [
        {"geom1": "FL_foot_geom", "geom2": geom, "step_count": 2}
        for geom in ("incline", "raised", "stair_1", "block_1")
    ]

    passed = evaluate_t06_course(
        samples,
        contacts,
        body_name="base_link",
        gates=gates,
        robot_geom_names=["FL_foot_geom"],
        required_contact_geom_groups=groups,
    )
    bypass = evaluate_t06_course(
        samples,
        contacts[:-1],
        body_name="base_link",
        gates=gates,
        robot_geom_names=["FL_foot_geom"],
        required_contact_geom_groups=groups,
    )

    assert passed["ordered_gate_completion_ratio"] == 1.0
    assert passed["passed"] is True
    assert bypass["ordered_gate_completion_ratio"] == 1.0
    assert bypass["contact_requirements_passed"] is False
    assert bypass["passed"] is False


def test_t16_4_95_seconds_dwell_fails_and_5_00_passes() -> None:
    def trajectory(end_time: float) -> list[dict[str, object]]:
        return [
            _sample(0.0, bodies={"base_link": (0.0, 0.0, 0.3)}),
            _sample(0.05, bodies={"base_link": (0.7, 0.0, 0.3)}),
            _sample(end_time, bodies={"base_link": (0.7, 0.0, 0.3)}),
        ]

    short = evaluate_t16_table_transfer(
        trajectory(5.0), body_name="base_link", target_position_m=(1.0, 0.0, 0.3)
    )
    exact = evaluate_t16_table_transfer(
        trajectory(5.05), body_name="base_link", target_position_m=(1.0, 0.0, 0.3)
    )

    assert short["longest_end_table_dwell_s"] == 4.95
    assert short["passed"] is False
    assert exact["longest_end_table_dwell_s"] == 5.0
    assert exact["passed"] is True


def test_t17_straight_line_fails_but_five_point_weave_passes() -> None:
    waypoints = [
        (0.55, 0.25),
        (1.25, -0.25),
        (1.90, 0.25),
        (2.45, -0.25),
        (3.05, 0.25),
    ]
    poles = [(x_m, 0.0) for x_m, _ in waypoints]
    straight = [
        _sample(float(index), bodies={"base_link": (x_m, 0.0, 0.3)})
        for index, (x_m, _) in enumerate(waypoints)
    ]
    weave = [
        _sample(0.0, bodies={"base_link": (0.0, 0.0, 0.3)}),
        *[
            _sample(float(index), bodies={"base_link": (x_m, y_m, 0.3)})
            for index, (x_m, y_m) in enumerate(waypoints, start=1)
        ],
    ]

    straight_result = evaluate_t17_weave(
        straight,
        body_name="base_link",
        waypoints=waypoints,
        pole_points=poles,
    )
    weave_result = evaluate_t17_weave(
        weave,
        body_name="base_link",
        waypoints=waypoints,
        pole_points=poles,
    )

    assert straight_result["passed"] is False
    assert straight_result["ordered_waypoint_completion_ratio"] == 0.0
    assert weave_result["ordered_waypoint_completion_ratio"] == 1.0
    assert weave_result["minimum_torso_pole_clearance_m"] == 0.25
    assert weave_result["passed"] is True


def test_wall_and_dial_goal_threshold_is_inclusive_at_7cm() -> None:
    wall_exact = evaluate_terminal_body_goal(
        [_sample(0.0, bodies={"workpiece": (0.07, 0.0, 0.0)})],
        body_name="workpiece",
        target_position_m=(0.0, 0.0, 0.0),
    )
    wall_outside = evaluate_terminal_body_goal(
        [_sample(0.0, bodies={"workpiece": (0.07001, 0.0, 0.0)})],
        body_name="workpiece",
        target_position_m=(0.0, 0.0, 0.0),
    )
    dial_exact = evaluate_terminal_site_goal(
        [_sample(0.0, sites={"dial_tip_site": (0.0, 0.07, 0.0)})],
        site_name="dial_tip_site",
        target_position_m=(0.0, 0.0, 0.0),
    )

    assert wall_exact["passed"] is True
    assert wall_outside["passed"] is False
    assert dial_exact["passed"] is True


def test_corrected_verdict_requires_metric_integrity_guards_and_video() -> None:
    result = compose_corrected_harness_verdict(
        task_metric={"passed": True},
        contact_integrity={"passed": True},
        physical_execution_passed=True,
        guard_outcomes={"physics": True, "no_state_write": True},
        video_complete=True,
    )

    assert result["formal_episode"] is False
    assert result["physical_harness_verdict"] == "PASS"
