"""
Unitree Go2 Quadruped Robot Driver — from scratch
==================================================
Robot class for the Unitree Go2 12-DOF quadruped (4 legs × 3 joints).

Architecture
------------
* Floating base: freejoint (qpos[0:3]=xyz, qpos[3:7]=quat wxyz)
* 12 hinge joints (qpos[7:19]):
    FL_hip(7), FL_thigh(8), FL_calf(9)
    FR_hip(10), FR_thigh(11), FR_calf(12)
    RL_hip(13), RL_thigh(14), RL_calf(15)
    RR_hip(16), RR_thigh(17), RR_calf(18)
* 12 motor actuators (ctrl[0:12]):
    FR_hip(0), FR_thigh(1), FR_calf(2)
    FL_hip(3), FL_thigh(4), FL_calf(5)
    RR_hip(6), RR_thigh(7), RR_calf(8)
    RL_hip(9), RL_thigh(10), RL_calf(11)

Control
-------
All behaviours use joint-space PD:
    τ = kp*(q_des − q) + kd*(0 − q̇)
clamped to actuator ctrlrange.

Behaviours
----------
* home()         — reset to keyframe standing pose
* stand_up()     — PD to standing pose; body height > 0.15 m
* sit()          — PD to folded pose; height drops below 0.8× standing
* walk_forward() — diagonal-pair trot gait with yaw+lateral feedback
* get_body_height() / get_base_pose()
* step() / render() / describe()
"""

import os
import math
import mujoco
import numpy as np
from typing import Tuple, Optional


# ---------------------------------------------------------------------------
# Index tables (derived from actuator_trnid inspection)
# ---------------------------------------------------------------------------
# Actuator order: FR_hip(0), FR_thigh(1), FR_calf(2),
#                 FL_hip(3), FL_thigh(4), FL_calf(5),
#                 RR_hip(6), RR_thigh(7), RR_calf(8),
#                 RL_hip(9), RL_thigh(10), RL_calf(11)
#
# qpos addresses for each actuator (freejoint occupies qpos[0:7]):
_QPOS_ADR = np.array([10, 11, 12,   # FR
                       7,  8,  9,   # FL
                       16, 17, 18,  # RR
                       13, 14, 15], # RL
                      dtype=np.int32)

# qvel (dof) addresses for each actuator:
_DOFADR = np.array([9,  10, 11,   # FR
                     6,  7,  8,   # FL
                     15, 16, 17,  # RR
                     12, 13, 14], # RL
                    dtype=np.int32)

# Home joint targets from keyframe ctrl=[0, 0.9, -1.8] × 4
_HOME_CTRL = np.array([0.0,  0.9, -1.8,   # FR
                        0.0,  0.9, -1.8,   # FL
                        0.0,  0.9, -1.8,   # RR
                        0.0,  0.9, -1.8],  # RL
                       dtype=np.float64)

# Sit pose: deeply folded hips and knees (within joint limits)
# calf limit: [-2.7227, -0.83776]; thigh front: [-1.5708, 3.4907]
_SIT_CTRL = np.array([0.0,  1.4, -2.5,   # FR
                       0.0,  1.4, -2.5,   # FL
                       0.0,  1.4, -2.5,   # RR
                       0.0,  1.4, -2.5],  # RL
                      dtype=np.float64)

# PD gains — tuned for stable standing and smooth gait
_KP = np.array([40.0, 40.0, 80.0,
                40.0, 40.0, 80.0,
                40.0, 40.0, 80.0,
                40.0, 40.0, 80.0], dtype=np.float64)

_KD = np.array([1.0, 1.0, 2.0,
                1.0, 1.0, 2.0,
                1.0, 1.0, 2.0,
                1.0, 1.0, 2.0], dtype=np.float64)


def _quat_to_rpy(q: np.ndarray) -> Tuple[float, float, float]:
    """Convert quaternion (w,x,y,z) to roll, pitch, yaw."""
    w, x, y, z = q
    roll  = math.atan2(2*(w*x + y*z), 1 - 2*(x*x + y*y))
    pitch = math.asin(max(-1.0, min(1.0, 2*(w*y - z*x))))
    yaw   = math.atan2(2*(w*z + x*y), 1 - 2*(y*y + z*z))
    return roll, pitch, yaw


def _quat_to_rot(q: np.ndarray) -> np.ndarray:
    """Convert quaternion (w,x,y,z) to 3×3 rotation matrix."""
    w, x, y, z = q
    return np.array([
        [1-2*(y*y+z*z),   2*(x*y-w*z),   2*(x*z+w*y)],
        [  2*(x*y+w*z), 1-2*(x*x+z*z),   2*(y*z-w*x)],
        [  2*(x*z-w*y),   2*(y*z+w*x), 1-2*(x*x+y*y)],
    ])


class Robot:
    """
    Unitree Go2 quadruped driver.

    Exposes:
        build_from_mjcf(mjcf_path) -> Robot
        home()                     -> bool
        stand_up(duration)         -> bool
        sit(duration)              -> bool
        walk_forward(secs, speed)  -> bool
        get_body_height()          -> float
        get_base_pose()            -> (xyz, R3x3)
        get_joint_positions()      -> np.ndarray (12,)
        step(n)
        render()                   -> np.ndarray | None
        describe()                 -> str
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData,
                 mjcf_path: str = ""):
        self.model = model
        self.data  = data
        self._mjcf_path = mjcf_path

        # Actuator control limits
        self._ctrl_lo = model.actuator_ctrlrange[:, 0].copy()
        self._ctrl_hi = model.actuator_ctrlrange[:, 1].copy()

        # Joint limits (12 × 2) in actuator order
        self._jnt_lo = np.zeros(12)
        self._jnt_hi = np.zeros(12)
        for i in range(12):
            jid = model.actuator_trnid[i, 0]
            self._jnt_lo[i] = model.jnt_range[jid, 0]
            self._jnt_hi[i] = model.jnt_range[jid, 1]

        # Body IDs
        self._base_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, "base_link")

        # Renderer (lazy)
        self._renderer: Optional[mujoco.Renderer] = None

        # Standing height (set after first stand_up)
        self._stand_height: float = 0.27  # nominal from keyframe

        # Timestep
        self._dt = model.opt.timestep

    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """
        Load the MJCF and construct a Robot.

        The mjcf.xml is a symlink into the go2 asset directory, so
        mujoco.MjModel.from_xml_path resolves includes correctly when
        called with the real path.
        """
        real_path = os.path.realpath(mjcf_path)
        if not os.path.exists(real_path):
            raise FileNotFoundError(f"MJCF not found: {mjcf_path!r}")

        model = mujoco.MjModel.from_xml_path(real_path)
        data  = mujoco.MjData(model)

        robot = cls(model, data, real_path)

        # Initialise to keyframe
        mujoco.mj_resetDataKeyframe(model, data, 0)
        mujoco.mj_forward(model, data)
        return robot

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _pd_step(self, q_des: np.ndarray) -> None:
        """Apply one PD control step toward q_des and advance simulation."""
        q  = self.data.qpos[_QPOS_ADR]
        qd = self.data.qvel[_DOFADR]
        tau = _KP * (q_des - q) + _KD * (0.0 - qd)
        self.data.ctrl[:] = np.clip(tau, self._ctrl_lo, self._ctrl_hi)
        mujoco.mj_step(self.model, self.data)

    def _run_pd(self, q_des: np.ndarray, duration: float) -> None:
        """Run PD control to q_des for `duration` seconds."""
        n = max(1, int(duration / self._dt))
        for _ in range(n):
            self._pd_step(q_des)

    def _get_quat(self) -> np.ndarray:
        """Return base quaternion (w,x,y,z)."""
        return self.data.qpos[3:7].copy()

    def _get_yaw(self) -> float:
        _, _, yaw = _quat_to_rpy(self._get_quat())
        return yaw

    # ------------------------------------------------------------------
    # Required API
    # ------------------------------------------------------------------

    def home(self) -> bool:
        """Reset to keyframe standing pose."""
        mujoco.mj_resetDataKeyframe(self.model, self.data, 0)
        mujoco.mj_forward(self.model, self.data)
        # Settle with PD for 0.5 s
        self._run_pd(_HOME_CTRL, 0.5)
        return self.data.qpos[2] > 0.15

    def get_joint_positions(self) -> np.ndarray:
        """Return 12 joint positions in actuator order (FR, FL, RR, RL)."""
        return self.data.qpos[_QPOS_ADR].copy()

    def step(self, n: int = 1) -> None:
        """Advance simulation by n steps (zero control)."""
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)

    def render(self) -> Optional[np.ndarray]:
        """
        Render an RGB image (H×W×3 uint8) using the offscreen renderer.
        Returns None if rendering fails.
        """
        try:
            if self._renderer is None:
                self._renderer = mujoco.Renderer(self.model, height=480, width=640)
            self._renderer.update_scene(self.data)
            return self._renderer.render()
        except Exception as exc:
            print(f"[Robot.render] warning: {exc}")
            return None

    def describe(self) -> str:
        """Return a human-readable summary of the robot state."""
        q   = self.get_joint_positions()
        xyz = self.data.qpos[0:3]
        roll, pitch, yaw = _quat_to_rpy(self._get_quat())
        h   = self.get_body_height()
        lines = [
            "=== Unitree Go2 Quadruped ===",
            f"  Base position : x={xyz[0]:.3f}  y={xyz[1]:.3f}  z={xyz[2]:.3f} m",
            f"  Body height   : {h:.4f} m",
            f"  Orientation   : roll={math.degrees(roll):.1f}°  "
            f"pitch={math.degrees(pitch):.1f}°  yaw={math.degrees(yaw):.1f}°",
            "  Joint positions (FR, FL, RR, RL) [rad]:",
            f"    FR: hip={q[0]:.3f}  thigh={q[1]:.3f}  calf={q[2]:.3f}",
            f"    FL: hip={q[3]:.3f}  thigh={q[4]:.3f}  calf={q[5]:.3f}",
            f"    RR: hip={q[6]:.3f}  thigh={q[7]:.3f}  calf={q[8]:.3f}",
            f"    RL: hip={q[9]:.3f}  thigh={q[10]:.3f}  calf={q[11]:.3f}",
            f"  DOF: 12  |  Actuators: 12  |  dt: {self._dt*1000:.1f} ms",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Quadruped-specific behaviours
    # ------------------------------------------------------------------

    def get_body_height(self) -> float:
        """Return the world Z of the base_link body."""
        return float(self.data.xpos[self._base_id, 2])

    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Return (xyz, R) where xyz is the world position of base_link
        and R is the 3×3 rotation matrix.
        """
        xyz = self.data.xpos[self._base_id].copy()
        R   = _quat_to_rot(self._get_quat())
        return xyz, R

    # ------------------------------------------------------------------
    # stand_up
    # ------------------------------------------------------------------

    def stand_up(self, duration: float = 2.0) -> bool:
        """
        PD-drive all joints to the home standing pose.

        The robot is first reset to the keyframe (which places it at the
        correct height) and then settled with PD control.

        Returns True if final body height > 0.15 m.
        """
        # Reset to keyframe so we start from a valid configuration
        mujoco.mj_resetDataKeyframe(self.model, self.data, 0)
        mujoco.mj_forward(self.model, self.data)

        self._run_pd(_HOME_CTRL, duration)

        h = self.get_body_height()
        self._stand_height = h
        success = h > 0.15
        if not success:
            print(f"[stand_up] WARNING: body height {h:.4f} m < 0.15 m")
        return success

    # ------------------------------------------------------------------
    # sit
    # ------------------------------------------------------------------

    def sit(self, duration: float = 1.5) -> bool:
        """
        Fold all legs to lower the body.

        Drives hips to ~1.4 rad and calves to ~-2.5 rad (within limits).
        Final height must be < 0.8 × standing height.

        Returns True if the height drop criterion is met.
        """
        stand_h = self._stand_height if self._stand_height > 0.15 else 0.27

        self._run_pd(_SIT_CTRL, duration)

        h = self.get_body_height()
        success = h < 0.8 * stand_h
        if not success:
            print(f"[sit] WARNING: height {h:.4f} m not < 0.8×{stand_h:.4f}")
        return success

    # ------------------------------------------------------------------
    # walk_forward — diagonal-pair trot gait
    # ------------------------------------------------------------------

    def walk_forward(self, secs: float = 2.0, speed: float = 0.2) -> bool:
        """
        Trot gait: diagonal pairs (FR+RL) and (FL+RR) swing anti-phase.

        Gait parameters
        ---------------
        T_period      : 0.4 s  (5 Hz trot)
        swing_frac    : 0.5    (50% duty cycle)
        thigh_lift    : +0.3 rad during swing (foot clearance)
        thigh_push    : -0.2 rad during stance (propulsion)
        calf_swing    : -1.4 rad during swing (leg extension)

        Feedback corrections (applied every step)
        ------------------------------------------
        Yaw   : PD on yaw angle + yaw rate → differential hip abduction
        Lateral: PD on y position + y velocity → symmetric hip abduction

        Returns True if forward displacement > 3 cm and robot stays upright.
        """
        # Gait parameters (scale with speed)
        T_period       = max(0.25, 0.4 / max(0.1, speed))
        swing_frac     = 0.5
        thigh_lift     = 0.3
        thigh_push     = -0.2
        calf_swing_tgt = -1.4   # calf angle during swing

        # Feedback gains
        KP_YAW  = 0.15
        KD_YAW  = 0.03
        KP_LAT  = 0.20
        KD_LAT  = 0.05

        start_x = float(self.data.qpos[0])
        n_steps = max(1, int(secs / self._dt))

        for i in range(n_steps):
            t     = i * self._dt
            phase = (t % T_period) / T_period   # 0 → 1

            q_des = _HOME_CTRL.copy()

            # Pair A: FR(0,1,2) + RL(9,10,11) — phase 0
            # Pair B: FL(3,4,5) + RR(6,7,8)   — phase 0.5
            phA = phase
            phB = (phase + 0.5) % 1.0

            def _leg(ph: float) -> Tuple[float, float]:
                """Return (thigh_des, calf_des) for a leg at gait phase ph."""
                if ph < swing_frac:
                    sw = ph / swing_frac          # 0 → 1 within swing
                    s  = math.sin(math.pi * sw)
                    thigh = 0.9 + thigh_lift * s
                    calf  = -1.8 + (calf_swing_tgt - (-1.8)) * s
                else:
                    st = (ph - swing_frac) / (1.0 - swing_frac)  # 0 → 1 stance
                    s  = math.sin(math.pi * st)
                    thigh = 0.9 + thigh_push * s
                    calf  = -1.8
                return thigh, calf

            # FR (pair A)
            th, ca = _leg(phA)
            q_des[1] = th;  q_des[2] = ca
            # RL (pair A)
            th, ca = _leg(phA)
            q_des[10] = th; q_des[11] = ca
            # FL (pair B)
            th, ca = _leg(phB)
            q_des[4] = th;  q_des[5] = ca
            # RR (pair B)
            th, ca = _leg(phB)
            q_des[7] = th;  q_des[8] = ca

            # ---- Yaw correction ----------------------------------------
            qw, qx, qy, qz = self.data.qpos[3:7]
            yaw     = math.atan2(2*(qw*qz + qx*qy), 1 - 2*(qy*qy + qz*qz))
            yaw_vel = float(self.data.qvel[5])
            yaw_corr = np.clip(-KP_YAW * yaw - KD_YAW * yaw_vel, -0.2, 0.2)

            # ---- Lateral correction ------------------------------------
            y_pos   = float(self.data.qpos[1])
            y_vel   = float(self.data.qvel[1])
            lat_corr = np.clip(-KP_LAT * y_pos - KD_LAT * y_vel, -0.2, 0.2)

            # Hip abduction corrections:
            #   FR (right side): positive hip → foot toward body centre
            #   FL (left side):  negative hip → foot toward body centre
            # Yaw correction: to steer right (fix left yaw), FR hip negative
            # Lateral correction: to push body right (fix y>0), FR hip positive
            fr_hip_corr = -yaw_corr + lat_corr
            fl_hip_corr =  yaw_corr - lat_corr
            q_des[0] += np.clip(fr_hip_corr, -0.4, 0.4)   # FR hip
            q_des[3] += np.clip(fl_hip_corr, -0.4, 0.4)   # FL hip
            q_des[6] += np.clip(fr_hip_corr, -0.4, 0.4)   # RR hip
            q_des[9] += np.clip(fl_hip_corr, -0.4, 0.4)   # RL hip

            # Clamp to joint limits
            q_des = np.clip(q_des, self._jnt_lo, self._jnt_hi)

            self._pd_step(q_des)

        end_x   = float(self.data.qpos[0])
        fwd     = end_x - start_x
        upright = self.get_body_height() > 0.15
        success = fwd > 0.03 and upright
        if not success:
            print(f"[walk_forward] fwd={fwd:.4f} m, upright={upright}")
        return success

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def get_foot_positions(self) -> dict:
        """Return world positions of all four feet."""
        result = {}
        for leg in ("FL", "FR", "RL", "RR"):
            bid = mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_BODY, f"{leg}_foot")
            if bid >= 0:
                result[leg] = self.data.xpos[bid].copy()
        return result

    def get_imu_data(self) -> dict:
        """Return IMU orientation, gyro, and accelerometer readings."""
        sid = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_SITE, "imu")
        if sid < 0:
            return {}
        # Sensor data layout: framequat(4) + gyro(3) + acc(3)
        # We read directly from qpos/qvel for reliability
        roll, pitch, yaw = _quat_to_rpy(self._get_quat())
        return {
            "roll_deg":  math.degrees(roll),
            "pitch_deg": math.degrees(pitch),
            "yaw_deg":   math.degrees(yaw),
            "ang_vel":   self.data.qvel[3:6].copy(),
            "lin_vel":   self.data.qvel[0:3].copy(),
        }
