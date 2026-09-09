# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for kinova_gen3_robotiq_2f85.

Robot subclass of ArmSerialDLSSkeleton. Implements the capability-v2
public contract (A1..A5) using the skeleton's low-level DLS IK,
actuator-space interpolation, and native gripper control. All motion is
produced by writing native actuator commands and advancing the same
MuJoCo model/data; deadlines/holds are measured with data.time.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton, IKUnreachableError
from auto_adapter.skeletons.base import ArmSpec

MJCF_PATH = "mjcf.xml"

ARM_JOINTS = ["joint_1", "joint_2", "joint_3", "joint_4", "joint_5", "joint_6", "joint_7"]
ARM_ACTS = ["joint_1", "joint_2", "joint_3", "joint_4", "joint_5", "joint_6", "joint_7"]
# Continuous (unlimited) joints get generous +/- pi bounds per study.json.
JOINT_LIMITS = {
    "joint_1": (-3.141592653589793, 3.141592653589793),
    "joint_2": (-2.24, 2.24),
    "joint_3": (-3.141592653589793, 3.141592653589793),
    "joint_4": (-2.57, 2.57),
    "joint_5": (-3.141592653589793, 3.141592653589793),
    "joint_6": (-2.09, 2.09),
    "joint_7": (-3.141592653589793, 3.141592653589793),
}
# Arm-forward home pose (ee ~ [0.68, -0.02, 0.80], mid-workspace).
HOME_QPOS = [0.0, 0.4, 0.0, 1.4, 0.0, -1.0, -1.57]


class CapabilityError(RuntimeError):
    """Bounded capability failure returned to the caller."""


def build() -> "Robot":
    spec = ArmSpec(
        ee_site_name="pinch_site",
        ee_body_name="base",
        arm_joint_names=ARM_JOINTS,
        arm_actuator_names=ARM_ACTS,
        joint_limits=JOINT_LIMITS,
        home_qpos=HOME_QPOS,
        ik_damping=5e-3,
        ik_max_iter=200,
        ik_tolerance=2e-3,
        ik_step_clamp=0.2,
        ik_raise_on_unreachable=False,
        gripper_actuator_names=["fingers_actuator"],
        gripper_joint_names=["right_driver_joint", "left_driver_joint"],
        grasp_backend="noop",
        sim_dt=0.002,
    )
    return Robot.from_mjcf(MJCF_PATH, spec=spec)


class Robot(ArmSerialDLSSkeleton):
    """kinova_gen3_robotiq_2f85 capability driver."""

    # Gripper native mapping: ctrl=0 -> fully open (driver ~ 0),
    # ctrl=255 -> fully closed (driver ~ 0.79). Probed on the real model.
    _GRIP_CTRL_MIN = 0.0
    _GRIP_CTRL_MAX = 255.0
    _DRIVER_OPEN = 0.0      # driver joint value when fully open
    _DRIVER_CLOSED = 0.7867  # driver joint value when fully closed

    # ---- time / feedback helpers -------------------------------------
    def _now(self) -> float:
        return float(self.data.time)

    def _dt(self) -> float:
        return float(self.model.opt.timestep)

    def _ee(self) -> np.ndarray:
        self._mj.mj_forward(self.model, self.data)
        return self._ee_pos_now()

    def _ee_speed(self) -> float:
        jacp = np.zeros((3, self.model.nv), dtype=np.float64)
        self._ee_jac_pos(jacp, self.data)
        v = jacp @ self.data.qvel
        return float(np.linalg.norm(v))

    def _finite_state(self) -> bool:
        q = self.get_joint_positions()
        return bool(np.all(np.isfinite(q)) and np.all(np.isfinite(self.get_joint_velocities())))

    def _hold_arm(self) -> None:
        """Freeze arm actuator targets at their current commanded value."""
        for aid in self._arm_actuator_ids:
            self.data.ctrl[aid] = float(self.data.ctrl[aid])

    # ---- IK that never raises; returns (q, reachable) ----------------
    def _solve_ik(self, target: np.ndarray, q_init: Optional[np.ndarray] = None):
        q = self.ik(np.asarray(target, float), q_init=q_init, raise_on_unreachable=False)
        res = self.fk(q)
        err = float(np.linalg.norm(res["pos"] - np.asarray(target, float)))
        return q, err

    # ---- closed-loop regulation to a world position ------------------
    def _regulate_to(self, target: np.ndarray, deadline: float,
                     tol: float, hold_s: float,
                     keep_grip: Optional[float] = None) -> bool:
        """Drive EE to `target`, then require err<=tol continuously hold_s.

        Returns True if the terminal hold is satisfied before `deadline`.
        Uses incremental IK re-solves for closed-loop correction.
        """
        target = np.asarray(target, float)
        dt = self._dt()
        hold_start = None
        while self._now() < deadline:
            if not self._finite_state():
                return False
            cur = self._ee()
            err = float(np.linalg.norm(target - cur))
            if err <= tol:
                if hold_start is None:
                    hold_start = self._now()
                elif (self._now() - hold_start) >= hold_s:
                    return True
            else:
                hold_start = None
            # closed-loop: re-solve IK from current pose toward target
            q_goal, _ = self._solve_ik(target, q_init=self.get_joint_positions())
            self.set_arm_actuators(q_goal)
            if keep_grip is not None:
                self.set_gripper_control(keep_grip)
            self.step(1)
        # final chance to satisfy hold at the boundary
        return False


    # ================= A1 : move_end_effector_to_position =============
    def move_end_effector_to_position(self, request):
        tp = request.get("target_position_m")
        md = request.get("max_duration_s")
        if tp is None or md is None:
            raise CapabilityError("A1 requires target_position_m and max_duration_s")
        target = np.asarray(tp, float).reshape(3)
        if target.shape != (3,) or not np.all(np.isfinite(target)):
            raise CapabilityError("A1 invalid target_position_m")
        if not (0.25 <= float(md) <= 8.0):
            raise CapabilityError("A1 max_duration_s out of bounds")
        if not self._finite_state():
            raise CapabilityError("A1 robot state not finite")

        # Reachability pre-check on scratch IK.
        q_goal, ik_err = self._solve_ik(target)
        keep = float(self.data.ctrl[self._gripper_actuator_ids[0]])
        deadline = self._now() + float(md)
        ok = self._regulate_to(target, deadline, tol=0.015, hold_s=0.5, keep_grip=keep)
        if not ok:
            self._hold_arm()
            final = float(np.linalg.norm(self._ee() - target))
            raise CapabilityError(
                f"A1 failed: final_err={final:.4f} m (ik_residual={ik_err:.4f})")
        return {"capability_id": "A1", "success": True,
                "final_position_error_m": float(np.linalg.norm(self._ee() - target))}

    # ================= A2 : trace_cartesian_path ======================
    def trace_cartesian_path(self, request):
        wps = request.get("waypoints_m")
        seg_dur = request.get("max_duration_per_segment_s")
        if wps is None or seg_dur is None:
            raise CapabilityError("A2 requires waypoints_m and max_duration_per_segment_s")
        if not (2 <= len(wps) <= 8):
            raise CapabilityError("A2 waypoints_m count out of bounds")
        if not (0.25 <= float(seg_dur) <= 5.0):
            raise CapabilityError("A2 max_duration_per_segment_s out of bounds")
        pts = [np.asarray(w, float).reshape(3) for w in wps]
        for p in pts:
            if p.shape != (3,) or not np.all(np.isfinite(p)):
                raise CapabilityError("A2 invalid waypoint")
        if not self._finite_state():
            raise CapabilityError("A2 robot state not finite")

        keep = float(self.data.ctrl[self._gripper_actuator_ids[0]])
        # Visit each waypoint in order. Intermediate waypoints only need to be
        # reached (brief settle); terminal waypoint gets the 0.5 s hold.
        for i, wp in enumerate(pts):
            terminal = (i == len(pts) - 1)
            tol = 0.015 if terminal else 0.02
            hold = 0.5 if terminal else 0.02
            deadline = self._now() + float(seg_dur)
            ok = self._regulate_to(wp, deadline, tol=tol, hold_s=hold, keep_grip=keep)
            if not ok:
                self._hold_arm()
                err = float(np.linalg.norm(self._ee() - wp))
                raise CapabilityError(
                    f"A2 failed at waypoint {i}: err={err:.4f} m")
        return {"capability_id": "A2", "success": True, "waypoints": len(pts)}


    # ================= A3 : set_gripper_opening =======================
    def _driver_now(self) -> float:
        pos = self.get_gripper_joint_positions()
        return float(np.mean(list(pos.values())))

    def _aperture_fraction(self) -> float:
        """Normalised opening fraction: 1.0 fully open, 0.0 fully closed."""
        span = self._DRIVER_CLOSED - self._DRIVER_OPEN
        frac_closed = (self._driver_now() - self._DRIVER_OPEN) / max(span, 1e-6)
        return float(np.clip(1.0 - frac_closed, 0.0, 1.0))

    def _ctrl_for_fraction(self, opening_fraction: float) -> float:
        # opening=1 -> open -> ctrl min (0); opening=0 -> closed -> ctrl max (255)
        f = float(np.clip(opening_fraction, 0.0, 1.0))
        return self._GRIP_CTRL_MAX + f * (self._GRIP_CTRL_MIN - self._GRIP_CTRL_MAX)

    def set_gripper_opening(self, request):
        of = request.get("opening_fraction")
        md = request.get("max_duration_s")
        if of is None or md is None:
            raise CapabilityError("A3 requires opening_fraction and max_duration_s")
        if not np.isfinite(of) or not (0.0 <= float(of) <= 1.0):
            raise CapabilityError("A3 opening_fraction out of bounds")
        if not (0.25 <= float(md) <= 8.0):
            raise CapabilityError("A3 max_duration_s out of bounds")
        pos = self.get_gripper_joint_positions()
        if not all(np.isfinite(v) for v in pos.values()):
            raise CapabilityError("A3 gripper state not finite")

        # Do NOT change the arm target: hold current arm actuator commands.
        arm_hold = [float(self.data.ctrl[aid]) for aid in self._arm_actuator_ids]
        target_ctrl = self._ctrl_for_fraction(float(of))
        deadline = self._now() + float(md)
        hold_start = None
        while self._now() < deadline:
            for aid, v in zip(self._arm_actuator_ids, arm_hold):
                self.data.ctrl[aid] = v
            self.set_gripper_control(target_ctrl)
            self.step(1)
            err = abs(self._aperture_fraction() - float(of))
            if err <= 0.1:
                if hold_start is None:
                    hold_start = self._now()
                elif (self._now() - hold_start) >= 0.25:
                    return {"capability_id": "A3", "success": True,
                            "aperture_fraction": self._aperture_fraction()}
            else:
                hold_start = None
        # keep command frozen, report bounded failure
        err = abs(self._aperture_fraction() - float(of))
        raise CapabilityError(f"A3 failed: aperture_err={err:.3f}")


    # ================= A5 : move_cartesian_offset_and_return ==========
    def _base_rot(self) -> np.ndarray:
        bid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_BODY, "base_link")
        if bid < 0:
            return np.eye(3)
        self._mj.mj_forward(self.model, self.data)
        return np.array(self.data.xmat[bid], float).reshape(3, 3)

    def move_cartesian_offset_and_return(self, request):
        off = request.get("offset_robot_base_m")
        leg_dur = request.get("max_duration_per_leg_s")
        if off is None or leg_dur is None:
            raise CapabilityError("A5 requires offset_robot_base_m and max_duration_per_leg_s")
        offset = np.asarray(off, float).reshape(3)
        if offset.shape != (3,) or not np.all(np.isfinite(offset)):
            raise CapabilityError("A5 invalid offset")
        if np.any(np.abs(offset) > 0.06):
            raise CapabilityError("A5 offset out of bounds")
        if not (0.25 <= float(leg_dur) <= 5.0):
            raise CapabilityError("A5 max_duration_per_leg_s out of bounds")
        if not self._finite_state():
            raise CapabilityError("A5 robot state not finite")

        keep = float(self.data.ctrl[self._gripper_actuator_ids[0]])
        # Capture the robot-base frame and start pose at call start.
        R = self._base_rot()
        start = self._ee().copy()
        world_off = R @ offset
        outbound = start + world_off

        # Outbound leg: reach and hold 0.25 s.
        deadline = self._now() + float(leg_dur)
        ok = self._regulate_to(outbound, deadline, tol=0.015, hold_s=0.25, keep_grip=keep)
        if not ok:
            self._hold_arm()
            err = float(np.linalg.norm(self._ee() - outbound))
            raise CapabilityError(f"A5 outbound failed: err={err:.4f} m")

        # Return leg only after outbound completed; reach start, hold 0.5 s.
        deadline = self._now() + float(leg_dur)
        ok = self._regulate_to(start, deadline, tol=0.015, hold_s=0.5, keep_grip=keep)
        if not ok:
            self._hold_arm()
            err = float(np.linalg.norm(self._ee() - start))
            raise CapabilityError(f"A5 return failed: err={err:.4f} m")
        return {"capability_id": "A5", "success": True,
                "return_error_m": float(np.linalg.norm(self._ee() - start))}


    # ================= A4 : approach_until_contact ====================
    def _robot_geom_ids(self):
        if getattr(self, "_rg_cache", None) is not None:
            return self._rg_cache
        ids = set()
        # arm + gripper bodies: everything except world (0) and mocap target.
        target_bid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_BODY,
                                         "fixed_contact_target")
        floor_gid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_GEOM,
                                        "fixed_floor")
        for gid in range(self.model.ngeom):
            bid = int(self.model.geom_bodyid[gid])
            if bid == 0 or bid == target_bid:
                continue
            if gid == floor_gid:
                continue
            ids.add(gid)
        self._rg_cache = ids
        return ids

    def _contact_scan(self):
        """Return (target_contact, unrelated_contact, max_penetration)."""
        tgt_gid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_GEOM,
                                      "fixed_contact_geom")
        rgeoms = self._robot_geom_ids()
        target_c = False
        unrelated_c = False
        max_pen = 0.0
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            g1, g2 = int(c.geom1), int(c.geom2)
            r1, r2 = g1 in rgeoms, g2 in rgeoms
            if not (r1 or r2):
                continue  # non-robot contact, ignore
            pen = max(0.0, -float(c.dist))
            other = g2 if r1 else g1
            if (r1 or r2) and (g1 == tgt_gid or g2 == tgt_gid):
                target_c = True
                max_pen = max(max_pen, pen)
            else:
                # robot geom touching something other than target
                unrelated_c = True
        return target_c, unrelated_c, max_pen

    def approach_until_contact(self, request):
        pre = request.get("precontact_position_m")
        dirv = request.get("approach_direction_unit")
        max_travel = request.get("max_travel_m")
        max_speed = request.get("max_approach_speed_m_s")
        md = request.get("max_duration_s")
        if any(v is None for v in (pre, dirv, max_travel, max_speed, md)):
            raise CapabilityError("A4 missing required fields")
        pre = np.asarray(pre, float).reshape(3)
        d_unit = np.asarray(dirv, float).reshape(3)
        if not (0.0 < float(max_travel) <= 0.08):
            raise CapabilityError("A4 max_travel_m out of bounds")
        if not (0.0 < float(max_speed) <= 0.05):
            raise CapabilityError("A4 max_approach_speed_m_s out of bounds")
        if not (0.25 <= float(md) <= 8.0):
            raise CapabilityError("A4 max_duration_s out of bounds")
        dn = float(np.linalg.norm(d_unit))
        if dn < 1e-9 or not np.all(np.isfinite(d_unit)):
            raise CapabilityError("A4 invalid approach_direction_unit")
        d_unit = d_unit / dn
        if not self._finite_state():
            raise CapabilityError("A4 robot state not finite")
        return self._run_approach(pre, d_unit, float(max_travel),
                                  float(max_speed), float(md))


    def _run_approach(self, pre, d_unit, max_travel, max_speed, md):
        keep = float(self.data.ctrl[self._gripper_actuator_ids[0]])
        deadline = self._now() + md
        # Phase 1: reach precontact pose (reserve time; hold briefly).
        half = self._now() + min(md * 0.5, md)
        reached = self._regulate_to(pre, half, tol=0.01, hold_s=0.05, keep_grip=keep)
        if not reached:
            self._hold_arm()
            raise CapabilityError("A4 could not reach precontact position")
        # abort if already in contact (unrelated) at precontact
        tc, uc, _ = self._contact_scan()
        if uc:
            self._hold_arm()
            raise CapabilityError("A4 unrelated contact at precontact")

        # Phase 2: advance along ray in small speed-limited increments.
        dt = self._dt()
        step_len = max_speed * dt  # bound per-step commanded advance
        start = self._ee().copy()
        traveled = 0.0
        contact_start = None
        while self._now() < deadline:
            # advance the commanded setpoint along the ray, bounded by travel
            remaining = max_travel - traveled
            adv = min(step_len, max(0.0, remaining))
            setpoint = start + d_unit * (traveled + adv)
            q_goal, _ = self._solve_ik(setpoint, q_init=self.get_joint_positions())
            self.set_arm_actuators(q_goal)
            self.set_gripper_control(keep)
            self.step(1)
            traveled = float(np.dot(self._ee() - start, d_unit))
            traveled = max(0.0, traveled)

            tc, uc, pen = self._contact_scan()
            if uc:
                self._hold_arm()
                raise CapabilityError("A4 unrelated contact during approach")
            if tc:
                # controlled contact: stop advancing, hold current setpoint
                if contact_start is None:
                    contact_start = self._now()
                self._hold_arm()
                speed = self._ee_speed()
                if (self._now() - contact_start) >= 0.1:
                    if speed <= 0.02 and pen <= 0.005:
                        return {"capability_id": "A4", "success": True,
                                "penetration_m": pen, "post_contact_speed": speed}
                # keep holding through the contact-hold window
                self.set_gripper_control(keep)
                self.step(1)
                continue
            if traveled >= max_travel - 1e-6:
                self._hold_arm()
                raise CapabilityError("A4 exhausted travel without contact")
        self._hold_arm()
        raise CapabilityError("A4 duration expired without controlled contact")


    # ---- override: gentler contact settle (replaces inline loop above) --
    def _run_approach(self, pre, d_unit, max_travel, max_speed, md):  # noqa: F811
        keep = float(self.data.ctrl[self._gripper_actuator_ids[0]])
        deadline = self._now() + md
        half = self._now() + min(md * 0.5, md)
        reached = self._regulate_to(pre, half, tol=0.01, hold_s=0.05, keep_grip=keep)
        if not reached:
            self._hold_arm()
            raise CapabilityError("A4 could not reach precontact position")
        tc0, uc0, _ = self._contact_scan()
        if uc0:
            self._hold_arm()
            raise CapabilityError("A4 unrelated contact at precontact")

        dt = self._dt()
        step_len = max_speed * dt
        start = self._ee().copy()
        traveled = 0.0
        while self._now() < deadline:
            traveled = max(0.0, float(np.dot(self._ee() - start, d_unit)))
            tc, uc, pen = self._contact_scan()
            if uc:
                self._hold_arm()
                raise CapabilityError("A4 unrelated contact during approach")
            if tc:
                return self._contact_settle(deadline, keep)
            if traveled >= max_travel - 1e-6:
                self._hold_arm()
                raise CapabilityError("A4 exhausted travel without contact")
            setpoint = start + d_unit * min(traveled + step_len, max_travel)
            q_goal, _ = self._solve_ik(setpoint, q_init=self.get_joint_positions())
            self.set_arm_actuators(q_goal)
            self.set_gripper_control(keep)
            self.step(1)
        self._hold_arm()
        raise CapabilityError("A4 duration expired without controlled contact")

    def _contact_settle(self, deadline, keep):
        """Freeze arm at current joint positions and require sustained,
        low-speed, low-penetration target contact for 0.1 s."""
        q_freeze = self.get_joint_positions()
        contact_start = None
        while self._now() < deadline:
            self.set_arm_actuators(q_freeze)
            self.set_gripper_control(keep)
            self.step(1)
            tc, uc, pen = self._contact_scan()
            if uc:
                self._hold_arm()
                raise CapabilityError("A4 unrelated contact during settle")
            speed = self._ee_speed()
            if tc and speed <= 0.02 and pen <= 0.005:
                if contact_start is None:
                    contact_start = self._now()
                elif (self._now() - contact_start) >= 0.1:
                    return {"capability_id": "A4", "success": True,
                            "penetration_m": pen, "post_contact_speed": speed}
            else:
                contact_start = None
        self._hold_arm()
        raise CapabilityError("A4 contact not sustained within duration")
