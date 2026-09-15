"""Focused regression: diagnostic controller completion is not task success."""
import unittest

from run import physical_verdict


class ReportingTest(unittest.TestCase):
    def test_controller_completion_cannot_be_counted_as_physical_success(self):
        report = {"ok": True, "status": "CONTROLLER_FINISHED", "sim_advanced": True,
                  "video_path": "video.mp4", "physical_task_success": None,
                  "scope": "diagnostic task execution; no independent task predicate evaluated"}
        self.assertIsNone(physical_verdict(report))
        report["physical_task_success"] = True
        self.assertIsNone(physical_verdict(report))
        self.assertIs(physical_verdict({"physical_task_success": False,
                                      "task_metrics": [],
                                      "scope": "independent task evaluation"}), False)

    def test_independently_scored_diagnostic_is_retained(self):
        report = {"physical_task_success": True, "task_metrics": [{"ok": True}],
                  "scope": "diagnostic task execution with independent physical task evaluation"}
        self.assertIs(physical_verdict(report), True)
        report["evaluation_error"] = "physical trace is incomplete"
        self.assertIsNone(physical_verdict(report))


if __name__ == "__main__":
    unittest.main()
