"""Trusted binary measurements for the fixed Experiment 1 B1 contracts.

The candidate process never receives this module or any contract threshold.
Every outcome below is derived from Framework-recorded MuJoCo samples and
contacts.  Missing symbols or evidence are errors, never candidate-reported
successes.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class B1ContractError(ValueError):
    """Raised when a B1 contract cannot be evaluated from trusted evidence."""


SUPPORTED_CONTRACT_IDS = frozenset(
    {
        *(f"A{index}" for index in range(1, 7)),
        *(f"G{index}" for index in range(1, 6)),
        *(f"L{index}" for index in range(1, 7)),
        *(f"ST{index}" for index in range(1, 9)),
        *(f"AL{index}" for index in range(1, 7)),
    }
)


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise B1ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise B1ContractError(f"{name} must be finite")
    return result


def _vector(value: Any, size: int, name: str) -> tuple[float, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != size
    ):
        raise B1ContractError(f"{name} must contain {size} numbers")
    return tuple(_number(item, name) for item in value)


def _vectors(value: Any, size: int, name: str) -> list[tuple[float, ...]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise B1ContractError(f"{name} must be an array")
    result = [_vector(item, size, name) for item in value]
    if not result:
        raise B1ContractError(f"{name} must not be empty")
    return result


def _name(parameters: Mapping[str, Any], key: str) -> str:
    value = parameters.get(key)
    if not isinstance(value, str) or not value:
        raise B1ContractError(f"B1 binding requires {key}")
    return value


def _names(parameters: Mapping[str, Any], key: str) -> list[str]:
    value = parameters.get(key)
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise B1ContractError(f"B1 binding requires non-empty {key}")
    return [str(item) for item in value]


def _mapping(parameters: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parameters.get(key)
    if not isinstance(value, Mapping) or not value:
        raise B1ContractError(f"B1 binding requires non-empty {key}")
    return value


def _limit(parameters: Mapping[str, Any], key: str, default: float) -> float:
    return _number(parameters.get(key, default), key)


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise B1ContractError("cannot compare vectors of different lengths")
    return math.sqrt(math.fsum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))


def _sub(left: Sequence[float], right: Sequence[float]) -> tuple[float, ...]:
    return tuple(float(a) - float(b) for a, b in zip(left, right))


def _add(left: Sequence[float], right: Sequence[float]) -> tuple[float, ...]:
    return tuple(float(a) + float(b) for a, b in zip(left, right))


def _scale(value: Sequence[float], factor: float) -> tuple[float, ...]:
    return tuple(float(item) * factor for item in value)


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return math.fsum(float(a) * float(b) for a, b in zip(left, right))


def _norm(value: Sequence[float]) -> float:
    return math.sqrt(math.fsum(float(item) ** 2 for item in value))


def _unit(value: Sequence[float], name: str) -> tuple[float, ...]:
    length = _norm(value)
    if length <= 0.0:
        raise B1ContractError(f"{name} must be non-zero")
    return tuple(float(item) / length for item in value)


def _normal_quaternion(value: Any) -> tuple[float, float, float, float]:
    raw = _vector(value, 4, "quaternion")
    length = _norm(raw)
    if length <= 0.0:
        raise B1ContractError("quaternion must be non-zero")
    return tuple(item / length for item in raw)  # type: ignore[return-value]


def _rotate(quaternion: Sequence[float], vector: Sequence[float]) -> tuple[float, float, float]:
    w, x, y, z = _normal_quaternion(quaternion)
    vx, vy, vz = _vector(vector, 3, "vector")
    return (
        (1 - 2 * (y * y + z * z)) * vx + 2 * (x * y - w * z) * vy + 2 * (x * z + w * y) * vz,
        2 * (x * y + w * z) * vx + (1 - 2 * (x * x + z * z)) * vy + 2 * (y * z - w * x) * vz,
        2 * (x * z - w * y) * vx + 2 * (y * z + w * x) * vy + (1 - 2 * (x * x + y * y)) * vz,
    )


def _rotate_inverse(
    quaternion: Sequence[float], vector: Sequence[float]
) -> tuple[float, float, float]:
    w, x, y, z = _normal_quaternion(quaternion)
    return _rotate((w, -x, -y, -z), vector)


def _yaw(quaternion: Sequence[float]) -> float:
    w, x, y, z = _normal_quaternion(quaternion)
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def _roll_pitch(quaternion: Sequence[float]) -> tuple[float, float]:
    w, x, y, z = _normal_quaternion(quaternion)
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    sine = max(-1.0, min(1.0, 2 * (w * y - z * x)))
    return roll, math.asin(sine)


def _wrapped(value: float) -> float:
    return (float(value) + math.pi) % (2 * math.pi) - math.pi


def _point_segment_distance(
    point: Sequence[float], start: Sequence[float], end: Sequence[float]
) -> float:
    segment = _sub(end, start)
    denominator = _dot(segment, segment)
    if denominator <= 1.0e-18:
        return _distance(point, start)
    fraction = max(0.0, min(1.0, _dot(_sub(point, start), segment) / denominator))
    projection = _add(start, _scale(segment, fraction))
    return _distance(point, projection)


@dataclass(frozen=True)
class _Context:
    parameters: Mapping[str, Any]
    evidence: Mapping[str, Any]
    request: Mapping[str, Any]
    samples: tuple[Mapping[str, Any], ...]
    times: tuple[float, ...]

    @classmethod
    def build(
        cls,
        parameters: Mapping[str, Any],
        evidence: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> _Context:
        raw_samples = evidence.get("samples")
        if (
            not isinstance(raw_samples, list)
            or len(raw_samples) < 2
            or not all(isinstance(item, Mapping) for item in raw_samples)
        ):
            raise B1ContractError("B1 contract requires trusted time-series samples")
        times = tuple(_number(sample.get("time"), "sample time") for sample in raw_samples)
        if any(current < previous for previous, current in zip(times, times[1:])):
            raise B1ContractError("trusted sample times must be non-decreasing")
        return cls(parameters, evidence, request, tuple(raw_samples), times)

    def site(self, index: int, name: str) -> tuple[float, float, float]:
        values = self.samples[index].get("site_positions")
        if not isinstance(values, Mapping) or name not in values:
            raise B1ContractError(f"trusted site {name!r} is unavailable")
        return _vector(values[name], 3, f"site {name}")  # type: ignore[return-value]

    def point(self, index: int, name: str) -> tuple[float, float, float]:
        """Resolve a tracked point as a named site, then a body origin."""

        values = self.samples[index].get("site_positions")
        if isinstance(values, Mapping) and name in values:
            return _vector(values[name], 3, f"site {name}")  # type: ignore[return-value]
        return self.body(index, name)

    def body(self, index: int, name: str) -> tuple[float, float, float]:
        values = self.samples[index].get("body_positions")
        if not isinstance(values, Mapping) or name not in values:
            raise B1ContractError(f"trusted body {name!r} is unavailable")
        return _vector(values[name], 3, f"body {name}")  # type: ignore[return-value]

    def quaternion(self, index: int, name: str) -> tuple[float, float, float, float]:
        values = self.samples[index].get("body_quaternions")
        if not isinstance(values, Mapping) or name not in values:
            raise B1ContractError(f"trusted body quaternion {name!r} is unavailable")
        return _normal_quaternion(values[name])

    def joint(self, index: int, name: str) -> float:
        values = self.samples[index].get("joint_positions")
        if not isinstance(values, Mapping) or name not in values:
            raise B1ContractError(f"trusted joint {name!r} is unavailable")
        return _number(values[name], f"joint {name}")

    def joint_velocity(self, index: int, name: str) -> float:
        values = self.samples[index].get("joint_velocities")
        if not isinstance(values, Mapping) or name not in values:
            raise B1ContractError(f"trusted joint velocity {name!r} is unavailable")
        return _number(values[name], f"joint velocity {name}")

    def in_body_frame(
        self, index: int, point: Sequence[float], body_name: str
    ) -> tuple[float, float, float]:
        return _rotate_inverse(
            self.quaternion(index, body_name), _sub(point, self.body(index, body_name))
        )

    def contacts(self, index: int) -> tuple[tuple[str, str, float], ...]:
        raw = self.samples[index].get("contacts")
        if not isinstance(raw, list):
            raise B1ContractError("trusted sample contacts are unavailable")
        result: list[tuple[str, str, float]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise B1ContractError("trusted sample contact is invalid")
            geom1 = item.get("geom1")
            geom2 = item.get("geom2")
            if not isinstance(geom1, str) or not isinstance(geom2, str):
                raise B1ContractError("trusted contact geom names are invalid")
            result.append((geom1, geom2, _number(item.get("distance", 0.0), "contact distance")))
        return tuple(result)


def _request(ctx: _Context, key: str) -> Any:
    if key not in ctx.request:
        raise B1ContractError(f"B1 request is missing {key}")
    return ctx.request[key]


def _window(
    ctx: _Context,
    predicate: Callable[[int], bool],
    duration_s: float,
    *,
    after: int = 0,
) -> tuple[int, int] | None:
    start: int | None = None
    for index in range(max(0, after), len(ctx.samples)):
        if predicate(index):
            if start is None:
                start = index
            if ctx.times[index] - ctx.times[start] + 1.0e-12 >= duration_s:
                return start, index
        else:
            start = None
    return None


def _final_hold(
    ctx: _Context, predicate: Callable[[int], bool], duration_s: float
) -> bool:
    end = len(ctx.samples) - 1
    if not predicate(end):
        return False
    start = end
    while start > 0 and predicate(start - 1):
        start -= 1
    return ctx.times[end] - ctx.times[start] + 1.0e-12 >= duration_s


def _entry_within_request_budget(
    ctx: _Context,
    index: int,
    key: str,
    *,
    after_index: int = 0,
) -> bool:
    """Require a trusted phase entry to occur within its public time budget."""

    budget = _number(_request(ctx, key), key)
    return ctx.times[index] - ctx.times[after_index] <= budget + 1.0e-9


def _requested_duration_context(
    ctx: _Context, key: str = "duration_s"
) -> _Context | None:
    """Bound fixed-duration scoring to the requested physical-time horizon."""

    duration = _number(_request(ctx, key), key)
    raw_timestep = ctx.evidence.get("physics_timestep_s", 0.0)
    timestep = _number(raw_timestep, "physics_timestep_s")
    if timestep < 0.0:
        raise B1ContractError("physics_timestep_s must be non-negative")
    elapsed = ctx.times[-1] - ctx.times[0]
    if elapsed + timestep + 1.0e-9 < duration:
        return None
    if elapsed > duration + timestep + 1.0e-9:
        return None
    horizon = ctx.times[0] + duration + timestep + 1.0e-9
    end = max(index for index, time in enumerate(ctx.times) if time <= horizon)
    return _Context(
        ctx.parameters,
        ctx.evidence,
        ctx.request,
        ctx.samples[: end + 1],
        ctx.times[: end + 1],
    )


def _ordered_entries(
    ctx: _Context,
    positions: Sequence[Sequence[float]],
    targets: Sequence[Sequence[float]],
    tolerance: float,
    *,
    start_index: int = 0,
) -> list[int] | None:
    completed: list[int] = []
    target_index = 0
    for index in range(start_index, len(positions)):
        if target_index == len(targets):
            break
        if _distance(positions[index], targets[target_index]) <= tolerance:
            completed.append(index)
            target_index += 1
    return completed if target_index == len(targets) else None


def _path_ok(
    positions: Sequence[Sequence[float]],
    points: Sequence[Sequence[float]],
    tolerance: float,
    *,
    through_index: int | None = None,
) -> bool:
    if len(points) < 2:
        raise B1ContractError("path contract requires at least two points")
    selected = positions if through_index is None else positions[: through_index + 1]
    return all(
        min(
            _point_segment_distance(position, start, end)
            for start, end in zip(points, points[1:])
        )
        <= tolerance
        for position in selected
    )


def _sample_speed(
    ctx: _Context, positions: Sequence[Sequence[float]], index: int
) -> float:
    if index <= 0:
        return 0.0
    elapsed = ctx.times[index] - ctx.times[index - 1]
    if elapsed <= 0.0:
        return math.inf
    return _distance(positions[index], positions[index - 1]) / elapsed


def _target_in_initial_yaw_frame(
    ctx: _Context, body_name: str, translation: Sequence[float]
) -> tuple[float, float]:
    start = ctx.body(0, body_name)
    yaw = _yaw(ctx.quaternion(0, body_name))
    x, y = _vector(translation, 2, "planar translation")
    return (
        start[0] + math.cos(yaw) * x - math.sin(yaw) * y,
        start[1] + math.sin(yaw) * x + math.cos(yaw) * y,
    )


def _aperture(ctx: _Context, index: int, *, arm: str | None = None) -> float:
    if arm is None:
        names = [
            _name(ctx.parameters, "joint_name")
        ] if "joint_name" in ctx.parameters else _names(ctx.parameters, "joint_names")
    else:
        mapping = _mapping(ctx.parameters, "arm_gripper_joint_names")
        raw = mapping.get(arm)
        if isinstance(raw, str):
            names = [raw]
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and raw:
            names = [str(item) for item in raw]
        else:
            raise B1ContractError(f"gripper symbols are missing for arm {arm}")
    values = [ctx.joint(index, name) for name in names]
    # Mirrored ALOHA fingers have opposite signs in some MJCFs.  A binding can
    # explicitly select signed scales; otherwise their absolute mean is the
    # trusted physical aperture proxy.
    scales = ctx.parameters.get("joint_scales")
    if isinstance(scales, Sequence) and not isinstance(scales, (str, bytes)):
        if len(scales) != len(values):
            raise B1ContractError("joint_scales must match aperture joints")
        return math.fsum(value * _number(scale, "joint scale") for value, scale in zip(values, scales)) / len(values)
    if len(values) == 1:
        return values[0]
    return math.fsum(abs(value) for value in values) / len(values)


def _aperture_contract(
    ctx: _Context,
    *,
    error_limit: float,
    hold_s: float,
    normalized: bool,
    minimum_excursion: float | None,
    arm: str | None = None,
    mirror_limit: float | None = None,
) -> bool:
    target_fraction = _number(_request(ctx, "opening_fraction"), "opening_fraction")
    closed = _limit(ctx.parameters, "closed_position", 0.0)
    opened = _limit(ctx.parameters, "open_position", 1.0)
    travel = abs(opened - closed)
    if travel <= 0.0:
        raise B1ContractError("gripper travel must be positive")
    target = closed + target_fraction * (opened - closed)

    def passed(index: int) -> bool:
        error = abs(_aperture(ctx, index, arm=arm) - target)
        if normalized:
            error /= travel
        if error > error_limit:
            return False
        if mirror_limit is not None and arm is not None:
            raw = _mapping(ctx.parameters, "arm_gripper_joint_names").get(arm)
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != 2:
                raise B1ContractError("mirrored gripper contract requires two joint names")
            disagreement = abs(abs(ctx.joint(index, str(raw[0]))) - abs(ctx.joint(index, str(raw[1])))) / travel
            return disagreement <= mirror_limit
        return True

    target_window = _window(ctx, passed, hold_s)
    if target_window is None:
        return False
    if "max_duration_s" in ctx.request and not _entry_within_request_budget(
        ctx, target_window[0], "max_duration_s"
    ):
        return False
    if minimum_excursion is not None:
        values = [_aperture(ctx, index, arm=arm) for index in range(len(ctx.samples))]
        if max(values) - min(values) + 1.0e-12 < minimum_excursion:
            return False
    return True


def _joint_drift_within(
    ctx: _Context, names_and_limits: Mapping[str, float]
) -> bool:
    extrema = ctx.evidence.get("joint_max_abs_deviation_from_reset")
    if isinstance(extrema, Mapping) and all(name in extrema for name in names_and_limits):
        return all(
            _number(extrema[name], f"joint drift {name}") <= limit + 1.0e-12
            for name, limit in names_and_limits.items()
        )
    starts = {name: ctx.joint(0, name) for name in names_and_limits}
    return all(
        abs(ctx.joint(index, name) - starts[name]) <= limit + 1.0e-12
        for index in range(len(ctx.samples))
        for name, limit in names_and_limits.items()
    )


def _point_drift_within(
    ctx: _Context, name: str, maximum_displacement_m: float
) -> bool:
    for field in (
        "site_max_displacement_from_reset",
        "body_max_displacement_from_reset",
    ):
        extrema = ctx.evidence.get(field)
        if isinstance(extrema, Mapping) and name in extrema:
            return (
                _number(extrema[name], f"point drift {name}")
                <= maximum_displacement_m + 1.0e-12
            )
    start = ctx.point(0, name)
    return all(
        _distance(ctx.point(index, name), start)
        <= maximum_displacement_m + 1.0e-12
        for index in range(len(ctx.samples))
    )


def _so101_side_effects(ctx: _Context, contract_id: str) -> bool:
    if contract_id in {"A1", "A2", "A4", "A5"}:
        gripper_travel = 1.74533 - (-0.17453)
        return _joint_drift_within(ctx, {"gripper": 0.05 * gripper_travel})
    if contract_id == "A3":
        return _point_drift_within(ctx, "gripperframe", 0.015) and _joint_drift_within(
            ctx,
            {
                "shoulder_pan": 0.03,
                "shoulder_lift": 0.03,
                "elbow_flex": 0.03,
                "wrist_flex": 0.03,
                "wrist_roll": 0.03,
            },
        )
    if contract_id == "A6":
        return _joint_drift_within(
            ctx,
            {
                "shoulder_pan": 0.03,
                "shoulder_lift": 0.03,
                "elbow_flex": 0.03,
                "wrist_flex": 0.03,
                "gripper": 0.03,
            },
        )
    return True


_ALOHA_ARM_JOINT_LIMITS = {
    "waist": 0.002,
    "shoulder": 0.003,
    "elbow": 0.030,
    "forearm_roll": 0.002,
    "wrist_angle": 0.035,
    "wrist_rotate": 0.002,
}


def _aloha_arm_joint_limits(arm: str) -> dict[str, float]:
    return {
        f"{arm}/{suffix}": limit
        for suffix, limit in _ALOHA_ARM_JOINT_LIMITS.items()
    }


def _aloha_gripper_limits(arm: str) -> dict[str, float]:
    return {
        f"{arm}/left_finger": 0.0005,
        f"{arm}/right_finger": 0.0005,
    }


def _aloha_static_arm(ctx: _Context, arm: str) -> bool:
    return (
        _point_drift_within(ctx, f"{arm}/gripper", 0.015)
        and _joint_drift_within(ctx, _aloha_arm_joint_limits(arm))
        and _joint_drift_within(ctx, _aloha_gripper_limits(arm))
    )


def _aloha_side_effects(ctx: _Context, contract_id: str) -> bool:
    if contract_id in {"AL1", "AL2", "AL3", "AL5"}:
        arm = str(_request(ctx, "arm"))
        if arm not in {"left", "right"}:
            raise B1ContractError(f"unknown ALOHA arm {arm!r}")
        other = "right" if arm == "left" else "left"
        if not _aloha_static_arm(ctx, other):
            return False
        if contract_id == "AL3":
            return _point_drift_within(
                ctx, f"{arm}/gripper", 0.015
            ) and _joint_drift_within(ctx, _aloha_arm_joint_limits(arm))
        return _joint_drift_within(ctx, _aloha_gripper_limits(arm))
    if contract_id in {"AL4", "AL6"}:
        return all(
            _joint_drift_within(ctx, _aloha_gripper_limits(arm))
            for arm in ("left", "right")
        )
    return True


def _side_effects_pass(ctx: _Context, contract_id: str) -> bool:
    profile = ctx.parameters.get("side_effect_guard_profile")
    if profile is None:
        return True
    if profile == "so101":
        return _so101_side_effects(ctx, contract_id)
    if profile == "aloha2":
        return _aloha_side_effects(ctx, contract_id)
    if profile == "fixed_arm":
        names = ctx.parameters.get("guarded_joint_names", [])
        tolerances = ctx.parameters.get("guarded_joint_tolerances", [])
        if len(names) != len(tolerances):
            raise B1ContractError("fixed arm joint guard names and tolerances must match")
        if any(float(value) <= 0 for value in tolerances):
            raise B1ContractError("fixed arm joint guard tolerances must be positive")
        if contract_id == "A3":
            if not names or not _point_drift_within(ctx, _name(ctx.parameters, "guard_site_name"), 0.015):
                return False
        return _joint_drift_within(ctx, dict(zip(names, tolerances)))
    raise B1ContractError(f"unsupported side-effect guard profile {profile!r}")


def _arm_position(ctx: _Context, contract_id: str, site_name: str, target_key: str, tolerance: float) -> bool:
    target = _vector(_request(ctx, target_key), 3, target_key)
    target_window = _window(
        ctx,
        lambda index: _distance(ctx.point(index, site_name), target) <= tolerance,
        0.5,
    )
    return target_window is not None and _entry_within_request_budget(
        ctx, target_window[0], "max_duration_s"
    )


def _wrist_roll(ctx: _Context) -> bool:
    joint = _name(ctx.parameters, "joint_name")
    target = _number(_request(ctx, "target_roll_rad"), "target_roll_rad")
    target_window = _window(
        ctx,
        lambda index: abs(ctx.joint(index, joint) - target) <= 0.030,
        0.25,
    )
    return target_window is not None and _entry_within_request_budget(
        ctx, target_window[0], "max_duration_s"
    )


def _cartesian_path(
    ctx: _Context,
    *,
    site_name: str,
    waypoint_key: str,
    waypoint_tolerance: float,
    cross_track_tolerance: float,
    final_tolerance: float,
) -> bool:
    waypoints = _vectors(_request(ctx, waypoint_key), 3, waypoint_key)
    positions = [ctx.point(index, site_name) for index in range(len(ctx.samples))]
    entries = _ordered_entries(ctx, positions, waypoints, waypoint_tolerance)
    if entries is None:
        return False
    budget = _number(_request(ctx, "max_duration_per_segment_s"), "segment budget")
    previous_time = ctx.times[0]
    for index in entries:
        if ctx.times[index] - previous_time > budget + 1.0e-12:
            return False
        previous_time = ctx.times[index]
    if not _path_ok(
        positions,
        [positions[0], *waypoints],
        cross_track_tolerance,
        through_index=entries[-1],
    ):
        return False
    return _window(
        ctx,
        lambda index: index >= entries[-1]
        and _distance(positions[index], waypoints[-1]) <= final_tolerance,
        0.5,
        after=entries[-1],
    ) is not None


def _offset_return(
    ctx: _Context,
    *,
    positions: Sequence[Sequence[float]],
    offset: Sequence[float],
    tolerance: float,
    outbound_hold_s: float,
    return_hold_s: float,
    frame_quaternion: Sequence[float] | None = None,
) -> bool:
    start = tuple(float(item) for item in positions[0])
    world_offset = _rotate(frame_quaternion, offset) if frame_quaternion is not None else _vector(offset, 3, "offset")
    target = _add(start, world_offset)
    outbound = _window(
        ctx,
        lambda index: _distance(positions[index], target) <= tolerance,
        outbound_hold_s,
    )
    if outbound is None:
        return False
    if not _entry_within_request_budget(
        ctx, outbound[0], "max_duration_per_leg_s"
    ):
        return False
    returned = _window(
        ctx,
        lambda index: _distance(positions[index], start) <= tolerance,
        return_hold_s,
        after=outbound[1] + 1,
    )
    if returned is None:
        return False
    if not _entry_within_request_budget(
        ctx,
        returned[0],
        "max_duration_per_leg_s",
        # The return phase starts only after the required outbound hold.
        # Charging that hold to the return budget shortens its public limit.
        after_index=outbound[1],
    ):
        return False
    requested = _norm(world_offset)
    maximum = max(_distance(position, start) for position in positions[: outbound[1] + 1])
    return requested <= 1.0e-12 or maximum + 1.0e-12 >= 0.8 * requested


def _pair_contact(
    ctx: _Context, index: int, robot_geoms: set[str], target_geoms: set[str]
) -> bool:
    return any(
        (geom1 in robot_geoms and geom2 in target_geoms)
        or (geom2 in robot_geoms and geom1 in target_geoms)
        for geom1, geom2, _ in ctx.contacts(index)
    )


def _observed_contact_pairs(ctx: _Context) -> set[tuple[str, str]]:
    """Return the union of sampled and per-physics-step trusted contacts."""

    pairs = {
        tuple(sorted((geom1, geom2)))
        for index in range(len(ctx.samples))
        for geom1, geom2, _ in ctx.contacts(index)
    }
    raw = ctx.evidence.get("contact_pair_step_counts", [])
    if not isinstance(raw, list):
        raise B1ContractError("trusted per-step contact counts are invalid")
    for item in raw:
        if not isinstance(item, Mapping):
            raise B1ContractError("trusted per-step contact pair is invalid")
        geom1 = item.get("geom1")
        geom2 = item.get("geom2")
        count = item.get("step_count")
        if (
            not isinstance(geom1, str)
            or not isinstance(geom2, str)
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise B1ContractError("trusted per-step contact pair is invalid")
        if count:
            pairs.add(tuple(sorted((geom1, geom2))))
    return pairs


def _first_contact_times(ctx: _Context) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for index in range(len(ctx.samples)):
        for geom1, geom2, _ in ctx.contacts(index):
            pair = tuple(sorted((geom1, geom2)))
            result[pair] = min(result.get(pair, math.inf), ctx.times[index])
    raw = ctx.evidence.get("contact_pair_first_times_s", [])
    if not isinstance(raw, list):
        raise B1ContractError("trusted first-contact evidence is invalid")
    for item in raw:
        if not isinstance(item, Mapping):
            raise B1ContractError("trusted first-contact pair is invalid")
        geom1 = item.get("geom1")
        geom2 = item.get("geom2")
        if not isinstance(geom1, str) or not isinstance(geom2, str):
            raise B1ContractError("trusted first-contact geom names are invalid")
        first_time = _number(item.get("first_time_s"), "first contact time")
        pair = tuple(sorted((geom1, geom2)))
        result[pair] = min(result.get(pair, math.inf), first_time)
    return result


def _contact_approach(
    ctx: _Context,
    *,
    positions: Sequence[Sequence[float]],
    precontact: Sequence[float],
    direction: Sequence[float],
    robot_geoms: set[str],
    target_geoms: set[str],
    precontact_tolerance: float = 0.015,
    stable_precontact: bool = False,
) -> bool:
    ray = _unit(direction, "approach direction")
    maximum_travel = _number(_request(ctx, "max_travel_m"), "max_travel_m")
    maximum_speed = _number(_request(ctx, "max_approach_speed_m_s"), "max_approach_speed_m_s")
    if stable_precontact:
        held_start: int | None = None
        pre_index = None
        for index in range(len(positions)):
            held = (
                _distance(positions[index], precontact)
                <= precontact_tolerance + 1.0e-12
                and not _pair_contact(ctx, index, robot_geoms, target_geoms)
            )
            if not held:
                held_start = None
                continue
            if held_start is None:
                held_start = index
            if ctx.times[index] - ctx.times[held_start] + 1.0e-12 < 0.1:
                continue
            delta = _sub(positions[index], precontact)
            axial = _dot(delta, ray)
            lateral = _norm(_sub(delta, _scale(ray, axial)))
            if (
                axial < -0.002 - 1.0e-12
                or axial > maximum_travel + 0.002 + 1.0e-12
                or lateral > 0.010 + 1.0e-12
                or (
                    index > 0
                    and _sample_speed(ctx, positions, index)
                    > maximum_speed + 0.01 + 1.0e-12
                )
                or (
                    index + 1 < len(positions)
                    and _sample_speed(ctx, positions, index + 1)
                    > maximum_speed + 0.01 + 1.0e-12
                )
            ):
                continue
            pre_index = index
            break
    else:
        pre_index = next(
            (
                index
                for index, position in enumerate(positions)
                if _distance(position, precontact) <= precontact_tolerance
                and not _pair_contact(ctx, index, robot_geoms, target_geoms)
            ),
            None,
        )
    if pre_index is None:
        return False
    first_target_contact = min(
        (
            time_s
            for (geom1, geom2), time_s in _first_contact_times(ctx).items()
            if (geom1 in robot_geoms and geom2 in target_geoms)
            or (geom2 in robot_geoms and geom1 in target_geoms)
        ),
        default=math.inf,
    )
    if first_target_contact < ctx.times[pre_index] - 1.0e-12:
        return False
    contact_window = _window(
        ctx,
        lambda index: _pair_contact(ctx, index, robot_geoms, target_geoms),
        0.1,
        after=pre_index + 1,
    )
    if contact_window is None:
        return False
    if not _entry_within_request_budget(
        ctx, contact_window[0], "max_duration_s"
    ):
        return False
    contact_index = contact_window[0]
    progress: list[float] = []
    path_length = 0.0
    for index in range(pre_index, contact_index + 1):
        delta = _sub(positions[index], precontact)
        axial = _dot(delta, ray)
        lateral = _norm(_sub(delta, _scale(ray, axial)))
        if axial < -0.002 - 1.0e-12 or axial > maximum_travel + 0.002 + 1.0e-12:
            return False
        if lateral > 0.010 + 1.0e-12:
            return False
        if progress and axial < progress[-1] - 0.002 - 1.0e-12:
            return False
        progress.append(axial)
        if index > pre_index:
            step_distance = _distance(positions[index], positions[index - 1])
            path_length += step_distance
            elapsed = ctx.times[index] - ctx.times[index - 1]
            if elapsed <= 0.0 or step_distance / elapsed > maximum_speed + 0.01 + 1.0e-12:
                return False
    if path_length > 1.10 * maximum_travel + 1.0e-12:
        return False
    for index in range(contact_window[0] + 1, contact_window[1] + 1):
        if _sample_speed(ctx, positions, index) > 0.02 + 1.0e-12:
            return False
    # Any tool contact with a non-target geom is an unrelated contact.
    for geom1, geom2 in _observed_contact_pairs(ctx):
        if (
            geom1 in robot_geoms
            and geom2 not in robot_geoms
            and geom2 not in target_geoms
        ) or (
            geom2 in robot_geoms
            and geom1 not in robot_geoms
            and geom1 not in target_geoms
        ):
            return False
    return True


def _joint_pose(ctx: _Context, *, path: bool) -> bool:
    names = _names(ctx.parameters, "joint_names")
    if path:
        waypoints = _vectors(_request(ctx, "joint_waypoints_rad"), len(names), "joint_waypoints_rad")
        budget = _number(_request(ctx, "max_duration_per_segment_s"), "segment budget")
        entries: list[int] = []
        next_waypoint = 0
        previous_time = ctx.times[0]
        for index in range(len(ctx.samples)):
            if next_waypoint == len(waypoints):
                break
            error = max(abs(ctx.joint(index, name) - target) for name, target in zip(names, waypoints[next_waypoint]))
            if error <= 0.05:
                if ctx.times[index] - previous_time > budget + 1.0e-12:
                    return False
                entries.append(index)
                previous_time = ctx.times[index]
                next_waypoint += 1
        if next_waypoint != len(waypoints):
            return False
        final = waypoints[-1]
        return _window(
            ctx,
            lambda index: max(abs(ctx.joint(index, name) - target) for name, target in zip(names, final)) <= 0.05,
            0.5,
            after=entries[-1],
        ) is not None
    target = _vector(_request(ctx, "target_joint_positions_rad"), len(names), "target_joint_positions_rad")
    target_window = _window(
        ctx,
        lambda index: max(abs(ctx.joint(index, name) - goal) for name, goal in zip(names, target)) < 0.1745329252,
        0.5,
    )
    return target_window is not None and _entry_within_request_budget(
        ctx, target_window[0], "max_duration_s"
    )


def _finger_sites(ctx: _Context) -> dict[str, str]:
    return {str(key): str(value) for key, value in _mapping(ctx.parameters, "fingertip_site_names").items()}


def _finger_positions(ctx: _Context, index: int, names: Sequence[str]) -> dict[str, tuple[float, float, float]]:
    palm = _name(ctx.parameters, "palm_body_name")
    sites = _finger_sites(ctx)
    return {
        finger: ctx.in_body_frame(index, ctx.point(index, sites[finger]), palm)
        for finger in names
    }


def _leap_nonrequested_guard(ctx: _Context, requested: Sequence[str]) -> bool:
    requested_set = set(requested)
    sites = _finger_sites(ctx)
    joints = _mapping(ctx.parameters, "finger_joint_names")
    unrequested = [finger for finger in sites if finger not in requested_set]
    starts = _finger_positions(ctx, 0, unrequested) if unrequested else {}
    joint_extrema = ctx.evidence.get("joint_max_abs_deviation_from_reset", {})
    site_extrema = ctx.evidence.get("site_max_displacement_from_reset", {})
    if not isinstance(joint_extrema, Mapping) or not isinstance(
        site_extrema, Mapping
    ):
        raise B1ContractError("trusted per-step LEAP extrema are invalid")
    for finger in unrequested:
        raw_joint_names = joints.get(finger)
        if (
            not isinstance(raw_joint_names, Sequence)
            or isinstance(raw_joint_names, (str, bytes))
            or len(raw_joint_names) != 4
        ):
            raise B1ContractError(f"LEAP finger {finger!r} requires four joint names")
        joint_names = [str(item) for item in raw_joint_names]
        site_name = sites[finger]
        if site_name in site_extrema and _number(
            site_extrema[site_name], f"site {site_name} displacement"
        ) > 0.010 + 1.0e-12:
            return False
        if any(
            name in joint_extrema
            and _number(joint_extrema[name], f"joint {name} deviation")
            > 0.05 + 1.0e-12
            for name in joint_names
        ):
            return False
        initial_joints = [ctx.joint(0, name) for name in joint_names]
        for index in range(len(ctx.samples)):
            position = _finger_positions(ctx, index, [finger])[finger]
            if _distance(position, starts[finger]) > 0.010 + 1.0e-12:
                return False
            if any(
                abs(ctx.joint(index, name) - initial) > 0.05 + 1.0e-12
                for name, initial in zip(joint_names, initial_joints)
            ):
                return False
    return True


def _leap_target_contacts_are_aligned(
    ctx: _Context,
    requested: Sequence[str],
    fingertip_geoms: Mapping[str, Any],
    target_geoms: Mapping[str, Any],
) -> bool:
    all_targets = {
        str(geom)
        for raw_geoms in target_geoms.values()
        if isinstance(raw_geoms, Sequence)
        and not isinstance(raw_geoms, (str, bytes))
        for geom in raw_geoms
    }
    allowed = {
        tuple(sorted((str(finger_geom), str(target_geom))))
        for finger in requested
        for finger_geom in fingertip_geoms[finger]
        for target_geom in target_geoms[finger]
    }
    for geom1, geom2 in _observed_contact_pairs(ctx):
        if (geom1 in all_targets or geom2 in all_targets) and tuple(
            sorted((geom1, geom2))
        ) not in allowed:
            return False
    return True


def _leap_reach(ctx: _Context) -> bool:
    maximum_steps = int(_number(_request(ctx, "max_control_steps"), "max_control_steps"))
    physics_steps_per_control_step = int(
        _limit(ctx.parameters, "physics_steps_per_control_step", 1.0)
    )
    if physics_steps_per_control_step < 1:
        raise B1ContractError("physics_steps_per_control_step must be positive")
    observed_steps = ctx.evidence.get("step_count")
    if (
        isinstance(observed_steps, bool)
        or not isinstance(observed_steps, int)
        or observed_steps != maximum_steps * physics_steps_per_control_step
    ):
        return False
    order = ["index", "middle", "ring", "thumb"]
    targets = _vector(_request(ctx, "target_fingertip_positions_palm_m"), 12, "target_fingertip_positions_palm_m")
    positions = _finger_positions(ctx, len(ctx.samples) - 1, order)
    actual = tuple(coordinate for finger in order for coordinate in positions[finger])
    return _distance(actual, targets) < 0.00894427191


def _leap_contact_pattern(ctx: _Context) -> bool:
    fingers_raw = _request(ctx, "required_fingers")
    if not isinstance(fingers_raw, Sequence) or isinstance(fingers_raw, (str, bytes)) or not fingers_raw:
        raise B1ContractError("required_fingers must be non-empty")
    fingers = [str(item) for item in fingers_raw]
    targets = _vectors(_request(ctx, "contact_target_positions_palm_m"), 3, "contact targets")
    directions = _vectors(_request(ctx, "approach_directions_palm_unit"), 3, "approach directions")
    if len(targets) != len(fingers) or len(directions) != len(fingers):
        raise B1ContractError("finger contact arrays must align")
    geom_mapping = _mapping(ctx.parameters, "fingertip_geom_names")
    target_mapping = _mapping(ctx.parameters, "target_geom_names")
    sites = _finger_sites(ctx)
    palm = _name(ctx.parameters, "palm_body_name")
    for finger, target, direction in zip(fingers, targets, directions):
        robot = {str(item) for item in geom_mapping[finger]}
        target_geoms = {str(item) for item in target_mapping[finger]}
        positions = [ctx.in_body_frame(index, ctx.point(index, sites[finger]), palm) for index in range(len(ctx.samples))]
        origin = positions[0]
        ray = _unit(direction, "approach direction")
        maximum_travel = _number(_request(ctx, "max_travel_m"), "max_travel_m")
        maximum_speed = _number(
            _request(ctx, "max_approach_speed_m_s"),
            "max_approach_speed_m_s",
        )
        target_delta = _sub(target, origin)
        target_axial = _dot(target_delta, ray)
        target_lateral = _norm(_sub(target_delta, _scale(ray, target_axial)))
        if (
            target_axial < -1.0e-12
            or target_axial > maximum_travel + 1.0e-12
            or target_lateral > 0.010 + 1.0e-12
        ):
            return False
        first_contact = next(
            (
                index
                for index in range(1, len(ctx.samples))
                if _pair_contact(ctx, index, robot, target_geoms)
            ),
            None,
        )
        if first_contact is None:
            return False
        target_region = next(
            (
                index
                for index in range(first_contact, len(ctx.samples))
                if _distance(positions[index], target) <= 0.010 + 1.0e-12
            ),
            None,
        )
        if target_region is None:
            return False
        contact_window = _window(
            ctx,
            lambda index: _pair_contact(ctx, index, robot, target_geoms),
            0.1,
            after=target_region,
        )
        if contact_window is None:
            return False
        if not _entry_within_request_budget(
            ctx, contact_window[0], "max_duration_s"
        ):
            return False
        path_length = 0.0
        previous_axial: float | None = None
        for index in range(contact_window[0] + 1):
            delta = _sub(positions[index], origin)
            axial = _dot(delta, ray)
            lateral = _norm(_sub(delta, _scale(ray, axial)))
            if axial < -0.002 - 1.0e-12 or axial > maximum_travel + 0.002 + 1.0e-12:
                return False
            if lateral > 0.010 + 1.0e-12:
                return False
            if previous_axial is not None and axial < previous_axial - 0.002 - 1.0e-12:
                return False
            previous_axial = axial
            if index > 0:
                step_distance = _distance(positions[index], positions[index - 1])
                path_length += step_distance
                if _sample_speed(ctx, positions, index) > maximum_speed + 0.01 + 1.0e-12:
                    return False
        if path_length > 1.10 * maximum_travel + 1.0e-12:
            return False
        for index in range(contact_window[0] + 1, contact_window[1] + 1):
            if _sample_speed(ctx, positions, index) > maximum_speed + 0.01 + 1.0e-12:
                return False
    return _leap_target_contacts_are_aligned(
        ctx, fingers, geom_mapping, target_mapping
    ) and _leap_nonrequested_guard(ctx, fingers)


def _maximum_contact_loss(ctx: _Context, active: Sequence[bool]) -> float:
    maximum = 0.0
    start: int | None = None
    for index, value in enumerate(active):
        if not value and start is None:
            start = index
        if value and start is not None:
            maximum = max(maximum, ctx.times[index] - ctx.times[start])
            start = None
    if start is not None:
        maximum = max(maximum, ctx.times[-1] - ctx.times[start])
    return maximum


def _leap_hold_contacts(ctx: _Context) -> bool:
    bounded = _requested_duration_context(ctx)
    if bounded is None:
        return False
    ctx = bounded
    raw = _request(ctx, "required_fingers")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise B1ContractError("required_fingers must be non-empty")
    fingers = [str(item) for item in raw]
    directions = _vectors(
        _request(ctx, "separation_directions_palm_unit"),
        3,
        "separation directions",
    )
    if len(directions) != len(fingers):
        raise B1ContractError("finger separation directions must align")
    site_names = _finger_sites(ctx)
    target_sites = _mapping(ctx.parameters, "target_site_names")
    target_bodies = _mapping(ctx.parameters, "target_body_names")
    geom_mapping = _mapping(ctx.parameters, "fingertip_geom_names")
    target_geom_mapping = _mapping(ctx.parameters, "target_geom_names")
    palm = _name(ctx.parameters, "palm_body_name")
    preinvoke = ctx.evidence.get("framework_preinvoke")
    if (
        not isinstance(preinvoke, Mapping)
        or preinvoke.get("all_required_contacts_held") is not True
        or _number(preinvoke.get("duration_s"), "preinvoke duration") < 0.10
    ):
        return False
    expected_preinvoke_pairs = {
        tuple(
            sorted(
                (
                    str(geom_mapping[finger][0]),
                    str(target_geom_mapping[finger][0]),
                )
            )
        )
        for finger in fingers
    }
    raw_preinvoke_pairs = preinvoke.get("required_contact_pairs")
    if not isinstance(raw_preinvoke_pairs, list):
        return False
    observed_preinvoke_pairs = {
        tuple(sorted(str(item) for item in pair))
        for pair in raw_preinvoke_pairs
        if isinstance(pair, Sequence)
        and not isinstance(pair, (str, bytes))
        and len(pair) == 2
    }
    if observed_preinvoke_pairs != expected_preinvoke_pairs:
        return False
    raw_events = ctx.evidence.get("framework_events")
    if not isinstance(raw_events, list):
        return False
    events_by_body = {
        str(event.get("body_name")): event
        for event in raw_events
        if isinstance(event, Mapping) and isinstance(event.get("body_name"), str)
    }
    for finger, direction in zip(fingers, directions):
        event = events_by_body.get(str(target_bodies[finger]))
        if not isinstance(event, Mapping) or event.get("complete") is not True:
            return False
        displacement = _vector(
            event.get("displacement_m"), 3, "Framework target displacement"
        )
        distance = _norm(displacement)
        if distance < 0.006 - 1.0e-12 or distance > 0.008 + 1.0e-12:
            return False
        if _dot(_unit(displacement, "Framework target displacement"), _unit(direction, "separation direction")) < 1.0 - 1.0e-9:
            return False
    initial_fingers = _finger_positions(ctx, 0, fingers)
    initial_targets = {
        finger: ctx.in_body_frame(0, ctx.point(0, str(target_sites[finger])), palm)
        for finger in fingers
    }
    initial_relative = {
        finger: _sub(initial_fingers[finger], initial_targets[finger])
        for finger in fingers
    }
    if _window(
        ctx,
        lambda index: all(
            _pair_contact(
                ctx,
                index,
                {str(item) for item in geom_mapping[finger]},
                {str(item) for item in target_geom_mapping[finger]},
            )
            for finger in fingers
        ),
        0.1,
    ) is None:
        return False
    disturbance_start = next(
        (
            index
            for index, time in enumerate(ctx.times)
            if time - ctx.times[0] + 1.0e-12 >= 0.25
        ),
        None,
    )
    disturbance_complete = next(
        (
            index
            for index, time in enumerate(ctx.times)
            if time - ctx.times[0] + 1.0e-12 >= 0.30
        ),
        None,
    )
    if disturbance_start is None or disturbance_complete is None:
        return False
    moved: list[tuple[str, int, tuple[float, float, float]]] = []
    for finger in fingers:
        for index in range(1, len(ctx.samples)):
            target = ctx.in_body_frame(index, ctx.point(index, str(target_sites[finger])), palm)
            displacement = _sub(target, initial_targets[finger])
            if _norm(displacement) >= 0.006 - 1.0e-9:
                moved.append((finger, index, displacement))
                break
    if len(moved) != len(fingers):
        return False
    for finger, moved_index, target_displacement in moved:
        robot = {str(item) for item in geom_mapping[finger]}
        target_geoms = {str(item) for item in target_geom_mapping[finger]}
        active = [
            _pair_contact(ctx, index, robot, target_geoms)
            for index in range(disturbance_start, len(ctx.samples))
        ]
        if math.fsum(active) / len(active) < 0.90 or _maximum_contact_loss(ctx, active) > 0.20 + 1.0e-12:
            return False
        recovery_limit = ctx.times[disturbance_complete] + 0.20
        recovered = False
        for index in range(disturbance_complete, len(ctx.samples)):
            if ctx.times[index] > recovery_limit + 1.0e-12:
                break
            finger_position = ctx.in_body_frame(index, ctx.point(index, site_names[finger]), palm)
            target_position = ctx.in_body_frame(index, ctx.point(index, str(target_sites[finger])), palm)
            followed = _dot(_sub(finger_position, initial_fingers[finger]), _unit(target_displacement, "target displacement"))
            relative_error = _norm(
                _sub(
                    _sub(finger_position, target_position),
                    initial_relative[finger],
                )
            )
            if followed + 1.0e-12 >= 0.8 * _norm(target_displacement) and relative_error <= 0.002 + 1.0e-12:
                recovered = True
                break
        if not recovered:
            return False
    return _leap_target_contacts_are_aligned(
        ctx, fingers, geom_mapping, target_geom_mapping
    ) and _leap_nonrequested_guard(ctx, fingers)


def _leap_offset_return(ctx: _Context) -> bool:
    raw = _request(ctx, "finger_names")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise B1ContractError("finger_names must be non-empty")
    fingers = [str(item) for item in raw]
    offsets = _vectors(_request(ctx, "offsets_palm_m"), 3, "offsets_palm_m")
    if len(fingers) != len(offsets):
        raise B1ContractError("finger offsets must align")
    all_positions = [
        _finger_positions(ctx, index, fingers) for index in range(len(ctx.samples))
    ]
    starts = all_positions[0]
    targets = {finger: _add(starts[finger], offset) for finger, offset in zip(fingers, offsets)}
    outbound = _window(
        ctx,
        lambda index: all(_distance(all_positions[index][finger], targets[finger]) <= 0.010 for finger in fingers),
        0.25,
    )
    if outbound is None:
        return False
    if not _entry_within_request_budget(
        ctx, outbound[0], "max_duration_per_leg_s"
    ):
        return False
    returned = _window(
        ctx,
        lambda index: all(_distance(all_positions[index][finger], starts[finger]) <= 0.010 for finger in fingers),
        0.25,
        after=outbound[1] + 1,
    )
    return (
        returned is not None
        and _entry_within_request_budget(
            ctx,
            returned[0],
            "max_duration_per_leg_s",
            after_index=outbound[0],
        )
        and _leap_nonrequested_guard(ctx, fingers)
    )


def _body_planar(ctx: _Context, body_name: str) -> list[tuple[float, float]]:
    return [ctx.body(index, body_name)[:2] for index in range(len(ctx.samples))]


def _go_twist(ctx: _Context) -> bool:
    bounded = _requested_duration_context(ctx)
    if bounded is None:
        return False
    ctx = bounded
    body = _name(ctx.parameters, "body_name")
    requested_velocity = _vector(_request(ctx, "linear_velocity_body_m_s"), 2, "linear velocity")
    requested_yaw_rate = _number(_request(ctx, "yaw_rate_rad_s"), "yaw rate")
    start_time = ctx.times[0] + _number(_request(ctx, "duration_s"), "duration_s") - 1.0
    velocity_errors: list[float] = []
    yaw_errors: list[float] = []
    direction_errors: list[float] = []
    requested_speed = _norm(requested_velocity)
    for index in range(1, len(ctx.samples)):
        if ctx.times[index] < start_time:
            continue
        elapsed = ctx.times[index] - ctx.times[index - 1]
        if elapsed <= 0.0:
            continue
        world_delta = _sub(ctx.body(index, body)[:2], ctx.body(index - 1, body)[:2])
        yaw = _yaw(ctx.quaternion(index - 1, body))
        actual_body = (
            (math.cos(yaw) * world_delta[0] + math.sin(yaw) * world_delta[1]) / elapsed,
            (-math.sin(yaw) * world_delta[0] + math.cos(yaw) * world_delta[1]) / elapsed,
        )
        velocity_errors.append(_distance(actual_body, requested_velocity))
        actual_yaw_rate = _wrapped(_yaw(ctx.quaternion(index, body)) - yaw) / elapsed
        yaw_errors.append(abs(actual_yaw_rate - requested_yaw_rate))
        if requested_speed >= 0.20 and _norm(actual_body) > 1.0e-9:
            cosine = max(-1.0, min(1.0, _dot(actual_body, requested_velocity) / (_norm(actual_body) * requested_speed)))
            direction_errors.append(math.acos(cosine))
    if not velocity_errors or not yaw_errors:
        return False
    return (
        math.fsum(velocity_errors) / len(velocity_errors) <= 0.10
        and math.fsum(yaw_errors) / len(yaw_errors) <= 0.30
        and (not direction_errors or math.fsum(direction_errors) / len(direction_errors) <= math.radians(10.0))
    )


def _go_relative_pose(ctx: _Context) -> bool:
    body = _name(ctx.parameters, "body_name")
    target = _target_in_initial_yaw_frame(ctx, body, _request(ctx, "translation_initial_yaw_m"))
    target_yaw = _yaw(ctx.quaternion(0, body)) + _number(_request(ctx, "yaw_delta_rad"), "yaw_delta_rad")
    positions = _body_planar(ctx, body)
    target_window = _window(
        ctx,
        lambda index: _distance(positions[index], target) <= 0.10
        and abs(_wrapped(_yaw(ctx.quaternion(index, body)) - target_yaw)) <= 0.0873
        and _sample_speed(ctx, positions, index) <= 0.10,
        0.5,
    )
    return target_window is not None and _entry_within_request_budget(
        ctx, target_window[0], "max_duration_s"
    )


def _go_path(ctx: _Context) -> bool:
    body = _name(ctx.parameters, "body_name")
    raw = _vectors(_request(ctx, "waypoints_initial_yaw_m"), 2, "waypoints_initial_yaw_m")
    targets = [_target_in_initial_yaw_frame(ctx, body, point) for point in raw]
    positions = _body_planar(ctx, body)
    entries = _ordered_entries(ctx, positions, targets, 0.10)
    if entries is None or not _path_ok(positions, [positions[0], *targets], 0.15, through_index=entries[-1]):
        return False
    target_window = _window(
        ctx,
        lambda index: _distance(positions[index], targets[-1]) <= 0.10
        and _sample_speed(ctx, positions, index) <= 0.10,
        0.5,
        after=entries[-1],
    )
    return target_window is not None and _entry_within_request_budget(
        ctx, target_window[0], "max_duration_s"
    )


def _go_height(ctx: _Context) -> bool:
    body = _name(ctx.parameters, "body_name")
    target = _number(_request(ctx, "target_height_m"), "target_height_m")
    start = ctx.body(0, body)
    start_yaw = _yaw(ctx.quaternion(0, body))
    target_window = _window(
        ctx,
        lambda index: abs(ctx.body(index, body)[2] - target) <= 0.03
        and all(abs(value) <= 0.1745 for value in _roll_pitch(ctx.quaternion(index, body)))
        and _distance(ctx.body(index, body)[:2], start[:2]) <= 0.05
        and abs(_wrapped(_yaw(ctx.quaternion(index, body)) - start_yaw)) <= 0.0873,
        0.5,
    )
    return target_window is not None and _entry_within_request_budget(
        ctx, target_window[0], "max_duration_s"
    )


def _go_stance(ctx: _Context) -> bool:
    bounded = _requested_duration_context(ctx)
    if bounded is None:
        return False
    ctx = bounded
    body = _name(ctx.parameters, "body_name")
    start = ctx.body(0, body)
    recovery_index = next(
        (
            index
            for index in range(len(ctx.samples))
            if ctx.times[index] - ctx.times[0] <= 0.5 + 1.0e-12
            and all(abs(value) <= 0.0524 for value in _roll_pitch(ctx.quaternion(index, body)))
        ),
        None,
    )
    if recovery_index is None:
        return False
    positions = [ctx.body(index, body) for index in range(len(ctx.samples))]
    for index in range(recovery_index, len(ctx.samples)):
        if any(abs(value) > 0.0873 for value in _roll_pitch(ctx.quaternion(index, body))):
            return False
        if abs(positions[index][2] - start[2]) > 0.03 or _distance(positions[index][:2], start[:2]) > 0.05:
            return False
    speeds = [_sample_speed(ctx, [position[:2] for position in positions], index) for index in range(max(1, recovery_index), len(ctx.samples))]
    if not speeds or math.fsum(speeds) / len(speeds) > 0.05:
        return False
    vertical_speed = abs(positions[-1][2] - positions[-2][2]) / max(1.0e-12, ctx.times[-1] - ctx.times[-2])
    if vertical_speed > 0.05:
        return False
    groups_raw = ctx.parameters.get("foot_geom_groups")
    floor = set(_names(ctx.parameters, "floor_geom_names"))
    if not isinstance(groups_raw, Sequence) or isinstance(groups_raw, (str, bytes)) or len(groups_raw) != 4:
        raise B1ContractError("G5 requires four foot_geom_groups")
    for raw_group in groups_raw:
        if not isinstance(raw_group, Sequence) or isinstance(raw_group, (str, bytes)) or not raw_group:
            raise B1ContractError("G5 foot geom group is invalid")
        group = {str(item) for item in raw_group}
        active = [
            any(
                (left in group and right in floor) or (right in group and left in floor)
                for left, right, _ in ctx.contacts(index)
            )
            for index in range(recovery_index, len(ctx.samples))
        ]
        if math.fsum(active) / len(active) < 0.90 or _maximum_contact_loss(ctx, active) > 0.10 + 1.0e-12:
            return False
    forbidden = set(_names(ctx.parameters, "forbidden_floor_geom_names"))
    return not any(
        (left in forbidden and right in floor) or (right in forbidden and left in floor)
        for index in range(len(ctx.samples))
        for left, right, _ in ctx.contacts(index)
    )


def _stretch_base_move(ctx: _Context) -> bool:
    body = _name(ctx.parameters, "body_name")
    target = _target_in_initial_yaw_frame(ctx, body, _request(ctx, "translation_initial_yaw_m"))
    yaw = _yaw(ctx.quaternion(0, body))
    positions = _body_planar(ctx, body)
    target_window = _window(
        ctx,
        lambda index: _distance(positions[index], target) <= 0.020
        and abs(_wrapped(_yaw(ctx.quaternion(index, body)) - yaw)) <= 0.020
        and _sample_speed(ctx, positions, index) <= 0.020,
        0.5,
    )
    return target_window is not None and _entry_within_request_budget(
        ctx, target_window[0], "max_duration_s"
    )


def _stretch_turn(ctx: _Context) -> bool:
    body = _name(ctx.parameters, "body_name")
    target = _number(_request(ctx, "target_yaw_world_rad"), "target_yaw_world_rad")
    start = ctx.body(0, body)[:2]
    yaws = [_yaw(ctx.quaternion(index, body)) for index in range(len(ctx.samples))]
    target_window = _window(
        ctx,
        lambda index: abs(_wrapped(yaws[index] - target)) <= 0.020
        and _distance(ctx.body(index, body)[:2], start) <= 0.020
        and (index == 0 or abs(_wrapped(yaws[index] - yaws[index - 1])) / max(1.0e-12, ctx.times[index] - ctx.times[index - 1]) <= 0.020),
        0.5,
    )
    return target_window is not None and _entry_within_request_budget(
        ctx, target_window[0], "max_duration_s"
    )


def _stretch_offset(ctx: _Context) -> bool:
    site = _name(ctx.parameters, "site_name")
    body = _name(ctx.parameters, "body_name")
    positions = [ctx.point(index, site) for index in range(len(ctx.samples))]
    offset = _vector(_request(ctx, "offset_call_time_base_m"), 3, "offset_call_time_base_m")
    if not _offset_return(
        ctx,
        positions=positions,
        offset=offset,
        tolerance=0.025,
        outbound_hold_s=0.25,
        return_hold_s=0.5,
        frame_quaternion=ctx.quaternion(0, body),
    ):
        return False
    start_position = ctx.body(0, body)
    start_yaw = _yaw(ctx.quaternion(0, body))
    return all(
        _distance(ctx.body(index, body)[:2], start_position[:2]) <= 0.020
        and abs(_wrapped(_yaw(ctx.quaternion(index, body)) - start_yaw)) <= 0.030
        for index in range(len(ctx.samples))
    )


def _arm_sites(ctx: _Context) -> dict[str, str]:
    return {str(key): str(value) for key, value in _mapping(ctx.parameters, "arm_site_names").items()}


def _aloha_single_arm_position(ctx: _Context) -> bool:
    arm = str(_request(ctx, "arm"))
    site = _arm_sites(ctx).get(arm)
    if site is None:
        raise B1ContractError(f"unknown arm {arm!r}")
    return _arm_position(ctx, "AL1", site, "target_position_world_m", 0.015)


def _aloha_path(ctx: _Context) -> bool:
    arm = str(_request(ctx, "arm"))
    site = _arm_sites(ctx).get(arm)
    if site is None:
        raise B1ContractError(f"unknown arm {arm!r}")
    return _cartesian_path(
        ctx,
        site_name=site,
        waypoint_key="waypoints_world_m",
        waypoint_tolerance=0.020,
        cross_track_tolerance=0.020,
        final_tolerance=0.015,
    )


def _aloha_bimanual_position(ctx: _Context) -> bool:
    sites = _arm_sites(ctx)
    targets = {
        "left": _vector(_request(ctx, "left_target_position_world_m"), 3, "left target"),
        "right": _vector(_request(ctx, "right_target_position_world_m"), 3, "right target"),
    }
    target_window = _window(
        ctx,
        lambda index: all(_distance(ctx.point(index, sites[arm]), targets[arm]) <= 0.015 for arm in ("left", "right")),
        0.5,
    )
    if target_window is None:
        return False
    final_entries: dict[str, int] = {}
    for arm in ("left", "right"):
        entry = target_window[0]
        while entry > 0 and _distance(
            ctx.point(entry - 1, sites[arm]), targets[arm]
        ) <= 0.015:
            entry -= 1
        final_entries[arm] = entry
    return (
        abs(
            ctx.times[final_entries["left"]]
            - ctx.times[final_entries["right"]]
        )
        <= 0.10 + 1.0e-12
        and _entry_within_request_budget(
            ctx, target_window[0], "max_duration_s"
        )
    )


def _aloha_contact(ctx: _Context) -> bool:
    arm = str(_request(ctx, "arm"))
    sites = _arm_sites(ctx)
    geom_mapping = _mapping(ctx.parameters, "arm_tool_geom_names")
    target_value = ctx.parameters.get("target_geom_names")
    if isinstance(target_value, Mapping):
        target_raw = target_value.get(arm)
    else:
        target_raw = target_value
    if not isinstance(target_raw, Sequence) or isinstance(target_raw, (str, bytes)):
        raise B1ContractError("AL5 target geom names are missing")
    return _contact_approach(
        ctx,
        positions=[ctx.point(index, sites[arm]) for index in range(len(ctx.samples))],
        precontact=_vector(_request(ctx, "precontact_position_m"), 3, "precontact_position_m"),
        direction=_vector(_request(ctx, "approach_direction_unit"), 3, "approach direction"),
        robot_geoms={str(item) for item in geom_mapping[arm]},
        target_geoms={str(item) for item in target_raw},
        stable_precontact=(
            ctx.parameters.get("precontact_gate") == "held_window_then_ray"
        ),
    )


def _aloha_offsets(ctx: _Context) -> bool:
    sites = _arm_sites(ctx)
    base_mapping = ctx.parameters.get("arm_base_body_names")
    positions = {
        arm: [ctx.point(index, sites[arm]) for index in range(len(ctx.samples))]
        for arm in ("left", "right")
    }
    offsets = {
        "left": _vector(_request(ctx, "left_offset_arm_base_m"), 3, "left offset"),
        "right": _vector(_request(ctx, "right_offset_arm_base_m"), 3, "right offset"),
    }
    targets: dict[str, tuple[float, ...]] = {}
    for arm in ("left", "right"):
        if isinstance(base_mapping, Mapping) and arm in base_mapping:
            world_offset = _rotate(ctx.quaternion(0, str(base_mapping[arm])), offsets[arm])
        else:
            world_offset = offsets[arm]
        targets[arm] = _add(positions[arm][0], world_offset)
    outbound = _window(
        ctx,
        lambda index: all(_distance(positions[arm][index], targets[arm]) <= 0.015 for arm in ("left", "right")),
        0.25,
    )
    if outbound is None:
        return False
    final_entries: dict[str, int] = {}
    for arm in ("left", "right"):
        entry = outbound[0]
        while entry > 0 and _distance(
            positions[arm][entry - 1], targets[arm]
        ) <= 0.015:
            entry -= 1
        final_entries[arm] = entry
    if (
        abs(
            ctx.times[final_entries["left"]]
            - ctx.times[final_entries["right"]]
        )
        > 0.10 + 1.0e-12
    ):
        return False
    if not _entry_within_request_budget(
        ctx, outbound[0], "max_duration_per_leg_s"
    ):
        return False
    returned = _window(
        ctx,
        lambda index: all(_distance(positions[arm][index], positions[arm][0]) <= 0.015 for arm in ("left", "right")),
        0.25,
        after=outbound[1] + 1,
    )
    return returned is not None and _entry_within_request_budget(
        ctx,
        returned[0],
        "max_duration_per_leg_s",
        after_index=outbound[0],
    )


def evaluate_b1_contract(
    parameters: Mapping[str, Any],
    *,
    evidence: Mapping[str, Any],
    request: Mapping[str, Any],
) -> float:
    """Return a trusted binary outcome for one fixed B1 contract."""

    contract_id = parameters.get("contract_id")
    if not isinstance(contract_id, str) or contract_id not in SUPPORTED_CONTRACT_IDS:
        raise B1ContractError(f"unsupported B1 contract_id {contract_id!r}")
    ctx = _Context.build(parameters, evidence, request)

    if contract_id == "A1":
        passed = _arm_position(ctx, contract_id, _name(parameters, "site_name"), "target_position_m", 0.015)
    elif contract_id == "A2":
        passed = _cartesian_path(ctx, site_name=_name(parameters, "site_name"), waypoint_key="waypoints_m", waypoint_tolerance=0.020, cross_track_tolerance=0.020, final_tolerance=0.015)
    elif contract_id == "A3":
        closed = _limit(parameters, "closed_position", 0.0)
        opened = _limit(parameters, "open_position", 1.0)
        passed = _aperture_contract(ctx, error_limit=0.10, hold_s=0.25, normalized=True, minimum_excursion=0.50 * abs(opened - closed))
    elif contract_id == "A4":
        passed = _contact_approach(
            ctx,
            positions=[ctx.point(index, _name(parameters, "site_name")) for index in range(len(ctx.samples))],
            precontact=_vector(_request(ctx, "precontact_position_m"), 3, "precontact_position_m"),
            direction=_vector(_request(ctx, "approach_direction_unit"), 3, "approach_direction_unit"),
            robot_geoms=set(_names(parameters, "tool_geom_names")),
            target_geoms=set(_names(parameters, "target_geom_names")),
            stable_precontact=(
                parameters.get("precontact_gate")
                == "held_window_then_ray"
            ),
        )
    elif contract_id == "A5":
        site = _name(parameters, "site_name")
        base = parameters.get("base_body_name")
        passed = _offset_return(
            ctx,
            positions=[ctx.point(index, site) for index in range(len(ctx.samples))],
            offset=_vector(_request(ctx, "offset_robot_base_m"), 3, "offset_robot_base_m"),
            tolerance=0.015,
            outbound_hold_s=0.25,
            return_hold_s=0.5,
            frame_quaternion=ctx.quaternion(0, str(base)) if isinstance(base, str) else None,
        )
    elif contract_id == "A6":
        passed = _wrist_roll(ctx)
    elif contract_id == "G1":
        passed = _go_twist(ctx)
    elif contract_id == "G2":
        passed = _go_relative_pose(ctx)
    elif contract_id == "G3":
        passed = _go_path(ctx)
    elif contract_id == "G4":
        passed = _go_height(ctx)
    elif contract_id == "G5":
        passed = _go_stance(ctx)
    elif contract_id == "L1":
        passed = _joint_pose(ctx, path=False)
    elif contract_id == "L2":
        passed = _joint_pose(ctx, path=True)
    elif contract_id == "L3":
        passed = _leap_reach(ctx)
    elif contract_id == "L4":
        passed = _leap_contact_pattern(ctx)
    elif contract_id == "L5":
        passed = _leap_hold_contacts(ctx)
    elif contract_id == "L6":
        passed = _leap_offset_return(ctx)
    elif contract_id == "ST1":
        passed = _stretch_base_move(ctx)
    elif contract_id == "ST2":
        passed = _stretch_turn(ctx)
    elif contract_id == "ST3":
        passed = _arm_position(ctx, contract_id, _name(parameters, "site_name"), "target_position_world_m", 0.025)
    elif contract_id == "ST4":
        passed = _cartesian_path(ctx, site_name=_name(parameters, "site_name"), waypoint_key="waypoints_world_m", waypoint_tolerance=0.030, cross_track_tolerance=0.030, final_tolerance=0.025)
    elif contract_id == "ST5":
        passed = _aperture_contract(ctx, error_limit=0.003, hold_s=0.25, normalized=False, minimum_excursion=0.015)
    elif contract_id == "ST6":
        joint = _name(parameters, "joint_name")
        target = _number(_request(ctx, "target_yaw_rad"), "target_yaw_rad")
        passed = _window(ctx, lambda index: abs(ctx.joint(index, joint) - target) <= 0.030, 0.5) is not None
    elif contract_id == "ST7":
        passed = _contact_approach(
            ctx,
            positions=[ctx.point(index, _name(parameters, "site_name")) for index in range(len(ctx.samples))],
            precontact=_vector(_request(ctx, "precontact_position_m"), 3, "precontact_position_m"),
            direction=_vector(_request(ctx, "approach_direction_unit"), 3, "approach_direction_unit"),
            robot_geoms=set(_names(parameters, "tool_geom_names")),
            target_geoms=set(_names(parameters, "target_geom_names")),
        )
    elif contract_id == "ST8":
        passed = _stretch_offset(ctx)
    elif contract_id == "AL1":
        passed = _aloha_single_arm_position(ctx)
    elif contract_id == "AL2":
        passed = _aloha_path(ctx)
    elif contract_id == "AL3":
        arm = str(_request(ctx, "arm"))
        passed = _aperture_contract(ctx, error_limit=0.10, hold_s=0.25, normalized=True, minimum_excursion=None, arm=arm, mirror_limit=0.05)
    elif contract_id == "AL4":
        passed = _aloha_bimanual_position(ctx)
    elif contract_id == "AL5":
        passed = _aloha_contact(ctx)
    else:  # AL6
        passed = _aloha_offsets(ctx)
    return 1.0 if passed and _side_effects_pass(ctx, contract_id) else 0.0
