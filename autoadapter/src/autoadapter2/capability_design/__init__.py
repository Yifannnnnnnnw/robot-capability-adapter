"""Task-Grounded Capability Design interfaces."""

from .tgcd import (
    CapabilityDesignError,
    TGCD_SYSTEM_PROMPT,
    run_tgcd,
    validate_capability_design,
    write_capability_design,
)

__all__ = [
    "CapabilityDesignError",
    "TGCD_SYSTEM_PROMPT",
    "run_tgcd",
    "validate_capability_design",
    "write_capability_design",
]
