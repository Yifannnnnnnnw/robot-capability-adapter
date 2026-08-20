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
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "piper" / "1.0.0"
SKELETON_PATH = PACKAGE_ROOT / "skeleton" / "arm_serial_dls.py"
ARM_JOINTS = tuple(f"joint{index}" for index in range(1, 7))
HOME_ARM = (0.0, 1.57, -1.3485, 0.0, 0.0, 0.0)


def _load_package_skeleton():
    spec = importlib.util.spec_from_file_location("piper_package_skeleton", SKELETON_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_piper_package_exports_only_the_capability_neutral_arm_family() -> None:
    module = _load_package_skeleton()
    assert module.ArmSerialDLSSkeleton is ArmSerialDLSSkeleton
    assert module.ArmSpec is ArmSpec
    assert module.IKUnreachableError is IKUnreachableError
    assert module.__all__ == ["ArmSerialDLSSkeleton", "ArmSpec", "IKUnreachableError"]

    source = SKELETON_PATH.read_text(encoding="utf-8").lower()
    for forbidden in ("task_id", "trajectory", "target_position", "reference"):
        assert forbidden not in source


def test_piper_morphology_builds_a_live_session_bound_skeleton() -> None:
    module = _load_package_skeleton()
    morphology = json.loads(
        (PACKAGE_ROOT / "morphology.json").read_text(encoding="utf-8")
    )
    control = morphology["public_control"]
    observations = morphology["public_observations"]
    model = mujoco.MjModel.from_xml_path(str(PACKAGE_ROOT / "assets" / "scene.xml"))
    data = mujoco.MjData(model)
    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    mujoco.mj_forward(model, data)

    skeleton = module.ArmSerialDLSSkeleton.from_session(
        model=model,
        data=data,
        spec=module.ArmSpec(
            ee_site_name=observations["end_effector_site"],
            arm_joint_names=ARM_JOINTS,
            arm_actuator_names=ARM_JOINTS,
            joint_limits=control["arm_joint_limits_rad"],
            home_qpos=HOME_ARM,
            ik_max_iter=500,
            ik_tolerance=0.003,
            ik_step_clamp=0.15,
            gripper_actuator_names=("gripper",),
            gripper_close_ctrl=0.0,
            gripper_open_ctrl=0.035,
        ),
    )

    position, rotation = skeleton.get_ee_pose()
    assert position.shape == (3,)
    assert rotation.shape == (3, 3)
    assert np.isfinite(position).all()
    assert np.isfinite(rotation).all()
    np.testing.assert_allclose(skeleton.get_joint_positions(), HOME_ARM, atol=1e-8)

    solved = skeleton.ik(position, q_init=HOME_ARM)
    np.testing.assert_allclose(skeleton.fk(solved)["pos"], position, atol=0.003)

    start_time = float(data.time)
    gripper_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, "gripper")
    assert skeleton.gripper_open(settle_steps=2) is True
    assert data.ctrl[gripper_id] == 0.035
    assert skeleton.gripper_close(settle_steps=2) is True
    assert data.ctrl[gripper_id] == 0.0
    assert data.time > start_time
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.ctrl).all()
