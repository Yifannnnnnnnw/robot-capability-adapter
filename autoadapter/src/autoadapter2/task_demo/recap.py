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
from dataclasses import dataclass
from typing import Any, Protocol, TYPE_CHECKING

from autoadapter2.react import ToolCall, ToolModelClient, ToolTurn

if TYPE_CHECKING:  # Avoid importing the compatibility b2 package at module load.
    from autoadapter2.b2.capability_adapter import CapabilityAdapter


RECAP_SYSTEM_PROMPT = """You are the fixed capability-v2 ReCAP controller for one public robot task.
Use only the capability tools supplied for this turn. Each capability tool is derived mechanically
from one sealed, IVC-passed capability and accepts that capability's native closed request object;
do not invent a task envelope, task_id, scene/reset input, task macro, private criterion, binding,
guard, reference input, or credential. Use the bounded public observations returned by tools to
plan the next physical action. You may call several tools in one turn when their order is clear.
Call finish after the necessary capability calls. finish records controller completion only: it is
never physical success, and the trusted Task Demo Harness alone issues the final verdict."""


_JSON_FALLBACK_SYSTEM_PROMPT = """You are the fixed capability-v2 ReCAP controller for one public robot task.
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

# Completed B2 evidence keeps its historical JSON prompt and response schema.
# The current mainline uses RECAP_SYSTEM_PROMPT and native tools.
RECAP_B2_SYSTEM_PROMPT = _JSON_FALLBACK_SYSTEM_PROMPT

_FINISH_TOOL_NAME = "finish"


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


class RecapModelClient(ToolModelClient, Protocol):
    """Canonical ReCAP client: the unified model client's native tool turn."""


class _LegacyRecapJsonModelClient(Protocol):
    """Compatibility edge for retained completed-B2 fixtures and focused tests."""

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


class _LegacyJsonToolClient:
    """Translate the retained focused JSON fake into native tool turns."""

    def __init__(self, client: _LegacyRecapJsonModelClient) -> None:
        self._client = client
        self._call_index = 0

    def generate_tool_turn(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> ToolTurn:
        del system_prompt, tools
        self._call_index += 1
        raw = self._client.generate_recap_json(
            stage=stage,
            system_prompt=_JSON_FALLBACK_SYSTEM_PROMPT,
            messages=messages,
            response_schema=RECAP_RESPONSE_SCHEMA,
        )
        try:
            output = _parse_model_output(raw, max_subtasks=8)
        except RecapControllerError as exc:
            return ToolTurn(
                content=f"Compatibility JSON output was invalid: {exc}",
                finish_reason="stop",
            )
        if not output["subtasks"]:
            arguments: dict[str, Any] = {}
            return ToolTurn(
                content=output["reasoning_summary"],
                tool_calls=(
                    ToolCall(
                        id=f"legacy-finish-{self._call_index}",
                        name=_FINISH_TOOL_NAME,
                        arguments=arguments,
                        raw_arguments="{}",
                    ),
                ),
                finish_reason="tool_calls",
            )
        head = output["subtasks"][0]
        if head["kind"] != "capability":
            return ToolTurn(
                content=output["reasoning_summary"],
                finish_reason="stop",
            )
        arguments = head["request"]
        return ToolTurn(
            content=output["reasoning_summary"],
            tool_calls=(
                ToolCall(
                    id=f"legacy-capability-{self._call_index}",
                    name=head["capability_name"],
                    arguments=arguments,
                    raw_arguments=json.dumps(arguments, sort_keys=True),
                ),
            ),
            finish_reason="tool_calls",
        )


def run_recap(
    *,
    public_task: Mapping[str, Any],
    adapter: "CapabilityAdapter",
    model: RecapModelClient | _LegacyRecapJsonModelClient,
    budgets: RecapBudgets | None = None,
    initial_public_state: Mapping[str, Any] | None = None,
) -> RecapControllerResult:
    """Run one bounded ReCAP Task Demo against one persistent live adapter.

    The current mainline is native tool use.  ``generate_recap_json`` remains
    only as a narrow compatibility edge for retained B2 fixtures; a client
    exposing both interfaces always takes the tool-use route.
    """

    tool_model: RecapModelClient
    if callable(getattr(model, "generate_tool_turn", None)):
        tool_model = model  # type: ignore[assignment]
    elif callable(getattr(model, "generate_recap_json", None)):
        tool_model = _LegacyJsonToolClient(model)  # type: ignore[arg-type,assignment]
    else:
        raise RecapControllerError(
            "ReCAP model must expose the unified generate_tool_turn interface"
        )
    return _run_tool_recap(
        public_task=public_task,
        adapter=adapter,
        model=tool_model,
        budgets=budgets,
        initial_public_state=initial_public_state,
    )


def _run_tool_recap(
    *,
    public_task: Mapping[str, Any],
    adapter: "CapabilityAdapter",
    model: RecapModelClient,
    budgets: RecapBudgets | None = None,
    initial_public_state: Mapping[str, Any] | None = None,
) -> RecapControllerResult:
    """Canonical dynamic-tool ReCAP path.

    ``adapter`` is created once around the Task Demo worker by the Harness
    session runner, so every call here acts on that same credential-free
    worker and MuJoCo state.  Only bounded public observations cross back into
    this parent-side model conversation.
    """

    from autoadapter2.b2.capability_adapter import (
        CapabilityAdapterError,
        CapabilityInvocationError,
    )

    fixed = budgets or RecapBudgets()
    task = _finite_json_object(public_task, label="public_task")
    _assert_public_payload(task, path="public_task")
    initial_state = (
        None
        if initial_public_state is None
        else _finite_json_object(
            initial_public_state, label="initial_public_state"
        )
    )
    if initial_state is not None:
        _assert_public_payload(initial_state, path="initial_public_state")

    catalog = adapter.public_catalog()
    _assert_public_payload(catalog, path="capability_catalog")
    capability_tools = _dynamic_capability_tools(catalog)
    capability_names = {
        tool["function"]["name"] for tool in capability_tools
    }
    finish_tool = {
        "type": "function",
        "function": {
            "name": _FINISH_TOOL_NAME,
            "description": (
                "End this controller trial after at least one real capability "
                "call. This does not claim or determine task success."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    }
    tools = [*capability_tools, finish_tool]
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": json.dumps(
                {
                    "public_task": task,
                    "robot_configuration_id": adapter.robot_configuration_id,
                    "capability_design_id": adapter.capability_design_id,
                    "initial_public_observations": initial_state,
                    "budgets": {
                        "planning_turns": fixed.max_planning_turns,
                        "capability_calls": fixed.max_capability_calls,
                    },
                },
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
    ]
    trace: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    capability_calls = 0
    invalid_outputs = 0

    for turn_index in range(1, fixed.max_planning_turns + 1):
        try:
            turn = model.generate_tool_turn(
                stage="task_demo_recap",
                system_prompt=RECAP_SYSTEM_PROMPT,
                messages=tuple(messages),
                tools=tuple(tools),
            )
        except Exception:
            trace.append(
                _trace_entry(
                    turn_index,
                    False,
                    capability_calls,
                    "model_error",
                    True,
                    "MODEL_ERROR",
                )
            )
            return _tool_result(
                "MODEL_ERROR",
                turn_index,
                capability_calls,
                invalid_outputs,
                trace,
                observations,
            )
        if not isinstance(turn, ToolTurn):
            trace.append(
                _trace_entry(
                    turn_index,
                    False,
                    capability_calls,
                    "invalid_model_turn",
                    True,
                    "NOT_EXECUTED",
                )
            )
            return _tool_result(
                "MODEL_ERROR",
                turn_index,
                capability_calls,
                invalid_outputs + 1,
                trace,
                observations,
            )

        messages.append(_tool_assistant_message(turn))
        if not turn.tool_calls:
            invalid_outputs += 1
            trace.append(
                _trace_entry(
                    turn_index,
                    True,
                    capability_calls,
                    "plan_without_tool",
                    True,
                    "NOT_EXECUTED",
                )
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "No tool was called. Continue with an exposed capability "
                        "tool, or call finish after at least one capability call."
                    ),
                }
            )
            continue

        for call in turn.tool_calls:
            if call.name == _FINISH_TOOL_NAME:
                error = call.argument_error
                if error is None and call.arguments is None:
                    error = "finish arguments must be one empty JSON object"
                if error is None and call.arguments:
                    error = "finish accepts no arguments"
                if error is None and capability_calls < 1:
                    error = "finish requires at least one capability call"
                if error is not None:
                    invalid_outputs += 1
                    _append_tool_observation(
                        messages,
                        call.id,
                        ok=False,
                        payload={"error": error},
                    )
                    trace.append(
                        _trace_entry(
                            turn_index,
                            True,
                            capability_calls,
                            "finish",
                            True,
                            "NOT_EXECUTED",
                        )
                    )
                    continue
                _append_tool_observation(
                    messages,
                    call.id,
                    ok=True,
                    payload={"status": "CONTROLLER_FINISHED"},
                )
                trace.append(
                    _trace_entry(
                        turn_index,
                        True,
                        capability_calls,
                        "finish",
                        False,
                        "NOT_APPLICABLE",
                        controller_self_reported_completion=True,
                    )
                )
                return _tool_result(
                    "CONTROLLER_FINISHED",
                    turn_index,
                    capability_calls,
                    invalid_outputs,
                    trace,
                    observations,
                    completed=True,
                )

            if call.name not in capability_names:
                invalid_outputs += 1
                _append_tool_observation(
                    messages,
                    call.id,
                    ok=False,
                    payload={"error": f"unknown tool: {call.name}"},
                )
                trace.append(
                    _trace_entry(
                        turn_index,
                        False,
                        capability_calls,
                        "unknown_tool",
                        True,
                        "NOT_EXECUTED",
                        capability_name=call.name,
                    )
                )
                continue

            error = call.argument_error
            validated: dict[str, Any] | None = None
            if error is None and call.arguments is None:
                error = "capability arguments must be one native request object"
            if error is None and call.arguments is not None:
                try:
                    validated = adapter.validate_request(call.name, call.arguments)
                except CapabilityAdapterError as exc:
                    error = str(exc)
            if error is not None or validated is None:
                invalid_outputs += 1
                _append_tool_observation(
                    messages,
                    call.id,
                    ok=False,
                    payload={"error": error or "invalid capability request"},
                )
                trace.append(
                    _trace_entry(
                        turn_index,
                        True,
                        capability_calls,
                        "capability",
                        True,
                        "NOT_EXECUTED",
                        capability_name=call.name,
                        public_arguments=call.arguments,
                        argument_binding_valid=False,
                    )
                )
                continue

            if capability_calls >= fixed.max_capability_calls:
                _append_tool_observation(
                    messages,
                    call.id,
                    ok=False,
                    payload={"error": "capability-call budget is exhausted"},
                )
                trace.append(
                    _trace_entry(
                        turn_index,
                        True,
                        capability_calls,
                        "capability",
                        False,
                        "CALL_BUDGET_EXHAUSTED",
                        capability_name=call.name,
                        public_arguments=validated,
                        argument_binding_valid=True,
                    )
                )
                return _tool_result(
                    "CAPABILITY_CALL_BUDGET_EXHAUSTED",
                    turn_index,
                    capability_calls,
                    invalid_outputs,
                    trace,
                    observations,
                )

            capability_calls += 1
            try:
                observation = adapter.execute(call.name, validated)
                _assert_public_payload(
                    observation, path="public_operation_observation"
                )
                outcome = _operation_outcome(observation)
            except CapabilityInvocationError:
                observation = {
                    "status": "CAPABILITY_EXECUTION_ERROR",
                    "message": "The valid capability call did not complete normally.",
                }
                outcome = "EXECUTION_ERROR"
            except RecapControllerError:
                observation = {
                    "status": "PUBLIC_OBSERVATION_REJECTED",
                    "message": (
                        "The worker response violated the public feedback boundary."
                    ),
                }
                outcome = "PUBLIC_OBSERVATION_REJECTED"

            record = {
                "kind": "capability_observation",
                "status": outcome,
                "capability_name": call.name,
                "public_observation": observation,
            }
            observations.append(record)
            _append_tool_observation(
                messages,
                call.id,
                ok=outcome == "EXECUTED",
                payload={
                    "outcome": outcome,
                    "public_observation": observation,
                    "capability_calls_remaining": (
                        fixed.max_capability_calls - capability_calls
                    ),
                },
            )
            trace.append(
                _trace_entry(
                    turn_index,
                    True,
                    capability_calls,
                    "capability",
                    False,
                    outcome,
                    capability_name=call.name,
                    public_arguments=validated,
                    argument_binding_valid=True,
                )
            )
            if outcome == "WORKER_ABORTED":
                return _tool_result(
                    "WORKER_ABORTED",
                    turn_index,
                    capability_calls,
                    invalid_outputs,
                    trace,
                    observations,
                )

    return _tool_result(
        "PLANNING_TURN_BUDGET_EXHAUSTED",
        fixed.max_planning_turns,
        capability_calls,
        invalid_outputs,
        trace,
        observations,
    )


_FORBIDDEN_CAPABILITY_REQUEST_FIELDS = {
    "criteria",
    "criterion",
    "oracle_plan",
    "private_criteria",
    "reset",
    "reset_state",
    "scene",
    "scene_id",
    "task",
    "task_id",
    "task_macro",
    "task_parameters",
    "task_request",
    "waypoint_plan",
}


def _dynamic_capability_tools(
    catalog: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Derive the exact model tools from the adapter's sealed catalogue."""

    if not catalog:
        raise RecapControllerError(
            "ReCAP requires at least one nominal+boundary-passed capability"
        )
    tools: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, value in enumerate(catalog):
        if not isinstance(value, Mapping):
            raise RecapControllerError(f"capability_catalog[{index}] must be an object")
        name = value.get("method_name")
        description = value.get("description")
        schema = value.get("request_schema")
        if (
            not isinstance(name, str)
            or not name.isidentifier()
            or name == _FINISH_TOOL_NAME
        ):
            raise RecapControllerError(
                f"capability_catalog[{index}] has an invalid or reserved method_name"
            )
        if name in names:
            raise RecapControllerError(f"duplicate ReCAP capability tool {name!r}")
        if not isinstance(description, str) or not description.strip():
            raise RecapControllerError(
                f"capability_catalog[{index}] has no public description"
            )
        if not isinstance(schema, Mapping):
            raise RecapControllerError(f"{name} has no public request_schema")
        schema_copy = _finite_json_object(schema, label=f"{name} request_schema")
        if (
            schema_copy.get("type") != "object"
            or schema_copy.get("additionalProperties") is not False
        ):
            raise RecapControllerError(f"{name} request_schema must be a closed object")
        _assert_task_neutral_request_schema(schema_copy, path=f"{name}.request_schema")
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description.strip(),
                    "parameters": schema_copy,
                },
            }
        )
        names.add(name)
    return tools


def _assert_task_neutral_request_schema(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        properties = value.get("properties")
        if isinstance(properties, Mapping):
            for field in properties:
                normalized = str(field).strip().lower().replace("-", "_")
                if normalized in _FORBIDDEN_CAPABILITY_REQUEST_FIELDS:
                    raise RecapControllerError(
                        f"{path} contains forbidden request field {field!r}"
                    )
        for key, child in value.items():
            _assert_task_neutral_request_schema(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_task_neutral_request_schema(child, path=f"{path}[{index}]")


def _tool_assistant_message(turn: ToolTurn) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": turn.content}
    if turn.reasoning_content:
        message["reasoning_content"] = turn.reasoning_content
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.raw_arguments,
                },
            }
            for call in turn.tool_calls
        ]
    return message


def _append_tool_observation(
    messages: list[dict[str, Any]],
    call_id: str,
    *,
    ok: bool,
    payload: Mapping[str, Any],
) -> None:
    envelope = {"ok": ok, **dict(payload)}
    try:
        content = json.dumps(
            envelope,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        content = json.dumps(
            {"ok": False, "error": "tool observation was not finite JSON"},
            separators=(",", ":"),
            sort_keys=True,
        )
    limit = 4_096
    if len(content) > limit:
        content = json.dumps(
            {
                "ok": ok,
                "truncated": True,
                "preview": content[: limit - 128],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    messages.append({"role": "tool", "tool_call_id": call_id, "content": content})


def _tool_result(
    status: str,
    planning_turns: int,
    capability_calls: int,
    invalid_outputs: int,
    trace: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    *,
    completed: bool = False,
) -> RecapControllerResult:
    tree = {
        "root_node_id": "n0",
        "active_node_id": "n0",
        "nodes": [
            {
                "node_id": "n0",
                "description": "Native dynamic-tool ReCAP controller",
                "parent_id": None,
                "depth": 0,
                "children": [],
                "remaining_plan": [],
                "observations": observations,
                "revisions": [],
                "completed": completed,
            }
        ],
    }
    return RecapControllerResult(
        status=status,
        planning_turns=planning_turns,
        capability_calls=capability_calls,
        invalid_outputs=invalid_outputs,
        trace=tuple(
            _finite_json_object(item, label="trace entry") for item in trace
        ),
        context_tree=_finite_json_object(tree, label="context tree"),
    )


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
