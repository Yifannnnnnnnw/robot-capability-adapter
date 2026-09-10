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

    # height posture calibration (calf = -2*thigh), from real MJCF probing
    _THIGH_LO = 0.60      # -> tallest
    _THIGH_HI = 1.05      # -> shortest

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

    def _hold_posture(self, secs: float) -> None:
        """Damp-and-hold the current joint posture for ``secs`` sim seconds."""
        q_hold = self.get_joint_positions()
        t0 = float(self.data.time)
        while float(self.data.time) - t0 < secs:
            self.apply_pd_posture(q_hold)
            self.step(1)

    def _height_posture(self, thigh: float) -> np.ndarray:
        thigh = float(np.clip(thigh, self._THIGH_LO, self._THIGH_HI))
        calf = -2.0 * thigh
        q = np.zeros(12, dtype=np.float64)
        for i in range(4):
            q[i * 3 + 0] = 0.0
            q[i * 3 + 1] = thigh
            q[i * 3 + 2] = calf
        return np.clip(q, self._joint_lo, self._joint_hi)

    def _settle_home(self) -> None:
        """Bring the robot to a supported standing stance at call start."""
        m, mj = self.model, self._mj
        if int(m.nkey) > 0:
            mj.mj_resetDataKeyframe(m, self.data, 0)
            mj.mj_forward(m, self.data)
        self.stand_up(1.0)
        self._hold_posture(0.3)

    def _twist_report(self) -> dict:
        tw = self.get_base_twist()
        vb = np.asarray(tw["linear_body_yaw_m_s"], dtype=np.float64)
        return {"linear_velocity_body_m_s": [float(vb[0]), float(vb[1])],
                "yaw_rate_rad_s": float(tw["yaw_rate_rad_s"])}

    # ── low-level twist tracking (uses the skeleton gait engine) ───────────
    def _drive_twist(self, vx: float, vy: float, wz: float) -> None:
        """Write one gait target for a body-frame twist and step once."""
        timestep = float(self.model.opt.timestep)
        gait_period = 1.0 / float(self.spec.gait_freq_hz)
        vx = float(np.clip(vx, -self.spec.vx_max, self.spec.vx_max))
        vy = float(np.clip(vy, -self.spec.vy_max, self.spec.vy_max))
        wz = float(np.clip(wz, -self.spec.vyaw_max, self.spec.vyaw_max))
        q = self._gait_posture(self._gait_time, vx, vy, wz)
        self.apply_pd_posture(q)
        self.step(1)
        self._gait_time = (self._gait_time + timestep) % gait_period

    def _run_twist(self, vx: float, vy: float, wz: float, secs: float,
                   yaw_ref0: Optional[float] = None) -> None:
        """Closed-loop body-frame twist tracking for ``secs`` sim seconds."""
        yaw0 = self._base_yaw() if yaw_ref0 is None else float(yaw_ref0)
        vbx_f = vby_f = wz_f = 0.0
        alpha = 0.04
        t0 = float(self.data.time)
        while float(self.data.time) - t0 < secs:
            if not self._state_ok():
                self.stop(0.1)
                raise CapabilityError("public base state became non-finite")
            tt = float(self.data.time) - t0
            tw = self.get_base_twist()
            vb = np.asarray(tw["linear_body_yaw_m_s"], dtype=np.float64)
            vbx_f = (1 - alpha) * vbx_f + alpha * float(vb[0])
            vby_f = (1 - alpha) * vby_f + alpha * float(vb[1])
            wz_f = (1 - alpha) * wz_f + alpha * float(tw["yaw_rate_rad_s"])
            href = yaw0 + wz * tt
            herr = _wrap(href - self._base_yaw())
            wz_cmd = wz + 0.8 * herr + 0.5 * (wz - wz_f)
            vx_cmd = vx + 0.7 * (vx - vbx_f)
            vy_cmd = vy + 0.7 * (vy - vby_f)
            self._drive_twist(vx_cmd, vy_cmd, wz_cmd)

    # ── planar point/pose regulation in a fixed initial-yaw frame ──────────
    def _goto_point(self, origin, yaw0, tx, ty, deadline_t,
                    pos_tol=0.06, yaw_target=None) -> bool:
        """Drive toward target (tx,ty) in the fixed initial-yaw frame."""
        c0, s0 = math.cos(yaw0), math.sin(yaw0)
        while float(self.data.time) < deadline_t:
            if not self._state_ok():
                self.stop(0.1)
                raise CapabilityError("public base state became non-finite")
            xy = self._planar_xy() - np.asarray(origin, dtype=np.float64)
            fx = c0 * xy[0] + s0 * xy[1]
            fy = -s0 * xy[0] + c0 * xy[1]
            ex, ey = tx - fx, ty - fy
            dist = math.hypot(ex, ey)
            yaw_now = self._base_yaw()
            if dist <= pos_tol:
                if yaw_target is None:
                    return True
                if abs(_wrap(yaw_target - yaw_now)) <= 0.06:
                    return True
            ewx = c0 * ex - s0 * ey
            ewy = s0 * ex + c0 * ey
            desired_heading = math.atan2(ewy, ewx)
            if dist > pos_tol:
                head_err = _wrap(desired_heading - yaw_now)
                speed = min(0.35, 0.9 * dist)
                vx = 0.0 if abs(head_err) > 0.6 else speed * math.cos(head_err)
                vy = 0.0
                wz = float(np.clip(1.8 * head_err, -0.9, 0.9))
            else:
                he = _wrap((yaw_target if yaw_target is not None else yaw_now)
                           - yaw_now)
                vx = vy = 0.0
                wz = float(np.clip(1.5 * he, -0.9, 0.9))
            self._drive_twist(vx, vy, wz)
        return False

    # ── G1: track_planar_twist ─────────────────────────────────────────────
    def track_planar_twist(self, request):
        if not isinstance(request, dict):
            raise CapabilityError("request must be a mapping")
        try:
            lin = list(request["linear_velocity_body_m_s"])
            wz = float(request["yaw_rate_rad_s"])
            dur = float(request["duration_s"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CapabilityError(f"invalid G1 request: {exc}")
        if len(lin) != 2:
            raise CapabilityError("linear_velocity_body_m_s must have 2 items")
        vx, vy = float(lin[0]), float(lin[1])
        if not _finite(vx, vy, wz, dur):
            raise CapabilityError("G1 request values must be finite")
        if abs(vx) > self._G1_VLIN or abs(vy) > self._G1_VLIN:
            raise CapabilityError("linear velocity out of bounds")
        if abs(wz) > self._G1_VYAW:
            raise CapabilityError("yaw rate out of bounds")
        if not (2.0 <= dur <= 4.0):
            raise CapabilityError("duration out of bounds")
        if not self._state_ok():
            raise CapabilityError("public base state unavailable")
        self._run_twist(vx, vy, wz, dur)
        self.stop(0.2)
        return {"capability_id": "G1", "status": "ok", "duration_s": dur,
                "final_twist": self._twist_report()}

    # ── G2: move_body_relative_pose ────────────────────────────────────────
    def move_body_relative_pose(self, request):
        if not isinstance(request, dict):
            raise CapabilityError("request must be a mapping")
        try:
            tr = list(request["translation_initial_yaw_m"])
            dyaw = float(request["yaw_delta_rad"])
            mdur = float(request["max_duration_s"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CapabilityError(f"invalid G2 request: {exc}")
        if len(tr) != 2:
            raise CapabilityError("translation must have 2 items")
        tx, ty = float(tr[0]), float(tr[1])
        if not _finite(tx, ty, dyaw, mdur):
            raise CapabilityError("G2 values must be finite")
        if abs(tx) > 0.3 or abs(ty) > 0.3 or abs(dyaw) > 0.6:
            raise CapabilityError("G2 request out of bounds")
        if not (0.25 <= mdur <= 8.0):
            raise CapabilityError("max_duration out of bounds")
        if not self._state_ok():
            raise CapabilityError("public base state unavailable")
        origin = self._planar_xy()
        yaw0 = self._base_yaw()
        hold = 0.5
        deadline = float(self.data.time) + max(0.0, mdur - hold)
        yaw_tgt = _wrap(yaw0 + dyaw)
        arrived = self._goto_point(origin, yaw0, tx, ty, deadline,
                                   pos_tol=0.05, yaw_target=yaw_tgt)
        self.stop(hold)
        if not arrived:
            raise CapabilityError("G2 pose not settled within max_duration")
        return {"capability_id": "G2", "status": "ok", "settled": True}

    # ── G3: trace_planar_path ──────────────────────────────────────────────
    def trace_planar_path(self, request):
        if not isinstance(request, dict):
            raise CapabilityError("request must be a mapping")
        try:
            wps = list(request["waypoints_initial_yaw_m"])
            mdur = float(request["max_duration_s"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CapabilityError(f"invalid G3 request: {exc}")
        if not (2 <= len(wps) <= 8):
            raise CapabilityError("waypoints count out of bounds")
        pts = []
        for w in wps:
            w = list(w)
            if len(w) != 2:
                raise CapabilityError("each waypoint must have 2 items")
            x, y = float(w[0]), float(w[1])
            if not _finite(x, y) or abs(x) > 0.4 or abs(y) > 0.4:
                raise CapabilityError("waypoint out of bounds")
            pts.append((x, y))
        if not (0.25 <= mdur <= 8.0):
            raise CapabilityError("max_duration out of bounds")
        if not self._state_ok():
            raise CapabilityError("public base state unavailable")
        origin = self._planar_xy()
        yaw0 = self._base_yaw()
        hold = 0.5
        end_t = float(self.data.time) + max(0.0, mdur - hold)
        n = len(pts)
        for i, (tx, ty) in enumerate(pts):
            tol = 0.06 if i < n - 1 else 0.05
            ok = self._goto_point(origin, yaw0, tx, ty, end_t,
                                  pos_tol=tol, yaw_target=None)
            if not ok:
                self.stop(hold)
                raise CapabilityError(
                    f"waypoint {i} not reached within duration")
        self.stop(hold)
        return {"capability_id": "G3", "status": "ok", "waypoints_visited": n}

    # ── G4: set_body_height ────────────────────────────────────────────────
    def set_body_height(self, request):
        if not isinstance(request, dict):
            raise CapabilityError("request must be a mapping")
        try:
            h = float(request["target_height_m"])
            mdur = float(request["max_duration_s"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CapabilityError(f"invalid G4 request: {exc}")
        if not _finite(h, mdur):
            raise CapabilityError("G4 values must be finite")
        if not (self._G4_H_MIN - 1e-6 <= h <= self._G4_H_MAX + 1e-6):
            raise CapabilityError("target_height out of bounds")
        if not (0.25 <= mdur <= 8.0):
            raise CapabilityError("max_duration out of bounds")
        if not self._state_ok():
            raise CapabilityError("public base/attitude state unavailable")
        hold = 0.5
        deadline = float(self.data.time) + max(0.0, mdur - hold)
        thigh = 0.65 + (0.323 - h) / (0.323 - 0.218) * (1.0 - 0.65)
        thigh = float(np.clip(thigh, self._THIGH_LO, self._THIGH_HI))
        while float(self.data.time) < deadline:
            if not self._state_ok():
                self.stop(0.1)
                raise CapabilityError("public state became non-finite")
            err = h - self.get_body_height()
            thigh -= 0.6 * err
            thigh = float(np.clip(thigh, self._THIGH_LO, self._THIGH_HI))
            self.apply_pd_posture(self._height_posture(thigh))
            self.step(1)
        t0 = float(self.data.time)
        while float(self.data.time) - t0 < hold:
            err = h - self.get_body_height()
            thigh -= 0.3 * err
            thigh = float(np.clip(thigh, self._THIGH_LO, self._THIGH_HI))
            self.apply_pd_posture(self._height_posture(thigh))
            self.step(1)
        if abs(h - self.get_body_height()) > 0.03:
            raise CapabilityError("height not settled within max_duration")
        return {"capability_id": "G4", "status": "ok",
                "body_height_m": self.get_body_height()}

    # ── G5: hold_stable_stance ─────────────────────────────────────────────
    def hold_stable_stance(self, request):
        if not isinstance(request, dict):
            raise CapabilityError("request must be a mapping")
        try:
            dur = float(request["duration_s"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CapabilityError(f"invalid G5 request: {exc}")
        if not _finite(dur):
            raise CapabilityError("duration must be finite")
        if not (1.0 <= dur <= 2.0):
            raise CapabilityError("duration out of bounds")
        if not self._state_ok():
            raise CapabilityError("public state unavailable after disturbance")
        # Recovery: drive to a stable standing posture (~0.267 m stance).
        recover_deadline = float(self.data.time) + 0.5
        thigh = 0.85
        while float(self.data.time) < recover_deadline:
            if not self._state_ok():
                self.stop(0.1)
                raise CapabilityError("public state became non-finite")
            self.apply_pd_posture(self._height_posture(thigh))
            self.step(1)
            roll, pitch = self._roll_pitch()
            if max(abs(roll), abs(pitch)) <= 0.0524:
                break
        # Bounded hold: keep the recovered stance fixed for the duration.
        q_hold = self._height_posture(thigh)
        t0 = float(self.data.time)
        while float(self.data.time) - t0 < dur:
            if not self._state_ok():
                self.stop(0.1)
                raise CapabilityError("public state became non-finite")
            self.apply_pd_posture(q_hold)
            self.step(1)
        roll, pitch = self._roll_pitch()
        if max(abs(roll), abs(pitch)) > 0.0873:
            raise CapabilityError("stable stance not maintained")
        return {"capability_id": "G5", "status": "ok",
                "roll_rad": roll, "pitch_rad": pitch,
                "body_height_m": self.get_body_height()}


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
        swing_amp_thigh=0.14,
        swing_amp_calf=0.20,
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
