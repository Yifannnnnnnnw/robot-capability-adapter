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
    "kinova_gen3",
    "ufactory_xarm7",
    "universal_robots_ur5e",
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
