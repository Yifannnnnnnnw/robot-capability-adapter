"""
driver_from_scratch.py — Self-contained driver for the Skydio X2 quadrotor.

Robot class: AERIAL / MULTIROTOR (free-flying base + 4 thrust motors).

A quadrotor is underactuated and open-loop unstable. This driver runs a
CASCADED feedback controller at every mj_step:

    outer position loop  : PD on (x, y, z) error -> desired total thrust Fz
                           and desired roll/pitch tilt angles
    inner attitude loop  : PD on (roll, pitch, yaw) error -> body torques
    control mixer        : maps [Fz, Mx, My, Mz] -> 4 rotor thrust commands

No IK, no walking gait — none of those apply to a flying body.

Uses only mujoco, numpy and the stdlib. Nothing from auto_adapter.skeletons.
"""

from __future__ import annotations

import os
import numpy as np
import mujoco


def _quat_to_R(q):
    """Convert a (w, x, y, z) quaternion to a 3x3 rotation matrix."""
    w, x, y, z = q
    n = w * w + x * x + y * y + z * z
    if n < 1e-12:
        return np.eye(3)
    s = 2.0 / n
    return np.array([
        [1 - s * (y * y + z * z), s * (x * y - z * w),     s * (x * z + y * w)],
        [s * (x * y + z * w),     1 - s * (x * x + z * z), s * (y * z - x * w)],
        [s * (x * z - y * w),     s * (y * z + x * w),     1 - s * (x * x + y * y)],
    ])


def _R_to_rpy(R):
    """Extract roll, pitch, yaw (radians) from a rotation matrix (XYZ-ish)."""
    pitch = np.arcsin(-np.clip(R[2, 0], -1.0, 1.0))
    roll = np.arctan2(R[2, 1], R[2, 2])
    yaw = np.arctan2(R[1, 0], R[0, 0])
    return roll, pitch, yaw


class Robot:
    """Closed-loop quadrotor controller for the Skydio X2."""

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def __init__(self, model, data):
        self.model = model
        self.data = data
        self._viewer = None

        # ---- physical constants ----
        self.mass = float(np.sum(model.body_mass))
        self.g = float(-model.opt.gravity[2]) or 9.81
        self.n_rotors = model.nu                      # 4 thrust motors
        self.ctrl_lo = model.actuator_ctrlrange[:, 0].copy()
        self.ctrl_hi = model.actuator_ctrlrange[:, 1].copy()
        self.hover_thrust = self.mass * self.g / max(self.n_rotors, 1)

        # ---- base (free-joint) layout ----
        self.base_bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "x2")
        if self.base_bid < 0:
            self.base_bid = 1  # first non-world body
        # free joint -> qpos[0:3] pos, qpos[3:7] quat, qvel[0:3] linvel, qvel[3:6] angvel
        self._qpos_adr = model.jnt_qposadr[0]
        self._qvel_adr = model.jnt_dofadr[0]

        # ---- build the control allocation (mixer) matrix ----
        # Rows: [total Fz, Mx(roll), My(pitch), Mz(yaw)] ; cols: rotor i
        # Force at rotor i is f_i along body +z applied at offset r_i=(x,y,z):
        #   torque = r x (0,0,f) = (y*f, -x*f, 0)
        # plus yaw reaction torque from the gear's z-component.
        A = np.zeros((4, self.n_rotors))
        for i in range(self.n_rotors):
            gear = model.actuator_gear[i]            # 6-vector force/torque
            site_id = model.actuator_trnid[i, 0]
            rpos = model.site_pos[site_id] if site_id >= 0 else np.zeros(3)
            A[0, i] = gear[2]                        # vertical force coeff (==1)
            A[1, i] = rpos[1] * gear[2]              # roll  Mx = y * f
            A[2, i] = -rpos[0] * gear[2]             # pitch My = -x * f
            A[3, i] = gear[5]                         # yaw reaction torque coeff
        self._A = A
        try:
            self._Ainv = np.linalg.pinv(A)
        except np.linalg.LinAlgError:
            self._Ainv = np.linalg.pinv(A + 1e-6 * np.eye(*A.shape))

        # ---- controller gains (tuned in self-test) ----
        self.kp_z, self.kd_z = 12.0, 6.0      # altitude PD (accel units)
        self.kp_xy, self.kd_xy = 1.5, 2.0     # horizontal position PD
        self.kp_att, self.kd_att = 18.0, 3.0  # roll/pitch attitude PD
        self.kp_yaw, self.kd_yaw = 2.0, 0.5   # yaw PD
        self.max_tilt = 0.35                  # rad, limit commanded tilt

        # control state
        self._target = self.get_base_pose()[0].copy()
        self._yaw_des = 0.0

    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """Load the model from an MJCF file and return a ready Robot."""
        mjcf_path = os.path.abspath(mjcf_path)
        try:
            model = mujoco.MjModel.from_xml_path(mjcf_path)
        except ValueError:
            # Symlinked scene files sometimes can't resolve relative <include>.
            # Resolve via the symlink's real directory.
            real = os.path.realpath(mjcf_path)
            model = mujoco.MjModel.from_xml_path(real)
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        return cls(model, data)

    # ------------------------------------------------------------------ #
    # State queries
    # ------------------------------------------------------------------ #
    def get_base_pose(self):
        """World base position (xyz) and 3x3 rotation matrix."""
        a = self._qpos_adr
        pos = self.data.qpos[a:a + 3].copy()
        quat = self.data.qpos[a + 3:a + 7].copy()
        return pos, _quat_to_R(quat)

    def get_base_yaw(self) -> float:
        _, R = self.get_base_pose()
        return float(_R_to_rpy(R)[2])

    def get_base_height(self) -> float:
        return float(self.data.qpos[self._qpos_adr + 2])

    def get_joint_positions(self):
        """Full free-joint state (3 pos + 4 quat). No articulated joints."""
        a = self._qpos_adr
        return self.data.qpos[a:a + 7].copy()

    def _base_velocity(self):
        v = self._qvel_adr
        linvel = self.data.qvel[v:v + 3].copy()      # world-frame linear vel
        angvel = self.data.qvel[v + 3:v + 6].copy()  # body-frame angular vel
        return linvel, angvel

    # ------------------------------------------------------------------ #
    # Core cascaded controller (called every sim step)
    # ------------------------------------------------------------------ #
    def _compute_ctrl(self, target, yaw_des=0.0):
        pos, R = self.get_base_pose()
        linvel, angvel = self._base_velocity()

        # --- outer loop: altitude -> total thrust ---
        ez = target[2] - pos[2]
        acc_z = self.kp_z * ez - self.kd_z * linvel[2]
        Fz = self.mass * (self.g + acc_z)
        # divide by cos(tilt) so vertical component matches when tilted
        Fz = Fz / max(R[2, 2], 0.3)

        # --- outer loop: horizontal position -> desired tilt ---
        ax = np.clip(self.kp_xy * (target[0] - pos[0]) - self.kd_xy * linvel[0], -4.0, 4.0)
        ay = np.clip(self.kp_xy * (target[1] - pos[1]) - self.kd_xy * linvel[1], -4.0, 4.0)
        # rotate desired world accel into yaw frame so it works at any heading
        roll, pitch, yaw = _R_to_rpy(R)
        c, s = np.cos(yaw), np.sin(yaw)
        ax_b = c * ax + s * ay
        ay_b = -s * ax + c * ay
        des_pitch = np.clip(ax_b / self.g, -self.max_tilt, self.max_tilt)
        des_roll = np.clip(-ay_b / self.g, -self.max_tilt, self.max_tilt)

        # --- inner loop: attitude PD -> body torques ---
        Mx = self.kp_att * (des_roll - roll) - self.kd_att * angvel[0]
        My = self.kp_att * (des_pitch - pitch) - self.kd_att * angvel[1]
        yaw_err = np.arctan2(np.sin(yaw_des - yaw), np.cos(yaw_des - yaw))
        Mz = self.kp_yaw * yaw_err - self.kd_yaw * angvel[2]

        # --- mix to per-rotor thrusts and clamp ---
        wrench = np.array([Fz, Mx, My, Mz])
        f = self._Ainv @ wrench
        return np.clip(f, self.ctrl_lo, self.ctrl_hi)

    def _run(self, target, yaw_des, n_steps):
        """Step the sim n_steps times under the cascaded controller."""
        target = np.asarray(target, dtype=float)
        for _ in range(int(n_steps)):
            self.data.ctrl[:] = self._compute_ctrl(target, yaw_des)
            mujoco.mj_step(self.model, self.data)
            if self._viewer is not None:
                try:
                    self._viewer.sync()
                except Exception:
                    self._viewer = None
        self._target = target.copy()
        self._yaw_des = yaw_des

    def _upright(self) -> bool:
        """True if the body is still roughly upright (not flipped)."""
        _, R = self.get_base_pose()
        return R[2, 2] > 0.5

    # ------------------------------------------------------------------ #
    # Standard required API
    # ------------------------------------------------------------------ #
    def home(self) -> bool:
        """Reset to the MJCF hover keyframe (or ground) and settle a hover."""
        if self.model.nkey > 0:
            mujoco.mj_resetDataKeyframe(self.model, self.data, 0)
        else:
            mujoco.mj_resetData(self.model, self.data)
        mujoco.mj_forward(self.model, self.data)
        pos, _ = self.get_base_pose()
        self._target = pos.copy()
        self._yaw_des = self.get_base_yaw()
        return True

    def step(self, n: int = 1):
        """Advance n control+physics steps holding the current target."""
        self._run(self._target, self._yaw_des, n)
        return self.get_base_pose()

    def render(self):
        """Open / refresh a passive viewer (best-effort, headless-safe)."""
        try:
            import mujoco.viewer as mjv
            if self._viewer is None:
                self._viewer = mjv.launch_passive(self.model, self.data)
            self._viewer.sync()
        except Exception as e:
            print(f"[render] viewer unavailable: {e}")
        return None

    def describe(self) -> str:
        pos, R = self.get_base_pose()
        roll, pitch, yaw = _R_to_rpy(R)
        return (
            "Skydio X2 quadrotor (AERIAL, free base + 4 thrust motors)\n"
            f"  mass={self.mass:.3f} kg, g={self.g:.2f}, hover_thrust/rotor="
            f"{self.hover_thrust:.4f} (ctrlrange {self.ctrl_lo[0]:.0f}-{self.ctrl_hi[0]:.0f})\n"
            f"  base pos=({pos[0]:.3f},{pos[1]:.3f},{pos[2]:.3f}) "
            f"rpy=({np.degrees(roll):.1f},{np.degrees(pitch):.1f},{np.degrees(yaw):.1f}) deg\n"
            "  cascaded controller: outer position PD -> tilt, inner attitude PD -> mixer\n"
            "  API: takeoff / move_to / hover / land / get_base_pose / get_base_yaw"
        )

    # ------------------------------------------------------------------ #
    # Aerial behaviors
    # ------------------------------------------------------------------ #
    def takeoff(self, height: float = 0.5) -> bool:
        """Spin up and climb to ~height m altitude, then hold a stable hover."""
        pos, _ = self.get_base_pose()
        yaw = self.get_base_yaw()
        target = np.array([pos[0], pos[1], float(height)])
        # ramp up over ~3 s of sim time, then settle 1 s
        dt = self.model.opt.timestep
        climb_steps = max(int(3.0 / dt), 1)
        self._run(target, yaw, climb_steps)
        settle_steps = max(int(1.0 / dt), 1)
        self._run(target, yaw, settle_steps)
        z = self.get_base_height()
        ok = self._upright() and abs(z - height) < 0.12
        if not ok:
            print(f"[takeoff] height={z:.3f} target={height:.3f} upright={self._upright()}")
        return bool(ok)

    def hover(self, secs: float = 2.0) -> bool:
        """Station-keep at the current target pose for `secs` seconds."""
        dt = self.model.opt.timestep
        start = self.get_base_pose()[0].copy()
        self._run(self._target, self._yaw_des, max(int(secs / dt), 1))
        drift = np.linalg.norm(self.get_base_pose()[0] - start)
        ok = self._upright() and drift < 0.25
        if not ok:
            print(f"[hover] drift={drift:.3f} upright={self._upright()}")
        return bool(ok)

    def move_to(self, x: float, y: float, z: float, tol: float = 0.1) -> bool:
        """Fly to world target (x,y,z), closing the loop on get_base_pose."""
        target = np.array([float(x), float(y), float(z)])
        yaw = self._yaw_des
        dt = self.model.opt.timestep
        max_steps = max(int(8.0 / dt), 1)
        settle = max(int(0.5 / dt), 1)
        n = 0
        chunk = max(int(0.1 / dt), 1)
        while n < max_steps:
            self._run(target, yaw, chunk)
            n += chunk
            if not self._upright():
                print("[move_to] lost attitude (flipped)")
                return False
            err = np.linalg.norm(self.get_base_pose()[0] - target)
            if err < tol:
                break
        # let it settle and re-check
        self._run(target, yaw, settle)
        err = np.linalg.norm(self.get_base_pose()[0] - target)
        ok = self._upright() and err < tol
        if not ok:
            print(f"[move_to] err={err:.3f} tol={tol:.3f} upright={self._upright()}")
        return bool(ok)

    def land(self) -> bool:
        """Descend to just above the ground and idle the rotors."""
        pos, _ = self.get_base_pose()
        yaw = self._yaw_des
        target = np.array([pos[0], pos[1], 0.08])
        dt = self.model.opt.timestep
        self._run(target, yaw, max(int(4.0 / dt), 1))
        # idle rotors and let it rest
        for _ in range(max(int(0.5 / dt), 1)):
            self.data.ctrl[:] = 0.0
            mujoco.mj_step(self.model, self.data)
        z = self.get_base_height()
        ok = z < 0.2
        if not ok:
            print(f"[land] final z={z:.3f}")
        return bool(ok)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "mjcf.xml"
    r = Robot.build_from_mjcf(path)
    r.home()
    print(r.describe())
    print("takeoff:", r.takeoff(0.5))
    print("hover:", r.hover(1.5))
    print("move_to(0.6,0.4,0.8):", r.move_to(0.6, 0.4, 0.8))
    print("land:", r.land())
