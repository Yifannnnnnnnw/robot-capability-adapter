# SPDX-License-Identifier: Apache-2.0
"""auto_adapter skeleton library — production-tested motion templates.

Agent code generates a Spec + selects the matching Skeleton subclass.
"""
from .base import ArmSpec, QuadrupedSpec, SkeletonBase
from .arm_serial_dls import ArmSerialDLSSkeleton
from .quadruped_pd_gait import QuadrupedPDGaitSkeleton
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
    "GraspBackend",
    "WeldGraspBackend",
    "ContactGraspBackend",
    "RealContactGraspBackend",
    "NoOpGraspBackend",
    "make_grasp_backend",
]
