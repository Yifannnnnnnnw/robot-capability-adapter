"""Trusted corrected-R1 task metrics over recorded MuJoCo samples.

These are deliberately small pure adapters, not a second Harness runner.  They
consume the same sample/contact objects already emitted by
``TrackedMuJoCoSession`` and return JSON-ready metric evidence for the isolated
corrected audit.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
import math
from typing import Any


GO2_EDGE_FINISH_X_M = 0.422
GO2_T16_START_DISPLACEMENT_M = 0.7
GO2_T16_TARGET_DISTANCE_M = 0.4
GO2_T16_DWELL_S = 5.0
GO2_T17_WAYPOINT_TOLERANCE_M = 0.12
GO2_T17_MINIMUM_CLEARANCE_M = 0.1
SO101_GOAL_DISTANCE_M = 0.07


def evaluate_edge_completion(
    samples: Sequence[Mapping[str, Any]],
    *,
    body_names: Sequence[str] = ("FL_foot", "FR_foot", "RL_foot", "RR_foot"),
    finish_x_m: float = GO2_EDGE_FINISH_X_M,
) -> dict[str, Any]:
    """Pass when all four foot centres reach the corrected physical edge."""

    checked = _samples(samples)
    names = _names(body_names, label="foot body_names")
    finish = _positive_finite(finish_x_m, label="finish_x_m")
    completed_index: int | None = None
    coordinates: dict[str, float] = {}
    for index, sample in enumerate(checked):
        coordinates = {name: _body_position(sample, name)[0] for name in names}
        if all(value + 1.0e-12 >= finish for value in coordinates.values()):
            completed_index = index
            break
    if completed_index is None:
        coordinates = {
            name: _body_position(checked[-1], name)[0] for name in names
        }
    return {
        "kind": "corrected_all_feet_edge_completion",
        "passed": completed_index is not None,
        "finish_x_m": finish,
        "foot_center_x_m": coordinates,
        "minimum_foot_center_x_m": min(coordinates.values()),
        "completion_sample_index": completed_index,
        "completion_time_s": (
            _time(checked[completed_index]) if completed_index is not None else None
        ),
    }


def evaluate_t06_course(
    samples: Sequence[Mapping[str, Any]],
    contact_pair_step_counts: Sequence[Mapping[str, Any]],
    *,
    body_name: str,
    gates: Sequence[Mapping[str, Any]],
    robot_geom_names: Collection[str],
    required_contact_geom_groups: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Require ordered far-edge gates and physical contact with every family."""

    checked = _samples(samples)
    gate_list = _gates(gates)
    completed = 0
    completion_times: list[float] = []
    for sample in checked:
        if completed == len(gate_list):
            break
        axis, coordinate, direction = gate_list[completed]
        value = _body_position(sample, body_name)[axis]
        crossed = value + 1.0e-12 >= coordinate if direction > 0 else value - 1.0e-12 <= coordinate
        if crossed:
            completed += 1
            completion_times.append(_time(sample))

    robot_geoms = set(_names(tuple(robot_geom_names), label="robot_geom_names"))
    contacts = _contact_step_records(contact_pair_step_counts)
    family_results: list[dict[str, Any]] = []
    for group in required_contact_geom_groups:
        if not isinstance(group, Mapping):
            raise ValueError("required contact groups must be objects")
        family_id = group.get("family_id")
        geom_names = group.get("geom_names")
        if not isinstance(family_id, str) or not family_id:
            raise ValueError("required contact family_id must be non-empty")
        if not isinstance(geom_names, Sequence) or isinstance(geom_names, (str, bytes)):
            raise ValueError("required contact geom_names must be a sequence")
        task_geoms = set(_names(tuple(geom_names), label=f"{family_id}.geom_names"))
        contact_steps = sum(
            step_count
            for geom1, geom2, step_count in contacts
            if (geom1 in robot_geoms and geom2 in task_geoms)
            or (geom2 in robot_geoms and geom1 in task_geoms)
        )
        family_results.append(
            {
                "family_id": family_id,
                "geom_names": sorted(task_geoms),
                "contact_steps": contact_steps,
                "passed": contact_steps > 0,
            }
        )

    gate_ratio = completed / len(gate_list)
    contacts_passed = bool(family_results) and all(
        item["passed"] for item in family_results
    )
    return {
        "kind": "corrected_ordered_gate_and_contact_course",
        "passed": completed == len(gate_list) and contacts_passed,
        "ordered_gate_completion_ratio": gate_ratio,
        "completed_gate_count": completed,
        "required_gate_count": len(gate_list),
        "gate_completion_times_s": completion_times,
        "contact_requirements_passed": contacts_passed,
        "contact_family_results": family_results,
    }


def evaluate_t16_table_transfer(
    samples: Sequence[Mapping[str, Any]],
    *,
    body_name: str,
    target_position_m: Sequence[float],
    start_displacement_m: float = GO2_T16_START_DISPLACEMENT_M,
    target_distance_m: float = GO2_T16_TARGET_DISTANCE_M,
    dwell_duration_s: float = GO2_T16_DWELL_S,
) -> dict[str, Any]:
    """Evaluate planar step-off and continuous planar end-table dwell."""

    checked = _samples(samples)
    target = _vector(target_position_m, minimum_size=2, label="target_position_m")[:2]
    start_required = _positive_finite(
        start_displacement_m, label="start_displacement_m"
    )
    target_limit = _positive_finite(target_distance_m, label="target_distance_m")
    dwell_required = _positive_finite(dwell_duration_s, label="dwell_duration_s")
    positions = [_body_position(sample, body_name)[:2] for sample in checked]
    start = positions[0]
    start_distances = [_distance(position, start) for position in positions]
    target_distances = [_distance(position, target) for position in positions]
    inside = [distance <= target_limit + 1.0e-12 for distance in target_distances]
    longest_dwell = _longest_contiguous_duration(checked, inside)
    start_passed = max(start_distances) + 1.0e-12 >= start_required
    dwell_passed = longest_dwell + 1.0e-12 >= dwell_required
    return {
        "kind": "corrected_planar_table_transfer",
        "passed": start_passed and dwell_passed,
        "start_displacement_passed": start_passed,
        "maximum_start_table_planar_displacement_m": max(start_distances),
        "required_start_displacement_m": start_required,
        "end_table_dwell_passed": dwell_passed,
        "longest_end_table_dwell_s": longest_dwell,
        "required_end_table_dwell_s": dwell_required,
        "target_distance_limit_m": target_limit,
        "terminal_target_planar_distance_m": target_distances[-1],
    }


def evaluate_t17_weave(
    samples: Sequence[Mapping[str, Any]],
    *,
    body_name: str,
    waypoints: Sequence[Sequence[float]],
    pole_points: Sequence[Sequence[float]],
    tolerance_m: float = GO2_T17_WAYPOINT_TOLERANCE_M,
    minimum_clearance_m: float = GO2_T17_MINIMUM_CLEARANCE_M,
) -> dict[str, Any]:
    """Require the five-waypoint alternating weave and all-sample clearance."""

    checked = _samples(samples)
    points = [
        _vector(point, minimum_size=2, label="waypoint")[:2]
        for point in waypoints
    ]
    poles = [
        _vector(point, minimum_size=2, label="pole point")[:2]
        for point in pole_points
    ]
    if not points or not poles:
        raise ValueError("T17 requires non-empty waypoints and pole_points")
    tolerance = _positive_finite(tolerance_m, label="tolerance_m")
    clearance_required = _positive_finite(
        minimum_clearance_m, label="minimum_clearance_m"
    )
    completed = 0
    completion_times: list[float] = []
    positions: list[tuple[float, float]] = []
    for sample in checked:
        position = _body_position(sample, body_name)[:2]
        positions.append(position)
        if completed < len(points) and _distance(position, points[completed]) <= tolerance + 1.0e-12:
            completed += 1
            completion_times.append(_time(sample))
    minimum_clearance = min(
        _distance(position, pole) for position in positions for pole in poles
    )
    ratio = completed / len(points)
    clearance_passed = minimum_clearance + 1.0e-12 >= clearance_required
    return {
        "kind": "corrected_ordered_weave_and_clearance",
        "passed": completed == len(points) and clearance_passed,
        "ordered_waypoint_completion_ratio": ratio,
        "completed_waypoint_count": completed,
        "required_waypoint_count": len(points),
        "waypoint_completion_times_s": completion_times,
        "minimum_torso_pole_clearance_m": minimum_clearance,
        "required_minimum_clearance_m": clearance_required,
        "clearance_passed": clearance_passed,
    }


def evaluate_terminal_body_goal(
    samples: Sequence[Mapping[str, Any]],
    *,
    body_name: str,
    target_position_m: Sequence[float],
    maximum_distance_m: float = SO101_GOAL_DISTANCE_M,
) -> dict[str, Any]:
    checked = _samples(samples)
    target = _vector(target_position_m, minimum_size=3, label="target_position_m")[:3]
    actual = _body_position(checked[-1], body_name)
    return _terminal_goal_result(
        kind="corrected_terminal_body_goal_distance",
        actual=actual,
        target=target,
        maximum_distance_m=maximum_distance_m,
    )


def evaluate_terminal_site_goal(
    samples: Sequence[Mapping[str, Any]],
    *,
    site_name: str,
    target_position_m: Sequence[float],
    maximum_distance_m: float = SO101_GOAL_DISTANCE_M,
) -> dict[str, Any]:
    checked = _samples(samples)
    target = _vector(target_position_m, minimum_size=3, label="target_position_m")[:3]
    actual = _site_position(checked[-1], site_name)
    return _terminal_goal_result(
        kind="corrected_terminal_site_goal_distance",
        actual=actual,
        target=target,
        maximum_distance_m=maximum_distance_m,
    )


def evaluate_corrected_task_metric(
    task_id: str,
    *,
    physical_evidence: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """Dispatch one corrected task from resolved trusted-side parameters.

    Public target arguments must already be resolved into ``parameters``.  This
    keeps task-suite parsing outside this module and avoids duplicating the B2
    runner or exposing private thresholds to the controller.
    """

    samples = physical_evidence.get("samples")
    if not isinstance(samples, list):
        raise ValueError("physical evidence has no samples")
    upper = task_id.upper()
    if upper in {"GO2-T02", "GO2-T03"}:
        result = evaluate_edge_completion(
            samples,
            body_names=parameters.get(
                "body_names", ("FL_foot", "FR_foot", "RL_foot", "RR_foot")
            ),
            finish_x_m=float(parameters.get("finish_x_m", GO2_EDGE_FINISH_X_M)),
        )
    elif upper == "GO2-T06":
        records = physical_evidence.get("contact_pair_step_counts")
        if not isinstance(records, list):
            raise ValueError("T06 physical evidence has no contact step records")
        result = evaluate_t06_course(
            samples,
            records,
            body_name=str(parameters["body_name"]),
            gates=parameters["gates"],
            robot_geom_names=parameters["robot_geom_names"],
            required_contact_geom_groups=parameters[
                "required_contact_geom_groups"
            ],
        )
    elif upper == "GO2-T16":
        result = evaluate_t16_table_transfer(
            samples,
            body_name=str(parameters["body_name"]),
            target_position_m=parameters["target_position_m"],
            start_displacement_m=float(
                parameters.get(
                    "start_displacement_m", GO2_T16_START_DISPLACEMENT_M
                )
            ),
            target_distance_m=float(
                parameters.get("target_distance_m", GO2_T16_TARGET_DISTANCE_M)
            ),
            dwell_duration_s=float(
                parameters.get("dwell_duration_s", GO2_T16_DWELL_S)
            ),
        )
    elif upper == "GO2-T17":
        result = evaluate_t17_weave(
            samples,
            body_name=str(parameters["body_name"]),
            waypoints=parameters["waypoints"],
            pole_points=parameters["pole_points"],
            tolerance_m=float(
                parameters.get("tolerance_m", GO2_T17_WAYPOINT_TOLERANCE_M)
            ),
            minimum_clearance_m=float(
                parameters.get(
                    "minimum_clearance_m", GO2_T17_MINIMUM_CLEARANCE_M
                )
            ),
        )
    elif task_id == "mw_pick_place_wall":
        result = evaluate_terminal_body_goal(
            samples,
            body_name=str(parameters.get("body_name", "workpiece")),
            target_position_m=parameters["target_position_m"],
            maximum_distance_m=float(
                parameters.get("maximum_distance_m", SO101_GOAL_DISTANCE_M)
            ),
        )
    elif task_id == "mw_dial_turn":
        result = evaluate_terminal_site_goal(
            samples,
            site_name=str(parameters.get("site_name", "dial_tip_site")),
            target_position_m=parameters["target_position_m"],
            maximum_distance_m=float(
                parameters.get("maximum_distance_m", SO101_GOAL_DISTANCE_M)
            ),
        )
    else:
        raise ValueError(f"unsupported corrected task metric {task_id!r}")
    return {"task_id": task_id, **result}


def _terminal_goal_result(
    *,
    kind: str,
    actual: Sequence[float],
    target: Sequence[float],
    maximum_distance_m: float,
) -> dict[str, Any]:
    maximum = _positive_finite(maximum_distance_m, label="maximum_distance_m")
    distance = _distance(actual, target)
    return {
        "kind": kind,
        "passed": distance <= maximum + 1.0e-12,
        "terminal_position_m": list(actual),
        "target_position_m": list(target),
        "terminal_goal_distance_m": distance,
        "maximum_goal_distance_m": maximum,
    }


def _samples(
    samples: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    if isinstance(samples, (str, bytes)) or not samples:
        raise ValueError("metric evaluation requires non-empty samples")
    checked: list[Mapping[str, Any]] = []
    previous_time: float | None = None
    for sample in samples:
        if not isinstance(sample, Mapping):
            raise ValueError("metric samples must be objects")
        current_time = _time(sample)
        if previous_time is not None and current_time <= previous_time:
            raise ValueError("metric sample times must be strictly increasing")
        previous_time = current_time
        checked.append(sample)
    return tuple(checked)


def _time(sample: Mapping[str, Any]) -> float:
    return _finite(sample.get("time"), label="sample time")


def _body_position(sample: Mapping[str, Any], body_name: str) -> tuple[float, ...]:
    positions = sample.get("body_positions")
    if not isinstance(positions, Mapping) or body_name not in positions:
        raise ValueError(f"sample has no body position for {body_name!r}")
    return _vector(positions[body_name], minimum_size=3, label=body_name)[:3]


def _site_position(sample: Mapping[str, Any], site_name: str) -> tuple[float, ...]:
    positions = sample.get("site_positions")
    if not isinstance(positions, Mapping) or site_name not in positions:
        raise ValueError(f"sample has no site position for {site_name!r}")
    return _vector(positions[site_name], minimum_size=3, label=site_name)[:3]


def _vector(value: Any, *, minimum_size: int, label: str) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < minimum_size:
        raise ValueError(f"{label} must contain at least {minimum_size} numbers")
    return tuple(_finite(item, label=label) for item in value)


def _names(values: Sequence[str], *, label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not values or not all(
        isinstance(value, str) and value for value in values
    ):
        raise ValueError(f"{label} must be non-empty names")
    return tuple(values)


def _gates(
    gates: Sequence[Mapping[str, Any]],
) -> tuple[tuple[int, float, int], ...]:
    if isinstance(gates, (str, bytes)) or not gates:
        raise ValueError("ordered course requires gates")
    checked: list[tuple[int, float, int]] = []
    for gate in gates:
        if not isinstance(gate, Mapping):
            raise ValueError("gate definitions must be objects")
        axis = int(gate.get("axis", 0))
        direction = int(gate.get("direction", 1))
        coordinate = _finite(gate.get("coordinate"), label="gate coordinate")
        if axis not in {0, 1, 2} or direction not in {-1, 1}:
            raise ValueError("gate axis/direction is invalid")
        checked.append((axis, coordinate, direction))
    return tuple(checked)


def _contact_step_records(
    records: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, str, int], ...]:
    checked: list[tuple[str, str, int]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("contact step records must be objects")
        geom1 = record.get("geom1")
        geom2 = record.get("geom2")
        step_count = record.get("step_count")
        if (
            not isinstance(geom1, str)
            or not geom1
            or not isinstance(geom2, str)
            or not geom2
            or isinstance(step_count, bool)
            or not isinstance(step_count, int)
            or step_count < 0
        ):
            raise ValueError("contact step record is invalid")
        checked.append((geom1, geom2, step_count))
    return tuple(checked)


def _longest_contiguous_duration(
    samples: Sequence[Mapping[str, Any]], states: Sequence[bool]
) -> float:
    if len(samples) != len(states):
        raise ValueError("sample/state lengths differ")
    best = 0.0
    current = 0.0
    for index in range(len(samples) - 1):
        if states[index] and states[index + 1]:
            current += _time(samples[index + 1]) - _time(samples[index])
            best = max(best, current)
        else:
            current = 0.0
    return best


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("distance vectors have different dimensions")
    return math.sqrt(math.fsum((a - b) ** 2 for a, b in zip(left, right)))


def _finite(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{label} must be finite")
    return converted


def _positive_finite(value: Any, *, label: str) -> float:
    converted = _finite(value, label=label)
    if converted <= 0.0:
        raise ValueError(f"{label} must be positive")
    return converted
