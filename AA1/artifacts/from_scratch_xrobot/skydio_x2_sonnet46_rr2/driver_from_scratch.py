"""
driver_from_scratch.py — Skydio X2 Quadrotor UAV Driver
=========================================================
Robot class for the Skydio X2 aerial vehicle (MJCF model).

Architecture
------------
The X2 is a single rigid body on a free-joint with 4 site-based motor
actuators (thrust1-4).  Each motor gear vector is [0,0,1,0,0,±0.0201],
meaning each rotor contributes:
  • a body-frame Z-force  (ctrl * 1.0)
  • a body-frame Z-torque (ctrl * ±0.0201)  — alternating sign for yaw

Controller: CASCADED PD
  Outer loop  (position)  → desired total thrust + desired roll/pitch angles
  Inner loop  (attitude)  → roll/pitch/yaw torques → per-rotor mixing

Mixing matrix M maps [F_z, τ_x, τ_y, τ_z] → [ctrl1..4] via M⁻¹.
All control is applied every mj_step call so the unstable free-flyer
never runs open-loop.

Public API
----------
  Robot.build_from_mjcf(mjcf_path) -> Robot
  robot.home()                     -> bool
  robot.get_joint_positions()      -> np.ndarray  (7-element qpos)
  robot.step(n=1)
  robot.render()
  robot.describe()                 -> str
  robot.takeoff(height=0.5)        -> bool
  robot.move_to(x, y, z, tol=0.1) -> bool
  robot.hover(secs=2.0)            -> bool
  robot.land()                     -> bool
  robot.get_base_pose()            -> (xyz, R3x3)
"""

import math
import time
from typing import Tuple

import mujoco
import numpy as np

# ---------------------------------------------------------------------------
# Quaternion / rotation helpers
# ---------------------------------------------------------------------------

def _quat_to_rot(q: np.ndarray) -> np.ndarray:
    """Convert quaternion [w, x, y, z] → 3×3 rotation matrix."""
    w, x, y, z = q
    return np.array([
        [1 - 2*(y*y + z*z),  2*(x*y - w*z),      2*(x*z + w*y)],
        [2*(x*y + w*z),      1 - 2*(x*x + z*z),  2*(y*z - w*x)],
        [2*(x*z - w*y),      2*(y*z + w*x),      1 - 2*(x*x + y*y)],
    ], dtype=float)


def _rot_to_euler_zyx(R: np.ndarray) -> Tuple[float, float, float]:
    """Extract (roll, pitch, yaw) from a ZYX rotation matrix."""
    pitch = math.asin(-float(np.clip(R[2, 0], -1.0, 1.0)))
    roll  = math.atan2(float(R[2, 1]), float(R[2, 2]))
    yaw   = math.atan2(float(R[1, 0]), float(R[0, 0]))
    return roll, pitch, yaw


def _wrap_angle(a: float) -> float:
    """Wrap angle to (−π, π]."""
    return (a + math.pi) % (2 * math.pi) - math.pi


def _quat_integrate(q: np.ndarray, omega: np.ndarray, dt: float) -> np.ndarray:
    """
    Integrate quaternion q by angular velocity omega (body frame) over dt.
    Uses first-order approximation: q_new = q + 0.5*dt*Ω(omega)*q
    """
    wx, wy, wz = omega
    # Ω matrix (skew-symmetric quaternion product)
    dq = 0.5 * dt * np.array([
        -wx*q[1] - wy*q[2] - wz*q[3],
         wx*q[0] + wz*q[2] - wy*q[3],
         wy*q[0] - wz*q[1] + wx*q[3],
         wz*q[0] + wy*q[1] - wx*q[2],
    ])
    q_new = q + dq
    norm = np.linalg.norm(q_new)
    if norm < 1e-9:
        return np.array([1.0, 0.0, 0.0, 0.0])
    return q_new / norm


# ---------------------------------------------------------------------------
# Cascaded PD controller
# ---------------------------------------------------------------------------

class _CascadedController:
    """
    Cascaded position + attitude PD controller for the Skydio X2.

    Outer loop  (10 Hz equivalent, but run every step):
        pos_error, vel_error → desired total thrust + desired roll/pitch

    Inner loop  (run every step):
        attitude_error, rate_error → torques → per-rotor mixing

    Mixing matrix M:
        [F_z, τ_x, τ_y, τ_z] = M @ [ctrl1, ctrl2, ctrl3, ctrl4]
    """

    # Rotor positions in body frame (from x2.xml)
    _ROTOR_POS = np.array([
        [-0.14, -0.18, 0.05],   # thrust1
        [-0.14, +0.18, 0.05],   # thrust2
        [+0.14, +0.18, 0.08],   # thrust3
        [+0.14, -0.18, 0.08],   # thrust4
    ])
    # Yaw torque coefficients (alternating sign)
    _YAW_COEFF = np.array([-0.0201, +0.0201, -0.0201, +0.0201])

    def __init__(self, mass: float, g: float = 9.81):
        self.mass = mass
        self.g    = g
        self.hover_thrust = mass * g / 4.0  # per-rotor hover thrust

        # Build mixing matrix
        r = self._ROTOR_POS
        self.M = np.array([
            [1.0,    1.0,    1.0,    1.0   ],   # F_z
            [r[0,1], r[1,1], r[2,1], r[3,1]],   # τ_x  (roll)
            [-r[0,0],-r[1,0],-r[2,0],-r[3,0]],  # τ_y  (pitch)
            self._YAW_COEFF,                      # τ_z  (yaw)
        ])
        self.M_inv = np.linalg.inv(self.M)

        # ---- Outer-loop (position) gains ----
        self.kp_xy  = 2.0    # horizontal position
        self.kd_xy  = 1.8    # horizontal velocity
        self.kp_z   = 5.0    # vertical position
        self.kd_z   = 3.5    # vertical velocity

        # ---- Inner-loop (attitude) gains ----
        self.kp_rp  = 9.0    # roll/pitch angle
        self.kd_rp  = 2.5    # roll/pitch rate
        self.kp_yaw = 3.0    # yaw angle
        self.kd_yaw = 1.0    # yaw rate

        # Limits
        self.max_tilt   = math.radians(30)   # max roll/pitch command
        self.ctrl_min   = 0.0
        self.ctrl_max   = 13.0

    def compute(
        self,
        pos:        np.ndarray,   # [x, y, z]  world
        vel:        np.ndarray,   # [vx,vy,vz] world
        quat:       np.ndarray,   # [w, x, y, z]
        omega:      np.ndarray,   # [wx,wy,wz]  body frame
        target_pos: np.ndarray,   # [x, y, z]  desired
        target_yaw: float = 0.0,  # desired yaw
    ) -> np.ndarray:
        """Return per-rotor ctrl array [4], clamped to [ctrl_min, ctrl_max]."""
        R = _quat_to_rot(quat)
        roll, pitch, yaw = _rot_to_euler_zyx(R)

        # ---- Outer loop: position → desired thrust + attitude ----
        pos_err = target_pos - pos
        vel_err = -vel  # desired velocity = 0

        ax_des = self.kp_xy * pos_err[0] + self.kd_xy * vel_err[0]
        ay_des = self.kp_xy * pos_err[1] + self.kd_xy * vel_err[1]
        az_des = self.kp_z  * pos_err[2] + self.kd_z  * vel_err[2]

        # Total thrust needed (world Z)
        F_total = self.mass * (self.g + az_des)
        F_total = float(np.clip(F_total, 0.5, 4.0 * self.ctrl_max))

        # Desired roll/pitch from horizontal acceleration commands
        # (small-angle: F_total ≈ mg, so pitch_des ≈ ax_des/g)
        pitch_des = math.atan2(self.mass * ax_des, F_total)
        roll_des  = math.atan2(-self.mass * ay_des, F_total)
        pitch_des = float(np.clip(pitch_des, -self.max_tilt, self.max_tilt))
        roll_des  = float(np.clip(roll_des,  -self.max_tilt, self.max_tilt))

        # ---- Inner loop: attitude → torques ----
        roll_err  = roll_des  - roll
        pitch_err = pitch_des - pitch
        yaw_err   = _wrap_angle(target_yaw - yaw)

        # Body-frame angular rate errors (desired rate = 0)
        tau_x = self.kp_rp  * roll_err  + self.kd_rp  * (-omega[0])
        tau_y = self.kp_rp  * pitch_err + self.kd_rp  * (-omega[1])
        tau_z = self.kp_yaw * yaw_err   + self.kd_yaw * (-omega[2])

        # ---- Mixing: [F_z, τ_x, τ_y, τ_z] → per-rotor ctrl ----
        desired = np.array([F_total, tau_x, tau_y, tau_z])
        ctrl = self.M_inv @ desired
        ctrl = np.clip(ctrl, self.ctrl_min, self.ctrl_max)
        return ctrl


# ---------------------------------------------------------------------------
# Robot class
# ---------------------------------------------------------------------------

class Robot:
    """
    Skydio X2 quadrotor UAV driver.

    The X2 is a free-flying rigid body with 4 thrust actuators.
    A cascaded PD controller (position outer loop + attitude inner loop)
    runs every simulation step to keep the vehicle stable.
    """

    # ------------------------------------------------------------------ #
    #  Construction                                                        #
    # ------------------------------------------------------------------ #

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        self._model = model
        self._data  = data

        # Body / joint indices
        self._body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "x2")
        if self._body_id < 0:
            raise RuntimeError("Body 'x2' not found in model")

        # Free-joint: qpos[0:3]=xyz, qpos[3:7]=quat(w,x,y,z)
        #             qvel[0:3]=lin_vel, qvel[3:6]=ang_vel
        self._qpos_adr = 0   # free-joint always starts at 0
        self._qvel_adr = 0

        # Actuator indices
        self._act_ids = []
        for name in ("thrust1", "thrust2", "thrust3", "thrust4"):
            aid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            if aid < 0:
                raise RuntimeError(f"Actuator '{name}' not found")
            self._act_ids.append(aid)

        # IMU site
        self._imu_site = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "imu")

        # Physical parameters
        self._mass = float(np.sum(model.body_mass))
        self._g    = float(abs(model.opt.gravity[2]))
        self._dt   = float(model.opt.timestep)

        # Controller
        self._ctrl = _CascadedController(self._mass, self._g)

        # Flight state
        self._target_pos = np.array([0.0, 0.0, 0.1])  # current setpoint
        self._target_yaw = 0.0
        self._armed      = False   # True after takeoff

        # Renderer (lazy)
        self._renderer = None

    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """Load MJCF and return a ready-to-use Robot instance."""
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data  = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        robot = cls(model, data)
        print(f"[Robot] Skydio X2 loaded — mass={robot._mass:.3f} kg, "
              f"hover_thrust={robot._ctrl.hover_thrust:.4f} N/rotor, "
              f"dt={robot._dt:.4f} s")
        return robot

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _get_state(self):
        """Return (pos, vel, quat, omega) from current MjData."""
        d = self._data
        pos   = d.qpos[0:3].copy()
        quat  = d.qpos[3:7].copy()   # [w, x, y, z]
        vel   = d.qvel[0:3].copy()
        omega = d.qvel[3:6].copy()   # body-frame angular velocity
        return pos, vel, quat, omega

    def _apply_ctrl(self):
        """Compute and apply controller output to MjData.ctrl."""
        pos, vel, quat, omega = self._get_state()
        ctrl = self._ctrl.compute(
            pos, vel, quat, omega,
            self._target_pos, self._target_yaw
        )
        for i, aid in enumerate(self._act_ids):
            self._data.ctrl[aid] = ctrl[i]

    def _idle_ctrl(self):
        """Zero all thrust (disarmed / landed)."""
        for aid in self._act_ids:
            self._data.ctrl[aid] = 0.0

    def _step_controlled(self, n: int = 1):
        """Advance simulation n steps with the controller active."""
        for _ in range(n):
            self._apply_ctrl()
            mujoco.mj_step(self._model, self._data)

    def _step_idle(self, n: int = 1):
        """Advance simulation n steps with zero thrust."""
        for _ in range(n):
            self._idle_ctrl()
            mujoco.mj_step(self._model, self._data)

    def _steps_for(self, secs: float) -> int:
        """Number of simulation steps for a given duration."""
        return max(1, int(round(secs / self._dt)))

    def _is_upright(self, tol_deg: float = 45.0) -> bool:
        """Return True if the body is roughly upright (not flipped)."""
        _, _, quat, _ = self._get_state()
        R = _quat_to_rot(quat)
        # World Z axis in body frame should point up
        up_dot = R[2, 2]   # R[2,2] = cos(tilt)
        return up_dot > math.cos(math.radians(tol_deg))

    # ------------------------------------------------------------------ #
    #  Mandatory API                                                       #
    # ------------------------------------------------------------------ #

    def home(self) -> bool:
        """
        Reset to the keyframe 'hover' pose (z=0.3 m, level, zero velocity).
        Disarms the controller so the drone sits at rest on the ground.
        """
        # Use the keyframe if available
        key_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_KEY, "hover")
        if key_id >= 0:
            mujoco.mj_resetDataKeyframe(self._model, self._data, key_id)
        else:
            # Manual reset: place on ground
            self._data.qpos[:] = 0.0
            self._data.qpos[2] = 0.1   # z = 0.1 m (initial height in XML)
            self._data.qpos[3] = 1.0   # quaternion w=1
            self._data.qvel[:] = 0.0

        self._idle_ctrl()
        mujoco.mj_forward(self._model, self._data)

        # Reset setpoint to current position
        self._target_pos = self._data.qpos[0:3].copy()
        self._target_yaw = 0.0
        self._armed = False
        print("[Robot] home() — reset to initial pose")
        return True

    def get_joint_positions(self) -> np.ndarray:
        """
        Return the full qpos vector (7 elements for the free joint):
          [x, y, z, qw, qx, qy, qz]
        """
        return self._data.qpos.copy()

    def step(self, n: int = 1):
        """
        Advance the simulation by n steps.
        If armed, the controller runs; otherwise thrust is zero.
        """
        if self._armed:
            self._step_controlled(n)
        else:
            self._step_idle(n)

    def render(self):
        """Render the current frame to an offscreen buffer and return it."""
        if self._renderer is None:
            self._renderer = mujoco.Renderer(self._model, height=480, width=640)
        self._renderer.update_scene(self._data)
        return self._renderer.render()

    def describe(self) -> str:
        pos, vel, quat, omega = self._get_state()
        R = _quat_to_rot(quat)
        roll, pitch, yaw = _rot_to_euler_zyx(R)
        speed = float(np.linalg.norm(vel))
        return (
            f"Skydio X2 Quadrotor UAV\n"
            f"  Position  : x={pos[0]:.3f}  y={pos[1]:.3f}  z={pos[2]:.3f} m\n"
            f"  Attitude  : roll={math.degrees(roll):.1f}°  "
            f"pitch={math.degrees(pitch):.1f}°  yaw={math.degrees(yaw):.1f}°\n"
            f"  Speed     : {speed:.3f} m/s\n"
            f"  Setpoint  : {self._target_pos}  yaw={math.degrees(self._target_yaw):.1f}°\n"
            f"  Armed     : {self._armed}\n"
            f"  Mass      : {self._mass:.3f} kg\n"
            f"  Hover/rotor: {self._ctrl.hover_thrust:.4f} N\n"
            f"  Timestep  : {self._dt:.4f} s"
        )

    # ------------------------------------------------------------------ #
    #  Aerial API                                                          #
    # ------------------------------------------------------------------ #

    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Return (xyz, R3x3) — world position and orientation of the X2 body.
        """
        pos, _, quat, _ = self._get_state()
        R = _quat_to_rot(quat)
        return pos.copy(), R

    def get_base_yaw(self) -> float:
        """Return current yaw angle in radians."""
        _, _, quat, _ = self._get_state()
        R = _quat_to_rot(quat)
        _, _, yaw = _rot_to_euler_zyx(R)
        return yaw

    def takeoff(self, height: float = 0.5) -> bool:
        """
        Spin up rotors and climb to `height` metres, then hold a stable hover.

        The controller runs every step.  We wait until the drone is within
        5 cm of the target height and is upright, then declare success.

        Returns True on success, False if the drone flips or times out.
        """
        if height <= 0.05:
            raise ValueError(f"takeoff height must be > 0.05 m, got {height}")

        pos, _, _, _ = self._get_state()
        # Set target to current XY but desired height
        self._target_pos = np.array([pos[0], pos[1], height])
        self._target_yaw = 0.0
        self._armed = True

        timeout_steps = self._steps_for(10.0)   # 10 s max
        tol = 0.05   # 5 cm altitude tolerance

        print(f"[Robot] takeoff() → target z={height:.2f} m …")
        for step in range(timeout_steps):
            self._step_controlled(1)

            if not self._is_upright(45.0):
                print(f"[Robot] takeoff() FAILED — drone flipped at step {step}")
                self._armed = False
                self._idle_ctrl()
                return False

            cur_pos, _, _, _ = self._get_state()
            if abs(cur_pos[2] - height) < tol:
                # Hold for 0.5 s to confirm stability
                stable = True
                for _ in range(self._steps_for(0.5)):
                    self._step_controlled(1)
                    if not self._is_upright(45.0):
                        stable = False
                        break
                if stable:
                    print(f"[Robot] takeoff() SUCCESS — z={cur_pos[2]:.3f} m "
                          f"after {(step+1)*self._dt:.2f} s")
                    return True

        pos, _, _, _ = self._get_state()
        print(f"[Robot] takeoff() TIMEOUT — final z={pos[2]:.3f} m")
        return False

    def move_to(
        self,
        x: float,
        y: float,
        z: float,
        tol: float = 0.1,
    ) -> bool:
        """
        Fly to world position (x, y, z) with closed-loop position control.

        The drone must already be airborne (call takeoff() first).
        Returns True when within `tol` metres of the target and upright.
        """
        if not self._armed:
            print("[Robot] move_to() — drone not armed; call takeoff() first")
            return False

        target = np.array([x, y, z], dtype=float)
        self._target_pos = target.copy()

        timeout_steps = self._steps_for(15.0)
        print(f"[Robot] move_to({x:.2f}, {y:.2f}, {z:.2f}) …")

        for step in range(timeout_steps):
            self._step_controlled(1)

            if not self._is_upright(45.0):
                print(f"[Robot] move_to() FAILED — drone flipped at step {step}")
                self._armed = False
                self._idle_ctrl()
                return False

            pos, vel, _, _ = self._get_state()
            dist = float(np.linalg.norm(pos - target))
            speed = float(np.linalg.norm(vel))

            if dist < tol and speed < 0.3:
                print(f"[Robot] move_to() SUCCESS — dist={dist:.3f} m "
                      f"after {(step+1)*self._dt:.2f} s")
                return True

        pos, _, _, _ = self._get_state()
        dist = float(np.linalg.norm(pos - target))
        print(f"[Robot] move_to() TIMEOUT — dist={dist:.3f} m from target")
        return False

    def hover(self, secs: float = 2.0) -> bool:
        """
        Station-keep at the current pose for `secs` seconds.

        Updates the setpoint to the current position so the controller
        actively rejects disturbances.  Returns True if the drone stays
        upright for the full duration.
        """
        if not self._armed:
            print("[Robot] hover() — drone not armed; call takeoff() first")
            return False

        pos, _, quat, _ = self._get_state()
        R = _quat_to_rot(quat)
        _, _, yaw = _rot_to_euler_zyx(R)

        # Lock setpoint to current position
        self._target_pos = pos.copy()
        self._target_yaw = yaw

        n_steps = self._steps_for(secs)
        print(f"[Robot] hover() for {secs:.1f} s at z={pos[2]:.3f} m …")

        for step in range(n_steps):
            self._step_controlled(1)
            if not self._is_upright(45.0):
                print(f"[Robot] hover() FAILED — drone flipped at step {step}")
                self._armed = False
                self._idle_ctrl()
                return False

        pos, _, _, _ = self._get_state()
        print(f"[Robot] hover() DONE — final z={pos[2]:.3f} m")
        return True

    def land(self) -> bool:
        """
        Descend to the ground and idle the rotors.

        Gradually lowers the altitude setpoint until the drone is near
        the ground, then cuts thrust.
        Returns True on successful landing.
        """
        if not self._armed:
            print("[Robot] land() — already disarmed")
            return True

        pos, _, _, _ = self._get_state()
        start_z = pos[2]
        land_z  = 0.05   # target landing height

        print(f"[Robot] land() — descending from z={start_z:.3f} m …")

        # Gradually lower setpoint
        n_steps = self._steps_for(8.0)
        for step in range(n_steps):
            # Linearly ramp down the z setpoint
            frac = min(1.0, step / max(1, n_steps - 1))
            self._target_pos[2] = start_z * (1.0 - frac) + land_z * frac

            self._step_controlled(1)

            if not self._is_upright(60.0):
                print("[Robot] land() — drone tilted, cutting thrust")
                break

            pos, _, _, _ = self._get_state()
            if pos[2] <= 0.08:
                break

        # Cut thrust and let it settle
        self._armed = False
        self._idle_ctrl()
        for _ in range(self._steps_for(1.0)):
            self._step_idle(1)

        pos, _, _, _ = self._get_state()
        print(f"[Robot] land() DONE — final z={pos[2]:.3f} m")
        return pos[2] < 0.15


# ---------------------------------------------------------------------------
# Quick self-test (run as script)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import os

    mjcf_path = sys.argv[1] if len(sys.argv) > 1 else "mjcf.xml"
    if not os.path.exists(mjcf_path):
        print(f"MJCF not found: {mjcf_path}")
        sys.exit(1)

    print("=" * 60)
    print("Skydio X2 Driver — Self-Test")
    print("=" * 60)

    r = Robot.build_from_mjcf(mjcf_path)
    r.home()
    print(r.describe())
    print()

    # Test 1: takeoff
    print("--- Test 1: takeoff to 0.5 m ---")
    ok = r.takeoff(height=0.5)
    print(f"takeoff() returned: {ok}")
    pos, R = r.get_base_pose()
    print(f"Position after takeoff: {pos}")
    print()

    # Test 2: hover
    print("--- Test 2: hover for 2 s ---")
    ok = r.hover(secs=2.0)
    print(f"hover() returned: {ok}")
    pos, R = r.get_base_pose()
    print(f"Position after hover: {pos}")
    print()

    # Test 3: move_to
    print("--- Test 3: move_to(0.5, 0.3, 0.8) ---")
    ok = r.move_to(0.5, 0.3, 0.8)
    print(f"move_to() returned: {ok}")
    pos, R = r.get_base_pose()
    print(f"Position after move_to: {pos}")
    print()

    # Test 4: land
    print("--- Test 4: land ---")
    ok = r.land()
    print(f"land() returned: {ok}")
    pos, R = r.get_base_pose()
    print(f"Position after land: {pos}")
    print()

    print(r.describe())
