"""Trusted, capability-neutral skeletons owned by the mainline Framework."""

from .arm_serial_dls import ArmSerialDLSSkeleton, ArmSpec, IKUnreachableError
from .quadruped_pd_gait import QuadrupedPDGaitSkeleton, QuadrupedSpec

__all__ = [
    "ArmSerialDLSSkeleton",
    "ArmSpec",
    "IKUnreachableError",
    "QuadrupedPDGaitSkeleton",
    "QuadrupedSpec",
]
