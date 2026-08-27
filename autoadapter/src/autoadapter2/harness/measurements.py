"""Trusted measurements and criteria over worker-produced MuJoCo evidence."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .operators import trusted_reference_contract_id


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


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise MeasurementError("cannot combine vectors with different lengths")
    return math.fsum(float(a) * float(b) for a, b in zip(left, right))


def _cross3(
    left: Sequence[float], right: Sequence[float]
) -> tuple[float, float, float]:
    lx, ly, lz = _vector(left, size=3)
    rx, ry, rz = _vector(right, size=3)
    return (
        ly * rz - lz * ry,
        lz * rx - lx * rz,
        lx * ry - ly * rx,
    )


def _unit_vector(value: Sequence[float], *, name: str) -> tuple[float, float, float]:
    vector = _vector(value, size=3)
    norm = math.sqrt(_dot(vector, vector))
    if not math.isfinite(norm) or norm <= 1.0e-8:
        raise MeasurementError(f"{name} must be a finite non-zero vector")
    return tuple(component / norm for component in vector)  # type: ignore[return-value]


def _declared_unit_vector(
    value: Sequence[float], *, name: str
) -> tuple[float, float, float]:
    vector = _vector(value, size=3)
    norm = math.sqrt(_dot(vector, vector))
    if not math.isfinite(norm) or norm <= 1.0e-12:
        raise MeasurementError(f"{name} must be a finite non-zero vector")
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1.0e-6):
        raise MeasurementError(f"{name} must have unit length")
    return vector  # type: ignore[return-value]


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


def _body_quaternion(
    sample: Mapping[str, Any], name: str
) -> tuple[float, float, float, float]:
    quaternions = sample.get("body_quaternions")
    if not isinstance(quaternions, Mapping) or name not in quaternions:
        raise MeasurementError(f"body quaternion {name!r} is unavailable")
    return _vector(quaternions[name], size=4)  # type: ignore[return-value]


def _normalized_quaternion(
    value: Any,
) -> tuple[float, float, float, float]:
    quaternion = _vector(value, size=4)
    norm = math.sqrt(math.fsum(component * component for component in quaternion))
    if norm <= 0.0:
        raise MeasurementError("measurement quaternion must be non-zero")
    return tuple(component / norm for component in quaternion)  # type: ignore[return-value]


def _quaternion_product(
    left: Sequence[float], right: Sequence[float]
) -> tuple[float, float, float, float]:
    lw, lx, ly, lz = _normalized_quaternion(left)
    rw, rx, ry, rz = _normalized_quaternion(right)
    return (
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    )


def _relative_quaternion(
    sample: Mapping[str, Any], body_name: str, reference_body_name: str
) -> tuple[float, float, float, float]:
    reference = _normalized_quaternion(
        _body_quaternion(sample, reference_body_name)
    )
    inverse_reference = (
        reference[0],
        -reference[1],
        -reference[2],
        -reference[3],
    )
    return _normalized_quaternion(
        _quaternion_product(inverse_reference, _body_quaternion(sample, body_name))
    )


def _rotate_inverse(
    quaternion: Sequence[float], vector: Sequence[float]
) -> tuple[float, float, float]:
    w, x, y, z = _normalized_quaternion(quaternion)
    vx, vy, vz = _vector(vector, size=3)
    # This is R(q)^T v, expanded to keep the trusted measurement NumPy-free.
    return (
        (1.0 - 2.0 * (y * y + z * z)) * vx
        + 2.0 * (x * y + w * z) * vy
        + 2.0 * (x * z - w * y) * vz,
        2.0 * (x * y - w * z) * vx
        + (1.0 - 2.0 * (x * x + z * z)) * vy
        + 2.0 * (y * z + w * x) * vz,
        2.0 * (x * z + w * y) * vx
        + 2.0 * (y * z - w * x) * vy
        + (1.0 - 2.0 * (x * x + y * y)) * vz,
    )


def _point_in_body_frame(
    sample: Mapping[str, Any],
    point: Sequence[float],
    reference_body_name: str,
) -> tuple[float, float, float]:
    origin = _body_position(sample, reference_body_name)
    displacement = tuple(
        coordinate - reference
        for coordinate, reference in zip(_vector(point, size=3), origin)
    )
    return _rotate_inverse(
        _body_quaternion(sample, reference_body_name), displacement
    )


def _body_position_in_frame(
    sample: Mapping[str, Any], body_name: str, reference_body_name: str
) -> tuple[float, float, float]:
    return _point_in_body_frame(
        sample, _body_position(sample, body_name), reference_body_name
    )


def _site_position_in_frame(
    sample: Mapping[str, Any], site_name: str, reference_body_name: str
) -> tuple[float, float, float]:
    return _point_in_body_frame(
        sample, _site_position(sample, site_name), reference_body_name
    )


def _trusted_geom_pair_distance(
    sample: Mapping[str, Any], geom_a_name: str, geom_b_name: str
) -> float:
    records = sample.get("trusted_geom_pair_distances")
    if not isinstance(records, list):
        raise MeasurementError(
            "trusted sample has no parent-computed geom-pair distances"
        )
    matches = [
        record
        for record in records
        if isinstance(record, Mapping)
        and record.get("geom_a_name") == geom_a_name
        and record.get("geom_b_name") == geom_b_name
    ]
    if len(matches) != 1:
        raise MeasurementError(
            f"trusted sample does not contain exactly one distance for geoms "
            f"{geom_a_name!r}, {geom_b_name!r}"
        )
    return _finite_number(matches[0].get("distance_m"), "trusted geom-pair distance")


def _accumulated_arc_angle(
    positions: Sequence[Sequence[float]],
    *,
    center: Sequence[float],
    axis: Sequence[float],
) -> float:
    trusted_center = _vector(center, size=3)
    trusted_axis = _declared_unit_vector(axis, name="arc axis")
    radial_directions: list[tuple[float, float, float]] = []
    for position_value in positions:
        position = _vector(position_value, size=3)
        displacement = tuple(
            coordinate - origin
            for coordinate, origin in zip(position, trusted_center)
        )
        axial = _dot(displacement, trusted_axis)
        radial = tuple(
            component - axial * axis_component
            for component, axis_component in zip(displacement, trusted_axis)
        )
        radial_directions.append(_unit_vector(radial, name="arc radius"))
    increments: list[float] = []
    for previous, current in zip(radial_directions, radial_directions[1:]):
        sine = _dot(trusted_axis, _cross3(previous, current))
        cosine = max(-1.0, min(1.0, _dot(previous, current)))
        increment = math.atan2(sine, cosine)
        if abs(increment) >= math.pi - 1.0e-6:
            raise MeasurementError(
                "arc samples are too far apart to disambiguate signed angle"
            )
        increments.append(increment)
    return math.fsum(increments)


def _yaw_rad(quaternion: Sequence[float]) -> float:
    w, x, y, z = _normalized_quaternion(quaternion)
    return math.atan2(
        2.0 * (w * z + x * y),
        1.0 - 2.0 * (y * y + z * z),
    )


def _unwrapped(values: Sequence[float]) -> list[float]:
    if not values:
        raise MeasurementError("angle trajectory must not be empty")
    result = [float(values[0])]
    for value in values[1:]:
        delta = (float(value) - result[-1] + math.pi) % (2.0 * math.pi) - math.pi
        result.append(result[-1] + delta)
    return result


def _positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MeasurementError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise MeasurementError(f"{name} must be positive")
    return result


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MeasurementError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise MeasurementError(f"{name} must be finite")
    return result


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MeasurementError(f"{name} must be a positive integer")
    return value


def _control_samples(
    parameters: Mapping[str, Any],
    *,
    evidence: Mapping[str, Any],
    public_arguments: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    samples = _samples(evidence)
    period = _positive_number(parameters.get("control_period_s"), "control_period_s")
    count_argument = parameters.get("control_steps_argument")
    if not isinstance(count_argument, str) or not count_argument:
        raise MeasurementError("control_steps_argument must be a non-empty string")
    count = _positive_integer(
        _argument(public_arguments, count_argument), count_argument
    )
    if len(samples) < count + 1:
        raise MeasurementError(
            f"trusted evidence contains fewer than {count} control-step samples"
        )
    times = _sample_times(samples)
    start = times[0]
    selected = samples[1 : count + 1]
    tolerance = max(1.0e-9, 0.25 * period)
    for index, sample in enumerate(selected, start=1):
        actual = float(sample["time"])
        expected = start + index * period
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=tolerance):
            raise MeasurementError(
                "trusted samples do not match the declared control period"
            )
    return selected


def _source_horizon(
    binding: Mapping[str, Any],
    temporal: Mapping[str, Any],
    *,
    evidence: Mapping[str, Any],
    public_arguments: Mapping[str, Any],
) -> tuple[int, int, int]:
    maximum_control_steps = _positive_integer(
        temporal.get("max_control_steps"), "max_control_steps"
    )
    parameters = binding.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise MeasurementError("binding parameters must be an object")
    physics_steps_per_control_step = _positive_integer(
        parameters.get("physics_steps_per_control_step"),
        "physics_steps_per_control_step",
    )
    budget_argument = parameters.get("control_steps_argument")
    if not isinstance(budget_argument, str) or not budget_argument:
        raise MeasurementError("control_steps_argument must be text")
    requested = _positive_integer(
        _argument(public_arguments, budget_argument), budget_argument
    )
    if requested != maximum_control_steps:
        raise MeasurementError(
            "public control-step budget differs from the source criterion"
        )
    step_count = evidence.get("step_count")
    if isinstance(step_count, bool) or not isinstance(step_count, int):
        raise MeasurementError("source horizon requires trusted physics step_count")
    return (
        step_count,
        maximum_control_steps,
        maximum_control_steps * physics_steps_per_control_step,
    )


def _contact_step_count(
    evidence: Mapping[str, Any],
    robot_geom_names: set[str],
    object_geom_names: set[str],
) -> int:
    records = evidence.get("contact_pair_step_counts")
    if not isinstance(records, list):
        raise MeasurementError("per-step contact evidence is unavailable")
    total = 0
    for record in records:
        if not isinstance(record, Mapping):
            raise MeasurementError("per-step contact evidence is invalid")
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
            raise MeasurementError("per-step contact evidence is invalid")
        if (geom1 in robot_geom_names and geom2 in object_geom_names) or (
            geom2 in robot_geom_names and geom1 in object_geom_names
        ):
            total += step_count
    return total


def _contacted_group_indices(
    sample: Mapping[str, Any],
    robot_geom_groups: Sequence[set[str]],
    object_geom_names: set[str],
) -> set[int]:
    contacts = sample.get("contacts")
    if not isinstance(contacts, list):
        raise MeasurementError("sample contact evidence is unavailable")
    matched: set[int] = set()
    for contact in contacts:
        if not isinstance(contact, Mapping):
            raise MeasurementError("sample contact evidence is invalid")
        pair = {str(contact.get("geom1")), str(contact.get("geom2"))}
        if not pair.intersection(object_geom_names):
            continue
        matched.update(
            index
            for index, group in enumerate(robot_geom_groups)
            if pair.intersection(group)
        )
    return matched


def _contact_group_transition_count(active_groups: Sequence[set[int]]) -> int:
    return sum(
        1
        for previous, current in zip(active_groups, active_groups[1:])
        if previous and current and previous != current
    )


def _direction_change_count(values: Sequence[float], *, epsilon: float) -> int:
    deltas = [
        float(current) - float(previous)
        for previous, current in zip(values, values[1:])
        if abs(float(current) - float(previous)) > epsilon
    ]
    return sum(
        1 for previous, current in zip(deltas, deltas[1:]) if previous * current < 0.0
    )


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

    reference_contract_id = trusted_reference_contract_id(kind)
    if reference_contract_id is not None:
        request = public_arguments.get("request")
        if not isinstance(request, Mapping):
            raise MeasurementError(
                "trusted reference operator requires request public arguments"
            )
        from .b1_contracts import B1ContractError, evaluate_b1_contract

        try:
            return evaluate_b1_contract(
                {**dict(parameters), "contract_id": reference_contract_id},
                evidence=evidence,
                request=request,
            )
        except B1ContractError as exc:
            raise MeasurementError(str(exc)) from exc

    if kind == "b1_contract":
        request = public_arguments.get("request")
        if not isinstance(request, Mapping):
            raise MeasurementError("B1 contract requires request public arguments")
        from .b1_contracts import B1ContractError, evaluate_b1_contract

        try:
            return evaluate_b1_contract(
                parameters,
                evidence=evidence,
                request=request,
            )
        except B1ContractError as exc:
            raise MeasurementError(str(exc)) from exc

    if kind == "in_hand_object_pattern_success":
        body_name = str(parameters["body_name"])
        reference_body_name = str(parameters["reference_body_name"])
        raw_object_names = parameters.get("object_geom_names")
        raw_groups = parameters.get("required_robot_geom_groups")
        if (
            not isinstance(raw_object_names, Sequence)
            or isinstance(raw_object_names, (str, bytes))
            or not raw_object_names
            or not isinstance(raw_groups, Sequence)
            or isinstance(raw_groups, (str, bytes))
            or not raw_groups
        ):
            raise MeasurementError(
                "in-hand pattern requires object geoms and robot geom groups"
            )
        object_geom_names = {str(name) for name in raw_object_names}
        robot_geom_groups: list[set[str]] = []
        for raw_group in raw_groups:
            if (
                not isinstance(raw_group, Sequence)
                or isinstance(raw_group, (str, bytes))
                or not raw_group
            ):
                raise MeasurementError("in-hand robot geom group must not be empty")
            robot_geom_groups.append({str(name) for name in raw_group})
        minimum_contact_steps = _positive_integer(
            parameters.get("minimum_contact_steps", 1),
            "minimum_contact_steps",
        )
        if any(
            _contact_step_count(evidence, group, object_geom_names)
            < minimum_contact_steps
            for group in robot_geom_groups
        ):
            return 0.0
        active_groups = [
            _contacted_group_indices(sample, robot_geom_groups, object_geom_names)
            for sample in samples
        ]
        minimum_simultaneous_samples = parameters.get(
            "minimum_simultaneous_contact_samples"
        )
        if minimum_simultaneous_samples is not None:
            required_samples = _positive_integer(
                minimum_simultaneous_samples,
                "minimum_simultaneous_contact_samples",
            )
            simultaneous_samples = sum(
                len(active) == len(robot_geom_groups) for active in active_groups
            )
            if simultaneous_samples < required_samples:
                return 0.0
        minimum_transitions = parameters.get("minimum_contact_group_transitions")
        if minimum_transitions is not None and _contact_group_transition_count(
            active_groups
        ) < _positive_integer(
            minimum_transitions, "minimum_contact_group_transitions"
        ):
            return 0.0

        motion_kind = parameters.get("motion_kind")
        if motion_kind in {"translation", "translation_return", "translation_cycle"}:
            axis = int(parameters["axis"])
            if axis not in {0, 1, 2}:
                raise MeasurementError("in-hand translation axis must be 0, 1, or 2")
            coordinates = [
                _body_position_in_frame(sample, body_name, reference_body_name)[axis]
                for sample in samples
            ]
            required_range = _positive_number(
                parameters.get("minimum_translation_range_m"),
                "minimum_translation_range_m",
            )
            if max(coordinates) - min(coordinates) < required_range:
                return 0.0
            direction = parameters.get("translation_direction")
            if direction is not None:
                if isinstance(direction, bool) or direction not in {-1, 1}:
                    raise MeasurementError("translation_direction must be -1 or 1")
                if max(
                    int(direction) * (coordinate - coordinates[0])
                    for coordinate in coordinates
                ) < required_range:
                    return 0.0
            if motion_kind == "translation_return":
                return_tolerance = _positive_number(
                    parameters.get("maximum_return_error_m"),
                    "maximum_return_error_m",
                )
                if abs(coordinates[-1] - coordinates[0]) > return_tolerance:
                    return 0.0
            if motion_kind == "translation_cycle":
                required_changes = _positive_integer(
                    parameters.get("minimum_direction_changes"),
                    "minimum_direction_changes",
                )
                if _direction_change_count(coordinates, epsilon=1.0e-5) < required_changes:
                    return 0.0
            return 1.0

        if motion_kind == "contact_slide":
            axis = int(parameters["axis"])
            if axis not in {0, 1, 2}:
                raise MeasurementError("contact-slide axis must be 0, 1, or 2")
            raw_site_names = parameters.get("site_names")
            if (
                not isinstance(raw_site_names, Sequence)
                or isinstance(raw_site_names, (str, bytes))
                or len(raw_site_names) != len(robot_geom_groups)
            ):
                raise MeasurementError(
                    "contact slide requires one site per robot geom group"
                )
            required_range = _positive_number(
                parameters.get("minimum_site_translation_range_m"),
                "minimum_site_translation_range_m",
            )
            for group_index, site_name in enumerate(raw_site_names):
                coordinates = [
                    _point_in_body_frame(
                        sample,
                        _site_position(sample, str(site_name)),
                        body_name,
                    )[axis]
                    for sample, active in zip(samples, active_groups)
                    if group_index in active
                ]
                if (
                    len(coordinates) < 2
                    or max(coordinates) - min(coordinates) < required_range
                ):
                    return 0.0
            return 1.0

        if motion_kind in {"rotation", "rotation_cycle"}:
            angles = _unwrapped(
                [
                    _yaw_rad(
                        _relative_quaternion(
                            sample, body_name, reference_body_name
                        )
                    )
                    for sample in samples
                ]
            )
            cumulative_deg = math.degrees(
                math.fsum(
                    abs(current - previous)
                    for previous, current in zip(angles, angles[1:])
                )
            )
            minimum_cumulative = _positive_number(
                parameters.get("minimum_cumulative_rotation_deg"),
                "minimum_cumulative_rotation_deg",
            )
            if cumulative_deg < minimum_cumulative:
                return 0.0
            minimum_net = parameters.get("minimum_net_rotation_deg")
            if minimum_net is not None and abs(
                math.degrees(angles[-1] - angles[0])
            ) < _positive_number(minimum_net, "minimum_net_rotation_deg"):
                return 0.0
            if motion_kind == "rotation_cycle":
                required_changes = _positive_integer(
                    parameters.get("minimum_direction_changes"),
                    "minimum_direction_changes",
                )
                if _direction_change_count(angles, epsilon=math.radians(0.1)) < required_changes:
                    return 0.0
            return 1.0

        if motion_kind == "joint":
            positions = [
                _joint_position(sample, str(parameters["joint_name"]))
                for sample in samples
            ]
            required_range = _positive_number(
                parameters.get("minimum_joint_range_rad"),
                "minimum_joint_range_rad",
            )
            return 1.0 if max(positions) - min(positions) >= required_range else 0.0

        raise MeasurementError(f"unsupported in-hand motion kind {motion_kind!r}")

    if kind == "final_concatenated_site_position_error":
        raw_names = parameters.get("site_names")
        if (
            not isinstance(raw_names, Sequence)
            or isinstance(raw_names, (str, bytes))
            or not raw_names
        ):
            raise MeasurementError("concatenated site measurement requires site_names")
        site_names = [str(name) for name in raw_names]
        reference_body_name = str(parameters["reference_body_name"])
        target = _vector(
            _argument(public_arguments, str(parameters["target_argument"])),
            size=3 * len(site_names),
        )
        actual = tuple(
            coordinate
            for site_name in site_names
            for coordinate in _point_in_body_frame(
                final,
                _site_position(final, site_name),
                reference_body_name,
            )
        )
        return _distance(actual, target)

    if kind == "final_body_position_offset_error":
        body_name = str(parameters["body_name"])
        reference_body_name = str(parameters["reference_body_name"])
        initial = _body_position_in_frame(first, body_name, reference_body_name)
        terminal = _body_position_in_frame(final, body_name, reference_body_name)
        actual_offset = tuple(end - start for start, end in zip(initial, terminal))
        target_offset = _vector(
            _argument(public_arguments, str(parameters["target_argument"])), size=3
        )
        actual_orientation = _relative_quaternion(
            final, body_name, reference_body_name
        )
        target_orientation = _normalized_quaternion(
            _argument(
                public_arguments,
                str(parameters["orientation_target_argument"]),
            )
        )
        orientation_cosine = abs(
            math.fsum(
                left * right
                for left, right in zip(actual_orientation, target_orientation)
            )
        )
        orientation_error = 2.0 * math.acos(
            min(1.0, max(0.0, orientation_cosine))
        )
        if orientation_error >= _positive_number(
            parameters.get("maximum_orientation_error_rad"),
            "maximum_orientation_error_rad",
        ):
            raise MeasurementError(
                "same-state block orientation conjunction did not pass"
            )
        return _distance(actual_offset, target_offset)

    if kind == "final_body_quaternion_error":
        actual = _relative_quaternion(
            final,
            str(parameters["body_name"]),
            str(parameters["reference_body_name"]),
        )
        target = _normalized_quaternion(
            _argument(public_arguments, str(parameters["target_argument"]))
        )
        initial_position = _body_position_in_frame(
            first, str(parameters["body_name"]), str(parameters["reference_body_name"])
        )
        final_position = _body_position_in_frame(
            final, str(parameters["body_name"]), str(parameters["reference_body_name"])
        )
        actual_offset = tuple(
            end - start for start, end in zip(initial_position, final_position)
        )
        target_offset = _vector(
            _argument(
                public_arguments,
                str(parameters["position_target_argument"]),
            ),
            size=3,
        )
        if _distance(actual_offset, target_offset) >= _positive_number(
            parameters.get("maximum_position_error_m"),
            "maximum_position_error_m",
        ):
            raise MeasurementError(
                "same-state block position conjunction did not pass"
            )
        cosine = abs(math.fsum(left * right for left, right in zip(actual, target)))
        return 2.0 * math.acos(min(1.0, max(0.0, cosine)))

    if kind == "final_maximum_joint_position_error":
        raw_names = parameters.get("joint_names")
        if (
            not isinstance(raw_names, Sequence)
            or isinstance(raw_names, (str, bytes))
            or not raw_names
        ):
            raise MeasurementError("maximum joint error requires joint_names")
        names = [str(name) for name in raw_names]
        targets = _vector(
            _argument(public_arguments, str(parameters["target_argument"])),
            size=len(names),
        )
        return max(
            abs(_joint_position(final, name) - target)
            for name, target in zip(names, targets)
        )

    if kind == "final_wrapped_joint_position_error":
        actual = _joint_position(final, str(parameters["joint_name"]))
        target = float(
            _argument(public_arguments, str(parameters["target_argument"]))
        )
        if not math.isfinite(target):
            raise MeasurementError("wrapped joint target must be finite")
        return abs((actual - target + math.pi) % (2.0 * math.pi) - math.pi)

    if kind == "maximum_joint_linear_trajectory_error":
        name = str(parameters["joint_name"])
        velocity = float(
            _argument(public_arguments, str(parameters["velocity_argument"]))
        )
        if not math.isfinite(velocity):
            raise MeasurementError("joint target velocity must be finite")
        control_samples = _control_samples(
            parameters,
            evidence=evidence,
            public_arguments=public_arguments,
        )
        trajectory_samples = [first, *control_samples]
        times = _sample_times(trajectory_samples)
        initial = _joint_position(first, name)
        return max(
            abs(
                _joint_position(sample, name)
                - (initial + velocity * (time - times[0]))
            )
            for sample, time in zip(trajectory_samples, times)
        )

    if kind in {"body_target_solved_sample_count", "body_target_drop_event_count"}:
        selected = _control_samples(
            parameters,
            evidence=evidence,
            public_arguments=public_arguments,
        )
        body_name = str(parameters["body_name"])
        reference_body_name = str(parameters["reference_body_name"])
        target = _vector(
            _argument(public_arguments, str(parameters["target_argument"])), size=3
        )
        distances = [
            _distance(
                _body_position_in_frame(sample, body_name, reference_body_name),
                target,
            )
            for sample in selected
        ]
        if kind == "body_target_solved_sample_count":
            solved_threshold = _positive_number(
                parameters.get("solved_distance_m"), "solved_distance_m"
            )
            drop_threshold = _positive_number(
                parameters.get("drop_distance_m"), "drop_distance_m"
            )
            if any(distance > drop_threshold for distance in distances):
                raise MeasurementError(
                    "same-horizon object-hold no-drop conjunction did not pass"
                )
            return float(sum(distance < solved_threshold for distance in distances))
        drop_threshold = _positive_number(
            parameters.get("drop_distance_m"), "drop_distance_m"
        )
        solved_threshold = _positive_number(
            parameters.get("solved_distance_m"), "solved_distance_m"
        )
        solved_count = sum(distance < solved_threshold for distance in distances)
        minimum_solved_steps = parameters.get("minimum_solved_steps")
        if (
            isinstance(minimum_solved_steps, bool)
            or not isinstance(minimum_solved_steps, int)
            or minimum_solved_steps < 0
        ):
            raise MeasurementError(
                "minimum_solved_steps must be a non-negative integer"
            )
        if solved_count <= minimum_solved_steps:
            raise MeasurementError(
                "same-horizon object-hold solved-step conjunction did not pass"
            )
        events = 0
        previously_dropped = False
        for distance in distances:
            dropped = distance > drop_threshold
            if dropped and not previously_dropped:
                events += 1
            previously_dropped = dropped
        return float(events)

    if kind == "mean_two_body_orbit_tracking_fraction":
        selected = _control_samples(
            parameters,
            evidence=evidence,
            public_arguments=public_arguments,
        )
        raw_names = parameters.get("body_names")
        if (
            not isinstance(raw_names, Sequence)
            or isinstance(raw_names, (str, bytes))
            or len(raw_names) != 2
        ):
            raise MeasurementError("two-body orbit requires exactly two body names")
        names = [str(name) for name in raw_names]
        reference_body_name = str(parameters["reference_body_name"])
        center = _vector(parameters.get("orbit_center"), size=3)
        radii = _vector(
            _argument(public_arguments, str(parameters["radii_argument"])), size=2
        )
        period = _positive_number(
            _argument(public_arguments, str(parameters["period_argument"])),
            "orbit period",
        )
        initial_phase = float(parameters["initial_phase_rad"])
        if not math.isfinite(initial_phase):
            raise MeasurementError("initial_phase_rad must be finite")
        maximum_error = _positive_number(
            parameters.get("maximum_tracking_error_m"),
            "maximum_tracking_error_m",
        )
        height_offset = float(parameters.get("source_height_offset_m", 0.0))
        minimum_height = float(parameters["minimum_source_height"])
        if not math.isfinite(height_offset) or not math.isfinite(minimum_height):
            raise MeasurementError("source-frame height parameters must be finite")
        start_time = float(_samples(evidence)[0]["time"])
        successful = 0
        for sample in selected:
            phase = (
                initial_phase
                + 2.0 * math.pi * (float(sample["time"]) - start_time) / period
            )
            targets = (
                (
                    center[0] + radii[0] * math.cos(phase),
                    center[1] + radii[1] * math.sin(phase),
                    center[2],
                ),
                (
                    center[0] + radii[0] * math.cos(phase + math.pi),
                    center[1] + radii[1] * math.sin(phase + math.pi),
                    center[2],
                ),
            )
            actual = [
                _body_position_in_frame(sample, name, reference_body_name)
                for name in names
            ]
            if all(
                _distance(position, target) < maximum_error
                and position[2] + height_offset >= minimum_height
                for position, target in zip(actual, targets)
            ):
                successful += 1
        return successful / len(selected)

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
    if kind == "final_body_xyz_position_error":
        actual = _body_position(final, str(parameters["body_name"]))
        target = tuple(
            _finite_number(
                _argument(public_arguments, str(parameters[field])),
                field,
            )
            for field in (
                "target_x_argument",
                "target_y_argument",
                "target_z_argument",
            )
        )
        return _distance(actual, target)
    if kind in {
        "final_site_frame_xyz_position_error",
        "final_body_frame_xyz_position_error",
    }:
        reference_body_name = str(parameters["reference_body_name"])
        if kind == "final_site_frame_xyz_position_error":
            actual = _site_position_in_frame(
                final, str(parameters["site_name"]), reference_body_name
            )
        else:
            actual = _body_position_in_frame(
                final, str(parameters["body_name"]), reference_body_name
            )
        target = tuple(
            _finite_number(
                _argument(public_arguments, str(parameters[field])),
                field,
            )
            for field in (
                "target_x_argument",
                "target_y_argument",
                "target_z_argument",
            )
        )
        return _distance(actual, target)
    if kind == "body_planar_target_error":
        actual = _body_position(final, str(parameters["body_name"]))
        target = _vector(
            _argument(public_arguments, str(parameters["target_argument"])),
            size=3,
        )
        return _distance(actual[:2], target[:2])
    if kind == "final_joint_position_error":
        actual = _finite_number(
            _joint_position(final, str(parameters["joint_name"])),
            "terminal joint position",
        )
        request_value = _finite_number(
            _argument(public_arguments, str(parameters["target_argument"])),
            "joint target request value",
        )
        target_scale = _finite_number(
            parameters.get("target_scale", 1.0), "target_scale"
        )
        target_offset = _finite_number(
            parameters.get("target_offset", 0.0), "target_offset"
        )
        target = request_value * target_scale + target_offset
        if not math.isfinite(target):
            raise MeasurementError("scaled joint target must be finite")
        return abs(actual - target)
    if kind == "final_joint_displacement_error":
        joint_name = str(parameters["joint_name"])
        actual = _finite_number(
            _joint_position(final, joint_name) - _joint_position(first, joint_name),
            "terminal joint displacement",
        )
        target = _finite_number(
            _argument(
                public_arguments,
                str(parameters["target_displacement_argument"]),
            ),
            "joint displacement request value",
        )
        return abs(actual - target)
    if kind in {"final_geom_pair_distance_error", "final_geom_pair_distance"}:
        actual_distance = _trusted_geom_pair_distance(
            final,
            str(parameters["geom_a_name"]),
            str(parameters["geom_b_name"]),
        )
        if kind == "final_geom_pair_distance":
            return actual_distance
        target = _finite_number(
            _argument(public_arguments, str(parameters["target_argument"])),
            "geom-pair distance target request value",
        )
        return abs(actual_distance - target)
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
    if kind == "accumulated_body_arc_angle_error":
        body_name = str(parameters["body_name"])
        center = tuple(
            _finite_number(
                _argument(public_arguments, str(parameters[field])),
                field,
            )
            for field in (
                "center_x_argument",
                "center_y_argument",
                "center_z_argument",
            )
        )
        axis_name = _argument(
            public_arguments, str(parameters["axis_argument"])
        )
        axes = {
            "x": (1.0, 0.0, 0.0),
            "y": (0.0, 1.0, 0.0),
            "z": (0.0, 0.0, 1.0),
        }
        if not isinstance(axis_name, str) or axis_name not in axes:
            raise MeasurementError("arc axis request must be x, y, or z")
        accumulated = _accumulated_arc_angle(
            [_body_position(sample, body_name) for sample in samples],
            center=center,
            axis=axes[str(axis_name)],
        )
        target = _finite_number(
            _argument(
                public_arguments, str(parameters["target_angle_argument"])
            ),
            "target_angle_argument",
        )
        return abs(accumulated - target)
    if kind in {
        "final_body_directional_displacement_error",
        "final_site_directional_displacement_error",
    }:
        direction = _declared_unit_vector(
            tuple(
                _finite_number(
                    _argument(public_arguments, str(parameters[field])),
                    field,
                )
                for field in (
                    "direction_x_argument",
                    "direction_y_argument",
                    "direction_z_argument",
                )
            ),
            name="displacement direction",
        )
        if kind == "final_site_directional_displacement_error":
            site_name = str(parameters["site_name"])
            start = _site_position(first, site_name)
            end = _site_position(final, site_name)
        else:
            body_name = str(parameters["body_name"])
            start = _body_position(first, body_name)
            end = _body_position(final, body_name)
        displacement = tuple(
            end_coordinate - start_coordinate
            for start_coordinate, end_coordinate in zip(start, end)
        )
        actual = _dot(displacement, direction)
        target = _finite_number(
            _argument(
                public_arguments, str(parameters["target_distance_argument"])
            ),
            "target_distance_argument",
        )
        return abs(actual - target)
    if kind in {
        "site_frame_xyz_directional_displacement",
        "body_frame_xyz_directional_displacement",
    }:
        reference_body_name = str(parameters["reference_body_name"])
        direction = _declared_unit_vector(
            tuple(
                _finite_number(
                    _argument(public_arguments, str(parameters[field])), field
                )
                for field in (
                    "direction_x_argument",
                    "direction_y_argument",
                    "direction_z_argument",
                )
            ),
            name="site displacement direction",
        )
        if kind == "site_frame_xyz_directional_displacement":
            start = _site_position_in_frame(
                first, str(parameters["site_name"]), reference_body_name
            )
            end = _site_position_in_frame(
                final, str(parameters["site_name"]), reference_body_name
            )
        else:
            start = _body_position_in_frame(
                first, str(parameters["body_name"]), reference_body_name
            )
            end = _body_position_in_frame(
                final, str(parameters["body_name"]), reference_body_name
            )
        return _dot(
            tuple(
                end_coordinate - start_coordinate
                for start_coordinate, end_coordinate in zip(start, end)
            ),
            direction,
        )
    if kind == "accumulated_site_frame_axis_arc_angle_error":
        site_name = str(parameters["site_name"])
        reference_body_name = str(parameters["reference_body_name"])
        center = tuple(
            _finite_number(
                _argument(public_arguments, str(parameters[field])), field
            )
            for field in (
                "center_x_argument",
                "center_y_argument",
                "center_z_argument",
            )
        )
        axis = _declared_unit_vector(
            tuple(
                _finite_number(
                    _argument(public_arguments, str(parameters[field])), field
                )
                for field in (
                    "axis_x_argument",
                    "axis_y_argument",
                    "axis_z_argument",
                )
            ),
            name="site arc axis",
        )
        accumulated = _accumulated_arc_angle(
            [
                _site_position_in_frame(sample, site_name, reference_body_name)
                for sample in samples
            ],
            center=center,
            axis=axis,
        )
        target = _finite_number(
            _argument(public_arguments, str(parameters["target_angle_argument"])),
            "target_angle_argument",
        )
        return abs(accumulated - target)
    if kind == "body_directional_progress_until_corridor_exit":
        body_name = str(parameters["body_name"])
        start = _body_position(first, body_name)
        direction = float(
            _argument(public_arguments, str(parameters["direction_argument"]))
        )
        distance_limit = float(
            _argument(public_arguments, str(parameters["limit_argument"]))
        )
        cross_track_limit = float(parameters["maximum_cross_track_m"])
        minimum_height = float(parameters["minimum_height_m"])
        if not all(
            math.isfinite(value)
            for value in (
                direction,
                distance_limit,
                cross_track_limit,
                minimum_height,
            )
        ):
            raise MeasurementError("bounded directional progress parameters must be finite")
        if distance_limit <= 0.0 or cross_track_limit <= 0.0:
            raise MeasurementError(
                "bounded directional progress limits must be positive"
            )

        forward = (math.cos(direction), math.sin(direction))
        lateral = (-forward[1], forward[0])
        maximum_progress = 0.0
        for sample in samples:
            position = _body_position(sample, body_name)
            displacement = (position[0] - start[0], position[1] - start[1])
            cross_track = displacement[0] * lateral[0] + displacement[1] * lateral[1]
            if abs(cross_track) > cross_track_limit or position[2] < minimum_height:
                break
            progress = displacement[0] * forward[0] + displacement[1] * forward[1]
            maximum_progress = max(maximum_progress, progress)
        return min(maximum_progress, distance_limit)
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
    "final_body_xyz_position_error",
    "final_site_frame_xyz_position_error",
    "final_body_frame_xyz_position_error",
    "body_planar_target_error",
    "final_joint_position_error",
    "final_joint_displacement_error",
    "final_geom_pair_distance_error",
    "final_geom_pair_distance",
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

    if kind == "fixed_trials":
        value = measure(
            binding, evidence=evidence, public_arguments=public_arguments
        )
        if not math.isclose(value, 0.0, rel_tol=0.0, abs_tol=1.0e-9) and not math.isclose(
            value, 1.0, rel_tol=0.0, abs_tol=1.0e-9
        ):
            raise MeasurementError(
                "fixed_trials requires one trusted binary outcome per repetition"
            )
        return {"kind": kind, "passed": True, "value": value}

    if kind == "terminal_step":
        step_count, maximum_control_steps, maximum_physics_steps = _source_horizon(
            binding,
            temporal,
            evidence=evidence,
            public_arguments=public_arguments,
        )
        times = _sample_times(samples)
        elapsed = times[-1] - times[0]
        duration_value = temporal.get("duration_s")
        duration_complete = True
        if duration_value is not None:
            duration_s = _positive_number(duration_value, "duration_s")
            duration_complete = elapsed + 1.0e-9 >= duration_s
        return {
            "kind": kind,
            "passed": (
                step_count == maximum_physics_steps and duration_complete
            ),
            "value": measure(
                binding, evidence=evidence, public_arguments=public_arguments
            ),
            "physics_step_count": step_count,
            "maximum_physics_steps": maximum_physics_steps,
            "valid_duration_s": elapsed,
        }

    if kind == "fixed_horizon":
        step_count, maximum_control_steps, maximum_physics_steps = _source_horizon(
            binding,
            temporal,
            evidence=evidence,
            public_arguments=public_arguments,
        )
        parameters = binding.get("parameters", {})
        if not isinstance(parameters, Mapping):
            raise MeasurementError("binding parameters must be an object")
        period = _positive_number(
            parameters.get("control_period_s"), "control_period_s"
        )
        times = _sample_times(samples)
        elapsed = times[-1] - times[0]
        required = maximum_control_steps * period
        if (
            step_count != maximum_physics_steps
            or elapsed + max(1.0e-9, 0.25 * period) < required
        ):
            return {
                "kind": kind,
                "passed": False,
                "value": None,
                "physics_step_count": step_count,
                "required_physics_steps": maximum_physics_steps,
                "valid_duration_s": elapsed,
                "required_duration_s": required,
            }
        return {
            "kind": kind,
            "passed": True,
            "value": measure(
                binding, evidence=evidence, public_arguments=public_arguments
            ),
            "physics_step_count": step_count,
            "required_physics_steps": maximum_physics_steps,
            "valid_duration_s": elapsed,
            "required_duration_s": required,
        }

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
        horizon_complete = True
        horizon_evidence: dict[str, Any] = {}
        if "max_control_steps" in temporal:
            step_count, _, maximum_physics_steps = _source_horizon(
                binding,
                temporal,
                evidence=evidence,
                public_arguments=public_arguments,
            )
            horizon_complete = step_count == maximum_physics_steps
            horizon_evidence = {
                "physics_step_count": step_count,
                "required_physics_steps": maximum_physics_steps,
            }
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
                **horizon_evidence,
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
            "passed": state_passed and horizon_complete,
            "value": value,
            "valid_duration_s": valid_duration,
            "required_duration_s": required,
            **horizon_evidence,
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
    if kind == "all_trials":
        if any(
            not (
                math.isclose(value, 0.0, rel_tol=0.0, abs_tol=1.0e-9)
                or math.isclose(value, 1.0, rel_tol=0.0, abs_tol=1.0e-9)
            )
            for value in numeric_values
        ):
            return {"kind": kind, "passed": False, "value": None}
        successful_trial_count = math.fsum(numeric_values)
        passed = all(temporal_passes) and compare(
            successful_trial_count,
            comparator=comparator,
            threshold=threshold,
        )
        return {
            "kind": kind,
            "passed": passed,
            "value": successful_trial_count,
        }
    if kind == "single_trial":
        if len(numeric_values) != 1:
            return {"kind": kind, "passed": False, "value": None}
        passed = bool(temporal_passes[0]) and compare(
            numeric_values[0], comparator=comparator, threshold=threshold
        )
        return {"kind": kind, "passed": passed, "value": numeric_values[0]}
    if kind in {
        "all_four_fingertips",
        "same_state_conjunction",
        "maximum_over_all_16_joints",
        "maximum_over_control_steps",
        "count_successful_steps",
        "count_events",
        "mean_over_control_steps",
    }:
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
        elif kind == "control_range":
            outcomes[guard_id] = bool(
                evidence.get("control_range_monitoring_complete")
            ) and not bool(evidence.get("control_range_violation_detected"))
        elif kind == "complete_video":
            outcomes[guard_id] = bool(video.get("complete"))
        elif kind == "named_geom_contact_pair_required":
            robot_geom_name = guard.get("robot_geom_name")
            robot_geom_names = guard.get("robot_geom_names")
            task_geom_names = guard.get("task_geom_names")
            minimum_steps = guard.get("minimum_steps")
            records = evidence.get("contact_pair_step_counts")
            outcome = False
            has_robot_geom_name = "robot_geom_name" in guard
            has_robot_geom_names = "robot_geom_names" in guard
            robot_names: set[str] = set()
            if has_robot_geom_name != has_robot_geom_names:
                if isinstance(robot_geom_name, str) and robot_geom_name.strip():
                    robot_names.add(robot_geom_name)
                elif (
                    isinstance(robot_geom_names, list)
                    and robot_geom_names
                    and all(
                        isinstance(name, str) and bool(name.strip())
                        for name in robot_geom_names
                    )
                ):
                    robot_names.update(robot_geom_names)
            if (
                robot_names
                and isinstance(task_geom_names, list)
                and task_geom_names
                and all(isinstance(name, str) and bool(name.strip()) for name in task_geom_names)
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
                    if (geom1 in robot_names and geom2 in task_names) or (
                        geom2 in robot_names and geom1 in task_names
                    ):
                        contact_steps += step_count
                outcome = valid_records and contact_steps >= minimum_steps
            outcomes[guard_id] = outcome
        elif kind == "named_joints_remain_near_reset":
            tolerances = guard.get("joint_tolerances")
            deviations = evidence.get("joint_max_abs_deviation_from_reset")
            outcome = False
            if (
                isinstance(tolerances, Mapping)
                and tolerances
                and isinstance(deviations, Mapping)
            ):
                try:
                    outcome = all(
                        isinstance(name, str)
                        and bool(name.strip())
                        and not isinstance(tolerance, bool)
                        and math.isfinite(float(tolerance))
                        and float(tolerance) > 0.0
                        and name in deviations
                        and not isinstance(deviations[name], bool)
                        and math.isfinite(float(deviations[name]))
                        and 0.0 <= float(deviations[name]) <= float(tolerance)
                        for name, tolerance in tolerances.items()
                    )
                except (TypeError, ValueError):
                    outcome = False
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
