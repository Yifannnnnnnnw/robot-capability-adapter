# SPDX-License-Identifier: Apache-2.0
"""auto_adapter.agent — ReAct loop + tools (framework-provided Layer 1)."""
from .react_loop import ReactLoop, ReactResult, ToolSpec, TraceStep

__all__ = [
    "ReactLoop",
    "ReactResult",
    "ToolSpec",
    "TraceStep",
    "TaskPlanner",
    "TaskResult",
]


def __getattr__(name):
    # Old benchmark exports should not load the legacy planner on the ReCAP path.
    if name in {"TaskPlanner", "TaskResult"}:
        from . import task_planner
        return getattr(task_planner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
