from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EXPERIMENT_ROOT.parents[1]
RESOLVER_PATH = REPOSITORY_ROOT / "AutoAdapter-Bench" / "runners" / "manifest.py"


def _load_resolver():
    spec = importlib.util.spec_from_file_location("autoadapter_bench_manifest", RESOLVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load manifest resolver: {RESOLVER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manifest = _load_resolver()


class Experiment1ManifestTests(unittest.TestCase):
    def test_core_manifest_matches_the_authority_matrix(self) -> None:
        resolved = manifest.resolve_b1(EXPERIMENT_ROOT / "manifest.json")

        self.assertEqual(resolved["robot_count"], 5)
        self.assertEqual(resolved["backbone_count"], 7)
        self.assertEqual(resolved["replicate_count"], 3)
        self.assertEqual(
            resolved["condition_counts"],
            {"from-scratch": 105, "skeleton-assisted": 105},
        )
        self.assertEqual(resolved["unit_count"], 210)
        self.assertEqual(resolved["maximum_submitted_driver_attempts"], 630)

    def test_every_block_contains_both_isolated_conditions(self) -> None:
        resolved = manifest.resolve_b1(EXPERIMENT_ROOT / "manifest.json")
        conditions_by_pair: dict[str, set[str]] = {}
        for unit in resolved["units"]:
            conditions_by_pair.setdefault(unit["condition_pair_id"], set()).add(
                unit["generation_condition"]
            )

        self.assertEqual(len(conditions_by_pair), 5 * 7 * 3)
        self.assertTrue(
            all(
                conditions == {"skeleton-assisted", "from-scratch"}
                for conditions in conditions_by_pair.values()
            )
        )

    def test_manifest_points_to_the_connected_fixed_bundle_set(self) -> None:
        recipe = json.loads(
            (EXPERIMENT_ROOT / "manifest.json").read_text(encoding="utf-8")
        )
        resolved = manifest.resolve_b1(EXPERIMENT_ROOT / "manifest.json")

        self.assertNotIn("status", recipe)
        self.assertEqual(recipe["authority_revision"], "0.1.5")
        self.assertEqual(recipe["execution_concurrency"]["status"], "operational")
        self.assertTrue(resolved["ready_to_expand"])
        self.assertEqual(
            resolved["fixed_validation_bundle_set"],
            "experiment/experiment1/fixed_validation_bundles/index.json",
        )
        self.assertEqual(resolved["blockers"], [])

    def test_derived_files_lock_the_authority_selection(self) -> None:
        recipe = json.loads(
            (EXPERIMENT_ROOT / "manifest.json").read_text(encoding="utf-8")
        )
        robots = json.loads(
            (EXPERIMENT_ROOT / "components" / "robot-set.json").read_text(
                encoding="utf-8"
            )
        )["robot_configuration_ids"]
        replicates = json.loads(
            (
                EXPERIMENT_ROOT
                / "components"
                / "replicates-core-r3.json"
            ).read_text(encoding="utf-8")
        )["replicate_ids"]
        backbones = json.loads(
            (
                REPOSITORY_ROOT
                / "AutoAdapter-Bench"
                / "components"
                / "backbone_sets"
                / "declared-seven.json"
            ).read_text(encoding="utf-8")
        )["backbone_ids"]

        self.assertEqual(
            robots,
            [
                "robotstudio_so101",
                "unitree-go2-stock-12dof",
                "leap_hand",
                "hello_robot_stretch_2",
                "aloha_2",
            ],
        )
        self.assertEqual(backbones, ["M1", "M2", "M3", "M4", "M5", "M6", "M7"])
        self.assertEqual(
            recipe["backbone_runtime_configs"],
            {
                "M1": "providers/M1-company-api-sonnet-4-6.json",
                "M2": "providers/M2-company-api-opus-5.json",
                "M3": "providers/M3-company-api-haiku-4-5.json",
                "M4": "providers/M4-company-api-nova-pro.json",
                "M5": "providers/M5-deepseek-v4-pro.json",
                "M6": "providers/M6-company-api-ministral-3-8b.json",
                "M7": "providers/M7-company-api-qwen3-32b.json",
            },
        )
        self.assertEqual(recipe["model_execution_policy"], "remote-hosted-api-only")
        m1 = json.loads(
            (EXPERIMENT_ROOT / "providers" / "M1-company-api-sonnet-4-6.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(m1["backbone_id"], "M1")
        self.assertEqual(m1["deployment_mode"], "company-hosted-api")
        self.assertEqual(m1["exact_model_id"], "eu.anthropic.claude-sonnet-4-6")
        self.assertEqual(m1["transport"], "openai-compatible")
        self.assertEqual(m1["auth_header"], "X-Api-Key")
        self.assertEqual(m1["inference_settings"]["temperature"], 0.0)
        self.assertEqual(m1["price_snapshot"]["snapshot_date"], "2026-08-21")
        for config_path in recipe["backbone_runtime_configs"].values():
            config = json.loads(
                (EXPERIMENT_ROOT / config_path).read_text(encoding="utf-8")
            )
            if config["backbone_id"] == "M7":
                continue
            self.assertGreater(config["context_limit_tokens"], 0)
            self.assertGreater(config["provider_max_output_tokens"], 0)
            self.assertLessEqual(
                config["inference_settings"]["max_tokens"],
                config["provider_max_output_tokens"],
            )
            self.assertGreaterEqual(
                config["price_snapshot"]["input_cache_miss"], 0
            )
            self.assertGreaterEqual(config["price_snapshot"]["output"], 0)
        for config_name in (
            "M6-company-api-ministral-3-8b.json",
            "M7-company-api-qwen3-32b.json",
        ):
            config = json.loads(
                (EXPERIMENT_ROOT / "providers" / config_name).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                config["inference_settings"]["tool_history_mode"],
                "text-observation",
            )
        self.assertEqual(replicates, ["r01", "r02", "r03"])
        self.assertEqual(recipe["extension_replicate_ids"], ["r04", "r05"])
        self.assertEqual(recipe["maximum_cumulative_generation_condition_replicates"], 350)
        self.assertEqual(recipe["maximum_cumulative_submitted_driver_attempts"], 1050)
        self.assertFalse(recipe["run_task_demo"])
        self.assertFalse(recipe["run_high_level_controller"])
        self.assertFalse(recipe["run_evolution"])
        self.assertEqual(recipe["experience_input"], "empty")
        self.assertEqual(recipe["execution_concurrency"]["status"], "operational")

    def test_resolver_rejects_a_b1_high_level_controller(self) -> None:
        recipe = json.loads(
            (EXPERIMENT_ROOT / "manifest.json").read_text(encoding="utf-8")
        )
        recipe["protocol"] = str(
            REPOSITORY_ROOT
            / "AutoAdapter-Bench"
            / "protocols"
            / "b1-driver-synthesis.json"
        )
        recipe["backbone_set"] = str(
            REPOSITORY_ROOT
            / "AutoAdapter-Bench"
            / "components"
            / "backbone_sets"
            / "declared-seven.json"
        )
        recipe["replicate_set"] = str(
            EXPERIMENT_ROOT / "components" / "replicates-core-r3.json"
        )
        for assignment in recipe["condition_coverage"]:
            assignment["robot_set"] = str(
                EXPERIMENT_ROOT / "components" / "robot-set.json"
            )
        recipe["run_high_level_controller"] = True

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-b1.json"
            path.write_text(json.dumps(recipe), encoding="utf-8")
            with self.assertRaises(manifest.ManifestError):
                manifest.resolve_b1(path)

    def test_resolver_rejects_b1_evolution(self) -> None:
        recipe = json.loads(
            (EXPERIMENT_ROOT / "manifest.json").read_text(encoding="utf-8")
        )
        recipe["protocol"] = str(
            REPOSITORY_ROOT
            / "AutoAdapter-Bench"
            / "protocols"
            / "b1-driver-synthesis.json"
        )
        recipe["backbone_set"] = str(
            REPOSITORY_ROOT
            / "AutoAdapter-Bench"
            / "components"
            / "backbone_sets"
            / "declared-seven.json"
        )
        recipe["replicate_set"] = str(
            EXPERIMENT_ROOT / "components" / "replicates-core-r3.json"
        )
        for assignment in recipe["condition_coverage"]:
            assignment["robot_set"] = str(
                EXPERIMENT_ROOT / "components" / "robot-set.json"
            )
        recipe["run_evolution"] = True

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-b1-evolution.json"
            path.write_text(json.dumps(recipe), encoding="utf-8")
            with self.assertRaises(manifest.ManifestError):
                manifest.resolve_b1(path)


if __name__ == "__main__":
    unittest.main()
