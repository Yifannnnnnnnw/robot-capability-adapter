"""Small 1.0-skeleton assembly helpers for Demo2 reference task baselines."""
from __future__ import annotations

import types
from pathlib import Path
from typing import Any, Iterable

import mujoco
import numpy as np

from auto_adapter.skeletons import ArmSerialDLSSkeleton, ArmSpec
from auto_adapter.skeletons.arm_serial_dls import IKUnreachableError


def _vector3(value: Any, name: str) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if result.shape != (3,) or not np.isfinite(result).all():
        raise ValueError(f"{name} must be a finite 3-vector")
    return result


def _seconds(value: Any) -> float:
    result = float(value)
    if not np.isfinite(result) or result <= 0:
        raise ValueError("duration must be finite and positive")
    return result


def _limits(model: Any, joint_names: list[str]) -> dict[str, tuple[float, float]]:
    result: dict[str, tuple[float, float]] = {}
    for name in joint_names:
        identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if identifier < 0:
            raise ValueError(f"joint {name!r} is absent")
        lo, hi = (float(value) for value in model.jnt_range[identifier])
        # Unlimited hinge ranges appear as 0,0; retain a finite DLS workspace.
        if hi <= lo:
            lo, hi = -2.0 * np.pi, 2.0 * np.pi
        result[name] = (lo, hi)
    return result


def build_serial(
    *,
    mjcf_path: str | None,
    model: Any,
    data: Any,
    ee_site: str,
    joint_names: list[str],
    actuator_names: list[str],
    home_qpos: list[float] | None = None,
    gripper_actuator_names: list[str] | None = None,
    gripper_open_ctrl: float = 1.0,
    gripper_close_ctrl: float = 0.0,
    grasp_backend: str = "noop",
    graspable_bodies: list[str] | None = None,
    task_effects: Iterable[str] | None = None,
) -> ArmSerialDLSSkeleton:
    if model is None:
        path = Path(mjcf_path or "mjcf.xml").resolve()
        model = mujoco.MjModel.from_xml_path(str(path))
    if data is None:
        data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    robot = ArmSerialDLSSkeleton(
        model,
        data,
        ArmSpec(
            ee_site_name=ee_site,
            arm_joint_names=list(joint_names),
            arm_actuator_names=list(actuator_names),
            joint_limits=_limits(model, joint_names),
            home_qpos=home_qpos,
            gripper_actuator_names=gripper_actuator_names,
            gripper_open_ctrl=gripper_open_ctrl,
            gripper_close_ctrl=gripper_close_ctrl,
            grasp_backend=grasp_backend,
            weld_graspable_bodies=graspable_bodies,
            ik_damping=1e-3,
            ik_max_iter=120,
            ik_tolerance=1e-3,
            ik_step_clamp=0.2,
            ik_raise_on_unreachable=True,
        ),
    )
    _attach_task_methods(robot, task_effects=task_effects)
    return robot


def _move_cartesian_best_effort(
    robot: ArmSerialDLSSkeleton,
    target: Any,
    duration: Any,
) -> bool:
    """Run DLS + actuator interpolation and leave success to external state.

    The copied arm models include singular source-default poses.  A deterministic
    keyframe seed, when present, gives DLS a second legitimate initial guess.
    If a target still misses the skeleton's millimetre tolerance, the closest
    computed joint solution is physically executed; no task verdict is inferred
    from that execution.
    """

    target_vector = _vector3(target, "target_position")
    motion_duration = _seconds(duration)
    seeds = [robot.get_joint_positions()]
    if int(robot.model.nkey) > 0:
        key_seed = np.asarray(
            [robot.model.key_qpos[0, address] for address in robot._arm_qpos_adr],
            dtype=float,
        )
        if not np.allclose(key_seed, seeds[0]):
            seeds.append(key_seed)

    candidates: list[tuple[float, np.ndarray]] = []
    for seed in seeds:
        q_target = robot.ik(
            target_vector,
            q_init=seed,
            raise_on_unreachable=False,
        )
        residual = float(np.linalg.norm(robot.fk(q_target)["pos"] - target_vector))
        candidates.append((residual, q_target))
    _, best = min(candidates, key=lambda item: item[0])
    return robot.move_joints(best, duration=motion_duration)


def _attach_task_methods(
    robot: ArmSerialDLSSkeleton,
    *,
    task_effects: Iterable[str] | None,
) -> None:
    def reach_target(self, target_position, duration=1.0):
        return _move_cartesian_best_effort(self, target_position, duration)

    def reach_above_object(self, target_position, duration=1.0):
        return reach_target(self, target_position, duration)

    def trace_cartesian_path(self, waypoints, duration_per_segment=0.5):
        values = np.asarray(waypoints, dtype=float)
        if values.ndim != 2 or values.shape[1:] != (3,) or not np.isfinite(values).all():
            raise ValueError("waypoints must be a finite N x 3 array")
        for value in values:
            _move_cartesian_best_effort(self, value, duration_per_segment)
        return True

    def move_cartesian_offset_and_return(self, offset, duration=1.0):
        start = self.get_ee_pose()[0]
        leg = 0.5 * _seconds(duration)
        _move_cartesian_best_effort(self, start + _vector3(offset, "offset"), leg)
        return _move_cartesian_best_effort(self, start, leg)

    def visit_cartesian_waypoints(self, waypoints, duration_per_segment=0.5):
        return trace_cartesian_path(self, waypoints, duration_per_segment)

    def reject_unreachable_and_return_home(self, target_position, duration=1.0):
        target = _vector3(target_position, "target_position")
        motion_duration = _seconds(duration)
        try:
            q_target = self.ik(target)
        except IKUnreachableError:
            self.home(duration=max(0.1, 0.5 * motion_duration))
            return {"outcome": "rejected", "reason": "unreachable_target"}
        self.move_joints(q_target, duration=motion_duration)
        self.home(duration=max(0.1, 0.5 * motion_duration))
        return {"outcome": "reached", "reason": None}

    def push_object_to_goal(self, object_position, goal_position, duration=1.0):
        start = _vector3(object_position, "object_position")
        goal = _vector3(goal_position, "goal_position")
        delta = goal - start
        length = float(np.linalg.norm(delta[:2]))
        direction = delta / max(length, 1e-9)
        behind = start - 0.035 * direction
        behind[2] = start[2] + 0.025
        contact = start.copy()
        contact[2] = start[2] + 0.02
        leg = _seconds(duration) / 3.0
        _move_cartesian_best_effort(self, behind, leg)
        _move_cartesian_best_effort(self, contact, leg)
        return _move_cartesian_best_effort(self, goal, leg)

    def grasp_and_lift(self, object_position, lift, duration=1.0):
        start = _vector3(object_position, "object_position")
        height = float(lift)
        if not np.isfinite(height) or height <= 0:
            raise ValueError("lift must be finite and positive")
        leg = _seconds(duration) / 3.0
        self.gripper_open()
        _move_cartesian_best_effort(
            self, start + np.array([0.0, 0.0, 0.02]), leg
        )
        self.gripper_close()
        return _move_cartesian_best_effort(
            self, start + np.array([0.0, 0.0, height + 0.02]), leg
        )

    def establish_controlled_contact(self, contact_position, duration=1.0):
        return _move_cartesian_best_effort(self, contact_position, duration)

    def press_button(self, button_position, duration=1.0):
        return _move_cartesian_best_effort(self, button_position, duration)

    def move_to_waypoint_and_return(self, waypoint, duration=1.0):
        start = self.get_ee_pose()[0]
        leg = 0.5 * _seconds(duration)
        _move_cartesian_best_effort(self, _vector3(waypoint, "waypoint"), leg)
        return _move_cartesian_best_effort(self, start, leg)

    methods = {
        "reach_target": reach_target,
        "reach_above_object": reach_above_object,
        "trace_cartesian_path": trace_cartesian_path,
        "move_cartesian_offset_and_return": move_cartesian_offset_and_return,
        "visit_cartesian_waypoints": visit_cartesian_waypoints,
        "reject_unreachable_and_return_home": reject_unreachable_and_return_home,
        "push_object_to_goal": push_object_to_goal,
        "grasp_and_lift": grasp_and_lift,
        "establish_controlled_contact": establish_controlled_contact,
        "press_button": press_button,
        "move_to_waypoint_and_return": move_to_waypoint_and_return,
    }
    selected = set(methods) if task_effects is None else set(task_effects)
    unknown = selected - set(methods)
    if unknown:
        raise ValueError(f"unknown task effects: {sorted(unknown)}")
    for name, method in methods.items():
        if name not in selected:
            continue
        setattr(robot, name, types.MethodType(method, robot))
