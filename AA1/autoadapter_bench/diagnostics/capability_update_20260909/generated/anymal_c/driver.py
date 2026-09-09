"""ANYmal-C capability driver – QuadrupedPDGaitSkeleton subclass."""
from __future__ import annotations

import math
import os
import numpy as np
import mujoco

from auto_adapter.skeletons.quadruped_pd_gait import QuadrupedPDGaitSkeleton
from auto_adapter.skeletons.base import QuadrupedSpec

# ---------------------------------------------------------------------------
# Spec – filled from MJCF / study analysis
# ---------------------------------------------------------------------------
_ANYMAL_SPEC = QuadrupedSpec(
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
    # keyframe ctrl: [0, 0.7, -1.4, 0, 0.7, -1.4, 0, -0.7, 1.4, 0, -0.7, 1.4]
    home_qpos=[0.0, 0.7, -1.4, 0.0, 0.7, -1.4, 0.0, -0.7, 1.4, 0.0, -0.7, 1.4],
    kp=100.0,
    kd=2.0,
    gait_freq_hz=1.5,
    swing_amp_thigh=0.12,
    swing_amp_calf=0.18,
    thigh_forward_sign=1.0,
    gait_phases={"LF": 0.0, "RH": 0.0, "RF": 0.5, "LH": 0.5},
    body_height_target=0.374,
    vx_max=0.4,
    vy_max=0.4,
    vyaw_max=1.0,
    sim_dt=0.002,
    actuation="joint_position",
)

# Height bounds from capability schema
_HEIGHT_MIN = 0.2989979050104695
_HEIGHT_MAX = 0.44849685751570423
_HEIGHT_NOM = 0.374  # nominal standing height from keyframe


def _wrap_angle(a: float) -> float:
    """Wrap angle to [-pi, pi]."""
    return float(np.arctan2(np.sin(a), np.cos(a)))


def _yaw_from_quat(q: np.ndarray) -> float:
    """Extract yaw from quaternion [w, x, y, z]."""
    w, x, y, z = q
    return float(np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))


def _rot2d(yaw: float) -> np.ndarray:
    c, s = np.cos(yaw), np.sin(yaw)
    return np.array([[c, s], [-s, c]])


class CapabilityError(RuntimeError):
    """Bounded capability failure."""


class Robot(QuadrupedPDGaitSkeleton):
    """ANYmal-C capability driver."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_state(self):
        """Return (pos_xy, yaw, height, twist_body_xy, yaw_rate) from live data."""
        mujoco.mj_forward(self.model, self.data)
        pos, R = self.get_base_pose()
        quat = np.array(self.data.qpos[3:7])  # w x y z
        yaw = _yaw_from_quat(quat)
        twist = self.get_base_twist()
        vel_body = np.array(twist["linear_body_yaw_m_s"])
        yaw_rate = float(twist["yaw_rate_rad_s"])
        return pos[:2].copy(), yaw, float(pos[2]), vel_body, yaw_rate

    def _check_finite(self):
        """Raise CapabilityError if base state is not finite."""
        pos, R = self.get_base_pose()
        if not (np.all(np.isfinite(pos)) and np.all(np.isfinite(R))):
            raise CapabilityError("Base state is not finite – aborting.")

    def _step_with_velocity(self, vx: float, vy: float, yaw_rate: float, n_steps: int):
        """Advance n_steps with a planar velocity command (body frame)."""
        dt = float(self.model.opt.timestep)
        gait_period = 1.0 / float(self.spec.gait_freq_hz)
        for _ in range(n_steps):
            q_target = self._gait_posture(self._gait_time, vx, vy, yaw_rate)
            self._apply_pd(q_target)
            self.step(1)
            self._gait_time = (self._gait_time + dt) % gait_period

    def _body_frame_velocity(self, vx_world: float, vy_world: float, yaw: float):
        """Rotate world-frame velocity to body frame."""
        R = _rot2d(yaw)
        v_world = np.array([vx_world, vy_world])
        return R @ v_world

    def _height_to_joint_targets(self, target_h: float) -> np.ndarray:
        """Compute joint targets for a given body height via simple scaling."""
        # Scale home_qpos HFE/KFE angles proportionally to height
        h_nom = _HEIGHT_NOM
        ratio = np.clip(target_h / h_nom, 0.6, 1.3)
        q = np.array(self.spec.home_qpos, dtype=float)
        # Indices: HFE=1,4,7,10  KFE=2,5,8,11
        for i in [1, 4]:   # front HFE
            q[i] = q[i] / ratio
        for i in [2, 5]:   # front KFE
            q[i] = q[i] / ratio
        for i in [7, 10]:  # rear HFE (negative)
            q[i] = q[i] / ratio
        for i in [8, 11]:  # rear KFE (positive)
            q[i] = q[i] / ratio
        return q

    # ------------------------------------------------------------------
    # G1 – track_planar_twist
    # ------------------------------------------------------------------

    def track_planar_twist(self, request: dict) -> dict:
        """Track a bounded planar body-frame twist for a fixed duration."""
        vx, vy = float(request["linear_velocity_body_m_s"][0]), float(request["linear_velocity_body_m_s"][1])
        yaw_rate = float(request["yaw_rate_rad_s"])
        duration = float(request["duration_s"])

        # Validate bounds
        if not (-0.4 <= vx <= 0.4 and -0.4 <= vy <= 0.4):
            raise CapabilityError("linear_velocity_body_m_s out of bounds")
        if not (-1.0 <= yaw_rate <= 1.0):
            raise CapabilityError("yaw_rate_rad_s out of bounds")
        if not (2.0 <= duration <= 4.0):
            raise CapabilityError("duration_s out of bounds")

        self._check_finite()

        dt = float(self.model.opt.timestep)
        n_total = max(1, int(round(duration / dt)))
        window_s = 1.0
        n_window = max(1, int(round(window_s / dt)))
        n_pre = max(0, n_total - n_window)

        vel_errors, yaw_errors = [], []

        # Pre-window steps
        for _ in range(n_pre):
            self._check_finite()
            self._step_with_velocity(vx, vy, yaw_rate, 1)

        # Final-window steps – collect metrics
        for _ in range(n_window):
            self._check_finite()
            self._step_with_velocity(vx, vy, yaw_rate, 1)
            twist = self.get_base_twist()
            vb = np.array(twist["linear_body_yaw_m_s"])
            yr = float(twist["yaw_rate_rad_s"])
            cmd = np.array([vx, vy])
            vel_errors.append(float(np.linalg.norm(vb - cmd)))
            yaw_errors.append(abs(yr - yaw_rate))

        # Stop motion
        self.stop(duration=0.2)

        mean_vel_err = float(np.mean(vel_errors)) if vel_errors else float("inf")
        mean_yaw_err = float(np.mean(yaw_errors)) if yaw_errors else float("inf")

        # Direction error (degrees) – only meaningful if cmd speed > 0
        cmd_speed = math.hypot(vx, vy)
        if cmd_speed > 0.01 and vel_errors:
            twist = self.get_base_twist()
            vb = np.array(twist["linear_body_yaw_m_s"])
            cmd_dir = math.atan2(vy, vx)
            act_dir = math.atan2(float(vb[1]), float(vb[0]))
            dir_err_deg = abs(math.degrees(_wrap_angle(act_dir - cmd_dir)))
        else:
            dir_err_deg = 0.0

        success = (mean_vel_err <= 0.1 and mean_yaw_err <= 0.3 and dir_err_deg <= 10.0)
        return {
            "success": success,
            "mean_velocity_error_m_s": mean_vel_err,
            "mean_yaw_rate_error_rad_s": mean_yaw_err,
            "direction_error_deg": dir_err_deg,
        }

    # ------------------------------------------------------------------
    # G2 – move_body_relative_pose
    # ------------------------------------------------------------------

    def move_body_relative_pose(self, request: dict) -> dict:
        """Move the ANYmal-C base to a pose relative to its initial yaw frame."""
        tx, ty = float(request["translation_initial_yaw_m"][0]), float(request["translation_initial_yaw_m"][1])
        yaw_delta = float(request["yaw_delta_rad"])
        max_dur = float(request["max_duration_s"])

        if not (-0.3 <= tx <= 0.3 and -0.3 <= ty <= 0.3):
            raise CapabilityError("translation_initial_yaw_m out of bounds")
        if not (-0.6 <= yaw_delta <= 0.6):
            raise CapabilityError("yaw_delta_rad out of bounds")
        if not (0.25 <= max_dur <= 8.0):
            raise CapabilityError("max_duration_s out of bounds")

        self._check_finite()

        # Capture initial state
        pos0, R0 = self.get_base_pose()
        quat0 = np.array(self.data.qpos[3:7])
        yaw0 = _yaw_from_quat(quat0)
        xy0 = pos0[:2].copy()

        # Target in world frame
        R_init = _rot2d(yaw0)
        # initial_yaw frame -> world: rotate by yaw0
        t_world = R_init.T @ np.array([tx, ty])
        target_xy = xy0 + t_world
        target_yaw = yaw0 + yaw_delta

        dt = float(self.model.opt.timestep)
        n_max = max(1, int(round(max_dur / dt)))
        hold_s = 0.5
        n_hold = max(1, int(round(hold_s / dt)))

        # PD-style planar controller gains
        kp_xy = 0.8
        kp_yaw = 1.2
        v_max = 0.3
        yr_max = 0.8

        hold_ok_count = 0
        settled = False

        for step_i in range(n_max):
            self._check_finite()
            pos, _ = self.get_base_pose()
            quat = np.array(self.data.qpos[3:7])
            yaw = _yaw_from_quat(quat)
            xy = pos[:2]

            err_world = target_xy - xy
            err_yaw = _wrap_angle(target_yaw - yaw)

            # Rotate error to body frame
            err_body = _rot2d(yaw) @ err_world
            vx = float(np.clip(kp_xy * err_body[0], -v_max, v_max))
            vy = float(np.clip(kp_xy * err_body[1], -v_max, v_max))
            yr = float(np.clip(kp_yaw * err_yaw, -yr_max, yr_max))

            self._step_with_velocity(vx, vy, yr, 1)

            # Check settling
            pos_err = float(np.linalg.norm(err_world))
            twist = self.get_base_twist()
            speed = float(np.linalg.norm(twist["linear_body_yaw_m_s"]))
            if pos_err <= 0.1 and abs(err_yaw) <= 0.0873 and speed <= 0.1:
                hold_ok_count += 1
                if hold_ok_count >= n_hold:
                    settled = True
                    break
            else:
                hold_ok_count = 0

        # Terminal hold if not yet settled
        if not settled:
            self.stop(duration=0.3)

        # Final measurement
        pos_f, _ = self.get_base_pose()
        quat_f = np.array(self.data.qpos[3:7])
        yaw_f = _yaw_from_quat(quat_f)
        twist_f = self.get_base_twist()
        speed_f = float(np.linalg.norm(twist_f["linear_body_yaw_m_s"]))
        pos_err_f = float(np.linalg.norm(target_xy - pos_f[:2]))
        yaw_err_f = abs(_wrap_angle(target_yaw - yaw_f))

        success = (pos_err_f <= 0.1 and yaw_err_f <= 0.0873 and speed_f <= 0.1)
        if not settled and not success:
            raise CapabilityError(
                f"Could not settle: pos_err={pos_err_f:.3f} yaw_err={yaw_err_f:.3f} speed={speed_f:.3f}"
            )
        return {
            "success": success,
            "terminal_position_error_m": pos_err_f,
            "terminal_yaw_error_rad": yaw_err_f,
            "terminal_speed_m_s": speed_f,
        }

    # ------------------------------------------------------------------
    # G3 – trace_planar_path
    # ------------------------------------------------------------------

    def trace_planar_path(self, request: dict) -> dict:
        """Trace bounded planar waypoints in the initial-yaw frame."""
        waypoints_raw = request["waypoints_initial_yaw_m"]
        max_dur = float(request["max_duration_s"])

        if not (2 <= len(waypoints_raw) <= 8):
            raise CapabilityError("waypoints count out of bounds [2,8]")
        if not (0.25 <= max_dur <= 8.0):
            raise CapabilityError("max_duration_s out of bounds")

        waypoints = [np.array([float(w[0]), float(w[1])]) for w in waypoints_raw]
        for w in waypoints:
            if not (np.all(w >= -0.4) and np.all(w <= 0.4)):
                raise CapabilityError("waypoint coordinate out of bounds [-0.4, 0.4]")

        self._check_finite()

        # Capture initial frame
        pos0, _ = self.get_base_pose()
        quat0 = np.array(self.data.qpos[3:7])
        yaw0 = _yaw_from_quat(quat0)
        xy0 = pos0[:2].copy()
        R_init_T = _rot2d(yaw0)  # initial_yaw -> world rotation (transpose of R_init)

        # Convert waypoints to world frame
        wp_world = []
        for w in waypoints:
            wp_world.append(xy0 + R_init_T.T @ w)

        dt = float(self.model.opt.timestep)
        n_max = max(1, int(round(max_dur / dt)))
        hold_s = 0.5
        n_hold = max(1, int(round(hold_s / dt)))

        kp_xy = 0.9
        kp_yaw = 1.0
        v_max = 0.35
        yr_max = 0.8
        wp_tol = 0.08  # waypoint acceptance radius

        wp_idx = 0
        n_wps = len(wp_world)
        hold_ok_count = 0
        settled = False
        cross_track_errors = []
        waypoint_errors = [None] * n_wps

        for step_i in range(n_max):
            self._check_finite()
            pos, _ = self.get_base_pose()
            quat = np.array(self.data.qpos[3:7])
            yaw = _yaw_from_quat(quat)
            xy = pos[:2]

            # Advance waypoint index if close enough
            while wp_idx < n_wps and np.linalg.norm(wp_world[wp_idx] - xy) < wp_tol:
                waypoint_errors[wp_idx] = float(np.linalg.norm(wp_world[wp_idx] - xy))
                wp_idx += 1

            if wp_idx >= n_wps:
                # All waypoints visited – hold at last
                target_xy = wp_world[-1]
                err_world = target_xy - xy
                err_yaw = 0.0
                twist = self.get_base_twist()
                speed = float(np.linalg.norm(twist["linear_body_yaw_m_s"]))
                pos_err = float(np.linalg.norm(err_world))
                if pos_err <= 0.1 and speed <= 0.1:
                    hold_ok_count += 1
                    if hold_ok_count >= n_hold:
                        settled = True
                        break
                else:
                    hold_ok_count = 0
            else:
                target_xy = wp_world[wp_idx]
                err_world = target_xy - xy

            # Cross-track error (perpendicular to path segment)
            if wp_idx > 0:
                seg_start = wp_world[wp_idx - 1]
                seg_end = wp_world[min(wp_idx, n_wps - 1)]
                seg = seg_end - seg_start
                seg_len = np.linalg.norm(seg)
                if seg_len > 1e-6:
                    seg_dir = seg / seg_len
                    ct = float(abs(np.cross(seg_dir, xy - seg_start)))
                    cross_track_errors.append(ct)

            err_body = _rot2d(yaw) @ err_world
            vx = float(np.clip(kp_xy * err_body[0], -v_max, v_max))
            vy = float(np.clip(kp_xy * err_body[1], -v_max, v_max))
            # Face direction of travel
            if np.linalg.norm(err_world) > 0.05:
                desired_yaw = math.atan2(float(err_world[1]), float(err_world[0]))
                yr = float(np.clip(kp_yaw * _wrap_angle(desired_yaw - yaw), -yr_max, yr_max))
            else:
                yr = 0.0
            self._step_with_velocity(vx, vy, yr, 1)

        if not settled:
            self.stop(duration=0.3)

        # Fill any unvisited waypoint errors
        for i in range(n_wps):
            if waypoint_errors[i] is None:
                waypoint_errors[i] = float(np.linalg.norm(wp_world[i] - pos[:2]))

        pos_f, _ = self.get_base_pose()
        twist_f = self.get_base_twist()
        speed_f = float(np.linalg.norm(twist_f["linear_body_yaw_m_s"]))
        endpoint_err = float(np.linalg.norm(wp_world[-1] - pos_f[:2]))
        mean_ct = float(np.mean(cross_track_errors)) if cross_track_errors else 0.0
        max_wp_err = max(waypoint_errors)

        success = (max_wp_err <= 0.1 and mean_ct <= 0.15 and endpoint_err <= 0.1 and speed_f <= 0.1)
        if not settled and not success:
            raise CapabilityError(
                f"Path not completed: wp_err={max_wp_err:.3f} ct={mean_ct:.3f} ep={endpoint_err:.3f}"
            )
        return {
            "success": success,
            "max_waypoint_error_m": max_wp_err,
            "mean_cross_track_error_m": mean_ct,
            "endpoint_error_m": endpoint_err,
            "endpoint_speed_m_s": speed_f,
        }

    # ------------------------------------------------------------------
    # G4 – set_body_height
    # ------------------------------------------------------------------

    def set_body_height(self, request: dict) -> dict:
        """Set ANYmal-C body height while retaining a stable local stance."""
        target_h = float(request["target_height_m"])
        max_dur = float(request["max_duration_s"])

        if not (_HEIGHT_MIN <= target_h <= _HEIGHT_MAX):
            raise CapabilityError(f"target_height_m {target_h} out of bounds")
        if not (0.25 <= max_dur <= 8.0):
            raise CapabilityError("max_duration_s out of bounds")

        self._check_finite()

        # Capture initial planar pose and yaw
        pos0, _ = self.get_base_pose()
        quat0 = np.array(self.data.qpos[3:7])
        yaw0 = _yaw_from_quat(quat0)
        xy0 = pos0[:2].copy()

        # Compute target joint posture for desired height
        q_target = self._height_to_joint_targets(target_h)

        dt = float(self.model.opt.timestep)
        n_max = max(1, int(round(max_dur / dt)))
        hold_s = 0.5
        n_hold = max(1, int(round(hold_s / dt)))

        # Interpolate to target posture
        interp_steps = min(n_max // 2, int(round(1.5 / dt)))
        q_start = self.get_joint_positions().copy()

        hold_ok_count = 0
        settled = False

        for step_i in range(n_max):
            self._check_finite()

            # Smooth interpolation during first half
            if step_i < interp_steps:
                alpha = (step_i + 1) / interp_steps
                q_cmd = q_start + alpha * (q_target - q_start)
            else:
                q_cmd = q_target

            # Apply planar stabilization (keep xy and yaw)
            pos, _ = self.get_base_pose()
            quat = np.array(self.data.qpos[3:7])
            yaw = _yaw_from_quat(quat)
            xy = pos[:2]
            err_xy = xy0 - xy
            err_yaw = _wrap_angle(yaw0 - yaw)
            err_body = _rot2d(yaw) @ err_xy
            vx = float(np.clip(0.5 * err_body[0], -0.15, 0.15))
            vy = float(np.clip(0.5 * err_body[1], -0.15, 0.15))
            yr = float(np.clip(0.8 * err_yaw, -0.5, 0.5))

            # Blend posture command with gait for stabilization
            gait_q = self._gait_posture(self._gait_time, vx, vy, yr)
            # Use posture command directly (height regulation dominates)
            self.apply_pd_posture(q_cmd)
            self.step(1)
            self._gait_time = (self._gait_time + dt) % (1.0 / self.spec.gait_freq_hz)

            # Check settling
            h = self.get_body_height()
            h_err = abs(h - target_h)
            pos2, _ = self.get_base_pose()
            quat2 = np.array(self.data.qpos[3:7])
            yaw2 = _yaw_from_quat(quat2)
            roll = math.asin(max(-1.0, min(1.0, float(self.data.xmat[self._base_body_id].reshape(3,3)[2,1]))))
            pitch = math.asin(max(-1.0, min(1.0, -float(self.data.xmat[self._base_body_id].reshape(3,3)[2,0]))))

            if (h_err <= 0.03 and abs(roll) <= 0.1745 and abs(pitch) <= 0.1745
                    and np.linalg.norm(pos2[:2] - xy0) <= 0.05
                    and abs(_wrap_angle(yaw2 - yaw0)) <= 0.0873):
                hold_ok_count += 1
                if hold_ok_count >= n_hold:
                    settled = True
                    break
            else:
                hold_ok_count = 0

        if not settled:
            self.stop(duration=0.2)

        # Final measurements
        h_f = self.get_body_height()
        pos_f, _ = self.get_base_pose()
        quat_f = np.array(self.data.qpos[3:7])
        yaw_f = _yaw_from_quat(quat_f)
        R_f = self.data.xmat[self._base_body_id].reshape(3, 3)
        roll_f = math.asin(max(-1.0, min(1.0, float(R_f[2, 1]))))
        pitch_f = math.asin(max(-1.0, min(1.0, -float(R_f[2, 0]))))
        disp_f = float(np.linalg.norm(pos_f[:2] - xy0))
        yaw_drift_f = abs(_wrap_angle(yaw_f - yaw0))
        h_err_f = abs(h_f - target_h)

        success = (h_err_f <= 0.03 and abs(roll_f) <= 0.1745 and abs(pitch_f) <= 0.1745
                   and disp_f <= 0.05 and yaw_drift_f <= 0.0873)
        if not settled and not success:
            raise CapabilityError(
                f"Height not settled: h_err={h_err_f:.3f} roll={roll_f:.3f} pitch={pitch_f:.3f}"
            )
        return {
            "success": success,
            "height_error_m": h_err_f,
            "roll_rad": roll_f,
            "pitch_rad": pitch_f,
            "planar_displacement_m": disp_f,
            "yaw_drift_rad": yaw_drift_f,
        }

    # ------------------------------------------------------------------
    # G5 – hold_stable_stance
    # ------------------------------------------------------------------

    def hold_stable_stance(self, request: dict) -> dict:
        """Recover from one Framework reset disturbance and hold stable stance."""
        duration = float(request["duration_s"])
        if not (1.0 <= duration <= 2.0):
            raise CapabilityError("duration_s out of bounds [1.0, 2.0]")

        self._check_finite()

        dt = float(self.model.opt.timestep)
        recovery_s = 0.5
        n_recovery = max(1, int(round(recovery_s / dt)))
        n_hold = max(1, int(round(duration / dt)))

        # Capture initial position for drift tracking
        pos0, _ = self.get_base_pose()
        xy0 = pos0[:2].copy()
        h0 = float(pos0[2])

        # Recovery phase: stand_up to home posture
        q_home = np.array(self.spec.home_qpos, dtype=float)
        q_cur = self.get_joint_positions().copy()

        for step_i in range(n_recovery):
            self._check_finite()
            alpha = min(1.0, (step_i + 1) / n_recovery)
            q_cmd = q_cur + alpha * (q_home - q_cur)
            self.apply_pd_posture(q_cmd)
            self.step(1)

        # Check recovery criterion: roll/pitch <= 0.0524 rad
        R_r = self.data.xmat[self._base_body_id].reshape(3, 3)
        roll_r = math.asin(max(-1.0, min(1.0, float(R_r[2, 1]))))
        pitch_r = math.asin(max(-1.0, min(1.0, -float(R_r[2, 0]))))
        if abs(roll_r) > 0.0524 or abs(pitch_r) > 0.0524:
            # Try harder recovery
            self.stand_up(duration=1.0)
            R_r = self.data.xmat[self._base_body_id].reshape(3, 3)
            roll_r = math.asin(max(-1.0, min(1.0, float(R_r[2, 1]))))
            pitch_r = math.asin(max(-1.0, min(1.0, -float(R_r[2, 0]))))
            if abs(roll_r) > 0.0524 or abs(pitch_r) > 0.0524:
                raise CapabilityError(
                    f"Recovery failed: roll={roll_r:.4f} pitch={pitch_r:.4f}"
                )

        # Hold phase: maintain posture
        speeds = []
        roll_vals, pitch_vals = [], []
        h_vals = []
        xy_vals = []
        vz_vals = []

        for step_i in range(n_hold):
            self._check_finite()
            self.apply_pd_posture(q_home)
            self.step(1)

            R_h = self.data.xmat[self._base_body_id].reshape(3, 3)
            roll_h = math.asin(max(-1.0, min(1.0, float(R_h[2, 1]))))
            pitch_h = math.asin(max(-1.0, min(1.0, -float(R_h[2, 0]))))
            roll_vals.append(abs(roll_h))
            pitch_vals.append(abs(pitch_h))

            pos_h, _ = self.get_base_pose()
            h_vals.append(float(pos_h[2]))
            xy_vals.append(pos_h[:2].copy())

            twist_h = self.get_base_twist()
            vb = np.array(twist_h["linear_body_yaw_m_s"])
            speeds.append(float(np.linalg.norm(vb)))
            vz_vals.append(abs(float(twist_h["linear_world_m_s"][2])))

        # Evaluate criteria
        max_roll = float(np.max(roll_vals)) if roll_vals else 0.0
        max_pitch = float(np.max(pitch_vals)) if pitch_vals else 0.0
        h_drift = abs(float(np.mean(h_vals)) - h0) if h_vals else 0.0
        xy_arr = np.array(xy_vals)
        planar_drift = float(np.max(np.linalg.norm(xy_arr - xy0, axis=1))) if len(xy_arr) > 0 else 0.0
        mean_speed = float(np.mean(speeds)) if speeds else 0.0
        term_vz = float(vz_vals[-1]) if vz_vals else 0.0

        success = (
            abs(roll_r) <= 0.0524 and abs(pitch_r) <= 0.0524
            and max_roll <= 0.0873 and max_pitch <= 0.0873
            and h_drift <= 0.03
            and planar_drift <= 0.05
            and mean_speed <= 0.05
            and term_vz <= 0.05
        )
        if not success:
            raise CapabilityError(
                f"Stance not stable: roll={max_roll:.3f} pitch={max_pitch:.3f} "
                f"h_drift={h_drift:.3f} xy_drift={planar_drift:.3f} speed={mean_speed:.3f}"
            )
        return {
            "success": success,
            "recovery_roll_rad": abs(roll_r),
            "recovery_pitch_rad": abs(pitch_r),
            "hold_max_roll_rad": max_roll,
            "hold_max_pitch_rad": max_pitch,
            "height_drift_m": h_drift,
            "planar_drift_m": planar_drift,
            "mean_planar_speed_m_s": mean_speed,
            "terminal_vertical_speed_m_s": term_vz,
        }


# ---------------------------------------------------------------------------
# Module-level build()
# ---------------------------------------------------------------------------

def build() -> Robot:
    """Return a Robot instance loaded from the local mjcf.xml."""
    here = os.path.dirname(os.path.abspath(__file__))
    mjcf_path = os.path.join(here, "mjcf.xml")
    return Robot.from_mjcf(mjcf_path, spec=_ANYMAL_SPEC)
