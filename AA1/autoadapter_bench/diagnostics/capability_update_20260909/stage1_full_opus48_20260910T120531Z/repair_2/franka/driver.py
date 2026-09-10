"""Generated capability driver for the Franka Panda (franka).

Subclasses ArmSerialDLSSkeleton (DLS IK + actuator-space interp) and
implements the public capability-v2 contract methods A1..A5. All actions
advance the same MuJoCo model/data through native actuator commands; live
qpos/qvel are never set to achieve an action. Deadlines and holds are
measured with data.time (simulation seconds).

Closed-loop Cartesian regulation uses per-step DLS IK toward a target that
is augmented with a proportional feedforward term plus an integral term on
the measured end-effector error. The feedforward overdrives the native
position-PD actuators so the EE converges quickly enough to satisfy the
short per-leg deadlines, while the integral cancels steady-state droop.
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
GRIPPER_ACTUATORS = ["actuator8"]     # ctrlrange 0..255 (0=closed,255=open)
GRIPPER_JOINTS = ["finger_joint1", "finger_joint2"]
BASE_BODY = "link0"
TARGET_GEOM = "fixed_contact_geom"
TOOL_BODIES = ("hand", "left_finger", "right_finger")


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
        self._target_gid = int(self._mj.mj_name2id(
            self.model, self._mj.mjtObj.mjOBJ_GEOM, TARGET_GEOM))
        self._tool_gids = self._collect_tool_geoms()
        self._probe_gripper()

    # ---------- binding / probing helpers ----------
    def _arm_actuator_id_of(self, name):
        return int(self._mj.mj_name2id(
            self.model, self._mj.mjtObj.mjOBJ_ACTUATOR, name))

    def _collect_tool_geoms(self):
        gids = set()
        for bn in TOOL_BODIES:
            bid = int(self._mj.mj_name2id(
                self.model, self._mj.mjtObj.mjOBJ_BODY, bn))
            if bid < 0:
                continue
            start = int(self.model.body_geomadr[bid])
            n = int(self.model.body_geomnum[bid])
            for g in range(start, start + n):
                gids.add(int(g))
        return gids

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

    def _ee_pos(self):
        pos, _ = self.get_ee_pose()
        return np.asarray(pos, dtype=np.float64)

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
        ik_damping=0.02,
        ik_max_iter=120,
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
# Request validation helpers
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


# ============================================================================
# Closed-loop Cartesian servo: proportional feedforward + integral term.
# ============================================================================

def _cart_step(self, target, integ, hold_grip_ctrl,
               kff=2.0, ki=3.0, imax=0.1):
    """One control tick. The commanded EE target is augmented with a
    proportional feedforward term (overdriving the slow position-PD
    actuators) and an integral term (removing steady-state droop). Solve
    DLS IK from the current joint state, command native actuators, and step
    physics once. Returns (ee_error_norm, updated_integral)."""
    dt = self._dt()
    ee = self._ee_pos()
    err = target - ee
    integ = np.clip(integ + err * dt, -imax, imax)
    aug = target + kff * err + ki * integ
    q_now = self.get_joint_positions()
    try:
        q_goal = self.ik(aug, q_init=q_now, raise_on_unreachable=False)
    except IKUnreachableError as e:
        q_goal = e.q_final
    if hold_grip_ctrl is not None:
        self.set_gripper_control(hold_grip_ctrl)
    self.set_arm_actuators(q_goal)
    self.step(1)
    return float(np.linalg.norm(self._ee_pos() - target)), integ


def _regulate_to_xyz(self, cap, target, deadline_time, hold_s, tol,
                     hold_grip_ctrl, hold_margin_s=0.06):
    """Regulate the EE to `target` until the error stays <= tol for a
    continuous window of (hold_s + hold_margin_s) sim-seconds, or until
    data.time exceeds deadline_time. The extra margin guarantees the
    validator observes a full hold_s continuous hold despite discrete-step
    truncation. Returns (ok, final_error). Never teleports."""
    dt = self._dt()
    need = hold_s + (hold_margin_s if hold_s > 0.0 else 0.0)
    hold_accum = 0.0
    integ = np.zeros(3)
    while self.data.time < deadline_time:
        err, integ = _cart_step(self, target, integ, hold_grip_ctrl)
        if err <= tol:
            hold_accum += dt
            if hold_accum >= need:
                return True, err
        else:
            hold_accum = 0.0
    err = float(np.linalg.norm(self._ee_pos() - target))
    if hold_s <= 0.0:
        return err <= tol, err
    return (err <= tol and hold_accum >= hold_s), err


Robot._cart_step = _cart_step
Robot._regulate_to_xyz = _regulate_to_xyz


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
                               hold_s=0.5, tol=0.010, hold_grip_ctrl=grip)
    if not ok:
        self.set_arm_actuators(self.get_joint_positions())
        raise CapabilityError(cap, "not_reached",
                              f"final error {err:.4f} m within {max_dur:.2f} s")
    return {"capability_id": cap, "status": "ok",
            "final_position_error_m": err,
            "ee_position_m": self._ee_pos().tolist()}


Robot.move_end_effector_to_position = move_end_effector_to_position


# ============================================================================
# A2: trace_cartesian_path
# ============================================================================

def _trace_segment(self, cap, p0, p1, deadline, hold_s, grip):
    """March a moving setpoint from p0 to p1 (bounding cross-track), then
    hold the terminal point. Uses feedforward Cartesian feedback so the
    servo tracks the setpoint tightly. Returns (ok, terminal_err, max_cross)."""
    dt = self._dt()
    seg = p1 - p0
    seg_len = float(np.linalg.norm(seg))
    seg_dir = seg / seg_len if seg_len > 1e-9 else seg
    max_cross = 0.0
    hold_accum = 0.0
    need = hold_s + (0.06 if hold_s > 0.0 else 0.0)
    integ = np.zeros(3)
    avail = max(deadline - self.data.time, dt)
    march_time = max(dt, avail - hold_s - 0.12)
    march_speed = seg_len / march_time if seg_len > 0 else 0.0
    s = 0.0
    while self.data.time < deadline:
        s = min(seg_len, s + march_speed * dt)
        setpoint = p0 + seg_dir * s if seg_len > 1e-9 else p1
        _, integ = _cart_step(self, setpoint, integ, grip)
        ee = self._ee_pos()
        if seg_len > 1e-9:
            proj = np.dot(ee - p0, seg_dir)
            closest = p0 + seg_dir * np.clip(proj, 0.0, seg_len)
            max_cross = max(max_cross, float(np.linalg.norm(ee - closest)))
        term_err = float(np.linalg.norm(ee - p1))
        if s >= seg_len - 1e-9 and term_err <= 0.010:
            hold_accum += dt
            if hold_accum >= need:
                return True, term_err, max_cross
        else:
            hold_accum = 0.0
    term_err = float(np.linalg.norm(self._ee_pos() - p1))
    ok = term_err <= 0.012 and (hold_s == 0.0 or hold_accum >= hold_s)
    return ok, term_err, max_cross


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
    prev = self._ee_pos().copy()
    err = 0.0
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


Robot._trace_segment = _trace_segment
Robot.trace_cartesian_path = trace_cartesian_path


# ============================================================================
# A3: set_gripper_opening
# ============================================================================

def set_gripper_opening(self, request):
    cap = "A3"
    self._check_finite_state()
    frac = _num(request["opening_fraction"], cap, "opening_fraction", 0.0, 1.0)
    max_dur = _num(request["max_duration_s"], cap, "max_duration_s", 0.25, 8.0)
    # Freeze the active arm pose target so the arm target is not changed.
    q_hold = self.get_joint_positions().copy()
    target_ctrl = self._ctrl_for_fraction(frac)
    dt = self._dt()
    deadline = self.data.time + max_dur
    # Require a comfortably longer continuous hold than the 0.25 s criterion
    # so the validator observes a full, untruncated hold window.
    need = min(0.25 + 0.20, max(0.25, max_dur - dt))
    hold_accum = 0.0
    cur = self._current_aperture_fraction()
    reached_once = False
    while self.data.time < deadline:
        self.set_arm_actuators(q_hold)
        self.set_gripper_control(target_ctrl)
        self.step(1)
        cur = self._current_aperture_fraction()
        e = abs(cur - frac)
        if e <= 0.1:
            reached_once = True
            hold_accum += dt
            if hold_accum >= need:
                return {"capability_id": cap, "status": "ok",
                        "aperture_fraction": cur, "aperture_error": e}
        else:
            hold_accum = 0.0
    e = abs(cur - frac)
    if reached_once and e <= 0.1 and hold_accum >= 0.25:
        return {"capability_id": cap, "status": "ok",
                "aperture_fraction": cur, "aperture_error": e}
    raise CapabilityError(cap, "aperture_not_reached",
                          f"frac {cur:.3f} vs target {frac:.3f}")


Robot.set_gripper_opening = set_gripper_opening


# ============================================================================
# EE velocity + contact helpers
# ============================================================================

def get_ee_velocity(self):
    jacp = np.zeros((3, self.model.nv), dtype=np.float64)
    self._ee_jac_pos(jacp, self.data)
    return jacp @ self.data.qvel


def _target_contact(self):
    """Return (n_target_contacts, max_penetration_m) between tool geoms and
    the fixed contact target geom, using live data.contact."""
    ntc = 0
    pen = 0.0
    for i in range(int(self.data.ncon)):
        c = self.data.contact[i]
        g1, g2 = int(c.geom1), int(c.geom2)
        if not (g1 == self._target_gid or g2 == self._target_gid):
            continue
        other = g2 if g1 == self._target_gid else g1
        if other in self._tool_gids:
            ntc += 1
            if c.dist < 0:
                pen = max(pen, -float(c.dist))
    return ntc, pen


def _unrelated_contact(self):
    """Count tool contacts against something that is not the target geom."""
    cnt = 0
    for i in range(int(self.data.ncon)):
        c = self.data.contact[i]
        g1, g2 = int(c.geom1), int(c.geom2)
        if (g1 in self._tool_gids) ^ (g2 in self._tool_gids):
            other = g2 if g1 in self._tool_gids else g1
            if other != self._target_gid:
                cnt += 1
    return cnt


Robot.get_ee_velocity = get_ee_velocity
Robot._target_contact = _target_contact
Robot._unrelated_contact = _unrelated_contact


# ============================================================================
# A4: approach_until_contact
# ============================================================================

def approach_until_contact(self, request):
    cap = "A4"
    self._check_finite_state()
    pre = _as_xyz(request["precontact_position_m"], cap, "precontact_position_m")
    if np.any(pre < -1.0) or np.any(pre > 1.0):
        raise CapabilityError(cap, "bad_request", "precontact out of [-1,1]")
    dvec = _as_xyz(request["approach_direction_unit"], cap,
                   "approach_direction_unit")
    dn = float(np.linalg.norm(dvec))
    if dn < 1e-6:
        raise CapabilityError(cap, "bad_request", "approach_direction zero")
    dvec = dvec / dn
    max_travel = _num(request["max_travel_m"], cap, "max_travel_m",
                      hi=0.08, excl_lo=0.0)
    max_speed = _num(request["max_approach_speed_m_s"], cap,
                     "max_approach_speed_m_s", hi=0.05, excl_lo=0.0)
    max_dur = _num(request["max_duration_s"], cap, "max_duration_s", 0.25, 8.0)
    grip = float(self.data.ctrl[self._grip_aid])
    deadline = self.data.time + max_dur
    dt = self._dt()

    # Phase 1: reach precontact pose (reserve most time for the approach).
    pre_deadline = min(deadline, self.data.time + max(0.25, max_dur * 0.35))
    ok, err = _regulate_to_xyz(self, cap, pre, pre_deadline,
                               hold_s=0.0, tol=0.015, hold_grip_ctrl=grip)
    if not ok:
        self.set_arm_actuators(self.get_joint_positions())
        raise CapabilityError(cap, "precontact_unreached",
                              f"precontact err {err:.4f} m")

    # Phase 2: advance a moving setpoint along the ray at bounded speed until
    # controlled contact. After contact, keep commanding a small, bounded
    # press just past the contact point (never re-solving toward free space)
    # so the fingers stay lightly loaded against the target for the hold.
    start = self._ee_pos().copy()
    integ = np.zeros(3)
    setpoint_s = 0.0
    contact_hold = 0.0
    contacted = False
    press_extra = 0.004  # small bounded overshoot to keep light contact
    while self.data.time < deadline:
        if _unrelated_contact(self) > 0:
            self.set_arm_actuators(self.get_joint_positions())
            raise CapabilityError(cap, "unrelated_contact",
                                  "unintended contact during approach")
        ntc, pen = _target_contact(self)
        if ntc > 0 and not contacted:
            contacted = True
            # freeze the setpoint just past the current contact position so
            # the servo maintains a light, bounded load without receding.
            traveled_now = float(np.dot(self._ee_pos() - start, dvec))
            setpoint_s = min(max_travel, traveled_now + press_extra)
        if contacted:
            setpoint = start + dvec * min(max_travel, setpoint_s)
            _, integ = _cart_step(self, setpoint, integ, grip,
                                  kff=0.4, ki=0.4, imax=0.02)
            ntc, pen = _target_contact(self)
            speed = float(np.linalg.norm(get_ee_velocity(self)))
            if ntc > 0 and speed <= 0.02 and pen <= 0.005:
                contact_hold += dt
                if contact_hold >= 0.16:
                    self.set_arm_actuators(self.get_joint_positions())
                    return {"capability_id": cap, "status": "ok",
                            "traveled_m": min(max_travel, setpoint_s),
                            "post_contact_speed_m_s": speed,
                            "penetration_m": pen, "contact_count": ntc}
            else:
                contact_hold = 0.0
                if pen > 0.005 and setpoint_s > 0:
                    setpoint_s = max(0.0, setpoint_s - dvec_step(dt))
            continue
        # not yet contacted: advance the commanded setpoint along the ray
        setpoint_s = min(max_travel, setpoint_s + max_speed * dt)
        setpoint = start + dvec * setpoint_s
        _, integ = _cart_step(self, setpoint, integ, grip)
    self.set_arm_actuators(self.get_joint_positions())
    if contacted:
        raise CapabilityError(cap, "contact_unstable",
                              "contact made but hold criteria unmet")
    raise CapabilityError(cap, "no_contact",
                          "duration expired before controlled contact")


def dvec_step(dt):
    return 0.05 * dt


Robot.approach_until_contact = approach_until_contact


# ============================================================================
# A5: move_cartesian_offset_and_return
# ============================================================================

def _base_rotation(self):
    """World rotation matrix of the robot base body (link0)."""
    self._mj.mj_forward(self.model, self.data)
    bid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_BODY, BASE_BODY)
    if bid < 0:
        return np.eye(3)
    return np.array(self.data.xmat[bid], dtype=np.float64).reshape(3, 3)


def move_cartesian_offset_and_return(self, request):
    cap = "A5"
    self._check_finite_state()
    off = _as_xyz(request["offset_robot_base_m"], cap, "offset_robot_base_m")
    if np.any(off < -0.06) or np.any(off > 0.06):
        raise CapabilityError(cap, "bad_request", "offset out of [-0.06,0.06]")
    leg_dur = _num(request["max_duration_per_leg_s"], cap,
                   "max_duration_per_leg_s", 0.25, 5.0)
    grip = float(self.data.ctrl[self._grip_aid])

    # Capture the robot-base frame and start pose at call start.
    R_base = _base_rotation(self)
    start_ee = self._ee_pos().copy()
    offset_world = R_base @ off
    outbound_target = start_ee + offset_world
    off_norm = float(np.linalg.norm(offset_world))

    # Outbound leg: establish requested displacement (hold 0.25 s). Tight
    # tolerance so displacement fraction comfortably exceeds 0.8.
    deadline = self.data.time + leg_dur
    ok, err = _regulate_to_xyz(self, cap, outbound_target, deadline,
                               hold_s=0.25, tol=0.006, hold_grip_ctrl=grip)
    disp = self._ee_pos() - start_ee
    frac = float(np.linalg.norm(disp) / max(off_norm, 1e-9))
    if not ok or frac < 0.8:
        self.set_arm_actuators(self.get_joint_positions())
        raise CapabilityError(cap, "outbound_failed",
                              f"err {err:.4f} m frac {frac:.2f}")

    # Return leg only after the outbound leg has completed (hold 0.5 s).
    deadline = self.data.time + leg_dur
    ok, err = _regulate_to_xyz(self, cap, start_ee, deadline,
                               hold_s=0.5, tol=0.008, hold_grip_ctrl=grip)
    if not ok:
        self.set_arm_actuators(self.get_joint_positions())
        raise CapabilityError(cap, "return_failed", f"return err {err:.4f} m")
    return {"capability_id": cap, "status": "ok",
            "outbound_displacement_fraction": frac,
            "return_position_error_m": err}


Robot._base_rotation = _base_rotation
Robot.move_cartesian_offset_and_return = move_cartesian_offset_and_return
