"""Franka Panda capability driver – ArmSerialDLSSkeleton subclass."""
from __future__ import annotations

import numpy as np
import mujoco

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton
from auto_adapter.skeletons.base import ArmSpec

# ── Franka joint / actuator names ──────────────────────────────────────────
_ARM_JOINTS = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "joint7"]
_ARM_ACTS   = ["actuator1", "actuator2", "actuator3", "actuator4",
               "actuator5", "actuator6", "actuator7"]
_GRIP_ACTS   = ["actuator8"]
_GRIP_JOINTS = ["finger_joint1", "finger_joint2"]

# actuator8 ctrl: 0 = fully closed, 255 = fully open
_GRIP_CLOSE = 0.0
_GRIP_OPEN  = 255.0

# finger_joint range [0, 0.04] m each
_FINGER_MAX = 0.04

_HOME_QPOS = [0.0, 0.0, 0.0, -1.57079, 0.0, 1.57079, -0.7853]

_SPEC = ArmSpec(
    ee_site_name        = "fixed_tcp",
    ee_body_name        = "hand",
    arm_joint_names     = _ARM_JOINTS,
    arm_actuator_names  = _ARM_ACTS,
    joint_limits        = {
        "joint1": (-2.8973,  2.8973),
        "joint2": (-1.7628,  1.7628),
        "joint3": (-2.8973,  2.8973),
        "joint4": (-3.0718, -0.0698),
        "joint5": (-2.8973,  2.8973),
        "joint6": (-0.0175,  3.7525),
        "joint7": (-2.8973,  2.8973),
    },
    home_qpos           = _HOME_QPOS,
    ik_damping          = 1e-3,
    ik_max_iter         = 60,
    ik_tolerance        = 5e-4,
    ik_step_clamp       = 0.15,
    ik_raise_on_unreachable = False,
    gripper_actuator_names = _GRIP_ACTS,
    gripper_joint_names    = _GRIP_JOINTS,
    gripper_close_ctrl     = _GRIP_CLOSE,
    gripper_open_ctrl      = _GRIP_OPEN,
    gripper_settle_steps   = 50,
    sim_dt                 = 0.002,
)


class CapabilityError(RuntimeError):
    """Raised when a bounded capability cannot be completed."""


class Robot(ArmSerialDLSSkeleton):
    """Franka Panda capability robot."""

    # ── helpers ────────────────────────────────────────────────────────────

    def _ee_pos(self) -> np.ndarray:
        xyz, _ = self.get_ee_pose()
        return xyz.copy()

    def _now(self) -> float:
        return float(self.data.time)

    def _gripper_aperture_fraction(self) -> float:
        gp = self.get_gripper_joint_positions()
        avg = (gp.get("finger_joint1", 0.0) + gp.get("finger_joint2", 0.0)) * 0.5
        return float(np.clip(avg / _FINGER_MAX, 0.0, 1.0))

    def _set_gripper_fraction(self, frac: float) -> None:
        ctrl = float(np.clip(frac, 0.0, 1.0)) * _GRIP_OPEN
        self.set_gripper_control(ctrl)

    def _regulate_to(
        self,
        target: np.ndarray,
        deadline: float,
        hold_s: float = 0.5,
        tol: float = 0.015,
    ) -> bool:
        """Closed-loop IK regulation toward target. Returns True on success."""
        hold_start: float | None = None
        while self._now() < deadline:
            pos = self._ee_pos()
            err = float(np.linalg.norm(pos - target))
            if err <= tol:
                if hold_start is None:
                    hold_start = self._now()
                elif self._now() - hold_start >= hold_s:
                    return True
            else:
                hold_start = None
            q_sol = self.ik(target)
            if q_sol is not None:
                self.set_arm_actuators(q_sol)
            self.step(1)
        return False

    # ── A1: move_end_effector_to_position ──────────────────────────────────

    def move_end_effector_to_position(self, request: dict) -> dict:
        target  = np.array(request["target_position_m"], dtype=float)
        max_dur = float(request["max_duration_s"])
        if not np.all(np.isfinite(target)):
            raise CapabilityError("A1: non-finite target_position_m")
        if not (0.25 <= max_dur <= 8.0):
            raise CapabilityError(f"A1: max_duration_s={max_dur} out of [0.25,8]")
        deadline = self._now() + max_dur
        ok = self._regulate_to(target, deadline, hold_s=0.5, tol=0.015)
        if not ok:
            self.set_arm_actuators(self.get_joint_positions())
            raise CapabilityError(
                f"A1: target not reached; err={np.linalg.norm(self._ee_pos()-target):.4f}m"
            )
        return {"status": "success",
                "final_error_m": float(np.linalg.norm(self._ee_pos() - target))}

    # ── A2: trace_cartesian_path ───────────────────────────────────────────

    def trace_cartesian_path(self, request: dict) -> dict:
        """Visit waypoints in order; interpolate along each segment."""
        waypoints = [np.array(w, dtype=float) for w in request["waypoints_m"]]
        seg_dur   = float(request["max_duration_per_segment_s"])
        if not (0.25 <= seg_dur <= 5.0):
            raise CapabilityError(f"A2: max_duration_per_segment_s={seg_dur} out of [0.25,5]")
        for i, w in enumerate(waypoints):
            if not np.all(np.isfinite(w)):
                raise CapabilityError(f"A2: waypoint {i} non-finite")

        dt        = float(self.model.opt.timestep)
        # max Cartesian speed for path tracking (m/s) – keeps cross-track low
        path_speed = 0.10   # m/s
        max_cross  = 0.0

        for idx, wp in enumerate(waypoints):
            deadline = self._now() + seg_dur
            is_last  = (idx == len(waypoints) - 1)
            prev_wp  = waypoints[idx - 1] if idx > 0 else self._ee_pos()
            seg_vec  = wp - prev_wp
            seg_len  = float(np.linalg.norm(seg_vec))

            # ── interpolated approach ──────────────────────────────────
            # advance a virtual cursor along the segment at path_speed
            cursor_t = 0.0   # fraction [0,1] along segment
            reached  = False
            hold_start: float | None = None

            while self._now() < deadline:
                pos = self._ee_pos()
                err = float(np.linalg.norm(pos - wp))

                # cross-track measurement
                if seg_len > 1e-6:
                    t_proj = float(np.clip(
                        np.dot(pos - prev_wp, seg_vec) / (seg_len ** 2), 0.0, 1.0))
                    closest = prev_wp + t_proj * seg_vec
                    ct = float(np.linalg.norm(pos - closest))
                    max_cross = max(max_cross, ct)

                # advance cursor
                if seg_len > 1e-6:
                    cursor_t = min(1.0, cursor_t + (path_speed * dt) / seg_len)
                else:
                    cursor_t = 1.0
                sub_target = prev_wp + cursor_t * seg_vec

                # terminal hold check
                if err <= 0.015:
                    if is_last:
                        if hold_start is None:
                            hold_start = self._now()
                        elif self._now() - hold_start >= 0.5:
                            reached = True
                            break
                    else:
                        reached = True
                        break
                else:
                    hold_start = None

                q_sol = self.ik(sub_target)
                if q_sol is not None:
                    self.set_arm_actuators(q_sol)
                self.step(1)

            if not reached:
                self.set_arm_actuators(self.get_joint_positions())
                raise CapabilityError(
                    f"A2: waypoint {idx} not reached; "
                    f"err={np.linalg.norm(self._ee_pos()-wp):.4f}m"
                )

        return {"status": "success",
                "max_cross_track_m": max_cross,
                "final_error_m": float(np.linalg.norm(self._ee_pos() - waypoints[-1]))}

    # ── A3: set_gripper_opening ────────────────────────────────────────────

    def set_gripper_opening(self, request: dict) -> dict:
        frac    = float(request["opening_fraction"])
        max_dur = float(request["max_duration_s"])
        if not (0.0 <= frac <= 1.0):
            raise CapabilityError(f"A3: opening_fraction={frac} out of [0,1]")
        if not (0.25 <= max_dur <= 8.0):
            raise CapabilityError(f"A3: max_duration_s={max_dur} out of [0.25,8]")

        start_frac = self._gripper_aperture_fraction()
        deadline   = self._now() + max_dur
        min_seen   = start_frac
        max_seen   = start_frac
        hold_start: float | None = None

        while self._now() < deadline:
            self._set_gripper_fraction(frac)
            self.step(1)
            cur      = self._gripper_aperture_fraction()
            min_seen = min(min_seen, cur)
            max_seen = max(max_seen, cur)
            err      = abs(cur - frac)
            if err <= 0.1:
                if hold_start is None:
                    hold_start = self._now()
                elif self._now() - hold_start >= 0.25:
                    excursion = max_seen - min_seen
                    if excursion >= 0.5:
                        return {"status": "success",
                                "final_aperture_fraction": cur,
                                "excursion": excursion}
            else:
                hold_start = None

        self._set_gripper_fraction(frac)
        cur = self._gripper_aperture_fraction()
        raise CapabilityError(
            f"A3: aperture not settled; err={abs(cur-frac):.3f}, "
            f"excursion={max_seen-min_seen:.3f}"
        )

    # ── A4: approach_until_contact ─────────────────────────────────────────

    def approach_until_contact(self, request: dict) -> dict:
        precontact = np.array(request["precontact_position_m"], dtype=float)
        direction  = np.array(request["approach_direction_unit"], dtype=float)
        max_travel = float(request["max_travel_m"])
        max_speed  = float(request["max_approach_speed_m_s"])
        max_dur    = float(request["max_duration_s"])

        d_norm = float(np.linalg.norm(direction))
        if d_norm < 1e-6:
            raise CapabilityError("A4: approach_direction_unit near-zero")
        direction = direction / d_norm

        if not (0.0 < max_travel <= 0.08):
            raise CapabilityError(f"A4: max_travel_m={max_travel} out of (0,0.08]")
        if not (0.0 < max_speed <= 0.05):
            raise CapabilityError(f"A4: max_approach_speed_m_s={max_speed} out of (0,0.05]")
        if not (0.25 <= max_dur <= 8.0):
            raise CapabilityError(f"A4: max_duration_s={max_dur} out of [0.25,8]")

        deadline = self._now() + max_dur

        # Phase 1: reach precontact
        ok = self._regulate_to(precontact, deadline, hold_s=0.1, tol=0.015)
        if not ok:
            self.set_arm_actuators(self.get_joint_positions())
            raise CapabilityError("A4: precontact not reached in time")

        # robot body ids: link0(1)..right_finger(11)
        _ROBOT_BODY_IDS = set(range(1, 12))
        _FLOOR_GEOM = 0   # fixed_floor geom id

        def _robot_contacts():
            """Return list of (g1,g2) pairs involving robot bodies (excl. floor)."""
            pairs = []
            for i in range(self.data.ncon):
                c = self.data.contact[i]
                g1, g2 = int(c.geom1), int(c.geom2)
                if g1 == _FLOOR_GEOM or g2 == _FLOOR_GEOM:
                    continue
                b1 = int(self.model.geom_bodyid[g1])
                b2 = int(self.model.geom_bodyid[g2])
                if b1 in _ROBOT_BODY_IDS or b2 in _ROBOT_BODY_IDS:
                    pairs.append((g1, g2))
            return pairs

        # Phase 2: advance along ray
        dt        = float(self.model.opt.timestep)
        step_dist = max_speed * dt
        travel    = 0.0

        while self._now() < deadline and travel < max_travel:
            if _robot_contacts():
                break
            travel += step_dist
            tgt = precontact + direction * min(travel, max_travel)
            q_sol = self.ik(tgt)
            if q_sol is not None:
                self.set_arm_actuators(q_sol)
            self.step(1)

        if not _robot_contacts():
            self.set_arm_actuators(self.get_joint_positions())
            raise CapabilityError(f"A4: no contact after {travel:.4f}m / {max_dur}s")

        # Phase 3: hold at contact for 0.1 s
        contact_pos   = self._ee_pos()
        hold_deadline = self._now() + 0.1
        while self._now() < hold_deadline:
            q_sol = self.ik(contact_pos)
            if q_sol is not None:
                self.set_arm_actuators(q_sol)
            self.step(1)

        # post-contact metrics
        site_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "fixed_tcp")
        jacp = np.zeros((3, self.model.nv))
        jacr = np.zeros((3, self.model.nv))
        mujoco.mj_jacSite(self.model, self.data, jacp, jacr, site_id)
        ee_vel     = jacp @ self.data.qvel
        post_speed = float(np.linalg.norm(ee_vel))

        final_pos   = self._ee_pos()
        penetration = max(0.0, float(np.dot(final_pos - contact_pos, direction)))
        unrelated   = len(_robot_contacts())   # contacts still active

        return {
            "status": "success",
            "travel_m": travel,
            "post_contact_speed_m_s": post_speed,
            "penetration_m": penetration,
            "unrelated_contacts": unrelated,
        }

    # ── A5: move_cartesian_offset_and_return ───────────────────────────────

    def move_cartesian_offset_and_return(self, request: dict) -> dict:
        offset  = np.array(request["offset_robot_base_m"], dtype=float)
        leg_dur = float(request["max_duration_per_leg_s"])
        if not np.all(np.isfinite(offset)):
            raise CapabilityError("A5: non-finite offset_robot_base_m")
        if not (0.25 <= leg_dur <= 5.0):
            raise CapabilityError(f"A5: max_duration_per_leg_s={leg_dur} out of [0.25,5]")

        start_pos       = self._ee_pos()
        outbound_target = start_pos + offset   # robot_base == world for fixed base

        # Outbound leg
        deadline_out = self._now() + leg_dur
        ok_out = self._regulate_to(outbound_target, deadline_out, hold_s=0.25, tol=0.015)
        if not ok_out:
            self.set_arm_actuators(self.get_joint_positions())
            raise CapabilityError(
                f"A5: outbound failed; err={np.linalg.norm(self._ee_pos()-outbound_target):.4f}m"
            )

        req_disp  = float(np.linalg.norm(offset))
        disp_frac = 1.0
        if req_disp > 1e-6:
            actual_disp = float(np.linalg.norm(self._ee_pos() - start_pos))
            disp_frac   = actual_disp / req_disp
            if disp_frac < 0.8:
                self.set_arm_actuators(self.get_joint_positions())
                raise CapabilityError(f"A5: displacement fraction {disp_frac:.3f} < 0.8")

        # Return leg
        deadline_ret = self._now() + leg_dur
        ok_ret = self._regulate_to(start_pos, deadline_ret, hold_s=0.5, tol=0.015)
        if not ok_ret:
            self.set_arm_actuators(self.get_joint_positions())
            raise CapabilityError(
                f"A5: return failed; err={np.linalg.norm(self._ee_pos()-start_pos):.4f}m"
            )

        return {"status": "success",
                "return_error_m": float(np.linalg.norm(self._ee_pos() - start_pos)),
                "displacement_fraction": disp_frac}


# ── module-level build() ───────────────────────────────────────────────────

def build() -> Robot:
    """Return a Robot instance bound to mjcf.xml."""
    return Robot.from_mjcf("mjcf.xml", spec=_SPEC)
