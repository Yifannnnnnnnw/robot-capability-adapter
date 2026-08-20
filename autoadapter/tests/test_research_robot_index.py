from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "research" / "robots" / "index.json"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
SOURCE_ROOT = ROOT / "src"

REQUIRED_CANDIDATE_IDS = {
    "franka_panda",
    "kinova_gen3_robotiq_2f85",
    "ufactory_xarm7",
    "universal_robots_ur5e_robotiq_2f85",
    "piper",
    "kuka_iiwa_14",
    "leap_hand",
    "google_barkour_vb",
    "unitree_g1",
    "hello_robot_stretch_2",
    "aloha_2",
}


class ResearchRobotIndexTests(unittest.TestCase):
    def test_index_is_non_runtime_and_disjoint_from_runtime_selection(self) -> None:
        index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
        candidates = index["candidates"]
        candidate_ids = [candidate["robot_configuration_id"] for candidate in candidates]
        runnable_ids = set(runnable_index["robots"])

        self.assertIs(index["non_runtime"], True)
        self.assertIs(index["planning_only"], True)
        self.assertIs(index["runtime"], False)
        self.assertEqual(len(candidate_ids), len(set(candidate_ids)))
        self.assertTrue(REQUIRED_CANDIDATE_IDS.issubset(candidate_ids))
        self.assertNotIn("boston_dynamics_spot_with_arm", candidate_ids)
        self.assertTrue(set(candidate_ids).isdisjoint(runnable_ids))

        candidates_by_id = {
            candidate["robot_configuration_id"]: candidate for candidate in candidates
        }
        ur5e = candidates_by_id["universal_robots_ur5e_robotiq_2f85"]
        self.assertEqual(ur5e["observed_task_count"], 20)
        ur5e_paths = {
            material["path"] for material in ur5e["locally_observed_source_material"]
        }
        self.assertIn(
            "autoadapter/libraries/robots/universal_robots_ur5e_robotiq_2f85/1.0.0/assets/scene.xml",
            ur5e_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/universal_robots_ur5e/1.0.0/assets/scene.xml",
            ur5e_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/universal_robots_ur5e_robotiq_2f85/1.0.0/tasks/sources.json",
            ur5e_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/universal_robots_ur5e_robotiq_2f85/1.0.0/tasks/catalog.json",
            ur5e_paths,
        )
        self.assertFalse(
            any("20 distinct" in item for item in ur5e["missing_for_runnable_package"])
        )
        self.assertFalse(
            any("tasks/sources.json" in item for item in ur5e["missing_for_runnable_package"])
        )
        self.assertIn(
            "autoadapter/libraries/robots/universal_robots_ur5e_robotiq_2f85/1.0.0/skeleton/arm_serial_dls.py",
            ur5e_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/universal_robots_ur5e_robotiq_2f85/1.0.0/assets/reach_scene.xml",
            ur5e_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/universal_robots_ur5e_robotiq_2f85/1.0.0/tasks/private/instances.json",
            ur5e_paths,
        )
        self.assertIn("autoadapter/evidence/README.md", ur5e_paths)
        self.assertFalse(
            any(
                "arm_serial_dls skeleton" in item
                for item in ur5e["missing_for_runnable_package"]
            )
        )
        self.assertFalse(
            any(
                "Create Framework-private tasks/private" in item
                for item in ur5e["missing_for_runnable_package"]
            )
        )
        self.assertFalse(
            any(
                phrase in item
                for item in ur5e["missing_for_runnable_package"]
                for phrase in (
                    "calibration-only reference driver",
                    "package check",
                    "positive control",
                )
            )
        )
        self.assertTrue(
            any(
                "dynamic canary" in item
                for item in ur5e["missing_for_runnable_package"]
            )
        )

        for robot_id in (
            "franka_panda",
            "kinova_gen3_robotiq_2f85",
            "ufactory_xarm7",
            "piper",
            "kuka_iiwa_14",
            "aloha_2",
        ):
            self.assertEqual(candidates_by_id[robot_id]["observed_task_count"], 20)

        leap = candidates_by_id["leap_hand"]
        leap_paths = {
            material["path"] for material in leap["locally_observed_source_material"]
        }
        self.assertIn(
            "autoadapter/libraries/robots/leap_hand/1.0.0/tasks/sources.json",
            leap_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/leap_hand/1.0.0/tasks/catalog.json",
            leap_paths,
        )
        self.assertFalse(
            any("20 distinct" in item for item in leap["missing_for_runnable_package"])
        )
        self.assertFalse(
            any("tasks/sources.json" in item for item in leap["missing_for_runnable_package"])
        )

        g1 = candidates_by_id["unitree_g1"]
        self.assertEqual(g1["mainline_disposition"], "optional_backup_only")
        g1_paths = {
            material["path"] for material in g1["locally_observed_source_material"]
        }
        self.assertIn(
            "autoadapter/libraries/robots/unitree_g1/1.0.0/assets/scene.xml",
            g1_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/unitree_g1/1.0.0/morphology.json",
            g1_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/unitree_g1/1.0.0/tasks/sources.json",
            g1_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/unitree_g1/1.0.0/tasks/catalog.json",
            g1_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/unitree_g1/1.0.0/skeleton/joint_position.py",
            g1_paths,
        )
        self.assertFalse(
            any("asset closure" in item for item in g1["missing_for_runnable_package"])
        )
        self.assertFalse(
            any("morphology.json" in item for item in g1["missing_for_runnable_package"])
        )
        self.assertFalse(
            any("20 distinct" in item for item in g1["missing_for_runnable_package"])
        )
        self.assertFalse(
            any("tasks/sources.json" in item for item in g1["missing_for_runnable_package"])
        )
        self.assertFalse(
            any("humanoid-control skeleton" in item for item in g1["missing_for_runnable_package"])
        )

        stretch = candidates_by_id["hello_robot_stretch_2"]
        self.assertEqual(stretch["observed_task_count"], 20)
        stretch_paths = {
            material["path"] for material in stretch["locally_observed_source_material"]
        }
        for path in (
            "autoadapter/libraries/robots/hello_robot_stretch_2/1.0.0/assets/scene.xml",
            "autoadapter/libraries/robots/hello_robot_stretch_2/1.0.0/morphology.json",
            "autoadapter/libraries/robots/hello_robot_stretch_2/1.0.0/tasks/sources.json",
            "autoadapter/libraries/robots/hello_robot_stretch_2/1.0.0/tasks/catalog.json",
            "autoadapter/libraries/robots/hello_robot_stretch_2/1.0.0/skeleton/stretch_control.py",
            "autoadapter/libraries/robots/hello_robot_stretch_2/1.0.0/skeleton/mobile_manipulation.py",
            "autoadapter/libraries/robots/hello_robot_stretch_2/1.0.0/assets/reach_scene.xml",
            "autoadapter/libraries/robots/hello_robot_stretch_2/1.0.0/tasks/private/instances.json",
            "autoadapter/libraries/robots/hello_robot_stretch_2/1.0.0/reference/driver.py",
            "autoadapter/evidence/README.md",
        ):
            self.assertIn(path, stretch_paths)
        reference_observation = next(
            material["observation"]
            for material in stretch["locally_observed_source_material"]
            if material["kind"] == "canonical_reference_calibration"
        )
        self.assertIn("all 20", reference_observation)
        self.assertIn("complete readable videos", reference_observation)
        missing = " ".join(stretch["missing_for_runnable_package"])
        self.assertNotIn("research candidate only", missing)
        self.assertNotIn("Create Framework-private", missing)
        self.assertNotIn("Add the package check", missing)
        self.assertNotIn("remaining three", missing)
        self.assertNotIn("video-backed package-wide positive control", missing)
        self.assertIn("dynamic canary", missing)

        barkour = candidates_by_id["google_barkour_vb"]
        self.assertEqual(barkour["mainline_disposition"], "optional_backup_only")
        barkour_paths = {
            material["path"] for material in barkour["locally_observed_source_material"]
        }
        self.assertIn(
            "autoadapter/runs/barkour-flat-bridge-20260820T125848Z/summary.json",
            barkour_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/google_barkour_vb/1.0.0/reference/flat_joystick.py",
            barkour_paths,
        )
        self.assertIn(
            "autoadapter/libraries/robots/google_barkour_vb/1.0.0/skeleton/quadruped_position_policy.py",
            barkour_paths,
        )
        self.assertEqual(barkour["morphology_family"], "quadruped_position_policy")
        self.assertFalse(
            any(
                "Train and retain" in item
                for item in barkour["missing_for_runnable_package"]
            )
        )
        self.assertFalse(
            any(
                "Provide a capability-neutral quadruped_position_policy skeleton" in item
                for item in barkour["missing_for_runnable_package"]
            )
        )
        self.assertTrue(
            any(
                "specialist policies" in item
                for item in barkour["missing_for_runnable_package"]
            )
        )

        for candidate in candidates:
            self.assertTrue(candidate["missing_for_runnable_package"])

        for path in SOURCE_ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".json", ".toml", ".yaml", ".yml"}:
                continue
            source = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "research/robots",
                source,
                f"runtime source must not consume {path}",
            )


if __name__ == "__main__":
    unittest.main()
