# SPDX-License-Identifier: Apache-2.0
"""Shared-model bimanual serial-arm skeleton.

The bimanual wrapper owns coordination only.  Each arm keeps using the
existing ``ArmSerialDLSSkeleton`` for FK, DLS IK, interpolation, and
gripper control; both children point at the same MuJoCo model and data.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import numpy as np

from .arm_serial_dls import ArmSerialDLSSkeleton
from .base import ArmSpec, SkeletonBase


ArmLabel = Literal["left", "right"]


@dataclass
class BimanualSerialDLSSpec:
    """Two arm specs in one world; ALOHA uses initial_keyframe='neutral_pose'.

    With an initial keyframe, omitted child home_qpos values use that pose.
    """

    left: ArmSpec
    right: ArmSpec
    initial_keyframe: str | None = None


class BimanualSerialDLSSkeleton(SkeletonBase):
    """Coordinate two ``ArmSerialDLSSkeleton`` instances on one model/data."""

    def __init__(self, model, data, spec: BimanualSerialDLSSpec) -> None:
        if not isinstance(spec, BimanualSerialDLSSpec):
            raise TypeError("spec must be a BimanualSerialDLSSpec")
        super().__init__(model, data, spec)
        self._validate_spec_name_disjoint(spec)

        self.left = ArmSerialDLSSkeleton(model, data, spec.left)
        self.right = ArmSerialDLSSkeleton(model, data, spec.right)

        self._validate_disjoint_arm_bindings()
        self._wire_child_steps_to_parent()
        self._initialize_safe_home()

    @staticmethod
    def _validate_spec_name_disjoint(spec: BimanualSerialDLSSpec) -> None:
        for label, left_names, right_names in (
            ("joint names", spec.left.arm_joint_names, spec.right.arm_joint_names),
            (
                "actuator names",
                spec.left.arm_actuator_names + (spec.left.gripper_actuator_names or []),
                spec.right.arm_actuator_names + (spec.right.gripper_actuator_names or []),
            ),
        ):
            overlap = sorted(set(left_names).intersection(right_names))
            if overlap:
                raise ValueError(f"left/right arm {label} overlap: {overlap}")

    @classmethod
    def from_mjcf(cls, mjcf_path, spec: BimanualSerialDLSSpec):
        """Create one world, optionally initialized from a model keyframe."""
        import mujoco
        model = mujoco.MjModel.from_xml_path(str(Path(mjcf_path).resolve()))
        data = mujoco.MjData(model)
        if spec.initial_keyframe is not None:
            kid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, spec.initial_keyframe)
            if kid < 0:
                raise ValueError(f"initial keyframe {spec.initial_keyframe!r} is absent")
            mujoco.mj_resetDataKeyframe(model, data, kid)
            arms = []
            for arm_spec in (spec.left, spec.right):
                if arm_spec.home_qpos is None:
                    ids = [model.joint(name).id for name in arm_spec.arm_joint_names]
                    arm_spec = replace(arm_spec,
                                       home_qpos=data.qpos[model.jnt_qposadr[ids]].tolist())
                arms.append(arm_spec)
            spec = replace(spec, left=arms[0], right=arms[1])
        mujoco.mj_forward(model, data)
        return cls(model, data, spec)

    def _validate_disjoint_arm_bindings(self) -> None:
        """Reject ambiguous left/right arm joints or actuators early."""
        for label, left_ids, right_ids in (
            ("joint ids", self.left._arm_joint_ids, self.right._arm_joint_ids),
            ("actuator ids", self.left._arm_actuator_ids, self.right._arm_actuator_ids),
        ):
            overlap = sorted(set(left_ids).intersection(right_ids))
            if overlap:
                names = [
                    self._mj.mj_id2name(
                        self.model,
                        self._mj.mjtObj.mjOBJ_JOINT
                        if label == "joint ids"
                        else self._mj.mjtObj.mjOBJ_ACTUATOR,
                        item,
                    )
                    for item in overlap
                ]
                raise ValueError(f"left/right arm {label} overlap: {names}")

    def _wire_child_steps_to_parent(self) -> None:
        """Route child motion ticks through this wrapper's shared step."""

        def delegate(n: int = 1) -> None:
            self.step(n)

        # ArmSerialDLSSkeleton.move_joints and grasp backends call self.step.
        # Binding this delegate preserves that implementation while ensuring
        # every child tick advances the one shared parent world.
        self.left.step = delegate  # type: ignore[method-assign]
        self.right.step = delegate  # type: ignore[method-assign]

    def _initialize_safe_home(self) -> None:
        """Set both home control targets without changing the physical pose."""
        for arm in (self.left, self.right):
            arm.set_arm_actuators(arm._home_q)
            for aid in arm._gripper_actuator_ids:
                open_ctrl = float(arm.spec.gripper_open_ctrl)
                self.data.ctrl[aid] = open_ctrl

        self._mj.mj_forward(self.model, self.data)

    def _arm(self, arm: str) -> ArmSerialDLSSkeleton:
        if not isinstance(arm, str) or arm not in ("left", "right"):
            raise ValueError("arm must be explicitly 'left' or 'right'")
        return self.left if arm == "left" else self.right

    def step(self, n: int = 1) -> None:
        """Advance the shared world; children delegate here."""
        SkeletonBase.step(self, n)

    def _move_both_joints(
        self,
        left_target: np.ndarray,
        right_target: np.ndarray,
        duration: float,
    ) -> bool:
        """Interpolate both child arm targets while stepping one shared world."""
        left_target = np.asarray(left_target, dtype=np.float64)
        right_target = np.asarray(right_target, dtype=np.float64)
        if left_target.shape != (self.left.dof,):
            raise ValueError(f"left q_target shape {left_target.shape} != ({self.left.dof},)")
        if right_target.shape != (self.right.dof,):
            raise ValueError(f"right q_target shape {right_target.shape} != ({self.right.dof},)")

        left_target = np.clip(left_target, self.left._q_lo, self.left._q_hi)
        right_target = np.clip(right_target, self.right._q_lo, self.right._q_hi)
        left_start = np.array(
            [float(self.data.ctrl[aid]) for aid in self.left._arm_actuator_ids],
            dtype=np.float64,
        )
        right_start = np.array(
            [float(self.data.ctrl[aid]) for aid in self.right._arm_actuator_ids],
            dtype=np.float64,
        )
        n_steps = max(1, int(float(duration) / float(self.model.opt.timestep)))
        for i in range(n_steps):
            t = (i + 1) / n_steps
            self.left.set_arm_actuators(left_start + t * (left_target - left_start))
            self.right.set_arm_actuators(right_start + t * (right_target - right_start))
            self.step(1)
        return True

    def home(self, duration: float = 2.0) -> bool:
        """Drive both arms to their configured home targets together."""
        return self._move_both_joints(self.left._home_q, self.right._home_q, duration)

    def get_ee_pose(self, *, arm: str) -> tuple[np.ndarray, np.ndarray]:
        """Return one arm's world-frame end-effector pose."""
        return self._arm(arm).get_ee_pose()

    def move_cartesian(
        self,
        target_xyz: np.ndarray,
        duration: float = 2.0,
        *,
        arm: str,
    ) -> bool:
        """Use the selected child arm's DLS IK and shared-world motion."""
        return self._arm(arm).move_cartesian(target_xyz, duration=duration)

    def gripper_open(self, *, arm: str) -> bool:
        """Open the selected arm's physical gripper."""
        return self._arm(arm).gripper_open()

    def gripper_close(self, *, arm: str) -> bool:
        """Close the selected arm's physical gripper."""
        return self._arm(arm).gripper_close()

    def get_joint_positions(self, *, arm: str) -> np.ndarray:
        """Return one arm's serial-joint positions."""
        return self._arm(arm).get_joint_positions()

    def move_joints(
        self,
        q_target: np.ndarray,
        duration: float = 2.0,
        *,
        arm: str,
    ) -> bool:
        """Move one arm while the other keeps its current actuator targets."""
        return self._arm(arm).move_joints(q_target, duration=duration)

    def hold(self, duration: float = 0.5) -> bool:
        """Advance real physics while both arms retain their current controls."""
        control_ids: list[int] = []
        for arm in (self.left, self.right):
            control_ids.extend(arm._arm_actuator_ids)
            control_ids.extend(arm._gripper_actuator_ids)
        unique_ids = list(dict.fromkeys(control_ids))
        held_ctrl = self.data.ctrl[unique_ids].copy()
        n_steps = max(1, int(float(duration) / float(self.model.opt.timestep)))
        for _ in range(n_steps):
            self.data.ctrl[unique_ids] = held_ctrl
            self.step(1)
        return True

    def describe(self) -> dict:
        """Return both child descriptions and the shared-world contract."""
        return {
            "skeleton": "BimanualSerialDLSSkeleton",
            "dof": self.left.dof + self.right.dof,
            "shared_model": self.left.model is self.model and self.right.model is self.model,
            "shared_data": self.left.data is self.data and self.right.data is self.data,
            "left": self.left.describe(),
            "right": self.right.describe(),
        }
