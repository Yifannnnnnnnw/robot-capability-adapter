"""
driver_from_scratch.py  --  Self-contained driver for the SO-101 5-DOF arm.

Robot class: ARM (serial chain, 5 revolute DOF + visual jaw).
  - Position-control actuators (kp servos) on the 5 arm joints + jaw.
  - Grasping is done with weld equality constraints between gripper_link and
    six free-floating scene objects (banana/mug/bottle/screwdriver/duck/lego).
    The jaw joint is purely visual; the weld is what actually holds an object.

    IMPORTANT (the grasp fix): a MuJoCo weld stores a *relative pose*
    (eq_data[3:10] = relpos(3) + relquat(4)) that the solver tries to enforce
    between body1 (gripper_link) and body2 (the object).  The relpose baked
    into the MJCF is the pose at the *initial* scene layout, when the object
    sits far from the gripper.  Activating the weld as-is therefore yanks the
    object back to that stale far-away offset (the "displaced away from the
    hand" failure).  The fix is to RECOMPUTE eq_data[3:10] from the CURRENT
    transforms of gripper_link and the object at the instant we close, so the
    object is welded exactly where it currently is and then rides with the
    hand on lift.

IK method:
  Damped Least Squares (DLS) on the *position* (3-DOF) task only.  The arm is
  5-DOF and non-redundant for full 6-DOF pose, but we control just the EE
  position (the planner names object positions, not orientations), which is a
  well-conditioned 3-task / 5-joint problem -> redundancy is absorbed by DLS.
  Damping scales with the residual norm so we get fast progress far away and
  stable steps near the solution.  We clamp the per-iteration joint step and
  project onto joint limits each iteration.

No imports from auto_adapter.skeletons.  mujoco + numpy + stdlib only.
"""

import numpy as np
import mujoco


class Robot:
    # ---- joint / actuator naming from study.json ----
    ARM_JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex",
                  "wrist_flex", "wrist_roll"]
    ARM_ACTS = ["act_shoulder_pan", "act_shoulder_lift", "act_elbow_flex",
                "act_wrist_flex", "act_wrist_roll"]
    JAW_ACT = "act_jaw_visual"
    EE_SITE = "ee_site"
    EE_BODY = "gripper_link"
    GRASPABLE = ["banana", "mug", "bottle", "screwdriver", "duck", "lego"]

    # ----------------------------------------------------------------- build
    def __init__(self, model, data):
        self.model = model
        self.data = data
        self._viewer = None

        # resolve joint ids / addresses
        self.jids = [self._jid(j) for j in self.ARM_JOINTS]
        self.qadr = [int(model.jnt_qposadr[j]) for j in self.jids]
        self.dofadr = [int(model.jnt_dofadr[j]) for j in self.jids]
        self.lo = np.array([model.jnt_range[j][0] for j in self.jids])
        self.hi = np.array([model.jnt_range[j][1] for j in self.jids])

        # actuator ids
        self.act_ids = [self._aid(a) for a in self.ARM_ACTS]
        self.jaw_aid = self._aid_or_none(self.JAW_ACT)
        if self.jaw_aid is not None:
            self.jaw_range = tuple(model.actuator_ctrlrange[self.jaw_aid])
        else:
            self.jaw_range = (0.0, 1.0)

        # ee site / body
        self.ee_sid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE,
                                        self.EE_SITE)
        self.ee_bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY,
                                        self.EE_BODY)

        # weld equality constraint ids keyed by object body name.
        # Record which of the welded bodies is the *object* (the non-gripper
        # side) so we can recompute its relative pose on grasp.
        self.weld_eq = {}        # name -> eq index
        self.weld_b1 = {}        # name -> body id of side 1 (gripper)
        self.weld_b2 = {}        # name -> body id of side 2 (object)
        for i in range(model.neq):
            if model.eq_type[i] == mujoco.mjtEq.mjEQ_WELD:
                b1 = int(model.eq_obj1id[i])
                b2 = int(model.eq_obj2id[i])
                n1 = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, b1)
                n2 = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, b2)
                # the object is whichever side is in GRASPABLE
                if n2 in self.GRASPABLE:
                    self.weld_eq[n2] = i
                    self.weld_b1[n2] = b1
                    self.weld_b2[n2] = b2
                elif n1 in self.GRASPABLE:
                    self.weld_eq[n1] = i
                    self.weld_b1[n1] = b2
                    self.weld_b2[n1] = b1

        self._held = None         # name of currently welded object, or None
        self._jaw_closed = False

        # bring everything to a defined state
        mujoco.mj_forward(model, data)

    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data = mujoco.MjData(model)
        return cls(model, data)

    # ----------------------------------------------------------- id helpers
    def _jid(self, name):
        i = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if i < 0:
            raise KeyError(f"joint '{name}' not found in model")
        return i

    def _aid(self, name):
        i = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
        if i < 0:
            raise KeyError(f"actuator '{name}' not found in model")
        return i

    def _aid_or_none(self, name):
        i = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
        return None if i < 0 else i

    # ------------------------------------------------------------- core sim
    def step(self, n: int = 1):
        """Advance the physics n steps, holding current actuator targets."""
        for _ in range(int(n)):
            mujoco.mj_step(self.model, self.data)
        return True

    def get_joint_positions(self):
        """Return the 5 arm joint angles (radians) as a numpy array."""
        return np.array([self.data.qpos[a] for a in self.qadr])

    def _set_arm_ctrl(self, q):
        q = np.clip(np.asarray(q, float), self.lo, self.hi)
        for aid, v in zip(self.act_ids, q):
            self.data.ctrl[aid] = v

    def _hold_jaw(self):
        if self.jaw_aid is not None:
            self.data.ctrl[self.jaw_aid] = (self.jaw_range[1] if not
                                            self._jaw_closed
                                            else self.jaw_range[0])

    # -------------------------------------------------------- home / describe
    def home(self) -> bool:
        """Drive all joints to the zero (neutral) configuration."""
        target = np.zeros(5)
        self._set_arm_ctrl(target)
        self._hold_jaw()
        # settle to target
        for _ in range(800):
            mujoco.mj_step(self.model, self.data)
            if np.max(np.abs(self.get_joint_positions() - target)) < 1e-3:
                break
        return bool(np.max(np.abs(self.get_joint_positions() - target)) < 5e-2)

    def describe(self) -> str:
        xyz, _ = self.get_ee_pose()
        q = self.get_joint_positions()
        return (
            "SO-101 5-DOF arm (ARM class)\n"
            f"  arm joints : {self.ARM_JOINTS}\n"
            f"  joint pos  : {np.round(q, 4).tolist()}\n"
            f"  ee_site    : {self.EE_SITE} at xyz={np.round(xyz, 4).tolist()}\n"
            f"  gripper    : visual jaw + weld grasp; held={self._held}\n"
            f"  objects    : {self.get_object_names()}\n"
            "  IK         : damped least squares (position, 3-task/5-joint)"
        )

    # ------------------------------------------------------------- FK / pose
    def get_ee_pose(self):
        """Return (xyz, R 3x3) of the EE site in world frame."""
        mujoco.mj_forward(self.model, self.data)
        xyz = self.data.site_xpos[self.ee_sid].copy()
        R = self.data.site_xmat[self.ee_sid].reshape(3, 3).copy()
        return xyz, R

    def _fk_pos(self, q):
        """Pure kinematic FK: set qpos, mj_forward (no integration), read site."""
        for a, v in zip(self.qadr, q):
            self.data.qpos[a] = v
        mujoco.mj_forward(self.model, self.data)
        return self.data.site_xpos[self.ee_sid].copy()

    # ----------------------------------------------------------------- IK
    def _ik(self, target_xyz, q0=None, iters=300, tol=1e-3):
        """Damped least squares position IK.

        Returns (q_solution, residual_norm). Raises ValueError if it cannot
        converge to within ~1.5 cm (treated as unreachable).
        """
        target = np.asarray(target_xyz, float)
        q = self.get_joint_positions() if q0 is None else np.asarray(q0, float)
        q = np.clip(q, self.lo, self.hi)
        # save state so the kinematics scratch doesn't disturb the sim
        qpos_save = self.data.qpos.copy()
        qvel_save = self.data.qvel.copy()

        jacp = np.zeros((3, self.model.nv))
        jacr = np.zeros((3, self.model.nv))
        n = np.inf
        try:
            for _ in range(iters):
                p = self._fk_pos(q)
                err = target - p
                n = float(np.linalg.norm(err))
                if n < tol:
                    break
                mujoco.mj_jacSite(self.model, self.data, jacp, jacr,
                                  self.ee_sid)
                J = jacp[:, self.dofadr]                  # 3 x 5
                lam = 0.04 + 0.6 * n                      # damping schedule
                dq = J.T @ np.linalg.solve(J @ J.T + (lam ** 2) * np.eye(3),
                                           err)
                dq = np.clip(dq, -0.2, 0.2)               # step clamp
                q = np.clip(q + dq, self.lo, self.hi)
        finally:
            # restore the dynamic state
            self.data.qpos[:] = qpos_save
            self.data.qvel[:] = qvel_save
            mujoco.mj_forward(self.model, self.data)

        if n > 0.015:
            raise ValueError(
                f"IK did not converge: target {target} unreachable "
                f"(residual {n*100:.1f} cm). Pick a closer target.")
        return np.clip(q, self.lo, self.hi), n

    # --------------------------------------------------------- cartesian move
    def move_cartesian(self, target_xyz, duration: float = 2.0) -> bool:
        """Move the EE to target_xyz over `duration` seconds via IK + servo
        interpolation. Returns True if the final EE error < 2 cm."""
        target = np.asarray(target_xyz, float)
        q_goal, _ = self._ik(target)            # raises if unreachable
        q_start = self.get_joint_positions()

        dt = self.model.opt.timestep
        nsteps = max(1, int(duration / dt))
        for k in range(1, nsteps + 1):
            a = k / nsteps
            # smooth (cosine) interpolation in joint space
            s = 0.5 - 0.5 * np.cos(np.pi * a)
            self._set_arm_ctrl((1 - s) * q_start + s * q_goal)
            self._hold_jaw()
            mujoco.mj_step(self.model, self.data)

        # let it settle on the final target
        self._set_arm_ctrl(q_goal)
        for _ in range(300):
            self._hold_jaw()
            mujoco.mj_step(self.model, self.data)

        xyz, _ = self.get_ee_pose()
        err = float(np.linalg.norm(xyz - target))
        return bool(err < 0.02)

    # ----------------------------------------------------------- gripper
    def gripper_open(self) -> bool:
        """Open the jaw and release any welded object."""
        self._jaw_closed = False
        if self.jaw_aid is not None:
            self.data.ctrl[self.jaw_aid] = self.jaw_range[1]
        # release weld
        if self._held is not None:
            self._set_weld(self._held, False)
            self._held = None
        self._set_arm_ctrl(self.get_joint_positions())
        for _ in range(200):
            mujoco.mj_step(self.model, self.data)
        return True

    def gripper_close(self) -> bool:
        """Close the jaw; if a graspable object is within reach of the EE,
        activate its weld so the object is carried."""
        self._jaw_closed = True
        if self.jaw_aid is not None:
            self.data.ctrl[self.jaw_aid] = self.jaw_range[0]
        self._set_arm_ctrl(self.get_joint_positions())
        for _ in range(150):
            mujoco.mj_step(self.model, self.data)

        # find nearest graspable object to the EE within a grasp tolerance
        ee, _ = self.get_ee_pose()
        best, best_d = None, 1e9
        for name in self.weld_eq:
            try:
                d = float(np.linalg.norm(self.get_object_position(name) - ee))
            except KeyError:
                continue
            if d < best_d:
                best, best_d = name, d
        # generous grasp radius so a reach that lands within a few cm of the
        # object body origin counts (object meshes can be sizeable).
        if best is not None and best_d < 0.12:
            self._set_weld(best, True)
            self._held = best
            for _ in range(100):
                mujoco.mj_step(self.model, self.data)
        return True

    def _set_weld(self, name, active):
        """Activate / deactivate the weld equality for `name`.

        When activating we RECOMPUTE the weld's relative pose (eq_data[3:10])
        from the CURRENT world transforms of gripper_link and the object, so
        the object is held exactly where it is now and rides with the gripper.
        Using the stale MJCF relpose would teleport the object to its initial
        (far) offset -- that was the grasp_lift failure.
        """
        eq = self.weld_eq.get(name)
        if eq is None:
            return
        if active:
            mujoco.mj_forward(self.model, self.data)
            b1 = self.weld_b1[name]   # gripper side (obj1 in the MJCF weld)
            b2 = self.weld_b2[name]   # object side  (obj2)
            relpos, relquat = self._relative_pose(b1, b2)
            # eq_data layout for weld: [anchor(3), relpos(3), relquat(4), tscale]
            self.model.eq_data[eq, 3:6] = relpos
            self.model.eq_data[eq, 6:10] = relquat
        self.model.eq_active0[eq] = 1 if active else 0
        self.data.eq_active[eq] = 1 if active else 0
        mujoco.mj_forward(self.model, self.data)

    def _relative_pose(self, b1, b2):
        """Pose of body b2 expressed in the frame of body b1:
        returns (relpos(3), relquat(4))  such that
        world_b2 = world_b1 * (relpos, relquat).
        """
        p1 = self.data.xpos[b1]
        q1 = self.data.xquat[b1]
        p2 = self.data.xpos[b2]
        q2 = self.data.xquat[b2]
        negq1 = np.zeros(4)
        mujoco.mju_negQuat(negq1, q1)
        dp = p2 - p1
        relpos = np.zeros(3)
        mujoco.mju_rotVecQuat(relpos, dp, negq1)   # rotate world delta into b1
        relquat = np.zeros(4)
        mujoco.mju_mulQuat(relquat, negq1, q2)
        return relpos.copy(), relquat.copy()

    def is_holding(self) -> bool:
        """True if an object is currently welded to the gripper."""
        if self._held is None:
            return False
        eq = self.weld_eq.get(self._held)
        if eq is None:
            return False
        return bool(self.data.eq_active[eq])

    # --------------------------------------------------------- scene objects
    def get_object_names(self):
        """Names of the manipulable scene objects present in the model."""
        names = []
        for n in self.GRASPABLE:
            if mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, n) >= 0:
                names.append(n)
        return names

    def get_object_position(self, name: str):
        """World xyz of a named scene object. Resolves body -> geom -> site.
        Raises KeyError if the name is unknown."""
        mujoco.mj_forward(self.model, self.data)
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
        if bid >= 0:
            return self.data.xpos[bid].copy()
        gid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, name)
        if gid >= 0:
            return self.data.geom_xpos[gid].copy()
        sid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, name)
        if sid >= 0:
            return self.data.site_xpos[sid].copy()
        raise KeyError(f"unknown scene object '{name}'")

    # ----------------------------------------------------------------- render
    def render(self):
        """Open / refresh an interactive viewer (best-effort, no-op if
        unavailable)."""
        try:
            import mujoco.viewer
            if self._viewer is None:
                self._viewer = mujoco.viewer.launch_passive(self.model,
                                                            self.data)
            self._viewer.sync()
            return True
        except Exception as e:
            print(f"[render] viewer unavailable: {e}")
            return False
