"""Go2 capability driver – Go2VelocityPolicySkeleton subclass."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from auto_adapter.skeletons.go2_velocity_policy import (
    Go2VelocityPolicySkeleton,
    Go2VelocityPolicySpec,
)

# ---------------------------------------------------------------------------
# Joint / actuator ordering required by the policy skeleton
# (joint_names and actuator_names must be parallel lists)
# ---------------------------------------------------------------------------
_JOINT_NAMES = [
    "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
    "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
    "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint",
    "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
]
_ACTUATOR_NAMES = [
    "FL_hip", "FL_thigh", "FL_calf",
    "FR_hip", "FR_thigh", "FR_calf",
    "RL_hip", "RL_thigh", "RL_calf",
    "RR_hip", "RR_thigh", "RR_calf",
]
# Default standing pose (from keyframe 0)
_DEFAULT_ANGLES = [
    0.0,  0.9, -1.8,   # FL
    0.0,  0.9, -1.8,   # FR
    0.0,  0.9, -1.8,   # RL
    0.0,  0.9, -1.8,   # RR
]

_SPEC = Go2VelocityPolicySpec(
    base_body_name="base_link",
    joint_names=_JOINT_NAMES,
    actuator_names=_ACTUATOR_NAMES,
    default_joint_angles=_DEFAULT_ANGLES,
    kp=20.0,
    kd=0.5,
    action_scale=0.25,
    policy_period_s=0.02,
    expected_physics_timestep_s=0.002,
    maximum_planar_speed_m_s=1.2,
    maximum_yaw_rate_rad_s=3.0,
    maximum_command_duration_s=30.0,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_POLICY_DT = 0.02   # seconds per policy step (chunk size for commands)
_MAX_CHUNK = 0.5    # max seconds per command_planar_velocity call


def _wrap_angle(a: float) -> float:
    """Wrap angle to [-pi, pi]."""
    return float((a + math.pi) % (2 * math.pi) - math.pi)


def _rot2yaw(R: np.ndarray) -> float:
    """Extract yaw from 3×3 rotation matrix."""
    return math.atan2(float(R[1, 0]), float(R[0, 0]))


def _body_frame_vel(twist: dict) -> tuple[float, float, float]:
    """Return (vx_body, vy_body, yaw_rate) from get_base_twist dict."""
    lby = twist["linear_body_yaw_m_s"]
    yr  = float(twist["yaw_rate_rad_s"])
    return float(lby[0]), float(lby[1]), yr


class CapabilityError(RuntimeError):
    """Bounded capability failure."""


class Robot(Go2VelocityPolicySkeleton):
    """Go2 capability robot."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _get_state(self):
        """Return (pos, R, twist) with a forward pass."""
        import mujoco
        mujoco.mj_forward(self.model, self.data)
        pos, R = self.get_base_pose()
        twist  = self.get_base_twist()
        return pos, R, twist

    def _check_finite(self, label: str = ""):
        if not (np.all(np.isfinite(self.data.qpos)) and
                np.all(np.isfinite(self.data.qvel))):
            raise CapabilityError(f"Non-finite state detected{': ' + label if label else ''}")

    def _step_cmd(self, vx: float, vy: float, yr: float, dt: float):
        """Issue command_planar_velocity in safe chunks."""
        remaining = dt
        while remaining > 1e-9:
            chunk = min(remaining, _MAX_CHUNK)
            self.command_planar_velocity(vx, vy, yr, chunk)
            remaining -= chunk
            self._check_finite()

    def _current_time(self) -> float:
        return float(self.data.time)

    # ------------------------------------------------------------------
    # G1 – track_planar_twist
    # ------------------------------------------------------------------
    def track_planar_twist(self, request: dict) -> dict:
        """Track a bounded planar body-frame twist for a fixed duration."""
        vx_req, vy_req = float(request["linear_velocity_body_m_s"][0]), \
                         float(request["linear_velocity_body_m_s"][1])
        yr_req   = float(request["yaw_rate_rad_s"])
        dur      = float(request["duration_s"])

        # Clamp to schema bounds
        vx_req = max(-0.4, min(0.4, vx_req))
        vy_req = max(-0.4, min(0.4, vy_req))
        yr_req = max(-1.0, min(1.0, yr_req))
        dur    = max(2.0,  min(4.0, dur))

        self._check_finite("track_planar_twist start")

        # Settle briefly to ensure stable initial state
        self.hold(0.1)

        # Tracking window: record final 1 s
        window_s   = 1.0
        window_vx  = []
        window_vy  = []
        window_yr  = []

        elapsed = 0.0
        chunk   = _POLICY_DT * 5   # 0.1 s chunks for feedback

        try:
            while elapsed < dur - 1e-9:
                step_dt = min(chunk, dur - elapsed)
                self._step_cmd(vx_req, vy_req, yr_req, step_dt)
                elapsed += step_dt

                if elapsed >= dur - window_s - 1e-9:
                    twist = self.get_base_twist()
                    bvx, bvy, byr = _body_frame_vel(twist)
                    window_vx.append(bvx)
                    window_vy.append(bvy)
                    window_yr.append(byr)

        except CapabilityError as e:
            self.hold(0.2)
            raise CapabilityError(f"track_planar_twist: support lost – {e}") from e

        # Stop
        self.hold(0.2)

        # Evaluate
        if window_vx:
            mean_vx = float(np.mean(window_vx))
            mean_vy = float(np.mean(window_vy))
            mean_yr = float(np.mean(window_yr))
        else:
            twist = self.get_base_twist()
            mean_vx, mean_vy, mean_yr = _body_frame_vel(twist)

        vel_err = math.hypot(mean_vx - vx_req, mean_vy - vy_req)
        yr_err  = abs(mean_yr - yr_req)

        # Direction error (deg) – only meaningful when requested speed > 0
        req_speed = math.hypot(vx_req, vy_req)
        if req_speed > 0.01:
            req_ang  = math.atan2(vy_req, vx_req)
            meas_ang = math.atan2(mean_vy, mean_vx)
            dir_err  = abs(math.degrees(_wrap_angle(meas_ang - req_ang)))
        else:
            dir_err = 0.0

        return {
            "mean_vx_body": mean_vx,
            "mean_vy_body": mean_vy,
            "mean_yr":      mean_yr,
            "planar_velocity_error": vel_err,
            "yaw_rate_error":        yr_err,
            "direction_error_deg":   dir_err,
        }

    # ------------------------------------------------------------------
    # G2 – move_body_relative_pose
    # ------------------------------------------------------------------
    def move_body_relative_pose(self, request: dict) -> dict:
        """Move the Go2 base to a pose relative to its initial yaw frame."""
        tx, ty   = float(request["translation_initial_yaw_m"][0]), \
                   float(request["translation_initial_yaw_m"][1])
        yaw_d    = float(request["yaw_delta_rad"])
        max_dur  = float(request["max_duration_s"])

        self._check_finite("move_body_relative_pose start")

        # Capture initial pose
        pos0, R0, _ = self._get_state()
        yaw0 = _rot2yaw(R0)
        x0, y0 = float(pos0[0]), float(pos0[1])

        # Target in world frame
        c0, s0 = math.cos(yaw0), math.sin(yaw0)
        tx_w = x0 + c0 * tx - s0 * ty
        ty_w = y0 + s0 * tx + c0 * ty
        yaw_tgt = yaw0 + yaw_d

        deadline = self._current_time() + max_dur
        hold_dur = 0.5
        hold_ok  = 0.0
        chunk    = _POLICY_DT * 5

        try:
            while self._current_time() < deadline:
                pos, R, _ = self._get_state()
                yaw_cur = _rot2yaw(R)
                dx = tx_w - float(pos[0])
                dy = ty_w - float(pos[1])
                dyaw = _wrap_angle(yaw_tgt - yaw_cur)

                pos_err  = math.hypot(dx, dy)
                yaw_err  = abs(dyaw)

                # Check hold criterion
                twist = self.get_base_twist()
                speed = math.hypot(*_body_frame_vel(twist)[:2])
                if pos_err <= 0.08 and yaw_err <= 0.07 and speed <= 0.08:
                    hold_ok += chunk
                    if hold_ok >= hold_dur:
                        break
                    self.hold(chunk)
                    continue
                else:
                    hold_ok = 0.0

                # Proportional velocity commands in body frame
                kp_lin = 1.2
                kp_yaw = 1.5
                # Rotate error to body frame
                c_cur, s_cur = math.cos(yaw_cur), math.sin(yaw_cur)
                vx_cmd = kp_lin * ( c_cur * dx + s_cur * dy)
                vy_cmd = kp_lin * (-s_cur * dx + c_cur * dy)
                yr_cmd = kp_yaw * dyaw

                # Clamp to policy limits
                vx_cmd = max(-0.4, min(0.4, vx_cmd))
                vy_cmd = max(-0.4, min(0.4, vy_cmd))
                yr_cmd = max(-1.0, min(1.0, yr_cmd))

                self._step_cmd(vx_cmd, vy_cmd, yr_cmd, chunk)

        except CapabilityError as e:
            self.hold(0.2)
            raise CapabilityError(f"move_body_relative_pose: {e}") from e

        # Terminal hold
        self.hold(0.3)

        pos_f, R_f, _ = self._get_state()
        yaw_f = _rot2yaw(R_f)
        twist_f = self.get_base_twist()
        speed_f = math.hypot(*_body_frame_vel(twist_f)[:2])
        pos_err_f = math.hypot(tx_w - float(pos_f[0]), ty_w - float(pos_f[1]))
        yaw_err_f = abs(_wrap_angle(yaw_tgt - yaw_f))

        if pos_err_f > 0.1 or yaw_err_f > 0.0873:
            raise CapabilityError(
                f"move_body_relative_pose: terminal error too large "
                f"(pos={pos_err_f:.3f} m, yaw={yaw_err_f:.4f} rad)"
            )

        return {
            "terminal_planar_position_error": pos_err_f,
            "wrapped_yaw_error": yaw_err_f,
            "planar_speed": speed_f,
        }

    # ------------------------------------------------------------------
    # G3 – trace_planar_path
    # ------------------------------------------------------------------
    def trace_planar_path(self, request: dict) -> dict:
        """Trace bounded planar waypoints in the initial-yaw frame."""
        wps_raw  = request["waypoints_initial_yaw_m"]
        max_dur  = float(request["max_duration_s"])

        self._check_finite("trace_planar_path start")

        # Capture initial pose
        pos0, R0, _ = self._get_state()
        yaw0 = _rot2yaw(R0)
        x0, y0 = float(pos0[0]), float(pos0[1])
        c0, s0 = math.cos(yaw0), math.sin(yaw0)

        # Convert waypoints to world frame
        waypoints_w = []
        for wp in wps_raw:
            wx_loc, wy_loc = float(wp[0]), float(wp[1])
            wx_w = x0 + c0 * wx_loc - s0 * wy_loc
            wy_w = y0 + s0 * wx_loc + c0 * wy_loc
            waypoints_w.append((wx_w, wy_w))

        deadline  = self._current_time() + max_dur
        chunk     = _POLICY_DT * 5
        wp_idx    = 0
        wp_tol    = 0.08
        hold_dur  = 0.5
        hold_ok   = 0.0
        kp_lin    = 1.5
        kp_yaw    = 1.5

        try:
            while self._current_time() < deadline:
                if wp_idx >= len(waypoints_w):
                    # All waypoints visited – terminal hold
                    hold_ok += chunk
                    if hold_ok >= hold_dur:
                        break
                    self.hold(chunk)
                    continue

                tgt_x, tgt_y = waypoints_w[wp_idx]
                pos, R, _ = self._get_state()
                yaw_cur = _rot2yaw(R)
                dx = tgt_x - float(pos[0])
                dy = tgt_y - float(pos[1])
                dist = math.hypot(dx, dy)

                if dist < wp_tol:
                    wp_idx += 1
                    hold_ok = 0.0
                    continue

                # Proportional velocity in body frame
                c_cur, s_cur = math.cos(yaw_cur), math.sin(yaw_cur)
                vx_cmd = kp_lin * ( c_cur * dx + s_cur * dy)
                vy_cmd = kp_lin * (-s_cur * dx + c_cur * dy)

                # Yaw toward waypoint
                desired_yaw = math.atan2(dy, dx)
                yr_cmd = kp_yaw * _wrap_angle(desired_yaw - yaw_cur)

                vx_cmd = max(-0.4, min(0.4, vx_cmd))
                vy_cmd = max(-0.4, min(0.4, vy_cmd))
                yr_cmd = max(-1.0, min(1.0, yr_cmd))

                self._step_cmd(vx_cmd, vy_cmd, yr_cmd, chunk)

        except CapabilityError as e:
            self.hold(0.2)
            raise CapabilityError(f"trace_planar_path: {e}") from e

        if wp_idx < len(waypoints_w):
            raise CapabilityError(
                f"trace_planar_path: timed out at waypoint {wp_idx}/{len(waypoints_w)}"
            )

        # Terminal hold
        self.hold(0.3)

        pos_f, R_f, _ = self._get_state()
        twist_f = self.get_base_twist()
        speed_f = math.hypot(*_body_frame_vel(twist_f)[:2])
        ep_x, ep_y = waypoints_w[-1]
        ep_err = math.hypot(ep_x - float(pos_f[0]), ep_y - float(pos_f[1]))

        return {
            "waypoints_visited": wp_idx,
            "endpoint_error_m":  ep_err,
            "endpoint_speed_m_s": speed_f,
        }

    # ------------------------------------------------------------------
    # G4 – set_body_height
    # ------------------------------------------------------------------
    def set_body_height(self, request: dict) -> dict:
        """Set Go2 body height while retaining a stable local stance."""
        tgt_h   = float(request["target_height_m"])
        max_dur = float(request["max_duration_s"])

        tgt_h = max(0.22, min(0.36, tgt_h))

        self._check_finite("set_body_height start")

        # Capture initial planar pose and yaw
        pos0, R0, _ = self._get_state()
        x0, y0 = float(pos0[0]), float(pos0[1])
        yaw0   = _rot2yaw(R0)

        # Compute target joint angles via simple leg-length scaling
        # Go2 leg: thigh + calf links; nominal height 0.27 m at angles [0, 0.9, -1.8]
        # Use IK: hip_height = L1*cos(theta1) + L2*cos(theta1+theta2)
        # L1 = L2 ≈ 0.213 m (thigh/calf length for Go2)
        L1, L2 = 0.213, 0.213
        h_leg = tgt_h  # approximate: body height ≈ leg extension

        # Solve 2-link IK for thigh/calf given desired vertical reach
        # cos(theta2) = (h_leg^2 - L1^2 - L2^2) / (2*L1*L2)
        cos2 = (h_leg**2 - L1**2 - L2**2) / (2 * L1 * L2)
        cos2 = max(-1.0, min(1.0, cos2))
        theta2 = -abs(math.acos(cos2))   # calf bends backward (negative)
        theta1 = math.atan2(h_leg, 0.0) - math.atan2(L2 * math.sin(theta2),
                                                       L1 + L2 * math.cos(theta2))
        theta1 = max(-1.5708, min(3.4907, theta1))
        theta2 = max(-2.7227, min(-0.83776, theta2))

        tgt_angles = [
            0.0, theta1, theta2,   # FL
            0.0, theta1, theta2,   # FR
            0.0, theta1, theta2,   # RL
            0.0, theta1, theta2,   # RR
        ]

        deadline = self._current_time() + max_dur
        hold_dur = 0.5
        hold_ok  = 0.0
        chunk    = _POLICY_DT * 5
        kp_h     = 2.0

        try:
            while self._current_time() < deadline:
                pos, R, _ = self._get_state()
                cur_h  = float(pos[2])
                h_err  = tgt_h - cur_h
                yaw_c  = _rot2yaw(R)

                # Planar drift correction
                dx = x0 - float(pos[0])
                dy = y0 - float(pos[1])
                c_c, s_c = math.cos(yaw_c), math.sin(yaw_c)
                vx_cmd = 1.0 * ( c_c * dx + s_c * dy)
                vy_cmd = 1.0 * (-s_c * dx + c_c * dy)
                yr_cmd = 1.5 * _wrap_angle(yaw0 - yaw_c)

                vx_cmd = max(-0.3, min(0.3, vx_cmd))
                vy_cmd = max(-0.3, min(0.3, vy_cmd))
                yr_cmd = max(-0.8, min(0.8, yr_cmd))

                # Check hold criterion
                twist = self.get_base_twist()
                speed = math.hypot(*_body_frame_vel(twist)[:2])
                roll  = math.atan2(float(R[2, 1]), float(R[2, 2]))
                pitch = math.asin(max(-1.0, min(1.0, -float(R[2, 0]))))

                if (abs(h_err) <= 0.025 and abs(roll) <= 0.15 and
                        abs(pitch) <= 0.15 and speed <= 0.08):
                    hold_ok += chunk
                    if hold_ok >= hold_dur:
                        break
                    self.hold(chunk)
                    continue
                else:
                    hold_ok = 0.0

                # Drive height via joint targets + planar correction
                self.set_joint_targets(tgt_angles)
                self.apply_joint_targets()
                self._step_cmd(vx_cmd, vy_cmd, yr_cmd, chunk)

        except CapabilityError as e:
            self.hold(0.2)
            raise CapabilityError(f"set_body_height: {e}") from e

        self.hold(0.3)

        pos_f, R_f, _ = self._get_state()
        h_err_f = abs(tgt_h - float(pos_f[2]))
        roll_f  = math.atan2(float(R_f[2, 1]), float(R_f[2, 2]))
        pitch_f = math.asin(max(-1.0, min(1.0, -float(R_f[2, 0]))))
        disp_f  = math.hypot(float(pos_f[0]) - x0, float(pos_f[1]) - y0)
        yaw_f   = _rot2yaw(R_f)
        yaw_drift = abs(_wrap_angle(yaw_f - yaw0))

        if h_err_f > 0.03:
            raise CapabilityError(
                f"set_body_height: height error {h_err_f:.3f} m > 0.03 m"
            )

        return {
            "body_height_error": h_err_f,
            "absolute_roll":     abs(roll_f),
            "absolute_pitch":    abs(pitch_f),
            "planar_displacement": disp_f,
            "yaw_drift":         yaw_drift,
        }

    # ------------------------------------------------------------------
    # G5 – hold_stable_stance
    # ------------------------------------------------------------------
    def hold_stable_stance(self, request: dict) -> dict:
        """Recover from one Framework reset disturbance and hold stable stance."""
        dur = float(request["duration_s"])
        dur = max(1.0, min(2.0, dur))

        self._check_finite("hold_stable_stance start")

        # Recovery phase: up to 0.5 s to bring roll/pitch within 0.0524 rad
        recovery_deadline = self._current_time() + 0.5
        chunk = _POLICY_DT * 5   # 0.1 s

        # Capture initial planar position for drift tracking
        pos0, R0, _ = self._get_state()
        x0, y0 = float(pos0[0]), float(pos0[1])
        h0     = float(pos0[2])

        try:
            # Recovery: hold with zero velocity command
            while self._current_time() < recovery_deadline:
                self.hold(chunk)
                pos, R, _ = self._get_state()
                roll  = math.atan2(float(R[2, 1]), float(R[2, 2]))
                pitch = math.asin(max(-1.0, min(1.0, -float(R[2, 0]))))
                if abs(roll) <= 0.0524 and abs(pitch) <= 0.0524:
                    break

            # Check recovery criterion
            pos, R, _ = self._get_state()
            roll  = math.atan2(float(R[2, 1]), float(R[2, 2]))
            pitch = math.asin(max(-1.0, min(1.0, -float(R[2, 0]))))
            if abs(roll) > 0.0524 or abs(pitch) > 0.0524:
                raise CapabilityError(
                    f"hold_stable_stance: recovery failed "
                    f"(roll={roll:.4f}, pitch={pitch:.4f})"
                )

            # Hold phase: maintain stance for requested duration
            hold_deadline = self._current_time() + dur
            roll_hist, pitch_hist, speed_hist, vz_hist = [], [], [], []

            while self._current_time() < hold_deadline:
                self.hold(chunk)
                pos, R, _ = self._get_state()
                twist = self.get_base_twist()
                bvx, bvy, _ = _body_frame_vel(twist)
                speed = math.hypot(bvx, bvy)
                vz    = abs(float(twist["linear_world_m_s"][2]))
                roll  = math.atan2(float(R[2, 1]), float(R[2, 2]))
                pitch = math.asin(max(-1.0, min(1.0, -float(R[2, 0]))))

                roll_hist.append(abs(roll))
                pitch_hist.append(abs(pitch))
                speed_hist.append(speed)
                vz_hist.append(vz)

                # Fail fast on gross instability
                if abs(roll) > 0.5 or abs(pitch) > 0.5:
                    raise CapabilityError(
                        f"hold_stable_stance: gross instability "
                        f"(roll={roll:.3f}, pitch={pitch:.3f})"
                    )

        except CapabilityError:
            raise
        except Exception as e:
            raise CapabilityError(f"hold_stable_stance: unexpected error – {e}") from e

        pos_f, R_f, _ = self._get_state()
        roll_f  = math.atan2(float(R_f[2, 1]), float(R_f[2, 2]))
        pitch_f = math.asin(max(-1.0, min(1.0, -float(R_f[2, 0]))))
        h_drift = abs(float(pos_f[2]) - h0)
        p_drift = math.hypot(float(pos_f[0]) - x0, float(pos_f[1]) - y0)
        mean_speed = float(np.mean(speed_hist)) if speed_hist else 0.0
        term_vz    = float(vz_hist[-1]) if vz_hist else 0.0

        return {
            "recovery_roll":        abs(roll_f),
            "recovery_pitch":       abs(pitch_f),
            "hold_roll_pitch_max":  max(roll_hist + pitch_hist) if roll_hist else 0.0,
            "height_drift":         h_drift,
            "planar_drift":         p_drift,
            "mean_planar_speed":    mean_speed,
            "terminal_vertical_speed": term_vz,
        }


# ---------------------------------------------------------------------------
# Module-level factory
# ---------------------------------------------------------------------------
def build() -> Robot:
    """Return a Robot instance loaded from the local mjcf.xml."""
    return Robot.from_mjcf("mjcf.xml", spec=_SPEC)
