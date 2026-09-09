# SPDX-License-Identifier: Apache-2.0
"""Direct-MuJoCo DLS skeleton for a fixed-base four-finger hand."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any

import numpy as np

from .base import SkeletonBase


FINGER_ORDER = ("index", "middle", "ring", "thumb")


def _name(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _finite(value: object, field: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


@dataclass(frozen=True)
class HandFingertipDLSSpec:
    """MJCF names and small bounds needed by the hand control skeleton."""

    palm_body_name: str
    joint_names: tuple[str, ...]
    actuator_names: tuple[str, ...]
    fingertip_geom_names: Mapping[str, str]
    finger_joint_names: Mapping[str, tuple[str, ...]] | None = None
    home_qpos: tuple[float, ...] | None = None
    max_joint_command_delta: float = 0.1
    joint_tolerance_rad: float = 0.03
    fingertip_tolerance_m: float = 0.003
    ik_damping: float = 0.02
    ik_step_clamp: float = 0.08
    ik_position_gain: float = 1.0

    def __post_init__(self) -> None:
        palm = _name(self.palm_body_name, "palm_body_name")
        joints = tuple(_name(value, "joint_names") for value in self.joint_names)
        actuators = tuple(
            _name(value, "actuator_names") for value in self.actuator_names
        )
        if not joints or len(joints) != len(actuators):
            raise ValueError(
                "joint_names and actuator_names must be non-empty and equal length"
            )
        if len(set(joints)) != len(joints) or len(set(actuators)) != len(actuators):
            raise ValueError("joint_names and actuator_names must be unique")

        if not isinstance(self.fingertip_geom_names, Mapping):
            raise ValueError("fingertip_geom_names must be a mapping")
        fingertip_geoms = {
            _name(finger, "fingertip_geom_names key"): _name(
                geom, "fingertip_geom_names value"
            )
            for finger, geom in self.fingertip_geom_names.items()
        }
        if set(fingertip_geoms) != set(FINGER_ORDER):
            raise ValueError(
                "fingertip_geom_names must contain exactly "
                f"{list(FINGER_ORDER)!r}"
            )
        if len(set(fingertip_geoms.values())) != len(FINGER_ORDER):
            raise ValueError("fingertip geometry names must be unique")
        fingertip_geoms = {finger: fingertip_geoms[finger] for finger in FINGER_ORDER}

        if self.finger_joint_names is None:
            if len(joints) % len(FINGER_ORDER):
                raise ValueError("joint_names must split evenly across four fingers")
            count = len(joints) // len(FINGER_ORDER)
            finger_joints = {
                finger: joints[index * count : (index + 1) * count]
                for index, finger in enumerate(FINGER_ORDER)
            }
        else:
            if not isinstance(self.finger_joint_names, Mapping):
                raise ValueError("finger_joint_names must be a mapping")
            raw_finger_joints = {
                _name(finger, "finger_joint_names key"): tuple(
                    _name(value, "finger_joint_names value") for value in names
                )
                for finger, names in self.finger_joint_names.items()
            }
            if set(raw_finger_joints) != set(FINGER_ORDER):
                raise ValueError(
                    "finger_joint_names must contain exactly "
                    f"{list(FINGER_ORDER)!r}"
                )
            finger_joints = {
                finger: raw_finger_joints[finger] for finger in FINGER_ORDER
            }
        flattened = tuple(
            joint for finger in FINGER_ORDER for joint in finger_joints[finger]
        )
        if (
            not flattened
            or len(flattened) != len(joints)
            or set(flattened) != set(joints)
        ):
            raise ValueError("finger_joint_names must partition joint_names exactly once")

        home = None if self.home_qpos is None else tuple(
            _finite(value, "home_qpos") for value in self.home_qpos
        )
        if home is not None and len(home) != len(joints):
            raise ValueError("home_qpos must match joint_names length")
        for field in (
            "max_joint_command_delta",
            "joint_tolerance_rad",
            "fingertip_tolerance_m",
            "ik_damping",
            "ik_step_clamp",
            "ik_position_gain",
        ):
            value = _finite(getattr(self, field), field)
            if value <= 0.0:
                raise ValueError(f"{field} must be positive")

        object.__setattr__(self, "palm_body_name", palm)
        object.__setattr__(self, "joint_names", joints)
        object.__setattr__(self, "actuator_names", actuators)
        object.__setattr__(self, "fingertip_geom_names", fingertip_geoms)
        object.__setattr__(self, "finger_joint_names", finger_joints)
        object.__setattr__(self, "home_qpos", home)


class HandFingertipDLSSkeleton(SkeletonBase):
    """Joint-position control and four-fingertip position-only DLS."""

    def __init__(self, model: Any, data: Any, spec: HandFingertipDLSSpec) -> None:
        if not isinstance(spec, HandFingertipDLSSpec):
            raise TypeError("spec must be a HandFingertipDLSSpec")
        super().__init__(model, data, spec)
        self.spec: HandFingertipDLSSpec = spec
        self._resolve_indices()

    def _resolve_indices(self) -> None:
        mj = self._mj
        model = self.model
        self._palm_body_id = int(
            mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, self.spec.palm_body_name)
        )
        if self._palm_body_id < 0:
            raise ValueError(f"palm body {self.spec.palm_body_name!r} is absent")

        self._joint_ids: list[int] = []
        self._joint_qpos_adr: list[int] = []
        self._joint_dof_adr: list[int] = []
        joint_low: list[float] = []
        joint_high: list[float] = []
        for name in self.spec.joint_names:
            joint_id = int(mj.mj_name2id(model, mj.mjtObj.mjOBJ_JOINT, name))
            if joint_id < 0:
                raise ValueError(f"joint {name!r} is absent")
            if int(model.jnt_type[joint_id]) != int(mj.mjtJoint.mjJNT_HINGE):
                raise ValueError(f"joint {name!r} must be a hinge")
            if not bool(model.jnt_limited[joint_id]):
                raise ValueError(f"joint {name!r} must have a finite range")
            lower, upper = (float(value) for value in model.jnt_range[joint_id])
            if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
                raise ValueError(f"joint {name!r} has an invalid range")
            self._joint_ids.append(joint_id)
            self._joint_qpos_adr.append(int(model.jnt_qposadr[joint_id]))
            self._joint_dof_adr.append(int(model.jnt_dofadr[joint_id]))
            joint_low.append(lower)
            joint_high.append(upper)

        self._actuator_ids: list[int] = []
        ctrl_low: list[float] = []
        ctrl_high: list[float] = []
        for joint_id, name in zip(
            self._joint_ids, self.spec.actuator_names, strict=True
        ):
            actuator_id = int(mj.mj_name2id(model, mj.mjtObj.mjOBJ_ACTUATOR, name))
            if actuator_id < 0:
                raise ValueError(f"actuator {name!r} is absent")
            if int(model.actuator_trntype[actuator_id]) != int(
                mj.mjtTrn.mjTRN_JOINT
            ):
                raise ValueError(f"actuator {name!r} must use joint transmission")
            if int(model.actuator_trnid[actuator_id, 0]) != joint_id:
                raise ValueError(f"actuator {name!r} is mapped to the wrong joint")
            if not bool(model.actuator_ctrllimited[actuator_id]):
                raise ValueError(f"actuator {name!r} must have a finite ctrlrange")
            lower, upper = (
                float(value) for value in model.actuator_ctrlrange[actuator_id]
            )
            if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
                raise ValueError(f"actuator {name!r} has an invalid ctrlrange")
            self._actuator_ids.append(actuator_id)
            ctrl_low.append(lower)
            ctrl_high.append(upper)

        self._geom_ids: dict[str, int] = {}
        for finger in FINGER_ORDER:
            name = self.spec.fingertip_geom_names[finger]
            geom_id = int(mj.mj_name2id(model, mj.mjtObj.mjOBJ_GEOM, name))
            if geom_id < 0:
                raise ValueError(f"fingertip geometry {name!r} is absent")
            self._geom_ids[finger] = geom_id

        self.dof = len(self._joint_ids)
        self._joint_low = np.asarray(joint_low, dtype=np.float64)
        self._joint_high = np.asarray(joint_high, dtype=np.float64)
        self._ctrl_low = np.asarray(ctrl_low, dtype=np.float64)
        self._ctrl_high = np.asarray(ctrl_high, dtype=np.float64)
        self._command_low = np.maximum(self._joint_low, self._ctrl_low)
        self._command_high = np.minimum(self._joint_high, self._ctrl_high)
        if np.any(self._command_low > self._command_high):
            raise ValueError("joint and actuator ranges do not overlap")

        if self.spec.home_qpos is None:
            home = np.asarray(
                [model.qpos0[address] for address in self._joint_qpos_adr],
                dtype=np.float64,
            )
        else:
            home = np.asarray(self.spec.home_qpos, dtype=np.float64)
        if home.shape != (self.dof,) or not np.isfinite(home).all():
            raise ValueError(f"home_qpos must contain exactly {self.dof} finite values")
        self._home_q = np.clip(home, self._command_low, self._command_high)
        self._timestep = float(model.opt.timestep)
        if not math.isfinite(self._timestep) or self._timestep <= 0.0:
            raise ValueError("model.opt.timestep must be positive")

    def _duration_steps(self, duration: object) -> int:
        value = _finite(duration, "duration")
        if value < 0.0:
            raise ValueError("duration must be non-negative")
        return max(1, int(math.ceil(value / self._timestep)))

    def _joint_vector(self, value: object, field: str) -> np.ndarray:
        try:
            result = np.asarray(value, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must contain {self.dof} finite values") from exc
        if result.shape != (self.dof,) or not np.isfinite(result).all():
            raise ValueError(f"{field} must contain {self.dof} finite values")
        return result

    def _read_joint_positions(self) -> np.ndarray:
        return np.asarray(
            [self.data.qpos[address] for address in self._joint_qpos_adr],
            dtype=np.float64,
        )

    def _write_joint_command(self, command: np.ndarray) -> None:
        command = np.clip(command, self._command_low, self._command_high)
        for actuator_id, value in zip(self._actuator_ids, command, strict=True):
            self.data.ctrl[actuator_id] = float(value)

    def _target_array(self, targets: object) -> np.ndarray:
        if not isinstance(targets, Mapping) or set(targets) != set(FINGER_ORDER):
            raise ValueError(
                "targets must contain exactly " f"{list(FINGER_ORDER)!r}"
            )
        points = []
        for finger in FINGER_ORDER:
            try:
                point = np.asarray(targets[finger], dtype=np.float64)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"targets[{finger!r}] must be three finite values"
                ) from exc
            if point.shape != (3,) or not np.isfinite(point).all():
                raise ValueError(f"targets[{finger!r}] must be three finite values")
            points.append(point)
        return np.asarray(points, dtype=np.float64)

    def _fingertip_positions_array(self) -> np.ndarray:
        self._mj.mj_forward(self.model, self.data)
        return np.asarray(
            [self.data.geom_xpos[self._geom_ids[finger]] for finger in FINGER_ORDER],
            dtype=np.float64,
        )

    def get_joint_positions(self) -> np.ndarray:
        return self._read_joint_positions()

    def get_fingertip_positions(self) -> dict[str, np.ndarray]:
        positions = self._fingertip_positions_array()
        return {
            finger: positions[index].copy()
            for index, finger in enumerate(FINGER_ORDER)
        }

    def move_joints(self, q_target: object, duration: float = 2.0) -> bool:
        target = np.clip(
            self._joint_vector(q_target, "q_target"),
            self._command_low,
            self._command_high,
        )
        for _ in range(self._duration_steps(duration)):
            current = self._read_joint_positions()
            delta = np.clip(
                target - current,
                -float(self.spec.max_joint_command_delta),
                float(self.spec.max_joint_command_delta),
            )
            self._write_joint_command(current + delta)
            self.step(1)
        return bool(
            np.all(
                np.abs(target - self._read_joint_positions())
                <= float(self.spec.joint_tolerance_rad)
            )
        )

    def home(self, duration: float = 2.0) -> bool:
        return self.move_joints(self._home_q, duration=duration)

    def move_fingertips(
        self, targets: Mapping[str, object], duration: float = 2.0
    ) -> bool:
        """Track all world-frame goals for a finite number of physics steps."""

        target_positions = self._target_array(targets)
        for _ in range(self._duration_steps(duration)):
            positions = self._fingertip_positions_array()
            error = target_positions - positions
            if float(np.linalg.norm(error, axis=1).max()) <= float(
                self.spec.fingertip_tolerance_m
            ):
                break

            jacobian_rows: list[np.ndarray] = []
            for finger in FINGER_ORDER:
                jacobian = np.zeros((3, self.model.nv), dtype=np.float64)
                self._mj.mj_jacGeom(
                    self.model,
                    self.data,
                    jacobian,
                    None,
                    self._geom_ids[finger],
                )
                jacobian_rows.append(jacobian[:, self._joint_dof_adr])
            jacobian = np.vstack(jacobian_rows)
            stacked_error = error.reshape(-1)
            system = jacobian @ jacobian.T + float(self.spec.ik_damping) ** 2 * np.eye(
                jacobian.shape[0]
            )
            try:
                delta = jacobian.T @ np.linalg.solve(system, stacked_error)
            except np.linalg.LinAlgError:
                delta = np.zeros(self.dof, dtype=np.float64)
            if not np.isfinite(delta).all():
                delta = np.zeros(self.dof, dtype=np.float64)
            norm = float(np.linalg.norm(delta))
            if norm > float(self.spec.ik_step_clamp):
                delta *= float(self.spec.ik_step_clamp) / norm

            current = self._read_joint_positions()
            command = current + float(self.spec.ik_position_gain) * delta
            command = np.clip(
                command,
                current - float(self.spec.max_joint_command_delta),
                current + float(self.spec.max_joint_command_delta),
            )
            self._write_joint_command(command)
            self.step(1)

        final_error = target_positions - self._fingertip_positions_array()
        return bool(
            np.linalg.norm(final_error, axis=1).max()
            <= float(self.spec.fingertip_tolerance_m)
        )

    def describe(self) -> dict[str, Any]:
        return {
            "dof": self.dof,
            "palm_body": self.spec.palm_body_name,
            "joint_names": list(self.spec.joint_names),
            "actuator_names": list(self.spec.actuator_names),
            "fingers": {
                finger: {
                    "joint_names": list(self.spec.finger_joint_names[finger]),
                    "fingertip_geom": self.spec.fingertip_geom_names[finger],
                }
                for finger in FINGER_ORDER
            },
            "home_qpos": self._home_q.tolist(),
            "ik": {
                "damping": float(self.spec.ik_damping),
                "step_clamp": float(self.spec.ik_step_clamp),
                "position_gain": float(self.spec.ik_position_gain),
                "fingertip_tolerance_m": float(self.spec.fingertip_tolerance_m),
            },
        }


__all__ = ["FINGER_ORDER", "HandFingertipDLSSpec", "HandFingertipDLSSkeleton"]
