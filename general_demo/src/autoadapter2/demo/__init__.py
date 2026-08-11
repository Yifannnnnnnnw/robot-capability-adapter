"""Private evaluation Harness adapters for the experiment-grade General Demo."""

from .fixed_criteria import FIXED_DEMO_TASK_IDS, evaluate_fixed_demo_criterion
from .harness import (
    CriterionEvaluator,
    DemoEvaluationHarness,
    DemoTask,
    DemoTrialResult,
    EvaluationRobotSession,
    EvaluationRoute,
    RecordingValidationHarness,
    ValidationEvidence,
    VideoEncoderFactory,
)

__all__ = [
    "CriterionEvaluator",
    "DemoEvaluationHarness",
    "DemoTask",
    "DemoTrialResult",
    "EvaluationRobotSession",
    "EvaluationRoute",
    "FIXED_DEMO_TASK_IDS",
    "RecordingValidationHarness",
    "ValidationEvidence",
    "VideoEncoderFactory",
    "evaluate_fixed_demo_criterion",
]
