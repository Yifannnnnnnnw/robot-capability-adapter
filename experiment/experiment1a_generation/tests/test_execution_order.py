from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EXPERIMENT_ROOT.parents[1]
for path in (REPOSITORY_ROOT, REPOSITORY_ROOT / "autoadapter" / "src"):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from experiment.experiment1a_generation.runtime.b1 import (  # noqa: E402
    resolve_experiment_manifest,
)


class Experiment1ExecutionOrderTests(unittest.TestCase):
    def test_seed_map_and_all_paired_blocks_are_prospectively_fixed(self) -> None:
        components = EXPERIMENT_ROOT / "config" / "components"
        replicate_set = json.loads(
            (components / "replicates-core-r3.json").read_text(encoding="utf-8")
        )
        execution_order = json.loads(
            (components / "execution-order-r1-r5.json").read_text(encoding="utf-8")
        )

        self.assertEqual(replicate_set["replicate_ids"], ["r01", "r02", "r03"])
        self.assertEqual(replicate_set["extension_replicate_ids"], ["r04", "r05"])
        self.assertEqual(
            replicate_set["seed_map"],
            [
                {"replicate_id": "r01", "role": "core", "seed": 583329363},
                {"replicate_id": "r02", "role": "core", "seed": 114217382},
                {"replicate_id": "r03", "role": "core", "seed": 545636081},
                {
                    "replicate_id": "r04",
                    "role": "extension-only",
                    "seed": 366765176,
                },
                {
                    "replicate_id": "r05",
                    "role": "extension-only",
                    "seed": 1379476005,
                },
            ],
        )
        self.assertEqual(
            len({entry["seed"] for entry in replicate_set["seed_map"]}), 5
        )
        self.assertEqual(
            replicate_set["execution_order"], "execution-order-r1-r5.json"
        )
        self.assertEqual(replicate_set["authority_revision"], "0.2.0")
        self.assertEqual(execution_order["authority_revision"], "0.2.0")

        robots = [
            "robotstudio_so101",
            "unitree-go2-stock-12dof",
        ]
        backbones = ["M1", "M2", "M3", "M4", "M5", "M6", "M8"]
        wave_specs = [
            (
                "core-r3",
                "core-primary",
                ["r01", "r02", "r03"],
                12928008,
                21,
            ),
            ("extension-r04", "extension-only", ["r04"], 1558252846, 7),
            ("extension-r05", "extension-only", ["r05"], 1014548398, 7),
        ]

        expected_all_units: set[str] = set()
        expected_core_units: set[str] = set()
        self.assertEqual(len(execution_order["waves"]), len(wave_specs))
        for wave, (
            wave_id,
            role,
            replicate_ids,
            order_seed,
            skeleton_first_count,
        ) in zip(
            execution_order["waves"], wave_specs, strict=True
        ):
            pair_ids = [
                f"b1::{robot}::{backbone}::{replicate_id}"
                for robot in robots
                for backbone in backbones
                for replicate_id in replicate_ids
            ]
            generator = random.Random(order_seed)
            generator.shuffle(pair_ids)
            first_conditions = (
                ["skeleton-assisted"] * skeleton_first_count
                + ["from-scratch"] * (len(pair_ids) - skeleton_first_count)
            )
            generator.shuffle(first_conditions)
            expected_blocks = []
            for pair_id, first_condition in zip(
                pair_ids, first_conditions, strict=True
            ):
                other_condition = (
                    "from-scratch"
                    if first_condition == "skeleton-assisted"
                    else "skeleton-assisted"
                )
                condition_order = [first_condition, other_condition]
                expected_blocks.append(
                    {
                        "condition_pair_id": pair_id,
                        "condition_order": condition_order,
                    }
                )
                units = {f"{pair_id}::{condition}" for condition in condition_order}
                expected_all_units.update(units)
                if role == "core-primary":
                    expected_core_units.update(units)

            self.assertEqual(wave["wave_id"], wave_id)
            self.assertEqual(wave["role"], role)
            self.assertEqual(wave["replicate_ids"], replicate_ids)
            self.assertEqual(wave["order_seed"], order_seed)
            self.assertEqual(wave["block_count"], len(pair_ids))
            self.assertEqual(wave["condition_cell_count"], len(pair_ids) * 2)
            self.assertEqual(
                wave["first_condition_counts"],
                {
                    "skeleton-assisted": skeleton_first_count,
                    "from-scratch": len(pair_ids) - skeleton_first_count,
                },
            )
            self.assertEqual(wave["blocks"], expected_blocks)

        resolved = resolve_experiment_manifest(EXPERIMENT_ROOT / "manifest.json")
        resolved_core_units = {unit["unit_id"] for unit in resolved["units"]}
        self.assertEqual(expected_core_units, resolved_core_units)
        self.assertEqual(len(expected_core_units), 84)
        self.assertEqual(len(expected_all_units), 140)
        self.assertEqual(execution_order["total_block_count"], 70)
        self.assertEqual(execution_order["total_condition_cell_count"], 140)


if __name__ == "__main__":
    unittest.main()
