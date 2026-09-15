"""Five concrete Panda tasks, each translated by 0 or +/-5 cm along world Y.

prepare_cases writes task scenes only; it never generates or invokes a driver.
The checked-in central scenes retain the canonical Panda robot unchanged.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parents[2]
ROOT = HERE.parents[1]
PANDA = ROOT / "AA1/assets/mjcf/franka_panda"
TASKS = ("mw_reach_target", "mw_push_to_goal", "mw_pick_place", "mw_drawer_open", "mw_dial_turn")
CONDITIONS = (("central", 0.0), ("left", 0.05), ("right", -0.05))
TOLERANCES = dict(zip(TASKS, (0.05, 0.05, 0.07, 0.03, 0.07)))
STARTS = {
    "mw_reach_target": None,
    "mw_push_to_goal": [0.47, -0.07, 0.325],
    "mw_pick_place": [0.45, -0.09, 0.325],
    "mw_drawer_open": [0.56, 0.0, 0.39],
    "mw_dial_turn": [0.64, 0.0, 0.37],
}
GOALS = {
    "mw_reach_target": [0.46, 0.10, 0.47],
    "mw_push_to_goal": [0.63, -0.07, 0.325],
    "mw_pick_place": [0.60, 0.09, 0.405],
    "mw_drawer_open": [0.40, 0.0, 0.39],
    "mw_dial_turn": [0.53, 0.11, 0.37],
}
DESCRIPTIONS = {
    "mw_reach_target": "Move the hand body origin to the world-frame target and stop there.",
    "mw_push_to_goal": "Physically push target_object across source_table to the target; leave the object at the target.",
    "mw_pick_place": "Grasp target_object with both fingers, lift it clear of source_table, carry it onto the raised destination, then open the fingers and withdraw. Leave the object supported at the goal without tool contact for at least 0.2 seconds.",
    "mw_drawer_open": "Grasp or hook the drawer handle target_object and pull it 0.16 m along negative world X to its open-state target. Move the physical handle, not just the hand.",
    "mw_dial_turn": "Grasp or push the off-axis peg target_object to turn the dial counterclockwise by 90 degrees around its vertical axis. Leave the peg at its target; rotating only the robot wrist does not complete this task.",
}


def _shift(point, y):
    return [point[0], round(point[1] + y, 8), point[2]]


def _case(task_id: str, condition: str, shift_y: float, scene: Path) -> dict:
    goal = _shift(GOALS[task_id], shift_y)
    start = _shift(STARTS[task_id], shift_y) if STARTS[task_id] else None
    bindings = {"ee": {"kind": "body", "name": "hand"},
                "target": {"kind": "body", "name": "target_goal"},
                "left_finger": {"kind": "body", "name": "left_finger"},
                "right_finger": {"kind": "body", "name": "right_finger"}}
    if start:
        bindings["object"] = {"kind": "body", "name": "target_object"}
    if task_id in ("mw_drawer_open", "mw_dial_turn"):
        bindings["fixture_joint"] = {"kind": "joint", "name": "fixture_joint"}
    initial = {"robot": {"keyframe": "home"}, "settle_s": 0.3}
    if task_id in ("mw_push_to_goal", "mw_pick_place"):
        initial["free_bodies"] = {"target_object": {
            "position_m": start, "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]}}
    if task_id in ("mw_drawer_open", "mw_dial_turn"):
        initial["robot"]["qpos_by_joint"] = {"fixture_joint": 0.0}
    parameters = {
        "target_position_world_m": goal,
        "max_duration_s": 30.0,
        "endpoint_tolerance_m": TOLERANCES[task_id],
        "end_effector_reference": {"kind": "body", "name": "hand"},
        "grasp_reference_note": "The hand origin is not the fingertip midpoint. With the home downward orientation, the finger-pad midpoint is approximately 0.1034 m below the hand origin; compute its current world position from the robot state and orientation when approaching an object.",
        "gripper_actuator_range": [0, 255],
        "gripper_opening_note": "The canonical actuator8 control is 0 for closed and 255 for fully open. Use the generated capability's advertised schema and units.",
    }
    if start:
        parameters.update(object_body="target_object", object_initial_position_world_m=start)
    spec = {"type": "franka_catalog_task", "task_id": task_id, "bindings": bindings,
            "endpoint_tolerance_m": TOLERANCES[task_id],
            "contact_tool_body_names": ["hand", "left_finger", "right_finger"],
            "object_body_name": "target_object", "minimum_motion_m": 0.01,
            "max_duration_s": 30.0}
    if task_id == "mw_push_to_goal":
        parameters.update(push_delta_world_m=[0.16, 0.0, 0.0], table_top_height_m=0.3,
                          object_half_size_m=[0.022, 0.022, 0.025])
    if task_id == "mw_pick_place":
        parameters.update(object_half_size_m=[0.022, 0.022, 0.025],
                          source_table_top_height_m=0.3, destination_top_height_m=0.38,
                          lift_clearance_m=0.09, release_hold_s=0.2)
        spec.update(minimum_lift_m=0.03, release_hold_s=0.2,
                    support_body_names=["source_table", "destination"])
    if task_id == "mw_drawer_open":
        parameters.update(drawer_slide_axis_world=[-1.0, 0.0, 0.0],
                          target_joint_position_m=0.16, handle_bar_radius_m=0.008,
                          handle_bar_axis_world=[0.0, 1.0, 0.0])
        spec.update(minimum_joint_motion=0.01, drawer_slide_axis_world=[-1.0, 0.0, 0.0])
    if task_id == "mw_dial_turn":
        parameters.update(dial_pivot_world_m=_shift([0.53, 0.0, 0.37], shift_y),
                          dial_axis_world=[0.0, 0.0, 1.0], dial_radius_m=0.11,
                          target_joint_position_rad=1.5707963267948966,
                          peg_radius_m=0.009, peg_half_height_m=0.025)
        spec.update(minimum_joint_motion=0.01, dial_axis_world=[0.0, 0.0, 1.0],
                    dial_pivot_world_m=parameters["dial_pivot_world_m"])
    return {"id": f"{task_id}_{condition}", "task_id": task_id,
            "scene_path": str(scene.resolve()), "initial_state": initial,
            "parameters": parameters, "task_description": DESCRIPTIONS[task_id],
            "success_spec": spec}


def prepare_cases(output_dir: str | Path) -> list[dict]:
    """Write 15 derived MJCFs and return explicit, independently executable tasks."""
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    cases = []
    for task_id in TASKS:
        template = ET.parse(HERE / "scenes" / f"{task_id}.xml")
        for condition, shift_y in CONDITIONS:
            root = copy.deepcopy(template.getroot())
            root.find("include").set("file", str(PANDA / "panda.xml"))
            root.find("compiler").set("meshdir", str(PANDA / "assets"))
            # All added fixtures are top-level bodies; the included robot is untouched.
            for body in root.find("worldbody").findall("body"):
                position = [float(value) for value in body.get("pos", "0 0 0").split()]
                body.set("pos", " ".join(map(str, _shift(position, shift_y))))
            scene = destination / f"{task_id}_{condition}.xml"
            ET.indent(root)
            ET.ElementTree(root).write(scene, encoding="unicode")
            cases.append(_case(task_id, condition, shift_y, scene))
    return cases


def check_cases(cases: list[dict]) -> list[dict]:
    """Real MuJoCo scene check; no driver or model request is involved."""
    import mujoco
    import numpy as np
    sys.path.insert(0, str(ROOT / "AA1"))
    from auto_adapter.scene_runtime import apply_initial_state

    checked = []
    for case in cases:
        model = mujoco.MjModel.from_xml_path(case["scene_path"])
        data = mujoco.MjData(model)
        apply_initial_state(model, data, case)
        target = model.body("target_goal").id
        observed = model.body("hand" if case["task_id"] == "mw_reach_target" else "target_object").id
        initial_distance = float(np.linalg.norm(data.xpos[observed] - data.xpos[target]))
        for _ in range(10):
            mujoco.mj_step(model, data)
        distance = float(np.linalg.norm(data.xpos[observed] - data.xpos[target]))
        if not all(np.isfinite(array).all() for array in (data.qpos, data.qvel, data.ctrl)):
            raise ValueError(f"non-finite scene state: {case['id']}")
        if min(initial_distance, distance) <= case["success_spec"]["endpoint_tolerance_m"]:
            raise ValueError(f"task starts satisfied or settles into success: {case['id']}")
        checked.append({"case": case["id"], "nq": model.nq, "nv": model.nv,
                        "nu": model.nu, "initial_distance_m": initial_distance,
                        "distance_after_10_steps_m": distance, "finite": True})
    return checked


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    prepared = prepare_cases(arguments.output_dir)
    print(json.dumps(check_cases(prepared) if arguments.check else prepared, indent=2))
