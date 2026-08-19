from __future__ import annotations

import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
FRANKA_ASSETS_ROOT = ROOT / "libraries" / "robots" / "franka_panda" / "1.0.0" / "assets"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"

SCENE_NAMES = (
    "reach_scene.xml",
    "push_to_goal_scene.xml",
    "pick_place_scene.xml",
    "pick_place_wall_scene.xml",
    "wall_scene.xml",
    "sweep_into_goal_scene.xml",
    "drawer_scene.xml",
    "button_front_scene.xml",
    "button_topdown_scene.xml",
    "handle_vertical_scene.xml",
    "door_scene.xml",
    "faucet_scene.xml",
    "dial_scene.xml",
    "lever_scene.xml",
    "peg_insertion_side_scene.xml",
    "bin_picking_scene.xml",
    "pick_out_of_hole_scene.xml",
)

EXPECTED_DIMS = {
    "reach_scene.xml": (9, 9, 8),
    "bin_picking_scene.xml": (16, 15, 8),
    "peg_insertion_side_scene.xml": (16, 15, 8),
    "pick_out_of_hole_scene.xml": (16, 15, 8),
    "pick_place_scene.xml": (16, 15, 8),
    "pick_place_wall_scene.xml": (16, 15, 8),
    "push_to_goal_scene.xml": (16, 15, 8),
    "sweep_into_goal_scene.xml": (16, 15, 8),
    "wall_scene.xml": (16, 15, 8),
    "button_front_scene.xml": (10, 10, 8),
    "button_topdown_scene.xml": (10, 10, 8),
    "dial_scene.xml": (10, 10, 8),
    "door_scene.xml": (10, 10, 8),
    "drawer_scene.xml": (10, 10, 8),
    "faucet_scene.xml": (10, 10, 8),
    "handle_vertical_scene.xml": (10, 10, 8),
    "lever_scene.xml": (10, 10, 8),
}

PANDA_JOINTS = tuple(f"joint{index}" for index in range(1, 8)) + (
    "finger_joint1",
    "finger_joint2",
)
PANDA_ACTUATORS = tuple(f"actuator{index}" for index in range(1, 9))

TASK_SYMBOLS = {
    "bin_picking_scene.xml": {"bodies": ("workpiece", "bin_goal"), "sites": ("workpiece_center",)},
    "button_front_scene.xml": {
        "bodies": ("front_button",),
        "joints": ("front_button_slide",),
        "sites": ("front_button_site",),
    },
    "button_topdown_scene.xml": {
        "bodies": ("top_button",),
        "joints": ("top_button_slide",),
        "sites": ("top_button_site",),
    },
    "dial_scene.xml": {
        "bodies": ("dial", "dial_tip"),
        "joints": ("dial_hinge",),
        "sites": ("dial_tip_site",),
    },
    "door_scene.xml": {
        "bodies": ("door", "door_panel", "door_handle"),
        "joints": ("door_hinge",),
        "sites": ("door_handle_site",),
    },
    "drawer_scene.xml": {
        "bodies": ("drawer", "drawer_handle"),
        "joints": ("drawer_slide",),
        "sites": ("drawer_handle_site",),
    },
    "faucet_scene.xml": {
        "bodies": ("faucet_handle", "faucet_tip"),
        "joints": ("faucet_hinge",),
        "sites": ("faucet_tip_site",),
    },
    "handle_vertical_scene.xml": {
        "bodies": ("vertical_handle",),
        "joints": ("vertical_handle_slide",),
        "sites": ("vertical_handle_site",),
    },
    "lever_scene.xml": {
        "bodies": ("lever", "lever_tip"),
        "joints": ("lever_hinge",),
        "sites": ("lever_tip_site",),
    },
    "peg_insertion_side_scene.xml": {
        "bodies": ("workpiece", "peg_goal"),
        "sites": ("peg_head_site",),
    },
    "pick_out_of_hole_scene.xml": {
        "bodies": ("workpiece", "extraction_goal"),
        "sites": ("workpiece_center",),
    },
    "pick_place_scene.xml": {
        "bodies": ("workpiece", "pick_place_goal"),
        "sites": ("workpiece_center",),
    },
    "pick_place_wall_scene.xml": {
        "bodies": ("workpiece", "wall_pick_goal"),
        "sites": ("workpiece_center",),
    },
    "push_to_goal_scene.xml": {"bodies": ("workpiece", "push_goal")},
    "reach_scene.xml": {"bodies": ("reach_goal",), "sites": ("reach_goal_site",)},
    "sweep_into_goal_scene.xml": {
        "bodies": ("workpiece", "sweep_goal"),
        "sites": ("sweep_goal_site",),
    },
    "wall_scene.xml": {
        "bodies": ("workpiece", "wall_push_goal"),
        "sites": ("workpiece_center",),
    },
}


def _has_name(model: mujoco.MjModel, object_type: mujoco.mjtObj, name: str) -> bool:
    return mujoco.mj_name2id(model, object_type, name) >= 0


class FrankaTaskSceneTests(unittest.TestCase):
    def _load(self, scene_name: str) -> mujoco.MjModel:
        return mujoco.MjModel.from_xml_path(str(FRANKA_ASSETS_ROOT / scene_name))

    def test_exact_local_scene_set_and_mjcf_identity(self) -> None:
        self.assertEqual(len(SCENE_NAMES), 17)
        self.assertEqual(
            {name for name in SCENE_NAMES if (FRANKA_ASSETS_ROOT / name).is_file()},
            set(SCENE_NAMES),
        )

        for scene_name in SCENE_NAMES:
            with self.subTest(scene_name=scene_name):
                scene_path = FRANKA_ASSETS_ROOT / scene_name
                root = ET.parse(scene_path).getroot()
                self.assertTrue(root.get("model", "").startswith("franka_"))
                includes = root.findall("include")
                self.assertEqual(len(includes), 1)
                self.assertEqual(includes[0].get("file"), "panda.xml")

                scene_text = scene_path.read_text(encoding="utf-8").lower()
                for forbidden in ("so101", "so-101", "robotstudio_so101"):
                    self.assertNotIn(forbidden, scene_text)

    def test_all_scenes_load_step_and_expose_panda_contract(self) -> None:
        self.assertEqual(mujoco.__version__, "3.3.6")

        for scene_name in SCENE_NAMES:
            with self.subTest(scene_name=scene_name):
                model = self._load(scene_name)
                self.assertEqual((model.nq, model.nv, model.nu), EXPECTED_DIMS[scene_name])
                for joint_name in PANDA_JOINTS:
                    self.assertTrue(
                        _has_name(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name),
                        joint_name,
                    )
                for actuator_name in PANDA_ACTUATORS:
                    self.assertTrue(
                        _has_name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name),
                        actuator_name,
                    )
                self.assertTrue(_has_name(model, mujoco.mjtObj.mjOBJ_BODY, "hand"))
                home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
                self.assertGreaterEqual(home_id, 0)

                symbols = TASK_SYMBOLS[scene_name]
                for body_name in symbols.get("bodies", ()):
                    self.assertTrue(_has_name(model, mujoco.mjtObj.mjOBJ_BODY, body_name), body_name)
                for joint_name in symbols.get("joints", ()):
                    self.assertTrue(_has_name(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name), joint_name)
                for site_name in symbols.get("sites", ()):
                    self.assertTrue(_has_name(model, mujoco.mjtObj.mjOBJ_SITE, site_name), site_name)

                data = mujoco.MjData(model)
                mujoco.mj_resetDataKeyframe(model, data, home_id)
                mujoco.mj_forward(model, data)
                self.assertTrue(np.isfinite(data.qpos).all())
                self.assertTrue(np.isfinite(data.qvel).all())
                start_time = float(data.time)
                mujoco.mj_step(model, data)
                self.assertTrue(np.isfinite(data.time))
                self.assertGreater(data.time, start_time)
                self.assertTrue(np.isfinite(data.qpos).all())
                self.assertTrue(np.isfinite(data.qvel).all())

    def test_transformed_world_coordinates_are_present_after_forward(self) -> None:
        reach_model = self._load("reach_scene.xml")
        reach_data = mujoco.MjData(reach_model)
        mujoco.mj_forward(reach_model, reach_data)
        reach_goal_id = mujoco.mj_name2id(
            reach_model,
            mujoco.mjtObj.mjOBJ_BODY,
            "reach_goal",
        )
        np.testing.assert_allclose(
            reach_data.xpos[reach_goal_id],
            [0.55, 0.10, 0.43],
            atol=1e-9,
        )

        push_model = self._load("push_to_goal_scene.xml")
        push_data = mujoco.MjData(push_model)
        mujoco.mj_forward(push_model, push_data)
        work_surface_id = mujoco.mj_name2id(
            push_model,
            mujoco.mjtObj.mjOBJ_GEOM,
            "work_surface",
        )
        work_surface_top = (
            float(push_data.geom_xpos[work_surface_id, 2])
            + float(push_model.geom_size[work_surface_id, 2])
        )
        self.assertAlmostEqual(work_surface_top, 0.41, places=9)

    def test_franka_remains_non_runtime_with_remaining_research_gaps(self) -> None:
        runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("franka_panda", runnable_index["robots"])

        research_index = json.loads(RESEARCH_INDEX_PATH.read_text(encoding="utf-8"))
        candidate = next(
            item
            for item in research_index["candidates"]
            if item["robot_configuration_id"] == "franka_panda"
        )
        self.assertEqual(candidate["observed_task_count"], 5)
        scene_observations = candidate["locally_observed_source_material"]
        self.assertTrue(
            any(
                item["path"].endswith("/assets/reach_scene.xml")
                and item["kind"] == "canonical_task_scene_set"
                and "17" in item["observation"]
                and "local" in item["observation"].lower()
                and "tested" in item["observation"].lower()
                for item in scene_observations
            )
        )
        missing = candidate["missing_for_runnable_package"]
        self.assertFalse(any("task-specific MuJoCo scenes" in item for item in missing))
        for phrase in (
            "Framework-private tasks/private",
            "arm_serial_dls skeleton",
            "package check",
            "positive control",
            "dynamic canary",
        ):
            self.assertTrue(any(phrase in item for item in missing), phrase)


if __name__ == "__main__":
    unittest.main()
