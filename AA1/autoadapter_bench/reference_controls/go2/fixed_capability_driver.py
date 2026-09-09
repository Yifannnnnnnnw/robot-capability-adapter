"""Private Go2 reference driver for the fixed G1--G5 interface.

The Framework owns the canonical MuJoCo model, data, reset, and verdict.  This
hidden positive control accepts only capability-native requests and advances
the supplied physical session through torque actuators.  It may be pinned as
the fixed driver for the separately declared B2 extension, but is never shown
to Driver Synthesis or to a high-level controller.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import mujoco
import numpy as np

from auto_adapter.skeletons.go2_velocity_policy import (
    Go2VelocityPolicySkeleton,
    Go2VelocityPolicySpec,
)


_LEG_ORDER = ("FL", "FR", "RL", "RR")
_JOINT_SUFFIXES = ("hip", "thigh", "calf")
_HOME = np.asarray([0.0, 0.9, -1.8] * 4, dtype=float)
_POLICY_PERIOD_S = 0.02
_CONTROL_PERIOD_S = 0.10
_POLICY_SPEC = Go2VelocityPolicySpec(
    base_body_name="base_link",
    joint_names=tuple(
        f"{leg}_{suffix}_joint"
        for leg in _LEG_ORDER
        for suffix in _JOINT_SUFFIXES
    ),
    actuator_names=tuple(
        f"{leg}_{suffix}"
        for leg in _LEG_ORDER
        for suffix in _JOINT_SUFFIXES
    ),
    default_joint_angles=(
        0.1,
        0.8,
        -1.5,
        -0.1,
        0.8,
        -1.5,
        0.1,
        1.0,
        -1.5,
        -0.1,
        1.0,
        -1.5,
    ),
    kd=0.8,
)


def _request(value: Any, fields: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"request must contain exactly {sorted(fields)}")
    return value


def _number(
    value: Any,
    name: str,
    *,
    lower: float | None = None,
    upper: float | None = None,
) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if lower is not None and result < lower:
        raise ValueError(f"{name} is below its public bound")
    if upper is not None and result > upper:
        raise ValueError(f"{name} is above its public bound")
    return result


def _vector(value: Any, size: int, name: str) -> np.ndarray:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be an array")
    result = np.asarray(value, dtype=float)
    if result.shape != (size,) or not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain {size} finite numbers")
    return result


def _wrap(angle: float) -> float:
    return (angle + math.pi) % math.tau - math.pi


class ReferenceGo2Driver:
    """Actuator-only positive control for the frozen Go2 capability ABI."""

    def __init__(self, *, model: Any, data: Any) -> None:
        self.model = model
        self.data = data
        self._timestep = float(model.opt.timestep)
        if not math.isfinite(self._timestep) or self._timestep <= 0.0:
            raise ValueError("canonical timestep must be positive")
        self._base_body = self._name_id(
            mujoco.mjtObj.mjOBJ_BODY, "base_link"
        )
        self._policy: Go2VelocityPolicySkeleton | None = None
        self._actuators: list[int] = []
        self._qpos_addresses: list[int] = []
        self._qvel_addresses: list[int] = []
        self._control_low: list[float] = []
        self._control_high: list[float] = []
        for leg in _LEG_ORDER:
            for suffix in _JOINT_SUFFIXES:
                joint_id = self._name_id(
                    mujoco.mjtObj.mjOBJ_JOINT,
                    f"{leg}_{suffix}_joint",
                )
                actuator_id = self._name_id(
                    mujoco.mjtObj.mjOBJ_ACTUATOR,
                    f"{leg}_{suffix}",
                )
                self._actuators.append(actuator_id)
                self._qpos_addresses.append(int(model.jnt_qposadr[joint_id]))
                self._qvel_addresses.append(int(model.jnt_dofadr[joint_id]))
                self._control_low.append(
                    float(model.actuator_ctrlrange[actuator_id, 0])
                )
                self._control_high.append(
                    float(model.actuator_ctrlrange[actuator_id, 1])
                )

    def _name_id(self, object_type: Any, name: str) -> int:
        identifier = int(mujoco.mj_name2id(self.model, object_type, name))
        if identifier < 0:
            raise ValueError(f"canonical model is missing {name!r}")
        return identifier

    def _steps(self, duration_s: float) -> int:
        return max(1, int(math.floor(duration_s / self._timestep + 1.0e-12)))

    def _bounded_duration(self, duration_s: float) -> float:
        steps = int(math.floor(duration_s / self._timestep + 1.0e-12))
        return max(steps, 0) * self._timestep

    def _pose(self) -> tuple[np.ndarray, float]:
        position = np.asarray(
            self.data.xpos[self._base_body, :2], dtype=float
        ).copy()
        rotation = np.asarray(
            self.data.xmat[self._base_body], dtype=float
        ).reshape(3, 3)
        yaw = math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))
        return position, yaw

    @staticmethod
    def _initial_frame_target(
        origin: np.ndarray,
        initial_yaw: float,
        displacement: np.ndarray,
    ) -> np.ndarray:
        cosine = math.cos(initial_yaw)
        sine = math.sin(initial_yaw)
        return origin + np.asarray(
            (
                cosine * displacement[0] - sine * displacement[1],
                sine * displacement[0] + cosine * displacement[1],
            ),
            dtype=float,
        )

    @staticmethod
    def _world_to_body(vector: np.ndarray, yaw: float) -> np.ndarray:
        cosine = math.cos(yaw)
        sine = math.sin(yaw)
        return np.asarray(
            (
                cosine * vector[0] + sine * vector[1],
                -sine * vector[0] + cosine * vector[1],
            ),
            dtype=float,
        )

    def _policy_command(
        self,
        linear_body: np.ndarray,
        yaw_rate: float,
        duration_s: float,
    ) -> None:
        if self._policy is None:
            raise RuntimeError("locomotion policy was not started for this call")
        bounded_duration = self._bounded_duration(duration_s)
        if bounded_duration <= 0.0:
            return
        self._policy.command_planar_velocity(
            float(linear_body[0]),
            float(linear_body[1]),
            float(yaw_rate),
            duration=bounded_duration,
        )

    def _start_policy(self) -> None:
        """Start each locomotion call from fresh history and current physics."""

        self._policy = Go2VelocityPolicySkeleton(
            model=self.model,
            data=self.data,
            spec=_POLICY_SPEC,
        )

    def _settle_policy(self, duration_s: float = 0.70) -> None:
        self._policy_command(np.zeros(2, dtype=float), 0.0, duration_s)

    def _run_pose_path(
        self,
        targets: Sequence[np.ndarray],
        *,
        target_yaw: float,
        max_duration_s: float,
    ) -> None:
        if not targets:
            raise ValueError("pose path requires at least one target")
        elapsed = 0.0
        target_index = 0
        while elapsed + 1.0e-12 < max_duration_s:
            position, yaw = self._pose()
            error = targets[target_index] - position
            distance = float(np.linalg.norm(error))
            if distance <= 0.045 and target_index + 1 < len(targets):
                target_index += 1
                continue
            yaw_error = _wrap(target_yaw - yaw)
            if (
                target_index + 1 == len(targets)
                and distance <= 0.035
                and abs(yaw_error) <= 0.035
            ):
                break
            world_velocity = np.clip(2.2 * error, -0.42, 0.42)
            body_velocity = 1.4 * self._world_to_body(world_velocity, yaw)
            speed = float(np.linalg.norm(body_velocity))
            if speed > 1.0:
                body_velocity *= 1.0 / speed
            yaw_rate = float(np.clip(4.25 * yaw_error, -1.0, 1.0))
            step_duration = self._bounded_duration(
                min(_CONTROL_PERIOD_S, max_duration_s - elapsed)
            )
            if step_duration <= 0.0:
                break
            self._policy_command(body_velocity, yaw_rate, step_duration)
            elapsed += step_duration
        remaining = self._bounded_duration(max_duration_s - elapsed)
        if remaining > 0.0:
            self._settle_policy(min(0.70, remaining))

    def _hold_posture(
        self,
        target: np.ndarray,
        duration_s: float,
        *,
        kp: float = 80.0,
        kd: float = 4.0,
    ) -> None:
        if target.shape != (12,) or not np.all(np.isfinite(target)):
            raise ValueError("posture target must contain 12 finite values")
        low = np.asarray(self._control_low, dtype=float)
        high = np.asarray(self._control_high, dtype=float)
        for _ in range(self._steps(duration_s)):
            q = np.asarray(
                [self.data.qpos[address] for address in self._qpos_addresses],
                dtype=float,
            )
            qd = np.asarray(
                [self.data.qvel[address] for address in self._qvel_addresses],
                dtype=float,
            )
            command = np.clip(kp * (target - q) - kd * qd, low, high)
            for actuator_id, value in zip(
                self._actuators, command, strict=True
            ):
                self.data.ctrl[actuator_id] = float(value)
            mujoco.mj_step(self.model, self.data)

    def track_planar_twist(self, request: Any) -> None:
        """Track one bounded body-frame planar twist for its full duration."""

        values = _request(
            request,
            {"linear_velocity_body_m_s", "yaw_rate_rad_s", "duration_s"},
        )
        linear = _vector(
            values["linear_velocity_body_m_s"],
            2,
            "linear_velocity_body_m_s",
        )
        if float(np.max(np.abs(linear))) > 0.4:
            raise ValueError("linear velocity exceeds the public bound")
        yaw_rate = _number(
            values["yaw_rate_rad_s"],
            "yaw_rate_rad_s",
            lower=-1.0,
            upper=1.0,
        )
        duration = _number(
            values["duration_s"],
            "duration_s",
            lower=2.0,
            upper=4.0,
        )
        self._start_policy()
        self._policy_command(1.05 * linear, yaw_rate, duration)

    def move_body_relative_pose(self, request: Any) -> None:
        """Reach a planar pose expressed in the call-time body-yaw frame."""

        values = _request(
            request,
            {"translation_initial_yaw_m", "yaw_delta_rad", "max_duration_s"},
        )
        displacement = _vector(
            values["translation_initial_yaw_m"],
            2,
            "translation_initial_yaw_m",
        )
        if float(np.max(np.abs(displacement))) > 0.3:
            raise ValueError("relative translation exceeds the public bound")
        yaw_delta = _number(
            values["yaw_delta_rad"],
            "yaw_delta_rad",
            lower=-0.6,
            upper=0.6,
        )
        duration = _number(
            values["max_duration_s"],
            "max_duration_s",
            lower=0.25,
            upper=8.0,
        )
        origin, initial_yaw = self._pose()
        target = self._initial_frame_target(
            origin, initial_yaw, displacement
        )
        self._start_policy()
        self._run_pose_path(
            [target],
            target_yaw=initial_yaw + yaw_delta,
            max_duration_s=duration,
        )

    def trace_planar_path(self, request: Any) -> None:
        """Trace planar waypoints expressed in the call-time body-yaw frame."""

        values = _request(
            request, {"waypoints_initial_yaw_m", "max_duration_s"}
        )
        raw_waypoints = values["waypoints_initial_yaw_m"]
        if (
            isinstance(raw_waypoints, (str, bytes))
            or not isinstance(raw_waypoints, Sequence)
            or not 2 <= len(raw_waypoints) <= 8
        ):
            raise ValueError(
                "waypoints_initial_yaw_m must contain two to eight waypoints"
            )
        displacements = [
            _vector(item, 2, "waypoints_initial_yaw_m")
            for item in raw_waypoints
        ]
        if any(float(np.max(np.abs(item))) > 0.4 for item in displacements):
            raise ValueError("planar waypoint exceeds the public bound")
        duration = _number(
            values["max_duration_s"],
            "max_duration_s",
            lower=0.25,
            upper=8.0,
        )
        origin, initial_yaw = self._pose()
        targets = [
            self._initial_frame_target(origin, initial_yaw, item)
            for item in displacements
        ]
        self._start_policy()
        self._run_pose_path(
            targets,
            target_yaw=initial_yaw,
            max_duration_s=duration,
        )

    def set_body_height(self, request: Any) -> None:
        """Set a bounded standing height with a symmetric joint posture."""

        values = _request(request, {"target_height_m", "max_duration_s"})
        target_height = _number(
            values["target_height_m"],
            "target_height_m",
            lower=0.22,
            upper=0.36,
        )
        duration = _number(
            values["max_duration_s"],
            "max_duration_s",
            lower=0.25,
            upper=8.0,
        )
        thigh = 0.9 + (0.274 - target_height) / 0.32
        target = np.asarray([0.0, thigh, -2.0 * thigh] * 4, dtype=float)
        self._hold_posture(target, duration)

    def hold_stable_stance(self, request: Any) -> None:
        """Recover to and retain the nominal four-foot stance."""

        values = _request(request, {"duration_s"})
        duration = _number(
            values["duration_s"],
            "duration_s",
            lower=1.0,
            upper=2.0,
        )
        self._hold_posture(_HOME, duration)


Driver = ReferenceGo2Driver


def build(*, model: Any, data: Any) -> ReferenceGo2Driver:
    """Bind the reference driver to the Framework-owned physical session."""

    return ReferenceGo2Driver(model=model, data=data)
