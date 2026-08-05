"""G3 compound tabletop capabilities using only the six-key runtime API."""

import time

from ._kinematics import _forward_kinematics, _solve_ik


def _target(position_m: list[float], gripper: float) -> dict[str, float]:
    return _solve_ik(position_m, gripper)


def _move(runtime: object, position_m: list[float], gripper: float) -> dict[str, float]:
    accepted = runtime.send_action(_target(position_m, gripper))
    observation = runtime.get_observation()
    _forward_kinematics(observation)
    time.sleep(0.001)
    return accepted


def _pick_place(
    runtime: object,
    object_position_m: list[float],
    target_position_m: list[float],
    lift_height_m: float,
) -> int:
    source = [float(value) for value in object_position_m]
    target = [float(value) for value in target_position_m]
    target[2] = max(target[2] + 0.017, source[2])
    transit_z = max(lift_height_m, source[2] + 0.05, target[2] + 0.05)
    _move(runtime, [source[0], source[1], transit_z], 80.0)
    _move(runtime, source, 80.0)
    _move(runtime, source, 10.0)
    _move(runtime, [source[0], source[1], transit_z], 10.0)
    _move(runtime, [target[0], target[1], transit_z], 10.0)
    _move(runtime, target, 10.0)
    _move(runtime, target, 80.0)
    _move(runtime, [target[0], target[1], transit_z], 80.0)
    return 8


def push_object(
    runtime: object,
    object_position_m: list[float],
    target_position_m: list[float],
    gripper_position: float = 80.0,
) -> dict[str, object]:
    source = [float(value) for value in object_position_m]
    target = [float(value) for value in target_position_m]
    dx = target[0] - source[0]
    dy = target[1] - source[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 0.000001:
        return {
            "status": "succeeded",
            "steps": 0,
            "accepted": {},
            "phase_reached": "complete",
        }
    ux = dx / length
    uy = dy / length
    offset = 0.035
    start = [source[0] - ux * offset, source[1] - uy * offset, source[2]]
    finish = [target[0] - ux * offset, target[1] - uy * offset, source[2]]
    _move(runtime, [start[0], start[1], start[2] + 0.06], gripper_position)
    _move(runtime, start, gripper_position)
    accepted = _move(runtime, finish, gripper_position)
    return {
        "status": "succeeded",
        "steps": 3,
        "accepted": accepted,
        "phase_reached": "complete",
    }


def pick_and_place(
    runtime: object,
    object_position_m: list[float],
    target_position_m: list[float],
    lift_height_m: float = 0.12,
) -> dict[str, object]:
    steps = _pick_place(runtime, object_position_m, target_position_m, lift_height_m)
    return {
        "status": "succeeded",
        "steps": steps,
        "completed_moves": 1,
        "phase_reached": "complete",
    }


def place_objects(
    runtime: object,
    moves: list[dict[str, object]],
    lift_height_m: float = 0.12,
) -> dict[str, object]:
    total_steps = 0
    completed = 0
    for move in moves:
        object_extent_m = float(move["object_extent_m"])
        if object_extent_m <= 0.0:
            raise ValueError("object_extent_m must be positive")
        total_steps += _pick_place(
            runtime,
            list(move["object_position_m"]),
            list(move["target_position_m"]),
            max(lift_height_m, object_extent_m + 0.05),
        )
        completed += 1
    return {
        "status": "succeeded",
        "steps": total_steps,
        "completed_moves": completed,
        "phase_reached": "complete",
        "failed_move_index": None,
        "timeout_scope": None,
    }
