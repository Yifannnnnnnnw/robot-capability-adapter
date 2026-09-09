# SPDX-License-Identifier: Apache-2.0
"""auto_adapter — self-assembling robot driver agent.

Top-level surface:
    SelfAssemble, SelfAssembleConfig, SelfAssembleResult, PhaseResult
    skeletons   (ArmSpec, QuadrupedSpec, ArmSerialDLSSkeleton, ...)
    agent       (ReactLoop, ToolSpec, ReactResult)
"""
from .orchestrator import (
    PhaseResult,
    SelfAssemble,
    SelfAssembleConfig,
    SelfAssembleResult,
)

__all__ = [
    "PhaseResult",
    "SelfAssemble",
    "SelfAssembleConfig",
    "SelfAssembleResult",
]
