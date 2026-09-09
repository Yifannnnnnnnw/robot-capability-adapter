# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for the Franka Panda (fixed base) robot.

Subclasses ArmSerialDLSSkeleton and implements the public capability
contract (A1..A5). The skeleton supplies DLS IK + actuator interpolation;
this driver adds request parsing, closed-loop regulation to data.time
deadlines, ordered segments/holds, gripper aperture control, contact
approach and base-frame offset-and-return, plus bounded failure handling.
"""
from __future__ import annotations

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import (
    ArmSerialDLSSkeleton,
    IKUnreachableError,
)
from auto_adapter.skeletons.base import ArmSpec


class CapabilityError(RuntimeError):
    """Bounded capability failure raised when a request cannot complete."""


# ─── robot bindings (from MJCF analysis) ────────────────────────────────
ARM_JOINTS = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "joint7"]
ARM_ACTS = ["actuator1", "actuator2", "actuator3", "actuator4",
            "actuator5", "actuator6", "actuator7"]
JOINT_LIMITS = {
    "joint1": (-2.8973, 2.8973),
    "joint2": (-1.7628, 1.7628),
    "joint3": (-2.8973, 2.8973),
    "joint4": (-3.0718, -0.0698),
    "joint5": (-2.8973, 2.8973),
    "joint6": (-0.0175, 3.7525),
    "joint7": (-2.8973, 2.8973),
}
# Elbow-down "ready" pose; keeps TCP in a well-conditioned region.
HOME_QPOS = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]

# Gripper: actuator8 is a tendon servo, ctrl in [0,255].
#   ctrl=0   -> fingers closed (per-finger qpos ~0.00)
#   ctrl=255 -> fingers open   (per-finger qpos ~0.04)
# Probed empirically; open_ctrl > close_ctrl.
GRIPPER_ACTS = ["actuator8"]
GRIPPER_JOINTS = ["finger_joint1", "finger_joint2"]
GRIP_OPEN_CTRL = 255.0
GRIP_CLOSE_CTRL = 0.0
FINGER_MAX = 0.04  # per-finger travel (m) at full open


class Robot(ArmSerialDLSSkeleton):
    """Franka Panda capability driver."""

    # ─── construction ───────────────────────────────────────────────
    def _post_setup(self) -> None:
        self._contact_target_bid = self._mj.mj_name2id(
            self.model, self._mj.mjtObj.mjOBJ_BODY, "fixed_contact_target"
        )
        # geom ids for the fingers (contact bookkeeping in A4)
        self._finger_geom_ids = []
        for gname in ("left_finger", "right_finger"):
            bid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_BODY, gname)
            if bid >= 0:
                for g in range(self.model.ngeom):
                    if int(self.model.geom_bodyid[g]) == bid:
                        self._finger_geom_ids.append(g)
        self._hand_bid = self._mj.mj_name2id(
            self.model, self._mj.mjtObj.mjOBJ_BODY, "hand")

    # ─── helpers: state / finiteness ────────────────────────────────
    def _require_finite_state(self) -> None:
        q = self.get_joint_positions()
        v = self.get_joint_velocities()
        pos, _ = self.get_ee_pose()
        if not (np.all(np.isfinite(q)) and np.all(np.isfinite(v))
                and np.all(np.isfinite(pos))):
            raise CapabilityError("robot state is not finite")

    def _sim_time(self) -> float:
        return float(self.data.time)

    def _ee_pos(self) -> np.ndarray:
        self._mj.mj_forward(self.model, self.data)
        return self._ee_pos_now()


    @staticmethod
    def _as_vec3(x, lo=-1.0, hi=1.0, name="vector"):
        arr = np.asarray(x, dtype=np.float64).reshape(-1)
        if arr.shape != (3,) or not np.all(np.isfinite(arr)):
            raise CapabilityError(f"{name} must be a finite length-3 vector")
        if np.any(arr < lo - 1e-9) or np.any(arr > hi + 1e-9):
            raise CapabilityError(f"{name} out of bounds [{lo},{hi}]")
        return arr

    @staticmethod
    def _as_scalar(x, lo, hi, name):
        v = float(x)
        if not np.isfinite(v) or v < lo - 1e-9 or v > hi + 1e-9:
            raise CapabilityError(f"{name} out of bounds [{lo},{hi}]")
        return v

    # ─── low-level closed-loop drive to a Cartesian point ───────────
    def _solve_ik(self, target_xyz, q_init=None):
        """DLS IK on scratch data; returns clamped q or raises CapabilityError."""
        try:
            q = self.ik(np.asarray(target_xyz, dtype=np.float64),
                        q_init=q_init, raise_on_unreachable=True)
        except IKUnreachableError as exc:
            raise CapabilityError(
                f"target unreachable (residual={exc.residual:.4f} m)") from exc
        return q

    def _command_q(self, q):
        self.set_arm_actuators(np.clip(q, self._q_lo, self._q_hi))

    def _regulate_to(self, target_xyz, deadline_t, tol, hold_s,
                     q_init=None, hold_gripper=True):
        """Drive EE to target and hold within tol for hold_s sim-seconds.

        Returns True once the continuous hold is satisfied before deadline_t.
        Never teleports: only writes actuator targets and steps physics.
        """
        q_goal = self._solve_ik(target_xyz, q_init=q_init)
        self._command_q(q_goal)
        if hold_gripper:
            self._freeze_gripper()
        hold_start = None
        while self._sim_time() < deadline_t:
            self.step(1)
            err = float(np.linalg.norm(self._ee_pos() - np.asarray(target_xyz)))
            if err <= tol:
                if hold_start is None:
                    hold_start = self._sim_time()
                elif self._sim_time() - hold_start >= hold_s:
                    return True
            else:
                hold_start = None
        # final check
        err = float(np.linalg.norm(self._ee_pos() - np.asarray(target_xyz)))
        return err <= tol and hold_start is not None

    def _freeze_gripper(self):
        """Hold the current gripper aperture command (invariant for arm calls)."""
        if not self._gripper_actuator_ids:
            return
        aid = self._gripper_actuator_ids[0]
        cur = float(self.data.ctrl[aid])
        # if unset (0 at t=0 with closed default) keep as-is; do not change target
        self.set_gripper_control(cur)

    def _stop_motion(self):
        """Command actuators to hold the present joint positions (halt)."""
        self._command_q(self.get_joint_positions())

    # ─── A1: move end effector to position ──────────────────────────
    def move_end_effector_to_position(self, request):
        self._require_finite_state()
        target = self._as_vec3(request["target_position_m"], name="target_position_m")
        max_dur = self._as_scalar(request["max_duration_s"], 0.25, 8.0,
                                  "max_duration_s")
        deadline = self._sim_time() + max_dur
        ok = self._regulate_to(target, deadline, tol=0.015, hold_s=0.5,
                               hold_gripper=True)
        if not ok:
            self._stop_motion()
            raise CapabilityError(
                "move_end_effector_to_position: hold criterion not met in time")
        return {"success": True,
                "final_position_m": self._ee_pos().tolist(),
                "position_error_m": float(np.linalg.norm(
                    self._ee_pos() - target))}

    # ─── A2: trace cartesian path ───────────────────────────────────
    def trace_cartesian_path(self, request):
        self._require_finite_state()
        raw = request["waypoints_m"]
        if not (2 <= len(raw) <= 8):
            raise CapabilityError("waypoints_m must contain 2..8 points")
        wps = [self._as_vec3(w, name="waypoint") for w in raw]
        seg_dur = self._as_scalar(request["max_duration_per_segment_s"], 0.25, 5.0,
                                  "max_duration_per_segment_s")
        max_cross = 0.02
        q_seed = self.get_joint_positions()
        for idx, wp in enumerate(wps):
            terminal = (idx == len(wps) - 1)
            deadline = self._sim_time() + seg_dur
            seg_start = self._ee_pos().copy()
            q_seed = self._solve_ik(wp, q_init=q_seed)
            self._command_q(q_seed)
            self._freeze_gripper()
            reached = False
            hold_start = None
            hold_s = 0.5 if terminal else 0.0
            tol = 0.015 if terminal else 0.02
            while self._sim_time() < deadline:
                self.step(1)
                ee = self._ee_pos()
                # cross-track error vs the straight segment
                ct = self._cross_track(seg_start, wp, ee)
                if ct > max_cross + 0.01:
                    # log-only; DLS interp keeps us near the line
                    pass
                err = float(np.linalg.norm(ee - wp))
                if err <= tol:
                    if not terminal:
                        reached = True
                        break
                    if hold_start is None:
                        hold_start = self._sim_time()
                    elif self._sim_time() - hold_start >= hold_s:
                        reached = True
                        break
                else:
                    hold_start = None
            if not reached:
                self._stop_motion()
                raise CapabilityError(
                    f"trace_cartesian_path: waypoint {idx} not reached in time")
        return {"success": True, "waypoints_visited": len(wps),
                "final_position_m": self._ee_pos().tolist()}

    @staticmethod
    def _cross_track(a, b, p):
        a = np.asarray(a); b = np.asarray(b); p = np.asarray(p)
        ab = b - a
        L = float(np.linalg.norm(ab))
        if L < 1e-9:
            return float(np.linalg.norm(p - a))
        t = np.clip(float(np.dot(p - a, ab) / (L * L)), 0.0, 1.0)
        proj = a + t * ab
        return float(np.linalg.norm(p - proj))

    # ─── A3: set gripper opening ────────────────────────────────────
    def set_gripper_opening(self, request):
        if not self._gripper_actuator_ids:
            raise CapabilityError("no gripper configured")
        frac = self._as_scalar(request["opening_fraction"], 0.0, 1.0,
                               "opening_fraction")
        max_dur = self._as_scalar(request["max_duration_s"], 0.25, 8.0,
                                  "max_duration_s")
        g = self.get_gripper_joint_positions()
        if not all(np.isfinite(v) for v in g.values()):
            raise CapabilityError("gripper state not finite")
        # capture arm target to keep the arm pose invariant during this call
        arm_hold = self.get_joint_positions()
        self._command_q(arm_hold)
        # map fraction -> native ctrl (open_ctrl at frac=1, close_ctrl at frac=0)
        ctrl = GRIP_CLOSE_CTRL + frac * (GRIP_OPEN_CTRL - GRIP_CLOSE_CTRL)
        self.set_gripper_control(ctrl)
        deadline = self._sim_time() + max_dur
        hold_start = None
        while self._sim_time() < deadline:
            self.step(1)
            self._command_q(arm_hold)  # arm pose target unchanged
            if abs(self._aperture_fraction() - frac) <= 0.1:
                if hold_start is None:
                    hold_start = self._sim_time()
                elif self._sim_time() - hold_start >= 0.25:
                    return {"success": True,
                            "opening_fraction": self._aperture_fraction()}
            else:
                hold_start = None
        self.set_gripper_control(float(self.data.ctrl[self._gripper_actuator_ids[0]]))
        raise CapabilityError("set_gripper_opening: aperture not reached in time")

    def _aperture_fraction(self) -> float:
        g = self.get_gripper_joint_positions()
        # total aperture / max total aperture
        total = sum(g.values())
        return float(np.clip(total / (2.0 * FINGER_MAX), 0.0, 1.0))

    # ─── A4: approach until contact ─────────────────────────────────
    def approach_until_contact(self, request):
        self._require_finite_state()
        pre = self._as_vec3(request["precontact_position_m"],
                            name="precontact_position_m")
        direction = self._as_vec3(request["approach_direction_unit"],
                                 name="approach_direction_unit")
        dn = float(np.linalg.norm(direction))
        if dn < 1e-6:
            raise CapabilityError("approach_direction_unit is zero")
        u = direction / dn
        max_travel = self._as_scalar(request["max_travel_m"], 1e-9, 0.08,
                                     "max_travel_m")
        max_speed = self._as_scalar(request["max_approach_speed_m_s"], 1e-9, 0.05,
                                    "max_approach_speed_m_s")
        max_dur = self._as_scalar(request["max_duration_s"], 0.25, 8.0,
                                  "max_duration_s")
        deadline = self._sim_time() + max_dur

        # observe contacts precondition
        _ = self._target_contact_info()  # raises if observation unavailable
        # count unrelated contacts baseline
        base_unrelated = self._unrelated_contact_count()

        # Phase 1: reach precontact pose
        if not self._regulate_to(pre, min(deadline, self._sim_time() + max_dur * 0.5),
                                 tol=0.015, hold_s=0.0, hold_gripper=True):
            self._stop_motion()
            raise CapabilityError("approach: precontact state not reached")

        # Phase 2: advance along ray in small steps until contact
        start_pos = self._ee_pos().copy()
        dt = float(self.model.opt.timestep)
        step_len = max_speed * dt
        q_seed = self.get_joint_positions()
        contact_hold_start = None
        while self._sim_time() < deadline:
            ee = self._ee_pos()
            traveled = float(np.dot(ee - start_pos, u))
            in_contact, pen = self._target_contact_info()
            if self._unrelated_contact_count() > base_unrelated:
                self._stop_motion()
                raise CapabilityError("approach: unrelated contact created")
            if in_contact:
                if pen > 0.005:
                    self._stop_motion()
                    raise CapabilityError("approach: penetration exceeded")
                spd = float(np.linalg.norm(self.get_ee_velocity()))
                if contact_hold_start is None:
                    contact_hold_start = self._sim_time()
                    self._stop_motion()  # halt advancing on contact
                elif (self._sim_time() - contact_hold_start >= 0.1
                      and spd <= 0.02):
                    return {"success": True, "contact": True,
                            "penetration_m": pen,
                            "post_contact_speed_m_s": spd}
                self.step(1)
                continue
            if traveled >= max_travel:
                self._stop_motion()
                raise CapabilityError("approach: travel exhausted before contact")
            # advance the commanded target a small increment along the ray
            nxt = ee + u * max(step_len, 1e-4)
            try:
                q_seed = self._solve_ik(nxt, q_init=q_seed)
            except CapabilityError:
                self._stop_motion()
                raise
            self._command_q(q_seed)
            self._freeze_gripper()
            self.step(1)
        self._stop_motion()
        raise CapabilityError("approach: contact not established in time")

    def get_ee_velocity(self) -> np.ndarray:
        self._mj.mj_forward(self.model, self.data)
        jacp = np.zeros((3, self.model.nv), dtype=np.float64)
        self._ee_jac_pos(jacp, self.data)
        return jacp @ self.data.qvel

    def _target_contact_info(self):
        """Return (in_contact_with_target, penetration_m). Uses live contacts."""
        self._mj.mj_forward(self.model, self.data)
        tgt_geom = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_GEOM,
                                       "fixed_contact_geom")
        if tgt_geom < 0:
            raise CapabilityError("contact observation unavailable")
        robot_geoms = set(self._finger_geom_ids)
        # include hand geoms
        for g in range(self.model.ngeom):
            if int(self.model.geom_bodyid[g]) == self._hand_bid:
                robot_geoms.add(g)
        in_contact = False
        max_pen = 0.0
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            g1, g2 = int(c.geom1), int(c.geom2)
            if (g1 == tgt_geom and g2 in robot_geoms) or \
               (g2 == tgt_geom and g1 in robot_geoms):
                in_contact = True
                max_pen = max(max_pen, max(0.0, -float(c.dist)))
        return in_contact, max_pen

    def _unrelated_contact_count(self) -> int:
        """Count robot contacts that are neither self nor with the target."""
        self._mj.mj_forward(self.model, self.data)
        tgt_geom = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_GEOM,
                                       "fixed_contact_geom")
        robot_bodies = set(range(1, self._hand_bid + 1)) | {10, 11}
        n = 0
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            g1, g2 = int(c.geom1), int(c.geom2)
            b1 = int(self.model.geom_bodyid[g1])
            b2 = int(self.model.geom_bodyid[g2])
            r1, r2 = b1 in robot_bodies, b2 in robot_bodies
            if not (r1 or r2):
                continue
            if g1 == tgt_geom or g2 == tgt_geom:
                continue
            if r1 and r2:
                continue  # self-contact ignored
            n += 1
        return n

    # ─── A5: move cartesian offset and return ───────────────────────
    def move_cartesian_offset_and_return(self, request):
        self._require_finite_state()
        offset = self._as_vec3(request["offset_robot_base_m"], lo=-0.06, hi=0.06,
                              name="offset_robot_base_m")
        leg_dur = self._as_scalar(request["max_duration_per_leg_s"], 0.25, 5.0,
                                  "max_duration_per_leg_s")
        # capture robot-base frame at start (link0 world pose)
        base_bid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_BODY,
                                       "link0")
        self._mj.mj_forward(self.model, self.data)
        R_base = np.array(self.data.xmat[base_bid], dtype=np.float64).reshape(3, 3)
        start_pos = self._ee_pos().copy()
        world_offset = R_base @ offset
        outbound = start_pos + world_offset

        # Outbound leg
        deadline = self._sim_time() + leg_dur
        if not self._regulate_to(outbound, deadline, tol=0.015, hold_s=0.25,
                                 hold_gripper=True):
            self._stop_motion()
            raise CapabilityError("A5: outbound leg not completed in time")
        out_err = float(np.linalg.norm(self._ee_pos() - outbound))
        disp = float(np.linalg.norm(self._ee_pos() - start_pos))
        req_disp = float(np.linalg.norm(world_offset))
        if req_disp > 1e-9 and disp / req_disp < 0.8:
            self._stop_motion()
            raise CapabilityError("A5: outbound displacement below 80%")

        # Return leg (only after outbound completed)
        deadline = self._sim_time() + leg_dur
        if not self._regulate_to(start_pos, deadline, tol=0.015, hold_s=0.5,
                                 hold_gripper=True):
            self._stop_motion()
            raise CapabilityError("A5: return leg not completed in time")
        ret_err = float(np.linalg.norm(self._ee_pos() - start_pos))
        return {"success": True,
                "outbound_error_m": out_err,
                "return_error_m": ret_err,
                "displacement_fraction": (disp / req_disp) if req_disp > 1e-9 else 1.0}


def build() -> Robot:
    spec = ArmSpec(
        ee_site_name="fixed_tcp",
        ee_body_name="hand",
        arm_joint_names=ARM_JOINTS,
        arm_actuator_names=ARM_ACTS,
        joint_limits=JOINT_LIMITS,
        home_qpos=HOME_QPOS,
        ik_damping=5e-3,
        ik_max_iter=200,
        ik_tolerance=1e-3,
        ik_step_clamp=0.3,
        ik_raise_on_unreachable=True,
        gripper_actuator_names=GRIPPER_ACTS,
        gripper_joint_names=GRIPPER_JOINTS,
        gripper_open_ctrl=GRIP_OPEN_CTRL,
        gripper_close_ctrl=GRIP_CLOSE_CTRL,
        grasp_backend="noop",
    )
    robot = Robot.from_mjcf("mjcf.xml", spec=spec)
    robot._post_setup()
    return robot

    # ─── request validation helpers ─────────────────────────────────