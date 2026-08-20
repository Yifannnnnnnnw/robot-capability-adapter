"""Task-neutral Unitree G1 joint-position control inventory."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import mujoco
import numpy as np


G1_JOINT_NAMES = (
    "left_hip_pitch_joint",
    "left_hip_roll_joint",
    "left_hip_yaw_joint",
    "left_knee_joint",
    "left_ankle_pitch_joint",
    "left_ankle_roll_joint",
    "right_hip_pitch_joint",
    "right_hip_roll_joint",
    "right_hip_yaw_joint",
    "right_knee_joint",
    "right_ankle_pitch_joint",
    "right_ankle_roll_joint",
    "waist_yaw_joint",
    "waist_roll_joint",
    "waist_pitch_joint",
    "left_shoulder_pitch_joint",
    "left_shoulder_roll_joint",
    "left_shoulder_yaw_joint",
    "left_elbow_joint",
    "left_wrist_roll_joint",
    "left_wrist_pitch_joint",
    "left_wrist_yaw_joint",
    "right_shoulder_pitch_joint",
    "right_shoulder_roll_joint",
    "right_shoulder_yaw_joint",
    "right_elbow_joint",
    "right_wrist_roll_joint",
    "right_wrist_pitch_joint",
    "right_wrist_yaw_joint",
)

G1_ACTUATOR_NAMES = G1_JOINT_NAMES

G1_JOINT_LIMITS_RAD = {
    "left_hip_pitch_joint": (-2.5307, 2.8798),
    "left_hip_roll_joint": (-0.5236, 2.9671),
    "left_hip_yaw_joint": (-2.7576, 2.7576),
    "left_knee_joint": (-0.087267, 2.8798),
    "left_ankle_pitch_joint": (-0.87267, 0.5236),
    "left_ankle_roll_joint": (-0.2618, 0.2618),
    "right_hip_pitch_joint": (-2.5307, 2.8798),
    "right_hip_roll_joint": (-2.9671, 0.5236),
    "right_hip_yaw_joint": (-2.7576, 2.7576),
    "right_knee_joint": (-0.087267, 2.8798),
    "right_ankle_pitch_joint": (-0.87267, 0.5236),
    "right_ankle_roll_joint": (-0.2618, 0.2618),
    "waist_yaw_joint": (-2.618, 2.618),
    "waist_roll_joint": (-0.52, 0.52),
    "waist_pitch_joint": (-0.52, 0.52),
    "left_shoulder_pitch_joint": (-3.0892, 2.6704),
    "left_shoulder_roll_joint": (-1.5882, 2.2515),
    "left_shoulder_yaw_joint": (-2.618, 2.618),
    "left_elbow_joint": (-1.0472, 2.0944),
    "left_wrist_roll_joint": (-1.97222, 1.97222),
    "left_wrist_pitch_joint": (-1.61443, 1.61443),
    "left_wrist_yaw_joint": (-1.61443, 1.61443),
    "right_shoulder_pitch_joint": (-3.0892, 2.6704),
    "right_shoulder_roll_joint": (-2.2515, 1.5882),
    "right_shoulder_yaw_joint": (-2.618, 2.618),
    "right_elbow_joint": (-1.0472, 2.0944),
    "right_wrist_roll_joint": (-1.97222, 1.97222),
    "right_wrist_pitch_joint": (-1.61443, 1.61443),
    "right_wrist_yaw_joint": (-1.61443, 1.61443),
}

G1_SITE_NAMES = (
    "imu_in_pelvis",
    "left_foot",
    "right_foot",
    "imu_in_torso",
)


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _items(value: object, field_name: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field_name} must be a sequence")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field_name} must be a sequence") from exc


@dataclass(frozen=True)
class G1JointPositionSpec:
    """Public G1 names, ranges, and bounded position-control settings."""

    joint_names: tuple[str, ...] = G1_JOINT_NAMES
    actuator_names: tuple[str, ...] = G1_ACTUATOR_NAMES
    joint_limits_rad: Mapping[str, tuple[float, float]] = field(
        default_factory=lambda: dict(G1_JOINT_LIMITS_RAD)
    )
    base_body_name: str = "pelvis"
    site_names: tuple[str, ...] = G1_SITE_NAMES
    max_joint_command_delta_rad: float = 0.08
    max_steps: int = 10000

    def __post_init__(self) -> None:
        joints = tuple(str(value) for value in _items(self.joint_names, "joint_names"))
        actuators = tuple(
            str(value) for value in _items(self.actuator_names, "actuator_names")
        )
        sites = tuple(str(value) for value in _items(self.site_names, "site_names"))
        if len(joints) != len(actuators) or not joints:
            raise ValueError("joint_names and actuator_names must be equally non-empty")
        if len(set(joints)) != len(joints) or len(set(actuators)) != len(actuators):
            raise ValueError("joint and actuator names must be unique")
        if not isinstance(self.joint_limits_rad, Mapping):
            raise ValueError("joint_limits_rad must be a mapping")
        limits: dict[str, tuple[float, float]] = {}
        for name in joints:
            raw_limit = self.joint_limits_rad.get(name)
            if not isinstance(raw_limit, Sequence) or len(raw_limit) != 2:
                raise ValueError(f"joint_limits_rad[{name!r}] must contain two values")
            lower = _finite(raw_limit[0], f"joint_limits_rad[{name!r}]")
            upper = _finite(raw_limit[1], f"joint_limits_rad[{name!r}]")
            if lower >= upper:
                raise ValueError(f"joint_limits_rad[{name!r}] must be increasing")
            limits[name] = (lower, upper)
        delta = _finite(
            self.max_joint_command_delta_rad, "max_joint_command_delta_rad"
        )
        if delta <= 0.0:
            raise ValueError("max_joint_command_delta_rad must be positive")
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int):
            raise ValueError("max_steps must be a positive integer")
        if self.max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        if not isinstance(self.base_body_name, str) or not self.base_body_name.strip():
            raise ValueError("base_body_name must be non-empty")
        object.__setattr__(self, "joint_names", joints)
        object.__setattr__(self, "actuator_names", actuators)
        object.__setattr__(self, "site_names", sites)
        object.__setattr__(self, "joint_limits_rad", limits)
        object.__setattr__(self, "max_joint_command_delta_rad", delta)

    @classmethod
    def default(cls) -> "G1JointPositionSpec":
        return cls()


class G1JointPositionSkeleton:
    """Bounded G1 joint observations and actuator-driven position control."""

    def __init__(
        self,
        *,
        model: Any,
        data: Any,
        spec: G1JointPositionSpec,
    ) -> None:
        if model is None or data is None:
            raise ValueError("canonical model and data are required")
        if not isinstance(spec, G1JointPositionSpec):
            raise TypeError("spec must be a G1JointPositionSpec")
        self.model = model
        self.data = data
        self.spec = spec
        self._resolved = False

    @classmethod
    def from_session(
        cls,
        *,
        model: Any,
        data: Any,
        spec: G1JointPositionSpec,
    ) -> "G1JointPositionSkeleton":
        return cls(model=model, data=data, spec=spec)

    def _resolve(self) -> None:
        if self._resolved:
            return
        base_id = int(
            mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_BODY, self.spec.base_body_name
            )
        )
        if base_id < 0:
            raise ValueError(f"base body {self.spec.base_body_name!r} is absent")

        joint_ids: list[int] = []
        joint_qpos_addresses: list[int] = []
        joint_qvel_addresses: list[int] = []
        joint_low: list[float] = []
        joint_high: list[float] = []
        for name in self.spec.joint_names:
            joint_id = int(
                mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            )
            if joint_id < 0:
                raise ValueError(f"joint {name!r} is absent")
            if int(self.model.jnt_type[joint_id]) != int(mujoco.mjtJoint.mjJNT_HINGE):
                raise ValueError(f"joint {name!r} must be a hinge")
            if not bool(self.model.jnt_limited[joint_id]):
                raise ValueError(f"joint {name!r} must have a finite range")
            model_low, model_high = (
                float(self.model.jnt_range[joint_id, 0]),
                float(self.model.jnt_range[joint_id, 1]),
            )
            expected_low, expected_high = self.spec.joint_limits_rad[name]
            if not math.isclose(model_low, expected_low, abs_tol=1e-6) or not math.isclose(
                model_high, expected_high, abs_tol=1e-6
            ):
                raise ValueError(f"joint {name!r} range differs from the G1 contract")
            joint_ids.append(joint_id)
            joint_qpos_addresses.append(int(self.model.jnt_qposadr[joint_id]))
            joint_qvel_addresses.append(int(self.model.jnt_dofadr[joint_id]))
            joint_low.append(model_low)
            joint_high.append(model_high)

        actuator_ids: list[int] = []
        ctrl_low: list[float] = []
        ctrl_high: list[float] = []
        for joint_id, name in zip(joint_ids, self.spec.actuator_names, strict=True):
            actuator_id = int(
                mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            )
            if actuator_id < 0:
                raise ValueError(f"actuator {name!r} is absent")
            if int(self.model.actuator_trntype[actuator_id]) != int(
                mujoco.mjtTrn.mjTRN_JOINT
            ):
                raise ValueError(f"actuator {name!r} must use joint transmission")
            if int(self.model.actuator_trnid[actuator_id, 0]) != joint_id:
                raise ValueError(f"actuator {name!r} is not mapped to its joint")
            if not bool(self.model.actuator_ctrllimited[actuator_id]):
                raise ValueError(f"actuator {name!r} must have a finite ctrlrange")
            ctrl_low.append(float(self.model.actuator_ctrlrange[actuator_id, 0]))
            ctrl_high.append(float(self.model.actuator_ctrlrange[actuator_id, 1]))
            actuator_ids.append(actuator_id)

        site_ids: dict[str, int] = {}
        for name in self.spec.site_names:
            site_id = int(
                mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, name)
            )
            if site_id < 0:
                raise ValueError(f"site {name!r} is absent")
            site_ids[name] = site_id

        self._base_id = base_id
        self._joint_ids = tuple(joint_ids)
        self._joint_qpos_addresses = tuple(joint_qpos_addresses)
        self._joint_qvel_addresses = tuple(joint_qvel_addresses)
        self._actuator_ids = tuple(actuator_ids)
        self._site_ids = site_ids
        self._joint_low = np.asarray(joint_low, dtype=float)
        self._joint_high = np.asarray(joint_high, dtype=float)
        self._ctrl_low = np.asarray(ctrl_low, dtype=float)
        self._ctrl_high = np.asarray(ctrl_high, dtype=float)
        self._resolved = True

    def _vector(self, value: object, field_name: str) -> np.ndarray:
        self._resolve()
        try:
            result = np.asarray(value, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must contain finite values") from exc
        if result.shape != (len(self._joint_ids),) or not np.isfinite(result).all():
            raise ValueError(
                f"{field_name} must contain exactly {len(self._joint_ids)} finite values"
            )
        return result

    def _read_joint_positions(self) -> np.ndarray:
        return np.asarray(
            [self.data.qpos[address] for address in self._joint_qpos_addresses],
            dtype=float,
        )

    def get_joint_positions(self) -> np.ndarray:
        """Read configured hinge positions in the public G1 order."""

        self._resolve()
        return self._read_joint_positions().copy()

    def get_joint_velocities(self) -> np.ndarray:
        """Read configured hinge velocities in the public G1 order."""

        self._resolve()
        return np.asarray(
            [self.data.qvel[address] for address in self._joint_qvel_addresses],
            dtype=float,
        )

    def observe(self) -> dict[str, Any]:
        """Return bounded public state from the canonical MuJoCo session."""

        self._resolve()
        mujoco.mj_forward(self.model, self.data)
        return {
            "joint_positions": self.get_joint_positions().tolist(),
            "joint_velocities": self.get_joint_velocities().tolist(),
            "base_position": np.asarray(self.data.xpos[self._base_id], dtype=float).tolist(),
            "base_quaternion": np.asarray(self.data.xquat[self._base_id], dtype=float).tolist(),
            "base_velocity": np.asarray(self.data.qvel[:6], dtype=float).tolist(),
            "site_positions": {
                name: np.asarray(self.data.site_xpos[site_id], dtype=float).tolist()
                for name, site_id in self._site_ids.items()
            },
            "actuator_controls": np.asarray(
                [self.data.ctrl[actuator_id] for actuator_id in self._actuator_ids],
                dtype=float,
            ).tolist(),
            "simulation_time": float(self.data.time),
        }

    def command_joint_positions(
        self,
        target_positions: Sequence[float],
        *,
        steps: int = 1,
    ) -> dict[str, Any]:
        """Track a bounded joint-position request through real actuator steps."""

        self._resolve()
        if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= self.spec.max_steps:
            raise ValueError(f"steps must be an integer in [1, {self.spec.max_steps}]")
        target = np.clip(self._vector(target_positions, "target_positions"), self._joint_low, self._joint_high)
        target = np.clip(target, self._ctrl_low, self._ctrl_high)
        for _ in range(steps):
            current = self._read_joint_positions()
            command = current + np.clip(
                target - current,
                -self.spec.max_joint_command_delta_rad,
                self.spec.max_joint_command_delta_rad,
            )
            command = np.clip(command, self._ctrl_low, self._ctrl_high)
            for actuator_id, value in zip(self._actuator_ids, command, strict=True):
                self.data.ctrl[actuator_id] = float(value)
            mujoco.mj_step(self.model, self.data)
        return self.observe()

    def hold(self, *, steps: int = 1) -> dict[str, Any]:
        """Hold the freshly observed pose while advancing real physics."""

        return self.command_joint_positions(self.get_joint_positions(), steps=steps)

    def describe(self) -> dict[str, Any]:
        """Return the public control inventory without task-specific semantics."""

        self._resolve()
        return {
            "joint_names": list(self.spec.joint_names),
            "actuator_names": list(self.spec.actuator_names),
            "joint_limits_rad": {
                name: list(limit) for name, limit in self.spec.joint_limits_rad.items()
            },
            "base_body_name": self.spec.base_body_name,
            "site_names": list(self.spec.site_names),
        }


__all__ = [
    "G1_ACTUATOR_NAMES",
    "G1_JOINT_LIMITS_RAD",
    "G1_JOINT_NAMES",
    "G1_SITE_NAMES",
    "G1JointPositionSkeleton",
    "G1JointPositionSpec",
]
