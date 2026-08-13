from demo2.reference_drivers.arm_task_common import build_serial

JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]

def build(*, model=None, data=None, mjcf_path=None):
    return build_serial(mjcf_path=mjcf_path, model=model, data=data, ee_site="gripperframe", joint_names=JOINTS, actuator_names=JOINTS, gripper_actuator_names=["gripper"], gripper_open_ctrl=1.74533, gripper_close_ctrl=-0.17453, grasp_backend="contact")
