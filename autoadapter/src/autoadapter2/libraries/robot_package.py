"""Admission checks for one self-contained Direct-MuJoCo robot package."""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RobotPackageError(ValueError):
    """Raised when a robot package is incomplete or internally inconsistent."""


_SUPPORTED_BINDING_KINDS = {
    "final_site_position_error",
    "final_site_axis_error",
    "final_weighted_site_position_error",
    "final_body_position_error",
    "final_joint_position_error",
    "joint_range",
    "body_height",
    "minimum_body_height",
    "body_planar_displacement",
    "body_axis_displacement",
    "body_directional_displacement",
    "body_directional_progress_until_corridor_exit",
    "mean_body_planar_speed",
    "body_yaw_change_deg",
    "mean_body_heading_error_deg",
    "named_bodies_axis_completion",
    "mean_body_yaw_rate",
    "ordered_body_waypoint_completion_ratio",
    "ordered_body_waypoint_completion_time",
    "ordered_body_axis_gate_completion_ratio",
    "minimum_body_point_clearance",
    "named_geom_contact_step_count",
    "contact_sample_count",
    "physics_step_count",
    "in_hand_object_pattern_success",
    "final_concatenated_site_position_error",
    "final_body_position_offset_error",
    "final_body_quaternion_error",
    "final_maximum_joint_position_error",
    "final_wrapped_joint_position_error",
    "maximum_joint_linear_trajectory_error",
    "body_target_solved_sample_count",
    "body_target_drop_event_count",
    "mean_two_body_orbit_tracking_fraction",
}
_REQUIRED_GUARD_KINDS = {
    "actuator_and_physics_step_required",
    "no_direct_state_write",
    "canonical_model_data",
}
_SUPPORTED_GUARD_KINDS = _REQUIRED_GUARD_KINDS | {
    "complete_video",
    "named_geom_contact_pair_required",
    "named_joints_remain_near_reset",
    "terminal_body_stability",
}

_LEAP_BINDING_UNITS = {
    "in_hand_object_pattern_success": "trial",
    "final_concatenated_site_position_error": "m",
    "final_body_position_offset_error": "m",
    "final_body_quaternion_error": "rad",
    "final_maximum_joint_position_error": "rad",
    "final_wrapped_joint_position_error": "rad",
    "maximum_joint_linear_trajectory_error": "rad",
    "body_target_solved_sample_count": "control_step",
    "body_target_drop_event_count": "event",
    "mean_two_body_orbit_tracking_fraction": "ratio",
}

_LEAP_BINDING_REQUIRED_PARAMETERS = {
    "in_hand_object_pattern_success": {
        "body_name",
        "reference_body_name",
        "object_geom_names",
        "required_robot_geom_groups",
        "minimum_contact_steps",
        "motion_kind",
    },
    "final_concatenated_site_position_error": {
        "site_names",
        "reference_body_name",
        "target_argument",
        "physics_steps_per_control_step",
        "control_steps_argument",
    },
    "final_body_position_offset_error": {
        "body_name",
        "reference_body_name",
        "target_argument",
        "orientation_target_argument",
        "maximum_orientation_error_rad",
        "physics_steps_per_control_step",
        "control_steps_argument",
    },
    "final_body_quaternion_error": {
        "body_name",
        "reference_body_name",
        "target_argument",
        "position_target_argument",
        "maximum_position_error_m",
        "physics_steps_per_control_step",
        "control_steps_argument",
    },
    "final_maximum_joint_position_error": {
        "joint_names",
        "target_argument",
        "physics_steps_per_control_step",
        "control_steps_argument",
    },
    "final_wrapped_joint_position_error": {
        "joint_name",
        "target_argument",
        "physics_steps_per_control_step",
        "control_steps_argument",
    },
    "maximum_joint_linear_trajectory_error": {
        "joint_name",
        "velocity_argument",
        "control_period_s",
        "control_steps_argument",
        "physics_steps_per_control_step",
    },
    "body_target_solved_sample_count": {
        "body_name",
        "reference_body_name",
        "target_argument",
        "solved_distance_m",
        "drop_distance_m",
        "control_period_s",
        "control_steps_argument",
        "physics_steps_per_control_step",
    },
    "body_target_drop_event_count": {
        "body_name",
        "reference_body_name",
        "target_argument",
        "drop_distance_m",
        "solved_distance_m",
        "minimum_solved_steps",
        "control_period_s",
        "control_steps_argument",
        "physics_steps_per_control_step",
    },
    "mean_two_body_orbit_tracking_fraction": {
        "body_names",
        "reference_body_name",
        "orbit_center",
        "radii_argument",
        "period_argument",
        "maximum_tracking_error_m",
        "minimum_source_height",
        "source_height_offset_m",
        "control_period_s",
        "control_steps_argument",
        "physics_steps_per_control_step",
    },
}

_LEAP_METRIC_BINDING_KINDS = {
    "concatenated_fingertip_cartesian_l2_error": {
        "final_concatenated_site_position_error"
    },
    "block_target_euclidean_position_error": {"final_body_position_offset_error"},
    "block_target_shortest_quaternion_angle_error": {"final_body_quaternion_error"},
    "maximum_absolute_joint_position_error": {"final_maximum_joint_position_error"},
    "absolute_wrapped_fixture_target_angle_error": {
        "final_wrapped_joint_position_error"
    },
    "maximum_absolute_unbounded_fixture_target_angle_error": {
        "maximum_joint_linear_trajectory_error"
    },
    "object_hold_solved_control_step_count": {"body_target_solved_sample_count"},
    "object_hold_drop_event_count": {"body_target_drop_event_count"},
    "mean_two_ball_solved_fraction": {"mean_two_body_orbit_tracking_fraction"},
}

_LEAP_METRIC_CONTRACTS = {
    "concatenated_fingertip_cartesian_l2_error": (
        "terminal_step",
        "all_four_fingertips",
    ),
    "block_target_euclidean_position_error": (
        "terminal_step",
        "same_state_conjunction",
    ),
    "block_target_shortest_quaternion_angle_error": (
        "terminal_step",
        "same_state_conjunction",
    ),
    "maximum_absolute_joint_position_error": (
        "terminal_step",
        "maximum_over_all_16_joints",
    ),
    "absolute_wrapped_fixture_target_angle_error": (
        "terminal_step",
        "single_trial",
    ),
    "maximum_absolute_unbounded_fixture_target_angle_error": (
        "continuous",
        "maximum_over_control_steps",
    ),
    "object_hold_solved_control_step_count": (
        "fixed_horizon",
        "count_successful_steps",
    ),
    "object_hold_drop_event_count": ("fixed_horizon", "count_events"),
    "mean_two_ball_solved_fraction": (
        "fixed_horizon",
        "mean_over_control_steps",
    ),
}

_LEAP_MOVABLE_BODY_FIELDS = {
    "in_hand_object_pattern_success": ("body_name",),
    "final_body_position_offset_error": ("body_name",),
    "final_body_quaternion_error": ("body_name",),
    "body_target_solved_sample_count": ("body_name",),
    "body_target_drop_event_count": ("body_name",),
    "mean_two_body_orbit_tracking_fraction": ("body_names",),
}


@dataclass(frozen=True)
class RobotPackage:
    """A package that passed the minimum mainline runnable-input checks."""

    root: Path
    robot_configuration_id: str
    package_version: str
    snapshot_id: str
    morphology: dict[str, Any]
    sources: tuple[dict[str, Any], ...]
    tasks: tuple[dict[str, Any], ...]
    mjcf_path: Path
    skeleton_dir: Path
    reference_driver: Path
    private_dir: Path


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RobotPackageError(f"required package file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RobotPackageError(f"invalid JSON in {path}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise RobotPackageError(f"{path} must contain one JSON object")
    return value


def _required_text(value: dict[str, Any], field: str, *, where: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise RobotPackageError(f"{where}.{field} must be a non-empty string")
    return item.strip()


def _required_list(value: dict[str, Any], field: str, *, where: str) -> list[Any]:
    item = value.get(field)
    if not isinstance(item, list) or not item:
        raise RobotPackageError(f"{where}.{field} must be a non-empty list")
    return item


def _validate_public_affordances(morphology: dict[str, Any], *, where: str) -> None:
    affordances = morphology.get("public_affordances")
    if not isinstance(affordances, dict):
        raise RobotPackageError(f"{where}.public_affordances must be an object")
    for field in ("actions", "observations"):
        values = _required_list(
            affordances,
            field,
            where=f"{where}.public_affordances",
        )
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise RobotPackageError(
                f"{where}.public_affordances.{field} must contain non-empty strings"
            )
        if len(values) != len(set(values)):
            raise RobotPackageError(
                f"{where}.public_affordances.{field} must not contain duplicates"
            )


def _validate_identity(
    document: dict[str, Any],
    *,
    path: Path,
    robot_configuration_id: str,
    package_version: str,
) -> None:
    if document.get("robot_configuration_id") != robot_configuration_id:
        raise RobotPackageError(f"robot identity mismatch in {path}")
    if document.get("package_version") != package_version:
        raise RobotPackageError(f"package version mismatch in {path}")


def _validate_sources(
    document: dict[str, Any],
    *,
    path: Path,
) -> tuple[dict[str, Any], ...]:
    values = _required_list(document, "sources", where=path.name)
    sources: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for index, value in enumerate(values):
        where = f"{path.name}.sources[{index}]"
        if not isinstance(value, dict):
            raise RobotPackageError(f"{where} must be an object")
        source_id = _required_text(value, "source_id", where=where)
        if source_id in source_ids:
            raise RobotPackageError(f"duplicate source_id {source_id!r}")
        for field in (
            "title",
            "organization",
            "version_or_date",
            "locator",
            "specific_reference",
        ):
            _required_text(value, field, where=where)
        locator = str(value["locator"])
        if not locator.startswith(("https://", "http://")):
            raise RobotPackageError(f"{where}.locator must be an HTTP(S) citation")
        lowered_locator = locator.lower()
        if "github.com" in lowered_locator:
            version = str(value["version_or_date"]).lower()
            floating = any(
                marker in lowered_locator or marker in version
                for marker in (
                    "/blob/main/",
                    "/blob/master/",
                    "/tree/main/",
                    "/tree/master/",
                    "main branch",
                    "master branch",
                )
            )
            pinned = bool(
                re.search(r"(?<![0-9a-f])[0-9a-f]{12,40}(?![0-9a-f])", locator + " " + version)
                or "/releases/tag/" in lowered_locator
            )
            if floating or not pinned:
                raise RobotPackageError(f"{where}.locator must pin a GitHub revision")
        source_ids.add(source_id)
        sources.append(value)
    return tuple(sources)


def _validate_source_refs(
    clause: dict[str, Any],
    *,
    where: str,
    source_ids: set[str],
) -> None:
    refs = _required_list(clause, "source_refs", where=where)
    for index, ref in enumerate(refs):
        ref_where = f"{where}.source_refs[{index}]"
        if not isinstance(ref, dict):
            raise RobotPackageError(f"{ref_where} must be an object")
        source_id = _required_text(ref, "source_id", where=ref_where)
        if source_id not in source_ids:
            raise RobotPackageError(f"{ref_where} references unknown source {source_id!r}")
        _required_text(ref, "specific_reference", where=ref_where)
        support = _required_text(ref, "support", where=ref_where)
        if support not in {"direct", "adapted"}:
            raise RobotPackageError(f"{ref_where}.support must be direct or adapted")
        if support == "adapted":
            _required_text(ref, "adaptation", where=ref_where)


def _validate_scoring_clause(
    clause: dict[str, Any],
    *,
    where: str,
    source_ids: set[str],
) -> None:
    for field in ("clause_id", "metric", "unit", "comparator"):
        _required_text(clause, field, where=where)
    if clause["comparator"] not in {"<", "<=", ">", ">=", "==", "between"}:
        raise RobotPackageError(f"{where}.comparator is unsupported")
    if "threshold" not in clause:
        raise RobotPackageError(f"{where}.threshold is required")
    threshold = clause["threshold"]
    valid_number = isinstance(threshold, (int, float)) and not isinstance(threshold, bool)
    valid_range = (
        isinstance(threshold, list)
        and len(threshold) == 2
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in threshold)
    )
    if not (valid_number or valid_range):
        raise RobotPackageError(f"{where}.threshold must be numeric or a two-number range")
    if not isinstance(clause.get("temporal"), dict):
        raise RobotPackageError(f"{where}.temporal must be an object")
    _required_text(clause["temporal"], "kind", where=f"{where}.temporal")
    if not isinstance(clause.get("aggregation"), dict):
        raise RobotPackageError(f"{where}.aggregation must be an object")
    _required_text(clause["aggregation"], "kind", where=f"{where}.aggregation")
    _validate_source_refs(clause, where=where, source_ids=source_ids)


def _validate_parameter_schema(schema: Any, *, where: str) -> None:
    if not isinstance(schema, dict):
        raise RobotPackageError(f"{where} must be an object")
    kind = _required_text(schema, "type", where=where)
    _required_text(schema, "unit", where=where)
    _required_text(schema, "frame", where=where)

    def validate_shape(value: Any, *, shape_where: str) -> None:
        if not isinstance(value, dict):
            raise RobotPackageError(f"{shape_where} must be an object")
        value_kind = value.get("type")
        if value_kind not in {
            "array",
            "boolean",
            "integer",
            "number",
            "object",
            "string",
        }:
            raise RobotPackageError(f"{shape_where}.type is unsupported")
        if value_kind != "array":
            return
        if "items" not in value:
            raise RobotPackageError(f"{shape_where}.items must declare a supported type")
        validate_shape(value["items"], shape_where=f"{shape_where}.items")
        length = value.get("length")
        if length is not None and (
            isinstance(length, bool) or not isinstance(length, int) or length < 1
        ):
            raise RobotPackageError(f"{shape_where}.length must be a positive integer")

    validate_shape(schema, shape_where=where)


def _validate_parameter_value(
    value: Any, schema: Mapping[str, Any], *, where: str
) -> None:
    kind = schema["type"]
    if kind == "number":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise RobotPackageError(f"{where} must be a finite number")
        return
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise RobotPackageError(f"{where} must be an integer")
        return
    if kind == "boolean":
        if not isinstance(value, bool):
            raise RobotPackageError(f"{where} must be boolean")
        return
    if kind == "string":
        if not isinstance(value, str):
            raise RobotPackageError(f"{where} must be text")
        return
    if kind == "object":
        if not isinstance(value, dict):
            raise RobotPackageError(f"{where} must be an object")
        return
    if not isinstance(value, list):
        raise RobotPackageError(f"{where} must be an array")
    length = schema.get("length")
    if isinstance(length, int) and len(value) != length:
        raise RobotPackageError(f"{where} must contain exactly {length} items")
    item_schema = schema["items"]
    for index, item in enumerate(value):
        _validate_parameter_value(item, item_schema, where=f"{where}[{index}]")


def _validate_tasks(
    document: dict[str, Any],
    *,
    path: Path,
    source_ids: set[str],
) -> tuple[dict[str, Any], ...]:
    values = _required_list(document, "tasks", where=path.name)
    if len(values) < 20:
        raise RobotPackageError("Task Library must contain at least 20 tasks")
    tasks: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for index, value in enumerate(values):
        where = f"{path.name}.tasks[{index}]"
        if not isinstance(value, dict):
            raise RobotPackageError(f"{where} must be an object")
        task_id = _required_text(value, "task_id", where=where)
        if task_id in task_ids:
            raise RobotPackageError(f"duplicate task_id {task_id!r}")
        for field in (
            "name",
            "description",
            "source_task_or_operation",
            "applicability",
            "adaptation",
        ):
            _required_text(value, field, where=where)
        invocation = value.get("invocation_schema")
        if not isinstance(invocation, dict):
            raise RobotPackageError(f"{where}.invocation_schema must be an object")
        if invocation.get("envelope") != "request" or invocation.get("required") != ["request"]:
            raise RobotPackageError(f"{where}.invocation_schema must use the request envelope")
        request = invocation.get("request")
        if not isinstance(request, dict):
            raise RobotPackageError(f"{where}.invocation_schema.request must be an object")
        if request.get("type") != "object" or request.get("required") != [
            "task_id",
            "task_parameters",
        ]:
            raise RobotPackageError(
                f"{where}.invocation_schema.request must require task_id and task_parameters"
            )
        if request.get("task_id") != task_id:
            raise RobotPackageError(f"{where}.invocation_schema.request.task_id must match task_id")
        parameters = request.get("task_parameters")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise RobotPackageError(
                f"{where}.invocation_schema.request.task_parameters must be an object schema"
            )
        required_parameters = parameters.get("required")
        properties = parameters.get("properties")
        if (
            not isinstance(required_parameters, list)
            or not required_parameters
            or any(not isinstance(name, str) or not name.strip() for name in required_parameters)
            or len(set(required_parameters)) != len(required_parameters)
        ):
            raise RobotPackageError(
                f"{where}.invocation_schema task_parameters must declare required fields"
            )
        if not isinstance(properties, dict) or any(
            not isinstance(name, str) or name not in properties for name in required_parameters
        ):
            raise RobotPackageError(
                f"{where}.invocation_schema required task parameters need property schemas"
            )
        if parameters.get("additional_properties") is not False:
            raise RobotPackageError(
                f"{where}.invocation_schema task_parameters must forbid additional properties"
            )
        for name, schema in properties.items():
            if not isinstance(name, str) or not name.strip():
                raise RobotPackageError(
                    f"{where}.invocation_schema task_parameters has an invalid property name"
                )
            _validate_parameter_schema(
                schema,
                where=f"{where}.invocation_schema.request.task_parameters.properties.{name}",
            )
        _required_list(value, "scene_assumptions", where=where)
        _required_list(value, "observation_assumptions", where=where)
        clauses = _required_list(value, "scoring", where=where)
        clause_ids: set[str] = set()
        for clause_index, clause in enumerate(clauses):
            clause_where = f"{where}.scoring[{clause_index}]"
            if not isinstance(clause, dict):
                raise RobotPackageError(f"{clause_where} must be an object")
            _validate_scoring_clause(clause, where=clause_where, source_ids=source_ids)
            clause_id = str(clause["clause_id"])
            if clause_id in clause_ids:
                raise RobotPackageError(f"duplicate clause_id {clause_id!r} in {task_id!r}")
            clause_ids.add(clause_id)
        task_ids.add(task_id)
        tasks.append(value)
    return tuple(tasks)


def _contained_path(root: Path, relative: str, *, field: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute():
        raise RobotPackageError(f"{field} must be package-relative")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise RobotPackageError(f"{field} escapes the robot package") from exc
    return resolved


def _validate_mjcf(path: Path) -> None:
    if not path.is_file():
        raise RobotPackageError(f"MJCF entrypoint is missing: {path}")
    try:
        import mujoco

        mujoco.MjModel.from_xml_path(str(path))
    except Exception as exc:
        raise RobotPackageError(f"MJCF closure does not load: {type(exc).__name__}: {exc}") from exc


def _validate_asset_closure(package_root: Path, entrypoint: Path) -> None:
    assets_root = (package_root / "assets").resolve()
    for path in (assets_root, entrypoint):
        try:
            path.resolve(strict=True).relative_to(assets_root)
        except (FileNotFoundError, ValueError) as exc:
            raise RobotPackageError(f"asset path escapes package assets: {path}") from exc
    for path in assets_root.rglob("*"):
        if path.is_symlink():
            try:
                path.resolve(strict=True).relative_to(assets_root)
            except (FileNotFoundError, ValueError) as exc:
                raise RobotPackageError(f"asset symlink escapes package: {path}") from exc

    pending = [entrypoint]
    visited: set[Path] = set()
    while pending:
        xml_path = pending.pop().resolve()
        if xml_path in visited:
            continue
        visited.add(xml_path)
        try:
            root = ET.parse(xml_path).getroot()
        except (OSError, ET.ParseError) as exc:
            raise RobotPackageError(f"cannot inspect MJCF asset {xml_path}: {exc}") from exc
        for element in root.iter():
            file_value = element.attrib.get("file")
            if not file_value:
                continue
            relative = Path(file_value)
            if relative.is_absolute() or ".." in relative.parts:
                raise RobotPackageError(
                    f"MJCF file reference must stay package-relative: {file_value}"
                )
            if element.tag == "include":
                included = (xml_path.parent / relative).resolve()
                try:
                    included.relative_to(assets_root)
                except ValueError as exc:
                    raise RobotPackageError(
                        f"MJCF include escapes package assets: {file_value}"
                    ) from exc
                if not included.is_file():
                    raise RobotPackageError(f"MJCF include is missing: {included}")
                pending.append(included)


def _validate_leap_binding_parameters(
    binding: Mapping[str, Any], *, where: str
) -> None:
    kind = str(binding["kind"])
    if kind not in _LEAP_BINDING_UNITS:
        return
    if binding["unit"] != _LEAP_BINDING_UNITS[kind]:
        raise RobotPackageError(
            f"{where}.unit is incompatible with trusted binding kind {kind!r}"
        )
    parameters = binding["parameters"]
    required = _LEAP_BINDING_REQUIRED_PARAMETERS[kind]
    missing = sorted(required - set(parameters))
    if missing:
        raise RobotPackageError(
            f"{where}.parameters misses required fields {missing}"
        )
    for field in required & {
        "body_name",
        "reference_body_name",
        "target_argument",
        "control_steps_argument",
        "joint_name",
        "velocity_argument",
        "orientation_target_argument",
        "position_target_argument",
        "radii_argument",
        "period_argument",
        "motion_kind",
    }:
        if not isinstance(parameters[field], str) or not parameters[field].strip():
            raise RobotPackageError(
                f"{where}.parameters.{field} must be a non-empty string"
            )
    for field in required & {
        "object_geom_names",
        "required_robot_geom_groups",
        "site_names",
        "joint_names",
        "body_names",
        "orbit_center",
    }:
        if not isinstance(parameters[field], list) or not parameters[field]:
            raise RobotPackageError(
                f"{where}.parameters.{field} must be a non-empty list"
            )
    for field in required & {
        "object_geom_names",
        "site_names",
        "joint_names",
        "body_names",
    }:
        if any(
            not isinstance(name, str) or not name.strip()
            for name in parameters[field]
        ):
            raise RobotPackageError(
                f"{where}.parameters.{field} must contain only names"
            )
    for field in required & {
        "minimum_contact_steps",
        "physics_steps_per_control_step",
    }:
        value = parameters[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RobotPackageError(
                f"{where}.parameters.{field} must be a positive integer"
            )
    for field in required & {
        "solved_distance_m",
        "drop_distance_m",
        "control_period_s",
        "maximum_tracking_error_m",
        "maximum_orientation_error_rad",
        "maximum_position_error_m",
    }:
        value = parameters[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0.0
        ):
            raise RobotPackageError(
                f"{where}.parameters.{field} must be a positive finite number"
            )
    if "minimum_solved_steps" in required:
        value = parameters["minimum_solved_steps"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RobotPackageError(
                f"{where}.parameters.minimum_solved_steps must be a non-negative integer"
            )

    target_argument = parameters.get("target_argument")
    if isinstance(target_argument, str) and target_argument.endswith(
        ".max_control_steps"
    ):
        raise RobotPackageError(
            f"{where}.parameters.target_argument cannot use a control-step budget"
        )

    if kind == "mean_two_body_orbit_tracking_fraction":
        if len(parameters["body_names"]) != 2:
            raise RobotPackageError(
                f"{where}.parameters.body_names must contain exactly two bodies"
            )
        center = parameters["orbit_center"]
        if len(center) != 3 or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in center
        ):
            raise RobotPackageError(
                f"{where}.parameters.orbit_center must contain three finite numbers"
            )
        for field in ("minimum_source_height", "source_height_offset_m"):
            value = parameters[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise RobotPackageError(
                    f"{where}.parameters.{field} must be a finite number"
                )

    if kind == "in_hand_object_pattern_success":
        motion_kind = parameters["motion_kind"]
        motion_requirements = {
            "translation": {"axis", "minimum_translation_range_m"},
            "translation_return": {
                "axis",
                "minimum_translation_range_m",
                "maximum_return_error_m",
            },
            "translation_cycle": {
                "axis",
                "minimum_translation_range_m",
                "minimum_direction_changes",
            },
            "rotation": {"minimum_cumulative_rotation_deg"},
            "rotation_cycle": {
                "minimum_cumulative_rotation_deg",
                "minimum_direction_changes",
            },
            "joint": {"joint_name", "minimum_joint_range_rad"},
            "contact_slide": {
                "site_names",
                "axis",
                "minimum_site_translation_range_m",
            },
        }
        if motion_kind not in motion_requirements:
            raise RobotPackageError(
                f"{where}.parameters.motion_kind is unsupported"
            )
        missing_motion = sorted(
            motion_requirements[str(motion_kind)] - set(parameters)
        )
        if missing_motion:
            raise RobotPackageError(
                f"{where}.parameters misses motion fields {missing_motion}"
            )
        groups = parameters["required_robot_geom_groups"]
        if any(
            not isinstance(group, list)
            or not group
            or any(not isinstance(name, str) or not name.strip() for name in group)
            for group in groups
        ):
            raise RobotPackageError(
                f"{where}.parameters.required_robot_geom_groups is invalid"
            )
        if any(
            not isinstance(name, str) or not name.strip()
            for name in parameters["object_geom_names"]
        ):
            raise RobotPackageError(
                f"{where}.parameters.object_geom_names is invalid"
            )
        if "axis" in motion_requirements[str(motion_kind)]:
            axis = parameters["axis"]
            if isinstance(axis, bool) or not isinstance(axis, int) or axis not in {0, 1, 2}:
                raise RobotPackageError(
                    f"{where}.parameters.axis must be 0, 1, or 2"
                )
        for field in motion_requirements[str(motion_kind)] & {
            "minimum_direction_changes",
        }:
            value = parameters[field]
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RobotPackageError(
                    f"{where}.parameters.{field} must be a positive integer"
                )
        for field in (
            "minimum_simultaneous_contact_samples",
            "minimum_contact_group_transitions",
        ):
            if field not in parameters:
                continue
            value = parameters[field]
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RobotPackageError(
                    f"{where}.parameters.{field} must be a positive integer"
                )
        for field in motion_requirements[str(motion_kind)] & {
            "minimum_translation_range_m",
            "maximum_return_error_m",
            "minimum_cumulative_rotation_deg",
            "minimum_joint_range_rad",
            "minimum_site_translation_range_m",
        }:
            value = parameters[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) <= 0.0
            ):
                raise RobotPackageError(
                    f"{where}.parameters.{field} must be a positive finite number"
                )
        if "translation_direction" in parameters:
            direction = parameters["translation_direction"]
            if isinstance(direction, bool) or direction not in {-1, 1}:
                raise RobotPackageError(
                    f"{where}.parameters.translation_direction must be -1 or 1"
                )
        if motion_kind == "contact_slide" and len(parameters["site_names"]) != len(
            parameters["required_robot_geom_groups"]
        ):
            raise RobotPackageError(
                f"{where}.parameters.site_names must match the contact groups"
            )


def _validate_leap_clause_binding(
    clause: Mapping[str, Any], binding: Mapping[str, Any], *, where: str
) -> None:
    metric = str(clause["metric"])
    expected_kinds = _LEAP_METRIC_BINDING_KINDS.get(metric)
    expected_contract = _LEAP_METRIC_CONTRACTS.get(metric)
    if metric.startswith("ec_") and metric.endswith("_successful_trial_count"):
        expected_kinds = {"in_hand_object_pattern_success"}
        expected_contract = ("fixed_trials", "all_trials")
    if expected_kinds is None or expected_contract is None:
        return
    if binding["kind"] not in expected_kinds:
        raise RobotPackageError(
            f"{where} misinterprets source metric {metric!r} with binding kind "
            f"{binding['kind']!r}"
        )
    temporal = clause["temporal"]
    aggregation = clause["aggregation"]
    actual_contract = (temporal.get("kind"), aggregation.get("kind"))
    if actual_contract != expected_contract:
        raise RobotPackageError(
            f"{where} changes the trusted temporal or aggregation contract for {metric!r}"
        )


def _validate_leap_scene_contract(
    scene: Path,
    *,
    instance: Mapping[str, Any],
    clauses: Mapping[str, Mapping[str, Any]],
    clause_bindings: Mapping[str, Any],
    bindings: Mapping[str, Mapping[str, Any]],
    public_arguments: Mapping[str, Any],
    where: str,
) -> None:
    selected = [
        (clause, bindings[str(clause_bindings[clause_id])])
        for clause_id, clause in clauses.items()
        if str(clause["metric"]) in _LEAP_METRIC_BINDING_KINDS
        or (
            str(clause["metric"]).startswith("ec_")
            and str(clause["metric"]).endswith("_successful_trial_count")
        )
    ]
    if not selected:
        return
    try:
        import mujoco

        model = mujoco.MjModel.from_xml_path(str(scene))
    except Exception as exc:
        raise RobotPackageError(
            f"cannot inspect LEAP scene contract: {type(exc).__name__}: {exc}"
        ) from exc

    for clause, binding in selected:
        parameters = binding["parameters"]
        kind = str(binding["kind"])
        body_fields = _LEAP_MOVABLE_BODY_FIELDS.get(kind, ())
        if kind == "in_hand_object_pattern_success" and parameters.get(
            "motion_kind"
        ) == "joint":
            body_fields = ()
        for field in body_fields:
            raw_names = parameters[field]
            names = raw_names if isinstance(raw_names, list) else [raw_names]
            for name in names:
                body_id = mujoco.mj_name2id(
                    model, mujoco.mjtObj.mjOBJ_BODY, str(name)
                )
                if body_id < 0:
                    raise RobotPackageError(
                        f"{where} binding body {name!r} is absent from its scene"
                    )
                if int(model.body_jntnum[body_id]) == 0:
                    raise RobotPackageError(
                        f"{where} binding body {name!r} is fixed, not manipulable"
                    )
        raw_joint_names: list[str] = []
        if "joint_name" in parameters:
            raw_joint_names.append(str(parameters["joint_name"]))
        if isinstance(parameters.get("joint_names"), list):
            raw_joint_names.extend(str(name) for name in parameters["joint_names"])
        for name in raw_joint_names:
            if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) < 0:
                raise RobotPackageError(
                    f"{where} binding joint {name!r} is absent from its scene"
                )

        temporal = clause["temporal"]
        temporal_kind = temporal.get("kind")
        if temporal_kind not in {"terminal_step", "fixed_horizon", "continuous"}:
            continue
        maximum_control_steps = temporal.get("max_control_steps")
        physics_steps_per_control_step = parameters.get(
            "physics_steps_per_control_step"
        )
        if (
            isinstance(maximum_control_steps, bool)
            or not isinstance(maximum_control_steps, int)
            or maximum_control_steps <= 0
            or isinstance(physics_steps_per_control_step, bool)
            or not isinstance(physics_steps_per_control_step, int)
            or physics_steps_per_control_step <= 0
        ):
            raise RobotPackageError(
                f"{where} source horizon requires positive control and physics steps"
            )
        expected_physics_steps = (
            maximum_control_steps * physics_steps_per_control_step
        )
        argument_name = str(parameters["control_steps_argument"])
        argument_value: Any = public_arguments
        for part in argument_name.split("."):
            if not isinstance(argument_value, Mapping) or part not in argument_value:
                raise RobotPackageError(
                    f"{where} cannot resolve control-step budget {argument_name!r}"
                )
            argument_value = argument_value[part]
        if argument_value != maximum_control_steps:
            raise RobotPackageError(
                f"{where} public control-step budget differs from its source horizon"
            )
        if instance["max_steps"] != expected_physics_steps:
            raise RobotPackageError(
                f"{where}.max_steps must equal the complete source physics horizon "
                f"{expected_physics_steps}"
            )

        timestep = float(model.opt.timestep)
        control_period = parameters.get("control_period_s")
        if control_period is not None:
            expected_period = timestep * physics_steps_per_control_step
            if not math.isclose(
                float(control_period), expected_period, rel_tol=0.0, abs_tol=1e-9
            ):
                raise RobotPackageError(
                    f"{where} control period is inconsistent with scene timestep"
                )
            expected_rate = 1.0 / float(control_period)
            for field in ("sample_hz", "video_fps"):
                rate = instance.get(field)
                if (
                    isinstance(rate, bool)
                    or not isinstance(rate, (int, float))
                    or not math.isclose(
                        float(rate), expected_rate, rel_tol=0.0, abs_tol=1e-9
                    )
                ):
                    raise RobotPackageError(
                        f"{where}.{field} must sample every source control step"
                    )

        physics_duration = expected_physics_steps * timestep
        source_duration = temporal.get("duration_s")
        if source_duration is not None and not math.isclose(
            float(source_duration), physics_duration, rel_tol=0.0, abs_tol=1e-9
        ):
            raise RobotPackageError(
                f"{where} source duration is inconsistent with timestep and step budget"
            )
        if float(instance["timeout_sim_s"]) + 1e-9 < physics_duration:
            raise RobotPackageError(
                f"{where}.timeout_sim_s is shorter than the complete physics horizon"
            )


def _validate_private_inputs(
    *,
    package_root: Path,
    private_dir: Path,
    robot_configuration_id: str,
    package_version: str,
    snapshot_id: str,
    tasks: tuple[dict[str, Any], ...],
) -> None:
    documents = {
        name: _read_object(private_dir / f"{name}.json")
        for name in ("instances", "bindings", "guards")
    }
    for name, document in documents.items():
        _validate_identity(
            document,
            path=private_dir / f"{name}.json",
            robot_configuration_id=robot_configuration_id,
            package_version=package_version,
        )
        if document.get("task_snapshot_id") != snapshot_id:
            raise RobotPackageError(f"task snapshot mismatch in private {name}.json")

    binding_values = _required_list(documents["bindings"], "bindings", where="bindings.json")
    bindings: dict[str, dict[str, Any]] = {}
    for index, binding in enumerate(binding_values):
        where = f"bindings.json.bindings[{index}]"
        if not isinstance(binding, dict):
            raise RobotPackageError(f"{where} must be an object")
        binding_id = _required_text(binding, "binding_id", where=where)
        if binding_id in bindings:
            raise RobotPackageError(f"duplicate binding_id {binding_id!r}")
        for field in ("metric", "unit", "kind"):
            _required_text(binding, field, where=where)
        if binding["kind"] not in _SUPPORTED_BINDING_KINDS:
            raise RobotPackageError(f"{where}.kind is not implemented by the trusted Harness")
        if not isinstance(binding.get("parameters"), dict):
            raise RobotPackageError(f"{where}.parameters must be an object")
        target_argument = binding["parameters"].get("target_argument")
        if isinstance(target_argument, str) and target_argument.endswith(
            ".max_control_steps"
        ):
            raise RobotPackageError(
                f"{where}.parameters.target_argument cannot use a control-step budget"
            )
        if binding["kind"] == "body_yaw_change_deg" and binding["unit"] != "deg":
            raise RobotPackageError(
                f"{where}.unit must be deg for body_yaw_change_deg"
            )
        _validate_leap_binding_parameters(binding, where=where)
        bindings[binding_id] = binding

    guard_values = _required_list(documents["guards"], "guards", where="guards.json")
    guards: dict[str, dict[str, Any]] = {}
    for index, guard in enumerate(guard_values):
        where = f"guards.json.guards[{index}]"
        if not isinstance(guard, dict):
            raise RobotPackageError(f"{where} must be an object")
        guard_id = _required_text(guard, "guard_id", where=where)
        kind = _required_text(guard, "kind", where=where)
        if guard_id in guards:
            raise RobotPackageError(f"duplicate guard_id {guard_id!r}")
        if kind not in _SUPPORTED_GUARD_KINDS:
            raise RobotPackageError(f"{where}.kind is not implemented by the trusted Harness")
        if kind == "named_geom_contact_pair_required":
            has_robot_geom_name = "robot_geom_name" in guard
            has_robot_geom_names = "robot_geom_names" in guard
            if has_robot_geom_name == has_robot_geom_names:
                raise RobotPackageError(
                    f"{where} must contain exactly one of robot_geom_name or robot_geom_names"
                )
            if has_robot_geom_name:
                robot_geom_names = [_required_text(guard, "robot_geom_name", where=where)]
            else:
                robot_geom_names = guard.get("robot_geom_names")
                if (
                    not isinstance(robot_geom_names, list)
                    or not robot_geom_names
                    or any(
                        not isinstance(name, str) or not name.strip()
                        for name in robot_geom_names
                    )
                ):
                    raise RobotPackageError(
                        f"{where}.robot_geom_names must be a non-empty list of names"
                    )
            task_geom_names = guard.get("task_geom_names")
            if (
                not isinstance(task_geom_names, list)
                or not task_geom_names
                or any(not isinstance(name, str) or not name.strip() for name in task_geom_names)
            ):
                raise RobotPackageError(f"{where}.task_geom_names must be a non-empty list of names")
            if set(robot_geom_names).intersection(task_geom_names):
                raise RobotPackageError(
                    f"{where}.task_geom_names must not contain a selected robot geom"
                )
            minimum_steps = guard.get("minimum_steps")
            if (
                isinstance(minimum_steps, bool)
                or not isinstance(minimum_steps, int)
                or minimum_steps <= 0
            ):
                raise RobotPackageError(f"{where}.minimum_steps must be a positive integer")
        elif kind == "named_joints_remain_near_reset":
            tolerances = guard.get("joint_tolerances")
            if not isinstance(tolerances, dict) or not tolerances:
                raise RobotPackageError(
                    f"{where}.joint_tolerances must be a non-empty object"
                )
            for joint_name, tolerance in tolerances.items():
                if not isinstance(joint_name, str) or not joint_name.strip():
                    raise RobotPackageError(
                        f"{where}.joint_tolerances keys must be non-empty names"
                    )
                if (
                    isinstance(tolerance, bool)
                    or not isinstance(tolerance, (int, float))
                    or not math.isfinite(float(tolerance))
                    or float(tolerance) <= 0.0
                ):
                    raise RobotPackageError(
                        f"{where}.joint_tolerances[{joint_name!r}] "
                        "must be a positive finite number"
                    )
        elif kind == "terminal_body_stability":
            _required_text(guard, "body_name", where=where)
            for field in ("minimum_height_m", "minimum_upright_cosine"):
                value = guard.get(field)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    raise RobotPackageError(f"{where}.{field} must be a finite number")
            if float(guard["minimum_height_m"]) <= 0.0:
                raise RobotPackageError(f"{where}.minimum_height_m must be positive")
            if not 0.0 <= float(guard["minimum_upright_cosine"]) <= 1.0:
                raise RobotPackageError(
                    f"{where}.minimum_upright_cosine must be in [0, 1]"
                )
        guards[guard_id] = guard

    tasks_by_id = {str(task["task_id"]): task for task in tasks}

    def validate_public_arguments(
        public_arguments: Any,
        *,
        task_id: str,
        where: str,
    ) -> None:
        if not isinstance(public_arguments, dict) or set(public_arguments) != {"request"}:
            raise RobotPackageError(f"{where} must contain only request")
        request = public_arguments.get("request")
        if not isinstance(request, dict) or request.get("task_id") != task_id:
            raise RobotPackageError(f"{where}.request has the wrong task_id")
        parameters = request.get("task_parameters")
        if not isinstance(parameters, dict):
            raise RobotPackageError(f"{where}.request.task_parameters must be an object")
        schema_parameters = tasks_by_id[task_id]["invocation_schema"]["request"][
            "task_parameters"
        ]
        missing_parameters = set(schema_parameters["required"]) - set(parameters)
        if missing_parameters:
            raise RobotPackageError(
                f"{where}.request.task_parameters misses {sorted(missing_parameters)}"
            )
        property_schemas = schema_parameters["properties"]
        unexpected_parameters = set(parameters) - set(property_schemas)
        if unexpected_parameters:
            raise RobotPackageError(
                f"{where}.request.task_parameters contains undeclared fields "
                f"{sorted(unexpected_parameters)}"
            )
        non_contract_parameters = set(parameters) - set(schema_parameters["required"])
        if non_contract_parameters:
            raise RobotPackageError(
                f"{where}.request.task_parameters supplies fields outside the required "
                f"capability interface {sorted(non_contract_parameters)}"
            )
        for name, value in parameters.items():
            _validate_parameter_value(
                value,
                property_schemas[name],
                where=f"{where}.request.task_parameters.{name}",
            )

    def validate_reset(reset: Any, *, where: str) -> None:
        if not isinstance(reset, dict) or reset.get("kind") not in {"default", "keyframe"}:
            raise RobotPackageError(f"{where} must select a supported Framework reset")
        quaternions = reset.get("body_quaternions", {})
        if not isinstance(quaternions, dict):
            raise RobotPackageError(f"{where}.body_quaternions must be an object")
        for body_name, quaternion in quaternions.items():
            if not isinstance(body_name, str) or not body_name.strip():
                raise RobotPackageError(f"{where}.body_quaternions has an invalid body name")
            if (
                not isinstance(quaternion, list)
                or len(quaternion) != 4
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in quaternion
                )
            ):
                raise RobotPackageError(
                    f"{where}.body_quaternions.{body_name} must be four finite numbers"
                )
            norm = math.sqrt(sum(float(value) ** 2 for value in quaternion))
            if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-6):
                raise RobotPackageError(
                    f"{where}.body_quaternions.{body_name} must be normalized"
                )

    instance_values = _required_list(
        documents["instances"], "instances", where="instances.json"
    )
    instance_ids: set[str] = set()
    covered_tasks: set[str] = set()
    scenes: set[Path] = set()
    for index, instance in enumerate(instance_values):
        where = f"instances.json.instances[{index}]"
        if not isinstance(instance, dict):
            raise RobotPackageError(f"{where} must be an object")
        instance_id = _required_text(instance, "instance_id", where=where)
        task_id = _required_text(instance, "task_id", where=where)
        if instance_id in instance_ids:
            raise RobotPackageError(f"duplicate instance_id {instance_id!r}")
        if task_id not in tasks_by_id:
            raise RobotPackageError(f"{where} references unknown task {task_id!r}")
        if task_id in covered_tasks:
            raise RobotPackageError(
                f"the mainline expects one private instance for task {task_id!r}"
            )
        instance_ids.add(instance_id)
        covered_tasks.add(task_id)

        validate_public_arguments(
            instance.get("public_arguments"),
            task_id=task_id,
            where=f"{where}.public_arguments",
        )

        validate_reset(instance.get("reset"), where=f"{where}.reset")
        clause_bindings = instance.get("clause_bindings")
        if not isinstance(clause_bindings, dict):
            raise RobotPackageError(f"{where}.clause_bindings must be an object")
        clauses = {
            str(clause["clause_id"]): clause for clause in tasks_by_id[task_id]["scoring"]
        }
        if set(clause_bindings) != set(clauses):
            raise RobotPackageError(f"{where} must bind every task scoring clause exactly once")
        for clause_id, binding_id in clause_bindings.items():
            binding = bindings.get(str(binding_id))
            if binding is None:
                raise RobotPackageError(f"{where} references unknown binding {binding_id!r}")
            clause = clauses[clause_id]
            if binding["metric"] != clause["metric"] or binding["unit"] != clause["unit"]:
                raise RobotPackageError(f"{where} uses an incompatible binding for {clause_id!r}")
            _validate_leap_clause_binding(
                clause,
                binding,
                where=f"{where}.clause_bindings[{clause_id!r}]",
            )

        guard_ids = instance.get("guard_ids")
        if not isinstance(guard_ids, list) or not guard_ids or any(
            not isinstance(guard_id, str) or guard_id not in guards for guard_id in guard_ids
        ):
            raise RobotPackageError(f"{where}.guard_ids must reference known private guards")
        selected_guard_kinds = {str(guards[guard_id]["kind"]) for guard_id in guard_ids}
        if not _REQUIRED_GUARD_KINDS <= selected_guard_kinds:
            raise RobotPackageError(f"{where} omits a required anti-false-pass guard")
        repetitions = instance.get("repetitions")
        timeout_sim_s = instance.get("timeout_sim_s")
        max_steps = instance.get("max_steps")
        if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
            raise RobotPackageError(f"{where}.repetitions must be a positive integer")
        for clause in clauses.values():
            temporal = clause["temporal"]
            if temporal.get("kind") != "fixed_trials":
                continue
            trial_count = temporal.get("trial_count")
            if (
                isinstance(trial_count, bool)
                or not isinstance(trial_count, int)
                or trial_count <= 0
            ):
                raise RobotPackageError(
                    f"{where} fixed_trials requires a positive trial_count"
                )
            task_parameters = instance["public_arguments"]["request"][
                "task_parameters"
            ]
            if repetitions != trial_count or task_parameters.get(
                "trial_count"
            ) != trial_count:
                raise RobotPackageError(
                    f"{where} must execute every fixed_trials repetition independently"
                )
        repetition_variants = instance.get("repetition_variants")
        if repetition_variants is not None:
            if not isinstance(repetition_variants, list) or len(repetition_variants) != repetitions:
                raise RobotPackageError(
                    f"{where}.repetition_variants must contain one entry per repetition"
                )
            for variant_index, variant in enumerate(repetition_variants):
                variant_where = f"{where}.repetition_variants[{variant_index}]"
                if not isinstance(variant, dict) or not variant:
                    raise RobotPackageError(f"{variant_where} must be a non-empty object")
                if not set(variant) <= {"public_arguments", "reset"}:
                    raise RobotPackageError(
                        f"{variant_where} may contain only public_arguments and reset"
                    )
                if "public_arguments" in variant:
                    validate_public_arguments(
                        variant["public_arguments"],
                        task_id=task_id,
                        where=f"{variant_where}.public_arguments",
                    )
                    for clause in clauses.values():
                        temporal = clause["temporal"]
                        if temporal.get("kind") == "fixed_trials" and variant[
                            "public_arguments"
                        ]["request"]["task_parameters"].get(
                            "trial_count"
                        ) != temporal.get("trial_count"):
                            raise RobotPackageError(
                                f"{variant_where} changes the fixed_trials count"
                            )
                if "reset" in variant:
                    validate_reset(variant["reset"], where=f"{variant_where}.reset")
        if (
            isinstance(timeout_sim_s, bool)
            or not isinstance(timeout_sim_s, (int, float))
            or timeout_sim_s <= 0
        ):
            raise RobotPackageError(f"{where}.timeout_sim_s must be positive")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise RobotPackageError(f"{where}.max_steps must be a positive integer")

        if any(
            binding["kind"] == "contact_sample_count"
            and clauses[clause_id]["comparator"] == "=="
            and clauses[clause_id]["threshold"] == 0
            for clause_id, binding_id in clause_bindings.items()
            for binding in (bindings[str(binding_id)],)
        ) and "named_geom_contact_pair_required" in selected_guard_kinds:
            raise RobotPackageError(
                f"{where} cannot require contact while scoring zero contact samples"
            )

        scene_relative = _required_text(instance, "scene_entrypoint", where=where)
        scene = _contained_path(package_root, scene_relative, field=f"{where}.scene_entrypoint")
        try:
            scene.relative_to((package_root / "assets").resolve())
        except ValueError as exc:
            raise RobotPackageError(f"{where}.scene_entrypoint must live under assets/") from exc
        _validate_leap_scene_contract(
            scene,
            instance=instance,
            clauses=clauses,
            clause_bindings=clause_bindings,
            bindings=bindings,
            public_arguments=instance["public_arguments"],
            where=where,
        )
        scenes.add(scene)

    if covered_tasks != set(tasks_by_id):
        missing = sorted(set(tasks_by_id) - covered_tasks)
        raise RobotPackageError(f"private instances do not cover every task: {missing}")
    for scene in scenes:
        _validate_asset_closure(package_root, scene)
        _validate_mjcf(scene)


def load_robot_package(root: str | Path) -> RobotPackage:
    """Load one package only after its complete runnable inputs pass checks."""

    package_root = Path(root).resolve()
    morphology_path = package_root / "morphology.json"
    sources_path = package_root / "tasks" / "sources.json"
    catalog_path = package_root / "tasks" / "catalog.json"
    private_dir = package_root / "tasks" / "private"

    morphology = _read_object(morphology_path)
    robot_configuration_id = _required_text(
        morphology, "robot_configuration_id", where=morphology_path.name
    )
    package_version = _required_text(morphology, "package_version", where=morphology_path.name)
    mjcf_relative = _required_text(morphology, "mjcf_entrypoint", where=morphology_path.name)
    _validate_public_affordances(morphology, where=morphology_path.name)

    sources_document = _read_object(sources_path)
    catalog_document = _read_object(catalog_path)
    for document, path in ((sources_document, sources_path), (catalog_document, catalog_path)):
        _validate_identity(
            document,
            path=path,
            robot_configuration_id=robot_configuration_id,
            package_version=package_version,
        )
    sources = _validate_sources(sources_document, path=sources_path)
    tasks = _validate_tasks(
        catalog_document,
        path=catalog_path,
        source_ids={str(item["source_id"]) for item in sources},
    )
    snapshot_id = _required_text(catalog_document, "snapshot_id", where=catalog_path.name)

    _validate_private_inputs(
        package_root=package_root,
        private_dir=private_dir,
        robot_configuration_id=robot_configuration_id,
        package_version=package_version,
        snapshot_id=snapshot_id,
        tasks=tasks,
    )

    skeleton_dir = package_root / "skeleton"
    if not skeleton_dir.is_dir() or not any(skeleton_dir.glob("*.py")):
        raise RobotPackageError("robot package must contain a Python skeleton family")
    reference_driver = package_root / "reference" / "driver.py"
    if not reference_driver.is_file():
        raise RobotPackageError("robot package must contain reference/driver.py")

    mjcf_path = _contained_path(package_root, mjcf_relative, field="mjcf_entrypoint")
    assets_dir = (package_root / "assets").resolve()
    try:
        mjcf_path.relative_to(assets_dir)
    except ValueError as exc:
        raise RobotPackageError("mjcf_entrypoint must live under assets/") from exc
    _validate_asset_closure(package_root, mjcf_path)
    _validate_mjcf(mjcf_path)

    return RobotPackage(
        root=package_root,
        robot_configuration_id=robot_configuration_id,
        package_version=package_version,
        snapshot_id=snapshot_id,
        morphology=morphology,
        sources=sources,
        tasks=tasks,
        mjcf_path=mjcf_path,
        skeleton_dir=skeleton_dir,
        reference_driver=reference_driver,
        private_dir=private_dir,
    )


def load_indexed_robot_package(
    mainline_root: str | Path,
    robot_configuration_id: str,
) -> RobotPackage:
    """Resolve only an explicitly indexed runnable package under the mainline."""

    root = Path(mainline_root).resolve()
    index_path = root / "libraries" / "robots" / "index.json"
    index = _read_object(index_path)
    robots = index.get("robots")
    if not isinstance(robots, dict):
        raise RobotPackageError("runnable robot index must contain a robots object")
    relative = robots.get(robot_configuration_id)
    if not isinstance(relative, str) or not relative:
        raise RobotPackageError(
            f"robot configuration is not runnable: {robot_configuration_id!r}"
        )
    package_root = _contained_path(
        index_path.parent,
        relative,
        field=f"robot index entry {robot_configuration_id!r}",
    )
    return load_robot_package(package_root)
