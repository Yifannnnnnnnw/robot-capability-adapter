from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "research" / "robots" / "index.json"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
SOURCE_ROOT = ROOT / "src"

BACKUP_CANDIDATE_IDS = ("google_barkour_vb", "unitree_g1")


class ResearchRobotIndexTests(unittest.TestCase):
    def test_index_contains_only_non_runtime_backup_candidates(self) -> None:
        index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
        candidates = index["candidates"]
        candidate_ids = tuple(
            candidate["robot_configuration_id"] for candidate in candidates
        )

        self.assertIs(index["non_runtime"], True)
        self.assertIs(index["planning_only"], True)
        self.assertIs(index["runtime"], False)
        self.assertEqual(candidate_ids, BACKUP_CANDIDATE_IDS)
        self.assertTrue(set(candidate_ids).isdisjoint(runnable_index["robots"]))
        self.assertTrue(
            all(
                candidate["mainline_disposition"] == "optional_backup_only"
                for candidate in candidates
            )
        )
        self.assertTrue(
            all(candidate["missing_for_runnable_package"] for candidate in candidates)
        )

    def test_backup_candidate_records_retain_reviewed_facts(self) -> None:
        index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        candidates_by_id = {
            candidate["robot_configuration_id"]: candidate
            for candidate in index["candidates"]
        }

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
            any(
                "humanoid-control skeleton" in item
                for item in g1["missing_for_runnable_package"]
            )
        )

        barkour = candidates_by_id["google_barkour_vb"]
        self.assertEqual(barkour["mainline_disposition"], "optional_backup_only")
        barkour_paths = {
            material["path"]
            for material in barkour["locally_observed_source_material"]
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
                "Provide a capability-neutral quadruped_position_policy skeleton"
                in item
                for item in barkour["missing_for_runnable_package"]
            )
        )
        self.assertTrue(
            any(
                "specialist policies" in item
                for item in barkour["missing_for_runnable_package"]
            )
        )

    def test_runtime_source_does_not_consume_research_index(self) -> None:
        for path in SOURCE_ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in {
                ".py",
                ".json",
                ".toml",
                ".yaml",
                ".yml",
            }:
                continue
            source = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "research/robots",
                source,
                f"runtime source must not consume {path}",
            )


if __name__ == "__main__":
    unittest.main()
