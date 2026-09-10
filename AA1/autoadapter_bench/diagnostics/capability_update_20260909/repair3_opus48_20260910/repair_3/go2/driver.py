# SPDX-License-Identifier: Apache-2.0
"""Generated Go2 capability driver.

Wraps the retained Go2 velocity policy skeleton and implements the public
capability contract (G1..G5). All motion is produced by advancing the shared
MuJoCo session through native torque commands; no live qpos/qvel is written to
achieve an action. Deadlines and holds are measured with ``data.time``.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from auto_adapter.skeletons.go2_velocity_policy import (
    Go2VelocityPolicySkeleton,
    Go2VelocityPolicySpec,
)


class CapabilityError(RuntimeError):
    """Bounded capability error returned when a request cannot be honored."""


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _wrap_pi(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


class Robot(Go2VelocityPolicySkeleton):
    """Go2 policy driver implementing the catalogued capability contract."""

    # --- shared low-level control period ---
    def _dt(self) -> float:
        return float(self.model.opt.timestep)

    def _sim_time(self) -> float:
        return float(self.data.time)

    # --- fresh public state helpers ---
    def _base_xy_yaw(self):
        """World planar position and heading yaw of the base."""
        position, rotation = self.get_base_pose()
        x_axis = rotation[:, 0]
        yaw = math.atan2(float(x_axis[1]), float(x_axis[0]))
        return float(position[0]), float(position[1]), float(position[2]), yaw

    def _require_finite_state(self) -> None:
        if not (
            np.all(np.isfinite(self.data.qpos))
            and np.all(np.isfinite(self.data.qvel))
        ):
            raise CapabilityError("public MuJoCo state is not finite")

    def _support_ok(self) -> bool:
        """Support integrity gate: base upright and above a floor margin."""
        _, rotation = self.get_base_pose()
        up_z = float(rotation[2, 2])  # body z projected on world z
        _, _, height, _ = self._base_xy_yaw()
        return up_z > 0.5 and height > 0.12

    # --- one closed-loop policy tick that keeps the shared frame ---
    def _drive_body_command(self, vx: float, vy: float, yaw_rate: float) -> None:
        """Advance exactly one physics step under a body-frame twist command.

        Reuses the skeleton's retained-policy inference/torque path but at the
        granularity of a single step so capability loops can re-plan feedback
        and enforce deadlines / holds against ``data.time``.
        """
        vx = _clamp(vx, -self.spec.maximum_planar_speed_m_s,
                    self.spec.maximum_planar_speed_m_s)
        vy = _clamp(vy, -self.spec.maximum_planar_speed_m_s,
                    self.spec.maximum_planar_speed_m_s)
        yaw_rate = _clamp(yaw_rate, -self.spec.maximum_yaw_rate_rad_s,
                          self.spec.maximum_yaw_rate_rad_s)
        self.command_planar_velocity(vx, vy, yaw_rate, duration=self._dt())

    # === shared closed-loop twist tracker ===
    def _track_twist_step(self, tvx: float, tvy: float, tw: float, st: dict) -> None:
        """One step of PI velocity tracking on the shared session.

        The retained policy under-tracks open-loop; a bounded PI wrapper on the
        measured body-frame twist closes the gap while all torques still flow
        through the policy's own control path.
        """
        twist = self.get_base_twist()
        vb = twist["linear_body_yaw_m_s"]
        w = float(twist["yaw_rate_rad_s"])
        ex = tvx - float(vb[0])
        ey = tvy - float(vb[1])
        ew = tw - w
        st["ix"] = _clamp(st["ix"] + 0.02 * ex, -0.3, 0.3)
        st["iy"] = _clamp(st["iy"] + 0.02 * ey, -0.3, 0.3)
        st["iw"] = _clamp(st["iw"] + 0.02 * ew, -0.5, 0.5)
        cvx = tvx + 1.5 * ex + st["ix"]
        cvy = tvy + 1.5 * ey + st["iy"]
        cw = tw + 2.0 * ew + st["iw"]
        self._drive_body_command(cvx, cvy, cw)

    @staticmethod
    def _twist_state() -> dict:
        return {"ix": 0.0, "iy": 0.0, "iw": 0.0}

    # === G1: track a bounded planar body-frame twist for a fixed duration ===
    def track_planar_twist(self, request):
        lv = request["linear_velocity_body_m_s"]
        yaw_rate = float(request["yaw_rate_rad_s"])
        duration = float(request["duration_s"])
        tvx, tvy = float(lv[0]), float(lv[1])
        if not all(math.isfinite(v) for v in (tvx, tvy, yaw_rate, duration)):
            raise CapabilityError("G1 request contains non-finite values")
        if not (-0.4 <= tvx <= 0.4 and -0.4 <= tvy <= 0.4):
            raise CapabilityError("G1 linear velocity out of bounds")
        if not (-1.0 <= yaw_rate <= 1.0):
            raise CapabilityError("G1 yaw rate out of bounds")
        if not (2.0 <= duration <= 4.0):
            raise CapabilityError("G1 duration out of bounds")

        self._require_finite_state()
        st = self._twist_state()
        t0 = self._sim_time()
        deadline = t0 + duration
        while self._sim_time() < deadline:
            if not self._support_ok():
                self.hold(0.05)
                raise CapabilityError("G1 support integrity lost")
            self._track_twist_step(tvx, tvy, yaw_rate, st)
            self._require_finite_state()

        # measure the criterion-defined final 1.0 s window
        vsum = np.zeros(2)
        wsum = 0.0
        n = 0
        w_end = self._sim_time() + 1.0
        while self._sim_time() < w_end:
            self._track_twist_step(tvx, tvy, yaw_rate, st)
            tw = self.get_base_twist()
            vsum += np.asarray(tw["linear_body_yaw_m_s"], dtype=float)
            wsum += float(tw["yaw_rate_rad_s"])
            n += 1
        mean_v = vsum / max(1, n)
        mean_w = wsum / max(1, n)
        return {
            "capability_id": "G1",
            "status": "ok",
            "mean_body_velocity_m_s": mean_v.tolist(),
            "mean_yaw_rate_rad_s": float(mean_w),
            "sim_time_s": self._sim_time(),
        }

    # === shared planar pose regulator (initial-yaw frame targets) ===
    def _regulate_to_world_pose(self, tx, ty, tyaw, deadline, st,
                                vlim=0.35, wlim=0.9,
                                pos_tol=0.015, yaw_tol=0.02, hold_yaw=True):
        """Drive toward a fixed world (tx,ty[,tyaw]); return once converged.

        Feedback is computed from fresh base pose, transformed into the current
        body frame, and issued as a bounded body-frame twist through the policy.
        Returns True on convergence, False if the deadline elapses first.
        """
        while self._sim_time() < deadline:
            if not self._support_ok():
                raise CapabilityError("support integrity lost during regulation")
            x, y, _, yaw = self._base_xy_yaw()
            ex_w, ey_w = tx - x, ty - y
            c, s = math.cos(yaw), math.sin(yaw)
            ex_b = c * ex_w + s * ey_w
            ey_b = -s * ex_w + c * ey_w
            eyaw = _wrap_pi(tyaw - yaw) if tyaw is not None else 0.0
            vx = _clamp(1.6 * ex_b, -vlim, vlim)
            vy = _clamp(1.6 * ey_b, -vlim, vlim)
            w = _clamp(2.0 * eyaw, -wlim, wlim) if hold_yaw else 0.0
            self._track_twist_step(vx, vy, w, st)
            self._require_finite_state()
            if (math.hypot(ex_b, ey_b) < pos_tol
                    and (not hold_yaw or abs(eyaw) < yaw_tol)):
                return True
        return False

    def _hold_world_pose(self, tx, ty, tyaw, hold_s, st, hold_yaw=True):
        """Continuous terminal hold that keeps regulating the captured target."""
        end = self._sim_time() + hold_s
        while self._sim_time() < end:
            if not self._support_ok():
                raise CapabilityError("support integrity lost during hold")
            x, y, _, yaw = self._base_xy_yaw()
            ex_w, ey_w = tx - x, ty - y
            c, s = math.cos(yaw), math.sin(yaw)
            ex_b = c * ex_w + s * ey_w
            ey_b = -s * ex_w + c * ey_w
            eyaw = _wrap_pi(tyaw - yaw) if tyaw is not None else 0.0
            vx = _clamp(1.6 * ex_b, -0.2, 0.2)
            vy = _clamp(1.6 * ey_b, -0.2, 0.2)
            w = _clamp(2.0 * eyaw, -0.5, 0.5) if hold_yaw else 0.0
            self._track_twist_step(vx, vy, w, st)

    # === G2: move to a pose relative to the initial-yaw frame ===
    def move_body_relative_pose(self, request):
        tr = request["translation_initial_yaw_m"]
        dyaw = float(request["yaw_delta_rad"])
        max_dur = float(request["max_duration_s"])
        dx, dy = float(tr[0]), float(tr[1])
        if not all(math.isfinite(v) for v in (dx, dy, dyaw, max_dur)):
            raise CapabilityError("G2 request contains non-finite values")
        if not (-0.3 <= dx <= 0.3 and -0.3 <= dy <= 0.3):
            raise CapabilityError("G2 translation out of bounds")
        if not (-0.6 <= dyaw <= 0.6):
            raise CapabilityError("G2 yaw delta out of bounds")
        if not (0.25 <= max_dur <= 8.0):
            raise CapabilityError("G2 duration out of bounds")

        self._require_finite_state()
        # capture the initial-yaw reference frame; it stays fixed for the call
        x0, y0, _, yaw0 = self._base_xy_yaw()
        c, s = math.cos(yaw0), math.sin(yaw0)
        tx = x0 + c * dx - s * dy
        ty = y0 + s * dx + c * dy
        tyaw = yaw0 + dyaw

        st = self._twist_state()
        hold_s = 0.5
        deadline = self._sim_time() + max(0.0, max_dur - hold_s)
        converged = self._regulate_to_world_pose(tx, ty, tyaw, deadline, st)
        if not converged:
            self.hold(0.05)
            raise CapabilityError("G2 pose not settled within max_duration_s")
        self._hold_world_pose(tx, ty, tyaw, hold_s, st)
        x, y, _, yaw = self._base_xy_yaw()
        return {
            "capability_id": "G2",
            "status": "ok",
            "position_error_m": math.hypot(tx - x, ty - y),
            "yaw_error_rad": abs(_wrap_pi(tyaw - yaw)),
            "sim_time_s": self._sim_time(),
        }

    # === G3: trace ordered planar waypoints in the initial-yaw frame ===
    def trace_planar_path(self, request):
        wps = request["waypoints_initial_yaw_m"]
        max_dur = float(request["max_duration_s"])
        if not (2 <= len(wps) <= 8):
            raise CapabilityError("G3 waypoint count out of bounds")
        if not (0.25 <= max_dur <= 8.0):
            raise CapabilityError("G3 duration out of bounds")
        parsed = []
        for wp in wps:
            wx, wy = float(wp[0]), float(wp[1])
            if not (math.isfinite(wx) and math.isfinite(wy)):
                raise CapabilityError("G3 waypoint non-finite")
            if not (-0.4 <= wx <= 0.4 and -0.4 <= wy <= 0.4):
                raise CapabilityError("G3 waypoint out of bounds")
            parsed.append((wx, wy))

        self._require_finite_state()
        # capture initial-yaw frame; waypoint order stays fixed
        x0, y0, _, yaw0 = self._base_xy_yaw()
        c, s = math.cos(yaw0), math.sin(yaw0)
        world_wps = [
            (x0 + c * wx - s * wy, y0 + s * wx + c * wy) for (wx, wy) in parsed
        ]

        st = self._twist_state()
        hold_s = 0.5
        deadline = self._sim_time() + max(0.0, max_dur - hold_s)
        # visit each waypoint in order; keep yaw at capture heading (yaw-free
        # transit uses omnidirectional velocity so path order is preserved)
        for idx, (tx, ty) in enumerate(world_wps):
            last = idx == len(world_wps) - 1
            tol = 0.05 if last else 0.08
            ok = self._regulate_to_world_pose(
                tx, ty, yaw0, deadline, st,
                pos_tol=tol, yaw_tol=0.15,
            )
            if not ok:
                self.hold(0.05)
                raise CapabilityError(
                    f"G3 waypoint {idx} not reached within max_duration_s"
                )
        # terminal hold at the endpoint
        ex, ey = world_wps[-1]
        self._hold_world_pose(ex, ey, yaw0, hold_s, st)
        x, y, _, yaw = self._base_xy_yaw()
        return {
            "capability_id": "G3",
            "status": "ok",
            "endpoint_error_m": math.hypot(ex - x, ey - y),
            "waypoints_visited": len(world_wps),
            "sim_time_s": self._sim_time(),
        }
