"""Capability-neutral bounded control surface for the Stretch 2 MJCF model."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import mujoco


class StretchSkeletonError(ValueError):
    """Raised when a control or observation request is outside the public surface."""


class StretchControlSkeleton:
    """Expose named actuator controls, physics stepping, and public state reads."""

    ACTUATOR_NAMES = (
        "forward",
        "turn",
        "lift",
        "arm_extend",
        "wrist_yaw",
        "grip",
        "head_pan",
        "head_tilt",
    )
    BODY_NAMES = ("base_link", "link_lift", "link_gripper_slider")
    MAX_STEP_BATCH = 2000

    def __init__(self, model: Any, data: Any) -> None:
        self.model = model
        self.data = data
        self._actuator_ids = {
            name: self._name_id(mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            for name in self.ACTUATOR_NAMES
        }
        self._body_ids = {
            name: self._name_id(mujoco.mjtObj.mjOBJ_BODY, name)
            for name in self.BODY_NAMES
        }

    def _name_id(self, object_type: Any, name: str) -> int:
        identifier = int(mujoco.mj_name2id(self.model, object_type, name))
        if identifier < 0:
            raise StretchSkeletonError(f"unknown public MuJoCo name: {name}")
        return identifier

    def set_controls(self, controls: Mapping[str, float]) -> None:
        """Write only bounded named actuator controls through ``data.ctrl``."""

        if not isinstance(controls, Mapping) or not controls:
            raise StretchSkeletonError("controls must be a non-empty mapping")
        unknown = set(controls) - set(self._actuator_ids)
        if unknown:
            raise StretchSkeletonError(f"unknown actuator controls: {sorted(unknown)}")
        for name, value in controls.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise StretchSkeletonError(f"control {name!r} must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise StretchSkeletonError(f"control {name!r} must be finite")
            actuator_id = self._actuator_ids[name]
            lower, upper = (float(item) for item in self.model.actuator_ctrlrange[actuator_id])
            if numeric < lower or numeric > upper:
                raise StretchSkeletonError(
                    f"control {name!r} is outside [{lower}, {upper}]"
                )
            self.data.ctrl[actuator_id] = numeric

    def step(self, steps: int = 1) -> None:
        """Advance the canonical MuJoCo session by a bounded number of steps."""

        if isinstance(steps, bool) or not isinstance(steps, int):
            raise StretchSkeletonError("steps must be an integer")
        if steps < 1 or steps > self.MAX_STEP_BATCH:
            raise StretchSkeletonError(f"steps must be in [1, {self.MAX_STEP_BATCH}]")
        for _ in range(steps):
            mujoco.mj_step(self.model, self.data)

    def observe(self) -> dict[str, Any]:
        """Read finite public state without exposing task or evaluation semantics."""

        joint_positions: dict[str, float] = {}
        joint_velocities: dict[str, float] = {}
        for joint_id in range(int(self.model.njnt)):
            joint_type = int(self.model.jnt_type[joint_id])
            if joint_type not in {
                int(mujoco.mjtJoint.mjJNT_HINGE),
                int(mujoco.mjtJoint.mjJNT_SLIDE),
            }:
                continue
            name = mujoco.mj_id2name(
                self.model, mujoco.mjtObj.mjOBJ_JOINT, joint_id
            ) or f"joint_{joint_id}"
            joint_positions[name] = float(self.data.qpos[int(self.model.jnt_qposadr[joint_id])])
            joint_velocities[name] = float(self.data.qvel[int(self.model.jnt_dofadr[joint_id])])

        body_positions = {
            name: [float(value) for value in self.data.xpos[body_id]]
            for name, body_id in self._body_ids.items()
        }
        actuator_controls = {
            name: float(self.data.ctrl[actuator_id])
            for name, actuator_id in self._actuator_ids.items()
        }
        return {
            "time": float(self.data.time),
            "joint_positions": joint_positions,
            "joint_velocities": joint_velocities,
            "body_positions": body_positions,
            "actuator_controls": actuator_controls,
        }


__all__ = ["StretchControlSkeleton", "StretchSkeletonError"]
