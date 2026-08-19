"""Calibration-only Go2 controller for the package's canonical MuJoCo model.

The Framework supplies ``model`` and ``data``.  This reference is deliberately
ordinary Python rather than a TGCD effect table: each public method accepts the
same task request envelope that a generated driver receives, while the small
controller below is only a positive physical-control check.  It writes
actuator commands to ``data.ctrl`` and advances the supplied session with
``mujoco.mj_step``; it never loads a model or writes simulator state.
"""

from __future__ import annotations

import math
from collections import namedtuple
from collections.abc import Mapping, Sequence
from typing import Any

import mujoco
import numpy as np


_MODEL_LEG_ORDER = ("FR", "FL", "RR", "RL")
_HOME = np.asarray([0.0, 0.9, -1.8] * 4, dtype=float)


_GaitPreset = namedtuple(
    "_GaitPreset",
    (
        "swing_thigh",
        "swing_calf",
        "stance_thigh",
        "stance_calf",
        "hip_delta",
        "hip_offset",
        "period_s",
    ),
    defaults=(0.25,),
)


# Source protocol order: 0, 45, 90, 135, 180, -135, -90, -45 degrees.
_DIRECTIONAL_GAITS = (
    _GaitPreset(1.00, -1.35, -0.55, -1.95, -0.10, 0.05),
    _GaitPreset(1.00, -1.70, -1.70, -1.80, 0.24, 0.00),
    _GaitPreset(1.00, -1.70, -1.70, -1.80, 0.08, 0.00),
    _GaitPreset(0.35, -1.50, 0.85, -1.70, 0.25, -0.10),
    _GaitPreset(-0.40, -1.45, 0.70, -2.00, 0.35, -0.10),
    _GaitPreset(0.60, -1.05, 0.55, -1.60, 0.15, 0.15),
    _GaitPreset(0.65, -1.55, 0.15, -1.85, 0.25, 0.10, period_s=0.20),
    _GaitPreset(-1.15, -1.90, 0.90, -1.65, 0.15, 0.00),
)


def _directional_gait(direction_rad: float) -> _GaitPreset:
    octant = int(math.floor(direction_rad / (math.pi / 4.0) + 0.5)) % 8
    return _DIRECTIONAL_GAITS[octant]


def _request(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("request must be an object")
    task_id = value.get("task_id")
    parameters = value.get("task_parameters")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("request.task_id must be a non-empty string")
    if not isinstance(parameters, Mapping):
        raise ValueError("request.task_parameters must be an object")
    return dict(parameters)


def _number(parameters: Mapping[str, Any], name: str, default: float) -> float:
    value = parameters.get(name, default)
    if isinstance(value, bool):
        raise TypeError(f"task_parameters.{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"task_parameters.{name} must be finite")
    return result


class ReferenceGo2Driver:
    """A fixed, source-informed calibration controller for the stock Go2 MJCF."""

    def __init__(self, *, model: Any, data: Any) -> None:
        self.model = model
        self.data = data
        self._base_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base_link"))
        if self._base_id < 0:
            raise ValueError("base_link is absent from the canonical Go2 model")

        self._actuator_ids: list[int] = []
        self._qpos_addresses: list[int] = []
        self._qvel_addresses: list[int] = []
        self._joint_limits: list[tuple[float, float]] = []
        self._control_limits: list[tuple[float, float]] = []
        for leg in _MODEL_LEG_ORDER:
            for suffix in ("hip", "thigh", "calf"):
                joint_name = f"{leg}_{suffix}_joint"
                actuator_name = f"{leg}_{suffix}"
                joint_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name))
                actuator_id = int(
                    mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
                )
                if joint_id < 0 or actuator_id < 0:
                    raise ValueError(f"missing Go2 joint/actuator pair {joint_name!r}")
                self._actuator_ids.append(actuator_id)
                self._qpos_addresses.append(int(model.jnt_qposadr[joint_id]))
                self._qvel_addresses.append(int(model.jnt_dofadr[joint_id]))
                self._joint_limits.append(
                    (float(model.jnt_range[joint_id, 0]), float(model.jnt_range[joint_id, 1]))
                )
                self._control_limits.append(
                    (
                        float(model.actuator_ctrlrange[actuator_id, 0]),
                        float(model.actuator_ctrlrange[actuator_id, 1]),
                    )
                )

    def _clip_targets(self, targets: Sequence[float]) -> np.ndarray:
        result = np.asarray(targets, dtype=float)
        if result.shape != (12,) or not np.all(np.isfinite(result)):
            raise ValueError("reference target must contain 12 finite joint values")
        low = np.asarray([item[0] for item in self._joint_limits], dtype=float)
        high = np.asarray([item[1] for item in self._joint_limits], dtype=float)
        return np.clip(result, low, high)

    def _torque(self, target: Sequence[float], *, kp: float = 80.0, kd: float = 4.0) -> np.ndarray:
        target_array = np.asarray(target, dtype=float)
        if target_array.shape != (12,) or not np.all(np.isfinite(target_array)):
            raise ValueError("reference target must contain 12 finite joint values")
        q = np.asarray([self.data.qpos[address] for address in self._qpos_addresses], dtype=float)
        qd = np.asarray([self.data.qvel[address] for address in self._qvel_addresses], dtype=float)
        # The actuator saturation is the physical command boundary.  Clipping
        # the gait target first would apply asymmetric joint limits in the
        # controller's FR/FL/RR/RL order and change the gait itself.
        command = kp * (target_array - q) - kd * qd
        low = np.asarray([item[0] for item in self._control_limits], dtype=float)
        high = np.asarray([item[1] for item in self._control_limits], dtype=float)
        return np.clip(command, low, high)

    def _apply(self, target: Sequence[float], *, kp: float = 80.0, kd: float = 4.0) -> None:
        command = self._torque(target, kp=kp, kd=kd)
        for index, actuator_id in enumerate(self._actuator_ids):
            self.data.ctrl[actuator_id] = float(command[index])

    def _steps(self, duration_s: float) -> int:
        if duration_s <= 0.0:
            raise ValueError("duration_s must be positive")
        return max(1, int(math.ceil(duration_s / float(self.model.opt.timestep))))

    def _posture(self, target: Sequence[float], duration_s: float, *, kp: float = 80.0, kd: float = 4.0) -> None:
        target_array = self._clip_targets(target)
        start = np.asarray([self.data.qpos[address] for address in self._qpos_addresses], dtype=float)
        steps = self._steps(duration_s)
        for index in range(steps):
            fraction = (index + 1) / steps
            self._apply((1.0 - fraction) * start + fraction * target_array, kp=kp, kd=kd)
            mujoco.mj_step(self.model, self.data)

    def _gait_target(
        self,
        phase: float,
        *,
        mode: str,
        hip_bias: float = 0.0,
        turn_pattern: Sequence[float] | None = None,
        directional_preset: _GaitPreset | None = None,
    ) -> np.ndarray:
        target = np.zeros(12, dtype=float)
        for leg_index, phase_offset in enumerate((0.5, 0.0, 0.0, 0.5)):
            leg_phase = (phase + phase_offset) % 1.0
            swing = leg_phase < 0.4
            if directional_preset is not None:
                if swing:
                    thigh = directional_preset.swing_thigh
                    calf = directional_preset.swing_calf
                    hip = directional_preset.hip_offset + directional_preset.hip_delta
                else:
                    thigh = directional_preset.stance_thigh
                    calf = directional_preset.stance_calf
                    hip = directional_preset.hip_offset - directional_preset.hip_delta
            elif mode == "backward":
                swing_thigh, swing_calf = 1.3, -1.3
                stance_thigh, stance_calf = 0.5, -1.8
            elif mode == "clearance":
                swing_thigh, swing_calf = 1.4, -1.2
                stance_thigh, stance_calf = -1.7, -1.8
            else:
                swing_thigh, swing_calf = 1.0, -1.7
                stance_thigh, stance_calf = -1.7, -1.8
            if directional_preset is None:
                if swing:
                    thigh, calf = swing_thigh, swing_calf
                else:
                    thigh, calf = stance_thigh, stance_calf
                hip = hip_bias
                if turn_pattern is not None:
                    hip = float(turn_pattern[leg_index])
            target[3 * leg_index : 3 * leg_index + 3] = (hip, thigh, calf)
        return target

    def _gait(
        self,
        duration_s: float,
        *,
        mode: str = "forward",
        hip_bias: float = 0.0,
        turn_pattern: Sequence[float] | None = None,
        direction_rad: float | None = None,
    ) -> None:
        steps = self._steps(duration_s)
        directional_preset = (
            _directional_gait(direction_rad) if direction_rad is not None else None
        )
        period_s = (
            directional_preset.period_s
            if directional_preset is not None
            else 0.25
        )
        timestep = float(self.model.opt.timestep)
        for index in range(steps):
            phase = ((index * timestep) % period_s) / period_s
            self._apply(
                self._gait_target(
                    phase,
                    mode=mode,
                    hip_bias=hip_bias,
                    turn_pattern=turn_pattern,
                    directional_preset=directional_preset,
                )
            )
            mujoco.mj_step(self.model, self.data)

    def stand(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._posture(_HOME, _number(parameters, "duration_s", 0.8))

    def crouch(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        target = _HOME.copy()
        target[1::3] += 0.45
        target[2::3] -= 0.35
        self._posture(target, _number(parameters, "duration_s", 0.8), kp=60.0, kd=3.0)

    def recover_stand(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._posture(_HOME, _number(parameters, "duration_s", 0.8))

    def balance_hold(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._posture(_HOME, _number(parameters, "duration_s", 0.5))

    def walk_forward(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(
            _number(parameters, "duration_s", 1.0),
            mode="forward",
            direction_rad=_number(parameters, "direction_rad", 0.0),
        )

    def walk_backward(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(_number(parameters, "duration_s", 1.0), mode="backward")

    def walk_lateral(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(_number(parameters, "duration_s", 1.0), hip_bias=_number(parameters, "hip_bias", -0.2))

    def walk_diagonal(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(_number(parameters, "duration_s", 1.0), hip_bias=_number(parameters, "hip_bias", 0.2))

    def stop(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._posture(_HOME, _number(parameters, "duration_s", 0.4))

    def turn_in_place(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        direction = _number(parameters, "direction", 1.0)
        pattern = (0.1, 0.1, -0.1, -0.1) if direction >= 0.0 else (0.1, 0.1, 0.1, 0.1)
        self._gait(_number(parameters, "duration_s", 0.15), turn_pattern=pattern)

    def reach_waypoint(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(_number(parameters, "duration_s", 1.0), mode="forward")

    def follow_path(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(_number(parameters, "first_segment_s", 0.8), mode="forward")
        self._gait(_number(parameters, "second_segment_s", 0.8), mode="forward")

    def traverse_ramp(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(_number(parameters, "duration_s", 1.0), mode="forward")

    def traverse_step(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(_number(parameters, "duration_s", 0.8), mode="forward")

    def traverse_stairs(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(_number(parameters, "duration_s", 0.8), mode="clearance")

    def traverse_blocks(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(_number(parameters, "duration_s", 1.5), mode="forward")

    def traverse_stepping_stones(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(_number(parameters, "duration_s", 0.5), mode="forward")

    def traverse_poles(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(_number(parameters, "duration_s", 1.0), mode="forward")

    def traverse_rough_rigid(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(_number(parameters, "duration_s", 1.5), mode="forward")

    def gait_cycle(self, request: Mapping[str, Any]) -> None:
        parameters = _request(request)
        self._gait(_number(parameters, "duration_s", 0.5), mode="forward")


def build(*, model: Any, data: Any) -> ReferenceGo2Driver:
    """Bind the calibration controller to Framework-owned canonical objects."""

    return ReferenceGo2Driver(model=model, data=data)
