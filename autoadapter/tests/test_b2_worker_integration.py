from __future__ import annotations

import json
import math
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoadapter2.b2.public_observation import (
    _contacted_geom_ids,
    project_public_state,
)
from autoadapter2.b2.session_runner import (
    RecapWorkerSessionConfig,
    _ensure_before_deadline,
    run_recap_worker_session,
)
from autoadapter2.b2.worker_protocol import (
    initial_public_state,
    public_observation,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ROBOT_ROOT = REPOSITORY_ROOT / "autoadapter" / "libraries" / "robots"
SO101_ROOT = ROBOT_ROOT / "robotstudio_so101" / "1.0.0"
GO2_ROOT = ROBOT_ROOT / "unitree-go2-stock-12dof" / "1.0.0"


def _all_finite(value: Any) -> bool:
    if isinstance(value, bool) or isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_all_finite(item) for item in value)
    if isinstance(value, dict):
        return all(_all_finite(item) for item in value.values())
    return False


def test_public_state_profiles_are_small_allowlists_on_real_models() -> None:
    mujoco = pytest.importorskip("mujoco")
    so_model = mujoco.MjModel.from_xml_path(str(SO101_ROOT / "assets/reach_scene.xml"))
    so_data = mujoco.MjData(so_model)
    mujoco.mj_forward(so_model, so_data)
    so_state = project_public_state(
        robot_configuration_id="robotstudio_so101",
        mujoco=mujoco,
        model=so_model,
        data=so_data,
    )
    assert set(so_state) == {
        "simulation_time_s",
        "end_effector_position_world_m",
        "gripper_opening_fraction",
        "end_effector_contact_detected",
    }
    assert 0.0 <= so_state["gripper_opening_fraction"] <= 1.0

    go_model = mujoco.MjModel.from_xml_path(str(GO2_ROOT / "assets/go2_scene.xml"))
    go_data = mujoco.MjData(go_model)
    home_id = mujoco.mj_name2id(go_model, mujoco.mjtObj.mjOBJ_KEY, "home")
    mujoco.mj_resetDataKeyframe(go_model, go_data, home_id)
    mujoco.mj_forward(go_model, go_data)
    go_state = project_public_state(
        robot_configuration_id="unitree-go2-stock-12dof",
        mujoco=mujoco,
        model=go_model,
        data=go_data,
    )
    assert set(go_state) == {
        "simulation_time_s",
        "base_position_world_m",
        "base_roll_pitch_yaw_rad",
        "base_linear_velocity_world_m_s",
        "foot_contact_count",
    }
    assert 0 <= go_state["foot_contact_count"] <= 4
    assert _all_finite(so_state)
    assert _all_finite(go_state)

    visible = json.dumps({"so101": so_state, "go2": go_state}).lower()
    for forbidden in (
        "criterion",
        "threshold",
        "binding",
        "guard",
        "qpos",
        "qvel",
        "contact_pair",
    ):
        assert forbidden not in visible


def test_worker_protocol_selects_only_the_public_envelope() -> None:
    ready = {
        "type": "ready",
        "public_state": {"simulation_time_s": 0.0},
        "private_criterion": "must not cross",
    }
    assert initial_public_state(ready) == {"simulation_time_s": 0.0}

    mapped = public_observation(
        {
            "type": "observation",
            "ok": True,
            "sim_step_count": 7,
            "steps_added": 3,
            "public_state": {"simulation_time_s": 0.014},
            "private_criterion": "must not cross",
        }
    )
    assert mapped == {
        "operation": {
            "status": "EXECUTED",
            "steps_added": 3,
            "total_sim_steps": 7,
        },
        "public_state": {"simulation_time_s": 0.014},
    }

    exhausted = public_observation(
        {
            "type": "observation",
            "ok": False,
            "sim_step_count": 10,
            "error": {"code": "SESSION_BUDGET_EXCEEDED"},
            "public_state": {"simulation_time_s": 0.02},
        }
    )
    assert exhausted["operation"] == {
        "status": "ABORT",
        "error_code": "SESSION_BUDGET_EXCEEDED",
        "total_sim_steps": 10,
    }


def test_go2_foot_contact_count_uses_collision_geoms_not_marker_bodies() -> None:
    data = SimpleNamespace(
        ncon=3,
        contact=[
            SimpleNamespace(geom1=10, geom2=99),
            SimpleNamespace(geom1=98, geom2=12),
            SimpleNamespace(geom1=10, geom2=97),
        ],
    )

    assert _contacted_geom_ids(data, {10, 11, 12, 13}) == {10, 12}


def test_expired_session_deadline_is_rejected_before_protocol_write() -> None:
    with pytest.raises(RuntimeError, match="wall-time budget"):
        _ensure_before_deadline(time.monotonic() - 1.0)


class _ScriptedModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate_recap_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(json.loads(json.dumps(kwargs)))
        if len(self.calls) == 1:
            return {
                "reasoning_summary": "Set the public gripper opening low first.",
                "subtasks": [
                    {
                        "kind": "capability",
                        "capability_name": "set_gripper_opening",
                        "request": {
                            "opening_fraction": 0.25,
                            "max_duration_s": 1.0,
                        },
                    }
                ],
            }
        if len(self.calls) == 2:
            return {
                "reasoning_summary": "Reuse the same capability and physical session.",
                "subtasks": [
                    {
                        "kind": "capability",
                        "capability_name": "set_gripper_opening",
                        "request": {
                            "opening_fraction": 0.75,
                            "max_duration_s": 1.0,
                        },
                    }
                ],
            }
        return {
            "reasoning_summary": "No remaining controller action.",
            "subtasks": [],
        }


def test_real_persistent_worker_canary_runs_typed_recap_leaf(tmp_path: Path) -> None:
    pytest.importorskip("mujoco")
    driver_path = tmp_path / "driver.py"
    driver_path.write_text(
        textwrap.dedent(
            """
            import mujoco

            class Driver:
                def __init__(self, model, data):
                    self.model = model
                    self.data = data
                    self.gripper_actuator = mujoco.mj_name2id(
                        model, mujoco.mjtObj.mjOBJ_ACTUATOR, "gripper"
                    )
                    self.calls = 0

                def move_end_effector_to_position(self, request):
                    del request

                def trace_cartesian_path(self, request):
                    del request

                def set_gripper_opening(self, request):
                    self.calls += 1
                    fraction = float(request["opening_fraction"])
                    if fraction > 0.5 and self.calls != 2:
                        raise RuntimeError("driver instance was not reused")
                    self.data.ctrl[self.gripper_actuator] = -0.17453 + (
                        fraction * (1.74533 + 0.17453)
                    )
                    mujoco.mj_step(self.model, self.data)

                def approach_until_contact(self, request):
                    del request

                def move_cartesian_offset_and_return(self, request):
                    del request

            def build(*, model, data):
                return Driver(model, data)
            """
        ),
        encoding="utf-8",
    )
    design = json.loads(
        (
            REPOSITORY_ROOT
            / "experiment"
            / "experiment1"
            / "fixed_validation_bundles"
            / "robotstudio_so101"
            / "capability_design.json"
        ).read_text(encoding="utf-8")
    )
    model = _ScriptedModel()

    result = run_recap_worker_session(
        config=RecapWorkerSessionConfig(
            driver_path=driver_path,
            scene_path=SO101_ROOT / "assets/reach_scene.xml",
            robot_configuration_id="robotstudio_so101",
            max_steps=10,
            max_sim_time_s=1.0,
            wall_timeout_s=20.0,
        ),
        capability_design=design,
        public_task={"task_template_id": "canary", "objective": "Set the gripper."},
        model=model,
    )

    assert result["controller"]["status"] == "CONTROLLER_FINISHED"
    assert result["controller"]["capability_calls"] == 2
    assert result["worker"]["controller_protocol_completed"] is True
    assert result["worker"]["successful_method_invocations"] == 2
    assert result["worker"]["physical_evidence"]["step_count"] == 2
    assert set(result["initial_public_state"]) == {
        "simulation_time_s",
        "end_effector_position_world_m",
        "gripper_opening_fraction",
        "end_effector_contact_detected",
    }

    controller_start = json.loads(model.calls[0]["messages"][0]["content"])
    assert controller_start["initial_public_state"] == result[
        "initial_public_state"
    ]
