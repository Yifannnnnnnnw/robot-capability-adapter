"""Small, bounded ReAct loop used by Demo3 model-authored stages."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class ReactLoopError(RuntimeError):
    """Raised when a bounded ReAct phase ends without a valid submission."""

    def __init__(
        self,
        message: str,
        *,
        trace: Sequence[Mapping[str, Any]] = (),
        model_turns: int = 0,
        tool_calls: int = 0,
    ) -> None:
        super().__init__(message)
        self.trace = tuple(dict(item) for item in trace)
        self.model_turns = int(model_turns)
        self.tool_calls = int(tool_calls)


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
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> ToolTurn: ...


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: Callable[[Mapping[str, Any]], Any]
    terminal: bool = False

    def model_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.input_schema),
            },
        }


@dataclass(frozen=True)
class ReactResult:
    submission: Any
    trace: tuple[dict[str, Any], ...]
    model_turns: int
    tool_calls: int
    submitted_with: str


def _bounded_text(value: Any, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError):
        text = json.dumps(str(value), ensure_ascii=True)
    if len(text) <= limit:
        return text
    suffix = "...[truncated]"
    return text[: limit - len(suffix)] + suffix


def _assistant_message(turn: ToolTurn) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": turn.content,
    }
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


def _append_user_instruction(
    messages: list[dict[str, Any]], instruction: str
) -> None:
    """Add a model-facing instruction without creating adjacent user turns."""

    if messages and messages[-1].get("role") == "user" and isinstance(
        messages[-1].get("content"), str
    ):
        messages[-1] = {
            **messages[-1],
            "content": f"{messages[-1]['content']}\n\n{instruction}",
        }
        return
    messages.append({"role": "user", "content": instruction})


def run_react(
    *,
    client: ToolModelClient,
    stage: str,
    system_prompt: str,
    user_prompt: str,
    tools: Sequence[ToolSpec],
    max_turns: int = 16,
    max_tool_calls: int = 48,
    tool_output_chars: int = 24000,
) -> ReactResult:
    """Run one model conversation until a terminal tool accepts an artifact."""

    if max_turns < 1 or max_tool_calls < 1 or tool_output_chars < 256:
        raise ValueError("ReAct limits must be positive and tool output at least 256 chars")
    tool_map = {tool.name: tool for tool in tools}
    if len(tool_map) != len(tools):
        raise ValueError("ReAct tool names must be unique")
    if not any(tool.terminal for tool in tools):
        raise ValueError("ReAct phase requires at least one terminal submission tool")

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    trace: list[dict[str, Any]] = []
    call_count = 0
    model_tools = [tool.model_definition() for tool in tools]
    terminal_tools = [tool for tool in tools if tool.terminal]
    terminal_model_tools = [tool.model_definition() for tool in terminal_tools]
    terminal_names = [tool.name for tool in terminal_tools]
    convergence_warned = False

    for turn_number in range(1, max_turns + 1):
        final_turn = turn_number == max_turns
        tools_for_turn = model_tools
        if final_turn:
            instruction = (
                "This is the reserved final submission turn. Development tools are no "
                f"longer available. Call {', '.join(terminal_names)} now with the current "
                "artifact; if submission is rejected, this phase ends."
            )
            _append_user_instruction(messages, instruction)
            trace.append(
                {
                    "turn": turn_number,
                    "event": "final_submission_turn",
                    "submission_tools": terminal_names,
                }
            )
            tools_for_turn = terminal_model_tools
        turn = client.generate_tool_turn(
            stage=stage,
            system_prompt=system_prompt,
            messages=messages,
            tools=tools_for_turn,
        )
        messages.append(_assistant_message(turn))
        if not turn.tool_calls:
            remaining_turns = max_turns - turn_number
            trace.append(
                {
                    "turn": turn_number,
                    "assistant": turn.content or "",
                    "finish_reason": turn.finish_reason,
                    "event": "submission_required",
                }
            )
            _append_user_instruction(
                messages,
                f"No artifact was submitted. {remaining_turns} model turns remain. "
                "Continue only the work required for acceptance and finish by calling "
                f"{', '.join(terminal_names)}.",
            )
            continue

        for call in turn.tool_calls:
            call_count += 1
            if call_count > max_tool_calls:
                raise ReactLoopError(
                    f"{stage} exceeded its {max_tool_calls}-tool-call limit without submission",
                    trace=trace,
                    model_turns=turn_number,
                    tool_calls=call_count,
                )

            tool = tool_map.get(call.name)
            result: Any = None
            error: str | None = call.argument_error
            if tool is None:
                error = f"unknown tool: {call.name}"
            elif call.arguments is None and error is None:
                error = "tool arguments must be one JSON object"
            if error is None and tool is not None and call.arguments is not None:
                try:
                    result = tool.handler(call.arguments)
                except Exception as exc:  # Tool failures are observations for the model.
                    error = f"{type(exc).__name__}: {exc}"

            budget_status = {
                "model_turn": turn_number,
                "model_turns_remaining": max_turns - turn_number,
                "tool_calls_used": call_count,
                "tool_calls_remaining": max_tool_calls - call_count,
            }
            envelope = (
                {"ok": False, "error": error, "budget": budget_status}
                if error is not None
                else {"ok": True, "result": result, "budget": budget_status}
            )
            observation = _bounded_text(envelope, tool_output_chars)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": observation,
                }
            )
            trace.append(
                {
                    "turn": turn_number,
                    "tool_call_id": call.id,
                    "tool": call.name,
                    "arguments": _bounded_text(call.raw_arguments, 4000),
                    "ok": error is None,
                    "observation": observation,
                }
            )
            if tool is not None and tool.terminal and error is None:
                return ReactResult(
                    submission=result,
                    trace=tuple(trace),
                    model_turns=turn_number,
                    tool_calls=call_count,
                    submitted_with=tool.name,
                )

        remaining_turns = max_turns - turn_number
        if (
            not convergence_warned
            and 0 < remaining_turns <= max(4, max_turns // 4)
        ):
            convergence_warned = True
            reminder = {
                "turn": turn_number,
                "event": "convergence_required",
                "model_turns_remaining": remaining_turns,
                "tool_calls_remaining": max_tool_calls - call_count,
                "submission_tools": terminal_names,
            }
            trace.append(reminder)
            _append_user_instruction(
                messages,
                f"Budget warning: {remaining_turns} model turns and "
                f"{max_tool_calls - call_count} tool calls remain. Stop optional "
                "exploration. Preserve the current viable revision, complete only its "
                "required checks, and call "
                f"{', '.join(terminal_names)} as soon as its acceptance conditions hold.",
            )

    raise ReactLoopError(
        f"{stage} reached {max_turns} model turns without submission",
        trace=trace,
        model_turns=max_turns,
        tool_calls=call_count,
    )
