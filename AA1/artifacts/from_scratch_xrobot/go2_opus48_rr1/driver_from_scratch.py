"""
driver_from_scratch.py — standalone driver for the Unitree Go2 quadruped.

Robot class: Unitree Go2 (12 actuated DOF: 4 legs x [hip, thigh, calf]),
floating base (freejoint), all TORQUE motors. No gripper, no end-effector,
no arm — so this driver implements QUADRUPED behaviors (stand / sit / walk)
using joint-space PD control:

        tau = clip( kp * (q_des - q) - kd * qd , -tau_max, +tau_max )

Key modelling facts discovered from the MJCF (and baked in below):
  * nq=19, nv=18, nu=12, timestep=0.002 s, gravity -9.81.
  * Joint qpos/qvel are ordered alphabetically (FL, FR, RL, RR), but the
    ACTUATORS are ordered FR, FL, RR, RL.  We therefore resolve, for each
    actuator i, the qpos / qvel address of the joint it drives via
    actuator_trnid -> jnt_qposadr / jnt_dofadr.  All control vectors in this
    file are expressed in ACTUATOR order (which is also d.ctrl order).
  * Home keyframe: base z = 0.27 m, per-leg joint angles [0, 0.9, -1.8].
  * Diagonal trot pairs (actuator-leg index): A = {FR=0, RL=3},
    B = {FL=1, RR=2}; the two diagonals swing in anti-phase.

No imports from auto_adapter.skeletons.  mujoco + numpy + stdlib only.
"""

from __future__ import annotations

import os
import numpy as np
import mujoco


def _quat_to_mat(quat: np.ndarray) -> np.ndarray:
    """Convert a wxyz quaternion to a 3x3 rotation matrix."""
    R = np.zeros(9, dtype=float)
    mujoco.mju_quat2Mat(R, np.asarray(quat, dtype=float))
    return R.reshape(3, 3)


class Robot:
    """Standalone PD-controlled driver for the Unitree Go2 quadruped."""

    # ----- per-leg nominal "fold" pose used for sitting (actuator order) -----
    SIT_THIGH = 1.4    # thigh folds forward/up
    SIT_CALF = -2.5    # calf tucks in

    # ----- PD gains (joint-space) -----
    KP = 55.0
    KD = 3.0

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        self.model = model
        self.data = data
        self._viewer = None

        # --- index maps: actuator i -> qpos / qvel address of driven joint ---
        nu = model.nu
        self.act_qadr = np.zeros(nu, dtype=int)
        self.act_vadr = np.zeros(nu, dtype=int)
        self.act_names = []
        for i in range(nu):
            jid = int(model.actuator_trnid[i, 0])
            self.act_qadr[i] = int(model.jnt_qposadr[jid])
            self.act_vadr[i] = int(model.jnt_dofadr[jid])
            self.act_names.append(
                mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, i) or f"act{i}"
            )

        # torque limits from actuator ctrlrange (symmetric)
        self.tau_max = model.actuator_ctrlrange[:, 1].copy()

        # base (free joint) addresses
        self.base_qadr = 0   # qpos[0:3]=xyz, qpos[3:7]=quat(wxyz)
        self.base_vadr = 0   # qvel[0:3]=lin, qvel[3:6]=ang

        # standing (home) joint targets in actuator order
        if model.nkey > 0:
            self._stand_q = np.array(
                [model.key_qpos[0][a] for a in self.act_qadr], dtype=float
            )
            self._home_qpos = model.key_qpos[0].copy()
            self._home_ctrl = model.key_ctrl[0].copy()
        else:
            # fallback nominal crouch
            tile = np.array([0.0, 0.9, -1.8])
            self._stand_q = np.tile(tile, 4)
            self._home_qpos = None
            self._home_ctrl = None

        self._stand_height = 0.27  # nominal, refined after home()

    # ------------------------------------------------------------------ build
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        # follow symlinks / relative includes correctly
        path = os.path.realpath(mjcf_path)
        model = mujoco.MjModel.from_xml_path(path)
        data = mujoco.MjData(model)
        robot = cls(model, data)
        return robot

    # ------------------------------------------------------- low-level access
    def _q_act(self) -> np.ndarray:
        return self.data.qpos[self.act_qadr].copy()

    def _qd_act(self) -> np.ndarray:
        return self.data.qvel[self.act_vadr].copy()

    def _apply_pd(self, q_des: np.ndarray, kp: float = None, kd: float = None):
        kp = self.KP if kp is None else kp
        kd = self.KD if kd is None else kd
        q = self.data.qpos[self.act_qadr]
        qd = self.data.qvel[self.act_vadr]
        tau = kp * (q_des - q) - kd * qd
        np.clip(tau, -self.tau_max, self.tau_max, out=tau)
        self.data.ctrl[:] = tau

    def _hold_pd(self, q_des: np.ndarray, steps: int, kp=None, kd=None,
                 render: bool = False):
        for _ in range(int(steps)):
            self._apply_pd(q_des, kp, kd)
            mujoco.mj_step(self.model, self.data)
            if render:
                self.render()

    def _ramp_pd(self, q_start: np.ndarray, q_goal: np.ndarray, steps: int,
                 kp=None, kd=None, render: bool = False):
        """Smoothly interpolate the PD target from q_start to q_goal."""
        steps = max(int(steps), 1)
        for k in range(steps):
            a = (k + 1) / steps
            q_des = (1.0 - a) * q_start + a * q_goal
            self._apply_pd(q_des, kp, kd)
            mujoco.mj_step(self.model, self.data)
            if render:
                self.render()

    # --------------------------------------------------------------- required
    def home(self) -> bool:
        """Reset to the home keyframe and settle into a stable stand."""
        try:
            if self.model.nkey > 0:
                mujoco.mj_resetDataKeyframe(self.model, self.data, 0)
            else:
                mujoco.mj_resetData(self.model, self.data)
            mujoco.mj_forward(self.model, self.data)
            # settle under PD so contacts equilibrate
            self._hold_pd(self._stand_q, 400)
            self._stand_height = float(self.data.qpos[2])
            return bool(self._upright() and self.data.qpos[2] > 0.15)
        except Exception as e:  # pragma: no cover
            print(f"[home] failed: {e}")
            return False

    def get_joint_positions(self) -> np.ndarray:
        """Actuated joint positions, in actuator order (FR,FL,RR,RL x hip/thigh/calf)."""
        return self._q_act()

    def step(self, n: int = 1):
        """Advance the simulation n steps, holding the current PD target."""
        q_des = self._q_act()  # hold current pose by default
        for _ in range(int(n)):
            self._apply_pd(q_des)
            mujoco.mj_step(self.model, self.data)

    def render(self):
        """Open / update a passive viewer if available; otherwise no-op."""
        try:
            import mujoco.viewer as mjv
            if self._viewer is None:
                self._viewer = mjv.launch_passive(self.model, self.data)
            self._viewer.sync()
        except Exception:
            # headless / no viewer available
            pass

    def describe(self) -> dict:
        xyz, R = self.get_base_pose()
        return {
            "robot_id": "go2_opus48_rr1",
            "class": "quadruped",
            "dof": int(self.model.nu),
            "actuator_order": list(self.act_names),
            "control": "joint-space PD torque (tau = kp*(q_des-q) - kd*qd)",
            "kp": self.KP,
            "kd": self.KD,
            "stand_height_m": round(self._stand_height, 4),
            "base_height_now_m": round(float(self.data.qpos[2]), 4),
            "base_xyz": [round(float(v), 4) for v in xyz],
            "upright": bool(self._upright()),
            "behaviors": ["stand_up", "sit", "walk_forward",
                          "get_body_height", "get_base_pose"],
        }

    # --------------------------------------------------- quadruped behaviors
    def stand_up(self, duration: float = 2.0) -> bool:
        """Drive all legs to the stable standing pose; end with body height > 0.15 m."""
        try:
            steps = max(int(duration / self.model.opt.timestep), 1)
            q_now = self._q_act()
            # ramp to standing target, then hold to settle
            self._ramp_pd(q_now, self._stand_q, int(steps * 0.6))
            self._hold_pd(self._stand_q, int(steps * 0.4))
            h = float(self.data.qpos[2])
            self._stand_height = h
            ok = (h > 0.15) and self._upright()
            if not ok:
                print(f"[stand_up] height={h:.3f} upright={self._upright()}")
            return bool(ok)
        except Exception as e:  # pragma: no cover
            print(f"[stand_up] failed: {e}")
            return False

    def sit(self, duration: float = 1.5) -> bool:
        """Fold hips+knees so the body LOWERS below 0.8x the standing height."""
        try:
            h0 = float(self.data.qpos[2])
            if h0 < 0.15:
                # make sure we start from a stand for a meaningful reference
                self.stand_up(1.0)
                h0 = float(self.data.qpos[2])

            sit_q = self._stand_q.copy()
            for leg in range(4):
                sit_q[leg * 3 + 1] = self.SIT_THIGH
                sit_q[leg * 3 + 2] = self.SIT_CALF
            sit_q = self._clamp_to_limits(sit_q)

            steps = max(int(duration / self.model.opt.timestep), 1)
            q_now = self._q_act()
            self._ramp_pd(q_now, sit_q, int(steps * 0.7))
            self._hold_pd(sit_q, int(steps * 0.3))

            h = float(self.data.qpos[2])
            ok = h < 0.8 * self._stand_height
            if not ok:
                print(f"[sit] h={h:.3f} stand={self._stand_height:.3f} "
                      f"(need < {0.8*self._stand_height:.3f})")
            return bool(ok)
        except Exception as e:  # pragma: no cover
            print(f"[sit] failed: {e}")
            return False

    def walk_forward(self, secs: float = 2.0, speed: float = 0.2) -> bool:
        """Diagonal-trot gait producing REAL forward base displacement.

        Diagonal pairs (FR,RL) and (FL,RR) swing in anti-phase.  Each leg's
        thigh tracks a cosine fore/aft stroke and the calf bends during the
        swing (positive sine) phase to lift the foot.  `speed` scales the
        stride length so a larger speed -> longer step -> more displacement.
        Returns True if the base moves forward more than ~3 cm while upright.
        """
        try:
            # make sure we begin from a settled stand
            if self.data.qpos[2] < 0.18 or not self._upright():
                self.stand_up(1.0)

            dt = self.model.opt.timestep
            nstep = max(int(secs / dt), 1)

            # gait parameters; stride scales with requested speed
            T = 0.4                                  # gait period (s)
            stride = float(np.clip(0.18 + speed * 0.9, 0.15, 0.45))  # thigh fore/aft amp
            clearance = 0.55                         # calf bend during swing

            # diagonal phasing in actuator-leg order: FR=0, FL=1, RR=2, RL=3
            leg_phase = {0: 0.0, 3: 0.0, 1: np.pi, 2: np.pi}

            # capture start pose in the base heading frame
            x0, y0, _ = self.data.qpos[0:3]
            R0 = _quat_to_mat(self.data.qpos[3:7])
            fwd0 = R0[:, 0]  # body x-axis in world at start = heading

            up_ok = True
            for k in range(nstep):
                t = k * dt
                phase = 2.0 * np.pi * (t / T)
                q_des = self._stand_q.copy()
                for leg, ph in leg_phase.items():
                    s = np.sin(phase + ph)
                    lift = max(s, 0.0)
                    # fore/aft thigh stroke
                    q_des[leg * 3 + 1] = self._stand_q[leg * 3 + 1] + \
                        stride * np.cos(phase + ph)
                    # bend calf on the swing half to clear the ground
                    q_des[leg * 3 + 2] = self._stand_q[leg * 3 + 2] - \
                        clearance * lift
                q_des = self._clamp_to_limits(q_des)
                self._apply_pd(q_des)
                mujoco.mj_step(self.model, self.data)
                if not self._upright(thresh=0.6):
                    up_ok = False
                    print("[walk_forward] lost uprightness — aborting")
                    break

            # smoothly recover to the stand pose so we end stable
            self._ramp_pd(self._q_act(), self._stand_q, 200)

            x1, y1, _ = self.data.qpos[0:3]
            disp = np.array([x1 - x0, y1 - y0, 0.0])
            forward = float(disp @ fwd0)          # progress along start heading
            ok = up_ok and forward > 0.03 and self._upright(thresh=0.6)
            if not ok:
                print(f"[walk_forward] forward={forward:.3f} m up_ok={up_ok}")
            return bool(ok)
        except Exception as e:  # pragma: no cover
            print(f"[walk_forward] failed: {e}")
            return False

    # ----------------------------------------------------------- state query
    def get_body_height(self) -> float:
        """World Z of the floating base (torso)."""
        return float(self.data.qpos[2])

    def get_base_pose(self):
        """(xyz, R3x3) world pose of the floating base."""
        xyz = self.data.qpos[0:3].copy()
        R = _quat_to_mat(self.data.qpos[3:7])
        return xyz, R

    def get_base_yaw(self) -> float:
        R = _quat_to_mat(self.data.qpos[3:7])
        return float(np.arctan2(R[1, 0], R[0, 0]))

    # ---------------------------------------------------------------- helpers
    def _upright(self, thresh: float = 0.7) -> bool:
        """True if the base z-axis still points mostly up (dot with world Z)."""
        R = _quat_to_mat(self.data.qpos[3:7])
        return float(R[2, 2]) > thresh

    def _clamp_to_limits(self, q_des: np.ndarray) -> np.ndarray:
        """Clamp actuator-order joint targets to their MJCF joint limits."""
        out = np.array(q_des, dtype=float)
        for i in range(self.model.nu):
            jid = int(self.model.actuator_trnid[i, 0])
            if self.model.jnt_limited[jid]:
                lo, hi = self.model.jnt_range[jid]
                out[i] = min(max(out[i], lo), hi)
        return out
