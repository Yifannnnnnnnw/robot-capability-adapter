# SPDX-License-Identifier: Apache-2.0
"""Focused physics-boundary checks for the capability evaluator."""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np
import pytest

from autoadapter_bench.capability_eval import (
    reset_capability_case,
    run_capability_case,
    run_capability_suite,
)


XML = """
<mujoco>
  <option timestep="0.01"/>
  <worldbody>
    <body name="base">
      <joint name="joint" type="hinge"/>
      <geom name="body_geom" type="box" size="0.1 0.1 0.1"/>
      <site name="ee_site" pos="0 0 0"/>
    </body>
  </worldbody>
  <actuator>
    <position name="actuator" joint="joint" ctrlrange="-1 1" kp="10"/>
  </actuator>
</mujoco>
"""


@pytest.fixture(autouse=True)
def named_video_fixture(monkeypatch):
    """Only these focused tests replace the trusted renderer; physics stays real."""
    class FixtureRenderer:
        def __init__(self, *args, **kwargs):
            pass

        def update_scene(self, *args, **kwargs):
            pass

        def render(self):
            return np.zeros((16, 16, 3), dtype=np.uint8)

        def close(self):
            pass

    monkeypatch.setattr(mujoco, "Renderer", FixtureRenderer)


class _Driver:
    def __init__(self) -> None:
        self.model = mujoco.MjModel.from_xml_string(XML)
        self.data = mujoco.MjData(self.model)
        mujoco.mj_forward(self.model, self.data)

    def render(self) -> np.ndarray:
        # The world and the trace remain real MuJoCo objects.  This tiny
        # fallback only keeps the focused test independent of a display.
        return np.zeros((8, 8, 3), dtype=np.uint8)

    def hold(self, request: dict) -> None:
        del request
        self.data.ctrl[0] = 0.25
        for _ in range(60):
            mujoco.mj_step(self.model, self.data)

    def batched_hold(self, request: dict) -> None:
        del request
        self.data.ctrl[0] = 0.25
        mujoco.mj_step(self.model, self.data, 3)

    def writes_state(self, request: dict) -> None:
        del request
        self.data.qpos[0] = 0.4
        mujoco.mj_step(self.model, self.data)

    def writes_model(self, request: dict) -> None:
        del request
        self.model.body_mass[1] *= 2.0
        mujoco.mj_step(self.model, self.data)


def _case(method_name: str, *, case_id: str = "case") -> dict:
    return {
        "case_id": case_id,
        "capability_id": "A1",
        "method_name": method_name,
        "request": {
            "target_position_m": [0.0, 0.0, 0.0],
            "max_duration_s": 0.8,
        },
        "measurement_binding": {
            "contract_id": "A1",
            "site_name": "ee_site",
        },
        "timing": {
            "max_sim_time_s": 1.0,
            "max_steps": 100,
            # Deliberately lower than the physics rate.  Video stride must be
            # derived from dt and video_fps, not from this evidence metadata.
            "sample_hz": 40,
            "video_fps": 20,
        },
    }


def test_case_records_each_batched_physics_step_and_writes_trace_video(tmp_path: Path) -> None:
    driver = _Driver()
    result = run_capability_case(driver, _case("batched_hold"), tmp_path)

    assert result["physics_steps"] == 3
    assert result["n_frames"] == 2  # initial and final; final is not duplicated
    assert result["model_generated"] is None
    assert Path(result["trace_path"]).is_file()
    assert Path(result["video_path"]).is_file()
    trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
    assert len(trace["evidence"]["samples"]) == 4
    assert trace["evidence"]["step_count"] == 3
    assert trace["model_generated"] is None


def test_trace_positions_match_the_recorded_post_step_joint_state(tmp_path: Path) -> None:
    """Reproduce the one-step pose lag observed in the Franka A1 hold trace."""
    driver = _Driver()
    driver.model = mujoco.MjModel.from_xml_string(XML.replace(
        'name="ee_site" pos="0 0 0"', 'name="ee_site" pos="0.2 0 0"'))
    driver.data = mujoco.MjData(driver.model)
    result = run_capability_case(driver, _case("batched_hold"), tmp_path)
    samples = json.loads(Path(result["trace_path"]).read_text())["evidence"]["samples"]
    assert abs(samples[-1]["joint_positions"]["joint"]) > 0.001
    for sample in samples:
        angle = sample["joint_positions"]["joint"]
        expected = [0.2 * np.cos(angle), 0.2 * np.sin(angle), 0.0]
        assert np.allclose(sample["site_positions"]["ee_site"], expected, atol=1e-12)


def test_case_rejects_live_state_and_model_parameter_writes(tmp_path: Path) -> None:
    state_result = run_capability_case(_Driver(), _case("writes_state", case_id="state"), tmp_path)
    model_result = run_capability_case(_Driver(), _case("writes_model", case_id="model"), tmp_path)

    assert state_result["ok"] is False
    assert "qpos" in state_result["evidence_summary"]["direct_state_write_fields"]
    assert model_result["ok"] is False
    assert model_result["evidence_summary"]["model_parameter_write_detected"] is True


def test_renderer_failure_cannot_pass_using_candidate_black_frames(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("fixture unavailable renderer")

    monkeypatch.setattr(mujoco, "Renderer", unavailable)
    result = run_capability_case(_Driver(), _case("hold"), tmp_path)
    assert result["score"] == 1.0  # physical contract alone would pass
    assert result["ok"] is False
    assert result["n_frames"] == 0
    assert "renderer initialization failed" in result["detail"]


def test_reset_and_suite_filter_reuse_the_same_driver_world(tmp_path: Path, monkeypatch) -> None:
    driver = _Driver()
    reset_capability_case(
        driver,
        {
            "qpos_by_joint": {"joint": 0.2},
            "ctrl_by_actuator": {"actuator": 0.1},
        },
    )
    assert np.isclose(driver.data.qpos[0], 0.2)
    assert np.isclose(driver.data.ctrl[0], 0.1)

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "capability_validation_suite.json").write_text(
        json.dumps({"cases": [_case("hold", case_id="keep"), _case("hold", case_id="skip")]}),
        encoding="utf-8",
    )
    import autoadapter_bench.capability_eval as evaluator

    monkeypatch.setattr(evaluator, "REPO_ROOT", tmp_path)
    result = run_capability_suite(
        driver,
        {"id": "test", "capability_profile": "profile"},
        tmp_path / "out",
        case_ids=["keep"],
    )

    assert result["all_ok"] is True
    assert [item["case_id"] for item in result["tests"]] == ["keep"]
    assert result["tests"][0]["score"] == 1.0
