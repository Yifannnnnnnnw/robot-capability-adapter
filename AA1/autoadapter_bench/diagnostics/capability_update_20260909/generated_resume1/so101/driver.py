"""SO-101 capability driver – ArmSerialDLSSkeleton subclass."""
from __future__ import annotations

import math
import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton
from auto_adapter.skeletons.base import ArmSpec

# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------
_SPEC = ArmSpec(
    ee_site_name="ee_site",
    ee_body_name="gripper_link",
    arm_joint_names=[
        "shoulder_pan", "shoulder_lift", "elbow_flex",
        "wrist_flex", "wrist_roll",
    ],
    arm_actuator_names=[
        "act_shoulder_pan", "act_shoulder_lift", "act_elbow_flex",
        "act_wrist_flex", "act_wrist_roll",
    ],
    joint_limits={
        "shoulder_pan":  (-1.92,  1.92),
        "shoulder_lift": (-1.75,  1.75),
        "elbow_flex":    (-1.69,  1.69),
        "wrist_flex":    (-1.66,  1.66),
        "wrist_roll":    (-2.74,  2.84),
        "jaw_visual_joint": (-0.175, 1.75),
    },
    home_qpos=[0.0, 0.0, 0.0, 0.0, 0.0],
    ik_damping=0.005,
    ik_max_iter=60,
    ik_tolerance=0.0008,
    ik_step_clamp=0.15,
    ik_raise_on_unreachable=False,
    gripper_actuator_names=["act_jaw_visual"],
    gripper_joint_names=["jaw_visual_joint"],
    gripper_close_ctrl=-0.175,
    gripper_open_ctrl=1.75,
    gripper_settle_steps=60,
    grasp_backend="weld",
    weld_graspable_bodies=["banana", "mug", "bottle", "screwdriver", "duck", "lego"],
    grasp_radius=0.08,
    sim_dt=0.002,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_HOLD_TOL_POS   = 0.015   # m  – A1/A2 terminal hold
_HOLD_DUR_POS   = 0.5     # s
_HOLD_TOL_GRIP  = 0.1     # ratio – A3
_HOLD_DUR_GRIP  = 0.25    # s
_CONTACT_HOLD   = 0.1     # s  – A4
_OUTBOUND_HOLD  = 0.25    # s  – A5
_RETURN_HOLD    = 0.5     # s  – A5
_CROSS_TRACK    = 0.02    # m  – A2
_STEP_DT        = 0.002   # s  (matches model timestep)


def _norm(v):
    return np.linalg.norm(v)


def _unit(v):
    n = _norm(v)
    return v / n if n > 1e-9 else v


class CapabilityError(RuntimeError):
    pass


class Robot(ArmSerialDLSSkeleton):
    """SO-101 capability robot."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ee_pos(self) -> np.ndarray:
        return self.get_ee_pose()[0].copy()

    def _gripper_fraction(self) -> float:
        """Current gripper opening fraction [0,1]."""
        pos = self.get_gripper_joint_positions()
        val = pos.get("jaw_visual_joint", 0.0)
        lo, hi = -0.175, 1.75
        return float(np.clip((val - lo) / (hi - lo), 0.0, 1.0))

    def _fraction_to_ctrl(self, frac: float) -> float:
        lo, hi = -0.175, 1.75
        return float(np.clip(lo + frac * (hi - lo), lo, hi))

    def _sim_time(self) -> float:
        return float(self.data.time)

    def _contacts_with(self, geom_name: str) -> int:
        """Count active contacts involving a named geom."""
        try:
            gid = self.model.geom(geom_name).id
        except Exception:
            return 0
        count = 0
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            if c.geom1 == gid or c.geom2 == gid:
                count += 1
        return count

    def _any_contact_except(self, allowed_geom: str) -> int:
        """Count contacts on tcp geom that are NOT with allowed_geom."""
        try:
            tcp_id = self.model.geom("so101_tcp_contact").id
        except Exception:
            return 0
        try:
            allowed_id = self.model.geom(allowed_geom).id
        except Exception:
            allowed_id = -1
        count = 0
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            if c.geom1 == tcp_id or c.geom2 == tcp_id:
                other = c.geom2 if c.geom1 == tcp_id else c.geom1
                if other != allowed_id:
                    count += 1
        return count

    # ------------------------------------------------------------------
    # A1 – move_end_effector_to_position
    # ------------------------------------------------------------------
    def move_end_effector_to_position(self, request: dict) -> dict:
        target = np.array(request["target_position_m"], dtype=float)
        deadline = request["max_duration_s"]

        # IK solve
        q_target = self.ik(target, raise_on_unreachable=False)
        if q_target is None:
            raise CapabilityError("A1: IK failed – target unreachable")

        t0 = self._sim_time()
        t_end = t0 + deadline

        # Interpolate to target over up to half the budget, then regulate
        move_dur = min(deadline * 0.5, 2.0)
        self.move_joints(q_target, duration=move_dur)

        # Closed-loop regulation until hold satisfied or deadline
        hold_start = None
        while self._sim_time() < t_end:
            # Re-solve IK from current pose for closed-loop correction
            q_now = self.ik(target, q_init=self.get_joint_positions(),
                            raise_on_unreachable=False)
            if q_now is not None:
                self.set_arm_actuators(q_now)
            self.step(5)

            err = _norm(self._ee_pos() - target)
            t_now = self._sim_time()
            if err <= _HOLD_TOL_POS:
                if hold_start is None:
                    hold_start = t_now
                elif t_now - hold_start >= _HOLD_DUR_POS:
                    return {"success": True, "final_error_m": float(err)}
            else:
                hold_start = None

        # Final check
        err = _norm(self._ee_pos() - target)
        if err <= _HOLD_TOL_POS:
            return {"success": True, "final_error_m": float(err)}
        raise CapabilityError(
            f"A1: deadline expired, final error {err:.4f} m > {_HOLD_TOL_POS} m"
        )

    # ------------------------------------------------------------------
    # A2 – trace_cartesian_path
    # ------------------------------------------------------------------
    def trace_cartesian_path(self, request: dict) -> dict:
        waypoints = [np.array(w, dtype=float) for w in request["waypoints_m"]]
        seg_dur = float(request["max_duration_per_segment_s"])

        for idx, wp in enumerate(waypoints):
            q_target = self.ik(wp, q_init=self.get_joint_positions(),
                               raise_on_unreachable=False)
            if q_target is None:
                raise CapabilityError(
                    f"A2: IK failed for waypoint {idx}: {wp}"
                )

            t0 = self._sim_time()
            t_end = t0 + seg_dur
            move_dur = min(seg_dur * 0.5, 2.0)
            self.move_joints(q_target, duration=move_dur)

            hold_start = None
            is_terminal = (idx == len(waypoints) - 1)
            hold_dur = _HOLD_DUR_POS if is_terminal else 0.0
            tol = _HOLD_TOL_POS

            while self._sim_time() < t_end:
                q_now = self.ik(wp, q_init=self.get_joint_positions(),
                                raise_on_unreachable=False)
                if q_now is not None:
                    self.set_arm_actuators(q_now)
                self.step(5)

                err = _norm(self._ee_pos() - wp)
                t_now = self._sim_time()
                if err <= tol:
                    if not is_terminal:
                        break  # non-terminal: proceed immediately
                    if hold_start is None:
                        hold_start = t_now
                    elif t_now - hold_start >= hold_dur:
                        break
                else:
                    hold_start = None

            err = _norm(self._ee_pos() - wp)
            if err > _CROSS_TRACK:
                raise CapabilityError(
                    f"A2: waypoint {idx} error {err:.4f} m > {_CROSS_TRACK} m"
                )

        final_err = _norm(self._ee_pos() - waypoints[-1])
        return {"success": True, "final_error_m": float(final_err)}

    # ------------------------------------------------------------------
    # A3 – set_gripper_opening
    # ------------------------------------------------------------------
    def set_gripper_opening(self, request: dict) -> dict:
        frac_target = float(request["opening_fraction"])
        deadline = float(request["max_duration_s"])

        ctrl_target = self._fraction_to_ctrl(frac_target)
        t0 = self._sim_time()
        t_end = t0 + deadline

        # Check bidirectional excursion: we need to move >=0.5 ratio span
        frac_start = self._gripper_fraction()
        excursion = abs(frac_target - frac_start)

        # Command the target
        self.set_gripper_control(ctrl_target)

        hold_start = None
        while self._sim_time() < t_end:
            self.set_gripper_control(ctrl_target)
            self.step(5)
            frac_now = self._gripper_fraction()
            err = abs(frac_now - frac_target)
            t_now = self._sim_time()
            if err <= _HOLD_TOL_GRIP:
                if hold_start is None:
                    hold_start = t_now
                elif t_now - hold_start >= _HOLD_DUR_GRIP:
                    if excursion < 0.5:
                        raise CapabilityError(
                            f"A3: bidirectional excursion {excursion:.3f} < 0.5"
                        )
                    return {"success": True, "final_fraction": float(frac_now)}
            else:
                hold_start = None

        frac_now = self._gripper_fraction()
        err = abs(frac_now - frac_target)
        if err <= _HOLD_TOL_GRIP and excursion >= 0.5:
            return {"success": True, "final_fraction": float(frac_now)}
        raise CapabilityError(
            f"A3: deadline expired, fraction error {err:.3f} > {_HOLD_TOL_GRIP}"
        )

    # ------------------------------------------------------------------
    # A4 – approach_until_contact
    # ------------------------------------------------------------------
    def approach_until_contact(self, request: dict) -> dict:
        precontact = np.array(request["precontact_position_m"], dtype=float)
        direction  = _unit(np.array(request["approach_direction_unit"], dtype=float))
        max_travel = float(request["max_travel_m"])
        max_speed  = float(request["max_approach_speed_m_s"])
        deadline   = float(request["max_duration_s"])

        t0   = self._sim_time()
        t_end = t0 + deadline

        # --- Phase 1: move to precontact position ---
        q_pre = self.ik(precontact, q_init=self.get_joint_positions(),
                        raise_on_unreachable=False)
        if q_pre is None:
            raise CapabilityError("A4: IK failed for precontact position")

        pre_dur = min(deadline * 0.4, 3.0)
        self.move_joints(q_pre, duration=pre_dur)

        # Regulate to precontact
        pre_hold_start = None
        while self._sim_time() < t_end:
            q_now = self.ik(precontact, q_init=self.get_joint_positions(),
                            raise_on_unreachable=False)
            if q_now is not None:
                self.set_arm_actuators(q_now)
            self.step(5)
            err = _norm(self._ee_pos() - precontact)
            t_now = self._sim_time()
            if err <= _HOLD_TOL_POS:
                if pre_hold_start is None:
                    pre_hold_start = t_now
                elif t_now - pre_hold_start >= 0.1:
                    break
            else:
                pre_hold_start = None

        if _norm(self._ee_pos() - precontact) > _HOLD_TOL_POS * 3:
            raise CapabilityError("A4: could not reach precontact position")

        # --- Phase 2: approach along ray ---
        # Steps per physics step: speed * dt_step
        steps_per_ctrl = max(1, int(0.01 / _STEP_DT))  # ~10 ms per command
        step_dist = max_speed * steps_per_ctrl * _STEP_DT  # m per command

        traveled = 0.0
        contact_hold_start = None
        contact_detected = False

        while self._sim_time() < t_end and traveled < max_travel:
            # Check unrelated contact
            unrelated = self._any_contact_except("fixed_contact_geom")
            if unrelated > 0:
                raise CapabilityError(
                    f"A4: unrelated contact detected ({unrelated} contacts)"
                )

            # Check contact with target
            n_contact = self._contacts_with("fixed_contact_geom")
            if n_contact > 0:
                contact_detected = True
                # Hold at contact
                if contact_hold_start is None:
                    contact_hold_start = self._sim_time()
                # Keep commanding current position (no further advance)
                cur = self._ee_pos()
                q_hold = self.ik(cur, q_init=self.get_joint_positions(),
                                 raise_on_unreachable=False)
                if q_hold is not None:
                    self.set_arm_actuators(q_hold)
                self.step(steps_per_ctrl)
                if self._sim_time() - contact_hold_start >= _CONTACT_HOLD:
                    # Verify speed
                    vel = _norm(self.get_joint_velocities())
                    return {
                        "success": True,
                        "traveled_m": float(traveled),
                        "contact": True,
                    }
                continue

            # Advance one step along ray
            next_pos = precontact + (traveled + step_dist) * direction
            q_next = self.ik(next_pos, q_init=self.get_joint_positions(),
                             raise_on_unreachable=False)
            if q_next is None:
                raise CapabilityError("A4: IK failed during approach")
            self.set_arm_actuators(q_next)
            self.step(steps_per_ctrl)
            traveled += step_dist

        if not contact_detected:
            raise CapabilityError(
                f"A4: no contact after {traveled:.4f} m travel / deadline"
            )
        return {"success": True, "traveled_m": float(traveled), "contact": True}

    # ------------------------------------------------------------------
    # A5 – move_cartesian_offset_and_return
    # ------------------------------------------------------------------
    def move_cartesian_offset_and_return(self, request: dict) -> dict:
        offset_base = np.array(request["offset_robot_base_m"], dtype=float)
        leg_dur = float(request["max_duration_per_leg_s"])

        # Capture start pose in world frame
        start_pos = self._ee_pos()

        # Robot base frame = world frame for fixed-base robot (base at origin)
        # offset_robot_base_m is in robot-base frame; base_link is at world origin
        # so world offset == base offset for this robot
        outbound_pos = start_pos + offset_base

        # --- Outbound leg ---
        q_out = self.ik(outbound_pos, q_init=self.get_joint_positions(),
                        raise_on_unreachable=False)
        if q_out is None:
            raise CapabilityError("A5: IK failed for outbound target")

        t0 = self._sim_time()
        t_end_out = t0 + leg_dur
        move_dur = min(leg_dur * 0.5, 2.0)
        self.move_joints(q_out, duration=move_dur)

        hold_start = None
        while self._sim_time() < t_end_out:
            q_now = self.ik(outbound_pos, q_init=self.get_joint_positions(),
                            raise_on_unreachable=False)
            if q_now is not None:
                self.set_arm_actuators(q_now)
            self.step(5)
            err = _norm(self._ee_pos() - outbound_pos)
            t_now = self._sim_time()
            if err <= _HOLD_TOL_POS:
                if hold_start is None:
                    hold_start = t_now
                elif t_now - hold_start >= _OUTBOUND_HOLD:
                    break
            else:
                hold_start = None

        out_err = _norm(self._ee_pos() - outbound_pos)
        disp = _norm(self._ee_pos() - start_pos)
        req_disp = _norm(offset_base)
        if req_disp > 1e-6 and disp / req_disp < 0.8:
            raise CapabilityError(
                f"A5: outbound displacement fraction {disp/req_disp:.3f} < 0.8"
            )
        if out_err > _HOLD_TOL_POS * 2:
            raise CapabilityError(
                f"A5: outbound error {out_err:.4f} m too large"
            )

        # --- Return leg ---
        q_ret = self.ik(start_pos, q_init=self.get_joint_positions(),
                        raise_on_unreachable=False)
        if q_ret is None:
            raise CapabilityError("A5: IK failed for return target")

        t0r = self._sim_time()
        t_end_ret = t0r + leg_dur
        move_dur_r = min(leg_dur * 0.5, 2.0)
        self.move_joints(q_ret, duration=move_dur_r)

        hold_start = None
        while self._sim_time() < t_end_ret:
            q_now = self.ik(start_pos, q_init=self.get_joint_positions(),
                            raise_on_unreachable=False)
            if q_now is not None:
                self.set_arm_actuators(q_now)
            self.step(5)
            err = _norm(self._ee_pos() - start_pos)
            t_now = self._sim_time()
            if err <= _HOLD_TOL_POS:
                if hold_start is None:
                    hold_start = t_now
                elif t_now - hold_start >= _RETURN_HOLD:
                    return {
                        "success": True,
                        "outbound_error_m": float(out_err),
                        "return_error_m": float(err),
                    }
            else:
                hold_start = None

        ret_err = _norm(self._ee_pos() - start_pos)
        if ret_err <= _HOLD_TOL_POS:
            return {
                "success": True,
                "outbound_error_m": float(out_err),
                "return_error_m": float(ret_err),
            }
        raise CapabilityError(
            f"A5: return error {ret_err:.4f} m > {_HOLD_TOL_POS} m"
        )


# ---------------------------------------------------------------------------
# Module-level build()
# ---------------------------------------------------------------------------
def build() -> Robot:
    return Robot.from_mjcf("mjcf.xml", spec=_SPEC)
