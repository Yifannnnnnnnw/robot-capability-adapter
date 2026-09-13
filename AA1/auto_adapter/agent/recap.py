# SPDX-License-Identifier: Apache-2.0
"""AA1's bounded recursive task controller. Completion is not a physical verdict."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any] | None
    raw_arguments: str
    argument_error: str | None = None


@dataclass(frozen=True)
class ToolTurn:
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    reasoning_content: str | None = None


class ToolModelClient(Protocol):
    def generate_tool_turn(
        self, *, stage: str, system_prompt: str,
        messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]],
    ) -> ToolTurn: ...


class CapabilityAdapterError(RuntimeError):
    """The proposed capability name or request is invalid."""


class CapabilityInvocationError(CapabilityAdapterError):
    """A valid capability could not be executed."""


class RecapControllerError(ValueError):
    """The controller's public input or proposed plan is invalid."""


@dataclass(frozen=True)
class RecapBudgets:
    max_planning_turns: int = 16
    max_capability_calls: int = 12
    max_depth: int = 6
    max_subtasks_per_plan: int = 8
    max_invalid_outputs: int = 3
    max_history_chars: int = 80_000

    def __post_init__(self):
        for name, value in asdict(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise RecapControllerError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class RecapControllerResult:
    status: str
    planning_turns: int
    capability_calls: int
    invalid_outputs: int
    trace: tuple[Mapping[str, Any], ...]
    context_tree: Mapping[str, Any]

    @property
    def model_turns(self):
        return self.planning_turns

    @property
    def tool_calls(self):
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


RECAP_SYSTEM_PROMPT = """You control one robot task by recursive planning.
Call submit_plan exactly once per turn to replace the current node's entire remaining
ordered plan. Supply only a short action summary, not private chain-of-thought.
Use abstract subtasks to decompose a task; use capability leaves with names and native
requests from the supplied public catalogue. The runtime executes ONLY the first item,
then asks you to revise the remaining plan using the latest public observation.
An abstract first item opens a child node. Finish that child with an empty subtasks list;
control then returns to its parent so you can revise the parent's remaining plan.
Do not repeat completed actions. Do not assume pending siblings executed. Preserve any
unfinished task requirements when revising. Empty subtasks at the root ends the controller
only after a real capability call. Completion does not determine physical task success.
Use only the supplied public task, capability interfaces and observations; do not invent
capabilities, simulation resets, private criteria or driver repair actions.
Budgets are shared by every node: leave planning turns for child returns and root completion.
"""


def _json_copy(value, label):
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise RecapControllerError(f"{label} must be finite JSON") from exc


def _message(payload):
    return {"role": "user", "content": json.dumps(payload, ensure_ascii=False, allow_nan=False)}


def _plan_tool(catalog, max_subtasks):
    variants = [{
        "type": "object",
        "properties": {"kind": {"const": "subtask", "type": "string"},
                       "description": {"type": "string", "minLength": 1}},
        "required": ["kind", "description"], "additionalProperties": False,
    }]
    names = set()
    for capability in catalog:
        name = capability.get("capability_name", capability.get("method_name"))
        schema = capability.get("request_schema")
        if not isinstance(name, str) or not name or name in names or not isinstance(schema, dict):
            raise RecapControllerError("public catalogue has an invalid or duplicate capability")
        names.add(name)
        variants.append({
            "type": "object", "description": capability.get("description", name),
            "properties": {"kind": {"const": "capability", "type": "string"},
                           "capability_name": {"const": name, "type": "string"},
                           "request": schema},
            "required": ["kind", "capability_name", "request"], "additionalProperties": False,
        })
    return {"type": "function", "function": {
        "name": "submit_plan",
        "description": "Replace this node's remaining plan; execute only its head. Empty completes this node.",
        "parameters": {
            "type": "object",
            "properties": {
                "reasoning_summary": {"type": "string", "minLength": 1, "maxLength": 2000},
                "subtasks": {"type": "array", "maxItems": max_subtasks,
                             "items": {"oneOf": variants}},
            },
            "required": ["reasoning_summary", "subtasks"], "additionalProperties": False,
        },
    }}, names


def _parse_plan(call, adapter, names, max_subtasks):
    if call.name != "submit_plan" or call.argument_error:
        raise RecapControllerError(call.argument_error or "only submit_plan is available")
    output = _json_copy(call.arguments, "plan")
    if not isinstance(output, dict) or set(output) != {"reasoning_summary", "subtasks"}:
        raise RecapControllerError("submit_plan requires exactly reasoning_summary and subtasks")
    summary, subtasks = output["reasoning_summary"], output["subtasks"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 2000:
        raise RecapControllerError("reasoning_summary must contain 1 to 2000 characters")
    if not isinstance(subtasks, list) or len(subtasks) > max_subtasks:
        raise RecapControllerError("subtasks must be an array within the plan budget")
    # Validate the complete proposed plan before executing its first item.
    for item in subtasks:
        if not isinstance(item, dict):
            raise RecapControllerError("every plan item must be an object")
        if item.get("kind") == "subtask":
            if (set(item) != {"kind", "description"} or not isinstance(item["description"], str)
                    or not item["description"].strip()):
                raise RecapControllerError("abstract subtask requires a nonempty description")
        elif item.get("kind") == "capability":
            if (set(item) != {"kind", "capability_name", "request"}
                    or not isinstance(item["capability_name"], str)
                    or item["capability_name"] not in names or not isinstance(item["request"], dict)):
                raise RecapControllerError("capability leaf requires a known name and native request object")
            item["request"] = adapter.validate_request(item["capability_name"], item["request"])
        else:
            raise RecapControllerError("plan item kind must be subtask or capability")
    return output


def _assistant_message(turn):
    return {"role": "assistant", "content": turn.content, "tool_calls": [
        {"id": call.id, "type": "function", "function": {
            "name": call.name, "arguments": json.dumps(call.arguments, allow_nan=False)
            if call.arguments is not None else call.raw_arguments}}
        for call in turn.tool_calls
    ]}


def _bounded_messages(base, history, current, max_chars):
    def cost(message):
        return len(json.dumps(message, ensure_ascii=False))
    used = cost(base) + cost(current)
    if used > max_chars:
        raise RecapControllerError("task, capability catalogue and current context exceed history budget")
    selected = []
    # Retain whole exchanges, including every tool result for an assistant turn.
    for exchange in reversed(history):
        size = sum(cost(message) for message in exchange)
        if used + size > max_chars:
            break
        selected.append(exchange)
        used += size
    return tuple([base, *(message for exchange in reversed(selected) for message in exchange), current])


def run_recap(*, public_task, adapter, model: ToolModelClient,
              budgets: RecapBudgets | None = None, initial_public_state=None) -> RecapControllerResult:
    """Execute one recursive task against a persistent adapter; never generate/repair a driver."""
    fixed = budgets or RecapBudgets()
    task = _json_copy(public_task, "public_task")
    if not isinstance(task, dict):
        raise RecapControllerError("public_task must be an object")
    latest = _json_copy(initial_public_state, "initial_public_state")
    catalog = _json_copy(adapter.public_catalog(), "capability catalogue")
    tool, names = _plan_tool(catalog, fixed.max_subtasks_per_plan)
    description = task.get("objective") or task.get("description") or "Complete the supplied robot task."
    nodes = {"n0": _Node("n0", description, None, 0)}
    current_id = "n0"
    planning_turns = capability_calls = invalid_outputs = executed_calls = 0
    trace, history = [], []
    base = _message({"event": "controller_start", "public_task": task,
                     "robot_configuration_id": adapter.robot_configuration_id,
                     "capability_design_id": adapter.capability_design_id,
                     "capability_catalog": catalog, "initial_public_state": latest,
                     "budgets": asdict(fixed)})

    def result(status):
        return RecapControllerResult(status, planning_turns, capability_calls, invalid_outputs,
                                     tuple(_json_copy(trace, "trace")), {
                                         "root_node_id": "n0", "active_node_id": current_id,
                                         "nodes": [asdict(node) for node in nodes.values()],
                                     })

    while planning_turns < fixed.max_planning_turns:
        current = nodes[current_id]
        path, cursor = [], current
        while cursor is not None:
            path.append({"node_id": cursor.node_id, "description": cursor.description,
                         "remaining_plan": cursor.remaining_plan})
            cursor = nodes.get(cursor.parent_id)
        context = _message({"event": "plan_or_refine", "current_path": list(reversed(path)),
                            "current_node": {"node_id": current_id, "description": current.description,
                                             "depth": current.depth},
                            "previous_remaining_plan": current.remaining_plan,
                            "latest_public_observation": latest,
                            "latest_node_event": current.observations[-1] if current.observations else None,
                            "remaining_budgets": {"planning_turns": fixed.max_planning_turns - planning_turns,
                                                  "capability_calls": fixed.max_capability_calls - capability_calls}})
        try:
            messages = _bounded_messages(base, history, context, fixed.max_history_chars)
        except RecapControllerError:
            return result("HISTORY_BUDGET_EXHAUSTED")
        planning_turns += 1
        try:
            turn = model.generate_tool_turn(stage="recursive_plan_or_refine",
                                            system_prompt=RECAP_SYSTEM_PROMPT, messages=messages, tools=(tool,))
            if not isinstance(turn, ToolTurn) or any(not isinstance(call, ToolCall) for call in turn.tool_calls):
                raise TypeError("model must return ToolTurn")
            assistant = _assistant_message(turn)
        except Exception as exc:
            trace.append({"turn_index": planning_turns, "node_id": current_id, "action_kind": "model_error",
                          "error_type": type(exc).__name__, "message": str(exc)})
            return result("MODEL_ERROR")

        entry = {"turn_index": planning_turns, "node_id": current_id,
                 "call_budget_used": capability_calls, "action_kind": "invalid_plan"}
        terminal = None
        try:
            if len(turn.tool_calls) != 1:
                raise RecapControllerError("call submit_plan exactly once per planning turn")
            output = _parse_plan(turn.tool_calls[0], adapter, names, fixed.max_subtasks_per_plan)
            plan = output["subtasks"]
            if not plan and current.parent_id is None and executed_calls == 0:
                raise RecapControllerError("root completion requires at least one executed capability call")
            if plan and plan[0]["kind"] == "subtask" and current.depth >= fixed.max_depth:
                raise RecapControllerError("maximum recursion depth reached; use a capability or complete this node")
            current.revisions.append({"turn_index": planning_turns, **_json_copy(output, "revision")})
            current.remaining_plan = list(plan)
            entry["reasoning_summary"] = output["reasoning_summary"]
            if not plan:
                current.completed = True
                entry.update(action_kind="complete_node", controller_self_reported_completion=current.parent_id is None)
                feedback = {"status": "NODE_COMPLETED", "node_id": current_id,
                            "latest_public_observation": latest}
                if current.parent_id is None:
                    terminal = "CONTROLLER_FINISHED"
                else:
                    parent = nodes[current.parent_id]
                    parent.observations.append(feedback)
                    current_id = parent.node_id
            elif plan[0]["kind"] == "subtask":
                head = current.remaining_plan.pop(0)
                child_id = f"n{len(nodes)}"
                nodes[child_id] = _Node(child_id, head["description"], current_id, current.depth + 1)
                current.children.append(child_id)
                entry.update(action_kind="subtask", child_node_id=child_id)
                feedback = {"status": "CHILD_STARTED", "node_id": child_id,
                            "latest_public_observation": latest}
                current_id = child_id
            else:
                head = current.remaining_plan[0]
                entry.update(action_kind="capability", capability_name=head["capability_name"],
                             public_arguments=head["request"])
                if capability_calls >= fixed.max_capability_calls:
                    terminal = "CAPABILITY_CALL_BUDGET_EXHAUSTED"
                    feedback = {"status": terminal}
                else:
                    current.remaining_plan.pop(0)
                    capability_calls += 1
                    observation = _json_copy(adapter.execute(head["capability_name"], head["request"]),
                                             "capability observation")
                    outcome = observation.get("operation", {}).get("status") if isinstance(observation, dict) else None
                    feedback = {"kind": "capability_observation", "capability_name": head["capability_name"],
                                "status": outcome or "INVALID_OBSERVATION", "public_observation": observation}
                    latest = observation
                    current.observations.append(feedback)
                    entry["capability_execution_outcome"] = outcome
                    if outcome == "EXECUTED":
                        executed_calls += 1
                    elif outcome in {"ABORTED", "WORKER_ABORTED"}:
                        terminal = "WORKER_ABORTED"
                    elif outcome != "ERROR":
                        terminal = "RUNTIME_ERROR"
        except (RecapControllerError, CapabilityAdapterError) as exc:
            # Invocation failures consume their attempted call, not the invalid-plan budget.
            if isinstance(exc, CapabilityInvocationError) or entry["action_kind"] == "capability":
                terminal = "RUNTIME_ERROR"
                feedback = {"status": "CAPABILITY_EXECUTION_ERROR", "error_type": type(exc).__name__,
                            "message": str(exc)}
            else:
                invalid_outputs += 1
                feedback = {"status": "INVALID_PLAN", "message": str(exc)}
                current.observations.append(feedback)
                if invalid_outputs >= fixed.max_invalid_outputs:
                    terminal = "INVALID_OUTPUT_BUDGET_EXHAUSTED"
        except Exception as exc:
            terminal = "RUNTIME_ERROR"
            feedback = {"status": "CAPABILITY_EXECUTION_ERROR", "error_type": type(exc).__name__,
                        "message": str(exc)}

        # A malformed multi-tool turn still receives one reply per tool ID, with no action.
        replies = [{"role": "tool", "tool_call_id": call.id,
                    "content": json.dumps(feedback, allow_nan=False)} for call in turn.tool_calls]
        if not replies:
            replies = [_message(feedback)]
        history.append([context, assistant, *replies])
        entry.update(call_budget_used=capability_calls, feedback=feedback,
                     tool_results=replies, invalid_call=feedback.get("status") == "INVALID_PLAN")
        trace.append(entry)
        if terminal:
            return result(terminal)
    return result("PLANNING_TURN_BUDGET_EXHAUSTED")
