# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for universal_robots_ur5e_robotiq_2f85.

UR5e 6-DoF serial arm + Robotiq 2F-85 tendon gripper. Subclasses
ArmSerialDLSSkeleton (DLS IK + actuator interp) and implements the public
capability contract A1..A5. All observation and actuation share one
MuJoCo model/data; kinematic-only calculations use scratch MjData.

Key control: a Cartesian *integral* servo wraps the inherited DLS IK to
cancel the steady-state gravity droop of the position actuators, so the
settled end-effector truly reaches the requested world target (sub-mm)
instead of sagging ~1.4 cm below the IK setpoint.
"""
from __future__ import annotations

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton, IKUnreachableError
from auto_adapter.skeletons.base import ArmSpec

_ARM_JOINTS = [
    "shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint",
    "wrist_1_joint", "wrist_2_joint", "wrist_3_joint",
]
_ARM_ACTS = ["shoulder_pan", "shoulder_lift", "elbow", "wrist_1", "wrist_2", "wrist_3"]
_JLIM = {
    "shoulder_pan_joint": (-6.28319, 6.28319),
    "shoulder_lift_joint": (-6.28319, 6.28319),
    "elbow_joint": (-3.1415, 3.1415),
    "wrist_1_joint": (-6.28319, 6.28319),
    "wrist_2_joint": (-6.28319, 6.28319),
    "wrist_3_joint": (-6.28319, 6.28319),
}
_HOME = [0.0, -1.5708, 1.5708, -1.5708, -1.5708, 0.0]


class CapabilityError(RuntimeError):
    """Bounded capability failure with a machine-readable code."""

    def __init__(self, code: str, message: str, **info):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.info = info


class Robot(ArmSerialDLSSkeleton):
    """UR5e + Robotiq 2F-85 capability robot."""

    _GRIP_CTRL_OPEN = 0.0
    _GRIP_CTRL_CLOSED = 255.0
    _GRIP_Q_OPEN = 0.0026
    _GRIP_Q_CLOSED = 0.7822

    # Cartesian integral servo gains.
    _KI = 1.2            # integral gain (1/s) on Cartesian residual
    _INTEG_CLAMP = 0.15  # max virtual-target shift (m)

    def __init__(self, model, data, spec):
        super().__init__(model, data, spec)
        mj = self._mj
        m = self.model
        self._contact_body_id = mj.mj_name2id(m, mj.mjtObj.mjOBJ_BODY, "fixed_contact_target")
        self._contact_geom_id = mj.mj_name2id(m, mj.mjtObj.mjOBJ_GEOM, "fixed_contact_geom")
        self._tool_geom_ids = [
            mj.mj_name2id(m, mj.mjtObj.mjOBJ_GEOM, g)
            for g in ("right_pad1", "right_pad2", "left_pad1", "left_pad2")
        ]
        self._tool_geom_ids = [g for g in self._tool_geom_ids if g >= 0]
        self._base_body_id = mj.mj_name2id(m, mj.mjtObj.mjOBJ_BODY, "base")
        self._grip_aid = self._gripper_actuator_ids[0]

    # ---- helpers ----
    def _dt(self):
        return float(self.model.opt.timestep)

    def _ee(self):
        self._mj.mj_forward(self.model, self.data)
        return self._ee_pos_now()

    def _solve_ik(self, target, q_init):
        try:
            return self.ik(np.asarray(target, float), q_init=q_init,
                           raise_on_unreachable=False)
        except IKUnreachableError as e:
            return e.q_final

    def _check_finite_state(self):
        q = self.get_joint_positions()
        if not np.all(np.isfinite(q)):
            raise CapabilityError("state_unavailable", "joint state not finite")

    @staticmethod
    def _req_arr(request, key, n):
        v = np.asarray(request[key], dtype=float).reshape(-1)
        if v.shape[0] != n or not np.all(np.isfinite(v)):
            raise CapabilityError("bad_request", f"{key} must have {n} finite items")
        return v

    # ─────────────────────────────────────────────────────────────────────
    # Cartesian integral servo: drive the *settled* end-effector to `target`
    # by shifting a virtual IK target with an integral of the measured
    # Cartesian residual. Cancels position-actuator gravity droop.
    # ─────────────────────────────────────────────────────────────────────
    def _regulate_to(self, target, deadline_s, tol, hold_s, hold_grip=None,
                     integ0=None):
        dt = self._dt()
        target = np.asarray(target, float)
        t0 = float(self.data.time)
        integ = np.zeros(3) if integ0 is None else np.array(integ0, float)
        q = self.get_joint_positions().copy()
        hold_acc = 0.0
        best_hold = 0.0
        best = float("inf")
        last = None
        while (float(self.data.time) - t0) <= deadline_s:
            vt = target + integ
            q = self._solve_ik(vt, q)
            self.set_arm_actuators(q)
            if hold_grip is not None:
                self.set_gripper_control(hold_grip)
            self.step(1)
            e = target - self._ee()
            err = float(np.linalg.norm(e))
            integ = np.clip(integ + self._KI * e * dt, -self._INTEG_CLAMP,
                            self._INTEG_CLAMP)
            last = err
            best = min(best, err)
            if err <= tol:
                hold_acc += dt
                best_hold = max(best_hold, hold_acc)
                if hold_acc >= hold_s:
                    return {"ok": True, "error": err, "best": best,
                            "hold": hold_acc, "integ": integ}
            else:
                hold_acc = 0.0
        return {"ok": False, "error": last if last is not None else float("inf"),
                "best": best, "hold": best_hold, "integ": integ}

    # ──────────────────────────────── A1 ────────────────────────────────
    def move_end_effector_to_position(self, request):
        self._check_finite_state()
        target = self._req_arr(request, "target_position_m", 3)
        if np.any(np.abs(target) > 1.0):
            raise CapabilityError("bad_request", "target_position_m out of [-1,1]")
        dur = float(request["max_duration_s"])
        if not (0.25 <= dur <= 8.0):
            raise CapabilityError("bad_request", "max_duration_s out of bounds")
        grip_hold = float(self.data.ctrl[self._grip_aid])
        res = self._regulate_to(target, dur, tol=0.012, hold_s=0.55,
                                hold_grip=grip_hold)
        if not res["ok"]:
            raise CapabilityError("not_reached",
                                  f"position error {res['error']:.4f} m within {dur}s",
                                  error_m=res["error"])
        return {"ok": True, "capability_id": "A1", "final_error_m": res["error"]}

    # ──────────────────────────────── A2 ────────────────────────────────
    def trace_cartesian_path(self, request):
        self._check_finite_state()
        wps = request["waypoints_m"]
        if not isinstance(wps, (list, tuple)) or not (2 <= len(wps) <= 8):
            raise CapabilityError("bad_request", "waypoints_m must have 2..8 items")
        pts = [self._req_arr({"w": w}, "w", 3) for w in wps]
        for p in pts:
            if np.any(np.abs(p) > 1.0):
                raise CapabilityError("bad_request", "waypoint out of [-1,1]")
        seg_dur = float(request["max_duration_per_segment_s"])
        if not (0.25 <= seg_dur <= 5.0):
            raise CapabilityError("bad_request", "segment duration out of bounds")
        grip_hold = float(self.data.ctrl[self._grip_aid])
        integ = np.zeros(3)
        for i, p in enumerate(pts):
            terminal = (i == len(pts) - 1)
            hold = 0.55 if terminal else 0.03
            tol = 0.012 if terminal else 0.015
            res = self._regulate_to(p, seg_dur, tol=tol, hold_s=hold,
                                    hold_grip=grip_hold, integ0=integ)
            integ = res["integ"]
            if not res["ok"]:
                raise CapabilityError("waypoint_unreached",
                                      f"waypoint {i} error {res['error']:.4f} m",
                                      waypoint_index=i, error_m=res["error"])
        return {"ok": True, "capability_id": "A2", "waypoints": len(pts)}

    # ──────────────────────────────── A3 ────────────────────────────────
    def _aperture_fraction(self):
        q = self.get_gripper_joint_positions()
        vals = list(q.values())
        avg = float(np.mean(vals)) if vals else self._GRIP_Q_OPEN
        span = self._GRIP_Q_CLOSED - self._GRIP_Q_OPEN
        closed_frac = (avg - self._GRIP_Q_OPEN) / span if span else 0.0
        return float(np.clip(1.0 - closed_frac, 0.0, 1.0))

    def set_gripper_opening(self, request):
        q = self.get_gripper_joint_positions()
        if not all(np.isfinite(v) for v in q.values()):
            raise CapabilityError("state_unavailable", "gripper state not finite")
        frac = float(request["opening_fraction"])
        if not (0.0 <= frac <= 1.0) or not np.isfinite(frac):
            raise CapabilityError("bad_request", "opening_fraction out of [0,1]")
        dur = float(request["max_duration_s"])
        if not (0.25 <= dur <= 8.0):
            raise CapabilityError("bad_request", "max_duration_s out of bounds")
        ctrl = self._GRIP_CTRL_OPEN + (1.0 - frac) * (self._GRIP_CTRL_CLOSED - self._GRIP_CTRL_OPEN)
        arm_hold = np.array([float(self.data.ctrl[a]) for a in self._arm_actuator_ids])
        dt = self._dt()
        t0 = float(self.data.time)
        hold_acc = 0.0
        last = None
        while (float(self.data.time) - t0) <= dur:
            self.set_arm_actuators(arm_hold)
            self.set_gripper_control(ctrl)
            self.step(1)
            err = abs(self._aperture_fraction() - frac)
            last = err
            if err <= 0.1:
                hold_acc += dt
                if hold_acc >= 0.3:
                    return {"ok": True, "capability_id": "A3", "aperture_error": err}
            else:
                hold_acc = 0.0
        raise CapabilityError("aperture_unreached",
                              f"aperture error {last:.3f} within {dur}s", error=last)
