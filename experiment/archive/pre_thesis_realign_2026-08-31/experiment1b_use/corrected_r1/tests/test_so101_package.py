from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from autoadapter2.harness.session import apply_framework_reset
from autoadapter2.libraries import load_robot_package


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
ROBOT_ROOT = (
    REPOSITORY_ROOT
    / "autoadapter"
    / "libraries"
    / "robots"
    / "robotstudio_so101"
)
PACKAGE_ROOT = ROBOT_ROOT / "1.0.2"
OLD_PACKAGE_ROOT = ROBOT_ROOT / "1.0.0"
CORRECTED_TASK_IDS = {
    "mw_push_to_goal",
    "mw_sweep_into_goal",
    "mw_pick_place",
    "mw_pick_place_wall",
    "mw_dial_turn",
}
SNAPSHOT_ID = "robotstudio-so101-corrected-r1-2026-08-24-v1"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _instances(root: Path) -> dict[str, dict[str, Any]]:
    document = _read(root / "tasks" / "private" / "instances.json")
    return {item["task_id"]: item for item in document["instances"]}


def _tasks(root: Path) -> dict[str, dict[str, Any]]:
    document = _read(root / "tasks" / "catalog.json")
    return {item["task_id"]: item for item in document["tasks"]}


def _binding(root: Path, binding_id: str) -> dict[str, Any]:
    document = _read(root / "tasks" / "private" / "bindings.json")
    return next(
        item for item in document["bindings"] if item["binding_id"] == binding_id
    )


def _name_id(model: Any, object_type: Any, name: str) -> int:
    identifier = int(mujoco.mj_name2id(model, object_type, name))
    assert identifier >= 0, name
    return identifier


def _model_and_data(root: Path, instance: dict[str, Any]) -> tuple[Any, Any]:
    model = mujoco.MjModel.from_xml_path(
        str((root / instance["scene_entrypoint"]).resolve())
    )
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, instance["reset"])
    mujoco.mj_forward(model, data)
    return model, data


def _strict_chain_margin(root: Path) -> float:
    instance = _instances(root)["mw_pick_place_wall"]
    parameters = instance["public_arguments"]["request"]["task_parameters"]
    model, data = _model_and_data(root, instance)
    site_id = _name_id(model, mujoco.mjtObj.mjOBJ_SITE, "gripperframe")
    body_id = int(model.site_bodyid[site_id])
    chain_bound = float(np.linalg.norm(model.site_pos[site_id]))
    while body_id > 0:
        chain_bound += float(np.linalg.norm(model.body_pos[body_id]))
        body_id = int(model.body_parentid[body_id])
    base_id = _name_id(model, mujoco.mjtObj.mjOBJ_BODY, "base")
    base_position = np.asarray(data.xpos[base_id], dtype=float)
    grasp = np.asarray(parameters["grasp_position"], dtype=float)
    waypoints = (
        grasp + np.asarray((0.0, 0.0, 0.095)),
        grasp,
        np.asarray(parameters["route_position"], dtype=float),
        np.asarray(parameters["release_position"], dtype=float),
        np.asarray(parameters["tool_target_position"], dtype=float),
    )
    return min(
        chain_bound - float(np.linalg.norm(waypoint - base_position))
        for waypoint in waypoints
    ) - 0.030


def _fixed_driver(model: Any, data: Any) -> Any:
    driver_path = PACKAGE_ROOT / "reference" / "fixed_capability_driver.py"
    spec = importlib.util.spec_from_file_location(
        "so101_corrected_fixed_capability_driver", driver_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(model=model, data=data)


def _opening_fraction(target_rad: float) -> float:
    return (target_rad + 0.17453) / (1.74533 + 0.17453)


def test_corrected_package_is_isolated_and_selects_the_five_arm_tasks() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    assert package.package_version == "1.0.2"
    assert package.snapshot_id == SNAPSHOT_ID
    assert CORRECTED_TASK_IDS < {task["task_id"] for task in package.tasks}

    for relative in (
        Path("tasks/catalog.json"),
        Path("tasks/private/instances.json"),
        Path("tasks/private/bindings.json"),
        Path("tasks/private/guards.json"),
    ):
        document = _read(PACKAGE_ROOT / relative)
        assert document["package_version"] == "1.0.2"
        snapshot = document.get("snapshot_id", document.get("task_snapshot_id"))
        assert snapshot == SNAPSHOT_ID

    # These three retained/replacement tasks keep their old public contract,
    # scene/reset, and fixed capability Driver exactly; the corrected suite,
    # not the package catalogue, excludes bin and reach.
    old_instances = _instances(OLD_PACKAGE_ROOT)
    new_instances = _instances(PACKAGE_ROOT)
    old_tasks = _tasks(OLD_PACKAGE_ROOT)
    new_tasks = _tasks(PACKAGE_ROOT)
    scene_names = {
        "mw_push_to_goal": "push_to_goal_scene.xml",
        "mw_sweep_into_goal": "sweep_into_goal_scene.xml",
        "mw_pick_place": "pick_place_scene.xml",
    }
    for task_id, scene_name in scene_names.items():
        old_instance = dict(old_instances[task_id])
        new_instance = dict(new_instances[task_id])
        assert old_instance.pop("video_fps") == 10.0
        assert new_instance.pop("video_fps") == 20.0
        assert new_instance == old_instance
        assert new_tasks[task_id] == old_tasks[task_id]
        assert (PACKAGE_ROOT / "assets" / scene_name).read_bytes() == (
            OLD_PACKAGE_ROOT / "assets" / scene_name
        ).read_bytes()
    assert (
        PACKAGE_ROOT / "reference" / "fixed_capability_driver.py"
    ).read_bytes() == (
        OLD_PACKAGE_ROOT / "reference" / "fixed_capability_driver.py"
    ).read_bytes()


def test_wall_layout_repairs_reachability_without_relaxing_the_metric() -> None:
    old_margin = _strict_chain_margin(OLD_PACKAGE_ROOT)
    corrected_margin = _strict_chain_margin(PACKAGE_ROOT)
    assert old_margin < -0.045
    assert corrected_margin > 0.022

    instance = _instances(PACKAGE_ROOT)["mw_pick_place_wall"]
    parameters = instance["public_arguments"]["request"]["task_parameters"]
    assert parameters == {
        "start_position": [0.30, 0.08, 0.18],
        "grasp_position": [0.30, 0.07, 0.195],
        "grasp_wrist_roll": -1.5707963267948966,
        "grasp_gripper": 0.25,
        "target_position": [0.42, 0.08, 0.26],
        "release_position": [0.411, 0.082, 0.272],
        "tool_target_position": [0.411, 0.082, 0.272],
        "route_position": [0.36, 0.08, 0.30],
    }
    assert instance["sample_hz"] == 20.0
    assert instance["video_fps"] == 20.0

    task = _tasks(PACKAGE_ROOT)["mw_pick_place_wall"]
    assert task["scoring"][0]["metric"] == "object_goal_distance"
    assert task["scoring"][0]["comparator"] == "<="
    assert task["scoring"][0]["threshold"] == 0.07
    assert _binding(PACKAGE_ROOT, "binding-mw_pick_place_wall") == {
        "binding_id": "binding-mw_pick_place_wall",
        "metric": "object_goal_distance",
        "unit": "m",
        "kind": "final_body_position_error",
        "parameters": {
            "body_name": "workpiece",
            "target_argument": "request.task_parameters.target_position",
        },
    }

    scene = ET.parse(PACKAGE_ROOT / instance["scene_entrypoint"]).getroot()
    worldbody = scene.find("worldbody")
    assert worldbody is not None
    wall = next(geom for geom in worldbody.findall("geom") if geom.get("name") == "wall")
    assert wall.get("pos") == "0.36 0.08 0.20"
    assert wall.get("size") == "0.012 0.18 0.04"
    workpiece = next(
        body for body in worldbody.findall("body") if body.get("name") == "workpiece"
    )
    assert workpiece.get("pos") == "0.30 0.08 0.18"
    workpiece_geom = next(
        geom for geom in workpiece.findall("geom") if geom.get("name") == "workpiece_geom"
    )
    assert workpiece_geom.get("mass") == "0.20"


def test_dial_metric_and_public_task_data_match_the_corrected_suite() -> None:
    instance = _instances(PACKAGE_ROOT)["mw_dial_turn"]
    parameters = instance["public_arguments"]["request"]["task_parameters"]
    assert parameters == {
        "contact_position": [0.397, 0.07, 0.29],
        "route_position": [0.397, 0.0875, 0.2335],
        "target_position": [0.40, 0.134, 0.177],
        "tool_target_position": [0.397, 0.134, 0.197],
    }
    assert instance["sample_hz"] == 20.0
    assert instance["video_fps"] == 20.0

    task = _tasks(PACKAGE_ROOT)["mw_dial_turn"]
    criterion = task["scoring"][0]
    assert criterion["metric"] == "dial_target_distance"
    assert criterion["comparator"] == "<="
    assert criterion["threshold"] == 0.07
    schema = task["invocation_schema"]["request"]["task_parameters"]
    assert set(schema["required"]) == {
        "contact_position",
        "route_position",
        "target_position",
        "tool_target_position",
    }
    assert _binding(PACKAGE_ROOT, "binding-mw_dial_turn") == {
        "binding_id": "binding-mw_dial_turn",
        "metric": "dial_target_distance",
        "unit": "m",
        "kind": "final_site_position_error",
        "parameters": {
            "site_name": "dial_tip_site",
            "target_argument": "request.task_parameters.target_position",
        },
    }

    model, data = _model_and_data(PACKAGE_ROOT, instance)
    _name_id(model, mujoco.mjtObj.mjOBJ_BODY, "dial")
    _name_id(model, mujoco.mjtObj.mjOBJ_BODY, "dial_tip")
    _name_id(model, mujoco.mjtObj.mjOBJ_GEOM, "dial_tip_geom")
    tip_id = _name_id(model, mujoco.mjtObj.mjOBJ_SITE, "dial_tip_site")
    reset_error = float(
        np.linalg.norm(
            np.asarray(data.site_xpos[tip_id], dtype=float)
            - np.asarray(parameters["target_position"], dtype=float)
        )
    )
    assert reset_error > 0.07


def test_fixed_capability_driver_is_a_metric_positive_control_for_wall_and_dial() -> None:
    wall = _instances(PACKAGE_ROOT)["mw_pick_place_wall"]
    wall_model, wall_data = _model_and_data(PACKAGE_ROOT, wall)
    wall_driver = _fixed_driver(wall_model, wall_data)
    wall_driver.set_gripper_opening(
        {"opening_fraction": 1.0, "max_duration_s": 0.5}
    )
    wall_driver.set_wrist_roll(
        {"target_roll_rad": -np.pi / 2.0, "max_duration_s": 1.0}
    )
    wall_driver.move_end_effector_to_position(
        {"target_position_m": [0.29, 0.08, 0.29], "max_duration_s": 3.0}
    )
    wall_driver.set_gripper_opening(
        {
            "opening_fraction": _opening_fraction(0.35),
            "max_duration_s": 0.75,
        }
    )
    wall_driver.approach_until_contact(
        {
            "precontact_position_m": [0.29, 0.08, 0.255],
            "approach_direction_unit": [0.0, 0.0, -1.0],
            "max_travel_m": 0.075,
            "max_approach_speed_m_s": 0.03,
            "max_duration_s": 4.0,
        }
    )
    wall_driver.set_gripper_opening(
        {
            "opening_fraction": _opening_fraction(0.25),
            "max_duration_s": 1.0,
        }
    )
    wall_driver.move_end_effector_to_position(
        {"target_position_m": [0.29, 0.08, 0.29], "max_duration_s": 3.0}
    )
    wall_parameters = wall["public_arguments"]["request"]["task_parameters"]
    wall_driver.trace_cartesian_path(
        {
            "waypoints_m": [
                wall_parameters["route_position"],
                wall_parameters["tool_target_position"],
            ],
            "max_duration_per_segment_s": 4.0,
        }
    )
    wall_driver.set_gripper_opening(
        {"opening_fraction": 1.0, "max_duration_s": 0.5}
    )
    workpiece_id = _name_id(
        wall_model, mujoco.mjtObj.mjOBJ_BODY, "workpiece"
    )
    wall_error = float(
        np.linalg.norm(
            np.asarray(wall_data.xpos[workpiece_id], dtype=float)
            - np.asarray(wall_parameters["target_position"], dtype=float)
        )
    )
    assert wall_error <= 0.07

    dial = _instances(PACKAGE_ROOT)["mw_dial_turn"]
    dial_model, dial_data = _model_and_data(PACKAGE_ROOT, dial)
    dial_driver = _fixed_driver(dial_model, dial_data)
    dial_driver.set_gripper_opening(
        {"opening_fraction": 0.0, "max_duration_s": 0.5}
    )
    dial_driver.move_end_effector_to_position(
        {"target_position_m": [0.397, 0.07, 0.37], "max_duration_s": 3.0}
    )
    dial_driver.approach_until_contact(
        {
            "precontact_position_m": [0.397, 0.07, 0.31],
            "approach_direction_unit": [0.0, 0.0, -1.0],
            "max_travel_m": 0.04,
            "max_approach_speed_m_s": 0.03,
            "max_duration_s": 3.0,
        }
    )
    dial_parameters = dial["public_arguments"]["request"]["task_parameters"]
    dial_driver.trace_cartesian_path(
        {
            "waypoints_m": [
                dial_parameters["route_position"],
                dial_parameters["tool_target_position"],
            ],
            "max_duration_per_segment_s": 4.0,
        }
    )
    dial_tip_id = _name_id(
        dial_model, mujoco.mjtObj.mjOBJ_SITE, "dial_tip_site"
    )
    dial_error = float(
        np.linalg.norm(
            np.asarray(dial_data.site_xpos[dial_tip_id], dtype=float)
            - np.asarray(dial_parameters["target_position"], dtype=float)
        )
    )
    assert dial_error <= 0.07
