"""AutoAdapter 1.0-style Franka driver using the proven serial-arm DLS family."""
from __future__ import annotations

from pathlib import Path
import types

import mujoco
import numpy as np

from auto_adapter.skeletons import ArmSerialDLSSkeleton, ArmSpec


JOINT_NAMES = [f"joint{index}" for index in range(1, 8)]
ACTUATOR_NAMES = [f"actuator{index}" for index in range(1, 8)]


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
        key = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
        mujoco.mj_resetDataKeyframe(model, data, key)
        mujoco.mj_forward(model, data)
    spec = ArmSpec(
        ee_body_name="hand",
        arm_joint_names=list(JOINT_NAMES),
        arm_actuator_names=list(ACTUATOR_NAMES),
        joint_limits=_limits(model),
        grasp_backend="noop",
        ik_damping=1e-3,
        ik_max_iter=60,
        ik_tolerance=1e-3,
        ik_step_clamp=0.2,
    )
    robot = ArmSerialDLSSkeleton(model, data, spec)

    def move_to_cartesian(self, position, duration=2.0):
        return self.move_cartesian(position, duration=duration)

    def reach_above_object(self, target_position, duration=2.0):
        """Reach the caller-supplied world-frame target using DLS IK."""
        target = _vector3(target_position, name="target_position")
        return self.move_cartesian(target, duration=_duration(duration))

    def push_object_to_goal(
        self, object_position, goal_position, duration=2.0
    ):
        """Approach behind an object and drive the hand toward its goal.

        This is intentionally a direct MuJoCo motion primitive: object state is
        never teleported. Any object displacement must come from contact with
        the actuated Franka hand.
        """
        object_target = _vector3(object_position, name="object_position")
        goal_target = _vector3(goal_position, name="goal_position")
        total_duration = _duration(duration)
        displacement = goal_target - object_target
        distance = float(np.linalg.norm(displacement))
        if distance > 1e-9:
            approach_target = object_target - 0.04 * displacement / distance
        else:
            approach_target = object_target.copy()
        leg_duration = 0.5 * total_duration
        self.move_cartesian(approach_target, duration=leg_duration)
        return self.move_cartesian(goal_target, duration=leg_duration)

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

    robot.move_to_cartesian = types.MethodType(move_to_cartesian, robot)
    robot.reach_above_object = types.MethodType(reach_above_object, robot)
    robot.push_object_to_goal = types.MethodType(push_object_to_goal, robot)
    robot.trace_cartesian_path = types.MethodType(trace_cartesian_path, robot)
    return robot
