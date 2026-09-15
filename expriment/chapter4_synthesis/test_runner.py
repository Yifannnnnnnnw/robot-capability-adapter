"""Named unit fixtures only: no model requests and no experiment evidence."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = run.read_json(run.HERE / "config.json")

    def test_matrix_and_filtered_worker_forwarding(self):
        plan = run.make_plan(self.cfg)
        self.assertEqual(len(plan["cells"]), 42)
        self.assertEqual(len({c["id"] for c in plan["cells"]}), 42)
        self.assertEqual(sum(c["replicate"] == 1 for c in plan["cells"]), 14)
        selected = run.make_plan(self.cfg, ["gpt56_sol"], ["so101"], [2])
        self.assertEqual(len(selected["cells"]), 1)
        cell = selected["cells"][0]
        captured = {}

        class NamedPipelineFixture:
            def __init__(self, settings):
                captured["settings"] = settings

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def run(self, *, stop_after):
                captured["stop_after"] = stop_after
                return SimpleNamespace(to_json=lambda: {"stage1_ok": False, "phases": []})

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / cell["id"]).mkdir()
            run.write_json(root / "plan.json", selected)
            with patch("auto_adapter.orchestrator.SelfAssemble", NamedPipelineFixture):
                self.assertEqual(run.worker(root / "plan.json", cell["id"]), 0)
            settings = captured["settings"]
            self.assertEqual(settings.robot_id, "menagerie_so101")
            self.assertEqual(settings.bedrock_model, "openai.gpt-5.6-sol")
            self.assertIsNone(settings.capability_design_path)
            self.assertIsNone(settings.scene_cases_path)
            self.assertEqual(settings.max_outer_gen_val_iters, 3)
            self.assertFalse(settings.enable_demo)
            self.assertEqual(captured["stop_after"], "validate")
            with self.assertRaises(FileExistsError):
                run.worker(root / "plan.json", cell["id"])

    def test_deepseek_uses_its_official_provider_only(self):
        plan = run.make_plan(self.cfg, ["deepseek_v4_pro", "sonnet46"], ["go2"], [1])
        with tempfile.TemporaryDirectory() as temp:
            for cell in plan["cells"]:
                settings = run.pipeline_config(self.cfg, cell, Path(temp) / cell["id"])
                expected = "deepseek" if cell["model"]["id"] == "deepseek_v4_pro" else "holistic"
                self.assertEqual(settings.model_provider, expected)
                self.assertEqual(settings.bedrock_model, cell["model"]["model_id"])

    def test_incomplete_or_failed_runs_cannot_inherit_a_passing_report(self):
        plan = run.make_plan(self.cfg, ["sonnet46"], ["go2"], [1, 2, 3])
        passing = {"pipeline": {"stage1_ok": True, "framework_ok": True, "phases": [
            {"name": "03_validate", "ok": True, "token_usage": {"in": 11, "out": 7},
             "metadata": {"validation_report": {"all_ok": True, "n_passed": 4, "n_total": 4}}}]}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run.write_json(root / "plan.json", plan)
            directories = [root / c["id"] for c in plan["cells"]]
            for directory in directories[:2]:
                directory.mkdir()
                run.write_json(directory / "result.json", passing)
                run.write_json(directory / "process.json", {"returncode": 0, "timed_out": False})
            run.write_json(directories[1] / "process.json", {"returncode": -15, "timed_out": True})
            summary = run.summarize(root)
            self.assertEqual(summary["planned_cells"], 3)
            self.assertEqual(summary["completed_processes"], 1)
            self.assertEqual(summary["drivers_passing_own_suite"], 1)
            self.assertIsNone(run.result_row(plan["cells"][1], directories[1])["driver_passes_own_suite"])
            self.assertIsNone(run.result_row(plan["cells"][2], directories[2])["driver_passes_own_suite"])
            failed = copy.deepcopy(passing)
            failed["pipeline"]["stage1_ok"] = False
            failed["pipeline"]["framework_ok"] = False
            failed["pipeline"]["phases"].append({"name": "03_validate", "ok": False,
                "metadata": {"validation_report": {"all_ok": False, "n_passed": 2, "n_total": 4}}})
            run.write_json(directories[0] / "result.json", failed)
            row = run.result_row(plan["cells"][0], directories[0])
            self.assertFalse(row["driver_passes_own_suite"])
            self.assertEqual(row["cases_passed"], 2)
            self.assertEqual(row["validation_attempts"], 2)
            repaired = copy.deepcopy(passing)
            failed_attempt = copy.deepcopy(failed["pipeline"]["phases"][-1])
            failed_attempt["error"] = "named unit fixture: initial validation failed"
            repaired["pipeline"]["phases"].insert(0, failed_attempt)
            run.write_json(directories[0] / "result.json", repaired)
            row = run.result_row(plan["cells"][0], directories[0])
            self.assertTrue(row["driver_passes_own_suite"])
            self.assertIsNone(row["error"])
            run.write_json(directories[0] / "result.json", {"pipeline": {"stage1_ok": True,
                                                                      "framework_ok": True, "phases": []}})
            self.assertFalse(run.result_row(plan["cells"][0], directories[0])["pipeline_ok"])

    def test_existing_batch_is_rejected_before_check_or_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "existing.txt"
            marker.write_text("keep")
            with patch.object(run, "check") as check, patch.object(run, "launch") as launch:
                with self.assertRaises(FileExistsError):
                    run.run_batch(run.make_plan(self.cfg), root)
                check.assert_not_called()
                launch.assert_not_called()
            self.assertEqual(marker.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
