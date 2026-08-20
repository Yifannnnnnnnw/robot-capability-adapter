"""Capability-neutral serial-arm primitives for the Direct-MuJoCo mainline.

The Framework supplies the canonical MuJoCo ``model`` and ``data`` objects.
This module contains reusable kinematics and actuator control only.  It does
not know which capability names TGCD will design, and it does not own scene
construction or trial reset.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

from autoadapter2.driver_synthesis import SessionBoundSkeleton


def _nonempty_name(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _finite(value: float, field_name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


@dataclass(frozen=True)
class ArmSpec:
    """Robot-specific names and numerical limits needed by the arm skeleton.

    Names and limits are public morphology/configuration facts.  The spec does
    not contain task effects or generated capability contracts.
    """

    ee_site_name: str | None = None
    ee_body_name: str | None = None
    ee_geom_name: str | None = None
    arm_joint_names: Sequence[str] = field(default_factory=tuple)
    arm_actuator_names: Sequence[str] = field(default_factory=tuple)
    joint_limits: Mapping[str, Sequence[float]] = field(default_factory=dict)
    home_qpos: Sequence[float] | None = None
    ik_damping: float = 1e-3
    ik_max_iter: int = 30
    ik_tolerance: float = 1e-3
    ik_step_clamp: float = 0.2
    gripper_actuator_names: Sequence[str] | None = None
    gripper_close_ctrl: float = 0.0
    gripper_open_ctrl: float = 1.0
    gripper_settle_steps: int = 30

    def __post_init__(self) -> None:
        site = _nonempty_name(self.ee_site_name, "ee_site_name")
        body = _nonempty_name(self.ee_body_name, "ee_body_name")
        geom = _nonempty_name(self.ee_geom_name, "ee_geom_name")
        if sum(value is not None for value in (site, body, geom)) != 1:
            raise ValueError(
                "set exactly one of ee_site_name, ee_body_name, or ee_geom_name"
            )

        joints = tuple(self.arm_joint_names)
        actuators = tuple(self.arm_actuator_names)
        if not joints:
            raise ValueError("arm_joint_names must not be empty")
        if not actuators:
            raise ValueError("arm_actuator_names must not be empty")
        if len(joints) != len(actuators):
            raise ValueError("arm joints and actuators must have equal length")
        if any(not isinstance(name, str) or not name.strip() for name in joints):
            raise ValueError("arm_joint_names must contain non-empty strings")
        if any(not isinstance(name, str) or not name.strip() for name in actuators):
            raise ValueError("arm_actuator_names must contain non-empty strings")
        if len(set(joints)) != len(joints) or len(set(actuators)) != len(actuators):
            raise ValueError("arm joint and actuator names must be unique")

        normalized_limits: dict[str, tuple[float, float]] = {}
        for name in joints:
            if name not in self.joint_limits:
                raise ValueError(f"joint_limits missing entry for {name!r}")
            pair = tuple(self.joint_limits[name])
            if len(pair) != 2:
                raise ValueError(f"joint limit for {name!r} must be (lower, upper)")
            lower = _finite(pair[0], f"joint_limits[{name!r}][0]")
            upper = _finite(pair[1], f"joint_limits[{name!r}][1]")
            if lower >= upper:
                raise ValueError(f"joint limit for {name!r} must be increasing")
            normalized_limits[name] = (lower, upper)

        home = None if self.home_qpos is None else tuple(float(value) for value in self.home_qpos)
        if home is not None:
            if len(home) != len(joints):
                raise ValueError("home_qpos must match arm_joint_names length")
            if any(not math.isfinite(value) for value in home):
                raise ValueError("home_qpos must contain finite values")

        damping = _finite(self.ik_damping, "ik_damping")
        tolerance = _finite(self.ik_tolerance, "ik_tolerance")
        step_clamp = _finite(self.ik_step_clamp, "ik_step_clamp")
        if damping <= 0 or tolerance <= 0 or step_clamp <= 0:
            raise ValueError("IK damping, tolerance, and step clamp must be positive")
        if isinstance(self.ik_max_iter, bool) or int(self.ik_max_iter) <= 0:
            raise ValueError("ik_max_iter must be a positive integer")
        if isinstance(self.gripper_settle_steps, bool) or int(self.gripper_settle_steps) < 0:
            raise ValueError("gripper_settle_steps must be a non-negative integer")

        gripper = None
        if self.gripper_actuator_names is not None:
            gripper = tuple(self.gripper_actuator_names)
            if not gripper or any(not isinstance(name, str) or not name.strip() for name in gripper):
                raise ValueError("gripper_actuator_names must contain non-empty strings")
            if len(set(gripper)) != len(gripper):
                raise ValueError("gripper actuator names must be unique")

        close_ctrl = _finite(self.gripper_close_ctrl, "gripper_close_ctrl")
        open_ctrl = _finite(self.gripper_open_ctrl, "gripper_open_ctrl")

        object.__setattr__(self, "ee_site_name", site)
        object.__setattr__(self, "ee_body_name", body)
        object.__setattr__(self, "ee_geom_name", geom)
        object.__setattr__(self, "arm_joint_names", joints)
        object.__setattr__(self, "arm_actuator_names", actuators)
        object.__setattr__(self, "joint_limits", normalized_limits)
        object.__setattr__(self, "home_qpos", home)
        object.__setattr__(self, "ik_damping", damping)
        object.__setattr__(self, "ik_max_iter", int(self.ik_max_iter))
        object.__setattr__(self, "ik_tolerance", tolerance)
        object.__setattr__(self, "ik_step_clamp", step_clamp)
        object.__setattr__(self, "gripper_actuator_names", gripper)
        object.__setattr__(self, "gripper_close_ctrl", close_ctrl)
        object.__setattr__(self, "gripper_open_ctrl", open_ctrl)
        object.__setattr__(self, "gripper_settle_steps", int(self.gripper_settle_steps))


class IKUnreachableError(RuntimeError):
    """Raised when DLS cannot reach a target within the configured tolerance."""

    def __init__(self, residual: float, tolerance: float, q_final: Any) -> None:
        super().__init__(
            f"IK did not converge: residual={residual:.6f} > tolerance={tolerance:.6f}"
        )
        self.residual = float(residual)
        self.tolerance = float(tolerance)
        self.q_final = q_final


class ArmSerialDLSSkeleton(SessionBoundSkeleton):
    """Trusted DLS and actuator primitives bound to one canonical session."""

    def __init__(self, *, model: Any, data: Any, spec: ArmSpec) -> None:
        if not isinstance(spec, ArmSpec):
            raise TypeError("spec must be an ArmSpec")
        super().__init__(model=model, data=data, spec=spec)
        self._mj: Any = None
        self._np: Any = None
        self._resolved = False
        self._ee_site_id = -1
        self._ee_body_id = -1
        self._ee_geom_id = -1
        self._ee_use_site = False
        self._ee_use_geom = False
        self._arm_qpos_adr: list[int] = []
        self._arm_qvel_adr: list[int] = []
        self._arm_actuator_ids: list[int] = []
        self._gripper_actuator_ids: list[int] = []
        self._q_lo: Any = None
        self._q_hi: Any = None
        self._home_q: Any = None
        self._dof = len(spec.arm_joint_names)
        self._timestep = 0.0

    def _load_numpy(self) -> Any:
        if self._np is None:
            try:
                import numpy as np
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("NumPy is required for arm numerical operations") from exc
            self._np = np
        return self._np

    def _load_mujoco(self) -> Any:
        if self._mj is None:
            try:
                import mujoco
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("MuJoCo is required for arm session operations") from exc
            self._mj = mujoco
        return self._mj

    def _resolve_indices(self) -> None:
        if self._resolved:
            return

        mj = self._load_mujoco()
        np = self._load_numpy()
        model = self.model

        if self.spec.ee_site_name is not None:
            self._ee_site_id = int(
                mj.mj_name2id(model, mj.mjtObj.mjOBJ_SITE, self.spec.ee_site_name)
            )
            if self._ee_site_id < 0:
                raise ValueError(f"EE site {self.spec.ee_site_name!r} is absent from the model")
            self._ee_use_site = True
        elif self.spec.ee_geom_name is not None:
            self._ee_geom_id = int(
                mj.mj_name2id(model, mj.mjtObj.mjOBJ_GEOM, self.spec.ee_geom_name)
            )
            if self._ee_geom_id < 0:
                raise ValueError(f"EE geom {self.spec.ee_geom_name!r} is absent from the model")
            self._ee_use_geom = True
        else:
            self._ee_body_id = int(
                mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, self.spec.ee_body_name)
            )
            if self._ee_body_id < 0:
                raise ValueError(f"EE body {self.spec.ee_body_name!r} is absent from the model")

        self._arm_qpos_adr = []
        self._arm_qvel_adr = []
        for name in self.spec.arm_joint_names:
            joint_id = int(mj.mj_name2id(model, mj.mjtObj.mjOBJ_JOINT, name))
            if joint_id < 0:
                raise ValueError(f"arm joint {name!r} is absent from the model")
            joint_type = int(model.jnt_type[joint_id])
            allowed = (
                int(mj.mjtJoint.mjJNT_HINGE),
                int(mj.mjtJoint.mjJNT_SLIDE),
            )
            if joint_type not in allowed:
                raise ValueError(f"arm joint {name!r} must be hinge or slide")
            self._arm_qpos_adr.append(int(model.jnt_qposadr[joint_id]))
            self._arm_qvel_adr.append(int(model.jnt_dofadr[joint_id]))

        self._arm_actuator_ids = []
        for name in self.spec.arm_actuator_names:
            actuator_id = int(mj.mj_name2id(model, mj.mjtObj.mjOBJ_ACTUATOR, name))
            if actuator_id < 0:
                raise ValueError(f"arm actuator {name!r} is absent from the model")
            self._arm_actuator_ids.append(actuator_id)

        self._gripper_actuator_ids = []
        for name in self.spec.gripper_actuator_names or ():
            actuator_id = int(mj.mj_name2id(model, mj.mjtObj.mjOBJ_ACTUATOR, name))
            if actuator_id < 0:
                raise ValueError(f"gripper actuator {name!r} is absent from the model")
            self._gripper_actuator_ids.append(actuator_id)

        self._q_lo = np.asarray(
            [self.spec.joint_limits[name][0] for name in self.spec.arm_joint_names],
            dtype=float,
        )
        self._q_hi = np.asarray(
            [self.spec.joint_limits[name][1] for name in self.spec.arm_joint_names],
            dtype=float,
        )
        if self.spec.home_qpos is None:
            self._home_q = np.asarray(
                [model.qpos0[address] for address in self._arm_qpos_adr], dtype=float
            )
        else:
            self._home_q = np.asarray(self.spec.home_qpos, dtype=float)

        self._timestep = float(model.opt.timestep)
        if not math.isfinite(self._timestep) or self._timestep <= 0:
            raise ValueError("model.opt.timestep must be positive")
        self._resolved = True

    def _vector(self, value: Sequence[float], field_name: str) -> Any:
        self._resolve_indices()
        np = self._load_numpy()
        result = np.asarray(value, dtype=float)
        if result.shape != (self._dof,):
            raise ValueError(f"{field_name} must have shape ({self._dof},)")
        if not np.all(np.isfinite(result)):
            raise ValueError(f"{field_name} must contain finite values")
        return result

    def _xyz(self, value: Sequence[float]) -> Any:
        np = self._load_numpy()
        result = np.asarray(value, dtype=float)
        if result.shape != (3,):
            raise ValueError("target_xyz must have shape (3,)")
        if not np.all(np.isfinite(result)):
            raise ValueError("target_xyz must contain finite values")
        return result

    def _ee_position(self) -> Any:
        np = self._load_numpy()
        if self._ee_use_site:
            return np.array(self.data.site_xpos[self._ee_site_id], dtype=float, copy=True)
        if self._ee_use_geom:
            return np.array(self.data.geom_xpos[self._ee_geom_id], dtype=float, copy=True)
        return np.array(self.data.xpos[self._ee_body_id], dtype=float, copy=True)

    def _ee_rotation(self) -> Any:
        np = self._load_numpy()
        if self._ee_use_site:
            return np.array(self.data.site_xmat[self._ee_site_id], dtype=float, copy=True).reshape(3, 3)
        if self._ee_use_geom:
            return np.array(self.data.geom_xmat[self._ee_geom_id], dtype=float, copy=True).reshape(3, 3)
        return np.array(self.data.xmat[self._ee_body_id], dtype=float, copy=True).reshape(3, 3)

    def _ee_position_jacobian(self, jacobian: Any) -> None:
        if self._ee_use_site:
            self._mj.mj_jacSite(self.model, self.data, jacobian, None, self._ee_site_id)
        elif self._ee_use_geom:
            self._mj.mj_jacGeom(self.model, self.data, jacobian, None, self._ee_geom_id)
        else:
            self._mj.mj_jacBody(self.model, self.data, jacobian, None, self._ee_body_id)

    @contextmanager
    def _temporary_joint_positions(self, q: Any) -> Iterator[None]:
        """Temporarily place a candidate pose for FK/IK analysis only."""

        np = self._load_numpy()
        saved_qpos = np.array(self.data.qpos, dtype=float, copy=True)
        try:
            for address, value in zip(self._arm_qpos_adr, q):
                self.data.qpos[address] = float(value)
            self._mj.mj_forward(self.model, self.data)
            yield
        finally:
            self.data.qpos[:] = saved_qpos
            self._mj.mj_forward(self.model, self.data)

    def _physics_step(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError("physics step count must be non-negative")
        for _ in range(count):
            self._mj.mj_step(self.model, self.data)

    def get_joint_positions(self) -> Any:
        """Read the arm joint positions from the canonical session."""

        self._resolve_indices()
        np = self._load_numpy()
        return np.asarray([self.data.qpos[address] for address in self._arm_qpos_adr], dtype=float)

    def get_joint_velocities(self) -> Any:
        """Read the arm joint velocities from the canonical session."""

        self._resolve_indices()
        np = self._load_numpy()
        return np.asarray([self.data.qvel[address] for address in self._arm_qvel_adr], dtype=float)

    def get_ee_pose(self) -> tuple[Any, Any]:
        """Return the current world-frame end-effector position and rotation."""

        self._resolve_indices()
        self._mj.mj_forward(self.model, self.data)
        return self._ee_position(), self._ee_rotation()

    def fk(self, q: Sequence[float] | None = None) -> dict[str, Any]:
        """Compute EE pose, restoring the canonical state after analysis."""

        self._resolve_indices()
        if q is None:
            position, rotation = self.get_ee_pose()
            return {"pos": position, "R": rotation}
        candidate = self._vector(q, "q")
        with self._temporary_joint_positions(candidate):
            position = self._ee_position()
            rotation = self._ee_rotation()
        return {"pos": position, "R": rotation}

    def ik(self, target_xyz: Sequence[float], q_init: Sequence[float] | None = None) -> Any:
        """Solve position-only damped-least-squares IK without executing it."""

        self._resolve_indices()
        np = self._load_numpy()
        target = self._xyz(target_xyz)
        q = self.get_joint_positions() if q_init is None else self._vector(q_init, "q_init")
        last_residual = math.inf

        for _ in range(self.spec.ik_max_iter):
            with self._temporary_joint_positions(q):
                error = target - self._ee_position()
                last_residual = float(np.linalg.norm(error))
                if last_residual <= self.spec.ik_tolerance:
                    return q.copy()

                jacobian = np.zeros((3, int(self.model.nv)), dtype=float)
                self._ee_position_jacobian(jacobian)
                arm_jacobian = jacobian[:, self._arm_qvel_adr]

            damping = self.spec.ik_damping * max(
                1.0, 0.05 / max(last_residual, 1e-6)
            )
            system = arm_jacobian @ arm_jacobian.T + (damping**2) * np.eye(3)
            delta = arm_jacobian.T @ np.linalg.solve(system, error)
            delta_norm = float(np.linalg.norm(delta))
            if delta_norm > self.spec.ik_step_clamp:
                delta *= self.spec.ik_step_clamp / delta_norm
            q = np.clip(q + delta, self._q_lo, self._q_hi)

        if last_residual > self.spec.ik_tolerance:
            raise IKUnreachableError(last_residual, self.spec.ik_tolerance, q.copy())
        return q.copy()

    def set_arm_actuators(self, q: Sequence[float]) -> None:
        """Write arm actuator targets to the canonical data.ctrl array."""

        q_target = self._vector(q, "q")
        for actuator_id, value in zip(self._arm_actuator_ids, q_target):
            self.data.ctrl[actuator_id] = float(value)

    def move_joints(self, q_target: Sequence[float], duration: float = 2.0) -> bool:
        """Interpolate actuator targets and advance the real physics session."""

        self._resolve_indices()
        target = self._vector(q_target, "q_target")
        duration_value = _finite(duration, "duration")
        if duration_value < 0:
            raise ValueError("duration must be non-negative")
        np = self._load_numpy()
        target = np.clip(target, self._q_lo, self._q_hi)
        start = np.asarray(
            [self.data.ctrl[actuator_id] for actuator_id in self._arm_actuator_ids],
            dtype=float,
        )
        steps = max(1, int(math.ceil(duration_value / self._timestep)))
        for index in range(steps):
            fraction = (index + 1) / steps
            self.set_arm_actuators(start + fraction * (target - start))
            self._physics_step(1)
        return True

    def move_cartesian(
        self,
        target_xyz: Sequence[float],
        duration: float = 2.0,
        *,
        wrist_roll: float | None = None,
        gain: float = 1.8,
        max_joint_delta: float = 0.12,
        residual_tolerance: float = 0.12,
    ) -> bool:
        """Track a Cartesian target with actuator-only closed-loop DLS control."""

        self._resolve_indices()
        np = self._load_numpy()
        target = self._xyz(target_xyz)
        duration_value = _finite(duration, "duration")
        gain_value = _finite(gain, "gain")
        delta_limit = _finite(max_joint_delta, "max_joint_delta")
        residual_limit = _finite(residual_tolerance, "residual_tolerance")
        if duration_value < 0.0:
            raise ValueError("duration must be non-negative")
        if gain_value <= 0.0 or delta_limit <= 0.0 or residual_limit <= 0.0:
            raise ValueError(
                "gain, max_joint_delta, and residual_tolerance must be positive"
            )

        pinned_roll: float | None = None
        controlled_count = self._dof
        if wrist_roll is not None:
            pinned_roll = float(
                np.clip(_finite(wrist_roll, "wrist_roll"), self._q_lo[-1], self._q_hi[-1])
            )
            controlled_count -= 1
            if controlled_count < 1:
                raise ValueError("wrist_roll pinning requires at least two arm joints")

        steps = max(1, int(math.ceil(duration_value / self._timestep)))
        last_residual = math.inf
        for _ in range(steps):
            self._mj.mj_forward(self.model, self.data)
            error = target - self._ee_position()
            last_residual = float(np.linalg.norm(error))
            current = self.get_joint_positions()
            roll_reached = pinned_roll is None or abs(float(current[-1]) - pinned_roll) <= 0.01
            if last_residual <= self.spec.ik_tolerance and roll_reached:
                hold_target = current.copy()
                if pinned_roll is not None:
                    hold_target[-1] = pinned_roll
                self.set_arm_actuators(hold_target)
                self._physics_step(1)
                return True

            jacobian = np.zeros((3, int(self.model.nv)), dtype=float)
            self._ee_position_jacobian(jacobian)
            controlled_addresses = self._arm_qvel_adr[:controlled_count]
            arm_jacobian = jacobian[:, controlled_addresses]
            damping = self.spec.ik_damping * max(
                1.0, 0.05 / max(last_residual, 1e-6)
            )
            system = arm_jacobian @ arm_jacobian.T + (damping**2) * np.eye(3)
            delta = arm_jacobian.T @ np.linalg.solve(system, error)
            delta_norm = float(np.linalg.norm(delta))
            if delta_norm > delta_limit:
                delta *= delta_limit / delta_norm
            desired = current.copy()
            desired[:controlled_count] += gain_value * delta
            if pinned_roll is not None:
                desired[-1] = pinned_roll
            self.set_arm_actuators(np.clip(desired, self._q_lo, self._q_hi))
            self._physics_step(1)

        self._mj.mj_forward(self.model, self.data)
        last_residual = float(np.linalg.norm(target - self._ee_position()))
        if last_residual > residual_limit:
            raise IKUnreachableError(
                last_residual,
                residual_limit,
                self.get_joint_positions().copy(),
            )
        return True

    def home(self, duration: float = 2.0) -> bool:
        """Drive the arm to the configured home pose through actuators."""

        self._resolve_indices()
        return self.move_joints(self._home_q, duration=duration)

    def hold(self, steps: int = 30) -> bool:
        """Hold the current actuator targets while advancing real physics."""

        self._resolve_indices()
        if isinstance(steps, bool) or int(steps) < 0:
            raise ValueError("steps must be a non-negative integer")
        self._physics_step(max(1, int(steps)))
        return True

    def set_gripper(self, control: float, settle_steps: int | None = None) -> bool:
        """Command a finite public gripper target and advance real physics."""

        self._resolve_indices()
        if not self._gripper_actuator_ids:
            return False
        control_value = _finite(control, "control")
        for actuator_id in self._gripper_actuator_ids:
            self.data.ctrl[actuator_id] = control_value
        steps = self.spec.gripper_settle_steps if settle_steps is None else int(settle_steps)
        if steps < 0:
            raise ValueError("settle_steps must be non-negative")
        self._physics_step(max(1, steps))
        return True

    def gripper_open(self, settle_steps: int | None = None) -> bool:
        """Command the configured physical gripper actuators open."""

        return self.set_gripper(self.spec.gripper_open_ctrl, settle_steps)

    def gripper_close(self, settle_steps: int | None = None) -> bool:
        """Command the configured physical gripper actuators closed."""

        return self.set_gripper(self.spec.gripper_close_ctrl, settle_steps)
