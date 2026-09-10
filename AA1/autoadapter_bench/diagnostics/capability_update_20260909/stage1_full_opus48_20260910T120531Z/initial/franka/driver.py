"""Generated capability driver for the Franka Panda (franka).

Subclasses ArmSerialDLSSkeleton (DLS IK + actuator-space interp) and
implements the public capability-v2 contract methods A1..A5. All actions
advance the same MuJoCo model/data through native actuator commands; live
qpos/qvel are never set to achieve an action. Deadlines and holds are
measured with data.time (simulation seconds).
"""
from __future__ import annotations

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import (
    ArmSerialDLSSkeleton,
    IKUnreachableError,
)
from auto_adapter.skeletons.base import ArmSpec


# ---- Robot-specific bindings derived from panda_fixed.xml / study.json ----
ARM_JOINTS = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "joint7"]
ARM_ACTUATORS = ["actuator1", "actuator2", "actuator3", "actuator4",
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
HOME_QPOS = [0.0, 0.0, 0.0, -1.57079, 0.0, 1.57079, -0.7853]
GRIPPER_ACTUATORS = ["actuator8"]     # drives tendon 'split', ctrlrange 0..255
GRIPPER_JOINTS = ["finger_joint1", "finger_joint2"]


class CapabilityError(RuntimeError):
    """Bounded capability failure with a machine-readable code."""

    def __init__(self, capability_id, code, detail):
        super().__init__(f"{capability_id}:{code}: {detail}")
        self.capability_id = capability_id
        self.code = code
        self.detail = detail


class Robot(ArmSerialDLSSkeleton):
    """Franka Panda capability robot."""

    def __init__(self, model, data, spec):
        super().__init__(model, data, spec)
        import mujoco
        self._scratch = mujoco.MjData(self.model)
        self._grip_aid = self._arm_actuator_id_of(GRIPPER_ACTUATORS[0])
        self._probe_gripper()

    # ---------- binding / probing helpers ----------
    def _arm_actuator_id_of(self, name):
        return int(self._mj.mj_name2id(
            self.model, self._mj.mjtObj.mjOBJ_ACTUATOR, name))

    def _gripper_ctrl_range(self):
        cr = self.model.actuator_ctrlrange[self._grip_aid]
        return float(cr[0]), float(cr[1])

    def _aperture_from_data(self, d):
        s = 0.0
        for jn in GRIPPER_JOINTS:
            jid = self.model.joint(jn).id
            adr = self.model.jnt_qposadr[jid]
            s += float(d.qpos[adr])
        return s

    def _aperture_for_ctrl(self, ctrl_value):
        """Kinematic settle of gripper on scratch data to read aperture."""
        d = self._scratch
        self._mj.mj_copyData(d, self.model, self.data)
        d.ctrl[self._grip_aid] = float(ctrl_value)
        for _ in range(600):
            self._mj.mj_step(self.model, d)
        return self._aperture_from_data(d)

    def _probe_gripper(self):
        lo, hi = self._gripper_ctrl_range()
        ap_hi = self._aperture_for_ctrl(hi)
        ap_lo = self._aperture_for_ctrl(lo)
        if ap_hi >= ap_lo:
            self._open_ctrl, self._close_ctrl = hi, lo
            self._ap_open, self._ap_close = ap_hi, ap_lo
        else:
            self._open_ctrl, self._close_ctrl = lo, hi
            self._ap_open, self._ap_close = ap_lo, ap_hi
        self._ap_span = max(abs(self._ap_open - self._ap_close), 1e-9)

    # ---------- shared helpers used by capability methods ----------
    def _ctrl_for_fraction(self, frac):
        frac = float(np.clip(frac, 0.0, 1.0))
        return self._close_ctrl + frac * (self._open_ctrl - self._close_ctrl)

    def _current_aperture_fraction(self):
        ap = self._aperture_from_data(self.data)
        return float(np.clip((ap - self._ap_close) / self._ap_span, 0.0, 1.0))

    def _dt(self):
        return float(self.model.opt.timestep)

    def _check_finite_state(self):
        q = self.get_joint_positions()
        v = self.get_joint_velocities()
        if not (np.all(np.isfinite(q)) and np.all(np.isfinite(v))):
            raise CapabilityError("state", "nonfinite_state",
                                  "joint state not finite")


def build():
    spec = ArmSpec(
        ee_site_name="fixed_tcp",
        ee_body_name="hand",
        arm_joint_names=ARM_JOINTS,
        arm_actuator_names=ARM_ACTUATORS,
        joint_limits=JOINT_LIMITS,
        home_qpos=HOME_QPOS,
        ik_damping=5e-3,
        ik_max_iter=200,
        ik_tolerance=5e-4,
        ik_step_clamp=0.3,
        ik_raise_on_unreachable=True,
        gripper_actuator_names=GRIPPER_ACTUATORS,
        gripper_joint_names=GRIPPER_JOINTS,
        grasp_backend="noop",
        sim_dt=0.002,
    )
    return Robot.from_mjcf("mjcf.xml", spec=spec)


# ============================================================================
# Shared closed-loop regulation primitives (measured in data.time seconds)
# ============================================================================

def _as_xyz(seq, cap, field):
    a = np.asarray(seq, dtype=np.float64).reshape(-1)
    if a.shape != (3,) or not np.all(np.isfinite(a)):
        raise CapabilityError(cap, "bad_request", f"{field} must be finite 3-vector")
    return a


def _num(v, cap, field, lo=None, hi=None, excl_lo=None):
    x = float(v)
    if not np.isfinite(x):
        raise CapabilityError(cap, "bad_request", f"{field} not finite")
    if lo is not None and x < lo:
        raise CapabilityError(cap, "bad_request", f"{field} < {lo}")
    if hi is not None and x > hi:
        raise CapabilityError(cap, "bad_request", f"{field} > {hi}")
    if excl_lo is not None and x <= excl_lo:
        raise CapabilityError(cap, "bad_request", f"{field} must be > {excl_lo}")
    return x


class _RobotCaps:
    pass


def _regulate_to_xyz(self, cap, target, deadline_time, hold_s, tol,
                     hold_grip_ctrl):
    """Drive the arm toward `target` (world xyz) with continuous position
    servo, holding gripper ctrl fixed, until EE error stays <= tol for
    hold_s continuous sim-seconds, or data.time exceeds deadline_time.
    Returns (ok, final_error). Never teleports."""
    dt = self._dt()
    hold_accum = 0.0
    # Command joints toward IK target incrementally each control tick so the
    # underlying PD servo tracks smoothly.
    while self.data.time < deadline_time:
        if hold_grip_ctrl is not None:
            self.set_gripper_control(hold_grip_ctrl)
        q_now = self.get_joint_positions()
        try:
            q_goal = self.ik(target, q_init=q_now, raise_on_unreachable=False)
        except IKUnreachableError as e:
            q_goal = e.q_final
        self.set_arm_actuators(q_goal)
        self.step(1)
        err = float(np.linalg.norm(self._ee_pos_now() - target))
        if err <= tol:
            hold_accum += dt
            if hold_accum >= hold_s:
                return True, err
        else:
            hold_accum = 0.0
    err = float(np.linalg.norm(self._ee_pos_now() - target))
    return (err <= tol), err


# ============================================================================
# A1: move_end_effector_to_position
# ============================================================================

def move_end_effector_to_position(self, request):
    cap = "A1"
    self._check_finite_state()
    tgt = _as_xyz(request["target_position_m"], cap, "target_position_m")
    if np.any(tgt < -1.0) or np.any(tgt > 1.0):
        raise CapabilityError(cap, "bad_request", "target out of [-1,1]")
    max_dur = _num(request["max_duration_s"], cap, "max_duration_s", 0.25, 8.0)
    grip = float(self.data.ctrl[self._grip_aid])  # hold aperture fixed
    deadline = self.data.time + max_dur
    ok, err = _regulate_to_xyz(self, cap, tgt, deadline,
                               hold_s=0.5, tol=0.015, hold_grip_ctrl=grip)
    if not ok:
        # Stop active motion: freeze command at current pose.
        self.set_arm_actuators(self.get_joint_positions())
        raise CapabilityError(cap, "not_reached",
                              f"final error {err:.4f} m within {max_dur:.2f} s")
    return {"capability_id": cap, "status": "ok",
            "final_position_error_m": err,
            "ee_position_m": self._ee_pos_now().tolist()}


# ============================================================================
# A2: trace_cartesian_path
# ============================================================================

def trace_cartesian_path(self, request):
    cap = "A2"
    self._check_finite_state()
    wps_raw = request["waypoints_m"]
    if not (2 <= len(wps_raw) <= 8):
        raise CapabilityError(cap, "bad_request", "need 2..8 waypoints")
    wps = [_as_xyz(w, cap, "waypoint") for w in wps_raw]
    for w in wps:
        if np.any(w < -1.0) or np.any(w > 1.0):
            raise CapabilityError(cap, "bad_request", "waypoint out of [-1,1]")
    seg_dur = _num(request["max_duration_per_segment_s"], cap,
                   "max_duration_per_segment_s", 0.25, 5.0)
    grip = float(self.data.ctrl[self._grip_aid])
    max_cross = 0.0
    prev = self._ee_pos_now().copy()
    for i, wp in enumerate(wps):
        terminal = (i == len(wps) - 1)
        hold_s = 0.5 if terminal else 0.0
        deadline = self.data.time + seg_dur
        ok, err, seg_cross = _trace_segment(self, cap, prev, wp, deadline,
                                             hold_s, grip)
        max_cross = max(max_cross, seg_cross)
        if not ok:
            self.set_arm_actuators(self.get_joint_positions())
            raise CapabilityError(cap, "segment_failed",
                                  f"waypoint {i} err {err:.4f} m")
        prev = wp.copy()
    return {"capability_id": cap, "status": "ok",
            "max_cross_track_error_m": max_cross,
            "terminal_position_error_m": err}


def _trace_segment(self, cap, p0, p1, deadline, hold_s, grip):
    """Drive along the p0->p1 line, keeping cross-track error small, then hold
    the terminal point for hold_s. Returns (ok, terminal_err, max_cross)."""
    dt = self._dt()
    seg = p1 - p0
    seg_len = float(np.linalg.norm(seg))
    seg_dir = seg / seg_len if seg_len > 1e-9 else seg
    max_cross = 0.0
    hold_accum = 0.0
    # A moving setpoint marches from p0 to p1 to bound cross-track error.
    # Reserve time for the terminal hold + settle margin so the setpoint
    # arrives well before the deadline.
    avail = max(deadline - self.data.time, dt)
    march_time = max(dt, avail - hold_s - 0.15)
    march_speed = seg_len / march_time if seg_len > 0 else 0.0
    s = 0.0
    while self.data.time < deadline:
        self.set_gripper_control(grip)
        s = min(seg_len, s + march_speed * dt)
        setpoint = p0 + seg_dir * s if seg_len > 1e-9 else p1
        q_now = self.get_joint_positions()
        try:
            q_goal = self.ik(setpoint, q_init=q_now, raise_on_unreachable=False)
        except IKUnreachableError as e:
            q_goal = e.q_final
        self.set_arm_actuators(q_goal)
        self.step(1)
        ee = self._ee_pos_now()
        # cross-track distance to the p0->p1 line
        if seg_len > 1e-9:
            proj = np.dot(ee - p0, seg_dir)
            closest = p0 + seg_dir * np.clip(proj, 0.0, seg_len)
            max_cross = max(max_cross, float(np.linalg.norm(ee - closest)))
        term_err = float(np.linalg.norm(ee - p1))
        if s >= seg_len - 1e-9 and term_err <= 0.015:
            hold_accum += dt
            if hold_accum >= hold_s:
                return True, term_err, max_cross
        else:
            hold_accum = 0.0
    term_err = float(np.linalg.norm(self._ee_pos_now() - p1))
    ok = term_err <= 0.015 and (hold_s == 0.0 or hold_accum >= hold_s)
    return ok, term_err, max_cross


Robot.move_end_effector_to_position = move_end_effector_to_position
Robot.trace_cartesian_path = trace_cartesian_path
Robot._trace_segment = _trace_segment
Robot._regulate_to_xyz = _regulate_to_xyz


# ============================================================================
# A3: set_gripper_opening
# ============================================================================

def set_gripper_opening(self, request):
    cap = "A3"
    self._check_finite_state()
    frac = _num(request["opening_fraction"], cap, "opening_fraction", 0.0, 1.0)
    max_dur = _num(request["max_duration_s"], cap, "max_duration_s", 0.25, 8.0)
    # Freeze the active arm pose target so the arm target is not changed.
    q_hold = self.get_joint_positions()
    self.set_arm_actuators(q_hold)
    target_ctrl = self._ctrl_for_fraction(frac)
    dt = self._dt()
    deadline = self.data.time + max_dur
    hold_accum = 0.0
    while self.data.time < deadline:
        self.set_arm_actuators(q_hold)
        self.set_gripper_control(target_ctrl)
        self.step(1)
        cur = self._current_aperture_fraction()
        if abs(cur - frac) <= 0.1:
            hold_accum += dt
            if hold_accum >= 0.25:
                return {"capability_id": cap, "status": "ok",
                        "aperture_fraction": cur,
                        "aperture_error": abs(cur - frac)}
        else:
            hold_accum = 0.0
    cur = self._current_aperture_fraction()
    if abs(cur - frac) <= 0.1:
        return {"capability_id": cap, "status": "ok",
                "aperture_fraction": cur, "aperture_error": abs(cur - frac)}
    raise CapabilityError(cap, "aperture_not_reached",
                          f"frac {cur:.3f} vs target {frac:.3f}")


# ============================================================================
# A4: approach_until_contact
# ============================================================================

def approach_until_contact(self, request):
    cap = "A4"
    self._check_finite_state()
    pre = _as_xyz(request["precontact_position_m"], cap, "precontact_position_m")
    if np.any(pre < -1.0) or np.any(pre > 1.0):
        raise CapabilityError(cap, "bad_request", "precontact out of [-1,1]")
    d = _as_xyz(request["approach_direction_unit"], cap, "approach_direction_unit")
    dnorm = float(np.linalg.norm(d))
    if dnorm < 1e-6:
        raise CapabilityError(cap, "bad_request", "approach_direction zero")
    d = d / dnorm
    max_travel = _num(request["max_travel_m"], cap, "max_travel_m",
                      hi=0.08, excl_lo=0.0)
    max_speed = _num(request["max_approach_speed_m_s"], cap,
                     "max_approach_speed_m_s", hi=0.05, excl_lo=0.0)
    max_dur = _num(request["max_duration_s"], cap, "max_duration_s", 0.25, 8.0)
    grip = float(self.data.ctrl[self._grip_aid])
    deadline = self.data.time + max_dur

    # baseline unrelated-contact count before we start
    base_contacts = int(self.data.ncon)

    # Phase 1: reach precontact pose.
    ok, err = _regulate_to_xyz(self, cap, pre, min(deadline, self.data.time + max_dur),
                               hold_s=0.05, tol=0.02, hold_grip_ctrl=grip)
    if not ok:
        self.set_arm_actuators(self.get_joint_positions())
        raise CapabilityError(cap, "precontact_unreached",
                              f"precontact err {err:.4f} m")

    # Phase 2: advance along ray at bounded speed until controlled contact.
    dt = self._dt()
    start = self._ee_pos_now().copy()
    traveled = 0.0
    contact_hold = 0.0
    while self.data.time < deadline:
        self.set_gripper_control(grip)
        step_adv = min(max_speed * dt, max_travel - traveled)
        if step_adv <= 0.0:
            self.set_arm_actuators(self.get_joint_positions())
            raise CapabilityError(cap, "travel_exhausted",
                                  "max_travel reached without contact")
        setpoint = start + d * (traveled + step_adv)
        q_now = self.get_joint_positions()
        try:
            q_goal = self.ik(setpoint, q_init=q_now, raise_on_unreachable=False)
        except IKUnreachableError as e:
            q_goal = e.q_final
        self.set_arm_actuators(q_goal)
        self.step(1)
        traveled = float(np.clip(np.dot(self._ee_pos_now() - start, d),
                                 0.0, max_travel))
        speed = float(np.linalg.norm(self.get_ee_velocity()))
        ncon = int(self.data.ncon)
        made_contact = ncon > base_contacts
        if made_contact:
            # stop advancing; hold and verify controlled contact
            self.set_arm_actuators(self.get_joint_positions())
            if speed <= 0.02:
                contact_hold += dt
                if contact_hold >= 0.1:
                    return {"capability_id": cap, "status": "ok",
                            "traveled_m": traveled,
                            "post_contact_speed_m_s": speed,
                            "contact_count": ncon}
            else:
                contact_hold = 0.0
        else:
            contact_hold = 0.0
    self.set_arm_actuators(self.get_joint_positions())
    raise CapabilityError(cap, "no_contact",
                          "duration expired before controlled contact")


Robot.set_gripper_opening = set_gripper_opening
Robot.approach_until_contact = approach_until_contact


# ============================================================================
# EE velocity helper (world-frame linear velocity of the EE reference)
# ============================================================================

def get_ee_velocity(self):
    jacp = np.zeros((3, self.model.nv), dtype=np.float64)
    self._ee_jac_pos(jacp, self.data)
    return jacp @ self.data.qvel


Robot.get_ee_velocity = get_ee_velocity


# ============================================================================
# A5: move_cartesian_offset_and_return
# ============================================================================

def move_cartesian_offset_and_return(self, request):
    cap = "A5"
    self._check_finite_state()
    off = _as_xyz(request["offset_robot_base_m"], cap, "offset_robot_base_m")
    if np.any(off < -0.06) or np.any(off > 0.06):
        raise CapabilityError(cap, "bad_request", "offset out of [-0.06,0.06]")
    leg_dur = _num(request["max_duration_per_leg_s"], cap,
                   "max_duration_per_leg_s", 0.25, 5.0)
    grip = float(self.data.ctrl[self._grip_aid])

    # Capture the robot-base frame at call start. link0 is the base body.
    R_base = self._base_rotation()
    start_ee = self._ee_pos_now().copy()
    offset_world = R_base @ off
    outbound_target = start_ee + offset_world

    # Outbound leg: establish requested displacement (hold 0.25 s).
    deadline = self.data.time + leg_dur
    ok, err = _regulate_to_xyz(self, cap, outbound_target, deadline,
                               hold_s=0.25, tol=0.015, hold_grip_ctrl=grip)
    disp = self._ee_pos_now() - start_ee
    frac = float(np.linalg.norm(disp) / max(np.linalg.norm(offset_world), 1e-9))
    if not ok or frac < 0.8:
        self.set_arm_actuators(self.get_joint_positions())
        raise CapabilityError(cap, "outbound_failed",
                              f"err {err:.4f} m frac {frac:.2f}")

    # Return leg only after outbound completes (hold 0.5 s).
    deadline = self.data.time + leg_dur
    ok, err = _regulate_to_xyz(self, cap, start_ee, deadline,
                               hold_s=0.5, tol=0.015, hold_grip_ctrl=grip)
    if not ok:
        self.set_arm_actuators(self.get_joint_positions())
        raise CapabilityError(cap, "return_failed", f"return err {err:.4f} m")
    return {"capability_id": cap, "status": "ok",
            "outbound_displacement_fraction": frac,
            "return_position_error_m": err}


def _base_rotation(self):
    """World rotation matrix of the robot base body (link0)."""
    self._mj.mj_forward(self.model, self.data)
    bid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_BODY, "link0")
    if bid < 0:
        return np.eye(3)
    return np.array(self.data.xmat[bid], dtype=np.float64).reshape(3, 3)


Robot._base_rotation = _base_rotation
Robot.move_cartesian_offset_and_return = move_cartesian_offset_and_return
