"""Focused official-generator checks; models and adapters here are named fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from auto_adapter.agent.recap import CapabilityAdapterError, RecapBudgets, run_recap, upstream


def _leaf(amount, name="move"):
    return json.dumps({"capability_name": name, "request": {"amount": amount}})


def _plan(subtasks, summary="Revise the next actions."):
    return json.dumps({"think": summary, "subtasks": subtasks})


class _Adapter:
    def __init__(self, outcomes=()):
        self.calls = []
        self.outcomes = iter(outcomes)

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
        self.messages = []

    def generate_json(self, *, messages):
        self.messages.append(json.loads(json.dumps(messages)))
        return next(self.turns)


def test_official_recursive_return_revises_parent_siblings():
    adapter = _Adapter()
    model = _Model([_plan(["group", _leaf(99)], "ROOT_SUMMARY"),
                    _plan([_leaf(1), _leaf(99)], "CHILD_SUMMARY"),
                    _plan([]), _plan([_leaf(2)]), _plan([])])
    # Observe execution of the real vendored generator, not just an API with its name.
    frames = []
    original_profiler = sys.getprofile()
    def observe(frame, event, arg):
        if event == "call" and frame.f_code is upstream.chatbot.__code__:
            frames.append(frame.f_code.co_filename)
    sys.setprofile(observe)
    try:
        result = run_recap(public_task={"description": "Reach positions 1, 2."},
                           adapter=adapter, model=model, initial_public_state={"position": 0})
    finally:
        sys.setprofile(original_profiler)
    assert frames and all(path.endswith("vendor/recap/chatbot.py") for path in frames)
    assert result.status == "CONTROLLER_FINISHED"
    assert result.planning_turns == 5 and result.capability_calls == 2
    assert [r["amount"] for _, r in adapter.calls] == [1, 2]
    root = result.context_tree
    group, second_leaf = root["children"]
    assert group["task_name"] == "group"
    assert group["children"][0]["task_name"] == _leaf(1)
    assert second_leaf["task_name"] == _leaf(2)
    assert len(root["info_list"]) == 3
    leaf_return = model.messages[2][-1]["content"]
    parent_return = model.messages[3][-1]["content"]
    assert "CHILD_SUMMARY" in leaf_return and '"position": 1' in leaf_return
    assert "ROOT_SUMMARY" in parent_return and _leaf(99) in parent_return
    assert "successfully completed" not in leaf_return
    assert any(e["event"] == "parent_return" and e["kind"] == "subtask" for e in result.trace)


@pytest.mark.parametrize("bad", ["not json", _plan([_leaf("bad")]),
                                _plan([_leaf(1, name="missing")]),
                                json.dumps({"think": "bad format", "subtasks": [{}]})])
def test_invalid_plan_or_request_never_reaches_driver(bad):
    adapter = _Adapter()
    result = run_recap(public_task={}, adapter=adapter, model=_Model([bad]),
                       budgets=RecapBudgets(max_invalid_outputs=1))
    assert result.status == "INVALID_OUTPUT_BUDGET_EXHAUSTED"
    assert result.invalid_outputs == 1 and not adapter.calls


@pytest.mark.parametrize("budgets,turns,status,calls", [
    (RecapBudgets(max_planning_turns=2),
     [_plan(["child", "later"]), _plan([_leaf(1)])], "PLANNING_TURN_BUDGET_EXHAUSTED", 1),
    (RecapBudgets(max_capability_calls=1),
     [_plan([_leaf(1), _leaf(2)]), _plan([_leaf(2)])], "CAPABILITY_CALL_BUDGET_EXHAUSTED", 1),
    (RecapBudgets(max_depth=1),
     [_plan(["child", "later"]), _plan(["too deep", "later"])], "DEPTH_BUDGET_EXHAUSTED", 0),
])
def test_host_budgets_terminate_official_generator(budgets, turns, status, calls, tmp_path):
    adapter = _Adapter()
    result = run_recap(public_task={}, adapter=adapter, model=_Model(turns), budgets=budgets,
                       log_dir=tmp_path / "recap")
    assert result.status == status and len(adapter.calls) == calls
    assert result.planning_turns == len(turns)
    assert (tmp_path / "recap/tree.json").is_file()
    assert (tmp_path / "recap/history.json").is_file()


def test_upstream_empty_completion_is_not_a_successful_run():
    # Upstream accepts an empty root; host must not claim an executed diagnostic.
    adapter = _Adapter()
    result = run_recap(public_task={}, adapter=adapter, model=_Model([_plan([])]))
    assert result.status == "NO_ACTION_EXECUTED" and not adapter.calls
    # A failed root primitive also causes upstream StopIteration, not task success.
    failed = run_recap(public_task={}, adapter=_Adapter(["ERROR"]), model=_Model([_plan([_leaf(1)])]))
    assert failed.status == "LAST_ACTION_FAILED"


def test_operation_error_returns_to_official_parent_for_replanning():
    adapter = _Adapter(["ERROR", "EXECUTED"])
    model = _Model([_plan(["first attempt", "remaining"]), _plan([_leaf(1)]),
                    _plan([_leaf(2)]), _plan([])])
    result = run_recap(public_task={}, adapter=adapter, model=model)
    assert result.status == "CONTROLLER_FINISHED" and result.capability_calls == 2
    feedback = model.messages[2][-1]["content"]
    assert '"status": "ERROR"' in feedback
    assert "successfully completed" not in feedback


def test_transport_error_and_world_abort_do_not_complete():
    class DisconnectedModel:
        def generate_json(self, **kwargs):
            raise RuntimeError("transport disconnected")
    result = run_recap(public_task={}, adapter=_Adapter(), model=DisconnectedModel())
    assert result.status == "MODEL_ERROR"
    assert any(e.get("message") == "transport disconnected" for e in result.trace)
    aborted = run_recap(public_task={}, adapter=_Adapter(["WORKER_ABORTED"]),
                        model=_Model([_plan([_leaf(1)])]))
    assert aborted.status == "WORKER_ABORTED"


def test_official_controller_runs_without_old_package_or_standalone_sdks(tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = """
import sys
from importlib.abc import MetaPathFinder
class BlockUnusedPackages(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'autoadapter2', 'openai', 'tiktoken', 'together'}:
            raise ImportError('package deliberately unavailable')
sys.meta_path.insert(0, BlockUnusedPackages())
from auto_adapter.tests.test_recap import test_official_recursive_return_revises_parent_siblings
test_official_recursive_return_revises_parent_siblings()
assert not any(name.split('.')[0] == 'autoadapter2' for name in sys.modules)
"""
    completed = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                               env={**os.environ, "PYTHONPATH": str(root)},
                               text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
