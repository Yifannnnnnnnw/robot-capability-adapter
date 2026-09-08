"""Capability-neutral torque primitives for a four-legged robot.

The Framework supplies the canonical MuJoCo ``model`` and ``data`` objects.
This module contains only reusable observations, joint-space PD, and a small
periodic gait.  It does not choose TGCD capabilities, load a scene, reset a
trial, or decide whether a physical objective passed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from autoadapter2.driver_synthesis import SessionBoundSkeleton


_JOINTS_PER_LEG = 3
_EXPECTED_LEGS = 4
_J_HIP = 0
_J_THIGH = 1
_J_CALF = 2


def _finite(value: float, field_name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _name(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class QuadrupedSpec:
    """Public morphology and conservative control parameters for one quadruped.

    ``leg_joint_names`` and ``leg_actuator_names`` have the same ordered leg
    keys.  Each leg is ordered ``hip, thigh, calf`` and ``home_qpos`` follows
    the flattened leg order.  This is robot configuration, not a task or
    capability catalog.
    """

    base_body_name: str
    leg_joint_names: Mapping[str, Sequence[str]]
    leg_actuator_names: Mapping[str, Sequence[str]]
    home_qpos: Sequence[float]
    actuation: str = "joint_torque"
    kp: float = 80.0
    kd: float = 4.0
    gait_freq_hz: float = 1.5
    swing_amp_hip: float = 0.035
    swing_amp_thigh: float = 0.10
    swing_amp_calf: float = 0.16
    thigh_forward_sign: float = -1.0
    gait_phases: Mapping[str, float] | None = None
    body_height_target: float = 0.27
    vx_limit: float = 0.40
    vy_limit: float = 0.20
    yaw_rate_limit: float = 1.0
    sit_thigh_offset: float = 0.45
    sit_calf_offset: float = -0.35

    def __post_init__(self) -> None:
        if self.actuation not in {"joint_torque", "joint_position"}:
            raise ValueError("actuation must be joint_torque or joint_position")
        object.__setattr__(self, "base_body_name", _name(self.base_body_name, "base_body_name"))

        if not isinstance(self.leg_joint_names, Mapping):
            raise ValueError("leg_joint_names must be a mapping")
        if not isinstance(self.leg_actuator_names, Mapping):
            raise ValueError("leg_actuator_names must be a mapping")

        joint_items = tuple(
            (_name(leg, "leg name"), values)
            for leg, values in self.leg_joint_names.items()
        )
        actuator_items = tuple(
            (_name(leg, "leg name"), values)
            for leg, values in self.leg_actuator_names.items()
        )
        if len({leg for leg, _ in joint_items}) != len(joint_items):
            raise ValueError("leg names must be unique")
        if len({leg for leg, _ in actuator_items}) != len(actuator_items):
            raise ValueError("actuator leg names must be unique")
        normalized_joint_map = dict(joint_items)
        normalized_actuator_map = dict(actuator_items)
        leg_order = tuple(normalized_joint_map)
        if len(leg_order) != _EXPECTED_LEGS:
            raise ValueError(f"exactly {_EXPECTED_LEGS} legs are required")
        if len(set(leg_order)) != len(leg_order):
            raise ValueError("leg names must be unique")
        actuator_keys = tuple(normalized_actuator_map)
        if set(leg_order) != set(actuator_keys):
            raise ValueError("joint and actuator leg keys must match")

        joints: dict[str, tuple[str, ...]] = {}
        actuators: dict[str, tuple[str, ...]] = {}
        flat_joints: list[str] = []
        flat_actuators: list[str] = []
        for leg in leg_order:
            leg_joints = tuple(
                _name(item, f"leg_joint_names[{leg!r}]")
                for item in normalized_joint_map[leg]
            )
            leg_actuators = tuple(
                _name(item, f"leg_actuator_names[{leg!r}]")
                for item in normalized_actuator_map[leg]
            )
            if len(leg_joints) != _JOINTS_PER_LEG:
                raise ValueError(f"leg {leg!r} must have {_JOINTS_PER_LEG} joints")
            if len(leg_actuators) != _JOINTS_PER_LEG:
                raise ValueError(f"leg {leg!r} must have {_JOINTS_PER_LEG} actuators")
            joints[leg] = leg_joints
            actuators[leg] = leg_actuators
            flat_joints.extend(leg_joints)
            flat_actuators.extend(leg_actuators)

        if len(set(flat_joints)) != len(flat_joints):
            raise ValueError("joint names must be unique across legs")
        if len(set(flat_actuators)) != len(flat_actuators):
            raise ValueError("actuator names must be unique across legs")

        home = tuple(_finite(value, "home_qpos") for value in self.home_qpos)
        if len(home) != _EXPECTED_LEGS * _JOINTS_PER_LEG:
            raise ValueError("home_qpos must contain 12 values")

        kp = _finite(self.kp, "kp")
        kd = _finite(self.kd, "kd")
        if kp <= 0.0 or kd <= 0.0:
            raise ValueError("kp and kd must be positive")

        gait_freq = _finite(self.gait_freq_hz, "gait_freq_hz")
        if gait_freq <= 0.0:
            raise ValueError("gait_freq_hz must be positive")
        for field_name, value, upper in (
            ("swing_amp_hip", self.swing_amp_hip, 0.5),
            ("swing_amp_thigh", self.swing_amp_thigh, 0.8),
            ("swing_amp_calf", self.swing_amp_calf, 1.0),
        ):
            amount = _finite(value, field_name)
            if amount < 0.0 or amount > upper:
                raise ValueError(f"{field_name} must be in [0, {upper}]")
            object.__setattr__(self, field_name, amount)

        sign = _finite(self.thigh_forward_sign, "thigh_forward_sign")
        if sign not in (-1.0, 1.0):
            raise ValueError("thigh_forward_sign must be -1 or 1")
        height = _finite(self.body_height_target, "body_height_target")
        if height <= 0.0:
            raise ValueError("body_height_target must be positive")

        limits = (
            ("vx_limit", self.vx_limit),
            ("vy_limit", self.vy_limit),
            ("yaw_rate_limit", self.yaw_rate_limit),
        )
        for field_name, value in limits:
            limit = _finite(value, field_name)
            if limit <= 0.0:
                raise ValueError(f"{field_name} must be positive")
            object.__setattr__(self, field_name, limit)

        thigh_offset = _finite(self.sit_thigh_offset, "sit_thigh_offset")
        calf_offset = _finite(self.sit_calf_offset, "sit_calf_offset")
        if thigh_offset < 0.0 or calf_offset > 0.0:
            raise ValueError("sit offsets must fold the thigh forward and calf inward")

        phases: dict[str, float] | None = None
        if self.gait_phases is not None:
            if not isinstance(self.gait_phases, Mapping):
                raise ValueError("gait_phases must be a mapping")
            if set(self.gait_phases) != set(leg_order):
                raise ValueError("gait_phases must contain exactly the four leg names")
            phases = {
                leg: _finite(self.gait_phases[leg], f"gait_phases[{leg!r}]")
                for leg in leg_order
            }

        object.__setattr__(self, "leg_joint_names", joints)
        object.__setattr__(self, "leg_actuator_names", actuators)
        object.__setattr__(self, "home_qpos", home)
        object.__setattr__(self, "kp", kp)
        object.__setattr__(self, "kd", kd)
        object.__setattr__(self, "gait_freq_hz", gait_freq)
        object.__setattr__(self, "thigh_forward_sign", sign)
        object.__setattr__(self, "gait_phases", phases)
        object.__setattr__(self, "body_height_target", height)
        object.__setattr__(self, "sit_thigh_offset", thigh_offset)
        object.__setattr__(self, "sit_calf_offset", calf_offset)


class QuadrupedPDGaitSkeleton(SessionBoundSkeleton):
    """Trusted four-legged PD and periodic-gait primitives.

    Construction is session-bound: ``model`` and ``data`` must be supplied by
    the Framework.  Evaluated motion writes only torque commands to
    ``data.ctrl`` and advances the same session with ``mj_step``.
    """

    def __init__(self, *, model: Any, data: Any, spec: QuadrupedSpec) -> None:
        if not isinstance(spec, QuadrupedSpec):
            raise TypeError("spec must be a QuadrupedSpec")
        super().__init__(model=model, data=data, spec=spec)
        self._mj: Any = None
        self._np: Any = None
        self._resolved = False
        self._base_body_id = -1
        self._leg_order: tuple[str, ...] = ()
        self._joint_qpos_adr: list[int] = []
        self._joint_qvel_adr: list[int] = []
        self._actuator_ids: list[int] = []
        self._q_lo: Any = None
        self._q_hi: Any = None
        self._ctrl_lo: Any = None
        self._ctrl_hi: Any = None
        self._home_q: Any = None
        self._timestep = 0.0
        self._gait_time_s = 0.0

    def _load_numpy(self) -> Any:
        if self._np is None:
            try:
                import numpy as np
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("NumPy is required for quadruped operations") from exc
            self._np = np
        return self._np

    def _load_mujoco(self) -> Any:
        if self._mj is None:
            try:
                import mujoco
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("MuJoCo is required for quadruped operations") from exc
            self._mj = mujoco
        return self._mj

    def _resolve_indices(self) -> None:
        if self._resolved:
            return

        mj = self._load_mujoco()
        np = self._load_numpy()
        model = self.model
        self._base_body_id = int(
            mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, self.spec.base_body_name)
        )
        if self._base_body_id < 0:
            raise ValueError(f"base body {self.spec.base_body_name!r} is absent from the model")

        self._leg_order = tuple(self.spec.leg_joint_names)
        self._joint_qpos_adr = []
        self._joint_qvel_adr = []
        for leg in self._leg_order:
            for joint_name in self.spec.leg_joint_names[leg]:
                joint_id = int(mj.mj_name2id(model, mj.mjtObj.mjOBJ_JOINT, joint_name))
                if joint_id < 0:
                    raise ValueError(f"joint {joint_name!r} is absent from the model")
                if int(model.jnt_type[joint_id]) != int(mj.mjtJoint.mjJNT_HINGE):
                    raise ValueError(f"joint {joint_name!r} must be a hinge joint")
                self._joint_qpos_adr.append(int(model.jnt_qposadr[joint_id]))
                self._joint_qvel_adr.append(int(model.jnt_dofadr[joint_id]))

        self._actuator_ids = []
        for leg in self._leg_order:
            for actuator_name in self.spec.leg_actuator_names[leg]:
                actuator_id = int(
                    mj.mj_name2id(model, mj.mjtObj.mjOBJ_ACTUATOR, actuator_name)
                )
                if actuator_id < 0:
                    raise ValueError(f"actuator {actuator_name!r} is absent from the model")
                self._actuator_ids.append(actuator_id)

        if len(set(self._actuator_ids)) != len(self._actuator_ids):
            raise ValueError("actuator mapping must not contain duplicates")

        self._home_q = np.asarray(self.spec.home_qpos, dtype=float)
        self._q_lo = np.full(len(self._joint_qpos_adr), -np.inf, dtype=float)
        self._q_hi = np.full(len(self._joint_qpos_adr), np.inf, dtype=float)
        flat_joint_names = tuple(
            name for leg in self._leg_order for name in self.spec.leg_joint_names[leg]
        )
        for index, joint_name in enumerate(flat_joint_names):
            joint_id = int(mj.mj_name2id(model, mj.mjtObj.mjOBJ_JOINT, joint_name))
            if bool(model.jnt_limited[joint_id]):
                self._q_lo[index] = float(model.jnt_range[joint_id, 0])
                self._q_hi[index] = float(model.jnt_range[joint_id, 1])
        if np.any(self._home_q < self._q_lo) or np.any(self._home_q > self._q_hi):
            raise ValueError("home_qpos is outside one or more model joint limits")

        self._ctrl_lo = np.full(len(self._actuator_ids), -np.inf, dtype=float)
        self._ctrl_hi = np.full(len(self._actuator_ids), np.inf, dtype=float)
        for index, actuator_id in enumerate(self._actuator_ids):
            if bool(model.actuator_ctrllimited[actuator_id]):
                self._ctrl_lo[index] = float(model.actuator_ctrlrange[actuator_id, 0])
                self._ctrl_hi[index] = float(model.actuator_ctrlrange[actuator_id, 1])

        self._timestep = float(model.opt.timestep)
        if not math.isfinite(self._timestep) or self._timestep <= 0.0:
            raise ValueError("model.opt.timestep must be positive")
        self._resolved = True

    def _vector(self, value: Sequence[float], field_name: str) -> Any:
        self._resolve_indices()
        np = self._load_numpy()
        result = np.asarray(value, dtype=float)
        if result.shape != (len(self._joint_qpos_adr),):
            raise ValueError(f"{field_name} must contain exactly 12 values")
        if not np.all(np.isfinite(result)):
            raise ValueError(f"{field_name} must contain only finite values")
        return result

    def _duration_steps(self, duration: float) -> int:
        value = _finite(duration, "duration")
        if value < 0.0:
            raise ValueError("duration must be non-negative")
        return max(1, int(math.ceil(value / self._timestep)))

    def _bounded_command(self, value: float, limit: float, field_name: str) -> float:
        result = _finite(value, field_name)
        if abs(result) > limit:
            raise ValueError(f"{field_name} exceeds configured limit {limit}")
        return result

    def _phase(self, leg_index: int, leg: str) -> float:
        if self.spec.gait_phases is not None:
            return float(self.spec.gait_phases[leg])
        return 0.0 if leg_index in (0, 3) else math.pi

    def get_joint_positions(self) -> Any:
        """Read hinge positions in configured leg and joint order."""

        self._resolve_indices()
        np = self._load_numpy()
        return np.asarray(
            [self.data.qpos[address] for address in self._joint_qpos_adr], dtype=float
        )

    def get_joint_velocities(self) -> Any:
        """Read hinge velocities in configured leg and joint order."""

        self._resolve_indices()
        np = self._load_numpy()
        return np.asarray(
            [self.data.qvel[address] for address in self._joint_qvel_adr], dtype=float
        )

    def get_base_pose(self) -> tuple[Any, Any]:
        """Return the base body's world position and 3x3 rotation matrix."""

        self._resolve_indices()
        self._load_mujoco().mj_forward(self.model, self.data)
        np = self._load_numpy()
        position = np.array(self.data.xpos[self._base_body_id], dtype=float, copy=True)
        rotation = np.array(self.data.xmat[self._base_body_id], dtype=float, copy=True).reshape(3, 3)
        return position, rotation

    def get_base_velocity(self) -> dict[str, Any]:
        """Return trusted world-frame base linear and angular velocity."""

        self._resolve_indices()
        mj = self._load_mujoco()
        np = self._load_numpy()
        spatial = np.zeros(6, dtype=float)
        mj.mj_objectVelocity(
            self.model,
            self.data,
            mj.mjtObj.mjOBJ_BODY,
            self._base_body_id,
            spatial,
            0,
        )
        return {
            "angular": np.array(spatial[:3], dtype=float, copy=True),
            "linear": np.array(spatial[3:], dtype=float, copy=True),
        }

    def get_body_height(self) -> float:
        """Read the current world z position of the base body."""

        self._resolve_indices()
        return float(self.data.xpos[self._base_body_id, 2])

    def set_joint_torques(self, torques: Sequence[float]) -> None:
        """Write clipped torque commands to the canonical ``data.ctrl`` only."""

        self._resolve_indices()
        np = self._load_numpy()
        command = self._vector(torques, "torques")
        command = np.clip(command, self._ctrl_lo, self._ctrl_hi)
        for index, actuator_id in enumerate(self._actuator_ids):
            self.data.ctrl[actuator_id] = float(command[index])

    def apply_pd_posture(
        self,
        q_target: Sequence[float],
        qd_target: Sequence[float] | None = None,
    ) -> None:
        """Compute joint-space PD torque and write it to ``data.ctrl``."""

        self._resolve_indices()
        np = self._load_numpy()
        target = np.clip(self._vector(q_target, "q_target"), self._q_lo, self._q_hi)
        velocity_target = (
            np.zeros(len(self._joint_qvel_adr), dtype=float)
            if qd_target is None
            else self._vector(qd_target, "qd_target")
        )
        if self.spec.actuation == "joint_position":
            # Native position actuators already implement the joint servo.
            for index, actuator_id in enumerate(self._actuator_ids):
                self.data.ctrl[actuator_id] = float(target[index])
            return
        torque = self.spec.kp * (target - self.get_joint_positions()) + self.spec.kd * (
            velocity_target - self.get_joint_velocities()
        )
        self.set_joint_torques(torque)

    def step(self, count: int = 1) -> None:
        """Advance the canonical MuJoCo physics session by ``count`` steps."""

        self._resolve_indices()
        if isinstance(count, bool) or int(count) != count or count < 0:
            raise ValueError("count must be a non-negative integer")
        mj = self._load_mujoco()
        for _ in range(int(count)):
            mj.mj_step(self.model, self.data)

    def move_to_posture(self, q_target: Sequence[float], duration: float = 2.0) -> None:
        """Interpolate to a posture through PD torque and real physics steps."""

        self._resolve_indices()
        np = self._load_numpy()
        target = np.clip(self._vector(q_target, "q_target"), self._q_lo, self._q_hi)
        start = self.get_joint_positions()
        steps = self._duration_steps(duration)
        for index in range(steps):
            fraction = (index + 1) / steps
            self.apply_pd_posture((1.0 - fraction) * start + fraction * target)
            self.step(1)

    def stand_up(self, duration: float = 2.0) -> None:
        """Track the configured home posture through physical actuators."""

        self._resolve_indices()
        self.move_to_posture(self._home_q, duration=duration)

    def sit(self, duration: float = 2.0) -> None:
        """Track a conservative folded posture through physical actuators."""

        self._resolve_indices()
        np = self._load_numpy()
        target = self._home_q.copy()
        for leg_index in range(len(self._leg_order)):
            base = leg_index * _JOINTS_PER_LEG
            target[base + _J_THIGH] += self.spec.sit_thigh_offset
            target[base + _J_CALF] += self.spec.sit_calf_offset
        self.move_to_posture(np.clip(target, self._q_lo, self._q_hi), duration=duration)

    def stop(self, duration: float = 0.20) -> None:
        """Stop commanded gait and hold the currently observed joint posture."""

        self.move_to_posture(self.get_joint_positions(), duration=duration)

    def _gait_posture(
        self,
        phase_time: float,
        vx: float,
        vy: float,
        yaw_rate: float,
    ) -> Any:
        np = self._load_numpy()
        vx_norm = vx / self.spec.vx_limit
        vy_norm = vy / self.spec.vy_limit
        yaw_norm = yaw_rate / self.spec.yaw_rate_limit
        q_target = self._home_q.copy()
        omega = 2.0 * math.pi * self.spec.gait_freq_hz
        for leg_index, leg in enumerate(self._leg_order):
            phase = omega * phase_time + self._phase(leg_index, leg)
            wave = math.sin(phase)
            swing = max(0.0, wave)
            stance = min(0.0, wave)
            base = leg_index * _JOINTS_PER_LEG
            front_sign = 1.0 if leg_index < 2 else -1.0
            lateral_command = vy_norm + front_sign * yaw_norm
            q_target[base + _J_HIP] += self.spec.swing_amp_hip * lateral_command * (
                swing - 0.25 * (-stance)
            )
            q_target[base + _J_THIGH] += self.spec.swing_amp_thigh * (
                self.spec.thigh_forward_sign * vx_norm * (swing - 0.5 * (-stance))
            )
            q_target[base + _J_CALF] -= self.spec.swing_amp_calf * abs(vx_norm) * swing
        return np.clip(q_target, self._q_lo, self._q_hi)

    def command_planar_velocity(
        self,
        vx: float,
        vy: float = 0.0,
        yaw_rate: float = 0.0,
        duration: float = 1.0,
    ) -> None:
        """Track a bounded planar/yaw command with a diagonal periodic gait."""

        self._resolve_indices()
        vx_value = self._bounded_command(vx, self.spec.vx_limit, "vx")
        vy_value = self._bounded_command(vy, self.spec.vy_limit, "vy")
        yaw_value = self._bounded_command(yaw_rate, self.spec.yaw_rate_limit, "yaw_rate")
        steps = self._duration_steps(duration)
        gait_period_s = 1.0 / self.spec.gait_freq_hz
        for _ in range(steps):
            q_target = self._gait_posture(
                self._gait_time_s,
                vx_value,
                vy_value,
                yaw_value,
            )
            self.apply_pd_posture(q_target)
            self.step(1)
            self._gait_time_s = (
                self._gait_time_s + self._timestep
            ) % gait_period_s

    def walk_forward(self, speed: float = 0.30, duration: float = 1.0) -> None:
        """Convenience primitive for a forward-only planar command."""

        self.command_planar_velocity(speed, duration=duration)

    def describe(self) -> dict[str, Any]:
        """Return public morphology and current observation metadata."""

        self._resolve_indices()
        return {
            "dof": len(self._joint_qpos_adr),
            "base_body": self.spec.base_body_name,
            "leg_order": list(self._leg_order),
            "joints_per_leg": _JOINTS_PER_LEG,
            "home_qpos": self._home_q.tolist(),
            "pd": {"kp": self.spec.kp, "kd": self.spec.kd},
            "gait": {
                "frequency_hz": self.spec.gait_freq_hz,
                "swing_amp_hip": self.spec.swing_amp_hip,
                "swing_amp_thigh": self.spec.swing_amp_thigh,
                "swing_amp_calf": self.spec.swing_amp_calf,
            },
        }
