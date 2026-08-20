"""Partial Stretch 2 calibration controller for real Direct-MuJoCo fixtures.

The controller owns no model construction or reset. It writes only actuator
controls on the Framework session and advances that session with MuJoCo steps.
Unsupported task families fail explicitly until their physical routes are
calibrated.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import mujoco
import numpy as np

from autoadapter2.trusted_skeletons.stretch_mobile_manipulation import (
    StretchMobileManipulationSkeleton,
    StretchMobileManipulationSpec,
)


SPEC = StretchMobileManipulationSpec(
    base_body_name="base_link",
    tool_body_name="link_gripper_slider",
    forward_actuator_name="forward",
    turn_actuator_name="turn",
    lift_actuator_name="lift",
    arm_actuator_name="arm_extend",
    wrist_actuator_name="wrist_yaw",
    gripper_actuator_name="grip",
    lift_joint_name="joint_lift",
    arm_joint_names=(
        "joint_arm_l3",
        "joint_arm_l2",
        "joint_arm_l1",
        "joint_arm_l0",
    ),
    wrist_joint_name="joint_wrist_yaw",
    gripper_joint_name="joint_gripper_slide",
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


class ReferenceStretch2Driver:
    """Actuator-only calibration paths verified on the canonical Stretch model."""

    def __init__(self, *, model: Any, data: Any) -> None:
        self.model = model
        self.data = data
        self._control = StretchMobileManipulationSkeleton(
            model=model,
            data=data,
            spec=SPEC,
        )
        self._tool_id = self._id(
            mujoco.mjtObj.mjOBJ_BODY, SPEC.tool_body_name
        )
        self._grip_id = self._id(
            mujoco.mjtObj.mjOBJ_ACTUATOR, SPEC.gripper_actuator_name
        )
        self._finger_geoms = self._resolve_finger_geoms()

    def _id(self, object_type: Any, name: str) -> int:
        identifier = int(mujoco.mj_name2id(self.model, object_type, name))
        if identifier < 0:
            raise ValueError(f"canonical Stretch model is missing {name!r}")
        return identifier

    def _resolve_finger_geoms(self) -> tuple[int, int]:
        result: list[int] = []
        for body_name in (
            "link_gripper_finger_left",
            "link_gripper_finger_right",
        ):
            body_id = self._id(mujoco.mjtObj.mjOBJ_BODY, body_name)
            start = int(self.model.body_geomadr[body_id])
            stop = start + int(self.model.body_geomnum[body_id])
            boxes = [
                geom_id
                for geom_id in range(start, stop)
                if int(self.model.geom_contype[geom_id]) != 0
                and int(self.model.geom_type[geom_id])
                == int(mujoco.mjtGeom.mjGEOM_BOX)
            ]
            if len(boxes) != 1:
                raise ValueError(
                    f"canonical Stretch finger {body_name!r} has no unique contact box"
                )
            result.append(boxes[0])
        return result[0], result[1]

    def _tool_position(self) -> np.ndarray:
        return np.asarray(self.data.xpos[self._tool_id], dtype=float).copy()

    def _body_position(self, name: str) -> np.ndarray:
        body_id = self._id(mujoco.mjtObj.mjOBJ_BODY, name)
        return np.asarray(self.data.xpos[body_id], dtype=float).copy()

    def _site_position(self, name: str) -> np.ndarray:
        site_id = self._id(mujoco.mjtObj.mjOBJ_SITE, name)
        return np.asarray(self.data.site_xpos[site_id], dtype=float).copy()

    def _contact_position(
        self, selector: Literal["left", "right", "midpoint"]
    ) -> np.ndarray:
        positions = np.asarray(
            self.data.geom_xpos[list(self._finger_geoms)], dtype=float
        )
        if selector == "left":
            return positions[0].copy()
        if selector == "right":
            return positions[1].copy()
        return np.mean(positions, axis=0)

    def _idle(self, steps: int) -> None:
        for _ in range(int(steps)):
            mujoco.mj_step(self.model, self.data)

    def _pressure_gripper(self, target: float, *, steps: int) -> None:
        self.data.ctrl[self._grip_id] = float(target)
        self._idle(steps)

    def _move_contact(
        self,
        target: np.ndarray,
        *,
        selector: Literal["left", "right", "midpoint"],
        accepted_error_m: float,
        attempts: int = 10,
    ) -> None:
        target = _vector(target, name="contact_target")
        for _ in range(attempts):
            tool_target = (
                self._tool_position()
                + target
                - self._contact_position(selector)
            )
            try:
                self._control.move_tool_to_position(
                    tool_target,
                    tolerance_m=0.035,
                )
            except RuntimeError:
                error = float(
                    np.linalg.norm(self._contact_position(selector) - target)
                )
                if error > accepted_error_m:
                    raise
                return
            if (
                float(np.linalg.norm(self._contact_position(selector) - target))
                < 0.025
            ):
                return
        residual = float(np.linalg.norm(self._contact_position(selector) - target))
        if residual > accepted_error_m:
            raise RuntimeError(
                f"Stretch contact point did not converge; residual={residual:.5f}"
            )

    def _fixture_error(
        self, site_name: str, parameters: Mapping[str, Any]
    ) -> float:
        target = _vector(parameters["target_position"], name="target_position")
        return float(np.linalg.norm(self._site_position(site_name) - target))

    def reach_task(self, request: Any) -> None:
        _, parameters = _request(request)
        target = _vector(parameters["target_position"], name="target_position")
        self._control.move_tool_to_position(target, tolerance_m=0.02)

    def contact_task(self, request: Any) -> None:
        task_id, parameters = _request(request)
        thresholds = {
            "mw_push_to_goal": 0.05,
            "mw_push_wall": 0.07,
            "mw_sweep_into_goal": 0.05,
        }
        threshold = thresholds.get(task_id)
        if threshold is None:
            raise ValueError(
                f"unsupported Stretch contact calibration task {task_id!r}"
            )

        target = _vector(parameters["target_position"], name="target_position")
        self._control.set_wrist_yaw(0.0)
        self._control.set_gripper(-0.005)
        # Center the angled finger boxes low enough to engage the workpiece.
        contact_height = float(self._body_position("workpiece")[2]) + 0.024

        current = _vector(
            parameters["contact_position"], name="contact_position"
        )
        current[2] = contact_height
        self._move_contact(
            current,
            selector="midpoint",
            accepted_error_m=0.10,
            attempts=5,
        )

        route_keys = (
            ("route_position", "tool_target_position")
            if task_id == "mw_push_wall"
            else ("tool_target_position",)
        )
        for key in route_keys:
            destination = _vector(parameters[key], name=key)
            destination[2] = contact_height
            waypoint_count = max(
                2,
                int(np.ceil(float(np.linalg.norm(destination - current)) / 0.01)),
            )
            for waypoint in np.linspace(
                current, destination, waypoint_count + 1
            )[1:]:
                self._move_contact(
                    waypoint,
                    selector="midpoint",
                    accepted_error_m=0.10,
                    attempts=3,
                )
                error = float(
                    np.linalg.norm(self._body_position("workpiece") - target)
                )
                if error <= 0.6 * threshold:
                    self._idle(200)
                    if (
                        float(
                            np.linalg.norm(
                                self._body_position("workpiece") - target
                            )
                        )
                        <= threshold
                    ):
                        return
            current = destination

        self._idle(300)
        error = float(np.linalg.norm(self._body_position("workpiece") - target))
        if error > threshold:
            raise RuntimeError(
                f"Stretch contact task did not converge; residual={error:.5f}"
            )

    def object_task(self, request: Any) -> None:
        task_id, parameters = _request(request)
        if task_id != "mw_pick_place":
            raise ValueError(f"unsupported Stretch object calibration task {task_id!r}")

        self._control.set_wrist_yaw(0.0)
        self._control.set_gripper(0.04)
        workpiece = self._body_position("workpiece")
        self._move_contact(
            workpiece,
            selector="midpoint",
            accepted_error_m=0.05,
            attempts=3,
        )
        self._pressure_gripper(-0.005, steps=500)

        self._control.move_tool_to_position(
            self._tool_position() + np.asarray((0.0, 0.0, 0.12)),
            tolerance_m=0.03,
        )
        self._control.move_tool_to_position(
            self._tool_position() + np.asarray((-0.16, 0.0, 0.0)),
            tolerance_m=0.03,
        )

        goal = _vector(parameters["target_position"], name="target_position")
        placement_delta = (
            goal
            + np.asarray((0.0, 0.0, 0.02))
            - self._body_position("workpiece")
        )
        try:
            self._control.move_tool_to_position(
                self._tool_position() + placement_delta,
                tolerance_m=0.03,
            )
        except RuntimeError:
            if float(np.linalg.norm(self._body_position("workpiece") - goal)) > 0.05:
                raise

        for target in (0.0, 0.01, 0.02, 0.03, 0.04):
            self._pressure_gripper(target, steps=100)
        self._idle(400)
        error = float(np.linalg.norm(self._body_position("workpiece") - goal))
        if error > 0.07:
            raise RuntimeError(f"Stretch pick-place did not converge; residual={error:.5f}")

    def _drawer(self, task_id: str, parameters: Mapping[str, Any]) -> None:
        site_name = "drawer_handle_site"
        joint_id = self._id(mujoco.mjtObj.mjOBJ_JOINT, "drawer_slide")
        open_axis = -np.asarray(self.data.xaxis[joint_id], dtype=float).copy()
        self._control.set_wrist_yaw(0.0)
        self._control.set_gripper(0.04)
        self._idle(100)
        self._move_contact(
            self._site_position(site_name),
            selector="midpoint",
            accepted_error_m=0.12,
        )

        threshold = 0.03 if task_id == "mw_drawer_open" else 0.065
        if task_id == "mw_drawer_open":
            self._pressure_gripper(-0.005, steps=500)
            direction = open_axis
        else:
            direction = -open_axis
        if self._fixture_error(site_name, parameters) > threshold:
            try:
                self._control.move_tool_to_position(
                    self._tool_position() + 0.09 * direction,
                    tolerance_m=0.03,
                )
            except RuntimeError:
                if self._fixture_error(site_name, parameters) > threshold:
                    raise
        self._idle(100)
        error = self._fixture_error(site_name, parameters)
        if error > threshold:
            raise RuntimeError(f"Stretch drawer did not converge; residual={error:.5f}")

    def _press_front_button(self, parameters: Mapping[str, Any]) -> None:
        site_name = "front_button_site"
        self._control.set_wrist_yaw(0.0)
        self._control.set_gripper(0.04)
        site = self._site_position(site_name)
        self._move_contact(
            site,
            selector="left",
            accepted_error_m=0.08,
        )
        if self._fixture_error(site_name, parameters) > 0.02:
            joint_id = self._id(mujoco.mjtObj.mjOBJ_JOINT, "front_button_slide")
            axis = np.asarray(self.data.xaxis[joint_id], dtype=float).copy()
            try:
                self._control.move_tool_to_position(
                    self._tool_position() + 0.07 * axis,
                    tolerance_m=0.03,
                )
            except RuntimeError:
                if self._fixture_error(site_name, parameters) > 0.02:
                    raise

    def _press_top_button(self, parameters: Mapping[str, Any]) -> None:
        site_name = "top_button_site"
        self._control.set_wrist_yaw(0.0)
        self._control.set_gripper(0.04)
        site = self._site_position(site_name)
        self._move_contact(
            site,
            selector="left",
            accepted_error_m=0.08,
        )
        if self._fixture_error(site_name, parameters) > 0.024:
            joint_id = self._id(mujoco.mjtObj.mjOBJ_JOINT, "top_button_slide")
            axis = np.asarray(self.data.xaxis[joint_id], dtype=float).copy()
            try:
                self._control.move_tool_to_position(
                    self._tool_position() - 0.07 * axis,
                    tolerance_m=0.03,
                )
            except RuntimeError:
                if self._fixture_error(site_name, parameters) > 0.024:
                    raise

    def _move_vertical_handle(
        self, task_id: str, parameters: Mapping[str, Any]
    ) -> None:
        site_name = "vertical_handle_site"
        joint_id = self._id(
            mujoco.mjtObj.mjOBJ_JOINT, "vertical_handle_slide"
        )
        axis = np.asarray(self.data.xaxis[joint_id], dtype=float).copy()
        self._control.set_wrist_yaw(0.0)
        self._control.set_gripper(0.04)
        site = self._site_position(site_name)
        if task_id == "mw_handle_press":
            self._move_contact(
                site + 0.06 * axis,
                selector="right",
                accepted_error_m=0.08,
            )
            self._move_contact(
                site,
                selector="right",
                accepted_error_m=0.08,
            )
            direction = -axis
            threshold = 0.02
        else:
            self._move_contact(
                site - 0.025 * axis,
                selector="right",
                accepted_error_m=0.08,
            )
            direction = axis
            threshold = 0.05
        if self._fixture_error(site_name, parameters) > threshold:
            try:
                self._control.move_tool_to_position(
                    self._tool_position() + 0.07 * direction,
                    tolerance_m=0.03,
                )
            except RuntimeError:
                if self._fixture_error(site_name, parameters) > threshold:
                    raise
        self._idle(100)
        error = self._fixture_error(site_name, parameters)
        if error > threshold:
            raise RuntimeError(f"Stretch handle did not converge; residual={error:.5f}")

    def fixture_task(self, request: Any) -> None:
        task_id, parameters = _request(request)
        if task_id in {"mw_drawer_open", "mw_drawer_close"}:
            self._drawer(task_id, parameters)
            return
        if task_id == "mw_button_press":
            self._press_front_button(parameters)
            return
        if task_id == "mw_button_press_topdown":
            self._press_top_button(parameters)
            return
        if task_id in {"mw_handle_press", "mw_handle_pull"}:
            self._move_vertical_handle(task_id, parameters)
            return
        raise ValueError(f"unsupported Stretch fixture calibration task {task_id!r}")

    def rotation_task(self, request: Any) -> None:
        task_id, _ = _request(request)
        raise ValueError(f"unsupported Stretch rotation calibration task {task_id!r}")


def build(*, model: Any, data: Any) -> ReferenceStretch2Driver:
    return ReferenceStretch2Driver(model=model, data=data)
