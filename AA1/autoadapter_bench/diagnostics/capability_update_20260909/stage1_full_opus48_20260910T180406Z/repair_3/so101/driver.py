# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for SO-101 (ArmSerialDLSSkeleton).

Implements the public capability contract (A1..A5) on top of the skeleton's
low-level DLS IK / actuator primitives. All timing is measured with
``data.time`` (simulation seconds). Physics is advanced only through actuator
commands on the canonical model/data; live qpos/qvel are never set to achieve
an action, and no non-canonical world is ever stepped. Kinematic IK runs on
the skeleton's internal scratch state without stepping physics.
"""
from __future__ import annotations

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton, IKUnreachableError
from auto_adapter.skeletons.base import ArmSpec


class CapabilityError(RuntimeError):
    """Bounded capability failure with a structured payload."""

    def __init__(self, capability: str, reason: str, **info) -> None:
        super().__init__(f"[{capability}] {reason}")
        self.capability = capability
        self.reason = reason
        self.info = info


_ARM_JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
_ARM_ACTS = ["act_shoulder_pan", "act_shoulder_lift", "act_elbow_flex",
             "act_wrist_flex", "act_wrist_roll"]
_JOINT_LIMITS = {
    "shoulder_pan": (-1.92, 1.92),
    "shoulder_lift": (-1.75, 1.75),
    "elbow_flex": (-1.69, 1.69),
    "wrist_flex": (-1.66, 1.66),
    "wrist_roll": (-2.74, 2.84),
}


class Robot(ArmSerialDLSSkeleton):
    """SO-101 capability robot: 5-DOF arm + 1-DOF jaw gripper."""

    def __init__(self, model, data, spec: ArmSpec) -> None:
        super().__init__(model, data, spec)
        aid = self._gripper_actuator_ids[0]
        lo, hi = self.model.actuator_ctrlrange[aid]
        self._jaw_lo = float(lo)
        self._jaw_hi = float(hi)
        jid = self._gripper_joint_ids[0]
        jlo, jhi = self.model.jnt_range[jid]
        self._jaw_jlo = float(jlo)
        self._jaw_jhi = float(jhi)
        self._open_ctrl = self._jaw_hi
        self._close_ctrl = self._jaw_lo

    # ------------------------------------------------------------------ utils
    @property
    def _dt(self) -> float:
        return float(self.model.opt.timestep)

    def _check_finite_state(self, cap: str) -> None:
        q = self.get_joint_positions()
        v = self.get_joint_velocities()
        if not (np.all(np.isfinite(q)) and np.all(np.isfinite(v))):
            raise CapabilityError(cap, "non-finite robot state before motion")

    def _hold_arm(self) -> None:
        cur = np.array([float(self.data.ctrl[a]) for a in self._arm_actuator_ids])
        self.set_arm_actuators(cur)

    def _hold_gripper(self) -> None:
        aid = self._gripper_actuator_ids[0]
        self.data.ctrl[aid] = float(self.data.ctrl[aid])

    def _ee(self) -> np.ndarray:
        self._mj.mj_forward(self.model, self.data)
        return self._ee_pos_now()

    def _ee_speed(self) -> float:
        """World EE linear speed via positional Jacobian and joint velocity."""
        self._mj.mj_forward(self.model, self.data)
        jacp = np.zeros((3, self.model.nv))
        self._ee_jac_pos(jacp, self.data)
        v = jacp @ self.data.qvel
        return float(np.linalg.norm(v))

    def _solve_ik(self, cap: str, target: np.ndarray) -> np.ndarray:
        try:
            return self.ik(np.asarray(target, float),
                           q_init=self.get_joint_positions(),
                           raise_on_unreachable=False)
        except IKUnreachableError as e:  # pragma: no cover - defensive
            return e.q_final

    def _regulate_to_target(self, cap, target, deadline_t, tol,
                            hold_s, keep_gripper=True):
        """Closed-loop world-position regulation using only ctrl + physics."""
        target = np.asarray(target, float)
        q_goal = self._solve_ik(cap, target)
        self.set_arm_actuators(q_goal)
        hold_start = None
        last_err = float("inf")
        resolve_every = max(1, int(0.05 / self._dt))
        i = 0
        while self.data.time < deadline_t:
            if i % resolve_every == 0:
                q_goal = self._solve_ik(cap, target)
            self.set_arm_actuators(q_goal)
            if keep_gripper:
                self._hold_gripper()
            self.step(1)
            i += 1
            err = float(np.linalg.norm(self._ee() - target))
            last_err = err
            if err <= tol:
                if hold_start is None:
                    hold_start = self.data.time
                elif self.data.time - hold_start >= hold_s:
                    return True, err
            else:
                hold_start = None
        return False, last_err

    # ------------------------------------------------------- request parsing
    def _req(self, cap, request, key):
        if not isinstance(request, dict) or key not in request:
            raise CapabilityError(cap, f"missing required field {key!r}")
        return request[key]

    def _req_num(self, cap, request, key, lo, hi):
        v = self._req(cap, request, key)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not np.isfinite(v):
            raise CapabilityError(cap, f"{key} must be a finite number")
        v = float(v)
        if v < lo or v > hi:
            raise CapabilityError(cap, f"{key}={v} out of [{lo},{hi}]")
        return v

    def _req_vec3(self, cap, request, key, lo, hi):
        v = self._req(cap, request, key)
        if not isinstance(v, (list, tuple)) or len(v) != 3:
            raise CapabilityError(cap, f"{key} must have 3 elements")
        out = []
        for x in v:
            if not isinstance(x, (int, float)) or isinstance(x, bool) or not np.isfinite(x):
                raise CapabilityError(cap, f"{key} elements must be finite")
            if x < lo or x > hi:
                raise CapabilityError(cap, f"{key} element {x} out of [{lo},{hi}]")
            out.append(float(x))
        return np.array(out, float)

    def _extra_keys(self, cap, request, allowed):
        if not isinstance(request, dict):
            raise CapabilityError(cap, "request must be an object")
        extra = set(request) - allowed
        if extra:
            raise CapabilityError(cap, f"unexpected fields {sorted(extra)}")

    # =============================================================== A1
    def move_end_effector_to_position(self, request):
        cap = "A1"
        target = self._req_vec3(cap, request, "target_position_m", -1.0, 1.0)
        dur = self._req_num(cap, request, "max_duration_s", 0.25, 8.0)
        self._extra_keys(cap, request, {"target_position_m", "max_duration_s"})
        self._check_finite_state(cap)
        deadline = self.data.time + dur
        reached, err = self._regulate_to_target(cap, target, deadline,
                                                 tol=0.015, hold_s=0.5)
        if not reached:
            self._hold_arm()
            raise CapabilityError(cap, "target not held within duration",
                                  final_error=err)
        return {"capability": cap, "status": "ok", "final_error_m": err,
                "ee_position_m": self._ee().tolist()}

    # =============================================================== A2
    def trace_cartesian_path(self, request):
        cap = "A2"
        wps = self._req(cap, request, "waypoints_m")
        if not isinstance(wps, (list, tuple)) or not (2 <= len(wps) <= 8):
            raise CapabilityError(cap, "waypoints_m must have 2..8 entries")
        seg_dur = self._req_num(cap, request, "max_duration_per_segment_s", 0.25, 5.0)
        self._extra_keys(cap, request,
                         {"waypoints_m", "max_duration_per_segment_s"})
        self._check_finite_state(cap)
        pts = [self._req_vec3(cap, {"w": w}, "w", -1.0, 1.0) for w in wps]
        for idx, p in enumerate(pts):
            terminal = (idx == len(pts) - 1)
            hold = 0.5 if terminal else 0.05
            tol = 0.015 if terminal else 0.02
            deadline = self.data.time + seg_dur
            reached, err = self._regulate_to_target(cap, p, deadline,
                                                     tol=tol, hold_s=hold)
            if not reached:
                self._hold_arm()
                raise CapabilityError(cap, f"waypoint {idx} not reached in order",
                                      waypoint_index=idx, final_error=err)
        return {"capability": cap, "status": "ok",
                "waypoints_visited": len(pts),
                "ee_position_m": self._ee().tolist()}

    # ------------------------------------------------------- gripper mapping
    def _jaw_pos(self) -> float:
        return float(next(iter(self.get_gripper_joint_positions().values())))

    def _ctrl_for_fraction(self, f: float) -> float:
        return self._close_ctrl + float(f) * (self._open_ctrl - self._close_ctrl)

    def _aperture_fraction(self) -> float:
        span = self._jaw_jhi - self._jaw_jlo
        if abs(span) < 1e-9:
            return 0.0
        return float(np.clip((self._jaw_pos() - self._jaw_jlo) / span, 0.0, 1.0))

    # =============================================================== A3
    def set_gripper_opening(self, request):
        cap = "A3"
        frac = self._req_num(cap, request, "opening_fraction", 0.0, 1.0)
        dur = self._req_num(cap, request, "max_duration_s", 0.25, 8.0)
        self._extra_keys(cap, request, {"opening_fraction", "max_duration_s"})
        if not np.all(np.isfinite(list(self.get_gripper_joint_positions().values()))):
            raise CapabilityError(cap, "non-finite gripper state")
        arm_hold = np.array([float(self.data.ctrl[a]) for a in self._arm_actuator_ids])
        aid = self._gripper_actuator_ids[0]
        cmd = float(np.clip(self._ctrl_for_fraction(frac),
                            *self.model.actuator_ctrlrange[aid]))
        deadline = self.data.time + dur
        hold_start = None
        last_err = 1.0
        while self.data.time < deadline:
            self.set_arm_actuators(arm_hold)
            self.set_gripper_control(cmd)
            self.step(1)
            err = abs(self._aperture_fraction() - frac)
            last_err = err
            if err <= 0.1:
                if hold_start is None:
                    hold_start = self.data.time
                elif self.data.time - hold_start >= 0.25:
                    return {"capability": cap, "status": "ok",
                            "aperture_fraction": self._aperture_fraction(),
                            "error": err}
            else:
                hold_start = None
        self.set_arm_actuators(arm_hold)
        self.set_gripper_control(cmd)
        raise CapabilityError(cap, "aperture not established within duration",
                              final_error=last_err)

    # ------------------------------------------------------- contact helpers
    def _tcp_geom_id(self) -> int:
        return int(self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_GEOM,
                                       "so101_tcp_contact"))

    def _target_geom_id(self) -> int:
        return int(self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_GEOM,
                                       "fixed_contact_geom"))

    def _contact_report(self):
        """Return (n_target, n_unrelated, max_penetration) for TCP contacts."""
        self._mj.mj_forward(self.model, self.data)
        tcp = self._tcp_geom_id()
        tgt = self._target_geom_id()
        n_target = n_unrel = 0
        pen = 0.0
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            g1, g2 = int(c.geom1), int(c.geom2)
            if tcp not in (g1, g2):
                continue
            other = g2 if g1 == tcp else g1
            depth = -float(c.dist) if c.dist < 0 else 0.0
            if other == tgt:
                n_target += 1
                pen = max(pen, depth)
            else:
                n_unrel += 1
        return n_target, n_unrel, pen

    def _contact_available(self, cap) -> None:
        try:
            _ = self.data.ncon
            if self._tcp_geom_id() < 0 or self._target_geom_id() < 0:
                raise CapabilityError(cap, "contact geoms unavailable")
        except CapabilityError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            raise CapabilityError(cap, "contact observations unavailable") from e

    # =============================================================== A4
    def approach_until_contact(self, request):
        cap = "A4"
        pre = self._req_vec3(cap, request, "precontact_position_m", -1.0, 1.0)
        d = self._req_vec3(cap, request, "approach_direction_unit", -1.0, 1.0)
        max_travel = self._req_num(cap, request, "max_travel_m", 1e-9, 0.08)
        max_speed = self._req_num(cap, request, "max_approach_speed_m_s", 1e-9, 0.05)
        dur = self._req_num(cap, request, "max_duration_s", 0.25, 8.0)
        self._extra_keys(cap, request, {
            "precontact_position_m", "approach_direction_unit",
            "max_travel_m", "max_approach_speed_m_s", "max_duration_s"})
        self._check_finite_state(cap)
        self._contact_available(cap)
        nrm = float(np.linalg.norm(d))
        if nrm < 1e-9:
            raise CapabilityError(cap, "approach_direction_unit is zero")
        d = d / nrm
        deadline = self.data.time + dur

        # Phase 1: regulate to the precontact pose (no intended contact yet).
        _, n_u0, _ = self._contact_report()
        reached, err = self._regulate_to_target(cap, pre, deadline,
                                                 tol=0.012, hold_s=0.0)
        if not reached:
            self._hold_arm()
            raise CapabilityError(cap, "precontact pose not reached",
                                  final_error=err)

        # Phase 1b: hold the precontact command steady and dwell CONTACT-FREE
        # for a margin above the required 0.1 s.  The dwell must remain both
        # within the 0.015 m tolerance of the requested precontact position AND
        # free of any target contact.  We keep a fixed IK command (no re-solve,
        # no advance) so the tool stays put.  p_start is the measured tool
        # position at the endpoint of this completed dwell (contract ray origin).
        q_pre = self._solve_ik(cap, pre)
        required_dwell = 0.1
        dwell_margin = 0.06  # aim for ~0.16 s to clear the 0.1 s gate cleanly
        dwell_start = None
        while self.data.time < deadline:
            self.set_arm_actuators(q_pre)
            self._hold_gripper()
            self.step(1)
            n_t, n_u, pen = self._contact_report()
            perr = float(np.linalg.norm(self._ee() - pre))
            contact_free = (n_t == 0 and pen <= 0.0 and n_u <= n_u0)
            in_tol = perr <= 0.015
            if contact_free and in_tol:
                if dwell_start is None:
                    dwell_start = self.data.time
                elif self.data.time - dwell_start >= required_dwell + dwell_margin:
                    break
            else:
                dwell_start = None
        if dwell_start is None or (self.data.time - dwell_start) < required_dwell:
            self._hold_arm()
            raise CapabilityError(cap, "no contact-free precontact dwell",
                                  precontact_error=perr)

        # Ray origin: measured tool position at the end of the completed dwell.
        start = self._ee().copy()

        # Phase 2: advance the commanded target steadily along the declared ray.
        # Speed bounded by ramping commanded distance at <= max_approach_speed.
        cmd_dist = 0.0
        step_len = max_speed * self._dt
        hold_start = None
        while self.data.time < deadline:
            n_t, n_u, pen = self._contact_report()
            spd = self._ee_speed()
            if n_u > n_u0:
                self._hold_arm()
                raise CapabilityError(cap, "unrelated contact during approach",
                                      unrelated=n_u)
            contact = (n_t >= 1) or (pen > 0.0)
            if contact and spd <= 0.02 and pen <= 0.005:
                if hold_start is None:
                    hold_start = self.data.time
                elif self.data.time - hold_start >= 0.1:
                    return {"capability": cap, "status": "ok",
                            "penetration_m": pen, "post_contact_speed": spd,
                            "unrelated_contacts": n_u - n_u0,
                            "target_contacts": n_t}
                self._hold_arm()
                self._hold_gripper()
                self.step(1)
                continue
            hold_start = None
            if contact and (spd > 0.02 or pen > 0.005):
                self._hold_arm()
                self._hold_gripper()
                self.step(1)
                continue
            progress = float(np.dot(self._ee() - start, d))
            if progress >= max_travel and cmd_dist >= max_travel:
                self._hold_arm()
                raise CapabilityError(cap, "travel exhausted without contact",
                                      travelled=progress)
            cmd_dist = min(max_travel, cmd_dist + step_len)
            goal = start + d * cmd_dist
            q_goal = self._solve_ik(cap, goal)
            self.set_arm_actuators(q_goal)
            self._hold_gripper()
            self.step(1)
        self._hold_arm()
        raise CapabilityError(cap, "contact not established within duration")

    # =============================================================== A5
    def move_cartesian_offset_and_return(self, request):
        cap = "A5"
        off = self._req_vec3(cap, request, "offset_robot_base_m", -0.06, 0.06)
        leg_dur = self._req_num(cap, request, "max_duration_per_leg_s", 0.25, 5.0)
        self._extra_keys(cap, request,
                         {"offset_robot_base_m", "max_duration_per_leg_s"})
        self._check_finite_state(cap)

        self._mj.mj_forward(self.model, self.data)
        bid = int(self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_BODY,
                                      "base"))
        if bid < 0:
            R = np.eye(3)
        else:
            R = np.array(self.data.xmat[bid]).reshape(3, 3)
        start_ee = self._ee().copy()
        outbound_target = start_ee + R @ off

        deadline = self.data.time + leg_dur
        reached, err = self._regulate_to_target(cap, outbound_target, deadline,
                                                 tol=0.015, hold_s=0.25)
        if not reached:
            self._hold_arm()
            raise CapabilityError(cap, "outbound leg not completed",
                                  final_error=err)
        disp = float(np.linalg.norm(self._ee() - start_ee))
        req_disp = float(np.linalg.norm(R @ off))

        deadline = self.data.time + leg_dur
        reached, err = self._regulate_to_target(cap, start_ee, deadline,
                                                 tol=0.015, hold_s=0.5)
        if not reached:
            self._hold_arm()
            raise CapabilityError(cap, "return leg not completed",
                                  final_error=err)
        return {"capability": cap, "status": "ok",
                "outbound_displacement_m": disp,
                "requested_displacement_m": req_disp,
                "return_error_m": err}


def build():
    """Construct the SO-101 capability Robot bound to the fixed scene MJCF."""
    spec = ArmSpec(
        ee_site_name="ee_site",
        ee_body_name="gripper_link",
        arm_joint_names=list(_ARM_JOINTS),
        arm_actuator_names=list(_ARM_ACTS),
        joint_limits=dict(_JOINT_LIMITS),
        gripper_actuator_names=["act_jaw_visual"],
        gripper_joint_names=["jaw_visual_joint"],
        gripper_close_ctrl=-0.175,
        gripper_open_ctrl=1.75,
        gripper_settle_steps=30,
        ik_damping=0.005,
        ik_max_iter=60,
        ik_tolerance=1e-4,
        ik_step_clamp=0.2,
        ik_raise_on_unreachable=False,
        grasp_backend=None,
        weld_graspable_bodies=None,
    )
    return Robot.from_mjcf("mjcf.xml", spec=spec)
