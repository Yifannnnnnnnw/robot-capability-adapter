"""Official Barkour vB position-servo reference wrapper."""
from __future__ import annotations

from pathlib import Path

import mujoco

from demo2.reference_drivers.position_servo_common import (
    QuadrupedPositionServoWrapper,
)


LEGS = [
    ["abduction_front_left", "hip_front_left", "knee_front_left"],
    ["abduction_hind_left", "hip_hind_left", "knee_hind_left"],
    ["abduction_front_right", "hip_front_right", "knee_front_right"],
    ["abduction_hind_right", "hip_hind_right", "knee_hind_right"],
]
HOME = {name: value for group in LEGS for name, value in zip(group, (0.0, 0.5, 1.0), strict=True)}


class BarkourPositionServoWrapper(QuadrupedPositionServoWrapper):
    def __init__(self, model, data) -> None:
        super().__init__(
            model,
            data,
            leg_groups=LEGS,
            home_joint_positions=HOME,
            base_body="torso",
        )

    def set_leg_posture_and_hold(
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
        key = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
        if key < 0:
            raise ValueError("Barkour MJCF must provide the 'home' keyframe")
        mujoco.mj_resetDataKeyframe(model, data, key)
    mujoco.mj_forward(model, data)
    return BarkourPositionServoWrapper(model, data)
