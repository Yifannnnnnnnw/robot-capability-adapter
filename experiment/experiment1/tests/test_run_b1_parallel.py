from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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
            """import argparse, json, os, time
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
safe_environment = {
    'provider': os.environ.get('AUTOADAPTER_MODEL_PROVIDER'),
    'vendor': os.environ.get('AUTOADAPTER_MODEL_VENDOR'),
    'model_id': os.environ.get('AUTOADAPTER_MODEL_ID'),
    'base_url': os.environ.get('AUTOADAPTER_MODEL_API_BASE_URL'),
    'auth_header': os.environ.get('AUTOADAPTER_MODEL_API_AUTH_HEADER'),
    'auth_prefix': os.environ.get('AUTOADAPTER_MODEL_API_AUTH_PREFIX'),
    'thinking': os.environ.get('AUTOADAPTER_MODEL_THINKING'),
    'max_tokens': os.environ.get('AUTOADAPTER_MODEL_MAX_TOKENS'),
    'tool_history_mode': os.environ.get('AUTOADAPTER_MODEL_TOOL_HISTORY_MODE'),
    'history_chars': os.environ.get('AUTOADAPTER_MODEL_HISTORY_CHARS'),
    'timeout_s': os.environ.get('AUTOADAPTER_MODEL_TIMEOUT_S'),
    'model_key_present': bool(os.environ.get('AUTOADAPTER_MODEL_API_KEY')),
    'company_key_present': bool(os.environ.get('AUTOADAPTER_COMPANY_API_KEY')),
}
(out / 'cell_record.json').write_text(json.dumps({
    'unit_id': a.unit_id,
    'safe_environment': safe_environment,
}) + '\\n')
""",
            encoding="utf-8",
        )
        output = root / "scheduler"

        company_key = "test-company-secret-not-for-records"
        deepseek_key = "test-deepseek-secret-not-for-records"
        with patch.dict(
            os.environ,
            {
                "AUTOADAPTER_COMPANY_API_KEY": company_key,
                "AUTOADAPTER_MODEL_API_KEY": deepseek_key,
            },
            clear=False,
        ):
            record = launch_parallel(
                manifest_path=EXPERIMENT_ROOT / "manifest.json",
                order_path=EXPERIMENT_ROOT
                / "components"
                / "execution-order-r1-r5.json",
                output_dir=output,
                backbone_ids=["M1", "M2", "M3", "M4", "M5", "M6", "M7"],
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
            self.assertEqual(cell["unit_id"], item["unit_id"])
            config_path = json.loads(
                (EXPERIMENT_ROOT / "manifest.json").read_text(encoding="utf-8")
            )["backbone_runtime_configs"][item["backbone_id"]]
            config = json.loads(
                (EXPERIMENT_ROOT / config_path).read_text(encoding="utf-8")
            )
            settings = config["inference_settings"]
            safe = cell["safe_environment"]
            self.assertEqual(safe["provider"], "openai-compatible")
            self.assertEqual(safe["vendor"], config["vendor"].lower())
            self.assertEqual(safe["model_id"], config["exact_model_id"])
            self.assertEqual(safe["base_url"], config["endpoint_base_url"])
            self.assertEqual(safe["auth_header"], config["auth_header"])
            self.assertEqual(safe["auth_prefix"], config["auth_prefix"])
            self.assertEqual(safe["thinking"], settings["thinking"] or "")
            self.assertEqual(safe["max_tokens"], str(settings["max_tokens"]))
            self.assertEqual(
                safe["tool_history_mode"], settings["tool_history_mode"]
            )
            self.assertEqual(
                safe["history_chars"], str(settings["history_char_budget"])
            )
            self.assertEqual(safe["timeout_s"], str(settings["timeout_s"]))
            self.assertTrue(safe["model_key_present"])
            self.assertFalse(safe["company_key_present"])
        persisted = json.loads(
            (output / "scheduler_record.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["exit_summary"], record["exit_summary"])
        self.assertNotIn(
            company_key,
            (output / "scheduler_record.json").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            deepseek_key,
            (output / "scheduler_record.json").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
