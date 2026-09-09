"""Focused checks for the independent PiPER capability fixture."""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter_bench.reference_controls.piper.reference_arm import (
    ReferencePiperDriver,
)


AA1_ROOT = Path(__file__).resolve().parents[2]
DESIGN_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/piper/capability_design.json"
SUITE_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/piper/capability_validation_suite.json"
SCENE_PATH = AA1_ROOT / "assets/mjcf/capabilities/piper/fixed_scene.xml"


def _json(path: Path) -> dict:
    with path.open() as handle:
        return json.load(handle)


def _reset_home(model: mujoco.MjModel, data: mujoco.MjData) -> None:
    keyframe_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    assert keyframe_id >= 0
    mujoco.mj_resetDataKeyframe(model, data, keyframe_id)
    mujoco.mj_forward(model, data)


def test_piper_capability_design_and_fixture_bind_real_symbols() -> None:
    design = _json(DESIGN_PATH)
    suite = _json(SUITE_PATH)

    assert design["robot_configuration_id"] == "piper"
    assert design["scene_path"] == "assets/mjcf/capabilities/piper/fixed_scene.xml"
    assert design["robot_bindings_path"].endswith("piper/robot_bindings.json")
    assert [item["capability_id"] for item in design["capabilities"]] == [
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
    ]
    assert suite["robot_configuration_id"] == "piper"
    assert suite["scene_path"] == design["scene_path"]
    assert len(suite["cases"]) == 10
    assert all(item["reset"].get("settle_s", 0.0) > 0.0 for item in suite["cases"])
    assert {item["capability_id"] for item in suite["cases"]} == {
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
    }

    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert model.nq == 8
    assert model.nu == 7
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "ee_site") >= 0
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base_link") >= 0
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "fixed_contact_target") >= 0
    for name in (
        "joint1",
        "joint2",
        "joint3",
        "joint4",
        "joint5",
        "joint6",
        "joint7",
        "joint8",
    ):
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) >= 0
    for name in (
        "joint1",
        "joint2",
        "joint3",
        "joint4",
        "joint5",
        "joint6",
        "gripper",
    ):
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name) >= 0
    for name in (
        "geom_82",
        "geom_84",
        "geom_85",
        "geom_87",
        "geom_88",
        "fixed_contact_geom",
    ):
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name) >= 0


def test_piper_a1_nominal_reference_control_reaches_real_target() -> None:
    suite = _json(SUITE_PATH)
    case = next(item for item in suite["cases"] if item["case_id"] == "piper-a1-nominal")
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    _reset_home(model, data)
    for _ in range(int(round(0.5 / model.opt.timestep))):
        mujoco.mj_step(model, data)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "ee_site")
    joint7_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "joint7")
    q7_address = int(model.jnt_qposadr[joint7_id])
    initial_q7 = float(data.qpos[q7_address])

    ReferencePiperDriver(model=model, data=data).move_end_effector_to_position(
        case["request"]
    )

    target = np.asarray(case["request"]["target_position_m"], dtype=float)
    error = float(np.linalg.norm(data.site_xpos[site_id] - target))
    assert data.time >= 0.5 + case["request"]["max_duration_s"] + 0.5 - 1e-9
    assert error <= 0.015
    assert abs(float(data.qpos[q7_address]) - initial_q7) <= 0.00175
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.ctrl).all()
    assert np.all(data.ctrl >= model.actuator_ctrlrange[:, 0] - 1e-12)
    assert np.all(data.ctrl <= model.actuator_ctrlrange[:, 1] + 1e-12)
