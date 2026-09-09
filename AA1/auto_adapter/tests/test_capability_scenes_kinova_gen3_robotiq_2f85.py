"""Focused checks for the independent Kinova Gen3 + Robotiq 2F85 fixture."""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter_bench.reference_controls.kinova_gen3_robotiq_2f85.reference_arm import (
    ReferenceKinovaGen3RobotiqDriver,
)


AA1_ROOT = Path(__file__).resolve().parents[2]
DESIGN_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/kinova_gen3_robotiq_2f85/capability_design.json"
SUITE_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/kinova_gen3_robotiq_2f85/capability_validation_suite.json"
BINDINGS_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/kinova_gen3_robotiq_2f85/robot_bindings.json"
SCENE_PATH = AA1_ROOT / "assets/mjcf/capabilities/kinova_gen3_robotiq_2f85/fixed_scene.xml"


def _json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _reset_home(
    model: mujoco.MjModel, data: mujoco.MjData, case: dict
) -> None:
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
    settle_s = float(reset.get("settle_s", 0.0))
    for _ in range(int(round(settle_s / model.opt.timestep))):
        mujoco.mj_step(model, data)


def test_kinova_capability_artifacts_bind_real_model_symbols() -> None:
    design = _json(DESIGN_PATH)
    suite = _json(SUITE_PATH)
    bindings = _json(BINDINGS_PATH)

    assert design["robot_configuration_id"] == "kinova_gen3_robotiq_2f85"
    assert design["scene_path"] == (
        "assets/mjcf/capabilities/kinova_gen3_robotiq_2f85/fixed_scene.xml"
    )
    assert design["robot_bindings_path"].endswith(
        "kinova_gen3_robotiq_2f85/robot_bindings.json"
    )
    assert [item["capability_id"] for item in design["capabilities"]] == [
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
    ]
    assert all("SO-101" not in item["description"] for item in design["capabilities"])

    assert suite["robot_configuration_id"] == "kinova_gen3_robotiq_2f85"
    assert suite["scene_path"] == design["scene_path"]
    assert suite["design_path"].endswith(
        "kinova_gen3_robotiq_2f85/capability_design.json"
    )
    assert suite["robot_bindings_path"] == design["robot_bindings_path"]
    assert suite["source_suite_path"].endswith(
        "fixed_family_v1/kinova_gen3_robotiq_2f85/capability_validation_suite.json"
    )
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

    assert bindings["robot_configuration_id"] == "kinova_gen3_robotiq_2f85"
    assert bindings["site_name"] == "pinch_site"
    assert bindings["base_body_name"] == "base_link"
    assert bindings["arm_joint_names"] == [f"joint_{index}" for index in range(1, 8)]
    assert bindings["arm_actuator_names"] == [
        f"joint_{index}" for index in range(1, 8)
    ]
    assert bindings["gripper_actuator_name"] == "fingers_actuator"
    assert bindings["gripper_joint_name"] == "right_driver_joint"

    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (model.nq, model.nv, model.nu, model.nmocap, model.nkey) == (
        15,
        15,
        8,
        1,
        2,
    )
    for name in ("pinch_site", "fixed_floor", "fixed_contact_geom"):
        object_type = (
            mujoco.mjtObj.mjOBJ_SITE
            if name == "pinch_site"
            else mujoco.mjtObj.mjOBJ_GEOM
        )
        assert mujoco.mj_name2id(model, object_type, name) >= 0
    for name in bindings["arm_joint_names"] + [
        "right_driver_joint",
        "left_driver_joint",
    ]:
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) >= 0
    for name in bindings["tool_geom_names"]:
        # MuJoCo leaves the source mesh collision geoms unnamed; the trusted
        # evaluator exposes their stable ``geom_<id>`` fallback names.
        if name.startswith("geom_"):
            assert int(name.removeprefix("geom_")) < model.ngeom
        else:
            assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name) >= 0
    assert np.allclose(
        model.jnt_range[:7],
        np.asarray(
            [
                [0.0, 0.0],
                [-2.24, 2.24],
                [0.0, 0.0],
                [-2.57, 2.57],
                [0.0, 0.0],
                [-2.09, 2.09],
                [0.0, 0.0],
            ]
        ),
    )
    assert np.allclose(model.actuator_gainprm[:7, 0], [2000, 2000, 2000, 2000, 500, 500, 500])
    assert np.allclose(
        model.actuator_biasprm[:7, :3],
        np.asarray(
            [
                [0, -2000, -100],
                [0, -2000, -100],
                [0, -2000, -100],
                [0, -2000, -100],
                [0, -500, -50],
                [0, -500, -50],
                [0, -500, -50],
            ]
        ),
    )
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_TENDON, "split") >= 0


def test_kinova_a1_nominal_reference_control_reaches_real_target() -> None:
    suite = _json(SUITE_PATH)
    case = next(
        item
        for item in suite["cases"]
        if item["case_id"] == "kinova_gen3_robotiq_2f85-a1-nominal"
    )
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    _reset_home(model, data, case)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "pinch_site")
    gripper_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_JOINT, "right_driver_joint"
    )
    gripper_address = int(model.jnt_qposadr[gripper_id])
    initial_gripper = float(data.qpos[gripper_address])

    ReferenceKinovaGen3RobotiqDriver(model=model, data=data).move_end_effector_to_position(
        case["request"]
    )

    target = np.asarray(case["request"]["target_position_m"], dtype=float)
    error = float(np.linalg.norm(data.site_xpos[site_id] - target))
    assert data.time >= 0.5 + case["request"]["max_duration_s"] + 0.5 - 1e-9
    assert error <= 0.015
    assert abs(float(data.qpos[gripper_address]) - initial_gripper) <= 0.04
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.ctrl).all()
    assert np.all(
        data.ctrl[model.actuator_ctrllimited.astype(bool)]
        >= model.actuator_ctrlrange[model.actuator_ctrllimited.astype(bool), 0] - 1e-12
    )
    assert np.all(
        data.ctrl[model.actuator_ctrllimited.astype(bool)]
        <= model.actuator_ctrlrange[model.actuator_ctrllimited.astype(bool), 1] + 1e-12
    )
