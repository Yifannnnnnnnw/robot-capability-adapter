"""Public serial-arm skeleton inventory for Kinova Gen3 with Robotiq 2F-85."""

from autoadapter2.trusted_skeletons.arm_serial_dls import (
    ArmSerialDLSSkeleton,
    ArmSpec,
    IKUnreachableError,
)

__all__ = ["ArmSerialDLSSkeleton", "ArmSpec", "IKUnreachableError"]
