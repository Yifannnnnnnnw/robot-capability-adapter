"""Experiment-grade Validation A/B and bounded implementation Repair."""

from .repair import RepairCallback, RepairConfig, RepairResult, RepairRunner
from .validation_a import (
    BindingOverlay,
    ValidatedCandidateHandle,
    ValidationAProfile,
    ValidationAResult,
    ValidationARunner,
    bind_candidate_to_suite,
    create_execution_binding_overlay,
)
from .validation_b import (
    FrozenValidationContext,
    HarnessInfrastructureError,
    HarnessCriterionMeasurement,
    HarnessInvocation,
    HarnessMeasurement,
    MeasurementSample,
    TypedHarness,
    TypedHarnessSession,
    ValidationBResult,
    ValidationBRunner,
    ValidationContext,
    freeze_validation_context,
)

__all__ = [
    "BindingOverlay",
    "FrozenValidationContext",
    "HarnessInfrastructureError",
    "HarnessCriterionMeasurement",
    "HarnessInvocation",
    "HarnessMeasurement",
    "MeasurementSample",
    "RepairCallback",
    "RepairConfig",
    "RepairResult",
    "RepairRunner",
    "TypedHarness",
    "TypedHarnessSession",
    "ValidatedCandidateHandle",
    "ValidationAProfile",
    "ValidationAResult",
    "ValidationARunner",
    "ValidationBResult",
    "ValidationBRunner",
    "ValidationContext",
    "bind_candidate_to_suite",
    "create_execution_binding_overlay",
    "freeze_validation_context",
]
