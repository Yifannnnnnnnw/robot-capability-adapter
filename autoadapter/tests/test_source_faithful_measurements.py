from __future__ import annotations

import math

import pytest

from autoadapter2.harness.measurements import (
    MeasurementError,
    aggregate_criterion,
    evaluate_temporal,
    measure,
)


def _evidence(position: list[float]) -> dict:
    return {
        "samples": [
            {
                "time": 0.0,
                "site_positions": {"fixture_site": position},
            }
        ]
    }


def _arguments(target: list[float]) -> dict:
    return {"request": {"task_parameters": {"target_position": target}}}


def test_site_axis_error_uses_only_the_source_axis() -> None:
    value = measure(
        {
            "kind": "final_site_axis_error",
            "parameters": {
                "site_name": "fixture_site",
                "target_argument": "request.task_parameters.target_position",
                "axis": 1,
            },
        },
        evidence=_evidence([10.0, 0.12, -8.0]),
        public_arguments=_arguments([0.0, 0.10, 0.0]),
    )
    assert value == pytest.approx(0.02)


def test_weighted_site_error_matches_metaworld_peg_formula() -> None:
    value = measure(
        {
            "kind": "final_weighted_site_position_error",
            "parameters": {
                "site_name": "fixture_site",
                "target_argument": "request.task_parameters.target_position",
                "weights": [1.0, 2.0, 2.0],
            },
        },
        evidence=_evidence([0.03, 0.04, 0.05]),
        public_arguments=_arguments([0.0, 0.0, 0.0]),
    )
    assert value == pytest.approx(math.sqrt(0.03**2 + 0.08**2 + 0.10**2))


def test_site_axis_error_rejects_an_invalid_axis() -> None:
    with pytest.raises(MeasurementError, match="axis 0, 1, or 2"):
        measure(
            {
                "kind": "final_site_axis_error",
                "parameters": {
                    "site_name": "fixture_site",
                    "target_argument": "request.task_parameters.target_position",
                    "axis": 3,
                },
            },
            evidence=_evidence([0.0, 0.0, 0.0]),
            public_arguments=_arguments([0.0, 0.0, 0.0]),
        )


def _body_sample(
    time: float,
    position: list[float],
    quaternion: list[float] | None = None,
    *,
    feet_x: float | None = None,
) -> dict:
    body_positions = {"base_link": position}
    if feet_x is not None:
        body_positions.update(
            {name: [feet_x, offset, 0.0] for name, offset in zip(
                ("FL_foot", "FR_foot", "RL_foot", "RR_foot"),
                (0.1, -0.1, 0.1, -0.1),
            )}
        )
    return {
        "time": time,
        "body_positions": body_positions,
        "body_quaternions": {"base_link": quaternion or [1.0, 0.0, 0.0, 0.0]},
    }


def test_heading_error_uses_commanded_world_direction() -> None:
    evidence = {
        "samples": [
            _body_sample(0.0, [0.0, 0.0, 0.3]),
            _body_sample(1.0, [1.0, 1.0, 0.3]),
        ]
    }
    value = measure(
        {
            "kind": "mean_body_heading_error_deg",
            "parameters": {
                "body_name": "base_link",
                "direction_argument": "request.task_parameters.direction_rad",
                "minimum_displacement": 0.1,
            },
        },
        evidence=evidence,
        public_arguments={
            "request": {"task_parameters": {"direction_rad": math.pi / 4.0}}
        },
    )
    assert value == pytest.approx(0.0)


def test_directional_displacement_projects_onto_each_command() -> None:
    evidence = {
        "samples": [
            _body_sample(0.0, [0.0, 0.0, 0.3]),
            _body_sample(1.0, [-3.0, 4.0, 0.3]),
        ]
    }
    binding = {
        "kind": "body_directional_displacement",
        "parameters": {
            "body_name": "base_link",
            "direction_argument": "request.task_parameters.direction_rad",
        },
    }
    backward = measure(
        binding,
        evidence=evidence,
        public_arguments={
            "request": {"task_parameters": {"direction_rad": math.pi}}
        },
    )
    lateral = measure(
        binding,
        evidence=evidence,
        public_arguments={
            "request": {"task_parameters": {"direction_rad": math.pi / 2.0}}
        },
    )
    assert backward == pytest.approx(3.0)
    assert lateral == pytest.approx(4.0)


def test_directional_progress_rejects_corridor_and_lower_floor_bypasses() -> None:
    binding = {
        "kind": "body_directional_progress_until_corridor_exit",
        "parameters": {
            "body_name": "base_link",
            "direction_argument": "request.task_parameters.direction_rad",
            "limit_argument": "request.task_parameters.map_limit_m",
            "maximum_cross_track_m": 0.55,
            "minimum_height_m": 0.18,
        },
    }
    arguments = {
        "request": {
            "task_parameters": {"direction_rad": 0.0, "map_limit_m": 5.0}
        }
    }
    side_bypass = {
        "samples": [
            _body_sample(0.0, [0.0, 0.0, 0.3]),
            _body_sample(1.0, [2.0, 0.2, 0.3]),
            _body_sample(2.0, [3.0, 0.6, 0.3]),
            _body_sample(3.0, [6.0, 0.0, 0.3]),
        ]
    }
    lower_floor_bypass = {
        "samples": [
            _body_sample(0.0, [0.0, 0.0, 0.3]),
            _body_sample(1.0, [1.0, 0.0, 0.3]),
            _body_sample(2.0, [2.0, 0.0, 0.17]),
            _body_sample(3.0, [6.0, 0.0, 0.3]),
        ]
    }
    valid_but_over_limit = {
        "samples": [
            _body_sample(0.0, [0.0, 0.0, 0.3]),
            _body_sample(1.0, [6.0, 0.1, 0.3]),
        ]
    }

    assert measure(binding, evidence=side_bypass, public_arguments=arguments) == 2.0
    assert measure(binding, evidence=lower_floor_bypass, public_arguments=arguments) == 1.0
    assert measure(binding, evidence=valid_but_over_limit, public_arguments=arguments) == 5.0


def test_step_completion_requires_all_four_feet_past_finish() -> None:
    binding = {
        "kind": "named_bodies_axis_completion",
        "parameters": {
            "body_names": ["FL_foot", "FR_foot", "RL_foot", "RR_foot"],
            "axis": 0,
            "finish_coordinate": 1.0,
            "direction": 1,
        },
    }
    incomplete = {"samples": [_body_sample(1.0, [1.2, 0.0, 0.3], feet_x=0.99)]}
    complete = {"samples": [_body_sample(1.0, [1.2, 0.0, 0.3], feet_x=1.01)]}
    assert measure(binding, evidence=incomplete, public_arguments={}) == 0.0
    assert measure(binding, evidence=complete, public_arguments={}) == 1.0


def test_mean_yaw_rate_unwraps_pi_boundary() -> None:
    def quaternion(yaw_deg: float) -> list[float]:
        yaw = math.radians(yaw_deg)
        return [math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0)]

    evidence = {
        "samples": [
            _body_sample(0.0, [0.0, 0.0, 0.3], quaternion(170.0)),
            _body_sample(1.0, [0.0, 0.0, 0.3], quaternion(-170.0)),
        ]
    }
    value = measure(
        {"kind": "mean_body_yaw_rate", "parameters": {"body_name": "base_link"}},
        evidence=evidence,
        public_arguments={},
    )
    assert value == pytest.approx(math.radians(20.0))


def test_ordered_waypoints_cannot_be_claimed_out_of_order() -> None:
    binding = {
        "kind": "ordered_body_waypoint_completion_ratio",
        "parameters": {
            "body_name": "base_link",
            "waypoints": [[1.0, 0.0], [2.0, 0.0]],
            "tolerance": 0.1,
        },
    }
    evidence = {
        "samples": [
            _body_sample(0.0, [0.0, 0.0, 0.3]),
            _body_sample(1.0, [2.0, 0.0, 0.3]),
            _body_sample(2.0, [1.0, 0.0, 0.3]),
        ]
    }
    assert measure(binding, evidence=evidence, public_arguments={}) == 0.5


def test_named_contact_uses_every_physics_step_not_video_samples() -> None:
    evidence = {
        "samples": [_body_sample(0.0, [0.0, 0.0, 0.3])],
        "contact_pair_step_counts": [
            {"geom1": "broad_jump", "geom2": "FL", "step_count": 3},
            {"geom1": "floor", "geom2": "FR", "step_count": 100},
        ],
    }
    value = measure(
        {
            "kind": "named_geom_contact_step_count",
            "parameters": {"geom_names": ["broad_jump"]},
        },
        evidence=evidence,
        public_arguments={},
    )
    assert value == 3.0


def test_ordered_axis_gates_require_the_source_order() -> None:
    binding = {
        "kind": "ordered_body_axis_gate_completion_ratio",
        "parameters": {
            "body_name": "base_link",
            "gates": [
                {"axis": 0, "coordinate": 1.0, "direction": 1},
                {"axis": 1, "coordinate": 1.0, "direction": 1},
            ],
        },
    }
    evidence = {
        "samples": [
            _body_sample(0.0, [0.0, 1.1, 0.3]),
            _body_sample(1.0, [1.1, 0.0, 0.3]),
        ]
    }
    assert measure(binding, evidence=evidence, public_arguments={}) == 0.5


def test_ordered_body_waypoint_completion_time_uses_first_complete_sample() -> None:
    binding = {
        "kind": "ordered_body_waypoint_completion_time",
        "parameters": {
            "body_name": "base_link",
            "waypoints": [[1.0, 0.0], [2.0, 0.0]],
            "tolerance": 0.15,
        },
    }
    evidence = {
        "samples": [
            _body_sample(0.0, [0.0, 0.0, 0.3]),
            _body_sample(1.5, [1.0, 0.0, 0.3]),
            _body_sample(3.25, [2.0, 0.0, 0.3]),
            _body_sample(4.0, [2.4, 0.0, 0.3]),
        ]
    }
    assert measure(binding, evidence=evidence, public_arguments={}) == 3.25
    with pytest.raises(MeasurementError, match="were not completed"):
        measure(
            binding,
            evidence={"samples": evidence["samples"][:2]},
            public_arguments={},
        )


def test_point_clearance_checks_the_whole_trajectory() -> None:
    evidence = {
        "samples": [
            _body_sample(0.0, [0.0, 0.0, 0.3]),
            _body_sample(1.0, [0.9, 0.0, 0.3]),
            _body_sample(2.0, [2.0, 0.0, 0.3]),
        ]
    }
    value = measure(
        {
            "kind": "minimum_body_point_clearance",
            "parameters": {
                "body_name": "base_link",
                "points": [[1.0, 0.0], [3.0, 0.0]],
            },
        },
        evidence=evidence,
        public_arguments={},
    )
    assert value == pytest.approx(0.1)


def _leap_sample(
    time: float,
    *,
    body_positions: dict[str, list[float]] | None = None,
    body_quaternions: dict[str, list[float]] | None = None,
    site_positions: dict[str, list[float]] | None = None,
    joint_positions: dict[str, float] | None = None,
    contacts: list[dict[str, str]] | None = None,
) -> dict:
    return {
        "time": time,
        "body_positions": body_positions or {},
        "body_quaternions": body_quaternions or {},
        "site_positions": site_positions or {},
        "joint_positions": joint_positions or {},
        "contacts": contacts or [],
    }


def _z_quaternion(angle: float) -> list[float]:
    return [math.cos(angle / 2.0), 0.0, 0.0, math.sin(angle / 2.0)]


def test_leap_ec_pattern_returns_one_binary_trial_not_contact_steps() -> None:
    contacts = [
        {"geom1": "index_tip", "geom2": "object"},
        {"geom1": "thumb_tip", "geom2": "object"},
    ]
    samples = [
        _leap_sample(
            time,
            body_positions={"palm": [0.0, 0.0, 0.0], "object": position},
            body_quaternions={
                "palm": [1.0, 0.0, 0.0, 0.0],
                "object": [1.0, 0.0, 0.0, 0.0],
            },
            contacts=contacts,
        )
        for time, position in (
            (0.0, [0.0, 0.0, 0.0]),
            (0.1, [0.03, 0.0, 0.0]),
            (0.2, [0.0, 0.0, 0.0]),
        )
    ]
    binding = {
        "kind": "in_hand_object_pattern_success",
        "parameters": {
            "body_name": "object",
            "reference_body_name": "palm",
            "object_geom_names": ["object"],
            "required_robot_geom_groups": [["index_tip"], ["thumb_tip"]],
            "minimum_contact_steps": 10,
            "minimum_simultaneous_contact_samples": 2,
            "motion_kind": "translation_return",
            "axis": 0,
            "minimum_translation_range_m": 0.02,
            "maximum_return_error_m": 0.005,
        },
    }
    evidence = {
        "samples": samples,
        "contact_pair_step_counts": [
            {"geom1": "index_tip", "geom2": "object", "step_count": 20},
            {"geom1": "thumb_tip", "geom2": "object", "step_count": 20},
        ],
    }

    assert measure(binding, evidence=evidence, public_arguments={}) == 1.0
    evidence["contact_pair_step_counts"].pop()
    assert measure(binding, evidence=evidence, public_arguments={}) == 0.0


def test_leap_ec_contact_slide_requires_physical_gaiting_contacts() -> None:
    samples = []
    for index, active in enumerate(("index", "middle", "index", "middle")):
        samples.append(
            _leap_sample(
                index * 0.1,
                body_positions={"object": [0.0, 0.0, 0.0]},
                body_quaternions={"object": [1.0, 0.0, 0.0, 0.0]},
                site_positions={
                    "index_site": [0.01 * index, 0.0, 0.0],
                    "middle_site": [0.01 * index, 0.0, 0.0],
                },
                contacts=[{"geom1": f"{active}_tip", "geom2": "object"}],
            )
        )
    binding = {
        "kind": "in_hand_object_pattern_success",
        "parameters": {
            "body_name": "object",
            "reference_body_name": "object",
            "object_geom_names": ["object"],
            "required_robot_geom_groups": [["index_tip"], ["middle_tip"]],
            "minimum_contact_steps": 2,
            "minimum_contact_group_transitions": 3,
            "motion_kind": "contact_slide",
            "site_names": ["index_site", "middle_site"],
            "axis": 0,
            "minimum_site_translation_range_m": 0.015,
        },
    }
    evidence = {
        "samples": samples,
        "contact_pair_step_counts": [
            {"geom1": "index_tip", "geom2": "object", "step_count": 10},
            {"geom1": "middle_tip", "geom2": "object", "step_count": 10},
        ],
    }
    assert measure(binding, evidence=evidence, public_arguments={}) == 1.0

    samples[-1]["contacts"] = samples[-2]["contacts"]
    assert measure(binding, evidence=evidence, public_arguments={}) == 0.0


def test_leap_concatenated_fingertips_are_measured_in_the_palm_frame() -> None:
    evidence = {
        "samples": [
            _leap_sample(
                0.0,
                body_positions={"palm": [1.0, 2.0, 3.0]},
                body_quaternions={"palm": _z_quaternion(math.pi / 2.0)},
                site_positions={
                    "index_tip": [1.0, 2.1, 3.0],
                    "thumb_tip": [0.8, 2.0, 3.0],
                },
            )
        ]
    }
    value = measure(
        {
            "kind": "final_concatenated_site_position_error",
            "parameters": {
                "site_names": ["index_tip", "thumb_tip"],
                "reference_body_name": "palm",
                "target_argument": "request.task_parameters.target",
            },
        },
        evidence=evidence,
        public_arguments={
            "request": {
                "task_parameters": {"target": [0.1, 0.0, 0.0, 0.0, 0.2, 0.0]}
            }
        },
    )
    assert value == pytest.approx(0.0, abs=1e-12)


def test_leap_block_position_clause_enforces_same_state_orientation() -> None:
    target_orientation = _z_quaternion(0.4)
    samples = [
        _leap_sample(
            0.0,
            body_positions={"palm": [0.0, 0.0, 0.0], "block": [0.0, 0.0, 0.0]},
            body_quaternions={
                "palm": [1.0, 0.0, 0.0, 0.0],
                "block": [1.0, 0.0, 0.0, 0.0],
            },
        ),
        _leap_sample(
            1.0,
            body_positions={"palm": [0.0, 0.0, 0.0], "block": [0.01, -0.02, 0.0]},
            body_quaternions={
                "palm": [1.0, 0.0, 0.0, 0.0],
                "block": target_orientation,
            },
        ),
    ]
    binding = {
        "kind": "final_body_position_offset_error",
        "parameters": {
            "body_name": "block",
            "reference_body_name": "palm",
            "target_argument": "request.task_parameters.position",
            "orientation_target_argument": "request.task_parameters.orientation",
            "maximum_orientation_error_rad": 0.1,
        },
    }
    arguments = {
        "request": {
            "task_parameters": {
                "position": [0.01, -0.02, 0.0],
                "orientation": target_orientation,
            }
        }
    }
    assert measure(binding, evidence={"samples": samples}, public_arguments=arguments) == 0.0

    samples[-1]["body_quaternions"]["block"] = [1.0, 0.0, 0.0, 0.0]
    with pytest.raises(MeasurementError, match="orientation conjunction"):
        measure(binding, evidence={"samples": samples}, public_arguments=arguments)


def test_leap_block_orientation_clause_enforces_same_state_position() -> None:
    target_orientation = _z_quaternion(0.4)
    samples = [
        _leap_sample(
            0.0,
            body_positions={"palm": [0.0, 0.0, 0.0], "block": [0.0, 0.0, 0.0]},
            body_quaternions={
                "palm": [1.0, 0.0, 0.0, 0.0],
                "block": [1.0, 0.0, 0.0, 0.0],
            },
        ),
        _leap_sample(
            1.0,
            body_positions={"palm": [0.0, 0.0, 0.0], "block": [0.01, 0.0, 0.0]},
            body_quaternions={
                "palm": [1.0, 0.0, 0.0, 0.0],
                "block": [-value for value in target_orientation],
            },
        ),
    ]
    binding = {
        "kind": "final_body_quaternion_error",
        "parameters": {
            "body_name": "block",
            "reference_body_name": "palm",
            "target_argument": "request.task_parameters.orientation",
            "position_target_argument": "request.task_parameters.position",
            "maximum_position_error_m": 0.01,
        },
    }
    arguments = {
        "request": {
            "task_parameters": {
                "position": [0.01, 0.0, 0.0],
                "orientation": target_orientation,
            }
        }
    }
    assert measure(
        binding, evidence={"samples": samples}, public_arguments=arguments
    ) == pytest.approx(0.0)

    samples[-1]["body_positions"]["block"] = [0.03, 0.0, 0.0]
    with pytest.raises(MeasurementError, match="position conjunction"):
        measure(binding, evidence={"samples": samples}, public_arguments=arguments)


def test_leap_maximum_joint_error_uses_all_declared_joint_targets() -> None:
    value = measure(
        {
            "kind": "final_maximum_joint_position_error",
            "parameters": {
                "joint_names": ["joint_a", "joint_b"],
                "target_argument": "request.task_parameters.targets",
            },
        },
        evidence={
            "samples": [
                _leap_sample(0.0, joint_positions={"joint_a": 0.11, "joint_b": -0.22})
            ]
        },
        public_arguments={
            "request": {"task_parameters": {"targets": [0.1, -0.2]}}
        },
    )
    assert value == pytest.approx(0.02)


def test_leap_fixture_target_error_wraps_across_pi() -> None:
    value = measure(
        {
            "kind": "final_wrapped_joint_position_error",
            "parameters": {
                "joint_name": "fixture",
                "target_argument": "request.task_parameters.target",
            },
        },
        evidence={
            "samples": [
                _leap_sample(
                    0.0,
                    joint_positions={"fixture": -math.pi + 0.01},
                )
            ]
        },
        public_arguments={
            "request": {"task_parameters": {"target": math.pi - 0.01}}
        },
    )
    assert value == pytest.approx(0.02)


def test_leap_fixture_trajectory_uses_velocity_over_each_control_step() -> None:
    evidence = {
        "samples": [
            _leap_sample(0.0, joint_positions={"fixture": 0.0}),
            _leap_sample(0.1, joint_positions={"fixture": 0.05}),
            _leap_sample(0.2, joint_positions={"fixture": 0.101}),
        ]
    }
    value = measure(
        {
            "kind": "maximum_joint_linear_trajectory_error",
            "parameters": {
                "joint_name": "fixture",
                "velocity_argument": "request.task_parameters.velocity",
                "control_period_s": 0.1,
                "control_steps_argument": "request.task_parameters.max_control_steps",
            },
        },
        evidence=evidence,
        public_arguments={
            "request": {
                "task_parameters": {"velocity": 0.5, "max_control_steps": 2}
            }
        },
    )
    assert value == pytest.approx(0.001)


def test_leap_hold_metrics_enforce_the_same_complete_horizon() -> None:
    samples = [
        _leap_sample(
            index * 0.1,
            body_positions={"palm": [0.0, 0.0, 0.0], "object": [0.0, 0.0, 0.0]},
            body_quaternions={
                "palm": [1.0, 0.0, 0.0, 0.0],
                "object": [1.0, 0.0, 0.0, 0.0],
            },
        )
        for index in range(8)
    ]
    common = {
        "body_name": "object",
        "reference_body_name": "palm",
        "target_argument": "request.task_parameters.target",
        "solved_distance_m": 0.01,
        "drop_distance_m": 0.3,
        "minimum_solved_steps": 5,
        "control_period_s": 0.1,
        "control_steps_argument": "request.task_parameters.max_control_steps",
    }
    arguments = {
        "request": {
            "task_parameters": {"target": [0.0, 0.0, 0.0], "max_control_steps": 7}
        }
    }
    evidence = {"samples": samples}
    assert measure(
        {"kind": "body_target_solved_sample_count", "parameters": common},
        evidence=evidence,
        public_arguments=arguments,
    ) == 7.0
    assert measure(
        {"kind": "body_target_drop_event_count", "parameters": common},
        evidence=evidence,
        public_arguments=arguments,
    ) == 0.0

    samples[-1]["body_positions"]["object"] = [0.31, 0.0, 0.0]
    with pytest.raises(MeasurementError, match="no-drop conjunction"):
        measure(
            {"kind": "body_target_solved_sample_count", "parameters": common},
            evidence=evidence,
            public_arguments=arguments,
        )
    assert measure(
        {"kind": "body_target_drop_event_count", "parameters": common},
        evidence=evidence,
        public_arguments=arguments,
    ) == 1.0


def test_leap_baoding_ratio_uses_both_actual_ball_trajectories() -> None:
    period = 0.4
    radii = [0.025, 0.028]

    def positions(time: float) -> dict[str, list[float]]:
        phase = 2.0 * math.pi * time / period
        return {
            "palm": [0.0, 0.0, 0.0],
            "ball_1": [radii[0] * math.cos(phase), radii[1] * math.sin(phase), 1.3],
            "ball_2": [
                radii[0] * math.cos(phase + math.pi),
                radii[1] * math.sin(phase + math.pi),
                1.3,
            ],
        }

    samples = [
        _leap_sample(
            time,
            body_positions=positions(time),
            body_quaternions={
                "palm": [1.0, 0.0, 0.0, 0.0],
                "ball_1": [1.0, 0.0, 0.0, 0.0],
                "ball_2": [1.0, 0.0, 0.0, 0.0],
            },
        )
        for time in (0.0, 0.1, 0.2)
    ]
    binding = {
        "kind": "mean_two_body_orbit_tracking_fraction",
        "parameters": {
            "body_names": ["ball_1", "ball_2"],
            "reference_body_name": "palm",
            "orbit_center": [0.0, 0.0, 1.3],
            "radii_argument": "request.task_parameters.radii",
            "period_argument": "request.task_parameters.period",
            "maximum_tracking_error_m": 0.015,
            "minimum_source_height": 1.25,
            "source_height_offset_m": 0.0,
            "control_period_s": 0.1,
            "control_steps_argument": "request.task_parameters.max_control_steps",
        },
    }
    arguments = {
        "request": {
            "task_parameters": {
                "radii": radii,
                "period": period,
                "max_control_steps": 2,
            }
        }
    }
    evidence = {"samples": samples}
    assert measure(binding, evidence=evidence, public_arguments=arguments) == 1.0

    samples[-1]["body_positions"]["ball_2"][0] += 0.02
    assert measure(binding, evidence=evidence, public_arguments=arguments) == 0.5


def _horizon_binding() -> dict:
    return {
        "kind": "physics_step_count",
        "parameters": {
            "physics_steps_per_control_step": 2,
            "control_steps_argument": "request.task_parameters.max_control_steps",
            "control_period_s": 0.1,
        },
    }


def _horizon_arguments() -> dict:
    return {"request": {"task_parameters": {"max_control_steps": 3}}}


def test_fixed_trials_rejects_contact_counts_and_aggregates_three_binary_runs() -> None:
    with pytest.raises(MeasurementError, match="binary outcome"):
        evaluate_temporal(
            {"kind": "physics_step_count", "parameters": {}},
            criterion={
                "comparator": "==",
                "threshold": 3,
                "temporal": {"kind": "fixed_trials", "trial_count": 3},
            },
            evidence={"samples": [{"time": 0.0}], "step_count": 20},
            public_arguments={},
        )

    result = aggregate_criterion(
        {"kind": "all_trials"},
        comparator="==",
        threshold=3,
        values=[1.0, 1.0, 1.0],
        temporal_passes=[True, True, True],
    )
    assert result == {"kind": "all_trials", "passed": True, "value": 3.0}
    assert not aggregate_criterion(
        {"kind": "all_trials"},
        comparator="==",
        threshold=3,
        values=[20.0, 20.0, 20.0],
        temporal_passes=[True, True, True],
    )["passed"]


def test_terminal_step_requires_the_complete_physics_horizon() -> None:
    criterion = {
        "comparator": "<=",
        "threshold": 6,
        "temporal": {"kind": "terminal_step", "max_control_steps": 3},
    }
    complete = evaluate_temporal(
        _horizon_binding(),
        criterion=criterion,
        evidence={"samples": [{"time": 0.0}, {"time": 0.3}], "step_count": 6},
        public_arguments=_horizon_arguments(),
    )
    early = evaluate_temporal(
        _horizon_binding(),
        criterion=criterion,
        evidence={"samples": [{"time": 0.0}, {"time": 0.25}], "step_count": 5},
        public_arguments=_horizon_arguments(),
    )
    assert complete["passed"]
    assert not early["passed"]


def test_fixed_horizon_requires_exact_steps_duration_and_control_samples() -> None:
    criterion = {
        "comparator": "==",
        "threshold": 6,
        "temporal": {"kind": "fixed_horizon", "max_control_steps": 3},
    }
    samples = [{"time": value} for value in (0.0, 0.1, 0.2, 0.3)]
    complete = evaluate_temporal(
        _horizon_binding(),
        criterion=criterion,
        evidence={"samples": samples, "step_count": 6},
        public_arguments=_horizon_arguments(),
    )
    early = evaluate_temporal(
        _horizon_binding(),
        criterion=criterion,
        evidence={"samples": samples, "step_count": 5},
        public_arguments=_horizon_arguments(),
    )
    assert complete["passed"] and complete["value"] == 6.0
    assert not early["passed"] and early["value"] is None


def test_continuous_source_horizon_rejects_an_early_execution() -> None:
    criterion = {
        "comparator": "<=",
        "threshold": 6,
        "temporal": {
            "kind": "continuous",
            "max_control_steps": 3,
            "duration_s": 0.3,
        },
    }
    samples = [{"time": value} for value in (0.0, 0.1, 0.2, 0.3)]
    complete = evaluate_temporal(
        _horizon_binding(),
        criterion=criterion,
        evidence={"samples": samples, "step_count": 6},
        public_arguments=_horizon_arguments(),
    )
    early = evaluate_temporal(
        _horizon_binding(),
        criterion=criterion,
        evidence={"samples": samples, "step_count": 5},
        public_arguments=_horizon_arguments(),
    )
    assert complete["passed"]
    assert not early["passed"]


@pytest.mark.parametrize(
    "kind",
    [
        "all_four_fingertips",
        "same_state_conjunction",
        "maximum_over_all_16_joints",
        "maximum_over_control_steps",
        "count_successful_steps",
        "count_events",
        "mean_over_control_steps",
    ],
)
def test_leap_single_execution_aggregations_compare_the_trusted_value(kind: str) -> None:
    assert aggregate_criterion(
        {"kind": kind},
        comparator="<",
        threshold=0.2,
        values=[0.1],
        temporal_passes=[True],
    ) == {"kind": kind, "passed": True, "value": 0.1}
