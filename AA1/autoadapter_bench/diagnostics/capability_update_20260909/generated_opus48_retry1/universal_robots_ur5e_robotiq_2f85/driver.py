# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for universal_robots_ur5e_robotiq_2f85.

UR5e 6-DoF serial arm + Robotiq 2F-85 parallel gripper (single tendon
actuator).  Built on ArmSerialDLSSkeleton (DLS IK + actuator-space interp).
The skeleton supplies low-level FK/IK and interpolated joint moves; every
public capability (A1..A5) is implemented here with request handling,
closed-loop feedback, ordering, holds and bounded failure, all measured in
simulation seconds via data.time.
"""
from __future__ import annotations

import os

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton, IKUnreachableError
from auto_adapter.skeletons.base import ArmSpec

_ARM_JOINTS = [
    "shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint",
    "wrist_1_joint", "wrist_2_joint", "wrist_3_joint",
]
_ARM_ACTS = ["shoulder_pan", "shoulder_lift", "elbow", "wrist_1", "wrist_2", "wrist_3"]

# Robotiq 2F-85: measured ctrl 0 -> driver joint ~0 (open), ctrl 255 -> ~0.782
# rad (closed).  Aperture fraction 1.0 == fully open == ctrl 0.
_GRIP_CTRL_MIN = 0.0     # ctrl for fully OPEN
_GRIP_CTRL_MAX = 255.0   # ctrl for fully CLOSED
_GRIP_JOINT = "right_driver_joint"
_GRIP_JOINT_OPEN = 0.0
_GRIP_JOINT_CLOSED = 0.7822


class CapabilityError(RuntimeError):
    """Bounded capability failure raised when a request cannot be completed."""


class Robot(ArmSerialDLSSkeleton):
    """UR5e + Robotiq 2F-85 capability robot."""

    def __init__(self, model, data, spec: ArmSpec) -> None:
        super().__init__(model, data, spec)
        mj = self._mj
        self._contact_geom = mj.mj_name2id(
            model, mj.mjtObj.mjOBJ_GEOM, "fixed_contact_geom")
        self._floor_geom = mj.mj_name2id(model, mj.mjtObj.mjOBJ_GEOM, "fixed_floor")
        self._grip_joint_id = mj.mj_name2id(model, mj.mjtObj.mjOBJ_JOINT, _GRIP_JOINT)
        self._grip_qadr = int(model.jnt_qposadr[self._grip_joint_id])
        if model.nkey > 0:
            mj.mj_resetDataKeyframe(model, data, 0)
            mj.mj_forward(model, data)
            self.set_arm_actuators(self.get_joint_positions())

    # ----- helpers ------------------------------------------------------
    @property
    def dt(self) -> float:
        return float(self.model.opt.timestep)

    def _ee(self) -> np.ndarray:
        self._mj.mj_forward(self.model, self.data)
        return self._ee_pos_now()

    def _check_finite_state(self) -> None:
        q = self.get_joint_positions()
        v = self.get_joint_velocities()
        if not (np.all(np.isfinite(q)) and np.all(np.isfinite(v))):
            raise CapabilityError("robot state is not finite")

    def _gripper_fraction(self) -> float:
        """Current opening fraction in [0,1] (1 == fully open)."""
        val = float(self.data.qpos[self._grip_qadr])
        closed = (val - _GRIP_JOINT_OPEN) / (_GRIP_JOINT_CLOSED - _GRIP_JOINT_OPEN)
        return float(np.clip(1.0 - closed, 0.0, 1.0))

    def _fraction_to_ctrl(self, opening_fraction: float) -> float:
        closed = 1.0 - float(np.clip(opening_fraction, 0.0, 1.0))
        return _GRIP_CTRL_MIN + closed * (_GRIP_CTRL_MAX - _GRIP_CTRL_MIN)

    def _stop_arm(self) -> None:
        """Hold the arm at its current measured configuration (stop motion)."""
        self.set_arm_actuators(self.get_joint_positions())

    # ----- core closed-loop regulation --------------------------------
    def _regulate_to_xyz(self, target, deadline_t, hold_s, tol, track_cb=None):
        """Drive EE to `target`; require err<=tol continuously for hold_s
        sim-seconds before deadline_t.  Returns (ok, final_err).  Re-solves
        IK from the live config each step so the servo tracks the Cartesian
        goal.  Advances real physics only; never teleports live state."""
        target = np.asarray(target, dtype=np.float64).reshape(3)
        hold_start = None
        try:
            q_goal = self.ik(target, raise_on_unreachable=True)
        except IKUnreachableError as e:
            q_goal = e.q_final
        while self.data.time < deadline_t:
            self.set_arm_actuators(q_goal)
            self.step(1)
            err = float(np.linalg.norm(self._ee() - target))
            if track_cb is not None:
                track_cb(err)
            if err <= tol:
                if hold_start is None:
                    hold_start = self.data.time
                elif (self.data.time - hold_start) >= hold_s:
                    return True, err
            else:
                hold_start = None
                try:
                    q_goal = self.ik(target, q_init=self.get_joint_positions(),
                                     raise_on_unreachable=False)
                except IKUnreachableError as e:
                    q_goal = e.q_final
        final_err = float(np.linalg.norm(self._ee() - target))
        return (final_err <= tol), final_err

    # ===== A1: move_end_effector_to_position ==========================
    def move_end_effector_to_position(self, request):
        self._check_finite_state()
        target = np.asarray(request["target_position_m"], dtype=np.float64).reshape(3)
        max_dur = float(request["max_duration_s"])
        if not np.all(np.isfinite(target)):
            raise CapabilityError("A1: non-finite target")
        deadline = self.data.time + max_dur
        ok, err = self._regulate_to_xyz(target, deadline, hold_s=0.5, tol=0.015)
        if not ok:
            self._stop_arm()
            raise CapabilityError(
                f"A1: could not regulate within {max_dur}s (err={err:.4f}m)")
        self._stop_arm()
        return {"ok": True, "final_error_m": err}

    # ===== A2: trace_cartesian_path ===================================
    def trace_cartesian_path(self, request):
        self._check_finite_state()
        wps = [np.asarray(w, dtype=np.float64).reshape(3)
               for w in request["waypoints_m"]]
        seg_dur = float(request["max_duration_per_segment_s"])
        if not all(np.all(np.isfinite(w)) for w in wps):
            raise CapabilityError("A2: non-finite waypoint")
        results = []
        for i, wp in enumerate(wps):
            is_terminal = (i == len(wps) - 1)
            hold_s = 0.5 if is_terminal else 0.0
            tol = 0.015 if is_terminal else 0.02
            deadline = self.data.time + seg_dur
            ct = {"v": 0.0}

            def _cb(err, _s=ct):
                if err > _s["v"]:
                    _s["v"] = err
            ok, err = self._regulate_to_xyz(wp, deadline, hold_s=hold_s,
                                            tol=tol, track_cb=_cb)
            results.append({"waypoint": i, "err": err, "cross_track": ct["v"]})
            if not ok:
                self._stop_arm()
                raise CapabilityError(
                    f"A2: waypoint {i} not reached in {seg_dur}s (err={err:.4f}m)")
        self._stop_arm()
        return {"ok": True, "waypoints": results}

    # ===== A3: set_gripper_opening ====================================
    def set_gripper_opening(self, request):
        self._check_finite_state()
        frac = float(request["opening_fraction"])
        max_dur = float(request["max_duration_s"])
        if not np.isfinite(frac):
            raise CapabilityError("A3: non-finite opening_fraction")
        frac = float(np.clip(frac, 0.0, 1.0))
        self._stop_arm()  # freeze arm target; do not change arm pose
        ctrl = self._fraction_to_ctrl(frac)
        deadline = self.data.time + max_dur
        hold_start = None
        tol = 0.10
        while self.data.time < deadline:
            self.set_gripper_control(ctrl)
            self._stop_arm()
            self.step(1)
            cur = self._gripper_fraction()
            if abs(cur - frac) <= tol:
                if hold_start is None:
                    hold_start = self.data.time
                elif (self.data.time - hold_start) >= 0.25:
                    return {"ok": True, "final_fraction": cur}
            else:
                hold_start = None
        cur = self._gripper_fraction()
        if abs(cur - frac) > tol:
            self.set_gripper_control(self._fraction_to_ctrl(cur))
            raise CapabilityError(
                f"A3: aperture not reached (want={frac:.3f} got={cur:.3f})")
        return {"ok": True, "final_fraction": cur}

    # ----- contact helpers --------------------------------------------
    def _target_contact(self) -> bool:
        d = self.data
        for i in range(d.ncon):
            c = d.contact[i]
            if self._contact_geom in (c.geom1, c.geom2):
                return True
        return False

    def _penetration(self) -> float:
        d = self.data
        pen = 0.0
        for i in range(d.ncon):
            c = d.contact[i]
            if self._contact_geom in (c.geom1, c.geom2):
                pen = max(pen, max(0.0, -float(c.dist)))
        return pen

    def _unrelated_contacts(self) -> int:
        d = self.data
        cnt = 0
        for i in range(d.ncon):
            c = d.contact[i]
            if self._contact_geom in (c.geom1, c.geom2):
                continue
            if self._floor_geom in (c.geom1, c.geom2):
                continue
            cnt += 1
        return cnt

    # ===== A4: approach_until_contact =================================
    def approach_until_contact(self, request):
        self._check_finite_state()
        pre = np.asarray(request["precontact_position_m"], dtype=np.float64).reshape(3)
        direction = np.asarray(request["approach_direction_unit"], dtype=np.float64).reshape(3)
        max_travel = float(request["max_travel_m"])
        max_speed = float(request["max_approach_speed_m_s"])
        max_dur = float(request["max_duration_s"])
        nrm = float(np.linalg.norm(direction))
        if nrm < 1e-9 or not np.all(np.isfinite(direction)):
            raise CapabilityError("A4: invalid approach direction")
        direction = direction / nrm
        base_unrelated = self._unrelated_contacts()
        deadline = self.data.time + max_dur
        # Phase 1: reach precontact pose
        ok, err = self._regulate_to_xyz(pre, deadline, hold_s=0.0, tol=0.015)
        if not ok:
            self._stop_arm()
            raise CapabilityError(f"A4: could not reach precontact (err={err:.4f}m)")
        # Phase 2: advance along ray at bounded speed until controlled contact
        start = self._ee()
        step_dist = max_speed * self.dt
        travelled = 0.0
        contact_hold_start = None
        while self.data.time < deadline:
            if self._target_contact():
                self._stop_arm()  # stop advancing; hold against target
                self.step(1)
                if self._unrelated_contacts() > base_unrelated:
                    self._stop_arm()
                    raise CapabilityError("A4: unrelated contact created")
                if contact_hold_start is None:
                    contact_hold_start = self.data.time
                if (self.data.time - contact_hold_start) >= 0.1:
                    return {"ok": True, "penetration_m": self._penetration()}
                continue
            if travelled >= max_travel:
                break
            travelled += step_dist
            goal = start + direction * min(travelled, max_travel)
            try:
                q_goal = self.ik(goal, q_init=self.get_joint_positions(),
                                 raise_on_unreachable=False)
            except IKUnreachableError as e:
                q_goal = e.q_final
            self.set_arm_actuators(q_goal)
            self.step(1)
            if self._unrelated_contacts() > base_unrelated and not self._target_contact():
                self._stop_arm()
                raise CapabilityError("A4: unrelated contact created")
        self._stop_arm()
        if self._target_contact():
            return {"ok": True, "penetration_m": self._penetration()}
        raise CapabilityError("A4: no contact within travel/duration bounds")

    # ===== A5: move_cartesian_offset_and_return =======================
    def move_cartesian_offset_and_return(self, request):
        self._check_finite_state()
        offset = np.asarray(request["offset_robot_base_m"], dtype=np.float64).reshape(3)
        leg_dur = float(request["max_duration_per_leg_s"])
        if not np.all(np.isfinite(offset)):
            raise CapabilityError("A5: non-finite offset")
        # Robot base body 'base' sits at world origin with identity
        # orientation (probed from MJCF), so base-frame offset == world offset.
        start_pose = self._ee().copy()
        outbound = start_pose + offset
        # Outbound leg (must complete before return leg starts).
        deadline = self.data.time + leg_dur
        ok, err = self._regulate_to_xyz(outbound, deadline, hold_s=0.25, tol=0.015)
        disp = float(np.linalg.norm(self._ee() - start_pose))
        req_disp = float(np.linalg.norm(offset))
        if not ok or (req_disp > 1e-9 and disp < 0.8 * req_disp):
            self._stop_arm()
            raise CapabilityError(
                f"A5: outbound leg failed (err={err:.4f}m disp={disp:.4f})")
        # Return leg back to captured start pose.
        deadline = self.data.time + leg_dur
        ok, err = self._regulate_to_xyz(start_pose, deadline, hold_s=0.5, tol=0.015)
        if not ok:
            self._stop_arm()
            raise CapabilityError(f"A5: return leg failed (err={err:.4f}m)")
        self._stop_arm()
        return {"ok": True, "outbound_displacement_m": disp, "return_error_m": err}


def build() -> Robot:
    spec = ArmSpec(
        ee_site_name="pinch_site",
        ee_body_name="robotiq_base",
        arm_joint_names=list(_ARM_JOINTS),
        arm_actuator_names=list(_ARM_ACTS),
        joint_limits={
            "shoulder_pan_joint": (-6.28319, 6.28319),
            "shoulder_lift_joint": (-6.28319, 6.28319),
            "elbow_joint": (-3.1415, 3.1415),
            "wrist_1_joint": (-6.28319, 6.28319),
            "wrist_2_joint": (-6.28319, 6.28319),
            "wrist_3_joint": (-6.28319, 6.28319),
        },
        home_qpos=[-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0],
        ik_damping=0.02,
        ik_max_iter=200,
        ik_tolerance=0.002,
        ik_step_clamp=0.3,
        ik_raise_on_unreachable=True,
        gripper_actuator_names=["fingers_actuator"],
        gripper_joint_names=[_GRIP_JOINT],
        grasp_backend="noop",
        sim_dt=0.002,
    )
    mjcf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mjcf.xml")
    return Robot.from_mjcf(mjcf, spec=spec)
