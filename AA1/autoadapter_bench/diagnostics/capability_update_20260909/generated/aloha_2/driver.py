"""
driver.py – ALOHA 2 bimanual driver.

Robot:   ALOHA 2 (two VX300s 6-DOF arms, left + right)
Skeleton: BimanualSerialDLSSkeleton
Spec:    BimanualSerialDLSSpec  (left ArmSpec + right ArmSpec)

Gripper probe results (from neutral_pose starting position):
  - ctrl = 0.002 (min) → qpos ≈ 0.0078  → CLOSED  (fingers near centre of rail)
  - ctrl = 0.037 (max) → qpos ≈ 0.0370  → OPEN    (fingers spread to outer edge)
  => gripper_close_ctrl = 0.002, gripper_open_ctrl = 0.037

grasp_backend = "contact"  (parallel-jaw physical gripper; equality constraints
                             only couple left_finger ↔ right_finger, no weld to
                             any graspable body)
"""

from auto_adapter.skeletons.bimanual_serial_dls import (
    BimanualSerialDLSSkeleton,
    BimanualSerialDLSSpec,
)
from auto_adapter.skeletons.base import ArmSpec

# ── shared joint limits (identical for both arms) ──────────────────────────
_ARM_JOINT_LIMITS = {
    "waist":        (-3.14158,  3.14158),
    "shoulder":     (-1.85005,  1.25664),
    "elbow":        (-1.76278,  1.60570),
    "forearm_roll": (-3.14158,  3.14158),
    "wrist_angle":  (-1.86750,  2.23402),
    "wrist_rotate": (-3.14158,  3.14158),
}

# home_qpos derived from the collision-free "neutral_pose" keyframe
_LEFT_HOME_QPOS  = [0.0, -0.96, 1.16, 0.0, -0.3, 0.0]
_RIGHT_HOME_QPOS = [0.0, -0.96, 1.16, 0.0, -0.3, 0.0]


def build() -> BimanualSerialDLSSkeleton:
    """Construct and return the ALOHA 2 BimanualSerialDLSSkeleton."""

    left_spec = ArmSpec(
        # End-effector: site defined inside left/gripper_link body
        ee_site_name="left/gripper",

        # 6-DOF serial arm joints (excludes finger slide joints)
        arm_joint_names=[
            "left/waist",
            "left/shoulder",
            "left/elbow",
            "left/forearm_roll",
            "left/wrist_angle",
            "left/wrist_rotate",
        ],
        arm_actuator_names=[
            "left/waist",
            "left/shoulder",
            "left/elbow",
            "left/forearm_roll",
            "left/wrist_angle",
            "left/wrist_rotate",
        ],
        joint_limits={
            "left/waist":        (-3.14158,  3.14158),
            "left/shoulder":     (-1.85005,  1.25664),
            "left/elbow":        (-1.76278,  1.60570),
            "left/forearm_roll": (-3.14158,  3.14158),
            "left/wrist_angle":  (-1.86750,  2.23402),
            "left/wrist_rotate": (-3.14158,  3.14158),
        },
        home_qpos=_LEFT_HOME_QPOS,

        # DLS IK parameters
        ik_damping=0.001,
        ik_max_iter=30,
        ik_tolerance=0.001,
        ik_step_clamp=0.2,
        ik_raise_on_unreachable=True,

        # Gripper: single actuator drives left_finger; right_finger is coupled
        # via equality constraint (joint1=left_finger, joint2=right_finger)
        gripper_actuator_names=["left/gripper"],
        gripper_close_ctrl=0.002,   # ctrl=min → qpos≈0.008 (CLOSED)
        gripper_open_ctrl=0.037,    # ctrl=max → qpos≈0.037 (OPEN)
        gripper_settle_steps=30,

        # Physical parallel-jaw gripper; no weld equality to graspable bodies
        grasp_backend="contact",
    )

    right_spec = ArmSpec(
        # End-effector: site defined inside right/gripper_link body
        ee_site_name="right/gripper",

        # 6-DOF serial arm joints
        arm_joint_names=[
            "right/waist",
            "right/shoulder",
            "right/elbow",
            "right/forearm_roll",
            "right/wrist_angle",
            "right/wrist_rotate",
        ],
        arm_actuator_names=[
            "right/waist",
            "right/shoulder",
            "right/elbow",
            "right/forearm_roll",
            "right/wrist_angle",
            "right/wrist_rotate",
        ],
        joint_limits={
            "right/waist":        (-3.14158,  3.14158),
            "right/shoulder":     (-1.85005,  1.25664),
            "right/elbow":        (-1.76278,  1.60570),
            "right/forearm_roll": (-3.14158,  3.14158),
            "right/wrist_angle":  (-1.86750,  2.23402),
            "right/wrist_rotate": (-3.14158,  3.14158),
        },
        home_qpos=_RIGHT_HOME_QPOS,

        # DLS IK parameters
        ik_damping=0.001,
        ik_max_iter=30,
        ik_tolerance=0.001,
        ik_step_clamp=0.2,
        ik_raise_on_unreachable=True,

        # Gripper
        gripper_actuator_names=["right/gripper"],
        gripper_close_ctrl=0.002,   # ctrl=min → CLOSED
        gripper_open_ctrl=0.037,    # ctrl=max → OPEN
        gripper_settle_steps=30,

        grasp_backend="contact",
    )

    spec = BimanualSerialDLSSpec(
        left=left_spec,
        right=right_spec,
        initial_keyframe="neutral_pose",   # collision-free starting pose
    )

    mjcf_path = "mjcf.xml"
    return BimanualSerialDLSSkeleton.from_mjcf(mjcf_path, spec)
