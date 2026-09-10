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

The native position actuators have a steady-state tracking offset under
gravity, so raw IK targets settle ~0.03 m from goal.  Every cartesian
regulation therefore uses an outer-loop Cartesian integral correction: the
IK target is re-biased by the measured world-frame EE error until the true
measured error meets the criterion tolerance.

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
        self._BIAS_PERIOD = 60  # ticks between integral bias updates
        self.set_arm_actuators(self.get_joint_positions())
        self.set_gripper_control(float(self.data.ctrl[self._grip_aid]))

    # ── binding helpers ─────────────────────────────────────────────────────
    def _discover_tool_geoms(self) -> set:
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
        # Probed: driver_joint ~0 -> jaws OPEN (max separation); driver_joint
        # ~0.85 -> jaws CLOSED.  Opening fraction is therefore inverted.
        closed_frac = float(np.clip((val - self._ap_closed) / span, 0.0, 1.0))
        return float(1.0 - closed_frac)

    def _solve_ik(self, target_xyz, q_init=None):
        """DLS IK on scratch data; returns (q, residual_m)."""
        q = self.ik(np.asarray(target_xyz, float), q_init=q_init,
                    raise_on_unreachable=False)
        res = float(np.linalg.norm(self.fk(q)["pos"]
                                   - np.asarray(target_xyz, float)))
        return self._clamp_arm(q), res

    def _arm_ctrl(self):
        return np.array([self.data.ctrl[a] for a in self._arm_actuator_ids],
                        float)

    # ── closed-loop cartesian regulation (integral Cartesian correction) ────
    def _servo_step(self, target, bias, q_cmd, grip_hold, max_step, tick):
        """One control tick: IK toward (target+bias), slew q_cmd, step, and
        update the integral bias on a slow timescale so the loop settles
        before each correction.  Returns (q_cmd, bias, measured_error, tick)."""
        goal = target + bias
        q_sol, res = self._solve_ik(goal, q_init=q_cmd)
        if np.isfinite(res) and res <= 0.03:
            dq = q_sol - q_cmd
            n = float(np.linalg.norm(dq))
            if n > max_step:
                dq *= max_step / n
            q_cmd = self._clamp_arm(q_cmd + dq)
        self.set_arm_actuators(q_cmd)
        self.data.ctrl[self._grip_aid] = grip_hold
        self.step(1)
        err_vec = target - self._ee_pos_now()
        err = float(np.linalg.norm(err_vec))
        tick += 1
        if tick >= self._BIAS_PERIOD:
            bias = np.clip(bias + 0.6 * err_vec, -0.15, 0.15)
            tick = 0
        return q_cmd, bias, err, tick

    def _regulate_to(self, target_xyz, max_duration_s, *, hold_s, tol,
                     ik_res_gate=0.02):
        """Servo the EE toward target_xyz within max_duration_s; require the
        measured position error <= tol continuously for hold_s.  Uses an
        outer Cartesian integral correction so the true measured error meets
        the criterion despite actuator steady-state offset.  Holds gripper.
        Returns (ok, best_error)."""
        target = np.asarray(target_xyz, dtype=np.float64).reshape(3)
        grip_hold = float(self.data.ctrl[self._grip_aid])
        _, res = self._solve_ik(target, q_init=self.get_joint_positions())
        if not np.isfinite(res) or res > ik_res_gate:
            raise CapabilityError("target_unreachable_ik",
                                  residual=res, target=target.tolist())
        deadline = float(self.data.time) + float(max_duration_s)
        q_cmd = self._arm_ctrl()
        bias = np.zeros(3)
        max_step = 0.08
        hold_start = None
        best = float("inf")
        tick = 0
        while float(self.data.time) <= deadline + 1e-9:
            q_cmd, bias, err, tick = self._servo_step(
                target, bias, q_cmd, grip_hold, max_step, tick)
            best = min(best, err)
            if err <= tol:
                if hold_start is None:
                    hold_start = float(self.data.time)
                elif float(self.data.time) - hold_start >= hold_s - 1e-9:
                    return True, err
            else:
                hold_start = None
        return False, best

    # ── contact helpers ─────────────────────────────────────────────────────
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

    # ── A1: move_end_effector_to_position ───────────────────────────────────
    def move_end_effector_to_position(self, request):
        if not self._state_finite():
            raise CapabilityError("state_not_finite")
        tgt = np.asarray(request["target_position_m"], dtype=np.float64)
        dur = float(request["max_duration_s"])
        if tgt.shape != (3,) or not np.all(np.isfinite(tgt)):
            raise CapabilityError("bad_target")
        ok, err = self._regulate_to(tgt, dur, hold_s=0.5, tol=0.014)
        if not ok:
            raise CapabilityError("deadline_or_limit", error_m=err)
        return {"ok": True, "final_error_m": err,
                "ee_position_m": self._ee_pos_now().tolist()}

    # ── A2: trace_cartesian_path ─────────────────────────────────────────────
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
            tol = 0.014 if terminal else 0.016
            hold = 0.5 if terminal else 0.02
            ok, err = self._regulate_to(wp, seg_dur, hold_s=hold, tol=tol)
            if not ok:
                raise CapabilityError("segment_failed", index=idx, error_m=err)
        return {"ok": True, "final_error_m": err, "visited": len(wps)}

    # ── A3: set_gripper_opening ──────────────────────────────────────────────
    def set_gripper_opening(self, request):
        if not np.all(np.isfinite(self.get_joint_positions())):
            raise CapabilityError("state_not_finite")
        frac = float(request["opening_fraction"])
        dur = float(request["max_duration_s"])
        if not (0.0 <= frac <= 1.0) or not np.isfinite(frac):
            raise CapabilityError("bad_fraction")
        arm_hold = self._arm_ctrl()
        # Opening fraction is inverted vs native ctrl: frac=1 (open) -> ctrl_lo
        # (driver_joint ~0), frac=0 (closed) -> ctrl_hi (driver_joint ~0.85).
        cmd = self._grip_hi - frac * (self._grip_hi - self._grip_lo)
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

    # ── A4 helpers ──────────────────────────────────────────────────────────
    def _reach_contact_free(self, target_xyz, max_duration_s, *, tol, dwell_s):
        """Servo to target_xyz (integral-corrected) and require a contact-free
        measured hold of dwell_s within tol.  Returns (p_start, q_cmd, bias,
        grip_hold).  Raises on deadline/limit/premature contact."""
        target = np.asarray(target_xyz, float).reshape(3)
        grip_hold = float(self.data.ctrl[self._grip_aid])
        _, res = self._solve_ik(target, q_init=self.get_joint_positions())
        if not np.isfinite(res) or res > 0.02:
            raise CapabilityError("precontact_unreachable", residual=res)
        deadline = float(self.data.time) + float(max_duration_s)
        q_cmd = self._arm_ctrl()
        bias = np.zeros(3)
        hold_start = None
        err = float("inf")
        tick = 0
        while float(self.data.time) <= deadline + 1e-9:
            q_cmd, bias, err, tick = self._servo_step(
                target, bias, q_cmd, grip_hold, 0.06, tick)
            if len(self._tool_target_contacts()) > 0:
                raise CapabilityError("contact_before_precontact")
            if err <= tol:
                if hold_start is None:
                    hold_start = float(self.data.time)
                elif float(self.data.time) - hold_start >= dwell_s - 1e-9:
                    return (self._ee_pos_now().copy(), q_cmd.copy(),
                            bias.copy(), grip_hold)
            else:
                hold_start = None
        raise CapabilityError("precontact_deadline", error_m=err)

    # ── A4: approach_until_contact ──────────────────────────────────────────
    def approach_until_contact(self, request):
        if not self._state_finite():
            raise CapabilityError("state_not_finite")
        pre = np.asarray(request["precontact_position_m"], float).reshape(3)
        d = np.asarray(request["approach_direction_unit"], float).reshape(3)
        max_travel = float(request["max_travel_m"])
        max_speed = float(request["max_approach_speed_m_s"])
        dur = float(request["max_duration_s"])
        dn = float(np.linalg.norm(d))
        if not np.all(np.isfinite(pre)) or dn < 1e-9 or not np.isfinite(dn):
            raise CapabilityError("bad_request")
        d = d / dn
        deadline = float(self.data.time) + dur
        # Phase 1: reach precontact, contact-free dwell >= 0.1 s.
        p_start, q_cmd, bias, grip_hold = self._reach_contact_free(
            pre, min(dur * 0.7, dur - 1.0) if dur > 1.5 else dur * 0.7,
            tol=0.013, dwell_s=0.12)
        # Phase 2: creep along the declared ray with a setpoint advancing at a
        # fixed slow axial velocity from p_start.  The setpoint stays exactly
        # on the ray (zero lateral component) so the servo keeps lateral drift
        # small, and the slow rate keeps tool surface speed well under limit.
        # Advance a setpoint that stays exactly on the declared ray at a slow
        # fixed axial velocity.  The setpoint is pure p_start + d*cmd_s (zero
        # lateral), and the per-tick joint step is capped so the tool tracks
        # smoothly, keeping surface relative speed well under the requested
        # limit and lateral deviation small.  We also keep re-biasing the ray
        # setpoint's lateral component via the measured lateral error so the
        # tool is pulled back onto the ray as it advances.
        cmd_speed = max(0.004, min(max_speed * 0.30, 0.010))
        creep_start = float(self.data.time)
        run_max_s = 0.0
        contacted = False
        lat_bias = np.zeros(3)
        lat_tick = 0
        while float(self.data.time) <= deadline + 1e-9:
            p_meas = self._ee_pos_now()
            rel = p_meas - p_start
            s = float(np.dot(rel, d))
            run_max_s = max(run_max_s, s)
            if len(self._tool_target_contacts()) > 0:
                contacted = True
                break
            if self._unrelated_tool_contacts() > 0:
                raise CapabilityError("unrelated_contact")
            if run_max_s > max_travel + 0.001:
                raise CapabilityError("travel_exhausted", s=run_max_s)
            elapsed = float(self.data.time) - creep_start
            cmd_s = min(cmd_speed * elapsed, max_travel)
            # correct residual lateral drift back onto the ray (slow integral)
            lat_tick += 1
            if lat_tick >= 40:
                lateral = rel - s * d
                lat_bias = np.clip(lat_bias - 0.4 * lateral, -0.02, 0.02)
                lat_tick = 0
            setpt = p_start + d * cmd_s + lat_bias
            q_sol, res = self._solve_ik(setpt, q_init=q_cmd)
            if np.isfinite(res) and res <= 0.03:
                dq = q_sol - q_cmd
                n = float(np.linalg.norm(dq))
                cap = cmd_speed * self._dt * 5.0
                if n > cap:
                    dq *= cap / n
                q_cmd = self._clamp_arm(q_cmd + dq)
            self.set_arm_actuators(q_cmd)
            self.data.ctrl[self._grip_aid] = grip_hold
            self.step(1)
        if not contacted:
            raise CapabilityError("no_contact_within_deadline")
        # Phase 3: stop advancing, hold contact >= 0.1 s at low speed.
        hold_q = q_cmd.copy()
        prev_p = self._ee_pos_now().copy()
        hold_start = float(self.data.time)
        max_post_speed = 0.0
        while float(self.data.time) <= deadline + 1e-9:
            self.set_arm_actuators(hold_q)
            self.data.ctrl[self._grip_aid] = grip_hold
            self.step(1)
            p_now = self._ee_pos_now()
            spd = float(np.linalg.norm(p_now - prev_p)) / self._dt
            prev_p = p_now.copy()
            max_post_speed = max(max_post_speed, spd)
            if self._unrelated_tool_contacts() > 0:
                raise CapabilityError("unrelated_contact_hold")
            if float(self.data.time) - hold_start >= 0.12 - 1e-9:
                return {"ok": True, "axial_progress_m": run_max_s,
                        "post_contact_speed_m_s": max_post_speed,
                        "contact": True}
        raise CapabilityError("contact_hold_deadline",
                              post_speed=max_post_speed)

    # ── A5: move_cartesian_offset_and_return ────────────────────────────────
    def move_cartesian_offset_and_return(self, request):
        """Move by a robot-base offset then return to the captured start.

        The xArm7 has a fixed base aligned with the world frame, so the
        base-frame offset equals a world-frame displacement.  The start
        world pose is captured at the call start; the outbound leg must
        finish (with a hold) before the return leg begins.
        """
        if not self._state_finite():
            raise CapabilityError("state_not_finite")
        off = np.asarray(request["offset_robot_base_m"], float).reshape(3)
        leg_dur = float(request["max_duration_per_leg_s"])
        if not np.all(np.isfinite(off)):
            raise CapabilityError("bad_offset")
        p_start = self._ee_pos_now().copy()
        p_out = p_start + off
        disp_req = float(np.linalg.norm(off))
        ok, err_out = self._regulate_to(p_out, leg_dur, hold_s=0.25, tol=0.014)
        if not ok:
            raise CapabilityError("outbound_failed", error_m=err_out)
        p_out_meas = self._ee_pos_now().copy()
        disp_ach = float(np.linalg.norm(p_out_meas - p_start))
        if disp_req > 1e-6 and disp_ach < 0.8 * disp_req:
            raise CapabilityError("insufficient_displacement",
                                  achieved=disp_ach, requested=disp_req)
        ok, err_ret = self._regulate_to(p_start, leg_dur, hold_s=0.5, tol=0.014)
        if not ok:
            raise CapabilityError("return_failed", error_m=err_ret)
        return {"ok": True, "outbound_error_m": err_out,
                "return_error_m": err_ret, "displacement_m": disp_ach}
