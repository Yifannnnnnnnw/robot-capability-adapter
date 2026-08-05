from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
from types import ModuleType
from typing import Mapping, Sequence

import pytest
import yaml

from soarm_demo.oracle import (
    MujocoTabletopValidationEnvironment,
    _advance_with_sampling,
    _cylinder_axis_tilt,
    _object_speed,
    _supported_by_table,
)


ROOT = Path(__file__).resolve().parents[1]
MORPHOLOGY = ROOT / "libraries/morphology/soarm101/v1/kinematics.yaml"
REFERENCE = ROOT / "libraries/morphology/soarm101/v1/kinematics_reference.py"
MODEL = ROOT / "libraries/morphology/soarm101/v1/model/so101.xml"
COMMON_RESET = (
    ROOT
    / "private/task_library/soarm101_tabletop/v1/initial_states/_common_reset.json"
)
SCENE_CATALOG = (
    ROOT
    / "libraries/morphology/scenes/soarm101_tabletop/v1/assets/primitive_catalog.yaml"
)
ARM_JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
)
JAW_BODIES = frozenset({"gripper", "moving_jaw_so101_v1"})
OBJECT_ID = "release_clearance_cylinder"


def _load_reference() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "soarm101_release_clearance_reference", REFERENCE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _arm_radians(observation: Mapping[str, float]) -> tuple[float, ...]:
    return tuple(
        math.radians(float(observation[f"{joint}.pos"])) for joint in ARM_JOINTS
    )


def _action(
    joints_rad: Sequence[float],
    gripper_command: float,
    *,
    gripper_key: str,
) -> dict[str, float]:
    result = {
        f"{joint}.pos": math.degrees(float(joints_rad[index]))
        for index, joint in enumerate(ARM_JOINTS)
    }
    result[gripper_key] = float(gripper_command)
    return result


def _advance_action(
    environment: MujocoTabletopValidationEnvironment,
    joints_rad: Sequence[float],
    gripper_command: float,
    duration_s: float,
    *,
    gripper_key: str,
) -> Mapping[str, float]:
    environment.runtime.send_action(
        _action(joints_rad, gripper_command, gripper_key=gripper_key)
    )
    _advance_with_sampling(
        environment._mujoco_runtime,
        duration_s,
        environment._observe_simulation_state,
    )
    return environment.runtime.get_observation()


def _effective_arm_limits_rad(
    morphology: Mapping[str, object],
) -> tuple[tuple[float, float], ...]:
    policy = morphology["command_limit_policy"]
    assert isinstance(policy, Mapping)
    margin = max(
        float(policy["conversion_roundoff_margin_rad"]),
        float(policy["validated_dynamic_margin_rad"]),
    )
    joint_records = morphology["joints"]
    assert isinstance(joint_records, Sequence)
    records_by_name = {
        str(record["name"]): record
        for record in joint_records
        if isinstance(record, Mapping)
    }
    limits: list[tuple[float, float]] = []
    for name in ARM_JOINTS:
        record = records_by_name[name]
        joint_low, joint_high = (float(value) for value in record["range_rad"])
        actuator_low, actuator_high = (
            float(value) for value in record["actuator_ctrlrange_rad"]
        )
        low = max(joint_low, actuator_low) + margin
        high = min(joint_high, actuator_high) - margin
        assert low < high
        limits.append((low, high))
    return tuple(limits)


def _solve_and_send_tool_target(
    environment: MujocoTabletopValidationEnvironment,
    reference: ModuleType,
    previous_joints_rad: Sequence[float],
    target_position_m: Sequence[float],
    tool_center_in_frame_m: Sequence[float],
    gripper_command: float,
    duration_s: float,
    *,
    gripper_key: str,
    effective_arm_limits_rad: Sequence[tuple[float, float]],
    tolerance_m: float,
    wrist_roll_rad: float,
) -> tuple[float, ...]:
    solution, error, converged = reference._solve_position_ik_rad(
        tuple(float(value) for value in target_position_m),
        initial_joints_rad=tuple(float(value) for value in previous_joints_rad),
        tolerance_m=tolerance_m,
        max_iterations_per_seed=300,
        fixed_wrist_roll_rad=wrist_roll_rad,
        tool_point_in_gripperframe_m=tuple(
            float(value) for value in tool_center_in_frame_m
        ),
    )
    assert converged is True
    assert error <= tolerance_m

    clamped = tuple(
        min(high, max(low, float(solution[index])))
        for index, (low, high) in enumerate(effective_arm_limits_rad)
    )
    requested = _action(clamped, gripper_command, gripper_key=gripper_key)
    accepted = environment.runtime.send_action(requested)
    assert set(accepted) == set(requested)
    for key, value in requested.items():
        assert accepted[key] == pytest.approx(value, abs=1e-9)

    accepted_joints = _arm_radians(accepted)
    reached = reference._forward_tool_point_rad(
        accepted_joints,
        tuple(float(value) for value in tool_center_in_frame_m),
    )
    post_conversion_error = math.sqrt(
        sum(
            (reached[axis] - float(target_position_m[axis])) ** 2
            for axis in range(3)
        )
    )
    assert post_conversion_error <= tolerance_m
    _advance_with_sampling(
        environment._mujoco_runtime,
        duration_s,
        environment._observe_simulation_state,
    )
    environment.runtime.get_observation()
    return accepted_joints


def _has_jaw_contact(snapshot: Mapping[str, object]) -> bool:
    contacts = snapshot["contacts"]
    assert isinstance(contacts, Sequence)
    return any(
        isinstance(contact, Mapping)
        and OBJECT_ID in {contact.get("body1"), contact.get("body2")}
        and bool(JAW_BODIES & {contact.get("body1"), contact.get("body2")})
        for contact in contacts
    )


def _trace_snapshots_since(
    environment: MujocoTabletopValidationEnvironment, start_index: int
) -> list[Mapping[str, object]]:
    return [
        snapshot
        for event in environment._mujoco_runtime.trace_events[start_index:]
        if isinstance(event, Mapping)
        and isinstance((snapshot := event.get("snapshot")), Mapping)
    ]


def _run_release_clearance_episode() -> tuple[tuple[float, ...], float, float]:
    morphology = yaml.safe_load(MORPHOLOGY.read_text(encoding="utf-8"))
    contact = morphology["gripper_contact_model"]
    baseline = contact["verified_tabletop_pinch_baseline"]
    release_contract = contact["tabletop_release_and_sequence_requirements"]
    release_selection = release_contract["release_aperture_selection"]
    samples = contact["aperture_samples"]

    object_extent_m = 0.030
    grasp_sample = next(
        sample
        for sample in samples
        if float(sample["aperture_m"])
        >= object_extent_m + float(baseline["minimum_total_aperture_clearance_m"])
    )
    release_sample = next(
        sample
        for sample in samples
        if float(sample["aperture_m"])
        >= object_extent_m
        + float(release_selection["minimum_total_aperture_clearance_m"])
    )
    assert float(baseline["minimum_total_aperture_clearance_m"]) == 0.002
    assert float(release_selection["minimum_total_aperture_clearance_m"]) == 0.008
    assert grasp_sample["command"] == 20.0
    assert release_sample["command"] == 24.0
    assert release_sample is not grasp_sample

    catalog = yaml.safe_load(SCENE_CATALOG.read_text(encoding="utf-8"))
    table_asset = next(
        asset for asset in catalog["assets"] if asset["runtime_kind"] == "table"
    )
    table_surface_z_m = float(table_asset["geometry_profile"]["surface_z_m"])
    cylinder_height_m = 0.040
    source_position_m = (0.30, -0.05, table_surface_z_m + cylinder_height_m / 2.0)
    placement_target_m = (0.38, 0.05, source_position_m[2])
    grasp_tool_center = tuple(
        float(value) for value in grasp_sample["tool_center_in_frame_m"]
    )
    release_tool_center = tuple(
        float(value) for value in release_sample["tool_center_in_frame_m"]
    )
    grasp_open_command = float(grasp_sample["command"])
    release_command = float(release_sample["command"])
    close_command = float(baseline["verified_close_command"]["command"])
    gripper_key = str(contact["command_key"])
    wrist_roll_rad = float(baseline["fixed_wrist_roll_rad"])
    tolerance_m = float(baseline["verified_controlled_point_ik"]["tolerance_m"])
    effective_limits = _effective_arm_limits_rad(morphology)
    reference = _load_reference()

    environment = MujocoTabletopValidationEnvironment(
        model_path=MODEL,
        common_reset=COMMON_RESET,
        scene_catalog=SCENE_CATALOG,
        require_scene_catalog=True,
        require_explicit_asset_refs=True,
        auto_step=False,
        reset_settle_s=0.15,
        measurement_settle_s=0.0,
    )
    try:
        environment.reset(
            {
                "bodies": [
                    {
                        "id": OBJECT_ID,
                        "asset_ref": (
                            "morphology.scene_asset/"
                            "cylinder_r15_h40_22g@1.0.0#orange"
                        ),
                        "position_m": list(source_position_m),
                        "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                    }
                ],
                "markers": [],
            }
        )
        initial_joints = _arm_radians(environment.runtime.get_observation())

        approach_height_m = float(baseline["approach_height_above_object_m"])
        approach_target = (
            source_position_m[0],
            source_position_m[1],
            source_position_m[2] + approach_height_m,
        )
        approach_solution, error, converged = reference._solve_position_ik_rad(
            approach_target,
            initial_joints_rad=initial_joints,
            tolerance_m=tolerance_m,
            max_iterations_per_seed=300,
            fixed_wrist_roll_rad=wrist_roll_rad,
            tool_point_in_gripperframe_m=grasp_tool_center,
        )
        assert converged is True
        assert error <= tolerance_m
        approach_waypoints = int(baseline["descent_waypoints"]) + 10
        for index in range(1, approach_waypoints + 1):
            fraction = index / approach_waypoints
            interpolated = tuple(
                initial_joints[axis]
                + fraction * (approach_solution[axis] - initial_joints[axis])
                for axis in range(5)
            )
            _advance_action(
                environment,
                interpolated,
                grasp_open_command,
                0.04,
                gripper_key=gripper_key,
            )
        current_joints = _solve_and_send_tool_target(
            environment,
            reference,
            approach_solution,
            approach_target,
            grasp_tool_center,
            grasp_open_command,
            0.20,
            gripper_key=gripper_key,
            effective_arm_limits_rad=effective_limits,
            tolerance_m=tolerance_m,
            wrist_roll_rad=wrist_roll_rad,
        )

        descent_waypoints = int(baseline["descent_waypoints"])
        for index in range(1, descent_waypoints + 1):
            fraction = index / descent_waypoints
            waypoint = (
                source_position_m[0],
                source_position_m[1],
                approach_target[2]
                + fraction * (source_position_m[2] - approach_target[2]),
            )
            current_joints = _solve_and_send_tool_target(
                environment,
                reference,
                current_joints,
                waypoint,
                grasp_tool_center,
                grasp_open_command,
                0.06,
                gripper_key=gripper_key,
                effective_arm_limits_rad=effective_limits,
                tolerance_m=tolerance_m,
                wrist_roll_rad=wrist_roll_rad,
            )

        close_waypoints = int(baseline["close_waypoints"])
        for index in range(1, close_waypoints + 1):
            fraction = index / close_waypoints
            command = grasp_open_command + fraction * (
                close_command - grasp_open_command
            )
            _advance_action(
                environment,
                current_joints,
                command,
                0.04,
                gripper_key=gripper_key,
            )
        for _ in range(max(2, close_waypoints // 5)):
            _advance_action(
                environment,
                current_joints,
                close_command,
                0.06,
                gripper_key=gripper_key,
            )

        lift_height_m = float(baseline["lift_height_m"])
        lift_waypoints = int(baseline["lift_waypoints"])
        for index in range(1, lift_waypoints + 1):
            fraction = index / lift_waypoints
            waypoint = (
                source_position_m[0],
                source_position_m[1],
                source_position_m[2] + fraction * lift_height_m,
            )
            current_joints = _solve_and_send_tool_target(
                environment,
                reference,
                current_joints,
                waypoint,
                grasp_tool_center,
                close_command,
                0.07,
                gripper_key=gripper_key,
                effective_arm_limits_rad=effective_limits,
                tolerance_m=tolerance_m,
                wrist_roll_rad=wrist_roll_rad,
            )
        _advance_action(
            environment,
            current_joints,
            close_command,
            0.40,
            gripper_key=gripper_key,
        )

        horizontal_distance = math.hypot(
            placement_target_m[0] - source_position_m[0],
            placement_target_m[1] - source_position_m[1],
        )
        transport_waypoints = max(2, math.ceil(horizontal_distance / 0.02))
        lifted_z = placement_target_m[2] + lift_height_m
        for index in range(1, transport_waypoints + 1):
            fraction = index / transport_waypoints
            waypoint = (
                source_position_m[0]
                + fraction * (placement_target_m[0] - source_position_m[0]),
                source_position_m[1]
                + fraction * (placement_target_m[1] - source_position_m[1]),
                lifted_z,
            )
            current_joints = _solve_and_send_tool_target(
                environment,
                reference,
                current_joints,
                waypoint,
                grasp_tool_center,
                close_command,
                0.07,
                gripper_key=gripper_key,
                effective_arm_limits_rad=effective_limits,
                tolerance_m=tolerance_m,
                wrist_roll_rad=wrist_roll_rad,
            )

        for index in range(1, descent_waypoints + 1):
            fraction = index / descent_waypoints
            waypoint = (
                placement_target_m[0],
                placement_target_m[1],
                lifted_z
                + fraction * (placement_target_m[2] - lifted_z),
            )
            current_joints = _solve_and_send_tool_target(
                environment,
                reference,
                current_joints,
                waypoint,
                grasp_tool_center,
                close_command,
                0.07,
                gripper_key=gripper_key,
                effective_arm_limits_rad=effective_limits,
                tolerance_m=tolerance_m,
                wrist_roll_rad=wrist_roll_rad,
            )

        _advance_action(
            environment,
            current_joints,
            release_command,
            float(release_contract["release_dwell_s"]),
            gripper_key=gripper_key,
        )
        released_snapshot = environment._mujoco_runtime.world_snapshot()
        assert _has_jaw_contact(released_snapshot) is False
        assert _object_speed(released_snapshot, OBJECT_ID) <= 0.03

        observed_joints = _arm_radians(environment.runtime.get_observation())
        release_tool_position = reference._forward_tool_point_rad(
            observed_joints, release_tool_center
        )
        retreat_height_m = float(release_contract["minimum_vertical_retreat_m"])
        retreat_waypoints = int(release_contract["retreat_waypoints"])
        current_joints = observed_joints
        for index in range(1, retreat_waypoints + 1):
            waypoint = (
                release_tool_position[0],
                release_tool_position[1],
                release_tool_position[2]
                + index * retreat_height_m / retreat_waypoints,
            )
            retreat_trace_start = len(environment._mujoco_runtime.trace_events)
            current_joints = _solve_and_send_tool_target(
                environment,
                reference,
                current_joints,
                waypoint,
                release_tool_center,
                release_command,
                0.07,
                gripper_key=gripper_key,
                effective_arm_limits_rad=effective_limits,
                tolerance_m=tolerance_m,
                wrist_roll_rad=wrist_roll_rad,
            )
            retreat_snapshots = _trace_snapshots_since(
                environment, retreat_trace_start
            )
            assert retreat_snapshots
            assert not any(_has_jaw_contact(snapshot) for snapshot in retreat_snapshots)
            if index == 1:
                assert max(
                    _object_speed(snapshot, OBJECT_ID)
                    for snapshot in retreat_snapshots
                ) <= 0.03

        _advance_action(
            environment,
            current_joints,
            release_command,
            0.50,
            gripper_key=gripper_key,
        )
        terminal = environment._mujoco_runtime.world_snapshot()
        state = terminal["objects"][OBJECT_ID]
        position = tuple(float(value) for value in state["position_m"])
        speed = _object_speed(terminal, OBJECT_ID)
        tilt = _cylinder_axis_tilt(terminal, OBJECT_ID)
        diagnostics = environment.diagnostics()
        motion = diagnostics["object_motion_summaries"][OBJECT_ID]
        forbidden = environment.forbidden(
            [
                "action_clipped",
                "object_outside_workspace",
                "object_outside_table_support_polygon",
                "non_gripper_robot_table_collision",
                "actuator_or_joint_limit_exceeded",
                "simulation_nan_or_instability",
            ]
        )

        assert motion["ever_grasped_by_opposing_jaws"] is True
        assert motion["maximum_lift_above_initial_m"] >= 0.07
        assert math.hypot(
            position[0] - placement_target_m[0],
            position[1] - placement_target_m[1],
        ) <= 0.015
        assert abs(position[2] - placement_target_m[2]) <= 0.003
        assert _supported_by_table(terminal, OBJECT_ID) is True
        assert speed <= 0.03
        assert tilt <= 0.20
        assert _has_jaw_contact(terminal) is False
        assert not any(bool(value) for value in forbidden.values())
        return position, speed, tilt
    finally:
        environment.close()


def test_independent_release_aperture_prevents_cylinder_retreat_recontact() -> None:
    pytest.importorskip("mujoco")
    repetitions = [_run_release_clearance_episode() for _ in range(3)]
    first_position, first_speed, first_tilt = repetitions[0]
    for position, speed, tilt in repetitions[1:]:
        assert position == pytest.approx(first_position, abs=1e-9)
        assert speed == pytest.approx(first_speed, abs=1e-9)
        assert tilt == pytest.approx(first_tilt, abs=1e-9)
