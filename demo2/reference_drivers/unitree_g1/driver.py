"""Official Unitree G1 position-servo reference wrapper."""
from __future__ import annotations

import math
from pathlib import Path

import mujoco
import numpy as np

from demo2.reference_drivers.position_servo_common import (
    PositionServoWrapper,
    positive,
)


LEFT_LEG = ["left_hip_pitch_joint", "left_hip_roll_joint", "left_hip_yaw_joint", "left_knee_joint", "left_ankle_pitch_joint", "left_ankle_roll_joint"]
RIGHT_LEG = ["right_hip_pitch_joint", "right_hip_roll_joint", "right_hip_yaw_joint", "right_knee_joint", "right_ankle_pitch_joint", "right_ankle_roll_joint"]
WAIST = ["waist_yaw_joint", "waist_roll_joint", "waist_pitch_joint"]
LEFT_ARM = ["left_shoulder_pitch_joint", "left_shoulder_roll_joint", "left_shoulder_yaw_joint", "left_elbow_joint", "left_wrist_roll_joint", "left_wrist_pitch_joint", "left_wrist_yaw_joint"]
RIGHT_ARM = ["right_shoulder_pitch_joint", "right_shoulder_roll_joint", "right_shoulder_yaw_joint", "right_elbow_joint", "right_wrist_roll_joint", "right_wrist_pitch_joint", "right_wrist_yaw_joint"]
JOINTS = LEFT_LEG + RIGHT_LEG + WAIST + LEFT_ARM + RIGHT_ARM
HOME_VALUES = [0.0] * 15 + [0.2, 0.2, 0.0, 1.28, 0.0, 0.0, 0.0, 0.2, -0.2, 0.0, 1.28, 0.0, 0.0, 0.0]
HOME = dict(zip(JOINTS, HOME_VALUES, strict=True))


class G1PositionServoWrapper(PositionServoWrapper):
    def __init__(self, model, data) -> None:
        super().__init__(model, data, {name: name for name in JOINTS})
        self._home_controls = {
            self._joint_actuator[name]: value for name, value in HOME.items()
        }
        self._pelvis_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, "pelvis"
        )
        if self._pelvis_id < 0:
            raise ValueError("G1 pelvis body is absent")

    def stand_and_hold(self, duration=1.0):
        return self.move_named(JOINTS, HOME_VALUES, duration)

    def set_body_height_and_hold(self, target_body_height, duration=1.0):
        target = positive(target_body_height, "target_body_height")
        current = float(self.data.xpos[self._pelvis_id, 2])
        bend = float(np.clip(current - target, -0.25, 0.35))
        values = dict(HOME)
        for side in ("left", "right"):
            values[f"{side}_hip_pitch_joint"] = -0.5 * bend
            values[f"{side}_knee_joint"] = bend
            values[f"{side}_ankle_pitch_joint"] = -0.5 * bend
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
        amplitude = float(np.clip(0.3 * distance_m / seconds, 0.025, 0.10))

        def command(t: float, controls: dict[int, float]) -> None:
            wave = math.sin(2.0 * math.pi * 1.2 * t)
            if direction == "forward_initial_body_yaw":
                controls[self._joint_actuator["left_hip_pitch_joint"]] += amplitude * wave
                controls[self._joint_actuator["right_hip_pitch_joint"]] -= amplitude * wave
                controls[self._joint_actuator["left_knee_joint"]] += 0.5 * amplitude * max(0.0, wave)
                controls[self._joint_actuator["right_knee_joint"]] += 0.5 * amplitude * max(0.0, -wave)
            else:
                controls[self._joint_actuator["left_hip_roll_joint"]] += amplitude * wave
                controls[self._joint_actuator["right_hip_roll_joint"]] += amplitude * wave

        return self.run_periodic(self._home_controls, seconds, command)

    def set_upper_body_posture_and_hold(
        self, joint_names, target_joint_positions, duration=1.0
    ):
        return self.move_named(joint_names, target_joint_positions, duration)


def build(*, model=None, data=None, mjcf_path=None):
    if model is None:
        model = mujoco.MjModel.from_xml_path(
            str(Path(mjcf_path or "mjcf.xml").resolve())
        )
    if data is None:
        data = mujoco.MjData(model)
        key = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "stand")
        if key < 0:
            raise ValueError("G1 MJCF must provide the 'stand' keyframe")
        mujoco.mj_resetDataKeyframe(model, data, key)
    mujoco.mj_forward(model, data)
    return G1PositionServoWrapper(model, data)
