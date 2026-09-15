"""Physical verdicts for the five Chapter 5 Franka catalog tasks.

Supplied by this experiment after the native DEMO trace has been validated.
Controller messages and method names are not evidence of task completion.
"""

from __future__ import annotations

import math


_TOLERANCES = {
    "mw_reach_target": 0.05,
    "mw_push_to_goal": 0.05,
    "mw_pick_place": 0.07,
    "mw_drawer_open": 0.03,
    "mw_dial_turn": 0.07,
}


def _position(sample, label):
    return sample["state"][label]["position"]


def _contact(sample, object_name, other_names):
    return any(object_name in pair and any(name in pair for name in other_names)
               for pair in sample["contacts"])


def _metric(name, value, threshold, ok):
    return {"check": name, "value": value, "threshold": threshold, "ok": bool(ok)}


def _positive(spec, name, default):
    value = float(spec.get(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return value


def _unit_axis(values):
    if len(values) != 3 or not all(math.isfinite(float(value)) for value in values):
        raise ValueError("fixture axis must contain three finite values")
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        raise ValueError("fixture axis must be nonzero")
    return [value / norm for value in values]


def evaluate_franka_task(spec, parameters, samples, times):
    """Return physical metrics using the existing DEMO-family interface."""
    task_id = spec["task_id"]
    if task_id not in _TOLERANCES:
        raise ValueError(f"unknown Franka catalog task {task_id!r}")
    tolerance = _TOLERANCES[task_id]
    if float(spec.get("endpoint_tolerance_m", tolerance)) != tolerance:
        raise ValueError("endpoint_tolerance_m must retain the catalog threshold")
    minimum_motion = _positive(spec, "minimum_motion_m", 0.01)
    max_duration = _positive(spec, "max_duration_s", 30.0)
    label = "ee" if task_id == "mw_reach_target" else "object"
    positions = [_position(sample, label) for sample in samples]
    target = _position(samples[-1], "target")
    initial_distance = math.dist(positions[0], _position(samples[0], "target"))
    final_distance = math.dist(positions[-1], target)
    duration = times[-1] - times[0]
    metrics = [
        _metric("initial_outside_goal_m", initial_distance, tolerance, initial_distance > tolerance),
        _metric("terminal_target_distance_m", final_distance, tolerance, final_distance <= tolerance),
        _metric("task_duration_s", duration, max_duration, 0 < duration <= max_duration),
    ]
    if task_id == "mw_reach_target":
        motion = math.dist(positions[0], positions[-1])
        metrics.append(_metric("end_effector_motion_m", motion, minimum_motion, motion >= minimum_motion))
        return metrics

    object_name = spec.get("object_body_name", "target_object")
    tool_names = spec.get("contact_tool_body_names", ["hand", "left_finger", "right_finger"])
    contacts = [_contact(sample, object_name, tool_names) for sample in samples]
    contact_index = next((i for i, seen in enumerate(contacts) if seen), None)
    contact_motion = 0.0
    if contact_index is not None:
        # Include displacement in the first contact sample, but never credit
        # object motion completed before the robot touched it.
        anchor = positions[max(0, contact_index - 1)]
        contact_motion = max(math.dist(anchor, point) for point in positions[contact_index:])
    metrics.extend([
        _metric("object_tool_contact", contact_index is not None, True, contact_index is not None),
        _metric("object_motion_after_contact_m", contact_motion, minimum_motion,
                contact_index is not None and contact_motion >= minimum_motion),
    ])

    if task_id in {"mw_drawer_open", "mw_dial_turn"}:
        joints = [sample["state"]["fixture_joint"]["value"] for sample in samples]
        joint_delta = joints[-1] - joints[0]
        unit = "m" if task_id == "mw_drawer_open" else "rad"
        joint_minimum = 0.01
        metrics.append(_metric(f"fixture_joint_motion_{unit}", abs(joint_delta), joint_minimum,
                               abs(joint_delta) >= joint_minimum))
        if task_id == "mw_drawer_open":
            axis = _unit_axis(spec["drawer_slide_axis_world"])
            expected_position = [positions[0][i] + joint_delta * axis[i] for i in range(3)]
        else:
            axis = _unit_axis(spec["dial_axis_world"])
            pivot = spec["dial_pivot_world_m"]
            radius = [positions[0][i] - pivot[i] for i in range(3)]
            dot = sum(axis[i] * radius[i] for i in range(3))
            cross = [axis[1] * radius[2] - axis[2] * radius[1],
                     axis[2] * radius[0] - axis[0] * radius[2],
                     axis[0] * radius[1] - axis[1] * radius[0]]
            cosine, sine = math.cos(joint_delta), math.sin(joint_delta)
            expected_position = [pivot[i] + radius[i] * cosine + cross[i] * sine
                                 + axis[i] * dot * (1 - cosine) for i in range(3)]
        consistency_error = math.dist(positions[-1], expected_position)
        metrics.append(_metric("fixture_object_motion_consistency_m", consistency_error, 0.001,
                               consistency_error <= 0.001))

    if task_id == "mw_pick_place":
        lift_height = max(0.03, _positive(spec, "minimum_lift_m", 0.03))
        release_hold = max(0.2, _positive(spec, "release_hold_s", 0.2))
        support_names = spec.get("support_body_names", ["source_table", "destination"])
        supported = [_contact(sample, object_name, support_names) for sample in samples]
        grasped = [all(_contact(sample, object_name, [finger])
                       for finger in ("left_finger", "right_finger")) for sample in samples]
        grasp_index = next((i for i, seen in enumerate(grasped) if seen), None)
        lift_index = None
        held_motion = 0.0
        if grasp_index is not None:
            for i in range(grasp_index + 1, len(samples)):
                if grasped[i] and not supported[i]:
                    held_motion = max(held_motion, math.dist(positions[grasp_index], positions[i]))
                    if positions[i][2] - positions[0][2] >= lift_height:
                        lift_index = i if lift_index is None else lift_index
        carry_index = None if lift_index is None else next(
            (i for i in range(lift_index, len(samples))
             if grasped[i] and math.dist(positions[i], target) <= tolerance), None)
        metrics.extend([
            _metric("object_initially_supported", supported[0], True, supported[0]),
            _metric("bilateral_finger_contact", grasp_index is not None, True, grasp_index is not None),
            _metric("grasped_object_lift_m", None if lift_index is None else positions[lift_index][2] - positions[0][2],
                    lift_height, lift_index is not None),
            _metric("grasped_object_transport_m", held_motion, minimum_motion, held_motion >= minimum_motion),
            _metric("object_carried_to_goal", carry_index is not None, True, carry_index is not None),
        ])
        # A terminal suffix must show a released, supported object in the goal.
        # A fly-through, a still-held object, or a single contact sample fails.
        release_start = len(samples)
        for i in range(len(samples) - 1, -1, -1):
            if contacts[i] or not supported[i] or math.dist(positions[i], target) > tolerance:
                break
            release_start = i
        held_for = 0.0 if release_start == len(samples) else times[-1] - times[release_start]
        released_after_carry = carry_index is not None and release_start > carry_index
        metrics.append(_metric("released_supported_goal_hold_s", held_for, release_hold,
                               released_after_carry and held_for + 1e-9 >= release_hold))
    return metrics


__all__ = ["evaluate_franka_task"]
