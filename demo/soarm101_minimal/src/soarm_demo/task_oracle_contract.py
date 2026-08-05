"""Strict, immutable parser for the frozen private SOARM101 task oracles.

The YAML is data, not executable configuration.  This module is the single
trusted interpretation boundary: it rejects duplicate YAML keys, validates a
closed JSON Schema, enforces the exact P0 task/measurement vocabulary, and
returns immutable records used by both fixture and MuJoCo Demo scorers.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import yaml

from .audit import sha256_bytes
from .schema_validation import validate_json_schema


_DEMO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASK_ORACLE_SCHEMA = _DEMO_ROOT / "schemas/private_task_oracles.schema.json"
DEFAULT_TASK_ORACLE_SOURCE = (
    _DEMO_ROOT / "private/task_library/soarm101_tabletop/v1/task_oracles.yaml"
)


class TaskOracleContractError(ValueError):
    """The private scoring contract is malformed, incomplete, or not frozen."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise TaskOracleContractError(f"duplicate YAML key {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


@dataclass(frozen=True)
class _MeasureSpec:
    operator: str
    required_fields: frozenset[str]


_BASE_CONDITION_FIELDS = frozenset({"measure", "operator"})
_MEASURE_SPECS: Mapping[str, _MeasureSpec] = MappingProxyType(
    {
        "ee_position_error_m": _MeasureSpec(
            "less_than_or_equal", frozenset({"target_ref", "tolerance"})
        ),
        "ee_linear_speed_m_s": _MeasureSpec(
            "less_than_or_equal", frozenset({"tolerance"})
        ),
        "object_xy_distance_to_target_center_m": _MeasureSpec(
            "less_than_or_equal",
            frozenset({"object_id", "target_ref", "tolerance"}),
        ),
        "object_supported_by_table": _MeasureSpec(
            "is_true", frozenset({"object_id"})
        ),
        "maximum_object_lift_above_initial_m": _MeasureSpec(
            "less_than_or_equal", frozenset({"object_id", "tolerance"})
        ),
        "object_linear_speed_m_s": _MeasureSpec(
            "less_than_or_equal", frozenset({"object_id", "tolerance"})
        ),
        "opposing_gripper_contacts": _MeasureSpec(
            "is_true_for_window", frozenset({"object_id"})
        ),
        "object_gripper_relative_translation_drift_m": _MeasureSpec(
            "less_than_or_equal_over_window",
            frozenset({"object_id", "tolerance"}),
        ),
        "object_dropped": _MeasureSpec("is_false", frozenset({"object_id"})),
        "object_height_delta_error_m": _MeasureSpec(
            "less_than_or_equal",
            frozenset({"object_id", "target_ref", "tolerance"}),
        ),
        "object_grasped": _MeasureSpec("is_false", frozenset({"object_id"})),
        "object_footprint_contained_by_receptacle": _MeasureSpec(
            "is_true", frozenset({"object_id", "receptacle_id", "parameters"})
        ),
        "object_supported_inside_receptacle": _MeasureSpec(
            "is_true", frozenset({"object_id", "receptacle_id"})
        ),
        "object_axis_tilt_rad": _MeasureSpec(
            "less_than_or_equal",
            frozenset({"object_id", "target_axis", "tolerance"}),
        ),
        "object_xy_alignment_error_m": _MeasureSpec(
            "less_than_or_equal",
            frozenset({"object_id", "support_object_id", "tolerance"}),
        ),
        "object_vertical_support_gap_error_m": _MeasureSpec(
            "less_than_or_equal",
            frozenset({"object_id", "support_object_id", "tolerance"}),
        ),
        "object_supported_by_object": _MeasureSpec(
            "is_true", frozenset({"object_id", "support_object_id"})
        ),
        "max_object_linear_speed_m_s": _MeasureSpec(
            "less_than_or_equal", frozenset({"object_ids", "tolerance"})
        ),
        "successful_containment_event_order": _MeasureSpec(
            "equals",
            frozenset(
                {
                    "expected_object_ids_ref",
                    "receptacle_id",
                    "parameters",
                }
            ),
        ),
        "all_object_footprints_contained_by_receptacle": _MeasureSpec(
            "is_true", frozenset({"object_ids", "receptacle_id", "parameters"})
        ),
        "any_object_grasped": _MeasureSpec(
            "is_false", frozenset({"object_ids"})
        ),
        "object_contained_by_bowl": _MeasureSpec(
            "is_true", frozenset({"object_id", "receptacle_id", "parameters"})
        ),
        "object_receptacle_assignment": _MeasureSpec(
            "equals", frozenset({"expected_mapping_ref"})
        ),
        "all_object_footprints_contained_by_assigned_receptacle": _MeasureSpec(
            "is_true", frozenset({"expected_mapping_ref", "parameters"})
        ),
    }
)


EXPECTED_MEASURES_BY_TASK: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "soarm101_p0_reach_target_pose": (
            "ee_position_error_m",
            "ee_linear_speed_m_s",
        ),
        "soarm101_p0_push_cube_to_region": (
            "object_xy_distance_to_target_center_m",
            "object_supported_by_table",
            "maximum_object_lift_above_initial_m",
            "object_linear_speed_m_s",
        ),
        "soarm101_p0_grasp_cube_hold": (
            "opposing_gripper_contacts",
            "object_gripper_relative_translation_drift_m",
            "object_dropped",
        ),
        "soarm101_p0_lift_cube_by_height": (
            "object_height_delta_error_m",
            "opposing_gripper_contacts",
            "object_linear_speed_m_s",
        ),
        "soarm101_p0_place_cube_on_target": (
            "object_xy_distance_to_target_center_m",
            "object_supported_by_table",
            "object_grasped",
            "object_linear_speed_m_s",
        ),
        "soarm101_p0_place_cube_in_tray": (
            "object_footprint_contained_by_receptacle",
            "object_supported_inside_receptacle",
            "object_grasped",
            "object_linear_speed_m_s",
        ),
        "soarm101_p0_place_cylinder_in_tray": (
            "object_footprint_contained_by_receptacle",
            "object_axis_tilt_rad",
            "object_grasped",
            "object_linear_speed_m_s",
        ),
        "soarm101_p0_stack_red_on_green": (
            "object_xy_alignment_error_m",
            "object_vertical_support_gap_error_m",
            "object_supported_by_object",
            "object_grasped",
            "max_object_linear_speed_m_s",
        ),
        "soarm101_p0_place_two_objects_in_tray": (
            "successful_containment_event_order",
            "all_object_footprints_contained_by_receptacle",
            "any_object_grasped",
            "max_object_linear_speed_m_s",
        ),
        "soarm101_p0_push_cylinder_lateral": (
            "object_xy_distance_to_target_center_m",
            "object_axis_tilt_rad",
            "object_supported_by_table",
            "maximum_object_lift_above_initial_m",
            "object_linear_speed_m_s",
        ),
        "soarm101_p0_place_cube_in_bowl_new_region": (
            "object_contained_by_bowl",
            "object_grasped",
            "object_linear_speed_m_s",
        ),
        "soarm101_p0_sort_two_cubes_matching_trays": (
            "object_receptacle_assignment",
            "all_object_footprints_contained_by_assigned_receptacle",
            "any_object_grasped",
            "max_object_linear_speed_m_s",
        ),
    }
)


_TASK_OBJECTS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "soarm101_p0_reach_target_pose": frozenset(),
        "soarm101_p0_push_cube_to_region": frozenset({"red_cube"}),
        "soarm101_p0_grasp_cube_hold": frozenset({"blue_cube"}),
        "soarm101_p0_lift_cube_by_height": frozenset({"green_cube"}),
        "soarm101_p0_place_cube_on_target": frozenset({"yellow_cube"}),
        "soarm101_p0_place_cube_in_tray": frozenset({"red_cube"}),
        "soarm101_p0_place_cylinder_in_tray": frozenset({"blue_cylinder"}),
        "soarm101_p0_stack_red_on_green": frozenset({"red_cube", "green_cube"}),
        "soarm101_p0_place_two_objects_in_tray": frozenset(
            {"red_cube", "blue_cylinder"}
        ),
        "soarm101_p0_push_cylinder_lateral": frozenset({"orange_cylinder"}),
        "soarm101_p0_place_cube_in_bowl_new_region": frozenset({"purple_cube"}),
        "soarm101_p0_sort_two_cubes_matching_trays": frozenset(
            {"red_cube", "blue_cube"}
        ),
    }
)


_TASK_RECEPTACLES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        task_id: (
            frozenset({"tray"})
            if task_id
            in {
                "soarm101_p0_place_cube_in_tray",
                "soarm101_p0_place_cylinder_in_tray",
                "soarm101_p0_place_two_objects_in_tray",
            }
            else frozenset({"bowl"})
            if task_id == "soarm101_p0_place_cube_in_bowl_new_region"
            else frozenset({"red_tray", "blue_tray"})
            if task_id == "soarm101_p0_sort_two_cubes_matching_trays"
            else frozenset()
        )
        for task_id in EXPECTED_MEASURES_BY_TASK
    }
)


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _finite_positive(value: Any, *, path: str, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskOracleContractError(f"{path} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < 0.0 or (result == 0.0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise TaskOracleContractError(f"{path} must be finite and {qualifier}")
    return result


@dataclass(frozen=True)
class FrozenOracleCondition:
    measure: str
    operator: str
    fields: Mapping[str, Any]

    def number(self, name: str) -> float:
        if name not in self.fields:
            raise TaskOracleContractError(
                f"measure {self.measure!r} has no numeric field {name!r}"
            )
        return _finite_positive(
            self.fields[name], path=f"{self.measure}.{name}", allow_zero=True
        )

    def parameter(self, name: str) -> Any:
        parameters = self.fields.get("parameters")
        if not isinstance(parameters, Mapping) or name not in parameters:
            raise TaskOracleContractError(
                f"measure {self.measure!r} has no parameter {name!r}"
            )
        return parameters[name]


@dataclass(frozen=True)
class FrozenTaskPredicate:
    oracle_id: str
    task_id: str
    timeout_s: float
    settle_window_s: float | None
    hold_window_s: float | None
    conditions: tuple[FrozenOracleCondition, ...]

    def condition(self, measure: str) -> FrozenOracleCondition:
        matches = [item for item in self.conditions if item.measure == measure]
        if len(matches) != 1:
            raise TaskOracleContractError(
                f"task {self.task_id!r} does not have exactly one {measure!r} condition"
            )
        return matches[0]

    @property
    def evaluation_window_s(self) -> float:
        value = self.settle_window_s
        if value is None:
            value = self.hold_window_s
        if value is None:
            raise TaskOracleContractError(f"task {self.task_id!r} has no evaluation window")
        return value


@dataclass(frozen=True)
class FrozenTaskOracleContract:
    schema_version: str
    oracle_set_id: str
    version: str
    source_sha256: str
    predicates_by_task: Mapping[str, FrozenTaskPredicate]
    forbidden_by_name: Mapping[str, Mapping[str, Any]]
    raw: Mapping[str, Any]

    def predicate_for_task(self, task_id: str) -> FrozenTaskPredicate:
        predicate = self.predicates_by_task.get(task_id)
        if predicate is None:
            raise TaskOracleContractError(
                f"task {task_id!r} is absent from the frozen scoring contract"
            )
        return predicate

    def forbidden_parameter(self, condition: str, name: str) -> float:
        record = self.forbidden_by_name.get(condition)
        parameters = record.get("parameters") if isinstance(record, Mapping) else None
        if not isinstance(parameters, Mapping) or name not in parameters:
            raise TaskOracleContractError(
                f"forbidden condition {condition!r} has no parameter {name!r}"
            )
        return _finite_positive(
            parameters[name],
            path=f"common_forbidden_conditions.{condition}.parameters.{name}",
            allow_zero=True,
        )


def _validate_parameters(measure: str, parameters: Any, *, path: str) -> None:
    if not isinstance(parameters, Mapping):
        raise TaskOracleContractError(f"{path} must be an object")
    if measure in {
        "object_footprint_contained_by_receptacle",
        "all_object_footprints_contained_by_receptacle",
        "all_object_footprints_contained_by_assigned_receptacle",
    }:
        if set(parameters) != {"minimum_wall_margin_m"}:
            raise TaskOracleContractError(
                f"{path} must contain only minimum_wall_margin_m"
            )
        _finite_positive(
            parameters["minimum_wall_margin_m"],
            path=f"{path}.minimum_wall_margin_m",
            allow_zero=True,
        )
    elif measure == "object_contained_by_bowl":
        expected = {
            "maximum_radial_center_distance_m",
            "maximum_center_height_above_rim_m",
        }
        if set(parameters) != expected:
            raise TaskOracleContractError(f"{path} has an invalid bowl parameter set")
        for name in sorted(expected):
            _finite_positive(parameters[name], path=f"{path}.{name}")
    elif measure == "successful_containment_event_order":
        expected = {"minimum_wall_margin_m", "stable_placement_event"}
        if set(parameters) != expected:
            raise TaskOracleContractError(f"{path} has an invalid event parameter set")
        _finite_positive(
            parameters["minimum_wall_margin_m"],
            path=f"{path}.minimum_wall_margin_m",
            allow_zero=True,
        )
        event = parameters["stable_placement_event"]
        if not isinstance(event, Mapping) or set(event) != {
            "continuous_window_s",
            "all_of",
            "latch_first_qualifying_event",
            "revoke_after_latch_on_transient_jitter",
        }:
            raise TaskOracleContractError(f"{path}.stable_placement_event is malformed")
        _finite_positive(
            event["continuous_window_s"],
            path=f"{path}.stable_placement_event.continuous_window_s",
        )
        if tuple(event["all_of"]) != (
            "object_footprint_contained_by_receptacle",
            "object_supported_by_table",
            "object_grasped_is_false",
        ):
            raise TaskOracleContractError(
                f"{path}.stable_placement_event.all_of is unsupported"
            )
        if event["latch_first_qualifying_event"] is not True:
            raise TaskOracleContractError(f"{path} must latch the first qualifying event")
        if event["revoke_after_latch_on_transient_jitter"] is not False:
            raise TaskOracleContractError(f"{path} must retain a latched event")
    else:
        raise TaskOracleContractError(f"{path} is not supported for measure {measure!r}")


def _parse_condition(
    raw: Mapping[str, Any], *, task_id: str, index: int
) -> FrozenOracleCondition:
    path = f"predicates.{task_id}.all_of[{index}]"
    measure = str(raw.get("measure", ""))
    spec = _MEASURE_SPECS.get(measure)
    if spec is None:
        raise TaskOracleContractError(f"{path}.measure {measure!r} is unsupported")
    if raw.get("operator") != spec.operator:
        raise TaskOracleContractError(
            f"{path}.operator must be {spec.operator!r} for {measure!r}"
        )
    expected_fields = _BASE_CONDITION_FIELDS | spec.required_fields
    if set(raw) != expected_fields:
        raise TaskOracleContractError(
            f"{path} fields must be exactly {sorted(expected_fields)}"
        )
    if "tolerance" in raw:
        _finite_positive(raw["tolerance"], path=f"{path}.tolerance")
    for name in ("object_id", "support_object_id"):
        value = raw.get(name)
        if value is not None and value not in _TASK_OBJECTS[task_id]:
            raise TaskOracleContractError(f"{path}.{name} is not an object in this task")
    object_ids = raw.get("object_ids")
    if object_ids is not None and frozenset(object_ids) != _TASK_OBJECTS[task_id]:
        raise TaskOracleContractError(f"{path}.object_ids must name the complete task object set")
    receptacle_id = raw.get("receptacle_id")
    if receptacle_id is not None and receptacle_id not in _TASK_RECEPTACLES[task_id]:
        raise TaskOracleContractError(f"{path}.receptacle_id is not a task receptacle")
    target_axis = raw.get("target_axis")
    if target_axis is not None:
        axis = tuple(float(value) for value in target_axis)
        if axis != (0.0, 0.0, 1.0):
            raise TaskOracleContractError(f"{path}.target_axis must be world +Z")
    if "parameters" in raw:
        _validate_parameters(measure, raw["parameters"], path=f"{path}.parameters")
    fields = {key: value for key, value in raw.items() if key not in _BASE_CONDITION_FIELDS}
    return FrozenOracleCondition(
        measure=measure,
        operator=spec.operator,
        fields=_deep_freeze(fields),
    )


def parse_task_oracle_contract(
    payload: bytes,
    *,
    schema_path: str | Path = DEFAULT_TASK_ORACLE_SCHEMA,
) -> FrozenTaskOracleContract:
    """Parse exact frozen bytes into the one P0 scoring authority."""

    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise TaskOracleContractError("task oracle contract is not UTF-8") from exc
    try:
        parsed = yaml.load(text, Loader=_UniqueKeyLoader)
    except TaskOracleContractError:
        raise
    except yaml.YAMLError as exc:
        raise TaskOracleContractError("task oracle contract is not valid YAML") from exc
    if not isinstance(parsed, Mapping):
        raise TaskOracleContractError("task oracle contract must be an object")
    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskOracleContractError("private task oracle schema is unavailable") from exc
    issues = validate_json_schema(parsed, schema, instance_path="$task_oracles")
    if issues:
        compact = "; ".join(f"{item.path}: {item.message}" for item in issues[:8])
        raise TaskOracleContractError(f"task oracle schema validation failed: {compact}")

    predicates = parsed["predicates"]
    expected_task_ids = set(EXPECTED_MEASURES_BY_TASK)
    observed_task_ids = {
        str(record.get("task_id"))
        for record in predicates.values()
        if isinstance(record, Mapping)
    }
    if observed_task_ids != expected_task_ids:
        missing = sorted(expected_task_ids - observed_task_ids)
        extra = sorted(observed_task_ids - expected_task_ids)
        raise TaskOracleContractError(
            f"task oracle coverage must be exactly 12 P0 tasks; missing={missing}, extra={extra}"
        )

    parsed_by_task: dict[str, FrozenTaskPredicate] = {}
    for oracle_id, raw_predicate in predicates.items():
        if not isinstance(raw_predicate, Mapping):
            raise TaskOracleContractError(f"predicate {oracle_id!r} must be an object")
        task_id = str(raw_predicate["task_id"])
        expected_oracle_id = "oracle." + task_id.removeprefix("soarm101_p0_")
        if oracle_id != expected_oracle_id:
            raise TaskOracleContractError(
                f"task {task_id!r} must use oracle id {expected_oracle_id!r}"
            )
        raw_conditions = raw_predicate["all_of"]
        observed_measures = tuple(str(item.get("measure")) for item in raw_conditions)
        expected_measures = EXPECTED_MEASURES_BY_TASK[task_id]
        if observed_measures != expected_measures:
            raise TaskOracleContractError(
                f"task {task_id!r} measures must be exactly {expected_measures!r}"
            )
        conditions = tuple(
            _parse_condition(item, task_id=task_id, index=index)
            for index, item in enumerate(raw_conditions)
        )
        settle = raw_predicate.get("settle_window_s")
        hold = raw_predicate.get("hold_window_s")
        parsed_by_task[task_id] = FrozenTaskPredicate(
            oracle_id=str(oracle_id),
            task_id=task_id,
            timeout_s=_finite_positive(
                raw_predicate["timeout_s"], path=f"predicates.{oracle_id}.timeout_s"
            ),
            settle_window_s=(
                None
                if settle is None
                else _finite_positive(
                    settle,
                    path=f"predicates.{oracle_id}.settle_window_s",
                    allow_zero=True,
                )
            ),
            hold_window_s=(
                None
                if hold is None
                else _finite_positive(
                    hold, path=f"predicates.{oracle_id}.hold_window_s"
                )
            ),
            conditions=conditions,
        )
        parsed_predicate = parsed_by_task[task_id]
        if parsed_predicate.evaluation_window_s > parsed_predicate.timeout_s:
            raise TaskOracleContractError(
                f"task {task_id!r} evaluation window exceeds its timeout"
            )
        if task_id == "soarm101_p0_place_two_objects_in_tray":
            event = parsed_predicate.condition(
                "successful_containment_event_order"
            ).fields["parameters"]["stable_placement_event"]
            event_window = _finite_positive(
                event["continuous_window_s"],
                path=(
                    "predicates.oracle.place_two_objects_in_tray."
                    "stable_placement_event.continuous_window_s"
                ),
            )
            if event_window > parsed_predicate.evaluation_window_s:
                raise TaskOracleContractError(
                    "stable placement event window exceeds the task settle window"
                )

    forbidden: dict[str, Mapping[str, Any]] = {}
    expected_forbidden = {
        "infrastructure_error",
        "object_outside_table_support_polygon",
        "non_gripper_robot_table_collision",
        "actuator_or_joint_limit_exceeded",
        "simulation_nan_or_instability",
    }
    for record in parsed["common_forbidden_conditions"]:
        name = str(record["condition"])
        if name in forbidden:
            raise TaskOracleContractError(f"duplicate forbidden condition {name!r}")
        forbidden[name] = _deep_freeze(record)
    if set(forbidden) != expected_forbidden:
        raise TaskOracleContractError("common forbidden condition set is incomplete")
    limit = forbidden["actuator_or_joint_limit_exceeded"].get("parameters")
    if not isinstance(limit, Mapping) or set(limit) != {"minimum_limit_margin_rad"}:
        raise TaskOracleContractError("joint-limit forbidden condition parameters are malformed")
    _finite_positive(
        limit["minimum_limit_margin_rad"],
        path="common_forbidden_conditions.actuator_or_joint_limit_exceeded.minimum_limit_margin_rad",
        allow_zero=True,
    )
    for name, record in forbidden.items():
        expected_disposition = (
            "do_not_score_as_capability_failure"
            if name == "infrastructure_error"
            else "fail"
        )
        if record.get("disposition") != expected_disposition:
            raise TaskOracleContractError(
                f"forbidden condition {name!r} has an invalid disposition"
            )
        if name != "actuator_or_joint_limit_exceeded" and "parameters" in record:
            raise TaskOracleContractError(
                f"forbidden condition {name!r} must not declare parameters"
            )

    return FrozenTaskOracleContract(
        schema_version=str(parsed["schema_version"]),
        oracle_set_id=str(parsed["oracle_set_id"]),
        version=str(parsed["version"]),
        source_sha256=sha256_bytes(payload),
        predicates_by_task=MappingProxyType(parsed_by_task),
        forbidden_by_name=MappingProxyType(forbidden),
        raw=_deep_freeze(parsed),
    )


def load_default_task_oracle_contract() -> FrozenTaskOracleContract:
    """Compatibility helper for direct environment unit tests only."""

    return parse_task_oracle_contract(DEFAULT_TASK_ORACLE_SOURCE.read_bytes())


def trusted_environment_context(
    instance: Mapping[str, Any], contract: FrozenTaskOracleContract
) -> dict[str, Any]:
    """Attach a framework-only contract without mutating or exposing the instance."""

    context = dict(instance)
    metadata = context.get("framework_metadata")
    trusted_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    trusted_metadata["task_oracle_contract"] = contract
    context["framework_metadata"] = trusted_metadata
    return context


def contract_from_environment_context(
    context: Mapping[str, Any] | None,
) -> FrozenTaskOracleContract | None:
    metadata = context.get("framework_metadata") if isinstance(context, Mapping) else None
    value = metadata.get("task_oracle_contract") if isinstance(metadata, Mapping) else None
    if value is None:
        return None
    if not isinstance(value, FrozenTaskOracleContract):
        raise TaskOracleContractError("trusted task oracle contract has an invalid type")
    return value
