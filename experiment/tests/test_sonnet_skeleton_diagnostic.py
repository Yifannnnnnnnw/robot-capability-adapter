from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from experiment.experiment3.diagnostics import run_sonnet_skeleton_diagnostic as diagnostic


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
    config_path = (
        diagnostic.REPOSITORY_ROOT
        / "autoadapter"
        / "configs"
        / "experiments"
        / "sonnet-chapter3-exp2-r01-nine-one-repair-diagnostic.json"
    )

    config = diagnostic._single_robot_config(config_path, "franka_panda")

    assert config.robots == ("franka_panda",)
    assert config.generation_conditions == ("skeleton-assisted",)
    assert config.max_driver_attempts_per_condition == 2
    assert config.formal is False
    assert config.evolution_enabled is False

    with pytest.raises(diagnostic.DiagnosticRunError, match="not one of the remaining"):
        diagnostic._single_robot_config(config_path, "unitree-go2-stock-12dof")


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
