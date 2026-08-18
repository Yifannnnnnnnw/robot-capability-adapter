"""Package-local, capability-neutral Go2 control primitives.

The Framework supplies the canonical MuJoCo model and data. This skeleton
only resolves the declared Go2 joints, writes actuator torques, and advances
that session with mujoco.mj_step. It does not choose task methods or contain
a task-to-capability map.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import mujoco
import numpy as np


@dataclass(frozen=True)
class QuadrupedSpec:
    base_body_name: str
    leg_joint_names: Mapping[str, Sequence[str]]
    leg_actuator_names: Mapping[str, Sequence[str]]
    home_qpos: Sequence[float]
    kp: float = 80.0
    kd: float = 4.0
    gait_frequency_hz: float = 4.0


GO2_SPEC = QuadrupedSpec(
    base_body_name="base_link",
    leg_joint_names={
        "FL": ("FL_hip_joint", "FL_thigh_joint", "FL_calf_joint"),
        "FR": ("FR_hip_joint", "FR_thigh_joint", "FR_calf_joint"),
        "RL": ("RL_hip_joint", "RL_thigh_joint", "RL_calf_joint"),
        "RR": ("RR_hip_joint", "RR_thigh_joint", "RR_calf_joint"),
    },
    leg_actuator_names={
        "FL": ("FL_hip", "FL_thigh", "FL_calf"),
        "FR": ("FR_hip", "FR_thigh", "FR_calf"),
        "RL": ("RL_hip", "RL_thigh", "RL_calf"),
        "RR": ("RR_hip", "RR_thigh", "RR_calf"),
    },
    home_qpos=(0.0, 0.9, -1.8) * 4,
)


class QuadrupedPDGaitSkeleton:
    """Session-bound Go2 PD and small periodic gait primitive family."""

    def __init__(self, *, model: Any, data: Any, spec: QuadrupedSpec = GO2_SPEC) -> None:
        if model is None or data is None:
            raise ValueError("canonical model and data are required")
        self.model = model
        self.data = data
        self.spec = spec
        self._qpos: list[int] = []
        self._qvel: list[int] = []
        self._actuators: list[int] = []
        self._q_lo: np.ndarray | None = None
        self._q_hi: np.ndarray | None = None
        self._ctrl_lo: np.ndarray | None = None
        self._ctrl_hi: np.ndarray | None = None
        self._resolve()

    @classmethod
    def from_session(cls, *, model: Any, data: Any, spec: QuadrupedSpec = GO2_SPEC) -> "QuadrupedPDGaitSkeleton":
        return cls(model=model, data=data, spec=spec)

    def _resolve(self) -> None:
        q_lo: list[float] = []
        q_hi: list[float] = []
        ctrl_lo: list[float] = []
        ctrl_hi: list[float] = []
        for leg in self.spec.leg_joint_names:
            for joint_name, actuator_name in zip(
                self.spec.leg_joint_names[leg], self.spec.leg_actuator_names[leg]
            ):
                joint_id = int(mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, joint_name))
                actuator_id = int(mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name))
                if joint_id < 0 or actuator_id < 0:
                    raise ValueError(f"missing Go2 joint or actuator {joint_name!r}")
                self._qpos.append(int(self.model.jnt_qposadr[joint_id]))
                self._qvel.append(int(self.model.jnt_dofadr[joint_id]))
                self._actuators.append(actuator_id)
                q_lo.append(float(self.model.jnt_range[joint_id, 0]))
                q_hi.append(float(self.model.jnt_range[joint_id, 1]))
                ctrl_lo.append(float(self.model.actuator_ctrlrange[actuator_id, 0]))
                ctrl_hi.append(float(self.model.actuator_ctrlrange[actuator_id, 1]))
        self._q_lo = np.asarray(q_lo, dtype=float)
        self._q_hi = np.asarray(q_hi, dtype=float)
        self._ctrl_lo = np.asarray(ctrl_lo, dtype=float)
        self._ctrl_hi = np.asarray(ctrl_hi, dtype=float)

    def get_joint_positions(self) -> np.ndarray:
        return np.asarray([self.data.qpos[index] for index in self._qpos], dtype=float)

    def get_joint_velocities(self) -> np.ndarray:
        return np.asarray([self.data.qvel[index] for index in self._qvel], dtype=float)

    def get_body_height(self) -> float:
        body_id = int(mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, self.spec.base_body_name))
        mujoco.mj_forward(self.model, self.data)
        return float(self.data.xpos[body_id, 2])

    def set_joint_torques(self, torques: Sequence[float]) -> None:
        command = np.asarray(torques, dtype=float)
        if command.shape != (12,) or not np.all(np.isfinite(command)):
            raise ValueError("torques must contain 12 finite values")
        assert self._ctrl_lo is not None and self._ctrl_hi is not None
        command = np.clip(command, self._ctrl_lo, self._ctrl_hi)
        for index, actuator_id in enumerate(self._actuators):
            self.data.ctrl[actuator_id] = float(command[index])

    def apply_pd_posture(self, q_target: Sequence[float]) -> None:
        target = np.asarray(q_target, dtype=float)
        if target.shape != (12,) or not np.all(np.isfinite(target)):
            raise ValueError("q_target must contain 12 finite values")
        assert self._q_lo is not None and self._q_hi is not None
        target = np.clip(target, self._q_lo, self._q_hi)
        torque = self.spec.kp * (target - self.get_joint_positions()) - self.spec.kd * self.get_joint_velocities()
        self.set_joint_torques(torque)

    def step(self, count: int = 1) -> None:
        if isinstance(count, bool) or int(count) != count or count < 0:
            raise ValueError("count must be a non-negative integer")
        for _ in range(int(count)):
            mujoco.mj_step(self.model, self.data)

    def move_to_posture(self, q_target: Sequence[float], duration_s: float = 1.0) -> None:
        target = np.asarray(q_target, dtype=float)
        start = self.get_joint_positions()
        steps = max(1, int(math.ceil(float(duration_s) / float(self.model.opt.timestep))))
        for index in range(steps):
            fraction = (index + 1) / steps
            self.apply_pd_posture((1.0 - fraction) * start + fraction * target)
            self.step()

    def stand_up(self, duration_s: float = 1.0) -> None:
        self.move_to_posture(self.spec.home_qpos, duration_s)

    def sit(self, duration_s: float = 1.0) -> None:
        target = np.asarray(self.spec.home_qpos, dtype=float).copy()
        target[1::3] += 0.45
        target[2::3] -= 0.35
        self.move_to_posture(target, duration_s)

    def stop(self, duration_s: float = 0.2) -> None:
        self.move_to_posture(self.get_joint_positions(), duration_s)

    def command_planar_velocity(self, vx: float, vy: float = 0.0, yaw_rate: float = 0.0, duration_s: float = 1.0) -> None:
        del vy, yaw_rate
        steps = max(1, int(math.ceil(float(duration_s) / float(self.model.opt.timestep))))
        phases = (0.5, 0.0, 0.0, 0.5)
        for index in range(steps):
            phase = (index * float(self.model.opt.timestep) * self.spec.gait_frequency_hz) % 1.0
            target = np.asarray(self.spec.home_qpos, dtype=float).copy()
            for leg_index, offset in enumerate(phases):
                wave = math.sin(2.0 * math.pi * (phase + offset))
                target[3 * leg_index + 1] += 0.25 * float(vx) * wave
                target[3 * leg_index + 2] -= 0.35 * max(0.0, wave)
            self.apply_pd_posture(target)
            self.step()

    def walk_forward(self, speed: float = 0.3, duration_s: float = 1.0) -> None:
        self.command_planar_velocity(speed, duration_s=duration_s)

    def describe(self) -> dict[str, Any]:
        return {
            "base_body": self.spec.base_body_name,
            "leg_order": list(self.spec.leg_joint_names),
            "joints_per_leg": 3,
            "home_qpos": list(self.spec.home_qpos),
            "pd": {"kp": self.spec.kp, "kd": self.spec.kd},
        }


__all__ = ["GO2_SPEC", "QuadrupedPDGaitSkeleton", "QuadrupedSpec"]
