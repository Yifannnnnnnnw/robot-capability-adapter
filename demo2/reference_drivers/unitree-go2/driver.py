"""AutoAdapter 1.0-style Go2 driver assembled from the trusted PD-gait skeleton."""
from __future__ import annotations

from pathlib import Path

import mujoco

from auto_adapter.skeletons import QuadrupedPDGaitSkeleton, QuadrupedSpec


LEG_JOINTS = {
    "FL": ["FL_hip_joint", "FL_thigh_joint", "FL_calf_joint"],
    "FR": ["FR_hip_joint", "FR_thigh_joint", "FR_calf_joint"],
    "RL": ["RL_hip_joint", "RL_thigh_joint", "RL_calf_joint"],
    "RR": ["RR_hip_joint", "RR_thigh_joint", "RR_calf_joint"],
}
LEG_ACTUATORS = {
    leg: [name.removesuffix("_joint") for name in names]
    for leg, names in LEG_JOINTS.items()
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
    spec = QuadrupedSpec(
        base_body_name="base_link",
        leg_joint_names={name: list(values) for name, values in LEG_JOINTS.items()},
        leg_actuator_names={name: list(values) for name, values in LEG_ACTUATORS.items()},
        home_qpos=[0.0, 0.9, -1.8] * 4,
        body_height_target=0.27,
    )
    return QuadrupedPDGaitSkeleton(model, data, spec)
