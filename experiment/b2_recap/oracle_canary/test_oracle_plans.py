"""Focused checks for the hidden ten-task scripted oracle asset."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from types import ModuleType


HERE = Path(__file__).resolve().parent


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "b2_oracle_validator", HERE / "validate_oracle_plans.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load B2 oracle validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OraclePlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = _load_validator()
        cls.oracle = json.loads(
            (HERE / "oracle_plans.json").read_text(encoding="utf-8")
        )

    def test_committed_asset_validates_against_sealed_tasks_and_designs(self) -> None:
        self.assertEqual(
            self.validator.validate(),
            {"task_count": 10, "leaf_count": 50},
        )

    def test_each_task_has_only_ordered_typed_native_leaves(self) -> None:
        expected_methods = {
            "robotstudio_so101": {
                "move_end_effector_to_position",
                "trace_cartesian_path",
                "set_gripper_opening",
                "approach_until_contact",
                "move_cartesian_offset_and_return",
            },
            "unitree-go2-stock-12dof": {
                "track_planar_twist",
                "move_body_relative_pose",
                "trace_planar_path",
                "set_body_height",
                "hold_stable_stance",
            },
        }
        for plan in self.oracle["plans"]:
            leaves = plan["ordered_leaves"]
            self.assertTrue(leaves)
            for leaf in leaves:
                self.assertEqual(set(leaf), {"capability_name", "request"})
                self.assertIn(
                    leaf["capability_name"],
                    expected_methods[plan["robot_configuration_id"]],
                )
                self.assertNotIn("task_id", leaf["request"])
                self.assertNotIn("task_parameters", leaf["request"])

    def test_plan_asset_contains_no_private_verdict_definition_fields(self) -> None:
        self.validator._assert_no_private_fields(self.oracle)


if __name__ == "__main__":
    unittest.main()
