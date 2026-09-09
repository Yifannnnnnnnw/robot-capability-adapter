"""
driver_from_scratch.py
======================
Full Robot driver for the SO-101 5-DOF serial arm + 1-DOF hinge gripper
(menagerie_so101).

Architecture
------------
* 5 arm joints  : shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll
                  (qpos/ctrl indices 0-4)
* 1 gripper joint: gripper  (qpos/ctrl index 5)
* EE site        : 'gripperframe'
* All actuators  : position-controlled STS3215 servos

IK Algorithm
------------
Damped Least Squares (DLS) with adaptive damping:
  dq = J^T (J J^T + lambda^2 * I)^{-1} * e
  lambda = clip(lambda_min + 0.5 * ||e_pos||, lambda_min, lambda_max)

For 6-D targets (pos + rot) the error vector is:
  e = [w_pos * e_pos ; w_rot * e_rot]
where e_rot is the axis-angle residual from R_target @ R_cur^T.

Step is clamped to max_step radians per iteration to prevent instability.
Joint limits are enforced by clipping after every step.

Motion execution
----------------
move_cartesian() solves IK for the target, then linearly interpolates
joint positions from current to solution, writing ctrl[] each sim step.
"""

from __future__ import annotations

import math
import os
from typing import Optional, Tuple

import mujoco
import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ARM_JOINT_NAMES = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
]
_GRIPPER_JOINT_NAME = "gripper"
_EE_SITE_NAME       = "gripperframe"
_EE_BODY_NAME       = "gripper"

# IK hyper-parameters (tuned on this robot)
_IK_MAX_ITER  = 400
_IK_POS_TOL   = 1e-3   # 1 mm
_IK_ROT_TOL   = 1e-2   # ~0.57 deg
_IK_LAM_MIN   = 0.005
_IK_LAM_MAX   = 0.12
_IK_MAX_STEP  = 0.25   # rad per iteration
_IK_W_POS     = 1.0
_IK_W_ROT     = 0.3

# Home pose: all arm joints at zero, gripper open
_HOME_ARM_QPOS = np.zeros(5, dtype=np.float64)


# ---------------------------------------------------------------------------
# Helper: rotation matrix -> axis-angle error
# ---------------------------------------------------------------------------
def _rot_error(R_target: np.ndarray, R_cur: np.ndarray) -> np.ndarray:
    """Return axis-angle vector representing R_target @ R_cur^T."""
    R_err = R_target @ R_cur.T
    cos_a = float(np.clip((np.trace(R_err) - 1.0) / 2.0, -1.0, 1.0))
    angle = math.acos(cos_a)
    if abs(angle) < 1e-8:
        return np.zeros(3)
    s = math.sin(angle)
    return (angle / (2.0 * s)) * np.array([
        R_err[2, 1] - R_err[1, 2],
        R_err[0, 2] - R_err[2, 0],
        R_err[1, 0] - R_err[0, 1],
    ])


# ---------------------------------------------------------------------------
# Robot class
# ---------------------------------------------------------------------------
class Robot:
    """
    Driver for the SO-101 5-DOF arm + gripper.

    Public API
    ----------
    build_from_mjcf(mjcf_path)  -> Robot
    home()                      -> bool
    get_joint_positions()       -> np.ndarray  (6,)
    step(n)
    render()
    describe()                  -> str

    ARM-specific
    ------------
    get_ee_pose()               -> (xyz, R3x3)
    move_cartesian(target_xyz, target_rot, duration) -> bool
    gripper_open()              -> bool
    gripper_close()             -> bool
    is_holding()                -> bool
    get_object_position(name)   -> np.ndarray (3,)
    get_object_names()          -> list[str]
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def __init__(
        self,
        model: mujoco.MjModel,
        data:  mujoco.MjData,
        mjcf_path: str,
    ) -> None:
        self._model     = model
        self._data      = data
        self._mjcf_path = mjcf_path
        self._viewer    = None   # lazy-init on first render()

        # --- resolve arm joint qpos / dof addresses ---
        self._arm_qpos_ids: list = []
        self._arm_dof_ids:  list = []

        for jname in _ARM_JOINT_NAMES:
            jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, jname)
            if jid < 0:
                raise RuntimeError(f"Joint '{jname}' not found in model")
            self._arm_qpos_ids.append(int(model.jnt_qposadr[jid]))
            self._arm_dof_ids.append(int(model.jnt_dofadr[jid]))

        # Joint limits for arm (5,)
        arm_jids = [
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, n)
            for n in _ARM_JOINT_NAMES
        ]
        self._arm_lo = np.array([model.jnt_range[j, 0] for j in arm_jids], dtype=np.float64)
        self._arm_hi = np.array([model.jnt_range[j, 1] for j in arm_jids], dtype=np.float64)

        # --- gripper joint ---
        gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, _GRIPPER_JOINT_NAME)
        if gid < 0:
            raise RuntimeError(f"Gripper joint '{_GRIPPER_JOINT_NAME}' not found")
        self._gripper_qpos_id = int(model.jnt_qposadr[gid])
        self._gripper_lo      = float(model.jnt_range[gid, 0])
        self._gripper_hi      = float(model.jnt_range[gid, 1])

        # --- actuator indices ---
        self._arm_ctrl_ids: list = []
        for aname in _ARM_JOINT_NAMES:
            aid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, aname)
            if aid < 0:
                raise RuntimeError(f"Actuator '{aname}' not found")
            self._arm_ctrl_ids.append(int(aid))

        gaid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, _GRIPPER_JOINT_NAME)
        if gaid < 0:
            raise RuntimeError("Gripper actuator not found")
        self._gripper_ctrl_id = int(gaid)

        # --- EE site ---
        self._ee_site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, _EE_SITE_NAME)
        if self._ee_site_id < 0:
            raise RuntimeError(f"EE site '{_EE_SITE_NAME}' not found")

        # --- gripper body IDs for contact detection ---
        gripper_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, _EE_BODY_NAME)
        moving_jaw_id   = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "moving_jaw_so101_v1")
        self._gripper_body_ids: set = set()
        if gripper_body_id >= 0:
            self._gripper_body_ids.add(gripper_body_id)
        if moving_jaw_id >= 0:
            self._gripper_body_ids.add(moving_jaw_id)

        # Collect geom IDs belonging to gripper bodies
        self._gripper_geom_ids: set = set()
        for i in range(model.ngeom):
            if int(model.geom_bodyid[i]) in self._gripper_body_ids:
                self._gripper_geom_ids.add(i)

        # --- initial forward kinematics ---
        mujoco.mj_forward(model, data)

    # ------------------------------------------------------------------
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """
        Load MJCF (resolving symlinks) and return a ready-to-use Robot.

        Parameters
        ----------
        mjcf_path : str  path to the scene XML (may be a symlink)
        """
        # Resolve symlinks so MuJoCo can find included sub-files
        real_path = os.path.realpath(mjcf_path)
        model = mujoco.MjModel.from_xml_path(real_path)
        data  = mujoco.MjData(model)
        mujoco.mj_resetData(model, data)
        mujoco.mj_forward(model, data)
        return cls(model, data, mjcf_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _get_arm_qpos(self) -> np.ndarray:
        """Return current arm joint positions as (5,) array."""
        return np.array(
            [self._data.qpos[i] for i in self._arm_qpos_ids],
            dtype=np.float64,
        )

    def _set_arm_qpos(self, q: np.ndarray) -> None:
        """Write arm joint positions directly into qpos (for IK)."""
        for i, idx in enumerate(self._arm_qpos_ids):
            self._data.qpos[idx] = float(q[i])

    def _set_arm_ctrl(self, q: np.ndarray) -> None:
        """Write arm position targets into ctrl."""
        for i, idx in enumerate(self._arm_ctrl_ids):
            self._data.ctrl[idx] = float(q[i])

    def _set_gripper_ctrl(self, val: float) -> None:
        """Write gripper position target into ctrl (clamped to limits)."""
        self._data.ctrl[self._gripper_ctrl_id] = float(
            np.clip(val, self._gripper_lo, self._gripper_hi)
        )

    def _fk(self) -> Tuple[np.ndarray, np.ndarray]:
        """Run forward kinematics; return (pos, R3x3) of EE site."""
        mujoco.mj_forward(self._model, self._data)
        pos = self._data.site_xpos[self._ee_site_id].copy()
        R   = self._data.site_xmat[self._ee_site_id].reshape(3, 3).copy()
        return pos, R

    def _jacobian(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Compute (Jp, Jr) each shape (3, 5) — position and rotation Jacobians
        for the 5 arm DOFs only (columns indexed by self._arm_dof_ids).
        """
        nv   = self._model.nv
        jacp = np.zeros((3, nv))
        jacr = np.zeros((3, nv))
        mujoco.mj_jacSite(self._model, self._data, jacp, jacr, self._ee_site_id)
        cols = self._arm_dof_ids
        return jacp[:, cols], jacr[:, cols]

    # ------------------------------------------------------------------
    # DLS IK
    # ------------------------------------------------------------------
    def _ik(
        self,
        target_pos: np.ndarray,
        target_rot: Optional[np.ndarray] = None,
        q_init:     Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, float, bool]:
        """
        Damped Least Squares IK for the 5-DOF arm.

        Algorithm
        ---------
        Iterates:
          1. FK to get current EE pose
          2. Compute position (and optionally rotation) error
          3. Build Jacobian J (3x5 or 6x5)
          4. Adaptive damping: lambda = clip(lam_min + 0.5*||e_pos||, lam_min, lam_max)
          5. DLS step: dq = J^T (J J^T + lambda^2 I)^{-1} e
          6. Clamp ||dq|| <= max_step
          7. Clip q to joint limits

        Parameters
        ----------
        target_pos : (3,) desired EE position in world frame [m]
        target_rot : (3,3) desired EE rotation matrix (optional)
        q_init     : (5,) initial joint angles (defaults to current qpos)

        Returns
        -------
        q_sol    : (5,) solution joint angles
        residual : float  position residual [m]
        success  : bool   True if residual < _IK_POS_TOL
        """
        model = self._model
        data  = self._data

        # Save gripper state so IK loop doesn't disturb it
        gripper_saved = float(data.qpos[self._gripper_qpos_id])

        q = (q_init.copy() if q_init is not None else self._get_arm_qpos())
        pos_res = float("inf")

        for _it in range(_IK_MAX_ITER):
            # Apply current guess and run FK
            self._set_arm_qpos(q)
            mujoco.mj_forward(model, data)

            pos_err = target_pos - data.site_xpos[self._ee_site_id]
            pos_res = float(np.linalg.norm(pos_err))

            if target_rot is not None:
                R_cur   = data.site_xmat[self._ee_site_id].reshape(3, 3)
                rot_err = _rot_error(target_rot, R_cur)
                rot_res = float(np.linalg.norm(rot_err))

                if pos_res < _IK_POS_TOL and rot_res < _IK_ROT_TOL:
                    data.qpos[self._gripper_qpos_id] = gripper_saved
                    return q, pos_res, True

                # Weighted 6-D error
                err = np.concatenate([_IK_W_POS * pos_err, _IK_W_ROT * rot_err])
                Jp, Jr = self._jacobian()
                J = np.vstack([_IK_W_POS * Jp, _IK_W_ROT * Jr])
            else:
                if pos_res < _IK_POS_TOL:
                    data.qpos[self._gripper_qpos_id] = gripper_saved
                    return q, pos_res, True

                err = pos_err
                Jp, _ = self._jacobian()
                J = Jp

            # Adaptive damping: larger when far from target
            lam = float(np.clip(
                _IK_LAM_MIN + 0.5 * pos_res,
                _IK_LAM_MIN,
                _IK_LAM_MAX,
            ))

            # DLS: dq = J^T (J J^T + lam^2 I)^{-1} e
            JJT = J @ J.T
            try:
                dq = J.T @ np.linalg.solve(
                    JJT + lam ** 2 * np.eye(J.shape[0]), err
                )
            except np.linalg.LinAlgError:
                break

            # Clamp step magnitude
            dq_norm = float(np.linalg.norm(dq))
            if dq_norm > _IK_MAX_STEP:
                dq = dq * (_IK_MAX_STEP / dq_norm)

            q = np.clip(q + dq, self._arm_lo, self._arm_hi)

        # Restore gripper
        data.qpos[self._gripper_qpos_id] = gripper_saved
        return q, pos_res, pos_res < _IK_POS_TOL

    # ------------------------------------------------------------------
    # Trajectory execution
    # ------------------------------------------------------------------
    def _run_to_joints(
        self,
        q_target:     np.ndarray,
        duration:     float = 2.0,
        gripper_ctrl: Optional[float] = None,
    ) -> bool:
        """
        Linearly interpolate arm joints from current -> q_target over
        `duration` seconds of simulation time, stepping the physics.

        Parameters
        ----------
        q_target     : (5,) target arm joint positions
        duration     : motion duration [s of sim time]
        gripper_ctrl : gripper position target (None = hold current)
        """
        model = self._model
        data  = self._data
        dt    = float(model.opt.timestep)
        n_steps = max(1, int(duration / dt))

        q_start = self._get_arm_qpos()
        g_val   = (
            float(data.qpos[self._gripper_qpos_id])
            if gripper_ctrl is None
            else float(np.clip(gripper_ctrl, self._gripper_lo, self._gripper_hi))
        )

        for i in range(n_steps):
            alpha = (i + 1) / n_steps
            q_des = q_start + alpha * (q_target - q_start)
            self._set_arm_ctrl(q_des)
            self._set_gripper_ctrl(g_val)
            mujoco.mj_step(model, data)

        # Short settle: hold target for 0.1 s
        settle = max(1, int(0.1 / dt))
        for _ in range(settle):
            self._set_arm_ctrl(q_target)
            self._set_gripper_ctrl(g_val)
            mujoco.mj_step(model, data)

        return True

    # ------------------------------------------------------------------
    # Required public API
    # ------------------------------------------------------------------
    def home(self) -> bool:
        """
        Move arm to home pose (all joints zero) and open the gripper.

        Returns True on success.
        """
        g_open = self._gripper_lo   # fully open
        return self._run_to_joints(_HOME_ARM_QPOS.copy(), duration=2.0, gripper_ctrl=g_open)

    def get_joint_positions(self) -> np.ndarray:
        """
        Return all 6 joint positions: [arm(5) | gripper(1)] in radians.
        """
        arm_q     = self._get_arm_qpos()
        gripper_q = float(self._data.qpos[self._gripper_qpos_id])
        return np.append(arm_q, gripper_q)

    def step(self, n: int = 1) -> None:
        """Advance simulation by n steps (holding current ctrl targets)."""
        for _ in range(n):
            mujoco.mj_step(self._model, self._data)

    def render(self) -> None:
        """
        Open / update a passive viewer window.
        No-op (with warning) if a display is unavailable.
        """
        try:
            if self._viewer is None:
                import mujoco.viewer as mjv
                self._viewer = mjv.launch_passive(self._model, self._data)
            else:
                self._viewer.sync()
        except Exception as exc:
            print(f"[Robot.render] viewer unavailable: {exc}")

    def describe(self) -> str:
        """Return a human-readable summary of the robot state."""
        pos, R = self._fk()
        q      = self.get_joint_positions()
        mid    = 0.5 * (self._gripper_lo + self._gripper_hi)
        lines  = [
            "SO-101 5-DOF Arm + Gripper  (menagerie_so101)",
            f"  MJCF          : {self._mjcf_path}",
            f"  DOF           : 5 arm + 1 gripper",
            f"  EE position   : [{pos[0]:.4f}, {pos[1]:.4f}, {pos[2]:.4f}] m",
            f"  Gripper state : {'CLOSED' if q[5] > mid else 'OPEN'} ({q[5]:.3f} rad)",
            f"  Active contacts: {self._data.ncon}",
            f"  Is holding    : {self.is_holding()}",
            "  Arm joints    :",
        ]
        for i, name in enumerate(_ARM_JOINT_NAMES):
            lines.append(
                f"    [{i}] {name:20s} = {q[i]:+.4f} rad  "
                f"(limits [{self._arm_lo[i]:.3f}, {self._arm_hi[i]:.3f}])"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # ARM-specific API
    # ------------------------------------------------------------------
    def get_ee_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Return current end-effector pose.

        Returns
        -------
        xyz : (3,) position in world frame [m]
        R   : (3,3) rotation matrix (world <- EE frame)
        """
        return self._fk()

    def move_cartesian(
        self,
        target_xyz: np.ndarray,
        target_rot: Optional[np.ndarray] = None,
        duration:   float = 2.0,
    ) -> bool:
        """
        Move EE to target_xyz (and optionally target_rot) in world frame.

        Uses DLS IK to find joint angles, then executes a linear joint-space
        trajectory over `duration` seconds of simulation time.

        Parameters
        ----------
        target_xyz : array-like (3,)  desired EE position [m]
        target_rot : array-like (3,3) desired EE rotation matrix (optional)
        duration   : float  motion duration [s of sim time]

        Returns
        -------
        True if IK succeeded and motion was executed.

        Raises
        ------
        ValueError  if the target is unreachable (IK residual > 1 mm)
        """
        target_xyz = np.asarray(target_xyz, dtype=np.float64).flatten()
        if target_xyz.shape != (3,):
            raise ValueError(f"target_xyz must be shape (3,), got {target_xyz.shape}")
        if target_rot is not None:
            target_rot = np.asarray(target_rot, dtype=np.float64)
            if target_rot.shape != (3, 3):
                raise ValueError(f"target_rot must be shape (3,3), got {target_rot.shape}")

        q_sol, residual, ok = self._ik(target_xyz, target_rot)

        if not ok:
            raise ValueError(
                f"IK failed: target {np.round(target_xyz, 4)} unreachable "
                f"(position residual = {residual * 1000:.1f} mm, "
                f"threshold = {_IK_POS_TOL * 1000:.0f} mm). "
                "Check that the target is within the robot's workspace."
            )

        return self._run_to_joints(q_sol, duration=duration)

    def gripper_open(self) -> bool:
        """
        Open the gripper fully (joint -> minimum value = -0.175 rad).

        Executes over 0.5 s of simulation time while holding arm pose.

        Returns True on success.
        """
        g_val   = self._gripper_lo
        model   = self._model
        data    = self._data
        dt      = float(model.opt.timestep)
        n_steps = max(1, int(0.5 / dt))
        q_cur   = self._get_arm_qpos()

        for _ in range(n_steps):
            self._set_arm_ctrl(q_cur)
            self._set_gripper_ctrl(g_val)
            mujoco.mj_step(model, data)
        return True

    def gripper_close(self) -> bool:
        """
        Close the gripper fully (joint -> maximum value = 1.745 rad).

        Executes over 0.5 s of simulation time while holding arm pose.

        Returns True on success.
        """
        g_val   = self._gripper_hi
        model   = self._model
        data    = self._data
        dt      = float(model.opt.timestep)
        n_steps = max(1, int(0.5 / dt))
        q_cur   = self._get_arm_qpos()

        for _ in range(n_steps):
            self._set_arm_ctrl(q_cur)
            self._set_gripper_ctrl(g_val)
            mujoco.mj_step(model, data)
        return True

    def is_holding(self) -> bool:
        """
        Return True if the gripper is in contact with a non-gripper object.

        Scans all active MuJoCo contacts; returns True if any contact
        involves one gripper geom and one non-gripper geom (i.e. the
        gripper is touching something external).
        """
        data = self._data
        for i in range(data.ncon):
            c  = data.contact[i]
            g1 = int(c.geom1)
            g2 = int(c.geom2)
            g1_grip = g1 in self._gripper_geom_ids
            g2_grip = g2 in self._gripper_geom_ids
            # Exactly one side is a gripper geom -> external contact
            if g1_grip != g2_grip:
                return True
        return False

    def get_object_position(self, name: str) -> np.ndarray:
        """
        Return world-frame XYZ of a named scene object.

        Resolution order: body -> geom -> site.
        Raises KeyError if the name is not found anywhere in the model.

        Parameters
        ----------
        name : str  object name (e.g. 'cube_red', 'mug', 'banana')

        Returns
        -------
        xyz : (3,) world position [m]
        """
        model = self._model
        data  = self._data
        mujoco.mj_forward(model, data)

        # 1. Try body
        bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        if bid >= 0:
            return data.xpos[bid].copy()

        # 2. Try geom
        gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
        if gid >= 0:
            return data.geom_xpos[gid].copy()

        # 3. Try site
        sid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, name)
        if sid >= 0:
            return data.site_xpos[sid].copy()

        raise KeyError(
            f"Object '{name}' not found in model (checked bodies, geoms, sites). "
            f"Known scene objects: {self.get_object_names()}"
        )

    def get_object_names(self) -> list:
        """
        Return names of all non-robot bodies in the scene.

        Filters out the robot's own kinematic chain and the world body.
        A task planner can call this to discover what objects are present.
        """
        _ROBOT_BODIES = {
            "world", "base", "shoulder", "upper_arm", "lower_arm",
            "wrist", "gripper", "camera_mount", "moving_jaw_so101_v1",
        }
        model = self._model
        names = []
        for i in range(model.nbody):
            bname = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, i)
            if bname and bname not in _ROBOT_BODIES:
                names.append(bname)
        return names

    # ------------------------------------------------------------------
    # Convenience / utility
    # ------------------------------------------------------------------
    def set_joint_positions(self, q: np.ndarray, duration: float = 1.0) -> bool:
        """
        Move arm to specified joint positions (5 values) over `duration` seconds.
        Values are clamped to joint limits.

        Parameters
        ----------
        q        : array-like (5,) target joint angles [rad]
        duration : float  motion duration [s of sim time]
        """
        q = np.asarray(q, dtype=np.float64).flatten()
        if len(q) != 5:
            raise ValueError(f"Expected 5 joint values, got {len(q)}")
        q = np.clip(q, self._arm_lo, self._arm_hi)
        return self._run_to_joints(q, duration=duration)

    def close(self) -> None:
        """Release viewer resources if open."""
        if self._viewer is not None:
            try:
                self._viewer.close()
            except Exception:
                pass
            self._viewer = None

    def __repr__(self) -> str:
        q = self.get_joint_positions()
        pos, _ = self._fk()
        return (
            f"Robot(SO-101, EE={np.round(pos, 3).tolist()}, "
            f"arm_q={np.round(q[:5], 3).tolist()}, gripper={q[5]:.3f})"
        )
