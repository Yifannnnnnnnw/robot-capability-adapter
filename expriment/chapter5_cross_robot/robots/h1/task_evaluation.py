"""Physical verdicts for the five selected Unitree H1 catalog tasks.

The scorer consumes the native MuJoCo trace emitted by
``auto_adapter.demo_trace.DemoTrace``.  Controller return values, method
names, and generated-driver metadata are never used as task evidence.  The
numerical criteria are the maintained H1 catalog's local proposed,
uncalibrated requirements; external sources motivate the operation families.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from numbers import Real
from typing import Any


TASKS = ("H1-T01", "H1-T02", "H1-T04", "H1-T13", "H1-T14")
DEADLINE_S = 30.0
FINAL_WINDOW_S = 2.0
SPEED_WINDOW_S = (5.0, 15.0)
WALKING_SPEED_M_S = 0.4
WALKING_SPEED_RMSE_THRESHOLD_M_S = 0.15
STANDING_DRIFT_THRESHOLD_M = 0.08
FOREARM_ERROR_THRESHOLD_M = 0.06
OBJECT_GOAL_ERROR_THRESHOLD_M = 0.08
BODY_TILT_THRESHOLD_DEG = 25.0
PELVIS_CLEARANCE_THRESHOLD_M = 0.65
FOREARM_LOCAL_POINT_M = (0.28, 0.0, -0.015)
LEFT_ELBOW_BODY = "left_elbow_link"
RIGHT_ELBOW_BODY = "right_elbow_link"
FOREARM_BODIES = {LEFT_ELBOW_BODY, RIGHT_ELBOW_BODY}
LEFT_FOOT_BODY = "left_ankle_link"
RIGHT_FOOT_BODY = "right_ankle_link"
PELVIS_LABEL = "pelvis"
TORSO_LABEL = "torso"


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _vector(value: Any, size: int, name: str) -> list[float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != size:
        raise ValueError(f"{name} must have length {size}")
    return [_number(item, f"{name}[{index}]") for index, item in enumerate(value)]


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _metric(check: str, value: Any, threshold: Any, ok: bool, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "check": check,
        "value": value,
        "threshold": threshold,
        "ok": bool(ok),
    }
    result.update(extra)
    return result


def _state_item(samples: list[Mapping[str, Any]], label: str, index: int) -> Mapping[str, Any]:
    state = _mapping(samples[index].get("state"), f"sample[{index}].state")
    if label not in state:
        raise ValueError(f"sample[{index}] is missing state label {label!r}")
    return _mapping(state[label], f"sample[{index}].state[{label!r}]")


def _position(samples: list[Mapping[str, Any]], label: str, index: int) -> list[float]:
    return _vector(
        _state_item(samples, label, index).get("position"),
        3,
        f"sample[{index}].{label}.position",
    )


def _rotation(samples: list[Mapping[str, Any]], label: str, index: int) -> list[float]:
    return _vector(
        _state_item(samples, label, index).get("rotation"),
        9,
        f"sample[{index}].{label}.rotation",
    )


def _linear_velocity(samples: list[Mapping[str, Any]], label: str, index: int) -> list[float]:
    return _vector(
        _state_item(samples, label, index).get("linear_velocity"),
        3,
        f"sample[{index}].{label}.linear_velocity",
    )


def _distance(first: Sequence[float], second: Sequence[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(first, second, strict=True)))


def _validate_trace(
    samples_arg: Any,
    times_arg: Any,
) -> tuple[list[Mapping[str, Any]], list[float]]:
    if isinstance(samples_arg, (str, bytes)) or not isinstance(samples_arg, Sequence):
        raise ValueError("samples must be a sequence")
    if len(samples_arg) < 2:
        raise ValueError("at least two physics samples are required")
    samples = [_mapping(sample, f"sample[{index}]") for index, sample in enumerate(samples_arg)]
    if isinstance(times_arg, (str, bytes)) or not isinstance(times_arg, Sequence):
        raise ValueError("times must be a sequence")
    if len(times_arg) != len(samples):
        raise ValueError("times must align with samples")
    times: list[float] = []
    previous = None
    for index, value in enumerate(times_arg):
        current = _number(value, f"times[{index}]")
        if previous is not None and current < previous - 1e-9:
            raise ValueError("sample times are not monotone")
        times.append(current)
        previous = current
        contacts = samples[index].get("contacts")
        if isinstance(contacts, (str, bytes)) or not isinstance(contacts, Sequence):
            raise ValueError(f"sample[{index}].contacts must be a sequence")
        for contact in contacts:
            if isinstance(contact, (str, bytes)) or not isinstance(contact, Sequence) or len(contact) != 2:
                raise ValueError(f"sample[{index}] has an invalid contact pair")
            if not all(isinstance(name, str) and name for name in contact):
                raise ValueError(f"sample[{index}] has a non-name contact body")
    return samples, times


def _relative_duration(times: Sequence[float]) -> float:
    return float(times[-1] - times[0])


def _duration_metric(task_id: str, times: Sequence[float]) -> tuple[dict[str, Any], bool]:
    duration = _relative_duration(times)
    if task_id in {"H1-T02", "H1-T13"}:
        ok = abs(duration - DEADLINE_S) <= 1e-6
        threshold: Any = DEADLINE_S
        return _metric("task_duration_s", duration, threshold, ok, temporal="complete_deadline"), ok
    if task_id == "H1-T01":
        ok = SPEED_WINDOW_S[1] <= duration <= DEADLINE_S + 1e-6
        return _metric(
            "task_duration_s", duration, [SPEED_WINDOW_S[1], DEADLINE_S], ok,
            temporal="speed_window_and_deadline",
        ), ok
    # T04 and T14 use the catalog's 30 s value as a deadline.  Their final
    # window is relative to the controller's actual termination time.
    ok = FINAL_WINDOW_S <= duration <= DEADLINE_S + 1e-6
    return _metric(
        "task_duration_s", duration, [FINAL_WINDOW_S, DEADLINE_S], ok,
        temporal="termination_deadline",
    ), ok


def _final_indices(times: Sequence[float]) -> list[int]:
    end = float(times[-1])
    start = end - FINAL_WINDOW_S
    indices = [index for index, value in enumerate(times) if float(value) >= start - 1e-7]
    if not indices or float(times[indices[0]]) > start + 1e-6:
        return []
    return indices


def _window_indices(times: Sequence[float], start: float, end: float) -> list[int]:
    return [
        index for index, value in enumerate(times)
        if start - 1e-7 <= float(value) <= end + 1e-7
    ]


def _contacts(samples: list[Mapping[str, Any]], index: int) -> set[tuple[str, str]]:
    return {tuple(sorted((str(pair[0]), str(pair[1])))) for pair in samples[index]["contacts"]}


def _has_contact(pairs: set[tuple[str, str]], first: str, second: str) -> bool:
    return tuple(sorted((first, second))) in pairs


def _floor_contact(samples: list[Mapping[str, Any]], index: int, body: str) -> bool:
    return _has_contact(_contacts(samples, index), "world", body)


def _forbidden_floor_contacts(samples: list[Mapping[str, Any]], foot_bodies: set[str]) -> int:
    count = 0
    for index in range(len(samples)):
        forbidden = any(
            "world" in pair and next(name for name in pair if name != "world") not in foot_bodies
            for pair in _contacts(samples, index)
            if "world" in pair and pair[0] != pair[1]
        )
        count += int(forbidden)
    return count


def _tilt_degrees(samples: list[Mapping[str, Any]], indices: Sequence[int]) -> float:
    values = []
    for index in indices:
        rotation = _rotation(samples, TORSO_LABEL, index)
        values.append(math.degrees(math.acos(float(max(-1.0, min(1.0, rotation[8]))))))
    return max(values)


def _pelvis_clearance(samples: list[Mapping[str, Any]], indices: Sequence[int], support_z: float = 0.0) -> float:
    return min(_position(samples, PELVIS_LABEL, index)[2] - support_z for index in indices)


def _balance_metrics(
    samples: list[Mapping[str, Any]],
    final_indices: Sequence[int],
    *,
    forbidden_floor_contacts: int,
) -> list[dict[str, Any]]:
    complete = bool(final_indices)
    tilt = _tilt_degrees(samples, final_indices) if complete else None
    clearance = _pelvis_clearance(samples, final_indices) if complete else None
    return [
        _metric(
            "forbidden_contact_samples", forbidden_floor_contacts, 0,
            forbidden_floor_contacts == 0,
        ),
        _metric(
            "terminal_body_tilt", tilt, BODY_TILT_THRESHOLD_DEG,
            complete and tilt <= BODY_TILT_THRESHOLD_DEG,
        ),
        _metric(
            "terminal_pelvis_clearance", clearance, PELVIS_CLEARANCE_THRESHOLD_M,
            complete and clearance >= PELVIS_CLEARANCE_THRESHOLD_M,
            support_surface_z_m=0.0,
        ),
    ]


def _walk(
    samples: list[Mapping[str, Any]],
    times: list[float],
    duration_ok: bool,
) -> list[dict[str, Any]]:
    relative_times = [value - times[0] for value in times]
    speed_indices = _window_indices(relative_times, *SPEED_WINDOW_S)
    speed_coverage = bool(speed_indices and relative_times[speed_indices[0]] <= SPEED_WINDOW_S[0] + 1e-6
                          and relative_times[speed_indices[-1]] >= SPEED_WINDOW_S[1] - 1e-6)
    if speed_coverage:
        initial_rotation = _rotation(samples, PELVIS_LABEL, 0)
        heading = [initial_rotation[0], initial_rotation[3]]
        norm = math.hypot(*heading)
        if norm <= 1e-12:
            heading = [1.0, 0.0]
        else:
            heading = [heading[0] / norm, heading[1] / norm]
        speeds = [
            _linear_velocity(samples, PELVIS_LABEL, index)[0] * heading[0]
            + _linear_velocity(samples, PELVIS_LABEL, index)[1] * heading[1]
            for index in speed_indices
        ]
        rmse = math.sqrt(sum((value - WALKING_SPEED_M_S) ** 2 for value in speeds) / len(speeds))
    else:
        rmse = None

    transitions: dict[str, int] = {}
    support_all = bool(speed_indices)
    for foot in (LEFT_FOOT_BODY, RIGHT_FOOT_BODY):
        statuses = [_floor_contact(samples, index, foot) for index in speed_indices]
        support_all = support_all and all(
            _floor_contact(samples, index, LEFT_FOOT_BODY)
            or _floor_contact(samples, index, RIGHT_FOOT_BODY)
            for index in speed_indices
        )
        transitions[foot] = sum(
            int(not before and after)
            for before, after in zip(statuses, statuses[1:], strict=False)
        )
    operation_ok = (
        duration_ok and speed_coverage and support_all
        and all(value >= 3 for value in transitions.values())
    )
    final = _final_indices(times)
    metrics = [
        _metric("walking_speed_rmse", rmse, WALKING_SPEED_RMSE_THRESHOLD_M_S,
                speed_coverage and rmse <= WALKING_SPEED_RMSE_THRESHOLD_M_S),
        _metric("completed_operation", int(operation_ok), 1, operation_ok,
                swing_to_stance_transitions=transitions,
                support_all_samples=support_all),
    ]
    metrics.extend(_balance_metrics(
        samples, final,
        forbidden_floor_contacts=_forbidden_floor_contacts(
            samples, {LEFT_FOOT_BODY, RIGHT_FOOT_BODY}
        ),
    ))
    return metrics


def _stand(
    samples: list[Mapping[str, Any]],
    times: list[float],
    duration_ok: bool,
) -> list[dict[str, Any]]:
    initial = _position(samples, PELVIS_LABEL, 0)
    drift = max(
        math.hypot(_position(samples, PELVIS_LABEL, index)[0] - initial[0],
                   _position(samples, PELVIS_LABEL, index)[1] - initial[1])
        for index in range(len(samples))
    )
    support = all(
        _floor_contact(samples, index, LEFT_FOOT_BODY)
        and _floor_contact(samples, index, RIGHT_FOOT_BODY)
        for index in range(len(samples))
    )
    final = _final_indices(times)
    metrics = [
        _metric("standing_planar_drift", drift, STANDING_DRIFT_THRESHOLD_M,
                duration_ok and drift <= STANDING_DRIFT_THRESHOLD_M),
        _metric("completed_operation", int(duration_ok and support), 1,
                duration_ok and support,
                both_feet_supported_throughout=support),
    ]
    metrics.extend(_balance_metrics(
        samples, final,
        forbidden_floor_contacts=_forbidden_floor_contacts(
            samples, {LEFT_FOOT_BODY, RIGHT_FOOT_BODY}
        ),
    ))
    return metrics


def _reach(
    spec: Mapping[str, Any],
    parameters: Mapping[str, Any],
    samples: list[Mapping[str, Any]],
    times: list[float],
    duration_ok: bool,
) -> list[dict[str, Any]]:
    target = _vector(parameters.get("target_position_m"), 3, "parameters.target_position_m")
    spec_target = spec.get("target_position_m")
    if spec_target is not None and _distance(target, _vector(spec_target, 3, "success_spec.target_position_m")) > 1e-9:
        raise ValueError("success_spec.target_position_m disagrees with parameters.target_position_m")
    final = _final_indices(times)
    errors = []
    for index in final:
        errors.append(_distance(_position(samples, "forearm", index), target))
    error = max(errors) if errors else None
    metrics = [
        _metric("forearm_target_error", error, FOREARM_ERROR_THRESHOLD_M,
                duration_ok and bool(errors) and error <= FOREARM_ERROR_THRESHOLD_M),
    ]
    metrics.extend(_balance_metrics(
        samples, final,
        forbidden_floor_contacts=_forbidden_floor_contacts(
            samples, {LEFT_FOOT_BODY, RIGHT_FOOT_BODY}
        ),
    ))
    return metrics


def _push(
    spec: Mapping[str, Any],
    parameters: Mapping[str, Any],
    samples: list[Mapping[str, Any]],
    times: list[float],
    duration_ok: bool,
) -> list[dict[str, Any]]:
    goal = _vector(parameters.get("goal_xy_m"), 2, "parameters.goal_xy_m")
    spec_goal = spec.get("goal_xy_m")
    if spec_goal is not None and _distance(goal, _vector(spec_goal, 2, "success_spec.goal_xy_m")) > 1e-9:
        raise ValueError("success_spec.goal_xy_m disagrees with parameters.goal_xy_m")
    final = _final_indices(times)
    errors = [
        math.hypot(_position(samples, "cube", index)[0] - goal[0],
                   _position(samples, "cube", index)[1] - goal[1])
        for index in final
    ]
    pairs_by_sample = [_contacts(samples, index) for index in range(len(samples))]
    forearm_contact = any(_has_contact(pairs, LEFT_ELBOW_BODY, "push_cube") for pairs in pairs_by_sample)
    table_supported_at_deadline = bool(pairs_by_sample and _has_contact(pairs_by_sample[-1], "push_cube", "push_table"))
    operation_ok = duration_ok and forearm_contact and table_supported_at_deadline
    metrics = [
        _metric("completed_operation", int(operation_ok), 1, operation_ok,
                forearm_cube_contact_seen=forearm_contact,
                cube_table_contact_at_deadline=table_supported_at_deadline),
        _metric("object_goal_planar_error", max(errors) if errors else None,
                OBJECT_GOAL_ERROR_THRESHOLD_M,
                duration_ok and bool(errors) and max(errors) <= OBJECT_GOAL_ERROR_THRESHOLD_M),
    ]
    # H1-T13's maintained catalog has four criteria: operation completion,
    # object goal error, torso tilt, and pelvis clearance. It does not add a
    # non-foot floor-contact clause, so omit that locomotion-only metric.
    balance = _balance_metrics(
        samples, final,
        forbidden_floor_contacts=_forbidden_floor_contacts(
            samples, {LEFT_FOOT_BODY, RIGHT_FOOT_BODY}
        ),
    )
    metrics.extend(metric for metric in balance if metric["check"] != "forbidden_contact_samples")
    return metrics


def _button(
    spec: Mapping[str, Any],
    parameters: Mapping[str, Any],
    samples: list[Mapping[str, Any]],
    times: list[float],
    duration_ok: bool,
) -> list[dict[str, Any]]:
    target_index = parameters.get("target_index")
    if isinstance(target_index, bool) or not isinstance(target_index, int) or target_index not in (0, 1, 2):
        raise ValueError("parameters.target_index must be an integer in [0, 2]")
    spec_index = spec.get("target_index")
    if spec_index is not None and spec_index != target_index:
        raise ValueError("success_spec.target_index disagrees with parameters.target_index")
    selected = f"button_target_{target_index}"
    nonselected = {f"button_target_{index}" for index in range(3) if index != target_index}
    final = _final_indices(times)
    # This operation event is valid anywhere in the complete execution:
    # either existing distal forearm may touch the selected sphere, including
    # an early contact before the controller terminates.
    selected_contact = any(
        any(_has_contact(_contacts(samples, index), forearm, selected)
            for forearm in FOREARM_BODIES)
        for index in range(len(samples))
    )
    forbidden = sum(
        int(any(
            any(body in pair for body in nonselected)
            for pair in _contacts(samples, index)
        ))
        for index in range(len(samples))
    )
    operation_ok = duration_ok and selected_contact
    metrics = [
        _metric("completed_operation", int(operation_ok), 1, operation_ok,
                selected_target=selected, selected_contact_seen=selected_contact),
        _metric("forbidden_contact_samples", forbidden, 0, forbidden == 0),
    ]
    # The shared balance helper also emits the catalog's non-target floor
    # contact clause; keep the button contact clause above separate.
    balance = _balance_metrics(
        samples, final,
        forbidden_floor_contacts=_forbidden_floor_contacts(
            samples, {LEFT_FOOT_BODY, RIGHT_FOOT_BODY}
        ),
    )
    metrics.extend(metric for metric in balance if metric["check"] != "forbidden_contact_samples")
    return metrics


def evaluate_h1_task(
    spec: Mapping[str, Any],
    parameters: Mapping[str, Any],
    samples: Sequence[Mapping[str, Any]],
    times: Sequence[float],
) -> list[dict[str, Any]]:
    """Evaluate one selected H1 task from a native physical trace."""

    spec_map = _mapping(spec, "success_spec")
    task_id = spec_map.get("task_id")
    if task_id not in TASKS:
        raise ValueError(f"unknown H1 catalog task {task_id!r}")
    parameters_map = _mapping(parameters, "parameters")
    sample_list, time_list = _validate_trace(samples, times)
    duration_metric, duration_ok = _duration_metric(task_id, time_list)
    if task_id == "H1-T01":
        metrics = _walk(sample_list, time_list, duration_ok)
    elif task_id == "H1-T02":
        metrics = _stand(sample_list, time_list, duration_ok)
    elif task_id == "H1-T04":
        metrics = _reach(spec_map, parameters_map, sample_list, time_list, duration_ok)
    elif task_id == "H1-T13":
        metrics = _push(spec_map, parameters_map, sample_list, time_list, duration_ok)
    else:
        metrics = _button(spec_map, parameters_map, sample_list, time_list, duration_ok)
    return [duration_metric, *metrics]


__all__ = ["evaluate_h1_task"]
