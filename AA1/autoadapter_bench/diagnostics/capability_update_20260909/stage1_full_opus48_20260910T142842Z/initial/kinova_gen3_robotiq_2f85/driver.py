# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for kinova_gen3_robotiq_2f85.

Robot subclass of ArmSerialDLSSkeleton implementing the public capability
contract (A1..A5). Low-level IK / actuation / state come from the skeleton;
this module supplies request handling, closed-loop feedback, ordering, holds,
stopping, and bounded failure handling. All timing uses data.time (sim s).
"""
from __future__ import annotations

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton, IKUnreachableError
from auto_adapter.skeletons.base import ArmSpec

_ARM_JOINTS = ["joint_1", "joint_2", "joint_3", "joint_4",
               "joint_5", "joint_6", "joint_7"]
_ARM_ACTS = list(_ARM_JOINTS)
# Continuous joints (unlimited in MJCF) clamped to a safe wrap for IK.
_LIMITS = {
    "joint_1": (-3.14159, 3.14159),
    "joint_2": (-2.24, 2.24),
    "joint_3": (-3.14159, 3.14159),
    "joint_4": (-2.57, 2.57),
    "joint_5": (-3.14159, 3.14159),
    "joint_6": (-2.09, 2.09),
    "joint_7": (-3.14159, 3.14159),
}


class CapabilityError(RuntimeError):
    """Bounded capability failure with a machine-readable payload."""

    def __init__(self, capability_id, reason, detail=None):
        super().__init__(f"{capability_id}: {reason}")
        self.capability_id = capability_id
        self.reason = reason
        self.detail = detail or {}


class Robot(ArmSerialDLSSkeleton):
    """kinova_gen3 + robotiq 2f85 capability robot."""

    # aperture endpoints probed from the model (driver joint angle)
    _APERTURE_OPEN = 0.0
    _APERTURE_CLOSED = 0.79

    def __init__(self, model, data, spec):
        super().__init__(model, data, spec)
        self._fingers_aid = self._gripper_actuator_ids[0]
        self._ctrl_lo, self._ctrl_hi = self.model.actuator_ctrlrange[self._fingers_aid]
        self._calibrate_aperture()

    # ---- gripper aperture calibration -------------------------------------
    def _calibrate_aperture(self):
        """Probe driver-joint travel with scratch data (never live state)."""
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
        # ctrl_lo -> open, ctrl_hi -> closed (Robotiq convention)
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
        # opening=1 -> ctrl_lo (open); opening=0 -> ctrl_hi (closed)
        f = float(np.clip(opening_fraction, 0.0, 1.0))
        return self._ctrl_hi + f * (self._ctrl_lo - self._ctrl_hi)

    # ---- shared helpers ---------------------------------------------------
    def _dt(self):
        return float(self.model.opt.timestep)

    def _hold_ctrl(self):
        """Freeze arm actuators at their current commanded targets."""
        cur = np.array([float(self.data.ctrl[a]) for a in self._arm_actuator_ids])
        self.set_arm_actuators(cur)

    def _check_state_finite(self, cap):
        q = self.get_joint_positions()
        v = self.get_joint_velocities()
        ee, _ = self.get_ee_pose()
        if not (np.all(np.isfinite(q)) and np.all(np.isfinite(v))
                and np.all(np.isfinite(ee))):
            raise CapabilityError(cap, "non-finite robot state")
        return ee

    def _ik_target(self, cap, target_xyz):
        """Solve IK on scratch data; return clamped q or raise bounded error."""
        try:
            q = self.ik(np.asarray(target_xyz, float),
                        q_init=self.get_joint_positions(),
                        raise_on_unreachable=True)
        except IKUnreachableError as e:
            raise CapabilityError(cap, "target unreachable",
                                  {"residual": e.residual})
        return np.clip(q, self._q_lo, self._q_hi)

    def _servo_to(self, cap, target_xyz, deadline_t, tol, hold_s,
                  gripper_ctrl=None, track_lateral=None):
        """Closed-loop: command IK target, step, watch position error hold.

        Returns dict with achieved error / held status. Raises CapabilityError
        if the hold criterion is not met before the deadline.
        """
        q_goal = self._ik_target(cap, target_xyz)
        self.set_arm_actuators(q_goal)
        if gripper_ctrl is not None:
            self.set_gripper_control(gripper_ctrl)
        dt = self._dt()
        hold_acc = 0.0
        best = float("inf")
        while self.data.time < deadline_t:
            self.step(1)
            ee, _ = self.get_ee_pose()
            err = float(np.linalg.norm(ee - np.asarray(target_xyz, float)))
            best = min(best, err)
            if err <= tol:
                hold_acc += dt
                if hold_acc >= hold_s:
                    return {"error": err, "held": True}
            else:
                hold_acc = 0.0
        raise CapabilityError(cap, "deadline exceeded before hold",
                              {"best_error": best, "tol": tol})

    def _validate_num_array(self, cap, arr, n, lo, hi):
        a = np.asarray(arr, dtype=float)
        if a.shape != (n,) or not np.all(np.isfinite(a)):
            raise CapabilityError(cap, "malformed numeric array")
        if np.any(a < lo - 1e-9) or np.any(a > hi + 1e-9):
            raise CapabilityError(cap, "array out of bounds")
        return a

    def _validate_duration(self, cap, val, lo, hi):
        try:
            v = float(val)
        except Exception:
            raise CapabilityError(cap, "duration not numeric")
        if not np.isfinite(v) or v < lo - 1e-9 or v > hi + 1e-9:
            raise CapabilityError(cap, "duration out of bounds")
        return v

    # ---- A1: move_end_effector_to_position --------------------------------
    def move_end_effector_to_position(self, request):
        cap = "A1"
        if not isinstance(request, dict):
            raise CapabilityError(cap, "request must be a mapping")
        tgt = self._validate_num_array(cap, request.get("target_position_m"),
                                       3, -1.0, 1.0)
        dur = self._validate_duration(cap, request.get("max_duration_s"), 0.25, 8.0)
        self._check_state_finite(cap)
        # keep aperture fixed (invariant): record current gripper command
        grip_hold = float(self.data.ctrl[self._fingers_aid])
        t0 = float(self.data.time)
        res = self._servo_to(cap, tgt, t0 + dur, tol=0.015, hold_s=0.5,
                             gripper_ctrl=grip_hold)
        self._hold_ctrl()
        return {"capability_id": cap, "status": "ok",
                "final_error_m": res["error"]}

    # ---- A3: set_gripper_opening ------------------------------------------
    def set_gripper_opening(self, request):
        cap = "A3"
        if not isinstance(request, dict):
            raise CapabilityError(cap, "request must be a mapping")
        frac = request.get("opening_fraction")
        try:
            frac = float(frac)
        except Exception:
            raise CapabilityError(cap, "opening_fraction not numeric")
        if not np.isfinite(frac) or frac < -1e-9 or frac > 1.0 + 1e-9:
            raise CapabilityError(cap, "opening_fraction out of bounds")
        dur = self._validate_duration(cap, request.get("max_duration_s"), 0.25, 8.0)
        # gripper state available & finite
        ap0 = self._measured_opening_fraction()
        if not np.isfinite(ap0):
            raise CapabilityError(cap, "non-finite gripper state")
        # invariant: do NOT change arm target; freeze arm actuators
        self._hold_ctrl()
        ctrl = self._fraction_to_ctrl(frac)
        self.set_gripper_control(ctrl)
        dt = self._dt()
        t0 = float(self.data.time)
        deadline = t0 + dur
        hold_acc = 0.0
        best = float("inf")
        while self.data.time < deadline:
            self.step(1)
            self._hold_ctrl()  # keep arm parked, re-apply gripper command
            self.set_gripper_control(ctrl)
            cur = self._measured_opening_fraction()
            err = abs(cur - frac)
            best = min(best, err)
            if err <= 0.10:
                hold_acc += dt
                if hold_acc >= 0.25:
                    return {"capability_id": cap, "status": "ok",
                            "opening_fraction": cur}
            else:
                hold_acc = 0.0
        raise CapabilityError(cap, "aperture hold not reached",
                              {"best_error": best})

    # ---- A2: trace_cartesian_path -----------------------------------------
    def _segment_servo(self, cap, target, deadline_t, cross_tol, prev,
                       final=False):
        """Drive toward `target`; enforce cross-track vs the prev->target line
        during transit. On final segment require terminal hold."""
        q_goal = self._ik_target(cap, target)
        self.set_arm_actuators(q_goal)
        grip = float(self.data.ctrl[self._fingers_aid])
        self.set_gripper_control(grip)
        dt = self._dt()
        seg = np.asarray(target, float) - np.asarray(prev, float)
        seglen = float(np.linalg.norm(seg))
        u = seg / seglen if seglen > 1e-9 else np.zeros(3)
        hold_acc = 0.0
        best = float("inf")
        while self.data.time < deadline_t:
            self.step(1)
            self.set_gripper_control(grip)
            ee, _ = self.get_ee_pose()
            d = ee - np.asarray(prev, float)
            s = float(np.dot(d, u))
            cross = float(np.linalg.norm(d - s * u)) if seglen > 1e-9 else \
                float(np.linalg.norm(ee - np.asarray(target, float)))
            if cross > cross_tol + 1e-6:
                raise CapabilityError(cap, "cross-track exceeded",
                                      {"cross": cross})
            err = float(np.linalg.norm(ee - np.asarray(target, float)))
            best = min(best, err)
            if final:
                if err <= 0.015:
                    hold_acc += dt
                    if hold_acc >= 0.5:
                        return err
                else:
                    hold_acc = 0.0
            else:
                if err <= 0.015:
                    return err
        raise CapabilityError(cap, "segment deadline exceeded",
                              {"best_error": best})

    def trace_cartesian_path(self, request):
        cap = "A2"
        if not isinstance(request, dict):
            raise CapabilityError(cap, "request must be a mapping")
        wps = request.get("waypoints_m")
        if not isinstance(wps, (list, tuple)) or not (2 <= len(wps) <= 8):
            raise CapabilityError(cap, "waypoints_m count invalid")
        pts = [self._validate_num_array(cap, w, 3, -1.0, 1.0) for w in wps]
        seg_dur = self._validate_duration(
            cap, request.get("max_duration_per_segment_s"), 0.25, 5.0)
        self._check_state_finite(cap)
        prev, _ = self.get_ee_pose()
        last_err = None
        for i, tgt in enumerate(pts):
            deadline = float(self.data.time) + seg_dur
            final = (i == len(pts) - 1)
            last_err = self._segment_servo(cap, tgt, deadline, 0.02, prev,
                                           final=final)
            prev = tgt
        self._hold_ctrl()
        return {"capability_id": cap, "status": "ok",
                "final_error_m": last_err}

    # ---- A5: move_cartesian_offset_and_return -----------------------------
    def move_cartesian_offset_and_return(self, request):
        cap = "A5"
        if not isinstance(request, dict):
            raise CapabilityError(cap, "request must be a mapping")
        off = self._validate_num_array(cap, request.get("offset_robot_base_m"),
                                       3, -0.06, 0.06)
        leg_dur = self._validate_duration(
            cap, request.get("max_duration_per_leg_s"), 0.25, 5.0)
        self._check_state_finite(cap)
        # capture robot-base frame at start. base body assumed world-aligned;
        # use base_link orientation to map the base-frame offset to world.
        bid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_BODY,
                                  "base_link")
        R = np.array(self.data.xmat[bid], float).reshape(3, 3) if bid >= 0 \
            else np.eye(3)
        start_ee, _ = self.get_ee_pose()
        world_off = R @ off
        outbound = start_ee + world_off
        # outbound leg
        t0 = float(self.data.time)
        self._servo_to(cap, outbound, t0 + leg_dur, tol=0.015, hold_s=0.25)
        ee_out, _ = self.get_ee_pose()
        disp = float(np.linalg.norm(ee_out - start_ee))
        req_disp = float(np.linalg.norm(world_off))
        if req_disp > 1e-9 and disp < 0.8 * req_disp:
            raise CapabilityError(cap, "insufficient outbound displacement",
                                  {"disp": disp, "req": req_disp})
        # return leg (only after outbound completed)
        t1 = float(self.data.time)
        res = self._servo_to(cap, start_ee, t1 + leg_dur, tol=0.015, hold_s=0.5)
        self._hold_ctrl()
        return {"capability_id": cap, "status": "ok",
                "outbound_disp_m": disp, "return_error_m": res["error"]}

    # ---- A4: approach_until_contact ---------------------------------------
    def _tool_geom_ids(self):
        """Collision geoms belonging to the gripper/tool (finger pads)."""
        ids = []
        for gi in range(self.model.ngeom):
            bid = self.model.geom_bodyid[gi]
            name = self.model.body(bid).name
            if self.model.geom_contype[gi] == 0 and self.model.geom_conaffinity[gi] == 0:
                continue
            if any(k in name for k in ("pad", "finger", "follower", "driver",
                                       "coupler", "spring", "gripper", "2f85")):
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
            g1, g2 = c.geom1, c.geom2
            if (g1 in tool and g2 in target) or (g2 in tool and g1 in target):
                found.append(c)
        return found

    def approach_until_contact(self, request):
        cap = "A4"
        if not isinstance(request, dict):
            raise CapabilityError(cap, "request must be a mapping")
        pre = self._validate_num_array(cap, request.get("precontact_position_m"),
                                       3, -1.0, 1.0)
        d = self._validate_num_array(cap, request.get("approach_direction_unit"),
                                     3, -1.0, 1.0)
        dn = float(np.linalg.norm(d))
        if dn < 1e-6:
            raise CapabilityError(cap, "approach direction is zero")
        d = d / dn
        max_travel = self._validate_duration(cap, request.get("max_travel_m"),
                                              1e-9, 0.08)
        max_speed = self._validate_duration(cap,
                                            request.get("max_approach_speed_m_s"),
                                            1e-9, 0.05)
        dur = self._validate_duration(cap, request.get("max_duration_s"), 0.25, 8.0)
        self._check_state_finite(cap)
        tool = self._tool_geom_ids()
        target = self._target_geom_ids()
        if not target:
            raise CapabilityError(cap, "contact target unavailable")
        dt = self._dt()
        t0 = float(self.data.time)
        deadline = t0 + dur
        # phase 1: reach precontact and dwell contact-free
        self._servo_to(cap, pre, deadline, tol=0.015, hold_s=0.0)
        dwell = 0.0
        while self.data.time < deadline:
            self.step(1)
            ee, _ = self.get_ee_pose()
            if np.linalg.norm(ee - pre) <= 0.015 and \
               not self._tool_target_contacts(tool, target):
                dwell += dt
                if dwell >= 0.1:
                    break
            else:
                dwell = 0.0
        else:
            raise CapabilityError(cap, "precontact dwell not achieved")
        self._approach_ray(cap, d, max_travel, max_speed, deadline, tool, target)
        return {"capability_id": cap, "status": "ok"}

    def _approach_ray(self, cap, d, max_travel, max_speed, deadline, tool, target):
        """Advance along +d from measured p_start until contact, at bounded
        speed, bounded lateral deviation, then hold contact 0.1 s."""
        dt = self._dt()
        p_start, _ = self.get_ee_pose()
        max_s = -1e9
        # incremental commanded advance keeps surface speed bounded
        step_ds = min(max_speed * dt, max_travel)
        commanded = 0.0
        contacted = False
        while self.data.time < deadline and not contacted:
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
            if commanded >= max_travel:
                raise CapabilityError(cap, "travel exhausted before contact")
            commanded = min(max_travel, commanded + step_ds)
            goal = p_start + d * commanded
            q = self._ik_target(cap, goal)
            self.set_arm_actuators(q)
            self.step(1)
        if not contacted:
            raise CapabilityError(cap, "no contact before deadline")
        # unrelated tool external contact must be zero
        self._check_unrelated(cap, tool, target)
        # contact hold: freeze command, verify continuous contact & slow tool
        self._hold_ctrl()
        hold = 0.0
        prev_ee, _ = self.get_ee_pose()
        while self.data.time < deadline:
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
                raise CapabilityError(cap, "penetration too deep", {"pen": pen})
            self._check_unrelated(cap, tool, target)
            hold += dt
            if hold >= 0.1:
                return
        raise CapabilityError(cap, "contact hold not sustained")

    def _check_unrelated(self, cap, tool, target):
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            g1, g2 = c.geom1, c.geom2
            involves_tool = g1 in tool or g2 in tool
            involves_target = g1 in target or g2 in target
            if involves_tool and not involves_target:
                other = g2 if g1 in tool else g1
                bid = self.model.geom_bodyid[other]
                bname = self.model.body(bid).name
                # ignore self-contacts within the gripper assembly
                if other in tool:
                    continue
                raise CapabilityError(cap, "unrelated tool contact",
                                      {"geom": bname})


def _make_spec():
    return ArmSpec(
        ee_site_name="pinch_site",
        ee_body_name="bracelet_link",
        arm_joint_names=list(_ARM_JOINTS),
        arm_actuator_names=list(_ARM_ACTS),
        joint_limits=dict(_LIMITS),
        home_qpos=[0.0] * 7,
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
