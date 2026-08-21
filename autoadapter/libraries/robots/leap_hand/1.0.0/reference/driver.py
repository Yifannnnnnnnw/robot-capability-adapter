"""Actuator-only trusted reference controller for the LEAP Hand package."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

import mujoco
import numpy as np

from autoadapter2.trusted_skeletons import (
    LeapCubeReorientationSkeleton,
    LeapCubeReorientationSpec,
)


JOINT_NAMES = (
    "if_mcp",
    "if_rot",
    "if_pip",
    "if_dip",
    "mf_mcp",
    "mf_rot",
    "mf_pip",
    "mf_dip",
    "rf_mcp",
    "rf_rot",
    "rf_pip",
    "rf_dip",
    "th_cmc",
    "th_axl",
    "th_mcp",
    "th_ipl",
)
ACTUATOR_NAMES = tuple(f"{name}_act" for name in JOINT_NAMES)
FINGERTIP_SITES = ("if_tip", "mf_tip", "rf_tip", "th_tip")


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


class ReferenceLeapHandDriver:
    """Small deterministic controller used only for trusted positive controls."""

    def __init__(self, *, model: Any, data: Any) -> None:
        self.model = model
        self.data = data
        self._joint_ids = np.asarray(
            [self._id(mujoco.mjtObj.mjOBJ_JOINT, name) for name in JOINT_NAMES],
            dtype=int,
        )
        self._qpos_addresses = np.asarray(
            [model.jnt_qposadr[index] for index in self._joint_ids], dtype=int
        )
        self._dof_addresses = np.asarray(
            [model.jnt_dofadr[index] for index in self._joint_ids], dtype=int
        )
        self._actuator_ids = np.asarray(
            [
                self._id(mujoco.mjtObj.mjOBJ_ACTUATOR, name)
                for name in ACTUATOR_NAMES
            ],
            dtype=int,
        )
        self._control_low = model.actuator_ctrlrange[self._actuator_ids, 0].copy()
        self._control_high = model.actuator_ctrlrange[self._actuator_ids, 1].copy()
        self._palm_id = self._id(mujoco.mjtObj.mjOBJ_BODY, "palm")

    def _id(self, object_type: Any, name: str) -> int:
        identifier = int(mujoco.mj_name2id(self.model, object_type, name))
        if identifier < 0:
            raise ValueError(f"canonical model is missing {name!r}")
        return identifier

    def _write_hand_control(self, values: Any) -> None:
        command = np.clip(
            np.asarray(values, dtype=float), self._control_low, self._control_high
        )
        if command.shape != (16,) or not np.isfinite(command).all():
            raise ValueError("hand command must contain 16 finite values")
        for actuator_id, value in zip(self._actuator_ids, command, strict=True):
            self.data.ctrl[actuator_id] = float(value)

    def _joint_positions(self) -> np.ndarray:
        return np.asarray(self.data.qpos[self._qpos_addresses], dtype=float).copy()

    def _step_joint_target(self, target: Any, steps: int) -> None:
        target_array = np.asarray(target, dtype=float)
        if target_array.shape != (16,) or not np.isfinite(target_array).all():
            raise ValueError("joint target must contain 16 finite values")
        for _ in range(int(steps)):
            self._write_hand_control(target_array)
            mujoco.mj_step(self.model, self.data)

    def _policy(self, goal_sensor_name: str, steps: int) -> None:
        policy = LeapCubeReorientationSkeleton(
            model=self.model,
            data=self.data,
            spec=LeapCubeReorientationSpec(
                joint_names=JOINT_NAMES,
                actuator_names=ACTUATOR_NAMES,
                palm_position_sensor_name="palm_position",
                cube_position_sensor_name="cube_position",
                cube_orientation_sensor_name="cube_orientation",
                goal_orientation_sensor_name=goal_sensor_name,
            ),
        )
        duration = int(steps) * float(self.model.opt.timestep)
        outcome = policy.track_orientation(duration)
        if int(outcome["physics_steps"]) != int(steps):
            raise RuntimeError("trusted policy did not execute the requested physics steps")

    def _reach_fingertips(self, targets: Any, steps: int) -> None:
        target_array = np.asarray(targets, dtype=float)
        if target_array.shape != (12,) or not np.isfinite(target_array).all():
            raise ValueError("fingertip target must contain 12 finite values")
        site_ids = [self._id(mujoco.mjtObj.mjOBJ_SITE, name) for name in FINGERTIP_SITES]
        for _ in range(int(steps)):
            palm_position = np.asarray(self.data.xpos[self._palm_id], dtype=float)
            palm_rotation = np.asarray(
                self.data.xmat[self._palm_id], dtype=float
            ).reshape(3, 3)
            world_targets = [
                palm_position + palm_rotation @ target_array[index : index + 3]
                for index in range(0, 12, 3)
            ]
            errors = []
            rows = []
            for site_id, world_target in zip(site_ids, world_targets, strict=True):
                errors.extend(world_target - self.data.site_xpos[site_id])
                jacobian = np.zeros((3, int(self.model.nv)), dtype=float)
                mujoco.mj_jacSite(
                    self.model, self.data, jacobian, None, site_id
                )
                rows.append(jacobian[:, self._dof_addresses])
            stacked = np.vstack(rows)
            error = np.asarray(errors, dtype=float)
            damping = 0.02
            system = stacked @ stacked.T + (damping * damping) * np.eye(12)
            delta = stacked.T @ np.linalg.solve(system, error)
            norm = float(np.linalg.norm(delta))
            if norm > 0.08:
                delta *= 0.08 / norm
            self._write_hand_control(self._joint_positions() + 0.7 * delta)
            mujoco.mj_step(self.model, self.data)

    def ec_task(self, *, request: Any) -> None:
        task_id, _ = _request(request)
        if task_id in {"ec_pinch", "ec_dynamic_tripod", "ec_palmar_slide"}:
            base = self._joint_positions()
            for step in range(1000):
                phase = 2.0 * math.pi * step / 500.0
                target = base.copy()
                target[[0, 2, 4, 6, 12, 14]] += 0.18 * math.sin(phase)
                self._write_hand_control(target)
                mujoco.mj_step(self.model, self.data)
            return
        self._policy("cube_goal_orientation", 1000)

    def reach_task(self, *, request: Any) -> None:
        _, parameters = _request(request)
        self._reach_fingertips(parameters["target_fingertip_positions_m"], 500)

    def block_task(self, *, request: Any) -> None:
        _request(request)
        self._policy("cube_goal_orientation", 300)

    def pose_task(self, *, request: Any) -> None:
        _, parameters = _request(request)
        self._step_joint_target(parameters["target_joint_positions_rad"], 2000)

    def fixture_task(self, *, request: Any) -> None:
        task_id, _ = _request(request)
        steps = 2000 if task_id == "robel_dclaw_turn_fixed" else 4000
        base = self._joint_positions()
        for step in range(steps):
            phase = 2.0 * math.pi * step / 500.0
            target = base.copy()
            target[[0, 4, 8]] += 0.45 * np.sin(
                phase + np.asarray((0.0, 2.0, 4.0))
            )
            self._write_hand_control(target)
            mujoco.mj_step(self.model, self.data)

    def hold_task(self, *, request: Any) -> None:
        _request(request)
        self._policy("cube_goal_orientation", 1500)

    def baoding_task(self, *, request: Any) -> None:
        _request(request)
        base = self._joint_positions()
        for step in range(4000):
            phase = 2.0 * math.pi * step / 1000.0
            target = base.copy()
            target[[0, 4, 8, 12]] += 0.35 * np.sin(
                phase + np.asarray((0.0, 1.6, 3.2, 4.8))
            )
            self._write_hand_control(target)
            mujoco.mj_step(self.model, self.data)


def build(*, model: Any, data: Any) -> ReferenceLeapHandDriver:
    return ReferenceLeapHandDriver(model=model, data=data)
