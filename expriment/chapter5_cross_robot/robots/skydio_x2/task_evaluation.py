"""Physical verdicts for the five selected Skydio X2 catalog tasks.

The evaluator consumes only the native MuJoCo trace passed by
``auto_adapter.demo_evaluation.evaluate_demo_task``.  Controller return values,
method names and model text are never used as task evidence.  The selected
criteria are the maintained local catalog's proposed, uncalibrated numerical
requirements; the catalog's source pages motivate operation families only.

``DemoTrace`` exposes the X2 body position, orientation, velocity and solver
contacts.  It does not expose actuator controls, so the X2-T02 landing family
is intentionally outside this module rather than receiving a thrust proxy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from numbers import Real
from typing import Any


_TASKS = {"X2-T01", "X2-T03", "X2-T04", "X2-T05", "X2-T07"}
_BODY_LABEL = "base"
_BODY_NAME = "x2"
_TERMINAL_WINDOW_S = 2.0
_MAX_DURATION_S = 60.0
_EPS = 1e-7


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
    return [_number(item, name) for item in value]


def _metric(check: str, value: Any, threshold: Any, ok: bool, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "check": check,
        "value": value,
        "threshold": threshold,
        "ok": bool(ok),
    }
    item.update(extra)
    return item


def _position(samples: list[Mapping[str, Any]], index: int) -> list[float]:
    return _vector(
        samples[index]["state"][_BODY_LABEL]["position"],
        3,
        f"sample[{index}].base.position",
    )


def _rotation(samples: list[Mapping[str, Any]], index: int) -> list[float]:
    return _vector(
        samples[index]["state"][_BODY_LABEL]["rotation"],
        9,
        f"sample[{index}].base.rotation",
    )


def _linear_velocity(samples: list[Mapping[str, Any]], index: int) -> list[float]:
    return _vector(
        samples[index]["state"][_BODY_LABEL]["linear_velocity"],
        3,
        f"sample[{index}].base.linear_velocity",
    )


def _distance(first: Sequence[float], second: Sequence[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(first, second)))


def _norm(values: Sequence[float]) -> float:
    return math.sqrt(sum(float(value) ** 2 for value in values))


def _wrap_angle(angle: float) -> float:
    return (float(angle) + math.pi) % (2.0 * math.pi) - math.pi


def _yaw(rotation: Sequence[float]) -> float:
    # Row-major xmat: body +x in world is the first column (R00, R10).
    return math.atan2(float(rotation[3]), float(rotation[0]))


def _body_tilt_deg(rotation: Sequence[float]) -> float:
    # Body +z in world is the third column; R22 is its world-z component.
    z_component = max(-1.0, min(1.0, float(rotation[8])))
    return math.degrees(math.acos(z_component))


def _duration(times: Sequence[float]) -> float:
    if len(times) < 2:
        raise ValueError("at least two samples are required")
    duration = _number(times[-1], "times[-1]") - _number(times[0], "times[0]")
    if duration < -_EPS:
        raise ValueError("sample times are not monotone")
    return max(0.0, duration)


def _duration_metric(times: Sequence[float], *, minimum: float = 0.0,
                     maximum: float = _MAX_DURATION_S) -> dict[str, Any]:
    duration = _duration(times)
    return _metric(
        "task_duration_s",
        duration,
        [minimum, maximum],
        duration + _EPS >= minimum and duration <= maximum + _EPS,
    )


def _final_window(times: Sequence[float], duration: float = _TERMINAL_WINDOW_S) -> list[int] | None:
    """Return every sample in the complete final window, or ``None``.

    A terminal sample at the requested endpoint is insufficient evidence for a
    final-window criterion.  The returned interval must cover the full
    requested duration in the native trace.
    """

    if not times:
        return None
    end = _number(times[-1], "times[-1]")
    start = end - duration
    if end - _number(times[0], "times[0]") < duration - _EPS:
        return None
    indices = [index for index, time in enumerate(times)
               if _number(time, f"times[{index}]") >= start - _EPS]
    if not indices or end - _number(times[indices[0]], f"times[{indices[0]}]") < duration - _EPS:
        return None
    return indices


def _final_window_metric(times: Sequence[float], indices: list[int] | None,
                         duration: float = _TERMINAL_WINDOW_S) -> dict[str, Any]:
    observed = None if indices is None else _number(times[-1], "times[-1]") - _number(
        times[indices[0]], f"times[{indices[0]}]"
    )
    return _metric(
        "terminal_window_covered_s",
        observed,
        duration,
        indices is not None,
    )


def _contact_pairs(sample: Mapping[str, Any]) -> Sequence[Sequence[str]]:
    contacts = sample.get("contacts")
    if isinstance(contacts, (str, bytes)) or not isinstance(contacts, Sequence):
        raise ValueError("sample.contacts must be a sequence")
    return contacts


def _robot_contact(sample: Mapping[str, Any]) -> bool:
    return any(_BODY_NAME in pair for pair in _contact_pairs(sample))


def _floor_contact(sample: Mapping[str, Any]) -> bool:
    # DemoTrace records the world body as ``world`` (older MuJoCo builds may
    # expose it as body#0); both identify the floor's world-side contact.
    return any(
        _BODY_NAME in pair and ("world" in pair or "body#0" in pair)
        for pair in _contact_pairs(sample)
    )


def _terminal_motion_metrics(
    samples: list[Mapping[str, Any]],
    times: list[float],
    target: Sequence[float],
    *,
    spec: Mapping[str, Any],
    include_speed: bool = True,
) -> list[dict[str, Any]]:
    terminal_window = _number(spec.get("terminal_window_s", _TERMINAL_WINDOW_S),
                              "success_spec.terminal_window_s")
    position_limit = _number(spec.get("position_threshold_m", 0.15),
                             "success_spec.position_threshold_m")
    speed_limit = _number(spec.get("translation_speed_threshold_m_s", 0.1),
                          "success_spec.translation_speed_threshold_m_s")
    tilt_limit = _number(spec.get("body_tilt_threshold_deg", 20.0),
                         "success_spec.body_tilt_threshold_deg")
    indices = _final_window(times, terminal_window)
    metrics = [_final_window_metric(times, indices, terminal_window)]
    if indices is None:
        metrics.append(_metric("terminal_position_error_m", None, position_limit, False))
        if include_speed:
            metrics.append(_metric("terminal_translation_speed_m_s", None, speed_limit, False))
        metrics.append(_metric("terminal_body_tilt_deg", None, tilt_limit, False))
        return metrics
    position_error = max(_distance(_position(samples, i), target) for i in indices)
    metrics.append(_metric(
        "terminal_position_error_m", position_error, position_limit,
        position_error <= position_limit + _EPS,
    ))
    if include_speed:
        speed = max(_norm(_linear_velocity(samples, i)) for i in indices)
        metrics.append(_metric(
            "terminal_translation_speed_m_s", speed, speed_limit,
            speed <= speed_limit + _EPS,
        ))
    tilt = max(_body_tilt_deg(_rotation(samples, i)) for i in indices)
    metrics.append(_metric("terminal_body_tilt_deg", tilt, tilt_limit,
                           tilt <= tilt_limit + _EPS))
    return metrics


def _forbidden_contacts(samples: list[Mapping[str, Any]]) -> dict[str, Any]:
    count = sum(_robot_contact(sample) for sample in samples)
    return _metric("forbidden_contact_samples", count, 0, count == 0)


def _t01(spec: Mapping[str, Any], samples: list[Mapping[str, Any]], times: list[float]) -> list[dict[str, Any]]:
    target = _vector(spec.get("holding_point_world_m"), 3, "success_spec.holding_point_world_m")
    deadline = _number(spec.get("ground_contact_deadline_s", 5.0),
                       "success_spec.ground_contact_deadline_s")
    duration = _duration(times)
    floor_before = sum(_floor_contact(sample) and time <= deadline + _EPS
                       for sample, time in zip(samples, times, strict=True))
    floor_after = sum(_floor_contact(sample) and time > deadline + _EPS
                      for sample, time in zip(samples, times, strict=True))
    completed = bool(floor_before > 0 and floor_after == 0)
    metrics = [
        _duration_metric(times),
        _metric("ground_contact_samples_before_deadline", floor_before, 1, floor_before > 0),
        _metric("ground_contact_samples_after_deadline", floor_after, 0, floor_after == 0),
        _metric("completed_operation", int(completed), 1, completed),
    ]
    metrics.extend(_terminal_motion_metrics(samples, times, target, spec=spec))
    return metrics


def _t03(spec: Mapping[str, Any], samples: list[Mapping[str, Any]], times: list[float]) -> list[dict[str, Any]]:
    initial_position = _position(samples, 0)
    initial_heading = _yaw(_rotation(samples, 0))
    duration = _duration(times)
    hold_duration = _number(spec.get("hold_duration_s", _MAX_DURATION_S),
                            "success_spec.hold_duration_s")
    position_limit = _number(spec.get("position_threshold_m", 0.15),
                             "success_spec.position_threshold_m")
    heading_limit = _number(spec.get("heading_threshold_deg", 10.0),
                            "success_spec.heading_threshold_deg")
    tilt_limit = _number(spec.get("body_tilt_threshold_deg", 20.0),
                         "success_spec.body_tilt_threshold_deg")
    horizon_ok = duration + _EPS >= hold_duration
    maximum_position_error = max(_distance(initial_position, _position(samples, i))
                                 for i in range(len(samples)))
    maximum_heading_error = max(
        math.degrees(abs(_wrap_angle(_yaw(_rotation(samples, i)) - initial_heading)))
        for i in range(len(samples))
    )
    terminal_window = _number(spec.get("terminal_window_s", _TERMINAL_WINDOW_S),
                              "success_spec.terminal_window_s")
    indices = _final_window(times, terminal_window)
    metrics = [
        _duration_metric(times, minimum=hold_duration),
        _metric("hold_horizon_covered_s", duration, hold_duration, horizon_ok),
        _metric("maximum_hold_position_error_m", maximum_position_error, position_limit,
                horizon_ok and maximum_position_error <= position_limit + _EPS),
        _metric("maximum_hold_heading_error_deg", maximum_heading_error, heading_limit,
                horizon_ok and maximum_heading_error <= heading_limit + _EPS),
        _final_window_metric(times, indices, terminal_window),
    ]
    if indices is None:
        metrics.append(_metric("terminal_body_tilt_deg", None, tilt_limit, False))
    else:
        tilt = max(_body_tilt_deg(_rotation(samples, i)) for i in indices)
        metrics.append(_metric("terminal_body_tilt_deg", tilt, tilt_limit,
                               tilt <= tilt_limit + _EPS))
    metrics.append(_forbidden_contacts(samples))
    return metrics


def _t04(spec: Mapping[str, Any], parameters: Mapping[str, Any],
         samples: list[Mapping[str, Any]], times: list[float]) -> list[dict[str, Any]]:
    home = _vector(parameters.get("home_xy_m"), 2, "parameters.home_xy_m")
    start = _position(samples, 0)
    delta = [home[i] - start[i] for i in range(2)]
    separation = _norm(delta)
    if separation <= 0.2 + _EPS:
        raise ValueError("parameters.home_xy_m must be more than 0.2 m from the initial position")
    direction = [value / separation for value in delta]
    positions = [_position(samples, i) for i in range(len(samples))]
    heights = [position[2] for position in positions]
    horizontal_distances = [math.dist(position[:2], home) for position in positions]
    toward_home = [max(0.0, sum((position[i] - start[i]) * direction[i] for i in range(2)))
                   for position in positions]
    transit_altitude = _number(spec.get("transit_altitude_m", 1.9),
                               "success_spec.transit_altitude_m")
    transit_clearance = _number(spec.get("transit_clearance_m", 1.8),
                                "success_spec.transit_clearance_m")
    home_tolerance = _number(spec.get("home_horizontal_tolerance_m", 0.2),
                             "success_spec.home_horizontal_tolerance_m")
    high_indices = [i for i, height in enumerate(heights)
                    if height >= transit_altitude - _EPS]
    first_high = high_indices[0] if high_indices else None
    pre_high_ok = bool(first_high is not None and
                       max(toward_home[:first_high + 1], default=math.inf) <= 0.3 + _EPS)
    near_indices = ([] if first_high is None else
                    [i for i in range(first_high, len(samples))
                     if horizontal_distances[i] <= home_tolerance + _EPS])
    first_near = near_indices[0] if near_indices else None
    high_corridor_ok = bool(
        first_near is not None
        and all(height >= transit_clearance - _EPS
                for height in heights[first_high:first_near + 1])
    ) if first_high is not None else False
    descended_after = bool(
        first_near is not None
        and any(height < transit_clearance - _EPS
                for height in heights[first_near + 1:])
    )
    completed = bool(pre_high_ok and high_corridor_ok and descended_after)
    target = [home[0], home[1], _number(spec.get("holding_altitude_m", 0.5),
                                       "success_spec.holding_altitude_m")]
    metrics = [
        _duration_metric(times),
        _metric("initial_home_separation_m", separation, home_tolerance,
                separation > home_tolerance + _EPS),
        _metric("first_transit_altitude_m", None if first_high is None else heights[first_high],
                transit_altitude, first_high is not None),
        _metric("horizontal_progress_before_transit_m",
                None if first_high is None else max(toward_home[:first_high + 1]),
                0.3, pre_high_ok),
        _metric("high_altitude_corridor_to_home", high_corridor_ok, True, high_corridor_ok),
        _metric("descent_after_home_entry", descended_after, True, descended_after),
        _metric("completed_operation", int(completed), 1, completed),
    ]
    metrics.extend(_terminal_motion_metrics(samples, times, target, spec=spec))
    metrics.append(_forbidden_contacts(samples))
    return metrics


def _dwell_window(times: Sequence[float], first: int, duration: float,
                  predicate) -> tuple[int, int] | None:
    for start in range(first, len(times)):
        end = start
        while end + 1 < len(times) and times[end] - times[start] < duration - _EPS:
            end += 1
        if (times[end] - times[start] >= duration - _EPS
                and all(predicate(index) for index in range(start, end + 1))):
            return start, end
    return None


def _t05(spec: Mapping[str, Any], parameters: Mapping[str, Any],
         samples: list[Mapping[str, Any]], times: list[float]) -> list[dict[str, Any]]:
    raw_waypoints = parameters.get("waypoints_xyz_m")
    if isinstance(raw_waypoints, (str, bytes)) or not isinstance(raw_waypoints, Sequence) or len(raw_waypoints) != 4:
        raise ValueError("parameters.waypoints_xyz_m must contain exactly four waypoints")
    waypoints = [_vector(point, 3, f"parameters.waypoints_xyz_m[{i}]")
                 for i, point in enumerate(raw_waypoints)]
    raw_headings = parameters.get("waypoint_headings_rad")
    if isinstance(raw_headings, (str, bytes)) or not isinstance(raw_headings, Sequence) or len(raw_headings) != 4:
        raise ValueError("parameters.waypoint_headings_rad must contain exactly four headings")
    headings = [_number(value, f"parameters.waypoint_headings_rad[{i}]")
                for i, value in enumerate(raw_headings)]
    position_limit = _number(spec.get("waypoint_position_threshold_m", 0.2),
                             "success_spec.waypoint_position_threshold_m")
    heading_limit = math.radians(_number(spec.get("waypoint_heading_threshold_deg", 10.0),
                                         "success_spec.waypoint_heading_threshold_deg"))
    dwell = _number(spec.get("waypoint_hold_s", 0.5), "success_spec.waypoint_hold_s")
    cursor = 1
    events: list[dict[str, Any]] = []
    for waypoint, heading in zip(waypoints, headings, strict=True):
        window = _dwell_window(
            times,
            cursor,
            dwell,
            lambda index, waypoint=waypoint, heading=heading: (
                _distance(_position(samples, index), waypoint) <= position_limit + _EPS
                and abs(_wrap_angle(_yaw(_rotation(samples, index)) - heading))
                <= heading_limit + _EPS
            ),
        )
        if window is None:
            break
        start, end = window
        events.append({"start": start, "end": end})
        cursor = end + 1
    visited = len(events)
    metrics = [
        _duration_metric(times),
        _metric("ordered_waypoint_visits", visited, 4, visited == 4,
                waypoint_hold_s=dwell),
    ]
    for number, event in enumerate(events, 1):
        metrics.append(_metric(
            f"waypoint_{number}_dwell_s",
            times[event["end"]] - times[event["start"]],
            dwell,
            times[event["end"]] - times[event["start"]] >= dwell - _EPS,
        ))
    target = waypoints[-1]
    metrics.extend(_terminal_motion_metrics(samples, times, target, spec=spec))
    metrics.append(_forbidden_contacts(samples))
    return metrics


def _t07(spec: Mapping[str, Any], parameters: Mapping[str, Any],
         samples: list[Mapping[str, Any]], times: list[float]) -> list[dict[str, Any]]:
    center = _vector(parameters.get("center_xy_m"), 2, "parameters.center_xy_m")
    radius_target = _number(spec.get("orbit_radius_m", 1.5),
                            "success_spec.orbit_radius_m")
    height_target = _number(spec.get("orbit_height_m", 1.5),
                            "success_spec.orbit_height_m")
    progress_target = _number(spec.get("orbit_progress_threshold_rad", 2.0 * math.pi),
                              "success_spec.orbit_progress_threshold_rad")
    positive_direction = _number(spec.get("positive_direction", 1),
                                 "success_spec.positive_direction")
    if positive_direction not in (-1.0, 1.0):
        raise ValueError("success_spec.positive_direction must be 1 or -1")
    positions = [_position(samples, i) for i in range(len(samples))]
    orbit_angles = [math.atan2(position[1] - center[1], position[0] - center[0])
                    for position in positions]
    progress = 0.0
    circuit_end: int | None = None
    for index in range(1, len(orbit_angles)):
        progress += positive_direction * _wrap_angle(
            orbit_angles[index] - orbit_angles[index - 1]
        )
        if circuit_end is None and progress >= progress_target - _EPS:
            circuit_end = index
    signed_progress = progress
    radii = [math.dist(position[:2], center) for position in positions]
    radius_error = max(abs(radius - radius_target) for radius in radii)
    height_error = max(abs(position[2] - height_target) for position in positions)
    bearing_errors: list[float] = []
    # The catalog's bearing RMSE is measured during the circuit.  Once the
    # first complete positive circuit is reached, later station-keeping must
    # not dilute a poor orientation during the actual orbit.
    bearing_stop = len(positions) if circuit_end is None else circuit_end + 1
    for index, position in enumerate(positions[:bearing_stop]):
        center_vector = [center[0] - position[0], center[1] - position[1]]
        if _norm(center_vector) <= 1e-12:
            continue
        desired = math.atan2(center_vector[1], center_vector[0])
        bearing_errors.append(abs(_wrap_angle(_yaw(_rotation(samples, index)) - desired)))
    bearing_rmse = None if not bearing_errors else math.degrees(
        math.sqrt(sum(error * error for error in bearing_errors) / len(bearing_errors))
    )
    radius_limit = _number(spec.get("radius_threshold_m", 0.2),
                            "success_spec.radius_threshold_m")
    height_limit = _number(spec.get("height_threshold_m", 0.15),
                           "success_spec.height_threshold_m")
    bearing_limit = _number(spec.get("bearing_rmse_threshold_deg", 12.0),
                            "success_spec.bearing_rmse_threshold_deg")
    terminal_window = _number(spec.get("terminal_window_s", _TERMINAL_WINDOW_S),
                              "success_spec.terminal_window_s")
    tilt_limit = _number(spec.get("body_tilt_threshold_deg", 20.0),
                         "success_spec.body_tilt_threshold_deg")
    indices = _final_window(times, terminal_window)
    metrics = [
        _duration_metric(times),
        _metric("signed_orbit_progress_rad", signed_progress, progress_target,
                signed_progress >= progress_target - _EPS),
        _metric("maximum_orbit_radius_error_m", radius_error, radius_limit,
                radius_error <= radius_limit + _EPS),
        _metric("maximum_orbit_height_error_m", height_error, height_limit,
                height_error <= height_limit + _EPS),
        _metric("orbit_bearing_rmse_deg", bearing_rmse, bearing_limit,
                bearing_rmse is not None and bearing_rmse <= bearing_limit + _EPS),
        _final_window_metric(times, indices, terminal_window),
    ]
    if indices is None:
        metrics.append(_metric("terminal_body_tilt_deg", None, tilt_limit, False))
    else:
        tilt = max(_body_tilt_deg(_rotation(samples, i)) for i in indices)
        metrics.append(_metric("terminal_body_tilt_deg", tilt, tilt_limit,
                               tilt <= tilt_limit + _EPS))
    metrics.append(_forbidden_contacts(samples))
    return metrics


def evaluate_skydio_task(
    spec: Mapping[str, Any], parameters: Mapping[str, Any],
    samples: list[Mapping[str, Any]], times: list[float],
) -> list[dict[str, Any]]:
    """Return physical metrics for one selected X2 task."""

    if not isinstance(spec, Mapping):
        raise ValueError("success_spec must be a mapping")
    task_id = spec.get("task_id")
    if task_id not in _TASKS:
        raise ValueError(f"unknown selected Skydio task {task_id!r}")
    if not isinstance(parameters, Mapping):
        raise ValueError("parameters must be a mapping")
    if not isinstance(samples, list) or not samples:
        raise ValueError("samples must be a nonempty list")
    if not isinstance(times, list) or len(times) != len(samples):
        raise ValueError("times must be a list aligned with samples")
    if task_id == "X2-T01":
        return _t01(spec, samples, times)
    if task_id == "X2-T03":
        return _t03(spec, samples, times)
    if task_id == "X2-T04":
        return _t04(spec, parameters, samples, times)
    if task_id == "X2-T05":
        return _t05(spec, parameters, samples, times)
    return _t07(spec, parameters, samples, times)


__all__ = ["evaluate_skydio_task"]
