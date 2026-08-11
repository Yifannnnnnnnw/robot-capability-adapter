from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from typing import Any

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import StateTransitionError
from ..foundation.hashing import content_hash, is_content_hash


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


@dataclass(frozen=True)
class GateReceipt:
    run_id: str
    gate: str
    content_hash: str
    receipt_hash: str

    @classmethod
    def issue(cls, run_id: str, gate: str, evidence: Any) -> "GateReceipt":
        content = content_hash(canonical_bytes(evidence))
        body = {"run_id": run_id, "gate": gate, "content_hash": content}
        return cls(run_id, gate, content, content_hash(canonical_bytes(body)))

    def verify(self, run_id: str, gate: str) -> None:
        if self.run_id != run_id or self.gate != gate or not is_content_hash(self.content_hash):
            raise StateTransitionError("gate receipt identity or content hash is invalid")
        body = {"run_id": self.run_id, "gate": self.gate, "content_hash": self.content_hash}
        if content_hash(canonical_bytes(body)) != self.receipt_hash:
            raise StateTransitionError("gate receipt hash is invalid")


class RunStateMachine:
    def __init__(self, initial: RunState = RunState.CREATED, run_id: str = "run"):
        self.state = RunState(initial)
        self.run_id = run_id

    def transition(self, target: RunState, receipt: GateReceipt | None = None) -> RunState:
        target = RunState(target)
        if target not in TRANSITIONS[self.state]:
            raise StateTransitionError(
                f"illegal transition {self.state.value} -> {target.value}"
            )
        required_gate = {
            RunState.RIM_RESOLVED: "rim_resolved",
            RunState.READY_FOR_STAGE1: "ready_for_stage1",
        }.get(target)
        if required_gate is not None:
            if receipt is None:
                raise StateTransitionError(f"{target.value} requires a gate receipt")
            receipt.verify(self.run_id, required_gate)
        self.state = target
        return self.state
