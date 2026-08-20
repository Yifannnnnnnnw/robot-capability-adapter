"""Minimal dual-arm DLS and position-actuator wrapper for official ALOHA."""
from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import mujoco
import numpy as np

from auto_adapter.skeletons import ArmSerialDLSSkeleton, ArmSpec, SkeletonBase


ARM_SUFFIXES = ["waist", "shoulder", "elbow", "forearm_roll", "wrist_angle", "wrist_rotate"]
ARMS = ("left", "right")


def _seconds(value) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError("duration must be finite and positive")
    return result


def _vector3(value, name: str) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if result.shape != (3,) or not np.isfinite(result).all():
        raise ValueError(f"{name} must be a finite 3-vector")
    return result


class AlohaBimanualPositionActuatorWrapper(SkeletonBase):
    def __init__(self, model, data) -> None:
        super().__init__(model, data, SimpleNamespace())
        self._channels: dict[str, ArmSerialDLSSkeleton] = {}
        self._home: dict[str, np.ndarray] = {}
        home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "neutral_pose")
        if home_id < 0:
            raise ValueError("ALOHA MJCF must provide the 'neutral_pose' keyframe")
        for arm in ARMS:
            joints = [f"{arm}/{suffix}" for suffix in ARM_SUFFIXES]
            joint_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) for name in joints]
            if any(identifier < 0 for identifier in joint_ids):
                raise ValueError(f"ALOHA {arm} arm joint mapping is incomplete")
            limits = {name: tuple(float(value) for value in model.jnt_range[identifier]) for name, identifier in zip(joints, joint_ids, strict=True)}
            home = np.asarray([model.key_qpos[home_id, model.jnt_qposadr[identifier]] for identifier in joint_ids], dtype=float)
            self._home[arm] = home
            self._channels[arm] = ArmSerialDLSSkeleton(model, data, ArmSpec(ee_site_name=f"{arm}/gripper", arm_joint_names=joints, arm_actuator_names=joints, joint_limits=limits, home_qpos=home.tolist(), grasp_backend="noop", ik_damping=1e-3, ik_max_iter=120, ik_tolerance=1e-3, ik_step_clamp=0.2, ik_raise_on_unreachable=False))
        self._gripper_ids = {arm: self._actuator_id(f"{arm}/gripper") for arm in ARMS}

    def _actuator_id(self, name: str) -> int:
        identifier = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
        if identifier < 0:
            raise ValueError(f"ALOHA actuator {name!r} is absent")
        return int(identifier)

    def _target(self, arm: str, target_position) -> np.ndarray:
        if arm not in self._channels:
            raise ValueError("arm must be 'left' or 'right'")
        channel = self._channels[arm]
        target = _vector3(target_position, "target_position")
        seeds = [channel.get_joint_positions(), self._home[arm]]
        candidates = []
        for seed in seeds:
            q = channel.ik(target, q_init=seed, raise_on_unreachable=False)
            residual = float(np.linalg.norm(channel.fk(q)["pos"] - target))
            candidates.append((residual, q))
        return min(candidates, key=lambda item: item[0])[1]

    def _move(self, targets: dict[str, np.ndarray], duration) -> bool:
        seconds = _seconds(duration)
        starts = {arm: channel.get_joint_positions() for arm, channel in self._channels.items() if arm in targets}
        steps = max(1, int(math.ceil(seconds / float(self.model.opt.timestep))))
        for index in range(steps):
            alpha = (index + 1) / steps
            for arm, target in targets.items():
                command = (1.0 - alpha) * starts[arm] + alpha * target
                self._channels[arm].set_arm_actuators(command)
            self.step(1)
        return bool(np.isfinite(self.data.qpos).all())

    def reach_target(self, target_position, arm, duration=1.0):
        return self._move({arm: self._target(arm, target_position)}, duration)

    def move_bimanual_targets(self, left_target_position, right_target_position, duration=1.0):
        targets = {"left": self._target("left", left_target_position), "right": self._target("right", right_target_position)}
        return self._move(targets, duration)

    def cycle_bimanual_grippers(self, duration=1.0):
        seconds = _seconds(duration)
        half_steps = max(1, int(math.ceil(0.5 * seconds / float(self.model.opt.timestep))))
        current = {arm: float(self.data.ctrl[identifier]) for arm, identifier in self._gripper_ids.items()}
        for target in (0.037, 0.002):
            start = dict(current)
            for index in range(half_steps):
                alpha = (index + 1) / half_steps
                for arm, identifier in self._gripper_ids.items():
                    self.data.ctrl[identifier] = (1.0 - alpha) * start[arm] + alpha * target
                self.step(1)
            current = {arm: target for arm in ARMS}
        return bool(np.isfinite(self.data.qpos).all())

    def move_bimanual_offset_and_return(self, left_offset, right_offset, duration=1.0):
        seconds = _seconds(duration)
        starts = {arm: self._channels[arm].get_ee_pose()[0] for arm in ARMS}
        outward = {"left": self._target("left", starts["left"] + _vector3(left_offset, "left_offset")), "right": self._target("right", starts["right"] + _vector3(right_offset, "right_offset"))}
        self._move(outward, 0.5 * seconds)
        returning = {arm: self._target(arm, starts[arm]) for arm in ARMS}
        return self._move(returning, 0.5 * seconds)


def build(*, model=None, data=None, mjcf_path=None):
    if model is None:
        model = mujoco.MjModel.from_xml_path(str(Path(mjcf_path or "mjcf.xml").resolve()))
    if data is None:
        data = mujoco.MjData(model)
        key = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "neutral_pose")
        if key < 0:
            raise ValueError("ALOHA MJCF must provide the 'neutral_pose' keyframe")
        mujoco.mj_resetDataKeyframe(model, data, key)
    mujoco.mj_forward(model, data)
    return AlohaBimanualPositionActuatorWrapper(model, data)
