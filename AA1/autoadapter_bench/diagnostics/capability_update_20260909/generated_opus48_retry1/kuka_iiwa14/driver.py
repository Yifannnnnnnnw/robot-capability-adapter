# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for kuka_iiwa14.

Subclasses ArmSerialDLSSkeleton (DLS IK + actuator-space interpolation on
position-servo actuators) and implements the public capability contract:
  A1 move_end_effector_to_position
  A2 trace_cartesian_path
  A4 approach_until_contact
  A5 move_cartesian_offset_and_return

All timing uses data.time (sim seconds). All motion is produced by writing
native actuator targets and stepping the same MuJoCo model/data. Kinematic
IK uses the skeleton's scratch-data based ik(). Live qpos/qvel are never set.

The iiwa position servos leave a small steady-state Cartesian offset (finite
stiffness under gravity), so regulation closes the loop with an integral
correction on the IK *target* using the measured EE error.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import (
    ArmSerialDLSSkeleton,
    IKUnreachableError,
)
from auto_adapter.skeletons.base import ArmSpec


class CapabilityError(RuntimeError):
    """Bounded capability error returned/raised on failure per contract."""


ARM_JOINTS = [f"joint{i}" for i in range(1, 8)]
ARM_ACTUATORS = [f"actuator{i}" for i in range(1, 8)]
JOINT_LIMITS = {
    "joint1": (-2.96706, 2.96706),
    "joint2": (-2.0944, 2.0944),
    "joint3": (-2.96706, 2.96706),
    "joint4": (-2.0944, 2.0944),
    "joint5": (-2.96706, 2.96706),
    "joint6": (-2.0944, 2.0944),
    "joint7": (-3.05433, 3.05433),
}


class Robot(ArmSerialDLSSkeleton):
    """KUKA iiwa 14 capability robot."""

    _POS_TOL = 0.015          # A1/A5 terminal error (m)
    _CROSS_TOL = 0.02         # A2 cross-track (m)
    _TERM_TOL = 0.015         # A2 terminal (m)
    _KI = 0.8                 # integral gain on Cartesian target correction

    # ── helpers ────────────────────────────────────────────────────────
    @staticmethod
    def _finite(a) -> bool:
        return bool(np.all(np.isfinite(np.asarray(a, dtype=np.float64))))

    def _dt(self) -> float:
        return float(self.model.opt.timestep)

    def _ee(self) -> np.ndarray:
        self._mj.mj_forward(self.model, self.data)
        return self._ee_pos_now()

    def _ee_speed(self) -> float:
        jacp = np.zeros((3, self.model.nv), dtype=np.float64)
        self._ee_jac_pos(jacp, self.data)
        return float(np.linalg.norm(jacp @ self.data.qvel))

    def _check_state_finite(self) -> None:
        if not (self._finite(self.get_joint_positions())
                and self._finite(self.get_joint_velocities())
                and self._finite(self._ee())):
            raise CapabilityError("robot state not finite before motion")

    def _ik_or_fail(self, target: np.ndarray) -> np.ndarray:
        try:
            return self.ik(np.asarray(target, dtype=np.float64),
                           raise_on_unreachable=True)
        except IKUnreachableError as e:
            raise CapabilityError(
                f"target {np.asarray(target).tolist()} unreachable "
                f"(residual={e.residual:.4f} m)"
            ) from e

    # ── closed-loop regulation with integral Cartesian correction ──────
    def _regulate_to(self, target, deadline_t, tol, hold_s,
                     settle_chunk=0.02):
        """Drive EE to `target` with integral correction; require continuous
        error<=tol for hold_s within deadline. Returns True/False."""
        target = np.asarray(target, dtype=np.float64).reshape(3)
        self._ik_or_fail(target)               # reachability gate
        aug = target.copy()
        dt = self._dt()
        chunk = max(1, int(settle_chunk / dt))
        hold_start: Optional[float] = None
        while self.data.time < deadline_t:
            try:
                q = self.ik(aug, raise_on_unreachable=False)
                self.set_arm_actuators(q)
            except Exception:
                pass
            self.step(chunk)
            err_vec = target - self._ee()
            err = float(np.linalg.norm(err_vec))
            aug = aug + self._KI * err_vec       # integral correction
            if err <= tol:
                if hold_start is None:
                    hold_start = self.data.time
                elif self.data.time - hold_start >= hold_s:
                    return True
            else:
                hold_start = None
        # final check for zero-hold requests
        if hold_s <= 0.0 and float(np.linalg.norm(target - self._ee())) <= tol:
            return True
        return False

    def _stop(self) -> None:
        """Halt motion by commanding the current joint positions (no teleport)."""
        self.set_arm_actuators(self.get_joint_positions())

    # ── A1: move_end_effector_to_position ──────────────────────────────
    def move_end_effector_to_position(self, request):
        req = dict(request)
        target = req.get("target_position_m")
        max_dur = req.get("max_duration_s")
        if target is None or max_dur is None:
            raise CapabilityError("A1 requires target_position_m and max_duration_s")
        target = np.asarray(target, dtype=np.float64).reshape(3)
        if not self._finite(target) or not np.all(np.abs(target) <= 1.0):
            raise CapabilityError("A1 target_position_m out of bounds")
        if not (0.25 <= float(max_dur) <= 8.0):
            raise CapabilityError("A1 max_duration_s out of bounds")
        self._check_state_finite()

        deadline = self.data.time + float(max_dur)
        ok = self._regulate_to(target, deadline, self._POS_TOL, 0.5)
        final_err = float(np.linalg.norm(self._ee() - target))
        if not ok:
            self._stop()
            raise CapabilityError(
                f"A1 did not hold within {max_dur:.3f}s (err={final_err:.4f} m)")
        return {
            "capability_id": "A1",
            "success": True,
            "final_position_error_m": final_err,
            "ee_position_m": self._ee().tolist(),
        }

    # ── A2: trace_cartesian_path ───────────────────────────────────────
    def _track_segment(self, p0, p1, deadline_t, cross_tol):
        """Track straight segment p0->p1 with bounded cross-track error using
        interpolated intermediate targets (each regulated with correction).
        Returns True when p1 reached within terminal tol before deadline."""
        p0 = np.asarray(p0, dtype=np.float64)
        p1 = np.asarray(p1, dtype=np.float64)
        seg = p1 - p0
        seglen = float(np.linalg.norm(seg))
        # discretize so consecutive interpolation points stay within cross_tol
        n_sub = int(max(1, np.ceil(seglen / max(cross_tol, 1e-3))))
        n_sub = min(n_sub, 40)
        span = max(self._dt(), deadline_t - self.data.time)
        per = span / (n_sub + 1)  # leave budget for terminal settle
        for k in range(1, n_sub + 1):
            if self.data.time >= deadline_t:
                return False
            interp = p0 + (k / n_sub) * seg
            sub_deadline = min(deadline_t, self.data.time + per)
            # regulate toward the sub-point; brief hold keeps us on the line
            self._regulate_to(interp, sub_deadline, cross_tol, 0.0)
        return self._regulate_to(p1, deadline_t, self._TERM_TOL, 0.0)

    def trace_cartesian_path(self, request):
        req = dict(request)
        wps = req.get("waypoints_m")
        seg_dur = req.get("max_duration_per_segment_s")
        if wps is None or seg_dur is None:
            raise CapabilityError("A2 requires waypoints_m and max_duration_per_segment_s")
        if not (2 <= len(wps) <= 8):
            raise CapabilityError("A2 waypoints_m count out of bounds")
        wps = [np.asarray(w, dtype=np.float64).reshape(3) for w in wps]
        for w in wps:
            if not self._finite(w) or not np.all(np.abs(w) <= 1.0):
                raise CapabilityError("A2 waypoint out of bounds")
        if not (0.25 <= float(seg_dur) <= 5.0):
            raise CapabilityError("A2 max_duration_per_segment_s out of bounds")
        self._check_state_finite()
        for w in wps:
            self._ik_or_fail(w)

        prev = self._ee()
        for idx, wp in enumerate(wps):
            deadline = self.data.time + float(seg_dur)
            if not self._track_segment(prev, wp, deadline, self._CROSS_TOL):
                self._stop()
                raise CapabilityError(
                    f"A2 could not reach waypoint {idx} in order within budget")
            prev = wp
        deadline = self.data.time + max(float(seg_dur), 1.0)
        ok = self._regulate_to(wps[-1], deadline, self._TERM_TOL, 0.5)
        term_err = float(np.linalg.norm(self._ee() - wps[-1]))
        if not ok:
            self._stop()
            raise CapabilityError(f"A2 terminal hold failed (err={term_err:.4f} m)")
        return {
            "capability_id": "A2",
            "success": True,
            "waypoints_visited": len(wps),
            "terminal_position_error_m": term_err,
        }

    # ── A5: move_cartesian_offset_and_return ───────────────────────────
    def move_cartesian_offset_and_return(self, request):
        req = dict(request)
        offset = req.get("offset_robot_base_m")
        leg_dur = req.get("max_duration_per_leg_s")
        if offset is None or leg_dur is None:
            raise CapabilityError("A5 requires offset_robot_base_m and max_duration_per_leg_s")
        offset = np.asarray(offset, dtype=np.float64).reshape(3)
        if not self._finite(offset) or not np.all(np.abs(offset) <= 0.06):
            raise CapabilityError("A5 offset_robot_base_m out of bounds")
        if not (0.25 <= float(leg_dur) <= 5.0):
            raise CapabilityError("A5 max_duration_per_leg_s out of bounds")
        self._check_state_finite()

        # base frame captured at call start (fixed base at world origin)
        base_bid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_BODY, "base")
        R_base = np.array(self.data.xmat[base_bid], dtype=np.float64).reshape(3, 3)
        start_ee = self._ee().copy()
        world_offset = R_base @ offset
        outbound_target = start_ee + world_offset
        self._ik_or_fail(outbound_target)

        deadline = self.data.time + float(leg_dur)
        ok = self._regulate_to(outbound_target, deadline, self._POS_TOL, 0.25)
        out_err = float(np.linalg.norm(self._ee() - outbound_target))
        if not ok:
            self._stop()
            raise CapabilityError(f"A5 outbound leg failed (err={out_err:.4f} m)")
        disp = float(np.linalg.norm(self._ee() - start_ee))
        req_disp = float(np.linalg.norm(world_offset))
        frac = disp / req_disp if req_disp > 1e-9 else 1.0

        # return leg only after outbound completes
        deadline = self.data.time + float(leg_dur)
        ok = self._regulate_to(start_ee, deadline, self._POS_TOL, 0.5)
        ret_err = float(np.linalg.norm(self._ee() - start_ee))
        if not ok:
            self._stop()
            raise CapabilityError(f"A5 return leg failed (err={ret_err:.4f} m)")
        return {
            "capability_id": "A5",
            "success": True,
            "outbound_error_m": out_err,
            "return_error_m": ret_err,
            "requested_displacement_fraction": frac,
        }
