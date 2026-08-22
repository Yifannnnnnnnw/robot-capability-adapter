from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.driver_synthesis import SessionBoundSkeleton, discover_primitives
from autoadapter2.trusted_skeletons import ArmSerialDLSSkeleton, ArmSpec
from autoadapter2.trusted_skeletons import arm_serial_dls


class ArmSerialDLSContractTests(unittest.TestCase):
    def valid_spec(self) -> ArmSpec:
        return ArmSpec(
            ee_site_name="tool_site",
            arm_joint_names=("joint_1", "joint_2"),
            arm_actuator_names=("actuator_1", "actuator_2"),
            joint_limits={"joint_1": (-1.0, 1.0), "joint_2": (-2.0, 2.0)},
            home_qpos=(0.0, 0.5),
            gripper_actuator_names=("gripper",),
        )

    def test_arm_spec_enforces_minimum_structure(self) -> None:
        with self.assertRaises(ValueError):
            ArmSpec(
                arm_joint_names=("joint_1",),
                arm_actuator_names=("actuator_1",),
                joint_limits={"joint_1": (-1.0, 1.0)},
            )
        with self.assertRaises(ValueError):
            ArmSpec(
                ee_site_name="tool_site",
                arm_joint_names=("joint_1",),
                arm_actuator_names=("actuator_1", "actuator_2"),
                joint_limits={"joint_1": (-1.0, 1.0)},
            )
        with self.assertRaises(ValueError):
            ArmSpec(
                ee_site_name="tool_site",
                arm_joint_names=("joint_1",),
                arm_actuator_names=("actuator_1",),
                joint_limits={},
            )
        with self.assertRaises(ValueError):
            ArmSpec(
                ee_site_name="tool_site",
                ee_geom_name="tool_geom",
                arm_joint_names=("joint_1",),
                arm_actuator_names=("actuator_1",),
                joint_limits={"joint_1": (-1.0, 1.0)},
            )

    def test_spec_normalizes_public_configuration_values(self) -> None:
        spec = ArmSpec(
            ee_body_name="tool_body",
            arm_joint_names=["joint_1"],
            arm_actuator_names=["actuator_1"],
            joint_limits={"joint_1": [-1, 1]},
        )
        self.assertEqual(spec.arm_joint_names, ("joint_1",))
        self.assertEqual(spec.arm_actuator_names, ("actuator_1",))
        self.assertEqual(spec.joint_limits, {"joint_1": (-1.0, 1.0)})
        self.assertEqual(spec.continuous_joint_names, ())

        continuous = ArmSpec(
            ee_body_name="tool_body",
            arm_joint_names=["spin"],
            arm_actuator_names=["spin"],
            joint_limits={},
            continuous_joint_names=["spin"],
        )
        self.assertEqual(continuous.joint_limits, {})
        self.assertEqual(continuous.continuous_joint_names, ("spin",))

        with self.assertRaises(ValueError):
            ArmSpec(
                ee_body_name="tool_body",
                arm_joint_names=["spin"],
                arm_actuator_names=["spin"],
                joint_limits={"spin": (-1.0, 1.0)},
                continuous_joint_names=["spin"],
            )

    def test_continuous_hinge_moves_to_the_nearest_equivalent_target(self) -> None:
        model = mujoco.MjModel.from_xml_string(
            """
            <mujoco>
              <worldbody>
                <body>
                  <joint name="spin" type="hinge"/>
                  <geom name="tool_geom" type="capsule" size="0.01 0.1" pos="0 0.1 0"/>
                </body>
              </worldbody>
              <actuator>
                <position name="spin" joint="spin"/>
              </actuator>
              <keyframe>
                <key name="half_turn" qpos="3.141592653589793" ctrl="3.141592653589793"/>
              </keyframe>
            </mujoco>
            """
        )
        data = mujoco.MjData(model)
        mujoco.mj_resetDataKeyframe(model, data, 0)
        skeleton = ArmSerialDLSSkeleton.from_session(
            model=model,
            data=data,
            spec=ArmSpec(
                ee_geom_name="tool_geom",
                arm_joint_names=("spin",),
                arm_actuator_names=("spin",),
                joint_limits={},
                continuous_joint_names=("spin",),
            ),
        )

        skeleton.move_joints((-np.pi + 0.1,), duration=0.0)
        np.testing.assert_allclose(data.ctrl, (np.pi + 0.1,), atol=1e-12)

    def test_geom_endpoint_drives_fk_and_ik_from_the_geom_center(self) -> None:
        model = mujoco.MjModel.from_xml_string(
            """
            <mujoco>
              <compiler autolimits="true"/>
              <worldbody>
                <body>
                  <joint name="slide" type="slide" axis="1 0 0" range="-1 1"/>
                  <geom name="tool_geom" type="sphere" size="0.01" pos="0 0.2 0"/>
                </body>
              </worldbody>
              <actuator>
                <position name="slide_actuator" joint="slide"/>
              </actuator>
            </mujoco>
            """
        )
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        skeleton = ArmSerialDLSSkeleton.from_session(
            model=model,
            data=data,
            spec=ArmSpec(
                ee_geom_name="tool_geom",
                arm_joint_names=("slide",),
                arm_actuator_names=("slide_actuator",),
                joint_limits={"slide": (-1.0, 1.0)},
                ik_max_iter=50,
                ik_tolerance=1e-6,
            ),
        )

        position, _ = skeleton.get_ee_pose()
        geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "tool_geom")
        np.testing.assert_allclose(position, data.geom_xpos[geom_id], atol=0.0)

        target = position + np.asarray((0.1, 0.0, 0.0))
        solved = skeleton.ik(target, q_init=(0.0,))
        np.testing.assert_allclose(solved, (0.1,), atol=1e-6)
        np.testing.assert_allclose(skeleton.fk(solved)["pos"], target, atol=1e-6)

    def test_pose_ik_recovers_a_reachable_six_dof_pose(self) -> None:
        model = mujoco.MjModel.from_xml_string(
            """
            <mujoco>
              <compiler autolimits="true"/>
              <worldbody>
                <body>
                  <joint name="slide_x" type="slide" axis="1 0 0" range="-1 1"/>
                  <joint name="slide_y" type="slide" axis="0 1 0" range="-1 1"/>
                  <joint name="slide_z" type="slide" axis="0 0 1" range="-1 1"/>
                  <joint name="roll" type="hinge" axis="1 0 0" range="-1 1"/>
                  <joint name="pitch" type="hinge" axis="0 1 0" range="-1 1"/>
                  <joint name="yaw" type="hinge" axis="0 0 1" range="-1 1"/>
                  <geom type="sphere" size="0.01"/>
                  <site name="tool_site" pos="0 0 0.1"/>
                </body>
              </worldbody>
              <actuator>
                <position name="slide_x_act" joint="slide_x"/>
                <position name="slide_y_act" joint="slide_y"/>
                <position name="slide_z_act" joint="slide_z"/>
                <position name="roll_act" joint="roll"/>
                <position name="pitch_act" joint="pitch"/>
                <position name="yaw_act" joint="yaw"/>
              </actuator>
            </mujoco>
            """
        )
        data = mujoco.MjData(model)
        skeleton = ArmSerialDLSSkeleton.from_session(
            model=model,
            data=data,
            spec=ArmSpec(
                ee_site_name="tool_site",
                arm_joint_names=(
                    "slide_x",
                    "slide_y",
                    "slide_z",
                    "roll",
                    "pitch",
                    "yaw",
                ),
                arm_actuator_names=(
                    "slide_x_act",
                    "slide_y_act",
                    "slide_z_act",
                    "roll_act",
                    "pitch_act",
                    "yaw_act",
                ),
                joint_limits={
                    "slide_x": (-1.0, 1.0),
                    "slide_y": (-1.0, 1.0),
                    "slide_z": (-1.0, 1.0),
                    "roll": (-1.0, 1.0),
                    "pitch": (-1.0, 1.0),
                    "yaw": (-1.0, 1.0),
                },
                ik_max_iter=300,
                ik_tolerance=1e-5,
                ik_step_clamp=0.1,
            ),
        )
        reachable_q = np.asarray((0.1, -0.05, 0.2, 0.15, -0.2, 0.3))
        target = skeleton.fk(reachable_q)

        solved = skeleton.ik_pose(
            target["pos"], target["R"], q_init=np.zeros(6)
        )
        solved_pose = skeleton.fk(solved)

        np.testing.assert_allclose(solved_pose["pos"], target["pos"], atol=1e-5)
        np.testing.assert_allclose(solved_pose["R"], target["R"], atol=1e-5)

    def test_cartesian_path_tracks_the_supplied_polyline_instead_of_cutting_to_endpoint(
        self,
    ) -> None:
        model = mujoco.MjModel.from_xml_string(
            """
            <mujoco>
              <compiler autolimits="true"/>
              <option timestep="0.002" gravity="0 0 0"/>
              <default>
                <joint damping="2"/>
                <position kp="300"/>
              </default>
              <worldbody>
                <body>
                  <joint name="slide_x" type="slide" axis="1 0 0" range="-0.3 0.3"/>
                  <joint name="slide_y" type="slide" axis="0 1 0" range="-0.3 0.3"/>
                  <geom type="sphere" size="0.01" mass="0.1"/>
                  <site name="tool_site"/>
                </body>
              </worldbody>
              <actuator>
                <position name="slide_x_act" joint="slide_x"/>
                <position name="slide_y_act" joint="slide_y"/>
              </actuator>
            </mujoco>
            """
        )
        data = mujoco.MjData(model)
        skeleton = ArmSerialDLSSkeleton.from_session(
            model=model,
            data=data,
            spec=ArmSpec(
                ee_site_name="tool_site",
                arm_joint_names=("slide_x", "slide_y"),
                arm_actuator_names=("slide_x_act", "slide_y_act"),
                joint_limits={"slide_x": (-0.3, 0.3), "slide_y": (-0.3, 0.3)},
            ),
        )
        samples: list[np.ndarray] = []
        original_physics_step = skeleton._physics_step

        def record_physics_step(count: int = 1) -> None:
            for _ in range(count):
                original_physics_step(1)
                mujoco.mj_forward(model, data)
                samples.append(np.array(data.site_xpos[0], copy=True))

        skeleton._physics_step = record_physics_step  # type: ignore[method-assign]
        waypoints = np.asarray(
            ((0.0, 0.0, 0.0), (0.0, 0.15, 0.0), (0.15, 0.15, 0.0))
        )

        self.assertTrue(
            skeleton.track_cartesian_path(
                waypoints,
                duration=0.8,
                max_reference_step=0.002,
                gain=3.0,
                max_joint_delta=0.02,
                residual_tolerance=0.02,
            )
        )

        trace = np.asarray(samples)[:, :2]
        elbow = waypoints[1, :2]
        self.assertLess(float(np.min(np.linalg.norm(trace - elbow, axis=1))), 0.02)
        first_segment_distance = np.hypot(
            trace[:, 0], trace[:, 1] - np.clip(trace[:, 1], 0.0, 0.15)
        )
        second_segment_distance = np.hypot(
            trace[:, 0] - np.clip(trace[:, 0], 0.0, 0.15), trace[:, 1] - 0.15
        )
        cross_track = np.minimum(first_segment_distance, second_segment_distance)
        self.assertLess(float(np.max(cross_track)), 0.02)
        np.testing.assert_allclose(trace[-1], waypoints[-1, :2], atol=0.02)

    def test_skeleton_uses_the_canonical_session_objects(self) -> None:
        model = object()
        data = object()
        spec = self.valid_spec()
        skeleton = ArmSerialDLSSkeleton.from_session(model=model, data=data, spec=spec)

        self.assertIsInstance(skeleton, SessionBoundSkeleton)
        self.assertIs(skeleton.model, model)
        self.assertIs(skeleton.data, data)
        self.assertIs(skeleton.spec, spec)

    def test_primitive_inventory_is_public_and_capability_neutral(self) -> None:
        primitive_names = {item.name for item in discover_primitives(ArmSerialDLSSkeleton)}
        self.assertEqual(
            primitive_names,
            {
                "get_joint_positions",
                "get_joint_velocities",
                "get_ee_pose",
                "fk",
                "ik",
                "ik_pose",
                "set_arm_actuators",
                "move_joints",
                "move_cartesian",
                "track_cartesian_path",
                "hold",
                "set_gripper",
                "home",
                "gripper_open",
                "gripper_close",
            },
        )
        self.assertNotIn("_physics_step", primitive_names)
        self.assertNotIn("_temporary_joint_positions", primitive_names)

    def test_module_has_no_registry_or_dynamic_binding(self) -> None:
        source = inspect.getsource(arm_serial_dls)
        for forbidden in (
            "capability_design.json",
            "TaskPlanner",
            "MethodType",
            "setattr(",
            "from_xml_path",
            "mj_resetData",
            "MjData(",
        ):
            self.assertNotIn(forbidden, source)

    def test_execution_methods_do_not_write_state_arrays(self) -> None:
        source_path = Path(arm_serial_dls.__file__ or "")
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        execution_names = {
            "set_arm_actuators",
            "move_joints",
            "move_cartesian",
            "track_cartesian_path",
            "hold",
            "set_gripper",
            "home",
            "gripper_open",
            "gripper_close",
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in execution_names:
                segment = ast.get_source_segment(source, node) or ""
                self.assertNotIn(".qpos", segment)
                self.assertNotIn(".qvel", segment)

    def test_qpos_writes_are_confined_to_kinematic_context(self) -> None:
        source = inspect.getsource(arm_serial_dls)
        tree = ast.parse(source)

        class QposWriteVisitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.function_stack: list[str] = []
                self.write_functions: list[str] = []

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.function_stack.append(node.name)
                self.generic_visit(node)
                self.function_stack.pop()

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self.function_stack.append(node.name)
                self.generic_visit(node)
                self.function_stack.pop()

            def visit_Attribute(self, node: ast.Attribute) -> None:
                if node.attr == "qpos" and isinstance(node.ctx, ast.Load):
                    parent = getattr(node, "parent", None)
                    while parent is not None and not isinstance(parent, ast.Assign):
                        parent = getattr(parent, "parent", None)
                    if parent is not None:
                        self.write_functions.append(self.function_stack[-1])
                self.generic_visit(node)

        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                child.parent = parent  # type: ignore[attr-defined]
        visitor = QposWriteVisitor()
        visitor.visit(tree)
        self.assertEqual(set(visitor.write_functions), {"_temporary_joint_positions"})


if __name__ == "__main__":
    unittest.main()
