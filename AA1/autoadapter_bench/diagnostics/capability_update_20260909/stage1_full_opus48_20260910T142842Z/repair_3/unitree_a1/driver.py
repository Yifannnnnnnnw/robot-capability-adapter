# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for the Unitree A1 quadruped.

Subclasses ``QuadrupedPDGaitSkeleton`` (native joint-position actuators) and
implements the public capability contract (G1..G5). All motion is produced by
writing bounded joint-position targets to ``data.ctrl`` and advancing the same
MuJoCo model/data via ``self.step``. Deadlines and holds are measured with
``data.time`` (simulation seconds). No live qpos/qvel is ever set to achieve
an action.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from auto_adapter.skeletons.quadruped_pd_gait import QuadrupedPDGaitSkeleton
from auto_adapter.skeletons.base import QuadrupedSpec


class CapabilityError(RuntimeError):
    """Bounded capability error raised on precondition / support failure."""


def _finite(*vals) -> bool:
    for v in vals:
        arr = np.asarray(v, dtype=np.float64)
        if not np.all(np.isfinite(arr)):
            return False
    return True


def _wrap(a: float) -> float:
    return float((a + math.pi) % (2.0 * math.pi) - math.pi)


class Robot(QuadrupedPDGaitSkeleton):
    """Unitree A1 capability driver."""

    _G1_VLIN = 0.4
    _G1_VYAW = 1.0
    _G4_H_MIN = 0.2086423007004964
    _G4_H_MAX = 0.3129634510507446

    # Height calibration (calf = -2*thigh), measured from real settled physics:
    #   thigh 0.75 -> h 0.297 ; thigh 1.00 -> h 0.217  (slope ~ -0.32 / rad)
    _H_AT_T075 = 0.297
    _H_SLOPE = 0.32
    _THIGH_LO = 0.55
    _THIGH_HI = 1.20

    # ── observation helpers ────────────────────────────────────────────────
    def _base_yaw(self) -> float:
        _, R = self.get_base_pose()
        return math.atan2(float(R[1, 0]), float(R[0, 0]))

    def _planar_xy(self) -> np.ndarray:
        pos, _ = self.get_base_pose()
        return np.array([float(pos[0]), float(pos[1])], dtype=np.float64)

    def _roll_pitch(self) -> tuple[float, float]:
        _, R = self.get_base_pose()
        pitch = math.asin(max(-1.0, min(1.0, -float(R[2, 0]))))
        roll = math.atan2(float(R[2, 1]), float(R[2, 2]))
        return roll, pitch

    def _state_ok(self) -> bool:
        pos, R = self.get_base_pose()
        tw = self.get_base_twist()
        return _finite(pos, R, tw["linear_world_m_s"], tw["angular_world_rad_s"])

    def _planar_speed(self) -> float:
        tw = self.get_base_twist()
        lw = np.asarray(tw["linear_world_m_s"], dtype=np.float64)
        return float(math.hypot(lw[0], lw[1]))

    def _twist_report(self) -> dict:
        tw = self.get_base_twist()
        vb = np.asarray(tw["linear_body_yaw_m_s"], dtype=np.float64)
        return {"linear_velocity_body_m_s": [float(vb[0]), float(vb[1])],
                "yaw_rate_rad_s": float(tw["yaw_rate_rad_s"])}

    def _thigh_for_height(self, h: float) -> float:
        thigh = 0.75 + (self._H_AT_T075 - h) / self._H_SLOPE
        return float(np.clip(thigh, self._THIGH_LO, self._THIGH_HI))

    def _height_posture(self, thigh: float) -> np.ndarray:
        thigh = float(np.clip(thigh, self._THIGH_LO, self._THIGH_HI))
        calf = -2.0 * thigh
        q = np.zeros(12, dtype=np.float64)
        for i in range(4):
            q[i * 3 + 0] = 0.0
            q[i * 3 + 1] = thigh
            q[i * 3 + 2] = calf
        return np.clip(q, self._joint_lo, self._joint_hi)

    def _hold_posture(self, q_hold: np.ndarray, secs: float) -> None:
        t0 = float(self.data.time)
        while float(self.data.time) - t0 < secs:
            self.apply_pd_posture(q_hold)
            self.step(1)

    def _settle_home(self) -> None:
        """Bring the robot to a supported standing stance at call start."""
        m, mj = self.model, self._mj
        if int(m.nkey) > 0:
            mj.mj_resetDataKeyframe(m, self.data, 0)
            mj.mj_forward(m, self.data)
        self.stand_up(1.0)
        self._hold_posture(self.get_joint_positions(), 0.3)
