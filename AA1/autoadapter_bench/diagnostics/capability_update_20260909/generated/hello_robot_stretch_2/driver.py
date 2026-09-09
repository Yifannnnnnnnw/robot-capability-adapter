"""
driver.py – Hello Robot Stretch 2 (hello_robot_stretch_2)
Skeleton : StretchMobileManipulationSkeleton
MJCF     : /Users/wangyifan/Projects/auto_adapter2.0/AA1/assets/mjcf/hello_robot_stretch_2/scene.xml

Gripper probe result
--------------------
  joint_gripper_slide  ctrlrange = [-0.005, 0.04]
  qpos @ ctrl=-0.005  →  -0.004637  (|q| ≈ 0.005)  ← CLOSED (smallest stroke)
  qpos @ ctrl= 0.040  →   0.039849  (|q| ≈ 0.040)  ← OPEN
  ∴  gripper_close_ctrl = -0.005,  gripper_open_ctrl = 0.04
"""

import os
from auto_adapter.skeletons.stretch_mobile_manipulation import (
    StretchMobileManipulationSkeleton,
    StretchMobileManipulationSpec,
)

# Resolve the MJCF path relative to the symlink target so MuJoCo can find
# the included stretch.xml and asset files.
_MJCF_PATH = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "mjcf.xml")
)


def build() -> StretchMobileManipulationSkeleton:
    spec = StretchMobileManipulationSpec(
        # ── bodies ──────────────────────────────────────────────────────────
        base_body_name="base_link",
        tool_body_name="link_gripper_slider",

        # ── base actuators ───────────────────────────────────────────────────
        forward_actuator_name="forward",
        turn_actuator_name="turn",

        # ── arm actuators ────────────────────────────────────────────────────
        lift_actuator_name="lift",
        arm_actuator_name="arm_extend",
        wrist_actuator_name="wrist_yaw",

        # ── gripper actuator (probe: close=−0.005, open=0.04) ────────────────
        gripper_actuator_name="grip",

        # ── joint names ──────────────────────────────────────────────────────
        lift_joint_name="joint_lift",
        arm_joint_names=(
            "joint_arm_l3",
            "joint_arm_l2",
            "joint_arm_l1",
            "joint_arm_l0",
        ),
        wrist_joint_name="joint_wrist_yaw",
        gripper_joint_name="joint_gripper_slide",

        # ── physics ──────────────────────────────────────────────────────────
        expected_physics_timestep_s=0.002,

        # ── motion tuning ────────────────────────────────────────────────────
        turn_gain=5.0,
        drive_gain=8.0,
        maximum_drive_distance_m=1.0,
        maximum_control_steps=6000,
        settle_steps=75,
    )

    return StretchMobileManipulationSkeleton.from_mjcf(_MJCF_PATH, spec)
