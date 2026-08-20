"""Task-neutral retained velocity policy for the Unitree G1.

The policy maps fresh public MuJoCo state and one local velocity command to
29 joint-position targets. It neither loads scenes nor owns resets, routes,
task dispatch, private criteria, or success decisions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from autoadapter2.driver_synthesis import SessionBoundSkeleton


POLICY_ARTIFACT_ID = "unitree-mjlab-g1-velocity-v0"
POLICY_SOURCE_REVISION = "1425b15f73bd4095f0df53709d7c389c3eb9e790"

_POLICY_RESOURCE = Path(__file__).with_name("data") / "g1_velocity_policy.npz"
_JOINT_COUNT = 29
_OBSERVATION_SIZE = 98


def _finite(value: float, field_name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _name(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _finite_tuple(
    values: Sequence[float], field_name: str, expected_length: int
) -> tuple[float, ...]:
    result = tuple(_finite(value, field_name) for value in values)
    if len(result) != expected_length:
        raise ValueError(f"{field_name} must contain {expected_length} values")
    return result


@dataclass(frozen=True)
class G1VelocityPolicySpec:
    """Public morphology and command bounds for the retained G1 policy."""

    base_body_name: str
    gyro_sensor_name: str
    joint_names: Sequence[str]
    actuator_names: Sequence[str]
    default_joint_positions: Sequence[float]
    action_scales: Sequence[float]
    stiffness: Sequence[float]
    damping: Sequence[float]
    effort_limits: Sequence[float]
    policy_period_s: float = 0.02
    expected_physics_timestep_s: float = 0.005
    gait_period_s: float = 0.6
    minimum_command: Sequence[float] = (-0.5, -0.5, -1.0)
    maximum_command: Sequence[float] = (1.0, 0.5, 1.0)
    maximum_command_duration_s: float = 30.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "base_body_name", _name(self.base_body_name, "base_body_name")
        )
        object.__setattr__(
            self,
            "gyro_sensor_name",
            _name(self.gyro_sensor_name, "gyro_sensor_name"),
        )
        joints = tuple(_name(value, "joint_names") for value in self.joint_names)
        actuators = tuple(
            _name(value, "actuator_names") for value in self.actuator_names
        )
        if len(joints) != _JOINT_COUNT or len(set(joints)) != _JOINT_COUNT:
            raise ValueError("joint_names must contain 29 unique values")
        if len(actuators) != _JOINT_COUNT or len(set(actuators)) != _JOINT_COUNT:
            raise ValueError("actuator_names must contain 29 unique values")
        object.__setattr__(self, "joint_names", joints)
        object.__setattr__(self, "actuator_names", actuators)

        for field_name in (
            "default_joint_positions",
            "action_scales",
            "stiffness",
            "damping",
            "effort_limits",
        ):
            values = _finite_tuple(
                getattr(self, field_name), field_name, _JOINT_COUNT
            )
            if field_name != "default_joint_positions" and any(
                value <= 0.0 for value in values
            ):
                raise ValueError(f"{field_name} values must be positive")
            object.__setattr__(self, field_name, values)

        minimum = _finite_tuple(self.minimum_command, "minimum_command", 3)
        maximum = _finite_tuple(self.maximum_command, "maximum_command", 3)
        if any(low >= high for low, high in zip(minimum, maximum, strict=True)):
            raise ValueError("each minimum command must be below its maximum")
        object.__setattr__(self, "minimum_command", minimum)
        object.__setattr__(self, "maximum_command", maximum)

        for field_name in (
            "policy_period_s",
            "expected_physics_timestep_s",
            "gait_period_s",
            "maximum_command_duration_s",
        ):
            value = _finite(getattr(self, field_name), field_name)
            if value <= 0.0:
                raise ValueError(f"{field_name} must be positive")
            object.__setattr__(self, field_name, value)


class G1VelocityPolicySkeleton(SessionBoundSkeleton):
    """Retained G1 velocity policy bound to one Framework MuJoCo session."""

    def __init__(
        self,
        *,
        model: Any,
        data: Any,
        spec: G1VelocityPolicySpec,
    ) -> None:
        if not isinstance(spec, G1VelocityPolicySpec):
            raise TypeError("spec must be a G1VelocityPolicySpec")
        super().__init__(model=model, data=data, spec=spec)
        self._mj: Any = None
        self._np: Any = None
        self._resolved = False
        self._weights: dict[str, Any] = {}
        self._action: Any = None
        self._target: Any = None
        self._phase = 0.0
        self._physics_tick = 0
        self._inference_count = 0

    def _load_numpy(self) -> Any:
        if self._np is None:
            try:
                import numpy as np
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("NumPy is required for the G1 policy") from exc
            self._np = np
        return self._np

    def _load_mujoco(self) -> Any:
        if self._mj is None:
            try:
                import mujoco
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("MuJoCo is required for the G1 policy") from exc
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
            raise ValueError("canonical timestep does not match the G1 policy")
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
            raise ValueError("G1 base body must own a free joint")
        self._root_qpos_address = int(self.model.jnt_qposadr[root_joint_id])

        sensor_id = int(
            mj.mj_name2id(
                self.model,
                mj.mjtObj.mjOBJ_SENSOR,
                self.spec.gyro_sensor_name,
            )
        )
        if sensor_id < 0 or int(self.model.sensor_type[sensor_id]) != int(
            mj.mjtSensor.mjSENS_GYRO
        ):
            raise ValueError("G1 policy gyro sensor is absent or has the wrong type")
        if int(self.model.sensor_dim[sensor_id]) != 3:
            raise ValueError("G1 policy gyro sensor must have three values")
        sensor_address = int(self.model.sensor_adr[sensor_id])
        self._gyro_slice = slice(sensor_address, sensor_address + 3)

        self._joint_qpos_addresses: list[int] = []
        self._joint_qvel_addresses: list[int] = []
        self._actuator_ids: list[int] = []
        expected_armature = np.asarray(self.spec.stiffness) / np.float64(
            (20.0 * math.pi) ** 2
        )
        for index, (joint_name, actuator_name) in enumerate(
            zip(self.spec.joint_names, self.spec.actuator_names, strict=True)
        ):
            joint_id = int(
                mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_JOINT, joint_name)
            )
            actuator_id = int(
                mj.mj_name2id(
                    self.model, mj.mjtObj.mjOBJ_ACTUATOR, actuator_name
                )
            )
            if joint_id < 0 or actuator_id < 0:
                raise ValueError(
                    f"G1 joint/actuator pair is missing: {joint_name!r}, "
                    f"{actuator_name!r}"
                )
            if int(self.model.jnt_type[joint_id]) != int(mj.mjtJoint.mjJNT_HINGE):
                raise ValueError(f"G1 policy joint {joint_name!r} must be a hinge")
            if int(self.model.actuator_trntype[actuator_id]) != int(
                mj.mjtTrn.mjTRN_JOINT
            ) or int(self.model.actuator_trnid[actuator_id, 0]) != joint_id:
                raise ValueError(f"G1 actuator {actuator_name!r} targets the wrong joint")

            kp = float(self.spec.stiffness[index])
            kd = float(self.spec.damping[index])
            effort = float(self.spec.effort_limits[index])
            if not (
                math.isclose(float(self.model.actuator_gainprm[actuator_id, 0]), kp)
                and math.isclose(float(self.model.actuator_biasprm[actuator_id, 1]), -kp)
                and math.isclose(float(self.model.actuator_biasprm[actuator_id, 2]), -kd)
            ):
                raise ValueError(f"G1 actuator {actuator_name!r} has incompatible gains")
            if not bool(self.model.actuator_forcelimited[actuator_id]) or not np.allclose(
                self.model.actuator_forcerange[actuator_id], (-effort, effort)
            ):
                raise ValueError(f"G1 actuator {actuator_name!r} has incompatible limits")

            qpos_address = int(self.model.jnt_qposadr[joint_id])
            qvel_address = int(self.model.jnt_dofadr[joint_id])
            if not math.isclose(
                float(self.model.dof_armature[qvel_address]),
                float(expected_armature[index]),
                rel_tol=0.0,
                abs_tol=1.0e-10,
            ):
                raise ValueError(f"G1 joint {joint_name!r} has incompatible armature")
            self._joint_qpos_addresses.append(qpos_address)
            self._joint_qvel_addresses.append(qvel_address)
            self._actuator_ids.append(actuator_id)

        self._default_positions = np.asarray(
            self.spec.default_joint_positions, dtype=np.float32
        )
        self._action_scales = np.asarray(self.spec.action_scales, dtype=np.float32)
        self._minimum_command = np.asarray(self.spec.minimum_command, dtype=np.float32)
        self._maximum_command = np.asarray(self.spec.maximum_command, dtype=np.float32)
        self._load_weights()
        self._action = np.zeros(_JOINT_COUNT, dtype=np.float32)
        self._target = self._default_positions.copy()
        self._resolved = True

    def _load_weights(self) -> None:
        np = self._load_numpy()
        if not _POLICY_RESOURCE.is_file():
            raise RuntimeError(
                f"trusted G1 policy artifact is missing: {POLICY_ARTIFACT_ID}"
            )
        required_shapes = {
            "observation_mean": (98,),
            "observation_divisor": (98,),
            "layer_0_weight": (512, 98),
            "layer_0_bias": (512,),
            "layer_1_weight": (256, 512),
            "layer_1_bias": (256,),
            "layer_2_weight": (128, 256),
            "layer_2_bias": (128,),
            "layer_3_weight": (29, 128),
            "layer_3_bias": (29,),
        }
        with np.load(_POLICY_RESOURCE, allow_pickle=False) as retained:
            if set(retained.files) != set(required_shapes):
                raise RuntimeError("trusted G1 policy artifact has unexpected tensors")
            self._weights = {
                name: retained[name].astype(np.float32)
                for name in required_shapes
            }
        for name, shape in required_shapes.items():
            value = self._weights[name]
            if value.shape != shape or not np.all(np.isfinite(value)):
                raise RuntimeError(f"trusted G1 policy tensor {name!r} is invalid")
        if np.any(self._weights["observation_divisor"] == 0.0):
            raise RuntimeError("trusted G1 observation divisor contains zero")

    def _elu(self, value: Any) -> Any:
        np = self._load_numpy()
        result = value.copy()
        negative = result < 0.0
        result[negative] = np.expm1(result[negative])
        return result

    def _infer(self, observation: Any) -> Any:
        np = self._load_numpy()
        value = np.asarray(observation, dtype=np.float32)
        if value.shape != (_OBSERVATION_SIZE,) or not np.all(np.isfinite(value)):
            raise ValueError("G1 policy observation must contain 98 finite values")
        value = (
            value - self._weights["observation_mean"]
        ) / self._weights["observation_divisor"]
        for layer in range(4):
            value = (
                value @ self._weights[f"layer_{layer}_weight"].T
                + self._weights[f"layer_{layer}_bias"]
            )
            if layer < 3:
                value = self._elu(value)
        action = value.astype(np.float32)
        if action.shape != (_JOINT_COUNT,) or not np.all(np.isfinite(action)):
            raise RuntimeError("trusted G1 policy returned an invalid action")
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
        quaternion = np.asarray(self.data.qpos[qpos + 3 : qpos + 7], dtype=np.float32)
        gyro = np.asarray(self.data.sensordata[self._gyro_slice], dtype=np.float32)
        gravity = self._quat_rotate_inverse(
            quaternion, np.asarray((0.0, 0.0, -1.0), dtype=np.float32)
        )
        self._phase = (
            self._phase + self.spec.policy_period_s / self.spec.gait_period_s
        ) % 1.0
        gait = np.asarray(
            (
                math.sin(2.0 * math.pi * self._phase),
                math.cos(2.0 * math.pi * self._phase),
            ),
            dtype=np.float32,
        )
        if float(np.linalg.norm(command)) < 0.1:
            gait.fill(0.0)
        joint_position = np.asarray(
            self.data.qpos[self._joint_qpos_addresses], dtype=np.float32
        )
        joint_velocity = np.asarray(
            self.data.qvel[self._joint_qvel_addresses], dtype=np.float32
        )
        observation = np.concatenate(
            (
                gyro,
                gravity,
                command,
                gait,
                joint_position - self._default_positions,
                joint_velocity,
                self._action,
            )
        ).astype(np.float32)
        if observation.shape != (_OBSERVATION_SIZE,) or not np.all(
            np.isfinite(observation)
        ):
            raise RuntimeError("G1 policy assembled an invalid observation")
        return observation

    def get_base_pose(self) -> tuple[Any, Any]:
        """Return fresh pelvis world position and rotation."""

        self._resolve()
        np = self._load_numpy()
        return (
            np.asarray(self.data.xpos[self._base_body_id], dtype=float).copy(),
            np.asarray(self.data.xmat[self._base_body_id], dtype=float)
            .reshape(3, 3)
            .copy(),
        )

    def command_velocity(
        self,
        vx: float,
        vy: float,
        yaw_rate: float,
        duration: float = 1.0,
    ) -> dict[str, Any]:
        """Track one finite local velocity command through MuJoCo physics."""

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
        if np.any(command < self._minimum_command) or np.any(
            command > self._maximum_command
        ):
            raise ValueError("velocity command exceeds the retained policy bounds")
        duration_s = _finite(duration, "duration")
        if duration_s <= 0.0 or duration_s > self.spec.maximum_command_duration_s:
            raise ValueError("duration is outside the policy bound")

        physics_steps = max(
            1, int(math.ceil(duration_s / float(self.model.opt.timestep)))
        )
        for _ in range(physics_steps):
            if self._physics_tick % self._steps_per_action == 0:
                self._action = self._infer(self._observation(command))
                self._target = (
                    self._default_positions + self._action_scales * self._action
                )
                self._inference_count += 1
            self.data.ctrl[self._actuator_ids] = self._target
            self._load_mujoco().mj_step(self.model, self.data)
            self._physics_tick += 1

        if not np.all(np.isfinite(self.data.qpos)) or not np.all(
            np.isfinite(self.data.qvel)
        ):
            raise RuntimeError("G1 policy produced non-finite MuJoCo state")
        position, rotation = self.get_base_pose()
        return {
            "requested_duration_s": duration_s,
            "physics_steps": physics_steps,
            "policy_inference_count": self._inference_count,
            "base_position": position,
            "base_rotation": rotation,
            "last_action": self._action.copy(),
            "last_joint_target": self._target.copy(),
        }

    def hold(self, duration: float = 1.0) -> dict[str, Any]:
        """Run the retained policy with a zero local velocity command."""

        return self.command_velocity(0.0, 0.0, 0.0, duration)


__all__ = [
    "G1VelocityPolicySkeleton",
    "G1VelocityPolicySpec",
    "POLICY_ARTIFACT_ID",
    "POLICY_SOURCE_REVISION",
]
