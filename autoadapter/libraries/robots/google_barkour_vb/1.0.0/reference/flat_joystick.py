"""Calibration-only binding for the Barkour vB flat position policy.

This wrapper supplies exact public morphology facts to the trusted primitive.
It contains no task dispatch, private case data, scene loading, reset, obstacle
route, or verdict logic.
"""

from __future__ import annotations

from typing import Any

from autoadapter2.trusted_skeletons.quadruped_position_policy import (
    BARKOUR_POLICY_JOINT_HIGH,
    BARKOUR_POLICY_JOINT_LOW,
    BarkourPositionPolicySkeleton,
    QuadrupedPositionPolicySpec,
)


_JOINT_NAMES = (
    "abduction_front_left",
    "hip_front_left",
    "knee_front_left",
    "abduction_hind_left",
    "hip_hind_left",
    "knee_hind_left",
    "abduction_front_right",
    "hip_front_right",
    "knee_front_right",
    "abduction_hind_right",
    "hip_hind_right",
    "knee_hind_right",
)


def barkour_spec() -> QuadrupedPositionPolicySpec:
    """Return the exact public Barkour vB position-policy mapping."""

    return QuadrupedPositionPolicySpec(
        base_body_name="torso",
        imu_site_name="imu_frame",
        gyro_sensor_name="gyro",
        home_keyframe_name="home",
        joint_names=_JOINT_NAMES,
        actuator_names=_JOINT_NAMES,
        policy_joint_low=BARKOUR_POLICY_JOINT_LOW,
        policy_joint_high=BARKOUR_POLICY_JOINT_HIGH,
        action_scale=0.3,
        policy_period_s=0.02,
        expected_physics_timestep_s=0.001,
        minimum_base_height_m=0.18,
        maximum_command_duration_s=20.0,
    )


def build(*, model: Any, data: Any) -> BarkourPositionPolicySkeleton:
    """Bind the calibration primitive to Framework-owned model and data."""

    return BarkourPositionPolicySkeleton.from_session(
        model=model,
        data=data,
        spec=barkour_spec(),
    )


__all__ = ["barkour_spec", "build"]
