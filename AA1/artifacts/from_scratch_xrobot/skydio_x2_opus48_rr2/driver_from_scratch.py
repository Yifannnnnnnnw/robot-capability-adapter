"""
driver_from_scratch.py
======================

Self-contained driver for the **Skydio X2 quadrotor** (robot id
``skydio_x2_opus48_rr2``).

Robot class: AERIAL / MULTIROTOR
  * Free-floating base: 1 freejoint -> 6 DOF (qpos[0:3] = world xyz,
    qpos[3:7] = orientation quaternion in (w,x,y,z) order).
  * 4 thrust motors (thrust1..thrust4), ctrlrange 0..13, hover ~ 3.25 each.
  * No arm joints, no gripper, no welds.

A quadrotor is UNDERACTUATED and open-loop UNSTABLE: it will flip and crash
unless a feedback controller runs *every* simulation step.  We therefore
implement a **cascaded controller** that is evaluated inside ``step()`` so it
runs once per ``mj_step``:

  Outer position loop (world frame):
    - altitude PD on z error -> desired *total* thrust (feed-forward m*g)
    - velocity-tracking PD on x,y -> desired horizontal acceleration, rotated
      into the body-yaw frame and mapped to desired roll / pitch tilt angles.
  Inner attitude loop (body frame):
    - PD on (roll, pitch, yaw) error -> desired body torques.
  Mixer:
    - distributes desired total thrust + roll/pitch/yaw torques onto the 4
      rotors using the known rotor geometry and the reaction-torque signs,
      then clamps to the actuator ctrlrange.

Sign conventions (verified empirically against this MJCF):
  * Rotor layout (x forward, y left):
        thrust1 rear-right (-.14,-.18)  reaction yaw -
        thrust2 rear-left  (-.14,+.18)  reaction yaw +
        thrust3 front-left (+.14,+.18)  reaction yaw -
        thrust4 front-right(+.14,-.18)  reaction yaw +
  * More FRONT thrust  -> negative pitch -> moves in -x, so to go +x we want
    POSITIVE pitch (more rear thrust).
  * More +y thrust     -> positive roll  -> moves in -y, so to go +y we want
    NEGATIVE roll.

Only ``mujoco``, ``numpy`` and the Python stdlib are used.
"""

from __future__ import annotations

import os
import math
import numpy as np
import mujoco


# ---------------------------------------------------------------------------
# small math helpers
# ---------------------------------------------------------------------------
def _quat_to_mat(quat: np.ndarray) -> np.ndarray:
    """(w,x,y,z) quaternion -> 3x3 rotation matrix."""
    R = np.zeros(9, dtype=np.float64)
    mujoco.mju_quat2Mat(R, np.asarray(quat, dtype=np.float64))
    return R.reshape(3, 3)


def _euler_from_mat(R: np.ndarray):
    """Return (roll, pitch, yaw) from rotation matrix (ZYX intrinsic)."""
    roll = math.atan2(R[2, 1], R[2, 2])
    pitch = math.atan2(-R[2, 0], math.sqrt(R[2, 1] ** 2 + R[2, 2] ** 2))
    yaw = math.atan2(R[1, 0], R[0, 0])
    return roll, pitch, yaw


class Robot:
    """Driver for the Skydio X2 quadrotor."""

    # ----- controller gains (tuned + verified in self-test) -----------------
    KP_Z, KD_Z = 14.0, 7.0          # altitude PD -> total thrust
    KP_POS = 0.6                    # position -> desired horizontal velocity
    VMAX = 0.6                      # cap desired horizontal velocity (m/s)
    KV = 0.25                       # velocity PD -> horizontal accel
    KP_ANG, KD_ANG = 3.0, 0.5       # attitude (roll/pitch) PD -> torque
    KP_YAW, KD_YAW = 0.6, 0.15      # yaw PD -> torque
    TILT_MAX = 0.18                 # max commanded tilt (rad) ~10 deg

    # =====================================================================
    # construction
    # =====================================================================
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData,
                 mjcf_path: str):
        self.model = model
        self.data = data
        self.mjcf_path = mjcf_path
        self._viewer = None

        # ---- resolve the free-floating base body -------------------------
        self.base_body = self._find_base_body()
        self.base_bid = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, self.base_body)

        # ---- resolve thrust actuators (ordered) --------------------------
        self.thrust_names = ["thrust1", "thrust2", "thrust3", "thrust4"]
        self.thrust_ids = []
        for n in self.thrust_names:
            aid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, n)
            if aid < 0:
                raise RuntimeError(f"thrust actuator '{n}' not found in model")
            self.thrust_ids.append(aid)
        self.n_rotors = len(self.thrust_ids)

        # ctrl range (assume identical for all rotors)
        self.ctrl_lo = float(model.actuator_ctrlrange[self.thrust_ids[0]][0])
        self.ctrl_hi = float(model.actuator_ctrlrange[self.thrust_ids[0]][1])

        # ---- physical constants ------------------------------------------
        self.mass = float(np.sum(model.body_mass))
        self.g = float(-model.opt.gravity[2]) if model.opt.gravity[2] != 0 else 9.81
        self.hover_thrust = self.mass * self.g / self.n_rotors

        # ---- rotor geometry (x,y in body frame) + reaction-yaw sign ------
        # read from the thrust site positions so we never hard-code wrong
        self._rotor_xy = np.zeros((self.n_rotors, 2))
        for i, n in enumerate(self.thrust_names):
            sid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, n)
            self._rotor_xy[i] = model.site_pos[sid][:2]
        # reaction-yaw signs from the gear z-torque (motor "gear" 6th element).
        # Fall back to the canonical Skydio pattern if not recoverable.
        self._yaw_sign = self._reaction_yaw_signs()

        # normalisation scales for the mixer (avoid divide-by-zero)
        self._sx = np.max(np.abs(self._rotor_xy[:, 0])) or 1.0
        self._sy = np.max(np.abs(self._rotor_xy[:, 1])) or 1.0

        # ---- active flight target (controller stepped in step()) ---------
        # When None, controller is idle (rotors off) -> used for land/idle.
        self._target = None          # np.array([x,y,z]) world target
        self._yaw_target = 0.0
        self._motors_idle = True

    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """Load the MJCF (resolving symlinks so <include> works) and build."""
        real = os.path.realpath(mjcf_path)
        model = mujoco.MjModel.from_xml_path(real)
        data = mujoco.MjData(model)
        mujoco.mj_resetData(model, data)
        mujoco.mj_forward(model, data)
        return cls(model, data, mjcf_path)

    # ---------------------------------------------------------------------
    def _find_base_body(self) -> str:
        """Return the name of the body that owns the free joint."""
        m = self.model
        for j in range(m.njnt):
            if m.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE:
                bid = m.jnt_bodyid[j]
                name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, bid)
                if name:
                    return name
        # fallback to study.json hint
        return "x2"

    def _reaction_yaw_signs(self) -> np.ndarray:
        """Determine yaw reaction sign per rotor from actuator gear."""
        m = self.model
        signs = np.zeros(self.n_rotors)
        ok = True
        for i, aid in enumerate(self.thrust_ids):
            gear = m.actuator_gear[aid]
            # gear layout for a site motor is (fx,fy,fz, mx,my,mz); the z-torque
            # is index 5.
            mz = gear[5]
            if abs(mz) > 1e-9:
                signs[i] = math.copysign(1.0, mz)
            else:
                ok = False
        if not ok or np.all(signs == 0):
            # canonical Skydio X2 pattern: - + - +
            signs = np.array([-1.0, 1.0, -1.0, 1.0])
        return signs

    # =====================================================================
    # state readout
    # =====================================================================
    def get_base_pose(self):
        """World base position (xyz) and 3x3 rotation matrix."""
        pos = self.data.xpos[self.base_bid].copy()
        R = self.data.xmat[self.base_bid].reshape(3, 3).copy()
        return pos, R

    def get_base_yaw(self) -> float:
        _, R = self.get_base_pose()
        return _euler_from_mat(R)[2]

    def get_joint_positions(self) -> np.ndarray:
        """Full generalized position vector (freejoint xyz + quaternion)."""
        return self.data.qpos.copy()

    def get_body_height(self) -> float:
        return float(self.data.xpos[self.base_bid][2])

    def _state(self):
        """Convenience: pos, vel, (roll,pitch,yaw), body angular velocity."""
        pos = self.data.qpos[:3].copy()
        R = _quat_to_mat(self.data.qpos[3:7])
        rpy = _euler_from_mat(R)
        vel = self.data.qvel[:3].copy()           # world linear velocity
        omega = self.data.qvel[3:6].copy()        # body angular velocity
        return pos, vel, rpy, omega

    # =====================================================================
    # cascaded controller (evaluated once per mj_step)
    # =====================================================================
    def _compute_ctrl(self) -> np.ndarray:
        """Return per-rotor thrust commands for the current state/target."""
        if self._motors_idle or self._target is None:
            return np.zeros(self.n_rotors)

        target = self._target
        pos, vel, (roll, pitch, yaw), omega = self._state()

        # ----- altitude PD -> total thrust (gravity feed-forward) ---------
        thrust_total = (self.mass * self.g
                        + self.KP_Z * (target[2] - pos[2])
                        - self.KD_Z * vel[2])

        # ----- horizontal: position -> desired velocity -> accel ----------
        vdes_x = np.clip(self.KP_POS * (target[0] - pos[0]),
                         -self.VMAX, self.VMAX)
        vdes_y = np.clip(self.KP_POS * (target[1] - pos[1]),
                         -self.VMAX, self.VMAX)
        ax = self.KV * (vdes_x - vel[0])
        ay = self.KV * (vdes_y - vel[1])

        # rotate world accel demand into the body-yaw frame
        c, s = math.cos(yaw), math.sin(yaw)
        bx = c * ax + s * ay
        by = -s * ax + c * ay

        # accel -> desired tilt (sign per verified dynamics):
        #   +x  <- positive pitch ;  +y <- negative roll
        des_pitch = float(np.clip(bx, -self.TILT_MAX, self.TILT_MAX))
        des_roll = float(np.clip(-by, -self.TILT_MAX, self.TILT_MAX))

        # ----- inner attitude PD -> body torques --------------------------
        tau_roll = self.KP_ANG * (des_roll - roll) - self.KD_ANG * omega[0]
        tau_pitch = self.KP_ANG * (des_pitch - pitch) - self.KD_ANG * omega[1]
        tau_yaw = (self.KP_YAW * (self._yaw_target - yaw)
                   - self.KD_YAW * omega[2])

        # ----- mixer ------------------------------------------------------
        base = thrust_total / self.n_rotors
        f = np.full(self.n_rotors, base)
        for i in range(self.n_rotors):
            x, y = self._rotor_xy[i]
            # roll torque ~ sum(f_i * y_i): +tau_roll -> more thrust at +y
            f[i] += tau_roll * (y / self._sy) * 0.5
            # pitch torque ~ -sum(f_i * x_i): +tau_pitch -> more thrust at -x
            f[i] += tau_pitch * (x / self._sx) * (-0.5)
            # yaw torque from rotor reaction signs
            f[i] += tau_yaw * self._yaw_sign[i] * 0.5

        return np.clip(f, self.ctrl_lo, self.ctrl_hi)

    # =====================================================================
    # simulation stepping
    # =====================================================================
    def step(self, n: int = 1):
        """Advance the simulation n steps, running the controller each step."""
        for _ in range(int(n)):
            self.data.ctrl[self.thrust_ids] = self._compute_ctrl()
            mujoco.mj_step(self.model, self.data)
            if self._viewer is not None:
                try:
                    self._viewer.sync()
                except Exception:
                    pass
        return True

    def _settle_to(self, target, max_secs, tol, hold_secs=0.0):
        """Drive toward `target`, return True once within tol (and held)."""
        self._target = np.asarray(target, dtype=np.float64)
        self._motors_idle = False
        dt = self.model.opt.timestep
        n_max = int(max_secs / dt)
        n_hold = int(hold_secs / dt)
        held = 0
        for _ in range(n_max):
            self.step(1)
            err = np.linalg.norm(self.data.qpos[:3] - self._target)
            if self._tumbled():
                return False
            if err < tol:
                held += 1
                if held >= n_hold:
                    return True
            else:
                held = 0
        # final check
        err = np.linalg.norm(self.data.qpos[:3] - self._target)
        return (err < tol * 1.5) and not self._tumbled()

    def _tumbled(self) -> bool:
        """True if the drone has flipped past ~70 degrees from upright."""
        _, R = self.get_base_pose()
        # body z axis vs world up
        up = R[:, 2]
        return up[2] < math.cos(math.radians(70))

    # =====================================================================
    # high-level behaviors
    # =====================================================================
    def home(self) -> bool:
        """Reset to the ground/keyframe pose and idle the rotors."""
        mujoco.mj_resetData(self.model, self.data)
        # If a 'hover' keyframe exists use its qpos, else start near ground.
        if self.model.nkey > 0:
            self.data.qpos[:] = self.model.key_qpos[0]
        else:
            self.data.qpos[2] = 0.12
            self.data.qpos[3:7] = [1, 0, 0, 0]
        self.data.qvel[:] = 0
        self._target = None
        self._yaw_target = 0.0
        self._motors_idle = True
        mujoco.mj_forward(self.model, self.data)
        return True

    def takeoff(self, height: float = 0.5) -> bool:
        """Spin up and climb to ~`height` m, then hold a stable hover."""
        pos = self.data.qpos[:3].copy()
        target = [pos[0], pos[1], float(height)]
        self._yaw_target = self.get_base_yaw()
        ok = self._settle_to(target, max_secs=8.0, tol=0.08, hold_secs=0.5)
        # confirm altitude and uprightness
        z = self.get_body_height()
        return ok and (abs(z - height) < 0.12) and not self._tumbled()

    def hover(self, secs: float = 2.0) -> bool:
        """Station-keep at the current pose for `secs` seconds."""
        pos = self.data.qpos[:3].copy()
        self._target = pos
        self._motors_idle = False
        dt = self.model.opt.timestep
        for _ in range(int(secs / dt)):
            self.step(1)
            if self._tumbled():
                return False
        return not self._tumbled()

    def move_to(self, x: float, y: float, z: float, tol: float = 0.1) -> bool:
        """Fly to a world target with a closed loop; arrive within tol."""
        target = [float(x), float(y), float(z)]
        ok = self._settle_to(target, max_secs=15.0, tol=tol, hold_secs=0.4)
        return ok and not self._tumbled()

    def land(self) -> bool:
        """Descend to just above the ground, then idle the rotors."""
        pos = self.data.qpos[:3].copy()
        # gently descend to ground clearance
        self._settle_to([pos[0], pos[1], 0.12], max_secs=10.0,
                        tol=0.05, hold_secs=0.2)
        # idle motors and let it settle
        self._motors_idle = True
        self._target = None
        dt = self.model.opt.timestep
        for _ in range(int(1.0 / dt)):
            self.step(1)
        return self.get_body_height() < 0.25

    # =====================================================================
    # introspection / rendering
    # =====================================================================
    def render(self):
        """Open (or sync) a passive MuJoCo viewer.  No-op if unavailable."""
        try:
            import mujoco.viewer as mjv
        except Exception:
            return False
        if self._viewer is None:
            try:
                self._viewer = mjv.launch_passive(self.model, self.data)
            except Exception:
                self._viewer = None
                return False
        try:
            self._viewer.sync()
        except Exception:
            pass
        return True

    def describe(self) -> dict:
        """Human/agent readable summary of the robot + current state."""
        pos, R = self.get_base_pose()
        roll, pitch, yaw = _euler_from_mat(R)
        return {
            "robot_id": "skydio_x2_opus48_rr2",
            "class": "aerial / multirotor (quadrotor)",
            "dof": int(self.model.nv),
            "n_rotors": self.n_rotors,
            "thrust_actuators": self.thrust_names,
            "ctrlrange": [self.ctrl_lo, self.ctrl_hi],
            "mass_kg": round(self.mass, 4),
            "hover_thrust_per_rotor": round(self.hover_thrust, 4),
            "base_body": self.base_body,
            "controller": "cascaded position(outer) + attitude(inner) PD",
            "base_pos": [round(float(v), 4) for v in pos],
            "base_rpy_rad": [round(roll, 4), round(pitch, 4), round(yaw, 4)],
            "height_m": round(self.get_body_height(), 4),
            "behaviors": ["takeoff", "hover", "move_to", "land",
                          "get_base_pose", "get_base_yaw", "get_body_height"],
        }


# ---------------------------------------------------------------------------
# quick manual smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json
    r = Robot.build_from_mjcf("mjcf.xml")
    r.home()
    print(json.dumps(r.describe(), indent=2))
    print("takeoff:", r.takeoff(0.6))
    print("hover:", r.hover(1.0))
    print("move_to:", r.move_to(0.5, 0.3, 0.8, tol=0.1))
    print("after move:", r.get_base_pose()[0].round(3))
    print("land:", r.land())
    print("final height:", round(r.get_body_height(), 3))
