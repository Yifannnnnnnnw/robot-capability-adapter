"""Prepare five central Skydio X2 task scenes for Chapter 5.

The generated driver is supplied by the Chapter 5 pipeline.  This module only
derives experiment-local XML scenes from the maintained X2 scene and returns
the public task contracts consumed by ``run_tasks.py``.  It never generates or
invokes a driver.

The five selected catalog families are the trace-observable aerial cases:
ground takeoff, stationary hold, a staged return, ordered waypoints, and an
orbit.  The catalog's landing family (X2-T02) is deliberately not selected:
its terminal total-thrust criterion needs actuator-control samples, which the
maintained DemoTrace does not record.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[4]
X2_ASSETS = ROOT / "AA1/assets/mjcf/skydio_x2"
X2_SCENE = X2_ASSETS / "scene.xml"
X2_XML = X2_ASSETS / "x2.xml"

TASKS = ("X2-T01", "X2-T03", "X2-T04", "X2-T05", "X2-T07")
ROBOT_BODY = "x2"
ACTUATORS = ("thrust1", "thrust2", "thrust3", "thrust4")
T03_HOLDING_POINT_WORLD_M = (0.0, 0.0, 1.0)
T01_INITIAL_GROUND_ORIGIN_WORLD_M = (0.0, 0.0, 0.0)
T01_HOLDING_POINT_WORLD_M = (0.0, 0.0, 1.0)
RETURN_START_WORLD_M = (0.0, 0.0, 1.0)
RETURN_HOME_XY_M = (3.0, 0.0)
RETURN_TRANSIT_ALTITUDE_M = 2.0
RETURN_HOLDING_ALTITUDE_M = 1.0
WAYPOINTS_WORLD_M = (
    (0.0, 0.0, 1.5),
    (0.8, 0.0, 1.5),
    (0.8, 0.8, 1.5),
    (0.0, 0.8, 1.5),
)
WAYPOINT_HEADINGS_RAD = (0.0, 0.0, 0.0, 0.0)
ORBIT_START_WORLD_M = (1.5, 0.0, 1.5)
ORBIT_CENTER_XY_M = (0.0, 0.0)
ORBIT_RADIUS_M = 1.5
ORBIT_HEIGHT_M = 1.5
MAX_INITIAL_PENETRATION_M = 1e-6
NEGATIVE_CONTROL_DURATION_S = 60.0


def _fmt(values: tuple[float, ...] | list[float]) -> str:
    return " ".join(f"{float(value):.12g}" for value in values)


def _zero_control_state() -> dict:
    return {name: 0.0 for name in ACTUATORS}


def _initial_state(
    position: tuple[float, float, float],
    quaternion_wxyz: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0),
) -> dict:
    return {
        "robot": {"keyframe": None, "ctrl_by_actuator": _zero_control_state()},
        "free_bodies": {
            ROBOT_BODY: {
                "position_m": list(position),
                "quaternion_wxyz": list(quaternion_wxyz),
                "linear_velocity_m_s": [0.0, 0.0, 0.0],
                "angular_velocity_rad_s": [0.0, 0.0, 0.0],
            }
        },
        "settle_s": 0.0,
    }


def _add_marker(
    worldbody: ET.Element,
    *,
    body_name: str,
    geom_name: str,
    position: tuple[float, float, float],
    size: tuple[float, ...] = (0.025,),
    geom_type: str = "sphere",
) -> None:
    body = ET.SubElement(worldbody, "body", {
        "name": body_name,
        "pos": _fmt(position),
    })
    ET.SubElement(body, "geom", {
        "name": geom_name,
        "type": geom_type,
        "size": _fmt(size),
        "material": "x2_task_target",
        "contype": "0",
        "conaffinity": "0",
        "density": "0",
    })


def _write_robot_include(destination: Path) -> Path:
    """Write a path-adjusted copy of the canonical robot include.

    MuJoCo resolves an included file's relative ``assetdir`` against the
    top-level prepared scene.  Keeping the include beside that scene while
    pointing only its asset directory at the maintained mesh directory makes
    each generated case independently loadable without changing the robot
    body, actuators, sensors or numeric model values.
    """

    root = copy.deepcopy(ET.parse(X2_XML).getroot())
    compiler = root.find("compiler")
    if compiler is None:
        raise ValueError("maintained X2 include has no compiler")
    compiler.set("assetdir", str((X2_ASSETS / "assets").resolve()))
    include = destination / "x2.xml"
    ET.indent(root)
    ET.ElementTree(root).write(include, encoding="unicode")
    return include


def _template(robot_include: Path) -> ET.Element:
    if not X2_SCENE.is_file() or not X2_XML.is_file():
        raise FileNotFoundError(f"maintained Skydio X2 assets are unavailable: {X2_SCENE}")
    root = copy.deepcopy(ET.parse(X2_SCENE).getroot())
    include = root.find("include")
    if include is None:
        raise ValueError("maintained X2 scene has no x2.xml include")
    # Keep the canonical included robot unchanged while making prepared scenes
    # independently loadable from a task output directory.
    include.set("file", str(robot_include.resolve()))
    asset = root.find("asset")
    worldbody = root.find("worldbody")
    if asset is None or worldbody is None:
        raise ValueError("maintained X2 scene has no asset/worldbody")
    ET.SubElement(asset, "material", {
        "name": "x2_task_target",
        "rgba": "0.95 0.35 0.05 0.75",
    })
    return root


def _write_scene(root: ET.Element, destination: Path) -> None:
    ET.indent(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(destination, encoding="unicode")


def _case_t01(scene: Path) -> dict:
    return {
        "id": "X2-T01_central",
        "task_id": "X2-T01",
        "scene_path": str(scene.resolve()),
        "initial_state": _initial_state(T01_INITIAL_GROUND_ORIGIN_WORLD_M),
        "parameters": {},
        "task_description": (
            "Ground takeoff task X2-T01. Starting from the current resting "
            "ground state, use the advertised X2 takeoff/flight capability to "
            "lift to the fixed holding point (0, 0, 1.0) m. Remain there with "
            "low translation speed and upright attitude; keep the rotorcraft "
            "off the floor after t=5 s."
        ),
        "success_spec": {
            "type": "skydio_x2_catalog_task",
            "task_id": "X2-T01",
            "bindings": {"base": {"kind": "body", "name": ROBOT_BODY}},
            "holding_point_world_m": list(T01_HOLDING_POINT_WORLD_M),
            "terminal_window_s": 2.0,
            "ground_contact_deadline_s": 5.0,
            "position_threshold_m": 0.15,
            "translation_speed_threshold_m_s": 0.1,
            "body_tilt_threshold_deg": 20.0,
        },
    }


def _case_t03(scene: Path) -> dict:
    return {
        "id": "X2-T03_central",
        "task_id": "X2-T03",
        "scene_path": str(scene.resolve()),
        "initial_state": _initial_state(T03_HOLDING_POINT_WORLD_M),
        "parameters": {},
        "task_description": (
            "Stationary airborne hold task X2-T03. The aircraft is already at "
            "the current airborne holding point; maintain this position and "
            "the initial heading for the full 60-second declared hold. Use "
            "native flight feedback and keep the aircraft upright without any "
            "floor or fixture contact."
        ),
        "success_spec": {
            "type": "skydio_x2_catalog_task",
            "task_id": "X2-T03",
            "bindings": {"base": {"kind": "body", "name": ROBOT_BODY}},
            "hold_duration_s": 60.0,
            "position_threshold_m": 0.15,
            "heading_threshold_deg": 10.0,
            "body_tilt_threshold_deg": 20.0,
        },
    }


def _case_t04(scene: Path) -> dict:
    home = list(RETURN_HOME_XY_M)
    return {
        "id": "X2-T04_central",
        "task_id": "X2-T04",
        "scene_path": str(scene.resolve()),
        "initial_state": _initial_state(RETURN_START_WORLD_M),
        "parameters": {"home_xy_m": home},
        "task_description": (
            "Climb-transit-descend return task X2-T04. From the current "
            "airborne state, first climb to 2.0 m before translating toward "
            "home_xy_m [3.0, 0.0]. Stay at or above 1.8 m until within 0.2 m "
            "horizontally of home, then descend to the fixed 1.0 m holding "
            "altitude and settle upright without contact."
        ),
        "success_spec": {
            "type": "skydio_x2_catalog_task",
            "task_id": "X2-T04",
            "bindings": {"base": {"kind": "body", "name": ROBOT_BODY}},
            "transit_altitude_m": 1.9,
            "transit_clearance_m": 1.8,
            "home_horizontal_tolerance_m": 0.2,
            "holding_altitude_m": RETURN_HOLDING_ALTITUDE_M,
            "terminal_window_s": 2.0,
            "position_threshold_m": 0.15,
            "translation_speed_threshold_m_s": 0.1,
            "body_tilt_threshold_deg": 20.0,
        },
    }


def _case_t05(scene: Path) -> dict:
    waypoints = [list(point) for point in WAYPOINTS_WORLD_M]
    return {
        "id": "X2-T05_central",
        "task_id": "X2-T05",
        "scene_path": str(scene.resolve()),
        "initial_state": _initial_state(RETURN_START_WORLD_M),
        "parameters": {
            "waypoints_xyz_m": waypoints,
            "waypoint_headings_rad": list(WAYPOINT_HEADINGS_RAD),
        },
        "task_description": (
            "Ordered waypoint mission X2-T05. Take off to the fixed 1.5 m "
            "mission altitude, then visit these four ordered world waypoints "
            f"{waypoints} m. Hold each position and the corresponding zero yaw "
            "heading for at least 0.5 s, then hold at the last waypoint. Stay "
            "upright and avoid all contact."
        ),
        "success_spec": {
            "type": "skydio_x2_catalog_task",
            "task_id": "X2-T05",
            "bindings": {"base": {"kind": "body", "name": ROBOT_BODY}},
            "waypoint_position_threshold_m": 0.20,
            "waypoint_heading_threshold_deg": 10.0,
            "waypoint_hold_s": 0.5,
            "terminal_window_s": 2.0,
            "position_threshold_m": 0.15,
            "translation_speed_threshold_m_s": 0.1,
            "body_tilt_threshold_deg": 20.0,
        },
    }


def _case_t07(scene: Path) -> dict:
    return {
        "id": "X2-T07_central",
        "task_id": "X2-T07",
        "scene_path": str(scene.resolve()),
        # At (1.5, 0, 1.5), yaw pi points body +x toward the center.  This
        # makes the bearing criterion cover the orbit itself from its first
        # sample rather than charging an artificial approach heading.
        "initial_state": _initial_state(ORBIT_START_WORLD_M, (0.0, 0.0, 0.0, 1.0)),
        "parameters": {"center_xy_m": list(ORBIT_CENTER_XY_M)},
        "task_description": (
            "Point-of-interest orbit task X2-T07. Fly one complete positive "
            "counter-clockwise circuit around center_xy_m [0.0, 0.0] at the "
            "fixed 1.5 m horizontal radius and 1.5 m height. Keep the aircraft "
            "body +x pointed toward the center, remain upright, and avoid all "
            "floor or fixture contact. The total simulation time for all flight "
            "and intermediate holds must be at most 60 s. Completion requires "
            "cumulative signed positive angular progress of at least 2*pi and "
            "a recorded trajectory that actually crosses the starting ray; "
            "merely commanding a return to the starting point is insufficient. "
            "Keep radius error at or below 0.20 m, height error at or below "
            "0.15 m, and bearing RMSE at or below 12 deg."
        ),
        "success_spec": {
            "type": "skydio_x2_catalog_task",
            "task_id": "X2-T07",
            "bindings": {"base": {"kind": "body", "name": ROBOT_BODY}},
            "orbit_radius_m": ORBIT_RADIUS_M,
            "orbit_height_m": ORBIT_HEIGHT_M,
            "positive_direction": 1,
            "orbit_progress_threshold_rad": 2.0 * math.pi,
            "radius_threshold_m": 0.2,
            "height_threshold_m": 0.15,
            "bearing_rmse_threshold_deg": 12.0,
            "body_tilt_threshold_deg": 20.0,
        },
    }


def _build_case(task_id: str, destination: Path, robot_include: Path) -> dict:
    root = _template(robot_include)
    worldbody = root.find("worldbody")
    assert worldbody is not None
    if task_id == "X2-T01":
        _add_marker(worldbody, body_name="x2_t01_holding_point",
                    geom_name="x2_t01_holding_target", position=T01_HOLDING_POINT_WORLD_M)
        case = _case_t01(destination / "x2_t01_central.xml")
    elif task_id == "X2-T03":
        _add_marker(worldbody, body_name="x2_t03_holding_point",
                    geom_name="x2_t03_holding_target", position=T03_HOLDING_POINT_WORLD_M)
        case = _case_t03(destination / "x2_t03_central.xml")
    elif task_id == "X2-T04":
        _add_marker(worldbody, body_name="x2_t04_home_point",
                    geom_name="x2_t04_home_target",
                    position=(RETURN_HOME_XY_M[0], RETURN_HOME_XY_M[1], RETURN_HOLDING_ALTITUDE_M))
        case = _case_t04(destination / "x2_t04_central.xml")
    elif task_id == "X2-T05":
        for index, point in enumerate(WAYPOINTS_WORLD_M, 1):
            _add_marker(worldbody, body_name=f"x2_t05_waypoint_{index}",
                        geom_name=f"x2_t05_waypoint_target_{index}", position=point)
        case = _case_t05(destination / "x2_t05_central.xml")
    elif task_id == "X2-T07":
        _add_marker(worldbody, body_name="x2_t07_orbit_center",
                    geom_name="x2_t07_orbit_target", position=(0.0, 0.0, ORBIT_HEIGHT_M),
                    size=(0.04,))
        case = _case_t07(destination / "x2_t07_central.xml")
    else:
        raise ValueError(f"unknown Skydio task {task_id!r}")
    _write_scene(root, Path(case["scene_path"]))
    return case


def prepare_cases(output: str | Path) -> list[dict]:
    """Write five central scenes and return explicit task contracts."""

    destination = Path(output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    robot_include = _write_robot_include(destination)
    cases = [_build_case(task_id, destination, robot_include) for task_id in TASKS]
    return cases


def _body_position(model, data) -> list[float]:
    return [float(value) for value in data.xpos[model.body(ROBOT_BODY).id]]


def _robot_floor_contacts(model, data) -> int:
    robot_id = int(model.body(ROBOT_BODY).id)
    count = 0
    for contact in data.contact[:data.ncon]:
        if contact.dist > 0:
            continue
        bodies = (int(model.geom_bodyid[contact.geom1]),
                  int(model.geom_bodyid[contact.geom2]))
        if robot_id in bodies and 0 in bodies:
            count += 1
    return count


def _max_contact_penetration(model, data) -> float:
    robot_id = int(model.body(ROBOT_BODY).id)
    return max(
        (max(0.0, -float(contact.dist))
         for contact in data.contact[:data.ncon]
         if robot_id in {
             int(model.geom_bodyid[contact.geom1]),
             int(model.geom_bodyid[contact.geom2]),
         }),
        default=0.0,
    )


def check_cases(cases: list[dict]) -> list[dict]:
    """Load native scenes and run explicit zero-control negative controls."""

    import mujoco
    import numpy as np

    sys.path.insert(0, str(ROOT / "AA1"))
    from auto_adapter.scene_runtime import apply_initial_state

    if [case["task_id"] for case in cases] != list(TASKS):
        raise ValueError("Skydio cases must retain the configured five-task order")
    checked = []
    for case in cases:
        model = mujoco.MjModel.from_xml_path(case["scene_path"])
        data = mujoco.MjData(model)
        apply_initial_state(model, data, case)
        mujoco.mj_forward(model, data)
        initial_position = _body_position(model, data)
        initial_penetration = _max_contact_penetration(model, data)
        if initial_penetration > MAX_INITIAL_PENETRATION_M:
            raise ValueError(
                f"{case['task_id']} initial X2 penetration exceeds "
                f"{MAX_INITIAL_PENETRATION_M:g} m: {initial_penetration:g}"
            )
        initial_floor_contacts = _robot_floor_contacts(model, data)
        if case["task_id"] == "X2-T01" and initial_floor_contacts == 0:
            raise ValueError("X2-T01 must begin with a native robot-floor contact")
        contact_samples = 0
        negative_steps = max(
            1,
            int(math.ceil(
                max(0.0, NEGATIVE_CONTROL_DURATION_S - float(data.time))
                / float(model.opt.timestep)
            )),
        )
        for _ in range(negative_steps):
            # The explicit zero-control baseline is an independent physical
            # negative control.  It does not call or inspect a driver.
            data.ctrl[:] = 0.0
            mujoco.mj_step(model, data)
            if _robot_floor_contacts(model, data):
                contact_samples += 1
        final_position = _body_position(model, data)
        if not all(np.isfinite(array).all() for array in (data.qpos, data.qvel, data.ctrl)):
            raise ValueError(f"non-finite zero-control state: {case['id']}")
        if case["task_id"] == "X2-T01":
            target = np.asarray(T01_HOLDING_POINT_WORLD_M)
            final_target_error = float(np.linalg.norm(np.asarray(final_position) - target))
            negative_success = bool(
                final_target_error <= 0.15 and contact_samples == 0
            )
        else:
            final_target_error = None
            negative_success = bool(contact_samples == 0)
        if negative_success:
            raise ValueError(f"zero-control baseline could satisfy {case['id']}")
        checked.append({
            "case": case["id"],
            "task_id": case["task_id"],
            "nq": int(model.nq),
            "nv": int(model.nv),
            "nu": int(model.nu),
            "initial_position_m": initial_position,
            "final_zero_control_position_m": final_position,
            "initial_max_contact_penetration_m": initial_penetration,
            "initial_robot_floor_contact_samples": initial_floor_contacts,
            "zero_control_floor_contact_samples": contact_samples,
            "zero_control_duration_s": float(data.time),
            "zero_control_final_target_error_m": final_target_error,
            "zero_control_physical_success": False,
            "finite": True,
            "no_action": True,
        })
    return checked


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    prepared = prepare_cases(args.output_dir)
    print(json.dumps(check_cases(prepared) if args.check else prepared,
                       indent=2, ensure_ascii=False))
