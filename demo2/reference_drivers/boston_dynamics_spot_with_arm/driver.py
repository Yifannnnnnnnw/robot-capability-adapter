"""Official Spot-with-arm position-servo reference wrapper."""
from __future__ import annotations

from pathlib import Path

import mujoco

from demo2.reference_drivers.position_servo_common import (
    QuadrupedPositionServoWrapper,
)


LEGS = [
    ["fl_hx", "fl_hy", "fl_kn"],
    ["fr_hx", "fr_hy", "fr_kn"],
    ["hl_hx", "hl_hy", "hl_kn"],
    ["hr_hx", "hr_hy", "hr_kn"],
]
ARM_JOINTS = ["arm_sh0", "arm_sh1", "arm_el0", "arm_el1", "arm_wr0", "arm_wr1", "arm_f1x"]
HOME = {name: value for group in LEGS for name, value in zip(group, (0.0, 1.04, -1.8), strict=True)}


class SpotPositionServoWrapper(QuadrupedPositionServoWrapper):
    def __init__(self, model, data) -> None:
        super().__init__(
            model,
            data,
            leg_groups=LEGS,
            home_joint_positions=HOME,
            base_body="body",
            extra_joint_to_actuator={name: name for name in ARM_JOINTS},
        )

    def set_arm_joint_posture_and_hold(
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
            raise ValueError("Spot MJCF must provide the 'home' keyframe")
        mujoco.mj_resetDataKeyframe(model, data, key)
    mujoco.mj_forward(model, data)
    return SpotPositionServoWrapper(model, data)
