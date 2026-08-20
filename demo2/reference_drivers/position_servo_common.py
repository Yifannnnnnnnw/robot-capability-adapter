"""Small helpers for official Menagerie position-servo reference drivers."""
from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Callable, Mapping, Sequence

import mujoco
import numpy as np

from auto_adapter.skeletons import SkeletonBase


def positive(value, name: str = "duration") -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be finite and positive")
    return result


class PositionServoWrapper(SkeletonBase):
    """Drive named position actuators; never write generalized coordinates."""

    def __init__(self, model, data, joint_to_actuator: Mapping[str, str]) -> None:
        super().__init__(model, data, SimpleNamespace())
        self._joint_qpos: dict[str, int] = {}
        self._joint_actuator: dict[str, int] = {}
        for joint, actuator in joint_to_actuator.items():
            joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint)
            actuator_id = mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator
            )
            if joint_id < 0 or actuator_id < 0:
                raise ValueError(f"missing servo mapping {joint!r} -> {actuator!r}")
            self._joint_qpos[joint] = int(model.jnt_qposadr[joint_id])
            self._joint_actuator[joint] = int(actuator_id)

    def actuator_id(self, name: str) -> int:
        identifier = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, name
        )
        if identifier < 0:
            raise ValueError(f"actuator {name!r} is absent")
        return int(identifier)

    def get_joint_positions(self, names: Sequence[str]) -> np.ndarray:
        unknown = sorted(set(names) - set(self._joint_qpos))
        if unknown:
            raise ValueError(f"unknown joints: {unknown}")
        return np.asarray(
            [self.data.qpos[self._joint_qpos[name]] for name in names], dtype=float
        )

    def _validated_targets(
        self, names: Sequence[str], values: Sequence[float]
    ) -> dict[int, float]:
        if len(set(names)) != len(names):
            raise ValueError("joint names must be unique")
        target = np.asarray(values, dtype=float)
        if target.shape != (len(names),) or not np.isfinite(target).all():
            raise ValueError("target vector must be finite and match joint names")
        result: dict[int, float] = {}
        for name, value in zip(names, target, strict=True):
            if name not in self._joint_actuator:
                raise ValueError(f"joint {name!r} has no position actuator")
            actuator_id = self._joint_actuator[name]
            low, high = self.model.actuator_ctrlrange[actuator_id]
            result[actuator_id] = float(np.clip(value, low, high))
        return result

    def move_named(
        self, names: Sequence[str], values: Sequence[float], duration=1.0
    ) -> bool:
        return self.move_actuators(
            self._validated_targets(names, values), duration=duration
        )

    def move_actuators(self, targets: Mapping[int, float], duration=1.0) -> bool:
        seconds = positive(duration)
        starts = {identifier: float(self.data.ctrl[identifier]) for identifier in targets}
        steps = max(1, int(math.ceil(seconds / float(self.model.opt.timestep))))
        for index in range(steps):
            alpha = (index + 1) / steps
            for identifier, target in targets.items():
                self.data.ctrl[identifier] = (
                    (1.0 - alpha) * starts[identifier] + alpha * float(target)
                )
            self.step(1)
        return self.finite()

    def run_periodic(
        self,
        home: Mapping[int, float],
        duration: float,
        command: Callable[[float, dict[int, float]], None],
    ) -> bool:
        seconds = positive(duration)
        timestep = float(self.model.opt.timestep)
        steps = max(1, int(math.ceil(seconds / timestep)))
        settle_start = max(1, int(0.8 * steps))
        for index in range(steps):
            values = dict(home)
            if index < settle_start:
                command(index * timestep, values)
            for identifier, value in values.items():
                low, high = self.model.actuator_ctrlrange[identifier]
                self.data.ctrl[identifier] = float(np.clip(value, low, high))
            self.step(1)
        return self.finite()

    def finite(self) -> bool:
        return bool(
            np.isfinite(self.data.qpos).all()
            and np.isfinite(self.data.qvel).all()
            and np.isfinite(self.data.ctrl).all()
        )


class QuadrupedPositionServoWrapper(PositionServoWrapper):
    """Conservative position-servo posture and periodic leg motions."""

    def __init__(
        self,
        model,
        data,
        *,
        leg_groups: Sequence[Sequence[str]],
        home_joint_positions: Mapping[str, float],
        base_body: str,
        extra_joint_to_actuator: Mapping[str, str] | None = None,
    ) -> None:
        joints = {name: name for group in leg_groups for name in group}
        joints.update(dict(extra_joint_to_actuator or {}))
        super().__init__(model, data, joints)
        self._leg_groups = [list(group) for group in leg_groups]
        if len(self._leg_groups) != 4 or any(len(group) != 3 for group in self._leg_groups):
            raise ValueError("quadruped wrapper requires four 3-joint leg groups")
        self._home_joint_positions = {
            name: float(home_joint_positions[name]) for group in self._leg_groups for name in group
        }
        self._home_controls = {
            self._joint_actuator[name]: value
            for name, value in self._home_joint_positions.items()
        }
        self._base_body_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, base_body
        )
        if self._base_body_id < 0:
            raise ValueError(f"base body {base_body!r} is absent")

    def stand_and_hold(self, duration=1.0):
        names = list(self._home_joint_positions)
        values = [self._home_joint_positions[name] for name in names]
        return self.move_named(names, values, duration)

    def set_body_height_and_hold(self, target_body_height, duration=1.0):
        target = positive(target_body_height, "target_body_height")
        current = float(self.data.xpos[self._base_body_id, 2])
        values = dict(self._home_joint_positions)
        # A small symmetric bend is a physical command, not a base teleport.
        bend = float(np.clip(current - target, -0.15, 0.15))
        for _, hip, knee in self._leg_groups:
            knee_sign = 1.0 if values[knee] >= 0.0 else -1.0
            values[hip] += 0.5 * bend * knee_sign
            values[knee] += bend * knee_sign
        return self.move_named(list(values), list(values.values()), duration)

    def walk_bounded_direction_and_stop(
        self, direction, distance, duration=2.0
    ):
        if direction not in {
            "forward_initial_body_yaw",
            "lateral_positive_initial_body_yaw",
        }:
            raise ValueError("direction is not a supported initial-yaw axis")
        distance_m = positive(distance, "distance")
        seconds = positive(duration)
        amplitude = float(np.clip(0.35 * distance_m / seconds, 0.03, 0.14))
        phases = (0.0, np.pi, np.pi, 0.0)

        def command(t: float, controls: dict[int, float]) -> None:
            for group, phase in zip(self._leg_groups, phases, strict=True):
                abduction, hip, knee = group
                wave = math.sin(2.0 * math.pi * 1.5 * t + phase)
                if direction == "forward_initial_body_yaw":
                    controls[self._joint_actuator[hip]] += amplitude * wave
                    controls[self._joint_actuator[knee]] -= 0.4 * amplitude * max(0.0, wave)
                else:
                    controls[self._joint_actuator[abduction]] += amplitude * wave

        return self.run_periodic(self._home_controls, seconds, command)
