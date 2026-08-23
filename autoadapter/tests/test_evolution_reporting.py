from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from autoadapter2.evolution import run_evolution
from autoadapter2.reporting import (
    ReportingError,
    build_cell_report,
    build_paired_report,
    read_json,
    write_json,
)


class FakeGenerator:
    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None):
        self.response = response if response is not None else {}
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def generate_json(self, *, stage: str, prompt: str, inputs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"stage": stage, "prompt": prompt, "inputs": inputs})
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.response)


def _terminal_report(
    robot: str = "robotstudio_so101",
    condition: str = "from-scratch",
    passed: bool = True,
) -> dict[str, Any]:
    return {
        "run_id": "run-1",
        "robot_configuration_id": robot,
        "model": "test-model",
        "provider": "test-provider",
        "condition": condition,
        "pipeline_completed": True,
        "dynamic_model_called": True,
        "driver_generated_in_run": True,
        "capability_validation_executed": True,
        "initial_capability_validation_passed": passed,
        "final_capability_validation_passed": passed,
        "task_demo_executed": passed,
        "task_demo_passed": passed,
        "task_demo_pipeline_completed": passed,
        "physical_validation_executed": True,
        "initial_validation_passed": passed,
        "final_validation_passed": passed,
        "attempt_count": 1,
        "passed_task_count": 1 if passed else 0,
        "task_count": 1,
        "passed_clause_count": 1 if passed else 0,
        "clause_count": 1,
        "passed_case_count": 1 if passed else 0,
        "case_count": 1,
        "video_complete": True,
        "trials": [
            {
                "trial_id": "case-1-r00",
                "case_id": "case-1",
                "task_id": "task-1",
                "source_clause_id": "clause-1",
                "trial_passed": passed,
                "video": {"complete": True},
            }
        ],
        "task_demo": {
            "pipeline_completed": passed,
            "physical_validation_executed": passed,
            "validation_passed": passed,
            "video_complete": passed,
            "trials": (
                [
                    {
                        "case_id": "demo-case-1",
                        "task_id": "demo-task-1",
                        "source_clause_id": "demo-clause-1",
                        "trial_passed": passed,
                        "video": {"complete": passed},
                    }
                ]
                if passed
                else []
            ),
        },
        "tgcd": {"completed": True},
        "ivc": {"completed": True},
        "study": {"completed": True},
        "generate": {"completed": True},
        "repair": {"attempt_count": 1},
    }


class EvolutionTests(unittest.TestCase):
    def test_one_optional_proposal_is_non_blocking_and_does_not_mutate_report(self) -> None:
        report = _terminal_report()
        original = copy.deepcopy(report)
        client = FakeGenerator(
            {
                "observation": "The candidate reached the target.",
                "lesson": "Keep the control primitive available.",
                "recommendation": "Try the same interface in a later run.",
                "scope": "robotstudio_so101",
                "evidence": ["case-1-r00"],
            }
        )

        outcome = run_evolution(client, report)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["stage"], "evolution")
        self.assertTrue(outcome["terminal_report_read"])
        self.assertTrue(outcome["evolution_completed"])
        self.assertTrue(outcome["proposal_created"])
        self.assertTrue(outcome["current_run_unchanged"])
        self.assertEqual(outcome["model_calls"], [{"stage": "evolution"}])
        self.assertEqual(report, original)

    def test_model_failure_is_recorded_without_changing_terminal_outcome(self) -> None:
        report = _terminal_report(passed=False)
        client = FakeGenerator(error=RuntimeError("temporary model outage"))

        outcome = run_evolution(client, report)

        self.assertTrue(outcome["non_blocking"])
        self.assertEqual(outcome["model_call_count"], 1)
        self.assertTrue(outcome["terminal_report_read"])
        self.assertFalse(outcome["evolution_completed"])
        self.assertFalse(outcome["proposal_created"])
        self.assertEqual(outcome["failure"]["type"], "RuntimeError")
        self.assertFalse(report["final_validation_passed"])

    def test_model_input_keeps_failure_class_but_drops_raw_failure_message(self) -> None:
        report = _terminal_report(passed=False)
        report["trials"][0]["candidate_exception"] = {
            "type": "AttributeError",
            "message": "request is a dict",
        }
        report["trials"][0]["physical_evidence"] = {
            "step_count": 0,
            "samples": [{"qpos": list(range(1000))} for _ in range(20)],
        }
        report["attempts"] = [{"attempt": 0, "validation": copy.deepcopy(report)}]
        client = FakeGenerator({})

        outcome = run_evolution(client, report)

        model_report = client.calls[0]["inputs"]["terminal_report"]
        packed = json.dumps(model_report)
        self.assertNotIn('"samples"', packed)
        self.assertNotIn("request is a dict", packed)
        self.assertEqual(
            model_report["terminal_capability_validation"]["failed_trials"][0]
            ["candidate_exception"],
            {"type": "AttributeError"},
        )
        self.assertEqual(
            model_report["terminal_capability_validation"]["failed_trials"][0]
            ["physical_evidence"]["sample_count"],
            20,
        )
        self.assertTrue(outcome["model_input_compacted"])
        self.assertLess(outcome["model_input_chars"], 10000)

    def test_evolution_does_not_run_before_a_terminal_verdict(self) -> None:
        report = _terminal_report()
        report.pop("final_capability_validation_passed", None)
        report.pop("final_validation_passed", None)
        report.pop("validation_passed", None)
        client = FakeGenerator({})

        outcome = run_evolution(client, report)

        self.assertFalse(outcome["terminal_report_read"])
        self.assertFalse(outcome["evolution_attempted"])
        self.assertEqual(client.calls, [])

    def test_mutating_proposal_is_rejected_and_is_non_blocking(self) -> None:
        client = FakeGenerator({"retry_decision": "retry"})

        outcome = run_evolution(client, _terminal_report())

        self.assertFalse(outcome["evolution_completed"])
        self.assertFalse(outcome["proposal_created"])
        self.assertIn("mutate", outcome["failure"]["message"])


class ReportingTests(unittest.TestCase):
    def test_cell_report_keeps_execution_facts_separate_and_derives_failed_counts(self) -> None:
        raw = _terminal_report(passed=False)
        raw.pop("passed_task_count")
        raw.pop("task_count")
        raw.pop("passed_clause_count")
        raw.pop("clause_count")
        raw.pop("passed_case_count")
        raw.pop("case_count")

        cell = build_cell_report(raw)

        self.assertTrue(cell["pipeline_completed"])
        self.assertTrue(cell["dynamic_model_called"])
        self.assertTrue(cell["driver_generated_in_run"])
        self.assertTrue(cell["physical_validation_executed"])
        self.assertTrue(cell["capability_validation_executed"])
        self.assertFalse(cell["initial_validation_passed"])
        self.assertFalse(cell["final_validation_passed"])
        self.assertFalse(cell["final_capability_validation_passed"])
        self.assertFalse(cell["task_demo_executed"])
        self.assertEqual(cell["task_counts"], {"passed": 0, "total": 1})
        self.assertEqual(cell["clause_counts"], {"passed": 0, "total": 1})
        self.assertEqual(cell["case_counts"], {"passed": 0, "total": 1})

    def test_harness_clause_and_private_case_count_aliases_are_preserved(self) -> None:
        raw = _terminal_report()
        raw.pop("passed_clause_count")
        raw.pop("clause_count")
        raw.pop("passed_case_count")
        raw.pop("case_count")
        raw["passed_source_clause_count"] = 3
        raw["source_clause_count"] = 4
        raw["passed_private_case_count"] = 2
        raw["private_case_count"] = 5

        cell = build_cell_report(raw)

        self.assertEqual(cell["clause_counts"], {"passed": 3, "total": 4})
        self.assertEqual(cell["case_counts"], {"passed": 2, "total": 5})

    def test_paired_report_retains_failed_cell_instead_of_hiding_it(self) -> None:
        cells = [
            _terminal_report("robotstudio_so101", "skeleton-assisted", True),
            _terminal_report("robotstudio_so101", "from-scratch", False),
            _terminal_report("unitree-go2-stock-12dof", "skeleton-assisted", True),
            _terminal_report("unitree-go2-stock-12dof", "from-scratch", True),
        ]

        paired = build_paired_report(
            cells,
            expected_robots=("robotstudio_so101", "unitree-go2-stock-12dof"),
            expected_conditions=("skeleton-assisted", "from-scratch"),
            run_id="paired-1",
        )

        self.assertEqual(len(paired["cells"]), 4)
        failed = [cell for cell in paired["cells"] if not cell["final_validation_passed"]]
        self.assertEqual([cell["cell_id"] for cell in failed], ["robotstudio_so101::from-scratch"])
        self.assertTrue(paired["summary"]["all_expected_cells_reported"])
        self.assertFalse(paired["summary"]["all_cells_final_validation_passed"])
        self.assertFalse(
            paired["summary"]["all_cells_final_capability_validation_passed"]
        )
        comparison = paired["paired_comparisons"][0]
        self.assertFalse(comparison["both_cells_final_validation_passed"])
        self.assertFalse(
            comparison["both_cells_final_capability_validation_passed"]
        )
        self.assertFalse(comparison["conditions"]["from-scratch"]["final_validation_passed"])

    def test_missing_cell_is_explicit_and_json_round_trips(self) -> None:
        cells = [
            _terminal_report("robotstudio_so101", "skeleton-assisted", True),
        ]
        paired = build_paired_report(
            cells,
            expected_robots=("robotstudio_so101", "unitree-go2-stock-12dof"),
            expected_conditions=("skeleton-assisted", "from-scratch"),
        )

        self.assertFalse(paired["summary"]["all_expected_cells_reported"])
        self.assertIn(
            "robotstudio_so101::from-scratch", paired["summary"]["missing_cell_ids"]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            write_json(path, paired)
            self.assertEqual(read_json(path), paired)

    def test_cell_identity_is_required(self) -> None:
        with self.assertRaises(ReportingError):
            build_cell_report({"pipeline_completed": True})


if __name__ == "__main__":
    unittest.main()
