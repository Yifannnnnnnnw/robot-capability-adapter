# SPDX-License-Identifier: Apache-2.0
"""Quadruped skeleton: joint-space control + hand-tuned periodic trot.

This is the v1 quadruped skeleton — deliberately simple. It uses joint-space PD
torque control with a sinusoidal trot gait so we can demonstrate a Go2 / ANYmal /
Spot walking forward without any learning or convex MPC. v2 will replace the
trot with proper convex-MPC body-control.

Design:
  - Spec lists four legs' joints + actuators (3 per leg), in model order.
  - Torque actuators receive joint-space PD torques; native position actuators
    receive joint-position targets directly.
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
        if self.spec.actuation not in {"joint_torque", "joint_position"}:
            # Keep this guard for callers that provide a duck-typed spec or
            # mutate a spec after construction.
            raise ValueError(
                "actuation must be 'joint_torque' or 'joint_position'"
            )
        self._gait_time = 0.0
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
                self._check_actuator_mapping(aid, len(self._actuator_ids))
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

        # Use the model's native joint and actuator ranges.  In particular,
        # ANYmal's position actuator ctrlrange is wider/narrower than some
        # joint ranges, so a target is bounded by both ranges before writing
        # the canonical ctrl vector.
        self._joint_lo = np.array(
            [
                float(m.jnt_range[jid, 0])
                if bool(m.jnt_limited[jid])
                else -np.inf
                for jid in self._joint_ids
            ],
            dtype=np.float64,
        )
        self._joint_hi = np.array(
            [
                float(m.jnt_range[jid, 1])
                if bool(m.jnt_limited[jid])
                else np.inf
                for jid in self._joint_ids
            ],
            dtype=np.float64,
        )
        self._ctrl_lo = np.array(
            [
                float(m.actuator_ctrlrange[aid, 0])
                if bool(m.actuator_ctrllimited[aid])
                else -np.inf
                for aid in self._actuator_ids
            ],
            dtype=np.float64,
        )
        self._ctrl_hi = np.array(
            [
                float(m.actuator_ctrlrange[aid, 1])
                if bool(m.actuator_ctrllimited[aid])
                else np.inf
                for aid in self._actuator_ids
            ],
            dtype=np.float64,
        )

        # The base must own a free joint for a meaningful world-frame twist.
        root_joint_id = int(m.body_jntadr[self._base_body_id])
        if root_joint_id < 0 or int(m.jnt_type[root_joint_id]) != int(
            mj.mjtJoint.mjJNT_FREE
        ):
            raise ValueError(
                f"base body {self.spec.base_body_name!r} must own a free joint"
            )
        self._base_qvel_adr = int(m.jnt_dofadr[root_joint_id])

    def _check_actuator_mapping(self, actuator_id: int, index: int) -> None:
        """Check a declared actuator drives its same-position hinge joint."""
        mj = self._mj
        model = self.model
        joint_id = self._joint_ids[index]
        if int(model.actuator_trntype[actuator_id]) != int(
            mj.mjtTrn.mjTRN_JOINT
        ):
            raise ValueError(
                f"actuator {self._actuator_name_for_id(actuator_id)!r} must use "
                "joint transmission"
            )
        driven_joint_id = int(model.actuator_trnid[actuator_id, 0])
        if driven_joint_id != joint_id:
            expected = self._joint_names_flat[index]
            driven = mj.mj_id2name(
                model, mj.mjtObj.mjOBJ_JOINT, driven_joint_id
            )
            raise ValueError(
                f"actuator {self._actuator_name_for_id(actuator_id)!r} drives "
                f"{driven!r}, expected joint {expected!r}"
            )

        is_position = self._is_native_position_actuator(actuator_id)
        if self.spec.actuation == "joint_position" and not is_position:
            raise ValueError(
                f"actuation='joint_position' requires native position actuators; "
                f"{self._actuator_name_for_id(actuator_id)!r} is not one"
            )
        if self.spec.actuation == "joint_torque" and is_position:
            raise ValueError(
                f"actuation='joint_torque' requires torque/motor actuators; "
                f"{self._actuator_name_for_id(actuator_id)!r} is a position actuator"
            )

    def _actuator_name_for_id(self, actuator_id: int) -> str:
        name = self._mj.mj_id2name(
            self.model, self._mj.mjtObj.mjOBJ_ACTUATOR, actuator_id
        )
        return name if name is not None else f"actuator#{actuator_id}"

    def _is_native_position_actuator(self, actuator_id: int) -> bool:
        """Recognize MuJoCo's native ``<position>`` servo representation."""
        m = self.model
        mj = self._mj
        if int(m.actuator_gaintype[actuator_id]) != int(mj.mjtGain.mjGAIN_FIXED):
            return False
        if int(m.actuator_biastype[actuator_id]) != int(mj.mjtBias.mjBIAS_AFFINE):
            return False
        gain = float(m.actuator_gainprm[actuator_id, 0])
        bias_q = float(m.actuator_biasprm[actuator_id, 1])
        return np.isfinite(gain) and gain > 0.0 and np.isclose(
            bias_q, -gain, rtol=0.0, atol=1.0e-9
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

    def get_base_twist(self) -> dict[str, np.ndarray | float]:
        """Return the base twist in world and body-yaw frames.

        The MuJoCo spatial-velocity convention is angular then linear.  The
        free-joint's first three ``qvel`` entries are the base linear velocity
        in world coordinates; using them avoids the inertial-offset term that
        ``mj_objectVelocity`` includes for the body's linear component.
        """
        self._mj.mj_forward(self.model, self.data)
        spatial = np.zeros(6, dtype=np.float64)
        self._mj.mj_objectVelocity(
            self.model,
            self.data,
            self._mj.mjtObj.mjOBJ_BODY,
            self._base_body_id,
            spatial,
            0,
        )
        angular_world = np.array(spatial[:3], dtype=np.float64, copy=True)
        linear_world = np.array(
            self.data.qvel[self._base_qvel_adr : self._base_qvel_adr + 3],
            dtype=np.float64,
            copy=True,
        )

        rotation = np.array(
            self.data.xmat[self._base_body_id], dtype=np.float64, copy=False
        ).reshape(3, 3)
        body_x_world = rotation[:, 0]
        horizontal_norm = float(np.hypot(body_x_world[0], body_x_world[1]))
        if horizontal_norm <= 1.0e-9:
            raise RuntimeError("base yaw is undefined at the current orientation")
        cosine = float(body_x_world[0]) / horizontal_norm
        sine = float(body_x_world[1]) / horizontal_norm
        linear_body_yaw = np.array(
            (
                cosine * linear_world[0] + sine * linear_world[1],
                -sine * linear_world[0] + cosine * linear_world[1],
            ),
            dtype=np.float64,
        )
        heading_rate = np.cross(angular_world, body_x_world)
        yaw_rate = (
            float(body_x_world[0]) * float(heading_rate[1])
            - float(body_x_world[1]) * float(heading_rate[0])
        ) / (horizontal_norm * horizontal_norm)
        return {
            "linear_world_m_s": linear_world,
            "linear_body_yaw_m_s": linear_body_yaw,
            "angular_world_rad_s": angular_world,
            "yaw_rate_rad_s": float(yaw_rate),
        }

    # ─────────────────────────────────────────────────────────────────────
    # PD torque control (the workhorse — used by all behaviors)
    # ─────────────────────────────────────────────────────────────────────

    def _apply_pd(self, q_des: np.ndarray, qd_des: Optional[np.ndarray] = None) -> None:
        """Write one bounded posture command to the canonical ``data.ctrl``.

        Torque actuators receive joint-space PD torques.  Native MuJoCo
        position actuators already contain their own servo, so they receive
        the bounded joint-position target directly.
        """
        q_des = np.asarray(q_des, dtype=np.float64)
        if q_des.shape != (self.dof,) or not np.all(np.isfinite(q_des)):
            raise ValueError(f"q_des must contain {self.dof} finite values")
        q_des = np.clip(q_des, self._joint_lo, self._joint_hi)
        q = self.get_joint_positions()
        qd = self.get_joint_velocities()
        if qd_des is None:
            qd_des = np.zeros(self.dof, dtype=np.float64)
        else:
            qd_des = np.asarray(qd_des, dtype=np.float64)
            if qd_des.shape != (self.dof,) or not np.all(np.isfinite(qd_des)):
                raise ValueError(f"qd_des must contain {self.dof} finite values")

        if self.spec.actuation == "joint_position":
            command = np.clip(q_des, self._ctrl_lo, self._ctrl_hi)
        else:
            command = self.spec.kp * (q_des - q) + self.spec.kd * (qd_des - qd)
            command = np.clip(command, self._ctrl_lo, self._ctrl_hi)
        for i, aid in enumerate(self._actuator_ids):
            self.data.ctrl[aid] = float(command[i])

    def set_joint_targets(self, q_targets: np.ndarray) -> None:
        """Write bounded joint targets without advancing the simulation.

        In torque mode this is a PD target; in native position mode it is the
        target consumed by the MJCF position servo.  Motion is performed only
        by a subsequent :meth:`step` call.
        """
        self._apply_pd(q_targets)

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
        """Return the declared thigh sign without mutating simulator state.

        Earlier versions ran two probe trots and restored ``qpos``/``qvel``
        between them.  A motion primitive must keep the canonical simulation
        history intact, so sign selection is now an explicit spec decision;
        callers can set ``thigh_forward_sign`` after inspecting their MJCF.
        The legacy arguments remain accepted for source compatibility.
        """
        if getattr(self, "_calibrated_thigh_sign", None) is not None:
            return self._calibrated_thigh_sign  # type: ignore[return-value]

        del probe_secs, probe_speed
        chosen = 1.0 if float(self.spec.thigh_forward_sign) >= 0.0 else -1.0
        self._calibrated_thigh_sign = chosen
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
            "actuation": self.spec.actuation,
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
