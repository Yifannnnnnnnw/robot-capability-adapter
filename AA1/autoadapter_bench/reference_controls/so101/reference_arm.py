"""Standalone SO-101 positive control for capability calibration.

This module is private reference evidence.  It binds the independent AA1
capability scene and produces motion only by writing the scene's native
position actuator controls before ``mujoco.mj_step``.  Position IK uses a
separate ``MjData`` scratch state and never restores live ``qpos``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import mujoco
import numpy as np


ARM_JOINT_NAMES = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
)
ARM_ACTUATOR_NAMES = (
    "act_shoulder_pan",
    "act_shoulder_lift",
    "act_elbow_flex",
    "act_wrist_flex",
    "act_wrist_roll",
)
GRIPPER_JOINT_NAME = "jaw_visual_joint"
GRIPPER_ACTUATOR_NAME = "act_jaw_visual"
GRIPPER_CLOSED_POSITION = -0.175
GRIPPER_OPEN_POSITION = 1.75
EE_SITE_NAME = "ee_site"
BASE_BODY_NAME = "base_link"
TCP_GEOM_NAME = "so101_tcp_contact"
TARGET_GEOM_NAME = "fixed_contact_geom"


def _finite_vector(value: Any, *, name: str, size: int = 3) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if result.shape != (size,) or not np.isfinite(result).all():
        raise ValueError(f"{name} must be a finite {size}-vector")
    return result


def _finite_number(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _duration(
    value: Any,
    *,
    name: str,
    lower: float = 0.25,
    upper: float = 8.0,
) -> float:
    result = _finite_number(value, name=name)
    if not lower <= result <= upper:
        raise ValueError(f"{name} must be in [{lower}, {upper}]")
    return result


class ReferenceSO101Driver:
    """Small model-bound native-actuator controller used for calibration."""

    def __init__(self, *, model: Any, data: Any) -> None:
        self.model = model
        self.data = data
        self._ee_site_id = self._id(mujoco.mjtObj.mjOBJ_SITE, EE_SITE_NAME)
        self._base_body_id = self._id(mujoco.mjtObj.mjOBJ_BODY, BASE_BODY_NAME)
        self._tcp_geom_id = self._id(mujoco.mjtObj.mjOBJ_GEOM, TCP_GEOM_NAME)
        self._target_geom_id = self._id(mujoco.mjtObj.mjOBJ_GEOM, TARGET_GEOM_NAME)
        self._joint_ids = [
            self._id(mujoco.mjtObj.mjOBJ_JOINT, name) for name in ARM_JOINT_NAMES
        ]
        self._qpos_addresses = [
            int(model.jnt_qposadr[joint_id]) for joint_id in self._joint_ids
        ]
        self._qvel_addresses = [
            int(model.jnt_dofadr[joint_id]) for joint_id in self._joint_ids
        ]
        self._actuator_ids = [
            self._id(mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            for name in ARM_ACTUATOR_NAMES
        ]
        self._gripper_joint_id = self._id(
            mujoco.mjtObj.mjOBJ_JOINT, GRIPPER_JOINT_NAME
        )
        self._gripper_id = self._id(
            mujoco.mjtObj.mjOBJ_ACTUATOR, GRIPPER_ACTUATOR_NAME
        )
        self._gripper_qpos_address = int(model.jnt_qposadr[self._gripper_joint_id])
        self._lower = np.asarray(
            [model.jnt_range[joint_id, 0] for joint_id in self._joint_ids],
            dtype=float,
        )
        self._upper = np.asarray(
            [model.jnt_range[joint_id, 1] for joint_id in self._joint_ids],
            dtype=float,
        )
        self._timestep = float(model.opt.timestep)
        if self._timestep <= 0.0:
            raise ValueError("canonical model timestep must be positive")
        for actuator_id, joint_id in zip(self._actuator_ids, self._joint_ids):
            if int(model.actuator_trnid[actuator_id, 0]) != joint_id:
                raise ValueError("arm actuator transmission does not match its joint")
        if int(model.actuator_trnid[self._gripper_id, 0]) != self._gripper_joint_id:
            raise ValueError("gripper actuator transmission does not match its joint")
        self._arm_target = self._current_arm_q()
        self._gripper_target = float(
            np.clip(
                data.ctrl[self._gripper_id],
                GRIPPER_CLOSED_POSITION,
                GRIPPER_OPEN_POSITION,
            )
        )

    def _sync_targets_from_live_state(self) -> None:
        """Refresh controller targets after the harness resets this MjData."""

        self._arm_target = self._current_arm_q()
        self._gripper_target = float(
            np.clip(
                self.data.ctrl[self._gripper_id],
                GRIPPER_CLOSED_POSITION,
                GRIPPER_OPEN_POSITION,
            )
        )

    def _id(self, object_type: Any, name: str) -> int:
        identifier = int(mujoco.mj_name2id(self.model, object_type, name))
        if identifier < 0:
            raise ValueError(f"canonical SO-101 capability scene is missing {name!r}")
        return identifier

    def _current_arm_q(self) -> np.ndarray:
        return np.asarray(
            [self.data.qpos[address] for address in self._qpos_addresses], dtype=float
        )

    def _ee_position(self) -> np.ndarray:
        return np.asarray(self.data.site_xpos[self._ee_site_id], dtype=float).copy()

    def _set_arm_target(self, target: Sequence[float]) -> None:
        target_array = np.asarray(target, dtype=float)
        if target_array.shape != (len(self._actuator_ids),) or not np.isfinite(
            target_array
        ).all():
            raise ValueError("arm target must contain five finite joint positions")
        target_array = np.clip(target_array, self._lower, self._upper)
        self._arm_target = target_array.copy()
        for actuator_id, value in zip(self._actuator_ids, target_array):
            self.data.ctrl[actuator_id] = float(value)

    def _set_gripper_target(self, target: float) -> None:
        target = float(
            np.clip(target, GRIPPER_CLOSED_POSITION, GRIPPER_OPEN_POSITION)
        )
        self._gripper_target = target
        self.data.ctrl[self._gripper_id] = target

    def _hold_targets(self) -> None:
        self._set_arm_target(self._arm_target)
        self._set_gripper_target(self._gripper_target)

    def _steps(self, duration_s: float) -> int:
        return max(1, int(math.ceil(duration_s / self._timestep - 1.0e-12)))

    def _hold(self, duration_s: float) -> None:
        for _ in range(self._steps(duration_s)):
            self._hold_targets()
            mujoco.mj_step(self.model, self.data)

    def _solve_position_ik(self, target: np.ndarray) -> np.ndarray:
        """Return a bounded arm solution from an independent MuJoCo state."""

        target = _finite_vector(target, name="target_position")
        scratch = mujoco.MjData(self.model)
        scratch.qpos[:] = self.data.qpos
        scratch.qvel[:] = self.data.qvel
        if self.model.nmocap:
            scratch.mocap_pos[:] = self.data.mocap_pos
            scratch.mocap_quat[:] = self.data.mocap_quat
        candidate = self._current_arm_q()
        best = candidate.copy()
        best_error = float("inf")
        for _ in range(120):
            for address, value in zip(self._qpos_addresses, candidate):
                scratch.qpos[address] = float(value)
            mujoco.mj_forward(self.model, scratch)
            error = target - np.asarray(scratch.site_xpos[self._ee_site_id], dtype=float)
            error_norm = float(np.linalg.norm(error))
            if error_norm < best_error:
                best = candidate.copy()
                best_error = error_norm
            if error_norm <= 5.0e-4:
                break
            jacobian = np.zeros((3, int(self.model.nv)), dtype=float)
            mujoco.mj_jacSite(
                self.model, scratch, jacobian, None, self._ee_site_id
            )
            arm_jacobian = jacobian[:, self._qvel_addresses]
            damping = 0.02
            system = arm_jacobian @ arm_jacobian.T + (damping * damping) * np.eye(3)
            delta = arm_jacobian.T @ np.linalg.solve(system, error)
            delta_norm = float(np.linalg.norm(delta))
            if delta_norm > 0.08:
                delta *= 0.08 / delta_norm
            candidate = np.clip(candidate + 1.35 * delta, self._lower, self._upper)
        return np.clip(best, self._lower, self._upper)

    def _track_joint_target(self, target: np.ndarray, duration_s: float) -> None:
        target = np.asarray(target, dtype=float)
        start = self._current_arm_q()
        steps = self._steps(duration_s)
        for index in range(steps):
            fraction = float(index + 1) / float(steps)
            self._set_arm_target(start + fraction * (target - start))
            self.data.ctrl[self._gripper_id] = self._gripper_target
            mujoco.mj_step(self.model, self.data)

    def _drive_joint_target(self, target: np.ndarray, duration_s: float) -> None:
        target = np.asarray(target, dtype=float)
        for _ in range(self._steps(duration_s)):
            self._set_arm_target(target)
            self.data.ctrl[self._gripper_id] = self._gripper_target
            mujoco.mj_step(self.model, self.data)

    def _track_position(self, target: np.ndarray, duration_s: float) -> None:
        self._track_joint_target(self._solve_position_ik(target), duration_s)

    def _target_contact_active(self) -> bool:
        for index in range(int(self.data.ncon)):
            contact = self.data.contact[index]
            if {
                int(contact.geom1),
                int(contact.geom2),
            } == {self._tcp_geom_id, self._target_geom_id}:
                return True
        return False

    def move_end_effector_to_position(self, request: Mapping[str, Any]) -> None:
        self._sync_targets_from_live_state()
        target = _finite_vector(request["target_position_m"], name="target_position_m")
        duration_s = _duration(request["max_duration_s"], name="max_duration_s")
        self._track_position(target, duration_s)
        self._hold(0.5)

    def trace_cartesian_path(self, request: Mapping[str, Any]) -> None:
        self._sync_targets_from_live_state()
        waypoints = request["waypoints_m"]
        if not isinstance(waypoints, Sequence) or isinstance(waypoints, (str, bytes)):
            raise ValueError("waypoints_m must be an array")
        if not 2 <= len(waypoints) <= 8:
            raise ValueError("waypoints_m must contain two to eight points")
        duration_s = _duration(
            request["max_duration_per_segment_s"],
            name="max_duration_per_segment_s",
            upper=5.0,
        )
        for waypoint in waypoints:
            self._track_position(_finite_vector(waypoint, name="waypoint"), duration_s)
        self._hold(0.5)

    def set_gripper_opening(self, request: Mapping[str, Any]) -> None:
        self._sync_targets_from_live_state()
        fraction = _finite_number(request["opening_fraction"], name="opening_fraction")
        if not 0.0 <= fraction <= 1.0:
            raise ValueError("opening_fraction must be in [0, 1]")
        duration_s = _duration(request["max_duration_s"], name="max_duration_s")
        target = GRIPPER_CLOSED_POSITION + fraction * (
            GRIPPER_OPEN_POSITION - GRIPPER_CLOSED_POSITION
        )
        steps = self._steps(duration_s)
        sweep_steps = max(1, steps // 4)
        start = self._gripper_target
        for index in range(sweep_steps):
            fraction_done = float(index + 1) / float(sweep_steps)
            self._set_arm_target(self._arm_target)
            self._set_gripper_target(
                start + fraction_done * (target - start)
            )
            mujoco.mj_step(self.model, self.data)
        for _ in range(steps - sweep_steps):
            self._set_arm_target(self._arm_target)
            self._set_gripper_target(target)
            mujoco.mj_step(self.model, self.data)
        self._set_gripper_target(target)
        self._hold(0.25)

    def approach_until_contact(self, request: Mapping[str, Any]) -> None:
        self._sync_targets_from_live_state()
        precontact = _finite_vector(
            request["precontact_position_m"], name="precontact_position_m"
        )
        direction = _finite_vector(
            request["approach_direction_unit"], name="approach_direction_unit"
        )
        direction_norm = float(np.linalg.norm(direction))
        if direction_norm <= 1.0e-12:
            raise ValueError("approach_direction_unit must be non-zero")
        direction /= direction_norm
        max_travel = _finite_number(request["max_travel_m"], name="max_travel_m")
        max_speed = _finite_number(
            request["max_approach_speed_m_s"], name="max_approach_speed_m_s"
        )
        duration_s = _duration(request["max_duration_s"], name="max_duration_s")
        if not 0.0 < max_travel <= 0.08 or not 0.0 < max_speed <= 0.05:
            raise ValueError("approach travel and speed are outside public bounds")

        precontact_duration = min(1.0, max(0.5, 0.3 * duration_s))
        self._track_position(precontact, precontact_duration)
        precontact_hold_s = min(
            0.3, max(0.1, duration_s - precontact_duration - 0.25)
        )
        self._hold(precontact_hold_s)
        # Use a short physical ramp so the native position servos enter the
        # fixture with a low residual TCP speed.  The public request remains
        # the upper bound; the ramp only calibrates the reference positive
        # control to the sampled contact-hold gate.
        start_speed = min(max_speed, 0.016)
        end_speed = min(start_speed, 0.002)
        approach_duration = max(
            0.25, duration_s - precontact_duration - precontact_hold_s
        )
        approach_steps = self._steps(approach_duration)
        for index in range(approach_steps):
            elapsed = min(approach_duration, self._timestep * float(index + 1))
            distance = start_speed * elapsed + 0.5 * (
                end_speed - start_speed
            ) * elapsed * elapsed / approach_duration
            distance = min(max_travel, distance)
            desired = precontact + distance * direction
            self._set_arm_target(self._solve_position_ik(desired))
            self.data.ctrl[self._gripper_id] = self._gripper_target
            mujoco.mj_step(self.model, self.data)
            if self._target_contact_active():
                # Hold a small, bounded physical penetration target after
                # first contact.  A target exactly at the contact boundary
                # can lose contact while the native servos dissipate the
                # incoming velocity.
                contact_target = self._ee_position() + 5.0e-4 * direction
                self._set_arm_target(self._solve_position_ik(contact_target))
                break
        self._hold(0.1)

    def move_cartesian_offset_and_return(self, request: Mapping[str, Any]) -> None:
        self._sync_targets_from_live_state()
        offset = _finite_vector(
            request["offset_robot_base_m"], name="offset_robot_base_m"
        )
        duration_s = _duration(
            request["max_duration_per_leg_s"],
            name="max_duration_per_leg_s",
            upper=5.0,
        )
        start = self._ee_position()
        base_rotation = np.asarray(
            self.data.xmat[self._base_body_id], dtype=float
        ).reshape(3, 3)
        outbound = start + base_rotation @ offset
        leg_motion_s = max(0.25, duration_s - 0.25)
        self._drive_joint_target(self._solve_position_ik(outbound), leg_motion_s)
        self._hold(0.25)
        self._drive_joint_target(self._solve_position_ik(start), leg_motion_s)
        self._hold(0.5)
