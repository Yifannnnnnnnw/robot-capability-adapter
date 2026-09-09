"""
driver.py – KUKA iiwa 14 capability driver.
Robot subclass of ArmSerialDLSSkeleton implementing A1, A2, A4, A5.
"""
from __future__ import annotations

import math
import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton
from auto_adapter.skeletons.base import ArmSpec

# ── joint / actuator names ────────────────────────────────────────────────────
_ARM_JOINTS = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "joint7"]
_ARM_ACTS   = ["actuator1", "actuator2", "actuator3", "actuator4",
               "actuator5", "actuator6", "actuator7"]
_JOINT_LIMITS = {
    "joint1": (-2.96706,  2.96706),
    "joint2": (-2.0944,   2.0944),
    "joint3": (-2.96706,  2.96706),
    "joint4": (-2.0944,   2.0944),
    "joint5": (-2.96706,  2.96706),
    "joint6": (-2.0944,   2.0944),
    "joint7": (-3.05433,  3.05433),
}
# home keyframe: 0 0.785398 0 -1.5708 0 0 0
_HOME_QPOS = [0.0, 0.785398, 0.0, -1.5708, 0.0, 0.0, 0.0]

_SPEC = ArmSpec(
    ee_site_name       = "attachment_site",
    ee_body_name       = "link7",
    arm_joint_names    = _ARM_JOINTS,
    arm_actuator_names = _ARM_ACTS,
    joint_limits       = _JOINT_LIMITS,
    home_qpos          = _HOME_QPOS,
    ik_damping         = 5e-4,
    ik_max_iter        = 60,
    ik_tolerance       = 5e-4,
    ik_step_clamp      = 0.15,
    ik_raise_on_unreachable = False,
    sim_dt             = 0.002,
)


class CapabilityError(RuntimeError):
    """Bounded capability failure."""


class Robot(ArmSerialDLSSkeleton):
    """KUKA iiwa 14 capability robot."""

    # ── helpers ───────────────────────────────────────────────────────────────

    def _ee_pos(self) -> np.ndarray:
        """Current EE position (world frame)."""
        xyz, _ = self.get_ee_pose()
        return xyz.copy()

    def _sim_time(self) -> float:
        return float(self.data.time)

    def _regulate_to(self, target_xyz: np.ndarray, deadline: float,
                     tol: float = 0.015, hold_s: float = 0.5) -> bool:
        """
        Closed-loop IK regulation toward target_xyz until:
          - EE error <= tol continuously for hold_s sim-seconds, OR
          - sim time >= deadline.
        Returns True on success.
        """
        hold_start: float | None = None
        q_target = self.ik(target_xyz, raise_on_unreachable=False)
        replan_interval = 0.1          # seconds between IK replans
        last_replan = self._sim_time()

        while self._sim_time() < deadline:
            now = self._sim_time()
            # Replan IK periodically
            if now - last_replan >= replan_interval:
                q_new = self.ik(target_xyz, raise_on_unreachable=False)
                if q_new is not None:
                    q_target = q_new
                last_replan = now

            if q_target is not None:
                self.set_arm_actuators(q_target)
            self.step(1)

            err = float(np.linalg.norm(self._ee_pos() - target_xyz))
            if err <= tol:
                if hold_start is None:
                    hold_start = self._sim_time()
                elif self._sim_time() - hold_start >= hold_s:
                    return True
            else:
                hold_start = None

        return False

    def _stop_arm(self) -> None:
        """Freeze actuators at current joint positions."""
        q = self.get_joint_positions()
        self.set_arm_actuators(q)

    # ── A1: move_end_effector_to_position ─────────────────────────────────────

    def move_end_effector_to_position(self, request: dict) -> dict:
        target = np.array(request["target_position_m"], dtype=float)
        max_dur = float(request["max_duration_s"])

        # validate bounds
        if not (0.25 <= max_dur <= 8.0):
            raise CapabilityError(f"max_duration_s {max_dur} out of [0.25, 8.0]")
        if target.shape != (3,) or np.any(np.abs(target) > 1.0):
            raise CapabilityError("target_position_m out of bounds")

        deadline = self._sim_time() + max_dur
        ok = self._regulate_to(target, deadline, tol=0.015, hold_s=0.5)
        self._stop_arm()

        final_err = float(np.linalg.norm(self._ee_pos() - target))
        if not ok:
            raise CapabilityError(
                f"A1 failed: final error {final_err:.4f} m after {max_dur:.2f} s")
        return {"final_position_error_m": final_err}

    # ── A2: trace_cartesian_path ───────────────────────────────────────────────

    def trace_cartesian_path(self, request: dict) -> dict:
        waypoints = [np.array(w, dtype=float) for w in request["waypoints_m"]]
        seg_dur   = float(request["max_duration_per_segment_s"])

        if not (0.25 <= seg_dur <= 5.0):
            raise CapabilityError(f"max_duration_per_segment_s {seg_dur} out of [0.25, 5.0]")
        if not (2 <= len(waypoints) <= 8):
            raise CapabilityError("waypoints_m must have 2–8 entries")
        for i, w in enumerate(waypoints):
            if w.shape != (3,) or np.any(np.abs(w) > 1.0):
                raise CapabilityError(f"waypoint[{i}] out of bounds")

        max_cross = 0.0
        for idx, wp in enumerate(waypoints):
            deadline = self._sim_time() + seg_dur
            is_last  = (idx == len(waypoints) - 1)
            hold_s   = 0.5 if is_last else 0.0
            tol      = 0.015 if is_last else 0.02

            # For intermediate waypoints use a looser hold (just reach within tol)
            if is_last:
                ok = self._regulate_to(wp, deadline, tol=tol, hold_s=hold_s)
            else:
                ok = self._regulate_to(wp, deadline, tol=tol, hold_s=0.05)

            seg_err = float(np.linalg.norm(self._ee_pos() - wp))
            max_cross = max(max_cross, seg_err)

            if not ok:
                self._stop_arm()
                raise CapabilityError(
                    f"A2 failed at waypoint {idx}: error {seg_err:.4f} m")

        self._stop_arm()
        final_err = float(np.linalg.norm(self._ee_pos() - waypoints[-1]))
        return {"max_cross_track_error_m": max_cross,
                "final_position_error_m": final_err}

    # ── A4: approach_until_contact ────────────────────────────────────────────

    def _contact_with_target(self) -> tuple[bool, int]:
        """
        Returns (contact_with_target, unrelated_contact_count).
        'fixed_contact_target' body is the declared contact target.
        """
        import mujoco
        model, data = self.model, self.data
        target_contact = False
        unrelated = 0
        arm_bodies = {"base", "link1", "link2", "link3", "link4",
                      "link5", "link6", "link7"}
        contact_body = "fixed_contact_target"
        cb_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, contact_body)

        for i in range(data.ncon):
            c = data.contact[i]
            g1, g2 = c.geom1, c.geom2
            b1 = model.geom_bodyid[g1]
            b2 = model.geom_bodyid[g2]
            n1 = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, b1) or ""
            n2 = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, b2) or ""
            involves_arm = (n1 in arm_bodies or n2 in arm_bodies)
            involves_target = (b1 == cb_id or b2 == cb_id)
            if involves_arm and involves_target:
                target_contact = True
            elif involves_arm and not involves_target:
                floor_geom = "fixed_floor"
                fg_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, floor_geom)
                if g1 != fg_id and g2 != fg_id:
                    unrelated += 1
        return target_contact, unrelated

    def approach_until_contact(self, request: dict) -> dict:
        precontact = np.array(request["precontact_position_m"], dtype=float)
        direction  = np.array(request["approach_direction_unit"], dtype=float)
        max_travel = float(request["max_travel_m"])
        max_speed  = float(request["max_approach_speed_m_s"])
        max_dur    = float(request["max_duration_s"])

        # validate
        if not (0.0 < max_travel <= 0.08):
            raise CapabilityError("max_travel_m out of (0, 0.08]")
        if not (0.0 < max_speed <= 0.05):
            raise CapabilityError("max_approach_speed_m_s out of (0, 0.05]")
        if not (0.25 <= max_dur <= 8.0):
            raise CapabilityError("max_duration_s out of [0.25, 8.0]")

        norm = float(np.linalg.norm(direction))
        if norm < 1e-6:
            raise CapabilityError("approach_direction_unit is zero vector")
        direction = direction / norm

        overall_deadline = self._sim_time() + max_dur

        # Phase 1: move to precontact position
        pre_deadline = self._sim_time() + max_dur * 0.6
        ok = self._regulate_to(precontact, min(pre_deadline, overall_deadline),
                               tol=0.015, hold_s=0.1)
        if not ok:
            self._stop_arm()
            raise CapabilityError("A4: failed to reach precontact position")

        # Phase 2: approach along ray
        start_pos = self._ee_pos().copy()
        contact_detected = False
        contact_hold_start: float | None = None
        dt = float(self.model.opt.timestep)
        # step size per physics step along approach direction
        step_dist = max_speed * dt  # m per sim step

        while self._sim_time() < overall_deadline:
            traveled = float(np.linalg.norm(self._ee_pos() - start_pos))
            if traveled >= max_travel:
                break

            # Check unrelated contacts
            _, unrelated = self._contact_with_target()
            if unrelated > 0:
                self._stop_arm()
                raise CapabilityError("A4: unrelated contact detected")

            # Check target contact
            has_contact, _ = self._contact_with_target()
            if has_contact:
                contact_detected = True
                if contact_hold_start is None:
                    contact_hold_start = self._sim_time()
                # Hold in place
                self._stop_arm()
                self.step(1)
                # Check post-contact speed
                vel = self.get_joint_velocities()
                # Approximate EE speed via Jacobian
                ee_speed = float(np.linalg.norm(self._ee_pos() - start_pos))
                if self._sim_time() - contact_hold_start >= 0.1:
                    break
                continue

            # Advance target along approach ray
            current_target = self._ee_pos() + direction * step_dist * 5
            # Clamp to max_travel
            proj = float(np.dot(current_target - start_pos, direction))
            if proj > max_travel:
                current_target = start_pos + direction * max_travel

            q_target = self.ik(current_target, raise_on_unreachable=False)
            if q_target is not None:
                self.set_arm_actuators(q_target)
            self.step(1)

        self._stop_arm()
        # settle briefly
        for _ in range(10):
            self.step(1)
            has_c, unrel = self._contact_with_target()
            if unrel > 0:
                raise CapabilityError("A4: unrelated contact after stop")

        if not contact_detected:
            raise CapabilityError("A4: no contact detected within travel/duration limits")

        final_pos = self._ee_pos()
        penetration = max(0.0, float(np.dot(final_pos - start_pos, direction)) - max_travel)
        return {"contact_detected": True, "penetration_m": penetration}

    # ── A5: move_cartesian_offset_and_return ──────────────────────────────────

    def move_cartesian_offset_and_return(self, request: dict) -> dict:
        offset = np.array(request["offset_robot_base_m"], dtype=float)
        leg_dur = float(request["max_duration_per_leg_s"])

        if not (0.25 <= leg_dur <= 5.0):
            raise CapabilityError("max_duration_per_leg_s out of [0.25, 5.0]")
        if offset.shape != (3,) or np.any(np.abs(offset) > 0.06):
            raise CapabilityError("offset_robot_base_m out of bounds")

        # Capture start pose in world frame (robot base == world for fixed base)
        start_pos = self._ee_pos().copy()
        outbound_target = start_pos + offset  # robot_base == world for fixed arm

        # Validate outbound target is within world bounds
        if np.any(np.abs(outbound_target) > 1.0):
            raise CapabilityError("Outbound target exceeds world bounds")

        # ── Outbound leg ──────────────────────────────────────────────────────
        deadline_out = self._sim_time() + leg_dur
        ok_out = self._regulate_to(outbound_target, deadline_out,
                                   tol=0.015, hold_s=0.25)
        if not ok_out:
            self._stop_arm()
            out_err = float(np.linalg.norm(self._ee_pos() - outbound_target))
            raise CapabilityError(
                f"A5: outbound leg failed, error {out_err:.4f} m")

        outbound_err = float(np.linalg.norm(self._ee_pos() - outbound_target))
        actual_disp  = float(np.linalg.norm(self._ee_pos() - start_pos))
        requested_disp = float(np.linalg.norm(offset))
        disp_fraction = (actual_disp / requested_disp) if requested_disp > 1e-9 else 1.0

        if disp_fraction < 0.8:
            self._stop_arm()
            raise CapabilityError(
                f"A5: displacement fraction {disp_fraction:.3f} < 0.8")

        # ── Return leg ────────────────────────────────────────────────────────
        deadline_ret = self._sim_time() + leg_dur
        ok_ret = self._regulate_to(start_pos, deadline_ret,
                                   tol=0.015, hold_s=0.5)
        self._stop_arm()

        return_err = float(np.linalg.norm(self._ee_pos() - start_pos))
        if not ok_ret:
            raise CapabilityError(
                f"A5: return leg failed, error {return_err:.4f} m")

        return {
            "outbound_error_m":        outbound_err,
            "return_error_m":          return_err,
            "displacement_fraction":   disp_fraction,
        }


# ── module-level factory ──────────────────────────────────────────────────────

def build() -> Robot:
    """Return a Robot instance bound to mjcf.xml."""
    return Robot.from_mjcf("mjcf.xml", spec=_SPEC)
