# SPDX-License-Identifier: Apache-2.0
"""Regression for a replay exception incorrectly counted as task success."""
from types import SimpleNamespace

from autoadapter_bench.eval_baseline import run_one_trial


def test_baseline_replay_exception_cannot_pass() -> None:
    class ReplayExceptionDriverFixture:
        def gripper_open(self):
            raise RuntimeError("named fixture: replay failed")

    result_fixture = SimpleNamespace(
        ok=True, tool_call_log=[{"tool": "gripper_open", "input": {}}],
        n_tool_calls=1, n_frames=0, duration_sec=0.0, token_usage={},
        summary="named fixture", error=None,
    )
    planner_fixture = SimpleNamespace(
        _load_driver=ReplayExceptionDriverFixture,
        execute_task=lambda *_args, **_kwargs: result_fixture,
    )
    trial = run_one_trial(planner_fixture, {
        "id": "replay_exception_fixture", "prompt": "named fixture",
        "success": {"type": "tool_executed_without_crash",
                    "required_tools": ["gripper_open"]},
    }, 0)

    assert trial["physics_metrics"]["replay_diverged"] is True
    assert trial["physics_ok"] is False
