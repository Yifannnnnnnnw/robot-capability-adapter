from __future__ import annotations

import unittest
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.trusted_skeletons import ArmSerialDLSSkeleton, ArmSpec


ROOT = Path(__file__).resolve().parents[1]
SCENE = (
    ROOT
    / "research_candidates"
    / "robotstudio_so101"
    / "1.0.0"
    / "assets"
    / "scene_box.xml"
)
ARM_JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
)


class SO101SkeletonSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = mujoco.MjModel.from_xml_path(str(SCENE))
        self.data = mujoco.MjData(self.model)
        mujoco.mj_forward(self.model, self.data)
        limits = {}
        for name in ARM_JOINTS:
            joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            limits[name] = tuple(float(value) for value in self.model.jnt_range[joint_id])
        self.skeleton = ArmSerialDLSSkeleton.from_session(
            model=self.model,
            data=self.data,
            spec=ArmSpec(
                ee_site_name="gripperframe",
                arm_joint_names=ARM_JOINTS,
                arm_actuator_names=ARM_JOINTS,
                joint_limits=limits,
                gripper_actuator_names=("gripper",),
                gripper_close_ctrl=-0.17453,
                gripper_open_ctrl=1.74533,
                ik_max_iter=60,
            ),
        )

    def test_fk_restores_state_and_joint_motion_steps_physics(self) -> None:
        initial_qpos = self.data.qpos.copy()
        current = self.skeleton.get_joint_positions()
        probe = current.copy()
        probe[0] += 0.01

        pose = self.skeleton.fk(probe)

        self.assertEqual(pose["pos"].shape, (3,))
        np.testing.assert_allclose(self.data.qpos, initial_qpos)

        target = current.copy()
        target[0] += 0.03
        initial_time = float(self.data.time)
        self.skeleton.move_joints(target, duration=0.02)

        shoulder_actuator = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, "shoulder_pan"
        )
        self.assertGreater(float(self.data.time), initial_time)
        self.assertAlmostEqual(float(self.data.ctrl[shoulder_actuator]), float(target[0]))
        self.assertFalse(np.array_equal(self.data.qpos, initial_qpos))


if __name__ == "__main__":
    unittest.main()
