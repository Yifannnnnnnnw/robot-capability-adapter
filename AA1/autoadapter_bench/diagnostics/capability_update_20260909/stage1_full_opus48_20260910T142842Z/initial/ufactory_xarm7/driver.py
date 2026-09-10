# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for UFACTORY xArm7 (ArmSerialDLSSkeleton).

Bindings (from MJCF analysis):
  * 7 arm joints joint1..joint7 driven by position actuators act1..act7.
  * EE reference: site ``link_tcp`` (the TCP).
  * Parallel gripper: single actuator ``gripper`` (ctrl 0..255) driving a
    tendon ``split``.  Probed: ctrl 0 -> aperture joints ~0 (closed),
    ctrl 255 -> aperture joints ~0.85 rad (open).  So opening_fraction maps
    linearly onto ctrl and onto the driver-joint angle range [0, 0.85].
  * Tool/gripper collision geoms: bodies link7..fingers with contact enabled
    (disabled visual geoms excluded).  Contact target geom ``fixed_contact_geom``.

All capability timing/holds use ``data.time`` (sim seconds).  Physics is
advanced only via actuator commands + step(); live qpos/qvel is never set to
achieve an action.  IK uses scratch data internally.
"""
from __future__ import annotations

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton
from auto_adapter.skeletons.base import ArmSpec

# ── MJCF-derived constants ──────────────────────────────────────────────────
_ARM_JOINTS = [f"joint{i}" for i in range(1, 8)]
_ARM_ACTS = [f"act{i}" for i in range(1, 8)]
_JOINT_LIMITS = {
    "joint1": (-6.28319, 6.28319),
    "joint2": (-2.059, 2.0944),
    "joint3": (-6.28319, 6.28319),
    "joint4": (-0.19198, 3.927),
    "joint5": (-6.28319, 6.28319),
    "joint6": (-1.69297, 3.14159),
    "joint7": (-6.28319, 6.28319),
}
_GRIPPER_JOINTS = [
    "left_driver_joint",
    "right_driver_joint",
    "left_finger_joint",
    "right_finger_joint",
    "left_inner_knuckle_joint",
    "right_inner_knuckle_joint",
]
_APERTURE_JOINT = "left_driver_joint"  # representative jaw joint (0..0.85 rad)
_TARGET_GEOM = "fixed_contact_geom"
_TARGET_BODY = "fixed_contact_target"


class CapabilityError(RuntimeError):
    """Bounded capability failure; carries a machine-readable reason."""

    def __init__(self, reason: str, **info):
        super().__init__(reason)
        self.reason = reason
        self.info = info


def build():
    """Construct the generated Robot bound to the capability scene MJCF."""
    spec = ArmSpec(
        ee_site_name="link_tcp",
        ee_body_name="xarm_gripper_base_link",
        arm_joint_names=_ARM_JOINTS,
        arm_actuator_names=_ARM_ACTS,
        joint_limits=_JOINT_LIMITS,
        gripper_actuator_names=["gripper"],
        gripper_joint_names=_GRIPPER_JOINTS,
        gripper_close_ctrl=0.0,
        gripper_open_ctrl=255.0,
        grasp_backend="noop",
        ik_damping=1e-3,
        ik_max_iter=300,
        ik_tolerance=1e-4,
        ik_step_clamp=0.2,
        ik_raise_on_unreachable=False,
        sim_dt=0.002,
    )
    return Robot.from_mjcf("mjcf.xml", spec=spec)


class Robot(ArmSerialDLSSkeleton):
    """Generated xArm7 capability driver."""

    def __init__(self, model, data, spec):
        super().__init__(model, data, spec)
        mj = self._mj
        m = self.model
        self._gid_ap = m.jnt_qposadr[
            mj.mj_name2id(m, mj.mjtObj.mjOBJ_JOINT, _APERTURE_JOINT)
        ]
        self._grip_aid = mj.mj_name2id(m, mj.mjtObj.mjOBJ_ACTUATOR, "gripper")
        lo, hi = m.actuator_ctrlrange[self._grip_aid]
        self._grip_lo, self._grip_hi = float(lo), float(hi)
        self._ap_closed, self._ap_open = 0.0, 0.85
        self._target_gid = mj.mj_name2id(m, mj.mjtObj.mjOBJ_GEOM, _TARGET_GEOM)
        self._target_bid = mj.mj_name2id(m, mj.mjtObj.mjOBJ_BODY, _TARGET_BODY)
        self._tool_geoms = self._discover_tool_geoms()
        self._dt = float(m.opt.timestep)
        self.set_arm_actuators(self.get_joint_positions())
        self.set_gripper_control(float(self.data.ctrl[self._grip_aid]))

    # ── binding helpers ─────────────────────────────────────────────────
    def _discover_tool_geoms(self) -> set[int]:
        m = self.model
        tool = set()
        base = self._mj.mj_name2id(m, self._mj.mjtObj.mjOBJ_BODY, "link7")
        for gi in range(m.ngeom):
            bid = int(m.geom_bodyid[gi])
            if gi in (0, self._target_gid):
                continue
            if int(m.geom_contype[gi]) == 0 and int(m.geom_conaffinity[gi]) == 0:
                continue  # disabled visual geom
            if bid >= base:
                tool.add(gi)
        return tool

    def _clamp_arm(self, q):
        return np.clip(np.asarray(q, dtype=np.float64), self._q_lo, self._q_hi)

    def _state_finite(self) -> bool:
        return bool(
            np.all(np.isfinite(self.get_joint_positions()))
            and np.all(np.isfinite(self.get_joint_velocities()))
            and np.all(np.isfinite(self._ee_pos_now()))
        )

    def _aperture_fraction(self) -> float:
        self._mj.mj_forward(self.model, self.data)
        val = float(self.data.qpos[self._gid_ap])
        span = self._ap_open - self._ap_closed
        return float(np.clip((val - self._ap_closed) / span, 0.0, 1.0))

    def _solve_ik(self, target_xyz, q_init=None):
        """DLS IK on scratch data; returns (q, residual_m)."""
        q = self.ik(np.asarray(target_xyz, float), q_init=q_init,
                    raise_on_unreachable=False)
        res = float(np.linalg.norm(self.fk(q)["pos"]
                                   - np.asarray(target_xyz, float)))
        return self._clamp_arm(q), res

    # ── closed-loop cartesian regulation ────────────────────────────────
    def _regulate_to(self, target_xyz, max_duration_s, *, hold_s, tol,
                     ik_res_gate=0.02):
        """Servo the EE toward target_xyz within max_duration_s; require the
        measured position error <= tol continuously for hold_s.  Commanded
        joint targets slew toward the IK solution (bounded per tick) so the
        native position controller regulates closed-loop off measured joints.
        Returns (ok, best_error).  Holds the gripper command constant.
        """
        target = np.asarray(target_xyz, dtype=np.float64).reshape(3)
        grip_hold = float(self.data.ctrl[self._grip_aid])
        q_sol, res = self._solve_ik(target, q_init=self.get_joint_positions())
        if res > ik_res_gate:
            q_sol, res = self._solve_ik(target, q_init=self._home_q)
        if not np.isfinite(res) or res > ik_res_gate:
            raise CapabilityError("target_unreachable_ik",
                                  residual=res, target=target.tolist())

        deadline = float(self.data.time) + float(max_duration_s)
        q_cmd = np.array([self.data.ctrl[a] for a in self._arm_actuator_ids],
                         float)
        max_step = 0.08  # rad per tick cap; large enough to reach steady state
        hold_start = None
        best = float("inf")
        while float(self.data.time) <= deadline + 1e-9:
            dq = q_sol - q_cmd
            n = float(np.linalg.norm(dq))
            if n > max_step:
                dq *= max_step / n
            q_cmd = self._clamp_arm(q_cmd + dq)
            self.set_arm_actuators(q_cmd)
            self.data.ctrl[self._grip_aid] = grip_hold
            self.step(1)
            err = float(np.linalg.norm(self._ee_pos_now() - target))
            best = min(best, err)
            if err <= tol:
                if hold_start is None:
                    hold_start = float(self.data.time)
                elif float(self.data.time) - hold_start >= hold_s - 1e-9:
                    return True, err
            else:
                hold_start = None
        return False, best

    # ── contact helpers ─────────────────────────────────────────────────
    def _tool_target_contacts(self):
        out = []
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            g1, g2 = int(c.geom1), int(c.geom2)
            if self._target_gid in (g1, g2) and (
                    g1 in self._tool_geoms or g2 in self._tool_geoms):
                out.append(c)
        return out

    def _unrelated_tool_contacts(self) -> int:
        n = 0
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            g1, g2 = int(c.geom1), int(c.geom2)
            t1, t2 = g1 in self._tool_geoms, g2 in self._tool_geoms
            if not (t1 or t2):
                continue
            if self._target_gid in (g1, g2) or (t1 and t2):
                continue  # target contact or internal gripper contact
            n += 1
        return n

    # ── A1: move_end_effector_to_position ───────────────────────────────
    def move_end_effector_to_position(self, request):
        if not self._state_finite():
            raise CapabilityError("state_not_finite")
        tgt = np.asarray(request["target_position_m"], dtype=np.float64)
        dur = float(request["max_duration_s"])
        if tgt.shape != (3,) or not np.all(np.isfinite(tgt)):
            raise CapabilityError("bad_target")
        ok, err = self._regulate_to(tgt, dur, hold_s=0.5, tol=0.015)
        if not ok:
            raise CapabilityError("deadline_or_limit", error_m=err)
        return {"ok": True, "final_error_m": err,
                "ee_position_m": self._ee_pos_now().tolist()}

    # ── A2: trace_cartesian_path ────────────────────────────────────────
    def trace_cartesian_path(self, request):
        if not self._state_finite():
            raise CapabilityError("state_not_finite")
        wps = [np.asarray(w, dtype=np.float64) for w in request["waypoints_m"]]
        seg_dur = float(request["max_duration_per_segment_s"])
        if any(w.shape != (3,) or not np.all(np.isfinite(w)) for w in wps):
            raise CapabilityError("bad_waypoint")
        err = float("inf")
        for idx, wp in enumerate(wps):
            terminal = idx == len(wps) - 1
            tol = 0.015 if terminal else 0.018
            hold = 0.5 if terminal else 0.03
            ok, err = self._regulate_to(wp, seg_dur, hold_s=hold, tol=tol)
            if not ok:
                raise CapabilityError("segment_failed", index=idx, error_m=err)
        return {"ok": True, "final_error_m": err, "visited": len(wps)}

    # ── A3: set_gripper_opening ─────────────────────────────────────────
    def set_gripper_opening(self, request):
        if not np.all(np.isfinite(self.get_joint_positions())):
            raise CapabilityError("state_not_finite")
        frac = float(request["opening_fraction"])
        dur = float(request["max_duration_s"])
        if not (0.0 <= frac <= 1.0) or not np.isfinite(frac):
            raise CapabilityError("bad_fraction")
        arm_hold = np.array([self.data.ctrl[a] for a in self._arm_actuator_ids],
                            float)
        cmd = self._grip_lo + frac * (self._grip_hi - self._grip_lo)
        self.set_gripper_control(cmd)
        deadline = float(self.data.time) + dur
        hold_start = None
        best = float("inf")
        while float(self.data.time) <= deadline + 1e-9:
            self.set_arm_actuators(arm_hold)
            self.set_gripper_control(cmd)
            self.step(1)
            err = abs(self._aperture_fraction() - frac)
            best = min(best, err)
            if err <= 0.1:
                if hold_start is None:
                    hold_start = float(self.data.time)
                elif float(self.data.time) - hold_start >= 0.25 - 1e-9:
                    return {"ok": True, "aperture_error": err,
                            "aperture_fraction": self._aperture_fraction()}
            else:
                hold_start = None
        raise CapabilityError("gripper_deadline", error=best)
