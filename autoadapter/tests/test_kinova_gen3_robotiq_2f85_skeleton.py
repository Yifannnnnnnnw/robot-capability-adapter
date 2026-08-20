from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.trusted_skeletons import (
    ArmSerialDLSSkeleton,
    ArmSpec,
    IKUnreachableError,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "kinova_gen3_robotiq_2f85" / "1.0.0"
SKELETON_PATH = PACKAGE_ROOT / "skeleton" / "arm_serial_dls.py"
ARM_JOINTS = tuple(f"joint_{index}" for index in range(1, 8))
UNLIMITED_JOINTS = ("joint_1", "joint_3", "joint_5", "joint_7")


def _load_package_skeleton():
    spec = importlib.util.spec_from_file_location("kinova_package_skeleton", SKELETON_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kinova_package_exports_only_the_capability_neutral_arm_family() -> None:
    module = _load_package_skeleton()
    assert module.ArmSerialDLSSkeleton is ArmSerialDLSSkeleton
    assert module.ArmSpec is ArmSpec
    assert module.IKUnreachableError is IKUnreachableError
    assert module.__all__ == ["ArmSerialDLSSkeleton", "ArmSpec", "IKUnreachableError"]

    source = SKELETON_PATH.read_text(encoding="utf-8").lower()
    for forbidden in ("task_id", "trajectory", "target_position", "reference"):
        assert forbidden not in source


def test_kinova_public_morphology_builds_a_live_continuous_joint_skeleton() -> None:
    module = _load_package_skeleton()
    morphology = json.loads((PACKAGE_ROOT / "morphology.json").read_text(encoding="utf-8"))
    control = morphology["public_control"]
    observations = morphology["public_observations"]

    assert tuple(control["unlimited_joint_names"]) == UNLIMITED_JOINTS
    assert tuple(control["unlimited_actuator_names"]) == UNLIMITED_JOINTS
    assert set(control["arm_joint_limits_rad"]) == {"joint_2", "joint_4", "joint_6"}

    model = mujoco.MjModel.from_xml_path(str(PACKAGE_ROOT / "assets" / "scene.xml"))
    data = mujoco.MjData(model)
    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    mujoco.mj_forward(model, data)
    home_arm = tuple(float(value) for value in model.key_qpos[home_id, :7])

    skeleton = module.ArmSerialDLSSkeleton.from_session(
        model=model,
        data=data,
        spec=module.ArmSpec(
            ee_site_name=observations["end_effector_site"],
            arm_joint_names=ARM_JOINTS,
            arm_actuator_names=ARM_JOINTS,
            joint_limits=control["arm_joint_limits_rad"],
            continuous_joint_names=control["unlimited_joint_names"],
            home_qpos=home_arm,
            ik_max_iter=500,
            ik_tolerance=0.003,
            ik_step_clamp=0.15,
            gripper_actuator_names=("fingers_actuator",),
            gripper_close_ctrl=255.0,
            gripper_open_ctrl=0.0,
        ),
    )

    position, rotation = skeleton.get_ee_pose()
    assert position.shape == (3,)
    assert rotation.shape == (3, 3)
    assert np.isfinite(position).all()
    assert np.isfinite(rotation).all()
    np.testing.assert_allclose(skeleton.get_joint_positions(), home_arm, atol=1e-8)

    solved = skeleton.ik(position, q_init=home_arm)
    np.testing.assert_allclose(skeleton.fk(solved)["pos"], position, atol=0.003)

    start_time = float(data.time)
    assert skeleton.gripper_close(settle_steps=2) is True
    gripper_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, "fingers_actuator")
    assert data.ctrl[gripper_id] == 255.0
    assert skeleton.gripper_open(settle_steps=2) is True
    assert data.ctrl[gripper_id] == 0.0
    assert data.time > start_time
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.ctrl).all()
