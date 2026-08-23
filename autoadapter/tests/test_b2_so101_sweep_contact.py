from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from autoadapter2.b2.episode_runner import (
    B2DiagnosticEpisodeConfig,
    run_b2_diagnostic_episode,
)
from autoadapter2.b2.recap import RecapBudgets
from autoadapter2.libraries import load_robot_package


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = (
    REPOSITORY_ROOT
    / "autoadapter"
    / "libraries"
    / "robots"
    / "robotstudio_so101"
    / "1.0.0"
)
TASK_SUITE_PATH = (
    REPOSITORY_ROOT
    / "experiment"
    / "experiment1b_use"
    / "config"
    / "task_suite"
    / "task_suite.json"
)
CAPABILITY_DESIGN_PATH = (
    REPOSITORY_ROOT
    / "experiment"
    / "experiment1b_use"
    / "validation"
    / "reference"
    / "resolved"
    / "robotstudio_so101"
    / "capability_design.json"
)
DRIVER_PATH = PACKAGE_ROOT / "reference" / "fixed_capability_driver.py"


class _FixedPlanModel:
    def __init__(self, leaves: Sequence[Mapping[str, Any]]) -> None:
        self._responses = [
            {
                "reasoning_summary": "Execute the next fixed regression leaf.",
                "subtasks": [copy.deepcopy(dict(leaf))],
            }
            for leaf in leaves
        ]
        self._responses.append(
            {"reasoning_summary": "Regression plan complete.", "subtasks": []}
        )
        self._index = 0

    def generate_recap_json(self, **_kwargs: Any) -> dict[str, Any]:
        response = copy.deepcopy(self._responses[self._index])
        self._index += 1
        return response


def test_so101_legal_sweep_plan_stays_within_global_penetration_gate(
    tmp_path: Path,
) -> None:
    """Guard the valid real-model trajectory that exposed a soft catch contact."""

    pytest.importorskip("mujoco")
    leaves = [
        {
            "kind": "capability",
            "capability_name": "set_gripper_opening",
            "request": {"opening_fraction": 0.5, "max_duration_s": 1.0},
        },
        {
            "kind": "capability",
            "capability_name": "move_end_effector_to_position",
            "request": {
                "target_position_m": [0.38, 0.03, 0.25],
                "max_duration_s": 3.0,
            },
        },
        {
            "kind": "capability",
            "capability_name": "move_end_effector_to_position",
            "request": {
                "target_position_m": [0.38, 0.03, 0.2],
                "max_duration_s": 2.0,
            },
        },
        {
            "kind": "capability",
            "capability_name": "trace_cartesian_path",
            "request": {
                "waypoints_m": [[0.38, 0.03, 0.2], [0.38, 0.21, 0.2]],
                "max_duration_per_segment_s": 3.0,
            },
        },
    ]
    result = run_b2_diagnostic_episode(
        config=B2DiagnosticEpisodeConfig(
            task_suite_path=TASK_SUITE_PATH,
            robot_configuration_id="robotstudio_so101",
            task_id="mw_sweep_into_goal",
            replicate_id="R1",
            driver_path=DRIVER_PATH,
            capability_design_path=CAPABILITY_DESIGN_PATH,
            output_dir=tmp_path,
            record_video=False,
            wall_timeout_s=30.0,
        ),
        package=load_robot_package(PACKAGE_ROOT),
        model=_FixedPlanModel(leaves),
        budgets=RecapBudgets(
            max_model_calls=5,
            max_capability_calls=4,
            max_depth=1,
            max_subtasks_per_plan=1,
            max_invalid_outputs=1,
            max_history_chars=100_000,
        ),
    )

    assert result["controller"]["status"] == "CONTROLLER_FINISHED"
    assert result["harness"]["task_metric_passed"] is True
    assert result["harness"]["physical_execution_passed"] is True
    assert result["harness"]["contact_integrity"]["passed"] is True
    assert (
        result["harness"]["contact_integrity"]["minimum_contact_distance_m"]
        >= -0.005
    )
