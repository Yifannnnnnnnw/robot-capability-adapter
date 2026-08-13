from demo2.reference_drivers.arm_task_common import build_serial

JOINTS = ["shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint", "wrist_1_joint", "wrist_2_joint", "wrist_3_joint"]
ACTUATORS = ["shoulder_pan", "shoulder_lift", "elbow", "wrist_1", "wrist_2", "wrist_3"]

def build(*, model=None, data=None, mjcf_path=None):
    return build_serial(
        mjcf_path=mjcf_path,
        model=model,
        data=data,
        ee_site="attachment_site",
        joint_names=JOINTS,
        actuator_names=ACTUATORS,
        task_effects={
            "reach_target",
            "trace_cartesian_path",
            "move_cartesian_offset_and_return",
            "visit_cartesian_waypoints",
            "reject_unreachable_and_return_home",
        },
    )
