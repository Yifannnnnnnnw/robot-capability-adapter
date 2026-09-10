# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for the Unitree A1 quadruped.

Subclasses ``QuadrupedPDGaitSkeleton`` (native joint-position actuators) and
implements the public capability contract (G1..G5). All motion is produced by
writing bounded joint-position targets to ``data.ctrl`` and advancing the same
MuJoCo model/data via ``self.step``. Deadlines and holds are measured with
``data.time`` (simulation seconds). Kinematic scratch computations, when used,
operate on a cloned ``MjData`` and never mutate the live state.
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

    # ── observation helpers ───────────────────────────────────────────
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

    def _hold_posture(self, secs: float) -> None:
        """Damp-and-hold the current joint posture for ``secs`` sim seconds."""
        q_hold = self.get_joint_positions()
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
        self._hold_posture(0.3)


def build() -> "Robot":
    home = [
        0.0, 0.9, -1.8,   # FR
        0.0, 0.9, -1.8,   # FL
        0.0, 0.9, -1.8,   # RR
        0.0, 0.9, -1.8,   # RL
    ]
    spec = QuadrupedSpec(
        base_body_name="trunk",
        leg_joint_names={
            "FR": ["FR_hip_joint", "FR_thigh_joint", "FR_calf_joint"],
            "FL": ["FL_hip_joint", "FL_thigh_joint", "FL_calf_joint"],
            "RR": ["RR_hip_joint", "RR_thigh_joint", "RR_calf_joint"],
            "RL": ["RL_hip_joint", "RL_thigh_joint", "RL_calf_joint"],
        },
        leg_actuator_names={
            "FR": ["FR_hip", "FR_thigh", "FR_calf"],
            "FL": ["FL_hip", "FL_thigh", "FL_calf"],
            "RR": ["RR_hip", "RR_thigh", "RR_calf"],
            "RL": ["RL_hip", "RL_thigh", "RL_calf"],
        },
        home_qpos=home,
        kp=30.0,
        kd=1.5,
        gait_freq_hz=2.0,
        swing_amp_thigh=0.12,
        swing_amp_calf=0.18,
        thigh_forward_sign=-1.0,
        body_height_target=0.27,
        vx_max=0.4,
        vy_max=0.4,
        vyaw_max=1.0,
        sim_dt=0.002,
        actuation="joint_position",
    )
    robot = Robot.from_mjcf("mjcf.xml", spec=spec)
    robot._settle_home()
    return robot


    # ── gait engine ───────────────────────────────────────────────────
    _GAIT_FREQ = 3.0
    _CALF_AMP = 0.15
    _SX_MAX = 0.30
    _SY_MAX = 0.30
    _SYAW_MAX = 0.18

    def _gait_targets(self, tt: float, sx: float, sy: float, syaw: float) -> np.ndarray:
        """Build one trot joint-position target for phase-time ``tt``.

        Stance/swing come from a diagonal-pair trot (FR+RL vs FL+RR).  The
        thigh reaches with ``-cos`` (forward push through stance), the calf
        lifts during swing, and the hip supplies lateral + differential-yaw
        drive.  All values are clipped to model ranges inside ``apply_pd``.
        """
        home = self._home_q
        legs = self._leg_order
        ph = {legs[0]: 0.0, legs[3]: 0.0, legs[1]: math.pi, legs[2]: math.pi}
        omega = 2.0 * math.pi * self._GAIT_FREQ
        q = home.copy()
        for i, leg in enumerate(legs):
            phi = omega * tt + ph[leg]
            c = math.cos(phi)
            sw = max(0.0, math.sin(phi))
            b = i * 3
            front = 1.0 if i < 2 else -1.0
            q[b + 1] = home[b + 1] + sx * (-c)
            q[b + 2] = home[b + 2] - self._CALF_AMP * sw
            q[b + 0] = home[b + 0] + (sy + front * syaw) * (-c)
        return q

    def _run_twist(self, vx: float, vy: float, wz: float, secs: float,
                   yaw_ref0: Optional[float] = None) -> None:
        """Closed-loop body-frame twist tracking for ``secs`` sim seconds.

        Maintains a reference heading advancing at ``wz`` and regulates it,
        while proportional feedback on a low-pass body velocity trims the
        step-length / lateral drive.  Raises on non-finite state.
        """
        yaw0 = self._base_yaw() if yaw_ref0 is None else float(yaw_ref0)
        vbx_f = 0.0
        vby_f = 0.0
        alpha = 0.02
        t0 = float(self.data.time)
        while float(self.data.time) - t0 < secs:
            if not self._state_ok():
                self.stop(0.1)
                raise CapabilityError("public base state became non-finite")
            tt = float(self.data.time) - t0
            tw = self.get_base_twist()
            vb = tw["linear_body_yaw_m_s"]
            vbx_f = (1 - alpha) * vbx_f + alpha * float(vb[0])
            vby_f = (1 - alpha) * vby_f + alpha * float(vb[1])
            href = yaw0 + wz * tt
            herr = _wrap(href - self._base_yaw())
            syaw = 0.12 * herr + 0.03 * wz
            sx = -vx / 2.0 - 0.4 * (vx - vbx_f)
            sy = vy / 1.8 + 0.4 * (vy - vby_f)
            sx = float(np.clip(sx, -self._SX_MAX, self._SX_MAX))
            sy = float(np.clip(sy, -self._SY_MAX, self._SY_MAX))
            syaw = float(np.clip(syaw, -self._SYAW_MAX, self._SYAW_MAX))
            self.apply_pd_posture(self._gait_targets(tt, sx, sy, syaw))
            self.step(1)
