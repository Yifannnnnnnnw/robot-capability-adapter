"""
SO-101 full robot driver.
5-DOF serial arm with weld-based grasping.
"""
from auto_adapter.skeletons.arm_serial_dls import ArmSerialDLSSkeleton
from auto_adapter.skeletons.base import ArmSpec


def build():
    """
    Construct and return the ArmSerialDLSSkeleton for SO-101.
    """
    spec = ArmSpec(
        ee_site_name="ee_site",
        arm_joint_names=[
            "shoulder_pan",
            "shoulder_lift",
            "elbow_flex",
            "wrist_flex",
            "wrist_roll"
        ],
        arm_actuator_names=[
            "act_shoulder_pan",
            "act_shoulder_lift",
            "act_elbow_flex",
            "act_wrist_flex",
            "act_wrist_roll"
        ],
        joint_limits={
            "shoulder_pan": (-1.92, 1.92),
            "shoulder_lift": (-1.75, 1.75),
            "elbow_flex": (-1.69, 1.69),
            "wrist_flex": (-1.66, 1.66),
            "wrist_roll": (-2.74, 2.84)
        },
        home_qpos=[0.0, 0.0, 0.0, 0.0, 0.0],
        gripper_actuator_names=["act_jaw_visual"],
        gripper_close_ctrl=-0.175,
        gripper_open_ctrl=1.75,
        grasp_backend="weld",
        weld_graspable_bodies=[
            "banana",
            "mug",
            "bottle",
            "screwdriver",
            "duck",
            "lego"
        ],
        sim_dt=0.002
    )
    
    return ArmSerialDLSSkeleton.from_mjcf("mjcf.xml", spec)


if __name__ == "__main__":
    skel = build()
    print(skel.describe())
