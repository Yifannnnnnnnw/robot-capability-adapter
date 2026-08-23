from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EXPERIMENT_ROOT.parents[1]
for path in (REPOSITORY_ROOT, REPOSITORY_ROOT / "autoadapter" / "src"):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from experiment.experiment1a_generation.runtime.b1 import (  # noqa: E402
    B1RunError,
    resolve_experiment_manifest,
)


class Experiment1ManifestTests(unittest.TestCase):
    def test_core_manifest_matches_the_authority_matrix(self) -> None:
        resolved = resolve_experiment_manifest(EXPERIMENT_ROOT / "manifest.json")

        self.assertEqual(resolved["robot_ids"], ["robotstudio_so101", "unitree-go2-stock-12dof"])
        self.assertEqual(resolved["robot_count"], 2)
        self.assertEqual(resolved["backbone_ids"], ["M1", "M2", "M3", "M4", "M5", "M6", "M8"])
        self.assertEqual(resolved["backbone_count"], 7)
        self.assertEqual(resolved["replicate_count"], 3)
        self.assertEqual(
            resolved["condition_counts"],
            {"from-scratch": 42, "skeleton-assisted": 42},
        )
        self.assertEqual(resolved["unit_count"], 84)
        self.assertEqual(resolved["maximum_submitted_driver_attempts"], 252)
        self.assertEqual(resolved["extension_unit_counts"], {"r04": 28, "r05": 28})
        self.assertEqual(resolved["cumulative_unit_count"], 140)
        self.assertTrue(resolved["formal_dispatch_enabled"])

    def test_every_core_block_contains_both_isolated_conditions(self) -> None:
        resolved = resolve_experiment_manifest(EXPERIMENT_ROOT / "manifest.json")
        conditions_by_pair: dict[str, set[str]] = {}
        for unit in resolved["units"]:
            conditions_by_pair.setdefault(unit["condition_pair_id"], set()).add(
                unit["generation_condition"]
            )

        self.assertEqual(len(conditions_by_pair), 2 * 7 * 3)
        self.assertTrue(
            all(
                conditions == {"skeleton-assisted", "from-scratch"}
                for conditions in conditions_by_pair.values()
            )
        )

    def test_manifest_points_to_the_fixed_bundle_set_and_enables_ready_dispatch(self) -> None:
        recipe = json.loads(
            (EXPERIMENT_ROOT / "manifest.json").read_text(encoding="utf-8")
        )
        resolved = resolve_experiment_manifest(EXPERIMENT_ROOT / "manifest.json")

        self.assertNotIn("status", recipe)
        self.assertEqual(recipe["authority_revision"], "0.1.19")
        self.assertEqual(recipe["execution_concurrency"]["status"], "operational")
        self.assertTrue(resolved["formal_dispatch_enabled"])
        self.assertEqual(
            resolved["fixed_validation_bundle_set"],
            "validation/fixed_validation_bundles/index.json",
        )
        self.assertEqual(resolved["blocked_reasons"], [])

    def test_derived_files_lock_the_authority_selection(self) -> None:
        recipe = json.loads(
            (EXPERIMENT_ROOT / "manifest.json").read_text(encoding="utf-8")
        )
        robots = json.loads(
            (EXPERIMENT_ROOT / "config" / "components" / "robot-set.json").read_text(
                encoding="utf-8"
            )
        )["robot_configuration_ids"]
        replicates = json.loads(
            (
                EXPERIMENT_ROOT
                / "config" / "components"
                / "replicates-core-r3.json"
            ).read_text(encoding="utf-8")
        )["replicate_ids"]
        backbone_set = json.loads(
            (
                EXPERIMENT_ROOT
                / "config" / "components"
                / "backbone-set-m1-m6-m8.json"
            ).read_text(encoding="utf-8")
        )
        backbones = backbone_set["backbone_ids"]

        self.assertEqual(robots, ["robotstudio_so101", "unitree-go2-stock-12dof"])
        self.assertEqual(backbones, ["M1", "M2", "M3", "M4", "M5", "M6", "M8"])
        self.assertEqual(backbone_set["inactive_historical_backbone_ids"], ["M7"])
        self.assertEqual(
            recipe["backbone_runtime_configs"],
            {
                "M1": "config/providers/M1-company-api-sonnet-4-6.json",
                "M2": "config/providers/M2-company-api-opus-5.json",
                "M3": "config/providers/M3-company-api-haiku-4-5.json",
                "M4": "config/providers/M4-company-api-nova-pro.json",
                "M5": "config/providers/M5-deepseek-v4-pro.json",
                "M6": "config/providers/M6-company-api-ministral-3-8b.json",
                "M8": "config/providers/M8-company-api-gpt-5-6-sol.json",
            },
        )
        self.assertEqual(recipe["model_execution_policy"], "remote-hosted-api-only")

        for backbone_id, config_path in recipe["backbone_runtime_configs"].items():
            config = json.loads(
                (EXPERIMENT_ROOT / config_path).read_text(encoding="utf-8")
            )
            self.assertEqual(config["backbone_id"], backbone_id)
            if backbone_id == "M8":
                self.assertEqual(config["context_limit_tokens"], 1_050_000)
                self.assertEqual(config["provider_max_output_tokens"], 128_000)
                self.assertIsNone(config["provider_model_revision"])
                self.assertEqual(
                    config["upstream_revision_status"],
                    "not_independently_verifiable",
                )
                self.assertEqual(config["exact_model_id"], "openai.gpt-5.6-sol")
                self.assertEqual(
                    config["expected_returned_model_id"], "openai.gpt-5.6-sol"
                )
                self.assertEqual(
                    config["endpoint_base_url"].rstrip("/") + config["endpoint_path"],
                    "https://q7s6v6seerne7eyh5ttsovjjcu0hxbou.lambda-url.eu-west-2.on.aws/v1/chat/completions",
                )
                self.assertEqual(
                    config["limits_scope"],
                    "OpenAI public model specification; company-gateway enforcement not independently verified",
                )
                self.assertEqual(
                    config["limits_source"],
                    "https://developers.openai.com/api/docs/models/gpt-5.6-sol",
                )
                self.assertEqual(
                    config["price_snapshot"],
                    {
                        "snapshot_date": "2026-08-23",
                        "currency": "USD",
                        "unit": "per_1m_tokens",
                        "input_cache_hit": 0.5,
                        "input_cache_miss": 5.0,
                        "output": 30.0,
                        "long_context": {
                            "applies_when_input_tokens_gt": 272_000,
                            "input_cache_hit": 1.0,
                            "input_cache_miss": 10.0,
                            "output": 45.0,
                        },
                        "cost_basis": "public_standard_reference_estimate",
                        "pricing_scope": (
                            "OpenAI public Standard API reference; "
                            "company-gateway billing not independently verified"
                        ),
                        "source": "https://platform.openai.com/pricing",
                    },
                )
            else:
                self.assertGreater(config["context_limit_tokens"], 0)
                self.assertGreater(config["provider_max_output_tokens"], 0)
                self.assertLessEqual(
                    config["inference_settings"]["max_tokens"],
                    config["provider_max_output_tokens"],
                )
                self.assertGreaterEqual(config["price_snapshot"]["input_cache_miss"], 0)
                self.assertGreaterEqual(config["price_snapshot"]["output"], 0)

        m1 = json.loads(
            (
                EXPERIMENT_ROOT
                / "config"
                / "providers"
                / "M1-company-api-sonnet-4-6.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(m1["backbone_id"], "M1")
        self.assertEqual(m1["deployment_mode"], "company-hosted-api")
        self.assertEqual(m1["exact_model_id"], "eu.anthropic.claude-sonnet-4-6")
        self.assertEqual(m1["transport"], "openai-compatible")
        self.assertEqual(m1["auth_header"], "X-Api-Key")
        self.assertEqual(m1["inference_settings"]["temperature"], 0.0)
        self.assertEqual(m1["price_snapshot"]["snapshot_date"], "2026-08-21")

        m2 = json.loads(
            (
                EXPERIMENT_ROOT
                / "config"
                / "providers"
                / "M2-company-api-opus-5.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(m2["authority_revision"], "0.1.17")
        self.assertEqual(m2["inference_settings"]["max_tokens"], 32768)
        self.assertEqual(m2["inference_settings"]["timeout_s"], 600)

        m5 = json.loads(
            (
                EXPERIMENT_ROOT
                / "config"
                / "providers"
                / "M5-deepseek-v4-pro.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(m5["authority_revision"], "0.1.8")
        self.assertEqual(m5["inference_settings"]["timeout_s"], 600)

        m6 = json.loads(
            (
                EXPERIMENT_ROOT
                / "config"
                / "providers"
                / "M6-company-api-ministral-3-8b.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            m6["inference_settings"]["tool_history_mode"],
            "text-observation",
        )

        self.assertEqual(replicates, ["r01", "r02", "r03"])
        self.assertEqual(recipe["extension_replicate_ids"], ["r04", "r05"])
        self.assertEqual(recipe["maximum_cumulative_generation_condition_replicates"], 140)
        self.assertEqual(recipe["maximum_cumulative_submitted_driver_attempts"], 420)
        self.assertEqual(recipe["extension_generation_condition_replicates"], {"r04": 28, "r05": 28})
        self.assertEqual(recipe["maximum_extension_submitted_driver_attempts"], {"r04": 84, "r05": 84})
        self.assertFalse(recipe["run_task_demo"])
        self.assertFalse(recipe["run_high_level_controller"])
        self.assertFalse(recipe["run_evolution"])
        self.assertEqual(recipe["experience_input"], "empty")

    def _temporary_recipe(self, **changes: object) -> dict[str, object]:
        recipe = json.loads(
            (EXPERIMENT_ROOT / "manifest.json").read_text(encoding="utf-8")
        )
        recipe["protocol"] = str(
            REPOSITORY_ROOT / "AutoAdapter-Bench" / "protocols" / "b1-driver-synthesis.json"
        )
        recipe["backbone_set"] = str(
            EXPERIMENT_ROOT / "config" / "components" / "backbone-set-m1-m6-m8.json"
        )
        recipe["replicate_set"] = str(
            EXPERIMENT_ROOT / "config" / "components" / "replicates-core-r3.json"
        )
        for assignment in recipe["condition_coverage"]:
            assignment["robot_set"] = str(
                EXPERIMENT_ROOT / "config" / "components" / "robot-set.json"
            )
        recipe.update(changes)
        return recipe

    def test_resolver_rejects_a_b1_high_level_controller(self) -> None:
        recipe = self._temporary_recipe(run_high_level_controller=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-b1.json"
            path.write_text(json.dumps(recipe), encoding="utf-8")
            with self.assertRaises(B1RunError):
                resolve_experiment_manifest(path)

    def test_resolver_rejects_b1_evolution(self) -> None:
        recipe = self._temporary_recipe(run_evolution=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-b1-evolution.json"
            path.write_text(json.dumps(recipe), encoding="utf-8")
            with self.assertRaises(B1RunError):
                resolve_experiment_manifest(path)

    def test_resolver_rejects_non_boolean_formal_dispatch_flag(self) -> None:
        recipe = self._temporary_recipe(formal_dispatch_enabled="false")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-formal-dispatch.json"
            path.write_text(json.dumps(recipe), encoding="utf-8")
            with self.assertRaisesRegex(B1RunError, "must be a boolean"):
                resolve_experiment_manifest(path)

    def test_resolver_rejects_enabled_dispatch_with_unresolved_blocker(self) -> None:
        recipe = self._temporary_recipe(
            formal_dispatch_enabled=True,
            blocked_reasons=["synthetic unresolved blocker"],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blocked-formal-dispatch.json"
            path.write_text(json.dumps(recipe), encoding="utf-8")
            with self.assertRaisesRegex(B1RunError, "while blockers remain"):
                resolve_experiment_manifest(path)


if __name__ == "__main__":
    unittest.main()
