from __future__ import annotations

from enum import StrEnum

from ..foundation.errors import StateTransitionError


class RunState(StrEnum):
    CREATED = "CREATED"
    RIM_RESOLVED = "RIM_RESOLVED"
    READY_FOR_STAGE1 = "READY_FOR_STAGE1"
    CLOSED = "CLOSED"


TRANSITIONS = {
    RunState.CREATED: {RunState.RIM_RESOLVED},
    RunState.RIM_RESOLVED: {RunState.READY_FOR_STAGE1},
    RunState.READY_FOR_STAGE1: {RunState.CLOSED},
    RunState.CLOSED: set(),
}


class RunStateMachine:
    def __init__(self, initial: RunState = RunState.CREATED):
        self.state = RunState(initial)

    def transition(self, target: RunState) -> RunState:
        target = RunState(target)
        if target not in TRANSITIONS[self.state]:
            raise StateTransitionError(
                f"illegal transition {self.state.value} -> {target.value}"
            )
        self.state = target
        return self.state
