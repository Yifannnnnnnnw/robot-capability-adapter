# SPDX-License-Identifier: Apache-2.0
"""auto_adapter skeleton library — production-tested motion templates.

Agent code generates a Spec + selects the matching Skeleton subclass.
"""
from .base import ArmSpec, QuadrupedSpec, SkeletonBase
from .arm_serial_dls import ArmSerialDLSSkeleton
from .quadruped_pd_gait import QuadrupedPDGaitSkeleton
from .go2_velocity_policy import (
    Go2VelocityPolicySkeleton,
    Go2VelocityPolicySpec,
)
from .hand_fingertip_dls import HandFingertipDLSSpec, HandFingertipDLSSkeleton
from .stretch_mobile_manipulation import StretchMobileManipulationSpec, StretchMobileManipulationSkeleton
from .bimanual_serial_dls import BimanualSerialDLSSpec, BimanualSerialDLSSkeleton
from .grasp_backends import (
    ContactGraspBackend,
    GraspBackend,
    NoOpGraspBackend,
    RealContactGraspBackend,
    WeldGraspBackend,
    make_grasp_backend,
)

__all__ = [
    "ArmSpec",
    "QuadrupedSpec",
    "SkeletonBase",
    "ArmSerialDLSSkeleton",
    "QuadrupedPDGaitSkeleton",
    "Go2VelocityPolicySkeleton",
    "Go2VelocityPolicySpec",
    "HandFingertipDLSSpec",
    "HandFingertipDLSSkeleton",
    "StretchMobileManipulationSpec",
    "StretchMobileManipulationSkeleton",
    "BimanualSerialDLSSpec",
    "BimanualSerialDLSSkeleton",
    "GraspBackend",
    "WeldGraspBackend",
    "ContactGraspBackend",
    "RealContactGraspBackend",
    "NoOpGraspBackend",
    "make_grasp_backend",
]
