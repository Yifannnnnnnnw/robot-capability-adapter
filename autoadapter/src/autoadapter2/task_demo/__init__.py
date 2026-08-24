"""Post-admission Task Demo high-level controllers.

``recap`` is the capability-v2 canonical runtime.  ``react`` remains an
import-compatible legacy controller for callers outside the current mainline.
"""

from .recap import (
    RECAP_B2_SYSTEM_PROMPT,
    RECAP_RESPONSE_SCHEMA,
    RECAP_SYSTEM_PROMPT,
    RecapBudgets,
    RecapControllerError,
    RecapControllerResult,
    RecapModelClient,
    run_recap,
)

from .react import (
    TASK_DEMO_REACT_SYSTEM_PROMPT,
    TaskDemoControllerError,
    TaskDemoControllerResult,
    run_task_demo_react,
)

__all__ = [
    "TASK_DEMO_REACT_SYSTEM_PROMPT",
    "RECAP_B2_SYSTEM_PROMPT",
    "RECAP_RESPONSE_SCHEMA",
    "RECAP_SYSTEM_PROMPT",
    "RecapBudgets",
    "RecapControllerError",
    "RecapControllerResult",
    "RecapModelClient",
    "TaskDemoControllerError",
    "TaskDemoControllerResult",
    "run_recap",
    "run_task_demo_react",
]
