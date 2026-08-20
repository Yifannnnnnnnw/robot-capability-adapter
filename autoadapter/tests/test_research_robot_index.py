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
    "boston_dynamics_spot_with_arm",
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
        self.assertTrue(set(candidate_ids).isdisjoint(runnable_ids))

        candidates_by_id = {
            candidate["robot_configuration_id"]: candidate for candidate in candidates
        }
        ur5e = candidates_by_id["universal_robots_ur5e_robotiq_2f85"]
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

        barkour = candidates_by_id["google_barkour_vb"]
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
