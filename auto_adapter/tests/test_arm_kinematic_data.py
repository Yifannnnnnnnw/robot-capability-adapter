"""Regression: FK/IK calculations must never write the execution data."""
import mujoco
import numpy as np

from auto_adapter.skeletons import ArmSerialDLSSkeleton, ArmSpec


def test_fk_and_ik_use_independent_data(monkeypatch):
    model = mujoco.MjModel.from_xml_string('''<mujoco><worldbody><body>
        <joint name="j" type="slide" axis="1 0 0" range="-.5 .5"/>
        <geom type="sphere" size=".02"/><site name="ee"/></body></worldbody>
        <actuator><position name="a" joint="j" kp="100"/></actuator></mujoco>''')
    data = mujoco.MjData(model)
    arm = ArmSerialDLSSkeleton(model, data, ArmSpec(
        ee_site_name="ee", arm_joint_names=["j"], arm_actuator_names=["a"],
        joint_limits={"j": (-.5, .5)}, ik_max_iter=100))
    baseline = data.qpos.copy()
    original = mujoco.mj_forward
    seen = []

    def forward(m, d):
        seen.append(d is data)
        assert np.array_equal(data.qpos, baseline)
        return original(m, d)

    monkeypatch.setattr(mujoco, "mj_forward", forward)
    assert np.allclose(arm.fk(np.array([.08]))["pos"], [.08, 0., 0.])
    q = arm.ik(np.array([.08, 0., 0.]))
    assert abs(q[0] - .08) < .001
    assert seen and not any(seen)
    assert data.time == 0.


def test_piper_gripper_control_changes_measured_aperture():
    from auto_adapter.robot_catalog import REPO_ROOT
    model = mujoco.MjModel.from_xml_path(str(REPO_ROOT / "assets/mjcf/piper/scene.xml"))
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, model.key("home").id)
    joints = [f"joint{i}" for i in range(1, 7)]
    arm = ArmSerialDLSSkeleton(model, data, ArmSpec(
        ee_site_name="ee_site", arm_joint_names=joints, arm_actuator_names=joints,
        joint_limits={name: tuple(model.joint(name).range) for name in joints},
        gripper_actuator_names=["gripper"], gripper_open_ctrl=.035))
    start = arm.get_gripper_joint_positions()["joint7"]
    arm.set_gripper_control(.02)
    arm.step(250)
    measured = arm.get_gripper_joint_positions()["joint7"]
    assert abs(measured - .02) < .001
    assert measured - start > .015
