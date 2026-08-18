from __future__ import annotations

import math

import pytest

from autoadapter2.harness.measurements import MeasurementError, measure


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
