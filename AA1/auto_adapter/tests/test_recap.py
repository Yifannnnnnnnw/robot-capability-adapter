"""Focused recursion and bounded execution checks; adapters/models here are test doubles."""
import json
import os
from pathlib import Path
import subprocess
import sys
from dataclasses import asdict

import pytest

from auto_adapter.agent.recap import (
    CapabilityAdapterError, RecapBudgets, ToolCall, ToolTurn, run_recap,
)


def _leaf(amount):
    return {"kind": "capability", "capability_name": "move", "request": {"amount": amount}}


def _subtask(description):
    return {"kind": "subtask", "description": description}


def _turn(subtasks, call_id="plan", name="submit_plan"):
    arguments = {"reasoning_summary": "Revise the next actions.", "subtasks": subtasks}
    return ToolTurn(None, (ToolCall(call_id, name, arguments, json.dumps(arguments)),))


class _Adapter:
    robot_configuration_id = "test-robot"
    capability_design_id = "test-design"

    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = iter(outcomes or [])

    def public_catalog(self):
        return [{"method_name": "move", "description": "Move the test actuator.",
                 "request_schema": {"type": "object", "properties": {"amount": {"type": "integer"}},
                                    "required": ["amount"], "additionalProperties": False}}]

    def validate_request(self, name, request):
        if name != "move" or set(request) != {"amount"} or type(request["amount"]) is not int:
            raise CapabilityAdapterError("expected move with one integer amount")
        return dict(request)

    def execute(self, name, request):
        self.calls.append((name, request))
        return {"operation": {"status": next(self.outcomes, "EXECUTED")},
                "observations": {"position": request["amount"]}}


class _Model:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.contexts = []
        self.messages = []

    def generate_tool_turn(self, **kwargs):
        assert [tool["function"]["name"] for tool in kwargs["tools"]] == ["submit_plan"]
        parameters = kwargs["tools"][0]["function"]["parameters"]
        leaf_schema = parameters["properties"]["subtasks"]["items"]["oneOf"][1]
        assert leaf_schema["properties"]["capability_name"]["const"] == "move"
        assert leaf_schema["properties"]["request"]["required"] == ["amount"]
        self.messages.append(json.loads(json.dumps(kwargs["messages"])))
        self.contexts.append(json.loads(kwargs["messages"][-1]["content"]))
        return next(self.turns)


def test_recursive_return_revises_siblings_and_shares_latest_observation():
    adapter = _Adapter()
    model = _Model([_turn([_leaf(0), _subtask("old group")]),
                    _turn([_subtask("group"), _leaf(99)]),
                    _turn([_subtask("nested action")]),
                    _turn([_leaf(1), _leaf(99)]), _turn([]), _turn([]),
                    _turn([_leaf(2)]), _turn([])])
    result = run_recap(public_task={"description": "Reach positions 0, 1, 2."},
                       adapter=adapter, model=model, initial_public_state={"position": -1})
    assert result.status == "CONTROLLER_FINISHED"
    assert result.planning_turns == 8 and result.capability_calls == 3
    assert [request["amount"] for _, request in adapter.calls] == [0, 1, 2]
    root, child, nested = result.context_tree["nodes"]
    assert root["children"] == ["n1"] and child["children"] == ["n2"]
    assert nested["parent_id"] == "n1" and nested["depth"] == 2
    assert all(node["completed"] for node in [root, child, nested])
    assert model.contexts[3]["latest_public_observation"]["observations"]["position"] == 0
    assert model.contexts[5]["latest_public_observation"]["observations"]["position"] == 1
    assert model.contexts[6]["previous_remaining_plan"] == [_leaf(99)]
    assert len(root["revisions"]) == 4
    assert "physical_task_success" not in json.dumps(asdict(result))


@pytest.mark.parametrize("bad", [
    _turn([]),
    _turn([_leaf("bad")]),
    _turn([_leaf(1), {"kind": "capability", "capability_name": "missing", "request": {}}]),
    _turn([_leaf(1)], name="move"),
])
def test_invalid_plan_cannot_execute_or_finish(bad):
    adapter = _Adapter()
    result = run_recap(public_task={}, adapter=adapter, model=_Model([bad]),
                       budgets=RecapBudgets(max_invalid_outputs=1))
    assert result.status == "INVALID_OUTPUT_BUDGET_EXHAUSTED"
    assert result.invalid_outputs == 1 and not adapter.calls
    assert not result.context_tree["nodes"][0]["completed"]


@pytest.mark.parametrize("budgets,turns,status,calls", [
    (RecapBudgets(max_planning_turns=2), [_turn([_subtask("child")]), _turn([_leaf(1)])],
     "PLANNING_TURN_BUDGET_EXHAUSTED", 1),
    (RecapBudgets(max_capability_calls=1),
     [_turn([_subtask("child")]), _turn([_leaf(1)]), _turn([]), _turn([_leaf(2)])],
     "CAPABILITY_CALL_BUDGET_EXHAUSTED", 1),
    (RecapBudgets(max_depth=1, max_invalid_outputs=1),
     [_turn([_subtask("child")]), _turn([_subtask("too deep")])],
     "INVALID_OUTPUT_BUDGET_EXHAUSTED", 0),
])
def test_budgets_are_shared_by_all_nodes(budgets, turns, status, calls):
    adapter = _Adapter()
    result = run_recap(public_task={}, adapter=adapter, model=_Model(turns), budgets=budgets)
    assert result.status == status and len(adapter.calls) == calls
    assert result.planning_turns == len(turns)
    assert not result.context_tree["nodes"][0]["completed"]


def test_failed_operation_returns_observation_and_does_not_allow_empty_completion():
    adapter = _Adapter(["ERROR", "EXECUTED"])
    model = _Model([_turn([_leaf(1)]), _turn([]), _turn([_leaf(2)]), _turn([])])
    result = run_recap(public_task={}, adapter=adapter, model=model)
    assert result.status == "CONTROLLER_FINISHED" and result.capability_calls == 2
    assert result.invalid_outputs == 1
    assert model.contexts[1]["latest_public_observation"]["operation"]["status"] == "ERROR"


def test_unexpected_failures_report_cause_without_completion():
    class DisconnectedModel:
        def generate_tool_turn(self, **kwargs):
            raise RuntimeError("transport disconnected")

    model_error = run_recap(public_task={}, adapter=_Adapter(), model=DisconnectedModel())
    assert model_error.status == "MODEL_ERROR" and model_error.capability_calls == 0
    assert model_error.trace[-1]["error_type"] == "RuntimeError"
    assert model_error.trace[-1]["message"] == "transport disconnected"

    class BrokenAdapter(_Adapter):
        def execute(self, name, request):
            raise RuntimeError("lost simulation")

    runtime_error = run_recap(public_task={}, adapter=BrokenAdapter(), model=_Model([_turn([_leaf(1)])]))
    assert runtime_error.status == "RUNTIME_ERROR" and runtime_error.capability_calls == 1
    assert runtime_error.trace[-1]["feedback"]["message"] == "lost simulation"
    assert not runtime_error.context_tree["nodes"][0]["completed"]


def test_malformed_multi_tool_turn_gets_every_reply_and_executes_nothing():
    adapter = _Adapter()
    malformed = ToolTurn(None, (_turn([_leaf(99)], "first").tool_calls[0],
                                _turn([_leaf(99)], "second").tool_calls[0]))
    model = _Model([malformed, _turn([_leaf(1)]), _turn([])])
    result = run_recap(public_task={}, adapter=adapter, model=model)
    assert result.status == "CONTROLLER_FINISHED" and len(adapter.calls) == 1
    replies = [message for message in model.messages[1] if message["role"] == "tool"]
    assert [reply["tool_call_id"] for reply in replies] == ["first", "second"]
    assert all(json.loads(reply["content"])["status"] == "INVALID_PLAN" for reply in replies)


def test_history_trimming_keeps_complete_tool_exchanges():
    adapter = _Adapter()
    model = _Model([_turn([_leaf(i)], f"p{i}") for i in range(8)] + [_turn([], "done")])
    result = run_recap(public_task={}, adapter=adapter, model=model,
                       budgets=RecapBudgets(max_history_chars=3300))
    assert result.status == "CONTROLLER_FINISHED"
    assert not any(call["id"] == "p0" for message in model.messages[-1]
                   for call in message.get("tool_calls", []))
    for messages in model.messages:
        pending = []
        for message in messages:
            if message["role"] == "assistant":
                assert not pending
                pending = [call["id"] for call in message.get("tool_calls", [])]
            elif message["role"] == "tool":
                assert message["tool_call_id"] == pending.pop(0)
            else:
                assert not pending
        assert not pending


def test_native_controller_runs_with_old_package_import_blocked(tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = """
import sys
from importlib.abc import MetaPathFinder
class BlockOldPackage(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] == 'autoadapter2':
            raise ImportError('old package deliberately unavailable')
sys.meta_path.insert(0, BlockOldPackage())
from auto_adapter.tests.test_recap import test_recursive_return_revises_siblings_and_shares_latest_observation
test_recursive_return_revises_siblings_and_shares_latest_observation()
assert not any(name.split('.')[0] == 'autoadapter2' for name in sys.modules)
"""
    completed = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                               env={**os.environ, "PYTHONPATH": str(root)},
                               text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
