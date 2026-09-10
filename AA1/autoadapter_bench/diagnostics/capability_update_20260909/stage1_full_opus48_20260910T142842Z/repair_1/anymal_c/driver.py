# SPDX-License-Identifier: Apache-2.0
"""Generated ANYmal-C capability driver.

Robot subclass of QuadrupedPDGaitSkeleton implementing the public capability
contract (G1..G5). The skeleton supplies low-level joint-position control,
state queries, and a trot primitive; this driver builds request-dependent
feedback controllers on top of the SAME model/data. All timing uses
data.time (simulation seconds). No live qpos/qvel is ever set to achieve an
action; kinematic scratch calcs use a separate MjData copy.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from auto_adapter.skeletons.quadruped_pd_gait import QuadrupedPDGaitSkeleton
from auto_adapter.skeletons.base import QuadrupedSpec


class CapabilityError(RuntimeError):
    """Bounded capability failure raised when a request cannot be honored."""


# ANYmal-C leg order matches diagonal-pair-adjacent ordering used by the
# skeleton's auto trot: legs 0 and 3 (LF, RH) share one phase; legs 1 and 2
# (RF, LH) share the anti-phase.
#
# The standing posture is parameterized by a single thigh flexion `t`
# (calf = -2*t front, mirrored hind). Empirically settled body height falls
# smoothly with t over t in [0.55, 0.66] (0.42 m -> 0.35 m); larger t makes
# the shanks over-tuck and collapse. The home stance uses t = 0.64 -> ~0.387
# m, comfortably inside the G4 band [0.299, 0.448].
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
        # Scratch MjData for kinematic-only calculations (never stepped as the
        # live sim; used to read forward-kinematics on hypothetical states).
        self._scratch = self._mj.MjData(self.model)

    # ── helpers ──────────────────────────────────────────────────────────
    def _time(self) -> float:
        return float(self.data.time)

    def _finite_state(self) -> None:
        """Raise CapabilityError if public base state is not finite."""
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
        """Support integrity: torso upright and not collapsed to the floor."""
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

    # ── shared closed-loop planar velocity tracker ───────────────────────
    def _drive_velocity_once(self, vx_body, vy_body, yaw_rate) -> None:
        """Advance one sim step commanding a body-frame planar/yaw twist."""
        vx = float(np.clip(vx_body, -self.spec.vx_max, self.spec.vx_max))
        vy = float(np.clip(vy_body, -self.spec.vy_max, self.spec.vy_max))
        wz = float(np.clip(yaw_rate, -self.spec.vyaw_max, self.spec.vyaw_max))
        dt = float(self.model.opt.timestep)
        q_target = self._gait_posture(self._gait_time, vx, vy, wz)
        self.apply_pd_posture(q_target)
        self.step(1)
        self._gait_time = (self._gait_time + dt) % (1.0 / float(self.spec.gait_freq_hz))

    # ── G1: track_planar_twist ───────────────────────────────────────────
    def track_planar_twist(self, request):
        self._finite_state()
        lv = request["linear_velocity_body_m_s"]
        if not (isinstance(lv, (list, tuple)) and len(lv) == 2):
            raise CapabilityError("linear_velocity_body_m_s must have 2 items")
        vx = self._check_number(lv[0], -0.4, 0.4, "vx")
        vy = self._check_number(lv[1], -0.4, 0.4, "vy")
        wz = self._check_number(request["yaw_rate_rad_s"], -1.0, 1.0, "yaw_rate")
        duration = self._check_number(request["duration_s"], 2.0, 4.0, "duration_s")

        t0 = self._time()
        while self._time() - t0 < duration:
            self._drive_velocity_once(vx, vy, wz)
            if not self._support_ok():
                self.stop(0.2)
                raise CapabilityError("support integrity lost during twist")
            if not np.all(np.isfinite(self.get_base_velocity()["linear"])):
                self.stop(0.2)
                raise CapabilityError("base state became unavailable")
        tw = self.get_base_twist()
        return {
            "status": "ok",
            "commanded": {"vx": vx, "vy": vy, "yaw_rate": wz},
            "achieved_linear_body_m_s": [float(tw["linear_body_yaw_m_s"][0]),
                                          float(tw["linear_body_yaw_m_s"][1])],
            "achieved_yaw_rate_rad_s": float(tw["yaw_rate_rad_s"]),
            "duration_s": duration,
        }

    # ── planar pose regulator (used by G2 and G3) ────────────────────────
    def _plan_target_world(self, x0, y0, yaw0, dx_iy, dy_iy):
        """Map a translation in the captured initial-yaw frame to world xy."""
        c, s = math.cos(yaw0), math.sin(yaw0)
        wx = x0 + c * dx_iy - s * dy_iy
        wy = y0 + s * dx_iy + c * dy_iy
        return wx, wy

    def _body_frame_error(self, target_wx, target_wy):
        """Return (ex_body, ey_body, dist) of world target in current body frame."""
        pos, R = self.get_base_pose()
        dx = target_wx - float(pos[0])
        dy = target_wy - float(pos[1])
        yaw = math.atan2(R[1, 0], R[0, 0])
        c, s = math.cos(yaw), math.sin(yaw)
        ex = c * dx + s * dy
        ey = -s * dx + c * dy
        return ex, ey, math.hypot(dx, dy)

    def _regulate_to_point(self, target_wx, target_wy, deadline_t,
                           yaw_ref=None, pos_tol=0.06):
        """Closed-loop walk toward a world planar target until reached or the
        deadline. Optionally regulate yaw toward yaw_ref. Returns True if the
        planar target was reached within pos_tol."""
        k_lin = 2.0
        k_yaw = 1.5
        while self._time() < deadline_t:
            ex, ey, dist = self._body_frame_error(target_wx, target_wy)
            yaw_ok = yaw_ref is None or abs(self._wrap(yaw_ref - self._yaw())) <= 0.04
            if dist <= pos_tol and yaw_ok:
                return True
            vx = k_lin * ex
            vy = k_lin * ey
            if dist < 0.15:
                scale = max(0.2, dist / 0.15)
                vx *= scale
                vy *= scale
            vx = float(np.clip(vx, -self.spec.vx_max, self.spec.vx_max))
            vy = float(np.clip(vy, -self.spec.vy_max, self.spec.vy_max))
            wz = 0.0
            if yaw_ref is not None:
                wz = float(np.clip(k_yaw * self._wrap(yaw_ref - self._yaw()),
                                   -self.spec.vyaw_max, self.spec.vyaw_max))
            self._drive_velocity_once(vx, vy, wz)
            if not self._support_ok():
                self.stop(0.2)
                raise CapabilityError("support integrity lost during regulation")
        _, _, dist = self._body_frame_error(target_wx, target_wy)
        return dist <= pos_tol

    def _settle_at_point(self, wx, wy, yaw_ref, secs):
        """Hold near a world planar target with small correcting commands so
        residual speed and drift stay bounded during the terminal window."""
        t0 = self._time()
        while self._time() - t0 < secs:
            ex, ey, dist = self._body_frame_error(wx, wy)
            vx = float(np.clip(1.0 * ex, -0.15, 0.15))
            vy = float(np.clip(1.0 * ey, -0.15, 0.15))
            wz = 0.0
            if yaw_ref is not None:
                wz = float(np.clip(1.0 * self._wrap(yaw_ref - self._yaw()),
                                   -0.4, 0.4))
            if dist < 0.03:
                vx = vy = 0.0
            self._drive_velocity_once(vx, vy, wz)
            if not self._support_ok():
                raise CapabilityError("support integrity lost during settle")

    # ── G2: move_body_relative_pose ──────────────────────────────────────
    def move_body_relative_pose(self, request):
        self._finite_state()
        tr = request["translation_initial_yaw_m"]
        if not (isinstance(tr, (list, tuple)) and len(tr) == 2):
            raise CapabilityError("translation_initial_yaw_m must have 2 items")
        dx = self._check_number(tr[0], -0.3, 0.3, "tx")
        dy = self._check_number(tr[1], -0.3, 0.3, "ty")
        dyaw = self._check_number(request["yaw_delta_rad"], -0.6, 0.6, "yaw_delta")
        max_dur = self._check_number(request["max_duration_s"], 0.25, 8.0, "max_duration_s")

        pos0, _ = self.get_base_pose()
        yaw0 = self._yaw()
        target_wx, target_wy = self._plan_target_world(
            float(pos0[0]), float(pos0[1]), yaw0, dx, dy)
        yaw_ref = self._wrap(yaw0 + dyaw)

        hold_s = 0.5
        t0 = self._time()
        deadline = t0 + max(0.0, max_dur - hold_s)
        self._regulate_to_point(target_wx, target_wy, deadline,
                                yaw_ref=yaw_ref, pos_tol=0.04)
        self._settle_at_point(target_wx, target_wy, yaw_ref, hold_s)

        _, _, dist = self._body_frame_error(target_wx, target_wy)
        yaw_err = abs(self._wrap(yaw_ref - self._yaw()))
        if dist > 0.1 or yaw_err > 0.0873:
            raise CapabilityError(
                f"pose not settled: pos_err={dist:.3f} yaw_err={yaw_err:.3f}")
        return {"status": "ok", "position_error_m": dist,
                "yaw_error_rad": yaw_err, "duration_s": self._time() - t0}

    # ── G3: trace_planar_path ────────────────────────────────────────────
    def trace_planar_path(self, request):
        self._finite_state()
        wps = request["waypoints_initial_yaw_m"]
        if not (isinstance(wps, (list, tuple)) and 2 <= len(wps) <= 8):
            raise CapabilityError("waypoints_initial_yaw_m must have 2..8 points")
        pts = []
        for wp in wps:
            if not (isinstance(wp, (list, tuple)) and len(wp) == 2):
                raise CapabilityError("each waypoint must have 2 items")
            px = self._check_number(wp[0], -0.4, 0.4, "wp_x")
            py = self._check_number(wp[1], -0.4, 0.4, "wp_y")
            pts.append((px, py))
        max_dur = self._check_number(request["max_duration_s"], 0.25, 8.0, "max_duration_s")

        pos0, _ = self.get_base_pose()
        yaw0 = self._yaw()
        world_pts = [self._plan_target_world(float(pos0[0]), float(pos0[1]),
                                             yaw0, px, py) for px, py in pts]
        hold_s = 0.5
        t0 = self._time()
        deadline = t0 + max(0.0, max_dur - hold_s)
        for i, (wx, wy) in enumerate(world_pts):
            last = (i == len(world_pts) - 1)
            tol = 0.04 if last else 0.08
            reached = self._regulate_to_point(wx, wy, deadline, yaw_ref=None,
                                               pos_tol=tol)
            if not reached and self._time() >= deadline:
                self.stop(0.2)
                raise CapabilityError(f"waypoint {i} not reached before deadline")
        ewx, ewy = world_pts[-1]
        self._settle_at_point(ewx, ewy, None, hold_s)
        _, _, dist = self._body_frame_error(ewx, ewy)
        if dist > 0.1:
            raise CapabilityError(f"endpoint not settled: err={dist:.3f}")
        return {"status": "ok", "waypoints_visited": len(world_pts),
                "endpoint_error_m": dist, "duration_s": self._time() - t0}

    # ── G4: set_body_height ──────────────────────────────────────────────
    def set_body_height(self, request):
        self._finite_state()
        target_h = self._check_number(request["target_height_m"],
                                      0.2989979050104695, 0.44849685751570423,
                                      "target_height_m")
        max_dur = self._check_number(request["max_duration_s"], 0.25, 8.0,
                                     "max_duration_s")
        pos0, _ = self.get_base_pose()
        yaw0 = self._yaw()
        x0, y0 = float(pos0[0]), float(pos0[1])

        # Servo the thigh-flexion parameter t: larger t lowers the body. Valid
        # well-behaved band is t in [0.50, 0.66]; clamp there.
        t_lo, t_hi = 0.50, 0.66
        t = float(_HOME_THIGH)
        kp_t = 2.5  # rad thigh per meter height error per second
        dt = float(self.model.opt.timestep)
        hold_s = 0.5

        def drive(target_t):
            self.apply_pd_posture(np.clip(np.array(_stance_qpos(target_t)),
                                          self._joint_lo, self._joint_hi))
            self.step(1)

        t0 = self._time()
        deadline = t0 + max(0.0, max_dur - hold_s)
        while self._time() < deadline:
            err = self.get_body_height() - target_h  # +ve: too high -> raise t
            if abs(err) <= 0.015:
                break
            t = float(np.clip(t + kp_t * err * dt, t_lo, t_hi))
            drive(t)
            if not self._support_ok():
                self.stop(0.2)
                raise CapabilityError("support integrity lost during height set")

        # Terminal hold, continuing fine correction toward target.
        th0 = self._time()
        while self._time() - th0 < hold_s:
            err = self.get_body_height() - target_h
            t = float(np.clip(t + kp_t * err * dt, t_lo, t_hi))
            drive(t)
            if not self._support_ok():
                raise CapabilityError("support integrity lost during height hold")

        h_err = abs(self.get_body_height() - target_h)
        roll, pitch = self._roll_pitch()
        pos1, _ = self.get_base_pose()
        disp = math.hypot(float(pos1[0]) - x0, float(pos1[1]) - y0)
        yaw_drift = abs(self._wrap(self._yaw() - yaw0))
        if (h_err > 0.03 or max(abs(roll), abs(pitch)) > 0.1745
                or disp > 0.05 or yaw_drift > 0.0873):
            raise CapabilityError(
                f"height not settled: h_err={h_err:.3f} "
                f"rp={max(abs(roll),abs(pitch)):.3f} disp={disp:.3f} "
                f"yaw_drift={yaw_drift:.3f}")
        return {"status": "ok", "height_error_m": h_err,
                "displacement_m": disp, "yaw_drift_rad": yaw_drift,
                "duration_s": self._time() - t0}

    # ── G5: hold_stable_stance ───────────────────────────────────────────
    def hold_stable_stance(self, request):
        self._finite_state()
        duration = self._check_number(request["duration_s"], 1.0, 2.0, "duration_s")

        home = np.array(_HOME_QPOS, dtype=np.float64)
        pos0, _ = self.get_base_pose()
        x0, y0 = float(pos0[0]), float(pos0[1])
        h0 = self.get_body_height()

        # Recovery window: PD-track the home standing posture to right the base.
        recovery_s = 0.5
        tr = self._time()
        while self._time() - tr < recovery_s:
            self.apply_pd_posture(home)
            self.step(1)
            if not np.all(np.isfinite(self.get_base_velocity()["linear"])):
                raise CapabilityError("base state unavailable during recovery")
        roll, pitch = self._roll_pitch()
        if max(abs(roll), abs(pitch)) > 0.0524:
            extra = self._time()
            while self._time() - extra < recovery_s:
                self.apply_pd_posture(home)
                self.step(1)
                roll, pitch = self._roll_pitch()
                if max(abs(roll), abs(pitch)) <= 0.0524:
                    break
            if max(abs(roll), abs(pitch)) > 0.0524:
                raise CapabilityError(
                    f"could not recover attitude: rp={max(abs(roll),abs(pitch)):.3f}")

        # Capture drift reference after recovery, then hold for the duration.
        posr, _ = self.get_base_pose()
        xr, yr = float(posr[0]), float(posr[1])
        hr = self.get_body_height()
        th = self._time()
        speeds = []
        while self._time() - th < duration:
            self.apply_pd_posture(home)
            self.step(1)
            v = self.get_base_velocity()["linear"]
            speeds.append(float(math.hypot(v[0], v[1])))
            if not self._support_ok():
                raise CapabilityError("support integrity lost during hold")

        roll, pitch = self._roll_pitch()
        pos1, _ = self.get_base_pose()
        h_drift = abs(self.get_body_height() - hr)
        planar_drift = math.hypot(float(pos1[0]) - xr, float(pos1[1]) - yr)
        vz = abs(float(self.get_base_velocity()["linear"][2]))
        mean_speed = float(np.mean(speeds)) if speeds else 0.0
        if (max(abs(roll), abs(pitch)) > 0.0873 or planar_drift > 0.05
                or mean_speed > 0.05 or vz > 0.05 or h_drift > 0.03):
            raise CapabilityError(
                f"stance not stable: rp={max(abs(roll),abs(pitch)):.3f} "
                f"drift={planar_drift:.3f} vmean={mean_speed:.3f} vz={vz:.3f}")
        return {"status": "ok", "hold_roll_pitch_rad": max(abs(roll), abs(pitch)),
                "height_drift_m": h_drift, "planar_drift_m": planar_drift,
                "mean_planar_speed_m_s": mean_speed, "duration_s": duration}


def build():
    """Return a configured ANYmal-C capability Robot bound to mjcf.xml."""
    spec = QuadrupedSpec(
        base_body_name="base",
        leg_joint_names={
            "LF": ["LF_HAA", "LF_HFE", "LF_KFE"],
            "RF": ["RF_HAA", "RF_HFE", "RF_KFE"],
            "LH": ["LH_HAA", "LH_HFE", "LH_KFE"],
            "RH": ["RH_HAA", "RH_HFE", "RH_KFE"],
        },
        leg_actuator_names={
            "LF": ["LF_HAA", "LF_HFE", "LF_KFE"],
            "RF": ["RF_HAA", "RF_HFE", "RF_KFE"],
            "LH": ["LH_HAA", "LH_HFE", "LH_KFE"],
            "RH": ["RH_HAA", "RH_HFE", "RH_KFE"],
        },
        home_qpos=list(_HOME_QPOS),
        gait_freq_hz=1.5,
        swing_amp_thigh=0.12,
        swing_amp_calf=0.18,
        thigh_forward_sign=-1.0,
        body_height_target=0.387,
        vx_max=0.4,
        vy_max=0.2,
        vyaw_max=1.0,
        actuation="joint_position",
    )
    return Robot.from_mjcf("mjcf.xml", spec=spec)
