"""Franka Panda calibration controller for the private Direct-MuJoCo Harness.

This helper is calibration-only. It receives the Framework-owned model/data,
writes actuator targets through ``data.ctrl``, and advances only with
``mujoco.mj_step``. It does not construct or reset MuJoCo state.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

import mujoco
import numpy as np


ARM_JOINTS = tuple(f"joint{index}" for index in range(1, 8))
ARM_ACTUATORS = tuple(f"actuator{index}" for index in range(1, 8))
GRIPPER_ACTUATOR = "actuator8"
GRIPPER_CLOSED = 0.0
GRIPPER_OPEN = 255.0
WORLD_Z_QUARTER_TURN = np.asarray(
    ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)), dtype=float
)


def _vector(value: Any, *, name: str) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if result.shape != (3,) or not np.isfinite(result).all():
        raise ValueError(f"{name} must be a finite 3-vector")
    return result


def _request(value: Any) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        raise ValueError("request must be an object")
    task_id = value.get("task_id")
    parameters = value.get("task_parameters")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("request.task_id must be a non-empty string")
    if not isinstance(parameters, Mapping):
        raise ValueError("request.task_parameters must be an object")
    return task_id, parameters


class ReferenceFrankaPandaDriver:
    """Small actuator-only controller used to calibrate package fixtures."""

    def __init__(self, *, model: Any, data: Any) -> None:
        self.model = model
        self.data = data
        self._hand = self._id(mujoco.mjtObj.mjOBJ_BODY, "hand")
        self._joint_ids = [
            self._id(mujoco.mjtObj.mjOBJ_JOINT, name) for name in ARM_JOINTS
        ]
        self._qpos_addresses = [int(model.jnt_qposadr[index]) for index in self._joint_ids]
        self._qvel_addresses = [int(model.jnt_dofadr[index]) for index in self._joint_ids]
        self._actuator_ids = [
            self._id(mujoco.mjtObj.mjOBJ_ACTUATOR, name) for name in ARM_ACTUATORS
        ]
        self._gripper_id = self._id(mujoco.mjtObj.mjOBJ_ACTUATOR, GRIPPER_ACTUATOR)
        self._lower = np.asarray(
            [model.jnt_range[index, 0] for index in self._joint_ids], dtype=float
        )
        self._upper = np.asarray(
            [model.jnt_range[index, 1] for index in self._joint_ids], dtype=float
        )
        self._arm_target = self._current_q()
        self._timestep = float(model.opt.timestep)
        if self._timestep <= 0:
            raise ValueError("canonical model timestep must be positive")

    def _id(self, object_type: Any, name: str) -> int:
        identifier = int(mujoco.mj_name2id(self.model, object_type, name))
        if identifier < 0:
            raise ValueError(f"canonical model is missing {name!r}")
        return identifier

    def _ee_position(self) -> np.ndarray:
        return np.asarray(self.data.xpos[self._hand], dtype=float).copy()

    def _ee_rotation(self) -> np.ndarray:
        return np.asarray(self.data.xmat[self._hand], dtype=float).copy().reshape(3, 3)

    def _current_q(self) -> np.ndarray:
        return np.asarray(
            [self.data.qpos[address] for address in self._qpos_addresses], dtype=float
        )

    def _body_position(self, name: str) -> np.ndarray:
        body_id = self._id(mujoco.mjtObj.mjOBJ_BODY, name)
        return np.asarray(self.data.xpos[body_id], dtype=float).copy()

    def _body_point_position(self, name: str, local_position: np.ndarray) -> np.ndarray:
        body_id = self._id(mujoco.mjtObj.mjOBJ_BODY, name)
        rotation = np.asarray(self.data.xmat[body_id], dtype=float).reshape(3, 3)
        return self._body_position(name) + rotation @ _vector(
            local_position, name="local_position"
        )

    def _set_arm_target(self, target: np.ndarray) -> None:
        self._arm_target = np.asarray(target, dtype=float).copy()
        for actuator_id, value in zip(self._actuator_ids, target):
            self.data.ctrl[actuator_id] = float(value)

    def _set_gripper(self, value: float) -> None:
        self.data.ctrl[self._gripper_id] = float(np.clip(value, 0.0, 255.0))

    def _hold_arm(self) -> None:
        for actuator_id, value in zip(self._actuator_ids, self._arm_target):
            self.data.ctrl[actuator_id] = float(value)

    def _step_to(
        self,
        target: np.ndarray,
        *,
        steps: int = 1200,
        tolerance: float = 0.004,
        residual_tolerance: float = 0.05,
        wrist_roll: float | None = None,
        gain: float = 1.8,
        max_joint_delta: float = 0.12,
    ) -> None:
        target = _vector(target, name="target_position")
        if not math.isfinite(gain) or gain <= 0.0:
            raise ValueError("gain must be a positive finite number")
        if not math.isfinite(max_joint_delta) or max_joint_delta <= 0.0:
            raise ValueError("max_joint_delta must be a positive finite number")
        if wrist_roll is not None:
            wrist_roll = float(wrist_roll)
            if not math.isfinite(wrist_roll):
                raise ValueError("wrist_roll must be finite")
            wrist_roll = float(np.clip(wrist_roll, self._lower[-1], self._upper[-1]))
        for _ in range(int(steps)):
            error = target - self._ee_position()
            jacobian = np.zeros((3, int(self.model.nv)), dtype=float)
            mujoco.mj_jacBody(self.model, self.data, jacobian, None, self._hand)
            controlled_addresses = (
                self._qvel_addresses
                if wrist_roll is None
                else self._qvel_addresses[:-1]
            )
            arm_jacobian = jacobian[:, controlled_addresses]
            damping = 0.02
            system = arm_jacobian @ arm_jacobian.T + (damping * damping) * np.eye(3)
            delta = arm_jacobian.T @ np.linalg.solve(system, error)
            delta_norm = float(np.linalg.norm(delta))
            if delta_norm > max_joint_delta:
                delta *= max_joint_delta / delta_norm
            desired = self._current_q()
            if wrist_roll is None:
                desired += gain * delta
            else:
                desired[:-1] += gain * delta
                desired[-1] = wrist_roll
            desired = np.clip(desired, self._lower, self._upper)
            self._set_arm_target(desired)
            mujoco.mj_step(self.model, self.data)
            if float(np.linalg.norm(error)) <= tolerance:
                for _ in range(30):
                    self._hold_arm()
                    mujoco.mj_step(self.model, self.data)
                return
        residual = float(np.linalg.norm(target - self._ee_position()))
        if residual <= residual_tolerance:
            return
        raise RuntimeError(f"reference controller did not reach target; residual={residual:.5f}")

    def _idle(self, steps: int = 30) -> None:
        for _ in range(int(steps)):
            self._hold_arm()
            mujoco.mj_step(self.model, self.data)

    def _pose_steps(
        self,
        position: np.ndarray,
        rotation: np.ndarray,
        *,
        steps: int,
        gain: float = 1.5,
        max_joint_delta: float = 0.05,
    ) -> None:
        position = _vector(position, name="target_position")
        rotation = np.asarray(rotation, dtype=float)
        if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
            raise ValueError("target_rotation must be a finite 3x3 matrix")
        if int(steps) <= 0:
            raise ValueError("steps must be positive")
        if not math.isfinite(gain) or gain <= 0.0:
            raise ValueError("gain must be a positive finite number")
        if not math.isfinite(max_joint_delta) or max_joint_delta <= 0.0:
            raise ValueError("max_joint_delta must be a positive finite number")

        orientation_weight = 0.35
        damping = 0.03
        for _ in range(int(steps)):
            current_rotation = self._ee_rotation()
            rotation_error = 0.5 * sum(
                np.cross(current_rotation[:, axis], rotation[:, axis])
                for axis in range(3)
            )
            error = np.concatenate(
                (
                    position - self._ee_position(),
                    orientation_weight * rotation_error,
                )
            )
            position_jacobian = np.zeros((3, int(self.model.nv)), dtype=float)
            rotation_jacobian = np.zeros((3, int(self.model.nv)), dtype=float)
            mujoco.mj_jacBody(
                self.model,
                self.data,
                position_jacobian,
                rotation_jacobian,
                self._hand,
            )
            arm_jacobian = np.vstack(
                (
                    position_jacobian[:, self._qvel_addresses],
                    orientation_weight
                    * rotation_jacobian[:, self._qvel_addresses],
                )
            )
            system = arm_jacobian @ arm_jacobian.T + (damping * damping) * np.eye(6)
            delta = arm_jacobian.T @ np.linalg.solve(system, error)
            delta_norm = float(np.linalg.norm(delta))
            if delta_norm > max_joint_delta:
                delta *= max_joint_delta / delta_norm
            desired = np.clip(
                self._current_q() + gain * delta,
                self._lower,
                self._upper,
            )
            self._set_arm_target(desired)
            mujoco.mj_step(self.model, self.data)

    def _hold_arm_target(self, target: np.ndarray, *, steps: int) -> None:
        target = np.asarray(target, dtype=float)
        if target.shape != (len(self._actuator_ids),) or not np.isfinite(target).all():
            raise ValueError("arm target must contain one finite value per arm actuator")
        if int(steps) <= 0:
            raise ValueError("steps must be positive")
        self._set_arm_target(np.clip(target, self._lower, self._upper))
        self._idle(steps)

    def _carry_pick_place_object(
        self, destination_xy: np.ndarray, *, segments: int = 30
    ) -> None:
        destination_xy = np.asarray(destination_xy, dtype=float)
        if destination_xy.shape != (2,) or not np.isfinite(destination_xy).all():
            raise ValueError("destination_xy must be a finite 2-vector")
        segments = int(segments)
        if segments <= 0:
            raise ValueError("segments must be positive")
        start_hand = self._ee_position()
        start_object = self._body_position("workpiece")
        goal_hand = start_hand.copy()
        goal_hand[:2] += destination_xy - start_object[:2]
        for fraction in np.linspace(1.0 / segments, 1.0, segments):
            waypoint = start_hand + fraction * (goal_hand - start_hand)
            self._pose_steps(
                waypoint,
                self._pick_rotation,
                steps=15,
                gain=1.8,
                max_joint_delta=0.018,
            )

    def _grasp_and_lift_object(self, *, high_lift: bool) -> None:
        object_position = self._body_position("workpiece")
        grasp = object_position + np.asarray((0.0, 0.0, 0.111))
        normal_pregrasp = grasp + np.asarray((0.0, 0.0, 0.095))
        high_pregrasp = grasp + np.asarray((0.0, 0.0, 0.13))

        self._set_gripper(GRIPPER_OPEN)
        self._idle(30)
        high_target = None
        if high_lift:
            self._pose_steps(
                high_pregrasp,
                self._pick_rotation,
                steps=1200,
            )
            high_target = self._current_q()
        self._pose_steps(
            normal_pregrasp,
            self._pick_rotation,
            steps=1200,
        )
        normal_target = self._current_q()
        self._pose_steps(
            grasp,
            self._pick_rotation,
            steps=1200,
            gain=1.2,
            max_joint_delta=0.04,
        )
        self._set_gripper(GRIPPER_CLOSED)
        self._pose_steps(
            grasp,
            self._pick_rotation,
            steps=250,
            gain=1.0,
            max_joint_delta=0.02,
        )
        self._hold_arm_target(normal_target, steps=240)
        if high_target is not None:
            self._hold_arm_target(high_target, steps=240)

    def _pick_place_hop(
        self,
        destination_xy: np.ndarray,
        *,
        high_lift: bool,
        carry_segments: int = 30,
    ) -> None:
        self._grasp_and_lift_object(high_lift=high_lift)
        self._carry_pick_place_object(destination_xy, segments=carry_segments)
        self._set_gripper(GRIPPER_OPEN)
        self._idle(250)

    def _insert_peg(self, target: np.ndarray) -> None:
        self._grasp_and_lift_object(high_lift=True)
        self._carry_pick_place_object(
            np.asarray((target[0], 0.02)),
            segments=30,
        )

        peg_head = self._body_point_position(
            "workpiece", np.asarray((0.0, -0.054, 0.0))
        )
        aligned_hand = self._ee_position()
        aligned_hand[2] += target[2] - peg_head[2]
        self._pose_steps(
            aligned_hand,
            self._pick_rotation,
            steps=1200,
            gain=1.2,
            max_joint_delta=0.03,
        )

        peg_head = self._body_point_position(
            "workpiece", np.asarray((0.0, -0.054, 0.0))
        )
        workpiece = self._body_position("workpiece")
        insertion_xy = workpiece[:2] + target[:2] - peg_head[:2]
        self._carry_pick_place_object(insertion_xy, segments=45)
        self._idle(250)

    def _reach_parameter(
        self, task_parameters: Mapping[str, Any], key: str = "target_position"
    ) -> np.ndarray:
        return _vector(task_parameters[key], name=key)

    def _tool_target(self, task_parameters: Mapping[str, Any]) -> np.ndarray:
        return _vector(
            task_parameters.get("tool_target_position", task_parameters["target_position"]),
            name="tool_target_position",
        )

    def reach_task(self, request: Any) -> None:
        _, parameters = _request(request)
        self._set_gripper(GRIPPER_OPEN)
        self._step_to(self._reach_parameter(parameters))

    def contact_task(self, request: Any) -> None:
        task_id, parameters = _request(request)
        contact = _vector(parameters["contact_position"], name="contact_position")
        approach = _vector(
            parameters.get("approach_position", contact + np.asarray((0.0, 0.0, 0.08))),
            name="approach_position",
        )
        self._set_gripper(GRIPPER_CLOSED)
        self._idle(20)
        self._step_to(approach, residual_tolerance=0.08)
        self._step_to(contact, residual_tolerance=0.08)
        if "route_position" in parameters:
            self._step_to(
                _vector(parameters["route_position"], name="route_position"),
                steps=700,
                residual_tolerance=0.08 if task_id == "mw_push_wall" else 0.25,
            )
            self._step_to(
                self._tool_target(parameters),
                steps=1200,
                residual_tolerance=0.12,
                gain=0.2,
                max_joint_delta=0.012,
            )
            return
        tool_target = self._tool_target(parameters)
        if task_id == "mw_push_to_goal":
            self._step_to(
                tool_target,
                steps=1200,
                residual_tolerance=0.18,
                gain=0.2,
                max_joint_delta=0.012,
            )
            return
        target = self._reach_parameter(parameters)
        push_height = max(float(target[2]) + 0.024, 0.44)
        self._step_to(
            np.asarray((contact[0], contact[1], push_height), dtype=float),
            steps=700,
            residual_tolerance=0.10,
            gain=0.4,
            max_joint_delta=0.025,
        )
        self._step_to(
            np.asarray((tool_target[0], tool_target[1], push_height), dtype=float),
            steps=1400,
            residual_tolerance=0.15,
            gain=0.35,
            max_joint_delta=0.02,
        )
        self._idle(90)

    def object_task(self, request: Any) -> None:
        task_id, parameters = _request(request)
        start = _vector(parameters["start_position"], name="start_position")
        target = self._reach_parameter(parameters)
        if task_id in {
            "mw_pick_place",
            "mw_pick_place_wall",
            "mw_peg_insertion_side",
        }:
            self._arm_target = self._current_q()
            self._pick_rotation = self._ee_rotation()
            if task_id == "mw_peg_insertion_side":
                self._insert_peg(target)
                return
            if task_id == "mw_pick_place_wall":
                self._pick_rotation = WORLD_Z_QUARTER_TURN @ self._pick_rotation
                self._pick_place_hop(
                    target[:2],
                    high_lift=True,
                    carry_segments=60,
                )
                return
            midpoint_xy = (start[:2] + target[:2]) / 2.0
            self._pick_place_hop(midpoint_xy, high_lift=False)
            self._pick_place_hop(target[:2], high_lift=True)
            return
        grasp = _vector(
            parameters.get("grasp_position", start + np.asarray((0.0, 0.0, 0.005))),
            name="grasp_position",
        )
        release = _vector(
            parameters.get("release_position", target + np.asarray((0.0, 0.0, 0.07))),
            name="release_position",
        )
        approach_height = float(parameters.get("approach_height", 0.095))
        if not np.isfinite(approach_height) or approach_height <= 0.0:
            raise ValueError("approach_height must be a positive finite number")
        grasp_wrist_roll = float(parameters.get("grasp_wrist_roll", math.pi / 2.0))
        if not math.isfinite(grasp_wrist_roll):
            raise ValueError("grasp_wrist_roll must be finite")
        grasp_gripper = float(
            parameters.get(
                "grasp_gripper",
                (0.25 + 0.17453) / (1.74533 + 0.17453) * 255.0,
            )
        )
        if not math.isfinite(grasp_gripper):
            raise ValueError("grasp_gripper must be finite")
        pregrasp = grasp + np.asarray((0.0, 0.0, approach_height))
        lift = grasp + np.asarray((0.0, 0.0, approach_height))
        self._set_gripper(GRIPPER_OPEN)
        self._idle(20)
        self._step_to(pregrasp, residual_tolerance=0.08, wrist_roll=grasp_wrist_roll)
        self._step_to(grasp, residual_tolerance=0.08, wrist_roll=grasp_wrist_roll)
        self._set_gripper(grasp_gripper)
        self._idle(90)
        self._step_to(lift, steps=700, residual_tolerance=0.12, wrist_roll=grasp_wrist_roll)
        if "route_position" in parameters:
            self._step_to(
                _vector(parameters["route_position"], name="route_position"),
                steps=700,
                residual_tolerance=0.25,
                wrist_roll=grasp_wrist_roll,
                gain=0.4,
                max_joint_delta=0.025,
            )
        self._step_to(
            release,
            steps=700,
            residual_tolerance=0.12,
            wrist_roll=grasp_wrist_roll,
            gain=0.5,
            max_joint_delta=0.03,
        )
        self._step_to(
            _vector(parameters.get("tool_target_position", release), name="tool_target_position"),
            steps=500,
            residual_tolerance=0.12,
            wrist_roll=grasp_wrist_roll,
        )
        if task_id == "mw_peg_insertion_side":
            self._idle(90)
            return
        self._set_gripper(GRIPPER_OPEN)
        self._idle(90)

    def fixture_task(self, request: Any) -> None:
        task_id, parameters = _request(request)
        if task_id in {"mw_drawer_open", "mw_drawer_close", "mw_handle_pull"}:
            contact = _vector(parameters["contact_position"], name="contact_position")
            if task_id == "mw_handle_pull":
                contact = contact + np.asarray((0.0, 0.0, -0.08))
            approach = _vector(
                parameters.get("approach_position", contact + np.asarray((0.0, -0.07, 0.0))),
                name="approach_position",
            )
            wrist_roll = math.pi / 2.0
            self._set_gripper((0.5 + 0.17453) / (1.74533 + 0.17453) * 255.0)
            self._idle(30)
            self._step_to(approach, residual_tolerance=0.08, wrist_roll=wrist_roll)
            self._step_to(contact, residual_tolerance=0.08, wrist_roll=wrist_roll)
            self._set_gripper(
                0.0
                if task_id == "mw_handle_pull"
                else (0.05 + 0.17453) / (1.74533 + 0.17453) * 255.0
            )
            self._idle(80)
            tool_target = self._tool_target(parameters)
            if task_id == "mw_handle_pull":
                tool_target = tool_target + np.asarray((0.0, 0.0, 0.05))
            self._step_to(
                tool_target,
                steps=1000,
                residual_tolerance=0.18 if task_id == "mw_handle_pull" else 0.10,
                wrist_roll=wrist_roll,
                gain=0.5,
                max_joint_delta=0.025,
            )
            self._idle(40)
            return
        contact = _vector(parameters["contact_position"], name="contact_position")
        if task_id == "mw_button_press":
            approach_offset = np.asarray((0.0, -0.07, 0.0))
        elif task_id in {"mw_door_open", "mw_door_close"}:
            approach_offset = np.asarray((0.0, 0.0, 0.08))
        else:
            approach_offset = np.asarray((0.0, 0.0, 0.07))
        approach = _vector(
            parameters.get("approach_position", contact + approach_offset),
            name="approach_position",
        )
        self._set_gripper(GRIPPER_CLOSED)
        self._idle(20)
        self._step_to(
            approach,
            residual_tolerance=0.07 if task_id in {"mw_door_open", "mw_door_close"} else 0.08,
        )
        self._step_to(
            contact,
            residual_tolerance=0.06 if task_id in {"mw_door_open", "mw_door_close"} else 0.08,
        )
        if "route_position" in parameters:
            route_tolerance = {"mw_door_open": 0.09, "mw_door_close": 0.07}.get(task_id, 0.10)
            self._step_to(
                _vector(parameters["route_position"], name="route_position"),
                steps=700,
                residual_tolerance=route_tolerance,
                gain=0.4,
                max_joint_delta=0.025,
            )
        if task_id == "mw_door_open":
            open_target = self._tool_target(parameters) + np.asarray((-0.25, 0.20, 0.0))
            self._step_to(
                open_target,
                steps=1800,
                residual_tolerance=0.40,
                gain=0.3,
                max_joint_delta=0.02,
            )
            self._idle(60)
            return
        self._step_to(
            self._tool_target(parameters),
            steps=1000,
            residual_tolerance=0.11 if task_id == "mw_door_close" else (0.08 if task_id == "mw_door_open" else 0.10),
            gain=0.3,
            max_joint_delta=0.02,
        )
        self._idle(60)

    def rotation_task(self, request: Any) -> None:
        _, parameters = _request(request)
        contact = _vector(parameters["contact_position"], name="contact_position")
        approach = _vector(
            parameters.get("approach_position", contact + np.asarray((0.0, 0.0, 0.08))),
            name="approach_position",
        )
        self._set_gripper(GRIPPER_CLOSED)
        self._idle(20)
        self._step_to(approach, residual_tolerance=0.10)
        self._step_to(contact, residual_tolerance=0.10)
        if "route_position" in parameters:
            self._step_to(
                _vector(parameters["route_position"], name="route_position"),
                steps=800,
                residual_tolerance=0.12,
                gain=0.4,
                max_joint_delta=0.025,
            )
        self._step_to(
            self._tool_target(parameters),
            steps=1000,
            residual_tolerance=0.12,
            gain=0.3,
            max_joint_delta=0.02,
        )
        self._idle(90)


def build(*, model: Any, data: Any) -> ReferenceFrankaPandaDriver:
    return ReferenceFrankaPandaDriver(model=model, data=data)
