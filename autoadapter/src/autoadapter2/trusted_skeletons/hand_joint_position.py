"""Capability-neutral joint-position primitives for a fixed-base hand."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from autoadapter2.driver_synthesis import SessionBoundSkeleton


def _name(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _items(value: object, field_name: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field_name} must be a sequence")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field_name} must be a sequence") from exc


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _positive_finite(value: object, field_name: str) -> float:
    result = _finite(value, field_name)
    if result <= 0.0:
        raise ValueError(f"{field_name} must be positive")
    return result


@dataclass(frozen=True)
class HandJointPositionSpec:
    """Public names and bounded joint-position control facts for one hand."""

    palm_body_name: str
    joint_names: tuple[str, ...]
    actuator_names: tuple[str, ...]
    finger_joint_names: Mapping[str, tuple[str, ...]]
    fingertip_geom_names: Mapping[str, str]
    home_qpos: tuple[float, ...] | None = None
    max_joint_command_delta: float = 0.1
    joint_tolerance_rad: float = 0.02

    def __post_init__(self) -> None:
        palm = _name(self.palm_body_name, "palm_body_name")
        joints = tuple(
            _name(value, "joint_names")
            for value in _items(self.joint_names, "joint_names")
        )
        actuators = tuple(
            _name(value, "actuator_names")
            for value in _items(self.actuator_names, "actuator_names")
        )
        if not joints:
            raise ValueError("joint_names must not be empty")
        if not actuators:
            raise ValueError("actuator_names must not be empty")
        if len(joints) != len(actuators):
            raise ValueError("joint_names and actuator_names must have equal length")
        if len(set(joints)) != len(joints):
            raise ValueError("joint_names must be unique")
        if len(set(actuators)) != len(actuators):
            raise ValueError("actuator_names must be unique")

        if not isinstance(self.finger_joint_names, Mapping):
            raise ValueError("finger_joint_names must be a mapping")
        if not isinstance(self.fingertip_geom_names, Mapping):
            raise ValueError("fingertip_geom_names must be a mapping")

        finger_joints: dict[str, tuple[str, ...]] = {}
        for raw_finger, raw_names in self.finger_joint_names.items():
            finger = _name(raw_finger, "finger_joint_names key")
            if finger in finger_joints:
                raise ValueError("finger names must be unique")
            names = tuple(
                _name(value, f"finger_joint_names[{finger!r}]")
                for value in _items(raw_names, f"finger_joint_names[{finger!r}]")
            )
            if not names:
                raise ValueError(f"finger_joint_names[{finger!r}] must not be empty")
            if len(set(names)) != len(names):
                raise ValueError(f"finger joints for {finger!r} must be unique")
            finger_joints[finger] = names

        fingertip_geoms: dict[str, str] = {}
        for raw_finger, raw_geom in self.fingertip_geom_names.items():
            finger = _name(raw_finger, "fingertip_geom_names key")
            if finger in fingertip_geoms:
                raise ValueError("fingertip finger names must be unique")
            fingertip_geoms[finger] = _name(
                raw_geom, f"fingertip_geom_names[{finger!r}]"
            )

        if set(finger_joints) != set(fingertip_geoms):
            raise ValueError(
                "finger_joint_names and fingertip_geom_names must have matching keys"
            )
        flattened = tuple(
            joint_name for finger in finger_joints for joint_name in finger_joints[finger]
        )
        if len(flattened) != len(joints) or set(flattened) != set(joints):
            raise ValueError(
                "finger_joint_names must partition joint_names exactly once"
            )
        geom_names = tuple(fingertip_geoms.values())
        if len(set(geom_names)) != len(geom_names):
            raise ValueError("fingertip geometry names must be unique")

        home: tuple[float, ...] | None
        if self.home_qpos is None:
            home = None
        else:
            home_values = _items(self.home_qpos, "home_qpos")
            home = tuple(_finite(value, "home_qpos") for value in home_values)
            if len(home) != len(joints):
                raise ValueError("home_qpos must match joint_names length")

        max_delta = _positive_finite(
            self.max_joint_command_delta, "max_joint_command_delta"
        )
        tolerance = _positive_finite(self.joint_tolerance_rad, "joint_tolerance_rad")

        object.__setattr__(self, "palm_body_name", palm)
        object.__setattr__(self, "joint_names", joints)
        object.__setattr__(self, "actuator_names", actuators)
        object.__setattr__(self, "finger_joint_names", finger_joints)
        object.__setattr__(self, "fingertip_geom_names", fingertip_geoms)
        object.__setattr__(self, "home_qpos", home)
        object.__setattr__(self, "max_joint_command_delta", max_delta)
        object.__setattr__(self, "joint_tolerance_rad", tolerance)


class HandJointPositionSkeleton(SessionBoundSkeleton):
    """Trusted joint observations and bounded position control for one hand."""

    def __init__(
        self,
        *,
        model: Any,
        data: Any,
        spec: HandJointPositionSpec,
    ) -> None:
        if not isinstance(spec, HandJointPositionSpec):
            raise TypeError("spec must be a HandJointPositionSpec")
        super().__init__(model=model, data=data, spec=spec)
        self._mj: Any = None
        self._np: Any = None
        self._resolved = False
        self._palm_body_id = -1
        self._finger_order: tuple[str, ...] = ()
        self._joint_ids: tuple[int, ...] = ()
        self._joint_qpos_adr: tuple[int, ...] = ()
        self._actuator_ids: tuple[int, ...] = ()
        self._geom_ids: dict[str, int] = {}
        self._joint_low: Any = None
        self._joint_high: Any = None
        self._ctrl_low: Any = None
        self._ctrl_high: Any = None
        self._command_low: Any = None
        self._command_high: Any = None
        self._home_q: Any = None
        self._timestep = 0.0

    def _load_numpy(self) -> Any:
        if self._np is None:
            try:
                import numpy as np
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("NumPy is required for hand operations") from exc
            self._np = np
        return self._np

    def _load_mujoco(self) -> Any:
        if self._mj is None:
            try:
                import mujoco
            except Exception as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("MuJoCo is required for hand operations") from exc
            self._mj = mujoco
        return self._mj

    def _resolve(self) -> None:
        if self._resolved:
            return

        mj = self._load_mujoco()
        np = self._load_numpy()
        model = self.model

        palm_body_id = int(
            mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, self.spec.palm_body_name)
        )
        if palm_body_id < 0:
            raise ValueError(
                f"palm body {self.spec.palm_body_name!r} is absent from the model"
            )

        joint_ids: list[int] = []
        joint_qpos_adr: list[int] = []
        joint_low: list[float] = []
        joint_high: list[float] = []
        for joint_name in self.spec.joint_names:
            joint_id = int(mj.mj_name2id(model, mj.mjtObj.mjOBJ_JOINT, joint_name))
            if joint_id < 0:
                raise ValueError(f"joint {joint_name!r} is absent from the model")
            if int(model.jnt_type[joint_id]) != int(mj.mjtJoint.mjJNT_HINGE):
                raise ValueError(f"joint {joint_name!r} must be a hinge joint")
            if not bool(model.jnt_limited[joint_id]):
                raise ValueError(f"joint {joint_name!r} must have a finite range")
            lower = float(model.jnt_range[joint_id, 0])
            upper = float(model.jnt_range[joint_id, 1])
            if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
                raise ValueError(f"joint {joint_name!r} has an invalid range")
            joint_ids.append(joint_id)
            joint_qpos_adr.append(int(model.jnt_qposadr[joint_id]))
            joint_low.append(lower)
            joint_high.append(upper)

        actuator_ids: list[int] = []
        ctrl_low: list[float] = []
        ctrl_high: list[float] = []
        for joint_id, actuator_name in zip(
            joint_ids, self.spec.actuator_names, strict=True
        ):
            actuator_id = int(
                mj.mj_name2id(model, mj.mjtObj.mjOBJ_ACTUATOR, actuator_name)
            )
            if actuator_id < 0:
                raise ValueError(f"actuator {actuator_name!r} is absent from the model")
            if int(model.actuator_trntype[actuator_id]) != int(mj.mjtTrn.mjTRN_JOINT):
                raise ValueError(
                    f"actuator {actuator_name!r} must use joint transmission"
                )
            if int(model.actuator_trnid[actuator_id, 0]) != joint_id:
                raise ValueError(
                    f"actuator {actuator_name!r} is not mapped to its configured joint"
                )
            if not bool(model.actuator_ctrllimited[actuator_id]):
                raise ValueError(
                    f"actuator {actuator_name!r} must have a finite ctrlrange"
                )
            lower = float(model.actuator_ctrlrange[actuator_id, 0])
            upper = float(model.actuator_ctrlrange[actuator_id, 1])
            if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
                raise ValueError(f"actuator {actuator_name!r} has an invalid ctrlrange")
            actuator_ids.append(actuator_id)
            ctrl_low.append(lower)
            ctrl_high.append(upper)

        geom_ids: dict[str, int] = {}
        for finger in self.spec.fingertip_geom_names:
            geom_name = self.spec.fingertip_geom_names[finger]
            geom_id = int(
                mj.mj_name2id(model, mj.mjtObj.mjOBJ_GEOM, geom_name)
            )
            if geom_id < 0:
                raise ValueError(
                    f"fingertip geometry {geom_name!r} is absent from the model"
                )
            geom_ids[finger] = geom_id

        joint_low_array = np.asarray(joint_low, dtype=float)
        joint_high_array = np.asarray(joint_high, dtype=float)
        ctrl_low_array = np.asarray(ctrl_low, dtype=float)
        ctrl_high_array = np.asarray(ctrl_high, dtype=float)
        command_low = np.maximum(joint_low_array, ctrl_low_array)
        command_high = np.minimum(joint_high_array, ctrl_high_array)
        if np.any(command_low > command_high):
            raise ValueError("joint and actuator ranges do not overlap")

        home = (
            np.asarray(
                [model.qpos0[address] for address in joint_qpos_adr], dtype=float
            )
            if self.spec.home_qpos is None
            else np.asarray(self.spec.home_qpos, dtype=float)
        )
        if not np.all(np.isfinite(home)):
            raise ValueError("home_qpos must contain finite values")

        timestep = float(model.opt.timestep)
        if not math.isfinite(timestep) or timestep <= 0.0:
            raise ValueError("model.opt.timestep must be positive")

        self._palm_body_id = palm_body_id
        self._finger_order = tuple(self.spec.fingertip_geom_names)
        self._joint_ids = tuple(joint_ids)
        self._joint_qpos_adr = tuple(joint_qpos_adr)
        self._actuator_ids = tuple(actuator_ids)
        self._geom_ids = geom_ids
        self._joint_low = joint_low_array
        self._joint_high = joint_high_array
        self._ctrl_low = ctrl_low_array
        self._ctrl_high = ctrl_high_array
        self._command_low = command_low
        self._command_high = command_high
        self._home_q = home
        self._timestep = timestep
        self._resolved = True

    def _vector(self, value: object, field_name: str) -> Any:
        self._resolve()
        np = self._load_numpy()
        try:
            result = np.asarray(value, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must contain finite values") from exc
        if result.shape != (len(self._joint_qpos_adr),) or not np.all(
            np.isfinite(result)
        ):
            raise ValueError(
                f"{field_name} must contain exactly {len(self._joint_qpos_adr)} finite values"
            )
        return result

    def _indices(self, names: object) -> tuple[int, ...]:
        if names is None:
            return tuple(range(len(self._joint_qpos_adr)))
        if isinstance(names, str):
            requested = (names,)
        else:
            requested = _items(names, "names")
        if not requested:
            raise ValueError("names must not be empty")
        index_by_name = {name: index for index, name in enumerate(self.spec.joint_names)}
        indices: list[int] = []
        for name in requested:
            normalized = _name(name, "names")
            if normalized not in index_by_name:
                raise ValueError(f"joint {normalized!r} is not configured")
            if index_by_name[normalized] in indices:
                raise ValueError("names must be unique")
            indices.append(index_by_name[normalized])
        return tuple(indices)

    def _read_positions(self) -> Any:
        np = self._load_numpy()
        return np.asarray(
            [self.data.qpos[address] for address in self._joint_qpos_adr], dtype=float
        )

    def _duration_steps(self, duration_s: object) -> int:
        if isinstance(duration_s, bool):
            raise ValueError("duration_s must be finite")
        duration = _finite(duration_s, "duration_s")
        if duration < 0.0:
            raise ValueError("duration_s must be non-negative")
        return max(1, int(math.ceil(duration / self._timestep)))

    def _tolerance(self, tolerance_rad: object | None) -> float:
        if tolerance_rad is None:
            return self.spec.joint_tolerance_rad
        return _positive_finite(tolerance_rad, "tolerance_rad")

    def _write_command(self, command: Any) -> None:
        for actuator_id, value in zip(self._actuator_ids, command, strict=True):
            self.data.ctrl[actuator_id] = float(value)

    def _run_position_control(
        self,
        target: Any,
        steps: int,
        tolerance: float,
        *,
        force_steps: bool = False,
    ) -> bool:
        np = self._load_numpy()
        mj = self._load_mujoco()
        target = np.clip(target, self._command_low, self._command_high)
        for _ in range(steps):
            current = self._read_positions()
            error = target - current
            if bool(np.all(np.abs(error) <= tolerance)):
                if not force_steps:
                    return True
                command = np.clip(current, self._command_low, self._command_high)
                self._write_command(command)
                mj.mj_step(self.model, self.data)
                continue
            delta = np.clip(
                error,
                -self.spec.max_joint_command_delta,
                self.spec.max_joint_command_delta,
            )
            command = np.clip(current + delta, self._command_low, self._command_high)
            self._write_command(command)
            mj.mj_step(self.model, self.data)
            observed = self._read_positions()
            if bool(np.all(np.abs(target - observed) <= tolerance)):
                return True
        observed = self._read_positions()
        return bool(np.all(np.abs(target - observed) <= tolerance))

    def get_joint_positions(self, names: object | None = None) -> Any:
        """Read configured hinge positions in requested order."""

        self._resolve()
        positions = self._read_positions()
        return positions[list(self._indices(names))].copy()

    def get_fingertip_positions(self, frame: str = "palm") -> dict[str, Any]:
        """Read fingertip positions in the palm or world frame."""

        self._resolve()
        if frame not in ("palm", "world"):
            raise ValueError("frame must be 'palm' or 'world'")
        mj = self._load_mujoco()
        np = self._load_numpy()
        mj.mj_forward(self.model, self.data)
        palm_position = np.asarray(self.data.xpos[self._palm_body_id], dtype=float).copy()
        palm_rotation = np.asarray(
            self.data.xmat[self._palm_body_id], dtype=float
        ).reshape(3, 3)
        positions: dict[str, Any] = {}
        for finger in self._finger_order:
            world_position = np.asarray(
                self.data.geom_xpos[self._geom_ids[finger]], dtype=float
            ).copy()
            positions[finger] = (
                world_position
                if frame == "world"
                else palm_rotation.T @ (world_position - palm_position)
            )
        return positions

    def move_joints(
        self,
        target_qpos: object,
        duration_s: float = 1.0,
        tolerance_rad: float | None = None,
    ) -> bool:
        """Track all configured joints with bounded feedback and real physics."""

        target = self._vector(target_qpos, "target_qpos")
        return self._run_position_control(
            target,
            self._duration_steps(duration_s),
            self._tolerance(tolerance_rad),
        )

    def move_named_joints(
        self,
        targets: Mapping[str, float],
        duration_s: float = 1.0,
        tolerance_rad: float | None = None,
    ) -> bool:
        """Track named joints while holding unspecified joints at observation."""

        self._resolve()
        if not isinstance(targets, Mapping) or not targets:
            raise ValueError("targets must be a non-empty mapping")
        current = self._read_positions()
        index_by_name = {name: index for index, name in enumerate(self.spec.joint_names)}
        target = current.copy()
        for raw_name, raw_value in targets.items():
            name = _name(raw_name, "targets key")
            if name not in index_by_name:
                raise ValueError(f"joint {name!r} is not configured")
            target[index_by_name[name]] = _finite(raw_value, f"targets[{name!r}]")
        return self._run_position_control(
            target,
            self._duration_steps(duration_s),
            self._tolerance(tolerance_rad),
        )

    def home(self, duration_s: float = 1.0) -> bool:
        """Track the configured home pose through the canonical actuators."""

        self._resolve()
        return self.move_joints(self._home_q, duration_s=duration_s)

    def hold(self, steps: int = 1) -> bool:
        """Hold the observed hand pose while advancing real physics steps."""

        self._resolve()
        if isinstance(steps, bool) or int(steps) != steps or steps < 0:
            raise ValueError("steps must be a non-negative integer")
        target = self._read_positions()
        return self._run_position_control(
            target, int(steps), self.spec.joint_tolerance_rad, force_steps=True
        )


__all__ = ["HandJointPositionSkeleton", "HandJointPositionSpec"]
