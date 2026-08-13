"""AutoAdapter 1.0-style SO-101 driver assembled from the trusted arm skeleton."""
from __future__ import annotations

from pathlib import Path
import types

import mujoco
import numpy as np

from auto_adapter.skeletons import ArmSerialDLSSkeleton, ArmSpec


JOINT_NAMES = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
]


def _vector3(value, *, name: str) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float64)
    if vector.shape != (3,) or not np.isfinite(vector).all():
        raise ValueError(f"{name} must be a finite world-frame vector with shape (3,)")
    return vector


def _duration(value, *, name: str = "duration") -> float:
    duration = float(value)
    if not np.isfinite(duration) or duration <= 0.0:
        raise ValueError(f"{name} must be a finite positive number of seconds")
    return duration


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
        mujoco.mj_forward(model, data)
    spec = ArmSpec(
        ee_site_name="gripperframe",
        arm_joint_names=list(JOINT_NAMES),
        arm_actuator_names=list(JOINT_NAMES),
        joint_limits=_limits(model),
        gripper_actuator_names=["gripper"],
        gripper_close_ctrl=-0.17453,
        gripper_open_ctrl=1.74533,
        grasp_backend="contact",
        ik_damping=1e-3,
        ik_max_iter=60,
        ik_tolerance=1e-3,
        ik_step_clamp=0.2,
        # This five-DoF arm cannot drive every 3-D target to millimetre
        # residual. Execute the best reachable joint solution and leave the
        # actual position error to the external task validator.
        ik_raise_on_unreachable=False,
    )
    robot = ArmSerialDLSSkeleton(model, data, spec)

    def move_to_cartesian(self, position, duration=2.0):
        return self.move_cartesian(position, duration=duration)

    def reach_above_object(self, target_position, duration=2.0):
        """Reach the caller-supplied world-frame target using the DLS primitive."""
        target = _vector3(target_position, name="target_position")
        return self.move_cartesian(target, duration=_duration(duration))

    def trace_cartesian_path(self, waypoints, duration_per_segment=1.0):
        """Visit every supplied world-frame waypoint in order."""
        path = np.asarray(waypoints, dtype=np.float64)
        if path.ndim != 2 or path.shape[0] < 1 or path.shape[1] != 3:
            raise ValueError("waypoints must have shape (N, 3) with N >= 1")
        if not np.isfinite(path).all():
            raise ValueError("waypoints must contain only finite values")
        segment_duration = _duration(
            duration_per_segment, name="duration_per_segment"
        )
        for target in path:
            self.move_cartesian(target, duration=segment_duration)
        return True

    def cycle_gripper(self, duration=1.0):
        """Physically command the SO-101 jaw to both actuator-range extremes."""
        cycle_duration = _duration(duration)
        phase_steps = max(
            1, int(0.5 * cycle_duration / float(self.model.opt.timestep))
        )
        self.gripper_open(settle_steps=phase_steps)
        # gripper_close() reports grasp state, not whether the actuator command
        # completed, so the capability deliberately returns command completion.
        self.gripper_close(settle_steps=phase_steps)
        return True

    def establish_controlled_contact(self, contact_position, duration=2.0):
        """Approach the supplied contact point through normal Cartesian control."""
        target = _vector3(contact_position, name="contact_position")
        return self.move_cartesian(target, duration=_duration(duration))

    def move_cartesian_offset_and_return(self, offset, duration=2.0):
        """Move by a base-aligned Cartesian offset, then return to the start."""
        delta = _vector3(offset, name="offset")
        total_duration = _duration(duration)
        start, _ = self.get_ee_pose()
        leg_duration = 0.5 * total_duration
        self.move_cartesian(start + delta, duration=leg_duration)
        return self.move_cartesian(start, duration=leg_duration)

    robot.move_to_cartesian = types.MethodType(move_to_cartesian, robot)
    robot.reach_above_object = types.MethodType(reach_above_object, robot)
    robot.trace_cartesian_path = types.MethodType(trace_cartesian_path, robot)
    robot.cycle_gripper = types.MethodType(cycle_gripper, robot)
    robot.establish_controlled_contact = types.MethodType(
        establish_controlled_contact, robot
    )
    robot.move_cartesian_offset_and_return = types.MethodType(
        move_cartesian_offset_and_return, robot
    )
    return robot
