"""Public inventory for the task-neutral retained Go2 velocity policy."""

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
)


__all__ = [
    "GO2_VELOCITY_POLICY_SPEC",
    "Go2VelocityPolicySkeleton",
    "Go2VelocityPolicySpec",
    "POLICY_ARTIFACT_ID",
    "POLICY_SOURCE_REVISION",
]
