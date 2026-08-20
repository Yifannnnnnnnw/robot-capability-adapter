from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.trusted_skeletons import ArmSerialDLSSkeleton, ArmSpec


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "aloha_2" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
REACH_SCENE = ASSETS_ROOT / "reach_scene.xml"
RIGHT_ARM_JOINTS = tuple(
    f"right/{name}"
    for name in (
        "waist",
        "shoulder",
        "elbow",
        "forearm_roll",
        "wrist_angle",
        "wrist_rotate",
    )
)
RIGHT_NEUTRAL_Q = (0.0, -0.96, 1.16, 0.0, -0.3, 0.0)
REACH_TARGET = np.asarray((0.20, 0.10, 0.20), dtype=float)


def _name_id(model: mujoco.MjModel, object_type: mujoco.mjtObj, name: str) -> int:
    identifier = mujoco.mj_name2id(model, object_type, name)
    assert identifier >= 0, name
    return identifier


def test_aloha_reach_scene_is_local_live_and_initially_fails() -> None:
    root = ET.parse(REACH_SCENE).getroot()
    assert root.get("model") == "aloha_2_reach"
    includes = root.findall("include")
    assert len(includes) == 1
    assert includes[0].get("file") == "scene.xml"
    assert (ASSETS_ROOT / "scene.xml").is_file()

    model = mujoco.MjModel.from_xml_path(str(REACH_SCENE))
    data = mujoco.MjData(model)
    key_id = _name_id(model, mujoco.mjtObj.mjOBJ_KEY, "neutral_pose")
    mujoco.mj_resetDataKeyframe(model, data, key_id)
    mujoco.mj_forward(model, data)

    assert model.vis.global_.offwidth >= 800
    assert model.vis.global_.offheight >= 600
    assert _name_id(model, mujoco.mjtObj.mjOBJ_CAMERA, "evidence") >= 0
    target_site = _name_id(model, mujoco.mjtObj.mjOBJ_SITE, "reach_goal_site")
    gripper_site = _name_id(model, mujoco.mjtObj.mjOBJ_SITE, "right/gripper")
    np.testing.assert_allclose(data.site_xpos[target_site], REACH_TARGET, atol=0.0)
    assert np.linalg.norm(data.site_xpos[gripper_site] - REACH_TARGET) > 0.05
    assert data.ncon == 0

    initial_qpos = np.asarray(data.qpos, dtype=float).copy()
    limits = {
        name: model.jnt_range[
            _name_id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        ].tolist()
        for name in RIGHT_ARM_JOINTS
    }
    skeleton = ArmSerialDLSSkeleton.from_session(
        model=model,
        data=data,
        spec=ArmSpec(
            ee_site_name="right/gripper",
            arm_joint_names=RIGHT_ARM_JOINTS,
            arm_actuator_names=RIGHT_ARM_JOINTS,
            joint_limits=limits,
            home_qpos=RIGHT_NEUTRAL_Q,
            ik_max_iter=1000,
            ik_tolerance=0.002,
            ik_step_clamp=0.12,
            gripper_actuator_names=("right/gripper",),
            gripper_close_ctrl=0.002,
            gripper_open_ctrl=0.037,
        ),
    )
    solved = skeleton.ik(REACH_TARGET, q_init=RIGHT_NEUTRAL_Q)
    np.testing.assert_allclose(skeleton.fk(solved)["pos"], REACH_TARGET, atol=0.002)
    np.testing.assert_array_equal(data.qpos, initial_qpos)

    for _ in range(250):
        mujoco.mj_step(model, data)
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
