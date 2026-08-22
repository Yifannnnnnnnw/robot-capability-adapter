from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoadapter2.b2.capability_adapter import (
    CapabilityAdapter,
    CapabilityAdapterError,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DESIGN_ROOT = (
    REPOSITORY_ROOT
    / "experiment"
    / "b2_recap"
    / "reference_validation"
    / "resolved"
)


def _design(robot_id: str) -> dict:
    return json.loads(
        (DESIGN_ROOT / robot_id / "capability_design.json").read_text(
            encoding="utf-8"
        )
    )


def test_so101_and_go2_catalogues_are_native_b1_capabilities_only() -> None:
    sink = lambda method, arguments: {"operation": {"status": "EXECUTED"}}
    so_adapter = CapabilityAdapter(_design("robotstudio_so101"), sink)
    go_adapter = CapabilityAdapter(_design("unitree-go2-stock-12dof"), sink)

    assert so_adapter.capability_design_id == (
        "experiment1-b1-fixed-interface::robotstudio_so101::v3"
    )
    assert go_adapter.capability_design_id == (
        "experiment1-b1-fixed-interface::unitree-go2-stock-12dof::v2"
    )
    assert [contract.capability_id for contract in so_adapter.contracts] == [
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
        "A6",
    ]
    assert [contract.capability_id for contract in go_adapter.contracts] == [
        "G1",
        "G2",
        "G3",
        "G4",
        "G5",
    ]
    assert [item["capability_name"] for item in so_adapter.public_catalog()] == [
        "move_end_effector_to_position",
        "trace_cartesian_path",
        "set_gripper_opening",
        "approach_until_contact",
        "move_cartesian_offset_and_return",
        "set_wrist_roll",
    ]
    assert [item["capability_name"] for item in go_adapter.public_catalog()] == [
        "track_planar_twist",
        "move_body_relative_pose",
        "trace_planar_path",
        "set_body_height",
        "hold_stable_stance",
    ]

    visible = json.dumps(
        so_adapter.public_catalog() + go_adapter.public_catalog(),
        sort_keys=True,
    ).lower()
    for forbidden in (
        "public_standard",
        "criterion",
        "threshold",
        "all_hidden_cases_required",
        "task_id",
        "task_parameters",
    ):
        assert forbidden not in visible

    expected_request_fields = {
        "move_end_effector_to_position": {
            "target_position_m",
            "max_duration_s",
        },
        "trace_cartesian_path": {
            "waypoints_m",
            "max_duration_per_segment_s",
        },
        "set_gripper_opening": {"opening_fraction", "max_duration_s"},
        "approach_until_contact": {
            "precontact_position_m",
            "approach_direction_unit",
            "max_travel_m",
            "max_approach_speed_m_s",
            "max_duration_s",
        },
        "move_cartesian_offset_and_return": {
            "offset_robot_base_m",
            "max_duration_per_leg_s",
        },
        "set_wrist_roll": {"target_roll_rad", "max_duration_s"},
        "track_planar_twist": {
            "linear_velocity_body_m_s",
            "yaw_rate_rad_s",
            "duration_s",
        },
        "move_body_relative_pose": {
            "translation_initial_yaw_m",
            "yaw_delta_rad",
            "max_duration_s",
        },
        "trace_planar_path": {
            "waypoints_initial_yaw_m",
            "max_duration_s",
        },
        "set_body_height": {"target_height_m", "max_duration_s"},
        "hold_stable_stance": {"duration_s"},
    }
    for contract in (*so_adapter.contracts, *go_adapter.contracts):
        schema = contract.request_schema
        assert set(schema["properties"]) == expected_request_fields[
            contract.method_name
        ]
        assert set(schema["required"]) == expected_request_fields[
            contract.method_name
        ]
        assert schema["additionalProperties"] is False

    approach_schema = so_adapter.contracts[3].request_schema["properties"]
    assert approach_schema["max_travel_m"] == {
        "type": "number",
        "unit": "m",
        "frame": "none",
        "description": "Maximum axial approach travel.",
        "maximum": 0.08,
        "exclusiveMinimum": 0.0,
    }
    height_schema = go_adapter.contracts[3].request_schema["properties"][
        "target_height_m"
    ]
    assert height_schema["minimum"] == 0.22
    assert height_schema["maximum"] == 0.36


def test_execute_uses_method_request_request_without_task_wrapper() -> None:
    calls: list[tuple[str, dict]] = []

    def invoke(method_name: str, arguments: dict) -> dict:
        calls.append((method_name, arguments))
        return {
            "operation": {"status": "EXECUTED"},
            "public_state": {"end_effector_position_m": [0.1, 0.2, 0.3]},
        }

    adapter = CapabilityAdapter(_design("robotstudio_so101"), invoke)
    observation = adapter.execute(
        "move_end_effector_to_position",
        {"target_position_m": [0.1, 0.2, 0.3], "max_duration_s": 2.0},
    )

    assert calls == [
        (
            "move_end_effector_to_position",
            {
                "request": {
                    "target_position_m": [0.1, 0.2, 0.3],
                    "max_duration_s": 2.0,
                }
            },
        )
    ]
    assert observation["operation"]["status"] == "EXECUTED"


def test_go2_legal_native_request_uses_the_same_fixed_abi() -> None:
    calls: list[tuple[str, dict]] = []
    adapter = CapabilityAdapter(
        _design("unitree-go2-stock-12dof"),
        lambda method, arguments: calls.append((method, arguments))
        or {"operation": {"status": "EXECUTED"}},
    )

    adapter.execute(
        "track_planar_twist",
        {
            "linear_velocity_body_m_s": [0.2, 0.0],
            "yaw_rate_rad_s": 0.1,
            "duration_s": 2.5,
        },
    )

    assert calls == [
        (
            "track_planar_twist",
            {
                "request": {
                    "linear_velocity_body_m_s": [0.2, 0.0],
                    "yaw_rate_rad_s": 0.1,
                    "duration_s": 2.5,
                }
            },
        )
    ]


@pytest.mark.parametrize(
    "candidate_request",
    [
        {"target_position_m": [0.1, 0.2, 0.3]},
        {
            "target_position_m": [0.1, 0.2, 0.3],
            "max_duration_s": 2.0,
            "task_id": "legacy-wrapper-is-forbidden",
        },
        {"target_position_m": [0.1, 0.2], "max_duration_s": 2.0},
        {"target_position_m": [0.1, True, 0.3], "max_duration_s": 2.0},
        {"target_position_m": [1.1, 0.2, 0.3], "max_duration_s": 2.0},
        {"target_position_m": [0.1, 0.2, 0.3], "max_duration_s": 8.1},
        {"target_position_m": [0.1, 0.2, 0.3], "max_duration_s": float("nan")},
    ],
)
def test_invalid_native_request_never_reaches_invoker(
    candidate_request: dict,
) -> None:
    calls: list[tuple[str, dict]] = []
    adapter = CapabilityAdapter(
        _design("robotstudio_so101"),
        lambda method, arguments: calls.append((method, arguments)) or {},
    )

    with pytest.raises(CapabilityAdapterError):
        adapter.execute("move_end_effector_to_position", candidate_request)

    assert calls == []


def test_exclusive_bound_and_unknown_capability_are_rejected() -> None:
    calls: list[tuple[str, dict]] = []
    adapter = CapabilityAdapter(
        _design("robotstudio_so101"),
        lambda method, arguments: calls.append((method, arguments)) or {},
    )

    with pytest.raises(CapabilityAdapterError, match="exclusiveMinimum"):
        adapter.execute(
            "approach_until_contact",
            {
                "precontact_position_m": [0.1, 0.2, 0.3],
                "approach_direction_unit": [1.0, 0.0, 0.0],
                "max_travel_m": 0.0,
                "max_approach_speed_m_s": 0.02,
                "max_duration_s": 2.0,
            },
        )
    with pytest.raises(CapabilityAdapterError, match="unknown capability_name"):
        adapter.execute("walk_anywhere", {})

    assert calls == []


def test_adapter_rejects_a_different_invocation_abi() -> None:
    design = _design("robotstudio_so101")
    design["invocation_abi"]["method_call"] = "method(**request)"

    with pytest.raises(CapabilityAdapterError, match=r"method\(request=request\)"):
        CapabilityAdapter(design, lambda method, arguments: {})
