"""Public inventory for the trusted Stretch 2 feedback skeleton."""

from autoadapter2.trusted_skeletons.stretch_mobile_manipulation import (
    StretchMobileManipulationSkeleton,
    StretchMobileManipulationSpec,
    StretchUnreachableError,
)


STRETCH_MOBILE_MANIPULATION_SPEC = StretchMobileManipulationSpec(
    base_body_name="base_link",
    tool_body_name="link_gripper_slider",
    forward_actuator_name="forward",
    turn_actuator_name="turn",
    lift_actuator_name="lift",
    arm_actuator_name="arm_extend",
    gripper_actuator_name="grip",
    lift_joint_name="joint_lift",
    arm_joint_names=(
        "joint_arm_l3",
        "joint_arm_l2",
        "joint_arm_l1",
        "joint_arm_l0",
    ),
    gripper_joint_name="joint_gripper_slide",
)


__all__ = [
    "STRETCH_MOBILE_MANIPULATION_SPEC",
    "StretchMobileManipulationSkeleton",
    "StretchMobileManipulationSpec",
    "StretchUnreachableError",
]
