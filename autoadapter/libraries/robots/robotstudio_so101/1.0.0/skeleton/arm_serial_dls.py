"""Public SO-101 skeleton inventory for the Demo3 package."""

from autoadapter2.trusted_skeletons.arm_serial_dls import (
    ArmSerialDLSSkeleton,
    ArmSpec,
    IKUnreachableError,
)

__all__ = ["ArmSerialDLSSkeleton", "ArmSpec", "IKUnreachableError"]
