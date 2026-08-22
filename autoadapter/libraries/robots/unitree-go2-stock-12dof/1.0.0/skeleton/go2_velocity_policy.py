"""Public inventory for the task-neutral retained Go2 velocity policy.

Use ``track_planar_velocity`` when the requested velocity must be measured and
corrected. ``command_planar_velocity`` remains the raw feed-forward policy call.
"""

from autoadapter2.trusted_skeletons.go2_velocity_policy import (
    Go2VelocityPolicySkeleton,
    Go2VelocityPolicySpec,
    POLICY_ARTIFACT_ID,
    POLICY_SOURCE_REVISION,
)


GO2_VELOCITY_POLICY_SPEC = Go2VelocityPolicySpec(
    base_body_name="base_link",
    joint_names=tuple(
        f"{leg}_{suffix}_joint"
        for leg in ("FL", "FR", "RL", "RR")
        for suffix in ("hip", "thigh", "calf")
    ),
    actuator_names=tuple(
        f"{leg}_{suffix}"
        for leg in ("FL", "FR", "RL", "RR")
        for suffix in ("hip", "thigh", "calf")
    ),
    default_joint_angles=(
        0.1,
        0.8,
        -1.5,
        -0.1,
        0.8,
        -1.5,
        0.1,
        1.0,
        -1.5,
        -0.1,
        1.0,
        -1.5,
    ),
    feedback_period_s=0.04,
    planar_feedback_kp=0.4,
    planar_feedback_ki=0.3,
    yaw_feedback_kp=0.4,
    yaw_feedback_ki=0.3,
    maximum_planar_correction_m_s=0.4,
    maximum_yaw_correction_rad_s=0.8,
)


__all__ = [
    "GO2_VELOCITY_POLICY_SPEC",
    "Go2VelocityPolicySkeleton",
    "Go2VelocityPolicySpec",
    "POLICY_ARTIFACT_ID",
    "POLICY_SOURCE_REVISION",
]
