# SPDX-License-Identifier: Apache-2.0
"""Generated ANYmal-C capability driver.

Robot subclass of QuadrupedPDGaitSkeleton implementing the public capability
contract (G1..G5). The skeleton supplies low-level joint-position control,
native planar-velocity gait, and state queries; this driver builds
request-dependent closed-loop controllers on top of the SAME model/data.

All timing uses data.time (simulation seconds). No live qpos/qvel is ever set
to achieve an action; every motion is produced by native actuator commands
that advance real physics.

ANYmal-C spawns high (~0.62 m) and must fold its legs to settle onto a stable
stance at ~0.4 m. Each capability first drives to a stable low-velocity stance
so terminal settling / vertical-speed gates are met.
"""
from __future__ import annotations

import math

import numpy as np

from auto_adapter.skeletons.quadruped_pd_gait import QuadrupedPDGaitSkeleton
from auto_adapter.skeletons.base import QuadrupedSpec


class CapabilityError(RuntimeError):
    """Bounded capability failure raised when a request cannot be honored."""


_LEG_ORDER = ("LF", "RF", "LH", "RH")

# Standing posture (hip=0, thigh, calf) per leg. Front legs and hind legs use
# mirrored thigh/calf signs so the whole body stands level at ~0.4 m.
_STAND_THIGH = 0.6
_STAND_CALF = -1.1
_STAND = [
    0.0, _STAND_THIGH, _STAND_CALF,   # LF
    0.0, _STAND_THIGH, _STAND_CALF,   # RF
    0.0, -_STAND_THIGH, -_STAND_CALF,  # LH
    0.0, -_STAND_THIGH, -_STAND_CALF,  # RH
]


def _leg_joint_names():
    return {leg: [f"{leg}_HAA", f"{leg}_HFE", f"{leg}_KFE"] for leg in _LEG_ORDER}


def build():
    """Module-level entry point required by the framework."""
    spec = QuadrupedSpec(
        base_body_name="base",
        leg_joint_names=_leg_joint_names(),
        leg_actuator_names=_leg_joint_names(),
        home_qpos=list(_STAND),
        kp=60.0,
        kd=2.0,
        gait_freq_hz=1.5,
        swing_amp_thigh=0.25,
        swing_amp_calf=0.30,
        thigh_forward_sign=-1.0,
        body_height_target=0.40,
        vx_max=0.4,
        vy_max=0.4,
        vyaw_max=1.0,
        sim_dt=0.002,
        actuation="joint_position",
    )
    return Robot.from_mjcf("mjcf.xml", spec=spec)


class Robot(QuadrupedPDGaitSkeleton):
    """ANYmal-C quadruped capability robot."""

    def __init__(self, model, data, spec: QuadrupedSpec) -> None:
        super().__init__(model, data, spec)
        self._stand = np.clip(
            np.array(_STAND, dtype=np.float64), self._joint_lo, self._joint_hi
        )
        self._settled = False

    # ── state helpers ──────────────────────────────────────────────
    def _time(self) -> float:
        return float(self.data.time)

    def _dt(self) -> float:
        return float(self.model.opt.timestep)

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

    def _roll_pitch(self) -> tuple:
        _, R = self.get_base_pose()
        pitch = float(math.asin(max(-1.0, min(1.0, -R[2, 0]))))
        roll = float(math.atan2(R[2, 1], R[2, 2]))
        return roll, pitch

    @staticmethod
    def _wrap(a: float) -> float:
        return float((a + math.pi) % (2.0 * math.pi) - math.pi)

    def _planar(self):
        pos, _ = self.get_base_pose()
        return float(pos[0]), float(pos[1])

    def _support_ok(self) -> bool:
        roll, pitch = self._roll_pitch()
        h = self.get_body_height()
        if not math.isfinite(h) or h < 0.12:
            return False
        if abs(roll) > 1.0 or abs(pitch) > 1.0:
            return False
        return True

    def _check_number(self, x, lo, hi, name):
        v = float(x)
        if not math.isfinite(v):
            raise CapabilityError(f"{name} must be finite")
        if v < lo - 1e-6 or v > hi + 1e-6:
            raise CapabilityError(f"{name}={v} out of bounds [{lo}, {hi}]")
        return v

    # ── trot gait generator ────────────────────────────────────────
    _FREQ = 1.2          # gait frequency (Hz)
    _LIFT = 0.6          # calf swing lift
    # Measured feedforward gains (unit command -> body response):
    #   fwd command produces -body_x motion  => forward_sign = -1
    #   lat command produces +body_y motion
    #   turn command produces -yaw            => turn_sign   = -1
    _GAIN_FWD = 0.50     # m/s per unit fwd command (magnitude)
    _GAIN_LAT = 0.70     # m/s per unit lat command
    _GAIN_TURN = 0.80    # rad/s per unit turn command (magnitude)
    _MAX_CMD = 0.35      # command amplitude cap for stability

    def _trot_posture(self, gt: float, fwd: float, lat: float, turn: float) -> np.ndarray:
        q = self._stand.copy()
        ph0 = 2.0 * math.pi * self._FREQ * gt
        for i, leg in enumerate(_LEG_ORDER):
            ph = ph0 + (0.0 if i in (0, 3) else math.pi)
            s = math.sin(ph)
            c = math.cos(ph)
            b = i * 3
            hind = -1.0 if leg[1] == "H" else 1.0
            left = 1.0 if leg[0] == "L" else -1.0
            q[b + 0] += -lat * c
            q[b + 1] += -(fwd + turn * left) * c
            q[b + 2] += hind * (-self._LIFT) * max(0.0, s)
        return np.clip(q, self._joint_lo, self._joint_hi)

    def _drive_trot(self, fwd: float, lat: float, turn: float) -> None:
        gt = getattr(self, "_gait_time", 0.0)
        self.apply_pd_posture(self._trot_posture(gt, fwd, lat, turn))
        self.step(1)
        self._gait_time = (gt + self._dt()) % (1.0 / self._FREQ)
        if not self._support_ok():
            raise CapabilityError("support integrity lost during locomotion")

    def _ensure_stance(self) -> None:
        """Fold legs from the high spawn onto a stable stance once."""
        if self._settled:
            return
        self._finite_state()
        dt = self._dt()
        win = max(1, int(0.3 / dt))
        hist: list = []
        t0 = self._time()
        while self._time() - t0 < 4.0:
            self.apply_pd_posture(self._stand)
            self.step(1)
            hist.append(self.get_body_height())
            if len(hist) > win:
                hist.pop(0)
            if (self._time() - t0 >= 1.2 and len(hist) == win
                    and (max(hist) - min(hist)) < 0.003):
                break
            if not self._support_ok():
                raise CapabilityError("support integrity lost while settling")
        self._settled = True
        self._gait_time = 0.0

    def _hold_stance(self, secs: float) -> None:
        n = max(1, int(secs / self._dt()))
        for _ in range(n):
            self.apply_pd_posture(self._stand)
            self.step(1)
            if not self._support_ok():
                raise CapabilityError("support integrity lost while holding")

    # ── G1: track_planar_twist ─────────────────────────────────────
    def track_planar_twist(self, request):
        lv = request["linear_velocity_body_m_s"]
        if not (isinstance(lv, (list, tuple)) and len(lv) == 2):
            raise CapabilityError("linear_velocity_body_m_s must have 2 items")
        vx = self._check_number(lv[0], -0.4, 0.4, "vx")
        vy = self._check_number(lv[1], -0.4, 0.4, "vy")
        yaw_rate = self._check_number(request["yaw_rate_rad_s"], -1.0, 1.0, "yaw_rate")
        dur = self._check_number(request["duration_s"], 2.0, 4.0, "duration_s")
        self._ensure_stance()
        # feedforward commands from measured gains
        ff_fwd = -vx / self._GAIN_FWD
        ff_lat = vy / self._GAIN_LAT
        ff_turn = -yaw_rate / self._GAIN_TURN
        i_fwd = i_lat = i_turn = 0.0
        t0 = self._time()
        deadline = t0 + dur
        while self._time() < deadline:
            tw = self.get_base_twist()
            vb = np.asarray(tw["linear_body_yaw_m_s"], dtype=float)
            yr = float(tw["yaw_rate_rad_s"])
            e_vx = vx - float(vb[0])
            e_vy = vy - float(vb[1])
            e_yr = yaw_rate - yr
            i_fwd = float(np.clip(i_fwd - 0.4 * e_vx * self._dt(), -0.3, 0.3))
            i_lat = float(np.clip(i_lat + 0.4 * e_vy * self._dt(), -0.3, 0.3))
            i_turn = float(np.clip(i_turn - 0.4 * e_yr * self._dt(), -0.3, 0.3))
            fwd = float(np.clip(ff_fwd - 0.6 * e_vx + i_fwd, -self._MAX_CMD, self._MAX_CMD))
            lat = float(np.clip(ff_lat + 0.6 * e_vy + i_lat, -self._MAX_CMD, self._MAX_CMD))
            turn = float(np.clip(ff_turn - 0.6 * e_yr + i_turn, -self._MAX_CMD, self._MAX_CMD))
            self._drive_trot(fwd, lat, turn)
        tw = self.get_base_twist()
        vb = np.asarray(tw["linear_body_yaw_m_s"], dtype=float)
        return {
            "status": "ok",
            "achieved_body_velocity_m_s": [float(vb[0]), float(vb[1])],
            "achieved_yaw_rate_rad_s": float(tw["yaw_rate_rad_s"]),
            "sim_time_s": self._time(),
        }

    # ── planar go-to controller (world frame) ──────────────────────
    def _walk_to(self, wx: float, wy: float, deadline: float,
                 pos_tol: float, final_yaw=None) -> bool:
        """Turn-then-go toward world point; optional terminal yaw alignment."""
        while self._time() < deadline:
            px, py = self._planar()
            yaw = self._yaw()
            dx, dy = wx - px, wy - py
            dist = math.hypot(dx, dy)
            if dist <= pos_tol:
                break
            heading = math.atan2(dy, dx)
            herr = self._wrap(heading - yaw)
            # turn command: turn -> -yaw, so cmd = -k*herr / gain
            turn = float(np.clip(-1.2 * herr / self._GAIN_TURN,
                                 -self._MAX_CMD, self._MAX_CMD))
            face = max(0.0, math.cos(herr))
            # forward: +body_x needs fwd = -speed/gain
            spd = min(0.30, 0.9 * dist)
            fwd = -(spd / self._GAIN_FWD) * face
            if dist < 0.15:
                fwd *= max(0.4, dist / 0.15)
            self._drive_trot(fwd, 0.0, turn)
        # optional terminal yaw alignment
        if final_yaw is not None:
            while self._time() < deadline:
                yerr = self._wrap(final_yaw - self._yaw())
                px, py = self._planar()
                if abs(yerr) <= 0.05 and math.hypot(wx - px, wy - py) <= pos_tol:
                    break
                turn = float(np.clip(-1.2 * yerr / self._GAIN_TURN,
                                     -self._MAX_CMD, self._MAX_CMD))
                self._drive_trot(0.0, 0.0, turn)
        px, py = self._planar()
        return math.hypot(wx - px, wy - py) <= pos_tol

    # ── G2: move_body_relative_pose ────────────────────────────────
    def move_body_relative_pose(self, request):
        tr = request["translation_initial_yaw_m"]
        if not (isinstance(tr, (list, tuple)) and len(tr) == 2):
            raise CapabilityError("translation_initial_yaw_m must have 2 items")
        tx = self._check_number(tr[0], -0.3, 0.3, "tx")
        ty = self._check_number(tr[1], -0.3, 0.3, "ty")
        dyaw = self._check_number(request["yaw_delta_rad"], -0.6, 0.6, "yaw_delta_rad")
        max_dur = self._check_number(request["max_duration_s"], 0.25, 8.0, "max_duration_s")
        self._ensure_stance()
        px0, py0 = self._planar()
        yaw0 = self._yaw()
        c, s = math.cos(yaw0), math.sin(yaw0)
        # target in world = origin + R(yaw0) * translation
        wx = px0 + c * tx - s * ty
        wy = py0 + s * tx + c * ty
        target_yaw = self._wrap(yaw0 + dyaw)
        hold = 0.5
        deadline = self._time() + max_dur
        reached = self._walk_to(wx, wy, deadline - hold, 0.07, final_yaw=target_yaw)
        # terminal settle & hold
        self._hold_stance(min(hold, max(0.0, deadline - self._time())))
        px, py = self._planar()
        perr = math.hypot(wx - px, wy - py)
        yerr = abs(self._wrap(target_yaw - self._yaw()))
        if perr > 0.1 + 1e-3 and self._time() >= deadline:
            raise CapabilityError(f"pose not settled: perr={perr:.3f}")
        return {
            "status": "ok",
            "position_error_m": float(perr),
            "yaw_error_rad": float(yerr),
            "sim_time_s": self._time(),
        }

    # ── G3: trace_planar_path ──────────────────────────────────────
    def trace_planar_path(self, request):
        wps = request["waypoints_initial_yaw_m"]
        if not (isinstance(wps, (list, tuple)) and 2 <= len(wps) <= 8):
            raise CapabilityError("waypoints_initial_yaw_m must have 2..8 items")
        pts = []
        for w in wps:
            if not (isinstance(w, (list, tuple)) and len(w) == 2):
                raise CapabilityError("each waypoint must have 2 items")
            wx = self._check_number(w[0], -0.4, 0.4, "wp_x")
            wy = self._check_number(w[1], -0.4, 0.4, "wp_y")
            pts.append((wx, wy))
        max_dur = self._check_number(request["max_duration_s"], 0.25, 8.0, "max_duration_s")
        self._ensure_stance()
        px0, py0 = self._planar()
        yaw0 = self._yaw()
        c, s = math.cos(yaw0), math.sin(yaw0)
        world_pts = [(px0 + c * x - s * y, py0 + s * x + c * y) for (x, y) in pts]
        hold = 0.5
        deadline = self._time() + max_dur
        for k, (wx, wy) in enumerate(world_pts):
            last = (k == len(world_pts) - 1)
            budget = deadline - hold if last else deadline
            tol = 0.07 if last else 0.09
            self._walk_to(wx, wy, budget, tol)
            if self._time() >= deadline:
                break
        self._hold_stance(min(hold, max(0.0, deadline - self._time())))
        px, py = self._planar()
        wx, wy = world_pts[-1]
        eperr = math.hypot(wx - px, wy - py)
        if eperr > 0.1 + 1e-3 and self._time() >= deadline:
            raise CapabilityError(f"endpoint not reached: err={eperr:.3f}")
        return {
            "status": "ok",
            "endpoint_error_m": float(eperr),
            "waypoints": len(world_pts),
            "sim_time_s": self._time(),
        }

    # ── height stance mapping ──────────────────────────────────────
    def _stance_scaled(self, scale: float) -> np.ndarray:
        th = 0.6 * scale
        ca = -1.1 * scale
        q = np.array([0.0, th, ca, 0.0, th, ca,
                      0.0, -th, -ca, 0.0, -th, -ca], dtype=np.float64)
        return np.clip(q, self._joint_lo, self._joint_hi)

    # ── G4: set_body_height ────────────────────────────────────────
    def set_body_height(self, request):
        target = self._check_number(
            request["target_height_m"], 0.2989979050104695, 0.44849685751570423,
            "target_height_m")
        max_dur = self._check_number(request["max_duration_s"], 0.25, 8.0, "max_duration_s")
        self._ensure_stance()
        px0, py0 = self._planar()
        yaw0 = self._yaw()
        # approximate scale from linear fit: h ~ 0.439 at s=1.0, slope ~ -0.4/0.4
        # invert with online correction toward measured height.
        scale = 1.0 + (0.439 - target) / 0.40
        scale = float(np.clip(scale, 0.6, 1.5))
        hold = 0.5
        deadline = self._time() + max_dur
        dt = self._dt()
        win = max(1, int(hold / dt))
        hist: list = []
        while self._time() < deadline:
            h = self.get_body_height()
            err = h - target
            # positive err (too high) -> increase scale (lower body)
            scale = float(np.clip(scale + 2.5 * err, 0.6, 1.5))
            self.apply_pd_posture(self._stance_scaled(scale))
            self.step(1)
            if not self._support_ok():
                raise CapabilityError("support integrity lost during height change")
            hist.append(h)
            if len(hist) > win:
                hist.pop(0)
            if (len(hist) == win and abs(np.mean(hist) - target) <= 0.02
                    and (max(hist) - min(hist)) < 0.004):
                break
        # terminal hold at settled scale
        self._hold_scaled(scale, min(hold, max(0.0, deadline - self._time())))
        h = self.get_body_height()
        roll, pitch = self._roll_pitch()
        px, py = self._planar()
        disp = math.hypot(px - px0, py - py0)
        if abs(h - target) > 0.03 + 1e-3 and self._time() >= deadline:
            raise CapabilityError(f"height not settled: err={abs(h-target):.3f}")
        return {
            "status": "ok",
            "height_error_m": float(abs(h - target)),
            "roll_pitch_rad": [float(roll), float(pitch)],
            "planar_displacement_m": float(disp),
            "yaw_drift_rad": float(abs(self._wrap(self._yaw() - yaw0))),
            "sim_time_s": self._time(),
        }

    def _hold_scaled(self, scale: float, secs: float) -> None:
        n = max(1, int(secs / self._dt()))
        q = self._stance_scaled(scale)
        for _ in range(n):
            self.apply_pd_posture(q)
            self.step(1)
            if not self._support_ok():
                raise CapabilityError("support integrity lost while holding height")

    # ── G5: hold_stable_stance ─────────────────────────────────────
    def hold_stable_stance(self, request):
        dur = self._check_number(request["duration_s"], 1.0, 2.0, "duration_s")
        self._finite_state()
        # recovery: drive toward stable stance quickly (within ~0.5 s target)
        px0, py0 = self._planar()
        h0 = None
        t0 = self._time()
        rec_deadline = t0 + 0.5
        while self._time() < rec_deadline:
            self.apply_pd_posture(self._stand)
            self.step(1)
            if not self._support_ok():
                raise CapabilityError("support integrity lost during recovery")
        roll, pitch = self._roll_pitch()
        if max(abs(roll), abs(pitch)) > 0.0524:
            # continue stabilizing briefly if still tilted
            extra = t0 + 1.0
            while self._time() < extra:
                self.apply_pd_posture(self._stand)
                self.step(1)
                if not self._support_ok():
                    raise CapabilityError("support integrity lost during recovery")
        # bounded hold for the requested duration
        h0 = self.get_body_height()
        px0, py0 = self._planar()
        hold_start = self._time()
        n = max(1, int(dur / self._dt()))
        for _ in range(n):
            self.apply_pd_posture(self._stand)
            self.step(1)
            if not self._support_ok():
                raise CapabilityError("support integrity lost during hold")
        roll, pitch = self._roll_pitch()
        vel = self.get_base_velocity()["linear"]
        return {
            "status": "ok",
            "roll_pitch_rad": [float(roll), float(pitch)],
            "height_drift_m": float(abs(self.get_body_height() - h0)),
            "planar_drift_m": float(math.hypot(self._planar()[0] - px0,
                                               self._planar()[1] - py0)),
            "terminal_vertical_speed_m_s": float(abs(vel[2])),
            "sim_time_s": self._time(),
        }
