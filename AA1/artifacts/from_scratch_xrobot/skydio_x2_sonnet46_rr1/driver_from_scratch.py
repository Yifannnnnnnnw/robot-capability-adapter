"""
driver_from_scratch.py  —  Skydio X2 Quadrotor Driver
======================================================
Single-file Robot class for the Skydio X2 aerial quadrotor.

Architecture
------------
The X2 is a single rigid body with a freejoint (6-DOF) and four
site-based motor actuators (thrust1-4, ctrlrange [0, 13] N each).

Controller: Cascaded PD  (position outer loop -> attitude inner loop -> mixer)
  Outer loop : PD on world-frame position error -> desired total thrust
               + desired roll/pitch tilt angles (small-angle approx)
  Inner loop : PD on Euler-angle attitude error + body-rate damping
               -> desired roll/pitch/yaw moments
  Mixer      : pseudo-inverse of the 4x4 control-allocation matrix
               maps [Fz, Mx, My, Mz] -> per-rotor thrusts, clamped [0, 13]

The controller is called every mj_step (dt=0.01 s) so the drone never
goes open-loop.

Public API
----------
  Robot.build_from_mjcf(mjcf_path) -> Robot
  robot.home()                     -> bool
  robot.get_joint_positions()      -> np.ndarray  (freejoint qpos [7])
  robot.step(n=1)                  -> None
  robot.render()                   -> None
  robot.describe()                 -> str
  robot.get_base_pose()            -> (xyz, R3x3)
  robot.get_base_yaw()             -> float
  robot.takeoff(height=0.5)        -> bool
  robot.move_to(x, y, z, tol=0.1) -> bool
  robot.hover(secs=2.0)            -> bool
  robot.land()                     -> bool
"""

import math
import os
from typing import Tuple

import mujoco
import numpy as np


# ---------------------------------------------------------------------------
# Quaternion / rotation helpers
# ---------------------------------------------------------------------------

def _quat_to_rot(q: np.ndarray) -> np.ndarray:
    """Quaternion [w, x, y, z] -> 3x3 rotation matrix."""
    w, x, y, z = q
    return np.array([
        [1 - 2*(y*y + z*z),     2*(x*y - w*z),     2*(x*z + w*y)],
        [    2*(x*y + w*z), 1 - 2*(x*x + z*z),     2*(y*z - w*x)],
        [    2*(x*z - w*y),     2*(y*z + w*x), 1 - 2*(x*x + y*y)],
    ])


def _quat_to_euler(q: np.ndarray) -> Tuple[float, float, float]:
    """Quaternion [w, x, y, z] -> (roll, pitch, yaw) in radians."""
    w, x, y, z = q
    sinr_cosp = 2.0 * (w*x + y*z)
    cosr_cosp = 1.0 - 2.0 * (x*x + y*y)
    roll = math.atan2(sinr_cosp, cosr_cosp)
    sinp = max(-1.0, min(1.0, 2.0 * (w*y - z*x)))
    pitch = math.asin(sinp)
    siny_cosp = 2.0 * (w*z + x*y)
    cosy_cosp = 1.0 - 2.0 * (y*y + z*z)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return roll, pitch, yaw


def _wrap_angle(a: float) -> float:
    """Wrap angle to [-pi, pi]."""
    return (a + math.pi) % (2.0 * math.pi) - math.pi


# ---------------------------------------------------------------------------
# Control allocation matrix (built once from rotor geometry in x2.xml)
# ---------------------------------------------------------------------------

# Rotor positions in body frame
_ROTOR_POS = np.array([
    [-0.14, -0.18, 0.05],   # thrust1
    [-0.14,  0.18, 0.05],   # thrust2
    [ 0.14,  0.18, 0.08],   # thrust3
    [ 0.14, -0.18, 0.08],   # thrust4
], dtype=float)

# Yaw torque signs from gear vectors in x2.xml
_YAW_SIGNS = np.array([-0.0201, 0.0201, -0.0201, 0.0201], dtype=float)

# 4x4 allocation matrix: [Fz, Mx, My, Mz] = A @ [u1, u2, u3, u4]
_A = np.zeros((4, 4))
for _i in range(4):
    _A[0, _i] = 1.0                        # total thrust
    _A[1, _i] = _ROTOR_POS[_i, 1]         # roll moment  (y arm)
    _A[2, _i] = -_ROTOR_POS[_i, 0]        # pitch moment (-x arm)
    _A[3, _i] = _YAW_SIGNS[_i]            # yaw moment
_A_INV = np.linalg.pinv(_A)               # pseudo-inverse (4x4, full rank)

# Physical constants
_MASS = 1.325          # kg  (4x0.25 rotor + 0.325 body)
_G = 9.81              # m/s^2
_HOVER_PER_ROTOR = _MASS * _G / 4.0   # ~3.2496 N
_CTRL_MIN = 0.0
_CTRL_MAX = 13.0

# Cascaded PD gains (validated in simulation)
_KP_POS = np.array([2.0, 2.0, 5.0])    # position proportional
_KD_POS = np.array([1.5, 1.5, 3.0])    # position derivative
_KP_ATT = np.array([10.0, 10.0, 5.0])  # attitude proportional
_KD_ATT = np.array([3.0,  3.0,  1.5])  # attitude derivative
_MAX_TILT = math.radians(25.0)          # max roll/pitch command (rad)


# ---------------------------------------------------------------------------
# Robot class
# ---------------------------------------------------------------------------

class Robot:
    """
    Skydio X2 quadrotor driver.

    The drone is controlled by a cascaded PD controller that runs every
    simulation step.  All public motion methods block until the manoeuvre
    completes (or times out) and return True on success, False on failure.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        self._model = model
        self._data = data

        # Actuator indices (order matches x2.xml: thrust1..4)
        self._act_ids = []
        for i in range(4):
            aid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR,
                                    f"thrust{i+1}")
            if aid < 0:
                raise RuntimeError(f"Actuator 'thrust{i+1}' not found in model.")
            self._act_ids.append(aid)

        # Body index
        self._body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "x2")
        if self._body_id < 0:
            raise RuntimeError("Body 'x2' not found in model.")

        # freejoint qpos start address
        body_jntadr = model.body_jntadr[self._body_id]
        if body_jntadr >= 0:
            self._qpos_start = int(model.jnt_qposadr[body_jntadr])
        else:
            self._qpos_start = 0

        # Renderer (lazy init)
        self._renderer = None

        # Controller target (updated by motion methods)
        self._target_pos = np.array([0.0, 0.0, 0.3])
        self._target_yaw = 0.0

        # Timestep
        self._dt = float(model.opt.timestep)   # 0.01 s

    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """
        Load MJCF (resolving symlinks) and return a ready-to-use Robot.

        Parameters
        ----------
        mjcf_path : str
            Path to the scene XML file (may be a symlink).
        """
        # Resolve symlinks so MuJoCo can find included files
        real_path = os.path.realpath(mjcf_path)
        model = mujoco.MjModel.from_xml_path(real_path)
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        robot = cls(model, data)
        print(f"[Robot] Loaded '{real_path}'")
        print(f"[Robot] Skydio X2 quadrotor — mass={_MASS:.3f} kg, "
              f"hover/rotor={_HOVER_PER_ROTOR:.4f} N, dt={robot._dt:.4f} s")
        return robot

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_state(self):
        """Return (pos, vel, quat_wxyz, omega_body) from mjData."""
        qs = self._qpos_start
        pos   = self._data.qpos[qs:qs+3].copy()
        quat  = self._data.qpos[qs+3:qs+7].copy()   # [w, x, y, z]
        vel   = self._data.qvel[0:3].copy()           # world-frame linear vel
        omega = self._data.qvel[3:6].copy()           # body-frame angular vel
        return pos, vel, quat, omega

    def _compute_ctrl(self, pos, vel, quat, omega,
                      target_pos, target_yaw=0.0) -> np.ndarray:
        """
        Cascaded PD controller -> per-rotor thrust commands.

        Outer loop: position PD -> desired total thrust + desired tilt angles.
        Inner loop: attitude PD -> desired moments.
        Mixer: pseudo-inverse of allocation matrix -> per-rotor thrusts.
        """
        # ---- Outer (position) loop ----
        pos_err = target_pos - pos
        vel_err = -vel                          # desired velocity = 0
        acc_des = _KP_POS * pos_err + _KD_POS * vel_err

        # Total thrust to achieve desired vertical acceleration
        thrust_total = _MASS * (_G + acc_des[2])
        thrust_total = float(np.clip(thrust_total, 0.5, 4.0 * _CTRL_MAX))

        # Desired tilt angles from horizontal acceleration (small-angle approx)
        roll, pitch, yaw = _quat_to_euler(quat)
        cy, sy = math.cos(yaw), math.sin(yaw)
        # Rotate desired horizontal accel into body-yaw frame
        ax_body =  acc_des[0] * cy + acc_des[1] * sy
        ay_body = -acc_des[0] * sy + acc_des[1] * cy   # correct: world->body-yaw rotation
        pitch_des = float(np.clip( ax_body / _G, -_MAX_TILT, _MAX_TILT))
        roll_des  = float(np.clip(-ay_body / _G, -_MAX_TILT, _MAX_TILT))

        # ---- Inner (attitude) loop ----
        roll_err  = roll_des  - roll
        pitch_err = pitch_des - pitch
        yaw_err   = _wrap_angle(target_yaw - yaw)
        att_err   = np.array([roll_err, pitch_err, yaw_err])
        omega_err = -omega                      # desired omega = 0

        moments = _KP_ATT * att_err + _KD_ATT * omega_err

        # ---- Mixer ----
        wrench = np.array([thrust_total, moments[0], moments[1], moments[2]])
        ctrl = _A_INV @ wrench
        ctrl = np.clip(ctrl, _CTRL_MIN, _CTRL_MAX)
        return ctrl

    def _apply_ctrl(self, ctrl: np.ndarray):
        """Write per-rotor thrusts to mjData.ctrl."""
        for k, aid in enumerate(self._act_ids):
            self._data.ctrl[aid] = float(ctrl[k])

    def _step_controlled(self, target_pos, target_yaw=0.0, n_steps=1):
        """Advance simulation n_steps with the cascaded controller active."""
        for _ in range(n_steps):
            pos, vel, quat, omega = self._get_state()
            ctrl = self._compute_ctrl(pos, vel, quat, omega,
                                      target_pos, target_yaw)
            self._apply_ctrl(ctrl)
            mujoco.mj_step(self._model, self._data)

    def _is_upright(self, max_tilt_deg: float = 45.0) -> bool:
        """Return True if the drone body is not flipped."""
        _, _, quat, _ = self._get_state()
        roll, pitch, _ = _quat_to_euler(quat)
        return (abs(math.degrees(roll))  < max_tilt_deg and
                abs(math.degrees(pitch)) < max_tilt_deg)

    # ------------------------------------------------------------------
    # Required API
    # ------------------------------------------------------------------

    def home(self) -> bool:
        """
        Reset to the keyframe hover pose (z=0.3 m, level, zero velocity).
        Sets all actuators to hover equilibrium (~3.2496 N each).
        """
        key_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_KEY, "hover")
        if key_id >= 0:
            mujoco.mj_resetDataKeyframe(self._model, self._data, key_id)
        else:
            qs = self._qpos_start
            self._data.qpos[qs:qs+3]   = [0.0, 0.0, 0.3]
            self._data.qpos[qs+3:qs+7] = [1.0, 0.0, 0.0, 0.0]
            self._data.qvel[:]         = 0.0
            for aid in self._act_ids:
                self._data.ctrl[aid] = _HOVER_PER_ROTOR

        mujoco.mj_forward(self._model, self._data)
        self._target_pos = np.array([0.0, 0.0, 0.3])
        self._target_yaw = 0.0
        print("[Robot] home() — reset to hover keyframe at z=0.3 m")
        return True

    def get_joint_positions(self) -> np.ndarray:
        """
        Return the freejoint qpos: [x, y, z, qw, qx, qy, qz].
        (The X2 has no revolute joints; the freejoint IS the 6-DOF state.)
        """
        qs = self._qpos_start
        return self._data.qpos[qs:qs+7].copy()

    def step(self, n: int = 1):
        """
        Advance the simulation n steps with the current controller target.
        The cascaded PD controller is always active (drone never open-loop).
        """
        for _ in range(n):
            pos, vel, quat, omega = self._get_state()
            ctrl = self._compute_ctrl(pos, vel, quat, omega,
                                      self._target_pos, self._target_yaw)
            self._apply_ctrl(ctrl)
            mujoco.mj_step(self._model, self._data)

    def render(self):
        """
        Render the current scene to an offscreen RGB buffer.
        Returns the (H, W, 3) uint8 array and prints pose info.
        """
        if self._renderer is None:
            self._renderer = mujoco.Renderer(self._model, height=480, width=640)
        self._renderer.update_scene(self._data, camera="track")
        frame = self._renderer.render()
        pos, _, quat, _ = self._get_state()
        roll, pitch, yaw = _quat_to_euler(quat)
        print(f"[render] pos=({pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f}) m  "
              f"rpy=({math.degrees(roll):.1f}, {math.degrees(pitch):.1f}, "
              f"{math.degrees(yaw):.1f}) deg  frame={frame.shape}")
        return frame

    def describe(self) -> str:
        """Return a human-readable description of the robot and current state."""
        pos, vel, quat, omega = self._get_state()
        roll, pitch, yaw = _quat_to_euler(quat)
        speed = float(np.linalg.norm(vel))
        lines = [
            "=== Skydio X2 Quadrotor ===",
            f"  Class         : AERIAL / multirotor",
            f"  DOF           : 6 (freejoint — no revolute joints)",
            f"  Actuators     : 4 x thrust motors, ctrlrange [0, 13] N",
            f"  Total mass    : {_MASS:.3f} kg",
            f"  Hover thrust  : {_HOVER_PER_ROTOR:.4f} N/rotor",
            f"  Controller    : Cascaded PD (position + attitude loops)",
            f"  Mixer         : pseudo-inverse of 4x4 allocation matrix",
            f"  --- Current state ---",
            f"  Position      : ({pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f}) m",
            f"  Speed         : {speed:.3f} m/s",
            f"  Roll/Pitch/Yaw: ({math.degrees(roll):.1f}, "
            f"{math.degrees(pitch):.1f}, {math.degrees(yaw):.1f}) deg",
            f"  Target pos    : ({self._target_pos[0]:.3f}, "
            f"{self._target_pos[1]:.3f}, {self._target_pos[2]:.3f}) m",
            f"  Target yaw    : {math.degrees(self._target_yaw):.1f} deg",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Aerial-specific API
    # ------------------------------------------------------------------

    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Return (xyz, R3x3) — world position and orientation of the drone body.

        Returns
        -------
        xyz : np.ndarray, shape (3,)
            World-frame position in metres.
        R   : np.ndarray, shape (3, 3)
            World-frame rotation matrix (body axes as columns).
        """
        pos, _, quat, _ = self._get_state()
        R = _quat_to_rot(quat)
        return pos.copy(), R

    def get_base_yaw(self) -> float:
        """Return current yaw angle in radians."""
        _, _, quat, _ = self._get_state()
        _, _, yaw = _quat_to_euler(quat)
        return yaw

    def takeoff(self, height: float = 0.5) -> bool:
        """
        Spin up rotors and climb to `height` metres, then hold a stable hover.

        The cascaded PD controller runs every step.  Returns True when the
        drone reaches within 5 cm of the target height and stays upright for
        1 s.  Returns False if the drone flips or times out (10 s).

        Parameters
        ----------
        height : float
            Target altitude in metres (default 0.5 m).
        """
        print(f"[takeoff] Climbing to z={height:.2f} m ...")
        pos0, _, _, _ = self._get_state()
        target = np.array([pos0[0], pos0[1], height])
        self._target_pos = target.copy()

        timeout_steps = int(10.0 / self._dt)   # 10 s max
        settle_steps  = int(1.0  / self._dt)   # must hold for 1 s
        tol = 0.05                              # 5 cm

        settled = 0
        for step in range(timeout_steps):
            self._step_controlled(target, self._target_yaw)

            if not self._is_upright():
                print("[takeoff] FAILED — drone flipped!")
                return False

            pos, _, _, _ = self._get_state()
            err = abs(pos[2] - height)
            if err < tol:
                settled += 1
                if settled >= settle_steps:
                    print(f"[takeoff] Reached z={pos[2]:.3f} m "
                          f"(err={err:.4f} m) after {step*self._dt:.1f} s")
                    return True
            else:
                settled = 0

        pos, _, _, _ = self._get_state()
        err = abs(pos[2] - height)
        print(f"[takeoff] Timeout — final z={pos[2]:.3f} m "
              f"(target={height:.2f} m, err={err:.4f} m)")
        return err < tol * 2

    def move_to(self, x: float, y: float, z: float,
                tol: float = 0.1) -> bool:
        """
        Fly to world position (x, y, z) with closed-loop position control.

        Returns True when the drone arrives within `tol` metres and stays
        upright.  Returns False on flip or timeout (15 s).

        Parameters
        ----------
        x, y, z : float
            Target world position in metres.
        tol : float
            Arrival tolerance in metres (default 0.1 m).
        """
        target = np.array([x, y, z])
        self._target_pos = target.copy()
        print(f"[move_to] Flying to ({x:.2f}, {y:.2f}, {z:.2f}) m ...")

        timeout_steps = int(15.0 / self._dt)
        settle_steps  = int(0.5  / self._dt)   # hold for 0.5 s

        settled = 0
        for step in range(timeout_steps):
            self._step_controlled(target, self._target_yaw)

            if not self._is_upright():
                print("[move_to] FAILED — drone flipped!")
                return False

            pos, _, _, _ = self._get_state()
            err = float(np.linalg.norm(pos - target))
            if err < tol:
                settled += 1
                if settled >= settle_steps:
                    print(f"[move_to] Arrived at ({pos[0]:.3f}, {pos[1]:.3f}, "
                          f"{pos[2]:.3f}) m  err={err:.4f} m  "
                          f"t={step*self._dt:.1f} s")
                    return True
            else:
                settled = 0

        pos, _, _, _ = self._get_state()
        err = float(np.linalg.norm(pos - target))
        print(f"[move_to] Timeout — pos=({pos[0]:.3f}, {pos[1]:.3f}, "
              f"{pos[2]:.3f}) m  err={err:.4f} m")
        return err < tol * 2

    def hover(self, secs: float = 2.0) -> bool:
        """
        Station-keep at the current pose for `secs` seconds.

        Returns True if the drone remains upright throughout.

        Parameters
        ----------
        secs : float
            Duration to hover in seconds (default 2.0 s).
        """
        pos0, _, quat0, _ = self._get_state()
        _, _, yaw0 = _quat_to_euler(quat0)
        target = pos0.copy()
        self._target_pos = target.copy()
        self._target_yaw = yaw0

        n_steps = int(secs / self._dt)
        print(f"[hover] Station-keeping at ({target[0]:.3f}, {target[1]:.3f}, "
              f"{target[2]:.3f}) m for {secs:.1f} s ...")

        for step in range(n_steps):
            self._step_controlled(target, yaw0)
            if not self._is_upright():
                print(f"[hover] FAILED — drone flipped at "
                      f"t={step*self._dt:.2f} s!")
                return False

        pos, _, _, _ = self._get_state()
        drift = float(np.linalg.norm(pos - target))
        print(f"[hover] Done — drift={drift:.4f} m")
        return True

    def land(self) -> bool:
        """
        Descend to the ground and idle the rotors.

        Phase 1: Controlled descent to z=0.05 m (5 cm above ground).
        Phase 2: Cut thrust and let the drone settle on the floor.

        Returns True when the drone is on the ground (z < 0.15 m).
        """
        pos0, _, _, _ = self._get_state()
        print(f"[land] Descending from z={pos0[2]:.3f} m ...")

        # Phase 1: controlled descent to 5 cm
        low_target = np.array([pos0[0], pos0[1], 0.05])
        self._target_pos = low_target.copy()
        timeout_steps = int(15.0 / self._dt)

        for step in range(timeout_steps):
            self._step_controlled(low_target, self._target_yaw)
            if not self._is_upright():
                print("[land] WARNING — drone tilted during descent, "
                      "cutting thrust")
                break
            pos, _, _, _ = self._get_state()
            if pos[2] < 0.08:
                break

        # Phase 2: cut thrust and settle
        print("[land] Cutting thrust ...")
        settle_steps = int(1.5 / self._dt)
        for _ in range(settle_steps):
            for aid in self._act_ids:
                self._data.ctrl[aid] = 0.0
            mujoco.mj_step(self._model, self._data)

        pos, _, _, _ = self._get_state()
        on_ground = pos[2] < 0.15
        print(f"[land] Final z={pos[2]:.4f} m — "
              f"{'landed OK' if on_ground else 'WARNING: still airborne'}")
        self._target_pos = np.array([pos[0], pos[1], 0.05])
        return on_ground


# ---------------------------------------------------------------------------
# Quick self-test (run as script)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    mjcf = "mjcf.xml"
    if not os.path.exists(mjcf):
        print(f"ERROR: {mjcf} not found — run from the workspace directory.")
        sys.exit(1)

    print("=" * 60)
    print("Skydio X2 driver self-test")
    print("=" * 60)

    r = Robot.build_from_mjcf(mjcf)
    r.home()
    print(r.describe())

    print("\n--- Test: takeoff to 0.5 m ---")
    ok = r.takeoff(height=0.5)
    print(f"takeoff: {'PASS' if ok else 'FAIL'}")

    print("\n--- Test: hover 1 s ---")
    ok = r.hover(secs=1.0)
    print(f"hover: {'PASS' if ok else 'FAIL'}")

    print("\n--- Test: move_to (1, 0, 0.5) ---")
    ok = r.move_to(1.0, 0.0, 0.5, tol=0.1)
    print(f"move_to: {'PASS' if ok else 'FAIL'}")

    print("\n--- Test: land ---")
    ok = r.land()
    print(f"land: {'PASS' if ok else 'FAIL'}")

    print("\n--- Final state ---")
    print(r.describe())
    print("\nAll tests complete.")
