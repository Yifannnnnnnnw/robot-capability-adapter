"""Focused UR5e + Robotiq AA1 capability binding and native A1 checks."""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter_bench.reference_controls.universal_robots_ur5e_robotiq_2f85.reference_arm import (
    ReferenceUniversalRobotsUR5eRobotiqDriver,
)


AA1_ROOT = Path(__file__).resolve().parents[2]
PROFILE_ROOT = AA1_ROOT / "autoadapter_bench/spec/capabilities/universal_robots_ur5e_robotiq_2f85"
DESIGN_PATH = PROFILE_ROOT / "capability_design.json"
SUITE_PATH = PROFILE_ROOT / "capability_validation_suite.json"
BINDINGS_PATH = PROFILE_ROOT / "robot_bindings.json"
SCENE_PATH = AA1_ROOT / "assets/mjcf/capabilities/universal_robots_ur5e_robotiq_2f85/fixed_scene.xml"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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


def test_ur5e_robotiq_capability_artifacts_bind_real_model_symbols() -> None:
    design = _json(DESIGN_PATH)
    suite = _json(SUITE_PATH)
    bindings = _json(BINDINGS_PATH)

    robot_id = "universal_robots_ur5e_robotiq_2f85"
    assert design["robot_configuration_id"] == robot_id
    assert design["scene_path"] == (
        "assets/mjcf/capabilities/universal_robots_ur5e_robotiq_2f85/fixed_scene.xml"
    )
    assert design["robot_bindings_path"].endswith(
        "universal_robots_ur5e_robotiq_2f85/robot_bindings.json"
    )
    assert [item["capability_id"] for item in design["capabilities"]] == [
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
    ]
    assert all(
        "SO-101" not in item["description"] for item in design["capabilities"]
    )

    assert suite["robot_configuration_id"] == robot_id
    assert suite["scene_path"] == design["scene_path"]
    assert suite["design_path"] == "autoadapter_bench/spec/capabilities/universal_robots_ur5e_robotiq_2f85/capability_design.json"
    assert len(suite["cases"]) == 10
    assert {item["capability_id"] for item in suite["cases"]} == {
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
    }
    assert all(item["reset"].get("settle_s", 0.0) > 0.0 for item in suite["cases"])
    assert all(
        item["measurement_binding"].get("contract_id") == item["capability_id"]
        for item in suite["cases"]
    )
    assert all(
        item["measurement_binding"].get("max_penetration_m") == 0.005
        for item in suite["cases"]
        if item["capability_id"] == "A4"
    )

    assert bindings["robot_configuration_id"] == robot_id
    assert bindings["site_name"] == "pinch_site"
    assert bindings["base_body_name"] == "base"
    assert bindings["arm_joint_names"] == [
        "shoulder_pan_joint",
        "shoulder_lift_joint",
        "elbow_joint",
        "wrist_1_joint",
        "wrist_2_joint",
        "wrist_3_joint",
    ]
    assert bindings["arm_actuator_names"] == [
        "shoulder_pan",
        "shoulder_lift",
        "elbow",
        "wrist_1",
        "wrist_2",
        "wrist_3",
    ]
    assert bindings["gripper_actuator_name"] == "fingers_actuator"
    assert bindings["gripper_joint_name"] == "right_driver_joint"

    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (model.nq, model.nv, model.nu, model.nmocap, model.nkey) == (
        14,
        14,
        7,
        1,
        1,
    )
    assert model.opt.timestep == 0.002
    for name in ("pinch_site", "fixed_floor", "fixed_contact_geom"):
        object_type = (
            mujoco.mjtObj.mjOBJ_SITE
            if name == "pinch_site"
            else mujoco.mjtObj.mjOBJ_GEOM
        )
        assert mujoco.mj_name2id(model, object_type, name) >= 0
    assert mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_BODY, "fixed_contact_target"
    ) >= 0
    for name in [*bindings["arm_joint_names"], "right_driver_joint", "left_driver_joint"]:
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) >= 0
    for name in [*bindings["arm_actuator_names"], "fingers_actuator"]:
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name) >= 0
    for name in bindings["tool_geom_names"]:
        if name.startswith("geom_"):
            assert 0 <= int(name.removeprefix("geom_")) < model.ngeom
        else:
            assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name) >= 0

    assert np.allclose(
        model.jnt_range[:6],
        np.asarray(
            [
                [-6.28319, 6.28319],
                [-6.28319, 6.28319],
                [-3.1415, 3.1415],
                [-6.28319, 6.28319],
                [-6.28319, 6.28319],
                [-6.28319, 6.28319],
            ]
        ),
    )
    assert np.allclose(
        model.actuator_ctrlrange[:6],
        np.asarray(
            [
                [-6.2831, 6.2831],
                [-6.2831, 6.2831],
                [-3.1415, 3.1415],
                [-6.2831, 6.2831],
                [-6.2831, 6.2831],
                [-6.2831, 6.2831],
            ]
        ),
    )
    assert np.allclose(model.actuator_gainprm[:6, 0], [2000, 2000, 2000, 500, 500, 500])
    assert np.allclose(
        model.actuator_biasprm[:6, :3],
        np.asarray(
            [
                [0, -2000, -400],
                [0, -2000, -400],
                [0, -2000, -400],
                [0, -500, -100],
                [0, -500, -100],
                [0, -500, -100],
            ]
        ),
    )
    split_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_TENDON, "split")
    assert split_id >= 0
    assert model.ntendon == 1
    assert model.neq >= 3


def test_ur5e_robotiq_a1_nominal_reference_control_reaches_real_target() -> None:
    suite = _json(SUITE_PATH)
    case = next(
        item
        for item in suite["cases"]
        if item["case_id"] == "universal_robots_ur5e_robotiq_2f85-a1-nominal"
    )
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    _reset_home(model, data, case)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "pinch_site")
    gripper_joint_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_JOINT, "right_driver_joint"
    )
    gripper_address = int(model.jnt_qposadr[gripper_joint_id])
    initial_gripper = float(data.qpos[gripper_address])

    ReferenceUniversalRobotsUR5eRobotiqDriver(model=model, data=data).move_end_effector_to_position(
        case["request"]
    )

    target = np.asarray(case["request"]["target_position_m"], dtype=float)
    error = float(np.linalg.norm(data.site_xpos[site_id] - target))
    assert data.time >= 0.5 + case["request"]["max_duration_s"] + 0.5 - 1e-9
    assert error <= 0.015
    assert abs(float(data.qpos[gripper_address]) - initial_gripper) <= 0.04
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.ctrl).all()
    assert np.all(data.ctrl >= model.actuator_ctrlrange[:, 0] - 1e-12)
    assert np.all(data.ctrl <= model.actuator_ctrlrange[:, 1] + 1e-12)
