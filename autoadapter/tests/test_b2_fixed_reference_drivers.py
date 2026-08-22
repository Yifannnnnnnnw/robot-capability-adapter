from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autoadapter2.b2.session_runner import (
    RecapWorkerSessionConfig,
    run_recap_worker_session,
)
from autoadapter2.driver_synthesis import audit_driver_source


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ROBOT_ROOT = REPOSITORY_ROOT / "autoadapter/libraries/robots"
BUNDLE_ROOT = REPOSITORY_ROOT / "experiment/b2_recap/reference_validation/resolved"


def _leaf(name: str, request: dict[str, Any]) -> dict[str, Any]:
    return {
        "reasoning_summary": f"Run the next fixed canary leaf: {name}.",
        "subtasks": [
            {
                "kind": "capability",
                "capability_name": name,
                "request": request,
            }
        ],
    }


class _ScriptedModel:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def generate_recap_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(json.loads(json.dumps(kwargs)))
        return self.responses[len(self.calls) - 1]


def _design(robot_id: str) -> dict[str, Any]:
    return json.loads(
        (BUNDLE_ROOT / robot_id / "capability_design.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.mark.parametrize(
    ("robot_id", "expected_methods"),
    [
        (
            "robotstudio_so101",
            (
                "move_end_effector_to_position",
                "trace_cartesian_path",
                "set_gripper_opening",
                "approach_until_contact",
                "move_cartesian_offset_and_return",
            ),
        ),
        (
            "unitree-go2-stock-12dof",
            (
                "track_planar_twist",
                "move_body_relative_pose",
                "trace_planar_path",
                "set_body_height",
                "hold_stable_stance",
            ),
        ),
    ],
)
def test_fixed_reference_source_has_only_the_typed_capability_boundary(
    robot_id: str,
    expected_methods: tuple[str, ...],
) -> None:
    path = ROBOT_ROOT / robot_id / "1.0.0/reference/fixed_capability_driver.py"
    source = path.read_text(encoding="utf-8")

    audit = audit_driver_source(
        source,
        condition="skeleton-assisted",
        capability_methods=expected_methods,
    )

    assert audit.imports_trusted_skeleton is True
    for forbidden in (
        "fixture_task",
        "task_parameters",
        "mw_pick_place",
        "mw_push_to_goal",
        "GO2-T",
    ):
        assert forbidden not in source


@pytest.mark.parametrize(
    ("robot_id", "scene_name", "responses", "minimum_steps"),
    [
        (
            "robotstudio_so101",
            "reach_scene.xml",
            [
                _leaf(
                    "set_gripper_opening",
                    {"opening_fraction": 0.2, "max_duration_s": 1.0},
                ),
                _leaf(
                    "move_end_effector_to_position",
                    {
                        "target_position_m": [0.4, 0.1, 0.2],
                        "max_duration_s": 4.0,
                    },
                ),
                _leaf(
                    "set_gripper_opening",
                    {"opening_fraction": 0.8, "max_duration_s": 1.0},
                ),
                {"reasoning_summary": "Canary complete.", "subtasks": []},
            ],
            500,
        ),
        (
            "unitree-go2-stock-12dof",
            "go2_scene.xml",
            [
                _leaf(
                    "set_body_height",
                    {"target_height_m": 0.28, "max_duration_s": 0.5},
                ),
                _leaf(
                    "move_body_relative_pose",
                    {
                        "translation_initial_yaw_m": [0.08, 0.0],
                        "yaw_delta_rad": 0.0,
                        "max_duration_s": 4.0,
                    },
                ),
                _leaf("hold_stable_stance", {"duration_s": 1.0}),
                {"reasoning_summary": "Canary complete.", "subtasks": []},
            ],
            1_000,
        ),
    ],
)
def test_fixed_reference_reuses_one_real_session_across_three_capability_calls(
    robot_id: str,
    scene_name: str,
    responses: list[dict[str, Any]],
    minimum_steps: int,
) -> None:
    pytest.importorskip("mujoco")
    package_root = ROBOT_ROOT / robot_id / "1.0.0"
    model = _ScriptedModel(responses)

    result = run_recap_worker_session(
        config=RecapWorkerSessionConfig(
            driver_path=package_root / "reference/fixed_capability_driver.py",
            scene_path=package_root / "assets" / scene_name,
            robot_configuration_id=robot_id,
            max_steps=5_000,
            max_sim_time_s=10.0,
            wall_timeout_s=30.0,
        ),
        capability_design=_design(robot_id),
        public_task={
            "task_template_id": "b2-persistent-reference-canary",
            "objective": "Exercise three typed leaves in one physical session.",
        },
        model=model,
    )

    controller = result["controller"]
    worker = result["worker"]
    assert controller["status"] == "CONTROLLER_FINISHED"
    assert controller["model_calls"] == 4
    assert controller["capability_calls"] == 3
    assert worker["worker_completed"] is True
    assert worker["controller_protocol_completed"] is True
    assert worker["successful_method_invocations"] == 3
    assert worker["capability_errors"] == []
    assert worker["physical_evidence"]["step_count"] >= minimum_steps
    assert len(model.calls) == 4
