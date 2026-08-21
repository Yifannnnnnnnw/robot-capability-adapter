from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EXPERIMENT_ROOT.parents[1]
AUTOADAPTER_SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter" / "src"
for path in (REPOSITORY_ROOT, AUTOADAPTER_SOURCE_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from experiment.experiment1.runtime.parallel import launch_parallel  # noqa: E402


class Experiment1ParallelRunnerTests(unittest.TestCase):
    def test_eight_workers_get_unique_process_outputs_and_scheduler_records(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        fake_runner = root / "fake_run_b1.py"
        fake_runner.write_text(
            """import argparse, json, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--manifest')
p.add_argument('--unit-id', required=True)
p.add_argument('--output', required=True)
p.add_argument('--use-existing-fixed-route', action='store_true')
a = p.parse_args()
out = Path(a.output)
out.mkdir(parents=True)
time.sleep(0.03)
(out / 'cell_record.json').write_text(json.dumps({'unit_id': a.unit_id}) + '\\n')
""",
            encoding="utf-8",
        )
        output = root / "scheduler"

        record = launch_parallel(
            manifest_path=EXPERIMENT_ROOT / "manifest.json",
            order_path=EXPERIMENT_ROOT
            / "components"
            / "execution-order-r1-r5.json",
            output_dir=output,
            backbone_ids=["M5"],
            robot_ids=["robotstudio_so101", "unitree-go2-stock-12dof"],
            max_workers=8,
            limit=8,
            use_existing_fixed_route=True,
            runner_path=fake_runner,
        )

        self.assertEqual(record["exit_summary"]["process_count"], 8)
        self.assertEqual(record["exit_summary"]["successful_process_count"], 8)
        self.assertEqual(record["exit_summary"]["failed_process_count"], 0)
        self.assertEqual(len({item["cell_output"] for item in record["units"]}), 8)
        self.assertEqual(len({item["unit_id"] for item in record["units"]}), 8)
        for item in record["units"]:
            self.assertEqual(item["exit_code"], 0)
            self.assertIsNotNone(item["started_at_utc"])
            self.assertIsNotNone(item["finished_at_utc"])
            self.assertGreaterEqual(item["queue_elapsed_s"], 0.0)
            self.assertGreaterEqual(item["process_elapsed_s"], 0.0)
            self.assertTrue(item["cell_record_exists"])
            self.assertIn("--use-existing-fixed-route", item["command"])
            cell = json.loads(
                (Path(item["cell_output"]) / "cell_record.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(cell, {"unit_id": item["unit_id"]})
        persisted = json.loads(
            (output / "scheduler_record.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["exit_summary"], record["exit_summary"])


if __name__ == "__main__":
    unittest.main()
