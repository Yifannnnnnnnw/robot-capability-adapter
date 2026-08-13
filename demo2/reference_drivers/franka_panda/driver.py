"""AutoAdapter 1.0-style Franka driver using the proven serial-arm DLS family."""
from __future__ import annotations

from pathlib import Path
import types

import mujoco

from auto_adapter.skeletons import ArmSerialDLSSkeleton, ArmSpec


JOINT_NAMES = [f"joint{index}" for index in range(1, 8)]
ACTUATOR_NAMES = [f"actuator{index}" for index in range(1, 8)]


def _limits(model):
    return {
        name: tuple(
            float(value)
            for value in model.jnt_range[
                mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            ]
        )
        for name in JOINT_NAMES
    }


def build(*, model=None, data=None, mjcf_path=None):
    if model is None:
        path = Path(mjcf_path) if mjcf_path else Path("mjcf.xml")
        model = mujoco.MjModel.from_xml_path(str(path.resolve()))
    if data is None:
        data = mujoco.MjData(model)
        key = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
        mujoco.mj_resetDataKeyframe(model, data, key)
        mujoco.mj_forward(model, data)
    spec = ArmSpec(
        ee_body_name="hand",
        arm_joint_names=list(JOINT_NAMES),
        arm_actuator_names=list(ACTUATOR_NAMES),
        joint_limits=_limits(model),
        grasp_backend="noop",
        ik_damping=1e-3,
        ik_max_iter=60,
        ik_tolerance=1e-3,
        ik_step_clamp=0.2,
    )
    robot = ArmSerialDLSSkeleton(model, data, spec)

    def move_to_cartesian(self, position, duration=2.0):
        return self.move_cartesian(position, duration=duration)

    robot.move_to_cartesian = types.MethodType(move_to_cartesian, robot)
    return robot
