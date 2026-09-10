# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for UFACTORY xArm7 (ArmSerialDLSSkeleton).

Bindings + full implementations of the public capability contract:
  A1 move_end_effector_to_position
  A2 trace_cartesian_path
  A3 set_gripper_opening
  A4 approach_until_contact
  A5 move_cartesian_offset_and_return

Control model
-------------
* 7 position-servo arm actuators act1..act7 (native joint targets).
* 1 tendon gripper actuator `gripper`, ctrlrange [0, 255].
    - Probed: ctrl=0   -> driver joints ~0.00 rad  (jaws OPEN)
    - Probed: ctrl=255 -> driver joints ~0.83 rad  (jaws CLOSED)
  So opening_fraction 1.0 => ctrl 0, opening_fraction 0.0 => ctrl 255.
  Aperture fraction from state = 1 - driver_joint / driver_range_hi.
* All timing uses data.time (sim seconds). Physics advanced only through
  actuator ctrl + step(); live qpos/qvel are never written. IK uses the
  skeleton's DLS on scratch data.
"""
from __future__ import annotations

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton, IKUnreachableError
from auto_adapter.skeletons.base import ArmSpec

_ARM_JOINTS = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "joint7"]
_ARM_ACTS = ["act1", "act2", "act3", "act4", "act5", "act6", "act7"]
_JOINT_LIMITS = {
    "joint1": (-6.28319, 6.28319),
    "joint2": (-2.059, 2.0944),
    "joint3": (-6.28319, 6.28319),
    "joint4": (-0.19198, 3.927),
    "joint5": (-6.28319, 6.28319),
    "joint6": (-1.69297, 3.14159),
    "joint7": (-6.28319, 6.28319),
}
# A stable elbow-up-ish home that keeps the wrist above the base.
_HOME_Q = [0.0, -0.3, 0.0, 0.6, 0.0, 0.9, 0.0]

# Gripper aperture observation joints (parallel driver joints).
_GRIP_DRIVER_JOINTS = ["left_driver_joint", "right_driver_joint"]
_GRIP_JOINT_HI = 0.85  # rad, jnt_range upper for driver joints


class CapabilityError(RuntimeError):
    """Bounded capability failure raised when a request cannot complete."""


def _finite(a) -> bool:
    return bool(np.all(np.isfinite(np.asarray(a, dtype=np.float64))))


class Robot(ArmSerialDLSSkeleton):
    """xArm7 capability robot."""

    # ---- gripper native command mapping -------------------------------
    GRIP_OPEN_CTRL = 0.0     # opening_fraction 1.0
    GRIP_CLOSE_CTRL = 255.0  # opening_fraction 0.0

    def _grip_ctrl_for_fraction(self, frac: float) -> float:
        frac = float(np.clip(frac, 0.0, 1.0))
        return self.GRIP_CLOSE_CTRL + frac * (self.GRIP_OPEN_CTRL - self.GRIP_CLOSE_CTRL)

    def _gripper_aperture_fraction(self) -> float:
        pos = self.get_gripper_joint_positions()
        vals = [pos[n] for n in _GRIP_DRIVER_JOINTS if n in pos]
        if not vals:
            vals = list(pos.values())
        driver = float(np.mean(vals)) if vals else 0.0
        return float(np.clip(1.0 - driver / _GRIP_JOINT_HI, 0.0, 1.0))

    # ================================================================
    # Shared helpers
    # ================================================================
    def _dt(self) -> float:
        return float(self.model.opt.timestep)

    def _hold_arm(self, q_hold: np.ndarray) -> None:
        """Keep commanding the current arm target (no re-plan)."""
        self.set_arm_actuators(q_hold)

    def _regulate_to_xyz(self, target, deadline_s, tol, hold_s, q_seed=None):
        """Closed-loop: IK to target, drive there, then require continuous
        hold of position error <= tol for hold_s seconds before deadline.

        Returns (ok, q_cmd, final_err). Advances real physics via actuators.
        The gripper command is left untouched by this routine.
        """
        target = np.asarray(target, dtype=np.float64).reshape(3)
        try:
            q_cmd = self.ik(target, q_init=q_seed, raise_on_unreachable=True)
        except IKUnreachableError:
            return False, self.get_joint_positions(), float("inf")

        dt = self._dt()
        hold_accum = 0.0
        last_err = float("inf")
        while self.data.time < deadline_s:
            self.set_arm_actuators(q_cmd)
            self.step(1)
            ee = self._ee_pos_now()
            last_err = float(np.linalg.norm(target - ee))
            if last_err <= tol:
                hold_accum += dt
                if hold_accum >= hold_s:
                    return True, q_cmd, last_err
            else:
                hold_accum = 0.0
        return (last_err <= tol), q_cmd, last_err

    # ================================================================
    # A1: move_end_effector_to_position
    # ================================================================
    def move_end_effector_to_position(self, request):
        target = np.asarray(request["target_position_m"], dtype=np.float64)
        max_dur = float(request["max_duration_s"])
        if target.shape != (3,) or not _finite(target):
            raise CapabilityError("target_position_m must be finite length-3")
        if not (0.25 <= max_dur <= 8.0):
            raise CapabilityError("max_duration_s out of bounds")
        q_now = self.get_joint_positions()
        if not _finite(q_now):
            raise CapabilityError("robot state not finite")

        deadline = self.data.time + max_dur
        ok, q_cmd, err = self._regulate_to_xyz(
            target, deadline, tol=0.015, hold_s=0.5, q_seed=q_now
        )
        if not ok:
            self.set_arm_actuators(q_cmd)  # stop motion; hold last command
            raise CapabilityError(
                f"move_end_effector_to_position: err={err:.4f} m not held within {max_dur}s"
            )
        return {"ok": True, "final_error_m": err, "sim_time_s": float(self.data.time)}

    # ================================================================
    # A3: set_gripper_opening
    # ================================================================
    def set_gripper_opening(self, request):
        frac = float(request["opening_fraction"])
        max_dur = float(request["max_duration_s"])
        if not np.isfinite(frac) or not (0.0 <= frac <= 1.0):
            raise CapabilityError("opening_fraction out of bounds")
        if not (0.25 <= max_dur <= 8.0):
            raise CapabilityError("max_duration_s out of bounds")

        start_frac = self._gripper_aperture_fraction()
        if not np.isfinite(start_frac):
            raise CapabilityError("gripper state not finite")

        # Hold current arm target (do not change arm pose).
        q_hold = np.array(
            [float(self.data.ctrl[aid]) for aid in self._arm_actuator_ids],
            dtype=np.float64,
        )
        cmd = self._grip_ctrl_for_fraction(frac)
        self.set_gripper_control(cmd)

        dt = self._dt()
        deadline = self.data.time + max_dur
        hold_accum = 0.0
        last_err = float("inf")
        while self.data.time < deadline:
            self._hold_arm(q_hold)
            self.set_gripper_control(cmd)
            self.step(1)
            cur = self._gripper_aperture_fraction()
            last_err = abs(cur - frac)
            if last_err <= 0.1:
                hold_accum += dt
                if hold_accum >= 0.25:
                    return {
                        "ok": True,
                        "aperture_error": last_err,
                        "start_fraction": start_frac,
                        "sim_time_s": float(self.data.time),
                    }
            else:
                hold_accum = 0.0
        raise CapabilityError(
            f"set_gripper_opening: aperture err={last_err:.3f} not held within {max_dur}s"
        )

    # ================================================================
    # A2: trace_cartesian_path
    # ================================================================
    def _drive_segment(self, target, seg_deadline, tol, hold_s, q_seed):
        """Drive to `target`; track cross-track error along the straight line
        from segment start to target. Returns (ok, q_cmd, max_cross, err)."""
        target = np.asarray(target, dtype=np.float64).reshape(3)
        try:
            q_cmd = self.ik(target, q_init=q_seed, raise_on_unreachable=True)
        except IKUnreachableError:
            return False, self.get_joint_positions(), float("inf"), float("inf")

        start = self._ee_pos_now().copy()
        seg = target - start
        seg_len = float(np.linalg.norm(seg))
        seg_dir = seg / seg_len if seg_len > 1e-9 else np.zeros(3)

        dt = self._dt()
        hold_accum = 0.0
        max_cross = 0.0
        last_err = float("inf")
        while self.data.time < seg_deadline:
            self.set_arm_actuators(q_cmd)
            self.step(1)
            ee = self._ee_pos_now()
            if seg_len > 1e-9:
                rel = ee - start
                along = float(np.dot(rel, seg_dir))
                cross = float(np.linalg.norm(rel - along * seg_dir))
                max_cross = max(max_cross, cross)
            last_err = float(np.linalg.norm(target - ee))
            if last_err <= tol:
                hold_accum += dt
                if hold_accum >= hold_s:
                    return True, q_cmd, max_cross, last_err
            else:
                hold_accum = 0.0
        return (last_err <= tol), q_cmd, max_cross, last_err

    def trace_cartesian_path(self, request):
        wps = request["waypoints_m"]
        seg_dur = float(request["max_duration_per_segment_s"])
        pts = [np.asarray(w, dtype=np.float64) for w in wps]
        if not (2 <= len(pts) <= 8):
            raise CapabilityError("waypoints_m count out of bounds")
        for p in pts:
            if p.shape != (3,) or not _finite(p):
                raise CapabilityError("each waypoint must be finite length-3")
        if not (0.25 <= seg_dur <= 5.0):
            raise CapabilityError("max_duration_per_segment_s out of bounds")
        if not _finite(self.get_joint_positions()):
            raise CapabilityError("robot state not finite")

        q_seed = self.get_joint_positions()
        n = len(pts)
        for i, wp in enumerate(pts):
            is_last = i == n - 1
            hold_s = 0.5 if is_last else 0.0
            tol = 0.015 if is_last else 0.02
            seg_deadline = self.data.time + seg_dur
            ok, q_cmd, max_cross, err = self._drive_segment(
                wp, seg_deadline, tol=tol, hold_s=hold_s, q_seed=q_seed
            )
            q_seed = q_cmd
            if max_cross > 0.02 or not ok:
                self.set_arm_actuators(q_cmd)
                raise CapabilityError(
                    f"trace_cartesian_path: wp{i} cross={max_cross:.4f} err={err:.4f}"
                )
        return {"ok": True, "waypoints": n, "sim_time_s": float(self.data.time)}

    # ================================================================
    # A5: move_cartesian_offset_and_return
    # ================================================================
    def move_cartesian_offset_and_return(self, request):
        offset = np.asarray(request["offset_robot_base_m"], dtype=np.float64)
        leg_dur = float(request["max_duration_per_leg_s"])
        if offset.shape != (3,) or not _finite(offset):
            raise CapabilityError("offset_robot_base_m must be finite length-3")
        if not np.all((offset >= -0.06) & (offset <= 0.06)):
            raise CapabilityError("offset components out of bounds")
        if not (0.25 <= leg_dur <= 5.0):
            raise CapabilityError("max_duration_per_leg_s out of bounds")

        # Base is fixed at world origin (fixed-base arm): base-frame == world.
        start_pos = self._ee_pos_now().copy()
        if not _finite(start_pos):
            raise CapabilityError("robot state not finite")
        q_start = self.get_joint_positions()
        out_target = start_pos + offset

        # Outbound leg.
        deadline = self.data.time + leg_dur
        ok, q_out, err = self._regulate_to_xyz(
            out_target, deadline, tol=0.015, hold_s=0.25, q_seed=q_start
        )
        disp = float(np.linalg.norm(self._ee_pos_now() - start_pos))
        want = float(np.linalg.norm(offset))
        frac = disp / want if want > 1e-9 else 1.0
        if not ok or frac < 0.8:
            self.set_arm_actuators(q_out)
            raise CapabilityError(f"outbound leg failed err={err:.4f} disp_frac={frac:.2f}")

        # Return leg (only after outbound completed).
        deadline = self.data.time + leg_dur
        ok, q_ret, err = self._regulate_to_xyz(
            start_pos, deadline, tol=0.015, hold_s=0.5, q_seed=q_out
        )
        if not ok:
            self.set_arm_actuators(q_ret)
            raise CapabilityError(f"return leg failed err={err:.4f}")
        return {
            "ok": True,
            "outbound_displacement_fraction": frac,
            "return_error_m": err,
            "sim_time_s": float(self.data.time),
        }

    # ================================================================
    # A4: approach_until_contact
    # ================================================================
    def _contact_geom_sets(self):
        """Resolve (target_geom_id, floor_geom_id, robot_geom_ids)."""
        mj = self._mj
        m = self.model
        tgt = mj.mj_name2id(m, mj.mjtObj.mjOBJ_GEOM, "fixed_contact_geom")
        floor = mj.mj_name2id(m, mj.mjtObj.mjOBJ_GEOM, "fixed_floor")
        robot = [gi for gi in range(m.ngeom) if gi not in (tgt, floor)]
        return tgt, floor, set(robot)

    def _scan_contacts(self, target_geom, floor_geom, robot_geoms):
        """Return (target_contact, max_pen, unrelated_count) for current data.

        A robot geom touching the target geom is desired contact; a robot geom
        touching anything else (floor / unexpected) is unrelated contact.
        """
        target_contact = False
        max_pen = 0.0
        unrelated = 0
        for ci in range(self.data.ncon):
            c = self.data.contact[ci]
            g1, g2 = int(c.geom1), int(c.geom2)
            involves_robot = (g1 in robot_geoms) or (g2 in robot_geoms)
            if not involves_robot:
                continue
            if target_geom in (g1, g2):
                target_contact = True
                max_pen = max(max_pen, -float(c.dist))
            else:
                unrelated += 1
        return target_contact, max_pen, unrelated

    def get_ee_velocity(self) -> np.ndarray:
        """World-frame linear velocity of the EE site (uses live Jacobian)."""
        jacp = np.zeros((3, self.model.nv), dtype=np.float64)
        self._ee_jac_pos(jacp, self.data)
        return jacp @ self.data.qvel

    def approach_until_contact(self, request):
        pre = np.asarray(request["precontact_position_m"], dtype=np.float64)
        direction = np.asarray(request["approach_direction_unit"], dtype=np.float64)
        max_travel = float(request["max_travel_m"])
        max_speed = float(request["max_approach_speed_m_s"])
        max_dur = float(request["max_duration_s"])
        if pre.shape != (3,) or not _finite(pre):
            raise CapabilityError("precontact_position_m must be finite length-3")
        if direction.shape != (3,) or not _finite(direction):
            raise CapabilityError("approach_direction_unit must be finite length-3")
        if not (0.0 < max_travel <= 0.08):
            raise CapabilityError("max_travel_m out of bounds")
        if not (0.0 < max_speed <= 0.05):
            raise CapabilityError("max_approach_speed_m_s out of bounds")
        if not (0.25 <= max_dur <= 8.0):
            raise CapabilityError("max_duration_s out of bounds")
        dnorm = float(np.linalg.norm(direction))
        if dnorm < 1e-6:
            raise CapabilityError("approach_direction_unit is degenerate")
        u = direction / dnorm

        tgt_g, floor_g, robot_g = self._contact_geom_sets()
        deadline = self.data.time + max_dur

        # Phase 1: reach precontact pose (closed loop).
        ok, q_pre, err = self._regulate_to_xyz(
            pre, deadline, tol=0.015, hold_s=0.1, q_seed=self.get_joint_positions()
        )
        if not ok:
            self.set_arm_actuators(q_pre)
            raise CapabilityError(f"precontact not reached err={err:.4f}")

        # Phase 2: advance along the ray in speed-limited increments until
        # controlled contact with the target geom is established and held.
        dt = self._dt()
        step_len = max_speed * dt
        travelled = 0.0
        origin = self._ee_pos_now().copy()
        q_seed = q_pre
        while self.data.time < deadline:
            tc, pen, unrel = self._scan_contacts(tgt_g, floor_g, robot_g)
            if unrel > 0:
                self.set_arm_actuators(q_seed)
                raise CapabilityError("unrelated contact during approach")
            if tc:
                # Stop advancing; hold and verify the contact gates.
                contact_hold = 0.0
                hold_deadline = min(deadline, self.data.time + 0.1 + 6 * dt)
                speed = float(np.linalg.norm(self.get_ee_velocity()))
                pen2 = pen
                while self.data.time < hold_deadline:
                    self.set_arm_actuators(q_seed)  # freeze command at contact
                    self.step(1)
                    tc2, pen2, unrel2 = self._scan_contacts(tgt_g, floor_g, robot_g)
                    speed = float(np.linalg.norm(self.get_ee_velocity()))
                    if (not tc2) or unrel2 > 0 or speed > 0.02 or pen2 > 0.005:
                        contact_hold = 0.0
                    else:
                        contact_hold += dt
                    if contact_hold >= 0.1:
                        return {
                            "ok": True,
                            "penetration_m": pen2,
                            "post_contact_speed_m_s": speed,
                            "sim_time_s": float(self.data.time),
                        }
                self.set_arm_actuators(q_seed)
                raise CapabilityError(
                    f"contact not held: pen={pen2:.4f} speed={speed:.4f}"
                )

            if travelled >= max_travel:
                self.set_arm_actuators(q_seed)
                raise CapabilityError("travel exhausted before contact")

            adv = min(step_len, max_travel - travelled)
            sub = origin + (travelled + adv) * u
            try:
                q_seed = self.ik(sub, q_init=q_seed, raise_on_unreachable=True)
            except IKUnreachableError:
                self.set_arm_actuators(q_seed)
                raise CapabilityError("approach sub-target unreachable")
            self.set_arm_actuators(q_seed)
            self.step(1)
            travelled += adv

        raise CapabilityError("approach duration expired before contact")


def build() -> Robot:
    spec = ArmSpec(
        ee_site_name="link_tcp",
        arm_joint_names=list(_ARM_JOINTS),
        arm_actuator_names=list(_ARM_ACTS),
        joint_limits=dict(_JOINT_LIMITS),
        home_qpos=list(_HOME_Q),
        ik_damping=5e-3,
        ik_max_iter=200,
        ik_tolerance=2e-3,
        ik_step_clamp=0.3,
        ik_raise_on_unreachable=True,
        gripper_actuator_names=["gripper"],
        gripper_joint_names=list(_GRIP_DRIVER_JOINTS),
        grasp_backend="noop",
        sim_dt=0.002,
    )
    return Robot.from_mjcf("mjcf.xml", spec=spec)
