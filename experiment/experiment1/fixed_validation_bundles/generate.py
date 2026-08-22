#!/usr/bin/env python3
"""Generate the prospectively fixed, reviewable Experiment 1 B1 JSON inputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = Path(__file__).resolve().parent
ROBOT_ROOT = REPOSITORY_ROOT / "autoadapter" / "libraries" / "robots"
CRITERIA_SOURCE = "experiment/experiment1/B1_DRIVER_VALIDATION_CRITERIA.md"

CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "robotstudio_so101": (
        ("A1", "move_end_effector_to_position"),
        ("A2", "trace_cartesian_path"),
        ("A3", "set_gripper_opening"),
        ("A4", "approach_until_contact"),
        ("A5", "move_cartesian_offset_and_return"),
    ),
    "unitree-go2-stock-12dof": (
        ("G1", "track_planar_twist"),
        ("G2", "move_body_relative_pose"),
        ("G3", "trace_planar_path"),
        ("G4", "set_body_height"),
        ("G5", "hold_stable_stance"),
    ),
    "leap_hand": (
        ("L1", "move_hand_to_joint_pose"),
        ("L2", "trace_hand_joint_path"),
        ("L3", "reach_fingertips_to_targets"),
        ("L4", "establish_fingertip_contact_pattern"),
        ("L5", "hold_fingertip_contacts"),
        ("L6", "move_fingertip_offset_and_return"),
    ),
    "aloha_2": (
        ("AL1", "move_arm_to_position"),
        ("AL2", "trace_arm_cartesian_path"),
        ("AL3", "set_gripper_opening"),
        ("AL4", "move_bimanual_to_positions"),
        ("AL5", "approach_until_contact"),
        ("AL6", "move_bimanual_offsets_and_return"),
    ),
}

DESCRIPTIONS = {
    "A1": "Move the SO-101 end effector to one bounded world-frame position.",
    "A2": "Trace bounded SO-101 world-frame Cartesian waypoints in order.",
    "A3": "Set the SO-101 physical gripper aperture by normalised opening fraction.",
    "A4": "Approach a reachable target along a bounded ray until controlled contact.",
    "A5": "Move the SO-101 end effector by a base-frame offset and return.",
    "G1": "Track a bounded planar body-frame twist for a fixed duration.",
    "G2": "Move the Go2 base to a pose relative to its initial yaw frame.",
    "G3": "Trace bounded planar waypoints in the initial-yaw frame.",
    "G4": "Set Go2 body height while retaining a stable local stance.",
    "G5": "Recover from one Framework reset disturbance and hold stable stance.",
    "L1": "Move all sixteen LEAP joints to a bounded joint pose.",
    "L2": "Trace bounded sixteen-joint LEAP waypoints in order.",
    "L3": "Reach four palm-frame fingertip targets in fifty control steps.",
    "L4": "Establish a named multi-fingertip contact pattern by bounded ray approaches.",
    "L5": "Restore and hold named fingertip contacts after a registered separation.",
    "L6": "Move selected fingertips by palm-frame offsets and return.",
    "ST1": "Translate the Stretch base in its initial-yaw frame without yaw drift.",
    "ST2": "Turn the Stretch base to a bounded world yaw without planar drift.",
    "ST3": "Move the Stretch tool body to one bounded world-frame position.",
    "ST4": "Trace bounded Stretch tool world-frame waypoints in order.",
    "ST5": "Set the Stretch physical gripper slide by normalised opening fraction.",
    "ST6": "Set the Stretch wrist-yaw joint to a bounded absolute angle.",
    "ST7": "Approach a reachable target with the Stretch tool until contact.",
    "ST8": "Move the Stretch tool by a call-time-base offset and return.",
    "AL1": "Move one selected ALOHA arm to a bounded world-frame position.",
    "AL2": "Trace selected-arm world-frame Cartesian waypoints in order.",
    "AL3": "Set one selected ALOHA gripper by normalised opening fraction.",
    "AL4": "Move both ALOHA end effectors to bounded world-frame positions.",
    "AL5": "Approach a reachable target with one selected ALOHA arm until contact.",
    "AL6": "Move both ALOHA arms by base-frame offsets and return together.",
}

STANDARDS = {
    "A1": "Position error <= 0.015 m continuously for 0.5 s.",
    "A2": "Ordered waypoints and cross-track error <= 0.020 m; final error <= 0.015 m for 0.5 s.",
    "A3": "Normalised aperture error <= 0.10 for 0.25 s with bidirectional excursion >= 50% full travel.",
    "A4": "Precontact/ray/contact gates pass; contact lasts 0.1 s, post-contact speed <= 0.02 m/s, penetration <= 0.005 m, and no unrelated contact.",
    "A5": "Outbound and return errors <= 0.015 m in order, with 0.25 s/0.5 s holds and >= 80% requested displacement.",
    "G1": "Final-window mean planar velocity error <= 0.10 m/s and yaw-rate error <= 0.30 rad/s; direction error <= 10 degrees when moving.",
    "G2": "Terminal position error <= 0.10 m, yaw error <= 0.0873 rad, and speed <= 0.10 m/s for 0.5 s.",
    "G3": "Ordered waypoint error <= 0.10 m, cross-track <= 0.15 m, and endpoint error/speed <= 0.10 for 0.5 s.",
    "G4": "Height error <= 0.03 m with roll/pitch <= 0.1745 rad, displacement <= 0.05 m, and yaw drift <= 0.0873 rad for 0.5 s.",
    "G5": "Recover roll/pitch <= 0.0524 rad within 0.5 s, then satisfy the fixed stability, drift, support, and forbidden-contact gates.",
    "L1": "Maximum joint error < 0.1745329252 rad within 4 s and throughout a 0.5 s hold.",
    "L2": "Every joint waypoint reaches maximum error <= 0.05 rad within budget; final hold passes for 0.5 s.",
    "L3": "Concatenated four-fingertip Cartesian L2 error < 0.00894427191 m at control step 50.",
    "L4": "Every requested fingertip passes ray, target-region, 0.1 s contact, nonrequested-finger, speed, path, and penetration gates.",
    "L5": "Restore relative gap <= 0.002 m within 0.20 s with >= 90% contact occupancy and no continuous loss > 0.20 s.",
    "L6": "Selected fingertip outbound/return errors <= 0.010 m in order and held for 0.25 s; unselected-finger guards pass.",
    "ST1": "Position error, yaw drift, and planar speed each meet the public 0.020 bound for 0.5 s.",
    "ST2": "Yaw error, planar drift, and yaw rate each meet the public 0.020 bound for 0.5 s.",
    "ST3": "Tool-position error <= 0.025 m continuously for 0.5 s and side-effect guards pass.",
    "ST4": "Ordered waypoint/cross-track error <= 0.030 m and endpoint error <= 0.025 m for 0.5 s with side-effect guards.",
    "ST5": "Physical slide error <= 0.003 m for 0.25 s with hidden bidirectional excursion >= 0.015 m.",
    "ST6": "Absolute non-wrapped wrist-yaw error <= 0.030 rad for 0.5 s.",
    "ST7": "The full A4 precontact, ray, contact, speed, stop, unrelated-contact, and penetration gates apply.",
    "ST8": "Outbound/return tool errors <= 0.025 m in order with 0.25 s/0.5 s holds and base-pose guards.",
    "AL1": "Selected end-effector error <= 0.015 m for 0.5 s and all unselected-side guards pass.",
    "AL2": "Ordered waypoint/cross-track error <= 0.020 m and endpoint error <= 0.015 m for 0.5 s with unselected-side guards.",
    "AL3": "Selected aperture error <= 0.10 for 0.25 s, mirror disagreement <= 0.05, and unselected-side guards pass.",
    "AL4": "Both end-effector errors <= 0.015 m simultaneously for 0.5 s with first-entry skew <= 0.10 s.",
    "AL5": "The full A4 gates apply to the selected arm/contact group and all unselected-side guards pass.",
    "AL6": "Both arms complete outbound and return within budget with error <= 0.015 m and outbound skew <= 0.10 s; each phase holds 0.25 s.",
}


def number(
    *,
    unit: str,
    frame: str = "none",
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: float | None = None,
    description: str,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": "number",
        "unit": unit,
        "frame": frame,
        "description": description,
    }
    if minimum is not None:
        value["minimum"] = minimum
    if maximum is not None:
        value["maximum"] = maximum
    if exclusive_minimum is not None:
        value["exclusiveMinimum"] = exclusive_minimum
    return value


def vector(
    size: int,
    *,
    unit: str,
    frame: str,
    minimum: float,
    maximum: float,
    description: str,
) -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": size,
        "maxItems": size,
        "items": number(
            unit=unit,
            frame=frame,
            minimum=minimum,
            maximum=maximum,
            description="One finite vector component.",
        ),
        "unit": unit,
        "frame": frame,
        "description": description,
    }


def vectors(
    width: int,
    *,
    min_items: int,
    max_items: int,
    unit: str,
    frame: str,
    minimum: float,
    maximum: float,
    description: str,
) -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": min_items,
        "maxItems": max_items,
        "items": vector(
            width,
            unit=unit,
            frame=frame,
            minimum=minimum,
            maximum=maximum,
            description="One bounded waypoint or target vector.",
        ),
        "unit": unit,
        "frame": frame,
        "description": description,
    }


def closed(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


DURATION = lambda name="Maximum execution duration.": number(  # noqa: E731
    unit="s", minimum=0.25, maximum=8.0, description=name
)
SEGMENT_DURATION = lambda: number(  # noqa: E731
    unit="s", minimum=0.25, maximum=5.0, description="Maximum duration per ordered segment or leg."
)
OPENING = lambda: number(  # noqa: E731
    unit="ratio", minimum=0.0, maximum=1.0, description="Normalised physical gripper opening; 0 closed, 1 open."
)
ARM = {"type": "string", "enum": ["left", "right"], "description": "Selected ALOHA arm."}
FINGERS = ["index", "middle", "ring", "thumb"]
LEAP_FINGERTIP_SITES = {
    "index": "if_tip",
    "middle": "mf_tip",
    "ring": "rf_tip",
    "thumb": "th_tip",
}
LEAP_FINGERTIP_GEOMS = {
    finger: [site] for finger, site in LEAP_FINGERTIP_SITES.items()
}
LEAP_TARGET_BODIES = {
    finger: f"b1_{finger}_target_body" for finger in FINGERS
}
LEAP_TARGET_GEOMS = {
    finger: [f"b1_{finger}_target_geom"] for finger in FINGERS
}
LEAP_TARGET_SITES = {
    finger: f"b1_{finger}_target_site" for finger in FINGERS
}
LEAP_FINGER_JOINTS = {
    "index": ["if_mcp", "if_rot", "if_pip", "if_dip"],
    "middle": ["mf_mcp", "mf_rot", "mf_pip", "mf_dip"],
    "ring": ["rf_mcp", "rf_rot", "rf_pip", "rf_dip"],
    "thumb": ["th_cmc", "th_axl", "th_mcp", "th_ipl"],
}
FINGER_NAMES = {
    "type": "array",
    "minItems": 1,
    "maxItems": 4,
    "uniqueItems": True,
    "items": {"type": "string", "enum": FINGERS},
    "description": "Unique LEAP finger names in aligned array order.",
}

CONTACT_FIELDS = {
    "precontact_position_m": vector(3, unit="m", frame="world", minimum=-1.0, maximum=1.0, description="World-frame precontact tool point."),
    "approach_direction_unit": vector(3, unit="unitless", frame="world", minimum=-1.0, maximum=1.0, description="World-frame unit approach direction."),
    "max_travel_m": number(unit="m", exclusive_minimum=0.0, maximum=0.08, description="Maximum axial approach travel."),
    "max_approach_speed_m_s": number(unit="m/s", exclusive_minimum=0.0, maximum=0.05, description="Maximum approach speed."),
    "max_duration_s": DURATION(),
}

SCHEMAS = {
    "A1": closed({"target_position_m": vector(3, unit="m", frame="world", minimum=-1.0, maximum=1.0, description="World-frame SO-101 tool target."), "max_duration_s": DURATION()}),
    "A2": closed({"waypoints_m": vectors(3, min_items=2, max_items=8, unit="m", frame="world", minimum=-1.0, maximum=1.0, description="Ordered world-frame SO-101 tool waypoints."), "max_duration_per_segment_s": SEGMENT_DURATION()}),
    "A3": closed({"opening_fraction": OPENING(), "max_duration_s": DURATION()}),
    "A4": closed(dict(CONTACT_FIELDS)),
    "A5": closed({"offset_robot_base_m": vector(3, unit="m", frame="robot_base", minimum=-0.06, maximum=0.06, description="SO-101 base-frame tool offset."), "max_duration_per_leg_s": SEGMENT_DURATION()}),
    "G1": closed({"linear_velocity_body_m_s": vector(2, unit="m/s", frame="body", minimum=-0.4, maximum=0.4, description="Instantaneous body-yaw-frame planar velocity."), "yaw_rate_rad_s": number(unit="rad/s", minimum=-1.0, maximum=1.0, description="Requested yaw rate."), "duration_s": number(unit="s", minimum=2.0, maximum=4.0, description="Twist tracking duration.")}),
    "G2": closed({"translation_initial_yaw_m": vector(2, unit="m", frame="initial_body_yaw", minimum=-0.3, maximum=0.3, description="Planar displacement in the initial yaw frame."), "yaw_delta_rad": number(unit="rad", minimum=-0.6, maximum=0.6, description="Wrapped yaw change."), "max_duration_s": DURATION()}),
    "G3": closed({"waypoints_initial_yaw_m": vectors(2, min_items=2, max_items=8, unit="m", frame="initial_body_yaw", minimum=-0.4, maximum=0.4, description="Ordered planar waypoints in the initial yaw frame."), "max_duration_s": DURATION()}),
    "G4": closed({"target_height_m": number(unit="m", frame="world", minimum=0.22, maximum=0.36, description="Target base-body height."), "max_duration_s": DURATION()}),
    "G5": closed({"duration_s": number(unit="s", minimum=1.0, maximum=2.0, description="Post-disturbance stance-hold duration.")}),
    "L1": closed({"target_joint_positions_rad": vector(16, unit="rad", frame="joint", minimum=-0.3, maximum=1.5, description="LEAP joints in the package-declared order."), "max_duration_s": number(unit="s", minimum=0.5, maximum=4.0, description="Joint-pose execution duration.")}),
    "L2": closed({"joint_waypoints_rad": vectors(16, min_items=2, max_items=8, unit="rad", frame="joint", minimum=-0.3, maximum=1.5, description="Ordered LEAP joint waypoints."), "max_duration_per_segment_s": SEGMENT_DURATION()}),
    "L3": closed({"target_fingertip_positions_palm_m": vector(12, unit="m", frame="palm", minimum=-0.2, maximum=0.2, description="Index/middle/ring/thumb xyz targets in palm frame."), "max_control_steps": {"type": "integer", "const": 50, "unit": "control_step", "frame": "none", "description": "Fixed public control horizon."}}),
    "L4": closed({"required_fingers": FINGER_NAMES, "contact_target_positions_palm_m": vectors(3, min_items=1, max_items=4, unit="m", frame="palm", minimum=-0.2, maximum=0.2, description="Aligned contact targets."), "approach_directions_palm_unit": vectors(3, min_items=1, max_items=4, unit="unitless", frame="palm", minimum=-1.0, maximum=1.0, description="Aligned unit approach directions."), "max_travel_m": number(unit="m", exclusive_minimum=0.0, maximum=0.05, description="Maximum fingertip approach travel."), "max_approach_speed_m_s": number(unit="m/s", exclusive_minimum=0.0, maximum=0.05, description="Maximum fingertip approach speed."), "max_duration_s": DURATION()}),
    "L5": closed({"required_fingers": FINGER_NAMES, "separation_directions_palm_unit": vectors(3, min_items=1, max_items=4, unit="unitless", frame="palm", minimum=-1.0, maximum=1.0, description="Aligned private separation directions."), "duration_s": number(unit="s", minimum=1.0, maximum=2.0, description="Contact hold duration.")}),
    "L6": closed({"finger_names": FINGER_NAMES, "offsets_palm_m": vectors(3, min_items=1, max_items=4, unit="m", frame="palm", minimum=-0.03, maximum=0.03, description="Aligned selected-fingertip offsets."), "max_duration_per_leg_s": SEGMENT_DURATION()}),
    "ST1": closed({"translation_initial_yaw_m": vector(2, unit="m", frame="initial_base_yaw", minimum=-0.2, maximum=0.2, description="Stretch base translation in its initial-yaw frame."), "max_duration_s": DURATION()}),
    "ST2": closed({"target_yaw_world_rad": number(unit="rad", frame="world", minimum=-3.1416, maximum=3.1416, description="Absolute world-frame base yaw."), "max_duration_s": DURATION()}),
    "ST3": closed({"target_position_world_m": vector(3, unit="m", frame="world", minimum=-1.5, maximum=1.5, description="World-frame Stretch tool target."), "max_duration_s": DURATION()}),
    "ST4": closed({"waypoints_world_m": vectors(3, min_items=2, max_items=8, unit="m", frame="world", minimum=-1.5, maximum=1.5, description="Ordered world-frame Stretch tool waypoints."), "max_duration_per_segment_s": SEGMENT_DURATION()}),
    "ST5": closed({"opening_fraction": OPENING(), "max_duration_s": DURATION()}),
    "ST6": closed({"target_yaw_rad": number(unit="rad", frame="joint", minimum=-1.5, maximum=3.5, description="Absolute non-wrapped wrist-yaw joint target."), "max_duration_s": DURATION()}),
    "ST7": closed(dict(CONTACT_FIELDS)),
    "ST8": closed({"offset_call_time_base_m": vector(3, unit="m", frame="call_time_base", minimum=-0.06, maximum=0.06, description="Stretch call-time-base tool offset."), "max_duration_per_leg_s": SEGMENT_DURATION()}),
    "AL1": closed({"arm": ARM, "target_position_world_m": vector(3, unit="m", frame="world", minimum=-1.0, maximum=1.0, description="Selected-arm world-frame target."), "max_duration_s": DURATION()}),
    "AL2": closed({"arm": ARM, "waypoints_world_m": vectors(3, min_items=2, max_items=8, unit="m", frame="world", minimum=-1.0, maximum=1.0, description="Selected-arm ordered world-frame waypoints."), "max_duration_per_segment_s": SEGMENT_DURATION()}),
    "AL3": closed({"arm": ARM, "opening_fraction": OPENING(), "max_duration_s": DURATION()}),
    "AL4": closed({"left_target_position_world_m": vector(3, unit="m", frame="world", minimum=-1.0, maximum=1.0, description="Left-arm world-frame target."), "right_target_position_world_m": vector(3, unit="m", frame="world", minimum=-1.0, maximum=1.0, description="Right-arm world-frame target."), "max_duration_s": DURATION()}),
    "AL5": closed({"arm": ARM, **CONTACT_FIELDS}),
    "AL6": closed({"left_offset_arm_base_m": vector(3, unit="m", frame="left_arm_base", minimum=-0.06, maximum=0.06, description="Left arm-base-frame offset."), "right_offset_arm_base_m": vector(3, unit="m", frame="right_arm_base", minimum=-0.06, maximum=0.06, description="Right arm-base-frame offset."), "max_duration_per_leg_s": SEGMENT_DURATION()}),
}


def poses(base: float) -> list[float]:
    return [round(base + 0.02 * (index % 4), 3) for index in range(16)]


REQUESTS: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {
    "A1": ({"target_position_m": [0.40, 0.10, 0.20], "max_duration_s": 4.0}, {"target_position_m": [0.42, 0.08, 0.21], "max_duration_s": 4.5}, {"target_position_m": [0.38, 0.12, 0.19], "max_duration_s": 5.0}),
    "A2": ({"waypoints_m": [[0.36, 0.08, 0.20], [0.40, 0.10, 0.20]], "max_duration_per_segment_s": 3.0}, {"waypoints_m": [[0.35, 0.12, 0.21], [0.39, 0.11, 0.22], [0.42, 0.09, 0.21]], "max_duration_per_segment_s": 3.5}, {"waypoints_m": [[0.34, 0.06, 0.19], [0.38, 0.08, 0.20], [0.40, 0.12, 0.19]], "max_duration_per_segment_s": 4.0}),
    "A3": ({"opening_fraction": 0.2, "max_duration_s": 2.0}, {"opening_fraction": 0.8, "max_duration_s": 2.5}, {"opening_fraction": 0.5, "max_duration_s": 3.0}),
    "A4": ({"precontact_position_m": [0.38, 0.07, 0.22], "approach_direction_unit": [0.0, 1.0, 0.0], "max_travel_m": 0.06, "max_approach_speed_m_s": 0.02, "max_duration_s": 4.0}, {"precontact_position_m": [0.37, 0.07, 0.22], "approach_direction_unit": [0.0, 1.0, 0.0], "max_travel_m": 0.065, "max_approach_speed_m_s": 0.025, "max_duration_s": 4.5}, {"precontact_position_m": [0.39, 0.065, 0.22], "approach_direction_unit": [0.0, 1.0, 0.0], "max_travel_m": 0.07, "max_approach_speed_m_s": 0.03, "max_duration_s": 5.0}),
    "A5": ({"offset_robot_base_m": [0.02, 0.0, 0.0], "max_duration_per_leg_s": 3.0}, {"offset_robot_base_m": [0.0, 0.02, 0.0], "max_duration_per_leg_s": 3.5}, {"offset_robot_base_m": [0.0, 0.0, 0.02], "max_duration_per_leg_s": 4.0}),
    "G1": ({"linear_velocity_body_m_s": [0.2, 0.0], "yaw_rate_rad_s": 0.0, "duration_s": 2.2}, {"linear_velocity_body_m_s": [0.0, 0.2], "yaw_rate_rad_s": 0.3, "duration_s": 2.6}, {"linear_velocity_body_m_s": [0.25, 0.0], "yaw_rate_rad_s": -0.3, "duration_s": 3.0}),
    "G2": ({"translation_initial_yaw_m": [0.08, 0.0], "yaw_delta_rad": 0.1, "max_duration_s": 5.0}, {"translation_initial_yaw_m": [0.0, 0.08], "yaw_delta_rad": -0.1, "max_duration_s": 5.5}, {"translation_initial_yaw_m": [0.06, 0.04], "yaw_delta_rad": 0.15, "max_duration_s": 6.0}),
    "G3": ({"waypoints_initial_yaw_m": [[0.05, 0.0], [0.10, 0.0]], "max_duration_s": 5.0}, {"waypoints_initial_yaw_m": [[0.04, 0.02], [0.08, 0.04], [0.12, 0.04]], "max_duration_s": 5.5}, {"waypoints_initial_yaw_m": [[0.04, -0.02], [0.08, -0.04], [0.12, 0.0]], "max_duration_s": 6.0}),
    "G4": ({"target_height_m": 0.26, "max_duration_s": 3.0}, {"target_height_m": 0.29, "max_duration_s": 3.5}, {"target_height_m": 0.32, "max_duration_s": 4.0}),
    "G5": ({"duration_s": 1.2}, {"duration_s": 1.5}, {"duration_s": 1.8}),
    "L1": ({"target_joint_positions_rad": poses(0.15), "max_duration_s": 3.0}, {"target_joint_positions_rad": poses(0.25), "max_duration_s": 3.5}, {"target_joint_positions_rad": poses(0.35), "max_duration_s": 4.0}),
    "L2": ({"joint_waypoints_rad": [poses(0.10), poses(0.20)], "max_duration_per_segment_s": 2.0}, {"joint_waypoints_rad": [poses(0.15), poses(0.25), poses(0.30)], "max_duration_per_segment_s": 2.5}, {"joint_waypoints_rad": [poses(0.20), poses(0.30), poses(0.25)], "max_duration_per_segment_s": 3.0}),
    "L3": ({"target_fingertip_positions_palm_m": [0.128775611, 0.006391345, -0.048752213, 0.130839233, -0.036590229, -0.036725889, 0.130511063, -0.084105402, -0.038716047, -0.074933026, 0.136039464, -0.032154495], "max_control_steps": 50}, {"target_fingertip_positions_palm_m": [0.128014637, 0.008857801, -0.052027841, 0.130659492, -0.039310226, -0.037709774, 0.130489767, -0.08219295, -0.0390014, -0.075466139, 0.136042797, -0.032556929], "max_control_steps": 50}, {"target_fingertip_positions_palm_m": [0.129565291, 0.00477925, -0.044799256, 0.130035869, -0.034984134, -0.041463969, 0.130193737, -0.085662757, -0.040249204, -0.072123118, 0.13574216, -0.032895757], "max_control_steps": 50}),
    "L4": ({"required_fingers": ["index"], "contact_target_positions_palm_m": [[0.001682462, 0.00760039, -0.138217348]], "approach_directions_palm_unit": [[0.679068929, 0.0, -0.734074512]], "max_travel_m": 0.02, "max_approach_speed_m_s": 0.02, "max_duration_s": 3.0}, {"required_fingers": ["middle", "thumb"], "contact_target_positions_palm_m": [[0.001583204, -0.03779962, -0.138217358], [-0.036555221, 0.045714849, -0.13812421]], "approach_directions_palm_unit": [[0.679068929, 0.0, -0.734074512], [-0.487865368, 0.85776329, -0.161955307]], "max_travel_m": 0.025, "max_approach_speed_m_s": 0.025, "max_duration_s": 3.5}, {"required_fingers": ["index", "ring"], "contact_target_positions_palm_m": [[0.003852744, 0.00760039, -0.138225718], [0.001593494, -0.08319962, -0.138207358]], "approach_directions_palm_unit": [[0.679068929, 0.0, -0.734074512], [0.679068929, 0.0, -0.734074512]], "max_travel_m": 0.03, "max_approach_speed_m_s": 0.03, "max_duration_s": 4.0}),
    "L5": ({"required_fingers": ["index"], "separation_directions_palm_unit": [[0.679068929, 0.0, -0.734074512]], "duration_s": 1.2}, {"required_fingers": ["middle", "thumb"], "separation_directions_palm_unit": [[0.679068929, 0.0, -0.734074512], [-0.487865368, 0.85776329, -0.161955307]], "duration_s": 1.5}, {"required_fingers": ["index", "ring"], "separation_directions_palm_unit": [[0.679068929, 0.0, -0.734074512], [0.679068929, 0.0, -0.734074512]], "duration_s": 1.8}),
    "L6": ({"finger_names": ["index"], "offsets_palm_m": [[-0.005203845, -0.001771343, -0.02438816]], "max_duration_per_leg_s": 2.0}, {"finger_names": ["middle", "thumb"], "offsets_palm_m": [[-0.002649684, 0.002876563, -0.024692196], [0.017369178, -0.002662201, -0.017782698]], "max_duration_per_leg_s": 2.5}, {"finger_names": ["index", "ring"], "offsets_palm_m": [[-0.004738515, 0.002530162, -0.024416076], [-0.002697652, -0.002086947, -0.024766254]], "max_duration_per_leg_s": 3.0}),
    "ST1": ({"translation_initial_yaw_m": [0.05, 0.0], "max_duration_s": 4.0}, {"translation_initial_yaw_m": [0.0, 0.05], "max_duration_s": 4.5}, {"translation_initial_yaw_m": [-0.04, 0.02], "max_duration_s": 5.0}),
    "ST2": ({"target_yaw_world_rad": 0.05, "max_duration_s": 4.0}, {"target_yaw_world_rad": -0.08, "max_duration_s": 4.5}, {"target_yaw_world_rad": 0.12, "max_duration_s": 5.0}),
    "ST3": ({"target_position_world_m": [0.10, -0.55, 0.51], "max_duration_s": 4.0}, {"target_position_world_m": [0.12, -0.53, 0.53], "max_duration_s": 4.5}, {"target_position_world_m": [0.08, -0.57, 0.55], "max_duration_s": 5.0}),
    "ST4": ({"waypoints_world_m": [[0.08, -0.53, 0.50], [0.10, -0.55, 0.51]], "max_duration_per_segment_s": 3.0}, {"waypoints_world_m": [[0.09, -0.52, 0.51], [0.11, -0.54, 0.53], [0.12, -0.55, 0.54]], "max_duration_per_segment_s": 3.5}, {"waypoints_world_m": [[0.07, -0.56, 0.52], [0.09, -0.57, 0.54], [0.11, -0.56, 0.55]], "max_duration_per_segment_s": 4.0}),
    "ST5": ({"opening_fraction": 0.2, "max_duration_s": 2.0}, {"opening_fraction": 0.8, "max_duration_s": 2.5}, {"opening_fraction": 0.5, "max_duration_s": 3.0}),
    "ST6": ({"target_yaw_rad": 0.2, "max_duration_s": 2.0}, {"target_yaw_rad": -0.2, "max_duration_s": 2.5}, {"target_yaw_rad": 0.4, "max_duration_s": 3.0}),
    "ST7": ({"precontact_position_m": [0.08, -0.497, 0.57], "approach_direction_unit": [1.0, 0.0, 0.0], "max_travel_m": 0.05, "max_approach_speed_m_s": 0.02, "max_duration_s": 4.0}, {"precontact_position_m": [0.075, -0.49, 0.565], "approach_direction_unit": [1.0, 0.0, 0.0], "max_travel_m": 0.055, "max_approach_speed_m_s": 0.025, "max_duration_s": 4.5}, {"precontact_position_m": [0.085, -0.50, 0.575], "approach_direction_unit": [1.0, 0.0, 0.0], "max_travel_m": 0.06, "max_approach_speed_m_s": 0.03, "max_duration_s": 5.0}),
    "ST8": ({"offset_call_time_base_m": [0.02, 0.0, 0.0], "max_duration_per_leg_s": 3.0}, {"offset_call_time_base_m": [0.0, 0.02, 0.0], "max_duration_per_leg_s": 3.5}, {"offset_call_time_base_m": [0.0, 0.0, 0.02], "max_duration_per_leg_s": 4.0}),
    "AL1": ({"arm": "right", "target_position_world_m": [0.20, 0.10, 0.20], "max_duration_s": 4.0}, {"arm": "left", "target_position_world_m": [-0.20, 0.10, 0.20], "max_duration_s": 4.5}, {"arm": "right", "target_position_world_m": [0.22, 0.08, 0.18], "max_duration_s": 5.0}),
    "AL2": ({"arm": "right", "waypoints_world_m": [[0.18, 0.08, 0.18], [0.20, 0.10, 0.20]], "max_duration_per_segment_s": 3.0}, {"arm": "left", "waypoints_world_m": [[-0.18, 0.08, 0.18], [-0.20, 0.10, 0.20], [-0.22, 0.08, 0.18]], "max_duration_per_segment_s": 3.5}, {"arm": "right", "waypoints_world_m": [[0.18, 0.12, 0.17], [0.20, 0.10, 0.19], [0.22, 0.08, 0.20]], "max_duration_per_segment_s": 4.0}),
    "AL3": ({"arm": "right", "opening_fraction": 0.2, "max_duration_s": 2.0}, {"arm": "left", "opening_fraction": 0.8, "max_duration_s": 2.5}, {"arm": "right", "opening_fraction": 0.5, "max_duration_s": 3.0}),
    "AL4": ({"left_target_position_world_m": [-0.20, 0.10, 0.20], "right_target_position_world_m": [0.20, 0.10, 0.20], "max_duration_s": 4.0}, {"left_target_position_world_m": [-0.22, 0.08, 0.18], "right_target_position_world_m": [0.22, 0.08, 0.18], "max_duration_s": 4.5}, {"left_target_position_world_m": [-0.18, 0.12, 0.19], "right_target_position_world_m": [0.18, 0.12, 0.19], "max_duration_s": 5.0}),
    "AL5": ({"arm": "right", "precontact_position_m": [0.20, 0.07, 0.06], "approach_direction_unit": [0.0, 1.0, 0.0], "max_travel_m": 0.06, "max_approach_speed_m_s": 0.02, "max_duration_s": 4.0}, {"arm": "left", "precontact_position_m": [0.18, 0.065, 0.06], "approach_direction_unit": [0.0, 1.0, 0.0], "max_travel_m": 0.065, "max_approach_speed_m_s": 0.025, "max_duration_s": 4.5}, {"arm": "right", "precontact_position_m": [0.22, 0.06, 0.06], "approach_direction_unit": [0.0, 1.0, 0.0], "max_travel_m": 0.07, "max_approach_speed_m_s": 0.03, "max_duration_s": 5.0}),
    "AL6": ({"left_offset_arm_base_m": [0.02, 0.0, 0.0], "right_offset_arm_base_m": [0.02, 0.0, 0.0], "max_duration_per_leg_s": 3.0}, {"left_offset_arm_base_m": [0.0, 0.02, 0.0], "right_offset_arm_base_m": [0.0, -0.02, 0.0], "max_duration_per_leg_s": 3.5}, {"left_offset_arm_base_m": [0.0, 0.0, 0.02], "right_offset_arm_base_m": [0.0, 0.0, 0.02], "max_duration_per_leg_s": 4.0}),
}

COMMON_GUARDS = [
    {"guard_id": "control", "kind": "actuator_and_physics_step_required"},
    {"guard_id": "no-direct-write", "kind": "no_direct_state_write"},
    {"guard_id": "canonical", "kind": "canonical_model_data"},
    {"guard_id": "control-range", "kind": "control_range"},
]


def reset_for(
    robot_id: str, capability_id: str, variant: str
) -> dict[str, Any]:
    offset = {"H1": 0.0, "H2": 0.02, "H3": -0.02}[variant]
    if robot_id == "robotstudio_so101":
        pan = -1.82 + offset
        gripper = (
            {"H1": 1.5, "H2": 0.0, "H3": 1.0}[variant]
            if capability_id == "A3"
            else 0.0
        )
        return {"kind": "default", "joint_positions": {"shoulder_pan": pan, "gripper": gripper}, "actuator_controls": {"shoulder_pan": pan, "gripper": gripper}}
    if robot_id == "unitree-go2-stock-12dof":
        reset: dict[str, Any] = {"kind": "keyframe", "name": "home", "joint_positions": {"FL_hip_joint": offset}}
        if capability_id == "G5":
            reset["body_quaternions"] = {
                "base_link": {
                    "H1": [0.9987502604, 0.0499791693, 0.0, 0.0],
                    "H2": [0.9982005399, 0.0, 0.0599640065, 0.0],
                    "H3": [0.9975510003, -0.0699428473, 0.0, 0.0],
                }[variant]
            }
        return reset
    if robot_id == "leap_hand":
        contact_capability = capability_id in {"L4", "L5"}
        value = (0.80 if contact_capability else 0.10) + offset
        reset: dict[str, Any] = {
            "kind": "keyframe" if contact_capability else "default",
            "joint_positions": {"if_mcp": value},
            "actuator_controls": {"if_mcp_act": value},
        }
        if contact_capability:
            reset["name"] = "home"
        request_index = {"H1": 0, "H2": 1, "H3": 2}[variant]
        if capability_id == "L4":
            request = REQUESTS["L4"][request_index]
            reset["mocap_body_positions"] = {
                LEAP_TARGET_BODIES[finger]: {
                    "frame_body_name": "palm",
                    "position_m": target,
                }
                for finger, target in zip(
                    request["required_fingers"],
                    request["contact_target_positions_palm_m"],
                )
            }
        elif capability_id == "L5":
            request = REQUESTS["L5"][request_index]
            reset["mocap_body_positions"] = {
                LEAP_TARGET_BODIES[finger]: {
                    "site_name": LEAP_FINGERTIP_SITES[finger],
                    "frame_body_name": "palm",
                    "offset_m": [
                        round(
                            ({
            "index": 0.008,
            "middle": 0.008,
            "ring": 0.008,
            "thumb": 0.008,
                            }[finger])
                            * direction_axis,
                            9,
                        )
                        for direction_axis in direction
                    ],
                }
                for finger, direction in zip(
                    request["required_fingers"],
                    request["separation_directions_palm_unit"],
                )
            }
        return reset
    if robot_id == "hello_robot_stretch_2":
        lift = max(-0.5, offset)
        gripper = (
            {"H1": 0.04, "H2": -0.005, "H3": 0.04}[variant]
            if capability_id == "ST5"
            else 0.04
        )
        return {
            "kind": "default",
            "joint_positions": {"joint_lift": lift, "joint_gripper_slide": gripper},
            "actuator_controls": {"lift": lift, "grip": gripper},
        }
    shoulder = -0.96 + offset
    return {"kind": "keyframe", "name": "neutral_pose", "joint_positions": {"right/shoulder": shoulder}, "actuator_controls": {"right/shoulder": shoulder}}


def binding_for(capability_id: str) -> dict[str, Any]:
    parameters: dict[str, Any] = {"contract_id": capability_id}
    if capability_id.startswith("A") and not capability_id.startswith("AL"):
        parameters["side_effect_guard_profile"] = "so101"
    elif capability_id.startswith("AL"):
        parameters["side_effect_guard_profile"] = "aloha2"
    if capability_id in {"A1", "A2", "A5"}:
        parameters["site_name"] = "gripperframe"
        if capability_id == "A5":
            parameters["base_body_name"] = "base"
    elif capability_id == "A3":
        parameters.update({"joint_name": "gripper", "closed_position": -0.17453, "open_position": 1.74533})
    elif capability_id == "A4":
        parameters.update({"site_name": "gripperframe", "tool_body_names": ["gripper"], "target_geom_names": ["front_button_geom"], "precontact_gate": "held_window_then_ray"})
    elif capability_id.startswith("G"):
        parameters["body_name"] = "base_link"
        if capability_id == "G5":
            parameters.update({"foot_geom_groups": [["FL"], ["FR"], ["RL"], ["RR"]], "floor_geom_names": ["floor"], "forbidden_floor_geom_names": [f"geom_{index}" for index in range(1, 14)]})
    elif capability_id in {"L1", "L2"}:
        parameters["joint_names"] = ["if_mcp", "if_rot", "if_pip", "if_dip", "mf_mcp", "mf_rot", "mf_pip", "mf_dip", "rf_mcp", "rf_rot", "rf_pip", "rf_dip", "th_cmc", "th_axl", "th_mcp", "th_ipl"]
    elif capability_id == "L3":
        parameters.update({"palm_body_name": "palm", "fingertip_site_names": LEAP_FINGERTIP_SITES, "physics_steps_per_control_step": 10})
    elif capability_id == "L4":
        parameters.update({"palm_body_name": "palm", "fingertip_site_names": LEAP_FINGERTIP_SITES, "fingertip_geom_names": LEAP_FINGERTIP_GEOMS, "target_geom_names": LEAP_TARGET_GEOMS, "target_body_names": LEAP_TARGET_BODIES, "finger_joint_names": LEAP_FINGER_JOINTS})
    elif capability_id == "L5":
        parameters.update({"palm_body_name": "palm", "fingertip_site_names": LEAP_FINGERTIP_SITES, "fingertip_geom_names": LEAP_FINGERTIP_GEOMS, "target_geom_names": LEAP_TARGET_GEOMS, "target_site_names": LEAP_TARGET_SITES, "target_body_names": LEAP_TARGET_BODIES, "finger_joint_names": LEAP_FINGER_JOINTS})
    elif capability_id == "L6":
        parameters.update({"palm_body_name": "palm", "fingertip_site_names": LEAP_FINGERTIP_SITES, "finger_joint_names": LEAP_FINGER_JOINTS})
    elif capability_id in {"ST1", "ST2"}:
        parameters["body_name"] = "base_link"
    elif capability_id in {"ST3", "ST4", "ST8"}:
        parameters.update({"site_name": "link_gripper_slider", "body_name": "base_link"})
    elif capability_id == "ST5":
        parameters.update({"joint_name": "joint_gripper_slide", "closed_position": -0.005, "open_position": 0.04})
    elif capability_id == "ST6":
        parameters["joint_name"] = "joint_wrist_yaw"
    elif capability_id == "ST7":
        parameters.update({"site_name": "link_gripper_slider", "tool_geom_names": [], "tool_body_names": ["link_gripper_slider"], "target_geom_names": ["front_button_geom"]})
    elif capability_id in {"AL1", "AL2", "AL4", "AL6"}:
        parameters["arm_site_names"] = {"left": "left/gripper", "right": "right/gripper"}
        if capability_id == "AL6":
            parameters["arm_base_body_names"] = {"left": "left/base_link", "right": "right/base_link"}
    elif capability_id == "AL3":
        parameters.update({"arm_site_names": {"left": "left/gripper", "right": "right/gripper"}, "arm_gripper_joint_names": {"left": ["left/left_finger", "left/right_finger"], "right": ["right/left_finger", "right/right_finger"]}, "closed_position": 0.002, "open_position": 0.037})
    elif capability_id == "AL5":
        parameters.update({"arm_site_names": {"left": "left/gripper", "right": "right/gripper"}, "arm_tool_geom_names": {"left": ["left/left_g0", "left/right_g0"], "right": ["right/left_g0", "right/right_g0"]}, "target_geom_names": ["front_button_geom"], "precontact_gate": "held_window_then_ray"})
    return {"kind": "b1_contract", "parameters": parameters}


def framework_events_for(
    robot_id: str, capability_id: str, variant: str
) -> list[dict[str, Any]]:
    if robot_id != "leap_hand" or capability_id != "L5":
        return []
    request = REQUESTS["L5"][{"H1": 0, "H2": 1, "H3": 2}[variant]]
    distance_m = {"H1": 0.0065, "H2": 0.0070, "H3": 0.0075}[variant]
    return [
        {
            "kind": "move_mocap_body",
            "body_name": LEAP_TARGET_BODIES[finger],
            "frame_body_name": "palm",
            "start_time_s": 0.25,
            "duration_s": 0.05,
            "displacement_m": [
                round(distance_m * axis, 9) for axis in direction
            ],
        }
        for finger, direction in zip(
            request["required_fingers"],
            request["separation_directions_palm_unit"],
        )
    ]


def preinvoke_for(
    robot_id: str, capability_id: str, variant: str
) -> dict[str, Any] | None:
    if robot_id != "leap_hand" or capability_id != "L5":
        return None
    request = REQUESTS["L5"][{"H1": 0, "H2": 1, "H3": 2}[variant]]
    return {
        "duration_s": 0.10,
        "required_contact_pairs": [
            {
                "geom1": LEAP_FINGERTIP_GEOMS[finger][0],
                "geom2": LEAP_TARGET_GEOMS[finger][0],
            }
            for finger in request["required_fingers"]
        ],
    }


def scene_for(robot_id: str, capability_id: str) -> str:
    if capability_id in {"A4", "ST7", "AL5"}:
        return "assets/button_front_scene.xml"
    if robot_id == "robotstudio_so101":
        return "assets/reach_scene.xml"
    if robot_id == "unitree-go2-stock-12dof":
        return "assets/go2_scene.xml"
    if robot_id == "leap_hand":
        return "assets/leap_hand_kinematic_scene.xml"
    return "assets/scene.xml"


def package_identity(robot_id: str) -> tuple[str, str]:
    root = ROBOT_ROOT / robot_id / "1.0.0"
    morphology = json.loads((root / "morphology.json").read_text(encoding="utf-8"))
    catalog = json.loads((root / "tasks" / "catalog.json").read_text(encoding="utf-8"))
    return str(morphology["package_version"]), str(catalog["snapshot_id"])


def design_for(robot_id: str) -> dict[str, Any]:
    package_version, snapshot_id = package_identity(robot_id)
    return {
        "artifact_type": "b1_fixed_capability_design",
        "schema_version": "1.0",
        "capability_design_id": f"experiment1-b1-fixed-interface::{robot_id}::v2",
        "robot_configuration_id": robot_id,
        "package_version": package_version,
        "task_snapshot_id": snapshot_id,
        "invocation_abi": {"kind": "capability_request", "method_call": "method(request=request)"},
        "capabilities": [
            {
                "capability_id": capability_id,
                "method_name": method_name,
                "description": DESCRIPTIONS[capability_id],
                "covered_task_ids": [],
                "request_schema": SCHEMAS[capability_id],
                "public_standard": {
                    "criterion_id": capability_id,
                    "criterion_text": STANDARDS[capability_id],
                    "hidden_case_pass_rule": {
                        "minimum_passed": 2,
                        "case_count": 3,
                    },
                    "source": CRITERIA_SOURCE,
                },
            }
            for capability_id, method_name in CAPABILITIES[robot_id]
        ],
    }


def suite_version(robot_id: str) -> str:
    return "v3"


def suite_for(robot_id: str) -> dict[str, Any]:
    package_version, snapshot_id = package_identity(robot_id)
    cases: list[dict[str, Any]] = []
    for capability_id, method_name in CAPABILITIES[robot_id]:
        for index, variant in enumerate(("H1", "H2", "H3")):
            case = {
                "case_id": f"{capability_id}-{variant}",
                "case_variant": variant,
                "capability_id": capability_id,
                "method_name": method_name,
                "scene_entrypoint": scene_for(robot_id, capability_id),
                "request": REQUESTS[capability_id][index],
                "reset": reset_for(robot_id, capability_id, variant),
                "max_steps": 5000,
                "sample_hz": 20.0,
                "timeout_sim_s": 8.0,
                "repetitions": 1,
                "binding": binding_for(capability_id),
                "guards": COMMON_GUARDS,
                "criterion": {
                    "metric": "b1_contract_binary",
                    "unit": "binary",
                    "comparator": ">=",
                    "threshold": 1,
                    "temporal": {"kind": "fixed_trials"},
                    "aggregation": {"kind": "single_trial"},
                    "source_refs": [f"{CRITERIA_SOURCE}#{capability_id.lower()}"],
                },
            }
            framework_events = framework_events_for(
                robot_id, capability_id, variant
            )
            if framework_events:
                case["framework_events"] = framework_events
            preinvoke = preinvoke_for(robot_id, capability_id, variant)
            if preinvoke is not None:
                case["preinvoke"] = preinvoke
            cases.append(case)
    return {
        "artifact_type": "b1_fixed_validation_suite",
        "schema_version": "1.0",
        "suite_id": f"experiment1-b1-fixed-suite::{robot_id}::{suite_version(robot_id)}",
        "pass_standard_id": "experiment1-b1-driver-validation-criteria-v2",
        "robot_configuration_id": robot_id,
        "package_version": package_version,
        "task_snapshot_id": snapshot_id,
        "whole_suite_aggregation": {
            "kind": "all_capabilities_two_of_three_cases"
        },
        "cases": cases,
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    for robot_id in CAPABILITIES:
        destination = OUTPUT_ROOT / robot_id
        write_json(destination / "capability_design.json", design_for(robot_id))
        write_json(destination / "capability_validation_suite.json", suite_for(robot_id))
    write_json(
        OUTPUT_ROOT / "index.json",
        {
            "artifact_type": "b1_fixed_validation_bundle_set",
            "schema_version": "1.0",
            "bundle_set_id": "experiment1-b1-four-robot-fixed-bundles-v4",
            "robots": {
                robot_id: {
                    "capability_design": f"{robot_id}/capability_design.json",
                    "capability_validation_suite": (
                        f"{robot_id}/capability_validation_suite.json"
                    ),
                    "fixed_capability_interface_id": (
                        f"experiment1-b1-fixed-interface::{robot_id}::v2"
                    ),
                    "fixed_capability_pass_standard_id": (
                        "experiment1-b1-driver-validation-criteria-v2"
                    ),
                    "validation_suite_id": (
                        f"experiment1-b1-fixed-suite::{robot_id}::{suite_version(robot_id)}"
                    ),
                }
                for robot_id in CAPABILITIES
            },
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
