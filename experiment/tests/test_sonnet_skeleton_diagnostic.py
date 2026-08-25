from __future__ import annotations

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

    with pytest.raises(diagnostic.DiagnosticRunError, match="not one of the remaining"):
        diagnostic._single_robot_config(diagnostic.DEFAULT_CONFIG, "robotstudio_so101")


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
