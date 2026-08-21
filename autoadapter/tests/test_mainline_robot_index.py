from __future__ import annotations

import json
import unittest
from pathlib import Path

from autoadapter2.libraries import load_indexed_robot_package


ROOT = Path(__file__).resolve().parents[1]
MAINLINE_CONFIG_PATH = ROOT / "configs" / "experiments" / "mainline.json"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"

EXPECTED_PACKAGES = {
    "robotstudio_so101": "robotstudio_so101/1.0.0",
    "unitree-go2-stock-12dof": "unitree-go2-stock-12dof/1.0.0",
    "franka_panda": "franka_panda/1.0.0",
    "kinova_gen3_robotiq_2f85": "kinova_gen3_robotiq_2f85/1.0.0",
    "ufactory_xarm7": "ufactory_xarm7/1.0.0",
    "universal_robots_ur5e_robotiq_2f85": (
        "universal_robots_ur5e_robotiq_2f85/1.0.0"
    ),
    "piper": "piper/1.0.0",
    "kuka_iiwa_14": "kuka_iiwa_14/1.0.0",
    "leap_hand": "leap_hand/1.0.0",
    "hello_robot_stretch_2": "hello_robot_stretch_2/1.0.0",
    "aloha_2": "aloha_2/1.0.0",
}


class MainlineRobotIndexTests(unittest.TestCase):
    def test_mainline_config_and_runnable_index_are_the_same_cohort(self) -> None:
        config = json.loads(MAINLINE_CONFIG_PATH.read_text(encoding="utf-8"))
        runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))

        self.assertEqual(len(config["robots"]), len(EXPECTED_PACKAGES))
        self.assertEqual(set(config["robots"]), set(EXPECTED_PACKAGES))
        self.assertEqual(runnable_index["robots"], EXPECTED_PACKAGES)

    def test_indexed_paths_are_canonical_and_packages_are_loadable(self) -> None:
        runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
        robots_root = RUNNABLE_INDEX_PATH.parent

        for robot_id, relative_path in runnable_index["robots"].items():
            with self.subTest(robot_id=robot_id):
                package_root = (robots_root / relative_path).resolve()
                self.assertEqual(
                    package_root,
                    (robots_root / robot_id / "1.0.0").resolve(),
                )
                self.assertTrue(package_root.is_dir())

                leap_runtime_inputs = (
                    package_root / "tasks" / "private" / "instances.json",
                    package_root / "tasks" / "private" / "bindings.json",
                    package_root / "tasks" / "private" / "guards.json",
                    package_root / "reference" / "driver.py",
                )
                if robot_id == "leap_hand" and not all(
                    path.is_file() for path in leap_runtime_inputs
                ):
                    for relative in (
                        "morphology.json",
                        "tasks/sources.json",
                        "tasks/catalog.json",
                    ):
                        self.assertTrue((package_root / relative).is_file(), relative)
                    self.assertTrue((package_root / "assets").is_dir())
                    self.assertTrue(any((package_root / "skeleton").glob("*.py")))
                    continue

                package = load_indexed_robot_package(ROOT, robot_id)
                self.assertEqual(package.root, package_root)
                self.assertEqual(package.robot_configuration_id, robot_id)
                self.assertEqual(package.package_version, "1.0.0")


if __name__ == "__main__":
    unittest.main()
