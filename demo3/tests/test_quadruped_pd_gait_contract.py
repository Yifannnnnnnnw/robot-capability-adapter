from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.driver_synthesis import discover_primitives
from autoadapter2.trusted_skeletons.quadruped_pd_gait import (
    QuadrupedPDGaitSkeleton,
    QuadrupedSpec,
)


ROOT = Path(__file__).resolve().parents[1]
SCENE = (
    ROOT
    / "research_candidates"
    / "unitree-go2-stock-12dof"
    / "1.0.0"
    / "assets"
    / "go2_scene.xml"
)
LEGS = ("FL", "FR", "RL", "RR")
JOINTS = {
    leg: [f"{leg}_hip_joint", f"{leg}_thigh_joint", f"{leg}_calf_joint"]
    for leg in LEGS
}
ACTUATORS = {
    leg: [f"{leg}_hip", f"{leg}_thigh", f"{leg}_calf"] for leg in LEGS
}


def _go2_spec() -> QuadrupedSpec:
    return QuadrupedSpec(
        base_body_name="base_link",
        leg_joint_names=JOINTS,
        leg_actuator_names=ACTUATORS,
        home_qpos=[0.0, 0.9, -1.8] * 4,
        kp=80.0,
        kd=4.0,
        body_height_target=0.27,
    )


class QuadrupedContractTests(unittest.TestCase):
    def test_spec_is_validated_and_normalized(self) -> None:
        spec = _go2_spec()

        self.assertEqual(tuple(spec.leg_joint_names), LEGS)
        self.assertEqual(spec.home_qpos, (0.0, 0.9, -1.8) * 4)
        self.assertEqual(spec.thigh_forward_sign, -1.0)

        with self.assertRaisesRegex(ValueError, "exactly 4 legs"):
            QuadrupedSpec(
                base_body_name="base",
                leg_joint_names={"FL": JOINTS["FL"]},
                leg_actuator_names={"FL": ACTUATORS["FL"]},
                home_qpos=[0.0, 0.9, -1.8],
            )

        with self.assertRaisesRegex(ValueError, "home_qpos must contain 12"):
            QuadrupedSpec(
                base_body_name="base",
                leg_joint_names=JOINTS,
                leg_actuator_names=ACTUATORS,
                home_qpos=[0.0] * 11,
            )

    def test_primitive_surface_stays_capability_neutral(self) -> None:
        primitive_names = {item.name for item in discover_primitives(QuadrupedPDGaitSkeleton)}

        self.assertTrue(
            {
                "get_base_pose",
                "get_base_velocity",
                "get_joint_positions",
                "get_joint_velocities",
                "apply_pd_posture",
                "set_joint_torques",
                "move_to_posture",
                "stand_up",
                "sit",
                "stop",
                "command_planar_velocity",
                "walk_forward",
            }.issubset(primitive_names)
        )
        self.assertNotIn("build", primitive_names)
        self.assertNotIn("step", primitive_names)

        source = inspect.getsource(QuadrupedPDGaitSkeleton)
        self.assertNotIn("capability_design.json", source)
        self.assertNotIn("TaskPlanner", source)
        self.assertNotIn("from_xml_path", source)
        self.assertNotIn("MjData", source)
        self.assertNotIn("mj_resetData", source)
        self.assertNotIn("MethodType", source)
        self.assertNotIn("setattr", source)

    def test_skeleton_has_no_qpos_or_qvel_assignment(self) -> None:
        source = inspect.getsource(QuadrupedPDGaitSkeleton)
        tree = ast.parse(source)

        class StateWriteVisitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.writes: list[str] = []

            def visit_Assign(self, node: ast.Assign) -> None:
                for target in node.targets:
                    self._check_target(target)
                self.generic_visit(node.value)

            def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
                self._check_target(node.target)
                self.generic_visit(node.value)

            def visit_AugAssign(self, node: ast.AugAssign) -> None:
                self._check_target(node.target)
                self.generic_visit(node.value)

            def _check_target(self, node: ast.AST) -> None:
                if isinstance(node, ast.Subscript):
                    self._check_target(node.value)
                elif isinstance(node, ast.Attribute) and node.attr in {"qpos", "qvel"}:
                    if isinstance(node.value, ast.Attribute) and node.value.attr == "data":
                        self.writes.append(node.attr)

        visitor = StateWriteVisitor()
        visitor.visit(tree)
        self.assertEqual(visitor.writes, [])


class QuadrupedMuJoCoSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = mujoco.MjModel.from_xml_path(str(SCENE))
        self.data = mujoco.MjData(self.model)
        key = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_KEY, "home")
        self.assertGreaterEqual(key, 0)
        mujoco.mj_resetDataKeyframe(self.model, self.data, key)
        mujoco.mj_forward(self.model, self.data)
        self.skeleton = QuadrupedPDGaitSkeleton.from_session(
            model=self.model,
            data=self.data,
            spec=_go2_spec(),
        )

    def test_canonical_session_ctrl_and_physics_step(self) -> None:
        self.assertIs(self.skeleton.model, self.model)
        self.assertIs(self.skeleton.data, self.data)

        position, rotation = self.skeleton.get_base_pose()
        velocity = self.skeleton.get_base_velocity()
        self.assertEqual(position.shape, (3,))
        self.assertEqual(rotation.shape, (3, 3))
        self.assertEqual(velocity["linear"].shape, (3,))
        self.assertEqual(velocity["angular"].shape, (3,))

        qpos_before = self.data.qpos.copy()
        qvel_before = self.data.qvel.copy()
        time_before = float(self.data.time)
        torque_command = np.linspace(-1.0, 1.0, 12)
        self.skeleton.set_joint_torques(torque_command)

        self.assertTrue(np.array_equal(self.data.qpos, qpos_before))
        self.assertTrue(np.array_equal(self.data.qvel, qvel_before))
        self.assertAlmostEqual(float(self.data.time), time_before)
        actuator_ids = self.skeleton._actuator_ids
        np.testing.assert_allclose(self.data.ctrl[actuator_ids], torque_command)

        self.skeleton.step(5)
        self.assertGreater(float(self.data.time), time_before)


if __name__ == "__main__":
    unittest.main()
