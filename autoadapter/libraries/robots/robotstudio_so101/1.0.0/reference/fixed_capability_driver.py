"""Task-blind SO-101 reference driver for the fixed A1--A5 interface.

The Framework owns the canonical MuJoCo model, data, reset, and verdict.  This
driver consumes only capability-native requests, writes actuator targets, and
advances the supplied physical session.  It is a hidden positive control and
may be pinned as the fixed driver for the separately declared B2 extension;
it is never exposed to Driver Synthesis or a high-level controller.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import mujoco
import numpy as np

from autoadapter2.trusted_skeletons.arm_serial_dls import (
    ArmSerialDLSSkeleton,
    ArmSpec,
)


_ARM_JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
)
_ARM_LIMITS = {
    "shoulder_pan": (-1.91986, 1.91986),
    "shoulder_lift": (-1.7453293, 1.7453293),
    "elbow_flex": (-1.69, 1.69),
    "wrist_flex": (-1.658063, 1.658063),
    "wrist_roll": (-2.7438473, 2.7438473),
}
_GRIPPER_CLOSED = -0.17453
_GRIPPER_OPEN = 1.74533
_SPEC = ArmSpec(
    ee_site_name="gripperframe",
    arm_joint_names=_ARM_JOINTS,
    arm_actuator_names=_ARM_JOINTS,
    joint_limits=_ARM_LIMITS,
    ik_damping=0.02,
    ik_max_iter=300,
    ik_tolerance=0.001,
    ik_step_clamp=0.10,
    gripper_actuator_names=("gripper",),
    gripper_close_ctrl=_GRIPPER_CLOSED,
    gripper_open_ctrl=_GRIPPER_OPEN,
    gripper_settle_steps=1,
)


def _request(value: Any, fields: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"request must contain exactly {sorted(fields)}")
    return value


def _number(value: Any, name: str, *, lower: float | None = None) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or (lower is not None and result < lower):
        raise ValueError(f"{name} is outside its finite domain")
    return result


def _vector(value: Any, size: int, name: str) -> np.ndarray:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be an array")
    result = np.asarray(value, dtype=float)
    if result.shape != (size,) or not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain {size} finite numbers")
    return result


class Driver:
    """Actuator-only positive control for the frozen capability ABI."""

    def __init__(self, *, model: Any, data: Any) -> None:
        self.model = model
        self.data = data
        self._arm = ArmSerialDLSSkeleton(model=model, data=data, spec=_SPEC)
        self._timestep = float(model.opt.timestep)
        if not math.isfinite(self._timestep) or self._timestep <= 0.0:
            raise ValueError("canonical timestep must be positive")
        self._gripper_joint = self._name_id(
            mujoco.mjtObj.mjOBJ_JOINT, "gripper"
        )
        self._gripper_qpos = int(model.jnt_qposadr[self._gripper_joint])
        self._gripper_actuator = self._name_id(
            mujoco.mjtObj.mjOBJ_ACTUATOR, "gripper"
        )
        self._base_body = self._name_id(mujoco.mjtObj.mjOBJ_BODY, "base")
        distal_names = {
            "fixed_jaw_sph_tip1",
            "fixed_jaw_sph_tip2",
            "fixed_jaw_sph_tip3",
            "fixed_jaw_box3",
            "fixed_jaw_box4",
            "fixed_jaw_box5",
            "fixed_jaw_box6",
            "fixed_jaw_box7",
            "moving_jaw_sph_tip1",
            "moving_jaw_sph_tip2",
            "moving_jaw_sph_tip3",
            "moving_jaw_box2",
            "moving_jaw_box3",
        }
        self._tool_geoms: set[int] = set()
        for geom_id in range(int(model.ngeom)):
            name = mujoco.mj_id2name(
                model, mujoco.mjtObj.mjOBJ_GEOM, geom_id
            )
            body_id = int(model.geom_bodyid[geom_id])
            body_name = mujoco.mj_id2name(
                model, mujoco.mjtObj.mjOBJ_BODY, body_id
            )
            collision_mesh = (
                name is None
                and body_name in {"gripper", "moving_jaw_so101_v1"}
                and int(model.geom_group[geom_id]) == 4
                and int(model.geom_contype[geom_id]) != 0
            )
            if name in distal_names or collision_mesh:
                self._tool_geoms.add(geom_id)
        if not self._tool_geoms:
            raise ValueError("canonical model has no distal gripper collision geoms")

    def _name_id(self, object_type: Any, name: str) -> int:
        identifier = int(mujoco.mj_name2id(self.model, object_type, name))
        if identifier < 0:
            raise ValueError(f"canonical model is missing {name!r}")
        return identifier

    def _steps(self, duration_s: float) -> int:
        return max(1, int(math.ceil(duration_s / self._timestep)))

    def _gripper_position(self) -> float:
        return float(self.data.qpos[self._gripper_qpos])

    def _step_cartesian(
        self,
        reference: np.ndarray,
        gripper_target: float,
    ) -> None:
        current, _ = self._arm.get_ee_pose()
        error = reference - current
        jacobian = np.zeros((3, int(self.model.nv)), dtype=float)
        site_id = int(
            mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_SITE, "gripperframe"
            )
        )
        mujoco.mj_jacSite(self.model, self.data, jacobian, None, site_id)
        qvel_addresses = []
        for name in _ARM_JOINTS:
            joint_id = int(
                mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            )
            qvel_addresses.append(int(self.model.jnt_dofadr[joint_id]))
        arm_jacobian = jacobian[:, qvel_addresses]
        damping = 0.02
        system = arm_jacobian @ arm_jacobian.T + damping * damping * np.eye(3)
        delta = arm_jacobian.T @ np.linalg.solve(system, error)
        norm = float(np.linalg.norm(delta))
        if norm > 0.08:
            delta *= 0.08 / norm
        desired = self._arm.get_joint_positions()
        desired += 1.4 * delta
        desired = np.clip(
            desired,
            np.asarray([_ARM_LIMITS[name][0] for name in _ARM_JOINTS]),
            np.asarray([_ARM_LIMITS[name][1] for name in _ARM_JOINTS]),
        )
        self._arm.set_arm_actuators(desired)
        self.data.ctrl[self._gripper_actuator] = gripper_target
        mujoco.mj_step(self.model, self.data)

    def _track_segment(
        self,
        target: np.ndarray,
        *,
        duration_s: float,
        final_hold_s: float,
        reference_speed_m_s: float = 0.10,
    ) -> None:
        start, _ = self._arm.get_ee_pose()
        gripper_target = self._gripper_position()
        distance = float(np.linalg.norm(target - start))
        motion_s = min(
            max(distance / reference_speed_m_s, 0.20),
            max(duration_s - final_hold_s, 0.20),
        )
        motion_steps = self._steps(motion_s)
        total_steps = self._steps(duration_s)
        dwell_steps = self._steps(final_hold_s)
        dwell_count = 0
        for index in range(total_steps):
            fraction = min((index + 1) / motion_steps, 1.0)
            reference = start + fraction * (target - start)
            self._step_cartesian(
                reference,
                gripper_target,
            )
            current, _ = self._arm.get_ee_pose()
            if float(np.linalg.norm(target - current)) <= 0.010:
                dwell_count += 1
                if dwell_count >= dwell_steps:
                    return
            else:
                dwell_count = 0

    def _hold_joint_targets(
        self,
        arm_target: np.ndarray,
        gripper_target: float,
        duration_s: float,
    ) -> None:
        for _ in range(self._steps(duration_s)):
            self._arm.set_arm_actuators(arm_target)
            self.data.ctrl[self._gripper_actuator] = gripper_target
            mujoco.mj_step(self.model, self.data)

    def _tool_contact(self) -> bool:
        for index in range(int(self.data.ncon)):
            contact = self.data.contact[index]
            first = int(contact.geom1)
            second = int(contact.geom2)
            if (first in self._tool_geoms) != (second in self._tool_geoms):
                return True
        return False

    def move_end_effector_to_position(self, request: Any) -> None:
        """Move to one world-frame target and retain the current gripper opening."""

        values = _request(request, {"target_position_m", "max_duration_s"})
        target = _vector(values["target_position_m"], 3, "target_position_m")
        duration = _number(values["max_duration_s"], "max_duration_s", lower=0.25)
        self._track_segment(target, duration_s=duration, final_hold_s=0.55)

    def trace_cartesian_path(self, request: Any) -> None:
        """Trace each supplied world-frame waypoint in order."""

        values = _request(
            request, {"waypoints_m", "max_duration_per_segment_s"}
        )
        raw_waypoints = values["waypoints_m"]
        if (
            isinstance(raw_waypoints, (str, bytes))
            or not isinstance(raw_waypoints, Sequence)
            or not 2 <= len(raw_waypoints) <= 8
        ):
            raise ValueError("waypoints_m must contain two to eight waypoints")
        waypoints = [
            _vector(item, 3, "waypoints_m") for item in raw_waypoints
        ]
        budget = _number(
            values["max_duration_per_segment_s"],
            "max_duration_per_segment_s",
            lower=0.25,
        )
        for index, target in enumerate(waypoints):
            self._track_segment(
                target,
                duration_s=budget,
                final_hold_s=0.55 if index == len(waypoints) - 1 else 0.05,
            )

    def set_gripper_opening(self, request: Any) -> None:
        """Set normalized aperture while holding the call-time arm pose."""

        values = _request(request, {"opening_fraction", "max_duration_s"})
        fraction = _number(values["opening_fraction"], "opening_fraction", lower=0.0)
        if fraction > 1.0:
            raise ValueError("opening_fraction must be in [0, 1]")
        duration = _number(values["max_duration_s"], "max_duration_s", lower=0.25)
        arm_target = self._arm.get_joint_positions()
        target = _GRIPPER_CLOSED + fraction * (_GRIPPER_OPEN - _GRIPPER_CLOSED)
        self._hold_joint_targets(arm_target, target, duration)

    def approach_until_contact(self, request: Any) -> None:
        """Enter a precontact point, then advance on the requested ray and stop."""

        fields = {
            "precontact_position_m",
            "approach_direction_unit",
            "max_travel_m",
            "max_approach_speed_m_s",
            "max_duration_s",
        }
        values = _request(request, fields)
        precontact = _vector(
            values["precontact_position_m"], 3, "precontact_position_m"
        )
        direction = _vector(
            values["approach_direction_unit"], 3, "approach_direction_unit"
        )
        direction_norm = float(np.linalg.norm(direction))
        if not math.isclose(direction_norm, 1.0, rel_tol=0.0, abs_tol=1.0e-3):
            raise ValueError("approach_direction_unit must be normalized")
        travel = _number(values["max_travel_m"], "max_travel_m", lower=0.0)
        speed = _number(
            values["max_approach_speed_m_s"],
            "max_approach_speed_m_s",
            lower=0.0,
        )
        duration = _number(values["max_duration_s"], "max_duration_s", lower=0.25)
        if travel <= 0.0 or speed <= 0.0 or speed > 0.05:
            raise ValueError("approach travel/speed is outside the public domain")

        method_start_s = float(self.data.time)
        use_camera_clear_branch = float(precontact[0]) >= 0.385
        if use_camera_clear_branch:
            camera_clear_arm = self._arm.get_joint_positions()
            camera_clear_arm[-1] = 1.50
            self._hold_joint_targets(
                camera_clear_arm,
                self._gripper_position(),
                0.30,
            )
        self._track_segment(
            precontact,
            duration_s=min(1.80, max(1.40, 0.35 * duration)),
            final_hold_s=0.15,
            reference_speed_m_s=0.30,
        )
        gripper_target = self._gripper_position()
        approach_speed = speed
        progress = 0.0
        while float(self.data.time) - method_start_s < duration:
            progress = min(
                progress + approach_speed * self._timestep,
                travel,
            )
            self._step_cartesian(
                precontact + progress * direction,
                gripper_target,
            )
            if self._tool_contact():
                anchor = min(progress + 0.014, travel)
                while (
                    progress + 1.0e-12 < anchor
                    and float(self.data.time) - method_start_s < duration
                ):
                    progress = min(
                        progress + 0.018 * self._timestep,
                        anchor,
                    )
                    self._step_cartesian(
                        precontact + progress * direction,
                        gripper_target,
                    )
                hold_steps = self._steps(
                    min(
                        0.50,
                        max(
                            0.0,
                            duration
                            - (float(self.data.time) - method_start_s),
                        ),
                    )
                )
                for _ in range(hold_steps):
                    if float(self.data.time) - method_start_s >= duration:
                        break
                    self._step_cartesian(
                        precontact + anchor * direction,
                        gripper_target,
                    )
                return

    def move_cartesian_offset_and_return(self, request: Any) -> None:
        """Move by a call-time base-frame offset, dwell, and return."""

        values = _request(
            request, {"offset_robot_base_m", "max_duration_per_leg_s"}
        )
        offset = _vector(
            values["offset_robot_base_m"], 3, "offset_robot_base_m"
        )
        budget = _number(
            values["max_duration_per_leg_s"],
            "max_duration_per_leg_s",
            lower=0.25,
        )
        start, _ = self._arm.get_ee_pose()
        base_rotation = np.asarray(
            self.data.xmat[self._base_body], dtype=float
        ).reshape(3, 3)
        outbound = start + base_rotation @ offset
        self._track_segment(outbound, duration_s=budget, final_hold_s=0.30)
        self._track_segment(start, duration_s=budget, final_hold_s=0.55)


def build(*, model: Any, data: Any) -> Driver:
    """Bind the reference driver to the Framework-owned physical session."""

    return Driver(model=model, data=data)
