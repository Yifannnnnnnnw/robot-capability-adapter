"""Calibration-only Barkour vB flat-ground joystick controller.

This module replays the retained MuJoCo Playground v0.0.5 actor against the
Framework-supplied canonical MuJoCo session.  It contains no task dispatch,
private case data, verdict logic, scene loading, or simulator reset.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Sequence

import mujoco
import numpy as np


_ACTUATOR_NAMES = (
    "abduction_front_left",
    "hip_front_left",
    "knee_front_left",
    "abduction_hind_left",
    "hip_hind_left",
    "knee_hind_left",
    "abduction_front_right",
    "hip_front_right",
    "knee_front_right",
    "abduction_hind_right",
    "hip_hind_right",
    "knee_hind_right",
)
_POLICY_JOINT_LOW = np.asarray([-0.7, -1.0, 0.05] * 4, dtype=np.float64)
_POLICY_JOINT_HIGH = np.asarray([0.52, 2.1, 2.1] * 4, dtype=np.float64)
_COMMAND_SCALE = np.asarray([2.0, 2.0, 0.25], dtype=np.float64)
_ACTION_SCALE = 0.3
_POLICY_PERIOD_S = 0.02
_OBSERVATION_FRAME_SIZE = 31
_OBSERVATION_HISTORY_FRAMES = 15
_OBSERVATION_SIZE = _OBSERVATION_FRAME_SIZE * _OBSERVATION_HISTORY_FRAMES
_DEFAULT_ACTOR_PATH = Path(__file__).with_name("barkour_joystick_actor.npz")


def _finite_vector(value: Sequence[float], *, size: int, name: str) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64)
    if result.shape != (size,) or not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain exactly {size} finite values")
    return result


def _swish(value: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore"):
        return value / (np.float32(1.0) + np.exp(-value))


class BarkourFlatJoystickController:
    """Retained flat-ground actor bound to one canonical Barkour session."""

    def __init__(
        self,
        *,
        model: Any,
        data: Any,
        actor_path: str | Path = _DEFAULT_ACTOR_PATH,
    ) -> None:
        if model is None or data is None:
            raise ValueError("canonical model and data are required")
        self.model = model
        self.data = data
        self._validate_model()
        self._load_actor(Path(actor_path))
        self._history: np.ndarray | None = None
        self._previous_action = np.zeros(12, dtype=np.float64)

    def _validate_model(self) -> None:
        if (int(self.model.nq), int(self.model.nv), int(self.model.nu)) != (19, 18, 12):
            raise ValueError("canonical Barkour dimensions must be nq=19, nv=18, nu=12")
        timestep = float(self.model.opt.timestep)
        if not math.isclose(timestep, 0.001, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("canonical Barkour timestep must be 0.001 seconds")
        self._steps_per_action = round(_POLICY_PERIOD_S / timestep)
        if self._steps_per_action != 20:
            raise ValueError("Barkour policy period must resolve to 20 physics steps")

        self._actuator_ids: list[int] = []
        self._joint_qpos_addresses: list[int] = []
        for name in _ACTUATOR_NAMES:
            actuator_id = int(
                mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            )
            joint_id = int(mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name))
            if actuator_id < 0 or joint_id < 0:
                raise ValueError(f"canonical Barkour joint/actuator {name!r} is missing")
            self._actuator_ids.append(actuator_id)
            self._joint_qpos_addresses.append(int(self.model.jnt_qposadr[joint_id]))
        if len(set(self._actuator_ids)) != 12:
            raise ValueError("canonical Barkour actuator mapping contains duplicates")

        self._imu_site_id = int(
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "imu_frame")
        )
        self._gyro_sensor_id = int(
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SENSOR, "gyro")
        )
        self._home_key_id = int(
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_KEY, "home")
        )
        if min(self._imu_site_id, self._gyro_sensor_id, self._home_key_id) < 0:
            raise ValueError("canonical Barkour IMU, gyro, or home keyframe is missing")
        if int(self.model.sensor_dim[self._gyro_sensor_id]) != 3:
            raise ValueError("canonical Barkour gyro sensor must contain three values")
        self._home_joint_position = np.asarray(
            self.model.key_qpos[self._home_key_id, self._joint_qpos_addresses],
            dtype=np.float64,
        ).copy()

    def _load_actor(self, actor_path: Path) -> None:
        if not actor_path.is_file():
            raise ValueError(f"retained Barkour actor is missing: {actor_path}")
        with np.load(actor_path, allow_pickle=False) as retained:
            required = {"normalizer_mean", "normalizer_std"}
            required.update(
                f"actor_{index}_{suffix}"
                for index in range(5)
                for suffix in ("kernel", "bias")
            )
            if not required.issubset(retained.files):
                missing = sorted(required - set(retained.files))
                raise ValueError(f"retained Barkour actor is missing arrays: {missing}")
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
            raise ValueError("retained Barkour normalizer mean has the wrong shape")
        if self._normalizer_std.shape != (_OBSERVATION_SIZE,):
            raise ValueError("retained Barkour normalizer std has the wrong shape")
        if not np.all(np.isfinite(self._normalizer_mean)):
            raise ValueError("retained Barkour normalizer mean must be finite")
        if not np.all(np.isfinite(self._normalizer_std)) or np.any(
            self._normalizer_std <= 0.0
        ):
            raise ValueError("retained Barkour normalizer std must be finite and positive")
        for index, ((kernel, bias), (kernel_shape, bias_shape)) in enumerate(
            zip(self._layers, expected_shapes, strict=True)
        ):
            if kernel.shape != kernel_shape or bias.shape != bias_shape:
                raise ValueError(f"retained Barkour actor layer {index} has the wrong shape")
            if not np.all(np.isfinite(kernel)) or not np.all(np.isfinite(bias)):
                raise ValueError(f"retained Barkour actor layer {index} must be finite")

    def _gyro(self) -> np.ndarray:
        start = int(self.model.sensor_adr[self._gyro_sensor_id])
        stop = start + int(self.model.sensor_dim[self._gyro_sensor_id])
        return np.asarray(self.data.sensordata[start:stop], dtype=np.float64).copy()

    def _upvector(self) -> np.ndarray:
        rotation = np.asarray(
            self.data.site_xmat[self._imu_site_id], dtype=np.float64
        ).reshape(3, 3)
        return rotation[:, 2].copy()

    def _local_imu_velocity(self) -> np.ndarray:
        spatial_velocity = np.zeros(6, dtype=np.float64)
        mujoco.mj_objectVelocity(
            self.model,
            self.data,
            mujoco.mjtObj.mjOBJ_SITE,
            self._imu_site_id,
            spatial_velocity,
            1,
        )
        return spatial_velocity[3:].copy()

    def _observation_frame(self, command: np.ndarray) -> np.ndarray:
        joint_position = np.asarray(
            self.data.qpos[self._joint_qpos_addresses], dtype=np.float64
        )
        frame = np.concatenate(
            (
                self._gyro()[-1:] * 0.25,
                self._upvector(),
                command * _COMMAND_SCALE,
                joint_position - self._home_joint_position,
                self._previous_action,
            )
        )
        if frame.shape != (_OBSERVATION_FRAME_SIZE,) or not np.all(np.isfinite(frame)):
            raise RuntimeError("canonical Barkour observation frame is invalid")
        return np.clip(frame, -100.0, 100.0)

    def initialize_history(self, command: Sequence[float] = (0.0, 0.0, 0.0)) -> None:
        """Reset controller memory from the current physical state, not MuJoCo state."""

        command_vector = _finite_vector(command, size=3, name="command")
        mujoco.mj_forward(self.model, self.data)
        self._previous_action = np.zeros(12, dtype=np.float64)
        self._history = np.zeros(_OBSERVATION_SIZE, dtype=np.float64)
        frame = self._observation_frame(command_vector)
        self._history = np.roll(self._history, _OBSERVATION_FRAME_SIZE)
        self._history[:_OBSERVATION_FRAME_SIZE] = frame

    def _actor(self) -> np.ndarray:
        if self._history is None:
            raise RuntimeError("controller history has not been initialized")
        value = (
            np.asarray(self._history, dtype=np.float32) - self._normalizer_mean
        ) / self._normalizer_std
        for kernel, bias in self._layers[:-1]:
            value = _swish(value @ kernel + bias)
        logits = value @ self._layers[-1][0] + self._layers[-1][1]
        action = np.tanh(logits[:12]).astype(np.float64)
        if action.shape != (12,) or not np.all(np.isfinite(action)):
            raise RuntimeError("retained Barkour actor returned an invalid action")
        return action

    def _fall_reason(self) -> str | None:
        joint_position = np.asarray(
            self.data.qpos[self._joint_qpos_addresses], dtype=np.float64
        )
        if not np.all(np.isfinite(self.data.qpos)) or not np.all(
            np.isfinite(self.data.qvel)
        ):
            return "non_finite_state"
        if float(self._upvector()[-1]) < 0.0:
            return "inverted"
        if float(self.data.qpos[2]) < 0.18:
            return "low_torso"
        if np.any(joint_position < _POLICY_JOINT_LOW) or np.any(
            joint_position > _POLICY_JOINT_HIGH
        ):
            return "policy_joint_limit"
        return None

    def command_velocity(
        self,
        command: Sequence[float],
        *,
        duration_s: float,
    ) -> dict[str, Any]:
        """Track a finite local planar/yaw command for a bounded duration."""

        command_vector = _finite_vector(command, size=3, name="command")
        duration = float(duration_s)
        if not math.isfinite(duration) or duration <= 0.0:
            raise ValueError("duration_s must be finite and positive")
        policy_steps = int(math.ceil(duration / _POLICY_PERIOD_S))
        if self._history is None:
            self.initialize_history(command_vector)

        local_velocities: list[np.ndarray] = []
        local_yaw_rates: list[float] = []
        fall_reason = None
        completed_policy_steps = 0
        for policy_step in range(policy_steps):
            action = self._actor()
            target = np.clip(
                self._home_joint_position + _ACTION_SCALE * action,
                _POLICY_JOINT_LOW,
                _POLICY_JOINT_HIGH,
            )
            self.data.ctrl[self._actuator_ids] = target
            for _ in range(self._steps_per_action):
                mujoco.mj_step(self.model, self.data)
            completed_policy_steps = policy_step + 1
            local_velocities.append(self._local_imu_velocity())
            local_yaw_rates.append(float(self._gyro()[-1]))

            frame = self._observation_frame(command_vector)
            assert self._history is not None
            self._history = np.roll(self._history, _OBSERVATION_FRAME_SIZE)
            self._history[:_OBSERVATION_FRAME_SIZE] = frame
            self._previous_action = action

            fall_reason = self._fall_reason()
            if fall_reason is not None:
                break

        settling_steps = min(
            round(1.0 / _POLICY_PERIOD_S), completed_policy_steps - 1
        )
        selected_velocities = np.asarray(local_velocities[settling_steps:])
        selected_yaw_rates = np.asarray(local_yaw_rates[settling_steps:])
        return {
            "requested_duration_s": duration,
            "elapsed_s": completed_policy_steps * _POLICY_PERIOD_S,
            "policy_steps": completed_policy_steps,
            "physics_steps": completed_policy_steps * self._steps_per_action,
            "steps_per_policy_action": self._steps_per_action,
            "fall_reason": fall_reason,
            "mean_local_velocity_after_1s_m_s": selected_velocities.mean(axis=0),
            "mean_local_yaw_rate_after_1s_rad_s": float(selected_yaw_rates.mean()),
            "final_upvector": self._upvector(),
        }

    def hold(self, *, duration_s: float) -> dict[str, Any]:
        """Apply the retained actor with a zero local velocity command."""

        return self.command_velocity((0.0, 0.0, 0.0), duration_s=duration_s)


def build(*, model: Any, data: Any) -> BarkourFlatJoystickController:
    """Bind the retained calibration controller to Framework-owned state."""

    return BarkourFlatJoystickController(model=model, data=data)


__all__ = ["BarkourFlatJoystickController", "build"]
