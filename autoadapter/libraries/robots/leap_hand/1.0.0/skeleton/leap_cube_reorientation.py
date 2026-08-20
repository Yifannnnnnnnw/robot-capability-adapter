"""Public inventory for the retained LEAP cube-orientation policy."""

from autoadapter2.trusted_skeletons.leap_cube_reorientation import (
    LeapCubeReorientationSkeleton,
    LeapCubeReorientationSpec,
    POLICY_ARTIFACT_ID,
    POLICY_SOURCE_REVISION,
)


LEAP_POLICY_JOINT_NAMES = (
    "if_mcp",
    "if_rot",
    "if_pip",
    "if_dip",
    "mf_mcp",
    "mf_rot",
    "mf_pip",
    "mf_dip",
    "rf_mcp",
    "rf_rot",
    "rf_pip",
    "rf_dip",
    "th_cmc",
    "th_axl",
    "th_mcp",
    "th_ipl",
)

LEAP_CUBE_REORIENTATION_SPEC = LeapCubeReorientationSpec(
    joint_names=LEAP_POLICY_JOINT_NAMES,
    actuator_names=tuple(f"{name}_act" for name in LEAP_POLICY_JOINT_NAMES),
    palm_position_sensor_name="palm_position",
    cube_position_sensor_name="cube_position",
    cube_orientation_sensor_name="cube_orientation",
    goal_orientation_sensor_name="cube_goal_orientation",
)


__all__ = [
    "LEAP_CUBE_REORIENTATION_SPEC",
    "LEAP_POLICY_JOINT_NAMES",
    "LeapCubeReorientationSkeleton",
    "LeapCubeReorientationSpec",
    "POLICY_ARTIFACT_ID",
    "POLICY_SOURCE_REVISION",
]
