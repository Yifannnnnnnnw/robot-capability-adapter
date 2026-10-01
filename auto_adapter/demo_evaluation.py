"""Small, controller-independent physical checks for the fixed DEMO traces.

The trace is produced by the native MuJoCo sampler.  This module deliberately
only consumes that trace, the public DEMO parameters, and the public success
specification.  In particular, a controller return value or a method name is
never used as a task verdict.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any, Callable


class _Unscorable(ValueError):
    """The supplied trace/spec cannot support a physical verdict."""


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _number(value: Any, name: str) -> float:
    if not _finite(value):
        raise _Unscorable(f"{name} must be a finite number")
    return float(value)


def _vector(value: Any, size: int, name: str) -> list[float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != size:
        raise _Unscorable(f"{name} must have length {size}")
    return [_number(v, name) for v in value]


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _Unscorable(f"{name} must be a mapping")
    return value


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def _metric(check: str, value: Any, threshold: Any, ok: bool, **extra: Any) -> dict[str, Any]:
    item = {"check": check, "value": value, "threshold": threshold, "ok": bool(ok)}
    item.update(extra)
    return item


def _state_item(samples: list[Mapping[str, Any]], label: str, index: int) -> Mapping[str, Any]:
    state = _mapping(samples[index].get("state"), f"sample[{index}].state")
    if label not in state:
        raise _Unscorable(f"sample[{index}] is missing state label {label!r}")
    return _mapping(state[label], f"sample[{index}].state[{label!r}]")


def _position(samples: list[Mapping[str, Any]], label: str, index: int) -> list[float]:
    item = _state_item(samples, label, index)
    return _vector(item.get("position"), 3, f"sample[{index}].{label}.position")


def _value(samples: list[Mapping[str, Any]], label: str, index: int) -> float:
    item = _state_item(samples, label, index)
    return _number(item.get("value"), f"sample[{index}].{label}.value")


def _rotation(samples: list[Mapping[str, Any]], label: str, index: int) -> list[float]:
    item = _state_item(samples, label, index)
    return _vector(item.get("rotation"), 9, f"sample[{index}].{label}.rotation")


def _velocity(samples: list[Mapping[str, Any]], label: str, index: int, field: str) -> list[float]:
    item = _state_item(samples, label, index)
    return _vector(item.get(field), 3, f"sample[{index}].{label}.{field}")


def _times(samples: list[Mapping[str, Any]]) -> list[float]:
    result = []
    previous = None
    for i, sample in enumerate(samples):
        time = _number(sample.get("time"), f"sample[{i}].time")
        if previous is not None and time < previous:
            raise _Unscorable("sample times are not monotone")
        result.append(time)
        previous = time
    return result


def _validate_trace(success_spec: Mapping[str, Any], samples_arg: Any) -> tuple[list[Mapping[str, Any]], list[float]]:
    if not isinstance(samples_arg, Sequence) or isinstance(samples_arg, (str, bytes)):
        raise _Unscorable("samples must be a sequence")
    if len(samples_arg) < 2:
        raise _Unscorable("at least two physics samples are required")
    samples: list[Mapping[str, Any]] = []
    for i, sample in enumerate(samples_arg):
        samples.append(_mapping(sample, f"sample[{i}]") )
    times = _times(samples)
    bindings = _mapping(success_spec.get("bindings"), "success_spec.bindings")
    if not bindings:
        raise _Unscorable("success_spec.bindings is empty")
    for label, binding_arg in bindings.items():
        binding = _mapping(binding_arg, f"binding {label!r}")
        kind = binding.get("kind")
        if kind not in {"body", "site", "geom", "joint"}:
            raise _Unscorable(f"binding {label!r} has unknown kind {kind!r}")
        if not isinstance(binding.get("name"), str) or not binding["name"]:
            raise _Unscorable(f"binding {label!r} has no MJCF name")
    for i, sample in enumerate(samples):
        state = _mapping(sample.get("state"), f"sample[{i}].state")
        contacts = sample.get("contacts")
        if isinstance(contacts, (str, bytes)) or not isinstance(contacts, Sequence):
            raise _Unscorable(f"sample[{i}].contacts must be a sequence")
        for contact in contacts:
            if isinstance(contact, (str, bytes)) or not isinstance(contact, Sequence) or len(contact) != 2:
                raise _Unscorable(f"sample[{i}] has an invalid contact pair")
            if not all(isinstance(name, str) and name for name in contact):
                raise _Unscorable(f"sample[{i}] has a non-name contact body")
        for label, binding_arg in bindings.items():
            binding = _mapping(binding_arg, f"binding {label!r}")
            if label not in state:
                raise _Unscorable(f"sample[{i}] is missing state label {label!r}")
            item = _mapping(state[label], f"sample[{i}].state[{label!r}]")
            if binding["kind"] == "joint":
                _number(item.get("value"), f"sample[{i}].{label}.value")
            else:
                _vector(item.get("position"), 3, f"sample[{i}].{label}.position")
    return samples, times


def _required_mapping(parameters: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    return _mapping(parameters.get(key), f"parameters.{key}")


def _required_vector(parameters: Mapping[str, Any], key: str, size: int = 3) -> list[float]:
    return _vector(parameters.get(key), size, f"parameters.{key}")


def _positive(parameters: Mapping[str, Any], key: str) -> float:
    value = _number(parameters.get(key), f"parameters.{key}")
    if value <= 0:
        raise _Unscorable(f"parameters.{key} must be positive")
    return value


def _nonnegative(value: Any, name: str) -> float:
    number = _number(value, name)
    if number < 0:
        raise _Unscorable(f"{name} must be nonnegative")
    return number


def _event_position(
    samples: list[Mapping[str, Any]],
    times: list[float],
    label: str,
    target: Sequence[float],
    start: int,
    deadline: float,
    tolerance: float,
    *,
    require_after: bool = False,
    predicate: Callable[[int], bool] | None = None,
) -> tuple[int | None, float | None]:
    first = start + (1 if require_after else 0)
    start_time = times[start]
    for index in range(first, len(samples)):
        elapsed = times[index] - start_time
        if elapsed > deadline + 1e-9:
            break
        if _distance(_position(samples, label, index), target) <= tolerance and (predicate is None or predicate(index)):
            return index, elapsed
    return None, None


def _event_multi_position(
    samples: list[Mapping[str, Any]],
    times: list[float],
    targets: Mapping[str, Sequence[float]],
    start: int,
    deadline: float,
    tolerance: float,
    *,
    require_after: bool = False,
    minimum_motion: float = 0.0,
) -> tuple[int | None, float | None]:
    """Find one sample where every labelled point reaches its target."""
    first = start + (1 if require_after else 0)
    start_positions = {label: _position(samples, label, start) for label in targets}
    start_time = times[start]
    for index in range(first, len(samples)):
        elapsed = times[index] - start_time
        if elapsed > deadline + 1e-9:
            break
        if all(
            _distance(_position(samples, label, index), target) <= tolerance
            and _max_position_change(samples, label, start, index) >= minimum_motion
            for label, target in targets.items()
        ):
            return index, elapsed
    return None, None


def _event_value(
    samples: list[Mapping[str, Any]],
    times: list[float],
    getter: Callable[[int], float],
    target: float,
    start: int,
    deadline: float,
    tolerance: float,
    *,
    require_after: bool = False,
    predicate: Callable[[int], bool] | None = None,
) -> tuple[int | None, float | None]:
    first = start + (1 if require_after else 0)
    start_time = times[start]
    for index in range(first, len(samples)):
        elapsed = times[index] - start_time
        if elapsed > deadline + 1e-9:
            break
        value = getter(index)
        if abs(value - target) <= tolerance and (predicate is None or predicate(index)):
            return index, elapsed
    return None, None


def _event_joint(
    samples: list[Mapping[str, Any]],
    times: list[float],
    label: str,
    target: float,
    start: int,
    deadline: float,
    tolerance: float,
    *,
    require_after: bool = False,
    minimum_change: float = 0.0,
) -> tuple[int | None, float | None]:
    start_value = _value(samples, label, start)
    return _event_value(
        samples,
        times,
        lambda i: _value(samples, label, i),
        target,
        start,
        deadline,
        tolerance,
        require_after=require_after,
        predicate=lambda i: abs(_value(samples, label, i) - start_value) >= minimum_change,
    )


def _event_joint_vector(
    samples: list[Mapping[str, Any]],
    times: list[float],
    labels: Sequence[str],
    targets: Mapping[str, float],
    start: int,
    deadline: float,
    tolerance: float,
    *,
    require_after: bool = False,
    minimum_change: float = 0.0,
) -> tuple[int | None, float | None]:
    """Find one sample where a joint configuration is simultaneously reached."""
    first = start + (1 if require_after else 0)
    start_values = {label: _value(samples, label, start) for label in labels}
    start_time = times[start]
    for index in range(first, len(samples)):
        elapsed = times[index] - start_time
        if elapsed > deadline + 1e-9:
            break
        if all(abs(_value(samples, label, index) - targets[label]) <= tolerance for label in labels):
            if any(abs(_value(samples, label, index) - start_values[label]) >= minimum_change for label in labels):
                return index, elapsed
    return None, None


def _max_position_change(samples: list[Mapping[str, Any]], label: str, start: int, end: int) -> float:
    anchor = _position(samples, label, start)
    return max((_distance(anchor, _position(samples, label, i)) for i in range(start, end + 1)), default=0.0)


def _window(
    samples: list[Mapping[str, Any]],
    times: list[float],
    start: int,
    duration: float,
    predicate: Callable[[int], bool],
) -> tuple[bool, int]:
    start_time = times[start]
    end = start
    target_time = start_time + duration
    while end + 1 < len(samples) and times[end] < target_time - 1e-9:
        end += 1
    covered = times[end] >= target_time - 1e-9
    return bool(covered and all(predicate(i) for i in range(start, end + 1))), end


def _find_window(
    samples: list[Mapping[str, Any]],
    times: list[float],
    start: int,
    duration: float,
    predicate: Callable[[int], bool],
) -> tuple[int | None, int | None]:
    for index in range(start, len(samples)):
        ok, end = _window(samples, times, index, duration, predicate)
        if ok:
            return index, end
    return None, None


def _rotation_upright_error(rotation: Sequence[float]) -> float:
    # R is row-major; its third column is the body z axis in world coordinates.
    z = max(-1.0, min(1.0, float(rotation[8])))
    return math.acos(z)


def _rotation_difference(a: Sequence[float], b: Sequence[float]) -> float:
    """Return the relative angle between two row-major rotation matrices."""
    trace = sum(float(a[3 * row + col]) * float(b[3 * row + col]) for row in range(3) for col in range(3))
    cosine = max(-1.0, min(1.0, (trace - 1.0) * 0.5))
    return math.acos(cosine)


def _yaw(rotation: Sequence[float]) -> float:
    return math.atan2(float(rotation[3]), float(rotation[0]))


def _angle_difference(a: float, b: float) -> float:
    return (a - b + math.pi) % (2 * math.pi) - math.pi


def _stable_body(
    samples: list[Mapping[str, Any]],
    label: str,
    index: int,
    *,
    target_height: float | None = None,
    height_tolerance: float = 0.015,
    upright_tolerance: float = 0.15,
    linear_speed_tolerance: float = 0.2,
    angular_speed_tolerance: float = 0.4,
) -> bool:
    position = _position(samples, label, index)
    upright = _rotation_upright_error(_rotation(samples, label, index))
    linear = _velocity(samples, label, index, "linear_velocity")
    angular = _velocity(samples, label, index, "angular_velocity")
    return bool(
        upright <= upright_tolerance
        and math.sqrt(sum(x * x for x in linear)) <= linear_speed_tolerance
        and math.sqrt(sum(x * x for x in angular)) <= angular_speed_tolerance
        and (target_height is None or abs(position[2] - target_height) <= height_tolerance)
    )


def _all_upright(samples: list[Mapping[str, Any]], label: str, end: int, tolerance: float) -> bool:
    return all(_rotation_upright_error(_rotation(samples, label, i)) <= tolerance for i in range(end + 1))


def _gap_getter(success_spec: Mapping[str, Any], samples: list[Mapping[str, Any]]) -> Callable[[int], float]:
    labels = success_spec.get("opening_gap_labels")
    if labels is not None:
        if isinstance(labels, (str, bytes)) or not isinstance(labels, Sequence) or len(labels) != 2:
            raise _Unscorable("opening_gap_labels must contain two labels")
        left, right = str(labels[0]), str(labels[1])
        return lambda index: _distance(_position(samples, left, index), _position(samples, right, index))
    joint_label = success_spec.get("opening_gap_joint_label")
    if isinstance(joint_label, str) and joint_label:
        scale = _number(success_spec.get("opening_gap_joint_scale", 1.0), "opening_gap_joint_scale")
        offset = _number(success_spec.get("opening_gap_joint_offset_m", 0.0), "opening_gap_joint_offset_m")
        return lambda index: offset + scale * _value(samples, joint_label, index)
    raise _Unscorable("success spec has no physical opening measurement")


def _arm_waypoints_gripper(
    spec: Mapping[str, Any], parameters: Mapping[str, Any], samples: list[Mapping[str, Any]], times: list[float]
) -> list[dict[str, Any]]:
    waypoints = parameters.get("waypoints_world_m")
    if isinstance(waypoints, (str, bytes)) or not isinstance(waypoints, Sequence) or len(waypoints) < 2:
        raise _Unscorable("parameters.waypoints_world_m must contain at least two waypoints")
    targets = [_vector(point, 3, f"parameters.waypoints_world_m[{i}]") for i, point in enumerate(waypoints)]
    max_segment = _positive(parameters, "max_duration_per_segment_s")
    tolerance = _nonnegative(spec.get("waypoint_tolerance_m", 0.02), "waypoint_tolerance_m")
    motion_threshold = _nonnegative(spec.get("waypoint_motion_m", 0.003), "waypoint_motion_m")
    metrics: list[dict[str, Any]] = []
    cursor = 0
    waypoint_indices: list[int] = []
    for number, target in enumerate(targets, 1):
        index, elapsed = _event_position(
            samples,
            times,
            "ee",
            target,
            cursor,
            max_segment,
            tolerance,
            require_after=True,
            predicate=lambda i, start=cursor: _max_position_change(samples, "ee", start, i) >= motion_threshold,
        )
        error = None if index is None else _distance(_position(samples, "ee", index), target)
        metrics.append(_metric(f"waypoint_{number}_position_m", error, tolerance, index is not None and error <= tolerance))
        metrics.append(_metric(f"waypoint_{number}_duration_s", elapsed, max_segment, index is not None and elapsed <= max_segment))
        if index is None:
            continue
        movement = _max_position_change(samples, "ee", cursor, index)
        metrics.append(_metric(f"waypoint_{number}_distinct_motion_m", movement, motion_threshold, movement >= motion_threshold))
        waypoint_indices.append(index)
        cursor = index
    if len(waypoint_indices) != len(targets):
        # The trace is well formed, but no later phase can rescue a missed waypoint.
        return metrics

    closed = _number(spec.get("opening_gap_closed_m"), "opening_gap_closed_m")
    opened = _number(spec.get("opening_gap_open_m"), "opening_gap_open_m")
    if opened <= closed:
        raise _Unscorable("opening_gap_open_m must exceed opening_gap_closed_m")
    closed_fraction = _number(parameters.get("closed_opening_fraction"), "parameters.closed_opening_fraction")
    open_fraction = _number(parameters.get("open_opening_fraction"), "parameters.open_opening_fraction")
    if not 0.0 <= closed_fraction <= 1.0 or not 0.0 <= open_fraction <= 1.0:
        raise _Unscorable("opening fractions must be in [0, 1]")
    opening_targets = [closed + fraction * (opened - closed) for fraction in (closed_fraction, open_fraction)]
    gap_tolerance = _nonnegative(spec.get("opening_gap_tolerance_m", 0.003), "opening_gap_tolerance_m")
    minimum_gap_motion = _nonnegative(spec.get("opening_gap_motion_m", gap_tolerance), "opening_gap_motion_m")
    gap = _gap_getter(spec, samples)
    opening_limit = _positive(parameters, "max_gripper_duration_s")
    for number, target in enumerate(opening_targets, 1):
        opening_start = cursor
        index, elapsed = _event_value(
            samples,
            times,
            gap,
            target,
            opening_start,
            opening_limit,
            gap_tolerance,
            require_after=True,
        )
        measured = None if index is None else gap(index)
        gap_error = None if measured is None else abs(measured - target)
        metrics.append(_metric(f"opening_{number}_gap_m", gap_error, gap_tolerance,
                               index is not None and gap_error <= gap_tolerance,
                               measured_m=measured, target_m=target))
        metrics.append(_metric(f"opening_{number}_duration_s", elapsed, opening_limit, index is not None and elapsed <= opening_limit))
        if index is not None:
            movement = abs(gap(index) - gap(opening_start))
            metrics.append(_metric(f"opening_{number}_distinct_motion_m", movement, minimum_gap_motion, movement >= minimum_gap_motion))
            # A target can be reached well before the next physical change.  Let
            # that dwell finish before starting the next change's deadline;
            # otherwise a long hold at target 1 is charged to target 2.
            cursor = index
            while cursor + 1 < len(samples):
                next_index = cursor + 1
                if times[next_index] - times[opening_start] > opening_limit + 1e-9:
                    break
                if abs(gap(next_index) - target) > gap_tolerance:
                    break
                cursor = next_index
    return metrics


def _arm_waypoints_offset(
    spec: Mapping[str, Any], parameters: Mapping[str, Any], samples: list[Mapping[str, Any]], times: list[float]
) -> list[dict[str, Any]]:
    waypoints = parameters.get("waypoints_world_m")
    if isinstance(waypoints, (str, bytes)) or not isinstance(waypoints, Sequence) or len(waypoints) < 2:
        raise _Unscorable("parameters.waypoints_world_m must contain at least two waypoints")
    targets = [_vector(point, 3, f"parameters.waypoints_world_m[{i}]") for i, point in enumerate(waypoints)]
    segment_limit = _positive(parameters, "max_duration_per_segment_s")
    leg_limit = _positive(parameters, "max_duration_per_leg_s")
    tolerance = _nonnegative(spec.get("waypoint_tolerance_m", 0.02), "waypoint_tolerance_m")
    motion_threshold = _nonnegative(spec.get("waypoint_motion_m", 0.003), "waypoint_motion_m")
    metrics: list[dict[str, Any]] = []
    cursor = 0
    # Resolve all ordinary checkpoints first.  The final checkpoint is
    # handled below because the following offset leg identifies the physical
    # point at which that checkpoint was actually reached.
    for number, target in enumerate(targets[:-1], 1):
        index, elapsed = _event_position(
            samples,
            times,
            "ee",
            target,
            cursor,
            segment_limit,
            tolerance,
            require_after=True,
            predicate=lambda i, start=cursor: _max_position_change(samples, "ee", start, i) >= motion_threshold,
        )
        error = None if index is None else _distance(_position(samples, "ee", index), target)
        metrics.append(_metric(f"waypoint_{number}_position_m", error, tolerance, index is not None and error <= tolerance))
        metrics.append(_metric(f"waypoint_{number}_duration_s", elapsed, segment_limit, index is not None and elapsed <= segment_limit))
        if index is None:
            return metrics
        cursor = index
    offset = _required_vector(parameters, "offset_robot_base_m")
    offset_distance = _distance([0.0, 0.0, 0.0], offset)
    offset_motion_threshold = _nonnegative(spec.get("offset_motion_m", max(0.003, 0.25 * offset_distance)), "offset_motion_m")
    offset_tolerance = _nonnegative(spec.get("offset_tolerance_m", min(tolerance, .25 * offset_distance)), "offset_tolerance_m")

    final_target = targets[-1]
    candidates: list[int] = []
    for candidate in range(cursor + 1, len(samples)):
        if times[candidate] - times[cursor] > segment_limit + 1e-9:
            break
        if (_distance(_position(samples, "ee", candidate), final_target) <= tolerance
                and _max_position_change(samples, "ee", cursor, candidate) >= motion_threshold):
            candidates.append(candidate)

    # Prefer the latest checkpoint sample that supports a complete offset
    # round trip.  This avoids constructing the offset from an early entry
    # into a large tolerance ball while the arm is still approaching.
    selected: tuple[int, int | None, float | None, int | None, float | None, list[float], list[float]] | None = None
    successful: tuple[int, int | None, float | None, int | None, float | None, list[float], list[float]] | None = None
    for candidate in candidates:
        start_position = _position(samples, "ee", candidate)
        base_rotation = _rotation(samples, "base", candidate)
        world_offset = [
            base_rotation[0] * offset[0] + base_rotation[1] * offset[1] + base_rotation[2] * offset[2],
            base_rotation[3] * offset[0] + base_rotation[4] * offset[1] + base_rotation[5] * offset[2],
            base_rotation[6] * offset[0] + base_rotation[7] * offset[1] + base_rotation[8] * offset[2],
        ]
        outbound = [start_position[i] + world_offset[i] for i in range(3)]
        outbound_index, outbound_elapsed = _event_position(
            samples,
            times,
            "ee",
            outbound,
            candidate,
            leg_limit,
            offset_tolerance,
            require_after=True,
            predicate=lambda i, start=candidate: _max_position_change(samples, "ee", start, i) >= offset_motion_threshold,
        )
        return_index: int | None = None
        return_elapsed: float | None = None
        if outbound_index is not None:
            return_index, return_elapsed = _event_position(
                samples,
                times,
                "ee",
                start_position,
                outbound_index,
                leg_limit,
                offset_tolerance,
                require_after=True,
                predicate=lambda i, start=outbound_index: _max_position_change(samples, "ee", start, i) >= offset_motion_threshold,
            )
        selected = (candidate, outbound_index, outbound_elapsed, return_index, return_elapsed, start_position, outbound)
        if return_index is not None:
            successful = selected

    selected = successful or selected
    if selected is None:
        # Preserve useful diagnostics for a trace that never reaches the last
        # checkpoint, while keeping all later checks false.
        metrics.append(_metric("waypoint_%d_position_m" % len(targets), None, tolerance, False))
        metrics.append(_metric("waypoint_%d_duration_s" % len(targets), None, segment_limit, False))
        metrics.append(_metric("waypoint_%d_distinct_motion_m" % len(targets), None, motion_threshold, False))
        return metrics

    candidate, index, elapsed, index2, elapsed2, start_position, outbound = selected
    final_error = _distance(_position(samples, "ee", candidate), final_target)
    final_elapsed = times[candidate] - times[cursor]
    final_motion = _max_position_change(samples, "ee", cursor, candidate)
    number = len(targets)
    metrics.append(_metric(f"waypoint_{number}_position_m", final_error, tolerance, final_error <= tolerance))
    metrics.append(_metric(f"waypoint_{number}_duration_s", final_elapsed, segment_limit, final_elapsed <= segment_limit))
    metrics.append(_metric(f"waypoint_{number}_distinct_motion_m", final_motion, motion_threshold,
                           final_motion >= motion_threshold))
    outbound_error = None if index is None else _distance(_position(samples, "ee", index), outbound)
    metrics.append(_metric("offset_outbound_position_m", outbound_error, offset_tolerance,
                           index is not None and outbound_error <= offset_tolerance))
    metrics.append(_metric("offset_outbound_duration_s", elapsed, leg_limit,
                           index is not None and elapsed <= leg_limit))
    outbound_motion = None if index is None else _max_position_change(samples, "ee", candidate, index)
    metrics.append(_metric("offset_outbound_motion_m", outbound_motion, offset_motion_threshold,
                           index is not None and outbound_motion >= offset_motion_threshold))
    return_error = None if index2 is None else _distance(_position(samples, "ee", index2), start_position)
    metrics.append(_metric("offset_return_position_m", return_error, offset_tolerance,
                           index2 is not None and return_error <= offset_tolerance))
    metrics.append(_metric("offset_return_duration_s", elapsed2, leg_limit,
                           index2 is not None and elapsed2 <= leg_limit))
    return metrics


def _arm_waypoints_joint_gripper(
    spec: Mapping[str, Any], parameters: Mapping[str, Any], samples: list[Mapping[str, Any]], times: list[float]
) -> list[dict[str, Any]]:
    waypoints = parameters.get("waypoints_world_m")
    if isinstance(waypoints, (str, bytes)) or not isinstance(waypoints, Sequence) or len(waypoints) < 2:
        raise _Unscorable("parameters.waypoints_world_m must contain at least two waypoints")
    targets = [_vector(point, 3, f"parameters.waypoints_world_m[{i}]") for i, point in enumerate(waypoints)]
    segment_limit = _positive(parameters, "max_duration_per_segment_s")
    gripper_limit = _positive(parameters, "max_gripper_duration_s")
    tolerance = _nonnegative(spec.get("waypoint_tolerance_m", 0.02), "waypoint_tolerance_m")
    motion_threshold = _nonnegative(spec.get("waypoint_motion_m", 0.003), "waypoint_motion_m")
    metrics: list[dict[str, Any]] = []
    cursor = 0
    for number, target in enumerate(targets, 1):
        index, elapsed = _event_position(
            samples,
            times,
            "ee",
            target,
            cursor,
            segment_limit,
            tolerance,
            require_after=True,
            predicate=lambda i, start=cursor: _max_position_change(samples, "ee", start, i) >= motion_threshold,
        )
        error = None if index is None else _distance(_position(samples, "ee", index), target)
        metrics.append(_metric(f"waypoint_{number}_position_m", error, tolerance, index is not None and error <= tolerance))
        metrics.append(_metric(f"waypoint_{number}_duration_s", elapsed, segment_limit, index is not None and elapsed <= segment_limit))
        if index is None:
            return metrics
        movement = _max_position_change(samples, "ee", cursor, index)
        metrics.append(_metric(f"waypoint_{number}_distinct_motion_m", movement, motion_threshold, movement >= motion_threshold))
        cursor = index
    gripper_targets = parameters.get("gripper_joint_targets_rad")
    if isinstance(gripper_targets, (str, bytes)) or not isinstance(gripper_targets, Sequence) or len(gripper_targets) < 2:
        raise _Unscorable("parameters.gripper_joint_targets_rad must contain two targets")
    gripper_tolerance = _nonnegative(spec.get("gripper_tolerance_rad", 0.02), "gripper_tolerance_rad")
    gripper_motion = _nonnegative(spec.get("gripper_motion_rad", 0.01), "gripper_motion_rad")
    for number, target_arg in enumerate(gripper_targets, 1):
        target = _number(target_arg, f"parameters.gripper_joint_targets_rad[{number - 1}]")
        index, elapsed = _event_joint(samples, times, "gripper", target, cursor, gripper_limit, gripper_tolerance,
                                      require_after=True, minimum_change=gripper_motion)
        value = None if index is None else _value(samples, "gripper", index)
        error = None if value is None else abs(value - target)
        metrics.append(_metric(f"gripper_{number}_target_rad", value, gripper_tolerance, index is not None and error <= gripper_tolerance,
                               target_rad=target))
        metrics.append(_metric(f"gripper_{number}_duration_s", elapsed, gripper_limit, index is not None and elapsed <= gripper_limit))
        if index is None:
            return metrics
        cursor = index
    return metrics


def _quadruped(
    spec: Mapping[str, Any], parameters: Mapping[str, Any], samples: list[Mapping[str, Any]], times: list[float]
) -> list[dict[str, Any]]:
    label = "base"
    initial = _position(samples, label, 0)
    initial_rotation = _rotation(samples, label, 0)
    initial_yaw = _yaw(initial_rotation)
    initial_stand = _positive(parameters, "initial_stand_s")
    walk_limit = _positive(parameters, "max_walk_duration_s")
    post_stand = _positive(parameters, "post_walk_stand_s")
    height_limit = _positive(parameters, "max_height_change_duration_s")
    final_stand = _positive(parameters, "final_stand_s")
    walk_delta = _required_vector(parameters, "translation_initial_yaw_m", 2)
    crouch_height = _number(parameters.get("crouch_height_m"), "parameters.crouch_height_m")
    stand_height = _number(parameters.get("standing_height_m"), "parameters.standing_height_m")
    upright_tolerance = _nonnegative(spec.get("upright_tolerance_rad", 0.15), "upright_tolerance_rad")
    height_tolerance = _nonnegative(spec.get("height_tolerance_m", 0.015), "height_tolerance_m")

    def stable(index: int, target_height: float | None = None) -> bool:
        return _stable_body(samples, label, index, target_height=target_height,
                            height_tolerance=height_tolerance, upright_tolerance=upright_tolerance,
                            linear_speed_tolerance=_nonnegative(spec.get("stable_linear_speed_m_s", 0.2), "stable_linear_speed_m_s"),
                            angular_speed_tolerance=_nonnegative(spec.get("stable_angular_speed_rad_s", 0.4), "stable_angular_speed_rad_s"))

    metrics: list[dict[str, Any]] = []
    initial_ok, initial_end = _window(samples, times, 0, initial_stand, lambda i: stable(i, initial[2]))
    metrics.append(_metric("initial_stance_hold_s", initial_stand if initial_ok else times[initial_end] - times[0], initial_stand, initial_ok))
    if not initial_ok:
        return metrics
    walk_start = initial_end
    target_xy = [
        initial[0] + math.cos(initial_yaw) * walk_delta[0] - math.sin(initial_yaw) * walk_delta[1],
        initial[1] + math.sin(initial_yaw) * walk_delta[0] + math.cos(initial_yaw) * walk_delta[1],
    ]
    target_position = [target_xy[0], target_xy[1], initial[2]]
    walk_motion_threshold = _nonnegative(
        spec.get("walk_motion_m", max(0.01, 0.5 * _distance(walk_delta, [0.0, 0.0]))),
        "walk_motion_m",
    )

    def walk_pred(index: int) -> bool:
        rotation = _rotation(samples, label, index)
        return abs(_angle_difference(_yaw(rotation), initial_yaw + _number(parameters.get("yaw_delta_rad"), "parameters.yaw_delta_rad"))) <= _nonnegative(spec.get("yaw_tolerance_rad", math.radians(5)), "yaw_tolerance_rad")

    walk_index, walk_elapsed = _event_position(samples, times, label, target_position, walk_start, walk_limit,
                                               _nonnegative(spec.get("walk_position_tolerance_m", 0.02), "walk_position_tolerance_m"),
                                               require_after=True,
                                               predicate=lambda i: walk_pred(i) and _max_position_change(samples, label, walk_start, i) >= walk_motion_threshold)
    walk_error = None if walk_index is None else _distance(_position(samples, label, walk_index), target_position)
    metrics.append(_metric("walk_forward_position_m", walk_error, spec.get("walk_position_tolerance_m", 0.02), walk_index is not None and walk_error <= spec.get("walk_position_tolerance_m", 0.02)))
    metrics.append(_metric("walk_duration_s", walk_elapsed, walk_limit, walk_index is not None and walk_elapsed <= walk_limit))
    walk_motion = None if walk_index is None else _max_position_change(samples, label, walk_start, walk_index)
    metrics.append(_metric("walk_motion_m", walk_motion, walk_motion_threshold,
                           walk_index is not None and walk_motion >= walk_motion_threshold))
    if walk_index is None:
        return metrics
    lateral = abs(-(math.sin(initial_yaw)) * (_position(samples, label, walk_index)[0] - initial[0]) + math.cos(initial_yaw) * (_position(samples, label, walk_index)[1] - initial[1]))
    metrics.append(_metric("walk_lateral_error_m", lateral, spec.get("walk_lateral_tolerance_m", 0.02), lateral <= spec.get("walk_lateral_tolerance_m", 0.02)))
    post_index, post_end = _find_window(samples, times, walk_index, post_stand, lambda i: stable(i, initial[2]))
    metrics.append(_metric("post_walk_stance_hold_s", post_stand if post_index is not None else None, post_stand, post_index is not None))
    if post_index is None or post_end is None:
        return metrics
    crouch_start = post_end
    crouch_index, crouch_elapsed = _event_value(samples, times, lambda i: _position(samples, label, i)[2], crouch_height,
                                                 crouch_start, height_limit, height_tolerance, require_after=True,
                                                 predicate=lambda i: _position(samples, label, i)[2] <= initial[2] - 0.5 * abs(initial[2] - crouch_height))
    crouch_error = None if crouch_index is None else abs(_position(samples, label, crouch_index)[2] - crouch_height)
    metrics.append(_metric("crouch_height_m", None if crouch_error is None else _position(samples, label, crouch_index)[2], height_tolerance, crouch_index is not None and crouch_error <= height_tolerance))
    metrics.append(_metric("crouch_duration_s", crouch_elapsed, height_limit, crouch_index is not None and crouch_elapsed <= height_limit))
    if crouch_index is None:
        return metrics
    stand_index, stand_elapsed = _event_value(samples, times, lambda i: _position(samples, label, i)[2], stand_height,
                                               crouch_index, height_limit, height_tolerance, require_after=True,
                                               predicate=lambda i: _position(samples, label, i)[2] >= crouch_height + 0.5 * abs(stand_height - crouch_height))
    stand_error = None if stand_index is None else abs(_position(samples, label, stand_index)[2] - stand_height)
    metrics.append(_metric("standing_height_m", None if stand_error is None else _position(samples, label, stand_index)[2], height_tolerance, stand_index is not None and stand_error <= height_tolerance))
    metrics.append(_metric("height_recovery_duration_s", stand_elapsed, height_limit, stand_index is not None and stand_elapsed <= height_limit))
    if stand_index is None:
        return metrics
    final_index, _ = _find_window(samples, times, stand_index, final_stand, lambda i: stable(i, stand_height))
    metrics.append(_metric("final_stance_hold_s", final_stand if final_index is not None else None, final_stand, final_index is not None))
    upright = _all_upright(samples, label, len(samples) - 1, upright_tolerance)
    metrics.append(_metric("upright_throughout", upright, upright_tolerance, upright))
    return metrics


def _hand_fingertips(
    spec: Mapping[str, Any], parameters: Mapping[str, Any], samples: list[Mapping[str, Any]], times: list[float]
) -> list[dict[str, Any]]:
    targets = _required_mapping(parameters, "fingertip_targets_world_m")
    labels = ["index", "middle", "ring", "thumb"]
    tolerance = _nonnegative(spec.get("target_tolerance_m", 0.02), "target_tolerance_m")
    motion_threshold = _nonnegative(spec.get("fingertip_motion_m", 0.003), "fingertip_motion_m")
    deadline = _positive(parameters, "max_duration_per_motion_s")
    initial_positions = {label: _position(samples, label, 0) for label in labels}
    metrics: list[dict[str, Any]] = []
    target_vectors = {
        label: _vector(targets.get(label), 3, f"parameters.fingertip_targets_world_m.{label}") for label in labels
    }
    target_index, target_elapsed = _event_multi_position(
        samples, times, target_vectors, 0, deadline, tolerance, require_after=True, minimum_motion=motion_threshold
    )
    for label in labels:
        target = target_vectors[label]
        error = None if target_index is None else _distance(_position(samples, label, target_index), target)
        movement = None if target_index is None else _max_position_change(samples, label, 0, target_index)
        metrics.append(_metric(f"{label}_target_position_m", error, tolerance, target_index is not None and error <= tolerance))
        metrics.append(_metric(f"{label}_target_duration_s", target_elapsed, deadline, target_index is not None and target_elapsed <= deadline))
        metrics.append(_metric(f"{label}_target_motion_m", movement, motion_threshold, target_index is not None and movement >= motion_threshold))
    if target_index is None:
        return metrics
    return_index, return_elapsed = _event_multi_position(
        samples, times, initial_positions, target_index, deadline, tolerance, require_after=True, minimum_motion=motion_threshold
    )
    for label in labels:
        error = None if return_index is None else _distance(_position(samples, label, return_index), initial_positions[label])
        movement = None if return_index is None else _max_position_change(samples, label, target_index, return_index)
        metrics.append(_metric(f"{label}_return_position_m", error, tolerance, return_index is not None and error <= tolerance))
        metrics.append(_metric(f"{label}_return_duration_s", return_elapsed, deadline, return_index is not None and return_elapsed <= deadline))
        metrics.append(_metric(f"{label}_return_motion_m", movement, motion_threshold, return_index is not None and movement >= motion_threshold))
    final = return_index if return_index is not None else len(samples) - 1
    palm_tolerance = _nonnegative(spec.get("palm_position_tolerance_m", 0.01), "palm_position_tolerance_m")
    palm_initial = _position(samples, "palm", 0)
    palm_drift = max(_distance(palm_initial, _position(samples, "palm", i)) for i in range(final + 1))
    metrics.append(_metric("palm_max_drift_m", palm_drift, palm_tolerance, palm_drift <= palm_tolerance))
    palm_rotation_tolerance = _nonnegative(spec.get("palm_rotation_tolerance_rad", 0.05), "palm_rotation_tolerance_rad")
    palm_rotation_drift = max(_rotation_difference(_rotation(samples, "palm", 0), _rotation(samples, "palm", i)) for i in range(final + 1))
    metrics.append(_metric("palm_max_rotation_drift_rad", palm_rotation_drift, palm_rotation_tolerance,
                           palm_rotation_drift <= palm_rotation_tolerance))
    return metrics


def _stretch(
    spec: Mapping[str, Any], parameters: Mapping[str, Any], samples: list[Mapping[str, Any]], times: list[float]
) -> list[dict[str, Any]]:
    base_initial = _position(samples, "base", 0)
    rotation = _rotation(samples, "base", 0)
    yaw = _yaw(rotation)
    distance = _number(parameters.get("forward_distance_m"), "parameters.forward_distance_m")
    speed = _number(parameters.get("drive_speed_m_s"), "parameters.drive_speed_m_s")
    target = [base_initial[0] + math.cos(yaw) * distance, base_initial[1] + math.sin(yaw) * distance, base_initial[2]]
    drive_limit = _positive(parameters, "max_drive_duration_s")
    tolerance = _nonnegative(spec.get("base_target_tolerance_m", 0.02), "base_target_tolerance_m")
    yaw_tol = _nonnegative(spec.get("yaw_tolerance_rad", math.radians(5)), "yaw_tolerance_rad")
    drive_index, elapsed = _event_position(samples, times, "base", target, 0, drive_limit, tolerance, require_after=True,
                                           predicate=lambda i: abs(_angle_difference(_yaw(_rotation(samples, "base", i)), yaw)) <= yaw_tol)
    metrics: list[dict[str, Any]] = []
    error = None if drive_index is None else _distance(_position(samples, "base", drive_index), target)
    metrics.append(_metric("base_forward_position_m", error, tolerance, drive_index is not None and error <= tolerance))
    metrics.append(_metric("base_drive_duration_s", elapsed, drive_limit, drive_index is not None and elapsed <= drive_limit))
    if drive_index is None:
        return metrics
    forward_velocities = []
    for i in range(0, drive_index + 1):
        velocity = _velocity(samples, "base", i, "linear_velocity")
        forward_velocities.append(velocity[0] * math.cos(yaw) + velocity[1] * math.sin(yaw))
    mean_speed = sum(forward_velocities) / len(forward_velocities)
    speed_tolerance = _nonnegative(spec.get("drive_speed_tolerance_m_s", 0.05), "drive_speed_tolerance_m_s")
    metrics.append(_metric("mean_forward_speed_m_s", mean_speed, speed_tolerance, abs(mean_speed - speed) <= speed_tolerance))
    ee_target = _required_vector(parameters, "end_effector_target_world_m")
    reach_limit = _positive(parameters, "max_reach_duration_s")
    reach_index, reach_elapsed = _event_position(samples, times, "ee", ee_target, drive_index, reach_limit,
                                                 _nonnegative(spec.get("ee_target_tolerance_m", 0.02), "ee_target_tolerance_m"), require_after=True)
    ee_error = None if reach_index is None else _distance(_position(samples, "ee", reach_index), ee_target)
    metrics.append(_metric("ee_target_position_m", ee_error, spec.get("ee_target_tolerance_m", 0.02), reach_index is not None and ee_error <= spec.get("ee_target_tolerance_m", 0.02)))
    metrics.append(_metric("ee_reach_duration_s", reach_elapsed, reach_limit, reach_index is not None and reach_elapsed <= reach_limit))
    if reach_index is not None:
        base_drift = max(_distance(_position(samples, "base", drive_index), _position(samples, "base", i)) for i in range(drive_index, reach_index + 1))
        fixed_tolerance = _nonnegative(spec.get("base_fixed_tolerance_m", 0.015), "base_fixed_tolerance_m")
        metrics.append(_metric("base_drift_during_reach_m", base_drift, fixed_tolerance, base_drift <= fixed_tolerance))
    return metrics


def _bimanual(
    spec: Mapping[str, Any], parameters: Mapping[str, Any], samples: list[Mapping[str, Any]], times: list[float]
) -> list[dict[str, Any]]:
    left_target = _required_vector(parameters, "left_target_world_m")
    right_target = _required_vector(parameters, "right_target_world_m")
    limit = _positive(parameters, "max_duration_per_arm_s")
    hold = _positive(parameters, "hold_duration_s")
    tolerance = _nonnegative(spec.get("target_tolerance_m", 0.02), "target_tolerance_m")
    other_tolerance = _nonnegative(spec.get("other_arm_tolerance_m", 0.015), "other_arm_tolerance_m")
    left_initial = _position(samples, "left_ee", 0)
    right_initial = _position(samples, "right_ee", 0)
    metrics: list[dict[str, Any]] = []
    left_index, left_elapsed = _event_position(samples, times, "left_ee", left_target, 0, limit, tolerance, require_after=True)
    left_error = None if left_index is None else _distance(_position(samples, "left_ee", left_index), left_target)
    metrics.append(_metric("left_target_position_m", left_error, tolerance, left_index is not None and left_error <= tolerance))
    metrics.append(_metric("left_move_duration_s", left_elapsed, limit, left_index is not None and left_elapsed <= limit))
    if left_index is None:
        return metrics
    right_drift = max(_distance(right_initial, _position(samples, "right_ee", i)) for i in range(left_index + 1))
    metrics.append(_metric("right_arm_held_during_left_m", right_drift, other_tolerance, right_drift <= other_tolerance))
    right_index, right_elapsed = _event_position(samples, times, "right_ee", right_target, left_index, limit, tolerance, require_after=True)
    right_error = None if right_index is None else _distance(_position(samples, "right_ee", right_index), right_target)
    metrics.append(_metric("right_target_position_m", right_error, tolerance, right_index is not None and right_error <= tolerance))
    metrics.append(_metric("right_move_duration_s", right_elapsed, limit, right_index is not None and right_elapsed <= limit))
    if right_index is None:
        return metrics
    left_hold_error = max(_distance(left_target, _position(samples, "left_ee", i)) for i in range(left_index, right_index + 1))
    metrics.append(_metric("left_arm_held_during_right_m", left_hold_error, other_tolerance, left_hold_error <= other_tolerance))
    hold_ok, _ = _window(samples, times, right_index, hold,
                         lambda i: _distance(_position(samples, "left_ee", i), left_target) <= tolerance
                         and _distance(_position(samples, "right_ee", i), right_target) <= tolerance)
    metrics.append(_metric("both_target_hold_s", hold if hold_ok else None, hold, hold_ok))
    return metrics


def _aerial(
    spec: Mapping[str, Any], parameters: Mapping[str, Any], samples: list[Mapping[str, Any]], times: list[float]
) -> list[dict[str, Any]]:
    initial = _position(samples, "base", 0)
    altitude = _number(parameters.get("target_altitude_world_m"), "parameters.target_altitude_world_m")
    takeoff_limit = _positive(parameters, "max_takeoff_duration_s")
    hover = _positive(parameters, "hover_duration_s")
    altitude_tol = _nonnegative(spec.get("altitude_tolerance_m", 0.02), "altitude_tolerance_m")
    upright_tol = _nonnegative(spec.get("upright_tolerance_rad", 0.15), "upright_tolerance_rad")
    target = [initial[0], initial[1], altitude]
    index, elapsed = _event_position(samples, times, "base", target, 0, takeoff_limit, altitude_tol, require_after=True,
                                     predicate=lambda i: _position(samples, "base", i)[2] >= initial[2] + 0.5 * abs(altitude - initial[2]))
    metrics: list[dict[str, Any]] = []
    error = None if index is None else abs(_position(samples, "base", index)[2] - altitude)
    metrics.append(_metric("takeoff_altitude_m", None if error is None else _position(samples, "base", index)[2], altitude_tol, index is not None and error <= altitude_tol))
    metrics.append(_metric("takeoff_duration_s", elapsed, takeoff_limit, index is not None and elapsed <= takeoff_limit))
    if index is None:
        return metrics
    hover_tol = _nonnegative(spec.get("hover_position_tolerance_m", 0.02), "hover_position_tolerance_m")
    linear_limit = _nonnegative(spec.get("hover_linear_speed_m_s", 0.1), "hover_linear_speed_m_s")
    angular_limit = _nonnegative(spec.get("hover_angular_speed_rad_s", 0.2), "hover_angular_speed_rad_s")
    hover_start: int | None = None
    hover_end: int | None = None
    # Reaching altitude can precede settling.  Search for the first complete
    # stable window whose start still satisfies the takeoff deadline.
    for candidate in range(index, len(samples)):
        if times[candidate] - times[0] > takeoff_limit + 1e-9:
            break
        anchor_xy = _position(samples, "base", candidate)[:2]

        def hover_pred(i: int, anchor_xy: list[float] = anchor_xy) -> bool:
            position = _position(samples, "base", i)
            linear = _velocity(samples, "base", i, "linear_velocity")
            angular = _velocity(samples, "base", i, "angular_velocity")
            return (abs(position[2] - altitude) <= altitude_tol
                    and _distance(position[:2], anchor_xy) <= hover_tol
                    and _rotation_upright_error(_rotation(samples, "base", i)) <= upright_tol
                    and math.sqrt(sum(x * x for x in linear)) <= linear_limit
                    and math.sqrt(sum(x * x for x in angular)) <= angular_limit)

        candidate_ok, candidate_end = _window(samples, times, candidate, hover, hover_pred)
        if candidate_ok:
            hover_start, hover_end = candidate, candidate_end
            break
    hover_ok = hover_start is not None and hover_end is not None
    metrics.append(_metric("hover_hold_s", hover if hover_ok else None, hover, hover_ok))
    upright = _all_upright(samples, "base", len(samples) - 1, upright_tol)
    metrics.append(_metric("upright_throughout", upright, upright_tol, upright))
    return metrics


def _humanoid_squat(
    spec: Mapping[str, Any], parameters: Mapping[str, Any], samples: list[Mapping[str, Any]], times: list[float]
) -> list[dict[str, Any]]:
    label = "base"
    initial = _position(samples, label, 0)
    initial_z = initial[2]
    initial_hold = _positive(parameters, "initial_stand_s")
    motion_limit = _positive(parameters, "squat_and_recovery_duration_s")
    final_hold = _positive(parameters, "final_stand_s")
    depth = _number(parameters.get("squat_depth_m"), "parameters.squat_depth_m")
    if depth <= 0:
        raise _Unscorable("squat_depth_m must be positive")
    upright_tol = _nonnegative(spec.get("upright_tolerance_rad", 0.15), "upright_tolerance_rad")
    height_tol = _nonnegative(spec.get("height_tolerance_m", 0.02), "height_tolerance_m")
    stable = lambda i, target_height: _stable_body(
        samples,
        label,
        i,
        target_height=target_height,
        upright_tolerance=upright_tol,
        height_tolerance=height_tol,
        linear_speed_tolerance=0.2,
        angular_speed_tolerance=0.4,
    )
    metrics: list[dict[str, Any]] = []
    initial_ok, initial_end = _window(samples, times, 0, initial_hold, lambda i: stable(i, initial_z))
    metrics.append(_metric("initial_stance_hold_s", initial_hold if initial_ok else times[initial_end] - times[0], initial_hold, initial_ok))
    if not initial_ok:
        return metrics
    crouch_target = initial_z - depth
    crouch_index, crouch_elapsed = _event_value(samples, times, lambda i: _position(samples, label, i)[2], crouch_target,
                                                 initial_end, motion_limit, height_tol, require_after=True,
                                                 predicate=lambda i: _position(samples, label, i)[2] <= initial_z - 0.5 * depth)
    crouch_error = None if crouch_index is None else abs(_position(samples, label, crouch_index)[2] - crouch_target)
    metrics.append(_metric("squat_depth_m", None if crouch_error is None else initial_z - _position(samples, label, crouch_index)[2], height_tol, crouch_index is not None and crouch_error <= height_tol))
    if crouch_index is None:
        return metrics
    metrics.append(_metric("squat_motion_duration_s", crouch_elapsed, motion_limit, crouch_elapsed is not None and crouch_elapsed <= motion_limit))
    remaining_motion = motion_limit - (times[crouch_index] - times[initial_end])
    stand_index: int | None = None
    stand_elapsed: float | None = None
    if remaining_motion > 0:
        stand_index, stand_elapsed = _event_value(
            samples,
            times,
            lambda i: _position(samples, label, i)[2],
            initial_z,
            crouch_index,
            remaining_motion,
            height_tol,
            require_after=True,
            predicate=lambda i: _position(samples, label, i)[2] >= crouch_target + 0.5 * depth,
        )
    stand_error = None if stand_index is None else abs(_position(samples, label, stand_index)[2] - initial_z)
    metrics.append(_metric("recovered_standing_height_m", None if stand_error is None else _position(samples, label, stand_index)[2], height_tol, stand_index is not None and stand_error <= height_tol))
    total_motion = times[stand_index] - times[initial_end] if stand_index is not None else None
    metrics.append(_metric("recovery_duration_s", total_motion, motion_limit,
                           stand_index is not None and total_motion <= motion_limit))
    if stand_index is None:
        return metrics
    final_index, final_end = _find_window(samples, times, stand_index, final_hold, lambda i: stable(i, initial_z))
    metrics.append(_metric("final_stance_hold_s", final_hold if final_index is not None else None, final_hold, final_index is not None))
    end = len(samples) - 1 if final_index is None else final_end
    upright = _all_upright(samples, label, len(samples) - 1, upright_tol)
    metrics.append(_metric("upright_throughout", upright, upright_tol, upright))
    if "torso" in _mapping(spec.get("bindings"), "success_spec.bindings"):
        torso_initial = _position(samples, "torso", 0)
        torso_drift = max(_distance(torso_initial, _position(samples, "torso", i)) for i in range(end + 1))
        # The torso is intentionally allowed to squat; this records it as an observed task marker.
        metrics.append(_metric("torso_observed_motion_m", torso_drift, depth * 0.25, torso_drift >= depth * 0.25))
    return metrics


def _push(
    spec: Mapping[str, Any], parameters: Mapping[str, Any], samples: list[Mapping[str, Any]], times: list[float]
) -> list[dict[str, Any]]:
    max_duration = _positive(parameters, "max_duration_s")
    initial_target = _required_vector(parameters, "object_initial_position_world_m")
    delta = _required_vector(parameters, "push_delta_world_m")
    pushed_label = str(parameters.get("pushed_body", "tee"))
    bindings = _mapping(spec.get("bindings"), "success_spec.bindings")
    pushed_binding = _mapping(bindings.get(pushed_label), f"binding {pushed_label!r}")
    pushed_name = str(pushed_binding.get("name"))
    ee_tolerance = _nonnegative(spec.get("contact_position_tolerance_m", 0.05), "contact_position_tolerance_m")
    object_tolerance = _nonnegative(spec.get("object_position_tolerance_m", 0.01), "object_position_tolerance_m")
    tool_names = spec.get("contact_tool_body_names")
    if isinstance(tool_names, (str, bytes)) or not isinstance(tool_names, Sequence) or not tool_names:
        raise _Unscorable("contact_tool_body_names must be a nonempty list")
    tool_names = {str(name) for name in tool_names}
    obstacle_label = str(spec.get("obstacle_label", "obstacle"))
    obstacle_binding = bindings.get(obstacle_label)
    obstacle_name = None
    if obstacle_binding is not None:
        obstacle_name = str(_mapping(obstacle_binding, f"binding {obstacle_label!r}").get("name"))
    metrics: list[dict[str, Any]] = []
    initial_object_error = _distance(_position(samples, pushed_label, 0), initial_target)
    metrics.append(_metric("object_initial_position_m", initial_object_error, object_tolerance, initial_object_error <= object_tolerance))
    if initial_object_error > object_tolerance:
        return metrics
    contact_index: int | None = None
    for i in range(1, len(samples)):
        if times[i] - times[0] > max_duration + 1e-9:
            break
        if _distance(_position(samples, "ee", i), _position(samples, pushed_label, i)) <= ee_tolerance:
            pairs = [set(pair) for pair in samples[i].get("contacts", ())]
            if any(pushed_name in pair and bool(pair & tool_names) for pair in pairs):
                contact_index = i
                break
    metrics.append(_metric("target_contact_seen", contact_index is not None, True, contact_index is not None))
    if contact_index is None:
        return metrics
    push_target = [initial_target[i] + delta[i] for i in range(3)]
    pushed_index, pushed_elapsed = _event_position(samples, times, pushed_label, push_target, contact_index, max_duration,
                                                   object_tolerance, require_after=True)
    object_error = None if pushed_index is None else _distance(_position(samples, pushed_label, pushed_index), push_target)
    metrics.append(_metric("pushed_object_position_m", object_error, object_tolerance, pushed_index is not None and object_error <= object_tolerance))
    metrics.append(_metric("push_duration_s", None if pushed_index is None else times[pushed_index] - times[0], max_duration,
                           pushed_index is not None and times[pushed_index] - times[0] <= max_duration))
    if pushed_index is None:
        return metrics
    actual_delta = [_position(samples, pushed_label, pushed_index)[i] - initial_target[i] for i in range(3)]
    min_motion = _nonnegative(spec.get("minimum_push_motion_m", max(0.005, 0.25 * _distance(delta, [0, 0, 0]))), "minimum_push_motion_m")
    metrics.append(_metric("pushed_object_motion_m", _distance(actual_delta, [0, 0, 0]), min_motion, _distance(actual_delta, [0, 0, 0]) >= min_motion))
    if any(abs(actual_delta[i] - delta[i]) > object_tolerance for i in range(3)):
        # Keep the target metric's false value authoritative; this just prevents a coincidental move.
        metrics[-1]["ok"] = False

    # The pushed body must remain at the requested destination after contact;
    # a later collision or drift cannot turn a completed intermediate event
    # into task success.
    target_hold_drift = max(
        _distance(_position(samples, pushed_label, i), push_target)
        for i in range(pushed_index, len(samples))
    )
    metrics.append(_metric("pushed_object_post_target_drift_m", target_hold_drift, object_tolerance,
                           target_hold_drift <= object_tolerance))

    cursor = pushed_index
    return_labels = spec.get("return_joint_labels", ())
    if return_labels:
        if isinstance(return_labels, (str, bytes)) or not isinstance(return_labels, Sequence):
            raise _Unscorable("return_joint_labels must be a sequence")
        labels = [str(label_arg) for label_arg in return_labels]
        return_tolerance = _nonnegative(spec.get("return_joint_tolerance_rad", 0.03), "return_joint_tolerance_rad")
        return_motion = _nonnegative(spec.get("return_joint_motion_rad", 0.01), "return_joint_motion_rad")
        homes = {label: _value(samples, label, 0) for label in labels}
        remaining = max_duration - (times[cursor] - times[0])
        return_index: int | None = None
        return_elapsed: float | None = None
        if remaining > 0:
            return_index, return_elapsed = _event_joint_vector(
                samples,
                times,
                labels,
                homes,
                cursor,
                remaining,
                return_tolerance,
                require_after=True,
                minimum_change=return_motion,
            )
        for number, label in enumerate(labels, 1):
            error = None if return_index is None else abs(_value(samples, label, return_index) - homes[label])
            metrics.append(_metric(f"return_joint_{number}_rad", error, return_tolerance,
                                   return_index is not None and error <= return_tolerance,
                                   joint=label, home_rad=homes[label]))
        metrics.append(_metric("return_configuration_duration_s", return_elapsed, remaining,
                               return_index is not None and return_elapsed <= remaining))
        if return_index is None:
            return metrics
        cursor = return_index
    if "clearance_waypoint_world_m" in parameters:
        clearance = _required_vector(parameters, "clearance_waypoint_world_m")
        clearance_tol = _nonnegative(spec.get("clearance_tolerance_m", 0.02), "clearance_tolerance_m")
        withdrawal_motion = _nonnegative(spec.get("withdrawal_motion_m", 0.005), "withdrawal_motion_m")
        remaining = max_duration - (times[cursor] - times[0])
        clear_index: int | None = None
        clear_elapsed: float | None = None
        if remaining > 0:
            clear_index, clear_elapsed = _event_position(
                samples,
                times,
                "ee",
                clearance,
                cursor,
                remaining,
                clearance_tol,
                require_after=True,
                predicate=lambda i, start=cursor: _max_position_change(samples, "ee", start, i) >= withdrawal_motion,
            )
        clear_error = None if clear_index is None else _distance(_position(samples, "ee", clear_index), clearance)
        metrics.append(_metric("withdrawal_position_m", clear_error, clearance_tol, clear_index is not None and clear_error <= clearance_tol))
        total_elapsed = None if clear_index is None else times[clear_index] - times[0]
        metrics.append(_metric("withdrawal_duration_s", total_elapsed, max_duration,
                               clear_index is not None and total_elapsed <= max_duration))
        if clear_index is None:
            return metrics
        cursor = clear_index

    # Check fixed scene objects and contact invariants over the whole recorded
    # task, including the return/withdrawal interval.
    if obstacle_name is not None:
        obstacle_initial = _position(samples, obstacle_label, 0)
        obstacle_tolerance = _nonnegative(spec.get("obstacle_position_tolerance_m", 0.005), "obstacle_position_tolerance_m")
        obstacle_drift = max(_distance(obstacle_initial, _position(samples, obstacle_label, i)) for i in range(len(samples)))
        metrics.append(_metric("obstacle_position_drift_m", obstacle_drift, obstacle_tolerance, obstacle_drift <= obstacle_tolerance))
        unrelated = any(
            bool(set(pair) & tool_names) and obstacle_name in pair
            for sample in samples for pair in sample.get("contacts", ())
        )
        metrics.append(_metric("no_tool_obstacle_contact", unrelated, False, not unrelated))
    safety_labels = spec.get("safety_body_labels", ())
    safety_tolerance = _nonnegative(spec.get("safety_position_tolerance_m", 0.02), "safety_position_tolerance_m")
    for label_arg in safety_labels:
        label = str(label_arg)
        initial = _position(samples, label, 0)
        drift = max(_distance(initial, _position(samples, label, i)) for i in range(len(samples)))
        metrics.append(_metric(f"{label}_safety_drift_m", drift, safety_tolerance, drift <= safety_tolerance))
    return metrics


def _joint_roundtrip(
    spec: Mapping[str, Any], parameters: Mapping[str, Any], samples: list[Mapping[str, Any]], times: list[float]
) -> list[dict[str, Any]]:
    targets = parameters.get("target_joint_positions_rad")
    if targets is None:
        targets = parameters.get("joint_targets_rad")
    if targets is None:
        targets = _required_mapping(parameters, "knee_targets_rad")
    targets = _mapping(targets, "joint target parameters")
    order = spec.get("joint_order")
    if order is None:
        order = list(targets)
    if isinstance(order, (str, bytes)) or not isinstance(order, Sequence) or not order:
        raise _Unscorable("joint_order must be a nonempty list")
    deadline_key = str(spec.get("deadline_parameter", "max_duration_per_leg_s"))
    if deadline_key not in parameters:
        deadline_key = "max_duration_per_joint_s"
    deadline = _positive(parameters, deadline_key)
    tolerance = _nonnegative(spec.get("joint_tolerance_rad", 0.03), "joint_tolerance_rad")
    minimum_motion = _nonnegative(spec.get("joint_motion_rad", 0.01), "joint_motion_rad")
    metrics: list[dict[str, Any]] = []
    cursor = 0
    labels = [str(label_arg) for label_arg in order]
    target_values = {label: _number(targets.get(label), f"joint target {label}") for label in labels}
    if bool(spec.get("target_joint_vector", False)):
        index, elapsed = _event_joint_vector(
            samples, times, labels, target_values, cursor, deadline, tolerance,
            require_after=True, minimum_change=minimum_motion,
        )
        for number, label in enumerate(labels, 1):
            error = None if index is None else abs(_value(samples, label, index) - target_values[label])
            metrics.append(_metric(f"joint_{number}_target_rad", error, tolerance,
                                   index is not None and error <= tolerance,
                                   target_rad=target_values[label], joint=label))
        metrics.append(_metric("target_joint_configuration_duration_s", elapsed, deadline,
                               index is not None and elapsed <= deadline))
        if index is None:
            return metrics
        cursor = index
    else:
        for number, label in enumerate(labels, 1):
            target = target_values[label]
            index, elapsed = _event_joint(samples, times, label, target, cursor, deadline, tolerance,
                                          require_after=True, minimum_change=minimum_motion)
            error = None if index is None else abs(_value(samples, label, index) - target)
            metrics.append(_metric(f"joint_{number}_target_rad", error, tolerance, index is not None and error <= tolerance,
                                   target_rad=target, joint=label))
            metrics.append(_metric(f"joint_{number}_duration_s", elapsed, deadline, index is not None and elapsed <= deadline, joint=label))
            if index is None:
                return metrics
            cursor = index

    homes = {label: _value(samples, label, 0) for label in labels}
    if bool(spec.get("return_joint_vector", False)):
        index, elapsed = _event_joint_vector(
            samples, times, labels, homes, cursor, deadline, tolerance,
            require_after=True, minimum_change=minimum_motion,
        )
        for number, label in enumerate(labels, 1):
            error = None if index is None else abs(_value(samples, label, index) - homes[label])
            metrics.append(_metric(f"joint_{number}_home_rad", error, tolerance,
                                   index is not None and error <= tolerance,
                                   home_rad=homes[label], joint=label))
        metrics.append(_metric("home_joint_configuration_duration_s", elapsed, deadline,
                               index is not None and elapsed <= deadline))
        if index is None:
            return metrics
        cursor = index
    else:
        for number, label in enumerate(labels, 1):
            home = homes[label]
            index, elapsed = _event_joint(samples, times, label, home, cursor, deadline, tolerance,
                                          require_after=True, minimum_change=minimum_motion)
            error = None if index is None else abs(_value(samples, label, index) - home)
            metrics.append(_metric(f"joint_{number}_home_rad", error, tolerance, index is not None and error <= tolerance,
                                   home_rad=home, joint=label))
            metrics.append(_metric(f"joint_{number}_return_duration_s", elapsed, deadline, index is not None and elapsed <= deadline, joint=label))
            if index is None:
                return metrics
            cursor = index
    if "base" in _mapping(spec.get("bindings"), "success_spec.bindings"):
        initial = _position(samples, "base", 0)
        drift = max(_distance(initial, _position(samples, "base", i)) for i in range(len(samples)))
        tolerance_base = _nonnegative(spec.get("base_position_tolerance_m", 0.01), "base_position_tolerance_m")
        metrics.append(_metric("floating_base_position_drift_m", drift, tolerance_base, drift <= tolerance_base))
        rotation_drift = max(_rotation_difference(_rotation(samples, "base", 0), _rotation(samples, "base", i))
                             for i in range(len(samples)))
        rotation_tolerance = _nonnegative(spec.get("base_rotation_tolerance_rad", 0.05), "base_rotation_tolerance_rad")
        metrics.append(_metric("floating_base_rotation_drift_rad", rotation_drift, rotation_tolerance,
                               rotation_drift <= rotation_tolerance))
    return metrics


_FAMILIES: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any], list[Mapping[str, Any]], list[float]], list[dict[str, Any]]]] = {
    "arm_waypoints_gripper": _arm_waypoints_gripper,
    "arm_waypoints_joint_gripper": _arm_waypoints_joint_gripper,
    "arm_waypoints_offset_return": _arm_waypoints_offset,
    "quadruped_stance_walk_height": _quadruped,
    "hand_fingertips_roundtrip": _hand_fingertips,
    "stretch_drive_reach": _stretch,
    "bimanual_ordered_reach": _bimanual,
    "aerial_takeoff_hover": _aerial,
    "humanoid_squat_recover": _humanoid_squat,
    "push_contact_displacement": _push,
    "joint_ordered_roundtrip": _joint_roundtrip,
}


def evaluate_demo_task(success_spec: Mapping[str, Any], parameters: Mapping[str, Any],
                       samples: Sequence[Mapping[str, Any]], *, evaluator=None) -> dict[str, Any]:
    """Evaluate a fixed DEMO from native physical samples.

    ``physical_task_success`` is ``None`` when the trace/spec is not
    scorable, and is a boolean when the trace is structurally valid.  A valid
    trajectory that misses any required event returns ``False``.

    A caller may supply its own metric function for an experiment task. The
    same physical trace checks and nonempty-metric verdict still apply.
    """
    try:
        spec = _mapping(success_spec, "success_spec")
        params = _mapping(parameters, "parameters")
        if evaluator is None:
            family = spec.get("type") or spec.get("family")
            if not isinstance(family, str) or family not in _FAMILIES:
                raise _Unscorable(f"unknown success family {family!r}")
            evaluator = _FAMILIES[family]
        trace, times = _validate_trace(spec, samples)
        metrics = evaluator(spec, params, trace, times)
        return {
            "physical_task_success": bool(metrics) and all(bool(metric.get("ok")) for metric in metrics),
            "task_metrics": metrics,
            "evaluation_error": None,
        }
    except (TypeError, KeyError, IndexError, ValueError, _Unscorable) as exc:
        return {"physical_task_success": None, "task_metrics": [], "evaluation_error": f"{type(exc).__name__}: {exc}"}


__all__ = ["evaluate_demo_task"]
