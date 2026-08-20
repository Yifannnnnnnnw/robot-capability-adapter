"""Task-neutral feedback primitives for the canonical Stretch 2 model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from autoadapter2.driver_synthesis import SessionBoundSkeleton


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


def _wrap_angle(value: float) -> float:
    return math.atan2(math.sin(value), math.cos(value))


class StretchUnreachableError(RuntimeError):
    """Raised when a target is outside the canonical side-arm workspace."""


@dataclass(frozen=True)
class StretchMobileManipulationSpec:
    """Public names and bounded feedback settings for Stretch 2."""

    base_body_name: str
    tool_body_name: str
    forward_actuator_name: str
    turn_actuator_name: str
    lift_actuator_name: str
    arm_actuator_name: str
    gripper_actuator_name: str
    lift_joint_name: str
    arm_joint_names: Sequence[str]
    gripper_joint_name: str
    expected_physics_timestep_s: float = 0.002
    turn_gain: float = 5.0
    drive_gain: float = 8.0
    maximum_drive_distance_m: float = 1.0
    maximum_control_steps: int = 6000
    settle_steps: int = 75

    def __post_init__(self) -> None:
        for field_name in (
            "base_body_name",
            "tool_body_name",
            "forward_actuator_name",
            "turn_actuator_name",
            "lift_actuator_name",
            "arm_actuator_name",
            "gripper_actuator_name",
            "lift_joint_name",
            "gripper_joint_name",
        ):
            object.__setattr__(
                self, field_name, _name(getattr(self, field_name), field_name)
            )
        arm_joints = tuple(
            _name(value, "arm_joint_names") for value in self.arm_joint_names
        )
        if len(arm_joints) != 4 or len(set(arm_joints)) != 4:
            raise ValueError("arm_joint_names must contain four unique values")
        object.__setattr__(self, "arm_joint_names", arm_joints)
        for field_name in (
            "expected_physics_timestep_s",
            "turn_gain",
            "drive_gain",
            "maximum_drive_distance_m",
        ):
            object.__setattr__(
                self, field_name, _positive(getattr(self, field_name), field_name)
            )
        for field_name in ("maximum_control_steps", "settle_steps"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")


class StretchMobileManipulationSkeleton(SessionBoundSkeleton):
    """Actuator-only base and side-arm feedback for one MuJoCo session."""

    def __init__(
        self,
        *,
        model: Any,
        data: Any,
        spec: StretchMobileManipulationSpec,
    ) -> None:
        if not isinstance(spec, StretchMobileManipulationSpec):
            raise TypeError("spec must be a StretchMobileManipulationSpec")
        super().__init__(model=model, data=data, spec=spec)
        self._mj: Any = None
        self._np: Any = None
        self._resolved = False

    def _load_numpy(self) -> Any:
        if self._np is None:
            try:
                import numpy as np
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("NumPy is required for Stretch control") from exc
            self._np = np
        return self._np

    def _load_mujoco(self) -> Any:
        if self._mj is None:
            try:
                import mujoco
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("MuJoCo is required for Stretch control") from exc
            self._mj = mujoco
        return self._mj

    def _id(self, object_type: Any, name: str) -> int:
        identifier = int(
            self._load_mujoco().mj_name2id(self.model, object_type, name)
        )
        if identifier < 0:
            raise ValueError(f"canonical Stretch model is missing {name!r}")
        return identifier

    def _resolve(self) -> None:
        if self._resolved:
            return
        mj = self._load_mujoco()
        np = self._load_numpy()
        if not math.isclose(
            float(self.model.opt.timestep),
            self.spec.expected_physics_timestep_s,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ):
            raise ValueError("canonical timestep does not match the Stretch skeleton")

        self._base_body_id = self._id(
            mj.mjtObj.mjOBJ_BODY, self.spec.base_body_name
        )
        self._tool_body_id = self._id(
            mj.mjtObj.mjOBJ_BODY, self.spec.tool_body_name
        )
        base_joint_id = int(self.model.body_jntadr[self._base_body_id])
        if base_joint_id < 0 or int(self.model.jnt_type[base_joint_id]) != int(
            mj.mjtJoint.mjJNT_FREE
        ):
            raise ValueError("Stretch base body must own a free joint")

        actuator_names = (
            self.spec.forward_actuator_name,
            self.spec.turn_actuator_name,
            self.spec.lift_actuator_name,
            self.spec.arm_actuator_name,
            self.spec.gripper_actuator_name,
        )
        actuator_ids = tuple(
            self._id(mj.mjtObj.mjOBJ_ACTUATOR, name) for name in actuator_names
        )
        self._forward_id, self._turn_id, self._lift_id, self._arm_id, self._grip_id = (
            actuator_ids
        )
        for name, actuator_id in zip(actuator_names, actuator_ids, strict=True):
            if not bool(self.model.actuator_ctrllimited[actuator_id]):
                raise ValueError(f"Stretch actuator {name!r} must be bounded")
            control_range = self.model.actuator_ctrlrange[actuator_id]
            if not np.all(np.isfinite(control_range)) or not (
                float(control_range[0]) < float(control_range[1])
            ):
                raise ValueError(f"Stretch actuator {name!r} has an invalid range")

        lift_joint_id = self._id(
            mj.mjtObj.mjOBJ_JOINT, self.spec.lift_joint_name
        )
        if int(self.model.jnt_type[lift_joint_id]) != int(mj.mjtJoint.mjJNT_SLIDE):
            raise ValueError("Stretch lift joint must be prismatic")
        self._lift_qpos_address = int(self.model.jnt_qposadr[lift_joint_id])

        arm_addresses: list[int] = []
        for name in self.spec.arm_joint_names:
            joint_id = self._id(mj.mjtObj.mjOBJ_JOINT, name)
            if int(self.model.jnt_type[joint_id]) != int(mj.mjtJoint.mjJNT_SLIDE):
                raise ValueError(f"Stretch arm joint {name!r} must be prismatic")
            arm_addresses.append(int(self.model.jnt_qposadr[joint_id]))
        self._arm_qpos_addresses = np.asarray(arm_addresses, dtype=np.int64)

        gripper_joint_id = self._id(
            mj.mjtObj.mjOBJ_JOINT, self.spec.gripper_joint_name
        )
        if int(self.model.jnt_type[gripper_joint_id]) != int(
            mj.mjtJoint.mjJNT_SLIDE
        ):
            raise ValueError("Stretch gripper joint must be prismatic")
        self._gripper_qpos_address = int(self.model.jnt_qposadr[gripper_joint_id])

        self._forward_range = np.asarray(
            self.model.actuator_ctrlrange[self._forward_id], dtype=float
        )
        self._turn_range = np.asarray(
            self.model.actuator_ctrlrange[self._turn_id], dtype=float
        )
        self._lift_range = np.asarray(
            self.model.actuator_ctrlrange[self._lift_id], dtype=float
        )
        self._arm_range = np.asarray(
            self.model.actuator_ctrlrange[self._arm_id], dtype=float
        )
        self._gripper_range = np.asarray(
            self.model.actuator_ctrlrange[self._grip_id], dtype=float
        )
        self._resolved = True

    def _base_rotation(self) -> Any:
        return self._load_numpy().asarray(
            self.data.xmat[self._base_body_id], dtype=float
        ).reshape(3, 3)

    def _base_yaw(self) -> float:
        rotation = self._base_rotation()
        return math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))

    def _clip(self, value: float, control_range: Any) -> float:
        return float(min(float(control_range[1]), max(float(control_range[0]), value)))

    def _stop_base(self) -> None:
        self.data.ctrl[self._forward_id] = 0.0
        self.data.ctrl[self._turn_id] = 0.0

    def _settle(self) -> None:
        self._stop_base()
        for _ in range(self.spec.settle_steps):
            self._load_mujoco().mj_step(self.model, self.data)

    def _assert_finite_and_upright(self) -> None:
        np = self._load_numpy()
        if not np.all(np.isfinite(self.data.qpos)) or not np.all(
            np.isfinite(self.data.qvel)
        ):
            raise RuntimeError("Stretch control produced non-finite MuJoCo state")
        if float(self._base_rotation()[2, 2]) < 0.95:
            raise RuntimeError("Stretch base is no longer upright")

    def get_state(self) -> dict[str, Any]:
        """Return fresh public base and tool state."""

        self._resolve()
        np = self._load_numpy()
        return {
            "base_position": np.asarray(
                self.data.xpos[self._base_body_id], dtype=float
            ).copy(),
            "base_yaw_rad": self._base_yaw(),
            "tool_position": np.asarray(
                self.data.xpos[self._tool_body_id], dtype=float
            ).copy(),
            "lift_position_m": float(self.data.qpos[self._lift_qpos_address]),
            "arm_extension_m": float(
                np.sum(self.data.qpos[self._arm_qpos_addresses])
            ),
            "gripper_position_m": float(
                self.data.qpos[self._gripper_qpos_address]
            ),
        }

    def turn_to_yaw(
        self,
        target_yaw_rad: float,
        *,
        tolerance_rad: float = 0.01,
        maximum_steps: int | None = None,
    ) -> dict[str, Any]:
        """Turn the mobile base to one absolute world yaw."""

        self._resolve()
        target = _wrap_angle(_finite(target_yaw_rad, "target_yaw_rad"))
        tolerance = _positive(tolerance_rad, "tolerance_rad")
        limit = self._step_limit(maximum_steps)
        dwell = 0
        for step in range(1, limit + 1):
            error = _wrap_angle(target - self._base_yaw())
            self.data.ctrl[self._forward_id] = 0.0
            self.data.ctrl[self._turn_id] = self._clip(
                -self.spec.turn_gain * error, self._turn_range
            )
            self._load_mujoco().mj_step(self.model, self.data)
            dwell = dwell + 1 if abs(error) <= tolerance else 0
            if dwell >= 20:
                break
        else:
            self._stop_base()
            raise RuntimeError("Stretch base did not converge to the requested yaw")
        self._settle()
        self._assert_finite_and_upright()
        final_error = abs(_wrap_angle(target - self._base_yaw()))
        if final_error > max(2.0 * tolerance, 0.02):
            raise RuntimeError("Stretch base yaw drifted after settling")
        return {
            "physics_steps": step + self.spec.settle_steps,
            "target_yaw_rad": target,
            "final_yaw_rad": self._base_yaw(),
            "final_error_rad": final_error,
        }

    def drive_distance(
        self,
        distance_m: float,
        *,
        tolerance_m: float = 0.01,
        maximum_steps: int | None = None,
    ) -> dict[str, Any]:
        """Drive a bounded signed distance along the current forward axis."""

        self._resolve()
        np = self._load_numpy()
        distance = _finite(distance_m, "distance_m")
        if abs(distance) > self.spec.maximum_drive_distance_m:
            raise ValueError("distance exceeds the Stretch skeleton bound")
        tolerance = _positive(tolerance_m, "tolerance_m")
        limit = self._step_limit(maximum_steps)
        start_position = np.asarray(
            self.data.xpos[self._base_body_id], dtype=float
        ).copy()
        start_yaw = self._base_yaw()
        forward_axis = self._base_rotation() @ np.asarray((-1.0, 0.0, 0.0))
        dwell = 0
        for step in range(1, limit + 1):
            displacement = np.asarray(
                self.data.xpos[self._base_body_id], dtype=float
            ) - start_position
            progress = float(np.dot(displacement, forward_axis))
            distance_error = distance - progress
            yaw_error = _wrap_angle(start_yaw - self._base_yaw())
            self.data.ctrl[self._forward_id] = self._clip(
                self.spec.drive_gain * distance_error, self._forward_range
            )
            self.data.ctrl[self._turn_id] = self._clip(
                -self.spec.turn_gain * yaw_error, self._turn_range
            )
            self._load_mujoco().mj_step(self.model, self.data)
            dwell = (
                dwell + 1
                if abs(distance_error) <= tolerance and abs(yaw_error) <= 0.02
                else 0
            )
            if dwell >= 20:
                break
        else:
            self._stop_base()
            raise RuntimeError(
                "Stretch base did not converge to the requested distance"
            )
        self._settle()
        self._assert_finite_and_upright()
        displacement = np.asarray(
            self.data.xpos[self._base_body_id], dtype=float
        ) - start_position
        progress = float(np.dot(displacement, forward_axis))
        final_error = abs(distance - progress)
        if final_error > max(2.0 * tolerance, 0.02):
            raise RuntimeError("Stretch base distance drifted after settling")
        return {
            "physics_steps": step + self.spec.settle_steps,
            "requested_distance_m": distance,
            "travelled_distance_m": progress,
            "final_error_m": final_error,
        }

    def set_gripper(
        self,
        position_m: float,
        *,
        tolerance_m: float = 0.002,
        maximum_steps: int | None = None,
    ) -> dict[str, Any]:
        """Move the gripper slide to a bounded position target."""

        self._resolve()
        target = _finite(position_m, "position_m")
        if target < self._gripper_range[0] or target > self._gripper_range[1]:
            raise ValueError("gripper target is outside the canonical range")
        tolerance = _positive(tolerance_m, "tolerance_m")
        limit = self._step_limit(maximum_steps)
        self._stop_base()
        dwell = 0
        for step in range(1, limit + 1):
            self.data.ctrl[self._grip_id] = target
            self._load_mujoco().mj_step(self.model, self.data)
            error = target - float(self.data.qpos[self._gripper_qpos_address])
            dwell = dwell + 1 if abs(error) <= tolerance else 0
            if dwell >= 20:
                break
        else:
            raise RuntimeError("Stretch gripper did not converge")
        self._assert_finite_and_upright()
        return {
            "physics_steps": step,
            "target_position_m": target,
            "final_position_m": float(
                self.data.qpos[self._gripper_qpos_address]
            ),
        }

    def move_tool_to_position(
        self,
        target_position: Sequence[float],
        *,
        tolerance_m: float = 0.01,
        gripper_position_m: float | None = None,
        maximum_steps: int | None = None,
    ) -> dict[str, Any]:
        """Reach one world point with feedback from the side-arm geometry."""

        self._resolve()
        np = self._load_numpy()
        target = np.asarray(target_position, dtype=float)
        if target.shape != (3,) or not np.all(np.isfinite(target)):
            raise ValueError("target_position must be a finite 3-vector")
        tolerance = _positive(tolerance_m, "tolerance_m")
        limit = self._step_limit(maximum_steps)
        if gripper_position_m is not None:
            gripper_target = _finite(gripper_position_m, "gripper_position_m")
            if (
                gripper_target < self._gripper_range[0]
                or gripper_target > self._gripper_range[1]
            ):
                raise ValueError("gripper target is outside the canonical range")
            self.data.ctrl[self._grip_id] = gripper_target

        dwell = 0
        minimum_error = math.inf
        for step in range(1, limit + 1):
            rotation = self._base_rotation()
            base_position = np.asarray(
                self.data.xpos[self._base_body_id], dtype=float
            )
            tool_position = np.asarray(
                self.data.xpos[self._tool_body_id], dtype=float
            )
            local_tool = rotation.T @ (tool_position - base_position)
            extension = float(
                np.sum(self.data.qpos[self._arm_qpos_addresses])
            )
            lateral_offset = float(local_tool[0])
            extension_origin = float(local_tool[1]) + extension
            lift_origin = (
                float(local_tool[2])
                - float(self.data.qpos[self._lift_qpos_address])
            )

            planar_delta = target[:2] - base_position[:2]
            radius = float(np.linalg.norm(planar_delta))
            if radius <= abs(lateral_offset) + 1.0e-6:
                raise StretchUnreachableError(
                    "target is too close to the Stretch side-arm axis"
                )
            desired_local_y = -math.sqrt(
                max(0.0, radius * radius - lateral_offset * lateral_offset)
            )
            desired_extension = extension_origin - desired_local_y
            local_target = rotation.T @ (target - base_position)
            desired_lift = float(local_target[2]) - lift_origin
            if not (
                self._arm_range[0] <= desired_extension <= self._arm_range[1]
            ):
                raise StretchUnreachableError(
                    "target requires arm extension outside the canonical range"
                )
            if not self._lift_range[0] <= desired_lift <= self._lift_range[1]:
                raise StretchUnreachableError(
                    "target requires lift position outside the canonical range"
                )

            world_angle = math.atan2(float(planar_delta[1]), float(planar_delta[0]))
            local_angle = math.atan2(desired_local_y, lateral_offset)
            desired_yaw = _wrap_angle(world_angle - local_angle)
            yaw_error = _wrap_angle(desired_yaw - self._base_yaw())
            self.data.ctrl[self._forward_id] = 0.0
            self.data.ctrl[self._turn_id] = self._clip(
                -self.spec.turn_gain * yaw_error, self._turn_range
            )
            self.data.ctrl[self._lift_id] = desired_lift
            self.data.ctrl[self._arm_id] = desired_extension
            self._load_mujoco().mj_step(self.model, self.data)

            error = float(
                np.linalg.norm(
                    target
                    - np.asarray(
                        self.data.xpos[self._tool_body_id], dtype=float
                    )
                )
            )
            minimum_error = min(minimum_error, error)
            dwell = (
                dwell + 1 if error <= tolerance and abs(yaw_error) <= 0.015 else 0
            )
            if dwell >= 20:
                break
        else:
            self._stop_base()
            raise RuntimeError("Stretch tool did not converge to the requested point")

        self._settle()
        self._assert_finite_and_upright()
        final_position = np.asarray(
            self.data.xpos[self._tool_body_id], dtype=float
        ).copy()
        final_error = float(np.linalg.norm(target - final_position))
        if final_error > max(2.0 * tolerance, 0.025):
            raise RuntimeError("Stretch tool target drifted after settling")
        return {
            "physics_steps": step + self.spec.settle_steps,
            "target_position": target.copy(),
            "final_position": final_position,
            "minimum_error_m": minimum_error,
            "final_error_m": final_error,
        }

    def _step_limit(self, value: int | None) -> int:
        if value is None:
            return self.spec.maximum_control_steps
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("maximum_steps must be an integer")
        if value <= 0 or value > self.spec.maximum_control_steps:
            raise ValueError("maximum_steps is outside the skeleton bound")
        return value


__all__ = [
    "StretchMobileManipulationSkeleton",
    "StretchMobileManipulationSpec",
    "StretchUnreachableError",
]
