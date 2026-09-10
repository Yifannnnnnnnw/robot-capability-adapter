# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for kuka_iiwa14.

Robot subclass of ArmSerialDLSSkeleton that implements the public
capability contract (A1, A2, A4, A5). All motion is realized by writing
native position-actuator commands and advancing the same MuJoCo model/data;
kinematic (IK/FK) calculations use the skeleton's scratch data only. Time and
holds are measured from data.time (sim seconds).
"""
from __future__ import annotations

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import (
    ArmSerialDLSSkeleton,
    IKUnreachableError,
)
from auto_adapter.skeletons.base import ArmSpec

_ARM_JOINTS = [f"joint{i}" for i in range(1, 8)]
_ARM_ACTS = [f"actuator{i}" for i in range(1, 8)]
_LIMITS = {
    "joint1": (-2.96706, 2.96706),
    "joint2": (-2.0944, 2.0944),
    "joint3": (-2.96706, 2.96706),
    "joint4": (-2.0944, 2.0944),
    "joint5": (-2.96706, 2.96706),
    "joint6": (-2.0944, 2.0944),
    "joint7": (-3.05433, 3.05433),
}


class CapabilityError(RuntimeError):
    """Bounded capability failure raised when a request cannot complete."""


def _err(code, **kw):
    return {"status": "error", "error": code, **kw}


def _ok(**kw):
    return {"status": "ok", **kw}


class Robot(ArmSerialDLSSkeleton):
    """KUKA iiwa14 capability robot: 7 position-controlled hinge joints."""

    _POS_TOL = 0.010            # inner regulation target, below 0.015 criterion
    _CROSS_TOL = 0.02
    _EE_CONTACT_GEOM = "link7_contact_geom"
    _TARGET_GEOM = "fixed_contact_geom"

    # ---- request / precondition helpers -------------------------------
    def _check_finite_state(self):
        q = self.get_joint_positions()
        v = self.get_joint_velocities()
        if not (np.all(np.isfinite(q)) and np.all(np.isfinite(v))):
            raise CapabilityError("nonfinite_state")

    @staticmethod
    def _vec3(x, lo=-1.0, hi=1.0):
        a = np.asarray(x, dtype=np.float64).reshape(-1)
        if a.shape != (3,) or not np.all(np.isfinite(a)):
            raise CapabilityError("bad_vector")
        if np.any(a < lo - 1e-9) or np.any(a > hi + 1e-9):
            raise CapabilityError("out_of_bounds")
        return a

    @staticmethod
    def _scalar(x, lo, hi, name):
        v = float(x)
        if not np.isfinite(v) or v < lo - 1e-12 or v > hi + 1e-12:
            raise CapabilityError(f"bad_{name}")
        return v

    def _ee(self):
        self._mj.mj_forward(self.model, self.data)
        return self._ee_pos_now()

    def _ee_speed(self):
        jacp = np.zeros((3, self.model.nv), dtype=np.float64)
        self._ee_jac_pos(jacp, self.data)
        v = jacp @ self.data.qvel
        return float(np.linalg.norm(v))

    def _solve_ik(self, target):
        """Reachability check via scratch IK; raises CapabilityError."""
        try:
            self.ik(np.asarray(target, dtype=np.float64),
                    raise_on_unreachable=True)
        except IKUnreachableError as e:
            raise CapabilityError("unreachable") from e

    def _command_q(self, q):
        q = np.clip(np.asarray(q, dtype=np.float64), self._q_lo, self._q_hi)
        self.set_arm_actuators(q)

    def _ik_command_to(self, aim, bias):
        """Solve DLS IK for `aim + bias` and command it (best effort)."""
        try:
            q = self.ik(aim + bias, q_init=self.get_joint_positions(),
                        raise_on_unreachable=False)
            self._command_q(q)
        except IKUnreachableError:
            pass

    # ---- closed-loop point regulation ---------------------------------
    def _regulate(self, target, deadline_t, tol, hold_s):
        """Closed-loop EE regulation to a single `target` point.

        Re-solves IK every 0.02 s on a bias-corrected target so the servo
        removes steady-state Cartesian offset. Advances the live model/data.
        Returns (reached, err)."""
        target = np.asarray(target, dtype=np.float64)
        dt = float(self.model.opt.timestep)
        resolve_every = max(1, int(0.02 / dt))
        bias = np.zeros(3)
        hold_start = None
        best = float("inf")
        i = 0
        while self.data.time < deadline_t:
            if i % resolve_every == 0:
                cur = self._ee()
                bias += 0.5 * (target - cur)
                bn = float(np.linalg.norm(bias))
                if bn > 0.2:
                    bias *= 0.2 / bn
                self._ik_command_to(target, bias)
            self.step(1)
            i += 1
            err = float(np.linalg.norm(self._ee() - target))
            best = min(best, err)
            if err <= tol:
                if hold_start is None:
                    hold_start = self.data.time
                if hold_s <= 0.0 or (self.data.time - hold_start) >= hold_s:
                    return True, err
            else:
                hold_start = None
        return (hold_s <= 0.0 and best <= tol), best

    def _follow_line(self, seg0, target, deadline_t, tol, hold_s):
        """Track the straight Cartesian line seg0->target with low cross-track.

        The commanded IK aim advances along the line just ahead of the EE's
        current projection (bounded lookahead), so the servo pulls the EE
        along the line rather than cutting the corner. The aim rate is also
        capped so the segment completes within its time window. Returns
        (reached, err, max_dev)."""
        seg0 = np.asarray(seg0, dtype=np.float64)
        target = np.asarray(target, dtype=np.float64)
        d = target - seg0
        dn = float(np.linalg.norm(d))
        u = d / dn if dn > 1e-9 else np.zeros(3)
        dt = float(self.model.opt.timestep)
        resolve_every = max(1, int(0.01 / dt))
        window = max(deadline_t - self.data.time, 1e-3)
        adv_speed = dn / max(0.55 * window, 1e-3)
        lookahead = 0.008     # keep the aim tight to the EE projection
        max_lead = 0.02       # aim never runs more than this ahead of EE
        bias = np.zeros(3)
        hold_start = None
        best = float("inf")
        max_dev = 0.0
        i = 0
        s = 0.0  # arc length of the moving aim point along the line
        while self.data.time < deadline_t:
            cur = self._ee()
            proj_s = float(np.dot(cur - seg0, u)) if dn > 1e-9 else dn
            s = s + adv_speed * dt
            s = max(s, proj_s + lookahead)
            s = min(s, proj_s + max_lead, dn)
            aim = seg0 + u * s
            if i % resolve_every == 0:
                bias += 0.5 * (aim - cur)
                bn = float(np.linalg.norm(bias))
                if bn > 0.05:
                    bias *= 0.05 / bn
                self._ik_command_to(aim, bias)
            self.step(1)
            i += 1
            cur = self._ee()
            if dn > 1e-9:
                t = np.clip(np.dot(cur - seg0, u), 0.0, dn)
                foot = seg0 + u * t
                max_dev = max(max_dev, float(np.linalg.norm(cur - foot)))
            err = float(np.linalg.norm(cur - target))
            best = min(best, err)
            if err <= tol and s >= dn - 1e-9:
                if hold_start is None:
                    hold_start = self.data.time
                if hold_s <= 0.0 or (self.data.time - hold_start) >= hold_s:
                    return True, err, max_dev
            elif err > tol:
                hold_start = None
        return (hold_s <= 0.0 and best <= tol), best, max_dev

    def _hold_fixed(self, secs):
        """Freeze the current joint command and step for `secs` sim seconds."""
        qfix = self.get_joint_positions().copy()
        self._command_q(qfix)
        deadline = self.data.time + secs
        while self.data.time < deadline:
            self.step(1)

    # ---- A1: move_end_effector_to_position ----------------------------
    def move_end_effector_to_position(self, request):
        try:
            self._check_finite_state()
            target = self._vec3(request["target_position_m"])
            max_dur = self._scalar(request["max_duration_s"], 0.25, 8.0,
                                   "duration")
            self._solve_ik(target)
        except CapabilityError as e:
            return _err(str(e))
        except (KeyError, TypeError) as e:
            return _err(f"bad_request:{e}")

        deadline = self.data.time + max_dur
        reached, err = self._regulate(target, deadline, self._POS_TOL,
                                      hold_s=0.5)
        if not reached:
            self._command_q(self.get_joint_positions())
            return _err("not_reached_in_time", final_error_m=err)
        return _ok(final_error_m=err, ee_position_m=self._ee().tolist())

    # ---- A2: trace_cartesian_path -------------------------------------
    def trace_cartesian_path(self, request):
        try:
            self._check_finite_state()
            raw = request["waypoints_m"]
            if not (2 <= len(raw) <= 8):
                raise CapabilityError("bad_waypoint_count")
            wps = [self._vec3(w) for w in raw]
            seg_dur = self._scalar(request["max_duration_per_segment_s"],
                                   0.25, 5.0, "segment_duration")
            for w in wps:
                self._solve_ik(w)
        except CapabilityError as e:
            return _err(str(e))
        except (KeyError, TypeError) as e:
            return _err(f"bad_request:{e}")

        max_cross = 0.0
        prev = self._ee().copy()
        for idx, w in enumerate(wps):
            deadline = self.data.time + seg_dur
            last = (idx == len(wps) - 1)
            tol = self._POS_TOL if last else 0.012
            hold = 0.5 if last else 0.0
            reached, err, dev = self._follow_line(prev, w, deadline, tol,
                                                  hold_s=hold)
            max_cross = max(max_cross, dev)
            if not reached:
                self._command_q(self.get_joint_positions())
                return _err("segment_not_reached", waypoint_index=idx,
                            final_error_m=err, max_cross_track_m=max_cross)
            prev = w.copy()
        terr = float(np.linalg.norm(self._ee() - wps[-1]))
        return _ok(max_cross_track_m=max_cross, terminal_error_m=terr)

    # ---- contact observation helpers ----------------------------------
    def _geom_id(self, name):
        gid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_GEOM, name)
        if gid < 0:
            raise CapabilityError("missing_contact_geom")
        return gid

    def _contact_report(self, ee_gid, tgt_gid):
        """Scan live contacts. Returns (target_contact, penetration_m,
        unrelated_count). Unrelated counts contacts touching the EE contact
        geom or the target geom outside the intended pair."""
        target_contact = False
        pen = 0.0
        unrelated = 0
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            pair = {int(c.geom1), int(c.geom2)}
            if pair == {ee_gid, tgt_gid}:
                target_contact = True
                if c.dist < 0:
                    pen = max(pen, -float(c.dist))
            elif ee_gid in pair or tgt_gid in pair:
                unrelated += 1
        return target_contact, pen, unrelated
