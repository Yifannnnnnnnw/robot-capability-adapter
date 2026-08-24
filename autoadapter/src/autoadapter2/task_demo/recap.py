"""Canonical bounded ReCAP Task Demo controller for capability-v2.

The old B2 module was the first implementation of this recursion.  This file
is its single generalized home; ``autoadapter2.b2.recap`` only re-exports the
symbols for compatibility.  The controller owns planning and typed tool
routing, never a physical verdict.  The trusted Harness remains the only
component allowed to issue PASS/FAIL.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TYPE_CHECKING

if TYPE_CHECKING:  # Avoid importing the compatibility b2 package at module load.
    from autoadapter2.b2.capability_adapter import CapabilityAdapter


RECAP_SYSTEM_PROMPT = """You are the fixed capability-v2 ReCAP controller for one public robot task.
At each planning turn replace the current node's complete remaining ordered plan. The runtime
executes only its head, returns one bounded public observation, and asks for a revision. Use an
abstract subtask when more decomposition is needed. Use a capability leaf only with a dynamic
capability_name and native request that exactly match the sealed public catalogue. Do not invent
wrapper fields or expose private criteria, bindings, guards, resets, references, credentials, or
Harness verdicts. A controller completion is not physical success; the trusted Harness owns the
final verdict.

Return only {reasoning_summary, subtasks}. reasoning_summary is a concise plan summary, not
private chain-of-thought. An empty list completes the current recursive node; an empty root list
stops the controller."""
RECAP_B2_SYSTEM_PROMPT = RECAP_SYSTEM_PROMPT


RECAP_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reasoning_summary": {"type": "string"},
        "subtasks": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"const": "subtask"},
                            "description": {"type": "string"},
                        },
                        "required": ["kind", "description"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"const": "capability"},
                            "capability_name": {"type": "string"},
                            "request": {"type": "object"},
                        },
                        "required": ["kind", "capability_name", "request"],
                        "additionalProperties": False,
                    },
                ]
            },
        },
    },
    "required": ["reasoning_summary", "subtasks"],
    "additionalProperties": False,
}


class RecapControllerError(RuntimeError):
    """Raised for invalid public controller inputs or model responses."""


class RecapModelClient(Protocol):
    def generate_recap_json(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        response_schema: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, init=False)
class RecapBudgets:
    """Frozen Task Demo budget: 16 planning turns and 12 capability calls."""

    max_planning_turns: int
    max_capability_calls: int
    max_depth: int
    max_subtasks_per_plan: int
    max_invalid_outputs: int
    max_history_chars: int

    def __init__(
        self,
        max_planning_turns: int = 16,
        max_capability_calls: int = 12,
        max_depth: int = 6,
        max_subtasks_per_plan: int = 8,
        max_invalid_outputs: int = 3,
        max_history_chars: int = 80_000,
        max_model_calls: int | None = None,
    ) -> None:
        if max_model_calls is not None:
            max_planning_turns = max_model_calls
        values = {
            "max_planning_turns": max_planning_turns,
            "max_capability_calls": max_capability_calls,
            "max_depth": max_depth,
            "max_subtasks_per_plan": max_subtasks_per_plan,
            "max_invalid_outputs": max_invalid_outputs,
            "max_history_chars": max_history_chars,
        }
        for name, value in values.items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise RecapControllerError(f"{name} must be a positive integer")
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @property
    def max_model_calls(self) -> int:
        """Historical name, retained as a read-only alias."""

        return self.max_planning_turns


@dataclass(frozen=True)
class RecapControllerResult:
    status: str
    planning_turns: int
    capability_calls: int
    invalid_outputs: int
    trace: tuple[Mapping[str, Any], ...]
    context_tree: Mapping[str, Any]

    @property
    def model_calls(self) -> int:
        return self.planning_turns

    @property
    def model_turns(self) -> int:
        """Compatibility alias used by parent-side reporting."""

        return self.planning_turns

    @property
    def tool_calls(self) -> int:
        """Compatibility alias for validated capability invocations."""

        return self.capability_calls


@dataclass
class _Node:
    node_id: str
    description: str
    parent_id: str | None
    depth: int
    children: list[str] = field(default_factory=list)
    remaining_plan: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    revisions: list[dict[str, Any]] = field(default_factory=list)
    completed: bool = False


def run_recap(
    *,
    public_task: Mapping[str, Any],
    adapter: "CapabilityAdapter",
    model: RecapModelClient,
    budgets: RecapBudgets | None = None,
    initial_public_state: Mapping[str, Any] | None = None,
) -> RecapControllerResult:
    """Run one bounded ReCAP Task Demo against a live typed adapter."""

    # Imported lazily because b2.recap re-exports this module while the b2
    # package itself is initialising.
    from autoadapter2.b2.capability_adapter import (
        CapabilityAdapterError,
        CapabilityInvocationError,
    )

    fixed = budgets or RecapBudgets()
    task = _finite_json_object(public_task, label="public_task")
    _assert_public_payload(task, path="public_task")
    initial_state = None if initial_public_state is None else _finite_json_object(initial_public_state, label="initial_public_state")
    if initial_state is not None:
        _assert_public_payload(initial_state, path="initial_public_state")
    catalog = adapter.public_catalog()
    _assert_public_payload(catalog, path="capability_catalog")

    root_description = task.get("objective")
    if not isinstance(root_description, str) or not root_description.strip():
        root_description = "Complete the supplied public root task."
    nodes: dict[str, _Node] = {"n0": _Node("n0", root_description, None, 0)}
    current_id = "n0"
    next_node = 1
    planning_turns = 0
    capability_calls = 0
    invalid_outputs = 0
    trace: list[dict[str, Any]] = []
    base_message = _event_message(
        {
            "event": "controller_start",
            "public_task": task,
            "robot_configuration_id": adapter.robot_configuration_id,
            "capability_design_id": adapter.capability_design_id,
            "capability_catalog": catalog,
            "initial_public_state": initial_state,
        }
    )
    history: list[dict[str, str]] = []

    while True:
        if planning_turns >= fixed.max_planning_turns:
            return _result("PLANNING_TURN_BUDGET_EXHAUSTED", planning_turns, capability_calls, invalid_outputs, trace, nodes, current_id)
        current = nodes[current_id]
        context = _event_message(
            {
                "event": "plan_or_refine",
                "public_task": task,
                "current_path": _current_path(nodes, current_id),
                "current_node": {"node_id": current.node_id, "description": current.description, "depth": current.depth},
                "previous_remaining_plan": current.remaining_plan,
                "latest_public_observation": current.observations[-1] if current.observations else None,
            }
        )
        messages = _bounded_messages(base_message, history, context, fixed.max_history_chars)
        planning_turns += 1
        try:
            raw = model.generate_recap_json(
                stage="recursive_plan_or_refine",
                system_prompt=RECAP_SYSTEM_PROMPT,
                messages=messages,
                response_schema=RECAP_RESPONSE_SCHEMA,
            )
        except Exception:
            trace.append(_trace_entry(planning_turns, False, capability_calls, "model_error", True, "MODEL_ERROR"))
            return _result("MODEL_ERROR", planning_turns, capability_calls, invalid_outputs, trace, nodes, current_id)
        try:
            output = _parse_model_output(raw, max_subtasks=fixed.max_subtasks_per_plan)
        except RecapControllerError as exc:
            invalid_outputs += 1
            observation = {"kind": "controller_validation", "status": "INVALID_MODEL_OUTPUT", "message": str(exc)}
            current.observations.append(observation)
            history.extend([context, _event_message({"event": "invalid_model_output", "observation": observation}, role="assistant")])
            trace.append(_trace_entry(planning_turns, False, capability_calls, "invalid_model_output", True, "NOT_EXECUTED"))
            if invalid_outputs >= fixed.max_invalid_outputs:
                return _result("INVALID_OUTPUT_BUDGET_EXHAUSTED", planning_turns, capability_calls, invalid_outputs, trace, nodes, current_id)
            continue

        history.extend([context, _event_message(output, role="assistant")])
        current.revisions.append({"turn_index": planning_turns, "reasoning_summary": output["reasoning_summary"], "subtasks": output["subtasks"]})
        current.remaining_plan = output["subtasks"]
        if not current.remaining_plan:
            current.completed = True
            trace.append(_trace_entry(planning_turns, True, capability_calls, "complete_node", False, "NOT_APPLICABLE", controller_self_reported_completion=current.parent_id is None))
            if current.parent_id is None:
                return _result("CONTROLLER_FINISHED", planning_turns, capability_calls, invalid_outputs, trace, nodes, current_id)
            parent = nodes[current.parent_id]
            parent.observations.append({"kind": "subtask_completion", "status": "COMPLETED", "node_id": current.node_id, "description": current.description})
            current_id = parent.node_id
            continue

        head = current.remaining_plan.pop(0)
        if head["kind"] == "subtask":
            if current.depth >= fixed.max_depth:
                invalid_outputs += 1
                current.observations.append({"kind": "controller_validation", "status": "MAX_RECURSION_DEPTH_REACHED"})
                trace.append(_trace_entry(planning_turns, True, capability_calls, "subtask", True, "NOT_EXECUTED"))
                if invalid_outputs >= fixed.max_invalid_outputs:
                    return _result("INVALID_OUTPUT_BUDGET_EXHAUSTED", planning_turns, capability_calls, invalid_outputs, trace, nodes, current_id)
                continue
            child_id = f"n{next_node}"
            next_node += 1
            child = _Node(child_id, head["description"], current.node_id, current.depth + 1)
            nodes[child_id] = child
            current.children.append(child_id)
            trace.append(_trace_entry(planning_turns, True, capability_calls, "subtask", False, "NOT_APPLICABLE"))
            current_id = child_id
            continue

        capability_name = head["capability_name"]
        request = head["request"]
        try:
            validated = adapter.validate_request(capability_name, request)
        except CapabilityAdapterError as exc:
            invalid_outputs += 1
            current.observations.append({"kind": "capability_observation", "status": "INVALID_CAPABILITY_LEAF", "capability_name": capability_name, "message": str(exc)})
            trace.append(_trace_entry(planning_turns, True, capability_calls, "capability", True, "NOT_EXECUTED", capability_name=capability_name, public_arguments=request, argument_binding_valid=False))
            if invalid_outputs >= fixed.max_invalid_outputs:
                return _result("INVALID_OUTPUT_BUDGET_EXHAUSTED", planning_turns, capability_calls, invalid_outputs, trace, nodes, current_id)
            continue
        if capability_calls >= fixed.max_capability_calls:
            trace.append(_trace_entry(planning_turns, True, capability_calls, "capability", False, "CALL_BUDGET_EXHAUSTED", capability_name=capability_name, public_arguments=validated, argument_binding_valid=True))
            return _result("CAPABILITY_CALL_BUDGET_EXHAUSTED", planning_turns, capability_calls, invalid_outputs, trace, nodes, current_id)
        capability_calls += 1
        try:
            observation = adapter.execute(capability_name, validated)
            _assert_public_payload(observation, path="public_operation_observation")
            outcome = _operation_outcome(observation)
        except CapabilityInvocationError:
            observation = {"status": "CAPABILITY_EXECUTION_ERROR", "message": "The valid capability call did not complete normally."}
            outcome = "EXECUTION_ERROR"
        except RecapControllerError:
            observation = {"status": "PUBLIC_OBSERVATION_REJECTED", "message": "The worker response violated the public feedback boundary."}
            outcome = "PUBLIC_OBSERVATION_REJECTED"
        record = {"kind": "capability_observation", "status": outcome, "capability_name": capability_name, "public_observation": observation}
        current.observations.append(record)
        trace.append(_trace_entry(planning_turns, True, capability_calls, "capability", False, outcome, capability_name=capability_name, public_arguments=validated, argument_binding_valid=True))
        if outcome == "WORKER_ABORTED":
            return _result("WORKER_ABORTED", planning_turns, capability_calls, invalid_outputs, trace, nodes, current_id)


def _parse_model_output(raw: Mapping[str, Any], *, max_subtasks: int) -> dict[str, Any]:
    output = _finite_json_object(raw, label="model output")
    if set(output) != {"reasoning_summary", "subtasks"}:
        raise RecapControllerError("model output must contain exactly reasoning_summary and subtasks")
    summary = output["reasoning_summary"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 2_000:
        raise RecapControllerError("reasoning_summary must be a non-empty string of at most 2000 characters")
    subtasks = output["subtasks"]
    if not isinstance(subtasks, list) or len(subtasks) > max_subtasks:
        raise RecapControllerError("subtasks must be an array within the fixed plan budget")
    parsed: list[dict[str, Any]] = []
    for index, item in enumerate(subtasks):
        if not isinstance(item, dict):
            raise RecapControllerError(f"subtasks[{index}] must be an object")
        if item.get("kind") == "subtask":
            if set(item) != {"kind", "description"} or not isinstance(item["description"], str) or not item["description"].strip():
                raise RecapControllerError(f"subtasks[{index}] is an invalid abstract subtask")
        elif item.get("kind") == "capability":
            if set(item) != {"kind", "capability_name", "request"} or not isinstance(item["capability_name"], str) or not item["capability_name"].strip() or not isinstance(item["request"], dict):
                raise RecapControllerError(f"subtasks[{index}] is an invalid capability leaf")
        else:
            raise RecapControllerError(f"subtasks[{index}].kind is invalid")
        parsed.append(json.loads(json.dumps(item, allow_nan=False)))
    return {"reasoning_summary": summary, "subtasks": parsed}


def _trace_entry(turn: int, grammar_valid: bool, capability_calls: int, action_kind: str, invalid_call: bool, outcome: str, *, capability_name: str | None = None, public_arguments: Mapping[str, Any] | None = None, argument_binding_valid: bool | None = None, controller_self_reported_completion: bool = False) -> dict[str, Any]:
    return {"turn_index": turn, "output_grammar_valid": grammar_valid, "action_kind": action_kind, "capability_name": capability_name, "public_arguments": public_arguments, "argument_binding_valid": argument_binding_valid, "capability_execution_outcome": outcome, "invalid_call": invalid_call, "unnecessary_call": None, "call_budget_used": capability_calls, "controller_self_reported_completion": controller_self_reported_completion}


def _result(status: str, planning_turns: int, capability_calls: int, invalid_outputs: int, trace: list[dict[str, Any]], nodes: Mapping[str, _Node], current_id: str) -> RecapControllerResult:
    tree = {"root_node_id": "n0", "active_node_id": current_id, "nodes": [{"node_id": node.node_id, "description": node.description, "parent_id": node.parent_id, "depth": node.depth, "children": list(node.children), "remaining_plan": node.remaining_plan, "observations": node.observations, "revisions": node.revisions, "completed": node.completed} for node in nodes.values()]}
    return RecapControllerResult(status, planning_turns, capability_calls, invalid_outputs, tuple(_finite_json_object(item, label="trace entry") for item in trace), _finite_json_object(tree, label="context tree"))


def _current_path(nodes: Mapping[str, _Node], current_id: str) -> list[dict[str, Any]]:
    path: list[dict[str, Any]] = []
    cursor: _Node | None = nodes[current_id]
    while cursor is not None:
        path.append({"node_id": cursor.node_id, "description": cursor.description, "remaining_plan": cursor.remaining_plan, "latest_public_observation": cursor.observations[-1] if cursor.observations else None})
        cursor = nodes[cursor.parent_id] if cursor.parent_id is not None else None
    path.reverse()
    return path


def _bounded_messages(base: Mapping[str, str], history: Sequence[Mapping[str, str]], current: Mapping[str, str], max_chars: int) -> tuple[Mapping[str, str], ...]:
    base_cost = _message_cost(base) + _message_cost(current)
    if base_cost > max_chars:
        raise RecapControllerError("public task and capability catalogue exceed max_history_chars")
    if len(history) % 2:
        raise RecapControllerError("ReCAP history must contain complete turns")
    selected: list[Mapping[str, str]] = []
    used = base_cost
    for index in range(len(history) - 2, -1, -2):
        pair = history[index:index + 2]
        cost = sum(_message_cost(item) for item in pair)
        if used + cost <= max_chars:
            selected[0:0] = list(pair)
            used += cost
    return (base, *selected, current)


def _message_cost(message: Mapping[str, str]) -> int:
    return len(message.get("role", "")) + len(message.get("content", ""))


def _event_message(payload: Mapping[str, Any], *, role: str = "user") -> dict[str, str]:
    return {"role": role, "content": json.dumps(payload, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)}


def _operation_outcome(observation: Mapping[str, Any]) -> str:
    operation = observation.get("operation")
    if not isinstance(operation, Mapping):
        raise RecapControllerError("public operation observation must contain an operation object")
    status = operation.get("status")
    outcomes = {"EXECUTED": "EXECUTED", "ERROR": "EXECUTION_ERROR", "ABORT": "WORKER_ABORTED"}
    if status not in outcomes:
        raise RecapControllerError("public operation status must be EXECUTED, ERROR, or ABORT")
    return outcomes[status]


_FORBIDDEN_PUBLIC_KEYS = {"binding", "bindings", "criterion", "criteria", "guard", "guards", "hidden", "private", "reset", "threshold", "verdict", "success", "reference"}
_FORBIDDEN_PUBLIC_TOKENS = {"binding", "guard", "hidden", "private", "reset", "threshold", "verdict", "credential", "reference"}


def _assert_public_payload(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_PUBLIC_KEYS or set(normalized.split("_")) & _FORBIDDEN_PUBLIC_TOKENS:
                raise RecapControllerError(f"{path} contains a field forbidden by the public boundary")
            _assert_public_payload(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_public_payload(child, path=f"{path}[{index}]")


def _finite_json_object(value: Any, *, label: str) -> dict[str, Any]:
    try:
        copied = json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise RecapControllerError(f"{label} must be a finite JSON object") from exc
    if not isinstance(copied, dict):
        raise RecapControllerError(f"{label} must be an object")
    return copied


__all__ = [
    "RECAP_B2_SYSTEM_PROMPT",
    "RECAP_RESPONSE_SCHEMA",
    "RECAP_SYSTEM_PROMPT",
    "RecapBudgets",
    "RecapControllerError",
    "RecapControllerResult",
    "RecapModelClient",
    "run_recap",
]
