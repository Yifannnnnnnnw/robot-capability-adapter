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
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "kuka_iiwa_14" / "1.0.0"
SKELETON_PATH = PACKAGE_ROOT / "skeleton" / "arm_serial_dls.py"
ARM_JOINTS = tuple(f"joint{index}" for index in range(1, 8))
ARM_ACTUATORS = tuple(f"actuator{index}" for index in range(1, 8))


def _load_package_skeleton():
    spec = importlib.util.spec_from_file_location("kuka_package_skeleton", SKELETON_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kuka_package_exports_only_the_capability_neutral_arm_family() -> None:
    module = _load_package_skeleton()
    assert module.ArmSerialDLSSkeleton is ArmSerialDLSSkeleton
    assert module.ArmSpec is ArmSpec
    assert module.IKUnreachableError is IKUnreachableError
    assert module.__all__ == ["ArmSerialDLSSkeleton", "ArmSpec", "IKUnreachableError"]

    source = SKELETON_PATH.read_text(encoding="utf-8")
    for forbidden in ("task_id", "trajectory", "target_position", "reference"):
        assert forbidden not in source.lower()


def test_kuka_public_morphology_builds_a_geom_centered_skeleton() -> None:
    module = _load_package_skeleton()
    morphology = json.loads((PACKAGE_ROOT / "morphology.json").read_text(encoding="utf-8"))
    control = morphology["public_control"]
    contact_geom = next(iter(morphology["public_observations"]["contact_geoms"]))

    model = mujoco.MjModel.from_xml_path(str(PACKAGE_ROOT / "assets" / "reach_scene.xml"))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    skeleton = module.ArmSerialDLSSkeleton.from_session(
        model=model,
        data=data,
        spec=module.ArmSpec(
            ee_geom_name=contact_geom,
            arm_joint_names=ARM_JOINTS,
            arm_actuator_names=ARM_ACTUATORS,
            joint_limits=control["arm_joint_limits_rad"],
            home_qpos=morphology["reset_fact"]["home_qpos"],
            ik_max_iter=1000,
            ik_tolerance=0.003,
            ik_step_clamp=0.15,
        ),
    )

    position, rotation = skeleton.get_ee_pose()
    geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, contact_geom)
    np.testing.assert_allclose(position, data.geom_xpos[geom_id], atol=0.0)
    assert rotation.shape == (3, 3)

    target = np.asarray((0.55, 0.10, 0.43))
    solved = skeleton.ik(target, q_init=np.zeros(7))
    np.testing.assert_allclose(skeleton.fk(solved)["pos"], target, atol=0.003)
    assert skeleton.set_gripper(0.0, settle_steps=0) is False
