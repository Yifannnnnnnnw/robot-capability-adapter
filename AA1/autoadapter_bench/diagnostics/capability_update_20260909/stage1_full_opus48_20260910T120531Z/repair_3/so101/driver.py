# SPDX-License-Identifier: Apache-2.0
"""Generated capability driver for SO-101 (ArmSerialDLSSkeleton).

Implements the public capability contract (A1..A5) on top of the skeleton's
low-level DLS IK / actuator primitives. All timing is measured with
``data.time`` (simulation seconds). Physics is advanced only through actuator
commands on the canonical model/data; live qpos/qvel are never set to achieve
an action, and no non-canonical world is ever stepped. Kinematic IK runs on
the skeleton's internal scratch state without stepping physics.
"""
from __future__ import annotations

import numpy as np

from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton, IKUnreachableError
from auto_adapter.skeletons.base import ArmSpec


class CapabilityError(RuntimeError):
    """Bounded capability failure with a structured payload."""

    def __init__(self, capability: str, reason: str, **info) -> None:
        super().__init__(f"[{capability}] {reason}")
        self.capability = capability
        self.reason = reason
        self.info = info


_ARM_JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
_ARM_ACTS = ["act_shoulder_pan", "act_shoulder_lift", "act_elbow_flex",
             "act_wrist_flex", "act_wrist_roll"]
_JOINT_LIMITS = {
    "shoulder_pan": (-1.92, 1.92),
    "shoulder_lift": (-1.75, 1.75),
    "elbow_flex": (-1.69, 1.69),
    "wrist_flex": (-1.66, 1.66),
    "wrist_roll": (-2.74, 2.84),
}


class Robot(ArmSerialDLSSkeleton):
    """SO-101 capability robot: 5-DOF arm + 1-DOF jaw gripper."""

    def __init__(self, model, data, spec: ArmSpec) -> None:
        super().__init__(model, data, spec)
        aid = self._gripper_actuator_ids[0]
        lo, hi = self.model.actuator_ctrlrange[aid]
        self._jaw_lo = float(lo)
        self._jaw_hi = float(hi)
        jid = self._gripper_joint_ids[0]
        jlo, jhi = self.model.jnt_range[jid]
        self._jaw_jlo = float(jlo)
        self._jaw_jhi = float(jhi)
        self._open_ctrl = self._jaw_hi
        self._close_ctrl = self._jaw_lo

    # ------------------------------------------------------------------ utils
    @property
    def _dt(self) -> float:
        return float(self.model.opt.timestep)

    def _check_finite_state(self, cap: str) -> None:
        q = self.get_joint_positions()
        v = self.get_joint_velocities()
        if not (np.all(np.isfinite(q)) and np.all(np.isfinite(v))):
            raise CapabilityError(cap, "non-finite robot state before motion")

    def _hold_arm(self) -> None:
        cur = np.array([float(self.data.ctrl[a]) for a in self._arm_actuator_ids])
        self.set_arm_actuators(cur)

    def _hold_gripper(self) -> None:
        aid = self._gripper_actuator_ids[0]
        self.data.ctrl[aid] = float(self.data.ctrl[aid])

    def _ee(self) -> np.ndarray:
        self._mj.mj_forward(self.model, self.data)
        return self._ee_pos_now()

    def _ee_speed(self) -> float:
        """World EE linear speed via positional Jacobian and joint velocity."""
        self._mj.mj_forward(self.model, self.data)
        jacp = np.zeros((3, self.model.nv))
        self._ee_jac_pos(jacp, self.data)
        v = jacp @ self.data.qvel
        return float(np.linalg.norm(v))

    def _solve_ik(self, cap: str, target: np.ndarray) -> np.ndarray:
        try:
            return self.ik(np.asarray(target, float),
                           q_init=self.get_joint_positions(),
                           raise_on_unreachable=False)
        except IKUnreachableError as e:  # pragma: no cover - defensive
            return e.q_final

    def _regulate_to_target(self, cap, target, deadline_t, tol,
                            hold_s, keep_gripper=True):
        """Closed-loop world-position regulation using only ctrl + physics."""
        target = np.asarray(target, float)
        q_goal = self._solve_ik(cap, target)
        self.set_arm_actuators(q_goal)
        hold_start = None
        last_err = float("inf")
        resolve_every = max(1, int(0.05 / self._dt))
        i = 0
        while self.data.time < deadline_t:
            if i % resolve_every == 0:
                q_goal = self._solve_ik(cap, target)
            self.set_arm_actuators(q_goal)
            if keep_gripper:
                self._hold_gripper()
            self.step(1)
            i += 1
            err = float(np.linalg.norm(self._ee() - target))
            last_err = err
            if err <= tol:
                if hold_start is None:
                    hold_start = self.data.time
                elif self.data.time - hold_start >= hold_s:
                    return True, err
            else:
                hold_start = None
        return False, last_err

    # ------------------------------------------------------- request parsing
    def _req(self, cap, request, key):
        if not isinstance(request, dict) or key not in request:
            raise CapabilityError(cap, f"missing required field {key!r}")
        return request[key]

    def _req_num(self, cap, request, key, lo, hi):
        v = self._req(cap, request, key)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not np.isfinite(v):
            raise CapabilityError(cap, f"{key} must be a finite number")
        v = float(v)
        if v < lo or v > hi:
            raise CapabilityError(cap, f"{key}={v} out of [{lo},{hi}]")
        return v

    def _req_vec3(self, cap, request, key, lo, hi):
        v = self._req(cap, request, key)
        if not isinstance(v, (list, tuple)) or len(v) != 3:
            raise CapabilityError(cap, f"{key} must have 3 elements")
        out = []
        for x in v:
            if not isinstance(x, (int, float)) or isinstance(x, bool) or not np.isfinite(x):
                raise CapabilityError(cap, f"{key} elements must be finite")
            if x < lo or x > hi:
                raise CapabilityError(cap, f"{key} element {x} out of [{lo},{hi}]")
            out.append(float(x))
        return np.array(out, float)

    def _extra_keys(self, cap, request, allowed):
        if not isinstance(request, dict):
            raise CapabilityError(cap, "request must be an object")
        extra = set(request) - allowed
        if extra:
            raise CapabilityError(cap, f"unexpected fields {sorted(extra)}")

    # =============================================================== A1
    def move_end_effector_to_position(self, request):
        cap = "A1"
        target = self._req_vec3(cap, request, "target_position_m", -1.0, 1.0)
        dur = self._req_num(cap, request, "max_duration_s", 0.25, 8.0)
        self._extra_keys(cap, request, {"target_position_m", "max_duration_s"})
        self._check_finite_state(cap)
        deadline = self.data.time + dur
        reached, err = self._regulate_to_target(cap, target, deadline,
                                                 tol=0.015, hold_s=0.5)
        if not reached:
            self._hold_arm()
            raise CapabilityError(cap, "target not held within duration",
                                  final_error=err)
        return {"capability": cap, "status": "ok", "final_error_m": err,
                "ee_position_m": self._ee().tolist()}

    # =============================================================== A2
    def trace_cartesian_path(self, request):
        cap = "A2"
        wps = self._req(cap, request, "waypoints_m")
        if not isinstance(wps, (list, tuple)) or not (2 <= len(wps) <= 8):
            raise CapabilityError(cap, "waypoints_m must have 2..8 entries")
        seg_dur = self._req_num(cap, request, "max_duration_per_segment_s", 0.25, 5.0)
        self._extra_keys(cap, request,
                         {"waypoints_m", "max_duration_per_segment_s"})
        self._check_finite_state(cap)
        pts = [self._req_vec3(cap, {"w": w}, "w", -1.0, 1.0) for w in wps]
        for idx, p in enumerate(pts):
            terminal = (idx == len(pts) - 1)
            hold = 0.5 if terminal else 0.05
            tol = 0.015 if terminal else 0.02
            deadline = self.data.time + seg_dur
            reached, err = self._regulate_to_target(cap, p, deadline,
                                                     tol=tol, hold_s=hold)
            if not reached:
                self._hold_arm()
                raise CapabilityError(cap, f"waypoint {idx} not reached in order",
                                      waypoint_index=idx, final_error=err)
        return {"capability": cap, "status": "ok",
                "waypoints_visited": len(pts),
                "ee_position_m": self._ee().tolist()}

    # ------------------------------------------------------- gripper mapping
    def _jaw_pos(self) -> float:
        return float(next(iter(self.get_gripper_joint_positions().values())))

    def _ctrl_for_fraction(self, f: float) -> float:
        return self._close_ctrl + float(f) * (self._open_ctrl - self._close_ctrl)

    def _aperture_fraction(self) -> float:
        span = self._jaw_jhi - self._jaw_jlo
        if abs(span) < 1e-9:
            return 0.0
        return float(np.clip((self._jaw_pos() - self._jaw_jlo) / span, 0.0, 1.0))

    # =============================================================== A3
    def set_gripper_opening(self, request):
        cap = "A3"
        frac = self._req_num(cap, request, "opening_fraction", 0.0, 1.0)
        dur = self._req_num(cap, request, "max_duration_s", 0.25, 8.0)
        self._extra_keys(cap, request, {"opening_fraction", "max_duration_s"})
        if not np.all(np.isfinite(list(self.get_gripper_joint_positions().values()))):
            raise CapabilityError(cap, "non-finite gripper state")
        arm_hold = np.array([float(self.data.ctrl[a]) for a in self._arm_actuator_ids])
        aid = self._gripper_actuator_ids[0]
        cmd = float(np.clip(self._ctrl_for_fraction(frac),
                            *self.model.actuator_ctrlrange[aid]))
        deadline = self.data.time + dur
        hold_start = None
        last_err = 1.0
        while self.data.time < deadline:
            self.set_arm_actuators(arm_hold)
            self.set_gripper_control(cmd)
            self.step(1)
            err = abs(self._aperture_fraction() - frac)
            last_err = err
            if err <= 0.1:
                if hold_start is None:
                    hold_start = self.data.time
                elif self.data.time - hold_start >= 0.25:
                    return {"capability": cap, "status": "ok",
                            "aperture_fraction": self._aperture_fraction(),
                            "error": err}
            else:
                hold_start = None
        self.set_arm_actuators(arm_hold)
        self.set_gripper_control(cmd)
        raise CapabilityError(cap, "aperture not established within duration",
                              final_error=last_err)
