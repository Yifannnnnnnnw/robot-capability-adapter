from demo2.reference_drivers.arm_task_common import build_serial

JOINTS = [f"joint{i}" for i in range(1, 7)]

def build(*, model=None, data=None, mjcf_path=None):
    return build_serial(
        mjcf_path=mjcf_path,
        model=model,
        data=data,
        ee_site="ee_site",
        joint_names=JOINTS,
        actuator_names=JOINTS,
        gripper_actuator_names=["gripper"],
        gripper_open_ctrl=0.035,
        gripper_close_ctrl=0.0,
        grasp_backend="weld",
        graspable_bodies=["cube_red", "cube_green", "cube_blue"],
        task_effects={
            "reach_above_object",
            "grasp_and_lift",
            "push_object_to_goal",
        },
    )
