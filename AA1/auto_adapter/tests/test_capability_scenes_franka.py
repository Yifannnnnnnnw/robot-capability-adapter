"""Focused checks for the independent Franka capability fixture."""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter_bench.reference_controls.franka.reference_arm import (
    ReferenceFrankaDriver,
)


AA1_ROOT = Path(__file__).resolve().parents[2]
DESIGN_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/franka/capability_design.json"
SUITE_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/franka/capability_validation_suite.json"
BINDINGS_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/franka/robot_bindings.json"
SCENE_PATH = AA1_ROOT / "assets/mjcf/capabilities/franka/fixed_scene.xml"


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


def test_franka_capability_artifacts_bind_real_model_symbols() -> None:
    design = _json(DESIGN_PATH)
    suite = _json(SUITE_PATH)
    bindings = _json(BINDINGS_PATH)

    assert design["robot_configuration_id"] == "franka"
    assert design["scene_path"] == "assets/mjcf/capabilities/franka/fixed_scene.xml"
    assert design["robot_bindings_path"].endswith("franka/robot_bindings.json")
    assert [item["capability_id"] for item in design["capabilities"]] == [
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
    ]
    assert all("SO-101" not in item["description"] for item in design["capabilities"])

    assert suite["robot_configuration_id"] == "franka"
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
    assert all(
        item["measurement_binding"].get("contract_id") == item["capability_id"]
        for item in suite["cases"]
    )
    assert all(
        item["measurement_binding"].get("max_penetration_m") == 0.005
        for item in suite["cases"]
        if item["capability_id"] == "A4"
    )

    assert bindings["robot_configuration_id"] == "franka"
    assert bindings["arm_joint_names"] == [f"joint{index}" for index in range(1, 8)]
    assert bindings["arm_actuator_names"] == [
        f"actuator{index}" for index in range(1, 8)
    ]
    assert bindings["gripper_actuator_name"] == "actuator8"

    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (model.nq, model.nv, model.nu, model.nmocap, model.nkey) == (9, 9, 8, 1, 1)
    for name in ("fixed_tcp", "fixed_floor", "fixed_contact_geom"):
        object_type = (
            mujoco.mjtObj.mjOBJ_SITE
            if name == "fixed_tcp"
            else mujoco.mjtObj.mjOBJ_GEOM
        )
        assert mujoco.mj_name2id(model, object_type, name) >= 0
    for name in (
        "geom_65",
        "geom_68",
        "geom_69",
        "geom_70",
        "geom_71",
        "geom_72",
        "geom_73",
        "geom_76",
        "geom_77",
        "geom_78",
        "geom_79",
        "geom_80",
        "geom_81",
    ):
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name) >= 0
    assert np.allclose(
        model.actuator_ctrlrange[:7],
        np.asarray(
            [
                [-2.8973, 2.8973],
                [-1.7628, 1.7628],
                [-2.8973, 2.8973],
                [-3.0718, -0.0698],
                [-2.8973, 2.8973],
                [-0.0175, 3.7525],
                [-2.8973, 2.8973],
            ]
        ),
    )


def test_franka_a1_nominal_reference_control_reaches_real_target() -> None:
    suite = _json(SUITE_PATH)
    case = next(item for item in suite["cases"] if item["case_id"] == "franka_panda-a1-nominal")
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    _reset_home(model, data, case)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "fixed_tcp")
    q1_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "finger_joint1")
    q2_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "finger_joint2")
    q1_address = int(model.jnt_qposadr[q1_id])
    q2_address = int(model.jnt_qposadr[q2_id])
    initial_fingers = data.qpos[[q1_address, q2_address]].copy()

    ReferenceFrankaDriver(model=model, data=data).move_end_effector_to_position(
        case["request"]
    )

    target = np.asarray(case["request"]["target_position_m"], dtype=float)
    error = float(np.linalg.norm(data.site_xpos[site_id] - target))
    assert data.time >= 0.5 + case["request"]["max_duration_s"] + 0.5 - 1e-9
    assert error <= 0.015
    assert np.max(np.abs(data.qpos[[q1_address, q2_address]] - initial_fingers)) <= 0.002
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.ctrl).all()
    assert np.all(data.ctrl >= model.actuator_ctrlrange[:, 0] - 1e-12)
    assert np.all(data.ctrl <= model.actuator_ctrlrange[:, 1] + 1e-12)
