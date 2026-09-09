"""Focused checks for the independent KUKA iiwa 14 capability fixture."""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter_bench.reference_controls.kuka_iiwa14.reference_arm import (
    ReferenceKukaDriver,
)


AA1_ROOT = Path(__file__).resolve().parents[2]
DESIGN_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/kuka_iiwa14/capability_design.json"
SUITE_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/kuka_iiwa14/capability_validation_suite.json"
BINDINGS_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/kuka_iiwa14/robot_bindings.json"
SCENE_PATH = AA1_ROOT / "assets/mjcf/capabilities/kuka_iiwa14/fixed_scene.xml"


def _json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _reset_home(model: mujoco.MjModel, data: mujoco.MjData, case: dict | None = None) -> None:
    case = case or {}
    reset = case.get("reset", {})
    keyframe_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_KEY, reset.get("name", "home")
    )
    assert keyframe_id >= 0
    mujoco.mj_resetDataKeyframe(model, data, keyframe_id)
    for name, value in reset.get("qpos_by_joint", {}).items():
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        assert joint_id >= 0
        data.qpos[int(model.jnt_qposadr[joint_id])] = float(value)
    for name, value in reset.get("ctrl_by_actuator", {}).items():
        actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
        assert actuator_id >= 0
        data.ctrl[actuator_id] = float(value)
    for name, position in reset.get("mocap_by_body", {}).items():
        body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        assert body_id >= 0
        mocap_id = int(model.body_mocapid[body_id])
        assert mocap_id >= 0
        data.mocap_pos[mocap_id] = np.asarray(position, dtype=float)
    mujoco.mj_forward(model, data)
    settle_s = float(reset.get("settle_s", 0.0))
    for _ in range(int(round(settle_s / model.opt.timestep))):
        mujoco.mj_step(model, data)


def test_kuka_capability_artifacts_bind_real_model_symbols() -> None:
    design = _json(DESIGN_PATH)
    suite = _json(SUITE_PATH)
    bindings = _json(BINDINGS_PATH)

    assert design["robot_configuration_id"] == "kuka_iiwa14"
    assert design["scene_path"] == "assets/mjcf/capabilities/kuka_iiwa14/fixed_scene.xml"
    assert design["robot_bindings_path"].endswith("kuka_iiwa14/robot_bindings.json")
    assert [item["capability_id"] for item in design["capabilities"]] == [
        "A1",
        "A2",
        "A4",
        "A5",
    ]
    assert all("SO-101" not in item["description"] for item in design["capabilities"])

    assert suite["robot_configuration_id"] == "kuka_iiwa14"
    assert suite["scene_path"] == design["scene_path"]
    assert len(suite["cases"]) == 8
    assert {item["capability_id"] for item in suite["cases"]} == {"A1", "A2", "A4", "A5"}
    assert all(item["reset"].get("settle_s", 0.0) > 0.0 for item in suite["cases"])
    assert all(item["measurement_binding"]["contract_id"] == item["capability_id"] for item in suite["cases"])
    assert all(
        item["measurement_binding"].get("max_penetration_m") == 0.005
        for item in suite["cases"]
        if item["capability_id"] == "A4"
    )

    assert bindings["robot_configuration_id"] == "kuka_iiwa14"
    assert bindings["arm_joint_names"] == [f"joint{index}" for index in range(1, 8)]
    assert bindings["arm_actuator_names"] == [f"actuator{index}" for index in range(1, 8)]
    assert "gripper_actuator_name" not in bindings

    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (model.nq, model.nv, model.nu, model.nmocap, model.nkey) == (7, 7, 7, 1, 1)
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "attachment_site") >= 0
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base") >= 0
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "link7_contact_geom") >= 0
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "fixed_contact_geom") >= 0
    assert np.allclose(
        model.jnt_range,
        np.asarray(
            [
                [-2.96706, 2.96706],
                [-2.0944, 2.0944],
                [-2.96706, 2.96706],
                [-2.0944, 2.0944],
                [-2.96706, 2.96706],
                [-2.0944, 2.0944],
                [-3.05433, 3.05433],
            ]
        ),
    )
    assert np.allclose(model.actuator_ctrlrange, model.jnt_range)
    assert np.allclose(model.actuator_gainprm[:, 0], 2000.0)
    assert np.allclose(model.actuator_biasprm[:, :3], np.asarray([0.0, -2000.0, -200.0]))


def test_kuka_a1_nominal_reference_control_reaches_real_target() -> None:
    suite = _json(SUITE_PATH)
    case = next(item for item in suite["cases"] if item["case_id"] == "kuka_iiwa_14-a1-nominal")
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    _reset_home(model, data, case)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "attachment_site")

    ReferenceKukaDriver(model=model, data=data).move_end_effector_to_position(case["request"])

    target = np.asarray(case["request"]["target_position_m"], dtype=float)
    error = float(np.linalg.norm(data.site_xpos[site_id] - target))
    assert data.time >= 0.5 + case["request"]["max_duration_s"] + 0.5 - 1e-9
    assert error <= 0.015
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.ctrl).all()
    assert np.all(data.ctrl >= model.actuator_ctrlrange[:, 0] - 1e-12)
    assert np.all(data.ctrl <= model.actuator_ctrlrange[:, 1] + 1e-12)
