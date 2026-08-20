"""Minimal direct-MuJoCo position-actuator wrapper for the left LEAP Hand."""
from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Sequence

import mujoco
import numpy as np

from auto_adapter.skeletons import SkeletonBase


JOINTS = [
    "if_mcp", "if_rot", "if_pip", "if_dip",
    "mf_mcp", "mf_rot", "mf_pip", "mf_dip",
    "rf_mcp", "rf_rot", "rf_pip", "rf_dip",
    "th_cmc", "th_axl", "th_mcp", "th_ipl",
]
ACTUATORS = [f"{name}_act" for name in JOINTS]


def _seconds(value: float, *, allow_zero: bool = False) -> float:
    result = float(value)
    minimum = 0.0 if allow_zero else np.nextafter(0.0, 1.0)
    if not math.isfinite(result) or result < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"duration must be finite and {qualifier}")
    return result


class LeapHandPositionActuatorWrapper(SkeletonBase):
    def __init__(self, model, data) -> None:
        super().__init__(model, data, SimpleNamespace())
        self._joint_qpos: dict[str, int] = {}
        self._actuator_id: dict[str, int] = {}
        for joint_name, actuator_name in zip(JOINTS, ACTUATORS, strict=True):
            joint_id = mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_JOINT, joint_name
            )
            actuator_id = mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name
            )
            if joint_id < 0 or actuator_id < 0:
                raise ValueError(
                    f"LEAP mapping is absent: {joint_name!r}/{actuator_name!r}"
                )
            self._joint_qpos[joint_name] = int(model.jnt_qposadr[joint_id])
            self._actuator_id[joint_name] = int(actuator_id)

    def get_joint_positions(self, joint_names: Iterable[str] = JOINTS) -> np.ndarray:
        names = list(joint_names)
        self._require_known(names)
        return np.asarray(
            [self.data.qpos[self._joint_qpos[name]] for name in names],
            dtype=float,
        )

    def _require_known(self, names: Sequence[str]) -> None:
        unknown = sorted(set(names) - set(self._joint_qpos))
        if unknown:
            raise ValueError(f"unknown LEAP joints: {unknown}")
        if len(set(names)) != len(names):
            raise ValueError("joint_names must not contain duplicates")

    def _move_named(
        self,
        joint_names: Sequence[str],
        target_joint_positions_rad: Sequence[float],
        duration: float,
        hold_s: float = 0.0,
    ) -> bool:
        names = list(joint_names)
        self._require_known(names)
        target = np.asarray(target_joint_positions_rad, dtype=float)
        if target.shape != (len(names),) or not np.isfinite(target).all():
            raise ValueError("target_joint_positions_rad must match joint_names")
        actuator_ids = [self._actuator_id[name] for name in names]
        limits = self.model.actuator_ctrlrange[actuator_ids]
        if np.any(target < limits[:, 0]) or np.any(target > limits[:, 1]):
            raise ValueError("LEAP target lies outside the MJCF actuator range")
        start = self.get_joint_positions(names)
        movement_s = _seconds(duration)
        steps = max(1, int(math.ceil(movement_s / float(self.model.opt.timestep))))
        for index in range(steps):
            alpha = (index + 1) / steps
            command = (1.0 - alpha) * start + alpha * target
            for actuator_id, value in zip(actuator_ids, command, strict=True):
                self.data.ctrl[actuator_id] = float(value)
            self.step(1)
        hold = _seconds(hold_s, allow_zero=True)
        if hold > 0.0:
            self.step(max(1, int(math.ceil(hold / float(self.model.opt.timestep)))))
        return bool(np.isfinite(self.get_joint_positions(names)).all())

    def set_joint_posture_and_hold(
        self, joint_names, target_joint_positions, duration=1.0, hold_s=0.0
    ) -> bool:
        return self._move_named(
            joint_names, target_joint_positions, duration, hold_s
        )

    def flex_index_finger_and_hold(
        self, target_joint_positions, duration=1.0, hold_s=0.0
    ) -> bool:
        return self._move_named(
            JOINTS[:4], target_joint_positions, duration, hold_s
        )

    def set_symmetric_finger_posture(
        self, joint_pairs, target_joint_positions, duration=1.0, hold_s=0.0
    ) -> bool:
        targets = np.asarray(target_joint_positions, dtype=float)
        if targets.shape != (len(joint_pairs),):
            raise ValueError("one target is required for each joint pair")
        names: list[str] = []
        values: list[float] = []
        for pair, target in zip(joint_pairs, targets, strict=True):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("joint_pairs must contain two-name pairs")
            names.extend([str(pair[0]), str(pair[1])])
            values.extend([float(target), float(target)])
        return self._move_named(names, values, duration, hold_s)

    def set_thumb_opposition_and_hold(
        self, target_joint_positions, duration=1.0, hold_s=0.0
    ) -> bool:
        return self._move_named(
            JOINTS[12:], target_joint_positions, duration, hold_s
        )

    def cycle_to_pregrasp_posture(
        self, joint_names, target_joint_positions, duration=1.0, hold_s=0.0
    ) -> bool:
        return self._move_named(
            joint_names, target_joint_positions, duration, hold_s
        )


def build(*, model=None, data=None, mjcf_path=None):
    if model is None:
        path = Path(mjcf_path or "mjcf.xml").resolve()
        model = mujoco.MjModel.from_xml_path(str(path))
    if data is None:
        data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    return LeapHandPositionActuatorWrapper(model, data)
