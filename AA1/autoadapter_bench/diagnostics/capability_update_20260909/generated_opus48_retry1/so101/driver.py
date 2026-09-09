# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for the SO-101 5-DoF serial arm.

Subclasses ArmSerialDLSSkeleton (DLS IK + actuator interp + weld grasp) and
implements the public capability contract (A1..A5) with closed-loop
regulation, ordering, holds, and bounded failure handling. All timing uses
data.time (sim seconds). Physics advances only through actuator commands.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import (
    ArmSerialDLSSkeleton,
    IKUnreachableError,
)
from auto_adapter.skeletons.base import ArmSpec

# ── Arm joints used for IK (5 DoF). jaw_visual_joint is the gripper. ──
ARM_JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
ARM_ACTS = ["act_shoulder_pan", "act_shoulder_lift", "act_elbow_flex",
            "act_wrist_flex", "act_wrist_roll"]
JOINT_LIMITS = {
    "shoulder_pan": (-1.92, 1.92),
    "shoulder_lift": (-1.75, 1.75),
    "elbow_flex": (-1.69, 1.69),
    "wrist_flex": (-1.66, 1.66),
    "wrist_roll": (-2.74, 2.84),
}
GRIPPER_ACTS = ["act_jaw_visual"]
GRIPPER_JOINTS = ["jaw_visual_joint"]
JAW_LO, JAW_HI = -0.175, 1.75  # jaw_visual_joint ctrlrange


class CapabilityError(RuntimeError):
    """Bounded capability failure raised when a request cannot be completed."""


class Robot(ArmSerialDLSSkeleton):
    """SO-101 capability driver."""

    def __init__(self, model, data, spec: ArmSpec) -> None:
        super().__init__(model, data, spec)
        self._dt = float(self.model.opt.timestep)
        self._last_cmd_q = self.get_joint_positions()
        self._open_ctrl, self._close_ctrl = self._probe_gripper_direction()
        # Cache robot collision geoms (only the finger/TCP geoms collide).
        self._robot_collision_geoms = self._collect_robot_geoms()

    # ── gripper direction probe (scratch data only) ─────────────────
    def _probe_gripper_direction(self) -> tuple[float, float]:
        mj = self._mj
        fixed = mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_GEOM, "fixed_jaw")
        moving = mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_GEOM, "moving_jaw")
        if fixed < 0 or moving < 0 or not self._gripper_actuator_ids:
            return JAW_HI, JAW_LO
        aid = self._gripper_actuator_ids[0]

        def sep(ctrl_val: float) -> float:
            s = mj.MjData(self.model)
            mj.mj_copyData(s, self.model, self.data)
            s.ctrl[aid] = float(ctrl_val)
            for _ in range(200):
                mj.mj_step(self.model, s)
            return float(np.linalg.norm(s.geom_xpos[moving] - s.geom_xpos[fixed]))

        return (JAW_HI, JAW_LO) if sep(JAW_HI) >= sep(JAW_LO) else (JAW_LO, JAW_HI)

    def _collect_robot_geoms(self) -> set[int]:
        """Geom ids attached to the robot chain (used to classify contacts)."""
        mj = self._mj
        m = self.model
        robot_bodies = {"base_link", "shoulder_link", "upper_arm_link",
                        "lower_arm_link", "wrist_link", "gripper_link",
                        "jaw_visual"}
        ids: set[int] = set()
        for gid in range(m.ngeom):
            bid = int(m.geom_bodyid[gid])
            if m.body(bid).name in robot_bodies:
                ids.add(gid)
        return ids

    def _frac_to_ctrl(self, frac: float) -> float:
        frac = float(np.clip(frac, 0.0, 1.0))
        return self._close_ctrl + frac * (self._open_ctrl - self._close_ctrl)

    def _jawpos_to_frac(self, jpos: float) -> float:
        lo, hi = min(self._open_ctrl, self._close_ctrl), max(self._open_ctrl, self._close_ctrl)
        raw = float(np.clip((float(jpos) - lo) / (hi - lo) if hi > lo else 0.0, 0.0, 1.0))
        return raw if self._open_ctrl >= self._close_ctrl else 1.0 - raw

    # ── validation / state helpers ─────────────────────────────────
    @staticmethod
    def _finite_vec3(v, name: str) -> np.ndarray:
        a = np.asarray(v, dtype=np.float64).reshape(-1)
        if a.shape != (3,) or not np.all(np.isfinite(a)):
            raise CapabilityError(f"{name} must be a finite length-3 vector")
        return a

    @staticmethod
    def _num(v, name: str, lo: float, hi: float) -> float:
        x = float(v)
        if not np.isfinite(x) or x < lo or x > hi:
            raise CapabilityError(f"{name}={v} out of range [{lo}, {hi}]")
        return x

    def _check_state_finite(self) -> None:
        if not (np.all(np.isfinite(self.get_joint_positions()))
                and np.all(np.isfinite(self.get_joint_velocities()))):
            raise CapabilityError("public robot state is not finite")

    def _ik_target(self, target_xyz: np.ndarray, q_init=None) -> np.ndarray:
        try:
            q = self.ik(target_xyz, q_init=q_init, raise_on_unreachable=True)
        except IKUnreachableError as e:
            raise CapabilityError(
                f"target {np.asarray(target_xyz).tolist()} unreachable "
                f"(residual {e.residual:.4f} m)")
        return np.clip(q, self._q_lo, self._q_hi)

    def _hold_gripper_ctrl(self) -> None:
        if self._gripper_actuator_ids:
            aid = self._gripper_actuator_ids[0]
            self.set_gripper_control(float(self.data.ctrl[aid]))

    def _regulate_to_q(self, q_target, deadline_t, ee_target, pos_tol,
                       hold_s, hold_gripper=True) -> bool:
        q_target = np.clip(q_target, self._q_lo, self._q_hi)
        hold_accum = 0.0
        while self.data.time < deadline_t:
            if hold_gripper:
                self._hold_gripper_ctrl()
            self.set_arm_actuators(q_target)
            self.step(1)
            if ee_target is None:
                continue
            err = float(np.linalg.norm(self._ee_pos_now() - ee_target))
            if err <= pos_tol:
                hold_accum += self._dt
                if hold_accum >= hold_s:
                    return True
            else:
                hold_accum = 0.0
        if ee_target is not None and hold_s <= 0.0:
            return float(np.linalg.norm(self._ee_pos_now() - ee_target)) <= pos_tol
        return False

    def _drive_to_ee(self, target_xyz, deadline_t, pos_tol, hold_s,
                     hold_gripper=True) -> bool:
        q = self._ik_target(target_xyz)
        self._last_cmd_q = q
        return self._regulate_to_q(q, deadline_t, target_xyz, pos_tol,
                                   hold_s, hold_gripper=hold_gripper)

    # ── A1: move_end_effector_to_position ──────────────────────────
    def move_end_effector_to_position(self, request) -> dict:
        tgt = self._finite_vec3(request["target_position_m"], "target_position_m")
        if np.any(tgt < -1.0) or np.any(tgt > 1.0):
            raise CapabilityError("target_position_m out of [-1, 1] m")
        max_dur = self._num(request["max_duration_s"], "max_duration_s", 0.25, 8.0)
        self._check_state_finite()
        deadline = self.data.time + max_dur
        ok = self._drive_to_ee(tgt, deadline, 0.015, 0.5)
        final_err = float(np.linalg.norm(self._ee_pos_now() - tgt))
        if not ok:
            self.set_arm_actuators(self.get_joint_positions())
            raise CapabilityError(
                f"A1 could not hold within 0.015 m; final err {final_err:.4f} m")
        return {"ok": True, "final_position_error_m": final_err}

    # ── A2: trace_cartesian_path ───────────────────────────────────
    def trace_cartesian_path(self, request) -> dict:
        wps_raw = request["waypoints_m"]
        if not isinstance(wps_raw, (list, tuple)) or not (2 <= len(wps_raw) <= 8):
            raise CapabilityError("waypoints_m must have 2..8 entries")
        wps = []
        for i, w in enumerate(wps_raw):
            v = self._finite_vec3(w, f"waypoint[{i}]")
            if np.any(v < -1.0) or np.any(v > 1.0):
                raise CapabilityError(f"waypoint[{i}] out of [-1, 1] m")
            wps.append(v)
        seg_dur = self._num(request["max_duration_per_segment_s"],
                            "max_duration_per_segment_s", 0.25, 5.0)
        self._check_state_finite()
        n = len(wps)
        for i, wp in enumerate(wps):
            is_last = (i == n - 1)
            tol = 0.015 if is_last else 0.02
            hold_s = 0.5 if is_last else 0.0
            deadline = self.data.time + seg_dur
            if not self._drive_to_ee(wp, deadline, tol, hold_s):
                self.set_arm_actuators(self.get_joint_positions())
                err = float(np.linalg.norm(self._ee_pos_now() - wp))
                raise CapabilityError(
                    f"A2 failed to reach waypoint {i} in order; err {err:.4f} m")
        final_err = float(np.linalg.norm(self._ee_pos_now() - wps[-1]))
        return {"ok": True, "waypoints_visited": n,
                "terminal_position_error_m": final_err}

    # ── A3: set_gripper_opening ────────────────────────────────────
    def set_gripper_opening(self, request) -> dict:
        frac = self._num(request["opening_fraction"], "opening_fraction", 0.0, 1.0)
        max_dur = self._num(request["max_duration_s"], "max_duration_s", 0.25, 8.0)
        if not self._gripper_actuator_ids:
            raise CapabilityError("no gripper actuator configured")
        gj = self.get_gripper_joint_positions()
        if not all(np.isfinite(v) for v in gj.values()):
            raise CapabilityError("gripper state not finite")
        self._check_state_finite()

        arm_hold = self.get_joint_positions()  # freeze arm target (invariant)
        cmd = self._frac_to_ctrl(frac)
        deadline = self.data.time + max_dur
        hold_accum = 0.0
        ok = False
        while self.data.time < deadline:
            self.set_arm_actuators(arm_hold)
            self.set_gripper_control(cmd)
            self.step(1)
            jpos = list(self.get_gripper_joint_positions().values())[0]
            if abs(self._jawpos_to_frac(jpos) - frac) <= 0.1:
                hold_accum += self._dt
                if hold_accum >= 0.25:
                    ok = True
                    break
            else:
                hold_accum = 0.0
        meas = self._jawpos_to_frac(list(self.get_gripper_joint_positions().values())[0])
        if not ok:
            self.set_gripper_control(float(self.data.ctrl[self._gripper_actuator_ids[0]]))
            raise CapabilityError(
                f"A3 aperture not established; frac err {abs(meas-frac):.3f}")
        return {"ok": True, "aperture_fraction": meas}

    # ── A4: approach_until_contact ─────────────────────────────────
    def _tcp_geom_id(self) -> int:
        return int(self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_GEOM,
                                       "so101_tcp_contact"))

    def _target_geom_id(self) -> int:
        return int(self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_GEOM,
                                       "fixed_contact_geom"))

    def _contact_state(self):
        """(touching_target, max_penetration, unrelated_robot_contacts).

        Only contacts that involve a robot geom are classified. Ambient
        object-on-table contacts are ignored (not created by the approach).
        A related contact = robot geom vs the fixed target geom.
        """
        tcp = self._tcp_geom_id()
        tgt = self._target_geom_id()
        touching = False
        pen = 0.0
        unrelated = 0
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            g1, g2 = int(c.geom1), int(c.geom2)
            involves_robot = (g1 in self._robot_collision_geoms
                              or g2 in self._robot_collision_geoms)
            if not involves_robot:
                continue  # ambient scene contact, ignore
            depth = -float(c.dist) if c.dist < 0 else 0.0
            if tgt in (g1, g2):
                touching = True
                pen = max(pen, depth)
            else:
                unrelated += 1
        return touching, pen, unrelated

    def approach_until_contact(self, request) -> dict:
        pre = self._finite_vec3(request["precontact_position_m"], "precontact_position_m")
        if np.any(pre < -1.0) or np.any(pre > 1.0):
            raise CapabilityError("precontact_position_m out of [-1, 1] m")
        d = self._finite_vec3(request["approach_direction_unit"], "approach_direction_unit")
        nrm = float(np.linalg.norm(d))
        if nrm < 1e-6:
            raise CapabilityError("approach_direction_unit is zero")
        d = d / nrm
        max_travel = self._num(request["max_travel_m"], "max_travel_m", 1e-9, 0.08)
        max_speed = self._num(request["max_approach_speed_m_s"],
                              "max_approach_speed_m_s", 1e-9, 0.05)
        max_dur = self._num(request["max_duration_s"], "max_duration_s", 0.25, 8.0)
        self._check_state_finite()
        if self._tcp_geom_id() < 0 or self._target_geom_id() < 0:
            raise CapabilityError("contact observations unavailable")

        pre_deadline = self.data.time + max(0.25, 0.5 * max_dur)
        if not self._drive_to_ee(pre, pre_deadline, 0.015, 0.0):
            self.set_arm_actuators(self.get_joint_positions())
            raise CapabilityError("A4 could not reach precontact pose")
        _, _, unrelated = self._contact_state()
        if unrelated > 0:
            self.set_arm_actuators(self.get_joint_positions())
            raise CapabilityError("unrelated contact present before approach")
        return self._advance_ray(pre, d, max_travel, max_speed, max_dur)

    def _advance_ray(self, pre, d, max_travel, max_speed, max_dur) -> dict:
        contact_hold_s, pen_limit, speed_limit = 0.1, 0.005, 0.02
        deadline = self.data.time + max_dur
        step_adv = max_speed * self._dt
        travelled = 0.0
        cur = np.array(pre, dtype=np.float64)
        contact_accum = 0.0
        while self.data.time < deadline:
            touching, pen, unrelated = self._contact_state()
            if unrelated > 0:
                self.set_arm_actuators(self.get_joint_positions())
                raise CapabilityError("unrelated contact created during approach")
            if touching:
                # stop advancing: hold last commanded target
                self.set_arm_actuators(self._last_cmd_q)
                self._hold_gripper_ctrl()
                self.step(1)
                touching2, pen2, unrel2 = self._contact_state()
                if unrel2 > 0:
                    self.set_arm_actuators(self.get_joint_positions())
                    raise CapabilityError("unrelated contact during contact hold")
                if pen2 > pen_limit:
                    self.set_arm_actuators(self.get_joint_positions())
                    raise CapabilityError("penetration exceeded limit")
                speed = float(np.linalg.norm(self.get_ee_velocity()))
                if touching2 and speed <= speed_limit and pen2 <= pen_limit:
                    contact_accum += self._dt
                    if contact_accum >= contact_hold_s:
                        return {"ok": True, "penetration_m": pen2,
                                "post_contact_speed_m_s": speed,
                                "travel_m": travelled}
                else:
                    contact_accum = 0.0
                continue
            if travelled + step_adv > max_travel:
                self.set_arm_actuators(self.get_joint_positions())
                raise CapabilityError("exhausted travel before contact")
            nxt = cur + d * step_adv
            q = self._ik_target(nxt)
            self._last_cmd_q = q
            self.set_arm_actuators(q)
            self._hold_gripper_ctrl()
            self.step(1)
            cur = nxt
            travelled += step_adv
        self.set_arm_actuators(self.get_joint_positions())
        raise CapabilityError("A4 duration exhausted before controlled contact")

    def get_ee_velocity(self) -> np.ndarray:
        jacp = np.zeros((3, self.model.nv), dtype=np.float64)
        self._ee_jac_pos(jacp, self.data)
        return jacp @ self.data.qvel

    # ── A5: move_cartesian_offset_and_return ───────────────────────
    def move_cartesian_offset_and_return(self, request) -> dict:
        off = self._finite_vec3(request["offset_robot_base_m"], "offset_robot_base_m")
        if np.any(off < -0.06) or np.any(off > 0.06):
            raise CapabilityError("offset_robot_base_m out of [-0.06, 0.06] m")
        leg_dur = self._num(request["max_duration_per_leg_s"],
                            "max_duration_per_leg_s", 0.25, 5.0)
        self._check_state_finite()

        mj = self._mj
        bid = mj.mj_name2id(self.model, mj.mjtObj.mjOBJ_BODY, "base_link")
        R_base = np.array(self.data.xmat[bid], dtype=np.float64).reshape(3, 3)
        start_ee = self._ee_pos_now().copy()
        out_target = start_ee + R_base @ off

        # Outbound leg (must complete before return leg starts).
        deadline = self.data.time + leg_dur
        if not self._drive_to_ee(out_target, deadline, 0.015, 0.25):
            self.set_arm_actuators(self.get_joint_positions())
            err = float(np.linalg.norm(self._ee_pos_now() - out_target))
            raise CapabilityError(f"A5 outbound leg failed; err {err:.4f} m")
        out_disp = self._ee_pos_now() - start_ee

        # Return leg.
        deadline = self.data.time + leg_dur
        if not self._drive_to_ee(start_ee, deadline, 0.015, 0.5):
            self.set_arm_actuators(self.get_joint_positions())
            err = float(np.linalg.norm(self._ee_pos_now() - start_ee))
            raise CapabilityError(f"A5 return leg failed; err {err:.4f} m")
        return {"ok": True,
                "outbound_displacement_m": out_disp.tolist(),
                "terminal_position_error_m": float(
                    np.linalg.norm(self._ee_pos_now() - start_ee))}


def build() -> Robot:
    """Construct the SO-101 capability robot from the scene MJCF."""
    spec = ArmSpec(
        ee_site_name="ee_site",
        ee_body_name="gripper_link",
        arm_joint_names=ARM_JOINTS,
        arm_actuator_names=ARM_ACTS,
        joint_limits=JOINT_LIMITS,
        ik_damping=5e-3,
        ik_max_iter=200,
        ik_tolerance=5e-3,
        ik_step_clamp=0.15,
        ik_raise_on_unreachable=True,
        gripper_actuator_names=GRIPPER_ACTS,
        gripper_joint_names=GRIPPER_JOINTS,
        grasp_backend="weld",
        weld_graspable_bodies=["banana", "mug", "bottle", "screwdriver",
                               "duck", "lego"],
        sim_dt=0.002,
    )
    return Robot.from_mjcf("mjcf.xml", spec=spec)
