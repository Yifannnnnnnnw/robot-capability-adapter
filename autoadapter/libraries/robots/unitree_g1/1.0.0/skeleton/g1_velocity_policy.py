"""Public inventory for the task-neutral retained G1 velocity policy."""

from autoadapter2.trusted_skeletons.g1_velocity_policy import (
    G1VelocityPolicySkeleton,
    G1VelocityPolicySpec,
    POLICY_ARTIFACT_ID,
    POLICY_SOURCE_REVISION,
)


G1_POLICY_JOINT_NAMES = (
    "left_hip_pitch_joint",
    "left_hip_roll_joint",
    "left_hip_yaw_joint",
    "left_knee_joint",
    "left_ankle_pitch_joint",
    "left_ankle_roll_joint",
    "right_hip_pitch_joint",
    "right_hip_roll_joint",
    "right_hip_yaw_joint",
    "right_knee_joint",
    "right_ankle_pitch_joint",
    "right_ankle_roll_joint",
    "waist_yaw_joint",
    "waist_roll_joint",
    "waist_pitch_joint",
    "left_shoulder_pitch_joint",
    "left_shoulder_roll_joint",
    "left_shoulder_yaw_joint",
    "left_elbow_joint",
    "left_wrist_roll_joint",
    "left_wrist_pitch_joint",
    "left_wrist_yaw_joint",
    "right_shoulder_pitch_joint",
    "right_shoulder_roll_joint",
    "right_shoulder_yaw_joint",
    "right_elbow_joint",
    "right_wrist_roll_joint",
    "right_wrist_pitch_joint",
    "right_wrist_yaw_joint",
)

G1_VELOCITY_POLICY_SPEC = G1VelocityPolicySpec(
    base_body_name="pelvis",
    gyro_sensor_name="imu_ang_vel",
    joint_names=G1_POLICY_JOINT_NAMES,
    actuator_names=G1_POLICY_JOINT_NAMES,
    default_joint_positions=(
        -0.1, 0.0, 0.0, 0.3, -0.2, 0.0,
        -0.1, 0.0, 0.0, 0.3, -0.2, 0.0,
        0.0, 0.0, 0.0,
        0.35, 0.18, 0.0, 0.87, 0.0, 0.0, 0.0,
        0.35, -0.18, 0.0, 0.87, 0.0, 0.0, 0.0,
    ),
    action_scales=(
        0.55, 0.35, 0.55, 0.35, 0.44, 0.44,
        0.55, 0.35, 0.55, 0.35, 0.44, 0.44,
        0.55, 0.44, 0.44,
        0.44, 0.44, 0.44, 0.44, 0.44, 0.07, 0.07,
        0.44, 0.44, 0.44, 0.44, 0.44, 0.07, 0.07,
    ),
    stiffness=(
        40.2, 99.1, 40.2, 99.1, 28.5, 28.5,
        40.2, 99.1, 40.2, 99.1, 28.5, 28.5,
        40.2, 28.5, 28.5,
        14.3, 14.3, 14.3, 14.3, 14.3, 16.8, 16.8,
        14.3, 14.3, 14.3, 14.3, 14.3, 16.8, 16.8,
    ),
    damping=(
        2.6, 6.3, 2.6, 6.3, 1.8, 1.8,
        2.6, 6.3, 2.6, 6.3, 1.8, 1.8,
        2.6, 1.8, 1.8,
        0.9, 0.9, 0.9, 0.9, 0.9, 1.1, 1.1,
        0.9, 0.9, 0.9, 0.9, 0.9, 1.1, 1.1,
    ),
    effort_limits=(
        88.0, 139.0, 88.0, 139.0, 50.0, 50.0,
        88.0, 139.0, 88.0, 139.0, 50.0, 50.0,
        88.0, 50.0, 50.0,
        25.0, 25.0, 25.0, 25.0, 25.0, 5.0, 5.0,
        25.0, 25.0, 25.0, 25.0, 25.0, 5.0, 5.0,
    ),
)


__all__ = [
    "G1_POLICY_JOINT_NAMES",
    "G1_VELOCITY_POLICY_SPEC",
    "G1VelocityPolicySkeleton",
    "G1VelocityPolicySpec",
    "POLICY_ARTIFACT_ID",
    "POLICY_SOURCE_REVISION",
]
