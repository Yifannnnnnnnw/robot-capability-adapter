"""Retained LEAP Hand cube-orientation policy for one Framework session.

The policy reads fresh public MuJoCo state and a Framework-owned goal sensor,
then advances only the hand position actuators and MuJoCo physics. It does not
load a scene, reset state, choose a task, or decide Harness success.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from autoadapter2.driver_synthesis import SessionBoundSkeleton


POLICY_ARTIFACT_ID = "mujoco-playground-leap-cube-reorient"
POLICY_SOURCE_REVISION = "e74217bb89c77a74ba02e4789263991864375799"

_POLICY_RESOURCE = (
    Path(__file__).with_name("data") / "leap_cube_reorientation_policy.npz"
)
_JOINT_COUNT = 16
_OBSERVATION_SIZE = 57


def _finite(value: float, field_name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _positive(value: float, field_name: str) -> float:
    result = _finite(value, field_name)
    if result <= 0.0:
        raise ValueError(f"{field_name} must be positive")
    return result


def _name(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class LeapCubeReorientationSpec:
    """Public model names and exact deployment dynamics for the policy."""

    joint_names: Sequence[str]
    actuator_names: Sequence[str]
    palm_position_sensor_name: str
    cube_position_sensor_name: str
    cube_orientation_sensor_name: str
    goal_orientation_sensor_name: str
    policy_period_s: float = 0.05
    expected_physics_timestep_s: float = 0.002
    action_scale: float = 0.5
    position_gain: float = 3.0
    joint_damping: float = 0.2
    joint_armature: float = 0.00149376
    joint_frictionloss: float = 0.02
    joint_effort_limit: float = 0.2196
    maximum_control_duration_s: float = 20.0

    def __post_init__(self) -> None:
        joints = tuple(_name(value, "joint_names") for value in self.joint_names)
        actuators = tuple(
            _name(value, "actuator_names") for value in self.actuator_names
        )
        if len(joints) != _JOINT_COUNT or len(set(joints)) != _JOINT_COUNT:
            raise ValueError("joint_names must contain 16 unique values")
        if len(actuators) != _JOINT_COUNT or len(set(actuators)) != _JOINT_COUNT:
            raise ValueError("actuator_names must contain 16 unique values")
        object.__setattr__(self, "joint_names", joints)
        object.__setattr__(self, "actuator_names", actuators)

        for field_name in (
            "palm_position_sensor_name",
            "cube_position_sensor_name",
            "cube_orientation_sensor_name",
            "goal_orientation_sensor_name",
        ):
            object.__setattr__(
                self, field_name, _name(getattr(self, field_name), field_name)
            )
        for field_name in (
            "policy_period_s",
            "expected_physics_timestep_s",
            "action_scale",
            "position_gain",
            "joint_damping",
            "joint_armature",
            "joint_frictionloss",
            "joint_effort_limit",
            "maximum_control_duration_s",
        ):
            object.__setattr__(
                self, field_name, _positive(getattr(self, field_name), field_name)
            )


class LeapCubeReorientationSkeleton(SessionBoundSkeleton):
    """Low-level cube-orientation tracker backed by the retained policy."""

    def __init__(
        self,
        *,
        model: Any,
        data: Any,
        spec: LeapCubeReorientationSpec,
    ) -> None:
        if not isinstance(spec, LeapCubeReorientationSpec):
            raise TypeError("spec must be a LeapCubeReorientationSpec")
        super().__init__(model=model, data=data, spec=spec)
        self._mj: Any = None
        self._np: Any = None
        self._resolved = False
        self._weights: dict[str, Any] = {}
        self._last_action: Any = None
        self._motor_targets: Any = None
        self._physics_tick = 0
        self._inference_count = 0

    def _load_numpy(self) -> Any:
        if self._np is None:
            try:
                import numpy as np
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("NumPy is required for the LEAP policy") from exc
            self._np = np
        return self._np

    def _load_mujoco(self) -> Any:
        if self._mj is None:
            try:
                import mujoco
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("MuJoCo is required for the LEAP policy") from exc
            self._mj = mujoco
        return self._mj

    def _sensor_slice(self, name: str, sensor_type: Any, dimension: int) -> slice:
        mj = self._load_mujoco()
        sensor_id = int(
            mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_SENSOR, name)
        )
        if sensor_id < 0:
            raise ValueError(f"LEAP policy sensor {name!r} is absent")
        if int(self.model.sensor_type[sensor_id]) != int(sensor_type):
            raise ValueError(f"LEAP policy sensor {name!r} has the wrong type")
        if int(self.model.sensor_dim[sensor_id]) != dimension:
            raise ValueError(f"LEAP policy sensor {name!r} has the wrong dimension")
        address = int(self.model.sensor_adr[sensor_id])
        return slice(address, address + dimension)

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
            raise ValueError("canonical timestep does not match the LEAP policy")
        ratio = self.spec.policy_period_s / timestep
        self._steps_per_action = int(round(ratio))
        if self._steps_per_action < 1 or not math.isclose(
            ratio, self._steps_per_action, rel_tol=0.0, abs_tol=1.0e-9
        ):
            raise ValueError("policy period must be an integer number of physics steps")

        joint_qpos_addresses: list[int] = []
        actuator_ids: list[int] = []
        control_low: list[float] = []
        control_high: list[float] = []
        for joint_name, actuator_name in zip(
            self.spec.joint_names, self.spec.actuator_names, strict=True
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
                    f"LEAP joint/actuator pair is missing: {joint_name!r}, "
                    f"{actuator_name!r}"
                )
            if int(self.model.jnt_type[joint_id]) != int(mj.mjtJoint.mjJNT_HINGE):
                raise ValueError(f"LEAP policy joint {joint_name!r} must be a hinge")
            if int(self.model.actuator_trntype[actuator_id]) != int(
                mj.mjtTrn.mjTRN_JOINT
            ) or int(self.model.actuator_trnid[actuator_id, 0]) != joint_id:
                raise ValueError(
                    f"LEAP actuator {actuator_name!r} targets the wrong joint"
                )
            if not bool(self.model.actuator_ctrllimited[actuator_id]):
                raise ValueError(
                    f"LEAP actuator {actuator_name!r} must have a control range"
                )
            if not (
                math.isclose(
                    float(self.model.actuator_gainprm[actuator_id, 0]),
                    self.spec.position_gain,
                )
                and math.isclose(
                    float(self.model.actuator_biasprm[actuator_id, 1]),
                    -self.spec.position_gain,
                )
            ):
                raise ValueError(
                    f"LEAP actuator {actuator_name!r} has incompatible gains"
                )

            dof_address = int(self.model.jnt_dofadr[joint_id])
            if not (
                math.isclose(
                    float(self.model.dof_damping[dof_address]),
                    self.spec.joint_damping,
                )
                and math.isclose(
                    float(self.model.dof_armature[dof_address]),
                    self.spec.joint_armature,
                )
                and math.isclose(
                    float(self.model.dof_frictionloss[dof_address]),
                    self.spec.joint_frictionloss,
                )
            ):
                raise ValueError(f"LEAP joint {joint_name!r} has incompatible dynamics")
            if not bool(self.model.jnt_actfrclimited[joint_id]) or not np.allclose(
                self.model.jnt_actfrcrange[joint_id],
                (-self.spec.joint_effort_limit, self.spec.joint_effort_limit),
                rtol=0.0,
                atol=1.0e-12,
            ):
                raise ValueError(
                    f"LEAP joint {joint_name!r} has incompatible effort limits"
                )

            joint_qpos_addresses.append(int(self.model.jnt_qposadr[joint_id]))
            actuator_ids.append(actuator_id)
            control_low.append(float(self.model.actuator_ctrlrange[actuator_id, 0]))
            control_high.append(float(self.model.actuator_ctrlrange[actuator_id, 1]))

        self._palm_position_slice = self._sensor_slice(
            self.spec.palm_position_sensor_name,
            mj.mjtSensor.mjSENS_FRAMEPOS,
            3,
        )
        self._cube_position_slice = self._sensor_slice(
            self.spec.cube_position_sensor_name,
            mj.mjtSensor.mjSENS_FRAMEPOS,
            3,
        )
        self._cube_orientation_slice = self._sensor_slice(
            self.spec.cube_orientation_sensor_name,
            mj.mjtSensor.mjSENS_FRAMEQUAT,
            4,
        )
        self._goal_orientation_slice = self._sensor_slice(
            self.spec.goal_orientation_sensor_name,
            mj.mjtSensor.mjSENS_FRAMEQUAT,
            4,
        )
        self._joint_qpos_addresses = np.asarray(
            joint_qpos_addresses, dtype=np.int64
        )
        self._actuator_ids = np.asarray(actuator_ids, dtype=np.int64)
        self._control_low = np.asarray(control_low, dtype=np.float64)
        self._control_high = np.asarray(control_high, dtype=np.float64)
        self._motor_targets = np.asarray(
            self.data.ctrl[self._actuator_ids], dtype=np.float64
        ).copy()
        if not np.all(np.isfinite(self._motor_targets)):
            raise ValueError("LEAP initial motor targets must be finite")
        self._motor_targets = np.clip(
            self._motor_targets, self._control_low, self._control_high
        )
        self._last_action = np.zeros(_JOINT_COUNT, dtype=np.float32)
        self._load_weights()
        self._resolved = True

    def _load_weights(self) -> None:
        np = self._load_numpy()
        if not _POLICY_RESOURCE.is_file():
            raise RuntimeError(
                f"trusted LEAP policy artifact is missing: {POLICY_ARTIFACT_ID}"
            )
        required_shapes = {
            "observation_mean": (57,),
            "observation_reciprocal": (57,),
            "layer_0_weight": (57, 512),
            "layer_0_bias": (512,),
            "layer_1_weight": (512, 256),
            "layer_1_bias": (256,),
            "layer_2_weight": (256, 128),
            "layer_2_bias": (128,),
            "layer_3_weight": (128, 32),
            "layer_3_bias": (32,),
        }
        with np.load(_POLICY_RESOURCE, allow_pickle=False) as retained:
            if set(retained.files) != set(required_shapes):
                raise RuntimeError(
                    "trusted LEAP policy artifact has unexpected tensors"
                )
            self._weights = {
                name: retained[name].astype(np.float32)
                for name in required_shapes
            }
        for name, shape in required_shapes.items():
            value = self._weights[name]
            if value.shape != shape or not np.all(np.isfinite(value)):
                raise RuntimeError(f"trusted LEAP policy tensor {name!r} is invalid")

    def _silu(self, value: Any) -> Any:
        np = self._load_numpy()
        sigmoid = np.empty_like(value)
        positive = value >= 0.0
        sigmoid[positive] = 1.0 / (1.0 + np.exp(-value[positive]))
        exponential = np.exp(value[~positive])
        sigmoid[~positive] = exponential / (1.0 + exponential)
        return value * sigmoid

    def _infer(self, observation: Any) -> Any:
        np = self._load_numpy()
        value = np.asarray(observation, dtype=np.float32)
        if value.shape != (_OBSERVATION_SIZE,) or not np.all(np.isfinite(value)):
            raise ValueError("LEAP policy observation must contain 57 finite values")
        value = (
            value - self._weights["observation_mean"]
        ) * self._weights["observation_reciprocal"]
        for layer in range(4):
            value = (
                value @ self._weights[f"layer_{layer}_weight"]
                + self._weights[f"layer_{layer}_bias"]
            )
            if layer < 3:
                value = self._silu(value)
        action = np.tanh(value[:_JOINT_COUNT]).astype(np.float32)
        if action.shape != (_JOINT_COUNT,) or not np.all(np.isfinite(action)):
            raise RuntimeError("trusted LEAP policy returned an invalid action")
        return action

    def _observation(self) -> Any:
        mj = self._load_mujoco()
        np = self._load_numpy()
        joint_angles = np.asarray(
            self.data.qpos[self._joint_qpos_addresses], dtype=np.float64
        )
        position_error = np.asarray(
            self.data.sensordata[self._palm_position_slice]
            - self.data.sensordata[self._cube_position_slice],
            dtype=np.float64,
        )
        cube_quaternion = np.asarray(
            self.data.sensordata[self._cube_orientation_slice], dtype=np.float64
        )
        goal_quaternion = np.asarray(
            self.data.sensordata[self._goal_orientation_slice], dtype=np.float64
        )
        inverse_goal = np.empty(4, dtype=np.float64)
        mj.mju_negQuat(inverse_goal, goal_quaternion)
        quaternion_difference = np.empty(4, dtype=np.float64)
        mj.mju_mulQuat(quaternion_difference, cube_quaternion, inverse_goal)
        rotation_difference = np.empty(9, dtype=np.float64)
        mj.mju_quat2Mat(rotation_difference, quaternion_difference)
        observation = np.concatenate(
            (
                joint_angles,
                joint_angles - self._motor_targets,
                position_error,
                rotation_difference[3:],
                self._last_action,
            )
        ).astype(np.float32)
        if observation.shape != (_OBSERVATION_SIZE,) or not np.all(
            np.isfinite(observation)
        ):
            raise RuntimeError("LEAP policy assembled an invalid observation")
        return observation

    def _orientation_error(self) -> float:
        np = self._load_numpy()
        cube = np.asarray(
            self.data.sensordata[self._cube_orientation_slice], dtype=np.float64
        )
        goal = np.asarray(
            self.data.sensordata[self._goal_orientation_slice], dtype=np.float64
        )
        cube_norm = float(np.linalg.norm(cube))
        goal_norm = float(np.linalg.norm(goal))
        if cube_norm <= 0.0 or goal_norm <= 0.0:
            raise RuntimeError(
                "LEAP cube orientation sensor returned a zero quaternion"
            )
        cosine = float(np.dot(cube / cube_norm, goal / goal_norm))
        return 2.0 * math.acos(min(1.0, max(0.0, abs(cosine))))

    def get_cube_state(self) -> dict[str, Any]:
        """Return fresh public cube, goal, and palm-relative measurements."""

        self._resolve()
        np = self._load_numpy()
        palm_position = np.asarray(
            self.data.sensordata[self._palm_position_slice], dtype=float
        ).copy()
        cube_position = np.asarray(
            self.data.sensordata[self._cube_position_slice], dtype=float
        ).copy()
        return {
            "palm_position": palm_position,
            "cube_position": cube_position,
            "cube_orientation": np.asarray(
                self.data.sensordata[self._cube_orientation_slice], dtype=float
            ).copy(),
            "goal_orientation": np.asarray(
                self.data.sensordata[self._goal_orientation_slice], dtype=float
            ).copy(),
            "palm_cube_distance_m": float(
                np.linalg.norm(palm_position - cube_position)
            ),
            "orientation_error_rad": self._orientation_error(),
        }

    def track_orientation(
        self,
        duration: float = 10.0,
        *,
        stop_below_rad: float | None = None,
    ) -> dict[str, Any]:
        """Advance the policy toward the current Framework-owned goal."""

        self._resolve()
        np = self._load_numpy()
        duration_s = _positive(duration, "duration")
        if duration_s > self.spec.maximum_control_duration_s:
            raise ValueError("duration exceeds the retained policy bound")
        threshold = (
            None
            if stop_below_rad is None
            else _positive(stop_below_rad, "stop_below_rad")
        )
        if threshold is not None and threshold > math.pi:
            raise ValueError("stop_below_rad must not exceed pi")

        requested_steps = max(
            1, int(math.ceil(duration_s / float(self.model.opt.timestep)))
        )
        minimum_error = self._orientation_error()
        executed_steps = 0
        for _ in range(requested_steps):
            if (self._physics_tick + 1) % self._steps_per_action == 0:
                self._last_action = self._infer(self._observation())
                self._motor_targets = np.clip(
                    self._motor_targets
                    + self.spec.action_scale * self._last_action,
                    self._control_low,
                    self._control_high,
                )
                self._inference_count += 1
            self.data.ctrl[self._actuator_ids] = self._motor_targets
            self._load_mujoco().mj_step(self.model, self.data)
            self._physics_tick += 1
            executed_steps += 1
            current_error = self._orientation_error()
            minimum_error = min(minimum_error, current_error)
            if threshold is not None and current_error <= threshold:
                break

        if not np.all(np.isfinite(self.data.qpos)) or not np.all(
            np.isfinite(self.data.qvel)
        ):
            raise RuntimeError("LEAP policy produced non-finite MuJoCo state")
        state = self.get_cube_state()
        return {
            "requested_duration_s": duration_s,
            "physics_steps": executed_steps,
            "policy_inference_count": self._inference_count,
            "minimum_orientation_error_rad": minimum_error,
            "final_orientation_error_rad": state["orientation_error_rad"],
            "palm_cube_distance_m": state["palm_cube_distance_m"],
            "cube_position": state["cube_position"],
            "last_action": self._last_action.copy(),
            "last_joint_target": self._motor_targets.copy(),
        }


__all__ = [
    "LeapCubeReorientationSkeleton",
    "LeapCubeReorientationSpec",
    "POLICY_ARTIFACT_ID",
    "POLICY_SOURCE_REVISION",
]
