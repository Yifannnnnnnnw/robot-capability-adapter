# SPDX-License-Identifier: Apache-2.0
"""Focused checks for the public DESIGN scene handoff used by generation."""

from __future__ import annotations

import json
import os
from pathlib import Path

import mujoco
import pytest
import yaml

from auto_adapter import design_validation
from auto_adapter.orchestrator import (
    PhaseResult,
    SelfAssemble,
    SelfAssembleConfig,
)
from auto_adapter.orchestrator_from_scratch import (
    FromScratchConfig,
    FromScratchOrchestrator,
)
from auto_adapter.scene_runtime import (
    SceneCaseError,
    build_scene_driver,
    export_probe_scenes,
    reset_scene_driver,
)


_SCENE_XML = """
<mujoco model="handoff_fixture">
  <option timestep="0.001"/>
  <worldbody>
    <body name="arm">
      <joint name="hinge" type="hinge" axis="0 0 1" range="-1 1"/>
      <geom name="arm_geom" type="box" size="0.02 0.02 0.02" mass="1"/>
      <site name="tool_site" pos="0 0 0"/>
    </body>
    <body name="target" pos="0.2 0 0">
      <freejoint/>
      <geom name="target_geom" type="box" size="0.01 0.01 0.01" mass="1"/>
    </body>
  </worldbody>
  <actuator>
    <position name="hinge" joint="hinge" kp="1" ctrlrange="-1 1"/>
  </actuator>
  <keyframe>
    <key name="home" qpos="0 0 0 0 0 0 0 0"/>
  </keyframe>
</mujoco>
"""


def _initial_state() -> dict:
    return {
        "robot": {"keyframe": "home", "qpos_by_joint": {"hinge": 0.25}},
        "free_bodies": {
            "target": {
                "position_m": [0.4, 0.1, 0.2],
                "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                "linear_velocity_m_s": [0.0, 0.0, 0.0],
                "angular_velocity_rad_s": [0.0, 0.0, 0.0],
                "ignored_private_note": "must not be exported",
            }
        },
        "ctrl_by_actuator": {"hinge": 0.25},
        "settle_s": 0.0,
        "ignored_private_field": True,
    }


def _suite() -> dict:
    state = _initial_state()
    return {
        "scenes": {
            "scene_empty": {"objects": []},
            "scene_contact": {
                "objects": [
                    {
                        "name": "target",
                        "motion": "free",
                        "shape": "box",
                        "size_m": [0.02, 0.02, 0.02],
                        "position_m": [0.4, 0.1, 0.2],
                        "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                        "mass_kg": 1.0,
                    }
                ]
            },
        },
        "cases": [
            {
                "case_id": "private_empty_case",
                "scene": "scene_empty",
                "capability_id": "move_cap",
                "initial_state": state,
                "request": {"private_request": True},
                "measurements": [{"private_measurement": True}],
                "execution": {"private_execution": True},
            },
            {
                "case_id": "private_contact_case_1",
                "scene": "scene_contact",
                "capability_id": "push_cap",
                "initial_state": state,
                "request": {"private_request": 1},
                "measurements": [{"private_measurement": 1}],
                "execution": {"private_execution": 1},
            },
            {
                "case_id": "private_contact_case_2",
                "scene": "scene_contact",
                "capability_id": "push_cap",
                "initial_state": state,
                "request": {"private_request": 2},
                "measurements": [{"private_measurement": 2}],
                "execution": {"private_execution": 2},
            },
        ],
    }


def _standard_driver(path: Path) -> None:
    path.write_text(
        "import mujoco\n"
        "class Robot:\n"
        "    def __init__(self, model, data):\n"
        "        self.model, self.data = model, data\n"
        "    def reset(self):\n"
        "        mujoco.mj_resetData(self.model, self.data)\n"
        "    def move(self, request):\n"
        "        mujoco.mj_step(self.model, self.data)\n"
        "def build():\n"
        "    model = mujoco.MjModel.from_xml_path('mjcf.xml')\n"
        "    return Robot(model, mujoco.MjData(model))\n",
        encoding="utf-8",
    )


def _scratch_driver(path: Path) -> None:
    path.write_text(
        "import mujoco\n"
        "class Robot:\n"
        "    def __init__(self, model, data):\n"
        "        self.model, self.data = model, data\n"
        "    def reset(self):\n"
        "        mujoco.mj_resetData(self.model, self.data)\n"
        "    def move(self, request):\n"
        "        mujoco.mj_step(self.model, self.data)\n"
        "    @classmethod\n"
        "    def build_from_mjcf(cls, mjcf_path):\n"
        "        model = mujoco.MjModel.from_xml_path(mjcf_path)\n"
        "        return cls(model, mujoco.MjData(model))\n",
        encoding="utf-8",
    )


def test_probe_scene_export_is_public_and_deduplicated(tmp_path: Path) -> None:
    scene = tmp_path / "prepared_scene.xml"
    scene.write_text(_SCENE_XML, encoding="utf-8")
    output = export_probe_scenes(
        suite=_suite(),
        scene_paths={"scene_empty": scene, "scene_contact": scene},
        output_path=tmp_path / "design" / "probe_scenes.json",
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert set(payload) == {"environments"}
    assert len(payload["environments"]) == 2
    assert {
        "scene_id",
        "capability_id",
        "scene_path",
        "initial_state",
    } == set(payload["environments"][0])
    rendered = json.dumps(payload, sort_keys=True)
    for private_field in (
        "case_id",
        "private_request",
        "private_measurement",
        "private_execution",
        "ignored_private_field",
        "ignored_private_note",
    ):
        assert private_field not in rendered
    assert all(
        Path(environment["scene_path"]).is_absolute()
        for environment in payload["environments"]
    )
    assert payload["environments"][0]["initial_state"]["robot"]["keyframe"] == "home"


@pytest.mark.parametrize("from_scratch", [False, True])
def test_scene_driver_builds_prepared_xml_and_resets_fresh_state(
    tmp_path: Path, from_scratch: bool
) -> None:
    scene = tmp_path / "prepared_scene.xml"
    scene.write_text(_SCENE_XML, encoding="utf-8")
    driver = tmp_path / (
        "driver_from_scratch.py" if from_scratch else "driver.py"
    )
    (_scratch_driver if from_scratch else _standard_driver)(driver)
    original_cwd = Path.cwd()
    try:
        work = tmp_path / ("scratch_probe" if from_scratch else "standard_probe")
        robot = build_scene_driver(
            driver.resolve(),
            scene.resolve(),
            work_dir=work,
            from_scratch=from_scratch,
        )
        assert robot.model.nbody == 3
        assert (work / "mjcf.xml").resolve() == scene.resolve()
        reset_scene_driver(robot, _initial_state())
        hinge_id = robot.model.joint("hinge").id
        hinge_qpos = robot.model.jnt_qposadr[hinge_id]
        target_id = robot.model.body("target").id
        target_qpos = robot.model.jnt_qposadr[
            robot.model.body_jntadr[target_id]
        ]
        assert robot.data.qpos[hinge_qpos] == pytest.approx(0.25)
        assert robot.data.qpos[target_qpos : target_qpos + 3].tolist() == pytest.approx(
            [0.4, 0.1, 0.2]
        )
        assert robot.data.ctrl[robot.model.actuator("hinge").id] == pytest.approx(0.25)

        robot.data.qpos[target_qpos] = 9.0
        fresh_work = tmp_path / (
            "scratch_probe_fresh" if from_scratch else "standard_probe_fresh"
        )
        fresh = build_scene_driver(
            driver.resolve(),
            scene.resolve(),
            work_dir=fresh_work,
            from_scratch=from_scratch,
        )
        reset_scene_driver(fresh, _initial_state())
        assert fresh.data.qpos[target_qpos] == pytest.approx(0.4)
    finally:
        os.chdir(original_cwd)


def test_scene_driver_rejects_non_disposable_workdir(tmp_path: Path) -> None:
    scene = tmp_path / "prepared_scene.xml"
    scene.write_text(_SCENE_XML, encoding="utf-8")
    driver = tmp_path / "driver.py"
    _standard_driver(driver)
    with pytest.raises(SceneCaseError, match="independent disposable"):
        build_scene_driver(driver, scene, work_dir=tmp_path)


def test_validator_uses_shared_scene_builder(tmp_path: Path, monkeypatch) -> None:
    scene = tmp_path / "prepared_scene.xml"
    scene.write_text(_SCENE_XML, encoding="utf-8")
    driver = tmp_path / "driver.py"
    _standard_driver(driver)
    suite = {
        "scenes": {"plain": {"objects": []}},
        "cases": [
            {
                "case_id": "move_case",
                "scene": "plain",
                "capability_id": "move_cap",
                "initial_state": {
                    "robot": {"keyframe": "home", "qpos_by_joint": {}},
                    "free_bodies": {},
                    "ctrl_by_actuator": {},
                    "settle_s": 0.0,
                },
                "request": {},
                "execution": {"max_sim_time_s": 0.05, "wall_timeout_s": 2.0},
                "measurements": [],
            }
        ],
    }
    design = {
        "capabilities": [
            {
                "capability_id": "move_cap",
                "method_name": "move",
                "criteria": [],
                "request_schema": {"type": "object"},
            }
        ]
    }
    calls: list[dict] = []
    real_builder = design_validation.build_scene_driver

    def spy_builder(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return real_builder(*args, **kwargs)

    monkeypatch.setattr(design_validation, "build_scene_driver", spy_builder)
    original_cwd = Path.cwd()
    try:
        result = design_validation._worker_result(
            {
                "driver_path": str(driver),
                "scene_path": str(scene),
                "output_dir": str(tmp_path / "worker"),
                "case": suite["cases"][0],
                "design": design,
                "from_scratch": False,
            }
        )
    finally:
        os.chdir(original_cwd)
    assert calls
    assert calls[0]["kwargs"]["from_scratch"] is False
    assert Path(calls[0]["args"][1]).resolve() == scene.resolve()
    assert result["error_stage"] == "build"
    assert "case.measurements must be a non-empty array" in result["error"]


def test_all_generation_entrypoints_include_scene_handoff(tmp_path: Path, monkeypatch) -> None:
    scene = tmp_path / "prepared_scene.xml"
    scene.write_text(_SCENE_XML, encoding="utf-8")
    handoff = tmp_path / "runs" / "fixture" / "design" / "probe_scenes.json"
    handoff.parent.mkdir(parents=True)
    handoff.write_text('{"environments": []}\n', encoding="utf-8")
    captured: list[dict] = []

    def capture_phase(**kwargs):
        captured.append(kwargs)
        return PhaseResult(kwargs["name"], True, 0.0)

    standard = SelfAssemble(
        SelfAssembleConfig("fixture", scene, tmp_path / "runs")
    )
    standard.capability_design = {
        "capabilities": [{"capability_id": "cap", "method_name": "move"}]
    }
    standard.probe_scenes_path = handoff
    monkeypatch.setattr(standard, "_run_phase", capture_phase)
    standard._phase_generate()
    standard._phase_repair("feedback", 1)

    scratch = FromScratchOrchestrator(
        FromScratchConfig("fixture", scene, tmp_path / "scratch_runs")
    )
    scratch.capability_design = {
        "capabilities": [{"capability_id": "cap", "method_name": "move"}]
    }
    scratch.probe_scenes_path = handoff

    def capture_loop(**kwargs):
        captured.append(kwargs)
        return type("Result", (), {"ok": True, "error": None, "final_text": "", "total_tokens": {}})()

    monkeypatch.setattr(scratch, "_run_loop", capture_loop)
    scratch.phase_gen_algo()
    scratch.phase_gen_repair("feedback", 1)

    assert len(captured) == 4
    for item in captured:
        text = item["user_msg"]
        assert "design/probe_scenes.json" in text
        assert "scene_path" in text
        assert "build_scene_driver" in text
        assert "reset_scene_driver" in text
        assert "Do not default to\nenvironments[0]" in text
        assert "whose contents contain" in text
        assert "build_from_mjcf('mjcf.xml')" not in text
