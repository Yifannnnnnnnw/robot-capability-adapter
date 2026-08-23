from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from autoadapter2.b2.task_harness import evaluate_b2_task_harness


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TASK_SUITE_PATH = (
    REPOSITORY_ROOT
    / "experiment"
    / "experiment1b_use"
    / "config"
    / "task_suite"
    / "task_suite.json"
)
INSTANCE_ID = "go2-go2-t16"


def _package(tmp_path: Path) -> SimpleNamespace:
    """Return matching identity with deliberately untrusted live task records."""

    private = tmp_path / "tasks" / "private"
    private.mkdir(parents=True)
    for name in ("instances.json", "bindings.json", "guards.json"):
        (private / name).write_text("not sealed JSON", encoding="utf-8")
    return SimpleNamespace(
        robot_configuration_id="unitree-go2-stock-12dof",
        package_version="1.0.0",
        snapshot_id="unitree-go2-source-protocols-2026-08-18-v4",
        private_dir=private,
        tasks=(
            {
                "task_id": "GO2-T16",
                "scoring": [
                    {
                        "clause_id": "live-package-false-success",
                        "comparator": ">=",
                        "threshold": -1.0,
                    }
                ],
            },
        ),
    )


def _session_result(
    *,
    end_position: list[float] | None = None,
    direct_state_write: bool = False,
    video_complete: bool = True,
    controller_status: str = "CONTROLLER_FINISHED",
) -> dict[str, Any]:
    target = end_position or [1.8, 0.0, 0.37]
    samples = [
        {
            "time": 0.0,
            "body_positions": {"base_link": [0.0, 0.0, 0.37]},
            "body_quaternions": {"base_link": [1.0, 0.0, 0.0, 0.0]},
        }
    ]
    for second in range(1, 7):
        samples.append(
            {
                "time": float(second),
                "body_positions": {"base_link": list(target)},
                "body_quaternions": {"base_link": [1.0, 0.0, 0.0, 0.0]},
            }
        )
    return {
        "controller": {"status": controller_status, "completed": False},
        "worker": {
            "worker_completed": True,
            "method_invoked": True,
            "canonical_model_data": True,
            "candidate_exception": None,
            "physical_evidence": {
                "step_count": 600,
                "ctrl_observed_before_step": True,
                "ctrl_changed_from_reset": True,
                "direct_state_write_detected": direct_state_write,
                "contact_monitoring_complete": True,
                "minimum_contact_distance_m": None,
                "contact_pair_min_distances": [],
                "samples": samples,
            },
            "video": {
                "requested": True,
                "complete": video_complete,
                "path": "/evidence/trial.mp4",
            },
        },
    }


def _evaluate(
    tmp_path: Path,
    *,
    session_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return evaluate_b2_task_harness(
        package=_package(tmp_path),
        task_suite_path=TASK_SUITE_PATH,
        instance_id=INSTANCE_ID,
        replicate_id="R2",
        session_result=session_result or _session_result(),
    )


def test_sealed_original_clauses_pass_even_when_controller_reports_failure(
    tmp_path: Path,
) -> None:
    report = _evaluate(
        tmp_path,
        session_result=_session_result(controller_status="MODEL_ERROR"),
    )

    assert [item["clause_id"] for item in report["task_clause_results"]] == [
        "table_step_off",
        "end_table_hold",
    ]
    assert all(item["task_metric_passed"] for item in report["task_clause_results"])
    assert report["replicate_id"] == "R2"
    assert report["task_metric_passed"] is True
    assert report["physical_integrity_passed"] is True
    assert report["video_complete"] is True
    assert report["physical_harness_verdict"] == "PASS"


def test_mutating_live_package_definitions_cannot_change_sealed_verdict(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    package.tasks[0]["scoring"][0]["threshold"] = 1_000_000.0
    (package.private_dir / "instances.json").write_text(
        json.dumps({"instances": []}), encoding="utf-8"
    )

    report = evaluate_b2_task_harness(
        package=package,
        task_suite_path=TASK_SUITE_PATH,
        instance_id=INSTANCE_ID,
        replicate_id="R1",
        session_result=_session_result(),
    )

    assert [item["clause_id"] for item in report["task_clause_results"]] == [
        "table_step_off",
        "end_table_hold",
    ]
    assert report["task_metric_passed"] is True
    assert report["physical_harness_verdict"] == "PASS"


def test_one_failed_sealed_clause_fails_only_the_task_metric(tmp_path: Path) -> None:
    report = _evaluate(
        tmp_path,
        session_result=_session_result(end_position=[1.8, 0.6, 0.37]),
    )

    assert [
        item["task_metric_passed"] for item in report["task_clause_results"]
    ] == [True, False]
    assert report["task_metric_passed"] is False
    assert report["physical_integrity_passed"] is True
    assert report["video_complete"] is True
    assert report["physical_harness_verdict"] == "FAIL"


def test_sealed_guard_failure_is_independent_of_task_metric(tmp_path: Path) -> None:
    report = _evaluate(
        tmp_path,
        session_result=_session_result(direct_state_write=True),
    )

    assert report["task_metric_passed"] is True
    assert report["guard_outcomes"]["go2_no_state_write"] is False
    assert report["physical_integrity_passed"] is False
    assert report["video_complete"] is True
    assert report["physical_harness_verdict"] == "FAIL"


def test_incomplete_video_fails_the_sealed_evidence_requirements(
    tmp_path: Path,
) -> None:
    report = _evaluate(
        tmp_path,
        session_result=_session_result(video_complete=False),
    )

    assert report["task_metric_passed"] is True
    assert report["physical_integrity_passed"] is False
    assert report["guard_outcomes"]["go2_video"] is False
    assert report["video_complete"] is False
    assert report["physical_harness_verdict"] == "FAIL"
