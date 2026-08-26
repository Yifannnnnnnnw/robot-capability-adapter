"""Machine-readable trusted measurement operators for capability-v2 IVC.

The catalog is the complete executable vocabulary available to an IVC.  An
IVC authors a binding from these operators; it never supplies Python and it
never selects an opaque pre-written case.  The deterministic audit below runs
before candidate code is started.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class MeasurementOperatorError(ValueError):
    """Raised when an inline capability measurement is not executable."""


def _spec(
    description: str,
    units: Sequence[str],
    *,
    required: Mapping[str, str] = {},
    optional: Mapping[str, str] = {},
    entities: Mapping[str, str] = {},
    request_paths: Sequence[str] = (),
    evaluation_mode: str = "numeric_measurement",
    parameter_details: Mapping[str, Mapping[str, Any]] = {},
    request_value_types: Mapping[str, str] = {},
) -> dict[str, Any]:
    properties = {
        **{name: {"type": kind} for name, kind in required.items()},
        **{name: {"type": kind} for name, kind in optional.items()},
    }
    for name, details in parameter_details.items():
        if name in properties:
            properties[name].update(copy.deepcopy(dict(details)))
    return {
        "description": description,
        "output_units": list(units),
        "parameter_schema": {
            "type": "object",
            "required": list(required),
            "properties": properties,
            "additionalProperties": False,
        },
        "request_path_parameters": list(request_paths),
        "request_value_types": dict(request_value_types),
        "entity_parameters": dict(entities),
        "evaluation_mode": evaluation_mode,
    }


# Parameter types are deliberately a small closed DSL.  ``request_path`` is a
# dotted path rooted at ``request``; entity types are resolved against the
# selected MuJoCo scene by ``audit_inline_measurement_binding``.
_OPERATOR_SPECS: dict[str, dict[str, Any]] = {
    "final_site_position_error": _spec(
        "Euclidean terminal position error for one named site.",
        ["m"],
        required={"site_name": "string", "target_argument": "request_path"},
        entities={"site_name": "site"},
        request_paths=("target_argument",),
        request_value_types={"target_argument": "number_array_3"},
    ),
    "final_site_axis_error": _spec(
        "Absolute terminal axis error for one named site.",
        ["m"],
        required={
            "site_name": "string",
            "target_argument": "request_path",
            "axis": "integer",
        },
        entities={"site_name": "site"},
        request_paths=("target_argument",),
    ),
    "final_weighted_site_position_error": _spec(
        "Weighted Euclidean terminal position error for one named site.",
        ["m"],
        required={
            "site_name": "string",
            "target_argument": "request_path",
            "weights": "number_array",
        },
        entities={"site_name": "site"},
        request_paths=("target_argument",),
    ),
    "final_body_position_error": _spec(
        "Euclidean terminal world-position error for one named body against a "
        "request numeric array of exactly three coordinates.",
        ["m"],
        required={"body_name": "string", "target_argument": "request_path"},
        entities={"body_name": "body"},
        request_paths=("target_argument",),
        request_value_types={"target_argument": "number_array_3"},
    ),
    "final_body_xyz_position_error": _spec(
        "Euclidean terminal world-position error for one named body against "
        "three scalar request-coordinate paths.",
        ["m"],
        required={
            "body_name": "string",
            "target_x_argument": "request_path",
            "target_y_argument": "request_path",
            "target_z_argument": "request_path",
        },
        entities={"body_name": "body"},
        request_paths=(
            "target_x_argument",
            "target_y_argument",
            "target_z_argument",
        ),
        request_value_types={
            "target_x_argument": "world_m_number",
            "target_y_argument": "world_m_number",
            "target_z_argument": "world_m_number",
        },
    ),
    "final_site_frame_xyz_position_error": _spec(
        "Euclidean terminal position error for one named site in a named body "
        "frame against three scalar request-coordinate paths.",
        ["m"],
        required={
            "site_name": "string",
            "reference_body_name": "string",
            "target_x_argument": "request_path",
            "target_y_argument": "request_path",
            "target_z_argument": "request_path",
        },
        entities={"site_name": "site", "reference_body_name": "body"},
        request_paths=(
            "target_x_argument",
            "target_y_argument",
            "target_z_argument",
        ),
        request_value_types={
            "target_x_argument": "frame_m_number",
            "target_y_argument": "frame_m_number",
            "target_z_argument": "frame_m_number",
        },
    ),
    "final_body_frame_xyz_position_error": _spec(
        "Euclidean terminal position error for one named body in another named "
        "body frame against three scalar request-coordinate paths.",
        ["m"],
        required={
            "body_name": "string",
            "reference_body_name": "string",
            "target_x_argument": "request_path",
            "target_y_argument": "request_path",
            "target_z_argument": "request_path",
        },
        entities={"body_name": "body", "reference_body_name": "body"},
        request_paths=(
            "target_x_argument",
            "target_y_argument",
            "target_z_argument",
        ),
        request_value_types={
            "target_x_argument": "frame_m_number",
            "target_y_argument": "frame_m_number",
            "target_z_argument": "frame_m_number",
        },
    ),
    "body_planar_target_error": _spec(
        "Terminal planar world-position error for one named body.",
        ["m"],
        required={"body_name": "string", "target_argument": "request_path"},
        entities={"body_name": "body"},
        request_paths=("target_argument",),
    ),
    "final_joint_position_error": _spec(
        "Absolute terminal position error for one scalar joint: radians for a "
        "hinge or metres for a slide. The trusted target is request_value * "
        "target_scale + target_offset. Non-identity conversion is accepted only "
        "when it exactly maps evidence-backed sealed request bounds onto the "
        "selected scene's finite joint range.",
        ["rad", "m"],
        required={"joint_name": "string", "target_argument": "request_path"},
        optional={"target_scale": "number", "target_offset": "number"},
        entities={"joint_name": "joint"},
        request_paths=("target_argument",),
        parameter_details={
            "target_scale": {
                "description": "Positive finite multiplier applied to the request "
                "value; non-identity values must match the audited bound conversion.",
                "default": 1.0,
            },
            "target_offset": {
                "description": "Finite joint-coordinate offset added after scaling; "
                "nonzero values must match the audited bound conversion.",
                "default": 0.0,
            },
        },
    ),
    "final_geom_pair_distance_error": _spec(
        "Absolute terminal error between the trusted MuJoCo distance of two named "
        "geoms and one scalar request target.",
        ["m"],
        required={
            "geom_a_name": "string",
            "geom_b_name": "string",
            "target_argument": "request_path",
        },
        entities={"geom_a_name": "geom", "geom_b_name": "geom"},
        request_paths=("target_argument",),
        request_value_types={"target_argument": "bounded_m_number"},
    ),
    "final_geom_pair_distance": _spec(
        "Terminal trusted MuJoCo signed distance between two named geoms.",
        ["m"],
        required={"geom_a_name": "string", "geom_b_name": "string"},
        entities={"geom_a_name": "geom", "geom_b_name": "geom"},
    ),
    "joint_range": _spec(
        "Observed position range of one named joint.",
        ["rad"],
        required={"joint_name": "string"},
        entities={"joint_name": "joint"},
    ),
    "body_height": _spec(
        "Terminal world-frame height of one named body.",
        ["m"],
        required={"body_name": "string"},
        entities={"body_name": "body"},
    ),
    "minimum_body_height": _spec(
        "Minimum observed world-frame height of one named body.",
        ["m"],
        required={"body_name": "string"},
        entities={"body_name": "body"},
    ),
    "body_planar_displacement": _spec(
        "Planar displacement of one named body over the evidence horizon.",
        ["m"],
        required={"body_name": "string"},
        entities={"body_name": "body"},
    ),
    "body_axis_displacement": _spec(
        "Signed displacement of one named body along a world axis.",
        ["m"],
        required={"body_name": "string"},
        optional={"axis": "integer"},
        entities={"body_name": "body"},
    ),
    "body_directional_displacement": _spec(
        "Planar displacement projected onto a requested heading.",
        ["m"],
        required={
            "body_name": "string",
            "direction_argument": "request_path",
        },
        entities={"body_name": "body"},
        request_paths=("direction_argument",),
        request_value_types={"direction_argument": "number"},
    ),
    "accumulated_body_arc_angle_error": _spec(
        "Absolute error between accumulated signed body arc angle and a requested "
        "angle, using a request-defined XYZ center and x/y/z axis name.",
        ["rad"],
        required={
            "body_name": "string",
            "center_x_argument": "request_path",
            "center_y_argument": "request_path",
            "center_z_argument": "request_path",
            "axis_argument": "request_path",
            "target_angle_argument": "request_path",
        },
        entities={"body_name": "body"},
        request_paths=(
            "center_x_argument",
            "center_y_argument",
            "center_z_argument",
            "axis_argument",
            "target_angle_argument",
        ),
        request_value_types={
            "center_x_argument": "world_m_number",
            "center_y_argument": "world_m_number",
            "center_z_argument": "world_m_number",
            "axis_argument": "world_axis_name",
            "target_angle_argument": "rad_number",
        },
    ),
    "final_body_directional_displacement_error": _spec(
        "Absolute error between terminal body displacement projected along a "
        "request XYZ direction and a requested distance.",
        ["m"],
        required={
            "body_name": "string",
            "direction_x_argument": "request_path",
            "direction_y_argument": "request_path",
            "direction_z_argument": "request_path",
            "target_distance_argument": "request_path",
        },
        entities={"body_name": "body"},
        request_paths=(
            "direction_x_argument",
            "direction_y_argument",
            "direction_z_argument",
            "target_distance_argument",
        ),
        request_value_types={
            "direction_x_argument": "world_direction_number",
            "direction_y_argument": "world_direction_number",
            "direction_z_argument": "world_direction_number",
            "target_distance_argument": "m_number",
        },
    ),
    "site_frame_xyz_directional_displacement": _spec(
        "Signed start-to-end displacement of a named site, expressed in a named "
        "body frame and projected onto a requested XYZ unit vector.",
        ["m"],
        required={
            "site_name": "string",
            "reference_body_name": "string",
            "direction_x_argument": "request_path",
            "direction_y_argument": "request_path",
            "direction_z_argument": "request_path",
        },
        entities={"site_name": "site", "reference_body_name": "body"},
        request_paths=(
            "direction_x_argument",
            "direction_y_argument",
            "direction_z_argument",
        ),
        request_value_types={
            "direction_x_argument": "frame_direction_number",
            "direction_y_argument": "frame_direction_number",
            "direction_z_argument": "frame_direction_number",
        },
    ),
    "accumulated_site_frame_axis_arc_angle_error": _spec(
        "Absolute error between accumulated signed site arc angle and a requested "
        "angle, with center and arbitrary unit axis expressed in a named body frame.",
        ["rad"],
        required={
            "site_name": "string",
            "reference_body_name": "string",
            "center_x_argument": "request_path",
            "center_y_argument": "request_path",
            "center_z_argument": "request_path",
            "axis_x_argument": "request_path",
            "axis_y_argument": "request_path",
            "axis_z_argument": "request_path",
            "target_angle_argument": "request_path",
        },
        entities={"site_name": "site", "reference_body_name": "body"},
        request_paths=(
            "center_x_argument",
            "center_y_argument",
            "center_z_argument",
            "axis_x_argument",
            "axis_y_argument",
            "axis_z_argument",
            "target_angle_argument",
        ),
        request_value_types={
            "center_x_argument": "frame_m_number",
            "center_y_argument": "frame_m_number",
            "center_z_argument": "frame_m_number",
            "axis_x_argument": "frame_direction_number",
            "axis_y_argument": "frame_direction_number",
            "axis_z_argument": "frame_direction_number",
            "target_angle_argument": "rad_number",
        },
    ),
    "body_directional_progress_until_corridor_exit": _spec(
        "Maximum requested-direction progress before leaving a trusted corridor.",
        ["m"],
        required={
            "body_name": "string",
            "direction_argument": "request_path",
            "limit_argument": "request_path",
            "maximum_cross_track_m": "number",
            "minimum_height_m": "number",
        },
        entities={"body_name": "body"},
        request_paths=("direction_argument", "limit_argument"),
    ),
    "mean_body_planar_speed": _spec(
        "Mean planar speed of one named body over the evidence horizon.",
        ["m/s"],
        required={"body_name": "string"},
        entities={"body_name": "body"},
    ),
    "body_yaw_change_deg": _spec(
        "Absolute yaw change of one named body.",
        ["deg"],
        required={"body_name": "string"},
        entities={"body_name": "body"},
    ),
    "mean_body_heading_error_deg": _spec(
        "Heading error inferred from body displacement.",
        ["deg"],
        required={
            "body_name": "string",
            "direction_argument": "request_path",
        },
        optional={"minimum_displacement": "number"},
        entities={"body_name": "body"},
        request_paths=("direction_argument",),
    ),
    "named_bodies_axis_completion": _spec(
        "Binary completion of a shared coordinate gate by named bodies.",
        ["binary"],
        required={
            "body_names": "string_array",
            "finish_coordinate": "number",
        },
        optional={"axis": "integer", "direction": "integer"},
        entities={"body_names": "body_array"},
    ),
    "mean_body_yaw_rate": _spec(
        "Absolute mean yaw rate of one named body.",
        ["rad/s"],
        required={"body_name": "string"},
        entities={"body_name": "body"},
    ),
    "ordered_body_waypoint_completion_ratio": _spec(
        "Ordered planar waypoint completion ratio for one named body.",
        ["ratio"],
        required={
            "body_name": "string",
            "waypoints": "number_matrix",
            "tolerance": "number",
        },
        entities={"body_name": "body"},
    ),
    "ordered_body_waypoint_completion_time": _spec(
        "Elapsed time to complete ordered planar waypoints.",
        ["s"],
        required={
            "body_name": "string",
            "waypoints": "number_matrix",
            "tolerance": "number",
        },
        entities={"body_name": "body"},
    ),
    "ordered_body_axis_gate_completion_ratio": _spec(
        "Ordered axis-gate completion ratio for one named body.",
        ["ratio"],
        required={"body_name": "string", "gates": "object_array"},
        entities={"body_name": "body"},
    ),
    "minimum_body_point_clearance": _spec(
        "Minimum planar clearance between one named body and fixed points.",
        ["m"],
        required={"body_name": "string", "points": "number_matrix"},
        entities={"body_name": "body"},
    ),
    "named_geom_contact_step_count": _spec(
        "Physics-step contact count involving any selected geom.",
        ["physics_steps", "count"],
        required={"geom_names": "string_array"},
        entities={"geom_names": "geom_array"},
    ),
    "contact_sample_count": _spec(
        "Number of trusted samples containing contact.",
        ["count"],
    ),
    "physics_step_count": _spec(
        "Number of trusted MuJoCo physics steps.",
        ["physics_steps", "count", "binary", "reward", "run"],
    ),
    "in_hand_object_pattern_success": _spec(
        "Binary trusted in-hand contact and motion-pattern outcome.",
        ["trial"],
        required={
            "body_name": "string",
            "reference_body_name": "string",
            "object_geom_names": "string_array",
            "required_robot_geom_groups": "string_matrix",
            "minimum_contact_steps": "integer",
            "motion_kind": "string",
        },
        optional={
            "minimum_simultaneous_contact_samples": "integer",
            "minimum_contact_group_transitions": "integer",
            "axis": "integer",
            "minimum_translation_range_m": "number",
            "translation_direction": "integer",
            "maximum_return_error_m": "number",
            "minimum_direction_changes": "integer",
            "site_names": "string_array",
            "minimum_site_translation_range_m": "number",
            "minimum_cumulative_rotation_deg": "number",
            "minimum_net_rotation_deg": "number",
            "joint_name": "string",
            "minimum_joint_range_rad": "number",
        },
        entities={
            "body_name": "body",
            "reference_body_name": "body",
            "object_geom_names": "geom_array",
            "required_robot_geom_groups": "geom_matrix",
            "site_names": "site_array",
            "joint_name": "joint",
        },
    ),
    "final_concatenated_site_position_error": _spec(
        "Terminal concatenated site-position error in a named body frame.",
        ["m"],
        required={
            "site_names": "string_array",
            "reference_body_name": "string",
            "target_argument": "request_path",
            "physics_steps_per_control_step": "integer",
            "control_steps_argument": "request_path",
        },
        entities={"site_names": "site_array", "reference_body_name": "body"},
        request_paths=("target_argument", "control_steps_argument"),
    ),
    "final_body_position_offset_error": _spec(
        "Terminal body-offset error with a same-state orientation gate.",
        ["m"],
        required={
            "body_name": "string",
            "reference_body_name": "string",
            "target_argument": "request_path",
            "orientation_target_argument": "request_path",
            "maximum_orientation_error_rad": "number",
            "physics_steps_per_control_step": "integer",
            "control_steps_argument": "request_path",
        },
        entities={"body_name": "body", "reference_body_name": "body"},
        request_paths=(
            "target_argument",
            "orientation_target_argument",
            "control_steps_argument",
        ),
    ),
    "final_body_quaternion_error": _spec(
        "Terminal relative quaternion error with a same-state position gate.",
        ["rad"],
        required={
            "body_name": "string",
            "reference_body_name": "string",
            "target_argument": "request_path",
            "position_target_argument": "request_path",
            "maximum_position_error_m": "number",
            "physics_steps_per_control_step": "integer",
            "control_steps_argument": "request_path",
        },
        entities={"body_name": "body", "reference_body_name": "body"},
        request_paths=(
            "target_argument",
            "position_target_argument",
            "control_steps_argument",
        ),
    ),
    "final_maximum_joint_position_error": _spec(
        "Maximum terminal position error across named joints.",
        ["rad"],
        required={
            "joint_names": "string_array",
            "target_argument": "request_path",
            "physics_steps_per_control_step": "integer",
            "control_steps_argument": "request_path",
        },
        entities={"joint_names": "joint_array"},
        request_paths=("target_argument", "control_steps_argument"),
    ),
    "final_wrapped_joint_position_error": _spec(
        "Terminal wrapped position error for one named joint.",
        ["rad"],
        required={
            "joint_name": "string",
            "target_argument": "request_path",
            "physics_steps_per_control_step": "integer",
            "control_steps_argument": "request_path",
        },
        entities={"joint_name": "joint"},
        request_paths=("target_argument", "control_steps_argument"),
    ),
    "maximum_joint_linear_trajectory_error": _spec(
        "Maximum linear-trajectory tracking error for one named joint.",
        ["rad"],
        required={
            "joint_name": "string",
            "velocity_argument": "request_path",
            "control_period_s": "number",
            "control_steps_argument": "request_path",
            "physics_steps_per_control_step": "integer",
        },
        entities={"joint_name": "joint"},
        request_paths=("velocity_argument", "control_steps_argument"),
    ),
    "body_target_solved_sample_count": _spec(
        "Count of control samples where a body is within target tolerance.",
        ["control_step"],
        required={
            "body_name": "string",
            "reference_body_name": "string",
            "target_argument": "request_path",
            "solved_distance_m": "number",
            "drop_distance_m": "number",
            "control_period_s": "number",
            "control_steps_argument": "request_path",
            "physics_steps_per_control_step": "integer",
        },
        entities={"body_name": "body", "reference_body_name": "body"},
        request_paths=("target_argument", "control_steps_argument"),
    ),
    "body_target_drop_event_count": _spec(
        "Count of target-drop events after a minimum solved horizon.",
        ["event"],
        required={
            "body_name": "string",
            "reference_body_name": "string",
            "target_argument": "request_path",
            "drop_distance_m": "number",
            "solved_distance_m": "number",
            "minimum_solved_steps": "integer",
            "control_period_s": "number",
            "control_steps_argument": "request_path",
            "physics_steps_per_control_step": "integer",
        },
        entities={"body_name": "body", "reference_body_name": "body"},
        request_paths=("target_argument", "control_steps_argument"),
    ),
    "mean_two_body_orbit_tracking_fraction": _spec(
        "Mean two-body source-frame orbit tracking fraction.",
        ["ratio"],
        required={
            "body_names": "string_array",
            "reference_body_name": "string",
            "orbit_center": "number_array",
            "radii_argument": "request_path",
            "period_argument": "request_path",
            "initial_phase_rad": "number",
            "maximum_tracking_error_m": "number",
            "minimum_source_height": "number",
            "source_height_offset_m": "number",
            "control_period_s": "number",
            "control_steps_argument": "request_path",
            "physics_steps_per_control_step": "integer",
        },
        entities={"body_names": "body_array", "reference_body_name": "body"},
        request_paths=(
            "radii_argument",
            "period_argument",
            "control_steps_argument",
        ),
    ),
    # Complete SO-101/Go2 worked references use semantic, parameterized
    # operators for their compound published criteria.  These operators reuse
    # the existing trusted implementation but expose neither a contract_id nor
    # a pre-written case selector to IVC.
    "so101_end_effector_regulation": _spec(
        "SO-101 terminal position regulation, hold, and side-effect gates.",
        ["m"],
        required={"side_effect_guard_profile": "string", "site_name": "string"},
        entities={"site_name": "site"},
        evaluation_mode="trusted_criterion_verdict",
    ),
    "so101_cartesian_path_tracking": _spec(
        "SO-101 ordered Cartesian path, cross-track, terminal hold, and side-effect gates.",
        ["m"],
        required={"side_effect_guard_profile": "string", "site_name": "string"},
        entities={"site_name": "site"},
        evaluation_mode="trusted_criterion_verdict",
    ),
    "so101_gripper_aperture_regulation": _spec(
        "SO-101 normalized aperture regulation, excursion, hold, and arm-stability gates.",
        ["ratio"],
        required={
            "side_effect_guard_profile": "string",
            "joint_name": "string",
            "closed_position": "number",
            "open_position": "number",
        },
        entities={"joint_name": "joint"},
        evaluation_mode="trusted_criterion_verdict",
    ),
    "so101_controlled_contact_approach": _spec(
        "SO-101 ordered precontact, ray, speed, penetration, contact, and unrelated-contact gates.",
        ["mixed"],
        required={
            "side_effect_guard_profile": "string",
            "site_name": "string",
            "tool_body_names": "string_array",
            "tool_geom_names": "string_array",
            "target_geom_names": "string_array",
            "precontact_gate": "string",
        },
        entities={
            "site_name": "site",
            "tool_body_names": "body_array",
            "tool_geom_names": "geom_array",
            "target_geom_names": "geom_array",
        },
        evaluation_mode="trusted_criterion_verdict",
    ),
    "so101_outbound_return_motion": _spec(
        "SO-101 ordered base-frame outbound/return holds and side-effect gates.",
        ["m"],
        required={
            "side_effect_guard_profile": "string",
            "site_name": "string",
            "base_body_name": "string",
        },
        entities={"site_name": "site", "base_body_name": "body"},
        evaluation_mode="trusted_criterion_verdict",
    ),
    "so101_wrist_roll_regulation": _spec(
        "SO-101 wrist-roll regulation, hold, and non-target-joint gates.",
        ["rad"],
        required={
            "side_effect_guard_profile": "string",
            "joint_name": "string",
            "target_request_key": "string",
            "target_tolerance_rad": "number",
            "continuous_hold_s": "number",
            "guarded_joint_names": "string_array",
            "guarded_joint_tolerance_rad": "number",
        },
        entities={"joint_name": "joint", "guarded_joint_names": "joint_array"},
        evaluation_mode="trusted_criterion_verdict",
    ),
    "go2_planar_twist_tracking": _spec(
        "Go2 final-window planar velocity, yaw-rate, direction, and support-integrity gates.",
        ["m/s"],
        required={"body_name": "string"},
        entities={"body_name": "body"},
        evaluation_mode="trusted_criterion_verdict",
    ),
    "go2_relative_pose_motion": _spec(
        "Go2 relative translation/yaw terminal hold and low-speed gates.",
        ["m"],
        required={"body_name": "string"},
        entities={"body_name": "body"},
        evaluation_mode="trusted_criterion_verdict",
    ),
    "go2_planar_path_tracking": _spec(
        "Go2 ordered planar path, cross-track, endpoint, and speed gates.",
        ["m"],
        required={"body_name": "string"},
        entities={"body_name": "body"},
        evaluation_mode="trusted_criterion_verdict",
    ),
    "go2_body_height_regulation": _spec(
        "Go2 height hold with attitude, displacement, yaw, and support gates.",
        ["m"],
        required={"body_name": "string"},
        entities={"body_name": "body"},
        evaluation_mode="trusted_criterion_verdict",
    ),
    "go2_stable_stance_recovery": _spec(
        "Go2 disturbance recovery, stance stability, drift, support, and forbidden-contact gates.",
        ["mixed"],
        required={
            "body_name": "string",
            "foot_geom_groups": "string_matrix",
            "floor_geom_names": "string_array",
            "forbidden_floor_geom_names": "string_array",
        },
        entities={
            "body_name": "body",
            "foot_geom_groups": "geom_matrix",
            "floor_geom_names": "geom_array",
            "forbidden_floor_geom_names": "geom_array",
        },
        evaluation_mode="trusted_criterion_verdict",
    ),
}


_REFERENCE_CONTRACT_IDS = {
    "so101_end_effector_regulation": "A1",
    "so101_cartesian_path_tracking": "A2",
    "so101_gripper_aperture_regulation": "A3",
    "so101_controlled_contact_approach": "A4",
    "so101_outbound_return_motion": "A5",
    "so101_wrist_roll_regulation": "A6",
    "go2_planar_twist_tracking": "G1",
    "go2_relative_pose_motion": "G2",
    "go2_planar_path_tracking": "G3",
    "go2_body_height_regulation": "G4",
    "go2_stable_stance_recovery": "G5",
}


def measurement_operator_catalog() -> dict[str, Any]:
    """Return the JSON-serializable trusted operator vocabulary."""

    return {
        "artifact_type": "measurement_operator_catalog",
        "schema_version": "1.0",
        "binding_fields": ["metric", "unit", "kind", "parameters"],
        "operators": [
            {"kind": kind, **copy.deepcopy(spec)}
            for kind, spec in sorted(_OPERATOR_SPECS.items())
        ],
    }


def trusted_reference_contract_id(kind: Any) -> str | None:
    """Resolve a semantic worked-reference operator to trusted implementation."""

    return _REFERENCE_CONTRACT_IDS.get(kind) if isinstance(kind, str) else None


def measurement_operator_evaluation_mode(kind: Any) -> str | None:
    spec = _OPERATOR_SPECS.get(kind) if isinstance(kind, str) else None
    return str(spec["evaluation_mode"]) if spec is not None else None


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _matches_type(value: Any, kind: str) -> bool:
    if kind in {"string", "request_path"}:
        return isinstance(value, str) and bool(value.strip())
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return _is_number(value)
    if kind == "string_array":
        return (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, str) and bool(item.strip()) for item in value)
        )
    if kind == "number_array":
        return isinstance(value, list) and bool(value) and all(_is_number(item) for item in value)
    if kind == "string_matrix":
        return (
            isinstance(value, list)
            and bool(value)
            and all(_matches_type(item, "string_array") for item in value)
        )
    if kind == "number_matrix":
        return (
            isinstance(value, list)
            and bool(value)
            and all(_matches_type(item, "number_array") for item in value)
        )
    if kind == "object_array":
        return isinstance(value, list) and bool(value) and all(isinstance(item, Mapping) for item in value)
    return False


def _assert_finite_json(value: Any, *, where: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise MeasurementOperatorError(f"{where} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise MeasurementOperatorError(f"{where} contains a non-string key")
            _assert_finite_json(child, where=f"{where}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_finite_json(child, where=f"{where}[{index}]")
        return
    raise MeasurementOperatorError(f"{where} contains a non-JSON value")


def _schema_at_request_path(request_schema: Mapping[str, Any], path: str) -> Mapping[str, Any]:
    parts = path.split(".")
    if len(parts) < 2 or parts[0] != "request" or any(not part for part in parts):
        raise MeasurementOperatorError(
            f"request path {path!r} must be rooted at request.<field>"
        )
    current: Any = request_schema
    for part in parts[1:]:
        if not isinstance(current, Mapping) or current.get("type") != "object":
            raise MeasurementOperatorError(
                f"request path {path!r} traverses a non-object schema"
            )
        properties = current.get("properties")
        if not isinstance(properties, Mapping) or part not in properties:
            raise MeasurementOperatorError(
                f"request path {path!r} is absent from the sealed request schema"
            )
        current = properties[part]
    if not isinstance(current, Mapping):
        raise MeasurementOperatorError(
            f"request path {path!r} does not resolve to a schema"
        )
    return current


def _validate_request_value_schema(
    schema: Mapping[str, Any],
    expected: str,
    *,
    path: str,
) -> None:
    schema_type = schema.get("type")
    if expected in {
        "number",
        "world_m_number",
        "world_direction_number",
        "frame_m_number",
        "frame_direction_number",
        "m_number",
        "bounded_m_number",
        "rad_number",
    }:
        if schema_type not in {"number", "integer"}:
            raise MeasurementOperatorError(
                f"request path {path!r} must resolve to a numeric scalar schema"
            )
        if expected == "world_m_number" and (
            schema.get("unit") != "m" or schema.get("frame") != "world"
        ):
            raise MeasurementOperatorError(
                f"request path {path!r} must declare unit='m' and frame='world'"
            )
        if expected == "world_direction_number" and (
            schema.get("unit")
            not in {"fraction", "ratio", "unitless", "none", "1"}
            or schema.get("frame") != "world"
        ):
            raise MeasurementOperatorError(
                f"request path {path!r} must declare a dimensionless unit and "
                "frame='world'"
            )
        if expected == "frame_m_number" and (
            schema.get("unit") != "m"
            or not isinstance(schema.get("frame"), str)
            or not str(schema["frame"]).strip()
        ):
            raise MeasurementOperatorError(
                f"request path {path!r} must declare unit='m' and a non-empty frame"
            )
        if expected == "frame_direction_number" and (
            schema.get("unit")
            not in {"dimensionless", "fraction", "ratio", "unitless", "none", "1"}
            or not isinstance(schema.get("frame"), str)
            or not str(schema["frame"]).strip()
        ):
            raise MeasurementOperatorError(
                f"request path {path!r} must declare a dimensionless unit and a "
                "non-empty frame"
            )
        if expected in {"m_number", "bounded_m_number"} and schema.get("unit") != "m":
            raise MeasurementOperatorError(
                f"request path {path!r} must declare unit='m'"
            )
        if expected == "bounded_m_number" and (
            not _is_number(schema.get("minimum"))
            or not _is_number(schema.get("maximum"))
            or float(schema["minimum"]) >= float(schema["maximum"])
        ):
            raise MeasurementOperatorError(
                f"request path {path!r} must declare finite increasing bounds"
            )
        if expected == "rad_number" and schema.get("unit") != "rad":
            raise MeasurementOperatorError(
                f"request path {path!r} must declare unit='rad'"
            )
        return
    if expected == "number_array_3":
        items = schema.get("items")
        if (
            schema_type != "array"
            or schema.get("minItems") != 3
            or schema.get("maxItems") != 3
            or not isinstance(items, Mapping)
            or items.get("type") not in {"number", "integer"}
        ):
            raise MeasurementOperatorError(
                f"request path {path!r} must resolve to a numeric array schema "
                "with exactly three items"
            )
        return
    if expected in {"axis_name", "world_axis_name"}:
        values = schema.get("enum")
        if (
            schema_type != "string"
            or not isinstance(values, list)
            or not values
            or any(value not in {"x", "y", "z"} for value in values)
        ):
            raise MeasurementOperatorError(
                f"request path {path!r} must resolve to a string schema restricted "
                "to x/y/z"
            )
        if expected == "world_axis_name" and schema.get("frame") != "world":
            raise MeasurementOperatorError(
                f"request path {path!r} must declare frame='world'"
            )
        return
    raise MeasurementOperatorError(
        f"trusted operator declares unsupported request value type {expected!r}"
    )


def _validate_xyz_sibling_paths(
    parameters: Mapping[str, Any],
    fields: Sequence[str],
    *,
    label: str,
    suffixes: Sequence[str] = ("x", "y", "z"),
) -> None:
    paths = [str(parameters[field]).split(".") for field in fields]
    if (
        len({str(parameters[field]) for field in fields}) != 3
        or any(len(parts) < 3 for parts in paths)
        or [parts[-1] for parts in paths] != list(suffixes)
        or any(parts[:-1] != paths[0][:-1] for parts in paths[1:])
    ):
        raise MeasurementOperatorError(
            f"{label} request paths must be distinct sibling x/y/z fields"
        )


def _validate_operator_request_roles(
    kind: str,
    parameters: Mapping[str, Any],
) -> None:
    if kind in {"final_geom_pair_distance_error", "final_geom_pair_distance"} and (
        parameters.get("geom_a_name") == parameters.get("geom_b_name")
    ):
        raise MeasurementOperatorError(
            f"{kind} requires two distinct geoms"
        )
    if kind in {
        "final_body_xyz_position_error",
        "final_site_frame_xyz_position_error",
        "final_body_frame_xyz_position_error",
    }:
        _validate_xyz_sibling_paths(
            parameters,
            (
                "target_x_argument",
                "target_y_argument",
                "target_z_argument",
            ),
            label="body target",
        )
    elif kind == "accumulated_body_arc_angle_error":
        _validate_xyz_sibling_paths(
            parameters,
            (
                "center_x_argument",
                "center_y_argument",
                "center_z_argument",
            ),
            label="arc center",
        )
    elif kind == "final_body_directional_displacement_error":
        _validate_xyz_sibling_paths(
            parameters,
            (
                "direction_x_argument",
                "direction_y_argument",
                "direction_z_argument",
            ),
            label="body direction",
        )
    elif kind in {
        "site_frame_xyz_directional_displacement",
    }:
        _validate_xyz_sibling_paths(
            parameters,
            (
                "direction_x_argument",
                "direction_y_argument",
                "direction_z_argument",
            ),
            label="frame direction",
            suffixes=("dx", "dy", "dz"),
        )
    elif kind in {
        "accumulated_site_frame_axis_arc_angle_error",
    }:
        _validate_xyz_sibling_paths(
            parameters,
            (
                "center_x_argument",
                "center_y_argument",
                "center_z_argument",
            ),
            label="arc center",
        )
        _validate_xyz_sibling_paths(
            parameters,
            (
                "axis_x_argument",
                "axis_y_argument",
                "axis_z_argument",
            ),
            label="arc axis",
            suffixes=("ax", "ay", "az"),
        )
    if (
        kind
        in {
            "final_body_frame_xyz_position_error",
        }
        and parameters.get("body_name") == parameters.get("reference_body_name")
    ):
        raise MeasurementOperatorError(
            f"{kind} measured body and reference body must be distinct"
        )


def _validate_common_request_frame(
    kind: str,
    request_path_schemas: Mapping[str, Mapping[str, Any]],
) -> None:
    frame_fields: tuple[str, ...] = ()
    if kind in {
        "final_site_frame_xyz_position_error",
        "final_body_frame_xyz_position_error",
    }:
        frame_fields = (
            "target_x_argument",
            "target_y_argument",
            "target_z_argument",
        )
    elif kind in {
        "site_frame_xyz_directional_displacement",
    }:
        frame_fields = (
            "direction_x_argument",
            "direction_y_argument",
            "direction_z_argument",
        )
    elif kind in {
        "accumulated_site_frame_axis_arc_angle_error",
    }:
        frame_fields = (
            "center_x_argument",
            "center_y_argument",
            "center_z_argument",
            "axis_x_argument",
            "axis_y_argument",
            "axis_z_argument",
        )
    if not frame_fields:
        return
    frames = [request_path_schemas[field].get("frame") for field in frame_fields]
    if (
        any(not isinstance(frame, str) or not frame.strip() for frame in frames)
        or len(set(frames)) != 1
    ):
        raise MeasurementOperatorError(
            f"{kind} coordinate request fields must declare one common non-empty frame"
        )


def _validate_reference_body_frame(
    kind: str,
    parameters: Mapping[str, Any],
    request_path_schemas: Mapping[str, Mapping[str, Any]],
    *,
    scene_path: Path | None,
    scene_entities: Mapping[str, Any] | None,
) -> None:
    if kind not in {
        "final_site_frame_xyz_position_error",
        "final_body_frame_xyz_position_error",
        "site_frame_xyz_directional_displacement",
        "accumulated_site_frame_axis_arc_angle_error",
    }:
        return
    if scene_entities is None:
        if scene_path is None:
            raise MeasurementOperatorError(
                "selected scene frame aliases are unavailable for measurement audit"
            )
        scene_entities = inspect_scene_entities(scene_path)
    aliases = scene_entities.get("frame_aliases")
    if not isinstance(aliases, Mapping) or any(
        not isinstance(alias, str) or not isinstance(body, str)
        for alias, body in aliases.items()
    ):
        raise MeasurementOperatorError(
            "selected scene has no trusted frame_aliases catalog"
        )
    frame_schema = next(iter(request_path_schemas.values()), None)
    declared_frame = frame_schema.get("frame") if isinstance(frame_schema, Mapping) else None
    expected_reference = aliases.get(declared_frame)
    if not isinstance(expected_reference, str) or not expected_reference:
        raise MeasurementOperatorError(
            f"request frame {declared_frame!r} has no trusted selected-scene body alias"
        )
    actual_reference = str(parameters["reference_body_name"])
    if actual_reference != expected_reference:
        raise MeasurementOperatorError(
            f"request frame {declared_frame!r} resolves to reference body "
            f"{expected_reference!r}, not {actual_reference!r}"
        )
    parent_names = scene_entities.get("body_parent_names")
    joint_counts = scene_entities.get("body_joint_counts")
    descendant_joint_counts = scene_entities.get("body_descendant_joint_counts")
    site_body_names = scene_entities.get("site_body_names")
    if not all(
        isinstance(value, Mapping)
        for value in (
            parent_names,
            joint_counts,
            descendant_joint_counts,
            site_body_names,
        )
    ):
        raise MeasurementOperatorError(
            "selected scene lacks trusted kinematic frame metadata"
        )

    def ancestors(body_name: str) -> list[str]:
        path: list[str] = []
        current = body_name
        visited: set[str] = set()
        while current != "world":
            if current in visited or current not in parent_names:
                raise MeasurementOperatorError(
                    f"selected scene has invalid ancestry for body {body_name!r}"
                )
            visited.add(current)
            path.append(current)
            parent = parent_names[current]
            if not isinstance(parent, str) or not parent:
                raise MeasurementOperatorError(
                    f"selected scene has invalid parent for body {current!r}"
                )
            current = parent
        path.append("world")
        return path

    reference_path = ancestors(actual_reference)
    subtree_joints = descendant_joint_counts.get(actual_reference)
    if (
        isinstance(subtree_joints, bool)
        or not isinstance(subtree_joints, int)
        or subtree_joints <= 0
    ):
        raise MeasurementOperatorError(
            f"reference body {actual_reference!r} does not root an articulated subtree"
        )

    if "site_name" in parameters:
        measured_body = site_body_names.get(str(parameters["site_name"]))
    else:
        measured_body = parameters.get("body_name")
    if not isinstance(measured_body, str) or not measured_body:
        raise MeasurementOperatorError(
            f"{kind} cannot resolve the measured entity's owning body"
        )
    measured_path = ancestors(measured_body)
    reference_ancestors = set(reference_path)
    common = next(
        (body for body in measured_path if body in reference_ancestors), None
    )
    if common is None:
        raise MeasurementOperatorError(
            "measured entity and reference body have no common kinematic ancestor"
        )
    relative_bodies = measured_path[: measured_path.index(common)]
    relative_bodies.extend(reference_path[: reference_path.index(common)])
    if any(body not in joint_counts for body in relative_bodies):
        raise MeasurementOperatorError(
            "selected scene lacks trusted joint metadata for the relative frame path"
        )
    path_joint_count = sum(int(joint_counts[body]) for body in relative_bodies)
    if path_joint_count <= 0:
        raise MeasurementOperatorError(
            f"measured entity is rigid relative to reference body {actual_reference!r}"
        )


def _scene_model(scene_path: Path) -> Any:
    try:
        import mujoco

        return mujoco.MjModel.from_xml_path(str(scene_path))
    except Exception as exc:
        raise MeasurementOperatorError(
            f"cannot load selected MuJoCo scene {scene_path}: {exc}"
        ) from exc


def inspect_scene_entities(scene_path: str | Path) -> dict[str, Any]:
    """Return the named entities available to trusted operators in one scene."""

    model = _scene_model(Path(scene_path).resolve())
    import mujoco

    object_types = {
        "bodies": (mujoco.mjtObj.mjOBJ_BODY, int(model.nbody)),
        "sites": (mujoco.mjtObj.mjOBJ_SITE, int(model.nsite)),
        "joints": (mujoco.mjtObj.mjOBJ_JOINT, int(model.njnt)),
        "geoms": (mujoco.mjtObj.mjOBJ_GEOM, int(model.ngeom)),
        "actuators": (mujoco.mjtObj.mjOBJ_ACTUATOR, int(model.nu)),
        "keyframes": (mujoco.mjtObj.mjOBJ_KEY, int(model.nkey)),
    }
    result: dict[str, Any] = {}
    for field, (object_type, count) in object_types.items():
        names: list[str] = []
        for index in range(count):
            name = mujoco.mj_id2name(model, object_type, index)
            if isinstance(name, str) and name:
                names.append(name)
            elif field == "geoms":
                # Trusted evidence uses this stable alias for unnamed geoms.
                names.append(f"geom_{index}")
        result[field] = sorted(names)
    joint_units: dict[str, str] = {}
    joint_ranges: dict[str, list[float]] = {}
    for joint_id in range(int(model.njnt)):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, joint_id)
        if not isinstance(name, str) or not name:
            continue
        joint_type = int(model.jnt_type[joint_id])
        if joint_type == int(mujoco.mjtJoint.mjJNT_HINGE):
            joint_units[name] = "rad"
        elif joint_type == int(mujoco.mjtJoint.mjJNT_SLIDE):
            joint_units[name] = "m"
        if name in joint_units and bool(model.jnt_limited[joint_id]):
            lower = float(model.jnt_range[joint_id][0])
            upper = float(model.jnt_range[joint_id][1])
            if math.isfinite(lower) and math.isfinite(upper) and lower < upper:
                joint_ranges[name] = [lower, upper]
    result["joint_units"] = dict(sorted(joint_units.items()))
    result["joint_ranges"] = dict(sorted(joint_ranges.items()))
    body_names = {
        body_id: mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id)
        for body_id in range(int(model.nbody))
    }
    aliases = {
        str(name): str(name)
        for name in body_names.values()
        if isinstance(name, str) and name
    }
    top_level_bodies = [
        body_id
        for body_id in range(1, int(model.nbody))
        if int(model.body_parentid[body_id]) == 0
    ]

    def descendant_joint_count(root_body_id: int) -> int:
        total = 0
        for body_id in range(1, int(model.nbody)):
            ancestor = body_id
            while ancestor and ancestor != root_body_id:
                ancestor = int(model.body_parentid[ancestor])
            if ancestor == root_body_id:
                total += int(model.body_jntnum[body_id])
        return total

    body_parent_names: dict[str, str] = {}
    body_joint_counts: dict[str, int] = {}
    body_descendant_joint_counts: dict[str, int] = {}
    for body_id in range(1, int(model.nbody)):
        name = body_names[body_id]
        if not isinstance(name, str) or not name:
            continue
        parent_id = int(model.body_parentid[body_id])
        parent_name = body_names.get(parent_id) if parent_id else "world"
        if not isinstance(parent_name, str) or not parent_name:
            # A named frame with an unnamed parent cannot be safely audited
            # using the model-facing metadata exposed to IVC.
            continue
        body_parent_names[name] = parent_name
        body_joint_counts[name] = int(model.body_jntnum[body_id])
        body_descendant_joint_counts[name] = descendant_joint_count(body_id)
    site_body_names: dict[str, str] = {}
    for site_id in range(int(model.nsite)):
        site_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_SITE, site_id)
        owner_name = body_names.get(int(model.site_bodyid[site_id]))
        if (
            isinstance(site_name, str)
            and site_name
            and isinstance(owner_name, str)
            and owner_name
        ):
            site_body_names[site_name] = owner_name

    ranked_roots = sorted(
        (
            (descendant_joint_count(body_id), body_id)
            for body_id in top_level_bodies
        ),
        reverse=True,
    )
    if ranked_roots and ranked_roots[0][0] > 0 and (
        len(ranked_roots) == 1 or ranked_roots[0][0] > ranked_roots[1][0]
    ):
        robot_base_name = body_names[ranked_roots[0][1]]
        if isinstance(robot_base_name, str) and robot_base_name:
            aliases["robot_base"] = robot_base_name
    result["frame_aliases"] = dict(sorted(aliases.items()))
    result["body_parent_names"] = dict(sorted(body_parent_names.items()))
    result["body_joint_counts"] = dict(sorted(body_joint_counts.items()))
    result["body_descendant_joint_counts"] = dict(
        sorted(body_descendant_joint_counts.items())
    )
    result["site_body_names"] = dict(sorted(site_body_names.items()))
    return result


def _entity_names(value: Any, entity_kind: str) -> list[str]:
    if entity_kind.endswith("_array"):
        return [str(item) for item in value]
    if entity_kind.endswith("_matrix"):
        return [str(item) for group in value for item in group]
    return [str(value)]


def _validate_entities(
    parameters: Mapping[str, Any],
    entity_specs: Mapping[str, str],
    *,
    scene_path: Path | None,
    scene_entities: Mapping[str, Any] | None,
) -> None:
    relevant = {
        field: entity_kind
        for field, entity_kind in entity_specs.items()
        if field in parameters
    }
    if not relevant:
        return
    if scene_entities is None:
        if scene_path is None:
            raise MeasurementOperatorError(
                "selected scene entities are unavailable for measurement audit"
            )
        scene_entities = inspect_scene_entities(scene_path)
    entity_fields = {
        "body": "bodies",
        "site": "sites",
        "joint": "joints",
        "geom": "geoms",
    }
    for field, declared_kind in relevant.items():
        if field == "tool_geom_names" and "tool_body_names" in parameters:
            # The runner deterministically expands the named body subtrees to
            # their real scene geoms before execution.
            continue
        base_kind = declared_kind.split("_", 1)[0]
        raw_available = scene_entities.get(entity_fields[base_kind])
        if not isinstance(raw_available, list) or any(
            not isinstance(item, str) for item in raw_available
        ):
            raise MeasurementOperatorError(
                f"selected scene has no valid {entity_fields[base_kind]} catalog"
            )
        available = set(raw_available)
        for name in _entity_names(parameters[field], declared_kind):
            if name not in available:
                raise MeasurementOperatorError(
                    f"measurement parameters.{field} references unknown "
                    f"{base_kind} {name!r} in selected scene"
                )


def _validate_final_joint_position_conversion(
    kind: str,
    unit: str,
    parameters: Mapping[str, Any],
    target_schema: Mapping[str, Any] | None,
    *,
    scene_path: Path | None,
    scene_entities: Mapping[str, Any] | None,
) -> None:
    if kind != "final_joint_position_error":
        return
    if scene_entities is None:
        if scene_path is None:
            raise MeasurementOperatorError(
                "selected scene joint units are unavailable for measurement audit"
            )
        scene_entities = inspect_scene_entities(scene_path)
    raw_joint_units = scene_entities.get("joint_units")
    if not isinstance(raw_joint_units, Mapping) or any(
        not isinstance(name, str) or value not in {"rad", "m"}
        for name, value in raw_joint_units.items()
    ):
        raise MeasurementOperatorError(
            "selected scene has no valid joint_units catalog"
        )
    joint_name = str(parameters["joint_name"])
    expected_unit = raw_joint_units.get(joint_name)
    if expected_unit is None:
        raise MeasurementOperatorError(
            "final_joint_position_error requires a scalar hinge or slide joint; "
            f"{joint_name!r} is not one"
        )
    if unit != expected_unit:
        raise MeasurementOperatorError(
            f"final_joint_position_error for joint {joint_name!r} must use "
            f"unit {expected_unit!r}, not {unit!r}"
        )
    scale = float(parameters.get("target_scale", 1.0))
    offset = float(parameters.get("target_offset", 0.0))
    if scale <= 0.0:
        raise MeasurementOperatorError(
            "final_joint_position_error target_scale must be positive"
        )
    if not isinstance(target_schema, Mapping):
        raise MeasurementOperatorError(
            "final_joint_position_error target_argument has no sealed schema"
        )
    request_unit = target_schema.get("unit")
    if request_unit == unit:
        if scale != 1.0 or offset != 0.0:
            raise MeasurementOperatorError(
                "same-unit joint targets require target_scale=1 and target_offset=0"
            )
        return
    if request_unit not in {"fraction", "ratio", "unitless", "none", "1"}:
        raise MeasurementOperatorError(
            "non-identity joint target conversion requires a dimensionless sealed "
            "request field"
        )
    evidence_refs = target_schema.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs or any(
        not isinstance(ref, Mapping)
        or not isinstance(ref.get("source_id"), str)
        or not ref["source_id"].strip()
        or not isinstance(ref.get("specific_reference"), str)
        or not ref["specific_reference"].strip()
        for ref in evidence_refs
    ):
        raise MeasurementOperatorError(
            "joint target conversion requires evidence-backed sealed request bounds"
        )
    request_lower = target_schema.get("minimum")
    request_upper = target_schema.get("maximum")
    if not _is_number(request_lower) or not _is_number(request_upper):
        raise MeasurementOperatorError(
            "joint target conversion requires finite sealed minimum and maximum"
        )
    request_lower = float(request_lower)
    request_upper = float(request_upper)
    if request_lower >= request_upper:
        raise MeasurementOperatorError(
            "joint target conversion requires increasing sealed request bounds"
        )
    raw_joint_ranges = scene_entities.get("joint_ranges")
    raw_joint_range = (
        raw_joint_ranges.get(joint_name)
        if isinstance(raw_joint_ranges, Mapping)
        else None
    )
    if (
        not isinstance(raw_joint_range, list)
        or len(raw_joint_range) != 2
        or not all(_is_number(value) for value in raw_joint_range)
        or float(raw_joint_range[0]) >= float(raw_joint_range[1])
    ):
        raise MeasurementOperatorError(
            "joint target conversion requires a finite selected-scene joint range"
        )
    joint_lower = float(raw_joint_range[0])
    joint_upper = float(raw_joint_range[1])
    expected_scale = (joint_upper - joint_lower) / (
        request_upper - request_lower
    )
    expected_offset = joint_lower - request_lower * expected_scale
    if not math.isclose(scale, expected_scale, rel_tol=1.0e-9, abs_tol=1.0e-12):
        raise MeasurementOperatorError(
            "target_scale does not match the evidence-backed sealed-to-joint "
            f"conversion ({expected_scale!r})"
        )
    if not math.isclose(offset, expected_offset, rel_tol=1.0e-9, abs_tol=1.0e-12):
        raise MeasurementOperatorError(
            "target_offset does not match the evidence-backed sealed-to-joint "
            f"conversion ({expected_offset!r})"
        )


def audit_inline_measurement_binding(
    binding: Mapping[str, Any],
    *,
    criterion: Mapping[str, Any],
    request_schema: Mapping[str, Any],
    scene_path: str | Path | None = None,
    scene_entities: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and canonicalize an IVC-authored trusted measurement binding."""

    if not isinstance(binding, Mapping):
        raise MeasurementOperatorError("measurement_binding must be an object")
    allowed_fields = {"metric", "unit", "kind", "parameters"}
    if set(binding) != allowed_fields:
        missing = sorted(allowed_fields - set(binding))
        extra = sorted(set(binding) - allowed_fields)
        raise MeasurementOperatorError(
            f"measurement_binding fields are invalid; missing={missing}, extra={extra}"
        )
    for field in ("metric", "unit", "kind"):
        if not isinstance(binding.get(field), str) or not str(binding[field]).strip():
            raise MeasurementOperatorError(
                f"measurement_binding.{field} must be a non-empty string"
            )
    if binding["metric"] != criterion.get("metric"):
        raise MeasurementOperatorError(
            "measurement_binding.metric must equal the sealed criterion metric"
        )
    if binding["unit"] != criterion.get("unit"):
        raise MeasurementOperatorError(
            "measurement_binding.unit must equal the sealed criterion unit"
        )
    kind = str(binding["kind"])
    if kind == "b1_contract":
        raise MeasurementOperatorError(
            "capability-v2 measurement_binding cannot use b1_contract"
        )
    spec = _OPERATOR_SPECS.get(kind)
    if spec is None:
        raise MeasurementOperatorError(
            f"measurement_binding.kind {kind!r} is not in the trusted operator catalog"
        )
    if binding["unit"] not in spec["output_units"]:
        raise MeasurementOperatorError(
            f"measurement_binding.unit {binding['unit']!r} is incompatible with "
            f"trusted operator {kind!r}"
        )
    parameters = binding.get("parameters")
    if not isinstance(parameters, Mapping):
        raise MeasurementOperatorError("measurement_binding.parameters must be an object")
    schema = spec["parameter_schema"]
    required = set(schema["required"])
    properties = schema["properties"]
    missing = sorted(required - set(parameters))
    extra = sorted(set(parameters) - set(properties))
    if missing or extra:
        raise MeasurementOperatorError(
            f"measurement_binding.parameters are invalid; missing={missing}, extra={extra}"
        )
    for field, value in parameters.items():
        expected = str(properties[field]["type"])
        if not _matches_type(value, expected):
            raise MeasurementOperatorError(
                f"measurement_binding.parameters.{field} must have type {expected}"
            )
    _assert_finite_json(parameters, where="measurement_binding.parameters")
    request_path_schemas = {
        field: _schema_at_request_path(request_schema, str(parameters[field]))
        for field in spec["request_path_parameters"]
    }
    for field, expected in spec["request_value_types"].items():
        _validate_request_value_schema(
            request_path_schemas[field],
            str(expected),
            path=str(parameters[field]),
        )
    _validate_operator_request_roles(kind, parameters)
    _validate_common_request_frame(kind, request_path_schemas)
    resolved_scene_path = (
        Path(scene_path).resolve() if scene_path is not None else None
    )
    resolved_scene_entities = scene_entities
    if (
        resolved_scene_entities is None
        and resolved_scene_path is not None
        and spec["entity_parameters"]
    ):
        resolved_scene_entities = inspect_scene_entities(resolved_scene_path)
    _validate_entities(
        parameters,
        spec["entity_parameters"],
        scene_path=resolved_scene_path,
        scene_entities=resolved_scene_entities,
    )
    _validate_reference_body_frame(
        kind,
        parameters,
        request_path_schemas,
        scene_path=resolved_scene_path,
        scene_entities=resolved_scene_entities,
    )
    _validate_final_joint_position_conversion(
        kind,
        str(binding["unit"]),
        parameters,
        request_path_schemas.get("target_argument"),
        scene_path=resolved_scene_path,
        scene_entities=resolved_scene_entities,
    )
    return copy.deepcopy(dict(binding))


__all__ = [
    "MeasurementOperatorError",
    "audit_inline_measurement_binding",
    "inspect_scene_entities",
    "measurement_operator_catalog",
    "measurement_operator_evaluation_mode",
    "trusted_reference_contract_id",
]
