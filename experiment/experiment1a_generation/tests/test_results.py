from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EXPERIMENT_ROOT.parents[1]
AUTOADAPTER_SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter" / "src"
for path in (REPOSITORY_ROOT, AUTOADAPTER_SOURCE_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from experiment.experiment1a_generation.runtime.results import (  # noqa: E402
    ResultsError,
    build_results,
    discover_cell_records,
    main as aggregate_main,
    select_cell_records,
)


def minimal_record(
    unit_id: str,
    *,
    backbone_id: str,
    revision: str,
    timeout_s: float = 120.0,
) -> dict:
    parts = unit_id.split("::")
    return {
        "schema_version": 2,
        "experiment_id": "experiment1-b1-core-r3",
        "track": "B1",
        "run_id": f"run::{unit_id}",
        "identity": {
            "authority_revision": revision,
            "unit_id": unit_id,
            "cell_id": unit_id,
            "robot_configuration_id": parts[1],
            "backbone_id": backbone_id,
            "replicate_id": parts[3],
            "generation_condition": parts[4],
            "git_commit": "fixture",
        },
        "model": {"exact_model_id": f"model-{backbone_id}", "timeout_s": timeout_s},
        "provider_calls": [],
        "actions": [],
        "attempts": [
            {
                "attempt_index": 0,
                "driver_frozen": False,
                "validation_report": None,
                "validation_verdict": None,
            }
        ],
        "derived": {
            "iteration_count": 0,
            "execution_error_count": 0,
            "provider_error_count": 0,
            "frozen_driver_attempt_count": 0,
            "stacked_action_counts": {
                "read_or_plan": 0,
                "execute_clean": 0,
                "execute_error": 0,
            },
            "token_totals": {"total_tokens": 0},
            "total_model_cost": 0.0,
        },
        "terminal_verdict": {
            "stop_reason": "study_error",
            "terminal_failure_class": "study_error",
            "frozen_driver_attempt_count": 0,
        },
        "timing": {
            "utc_finished_at": "2026-08-22T00:00:01Z",
            "total_wall_time_s": 1.0,
        },
    }


def candidate(record: dict, path: str) -> dict:
    return {
        "record": record,
        "cell_record_path": path,
        "scheduler_path": "/formal/scheduler_record.json",
    }


class Experiment1ResultTests(unittest.TestCase):
    def test_require_complete_cli_rejects_missing_formal_units(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        scheduler_path = root / "scheduler_record.json"
        scheduler_path.write_text(
            json.dumps(
                {
                    "utc_finished_at": "2026-08-23T00:00:00Z",
                    "exit_summary": {
                        "process_count": 0,
                        "failed_process_count": 0,
                    },
                    "units": [],
                }
            ),
            encoding="utf-8",
        )
        output = root / "aggregate"
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = aggregate_main(
                [
                    "--manifest",
                    str(EXPERIMENT_ROOT / "manifest.json"),
                    "--formal-scheduler",
                    str(scheduler_path),
                    "--output",
                    str(output),
                    "--require-complete",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("84 missing units", stdout.getvalue())
        self.assertFalse(output.exists())

    def test_selection_excludes_pre_020_records_and_rejects_eligible_duplicates(self) -> None:
        m2_unit = "b1::unitree-go2-stock-12dof::M2::r01::from-scratch"
        m1_unit = "b1::unitree-go2-stock-12dof::M1::r01::from-scratch"
        so_unit = "b1::robotstudio_so101::M4::r01::from-scratch"
        old_m2 = candidate(
            minimal_record(m2_unit, backbone_id="M2", revision="0.1.17"),
            "/formal/old-m2.json",
        )
        new_m2 = candidate(
            minimal_record(
                m2_unit,
                backbone_id="M2",
                revision="0.2.0",
                timeout_s=120,
            ),
            "/formal/new-m2.json",
        )
        m1 = candidate(
            minimal_record(m1_unit, backbone_id="M1", revision="0.2.0"),
            "/formal/m1.json",
        )
        old_so = candidate(
            minimal_record(so_unit, backbone_id="M4", revision="0.1.17"),
            "/formal/old-so.json",
        )
        new_so = candidate(
            minimal_record(so_unit, backbone_id="M4", revision="0.2.0"),
            "/formal/new-so.json",
        )

        selected, audit = select_cell_records(
            [old_m2, new_m2, m1, old_so, new_so],
            expected_unit_ids=[m2_unit, m1_unit, so_unit],
        )

        self.assertEqual(
            [row["record"]["identity"]["unit_id"] for row in selected],
            [m2_unit, m1_unit, so_unit],
        )
        self.assertEqual(audit["missing_unit_count"], 0)
        self.assertEqual(
            {row["reason"] for row in audit["excluded"]},
            {"superseded_pre_020"},
        )
        with self.assertRaisesRegex(ResultsError, "ambiguous eligible formal records"):
            select_cell_records(
                [new_m2, {**new_m2, "cell_record_path": "/formal/duplicate.json"}],
                expected_unit_ids=[m2_unit],
            )

    def test_build_results_uses_final_frozen_driver_but_inventories_all_attempts(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        cell_path = root / "runs" / "cell" / "cell_record.json"
        cell_path.parent.mkdir(parents=True)
        cell_path.write_text("{}\n", encoding="utf-8")
        videos = []
        for name in ("attempt-0.mp4", "attempt-1.mp4"):
            path = cell_path.parent / name
            path.write_bytes(b"video")
            videos.append(path)
        unit_id = "b1::leap_hand::M3::r01::from-scratch"
        record = minimal_record(
            unit_id, backbone_id="M3", revision="0.1.15", timeout_s=120
        )
        record["provider_calls"] = [
            {
                "status": "succeeded",
                "tokens": {"input_tokens": 10, "output_tokens": 5},
            },
            {
                "status": "succeeded",
                "tokens": {"input_tokens": 12, "output_tokens": 6},
            },
        ]
        record["actions"] = [
            {
                "iteration": 1,
                "stage": "study",
                "action_type": "observe_or_plan",
                "plot_action_type": "read_or_plan",
                "input_tokens": 10,
                "output_tokens": 5,
                "elapsed_s": 1.0,
                "tool_names": [],
                "target_attempt": None,
                "artifact_event": None,
                "stage_transition": None,
            },
            {
                "iteration": 2,
                "stage": "gen_algo",
                "action_type": "execute_error",
                "plot_action_type": "execute_error",
                "input_tokens": 12,
                "output_tokens": 6,
                "elapsed_s": 2.0,
                "tool_names": ["write_file"],
                "target_attempt": 1,
                "artifact_event": None,
                "stage_transition": None,
            },
        ]
        attempts = []
        for attempt_index, passed in ((0, False), (1, True)):
            capability_id = "L1"
            case_id = f"L1-H{attempt_index + 1}"
            trial_id = f"{case_id}-r00"
            attempts.append(
                {
                    "attempt_index": attempt_index,
                    "driver_frozen": True,
                    "validation_verdict": passed,
                    "validation_case_counts": {
                        "passed": 1 if passed else 0,
                        "failed": 0 if passed else 1,
                        "incomplete": 0,
                        "total": 1,
                    },
                    "validation_report": {
                        "capability_results": [
                            {
                                "capability_id": capability_id,
                                "passed": passed,
                                "passed_case_count": 3 if passed else 0,
                                "case_count": 3,
                            }
                        ],
                        "trials": [
                            {
                                "capability_id": capability_id,
                                "case_id": case_id,
                                "trial_id": trial_id,
                                "trial_passed": passed,
                                "video": (
                                    None
                                    if attempt_index == 0
                                    else {
                                        "attempt": attempt_index,
                                        "case_id": case_id,
                                        "trial_id": trial_id,
                                        "path": str(videos[attempt_index]),
                                        "requested": True,
                                        "complete": True,
                                        "decodable": True,
                                        "error": None,
                                        "frame_count": 10,
                                        "width": 800,
                                        "height": 600,
                                        "duration": 1.0,
                                        "sim_start_s": 0.0,
                                        "sim_end_s": 0.5,
                                    }
                                ),
                            }
                        ],
                        "video_complete": attempt_index == 1,
                    },
                }
            )
        record["attempts"] = attempts
        record["derived"].update(
            {
                "iteration_count": 2,
                "execution_error_count": 1,
                "frozen_driver_attempt_count": 2,
                "stacked_action_counts": {
                    "read_or_plan": 1,
                    "execute_clean": 0,
                    "execute_error": 1,
                },
                "token_totals": {"total_tokens": 33},
                "total_model_cost": 0.25,
            }
        )
        record["terminal_verdict"] = {
            "stop_reason": "validation_passed",
            "terminal_failure_class": None,
            "frozen_driver_attempt_count": 2,
        }

        results, inventory = build_results(
            [candidate(record, str(cell_path))],
            selection_audit={"missing_unit_count": 0},
            model_display_names={"M3": "Haiku 4.5"},
            repository_root=root,
        )

        self.assertEqual(results["summary"]["evaluated_capability_count"], 1)
        self.assertEqual(results["summary"]["passed_capability_count"], 1)
        self.assertEqual(results["summary"]["evaluated_case_count"], 1)
        self.assertEqual(results["summary"]["passed_case_count"], 1)
        self.assertEqual(results["summary"]["video_trial_count"], 2)
        self.assertEqual(results["summary"]["uploadable_video_count"], 1)
        self.assertEqual(results["summary"]["missing_video_count"], 1)
        self.assertEqual(results["cell_rows"][0]["final_frozen_attempt_index"], 1)
        self.assertTrue(results["cell_rows"][0]["token_usage_complete"])
        self.assertEqual([row["stage"] for row in results["action_rows"]], ["study", "generate"])
        self.assertEqual([row["stage_start"] for row in results["action_rows"]], [True, True])
        self.assertIn("attempt-00/L1/L1-H1/L1-H1-r00.mp4", inventory[0]["artifact_relpath"])
        self.assertIn("attempt-01/L1/L1-H2/L1-H2-r00.mp4", inventory[1]["artifact_relpath"])
        self.assertFalse(inventory[0]["metadata_present"])
        self.assertTrue(inventory[1]["uploadable"])
        stale = json.loads(json.dumps(record))
        stale["derived"]["iteration_count"] = 3
        with self.assertRaisesRegex(ResultsError, "derived record mismatch"):
            build_results(
                [candidate(stale, str(cell_path))],
                selection_audit={"missing_unit_count": 0},
                model_display_names={"M3": "Haiku 4.5"},
                repository_root=root,
            )

    def test_discovery_accepts_only_completed_explicit_scheduler_units(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        cell = root / "cell"
        cell.mkdir()
        unit_id = "b1::robotstudio_so101::M4::r01::from-scratch"
        (cell / "cell_record.json").write_text(
            json.dumps(
                minimal_record(unit_id, backbone_id="M4", revision="0.1.14")
            ),
            encoding="utf-8",
        )
        scheduler_path = root / "scheduler_record.json"
        scheduler = {
            "utc_finished_at": "2026-08-22T00:00:02Z",
            "exit_summary": {"process_count": 2, "failed_process_count": 1},
            "units": [
                {
                    "unit_id": unit_id,
                    "exit_code": 0,
                    "cell_record_exists": True,
                    "cell_output": str(cell),
                },
                {
                    "unit_id": "b1::robotstudio_so101::M4::r01::skeleton-assisted",
                    "exit_code": 2,
                    "cell_record_exists": False,
                    "cell_output": str(root / "failed-cell"),
                },
            ],
        }
        scheduler_path.write_text(json.dumps(scheduler), encoding="utf-8")

        rows, excluded = discover_cell_records([scheduler_path])

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["reason"], "scheduler_unit_incomplete")
        self.assertEqual(rows[0]["record"]["identity"]["unit_id"], unit_id)
        scheduler["utc_finished_at"] = None
        scheduler_path.write_text(json.dumps(scheduler), encoding="utf-8")
        with self.assertRaisesRegex(ResultsError, "scheduler is incomplete"):
            discover_cell_records([scheduler_path])


if __name__ == "__main__":
    unittest.main()
