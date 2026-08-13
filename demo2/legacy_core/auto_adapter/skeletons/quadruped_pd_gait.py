# SPDX-License-Identifier: Apache-2.0
"""Quadruped skeleton: joint-space PD + hand-tuned periodic trot, zero training.

This is the v1 quadruped skeleton — deliberately simple. It uses joint-space PD
torque control with a sinusoidal trot gait so we can demonstrate a Go2 / ANYmal /
Spot walking forward without any learning or convex MPC. v2 will replace the
trot with proper convex-MPC body-control.

Design:
  - Spec lists FL/FR/RL/RR joints + actuators (3 per leg). Torque actuators.
  - `stand_up(duration)`: PD-track an interpolated trajectory to spec.home_qpos
  - `walk_forward(secs, speed)`: trot gait, FL+RR paired with FR+RL anti-phase
  - `sit()`: lower body via PD to a folded pose
  - PD: τ = kp · (q_des - q) + kd · (qd_des - qd); applied at every sim step

Compatible with: Unitree Go1/Go2, Spot, ANYmal, Mini Cheetah (any 12-DoF
quadruped with torque-controlled hip/thigh/calf per leg, in that order).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .base import QuadrupedSpec, SkeletonBase


# Joint index within each leg (hip / thigh / calf is the universal triplet
# for "hip-driven quadrupeds" — Spot, Go2, ANYmal, Mini Cheetah, etc.)
_J_HIP, _J_THIGH, _J_CALF = 0, 1, 2

# NOTE: leg labels are NOT hardcoded. The skeleton uses whatever keys the
# spec provides (FL/FR/RL/RR is conventional but spec may use HL/HR/leg_0,
# etc.). Diagonal-pair phasing for trot is derived in _resolve_leg_pairs().


class QuadrupedPDGaitSkeleton(SkeletonBase):
    """12-DoF quadruped skeleton: joint-space PD + hand-tuned trot.

    All math is plain numpy + MuJoCo. The LLM agent only fills the Spec and
    instantiates this class. Behavior methods (`stand_up`, `walk_forward`,
    `sit`) advance physics internally.
    """

    def __init__(self, model, data, spec: QuadrupedSpec) -> None:
        super().__init__(model, data, spec)
        self.spec: QuadrupedSpec = spec
        self._resolve_indices()

    # ─────────────────────────────────────────────────────────────────────
    # One-time index resolution
    # ─────────────────────────────────────────────────────────────────────

    def _resolve_indices(self) -> None:
        mj = self._mj
        m = self.model

        # Torso body
        self._base_body_id = mj.mj_name2id(
            m, mj.mjtObj.mjOBJ_BODY, self.spec.base_body_name
        )
        if self._base_body_id < 0:
            raise ValueError(f"base_body_name {self.spec.base_body_name!r} not in MJCF")

        # Take leg ordering from the spec — NOT hardcoded. The skeleton works
        # for any leg labels (FL/FR/RL/RR, HL/HR/FL/FR, leg_0..leg_3, …).
        # We do require this to be a quadruped (exactly 4 legs) — that's what
        # the trot gait is designed for; a generic LeggedSkeleton with
        # arbitrary leg count is a v2 ext.
        leg_keys = list(self.spec.leg_joint_names.keys())
        if len(leg_keys) != 4:
            raise ValueError(
                f"this skeleton handles exactly 4 legs (quadruped); "
                f"got {len(leg_keys)} legs: {leg_keys}. For bipeds / hexapods, "
                f"use a different skeleton class."
            )
        if set(leg_keys) != set(self.spec.leg_actuator_names.keys()):
            raise ValueError(
                "leg_joint_names and leg_actuator_names must have the same keys; "
                f"got {sorted(leg_keys)} vs {sorted(self.spec.leg_actuator_names.keys())}"
            )
        self._leg_order: tuple[str, ...] = tuple(leg_keys)

        # Joint-per-leg count is derived (not hardcoded to 3); but in practice
        # current trot logic assumes hip/thigh/calf, so we require 3 per leg.
        joints_per_leg = len(self.spec.leg_joint_names[self._leg_order[0]])
        if joints_per_leg != 3:
            raise ValueError(
                f"this skeleton expects 3 joints per leg (hip/thigh/calf); "
                f"got {joints_per_leg} per leg"
            )
        for leg in self._leg_order:
            if len(self.spec.leg_joint_names[leg]) != joints_per_leg:
                raise ValueError(
                    f"leg {leg!r} has {len(self.spec.leg_joint_names[leg])} joints; "
                    f"expected {joints_per_leg}"
                )
            if len(self.spec.leg_actuator_names[leg]) != joints_per_leg:
                raise ValueError(
                    f"leg {leg!r} has {len(self.spec.leg_actuator_names[leg])} actuators; "
                    f"expected {joints_per_leg}"
                )

        self._joint_ids: list[int] = []
        self._qpos_adr: list[int] = []
        self._qvel_adr: list[int] = []
        self._joint_names_flat: list[str] = []
        for leg in self._leg_order:
            for jname in self.spec.leg_joint_names[leg]:
                jid = mj.mj_name2id(m, mj.mjtObj.mjOBJ_JOINT, jname)
                if jid < 0:
                    raise ValueError(f"joint {jname!r} (leg {leg}) not in MJCF")
                if int(m.jnt_type[jid]) != int(mj.mjtJoint.mjJNT_HINGE):
                    raise ValueError(
                        f"joint {jname!r} must be HINGE (got type {m.jnt_type[jid]})"
                    )
                self._joint_ids.append(int(jid))
                self._qpos_adr.append(int(m.jnt_qposadr[jid]))
                self._qvel_adr.append(int(m.jnt_dofadr[jid]))
                self._joint_names_flat.append(jname)

        self._actuator_ids: list[int] = []
        self._actuator_names_flat: list[str] = []
        for leg in self._leg_order:
            for aname in self.spec.leg_actuator_names[leg]:
                aid = mj.mj_name2id(m, mj.mjtObj.mjOBJ_ACTUATOR, aname)
                if aid < 0:
                    raise ValueError(f"actuator {aname!r} (leg {leg}) not in MJCF")
                self._actuator_ids.append(int(aid))
                self._actuator_names_flat.append(aname)

        expected_dof = 4 * joints_per_leg
        if len(self.spec.home_qpos) != expected_dof:
            raise ValueError(
                f"home_qpos length must be {expected_dof} (4 legs × {joints_per_leg}); "
                f"got {len(self.spec.home_qpos)}"
            )
        self._home_q = np.array(self.spec.home_qpos, dtype=np.float64)
        self._joints_per_leg = joints_per_leg
        self.dof = expected_dof

        # Per-actuator torque clamp from MJCF ctrlrange (avoid clipping warnings)
        self._ctrl_lo = np.array(
            [float(m.actuator_ctrlrange[aid, 0]) for aid in self._actuator_ids],
            dtype=np.float64,
        )
        self._ctrl_hi = np.array(
            [float(m.actuator_ctrlrange[aid, 1]) for aid in self._actuator_ids],
            dtype=np.float64,
        )

    # ─────────────────────────────────────────────────────────────────────
    # State queries
    # ─────────────────────────────────────────────────────────────────────

    def get_joint_positions(self) -> np.ndarray:
        """Flat-N vector in the spec's leg order × (hip, thigh, calf) order.
        For a typical 4-leg robot N=12.
        """
        return np.array([self.data.qpos[a] for a in self._qpos_adr], dtype=np.float64)

    def get_joint_velocities(self) -> np.ndarray:
        return np.array([self.data.qvel[a] for a in self._qvel_adr], dtype=np.float64)

    def get_base_pose(self) -> tuple[np.ndarray, np.ndarray]:
        """Return (xyz, R3x3) of the torso body."""
        self._mj.mj_forward(self.model, self.data)
        pos = np.array(self.data.xpos[self._base_body_id], dtype=np.float64)
        R = np.array(self.data.xmat[self._base_body_id], dtype=np.float64).reshape(3, 3)
        return pos, R

    def get_body_height(self) -> float:
        return float(self.data.xpos[self._base_body_id, 2])

    # ─────────────────────────────────────────────────────────────────────
    # PD torque control (the workhorse — used by all behaviors)
    # ─────────────────────────────────────────────────────────────────────

    def _apply_pd(self, q_des: np.ndarray, qd_des: Optional[np.ndarray] = None) -> None:
        """Compute τ = kp·(q_des - q) + kd·(qd_des - qd), write to data.ctrl."""
        q = self.get_joint_positions()
        qd = self.get_joint_velocities()
        if qd_des is None:
            qd_des = np.zeros(12, dtype=np.float64)
        tau = self.spec.kp * (q_des - q) + self.spec.kd * (qd_des - qd)
        tau = np.clip(tau, self._ctrl_lo, self._ctrl_hi)
        for i, aid in enumerate(self._actuator_ids):
            self.data.ctrl[aid] = float(tau[i])

    # ─────────────────────────────────────────────────────────────────────
    # Behavior: stand up — interpolate from current qpos to home_qpos
    # ─────────────────────────────────────────────────────────────────────

    def stand_up(self, duration: float = 2.0) -> bool:
        """PD-track a linear-in-time joint trajectory from current → home_qpos.

        Holds the final pose for ~0.5s extra to let the body settle. Returns
        True iff the torso ends up within 5cm of the spec body_height_target.
        """
        q0 = self.get_joint_positions()
        q_target = self._home_q.copy()
        n_steps = max(1, int(duration / self.model.opt.timestep))
        for k in range(n_steps):
            alpha = (k + 1) / n_steps
            q_des = (1.0 - alpha) * q0 + alpha * q_target
            self._apply_pd(q_des)
            self.step(1)
        # Hold for ~0.5s
        for _ in range(int(0.5 / self.model.opt.timestep)):
            self._apply_pd(q_target)
            self.step(1)

        h = self.get_body_height()
        ok = abs(h - self.spec.body_height_target) < 0.05
        return ok

    # ─────────────────────────────────────────────────────────────────────
    # Behavior: walk forward (hand-tuned trot)
    # ─────────────────────────────────────────────────────────────────────

    def _run_trot(self, secs: float, speed: float,
                  thigh_sign: float) -> tuple[float, float]:
        """Internal: run the trot loop for `secs` with a given thigh sign.
        Returns (forward_dx, lateral_dy) world-frame displacement. Pure
        execution — no calibration, no decisions."""
        pos0, _ = self.get_base_pose()
        sim_dt = float(self.model.opt.timestep)
        n_steps = max(1, int(secs / sim_dt))
        omega = 2.0 * np.pi * self.spec.gait_freq_hz

        if self.spec.gait_phases is not None:
            phase_per_leg = dict(self.spec.gait_phases)
        else:
            phase_per_leg = {
                self._leg_order[0]: 0.0,
                self._leg_order[3]: 0.0,
                self._leg_order[1]: np.pi,
                self._leg_order[2]: np.pi,
            }

        scale = float(speed) / 0.3
        amp_thigh = float(self.spec.swing_amp_thigh) * scale * float(thigh_sign)
        amp_calf = float(self.spec.swing_amp_calf) * scale

        jpl = self._joints_per_leg
        for k in range(n_steps):
            t = k * sim_dt
            q_des = self._home_q.copy()
            for leg_idx, leg in enumerate(self._leg_order):
                phi = omega * t + phase_per_leg[leg]
                swing = max(0.0, np.sin(phi))
                stance = min(0.0, np.sin(phi))
                thigh_idx = leg_idx * jpl + _J_THIGH
                calf_idx = leg_idx * jpl + _J_CALF
                q_des[thigh_idx] = self._home_q[thigh_idx] + amp_thigh * (
                    swing - 0.5 * (-stance)
                )
                q_des[calf_idx] = self._home_q[calf_idx] - amp_calf * swing
            self._apply_pd(q_des)
            self.step(1)

        pos1, _ = self.get_base_pose()
        return float(pos1[0] - pos0[0]), float(pos1[1] - pos0[1])

    def calibrate_walk_direction(self, probe_secs: float = 0.6,
                                  probe_speed: float = 0.2) -> float:
        """Empirically determine which thigh sign produces forward motion.

        Sign convention varies across MJCFs (Go2: -1, A1: was wrong, ANYmal: +1).
        This runs a short trot with the spec's current sign, then a short trot
        with the negated sign, and picks whichever produced larger forward
        displacement. Caches the calibrated sign on the skeleton so subsequent
        walk_forward() calls use it directly.

        Returns the chosen sign (+1 or -1).
        """
        if getattr(self, "_calibrated_thigh_sign", None) is not None:
            return self._calibrated_thigh_sign  # type: ignore[return-value]

        # Snapshot full sim state so the probe doesn't perturb downstream calls
        qpos0 = np.array(self.data.qpos)
        qvel0 = np.array(self.data.qvel)
        ctrl0 = np.array(self.data.ctrl)

        spec_sign = float(self.spec.thigh_forward_sign)
        dx_pos, _ = self._run_trot(probe_secs, probe_speed, +abs(spec_sign))

        # Restore state then probe the other sign
        self.data.qpos[:] = qpos0
        self.data.qvel[:] = qvel0
        self.data.ctrl[:] = ctrl0
        self._mj.mj_forward(self.model, self.data)

        dx_neg, _ = self._run_trot(probe_secs, probe_speed, -abs(spec_sign))

        # Restore so the caller starts from the original state
        self.data.qpos[:] = qpos0
        self.data.qvel[:] = qvel0
        self.data.ctrl[:] = ctrl0
        self._mj.mj_forward(self.model, self.data)

        chosen = +1.0 if dx_pos > dx_neg else -1.0
        self._calibrated_thigh_sign = chosen
        self._calibration_probe = {"dx_at_pos1": dx_pos, "dx_at_neg1": dx_neg,
                                    "chosen": chosen}
        return chosen

    def walk_forward(self, secs: float = 3.0, speed: float = 0.3,
                     auto_calibrate: bool = True) -> bool:
        """Hand-tuned trot.  v1 — fragile; v2 will be convex-MPC.

        Diagonal pairs swing in anti-phase. Trot phases come from
        spec.gait_phases or auto-derived from leg-key order.

        Thigh sign: if auto_calibrate=True (default), runs two short probe
        trots on first call to pick the sign that produces forward motion,
        then caches. Set auto_calibrate=False to use spec.thigh_forward_sign
        verbatim (legacy behavior).

        Returns True iff the body moved forward (+X world) by >= 0.05 m.
        """
        if auto_calibrate:
            thigh_sign = self.calibrate_walk_direction()
        else:
            thigh_sign = float(self.spec.thigh_forward_sign)

        pos0, _ = self.get_base_pose()
        dx, _ = self._run_trot(secs, speed, thigh_sign)
        return dx > 0.05

    # ─────────────────────────────────────────────────────────────────────
    # Behavior: sit
    # ─────────────────────────────────────────────────────────────────────

    def sit(self, duration: float = 2.0) -> bool:
        """Lower the body by folding thighs forward + tucking calves under."""
        sit_q = self._home_q.copy()
        jpl = self._joints_per_leg
        for leg_idx in range(len(self._leg_order)):
            sit_q[leg_idx * jpl + _J_THIGH] = self._home_q[leg_idx * jpl + _J_THIGH] + 0.6
            sit_q[leg_idx * jpl + _J_CALF] = self._home_q[leg_idx * jpl + _J_CALF] - 0.5

        q0 = self.get_joint_positions()
        n_steps = max(1, int(duration / self.model.opt.timestep))
        for k in range(n_steps):
            alpha = (k + 1) / n_steps
            q_des = (1.0 - alpha) * q0 + alpha * sit_q
            self._apply_pd(q_des)
            self.step(1)

        return self.get_body_height() < self.spec.body_height_target * 0.7

    # ─────────────────────────────────────────────────────────────────────
    # Debug / introspection
    # ─────────────────────────────────────────────────────────────────────

    def describe(self) -> dict:
        return {
            "dof": self.dof,
            "base_body": self.spec.base_body_name,
            "leg_order": list(self._leg_order),
            "joints_per_leg": self._joints_per_leg,
            "legs": {
                leg: {
                    "joints": list(self.spec.leg_joint_names[leg]),
                    "actuators": list(self.spec.leg_actuator_names[leg]),
                }
                for leg in self._leg_order
            },
            "home_qpos": self._home_q.tolist(),
            "gait": {
                "freq_hz": self.spec.gait_freq_hz,
                "swing_amp_thigh": self.spec.swing_amp_thigh,
                "swing_amp_calf": self.spec.swing_amp_calf,
                "thigh_forward_sign": self.spec.thigh_forward_sign,
            },
            "pd": {"kp": self.spec.kp, "kd": self.spec.kd},
            "body_height_target": self.spec.body_height_target,
            "current_body_height": self.get_body_height(),
        }
