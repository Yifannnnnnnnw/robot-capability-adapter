"""SO-101 calibration controller for the private Demo3 Harness.

This helper is calibration-only. It never constructs or resets MuJoCo state,
and it never writes qpos, qvel, time, or derived pose arrays. It reads the
Framework-owned model/data, writes actuator targets through data.ctrl, and
advances only with mujoco.mj_step.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

import numpy as np
import mujoco


ARM_JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
)
GRIPPER_OPEN = 1.74533
GRIPPER_CLOSED = -0.17453


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


class ReferenceSO101Driver:
    """Small actuator-only controller used to calibrate package fixtures.

    The method names are calibration design names, not a capability or effect
    catalog. A later TGCD design may wrap these primitives under its own
    explicitly generated method names while retaining the same task envelope.
    """

    def __init__(self, *, model: Any, data: Any) -> None:
        import mujoco

        self.model = model
        self.data = data
        self._mujoco = mujoco
        self._ee_site = self._id(mujoco.mjtObj.mjOBJ_SITE, "gripperframe")
        self._joint_ids = [
            self._id(mujoco.mjtObj.mjOBJ_JOINT, name) for name in ARM_JOINTS
        ]
        self._qpos_addresses = [int(model.jnt_qposadr[index]) for index in self._joint_ids]
        self._qvel_addresses = [int(model.jnt_dofadr[index]) for index in self._joint_ids]
        self._actuator_ids = [
            self._id(mujoco.mjtObj.mjOBJ_ACTUATOR, name) for name in ARM_JOINTS
        ]
        self._gripper_id = self._id(mujoco.mjtObj.mjOBJ_ACTUATOR, "gripper")
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
        identifier = int(self._mujoco.mj_name2id(self.model, object_type, name))
        if identifier < 0:
            raise ValueError(f"canonical model is missing {name!r}")
        return identifier

    def _ee_position(self) -> np.ndarray:
        return np.asarray(self.data.site_xpos[self._ee_site], dtype=float).copy()

    def _current_q(self) -> np.ndarray:
        return np.asarray(
            [self.data.qpos[address] for address in self._qpos_addresses], dtype=float
        )

    def _set_arm_target(self, target: np.ndarray) -> None:
        self._arm_target = np.asarray(target, dtype=float).copy()
        for actuator_id, value in zip(self._actuator_ids, target):
            self.data.ctrl[actuator_id] = float(value)

    def _set_gripper(self, value: float) -> None:
        self.data.ctrl[self._gripper_id] = float(value)

    def _hold_arm(self) -> None:
        for actuator_id, value in zip(self._actuator_ids, self._arm_target):
            self.data.ctrl[actuator_id] = float(value)

    def _step_to(
        self,
        target: np.ndarray,
        *,
        steps: int = 900,
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
            self._mujoco.mj_jacSite(
                self.model, self.data, jacobian, None, self._ee_site
            )
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

    def _reach_parameter(
        self, task_parameters: Mapping[str, Any], key: str = "target_position"
    ) -> np.ndarray:
        return _vector(task_parameters[key], name=key)

    def _tool_target(self, task_parameters: Mapping[str, Any]) -> np.ndarray:
        return _vector(
            task_parameters.get("tool_target_position", task_parameters["target_position"]),
            name="tool_target_position",
        )

    def reach_task(self, *, request: Any) -> None:
        _, parameters = _request(request)
        self._set_gripper(GRIPPER_OPEN)
        self._step_to(self._reach_parameter(parameters))

    def contact_task(self, *, request: Any) -> None:
        task_id, parameters = _request(request)
        contact = _vector(parameters["contact_position"], name="contact_position")
        approach = _vector(
            parameters.get(
                "approach_position", contact + np.asarray((0.0, 0.0, 0.08))
            ),
            name="approach_position",
        )
        self._set_gripper(GRIPPER_CLOSED)
        self._idle(20)
        self._step_to(approach, residual_tolerance=0.08)
        self._step_to(
            contact,
            residual_tolerance=0.08,
        )
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

    def object_task(self, *, request: Any) -> None:
        task_id, parameters = _request(request)
        start = _vector(parameters["start_position"], name="start_position")
        target = self._reach_parameter(parameters)
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
        grasp_gripper = float(parameters.get("grasp_gripper", 0.15))
        if not math.isfinite(grasp_gripper):
            raise ValueError("grasp_gripper must be finite")
        pregrasp = grasp + np.asarray((0.0, 0.0, approach_height))
        lift = grasp + np.asarray((0.0, 0.0, approach_height))
        self._set_gripper(GRIPPER_OPEN)
        self._idle(20)
        self._step_to(
            pregrasp, residual_tolerance=0.08, wrist_roll=grasp_wrist_roll
        )
        self._step_to(
            grasp, residual_tolerance=0.08, wrist_roll=grasp_wrist_roll
        )
        self._set_gripper(grasp_gripper)
        self._idle(90)
        self._step_to(
            lift,
            steps=700,
            residual_tolerance=0.12,
            wrist_roll=grasp_wrist_roll,
        )
        if "route_position" in parameters:
            self._step_to(
                _vector(parameters["route_position"], name="route_position"),
                steps=700,
                residual_tolerance=0.25,
                wrist_roll=grasp_wrist_roll,
            )
        self._step_to(
            release,
            steps=700,
            residual_tolerance=0.12,
            wrist_roll=grasp_wrist_roll,
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

    def fixture_task(self, *, request: Any) -> None:
        task_id, parameters = _request(request)
        if task_id in {"mw_drawer_open", "mw_drawer_close", "mw_handle_pull"}:
            contact = _vector(parameters["contact_position"], name="contact_position")
            approach = _vector(
                parameters.get(
                    "approach_position", contact + np.asarray((0.0, -0.07, 0.0))
                ),
                name="approach_position",
            )
            wrist_roll = math.pi / 2.0
            self._set_gripper(0.5)
            self._idle(30)
            self._step_to(
                approach, residual_tolerance=0.08, wrist_roll=wrist_roll
            )
            self._step_to(
                contact, residual_tolerance=0.08, wrist_roll=wrist_roll
            )
            self._set_gripper(0.05)
            self._idle(80)
            self._step_to(
                self._tool_target(parameters),
                steps=1000,
                residual_tolerance=0.10,
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
            residual_tolerance=(
                0.07 if task_id in {"mw_door_open", "mw_door_close"} else 0.08
            ),
        )
        self._step_to(
            contact,
            residual_tolerance=(
                0.06 if task_id in {"mw_door_open", "mw_door_close"} else 0.08
            ),
        )
        if "route_position" in parameters:
            self._step_to(
                _vector(parameters["route_position"], name="route_position"),
                steps=700,
                residual_tolerance=(
                    0.07
                    if task_id in {"mw_door_open", "mw_door_close"}
                    else 0.10
                ),
                gain=0.4,
                max_joint_delta=0.025,
            )
        if task_id == "mw_door_open":
            self._idle(60)
            return
        self._step_to(
            self._tool_target(parameters),
            steps=1000,
            residual_tolerance=(
                0.08 if task_id in {"mw_door_open", "mw_door_close"} else 0.10
            ),
            gain=0.3,
            max_joint_delta=0.02,
        )
        self._idle(60)

    def rotation_task(self, *, request: Any) -> None:
        _, parameters = _request(request)
        contact = _vector(parameters["contact_position"], name="contact_position")
        approach = _vector(
            parameters.get(
                "approach_position", contact + np.asarray((0.0, 0.0, 0.08))
            ),
            name="approach_position",
        )
        self._set_gripper(GRIPPER_CLOSED)
        self._idle(20)
        self._step_to(approach, residual_tolerance=0.10)
        self._step_to(
            contact,
            residual_tolerance=0.10,
        )
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


def build(*, model: Any, data: Any) -> ReferenceSO101Driver:
    return ReferenceSO101Driver(model=model, data=data)
