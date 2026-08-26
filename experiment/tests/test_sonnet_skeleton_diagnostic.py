from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from experiment.experiment3.diagnostics import run_sonnet_skeleton_diagnostic as diagnostic


CHAPTER3_EXP2_CONFIG = (
    diagnostic.REPOSITORY_ROOT
    / "autoadapter"
    / "configs"
    / "experiments"
    / "sonnet-chapter3-exp2-r01-nine-one-repair-diagnostic.json"
)


def test_sonnet_diagnostic_is_single_robot_nonformal_and_uses_exp3_pin() -> None:
    config = diagnostic._single_robot_config(
        diagnostic.DEFAULT_CONFIG,
        "unitree-go2-stock-12dof",
    )

    assert config.robots == ("unitree-go2-stock-12dof",)
    assert config.generation_conditions == ("skeleton-assisted",)
    assert config.formal is False
    assert config.evolution_enabled is False
    assert config.max_driver_attempts_per_condition == 3
    assert config.model_manifest is not None
    assert config.model_manifest["model_id"] == "eu.anthropic.claude-sonnet-4-6"

    manifest = json.loads(diagnostic.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["runtime"]["producer_transport"]["request_timeout_s"] == 300

    with pytest.raises(diagnostic.DiagnosticRunError, match="not one of the remaining"):
        diagnostic._single_robot_config(diagnostic.DEFAULT_CONFIG, "robotstudio_so101")


def test_chapter3_exp2_r01_diagnostic_allows_one_repair_only() -> None:
    config = diagnostic._single_robot_config(CHAPTER3_EXP2_CONFIG, "franka_panda")

    assert config.robots == ("franka_panda",)
    assert config.generation_conditions == ("skeleton-assisted",)
    assert config.max_driver_attempts_per_condition == 2
    assert config.formal is False
    assert config.evolution_enabled is False

    with pytest.raises(diagnostic.DiagnosticRunError, match="not one of the remaining"):
        diagnostic._single_robot_config(CHAPTER3_EXP2_CONFIG, "unitree-go2-stock-12dof")


def test_sonnet_diagnostic_usage_sums_all_physical_calls() -> None:
    client = SimpleNamespace(
        calls=[
            {
                "requested_model": "eu.anthropic.claude-sonnet-4-6",
                "returned_model": "eu.anthropic.claude-sonnet-4-6",
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
            },
            {
                "requested_model": "eu.anthropic.claude-sonnet-4-6",
                "returned_model": "eu.anthropic.claude-sonnet-4-6",
                "input_tokens": 20,
                "output_tokens": 3,
                "total_tokens": 23,
            },
        ]
    )

    usage = diagnostic._client_usage(client)

    assert usage["model_call_count"] == 2
    assert usage["tokens"]["input_tokens"] == 30
    assert usage["tokens"]["output_tokens"] == 5
    assert usage["tokens"]["total_tokens"] == 35
    assert usage["returned_models"] == ["eu.anthropic.claude-sonnet-4-6"]


def test_sonnet_diagnostic_summary_reports_nested_failure_stage(tmp_path) -> None:
    report = {
        "experiment_id": "diagnostic",
        "run_id": "run-1",
        "reference_calibration_skipped": True,
        "pipeline_completed": False,
        "cells": [
            {
                "robot_configuration_id": "unitree-go2-stock-12dof",
                "failure": {"stage": "tgcd", "type": "ModelInvocationError"},
            }
        ],
    }

    summary = diagnostic._summary(
        report,
        client=SimpleNamespace(calls=[]),
        output_dir=tmp_path,
    )

    assert summary["failed_stage"] == "tgcd"


def _write_json(path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _reusable_study_cell(tmp_path, *, physics_steps: int = 50):
    config = diagnostic._single_robot_config(CHAPTER3_EXP2_CONFIG, "franka_panda")
    output = tmp_path / "source-output"
    cell = output / "cells" / "franka_panda" / "skeleton-assisted"
    study = {
        "condition": "skeleton-assisted",
        "findings": ["The public scene advances."],
        "implementation_plan": ["Use bounded feedback."],
        "probe_requests": [{"probe_id": "physics", "script": "mujoco.mj_step(model, data)"}],
    }
    _write_json(cell / "files" / "study.json", {**study, "condition": "study"})
    _write_json(
        cell / "design" / "workspace" / "tgcd_inputs.json",
        {"completed_public_study": study},
    )
    _write_json(
        cell / "study_evidence.json",
        {
            "probe_results": [
                {
                    "successful": True,
                    "physics_steps": physics_steps,
                    "physics_steps_total": physics_steps,
                }
            ]
        },
    )
    _write_json(
        cell / "cell_report.json",
        {
            "robot_configuration_id": "franka_panda",
            "robot_package_version": "1.0.0",
            "task_snapshot_id": "snapshot-1",
            "condition": "skeleton-assisted",
            "failure": {"stage": "tgcd"},
            "outcomes": {"STUDY": {"completed": True}},
        },
    )
    source_calls = [
        {
            "requested_model": "eu.anthropic.claude-sonnet-4-6",
            "returned_model": "eu.anthropic.claude-sonnet-4-6",
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "elapsed_s": 2.0,
        }
    ]
    _write_json(
        output / "experiment_report.json",
        {
            "run_id": "run-1",
            "configuration": {
                "model": config.model_manifest,
                "experience": {"input": []},
                "max_driver_attempts_per_condition": 2,
            },
            "stage_evidence": [{"stage": "study", "model_calls": source_calls}],
        },
    )
    return config, cell


def test_sonnet_diagnostic_reuses_real_study_with_explicit_provenance(tmp_path) -> None:
    config, cell = _reusable_study_cell(tmp_path)

    study, provenance = diagnostic._load_reused_study(
        cell,
        robot="franka_panda",
        condition="skeleton-assisted",
        run_id="run-1",
        config=config,
    )

    assert study.condition == "skeleton-assisted"
    assert study.probe_results[0]["physics_steps"] == 50
    assert provenance["reused_stage"] == "STUDY"
    assert provenance["restarted_stage"] == "TGCD"
    assert provenance["formal"] is False
    assert provenance["source_resource_summary"]["call_count"] == 1
    assert provenance["source_resource_summary"]["estimated_cost"]["amount"] > 0
    assert "not a single budget-compliant formal cell" in provenance["claim_boundary"]


def test_sonnet_diagnostic_rejects_reused_study_without_physics(tmp_path) -> None:
    config, cell = _reusable_study_cell(tmp_path, physics_steps=0)

    with pytest.raises(diagnostic.DiagnosticRunError, match="physics probe"):
        diagnostic._load_reused_study(
            cell,
            robot="franka_panda",
            condition="skeleton-assisted",
            run_id="run-1",
            config=config,
        )
