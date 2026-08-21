"""Bounded, typed ReCAP controller core for a prospective B2 extension.

This module adapts the public ReCAP recursion pattern to AutoAdapter's sealed
B1 capability ABI.  It is controller logic only: a later B2 runner must supply
the persistent MuJoCo worker, public observation projector, fixed model client,
and final trusted Harness verdict.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from autoadapter2.b2.capability_adapter import (
    CapabilityAdapter,
    CapabilityAdapterError,
    CapabilityInvocationError,
)


RECAP_B2_SYSTEM_PROMPT = """\
You are the fixed high-level ReCAP controller for one public robot task.

Recursively decompose the current node into a complete ordered list of
subtasks. The runtime executes only the head item, returns one bounded public
observation, and then asks you to replace/refine the remaining list. Use an
abstract `subtask` when more decomposition is needed. Use a `capability` leaf
only with a capability_name and capability-native request that exactly match
the supplied public catalogue and request schema. Never invent wrapper fields.

Return only the fixed JSON response. `reasoning_summary` must be a concise plan
summary, not private chain-of-thought. An empty subtask list completes only the
current recursive node; an empty list at the root stops the controller. Stopping
the controller is not physical success and is never a Harness verdict. You do
not receive private criteria, thresholds, bindings, guards, reset state,
reference inputs, model credentials, or the final physical verdict.
"""


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
                            "request": {
                                "type": "object",
                                "additionalProperties": True,
                            },
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
    """Raised for an invalid fixed controller configuration or public input."""


class RecapModelClient(Protocol):
    """Replaceable backbone boundary used by the otherwise fixed controller."""

    def generate_recap_json(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        response_schema: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class RecapBudgets:
    """Controller-native budgets to freeze unchanged across backbones."""

    max_model_calls: int = 24
    max_capability_calls: int = 16
    max_depth: int = 6
    max_subtasks_per_plan: int = 8
    max_invalid_outputs: int = 3
    max_history_chars: int = 80_000

    def __post_init__(self) -> None:
        for name in (
            "max_model_calls",
            "max_capability_calls",
            "max_depth",
            "max_subtasks_per_plan",
            "max_invalid_outputs",
            "max_history_chars",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise RecapControllerError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class RecapControllerResult:
    """Controller outcome only; intentionally contains no task-success verdict."""

    status: str
    model_calls: int
    capability_calls: int
    invalid_outputs: int
    trace: tuple[Mapping[str, Any], ...]
    context_tree: Mapping[str, Any]


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
    adapter: CapabilityAdapter,
    model: RecapModelClient,
    budgets: RecapBudgets | None = None,
) -> RecapControllerResult:
    """Run the fixed recursive controller against one public capability adapter.

    A model turn may propose several ordered items, but this loop processes only
    the head before requesting another model revision.  Abstract heads descend
    into a child context; capability heads execute through the typed B1 adapter.
    """

    fixed_budgets = budgets or RecapBudgets()
    task = _finite_json_object(public_task, label="public_task")
    _assert_public_payload(task, path="public_task")
    catalog = adapter.public_catalog()
    _assert_public_payload(catalog, path="capability_catalog")

    root_description = task.get("objective")
    if not isinstance(root_description, str) or not root_description.strip():
        root_description = "Complete the supplied public root task."
    nodes: dict[str, _Node] = {
        "n0": _Node(
            node_id="n0",
            description=root_description,
            parent_id=None,
            depth=0,
        )
    }
    current_id = "n0"
    next_node_index = 1
    model_calls = 0
    capability_calls = 0
    invalid_outputs = 0
    trace: list[dict[str, Any]] = []

    base_message = _event_message(
        {
            "event": "controller_start",
            "root_task": task,
            "robot_configuration_id": adapter.robot_configuration_id,
            "capability_design_id": adapter.capability_design_id,
            "capability_catalog": catalog,
        }
    )
    rolling_history: list[dict[str, str]] = []

    while True:
        if model_calls >= fixed_budgets.max_model_calls:
            return _result(
                status="MODEL_CALL_BUDGET_EXHAUSTED",
                model_calls=model_calls,
                capability_calls=capability_calls,
                invalid_outputs=invalid_outputs,
                trace=trace,
                nodes=nodes,
                current_id=current_id,
            )

        current = nodes[current_id]
        context_message = _event_message(
            {
                "event": "plan_or_refine",
                "root_goal": task,
                "current_path": _current_path(nodes, current_id),
                "current_node": {
                    "node_id": current.node_id,
                    "description": current.description,
                    "depth": current.depth,
                },
                "previous_remaining_plan": current.remaining_plan,
                "latest_public_observation": (
                    current.observations[-1] if current.observations else None
                ),
                "instruction": (
                    "Replace the current node's complete remaining ordered plan. "
                    "The runtime will process only its head."
                ),
            }
        )
        messages = _bounded_messages(
            base_message=base_message,
            rolling_history=rolling_history,
            current_message=context_message,
            max_chars=fixed_budgets.max_history_chars,
        )

        model_calls += 1
        turn_index = model_calls
        try:
            raw_output = model.generate_recap_json(
                stage="recursive_plan_or_refine",
                system_prompt=RECAP_B2_SYSTEM_PROMPT,
                messages=messages,
                response_schema=RECAP_RESPONSE_SCHEMA,
            )
        except Exception:
            trace.append(
                _trace_entry(
                    turn_index=turn_index,
                    output_grammar_valid=False,
                    capability_calls=capability_calls,
                    action_kind="model_error",
                    invalid_call=True,
                    capability_execution_outcome="MODEL_ERROR",
                )
            )
            return _result(
                status="MODEL_ERROR",
                model_calls=model_calls,
                capability_calls=capability_calls,
                invalid_outputs=invalid_outputs,
                trace=trace,
                nodes=nodes,
                current_id=current_id,
            )

        try:
            output = _parse_model_output(
                raw_output,
                max_subtasks=fixed_budgets.max_subtasks_per_plan,
            )
        except RecapControllerError as exc:
            invalid_outputs += 1
            observation = {
                "kind": "controller_validation",
                "status": "INVALID_MODEL_OUTPUT",
                "message": str(exc),
            }
            current.observations.append(observation)
            rolling_history.extend(
                [
                    context_message,
                    _event_message(
                        {
                            "event": "invalid_model_output",
                            "observation": observation,
                        },
                        role="assistant",
                    ),
                ]
            )
            trace.append(
                _trace_entry(
                    turn_index=turn_index,
                    output_grammar_valid=False,
                    capability_calls=capability_calls,
                    action_kind="invalid_model_output",
                    invalid_call=True,
                    capability_execution_outcome="NOT_EXECUTED",
                )
            )
            if invalid_outputs >= fixed_budgets.max_invalid_outputs:
                return _result(
                    status="INVALID_OUTPUT_BUDGET_EXHAUSTED",
                    model_calls=model_calls,
                    capability_calls=capability_calls,
                    invalid_outputs=invalid_outputs,
                    trace=trace,
                    nodes=nodes,
                    current_id=current_id,
                )
            continue

        rolling_history.extend(
            [context_message, _event_message(output, role="assistant")]
        )
        current.revisions.append(
            {
                "turn_index": turn_index,
                "reasoning_summary": output["reasoning_summary"],
                "subtasks": output["subtasks"],
            }
        )
        current.remaining_plan = output["subtasks"]

        if not current.remaining_plan:
            current.completed = True
            is_root = current.parent_id is None
            trace.append(
                _trace_entry(
                    turn_index=turn_index,
                    output_grammar_valid=True,
                    capability_calls=capability_calls,
                    action_kind="complete_node",
                    invalid_call=False,
                    capability_execution_outcome="NOT_APPLICABLE",
                    controller_self_reported_completion=is_root,
                )
            )
            if is_root:
                return _result(
                    status="CONTROLLER_FINISHED",
                    model_calls=model_calls,
                    capability_calls=capability_calls,
                    invalid_outputs=invalid_outputs,
                    trace=trace,
                    nodes=nodes,
                    current_id=current_id,
                )

            parent = nodes[current.parent_id]
            parent.observations.append(
                {
                    "kind": "subtask_completion",
                    "status": "COMPLETED",
                    "node_id": current.node_id,
                    "description": current.description,
                }
            )
            current_id = parent.node_id
            continue

        head = current.remaining_plan[0]
        current.remaining_plan = current.remaining_plan[1:]
        if head["kind"] == "subtask":
            if current.depth >= fixed_budgets.max_depth:
                invalid_outputs += 1
                observation = {
                    "kind": "controller_validation",
                    "status": "MAX_RECURSION_DEPTH_REACHED",
                    "message": "Refine the remaining plan without another abstract level.",
                }
                current.observations.append(observation)
                trace.append(
                    _trace_entry(
                        turn_index=turn_index,
                        output_grammar_valid=True,
                        capability_calls=capability_calls,
                        action_kind="subtask",
                        invalid_call=True,
                        capability_execution_outcome="NOT_EXECUTED",
                    )
                )
                if invalid_outputs >= fixed_budgets.max_invalid_outputs:
                    return _result(
                        status="INVALID_OUTPUT_BUDGET_EXHAUSTED",
                        model_calls=model_calls,
                        capability_calls=capability_calls,
                        invalid_outputs=invalid_outputs,
                        trace=trace,
                        nodes=nodes,
                        current_id=current_id,
                    )
                continue

            child_id = f"n{next_node_index}"
            next_node_index += 1
            nodes[child_id] = _Node(
                node_id=child_id,
                description=head["description"],
                parent_id=current.node_id,
                depth=current.depth + 1,
            )
            current.children.append(child_id)
            trace.append(
                _trace_entry(
                    turn_index=turn_index,
                    output_grammar_valid=True,
                    capability_calls=capability_calls,
                    action_kind="subtask",
                    invalid_call=False,
                    capability_execution_outcome="NOT_APPLICABLE",
                )
            )
            current_id = child_id
            continue

        capability_name = head["capability_name"]
        native_request = head["request"]
        try:
            validated_request = adapter.validate_request(
                capability_name,
                native_request,
            )
        except CapabilityAdapterError as exc:
            invalid_outputs += 1
            observation = {
                "kind": "capability_observation",
                "status": "INVALID_CAPABILITY_LEAF",
                "capability_name": capability_name,
                "message": str(exc),
            }
            current.observations.append(observation)
            trace.append(
                _trace_entry(
                    turn_index=turn_index,
                    output_grammar_valid=True,
                    capability_calls=capability_calls,
                    action_kind="capability",
                    capability_name=capability_name,
                    public_arguments=native_request,
                    argument_binding_valid=False,
                    invalid_call=True,
                    capability_execution_outcome="NOT_EXECUTED",
                )
            )
            if invalid_outputs >= fixed_budgets.max_invalid_outputs:
                return _result(
                    status="INVALID_OUTPUT_BUDGET_EXHAUSTED",
                    model_calls=model_calls,
                    capability_calls=capability_calls,
                    invalid_outputs=invalid_outputs,
                    trace=trace,
                    nodes=nodes,
                    current_id=current_id,
                )
            continue

        if capability_calls >= fixed_budgets.max_capability_calls:
            trace.append(
                _trace_entry(
                    turn_index=turn_index,
                    output_grammar_valid=True,
                    capability_calls=capability_calls,
                    action_kind="capability",
                    capability_name=capability_name,
                    public_arguments=validated_request,
                    argument_binding_valid=True,
                    invalid_call=False,
                    capability_execution_outcome="CALL_BUDGET_EXHAUSTED",
                )
            )
            return _result(
                status="CAPABILITY_CALL_BUDGET_EXHAUSTED",
                model_calls=model_calls,
                capability_calls=capability_calls,
                invalid_outputs=invalid_outputs,
                trace=trace,
                nodes=nodes,
                current_id=current_id,
            )

        capability_calls += 1
        try:
            public_observation = adapter.execute(
                capability_name,
                validated_request,
            )
            _assert_public_payload(
                public_observation,
                path="public_operation_observation",
            )
            operation_outcome = _operation_outcome(public_observation)
        except CapabilityInvocationError:
            public_observation = {
                "status": "CAPABILITY_EXECUTION_ERROR",
                "message": "The valid capability call did not complete normally.",
            }
            operation_outcome = "EXECUTION_ERROR"
        except RecapControllerError:
            public_observation = {
                "status": "PUBLIC_OBSERVATION_REJECTED",
                "message": "The worker response violated the public feedback boundary.",
            }
            operation_outcome = "PUBLIC_OBSERVATION_REJECTED"

        observation = {
            "kind": "capability_observation",
            "status": operation_outcome,
            "capability_name": capability_name,
            "public_observation": public_observation,
        }
        current.observations.append(observation)
        trace.append(
            _trace_entry(
                turn_index=turn_index,
                output_grammar_valid=True,
                capability_calls=capability_calls,
                action_kind="capability",
                capability_name=capability_name,
                public_arguments=validated_request,
                argument_binding_valid=True,
                invalid_call=False,
                capability_execution_outcome=operation_outcome,
            )
        )
        if operation_outcome == "WORKER_ABORTED":
            return _result(
                status="WORKER_ABORTED",
                model_calls=model_calls,
                capability_calls=capability_calls,
                invalid_outputs=invalid_outputs,
                trace=trace,
                nodes=nodes,
                current_id=current_id,
            )


def _parse_model_output(
    raw_output: Mapping[str, Any],
    *,
    max_subtasks: int,
) -> dict[str, Any]:
    output = _finite_json_object(raw_output, label="model output")
    if set(output) != {"reasoning_summary", "subtasks"}:
        raise RecapControllerError(
            "model output must contain exactly reasoning_summary and subtasks"
        )
    summary = output["reasoning_summary"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 2_000:
        raise RecapControllerError(
            "reasoning_summary must be a non-empty string of at most 2000 characters"
        )
    subtasks = output["subtasks"]
    if not isinstance(subtasks, list):
        raise RecapControllerError("subtasks must be an array")
    if len(subtasks) > max_subtasks:
        raise RecapControllerError("subtasks exceeds max_subtasks_per_plan")

    parsed: list[dict[str, Any]] = []
    for index, step in enumerate(subtasks):
        if not isinstance(step, dict):
            raise RecapControllerError(f"subtasks[{index}] must be an object")
        kind = step.get("kind")
        if kind == "subtask":
            if set(step) != {"kind", "description"}:
                raise RecapControllerError(
                    f"subtasks[{index}] has invalid subtask fields"
                )
            description = step["description"]
            if (
                not isinstance(description, str)
                or not description.strip()
                or len(description) > 1_000
            ):
                raise RecapControllerError(
                    f"subtasks[{index}].description is invalid"
                )
        elif kind == "capability":
            if set(step) != {"kind", "capability_name", "request"}:
                raise RecapControllerError(
                    f"subtasks[{index}] has invalid capability fields"
                )
            if (
                not isinstance(step["capability_name"], str)
                or not step["capability_name"].strip()
            ):
                raise RecapControllerError(
                    f"subtasks[{index}].capability_name is invalid"
                )
            if not isinstance(step["request"], dict):
                raise RecapControllerError(f"subtasks[{index}].request must be an object")
        else:
            raise RecapControllerError(f"subtasks[{index}].kind is invalid")
        parsed.append(step)

    return {"reasoning_summary": summary, "subtasks": parsed}


def _trace_entry(
    *,
    turn_index: int,
    output_grammar_valid: bool,
    capability_calls: int,
    action_kind: str,
    invalid_call: bool,
    capability_execution_outcome: str,
    capability_name: str | None = None,
    public_arguments: Mapping[str, Any] | None = None,
    argument_binding_valid: bool | None = None,
    controller_self_reported_completion: bool = False,
) -> dict[str, Any]:
    return {
        "turn_index": turn_index,
        "output_grammar_valid": output_grammar_valid,
        "action_kind": action_kind,
        "capability_name": capability_name,
        "public_arguments": public_arguments,
        "argument_binding_valid": argument_binding_valid,
        "capability_execution_outcome": capability_execution_outcome,
        "invalid_call": invalid_call,
        "unnecessary_call": None,
        "call_budget_used": capability_calls,
        "controller_self_reported_completion": controller_self_reported_completion,
    }


def _result(
    *,
    status: str,
    model_calls: int,
    capability_calls: int,
    invalid_outputs: int,
    trace: list[dict[str, Any]],
    nodes: Mapping[str, _Node],
    current_id: str,
) -> RecapControllerResult:
    tree = {
        "root_node_id": "n0",
        "active_node_id": current_id,
        "nodes": [
            {
                "node_id": node.node_id,
                "description": node.description,
                "parent_id": node.parent_id,
                "depth": node.depth,
                "children": list(node.children),
                "remaining_plan": node.remaining_plan,
                "observations": node.observations,
                "revisions": node.revisions,
                "completed": node.completed,
            }
            for node in nodes.values()
        ],
    }
    return RecapControllerResult(
        status=status,
        model_calls=model_calls,
        capability_calls=capability_calls,
        invalid_outputs=invalid_outputs,
        trace=tuple(_finite_json_object(item, label="trace entry") for item in trace),
        context_tree=_finite_json_object(tree, label="context tree"),
    )


def _current_path(nodes: Mapping[str, _Node], current_id: str) -> list[dict[str, Any]]:
    path: list[dict[str, Any]] = []
    cursor: _Node | None = nodes[current_id]
    while cursor is not None:
        path.append(
            {
                "node_id": cursor.node_id,
                "description": cursor.description,
                "remaining_plan": cursor.remaining_plan,
                "latest_public_observation": (
                    cursor.observations[-1] if cursor.observations else None
                ),
            }
        )
        cursor = nodes[cursor.parent_id] if cursor.parent_id is not None else None
    path.reverse()
    return path


def _bounded_messages(
    *,
    base_message: Mapping[str, str],
    rolling_history: Sequence[Mapping[str, str]],
    current_message: Mapping[str, str],
    max_chars: int,
) -> tuple[Mapping[str, str], ...]:
    base_cost = _message_cost(base_message)
    current_cost = _message_cost(current_message)
    if base_cost + current_cost > max_chars:
        raise RecapControllerError(
            "public task and capability catalogue exceed max_history_chars"
        )

    if len(rolling_history) % 2 != 0:
        raise RecapControllerError("rolling ReCAP history must contain complete turns")

    selected_turns: list[Sequence[Mapping[str, str]]] = []
    used = base_cost + current_cost
    turns = [rolling_history[index : index + 2] for index in range(0, len(rolling_history), 2)]
    for turn in reversed(turns):
        cost = sum(_message_cost(message) for message in turn)
        if used + cost > max_chars:
            continue
        selected_turns.append(turn)
        used += cost
    selected_turns.reverse()
    selected = [message for turn in selected_turns for message in turn]
    return (base_message, *selected, current_message)


def _message_cost(message: Mapping[str, str]) -> int:
    return len(message.get("role", "")) + len(message.get("content", ""))


def _event_message(payload: Mapping[str, Any], *, role: str = "user") -> dict[str, str]:
    return {
        "role": role,
        "content": json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    }


def _operation_outcome(public_observation: Mapping[str, Any]) -> str:
    operation = public_observation.get("operation")
    if not isinstance(operation, dict):
        raise RecapControllerError(
            "public operation observation must contain an operation object"
        )
    status = operation.get("status")
    outcomes = {
        "EXECUTED": "EXECUTED",
        "ERROR": "EXECUTION_ERROR",
        "ABORT": "WORKER_ABORTED",
    }
    if status not in outcomes:
        raise RecapControllerError(
            "public operation status must be EXECUTED, ERROR, or ABORT"
        )
    return outcomes[status]


_FORBIDDEN_PUBLIC_KEYS = {
    "binding",
    "bindings",
    "criterion",
    "criteria",
    "guard",
    "guards",
    "hidden",
    "private",
    "private_seed",
    "public_standard",
    "reference_input",
    "reference_path",
    "reference_source",
    "reset",
    "reset_state",
    "threshold",
    "thresholds",
}
_FORBIDDEN_PUBLIC_KEY_TOKENS = {
    "binding",
    "bindings",
    "completed",
    "completion",
    "credential",
    "credentials",
    "criteria",
    "criterion",
    "guard",
    "guards",
    "hidden",
    "passed",
    "private",
    "reference",
    "reset",
    "success",
    "threshold",
    "thresholds",
    "verdict",
}


def _assert_public_payload(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.strip().lower().replace("-", "_")
            key_tokens = set(normalized.split("_"))
            if (
                normalized in _FORBIDDEN_PUBLIC_KEYS
                or key_tokens & _FORBIDDEN_PUBLIC_KEY_TOKENS
            ):
                raise RecapControllerError(
                    f"{path} contains a field forbidden by the public boundary"
                )
            _assert_public_payload(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_public_payload(child, path=f"{path}[{index}]")


def _finite_json_object(value: Any, *, label: str) -> dict[str, Any]:
    try:
        copied = json.loads(
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError) as exc:
        raise RecapControllerError(f"{label} must be finite JSON: {exc}") from None
    if not isinstance(copied, dict):
        raise RecapControllerError(f"{label} must be an object")
    return copied


__all__ = [
    "RECAP_B2_SYSTEM_PROMPT",
    "RECAP_RESPONSE_SCHEMA",
    "RecapBudgets",
    "RecapControllerError",
    "RecapControllerResult",
    "RecapModelClient",
    "run_recap",
]
