"""1.0-style xArm7 driver using the shared serial-arm DLS skeleton."""
from __future__ import annotations

from pathlib import Path

import mujoco

from demo2.reference_drivers.arm_task_common import build_serial


JOINTS = [f"joint{index}" for index in range(1, 8)]
ACTUATORS = [f"act{index}" for index in range(1, 8)]
TASK_EFFECTS = {"reach_target", "trace_cartesian_path", "move_cartesian_offset_and_return", "visit_cartesian_waypoints", "move_to_waypoint_and_return"}


def build(*, model=None, data=None, mjcf_path=None):
    if model is None:
        model = mujoco.MjModel.from_xml_path(str(Path(mjcf_path or "mjcf.xml").resolve()))
    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    if home_id < 0:
        raise ValueError("xArm7 MJCF must provide the 'home' keyframe")
    if data is None:
        data = mujoco.MjData(model)
        mujoco.mj_resetDataKeyframe(model, data, home_id)
    home_qpos = [float(model.key_qpos[home_id, model.jnt_qposadr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)]]) for name in JOINTS]
    return build_serial(mjcf_path=mjcf_path, model=model, data=data, ee_site="link_tcp", joint_names=JOINTS, actuator_names=ACTUATORS, home_qpos=home_qpos, task_effects=TASK_EFFECTS)
