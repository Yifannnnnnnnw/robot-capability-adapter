from __future__ import annotations

import json
import unittest
from pathlib import Path


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]


class BenchmarkContractTests(unittest.TestCase):
    def test_benchmark_owns_no_concrete_experiment_or_runs_directory(self) -> None:
        self.assertFalse((BENCHMARK_ROOT / "experiments").exists())
        self.assertFalse((BENCHMARK_ROOT / "runs").exists())

    def test_reusable_inventory_and_backbone_set_have_unique_ids(self) -> None:
        inventory = json.loads(
            (
                BENCHMARK_ROOT
                / "components"
                / "robot_sets"
                / "all-14.json"
            ).read_text(encoding="utf-8")
        )["robot_configuration_ids"]
        declared = json.loads(
            (
                BENCHMARK_ROOT
                / "components"
                / "backbone_sets"
                / "declared-seven.json"
            ).read_text(encoding="utf-8")
        )["backbone_ids"]
        registry = json.loads(
            (BENCHMARK_ROOT / "backbones" / "registry.json").read_text(
                encoding="utf-8"
            )
        )["backbones"]

        self.assertEqual(len(inventory), 14)
        self.assertEqual(len(inventory), len(set(inventory)))
        self.assertEqual(len(declared), 7)
        self.assertEqual(set(declared), {entry["id"] for entry in registry})

    def test_b1_protocol_keeps_the_synthesis_only_boundary(self) -> None:
        protocol = json.loads(
            (
                BENCHMARK_ROOT / "protocols" / "b1-driver-synthesis.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(protocol["max_driver_attempts_per_condition"], 3)
        self.assertFalse(protocol["run_task_demo"])
        self.assertFalse(protocol["run_high_level_controller"])
        self.assertEqual(
            protocol["stages"],
            [
                "study",
                "generate",
                "private-driver-validation",
                "bounded-repair",
                "final-driver-outcome",
            ],
        )

    def test_high_level_controller_catalogue_has_audit_files(self) -> None:
        root = BENCHMARK_ROOT / "high_level_controllers"
        registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))

        for entry in registry["controllers"]:
            self.assertNotIn("chapter3_role", entry)
            directory = root / entry["directory"]
            self.assertTrue((directory / "SOURCE.md").is_file())
            self.assertTrue((directory / "controller.json").is_file())


if __name__ == "__main__":
    unittest.main()
