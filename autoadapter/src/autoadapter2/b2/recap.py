"""Compatibility re-exports for the canonical Task Demo ReCAP controller."""

from autoadapter2.task_demo.recap import (
    RECAP_B2_SYSTEM_PROMPT,
    RECAP_RESPONSE_SCHEMA,
    RECAP_SYSTEM_PROMPT,
    RecapBudgets,
    RecapControllerError,
    RecapControllerResult,
    RecapModelClient,
    run_recap,
)

__all__ = [
    "RECAP_B2_SYSTEM_PROMPT",
    "RECAP_RESPONSE_SCHEMA",
    "RECAP_SYSTEM_PROMPT",
    "RecapBudgets",
    "RecapControllerError",
    "RecapControllerResult",
    "RecapModelClient",
    "run_recap",
]
