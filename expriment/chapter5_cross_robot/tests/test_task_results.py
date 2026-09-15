"""Focused reporting fixture; no model, driver or simulator execution."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from run import write_json
from run_tasks import prepare, summarize


class TaskReportingTest(unittest.TestCase):
    def test_failed_validation_override_still_checks_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "fixture"
            (workspace / "design").mkdir(parents=True)
            for name in ("driver.py", "mcp_server.py", "design/scene_cases.yaml"):
                (workspace / name).write_text("# Explicit non-executable fixture")
            write_json(workspace / "design/capability_design.json", {"robot_configuration_id": "franka"})
            report = workspace / "validate_report.json"
            write_json(report, {"all_ok": False, "n_passed": 4, "n_total": 5})
            original = report.read_bytes()
            output = Path(temporary) / "tasks"
            with patch("auto_adapter.export_support.validate_dynamic_export_source",
                       side_effect=ValueError("fixture export check")) as check:
                with self.assertRaisesRegex(ValueError, "successful validation report"):
                    prepare(workspace, output)
                check.assert_not_called()
                with self.assertRaisesRegex(ValueError, "fixture export check"):
                    prepare(workspace, output, allow_failed_validation=True)
                check.assert_called_once()
            self.assertEqual(report.read_bytes(), original)
            self.assertFalse(output.exists())

    def test_nonzero_exit_cannot_reuse_a_success_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "requests").mkdir()
            task = output / "reach_central"
            task.mkdir()
            write_json(output / "task_plan.json", {"cases": [
                {"id": "reach_central", "task_id": "mw_reach_target"}]})
            write_json(task / "task_report.json", {
                "ok": True, "execution_ok": True, "physical_task_success": True})
            process = output / "requests/reach_central.process.json"
            write_json(process, {"returncode": 0})
            self.assertEqual(summarize(output)["succeeded"], 1)
            write_json(process, {"returncode": 1})
            self.assertEqual(summarize(output)["succeeded"], 0)


if __name__ == "__main__":
    unittest.main()
