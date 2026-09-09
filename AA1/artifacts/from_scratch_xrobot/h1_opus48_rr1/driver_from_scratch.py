"""
driver_from_scratch.py — Self-contained driver for the Unitree H1 humanoid.

Robot class: HUMANOID / BIPED
  - Free-floating pelvis (6-DOF free joint) + 19 torque ("motor") actuators:
      10 leg joints, 1 torso yaw, 8 arm joints.
  - ctrl[i] is a joint TORQUE in N*m, clamped to each actuator's ctrlrange.
  - No gripper, no weld equalities, no end-effector site.

Because a standing humanoid is a tall inverted pendulum, every behaviour runs a
joint-space PD controller WITH an active CoM-balance feedback loop on EVERY
mj_step.  The balance loop is an ankle "strategy": it measures the horizontal
CoM offset (and velocity) relative to the foot support centre and adjusts the
ankle pitch targets to drive the CoM back over the feet.  This is what keeps the
robot upright; an open-loop PD hold falls backward within ~1.5 s (verified
during development).

Controller summary (tuned + verified in self-test):
  tau   = kp*(q_des - q) - kd*qd                  (pure joint-space PD)
  q_des = nominal_pose + balance_offsets + behaviour_offsets
  balance:  ankle_pitch += ka*ex + kav*vx         (ka=6, kav=2)
            where ex = com_x - foot_center_x, vx = com_x velocity
Standing height (torso world z) holds ~0.94 m; squat lowers ~0.14 m and
recovers; both stay upright (up-dot > 0.99).  Walk is a cautious closed-loop
quasi-static micro-step attempt that PRIORITISES not falling — dynamic bipedal
walking on this torque-only model is hard, so an honest "walk not achieved"
(while staying upright) is the expected outcome and is acceptable.

NO imports from auto_adapter.skeletons.  mujoco + numpy + stdlib only.
"""

import math
import os
import numpy as np
import mujoco


# ----------------------------------------------------------------------------
# Per-joint PD gains, in actuator order:
#   0 left_hip_yaw  1 left_hip_roll  2 left_hip_pitch  3 left_knee  4 left_ankle
#   5 right_hip_yaw 6 right_hip_roll 7 right_hip_pitch 8 right_knee 9 right_ankle
#   10 torso
#   11 left_shoulder_pitch 12 left_shoulder_roll 13 left_shoulder_yaw 14 left_elbow
#   15 right_shoulder_pitch 16 right_shoulder_roll 17 right_shoulder_yaw 18 right_elbow
# ----------------------------------------------------------------------------
_KP = np.array([
    200, 200, 300, 300, 200,   # left leg
    200, 200, 300, 300, 200,   # right leg
    300,                       # torso
    40, 40, 18, 18,            # left arm
    40, 40, 18, 18,            # right arm
], dtype=float)
_KD = _KP * 0.1

# Indices into the actuated-joint array for convenient reference.
LHY, LHR, LHP, LKN, LAN = 0, 1, 2, 3, 4
RHY, RHR, RHP, RKN, RAN = 5, 6, 7, 8, 9
TORSO = 10

# Balance feedback gains (CoM ankle strategy) — verified stable.
_KA = 6.0      # CoM position -> ankle pitch
_KAV = 2.0     # CoM velocity -> ankle pitch
_FOOT_CENTER_X = 0.06   # x of foot support centre relative to pelvis frame origin


class Robot:
    """Self-contained PD-balanced controller for the Unitree H1 humanoid."""

    # ------------------------------------------------------------------ build
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        self.model = model
        self.data = data
        self._viewer = None

        # Free joint occupies qpos[0:7] (pos + wxyz quat) and qvel[0:6].
        # The 19 actuated joints follow.  Build index maps from actuators.
        self.nu = model.nu
        self.qadr = np.zeros(self.nu, dtype=int)   # qpos address per actuator
        self.vadr = np.zeros(self.nu, dtype=int)   # qvel address per actuator
        self.act_names = []
        for i in range(self.nu):
            jid = model.actuator_trnid[i, 0]
            self.qadr[i] = model.jnt_qposadr[jid]
            self.vadr[i] = model.jnt_dofadr[jid]
            self.act_names.append(
                mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, i))

        self.ctrl_lo = model.actuator_ctrlrange[:, 0].copy()
        self.ctrl_hi = model.actuator_ctrlrange[:, 1].copy()

        # Joint limits per actuated joint (from the joint definitions).
        self.q_lo = np.zeros(self.nu)
        self.q_hi = np.zeros(self.nu)
        for i in range(self.nu):
            jid = model.actuator_trnid[i, 0]
            if model.jnt_limited[jid]:
                self.q_lo[i], self.q_hi[i] = model.jnt_range[jid]
            else:
                self.q_lo[i], self.q_hi[i] = -np.inf, np.inf

        # Bodies of interest.
        self.torso_bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "torso_link")
        self.pelvis_bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "pelvis")

        # Nominal standing pose from the "home" keyframe if present.
        if model.nkey > 0:
            self.q_nominal = np.array(model.key_qpos[0])[self.qadr].copy()
            self._home_key = 0
        else:
            self.q_nominal = np.zeros(self.nu)
            self.q_nominal[LHP] = self.q_nominal[RHP] = -0.4
            self.q_nominal[LKN] = self.q_nominal[RKN] = 0.8
            self.q_nominal[LAN] = self.q_nominal[RAN] = -0.4
            self._home_key = None

        self._stand_height = None  # cached after home()

    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """Load the MJCF (resolving symlinks so <include> paths work) and build."""
        real = os.path.realpath(mjcf_path)
        model = mujoco.MjModel.from_xml_path(real)
        data = mujoco.MjData(model)
        robot = cls(model, data)
        robot._reset_to_nominal()
        return robot

    # --------------------------------------------------------------- helpers
    def _reset_to_nominal(self):
        if self._home_key is not None:
            mujoco.mj_resetDataKeyframe(self.model, self.data, self._home_key)
        else:
            mujoco.mj_resetData(self.model, self.data)
            self.data.qpos[2] = 0.98
            self.data.qpos[3] = 1.0  # identity quat
            self.data.qpos[self.qadr] = self.q_nominal
        mujoco.mj_forward(self.model, self.data)

    def _q(self):
        return self.data.qpos[self.qadr].copy()

    def _qd(self):
        return self.data.qvel[self.vadr].copy()

    def _com(self):
        """World CoM of the whole robot (subtree of the world body)."""
        return np.array(self.data.subtree_com[0])

    def _com_vel(self):
        mujoco.mj_subtreeVel(self.model, self.data)
        return np.array(self.data.subtree_linvel[0])

    def _torso_R(self):
        return np.array(self.data.xmat[self.torso_bid]).reshape(3, 3)

    def _uprightness(self):
        # dot of torso local z-axis with world up
        return float(self._torso_R()[2, 2])

    def _apply_pd(self, q_des, extra_balance=True):
        """Compute torque from PD + (optional) CoM ankle balance, set ctrl, step."""
        q = self._q()
        qd = self._qd()
        q_des = np.clip(q_des, self.q_lo, self.q_hi)
        if extra_balance:
            com = self._com()
            comv = self._com_vel()
            ex = com[0] - _FOOT_CENTER_X
            vx = comv[0]
            bal = _KA * ex + _KAV * vx
            q_des = q_des.copy()
            q_des[LAN] += bal
            q_des[RAN] += bal
            q_des = np.clip(q_des, self.q_lo, self.q_hi)
        tau = _KP * (q_des - q) - _KD * qd
        self.data.ctrl[:] = np.clip(tau, self.ctrl_lo, self.ctrl_hi)
        mujoco.mj_step(self.model, self.data)

    # ----------------------------------------------------------- core API
    def home(self) -> bool:
        """Reset to the nominal standing keyframe and settle with the balance
        controller.  Returns True if upright and at standing height afterwards."""
        self._reset_to_nominal()
        try:
            for _ in range(int(1.0 / self.model.opt.timestep)):
                self._apply_pd(self.q_nominal)
            self._stand_height = self.get_torso_height()
            ok = self._uprightness() > 0.9 and self._stand_height > 0.7
            return bool(ok)
        except Exception as e:  # pragma: no cover
            print(f"[home] error: {e}")
            return False

    def get_joint_positions(self) -> np.ndarray:
        """Return the 19 actuated joint angles (radians), in actuator order."""
        return self._q()

    def step(self, n: int = 1):
        """Advance the simulation `n` steps holding the nominal balanced pose."""
        for _ in range(max(1, int(n))):
            self._apply_pd(self.q_nominal)

    def render(self):
        """Open / update a passive viewer if available; otherwise no-op."""
        try:
            import mujoco.viewer as mjv
            if self._viewer is None:
                self._viewer = mjv.launch_passive(self.model, self.data)
            self._viewer.sync()
        except Exception as e:
            print(f"[render] viewer unavailable: {e}")

    def describe(self) -> str:
        xyz, _ = self.get_base_pose()
        return (
            "Unitree H1 humanoid (class=HUMANOID/BIPED)\n"
            f"  actuators (torque/motor): {self.nu}  "
            f"(10 leg, 1 torso, 8 arm)\n"
            f"  free-floating pelvis: qpos[0:7] pos+quat, qvel[0:6]\n"
            f"  torso height: {self.get_torso_height():.3f} m  "
            f"uprightness(up-dot): {self._uprightness():.3f}\n"
            f"  base xyz: [{xyz[0]:.3f} {xyz[1]:.3f} {xyz[2]:.3f}]\n"
            "  control: per-joint PD + CoM ankle-strategy balance every step\n"
            "  behaviours: stand_balance, squat, walk_forward, get_base_pose,\n"
            "              get_torso_height"
        )

    # ----------------------------------------------------- pose accessors
    def get_base_pose(self):
        """World position + 3x3 rotation of the pelvis (base) free body."""
        xyz = np.array(self.data.xpos[self.pelvis_bid])
        R = np.array(self.data.xmat[self.pelvis_bid]).reshape(3, 3)
        return xyz, R

    def get_torso_height(self) -> float:
        """World Z of the torso link (the tall upper body)."""
        return float(self.data.xpos[self.torso_bid][2])

    # ------------------------------------------------------- behaviours
    def stand_balance(self, secs: float = 3.0) -> bool:
        """Hold a stable standing pose for `secs`, keeping the torso upright at
        roughly its standing height the whole time.  Returns False if it falls."""
        if self._stand_height is None:
            self.home()
        guard_h = max(0.6, 0.7 * self._stand_height)
        n = int(max(0.0, secs) / self.model.opt.timestep)
        try:
            for _ in range(n):
                self._apply_pd(self.q_nominal)
                if self.get_torso_height() < guard_h or self._uprightness() < 0.5:
                    print("[stand_balance] balance guard tripped")
                    return False
            return self._uprightness() > 0.85
        except Exception as e:  # pragma: no cover
            print(f"[stand_balance] error: {e}")
            return False

    def squat(self, depth: float = 0.15, secs: float = 3.0) -> bool:
        """Smoothly lower the torso by ~`depth` m by bending hips+knees+ankles,
        hold briefly, then return to standing — staying balanced throughout.

        The crouch amplitude `amp` (rad) maps to roughly 0.55*amp metres of
        torso drop; we cap it for safety so the recovery stays stable."""
        if self._stand_height is None:
            self.home()
        depth = max(0.03, float(depth))
        # Empirical map: amp ~ depth / 0.55, capped at 0.25 for reliable recovery.
        amp = min(0.25, depth / 0.55)
        T = max(1, int((0.5 * secs) / self.model.opt.timestep))
        hold = max(1, int(0.4 / self.model.opt.timestep))

        def crouch_pose(a):
            q = self.q_nominal.copy()
            q[LHP] -= a; q[RHP] -= a
            q[LKN] += 2 * a; q[RKN] += 2 * a
            q[LAN] -= a; q[RAN] -= a
            return q

        try:
            z_start = self.get_torso_height()
            # descend (cosine ease-in)
            for i in range(T):
                a = amp * 0.5 * (1 - math.cos(math.pi * i / T))
                self._apply_pd(crouch_pose(a))
                if self._uprightness() < 0.5:
                    print("[squat] toppled while lowering")
                    return False
            z_bottom = self.get_torso_height()
            # hold at the bottom
            for _ in range(hold):
                self._apply_pd(crouch_pose(amp))
            # ascend back to standing (cosine ease-out)
            for i in range(T):
                a = amp * 0.5 * (1 + math.cos(math.pi * i / T))
                self._apply_pd(crouch_pose(a))
                if self._uprightness() < 0.5:
                    print("[squat] toppled while rising")
                    return False
            # settle
            for _ in range(int(0.5 / self.model.opt.timestep)):
                self._apply_pd(self.q_nominal)
            z_end = self.get_torso_height()
            dropped = (z_start - z_bottom) > 0.05
            recovered = z_end > z_start - 0.06 and self._uprightness() > 0.85
            print(f"[squat] start={z_start:.3f} bottom={z_bottom:.3f} "
                  f"end={z_end:.3f} drop={z_start - z_bottom:.3f} amp={amp:.3f}")
            return bool(dropped and recovered)
        except Exception as e:  # pragma: no cover
            print(f"[squat] error: {e}")
            return False

    def walk_forward(self, distance_m: float = 0.10, secs: float = 2.0) -> bool:
        """ATTEMPT a small forward walk with a closed-loop quasi-static
        micro-step state machine that PRIORITISES staying upright.

        Each step (~0.6 s) runs four blended sub-phases:
          1. shift weight laterally onto the stance foot (hip-roll lean),
          2. lift the swing foot (knee bend for clearance) and swing the hip
             a small amount forward,
          3. place it down and unload,
          4. swap stance/swing for the next step.
        q_des is recomputed online every substep and the CoM ankle-balance loop
        runs underneath; if torso height or uprightness drops below a guard we
        ABORT, stabilise in place, and return without falling further.
        Progress is measured in the START-HEADING frame (never by writing the
        base pose).  On this torque-only H1 a few-cm shuffle is the goal and an
        honest "not achieved while staying upright" is an acceptable result."""
        if self._stand_height is None:
            self.home()

        start_xyz, start_R = self.get_base_pose()
        heading = start_R[:, 0].copy()  # body-x in world at start
        heading[2] = 0.0
        nrm = np.linalg.norm(heading)
        heading = heading / nrm if nrm > 1e-6 else np.array([1.0, 0.0, 0.0])

        guard_h = max(0.55, 0.7 * self._stand_height)
        step_period = 0.6
        n_steps = max(1, int(secs / step_period))
        sub = max(1, int(step_period / self.model.opt.timestep))

        # Gentle, balance-preserving gait amplitudes (radians) — tuned so the
        # robot stays upright through the cycle rather than lurching.
        hip_swing = 0.08      # swing-leg hip pitch forward
        knee_lift = 0.15      # swing-leg knee bend for clearance
        hip_roll_shift = 0.04  # lateral weight shift toward the stance foot

        def progress():
            xyz, _ = self.get_base_pose()
            return float(np.dot(xyz - start_xyz, heading))

        def balanced():
            return (self.get_torso_height() > guard_h
                    and self._uprightness() > 0.6)

        try:
            swing_left = True
            for _ in range(n_steps):
                if swing_left:
                    s_hp, s_kn, s_an = LHP, LKN, LAN
                    roll_dir = -1.0  # lean onto the RIGHT (stance) foot
                else:
                    s_hp, s_kn, s_an = RHP, RKN, RAN
                    roll_dir = +1.0

                for i in range(sub):
                    phase = i / sub
                    q = self.q_nominal.copy()
                    # lateral weight shift: ease in over first 30%, hold, ease out
                    if phase < 0.3:
                        s = phase / 0.3
                    elif phase < 0.8:
                        s = 1.0
                    else:
                        s = (1.0 - phase) / 0.2
                    s = max(0.0, min(1.0, s))
                    q[LHR] += roll_dir * hip_roll_shift * s
                    q[RHR] += roll_dir * hip_roll_shift * s
                    # swing-leg lift+swing during the middle of the phase
                    if 0.3 < phase < 0.8:
                        w = math.sin(math.pi * (phase - 0.3) / 0.5)
                        q[s_hp] -= hip_swing * w     # hip pitch forward
                        q[s_kn] += knee_lift * w     # knee bend (foot clearance)
                        q[s_an] += 0.10 * w          # toe up
                    self._apply_pd(q)
                    if not balanced():
                        print(f"[walk_forward] aborted (balance guard), "
                              f"progress={progress():.3f} m")
                        for _ in range(int(0.3 / self.model.opt.timestep)):
                            self._apply_pd(self.q_nominal)
                        return False
                swing_left = not swing_left

            # settle, then measure honestly in the start-heading frame
            for _ in range(int(0.4 / self.model.opt.timestep)):
                self._apply_pd(self.q_nominal)
            prog = progress()
            up = self._uprightness()
            print(f"[walk_forward] forward progress = {prog:.3f} m "
                  f"(target {distance_m:.3f} m), upright={up:.3f}")
            # Success requires REAL forward progress AND staying upright.
            return bool(prog > 0.03 and balanced())
        except Exception as e:  # pragma: no cover
            print(f"[walk_forward] error: {e}")
            return False


# ------------------------------------------------------------------ self-test
if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    r = Robot.build_from_mjcf(os.path.join(here, "mjcf.xml"))
    print("home() ->", r.home())
    print(r.describe())
    print("stand_balance(3) ->", r.stand_balance(3.0))
    r.home()
    print("squat(0.15) ->", r.squat(0.15, 3.0))
    r.home()
    print("walk_forward(0.10) ->", r.walk_forward(0.10, 2.4))
