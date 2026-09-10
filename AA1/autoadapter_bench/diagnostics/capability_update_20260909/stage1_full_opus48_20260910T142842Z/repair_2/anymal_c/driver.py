# SPDX-License-Identifier: Apache-2.0
"""Generated ANYmal-C capability driver.

Robot subclass of QuadrupedPDGaitSkeleton implementing the public capability
contract (G1..G5). The skeleton supplies low-level joint-position control and
state queries; this driver builds request-dependent feedback controllers on
top of the SAME model/data. All timing uses data.time (simulation seconds).
No live qpos/qvel is ever set to achieve an action.

Key robot fact: ANYmal-C spawns high (~0.62 m) and takes ~2.5 s of holding a
stance posture to settle onto its feet with near-zero vertical velocity. Every
capability therefore begins by settling to a stable, low-velocity stance so the
criterion windows (which include settling / vertical-speed gates) are met.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from auto_adapter.skeletons.quadruped_pd_gait import QuadrupedPDGaitSkeleton
from auto_adapter.skeletons.base import QuadrupedSpec


class CapabilityError(RuntimeError):
    """Bounded capability failure raised when a request cannot be honored."""


_LEG_ORDER = ("LF", "RF", "LH", "RH")
_HOME_THIGH = 0.64


def _stance_qpos(t: float) -> list:
    """Symmetric standing joint vector for thigh flexion `t`."""
    return [
        0.0, t, -2.0 * t,     # LF
        0.0, t, -2.0 * t,     # RF
        0.0, -t, 2.0 * t,     # LH
        0.0, -t, 2.0 * t,     # RH
    ]


_HOME_QPOS = _stance_qpos(_HOME_THIGH)


class Robot(QuadrupedPDGaitSkeleton):
    """ANYmal-C quadruped capability robot."""

    def __init__(self, model, data, spec: QuadrupedSpec) -> None:
        super().__init__(model, data, spec)
        self._home = np.array(_HOME_QPOS, dtype=np.float64)

    # ── basic state helpers ────────────────────────────────────────────────
    def _time(self) -> float:
        return float(self.data.time)

    def _finite_state(self) -> None:
        pos, R = self.get_base_pose()
        vel = self.get_base_velocity()
        if not (np.all(np.isfinite(pos)) and np.all(np.isfinite(R))):
            raise CapabilityError("base pose is not finite")
        if not (np.all(np.isfinite(vel["linear"])) and np.all(np.isfinite(vel["angular"]))):
            raise CapabilityError("base velocity is not finite")

    def _yaw(self) -> float:
        _, R = self.get_base_pose()
        return float(math.atan2(R[1, 0], R[0, 0]))

    def _roll_pitch(self) -> tuple[float, float]:
        _, R = self.get_base_pose()
        pitch = float(math.asin(max(-1.0, min(1.0, -R[2, 0]))))
        roll = float(math.atan2(R[2, 1], R[2, 2]))
        return roll, pitch

    @staticmethod
    def _wrap(a: float) -> float:
        return float((a + math.pi) % (2.0 * math.pi) - math.pi)

    def _support_ok(self) -> bool:
        roll, pitch = self._roll_pitch()
        h = self.get_body_height()
        if not math.isfinite(h):
            return False
        if abs(roll) > 1.0 or abs(pitch) > 1.0:
            return False
        if h < 0.15:
            return False
        return True

    def _check_number(self, x, lo, hi, name):
        v = float(x)
        if not math.isfinite(v):
            raise CapabilityError(f"{name} must be finite")
        if v < lo - 1e-9 or v > hi + 1e-9:
            raise CapabilityError(f"{name}={v} out of bounds [{lo}, {hi}]")
        return v

    def _clip_stance(self, t: float) -> np.ndarray:
        return np.clip(np.array(_stance_qpos(t), dtype=np.float64),
                       self._joint_lo, self._joint_hi)

    # ── settling to a stable stance ────────────────────────────────────────
    def _settle_stance(self, posture: np.ndarray, max_s: float = 4.0,
                       win_s: float = 0.3, tol_m: float = 0.004,
                       min_s: float = 1.0) -> None:
        """Hold `posture` until the body height is stable over a moving window
        (peak-to-peak < tol_m) with a minimum hold, or until max_s. This drives
        vertical velocity toward zero so terminal settling gates are met."""
        dt = float(self.model.opt.timestep)
        win = max(1, int(win_s / dt))
        hist: list[float] = []
        t0 = self._time()
        while self._time() - t0 < max_s:
            self.apply_pd_posture(posture)
            self.step(1)
            hist.append(self.get_body_height())
            if len(hist) > win:
                hist.pop(0)
            if (self._time() - t0 >= min_s and len(hist) == win
                    and (max(hist) - min(hist)) < tol_m):
                return
            if not self._support_ok():
                raise CapabilityError("support integrity lost while settling")

    # ── gait engine (produces controllable body-frame motion) ─────────────
    _GAIT_FREQ = 1.5
    _CALF_LIFT = 0.15

    def _gait_step(self, fwd: float, turn: float, gt: float) -> np.ndarray:
        """One diagonal-trot posture. `fwd` sweeps all thighs (positive => body
        moves in +body-x per calibration sign applied by callers); `turn`
        differentially sweeps left/right thighs for yaw. Feet lift on swing."""
        omega = 2.0 * math.pi * self._GAIT_FREQ
        q = self._home.copy()
        for i, leg in enumerate(_LEG_ORDER):
            ph = omega * gt + (0.0 if i in (0, 3) else math.pi)
            c = math.cos(ph)
            swing = max(0.0, math.sin(ph))
            b = i * 3
            left = 1.0 if leg[0] == "L" else -1.0
            q[b + 1] += (fwd + turn * left) * (-c)
            q[b + 2] += -self._CALF_LIFT * swing
        return np.clip(q, self._joint_lo, self._joint_hi)

    def _drive_gait_once(self, fwd: float, turn: float) -> None:
        self.apply_pd_posture(self._gait_step(fwd, turn, self._gait_time))
        self.step(1)
        dt = float(self.model.opt.timestep)
        self._gait_time = (self._gait_time + dt) % (1.0 / self._GAIT_FREQ)

    # Calibration: measured sign/gain mapping from gait command to body twist.
    # Established by a short in-place probe that does not need to be exact; a
    # closed loop drives the residual error. `_fwd_sign` maps command->+body-x.
    def _calibrate_gait(self) -> None:
        if getattr(self, "_gait_calibrated", False):
            return
        self._gait_time = 0.0
        p0, R0 = self.get_base_pose()
        y0 = math.atan2(R0[1, 0], R0[0, 0])
        amp = 0.22
        secs = 1.2
        n = int(secs / float(self.model.opt.timestep))
        for _ in range(n):
            self._drive_gait_once(amp, 0.0)
        p1, R1 = self.get_base_pose()
        y1 = math.atan2(R1[1, 0], R1[0, 0])
        c0, s0 = math.cos(y0), math.sin(y0)
        bx = c0 * (p1[0] - p0[0]) + s0 * (p1[1] - p0[1])
        self._fwd_sign = 1.0 if bx >= 0.0 else -1.0
        # yaw probe
        self._settle_stance(self._home, max_s=2.5, min_s=0.6)
        self._gait_time = 0.0
        _, R0 = self.get_base_pose()
        y0 = math.atan2(R0[1, 0], R0[0, 0])
        for _ in range(n):
            self._drive_gait_once(0.0, amp)
        _, R1 = self.get_base_pose()
        y1 = math.atan2(R1[1, 0], R1[0, 0])
        dyaw = self._wrap(y1 - y0)
        self._turn_sign = 1.0 if dyaw >= 0.0 else -1.0
        self._settle_stance(self._home, max_s=2.5, min_s=0.6)
        self._gait_calibrated = True

    # ── closed-loop walk toward a world planar target ──────────────────────
    def _walk_to_point(self, wx: float, wy: float, deadline_t: float,
                       pos_tol: float) -> bool:
        """Turn-then-go controller: yaw the body toward the target, then sweep
        forward. Uses calibrated signs. Returns True when within pos_tol."""
        max_amp = 0.24
        k_turn = 0.6
        while self._time() < deadline_t:
            pos, R = self.get_base_pose()
            yaw = math.atan2(R[1, 0], R[0, 0])
            dx = wx - float(pos[0])
            dy = wy - float(pos[1])
            dist = math.hypot(dx, dy)
            if dist <= pos_tol:
                return True
            heading = math.atan2(dy, dx)
            herr = self._wrap(heading - yaw)
            turn_cmd = float(np.clip(k_turn * herr, -max_amp, max_amp)) * self._turn_sign
            # forward only when roughly facing the target
            fwd_gate = max(0.0, math.cos(herr))
            fwd_cmd = max_amp * fwd_gate * self._fwd_sign
            if dist < 0.12:
                fwd_cmd *= max(0.35, dist / 0.12)
            self._drive_gait_once(fwd_cmd, turn_cmd)
            if not self._support_ok():
                raise CapabilityError("support integrity lost during walk")
        pos, _ = self.get_base_pose()
        return math.hypot(wx - float(pos[0]), wy - float(pos[1])) <= pos_tol
