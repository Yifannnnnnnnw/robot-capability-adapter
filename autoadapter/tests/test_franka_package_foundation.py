from __future__ import annotations

import json
import unittest
from pathlib import Path

import mujoco


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "franka_panda" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"

ENTRYPOINTS = (
    "scene.xml",
    "pushbench.xml",
    "scene_with_object.xml",
    "franka_cube_test_scene.xml",
)
CONTROLLED_JOINTS = [
    "joint1",
    "joint2",
    "joint3",
    "joint4",
    "joint5",
    "joint6",
    "joint7",
    "finger_joint1",
]
ACTUATORS = [
    "actuator1",
    "actuator2",
    "actuator3",
    "actuator4",
    "actuator5",
    "actuator6",
    "actuator7",
    "actuator8",
]
ARM_LIMITS = {
    "joint1": [-2.8973, 2.8973],
    "joint2": [-1.7628, 1.7628],
    "joint3": [-2.8973, 2.8973],
    "joint4": [-3.0718, -0.0698],
    "joint5": [-2.8973, 2.8973],
    "joint6": [-0.0175, 3.7525],
    "joint7": [-2.8973, 2.8973],
}
PUSHBENCH_OBJECT_BODIES = ["tee", "cube_red", "cube_green", "cube_blue", "obs"]
FINGER_JOINT_RANGE_M = [0.0, 0.04]
CORE_TOP_LEVEL_FILES = (
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "franka_cube_test_scene.xml",
    "hand.xml",
    "panda.png",
    "panda.xml",
    "panda_nohand.xml",
    "pushbench.xml",
    "scene.xml",
    "scene_with_object.xml",
)
CORE_MESH_FILES = (
    "assets/finger_0.obj",
    "assets/finger_1.obj",
    "assets/hand.stl",
    "assets/hand_0.obj",
    "assets/hand_1.obj",
    "assets/hand_2.obj",
    "assets/hand_3.obj",
    "assets/hand_4.obj",
    "assets/link0.stl",
    "assets/link0_0.obj",
    "assets/link0_1.obj",
    "assets/link0_10.obj",
    "assets/link0_11.obj",
    "assets/link0_2.obj",
    "assets/link0_3.obj",
    "assets/link0_4.obj",
    "assets/link0_5.obj",
    "assets/link0_7.obj",
    "assets/link0_8.obj",
    "assets/link0_9.obj",
    "assets/link1.obj",
    "assets/link1.stl",
    "assets/link2.obj",
    "assets/link2.stl",
    "assets/link3.stl",
    "assets/link3_0.obj",
    "assets/link3_1.obj",
    "assets/link3_2.obj",
    "assets/link3_3.obj",
    "assets/link4.stl",
    "assets/link4_0.obj",
    "assets/link4_1.obj",
    "assets/link4_2.obj",
    "assets/link4_3.obj",
    "assets/link5_0.obj",
    "assets/link5_1.obj",
    "assets/link5_2.obj",
    "assets/link5_collision_0.obj",
    "assets/link5_collision_1.obj",
    "assets/link5_collision_2.obj",
    "assets/link6.stl",
    "assets/link6_0.obj",
    "assets/link6_1.obj",
    "assets/link6_10.obj",
    "assets/link6_11.obj",
    "assets/link6_12.obj",
    "assets/link6_13.obj",
    "assets/link6_14.obj",
    "assets/link6_15.obj",
    "assets/link6_16.obj",
    "assets/link6_2.obj",
    "assets/link6_3.obj",
    "assets/link6_4.obj",
    "assets/link6_5.obj",
    "assets/link6_6.obj",
    "assets/link6_7.obj",
    "assets/link6_8.obj",
    "assets/link6_9.obj",
    "assets/link7.stl",
    "assets/link7_0.obj",
    "assets/link7_1.obj",
    "assets/link7_2.obj",
    "assets/link7_3.obj",
    "assets/link7_4.obj",
    "assets/link7_5.obj",
    "assets/link7_6.obj",
    "assets/link7_7.obj",
)
EXPECTED_DIMS = {
    "scene.xml": (9, 9, 8),
    "pushbench.xml": (37, 33, 8),
    "scene_with_object.xml": (16, 15, 8),
    "franka_cube_test_scene.xml": (16, 15, 8),
}


def _names(model: mujoco.MjModel, obj: mujoco.mjtObj, count: int) -> list[str]:
    return [mujoco.mj_id2name(model, obj, index) for index in range(count)]


class FrankaPackageFoundationTests(unittest.TestCase):
    def _load(self, filename: str) -> mujoco.MjModel:
        return mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / filename))

    def _assert_model_facts(self, model: mujoco.MjModel) -> None:
        for joint_name in CONTROLLED_JOINTS + ["finger_joint2"]:
            self.assertGreaterEqual(
                mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name),
                0,
            )
        for joint_name in ("finger_joint1", "finger_joint2"):
            joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
            self.assertEqual(
                int(model.jnt_type[joint_id]),
                int(mujoco.mjtJoint.mjJNT_SLIDE),
            )
            for actual, expected in zip(model.jnt_range[joint_id], FINGER_JOINT_RANGE_M):
                self.assertAlmostEqual(float(actual), expected)
        self.assertEqual(_names(model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu), ACTUATORS)
        self.assertEqual(model.nu, 8)
        self.assertEqual(model.nsite, 0)
        hand_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "hand")
        self.assertGreaterEqual(hand_id, 0)

        for actuator_index, joint_name in enumerate(CONTROLLED_JOINTS[:7]):
            joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
            self.assertEqual(
                int(model.actuator_trntype[actuator_index]),
                int(mujoco.mjtTrn.mjTRN_JOINT),
            )
            self.assertEqual(int(model.actuator_trnid[actuator_index, 0]), joint_id)
            for actual, expected in zip(
                model.actuator_ctrlrange[actuator_index], ARM_LIMITS[joint_name]
            ):
                self.assertAlmostEqual(float(actual), expected)

        tendon_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_TENDON, "split")
        self.assertGreaterEqual(tendon_id, 0)
        self.assertEqual(
            int(model.actuator_trntype[7]),
            int(mujoco.mjtTrn.mjTRN_TENDON),
        )
        self.assertEqual(int(model.actuator_trnid[7, 0]), tendon_id)
        self.assertEqual(model.actuator_ctrlrange[7].tolist(), [0.0, 255.0])

        finger_ids = {
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "finger_joint1"),
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "finger_joint2"),
        }
        tendon_start = int(model.tendon_adr[tendon_id])
        tendon_end = tendon_start + int(model.tendon_num[tendon_id])
        self.assertEqual(
            set(int(value) for value in model.wrap_objid[tendon_start:tendon_end]),
            finger_ids,
        )

        equality_pairs = []
        for equality_index in range(model.neq):
            if model.eq_type[equality_index] == mujoco.mjtEq.mjEQ_JOINT:
                equality_pairs.append(
                    {
                        int(model.eq_obj1id[equality_index]),
                        int(model.eq_obj2id[equality_index]),
                    }
                )
        self.assertIn(finger_ids, equality_pairs)

    def test_canonical_closure_shape_and_required_files(self) -> None:
        self.assertTrue(ASSETS_ROOT.is_dir())
        entries = list(ASSETS_ROOT.rglob("*"))
        self.assertFalse(any(entry.is_symlink() for entry in entries))
        self.assertEqual(len(CORE_TOP_LEVEL_FILES), 11)
        self.assertEqual(len(CORE_MESH_FILES), 67)
        for relative_path in CORE_TOP_LEVEL_FILES + CORE_MESH_FILES:
            self.assertTrue((ASSETS_ROOT / relative_path).is_file(), relative_path)

    def test_franka_is_absent_from_runnable_index(self) -> None:
        runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("franka_panda", runnable_index["robots"])

    def test_all_entrypoints_load_and_step_with_real_mujoco(self) -> None:
        for filename in ENTRYPOINTS:
            with self.subTest(filename=filename):
                model = self._load(filename)
                self.assertEqual(
                    (model.nq, model.nv, model.nu),
                    EXPECTED_DIMS[filename],
                )
                self._assert_model_facts(model)
                data = mujoco.MjData(model)
                mujoco.mj_step(model, data)
                self.assertGreater(data.time, 0.0)

        pushbench = self._load("pushbench.xml")
        for body_name in PUSHBENCH_OBJECT_BODIES:
            self.assertGreaterEqual(
                mujoco.mj_name2id(pushbench, mujoco.mjtObj.mjOBJ_BODY, body_name),
                0,
            )
        self.assertGreaterEqual(
            mujoco.mj_name2id(pushbench, mujoco.mjtObj.mjOBJ_KEY, "home"),
            0,
        )

    def test_morphology_matches_pushbench_model(self) -> None:
        morphology = json.loads(MORPHOLOGY_PATH.read_text(encoding="utf-8"))
        model = self._load("pushbench.xml")
        self._assert_model_facts(model)

        self.assertEqual(morphology["schema_version"], "1.0")
        self.assertEqual(morphology["robot_model_id"], "franka_panda")
        self.assertEqual(morphology["robot_configuration_id"], "franka_panda")
        self.assertEqual(morphology["package_version"], "1.0.0")
        self.assertEqual(morphology["morphology_kind"], "fixed_base_serial_manipulator")
        self.assertEqual(
            morphology["degrees_of_freedom"],
            {
                "arm": 7,
                "actuated_gripper": 1,
                "passive_mirrored_finger": 1,
            },
        )
        self.assertEqual(morphology["mjcf_entrypoint"], "assets/pushbench.xml")

        control = morphology["public_control"]
        self.assertEqual(control["actuation"], "joint_position")
        self.assertEqual(control["joint_names"], CONTROLLED_JOINTS)
        self.assertEqual(control["actuator_names"], ACTUATORS)
        self.assertEqual(control["arm_joint_limits_rad"], ARM_LIMITS)
        self.assertEqual(control["gripper_ctrl_range_native"], [0.0, 255.0])
        self.assertEqual(control["gripper_joint_range_m"], FINGER_JOINT_RANGE_M)
        self.assertEqual(control["passive_joint_names"], ["finger_joint2"])
        self.assertEqual(
            control["actuator_joint_map"]["actuator8"],
            "tendon:split",
        )
        self.assertEqual(
            control["gripper_tendon"],
            {
                "actuator_name": "actuator8",
                "tendon_name": "split",
                "controlled_joint": "finger_joint1",
                "passive_joint": "finger_joint2",
                "passive_coupling": "equality",
            },
        )

        observations = morphology["public_observations"]
        self.assertEqual(observations["end_effector_body"], "hand")
        self.assertNotIn("end_effector_site", observations)
        self.assertEqual(observations["object_bodies"], PUSHBENCH_OBJECT_BODIES)
        self.assertEqual(observations["frames"], ["world", "robot_base", "joint"])
        self.assertEqual(
            morphology["public_affordances"]["actions"],
            ["joint_position_control", "gripper_position_control"],
        )
        self.assertEqual(
            morphology["invocation_abi"],
            {
                "envelope": "request",
                "method_call": "generated_method(request=request)",
                "request_required": ["task_id", "task_parameters"],
                "task_id": "selected public task identifier",
                "task_parameters": "validated by the selected task invocation schema",
            },
        )
        self.assertEqual(
            morphology["reset_fact"],
            {
                "owner": "framework",
                "default": "model_qpos0",
                "available_keyframe": "home",
            },
        )

    def test_research_index_keeps_franka_incomplete_and_non_runtime(self) -> None:
        research_index = json.loads(RESEARCH_INDEX_PATH.read_text(encoding="utf-8"))
        self.assertIs(research_index["non_runtime"], True)
        self.assertIs(research_index["planning_only"], True)
        self.assertIs(research_index["runtime"], False)
        candidate = next(
            item
            for item in research_index["candidates"]
            if item["robot_configuration_id"] == "franka_panda"
        )
        self.assertEqual(candidate["observed_task_count"], 5)
        observed_paths = {
            item["path"] for item in candidate["locally_observed_source_material"]
        }
        self.assertIn(
            "autoadapter/libraries/robots/franka_panda/1.0.0/assets/pushbench.xml",
            observed_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/franka_panda/1.0.0/morphology.json",
            observed_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/franka_panda/1.0.0/tasks/sources.json",
            observed_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/franka_panda/1.0.0/tasks/catalog.json",
            observed_paths,
        )
        scene_observation = next(
            item
            for item in candidate["locally_observed_source_material"]
            if item["kind"] == "canonical_task_scene_set"
        )
        self.assertEqual(
            scene_observation["path"],
            "autoadapter/libraries/robots/franka_panda/1.0.0/assets/reach_scene.xml",
        )
        self.assertIn("17", scene_observation["observation"])
        self.assertIn("local", scene_observation["observation"].lower())
        self.assertIn("tested", scene_observation["observation"].lower())
        missing = candidate["missing_for_runnable_package"]
        self.assertFalse(any("local MuJoCo asset closure" in item for item in missing))
        self.assertFalse(any("current mainline morphology.json" in item for item in missing))
        self.assertFalse(any("20 distinct applicable source-backed tasks" in item for item in missing))
        self.assertFalse(any("tasks/sources.json" in item for item in missing))
        for phrase in (
            "full 20-task Direct-MuJoCo positive control",
            "positive control",
            "dynamic canary",
            "runnable index",
        ):
            self.assertTrue(any(phrase in item for item in missing), phrase)


if __name__ == "__main__":
    unittest.main()
