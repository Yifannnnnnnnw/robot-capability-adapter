"""Focused unit fixtures for final-verdict reporting; no model requests."""
import copy
import tempfile
from pathlib import Path
import unittest

import run


class ReportingTest(unittest.TestCase):
    def setUp(self):
        self.plan = run.make_plan(run.read_json(run.HERE / "configs/franka.json"), ["franka"], [1])
        self.cell = self.plan["cells"][0]
        self.report = {"all_ok": True, "n_total": 2, "n_passed": 2}
        self.pipeline = {"stage1_ok": True, "framework_ok": True, "phases": [
            {"name": "03_validate", "ok": True,
             "metadata": {"validation_report": self.report}}]}

    def row(self, pipeline, **process):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            run.write_json(directory / "result.json", {"pipeline": pipeline, "error": None})
            run.write_json(directory / "process.json", {"returncode": 0, **process})
            return run.result_row(self.plan, self.cell, directory)

    def test_final_submission_owns_verdict(self):
        self.assertTrue(self.row(self.pipeline)["driver_passes_own_suite"])
        last = copy.deepcopy(self.pipeline["phases"][0])
        last.update(ok=False, error="design validation failed")
        last["metadata"]["validation_report"].update(all_ok=False, n_passed=1)
        self.pipeline["phases"].append(last)
        self.pipeline.update(stage1_ok=False, framework_ok=False)
        row = self.row(self.pipeline)
        self.assertFalse(row["driver_passes_own_suite"])
        self.assertEqual((row["cases_passed"], row["validation_submissions"]), (1, 2))

    def test_empty_or_interrupted_result_is_not_success(self):
        self.assertIsNone(self.row(self.pipeline, interrupted=True)["driver_passes_own_suite"])
        self.report.update(n_total=0, n_passed=0)
        self.assertFalse(self.row(self.pipeline)["driver_passes_own_suite"])

    def test_from_scratch_native_report_and_tokens(self):
        self.pipeline.update(validate_report=self.report, total_tokens={"in": 123, "out": 45},
                             phases=[{"name": "validate", "ok": True, "error": None}])
        row = self.row(self.pipeline)
        self.assertTrue(row["driver_passes_own_suite"])
        self.assertEqual((row["input_tokens"], row["output_tokens"]), (123, 45))
        self.pipeline.update(stage1_ok=False, framework_ok=False,
                             error="Framework validation failed")
        self.pipeline["phases"][-1]["ok"] = False
        self.report.update(all_ok=False, n_passed=1)
        row = self.row(self.pipeline)
        self.assertFalse(row["driver_passes_own_suite"])
        self.assertEqual(row["failure_stage"], "validate")
        self.assertEqual(row["error"], "Framework validation failed")


if __name__ == "__main__":
    unittest.main()
