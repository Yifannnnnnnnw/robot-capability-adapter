from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from autoadapter2.b2.task_harness import evaluate_b2_task_harness


def _package(tmp_path: Path) -> SimpleNamespace:
    private = tmp_path / "tasks" / "private"
    private.mkdir(parents=True)
    task = {
        "task_id": "two-clause-task",
        "scoring": [
            {
                "clause_id": "object_at_goal",
                "metric": "object_goal_distance",
                "unit": "m",
                "comparator": "<=",
                "threshold": 0.05,
                "temporal": {"kind": "terminal_state"},
                "aggregation": {"kind": "single_trial"},
            },
            {
                "clause_id": "tool_at_goal",
                "metric": "tool_goal_distance",
                "unit": "m",
                "comparator": "<=",
                "threshold": 0.05,
                "temporal": {"kind": "terminal_state_after_release"},
                "aggregation": {"kind": "single_trial"},
            },
        ],
    }
    documents = {
        "instances.json": {
            "instances": [
                {
                    "instance_id": "private-two-clause-task",
                    "task_id": "two-clause-task",
                    "public_arguments": {
                        "request": {
                            "task_parameters": {
                                "object_goal": [1.0, 2.0, 3.0],
                                "tool_goal": [4.0, 5.0, 6.0],
                            }
                        }
                    },
                    "clause_bindings": {
                        "object_at_goal": "object-binding",
                        "tool_at_goal": "tool-binding",
                    },
                    "guard_ids": ["physics", "no-state-write", "canonical"],
                    "repetitions": 1,
                }
            ]
        },
        "bindings.json": {
            "bindings": [
                {
                    "binding_id": "object-binding",
                    "kind": "final_body_position_error",
                    "parameters": {
                        "body_name": "object",
                        "target_argument": "request.task_parameters.object_goal",
                    },
                },
                {
                    "binding_id": "tool-binding",
                    "kind": "final_site_position_error",
                    "parameters": {
                        "site_name": "tool",
                        "target_argument": "request.task_parameters.tool_goal",
                    },
                },
            ]
        },
        "guards.json": {
            "guards": [
                {
                    "guard_id": "physics",
                    "kind": "actuator_and_physics_step_required",
                },
                {"guard_id": "no-state-write", "kind": "no_direct_state_write"},
                {"guard_id": "canonical", "kind": "canonical_model_data"},
            ]
        },
    }
    for name, document in documents.items():
        (private / name).write_text(json.dumps(document), encoding="utf-8")
    return SimpleNamespace(
        private_dir=private,
        tasks=(task,),
        robot_configuration_id="test-robot",
    )


def _session_result(
    *,
    object_position: list[float] | None = None,
    direct_state_write: bool = False,
    video_complete: bool = True,
    controller_status: str = "CONTROLLER_FINISHED",
) -> dict[str, Any]:
    return {
        "controller": {"status": controller_status, "completed": False},
        "worker": {
            "worker_completed": True,
            "method_invoked": True,
            "canonical_model_data": True,
            "candidate_exception": None,
            "physical_evidence": {
                "step_count": 10,
                "ctrl_observed_before_step": True,
                "ctrl_changed_from_reset": True,
                "direct_state_write_detected": direct_state_write,
                "contact_monitoring_complete": True,
                "minimum_contact_distance_m": None,
                "contact_pair_min_distances": [],
                "samples": [
                    {
                        "time": 0.0,
                        "body_positions": {"object": [0.0, 0.0, 0.0]},
                        "site_positions": {"tool": [0.0, 0.0, 0.0]},
                    },
                    {
                        "time": 1.0,
                        "body_positions": {
                            "object": object_position or [1.0, 2.0, 3.0]
                        },
                        "site_positions": {"tool": [4.0, 5.0, 6.0]},
                    },
                ],
            },
            "video": {
                "requested": True,
                "complete": video_complete,
                "path": "/evidence/trial.mp4",
            },
        },
    }


def test_all_original_clauses_pass_even_when_controller_reports_failure(
    tmp_path: Path,
) -> None:
    report = evaluate_b2_task_harness(
        package=_package(tmp_path),
        instance_id="private-two-clause-task",
        session_result=_session_result(controller_status="MODEL_ERROR"),
    )

    assert [item["clause_id"] for item in report["task_clause_results"]] == [
        "object_at_goal",
        "tool_at_goal",
    ]
    assert all(item["task_metric_passed"] for item in report["task_clause_results"])
    assert report["task_metric_passed"] is True
    assert report["physical_integrity_passed"] is True
    assert report["video_complete"] is True
    assert report["physical_harness_verdict"] == "PASS"


def test_one_failed_clause_fails_only_the_task_metric(tmp_path: Path) -> None:
    report = evaluate_b2_task_harness(
        package=_package(tmp_path),
        instance_id="private-two-clause-task",
        session_result=_session_result(object_position=[1.2, 2.0, 3.0]),
    )

    assert [
        item["task_metric_passed"] for item in report["task_clause_results"]
    ] == [False, True]
    assert report["task_metric_passed"] is False
    assert report["physical_integrity_passed"] is True
    assert report["video_complete"] is True
    assert report["physical_harness_verdict"] == "FAIL"


def test_original_guard_failure_is_independent_of_task_metric(tmp_path: Path) -> None:
    report = evaluate_b2_task_harness(
        package=_package(tmp_path),
        instance_id="private-two-clause-task",
        session_result=_session_result(direct_state_write=True),
    )

    assert report["task_metric_passed"] is True
    assert report["guard_outcomes"]["no-state-write"] is False
    assert report["physical_integrity_passed"] is False
    assert report["video_complete"] is True
    assert report["physical_harness_verdict"] == "FAIL"


def test_incomplete_video_is_independent_of_physical_task_success(tmp_path: Path) -> None:
    report = evaluate_b2_task_harness(
        package=_package(tmp_path),
        instance_id="private-two-clause-task",
        session_result=_session_result(video_complete=False),
    )

    assert report["task_metric_passed"] is True
    assert report["physical_integrity_passed"] is True
    assert report["video_complete"] is False
    assert report["physical_harness_verdict"] == "FAIL"
