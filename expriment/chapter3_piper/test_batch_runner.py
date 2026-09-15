"""Batch-control unit fixtures only; these never produce experimental evidence."""
from contextlib import redirect_stdout
import csv
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run


class BatchRunnerTest(unittest.TestCase):
    def test_five_attempts_survive_one_failure_and_keep_unscored_tasks_blank(self):
        cfg = run.config()
        for task in cfg["tasks"]:
            task["config"] = str(run.HERE / task["config"])
        calls = []

        def fixture_trial(settings, run_id, root):
            calls.append(run_id)
            if run_id == "run_02":
                raise RuntimeError("named unit fixture: early generation failure")
            records = []
            for task, layout, _ in run.episodes(settings):
                report = {"ok": True, "physical_task_success": True, "task_metrics": [{"ok": True}]}
                if run_id == "run_01" and task["name"] == "reach":
                    if layout == "L1":
                        report = {"ok": True, "physical_task_success": None}
                    elif layout == "L2":
                        report.update(physical_task_success=False, task_metrics=[{"ok": False}])
                path = root / run_id / "episodes" / task["name"] / layout / "task" / "task_report.json"
                run.write_json(path, report)
                records.append({"task_id": task["id"], "layout": layout})
            run.write_json(root / run_id / "run_result.json", {
                "driver_validation_pass": True, "export_ok": True,
                "downstream_eligible": True, "episodes": records})

        with tempfile.TemporaryDirectory(prefix="ch3-batch-test-") as directory:
            root = Path(directory)
            with patch.object(run, "HERE", root), patch.object(run, "prepare"), \
                    patch.object(run, "check", return_value=True), \
                    patch.object(run, "run_trial", side_effect=fixture_trial), redirect_stdout(io.StringIO()):
                run.run_experiment(cfg)
            self.assertEqual(calls, cfg["run_ids"])
            summary = run.read_json(root / "data/runs/summary.json")
            self.assertEqual(summary["recorded_runs"], 5)
            self.assertEqual(summary["driver_validation_passes"], 4)
            self.assertEqual(summary["planned_task_slots"], 45)
            self.assertEqual(summary["attempted_task_episodes"], 36)
            self.assertEqual(summary["independently_scored_task_reports"], 35)
            self.assertEqual(summary["physical_task_successes"], 34)
            self.assertEqual(summary["physical_task_failures"], 1)
            with (root / "data/runs/episodes.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            missing = [row for row in rows if row["run_id"] == "run_02"]
            self.assertEqual(len(missing), 9)
            self.assertTrue(all(row["physical_task_success"] == "" for row in missing))

    def test_existing_cohort_is_rejected_before_preparation_or_model_calls(self):
        cfg = run.config()
        with tempfile.TemporaryDirectory(prefix="ch3-batch-test-") as directory:
            root = Path(directory)
            existing = root / "data/runs/run_03"
            existing.mkdir(parents=True)
            marker = existing / "user-data.txt"
            marker.write_text("preserve")
            with patch.object(run, "HERE", root), patch.object(run, "prepare") as prepare, \
                    patch.object(run, "run_trial") as trial:
                with self.assertRaises(FileExistsError):
                    run.run_experiment(cfg)
                prepare.assert_not_called()
                trial.assert_not_called()
            self.assertEqual(marker.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
