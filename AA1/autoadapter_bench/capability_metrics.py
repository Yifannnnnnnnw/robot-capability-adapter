"""Fixed A1-A5/G1-G5 physical metrics adapted from AA2 b1_contracts.py.

Only the approved arm/quadruped algorithms are retained. This module consumes
trusted time series; it has no AA2 runtime dependency and no candidate verdicts.
Reset, sampling, actuator guards and videos are handled by capability_eval.
"""
from __future__ import annotations
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
SUPPORTED_CONTRACT_IDS = frozenset(['A1', 'A2', 'A3', 'A4', 'A5', 'G1', 'G2', 'G3', 'G4', 'G5'])

class B1ContractError(ValueError):
    """Raised when a B1 contract cannot be evaluated from trusted evidence."""

def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise B1ContractError(f'{name} must be numeric')
    result = float(value)
    if not math.isfinite(result):
        raise B1ContractError(f'{name} must be finite')
    return result

def _vector(value: Any, size: int, name: str) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != size:
        raise B1ContractError(f'{name} must contain {size} numbers')
    return tuple((_number(item, name) for item in value))

def _vectors(value: Any, size: int, name: str) -> list[tuple[float, ...]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise B1ContractError(f'{name} must be an array')
    result = [_vector(item, size, name) for item in value]
    if not result:
        raise B1ContractError(f'{name} must not be empty')
    return result

def _name(parameters: Mapping[str, Any], key: str) -> str:
    value = parameters.get(key)
    if not isinstance(value, str) or not value:
        raise B1ContractError(f'B1 binding requires {key}')
    return value

def _names(parameters: Mapping[str, Any], key: str) -> list[str]:
    value = parameters.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or (not value) or (not all((isinstance(item, str) and item for item in value))):
        raise B1ContractError(f'B1 binding requires non-empty {key}')
    return [str(item) for item in value]

def _mapping(parameters: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parameters.get(key)
    if not isinstance(value, Mapping) or not value:
        raise B1ContractError(f'B1 binding requires non-empty {key}')
    return value

def _limit(parameters: Mapping[str, Any], key: str, default: float) -> float:
    return _number(parameters.get(key, default), key)

def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise B1ContractError('cannot compare vectors of different lengths')
    return math.sqrt(math.fsum(((float(a) - float(b)) ** 2 for a, b in zip(left, right))))

def _sub(left: Sequence[float], right: Sequence[float]) -> tuple[float, ...]:
    return tuple((float(a) - float(b) for a, b in zip(left, right)))

def _add(left: Sequence[float], right: Sequence[float]) -> tuple[float, ...]:
    return tuple((float(a) + float(b) for a, b in zip(left, right)))

def _scale(value: Sequence[float], factor: float) -> tuple[float, ...]:
    return tuple((float(item) * factor for item in value))

def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return math.fsum((float(a) * float(b) for a, b in zip(left, right)))

def _norm(value: Sequence[float]) -> float:
    return math.sqrt(math.fsum((float(item) ** 2 for item in value)))

def _unit(value: Sequence[float], name: str) -> tuple[float, ...]:
    length = _norm(value)
    if length <= 0.0:
        raise B1ContractError(f'{name} must be non-zero')
    return tuple((float(item) / length for item in value))

def _normal_quaternion(value: Any) -> tuple[float, float, float, float]:
    raw = _vector(value, 4, 'quaternion')
    length = _norm(raw)
    if length <= 0.0:
        raise B1ContractError('quaternion must be non-zero')
    return tuple((item / length for item in raw))

def _rotate(quaternion: Sequence[float], vector: Sequence[float]) -> tuple[float, float, float]:
    w, x, y, z = _normal_quaternion(quaternion)
    vx, vy, vz = _vector(vector, 3, 'vector')
    return ((1 - 2 * (y * y + z * z)) * vx + 2 * (x * y - w * z) * vy + 2 * (x * z + w * y) * vz, 2 * (x * y + w * z) * vx + (1 - 2 * (x * x + z * z)) * vy + 2 * (y * z - w * x) * vz, 2 * (x * z - w * y) * vx + 2 * (y * z + w * x) * vy + (1 - 2 * (x * x + y * y)) * vz)

def _rotate_inverse(quaternion: Sequence[float], vector: Sequence[float]) -> tuple[float, float, float]:
    w, x, y, z = _normal_quaternion(quaternion)
    return _rotate((w, -x, -y, -z), vector)

def _yaw(quaternion: Sequence[float]) -> float:
    w, x, y, z = _normal_quaternion(quaternion)
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))

def _roll_pitch(quaternion: Sequence[float]) -> tuple[float, float]:
    w, x, y, z = _normal_quaternion(quaternion)
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    sine = max(-1.0, min(1.0, 2 * (w * y - z * x)))
    return (roll, math.asin(sine))

def _wrapped(value: float) -> float:
    return (float(value) + math.pi) % (2 * math.pi) - math.pi

def _point_segment_distance(point: Sequence[float], start: Sequence[float], end: Sequence[float]) -> float:
    segment = _sub(end, start)
    denominator = _dot(segment, segment)
    if denominator <= 1e-18:
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
    def build(cls, parameters: Mapping[str, Any], evidence: Mapping[str, Any], request: Mapping[str, Any]) -> _Context:
        raw_samples = evidence.get('samples')
        if not isinstance(raw_samples, list) or len(raw_samples) < 2 or (not all((isinstance(item, Mapping) for item in raw_samples))):
            raise B1ContractError('B1 contract requires trusted time-series samples')
        times = tuple((_number(sample.get('time'), 'sample time') for sample in raw_samples))
        if any((current < previous for previous, current in zip(times, times[1:]))):
            raise B1ContractError('trusted sample times must be non-decreasing')
        return cls(parameters, evidence, request, tuple(raw_samples), times)

    def site(self, index: int, name: str) -> tuple[float, float, float]:
        values = self.samples[index].get('site_positions')
        if not isinstance(values, Mapping) or name not in values:
            raise B1ContractError(f'trusted site {name!r} is unavailable')
        return _vector(values[name], 3, f'site {name}')

    def point(self, index: int, name: str) -> tuple[float, float, float]:
        """Resolve a tracked point as a named site, then a body origin."""
        values = self.samples[index].get('site_positions')
        if isinstance(values, Mapping) and name in values:
            return _vector(values[name], 3, f'site {name}')
        return self.body(index, name)

    def body(self, index: int, name: str) -> tuple[float, float, float]:
        values = self.samples[index].get('body_positions')
        if not isinstance(values, Mapping) or name not in values:
            raise B1ContractError(f'trusted body {name!r} is unavailable')
        return _vector(values[name], 3, f'body {name}')

    def quaternion(self, index: int, name: str) -> tuple[float, float, float, float]:
        values = self.samples[index].get('body_quaternions')
        if not isinstance(values, Mapping) or name not in values:
            raise B1ContractError(f'trusted body quaternion {name!r} is unavailable')
        return _normal_quaternion(values[name])

    def joint(self, index: int, name: str) -> float:
        values = self.samples[index].get('joint_positions')
        if not isinstance(values, Mapping) or name not in values:
            raise B1ContractError(f'trusted joint {name!r} is unavailable')
        return _number(values[name], f'joint {name}')

    def joint_velocity(self, index: int, name: str) -> float:
        values = self.samples[index].get('joint_velocities')
        if not isinstance(values, Mapping) or name not in values:
            raise B1ContractError(f'trusted joint velocity {name!r} is unavailable')
        return _number(values[name], f'joint velocity {name}')

    def in_body_frame(self, index: int, point: Sequence[float], body_name: str) -> tuple[float, float, float]:
        return _rotate_inverse(self.quaternion(index, body_name), _sub(point, self.body(index, body_name)))

    def contacts(self, index: int) -> tuple[tuple[str, str, float], ...]:
        raw = self.samples[index].get('contacts')
        if not isinstance(raw, list):
            raise B1ContractError('trusted sample contacts are unavailable')
        result: list[tuple[str, str, float]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise B1ContractError('trusted sample contact is invalid')
            geom1 = item.get('geom1')
            geom2 = item.get('geom2')
            if not isinstance(geom1, str) or not isinstance(geom2, str):
                raise B1ContractError('trusted contact geom names are invalid')
            result.append((geom1, geom2, _number(item.get('distance', 0.0), 'contact distance')))
        return tuple(result)

def _request(ctx: _Context, key: str) -> Any:
    if key not in ctx.request:
        raise B1ContractError(f'B1 request is missing {key}')
    return ctx.request[key]

def _window(ctx: _Context, predicate: Callable[[int], bool], duration_s: float, *, after: int=0) -> tuple[int, int] | None:
    start: int | None = None
    for index in range(max(0, after), len(ctx.samples)):
        if predicate(index):
            if start is None:
                start = index
            if ctx.times[index] - ctx.times[start] + 1e-12 >= duration_s:
                return (start, index)
        else:
            start = None
    return None

def _entry_within_request_budget(ctx: _Context, index: int, key: str, *, after_index: int=0) -> bool:
    """Require a trusted phase entry to occur within its public time budget."""
    budget = _number(_request(ctx, key), key)
    return ctx.times[index] - ctx.times[after_index] <= budget + 1e-09

def _requested_duration_context(ctx: _Context, key: str='duration_s') -> _Context | None:
    """Bound fixed-duration scoring to the requested physical-time horizon."""
    duration = _number(_request(ctx, key), key)
    raw_timestep = ctx.evidence.get('physics_timestep_s', 0.0)
    timestep = _number(raw_timestep, 'physics_timestep_s')
    if timestep < 0.0:
        raise B1ContractError('physics_timestep_s must be non-negative')
    elapsed = ctx.times[-1] - ctx.times[0]
    if elapsed + timestep + 1e-09 < duration:
        return None
    if elapsed > duration + timestep + 1e-09:
        return None
    horizon = ctx.times[0] + duration + timestep + 1e-09
    end = max((index for index, time in enumerate(ctx.times) if time <= horizon))
    return _Context(ctx.parameters, ctx.evidence, ctx.request, ctx.samples[:end + 1], ctx.times[:end + 1])

def _ordered_entries(ctx: _Context, positions: Sequence[Sequence[float]], targets: Sequence[Sequence[float]], tolerance: float, *, start_index: int=0) -> list[int] | None:
    completed: list[int] = []
    target_index = 0
    for index in range(start_index, len(positions)):
        if target_index == len(targets):
            break
        if _distance(positions[index], targets[target_index]) <= tolerance:
            completed.append(index)
            target_index += 1
    return completed if target_index == len(targets) else None

def _path_ok(positions: Sequence[Sequence[float]], points: Sequence[Sequence[float]], tolerance: float, *, through_index: int | None=None) -> bool:
    if len(points) < 2:
        raise B1ContractError('path contract requires at least two points')
    selected = positions if through_index is None else positions[:through_index + 1]
    return all((min((_point_segment_distance(position, start, end) for start, end in zip(points, points[1:]))) <= tolerance for position in selected))

def _sample_speed(ctx: _Context, positions: Sequence[Sequence[float]], index: int) -> float:
    if index <= 0:
        return 0.0
    elapsed = ctx.times[index] - ctx.times[index - 1]
    if elapsed <= 0.0:
        return math.inf
    return _distance(positions[index], positions[index - 1]) / elapsed

def _target_in_initial_yaw_frame(ctx: _Context, body_name: str, translation: Sequence[float]) -> tuple[float, float]:
    start = ctx.body(0, body_name)
    yaw = _yaw(ctx.quaternion(0, body_name))
    x, y = _vector(translation, 2, 'planar translation')
    return (start[0] + math.cos(yaw) * x - math.sin(yaw) * y, start[1] + math.sin(yaw) * x + math.cos(yaw) * y)

def _aperture(ctx: _Context, index: int, *, arm: str | None=None) -> float:
    if arm is None:
        names = [_name(ctx.parameters, 'joint_name')] if 'joint_name' in ctx.parameters else _names(ctx.parameters, 'joint_names')
    else:
        mapping = _mapping(ctx.parameters, 'arm_gripper_joint_names')
        raw = mapping.get(arm)
        if isinstance(raw, str):
            names = [raw]
        elif isinstance(raw, Sequence) and (not isinstance(raw, (str, bytes))) and raw:
            names = [str(item) for item in raw]
        else:
            raise B1ContractError(f'gripper symbols are missing for arm {arm}')
    values = [ctx.joint(index, name) for name in names]
    scales = ctx.parameters.get('joint_scales')
    if isinstance(scales, Sequence) and (not isinstance(scales, (str, bytes))):
        if len(scales) != len(values):
            raise B1ContractError('joint_scales must match aperture joints')
        return math.fsum((value * _number(scale, 'joint scale') for value, scale in zip(values, scales))) / len(values)
    if len(values) == 1:
        return values[0]
    return math.fsum((abs(value) for value in values)) / len(values)

def _aperture_contract(ctx: _Context, *, error_limit: float, hold_s: float, normalized: bool, minimum_excursion: float | None, arm: str | None=None, mirror_limit: float | None=None) -> bool:
    target_fraction = _number(_request(ctx, 'opening_fraction'), 'opening_fraction')
    closed = _limit(ctx.parameters, 'closed_position', 0.0)
    opened = _limit(ctx.parameters, 'open_position', 1.0)
    travel = abs(opened - closed)
    if travel <= 0.0:
        raise B1ContractError('gripper travel must be positive')
    target = closed + target_fraction * (opened - closed)

    def passed(index: int) -> bool:
        error = abs(_aperture(ctx, index, arm=arm) - target)
        if normalized:
            error /= travel
        if error > error_limit:
            return False
        if mirror_limit is not None and arm is not None:
            raw = _mapping(ctx.parameters, 'arm_gripper_joint_names').get(arm)
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != 2:
                raise B1ContractError('mirrored gripper contract requires two joint names')
            disagreement = abs(abs(ctx.joint(index, str(raw[0]))) - abs(ctx.joint(index, str(raw[1])))) / travel
            return disagreement <= mirror_limit
        return True
    target_window = _window(ctx, passed, hold_s)
    if target_window is None:
        return False
    if 'max_duration_s' in ctx.request and (not _entry_within_request_budget(ctx, target_window[0], 'max_duration_s')):
        return False
    if minimum_excursion is not None:
        values = [_aperture(ctx, index, arm=arm) for index in range(len(ctx.samples))]
        if max(values) - min(values) + 1e-12 < minimum_excursion:
            return False
    return True

def _joint_drift_within(ctx: _Context, names_and_limits: Mapping[str, float]) -> bool:
    extrema = ctx.evidence.get('joint_max_abs_deviation_from_reset')
    if isinstance(extrema, Mapping) and all((name in extrema for name in names_and_limits)):
        return all((_number(extrema[name], f'joint drift {name}') <= limit + 1e-12 for name, limit in names_and_limits.items()))
    starts = {name: ctx.joint(0, name) for name in names_and_limits}
    return all((abs(ctx.joint(index, name) - starts[name]) <= limit + 1e-12 for index in range(len(ctx.samples)) for name, limit in names_and_limits.items()))

def _point_drift_within(ctx: _Context, name: str, maximum_displacement_m: float) -> bool:
    for field in ('site_max_displacement_from_reset', 'body_max_displacement_from_reset'):
        extrema = ctx.evidence.get(field)
        if isinstance(extrema, Mapping) and name in extrema:
            return _number(extrema[name], f'point drift {name}') <= maximum_displacement_m + 1e-12
    start = ctx.point(0, name)
    return all((_distance(ctx.point(index, name), start) <= maximum_displacement_m + 1e-12 for index in range(len(ctx.samples))))

def _side_effects_pass(ctx: _Context, contract_id: str) -> bool:
    profile = ctx.parameters.get('side_effect_guard_profile')
    if profile is None:
        return True
    if profile == 'fixed_arm':
        names = ctx.parameters.get('guarded_joint_names', [])
        tolerances = ctx.parameters.get('guarded_joint_tolerances', [])
        if len(names) != len(tolerances):
            raise B1ContractError('fixed arm joint guard names and tolerances must match')
        if any((float(value) <= 0 for value in tolerances)):
            raise B1ContractError('fixed arm joint guard tolerances must be positive')
        if contract_id == 'A3':
            if not names or not _point_drift_within(ctx, _name(ctx.parameters, 'guard_site_name'), 0.015):
                return False
        return _joint_drift_within(ctx, dict(zip(names, tolerances)))
    raise B1ContractError(f'unsupported side-effect guard profile {profile!r}')

def _arm_position(ctx: _Context, contract_id: str, site_name: str, target_key: str, tolerance: float) -> bool:
    target = _vector(_request(ctx, target_key), 3, target_key)
    target_window = _window(ctx, lambda index: _distance(ctx.point(index, site_name), target) <= tolerance, 0.5)
    return target_window is not None and _entry_within_request_budget(ctx, target_window[0], 'max_duration_s')

def _cartesian_path(ctx: _Context, *, site_name: str, waypoint_key: str, waypoint_tolerance: float, cross_track_tolerance: float, final_tolerance: float) -> bool:
    waypoints = _vectors(_request(ctx, waypoint_key), 3, waypoint_key)
    positions = [ctx.point(index, site_name) for index in range(len(ctx.samples))]
    entries = _ordered_entries(ctx, positions, waypoints, waypoint_tolerance)
    if entries is None:
        return False
    budget = _number(_request(ctx, 'max_duration_per_segment_s'), 'segment budget')
    previous_time = ctx.times[0]
    for index in entries:
        if ctx.times[index] - previous_time > budget + 1e-12:
            return False
        previous_time = ctx.times[index]
    if not _path_ok(positions, [positions[0], *waypoints], cross_track_tolerance, through_index=entries[-1]):
        return False
    return _window(ctx, lambda index: index >= entries[-1] and _distance(positions[index], waypoints[-1]) <= final_tolerance, 0.5, after=entries[-1]) is not None

def _offset_return(ctx: _Context, *, positions: Sequence[Sequence[float]], offset: Sequence[float], tolerance: float, outbound_hold_s: float, return_hold_s: float, frame_quaternion: Sequence[float] | None=None) -> bool:
    start = tuple((float(item) for item in positions[0]))
    world_offset = _rotate(frame_quaternion, offset) if frame_quaternion is not None else _vector(offset, 3, 'offset')
    target = _add(start, world_offset)
    outbound = _window(ctx, lambda index: _distance(positions[index], target) <= tolerance, outbound_hold_s)
    if outbound is None:
        return False
    if not _entry_within_request_budget(ctx, outbound[0], 'max_duration_per_leg_s'):
        return False
    returned = _window(ctx, lambda index: _distance(positions[index], start) <= tolerance, return_hold_s, after=outbound[1] + 1)
    if returned is None:
        return False
    if not _entry_within_request_budget(ctx, returned[0], 'max_duration_per_leg_s', after_index=outbound[1]):
        return False
    requested = _norm(world_offset)
    maximum = max((_distance(position, start) for position in positions[:outbound[1] + 1]))
    return requested <= 1e-12 or maximum + 1e-12 >= 0.8 * requested

def _pair_contact(ctx: _Context, index: int, robot_geoms: set[str], target_geoms: set[str]) -> bool:
    return any((geom1 in robot_geoms and geom2 in target_geoms or (geom2 in robot_geoms and geom1 in target_geoms) for geom1, geom2, _ in ctx.contacts(index)))

def _observed_contact_pairs(ctx: _Context) -> set[tuple[str, str]]:
    """Return the union of sampled and per-physics-step trusted contacts."""
    pairs = {tuple(sorted((geom1, geom2))) for index in range(len(ctx.samples)) for geom1, geom2, _ in ctx.contacts(index)}
    raw = ctx.evidence.get('contact_pair_step_counts', [])
    if not isinstance(raw, list):
        raise B1ContractError('trusted per-step contact counts are invalid')
    for item in raw:
        if not isinstance(item, Mapping):
            raise B1ContractError('trusted per-step contact pair is invalid')
        geom1 = item.get('geom1')
        geom2 = item.get('geom2')
        count = item.get('step_count')
        if not isinstance(geom1, str) or not isinstance(geom2, str) or isinstance(count, bool) or (not isinstance(count, int)) or (count < 0):
            raise B1ContractError('trusted per-step contact pair is invalid')
        if count:
            pairs.add(tuple(sorted((geom1, geom2))))
    return pairs

def _first_contact_times(ctx: _Context) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for index in range(len(ctx.samples)):
        for geom1, geom2, _ in ctx.contacts(index):
            pair = tuple(sorted((geom1, geom2)))
            result[pair] = min(result.get(pair, math.inf), ctx.times[index])
    raw = ctx.evidence.get('contact_pair_first_times_s', [])
    if not isinstance(raw, list):
        raise B1ContractError('trusted first-contact evidence is invalid')
    for item in raw:
        if not isinstance(item, Mapping):
            raise B1ContractError('trusted first-contact pair is invalid')
        geom1 = item.get('geom1')
        geom2 = item.get('geom2')
        if not isinstance(geom1, str) or not isinstance(geom2, str):
            raise B1ContractError('trusted first-contact geom names are invalid')
        first_time = _number(item.get('first_time_s'), 'first contact time')
        pair = tuple(sorted((geom1, geom2)))
        result[pair] = min(result.get(pair, math.inf), first_time)
    return result

def _contact_approach(ctx: _Context, *, positions: Sequence[Sequence[float]], precontact: Sequence[float], direction: Sequence[float], robot_geoms: set[str], target_geoms: set[str], precontact_tolerance: float=0.015, stable_precontact: bool=False) -> bool:
    ray = _unit(direction, 'approach direction')
    maximum_travel = _number(_request(ctx, 'max_travel_m'), 'max_travel_m')
    maximum_speed = _number(_request(ctx, 'max_approach_speed_m_s'), 'max_approach_speed_m_s')
    if stable_precontact:
        held_start: int | None = None
        pre_index = None
        for index in range(len(positions)):
            held = _distance(positions[index], precontact) <= precontact_tolerance + 1e-12 and (not _pair_contact(ctx, index, robot_geoms, target_geoms))
            if not held:
                held_start = None
                continue
            if held_start is None:
                held_start = index
            if ctx.times[index] - ctx.times[held_start] + 1e-12 < 0.1:
                continue
            delta = _sub(positions[index], precontact)
            axial = _dot(delta, ray)
            lateral = _norm(_sub(delta, _scale(ray, axial)))
            if axial < -0.002 - 1e-12 or axial > maximum_travel + 0.002 + 1e-12 or lateral > 0.01 + 1e-12 or (index > 0 and _sample_speed(ctx, positions, index) > maximum_speed + 0.01 + 1e-12) or (index + 1 < len(positions) and _sample_speed(ctx, positions, index + 1) > maximum_speed + 0.01 + 1e-12):
                continue
            pre_index = index
            break
    else:
        pre_index = next((index for index, position in enumerate(positions) if _distance(position, precontact) <= precontact_tolerance and (not _pair_contact(ctx, index, robot_geoms, target_geoms))), None)
    if pre_index is None:
        return False
    first_target_contact = min((time_s for (geom1, geom2), time_s in _first_contact_times(ctx).items() if geom1 in robot_geoms and geom2 in target_geoms or (geom2 in robot_geoms and geom1 in target_geoms)), default=math.inf)
    if first_target_contact < ctx.times[pre_index] - 1e-12:
        return False
    contact_window = _window(ctx, lambda index: _pair_contact(ctx, index, robot_geoms, target_geoms), 0.1, after=pre_index + 1)
    if contact_window is None:
        return False
    if not _entry_within_request_budget(ctx, contact_window[0], 'max_duration_s'):
        return False
    contact_index = contact_window[0]
    progress: list[float] = []
    path_length = 0.0
    for index in range(pre_index, contact_index + 1):
        delta = _sub(positions[index], precontact)
        axial = _dot(delta, ray)
        lateral = _norm(_sub(delta, _scale(ray, axial)))
        if axial < -0.002 - 1e-12 or axial > maximum_travel + 0.002 + 1e-12:
            return False
        if lateral > 0.01 + 1e-12:
            return False
        if progress and axial < progress[-1] - 0.002 - 1e-12:
            return False
        progress.append(axial)
        if index > pre_index:
            step_distance = _distance(positions[index], positions[index - 1])
            path_length += step_distance
            elapsed = ctx.times[index] - ctx.times[index - 1]
            if elapsed <= 0.0 or step_distance / elapsed > maximum_speed + 0.01 + 1e-12:
                return False
    if path_length > 1.1 * maximum_travel + 1e-12:
        return False
    for index in range(contact_window[0] + 1, contact_window[1] + 1):
        if _sample_speed(ctx, positions, index) > 0.02 + 1e-12:
            return False
    for geom1, geom2 in _observed_contact_pairs(ctx):
        if geom1 in robot_geoms and geom2 not in robot_geoms and (geom2 not in target_geoms) or (geom2 in robot_geoms and geom1 not in robot_geoms and (geom1 not in target_geoms)):
            return False
    return True

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

def _body_planar(ctx: _Context, body_name: str) -> list[tuple[float, float]]:
    return [ctx.body(index, body_name)[:2] for index in range(len(ctx.samples))]

def _go_twist(ctx: _Context) -> bool:
    bounded = _requested_duration_context(ctx)
    if bounded is None:
        return False
    ctx = bounded
    body = _name(ctx.parameters, 'body_name')
    requested_velocity = _vector(_request(ctx, 'linear_velocity_body_m_s'), 2, 'linear velocity')
    requested_yaw_rate = _number(_request(ctx, 'yaw_rate_rad_s'), 'yaw rate')
    start_time = ctx.times[0] + _number(_request(ctx, 'duration_s'), 'duration_s') - 1.0
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
        actual_body = ((math.cos(yaw) * world_delta[0] + math.sin(yaw) * world_delta[1]) / elapsed, (-math.sin(yaw) * world_delta[0] + math.cos(yaw) * world_delta[1]) / elapsed)
        velocity_errors.append(_distance(actual_body, requested_velocity))
        actual_yaw_rate = _wrapped(_yaw(ctx.quaternion(index, body)) - yaw) / elapsed
        yaw_errors.append(abs(actual_yaw_rate - requested_yaw_rate))
        if requested_speed >= 0.2 and _norm(actual_body) > 1e-09:
            cosine = max(-1.0, min(1.0, _dot(actual_body, requested_velocity) / (_norm(actual_body) * requested_speed)))
            direction_errors.append(math.acos(cosine))
    if not velocity_errors or not yaw_errors:
        return False
    return math.fsum(velocity_errors) / len(velocity_errors) <= 0.1 and math.fsum(yaw_errors) / len(yaw_errors) <= 0.3 and (not direction_errors or math.fsum(direction_errors) / len(direction_errors) <= math.radians(10.0))

def _go_relative_pose(ctx: _Context) -> bool:
    body = _name(ctx.parameters, 'body_name')
    target = _target_in_initial_yaw_frame(ctx, body, _request(ctx, 'translation_initial_yaw_m'))
    target_yaw = _yaw(ctx.quaternion(0, body)) + _number(_request(ctx, 'yaw_delta_rad'), 'yaw_delta_rad')
    positions = _body_planar(ctx, body)
    target_window = _window(ctx, lambda index: _distance(positions[index], target) <= 0.1 and abs(_wrapped(_yaw(ctx.quaternion(index, body)) - target_yaw)) <= 0.0873 and (_sample_speed(ctx, positions, index) <= 0.1), 0.5)
    return target_window is not None and _entry_within_request_budget(ctx, target_window[0], 'max_duration_s')

def _go_path(ctx: _Context) -> bool:
    body = _name(ctx.parameters, 'body_name')
    raw = _vectors(_request(ctx, 'waypoints_initial_yaw_m'), 2, 'waypoints_initial_yaw_m')
    targets = [_target_in_initial_yaw_frame(ctx, body, point) for point in raw]
    positions = _body_planar(ctx, body)
    entries = _ordered_entries(ctx, positions, targets, 0.1)
    if entries is None or not _path_ok(positions, [positions[0], *targets], 0.15, through_index=entries[-1]):
        return False
    target_window = _window(ctx, lambda index: _distance(positions[index], targets[-1]) <= 0.1 and _sample_speed(ctx, positions, index) <= 0.1, 0.5, after=entries[-1])
    return target_window is not None and _entry_within_request_budget(ctx, target_window[0], 'max_duration_s')

def _go_height(ctx: _Context) -> bool:
    body = _name(ctx.parameters, 'body_name')
    target = _number(_request(ctx, 'target_height_m'), 'target_height_m')
    start = ctx.body(0, body)
    start_yaw = _yaw(ctx.quaternion(0, body))
    target_window = _window(ctx, lambda index: abs(ctx.body(index, body)[2] - target) <= 0.03 and all((abs(value) <= 0.1745 for value in _roll_pitch(ctx.quaternion(index, body)))) and (_distance(ctx.body(index, body)[:2], start[:2]) <= 0.05) and (abs(_wrapped(_yaw(ctx.quaternion(index, body)) - start_yaw)) <= 0.0873), 0.5)
    return target_window is not None and _entry_within_request_budget(ctx, target_window[0], 'max_duration_s')

def _go_stance(ctx: _Context) -> bool:
    bounded = _requested_duration_context(ctx)
    if bounded is None:
        return False
    ctx = bounded
    body = _name(ctx.parameters, 'body_name')
    start = ctx.body(0, body)
    recovery_index = next((index for index in range(len(ctx.samples)) if ctx.times[index] - ctx.times[0] <= 0.5 + 1e-12 and all((abs(value) <= 0.0524 for value in _roll_pitch(ctx.quaternion(index, body))))), None)
    if recovery_index is None:
        return False
    positions = [ctx.body(index, body) for index in range(len(ctx.samples))]
    for index in range(recovery_index, len(ctx.samples)):
        if any((abs(value) > 0.0873 for value in _roll_pitch(ctx.quaternion(index, body)))):
            return False
        if abs(positions[index][2] - start[2]) > 0.03 or _distance(positions[index][:2], start[:2]) > 0.05:
            return False
    speeds = [_sample_speed(ctx, [position[:2] for position in positions], index) for index in range(max(1, recovery_index), len(ctx.samples))]
    if not speeds or math.fsum(speeds) / len(speeds) > 0.05:
        return False
    vertical_speed = abs(positions[-1][2] - positions[-2][2]) / max(1e-12, ctx.times[-1] - ctx.times[-2])
    if vertical_speed > 0.05:
        return False
    groups_raw = ctx.parameters.get('foot_geom_groups')
    floor = set(_names(ctx.parameters, 'floor_geom_names'))
    if not isinstance(groups_raw, Sequence) or isinstance(groups_raw, (str, bytes)) or len(groups_raw) != 4:
        raise B1ContractError('G5 requires four foot_geom_groups')
    for raw_group in groups_raw:
        if not isinstance(raw_group, Sequence) or isinstance(raw_group, (str, bytes)) or (not raw_group):
            raise B1ContractError('G5 foot geom group is invalid')
        group = {str(item) for item in raw_group}
        active = [any((left in group and right in floor or (right in group and left in floor) for left, right, _ in ctx.contacts(index))) for index in range(recovery_index, len(ctx.samples))]
        if math.fsum(active) / len(active) < 0.9 or _maximum_contact_loss(ctx, active) > 0.1 + 1e-12:
            return False
    forbidden = set(_names(ctx.parameters, 'forbidden_floor_geom_names'))
    return not any((left in forbidden and right in floor or (right in forbidden and left in floor) for index in range(len(ctx.samples)) for left, right, _ in ctx.contacts(index)))

def evaluate_b1_contract(parameters: Mapping[str, Any], *, evidence: Mapping[str, Any], request: Mapping[str, Any]) -> float:
    """Return a trusted binary outcome for one fixed B1 contract."""
    contract_id = parameters.get('contract_id')
    if not isinstance(contract_id, str) or contract_id not in SUPPORTED_CONTRACT_IDS:
        raise B1ContractError(f'unsupported B1 contract_id {contract_id!r}')
    ctx = _Context.build(parameters, evidence, request)
    if contract_id == 'A1':
        passed = _arm_position(ctx, contract_id, _name(parameters, 'site_name'), 'target_position_m', 0.015)
    elif contract_id == 'A2':
        passed = _cartesian_path(ctx, site_name=_name(parameters, 'site_name'), waypoint_key='waypoints_m', waypoint_tolerance=0.02, cross_track_tolerance=0.02, final_tolerance=0.015)
    elif contract_id == 'A3':
        closed = _limit(parameters, 'closed_position', 0.0)
        opened = _limit(parameters, 'open_position', 1.0)
        passed = _aperture_contract(ctx, error_limit=0.1, hold_s=0.25, normalized=True, minimum_excursion=0.5 * abs(opened - closed))
    elif contract_id == 'A4':
        passed = _contact_approach(ctx, positions=[ctx.point(index, _name(parameters, 'site_name')) for index in range(len(ctx.samples))], precontact=_vector(_request(ctx, 'precontact_position_m'), 3, 'precontact_position_m'), direction=_vector(_request(ctx, 'approach_direction_unit'), 3, 'approach_direction_unit'), robot_geoms=set(_names(parameters, 'tool_geom_names')), target_geoms=set(_names(parameters, 'target_geom_names')), stable_precontact=parameters.get('precontact_gate') == 'held_window_then_ray')
    elif contract_id == 'A5':
        site = _name(parameters, 'site_name')
        base = parameters.get('base_body_name')
        passed = _offset_return(ctx, positions=[ctx.point(index, site) for index in range(len(ctx.samples))], offset=_vector(_request(ctx, 'offset_robot_base_m'), 3, 'offset_robot_base_m'), tolerance=0.015, outbound_hold_s=0.25, return_hold_s=0.5, frame_quaternion=ctx.quaternion(0, str(base)) if isinstance(base, str) else None)
    elif contract_id == 'G1':
        passed = _go_twist(ctx)
    elif contract_id == 'G2':
        passed = _go_relative_pose(ctx)
    elif contract_id == 'G3':
        passed = _go_path(ctx)
    elif contract_id == 'G4':
        passed = _go_height(ctx)
    elif contract_id == 'G5':
        passed = _go_stance(ctx)
    return 1.0 if passed and _side_effects_pass(ctx, contract_id) else 0.0
