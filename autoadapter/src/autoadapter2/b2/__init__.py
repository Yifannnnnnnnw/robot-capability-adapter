"""Prospective B2 capability-interface-use components."""

from autoadapter2.b2.capability_adapter import (
    CapabilityAdapter,
    CapabilityAdapterError,
    CapabilityContract,
    CapabilityInvocationError,
)
from autoadapter2.b2.recap import (
    RECAP_B2_SYSTEM_PROMPT,
    RECAP_RESPONSE_SCHEMA,
    RecapBudgets,
    RecapControllerError,
    RecapControllerResult,
    RecapModelClient,
    run_recap,
)

__all__ = [
    "CapabilityAdapter",
    "CapabilityAdapterError",
    "CapabilityContract",
    "CapabilityInvocationError",
    "RECAP_B2_SYSTEM_PROMPT",
    "RECAP_RESPONSE_SCHEMA",
    "RecapBudgets",
    "RecapControllerError",
    "RecapControllerResult",
    "RecapModelClient",
    "run_recap",
]
