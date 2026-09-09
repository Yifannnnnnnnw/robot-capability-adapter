# SPDX-License-Identifier: Apache-2.0
"""Generated Unitree A1 capability driver.

Subclass of QuadrupedPDGaitSkeleton with native position-servo actuation.
Implements the fixed-family capability contract (G1..G5) on one MuJoCo
model/data. All timing uses data.time; physics advances only through
actuator commands (data.ctrl) + mj_step.
"""
from __future__ import annotations

import math
import numpy as np

from auto_adapter.skeletons.quadruped_pd_gait import QuadrupedPDGaitSkeleton
from auto_adapter.skeletons.base import QuadrupedSpec


def _wrap(a: float) -> float:
    return (float(a) + math.pi) % (2.0 * math.pi) - math.pi


class CapabilityError(RuntimeError):
    """Bounded capability failure."""


class Robot(QuadrupedPDGaitSkeleton):
    """Unitree A1 quadruped capability robot."""

    def __init__(self, model, data, spec: QuadrupedSpec) -> None:
        super().__init__(model, data, spec)
        # Cache a scratch data for kinematic-only calculations.
        self._scratch = self._mj.MjData(self.model)

    # ---- base yaw / planar pose helpers (read-only) ----
    def _base_yaw(self) -> float:
        _, R = self.get_base_pose()
        return float(math.atan2(R[1, 0], R[0, 0]))

    def _planar_xy(self) -> np.ndarray:
        pos, _ = self.get_base_pose()
        return np.array([pos[0], pos[1]], dtype=np.float64)

    def _roll_pitch(self) -> tuple[float, float]:
        _, R = self.get_base_pose()
        # roll about x, pitch about y from rotation matrix
        pitch = float(math.asin(max(-1.0, min(1.0, -R[2, 0]))))
        roll = float(math.atan2(R[2, 1], R[2, 2]))
        return roll, pitch

    def _state_finite(self) -> bool:
        pos, R = self.get_base_pose()
        tw = self.get_base_twist()
        vals = [pos, R, tw["linear_world_m_s"], tw["angular_world_rad_s"]]
        return all(np.all(np.isfinite(np.asarray(v))) for v in vals)

    def _check_support(self) -> None:
        """Support integrity: torso upright and above the floor."""
        roll, pitch = self._roll_pitch()
        h = self.get_body_height()
        if not self._state_finite():
            raise CapabilityError("public base state became unavailable")
        if abs(roll) > 1.0 or abs(pitch) > 1.0:
            raise CapabilityError("support integrity lost (attitude)")
        if h < 0.08:
            raise CapabilityError("support integrity lost (body collapsed)")

    def _sim_dt(self) -> float:
        return float(self.model.opt.timestep)


def build() -> "Robot":
    leg_joint_names = {
        "FR": ["FR_hip_joint", "FR_thigh_joint", "FR_calf_joint"],
        "FL": ["FL_hip_joint", "FL_thigh_joint", "FL_calf_joint"],
        "RR": ["RR_hip_joint", "RR_thigh_joint", "RR_calf_joint"],
        "RL": ["RL_hip_joint", "RL_thigh_joint", "RL_calf_joint"],
    }
    leg_actuator_names = {
        "FR": ["FR_hip", "FR_thigh", "FR_calf"],
        "FL": ["FL_hip", "FL_thigh", "FL_calf"],
        "RR": ["RR_hip", "RR_thigh", "RR_calf"],
        "RL": ["RL_hip", "RL_thigh", "RL_calf"],
    }
    # Home posture (hip, thigh, calf) x 4, matching the settled keyframe.
    home_qpos = [0.0, 0.9, -1.8] * 4
    spec = QuadrupedSpec(
        base_body_name="trunk",
        leg_joint_names=leg_joint_names,
        leg_actuator_names=leg_actuator_names,
        home_qpos=home_qpos,
        actuation="joint_position",
        gait_freq_hz=2.0,
        swing_amp_thigh=0.14,
        swing_amp_calf=0.22,
        thigh_forward_sign=-1.0,
        body_height_target=0.27,
        vx_max=0.4,
        vy_max=0.4,
        vyaw_max=1.0,
    )
    robot = Robot.from_mjcf("mjcf.xml", spec=spec)
    return robot
