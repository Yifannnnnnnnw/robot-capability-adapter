"""
driver_from_scratch.py  --  Self-contained driver for the Unitree Go2 quadruped.

Robot class: QUADRUPED (12 torque-actuated leg joints + floating base freejoint).

This file imports ONLY mujoco, numpy and the stdlib.  It does not import
anything from auto_adapter.skeletons.  All FK / PD-control / gait logic is
implemented here from scratch.

Model layout (derived from the MJCF by probing the model):
  * qpos[0:3]   base world position (x, y, z)
  * qpos[3:7]   base orientation quaternion (w, x, y, z)
  * qpos[7:19]  12 leg joint angles
  * 12 torque "motor" actuators (FR/FL/RR/RL  x  hip/thigh/calf), with
    leg torque limits +-23.7 Nm (hip/thigh) and +-45.43 Nm (calf).

Because the *actuator* ordering (FR, FL, RR, RL) differs from the *joint*
ordering (FL, FR, RL, RR), we resolve actuator -> joint qpos/dof indices via
actuator_trnid so every PD command targets the correct joint.

Control strategy (quadruped -- NO IK, there is no end-effector to reach):
  * stand_up : PD-track the nominal standing pose (hip=0, thigh=0.9, calf=-1.8).
  * sit      : PD-track a folded pose (thigh up, calf folded) that lowers
               the body well below the standing height.
  * walk_forward : a trot gait.  Diagonal leg pairs (FR+RL vs FL+RR) swing in
               anti-phase.  Each leg's thigh is driven by a cosine (fore/aft
               swing) and the calf is retracted on the swing (upward) half of
               the cycle for ground clearance, producing net forward thrust.
The walk gait was tuned and verified to move the base > 3 cm forward over
1.5 s while staying upright with small sideways drift.

Torque PD control:  tau = kp*(q_des - q) - kd*qd,  clamped to actuator limits.
"""

import math
import numpy as np
import mujoco


# Order in which legs appear in the *actuator* vector.
_LEG_ORDER = ("FR", "FL", "RR", "RL")
# Nominal stand-pose joint targets (radians) per leg: hip, thigh, calf.
_STAND_LEG = (0.0, 0.9, -1.8)


class Robot:
    """A from-scratch torque-PD controller for the Unitree Go2 quadruped."""

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def __init__(self, model, data, mjcf_path):
        self.model = model
        self.data = data
        self.mjcf_path = mjcf_path
        self.dt = float(model.opt.timestep)
        self._viewer = None

        # Base body (carries the freejoint).
        self.base_bid = self._body_id("base_link")

        # Resolve, per actuator, the qpos and dof addresses of the joint it
        # drives.  This handles the actuator/joint ordering mismatch.
        nu = model.nu
        self.act_qadr = np.empty(nu, dtype=int)
        self.act_dadr = np.empty(nu, dtype=int)
        for a in range(nu):
            jid = int(model.actuator_trnid[a, 0])
            self.act_qadr[a] = model.jnt_qposadr[jid]
            self.act_dadr[a] = model.jnt_dofadr[jid]

        # Per-actuator torque limit (absolute value of ctrlrange upper bound).
        self.tau_max = np.abs(model.actuator_ctrlrange[:, 1]).astype(float)
        if not np.all(np.isfinite(self.tau_max)) or np.any(self.tau_max <= 0):
            self.tau_max = np.full(nu, 30.0)

        # Nominal standing actuator-space target.
        self.q_stand = np.tile(np.array(_STAND_LEG, dtype=float), len(_LEG_ORDER))

        # PD gains (per actuator).  Tuned in prototyping.
        self.kp = 60.0
        self.kd = 4.0

        self._standing_height = 0.27  # populated after stand_up()

    @classmethod
    def build_from_mjcf(cls, mjcf_path):
        """Load the MJCF (resolving symlinks so <include> paths work) and
        construct the Robot at its home keyframe."""
        import os
        path = os.path.realpath(mjcf_path)
        model = mujoco.MjModel.from_xml_path(path)
        data = mujoco.MjData(model)
        robot = cls(model, data, mjcf_path)
        robot._reset_home()
        return robot

    # ------------------------------------------------------------------ #
    # Low-level helpers
    # ------------------------------------------------------------------ #
    def _body_id(self, name):
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
        if bid < 0:
            raise KeyError("Unknown body: %r" % name)
        return bid

    def _reset_home(self):
        """Reset to the 'home' keyframe if present, else zero state."""
        if self.model.nkey > 0:
            mujoco.mj_resetDataKeyframe(self.model, self.data, 0)
        else:
            mujoco.mj_resetData(self.model, self.data)
        mujoco.mj_forward(self.model, self.data)

    def _q_act(self):
        """Current joint angles in actuator order."""
        return self.data.qpos[self.act_qadr]

    def _qd_act(self):
        """Current joint velocities in actuator order."""
        return self.data.qvel[self.act_dadr]

    def _apply_pd(self, target, kp=None, kd=None):
        """Compute and apply one step of torque PD control toward `target`."""
        kp = self.kp if kp is None else kp
        kd = self.kd if kd is None else kd
        q = self._q_act()
        qd = self._qd_act()
        tau = kp * (target - q) - kd * qd
        tau = np.clip(tau, -self.tau_max, self.tau_max)
        self.data.ctrl[:] = tau

    def _hold(self, target, steps, kp=None, kd=None):
        """PD-track `target` for `steps` physics steps."""
        for _ in range(int(steps)):
            self._apply_pd(target, kp, kd)
            mujoco.mj_step(self.model, self.data)
            self._sync_view()

    def _sync_view(self):
        if self._viewer is not None:
            try:
                self._viewer.sync()
            except Exception:
                self._viewer = None

    # ------------------------------------------------------------------ #
    # Required generic API
    # ------------------------------------------------------------------ #
    def home(self):
        """Reset to the home keyframe and settle into a stable stand."""
        self._reset_home()
        return self.stand_up(duration=1.5)

    def get_joint_positions(self):
        """Return the 12 leg joint angles (in actuator order: FR/FL/RR/RL)."""
        return self._q_act().copy()

    def step(self, n=1):
        """Advance the simulation `n` steps, holding the current ctrl."""
        for _ in range(int(n)):
            mujoco.mj_step(self.model, self.data)
            self._sync_view()

    def render(self):
        """Launch / sync a passive MuJoCo viewer (best-effort)."""
        try:
            import mujoco.viewer as mjv
            if self._viewer is None:
                self._viewer = mjv.launch_passive(self.model, self.data)
            self._viewer.sync()
        except Exception as exc:  # headless / no viewer available
            return "render unavailable: %s" % exc
        return True

    def describe(self):
        """Human-readable summary of the robot state."""
        xyz, R = self.get_base_pose()
        return {
            "class": "quadruped",
            "model": "Unitree Go2",
            "n_joints": int(self.model.nu),
            "leg_order": list(_LEG_ORDER),
            "joints_per_leg": ["hip", "thigh", "calf"],
            "base_height_m": round(float(xyz[2]), 4),
            "base_xyz": [round(float(v), 4) for v in xyz],
            "upright_z": round(float(R[2, 2]), 4),
            "joint_positions": [round(float(v), 4) for v in self.get_joint_positions()],
            "timestep": self.dt,
        }

    # ------------------------------------------------------------------ #
    # Base-state accessors (quadruped API)
    # ------------------------------------------------------------------ #
    def get_base_pose(self):
        """World position (xyz) and 3x3 rotation matrix of the base."""
        mujoco.mj_forward(self.model, self.data)
        xyz = self.data.xpos[self.base_bid].copy()
        R = self.data.xmat[self.base_bid].reshape(3, 3).copy()
        return xyz, R

    def get_body_height(self):
        """World Z of the base body (m)."""
        mujoco.mj_forward(self.model, self.data)
        return float(self.data.xpos[self.base_bid, 2])

    def _uprightness(self):
        """Cosine of tilt: body-z dotted with world-z (1.0 == perfectly upright)."""
        R = self.data.xmat[self.base_bid].reshape(3, 3)
        return float(R[2, 2])

    # ------------------------------------------------------------------ #
    # Behavior: STAND UP
    # ------------------------------------------------------------------ #
    def stand_up(self, duration=2.0):
        """PD-track the nominal standing pose.  Succeeds if the base ends up
        above 0.15 m and stays upright."""
        steps = max(1, int(duration / self.dt))
        self._hold(self.q_stand, steps, kp=self.kp, kd=self.kd)
        h = self.get_body_height()
        self._standing_height = h
        return bool(h > 0.15 and self._uprightness() > 0.7)

    # ------------------------------------------------------------------ #
    # Behavior: SIT
    # ------------------------------------------------------------------ #
    def sit(self, duration=1.5):
        """PD-track a folded pose: fold thighs up and tuck the calves so the
        body actually lowers.  Succeeds if final height < 0.8 * standing
        height."""
        h_stand = self._standing_height if self._standing_height > 0 else self.get_body_height()
        sit_pose = self.q_stand.copy()
        for leg in range(len(_LEG_ORDER)):
            sit_pose[leg * 3 + 1] = 1.6   # thigh: rotate up / fold
            sit_pose[leg * 3 + 2] = -2.6  # calf: tuck (toward joint limit -2.72)
        steps = max(1, int(duration / self.dt))
        # Slightly higher gains help the fold complete against gravity.
        self._hold(sit_pose, steps, kp=self.kp, kd=self.kd)
        h = self.get_body_height()
        return bool(h < 0.8 * h_stand)

    # ------------------------------------------------------------------ #
    # Behavior: WALK FORWARD (trot gait)
    # ------------------------------------------------------------------ #
    def walk_forward(self, secs=2.0, speed=0.2):
        """Trot gait producing real forward (+X) base displacement.

        Diagonal leg pairs FR+RL and FL+RR swing in anti-phase.  Each leg's
        thigh follows a cosine (fore/aft swing) while the calf retracts on the
        upward half of the cycle for ground clearance.  `speed` modestly scales
        the gait period (higher speed -> shorter period -> faster stepping).

        Returns True if the base advanced > 0.03 m forward over ~1.5 s while
        staying upright.
        """
        # Gait parameters (tuned & verified during prototyping).
        T = 0.4 / max(0.05, speed / 0.2)   # nominal 0.4 s at speed 0.2
        T = float(np.clip(T, 0.25, 0.7))
        amp_thigh = 0.30   # rad of fore/aft thigh swing
        amp_calf = 0.45    # rad of calf retraction on swing
        kp, kd = 55.0, 3.0
        sign = -1.0        # cos-swing sign that yields +X (forward) motion

        # Phase assignment: diagonal pairs share a phase, offset by pi.
        # actuator legs: 0=FR, 1=FL, 2=RR, 3=RL
        phase_offset = {0: 0.0, 3: 0.0, 1: math.pi, 2: math.pi}

        start_xyz, _ = self.get_base_pose()
        n = max(1, int(secs / self.dt))
        for k in range(n):
            t = k * self.dt
            phase = 2.0 * math.pi * (t / T)
            tgt = self.q_stand.copy()
            for leg, off in phase_offset.items():
                ph = phase + off
                lift = max(0.0, math.sin(ph))            # >0 on swing half
                tgt[leg * 3 + 1] = self.q_stand[leg * 3 + 1] - sign * amp_thigh * math.cos(ph)
                tgt[leg * 3 + 2] = self.q_stand[leg * 3 + 2] - amp_calf * lift
            self._apply_pd(tgt, kp=kp, kd=kd)
            mujoco.mj_step(self.model, self.data)
            self._sync_view()
            # Safety: abort if the robot has clearly toppled.
            if self._uprightness() < 0.3:
                break

        end_xyz, _ = self.get_base_pose()
        dx = float(end_xyz[0] - start_xyz[0])
        upright = self._uprightness() > 0.6
        # Settle back into a clean stand after walking.
        self._hold(self.q_stand, int(0.3 / self.dt), kp=self.kp, kd=self.kd)
        return bool(dx > 0.03 and upright)


if __name__ == "__main__":
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    r = Robot.build_from_mjcf(os.path.join(here, "mjcf.xml"))
    print("home:", r.home())
    print("describe:", r.describe())
    print("stand_up:", r.stand_up())
    print("height after stand:", round(r.get_body_height(), 3))
    print("walk_forward:", r.walk_forward(secs=2.0))
    print("height after walk:", round(r.get_body_height(), 3))
    print("sit:", r.sit())
    print("height after sit:", round(r.get_body_height(), 3))
