from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from autoadapter2.react import ToolCall, ToolTurn
from autoadapter2.task_demo import run_task_demo_react


class ScriptedToolClient:
    def __init__(self, turns: Sequence[ToolTurn]) -> None:
        self._turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    def generate_tool_turn(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> ToolTurn:
        self.calls.append(
            {
                "stage": stage,
                "system_prompt": system_prompt,
                "messages": [dict(item) for item in messages],
                "tools": [dict(item) for item in tools],
            }
        )
        return self._turns.pop(0)


def _task(task_id: str, name: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "name": name,
        "description": f"Execute the public {name} objective.",
        "invocation_schema": {
            "request": {
                "task_parameters": {
                    "required": ["target_position"],
                    "properties": {
                        "target_position": {
                            "type": "array",
                            "items": {"type": "number"},
                            "length": 3,
                            "unit": "m",
                            "frame": "world",
                            "description": "Public task target.",
                        }
                    },
                }
            }
        },
    }


def _design() -> dict[str, Any]:
    return {
        "capabilities": [
            {
                "capability_id": "cap-push",
                "method_name": "push_object",
                "description": "Push a contacted object to a public target.",
                "covered_task_ids": ["task-push"],
            },
            {
                "capability_id": "cap-reach",
                "method_name": "reach_target",
                "description": "Move the end effector to a public target.",
                "covered_task_ids": ["task-reach"],
            },
        ]
    }


def _call(call_id: str, name: str, arguments: Mapping[str, Any]) -> ToolCall:
    raw = json.dumps(dict(arguments), sort_keys=True)
    return ToolCall(call_id, name, dict(arguments), raw)


def test_react_selects_the_covering_capability_and_finishes_without_issuing_a_verdict() -> None:
    public_arguments = {
        "request": {
            "task_id": "task-reach",
            "task_parameters": {"target_position": [0.4, 0.1, 0.3]},
        }
    }
    client = ScriptedToolClient(
        (
            ToolTurn(
                None,
                (_call("wrong", "push_object", public_arguments),),
                "tool_calls",
            ),
            ToolTurn(
                None,
                (_call("right", "reach_target", public_arguments),),
                "tool_calls",
            ),
            ToolTurn(
                None,
                (_call("finish", "finish_task_demo", {}),),
                "tool_calls",
            ),
        )
    )
    invocations: list[tuple[str, dict[str, Any]]] = []

    def invoke(method_name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        invocations.append((method_name, dict(arguments)))
        return {"status": "OK", "sim_step_count": 12}

    result = run_task_demo_react(
        client=client,
        robot_configuration_id="robot-arm",
        tasks=(_task("task-push", "push"), _task("task-reach", "reach")),
        design=_design(),
        task_id="task-reach",
        public_arguments=public_arguments,
        invoke=invoke,
    )

    assert result.status == "FINISHED"
    assert result.model_turns == 3
    assert result.capability_calls == 2
    assert invocations == [("reach_target", public_arguments)]
    assert all(call["stage"] == "task_demo_controller" for call in client.calls)
    first_names = [tool["function"]["name"] for tool in client.calls[0]["tools"]]
    assert first_names == ["push_object", "reach_target"]
    final_names = [tool["function"]["name"] for tool in client.calls[-1]["tools"]]
    assert final_names == ["push_object", "reach_target", "finish_task_demo"]
    serialized = json.dumps(client.calls, sort_keys=True)
    assert "private_criterion" not in serialized
    assert "threshold" not in serialized
    assert "PASS" not in json.dumps(result.trace, sort_keys=True)


def test_react_rejects_modified_public_arguments_before_the_worker_invocation() -> None:
    public_arguments = {
        "request": {
            "task_id": "task-reach",
            "task_parameters": {"target_position": [0.4, 0.1, 0.3]},
        }
    }
    changed_arguments = {
        "request": {
            "task_id": "task-reach",
            "task_parameters": {"target_position": [9.0, 9.0, 9.0]},
        }
    }
    client = ScriptedToolClient(
        (
            ToolTurn(
                None,
                (_call("changed", "reach_target", changed_arguments),),
                "tool_calls",
            ),
            ToolTurn(
                None,
                (_call("right", "reach_target", public_arguments),),
                "tool_calls",
            ),
            ToolTurn(
                None,
                (_call("finish", "finish_task_demo", {}),),
                "tool_calls",
            ),
        )
    )
    invocations: list[dict[str, Any]] = []

    result = run_task_demo_react(
        client=client,
        robot_configuration_id="robot-arm",
        tasks=(_task("task-push", "push"), _task("task-reach", "reach")),
        design=_design(),
        task_id="task-reach",
        public_arguments=public_arguments,
        invoke=lambda _name, arguments: (
            invocations.append(dict(arguments)) or {"status": "OK", "sim_step_count": 12}
        ),
    )

    assert result.capability_calls == 2
    assert invocations == [public_arguments]
    rejected = result.trace[0]
    assert rejected["ok"] is False
    assert "must equal the supplied public request" in rejected["observation"]
