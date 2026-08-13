"""Smoke checks for the vendored AutoAdapter 1.0 execution substrate."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_CORE = REPO_ROOT / "demo2" / "legacy_core"
LEGACY_ASSETS = REPO_ROOT / "demo2" / "legacy_assets"
sys.path.insert(0, str(LEGACY_CORE))

import mujoco

from auto_adapter.skeletons import (
    ArmSerialDLSSkeleton,
    ArmSpec,
    QuadrupedPDGaitSkeleton,
    QuadrupedSpec,
)


MODEL_PATHS = {
    "so101": LEGACY_ASSETS / "robotstudio_so101" / "scene.xml",
    "go2": LEGACY_ASSETS / "go2" / "go2_scene.xml",
    "franka": LEGACY_ASSETS / "franka_panda" / "scene.xml",
}


def _load_and_step(model_path: Path) -> tuple[mujoco.MjModel, mujoco.MjData]:
    assert model_path.is_relative_to(LEGACY_ASSETS)
    assert model_path.exists(), f"missing MJCF: {model_path}"

    model = mujoco.MjModel.from_xml_path(str(model_path))
    data = mujoco.MjData(model)
    mujoco.mj_step(model, data)

    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    return model, data


def _joint_limits(model: mujoco.MjModel, names: list[str]) -> dict[str, tuple[float, float]]:
    return {
        name: tuple(
            float(value)
            for value in model.jnt_range[
                mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            ]
        )
        for name in names
    }


def test_selected_mjcfs_load_and_take_finite_direct_step() -> None:
    """The three copied 1.0 robot closures execute one raw MuJoCo step."""
    observed = {}
    for robot_name, model_path in MODEL_PATHS.items():
        model, data = _load_and_step(model_path)
        observed[robot_name] = (model.nq, model.nv, model.nu)

    assert all(nq > 0 and nv > 0 for nq, nv, _ in observed.values())


def test_arm_skeleton_family_instantiates_on_so101_and_franka() -> None:
    """The 1.0 arm skeleton resolves both site- and body-based end effectors."""
    assert ArmSerialDLSSkeleton.__name__ == "ArmSerialDLSSkeleton"
    assert set(inspect.signature(ArmSerialDLSSkeleton.from_mjcf).parameters) == {
        "mjcf_path",
        "spec",
    }

    arm_cases = {
        "so101": {
            "path": MODEL_PATHS["so101"],
            "spec": {
                "ee_site_name": "gripperframe",
                "arm_joint_names": [
                    "shoulder_pan",
                    "shoulder_lift",
                    "elbow_flex",
                    "wrist_flex",
                    "wrist_roll",
                ],
                "arm_actuator_names": [
                    "shoulder_pan",
                    "shoulder_lift",
                    "elbow_flex",
                    "wrist_flex",
                    "wrist_roll",
                ],
            },
        },
        "franka": {
            "path": MODEL_PATHS["franka"],
            "spec": {
                "ee_body_name": "hand",
                "arm_joint_names": [f"joint{i}" for i in range(1, 8)],
                "arm_actuator_names": [f"actuator{i}" for i in range(1, 8)],
            },
        },
    }

    for robot_name, case in arm_cases.items():
        model, _ = _load_and_step(case["path"])
        joint_names = case["spec"]["arm_joint_names"]
        spec = ArmSpec(
            **case["spec"],
            joint_limits=_joint_limits(model, joint_names),
            grasp_backend="noop",
        )
        arm = ArmSerialDLSSkeleton.from_mjcf(str(case["path"]), spec)
        try:
            ee_pos, ee_rot = arm.get_ee_pose()
            arm.step(1)
            assert arm.dof == len(joint_names), robot_name
            assert np.isfinite(ee_pos).all() and np.isfinite(ee_rot).all()
            assert np.isfinite(arm.data.qpos).all() and np.isfinite(arm.data.qvel).all()
        finally:
            arm.close()


def test_quadruped_skeleton_family_instantiates_on_go2() -> None:
    """The 1.0 quadruped skeleton resolves Go2's 12 hinge/torque channels."""
    assert QuadrupedPDGaitSkeleton.__name__ == "QuadrupedPDGaitSkeleton"
    assert set(inspect.signature(QuadrupedPDGaitSkeleton.from_mjcf).parameters) == {
        "mjcf_path",
        "spec",
    }

    leg_names = {
        "FL": ["FL_hip_joint", "FL_thigh_joint", "FL_calf_joint"],
        "FR": ["FR_hip_joint", "FR_thigh_joint", "FR_calf_joint"],
        "RL": ["RL_hip_joint", "RL_thigh_joint", "RL_calf_joint"],
        "RR": ["RR_hip_joint", "RR_thigh_joint", "RR_calf_joint"],
    }
    actuator_names = {
        leg: [joint.removesuffix("_joint") for joint in joints]
        for leg, joints in leg_names.items()
    }
    spec = QuadrupedSpec(
        base_body_name="base_link",
        leg_joint_names=leg_names,
        leg_actuator_names=actuator_names,
        home_qpos=[0.0, 0.9, -1.8] * 4,
    )

    skel = QuadrupedPDGaitSkeleton.from_mjcf(str(MODEL_PATHS["go2"]), spec)
    try:
        skel.step(1)
        assert skel.dof == 12
        assert np.isfinite(skel.data.qpos).all() and np.isfinite(skel.data.qvel).all()
        assert np.isfinite(skel.get_body_height())
    finally:
        skel.close()
