# SPDX-License-Identifier: Apache-2.0
"""Serial-chain manipulator skeleton: DLS IK + weld-based sim grasp.

Production-tested motion primitives. The LLM agent fills an `ArmSpec`
(joint / actuator / site / weld names) and instantiates this class — it
does *not* implement any of the math below.

Designed for: SO-100, SO-101, Franka Panda, UR5e, xArm 6/7, KUKA iiwa,
and similar serial open-chain manipulators (5–7 DoF).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .base import ArmSpec, SkeletonBase
from .grasp_backends import GraspBackend, make_grasp_backend


class IKUnreachableError(RuntimeError):
    """Raised by `ik()` when the target is not reachable within tolerance.

    Carries the final residual + final joint vector so Phase-3 validators
    can decide whether to fail-loud or fall back to a partial-progress
    move. Codex review F5 — explicit unreachable-target detection.
    """

    def __init__(self, residual: float, tolerance: float, q_final: np.ndarray) -> None:
        super().__init__(
            f"IK did not converge: |err|={residual:.4f} m > tol={tolerance:.4f} m"
        )
        self.residual = float(residual)
        self.tolerance = float(tolerance)
        self.q_final = q_final.copy()


class ArmSerialDLSSkeleton(SkeletonBase):
    """Damped Least-Squares IK + actuator-space interp + weld grasp.

    All numerical methods (Jacobian, DLS damping, joint clamping, weld
    activation) live in this file. Agent-generated code should only
    construct an `ArmSpec`, call `from_mjcf`, and use the public API
    below from `skills.py`.
    """

    def __init__(self, model, data, spec: ArmSpec) -> None:
        super().__init__(model, data, spec)
        self.spec: ArmSpec = spec  # type-narrow for static checkers
        self._resolve_indices()

    # ─────────────────────────────────────────────────────────────────────
    # One-time index resolution (name → MuJoCo internal id)
    # ─────────────────────────────────────────────────────────────────────

    def _resolve_indices(self) -> None:
        """Resolve every name in the spec to its MuJoCo numeric id.

        Raises ValueError with a precise message on any mismatch so the
        agent's Phase-3 patch loop has a clear signal.
        """
        mj = self._mj
        m = self.model

        # End-effector reference: site preferred (more precise), body fallback
        # for MJCFs that ship without an explicit <site> (e.g. Franka panda.xml).
        self._ee_site_id: int = -1
        self._ee_body_id: int = -1
        if self.spec.ee_site_name:
            self._ee_site_id = mj.mj_name2id(
                m, mj.mjtObj.mjOBJ_SITE, self.spec.ee_site_name
            )
            if self._ee_site_id < 0:
                raise ValueError(f"ee_site_name {self.spec.ee_site_name!r} not in MJCF")
        elif self.spec.ee_body_name:
            self._ee_body_id = mj.mj_name2id(
                m, mj.mjtObj.mjOBJ_BODY, self.spec.ee_body_name
            )
            if self._ee_body_id < 0:
                raise ValueError(f"ee_body_name {self.spec.ee_body_name!r} not in MJCF")
        else:
            # Caught by ArmSpec.__post_init__, but keep defense-in-depth.
            raise ValueError("ArmSpec must set ee_site_name or ee_body_name")
        # Property: True iff we're using a site as the EE reference frame
        self._ee_use_site: bool = self._ee_site_id >= 0

        # Arm joints (qpos / qvel addresses; assume each is a hinge or slide = 1 DoF)
        self._arm_joint_ids: list[int] = []
        self._arm_qpos_adr: list[int] = []
        self._arm_qvel_adr: list[int] = []
        for name in self.spec.arm_joint_names:
            jid = mj.mj_name2id(m, mj.mjtObj.mjOBJ_JOINT, name)
            if jid < 0:
                raise ValueError(f"arm_joint_name {name!r} not in MJCF")
            jtype = int(m.jnt_type[jid])
            if jtype not in (int(mj.mjtJoint.mjJNT_HINGE), int(mj.mjtJoint.mjJNT_SLIDE)):
                raise ValueError(
                    f"joint {name!r} type={jtype} unsupported (need HINGE or SLIDE)"
                )
            self._arm_joint_ids.append(int(jid))
            self._arm_qpos_adr.append(int(m.jnt_qposadr[jid]))
            self._arm_qvel_adr.append(int(m.jnt_dofadr[jid]))

        # Arm actuators
        self._arm_actuator_ids: list[int] = []
        for name in self.spec.arm_actuator_names:
            aid = mj.mj_name2id(m, mj.mjtObj.mjOBJ_ACTUATOR, name)
            if aid < 0:
                raise ValueError(f"arm_actuator_name {name!r} not in MJCF")
            self._arm_actuator_ids.append(int(aid))

        if len(self._arm_actuator_ids) != len(self._arm_joint_ids):
            raise ValueError(
                f"arm joints ({len(self._arm_joint_ids)}) ≠ "
                f"arm actuators ({len(self._arm_actuator_ids)})"
            )
        self.dof: int = len(self._arm_joint_ids)

        # Gripper actuators (optional)
        self._gripper_actuator_ids: list[int] = []
        if self.spec.gripper_actuator_names:
            for name in self.spec.gripper_actuator_names:
                aid = mj.mj_name2id(m, mj.mjtObj.mjOBJ_ACTUATOR, name)
                if aid < 0:
                    raise ValueError(f"gripper_actuator_name {name!r} not in MJCF")
                self._gripper_actuator_ids.append(int(aid))

        # Grasp backend (codex F2): weld / contact / noop, picked from spec
        self.grasp_backend: GraspBackend = make_grasp_backend(self.spec)
        self.grasp_backend.setup(self)
        # Back-compat: expose the weld-eq map (if any) under the old attribute
        # name so existing inline introspection / tests still see it.
        self._weld_eq_ids: dict[str, int] = getattr(
            self.grasp_backend, "_weld_eq_ids", {}
        )

        # Home pose: explicit spec wins; fall back to qpos0 slice for arm joints
        if self.spec.home_qpos is not None:
            if len(self.spec.home_qpos) != self.dof:
                raise ValueError(
                    f"home_qpos length {len(self.spec.home_qpos)} ≠ dof {self.dof}"
                )
            self._home_q = np.array(self.spec.home_qpos, dtype=np.float64)
        else:
            self._home_q = np.array(
                [float(m.qpos0[adr]) for adr in self._arm_qpos_adr], dtype=np.float64
            )

        # Joint limit vectors (in arm-joint order). Pull from spec; cross-check MJCF.
        self._q_lo = np.zeros(self.dof, dtype=np.float64)
        self._q_hi = np.zeros(self.dof, dtype=np.float64)
        for i, name in enumerate(self.spec.arm_joint_names):
            lo_hi = self.spec.joint_limits.get(name)
            if lo_hi is None:
                raise ValueError(f"joint_limits missing entry for {name!r}")
            self._q_lo[i], self._q_hi[i] = float(lo_hi[0]), float(lo_hi[1])

        # _held_body kept as a read-through property for back-compat
        # (tests / agents may still reference skel._held_body).

    # ─────────────────────────────────────────────────────────────────────
    # State queries
    # ─────────────────────────────────────────────────────────────────────

    def get_joint_positions(self) -> np.ndarray:
        return np.array([self.data.qpos[adr] for adr in self._arm_qpos_adr], dtype=np.float64)

    def get_joint_velocities(self) -> np.ndarray:
        return np.array([self.data.qvel[adr] for adr in self._arm_qvel_adr], dtype=np.float64)

    def _ee_pos_now(self) -> np.ndarray:
        """World-frame EE position from current data. Dispatches site vs body."""
        if self._ee_use_site:
            return np.array(self.data.site_xpos[self._ee_site_id], dtype=np.float64)
        return np.array(self.data.xpos[self._ee_body_id], dtype=np.float64)

    def _ee_rot_now(self) -> np.ndarray:
        """World-frame EE rotation matrix from current data."""
        if self._ee_use_site:
            return np.array(self.data.site_xmat[self._ee_site_id], dtype=np.float64).reshape(3, 3)
        return np.array(self.data.xmat[self._ee_body_id], dtype=np.float64).reshape(3, 3)

    def _ee_jac_pos(self, jacp: np.ndarray) -> None:
        """Fill jacp (3×nv) with the EE positional Jacobian. Site vs body."""
        if self._ee_use_site:
            self._mj.mj_jacSite(self.model, self.data, jacp, None, self._ee_site_id)
        else:
            self._mj.mj_jacBody(self.model, self.data, jacp, None, self._ee_body_id)

    def get_ee_pose(self) -> tuple[np.ndarray, np.ndarray]:
        """Return (xyz, R) of the EE in the world frame.
        Uses ee_site if set, else ee_body."""
        self._mj.mj_forward(self.model, self.data)
        return self._ee_pos_now(), self._ee_rot_now()

    def get_object_position(self, body_name: str) -> np.ndarray:
        bid = self._mj.mj_name2id(self.model, self._mj.mjtObj.mjOBJ_BODY, body_name)
        if bid < 0:
            raise ValueError(f"body {body_name!r} not in MJCF")
        return np.array(self.data.xpos[bid], dtype=np.float64)

    def is_holding(self) -> bool:
        return self.grasp_backend.is_holding()

    @property
    def _held_body(self) -> Optional[str]:
        """Back-compat read-through. Older tests / agent code may inspect this."""
        return self.grasp_backend.held_body()

    # ─────────────────────────────────────────────────────────────────────
    # Forward kinematics
    # ─────────────────────────────────────────────────────────────────────

    def fk(self, q: Optional[np.ndarray] = None) -> dict:
        """Forward kinematics. If `q` is None, uses current qpos.

        Returns dict with: pos (3,) and R (3, 3) of the EE site in world frame.
        Restores state if a `q` was provided (does NOT permanently change qpos).
        """
        if q is None:
            pos, R = self.get_ee_pose()
            return {"pos": pos, "R": R}

        if len(q) != self.dof:
            raise ValueError(f"fk q length {len(q)} ≠ dof {self.dof}")

        saved = np.array(self.data.qpos[:])
        try:
            for adr, val in zip(self._arm_qpos_adr, q):
                self.data.qpos[adr] = float(val)
            self._mj.mj_forward(self.model, self.data)
            pos = self._ee_pos_now()
            R = self._ee_rot_now()
        finally:
            self.data.qpos[:] = saved
            self._mj.mj_forward(self.model, self.data)
        return {"pos": pos, "R": R}

    # ─────────────────────────────────────────────────────────────────────
    # Damped Least Squares IK (position only, position+orientation in v2)
    # ─────────────────────────────────────────────────────────────────────

    def ik(
        self,
        target_xyz: np.ndarray,
        q_init: Optional[np.ndarray] = None,
        *,
        raise_on_unreachable: Optional[bool] = None,
    ) -> np.ndarray:
        """Damped-Least-Squares IK on EE site position.

        Iterates: dq = J^T (J J^T + lambda^2 I)^-1 dx,
        clipped per step, clamped to limits.

        Adaptive damping (codex review F5):  effective lambda^2 grows when the
        residual is small (avoid noise amplification near singularities) and
        when joint chatter would otherwise occur near limits.

        Unreachable target detection (codex review F5):  if max_iter is
        exhausted with residual > tolerance, raise IKUnreachableError so the
        caller (skill / Phase-3 validator) can fail loudly rather than silently
        produce a bad q. Set raise_on_unreachable=False (or
        spec.ik_raise_on_unreachable=False) to keep legacy "return best-effort"
        behavior.

        Returns the final joint vector. Does *not* permanently modify state.
        """
        target = np.asarray(target_xyz, dtype=np.float64).reshape(3)
        q = (
            np.asarray(q_init, dtype=np.float64).copy()
            if q_init is not None
            else self.get_joint_positions()
        )
        if q.shape != (self.dof,):
            raise ValueError(f"q shape {q.shape} != ({self.dof},)")

        base_damping = float(self.spec.ik_damping)
        step_clamp = float(self.spec.ik_step_clamp)
        tol = float(self.spec.ik_tolerance)
        if raise_on_unreachable is None:
            raise_on_unreachable = bool(getattr(self.spec, "ik_raise_on_unreachable", True))

        saved = np.array(self.data.qpos[:])
        last_err = float("inf")
        try:
            for _ in range(int(self.spec.ik_max_iter)):
                # Push current candidate q into MuJoCo
                for adr, val in zip(self._arm_qpos_adr, q):
                    self.data.qpos[adr] = float(val)
                self._mj.mj_forward(self.model, self.data)

                ee_pos = self._ee_pos_now()
                err = target - ee_pos
                err_norm = float(np.linalg.norm(err))
                last_err = err_norm
                if err_norm < tol:
                    break

                # EE positional Jacobian (3, nv) — site- or body-based
                jacp = np.zeros((3, self.model.nv), dtype=np.float64)
                self._ee_jac_pos(jacp)
                J = jacp[:, self._arm_qvel_adr]  # (3, dof)

                # Adaptive damping: lambda^2 grows when ||err|| or sigma_min(J)
                # gets small. Cheap heuristic: scale damping^2 with 1/err_norm
                # so far-from-target moves get aggressive correction and
                # near-singularity moves get smoothed.
                damp = base_damping * max(1.0, 0.05 / max(err_norm, 1e-6))
                JJt = J @ J.T + (damp ** 2) * np.eye(3)
                dq = J.T @ np.linalg.solve(JJt, err)

                # Step clamp + joint-limit clamp
                norm_dq = float(np.linalg.norm(dq))
                if norm_dq > step_clamp:
                    dq *= step_clamp / norm_dq
                q = np.clip(q + dq, self._q_lo, self._q_hi)
        finally:
            self.data.qpos[:] = saved
            self._mj.mj_forward(self.model, self.data)

        if last_err > tol and raise_on_unreachable:
            raise IKUnreachableError(residual=last_err, tolerance=tol, q_final=q)
        return q

    # ─────────────────────────────────────────────────────────────────────
    # Actuation primitives
    # ─────────────────────────────────────────────────────────────────────

    def set_arm_actuators(self, q: np.ndarray) -> None:
        """Set actuator targets (assumes position-control actuators)."""
        if len(q) != self.dof:
            raise ValueError(f"set_arm_actuators q length {len(q)} ≠ dof {self.dof}")
        for aid, val in zip(self._arm_actuator_ids, q):
            self.data.ctrl[aid] = float(val)

    def move_joints(self, q_target: np.ndarray, duration: float = 2.0) -> bool:
        """Smoothly drive actuators from current target to q_target over `duration`.

        Implementation: linearly interpolate the actuator-target signal so the
        underlying PD controller produces a smooth trajectory. Returns True
        when the loop finishes; does not block on real-time when realtime=False.
        """
        q_target = np.asarray(q_target, dtype=np.float64)
        if q_target.shape != (self.dof,):
            raise ValueError(f"q_target shape {q_target.shape} != ({self.dof},)")
        q_target = np.clip(q_target, self._q_lo, self._q_hi)

        start = np.array(
            [float(self.data.ctrl[aid]) for aid in self._arm_actuator_ids], dtype=np.float64
        )

        dt = float(self.model.opt.timestep)
        n_steps = max(1, int(duration / dt))

        for i in range(n_steps):
            t = (i + 1) / n_steps
            interp = start + t * (q_target - start)
            self.set_arm_actuators(interp)
            # Route through self.step() so instrumentation (frame capture,
            # tracing, profiling) wrapping step() sees every sim tick.
            # Calling self._mj.mj_step directly bypasses those wrappers.
            self.step(1)
        return True

    def move_cartesian(self, target_xyz: np.ndarray, duration: float = 2.0) -> bool:
        """IK to target_xyz, then move_joints there over `duration`."""
        q_t = self.ik(target_xyz)
        return self.move_joints(q_t, duration=duration)

    def home(self, duration: float = 2.0) -> bool:
        """Move arm to home_qpos."""
        return self.move_joints(self._home_q, duration=duration)

    # ─────────────────────────────────────────────────────────────────────
    # Gripper + weld-based sim grasp
    # ─────────────────────────────────────────────────────────────────────

    def gripper_open(self, settle_steps: Optional[int] = None) -> bool:
        """Open gripper + release any held object via the grasp backend."""
        if settle_steps is not None:
            # Caller override; otherwise backend uses spec.gripper_settle_steps
            self.spec.gripper_settle_steps = int(settle_steps)
        self.grasp_backend.disengage(self)
        return True

    def gripper_close(self, settle_steps: Optional[int] = None) -> bool:
        """Close gripper + attempt grasp. Returns True iff the backend reports
        it is now holding a body. (For NoOp / no-weld setups with no target
        bodies within radius, returns False — the gripper still closed.)
        """
        if settle_steps is not None:
            self.spec.gripper_settle_steps = int(settle_steps)
        held = self.grasp_backend.engage(self)
        return held is not None

    # ─────────────────────────────────────────────────────────────────────
    # Debug / introspection
    # ─────────────────────────────────────────────────────────────────────

    def describe(self) -> dict:
        """Return a JSON-serializable summary — used by Phase 3 validators."""
        return {
            "dof": self.dof,
            "arm_joints": self.spec.arm_joint_names,
            "arm_actuators": self.spec.arm_actuator_names,
            "ee_ref": (
                {"kind": "site", "name": self.spec.ee_site_name}
                if self._ee_use_site
                else {"kind": "body", "name": self.spec.ee_body_name}
            ),
            "joint_limits": {n: list(v) for n, v in self.spec.joint_limits.items()},
            "home_qpos": self._home_q.tolist(),
            "gripper_actuators": self.spec.gripper_actuator_names or [],
            "grasp": self.grasp_backend.describe(),
            "ik": {
                "damping": self.spec.ik_damping,
                "max_iter": self.spec.ik_max_iter,
                "tolerance": self.spec.ik_tolerance,
                "step_clamp": self.spec.ik_step_clamp,
            },
        }
