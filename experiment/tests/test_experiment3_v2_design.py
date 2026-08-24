from __future__ import annotations

import copy
from pathlib import Path

import pytest

from experiment.experiment3 import runner


def test_checked_in_v2_manifest_expands_exact_33_with_declared_groups() -> None:
    manifest = runner.load_manifest()
    cells = runner.expand_cells(manifest)

    assert len(cells) == 33
    assert len({cell["cell_id"] for cell in cells}) == 33
    assert sum(
        cell["reference_exposure_group"] == "reference-seen-control"
        for cell in cells
    ) == 6
    assert sum(cell["reference_exposure_group"] == "transfer" for cell in cells) == 27
    assert {cell["generation_condition"] for cell in cells} == {
        "skeleton-assisted"
    }


def test_v2_manifest_pins_file_workflow_budgets_and_no_aggregate_tool_cap() -> None:
    manifest = runner.validate_design_manifest(runner.load_manifest())

    assert manifest["workflow"]["order"][:3] == ["STUDY", "TGCD", "IVC"]
    assert manifest["workflow"]["aggregate_tool_call_limit"] is None
    assert manifest["workflow"]["maximum_frozen_driver_attempts"] == 3
    assert manifest["runtime"]["resources"]["phase_turn_budgets"] == {
        "study": 16,
        "tgcd": 6,
        "ivc": 6,
        "generate_skeleton": 22,
        "repair_skeleton": 22,
    }
    assert manifest["runtime"]["resources"]["recap"] == {
        "max_planning_turns_per_task": 16,
        "max_capability_calls_per_task": 12,
    }


def test_formal_dispatch_is_locked_during_preparation(tmp_path) -> None:
    manifest = runner.load_manifest()

    with pytest.raises(runner.Experiment3RunnerError, match="not authorised"):
        runner.run_formal(
            tmp_path,
            manifest=manifest,
            output_dir=tmp_path / "forbidden-formal",
        )


def test_design_rejects_quadruped_transfer_claim_or_dispatch_unlock() -> None:
    claim = copy.deepcopy(runner.load_manifest())
    claim["reporting_groups"]["quadruped_transfer_claim"] = True
    with pytest.raises(runner.Experiment3RunnerError, match="reporting groups"):
        runner.validate_design_manifest(claim)

    unlocked = copy.deepcopy(runner.load_manifest())
    unlocked["formal_dispatch_authorised"] = True
    with pytest.raises(runner.Experiment3RunnerError, match="locked or explicitly authorised"):
        runner.validate_design_manifest(unlocked)


def test_formal_cell_config_is_fresh_reference_calibrated_and_evolution_free() -> None:
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


def test_partial_whitelist_task_demo_may_cover_fewer_than_five_tasks() -> None:
    robot = runner.ROBOT_CONFIGURATIONS[0]
    cell = {
        "cell_id": f"{robot}::{runner.CONDITION}",
        "robot_configuration_id": robot,
        "condition": runner.CONDITION,
        "outcomes": {
            "STUDY": {"completed": True},
            "TGCD": {"completed": True},
            "IVC": {"completed": True},
        },
        "capability_validation_executed": True,
        "video_required": True,
        "video_complete": True,
        "passed_capability_whitelist": ["capability-a"],
        "task_demo_task_counts": {"passed": 0, "total": 1},
        "task_demo_video_complete": True,
    }

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
