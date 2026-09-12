"""
driver.py – AgileX Piper capability driver.
Robot subclass of ArmSerialDLSSkeleton implementing the five required
capability methods for the piper robot configuration.
"""
from __future__ import annotations

import logging
import warnings
from typing import Optional

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton
from auto_adapter.skeletons.base import ArmSpec

logger = logging.getLogger(__name__)

# ── joint / actuator names ────────────────────────────────────────────────────
_ARM_JOINTS  = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"]
_ARM_ACTS    = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"]
_GRIPPER_ACT = ["gripper"]
_GRIPPER_JNT = ["joint7"]

_JOINT_LIMITS = {
    "joint1": (-2.618,  2.618),
    "joint2": ( 0.0,    3.14),
    "joint3": (-2.697,  0.0),
    "joint4": (-1.832,  1.832),
    "joint5": (-1.22,   1.22),
    "joint6": (-3.14,   3.14),
}

# home keyframe from MJCF: qpos = [0, 1.57, -1.3485, 0, 0, 0]
_HOME_QPOS      = [0.0, 1.57, -1.3485, 0.0, 0.0, 0.0]
_GRIPPER_CLOSED = 0.0
_GRIPPER_OPEN   = 0.035

# motion / tolerance constants
_DEFAULT_DUR   = 2.0    # seconds
_SETTLE_SECS   = 0.3    # seconds to hold after reaching target
_POS_TOL       = 0.05   # m  – EE position success threshold
_GRIPPER_TOL   = 0.003  # m  – gripper settling threshold
_WRIST_TOL     = 0.05   # rad – joint6 wrist-roll tolerance
# Gripper position controller needs ~300 steps (0.6 s) to converge
_GRIPPER_SETTLE_STEPS = 300


def _build_spec() -> ArmSpec:
    return ArmSpec(
        ee_site_name            = "ee_site",
        ee_body_name            = "link8",
        arm_joint_names         = _ARM_JOINTS,
        arm_actuator_names      = _ARM_ACTS,
        joint_limits            = _JOINT_LIMITS,
        home_qpos               = _HOME_QPOS,
        ik_damping              = 0.005,
        ik_max_iter             = 60,
        ik_tolerance            = 5e-4,
        ik_step_clamp           = 0.15,
        ik_raise_on_unreachable = False,
        gripper_actuator_names  = _GRIPPER_ACT,
        gripper_joint_names     = _GRIPPER_JNT,
        gripper_close_ctrl      = _GRIPPER_CLOSED,
        gripper_open_ctrl       = _GRIPPER_OPEN,
        gripper_settle_steps    = _GRIPPER_SETTLE_STEPS,
        grasp_backend           = None,
        weld_graspable_bodies   = None,
        sim_dt                  = 0.002,
    )


class Robot(ArmSerialDLSSkeleton):
    """AgileX Piper – 6-DOF arm + parallel-jaw gripper."""

    @classmethod
    def from_mjcf(cls, mjcf_path: str, spec: Optional[ArmSpec] = None) -> "Robot":
        if spec is None:
            spec = _build_spec()
        return super().from_mjcf(mjcf_path, spec=spec)

    # ── private helpers (prefixed _piper_ to avoid parent attribute collisions)
    def _piper_ee_pos(self) -> np.ndarray:
        """World-frame position of ee_site."""
        sid = self.model.site(self.spec.ee_site_name).id
        return self.data.site_xpos[sid].copy()

    def _piper_gripper_pos(self) -> float:
        """Current joint7 slide position (metres)."""
        jid = self.model.joint("joint7").id
        return float(self.data.qpos[self.model.jnt_qposadr[jid]])

    def _piper_joint6_pos(self) -> float:
        """Current joint6 hinge position (radians)."""
        jid = self.model.joint("joint6").id
        return float(self.data.qpos[self.model.jnt_qposadr[jid]])

    def _piper_gripper_aid(self) -> int:
        return int(self.model.actuator("gripper").id)

    def _piper_hold_gripper(self, g_val: float, n_steps: int) -> None:
        """Step physics n_steps while holding gripper ctrl fixed."""
        gid   = self._piper_gripper_aid()
        g_val = float(np.clip(g_val, _GRIPPER_CLOSED, _GRIPPER_OPEN))
        for _ in range(n_steps):
            self.data.ctrl[gid] = g_val
            self.step(1)

    def _piper_check_limits(self, q: np.ndarray) -> bool:
        """Return False and log if any arm joint exceeds its limit."""
        for i, name in enumerate(_ARM_JOINTS):
            lo, hi = _JOINT_LIMITS[name]
            if not (lo - 1e-4 <= q[i] <= hi + 1e-4):
                logger.warning("Joint %s limit violated: %.4f not in [%.4f, %.4f]",
                               name, q[i], lo, hi)
                return False
        return True

    def _piper_interp_move(self, q_target: np.ndarray, duration: float,
                           g_val: float) -> None:
        """Linearly interpolate arm actuators from current ctrl to q_target."""
        # NOTE: self._arm_actuator_ids is a list[int] set by the parent skeleton
        act_ids = self._arm_actuator_ids
        gid     = self._piper_gripper_aid()
        g_val   = float(np.clip(g_val, _GRIPPER_CLOSED, _GRIPPER_OPEN))
        q_start = np.array([self.data.ctrl[aid] for aid in act_ids])
        n_steps = max(1, int(round(duration / self.spec.sim_dt)))
        for k in range(n_steps):
            alpha    = (k + 1) / n_steps
            q_interp = q_start + alpha * (q_target - q_start)
            for aid, val in zip(act_ids, q_interp):
                self.data.ctrl[aid] = val
            self.data.ctrl[gid] = g_val
            self.step(1)

    def _piper_move_to(self, target_xyz: np.ndarray, duration: float,
                       g_val: float) -> bool:
        """IK + interpolated move + settle. Returns True iff EE within _POS_TOL."""
        q = self.ik(target_xyz, raise_on_unreachable=False)
        if q is None:
            logger.warning("IK infeasible for target %s", target_xyz)
            return False
        if not self._piper_check_limits(q):
            return False
        self._piper_interp_move(q, duration, g_val)
        settle_n = max(1, int(round(_SETTLE_SECS / self.spec.sim_dt)))
        self._piper_hold_gripper(g_val, settle_n)
        dist = float(np.linalg.norm(self._piper_ee_pos() - target_xyz))
        if dist > _POS_TOL:
            logger.warning("EE did not reach target: dist=%.4f m", dist)
            return False
        return True

    # ── capability: move_end_effector ─────────────────────────────────────────
    def move_end_effector(self, request: dict) -> bool:
        """
        Move ee_site to target_xyz (world frame).
        Gripper aperture is preserved throughout.
        Returns True iff EE reaches within 0.05 m of target_xyz.
        Returns False and halts if IK is infeasible or joint limits exceeded.
        """
        target_xyz = np.asarray(request["target_xyz"], dtype=float)
        duration   = float(request.get("duration", _DEFAULT_DUR))
        g_val      = float(self.data.ctrl[self._piper_gripper_aid()])
        ok = self._piper_move_to(target_xyz, duration, g_val)
        if not ok:
            logger.warning("move_end_effector: failed to reach %s", target_xyz)
        return ok

    # ── capability: set_gripper_aperture ─────────────────────────────────────
    def set_gripper_aperture(self, request: dict) -> bool:
        """
        Set gripper joint7 to aperture_m in [0.0, 0.035].
        joint8 mirrors via equality constraint automatically.
        Arm joints are held at their current ctrl values.
        Out-of-range values are clamped with a warning.
        Returns True iff |actual - commanded| <= 0.003 m.
        """
        aperture_m = float(request["aperture_m"])
        if not (_GRIPPER_CLOSED <= aperture_m <= _GRIPPER_OPEN):
            warnings.warn(
                f"aperture_m={aperture_m:.4f} out of [0, 0.035]; clamping.",
                UserWarning, stacklevel=2,
            )
            aperture_m = float(np.clip(aperture_m, _GRIPPER_CLOSED, _GRIPPER_OPEN))

        settle_steps = int(request.get("settle_steps", self.spec.gripper_settle_steps))
        settle_steps = int(np.clip(settle_steps, 1, 500))

        act_ids  = self._arm_actuator_ids          # list[int] from parent
        arm_ctrl = [float(self.data.ctrl[aid]) for aid in act_ids]
        gid      = self._piper_gripper_aid()

        for _ in range(settle_steps):
            for aid, val in zip(act_ids, arm_ctrl):
                self.data.ctrl[aid] = val
            self.data.ctrl[gid] = aperture_m
            self.step(1)

        actual = self._piper_gripper_pos()
        err = abs(actual - aperture_m)
        if err > _GRIPPER_TOL:
            logger.warning("set_gripper_aperture: settle error=%.4f m", err)
            return False
        return True

    # ── capability: move_end_effector_with_wrist ──────────────────────────────
    def move_end_effector_with_wrist(self, request: dict) -> bool:
        """
        Move ee_site to target_xyz AND set joint6 to wrist_roll_rad.
        Gripper aperture is preserved.
        Returns True iff EE dist <= 0.05 m AND |joint6 error| <= 0.05 rad.
        Returns False if IK infeasible or wrist_roll_rad out of range.
        """
        target_xyz = np.asarray(request["target_xyz"], dtype=float)
        wrist_roll = float(request["wrist_roll_rad"])
        duration   = float(request.get("duration", _DEFAULT_DUR))

        lo6, hi6 = _JOINT_LIMITS["joint6"]
        if not (lo6 - 1e-4 <= wrist_roll <= hi6 + 1e-4):
            logger.warning("wrist_roll_rad=%.4f out of joint6 range [%.4f, %.4f]",
                           wrist_roll, lo6, hi6)
            return False

        g_val = float(self.data.ctrl[self._piper_gripper_aid()])

        q = self.ik(target_xyz, raise_on_unreachable=False)
        if q is None:
            logger.warning("move_end_effector_with_wrist: IK infeasible for %s", target_xyz)
            return False

        # Override joint6 with commanded wrist roll
        j6_idx    = _ARM_JOINTS.index("joint6")
        q[j6_idx] = float(np.clip(wrist_roll, lo6, hi6))

        if not self._piper_check_limits(q):
            return False

        self._piper_interp_move(q, duration, g_val)
        settle_n = max(1, int(round(_SETTLE_SECS / self.spec.sim_dt)))
        self._piper_hold_gripper(g_val, settle_n)

        ee_dist = float(np.linalg.norm(self._piper_ee_pos() - target_xyz))
        j6_err  = abs(self._piper_joint6_pos() - wrist_roll)
        ok = (ee_dist <= _POS_TOL) and (j6_err <= _WRIST_TOL)
        if not ok:
            logger.warning("move_end_effector_with_wrist: ee_dist=%.4f j6_err=%.4f",
                           ee_dist, j6_err)
        return ok

    # ── capability: apply_contact_displacement ────────────────────────────────
    def apply_contact_displacement(self, request: dict) -> bool:
        """
        Move EE to contact_xyz (approach), then to terminal_xyz (push/press).
        Gripper aperture is preserved throughout.
        Returns True iff EE reaches within 0.05 m of terminal_xyz.
        Partial motion is allowed if contact forces block terminal_xyz.
        Returns False if IK is infeasible at either waypoint.
        """
        contact_xyz  = np.asarray(request["contact_xyz"],  dtype=float)
        terminal_xyz = np.asarray(request["terminal_xyz"], dtype=float)
        approach_dur = float(request.get("approach_duration", _DEFAULT_DUR))
        push_dur     = float(request.get("push_duration",     _DEFAULT_DUR))

        g_val = float(self.data.ctrl[self._piper_gripper_aid()])

        # Segment 1: approach to contact_xyz
        ok1 = self._piper_move_to(contact_xyz, approach_dur, g_val)
        if not ok1:
            logger.warning("apply_contact_displacement: approach segment failed")
            return False

        # Segment 2: push/press to terminal_xyz
        q_term = self.ik(terminal_xyz, raise_on_unreachable=False)
        if q_term is None:
            logger.warning("apply_contact_displacement: terminal IK infeasible")
            return False
        if not self._piper_check_limits(q_term):
            return False

        self._piper_interp_move(q_term, push_dur, g_val)
        settle_n = max(1, int(round(_SETTLE_SECS / self.spec.sim_dt)))
        self._piper_hold_gripper(g_val, settle_n)

        dist = float(np.linalg.norm(self._piper_ee_pos() - terminal_xyz))
        if dist > _POS_TOL:
            logger.warning("apply_contact_displacement: terminal dist=%.4f m", dist)
            return False
        return True

    # ── capability: move_end_effector_via_waypoint ────────────────────────────
    def move_end_effector_via_waypoint(self, request: dict) -> bool:
        """
        Move EE through waypoint_xyz then to final_xyz (ordering enforced).
        Gripper aperture is preserved throughout.
        Returns True iff EE reaches within 0.05 m of final_xyz.
        Halts at last feasible position on IK failure.
        """
        waypoint_xyz = np.asarray(request["waypoint_xyz"], dtype=float)
        final_xyz    = np.asarray(request["final_xyz"],    dtype=float)
        wp_dur       = float(request.get("waypoint_duration", _DEFAULT_DUR))
        final_dur    = float(request.get("final_duration",    _DEFAULT_DUR))

        g_val = float(self.data.ctrl[self._piper_gripper_aid()])

        # Segment 1: to waypoint (must succeed before proceeding)
        ok1 = self._piper_move_to(waypoint_xyz, wp_dur, g_val)
        if not ok1:
            logger.warning("move_end_effector_via_waypoint: waypoint segment failed")
            return False

        # Segment 2: to final
        ok2 = self._piper_move_to(final_xyz, final_dur, g_val)
        if not ok2:
            logger.warning("move_end_effector_via_waypoint: final segment failed")
            return False

        return True


# ── module-level factory ──────────────────────────────────────────────────────
def build() -> Robot:
    """Return a Robot bound to mjcf.xml with the Piper ArmSpec."""
    return Robot.from_mjcf("mjcf.xml", spec=_build_spec())
