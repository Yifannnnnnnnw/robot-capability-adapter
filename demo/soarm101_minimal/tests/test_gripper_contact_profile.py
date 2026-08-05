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
)


ROOT = Path(__file__).resolve().parents[1]
MORPHOLOGY = ROOT / "libraries/morphology/soarm101/v1/kinematics.yaml"
REFERENCE = ROOT / "libraries/morphology/soarm101/v1/kinematics_reference.py"
MODEL = ROOT / "libraries/morphology/soarm101/v1/model/so101.xml"
COMMON_RESET = ROOT / "private/task_library/soarm101_tabletop/v1/initial_states/_common_reset.json"
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


def _load_reference() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "soarm101_gripper_contact_regression_reference", REFERENCE
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


def _send_and_framework_advance(
    environment: MujocoTabletopValidationEnvironment,
    joints_rad: Sequence[float],
    gripper_command: float,
    duration_s: float,
    *,
    gripper_key: str,
) -> Mapping[str, float]:
    """Keep control public while the trusted test framework owns simulation time."""

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


def _send_checked_tool_target(
    environment: MujocoTabletopValidationEnvironment,
    reference: ModuleType,
    joints_rad: Sequence[float],
    target_position_m: Sequence[float],
    tool_center_in_frame_m: Sequence[float],
    gripper_command: float,
    duration_s: float,
    *,
    gripper_key: str,
    effective_arm_limits_rad: Sequence[tuple[float, float]],
    tolerance_m: float,
) -> tuple[tuple[float, ...], float]:
    """Exercise clamp -> SDK conversion -> runtime acceptance -> exact FK."""

    clamped_joints = tuple(
        min(high, max(low, float(joints_rad[index])))
        for index, (low, high) in enumerate(effective_arm_limits_rad)
    )
    requested = _action(
        clamped_joints,
        gripper_command,
        gripper_key=gripper_key,
    )
    accepted = environment.runtime.send_action(requested)
    assert set(accepted) == set(requested)
    for key, value in requested.items():
        assert accepted[key] == pytest.approx(value, abs=1e-9)

    # The bridge accepts arm actions in SDK degrees and converts them back to
    # model radians.  Reconstruct exactly those accepted radians, rather than
    # checking the unconstrained IK result returned by the reference solver.
    accepted_joints = _arm_radians(accepted)
    for value, (low, high) in zip(
        accepted_joints, effective_arm_limits_rad, strict=True
    ):
        assert low - 1e-12 <= value <= high + 1e-12
    reached = reference._forward_tool_point_rad(
        accepted_joints,
        tuple(float(value) for value in tool_center_in_frame_m),
    )
    error = math.sqrt(
        sum(
            (reached[axis] - float(target_position_m[axis])) ** 2
            for axis in range(3)
        )
    )
    assert error <= tolerance_m

    _advance_with_sampling(
        environment._mujoco_runtime,
        duration_s,
        environment._observe_simulation_state,
    )
    environment.runtime.get_observation()
    return accepted_joints, error


def test_command_20_profile_places_releases_and_retreats_with_post_conversion_fk_bound() -> None:
    pytest.importorskip("mujoco")
    morphology = yaml.safe_load(MORPHOLOGY.read_text(encoding="utf-8"))
    contact = morphology["gripper_contact_model"]
    baseline = contact["verified_tabletop_pinch_baseline"]
    cube_edge_m = 0.030
    minimum_aperture_m = (
        cube_edge_m + baseline["minimum_total_aperture_clearance_m"]
    )
    open_sample = next(
        sample
        for sample in contact["aperture_samples"]
        if sample["aperture_m"] >= minimum_aperture_m
    )
    assert open_sample["command"] == 20.0

    reference = _load_reference()
    catalog = yaml.safe_load(SCENE_CATALOG.read_text(encoding="utf-8"))
    table_asset = next(
        asset for asset in catalog["assets"] if asset["runtime_kind"] == "table"
    )
    table_surface_z_m = float(table_asset["geometry_profile"]["surface_z_m"])
    cube_position_m = (0.32, 0.04, table_surface_z_m + cube_edge_m / 2.0)
    tool_center = tuple(float(value) for value in open_sample["tool_center_in_frame_m"])
    wrist_roll = float(baseline["fixed_wrist_roll_rad"])
    approach_height_m = float(baseline["approach_height_above_object_m"])
    lift_height_m = float(baseline["lift_height_m"])
    descent_waypoints = int(baseline["descent_waypoints"])
    close_waypoints = int(baseline["close_waypoints"])
    lift_waypoints = int(baseline["lift_waypoints"])
    controlled_point_contract = baseline["verified_controlled_point_ik"]
    controlled_point_tolerance_m = float(controlled_point_contract["tolerance_m"])
    assert controlled_point_tolerance_m == 0.0006
    assert float(controlled_point_contract["post_clamp_fk_tolerance_m"]) == 0.0006
    effective_arm_limits = _effective_arm_limits_rad(morphology)
    release_contract = contact["tabletop_release_and_sequence_requirements"]
    release_dwell_s = float(release_contract["release_dwell_s"])
    retreat_height_m = float(release_contract["minimum_vertical_retreat_m"])
    retreat_waypoints = int(release_contract["retreat_waypoints"])
    open_command = float(open_sample["command"])
    close_contract = baseline["verified_close_command"]
    close_command = float(close_contract["command"])
    assert close_contract["status"] == "verified"
    assert close_command > float(
        close_contract["minimum_normalized_interior_command"]
    )
    gripper_key = str(contact["command_key"])

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
                        "id": "regression_cube",
                        "asset_ref": (
                            "morphology.scene_asset/cube_30mm_20g@1.0.0#red"
                        ),
                        "position_m": list(cube_position_m),
                        "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                    }
                ],
                "markers": [],
            }
        )
        environment.measure(
            {"object_positions_m": {"regression_cube": [0.0, 0.0, 0.0]}}
        )
        initial_joints = _arm_radians(environment.runtime.get_observation())

        grasp_target = cube_position_m
        approach_target = (
            grasp_target[0],
            grasp_target[1],
            grasp_target[2] + approach_height_m,
        )
        approach_joints, approach_error, approach_converged = (
            reference._solve_position_ik_rad(
                approach_target,
                initial_joints_rad=initial_joints,
                tolerance_m=controlled_point_tolerance_m,
                max_iterations_per_seed=300,
                fixed_wrist_roll_rad=wrist_roll,
                tool_point_in_gripperframe_m=tool_center,
            )
        )
        assert approach_converged is True
        assert approach_error <= controlled_point_tolerance_m
        post_conversion_fk_errors: list[float] = []

        # Reach the high approach pose without commanding an instantaneous jump.
        approach_waypoints = descent_waypoints + 10
        for index in range(1, approach_waypoints + 1):
            fraction = index / approach_waypoints
            joints = tuple(
                initial_joints[axis]
                + fraction * (approach_joints[axis] - initial_joints[axis])
                for axis in range(5)
            )
            _send_and_framework_advance(
                environment,
                joints,
                open_command,
                0.04,
                gripper_key=gripper_key,
            )
        approach_joints, checked_error = _send_checked_tool_target(
            environment,
            reference,
            approach_joints,
            approach_target,
            tool_center,
            open_command,
            0.20,
            gripper_key=gripper_key,
            effective_arm_limits_rad=effective_arm_limits,
            tolerance_m=controlled_point_tolerance_m,
        )
        post_conversion_fk_errors.append(checked_error)

        # Follow a vertical Cartesian descent using only the exact morphology IK.
        previous_joints = approach_joints
        grasp_joints = approach_joints
        for index in range(1, descent_waypoints + 1):
            fraction = index / descent_waypoints
            waypoint = (
                approach_target[0],
                approach_target[1],
                approach_target[2]
                + fraction * (grasp_target[2] - approach_target[2]),
            )
            grasp_joints, error, converged = reference._solve_position_ik_rad(
                waypoint,
                initial_joints_rad=previous_joints,
                tolerance_m=controlled_point_tolerance_m,
                max_iterations_per_seed=300,
                fixed_wrist_roll_rad=wrist_roll,
                tool_point_in_gripperframe_m=tool_center,
            )
            assert converged is True
            assert error <= controlled_point_tolerance_m
            grasp_joints, checked_error = _send_checked_tool_target(
                environment,
                reference,
                grasp_joints,
                waypoint,
                tool_center,
                open_command,
                0.06,
                gripper_key=gripper_key,
                effective_arm_limits_rad=effective_arm_limits,
                tolerance_m=controlled_point_tolerance_m,
            )
            post_conversion_fk_errors.append(checked_error)
            previous_joints = grasp_joints
        _send_and_framework_advance(
            environment,
            grasp_joints,
            open_command,
            0.20,
            gripper_key=gripper_key,
        )

        # Close gradually. The public observation is sampled at every step, but
        # physical contact is intentionally judged only by the trusted framework.
        for index in range(1, close_waypoints + 1):
            fraction = index / close_waypoints
            gripper_command = (
                open_command * (1.0 - fraction) + close_command * fraction
            )
            _send_and_framework_advance(
                environment,
                grasp_joints,
                gripper_command,
                0.04,
                gripper_key=gripper_key,
            )
        for _ in range(max(2, close_waypoints // 5)):
            _send_and_framework_advance(
                environment,
                grasp_joints,
                close_command,
                0.06,
                gripper_key=gripper_key,
            )

        # Lift the selected-open tool center; contact determines the actual jaw gap.
        previous_joints = grasp_joints
        lift_joints = grasp_joints
        for index in range(1, lift_waypoints + 1):
            fraction = index / lift_waypoints
            waypoint = (
                grasp_target[0],
                grasp_target[1],
                grasp_target[2] + fraction * lift_height_m,
            )
            lift_joints, error, converged = reference._solve_position_ik_rad(
                waypoint,
                initial_joints_rad=previous_joints,
                tolerance_m=controlled_point_tolerance_m,
                max_iterations_per_seed=300,
                fixed_wrist_roll_rad=wrist_roll,
                tool_point_in_gripperframe_m=tool_center,
            )
            assert converged is True
            assert error <= controlled_point_tolerance_m
            lift_joints, checked_error = _send_checked_tool_target(
                environment,
                reference,
                lift_joints,
                waypoint,
                tool_center,
                close_command,
                0.07,
                gripper_key=gripper_key,
                effective_arm_limits_rad=effective_arm_limits,
                tolerance_m=controlled_point_tolerance_m,
            )
            post_conversion_fk_errors.append(checked_error)
            previous_joints = lift_joints
        _send_and_framework_advance(
            environment,
            lift_joints,
            close_command,
            0.40,
            gripper_key=gripper_key,
        )

        # Move the grasped object to a distinct supported target, then lower
        # the same selected-open controlled point to the object-center height.
        placement_target = (0.38, -0.04, grasp_target[2])
        lifted_target = (
            placement_target[0],
            placement_target[1],
            placement_target[2] + lift_height_m,
        )
        horizontal_distance = math.hypot(
            placement_target[0] - grasp_target[0],
            placement_target[1] - grasp_target[1],
        )
        transport_waypoints = max(2, math.ceil(horizontal_distance / 0.02))
        previous_joints = lift_joints
        transport_joints = lift_joints
        for index in range(1, transport_waypoints + 1):
            fraction = index / transport_waypoints
            waypoint = (
                grasp_target[0]
                + fraction * (placement_target[0] - grasp_target[0]),
                grasp_target[1]
                + fraction * (placement_target[1] - grasp_target[1]),
                lifted_target[2],
            )
            transport_joints, error, converged = reference._solve_position_ik_rad(
                waypoint,
                initial_joints_rad=previous_joints,
                tolerance_m=controlled_point_tolerance_m,
                max_iterations_per_seed=300,
                fixed_wrist_roll_rad=wrist_roll,
                tool_point_in_gripperframe_m=tool_center,
            )
            assert converged is True
            assert error <= controlled_point_tolerance_m
            transport_joints, checked_error = _send_checked_tool_target(
                environment,
                reference,
                transport_joints,
                waypoint,
                tool_center,
                close_command,
                0.07,
                gripper_key=gripper_key,
                effective_arm_limits_rad=effective_arm_limits,
                tolerance_m=controlled_point_tolerance_m,
            )
            post_conversion_fk_errors.append(checked_error)
            previous_joints = transport_joints

        previous_joints = transport_joints
        lower_joints = transport_joints
        lower_checked_errors: list[float] = []
        for index in range(1, descent_waypoints + 1):
            fraction = index / descent_waypoints
            waypoint = (
                placement_target[0],
                placement_target[1],
                lifted_target[2]
                + fraction * (placement_target[2] - lifted_target[2]),
            )
            lower_joints, error, converged = reference._solve_position_ik_rad(
                waypoint,
                initial_joints_rad=previous_joints,
                tolerance_m=controlled_point_tolerance_m,
                max_iterations_per_seed=300,
                fixed_wrist_roll_rad=wrist_roll,
                tool_point_in_gripperframe_m=tool_center,
            )
            assert converged is True
            assert error <= controlled_point_tolerance_m
            lower_joints, checked_error = _send_checked_tool_target(
                environment,
                reference,
                lower_joints,
                waypoint,
                tool_center,
                close_command,
                0.07,
                gripper_key=gripper_key,
                effective_arm_limits_rad=effective_arm_limits,
                tolerance_m=controlled_point_tolerance_m,
            )
            post_conversion_fk_errors.append(checked_error)
            lower_checked_errors.append(checked_error)
            previous_joints = lower_joints
        assert lower_checked_errors
        assert max(lower_checked_errors) <= controlled_point_tolerance_m

        # Release only at the supported lower target, hold the selected open
        # command for the morphology dwell, retreat vertically, and settle.
        _send_and_framework_advance(
            environment,
            lower_joints,
            open_command,
            release_dwell_s,
            gripper_key=gripper_key,
        )
        previous_joints = lower_joints
        retreat_joints = lower_joints
        for index in range(1, retreat_waypoints + 1):
            fraction = index / retreat_waypoints
            waypoint = (
                placement_target[0],
                placement_target[1],
                placement_target[2] + fraction * retreat_height_m,
            )
            retreat_joints, error, converged = reference._solve_position_ik_rad(
                waypoint,
                initial_joints_rad=previous_joints,
                tolerance_m=controlled_point_tolerance_m,
                max_iterations_per_seed=300,
                fixed_wrist_roll_rad=wrist_roll,
                tool_point_in_gripperframe_m=tool_center,
            )
            assert converged is True
            assert error <= controlled_point_tolerance_m
            retreat_joints, checked_error = _send_checked_tool_target(
                environment,
                reference,
                retreat_joints,
                waypoint,
                tool_center,
                open_command,
                0.07,
                gripper_key=gripper_key,
                effective_arm_limits_rad=effective_arm_limits,
                tolerance_m=controlled_point_tolerance_m,
            )
            post_conversion_fk_errors.append(checked_error)
            previous_joints = retreat_joints
        _send_and_framework_advance(
            environment,
            retreat_joints,
            open_command,
            0.50,
            gripper_key=gripper_key,
        )

        terminal_measurement = environment.measure({"contacts": []})
        diagnostics = environment.diagnostics()
        terminal_pairs = {
            frozenset(pair) for pair in diagnostics["terminal_contact_pairs"]
        }
        ever_pairs = {
            frozenset(pair) for pair in diagnostics["contact_pairs_ever"]
        }
        terminal_object = diagnostics["final_object_positions_m"]["regression_cube"]
        terminal_velocity = diagnostics["final_object_linear_velocities_m_s"][
            "regression_cube"
        ]
        motion = diagnostics["object_motion_summaries"]["regression_cube"]
        tabletop_contacts = [
            item
            for item in terminal_measurement["contacts"]
            if "regression_cube" in {item["body1"], item["body2"]}
            and "tabletop_table" in {item["geom1"], item["geom2"]}
        ]
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

        assert motion["maximum_lift_above_initial_m"] >= 0.07
        assert motion["ever_grasped_by_opposing_jaws"] is True
        assert math.hypot(
            float(terminal_object[0]) - placement_target[0],
            float(terminal_object[1]) - placement_target[1],
        ) <= 0.015
        assert abs(float(terminal_object[2]) - placement_target[2]) <= 0.003
        assert math.sqrt(sum(float(value) ** 2 for value in terminal_velocity)) <= 0.03
        assert tabletop_contacts
        assert frozenset({"regression_cube", "world"}) in terminal_pairs
        assert frozenset({"regression_cube", "gripper"}) not in terminal_pairs
        assert frozenset({"regression_cube", "moving_jaw_so101_v1"}) not in terminal_pairs
        assert frozenset({"regression_cube", "gripper"}) in ever_pairs
        assert (
            frozenset({"regression_cube", "moving_jaw_so101_v1"})
            in ever_pairs
        )
        assert post_conversion_fk_errors
        assert max(post_conversion_fk_errors) <= controlled_point_tolerance_m
        assert diagnostics["clipped_action_count"] == 0
        assert not any(forbidden.values())
    finally:
        environment.close()
