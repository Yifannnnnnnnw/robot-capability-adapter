from __future__ import annotations

import copy
from pathlib import Path

import pytest

from experiment.experiment3 import runner


def _fixed_skip(stage: str) -> dict[str, object]:
    return {
        "stage": stage.casefold(),
        "attempted": False,
        "completed": False,
        "skipped": True,
        "model_call_count": 0,
        "fixed_input_provenance": {"mode": "fixed_inputs_from"},
    }


def test_checked_in_v2_manifest_expands_exact_33_without_exposure_groups() -> None:
    manifest = runner.load_manifest()
    cells = runner.expand_cells(manifest)

    assert len(cells) == 33
    assert len({cell["cell_id"] for cell in cells}) == 33
    assert all("reference_exposure_group" not in cell for cell in cells)
    assert "reporting_groups" not in manifest
    assert {cell["generation_condition"] for cell in cells} == {
        "skeleton-assisted"
    }


def test_v2_manifest_pins_fixed_route_budgets_and_zero_call_authoring() -> None:
    manifest = runner.validate_design_manifest(runner.load_manifest())

    assert manifest["workflow"]["order"][:2] == ["STUDY", "GENERATE"]
    assert "TGCD" not in manifest["workflow"]["order"]
    assert "IVC" not in manifest["workflow"]["order"]
    assert manifest["workflow"]["aggregate_tool_call_limit"] is None
    assert manifest["workflow"]["maximum_frozen_driver_attempts"] == 3
    assert manifest["runtime"]["resources"]["phase_turn_budgets"] == {
        "study": 16,
        "generate_skeleton": 22,
        "repair_skeleton": 22,
    }
    assert manifest["authoring_stages"] == {
        "tgcd": {"mode": "skipped-fixed-input", "model_call_budget": 0},
        "ivc": {"mode": "skipped-fixed-input", "model_call_budget": 0},
    }
    assert manifest["fixed_input_set"] == runner.FIXED_INPUT_INDEX


def test_formal_route_is_approved_but_cannot_be_relocked_or_bypass_manifest() -> None:
    manifest = runner.load_manifest()
    assert manifest["status"] == "formal-authorised"
    assert manifest["formal_dispatch_authorised"] is True

    relocked = copy.deepcopy(manifest)
    relocked["formal_dispatch_authorised"] = False
    with pytest.raises(runner.Experiment3RunnerError, match="explicitly authorised"):
        runner.validate_design_manifest(relocked)


def test_design_rejects_old_reporting_groups_or_dynamic_authoring() -> None:
    grouped = copy.deepcopy(runner.load_manifest())
    grouped["reporting_groups"] = {"transfer_cell_count": 27}
    with pytest.raises(runner.Experiment3RunnerError, match="reporting groups"):
        runner.validate_design_manifest(grouped)

    dynamic = copy.deepcopy(runner.load_manifest())
    dynamic["authoring_stages"]["ivc"] = {
        "mode": "dynamic",
        "model_call_budget": 6,
    }
    with pytest.raises(runner.Experiment3RunnerError, match="zero-call"):
        runner.validate_design_manifest(dynamic)


def test_formal_cell_config_is_skeleton_empty_experience_and_evolution_free() -> None:
    manifest = runner.load_manifest()
    preflight = runner.validate_executable_preflight(
        manifest,
        mainline_root=Path(__file__).resolve().parents[2] / "autoadapter",
    )

    config = runner._cell_config(preflight, "robotstudio_so101")

    assert config["formal"] is True
    assert config["robots"] == ["robotstudio_so101"]
    assert config["generation_conditions"] == ["skeleton-assisted"]
    assert config["experience"]["input"] == []
    assert config["max_driver_attempts_per_condition"] == 3
    assert config["evolution"] == {"enabled": False}


def test_task_demo_trigger_uses_partial_nominal_boundary_whitelist() -> None:
    partial_pass = {
        "final_capability_validation_passed": False,
        "passed_capability_whitelist": ["capability-a"],
        "task_demo_executed": True,
        "task_demo_passed": False,
    }

    assert runner._task_demo_terminal(partial_pass) == {
        "status": "executed",
        "passed": False,
        "capability_whitelist": ["capability-a"],
    }

    without_pair = {
        "final_capability_validation_passed": False,
        "passed_capability_whitelist": [],
        "task_demo_executed": False,
        "task_demo": {
            "skipped": True,
            "skip_reason": "no capability passed both required case roles",
        },
    }
    assert runner._task_demo_terminal(without_pair)["status"] == "not-run"


def test_formal_evidence_requires_fixed_zero_call_stages_but_allows_study_error() -> None:
    robot = runner.ROBOT_CONFIGURATIONS[0]
    cell = {
        "cell_id": f"{robot}::{runner.CONDITION}",
        "robot_configuration_id": robot,
        "condition": runner.CONDITION,
        "outcomes": {
            "STUDY": {
                "attempted": True,
                "completed": False,
                "model_call_count": 2,
                "error": {"type": "StudyError", "message": "invalid study"},
            },
            "TGCD": _fixed_skip("TGCD"),
            "IVC": _fixed_skip("IVC"),
        },
        "upstream_artifact_mode": "fixed-per-robot",
        "fixed_input_provenance": {"mode": "fixed_inputs_from"},
        "frozen_driver_attempt_count": 0,
        "capability_validation_executed": False,
        "passed_capability_whitelist": [],
    }

    runner._assert_formal_cell_evidence(
        {"run_id": "declared-run"},
        cell,
        robot=robot,
        run_id="declared-run",
    )

    cell["outcomes"]["IVC"] = {"completed": True, "model_call_count": 1}
    with pytest.raises(runner.Experiment3RunnerError, match="zero-call"):
        runner._assert_formal_cell_evidence(
            {"run_id": "declared-run"},
            cell,
            robot=robot,
            run_id="declared-run",
        )


def test_frozen_driver_attempts_are_the_only_formal_attempts() -> None:
    valid = {
        "cells": [
            {
                "attempt_count": 2,
                "frozen_driver_attempt_count": 2,
            }
        ]
    }
    assert runner._assert_attempt_ceiling(valid)["frozen_driver_attempt_count"] == 2

    mismatch = copy.deepcopy(valid)
    mismatch["cells"][0]["attempt_count"] = 3
    with pytest.raises(runner.Experiment3RunnerError, match="only frozen Driver"):
        runner._assert_attempt_ceiling(mismatch)
