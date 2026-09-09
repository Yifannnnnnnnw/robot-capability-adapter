# SPDX-License-Identifier: Apache-2.0
"""Task-neutral retained velocity policy for the Unitree Go2 torque model.

The policy maps fresh public MuJoCo state plus a local planar/yaw command to
bounded joint torques. It does not load scenes, reset state, choose routes,
dispatch tasks, inspect private criteria, or decide success.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .base import SkeletonBase


POLICY_ARTIFACT_ID = "go2-cts-150k"
POLICY_SOURCE_REVISION = "30e74dc507bec7a642a8c98be26081f2c6f0822d"

_POLICY_RESOURCE = Path(__file__).with_name("data") / "go2_velocity_policy.npz"
_JOINT_COUNT = 12
_OBSERVATION_SIZE = 45
_HISTORY_FRAMES = 5
_COMMAND_SCALE = (2.0, 2.0, 0.25)


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
class Go2VelocityPolicySpec:
    """Public morphology and command bounds for the retained Go2 policy."""

    base_body_name: str
    joint_names: Sequence[str]
    actuator_names: Sequence[str]
    default_joint_angles: Sequence[float]
    kp: float = 20.0
    kd: float = 0.5
    action_scale: float = 0.25
    policy_period_s: float = 0.02
    expected_physics_timestep_s: float = 0.002
    feedback_period_s: float = 0.04
    planar_feedback_kp: float = 0.4
    planar_feedback_ki: float = 0.3
    yaw_feedback_kp: float = 0.4
    yaw_feedback_ki: float = 0.3
    maximum_planar_correction_m_s: float = 0.4
    maximum_yaw_correction_rad_s: float = 0.8
    maximum_planar_speed_m_s: float = 1.2
    maximum_yaw_rate_rad_s: float = 3.0
    maximum_command_duration_s: float = 30.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "base_body_name", _name(self.base_body_name, "base_body_name")
        )
        joints = tuple(_name(value, "joint_names") for value in self.joint_names)
        actuators = tuple(
            _name(value, "actuator_names") for value in self.actuator_names
        )
        defaults = tuple(
            _finite(value, "default_joint_angles")
            for value in self.default_joint_angles
        )
        if len(joints) != _JOINT_COUNT or len(actuators) != _JOINT_COUNT:
            raise ValueError("joint_names and actuator_names must contain 12 values")
        if len(defaults) != _JOINT_COUNT:
            raise ValueError("default_joint_angles must contain 12 values")
        if len(set(joints)) != _JOINT_COUNT or len(set(actuators)) != _JOINT_COUNT:
            raise ValueError("joint and actuator names must be unique")

        for field_name in (
            "kp",
            "kd",
            "action_scale",
            "policy_period_s",
            "expected_physics_timestep_s",
            "feedback_period_s",
            "maximum_planar_correction_m_s",
            "maximum_yaw_correction_rad_s",
            "maximum_planar_speed_m_s",
            "maximum_yaw_rate_rad_s",
            "maximum_command_duration_s",
        ):
            value = _finite(getattr(self, field_name), field_name)
            if value <= 0.0:
                raise ValueError(f"{field_name} must be positive")
            object.__setattr__(self, field_name, value)

        for field_name in (
            "planar_feedback_kp",
            "planar_feedback_ki",
            "yaw_feedback_kp",
            "yaw_feedback_ki",
        ):
            value = _finite(getattr(self, field_name), field_name)
            if value < 0.0:
                raise ValueError(f"{field_name} must be non-negative")
            object.__setattr__(self, field_name, value)
        if self.planar_feedback_kp == 0.0 and self.planar_feedback_ki == 0.0:
            raise ValueError("planar feedback requires a positive gain")
        if self.yaw_feedback_kp == 0.0 and self.yaw_feedback_ki == 0.0:
            raise ValueError("yaw feedback requires a positive gain")
        feedback_ratio = self.feedback_period_s / self.policy_period_s
        if feedback_ratio < 1.0 or not math.isclose(
            feedback_ratio,
            round(feedback_ratio),
            rel_tol=0.0,
            abs_tol=1.0e-9,
        ):
            raise ValueError(
                "feedback_period_s must be an integer number of policy periods"
            )
        if self.feedback_period_s > 0.05:
            raise ValueError("feedback_period_s must not exceed 0.05 s")
        if self.maximum_planar_correction_m_s > self.maximum_planar_speed_m_s:
            raise ValueError("maximum planar correction exceeds the policy bound")
        if self.maximum_yaw_correction_rad_s > self.maximum_yaw_rate_rad_s:
            raise ValueError("maximum yaw correction exceeds the policy bound")

        object.__setattr__(self, "joint_names", joints)
        object.__setattr__(self, "actuator_names", actuators)
        object.__setattr__(self, "default_joint_angles", defaults)


class Go2VelocityPolicySkeleton(SkeletonBase):
    """Retained Go2 policy bound to one AA1-owned MuJoCo session.

    The policy shares the supplied ``model`` and ``data`` with the caller and
    advances that same physical session.  The implementation keeps the AA2
    NumPy forward pass, while the base class is AA1's ``SkeletonBase``.
    """

    def __init__(
        self,
        model: Any,
        data: Any,
        spec: Go2VelocityPolicySpec,
    ) -> None:
        if not isinstance(spec, Go2VelocityPolicySpec):
            raise TypeError("spec must be a Go2VelocityPolicySpec")
        super().__init__(model=model, data=data, spec=spec)
        # SkeletonBase already imports and stores MuJoCo.  Preserve that
        # handle so inherited ``step`` and ``render`` work before lazy policy
        # resolution; ``_load_mujoco`` still covers unusual direct use.
        self._np: Any = None
        self._resolved = False
        self._history: Any = None
        self._action: Any = None
        self._target: Any = None
        self._weights: dict[str, Any] = {}
        self._physics_tick = 0

    def _load_numpy(self) -> Any:
        if self._np is None:
            try:
                import numpy as np
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("NumPy is required for the Go2 policy") from exc
            self._np = np
        return self._np

    def _load_mujoco(self) -> Any:
        if self._mj is None:
            try:
                import mujoco
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("MuJoCo is required for the Go2 policy") from exc
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
            abs_tol=1.0e-12,
        ):
            raise ValueError("canonical timestep does not match the Go2 policy")
        ratio = self.spec.policy_period_s / timestep
        self._steps_per_action = int(round(ratio))
        if self._steps_per_action < 1 or not math.isclose(
            ratio, self._steps_per_action, rel_tol=0.0, abs_tol=1.0e-9
        ):
            raise ValueError("policy period must be an integer number of physics steps")

        self._base_body_id = int(
            mj.mj_name2id(
                self.model, mj.mjtObj.mjOBJ_BODY, self.spec.base_body_name
            )
        )
        if self._base_body_id < 0:
            raise ValueError(f"base body {self.spec.base_body_name!r} is absent")
        root_joint_id = int(self.model.body_jntadr[self._base_body_id])
        if root_joint_id < 0 or int(self.model.jnt_type[root_joint_id]) != int(
            mj.mjtJoint.mjJNT_FREE
        ):
            raise ValueError("Go2 base body must own a free joint")
        self._root_qpos_address = int(self.model.jnt_qposadr[root_joint_id])
        self._root_qvel_address = int(self.model.jnt_dofadr[root_joint_id])

        self._joint_qpos_addresses: list[int] = []
        self._joint_qvel_addresses: list[int] = []
        self._actuator_ids: list[int] = []
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
                    f"Go2 joint/actuator pair is missing: {joint_name!r}, "
                    f"{actuator_name!r}"
                )
            if int(self.model.jnt_type[joint_id]) != int(mj.mjtJoint.mjJNT_HINGE):
                raise ValueError(f"Go2 policy joint {joint_name!r} must be a hinge")
            self._joint_qpos_addresses.append(int(self.model.jnt_qposadr[joint_id]))
            self._joint_qvel_addresses.append(int(self.model.jnt_dofadr[joint_id]))
            self._actuator_ids.append(actuator_id)

        self._control_low = np.full(_JOINT_COUNT, -np.inf, dtype=np.float32)
        self._control_high = np.full(_JOINT_COUNT, np.inf, dtype=np.float32)
        for index, actuator_id in enumerate(self._actuator_ids):
            if bool(self.model.actuator_ctrllimited[actuator_id]):
                self._control_low[index] = float(
                    self.model.actuator_ctrlrange[actuator_id, 0]
                )
                self._control_high[index] = float(
                    self.model.actuator_ctrlrange[actuator_id, 1]
                )

        self._default_angles = np.asarray(
            self.spec.default_joint_angles, dtype=np.float32
        )
        self._load_weights()
        self._history = np.zeros(
            (_HISTORY_FRAMES, _OBSERVATION_SIZE), dtype=np.float32
        )
        self._action = np.zeros(_JOINT_COUNT, dtype=np.float32)
        self._target = self._default_angles.copy()
        self._resolved = True

    def _load_weights(self) -> None:
        np = self._load_numpy()
        if not _POLICY_RESOURCE.is_file():
            raise RuntimeError(
                f"trusted Go2 policy artifact is missing: {POLICY_ARTIFACT_ID}"
            )
        required_shapes = {
            "student_encoder_0_weight": (512, 225),
            "student_encoder_0_bias": (512,),
            "student_encoder_2_weight": (256, 512),
            "student_encoder_2_bias": (256,),
            "student_encoder_4_weight": (32, 256),
            "student_encoder_4_bias": (32,),
            "actor_0_weight": (512, 77),
            "actor_0_bias": (512,),
            "actor_2_weight": (256, 512),
            "actor_2_bias": (256,),
            "actor_4_weight": (128, 256),
            "actor_4_bias": (128,),
            "actor_6_weight": (12, 128),
            "actor_6_bias": (12,),
        }
        with np.load(_POLICY_RESOURCE, allow_pickle=False) as retained:
            if set(retained.files) != set(required_shapes):
                raise RuntimeError("trusted Go2 policy artifact has unexpected tensors")
            self._weights = {
                name: retained[name].astype(np.float32)
                for name in required_shapes
            }
        for name, shape in required_shapes.items():
            value = self._weights[name]
            if value.shape != shape or not np.all(np.isfinite(value)):
                raise RuntimeError(f"trusted Go2 policy tensor {name!r} is invalid")

    def _linear(self, value: Any, prefix: str) -> Any:
        return (
            value @ self._weights[f"{prefix}_weight"].T
            + self._weights[f"{prefix}_bias"]
        )

    def _elu(self, value: Any) -> Any:
        np = self._load_numpy()
        result = value.copy()
        negative = result <= 0.0
        result[negative] = np.expm1(result[negative])
        return result

    def _infer(self, observation: Any) -> Any:
        np = self._load_numpy()
        value = np.asarray(observation, dtype=np.float32)
        if value.shape != (_OBSERVATION_SIZE,) or not np.all(np.isfinite(value)):
            raise ValueError("Go2 policy observation must contain 45 finite values")
        self._history = np.concatenate(
            (self._history[1:], value[None, :]), axis=0
        )
        latent = self._elu(
            self._linear(self._history.reshape(-1), "student_encoder_0")
        )
        latent = self._elu(self._linear(latent, "student_encoder_2"))
        latent = self._linear(latent, "student_encoder_4")
        if not np.all(np.isfinite(latent)):
            raise RuntimeError("trusted Go2 policy encoder returned an invalid latent")
        latent = latent / max(float(np.linalg.norm(latent)), 1.0e-12)
        actor = np.concatenate((latent, value)).astype(np.float32)
        actor = self._elu(self._linear(actor, "actor_0"))
        actor = self._elu(self._linear(actor, "actor_2"))
        actor = self._elu(self._linear(actor, "actor_4"))
        action = self._linear(actor, "actor_6").astype(np.float32)
        if action.shape != (_JOINT_COUNT,) or not np.all(np.isfinite(action)):
            raise RuntimeError("trusted Go2 policy returned an invalid action")
        return action

    def _quat_rotate_inverse(self, quaternion: Any, vector: Any) -> Any:
        np = self._load_numpy()
        scalar = quaternion[0]
        axis = quaternion[1:]
        return (
            vector * (2.0 * scalar * scalar - 1.0)
            - np.cross(axis, vector) * scalar * 2.0
            + axis * np.dot(axis, vector) * 2.0
        )

    def _observation(self, command: Any) -> Any:
        np = self._load_numpy()
        qpos = self._root_qpos_address
        qvel = self._root_qvel_address
        quaternion = np.asarray(self.data.qpos[qpos + 3 : qpos + 7], dtype=np.float32)
        angular_velocity = np.asarray(
            self.data.qvel[qvel + 3 : qvel + 6], dtype=np.float32
        )
        local_angular_velocity = self._quat_rotate_inverse(
            quaternion, angular_velocity
        )
        qw, qx, qy, qz = quaternion
        gravity = np.asarray(
            (
                2.0 * (-qz * qx + qw * qy),
                -2.0 * (qz * qy + qw * qx),
                1.0 - 2.0 * (qw * qw + qz * qz),
            ),
            dtype=np.float32,
        )
        joint_position = np.asarray(
            self.data.qpos[self._joint_qpos_addresses], dtype=np.float32
        )
        joint_velocity = np.asarray(
            self.data.qvel[self._joint_qvel_addresses], dtype=np.float32
        )
        observation = np.concatenate(
            (
                local_angular_velocity * np.float32(0.25),
                gravity,
                command * np.asarray(_COMMAND_SCALE, dtype=np.float32),
                joint_position - self._default_angles,
                joint_velocity * np.float32(0.05),
                self._action,
            )
        ).astype(np.float32)
        if observation.shape != (_OBSERVATION_SIZE,) or not np.all(
            np.isfinite(observation)
        ):
            raise RuntimeError("Go2 policy assembled an invalid observation")
        return observation

    def _apply_target(self) -> None:
        np = self._load_numpy()
        joint_position = np.asarray(
            self.data.qpos[self._joint_qpos_addresses], dtype=np.float32
        )
        joint_velocity = np.asarray(
            self.data.qvel[self._joint_qvel_addresses], dtype=np.float32
        )
        torque = (
            np.float32(self.spec.kp) * (self._target - joint_position)
            - np.float32(self.spec.kd) * joint_velocity
        )
        self.data.ctrl[self._actuator_ids] = np.clip(
            torque, self._control_low, self._control_high
        )

    def get_joint_positions(self) -> Any:
        """Return the twelve policy joints in ``spec.joint_names`` order."""

        self._resolve()
        np = self._load_numpy()
        return np.asarray(
            self.data.qpos[self._joint_qpos_addresses], dtype=np.float64
        ).copy()

    def get_joint_velocities(self) -> Any:
        """Return the twelve policy joint velocities in spec order."""

        self._resolve()
        np = self._load_numpy()
        return np.asarray(
            self.data.qvel[self._joint_qvel_addresses], dtype=np.float64
        ).copy()

    def get_joint_targets(self) -> Any:
        """Return the current low-level PD targets without advancing physics."""

        self._resolve()
        return self._target.copy()

    def set_joint_targets(self, targets: Sequence[float]) -> None:
        """Set twelve low-level PD targets in ``spec.joint_names`` order.

        This is a primitive for callers that need to inspect or hold a posture;
        it writes only the corresponding PD torque to ``data.ctrl`` and does
        not write qpos/qvel or advance the MuJoCo session.  The retained policy
        may replace the targets at its next inference tick.
        """

        self._resolve()
        np = self._load_numpy()
        values = np.asarray(targets, dtype=np.float32)
        if values.shape != (_JOINT_COUNT,) or not np.all(np.isfinite(values)):
            raise ValueError("targets must contain 12 finite joint angles")
        self._target = values.copy()
        self._apply_target()

    def apply_joint_targets(self) -> None:
        """Write PD torques for the current targets without stepping physics."""

        self._resolve()
        self._apply_target()

    def describe(self) -> dict[str, Any]:
        """Return static morphology, policy, and command metadata."""

        self._resolve()
        return {
            "skeleton_type": type(self).__name__,
            "base_type": "SkeletonBase",
            "dof": _JOINT_COUNT,
            "base_body": self.spec.base_body_name,
            "joint_names": list(self.spec.joint_names),
            "actuator_names": list(self.spec.actuator_names),
            "default_joint_angles": list(self.spec.default_joint_angles),
            "pd": {"kp": self.spec.kp, "kd": self.spec.kd},
            "policy": {
                "artifact_id": POLICY_ARTIFACT_ID,
                "source_revision": POLICY_SOURCE_REVISION,
                "observation_size": _OBSERVATION_SIZE,
                "history_frames": _HISTORY_FRAMES,
                "policy_period_s": self.spec.policy_period_s,
                "action_scale": self.spec.action_scale,
                "torch_runtime_required": False,
            },
            "command_limits": {
                "maximum_planar_speed_m_s": self.spec.maximum_planar_speed_m_s,
                "maximum_yaw_rate_rad_s": self.spec.maximum_yaw_rate_rad_s,
                "maximum_command_duration_s": self.spec.maximum_command_duration_s,
            },
        }

    def get_base_pose(self) -> tuple[Any, Any]:
        """Return fresh base world position and rotation."""

        self._resolve()
        np = self._load_numpy()
        return (
            np.asarray(self.data.xpos[self._base_body_id], dtype=float).copy(),
            np.asarray(self.data.xmat[self._base_body_id], dtype=float)
            .reshape(3, 3)
            .copy(),
        )

    def get_base_twist(self) -> dict[str, Any]:
        """Return fresh base twist with explicit world and body-yaw frames."""

        self._resolve()
        mj = self._load_mujoco()
        np = self._load_numpy()
        mj.mj_forward(self.model, self.data)
        spatial = np.zeros(6, dtype=float)
        mj.mj_objectVelocity(
            self.model,
            self.data,
            mj.mjtObj.mjOBJ_BODY,
            self._base_body_id,
            spatial,
            0,
        )
        angular_world = np.asarray(spatial[:3], dtype=float)
        # MuJoCo's free-joint translational qvel is the world-frame velocity of
        # the body origin.  mj_objectVelocity(BODY) reports at the inertial
        # frame and would add a spurious omega-cross-offset term here.
        qvel = self._root_qvel_address
        linear_world = np.asarray(
            self.data.qvel[qvel : qvel + 3], dtype=float
        )
        rotation = np.asarray(
            self.data.xmat[self._base_body_id], dtype=float
        ).reshape(3, 3)
        body_x_world = rotation[:, 0]
        horizontal_norm = math.hypot(
            float(body_x_world[0]), float(body_x_world[1])
        )
        if horizontal_norm <= 1.0e-9:
            raise RuntimeError("Go2 body yaw is undefined at the current orientation")
        cosine = float(body_x_world[0]) / horizontal_norm
        sine = float(body_x_world[1]) / horizontal_norm
        linear_body_yaw = np.asarray(
            (
                cosine * linear_world[0] + sine * linear_world[1],
                -sine * linear_world[0] + cosine * linear_world[1],
            ),
            dtype=float,
        )
        heading_rate = np.cross(angular_world, body_x_world)
        yaw_rate = (
            float(body_x_world[0]) * float(heading_rate[1])
            - float(body_x_world[1]) * float(heading_rate[0])
        ) / (horizontal_norm * horizontal_norm)
        return {
            "linear_world_m_s": linear_world.copy(),
            "linear_body_yaw_m_s": linear_body_yaw,
            "angular_world_rad_s": angular_world.copy(),
            "yaw_rate_rad_s": yaw_rate,
        }

    def track_planar_velocity(
        self,
        vx: float,
        vy: float,
        yaw_rate: float,
        duration: float = 1.0,
    ) -> dict[str, Any]:
        """Track a local planar twist with bounded fresh-velocity PI feedback."""

        self._resolve()
        np = self._load_numpy()
        requested = np.asarray(
            (
                _finite(vx, "vx"),
                _finite(vy, "vy"),
                _finite(yaw_rate, "yaw_rate"),
            ),
            dtype=float,
        )
        if (
            float(np.linalg.norm(requested[:2]))
            > self.spec.maximum_planar_speed_m_s
        ):
            raise ValueError("planar velocity request exceeds the policy bound")
        if abs(float(requested[2])) > self.spec.maximum_yaw_rate_rad_s:
            raise ValueError("yaw-rate request exceeds the policy bound")
        duration_s = _finite(duration, "duration")
        if (
            duration_s <= 0.0
            or duration_s > self.spec.maximum_command_duration_s
        ):
            raise ValueError("duration is outside the policy bound")

        timestep = float(self.model.opt.timestep)
        total_steps = max(1, int(math.ceil(duration_s / timestep)))
        feedback_steps = int(round(self.spec.feedback_period_s / timestep))
        integral = np.zeros(3, dtype=float)
        remaining_steps = total_steps
        feedback_cycles = 0
        physics_steps = 0
        last_policy_command = requested.copy()

        while remaining_steps > 0:
            chunk_steps = min(feedback_steps, remaining_steps)
            chunk_duration = chunk_steps * timestep
            twist = self.get_base_twist()
            measured = np.asarray(
                (
                    twist["linear_body_yaw_m_s"][0],
                    twist["linear_body_yaw_m_s"][1],
                    twist["yaw_rate_rad_s"],
                ),
                dtype=float,
            )
            error = requested - measured
            integral += error * chunk_duration
            if self.spec.planar_feedback_ki > 0.0:
                planar_integral_limit = (
                    self.spec.maximum_planar_correction_m_s
                    / self.spec.planar_feedback_ki
                )
                integral[:2] = np.clip(
                    integral[:2], -planar_integral_limit, planar_integral_limit
                )
            else:
                integral[:2] = 0.0
            if self.spec.yaw_feedback_ki > 0.0:
                yaw_integral_limit = (
                    self.spec.maximum_yaw_correction_rad_s
                    / self.spec.yaw_feedback_ki
                )
                integral[2] = float(
                    np.clip(integral[2], -yaw_integral_limit, yaw_integral_limit)
                )
            else:
                integral[2] = 0.0

            correction = np.asarray(
                (
                    self.spec.planar_feedback_kp * error[0]
                    + self.spec.planar_feedback_ki * integral[0],
                    self.spec.planar_feedback_kp * error[1]
                    + self.spec.planar_feedback_ki * integral[1],
                    self.spec.yaw_feedback_kp * error[2]
                    + self.spec.yaw_feedback_ki * integral[2],
                ),
                dtype=float,
            )
            correction_norm = float(np.linalg.norm(correction[:2]))
            if correction_norm > self.spec.maximum_planar_correction_m_s:
                correction[:2] *= (
                    self.spec.maximum_planar_correction_m_s / correction_norm
                )
            correction[2] = float(
                np.clip(
                    correction[2],
                    -self.spec.maximum_yaw_correction_rad_s,
                    self.spec.maximum_yaw_correction_rad_s,
                )
            )
            policy_command = requested + correction
            policy_speed = float(np.linalg.norm(policy_command[:2]))
            safe_planar_limit = float(
                np.nextafter(
                    np.float32(self.spec.maximum_planar_speed_m_s),
                    np.float32(0.0),
                )
            )
            if policy_speed > safe_planar_limit:
                policy_command[:2] *= safe_planar_limit / policy_speed
            safe_yaw_limit = float(
                np.nextafter(
                    np.float32(self.spec.maximum_yaw_rate_rad_s),
                    np.float32(0.0),
                )
            )
            policy_command[2] = float(
                np.clip(policy_command[2], -safe_yaw_limit, safe_yaw_limit)
            )
            result = self.command_planar_velocity(
                float(policy_command[0]),
                float(policy_command[1]),
                float(policy_command[2]),
                duration=chunk_duration,
            )
            physics_steps += int(result["physics_steps"])
            remaining_steps -= chunk_steps
            feedback_cycles += 1
            last_policy_command = policy_command

        final_twist = self.get_base_twist()
        return {
            "requested_velocity_body_yaw_m_s": requested[:2].copy(),
            "requested_yaw_rate_rad_s": float(requested[2]),
            "requested_duration_s": duration_s,
            "physics_steps": physics_steps,
            "feedback_cycles": feedback_cycles,
            "last_policy_command": last_policy_command.copy(),
            "final_twist": final_twist,
        }

    def command_planar_velocity(
        self,
        vx: float,
        vy: float,
        yaw_rate: float,
        duration: float = 1.0,
    ) -> dict[str, Any]:
        """Execute one raw local policy command through torque and physics."""

        self._resolve()
        np = self._load_numpy()
        command = np.asarray(
            (
                _finite(vx, "vx"),
                _finite(vy, "vy"),
                _finite(yaw_rate, "yaw_rate"),
            ),
            dtype=np.float32,
        )
        if float(np.linalg.norm(command[:2])) > self.spec.maximum_planar_speed_m_s:
            raise ValueError("planar velocity command exceeds the policy bound")
        if abs(float(command[2])) > self.spec.maximum_yaw_rate_rad_s:
            raise ValueError("yaw-rate command exceeds the policy bound")
        duration_s = _finite(duration, "duration")
        if duration_s <= 0.0 or duration_s > self.spec.maximum_command_duration_s:
            raise ValueError("duration is outside the policy bound")

        physics_steps = max(
            1, int(math.ceil(duration_s / float(self.model.opt.timestep)))
        )
        for _ in range(physics_steps):
            self._apply_target()
            self._load_mujoco().mj_step(self.model, self.data)
            if self._physics_tick % self._steps_per_action == 0:
                self._action = self._infer(self._observation(command))
                self._target = (
                    self._default_angles
                    + np.float32(self.spec.action_scale) * self._action
                )
            self._physics_tick += 1

        if not np.all(np.isfinite(self.data.qpos)) or not np.all(
            np.isfinite(self.data.qvel)
        ):
            raise RuntimeError("Go2 policy produced non-finite MuJoCo state")
        position, rotation = self.get_base_pose()
        return {
            "requested_duration_s": duration_s,
            "physics_steps": physics_steps,
            "policy_inference_count": (
                (self._physics_tick + self._steps_per_action - 1)
                // self._steps_per_action
            ),
            "base_position": position,
            "base_rotation": rotation,
            "last_action": self._action.copy(),
        }

    def hold(self, duration: float = 1.0) -> dict[str, Any]:
        """Run the retained policy with a zero local velocity command."""

        return self.command_planar_velocity(0.0, 0.0, 0.0, duration)


__all__ = [
    "Go2VelocityPolicySkeleton",
    "Go2VelocityPolicySpec",
    "POLICY_ARTIFACT_ID",
    "POLICY_SOURCE_REVISION",
]
