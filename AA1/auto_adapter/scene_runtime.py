# SPDX-License-Identifier: Apache-2.0
"""Prepared DESIGN scene assembly and small MuJoCo case probes for AA1."""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import mujoco
import numpy as np
import yaml

from .design_measurements import (
    MeasurementError,
    capture_sample,
    validate_measurement,
)


_SAFE_PATH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SHAPES = {"box", "sphere", "cylinder"}
_MOTIONS = {"fixed", "free"}
_GEOM_TYPES = {
    "box": mujoco.mjtGeom.mjGEOM_BOX,
    "sphere": mujoco.mjtGeom.mjGEOM_SPHERE,
    "cylinder": mujoco.mjtGeom.mjGEOM_CYLINDER,
}


class SceneCaseError(ValueError):
    """Raised when a prepared scene/case description is not executable."""


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float, np.integer, np.floating))
        and not isinstance(value, (bool, np.bool_))
        and math.isfinite(float(value))
    )


def _number(value: Any, *, where: str, positive: bool = False, nonnegative: bool = False) -> float:
    if not _finite_number(value):
        raise SceneCaseError(f"{where} must be a finite number")
    result = float(value)
    if positive and result <= 0.0:
        raise SceneCaseError(f"{where} must be positive")
    if nonnegative and result < 0.0:
        raise SceneCaseError(f"{where} must be non-negative")
    return result


def _array(value: Any, *, length: int, where: str) -> list[float]:
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) != length:
        raise SceneCaseError(f"{where} must be a numeric array of length {length}")
    result = [
        _number(item, where=f"{where}[{index}]")
        for index, item in enumerate(value)
    ]
    return result


def _object(value: Any, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SceneCaseError(f"{where} must be an object")
    return value


def _nonempty_text(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SceneCaseError(f"{where} must be non-empty text")
    return value.strip()


def _safe_path_id(value: Any, *, where: str) -> str:
    text = _nonempty_text(value, where=where)
    if not _SAFE_PATH_ID.fullmatch(text) or text in {".", ".."}:
        raise SceneCaseError(
            f"{where} must be a safe path identifier containing only letters, "
            "digits, _, -, and ."
        )
    return text


def _finite_vector(value: Any, *, length: int, where: str) -> list[float]:
    return _array(value, length=length, where=where)


def _request_field(request: Any, field: Any, *, where: str) -> Any:
    if not isinstance(field, str) or not field.strip():
        raise SceneCaseError(f"{where} must be a non-empty request field")
    value: Any = request
    for component in field.split("."):
        if not component or not isinstance(value, Mapping) or component not in value:
            raise SceneCaseError(f"{where} {field!r} is missing from request")
        value = value[component]
    return value


def _validate_schema_value(value: Any, schema: Any, *, where: str) -> None:
    """Validate only the closed request-schema features used by AA1 designs."""

    schema_map = _object(schema, where=f"{where} schema")
    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "enum",
        "minLength",
        "maxLength",
        "unit",
        "frame",
        "evidence_refs",
        # JSON Schema annotations are descriptive metadata; validation below
        # deliberately ignores their values while enforcing the constraints.
        "description",
        "title",
        "default",
        "examples",
    }
    unknown = set(schema_map) - allowed
    if unknown:
        raise SceneCaseError(f"{where} schema has unsupported fields {sorted(unknown)}")
    schema_type = schema_map.get("type")
    if schema_type == "object":
        if not isinstance(value, Mapping):
            raise SceneCaseError(f"{where} must be an object")
        properties = schema_map.get("properties", {})
        if not isinstance(properties, Mapping):
            raise SceneCaseError(f"{where} schema.properties must be an object")
        required = schema_map.get("required", [])
        if not isinstance(required, list) or any(
            not isinstance(item, str) for item in required
        ):
            raise SceneCaseError(f"{where} schema.required must be a text array")
        missing = [item for item in required if item not in value]
        if missing:
            raise SceneCaseError(f"{where} is missing required fields {missing}")
        if schema_map.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise SceneCaseError(f"{where} has unknown fields {sorted(extras)}")
        for key, child in value.items():
            if key in properties:
                _validate_schema_value(child, properties[key], where=f"{where}.{key}")
        return
    if schema_type == "array":
        if not isinstance(value, (list, tuple, np.ndarray)):
            raise SceneCaseError(f"{where} must be an array")
        length = len(value)
        if "minItems" in schema_map and length < int(schema_map["minItems"]):
            raise SceneCaseError(f"{where} has fewer than minItems")
        if "maxItems" in schema_map and length > int(schema_map["maxItems"]):
            raise SceneCaseError(f"{where} has more than maxItems")
        if "items" in schema_map:
            for index, item in enumerate(value):
                _validate_schema_value(item, schema_map["items"], where=f"{where}[{index}]")
    elif schema_type in {"number", "integer"}:
        if not _finite_number(value):
            raise SceneCaseError(f"{where} must be a finite {schema_type}")
        if schema_type == "integer" and float(value) != math.floor(float(value)):
            raise SceneCaseError(f"{where} must be an integer")
        numeric = float(value)
        for field, predicate in (
            ("minimum", lambda bound: numeric < bound),
            ("maximum", lambda bound: numeric > bound),
            ("exclusiveMinimum", lambda bound: numeric <= bound),
            ("exclusiveMaximum", lambda bound: numeric >= bound),
        ):
            if field in schema_map:
                bound = _number(schema_map[field], where=f"{where} schema.{field}")
                if predicate(bound):
                    raise SceneCaseError(f"{where} violates {field}")
    elif schema_type == "string":
        if not isinstance(value, str):
            raise SceneCaseError(f"{where} must be text")
        if "minLength" in schema_map and len(value) < int(schema_map["minLength"]):
            raise SceneCaseError(f"{where} is shorter than minLength")
        if "maxLength" in schema_map and len(value) > int(schema_map["maxLength"]):
            raise SceneCaseError(f"{where} is longer than maxLength")
    elif schema_type == "boolean":
        if not isinstance(value, bool):
            raise SceneCaseError(f"{where} must be boolean")
    elif schema_type == "null":
        if value is not None:
            raise SceneCaseError(f"{where} must be null")
    elif schema_type is not None:
        raise SceneCaseError(f"{where} schema.type {schema_type!r} is unsupported")
    if "enum" in schema_map and value not in schema_map["enum"]:
        raise SceneCaseError(f"{where} is outside the declared enum")


def _validate_scene(scene_id: str, scene: Any) -> None:
    scene_map = _object(scene, where=f"scenes.{scene_id}")
    objects = scene_map.get("objects")
    if not isinstance(objects, list):
        raise SceneCaseError(f"scenes.{scene_id}.objects must be an array")
    seen: set[str] = set()
    for index, raw_object in enumerate(objects):
        where = f"scenes.{scene_id}.objects[{index}]"
        item = _object(raw_object, where=where)
        name = _nonempty_text(item.get("name"), where=f"{where}.name")
        if name in seen:
            raise SceneCaseError(f"{where}.name {name!r} is duplicated")
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise SceneCaseError(f"{where}.name cannot contain path separators")
        seen.add(name)
        shape = item.get("shape")
        if shape not in _SHAPES:
            raise SceneCaseError(f"{where}.shape must be box, sphere, or cylinder")
        motion = item.get("motion")
        if motion not in _MOTIONS:
            raise SceneCaseError(f"{where}.motion must be fixed or free")
        size_lengths = {"box": 3, "sphere": 1, "cylinder": 2}
        size = _finite_vector(
            item.get("size_m"),
            length=size_lengths[shape],
            where=f"{where}.size_m",
        )
        if any(value <= 0 for value in size):
            raise SceneCaseError(f"{where}.size_m must contain positive values")
        position = _finite_vector(
            item.get("position_m"), length=3, where=f"{where}.position_m"
        )
        quaternion = _finite_vector(
            item.get("quaternion_wxyz"),
            length=4,
            where=f"{where}.quaternion_wxyz",
        )
        if np.linalg.norm(quaternion) <= 1e-12:
            raise SceneCaseError(f"{where}.quaternion_wxyz must be non-zero")
        if "friction" in item:
            friction = _finite_vector(
                item["friction"], length=3, where=f"{where}.friction"
            )
            if any(value < 0 for value in friction):
                raise SceneCaseError(f"{where}.friction must be non-negative")
        if motion == "free":
            _number(item.get("mass_kg"), where=f"{where}.mass_kg", positive=True)
        elif "mass_kg" in item:
            _number(item["mass_kg"], where=f"{where}.mass_kg", positive=True)


def _validate_initial_state(initial_state: Any, *, where: str) -> None:
    state = _object(initial_state, where=where)
    robot = state.get("robot", {})
    robot_map = _object(robot, where=f"{where}.robot")
    if "keyframe" in robot_map and not (
        isinstance(robot_map["keyframe"], str)
        or (isinstance(robot_map["keyframe"], int) and not isinstance(robot_map["keyframe"], bool))
    ):
        raise SceneCaseError(f"{where}.robot.keyframe must be a name or integer")
    qpos_by_joint = robot_map.get("qpos_by_joint", {})
    if not isinstance(qpos_by_joint, Mapping):
        raise SceneCaseError(f"{where}.robot.qpos_by_joint must be an object")
    for name, value in qpos_by_joint.items():
        _nonempty_text(name, where=f"{where}.robot.qpos_by_joint key")
        _number(value, where=f"{where}.robot.qpos_by_joint.{name}")
    free_bodies = state.get("free_bodies", {})
    if not isinstance(free_bodies, Mapping):
        raise SceneCaseError(f"{where}.free_bodies must be an object")
    for name, raw_body in free_bodies.items():
        body_where = f"{where}.free_bodies.{name}"
        _nonempty_text(name, where=f"{where}.free_bodies key")
        body = _object(raw_body, where=body_where)
        _finite_vector(body.get("position_m"), length=3, where=f"{body_where}.position_m")
        quaternion = _finite_vector(
            body.get("quaternion_wxyz"),
            length=4,
            where=f"{body_where}.quaternion_wxyz",
        )
        if np.linalg.norm(quaternion) <= 1e-12:
            raise SceneCaseError(f"{body_where}.quaternion_wxyz must be non-zero")
        if "linear_velocity_m_s" in body:
            _finite_vector(
                body["linear_velocity_m_s"],
                length=3,
                where=f"{body_where}.linear_velocity_m_s",
            )
        if "angular_velocity_rad_s" in body:
            _finite_vector(
                body["angular_velocity_rad_s"],
                length=3,
                where=f"{body_where}.angular_velocity_rad_s",
            )
    ctrl_by_actuator = state.get("ctrl_by_actuator", {})
    if not isinstance(ctrl_by_actuator, Mapping):
        raise SceneCaseError(f"{where}.ctrl_by_actuator must be an object")
    for name, value in ctrl_by_actuator.items():
        _nonempty_text(name, where=f"{where}.ctrl_by_actuator key")
        _number(value, where=f"{where}.ctrl_by_actuator.{name}")
    _number(state.get("settle_s", 0.0), where=f"{where}.settle_s", nonnegative=True)


def _capability_map(design: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise SceneCaseError("design.capabilities must be a non-empty array")
    result: dict[str, Mapping[str, Any]] = {}
    for index, raw_capability in enumerate(capabilities):
        capability = _object(raw_capability, where=f"design.capabilities[{index}]")
        capability_id = _nonempty_text(
            capability.get("capability_id"),
            where=f"design.capabilities[{index}].capability_id",
        )
        if capability_id in result:
            raise SceneCaseError(f"duplicate capability_id {capability_id!r}")
        criteria = capability.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise SceneCaseError(f"design capability {capability_id!r} has no criteria")
        result[capability_id] = capability
    return result


def _validate_case(case: Any, *, index: int, scenes: Mapping[str, Any], capabilities: Mapping[str, Mapping[str, Any]]) -> set[tuple[str, int]]:
    where = f"cases[{index}]"
    item = _object(case, where=where)
    case_id = _safe_path_id(item.get("case_id"), where=f"{where}.case_id")
    scene_id = _safe_path_id(item.get("scene"), where=f"{where}.scene")
    if scene_id not in scenes:
        raise SceneCaseError(f"{where}.scene references unknown scene {scene_id!r}")
    capability_id = _nonempty_text(item.get("capability_id"), where=f"{where}.capability_id")
    capability = capabilities.get(capability_id)
    if capability is None:
        raise SceneCaseError(f"{where}.capability_id references unknown capability {capability_id!r}")
    request = item.get("request")
    _validate_schema_value(
        request,
        capability.get("request_schema"),
        where=f"{where}.request",
    )
    execution = _object(item.get("execution"), where=f"{where}.execution")
    _number(execution.get("max_sim_time_s"), where=f"{where}.execution.max_sim_time_s", positive=True)
    _number(execution.get("wall_timeout_s"), where=f"{where}.execution.wall_timeout_s", positive=True)
    _validate_initial_state(item.get("initial_state"), where=f"{where}.initial_state")
    measurements = item.get("measurements")
    if not isinstance(measurements, list) or not measurements:
        raise SceneCaseError(f"{where}.measurements must be a non-empty array")
    covered: set[tuple[str, int]] = set()
    criteria = capability["criteria"]
    for measurement_index, raw_measurement in enumerate(measurements):
        measurement = _object(raw_measurement, where=f"{where}.measurements[{measurement_index}]")
        criterion_index = measurement.get("criterion_index")
        if (
            isinstance(criterion_index, bool)
            or not isinstance(criterion_index, int)
            or not 0 <= criterion_index < len(criteria)
        ):
            raise SceneCaseError(
                f"{where}.measurements[{measurement_index}].criterion_index is invalid"
            )
        key = (capability_id, criterion_index)
        if key in covered:
            raise SceneCaseError(f"{where} repeats criterion {criterion_index}")
        covered.add(key)
    return covered


def _validate_suite(suite: Any, *, design: Mapping[str, Any] | None = None) -> dict[str, Any]:
    root = _object(suite, where="scene/case suite")
    scenes = root.get("scenes")
    if not isinstance(scenes, Mapping) or not scenes:
        raise SceneCaseError("scenes must be a non-empty object")
    for raw_scene_id, scene in scenes.items():
        scene_id = _safe_path_id(raw_scene_id, where="scene id")
        _validate_scene(scene_id, scene)
    cases = root.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SceneCaseError("cases must be a non-empty array")
    case_ids: set[str] = set()
    capabilities: dict[str, Mapping[str, Any]] = {}
    if design is not None:
        capabilities = _capability_map(design)
    covered: set[tuple[str, int]] = set()
    for index, case in enumerate(cases):
        case_map = _object(case, where=f"cases[{index}]")
        case_id = _safe_path_id(
            case_map.get("case_id"),
            where=f"cases[{index}].case_id",
        )
        if case_id in case_ids:
            raise SceneCaseError(f"duplicate case_id {case_id!r}")
        case_ids.add(case_id)
        scene_id = _safe_path_id(
            case_map.get("scene"),
            where=f"cases[{index}].scene",
        )
        if scene_id not in scenes:
            raise SceneCaseError(
                f"cases[{index}].scene references unknown scene {scene_id!r}"
            )
        _nonempty_text(
            case_map.get("capability_id"),
            where=f"cases[{index}].capability_id",
        )
        if design is not None:
            covered.update(
                _validate_case(
                    case,
                    index=index,
                    scenes=scenes,
                    capabilities=capabilities,
                )
            )
    if design is not None:
        expected = {
            (capability_id, criterion_index)
            for capability_id, capability in capabilities.items()
            for criterion_index in range(len(capability["criteria"]))
        }
        missing = sorted(expected - covered)
        if missing:
            raise SceneCaseError(
                "suite does not cover every design capability criterion: "
                f"{missing}"
            )
    return dict(root)


def load_scene_cases(path: Path, *, design: dict) -> dict:
    """Load and minimally validate the YAML scene/case description."""

    source = Path(path)
    try:
        parsed = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise SceneCaseError(f"cannot read scene/case YAML {source}: {exc}") from None
    return _validate_suite(parsed, design=_object(design, where="design"))


def _resolve_resource(path_value: str, *, model_dir: Path, resource_dir: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute() and path.is_file():
        return path.resolve()
    candidates = [
        model_dir / resource_dir / path,
        model_dir / path,
        Path.cwd() / path,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise SceneCaseError(f"cannot resolve MuJoCo resource {path_value!r}")


def _normalise_resources(spec: Any, *, source: Path) -> None:
    model_dir = Path(str(spec.modelfiledir or source.parent)).resolve()
    meshdir = Path(str(spec.compiler.meshdir or "."))
    texturedir = Path(str(spec.compiler.texturedir or "."))
    for collection, resource_dir in (
        (getattr(spec, "meshes", []), meshdir),
        (getattr(spec, "textures", []), texturedir),
        (getattr(spec, "hfields", []), meshdir),
        (getattr(spec, "skins", []), meshdir),
    ):
        for resource in collection:
            if not hasattr(resource, "file"):
                continue
            file_value = getattr(resource, "file")
            if not isinstance(file_value, str) or not file_value.strip():
                continue
            resolved = _resolve_resource(
                file_value,
                model_dir=model_dir,
                resource_dir=resource_dir,
            )
            resource.file = str(resolved)


def _name_unnamed_base_geoms(spec: Any, *, source: Path) -> None:
    """Give unnamed base geoms stable IDs matching compiled MuJoCo indices."""

    base_model = mujoco.MjModel.from_xml_path(str(source))
    spec_geoms = list(getattr(spec, "geoms", []))
    if len(spec_geoms) != int(base_model.ngeom):
        raise SceneCaseError(
            "MjSpec geom traversal does not match base MuJoCo geom indices"
        )
    existing = _existing_names(spec, "geoms")
    for geom_index, geom in enumerate(spec_geoms):
        body_id = int(base_model.geom_bodyid[geom_index])
        body_name = str(base_model.body(body_id).name)
        parent = getattr(geom, "parent", None)
        if str(getattr(parent, "name", "")) != body_name:
            raise SceneCaseError(
                "MjSpec geom traversal does not preserve base geom body order"
            )
        if str(getattr(geom, "name", "")):
            continue
        generated = f"aa1_robot_geom_{geom_index}"
        if generated in existing:
            raise SceneCaseError(
                f"generated base geom name collides with existing name {generated!r}"
            )
        geom.name = generated
        existing.add(generated)


def _base_geom_inventory(source: Path, model: Any) -> dict[str, Any]:
    """Report aliases added to unnamed base geoms for DESIGN bindings."""

    base_model = mujoco.MjModel.from_xml_path(str(source))
    base_count = int(base_model.ngeom)
    if int(model.ngeom) < base_count:
        raise SceneCaseError("reloaded scene has fewer geoms than its base MJCF")
    aliases: list[dict[str, Any]] = []
    for geom_index in range(base_count):
        if str(base_model.geom(geom_index).name):
            continue
        generated_name = f"aa1_robot_geom_{geom_index}"
        if str(model.geom(geom_index).name) != generated_name:
            raise SceneCaseError(
                f"reloaded base geom {geom_index} does not have its generated name"
            )
        body_id = int(model.geom_bodyid[geom_index])
        aliases.append(
            {
                "base_geom_id": geom_index,
                "generated_name": generated_name,
                "body_name": str(model.body(body_id).name),
            }
        )
    return {"base_geom_count": base_count, "unnamed_base_geoms": aliases}


def _existing_names(spec: Any, collection: str) -> set[str]:
    return {
        str(getattr(item, "name", ""))
        for item in getattr(spec, collection, [])
        if str(getattr(item, "name", ""))
    }


def _append_scene_objects(spec: Any, scene: Mapping[str, Any], *, scene_id: str) -> None:
    existing_bodies = _existing_names(spec, "bodies")
    existing_geoms = _existing_names(spec, "geoms")
    for index, raw_object in enumerate(scene.get("objects", [])):
        item = _object(raw_object, where=f"scenes.{scene_id}.objects[{index}]")
        name = str(item["name"])
        if name in existing_bodies:
            raise SceneCaseError(
                f"scene object {name!r} collides with a base MJCF body name"
            )
        # Keep the authored object name readable in bindings while avoiding
        # an awkward ``*_geom_geom`` when the author already supplied the
        # conventional geom suffix.
        geom_name = name if name.endswith("_geom") else f"{name}_geom"
        if geom_name in existing_geoms:
            raise SceneCaseError(
                f"scene object {name!r} collides with a base MJCF geom name"
            )
        quaternion = np.asarray(item["quaternion_wxyz"], dtype=float)
        quaternion /= np.linalg.norm(quaternion)
        body = spec.worldbody.add_body(name=name)
        body.pos = np.asarray(item["position_m"], dtype=float)
        body.quat = quaternion
        if item["motion"] == "free":
            body.add_freejoint()
        geom = body.add_geom(name=geom_name)
        geom.type = _GEOM_TYPES[item["shape"]]
        size = np.zeros(3, dtype=float)
        size[:len(item["size_m"])] = item["size_m"]
        geom.size = size
        if "friction" in item:
            geom.friction = np.asarray(item["friction"], dtype=float)
        if "mass_kg" in item:
            geom.mass = float(item["mass_kg"])


def _build_scene_xml(mjcf_path: Path, scene: Mapping[str, Any], *, scene_id: str, output: Path) -> None:
    source = Path(mjcf_path).resolve()
    if not source.is_file():
        raise SceneCaseError(f"base MJCF does not exist: {source}")
    try:
        spec = mujoco.MjSpec.from_file(str(source))
        _normalise_resources(spec, source=source)
        _name_unnamed_base_geoms(spec, source=source)
        _append_scene_objects(spec, scene, scene_id=scene_id)
        xml = spec.to_xml()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(xml, encoding="utf-8")
        # Compile once from the exported path so prepare_scenes never returns
        # a file that only worked while the source MjSpec was in memory.
        mujoco.MjModel.from_xml_path(str(output))
    except (OSError, ValueError, RuntimeError, SceneCaseError) as exc:
        if isinstance(exc, SceneCaseError):
            raise
        raise SceneCaseError(
            f"could not assemble/reload scene {scene_id!r}: {exc}"
        ) from exc


def prepare_scenes(*, mjcf_path: Path, suite: dict, output_dir: Path) -> dict[str, Path]:
    """Assemble every described scene from the supplied base robot MJCF."""

    checked = _validate_suite(suite)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    scenes = checked["scenes"]
    for scene_id, scene in scenes.items():
        scene_path = output / "scenes" / str(scene_id) / "scene.xml"
        _build_scene_xml(
            Path(mjcf_path),
            _object(scene, where=f"scenes.{scene_id}"),
            scene_id=str(scene_id),
            output=scene_path,
        )
        result[str(scene_id)] = scene_path.resolve()
    return result


def _model_named_id(model: Any, kind: str, name: str, *, where: str) -> int:
    try:
        return int(getattr(model, kind)(name).id)
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise SceneCaseError(f"{where} {name!r} is not present in the model") from exc


def _scalar_joint_address(model: Any, name: str, *, where: str) -> int:
    joint_id = _model_named_id(model, "joint", name, where=where)
    joint_type = int(model.jnt_type[joint_id])
    if joint_type not in {
        int(mujoco.mjtJoint.mjJNT_HINGE),
        int(mujoco.mjtJoint.mjJNT_SLIDE),
    }:
        raise SceneCaseError(f"{where} {name!r} is not a scalar hinge or slide joint")
    return int(model.jnt_qposadr[joint_id])


def _free_joint_for_body(model: Any, body_name: str, *, where: str) -> tuple[int, int]:
    body_id = _model_named_id(model, "body", body_name, where=where)
    start = int(model.body_jntadr[body_id])
    count = int(model.body_jntnum[body_id])
    for joint_id in range(start, start + count):
        if int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_FREE):
            return int(model.jnt_qposadr[joint_id]), int(model.jnt_dofadr[joint_id])
    raise SceneCaseError(f"{where} {body_name!r} has no free joint")


def apply_initial_state(model: Any, data: Any, case: dict) -> None:
    """Reset and apply the case state in the required order."""

    case_map = _object(case, where="case")
    initial_state = _object(case_map.get("initial_state"), where="case.initial_state")
    robot = _object(initial_state.get("robot", {}), where="case.initial_state.robot")
    keyframe = robot.get("keyframe")
    try:
        if keyframe is None:
            mujoco.mj_resetData(model, data)
        else:
            if isinstance(keyframe, str):
                keyframe_id = int(model.key(keyframe).id)
            elif isinstance(keyframe, int) and not isinstance(keyframe, bool):
                keyframe_id = int(keyframe)
            else:
                raise SceneCaseError("case.initial_state.robot.keyframe must be a name or integer")
            if keyframe_id < 0 or keyframe_id >= int(model.nkey):
                raise SceneCaseError(f"unknown robot keyframe {keyframe!r}")
            mujoco.mj_resetDataKeyframe(model, data, keyframe_id)
    except (KeyError, ValueError, TypeError) as exc:
        raise SceneCaseError(f"cannot reset requested keyframe {keyframe!r}: {exc}") from exc

    qpos_by_joint = _object(robot.get("qpos_by_joint", {}), where="case.initial_state.robot.qpos_by_joint")
    for name, value in qpos_by_joint.items():
        address = _scalar_joint_address(model, str(name), where="case.initial_state.robot.qpos_by_joint")
        data.qpos[address] = _number(value, where=f"qpos_by_joint.{name}")

    free_bodies = _object(
        initial_state.get("free_bodies", {}),
        where="case.initial_state.free_bodies",
    )
    # A keyframe authored for the robot often has no meaningful values for a
    # newly appended free body.  Requiring an explicit state here prevents a
    # keyframe reset from silently moving a prepared object to the origin.
    if keyframe is not None:
        free_joint_body_names: list[str] = []
        for body_id in range(1, int(model.nbody)):
            start = int(model.body_jntadr[body_id])
            count = int(model.body_jntnum[body_id])
            if any(
                int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_FREE)
                for joint_id in range(start, start + count)
            ):
                name = str(model.body(body_id).name)
                free_joint_id = next(
                    joint_id
                    for joint_id in range(start, start + count)
                    if int(model.jnt_type[joint_id])
                    == int(mujoco.mjtJoint.mjJNT_FREE)
                )
                free_qpos_address = int(model.jnt_qposadr[free_joint_id])
                if name and not np.allclose(
                    model.key_qpos[
                        keyframe_id, free_qpos_address : free_qpos_address + 7
                    ],
                    model.qpos0[free_qpos_address : free_qpos_address + 7],
                    rtol=0.0,
                    atol=1e-12,
                ):
                    free_joint_body_names.append(name)
        missing = [name for name in free_joint_body_names if name not in free_bodies]
        if missing:
            raise SceneCaseError(
                "keyframe reset requires explicit initial state for free bodies "
                f"{missing}"
            )

    for name, raw_body in free_bodies.items():
        body = _object(raw_body, where=f"case.initial_state.free_bodies.{name}")
        qpos_address, qvel_address = _free_joint_for_body(
            model,
            str(name),
            where="case.initial_state.free_bodies",
        )
        position = _array(
            body.get("position_m"), length=3, where=f"free_bodies.{name}.position_m"
        )
        quaternion = np.asarray(
            _array(
                body.get("quaternion_wxyz"),
                length=4,
                where=f"free_bodies.{name}.quaternion_wxyz",
            ),
            dtype=float,
        )
        norm = float(np.linalg.norm(quaternion))
        if norm <= 1e-12:
            raise SceneCaseError(f"free_bodies.{name}.quaternion_wxyz must be non-zero")
        data.qpos[qpos_address : qpos_address + 3] = position
        data.qpos[qpos_address + 3 : qpos_address + 7] = quaternion / norm
        linear = body.get("linear_velocity_m_s", [0.0, 0.0, 0.0])
        angular = body.get("angular_velocity_rad_s", [0.0, 0.0, 0.0])
        data.qvel[qvel_address : qvel_address + 3] = _array(
            linear,
            length=3,
            where=f"free_bodies.{name}.linear_velocity_m_s",
        )
        data.qvel[qvel_address + 3 : qvel_address + 6] = _array(
            angular,
            length=3,
            where=f"free_bodies.{name}.angular_velocity_rad_s",
        )

    ctrl_by_actuator = _object(
        initial_state.get("ctrl_by_actuator", {}),
        where="case.initial_state.ctrl_by_actuator",
    )
    for name, value in ctrl_by_actuator.items():
        actuator_id = _model_named_id(
            model,
            "actuator",
            str(name),
            where="case.initial_state.ctrl_by_actuator",
        )
        data.ctrl[actuator_id] = _number(
            value,
            where=f"ctrl_by_actuator.{name}",
        )

    mujoco.mj_forward(model, data)
    settle_s = _number(
        initial_state.get("settle_s", 0.0),
        where="case.initial_state.settle_s",
        nonnegative=True,
    )
    if settle_s > 0.0:
        timestep = float(model.opt.timestep)
        if timestep <= 0.0:
            raise SceneCaseError("model timestep must be positive for settle")
        steps = max(1, int(math.ceil(settle_s / timestep)))
        for _ in range(steps):
            mujoco.mj_step(model, data)
    mujoco.mj_forward(model, data)


def _case_by_id(suite: Mapping[str, Any], case_id: str) -> Mapping[str, Any]:
    cases = suite.get("cases")
    if not isinstance(cases, list):
        raise SceneCaseError("suite.cases must be an array")
    for case in cases:
        if isinstance(case, Mapping) and case.get("case_id") == case_id:
            return case
    raise SceneCaseError(f"unknown case_id {case_id!r}")


def _criterion_for_case(design: Mapping[str, Any], case: Mapping[str, Any], criterion_index: int) -> Mapping[str, Any]:
    capabilities = _capability_map(design)
    capability_id = case.get("capability_id")
    capability = capabilities.get(capability_id)
    if capability is None:
        raise SceneCaseError(f"case references unknown capability {capability_id!r}")
    criteria = capability.get("criteria")
    if not isinstance(criteria, list) or not isinstance(criterion_index, int) or not 0 <= criterion_index < len(criteria):
        raise SceneCaseError(f"invalid criterion_index {criterion_index!r}")
    criterion = criteria[criterion_index]
    if not isinstance(criterion, Mapping):
        raise SceneCaseError("criterion must be an object")
    return criterion


def probe_case(
    *,
    mjcf_path: Path,
    suite: dict,
    design: dict,
    output_dir: Path,
    case_id: str,
) -> dict:
    """Assemble, reload, initialise, bind, and briefly step one case.

    The returned report describes compilation and binding readiness only.  It
    intentionally contains no capability pass/fail verdict.
    """

    report: dict[str, Any] = {
        "case_id": case_id,
        "probe_only": True,
        "compiled": False,
        "reloaded": False,
        "bindings_resolved": False,
        "steps": 0,
        "errors": [],
    }
    try:
        checked = _validate_suite(suite, design=_object(design, where="design"))
        case = _case_by_id(checked, case_id)
        scene_id = str(case["scene"])
        scene_paths = prepare_scenes(
            mjcf_path=Path(mjcf_path),
            suite=checked,
            output_dir=Path(output_dir),
        )
        scene_path = scene_paths[scene_id]
        report["scene"] = scene_id
        report["scene_path"] = str(scene_path)
        report["compiled"] = True
        model = mujoco.MjModel.from_xml_path(str(scene_path))
        data = mujoco.MjData(model)
        report["reloaded"] = True
        report["model_inventory"] = _base_geom_inventory(
            Path(mjcf_path).resolve(), model
        )
        apply_initial_state(model, data, dict(case))
        bindings: list[dict[str, Any]] = []
        for raw_measurement in case["measurements"]:
            measurement = _object(raw_measurement, where="case.measurements")
            criterion_index = measurement["criterion_index"]
            criterion = _criterion_for_case(design, case, criterion_index)
            validate_measurement(model, measurement, criterion, case["request"])
            bindings.append(
                {
                    "criterion_index": criterion_index,
                    "operator": measurement["operator"],
                    "resolved": True,
                }
            )
        report["bindings"] = bindings
        report["bindings_resolved"] = True
        report["initial_sample"] = capture_sample(model, data)
        max_sim_time = _number(
            _object(case["execution"], where="case.execution")["max_sim_time_s"],
            where="case.execution.max_sim_time_s",
            positive=True,
        )
        timestep = float(model.opt.timestep)
        duration = min(max_sim_time, 0.05)
        steps = max(1, int(math.ceil(duration / timestep))) if timestep > 0 else 1
        for _ in range(steps):
            mujoco.mj_step(model, data)
        report["steps"] = steps
        report["post_step_sample"] = capture_sample(model, data)
        report["sim_time_s"] = float(data.time)
        report["ok"] = True
        return report
    except (SceneCaseError, MeasurementError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        report.setdefault("errors", []).append(str(exc))
        report["ok"] = False
        return report


__all__ = [
    "SceneCaseError",
    "apply_initial_state",
    "load_scene_cases",
    "prepare_scenes",
    "probe_case",
]
