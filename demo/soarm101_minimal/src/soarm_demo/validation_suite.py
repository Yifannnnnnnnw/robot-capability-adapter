"""Three-pass private validation-suite generation and deterministic freezing.

The model designs *cases*, while trusted framework code owns execution.  This
keeps Development Validation as direct Python invocation rather than a second
language-conditioned Agent.
"""

from __future__ import annotations

import inspect
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .audit import BudgetCounter, JsonlTrace, atomic_write_json, sha256_json
from .bridge.scene_catalog import SceneAssetCatalog, SceneAssetCatalogError
from .model_client import ModelClient, ModelMessage
from .schema_validation import validate_json_schema


SUITE_SCHEMA_VERSION = "robot_capability.validation_suite.v2"
SUITE_FREEZE_SCHEMA_VERSION = "robot_capability.validation_suite_freeze.v2"
HARNESS_TIMEOUT_FIELD = "timeout_s"
HARNESS_TIMEOUT_CLOCK = "host_monotonic_wall"
PUBLIC_TIMEOUT_CLOCK = "mujoco_simulation_time"
TIMEOUT_CONTRACT = {
    "harness_deadline_field": f"cases[].{HARNESS_TIMEOUT_FIELD}",
    "harness_deadline_clock": HARNESS_TIMEOUT_CLOCK,
    "public_timeout_clock": PUBLIC_TIMEOUT_CLOCK,
    "nominal_optional_public_timeout_policy": "omit_argument_use_declared_default",
    "cross_clock_numeric_margin_required": False,
}
CASE_FIELDS = {
    "case_id",
    "capability_id",
    "module",
    "function_name",
    "initial_state",
    "call_arguments",
    "target_measurements",
    "tolerances",
    "forbidden_conditions",
    "timeout_s",
    "test_entrypoint",
    "reference_provenance",
}

LEROBOT_JOINT_POSITION_KEYS = frozenset(
    {
        "shoulder_pan.pos",
        "shoulder_lift.pos",
        "elbow_flex.pos",
        "wrist_flex.pos",
        "wrist_roll.pos",
        "gripper.pos",
    }
)
LEROBOT_MOTOR_NAMES = frozenset(key.removesuffix(".pos") for key in LEROBOT_JOINT_POSITION_KEYS)
ARM_MOTOR_NAMES = LEROBOT_MOTOR_NAMES - {"gripper"}
PHYSICAL_REFERENCE_SCHEMA_VERSION = "robot_capability.validation_reference.v2"
_PHYSICAL_MEASUREMENT_BY_MODULE = {
    "g1": "joint_positions",
    "g2": "end_effector_position_m",
    "g3": "object_positions_m",
}
_PHYSICAL_FORBIDDEN_BY_MODULE = {
    "g1": frozenset(
        {
            "action_clipped",
            "actuator_or_joint_limit_exceeded",
            "simulation_nan_or_instability",
        }
    ),
    "g2": frozenset(
        {
            "action_clipped",
            "actuator_or_joint_limit_exceeded",
            "non_gripper_robot_table_collision",
            "simulation_nan_or_instability",
        }
    ),
    "g3": frozenset(
        {
            "action_clipped",
            "actuator_or_joint_limit_exceeded",
            "non_gripper_robot_table_collision",
            "object_outside_table_support_polygon",
            "simulation_nan_or_instability",
        }
    ),
}

_CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
# These are validation-contract bounds, not claimed controller accuracy.  They
# prevent a model-authored suite from turning numerical noise into a repair or
# from accepting a vacuous target.  Units follow the trusted measurement API:
# LeRobot arm degrees for G1 and metres for G2/G3.
_PHYSICAL_TOLERANCE_RANGE = {
    "g1": (0.25, 2.0),
    "g2": (0.005, 0.030),
    "g3": (0.008, 0.035),
}
_PHYSICAL_TIMEOUT_FLOOR_S = {"g1": 3.0, "g2": 8.0, "g3": 12.0}
_P0_WORKSPACE_BOUNDS_M = (
    (0.22, 0.46),
    (-0.14, 0.14),
    (0.035, 0.22),
)


class ValidationSuiteError(RuntimeError):
    """The three-pass model output could not be frozen safely."""


def _plain_catalog_value(value: Any) -> Any:
    """Detach immutable catalog projections into ordinary finite JSON values."""

    if isinstance(value, Mapping):
        return {str(key): _plain_catalog_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain_catalog_value(item) for item in value]
    return value


def _catalog_physical_projection(
    cases: Sequence[Any],
    reference_library: Mapping[str, Any],
) -> tuple[list[Any], list[str]]:
    """Resolve model-authored refs for trusted checks without changing the suite.

    The frozen case remains the compact ``asset_ref + id + pose``
    document authored by the validation Agent.  This detached copy adds
    morphology-owned geometry solely so deterministic extent/contact checks can
    compare public arguments with the selected immutable asset.
    """

    catalog_document = reference_library.get("scene_asset_catalog")
    if not isinstance(catalog_document, Mapping):
        if reference_library.get("require_scene_asset_refs") is True:
            return list(deepcopy(cases)), [
                "validation_reference.scene_asset_catalog must contain the frozen "
                "Morphology scene asset catalog"
            ]
        # Compatibility for orchestration/unit fixtures authored before the
        # catalog boundary. Formal AWS pipeline context always sets the flag.
        return list(deepcopy(cases)), []
    try:
        catalog = SceneAssetCatalog(catalog_document)
    except SceneAssetCatalogError:
        return list(deepcopy(cases)), [
            "validation_reference.scene_asset_catalog is invalid"
        ]

    projected = list(deepcopy(cases))
    errors: list[str] = []
    for case_index, case in enumerate(projected):
        if not isinstance(case, Mapping):
            continue
        initial_state = case.get("initial_state")
        if not isinstance(initial_state, dict):
            continue
        for collection, expected_role in (
            ("bodies", "dynamic_object"),
            ("markers", "marker"),
        ):
            values = initial_state.get(collection, [])
            if not isinstance(values, list):
                continue
            for item_index, spec in enumerate(values):
                path = (
                    f"cases[{case_index}].initial_state.{collection}[{item_index}]"
                )
                if not isinstance(spec, Mapping):
                    continue
                if not isinstance(spec.get("asset_ref"), str):
                    errors.append(f"{path}.asset_ref must select a frozen scene asset")
                    continue
                try:
                    resolved = catalog.resolve(
                        spec,
                        expected_role=expected_role,
                        legacy_kind=None,
                    )
                except SceneAssetCatalogError as exc:
                    errors.append(f"{path}: {exc}")
                    continue
                identifier = spec.get("id")
                replacement = {
                    "id": identifier,
                    "kind": resolved.runtime_kind,
                    **_plain_catalog_value(resolved.parameters),
                }
                values[item_index] = replacement
    return projected, errors


@dataclass(frozen=True)
class SuiteCheck:
    ok: bool
    errors: tuple[str, ...]
    covered_capabilities: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "covered_capabilities": list(self.covered_capabilities),
        }


@dataclass(frozen=True)
class FrozenValidationSuite:
    suite: dict[str, Any]
    sha256: str
    model_calls: int
    output_path: Path
    generated_test_path: Path


def parse_json_document(text: str) -> dict[str, Any]:
    """Parse one JSON object, accepting a single Markdown JSON fence."""

    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        value = json.loads(
            candidate,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant {token!r} is forbidden")
            ),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValidationSuiteError(f"model output is not a JSON document: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationSuiteError("validation suite must be a JSON object")
    return value


def _finite_tree_errors(value: Any, path: str) -> list[str]:
    """Reject NaN/Infinity recursively before values reach an oracle."""

    if isinstance(value, float) and not math.isfinite(value):
        return [f"{path} must contain only finite numbers"]
    if isinstance(value, Mapping):
        errors: list[str] = []
        for key, nested in value.items():
            errors.extend(_finite_tree_errors(nested, f"{path}.{key}"))
        return errors
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        errors = []
        for index, nested in enumerate(value):
            errors.extend(_finite_tree_errors(nested, f"{path}[{index}]"))
        return errors
    return []


def _stage1_index(stage1: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    layers = stage1.get("layers", {})
    if not isinstance(layers, Mapping):
        return index
    for layer_name in ("G1", "G2", "G3"):
        layer = layers.get(layer_name, {})
        if not isinstance(layer, Mapping):
            continue
        capabilities = layer.get("capabilities", [])
        if not isinstance(capabilities, Sequence) or isinstance(capabilities, (str, bytes)):
            continue
        for capability in capabilities:
            if not isinstance(capability, Mapping):
                continue
            identifier = capability.get("capability_id")
            name = capability.get("function_name")
            validation_effect = capability.get("validation_effect")
            implementation_family = capability.get("implementation_family")
            if (
                isinstance(identifier, str)
                and isinstance(name, str)
                and isinstance(validation_effect, str)
                and isinstance(implementation_family, str)
            ):
                index[identifier] = {
                    "capability_id": identifier,
                    "function_name": name,
                    "module": layer_name.lower(),
                    "validation_effect": validation_effect,
                    "implementation_family": implementation_family,
                }
    return index


def _manifest_index(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    output: dict[str, Mapping[str, Any]] = {}
    capabilities = manifest.get("capabilities", [])
    if not isinstance(capabilities, Sequence) or isinstance(capabilities, (str, bytes)):
        return output
    for capability in capabilities:
        if isinstance(capability, Mapping) and isinstance(capability.get("capability_id"), str):
            output[str(capability["capability_id"])] = capability
    return output


def _signature_parameters(signature: Any) -> tuple[set[str], set[str]]:
    """Return (all public argument names, required names) from manifest JSON."""

    if not isinstance(signature, Mapping):
        return set(), set()
    parameters = signature.get("parameters", [])
    if not isinstance(parameters, Sequence) or isinstance(parameters, (str, bytes)):
        return set(), set()
    all_names: set[str] = set()
    required: set[str] = set()
    for parameter in parameters:
        if not isinstance(parameter, Mapping):
            continue
        name = parameter.get("name")
        if not isinstance(name, str) or name == "runtime":
            continue
        all_names.add(name)
        if not bool(parameter.get("has_default", False)):
            required.add(name)
    return all_names, required


def _target_has_numeric_leaf(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    if isinstance(value, Mapping):
        return any(_target_has_numeric_leaf(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_target_has_numeric_leaf(item) for item in value)
    return False


def _tolerance_shape_errors(target: Any, tolerance: Any, path: str) -> list[str]:
    """Require tolerances to mirror measurable numeric target paths.

    Booleans/strings need no tolerance. Numeric scalars and arrays do. This
    rejects dotted pseudo-paths such as ``object_positions_m.cube`` that the
    direct comparator would otherwise interpret as unrelated keys and thus as
    zero tolerance.
    """

    if not _target_has_numeric_leaf(target):
        return [f"{path}: boolean/string targets must not have a tolerance"]
    if isinstance(target, Mapping):
        if not isinstance(tolerance, Mapping):
            return [f"{path}: tolerance must be an object mirroring the target"]
        errors = [
            f"{path}.{key}: tolerance key is not present in target_measurements"
            for key in tolerance
            if key not in target
        ]
        for key, nested_target in target.items():
            needs_tolerance = _target_has_numeric_leaf(nested_target)
            if not needs_tolerance:
                if key in tolerance:
                    errors.append(
                        f"{path}.{key}: boolean/string targets must not have a tolerance"
                    )
                continue
            if key not in tolerance:
                errors.append(f"{path}.{key}: numeric target lacks a tolerance")
                continue
            errors.extend(
                _tolerance_shape_errors(
                    nested_target,
                    tolerance[key],
                    f"{path}.{key}",
                )
            )
        return errors
    if isinstance(target, Sequence) and not isinstance(target, (str, bytes)):
        if isinstance(tolerance, (int, float)) and not isinstance(tolerance, bool):
            value = float(tolerance)
            return [] if math.isfinite(value) and value > 0 else [
                f"{path}: array tolerance must be finite and positive"
            ]
        if not isinstance(tolerance, Sequence) or isinstance(tolerance, (str, bytes)):
            return [f"{path}: array tolerance must be a positive scalar or array"]
        if len(tolerance) != len(target):
            return [f"{path}: tolerance array length must match target array"]
        errors: list[str] = []
        for index, nested_target in enumerate(target):
            errors.extend(
                _tolerance_shape_errors(
                    nested_target,
                    tolerance[index],
                    f"{path}[{index}]",
                )
            )
        return errors
    if isinstance(target, (int, float)) and not isinstance(target, bool):
        if (
            not isinstance(tolerance, (int, float))
            or isinstance(tolerance, bool)
            or not math.isfinite(float(tolerance))
            or float(tolerance) <= 0
        ):
            return [f"{path}: numeric tolerance must be finite and positive"]
        return []
    return [f"{path}: boolean/string targets must not have a tolerance"]


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _vector_errors(value: Any, *, length: int, path: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return [f"{path} must be an array of {length} finite numbers"]
    if len(value) != length or not all(_is_finite_number(item) for item in value):
        return [f"{path} must be an array of {length} finite numbers"]
    return []


def _measurement_shape_errors(measurements: Mapping[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    for name, value in measurements.items():
        item_path = f"{path}.{name}"
        if name == "joint_positions":
            if not isinstance(value, Mapping) or not value:
                errors.append(f"{item_path} must be a non-empty object")
                continue
            invalid = set(value) - LEROBOT_JOINT_POSITION_KEYS
            if invalid:
                errors.append(
                    f"{item_path} uses invalid LeRobot keys: {sorted(invalid)}"
                )
            for key, nested in value.items():
                if not _is_finite_number(nested):
                    errors.append(f"{item_path}.{key} must be a finite number")
        elif name == "end_effector_position_m":
            errors.extend(_vector_errors(value, length=3, path=item_path))
        elif name == "object_positions_m":
            if not isinstance(value, Mapping) or not value:
                errors.append(f"{item_path} must be a non-empty object")
                continue
            for object_id, point in value.items():
                if not isinstance(object_id, str) or not object_id:
                    errors.append(f"{item_path} object IDs must be non-empty strings")
                    continue
                errors.extend(
                    _vector_errors(point, length=3, path=f"{item_path}.{object_id}")
                )
        elif name in {"contact", "finite", "finite_state"}:
            if not isinstance(value, bool):
                errors.append(f"{item_path} must be a boolean")
        elif name == "contacts":
            if not isinstance(value, list) or not all(
                isinstance(item, Mapping) for item in value
            ):
                errors.append(f"{item_path} must be an array of contact objects")
        elif name == "contact_pairs":
            if not isinstance(value, list):
                errors.append(f"{item_path} must be an array of two-body pairs")
                continue
            for index, pair in enumerate(value):
                if (
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or not all(isinstance(item, str) and item for item in pair)
                ):
                    errors.append(
                        f"{item_path}[{index}] must contain exactly two body names"
                    )
    return errors


def _initial_state_errors(
    initial_state: Mapping[str, Any], path: str
) -> tuple[list[str], dict[str, tuple[float, float, float]], dict[str, float]]:
    errors: list[str] = []
    allowed = {"bodies", "markers", "qpos_rad"}
    extra = set(initial_state) - allowed
    if extra:
        errors.append(f"{path} has unsupported physical fields: {sorted(extra)}")

    qpos: dict[str, float] = {}
    raw_qpos = initial_state.get("qpos_rad", {})
    if not isinstance(raw_qpos, Mapping):
        errors.append(f"{path}.qpos_rad must be an object keyed by motor name")
    else:
        invalid_qpos = set(raw_qpos) - LEROBOT_MOTOR_NAMES
        if invalid_qpos:
            errors.append(
                f"{path}.qpos_rad uses invalid motor names: {sorted(invalid_qpos)}"
            )
        for name, value in raw_qpos.items():
            if not _is_finite_number(value):
                errors.append(f"{path}.qpos_rad.{name} must be a finite radian value")
            else:
                qpos[str(name)] = float(value)

    body_positions: dict[str, tuple[float, float, float]] = {}
    raw_bodies = initial_state.get("bodies", [])
    if not isinstance(raw_bodies, list):
        errors.append(f"{path}.bodies must be an array")
    else:
        for index, raw_body in enumerate(raw_bodies):
            body_path = f"{path}.bodies[{index}]"
            if not isinstance(raw_body, Mapping):
                errors.append(f"{body_path} must be an object")
                continue
            identifier = raw_body.get("id")
            kind = raw_body.get("kind")
            point = raw_body.get("position_m")
            if not isinstance(identifier, str) or not identifier:
                errors.append(f"{body_path}.id must be a non-empty string")
            elif identifier in body_positions:
                errors.append(f"{body_path}.id duplicates body {identifier!r}")
            if kind not in {"cube", "cylinder"}:
                errors.append(f"{body_path}.kind must be 'cube' or 'cylinder'")
            point_errors = _vector_errors(point, length=3, path=f"{body_path}.position_m")
            errors.extend(point_errors)
            if isinstance(identifier, str) and identifier and not point_errors:
                body_positions[identifier] = tuple(float(item) for item in point)

    markers = initial_state.get("markers", [])
    if not isinstance(markers, list):
        errors.append(f"{path}.markers must be an array when supplied")
    return errors, body_positions, qpos


def _normalize_joint_argument_name(name: str) -> str | None:
    if name in LEROBOT_JOINT_POSITION_KEYS:
        return name
    if name in LEROBOT_MOTOR_NAMES:
        return f"{name}.pos"
    if name.endswith("_deg") and name.removesuffix("_deg") in ARM_MOTOR_NAMES:
        return f"{name.removesuffix('_deg')}.pos"
    if name in {"gripper_norm", "gripper_position", "gripper_pos"}:
        return "gripper.pos"
    return None


def _extract_joint_commands(value: Any, *, target_context: bool = False) -> dict[str, float]:
    commands: dict[str, float] = {}
    if not isinstance(value, Mapping):
        return commands
    for raw_name, nested in value.items():
        name = str(raw_name)
        normalized = _normalize_joint_argument_name(name)
        if normalized is not None and _is_finite_number(nested):
            commands[normalized] = float(nested)
            continue
        nested_context = target_context or "target" in name.lower() or name in {
            "targets",
            "joint_positions",
            "joints",
        }
        if isinstance(nested, Mapping) and nested_context:
            commands.update(_extract_joint_commands(nested, target_context=True))
    return commands


def _numeric_vector(value: Any) -> tuple[float, ...] | None:
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) in {2, 3}
        and all(_is_finite_number(item) for item in value)
    ):
        return tuple(float(item) for item in value)
    return None


def _extract_target_vectors(
    value: Any, *, module: str, target_context: bool = False
) -> list[tuple[float, ...]]:
    vectors: list[tuple[float, ...]] = []
    if isinstance(value, Mapping):
        for raw_name, nested in value.items():
            name = str(raw_name)
            nested_context = target_context or "target" in name.lower()
            if module == "g2" and name == "position_m":
                nested_context = True
            vector = _numeric_vector(nested)
            if nested_context and vector is not None:
                vectors.append(vector)
            vectors.extend(
                _extract_target_vectors(
                    nested,
                    module=module,
                    target_context=nested_context,
                )
            )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            vectors.extend(
                _extract_target_vectors(
                    nested,
                    module=module,
                    target_context=target_context,
                )
            )
    return vectors


def _vectors_match(left: Sequence[float], right: Sequence[float], *, planar: bool) -> bool:
    dimensions = 2 if planar and len(left) >= 2 and len(right) >= 2 else len(left)
    if not planar and len(left) != len(right):
        return False
    if dimensions > len(right):
        return False
    return all(
        math.isclose(float(left[index]), float(right[index]), rel_tol=0.0, abs_tol=1e-9)
        for index in range(dimensions)
    )


def _tolerance_at(tolerances: Mapping[str, Any], *path: str) -> Any:
    value: Any = tolerances
    for name in path:
        if not isinstance(value, Mapping) or name not in value:
            return 0.0
        value = value[name]
    return value


def _change_exceeds_tolerance(
    initial: Sequence[float], target: Sequence[float], tolerance: Any
) -> bool:
    if _is_finite_number(tolerance):
        limits = [float(tolerance)] * len(target)
    elif (
        isinstance(tolerance, Sequence)
        and not isinstance(tolerance, (str, bytes))
        and len(tolerance) == len(target)
        and all(_is_finite_number(item) for item in tolerance)
    ):
        limits = [float(item) for item in tolerance]
    else:
        limits = [0.0] * len(target)
    return any(
        abs(float(expected) - float(start)) > limit + 1e-12
        for start, expected, limit in zip(initial, target, limits, strict=True)
    )


def _lift_matches(
    *,
    initial: Sequence[float],
    target: Sequence[float],
    arguments: Mapping[str, Any],
) -> bool:
    lift = arguments.get("lift_height_m")
    return (
        _is_finite_number(lift)
        and len(initial) == 3
        and len(target) == 3
        and math.isclose(float(initial[0]), float(target[0]), abs_tol=1e-9)
        and math.isclose(float(initial[1]), float(target[1]), abs_tol=1e-9)
        and math.isclose(
            float(initial[2]) + float(lift),
            float(target[2]),
            abs_tol=1e-9,
        )
    )


def _joint_commands_from_binding(
    arguments: Mapping[str, Any], binding: Mapping[str, Any]
) -> dict[str, float]:
    """Resolve joint commands from an explicit manifest binding."""

    commands: dict[str, float] = {}
    mapping_argument = binding.get("joint_targets_argument")
    if isinstance(mapping_argument, str):
        raw_targets = arguments.get(mapping_argument)
        if isinstance(raw_targets, Mapping):
            for raw_key, value in raw_targets.items():
                key = str(raw_key)
                if key in LEROBOT_JOINT_POSITION_KEYS and _is_finite_number(value):
                    commands[key] = float(value)
        return commands
    scalar_arguments = binding.get("joint_target_arguments")
    if isinstance(scalar_arguments, Mapping):
        for raw_key, raw_argument in scalar_arguments.items():
            key = str(raw_key)
            argument_name = str(raw_argument)
            value = arguments.get(argument_name)
            if key in LEROBOT_JOINT_POSITION_KEYS and _is_finite_number(value):
                commands[key] = float(value)
    return commands


def _joint_mapping_contract_errors(
    *,
    arguments: Mapping[str, Any],
    target_joints: Mapping[str, Any],
    mapping_argument: str,
    prefix: str,
) -> list[str]:
    """Explain the exact G1 mapping equality contract to the suite designer."""

    raw_targets = arguments.get(mapping_argument)
    argument_path = f"call_arguments[{mapping_argument!r}]"
    target_path = "target_measurements['joint_positions']"
    example = (
        f"{argument_path}={{'shoulder_pan.pos': 15.0}} with "
        f"{target_path}={{'shoulder_pan.pos': 15.0}}, not "
        f"{argument_path}={{'shoulder_pan': 15.0}}"
    )
    if not isinstance(raw_targets, Mapping):
        return [
            f"{prefix}: G1 mapping binding requires {argument_path} to be a mapping "
            f"whose keys exactly equal {target_path} keys (including each literal "
            f"'.pos' suffix) and whose values equal the corresponding target values; "
            f"got {type(raw_targets).__name__}. Example: {example}"
        ]

    call_keys = {str(key) for key in raw_targets}
    target_keys = {str(key) for key in target_joints}
    missing = sorted(target_keys - call_keys)
    extra = sorted(call_keys - target_keys)
    errors: list[str] = []
    if missing or extra:
        errors.append(
            f"{prefix}: G1 mapping binding requires {argument_path} keys to exactly "
            f"equal {target_path} keys (including each literal '.pos' suffix), and "
            "values at identical keys must equal the corresponding target values; "
            f"missing call keys={missing}, extra call keys={extra}. Example: {example}"
        )

    mismatched_values: list[str] = []
    for key in sorted(call_keys & target_keys):
        call_value = raw_targets.get(key)
        target_value = target_joints.get(key)
        if (
            _is_finite_number(call_value)
            and _is_finite_number(target_value)
            and float(call_value) != float(target_value)
        ):
            mismatched_values.append(key)
    if mismatched_values:
        errors.append(
            f"{prefix}: G1 mapping binding requires values in {argument_path} to "
            f"exactly equal the corresponding values in {target_path}; mismatched "
            f"keys={mismatched_values}. Copy the target values without conversion."
        )
    return errors


def _bound_coordinates(
    arguments: Mapping[str, Any],
    argument_name: Any,
    component_arguments: Any,
    *,
    components: tuple[str, ...],
) -> tuple[float, ...] | None:
    """Resolve an explicit vector argument or component-to-argument map."""

    if isinstance(argument_name, str):
        return _numeric_vector(arguments.get(argument_name))
    if not isinstance(component_arguments, Mapping):
        return None
    values: list[float] = []
    for component in components:
        bound_name = component_arguments.get(component)
        if not isinstance(bound_name, str):
            return None
        value = arguments.get(bound_name)
        if not _is_finite_number(value):
            return None
        values.append(float(value))
    return tuple(values)


def _binding_components_match(
    expected: Sequence[float], supplied: Sequence[float], components: Any
) -> bool:
    if components == "xy":
        return _vectors_match(expected, supplied, planar=True)
    if components == "xyz":
        return _vectors_match(expected, supplied, planar=False)
    return False


def _argument_matches_annotation(value: Any, annotation: Any) -> bool:
    normalized = str(annotation).replace(" ", "").lower()
    if normalized in {"", "object", "any", "typing.any"}:
        return True
    if normalized in {"float", "builtins.float"}:
        return _is_finite_number(value)
    if normalized in {"int", "builtins.int"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if normalized in {"str", "builtins.str"}:
        return isinstance(value, str)
    if normalized in {"bool", "builtins.bool"}:
        return isinstance(value, bool)
    if normalized.startswith(("dict[", "mapping[", "typing.dict[", "typing.mapping[")):
        return isinstance(value, Mapping)
    if normalized.startswith(("list[", "sequence[", "typing.list[", "typing.sequence[")):
        return isinstance(value, list)
    return True


def _is_timeout_or_duration_parameter(name: object) -> bool:
    """Recognise public deadline parameters without conflating move geometry."""

    normalized = str(name).strip().lower()
    suffixes = ("timeout_s", "timeout_sec", "duration_s", "duration_sec")
    return any(
        normalized == suffix or normalized.endswith(f"_{suffix}")
        for suffix in suffixes
    )


def _numeric_leaves(value: Any) -> tuple[float, ...]:
    if _is_finite_number(value):
        return (float(value),)
    if isinstance(value, Mapping):
        return tuple(
            item
            for nested in value.values()
            for item in _numeric_leaves(nested)
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(item for nested in value for item in _numeric_leaves(nested))
    return ()


def _workspace_point_errors(
    point: Sequence[float], *, path: str
) -> list[str]:
    if len(point) != 3:
        return []
    labels = ("x", "y", "z")
    errors: list[str] = []
    for label, value, (lower, upper) in zip(
        labels, point, _P0_WORKSPACE_BOUNDS_M, strict=True
    ):
        if float(value) < lower or float(value) > upper:
            errors.append(
                f"{path}.{label}={float(value)!r} lies outside the trusted P0 "
                f"workspace [{lower}, {upper}] m"
            )
    return errors


def _physical_numeric_policy_errors(
    *,
    module: str,
    target: Mapping[str, Any],
    tolerances: Mapping[str, Any],
    timeout: Any,
    prefix: str,
) -> list[str]:
    """Apply calibrated P0 floors/envelopes before a case can blame code."""

    errors: list[str] = []
    measurement = _PHYSICAL_MEASUREMENT_BY_MODULE[module]
    leaves = _numeric_leaves(tolerances.get(measurement))
    minimum, maximum = _PHYSICAL_TOLERANCE_RANGE[module]
    for value in leaves:
        if value < minimum or value > maximum:
            errors.append(
                f"{prefix}.tolerances.{measurement} value {value!r} must be "
                f"within trusted {module} range [{minimum}, {maximum}]"
            )

    if _is_finite_number(timeout):
        timeout_value = float(timeout)
        floor = _PHYSICAL_TIMEOUT_FLOOR_S[module]
        if timeout_value < floor:
            errors.append(
                f"{prefix}.timeout_s must be at least {floor:g}s for physical {module}"
            )
    if module == "g1" and isinstance(target.get(measurement), Mapping):
        for name, value in target[measurement].items():
            if not _is_finite_number(value):
                continue
            numeric = float(value)
            if name == "gripper.pos":
                allowed = 0.0 <= numeric <= 100.0
            else:
                # A deliberately conservative common subset of the SO-101 arm
                # joint limits.  It leaves margin for physical validation.
                allowed = -90.0 <= numeric <= 90.0
            if not allowed:
                errors.append(
                    f"{prefix}.target_measurements.joint_positions.{name} "
                    "lies outside the trusted action envelope"
                )
    elif module == "g2":
        point = _numeric_vector(target.get(measurement))
        if point is not None:
            errors.extend(
                _workspace_point_errors(
                    point,
                    path=f"{prefix}.target_measurements.{measurement}",
                )
            )
    elif module == "g3" and isinstance(target.get(measurement), Mapping):
        for object_id, value in target[measurement].items():
            point = _numeric_vector(value)
            if point is not None:
                errors.extend(
                    _workspace_point_errors(
                        point,
                        path=(
                            f"{prefix}.target_measurements.{measurement}.{object_id}"
                        ),
                    )
                )
    return errors


def _effective_public_argument(
    api: Mapping[str, Any],
    arguments: Mapping[str, Any],
    argument_name: object,
) -> Any:
    if not isinstance(argument_name, str):
        return None
    if argument_name in arguments:
        return arguments[argument_name]
    signature = api.get("signature")
    parameters = signature.get("parameters", []) if isinstance(signature, Mapping) else []
    if isinstance(parameters, Sequence) and not isinstance(parameters, (str, bytes)):
        for parameter in parameters:
            if (
                isinstance(parameter, Mapping)
                and parameter.get("name") == argument_name
                and bool(parameter.get("has_default", False))
            ):
                return parameter.get("default")
    return None


def _body_extent_m(body: Mapping[str, Any], semantics: object) -> float | None:
    kind = body.get("kind")
    if kind == "cube":
        raw_size = body.get("size_m", [0.03, 0.03, 0.03])
        if _is_finite_number(raw_size):
            sizes = [float(raw_size)] * 3
        elif (
            isinstance(raw_size, Sequence)
            and not isinstance(raw_size, (str, bytes))
            and len(raw_size) == 3
            and all(_is_finite_number(value) for value in raw_size)
        ):
            sizes = [float(value) for value in raw_size]
        else:
            return None
        if semantics == "cube_edge_m":
            if max(sizes) - min(sizes) > 1e-9:
                return None
            return sizes[0]
        if semantics == "maximum_horizontal_extent_m":
            return max(sizes[0], sizes[1])
        return None
    if kind == "cylinder":
        radius = body.get("radius_m", 0.015)
        if semantics in {"cylinder_diameter_m", "maximum_horizontal_extent_m"} and _is_finite_number(radius):
            return 2.0 * float(radius)
    return None


def _body_height_m(body: Mapping[str, Any]) -> float | None:
    if body.get("kind") == "cube":
        raw_size = body.get("size_m", [0.03, 0.03, 0.03])
        if _is_finite_number(raw_size):
            return float(raw_size)
        if (
            isinstance(raw_size, Sequence)
            and not isinstance(raw_size, (str, bytes))
            and len(raw_size) == 3
            and _is_finite_number(raw_size[2])
        ):
            return float(raw_size[2])
    if body.get("kind") == "cylinder" and _is_finite_number(body.get("height_m", 0.04)):
        return float(body.get("height_m", 0.04))
    return None


def _physical_case_errors(
    case: Mapping[str, Any],
    prefix: str,
    api: Mapping[str, Any],
    stage1_capability: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    module = case.get("module")
    if module not in _PHYSICAL_MEASUREMENT_BY_MODULE:
        return [f"{prefix}.module does not have a physical validation policy"]
    target = case.get("target_measurements")
    tolerances = case.get("tolerances")
    initial_state = case.get("initial_state")
    arguments = case.get("call_arguments")
    forbidden = case.get("forbidden_conditions")
    if not isinstance(target, Mapping) or not isinstance(tolerances, Mapping):
        return errors

    required_measurement = _PHYSICAL_MEASUREMENT_BY_MODULE[str(module)]
    if required_measurement not in target:
        errors.append(
            f"{prefix}.target_measurements must include {required_measurement!r} for {module}"
        )
    if target.get("finite") is not True:
        errors.append(f"{prefix}.target_measurements.finite must be exactly true")
    expected_measurements = {required_measurement, "finite"}
    unexpected_measurements = set(target) - expected_measurements
    if unexpected_measurements:
        errors.append(
            f"{prefix}.target_measurements has unsupported physical extras: "
            f"{sorted(unexpected_measurements)}"
        )
    errors.extend(_measurement_shape_errors(target, f"{prefix}.target_measurements"))

    required_forbidden = _PHYSICAL_FORBIDDEN_BY_MODULE[str(module)]
    if not isinstance(forbidden, list) or not forbidden:
        errors.append(f"{prefix}.forbidden_conditions must be non-empty")
    else:
        absent = required_forbidden - set(forbidden)
        if absent:
            errors.append(
                f"{prefix}.forbidden_conditions lacks required {module} safety checks: "
                f"{sorted(absent)}"
            )

    if not isinstance(initial_state, Mapping):
        return errors
    state_errors, body_positions, qpos = _initial_state_errors(
        initial_state, f"{prefix}.initial_state"
    )
    errors.extend(state_errors)
    if not isinstance(arguments, Mapping):
        return errors
    binding = api.get("validation_binding")
    if not isinstance(binding, Mapping):
        errors.append(f"{prefix}: public API lacks a structured validation_binding")
        return errors

    errors.extend(
        _physical_numeric_policy_errors(
            module=str(module),
            target=target,
            tolerances=tolerances,
            timeout=case.get("timeout_s"),
            prefix=prefix,
        )
    )

    if module == "g1" and isinstance(target.get("joint_positions"), Mapping):
        target_joints = target["joint_positions"]
        if binding.get("effect") != "joint_targets":
            errors.append(f"{prefix}: G1 validation_binding effect must be 'joint_targets'")
        mapping_argument = binding.get("joint_targets_argument")
        if isinstance(mapping_argument, str):
            errors.extend(
                _joint_mapping_contract_errors(
                    arguments=arguments,
                    target_joints=target_joints,
                    mapping_argument=mapping_argument,
                    prefix=prefix,
                )
            )
        else:
            commands = _joint_commands_from_binding(arguments, binding)
            for key, expected in target_joints.items():
                if key not in commands:
                    errors.append(
                        f"{prefix}: joint target {key!r} is not bound to call_arguments"
                    )
                elif _is_finite_number(expected) and not math.isclose(
                    float(expected), commands[key], rel_tol=0.0, abs_tol=1e-9
                ):
                    errors.append(
                        f"{prefix}: measured target {key!r} does not equal its call argument"
                    )
        nontrivial = False
        for key, expected in target_joints.items():
            if not _is_finite_number(expected):
                continue
            motor = key.removesuffix(".pos")
            tolerance = _tolerance_at(tolerances, "joint_positions", key)
            if motor in ARM_MOTOR_NAMES and motor in qpos:
                initial_value = math.degrees(qpos[motor])
            else:
                initial_value = 0.0
            if _change_exceeds_tolerance(
                [initial_value], [float(expected)], tolerance
            ):
                nontrivial = True
        if target_joints and not nontrivial:
            errors.append(
                f"{prefix}: G1 target remains within tolerance of its initial/baseline state"
            )

    if module == "g2" and _numeric_vector(target.get("end_effector_position_m")) is not None:
        expected = _numeric_vector(target["end_effector_position_m"])
        assert expected is not None
        if binding.get("effect") != "cartesian_target":
            errors.append(
                f"{prefix}: G2 validation_binding effect must be 'cartesian_target'"
            )
        supplied = _bound_coordinates(
            arguments,
            binding.get("target_argument"),
            binding.get("target_arguments"),
            components=("x", "y", "z"),
        )
        if supplied is None or not _vectors_match(expected, supplied, planar=False):
            errors.append(
                f"{prefix}: end-effector target is not bound to the declared Cartesian argument"
            )
        tolerance = _tolerance_at(tolerances, "end_effector_position_m")
        if not _change_exceeds_tolerance([0.0, 0.0, 0.0], expected, tolerance):
            errors.append(f"{prefix}: G2 target is a degenerate all-zero/no-op target")

    if module == "g3" and isinstance(target.get("object_positions_m"), Mapping):
        targets = target["object_positions_m"]
        effect = binding.get("effect")
        body_specs = {
            str(body.get("id")): body
            for body in initial_state.get("bodies", [])
            if isinstance(body, Mapping) and isinstance(body.get("id"), str)
        }
        targeted_kinds = [
            body_specs[object_id].get("kind")
            for object_id in targets
            if object_id in body_specs
        ]
        implementation_family = stage1_capability.get("implementation_family")
        if implementation_family == "pick_place" and targeted_kinds != ["cylinder"]:
            errors.append(
                f"{prefix}: minimal P0 pick_place validation must use exactly one "
                "vertical cylinder so release/retreat is exercised"
            )
        elif implementation_family == "lift" and targeted_kinds != ["cube"]:
            errors.append(
                f"{prefix}: minimal P0 lift validation must use exactly one cube "
                "to complement the cylinder placement case"
            )
        elif implementation_family == "ordered_pick_place_sequence" and not (
            len(targeted_kinds) == 2
            and set(targeted_kinds) == {"cube", "cylinder"}
        ):
            errors.append(
                f"{prefix}: minimal P0 ordered sequence validation must target "
                "exactly one cube and one vertical cylinder"
            )
        if effect not in {
            "object_source_to_target",
            "object_source_plus_height_delta",
            "object_move_sequence",
        }:
            errors.append(f"{prefix}: G3 validation_binding effect is unsupported")
            return errors
        if effect in {"object_source_to_target", "object_source_plus_height_delta"} and len(targets) != 1:
            errors.append(
                f"{prefix}: single-object G3 binding requires exactly one targeted object"
            )
        moves: list[Mapping[str, Any]] = []
        source_field = binding.get("source_field")
        target_field = binding.get("target_field")
        object_extent_field = binding.get("object_extent_field")
        if effect == "object_move_sequence":
            signature = api.get("signature")
            parameters = (
                signature.get("parameters", [])
                if isinstance(signature, Mapping)
                else []
            )
            parameter_names = {
                str(parameter.get("name"))
                for parameter in parameters
                if isinstance(parameter, Mapping)
                and isinstance(parameter.get("name"), str)
            } if isinstance(parameters, Sequence) and not isinstance(
                parameters, (str, bytes)
            ) else set()
            field_parameter_collisions = sorted(
                {
                    name
                    for name in (
                        source_field,
                        target_field,
                        object_extent_field,
                    )
                    if isinstance(name, str) and name in parameter_names
                }
            )
            if field_parameter_collisions:
                errors.append(
                    f"{prefix}: move-sequence source/target/extent fields are fixed "
                    "literal move-item keys, not public selector arguments; "
                    f"conflicts={field_parameter_collisions!r}"
                )
                return errors
            move_item_fields = (
                source_field,
                target_field,
                object_extent_field,
            )
            if (
                not all(isinstance(name, str) for name in move_item_fields)
                or len(set(move_item_fields)) != len(move_item_fields)
            ):
                errors.append(
                    f"{prefix}: move-sequence source_field, target_field, and "
                    "object_extent_field must be distinct literal keys"
                )
                return errors
            raw_moves = arguments.get(binding.get("moves_argument"))
            if isinstance(raw_moves, list) and all(isinstance(item, Mapping) for item in raw_moves):
                moves = list(raw_moves)
            else:
                errors.append(
                    f"{prefix}: declared move-sequence argument must be an array of objects"
                )
            if len(moves) != len(targets):
                errors.append(
                    f"{prefix}: move sequence must contain exactly one move per targeted object"
                )
        used_moves: set[int] = set()
        for object_id, raw_expected in targets.items():
            expected = _numeric_vector(raw_expected)
            if object_id not in body_positions:
                errors.append(
                    f"{prefix}: target object {object_id!r} is absent from initial_state.bodies"
                )
                continue
            if expected is None:
                continue
            initial = body_positions[object_id]
            body_spec = body_specs.get(str(object_id), {})
            extent_argument = binding.get("object_extent_argument")
            if extent_argument is not None:
                supplied_extent = _effective_public_argument(
                    api, arguments, extent_argument
                )
                expected_extent = _body_extent_m(
                    body_spec, binding.get("object_extent_semantics")
                )
                if (
                    expected_extent is None
                    or not _is_finite_number(supplied_extent)
                    or not math.isclose(
                        float(supplied_extent),
                        expected_extent,
                        rel_tol=0.0,
                        abs_tol=1e-9,
                    )
                ):
                    errors.append(
                        f"{prefix}: bound object extent for {object_id!r} does not "
                        "match the authored initial-state geometry"
                    )

            contact_argument = binding.get("contact_height_argument")
            if contact_argument is not None:
                supplied_contact_height = _effective_public_argument(
                    api, arguments, contact_argument
                )
                body_height = _body_height_m(body_spec)
                contact_reference = binding.get("contact_height_reference")
                world_contact_z: float | None = None
                if _is_finite_number(supplied_contact_height):
                    value = float(supplied_contact_height)
                    if contact_reference == "absolute_base_z_m":
                        world_contact_z = value
                    elif contact_reference == "height_above_table_m":
                        world_contact_z = 0.020 + value
                    elif contact_reference == "offset_from_object_center_m":
                        world_contact_z = initial[2] + value
                if body_height is None or world_contact_z is None:
                    errors.append(
                        f"{prefix}: bound contact height for {object_id!r} is not "
                        "finite or lacks authored vertical geometry"
                    )
                else:
                    bottom = initial[2] - body_height / 2.0
                    top = initial[2] + body_height / 2.0
                    if not bottom + 1e-4 <= world_contact_z <= top - 1e-4:
                        errors.append(
                            f"{prefix}: bound contact height for {object_id!r} lies "
                            "outside the object's vertical contact span"
                        )
            source_linked = False
            goal_linked = False
            if effect == "object_source_to_target":
                supplied_source = _bound_coordinates(
                    arguments,
                    binding.get("source_argument"),
                    binding.get("source_arguments"),
                    components=("x", "y", "z"),
                )
                target_components = (
                    ("x", "y")
                    if binding.get("target_components") == "xy"
                    else ("x", "y", "z")
                )
                supplied_target = _bound_coordinates(
                    arguments,
                    binding.get("target_argument"),
                    binding.get("target_arguments"),
                    components=target_components,
                )
                source_linked = supplied_source is not None and _vectors_match(
                    initial, supplied_source, planar=False
                )
                goal_linked = supplied_target is not None and _binding_components_match(
                    expected,
                    supplied_target,
                    binding.get("target_components"),
                )
            elif effect == "object_source_plus_height_delta":
                supplied_source = _bound_coordinates(
                    arguments,
                    binding.get("source_argument"),
                    binding.get("source_arguments"),
                    components=("x", "y", "z"),
                )
                delta = arguments.get(binding.get("height_delta_argument"))
                source_linked = supplied_source is not None and _vectors_match(
                    initial, supplied_source, planar=False
                )
                goal_linked = (
                    _is_finite_number(delta)
                    and float(delta) > 0.0
                    and len(expected) == 3
                    and math.isclose(initial[0], expected[0], abs_tol=1e-9)
                    and math.isclose(initial[1], expected[1], abs_tol=1e-9)
                    and math.isclose(
                        initial[2] + float(delta),
                        expected[2],
                        abs_tol=1e-9,
                    )
                )
            else:
                for move_index, move in enumerate(moves):
                    if move_index in used_moves:
                        continue
                    supplied_source = _numeric_vector(move.get(source_field))
                    supplied_target = _numeric_vector(move.get(target_field))
                    if (
                        supplied_source is not None
                        and supplied_target is not None
                        and _vectors_match(initial, supplied_source, planar=False)
                        and _binding_components_match(
                            expected,
                            supplied_target,
                            binding.get("target_components"),
                        )
                    ):
                        source_linked = True
                        goal_linked = True
                        used_moves.add(move_index)
                        supplied_extent = move.get(object_extent_field)
                        expected_extent = _body_extent_m(
                            body_spec,
                            binding.get("object_extent_semantics"),
                        )
                        if (
                            expected_extent is None
                            or not _is_finite_number(supplied_extent)
                            or float(supplied_extent) <= 0.0
                            or not math.isclose(
                                float(supplied_extent),
                                expected_extent,
                                rel_tol=0.0,
                                abs_tol=1e-9,
                            )
                        ):
                            errors.append(
                                f"{prefix}: bound move extent for {object_id!r} "
                                "does not match the authored initial-state geometry"
                            )
                        break
            if not source_linked:
                errors.append(
                    f"{prefix}: object source {object_id!r} is not bound to its authored initial state"
                )
            if not goal_linked:
                errors.append(
                    f"{prefix}: object target {object_id!r} is not bound by validation_binding"
                )
            tolerance = _tolerance_at(tolerances, "object_positions_m", str(object_id))
            if not _change_exceeds_tolerance(initial, expected, tolerance):
                errors.append(
                    f"{prefix}: object target {object_id!r} is within tolerance of its initial state"
                )
    return errors


def _public_api_sha256(package_manifest: Mapping[str, Any]) -> str:
    public_api = [
        {
            "capability_id": item.get("capability_id"),
            "granularity": item.get("granularity"),
            "module": item.get("module"),
            "function_name": item.get("function_name"),
            "signature": item.get("signature"),
            "validation_binding": item.get("validation_binding"),
            "result_contract": item.get("result_contract"),
        }
        for item in package_manifest.get("capabilities", [])
        if isinstance(item, Mapping)
    ]
    return sha256_json(public_api)


def validate_suite(
    suite: Mapping[str, Any],
    stage1: Mapping[str, Any],
    package_manifest: Mapping[str, Any],
    reference_library: Mapping[str, Any] | None = None,
    case_schema: Mapping[str, Any] | None = None,
    *,
    enforce_physical_policy: bool = True,
) -> SuiteCheck:
    """Deterministically validate structure, coverage, and public calls."""

    errors: list[str] = []
    extra_suite_fields = set(suite) - {"schema_version", "cases"}
    if extra_suite_fields:
        errors.append(
            f"suite has undeclared top-level fields: {sorted(extra_suite_fields)}"
        )
    expected = _stage1_index(stage1)
    manifest = _manifest_index(package_manifest)
    physical_policy = bool(
        enforce_physical_policy
        and isinstance(reference_library, Mapping)
        and reference_library.get("schema_version")
        == PHYSICAL_REFERENCE_SCHEMA_VERSION
    )
    if suite.get("schema_version") != SUITE_SCHEMA_VERSION:
        errors.append(f"schema_version must be {SUITE_SCHEMA_VERSION!r}")
    cases = suite.get("cases")
    if not isinstance(cases, list):
        return SuiteCheck(False, tuple(errors + ["cases must be an array"]), ())
    physical_cases: list[Any] = list(cases)
    if physical_policy:
        physical_cases, catalog_errors = _catalog_physical_projection(
            cases,
            reference_library,
        )
        errors.extend(catalog_errors)
    seen_case_ids: set[str] = set()
    covered: set[str] = set()
    coverage_counts = {capability_id: 0 for capability_id in expected}
    permitted_measurements = set(
        reference_library.get("permitted_measurements", [])
        if isinstance(reference_library, Mapping)
        else []
    )
    permitted_forbidden = set(
        reference_library.get("forbidden_conditions", [])
        if isinstance(reference_library, Mapping)
        else []
    )
    for number, case in enumerate(cases):
        prefix = f"cases[{number}]"
        if not isinstance(case, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        missing = CASE_FIELDS - set(case)
        extra = set(case) - CASE_FIELDS
        if missing:
            errors.append(f"{prefix} missing fields: {sorted(missing)}")
        if extra:
            errors.append(f"{prefix} has undeclared fields: {sorted(extra)}")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{prefix}.case_id must be a non-empty string")
        elif _CASE_ID_PATTERN.fullmatch(case_id) is None:
            errors.append(
                f"{prefix}.case_id must be filename-safe ASCII (max 160 characters)"
            )
        elif case_id in seen_case_ids:
            errors.append(f"duplicate case_id {case_id!r}")
        else:
            seen_case_ids.add(case_id)
        capability_id = case.get("capability_id")
        if capability_id not in expected:
            errors.append(f"{prefix}.capability_id is not in frozen Stage 1: {capability_id!r}")
            continue
        covered.add(str(capability_id))
        coverage_counts[str(capability_id)] += 1
        expected_item = expected[str(capability_id)]
        for field in ("module", "function_name"):
            if case.get(field) != expected_item[field]:
                errors.append(
                    f"{prefix}.{field}={case.get(field)!r}, expected {expected_item[field]!r}"
                )
        api = manifest.get(str(capability_id))
        if api is None:
            errors.append(f"{prefix}: capability absent from package manifest")
        else:
            binding = api.get("validation_binding")
            if (
                not isinstance(binding, Mapping)
                or binding.get("effect") != expected_item["validation_effect"]
            ):
                errors.append(
                    f"{prefix}: public validation_binding does not match frozen Stage 1 effect"
                )
            allowed, required = _signature_parameters(api.get("signature"))
            arguments = case.get("call_arguments")
            if not isinstance(arguments, Mapping):
                errors.append(f"{prefix}.call_arguments must be an object")
            else:
                argument_names = set(arguments)
                if "runtime" in argument_names:
                    errors.append(f"{prefix}.call_arguments must not expose injected runtime")
                unknown = argument_names - allowed
                absent = required - argument_names
                if unknown:
                    errors.append(f"{prefix} has unknown call arguments: {sorted(unknown)}")
                if absent:
                    errors.append(f"{prefix} lacks required call arguments: {sorted(absent)}")
                parameters = api.get("signature", {}).get("parameters", [])
                parameter_specs = {
                    str(item.get("name")): item
                    for item in parameters
                    if isinstance(item, Mapping) and isinstance(item.get("name"), str)
                } if isinstance(parameters, Sequence) and not isinstance(parameters, (str, bytes)) else {}
                for name, value in arguments.items():
                    spec = parameter_specs.get(str(name))
                    if spec is not None and not _argument_matches_annotation(
                        value, spec.get("annotation")
                    ):
                        errors.append(
                            f"{prefix}.call_arguments.{name} does not match public annotation "
                            f"{spec.get('annotation')!r}"
                        )
                    if (
                        physical_policy
                        and spec is not None
                        and spec.get("has_default") is True
                        and _is_timeout_or_duration_parameter(name)
                    ):
                        errors.append(
                            f"{prefix}.call_arguments.{name} must be omitted in a nominal "
                            "physical case so the frozen public default is exercised; "
                            "cases[].timeout_s is an independent host-monotonic harness "
                            "deadline"
                        )
        if not isinstance(case.get("initial_state"), Mapping):
            errors.append(f"{prefix}.initial_state must be an object")
        for field in ("target_measurements", "tolerances"):
            value = case.get(field)
            if not isinstance(value, Mapping) or not value:
                errors.append(f"{prefix}.{field} must be a non-empty object")
            else:
                errors.extend(_finite_tree_errors(value, f"{prefix}.{field}"))
        target_measurements = case.get("target_measurements")
        tolerances = case.get("tolerances")
        if isinstance(target_measurements, Mapping) and isinstance(tolerances, Mapping):
            errors.extend(
                _tolerance_shape_errors(
                    target_measurements,
                    tolerances,
                    f"{prefix}.tolerances",
                )
            )
            if permitted_measurements:
                unknown_measurements = set(target_measurements) - permitted_measurements
                if unknown_measurements:
                    errors.append(
                        f"{prefix}.target_measurements uses unpermitted names: "
                        f"{sorted(unknown_measurements)}"
                    )
            joint_positions = target_measurements.get("joint_positions")
            if isinstance(joint_positions, Mapping):
                invalid_joint_keys = set(joint_positions) - LEROBOT_JOINT_POSITION_KEYS
                if invalid_joint_keys:
                    errors.append(
                        f"{prefix}.target_measurements.joint_positions uses invalid "
                        f"LeRobot keys: {sorted(invalid_joint_keys)}"
                    )
        if case_schema is not None:
            errors.extend(
                f"{prefix}{issue.path.removeprefix('$')}: {issue.message}"
                for issue in validate_json_schema(
                    case,
                    case_schema,
                    instance_path="$",
                )
            )
        for field in ("initial_state", "call_arguments"):
            errors.extend(_finite_tree_errors(case.get(field), f"{prefix}.{field}"))
        timeout = case.get("timeout_s")
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(float(timeout))
            or timeout <= 0
            or timeout > 30.0
        ):
            errors.append(f"{prefix}.timeout_s must be finite and in (0, 30]")
        if case.get("test_entrypoint") != "soarm_demo.direct_validation:execute_case":
            errors.append(f"{prefix}.test_entrypoint must use the trusted direct harness")
        if not isinstance(case.get("forbidden_conditions"), list):
            errors.append(f"{prefix}.forbidden_conditions must be an array")
        elif permitted_forbidden:
            unknown_forbidden = set(case["forbidden_conditions"]) - permitted_forbidden
            if unknown_forbidden:
                errors.append(
                    f"{prefix}.forbidden_conditions uses unpermitted names: "
                    f"{sorted(unknown_forbidden)}"
                )
        if not isinstance(case.get("reference_provenance"), list):
            errors.append(f"{prefix}.reference_provenance must be an array")
        if physical_policy:
            physical_case = (
                physical_cases[number]
                if number < len(physical_cases)
                and isinstance(physical_cases[number], Mapping)
                else case
            )
            errors.extend(
                _physical_case_errors(
                    physical_case,
                    prefix,
                    api if isinstance(api, Mapping) else {},
                    expected_item,
                )
            )
    missing_coverage = set(expected) - covered
    if missing_coverage:
        errors.append(f"suite does not cover Stage 1 capabilities: {sorted(missing_coverage)}")
    duplicate_coverage = {
        capability_id: count
        for capability_id, count in coverage_counts.items()
        if count > 1
    }
    if duplicate_coverage:
        errors.append(
            "minimal P0 requires exactly one case per capability; duplicate counts: "
            f"{duplicate_coverage}"
        )
    if len(cases) != len(expected):
        errors.append(
            f"minimal P0 requires exactly {len(expected)} cases, got {len(cases)}"
        )
    return SuiteCheck(not errors, tuple(errors), tuple(sorted(covered)))


class ValidationSuiteGenerator:
    """Run generate/review/final-review calls, then freeze trusted artifacts."""

    def __init__(
        self,
        *,
        client: ModelClient,
        prompts: Sequence[str],
        output_dir: str | Path,
        trace_path: str | Path,
        call_limit: int = 3,
    ) -> None:
        if len(prompts) != 3:
            raise ValueError("validation suite generation requires exactly three prompts")
        if call_limit != 3:
            raise ValueError("P0 validation suite call_limit is fixed at exactly three")
        self.client = client
        self.prompts = tuple(prompts)
        self.output_dir = Path(output_dir)
        self.trace = JsonlTrace(trace_path)
        self.budget = BudgetCounter("validation_suite", call_limit)

    def generate(
        self,
        *,
        stage1: Mapping[str, Any],
        package_manifest: Mapping[str, Any],
        reference_library: Mapping[str, Any],
        case_schema: Mapping[str, Any],
        candidate_preflight: Callable[[Mapping[str, Any]], Sequence[str]] | None = None,
        model_configuration_sha256: str | None = None,
    ) -> FrozenValidationSuite:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Scripted mode is an orchestration fixture, not physical evidence. Its
        # legacy suite remains executable so offline end-to-end tests can prove
        # control flow, while every real provider run using the physical
        # reference is held to the stricter SO-ARM101 policy below.
        physical_policy_enforced = bool(
            reference_library.get("schema_version")
            == PHYSICAL_REFERENCE_SCHEMA_VERSION
            and not self.client.scripted
        )
        immutable_context = {
            "frozen_stage1": stage1,
            "frozen_public_api": package_manifest,
            "validation_reference": reference_library,
            "case_schema": case_schema,
            "suite_schema_version": SUITE_SCHEMA_VERSION,
            "timeout_contract": TIMEOUT_CONTRACT,
            "trusted_test_entrypoint": "soarm_demo.direct_validation:execute_case",
            "physical_policy_enforced": physical_policy_enforced,
        }
        candidate: dict[str, Any] | None = None
        check = SuiteCheck(False, ("no candidate yet",), ())
        messages = [
            ModelMessage(
                "system",
                "You are the private validation-suite designer. Return only one JSON object. "
                "Never infer or quote generated implementation source.",
            )
        ]
        for index, prompt in enumerate(self.prompts, start=1):
            request = {
                "purpose": ("generate", "review_and_rewrite", "final_review_and_rewrite")[index - 1],
                "instructions": prompt,
                "immutable_context": immutable_context,
                "previous_candidate": candidate,
                "deterministic_check": check.to_dict(),
            }
            messages.append(ModelMessage("user", json.dumps(request, ensure_ascii=False, sort_keys=True)))
            remaining = self.budget.consume()
            response = self.client.complete(messages)
            parse_error: str | None = None
            try:
                parsed = parse_json_document(response.text)
            except ValidationSuiteError as exc:
                parse_error = str(exc)
                check = SuiteCheck(False, (parse_error,), ())
                atomic_write_json(
                    self.output_dir / f"call_{index:02d}_parse_error.json",
                    {"error": parse_error},
                )
            else:
                candidate = parsed
                check = validate_suite(
                    candidate,
                    stage1,
                    package_manifest,
                    reference_library=reference_library,
                    case_schema=case_schema,
                    enforce_physical_policy=physical_policy_enforced,
                )
                if check.ok and candidate_preflight is not None:
                    preflight_errors = tuple(candidate_preflight(candidate))
                    if preflight_errors:
                        check = SuiteCheck(
                            False,
                            preflight_errors,
                            check.covered_capabilities,
                        )
                atomic_write_json(
                    self.output_dir / f"call_{index:02d}_candidate.json",
                    candidate,
                )
            self.trace.append(
                {
                    "event": "validation_suite_model_call",
                    "purpose": request["purpose"],
                    "model": self.client.model,
                    "call": index,
                    "remaining_calls": remaining,
                    "candidate_sha256": None if candidate is None else sha256_json(candidate),
                    "parse_error": parse_error,
                    "check": check.to_dict(),
                    "completion": {
                        "stop_reason": response.stop_reason,
                        "output_truncated": response.output_truncated,
                        "requested_max_tokens": response.requested_max_tokens,
                    },
                    "usage": {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "cost": response.usage.cost,
                        "remaining_budget": response.usage.remaining_budget,
                    },
                }
            )
            messages.append(ModelMessage("assistant", response.text))
        if candidate is None or not check.ok:
            atomic_write_json(self.output_dir / "final_check.json", check.to_dict())
            raise ValidationSuiteError(
                "validation suite remained invalid after exactly three calls: "
                + "; ".join(check.errors)
            )
        frozen = json.loads(json.dumps(candidate, ensure_ascii=False, sort_keys=True))
        suite_path = self.output_dir / "suite.json"
        atomic_write_json(suite_path, frozen)
        generated_test_path = self.output_dir / "test_generated_suite.py"
        generated_test_path.write_text(_render_generated_test(frozen), encoding="utf-8")
        digest = sha256_json(frozen)
        atomic_write_json(
            self.output_dir / "freeze.json",
            {
                "schema_version": SUITE_FREEZE_SCHEMA_VERSION,
                "suite_sha256": digest,
                "model_calls": self.budget.used,
                "covered_capabilities": list(check.covered_capabilities),
                "reference_library_sha256": sha256_json(reference_library),
                "case_schema_sha256": sha256_json(case_schema),
                "prompts_sha256": sha256_json(list(self.prompts)),
                "model": self.client.model,
                "model_configuration_sha256": (
                    model_configuration_sha256
                    or sha256_json({"model": self.client.model})
                ),
                "physical_policy_enforced": physical_policy_enforced,
                "executable_preflight_enforced": candidate_preflight is not None,
                "timeout_contract": TIMEOUT_CONTRACT,
                "stage1_sha256": sha256_json(stage1),
                "public_api_sha256": _public_api_sha256(package_manifest),
            },
        )
        return FrozenValidationSuite(frozen, digest, self.budget.used, suite_path, generated_test_path)


def _render_generated_test(suite: Mapping[str, Any]) -> str:
    payload = json.dumps(suite, ensure_ascii=False, sort_keys=True, indent=2)
    return (
        '"""Frozen generated direct-function cases. Do not edit during repair."""\n\n'
        "import json\n\n"
        f"SUITE = json.loads({payload!r})\n\n"
        "def iter_cases():\n"
        "    yield from SUITE['cases']\n"
    )


def signature_to_manifest(function: Any) -> dict[str, Any]:
    """Utility used by tests/fixture builders to serialize a public signature."""

    signature = inspect.signature(function)
    parameters = []
    for parameter in signature.parameters.values():
        parameters.append(
            {
                "name": parameter.name,
                "kind": parameter.kind.name,
                "annotation": inspect.formatannotation(parameter.annotation),
                "has_default": parameter.default is not inspect.Parameter.empty,
                "default": None
                if parameter.default is inspect.Parameter.empty
                else parameter.default,
            }
        )
    return {
        "parameters": parameters,
        "return_annotation": inspect.formatannotation(signature.return_annotation),
    }
