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
from autoadapter2.b2.public_observation import (
    PUBLIC_STATE_PROFILE_REVISION,
    PublicObservationError,
    project_public_state,
)
from autoadapter2.b2.worker_protocol import (
    B2WorkerProtocolError,
    abort_observation,
    initial_public_state,
    public_observation,
)

__all__ = [
    "CapabilityAdapter",
    "CapabilityAdapterError",
    "CapabilityContract",
    "CapabilityInvocationError",
    "B2WorkerProtocolError",
    "PUBLIC_STATE_PROFILE_REVISION",
    "RECAP_B2_SYSTEM_PROMPT",
    "RECAP_RESPONSE_SCHEMA",
    "RecapBudgets",
    "RecapControllerError",
    "RecapControllerResult",
    "RecapModelClient",
    "PublicObservationError",
    "abort_observation",
    "initial_public_state",
    "project_public_state",
    "public_observation",
    "run_recap",
]
