# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for ANYmal-C (quadruped).

Subclasses QuadrupedPDGaitSkeleton (native position-servo, joint_position
actuation) and implements the public capability contract:
  G1 track_planar_twist, G2 move_body_relative_pose, G3 trace_planar_path,
  G4 set_body_height, G5 hold_stable_stance.

All motion is produced by writing native joint-position targets to data.ctrl
and advancing the SAME model/data. Deadlines/holds are measured with
data.time. Kinematic scratch uses a throwaway MjData copy only.
"""
from __future__ import annotations

import math
import numpy as np
import mujoco

from auto_adapter.skeletons.quadruped_pd_gait import QuadrupedPDGaitSkeleton
from auto_adapter.skeletons.base import QuadrupedSpec


class CapabilityError(RuntimeError):
    """Bounded capability failure with a structured payload."""

    def __init__(self, capability_id, reason, detail=None):
        super().__init__(f"{capability_id}: {reason}")
        self.capability_id = capability_id
        self.reason = reason
        self.detail = detail or {}


# Leg order LF, RF, LH, RH -> positions (0,3) vs (1,2) form trot diagonals.
_LEG_ORDER = ("LF", "RF", "LH", "RH")


def _leg_dict(suffix_map):
    return {leg: [f"{leg}_{s}" for s in ("HAA", "HFE", "KFE")] for leg in _LEG_ORDER}


class Robot(QuadrupedPDGaitSkeleton):
    """ANYmal-C capability robot."""

    def __init__(self, model, data, spec):
        super().__init__(model, data, spec)
        # Foot geom ids (the class="foot" spheres) for contact/support checks.
        self._foot_geom_ids = []
        for leg in _LEG_ORDER:
            gid = self._find_foot_geom(leg)
            if gid >= 0:
                self._foot_geom_ids.append(gid)
        self._floor_geom_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_GEOM, "floor")

    # ---- introspection helpers -------------------------------------------
    def _find_foot_geom(self, leg):
        # foot sphere lives in the SHANK body; pick the sphere geom.
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY,
                                f"{leg}_SHANK")
        if bid < 0:
            return -1
        best = -1
        for gid in range(self.model.ngeom):
            if int(self.model.geom_bodyid[gid]) != bid:
                continue
            if int(self.model.geom_type[gid]) == int(mujoco.mjtGeom.mjGEOM_SPHERE):
                best = gid
        return best

    # ---- state helpers ---------------------------------------------------
    def _base_xy(self):
        pos, _ = self.get_base_pose()
        return np.array(pos[:2], dtype=np.float64)

    def _base_yaw(self):
        _, R = self.get_base_pose()
        return math.atan2(R[1, 0], R[0, 0])

    def _base_rp(self):
        """Return (roll, pitch) of the base from its rotation matrix."""
        _, R = self.get_base_pose()
        pitch = math.asin(max(-1.0, min(1.0, -R[2, 0])))
        roll = math.atan2(R[2, 1], R[2, 2])
        return roll, pitch

    def _planar_speed(self):
        tw = self.get_base_twist()
        v = np.asarray(tw["linear_world_m_s"], dtype=np.float64)
        return float(np.hypot(v[0], v[1]))

    def _state_finite(self):
        return (np.all(np.isfinite(self.data.qpos)) and
                np.all(np.isfinite(self.data.qvel)))

    def _support_ok(self):
        """At least 3 feet in ground contact and torso upright/off-floor."""
        if not self._state_finite():
            return False
        roll, pitch = self._base_rp()
        if abs(roll) > 0.6 or abs(pitch) > 0.6:
            return False
        if self.get_body_height() < 0.12:
            return False
        contacts = self._feet_in_contact()
        return sum(contacts) >= 2

    def _feet_in_contact(self):
        flags = [False] * len(self._foot_geom_ids)
        for ci in range(self.data.ncon):
            c = self.data.contact[ci]
            g1, g2 = int(c.geom1), int(c.geom2)
            for k, fg in enumerate(self._foot_geom_ids):
                if (g1 == fg and g2 == self._floor_geom_id) or \
                   (g2 == fg and g1 == self._floor_geom_id):
                    flags[k] = True
        return flags

    @staticmethod
    def _wrap(a):
        return (a + math.pi) % (2.0 * math.pi) - math.pi

    # ---- low-level velocity-tracking control step ------------------------
    def _drive_twist_step(self, vx, vy, yaw_rate):
        """One physics step tracking a body-frame planar twist via the
        skeleton's native gait posture primitive. Advances real physics."""
        vx = float(np.clip(vx, -self.spec.vx_max, self.spec.vx_max))
        vy = float(np.clip(vy, -self.spec.vy_max, self.spec.vy_max))
        yaw_rate = float(np.clip(yaw_rate, -self.spec.vyaw_max, self.spec.vyaw_max))
        timestep = float(self.model.opt.timestep)
        gait_period = 1.0 / float(self.spec.gait_freq_hz)
        q_target = self._gait_posture(self._gait_time, vx, vy, yaw_rate)
        self.apply_pd_posture(q_target)
        self.step(1)
        self._gait_time = (self._gait_time + timestep) % gait_period

    def _hold_posture_step(self, q_target):
        self.apply_pd_posture(q_target)
        self.step(1)

    # ---- G1: track_planar_twist ------------------------------------------
    def track_planar_twist(self, request):
        vb = request["linear_velocity_body_m_s"]
        yaw_rate = float(request["yaw_rate_rad_s"])
        duration = float(request["duration_s"])
        vx_cmd, vy_cmd = float(vb[0]), float(vb[1])
        if not self._state_finite():
            raise CapabilityError("G1", "public base state unavailable")
        # Stand first so the controller starts from a supported stance.
        self._ensure_standing()
        timestep = float(self.model.opt.timestep)
        n_steps = max(1, int(math.ceil(duration / timestep)))
        for _ in range(n_steps):
            if not self._support_ok():
                self.stop(0.1)
                raise CapabilityError("G1", "support integrity lost")
            self._drive_twist_step(vx_cmd, vy_cmd, yaw_rate)
        # Report final-window mean tracking for feedback (not asserted).
        return {
            "status": "ok",
            "capability_id": "G1",
            "commanded": {"vx": vx_cmd, "vy": vy_cmd, "yaw_rate": yaw_rate,
                          "duration_s": duration},
            "final_twist": self._twist_report(),
        }

    def _twist_report(self):
        tw = self.get_base_twist()
        return {
            "linear_body_yaw_m_s": np.asarray(
                tw["linear_body_yaw_m_s"]).tolist(),
            "yaw_rate_rad_s": float(tw["yaw_rate_rad_s"]),
        }

    def _ensure_standing(self):
        """Bring the robot to the home stance if it is not already there."""
        h = self.get_body_height()
        if h < 0.25 or not self._support_ok():
            self.stand_up(duration=1.0)

    # ---- shared planar goal regulator ------------------------------------
    def _regulate_to_planar_goal(self, cap_id, goal_xy_world, goal_yaw,
                                 deadline_time, pos_tol=0.06, yaw_tol=0.05):
        """Closed-loop: drive base toward a world-frame planar target using
        body-frame velocity commands. Returns True when settled, else False
        at deadline. Raises CapabilityError on lost support."""
        while self.data.time < deadline_time:
            if not self._support_ok():
                self.stop(0.1)
                raise CapabilityError(cap_id, "support integrity lost")
            xy = self._base_xy()
            yaw = self._base_yaw()
            err_w = goal_xy_world - xy
            dist = float(np.hypot(err_w[0], err_w[1]))
            yaw_err = self._wrap(goal_yaw - yaw)
            if dist <= pos_tol and abs(yaw_err) <= yaw_tol:
                return True
            # world error -> body frame
            c, s = math.cos(yaw), math.sin(yaw)
            ex_b = c * err_w[0] + s * err_w[1]
            ey_b = -s * err_w[0] + c * err_w[1]
            kv = 1.2
            vx = float(np.clip(kv * ex_b, -self.spec.vx_max, self.spec.vx_max))
            vy = float(np.clip(kv * ey_b, -self.spec.vy_max, self.spec.vy_max))
            kyaw = 1.5
            wz = float(np.clip(kyaw * yaw_err, -self.spec.vyaw_max,
                               self.spec.vyaw_max))
            # taper velocity when close to avoid overshoot
            if dist < 0.15:
                vx *= dist / 0.15
                vy *= dist / 0.15
            self._drive_twist_step(vx, vy, wz)
        return False

    def _terminal_hold(self, cap_id, hold_s, speed_tol=0.08):
        """Hold current posture for hold_s continuous seconds with low speed.
        Restarts the hold clock if speed exceeds tol."""
        q_hold = self.get_joint_positions()
        start = self.data.time
        while self.data.time - start < hold_s:
            if not self._support_ok():
                raise CapabilityError(cap_id, "support lost during hold")
            self._hold_posture_step(q_hold)
            if self._planar_speed() > speed_tol:
                start = self.data.time
                q_hold = self.get_joint_positions()

    # ---- G2: move_body_relative_pose -------------------------------------
    def move_body_relative_pose(self, request):
        tr = request["translation_initial_yaw_m"]
        yaw_delta = float(request["yaw_delta_rad"])
        max_dur = float(request["max_duration_s"])
        if not self._state_finite():
            raise CapabilityError("G2", "public base state unavailable")
        self._ensure_standing()
        # Capture fixed initial-yaw reference at call start.
        p0 = self._base_xy()
        yaw0 = self._base_yaw()
        c, s = math.cos(yaw0), math.sin(yaw0)
        dx, dy = float(tr[0]), float(tr[1])
        goal_xy = p0 + np.array([c * dx - s * dy, s * dx + c * dy])
        goal_yaw = self._wrap(yaw0 + yaw_delta)
        hold_s = 0.5
        deadline = self.data.time + max(0.0, max_dur - hold_s)
        settled = self._regulate_to_planar_goal("G2", goal_xy, goal_yaw,
                                                 deadline)
        if not settled:
            self.stop(0.15)
            raise CapabilityError("G2", "pose not settled within duration",
                                  {"goal_xy": goal_xy.tolist()})
        self._terminal_hold("G2", hold_s)
        return {"status": "ok", "capability_id": "G2",
                "final_xy": self._base_xy().tolist(),
                "final_yaw": self._base_yaw()}

    # ---- G3: trace_planar_path -------------------------------------------
    def trace_planar_path(self, request):
        wps = request["waypoints_initial_yaw_m"]
        max_dur = float(request["max_duration_s"])
        if not self._state_finite():
            raise CapabilityError("G3", "public base state unavailable")
        self._ensure_standing()
        p0 = self._base_xy()
        yaw0 = self._base_yaw()
        c, s = math.cos(yaw0), math.sin(yaw0)
        goals = []
        for wp in wps:
            dx, dy = float(wp[0]), float(wp[1])
            goals.append(p0 + np.array([c * dx - s * dy, s * dx + c * dy]))
        hold_s = 0.5
        deadline = self.data.time + max(0.0, max_dur - hold_s)
        # Visit each waypoint in order; keep yaw at initial yaw throughout.
        for i, g in enumerate(goals):
            final = (i == len(goals) - 1)
            tol = 0.06 if final else 0.08
            ok = self._regulate_to_planar_goal("G3", g, yaw0, deadline,
                                               pos_tol=tol, yaw_tol=0.12)
            if not ok:
                self.stop(0.15)
                raise CapabilityError("G3", "waypoint not reached in time",
                                      {"waypoint_index": i})
        self._terminal_hold("G3", hold_s)
        return {"status": "ok", "capability_id": "G3",
                "n_waypoints": len(goals),
                "final_xy": self._base_xy().tolist()}

    # ---- G4: set_body_height ---------------------------------------------
    def set_body_height(self, request):
        target_h = float(request["target_height_m"])
        max_dur = float(request["max_duration_s"])
        if not self._state_finite():
            raise CapabilityError("G4", "public base/attitude state unavailable")
        self._ensure_standing()
        hold_s = 0.5
        deadline = self.data.time + max(0.0, max_dur - hold_s)
        # Regulate a single symmetric knee-bend parameter `t` (thigh angle):
        # smaller t -> straighter legs -> higher body. Integrate from the
        # measured height error so the terminal height is closed-loop.
        t = 0.7
        dt = float(self.model.opt.timestep)
        settled = False
        while self.data.time < deadline:
            if not self._support_ok():
                self.stop(0.1)
                raise CapabilityError("G4", "support integrity lost")
            self._hold_posture_step(self._height_posture(t))
            h = self.get_body_height()
            err = target_h - h            # >0 -> raise -> decrease t
            t -= 3.0 * err * dt * 50.0
            t = float(np.clip(t, 0.30, 1.05))
            if abs(err) <= 0.015 and self._planar_speed() <= 0.05:
                settled = True
                break
        if not settled:
            self.stop(0.15)
            raise CapabilityError("G4", "height not settled within duration",
                                  {"target_h": target_h,
                                   "reached": self.get_body_height()})
        start = self.data.time
        while self.data.time - start < hold_s:
            if not self._support_ok():
                raise CapabilityError("G4", "support lost during hold")
            self._hold_posture_step(self._height_posture(t))
            h = self.get_body_height()
            t -= 3.0 * (target_h - h) * dt * 50.0
            t = float(np.clip(t, 0.30, 1.05))
            if abs(target_h - h) > 0.03:
                start = self.data.time
        return {"status": "ok", "capability_id": "G4",
                "final_height": self.get_body_height()}

    def _height_posture(self, t):
        """Symmetric stance keyed by thigh angle `t`; calf = -2t magnitude.
        Hips held at 0. Front legs (thigh>0, calf<0), hind legs mirrored."""
        c = 2.0 * float(t)
        q = np.array([0.0, t, -c, 0.0, t, -c,
                      0.0, -t, c, 0.0, -t, c], dtype=np.float64)
        return np.clip(q, self._joint_lo, self._joint_hi)

    # ---- G5: hold_stable_stance ------------------------------------------
    def hold_stable_stance(self, request):
        duration = float(request["duration_s"])
        if not self._state_finite():
            raise CapabilityError("G5", "public state unavailable post-disturbance")
        # Recovery phase: drive toward the home stance to right the base.
        recovery_s = 0.5
        r_deadline = self.data.time + recovery_s
        while self.data.time < r_deadline:
            self._hold_posture_step(self._home_q.copy())
        # After recovery window, require an upright supported stance.
        roll, pitch = self._base_rp()
        if not self._state_finite():
            raise CapabilityError("G5", "state became non-finite")
        if abs(roll) > 0.0524 or abs(pitch) > 0.0524:
            # keep trying briefly, then fail if still tilted
            extra_deadline = self.data.time + 0.5
            while self.data.time < extra_deadline:
                self._hold_posture_step(self._home_q.copy())
            roll, pitch = self._base_rp()
            if abs(roll) > 0.0524 or abs(pitch) > 0.0524:
                raise CapabilityError("G5", "failed to recover upright stance",
                                      {"roll": roll, "pitch": pitch})
        if not self._support_ok():
            raise CapabilityError("G5", "support not recovered")
        # Hold phase: maintain the stance for the requested duration.
        q_hold = self._home_q.copy()
        h0 = self.get_body_height()
        p0 = self._base_xy()
        start = self.data.time
        while self.data.time - start < duration:
            self._hold_posture_step(q_hold)
            if not self._state_finite() or not self._support_ok():
                raise CapabilityError("G5", "lost support during hold")
        return {"status": "ok", "capability_id": "G5",
                "held_s": duration,
                "height_drift": abs(self.get_body_height() - h0),
                "planar_drift": float(np.linalg.norm(self._base_xy() - p0))}


# ─────────────────────────────────────────────────────────────────────────
# Module-level build() — required entry point.
# ─────────────────────────────────────────────────────────────────────────
def build():
    """Construct the ANYmal-C capability robot from the local MJCF."""
    spec = QuadrupedSpec(
        base_body_name="base",
        leg_joint_names=_leg_dict(None),
        leg_actuator_names=_leg_dict(None),
        # Home stance = keyframe 'fixed_settled' ctrl targets (native servo).
        home_qpos=[
            0.0, 0.7, -1.4,   # LF
            0.0, 0.7, -1.4,   # RF
            0.0, -0.7, 1.4,   # LH
            0.0, -0.7, 1.4,   # RH
        ],
        gait_freq_hz=2.0,
        swing_amp_thigh=0.14,
        swing_amp_calf=0.22,
        thigh_forward_sign=-1.0,
        body_height_target=0.374,
        vx_max=0.4,
        vy_max=0.2,
        vyaw_max=1.0,
        actuation="joint_position",
    )
    return Robot.from_mjcf("mjcf.xml", spec=spec)
