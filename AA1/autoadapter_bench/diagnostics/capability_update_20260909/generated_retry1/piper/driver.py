"""driver.py – PiPER capability driver (ArmSerialDLSSkeleton)."""
from __future__ import annotations

import math
import time
from typing import Any, Dict

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton
from auto_adapter.skeletons.base import ArmSpec

# ---------------------------------------------------------------------------
# Spec / bindings
# ---------------------------------------------------------------------------
_SPEC = ArmSpec(
    ee_site_name="ee_site",
    ee_body_name="link6",
    arm_joint_names=["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"],
    arm_actuator_names=["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"],
    joint_limits={
        "joint1": (-2.618,  2.618),
        "joint2": ( 0.0,    3.14 ),
        "joint3": (-2.697,  0.0  ),
        "joint4": (-1.832,  1.832),
        "joint5": (-1.22,   1.22 ),
        "joint6": (-3.14,   3.14 ),
        "joint7": ( 0.0,    0.035),
        "joint8": (-0.035,  0.0  ),
    },
    home_qpos=[0.0, 1.0, -1.0, 0.0, 0.5, 0.0],
    ik_damping=0.005,
    ik_max_iter=60,
    ik_tolerance=0.0005,
    ik_step_clamp=0.15,
    ik_raise_on_unreachable=False,
    gripper_actuator_names=["gripper"],
    gripper_joint_names=["joint7"],
    gripper_close_ctrl=0.0,
    gripper_open_ctrl=0.035,
    gripper_settle_steps=80,
    grasp_backend=None,
    weld_graspable_bodies=None,
    sim_dt=0.002,
)

# ---------------------------------------------------------------------------
# Helper constants
# ---------------------------------------------------------------------------
_HOLD_DT      = 0.002   # sim timestep
_POS_HOLD     = 0.5     # s – A1 terminal hold
_PATH_HOLD    = 0.5     # s – A2 terminal hold
_GRP_HOLD     = 0.25    # s – A3 aperture hold
_CONTACT_HOLD = 0.1     # s – A4 contact hold
_OUT_HOLD     = 0.25    # s – A5 outbound hold
_RET_HOLD     = 0.5     # s – A5 return hold

_POS_THRESH    = 0.015  # m  A1/A2 terminal
_XTRACK_THRESH = 0.02   # m  A2 cross-track
_GRP_THRESH    = 0.1    # ratio A3
_PENETRATION   = 0.005  # m   A4
_DISP_FRAC     = 0.8    # ratio A5

# All arm body names (for internal-contact filtering in A4)
_ARM_BODY_NAMES = {
    "base_link", "link1", "link2", "link3", "link4",
    "link5", "link6", "link7", "link8",
}
# EE bodies (gripper + wrist) – contact with external = "related"
_EE_BODY_NAMES = {"link6", "link7", "link8"}


class Robot(ArmSerialDLSSkeleton):
    """PiPER 6-DOF arm capability driver."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ee_pos(self) -> np.ndarray:
        xyz, _ = self.get_ee_pose()
        return np.asarray(xyz, dtype=float)

    def _gripper_fraction(self) -> float:
        gpos = self.get_gripper_joint_positions()
        val = gpos.get("joint7", 0.0)
        return float(np.clip(val / 0.035, 0.0, 1.0))

    def _ctrl_from_fraction(self, frac: float) -> float:
        return float(np.clip(frac * 0.035, 0.0, 0.035))

    def _regulate_to(self, target_xyz: np.ndarray, duration: float,
                     hold_secs: float, thresh: float) -> Dict[str, Any]:
        """Closed-loop IK regulation toward target_xyz for up to duration s."""
        deadline = time.monotonic() + duration
        hold_start: float | None = None

        while time.monotonic() < deadline:
            cur = self._ee_pos()
            err = float(np.linalg.norm(cur - target_xyz))

            if err <= thresh:
                if hold_start is None:
                    hold_start = time.monotonic()
                elif time.monotonic() - hold_start >= hold_secs:
                    return {"success": True, "final_error_m": err}
            else:
                hold_start = None

            try:
                q_sol = self.ik(target_xyz)
            except Exception:
                q_sol = None
            if q_sol is not None:
                self.set_arm_actuators(q_sol)
            self.step(4)

        cur = self._ee_pos()
        err = float(np.linalg.norm(cur - target_xyz))
        if err <= thresh:
            return {"success": True, "final_error_m": err}
        return {"success": False, "final_error_m": err,
                "error": f"timeout: err={err:.4f}m > {thresh}m"}

    # ------------------------------------------------------------------
    # A1 – move_end_effector_to_position
    # ------------------------------------------------------------------
    def move_end_effector_to_position(self, request: dict) -> dict:
        target = np.asarray(request["target_position_m"], dtype=float)
        duration = float(request["max_duration_s"])
        if not np.all(np.isfinite(self._ee_pos())):
            return {"success": False, "error": "robot state not finite"}
        return self._regulate_to(target, duration, _POS_HOLD, _POS_THRESH)

    # ------------------------------------------------------------------
    # A2 – trace_cartesian_path
    # ------------------------------------------------------------------
    def trace_cartesian_path(self, request: dict) -> dict:
        waypoints = [np.asarray(w, dtype=float) for w in request["waypoints_m"]]
        seg_dur = float(request["max_duration_per_segment_s"])
        if not np.all(np.isfinite(self._ee_pos())):
            return {"success": False, "error": "robot state not finite"}

        max_xtrack = 0.0
        for i, wp in enumerate(waypoints):
            is_last = (i == len(waypoints) - 1)
            hold = _PATH_HOLD if is_last else 0.0
            thresh = _POS_THRESH if is_last else _XTRACK_THRESH
            deadline = time.monotonic() + seg_dur
            hold_start: float | None = None
            seg_start = self._ee_pos()

            while time.monotonic() < deadline:
                cur = self._ee_pos()
                err = float(np.linalg.norm(cur - wp))
                seg_vec = wp - seg_start
                seg_len = float(np.linalg.norm(seg_vec))
                if seg_len > 1e-6:
                    t = float(np.clip(np.dot(cur - seg_start, seg_vec) / (seg_len * seg_len), 0.0, 1.0))
                    xtrack = float(np.linalg.norm(cur - (seg_start + t * seg_vec)))
                else:
                    xtrack = err
                max_xtrack = max(max_xtrack, xtrack)

                if err <= thresh:
                    if hold_start is None:
                        hold_start = time.monotonic()
                    elif time.monotonic() - hold_start >= hold:
                        break
                else:
                    hold_start = None

                try:
                    q_sol = self.ik(wp)
                except Exception:
                    q_sol = None
                if q_sol is not None:
                    self.set_arm_actuators(q_sol)
                self.step(4)

            if is_last:
                final_err = float(np.linalg.norm(self._ee_pos() - wp))
                if final_err > _POS_THRESH:
                    return {"success": False,
                            "error": f"terminal waypoint err={final_err:.4f}m",
                            "max_cross_track_m": max_xtrack}

        return {"success": True, "max_cross_track_m": max_xtrack,
                "final_error_m": float(np.linalg.norm(self._ee_pos() - waypoints[-1]))}

    # ------------------------------------------------------------------
    # A3 – set_gripper_opening
    # ------------------------------------------------------------------
    def set_gripper_opening(self, request: dict) -> dict:
        frac = float(request["opening_fraction"])
        duration = float(request["max_duration_s"])
        cur_frac = self._gripper_fraction()
        if not math.isfinite(cur_frac):
            return {"success": False, "error": "gripper state not finite"}

        excursion = abs(frac - cur_frac)
        target_ctrl = self._ctrl_from_fraction(frac)
        self.set_gripper_control(target_ctrl)

        deadline = time.monotonic() + duration
        hold_start: float | None = None

        while time.monotonic() < deadline:
            self.step(4)
            cur_frac = self._gripper_fraction()
            err = abs(cur_frac - frac)
            if err <= _GRP_THRESH:
                if hold_start is None:
                    hold_start = time.monotonic()
                elif time.monotonic() - hold_start >= _GRP_HOLD:
                    return {"success": True, "final_fraction": cur_frac,
                            "excursion": excursion}
            else:
                hold_start = None
                self.set_gripper_control(target_ctrl)

        cur_frac = self._gripper_fraction()
        err = abs(cur_frac - frac)
        if err <= _GRP_THRESH:
            return {"success": True, "final_fraction": cur_frac, "excursion": excursion}
        return {"success": False, "error": f"gripper err={err:.3f} > {_GRP_THRESH}",
                "final_fraction": cur_frac, "excursion": excursion}

    # ------------------------------------------------------------------
    # A4 – approach_until_contact
    # ------------------------------------------------------------------
    def _classify_contacts(self):
        """Return (ee_external_contact: bool, unrelated_count: int).

        Rules:
        - Both bodies in arm set → internal, ignored.
        - EE body + external body → related contact.
        - Non-EE arm body + external body → unrelated contact.
        """
        model = self.model
        data  = self.data
        # Build body-id sets once per call (cheap)
        arm_ids = {model.body(n).id for n in _ARM_BODY_NAMES
                   if model.body(n).id >= 0}
        ee_ids  = {model.body(n).id for n in _EE_BODY_NAMES
                   if model.body(n).id >= 0}
        ee_contact = False
        unrelated  = 0
        for i in range(data.ncon):
            c  = data.contact[i]
            b1 = int(model.geom_bodyid[c.geom1])
            b2 = int(model.geom_bodyid[c.geom2])
            # Both arm → internal, skip
            if b1 in arm_ids and b2 in arm_ids:
                continue
            # At least one external body
            if b1 in ee_ids or b2 in ee_ids:
                ee_contact = True
            else:
                unrelated += 1
        return ee_contact, unrelated

    def approach_until_contact(self, request: dict) -> dict:
        precontact = np.asarray(request["precontact_position_m"], dtype=float)
        direction  = np.asarray(request["approach_direction_unit"], dtype=float)
        max_travel = float(request["max_travel_m"])
        max_speed  = float(request["max_approach_speed_m_s"])
        duration   = float(request["max_duration_s"])

        dir_norm = float(np.linalg.norm(direction))
        if dir_norm < 1e-6:
            return {"success": False, "error": "zero approach direction"}
        direction = direction / dir_norm

        if not np.all(np.isfinite(self._ee_pos())):
            return {"success": False, "error": "robot state not finite"}

        # Phase 1: reach precontact
        pre_result = self._regulate_to(precontact, duration * 0.6, 0.1, _POS_THRESH)
        if not pre_result["success"]:
            return {"success": False,
                    "error": "could not reach precontact: " + pre_result.get("error", "")}

        # Phase 2: approach along ray
        step_secs = 4 * _HOLD_DT
        step_dist = max(max_speed * step_secs, 1e-4)
        traveled  = 0.0
        contact_start: float | None = None
        deadline  = time.monotonic() + duration * 0.4
        data      = self.data

        while time.monotonic() < deadline and traveled < max_travel:
            cur = self._ee_pos()
            next_target = cur + direction * step_dist
            traveled += step_dist

            try:
                q_sol = self.ik(next_target)
            except Exception:
                q_sol = None
            if q_sol is not None:
                self.set_arm_actuators(q_sol)
            self.step(4)

            ee_contact, unrelated_count = self._classify_contacts()
            if unrelated_count > 0:
                return {"success": False, "error": "unrelated contact detected",
                        "unrelated_contact_count": unrelated_count}

            if ee_contact:
                if contact_start is None:
                    contact_start = time.monotonic()
                # Check penetration
                pen = max((max(0.0, -float(data.contact[i].dist))
                           for i in range(data.ncon)), default=0.0)
                if pen > _PENETRATION:
                    return {"success": False,
                            "error": f"penetration {pen:.4f}m > {_PENETRATION}m"}
                if time.monotonic() - contact_start >= _CONTACT_HOLD:
                    return {"success": True, "traveled_m": traveled,
                            "penetration_m": pen}
            else:
                contact_start = None

        if traveled >= max_travel:
            return {"success": False,
                    "error": f"max travel {max_travel}m exhausted without contact"}
        return {"success": False, "error": "approach duration exhausted without contact"}

    # ------------------------------------------------------------------
    # A5 – move_cartesian_offset_and_return
    # ------------------------------------------------------------------
    def move_cartesian_offset_and_return(self, request: dict) -> dict:
        offset  = np.asarray(request["offset_robot_base_m"], dtype=float)
        leg_dur = float(request["max_duration_per_leg_s"])

        start_pos = self._ee_pos().copy()
        if not np.all(np.isfinite(start_pos)):
            return {"success": False, "error": "robot state not finite"}

        # Fixed base: robot-base frame == world frame
        outbound_target = start_pos + offset

        out_result = self._regulate_to(outbound_target, leg_dur, _OUT_HOLD, _POS_THRESH)
        if not out_result["success"]:
            return {"success": False,
                    "error": "outbound leg failed: " + out_result.get("error", ""),
                    "leg": "outbound"}

        req_disp = float(np.linalg.norm(offset))
        disp_frac = 1.0
        if req_disp > 1e-6:
            actual_disp = float(np.linalg.norm(self._ee_pos() - start_pos))
            disp_frac = actual_disp / req_disp
            if disp_frac < _DISP_FRAC:
                return {"success": False,
                        "error": f"displacement fraction {disp_frac:.2f} < {_DISP_FRAC}",
                        "leg": "outbound"}

        ret_result = self._regulate_to(start_pos, leg_dur, _RET_HOLD, _POS_THRESH)
        if not ret_result["success"]:
            return {"success": False,
                    "error": "return leg failed: " + ret_result.get("error", ""),
                    "leg": "return"}

        return {"success": True,
                "outbound_error_m": out_result["final_error_m"],
                "return_error_m":   ret_result["final_error_m"],
                "displacement_fraction": disp_frac}


# ---------------------------------------------------------------------------
# Module-level build()
# ---------------------------------------------------------------------------
def build() -> Robot:
    return Robot.from_mjcf("mjcf.xml", spec=_SPEC)
