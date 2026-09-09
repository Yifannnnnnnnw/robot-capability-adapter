"""
driver_from_scratch.py — Skydio X2 Quadrotor UAV Driver
=========================================================
Robot class: AERIAL / MULTIROTOR
  - Single rigid body "x2" with freejoint (nq=7, nv=6)
  - 4 motor actuators: thrust1-4, ctrlrange [0,13] N
  - gear=[0,0,1,0,0,±0.0201] → body-Z force + yaw torque per rotor

Controller: Cascaded PD
  Outer loop : position PD  → desired total thrust + desired roll/pitch
  Inner loop : attitude PD  → roll/pitch/yaw torques
  Mixer      : B_pinv maps [Fz, Mx, My, Mz] → per-rotor thrusts (clamped [0,13])

No IK, no arm, no gripper — pure flight controller.
"""

import math
import time
import numpy as np
import mujoco

# ---------------------------------------------------------------------------
# Physical constants & model parameters
# ---------------------------------------------------------------------------
_GRAVITY       = 9.81          # m/s²
_TOTAL_MASS    = 1.325         # kg  (4×0.25 rotors + 0.325 body)
_N_ROTORS      = 4
_HOVER_THRUST  = _TOTAL_MASS * _GRAVITY / _N_ROTORS   # ≈ 3.2496 N each
_CTRL_MIN      = 0.0
_CTRL_MAX      = 13.0

# Rotor positions in body frame [m] and yaw-torque signs
_ROTOR_POS = np.array([
    [-0.14, -0.18, 0.05],   # thrust1
    [-0.14,  0.18, 0.05],   # thrust2
    [ 0.14,  0.18, 0.08],   # thrust3
    [ 0.14, -0.18, 0.08],   # thrust4
])
_YAW_SIGNS = np.array([-1.0, 1.0, -1.0, 1.0]) * 0.0201

# Mixing matrix  B: wrench = B @ ctrl,  shape (4,4)
# rows: [Fz, Mx(roll), My(pitch), Mz(yaw)]
_B = np.array([
    [1.0,          1.0,          1.0,          1.0         ],
    [_ROTOR_POS[0,1], _ROTOR_POS[1,1], _ROTOR_POS[2,1], _ROTOR_POS[3,1]],
    [-_ROTOR_POS[0,0],-_ROTOR_POS[1,0],-_ROTOR_POS[2,0],-_ROTOR_POS[3,0]],
    [_YAW_SIGNS[0], _YAW_SIGNS[1], _YAW_SIGNS[2], _YAW_SIGNS[3]],
])
_B_PINV = np.linalg.pinv(_B)   # shape (4,4): ctrl = B_pinv @ wrench

# Controller gains (tuned via simulation)
_KP_POS = np.array([3.0, 3.0, 5.0])    # position P gains  (x, y, z)
_KD_POS = np.array([2.5, 2.5, 3.5])    # position D gains
_KP_ATT = np.array([10.0, 10.0, 4.0])  # attitude P gains  (roll, pitch, yaw)
_KD_ATT = np.array([3.0,  3.0,  1.5])  # attitude D gains

_MAX_TILT_RAD  = math.radians(30)       # safety tilt limit
_MAX_HORIZ_ACC = _GRAVITY * math.tan(_MAX_TILT_RAD)


# ---------------------------------------------------------------------------
# Quaternion / rotation helpers
# ---------------------------------------------------------------------------

def _quat_wxyz_to_rmat(q: np.ndarray) -> np.ndarray:
    """MuJoCo wxyz quaternion → 3×3 rotation matrix."""
    q = q / np.linalg.norm(q)
    w, x, y, z = q
    return np.array([
        [1 - 2*(y*y + z*z),     2*(x*y - w*z),     2*(x*z + w*y)],
        [    2*(x*y + w*z), 1 - 2*(x*x + z*z),     2*(y*z - w*x)],
        [    2*(x*z - w*y),     2*(y*z + w*x), 1 - 2*(x*x + y*y)],
    ])


def _rmat_to_euler_zyx(R: np.ndarray):
    """ZYX Euler angles (roll, pitch, yaw) from rotation matrix."""
    pitch = math.asin(float(np.clip(-R[2, 0], -1.0, 1.0)))
    roll  = math.atan2(float(R[2, 1]), float(R[2, 2]))
    yaw   = math.atan2(float(R[1, 0]), float(R[0, 0]))
    return roll, pitch, yaw


def _angle_wrap(a: float) -> float:
    """Wrap angle to [-π, π]."""
    return (a + math.pi) % (2 * math.pi) - math.pi


# ---------------------------------------------------------------------------
# Core flight controller (called every mj_step)
# ---------------------------------------------------------------------------

class _FlightController:
    """
    Cascaded PD flight controller for the Skydio X2.

    Call update(data, target_pos, target_yaw) each sim step to compute
    and write data.ctrl[0:4].
    """

    def __init__(self):
        self.target_pos = np.array([0.0, 0.0, 0.3])
        self.target_yaw = 0.0
        self.enabled    = True

    def set_target(self, pos: np.ndarray, yaw: float = 0.0):
        self.target_pos = np.array(pos, dtype=float)
        self.target_yaw = float(yaw)

    def update(self, data: mujoco.MjData) -> np.ndarray:
        """Compute per-rotor thrust commands and write to data.ctrl."""
        if not self.enabled:
            data.ctrl[:] = 0.0
            return data.ctrl[:4].copy()

        # ── Read state ──────────────────────────────────────────────────────
        pos  = data.qpos[0:3].copy()          # world position
        quat = data.qpos[3:7].copy()          # wxyz quaternion
        vel  = data.qvel[0:3].copy()          # world linear velocity
        omega = data.qvel[3:6].copy()         # body angular velocity (rad/s)

        R = _quat_wxyz_to_rmat(quat)
        roll, pitch, yaw = _rmat_to_euler_zyx(R)

        # ── Outer loop: position PD ──────────────────────────────────────────
        pos_err = self.target_pos - pos
        acc_des = _KP_POS * pos_err - _KD_POS * vel

        # Vertical: total thrust
        Fz = _TOTAL_MASS * (_GRAVITY + acc_des[2])
        Fz = float(np.clip(Fz, 0.5, _N_ROTORS * _CTRL_MAX))

        # Horizontal: desired tilt angles (small-angle, world-frame)
        ax_des = float(np.clip(acc_des[0], -_MAX_HORIZ_ACC, _MAX_HORIZ_ACC))
        ay_des = float(np.clip(acc_des[1], -_MAX_HORIZ_ACC, _MAX_HORIZ_ACC))

        # Rotate desired horizontal acc into body-yaw frame → desired roll/pitch
        cy, sy = math.cos(yaw), math.sin(yaw)
        phi_des   = float(np.clip(
            (ax_des * sy - ay_des * cy) / _GRAVITY, -_MAX_TILT_RAD, _MAX_TILT_RAD))
        theta_des = float(np.clip(
            (ax_des * cy + ay_des * sy) / _GRAVITY, -_MAX_TILT_RAD, _MAX_TILT_RAD))
        psi_des   = self.target_yaw

        # ── Inner loop: attitude PD ──────────────────────────────────────────
        roll_err  = _angle_wrap(phi_des   - roll)
        pitch_err = _angle_wrap(theta_des - pitch)
        yaw_err   = _angle_wrap(psi_des   - yaw)

        # omega is in body frame; use directly as rate feedback
        Mx = _KP_ATT[0] * roll_err  - _KD_ATT[0] * omega[0]
        My = _KP_ATT[1] * pitch_err - _KD_ATT[1] * omega[1]
        Mz = _KP_ATT[2] * yaw_err   - _KD_ATT[2] * omega[2]

        # ── Mixer ────────────────────────────────────────────────────────────
        wrench = np.array([Fz, Mx, My, Mz])
        ctrl   = _B_PINV @ wrench
        ctrl   = np.clip(ctrl, _CTRL_MIN, _CTRL_MAX)

        data.ctrl[0:4] = ctrl
        return ctrl.copy()


# ---------------------------------------------------------------------------
# Robot class
# ---------------------------------------------------------------------------

class Robot:
    """
    Skydio X2 quadrotor UAV driver.

    Exposes:
      build_from_mjcf(mjcf_path)  → Robot
      home()                      → bool
      get_joint_positions()       → np.ndarray  (freejoint qpos[0:7])
      step(n)                     → None
      render()                    → None
      describe()                  → str
      takeoff(height, tol, timeout) → bool
      hover(secs)                 → bool
      move_to(x, y, z, tol, timeout) → bool
      land(timeout)               → bool
      get_base_pose()             → (xyz, R3x3)
      get_base_yaw()              → float
    """

    # ── Construction ────────────────────────────────────────────────────────

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        self._model   = model
        self._data    = data
        self._ctrl    = _FlightController()
        self._viewer  = None

        # Body index for the drone
        self._body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "x2")
        if self._body_id < 0:
            raise RuntimeError("Body 'x2' not found in model")

        # Actuator indices (thrust1-4)
        self._act_ids = []
        for name in ("thrust1", "thrust2", "thrust3", "thrust4"):
            aid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            if aid < 0:
                raise RuntimeError(f"Actuator '{name}' not found")
            self._act_ids.append(aid)

        # Reset to initial state
        mujoco.mj_resetData(model, data)
        mujoco.mj_forward(model, data)

    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """Load MJCF and construct the Robot."""
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data  = mujoco.MjData(model)
        return cls(model, data)

    # ── Low-level helpers ────────────────────────────────────────────────────

    def _run_controller(self, n_steps: int = 1):
        """Advance simulation n_steps, running the flight controller each step."""
        for _ in range(n_steps):
            self._ctrl.update(self._data)
            mujoco.mj_step(self._model, self._data)

    def _idle_rotors(self, n_steps: int = 1):
        """Advance simulation with zero thrust (idle / landed)."""
        for _ in range(n_steps):
            self._data.ctrl[:] = 0.0
            mujoco.mj_step(self._model, self._data)

    @property
    def _dt(self) -> float:
        return float(self._model.opt.timestep)

    def _steps_for(self, secs: float) -> int:
        return max(1, int(round(secs / self._dt)))

    # ── Mandatory API ────────────────────────────────────────────────────────

    def home(self) -> bool:
        """
        Reset to the keyframe 'hover' pose (z=0.3 m, level, rotors at trim).
        Returns True on success.
        """
        # Reset data and apply keyframe
        mujoco.mj_resetData(self._model, self._data)
        # Find keyframe named 'hover'
        key_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_KEY, "hover")
        if key_id >= 0:
            mujoco.mj_resetDataKeyframe(self._model, self._data, key_id)
        else:
            # Fallback: place at z=0.3, level
            self._data.qpos[0:3] = [0.0, 0.0, 0.3]
            self._data.qpos[3:7] = [1.0, 0.0, 0.0, 0.0]
            self._data.ctrl[:]   = _HOVER_THRUST

        mujoco.mj_forward(self._model, self._data)

        # Set controller target to current position
        pos = self._data.qpos[0:3].copy()
        self._ctrl.set_target(pos, yaw=0.0)
        self._ctrl.enabled = True
        return True

    def get_joint_positions(self) -> np.ndarray:
        """
        Return the freejoint state: [x, y, z, qw, qx, qy, qz].
        (The X2 has no revolute joints — the freejoint IS the full state.)
        """
        return self._data.qpos[0:7].copy()

    def step(self, n: int = 1):
        """Advance simulation n steps with the active flight controller."""
        self._run_controller(n)

    def render(self):
        """Open / refresh a passive MuJoCo viewer window (no-op if unavailable)."""
        try:
            import mujoco.viewer as _mv
            if self._viewer is None:
                self._viewer = _mv.launch_passive(self._model, self._data)
            else:
                self._viewer.sync()
        except Exception as exc:
            print(f"[Robot.render] viewer unavailable: {exc}")

    def describe(self) -> str:
        pos = self._data.qpos[0:3]
        quat = self._data.qpos[3:7]
        R = _quat_wxyz_to_rmat(quat)
        roll, pitch, yaw = _rmat_to_euler_zyx(R)
        ctrl = self._data.ctrl[0:4]
        return (
            f"Skydio X2 Quadrotor UAV\n"
            f"  sim time   : {self._data.time:.3f} s\n"
            f"  position   : x={pos[0]:.3f}  y={pos[1]:.3f}  z={pos[2]:.3f} m\n"
            f"  attitude   : roll={math.degrees(roll):.1f}°  "
            f"pitch={math.degrees(pitch):.1f}°  yaw={math.degrees(yaw):.1f}°\n"
            f"  ctrl (N)   : {np.round(ctrl, 3)}\n"
            f"  target pos : {np.round(self._ctrl.target_pos, 3)}\n"
            f"  controller : {'ENABLED' if self._ctrl.enabled else 'DISABLED'}\n"
            f"  nq={self._model.nq}  nv={self._model.nv}  nu={self._model.nu}"
        )

    # ── Pose accessors ───────────────────────────────────────────────────────

    def get_base_pose(self):
        """Return (xyz, R3x3) world position and orientation of the drone body."""
        pos  = self._data.qpos[0:3].copy()
        quat = self._data.qpos[3:7].copy()
        R    = _quat_wxyz_to_rmat(quat)
        return pos, R

    def get_base_yaw(self) -> float:
        """Return current yaw angle in radians."""
        quat = self._data.qpos[3:7].copy()
        R    = _quat_wxyz_to_rmat(quat)
        _, _, yaw = _rmat_to_euler_zyx(R)
        return yaw

    def _is_upright(self, max_tilt_deg: float = 45.0) -> bool:
        """Return True if the drone is not excessively tilted."""
        quat = self._data.qpos[3:7].copy()
        R    = _quat_wxyz_to_rmat(quat)
        roll, pitch, _ = _rmat_to_euler_zyx(R)
        return (abs(math.degrees(roll))  < max_tilt_deg and
                abs(math.degrees(pitch)) < max_tilt_deg)

    # ── Flight behaviours ────────────────────────────────────────────────────

    def takeoff(self, height: float = 0.5, tol: float = 0.05,
                timeout: float = 8.0) -> bool:
        """
        Spin rotors up and climb to `height` metres, then hold a stable hover.

        The controller runs every sim step (closed-loop).  Returns True when
        the drone is within `tol` metres of the target altitude and upright.
        Returns False on timeout or if the drone flips.

        Parameters
        ----------
        height  : target altitude in metres (default 0.5 m)
        tol     : position tolerance in metres (default 0.05 m)
        timeout : wall-clock timeout in seconds (default 8.0 s)
        """
        if height < 0.05:
            raise ValueError(f"takeoff height {height} m is too low (min 0.05 m)")

        # Set target to current XY but desired altitude
        cur_pos, _ = self.get_base_pose()
        target = np.array([cur_pos[0], cur_pos[1], float(height)])
        self._ctrl.set_target(target, yaw=self.get_base_yaw())
        self._ctrl.enabled = True

        deadline = self._data.time + timeout
        while self._data.time < deadline:
            self._run_controller(1)

            if not self._is_upright(max_tilt_deg=50.0):
                print(f"[takeoff] ABORT — drone flipped at t={self._data.time:.2f}s")
                self._ctrl.enabled = False
                return False

            pos, _ = self.get_base_pose()
            alt_err = abs(pos[2] - height)
            horiz_err = math.hypot(pos[0] - target[0], pos[1] - target[1])
            vel_mag = float(np.linalg.norm(self._data.qvel[0:3]))

            if alt_err < tol and horiz_err < tol and vel_mag < 0.15:
                return True   # stable hover achieved

        print(f"[takeoff] timeout — final z={self._data.qpos[2]:.3f} m "
              f"(target {height:.3f} m)")
        return False

    def hover(self, secs: float = 2.0) -> bool:
        """
        Station-keep at the current pose for `secs` seconds.

        Returns True if the drone remains upright and within 0.15 m of the
        hold position throughout.  Returns False if it drifts or flips.
        """
        hold_pos, _ = self.get_base_pose()
        hold_yaw    = self.get_base_yaw()
        self._ctrl.set_target(hold_pos, yaw=hold_yaw)
        self._ctrl.enabled = True

        n_steps = self._steps_for(secs)
        for _ in range(n_steps):
            self._run_controller(1)

            if not self._is_upright(max_tilt_deg=50.0):
                print(f"[hover] ABORT — drone flipped at t={self._data.time:.2f}s")
                return False

            pos, _ = self.get_base_pose()
            drift = float(np.linalg.norm(pos - hold_pos))
            if drift > 0.30:
                print(f"[hover] ABORT — excessive drift {drift:.3f} m "
                      f"at t={self._data.time:.2f}s")
                return False

        return True

    def move_to(self, x: float, y: float, z: float,
                tol: float = 0.10, timeout: float = 12.0) -> bool:
        """
        Fly to world target (x, y, z) with closed-loop position control.

        Returns True when the drone arrives within `tol` metres and is upright.
        Returns False on timeout or flip.

        Parameters
        ----------
        x, y, z  : target world position in metres
        tol      : arrival tolerance in metres (default 0.10 m)
        timeout  : sim-time timeout in seconds (default 12.0 s)
        """
        target = np.array([float(x), float(y), float(z)])
        if z < 0.05:
            raise ValueError(f"Target altitude z={z} m is below ground safety limit")

        self._ctrl.set_target(target, yaw=self.get_base_yaw())
        self._ctrl.enabled = True

        deadline = self._data.time + timeout
        while self._data.time < deadline:
            self._run_controller(1)

            if not self._is_upright(max_tilt_deg=50.0):
                print(f"[move_to] ABORT — drone flipped at t={self._data.time:.2f}s")
                self._ctrl.enabled = False
                return False

            pos, _ = self.get_base_pose()
            dist   = float(np.linalg.norm(pos - target))
            vel_mag = float(np.linalg.norm(self._data.qvel[0:3]))

            if dist < tol and vel_mag < 0.20:
                return True

        pos, _ = self.get_base_pose()
        dist = float(np.linalg.norm(pos - target))
        print(f"[move_to] timeout — remaining distance {dist:.3f} m")
        return False

    def land(self, timeout: float = 10.0) -> bool:
        """
        Descend to the ground and idle the rotors.

        Gradually lowers the altitude target to 0.05 m, then cuts thrust once
        the drone is near the ground.  Returns True on successful landing.
        """
        cur_pos, _ = self.get_base_pose()
        start_z    = float(cur_pos[2])
        land_z     = 0.05   # target altitude before cutting thrust

        # Phase 1: descend to land_z
        self._ctrl.set_target(
            np.array([cur_pos[0], cur_pos[1], land_z]),
            yaw=self.get_base_yaw()
        )
        self._ctrl.enabled = True

        deadline = self._data.time + timeout
        while self._data.time < deadline:
            self._run_controller(1)

            if not self._is_upright(max_tilt_deg=60.0):
                print(f"[land] WARNING — drone tilted during descent")
                break

            pos, _ = self.get_base_pose()
            if pos[2] <= land_z + 0.03:
                break   # close enough to ground

        # Phase 2: cut thrust and let it settle
        self._ctrl.enabled = False
        settle_steps = self._steps_for(1.0)
        self._idle_rotors(settle_steps)

        final_z = float(self._data.qpos[2])
        success  = final_z < 0.15
        if not success:
            print(f"[land] WARNING — final altitude {final_z:.3f} m (expected < 0.15 m)")
        return success

    # ── Convenience / diagnostics ────────────────────────────────────────────

    def get_rotor_thrusts(self) -> np.ndarray:
        """Return the current per-rotor thrust commands (N), shape (4,)."""
        return self._data.ctrl[0:4].copy()

    def set_target_position(self, pos, yaw: float = None):
        """
        Directly update the flight controller's position target.

        Parameters
        ----------
        pos : array-like (3,) — world target [x, y, z]
        yaw : desired yaw in radians (None = keep current)
        """
        pos = np.asarray(pos, dtype=float)
        if pos.shape != (3,):
            raise ValueError("pos must be a 3-element array")
        if yaw is None:
            yaw = self.get_base_yaw()
        self._ctrl.set_target(pos, yaw=float(yaw))

    def enable_controller(self, enabled: bool = True):
        """Enable or disable the flight controller (disabling idles rotors)."""
        self._ctrl.enabled = bool(enabled)
        if not enabled:
            self._data.ctrl[:] = 0.0


# ---------------------------------------------------------------------------
# Quick self-test (run as script)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys, os

    mjcf = sys.argv[1] if len(sys.argv) > 1 else "mjcf.xml"
    if not os.path.exists(mjcf):
        print(f"MJCF not found: {mjcf}")
        sys.exit(1)

    print("=" * 60)
    print("Skydio X2 driver self-test")
    print("=" * 60)

    r = Robot.build_from_mjcf(mjcf)
    r.home()
    print(r.describe())
    print()

    # ── Test 1: takeoff to 0.5 m ────────────────────────────────────────────
    print("Test 1: takeoff to 0.5 m ...")
    ok = r.takeoff(height=0.5, tol=0.05, timeout=8.0)
    pos, R = r.get_base_pose()
    roll, pitch, yaw = _rmat_to_euler_zyx(R)
    print(f"  result={ok}  z={pos[2]:.3f} m  "
          f"roll={math.degrees(roll):.1f}°  pitch={math.degrees(pitch):.1f}°")
    assert ok, "takeoff FAILED"
    assert abs(pos[2] - 0.5) < 0.08, f"altitude error too large: {pos[2]:.3f}"
    print("  PASS")

    # ── Test 2: hover for 1 s ───────────────────────────────────────────────
    print("Test 2: hover for 1 s ...")
    ok = r.hover(secs=1.0)
    pos2, _ = r.get_base_pose()
    print(f"  result={ok}  z={pos2[2]:.3f} m")
    assert ok, "hover FAILED"
    print("  PASS")

    # ── Test 3: move_to (+0.5 m in X) ───────────────────────────────────────
    print("Test 3: move_to (x+0.5 m) ...")
    start_pos, _ = r.get_base_pose()
    target_x = start_pos[0] + 0.5
    ok = r.move_to(target_x, start_pos[1], start_pos[2], tol=0.10, timeout=10.0)
    pos3, _ = r.get_base_pose()
    err = math.hypot(pos3[0] - target_x, pos3[1] - start_pos[1])
    print(f"  result={ok}  pos=({pos3[0]:.3f}, {pos3[1]:.3f}, {pos3[2]:.3f})  "
          f"horiz_err={err:.3f} m")
    assert ok, "move_to FAILED"
    assert err < 0.15, f"horizontal error too large: {err:.3f} m"
    print("  PASS")

    # ── Test 4: land ─────────────────────────────────────────────────────────
    print("Test 4: land ...")
    ok = r.land(timeout=8.0)
    final_z = float(r._data.qpos[2])
    print(f"  result={ok}  final_z={final_z:.3f} m")
    assert ok, "land FAILED"
    print("  PASS")

    print()
    print("All tests PASSED ✓")
    print(r.describe())
