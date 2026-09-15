"""Physical verdicts for the five selected LEAP catalog tasks.

The evaluator consumes only the native MuJoCo trace passed by
``auto_adapter.demo_evaluation.evaluate_demo_task``.  A control step is
sampled from that continuous trace at the source control period; a physics
sample (the LEAP MJCF uses a 2 ms MuJoCo step) is never treated as a source
control step.

The experiment scenes use these labels in ``success_spec.bindings``:

* ``palm`` (body), ``index_tip``, ``middle_tip``, ``ring_tip`` and
  ``thumb_tip`` (geoms),
* the sixteen public joint names (joints), and
* ``object`` (body or geom) for the two object tasks and ``fixture_joint``
  (joint) for the rotary fixture.

The target parameters are the names in the maintained catalog invocation
schemas.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
import math
from numbers import Real
from typing import Any


_REACH_THRESHOLD_M = 0.00894427190999916
_JOINT_THRESHOLD_RAD = 10.0 * math.pi / 180.0
_HOLD_SOLVED_THRESHOLD_M = 0.010
_HOLD_DROP_THRESHOLD_M = 0.300
_BLOCK_POSITION_THRESHOLD_M = 0.010
_BLOCK_ANGLE_THRESHOLD_RAD = 0.1
_TURN_ANGLE_THRESHOLD_RAD = 0.1


# These periods are from the source environment stepping contracts checked by
# the Chapter 5 robot-check agent: 20 x .002 s for the Gym hand tasks, 10 x
# .002 s for MyoSuite hold, and the fixed 4 s / 80 and 40 step D'Claw
# adaptations.
_TASKS: dict[str, dict[str, float | int]] = {
    "gym_hand_reach_all_fingertips": {"horizon": 50, "control_dt_s": 0.04},
    "robel_dclaw_pose_fixed": {"horizon": 80, "control_dt_s": 0.05},
    "myosuite_object_hold_fixed": {"horizon": 75, "control_dt_s": 0.02},
    "gym_hand_manipulate_block_full_pose": {"horizon": 100, "control_dt_s": 0.04},
    "robel_dclaw_turn_fixed": {"horizon": 40, "control_dt_s": 0.10},
}

_FINGERTIP_ORDER = ("index_tip", "middle_tip", "ring_tip", "thumb_tip")
_JOINT_ORDER = (
    "if_mcp",
    "if_rot",
    "if_pip",
    "if_dip",
    "mf_mcp",
    "mf_rot",
    "mf_pip",
    "mf_dip",
    "rf_mcp",
    "rf_rot",
    "rf_pip",
    "rf_dip",
    "th_cmc",
    "th_axl",
    "th_mcp",
    "th_ipl",
)


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _vector(value: Any, size: int, name: str) -> list[float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != size:
        raise ValueError(f"{name} must have length {size}")
    return [_number(item, name) for item in value]


def _metric(check: str, value: Any, threshold: Any, ok: bool, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"check": check, "value": value, "threshold": threshold, "ok": bool(ok)}
    item.update(extra)
    return item


def _position(samples: list[Mapping[str, Any]], label: str, index: int) -> list[float]:
    # evaluate_demo_task has already checked labels and 3-vector positions.
    return [float(value) for value in samples[index]["state"][label]["position"]]


def _rotation(samples: list[Mapping[str, Any]], label: str, index: int) -> list[float]:
    # Rotation is not part of the shared validator because most families do
    # not consume it, so validate the one extra field needed by block pose.
    return _vector(samples[index]["state"][label]["rotation"], 9,
                   f"sample[{index}].{label}.rotation")


def _joint_value(samples: list[Mapping[str, Any]], label: str, index: int) -> float:
    # Joint values are likewise checked by evaluate_demo_task.
    return float(samples[index]["state"][label]["value"])


def _distance(first: Sequence[float], second: Sequence[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(first, second)))


def _validate_fixed_parameters(task_id: str, parameters: Mapping[str, Any]) -> tuple[int, float, int]:
    contract = _TASKS[task_id]
    horizon = int(contract["horizon"])
    control_dt = float(contract["control_dt_s"])
    max_steps = _integer(parameters.get("max_control_steps"), "parameters.max_control_steps")
    if max_steps != horizon:
        raise ValueError(f"parameters.max_control_steps must be {horizon}")
    if task_id in {"robel_dclaw_pose_fixed", "robel_dclaw_turn_fixed"}:
        duration = _number(parameters.get("duration_s"), "parameters.duration_s")
        expected = horizon * control_dt
        if abs(duration - expected) > 1e-12:
            raise ValueError(f"parameters.duration_s must be {expected:g}")
    return horizon, control_dt, max_steps


def _control_indices(times: Sequence[float], horizon: int, control_dt: float) -> list[int] | None:
    """Select exact source-control timestamps from a continuous trace.

    ``DemoTrace`` records every native physics step.  The expected source
    timestamps are therefore looked up independently, and each one must have
    a sample within a tight numerical tolerance.  Missing any control sample
    makes the attempt unscorable for its full horizon; an early target hit is
    never substituted for the terminal sample.
    """
    if not times:
        return None
    start = float(times[0])
    selected: list[int] = []
    previous = -1
    for step in range(horizon + 1):
        target = start + step * control_dt
        cursor = bisect_left(times, target)
        candidates = [index for index in (cursor - 1, cursor)
                      if 0 <= index < len(times) and index > previous]
        if not candidates:
            return None
        index = min(candidates, key=lambda candidate: abs(float(times[candidate]) - target))
        if abs(float(times[index]) - target) > 1e-7:
            return None
        selected.append(index)
        previous = index
    return selected


def _coverage_metric(
    times: Sequence[float], indices: list[int] | None, horizon: int, control_dt: float,
) -> dict[str, Any]:
    expected_duration = horizon * control_dt
    observed_duration = None if indices is None else float(times[indices[-1]] - times[indices[0]])
    return _metric(
        "control_horizon_covered", observed_duration, expected_duration,
        indices is not None,
        control_steps=horizon,
        control_dt_s=control_dt,
    )


def _bindings(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(spec.get("bindings"), "success_spec.bindings")


def _palm_frame(
    spec: Mapping[str, Any], samples: list[Mapping[str, Any]],
) -> tuple[list[float], list[float]]:
    """Return the fixed palm position and row-major world rotation.

    The LEAP source targets are expressed in the right-palm Cartesian frame.
    The palm binding is therefore required for every task with a palm-frame
    target.
    """
    bindings = _bindings(spec)
    if "palm" not in bindings:
        raise ValueError("success_spec.bindings is missing 'palm'")
    return _position(samples, "palm", 0), _rotation(samples, "palm", 0)


def _mat_vec(matrix: Sequence[float], vector: Sequence[float]) -> list[float]:
    return [
        sum(float(matrix[3 * row + column]) * float(vector[column]) for column in range(3))
        for row in range(3)
    ]


def _mat_mul(first: Sequence[float], second: Sequence[float]) -> list[float]:
    return [
        sum(float(first[3 * row + k]) * float(second[3 * k + column]) for k in range(3))
        for row in range(3) for column in range(3)
    ]


def _mat_transpose(matrix: Sequence[float]) -> list[float]:
    return [float(matrix[3 * column + row]) for row in range(3) for column in range(3)]


def _quat_to_matrix(quaternion: Sequence[float]) -> list[float]:
    values = _vector(quaternion, 4, "target_orientation_wxyz")
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 1e-15:
        raise ValueError("target_orientation_wxyz must be nonzero")
    w, x, y, z = (value / norm for value in values)
    return [
        1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w),
        2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w),
        2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y),
    ]


def _rotation_angle(first: Sequence[float], second: Sequence[float]) -> float:
    """Shortest angle of ``first.T @ second`` for two row-major rotations."""
    relative = _mat_mul(_mat_transpose(first), second)
    cosine = (relative[0] + relative[4] + relative[8] - 1.0) / 2.0
    return math.acos(max(-1.0, min(1.0, cosine)))


def _wrap_angle(angle: float) -> float:
    return (float(angle) + math.pi) % (2.0 * math.pi) - math.pi


def _require_fingertips(bindings: Mapping[str, Any]) -> None:
    if any(label not in bindings for label in _FINGERTIP_ORDER):
        raise ValueError("success_spec.bindings must expose all four named fingertips")


def _require_joints(bindings: Mapping[str, Any]) -> None:
    if any(label not in bindings for label in _JOINT_ORDER):
        raise ValueError("success_spec.bindings must expose the 16 public hand joints")


def _reach(
    spec: Mapping[str, Any], parameters: Mapping[str, Any],
    samples: list[Mapping[str, Any]], times: list[float],
    horizon: int, control_dt: float,
) -> list[dict[str, Any]]:
    target_local = _vector(parameters.get("target_fingertip_positions_m"), 12,
                           "parameters.target_fingertip_positions_m")
    bindings = _bindings(spec)
    _require_fingertips(bindings)
    labels = _FINGERTIP_ORDER
    palm_position, palm_rotation = _palm_frame(spec, samples)
    target_world: list[float] = []
    for offset in range(0, 12, 3):
        local = target_local[offset:offset + 3]
        target_world.extend([palm_position[i] + _mat_vec(palm_rotation, local)[i] for i in range(3)])
    indices = _control_indices(times, horizon, control_dt)
    metrics = [_coverage_metric(times, indices, horizon, control_dt)]
    if indices is None:
        metrics.append(_metric("concatenated_fingertip_cartesian_l2_error_m", None,
                               _REACH_THRESHOLD_M, False))
        return metrics
    final = indices[-1]
    actual = [coordinate for label in labels for coordinate in _position(samples, label, final)]
    error = _distance(actual, target_world)
    metrics.append(_metric("concatenated_fingertip_cartesian_l2_error_m", error,
                           _REACH_THRESHOLD_M, error < _REACH_THRESHOLD_M,
                           fingertip_order=list(_FINGERTIP_ORDER)))
    return metrics


def _pose(
    spec: Mapping[str, Any], parameters: Mapping[str, Any],
    samples: list[Mapping[str, Any]], times: list[float],
    horizon: int, control_dt: float,
) -> list[dict[str, Any]]:
    targets = _vector(parameters.get("target_joint_positions_rad"), 16,
                      "parameters.target_joint_positions_rad")
    bindings = _bindings(spec)
    _require_joints(bindings)
    labels = _JOINT_ORDER
    indices = _control_indices(times, horizon, control_dt)
    metrics = [_coverage_metric(times, indices, horizon, control_dt)]
    if indices is None:
        metrics.append(_metric("maximum_absolute_joint_position_error_rad", None,
                               _JOINT_THRESHOLD_RAD, False))
        return metrics
    final = indices[-1]
    errors = [_joint_value(samples, label, final) - target
              for label, target in zip(labels, targets, strict=True)]
    maximum = max(abs(error) for error in errors)
    metrics.append(_metric("maximum_absolute_joint_position_error_rad", maximum,
                           _JOINT_THRESHOLD_RAD, maximum < _JOINT_THRESHOLD_RAD,
                           joint_order=list(labels)))
    return metrics


def _hold(
    spec: Mapping[str, Any], parameters: Mapping[str, Any],
    samples: list[Mapping[str, Any]], times: list[float],
    horizon: int, control_dt: float,
) -> list[dict[str, Any]]:
    target_local = _vector(parameters.get("target_object_position_m"), 3,
                           "parameters.target_object_position_m")
    bindings = _bindings(spec)
    if "object" not in bindings:
        raise ValueError("success_spec.bindings is missing 'object'")
    object_label = "object"
    palm_position, palm_rotation = _palm_frame(spec, samples)
    target_world = [palm_position[i] + _mat_vec(palm_rotation, target_local)[i] for i in range(3)]
    indices = _control_indices(times, horizon, control_dt)
    metrics = [_coverage_metric(times, indices, horizon, control_dt)]
    if indices is None:
        metrics.extend([
            _metric("object_hold_solved_control_step_count", None, 5, False),
            _metric("object_hold_drop_event_count", None, 0, False),
        ])
        return metrics
    # The initial sample is the pre-control state.  Source hold scoring counts
    # exactly the 75 control-step samples, steps 1..75.
    control_samples = indices[1:]
    distances = [_distance(_position(samples, object_label, index), target_world)
                 for index in control_samples]
    solved = sum(distance < _HOLD_SOLVED_THRESHOLD_M for distance in distances)
    drops = sum(distance > _HOLD_DROP_THRESHOLD_M for distance in distances)
    metrics.extend([
        _metric("object_hold_solved_control_step_count", solved, 5, solved > 5),
        _metric("object_hold_drop_event_count", drops, 0, drops == 0),
    ])
    return metrics


def _block(
    spec: Mapping[str, Any], parameters: Mapping[str, Any],
    samples: list[Mapping[str, Any]], times: list[float],
    horizon: int, control_dt: float,
) -> list[dict[str, Any]]:
    offset_local = _vector(parameters.get("target_position_offset_m"), 3,
                           "parameters.target_position_offset_m")
    target_quaternion = _vector(parameters.get("target_orientation_wxyz"), 4,
                                "parameters.target_orientation_wxyz")
    bindings = _bindings(spec)
    if "object" not in bindings:
        raise ValueError("success_spec.bindings is missing 'object'")
    object_label = "object"
    palm_position, palm_rotation = _palm_frame(spec, samples)
    initial_position = _position(samples, object_label, 0)
    initial_rotation = _rotation(samples, object_label, 0)
    target_position = [initial_position[i] + _mat_vec(initial_rotation, offset_local)[i]
                      for i in range(3)]
    target_rotation = _mat_mul(palm_rotation, _quat_to_matrix(target_quaternion))
    indices = _control_indices(times, horizon, control_dt)
    metrics = [_coverage_metric(times, indices, horizon, control_dt)]
    if indices is None:
        metrics.extend([
            _metric("block_target_euclidean_position_error_m", None,
                    _BLOCK_POSITION_THRESHOLD_M, False),
            _metric("block_target_shortest_quaternion_angle_error_rad", None,
                    _BLOCK_ANGLE_THRESHOLD_RAD, False),
        ])
        return metrics
    final = indices[-1]
    position_error = _distance(_position(samples, object_label, final), target_position)
    angle_error = _rotation_angle(target_rotation, _rotation(samples, object_label, final))
    metrics.extend([
        _metric("block_target_euclidean_position_error_m", position_error,
                _BLOCK_POSITION_THRESHOLD_M, position_error < _BLOCK_POSITION_THRESHOLD_M),
        _metric("block_target_shortest_quaternion_angle_error_rad", angle_error,
                _BLOCK_ANGLE_THRESHOLD_RAD, angle_error < _BLOCK_ANGLE_THRESHOLD_RAD),
    ])
    return metrics


def _turn(
    spec: Mapping[str, Any], parameters: Mapping[str, Any],
    samples: list[Mapping[str, Any]], times: list[float],
    horizon: int, control_dt: float,
) -> list[dict[str, Any]]:
    initial_target = _number(parameters.get("initial_fixture_angle_rad"),
                             "parameters.initial_fixture_angle_rad")
    target = _number(parameters.get("target_fixture_angle_rad"),
                     "parameters.target_fixture_angle_rad")
    if abs(initial_target) > 1e-12:
        raise ValueError("parameters.initial_fixture_angle_rad must be 0")
    if abs(target - math.pi) > 1e-12:
        raise ValueError("parameters.target_fixture_angle_rad must be pi")
    bindings = _bindings(spec)
    if "fixture_joint" not in bindings:
        raise ValueError("success_spec.bindings is missing 'fixture_joint'")
    fixture_label = "fixture_joint"
    indices = _control_indices(times, horizon, control_dt)
    metrics = [_coverage_metric(times, indices, horizon, control_dt)]
    if indices is None:
        metrics.append(_metric("absolute_wrapped_fixture_target_angle_error_rad", None,
                               _TURN_ANGLE_THRESHOLD_RAD, False))
        return metrics
    final_actual = _joint_value(samples, fixture_label, indices[-1])
    error = abs(_wrap_angle(final_actual - target))
    metrics.append(_metric("absolute_wrapped_fixture_target_angle_error_rad", error,
                           _TURN_ANGLE_THRESHOLD_RAD, error < _TURN_ANGLE_THRESHOLD_RAD))
    return metrics


def evaluate_leap_task(
    spec: Mapping[str, Any], parameters: Mapping[str, Any],
    samples: list[Mapping[str, Any]], times: list[float],
) -> list[dict[str, Any]]:
    """Evaluate one selected LEAP task from an independent physical trace."""
    task_id = spec.get("task_id")
    if task_id not in _TASKS:
        raise ValueError(f"unknown LEAP catalog task {task_id!r}")
    if not isinstance(parameters, Mapping):
        raise ValueError("parameters must be a mapping")
    if not isinstance(times, Sequence) or isinstance(times, (str, bytes)):
        raise ValueError("times must be a sequence")
    if len(times) != len(samples):
        raise ValueError("times must align with samples")
    horizon, control_dt, _ = _validate_fixed_parameters(task_id, parameters)
    declared_dt = spec.get("control_dt_s")
    if declared_dt is not None and abs(_number(declared_dt, "success_spec.control_dt_s") - control_dt) > 1e-12:
        raise ValueError(f"success_spec.control_dt_s must be {control_dt:g}")
    if task_id == "gym_hand_reach_all_fingertips":
        return _reach(spec, parameters, samples, list(times), horizon, control_dt)
    if task_id == "robel_dclaw_pose_fixed":
        return _pose(spec, parameters, samples, list(times), horizon, control_dt)
    if task_id == "myosuite_object_hold_fixed":
        return _hold(spec, parameters, samples, list(times), horizon, control_dt)
    if task_id == "gym_hand_manipulate_block_full_pose":
        return _block(spec, parameters, samples, list(times), horizon, control_dt)
    return _turn(spec, parameters, samples, list(times), horizon, control_dt)


__all__ = ["evaluate_leap_task"]
