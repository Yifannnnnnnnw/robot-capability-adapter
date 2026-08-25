from __future__ import annotations

import json
from pathlib import Path

from autoadapter.scripts.check_deepseek_so101_canary import (
    CONDITION,
    EXPERIMENT_ID,
    MODEL_ID,
    ROBOT_ID,
    check_evidence,
    check_preflight,
)


ROOT = Path(__file__).resolve().parents[2]
AUTOADAPTER_ROOT = ROOT / "autoadapter"
CONFIG = (
    AUTOADAPTER_ROOT
    / "configs"
    / "experiments"
    / "deepseek-so101-1.0.4-canary.json"
)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _model_call(stage: str) -> dict:
    return {
        "stage": stage,
        "status": "success",
        "requested_model": MODEL_ID,
        "returned_model": MODEL_ID,
    }


def test_deepseek_canary_preflight_is_zero_model_and_pins_so101_104() -> None:
    result = check_preflight(root=AUTOADAPTER_ROOT, config_path=CONFIG)

    assert result["passed"] is True
    assert result["formal"] is False
    assert result["indexed_package_version"] == "1.0.4"
    assert result["generation_condition"] == "skeleton-assisted"
    assert result["model_client_constructed"] is False
    assert result["model_requests"] == 0
    assert result["empty_experience"] is True
    assert result["evolution_attempt_limit"] == 1


def test_canary_evidence_gate_accepts_partial_capability_closure(tmp_path: Path) -> None:
    run_dir = tmp_path / "source-run"
    workspace = run_dir / "cells" / ROBOT_ID / CONDITION
    video = workspace / "task-demo" / "videos" / "task-a.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"diagnostic-video")

    stages = ["study", "tgcd", "ivc", "generate", "task_demo_recap"]
    _write(
        run_dir / "experiment_report.json",
        {
            "experiment_id": EXPERIMENT_ID,
            "run_id": "diagnostic-source",
            "pipeline_completed": True,
            "producer_model": {"provider": "deepseek", "model": MODEL_ID},
            "evolution_model": {"provider": "deepseek", "model": MODEL_ID},
            "configuration": {
                "formal": False,
                "robots": [ROBOT_ID],
                "generation_conditions": [CONDITION],
            },
            "reference_calibration_skipped": True,
            "reference_calibration_passed": False,
            "references": {
                f"{ROBOT_ID}::{CONDITION}": {
                    "passed": False,
                    "skipped": True,
                }
            },
            "cells": [
                {
                    "robot_package_version": "1.0.4",
                    "dynamic_model_called": True,
                }
            ],
            "stage_evidence": [
                {"stage": stage, "model_calls": [_model_call(stage)]}
                for stage in stages
            ],
        },
    )

    design_capabilities = [
        {"capability_id": "C1", "method_name": "move_tcp"},
        {"capability_id": "C2", "method_name": "set_gripper"},
        {"capability_id": "C3", "method_name": "hold_pose"},
    ]
    _write(
        workspace / "design" / "capability_design.json",
        {"capabilities": design_capabilities},
    )
    cases = [
        {
            "case_id": f"{capability_id}-{role}",
            "capability_id": capability_id,
            "case_role": role,
        }
        for capability_id in ("C1", "C2", "C3")
        for role in ("nominal", "calibrated_boundary")
    ]
    _write(
        workspace / "private" / "capability_validation_suite.json",
        {"cases": cases},
    )
    _write(
        workspace / "private" / "ivc_artifact_trace.json",
        {"model_calls": [_model_call("ivc")]},
    )
    validation_trials = [
        {
            "case_id": case["case_id"],
            "trial_passed": case["capability_id"] == "C1",
        }
        for case in cases
    ]
    proposal = {
        "observation": "One bounded motion capability closed both cases.",
        "lesson": "The bounded request was easier to calibrate.",
        "recommendation": "Retain grounded request bounds in a later run.",
        "scope": "SO-101 capability design only.",
        "public_evidence": ["C1 nominal and calibrated-boundary cases passed."],
    }
    evolution = {
        "evolution_attempted": True,
        "evolution_completed": True,
        "proposal_created": True,
        "proposal": proposal,
        "model_calls": [_model_call("evolution")],
    }
    _write(
        workspace / "cell_report.json",
        {
            "robot_package_version": "1.0.4",
            "attempts": [{"driver_frozen": True}],
            "passed_capability_whitelist": ["C1"],
            "capability_validation": {"trials": validation_trials},
            "outcomes": {"Evolution": evolution},
        },
    )
    _write(
        workspace / "task-demo" / "capability_design.json",
        {"capabilities": [design_capabilities[0]]},
    )
    trial_video = {
        "requested": True,
        "complete": True,
        "path": str(video),
    }
    _write(
        workspace / "task-demo" / "task_demo_report.json",
        {
            "pipeline_completed": True,
            "physical_validation_executed": True,
            "validation_passed": False,
            "video_complete": True,
            "high_level_controller": {
                "kind": "task_demo_recap",
                "capability_call_count": 1,
            },
            "trials": [
                {
                    "physical_execution_passed": True,
                    "video": trial_video,
                    "harness": {"physical_harness_verdict": "FAIL"},
                    "controller": {
                        "planning_turns": 2,
                        "capability_calls": 1,
                        "trace": [
                            {
                                "action_kind": "capability",
                                "capability_name": "move_tcp",
                                "capability_execution_outcome": "EXECUTED",
                            }
                        ],
                    },
                }
            ],
        },
    )
    _write(
        run_dir / "experience_review_queue.json",
        {
            "dispositions_complete": False,
            "records": [{"disposition": None, "reason": None, "evolution": evolution}],
        },
    )

    result = check_evidence(run_dir=run_dir, config_path=CONFIG)

    assert result["passed"] is True
    assert result["reference_calibration_skipped"] is True
    assert result["nominal_boundary_passed_capabilities"] == ["C1"]
    assert result["recap_real_capability_calls"] == 1
    assert result["task_demo_verdict"] == "FAIL"
    assert result["evolution_proposal"] == proposal
    assert result["human_disposition_required"] is True
    assert result["later_run_started"] is False
