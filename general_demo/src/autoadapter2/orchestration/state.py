from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from typing import Any

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import StateTransitionError
from ..foundation.hashing import content_hash, is_content_hash

_ADMISSION_TOKEN = object()


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
    selection_hash: str
    _token: object = field(default=None, repr=False, compare=False)

    @classmethod
    def _from_registered(
        cls, run_id: str, gate: str, selection_hash: str, evidence: Any
    ) -> "GateReceipt":
        if not is_content_hash(selection_hash):
            raise StateTransitionError("registered selection hash is invalid")
        content = content_hash(canonical_bytes(evidence))
        body = {
            "run_id": run_id,
            "gate": gate,
            "content_hash": content,
            "selection_hash": selection_hash,
        }
        return cls(
            run_id,
            gate,
            content,
            content_hash(canonical_bytes(body)),
            selection_hash,
            _ADMISSION_TOKEN,
        )

    def verify(self, run_id: str, gate: str, selection_hash: str) -> None:
        if (
            self._token is not _ADMISSION_TOKEN
            or self.run_id != run_id
            or self.gate != gate
            or self.selection_hash != selection_hash
            or not is_content_hash(self.content_hash)
        ):
            raise StateTransitionError("gate receipt identity or content hash is invalid")
        body = {
            "run_id": self.run_id,
            "gate": self.gate,
            "content_hash": self.content_hash,
            "selection_hash": self.selection_hash,
        }
        if content_hash(canonical_bytes(body)) != self.receipt_hash:
            raise StateTransitionError("gate receipt hash is invalid")


class RunStateMachine:
    def __init__(self, run_id: str, selection_hash: str, initial: RunState = RunState.CREATED):
        self.state = RunState(initial)
        self.run_id = run_id
        if not is_content_hash(selection_hash):
            raise StateTransitionError("run state machine requires a registered selection hash")
        self.selection_hash = selection_hash

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
            receipt.verify(self.run_id, required_gate, self.selection_hash)
        self.state = target
        return self.state
