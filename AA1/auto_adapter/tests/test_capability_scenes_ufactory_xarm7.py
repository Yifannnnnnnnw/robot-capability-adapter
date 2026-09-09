"""Focused checks for the independent UFACTORY xArm7 capability fixture."""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter_bench.reference_controls.ufactory_xarm7.reference_arm import (
    ReferenceXArm7Driver,
)


AA1_ROOT = Path(__file__).resolve().parents[2]
DESIGN_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/ufactory_xarm7/capability_design.json"
SUITE_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/ufactory_xarm7/capability_validation_suite.json"
BINDINGS_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/ufactory_xarm7/robot_bindings.json"
SCENE_PATH = AA1_ROOT / "assets/mjcf/capabilities/ufactory_xarm7/fixed_scene.xml"


def _json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _reset_home(model: mujoco.MjModel, data: mujoco.MjData, case: dict) -> None:
    reset = case["reset"]
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
    for _ in range(int(round(float(reset.get("settle_s", 0.0)) / model.opt.timestep))):
        mujoco.mj_step(model, data)


def test_xarm7_capability_artifacts_bind_real_model_symbols() -> None:
    design = _json(DESIGN_PATH)
    suite = _json(SUITE_PATH)
    bindings = _json(BINDINGS_PATH)

    assert design["robot_configuration_id"] == "ufactory_xarm7"
    assert design["scene_path"] == "assets/mjcf/capabilities/ufactory_xarm7/fixed_scene.xml"
    assert design["robot_bindings_path"].endswith("ufactory_xarm7/robot_bindings.json")
    assert [item["capability_id"] for item in design["capabilities"]] == [
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
    ]
    assert all("SO-101" not in item["description"] for item in design["capabilities"])

    assert suite["robot_configuration_id"] == "ufactory_xarm7"
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

    assert bindings["robot_configuration_id"] == "ufactory_xarm7"
    assert bindings["site_name"] == "link_tcp"
    assert bindings["base_body_name"] == "link_base"
    assert bindings["arm_joint_names"] == [f"joint{index}" for index in range(1, 8)]
    assert bindings["arm_actuator_names"] == [f"act{index}" for index in range(1, 8)]
    assert bindings["gripper_actuator_name"] == "gripper"
    assert bindings["gripper_joint_name"] == "left_driver_joint"

    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (model.nq, model.nv, model.nu, model.nmocap, model.nkey) == (13, 13, 8, 1, 1)
    for name in ("link_tcp", "fixed_floor", "fixed_contact_geom"):
        object_type = (
            mujoco.mjtObj.mjOBJ_SITE
            if name == "link_tcp"
            else mujoco.mjtObj.mjOBJ_GEOM
        )
        assert mujoco.mj_name2id(model, object_type, name) >= 0
    for name in (
        "joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "joint7",
        "left_driver_joint", "right_driver_joint",
    ):
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) >= 0
    for name in ("act1", "act2", "act3", "act4", "act5", "act6", "act7", "gripper"):
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name) >= 0
    for name in bindings["tool_geom_names"]:
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name) >= 0
    assert np.allclose(
        model.jnt_range[:7],
        np.asarray(
            [
                [-6.28319, 6.28319],
                [-2.059, 2.0944],
                [-6.28319, 6.28319],
                [-0.19198, 3.927],
                [-6.28319, 6.28319],
                [-1.69297, 3.14159],
                [-6.28319, 6.28319],
            ]
        ),
    )
    assert np.allclose(model.actuator_ctrlrange[:7], model.jnt_range[:7])
    assert np.allclose(model.actuator_gainprm[:7, 0], [1500, 1500, 1000, 1000, 1000, 800, 800])
    assert np.allclose(
        model.actuator_biasprm[:7, :3],
        np.asarray(
            [
                [0, -1500, -150], [0, -1500, -150], [0, -1000, -100],
                [0, -1000, -100], [0, -1000, -100], [0, -800, -80],
                [0, -800, -80],
            ]
        ),
    )


def test_xarm7_a1_nominal_reference_control_reaches_real_target() -> None:
    suite = _json(SUITE_PATH)
    case = next(item for item in suite["cases"] if item["case_id"] == "ufactory_xarm7-a1-nominal")
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    _reset_home(model, data, case)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "link_tcp")
    gripper_joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "left_driver_joint")
    gripper_address = int(model.jnt_qposadr[gripper_joint_id])
    initial_gripper = float(data.qpos[gripper_address])

    ReferenceXArm7Driver(model=model, data=data).move_end_effector_to_position(
        case["request"]
    )

    target = np.asarray(case["request"]["target_position_m"], dtype=float)
    error = float(np.linalg.norm(data.site_xpos[site_id] - target))
    assert data.time >= 0.5 + case["request"]["max_duration_s"] + 0.5 - 1e-9
    assert error <= 0.015
    assert abs(float(data.qpos[gripper_address]) - initial_gripper) <= 0.0425
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.ctrl).all()
    assert np.all(data.ctrl >= model.actuator_ctrlrange[:, 0] - 1e-12)
    assert np.all(data.ctrl <= model.actuator_ctrlrange[:, 1] + 1e-12)
