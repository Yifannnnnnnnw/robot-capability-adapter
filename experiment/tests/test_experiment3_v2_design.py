from __future__ import annotations

import copy

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
    with pytest.raises(runner.Experiment3RunnerError, match="unauthorised"):
        runner.validate_design_manifest(unlocked)
