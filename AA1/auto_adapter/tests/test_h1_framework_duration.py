# SPDX-License-Identifier: Apache-2.0
"""Focused regression for H1 stand validation requiring real elapsed physics."""

from __future__ import annotations

from pathlib import Path

import mujoco

from auto_adapter.orchestrator_from_scratch import _validate_humanoid_stand_balance


REPO_ROOT = Path(__file__).resolve().parents[2]
H1_SCENE = REPO_ROOT / "assets" / "mjcf" / "h1" / "scene.xml"


class _NoOpStandingDriver:
    """Looks upright on the real H1 state but advances no physics."""

    def __init__(self) -> None:
        self.model = mujoco.MjModel.from_xml_path(str(H1_SCENE))
        self.data = mujoco.MjData(self.model)
        mujoco.mj_forward(self.model, self.data)

    def stand_balance(self, *, secs: float) -> bool:
        del secs
        return True


def test_h1_stand_balance_rejects_no_step_driver() -> None:
    robot = _NoOpStandingDriver()
    pelvis = mujoco.mj_name2id(
        robot.model, mujoco.mjtObj.mjOBJ_BODY, "pelvis"
    )
    initial_height = float(robot.data.xpos[pelvis, 2])
    initial_upright = float(robot.data.xmat[pelvis].reshape(3, 3)[2, 2])

    # This was the old false-success condition: a no-op begins at a valid pose.
    assert initial_height > 0.6 and initial_upright > 0.7

    result = _validate_humanoid_stand_balance(
        robot, "h1", H1_SCENE, secs=2.0
    )

    assert result["ok"] is False
    assert result["metric"] == 0.0
    assert "elapsed=0.000s" in result["detail"]
    assert "steps=0" in result["detail"]


def test_from_scratch_summary_does_not_hide_failed_humanoid_walk(tmp_path):
    """Named orchestration fixture reproducing the real H1 7/8 verdict."""
    from types import SimpleNamespace
    from auto_adapter.orchestrator_from_scratch import FromScratchOrchestrator

    runner = object.__new__(FromScratchOrchestrator)
    runner.cfg = SimpleNamespace(robot_id="h1", max_outer_retries=0)
    runner.workspace = tmp_path
    (tmp_path / "study.json").write_text("{}")
    (tmp_path / "driver_from_scratch.py").write_text("# named test fixture\n")
    phase = SimpleNamespace(total_tokens={}, error=None)
    runner.phase_study = lambda: phase
    runner.phase_gen_algo = lambda: phase
    runner._validate_from_scratch_driver = lambda: {
        "all_ok": False, "structural_ok": True,
        "tests": [{"test": "stand_balance", "ok": True},
                  {"test": "humanoid_walk", "ok": False}],
    }
    result = runner.run()
    assert result.gen_ok
    assert result.validate_ok is False
    assert result.validate_report["structural_ok"] is True
