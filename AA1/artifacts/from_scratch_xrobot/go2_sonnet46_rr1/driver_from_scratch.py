"""
driver_from_scratch.py — Unitree Go2 Quadruped Robot Driver
============================================================
Full Robot class for the Unitree Go2 quadruped (12-DOF, torque-controlled).
No auto_adapter.skeletons imports.

Robot class: QUADRUPED
  - 4 legs × 3 joints (abduction/hip/knee)
  - Floating base via freejoint (qpos[0:7])
  - 12 torque-controlled motor actuators
  - Joint-space PD control: τ = kp*(q_des - q) + kd*(qd_des - qd)
  - Diagonal trot gait for walk_forward

Actuator order (m.nu=12):
  [0] FR_hip   [1] FR_thigh   [2] FR_calf
  [3] FL_hip   [4] FL_thigh   [5] FL_calf
  [6] RR_hip   [7] RR_thigh   [8] RR_calf
  [9] RL_hip  [10] RL_thigh  [11] RL_calf

qpos layout (m.nq=19):
  [0:7]  freejoint (xyz + wxyz quaternion)
  [7]    FL_hip_joint
  [8]    FL_thigh_joint
  [9]    FL_calf_joint
  [10]   FR_hip_joint
  [11]   FR_thigh_joint
  [12]   FR_calf_joint
  [13]   RL_hip_joint
  [14]   RL_thigh_joint
  [15]   RL_calf_joint
  [16]   RR_hip_joint
  [17]   RR_thigh_joint
  [18]   RR_calf_joint
"""

import os
import math
import numpy as np
import mujoco

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Mapping: actuator index -> qpos address
_ACT_QPOS = [
    10, 11, 12,   # FR: hip, thigh, calf
     7,  8,  9,   # FL: hip, thigh, calf
    16, 17, 18,   # RR: hip, thigh, calf
    13, 14, 15,   # RL: hip, thigh, calf
]

# Mapping: actuator index -> dof address (for velocity)
_ACT_DOF = [
     9, 10, 11,   # FR
     6,  7,  8,   # FL
    15, 16, 17,   # RR
    12, 13, 14,   # RL
]

# Standing pose: thigh=0.4 rad, calf=-0.85 rad (all hips=0)
# Produces ~0.40 m body height after settling on ground
_STAND_POSE = np.array([
    0.0,  0.4, -0.85,   # FR
    0.0,  0.4, -0.85,   # FL
    0.0,  0.4, -0.85,   # RR
    0.0,  0.4, -0.85,   # RL
], dtype=np.float64)

# Sit pose: deeply folded (thigh=1.4, calf=-2.6)
# Produces ~0.12 m body height (well below 0.8 × stand_height)
_SIT_POSE = np.array([
    0.0,  1.4, -2.6,   # FR
    0.0,  1.4, -2.6,   # FL
    0.0,  1.4, -2.6,   # RR
    0.0,  1.4, -2.6,   # RL
], dtype=np.float64)

# Home pose (same as stand)
_HOME_POSE = _STAND_POSE.copy()

# PD gains
_KP = np.array([80.0, 80.0, 80.0] * 4, dtype=np.float64)   # Nm/rad
_KD = np.array([ 2.0,  2.0,  2.0] * 4, dtype=np.float64)   # Nm·s/rad

# Trot gait parameters (tuned for reliable forward motion)
_GAIT_PERIOD   = 0.4    # seconds per full cycle
_GAIT_A_THIGH  = 0.15   # thigh oscillation amplitude (rad)
_GAIT_A_CALF   = 0.25   # calf lift amplitude (rad)

# Phase offsets for diagonal trot (FR+RL in phase, FL+RR anti-phase)
# Leg order: FR, FL, RR, RL  (matches actuator block order)
_GAIT_PHASES = [0.0, math.pi, math.pi, 0.0]


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _quat_to_rot(q_wxyz: np.ndarray) -> np.ndarray:
    """Convert wxyz quaternion to 3×3 rotation matrix."""
    w, x, y, z = q_wxyz
    return np.array([
        [1 - 2*(y*y + z*z),     2*(x*y - w*z),     2*(x*z + w*y)],
        [    2*(x*y + w*z), 1 - 2*(x*x + z*z),     2*(y*z - w*x)],
        [    2*(x*z - w*y),     2*(y*z + w*x), 1 - 2*(x*x + y*y)],
    ], dtype=np.float64)


def _rot_to_euler_zyx(R: np.ndarray):
    """Extract roll, pitch, yaw from rotation matrix (ZYX convention)."""
    pitch = math.asin(-R[2, 0])
    roll  = math.atan2(R[2, 1], R[2, 2])
    yaw   = math.atan2(R[1, 0], R[0, 0])
    return roll, pitch, yaw


# ---------------------------------------------------------------------------
# Robot class
# ---------------------------------------------------------------------------

class Robot:
    """
    Unitree Go2 quadruped driver.

    Exposes:
      build_from_mjcf(mjcf_path) -> Robot
      home()                     -> bool
      stand_up(duration)         -> bool
      sit(duration)              -> bool
      walk_forward(secs, speed)  -> bool
      get_joint_positions()      -> np.ndarray (12,)
      get_body_height()          -> float
      get_base_pose()            -> (xyz, R3x3)
      step(n)
      render()
      describe()                 -> str
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData,
                 mjcf_path: str):
        self._model = model
        self._data  = data
        self._mjcf_path = mjcf_path

        # Resolve body IDs
        self._base_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, "base_link")
        if self._base_id < 0:
            raise RuntimeError("Body 'base_link' not found in model")

        # Foot body IDs for contact sensing
        self._foot_ids = {}
        for name in ("FL_foot", "FR_foot", "RL_foot", "RR_foot"):
            bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
            if bid >= 0:
                self._foot_ids[name] = bid

        # Actuator ctrlrange
        self._ctrl_lo = model.actuator_ctrlrange[:, 0].copy()
        self._ctrl_hi = model.actuator_ctrlrange[:, 1].copy()

        # Renderer (lazy)
        self._renderer = None

        # Track standing height (set after first stand_up)
        self._stand_height: float = 0.40

    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """Load MJCF and construct a Robot instance."""
        # Resolve symlinks so MuJoCo can find included files
        real_path = os.path.realpath(mjcf_path)
        model = mujoco.MjModel.from_xml_path(real_path)
        data  = mujoco.MjData(model)

        robot = cls(model, data, real_path)

        # Place robot at a sensible initial configuration
        robot._reset_to_stand_pose()
        mujoco.mj_forward(model, data)
        return robot

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_to_stand_pose(self):
        """Set qpos to stand pose at nominal height (no physics)."""
        d = self._data
        d.qpos[0:3] = [0.0, 0.0, 0.445]   # xyz
        d.qpos[3:7] = [1.0, 0.0, 0.0, 0.0]  # wxyz quaternion
        for i in range(12):
            d.qpos[_ACT_QPOS[i]] = _STAND_POSE[i]
        d.qvel[:] = 0.0
        d.ctrl[:] = 0.0

    def _pd_torques(self, q_des: np.ndarray, qd_des: np.ndarray = None
                    ) -> np.ndarray:
        """
        Compute PD torques for all 12 actuators.
        τ = kp*(q_des - q) + kd*(qd_des - qd)
        Clamped to actuator ctrlrange.
        """
        if qd_des is None:
            qd_des = np.zeros(12)
        q  = self._data.qpos[_ACT_QPOS]
        qd = self._data.qvel[_ACT_DOF]
        tau = _KP * (q_des - q) + _KD * (qd_des - qd)
        return np.clip(tau, self._ctrl_lo, self._ctrl_hi)

    def _run_pd(self, q_des: np.ndarray, duration: float,
                qd_des: np.ndarray = None) -> None:
        """
        Run PD control toward q_des for `duration` seconds.
        Steps the simulation at model timestep.
        """
        dt = self._model.opt.timestep
        n  = max(1, int(duration / dt))
        for _ in range(n):
            self._data.ctrl[:] = self._pd_torques(q_des, qd_des)
            mujoco.mj_step(self._model, self._data)

    def _interpolate_pd(self, q_start: np.ndarray, q_end: np.ndarray,
                        duration: float) -> None:
        """
        Smoothly interpolate from q_start to q_end over `duration` seconds
        using PD control with a linearly moving target.
        """
        dt = self._model.opt.timestep
        n  = max(1, int(duration / dt))
        for i in range(n):
            alpha = (i + 1) / n
            q_des = q_start + alpha * (q_end - q_start)
            self._data.ctrl[:] = self._pd_torques(q_des)
            mujoco.mj_step(self._model, self._data)

    # ------------------------------------------------------------------
    # Public API — required
    # ------------------------------------------------------------------

    def home(self) -> bool:
        """
        Return robot to standing home pose.
        Resets state and runs PD to stand pose for 2 s.
        Returns True on success.
        """
        self._reset_to_stand_pose()
        mujoco.mj_forward(self._model, self._data)
        self._run_pd(_HOME_POSE, duration=2.0)
        h = self.get_body_height()
        self._stand_height = h
        return h > 0.15

    def get_joint_positions(self) -> np.ndarray:
        """
        Return current joint positions for all 12 leg joints (rad).
        Order: FR_hip, FR_thigh, FR_calf, FL_hip, FL_thigh, FL_calf,
               RR_hip, RR_thigh, RR_calf, RL_hip, RL_thigh, RL_calf
        """
        return self._data.qpos[_ACT_QPOS].copy()

    def step(self, n: int = 1) -> None:
        """Advance simulation by n steps (holding current ctrl)."""
        for _ in range(n):
            mujoco.mj_step(self._model, self._data)

    def render(self) -> np.ndarray:
        """
        Render the scene to an RGB image (480×640×3 uint8).
        Returns the image array.
        """
        if self._renderer is None:
            self._renderer = mujoco.Renderer(self._model, height=480, width=640)
        self._renderer.update_scene(self._data)
        return self._renderer.render()

    def describe(self) -> str:
        """Return a human-readable description of the robot state."""
        q   = self.get_joint_positions()
        xyz, R = self.get_base_pose()
        roll, pitch, yaw = _rot_to_euler_zyx(R)
        h = xyz[2]
        lines = [
            "=== Unitree Go2 Quadruped ===",
            f"  Base position : x={xyz[0]:.3f}  y={xyz[1]:.3f}  z={h:.3f} m",
            f"  Base RPY (deg): roll={math.degrees(roll):.1f}  "
            f"pitch={math.degrees(pitch):.1f}  yaw={math.degrees(yaw):.1f}",
            f"  Body height   : {h:.3f} m",
            "  Joint positions (rad):",
            f"    FR: hip={q[0]:.3f}  thigh={q[1]:.3f}  calf={q[2]:.3f}",
            f"    FL: hip={q[3]:.3f}  thigh={q[4]:.3f}  calf={q[5]:.3f}",
            f"    RR: hip={q[6]:.3f}  thigh={q[7]:.3f}  calf={q[8]:.3f}",
            f"    RL: hip={q[9]:.3f}  thigh={q[10]:.3f}  calf={q[11]:.3f}",
            f"  DOF: 12  |  Actuators: 12 (torque motors)",
            f"  MJCF: {self._mjcf_path}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public API — quadruped-specific
    # ------------------------------------------------------------------

    def stand_up(self, duration: float = 2.0) -> bool:
        """
        PD-control the robot to a stable standing pose.

        Strategy:
          1. Reset qpos to stand pose at nominal height.
          2. Run PD for `duration` seconds to settle.
          3. Verify body height > 0.15 m.

        Returns True if final height > 0.15 m.
        """
        self._reset_to_stand_pose()
        mujoco.mj_forward(self._model, self._data)
        self._run_pd(_STAND_POSE, duration=duration)
        h = self.get_body_height()
        self._stand_height = h
        return h > 0.15

    def sit(self, duration: float = 1.5) -> bool:
        """
        Fold all legs to lower the body to a sitting/resting pose.

        Strategy:
          1. Ensure robot is standing (run stand_up if height < 0.15 m).
          2. Smoothly interpolate from stand pose to sit pose over `duration`.
          3. Verify final height < 0.8 × standing height.

        Returns True if body height dropped sufficiently.
        """
        # Ensure we're standing first
        if self.get_body_height() < 0.15:
            self.stand_up(duration=2.0)

        stand_h = self.get_body_height()
        q_start = self.get_joint_positions()

        # Smoothly fold to sit pose
        self._interpolate_pd(q_start, _SIT_POSE, duration=duration)

        # Hold sit pose briefly to settle
        self._run_pd(_SIT_POSE, duration=0.3)

        sit_h = self.get_body_height()
        return sit_h < 0.8 * stand_h

    def walk_forward(self, secs: float = 2.0, speed: float = 0.2) -> bool:
        """
        Walk forward using a diagonal trot gait.

        Gait design:
          - Diagonal pairs: (FR, RL) and (FL, RR) swing anti-phase.
          - Thigh oscillation: q_thigh = thigh_stand - A_thigh * sin(ωt + φ)
            * Negative sin → foot moves forward (swing)
            * Positive sin → foot pushes back (stance, propels body forward)
          - Calf lift: q_calf = calf_stand + A_calf * max(0, -sin(ωt + φ))
            * Lifts foot during forward swing phase only.
          - Speed parameter scales thigh amplitude linearly.

        Requires robot to be standing (calls stand_up if needed).
        Returns True if forward displacement > 3 cm and robot stays upright.

        Args:
            secs:  Duration of walking in seconds.
            speed: Desired forward speed (0.1–0.4 m/s recommended).
                   Scales gait amplitude.
        """
        # Ensure standing
        if self.get_body_height() < 0.15:
            ok = self.stand_up(duration=2.0)
            if not ok:
                print("[walk_forward] Failed to stand up first.")
                return False

        # Clamp speed to safe range
        speed = float(np.clip(speed, 0.05, 0.5))

        # Scale amplitude with speed (baseline at speed=0.2)
        A_thigh = _GAIT_A_THIGH * (speed / 0.2)
        A_thigh = float(np.clip(A_thigh, 0.05, 0.25))
        A_calf  = _GAIT_A_CALF

        dt      = self._model.opt.timestep
        omega   = 2.0 * math.pi / _GAIT_PERIOD
        n_steps = max(1, int(secs / dt))

        # Record start position
        xyz0, _ = self.get_base_pose()
        x0 = xyz0[0]

        # Thigh and calf stand values
        thigh_stand = _STAND_POSE[1]   # 0.4 rad
        calf_stand  = _STAND_POSE[2]   # -0.85 rad

        # Leg layout in q_des (12-element, actuator order):
        # FR: [0]=hip, [1]=thigh, [2]=calf
        # FL: [3]=hip, [4]=thigh, [5]=calf
        # RR: [6]=hip, [7]=thigh, [8]=calf
        # RL: [9]=hip, [10]=thigh, [11]=calf
        leg_thigh_idx = [1, 4, 7, 10]   # FR, FL, RR, RL
        leg_calf_idx  = [2, 5, 8, 11]

        for step_i in range(n_steps):
            t = step_i * dt
            q_des = _STAND_POSE.copy()

            for leg_i, (ti, ci, phi) in enumerate(
                    zip(leg_thigh_idx, leg_calf_idx, _GAIT_PHASES)):
                s = math.sin(omega * t + phi)
                q_des[ti] = thigh_stand - A_thigh * s
                # Lift calf only during forward swing (s < 0)
                lift = A_calf * max(0.0, -s)
                q_des[ci] = calf_stand + lift

            self._data.ctrl[:] = self._pd_torques(q_des)
            mujoco.mj_step(self._model, self._data)

            # Safety: abort if robot falls
            if self.get_body_height() < 0.10:
                print(f"[walk_forward] Robot fell at step {step_i}. Aborting.")
                return False

        xyz1, _ = self.get_base_pose()
        dx = xyz1[0] - x0
        return dx > 0.03

    def get_body_height(self) -> float:
        """Return the world Z coordinate of the base_link body (metres)."""
        return float(self._data.xpos[self._base_id, 2])

    def get_base_pose(self):
        """
        Return (xyz, R) where:
          xyz : np.ndarray (3,) — world position of base_link
          R   : np.ndarray (3,3) — world rotation matrix of base_link
        """
        xyz = self._data.xpos[self._base_id].copy()
        # Rotation from freejoint quaternion (wxyz)
        q_wxyz = self._data.qpos[3:7].copy()
        R = _quat_to_rot(q_wxyz)
        return xyz, R

    # ------------------------------------------------------------------
    # Convenience / extra
    # ------------------------------------------------------------------

    def get_foot_positions(self) -> dict:
        """Return world positions of all four feet as a dict."""
        return {
            name: self._data.xpos[bid].copy()
            for name, bid in self._foot_ids.items()
        }

    def get_base_velocity(self) -> np.ndarray:
        """Return base linear velocity (3,) in world frame."""
        return self._data.qvel[0:3].copy()

    def get_base_angular_velocity(self) -> np.ndarray:
        """Return base angular velocity (3,) in world frame."""
        return self._data.qvel[3:6].copy()

    def is_upright(self, max_tilt_deg: float = 45.0) -> bool:
        """Return True if the robot body is roughly upright."""
        _, R = self.get_base_pose()
        # z-axis of body in world frame
        z_body_world = R[:, 2]
        cos_tilt = float(z_body_world[2])  # dot with world z
        return cos_tilt > math.cos(math.radians(max_tilt_deg))

    def reset(self) -> None:
        """Hard reset: zero all state and return to stand pose."""
        mujoco.mj_resetData(self._model, self._data)
        self._reset_to_stand_pose()
        mujoco.mj_forward(self._model, self._data)


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    mjcf = sys.argv[1] if len(sys.argv) > 1 else "mjcf.xml"
    print(f"Loading model from: {mjcf}")

    r = Robot.build_from_mjcf(mjcf)
    print(r.describe())

    print("\n--- Testing stand_up ---")
    ok = r.stand_up(duration=2.0)
    print(f"stand_up OK={ok}, height={r.get_body_height():.4f} m")

    print("\n--- Testing sit ---")
    ok = r.sit(duration=1.5)
    print(f"sit OK={ok}, height={r.get_body_height():.4f} m")

    print("\n--- Testing stand_up again ---")
    ok = r.stand_up(duration=2.0)
    print(f"stand_up OK={ok}, height={r.get_body_height():.4f} m")

    print("\n--- Testing walk_forward ---")
    xyz0, _ = r.get_base_pose()
    ok = r.walk_forward(secs=2.0, speed=0.2)
    xyz1, _ = r.get_base_pose()
    dx = xyz1[0] - xyz0[0]
    print(f"walk_forward OK={ok}, dx={dx:.4f} m, height={r.get_body_height():.4f} m")

    print("\n--- Final state ---")
    print(r.describe())
