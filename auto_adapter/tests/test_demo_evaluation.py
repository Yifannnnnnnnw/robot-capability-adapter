"""Focused unit checks for the fixed DEMO physical verdicts.

These fixtures exercise the evaluator contract with short native-trace-shaped
records; they do not stand in for a real MuJoCo run.
"""

from __future__ import annotations

from pathlib import Path
import copy
import math

import pytest
import yaml

from auto_adapter.demo_evaluation import evaluate_demo_task


ROOT = Path(__file__).resolve().parents[2]
DEMOS = yaml.safe_load((ROOT / "auto_adapter/demo_tasks.yaml").read_text())["robots"]


IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]


def _body(position, *, velocity=(0.0, 0.0, 0.0), angular=(0.0, 0.0, 0.0)):
    return {
        "position": list(position),
        "rotation": list(IDENTITY),
        "linear_velocity": list(velocity),
        "angular_velocity": list(angular),
    }


def _joint(value):
    return {"value": float(value)}


def _sample(time, state, contacts=()):
    return {"time": float(time), "state": state, "contacts": [list(pair) for pair in contacts]}


def _result(spec, parameters, samples):
    return evaluate_demo_task(spec, parameters, samples)


def _piper_spec():
    return {
        "type": "arm_waypoints_gripper",
        "bindings": {
            "ee": {"kind": "site", "name": "ee_site"},
            "gripper_left": {"kind": "body", "name": "link7"},
            "gripper_right": {"kind": "body", "name": "link8"},
        },
        "waypoint_tolerance_m": 0.01,
        "waypoint_motion_m": 0.003,
        "opening_gap_closed_m": 0.0,
        "opening_gap_open_m": 0.07,
        "opening_gap_tolerance_m": 0.003,
        "opening_gap_motion_m": 0.003,
        "opening_gap_labels": ["gripper_left", "gripper_right"],
    }


def _piper_parameters():
    return {
        "waypoints_world_m": [[0.10, 0.0, 0.2], [0.11, 0.0, 0.19]],
        "max_duration_per_segment_s": 1.0,
        "closed_opening_fraction": 0.2,
        "open_opening_fraction": 0.8,
        "max_gripper_duration_s": 1.0,
    }


def _piper_samples():
    p = _piper_parameters()["waypoints_world_m"]
    return [
        _sample(0.0, {"ee": _body([0.0, 0.0, 0.0]), "gripper_left": _body([0, 0, 0]), "gripper_right": _body([0, 0, 0])}),
        _sample(0.5, {"ee": _body(p[0]), "gripper_left": _body([0, 0, 0]), "gripper_right": _body([0.014, 0, 0])}),
        _sample(1.0, {"ee": _body(p[1]), "gripper_left": _body([0, 0, 0]), "gripper_right": _body([0, 0, 0])}),
        _sample(1.5, {"ee": _body(p[1]), "gripper_left": _body([0, 0, 0]), "gripper_right": _body([0.014, 0, 0])}),
        _sample(2.0, {"ee": _body(p[1]), "gripper_left": _body([0, 0, 0]), "gripper_right": _body([0.056, 0, 0])}),
    ]


def test_all_22_configs_have_public_physical_success_specs():
    assert len(DEMOS) == 22
    assert all(isinstance(config.get("success"), dict) for config in DEMOS.values())
    assert all(config["success"].get("bindings") for config in DEMOS.values())


def test_arm_waypoint_and_physical_opening_positive_and_no_motion_negative():
    spec = _piper_spec()
    params = _piper_parameters()
    result = _result(spec, params, _piper_samples())
    assert result["physical_task_success"] is True
    assert result["evaluation_error"] is None
    assert any(metric["check"] == "opening_2_gap_m" for metric in result["task_metrics"])

    no_motion = copy.deepcopy(_piper_samples())
    for sample in no_motion:
        sample["state"]["ee"] = _body(params["waypoints_world_m"][0])
    assert _result(spec, params, no_motion)["physical_task_success"] is False


def test_arm_waypoint_reversed_order_fails_and_missing_label_is_unscorable():
    spec = _piper_spec()
    params = _piper_parameters()
    samples = _piper_samples()
    # The second checkpoint is visited, then the first, with no later return to the second.
    reversed_samples = [copy.deepcopy(sample) for sample in [samples[0], samples[2], samples[1], samples[3], samples[4]]]
    for index, sample in enumerate(reversed_samples):
        sample["time"] = index * 0.5
    assert _result(spec, params, reversed_samples)["physical_task_success"] is False
    missing = _piper_samples()
    del missing[2]["state"]["gripper_right"]
    result = _result(spec, params, missing)
    assert result["physical_task_success"] is None
    assert result["evaluation_error"]


def test_offset_return_family_uses_base_frame_and_order():
    spec = {
        "type": "arm_waypoints_offset_return",
        "bindings": {
            "ee": {"kind": "site", "name": "attachment_site"},
            "base": {"kind": "body", "name": "base"},
        },
        "waypoint_tolerance_m": 0.01,
    }
    params = {
        "waypoints_world_m": [[0.1, 0, 0], [0.12, 0, 0]],
        "max_duration_per_segment_s": 1.0,
        "offset_robot_base_m": [0.02, 0, 0],
        "max_duration_per_leg_s": 1.0,
    }
    base = _body([0, 0, 0])
    samples = [
        _sample(0, {"ee": _body([0, 0, 0]), "base": base}),
        _sample(0.5, {"ee": _body([0.1, 0, 0]), "base": base}),
        _sample(1.0, {"ee": _body([0.12, 0, 0]), "base": base}),
        _sample(1.5, {"ee": _body([0.14, 0, 0]), "base": base}),
        _sample(2.0, {"ee": _body([0.12, 0, 0]), "base": base}),
    ]
    assert _result(spec, params, samples)["physical_task_success"] is True

    static_offset = [
        _sample(0, {"ee": _body([0, 0, 0]), "base": base}),
        _sample(.5, {"ee": _body([.1, 0, 0]), "base": base}),
        _sample(1.0, {"ee": _body([.12, 0, 0]), "base": base}),
        _sample(1.5, {"ee": _body([.12, 0, 0]), "base": base}),
    ]
    assert _result(spec, params, static_offset)["physical_task_success"] is False


def test_quadruped_family_requires_stance_walk_height_order():
    spec = {
        "type": "quadruped_stance_walk_height",
        "bindings": {"base": {"kind": "body", "name": "base_link"}},
        "walk_position_tolerance_m": 0.01,
        "walk_lateral_tolerance_m": 0.01,
        "yaw_tolerance_rad": 0.1,
        "height_tolerance_m": 0.01,
        "upright_tolerance_rad": 0.15,
    }
    params = {
        "initial_stand_s": 0.5,
        "translation_initial_yaw_m": [0.1, 0.0],
        "yaw_delta_rad": 0.0,
        "max_walk_duration_s": 0.7,
        "post_walk_stand_s": 0.5,
        "crouch_height_m": 0.24,
        "standing_height_m": 0.32,
        "max_height_change_duration_s": 0.7,
        "final_stand_s": 0.5,
    }
    def s(t, z, x=0.0):
        return _sample(t, {"base": _body([x, 0, z])})
    samples = [s(0, .3), s(.5, .3), s(.6, .3, .1), s(1.1, .3, .1), s(1.2, .24, .1), s(1.7, .32, .1), s(2.2, .32, .1)]
    assert _result(spec, params, samples)["physical_task_success"] is True
    bad = list(samples)
    bad[4] = s(1.2, .30, .1)
    assert _result(spec, params, bad)["physical_task_success"] is False


def test_hand_fingertip_roundtrip_requires_return_and_palm_invariant():
    spec = {
        "type": "hand_fingertips_roundtrip",
        "bindings": {label: {"kind": "geom" if label != "palm" else "body", "name": label} for label in ["index", "middle", "ring", "thumb", "palm"]},
        "target_tolerance_m": 0.01,
        "fingertip_motion_m": 0.003,
        "palm_position_tolerance_m": 0.01,
    }
    labels = ["index", "middle", "ring", "thumb"]
    initial = {label: [0.0, i * 0.02, 0.1] for i, label in enumerate(labels)}
    targets = {label: [0.05, i * 0.02, 0.12] for i, label in enumerate(labels)}
    def state(points):
        return {**{label: _body(points[label]) for label in labels}, "palm": _body([0, 0, 0])}
    samples = [_sample(0, state(initial)), _sample(.5, state(targets)), _sample(1.0, state(initial))]
    params = {"fingertip_targets_world_m": targets, "max_duration_per_motion_s": 1.0}
    assert _result(spec, params, samples)["physical_task_success"] is True
    samples[-1]["state"]["index"] = _body(targets["index"])
    assert _result(spec, params, samples)["physical_task_success"] is False


def test_stretch_bimanual_and_aerial_families_cover_holds():
    stretch_spec = {
        "type": "stretch_drive_reach",
        "bindings": {"base": {"kind": "body", "name": "base"}, "ee": {"kind": "body", "name": "ee"}},
        "base_target_tolerance_m": .01, "base_fixed_tolerance_m": .01, "ee_target_tolerance_m": .01,
        "drive_speed_tolerance_m_s": .05,
    }
    stretch_params = {"forward_distance_m": .1, "drive_speed_m_s": .1, "max_drive_duration_s": 1.5,
                      "end_effector_target_world_m": [.2, 0, 0], "max_reach_duration_s": 1.0}
    stretch_samples = [
        _sample(0, {"base": _body([0, 0, 0], velocity=(.1, 0, 0)), "ee": _body([0, 0, 0])}),
        _sample(.5, {"base": _body([.05, 0, 0], velocity=(.1, 0, 0)), "ee": _body([0, 0, 0])}),
        _sample(1.0, {"base": _body([.1, 0, 0]), "ee": _body([0, 0, 0])}),
        _sample(1.5, {"base": _body([.1, 0, 0]), "ee": _body([.2, 0, 0])}),
    ]
    assert _result(stretch_spec, stretch_params, stretch_samples)["physical_task_success"] is True

    bimanual_spec = {
        "type": "bimanual_ordered_reach",
        "bindings": {"left_ee": {"kind": "site", "name": "left"}, "right_ee": {"kind": "site", "name": "right"}},
        "target_tolerance_m": .01, "other_arm_tolerance_m": .01,
    }
    bimanual_params = {"left_target_world_m": [.1, 0, 0], "right_target_world_m": [-.1, 0, 0],
                       "max_duration_per_arm_s": 1.0, "hold_duration_s": .5}
    def bs(t, left, right):
        return _sample(t, {"left_ee": _body(left), "right_ee": _body(right)})
    bimanual_samples = [bs(0, [0, 0, 0], [0, 0, 0]), bs(.5, [.1, 0, 0], [0, 0, 0]),
                        bs(1.0, [.1, 0, 0], [-.1, 0, 0]), bs(1.5, [.1, 0, 0], [-.1, 0, 0])]
    assert _result(bimanual_spec, bimanual_params, bimanual_samples)["physical_task_success"] is True

    aerial_spec = {"type": "aerial_takeoff_hover", "bindings": {"base": {"kind": "body", "name": "x2"}},
                   "altitude_tolerance_m": .01, "hover_position_tolerance_m": .01, "hover_linear_speed_m_s": .1,
                   "hover_angular_speed_rad_s": .2, "upright_tolerance_rad": .15}
    aerial_params = {"target_altitude_world_m": .5, "max_takeoff_duration_s": 1.0, "hover_duration_s": .5}
    aerial_samples = [_sample(0, {"base": _body([0, 0, .1])}), _sample(.5, {"base": _body([0, 0, .5])}),
                      _sample(1.0, {"base": _body([0, 0, .5])})]
    assert _result(aerial_spec, aerial_params, aerial_samples)["physical_task_success"] is True


def test_squat_push_and_joint_roundtrip_families_reject_missing_physics():
    squat_spec = {"type": "humanoid_squat_recover", "bindings": {"base": {"kind": "body", "name": "pelvis"}},
                  "height_tolerance_m": .01, "upright_tolerance_rad": .15}
    squat_params = {"initial_stand_s": .5, "squat_depth_m": .1, "squat_and_recovery_duration_s": 1.0, "final_stand_s": .5}
    squat_samples = [_sample(0, {"base": _body([0, 0, .9])}), _sample(.5, {"base": _body([0, 0, .9])}),
                     _sample(.6, {"base": _body([0, 0, .8])}), _sample(1.0, {"base": _body([0, 0, .9])}),
                     _sample(1.5, {"base": _body([0, 0, .9])})]
    assert _result(squat_spec, squat_params, squat_samples)["physical_task_success"] is True

    push_spec = {"type": "push_contact_displacement",
                 "bindings": {"ee": {"kind": "site", "name": "ee"}, "tee": {"kind": "body", "name": "tee"}},
                 "contact_tool_body_names": ["tool"], "object_position_tolerance_m": .01,
                 "contact_position_tolerance_m": .02, "minimum_push_motion_m": .01}
    push_params = {"object_initial_position_world_m": [0, 0, .1], "push_delta_world_m": [.04, 0, 0], "max_duration_s": 2.0}
    push_samples = [
        _sample(0, {"ee": _body([.2, 0, 0]), "tee": _body([0, 0, .1])}),
        _sample(.5, {"ee": _body([0, 0, .1]), "tee": _body([0, 0, .1])}, [("tool", "tee")]),
        _sample(1.0, {"ee": _body([.04, 0, .1]), "tee": _body([.04, 0, .1])}, [("tool", "tee")]),
    ]
    assert _result(push_spec, push_params, push_samples)["physical_task_success"] is True
    no_contact = copy.deepcopy(push_samples)
    no_contact[1]["contacts"] = []
    no_contact[2]["contacts"] = []
    assert _result(push_spec, push_params, no_contact)["physical_task_success"] is False

    joint_spec = {"type": "joint_ordered_roundtrip",
                  "bindings": {"left": {"kind": "joint", "name": "left"}, "right": {"kind": "joint", "name": "right"}},
                  "joint_order": ["left", "right"], "deadline_parameter": "max_duration_per_joint_s",
                  "joint_tolerance_rad": .02, "joint_motion_rad": .01}
    joint_params = {"joint_targets_rad": {"left": 1.0, "right": 1.2}, "max_duration_per_joint_s": .75}
    joint_samples = [_sample(0, {"left": _joint(0), "right": _joint(0)}),
                     _sample(.5, {"left": _joint(1.0), "right": _joint(0)}),
                     _sample(1.0, {"left": _joint(1.0), "right": _joint(1.2)}),
                     _sample(1.5, {"left": _joint(0), "right": _joint(1.2)}),
                     _sample(2.0, {"left": _joint(0), "right": _joint(0)})]
    assert _result(joint_spec, joint_params, joint_samples)["physical_task_success"] is True
    missing_physics = copy.deepcopy(joint_samples)
    del missing_physics[2]["state"]["right"]
    assert _result(joint_spec, joint_params, missing_physics)["physical_task_success"] is None


def test_short_offset_does_not_pass_two_centimetre_motion():
    spec = {
        "type": "arm_waypoints_offset_return",
        "bindings": {"ee": {"kind": "site", "name": "ee"},
                     "base": {"kind": "body", "name": "base"}},
        "waypoint_tolerance_m": .02, "offset_tolerance_m": .005,
    }
    params = {"waypoints_world_m": [[.1, 0, 0], [.2, 0, 0]],
              "offset_robot_base_m": [.02, 0, 0],
              "max_duration_per_segment_s": 1.0, "max_duration_per_leg_s": 1.0}
    rows = [_sample(t, {"ee": _body([x, 0, 0]), "base": _body([0, 0, 0])})
            for t, x in [(0, 0), (.5, .1), (1, .2), (1.5, .206), (2, .2)]]
    assert _result(spec, params, rows)["physical_task_success"] is False


def test_custom_opening_fraction_changes_physical_target():
    spec = _piper_spec()
    params = _piper_parameters()
    samples = _piper_samples()
    params["closed_opening_fraction"] = 0.3
    result = _result(spec, params, samples)
    assert result["physical_task_success"] is False
    opening = [m for m in result["task_metrics"] if m["check"] == "opening_1_gap_m"]
    assert opening and math.isclose(opening[0]["target_m"], .021, rel_tol=0, abs_tol=1e-9)


def test_gripper_deadline_starts_after_first_target_dwell():
    spec = _piper_spec()
    params = _piper_parameters()
    params["max_gripper_duration_s"] = 1.0
    p = params["waypoints_world_m"]

    def s(t, gap):
        return _sample(t, {
            "ee": _body(p[1] if t >= 1.0 else (p[0] if t >= 0.5 else [0, 0, 0])),
            "gripper_left": _body([0, 0, 0]),
            "gripper_right": _body([gap, 0, 0]),
        })

    samples = [s(0.0, 0.0), s(0.5, 0.0), s(1.0, 0.0), s(1.2, 0.014), s(2.0, 0.014), s(2.9, 0.056)]
    assert _result(spec, params, samples)["physical_task_success"] is True

    samples[-1] = s(3.1, 0.056)
    assert _result(spec, params, samples)["physical_task_success"] is False


def test_push_rejects_actual_obstacle_contact_during_withdrawal():
    config = DEMOS["so101_push"]
    spec = copy.deepcopy(config["success"])
    params = copy.deepcopy(config["parameters"])
    initial = params["object_initial_position_world_m"]
    target = [initial[0] + params["push_delta_world_m"][0], initial[1], initial[2]]

    def state(ee, tee):
        return {
            "ee": _body(ee),
            "tee": _body(tee),
            "obstacle": _body([0.25, 0.0, 0.025]),
            "cube_red": _body([0.1, 0.1, 0.025]),
            "cube_green": _body([0.18, 0.12, 0.025]),
            "cube_blue": _body([0.14, 0.18, 0.025]),
        }

    samples = [
        _sample(0.0, state([0.3, -0.05, 0.025], initial)),
        _sample(0.5, state(initial, initial), [("gripper_link", "tee")]),
        _sample(1.0, state(target, target), [("gripper_link", "tee")]),
        _sample(1.5, state([0.30, -0.05, 0.08], target), [("gripper_link", "obs")]),
    ]
    result = _result(spec, params, samples)
    assert result["physical_task_success"] is False
    obstacle_check = next(metric for metric in result["task_metrics"] if metric["check"] == "no_tool_obstacle_contact")
    assert obstacle_check["ok"] is False


def test_hand_requires_common_target_sample_and_fixed_palm_orientation():
    spec = {
        "type": "hand_fingertips_roundtrip",
        "bindings": {label: {"kind": "geom" if label != "palm" else "body", "name": label} for label in ["index", "middle", "ring", "thumb", "palm"]},
        "target_tolerance_m": 0.01,
        "fingertip_motion_m": 0.003,
        "palm_position_tolerance_m": 0.01,
    }
    labels = ["index", "middle", "ring", "thumb"]
    initial = {label: [0.0, i * 0.02, 0.1] for i, label in enumerate(labels)}
    targets = {label: [0.05, i * 0.02, 0.12] for i, label in enumerate(labels)}

    def state(points, palm_rotation=IDENTITY):
        return {
            **{label: _body(points[label]) for label in labels},
            "palm": {**_body([0, 0, 0]), "rotation": list(palm_rotation)},
        }

    params = {"fingertip_targets_world_m": targets, "max_duration_per_motion_s": 1.0}
    staggered = [
        _sample(0.0, state(initial)),
        _sample(0.5, state({"index": targets["index"], **{label: initial[label] for label in labels[1:]}})),
        _sample(1.0, state({"middle": targets["middle"], "ring": targets["ring"], "thumb": targets["thumb"], "index": initial["index"]})),
    ]
    assert _result(spec, params, staggered)["physical_task_success"] is False

    common = [
        _sample(0.0, state(initial)),
        _sample(0.5, state(targets)),
        _sample(1.0, state(initial, [0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0])),
    ]
    assert _result(spec, params, common)["physical_task_success"] is False


def test_humanoid_combined_motion_deadline_and_joint_base_pose_are_enforced():
    squat_spec = {"type": "humanoid_squat_recover", "bindings": {"base": {"kind": "body", "name": "pelvis"}},
                  "height_tolerance_m": .01, "upright_tolerance_rad": .15}
    squat_params = {"initial_stand_s": .5, "squat_depth_m": .1, "squat_and_recovery_duration_s": 1.0, "final_stand_s": .5}
    delayed = [_sample(0.0, {"base": _body([0, 0, .9])}), _sample(.5, {"base": _body([0, 0, .9])}),
               _sample(.8, {"base": _body([0, 0, .8])}), _sample(1.7, {"base": _body([0, 0, .9])}),
               _sample(2.2, {"base": _body([0, 0, .9])})]
    assert _result(squat_spec, squat_params, delayed)["physical_task_success"] is False

    joint_spec = {"type": "joint_ordered_roundtrip",
                  "bindings": {"left": {"kind": "joint", "name": "left"}, "right": {"kind": "joint", "name": "right"},
                                "base": {"kind": "body", "name": "base"}},
                  "joint_order": ["left", "right"], "return_joint_vector": True,
                  "deadline_parameter": "max_duration_per_joint_s", "joint_tolerance_rad": .02, "joint_motion_rad": .01,
                  "base_position_tolerance_m": .01, "base_rotation_tolerance_rad": .05}
    joint_params = {"joint_targets_rad": {"left": 1.0, "right": 1.2}, "max_duration_per_joint_s": .75}
    base_rotated = [
        _sample(0.0, {"left": _joint(0), "right": _joint(0), "base": _body([0, 0, 0])}),
        _sample(.5, {"left": _joint(1.0), "right": _joint(0), "base": _body([0, 0, 0])}),
        _sample(1.0, {"left": _joint(1.0), "right": _joint(1.2), "base": _body([0, 0, 0])}),
        _sample(1.5, {"left": _joint(0), "right": _joint(0), "base": _body([0, 0, 0])}),
    ]
    base_rotated[-1]["state"]["base"]["rotation"] = [0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    result = _result(joint_spec, joint_params, base_rotated)
    assert result["physical_task_success"] is False
    assert any(metric["check"] == "floating_base_rotation_drift_rad" and not metric["ok"] for metric in result["task_metrics"])
