# SPDX-License-Identifier: Apache-2.0
"""Focused evaluator regressions for motion and replay false successes."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from autoadapter_bench.eval import _replay_tool_calls, evaluate_success, run_task


class _NoMotionArm:
    def get_ee_pose(self):
        return np.zeros(3), np.eye(3)


class _ReplayFailure:
    def move_cartesian(self, *_args, **_kwargs):
        return False

    def get_ee_pose(self):
        return np.zeros(3), np.eye(3)


class _EmptyGripper:
    def gripper_close(self):
        return False


class _H1:
    def get_base_pose(self):
        return np.array([0.0, 0.0, 1.0]), np.eye(3)


def _result(calls=()):
    return SimpleNamespace(tool_call_log=list(calls), summary="", ok=True)


def test_round_trip_home_requires_real_ee_excursion() -> None:
    ok, _detail, metrics = evaluate_success(
        _NoMotionArm(),
        {"ee_pose": [0.0, 0.0, 0.0], "_phase_snapshots": [
            {"tool": "home", "ee": [0.0, 0.0, 0.0]}
        ]},
        {"type": "ee_pose_returned_home", "tolerance_m": 0.03},
        _result(),
        "round_trip",
    )
    assert not ok
    assert metrics["peak_excursion_m"] == 0.0


def test_replay_failure_and_missing_tool_are_not_clean() -> None:
    assert not _replay_tool_calls(
        _ReplayFailure(),
        [{"tool": "move_cartesian", "input": {"x": 0, "y": 0, "z": 0}}],
    )
    assert not _replay_tool_calls(_ReplayFailure(), [{"tool": "missing_tool"}])


def test_empty_gripper_false_is_not_replay_divergence() -> None:
    assert _replay_tool_calls(_EmptyGripper(), [{"tool": "gripper_close"}])


def test_run_task_replay_exception_cannot_pass(tmp_path):
    class CrashingGripperFixture:
        def gripper_open(self):
            raise RuntimeError("observed replay failure")

    calls = [{"tool": "gripper_open", "input": {}}]
    result = SimpleNamespace(ok=True, tool_call_log=calls, n_tool_calls=1,
                             n_frames=0, duration_sec=0, token_usage={},
                             summary="", error=None, mp4_path=None, trace_path=None)
    planner = SimpleNamespace(
        _load_driver=CrashingGripperFixture,
        execute_task=lambda *args, **kwargs: result,
        rec_dir=tmp_path / "recordings", trace_dir=tmp_path / "traces",
    )
    task = {"id": "gripper_fixture", "prompt": "fixture",
            "success": {"type": "tool_executed_without_crash",
                        "required_tools": ["gripper_open"]}}
    trial = run_task(planner, {"class": "arm"}, task, 1)[0]
    assert not trial["physics_ok"]
    assert trial["physics_metrics"]["replay_diverged"]


def test_h1_timed_balance_without_steps_fails() -> None:
    ok, _detail, metrics = evaluate_success(
        _H1(),
        {"_physics_samples": []},
        {"type": "torso_upright_height", "min_torso_h_m": 0.85,
         "min_upright": 0.7, "duration_s": 2.0},
        _result(),
        "stand_balance",
    )
    assert not ok
    assert metrics["required_duration_s"] == 2.0


def test_new_motion_graders_reject_inaction() -> None:
    fingers = {finger: [0.0, 0.0, 0.0]
               for finger in ("index", "middle", "ring", "thumb")}
    finger_goals = {finger: [0.1, 0.0, 0.0] for finger in fingers}
    hand_ok, _detail, _metrics = evaluate_success(
        object(), {"fingertips": fingers,
                   "_physics_samples": [{"finite": True, "fingertips": fingers}]},
        {"type": "fingertip_targets_reached", "target_fingertips": finger_goals},
        _result(), "hand",
    )
    assert not hand_ok

    mobile_samples = [
        {"finite": True, "base_xyz": [0.0, 0.0, 0.0], "ee": [0, 0, 0],
         "arm_qpos": [0.0], "time": 0.0},
        {"finite": True, "base_xyz": [-0.09, 0.0, 0.0], "ee": [-0.02, -0.24, 0.67],
         "arm_qpos": [0.0], "time": 1.0},
    ]
    mobile_ok, _detail, _metrics = evaluate_success(
        object(), {"_physics_samples": mobile_samples},
        {"type": "mobile_manipulation_ordered", "base_target_xy": [-0.09, 0.0],
         "ee_target_xyz": [-0.02, -0.24, 0.67]},
        _result(), "stretch",
    )
    assert not mobile_ok

    hands = {"left": [0.0, 0.0, 0.0], "right": [0.1, 0.0, 0.0]}
    bimanual_samples = [
        {"finite": True, "ees": hands, "time": 0.0},
        {"finite": True, "ees": hands, "time": 0.5},
    ]
    bimanual_ok, _detail, _metrics = evaluate_success(
        object(), {"_physics_samples": bimanual_samples},
        {"type": "bimanual_reach_hold", "targets": hands, "hold_s": 0.5},
        _result(), "aloha",
    )
    assert not bimanual_ok
