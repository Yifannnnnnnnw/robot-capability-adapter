"""Task-neutral position-policy primitives for the Barkour vB quadruped.

The retained actor supplies flat-ground joint targets from fresh canonical
MuJoCo observations.  This module does not choose capability names, dispatch
tasks, load scenes, reset trials, construct obstacle routes, or decide success.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from autoadapter2.driver_synthesis import SessionBoundSkeleton


POLICY_ARTIFACT_ID = "barkour-joystick-v005-step-000100270080"
POLICY_SOURCE_REVISION = "81dfe512c9f2f03107fda1e31de585d04bb30bc4"
BARKOUR_POLICY_JOINT_LOW = (-0.7, -1.0, 0.05) * 4
BARKOUR_POLICY_JOINT_HIGH = (0.52, 2.1, 2.1) * 4

_POLICY_RESOURCE = Path(__file__).with_name("data") / "barkour_joystick_actor.npz"
_COMMAND_SCALE = (2.0, 2.0, 0.25)
_OBSERVATION_FRAME_SIZE = 31
_OBSERVATION_HISTORY_FRAMES = 15
_OBSERVATION_SIZE = _OBSERVATION_FRAME_SIZE * _OBSERVATION_HISTORY_FRAMES


def _name(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _finite(value: float, field_name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


@dataclass(frozen=True)
class QuadrupedPositionPolicySpec:
    """Public names and bounds for the exact position-controlled quadruped."""

    base_body_name: str
    imu_site_name: str
    gyro_sensor_name: str
    home_keyframe_name: str
    joint_names: Sequence[str]
    actuator_names: Sequence[str]
    policy_joint_low: Sequence[float]
    policy_joint_high: Sequence[float]
    action_scale: float = 0.3
    policy_period_s: float = 0.02
    expected_physics_timestep_s: float = 0.001
    minimum_base_height_m: float = 0.18
    maximum_command_duration_s: float = 20.0

    def __post_init__(self) -> None:
        for field_name in (
            "base_body_name",
            "imu_site_name",
            "gyro_sensor_name",
            "home_keyframe_name",
        ):
            object.__setattr__(self, field_name, _name(getattr(self, field_name), field_name))

        joints = tuple(_name(value, "joint_names") for value in self.joint_names)
        actuators = tuple(_name(value, "actuator_names") for value in self.actuator_names)
        if len(joints) != 12 or len(actuators) != 12:
            raise ValueError("joint_names and actuator_names must each contain 12 values")
        if len(set(joints)) != 12 or len(set(actuators)) != 12:
            raise ValueError("joint and actuator names must be unique")

        lower = tuple(_finite(value, "policy_joint_low") for value in self.policy_joint_low)
        upper = tuple(_finite(value, "policy_joint_high") for value in self.policy_joint_high)
        if len(lower) != 12 or len(upper) != 12:
            raise ValueError("policy joint bounds must each contain 12 values")
        if any(low >= high for low, high in zip(lower, upper, strict=True)):
            raise ValueError("each policy joint lower bound must be below its upper bound")

        for field_name in (
            "action_scale",
            "policy_period_s",
            "expected_physics_timestep_s",
            "minimum_base_height_m",
            "maximum_command_duration_s",
        ):
            value = _finite(getattr(self, field_name), field_name)
            if value <= 0.0:
                raise ValueError(f"{field_name} must be positive")
            object.__setattr__(self, field_name, value)

        object.__setattr__(self, "joint_names", joints)
        object.__setattr__(self, "actuator_names", actuators)
        object.__setattr__(self, "policy_joint_low", lower)
        object.__setattr__(self, "policy_joint_high", upper)


class BarkourPositionPolicySkeleton(SessionBoundSkeleton):
    """Retained flat position policy bound to one Framework-owned session."""

    def __init__(
        self,
        *,
        model: Any,
        data: Any,
        spec: QuadrupedPositionPolicySpec,
    ) -> None:
        if not isinstance(spec, QuadrupedPositionPolicySpec):
            raise TypeError("spec must be a QuadrupedPositionPolicySpec")
        super().__init__(model=model, data=data, spec=spec)
        self._mj: Any = None
        self._np: Any = None
        self._resolved = False
        self._history: Any = None
        self._current_command: Any = None
        self._previous_action: Any = None
        self._layers: tuple[tuple[Any, Any], ...] = ()

    def _load_numpy(self) -> Any:
        if self._np is None:
            try:
                import numpy as np
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("NumPy is required for the position policy") from exc
            self._np = np
        return self._np

    def _load_mujoco(self) -> Any:
        if self._mj is None:
            try:
                import mujoco
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("MuJoCo is required for the position policy") from exc
            self._mj = mujoco
        return self._mj

    def _resolve(self) -> None:
        if self._resolved:
            return
        mj = self._load_mujoco()
        np = self._load_numpy()

        timestep = float(self.model.opt.timestep)
        if not math.isclose(
            timestep,
            self.spec.expected_physics_timestep_s,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "canonical physics timestep does not match the position-policy spec"
            )
        ratio = self.spec.policy_period_s / timestep
        self._steps_per_action = int(round(ratio))
        if self._steps_per_action < 1 or not math.isclose(
            ratio, self._steps_per_action, rel_tol=0.0, abs_tol=1e-9
        ):
            raise ValueError("policy period must be an integer number of physics steps")

        self._base_body_id = int(
            mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_BODY, self.spec.base_body_name)
        )
        self._imu_site_id = int(
            mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_SITE, self.spec.imu_site_name)
        )
        self._gyro_sensor_id = int(
            mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_SENSOR, self.spec.gyro_sensor_name)
        )
        self._home_key_id = int(
            mj.mj_name2id(
                self.model, mj.mjtObj.mjOBJ_KEY, self.spec.home_keyframe_name
            )
        )
        if min(
            self._base_body_id,
            self._imu_site_id,
            self._gyro_sensor_id,
            self._home_key_id,
        ) < 0:
            raise ValueError("position-policy body, IMU, gyro, or home key is missing")
        if int(self.model.sensor_dim[self._gyro_sensor_id]) != 3:
            raise ValueError("position-policy gyro sensor must contain three values")

        self._actuator_ids: list[int] = []
        self._joint_qpos_addresses: list[int] = []
        for joint_name, actuator_name in zip(
            self.spec.joint_names, self.spec.actuator_names, strict=True
        ):
            joint_id = int(
                mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_JOINT, joint_name)
            )
            actuator_id = int(
                mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_ACTUATOR, actuator_name)
            )
            if joint_id < 0 or actuator_id < 0:
                raise ValueError(
                    f"position-policy joint/actuator pair is missing: {joint_name!r}, "
                    f"{actuator_name!r}"
                )
            self._joint_qpos_addresses.append(int(self.model.jnt_qposadr[joint_id]))
            self._actuator_ids.append(actuator_id)
        if len(set(self._actuator_ids)) != 12:
            raise ValueError("position-policy actuator mapping contains duplicates")

        self._joint_low = np.asarray(self.spec.policy_joint_low, dtype=np.float64)
        self._joint_high = np.asarray(self.spec.policy_joint_high, dtype=np.float64)
        self._home_joint_position = np.asarray(
            self.model.key_qpos[self._home_key_id, self._joint_qpos_addresses],
            dtype=np.float64,
        ).copy()
        if np.any(self._home_joint_position < self._joint_low) or np.any(
            self._home_joint_position > self._joint_high
        ):
            raise ValueError("home keyframe is outside the retained policy joint bounds")

        self._load_actor_resource()
        self._previous_action = np.zeros(12, dtype=np.float64)
        self._resolved = True

    def _load_actor_resource(self) -> None:
        np = self._load_numpy()
        if not _POLICY_RESOURCE.is_file():
            raise RuntimeError(f"trusted position-policy artifact is missing: {POLICY_ARTIFACT_ID}")
        with np.load(_POLICY_RESOURCE, allow_pickle=False) as retained:
            required = {"normalizer_mean", "normalizer_std"}
            required.update(
                f"actor_{index}_{suffix}"
                for index in range(5)
                for suffix in ("kernel", "bias")
            )
            if not required.issubset(retained.files):
                raise RuntimeError("trusted position-policy artifact is incomplete")
            self._normalizer_mean = retained["normalizer_mean"].astype(np.float32)
            self._normalizer_std = retained["normalizer_std"].astype(np.float32)
            self._layers = tuple(
                (
                    retained[f"actor_{index}_kernel"].astype(np.float32),
                    retained[f"actor_{index}_bias"].astype(np.float32),
                )
                for index in range(5)
            )

        expected_shapes = (
            ((_OBSERVATION_SIZE, 128), (128,)),
            ((128, 128), (128,)),
            ((128, 128), (128,)),
            ((128, 128), (128,)),
            ((128, 24), (24,)),
        )
        if self._normalizer_mean.shape != (_OBSERVATION_SIZE,):
            raise RuntimeError("trusted position-policy normalizer mean has the wrong shape")
        if self._normalizer_std.shape != (_OBSERVATION_SIZE,):
            raise RuntimeError("trusted position-policy normalizer std has the wrong shape")
        if not np.all(np.isfinite(self._normalizer_mean)):
            raise RuntimeError("trusted position-policy normalizer mean is not finite")
        if not np.all(np.isfinite(self._normalizer_std)) or np.any(
            self._normalizer_std <= 0.0
        ):
            raise RuntimeError("trusted position-policy normalizer std is invalid")
        for index, ((kernel, bias), (kernel_shape, bias_shape)) in enumerate(
            zip(self._layers, expected_shapes, strict=True)
        ):
            if kernel.shape != kernel_shape or bias.shape != bias_shape:
                raise RuntimeError(
                    f"trusted position-policy actor layer {index} has the wrong shape"
                )
            if not np.all(np.isfinite(kernel)) or not np.all(np.isfinite(bias)):
                raise RuntimeError(
                    f"trusted position-policy actor layer {index} is not finite"
                )

    def _vector(self, value: Sequence[float], *, size: int, name: str) -> Any:
        np = self._load_numpy()
        result = np.asarray(value, dtype=np.float64)
        if result.shape != (size,) or not np.all(np.isfinite(result)):
            raise ValueError(f"{name} must contain exactly {size} finite values")
        return result

    def _gyro(self) -> Any:
        start = int(self.model.sensor_adr[self._gyro_sensor_id])
        stop = start + int(self.model.sensor_dim[self._gyro_sensor_id])
        return self._load_numpy().asarray(
            self.data.sensordata[start:stop], dtype=self._load_numpy().float64
        ).copy()

    def get_upvector(self) -> Any:
        """Return the IMU frame's world-up projection from fresh state."""

        self._resolve()
        np = self._load_numpy()
        rotation = np.asarray(
            self.data.site_xmat[self._imu_site_id], dtype=np.float64
        ).reshape(3, 3)
        return rotation[:, 2].copy()

    def get_joint_positions(self) -> Any:
        """Read the twelve policy joints in configured order."""

        self._resolve()
        return self._load_numpy().asarray(
            self.data.qpos[self._joint_qpos_addresses], dtype=self._load_numpy().float64
        ).copy()

    def get_base_pose(self) -> tuple[Any, Any]:
        """Return base world position and rotation from fresh canonical state."""

        self._resolve()
        self._load_mujoco().mj_forward(self.model, self.data)
        np = self._load_numpy()
        position = np.asarray(self.data.xpos[self._base_body_id], dtype=np.float64).copy()
        rotation = np.asarray(
            self.data.xmat[self._base_body_id], dtype=np.float64
        ).reshape(3, 3).copy()
        return position, rotation

    def get_base_velocity(self) -> Any:
        """Return local IMU-frame linear velocity from fresh canonical state."""

        self._resolve()
        np = self._load_numpy()
        spatial_velocity = np.zeros(6, dtype=np.float64)
        self._load_mujoco().mj_objectVelocity(
            self.model,
            self.data,
            self._load_mujoco().mjtObj.mjOBJ_SITE,
            self._imu_site_id,
            spatial_velocity,
            1,
        )
        return spatial_velocity[3:].copy()

    def _observation_frame(self, command: Any) -> Any:
        np = self._load_numpy()
        frame = np.concatenate(
            (
                self._gyro()[-1:] * 0.25,
                self.get_upvector(),
                command * np.asarray(_COMMAND_SCALE, dtype=np.float64),
                self.get_joint_positions() - self._home_joint_position,
                self._previous_action,
            )
        )
        if frame.shape != (_OBSERVATION_FRAME_SIZE,) or not np.all(np.isfinite(frame)):
            raise RuntimeError("position-policy observation frame is invalid")
        return np.clip(frame, -100.0, 100.0)

    def _initialize_history(self, command: Any) -> None:
        np = self._load_numpy()
        self._load_mujoco().mj_forward(self.model, self.data)
        self._previous_action = np.zeros(12, dtype=np.float64)
        self._history = np.zeros(_OBSERVATION_SIZE, dtype=np.float64)
        self._history[:_OBSERVATION_FRAME_SIZE] = self._observation_frame(command)
        self._current_command = command.copy()

    def _set_current_command(self, command: Any) -> None:
        np = self._load_numpy()
        if self._history is None:
            self._initialize_history(command)
            return
        if not np.array_equal(command, self._current_command):
            self._history[4:7] = command * np.asarray(_COMMAND_SCALE, dtype=np.float64)
            self._current_command = command.copy()

    def _actor(self) -> Any:
        np = self._load_numpy()
        if self._history is None:
            raise RuntimeError("position-policy history has not been initialized")
        value = (
            np.asarray(self._history, dtype=np.float32) - self._normalizer_mean
        ) / self._normalizer_std
        for kernel, bias in self._layers[:-1]:
            with np.errstate(over="ignore"):
                value = (value @ kernel + bias)
                value = value / (np.float32(1.0) + np.exp(-value))
        logits = value @ self._layers[-1][0] + self._layers[-1][1]
        action = np.tanh(logits[:12]).astype(np.float64)
        if action.shape != (12,) or not np.all(np.isfinite(action)):
            raise RuntimeError("trusted position-policy actor returned an invalid action")
        return action

    def _fall_reason(self) -> str | None:
        np = self._load_numpy()
        position, _rotation = self.get_base_pose()
        joint_position = self.get_joint_positions()
        if not np.all(np.isfinite(self.data.qpos)) or not np.all(
            np.isfinite(self.data.qvel)
        ):
            return "non_finite_state"
        if float(self.get_upvector()[-1]) < 0.0:
            return "inverted"
        if float(position[2]) < self.spec.minimum_base_height_m:
            return "low_base"
        if np.any(joint_position < self._joint_low) or np.any(
            joint_position > self._joint_high
        ):
            return "policy_joint_limit"
        return None

    def command_planar_velocity(
        self,
        vx: float,
        vy: float,
        yaw_rate: float,
        duration: float = 1.0,
    ) -> dict[str, Any]:
        """Track a finite local planar/yaw command with closed-loop inference."""

        self._resolve()
        command = self._vector((vx, vy, yaw_rate), size=3, name="command")
        duration_s = _finite(duration, "duration")
        if duration_s <= 0.0 or duration_s > self.spec.maximum_command_duration_s:
            raise ValueError(
                "duration must be positive and within maximum_command_duration_s"
            )
        self._set_current_command(command)
        policy_steps = int(math.ceil(duration_s / self.spec.policy_period_s))

        np = self._load_numpy()
        local_velocities: list[Any] = []
        local_yaw_rates: list[float] = []
        fall_reason = None
        completed_policy_steps = 0
        for policy_step in range(policy_steps):
            action = self._actor()
            target = np.clip(
                self._home_joint_position + self.spec.action_scale * action,
                self._joint_low,
                self._joint_high,
            )
            self.data.ctrl[self._actuator_ids] = target
            for _ in range(self._steps_per_action):
                self._load_mujoco().mj_step(self.model, self.data)
            completed_policy_steps = policy_step + 1
            local_velocities.append(self.get_base_velocity())
            local_yaw_rates.append(float(self._gyro()[-1]))

            frame = self._observation_frame(command)
            if self._history is None:
                raise RuntimeError("position-policy history was lost")
            self._history = np.roll(self._history, _OBSERVATION_FRAME_SIZE)
            self._history[:_OBSERVATION_FRAME_SIZE] = frame
            self._previous_action = action

            fall_reason = self._fall_reason()
            if fall_reason is not None:
                break

        settling_steps = min(
            round(1.0 / self.spec.policy_period_s), completed_policy_steps - 1
        )
        selected_velocities = np.asarray(local_velocities[settling_steps:])
        selected_yaw_rates = np.asarray(local_yaw_rates[settling_steps:])
        return {
            "requested_duration_s": duration_s,
            "elapsed_s": completed_policy_steps * self.spec.policy_period_s,
            "policy_steps": completed_policy_steps,
            "physics_steps": completed_policy_steps * self._steps_per_action,
            "steps_per_policy_action": self._steps_per_action,
            "fall_reason": fall_reason,
            "mean_local_velocity_after_1s_m_s": selected_velocities.mean(axis=0),
            "mean_local_yaw_rate_after_1s_rad_s": float(selected_yaw_rates.mean()),
            "final_upvector": self.get_upvector(),
        }

    def hold(self, duration: float = 1.0) -> dict[str, Any]:
        """Apply the retained actor with a zero local velocity command."""

        return self.command_planar_velocity(0.0, 0.0, 0.0, duration)


__all__ = [
    "BARKOUR_POLICY_JOINT_HIGH",
    "BARKOUR_POLICY_JOINT_LOW",
    "BarkourPositionPolicySkeleton",
    "POLICY_ARTIFACT_ID",
    "POLICY_SOURCE_REVISION",
    "QuadrupedPositionPolicySpec",
]
