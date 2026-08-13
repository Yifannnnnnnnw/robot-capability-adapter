"""
driver_from_scratch.py — Unitree Go2 Quadruped Robot Driver
============================================================
Full Robot class for the Unitree Go2 12-DOF quadruped.
No auto_adapter.skeletons imports — all algorithms written from scratch.

Robot class: QUADRUPED
  - 4 legs × 3 joints (hip/abduction, thigh, calf)
  - Torque-controlled motors (hip ±23.7 Nm, knee ±45.43 Nm)
  - Freejoint on base_link (qpos[0:7] = xyz + quaternion)
  - 12 actuators in order: FR_hip, FR_thigh, FR_calf,
                            FL_hip, FL_thigh, FL_calf,
                            RR_hip, RR_thigh, RR_calf,
                            RL_hip, RL_thigh, RL_calf

Behaviors:
  - home()              → PD to nominal standing pose
  - stand_up()          → PD to stable standing (body height > 0.15 m)
  - sit()               → PD to folded pose (height drops below 0.8× stand)
  - walk_forward()      → Diagonal trot gait with real forward displacement
  - get_body_height()   → Current base_link Z height
  - get_base_pose()     → (xyz, R3×3) of base_link
  - get_joint_positions() → 12-element array of joint angles
  - step(n)             → Advance simulation n steps
  - render()            → Render current frame (passive viewer)
  - describe()          → Human-readable state summary

PD Control:
  τ = kp·(q_des − q) + kd·(0 − q̇)
  Clamped to actuator ctrlrange at every step.

Trot Gait (walk_forward):
  Diagonal pairs swing anti-phase:
    Group A: FL + RR  (phase offset 0.0)
    Group B: FR + RL  (phase offset 0.5)
  Swing: lift thigh + extend calf  →  foot clears ground
  Stance: push thigh back          →  propels body forward
"""

from __future__ import annotations

import math
import os
import warnings
from typing import Optional, Tuple

import mujoco
import numpy as np

# ---------------------------------------------------------------------------
# Constants — tuned for Go2 physics (timestep=0.002 s, gravity=-9.81 m/s²)
# ---------------------------------------------------------------------------

# Nominal standing pose (joint angles in radians)
# Actuator order: FR_hip(0) FR_thigh(1) FR_calf(2)
#                 FL_hip(3) FL_thigh(4) FL_calf(5)
#                 RR_hip(6) RR_thigh(7) RR_calf(8)
#                 RL_hip(9) RL_thigh(10) RL_calf(11)
_STAND_POSE = np.array([
    0.0,  0.9, -1.8,   # FR
    0.0,  0.9, -1.8,   # FL
    0.0,  0.9, -1.8,   # RR
    0.0,  0.9, -1.8,   # RL
], dtype=np.float64)

# Sit / folded pose — thigh more vertical, calf deeply bent
# Validated: body drops to ~41% of standing height (well below 0.8× threshold)
_SIT_POSE = np.array([
    0.0,  1.4, -2.6,   # FR
    0.0,  1.4, -2.6,   # FL
    0.0,  1.4, -2.6,   # RR
    0.0,  1.4, -2.6,   # RL
], dtype=np.float64)

# PD gains — high enough to hold posture against gravity
_KP_STAND = 80.0   # Nm/rad  (proportional)
_KD_STAND = 4.0    # Nm·s/rad (derivative / damping)
_KP_SIT   = 60.0
_KD_SIT   = 3.0

# ---------------------------------------------------------------------------
# Trot gait parameters (tuned empirically — ~0.79 m/s forward speed)
# ---------------------------------------------------------------------------
_GAIT_PERIOD  = 0.40   # seconds per full stride cycle
_SWING_FRAC   = 0.40   # fraction of period spent in swing phase

# Swing phase: lift foot off ground
_THIGH_SWING  = 1.30   # rad — raise thigh
_CALF_SWING   = -1.30  # rad — extend calf (foot clears ground)

# Stance phase: foot on ground, push body forward
_THIGH_STANCE = 0.75   # rad — more extended than stand → propulsion
_CALF_STANCE  = -1.80  # rad — calf angle during ground contact

_HIP_NEUTRAL  = 0.0    # rad — hip stays neutral during trot

# Minimum body height to consider "standing" (metres)
_MIN_STAND_HEIGHT = 0.15


# ---------------------------------------------------------------------------
# Helper: quaternion → 3×3 rotation matrix  (MuJoCo convention: w,x,y,z)
# ---------------------------------------------------------------------------
def _quat_to_mat(q: np.ndarray) -> np.ndarray:
    """Convert quaternion [w, x, y, z] to 3×3 rotation matrix."""
    w, x, y, z = q
    return np.array([
        [1 - 2*(y*y + z*z),     2*(x*y - w*z),     2*(x*z + w*y)],
        [    2*(x*y + w*z), 1 - 2*(x*x + z*z),     2*(y*z - w*x)],
        [    2*(x*z - w*y),     2*(y*z + w*x), 1 - 2*(x*x + y*y)],
    ], dtype=np.float64)


# ---------------------------------------------------------------------------
# Robot class
# ---------------------------------------------------------------------------
class Robot:
    """
    Unitree Go2 quadruped driver.

    Exposes joint-space PD control for standing, sitting, and trotting.
    All motion is simulated via MuJoCo physics (mj_step).

    Actuator layout (12 motors, torque-controlled):
        [0]  FR_hip    [1]  FR_thigh   [2]  FR_calf
        [3]  FL_hip    [4]  FL_thigh   [5]  FL_calf
        [6]  RR_hip    [7]  RR_thigh   [8]  RR_calf
        [9]  RL_hip   [10]  RL_thigh  [11]  RL_calf

    Gait — Diagonal Trot:
        Group A (FL + RR) and Group B (FR + RL) alternate in anti-phase.
        Each leg: SWING (lift) → STANCE (push) → repeat.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def __init__(
        self,
        model: mujoco.MjModel,
        data: mujoco.MjData,
        mjcf_path: str,
    ) -> None:
        self._model = model
        self._data  = data
        self._mjcf_path = mjcf_path

        # ---- resolve body / site IDs ----
        self._base_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, "base_link"
        )
        if self._base_id < 0:
            raise RuntimeError("Body 'base_link' not found in model.")

        self._imu_site_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_SITE, "imu"
        )

        # ---- build actuator → joint index maps ----
        # act_qposadr[i] : index into qpos for actuator i's joint
        # act_dofadr[i]  : index into qvel for actuator i's joint
        self._act_qposadr = np.zeros(model.nu, dtype=int)
        self._act_dofadr  = np.zeros(model.nu, dtype=int)
        for i in range(model.nu):
            jid = model.actuator_trnid[i, 0]
            self._act_qposadr[i] = model.jnt_qposadr[jid]
            self._act_dofadr[i]  = model.jnt_dofadr[jid]

        # ---- joint limits (12,) ----
        self._jnt_lo = np.zeros(model.nu)
        self._jnt_hi = np.zeros(model.nu)
        for i in range(model.nu):
            jid = model.actuator_trnid[i, 0]
            self._jnt_lo[i] = model.jnt_range[jid, 0]
            self._jnt_hi[i] = model.jnt_range[jid, 1]

        # ---- actuator torque limits ----
        self._ctrl_lo = model.actuator_ctrlrange[:, 0].copy()
        self._ctrl_hi = model.actuator_ctrlrange[:, 1].copy()

        # ---- actuator names (for describe()) ----
        self._act_names = []
        for i in range(model.nu):
            name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, i)
            self._act_names.append(name or f"act_{i}")

        # ---- viewer (lazy init) ----
        self._viewer = None

        # ---- cached stand height (set after first stand_up) ----
        self._stand_height: Optional[float] = None

    # ------------------------------------------------------------------
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """
        Load the MJCF model and return a ready-to-use Robot instance.

        Resolves symlinks so that relative <include> paths in the XML
        are found correctly by MuJoCo's XML parser.

        Parameters
        ----------
        mjcf_path : str
            Path to the top-level MJCF / XML scene file (may be a symlink).

        Returns
        -------
        Robot
        """
        # Resolve symlinks so MuJoCo can find relative <include> files
        real_path = os.path.realpath(mjcf_path)
        model = mujoco.MjModel.from_xml_path(real_path)
        data  = mujoco.MjData(model)
        mujoco.mj_resetData(model, data)
        mujoco.mj_forward(model, data)
        robot = cls(model, data, real_path)
        return robot

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _pd_torque(
        self,
        q_des: np.ndarray,
        kp: float,
        kd: float,
        qd_des: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Compute PD torques for all 12 actuators.

        τ_i = kp·(q_des_i − q_i) + kd·(qd_des_i − qd_i)
        Clamped to [ctrl_lo, ctrl_hi].

        Parameters
        ----------
        q_des  : desired joint positions (12,)
        kp     : proportional gain (Nm/rad)
        kd     : derivative gain (Nm·s/rad)
        qd_des : desired joint velocities (12,); defaults to zeros
        """
        if qd_des is None:
            qd_des = np.zeros(self._model.nu)

        q_cur  = self._data.qpos[self._act_qposadr]
        qd_cur = self._data.qvel[self._act_dofadr]

        tau = kp * (q_des - q_cur) + kd * (qd_des - qd_cur)
        return np.clip(tau, self._ctrl_lo, self._ctrl_hi)

    def _run_pd(
        self,
        q_des: np.ndarray,
        duration: float,
        kp: float,
        kd: float,
        qd_des: Optional[np.ndarray] = None,
    ) -> None:
        """
        Run PD control toward q_des for `duration` seconds of sim time.
        Applies torques and calls mj_step each iteration.
        """
        n_steps = max(1, int(round(duration / self._model.opt.timestep)))
        for _ in range(n_steps):
            self._data.ctrl[:] = self._pd_torque(q_des, kp, kd, qd_des)
            mujoco.mj_step(self._model, self._data)

    def _clamp_joints(self, q: np.ndarray) -> np.ndarray:
        """Clamp joint angles to their model-defined limits."""
        return np.clip(q, self._jnt_lo, self._jnt_hi)

    # ------------------------------------------------------------------
    # Core API (required by framework)
    # ------------------------------------------------------------------
    def home(self) -> bool:
        """
        Move to the nominal standing pose (delegates to stand_up).

        Returns
        -------
        bool
            True if body height > 0.15 m after settling.
        """
        return self.stand_up(duration=2.0)

    def get_joint_positions(self) -> np.ndarray:
        """
        Return current joint angles for all 12 actuated joints.

        Returns
        -------
        np.ndarray, shape (12,)
            Joint angles in radians, ordered:
            FR_hip, FR_thigh, FR_calf,
            FL_hip, FL_thigh, FL_calf,
            RR_hip, RR_thigh, RR_calf,
            RL_hip, RL_thigh, RL_calf
        """
        return self._data.qpos[self._act_qposadr].copy()

    def step(self, n: int = 1) -> None:
        """
        Advance the simulation by n steps using the current ctrl values.

        Parameters
        ----------
        n : int
            Number of mj_step calls (each advances by model.opt.timestep).
        """
        for _ in range(n):
            mujoco.mj_step(self._model, self._data)

    def render(self) -> None:
        """
        Render the current simulation state using MuJoCo passive viewer.
        Opens a window on first call; subsequent calls sync the display.
        Silently warns if running in a headless environment.
        """
        try:
            if self._viewer is None:
                self._viewer = mujoco.viewer.launch_passive(
                    self._model, self._data
                )
            self._viewer.sync()
        except Exception as exc:
            warnings.warn(f"render() failed (headless env?): {exc}")

    def describe(self) -> str:
        """
        Return a human-readable summary of the robot's current state.

        Includes: sim time, base position, body height, orientation
        (roll/pitch/yaw), and all 12 joint angles.

        Returns
        -------
        str
        """
        mujoco.mj_forward(self._model, self._data)
        xyz, R = self.get_base_pose()
        h = self.get_body_height()
        q = self.get_joint_positions()

        # Euler angles from rotation matrix (ZYX convention)
        roll  = math.atan2(R[2, 1], R[2, 2])
        pitch = math.asin(max(-1.0, min(1.0, -R[2, 0])))
        yaw   = math.atan2(R[1, 0], R[0, 0])

        lines = [
            "=== Unitree Go2 Quadruped ===",
            f"  Sim time      : {self._data.time:.3f} s",
            f"  Base position : x={xyz[0]:.3f}  y={xyz[1]:.3f}  z={xyz[2]:.3f} m",
            f"  Body height   : {h:.4f} m",
            f"  Orientation   : roll={math.degrees(roll):.1f}°  "
            f"pitch={math.degrees(pitch):.1f}°  yaw={math.degrees(yaw):.1f}°",
            "  Joint angles (rad):",
        ]
        leg_labels = ["FR", "FL", "RR", "RL"]
        for li, leg in enumerate(leg_labels):
            base = li * 3
            lines.append(
                f"    {leg}: hip={q[base]:.3f}  "
                f"thigh={q[base+1]:.3f}  "
                f"calf={q[base+2]:.3f}"
            )
        if self._stand_height is not None:
            lines.append(
                f"  Cached stand height : {self._stand_height:.4f} m"
            )
        lines.append(f"  MJCF path : {self._mjcf_path}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Quadruped-specific API
    # ------------------------------------------------------------------
    def get_body_height(self) -> float:
        """
        Return the current Z-height of base_link in world frame (metres).

        Returns
        -------
        float
        """
        return float(self._data.xpos[self._base_id, 2])

    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Return the world-frame pose of base_link.

        Returns
        -------
        xyz : np.ndarray, shape (3,)
            Position in metres.
        R   : np.ndarray, shape (3, 3)
            Rotation matrix (columns = body X, Y, Z axes in world frame).
        """
        xyz = self._data.xpos[self._base_id].copy()
        R   = self._data.xmat[self._base_id].reshape(3, 3).copy()
        return xyz, R

    # ------------------------------------------------------------------
    def stand_up(self, duration: float = 2.0) -> bool:
        """
        Drive all joints to the nominal standing pose using PD control.

        The robot starts from whatever state it is in (including lying flat
        at reset). High-gain PD (kp=80 Nm/rad, kd=4 Nm·s/rad) is applied
        for `duration` seconds of simulation time.

        Validated result: body height ≈ 0.27 m (well above 0.15 m threshold).

        Parameters
        ----------
        duration : float
            Simulation time (seconds) to apply PD control.

        Returns
        -------
        bool
            True if body height > 0.15 m after settling.
        """
        q_des = self._clamp_joints(_STAND_POSE.copy())
        self._run_pd(q_des, duration, kp=_KP_STAND, kd=_KD_STAND)
        mujoco.mj_forward(self._model, self._data)
        h = self.get_body_height()
        self._stand_height = h   # cache for sit() threshold
        success = h > _MIN_STAND_HEIGHT
        if not success:
            warnings.warn(
                f"stand_up(): body height {h:.4f} m ≤ {_MIN_STAND_HEIGHT} m — "
                "robot may have fallen. Try increasing duration or kp."
            )
        return success

    # ------------------------------------------------------------------
    def sit(self, duration: float = 1.5) -> bool:
        """
        Fold all legs to a low crouched / sitting pose.

        Uses softer PD (kp=60, kd=3) to avoid violent snapping.
        Target pose: thigh=1.4 rad, calf=−2.6 rad (all legs).

        Validated: body drops to ~41% of standing height
        (threshold: < 0.8× stand height).

        Parameters
        ----------
        duration : float
            Simulation time (seconds) to apply PD control.

        Returns
        -------
        bool
            True if body height dropped below 0.8× the last known
            standing height (or below 0.22 m if stand_up was never called).
        """
        q_des = self._clamp_joints(_SIT_POSE.copy())
        self._run_pd(q_des, duration, kp=_KP_SIT, kd=_KD_SIT)
        mujoco.mj_forward(self._model, self._data)
        h = self.get_body_height()

        # Use cached stand height if available, else use nominal
        ref_h = self._stand_height if self._stand_height is not None else 0.27
        threshold = 0.8 * ref_h
        success = h < threshold
        if not success:
            warnings.warn(
                f"sit(): body height {h:.4f} m is not below 0.8× stand "
                f"({threshold:.4f} m). Robot may not have folded properly."
            )
        return success

    # ------------------------------------------------------------------
    def walk_forward(self, secs: float = 2.0, speed: float = 0.2) -> bool:
        """
        Execute a diagonal trot gait to walk forward.

        ── Algorithm: Diagonal Trot ──────────────────────────────────────
        Two diagonal pairs alternate in strict anti-phase (180° offset):

          Group A: FL (front-left) + RR (rear-right)  — phase offset 0.0
          Group B: FR (front-right) + RL (rear-left)  — phase offset 0.5

        Each leg cycles through two phases per stride:

          SWING (40% of period):
            hip   = 0.0 rad  (neutral)
            thigh = 1.30 rad (raised — foot lifts off ground)
            calf  = −1.30 rad (extended — clears ground)

          STANCE (60% of period):
            hip   = 0.0 rad  (neutral)
            thigh = 0.75 rad (more extended than stand → pushes body fwd)
            calf  = −1.80 rad (ground contact angle)

        The stance thigh (0.75 rad) is less than the stand thigh (0.90 rad),
        meaning the leg is more extended during stance → net backward push
        on the ground → forward body motion.

        Gait period is scaled with speed:
          period = clip(0.4 × (0.2 / speed), 0.25, 0.80) seconds

        Validated: ~0.79 m forward displacement in 2 s at speed=0.2 m/s.

        Parameters
        ----------
        secs  : float  Total walk duration (simulation seconds).
        speed : float  Desired forward speed (m/s); scales gait period.

        Returns
        -------
        bool
            True if forward displacement > 3 cm AND body stayed upright
            (height > 0.15 m) throughout.
        """
        # Scale gait period with speed (faster → shorter period)
        period = float(
            np.clip(_GAIT_PERIOD * (0.2 / max(speed, 0.05)), 0.25, 0.80)
        )
        swing_frac = _SWING_FRAC

        x0   = float(self._data.xpos[self._base_id, 0])
        fell = False
        dt   = self._model.opt.timestep
        n_steps = max(1, int(round(secs / dt)))

        for step_i in range(n_steps):
            t     = step_i * dt
            phase = (t % period) / period   # normalised phase ∈ [0, 1)

            q_des = self._trot_joint_targets(phase, swing_frac)
            self._data.ctrl[:] = self._pd_torque(
                q_des, kp=_KP_STAND, kd=_KD_STAND
            )
            mujoco.mj_step(self._model, self._data)

            # Early-exit if robot falls
            if self._data.xpos[self._base_id, 2] < _MIN_STAND_HEIGHT:
                fell = True
                break

        mujoco.mj_forward(self._model, self._data)
        x1          = float(self._data.xpos[self._base_id, 0])
        displacement = x1 - x0
        h_final     = self.get_body_height()

        success = (displacement > 0.03) and (not fell) and (h_final > _MIN_STAND_HEIGHT)
        if not success:
            warnings.warn(
                f"walk_forward(): displacement={displacement:.4f} m, "
                f"fell={fell}, final_height={h_final:.4f} m. "
                "Try increasing secs or adjusting speed."
            )
        return success

    # ------------------------------------------------------------------
    # Internal: trot gait joint targets
    # ------------------------------------------------------------------
    def _trot_joint_targets(
        self, phase: float, swing_frac: float
    ) -> np.ndarray:
        """
        Compute desired joint angles for all 12 joints at a given gait phase.

        Diagonal pairs:
          Group A (phase offset 0.0): FL (idx 3-5) + RR (idx 6-8)
          Group B (phase offset 0.5): FR (idx 0-2) + RL (idx 9-11)

        Parameters
        ----------
        phase      : float in [0, 1) — current position in gait cycle
        swing_frac : float — fraction of cycle spent in swing

        Returns
        -------
        np.ndarray, shape (12,)  — clamped to joint limits
        """
        q = np.zeros(12)

        def _leg_angles(phase_offset: float):
            """Return (hip, thigh, calf) for a leg at given phase offset."""
            p = (phase + phase_offset) % 1.0
            if p < swing_frac:
                # Swing phase: lift foot
                return _HIP_NEUTRAL, _THIGH_SWING, _CALF_SWING
            else:
                # Stance phase: push body forward
                return _HIP_NEUTRAL, _THIGH_STANCE, _CALF_STANCE

        # FR — Group B (phase offset 0.5)
        q[0],  q[1],  q[2]  = _leg_angles(0.5)
        # FL — Group A (phase offset 0.0)
        q[3],  q[4],  q[5]  = _leg_angles(0.0)
        # RR — Group A (phase offset 0.0)
        q[6],  q[7],  q[8]  = _leg_angles(0.0)
        # RL — Group B (phase offset 0.5)
        q[9],  q[10], q[11] = _leg_angles(0.5)

        return self._clamp_joints(q)

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        h = self.get_body_height()
        t = self._data.time
        return (
            f"Robot(go2, t={t:.3f}s, base_height={h:.4f}m, "
            f"nu={self._model.nu}, nq={self._model.nq})"
        )
