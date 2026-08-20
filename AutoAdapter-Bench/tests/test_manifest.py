from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARK_ROOT / "runners"))

import manifest  # noqa: E402


class ManifestContractTests(unittest.TestCase):
    def test_b1_resolves_approved_asymmetric_matrix(self) -> None:
        resolved = manifest.resolve_b1()

        self.assertEqual(resolved["robot_count"], 14)
        self.assertEqual(resolved["backbone_count"], 7)
        self.assertEqual(resolved["replicate_count"], 5)
        self.assertEqual(
            resolved["condition_counts"],
            {"from-scratch": 175, "skeleton-assisted": 490},
        )
        self.assertEqual(resolved["unit_count"], 665)
        self.assertEqual(resolved["maximum_submitted_driver_attempts"], 1995)

    def test_b1_stops_before_task_demo_and_pairs_selected_cases(self) -> None:
        protocol = json.loads(
            (BENCHMARK_ROOT / "protocols" / "b1-driver-synthesis.json").read_text()
        )
        resolved = manifest.resolve_b1()
        conditions_by_pair: dict[str, set[str]] = {}
        for unit in resolved["units"]:
            conditions_by_pair.setdefault(unit["condition_pair_id"], set()).add(
                unit["generation_condition"]
            )

        paired = sum(conditions == {"skeleton-assisted", "from-scratch"} for conditions in conditions_by_pair.values())
        primary_only = sum(conditions == {"skeleton-assisted"} for conditions in conditions_by_pair.values())
        self.assertFalse(protocol["run_task_demo"])
        self.assertFalse(protocol["run_high_level_controller"])
        self.assertEqual(paired, 5 * 7 * 5)
        self.assertEqual(primary_only, 9 * 7 * 5)

    def test_full_robot_set_preserves_missing_target_without_substitution(self) -> None:
        resolved = manifest.resolve_b1()
        package_state = resolved["package_state"]

        self.assertEqual(
            package_state["missing_package_ids"],
            ["boston_dynamics_spot_with_arm"],
        )
        self.assertEqual(
            package_state["runnable_index_ids"],
            ["robotstudio_so101", "unitree-go2-stock-12dof"],
        )
        self.assertFalse(package_state["all_units_runnable"])

    def test_b2_size_is_visible_while_execution_remains_blocked(self) -> None:
        resolved = manifest.resolve_b2()

        self.assertEqual(resolved["planned_episode_count_if_admitted"], 280)
        self.assertFalse(resolved["ready_to_expand"])
        self.assertTrue(resolved["blockers"])

    def test_high_level_controller_catalogue_has_audit_files(self) -> None:
        root = BENCHMARK_ROOT / "high_level_controllers"
        registry = json.loads((root / "registry.json").read_text())
        controller_ids = {
            "code_bt",
            "llm_bt_planner",
            "inner_monologue",
            "saycan",
            "react",
            "code_as_policies",
            "uhbtp",
            "hbtp",
        }

        self.assertEqual(
            {entry["id"] for entry in registry["controllers"]}, controller_ids
        )
        for entry in registry["controllers"]:
            directory = root / entry["directory"]
            self.assertTrue((directory / "SOURCE.md").is_file())
            self.assertTrue((directory / "controller.json").is_file())


if __name__ == "__main__":
    unittest.main()
