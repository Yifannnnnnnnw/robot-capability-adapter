# SPDX-License-Identifier: Apache-2.0
"""Generated Go2 capability driver over Go2VelocityPolicySkeleton.

Implements the public capability contract (G1..G5) by closing feedback loops
around the retained velocity policy. All actions advance the SAME MuJoCo
model/data via the skeleton's torque + physics primitives; deadlines and holds
are measured with ``self.data.time`` (simulation seconds). No live qpos/qvel
teleporting is used to achieve an action.
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
    """Bounded capability failure with a machine-readable payload."""

    def __init__(self, capability_id: str, reason: str, detail: dict | None = None):
        super().__init__(f"{capability_id}: {reason}")
        self.capability_id = capability_id
        self.reason = reason
        self.detail = detail or {}


# Joint / actuator bindings in FL, FR, RL, RR order, hip/thigh/calf per leg.
_JOINT_NAMES = (
    "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
    "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
    "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint",
    "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
)
_ACTUATOR_NAMES = (
    "FL_hip", "FL_thigh", "FL_calf",
    "FR_hip", "FR_thigh", "FR_calf",
    "RL_hip", "RL_thigh", "RL_calf",
    "RR_hip", "RR_thigh", "RR_calf",
)


def _wrap(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


class Robot(Go2VelocityPolicySkeleton):
    """Go2 capability robot: closes loops around the retained velocity policy."""

    # Request bounds mirrored from the capability contract.
    G1_V_MAX = 0.4
    G1_YAW_MAX = 1.0
    G2_XY_MAX = 0.3
    G2_YAW_MAX = 0.6
    G3_XY_MAX = 0.4
    G4_H_MIN, G4_H_MAX = 0.22, 0.36

    _CTRL_SLICE_S = 0.02  # one policy period per closed-loop update
    # Command saturation kept safely inside the trained comfortable range.
    _V_CMD_MAX = 0.6
    _YAW_CMD_MAX = 1.4

    # ---- geometry helpers -------------------------------------------------
    def _yaw(self) -> float:
        _, rot = self.get_base_pose()
        return math.atan2(float(rot[1, 0]), float(rot[0, 0]))

    def _planar_pos(self) -> np.ndarray:
        pos, _ = self.get_base_pose()
        return np.asarray(pos[:2], dtype=float)

    def _height(self) -> float:
        pos, _ = self.get_base_pose()
        return float(pos[2])

    def _assert_finite_state(self, cap: str) -> None:
        if not (np.all(np.isfinite(self.data.qpos)) and
                np.all(np.isfinite(self.data.qvel))):
            raise CapabilityError(cap, "public base state is not finite")

    def _upright(self) -> tuple[float, float]:
        _, rot = self.get_base_pose()
        r = np.asarray(rot, dtype=float)
        pitch = math.asin(max(-1.0, min(1.0, -float(r[2, 0]))))
        roll = math.atan2(float(r[2, 1]), float(r[2, 2]))
        return roll, pitch

    def _support_ok(self, cap: str) -> None:
        roll, pitch = self._upright()
        if abs(roll) > 1.2 or abs(pitch) > 1.2 or self._height() < 0.12:
            raise CapabilityError(
                cap, "support integrity lost",
                {"roll": roll, "pitch": pitch, "height": self._height()},
            )

    def _joint_limits(self):
        if getattr(self, "_jl_cache", None) is not None:
            return self._jl_cache
        mj = self._load_mujoco()
        lo = np.empty(12); hi = np.empty(12)
        for i, jn in enumerate(self.spec.joint_names):
            jid = mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_JOINT, jn)
            lo[i] = float(self.model.jnt_range[jid, 0])
            hi[i] = float(self.model.jnt_range[jid, 1])
        self._jl_cache = (lo, hi)
        return self._jl_cache

    # ---- low-level drive slice with saturation ----------------------------
    def _drive_slice(self, cap, vx, vy, yaw_rate, dt=None):
        dt = self._CTRL_SLICE_S if dt is None else dt
        v = np.array([vx, vy], dtype=float)
        sp = float(np.linalg.norm(v))
        if sp > self._V_CMD_MAX:
            v *= self._V_CMD_MAX / sp
        yr = float(np.clip(yaw_rate, -self._YAW_CMD_MAX, self._YAW_CMD_MAX))
        try:
            self.command_planar_velocity(float(v[0]), float(v[1]), yr, dt)
        except Exception as exc:
            raise CapabilityError(cap, f"policy command failed: {exc}") from exc
        self._assert_finite_state(cap)
        self._support_ok(cap)

    # ======================================================================
    # G1 track_planar_twist  (PI feedback on measured body twist)
    # ======================================================================
    def track_planar_twist(self, request):
        cap = "G1"
        lv = request["linear_velocity_body_m_s"]
        yaw_rate = float(request["yaw_rate_rad_s"])
        duration = float(request["duration_s"])
        if not isinstance(lv, (list, tuple)) or len(lv) != 2:
            raise CapabilityError(cap, "linear_velocity_body_m_s must be length 2")
        vx, vy = float(lv[0]), float(lv[1])
        for name, val, lo, hi in (
            ("vx", vx, -self.G1_V_MAX, self.G1_V_MAX),
            ("vy", vy, -self.G1_V_MAX, self.G1_V_MAX),
            ("yaw_rate", yaw_rate, -self.G1_YAW_MAX, self.G1_YAW_MAX),
            ("duration", duration, 2.0, 4.0),
        ):
            if not math.isfinite(val) or val < lo - 1e-9 or val > hi + 1e-9:
                raise CapabilityError(cap, f"{name} out of contract bounds", {name: val})
        self._resolve()
        self._assert_finite_state(cap)

        des = np.array([vx, vy], dtype=float)
        integ = np.zeros(2)
        iyaw = 0.0
        kp, ki = 1.0, 1.2
        kpy, kiy = 0.6, 0.8
        t_end = self.data.time + duration
        while self.data.time < t_end - 1e-9:
            tw = self.get_base_twist()
            act = np.array([tw["linear_body_yaw_m_s"][0],
                            tw["linear_body_yaw_m_s"][1]], dtype=float)
            ay = float(tw["yaw_rate_rad_s"])
            err = des - act
            eyw = yaw_rate - ay
            integ = np.clip(integ + err * self._CTRL_SLICE_S, -0.6, 0.6)
            iyaw = float(np.clip(iyaw + eyw * self._CTRL_SLICE_S, -0.8, 0.8))
            cmd = des + kp * err + ki * integ
            cyaw = yaw_rate + kpy * eyw + kiy * iyaw
            dt = min(self._CTRL_SLICE_S, t_end - self.data.time)
            self._drive_slice(cap, float(cmd[0]), float(cmd[1]), float(cyaw), dt)

        tw = self.get_base_twist()
        return {
            "capability_id": cap, "status": "ok",
            "requested": {"vx": vx, "vy": vy, "yaw_rate": yaw_rate,
                          "duration_s": duration},
            "final_twist_body": {
                "vx": float(tw["linear_body_yaw_m_s"][0]),
                "vy": float(tw["linear_body_yaw_m_s"][1]),
                "yaw_rate": float(tw["yaw_rate_rad_s"]),
            },
            "sim_time": float(self.data.time),
        }

    # ---- closed-loop planar goal regulator (world goal) -------------------
    def _regulate_to(self, cap, gx, gy, gyaw, deadline,
                     pos_tol, yaw_tol, speed_tol, hold_s, final=True,
                     regulate_yaw=True):
        """Drive to world goal (gx,gy,gyaw). If ``final``, require the goal to
        be held continuously under tolerances for ``hold_s`` before
        ``deadline``. Near the goal, command zero velocity so the standing
        policy settles under the speed gate."""
        hold_start = None
        iyaw = 0.0
        while True:
            now = self.data.time
            pos = self._planar_pos()
            yaw = self._yaw()
            ex, ey = gx - pos[0], gy - pos[1]
            dist = math.hypot(ex, ey)
            eyaw = _wrap(gyaw - yaw) if regulate_yaw else 0.0
            tw = self.get_base_twist()
            speed = math.hypot(float(tw["linear_world_m_s"][0]),
                               float(tw["linear_world_m_s"][1]))
            if not final and dist <= pos_tol:
                return
            settled = (dist <= pos_tol and abs(eyaw) <= yaw_tol
                       and speed <= speed_tol)
            if final and settled:
                if hold_start is None:
                    hold_start = now
                elif now - hold_start >= hold_s:
                    return
            else:
                hold_start = None
            if now >= deadline - 1e-9:
                raise CapabilityError(
                    cap, "goal not settled within max_duration_s",
                    {"pos_err": dist, "yaw_err": eyaw, "speed": speed})
            # In-band -> command zero to let the standing policy settle.
            in_pos = dist <= max(0.05, pos_tol * 0.7)
            in_yaw = (abs(eyaw) <= max(0.05, yaw_tol * 0.7)) if regulate_yaw else True
            if final and in_pos and in_yaw:
                dt = min(self._CTRL_SLICE_S, deadline - now)
                self._drive_slice(cap, 0.0, 0.0, 0.0, dt)
                continue
            c, s = math.cos(yaw), math.sin(yaw)
            bx = c * ex + s * ey
            by = -s * ex + c * ey
            iyaw = float(np.clip(iyaw + eyaw * self._CTRL_SLICE_S, -0.8, 0.8))
            vx = float(np.clip(2.0 * bx, -self.G1_V_MAX, self.G1_V_MAX))
            vy = float(np.clip(2.0 * by, -self.G1_V_MAX, self.G1_V_MAX))
            yaw_cmd = (2.5 * eyaw + 1.5 * iyaw) if regulate_yaw else 0.0
            dt = min(self._CTRL_SLICE_S, deadline - now)
            self._drive_slice(cap, vx, vy, float(yaw_cmd), dt)

    # ======================================================================
    # G2 move_body_relative_pose
    # ======================================================================
    def move_body_relative_pose(self, request):
        cap = "G2"
        tr = request["translation_initial_yaw_m"]
        yaw_delta = float(request["yaw_delta_rad"])
        max_dur = float(request["max_duration_s"])
        if not isinstance(tr, (list, tuple)) or len(tr) != 2:
            raise CapabilityError(cap, "translation_initial_yaw_m must be length 2")
        tx, ty = float(tr[0]), float(tr[1])
        for name, val, lo, hi in (
            ("tx", tx, -self.G2_XY_MAX, self.G2_XY_MAX),
            ("ty", ty, -self.G2_XY_MAX, self.G2_XY_MAX),
            ("yaw_delta", yaw_delta, -self.G2_YAW_MAX, self.G2_YAW_MAX),
            ("max_duration_s", max_dur, 0.25, 8.0),
        ):
            if not math.isfinite(val) or val < lo - 1e-9 or val > hi + 1e-9:
                raise CapabilityError(cap, f"{name} out of contract bounds", {name: val})
        self._resolve()
        self._assert_finite_state(cap)

        p0 = self._planar_pos()
        yaw0 = self._yaw()
        c, s = math.cos(yaw0), math.sin(yaw0)
        gx = float(p0[0] + c * tx - s * ty)
        gy = float(p0[1] + s * tx + c * ty)
        gyaw = _wrap(yaw0 + yaw_delta)

        deadline = self.data.time + max_dur
        # Internal tolerances tighter than the contract (0.1/0.0873/0.1) so the
        # continuous 0.5 s hold reliably stays inside the criterion bounds.
        self._regulate_to(cap, gx, gy, gyaw, deadline,
                          pos_tol=0.06, yaw_tol=0.06, speed_tol=0.07,
                          hold_s=0.5, final=True)
        pos = self._planar_pos()
        return {
            "capability_id": cap, "status": "ok",
            "goal_world": [gx, gy, gyaw],
            "final_world": [float(pos[0]), float(pos[1]), self._yaw()],
            "position_error": float(math.hypot(gx - pos[0], gy - pos[1])),
            "yaw_error": float(_wrap(gyaw - self._yaw())),
            "sim_time": float(self.data.time),
        }

    # ======================================================================
    # G3 trace_planar_path
    # ======================================================================
    def trace_planar_path(self, request):
        cap = "G3"
        wps = request["waypoints_initial_yaw_m"]
        max_dur = float(request["max_duration_s"])
        if not isinstance(wps, (list, tuple)) or not (2 <= len(wps) <= 8):
            raise CapabilityError(cap, "waypoints_initial_yaw_m must have 2..8 items")
        if not math.isfinite(max_dur) or max_dur < 0.25 - 1e-9 or max_dur > 8.0 + 1e-9:
            raise CapabilityError(cap, "max_duration_s out of contract bounds")
        clean = []
        for w in wps:
            if not isinstance(w, (list, tuple)) or len(w) != 2:
                raise CapabilityError(cap, "each waypoint must be length 2")
            wx, wy = float(w[0]), float(w[1])
            for v in (wx, wy):
                if not math.isfinite(v) or v < -self.G3_XY_MAX - 1e-9 or v > self.G3_XY_MAX + 1e-9:
                    raise CapabilityError(cap, "waypoint out of contract bounds",
                                          {"waypoint": [wx, wy]})
            clean.append((wx, wy))
        self._resolve()
        self._assert_finite_state(cap)

        p0 = self._planar_pos()
        yaw0 = self._yaw()
        c, s = math.cos(yaw0), math.sin(yaw0)

        def to_world(wx, wy):
            return (float(p0[0] + c * wx - s * wy),
                    float(p0[1] + s * wx + c * wy))

        deadline = self.data.time + max_dur
        goals = [to_world(wx, wy) for (wx, wy) in clean]
        visited = []
        for i, (gx, gy) in enumerate(goals):
            last = (i == len(goals) - 1)
            cur = self._planar_pos()
            head = math.atan2(gy - cur[1], gx - cur[0])
            self._regulate_to(cap, gx, gy, head, deadline,
                              pos_tol=(0.06 if last else 0.08),
                              yaw_tol=(0.20 if last else math.pi),
                              speed_tol=(0.07 if last else 1e9),
                              hold_s=(0.5 if last else 0.0),
                              final=last,
                              regulate_yaw=not last)
            pv = self._planar_pos()
            visited.append([float(pv[0]), float(pv[1])])

        pos = self._planar_pos()
        endpoint = goals[-1]
        return {
            "capability_id": cap, "status": "ok",
            "goals_world": goals,
            "visited_world": visited,
            "endpoint_error": float(math.hypot(endpoint[0] - pos[0],
                                               endpoint[1] - pos[1])),
            "sim_time": float(self.data.time),
        }

    # ======================================================================
    # G4 set_body_height (retained-policy stand + posture height offset)
    # ======================================================================
    def set_body_height(self, request):
        cap = "G4"
        target_h = float(request["target_height_m"])
        max_dur = float(request["max_duration_s"])
        if not math.isfinite(target_h) or target_h < self.G4_H_MIN - 1e-9 or \
                target_h > self.G4_H_MAX + 1e-9:
            raise CapabilityError(cap, "target_height_m out of contract bounds",
                                  {"target_height_m": target_h})
        if not math.isfinite(max_dur) or max_dur < 0.25 - 1e-9 or max_dur > 8.0 + 1e-9:
            raise CapabilityError(cap, "max_duration_s out of contract bounds")
        self._resolve()
        self._assert_finite_state(cap)

        p0 = self._planar_pos()
        yaw0 = self._yaw()
        default = np.asarray(self.spec.default_joint_angles, dtype=np.float32)
        thigh_idx = [1, 4, 7, 10]
        calf_idx = [2, 5, 8, 11]
        jlo, jhi = self._joint_limits()
        dt = self._CTRL_SLICE_S
        n_sub = max(1, int(round(dt / float(self.model.opt.timestep))))
        deadline = self.data.time + max_dur
        hold_start = None
        offset = 0.0
        k_h = 6.0
        while True:
            now = self.data.time
            h = self._height()
            err = h - target_h  # positive => too tall => squat more
            roll, pitch = self._upright()
            settled = abs(err) <= 0.02 and abs(roll) <= 0.12 and abs(pitch) <= 0.12
            if settled:
                if hold_start is None:
                    hold_start = now
                elif now - hold_start >= 0.5:
                    break
            else:
                hold_start = None
            if now >= deadline - 1e-9:
                raise CapabilityError(cap, "height not settled in max_duration_s",
                                      {"height": h, "target": target_h})
            offset += k_h * err * dt
            offset = float(np.clip(offset, -0.6, 0.9))
            tgt = default.copy()
            for j in thigh_idx:
                tgt[j] = default[j] + offset
            for j in calf_idx:
                tgt[j] = default[j] - 2.0 * offset
            tgt = np.clip(tgt, jlo, jhi).astype(np.float32)
            self.set_joint_targets(tgt)
            self.step(n_sub)
            self._assert_finite_state(cap)
            self._support_ok(cap)
        pos = self._planar_pos()
        return {
            "capability_id": cap, "status": "ok",
            "target_height_m": target_h,
            "final_height_m": self._height(),
            "height_error": float(self._height() - target_h),
            "planar_displacement": float(np.linalg.norm(pos - p0)),
            "yaw_drift": float(_wrap(self._yaw() - yaw0)),
            "sim_time": float(self.data.time),
        }

    # ======================================================================
    # G5 hold_stable_stance  (recover WITHIN the requested duration window)
    # ======================================================================
    def hold_stable_stance(self, request):
        cap = "G5"
        duration = float(request["duration_s"])
        if not math.isfinite(duration) or duration < 1.0 - 1e-9 or \
                duration > 2.0 + 1e-9:
            raise CapabilityError(cap, "duration_s out of contract bounds",
                                  {"duration_s": duration})
        self._resolve()
        self._assert_finite_state(cap)

        # Single window of exactly ``duration`` sim seconds: the retained
        # policy recovers a level supported stance early (well inside the
        # 0.5 s recovery budget) and then holds it. Total sim time therefore
        # matches the requested duration (no extra recovery time appended).
        t0 = self.data.time
        t_end = t0 + duration
        recovered = False
        recovery_time = None
        while self.data.time < t_end - 1e-9:
            dt = min(self._CTRL_SLICE_S, t_end - self.data.time)
            self._drive_slice(cap, 0.0, 0.0, 0.0, dt)
            roll, pitch = self._upright()
            if not recovered and abs(roll) <= 0.0524 and abs(pitch) <= 0.0524:
                recovered = True
                recovery_time = self.data.time - t0
            # Bounded failure: recovery must occur inside the 0.5 s budget.
            if not recovered and (self.data.time - t0) >= 0.5 - 1e-9:
                raise CapabilityError(
                    cap, "roll/pitch not recovered within 0.5 s",
                    {"roll": roll, "pitch": pitch})

        tw = self.get_base_twist()
        return {
            "capability_id": cap, "status": "ok",
            "duration_s": duration,
            "recovery_time_s": float(recovery_time if recovery_time is not None else 0.0),
            "final_roll_pitch": list(self._upright()),
            "terminal_vertical_speed": float(abs(tw["linear_world_m_s"][2])),
            "sim_time": float(self.data.time),
        }


def build():
    """Construct the Go2 capability robot bound to the scene MJCF."""
    spec = Go2VelocityPolicySpec(
        base_body_name="base_link",
        joint_names=list(_JOINT_NAMES),
        actuator_names=list(_ACTUATOR_NAMES),
    )
    return Robot.from_mjcf("mjcf.xml", spec=spec)
