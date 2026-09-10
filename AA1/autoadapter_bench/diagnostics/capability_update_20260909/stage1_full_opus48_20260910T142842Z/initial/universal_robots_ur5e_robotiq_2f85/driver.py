# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for universal_robots_ur5e_robotiq_2f85.

UR5e 6-DoF serial arm + Robotiq 2F-85 tendon gripper. Subclasses
ArmSerialDLSSkeleton (DLS IK + actuator interp) and implements the public
capability contract A1..A5. All observation and actuation share one
MuJoCo model/data; kinematic-only calculations use scratch MjData.
"""
from __future__ import annotations

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton, IKUnreachableError
from auto_adapter.skeletons.base import ArmSpec

_ARM_JOINTS = [
    "shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint",
    "wrist_1_joint", "wrist_2_joint", "wrist_3_joint",
]
_ARM_ACTS = ["shoulder_pan", "shoulder_lift", "elbow", "wrist_1", "wrist_2", "wrist_3"]
_JLIM = {
    "shoulder_pan_joint": (-6.28319, 6.28319),
    "shoulder_lift_joint": (-6.28319, 6.28319),
    "elbow_joint": (-3.1415, 3.1415),
    "wrist_1_joint": (-6.28319, 6.28319),
    "wrist_2_joint": (-6.28319, 6.28319),
    "wrist_3_joint": (-6.28319, 6.28319),
}
# A comfortable elbow-up home so the tool reaches tabletop targets.
_HOME = [0.0, -1.5708, 1.5708, -1.5708, -1.5708, 0.0]


class CapabilityError(RuntimeError):
    """Bounded capability failure with a machine-readable code."""

    def __init__(self, code: str, message: str, **info):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.info = info


class Robot(ArmSerialDLSSkeleton):
    """UR5e + Robotiq 2F-85 capability robot."""

    # Gripper native ctrl: 0 -> open, 255 -> closed (probed).
    _GRIP_CTRL_OPEN = 0.0
    _GRIP_CTRL_CLOSED = 255.0
    # Driver-joint aperture calibration (probed): open ~0.0026, closed ~0.782.
    _GRIP_Q_OPEN = 0.0026
    _GRIP_Q_CLOSED = 0.7822

    def __init__(self, model, data, spec):
        super().__init__(model, data, spec)
        mj = self._mj
        m = self.model
        # Contact object (mocap) + its geom for A4.
        self._contact_body_id = mj.mj_name2id(m, mj.mjtObj.mjOBJ_BODY, "fixed_contact_target")
        self._contact_geom_id = mj.mj_name2id(m, mj.mjtObj.mjOBJ_GEOM, "fixed_contact_geom")
        # Tool geoms = gripper pads (allowed tool-target contact surfaces).
        self._tool_geom_ids = [
            mj.mj_name2id(m, mj.mjtObj.mjOBJ_GEOM, g)
            for g in ("right_pad1", "right_pad2", "left_pad1", "left_pad2")
        ]
        self._tool_geom_ids = [g for g in self._tool_geom_ids if g >= 0]
        # Base body for robot-base frame (A5): use shoulder_pan parent (world base).
        self._base_body_id = mj.mj_name2id(m, mj.mjtObj.mjOBJ_BODY, "base")
        self._grip_aid = self._gripper_actuator_ids[0]

    # ---- helpers ----
    def _dt(self):
        return float(self.model.opt.timestep)

    def _ee(self):
        self._mj.mj_forward(self.model, self.data)
        return self._ee_pos_now()

    # ─────────────────────────────────────────────────────────────────
    # Shared closed-loop position regulation to a world-frame target.
    # Advances real physics via actuator commands only. Returns dict with
    # final error and whether the terminal hold criterion was met.
    # ─────────────────────────────────────────────────────────────────
    def _regulate_to(self, target, deadline_s, tol, hold_s, hold_grip=None):
        mj = self._mj
        dt = self._dt()
        t0 = float(self.data.time)
        hold_acc = 0.0
        last = None
        # Re-solve IK periodically from current q for robustness.
        resolve_every = max(1, int(0.05 / dt))
        step_i = 0
        q_cmd = None
        while (float(self.data.time) - t0) <= deadline_s:
            if q_cmd is None or (step_i % resolve_every) == 0:
                try:
                    q_cmd = self.ik(np.asarray(target, float),
                                    q_init=self.get_joint_positions(),
                                    raise_on_unreachable=False)
                except IKUnreachableError as e:
                    q_cmd = e.q_final
            self.set_arm_actuators(q_cmd)
            if hold_grip is not None:
                self.set_gripper_control(hold_grip)
            self.step(1)
            step_i += 1
            err = float(np.linalg.norm(self._ee() - np.asarray(target, float)))
            last = err
            if err <= tol:
                hold_acc += dt
                if hold_acc >= hold_s:
                    return {"ok": True, "error": err}
            else:
                hold_acc = 0.0
        return {"ok": False, "error": last if last is not None else float("inf")}

    def _check_finite_state(self):
        q = self.get_joint_positions()
        if not np.all(np.isfinite(q)):
            raise CapabilityError("state_unavailable", "joint state not finite")

    @staticmethod
    def _req_arr(request, key, n):
        v = np.asarray(request[key], dtype=float).reshape(-1)
        if v.shape[0] != n or not np.all(np.isfinite(v)):
            raise CapabilityError("bad_request", f"{key} must have {n} finite items")
        return v

    # ─────────────────────────────── A1 ───────────────────────────────
    def move_end_effector_to_position(self, request):
        self._check_finite_state()
        target = self._req_arr(request, "target_position_m", 3)
        if np.any(np.abs(target) > 1.0):
            raise CapabilityError("bad_request", "target_position_m out of [-1,1]")
        dur = float(request["max_duration_s"])
        if not (0.25 <= dur <= 8.0):
            raise CapabilityError("bad_request", "max_duration_s out of bounds")
        grip_hold = float(self.data.ctrl[self._grip_aid])
        res = self._regulate_to(target, dur, tol=0.015, hold_s=0.5, hold_grip=grip_hold)
        if not res["ok"]:
            raise CapabilityError("not_reached",
                                  f"position error {res['error']:.4f} m within {dur}s",
                                  error_m=res["error"])
        return {"ok": True, "capability_id": "A1", "final_error_m": res["error"]}

    # ─────────────────────────────── A2 ───────────────────────────────
    def trace_cartesian_path(self, request):
        self._check_finite_state()
        wps = request["waypoints_m"]
        if not isinstance(wps, (list, tuple)) or not (2 <= len(wps) <= 8):
            raise CapabilityError("bad_request", "waypoints_m must have 2..8 items")
        pts = [self._req_arr({"w": w}, "w", 3) for w in wps]
        for p in pts:
            if np.any(np.abs(p) > 1.0):
                raise CapabilityError("bad_request", "waypoint out of [-1,1]")
        seg_dur = float(request["max_duration_per_segment_s"])
        if not (0.25 <= seg_dur <= 5.0):
            raise CapabilityError("bad_request", "segment duration out of bounds")
        grip_hold = float(self.data.ctrl[self._grip_aid])
        # Visit each waypoint in order; intermediate waypoints get a short hold,
        # terminal gets 0.5 s. Cross-track stays bounded by tracking segments.
        for i, p in enumerate(pts):
            terminal = (i == len(pts) - 1)
            hold = 0.5 if terminal else 0.05
            tol = 0.015 if terminal else 0.02
            res = self._regulate_to(p, seg_dur, tol=tol, hold_s=hold, hold_grip=grip_hold)
            if not res["ok"]:
                raise CapabilityError("waypoint_unreached",
                                      f"waypoint {i} error {res['error']:.4f} m",
                                      waypoint_index=i, error_m=res["error"])
        return {"ok": True, "capability_id": "A2", "waypoints": len(pts)}

    # ─────────────────────────────── A3 ───────────────────────────────
    def _aperture_fraction(self):
        # fraction: 1.0 = fully open, 0.0 = fully closed. Use driver joints.
        q = self.get_gripper_joint_positions()
        vals = list(q.values())
        avg = float(np.mean(vals)) if vals else self._GRIP_Q_OPEN
        span = self._GRIP_Q_CLOSED - self._GRIP_Q_OPEN
        closed_frac = (avg - self._GRIP_Q_OPEN) / span if span else 0.0
        return float(np.clip(1.0 - closed_frac, 0.0, 1.0))

    def set_gripper_opening(self, request):
        q = self.get_gripper_joint_positions()
        if not all(np.isfinite(v) for v in q.values()):
            raise CapabilityError("state_unavailable", "gripper state not finite")
        frac = float(request["opening_fraction"])
        if not (0.0 <= frac <= 1.0) or not np.isfinite(frac):
            raise CapabilityError("bad_request", "opening_fraction out of [0,1]")
        dur = float(request["max_duration_s"])
        if not (0.25 <= dur <= 8.0):
            raise CapabilityError("bad_request", "max_duration_s out of bounds")
        # fraction 1 -> ctrl OPEN(0), fraction 0 -> ctrl CLOSED(255)
        ctrl = self._GRIP_CTRL_OPEN + (1.0 - frac) * (self._GRIP_CTRL_CLOSED - self._GRIP_CTRL_OPEN)
        # Hold current arm actuator targets so the arm pose is not changed.
        arm_hold = np.array([float(self.data.ctrl[a]) for a in self._arm_actuator_ids])
        dt = self._dt()
        t0 = float(self.data.time)
        hold_acc = 0.0
        last = None
        while (float(self.data.time) - t0) <= dur:
            self.set_arm_actuators(arm_hold)
            self.set_gripper_control(ctrl)
            self.step(1)
            err = abs(self._aperture_fraction() - frac)
            last = err
            if err <= 0.1:
                hold_acc += dt
                if hold_acc >= 0.25:
                    return {"ok": True, "capability_id": "A3", "aperture_error": err}
            else:
                hold_acc = 0.0
        raise CapabilityError("aperture_unreached",
                              f"aperture error {last:.3f} within {dur}s",
                              error=last)

    # ─────────────────────────────── A4 ───────────────────────────────
    def _tool_target_contact(self):
        """Return (in_contact, min_dist, max_pen) for tool-pad vs target geom."""
        mj = self._mj
        d = self.data
        in_c = False
        max_pen = 0.0
        for i in range(d.ncon):
            c = d.contact[i]
            g1, g2 = int(c.geom1), int(c.geom2)
            pair = {g1, g2}
            if self._contact_geom_id in pair and (pair & set(self._tool_geom_ids)):
                in_c = True
                if c.dist < 0:
                    max_pen = max(max_pen, -float(c.dist))
        return in_c, max_pen

    def _unrelated_tool_contact(self):
        """Count tool-pad external contacts NOT with the target geom."""
        mj = self._mj
        d = self.data
        cnt = 0
        tool = set(self._tool_geom_ids)
        for i in range(d.ncon):
            c = d.contact[i]
            g1, g2 = int(c.geom1), int(c.geom2)
            pair = {g1, g2}
            if (pair & tool) and self._contact_geom_id not in pair:
                # ignore self-contacts within gripper pads
                if not (g1 in tool and g2 in tool):
                    cnt += 1
        return cnt

    def approach_until_contact(self, request):
        self._check_finite_state()
        p_pre = self._req_arr(request, "precontact_position_m", 3)
        d_dir = self._req_arr(request, "approach_direction_unit", 3)
        nrm = float(np.linalg.norm(d_dir))
        if nrm < 1e-9:
            raise CapabilityError("bad_request", "approach_direction_unit is zero")
        d_dir = d_dir / nrm
        max_travel = float(request["max_travel_m"])
        max_speed = float(request["max_approach_speed_m_s"])
        dur = float(request["max_duration_s"])
        if not (0.0 < max_travel <= 0.08):
            raise CapabilityError("bad_request", "max_travel_m out of bounds")
        if not (0.0 < max_speed <= 0.05):
            raise CapabilityError("bad_request", "max_approach_speed out of bounds")
        if not (0.25 <= dur <= 8.0):
            raise CapabilityError("bad_request", "max_duration_s out of bounds")
        if np.any(np.abs(p_pre) > 1.0):
            raise CapabilityError("bad_request", "precontact out of [-1,1]")

        dt = self._dt()
        t0 = float(self.data.time)
        grip_hold = float(self.data.ctrl[self._grip_aid])
        # Phase 1: reach precontact, contact-free dwell >= 0.1 s within 0.015 m.
        dwell_budget = min(dur * 0.6, dur - 0.15)
        res = self._regulate_to(p_pre, dwell_budget, tol=0.015, hold_s=0.1,
                                hold_grip=grip_hold)
        if not res["ok"]:
            raise CapabilityError("precontact_unreached",
                                  f"precontact error {res['error']:.4f} m")
        if self._tool_target_contact()[0]:
            raise CapabilityError("premature_contact", "contact before ray approach")
        if self._unrelated_tool_contact() > 0:
            raise CapabilityError("unrelated_contact", "unrelated contact at precontact")
        p_start = self._ee().copy()
        return self._approach_ray(p_start, d_dir, max_travel, max_speed, dur, t0, grip_hold)

    def _approach_ray(self, p_start, d_dir, max_travel, max_speed, dur, t0, grip_hold):
        """Advance the tool along d_dir at bounded speed until contact, then hold."""
        dt = self._dt()
        # Command a slowly-advancing target along the ray so the closed-loop
        # tool speed stays under max_speed. Use a conservative commanded speed.
        cmd_speed = 0.6 * max_speed
        s_cmd = 0.0
        prev_p = self._ee().copy()
        contact_time = None
        while (float(self.data.time) - t0) <= dur:
            if contact_time is None:
                s_cmd = min(s_cmd + cmd_speed * dt, max_travel)
            tgt = p_start + s_cmd * d_dir
            try:
                q_cmd = self.ik(tgt, q_init=self.get_joint_positions(),
                                raise_on_unreachable=False)
            except IKUnreachableError as e:
                q_cmd = e.q_final
            self.set_arm_actuators(q_cmd)
            self.set_gripper_control(grip_hold)
            self.step(1)
            p = self._ee()
            in_c, pen = self._tool_target_contact()
            if self._unrelated_tool_contact() > 0:
                raise CapabilityError("unrelated_contact",
                                      "unrelated tool contact during approach")
            if in_c and contact_time is None:
                contact_time = float(self.data.time)
                # Freeze commanded target at current pose to establish hold.
                q_hold = self.get_joint_positions().copy()
            if contact_time is not None:
                # Hold: keep current joints, keep contact, monitor speed/pen.
                self.set_arm_actuators(q_hold)
                spd = float(np.linalg.norm(p - prev_p)) / dt
                if pen > 0.005:
                    raise CapabilityError("overpenetration",
                                          f"penetration {pen:.4f} m")
                if (float(self.data.time) - contact_time) >= 0.1:
                    return {"ok": True, "capability_id": "A4",
                            "contact_time_s": contact_time, "penetration_m": pen}
            prev_p = p.copy()
            # Exhausted travel without contact -> bounded failure.
            if contact_time is None and s_cmd >= max_travel:
                # allow a little settle for contact to register
                s_over = float(np.dot(p - p_start, d_dir))
                if s_over >= max_travel + 0.002:
                    raise CapabilityError("no_contact",
                                          "travel exhausted before contact")
        raise CapabilityError("timeout", "approach did not complete in duration")

    # ─────────────────────────────── A5 ───────────────────────────────
    def _base_frame(self):
        mj = self._mj
        self._mj.mj_forward(self.model, self.data)
        R = np.array(self.data.xmat[self._base_body_id], float).reshape(3, 3)
        return R

    def move_cartesian_offset_and_return(self, request):
        self._check_finite_state()
        off = self._req_arr(request, "offset_robot_base_m", 3)
        if np.any(np.abs(off) > 0.06):
            raise CapabilityError("bad_request", "offset out of [-0.06,0.06]")
        leg_dur = float(request["max_duration_per_leg_s"])
        if not (0.25 <= leg_dur <= 5.0):
            raise CapabilityError("bad_request", "leg duration out of bounds")
        grip_hold = float(self.data.ctrl[self._grip_aid])
        R = self._base_frame()
        world_off = R @ off  # base-frame offset -> world displacement
        p_start = self._ee().copy()
        p_out = p_start + world_off
        # Outbound leg (hold 0.25 s), then return (hold 0.5 s). Ordered.
        r1 = self._regulate_to(p_out, leg_dur, tol=0.015, hold_s=0.25, hold_grip=grip_hold)
        if not r1["ok"]:
            raise CapabilityError("outbound_unreached",
                                  f"outbound error {r1['error']:.4f} m")
        r2 = self._regulate_to(p_start, leg_dur, tol=0.015, hold_s=0.5, hold_grip=grip_hold)
        if not r2["ok"]:
            raise CapabilityError("return_unreached",
                                  f"return error {r2['error']:.4f} m")
        return {"ok": True, "capability_id": "A5",
                "outbound_error_m": r1["error"], "return_error_m": r2["error"]}


def build():
    spec = ArmSpec(
        ee_site_name="pinch",
        arm_joint_names=list(_ARM_JOINTS),
        arm_actuator_names=list(_ARM_ACTS),
        joint_limits=dict(_JLIM),
        home_qpos=list(_HOME),
        ik_damping=1e-3,
        ik_max_iter=100,
        ik_tolerance=1e-3,
        ik_step_clamp=0.2,
        ik_raise_on_unreachable=True,
        gripper_actuator_names=["fingers_actuator"],
        gripper_joint_names=["right_driver_joint", "left_driver_joint"],
        grasp_backend="noop",
        sim_dt=0.002,
    )
    return Robot.from_mjcf("mjcf.xml", spec=spec)
