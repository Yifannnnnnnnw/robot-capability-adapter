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

    # ---- closed-loop regulation with Cartesian integral feedback ------
    def _regulate(self, target, deadline_t, tol, hold_s, cross_track=False,
                  ref_from=None):
        """Closed-loop EE regulation to `target`.

        Re-solves IK every 0.02 s on a bias-corrected target: bias integrates
        the measured Cartesian tracking error so the position servo converges
        to the true target (removes steady-state offset). Advances the live
        model/data via self.step. Returns (reached, err, max_dev).

        `reached` requires err <= tol continuously for `hold_s` (or a single
        touch when hold_s <= 0). `max_dev` tracks max distance to the segment
        line (start->target) when cross_track is True."""
        target = np.asarray(target, dtype=np.float64)
        dt = float(self.model.opt.timestep)
        resolve_every = max(1, int(0.02 / dt))
        bias = np.zeros(3)
        hold_start = None
        best = float("inf")
        max_dev = 0.0
        seg0 = None
        if cross_track:
            seg0 = (np.asarray(ref_from, dtype=np.float64)
                    if ref_from is not None else self._ee().copy())
        i = 0
        while self.data.time < deadline_t:
            if i % resolve_every == 0:
                cur = self._ee()
                bias += 0.5 * (target - cur)
                # keep bias bounded so IK stays well-posed
                bnorm = float(np.linalg.norm(bias))
                if bnorm > 0.2:
                    bias *= 0.2 / bnorm
                try:
                    q = self.ik(target + bias,
                                q_init=self.get_joint_positions(),
                                raise_on_unreachable=False)
                    self._command_q(q)
                except IKUnreachableError:
                    pass
            self.step(1)
            i += 1
            cur = self._ee()
            err = float(np.linalg.norm(cur - target))
            best = min(best, err)
            if cross_track and seg0 is not None:
                d = target - seg0
                dn = float(np.linalg.norm(d))
                if dn > 1e-9:
                    proj = seg0 + np.clip(np.dot(cur - seg0, d) / dn / dn,
                                          0.0, 1.0) * d
                    max_dev = max(max_dev, float(np.linalg.norm(cur - proj)))
            if err <= tol:
                if hold_start is None:
                    hold_start = self.data.time
                if hold_s <= 0.0 or (self.data.time - hold_start) >= hold_s:
                    return True, err, max_dev
            else:
                hold_start = None
        return (hold_s <= 0.0 and best <= tol), best, max_dev

    def _hold_current(self, secs):
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
        reached, err, _ = self._regulate(target, deadline, self._POS_TOL,
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
            tol = self._POS_TOL if last else self._CROSS_TOL * 0.6
            hold = 0.5 if last else 0.0
            reached, err, dev = self._regulate(
                w, deadline, tol, hold_s=hold, cross_track=True, ref_from=prev)
            max_cross = max(max_cross, dev, (0.0 if reached else err))
            if not reached:
                self._command_q(self.get_joint_positions())
                return _err("segment_not_reached", waypoint_index=idx,
                            final_error_m=err)
            prev = w
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
        unrelated_count) where unrelated counts contacts touching the EE
        contact geom or the target geom outside the intended pair."""
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

    # ---- A4: approach_until_contact -----------------------------------
    def approach_until_contact(self, request):
        try:
            self._check_finite_state()
            pre = self._vec3(request["precontact_position_m"])
            direc = self._vec3(request["approach_direction_unit"])
            max_travel = self._scalar(request["max_travel_m"], 1e-9, 0.08,
                                      "travel")
            max_speed = self._scalar(request["max_approach_speed_m_s"], 1e-9,
                                     0.05, "speed")
            max_dur = self._scalar(request["max_duration_s"], 0.25, 8.0,
                                   "duration")
            nrm = float(np.linalg.norm(direc))
            if nrm < 1e-6:
                raise CapabilityError("zero_direction")
            u = direc / nrm
            ee_gid = self._geom_id(self._EE_CONTACT_GEOM)
            tgt_gid = self._geom_id(self._TARGET_GEOM)
            self._solve_ik(pre)
        except CapabilityError as e:
            return _err(str(e))
        except (KeyError, TypeError) as e:
            return _err(f"bad_request:{e}")

        # phase 1: reach precontact (brief settle, no long hold)
        deadline = self.data.time + max_dur
        reached, perr, _ = self._regulate(pre, deadline, self._POS_TOL,
                                          hold_s=0.05)
        if not reached:
            self._command_q(self.get_joint_positions())
            return _err("precontact_not_reached", final_error_m=perr)

        self._mj.mj_forward(self.model, self.data)
        c0, _, unrel0 = self._contact_report(ee_gid, tgt_gid)
        if unrel0 > 0 or c0:
            self._command_q(self.get_joint_positions())
            return _err("unexpected_contact_before_approach")

        # phase 2: advance along the declared ray at bounded speed
        dt = float(self.model.opt.timestep)
        step_dist = max_speed * dt
        start = self._ee().copy()
        bias = np.zeros(3)
        travelled = 0.0
        resolve_every = max(1, int(0.01 / dt))
        i = 0
        while self.data.time < deadline:
            contact, pen, unrel = self._contact_report(ee_gid, tgt_gid)
            if unrel > 0:
                self._command_q(self.get_joint_positions())
                return _err("unrelated_contact")
            if contact:
                self._command_q(self.get_joint_positions())
                self._hold_current(0.1)
                _, pen2, u2 = self._contact_report(ee_gid, tgt_gid)
                if u2 > 0:
                    return _err("unrelated_contact")
                return _ok(contact=True, penetration_m=max(pen, pen2),
                           post_contact_speed_m_s=self._ee_speed(),
                           travel_m=travelled)
            if travelled >= max_travel - 1e-6:
                self._command_q(self.get_joint_positions())
                return _err("travel_exhausted_no_contact", travel_m=travelled)
            if i % resolve_every == 0:
                cmd = min(travelled + step_dist, max_travel)
                aim = start + u * cmd
                cur = self._ee()
                bias += 0.3 * (aim - cur)
                bn = float(np.linalg.norm(bias))
                if bn > 0.05:
                    bias *= 0.05 / bn
                try:
                    q = self.ik(aim + bias, q_init=self.get_joint_positions(),
                                raise_on_unreachable=False)
                    self._command_q(q)
                except IKUnreachableError:
                    pass
            self.step(1)
            i += 1
            travelled = max(0.0, min(float(np.dot(self._ee() - start, u)),
                                     max_travel))
        self._command_q(self.get_joint_positions())
        return _err("duration_exhausted_no_contact", travel_m=travelled)

    # ---- A5: move_cartesian_offset_and_return -------------------------
    def move_cartesian_offset_and_return(self, request):
        try:
            self._check_finite_state()
            offset = self._vec3(request["offset_robot_base_m"], lo=-0.06,
                                hi=0.06)
            leg_dur = self._scalar(request["max_duration_per_leg_s"], 0.25,
                                   5.0, "leg_duration")
        except CapabilityError as e:
            return _err(str(e))
        except (KeyError, TypeError) as e:
            return _err(f"bad_request:{e}")

        # The iiwa base is fixed at the world origin with identity
        # orientation, so a robot-base offset equals a world-frame offset.
        # Capture the start EE pose once; both legs reference it.
        start_ee = self._ee().copy()
        target_out = start_ee + offset
        try:
            self._solve_ik(target_out)
            self._solve_ik(start_ee)
        except CapabilityError as e:
            return _err(str(e))

        req_disp = float(np.linalg.norm(offset))

        # outbound leg (must complete before return begins)
        deadline = self.data.time + leg_dur
        reached, oerr, _ = self._regulate(target_out, deadline, self._POS_TOL,
                                          hold_s=0.25)
        if not reached:
            self._command_q(self.get_joint_positions())
            return _err("outbound_failed", final_error_m=oerr)
        out_ee = self._ee().copy()
        achieved = float(np.linalg.norm(out_ee - start_ee))
        frac = (achieved / req_disp) if req_disp > 1e-9 else 1.0
        if frac < 0.8:
            self._command_q(self.get_joint_positions())
            return _err("insufficient_displacement",
                        displacement_fraction=frac)

        # return leg
        deadline = self.data.time + leg_dur
        rreached, rerr, _ = self._regulate(start_ee, deadline, self._POS_TOL,
                                           hold_s=0.5)
        if not rreached:
            self._command_q(self.get_joint_positions())
            return _err("return_failed", final_error_m=rerr)
        return _ok(outbound_error_m=oerr, return_error_m=rerr,
                   displacement_fraction=frac)


def build():
    """Return a Robot bound to the fixed capability scene MJCF."""
    spec = ArmSpec(
        ee_site_name="attachment_site",
        ee_body_name="link7",
        arm_joint_names=list(_ARM_JOINTS),
        arm_actuator_names=list(_ARM_ACTS),
        joint_limits=dict(_LIMITS),
        home_qpos=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        ik_damping=1e-3,
        ik_max_iter=100,
        ik_tolerance=1e-3,
        ik_step_clamp=0.2,
        ik_raise_on_unreachable=True,
        sim_dt=0.002,
    )
    return Robot.from_mjcf("mjcf.xml", spec=spec)
