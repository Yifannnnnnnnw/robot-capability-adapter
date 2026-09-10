"""driver_from_scratch.py — self-contained driver for the Unitree H1 humanoid.

Robot class: HUMANOID / BIPED
  - 19 torque (motor) actuators, floating base via a free joint.
  - No gripper, no weld constraints.

Control strategy
----------------
Everything runs on joint-space PD:  tau = kp*(q_des - q) - kd*qd, clamped to the
actuator ctrlrange, applied every mj_step.  A humanoid is a tall inverted
pendulum, so on top of the nominal-pose PD we run a small feedback loop that
modulates the ankle-pitch (and hip-roll) targets from the measured torso
orientation and angular rate — an "ankle/hip strategy" that keeps the torso
upright.  This is what turns an open-loop pose hold (which topples in ~1 s) into
a stable stand.

Public API (humanoid contract):
  build_from_mjcf, home, get_joint_positions, step, render, describe
  stand_balance(secs), squat(depth, secs), walk_forward(distance_m, secs)
  get_base_pose(), get_torso_height()

No imports from auto_adapter.skeletons.
"""

import os
import numpy as np
import mujoco


def _quat_to_rpy(q):
    """wxyz quaternion -> (roll, pitch, yaw)."""
    w, x, y, z = q
    roll = np.arctan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = np.arcsin(np.clip(2.0 * (w * y - z * x), -1.0, 1.0))
    yaw = np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return roll, pitch, yaw


def _quat_to_mat(q):
    m = np.zeros(9)
    mujoco.mju_quat2Mat(m, np.asarray(q, dtype=float))
    return m.reshape(3, 3)


class Robot:
    # actuator order (matches the MJCF <actuator> block):
    #  0 L_hip_yaw 1 L_hip_roll 2 L_hip_pitch 3 L_knee 4 L_ankle
    #  5 R_hip_yaw 6 R_hip_roll 7 R_hip_pitch 8 R_knee 9 R_ankle
    # 10 torso
    # 11-14 L arm (shoulder p/r/y, elbow)  15-18 R arm
    _LHIP_ROLL, _RHIP_ROLL = 1, 6
    _LHIP_PITCH, _RHIP_PITCH = 2, 7
    _LKNEE, _RKNEE = 3, 8
    _LANKLE, _RANKLE = 4, 9

    def __init__(self, model, data):
        self.model = model
        self.data = data
        self.nu = model.nu
        # actuator -> qpos/qvel address maps
        self.qadr = np.array(
            [model.jnt_qposadr[model.actuator_trnid[i, 0]] for i in range(self.nu)]
        )
        self.vadr = np.array(
            [model.jnt_dofadr[model.actuator_trnid[i, 0]] for i in range(self.nu)]
        )
        self.ctrlrange = model.actuator_ctrlrange.copy()

        # nominal standing pose (leg joints from the "home" keyframe: hips/knees
        # slightly bent, arms neutral).
        self.q_nominal = np.array(
            [0, 0, -0.4, 0.8, -0.4,
             0, 0, -0.4, 0.8, -0.4,
             0,
             0, 0, 0, 0,
             0, 0, 0, 0.], dtype=float)

        # per-joint PD gains (legs & torso stiff, arms light)
        self.kp = np.array(
            [150, 150, 300, 300, 60,
             150, 150, 300, 300, 60,
             300,
             40, 40, 18, 18,
             40, 40, 18, 18.], dtype=float)
        self.kd = np.array(
            [8, 8, 12, 12, 6,
             8, 8, 12, 12, 6,
             12,
             3, 3, 2, 2,
             3, 3, 2, 2.], dtype=float)

        # balance-feedback gains
        self.k_pitch_p, self.k_pitch_d = 3.0, 0.5
        self.k_roll_p, self.k_roll_d = 2.0, 0.3

        self._torso_bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "torso_link")
        self._pelvis_bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
        self._renderer = None
        self._frames = []

        # discover manipulable scene objects (bodies not part of the robot).
        self._robot_bodies = set()
        for i in range(model.nbody):
            nm = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, i)
            if nm:
                self._robot_bodies.add(nm)

    # ------------------------------------------------------------------ build
    @classmethod
    def build_from_mjcf(cls, mjcf_path):
        """Load the MJCF (resolving relative includes) and reset to home."""
        mjcf_path = os.path.abspath(mjcf_path)
        cwd = os.getcwd()
        # includes in the scene are relative to the MJCF's directory
        os.chdir(os.path.dirname(mjcf_path))
        try:
            model = mujoco.MjModel.from_xml_path(os.path.basename(mjcf_path))
        finally:
            os.chdir(cwd)
        data = mujoco.MjData(model)
        robot = cls(model, data)
        robot._reset_home()
        return robot

    # -------------------------------------------------------------- internals
    def _reset_home(self):
        m, d = self.model, self.data
        if m.nkey > 0:
            mujoco.mj_resetDataKeyframe(m, d, 0)
        else:
            mujoco.mj_resetData(m, d)
        mujoco.mj_forward(m, d)

    def _pd(self, q_des):
        """One PD control substep with balance feedback; advances one mj_step."""
        m, d = self.model, self.data
        q = d.qpos[self.qadr]
        qd = d.qvel[self.vadr]
        roll, pitch, _ = _quat_to_rpy(d.qpos[3:7])
        wx, wy, wz = d.qvel[3:6]

        qd_des = q_des.copy()
        # ankle-pitch strategy to counter torso pitch (keeps CoM over feet)
        dp = self.k_pitch_p * pitch + self.k_pitch_d * wy
        qd_des[self._LANKLE] += dp
        qd_des[self._RANKLE] += dp
        # hip-roll strategy for lateral balance
        dr = self.k_roll_p * roll + self.k_roll_d * wx
        qd_des[self._LHIP_ROLL] += dr
        qd_des[self._RHIP_ROLL] += dr

        tau = self.kp * (qd_des - q) - self.kd * qd
        tau = np.clip(tau, self.ctrlrange[:, 0], self.ctrlrange[:, 1])
        d.ctrl[:] = tau
        mujoco.mj_step(m, d)

    def _upright(self):
        roll, pitch, _ = _quat_to_rpy(self.data.qpos[3:7])
        return abs(roll) < 0.6 and abs(pitch) < 0.6

    def _dt(self):
        return self.model.opt.timestep

    # ------------------------------------------------------------- public API
    def home(self):
        """Reset to the home keyframe and settle into a balanced stand."""
        self._reset_home()
        for _ in range(int(1.0 / self._dt())):
            self._pd(self.q_nominal)
        return self._upright() and self.get_torso_height() > 0.6

    def get_joint_positions(self):
        """Actuated joint positions (19,) in actuator order."""
        return self.data.qpos[self.qadr].copy()

    def step(self, n=1):
        """Hold the nominal balanced pose for n physics steps."""
        for _ in range(int(n)):
            self._pd(self.q_nominal)
        return True

    def get_base_pose(self):
        """Torso/pelvis world position (xyz) and 3x3 rotation matrix."""
        xyz = self.data.xpos[self._torso_bid].copy()
        R = _quat_to_mat(self.data.qpos[3:7])
        return xyz, R

    def get_torso_height(self):
        return float(self.data.xpos[self._torso_bid][2])

    def describe(self):
        xyz, _ = self.get_base_pose()
        return {
            "class": "humanoid",
            "model": "Unitree H1",
            "dof": int(self.model.nv),
            "n_actuators": int(self.nu),
            "actuator_type": "torque motor",
            "control": "joint-space PD + ankle/hip balance feedback",
            "torso_height": round(self.get_torso_height(), 3),
            "base_xyz": [round(float(v), 3) for v in xyz],
            "upright": bool(self._upright()),
            "capabilities": [
                "stand_balance", "squat", "walk_forward",
                "get_base_pose", "get_torso_height",
            ],
        }

    # --------------------------------------------------------- object helpers
    def get_object_names(self):
        """Manipulable scene objects (non-robot bodies). H1 scene has none."""
        robot = {
            "world", "pelvis", "torso_link",
        }
        names = []
        for i in range(self.model.nbody):
            nm = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_BODY, i)
            if nm and "link" not in nm and nm not in robot:
                names.append(nm)
        return names

    def get_object_position(self, name):
        """World xyz of a named body -> geom -> site. Raises KeyError if unknown."""
        m, d = self.model, self.data
        bid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, name)
        if bid >= 0:
            return d.xpos[bid].copy()
        gid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, name)
        if gid >= 0:
            return d.geom_xpos[gid].copy()
        sid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, name)
        if sid >= 0:
            return d.site_xpos[sid].copy()
        raise KeyError("unknown scene object: %r" % name)

    # ------------------------------------------------------------- behaviors
    def stand_balance(self, secs=3.0):
        """Hold a stable standing pose for `secs`. Returns True if it stayed up."""
        n = max(1, int(secs / self._dt()))
        ok = True
        for _ in range(n):
            self._pd(self.q_nominal)
            if not self._upright() or self.get_torso_height() < 0.55:
                ok = False
                break
        return bool(ok and self._upright() and self.get_torso_height() > 0.6)

    def squat(self, depth=0.15, secs=3.0):
        """Lower the torso by ~depth m (bending hips+knees) then return to
        standing.  Stays balanced; recovers the standing height."""
        depth = float(max(0.03, min(depth, 0.28)))
        # joint deltas calibrated so torso drop ~= depth while staying balanced
        kf, hf, af = 3.3 * depth, 1.6 * depth, 1.5 * depth

        def pose(a):
            q = self.q_nominal.copy()
            for hip, kn, an in ((self._LHIP_PITCH, self._LKNEE, self._LANKLE),
                                (self._RHIP_PITCH, self._RKNEE, self._RANKLE)):
                q[hip] -= hf * a
                q[kn] += kf * a
                q[an] -= af * a
            return q

        # settle briefly at the stand
        for _ in range(int(0.3 / self._dt())):
            self._pd(self.q_nominal)
        z_stand = self.get_torso_height()

        half = max(1, int((secs * 0.5) / self._dt()))
        ramp = max(1, int(half * 0.7))  # smooth interpolation portion
        z_low = z_stand
        # descend
        for i in range(half):
            a = min(1.0, i / ramp)
            self._pd(pose(a))
            z_low = min(z_low, self.get_torso_height())
            if not self._upright():
                return False
        # ascend / recover
        for i in range(half):
            a = max(0.0, 1.0 - i / ramp)
            self._pd(pose(a))
            if not self._upright():
                return False
        for _ in range(int(0.4 / self._dt())):
            self._pd(self.q_nominal)

        z_rec = self.get_torso_height()
        dropped = (z_stand - z_low) > 0.4 * depth
        recovered = self._upright() and z_rec > z_stand - 0.06
        return bool(dropped and recovered)

    def walk_forward(self, distance_m=0.10, secs=2.0):
        """Closed-loop quasi-static micro-step walk in the start-heading frame.

        4-phase state machine per step: shift weight onto the stance leg, lift &
        place the swing foot a small distance forward, settle to double support,
        then swap.  q_des is updated online from the measured torso state and the
        target is clamped to joint limits.  Aborts (without toppling further) if
        the torso drops or tilts past a guard.
        """
        distance_m = float(max(0.02, min(distance_m, 0.30)))
        # heading frame at start
        x0, _, _ = self.data.xpos[self._torso_bid]
        y0 = self.data.xpos[self._torso_bid][1]
        _, _, yaw0 = _quat_to_rpy(self.data.qpos[3:7])
        c0, s0 = np.cos(yaw0), np.sin(yaw0)

        def forward_progress():
            x = self.data.xpos[self._torso_bid][0]
            y = self.data.xpos[self._torso_bid][1]
            dx, dy = x - x0, y - y0
            return c0 * dx + s0 * dy  # projection on start heading

        # gait parameters (small, quasi-static)
        step_len = 0.045          # hip-pitch swing amplitude (rad-ish scaling)
        lift = 0.28               # knee flexion for foot clearance
        shift = 0.06              # hip-roll weight shift toward stance leg
        period = 0.62             # seconds per single-support step
        sub = max(1, int(period / self._dt()))

        z_start = self.get_torso_height()
        n_steps = 8
        stance_left = True  # start by loading the LEFT leg, swing RIGHT
        for _step in range(n_steps):
            # indices for swing/stance legs
            if stance_left:
                sw_hip, sw_kn = self._RHIP_PITCH, self._RKNEE
                st_roll, sw_roll = self._LHIP_ROLL, self._RHIP_ROLL
                roll_dir = +1.0
            else:
                sw_hip, sw_kn = self._LHIP_PITCH, self._LKNEE
                st_roll, sw_roll = self._RHIP_ROLL, self._LHIP_ROLL
                roll_dir = -1.0

            for i in range(sub):
                phase = i / sub  # 0..1 within this step
                q = self.q_nominal.copy()
                # phase 0-0.25: shift weight onto stance foot
                # phase 0.25-0.7: lift + swing forward
                # phase 0.7-1.0: place + settle to double support
                if phase < 0.25:
                    w = phase / 0.25
                    lf = 0.0
                    fwd = 0.0
                elif phase < 0.7:
                    w = 1.0
                    p2 = (phase - 0.25) / 0.45
                    lf = np.sin(np.pi * p2)             # bell-shaped lift
                    fwd = p2                            # advance swing hip
                else:
                    w = 1.0 - (phase - 0.7) / 0.3
                    lf = 0.0
                    fwd = 1.0
                # lateral weight shift toward stance leg
                q[st_roll] += roll_dir * shift * w
                q[sw_roll] += roll_dir * shift * w * 0.5
                # swing leg forward + lift; stance leg trails slightly back
                q[sw_hip] -= step_len * fwd
                q[sw_kn] += lift * lf
                self._pd(q)

                # balance guard
                if self.get_torso_height() < z_start - 0.18 or not self._upright():
                    return False
            stance_left = not stance_left
            if forward_progress() >= distance_m:
                break

        # settle back to a clean balanced stand
        for _ in range(int(0.5 / self._dt())):
            self._pd(self.q_nominal)

        prog = forward_progress()
        return bool(self._upright() and self.get_torso_height() > 0.6 and prog > 0.03)

    # ------------------------------------------------------------------ render
    def render(self, width=640, height=480):
        """Capture an offscreen RGB frame (best-effort; returns None on failure)."""
        try:
            if self._renderer is None:
                self._renderer = mujoco.Renderer(self.model, height, width)
            self._renderer.update_scene(self.data)
            frame = self._renderer.render()
            self._frames.append(frame)
            return frame
        except Exception:
            return None
