from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

import pytest

from autoadapter2.b2.task_public_observation import (
    B2_PUBLIC_TASK_PROJECTION_REVISION,
    B2_TASK_PUBLIC_OBSERVATION_REVISION,
    TaskPublicObservationError,
    build_b2_public_task_projection,
    project_task_public_observation,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ROBOT_ROOT = REPOSITORY_ROOT / "autoadapter" / "libraries" / "robots"

_TASKS = {
    "robotstudio_so101": {
        "mw_push_to_goal": "push_to_goal_scene.xml",
        "mw_sweep_into_goal": "sweep_into_goal_scene.xml",
        "mw_pick_place": "pick_place_scene.xml",
        "mw_pick_place_wall": "pick_place_wall_scene.xml",
        "mw_bin_picking": "bin_picking_scene.xml",
    },
    "unitree-go2-stock-12dof": {
        "GO2-T02": "lee_step_up.xml",
        "GO2-T03": "lee_step_down.xml",
        "GO2-T06": "miki_mixed_course.xml",
        "GO2-T16": "barkour_tables.xml",
        "GO2-T17": "barkour_weave.xml",
    },
}

_FORBIDDEN_OBSERVATION_TERMS = (
    "private",
    "criterion",
    "threshold",
    "binding",
    "guard",
    "verdict",
    "reference",
    "oracle",
    "qpos",
    "qvel",
    "raw_state",
    "reset",
    "seed",
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _records(robot_id: str) -> tuple[dict[str, dict], dict[str, dict], Path]:
    package = ROBOT_ROOT / robot_id / "1.0.0"
    catalog = _read(package / "tasks" / "catalog.json")
    instances = _read(package / "tasks" / "private" / "instances.json")
    return (
        {task["task_id"]: task for task in catalog["tasks"]},
        {instance["task_id"]: instance for instance in instances["instances"]},
        package,
    )


def _all_finite_json(value: Any) -> bool:
    if value is None or isinstance(value, (bool, str, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_all_finite_json(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _all_finite_json(item)
            for key, item in value.items()
        )
    return False


@pytest.mark.parametrize(
    ("robot_id", "task_id", "scene_name"),
    [
        (robot_id, task_id, scene_name)
        for robot_id, tasks in _TASKS.items()
        for task_id, scene_name in tasks.items()
    ],
)
def test_ten_b2_tasks_project_allowlisted_facts_on_real_scenes(
    robot_id: str,
    task_id: str,
    scene_name: str,
) -> None:
    mujoco = pytest.importorskip("mujoco")
    tasks, instances, package = _records(robot_id)
    instance = instances[task_id]
    projection = build_b2_public_task_projection(
        task_definition=tasks[task_id],
        public_arguments=instance["public_arguments"],
    )

    assert set(projection) == {
        "projection_revision",
        "robot_configuration_id",
        "task_id",
        "task_name",
        "objective",
        "task_parameters",
    }
    assert projection["projection_revision"] == B2_PUBLIC_TASK_PROJECTION_REVISION
    assert projection["robot_configuration_id"] == robot_id
    assert "scoring" not in projection
    assert "invocation_schema" not in projection

    model = mujoco.MjModel.from_xml_path(
        str((package / "assets" / scene_name).resolve())
    )
    data = mujoco.MjData(model)
    reset = instance["reset"]
    if reset["kind"] == "keyframe":
        key_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_KEY, reset["name"]
        )
        assert key_id >= 0
        mujoco.mj_resetDataKeyframe(model, data, key_id)
    mujoco.mj_forward(model, data)

    observation = project_task_public_observation(
        public_task=projection,
        robot_configuration_id=robot_id,
        mujoco=mujoco,
        model=model,
        data=data,
    )

    assert set(observation) == {
        "observation_revision",
        "task_id",
        "robot_state",
        "task_state",
    }
    assert observation["observation_revision"] == (
        B2_TASK_PUBLIC_OBSERVATION_REVISION
    )
    assert observation["task_id"] == task_id
    assert _all_finite_json(observation)
    serialized = json.dumps(observation, sort_keys=True).lower()
    for forbidden in _FORBIDDEN_OBSERVATION_TERMS:
        assert forbidden not in serialized

    task_state = observation["task_state"]
    if robot_id == "robotstudio_so101":
        object_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, "workpiece"
        )
        assert task_state["task_object"]["position_world_m"] == pytest.approx(
            data.xpos[object_id]
        )
        assert isinstance(
            task_state["task_object"]["gripper_contact_detected"], bool
        )
        assert task_state["public_goal"]["position_world_m"] == (
            projection["task_parameters"]["target_position"]
        )
        assert task_state["public_goal"]["task_object_distance_m"] >= 0.0
    else:
        assert set(task_state["feet_position_world_m"]) == {
            "front_left",
            "front_right",
            "rear_left",
            "rear_right",
        }
        assert task_state["public_time_budget_remaining_s"] >= 0.0
        if task_id in {"GO2-T02", "GO2-T03"}:
            assert task_state["step_command"]["direction"] == (
                "up" if task_id == "GO2-T02" else "down"
            )
        elif task_id == "GO2-T16":
            assert task_state["public_goal"]["base_planar_distance_m"] >= 0.0
        else:
            path = task_state["public_path"]
            assert len(path["waypoint_distances_m"]) == len(
                projection["task_parameters"]["path_waypoints_m"]
            )
            assert 0.0 <= path["closest_segment_fraction"] <= 1.0
            assert 0.0 <= path["projected_progress_fraction"] <= 1.0


def test_projection_rejects_unknown_fields_instead_of_copying_them() -> None:
    tasks, instances, _package = _records("robotstudio_so101")
    public_arguments = copy.deepcopy(instances["mw_push_to_goal"]["public_arguments"])
    public_arguments["request"]["task_parameters"]["private_threshold"] = 0.0

    with pytest.raises(TaskPublicObservationError, match="do not match"):
        build_b2_public_task_projection(
            task_definition=tasks["mw_push_to_goal"],
            public_arguments=public_arguments,
        )


def test_projector_rejects_noncanonical_projection_and_robot_mismatch() -> None:
    mujoco = pytest.importorskip("mujoco")
    tasks, instances, package = _records("robotstudio_so101")
    projection = build_b2_public_task_projection(
        task_definition=tasks["mw_push_to_goal"],
        public_arguments=instances["mw_push_to_goal"]["public_arguments"],
    )
    model = mujoco.MjModel.from_xml_path(
        str((package / "assets" / "push_to_goal_scene.xml").resolve())
    )
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    with_extra = dict(projection, verdict="PASS")
    with pytest.raises(TaskPublicObservationError, match="closed"):
        project_task_public_observation(
            public_task=with_extra,
            robot_configuration_id="robotstudio_so101",
            mujoco=mujoco,
            model=model,
            data=data,
        )

    with pytest.raises(TaskPublicObservationError, match="configured B2 robot"):
        project_task_public_observation(
            public_task=projection,
            robot_configuration_id="unitree-go2-stock-12dof",
            mujoco=mujoco,
            model=model,
            data=data,
        )
