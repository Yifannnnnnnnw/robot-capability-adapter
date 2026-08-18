"""Small, bounded ReAct loop used by Demo3 model-authored stages."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class ReactLoopError(RuntimeError):
    """Raised when a bounded ReAct phase ends without a valid submission."""


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

    for turn_number in range(1, max_turns + 1):
        turn = client.generate_tool_turn(
            stage=stage,
            system_prompt=system_prompt,
            messages=messages,
            tools=model_tools,
        )
        messages.append(_assistant_message(turn))
        if not turn.tool_calls:
            trace.append(
                {
                    "turn": turn_number,
                    "assistant": turn.content or "",
                    "finish_reason": turn.finish_reason,
                    "event": "submission_required",
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "No artifact was submitted. Continue working and finish by calling "
                        "the phase submission tool."
                    ),
                }
            )
            continue

        for call in turn.tool_calls:
            call_count += 1
            if call_count > max_tool_calls:
                raise ReactLoopError(
                    f"{stage} exceeded its {max_tool_calls}-tool-call limit without submission"
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

            envelope = (
                {"ok": False, "error": error}
                if error is not None
                else {"ok": True, "result": result}
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

    raise ReactLoopError(f"{stage} reached {max_turns} model turns without submission")
