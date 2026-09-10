# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for the `piper` 6-DOF serial arm + gripper.

Subclasses ArmSerialDLSSkeleton (DLS IK + actuator-space interp) and
implements the public capability contract (A1..A5). All motion is driven
through native position actuators; deadlines/holds are measured with
data.time. Kinematic (IK/FK) calculations use the skeleton's scratch data.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton, IKUnreachableError
from auto_adapter.skeletons.base import ArmSpec


class CapabilityError(RuntimeError):
    """Bounded capability failure raised when a request cannot complete."""

    def __init__(self, capability_id: str, reason: str, detail: dict | None = None):
        super().__init__(f"[{capability_id}] {reason}")
        self.capability_id = capability_id
        self.reason = reason
        self.detail = detail or {}


# Gripper native control: ctrl in [0, 0.035]; 0.035 -> fully open (aperture
# ~0.07 m), 0.0 -> closed. Probed on the real model.
_GRIP_CLOSED_CTRL = 0.0
_GRIP_OPEN_CTRL = 0.035


class Robot(ArmSerialDLSSkeleton):
    """PiPER capability robot."""

    CAP_A1 = "A1"
    CAP_A2 = "A2"
    CAP_A3 = "A3"
    CAP_A4 = "A4"
    CAP_A5 = "A5"

    # ---- helpers -------------------------------------------------------
    def _now(self) -> float:
        return float(self.data.time)

    def _finite(self, *arrs) -> bool:
        for a in arrs:
            if not np.all(np.isfinite(np.asarray(a, dtype=np.float64))):
                return False
        return True

    def _ee_pos(self) -> np.ndarray:
        self._mj.mj_forward(self.model, self.data)
        return self._ee_pos_now()

    def _ee_speed(self) -> float:
        """World-frame EE linear speed via positional Jacobian * qvel."""
        jacp = np.zeros((3, self.model.nv), dtype=np.float64)
        self._ee_jac_pos(jacp)
        v = jacp @ self.data.qvel
        return float(np.linalg.norm(v))

    def _grip_ctrl_for_fraction(self, frac: float) -> float:
        frac = float(np.clip(frac, 0.0, 1.0))
        return _GRIP_CLOSED_CTRL + frac * (_GRIP_OPEN_CTRL - _GRIP_CLOSED_CTRL)

    def _grip_aperture_fraction(self) -> float:
        """Normalised aperture in [0,1] from physical joint positions."""
        pos = self.get_gripper_joint_positions()
        # joint7 in [0, 0.035]; open at 0.035.
        j7 = pos.get("joint7", 0.0)
        span = _GRIP_OPEN_CTRL - _GRIP_CLOSED_CTRL
        return float(np.clip((j7 - _GRIP_CLOSED_CTRL) / span, 0.0, 1.0))

    def _hold_gripper(self) -> None:
        """Re-issue the current gripper command each step (freeze aperture)."""
        aid = self._gripper_actuator_ids[0]
        self.set_gripper_control(float(self.data.ctrl[aid]))

    # ---- shared closed-loop position regulation ------------------------
    def _regulate_to_xyz(self, cap: str, target: np.ndarray, deadline_s: float,
                         tol: float, hold_s: float, freeze_gripper: bool = True):
        """Drive EE to `target`, holding within `tol` for `hold_s` seconds.

        Uses IK for the joint target then re-plans incrementally each control
        tick from the current pose so residual error is closed. Returns the
        final position error. Raises CapabilityError on deadline expiry.
        """
        t0 = self._now()
        hold_start = None
        best_err = float("inf")
        while True:
            now = self._now()
            if now - t0 > deadline_s:
                self._stop_motion(freeze_gripper)
                raise CapabilityError(cap, "deadline_expired",
                                      {"error_m": best_err, "elapsed_s": now - t0})
            ee = self._ee_pos()
            err = float(np.linalg.norm(target - ee))
            best_err = min(best_err, err)
            if err <= tol:
                if hold_start is None:
                    hold_start = now
                elif now - hold_start >= hold_s:
                    return err
            else:
                hold_start = None
            try:
                q_goal = self.ik(target, q_init=self.get_joint_positions(),
                                 raise_on_unreachable=False)
            except IKUnreachableError as e:
                q_goal = e.q_final
            q_cur = np.array([self.data.ctrl[a] for a in self._arm_actuator_ids],
                             dtype=np.float64)
            step = q_goal - q_cur
            max_step = 0.02  # rad per tick
            n = float(np.max(np.abs(step))) / max_step if np.any(step) else 0.0
            if n > 1.0:
                step = step / n
            cmd = np.clip(q_cur + step, self._q_lo, self._q_hi)
            self.set_arm_actuators(cmd)
            if freeze_gripper:
                self._hold_gripper()
            self.step(1)

    def _stop_motion(self, freeze_gripper: bool = True) -> None:
        """Command the arm to hold its current pose (zero further motion)."""
        q_cur = self.get_joint_positions()
        self.set_arm_actuators(np.clip(q_cur, self._q_lo, self._q_hi))
        if freeze_gripper:
            self._hold_gripper()

    def _check_reachable(self, cap: str, target: np.ndarray) -> None:
        """Fail early if IK cannot reach the target within tolerance."""
        try:
            self.ik(target, q_init=self.get_joint_positions(),
                    raise_on_unreachable=True)
        except IKUnreachableError as e:
            raise CapabilityError(cap, "target_unreachable",
                                  {"residual_m": e.residual})

    # ---- A1: move_end_effector_to_position -----------------------------
    def move_end_effector_to_position(self, request: dict) -> dict:
        cap = self.CAP_A1
        target = np.asarray(request["target_position_m"], dtype=np.float64).reshape(3)
        max_dur = float(request["max_duration_s"])
        if not self._finite(target, self.get_joint_positions()):
            raise CapabilityError(cap, "nonfinite_state")
        self._check_reachable(cap, target)
        tol, hold = 0.015, 0.5
        err = self._regulate_to_xyz(cap, target, max_dur, tol, hold)
        return {"capability_id": cap, "status": "ok",
                "final_error_m": err, "target_m": target.tolist()}

    # ---- A2: trace_cartesian_path --------------------------------------
    def trace_cartesian_path(self, request: dict) -> dict:
        cap = self.CAP_A2
        wps = [np.asarray(w, dtype=np.float64).reshape(3)
               for w in request["waypoints_m"]]
        seg_dur = float(request["max_duration_per_segment_s"])
        if not self._finite(self.get_joint_positions(), *wps):
            raise CapabilityError(cap, "nonfinite_state")
        for w in wps:
            self._check_reachable(cap, w)
        cross_tol, term_tol, term_hold = 0.02, 0.015, 0.5
        max_cross = 0.0
        prev = self._ee_pos()
        for idx, wp in enumerate(wps):
            is_last = idx == len(wps) - 1
            seg_start = prev.copy()
            cross = self._track_segment(cap, seg_start, wp, seg_dur, cross_tol)
            max_cross = max(max_cross, cross)
            if is_last:
                err = self._regulate_to_xyz(cap, wp, seg_dur, term_tol, term_hold)
            prev = self._ee_pos()
        return {"capability_id": cap, "status": "ok",
                "max_cross_track_m": max_cross, "num_waypoints": len(wps)}

    def _track_segment(self, cap: str, p0: np.ndarray, p1: np.ndarray,
                       seg_dur: float, cross_tol: float) -> float:
        """Follow a straight line p0->p1 with bounded cross-track error."""
        dt = float(self.model.opt.timestep)
        seg = p1 - p0
        seg_len = float(np.linalg.norm(seg))
        seg_dir = seg / seg_len if seg_len > 1e-9 else np.zeros(3)
        t0 = self._now()
        max_cross = 0.0
        move_time = max(dt, 0.85 * seg_dur)
        while True:
            now = self._now()
            elapsed = now - t0
            if elapsed > seg_dur:
                self._stop_motion(True)
                raise CapabilityError(cap, "segment_deadline_expired",
                                      {"max_cross_m": max_cross})
            s = min(1.0, elapsed / move_time) if move_time > 0 else 1.0
            sp = p0 + s * seg
            ee = self._ee_pos()
            rel = ee - p0
            along = float(np.dot(rel, seg_dir))
            proj = p0 + along * seg_dir
            cross = float(np.linalg.norm(ee - proj))
            max_cross = max(max_cross, cross)
            if s >= 1.0 and float(np.linalg.norm(p1 - ee)) <= cross_tol:
                return max_cross
            try:
                q_goal = self.ik(sp, q_init=self.get_joint_positions(),
                                 raise_on_unreachable=False)
            except IKUnreachableError as e:
                q_goal = e.q_final
            q_cur = np.array([self.data.ctrl[a] for a in self._arm_actuator_ids],
                             dtype=np.float64)
            step = q_goal - q_cur
            mx = float(np.max(np.abs(step))) if np.any(step) else 0.0
            if mx > 0.02:
                step = step * (0.02 / mx)
            self.set_arm_actuators(np.clip(q_cur + step, self._q_lo, self._q_hi))
            self._hold_gripper()
            self.step(1)

    # ---- A3: set_gripper_opening ---------------------------------------
    def set_gripper_opening(self, request: dict) -> dict:
        cap = self.CAP_A3
        frac = float(request["opening_fraction"])
        max_dur = float(request["max_duration_s"])
        if not (0.0 <= frac <= 1.0):
            raise CapabilityError(cap, "fraction_out_of_range")
        start_frac = self._grip_aperture_fraction()
        if not np.isfinite(start_frac):
            raise CapabilityError(cap, "nonfinite_gripper_state")
        q_hold = np.clip(self.get_joint_positions(), self._q_lo, self._q_hi)
        self.set_arm_actuators(q_hold)
        target_ctrl = self._grip_ctrl_for_fraction(frac)
        self.set_gripper_control(target_ctrl)
        tol, hold = 0.1, 0.25
        t0 = self._now()
        hold_start = None
        best = float("inf")
        while True:
            now = self._now()
            if now - t0 > max_dur:
                self.set_gripper_control(float(self.data.ctrl[self._gripper_actuator_ids[0]]))
                raise CapabilityError(cap, "deadline_expired",
                                      {"aperture_error": best})
            self.set_arm_actuators(q_hold)  # invariant: arm target unchanged
            self.set_gripper_control(target_ctrl)
            self.step(1)
            cur = self._grip_aperture_fraction()
            err = abs(cur - frac)
            best = min(best, err)
            if err <= tol:
                if hold_start is None:
                    hold_start = now
                elif now - hold_start >= hold:
                    return {"capability_id": cap, "status": "ok",
                            "final_fraction": cur, "target_fraction": frac,
                            "start_fraction": start_frac}
            else:
                hold_start = None

    # ---- contact sensing ----------------------------------------------
    def _robot_body_ids(self) -> set:
        """Body ids belonging to the arm+gripper kinematic chain."""
        mj = self._mj
        ids = set()
        for name in ("base_link", "link1", "link2", "link3", "link4",
                     "link5", "link6", "link7", "link8"):
            bid = mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_BODY, name)
            if bid >= 0:
                ids.add(bid)
        return ids

    def _contact_with_target(self) -> tuple[bool, int, float]:
        """(target_contact, unrelated_external_contact_count, max_penetration).

        A contact is UNRELATED only when a robot geom touches an external
        (non-robot) geom that is NOT the target fixture (floor/other object).
        Robot-internal self contacts (gripper fingers) are structural and are
        not counted. Target contacts report their penetration in metres.
        """
        mj = self._mj
        tgt = mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_GEOM, "fixed_contact_geom")
        robot_body_ids = self._robot_body_ids()
        target_hit = False
        unrelated = 0
        max_pen = 0.0
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            g1, g2 = int(c.geom1), int(c.geom2)
            b1 = int(self.model.geom_bodyid[g1])
            b2 = int(self.model.geom_bodyid[g2])
            r1 = b1 in robot_body_ids
            r2 = b2 in robot_body_ids
            if not (r1 or r2):
                continue
            if r1 and r2:
                continue
            hits_target = (g1 == tgt) or (g2 == tgt)
            if hits_target:
                target_hit = True
                if c.dist < 0:
                    max_pen = max(max_pen, -float(c.dist))
            else:
                unrelated += 1
        return target_hit, unrelated, max_pen

    # ---- A4: approach_until_contact ------------------------------------
    def approach_until_contact(self, request: dict) -> dict:
        cap = self.CAP_A4
        pre = np.asarray(request["precontact_position_m"], dtype=np.float64).reshape(3)
        d = np.asarray(request["approach_direction_unit"], dtype=np.float64).reshape(3)
        max_travel = float(request["max_travel_m"])
        max_speed = float(request["max_approach_speed_m_s"])
        max_dur = float(request["max_duration_s"])
        nrm = float(np.linalg.norm(d))
        if nrm < 1e-9 or not self._finite(pre, d):
            raise CapabilityError(cap, "invalid_direction")
        d = d / nrm
        if not self._finite(self.get_joint_positions()):
            raise CapabilityError(cap, "nonfinite_state")
        self._check_reachable(cap, pre)
        # Phase 1: reach precontact pose and let residual velocity decay so we
        # start the ray from rest (keeps post-contact speed bounded).
        self._regulate_to_xyz(cap, pre, min(max_dur, 8.0), 0.015, 0.1)
        self._settle_to_rest(cap, max_dur)
        # Phase 2: advance along ray until controlled contact.
        dt = float(self.model.opt.timestep)
        start = self._ee_pos()
        t0 = self._now()
        # Cap the approach speed well below the 0.02 post-contact gate so the
        # EE never carries excess momentum into contact. Also bound by request.
        eff_speed = min(max_speed, 0.010)
        step_dist = eff_speed * dt
        traveled = 0.0
        contact_hold_start = None
        contacted = False  # latch: once contact seen, never advance again
        while True:
            now = self._now()
            if now - t0 > max_dur:
                self._stop_motion(True)
                raise CapabilityError(cap, "deadline_expired", {"traveled_m": traveled})
            tgt_hit, unrelated, pen = self._contact_with_target()
            if unrelated > 0:
                self._stop_motion(True)
                raise CapabilityError(cap, "unrelated_contact",
                                      {"unrelated_count": unrelated})
            if tgt_hit:
                contacted = True
            if contacted:
                # Latched: arrest all motion and hold current pose. Only begin
                # counting the contact hold once speed and penetration gates
                # are simultaneously satisfied.
                self._stop_motion(True)
                self.step(1)
                speed = self._ee_speed()
                still_hit, unrel2, pen2 = self._contact_with_target()
                if unrel2 > 0:
                    raise CapabilityError(cap, "unrelated_contact",
                                          {"unrelated_count": unrel2})
                if still_hit and speed <= 0.02 and pen2 <= 0.005:
                    if contact_hold_start is None:
                        contact_hold_start = now
                    elif now - contact_hold_start >= 0.1:
                        return {"capability_id": cap, "status": "ok",
                                "traveled_m": traveled, "penetration_m": pen2,
                                "post_contact_speed": speed}
                else:
                    contact_hold_start = None
                continue
            if traveled >= max_travel:
                self._stop_motion(True)
                raise CapabilityError(cap, "travel_exhausted", {"traveled_m": traveled})
            # advance the Cartesian setpoint by a bounded step along the ray.
            traveled = min(max_travel, traveled + step_dist)
            sp = start + traveled * d
            try:
                q_goal = self.ik(sp, q_init=self.get_joint_positions(),
                                 raise_on_unreachable=False)
            except IKUnreachableError as e:
                q_goal = e.q_final
            # Rate-limit the joint command so the tracked speed stays low.
            q_cur = np.array([self.data.ctrl[a] for a in self._arm_actuator_ids],
                             dtype=np.float64)
            jstep = q_goal - q_cur
            mx = float(np.max(np.abs(jstep))) if np.any(jstep) else 0.0
            cap_step = 0.004  # rad/tick -> keeps EE well under 0.02 m/s
            if mx > cap_step:
                jstep = jstep * (cap_step / mx)
            self.set_arm_actuators(np.clip(q_cur + jstep, self._q_lo, self._q_hi))
            self._hold_gripper()
            self.step(1)

    def _settle_to_rest(self, cap: str, max_dur: float,
                        speed_tol: float = 0.005, max_settle_s: float = 0.5) -> None:
        """Hold current pose until EE speed drops below `speed_tol`."""
        t0 = self._now()
        while self._ee_speed() > speed_tol:
            if self._now() - t0 > max_settle_s:
                break
            self._stop_motion(True)
            self.step(1)

    # ---- A5: move_cartesian_offset_and_return --------------------------
    def move_cartesian_offset_and_return(self, request: dict) -> dict:
        cap = self.CAP_A5
        offset = np.asarray(request["offset_robot_base_m"], dtype=np.float64).reshape(3)
        leg_dur = float(request["max_duration_per_leg_s"])
        if not self._finite(offset, self.get_joint_positions()):
            raise CapabilityError(cap, "nonfinite_state")
        # Capture the robot-base frame at start. base_link is fixed to world,
        # so its world orientation defines the base frame for the offset.
        mj = self._mj
        bid = mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_BODY, "base_link")
        R_base = np.array(self.data.xmat[bid], dtype=np.float64).reshape(3, 3)
        start_ee = self._ee_pos()
        world_offset = R_base @ offset
        outbound = start_ee + world_offset
        self._check_reachable(cap, outbound)
        self._check_reachable(cap, start_ee)
        # Outbound leg: regulate to outbound target, hold 0.25 s.
        err_out = self._regulate_to_xyz(cap, outbound, leg_dur, 0.015, 0.25)
        achieved = self._ee_pos() - start_ee
        req_norm = float(np.linalg.norm(world_offset))
        frac = (float(np.dot(achieved, world_offset)) / (req_norm ** 2)
                if req_norm > 1e-9 else 1.0)
        if frac < 0.8:
            self._stop_motion(True)
            raise CapabilityError(cap, "insufficient_outbound_displacement",
                                  {"displacement_fraction": frac})
        # Return leg: only after outbound completed. Regulate back, hold 0.5 s.
        err_ret = self._regulate_to_xyz(cap, start_ee, leg_dur, 0.015, 0.5)
        return {"capability_id": cap, "status": "ok",
                "outbound_error_m": err_out, "return_error_m": err_ret,
                "displacement_fraction": frac}


def build() -> Robot:
    spec = ArmSpec(
        ee_site_name="ee_site",
        arm_joint_names=["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"],
        arm_actuator_names=["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"],
        joint_limits={
            "joint1": (-2.618, 2.618),
            "joint2": (0.0, 3.14),
            "joint3": (-2.697, 0.0),
            "joint4": (-1.832, 1.832),
            "joint5": (-1.22, 1.22),
            "joint6": (-3.14, 3.14),
        },
        home_qpos=[0.0, 0.9, -0.9, 0.0, 0.4, 0.0],
        ik_damping=5e-3,
        ik_max_iter=80,
        ik_tolerance=2e-3,
        ik_step_clamp=0.15,
        ik_raise_on_unreachable=True,
        gripper_actuator_names=["gripper"],
        gripper_joint_names=["joint7", "joint8"],
        gripper_close_ctrl=_GRIP_CLOSED_CTRL,
        gripper_open_ctrl=_GRIP_OPEN_CTRL,
        gripper_settle_steps=60,
        grasp_backend="noop",
        sim_dt=0.002,
    )
    return Robot.from_mjcf("mjcf.xml", spec=spec)
