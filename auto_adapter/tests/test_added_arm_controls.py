"""Real AA1 arm control on the three added serial-arm configurations."""
import mujoco
import numpy as np
import pytest

from auto_adapter.robot_catalog import find_robot_definition, REPO_ROOT
from auto_adapter.skeletons import ArmSpec, ArmSerialDLSSkeleton


@pytest.mark.parametrize("robot_id", ["kinova_gen3_robotiq_2f85", "ufactory_xarm7",
                                      "universal_robots_ur5e_robotiq_2f85"])
def test_added_arm_moves_with_original_skeleton(robot_id):
    robot = find_robot_definition(robot_id)
    model = mujoco.MjModel.from_xml_path(str(REPO_ROOT / robot["mjcf"]))
    data = mujoco.MjData(model)
    joint_ids = model.actuator_trnid[:robot["dof"], 0]
    addresses = model.jnt_qposadr[joint_ids]
    home = (model.key_qpos[0] if model.nkey else model.qpos0)[addresses]
    spec = ArmSpec(ee_site_name=robot["state_refs"]["ee_site"],
                   arm_joint_names=[model.joint(int(j)).name for j in joint_ids],
                   arm_actuator_names=[model.actuator(i).name for i in range(robot["dof"])],
                   joint_limits={model.joint(int(j)).name: tuple(model.jnt_range[j])
                                 if model.jnt_limited[j] else (-2 * np.pi, 2 * np.pi)
                                 for j in joint_ids},
                   home_qpos=home.tolist(), ik_max_iter=100)
    skeleton = ArmSerialDLSSkeleton(model, data, spec)
    skeleton.home()
    start = skeleton.get_ee_pose()[0].copy()
    fk = mujoco.MjData(model)
    fk.qpos[:] = data.qpos
    fk.qpos[addresses[0]] += 0.12
    mujoco.mj_forward(model, fk)
    goal = fk.site_xpos[model.site(robot["state_refs"]["ee_site"]).id].copy()
    skeleton.move_cartesian(goal, duration=2.0)
    skeleton.settle(0.3)
    end = skeleton.get_ee_pose()[0]
    assert np.linalg.norm(end - start) > 0.01
    assert np.linalg.norm(end - goal) < 0.025
    assert np.isfinite(data.qpos).all()
