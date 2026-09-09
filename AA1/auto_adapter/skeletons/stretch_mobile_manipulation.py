# SPDX-License-Identifier: Apache-2.0
"""Bounded Direct-MuJoCo control for the canonical Stretch 2 model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from .base import SkeletonBase


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _positive(value: float, name: str) -> float:
    value = _finite(value, name)
    if value <= 0.0:
        raise ValueError(f"{name} must be positive")
    return value


def _name(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _wrap(value: float) -> float:
    return math.atan2(math.sin(value), math.cos(value))


class StretchUnreachableError(RuntimeError):
    """Raised when a target is outside the bounded side-arm workspace."""


@dataclass(frozen=True)
class StretchMobileManipulationSpec:
    """Canonical names plus the small feedback bounds used by Stretch."""

    base_body_name: str = "base_link"
    tool_body_name: str = "link_gripper_slider"
    forward_actuator_name: str = "forward"
    turn_actuator_name: str = "turn"
    lift_actuator_name: str = "lift"
    arm_actuator_name: str = "arm_extend"
    wrist_actuator_name: str = "wrist_yaw"
    gripper_actuator_name: str = "grip"
    lift_joint_name: str = "joint_lift"
    arm_joint_names: Sequence[str] = (
        "joint_arm_l3",
        "joint_arm_l2",
        "joint_arm_l1",
        "joint_arm_l0",
    )
    wrist_joint_name: str = "joint_wrist_yaw"
    gripper_joint_name: str = "joint_gripper_slide"
    expected_physics_timestep_s: float = 0.002
    turn_gain: float = 5.0
    drive_gain: float = 8.0
    maximum_drive_distance_m: float = 1.0
    maximum_control_steps: int = 6000
    settle_steps: int = 75

    def __post_init__(self) -> None:
        for field in (
            "base_body_name",
            "tool_body_name",
            "forward_actuator_name",
            "turn_actuator_name",
            "lift_actuator_name",
            "arm_actuator_name",
            "wrist_actuator_name",
            "gripper_actuator_name",
            "lift_joint_name",
            "wrist_joint_name",
            "gripper_joint_name",
        ):
            object.__setattr__(self, field, _name(getattr(self, field), field))
        try:
            joints = tuple(_name(value, "arm_joint_names") for value in self.arm_joint_names)
        except TypeError as exc:
            raise ValueError("arm_joint_names must contain four names") from exc
        if len(joints) != 4 or len(set(joints)) != 4:
            raise ValueError("arm_joint_names must contain four unique values")
        object.__setattr__(self, "arm_joint_names", joints)
        for field in (
            "expected_physics_timestep_s",
            "turn_gain",
            "drive_gain",
            "maximum_drive_distance_m",
        ):
            object.__setattr__(self, field, _positive(getattr(self, field), field))
        for field in ("maximum_control_steps", "settle_steps"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field} must be a positive integer")


class StretchMobileManipulationSkeleton(SkeletonBase):
    """Model-specific wheel, lift, coupled-extension, and gripper feedback."""

    def __init__(self, model: Any, data: Any, spec: StretchMobileManipulationSpec) -> None:
        if not isinstance(spec, StretchMobileManipulationSpec):
            raise TypeError("spec must be a StretchMobileManipulationSpec")
        super().__init__(model, data, spec)
        self.spec: StretchMobileManipulationSpec = spec
        self._resolved = False
        self._resolve()

    def _id(self, object_type: Any, name: str) -> int:
        identifier = int(self._mj.mj_name2id(self.model, object_type, name))
        if identifier < 0:
            raise ValueError(f"canonical Stretch model is missing {name!r}")
        return identifier

    def _resolve(self) -> None:
        if self._resolved:
            return
        mj = self._mj
        if not math.isclose(
            float(self.model.opt.timestep),
            self.spec.expected_physics_timestep_s,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ):
            raise ValueError("canonical timestep does not match the Stretch skeleton")

        self._base_id = self._id(mj.mjtObj.mjOBJ_BODY, self.spec.base_body_name)
        self._tool_id = self._id(mj.mjtObj.mjOBJ_BODY, self.spec.tool_body_name)
        base_joint = int(self.model.body_jntadr[self._base_id])
        if base_joint < 0 or int(self.model.jnt_type[base_joint]) != int(mj.mjtJoint.mjJNT_FREE):
            raise ValueError("Stretch base body must own a free joint")

        names = (
            self.spec.forward_actuator_name,
            self.spec.turn_actuator_name,
            self.spec.lift_actuator_name,
            self.spec.arm_actuator_name,
            self.spec.wrist_actuator_name,
            self.spec.gripper_actuator_name,
        )
        ids = tuple(self._id(mj.mjtObj.mjOBJ_ACTUATOR, name) for name in names)
        self._forward_id, self._turn_id, self._lift_id, self._arm_id, self._wrist_id, self._grip_id = ids
        for name, aid in zip(names, ids, strict=True):
            control_range = self.model.actuator_ctrlrange[aid]
            if not bool(self.model.actuator_ctrllimited[aid]) or not (
                np.all(np.isfinite(control_range)) and float(control_range[0]) < float(control_range[1])
            ):
                raise ValueError(f"Stretch actuator {name!r} must have a finite bounded range")

        tendon_type = int(mj.mjtTrn.mjTRN_TENDON)
        for name, aid in (
            (self.spec.forward_actuator_name, self._forward_id),
            (self.spec.turn_actuator_name, self._turn_id),
            (self.spec.arm_actuator_name, self._arm_id),
        ):
            if int(self.model.actuator_trntype[aid]) != tendon_type:
                raise ValueError(f"Stretch actuator {name!r} must be tendon driven")

        self._lift_qpos = self._joint_qpos(self.spec.lift_joint_name, mj.mjtJoint.mjJNT_SLIDE)
        self._arm_qpos = np.asarray(
            [self._joint_qpos(name, mj.mjtJoint.mjJNT_SLIDE) for name in self.spec.arm_joint_names],
            dtype=np.int64,
        )
        self._wrist_qpos = self._joint_qpos(self.spec.wrist_joint_name, mj.mjtJoint.mjJNT_HINGE)
        self._grip_qpos = self._joint_qpos(self.spec.gripper_joint_name, mj.mjtJoint.mjJNT_SLIDE)
        self._forward_range = np.asarray(self.model.actuator_ctrlrange[self._forward_id], dtype=float)
        self._turn_range = np.asarray(self.model.actuator_ctrlrange[self._turn_id], dtype=float)
        self._lift_range = np.asarray(self.model.actuator_ctrlrange[self._lift_id], dtype=float)
        self._arm_range = np.asarray(self.model.actuator_ctrlrange[self._arm_id], dtype=float)
        self._wrist_range = np.asarray(self.model.actuator_ctrlrange[self._wrist_id], dtype=float)
        self._grip_range = np.asarray(self.model.actuator_ctrlrange[self._grip_id], dtype=float)
        self._resolved = True

    def _joint_qpos(self, name: str, expected_type: Any) -> int:
        joint_id = self._id(self._mj.mjtObj.mjOBJ_JOINT, name)
        if int(self.model.jnt_type[joint_id]) != int(expected_type):
            raise ValueError(f"Stretch joint {name!r} has the wrong type")
        return int(self.model.jnt_qposadr[joint_id])

    def _clip(self, value: float, control_range: np.ndarray) -> float:
        return float(np.clip(value, control_range[0], control_range[1]))

    def _steps(self, duration: float) -> int:
        return min(
            self.spec.maximum_control_steps,
            max(1, int(math.ceil(_positive(duration, "duration") / self.model.opt.timestep))),
        )

    def _base_position(self) -> np.ndarray:
        return np.asarray(self.data.xpos[self._base_id], dtype=float).copy()

    def _base_rotation(self) -> np.ndarray:
        return np.asarray(self.data.xmat[self._base_id], dtype=float).reshape(3, 3)

    def _base_yaw(self) -> float:
        rotation = self._base_rotation()
        return math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))

    def _stop_base(self) -> None:
        self.data.ctrl[self._forward_id] = 0.0
        self.data.ctrl[self._turn_id] = 0.0

    def _settle(self) -> None:
        self._stop_base()
        for _ in range(self.spec.settle_steps):
            self.step(1)

    def _assert_safe_state(self) -> None:
        if not np.all(np.isfinite(self.data.qpos)) or not np.all(np.isfinite(self.data.qvel)):
            raise RuntimeError("Stretch control produced non-finite MuJoCo state")
        if float(self._base_rotation()[2, 2]) < 0.95:
            raise RuntimeError("Stretch base is no longer upright")

    def get_base_pose(self) -> tuple[np.ndarray, np.ndarray]:
        self._resolve()
        self._mj.mj_forward(self.model, self.data)
        return self._base_position(), self._base_rotation().copy()

    def get_base_yaw(self) -> float:
        self._resolve()
        self._mj.mj_forward(self.model, self.data)
        return self._base_yaw()

    def get_ee_pose(self) -> tuple[np.ndarray, np.ndarray]:
        self._resolve()
        self._mj.mj_forward(self.model, self.data)
        return (
            np.asarray(self.data.xpos[self._tool_id], dtype=float).copy(),
            np.asarray(self.data.xmat[self._tool_id], dtype=float).reshape(3, 3).copy(),
        )

    def turn(self, angle_rad: float, speed: float = 0.3) -> dict[str, Any]:
        """Turn by a signed relative yaw using the turn tendon."""

        self._resolve()
        target = _wrap(self._base_yaw() + _finite(angle_rad, "angle_rad"))
        return self._turn_to_yaw(target, speed=_positive(speed, "speed"))

    def _turn_to_yaw(self, target: float, *, speed: float, hold_arm: bool = False) -> dict[str, Any]:
        if hold_arm:
            self._hold_arm()
        dwell = 0
        for step in range(1, self.spec.maximum_control_steps + 1):
            error = _wrap(target - self._base_yaw())
            self.data.ctrl[self._forward_id] = 0.0
            command = -math.copysign(min(speed, self.spec.turn_gain * abs(error)), error) if error else 0.0
            self.data.ctrl[self._turn_id] = self._clip(command, self._turn_range)
            self.step(1)
            dwell = dwell + 1 if abs(error) <= 0.01 else 0
            if dwell >= 20:
                break
        else:
            self._stop_base()
            raise RuntimeError("Stretch base did not converge to the requested yaw")
        self._settle()
        self._assert_safe_state()
        final_error = abs(_wrap(target - self._base_yaw()))
        if final_error > 0.02:
            raise RuntimeError("Stretch base yaw drifted after settling")
        return {
            "physics_steps": step + self.spec.settle_steps,
            "target_yaw_rad": target,
            "final_yaw_rad": self._base_yaw(),
            "final_error_rad": final_error,
        }

    def drive_forward(self, distance_m: float, speed: float = 0.1) -> dict[str, Any]:
        """Drive a signed distance along the current Stretch forward axis."""

        self._resolve()
        distance = _finite(distance_m, "distance_m")
        if abs(distance) > self.spec.maximum_drive_distance_m:
            raise ValueError("distance exceeds the Stretch skeleton bound")
        speed = _positive(speed, "speed")
        start = self._base_position()
        yaw = self._base_yaw()
        forward = self._base_rotation() @ np.asarray((-1.0, 0.0, 0.0))
        if abs(distance) <= 0.01:
            self._settle()
            self._assert_safe_state()
            return {"physics_steps": self.spec.settle_steps, "travelled_distance_m": 0.0}

        dwell = 0
        for step in range(1, self.spec.maximum_control_steps + 1):
            progress = float(np.dot(self._base_position() - start, forward))
            error = distance - progress
            yaw_error = _wrap(yaw - self._base_yaw())
            command = math.copysign(min(speed, self.spec.drive_gain * abs(error)), error) if error else 0.0
            self.data.ctrl[self._forward_id] = self._clip(command, self._forward_range)
            self.data.ctrl[self._turn_id] = self._clip(-self.spec.turn_gain * yaw_error, self._turn_range)
            self.step(1)
            dwell = dwell + 1 if abs(error) <= 0.01 and abs(yaw_error) <= 0.02 else 0
            if dwell >= 20:
                break
        else:
            self._stop_base()
            raise RuntimeError("Stretch base did not converge to the requested distance")

        self._settle()
        self._assert_safe_state()
        travelled = float(np.dot(self._base_position() - start, forward))
        if abs(distance - travelled) > 0.02:
            raise RuntimeError("Stretch base distance drifted after settling")
        return {
            "physics_steps": step + self.spec.settle_steps,
            "requested_distance_m": distance,
            "travelled_distance_m": travelled,
            "final_error_m": abs(distance - travelled),
        }

    def _arm_geometry(self, target: np.ndarray) -> tuple[float, float, float]:
        rotation = self._base_rotation()
        base = self._base_position()
        local_tool = rotation.T @ (
            np.asarray(self.data.xpos[self._tool_id], dtype=float) - base
        )
        extension = float(np.sum(self.data.qpos[self._arm_qpos]))
        lateral = float(local_tool[0])
        extension_origin = float(local_tool[1]) + extension
        lift_origin = float(local_tool[2]) - float(self.data.qpos[self._lift_qpos])
        planar = target[:2] - base[:2]
        radius = float(np.linalg.norm(planar))
        if radius <= abs(lateral) + 1.0e-6:
            raise StretchUnreachableError("target is too close to the Stretch side-arm axis")
        local_y = -math.sqrt(max(0.0, radius * radius - lateral * lateral))
        extension_target = extension_origin - local_y
        lift_target = float((rotation.T @ (target - base))[2]) - lift_origin
        yaw_target = _wrap(
            math.atan2(float(planar[1]), float(planar[0]))
            - math.atan2(local_y, lateral)
        )
        return yaw_target, lift_target, extension_target

    def _hold_arm(self) -> None:
        self.data.ctrl[self._lift_id] = self._clip(
            float(self.data.qpos[self._lift_qpos]), self._lift_range
        )
        self.data.ctrl[self._arm_id] = self._clip(
            float(np.sum(self.data.qpos[self._arm_qpos])), self._arm_range
        )

    def move_cartesian(self, target_xyz: Sequence[float], duration: float = 2.0) -> dict[str, Any]:
        """Reach a fixed world point with sequential base-yaw and arm feedback."""

        self._resolve()
        target = np.asarray(target_xyz, dtype=float)
        if target.shape != (3,) or not np.all(np.isfinite(target)):
            raise ValueError("target_xyz must be a finite 3-vector")
        start_time = float(self.data.time)
        steps = self._steps(duration)
        yaw_target, _, _ = self._arm_geometry(target)
        self._turn_to_yaw(yaw_target, speed=0.3, hold_arm=True)
        _, lift_target, extension_target = self._arm_geometry(target)
        if not self._lift_range[0] <= lift_target <= self._lift_range[1]:
            raise StretchUnreachableError("target requires lift outside the canonical range")
        if not self._arm_range[0] <= extension_target <= self._arm_range[1]:
            raise StretchUnreachableError("target requires coupled extension outside the canonical range")

        dwell = 0
        minimum_error = math.inf
        for step in range(1, steps + 1):
            self.data.ctrl[self._lift_id] = self._clip(lift_target, self._lift_range)
            self.data.ctrl[self._arm_id] = self._clip(extension_target, self._arm_range)
            self.data.ctrl[self._forward_id] = 0.0
            self.data.ctrl[self._turn_id] = 0.0
            self.step(1)
            error = float(np.linalg.norm(target - self.data.xpos[self._tool_id]))
            minimum_error = min(minimum_error, error)
            dwell = dwell + 1 if error <= 0.01 and abs(_wrap(self._base_yaw() - yaw_target)) <= 0.02 else 0
            if dwell >= 20:
                break
        else:
            self._stop_base()
            raise RuntimeError("Stretch tool did not converge to the requested point")

        self._settle()
        self._assert_safe_state()
        final = np.asarray(self.data.xpos[self._tool_id], dtype=float).copy()
        final_error = float(np.linalg.norm(target - final))
        if final_error > 0.025:
            raise RuntimeError("Stretch tool target drifted after settling")
        return {
            "physics_steps": int(round((float(self.data.time) - start_time) / self.model.opt.timestep)),
            "target_position": target.copy(),
            "final_position": final,
            "minimum_error_m": minimum_error,
            "final_error_m": final_error,
        }

    def home(self, duration: float = 1.0) -> bool:
        """Return lift, coupled extension, and wrist to the model home pose."""

        self._resolve()
        steps = self._steps(duration)
        targets = (
            (self._lift_id, float(self.model.qpos0[self._lift_qpos]), self._lift_range),
            (self._arm_id, float(np.sum(self.model.qpos0[self._arm_qpos])), self._arm_range),
            (self._wrist_id, float(self.model.qpos0[self._wrist_qpos]), self._wrist_range),
        )
        starts = {aid: float(self.data.ctrl[aid]) for aid, _, _ in targets}
        for step in range(1, steps + 1):
            alpha = step / steps
            for aid, target, control_range in targets:
                self.data.ctrl[aid] = self._clip(
                    starts[aid] + alpha * (target - starts[aid]), control_range
                )
            self.data.ctrl[self._forward_id] = 0.0
            self.data.ctrl[self._turn_id] = 0.0
            self.step(1)
        self._settle()
        self._assert_safe_state()
        return True

    def _set_gripper(self, target: float) -> bool:
        self.data.ctrl[self._grip_id] = self._clip(target, self._grip_range)
        for _ in range(self.spec.settle_steps):
            self.step(1)
        self._assert_safe_state()
        return True

    def gripper_open(self) -> bool:
        self._resolve()
        return self._set_gripper(float(self._grip_range[1]))

    def gripper_close(self) -> bool:
        self._resolve()
        return self._set_gripper(float(self._grip_range[0]))

    def describe(self) -> dict[str, Any]:
        self._resolve()
        return {
            "robot": "hello_robot_stretch_2",
            "base_body": self.spec.base_body_name,
            "tool_body": self.spec.tool_body_name,
            "base_actuators": [self.spec.forward_actuator_name, self.spec.turn_actuator_name],
            "arm_actuators": [
                self.spec.lift_actuator_name,
                self.spec.arm_actuator_name,
                self.spec.wrist_actuator_name,
                self.spec.gripper_actuator_name,
            ],
            "arm_joints": list(self.spec.arm_joint_names),
            "coupled_arm_extension": True,
            "physics_timestep_s": float(self.model.opt.timestep),
        }


__all__ = [
    "StretchMobileManipulationSkeleton",
    "StretchMobileManipulationSpec",
    "StretchUnreachableError",
]
