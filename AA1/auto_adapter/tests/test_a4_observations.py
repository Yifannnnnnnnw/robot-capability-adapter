# SPDX-License-Identifier: Apache-2.0
"""Focused real-MuJoCo checks for the A4 velocity observations."""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter_bench.a4_observations import A4Observations
from autoadapter_bench.capability_eval import run_capability_case


XML = """
<mujoco>
  <option timestep="0.01"/>
  <worldbody>
    <body name="tool" pos="0 0 0">
      <joint name="tool_slide" type="slide" axis="1 0 0"/>
      <geom name="tool_collision" type="sphere" size="0.1" contype="1" conaffinity="1"/>
    </body>
    <body name="tool_visual" pos="0 0.3 0">
      <joint name="tool_visual_slide" type="slide" axis="1 0 0"/>
      <geom name="tool_visual" type="sphere" size="0.1" contype="0" conaffinity="0"/>
    </body>
    <body name="target" pos="0.5 0 0">
      <joint name="target_slide" type="slide" axis="1 0 0"/>
      <geom name="target_collision" type="sphere" size="0.1" contype="1" conaffinity="1"/>
    </body>
    <body name="target_visual" pos="0.5 0.3 0">
      <joint name="target_visual_slide" type="slide" axis="1 0 0"/>
      <geom name="target_visual" type="sphere" size="0.1" contype="0" conaffinity="0"/>
    </body>
  </worldbody>
</mujoco>
"""


def _world() -> tuple[mujoco.MjModel, mujoco.MjData]:
    model = mujoco.MjModel.from_xml_string(XML)
    data = mujoco.MjData(model)
    data.qvel[:] = [1.0, 100.0, -2.0, -100.0]
    mujoco.mj_forward(model, data)
    return model, data


def _binding() -> dict[str, list[str]]:
    return {
        "tool_geom_names": ["tool_collision", "tool_visual"],
        "target_geom_names": ["target_collision", "target_visual"],
    }


def test_canonical_geom_id_fallback_resolves_unnamed_model_geoms() -> None:
    unnamed_xml = XML
    for name in (
        "tool_collision",
        "tool_visual",
        "target_collision",
        "target_visual",
    ):
        unnamed_xml = unnamed_xml.replace(f' name="{name}"', "")
    model = mujoco.MjModel.from_xml_string(unnamed_xml)
    data = mujoco.MjData(model)
    data.qvel[:] = [1.0, 100.0, -2.0, -100.0]
    mujoco.mj_forward(model, data)

    observations = A4Observations(
        model,
        {
            "tool_geom_names": ["geom_0", "geom_1"],
            "target_geom_names": ["geom_2", "geom_3"],
        },
    )

    assert observations.collision_pairs == ((0, 2),)
    np.testing.assert_allclose(
        observations.snapshot(data)["a4_surface_relative_speed_m_s"],
        3.0,
        rtol=0.0,
        atol=1.0e-12,
    )


def test_surface_relative_speed_uses_both_moving_closest_points_and_filters_visuals() -> None:
    model, data = _world()
    observations = A4Observations(model, _binding())
    before_qpos = data.qpos.copy()
    before_qvel = data.qvel.copy()
    before_time = float(data.time)

    result = observations.snapshot(data)

    assert observations.collision_pairs == (
        (model.geom("tool_collision").id, model.geom("target_collision").id),
    )
    np.testing.assert_allclose(
        result["a4_surface_relative_speed_m_s"], 3.0, rtol=0.0, atol=1.0e-12
    )
    assert result["a4_contact_normal_closing_speed_m_s"] == 0.0
    np.testing.assert_array_equal(data.qpos, before_qpos)
    np.testing.assert_array_equal(data.qvel, before_qvel)
    assert float(data.time) == before_time


def test_contact_normal_closing_speed_has_the_expected_sign() -> None:
    model, data = _world()
    target_joint = model.joint("target_slide").id
    target_address = int(model.jnt_qposadr[target_joint])
    data.qpos[target_address] = -0.45
    mujoco.mj_forward(model, data)
    assert data.ncon == 1

    observations = A4Observations(model, _binding())
    closing = observations.snapshot(data)
    np.testing.assert_allclose(
        closing["a4_contact_normal_closing_speed_m_s"], 3.0, rtol=0.0, atol=1.0e-12
    )

    data.qvel[:] = [-1.0, -100.0, 2.0, 100.0]
    separating = observations.snapshot(data)
    assert separating["a4_contact_normal_closing_speed_m_s"] == 0.0


class _TraceDriver:
    def __init__(self) -> None:
        self.model, self.data = _world()

    def approach_until_contact(self, request: dict) -> None:
        del request
        mujoco.mj_step(self.model, self.data, 2)


def test_live_a4_trace_contains_required_observation_keys(tmp_path: Path, monkeypatch) -> None:
    class _Renderer:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def update_scene(self, *args, **kwargs):
            del args, kwargs

        def render(self):
            return np.zeros((8, 8, 3), dtype=np.uint8)

        def close(self):
            pass

    monkeypatch.setattr(mujoco, "Renderer", _Renderer)
    case = {
        "case_id": "a4-observation-trace",
        "capability_id": "A4",
        "method_name": "approach_until_contact",
        "request": {
            "precontact_position_m": [0.0, 0.0, 0.0],
            "approach_direction_unit": [1.0, 0.0, 0.0],
            "max_travel_m": 0.4,
            "max_approach_speed_m_s": 0.02,
            "max_duration_s": 1.0,
        },
        "measurement_binding": {
            "contract_id": "A4",
            "site_name": "missing_site_is_not_needed_for_trace",
            **_binding(),
            "precontact_gate": "held_window_then_ray",
        },
        "timing": {"max_sim_time_s": 1.0, "max_steps": 10, "video_fps": 10},
    }
    result = run_capability_case(_TraceDriver(), case, tmp_path)
    trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
    samples = trace["evidence"]["samples"]
    assert len(samples) == 3
    for sample in samples:
        assert set(
            (
                "a4_surface_relative_speed_m_s",
                "a4_contact_normal_closing_speed_m_s",
            )
        ) <= sample.keys()
