"""Small geometry admission gate for the SO-101 wall and lever fixtures.

The gate is deliberately task-specific.  It checks the two geometry failures
that made the old SO-101 package look ready while its reference path was not:
the wall fixture had an easy/incorrect layout, and the lever waypoints sat too
far outboard.  It is an analysis-only check; it never owns a MuJoCo reset or a
trial and never replaces the trusted Harness verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping

import mujoco
import numpy as np

from autoadapter2.trusted_skeletons.arm_serial_dls import (
    ArmSerialDLSSkeleton,
    ArmSpec,
    IKUnreachableError,
)


SO101_ARM_JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
)
MIN_WAYPOINT_CHAIN_MARGIN_M = 0.030
MAX_IK_RESIDUAL_M = 0.005
MIN_JOINT_LIMIT_MARGIN_RAD = math.radians(5.0)
MIN_ROUTE_CLEARANCE_M = 0.010


@dataclass(frozen=True)
class GeometryGateResult:
    """Named check outcomes and the tightest measured margins."""

    checks: Mapping[str, bool]
    margins: Mapping[str, float]
    failures: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return all(self.checks.values())


def _root(value: Any) -> Path:
    root = value if isinstance(value, (str, Path)) else getattr(value, "root", value)
    path = Path(root).resolve()
    if not path.is_dir():
        raise ValueError(f"package root is not a directory: {path}")
    return path


def _read_instances(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "tasks" / "private" / "instances.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    return {item["task_id"]: item for item in document["instances"]}


def _read_tasks(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "tasks" / "catalog.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    return {item["task_id"]: item for item in document["tasks"]}


def _name_id(model: Any, object_type: Any, name: str) -> int:
    identifier = int(mujoco.mj_name2id(model, object_type, name))
    if identifier < 0:
        raise ValueError(f"canonical model is missing {name!r}")
    return identifier


def _apply_analysis_reset(model: Any, data: Any, instance: Mapping[str, Any]) -> None:
    """Apply the declared reset to an analysis copy of Framework state."""

    mujoco.mj_resetData(model, data)
    for name, value in instance.get("reset", {}).get("joint_positions", {}).items():
        joint_id = _name_id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        data.qpos[int(model.jnt_qposadr[joint_id])] = float(value)
    for name, value in instance.get("reset", {}).get("actuator_controls", {}).items():
        actuator_id = _name_id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
        data.ctrl[actuator_id] = float(value)
    mujoco.mj_forward(model, data)


def _waypoints(task_id: str, parameters: Mapping[str, Any]) -> tuple[np.ndarray, ...]:
    if task_id == "mw_pick_place_wall":
        grasp = np.asarray(parameters["grasp_position"], dtype=float)
        return (
            grasp + np.asarray((0.0, 0.0, 0.095)),
            grasp,
            np.asarray(parameters["route_position"], dtype=float),
            np.asarray(parameters["release_position"], dtype=float),
            np.asarray(parameters["tool_target_position"], dtype=float),
        )
    if task_id == "mw_lever_pull":
        contact = np.asarray(parameters["contact_position"], dtype=float)
        return (
            contact + np.asarray((0.0, 0.0, 0.08)),
            contact,
            np.asarray(parameters["route_position"], dtype=float),
            np.asarray(parameters["tool_target_position"], dtype=float),
        )
    raise ValueError(f"unsupported SO-101 geometry task: {task_id!r}")


def _chain_upper_bound(model: Any, site_id: int) -> float:
    body_id = int(model.site_bodyid[site_id])
    upper_bound = float(np.linalg.norm(model.site_pos[site_id]))
    while body_id > 0:
        upper_bound += float(np.linalg.norm(model.body_pos[body_id]))
        body_id = int(model.body_parentid[body_id])
    return upper_bound


def _arm_spec(model: Any) -> ArmSpec:
    limits: dict[str, tuple[float, float]] = {}
    for name in SO101_ARM_JOINTS:
        joint_id = _name_id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        limits[name] = (
            float(model.jnt_range[joint_id, 0]),
            float(model.jnt_range[joint_id, 1]),
        )
    return ArmSpec(
        ee_site_name="gripperframe",
        arm_joint_names=SO101_ARM_JOINTS,
        arm_actuator_names=SO101_ARM_JOINTS,
        joint_limits=limits,
        ik_damping=0.02,
        ik_max_iter=500,
        ik_tolerance=MAX_IK_RESIDUAL_M,
        ik_step_clamp=0.10,
    )


def _ik_checks(model: Any, data: Any, waypoints: tuple[np.ndarray, ...]) -> tuple[bool, float, float]:
    skeleton = ArmSerialDLSSkeleton(model=model, data=data, spec=_arm_spec(model))
    reset_q = np.asarray(skeleton.get_joint_positions(), dtype=float)
    roll_index = SO101_ARM_JOINTS.index("wrist_roll")
    lower = np.asarray([skeleton.spec.joint_limits[name][0] for name in SO101_ARM_JOINTS])
    upper = np.asarray([skeleton.spec.joint_limits[name][1] for name in SO101_ARM_JOINTS])
    worst_residual = 0.0
    worst_limit_margin = math.inf
    passed = True
    for waypoint in waypoints:
        for initial_roll in (-math.pi / 2.0, 0.0, math.pi / 2.0):
            q_init = reset_q.copy()
            q_init[roll_index] = initial_roll
            try:
                solved = skeleton.ik(waypoint, q_init=q_init)
            except IKUnreachableError as exc:
                solved = np.asarray(exc.q_final, dtype=float)
                passed = False
            residual = float(np.linalg.norm(skeleton.fk(solved)["pos"] - waypoint))
            worst_residual = max(worst_residual, residual)
            if residual > MAX_IK_RESIDUAL_M + 1.0e-9:
                passed = False
            limit_margin = float(np.min(np.minimum(solved - lower, upper - solved)))
            worst_limit_margin = min(worst_limit_margin, limit_margin)
            if limit_margin < MIN_JOINT_LIMIT_MARGIN_RAD - 1.0e-9:
                passed = False
    return passed, worst_residual, worst_limit_margin


def _point_aabb_distance(point: np.ndarray, center: np.ndarray, half: np.ndarray) -> float:
    outside = np.maximum(np.abs(point - center) - half, 0.0)
    return float(np.linalg.norm(outside))


def _geom_aabb(model: Any, data: Any, geom_id: int) -> tuple[np.ndarray, np.ndarray]:
    """Return a conservative world AABB for an axis-aligned task geom."""

    center = np.asarray(data.geom_xpos[geom_id], dtype=float)
    geom_type = int(model.geom_type[geom_id])
    size = np.asarray(model.geom_size[geom_id], dtype=float)
    if geom_type == int(mujoco.mjtGeom.mjGEOM_BOX):
        rotation = np.asarray(data.geom_xmat[geom_id], dtype=float).reshape(3, 3)
        half = np.abs(rotation) @ size
    elif geom_type == int(mujoco.mjtGeom.mjGEOM_CYLINDER):
        half = np.asarray((size[0], size[0], size[1]), dtype=float)
    elif geom_type == int(mujoco.mjtGeom.mjGEOM_SPHERE):
        half = np.repeat(size[0], 3)
    else:
        # The two scoped fixtures only use boxes/cylinders for route checks.
        half = np.repeat(float(np.max(size)), 3)
    return center, half


def _static_integrity(model: Any, data: Any) -> tuple[bool, float]:
    movable_body: list[bool] = []
    for body_id in range(int(model.nbody)):
        current = body_id
        movable = False
        while current > 0:
            if int(model.body_dofnum[current]) > 0:
                movable = True
                break
            current = int(model.body_parentid[current])
        movable_body.append(movable)
    static_geoms = [
        geom_id
        for geom_id in range(int(model.ngeom))
        if not movable_body[int(model.geom_bodyid[geom_id])]
        and int(model.geom_contype[geom_id]) != 0
        and int(model.geom_conaffinity[geom_id]) != 0
    ]
    minimum_distance = math.inf
    for index, first in enumerate(static_geoms):
        for second in static_geoms[index + 1 :]:
            distance = float(mujoco.mj_geomDistance(model, data, first, second, 1.0, None))
            minimum_distance = min(minimum_distance, distance)
    return minimum_distance >= -1.0e-9, minimum_distance


def _wall_checks(model: Any, data: Any, parameters: Mapping[str, Any]) -> tuple[dict[str, bool], dict[str, float]]:
    wall_id = _name_id(model, mujoco.mjtObj.mjOBJ_GEOM, "wall")
    surface_id = _name_id(model, mujoco.mjtObj.mjOBJ_GEOM, "work_surface")
    pedestal_id = _name_id(model, mujoco.mjtObj.mjOBJ_GEOM, "wall_pick_goal_pedestal")
    workpiece_id = _name_id(model, mujoco.mjtObj.mjOBJ_GEOM, "workpiece_geom")
    wall_center, wall_half = _geom_aabb(model, data, wall_id)
    surface_center, surface_half = _geom_aabb(model, data, surface_id)
    pedestal_center, pedestal_half = _geom_aabb(model, data, pedestal_id)
    workpiece_radius = float(model.geom_size[workpiece_id, 0])
    wall_route = np.asarray(parameters["route_position"], dtype=float)
    wall_clearance = _point_aabb_distance(wall_route, wall_center, wall_half)
    object_route_clearance = wall_clearance - workpiece_radius
    gripper_route_clearance = wall_clearance - 0.030
    left_gap = float((wall_center[1] - wall_half[1]) - (surface_center[1] - surface_half[1]))
    right_gap = float((surface_center[1] + surface_half[1]) - (wall_center[1] + wall_half[1]))
    side_gap = min(left_gap, right_gap)
    start = np.asarray(parameters["start_position"], dtype=float)
    goal = np.asarray(parameters["target_position"], dtype=float)
    target_bottom = float(goal[2] - model.geom_size[workpiece_id, 1])
    pedestal_top = float(pedestal_center[2] + pedestal_half[2])
    checks = {
        "wall_between_start_and_goal": bool(start[0] < wall_center[0] < goal[0]),
        "wall_side_gap_below_object_diameter": bool(side_gap < 2.0 * workpiece_radius),
        "wall_object_route_clearance": object_route_clearance >= MIN_ROUTE_CLEARANCE_M,
        "wall_gripper_route_clearance": gripper_route_clearance >= MIN_ROUTE_CLEARANCE_M,
        "wall_pedestal_no_penetration": target_bottom >= pedestal_top - 1.0e-9,
        "wall_pedestal_tangent": abs(target_bottom - pedestal_top) <= 1.0e-9,
    }
    margins = {
        "wall_object_route_clearance_m": object_route_clearance,
        "wall_gripper_route_clearance_m": gripper_route_clearance,
        "wall_side_gap_m": side_gap,
        "wall_pedestal_tangent_error_m": abs(target_bottom - pedestal_top),
    }
    return checks, margins


def _lever_checks(model: Any, data: Any, parameters: Mapping[str, Any]) -> tuple[dict[str, bool], dict[str, float]]:
    route = np.asarray(parameters["route_position"], dtype=float)
    minimum_clearance = math.inf
    for name in ("work_surface", "lever_base", "lever_base_right"):
        geom_id = _name_id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
        center, half = _geom_aabb(model, data, geom_id)
        minimum_clearance = min(minimum_clearance, _point_aabb_distance(route, center, half) - 0.030)
    return {
        "lever_gripper_route_clearance": minimum_clearance >= MIN_ROUTE_CLEARANCE_M,
    }, {"lever_gripper_route_clearance_m": minimum_clearance}


def validate_so101_geometry(package: Any) -> GeometryGateResult:
    """Run the bounded wall/lever geometry gate on a package or package path."""

    root = _root(package)
    instances = _read_instances(root)
    tasks = _read_tasks(root)
    checks: dict[str, bool] = {}
    margins: dict[str, float] = {}
    failures: list[str] = []
    for task_id in ("mw_pick_place_wall", "mw_lever_pull"):
        instance = instances[task_id]
        parameters = instance["public_arguments"]["request"]["task_parameters"]
        model = mujoco.MjModel.from_xml_path(str((root / instance["scene_entrypoint"]).resolve()))
        data = mujoco.MjData(model)
        _apply_analysis_reset(model, data, instance)

        site_id = _name_id(model, mujoco.mjtObj.mjOBJ_SITE, "gripperframe")
        base_id = _name_id(model, mujoco.mjtObj.mjOBJ_BODY, "base")
        chain_bound = _chain_upper_bound(model, site_id)
        base_position = np.asarray(data.xpos[base_id], dtype=float)
        waypoint_margin = min(
            chain_bound - float(np.linalg.norm(waypoint - base_position))
            for waypoint in _waypoints(task_id, parameters)
        ) - MIN_WAYPOINT_CHAIN_MARGIN_M
        checks[f"{task_id}.strict_chain_margin"] = waypoint_margin >= -1.0e-9
        margins[f"{task_id}.strict_chain_margin_m"] = waypoint_margin

        ik_passed, worst_residual, worst_limit_margin = _ik_checks(
            model, data, _waypoints(task_id, parameters)
        )
        checks[f"{task_id}.ik_residual_and_limits"] = ik_passed
        margins[f"{task_id}.worst_ik_residual_m"] = worst_residual
        margins[f"{task_id}.worst_joint_limit_margin_deg"] = math.degrees(worst_limit_margin)

        static_passed, static_distance = _static_integrity(model, data)
        checks[f"{task_id}.static_no_penetration"] = static_passed
        margins[f"{task_id}.minimum_static_distance_m"] = static_distance

        if task_id == "mw_pick_place_wall":
            local_checks, local_margins = _wall_checks(model, data, parameters)
            reset_value = np.asarray(data.xpos[_name_id(model, mujoco.mjtObj.mjOBJ_BODY, "workpiece")])
            target = np.asarray(parameters["target_position"], dtype=float)
            reset_error = float(np.linalg.norm(reset_value - target))
        else:
            local_checks, local_margins = _lever_checks(model, data, parameters)
            joint_id = _name_id(model, mujoco.mjtObj.mjOBJ_JOINT, "lever_hinge")
            reset_value = float(data.qpos[int(model.jnt_qposadr[joint_id])])
            target = float(parameters["target_angle"])
            reset_error = abs(reset_value - target)
        checks.update({f"{task_id}.{name}": value for name, value in local_checks.items()})
        margins.update(local_margins)
        criterion = tasks[task_id]["scoring"][0]
        threshold = float(criterion["threshold"])
        checks[f"{task_id}.reset_not_success"] = reset_error > threshold
        margins[f"{task_id}.reset_criterion_margin"] = reset_error - threshold

    failures.extend(name for name, passed in checks.items() if not passed)
    return GeometryGateResult(checks=checks, margins=margins, failures=tuple(failures))


def assert_so101_geometry(package: Any) -> GeometryGateResult:
    """Raise a concise error when the scoped SO-101 gate does not pass."""

    result = validate_so101_geometry(package)
    if not result.passed:
        raise ValueError("SO-101 geometry gate failed: " + ", ".join(result.failures))
    return result


__all__ = [
    "GeometryGateResult",
    "MAX_IK_RESIDUAL_M",
    "MIN_JOINT_LIMIT_MARGIN_RAD",
    "MIN_ROUTE_CLEARANCE_M",
    "MIN_WAYPOINT_CHAIN_MARGIN_M",
    "assert_so101_geometry",
    "validate_so101_geometry",
]
