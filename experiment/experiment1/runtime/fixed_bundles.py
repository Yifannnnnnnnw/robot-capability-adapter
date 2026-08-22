"""Deterministic audit for Experiment 1's fixed B1 bundle format.

The B1 bundle is intentionally independent of TGCD/IVC.  It contains the
prospectively fixed capability requests and inline private Harness cases used
by every backbone, condition, replicate, and attempt in Experiment 1.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


CAPABILITY_IDS_BY_ROBOT: dict[str, tuple[str, ...]] = {
    "robotstudio_so101": ("A1", "A2", "A3", "A4", "A5", "A6"),
    "unitree-go2-stock-12dof": ("G1", "G2", "G3", "G4", "G5"),
    "leap_hand": ("L1", "L2", "L3", "L4", "L5", "L6"),
    "aloha_2": ("AL1", "AL2", "AL3", "AL4", "AL5", "AL6"),
}

METHOD_BY_CAPABILITY: dict[str, str] = {
    "A1": "move_end_effector_to_position",
    "A2": "trace_cartesian_path",
    "A3": "set_gripper_opening",
    "A4": "approach_until_contact",
    "A5": "move_cartesian_offset_and_return",
    "A6": "set_wrist_roll",
    "G1": "track_planar_twist",
    "G2": "move_body_relative_pose",
    "G3": "trace_planar_path",
    "G4": "set_body_height",
    "G5": "hold_stable_stance",
    "L1": "move_hand_to_joint_pose",
    "L2": "trace_hand_joint_path",
    "L3": "reach_fingertips_to_targets",
    "L4": "establish_fingertip_contact_pattern",
    "L5": "hold_fingertip_contacts",
    "L6": "move_fingertip_offset_and_return",
    "ST1": "move_base_relative",
    "ST2": "turn_base_to_heading",
    "ST3": "move_tool_to_position",
    "ST4": "trace_tool_cartesian_path",
    "ST5": "set_gripper_opening",
    "ST6": "set_wrist_yaw",
    "ST7": "approach_until_contact",
    "ST8": "move_tool_offset_and_return",
    "AL1": "move_arm_to_position",
    "AL2": "trace_arm_cartesian_path",
    "AL3": "set_gripper_opening",
    "AL4": "move_bimanual_to_positions",
    "AL5": "approach_until_contact",
    "AL6": "move_bimanual_offsets_and_return",
}

CASE_VARIANTS = ("H1", "H2", "H3")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class B1FixedBundleError(ValueError):
    """Raised when a fixed Experiment 1 bundle is incomplete or mutable."""


def _text(value: Mapping[str, Any], field: str, *, where: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise B1FixedBundleError(f"{where}.{field} must be non-empty text")
    return item.strip()


def _finite_tree(value: Any, *, where: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise B1FixedBundleError(f"{where} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise B1FixedBundleError(f"{where} contains a non-string object key")
            _finite_tree(child, where=f"{where}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _finite_tree(child, where=f"{where}[{index}]")
        return
    raise B1FixedBundleError(f"{where} contains a non-JSON value")


def _root_identity(document: Mapping[str, Any], package: Any, *, artifact_type: str) -> None:
    expected = {
        "artifact_type": artifact_type,
        "schema_version": "1.0",
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
    }
    for field, value in expected.items():
        if document.get(field) != value:
            raise B1FixedBundleError(f"{field} must equal {value!r}")


def _schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    return False


def _validate_schema_value(value: Any, schema: Mapping[str, Any], *, where: str) -> None:
    expected_type = schema.get("type")
    if not isinstance(expected_type, str) or not _schema_type_matches(value, expected_type):
        raise B1FixedBundleError(
            f"{where} must satisfy JSON type {expected_type!r}"
        )
    if "const" in schema and value != schema["const"]:
        raise B1FixedBundleError(f"{where} must equal the schema constant")
    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or value not in enum):
        raise B1FixedBundleError(f"{where} is outside the schema enum")
    if expected_type in {"number", "integer"}:
        number = float(value)
        if "minimum" in schema and number < float(schema["minimum"]):
            raise B1FixedBundleError(f"{where} is below its minimum")
        if "maximum" in schema and number > float(schema["maximum"]):
            raise B1FixedBundleError(f"{where} is above its maximum")
        if "exclusiveMinimum" in schema and number <= float(schema["exclusiveMinimum"]):
            raise B1FixedBundleError(f"{where} is below its exclusive minimum")
        if "exclusiveMaximum" in schema and number >= float(schema["exclusiveMaximum"]):
            raise B1FixedBundleError(f"{where} is above its exclusive maximum")
    elif expected_type == "array":
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise B1FixedBundleError(f"{where} has too few items")
        if isinstance(maximum, int) and len(value) > maximum:
            raise B1FixedBundleError(f"{where} has too many items")
        if schema.get("uniqueItems") is True:
            encoded = [repr(item) for item in value]
            if len(encoded) != len(set(encoded)):
                raise B1FixedBundleError(f"{where} must contain unique items")
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            raise B1FixedBundleError(f"{where} array schema must declare items")
        for index, item in enumerate(value):
            _validate_schema_value(item, item_schema, where=f"{where}[{index}]")
    elif expected_type == "object":
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            raise B1FixedBundleError(f"{where} object schema is incomplete")
        if any(not isinstance(name, str) for name in required):
            raise B1FixedBundleError(f"{where} required fields must be strings")
        missing = [name for name in required if name not in value]
        if missing:
            raise B1FixedBundleError(f"{where} is missing fields {missing}")
        if schema.get("additionalProperties") is not False:
            raise B1FixedBundleError(
                f"{where} object schema must reject additional properties"
            )
        unsupported = sorted(set(value) - set(properties))
        if unsupported:
            raise B1FixedBundleError(
                f"{where} contains unsupported fields {unsupported}"
            )
        for name, item in value.items():
            child_schema = properties.get(name)
            if not isinstance(child_schema, Mapping):
                raise B1FixedBundleError(f"{where}.{name} lacks a property schema")
            _validate_schema_value(item, child_schema, where=f"{where}.{name}")


def _validate_request_schema(schema: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(schema, Mapping):
        raise B1FixedBundleError(f"{where} must be an object")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise B1FixedBundleError(
            f"{where} must be a closed JSON object schema"
        )
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, Mapping) or not properties:
        raise B1FixedBundleError(f"{where}.properties must be non-empty")
    if (
        not isinstance(required, list)
        or any(not isinstance(item, str) for item in required)
        or len(required) != len(set(required))
        or set(required) != set(properties)
    ):
        raise B1FixedBundleError(
            f"{where}.required must list every property exactly once"
        )
    _finite_tree(schema, where=where)
    return dict(schema)


def validate_b1_fixed_capability_design(
    design: Mapping[str, Any], package: Any
) -> dict[str, Any]:
    """Audit the fixed public B1 contract without invoking TGCD."""

    _root_identity(design, package, artifact_type="b1_fixed_capability_design")
    abi = design.get("invocation_abi")
    if not isinstance(abi, Mapping) or abi.get("kind") != "capability_request":
        raise B1FixedBundleError(
            "invocation_abi.kind must equal 'capability_request'"
        )
    if abi.get("method_call") != "method(request=request)":
        raise B1FixedBundleError(
            "invocation_abi.method_call must preserve method(request=request)"
        )
    expected_ids = CAPABILITY_IDS_BY_ROBOT.get(package.robot_configuration_id)
    if expected_ids is None:
        raise B1FixedBundleError("robot is outside the fixed Experiment 1 cohort")
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list):
        raise B1FixedBundleError("capabilities must be a list")
    found_ids: list[str] = []
    found_methods: list[str] = []
    for index, capability in enumerate(capabilities):
        where = f"capabilities[{index}]"
        if not isinstance(capability, Mapping):
            raise B1FixedBundleError(f"{where} must be an object")
        capability_id = _text(capability, "capability_id", where=where)
        method_name = _text(capability, "method_name", where=where)
        if method_name != METHOD_BY_CAPABILITY.get(capability_id):
            raise B1FixedBundleError(
                f"{where}.method_name differs from the fixed public register"
            )
        if not _IDENTIFIER.fullmatch(method_name):
            raise B1FixedBundleError(f"{where}.method_name is not a Python identifier")
        _text(capability, "description", where=where)
        schema = _validate_request_schema(
            capability.get("request_schema"), where=f"{where}.request_schema"
        )
        standard = capability.get("public_standard")
        if not isinstance(standard, Mapping):
            raise B1FixedBundleError(f"{where}.public_standard must be an object")
        if standard.get("criterion_id") != capability_id:
            raise B1FixedBundleError(
                f"{where}.public_standard must identify the capability criterion"
            )
        _text(standard, "criterion_text", where=f"{where}.public_standard")
        if standard.get("hidden_case_pass_rule") != {
            "minimum_passed": 2,
            "case_count": 3,
        }:
            raise B1FixedBundleError(
                f"{where}.public_standard must require two of three hidden cases"
            )
        # Exercise the schema structure now; case values are checked below.
        _validate_schema_value(
            {name: _schema_placeholder(item) for name, item in schema["properties"].items()},
            schema,
            where=f"{where}.request_schema",
        )
        found_ids.append(capability_id)
        found_methods.append(method_name)
    if tuple(found_ids) != expected_ids:
        raise B1FixedBundleError(
            f"capability order/set must equal {list(expected_ids)}"
        )
    if len(found_methods) != len(set(found_methods)):
        raise B1FixedBundleError("capability method names must be unique per robot")
    return dict(design)


def _schema_placeholder(schema: Any) -> Any:
    """Construct a minimal value solely to check the supported schema subset."""

    if not isinstance(schema, Mapping):
        raise B1FixedBundleError("request property schema must be an object")
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    expected = schema.get("type")
    if expected == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            raise B1FixedBundleError("nested object schema is incomplete")
        return {
            name: _schema_placeholder(properties[name])
            for name in required
            if name in properties
        }
    if expected == "array":
        count = int(schema.get("minItems", 1))
        return [_schema_placeholder(schema.get("items")) for _ in range(count)]
    if expected in {"number", "integer"}:
        if "minimum" in schema:
            return schema["minimum"]
        if "exclusiveMinimum" in schema:
            lower = float(schema["exclusiveMinimum"])
            upper = schema.get("maximum", schema.get("exclusiveMaximum"))
            value = lower + 1.0 if upper is None else (lower + float(upper)) / 2.0
            return int(value) if expected == "integer" else value
        return 0
    if expected == "string":
        return "value"
    if expected == "boolean":
        return False
    raise B1FixedBundleError(f"unsupported request schema type {expected!r}")


def _validate_case_relations(capability_id: str, request: Mapping[str, Any], *, where: str) -> None:
    if capability_id in {"L4", "L5"}:
        names = request.get("required_fingers")
        if isinstance(names, list):
            array_fields = (
                ("contact_target_positions_palm_m", "approach_directions_palm_unit")
                if capability_id == "L4"
                else ("separation_directions_palm_unit",)
            )
            for field in array_fields:
                if len(request.get(field, [])) != len(names):
                    raise B1FixedBundleError(
                        f"{where}.{field} must align with required_fingers"
                    )
    if capability_id == "L6":
        names = request.get("finger_names")
        if isinstance(names, list) and len(request.get("offsets_palm_m", [])) != len(names):
            raise B1FixedBundleError(
                f"{where}.offsets_palm_m must align with finger_names"
            )


def _validate_leap_framework_protocol(
    capability_id: str,
    case: Mapping[str, Any],
    request: Mapping[str, Any],
    parameters: Mapping[str, Any],
    *,
    where: str,
) -> None:
    preinvoke = case.get("preinvoke")
    events = case.get("framework_events")
    if capability_id != "L5":
        if preinvoke is not None or events is not None:
            raise B1FixedBundleError(
                f"{where} may declare preinvoke/events only for L5"
            )
        return

    fingers = request.get("required_fingers")
    directions = request.get("separation_directions_palm_unit")
    target_bodies = parameters.get("target_body_names")
    fingertip_geoms = parameters.get("fingertip_geom_names")
    target_geoms = parameters.get("target_geom_names")
    if (
        not isinstance(fingers, list)
        or not isinstance(directions, list)
        or not isinstance(target_bodies, Mapping)
        or not isinstance(fingertip_geoms, Mapping)
        or not isinstance(target_geoms, Mapping)
    ):
        raise B1FixedBundleError(f"{where} has incomplete L5 finger bindings")
    expected_bodies = {str(target_bodies[finger]) for finger in fingers}
    reset = case.get("reset")
    placements = reset.get("mocap_body_positions") if isinstance(reset, Mapping) else None
    if not isinstance(placements, Mapping) or set(placements) != expected_bodies:
        raise B1FixedBundleError(
            f"{where}.reset must place every and only requested L5 target"
        )

    if not isinstance(preinvoke, Mapping) or preinvoke.get("duration_s") != 0.10:
        raise B1FixedBundleError(f"{where}.preinvoke must last exactly 0.10 s")
    pairs = preinvoke.get("required_contact_pairs")
    expected_pairs = {
        tuple(sorted((str(fingertip_geoms[finger][0]), str(target_geoms[finger][0]))))
        for finger in fingers
    }
    observed_pairs = {
        tuple(sorted((str(pair.get("geom1")), str(pair.get("geom2")))))
        for pair in pairs
        if isinstance(pair, Mapping)
    } if isinstance(pairs, list) else set()
    if observed_pairs != expected_pairs or len(observed_pairs) != len(fingers):
        raise B1FixedBundleError(
            f"{where}.preinvoke must hold every aligned L5 contact pair"
        )

    if not isinstance(events, list) or len(events) != len(fingers):
        raise B1FixedBundleError(f"{where}.framework_events must align with fingers")
    events_by_body: dict[str, Mapping[str, Any]] = {}
    for event in events:
        if not isinstance(event, Mapping) or event.get("kind") != "move_mocap_body":
            raise B1FixedBundleError(f"{where}.framework_events has an invalid event")
        body_name = event.get("body_name")
        if not isinstance(body_name, str) or body_name in events_by_body:
            raise B1FixedBundleError(f"{where}.framework_events has duplicate targets")
        if (
            event.get("frame_body_name") != "palm"
            or event.get("start_time_s") != 0.25
            or event.get("duration_s") != 0.05
        ):
            raise B1FixedBundleError(
                f"{where}.framework_events must use the fixed L5 timing/frame"
            )
        events_by_body[body_name] = event
    if set(events_by_body) != expected_bodies:
        raise B1FixedBundleError(
            f"{where}.framework_events must move every requested target"
        )
    for finger, raw_direction in zip(fingers, directions):
        event = events_by_body[str(target_bodies[finger])]
        displacement = event.get("displacement_m")
        if not isinstance(displacement, list) or len(displacement) != 3:
            raise B1FixedBundleError(
                f"{where}.framework_events displacement must be a 3-vector"
            )
        magnitude = math.sqrt(sum(float(value) ** 2 for value in displacement))
        direction_norm = math.sqrt(sum(float(value) ** 2 for value in raw_direction))
        alignment = sum(
            float(value) * float(axis)
            for value, axis in zip(displacement, raw_direction)
        )
        if (
            magnitude < 0.006 - 1.0e-12
            or magnitude > 0.008 + 1.0e-12
            or direction_norm <= 0.0
            or alignment / (magnitude * direction_norm) < 1.0 - 1.0e-9
        ):
            raise B1FixedBundleError(
                f"{where}.framework_events must move 6-8 mm along the requested direction"
            )


def _validate_criterion(criterion: Any, *, where: str) -> None:
    expected = {
        "metric": "b1_contract_binary",
        "unit": "binary",
        "comparator": ">=",
        "threshold": 1,
        "temporal": {"kind": "fixed_trials"},
        "aggregation": {"kind": "single_trial"},
    }
    if not isinstance(criterion, Mapping):
        raise B1FixedBundleError(f"{where} must be an object")
    for field, value in expected.items():
        if criterion.get(field) != value:
            raise B1FixedBundleError(f"{where}.{field} must equal {value!r}")
    refs = criterion.get("source_refs")
    if not isinstance(refs, list) or not refs or any(
        not isinstance(item, str) or not item for item in refs
    ):
        raise B1FixedBundleError(f"{where}.source_refs must be non-empty strings")


def _validate_scene(package: Any, value: Any, *, where: str) -> None:
    if not isinstance(value, str) or not value.startswith("assets/"):
        raise B1FixedBundleError(f"{where} must name a package assets scene")
    path = (package.root / value).resolve()
    assets = (package.root / "assets").resolve()
    try:
        path.relative_to(assets)
    except ValueError as exc:
        raise B1FixedBundleError(f"{where} escapes package assets") from exc
    if not path.is_file() or path.suffix.lower() != ".xml":
        raise B1FixedBundleError(f"{where} scene does not exist: {value}")


def validate_b1_fixed_validation_suite(
    suite: Mapping[str, Any], *, package: Any, design: Mapping[str, Any]
) -> dict[str, Any]:
    """Audit three inline H1/H2/H3 cases per fixed B1 capability."""

    _root_identity(suite, package, artifact_type="b1_fixed_validation_suite")
    if suite.get("whole_suite_aggregation") != {
        "kind": "all_capabilities_two_of_three_cases"
    }:
        raise B1FixedBundleError(
            "whole_suite_aggregation must require two of three cases for every capability"
        )
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list):
        raise B1FixedBundleError("validated design lacks capabilities")
    by_id = {
        str(item["capability_id"]): item
        for item in capabilities
        if isinstance(item, Mapping) and isinstance(item.get("capability_id"), str)
    }
    cases = suite.get("cases")
    if not isinstance(cases, list):
        raise B1FixedBundleError("cases must be a list")
    expected_ids = CAPABILITY_IDS_BY_ROBOT[package.robot_configuration_id]
    if len(cases) != len(expected_ids) * len(CASE_VARIANTS):
        raise B1FixedBundleError("suite must contain exactly three cases per capability")
    coverage: Counter[tuple[str, str]] = Counter()
    case_ids: set[str] = set()
    for index, case in enumerate(cases):
        where = f"cases[{index}]"
        if not isinstance(case, Mapping):
            raise B1FixedBundleError(f"{where} must be an object")
        case_id = _text(case, "case_id", where=where)
        if case_id in case_ids:
            raise B1FixedBundleError(f"duplicate case_id {case_id!r}")
        case_ids.add(case_id)
        variant = _text(case, "case_variant", where=where)
        capability_id = _text(case, "capability_id", where=where)
        capability = by_id.get(capability_id)
        if capability is None:
            raise B1FixedBundleError(f"{where} references an unknown capability")
        if variant not in CASE_VARIANTS or case_id != f"{capability_id}-{variant}":
            raise B1FixedBundleError(f"{where} has an invalid H1/H2/H3 identity")
        if case.get("method_name") != capability.get("method_name"):
            raise B1FixedBundleError(f"{where}.method_name differs from fixed design")
        _validate_scene(package, case.get("scene_entrypoint"), where=f"{where}.scene_entrypoint")
        request = case.get("request")
        if not isinstance(request, Mapping):
            raise B1FixedBundleError(f"{where}.request must be an object")
        _validate_schema_value(
            request,
            capability["request_schema"],
            where=f"{where}.request",
        )
        _validate_case_relations(capability_id, request, where=f"{where}.request")
        reset = case.get("reset")
        if not isinstance(reset, Mapping) or reset.get("kind") not in {"default", "keyframe"}:
            raise B1FixedBundleError(f"{where}.reset must be a supported Framework reset")
        _finite_tree(reset, where=f"{where}.reset")
        for field in ("max_steps", "repetitions"):
            value = case.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise B1FixedBundleError(f"{where}.{field} must be a positive integer")
        if case.get("repetitions") != 1:
            raise B1FixedBundleError(f"{where}.repetitions must equal one")
        for field in ("sample_hz", "timeout_sim_s"):
            value = case.get(field)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or float(value) <= 0
            ):
                raise B1FixedBundleError(f"{where}.{field} must be finite and positive")
        binding = case.get("binding")
        if not isinstance(binding, Mapping) or binding.get("kind") != "b1_contract":
            raise B1FixedBundleError(f"{where}.binding must use b1_contract")
        parameters = binding.get("parameters")
        if not isinstance(parameters, Mapping) or parameters.get("contract_id") != capability_id:
            raise B1FixedBundleError(
                f"{where}.binding.parameters must identify {capability_id}"
            )
        expected_profile = {
            "robotstudio_so101": "so101",
            "aloha_2": "aloha2",
        }.get(package.robot_configuration_id)
        if expected_profile is not None and parameters.get(
            "side_effect_guard_profile"
        ) != expected_profile:
            raise B1FixedBundleError(
                f"{where}.binding.parameters must select {expected_profile!r} side-effect guards"
            )
        if capability_id in {"A4", "AL5"} and parameters.get(
            "precontact_gate"
        ) != "held_window_then_ray":
            raise B1FixedBundleError(
                f"{where}.binding.parameters must require the held precontact gate"
            )
        _validate_leap_framework_protocol(
            capability_id,
            case,
            request,
            parameters,
            where=where,
        )
        guards = case.get("guards")
        if not isinstance(guards, list) or not guards:
            raise B1FixedBundleError(f"{where}.guards must be non-empty")
        guard_ids: set[str] = set()
        for guard_index, guard in enumerate(guards):
            guard_where = f"{where}.guards[{guard_index}]"
            if not isinstance(guard, Mapping):
                raise B1FixedBundleError(f"{guard_where} must be an object")
            guard_id = _text(guard, "guard_id", where=guard_where)
            kind = _text(guard, "kind", where=guard_where)
            if kind not in {
                "actuator_and_physics_step_required",
                "no_direct_state_write",
                "canonical_model_data",
                "control_range",
            }:
                raise B1FixedBundleError(f"{guard_where} uses an unsupported guard")
            if guard_id in guard_ids:
                raise B1FixedBundleError(f"{where}.guards duplicates {guard_id!r}")
            guard_ids.add(guard_id)
        guard_kinds = {str(guard["kind"]) for guard in guards}
        required_guard_kinds = {
            "actuator_and_physics_step_required",
            "no_direct_state_write",
            "canonical_model_data",
            "control_range",
        }
        if guard_kinds != required_guard_kinds:
            raise B1FixedBundleError(
                f"{where}.guards must contain exactly the four common B1 guards"
            )
        _validate_criterion(case.get("criterion"), where=f"{where}.criterion")
        coverage[(capability_id, variant)] += 1
    expected = {(capability_id, variant) for capability_id in expected_ids for variant in CASE_VARIANTS}
    if set(coverage) != expected or any(count != 1 for count in coverage.values()):
        raise B1FixedBundleError("suite must cover H1/H2/H3 exactly once per capability")
    return dict(suite)


def validate_b1_fixed_bundle(
    design: Mapping[str, Any], suite: Mapping[str, Any], *, package: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    validated_design = validate_b1_fixed_capability_design(design, package)
    validated_suite = validate_b1_fixed_validation_suite(
        suite, package=package, design=validated_design
    )
    return validated_design, validated_suite


__all__ = [
    "B1FixedBundleError",
    "CAPABILITY_IDS_BY_ROBOT",
    "CASE_VARIANTS",
    "METHOD_BY_CAPABILITY",
    "validate_b1_fixed_bundle",
    "validate_b1_fixed_capability_design",
    "validate_b1_fixed_validation_suite",
]
