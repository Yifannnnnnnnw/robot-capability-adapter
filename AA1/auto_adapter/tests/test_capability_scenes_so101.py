# SPDX-License-Identifier: Apache-2.0
"""Focused SO-101 capability binding and native A1 physics checks."""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter_bench.reference_controls.so101 import ReferenceSO101Driver


AA1_ROOT = Path(__file__).resolve().parents[2]
SCENE_PATH = AA1_ROOT / "assets/mjcf/capabilities/so101/fixed_scene.xml"
BINDINGS_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/so101/robot_bindings.json"
SUITE_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/so101/capability_validation_suite.json"


def _binding() -> dict:
    return json.loads(BINDINGS_PATH.read_text(encoding="utf-8"))


def _reset(model: mujoco.MjModel, data: mujoco.MjData, binding: dict) -> None:
    for name, value in binding["home_qpos_by_joint"].items():
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        assert joint_id >= 0
        data.qpos[model.jnt_qposadr[joint_id]] = value
    for name, value in {
        **dict(zip(binding["arm_actuator_names"], [0.0] * 5)),
        binding["gripper_actuator_name"]: binding["gripper_closed_ctrl"],
    }.items():
        actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
        assert actuator_id >= 0
        data.ctrl[actuator_id] = value
    mujoco.mj_forward(model, data)


def test_so101_binding_scene_and_suite_are_consistent() -> None:
    binding = _binding()
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))

    assert binding["robot_configuration_id"] == "so101"
    assert binding["canonical_source"] == "AA1/assets/mjcf/so101_mujoco.xml"
    assert model.nu == len(binding["arm_actuator_names"]) + 1
    assert binding["site_name"] == "ee_site"
    assert binding["tool_geom_names"] == ["so101_tcp_contact"]
    assert binding["target_geom_names"] == ["fixed_contact_geom"]
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, binding["site_name"]) >= 0
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, binding["base_body_name"]) >= 0
    for name in [*binding["arm_joint_names"], binding["gripper_joint_name"], *binding["arm_actuator_names"], binding["gripper_actuator_name"]]:
        object_type = (
            mujoco.mjtObj.mjOBJ_JOINT
            if "joint" in name or name in binding["arm_joint_names"]
            else mujoco.mjtObj.mjOBJ_ACTUATOR
        )
        assert mujoco.mj_name2id(model, object_type, name) >= 0

    suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    cases = suite["cases"]
    assert len(cases) == 10
    assert {case["capability_id"] for case in cases} == {"A1", "A2", "A3", "A4", "A5"}
    assert all(case["source_instance"].startswith("AA2 fixed_family_v1/robotstudio_so101/") for case in cases)


def test_so101_a1_reaches_target_with_native_actuators() -> None:
    binding = _binding()
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    _reset(model, data, binding)
    for _ in range(round(0.5 / model.opt.timestep)):
        mujoco.mj_step(model, data)

    driver = ReferenceSO101Driver(model=model, data=data)
    jaw_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, binding["gripper_joint_name"])
    jaw_address = model.jnt_qposadr[jaw_id]
    jaw_start = float(data.qpos[jaw_address])
    target = np.array([0.4, 0.02, 0.24], dtype=float)
    driver.move_end_effector_to_position(
        {"target_position_m": target.tolist(), "max_duration_s": 4.0}
    )
    ee_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, binding["site_name"])
    error = float(np.linalg.norm(np.asarray(data.site_xpos[ee_id]) - target))

    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.ctrl).all()
    assert error <= 0.015
    assert data.time >= 4.999
    assert abs(float(data.qpos[jaw_address]) - jaw_start) <= 0.002
    for index in range(model.nu):
        assert model.actuator_ctrlrange[index, 0] - 1.0e-12 <= data.ctrl[index] <= model.actuator_ctrlrange[index, 1] + 1.0e-12
