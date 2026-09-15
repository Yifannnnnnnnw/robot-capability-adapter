"""Named synthetic trace fixtures; these are not MuJoCo experiment evidence."""

import copy
import math

from robots.franka.task_evaluation import evaluate_franka_task
from auto_adapter.demo_evaluation import evaluate_demo_task


def _sample(time, position, target, *, contacts=(), ee=(0, 0, 0.5), joint=0):
    return {
        "time": time,
        "state": {
            "ee": {"position": list(ee)},
            "object": {"position": list(position)},
            "target": {"position": list(target)},
            "left_finger": {"position": list(ee)},
            "right_finger": {"position": list(ee)},
            "fixture_joint": {"value": joint},
        },
        "contacts": [["target_object", name] for name in contacts],
    }


def _spec(task_id):
    return {
        "type": "franka_catalog_task",
        "task_id": task_id,
        "bindings": {
            "ee": {"kind": "body", "name": "hand"},
            "object": {"kind": "body", "name": "target_object"},
            "target": {"kind": "body", "name": "target_goal"},
            "left_finger": {"kind": "body", "name": "left_finger"},
            "right_finger": {"kind": "body", "name": "right_finger"},
            "fixture_joint": {"kind": "joint", "name": "fixture_joint"},
        },
        "contact_tool_body_names": ["hand", "left_finger", "right_finger"],
        "object_body_name": "target_object",
        "minimum_motion_m": 0.01,
        "max_duration_s": 30,
        "minimum_lift_m": 0.03,
        "release_hold_s": 0.2,
        "support_body_names": ["source_table", "destination"],
        "drawer_slide_axis_world": [-1, 0, 0],
        "dial_axis_world": [0, 0, 1],
        "dial_pivot_world_m": [0.53, 0, 0.37],
    }


def _synthetic_success(task_id):
    if task_id == "mw_reach_target":
        target = [0.2, 0, 0.3]
        return [_sample(0, [0, 0, 0], target, ee=[0, 0, 0.3]),
                _sample(1, [0, 0, 0], target, ee=target)]
    if task_id == "mw_push_to_goal":
        target = [0.2, 0, 0.1]
        return [_sample(0, [0, 0, 0.1], target, contacts=["source_table"]),
                _sample(0.2, [0.02, 0, 0.1], target, contacts=["hand", "source_table"]),
                _sample(0.6, target, target, contacts=["hand", "source_table"]),
                _sample(1, target, target, contacts=["source_table"])]
    if task_id == "mw_drawer_open":
        target = [0.40, 0, 0.39]
        return [_sample(0, [0.56, 0, 0.39], target),
                _sample(0.2, [0.54, 0, 0.39], target, contacts=["left_finger"], joint=0.02),
                _sample(0.6, target, target, contacts=["left_finger"], joint=0.16),
                _sample(1, target, target, joint=0.16)]
    if task_id == "mw_dial_turn":
        target = [0.53, 0.11, 0.37]
        halfway = [0.53 + 0.11 / math.sqrt(2), 0.11 / math.sqrt(2), 0.37]
        return [_sample(0, [0.64, 0, 0.37], target),
                _sample(0.2, halfway, target, contacts=["hand"], joint=math.pi / 4),
                _sample(0.6, target, target, contacts=["hand"], joint=math.pi / 2),
                _sample(1, target, target, joint=math.pi / 2)]
    start, target = [0.45, -0.09, 0.325], [0.60, 0.09, 0.405]
    fingers = ["left_finger", "right_finger"]
    return [_sample(0, start, target, contacts=["source_table"]),
            _sample(0.2, start, target, contacts=fingers + ["source_table"]),
            _sample(0.4, [0.45, -0.09, 0.39], target, contacts=fingers),
            _sample(0.8, target, target, contacts=fingers + ["destination"]),
            _sample(1.0, target, target, contacts=["destination"]),
            _sample(1.3, target, target, contacts=["destination"])]


def _metrics(task_id, samples):
    result = evaluate_demo_task(_spec(task_id), {}, samples, evaluator=evaluate_franka_task)
    assert result["evaluation_error"] is None, result
    return result["task_metrics"]


def _passes(task_id, samples):
    return all(metric["ok"] for metric in _metrics(task_id, samples))


TASKS = ("mw_reach_target", "mw_push_to_goal", "mw_pick_place", "mw_drawer_open", "mw_dial_turn")


def test_synthetic_physical_success_for_each_selected_catalog_task():
    for task_id in TASKS:
        samples = _synthetic_success(task_id)
        assert _passes(task_id, samples), (task_id, _metrics(task_id, samples))


def test_experiment_scoring_keeps_shared_trace_checks_and_needs_explicit_evaluator():
    samples = _synthetic_success("mw_reach_target")
    spec = _spec("mw_reach_target")
    unregistered = evaluate_demo_task(spec, {}, samples)
    assert unregistered["physical_task_success"] is None
    assert "unknown success family" in unregistered["evaluation_error"]
    del samples[-1]["state"]["ee"]
    missing = evaluate_demo_task(spec, {}, samples, evaluator=evaluate_franka_task)
    assert missing["physical_task_success"] is None
    assert "missing state label" in missing["evaluation_error"]


def test_static_goal_and_only_end_effector_arrival_do_not_pass_object_tasks():
    for task_id in TASKS:
        static_goal = _synthetic_success(task_id)
        label = "ee" if task_id == "mw_reach_target" else "object"
        for sample in static_goal:
            sample["state"][label]["position"] = list(sample["state"]["target"]["position"])
        assert not _passes(task_id, static_goal), task_id
        if task_id != "mw_reach_target":
            ee_only = _synthetic_success(task_id)
            initial_object = list(ee_only[0]["state"]["object"]["position"])
            for sample in ee_only:
                sample["state"]["object"]["position"] = list(initial_object)
                sample["state"]["ee"]["position"] = list(sample["state"]["target"]["position"])
            assert not _passes(task_id, ee_only), task_id


def test_object_motion_before_contact_is_not_a_contact_driven_push():
    samples = _synthetic_success("mw_push_to_goal")
    samples[1]["contacts"] = []
    samples[1]["state"]["object"]["position"] = list(samples[-1]["state"]["object"]["position"])
    assert not _passes("mw_push_to_goal", samples)


def test_pick_place_requires_bilateral_lift_and_a_released_supported_terminal_hold():
    good = _synthetic_success("mw_pick_place")
    one_finger = copy.deepcopy(good)
    for sample in one_finger:
        sample["contacts"] = [pair for pair in sample["contacts"] if "right_finger" not in pair]
    assert not _passes("mw_pick_place", one_finger)

    slide = copy.deepcopy(good)
    for sample in slide:
        sample["contacts"].append(["target_object", "source_table"])
    assert not _passes("mw_pick_place", slide)

    thrown = copy.deepcopy(good)
    thrown[3]["contacts"] = [["target_object", "destination"]]
    assert not _passes("mw_pick_place", thrown)

    still_held = copy.deepcopy(good)
    still_held[-1]["contacts"].append(["target_object", "left_finger"])
    assert not _passes("mw_pick_place", still_held)
    unsupported = copy.deepcopy(good)
    unsupported[-1]["contacts"] = []
    assert not _passes("mw_pick_place", unsupported)
    too_short = copy.deepcopy(good)
    too_short[-1]["time"] = 1.1
    assert not _passes("mw_pick_place", too_short)


def test_fixture_joint_must_move_consistently_with_the_manipulated_body():
    for task_id in ("mw_drawer_open", "mw_dial_turn"):
        samples = _synthetic_success(task_id)
        for sample in samples:
            sample["state"]["fixture_joint"]["value"] = 0
        assert not _passes(task_id, samples), task_id
        reversed_joint = _synthetic_success(task_id)
        for sample in reversed_joint:
            sample["state"]["fixture_joint"]["value"] *= -1
        assert not _passes(task_id, reversed_joint), task_id
