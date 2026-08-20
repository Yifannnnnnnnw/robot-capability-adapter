"""Trusted measurements and criteria over worker-produced MuJoCo evidence."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


class MeasurementError(ValueError):
    """Raised when a private binding cannot be evaluated from trusted evidence."""


def _samples(evidence: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = evidence.get("samples")
    if not isinstance(values, list) or not values or not all(
        isinstance(item, Mapping) for item in values
    ):
        raise MeasurementError("worker evidence contains no trusted samples")
    return values


def _sample_times(samples: Sequence[Mapping[str, Any]]) -> list[float]:
    times: list[float] = []
    for index, sample in enumerate(samples):
        try:
            time = float(sample["time"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MeasurementError(f"sample {index} has no valid time") from exc
        if not math.isfinite(time):
            raise MeasurementError(f"sample {index} time must be finite")
        if times and time < times[-1]:
            raise MeasurementError("trusted sample times must be non-decreasing")
        times.append(time)
    return times


def _evidence_with_samples(
    evidence: Mapping[str, Any], samples: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    window = dict(evidence)
    window["samples"] = list(samples)
    return window


def _argument(arguments: Mapping[str, Any], name: str) -> Any:
    value: Any = arguments
    for part in name.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise MeasurementError(f"public argument {name!r} is unavailable")
        value = value[part]
    return value


def _vector(value: Any, *, size: int | None = None) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise MeasurementError("measurement expected a numeric vector")
    result = tuple(float(item) for item in value)
    if size is not None and len(result) != size:
        raise MeasurementError(f"measurement expected a vector of length {size}")
    if not all(math.isfinite(item) for item in result):
        raise MeasurementError("measurement vector must be finite")
    return result


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise MeasurementError("cannot compare vectors with different lengths")
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))


def _body_position(sample: Mapping[str, Any], name: str) -> tuple[float, float, float]:
    positions = sample.get("body_positions")
    if not isinstance(positions, Mapping) or name not in positions:
        raise MeasurementError(f"body {name!r} is unavailable")
    return _vector(positions[name], size=3)  # type: ignore[return-value]


def _site_position(sample: Mapping[str, Any], name: str) -> tuple[float, float, float]:
    positions = sample.get("site_positions")
    if not isinstance(positions, Mapping) or name not in positions:
        raise MeasurementError(f"site {name!r} is unavailable")
    return _vector(positions[name], size=3)  # type: ignore[return-value]


def _joint_position(sample: Mapping[str, Any], name: str) -> float:
    positions = sample.get("joint_positions")
    if not isinstance(positions, Mapping) or name not in positions:
        raise MeasurementError(f"joint {name!r} is unavailable")
    return float(positions[name])


def _yaw_deg(quaternion: Sequence[float]) -> float:
    w, x, y, z = _vector(quaternion, size=4)
    sin_yaw = 2.0 * (w * z + x * y)
    cos_yaw = 1.0 - 2.0 * (y * y + z * z)
    return math.degrees(math.atan2(sin_yaw, cos_yaw))


def _wrapped_angle_deg(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def measure(
    binding: Mapping[str, Any],
    *,
    evidence: Mapping[str, Any],
    public_arguments: Mapping[str, Any],
) -> float:
    """Evaluate one explicitly named private measurement binding."""

    kind = binding.get("kind")
    parameters = binding.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise MeasurementError("binding parameters must be an object")
    samples = _samples(evidence)
    first = samples[0]
    final = samples[-1]

    if kind == "final_site_position_error":
        actual = _site_position(final, str(parameters["site_name"]))
        target = _vector(_argument(public_arguments, str(parameters["target_argument"])), size=3)
        return _distance(actual, target)
    if kind == "final_site_axis_error":
        axis = int(parameters["axis"])
        if axis not in {0, 1, 2}:
            raise MeasurementError("site-axis measurement requires axis 0, 1, or 2")
        actual = _site_position(final, str(parameters["site_name"]))
        target = _vector(
            _argument(public_arguments, str(parameters["target_argument"])), size=3
        )
        return abs(actual[axis] - target[axis])
    if kind == "final_weighted_site_position_error":
        actual = _site_position(final, str(parameters["site_name"]))
        target = _vector(
            _argument(public_arguments, str(parameters["target_argument"])), size=3
        )
        weights = _vector(parameters["weights"], size=3)
        weighted_error = tuple(
            (value - goal) * weight
            for value, goal, weight in zip(actual, target, weights)
        )
        return _distance(
            weighted_error,
            (0.0, 0.0, 0.0),
        )
    if kind == "final_body_position_error":
        actual = _body_position(final, str(parameters["body_name"]))
        target = _vector(_argument(public_arguments, str(parameters["target_argument"])), size=3)
        return _distance(actual, target)
    if kind == "final_joint_position_error":
        actual = _joint_position(final, str(parameters["joint_name"]))
        target = float(_argument(public_arguments, str(parameters["target_argument"])))
        return abs(actual - target)
    if kind == "joint_range":
        values = [_joint_position(sample, str(parameters["joint_name"])) for sample in samples]
        return max(values) - min(values)
    if kind == "body_height":
        return _body_position(final, str(parameters["body_name"]))[2]
    if kind == "minimum_body_height":
        return min(
            _body_position(sample, str(parameters["body_name"]))[2] for sample in samples
        )
    if kind == "body_planar_displacement":
        start = _body_position(first, str(parameters["body_name"]))
        end = _body_position(final, str(parameters["body_name"]))
        return _distance(start[:2], end[:2])
    if kind == "body_axis_displacement":
        axis = int(parameters.get("axis", 0))
        start = _body_position(first, str(parameters["body_name"]))
        end = _body_position(final, str(parameters["body_name"]))
        return end[axis] - start[axis]
    if kind == "body_directional_displacement":
        start = _body_position(first, str(parameters["body_name"]))
        end = _body_position(final, str(parameters["body_name"]))
        direction = float(
            _argument(public_arguments, str(parameters["direction_argument"]))
        )
        if not math.isfinite(direction):
            raise MeasurementError("body displacement direction must be finite")
        return (end[0] - start[0]) * math.cos(direction) + (
            end[1] - start[1]
        ) * math.sin(direction)
    if kind == "mean_body_planar_speed":
        start = _body_position(first, str(parameters["body_name"]))
        end = _body_position(final, str(parameters["body_name"]))
        elapsed = float(final["time"]) - float(first["time"])
        if elapsed <= 0:
            raise MeasurementError("mean speed requires positive elapsed time")
        return _distance(start[:2], end[:2]) / elapsed
    if kind == "body_yaw_change_deg":
        name = str(parameters["body_name"])
        first_quaternions = first.get("body_quaternions")
        final_quaternions = final.get("body_quaternions")
        if not isinstance(first_quaternions, Mapping) or not isinstance(
            final_quaternions, Mapping
        ):
            raise MeasurementError("body quaternion observations are unavailable")
        return abs(_yaw_deg(final_quaternions[name]) - _yaw_deg(first_quaternions[name]))
    if kind == "mean_body_heading_error_deg":
        name = str(parameters["body_name"])
        start = _body_position(first, name)
        end = _body_position(final, name)
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        minimum = float(parameters.get("minimum_displacement", 1e-6))
        if math.hypot(dx, dy) < minimum:
            return 180.0
        actual = math.degrees(math.atan2(dy, dx))
        desired = math.degrees(
            float(_argument(public_arguments, str(parameters["direction_argument"])))
        )
        return abs(_wrapped_angle_deg(actual - desired))
    if kind == "named_bodies_axis_completion":
        names = parameters.get("body_names")
        if not isinstance(names, Sequence) or isinstance(names, (str, bytes)) or not names:
            raise MeasurementError("axis completion requires body_names")
        axis = int(parameters.get("axis", 0))
        if axis not in {0, 1, 2}:
            raise MeasurementError("axis completion requires axis 0, 1, or 2")
        finish = float(parameters["finish_coordinate"])
        direction = int(parameters.get("direction", 1))
        if direction not in {-1, 1}:
            raise MeasurementError("axis completion direction must be -1 or 1")
        coordinates = [_body_position(final, str(name))[axis] for name in names]
        completed = all(value >= finish for value in coordinates)
        if direction < 0:
            completed = all(value <= finish for value in coordinates)
        return 1.0 if completed else 0.0
    if kind == "mean_body_yaw_rate":
        name = str(parameters["body_name"])
        times = _sample_times(samples)
        elapsed = times[-1] - times[0]
        if elapsed <= 0.0:
            raise MeasurementError("mean yaw rate requires positive elapsed time")
        yaws: list[float] = []
        for sample in samples:
            quaternions = sample.get("body_quaternions")
            if not isinstance(quaternions, Mapping) or name not in quaternions:
                raise MeasurementError("body quaternion observations are unavailable")
            yaws.append(math.radians(_yaw_deg(quaternions[name])))
        total = 0.0
        for previous, current in zip(yaws, yaws[1:]):
            total += (current - previous + math.pi) % (2.0 * math.pi) - math.pi
        return abs(total / elapsed)
    if kind == "ordered_body_waypoint_completion_ratio":
        name = str(parameters["body_name"])
        waypoints = parameters.get("waypoints")
        if not isinstance(waypoints, Sequence) or isinstance(waypoints, (str, bytes)):
            raise MeasurementError("ordered waypoint measurement requires waypoints")
        points = [_vector(point, size=2) for point in waypoints]
        if not points:
            raise MeasurementError("ordered waypoint measurement requires waypoints")
        tolerance = float(parameters["tolerance"])
        if not math.isfinite(tolerance) or tolerance <= 0.0:
            raise MeasurementError("ordered waypoint tolerance must be positive")
        completed = 0
        for sample in samples:
            if completed == len(points):
                break
            position = _body_position(sample, name)
            if _distance(position[:2], points[completed]) <= tolerance:
                completed += 1
        return completed / len(points)
    if kind == "ordered_body_waypoint_completion_time":
        name = str(parameters["body_name"])
        waypoints = parameters.get("waypoints")
        if not isinstance(waypoints, Sequence) or isinstance(waypoints, (str, bytes)):
            raise MeasurementError("ordered waypoint time requires waypoints")
        points = [_vector(point, size=2) for point in waypoints]
        if not points:
            raise MeasurementError("ordered waypoint time requires waypoints")
        tolerance = float(parameters["tolerance"])
        if not math.isfinite(tolerance) or tolerance <= 0.0:
            raise MeasurementError("ordered waypoint tolerance must be positive")
        times = _sample_times(samples)
        completed = 0
        for index, sample in enumerate(samples):
            position = _body_position(sample, name)
            if _distance(position[:2], points[completed]) <= tolerance:
                completed += 1
                if completed == len(points):
                    return times[index] - times[0]
        raise MeasurementError("ordered waypoints were not completed")
    if kind == "ordered_body_axis_gate_completion_ratio":
        name = str(parameters["body_name"])
        gates = parameters.get("gates")
        if not isinstance(gates, Sequence) or isinstance(gates, (str, bytes)) or not gates:
            raise MeasurementError("ordered gate measurement requires gates")
        completed = 0
        for sample in samples:
            if completed == len(gates):
                break
            gate = gates[completed]
            if not isinstance(gate, Mapping):
                raise MeasurementError("ordered gate definition must be an object")
            axis = int(gate.get("axis", 0))
            direction = int(gate.get("direction", 1))
            if axis not in {0, 1, 2} or direction not in {-1, 1}:
                raise MeasurementError("ordered gate axis or direction is invalid")
            coordinate = _body_position(sample, name)[axis]
            threshold = float(gate["coordinate"])
            crossed = coordinate >= threshold if direction > 0 else coordinate <= threshold
            if crossed:
                completed += 1
        return completed / len(gates)
    if kind == "minimum_body_point_clearance":
        name = str(parameters["body_name"])
        points = parameters.get("points")
        if not isinstance(points, Sequence) or isinstance(points, (str, bytes)) or not points:
            raise MeasurementError("point-clearance measurement requires points")
        planar_points = [_vector(point, size=2) for point in points]
        return min(
            _distance(_body_position(sample, name)[:2], point)
            for sample in samples
            for point in planar_points
        )
    if kind == "named_geom_contact_step_count":
        names = parameters.get("geom_names")
        if not isinstance(names, Sequence) or isinstance(names, (str, bytes)) or not names:
            raise MeasurementError("named contact measurement requires geom_names")
        selected = {str(name) for name in names}
        records = evidence.get("contact_pair_step_counts")
        if not isinstance(records, list):
            raise MeasurementError("per-step contact evidence is unavailable")
        count = 0
        for record in records:
            if not isinstance(record, Mapping):
                raise MeasurementError("per-step contact evidence is invalid")
            if selected.intersection({str(record.get("geom1")), str(record.get("geom2"))}):
                count += int(record.get("step_count", 0))
        return float(count)
    if kind == "contact_sample_count":
        return float(sum(1 for sample in samples if sample.get("contacts")))
    if kind == "physics_step_count":
        return float(evidence.get("step_count", 0))
    raise MeasurementError(f"unsupported private measurement kind {kind!r}")


def compare(value: float, *, comparator: str, threshold: Any) -> bool:
    if not math.isfinite(value):
        return False
    if comparator == "<":
        return value < float(threshold)
    if comparator == "<=":
        return value <= float(threshold)
    if comparator == ">":
        return value > float(threshold)
    if comparator == ">=":
        return value >= float(threshold)
    if comparator == "==":
        return math.isclose(value, float(threshold), rel_tol=0.0, abs_tol=1e-9)
    if comparator == "between":
        low, high = _vector(threshold, size=2)
        return low <= value <= high
    raise MeasurementError(f"unsupported comparator {comparator!r}")


_STATE_BINDING_KINDS = {
    "final_site_position_error",
    "final_site_axis_error",
    "final_weighted_site_position_error",
    "final_body_position_error",
    "final_joint_position_error",
    "body_height",
    "body_yaw_change_deg",
}


def _prefix_value(
    binding: Mapping[str, Any],
    *,
    evidence: Mapping[str, Any],
    public_arguments: Mapping[str, Any],
    index: int,
) -> float:
    samples = _samples(evidence)
    return measure(
        binding,
        evidence=_evidence_with_samples(evidence, samples[: index + 1]),
        public_arguments=public_arguments,
    )


def _continuous_value(
    binding: Mapping[str, Any],
    *,
    evidence: Mapping[str, Any],
    public_arguments: Mapping[str, Any],
    duration_s: float,
) -> tuple[float, float, int] | None:
    samples = _samples(evidence)
    times = _sample_times(samples)
    start = times[0]
    for end_index, end in enumerate(times):
        elapsed = end - start
        if elapsed + 1e-12 >= duration_s:
            value = measure(
                binding,
                evidence=_evidence_with_samples(evidence, samples[: end_index + 1]),
                public_arguments=public_arguments,
            )
            return value, elapsed, end_index
    return None


def evaluate_temporal(
    binding: Mapping[str, Any],
    *,
    criterion: Mapping[str, Any],
    evidence: Mapping[str, Any],
    public_arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Reduce trusted samples according to the criterion's temporal contract.

    The returned value is still compared by the case aggregation. Dwell and
    eventual/within additionally use the comparator while deciding whether a
    state was reached or held in the trusted time series.
    """

    temporal = criterion.get("temporal")
    if not isinstance(temporal, Mapping):
        raise MeasurementError("criterion temporal rule must be an object")
    kind = temporal.get("kind")
    if not isinstance(kind, str):
        raise MeasurementError("criterion temporal kind must be a string")
    comparator = criterion.get("comparator")
    threshold = criterion.get("threshold")
    if not isinstance(comparator, str) or "threshold" not in criterion:
        raise MeasurementError("criterion comparator and threshold are required")
    samples = _samples(evidence)

    if kind == "terminal_state" or kind.startswith("terminal_state_"):
        return {
            "kind": kind,
            "passed": True,
            "value": measure(
                binding, evidence=evidence, public_arguments=public_arguments
            ),
        }

    def duration() -> float:
        value = temporal.get("duration_s")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MeasurementError(f"{kind} requires numeric duration_s")
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise MeasurementError(f"{kind} requires positive duration_s")
        return value

    if kind in {"dwell", "eventual", "within"}:
        times = _sample_times(samples)
        values: list[tuple[int, float]] = []
        last_error: MeasurementError | None = None
        for index in range(len(samples)):
            try:
                value = _prefix_value(
                    binding,
                    evidence=evidence,
                    public_arguments=public_arguments,
                    index=index,
                )
            except MeasurementError as exc:
                last_error = exc
                continue
            values.append((index, value))
        if not values:
            if last_error is not None:
                raise last_error
            raise MeasurementError("temporal rule has no measurable samples")

        if kind == "eventual":
            passing = [
                (index, value)
                for index, value in values
                if compare(value, comparator=comparator, threshold=threshold)
            ]
            if passing:
                index, value = passing[0]
                return {
                    "kind": kind,
                    "passed": True,
                    "value": value,
                    "sample_index": index,
                }
            return {
                "kind": kind,
                "passed": False,
                "value": values[-1][1],
            }

        if kind == "within":
            limit = times[0] + duration()
            passing = [
                (index, value)
                for index, value in values
                if times[index] <= limit + 1e-12
                and compare(value, comparator=comparator, threshold=threshold)
            ]
            if passing:
                index, value = passing[0]
                return {
                    "kind": kind,
                    "passed": True,
                    "value": value,
                    "sample_index": index,
                    "valid_duration_s": times[index] - times[0],
                }
            return {
                "kind": kind,
                "passed": False,
                "value": values[-1][1],
                "valid_duration_s": min(times[-1] - times[0], duration()),
            }

        # Dwell is a contiguous run of sampled states. A single passing sample
        # contributes zero duration; every counted interval has passing states
        # at both endpoints.
        state_by_index = {index: compare(value, comparator=comparator, threshold=threshold)
                          for index, value in values}
        best_duration = 0.0
        best_end_index: int | None = None
        run_duration = 0.0
        for index in range(len(samples) - 1):
            if state_by_index.get(index) and state_by_index.get(index + 1):
                run_duration += times[index + 1] - times[index]
                if run_duration > best_duration:
                    best_duration = run_duration
                    best_end_index = index + 1
            else:
                run_duration = 0.0
        required = duration()
        if best_end_index is None:
            value = values[-1][1]
        else:
            value = next(
                value for index, value in values if index == best_end_index
            )
        return {
            "kind": kind,
            "passed": best_duration + 1e-12 >= required,
            "value": value,
            "valid_duration_s": best_duration,
            "required_duration_s": required,
        }

    if kind == "continuous":
        required = duration()
        window = _continuous_value(
            binding,
            evidence=evidence,
            public_arguments=public_arguments,
            duration_s=required,
        )
        if window is None:
            return {
                "kind": kind,
                "passed": False,
                "value": None,
                "valid_duration_s": _sample_times(samples)[-1]
                - _sample_times(samples)[0],
                "required_duration_s": required,
            }
        value, valid_duration, end_index = window
        if binding.get("kind") in _STATE_BINDING_KINDS:
            times = _sample_times(samples)
            state_values = [
                _prefix_value(
                    binding,
                    evidence=evidence,
                    public_arguments=public_arguments,
                    index=index,
                )
                for index in range(end_index + 1)
            ]
            state_passed = all(
                compare(item, comparator=comparator, threshold=threshold)
                for item in state_values
            )
            value = state_values[-1]
        else:
            state_passed = True
        return {
            "kind": kind,
            "passed": state_passed,
            "value": value,
            "valid_duration_s": valid_duration,
            "required_duration_s": required,
        }

    raise MeasurementError(f"unsupported temporal kind {kind!r}")


def aggregate_criterion(
    aggregation: Mapping[str, Any],
    *,
    comparator: str,
    threshold: Any,
    values: Sequence[Any],
    temporal_passes: Sequence[bool],
) -> dict[str, Any]:
    """Apply the private case aggregation to trusted per-trial values."""

    kind = aggregation.get("kind")
    if not isinstance(kind, str):
        raise MeasurementError("criterion aggregation kind must be a string")
    if not values or len(values) != len(temporal_passes):
        raise MeasurementError("aggregation inputs must cover every trial")
    if any(not isinstance(item, bool) for item in temporal_passes):
        raise MeasurementError("aggregation pass inputs must be boolean")
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value))
           for value in values):
        return {"kind": kind, "passed": False, "value": None}

    numeric_values = [float(value) for value in values]
    if kind == "single_trial":
        if len(numeric_values) != 1:
            return {"kind": kind, "passed": False, "value": None}
        passed = bool(temporal_passes[0]) and compare(
            numeric_values[0], comparator=comparator, threshold=threshold
        )
        return {"kind": kind, "passed": passed, "value": numeric_values[0]}
    if kind == "per_trial":
        passed = all(
            temporal_passed and compare(
                value, comparator=comparator, threshold=threshold
            )
            for value, temporal_passed in zip(numeric_values, temporal_passes)
        )
        return {"kind": kind, "passed": passed, "value": None}
    if kind == "per_trial_mean":
        mean_value = math.fsum(numeric_values) / len(numeric_values)
        passed = all(temporal_passes) and compare(
            mean_value, comparator=comparator, threshold=threshold
        )
        return {"kind": kind, "passed": passed, "value": mean_value}
    raise MeasurementError(f"unsupported aggregation kind {kind!r}")


def evaluate_guards(
    guard_definitions: Sequence[Mapping[str, Any]],
    *,
    worker_result: Mapping[str, Any],
) -> dict[str, bool]:
    evidence = worker_result.get("physical_evidence")
    if not isinstance(evidence, Mapping):
        evidence = {}
    video = worker_result.get("video")
    if not isinstance(video, Mapping):
        video = {}
    outcomes: dict[str, bool] = {}
    for guard in guard_definitions:
        guard_id = str(guard["guard_id"])
        kind = guard.get("kind")
        if kind == "actuator_and_physics_step_required":
            outcomes[guard_id] = (
                int(evidence.get("step_count", 0)) > 0
                and bool(evidence.get("ctrl_observed_before_step"))
                and bool(evidence.get("ctrl_changed_from_reset"))
            )
        elif kind == "no_direct_state_write":
            outcomes[guard_id] = not bool(evidence.get("direct_state_write_detected"))
        elif kind == "canonical_model_data":
            outcomes[guard_id] = bool(worker_result.get("canonical_model_data"))
        elif kind == "complete_video":
            outcomes[guard_id] = bool(video.get("complete"))
        elif kind == "named_geom_contact_pair_required":
            robot_geom_name = guard.get("robot_geom_name")
            task_geom_names = guard.get("task_geom_names")
            minimum_steps = guard.get("minimum_steps")
            records = evidence.get("contact_pair_step_counts")
            outcome = False
            if (
                isinstance(robot_geom_name, str)
                and isinstance(task_geom_names, list)
                and task_geom_names
                and all(isinstance(name, str) for name in task_geom_names)
                and isinstance(minimum_steps, int)
                and not isinstance(minimum_steps, bool)
                and minimum_steps > 0
                and isinstance(records, list)
            ):
                task_names = set(task_geom_names)
                contact_steps = 0
                valid_records = True
                for record in records:
                    if not isinstance(record, Mapping):
                        valid_records = False
                        break
                    geom1 = record.get("geom1")
                    geom2 = record.get("geom2")
                    step_count = record.get("step_count")
                    if (
                        not isinstance(geom1, str)
                        or not isinstance(geom2, str)
                        or isinstance(step_count, bool)
                        or not isinstance(step_count, int)
                        or step_count < 0
                    ):
                        valid_records = False
                        break
                    if (geom1 == robot_geom_name and geom2 in task_names) or (
                        geom2 == robot_geom_name and geom1 in task_names
                    ):
                        contact_steps += step_count
                outcome = valid_records and contact_steps >= minimum_steps
            outcomes[guard_id] = outcome
        elif kind == "terminal_body_stability":
            samples = evidence.get("samples")
            body_name = guard.get("body_name")
            outcome = False
            if (
                isinstance(samples, list)
                and samples
                and isinstance(samples[-1], Mapping)
                and isinstance(body_name, str)
            ):
                final = samples[-1]
                positions = final.get("body_positions")
                quaternions = final.get("body_quaternions")
                if (
                    isinstance(positions, Mapping)
                    and body_name in positions
                    and isinstance(quaternions, Mapping)
                    and body_name in quaternions
                ):
                    try:
                        height = _vector(positions[body_name], size=3)[2]
                        w, x, y, z = _vector(quaternions[body_name], size=4)
                        norm_squared = w * w + x * x + y * y + z * z
                        upright_cosine = 1.0 - 2.0 * (x * x + y * y) / norm_squared
                        outcome = (
                            norm_squared > 0.0
                            and height >= float(guard["minimum_height_m"])
                            and upright_cosine
                            >= float(guard["minimum_upright_cosine"])
                        )
                    except (KeyError, TypeError, ValueError, ZeroDivisionError):
                        outcome = False
            outcomes[guard_id] = outcome
        else:
            raise MeasurementError(f"unsupported private guard kind {kind!r}")
    return outcomes
