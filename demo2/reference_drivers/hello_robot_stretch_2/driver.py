"""Official Stretch position-actuator reference wrapper."""
from __future__ import annotations

import math
from pathlib import Path

import mujoco
import numpy as np

from demo2.reference_drivers.position_servo_common import (
    PositionServoWrapper,
    positive,
)


class StretchPositionServoWrapper(PositionServoWrapper):
    def __init__(self, model, data) -> None:
        super().__init__(model, data, {
            "joint_lift": "lift",
            "joint_arm_l3": "arm_extend",
            "joint_wrist_yaw": "wrist_yaw",
            "joint_gripper_slide": "grip",
        })
        self._arm_joints = [f"joint_arm_l{index}" for index in (3, 2, 1, 0)]

    def drive_base_forward(self, distance, duration=2.0):
        distance_m = positive(distance, "distance")
        seconds = positive(duration)
        forward = self.actuator_id("forward")
        turn = self.actuator_id("turn")
        speed = float(np.clip(distance_m / seconds, 0.05, 1.0))
        motion_steps = max(1, int(math.ceil(0.8 * seconds / self.model.opt.timestep)))
        settle_steps = max(1, int(math.ceil(0.2 * seconds / self.model.opt.timestep)))
        self.data.ctrl[turn] = 0.0
        self.data.ctrl[forward] = speed
        self.step(motion_steps)
        self.data.ctrl[forward] = 0.0
        self.step(settle_steps)
        return self.finite()

    def set_lift_height(self, delta_height, duration=1.0):
        delta = float(delta_height)
        if not math.isfinite(delta):
            raise ValueError("delta_height must be finite")
        start = float(self.get_joint_positions(["joint_lift"])[0])
        return self.move_named(["joint_lift"], [start + delta], duration)

    def extend_arm(self, extension, duration=1.0):
        delta = float(extension)
        if not math.isfinite(delta):
            raise ValueError("extension must be finite")
        current = 0.0
        for name in self._arm_joints:
            joint_id = mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_JOINT, name
            )
            current += float(self.data.qpos[self.model.jnt_qposadr[joint_id]])
        actuator = self.actuator_id("arm_extend")
        low, high = self.model.actuator_ctrlrange[actuator]
        return self.move_actuators(
            {actuator: float(np.clip(current + delta, low, high))}, duration
        )

    def rotate_wrist(self, delta_yaw, duration=1.0):
        delta = float(delta_yaw)
        if not math.isfinite(delta):
            raise ValueError("delta_yaw must be finite")
        start = float(self.get_joint_positions(["joint_wrist_yaw"])[0])
        return self.move_named(["joint_wrist_yaw"], [start + delta], duration)

    def cycle_gripper(self, duration=1.0):
        seconds = positive(duration)
        actuator = self.actuator_id("grip")
        self.move_actuators({actuator: 0.04}, 0.5 * seconds)
        return self.move_actuators({actuator: -0.005}, 0.5 * seconds)


def build(*, model=None, data=None, mjcf_path=None):
    if model is None:
        model = mujoco.MjModel.from_xml_path(
            str(Path(mjcf_path or "mjcf.xml").resolve())
        )
    if data is None:
        data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    return StretchPositionServoWrapper(model, data)
