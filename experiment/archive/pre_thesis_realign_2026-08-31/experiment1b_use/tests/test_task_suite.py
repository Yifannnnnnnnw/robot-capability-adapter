"""Focused structural checks for the sealed B2 task suite."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any


HERE = Path(__file__).resolve().parent
TASK_SUITE_ROOT = HERE.parent / "config" / "task_suite"


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "b2_task_suite_builder", TASK_SUITE_ROOT / "build_suite.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load B2 task-suite builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class B2TaskSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = _load_builder()
        cls.suite = json.loads(
            (TASK_SUITE_ROOT / "task_suite.json").read_text(encoding="utf-8")
        )

    def test_committed_suite_is_the_mechanical_build(self) -> None:
        self.assertEqual(self.suite, self.builder.resolved_suite())

    def test_exact_authority_selection_and_replicates(self) -> None:
        self.assertEqual(self.suite["task_count"], 10)
        self.assertEqual(self.suite["replicate_plan"]["replicate_ids"], ["R1", "R2", "R3"])
        task_ids = {
            robot["robot_configuration_id"]: [task["task_id"] for task in robot["tasks"]]
            for robot in self.suite["robot_suites"]
        }
        self.assertEqual(
            task_ids,
            {
                "robotstudio_so101": [
                    "mw_push_to_goal",
                    "mw_sweep_into_goal",
                    "mw_pick_place",
                    "mw_pick_place_wall",
                    "mw_bin_picking",
                ],
                "unitree-go2-stock-12dof": [
                    "GO2-T02",
                    "GO2-T03",
                    "GO2-T06",
                    "GO2-T16",
                    "GO2-T17",
                ],
            },
        )

    def test_public_projection_excludes_private_verdict_inputs(self) -> None:
        forbidden = {
            "scoring",
            "criterion",
            "threshold",
            "binding",
            "guard",
            "reset",
            "seed",
            "source",
            "budget",
            "rendering",
            "verdict",
        }

        def check(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    tokens = set(str(key).lower().replace("-", "_").split("_"))
                    self.assertFalse(tokens & forbidden, key)
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        for robot in self.suite["robot_suites"]:
            for task in robot["tasks"]:
                projection = task["public_projection"]
                self.assertEqual(projection["task_id"], projection["request"]["task_id"])
                check(projection)

    def test_private_snapshot_pins_complete_clauses_bindings_guards_and_media(self) -> None:
        clause_count = 0
        for robot in self.suite["robot_suites"]:
            for task in robot["tasks"]:
                clauses = task["private_scoring_clauses"]
                clause_count += len(clauses)
                self.assertEqual(
                    {clause["clause_id"] for clause in clauses},
                    set(task["private_clause_bindings"]),
                )
                self.assertTrue(task["private_measurement_bindings"])
                self.assertTrue(task["private_guards"])
                self.assertTrue(task["source_records"])
                self.assertEqual(
                    set(task["episode_budget"]),
                    {"timeout_sim_s", "max_steps", "sample_hz"},
                )
                self.assertTrue(task["rendering"]["enabled"])
                self.assertTrue(task["rendering"]["continuous_episode_video_required"])
                self.assertEqual(
                    {item["replicate_id"] for item in task["replicate_inputs"]},
                    {"R1", "R2", "R3"},
                )
                resets = [item["reset"] for item in task["replicate_inputs"]]
                self.assertEqual(resets, [resets[0], resets[0], resets[0]])
                self.assertTrue(all(item["reset_seed"] is None for item in task["replicate_inputs"]))
                self.assertTrue(
                    all(item["reset_seed_applied"] is False for item in task["replicate_inputs"])
                )
        self.assertEqual(clause_count, 12)

    def test_multistage_so101_pick_tasks_have_the_extended_physical_budget(self) -> None:
        so101 = next(
            robot
            for robot in self.suite["robot_suites"]
            if robot["robot_configuration_id"] == "robotstudio_so101"
        )
        budgets = {
            task["task_id"]: task["episode_budget"] for task in so101["tasks"]
        }
        for task_id in ("mw_pick_place", "mw_pick_place_wall", "mw_bin_picking"):
            self.assertEqual(
                budgets[task_id],
                {"timeout_sim_s": 40.0, "max_steps": 10000, "sample_hz": 20.0},
            )
        for task_id in ("mw_push_to_goal", "mw_sweep_into_goal"):
            self.assertEqual(
                budgets[task_id],
                {"timeout_sim_s": 20.0, "max_steps": 5000, "sample_hz": 20.0},
            )

    def test_go2_t02_t03_repetition_limitation_is_explicit(self) -> None:
        go2 = next(
            robot
            for robot in self.suite["robot_suites"]
            if robot["robot_configuration_id"] == "unitree-go2-stock-12dof"
        )
        tasks = {task["task_id"]: task for task in go2["tasks"]}
        for task_id in ("GO2-T02", "GO2-T03"):
            lineage = tasks[task_id]["protocol_lineage"]
            self.assertEqual(lineage["source_protocol_repetitions"], 10)
            self.assertEqual(lineage["canonical_repetition_variants_available"], 0)
            self.assertEqual(lineage["b2_episode_reset_count"], 1)
            self.assertIn("does not reproduce", lineage["coverage_note"])
        self.assertEqual(
            {(item["robot_configuration_id"], item["task_id"]) for item in self.suite["limitations"]},
            {
                ("unitree-go2-stock-12dof", "GO2-T02"),
                ("unitree-go2-stock-12dof", "GO2-T03"),
            },
        )


if __name__ == "__main__":
    unittest.main()
