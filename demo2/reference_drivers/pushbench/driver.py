from demo2.reference_drivers.arm_task_common import build_serial

JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
ACTUATORS = ["act_shoulder_pan", "act_shoulder_lift", "act_elbow_flex", "act_wrist_flex", "act_wrist_roll"]

def build(*, model=None, data=None, mjcf_path=None):
    return build_serial(
        mjcf_path=mjcf_path,
        model=model,
        data=data,
        ee_site="ee_site",
        joint_names=JOINTS,
        actuator_names=ACTUATORS,
        gripper_actuator_names=["act_jaw_visual"],
        gripper_open_ctrl=1.75,
        gripper_close_ctrl=-0.175,
        grasp_backend="contact",
        task_effects={"push_object_to_goal", "move_to_waypoint_and_return"},
    )
