"""Task-aware public feedback for the ten fixed AA2-B2 tasks.

This module deliberately has no access to task scoring, private measurement
bindings, guards, resets, reference drivers, or oracle traces.  A trusted
parent first reduces a public Task Library record and its public arguments to
the closed projection below.  The worker can then combine that projection
with a small allowlist of named MuJoCo entities to report continuous physical
facts without reporting a task verdict.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from autoadapter2.b2.public_observation import project_public_state


B2_PUBLIC_TASK_PROJECTION_REVISION = "aa2-b2-public-task-v1"
B2_TASK_PUBLIC_OBSERVATION_REVISION = "aa2-b2-task-observation-v1"


class TaskPublicObservationError(RuntimeError):
    """Raised when a B2 public task or named public entity is invalid."""


@dataclass(frozen=True)
class _TaskSpec:
    robot_configuration_id: str
    observation_kind: str
    parameter_kinds: tuple[tuple[str, str], ...]


_SO101 = "robotstudio_so101"
_GO2 = "unitree-go2-stock-12dof"

_SO_PUSH_PARAMETERS = (
    ("contact_position", "vector3"),
    ("target_position", "vector3"),
    ("tool_target_position", "vector3"),
)
_SO_PICK_PARAMETERS = (
    ("start_position", "vector3"),
    ("target_position", "vector3"),
    ("release_position", "vector3"),
    ("tool_target_position", "vector3"),
    ("grasp_position", "vector3"),
    ("grasp_wrist_roll", "number"),
    ("grasp_gripper", "number"),
)
_SO_DIAL_PARAMETERS = (
    ("contact_position", "vector3"),
    ("target_position", "vector3"),
    ("tool_target_position", "vector3"),
    ("route_position", "vector3"),
)

_TASK_SPECS: dict[str, _TaskSpec] = {
    "mw_push_to_goal": _TaskSpec(_SO101, "so_object", _SO_PUSH_PARAMETERS),
    "mw_sweep_into_goal": _TaskSpec(_SO101, "so_object", _SO_PUSH_PARAMETERS),
    "mw_pick_place": _TaskSpec(_SO101, "so_object", _SO_PICK_PARAMETERS),
    "mw_pick_place_wall": _TaskSpec(
        _SO101,
        "so_object",
        _SO_PICK_PARAMETERS + (("route_position", "vector3"),),
    ),
    "mw_bin_picking": _TaskSpec(
        _SO101,
        "so_object",
        _SO_PICK_PARAMETERS + (("route_position", "vector3"),),
    ),
    "mw_dial_turn": _TaskSpec(_SO101, "so_dial", _SO_DIAL_PARAMETERS),
    "GO2-T02": _TaskSpec(
        _GO2,
        "go2_step_up",
        (
            ("duration_s", "positive_number"),
            ("command_speed_m_s", "optional_number"),
            ("step_height_m", "positive_number"),
        ),
    ),
    "GO2-T03": _TaskSpec(
        _GO2,
        "go2_step_down",
        (
            ("duration_s", "positive_number"),
            ("command_speed_m_s", "optional_number"),
            ("step_height_m", "positive_number"),
        ),
    ),
    "GO2-T06": _TaskSpec(
        _GO2,
        "go2_path",
        (("duration_s", "positive_number"), ("path_waypoints_m", "path2")),
    ),
    "GO2-T16": _TaskSpec(
        _GO2,
        "go2_point_goal",
        (("duration_s", "positive_number"), ("end_table_center_m", "vector3")),
    ),
    "GO2-T17": _TaskSpec(
        _GO2,
        "go2_path",
        (("duration_s", "positive_number"), ("path_waypoints_m", "path2")),
    ),
}

_PROJECTION_KEYS = {
    "projection_revision",
    "robot_configuration_id",
    "task_id",
    "task_name",
    "objective",
    "task_parameters",
}


def build_b2_public_task_projection(
    *,
    task_definition: Mapping[str, Any],
    public_arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Reduce one canonical public task and its public request to a closed view.

    ``task_definition`` may contain source and scoring material from the public
    Task Library, but only identity, human-readable intent, and the fixed
    task-native parameters cross this boundary.  Unknown request fields are
    rejected instead of being copied into the controller-visible projection.
    """

    if not isinstance(task_definition, Mapping):
        raise TaskPublicObservationError("task_definition must be an object")
    if not isinstance(public_arguments, Mapping):
        raise TaskPublicObservationError("public_arguments must be an object")

    task_id = task_definition.get("task_id")
    if not isinstance(task_id, str) or task_id not in _TASK_SPECS:
        raise TaskPublicObservationError(f"task is not in the fixed B2 set: {task_id!r}")
    spec = _TASK_SPECS[task_id]

    if set(public_arguments) != {"request"}:
        raise TaskPublicObservationError(
            "public_arguments must contain only the request envelope"
        )
    request = public_arguments.get("request")
    if not isinstance(request, Mapping) or set(request) != {
        "task_id",
        "task_parameters",
    }:
        raise TaskPublicObservationError(
            "public request must contain only task_id and task_parameters"
        )
    if request.get("task_id") != task_id:
        raise TaskPublicObservationError("public request task_id does not match the task")
    raw_parameters = request.get("task_parameters")
    parameters = _validate_parameters(spec, raw_parameters)

    task_name = task_definition.get("name")
    objective = task_definition.get("description")
    if not isinstance(task_name, str) or not task_name.strip():
        raise TaskPublicObservationError("public task name must be non-empty")
    if not isinstance(objective, str) or not objective.strip():
        raise TaskPublicObservationError("public task description must be non-empty")

    return _finite_json_object(
        {
            "projection_revision": B2_PUBLIC_TASK_PROJECTION_REVISION,
            "robot_configuration_id": spec.robot_configuration_id,
            "task_id": task_id,
            "task_name": task_name,
            "objective": objective,
            "task_parameters": parameters,
        }
    )


def project_task_public_observation(
    *,
    public_task: Mapping[str, Any],
    robot_configuration_id: str,
    mujoco: Any,
    model: Any,
    data: Any,
) -> dict[str, Any]:
    """Return the bounded task-aware observation for a canonical B2 session.

    The result contains named, derived physical facts only.  In particular it
    contains no raw generalized coordinates or velocities and computes no
    criterion threshold, pass/fail clause, Harness verdict, or oracle state.
    """

    projection, spec = _validate_projection(public_task)
    if robot_configuration_id != spec.robot_configuration_id:
        raise TaskPublicObservationError(
            "public task does not match the configured B2 robot"
        )

    robot_state = project_public_state(
        robot_configuration_id=robot_configuration_id,
        mujoco=mujoco,
        model=model,
        data=data,
    )
    parameters = projection["task_parameters"]
    if spec.observation_kind == "so_object":
        task_state = _project_so_object_task(
            parameters=parameters,
            mujoco=mujoco,
            model=model,
            data=data,
            robot_state=robot_state,
        )
    elif spec.observation_kind == "so_dial":
        task_state = _project_so_dial_task(
            parameters=parameters,
            mujoco=mujoco,
            model=model,
            data=data,
            robot_state=robot_state,
        )
    elif spec.observation_kind.startswith("go2_"):
        task_state = _project_go2_task(
            observation_kind=spec.observation_kind,
            parameters=parameters,
            mujoco=mujoco,
            model=model,
            data=data,
            robot_state=robot_state,
        )
    else:  # pragma: no cover - closed table makes this unreachable
        raise TaskPublicObservationError("unsupported B2 observation kind")

    return _finite_json_object(
        {
            "observation_revision": B2_TASK_PUBLIC_OBSERVATION_REVISION,
            "task_id": projection["task_id"],
            "robot_state": robot_state,
            "task_state": task_state,
        }
    )


def _validate_projection(
    public_task: Mapping[str, Any],
) -> tuple[dict[str, Any], _TaskSpec]:
    if not isinstance(public_task, Mapping) or set(public_task) != _PROJECTION_KEYS:
        raise TaskPublicObservationError(
            "public task must be the closed AA2-B2 task projection"
        )
    projection = _finite_json_object(public_task)
    if projection.get("projection_revision") != B2_PUBLIC_TASK_PROJECTION_REVISION:
        raise TaskPublicObservationError("public task projection revision is not fixed")
    task_id = projection.get("task_id")
    if not isinstance(task_id, str) or task_id not in _TASK_SPECS:
        raise TaskPublicObservationError("public task is not in the fixed B2 set")
    spec = _TASK_SPECS[task_id]
    if projection.get("robot_configuration_id") != spec.robot_configuration_id:
        raise TaskPublicObservationError("public task robot identity is invalid")
    for key in ("task_name", "objective"):
        value = projection.get(key)
        if not isinstance(value, str) or not value.strip():
            raise TaskPublicObservationError(f"public task {key} must be non-empty")
    projection["task_parameters"] = _validate_parameters(
        spec, projection.get("task_parameters")
    )
    return projection, spec


def _validate_parameters(spec: _TaskSpec, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TaskPublicObservationError("task_parameters must be an object")
    expected = {name for name, _kind in spec.parameter_kinds}
    optional = {
        name for name, kind in spec.parameter_kinds if kind.startswith("optional_")
    }
    if not expected - optional <= set(value) or not set(value) <= expected:
        raise TaskPublicObservationError(
            "task_parameters do not match the fixed public task projection"
        )
    result: dict[str, Any] = {}
    for name, kind in spec.parameter_kinds:
        if name not in value and kind.startswith("optional_"):
            continue
        raw = value[name]
        if kind == "number":
            result[name] = _number(raw, name=name)
        elif kind == "positive_number":
            number = _number(raw, name=name)
            if number <= 0.0:
                raise TaskPublicObservationError(f"{name} must be positive")
            result[name] = number
        elif kind == "optional_number":
            result[name] = _number(raw, name=name)
        elif kind == "vector3":
            result[name] = _vector(raw, length=3, name=name)
        elif kind == "path2":
            if not isinstance(raw, list) or len(raw) < 2:
                raise TaskPublicObservationError(
                    f"{name} must contain at least two planar waypoints"
                )
            result[name] = [
                _vector(item, length=2, name=f"{name}[{index}]")
                for index, item in enumerate(raw)
            ]
        else:  # pragma: no cover - closed table makes this unreachable
            raise TaskPublicObservationError("invalid public parameter kind")
    return result


def _project_so_object_task(
    *,
    parameters: Mapping[str, Any],
    mujoco: Any,
    model: Any,
    data: Any,
    robot_state: Mapping[str, Any],
) -> dict[str, Any]:
    object_body_id = _name_id(
        mujoco, model, mujoco.mjtObj.mjOBJ_BODY, "workpiece"
    )
    gripper_body_id = _name_id(
        mujoco, model, mujoco.mjtObj.mjOBJ_BODY, "gripper"
    )
    object_position = [float(value) for value in data.xpos[object_body_id]]
    end_effector_position = robot_state["end_effector_position_world_m"]
    goal_position = list(parameters["target_position"])

    task_object: dict[str, Any] = {
        "position_world_m": object_position,
        "end_effector_distance_m": _distance(
            object_position, end_effector_position
        ),
        "gripper_contact_detected": _bodies_in_contact(
            model=model,
            data=data,
            first_body_id=object_body_id,
            second_body_id=gripper_body_id,
        ),
    }
    public_start = parameters.get("start_position")
    if isinstance(public_start, list):
        task_object["displacement_from_public_start_m"] = _distance(
            object_position, public_start
        )
        task_object["height_change_from_public_start_m"] = (
            object_position[2] - public_start[2]
        )

    waypoint_distances: dict[str, float] = {}
    for public_name, output_name in (
        ("contact_position", "contact"),
        ("grasp_position", "grasp"),
        ("route_position", "route"),
        ("tool_target_position", "tool_target"),
        ("release_position", "release"),
    ):
        waypoint = parameters.get(public_name)
        if isinstance(waypoint, list):
            waypoint_distances[output_name] = _distance(
                end_effector_position, waypoint
            )

    return {
        "task_object": task_object,
        "public_goal": {
            "position_world_m": goal_position,
            "task_object_distance_m": _distance(object_position, goal_position),
        },
        "end_effector_public_waypoint_distances_m": waypoint_distances,
    }


def _project_so_dial_task(
    *,
    parameters: Mapping[str, Any],
    mujoco: Any,
    model: Any,
    data: Any,
    robot_state: Mapping[str, Any],
) -> dict[str, Any]:
    dial_tip_site_id = _name_id(
        mujoco, model, mujoco.mjtObj.mjOBJ_SITE, "dial_tip_site"
    )
    dial_body_id = _name_id(mujoco, model, mujoco.mjtObj.mjOBJ_BODY, "dial")
    gripper_body_id = _name_id(
        mujoco, model, mujoco.mjtObj.mjOBJ_BODY, "gripper"
    )
    dial_tip_position = [
        float(value) for value in data.site_xpos[dial_tip_site_id]
    ]
    target_position = list(parameters["target_position"])
    end_effector_position = robot_state["end_effector_position_world_m"]
    waypoint_distances = {
        output_name: _distance(end_effector_position, parameters[public_name])
        for public_name, output_name in (
            ("contact_position", "contact"),
            ("route_position", "route"),
            ("tool_target_position", "tool_target"),
        )
    }
    return {
        "dial_tip": {
            "position_world_m": dial_tip_position,
            "target_distance_m": _distance(dial_tip_position, target_position),
            "gripper_contact_detected": _bodies_in_contact(
                model=model,
                data=data,
                first_body_id=dial_body_id,
                second_body_id=gripper_body_id,
            ),
        },
        "public_goal": {"position_world_m": target_position},
        "end_effector_public_waypoint_distances_m": waypoint_distances,
    }


def _project_go2_task(
    *,
    observation_kind: str,
    parameters: Mapping[str, Any],
    mujoco: Any,
    model: Any,
    data: Any,
    robot_state: Mapping[str, Any],
) -> dict[str, Any]:
    foot_positions = {
        public_name: [float(value) for value in data.xpos[_name_id(
            mujoco, model, mujoco.mjtObj.mjOBJ_BODY, body_name
        )]]
        for public_name, body_name in (
            ("front_left", "FL_foot"),
            ("front_right", "FR_foot"),
            ("rear_left", "RL_foot"),
            ("rear_right", "RR_foot"),
        )
    }
    task_state: dict[str, Any] = {
        "feet_position_world_m": foot_positions,
        "public_time_budget_remaining_s": max(
            float(parameters["duration_s"])
            - float(robot_state["simulation_time_s"]),
            0.0,
        ),
    }

    if observation_kind in {"go2_step_up", "go2_step_down"}:
        task_state["step_command"] = {
            "direction": "up" if observation_kind == "go2_step_up" else "down",
            "step_height_m": float(parameters["step_height_m"]),
        }
        if "command_speed_m_s" in parameters:
            task_state["step_command"]["forward_speed_m_s"] = float(
                parameters["command_speed_m_s"]
            )
    elif observation_kind == "go2_point_goal":
        goal = list(parameters["end_table_center_m"])
        base = robot_state["base_position_world_m"]
        task_state["public_goal"] = {
            "position_world_m": goal,
            "base_planar_distance_m": _distance(base[:2], goal[:2]),
            "base_spatial_distance_m": _distance(base, goal),
        }
    elif observation_kind == "go2_path":
        path = parameters["path_waypoints_m"]
        base_xy = robot_state["base_position_world_m"][:2]
        task_state["public_path"] = _path_feedback(base_xy, path)
    else:  # pragma: no cover - closed table makes this unreachable
        raise TaskPublicObservationError("invalid Go2 public observation kind")

    return task_state


def _path_feedback(point: list[float], path: list[list[float]]) -> dict[str, Any]:
    waypoint_distances = [_distance(point, waypoint) for waypoint in path]
    nearest_waypoint_index = min(
        range(len(path)), key=lambda index: waypoint_distances[index]
    )

    segment_lengths = [
        _distance(path[index], path[index + 1])
        for index in range(len(path) - 1)
    ]
    total_length = sum(segment_lengths)
    best_distance = math.inf
    best_segment = 0
    best_fraction = 0.0
    best_along = 0.0
    traversed = 0.0
    for index, segment_length in enumerate(segment_lengths):
        start = path[index]
        end = path[index + 1]
        delta_x = end[0] - start[0]
        delta_y = end[1] - start[1]
        length_squared = delta_x * delta_x + delta_y * delta_y
        if length_squared <= 0.0:
            fraction = 0.0
        else:
            fraction = min(
                max(
                    ((point[0] - start[0]) * delta_x
                    + (point[1] - start[1]) * delta_y)
                    / length_squared,
                    0.0,
                ),
                1.0,
            )
        projection = [
            start[0] + fraction * delta_x,
            start[1] + fraction * delta_y,
        ]
        distance = _distance(point, projection)
        if distance < best_distance:
            best_distance = distance
            best_segment = index
            best_fraction = fraction
            best_along = traversed + fraction * segment_length
        traversed += segment_length

    progress = best_along / total_length if total_length > 0.0 else 0.0
    return {
        "waypoint_distances_m": waypoint_distances,
        "nearest_waypoint_index": nearest_waypoint_index,
        "closest_segment_index": best_segment,
        "closest_segment_fraction": best_fraction,
        "cross_track_distance_m": best_distance,
        "projected_progress_fraction": progress,
        "final_waypoint_distance_m": waypoint_distances[-1],
    }


def _name_id(mujoco: Any, model: Any, object_type: Any, name: str) -> int:
    identifier = int(mujoco.mj_name2id(model, object_type, name))
    if identifier < 0:
        raise TaskPublicObservationError(
            f"public task entity {name!r} is absent from the canonical scene"
        )
    return identifier


def _bodies_in_contact(
    *,
    model: Any,
    data: Any,
    first_body_id: int,
    second_body_id: int,
) -> bool:
    for index in range(int(data.ncon)):
        contact = data.contact[index]
        body_1 = int(model.geom_bodyid[int(contact.geom1)])
        body_2 = int(model.geom_bodyid[int(contact.geom2)])
        if (
            _is_descendant(model, body_1, first_body_id)
            and _is_descendant(model, body_2, second_body_id)
        ) or (
            _is_descendant(model, body_2, first_body_id)
            and _is_descendant(model, body_1, second_body_id)
        ):
            return True
    return False


def _is_descendant(model: Any, body_id: int, ancestor_id: int) -> bool:
    current = body_id
    while current > 0 and current != ancestor_id:
        current = int(model.body_parentid[current])
    return current == ancestor_id


def _distance(first: list[float], second: list[float]) -> float:
    if len(first) != len(second):
        raise TaskPublicObservationError("public positions use inconsistent frames")
    return math.sqrt(
        sum((float(a) - float(b)) ** 2 for a, b in zip(first, second, strict=True))
    )


def _number(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskPublicObservationError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise TaskPublicObservationError(f"{name} must be finite")
    return result


def _vector(value: Any, *, length: int, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        raise TaskPublicObservationError(f"{name} must have length {length}")
    return [
        _number(item, name=f"{name}[{index}]")
        for index, item in enumerate(value)
    ]


def _finite_json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        copied = json.loads(json.dumps(dict(value), allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise TaskPublicObservationError("public task value must be finite JSON") from exc
    if not isinstance(copied, dict):  # pragma: no cover - dict input guarantees this
        raise TaskPublicObservationError("public task value must be an object")
    return copied


__all__ = [
    "B2_PUBLIC_TASK_PROJECTION_REVISION",
    "B2_TASK_PUBLIC_OBSERVATION_REVISION",
    "TaskPublicObservationError",
    "build_b2_public_task_projection",
    "project_task_public_observation",
]
