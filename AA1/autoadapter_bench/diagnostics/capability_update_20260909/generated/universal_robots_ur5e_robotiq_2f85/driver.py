"""
Driver for universal_robots_ur5e_robotiq_2f85.
Robot: UR5e 6-DOF arm + Robotiq 2F-85 gripper (tendon-driven, 1 actuator).
Skeleton: ArmSerialDLSSkeleton with ArmSpec.
"""
from __future__ import annotations
import numpy as np
import mujoco
from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton
from auto_adapter.skeletons.base import ArmSpec

# ── Gripper constants ──────────────────────────────────────────────────────────
# fingers_actuator ctrl range: [0, 255]  0=open, 255=closed
_GRIPPER_OPEN_CTRL   = 0.0
_GRIPPER_CLOSE_CTRL  = 255.0
# right_driver_joint (qpos[6]) range: [0, 0.8]  ~0=open, ~0.78=closed
_DRIVER_OPEN  = 0.019   # measured at ctrl=0
_DRIVER_CLOSE = 0.781   # measured at ctrl=255

# ── Arm joint limits ───────────────────────────────────────────────────────────
_ARM_JOINT_LIMITS = {
    "shoulder_pan_joint":  (-6.2831, 6.2831),
    "shoulder_lift_joint": (-6.2831, 6.2831),
    "elbow_joint":         (-3.1415, 3.1415),
    "wrist_1_joint":       (-6.2831, 6.2831),
    "wrist_2_joint":       (-6.2831, 6.2831),
    "wrist_3_joint":       (-6.2831, 6.2831),
}

# ── Home configuration ─────────────────────────────────────────────────────────
_HOME_QPOS = [0.0, -1.5708, 1.5708, -1.5708, 0.0, 0.0]

# ── Gripper body ids for contact detection ─────────────────────────────────────
_GRIPPER_BODY_NAMES = {
    "right_driver", "right_coupler", "right_spring_link", "right_follower",
    "right_pad", "right_silicone_pad",
    "left_driver",  "left_coupler",  "left_spring_link",  "left_follower",
    "left_pad",  "left_silicone_pad",
    "robotiq_base", "robotiq_mount", "base_mount",
}
_ARM_BODY_NAMES = {
    "base", "shoulder_link", "upper_arm_link", "forearm_link",
    "wrist_1_link", "wrist_2_link", "wrist_3_link",
}


class Robot(ArmSerialDLSSkeleton):
    """UR5e + Robotiq 2F-85 capability driver."""

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _ee_pos(self) -> np.ndarray:
        sid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "pinch_site")
        return self.data.site_xpos[sid].copy()

    def _gripper_fraction(self) -> float:
        """Return normalised opening fraction [0=closed, 1=open]."""
        q = self.data.qpos[6]  # right_driver_joint
        frac = (q - _DRIVER_CLOSE) / (_DRIVER_OPEN - _DRIVER_CLOSE)
        return float(np.clip(frac, 0.0, 1.0))

    def _set_gripper_ctrl(self, fraction: float) -> None:
        """Set gripper actuator from normalised opening fraction."""
        # fraction=1 → open (ctrl=0), fraction=0 → closed (ctrl=255)
        ctrl = _GRIPPER_CLOSE_CTRL + fraction * (_GRIPPER_OPEN_CTRL - _GRIPPER_CLOSE_CTRL)
        ctrl = float(np.clip(ctrl, _GRIPPER_OPEN_CTRL, _GRIPPER_CLOSE_CTRL))
        self.data.ctrl[6] = ctrl

    def _arm_ctrl_from_q(self, q: np.ndarray) -> np.ndarray:
        """Clip arm joint targets to actuator limits."""
        lo = np.array([v[0] for v in _ARM_JOINT_LIMITS.values()])
        hi = np.array([v[1] for v in _ARM_JOINT_LIMITS.values()])
        return np.clip(q, lo, hi)

    def _ik_safe(self, target: np.ndarray) -> np.ndarray | None:
        """Run IK; return joint solution or None on failure."""
        try:
            return self.ik(np.asarray(target, dtype=float))
        except Exception:
            return None

    def _regulate_arm_to(self, q_target: np.ndarray, deadline: float,
                          hold_s: float, tol: float) -> bool:
        """
        Drive arm to q_target via set_arm_actuators, step physics, and
        check EE position hold.  Returns True if hold satisfied before deadline.
        """
        q_clipped = self._arm_ctrl_from_q(q_target)
        self.set_arm_actuators(q_clipped)
        hold_start: float | None = None
        dt = self.model.opt.timestep
        while self.data.time < deadline:
            self.step(1)
            err = float(np.linalg.norm(self._ee_pos() - self._fk_from_q(q_clipped)))
            # use actual EE vs IK target
            if err <= tol:
                if hold_start is None:
                    hold_start = self.data.time
                elif self.data.time - hold_start >= hold_s:
                    return True
            else:
                hold_start = None
        return False

    def _fk_from_q(self, q: np.ndarray) -> np.ndarray:
        """FK using scratch data to get EE position for q (arm joints only)."""
        scratch = mujoco.MjData(self.model)
        scratch.qpos[:6] = q[:6]
        scratch.qpos[6:] = self.data.qpos[6:]
        mujoco.mj_forward(self.model, scratch)
        sid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "pinch_site")
        return scratch.site_xpos[sid].copy()

    def _move_to_xyz(self, target_xyz: np.ndarray, deadline: float,
                     hold_s: float, tol: float) -> bool:
        """IK + regulate arm to reach target_xyz before deadline."""
        q_sol = self._ik_safe(target_xyz)
        if q_sol is None:
            return False
        q_clipped = self._arm_ctrl_from_q(q_sol)
        self.set_arm_actuators(q_clipped)
        hold_start: float | None = None
        while self.data.time < deadline:
            self.step(1)
            err = float(np.linalg.norm(self._ee_pos() - target_xyz))
            if err <= tol:
                if hold_start is None:
                    hold_start = self.data.time
                elif self.data.time - hold_start >= hold_s:
                    return True
            else:
                hold_start = None
        return False

    # ── A1: move_end_effector_to_position ──────────────────────────────────────
    def move_end_effector_to_position(self, request: dict) -> dict:
        """
        Move EE to target_position_m (world frame).
        Hold within 0.015 m for 0.5 s or return failure.
        """
        target = np.asarray(request["target_position_m"], dtype=float)
        max_dur = float(request["max_duration_s"])
        if not (0.25 <= max_dur <= 8.0):
            return {"success": False, "error": "max_duration_s out of bounds"}
        if target.shape != (3,) or np.any(np.abs(target) > 1.0):
            return {"success": False, "error": "target_position_m out of bounds"}

        deadline = self.data.time + max_dur
        hold_s = 0.5
        tol = 0.015

        ok = self._move_to_xyz(target, deadline, hold_s, tol)
        if not ok:
            # Stop: hold current actuator targets (already set)
            return {"success": False, "error": "Could not reach target within duration"}
        return {"success": True}

    # ── A2: trace_cartesian_path ───────────────────────────────────────────────
    def trace_cartesian_path(self, request: dict) -> dict:
        """
        Visit waypoints in order; cross-track <= 0.02 m; terminal hold 0.5 s.
        """
        waypoints = [np.asarray(w, dtype=float) for w in request["waypoints_m"]]
        seg_dur = float(request["max_duration_per_segment_s"])
        if not (0.25 <= seg_dur <= 5.0):
            return {"success": False, "error": "max_duration_per_segment_s out of bounds"}
        if not (2 <= len(waypoints) <= 8):
            return {"success": False, "error": "waypoints_m count out of bounds"}
        for w in waypoints:
            if w.shape != (3,) or np.any(np.abs(w) > 1.0):
                return {"success": False, "error": "waypoint out of bounds"}

        tol_cross = 0.02
        tol_term  = 0.015
        hold_s    = 0.5

        for idx, wp in enumerate(waypoints):
            deadline = self.data.time + seg_dur
            is_last = (idx == len(waypoints) - 1)
            h = hold_s if is_last else 0.0
            t = tol_term if is_last else tol_cross

            q_sol = self._ik_safe(wp)
            if q_sol is None:
                return {"success": False, "error": f"IK failed for waypoint {idx}"}
            q_clipped = self._arm_ctrl_from_q(q_sol)
            self.set_arm_actuators(q_clipped)

            hold_start: float | None = None
            reached = False
            while self.data.time < deadline:
                self.step(1)
                err = float(np.linalg.norm(self._ee_pos() - wp))
                if err <= t:
                    if is_last:
                        if hold_start is None:
                            hold_start = self.data.time
                        elif self.data.time - hold_start >= hold_s:
                            reached = True
                            break
                    else:
                        reached = True
                        break
                else:
                    hold_start = None
            if not reached:
                return {"success": False,
                        "error": f"Waypoint {idx} not reached within segment duration"}
        return {"success": True}

    # ── A3: set_gripper_opening ────────────────────────────────────────────────
    def set_gripper_opening(self, request: dict) -> dict:
        """
        Set gripper to opening_fraction [0=closed, 1=open].
        Hold within 0.10 for 0.25 s; bidirectional excursion >= 50%.
        """
        frac = float(request["opening_fraction"])
        max_dur = float(request["max_duration_s"])
        if not (0.0 <= frac <= 1.0):
            return {"success": False, "error": "opening_fraction out of bounds"}
        if not (0.25 <= max_dur <= 8.0):
            return {"success": False, "error": "max_duration_s out of bounds"}

        tol = 0.10
        hold_s = 0.25
        deadline = self.data.time + max_dur

        # Capture start fraction for excursion check
        start_frac = self._gripper_fraction()
        self._set_gripper_ctrl(frac)

        hold_start: float | None = None
        max_excursion = 0.0
        while self.data.time < deadline:
            self.step(1)
            cur = self._gripper_fraction()
            excursion = abs(cur - start_frac)
            if excursion > max_excursion:
                max_excursion = excursion
            err = abs(cur - frac)
            if err <= tol:
                if hold_start is None:
                    hold_start = self.data.time
                elif self.data.time - hold_start >= hold_s:
                    if max_excursion >= 0.5 * abs(frac - start_frac) or abs(frac - start_frac) < 0.01:
                        return {"success": True}
                    # keep waiting for excursion
            else:
                hold_start = None

        return {"success": False, "error": "Gripper did not reach target within duration"}

    # ── A4: approach_until_contact ─────────────────────────────────────────────
    def approach_until_contact(self, request: dict) -> dict:
        """
        Move to precontact_position_m, then advance along approach_direction_unit
        until contact is detected or max_travel_m / max_duration_s is exhausted.
        """
        precontact = np.asarray(request["precontact_position_m"], dtype=float)
        direction  = np.asarray(request["approach_direction_unit"], dtype=float)
        max_travel = float(request["max_travel_m"])
        max_speed  = float(request["max_approach_speed_m_s"])
        max_dur    = float(request["max_duration_s"])

        # Validate
        if precontact.shape != (3,) or np.any(np.abs(precontact) > 1.0):
            return {"success": False, "error": "precontact_position_m out of bounds"}
        dir_norm = float(np.linalg.norm(direction))
        if dir_norm < 1e-6:
            return {"success": False, "error": "approach_direction_unit is zero"}
        direction = direction / dir_norm
        if not (0.0 < max_travel <= 0.08):
            return {"success": False, "error": "max_travel_m out of bounds"}
        if not (0.0 < max_speed <= 0.05):
            return {"success": False, "error": "max_approach_speed_m_s out of bounds"}
        if not (0.25 <= max_dur <= 8.0):
            return {"success": False, "error": "max_duration_s out of bounds"}

        deadline = self.data.time + max_dur

        # ── Phase 1: move to precontact position ──────────────────────────────
        precontact_deadline = self.data.time + max_dur * 0.6
        ok = self._move_to_xyz(precontact, precontact_deadline, hold_s=0.05, tol=0.015)
        if not ok:
            return {"success": False, "error": "Could not reach precontact position"}

        # ── Phase 2: approach along ray ───────────────────────────────────────
        dt = self.model.opt.timestep
        step_dist = max_speed * dt  # distance per physics step
        start_pos = self._ee_pos().copy()
        travel_so_far = 0.0
        contact_hold_start: float | None = None
        contact_hold_s = 0.1
        contact_detected = False

        # Identify gripper + arm body ids for unrelated-contact filtering
        gripper_body_ids = set()
        arm_body_ids = set()
        for i in range(self.model.nbody):
            name = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_BODY, i)
            if name in _GRIPPER_BODY_NAMES:
                gripper_body_ids.add(i)
            if name in _ARM_BODY_NAMES:
                arm_body_ids.add(i)
        robot_body_ids = gripper_body_ids | arm_body_ids

        while self.data.time < deadline and travel_so_far < max_travel:
            # Compute next target
            next_pos = start_pos + (travel_so_far + step_dist) * direction
            q_sol = self._ik_safe(next_pos)
            if q_sol is None:
                break
            q_clipped = self._arm_ctrl_from_q(q_sol)
            self.set_arm_actuators(q_clipped)
            self.step(1)

            # Check contacts
            has_gripper_contact = False
            unrelated_contact = False
            for ci in range(self.data.ncon):
                c = self.data.contact[ci]
                b1 = self.model.geom_bodyid[c.geom1]
                b2 = self.model.geom_bodyid[c.geom2]
                bodies = {b1, b2}
                robot_involved = bool(bodies & robot_body_ids)
                gripper_involved = bool(bodies & gripper_body_ids)
                if robot_involved and gripper_involved:
                    # Check if the other body is non-robot
                    other = bodies - robot_body_ids
                    if other:
                        has_gripper_contact = True
                elif robot_involved:
                    # arm contact with non-robot body
                    other = bodies - robot_body_ids
                    if other:
                        unrelated_contact = True

            if unrelated_contact:
                return {"success": False, "error": "Unrelated contact detected"}

            if has_gripper_contact:
                contact_detected = True
                if contact_hold_start is None:
                    contact_hold_start = self.data.time
                # Stop advancing - hold current target
                if self.data.time - contact_hold_start >= contact_hold_s:
                    # Check post-contact speed
                    ee_vel = float(np.linalg.norm(self.data.site_xvelp[
                        mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "pinch_site")
                    ]))
                    if ee_vel <= 0.02:
                        return {"success": True}
                    # Still moving - keep holding
            else:
                contact_hold_start = None
                travel_so_far += step_dist

        if contact_detected:
            return {"success": True}
        return {"success": False, "error": "No contact within travel/duration limits"}

    # ── A5: move_cartesian_offset_and_return ───────────────────────────────────
    def move_cartesian_offset_and_return(self, request: dict) -> dict:
        """
        Move EE by offset_robot_base_m (robot-base frame = world frame for fixed base),
        hold 0.25 s, then return to start, hold 0.5 s.
        """
        offset = np.asarray(request["offset_robot_base_m"], dtype=float)
        leg_dur = float(request["max_duration_per_leg_s"])
        if offset.shape != (3,) or np.any(np.abs(offset) > 0.06):
            return {"success": False, "error": "offset_robot_base_m out of bounds"}
        if not (0.25 <= leg_dur <= 5.0):
            return {"success": False, "error": "max_duration_per_leg_s out of bounds"}

        # Capture start EE position
        start_pos = self._ee_pos().copy()
        target_pos = start_pos + offset  # base frame == world frame (fixed base)

        tol = 0.015
        outbound_hold = 0.25
        return_hold   = 0.5

        # ── Outbound leg ──────────────────────────────────────────────────────
        deadline_out = self.data.time + leg_dur
        ok_out = self._move_to_xyz(target_pos, deadline_out, outbound_hold, tol)
        if not ok_out:
            return {"success": False, "error": "Outbound leg failed"}

        # ── Return leg ────────────────────────────────────────────────────────
        deadline_ret = self.data.time + leg_dur
        ok_ret = self._move_to_xyz(start_pos, deadline_ret, return_hold, tol)
        if not ok_ret:
            return {"success": False, "error": "Return leg failed"}

        return {"success": True}


# ── Module-level build() ───────────────────────────────────────────────────────
def build() -> Robot:
    spec = ArmSpec(
        ee_site_name="pinch_site",
        ee_body_name="robotiq_base",
        arm_joint_names=[
            "shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint",
            "wrist_1_joint", "wrist_2_joint", "wrist_3_joint",
        ],
        arm_actuator_names=[
            "shoulder_pan", "shoulder_lift", "elbow",
            "wrist_1", "wrist_2", "wrist_3",
        ],
        joint_limits=_ARM_JOINT_LIMITS,
        home_qpos=_HOME_QPOS,
        ik_damping=0.005,
        ik_max_iter=50,
        ik_tolerance=0.001,
        ik_step_clamp=0.15,
        ik_raise_on_unreachable=False,
        gripper_actuator_names=["fingers_actuator"],
        gripper_joint_names=["right_driver_joint", "left_driver_joint"],
        gripper_close_ctrl=_GRIPPER_CLOSE_CTRL,
        gripper_open_ctrl=_GRIPPER_OPEN_CTRL,
        gripper_settle_steps=200,
        sim_dt=0.002,
    )
    return Robot.from_mjcf("mjcf.xml", spec=spec)
