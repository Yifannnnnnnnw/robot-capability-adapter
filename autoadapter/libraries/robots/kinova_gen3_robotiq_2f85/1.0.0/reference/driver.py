"""Kinova Gen3 with Robotiq 2F-85 calibration controller.

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

from autoadapter2.trusted_skeletons.arm_serial_dls import ArmSerialDLSSkeleton, ArmSpec


ARM_JOINTS = tuple(f"joint_{index}" for index in range(1, 8))
ARM_ACTUATORS = ARM_JOINTS
CONTINUOUS_JOINTS = frozenset(("joint_1", "joint_3", "joint_5", "joint_7"))
ARM_LIMITS = {
    "joint_2": (-2.24, 2.24),
    "joint_4": (-2.57, 2.57),
    "joint_6": (-2.09, 2.09),
}
HOME_ARM = (0.0, 0.26179939, math.pi, -2.26892803, 0.0, 0.95993109, math.pi / 2.0)
GRIPPER_ACTUATOR = "fingers_actuator"
GRIPPER_CLOSED = 255.0
GRIPPER_OPEN = 0.0


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


def _nearest_equivalent(angle: float, reference: float) -> float:
    return float(angle + 2.0 * math.pi * round((reference - angle) / (2.0 * math.pi)))


class ReferenceKinovaGen3Robotiq2F85Driver:
    """Small actuator-only controller used to calibrate package fixtures."""

    def __init__(self, *, model: Any, data: Any) -> None:
        self.model = model
        self.data = data
        self._ee_site = self._id(mujoco.mjtObj.mjOBJ_SITE, "pinch_site")
        self._joint_ids = [
            self._id(mujoco.mjtObj.mjOBJ_JOINT, name) for name in ARM_JOINTS
        ]
        self._qpos_addresses = [int(model.jnt_qposadr[index]) for index in self._joint_ids]
        self._qvel_addresses = [int(model.jnt_dofadr[index]) for index in self._joint_ids]
        self._actuator_ids = [
            self._id(mujoco.mjtObj.mjOBJ_ACTUATOR, name) for name in ARM_ACTUATORS
        ]
        self._gripper_id = self._id(mujoco.mjtObj.mjOBJ_ACTUATOR, GRIPPER_ACTUATOR)
        self._continuous = np.asarray(
            [name in CONTINUOUS_JOINTS for name in ARM_JOINTS], dtype=bool
        )
        self._lower = np.asarray(
            [
                -math.inf if continuous else model.jnt_range[index, 0]
                for index, continuous in zip(self._joint_ids, self._continuous)
            ],
            dtype=float,
        )
        self._upper = np.asarray(
            [
                math.inf if continuous else model.jnt_range[index, 1]
                for index, continuous in zip(self._joint_ids, self._continuous)
            ],
            dtype=float,
        )
        for name, joint_id, continuous in zip(
            ARM_JOINTS, self._joint_ids, self._continuous
        ):
            if continuous and bool(model.jnt_limited[joint_id]):
                raise ValueError(f"continuous joint {name!r} is limited in the canonical model")
            if not continuous and not bool(model.jnt_limited[joint_id]):
                raise ValueError(f"bounded joint {name!r} is unlimited in the canonical model")
        self._arm_target = self._current_q()
        self._timestep = float(model.opt.timestep)
        if self._timestep <= 0:
            raise ValueError("canonical model timestep must be positive")
        self._planner = ArmSerialDLSSkeleton(
            model=model,
            data=data,
            spec=ArmSpec(
                ee_site_name="pinch_site",
                arm_joint_names=ARM_JOINTS,
                arm_actuator_names=ARM_ACTUATORS,
                joint_limits=ARM_LIMITS,
                continuous_joint_names=tuple(CONTINUOUS_JOINTS),
                home_qpos=HOME_ARM,
                ik_max_iter=1000,
                ik_tolerance=0.003,
                ik_step_clamp=0.15,
                gripper_actuator_names=(GRIPPER_ACTUATOR,),
                gripper_close_ctrl=GRIPPER_CLOSED,
                gripper_open_ctrl=GRIPPER_OPEN,
            ),
        )

    def _id(self, object_type: Any, name: str) -> int:
        identifier = int(mujoco.mj_name2id(self.model, object_type, name))
        if identifier < 0:
            raise ValueError(f"canonical model is missing {name!r}")
        return identifier

    def _ee_position(self) -> np.ndarray:
        return np.asarray(self.data.site_xpos[self._ee_site], dtype=float).copy()

    def _ee_rotation(self) -> np.ndarray:
        return np.asarray(self.data.site_xmat[self._ee_site], dtype=float).copy().reshape(3, 3)

    def _current_q(self) -> np.ndarray:
        return np.asarray(
            [self.data.qpos[address] for address in self._qpos_addresses], dtype=float
        )

    def _body_position(self, name: str) -> np.ndarray:
        body_id = self._id(mujoco.mjtObj.mjOBJ_BODY, name)
        return np.asarray(self.data.xpos[body_id], dtype=float).copy()

    def _site_position(self, name: str) -> np.ndarray:
        site_id = self._id(mujoco.mjtObj.mjOBJ_SITE, name)
        return np.asarray(self.data.site_xpos[site_id], dtype=float).copy()

    def _set_arm_target(self, target: np.ndarray) -> None:
        self._arm_target = np.asarray(target, dtype=float).copy()
        for actuator_id, value in zip(self._actuator_ids, target):
            self.data.ctrl[actuator_id] = float(value)

    def _set_gripper(self, value: float) -> None:
        self.data.ctrl[self._gripper_id] = float(np.clip(value, 0.0, 255.0))

    def _hold_arm(self) -> None:
        for actuator_id, value in zip(self._actuator_ids, self._arm_target):
            self.data.ctrl[actuator_id] = float(value)

    def _begin_task(self) -> None:
        self._arm_target = self._current_q()

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
            wrist_roll = _nearest_equivalent(wrist_roll, float(self._current_q()[-1]))
        for _ in range(int(steps)):
            error = target - self._ee_position()
            jacobian = np.zeros((3, int(self.model.nv)), dtype=float)
            mujoco.mj_jacSite(self.model, self.data, jacobian, None, self._ee_site)
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

    def _move_ik_target(
        self, target: np.ndarray, *, q_seed: np.ndarray, duration: float
    ) -> np.ndarray:
        q = self._planner.ik(_vector(target, name="path_target"), q_init=q_seed)
        self._planner.move_joints(q, duration=duration)
        self._arm_target = np.asarray(
            [self.data.ctrl[actuator_id] for actuator_id in self._actuator_ids],
            dtype=float,
        )
        return q

    def _safe_joint_ik_path(
        self, targets: tuple[np.ndarray, ...], *, final_duration: float = 1.5
    ) -> None:
        q = self._current_q()
        for index, target in enumerate(targets):
            duration = final_duration if index >= len(targets) - 2 else 1.0
            q = self._move_ik_target(
                target,
                q_seed=q,
                duration=duration,
            )

    def _safe_horizontal_push(self, contact: np.ndarray, target: np.ndarray) -> None:
        lift = self._ee_position()
        lift[2] = max(0.58, float(lift[2]) + 0.12)
        self._safe_joint_ik_path(
            (
                lift,
                contact + np.asarray((0.0, -0.08, 0.14)),
                contact + np.asarray((0.0, -0.08, 0.0)),
                contact,
                target,
            )
        )
        self._idle(60)

    def _side_grasp_place(
        self, task_id: str, task_parameters: Mapping[str, Any]
    ) -> None:
        start = self._body_position("workpiece")
        contact = start + np.asarray((-0.03, 0.0, 0.0))
        high = contact.copy()
        high[2] = 0.59
        first_lift = self._ee_position()
        first_lift[2] = 0.59

        self._set_gripper(GRIPPER_OPEN)
        self._idle(30)
        q = self._current_q()
        for target in (first_lift, high, contact):
            q = self._move_ik_target(target, q_seed=q, duration=1.3)

        grasp_control = float(task_parameters.get("grasp_gripper", 198.6129978227579))
        if not math.isfinite(grasp_control):
            raise ValueError("grasp_gripper must be finite")
        self._set_gripper(grasp_control)
        self._idle(120)
        q = self._move_ik_target(high, q_seed=q, duration=1.7)
        self._idle(60)

        ee_position = self._ee_position()
        if task_id == "mw_peg_insertion_side":
            observed = self._site_position("peg_head_site")
        else:
            observed = self._body_position("workpiece")
        goal = self._reach_parameter(task_parameters)
        destination = goal - (observed - ee_position)
        route = destination.copy()
        route[2] = max(0.62, float(destination[2]) + 0.10)
        current_high = ee_position.copy()
        current_high[2] = route[2]
        for target in (current_high, route, destination):
            q = self._move_ik_target(target, q_seed=q, duration=1.6)

        if task_id == "mw_peg_insertion_side":
            insertion = self._ee_position() + np.asarray((0.0, -0.02, 0.0))
            self._move_ik_target(insertion, q_seed=q, duration=1.2)
        else:
            self._set_gripper(GRIPPER_OPEN)
            self._idle(150)
        self._idle(120)

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
            mujoco.mj_jacSite(
                self.model,
                self.data,
                position_jacobian,
                rotation_jacobian,
                self._ee_site,
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
        self._begin_task()
        _, parameters = _request(request)
        self._set_gripper(GRIPPER_OPEN)
        self._step_to(self._reach_parameter(parameters))

    def contact_task(self, request: Any) -> None:
        self._begin_task()
        task_id, parameters = _request(request)
        contact = _vector(parameters["contact_position"], name="contact_position")
        if task_id not in {"mw_push_to_goal", "mw_push_wall", "mw_sweep_into_goal"}:
            raise ValueError(f"unsupported contact task {task_id!r}")
        push_contact = contact
        push_target = self._tool_target(parameters)
        if task_id == "mw_push_to_goal":
            push_contact = contact + np.asarray((0.02, 0.0, 0.0))
            push_target = np.asarray(
                (push_contact[0], push_target[1] + 0.17, push_contact[2]),
                dtype=float,
            )
        self._set_gripper(GRIPPER_CLOSED)
        self._idle(20)
        self._safe_horizontal_push(push_contact, push_target)

    def object_task(self, request: Any) -> None:
        self._begin_task()
        task_id, parameters = _request(request)
        if task_id not in {
            "mw_bin_picking",
            "mw_pick_out_of_hole",
            "mw_pick_place",
            "mw_pick_place_wall",
            "mw_peg_insertion_side",
        }:
            raise ValueError(f"unsupported object task {task_id!r}")
        self._side_grasp_place(task_id, parameters)

    def fixture_task(self, request: Any) -> None:
        self._begin_task()
        task_id, parameters = _request(request)
        if task_id == "mw_button_press_topdown":
            contact = _vector(parameters["contact_position"], name="contact_position")
            lift = self._ee_position()
            lift[2] = max(0.62, float(lift[2]) + 0.12)
            self._set_gripper(GRIPPER_CLOSED)
            self._idle(20)
            self._safe_joint_ik_path(
                (
                    lift,
                    contact + np.asarray((0.0, 0.0, 0.10)),
                    contact,
                    np.asarray((contact[0], contact[1], 0.44), dtype=float),
                )
            )
            self._idle(40)
            return
        if task_id in {"mw_door_open", "mw_door_close"}:
            contact = _vector(parameters["contact_position"], name="contact_position")
            lift = self._ee_position()
            lift[2] = max(0.62, float(lift[2]) + 0.12)
            self._set_gripper(GRIPPER_CLOSED)
            self._idle(20)
            self._safe_joint_ik_path(
                (
                    lift,
                    contact + np.asarray((0.0, 0.0, 0.10)),
                    contact,
                    _vector(parameters["route_position"], name="route_position"),
                    self._tool_target(parameters),
                )
            )
            self._idle(40)
            return
        if task_id in {"mw_drawer_open", "mw_drawer_close", "mw_handle_pull"}:
            contact = _vector(parameters["contact_position"], name="contact_position")
            approach = _vector(
                parameters.get("approach_position", contact + np.asarray((0.0, -0.07, 0.0))),
                name="approach_position",
            )
            wrist_roll = math.pi / 2.0
            self._set_gripper(GRIPPER_OPEN)
            self._idle(30)
            self._step_to(approach, residual_tolerance=0.08, wrist_roll=wrist_roll)
            self._step_to(contact, residual_tolerance=0.08, wrist_roll=wrist_roll)
            contact_rotation = self._ee_rotation()
            self._set_gripper(
                255.0 if task_id == "mw_handle_pull" else 225.17818452186286
            )
            self._idle(80)
            tool_target = self._tool_target(parameters)
            if task_id == "mw_handle_pull":
                self._pose_steps(
                    tool_target,
                    contact_rotation,
                    steps=1200,
                    gain=1.2,
                    max_joint_delta=0.03,
                )
                self._idle(40)
                return
            if task_id == "mw_drawer_open":
                self._step_to(
                    tool_target + np.asarray((0.0, -0.065, 0.0)),
                    steps=1200,
                    residual_tolerance=0.20,
                    wrist_roll=wrist_roll,
                    gain=0.35,
                    max_joint_delta=0.02,
                )
                self._idle(40)
                return
            self._pose_steps(
                tool_target,
                contact_rotation,
                steps=1000,
                gain=0.8,
                max_joint_delta=0.02,
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
        contact_rotation = self._ee_rotation()
        if "route_position" in parameters:
            route_tolerance = {
                "mw_door_open": 0.20,
                "mw_door_close": 0.10,
            }.get(task_id, 0.10)
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
                residual_tolerance=0.55,
                gain=0.3,
                max_joint_delta=0.02,
            )
            self._idle(60)
            return
        tool_target = self._tool_target(parameters)
        if task_id == "mw_button_press":
            tool_target = tool_target + np.asarray((0.0, 0.08, 0.0))
            self._pose_steps(
                tool_target,
                contact_rotation,
                steps=1200,
                gain=1.2,
                max_joint_delta=0.03,
            )
            self._idle(60)
            return
        self._step_to(
            tool_target,
            steps=1000,
            residual_tolerance=0.15 if task_id == "mw_door_close" else 0.10,
            gain=0.3,
            max_joint_delta=0.02,
        )
        self._idle(60)

    def rotation_task(self, request: Any) -> None:
        self._begin_task()
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
            residual_tolerance=0.25,
            gain=0.3,
            max_joint_delta=0.02,
        )
        self._idle(90)


def build(*, model: Any, data: Any) -> ReferenceKinovaGen3Robotiq2F85Driver:
    return ReferenceKinovaGen3Robotiq2F85Driver(model=model, data=data)
