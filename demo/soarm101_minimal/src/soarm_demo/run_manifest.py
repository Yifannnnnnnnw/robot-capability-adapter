"""Auditable state machine for a single demo run."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .audit import atomic_write_json, utc_now


class RunState(StrEnum):
    INIT = "INIT"
    INPUT_LIBRARIES_READY = "INPUT_LIBRARIES_READY"
    TASK_SPLIT_FROZEN = "TASK_SPLIT_FROZEN"
    GENERATION_AGENT_READY = "GENERATION_AGENT_READY"
    STAGE1_RUNNING = "STAGE1_RUNNING"
    STAGE1_FROZEN = "STAGE1_FROZEN"
    STAGE2_RUNNING = "STAGE2_RUNNING"
    STATIC_VALIDATION = "STATIC_VALIDATION"
    VALIDATION_SUITE_GENERATION = "VALIDATION_SUITE_GENERATION"
    DIRECT_FUNCTION_VALIDATION = "DIRECT_FUNCTION_VALIDATION"
    TOOL_PACKAGING = "TOOL_PACKAGING"
    DEMO_FROZEN = "DEMO_FROZEN"
    DEMO_RUNNING = "DEMO_RUNNING"
    SEALED = "SEALED"
    INPUT_INVALID = "INPUT_INVALID"
    GENERATION_FAILED = "GENERATION_FAILED"
    VALIDATION_SUITE_GENERATION_FAILED = "VALIDATION_SUITE_GENERATION_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    TOOL_PACKAGING_FAILED = "TOOL_PACKAGING_FAILED"
    DEMO_FAILED = "DEMO_FAILED"
    INFRASTRUCTURE_FAILED = "INFRASTRUCTURE_FAILED"


DEMO_ORACLE_GATE_FAILURE_REASON = "demo_oracle_success_gate_failed"


TERMINAL_STATES = {
    RunState.SEALED,
    RunState.INPUT_INVALID,
    RunState.GENERATION_FAILED,
    RunState.VALIDATION_SUITE_GENERATION_FAILED,
    RunState.VALIDATION_FAILED,
    RunState.TOOL_PACKAGING_FAILED,
    RunState.DEMO_FAILED,
    RunState.INFRASTRUCTURE_FAILED,
}


ALLOWED_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.INIT: {RunState.INPUT_LIBRARIES_READY, RunState.INPUT_INVALID},
    RunState.INPUT_LIBRARIES_READY: {RunState.TASK_SPLIT_FROZEN, RunState.INPUT_INVALID},
    RunState.TASK_SPLIT_FROZEN: {RunState.GENERATION_AGENT_READY, RunState.INFRASTRUCTURE_FAILED},
    RunState.GENERATION_AGENT_READY: {RunState.STAGE1_RUNNING, RunState.INFRASTRUCTURE_FAILED},
    RunState.STAGE1_RUNNING: {
        RunState.STAGE1_FROZEN,
        RunState.GENERATION_FAILED,
        RunState.INFRASTRUCTURE_FAILED,
    },
    RunState.STAGE1_FROZEN: {RunState.STAGE2_RUNNING, RunState.INFRASTRUCTURE_FAILED},
    RunState.STAGE2_RUNNING: {
        RunState.STATIC_VALIDATION,
        RunState.GENERATION_FAILED,
        RunState.INFRASTRUCTURE_FAILED,
    },
    RunState.STATIC_VALIDATION: {
        RunState.STAGE2_RUNNING,
        RunState.VALIDATION_SUITE_GENERATION,
        RunState.DIRECT_FUNCTION_VALIDATION,
        RunState.VALIDATION_FAILED,
        RunState.INFRASTRUCTURE_FAILED,
    },
    RunState.VALIDATION_SUITE_GENERATION: {
        RunState.DIRECT_FUNCTION_VALIDATION,
        RunState.VALIDATION_SUITE_GENERATION_FAILED,
        RunState.INFRASTRUCTURE_FAILED,
    },
    RunState.DIRECT_FUNCTION_VALIDATION: {
        RunState.STAGE2_RUNNING,
        RunState.TOOL_PACKAGING,
        RunState.VALIDATION_SUITE_GENERATION_FAILED,
        RunState.VALIDATION_FAILED,
        RunState.INFRASTRUCTURE_FAILED,
    },
    RunState.TOOL_PACKAGING: {
        RunState.DEMO_FROZEN,
        RunState.TOOL_PACKAGING_FAILED,
        RunState.INFRASTRUCTURE_FAILED,
    },
    RunState.DEMO_FROZEN: {RunState.DEMO_RUNNING, RunState.INFRASTRUCTURE_FAILED},
    RunState.DEMO_RUNNING: {
        RunState.SEALED,
        RunState.DEMO_FAILED,
        RunState.INFRASTRUCTURE_FAILED,
    },
}


@dataclass
class RunManifest:
    root: Path
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: RunState = RunState.INIT
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    input_hashes: dict[str, str] = field(default_factory=dict)
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    budgets: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    terminal_reason: str | None = None

    @classmethod
    def create(cls, runs_root: str | Path, run_id: str | None = None) -> "RunManifest":
        identifier = run_id or uuid.uuid4().hex
        root = Path(runs_root) / identifier
        root.mkdir(parents=True, exist_ok=False)
        manifest = cls(root=root, run_id=identifier)
        manifest.record("run_created")
        return manifest

    @property
    def path(self) -> Path:
        return self.root / "run_manifest.json"

    def transition(self, target: RunState, *, reason: str | None = None) -> None:
        if self.state in TERMINAL_STATES:
            raise RuntimeError(f"cannot transition terminal run from {self.state}")
        allowed = ALLOWED_TRANSITIONS.get(self.state, set())
        if target not in allowed:
            raise ValueError(f"invalid run transition {self.state} -> {target}")
        previous = self.state
        self.state = target
        if target in TERMINAL_STATES:
            self.terminal_reason = reason
        self.record("state_transition", previous=previous.value, current=target.value, reason=reason)

    def record(self, event: str, **details: Any) -> None:
        self.updated_at = utc_now()
        self.events.append({"timestamp": self.updated_at, "event": event, **details})
        self.save()

    def save(self) -> None:
        payload = asdict(self)
        payload["root"] = str(self.root)
        payload["state"] = self.state.value
        atomic_write_json(self.path, payload)
