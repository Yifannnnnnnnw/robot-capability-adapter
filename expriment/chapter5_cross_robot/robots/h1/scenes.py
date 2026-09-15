"""Prepare the five central Unitree H1 task scenes.

The task contracts follow the maintained H1 public catalog.  The catalog
describes these numerical requirements as local proposed and uncalibrated;
this module preserves them as task inputs and checks.  Scene construction
uses the native ``h1.xml`` model and only appends the fixtures required by
the selected task.  It never imports or invokes a generated driver.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
from typing import Any

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[4]
H1_ASSETS = ROOT / "AA1/assets/mjcf/h1"
H1_SCENE = H1_ASSETS / "scene.xml"

TASKS = ("H1-T01", "H1-T02", "H1-T04", "H1-T13", "H1-T14")
H1_JOINTS = (
    "left_hip_yaw", "left_hip_roll", "left_hip_pitch", "left_knee", "left_ankle",
    "right_hip_yaw", "right_hip_roll", "right_hip_pitch", "right_knee", "right_ankle",
    "torso", "left_shoulder_pitch", "left_shoulder_roll", "left_shoulder_yaw",
    "left_elbow", "right_shoulder_pitch", "right_shoulder_roll", "right_shoulder_yaw",
    "right_elbow",
)
HORIZON_S = 30.0
FINAL_WINDOW_S = 2.0
FOREARM_LOCAL_POINT_M = (0.28, 0.0, -0.015)
LEFT_ELBOW_BODY = "left_elbow_link"
LEFT_FOOT_BODY = "left_ankle_link"
RIGHT_FOOT_BODY = "right_ankle_link"
PELVIS_BODY = "pelvis"
TORSO_BODY = "torso_link"
TABLE_TOP_Z_M = 0.80
TABLE_CENTER_M = (0.78, 0.0, 0.40)
TABLE_HALF_SIZE_M = (0.38, 0.65, 0.40)
CUBE_HALF_SIZE_M = (0.075, 0.075, 0.075)
CUBE_MASS_KG = 0.5
BUTTON_PANEL_CENTER_M = (0.48, 0.21352962, 0.95)
BUTTON_PANEL_HALF_SIZE_M = (0.02, 0.45, 0.45)
BUTTON_RADIUS_M = 0.06
BUTTON_SEPARATION_M = 0.25
BUTTON_Z_M = 0.95


def _fmt(values: tuple[float, ...] | list[float]) -> str:
    return " ".join(f"{float(value):.12g}" for value in values)


def _initial_state() -> dict[str, Any]:
    """Reset to the model's canonical standing keyframe."""

    # ``home`` changes the native pelvis free-joint position from qpos0, so
    # the shared initial-state applier requires the explicit free-body state.
    return {
        "robot": {"keyframe": "home"},
        "free_bodies": {
            PELVIS_BODY: {
                "position_m": [0.0, 0.0, 0.98],
                "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                "linear_velocity_m_s": [0.0, 0.0, 0.0],
                "angular_velocity_rad_s": [0.0, 0.0, 0.0],
            }
        },
        "settle_s": 0.0,
    }


def _base_bindings() -> dict[str, dict[str, Any]]:
    bindings = {
        "pelvis": {"kind": "body", "name": PELVIS_BODY},
        "torso": {"kind": "body", "name": TORSO_BODY},
        "left_foot": {"kind": "body", "name": LEFT_FOOT_BODY},
        "right_foot": {"kind": "body", "name": RIGHT_FOOT_BODY},
    }
    # Preserve every scalar hinge in the native 19-motor H1 model in the
    # independent trace. These are observation bindings only.
    bindings.update({f"joint_{name}": {"kind": "joint", "name": name} for name in H1_JOINTS})
    return bindings


def _add_body_geom(
    spec: Any,
    *,
    body_name: str,
    geom_name: str,
    position: tuple[float, float, float],
    geom_type: int,
    size: tuple[float, ...],
    collision: bool,
    mass_kg: float | None = None,
    friction: tuple[float, float, float] | None = None,
) -> None:
    body = spec.worldbody.add_body(name=body_name)
    body.pos = np.asarray(position, dtype=float)
    body.quat = np.asarray((1.0, 0.0, 0.0, 0.0), dtype=float)
    geom = body.add_geom(name=geom_name)
    geom.type = geom_type
    geom_size = np.zeros(3, dtype=float)
    geom_size[: len(size)] = size
    geom.size = geom_size
    if mass_kg is not None:
        geom.mass = float(mass_kg)
    if friction is not None:
        geom.friction = np.asarray(friction, dtype=float)
    if not collision:
        geom.contype = 0
        geom.conaffinity = 0
        geom.density = 0.0


def _add_fixed_box(
    spec: Any,
    *,
    body_name: str,
    geom_name: str,
    position: tuple[float, float, float],
    half_size: tuple[float, float, float],
    collision: bool,
) -> None:
    _add_body_geom(
        spec,
        body_name=body_name,
        geom_name=geom_name,
        position=position,
        geom_type=mujoco.mjtGeom.mjGEOM_BOX,
        size=half_size,
        collision=collision,
    )


def _add_fixed_sphere(
    spec: Any,
    *,
    body_name: str,
    geom_name: str,
    position: tuple[float, float, float],
    radius: float,
    collision: bool,
) -> None:
    _add_body_geom(
        spec,
        body_name=body_name,
        geom_name=geom_name,
        position=position,
        geom_type=mujoco.mjtGeom.mjGEOM_SPHERE,
        size=(radius,),
        collision=collision,
    )


def _add_free_box(
    spec: Any,
    *,
    body_name: str,
    geom_name: str,
    position: tuple[float, float, float],
    half_size: tuple[float, float, float],
    mass_kg: float,
) -> None:
    body = spec.worldbody.add_body(name=body_name)
    body.pos = np.asarray(position, dtype=float)
    body.quat = np.asarray((1.0, 0.0, 0.0, 0.0), dtype=float)
    body.add_freejoint(name=f"{body_name}_free")
    geom = body.add_geom(name=geom_name)
    geom.type = mujoco.mjtGeom.mjGEOM_BOX
    geom.size = np.asarray(half_size, dtype=float)
    geom.mass = float(mass_kg)
    geom.friction = np.asarray((1.0, 0.05, 0.01), dtype=float)


def _new_spec() -> Any:
    source = H1_SCENE.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"native H1 scene is unavailable: {source}")
    spec = mujoco.MjSpec.from_file(str(source))
    # h1.xml uses meshdir="assets".  The generated scene is written under an
    # experiment output directory, so make the existing native asset root
    # explicit without changing any robot body, joint, actuator, or mesh.
    spec.compiler.meshdir = str((H1_ASSETS / "assets").resolve())
    return spec


def _write_scene(spec: Any, path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite prepared scene: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(spec.to_xml(), encoding="utf-8")
    # Compile the exported file so preparation cannot return an in-memory-only
    # scene.  The task runner will compile the same path again for execution.
    mujoco.MjModel.from_xml_path(str(path))


def _task_case(
    *,
    task_id: str,
    scene_path: Path,
    description: str,
    parameters: dict[str, Any],
    success_spec: dict[str, Any],
    initial_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"h1_{task_id.lower()}_central",
        "task_id": task_id,
        "scene_path": str(scene_path.resolve()),
        "initial_state": initial_state or _initial_state(),
        "parameters": parameters,
        "task_description": description,
        "success_spec": success_spec,
    }


def _common_spec(task_id: str, bindings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "h1_catalog_task",
        "task_id": task_id,
        "bindings": bindings,
        "max_sim_time_s": HORIZON_S,
        "deadline_s": HORIZON_S,
        "horizon_s": HORIZON_S,
        "final_window_s": FINAL_WINDOW_S,
        "threshold_provenance": "External sources motivate operation families; numerical criteria are local proposed and uncalibrated.",
    }


def _build_walk(spec: Any, path: Path) -> dict[str, Any]:
    bindings = _base_bindings()
    success = _common_spec("H1-T01", bindings)
    success.update({
        "walking_speed_target_m_s": 0.4,
        "walking_speed_rmse_threshold_m_s": 0.15,
        "swing_to_stance_transitions_per_foot": 3,
        "forbidden_contact_samples_threshold": 0,
        "body_tilt_threshold_deg": 25.0,
        "pelvis_clearance_threshold_m": 0.65,
        "support_surface_z_m": 0.0,
    })
    parameters = {
        "route_length_m": 8.0,
        "route_width_m": 2.0,
        "requested_forward_speed_m_s": 0.4,
        "speed_window_s": [5.0, 15.0],
        "max_duration_s": HORIZON_S,
        "forward_heading_reference": "pelvis initial +x projected on the floor",
    }
    _write_scene(spec, path)
    return _task_case(
        task_id="H1-T01",
        scene_path=path,
        description=(
            "Walk along the level 8 m by 2 m route using alternating left and "
            "right foot support. Maintain 0.4 m/s forward pelvis speed from "
            "5 s through 15 s and stop by 30 s. The maintained local catalog "
            "criteria are retained: each foot needs at least three "
            "swing-to-stance transitions, at least one foot supports at every "
            "sample in the speed window, no non-foot floor contact is allowed, "
            "and final 2 s torso tilt/ pelvis clearance must be within the "
            "declared thresholds."
        ),
        parameters=parameters,
        success_spec=success,
    )


def _build_stand(spec: Any, path: Path) -> dict[str, Any]:
    bindings = _base_bindings()
    success = _common_spec("H1-T02", bindings)
    success.update({
        "standing_planar_drift_threshold_m": 0.08,
        "forbidden_contact_samples_threshold": 0,
        "body_tilt_threshold_deg": 25.0,
        "pelvis_clearance_threshold_m": 0.65,
        "support_surface_z_m": 0.0,
        "required_duration_s": HORIZON_S,
    })
    parameters = {
        "canonical_standing_configuration": "home keyframe",
        "max_duration_s": HORIZON_S,
        "support_contact_bodies": [LEFT_FOOT_BODY, RIGHT_FOOT_BODY],
    }
    _write_scene(spec, path)
    return _task_case(
        task_id="H1-T02",
        scene_path=path,
        description=(
            "Maintain the canonical H1 standing configuration on the level "
            "floor for the complete 30 s observation hold. Both ankle-link "
            "feet must remain in support contact throughout; pelvis planar "
            "drift must stay at or below 0.08 m, non-foot floor contacts are "
            "forbidden, and the final 2 s must satisfy the maintained local "
            "catalog tilt and pelvis-clearance criteria."
        ),
        parameters=parameters,
        success_spec=success,
    )


def _build_reach(spec: Any, path: Path) -> dict[str, Any]:
    # The model's home keyframe puts the actual endpoint at this measured
    # world point.  The target is 0.12 m forward, within the catalog's 0.20 m
    # neighborhood while remaining outside the 0.06 m terminal criterion.
    endpoint = np.asarray((0.2985, 0.21352962, 1.07161367), dtype=float)
    target = tuple((endpoint + np.asarray((0.12, 0.0, 0.0))).tolist())
    bindings = _base_bindings()
    bindings["forearm"] = {
        "kind": "body", "name": LEFT_ELBOW_BODY,
        "local_position_m": list(FOREARM_LOCAL_POINT_M),
    }
    bindings["reach_target"] = {"kind": "body", "name": "reach_target"}
    success = _common_spec("H1-T04", bindings)
    success.update({
        "target_position_m": list(target),
        "forearm_local_point_m": list(FOREARM_LOCAL_POINT_M),
        "forearm_target_error_threshold_m": 0.06,
        "forbidden_contact_samples_threshold": 0,
        "body_tilt_threshold_deg": 25.0,
        "pelvis_clearance_threshold_m": 0.65,
        "support_surface_z_m": 0.0,
    })
    _add_fixed_sphere(
        spec,
        body_name="reach_target",
        geom_name="reach_target_geom",
        position=target,
        radius=0.025,
        collision=False,
    )
    _write_scene(spec, path)
    return _task_case(
        task_id="H1-T04",
        scene_path=path,
        description=(
            "Move the existing left forearm endpoint, measured at local "
            "[0.28, 0, -0.015] on left_elbow_link, to the fixed world target "
            f"{list(target)} m within the 30 s deadline and maintain it "
            "through the final 2 s before termination. The maintained local "
            "catalog requirement is at "
            "most 0.06 m endpoint error, with no non-foot floor contact, no "
            "more than 25 degrees torso tilt, and at least 0.65 m pelvis "
            "clearance. This model has no wrist or articulated hand."
        ),
        parameters={
            "target_position_m": list(target),
            "max_duration_s": HORIZON_S,
            "forearm_local_point_m": list(FOREARM_LOCAL_POINT_M),
        },
        success_spec=success,
    )


def _build_push(spec: Any, path: Path) -> dict[str, Any]:
    table_name = "push_table"
    cube_name = "push_cube"
    y = 0.21352962
    start = (0.43, y, TABLE_TOP_Z_M + CUBE_HALF_SIZE_M[2])
    goal = (0.68, y)
    bindings = _base_bindings()
    bindings["forearm"] = {
        "kind": "body", "name": LEFT_ELBOW_BODY,
        "local_position_m": list(FOREARM_LOCAL_POINT_M),
    }
    bindings["cube"] = {"kind": "body", "name": cube_name}
    bindings["table"] = {"kind": "body", "name": table_name}
    success = _common_spec("H1-T13", bindings)
    success.update({
        "goal_xy_m": list(goal),
        "cube_side_m": 0.15,
        "cube_mass_kg": CUBE_MASS_KG,
        "table_top_z_m": TABLE_TOP_Z_M,
        "required_forearm_body": LEFT_ELBOW_BODY,
        "object_goal_planar_error_threshold_m": 0.08,
        "body_tilt_threshold_deg": 25.0,
        "pelvis_clearance_threshold_m": 0.65,
        "support_surface_z_m": 0.0,
        "required_duration_s": HORIZON_S,
    })
    _add_fixed_box(
        spec, body_name=table_name, geom_name="push_table_geom",
        position=TABLE_CENTER_M, half_size=TABLE_HALF_SIZE_M, collision=True,
    )
    _add_free_box(
        spec, body_name=cube_name, geom_name="push_cube_geom",
        position=start, half_size=CUBE_HALF_SIZE_M, mass_kg=CUBE_MASS_KG,
    )
    _write_scene(spec, path)
    initial = _initial_state()
    initial["free_bodies"][cube_name] = {
        "position_m": list(start),
        "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
        "linear_velocity_m_s": [0.0, 0.0, 0.0],
        "angular_velocity_rad_s": [0.0, 0.0, 0.0],
    }
    return _task_case(
        task_id="H1-T13",
        scene_path=path,
        description=(
            "Use an existing left_elbow_link collision geometry to push the "
            "physical 0.15 m cube of mass 0.5 kg on the rigid 0.80 m table. "
            f"The cube starts at {list(start)} m and its destination is "
            f"{list(goal)} m, 0.25 m away on the same support. At 30 s the "
            "cube must remain table-supported, have at most 0.08 m planar "
            "goal error during the final 2 s, and the final tilt/clearance "
            "criteria from the maintained local catalog must hold."
        ),
        parameters={
            "goal_xy_m": list(goal),
            "cube_initial_position_m": list(start),
            "cube_side_m": 0.15,
            "cube_mass_kg": CUBE_MASS_KG,
            "table_top_z_m": TABLE_TOP_Z_M,
            "max_duration_s": HORIZON_S,
            "forearm_local_point_m": list(FOREARM_LOCAL_POINT_M),
        },
        success_spec=success,
        initial_state=initial,
    )


def _build_button(spec: Any, path: Path) -> dict[str, Any]:
    y0 = BUTTON_PANEL_CENTER_M[1]
    target_positions = tuple(
        (0.40, y0 + (index - 1) * BUTTON_SEPARATION_M, BUTTON_Z_M)
        for index in range(3)
    )
    bindings = _base_bindings()
    bindings["forearm"] = {
        "kind": "body", "name": LEFT_ELBOW_BODY,
        "local_position_m": list(FOREARM_LOCAL_POINT_M),
    }
    bindings["right_forearm"] = {
        "kind": "body", "name": "right_elbow_link",
        "local_position_m": list(FOREARM_LOCAL_POINT_M),
    }
    for index in range(3):
        bindings[f"target_{index}"] = {
            "kind": "body", "name": f"button_target_{index}",
        }
    success = _common_spec("H1-T14", bindings)
    success.update({
        "target_index": 1,
        "button_radius_m": BUTTON_RADIUS_M,
        "button_separation_m": BUTTON_SEPARATION_M,
        "button_panel_height_m": BUTTON_Z_M,
        "required_forearm_bodies": [LEFT_ELBOW_BODY, "right_elbow_link"],
        "forbidden_contact_samples_threshold": 0,
        "body_tilt_threshold_deg": 25.0,
        "pelvis_clearance_threshold_m": 0.65,
        "support_surface_z_m": 0.0,
    })
    # The panel is a visual/non-colliding declaration of the target set.  The
    # spheres are the only physical targets, so a verdict can use native
    # contact pairs without inventing a button actuator or SDK.
    _add_fixed_box(
        spec, body_name="button_panel", geom_name="button_panel_geom",
        position=BUTTON_PANEL_CENTER_M, half_size=BUTTON_PANEL_HALF_SIZE_M,
        collision=False,
    )
    for index, position in enumerate(target_positions):
        _add_fixed_sphere(
            spec,
            body_name=f"button_target_{index}",
            geom_name=f"button_target_{index}_geom",
            position=position,
            radius=BUTTON_RADIUS_M,
            collision=True,
        )
    _write_scene(spec, path)
    return _task_case(
        task_id="H1-T14",
        scene_path=path,
        description=(
            "Touch the selected central physical target sphere with an "
            "existing distal left or right forearm collision geometry. Three fixed "
            f"spheres of radius {BUTTON_RADIUS_M:g} m are mounted at 0.95 m "
            f"height and separated by {BUTTON_SEPARATION_M:g} m; target index "
            "1 is selected. Contact with either non-selected sphere is "
            "forbidden. The final 2 s before termination must also satisfy the maintained local "
            "catalog torso-tilt and pelvis-clearance criteria. There is no "
            "articulated hand, pushbutton actuator, or copied SDK."
        ),
        parameters={
            "target_index": 1,
            "target_positions_m": [list(position) for position in target_positions],
            "target_radius_m": BUTTON_RADIUS_M,
            "target_separation_m": BUTTON_SEPARATION_M,
            "panel_height_m": BUTTON_Z_M,
            "max_duration_s": HORIZON_S,
            "forearm_local_point_m": list(FOREARM_LOCAL_POINT_M),
        },
        success_spec=success,
    )


def prepare_cases(output: str | Path) -> list[dict[str, Any]]:
    """Write one central scene for each selected H1 catalog task."""

    destination = Path(output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    builders = (
        ("H1-T01", _build_walk),
        ("H1-T02", _build_stand),
        ("H1-T04", _build_reach),
        ("H1-T13", _build_push),
        ("H1-T14", _build_button),
    )
    cases = []
    for task_id, builder in builders:
        scene = destination / f"h1_{task_id.lower()}_central.xml"
        cases.append(builder(_new_spec(), scene))
    if [case["task_id"] for case in cases] != list(TASKS):
        raise AssertionError("prepared H1 tasks changed order")
    return cases


def _body_id(model: Any, name: str) -> int:
    return int(model.body(name).id)


def _body_position(model: Any, data: Any, name: str) -> list[float]:
    return data.xpos[_body_id(model, name)].copy().tolist()


def _body_rotation(model: Any, data: Any, name: str) -> np.ndarray:
    return np.asarray(data.xmat[_body_id(model, name)], dtype=float).reshape(3, 3)


def _contact_pairs(model: Any, data: Any) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for contact in data.contact[: data.ncon]:
        if float(contact.dist) > 0.0:
            continue
        body_ids = [int(model.geom_bodyid[geom]) for geom in (contact.geom1, contact.geom2)]
        names = [str(model.body(body_id).name) for body_id in body_ids]
        pairs.add(tuple(sorted(names)))
    return pairs


def _has_contact(pairs: set[tuple[str, str]], first: str, second: str) -> bool:
    return tuple(sorted((first, second))) in pairs


def _reset(model: Any, data: Any, case: dict[str, Any]) -> None:
    sys.path.insert(0, str(ROOT / "AA1"))
    from auto_adapter.scene_runtime import apply_initial_state

    apply_initial_state(model, data, case)


def _no_action_probe(model: Any, data: Any) -> dict[str, Any]:
    """Run the native model with zero controls for the declared 30 s."""

    pelvis_id = _body_id(model, PELVIS_BODY)
    torso_id = _body_id(model, TORSO_BODY)
    while float(data.time) < HORIZON_S - float(model.opt.timestep) * 0.5:
        mujoco.mj_step(model, data)
    tilt = math.degrees(math.acos(float(np.clip(data.xmat[torso_id].reshape(3, 3)[2, 2], -1.0, 1.0))))
    pairs = _contact_pairs(model, data)
    return {
        "sim_time_s": float(data.time),
        "pelvis_position_m": data.xpos[pelvis_id].copy().tolist(),
        "torso_tilt_deg": tilt,
        "both_feet_contact": _has_contact(pairs, "world", LEFT_FOOT_BODY)
        and _has_contact(pairs, "world", RIGHT_FOOT_BODY),
        "criterion_passes": bool(
            tilt <= 25.0
            and float(data.xpos[pelvis_id, 2]) >= 0.65
            and _has_contact(pairs, "world", LEFT_FOOT_BODY)
            and _has_contact(pairs, "world", RIGHT_FOOT_BODY)
        ),
    }


def check_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compile, reset, and probe each native H1 task scene."""

    if [case.get("task_id") for case in cases] != list(TASKS):
        raise ValueError("H1 cases must retain the configured five-task order")
    checked: list[dict[str, Any]] = []
    for case in cases:
        scene_path = Path(case["scene_path"]).resolve()
        model = mujoco.MjModel.from_xml_path(str(scene_path))
        data = mujoco.MjData(model)
        _reset(model, data, case)
        initial_pairs = _contact_pairs(model, data)
        initial_pelvis = _body_position(model, data, PELVIS_BODY)
        initial_torso = _body_rotation(model, data, TORSO_BODY)
        initial_tilt = math.degrees(math.acos(float(np.clip(initial_torso[2, 2], -1.0, 1.0))))
        if not np.isfinite(data.qpos).all() or not np.isfinite(data.qvel).all():
            raise ValueError(f"non-finite reset state: {case['id']}")
        task_id = case["task_id"]
        diagnostics: dict[str, Any] = {
            "initial_pelvis_position_m": initial_pelvis,
            "initial_torso_tilt_deg": initial_tilt,
            "initial_floor_contacts": sorted(pair for pair in initial_pairs if "world" in pair),
        }
        if task_id in {"H1-T01", "H1-T02"}:
            for foot in (LEFT_FOOT_BODY, RIGHT_FOOT_BODY):
                if not _has_contact(initial_pairs, "world", foot):
                    raise ValueError(f"{task_id} initial state has no {foot} floor contact")
        if task_id == "H1-T04":
            endpoint = np.asarray(data.xpos[_body_id(model, LEFT_ELBOW_BODY)]) + _body_rotation(model, data, LEFT_ELBOW_BODY) @ np.asarray(FOREARM_LOCAL_POINT_M)
            target = np.asarray(case["parameters"]["target_position_m"], dtype=float)
            diagnostics["initial_forearm_target_error_m"] = float(np.linalg.norm(endpoint - target))
            if diagnostics["initial_forearm_target_error_m"] <= 0.06:
                raise ValueError(f"H1-T04 starts inside its 0.06 m target criterion: {case['id']}")
        elif task_id == "H1-T13":
            cube = _body_position(model, data, "push_cube")
            diagnostics["initial_cube_table_support"] = _has_contact(initial_pairs, "push_cube", "push_table")
            diagnostics["initial_cube_goal_planar_error_m"] = float(np.linalg.norm(np.asarray(cube[:2]) - np.asarray(case["parameters"]["goal_xy_m"])))
            if not diagnostics["initial_cube_table_support"]:
                raise ValueError(f"H1-T13 cube is not table-supported at reset: {case['id']}")
            if diagnostics["initial_cube_goal_planar_error_m"] <= 0.08:
                raise ValueError(f"H1-T13 starts inside its goal: {case['id']}")
        elif task_id == "H1-T14":
            selected = "button_target_1"
            forearm = np.asarray(data.xpos[_body_id(model, LEFT_ELBOW_BODY)]) + _body_rotation(model, data, LEFT_ELBOW_BODY) @ np.asarray(FOREARM_LOCAL_POINT_M)
            target = np.asarray(_body_position(model, data, selected))
            diagnostics["initial_selected_target_distance_m"] = float(np.linalg.norm(forearm - target))
            diagnostics["initial_button_contacts"] = sorted(pair for pair in initial_pairs if "button_target_" in " ".join(pair))
            if _has_contact(initial_pairs, LEFT_ELBOW_BODY, selected):
                raise ValueError(f"H1-T14 selected target is already touched: {case['id']}")
        no_action = _no_action_probe(model, data)
        if not np.isfinite(data.qpos).all() or not np.isfinite(data.qvel).all():
            raise ValueError(f"non-finite no-action state: {case['id']}")
        diagnostics["no_action_30s"] = no_action
        checked.append({
            "case": case["id"],
            "task_id": task_id,
            "scene_path": str(scene_path),
            "nq": int(model.nq),
            "nv": int(model.nv),
            "nu": int(model.nu),
            "initial": diagnostics,
            "finite": True,
            "native_zero_control_probe": True,
        })
    return checked


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    prepared = prepare_cases(args.output_dir)
    print(json.dumps(check_cases(prepared) if args.check else prepared, indent=2))


__all__ = ["TASKS", "check_cases", "prepare_cases"]
