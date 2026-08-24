from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import pytest

from autoadapter2.b2.capability_adapter import CapabilityAdapter
from autoadapter2.capability_design import (
    CAPABILITY_INVOCATION_ABI,
    CAPABILITY_PROTOCOL_VERSION,
)
from autoadapter2.react import ToolCall, ToolTurn
from autoadapter2.task_demo.recap import (
    RecapBudgets,
    RecapControllerError,
    run_recap,
)


def _schema(field: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            field: {
                "type": "number",
                "unit": "m",
                "frame": "world",
                "minimum": -1.0,
                "maximum": 1.0,
                "evidence_refs": [
                    {
                        "source_id": "public-calibration",
                        "specific_reference": f"bound for {field}",
                    }
                ],
            }
        },
        "required": [field],
        "additionalProperties": False,
    }


def _design(*, fields: tuple[str, ...] = ("amount",)) -> dict[str, Any]:
    return {
        "artifact_type": "capability_design",
        "schema_version": "2.0",
        "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
        "robot_configuration_id": "test-robot",
        "package_version": "1.0.0",
        "task_snapshot_id": "tasks-v1",
        "invocation_abi": CAPABILITY_INVOCATION_ABI,
        "capabilities": [
            {
                "capability_id": f"C{index}",
                "method_name": f"apply_effect_{index}",
                "description": f"Apply public physical effect {index}.",
                "effect": f"effect_{index}",
                "request_schema": _schema(field),
            }
            for index, field in enumerate(fields, start=1)
        ],
        "task_support": [],
    }


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> ToolCall:
    return ToolCall(
        id=call_id,
        name=name,
        arguments=arguments,
        raw_arguments=json.dumps(arguments, sort_keys=True),
    )


class _NativeToolClient:
    def __init__(self, turns: list[ToolTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []
        self.json_fallback_calls = 0

    def generate_tool_turn(self, **kwargs: Any) -> ToolTurn:
        self.calls.append(json.loads(json.dumps(kwargs)))
        return self.turns.pop(0)

    def generate_recap_json(self, **_: Any) -> dict[str, Any]:
        self.json_fallback_calls += 1
        raise AssertionError("the canonical path must prefer native tool use")


def test_native_recap_exposes_only_whitelisted_capability_tools_and_finish() -> None:
    invocations: list[tuple[str, dict[str, Any]]] = []
    client = _NativeToolClient(
        [
            ToolTurn(
                content=None,
                tool_calls=(
                    _call("cap-1", "apply_effect_1", {"amount": 0.2}),
                    _call("finish-1", "finish", {}),
                ),
                finish_reason="tool_calls",
            )
        ]
    )
    adapter = CapabilityAdapter(
        _design(),
        lambda method, request: (
            invocations.append((method, dict(request)))
            or {
                "operation": {"status": "EXECUTED"},
                "public_state": {"sim_time_s": 0.5},
            }
        ),
    )

    result = run_recap(
        public_task={"objective": "Apply the public effect."},
        adapter=adapter,
        model=client,
        initial_public_state={"sim_time_s": 0.0, "joint_positions": [0.0]},
    )

    assert result.status == "CONTROLLER_FINISHED"
    assert result.planning_turns == 1
    assert result.capability_calls == 1
    assert client.json_fallback_calls == 0
    assert invocations == [("apply_effect_1", {"request": {"amount": 0.2}})]

    tool_functions = [tool["function"] for tool in client.calls[0]["tools"]]
    assert [tool["name"] for tool in tool_functions] == ["apply_effect_1", "finish"]
    capability_schema = tool_functions[0]["parameters"]
    assert set(capability_schema["properties"]) == {"amount"}
    assert "request" not in capability_schema["properties"]
    assert capability_schema["additionalProperties"] is False
    assert tool_functions[1]["parameters"] == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    first_public_input = json.loads(client.calls[0]["messages"][0]["content"])
    assert first_public_input["initial_public_observations"]["sim_time_s"] == 0.0

    serialized_result = json.dumps(asdict(result), sort_keys=True)
    assert "harness_verdict" not in serialized_result
    assert "validation_passed" not in serialized_result
    assert result.trace[-1]["controller_self_reported_completion"] is True


def test_native_recap_enforces_exact_16_turn_12_capability_call_budgets() -> None:
    turns = [
        ToolTurn(
            content=None,
            tool_calls=(_call(f"cap-{index}", "apply_effect_1", {"amount": 0.1}),),
            finish_reason="tool_calls",
        )
        for index in range(13)
    ]
    client = _NativeToolClient(turns)
    invocations: list[dict[str, Any]] = []
    result = run_recap(
        public_task={"objective": "Exercise the bounded capability."},
        adapter=CapabilityAdapter(
            _design(),
            lambda _method, request: (
                invocations.append(dict(request))
                or {"operation": {"status": "EXECUTED"}}
            ),
        ),
        model=client,
    )

    assert RecapBudgets().max_planning_turns == 16
    assert RecapBudgets().max_capability_calls == 12
    assert result.status == "CAPABILITY_CALL_BUDGET_EXHAUSTED"
    assert result.planning_turns == 13
    assert result.capability_calls == 12
    assert len(invocations) == 12


def test_native_recap_rejects_task_dispatch_fields_in_dynamic_schema() -> None:
    client = _NativeToolClient([])

    class UnsafeAdapter:
        robot_configuration_id = "test-robot"
        capability_design_id = "unsafe-design"

        def public_catalog(self) -> list[dict[str, Any]]:
            return [
                {
                    "method_name": "unsafe_dispatch",
                    "description": "An interface that must not reach ReCAP.",
                    "request_schema": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                        "additionalProperties": False,
                    },
                }
            ]

    with pytest.raises(RecapControllerError, match="forbidden request field"):
        run_recap(
            public_task={"objective": "Do not expose dispatch."},
            adapter=UnsafeAdapter(),  # type: ignore[arg-type]
            model=client,
        )


def test_native_recap_returns_sanitized_worker_error_to_same_conversation() -> None:
    client = _NativeToolClient(
        [
            ToolTurn(
                content=None,
                tool_calls=(_call("cap-1", "apply_effect_1", {"amount": 0.2}),),
                finish_reason="tool_calls",
            ),
            ToolTurn(
                content=None,
                tool_calls=(_call("finish-1", "finish", {}),),
                finish_reason="tool_calls",
            ),
        ]
    )

    def fail_with_private_details(*_: Any) -> dict[str, Any]:
        raise RuntimeError("credential=secret private traceback")

    result = run_recap(
        public_task={"objective": "Attempt one public operation."},
        adapter=CapabilityAdapter(_design(), fail_with_private_details),
        model=client,
    )

    assert result.status == "CONTROLLER_FINISHED"
    assert result.capability_calls == 1
    second_turn_context = json.dumps(client.calls[1]["messages"]).lower()
    assert "credential=secret" not in second_turn_context
    assert "private traceback" not in second_turn_context
    assert "capability_execution_error" in second_turn_context
