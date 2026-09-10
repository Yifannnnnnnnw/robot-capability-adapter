# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for kinova_gen3_robotiq_2f85.

Robot subclass of ArmSerialDLSSkeleton implementing the public capability
contract (A1..A5). Low-level IK / actuation / state come from the skeleton;
this module supplies request handling, closed-loop feedback, ordering, holds,
stopping, and bounded failure handling. All timing uses data.time (sim s).

Motion control notes:
- Regulation tasks (A1/A2 final/A5) use a Cartesian outer-loop integral
  corrector: the commanded IK target is nudged by the accumulated measured
  tool-position error so the ACTUAL tool reaches the requested point with
  near-zero steady-state error (the position actuators otherwise droop a few
  mm under gravity, which starved the A5 outbound displacement fraction).
- Slow, controlled Cartesian motion (A4 approach) uses a Jacobian
  pure-translation velocity servo keeping the actual surface speed near the
  commanded speed with negligible wrist rotation.
"""
from __future__ import annotations

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import (
    ArmSerialDLSSkeleton, IKUnreachableError)
from auto_adapter.skeletons.base import ArmSpec

_ARM_JOINTS = ["joint_1", "joint_2", "joint_3", "joint_4",
               "joint_5", "joint_6", "joint_7"]
_ARM_ACTS = list(_ARM_JOINTS)
_LIMITS = {
    "joint_1": (-3.14159, 3.14159),
    "joint_2": (-2.24, 2.24),
    "joint_3": (-3.14159, 3.14159),
    "joint_4": (-2.57, 2.57),
    "joint_5": (-3.14159, 3.14159),
    "joint_6": (-2.09, 2.09),
    "joint_7": (-3.14159, 3.14159),
}
_HOME_QPOS = [1.047, 0.262, 3.142, -2.269, 0.0, 0.96, 1.571]


class CapabilityError(RuntimeError):
    """Bounded capability failure with a machine-readable payload."""

    def __init__(self, capability_id, reason, detail=None):
        super().__init__(f"{capability_id}: {reason}")
        self.capability_id = capability_id
        self.reason = reason
        self.detail = detail or {}


class Robot(ArmSerialDLSSkeleton):
    """kinova_gen3 + robotiq 2f85 capability robot."""

    _APERTURE_OPEN = 0.0
    _APERTURE_CLOSED = 0.79

    def __init__(self, model, data, spec):
        super().__init__(model, data, spec)
        self._fingers_aid = self._gripper_actuator_ids[0]
        self._ctrl_lo, self._ctrl_hi = \
            self.model.actuator_ctrlrange[self._fingers_aid]
        self._site_id = self._mj.mj_name2id(
            self.model, self._mj.mjtObj.mjOBJ_SITE, "pinch_site")
        self._arm_dofs = [
            int(self.model.jnt_dofadr[self.model.actuator_trnid[a, 0]])
            for a in self._arm_actuator_ids]
        self._calibrate_aperture()

    # ---- gripper aperture calibration -------------------------------------
    def _calibrate_aperture(self):
        try:
            jid = self._gripper_joint_ids[0]
        except Exception:
            return
        adr = self.model.jnt_qposadr[jid]
        vals = {}
        for ctrl in (self._ctrl_lo, self._ctrl_hi):
            sc = self._mj.MjData(self.model)
            self._mj.mj_copyData(sc, self.model, self.data)
            sc.ctrl[self._fingers_aid] = float(ctrl)
            for _ in range(1500):
                self._mj.mj_step(self.model, sc)
            vals[ctrl] = float(sc.qpos[adr])
        self._APERTURE_OPEN = vals[self._ctrl_lo]
        self._APERTURE_CLOSED = vals[self._ctrl_hi]

    def _aperture_joint_id(self):
        return self._gripper_joint_ids[0]

    def _measured_opening_fraction(self):
        adr = self.model.jnt_qposadr[self._aperture_joint_id()]
        ang = float(self.data.qpos[adr])
        span = self._APERTURE_CLOSED - self._APERTURE_OPEN
        if abs(span) < 1e-9:
            return 0.0
        closed_frac = (ang - self._APERTURE_OPEN) / span
        return float(np.clip(1.0 - closed_frac, 0.0, 1.0))

    def _fraction_to_ctrl(self, opening_fraction):
        f = float(np.clip(opening_fraction, 0.0, 1.0))
        return self._ctrl_hi + f * (self._ctrl_lo - self._ctrl_hi)

    # ---- shared helpers ---------------------------------------------------
    def _dt(self):
        return float(self.model.opt.timestep)

    def _arm_cmd(self):
        return np.array([float(self.data.ctrl[a])
                         for a in self._arm_actuator_ids])

    def _hold_ctrl(self):
        self.set_arm_actuators(self._arm_cmd())

    def _grip_cmd(self):
        return float(self.data.ctrl[self._fingers_aid])

    def _check_state_finite(self, cap):
        q = self.get_joint_positions()
        v = self.get_joint_velocities()
        ee, _ = self.get_ee_pose()
        if not (np.all(np.isfinite(q)) and np.all(np.isfinite(v))
                and np.all(np.isfinite(ee))):
            raise CapabilityError(cap, "non-finite robot state")
        return ee

    def _ik_target(self, cap, target_xyz):
        try:
            q = self.ik(np.asarray(target_xyz, float),
                        q_init=self.get_joint_positions(),
                        raise_on_unreachable=True)
        except IKUnreachableError as e:
            raise CapabilityError(cap, "target unreachable",
                                  {"residual": getattr(e, "residual", None)})
        return np.clip(q, self._q_lo, self._q_hi)

    def _validate_num_array(self, cap, arr, n, lo, hi):
        a = np.asarray(arr, dtype=float)
        if a.shape != (n,) or not np.all(np.isfinite(a)):
            raise CapabilityError(cap, "malformed numeric array")
        if np.any(a < lo - 1e-9) or np.any(a > hi + 1e-9):
            raise CapabilityError(cap, "array out of bounds")
        return a

    def _validate_scalar(self, cap, val, lo, hi):
        try:
            v = float(val)
        except Exception:
            raise CapabilityError(cap, "scalar not numeric")
        if not np.isfinite(v) or v < lo - 1e-9 or v > hi + 1e-9:
            raise CapabilityError(cap, "scalar out of bounds")
        return v

    # ---- motion primitives ------------------------------------------------
    def _site_jacobian(self):
        """Position Jacobian of the EE site w.r.t. arm DoFs (live data)."""
        jacp = np.zeros((3, self.model.nv))
        self._mj.mj_jacSite(self.model, self.data, jacp, None, self._site_id)
        return jacp[:, self._arm_dofs]

    def _regulate_to(self, cap, target_xyz, deadline_t, tol=0.015,
                     hold_s=0.5, grip_ctrl=None, hold_margin=0.15,
                     cross_prev=None, cross_tol=None, ki=0.6,
                     resolve_every=6):
        """Command an IK target and step until the deadline, tracking the
        longest continuous window with error<=tol.

        An outer Cartesian integral corrector accumulates the measured
        tool-position error and re-solves IK toward (target + correction) so
        the ACTUAL tool converges to the requested point despite position
        actuator droop. Physics always advances through the actuators only.
        Returns once a hold of (hold_s + hold_margin) is achieved OR the
        deadline expires; raises if no window of at least hold_s occurred.
        """
        tgt = np.asarray(target_xyz, float)
        dt = self._dt()
        corr = np.zeros(3)
        q_goal = self._ik_target(cap, tgt)
        self.set_arm_actuators(q_goal)
        if grip_ctrl is not None:
            self.set_gripper_control(grip_ctrl)
        hold_acc = 0.0
        best_hold = 0.0
        best = float("inf")
        u = None
        if cross_prev is not None:
            seg = tgt - np.asarray(cross_prev, float)
            n = float(np.linalg.norm(seg))
            u = seg / n if n > 1e-9 else None
        k = 0
        while self.data.time < deadline_t - dt * 0.5:
            self.set_arm_actuators(q_goal)
            if grip_ctrl is not None:
                self.set_gripper_control(grip_ctrl)
            self.step(1)
            ee, _ = self.get_ee_pose()
            err_vec = tgt - ee
            err = float(np.linalg.norm(err_vec))
            best = min(best, err)
            # outer integral correction on the IK target (feed-forward only)
            k += 1
            if k % resolve_every == 0:
                corr = corr + ki * err_vec
                corr = np.clip(corr, -0.08, 0.08)
                try:
                    q_goal = self._ik_target(cap, tgt + corr)
                except CapabilityError:
                    pass
            if u is not None and cross_tol is not None:
                d = ee - np.asarray(cross_prev, float)
                s = float(np.dot(d, u))
                cross = float(np.linalg.norm(d - s * u))
                if cross > cross_tol + 1e-6:
                    raise CapabilityError(cap, "cross-track exceeded",
                                          {"cross": cross})
            if err <= tol:
                hold_acc += dt
                best_hold = max(best_hold, hold_acc)
                if hold_acc >= hold_s + hold_margin:
                    return {"error": err, "held": True, "hold_s": hold_acc}
            else:
                hold_acc = 0.0
        if best_hold >= hold_s:
            ee, _ = self.get_ee_pose()
            return {"error": float(np.linalg.norm(ee - tgt)),
                    "held": True, "hold_s": best_hold}
        raise CapabilityError(cap, "hold criterion not met before deadline",
                              {"best_error": best, "best_hold_s": best_hold})

    def _reach_point(self, cap, target_xyz, deadline_t, tol=0.015,
                     grip_ctrl=None, ki=0.6, resolve_every=6):
        """Drive toward target until within tol once (integral-corrected)."""
        tgt = np.asarray(target_xyz, float)
        dt = self._dt()
        corr = np.zeros(3)
        q_goal = self._ik_target(cap, tgt)
        self.set_arm_actuators(q_goal)
        best = float("inf")
        k = 0
        while self.data.time < deadline_t - dt * 0.5:
            self.set_arm_actuators(q_goal)
            if grip_ctrl is not None:
                self.set_gripper_control(grip_ctrl)
            self.step(1)
            ee, _ = self.get_ee_pose()
            err_vec = tgt - ee
            err = float(np.linalg.norm(err_vec))
            best = min(best, err)
            k += 1
            if k % resolve_every == 0:
                corr = np.clip(corr + ki * err_vec, -0.08, 0.08)
                try:
                    q_goal = self._ik_target(cap, tgt + corr)
                except CapabilityError:
                    pass
            if err <= tol:
                return err
        raise CapabilityError(cap, "waypoint not reached before deadline",
                              {"best_error": best})

    def _translate_velocity_step(self, direction_unit, speed, grip_ctrl=None):
        """Advance the tool by one dt at `speed` along direction_unit using a
        Jacobian pure-translation joint increment (minimal wrist rotation)."""
        dt = self._dt()
        prev, _ = self.get_ee_pose()
        J = self._site_jacobian()
        v = np.asarray(direction_unit, float) * float(speed)
        dq = np.linalg.pinv(J) @ (v * dt)
        qcmd = np.clip(self._arm_cmd() + dq, self._q_lo, self._q_hi)
        self.set_arm_actuators(qcmd)
        if grip_ctrl is not None:
            self.set_gripper_control(grip_ctrl)
        self.step(1)
        cur, _ = self.get_ee_pose()
        return float(np.linalg.norm(cur - prev))

    # ---- A1: move_end_effector_to_position --------------------------------
    def move_end_effector_to_position(self, request):
        cap = "A1"
        if not isinstance(request, dict):
            raise CapabilityError(cap, "request must be a mapping")
        tgt = self._validate_num_array(cap, request.get("target_position_m"),
                                       3, -1.0, 1.0)
        dur = self._validate_scalar(cap, request.get("max_duration_s"),
                                    0.25, 8.0)
        self._check_state_finite(cap)
        grip_hold = self._grip_cmd()
        t0 = float(self.data.time)
        res = self._regulate_to(cap, tgt, t0 + dur, tol=0.015, hold_s=0.5,
                                grip_ctrl=grip_hold)
        return {"capability_id": cap, "status": "ok",
                "final_error_m": res["error"], "hold_s": res["hold_s"]}

    # ---- A2: trace_cartesian_path -----------------------------------------
    def trace_cartesian_path(self, request):
        cap = "A2"
        if not isinstance(request, dict):
            raise CapabilityError(cap, "request must be a mapping")
        wps = request.get("waypoints_m")
        if not isinstance(wps, (list, tuple)) or not (2 <= len(wps) <= 8):
            raise CapabilityError(cap, "waypoints_m count invalid")
        pts = [self._validate_num_array(cap, w, 3, -1.0, 1.0) for w in wps]
        seg_dur = self._validate_scalar(
            cap, request.get("max_duration_per_segment_s"), 0.25, 5.0)
        self._check_state_finite(cap)
        grip = self._grip_cmd()
        prev, _ = self.get_ee_pose()
        last = None
        for i, tgt in enumerate(pts):
            deadline = float(self.data.time) + seg_dur
            if i == len(pts) - 1:
                res = self._regulate_to(cap, tgt, deadline, tol=0.015,
                                        hold_s=0.5, grip_ctrl=grip,
                                        cross_prev=prev, cross_tol=0.02)
                last = res["error"]
            else:
                last = self._reach_point(cap, tgt, deadline, tol=0.015,
                                         grip_ctrl=grip)
            prev = tgt
        return {"capability_id": cap, "status": "ok", "final_error_m": last}

    # ---- A3: set_gripper_opening ------------------------------------------
    def set_gripper_opening(self, request):
        cap = "A3"
        if not isinstance(request, dict):
            raise CapabilityError(cap, "request must be a mapping")
        frac = self._validate_scalar(cap, request.get("opening_fraction"),
                                     0.0, 1.0)
        dur = self._validate_scalar(cap, request.get("max_duration_s"),
                                    0.25, 8.0)
        ap0 = self._measured_opening_fraction()
        if not np.isfinite(ap0):
            raise CapabilityError(cap, "non-finite gripper state")
        # invariant: do not change the arm target; freeze arm actuators
        arm_cmd = self._arm_cmd()
        self.set_arm_actuators(arm_cmd)
        ctrl = self._fraction_to_ctrl(frac)
        self.set_gripper_control(ctrl)
        dt = self._dt()
        t0 = float(self.data.time)
        deadline = t0 + dur
        hold_acc = 0.0
        best_hold = 0.0
        best = float("inf")
        while self.data.time < deadline - dt * 0.5:
            self.set_arm_actuators(arm_cmd)
            self.set_gripper_control(ctrl)
            self.step(1)
            cur = self._measured_opening_fraction()
            err = abs(cur - frac)
            best = min(best, err)
            if err <= 0.10:
                hold_acc += dt
                best_hold = max(best_hold, hold_acc)
                if hold_acc >= 0.25 + 0.1:
                    return {"capability_id": cap, "status": "ok",
                            "opening_fraction": cur, "hold_s": hold_acc}
            else:
                hold_acc = 0.0
        if best_hold >= 0.25:
            return {"capability_id": cap, "status": "ok",
                    "opening_fraction": self._measured_opening_fraction(),
                    "hold_s": best_hold}
        raise CapabilityError(cap, "aperture hold not reached",
                              {"best_error": best, "best_hold_s": best_hold})

    # ---- A5: move_cartesian_offset_and_return -----------------------------
    def move_cartesian_offset_and_return(self, request):
        cap = "A5"
        if not isinstance(request, dict):
            raise CapabilityError(cap, "request must be a mapping")
        off = self._validate_num_array(cap, request.get("offset_robot_base_m"),
                                       3, -0.06, 0.06)
        leg_dur = self._validate_scalar(
            cap, request.get("max_duration_per_leg_s"), 0.25, 5.0)
        self._check_state_finite(cap)
        # capture robot-base frame at start
        bid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_BODY,
                                  "base_link")
        R = np.array(self.data.xmat[bid], float).reshape(3, 3) if bid >= 0 \
            else np.eye(3)
        start_ee, _ = self.get_ee_pose()
        grip = self._grip_cmd()
        world_off = R @ off
        outbound = start_ee + world_off
        req_disp = float(np.linalg.norm(world_off))
        # tight outbound tolerance so displacement fraction clears 0.8 even
        # for small offsets (need error < ~0.2*req_disp to guarantee 80%).
        out_tol = min(0.015, max(0.15 * req_disp, 0.002))
        # outbound leg: regulate to the offset target and hold >=0.25 s
        t0 = float(self.data.time)
        self._regulate_to(cap, outbound, t0 + leg_dur, tol=out_tol,
                          hold_s=0.25, grip_ctrl=grip, hold_margin=0.1)
        ee_out, _ = self.get_ee_pose()
        disp = float(np.linalg.norm(ee_out - start_ee))
        if req_disp > 1e-9 and disp < 0.8 * req_disp:
            raise CapabilityError(cap, "insufficient outbound displacement",
                                  {"disp": disp, "req": req_disp})
        # return leg (only after outbound completed)
        t1 = float(self.data.time)
        res = self._regulate_to(cap, start_ee, t1 + leg_dur, tol=0.015,
                                hold_s=0.5, grip_ctrl=grip)
        return {"capability_id": cap, "status": "ok",
                "outbound_disp_m": disp, "return_error_m": res["error"]}

    # ---- A4: approach_until_contact ---------------------------------------
    def _tool_geom_ids(self):
        ids = []
        for gi in range(self.model.ngeom):
            if (self.model.geom_contype[gi] == 0 and
                    self.model.geom_conaffinity[gi] == 0):
                continue
            bid = self.model.geom_bodyid[gi]
            name = self.model.body(bid).name
            if any(k in name for k in ("pad", "finger", "follower", "driver",
                                       "coupler", "spring", "gripper",
                                       "2f85")):
                ids.append(gi)
        return set(ids)

    def _target_geom_ids(self):
        gid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_GEOM,
                                  "fixed_contact_geom")
        return {gid} if gid >= 0 else set()

    def _tool_target_contacts(self, tool, target):
        found = []
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            if ((c.geom1 in tool and c.geom2 in target) or
                    (c.geom2 in tool and c.geom1 in target)):
                found.append(c)
        return found

    def _check_unrelated(self, cap, tool, target):
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            g1, g2 = c.geom1, c.geom2
            involves_tool = g1 in tool or g2 in tool
            involves_target = g1 in target or g2 in target
            if involves_tool and not involves_target:
                other = g2 if g1 in tool else g1
                if other in tool:
                    continue
                bid = self.model.geom_bodyid[other]
                raise CapabilityError(cap, "unrelated tool contact",
                                      {"geom": self.model.body(bid).name})

    def approach_until_contact(self, request):
        cap = "A4"
        if not isinstance(request, dict):
            raise CapabilityError(cap, "request must be a mapping")
        pre = self._validate_num_array(cap,
                                       request.get("precontact_position_m"),
                                       3, -1.0, 1.0)
        d = self._validate_num_array(cap,
                                     request.get("approach_direction_unit"),
                                     3, -1.0, 1.0)
        dn = float(np.linalg.norm(d))
        if dn < 1e-6:
            raise CapabilityError(cap, "approach direction is zero")
        d = d / dn
        max_travel = self._validate_scalar(cap, request.get("max_travel_m"),
                                            1e-12, 0.08)
        max_speed = self._validate_scalar(
            cap, request.get("max_approach_speed_m_s"), 1e-12, 0.05)
        dur = self._validate_scalar(cap, request.get("max_duration_s"),
                                    0.25, 8.0)
        self._check_state_finite(cap)
        tool = self._tool_geom_ids()
        target = self._target_geom_ids()
        if not target:
            raise CapabilityError(cap, "contact target unavailable")
        grip = self._grip_cmd()
        dt = self._dt()
        t0 = float(self.data.time)
        deadline = t0 + dur
        # phase 1: reach precontact, then dwell contact-free >= 0.1 s
        self._reach_point(cap, pre, deadline, tol=0.015, grip_ctrl=grip)
        q_park = self._arm_cmd()
        dwell = 0.0
        while self.data.time < deadline - dt * 0.5:
            self.set_arm_actuators(q_park)
            self.set_gripper_control(grip)
            self.step(1)
            ee, _ = self.get_ee_pose()
            if (np.linalg.norm(ee - pre) <= 0.015 and
                    not self._tool_target_contacts(tool, target)):
                dwell += dt
                if dwell >= 0.1 + 0.05:
                    break
            else:
                dwell = 0.0
        if dwell < 0.1:
            raise CapabilityError(cap, "precontact dwell not achieved")
        # phase 2: slow Jacobian approach along +d until contact
        self._approach_ray(cap, d, max_travel, max_speed, deadline,
                           tool, target, grip)
        return {"capability_id": cap, "status": "ok"}

    def _approach_ray(self, cap, d, max_travel, max_speed, deadline,
                      tool, target, grip):
        dt = self._dt()
        # ray origin = measured tool point at end of precontact dwell
        p_start, _ = self.get_ee_pose()
        # command speed reduced so the actual nearest-surface relative speed
        # (finger pads vs target) stays under max_approach_speed + 0.01.
        cmd_speed = min(max_speed, 0.015)
        max_s = -1e9
        contacted = False
        while self.data.time < deadline - dt * 0.5 and not contacted:
            ee, _ = self.get_ee_pose()
            rel = ee - p_start
            s = float(np.dot(rel, d))
            lateral = float(np.linalg.norm(rel - s * d))
            if lateral > 0.01:
                raise CapabilityError(cap, "lateral deviation exceeded",
                                      {"lateral": lateral})
            max_s = max(max_s, s)
            if s < max_s - 0.002:
                raise CapabilityError(cap, "excessive backtrack")
            if self._tool_target_contacts(tool, target):
                contacted = True
                break
            if s >= max_travel:
                raise CapabilityError(cap, "travel exhausted before contact")
            self._translate_velocity_step(d, cmd_speed, grip_ctrl=grip)
        if not contacted:
            raise CapabilityError(cap, "no contact before deadline")
        self._check_unrelated(cap, tool, target)
        # phase 3: freeze command, verify contact hold >= 0.1 s, slow, shallow
        self._hold_ctrl()
        q_hold = self._arm_cmd()
        hold = 0.0
        prev_ee, _ = self.get_ee_pose()
        while self.data.time < deadline - dt * 0.5:
            self.set_arm_actuators(q_hold)
            self.set_gripper_control(grip)
            self.step(1)
            ee, _ = self.get_ee_pose()
            v = float(np.linalg.norm(ee - prev_ee)) / dt
            prev_ee = ee
            cons = self._tool_target_contacts(tool, target)
            if not cons:
                hold = 0.0
                continue
            if v > 0.02:
                raise CapabilityError(cap, "post-contact speed too high",
                                      {"v": v})
            pen = max((abs(c.dist) for c in cons if c.dist < 0), default=0.0)
            if pen > 0.005:
                raise CapabilityError(cap, "penetration too deep",
                                      {"pen": pen})
            self._check_unrelated(cap, tool, target)
            hold += dt
            if hold >= 0.1 + 0.05:
                return
        if hold < 0.1:
            raise CapabilityError(cap, "contact hold not sustained")


def _make_spec():
    return ArmSpec(
        ee_site_name="pinch_site",
        ee_body_name="bracelet_link",
        arm_joint_names=list(_ARM_JOINTS),
        arm_actuator_names=list(_ARM_ACTS),
        joint_limits=dict(_LIMITS),
        home_qpos=list(_HOME_QPOS),
        ik_damping=1e-3,
        ik_max_iter=100,
        ik_tolerance=1e-3,
        ik_step_clamp=0.3,
        ik_raise_on_unreachable=True,
        gripper_actuator_names=["fingers_actuator"],
        gripper_joint_names=["right_driver_joint"],
        gripper_close_ctrl=255.0,
        gripper_open_ctrl=0.0,
        grasp_backend="noop",
        sim_dt=0.002,
    )


def build():
    """Construct the capability Robot from the packaged MJCF scene."""
    return Robot.from_mjcf("mjcf.xml", spec=_make_spec())
