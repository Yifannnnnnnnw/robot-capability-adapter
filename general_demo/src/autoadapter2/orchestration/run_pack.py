"""Deterministic preparation of the first two-robot G2 run packs.

This module assembles only frozen JSON inputs.  It does not run a readiness
probe, an LLM, a simulator, a Session, or a Demo.  Readiness and reviewed Blue
Line inputs are supplied as external, content-addressed evidence.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..consumer.tool_layer import validate_closed_object_schema, validate_json_object
from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError, GateError, IntegrityError
from ..foundation.hashing import content_hash, sha256_bytes
from ..foundation.seals import verify_seal
from ..generation.model_api import DEFAULT_BASE_URL, DEFAULT_MODEL
from ..generation.stage1 import check_capability_design
from ..implementation import validate_implementation_bundle
from ..integration import ExperimentIntegrationGate, JsonArtifact, write_stable_json
from ..integration.artifacts import (
    load_integration_manifest,
    load_json_artifact,
    load_readiness_report,
    stable_json_bytes,
    stable_json_sha256,
    validate_readiness_profile,
    verify_file_reference,
)
from ..libraries import TasksLibrary
from ..validation import ValidationAProfile


SCHEMA_VERSION = "1.0.0"
G2_PROFILE_STABLE_SHA256 = "a7a74d81d0854aa2de8caa8e318430abf9981c64a75429b518fec122a9391c65"
G2_PROFILE_RELATIVE_PATH = (
    "general_demo/contracts/profiles/granularity/"
    "g2-reusable-effect/1.0.0/profile.json"
)
TASK_LIBRARY_RELATIVE_PATH = "general_demo/libraries/tasks"
DEFAULT_MANIFESTS = {
    "so-arm101": "general_demo/integrations/so-arm101/integration_manifest.json",
    "unitree-go2": "general_demo/integrations/unitree-go2/integration_manifest.json",
}
ROBOT_ALIASES = {
    "so": "so-arm101",
    "so-arm101": "so-arm101",
    "go2": "unitree-go2",
    "unitree-go2": "unitree-go2",
}
MODEL_API_KEY_ENV = "AUTOADAPTER_MODEL_API_KEY"
MEASUREMENT_SESSION_TASK = "first_g2_capability_validation_b"
PUBLIC_EFFECT_ALLOWLIST = {
    "so-arm101": [
        "move_end_effector_to_target",
        "establish_target_contact",
        "move_object_to_region",
        "grasp_and_lift_object",
        "actuate_target_button",
    ],
    "unitree-go2": [
        "stand",
        "sit",
        "hold_stable",
        "move_forward",
        "target_body_height",
    ],
}
FROZEN_VIDEO_PROFILE = {
    "profile_id": "framework-external-scene-ffv1",
    "profile_version": "1.0.0",
    "camera": "external-scene",
    "view": "external-scene",
    "fps": 30,
    "resolution": {"width": 640, "height": 480},
    "container": "matroska",
    "codec": "ffv1",
}
G2_PROFILE_PUBLIC = {
    "profile_id": "g2-reusable-effect",
    "version": "1.0.0",
    "granularity": "G2",
}


class RunPackError(ContractError):
    """A preparation/configuration error that prevents a formal pack."""


def _as_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RunPackError(f"{label} must be a JSON object")
    return copy.deepcopy(dict(value))


def _nonempty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunPackError(f"{label} must be non-empty text")
    return value


def _finite_number(value: Any, label: str, *, positive: bool = False) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RunPackError(f"{label} must be a finite number")
    if positive and value <= 0:
        raise RunPackError(f"{label} must be positive")
    return value


def _json_file(root: Path, relative: str, value: Any) -> tuple[Path, dict[str, str]]:
    path = root / Path(*relative.split("/"))
    digest = write_stable_json(path, value)
    try:
        normalized = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RunPackError(f"generated artifact escapes project root: {relative}") from exc
    return path, {"path": normalized, "sha256": digest}


def _write_new_json(root: Path, path: Path, value: Mapping[str, Any]) -> JsonArtifact:
    """Write a finalized snapshot with O_EXCL; never replace an existing file."""

    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RunPackError("finalized snapshot path must be beneath the project root") from exc
    if path.is_symlink():
        raise RunPackError("finalized snapshot path must not be a symlink")
    payload = stable_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise RunPackError(f"finalized snapshot already exists: {relative}") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    return load_json_artifact(path)


def _relative_artifact_ref(root: Path, artifact: JsonArtifact) -> dict[str, str]:
    try:
        relative = artifact.path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RunPackError(f"external artifact is outside the project root: {artifact.path}") from exc
    if artifact.path.is_symlink():
        raise RunPackError(f"external artifact must not be a symlink: {artifact.path}")
    return {"path": relative, "sha256": artifact.sha256}


def _resolve_input(root: Path, supplied: str | Path | None, default: str | None, label: str) -> Path:
    raw = supplied if supplied is not None else default
    if raw is None:
        raise RunPackError(f"{label} is required")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise RunPackError(f"{label} must be an existing regular file, not a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise RunPackError(
            f"{label} must be an existing regular file beneath the project root"
        ) from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise RunPackError(f"{label} must be an existing regular file")
    return resolved


def _load_template(root: Path, relative: str, label: str) -> dict[str, Any]:
    path = root / Path(*relative.split("/"))
    try:
        return load_json_artifact(path).value
    except (OSError, ContractError) as exc:
        raise RunPackError(f"cannot load {label} template: {relative}") from exc


def _normalize_robot(robot: str) -> str:
    normalized = _nonempty_text(robot, "robot").strip().lower()
    try:
        return ROBOT_ALIASES[normalized]
    except KeyError as exc:
        raise RunPackError("robot must be SO-ARM101 or Go2") from exc


def _robot_template_path(robot: str) -> str:
    return f"general_demo/config/first_g2_demo/robots/{robot}.json"


def _recursive_forbidden(
    value: Any,
    forbidden: tuple[str, ...],
    location: str = "$",
    *,
    reject_string_values: bool = False,
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(term in lowered for term in forbidden):
                raise RunPackError(f"{location}.{key} contains a forbidden private field")
            _recursive_forbidden(
                item,
                forbidden,
                f"{location}.{key}",
                reject_string_values=reject_string_values,
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _recursive_forbidden(
                item,
                forbidden,
                f"{location}[{index}]",
                reject_string_values=reject_string_values,
            )
    elif reject_string_values and isinstance(value, str):
        lowered = value.lower()
        if any(term in lowered for term in forbidden):
            raise RunPackError(f"{location} contains forbidden private content")


def _reject_secret_fields(value: Any, location: str = "$") -> None:
    """Reject credential-bearing fields without rejecting ``credential_env``."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"api_key", "secret", "token_value"}:
                raise RunPackError(f"{location}.{key} must not contain a credential")
            _reject_secret_fields(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_fields(item, f"{location}[{index}]")


def _assert_closed_public_schema(schema: Mapping[str, Any], location: str = "public_state_schema") -> None:
    if not isinstance(schema, Mapping):
        raise RunPackError(f"{location} must be an object")
    schema_type = schema.get("type")
    if schema_type == "object":
        if schema.get("additionalProperties") is not False:
            raise RunPackError(f"{location} must close object properties")
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            raise RunPackError(f"{location}.properties must be an object")
        for name, child in properties.items():
            _assert_closed_public_schema(child, f"{location}.properties.{name}")
    elif schema_type == "array":
        if "items" not in schema:
            raise RunPackError(f"{location}.items is required")
        _assert_closed_public_schema(schema["items"], f"{location}.items")
    elif schema_type not in {"string", "number", "integer", "boolean", "null"}:
        raise RunPackError(f"{location}.type is unsupported")


def _validate_model_config(config: Mapping[str, Any]) -> dict[str, Any]:
    value = _as_object(config, "model_prompt_config")
    required = {
        "artifact_type", "schema_version", "provider", "base_url", "endpoint_path",
        "model", "max_tokens", "temperature", "timeout_s", "credential_env", "roles",
    }
    if set(value) != required:
        raise RunPackError("model_prompt_config has an unexpected or missing field")
    if value["artifact_type"] != "model_prompt_config" or value["schema_version"] != SCHEMA_VERSION:
        raise RunPackError("model_prompt_config identity is invalid")
    if value["provider"] != "anthropic-compatible":
        raise RunPackError("first G2 model provider must be anthropic-compatible")
    if value["base_url"] != DEFAULT_BASE_URL:
        raise RunPackError("first G2 model config must use the frozen long endpoint base URL")
    if value["endpoint_path"] != "/v1/chat/completions":
        raise RunPackError("first G2 model config has the wrong endpoint path")
    if value["model"] != DEFAULT_MODEL:
        raise RunPackError("first G2 model config must use Claude Sonnet 4.5")
    if value["max_tokens"] != 4096 or value["temperature"] != 0 or value["timeout_s"] != 125:
        raise RunPackError("first G2 model limits are not frozen")
    if value["credential_env"] != MODEL_API_KEY_ENV:
        raise RunPackError("first G2 model credentials must remain environment-only")
    if not isinstance(value["roles"], Mapping) or set(value["roles"]) != {
        "stage1", "blue_line", "stage2", "repair", "consumer"
    }:
        raise RunPackError("model_prompt_config roles are incomplete")
    _reject_secret_fields(value)
    return value


def _validate_budget(value: Mapping[str, Any]) -> dict[str, Any]:
    budget = _as_object(value, "budget")
    if set(budget) != {"artifact_type", "schema_version", "stage1", "blue_line", "stage2", "repair", "consumer", "demo"}:
        raise RunPackError("budget fields are not closed")
    if budget["artifact_type"] != "run_budget" or budget["schema_version"] != SCHEMA_VERSION:
        raise RunPackError("budget identity is invalid")
    expected = {
        "stage1": {"max_correction_calls": 2},
        "blue_line": {"max_inference_calls": 3},
        "stage2": {"max_llm_calls": 30},
        "repair": {"max_repairs": 10, "max_infrastructure_retries": 1},
        "consumer": {"max_steps": 4},
        "demo": {"repetitions": 1},
    }
    for role, fields in expected.items():
        if budget.get(role) != fields:
            raise RunPackError(f"budget.{role} does not match the first G2 contract")
    return budget


def _validate_robot_template(template: Mapping[str, Any], robot: str, configuration: str) -> dict[str, Any]:
    value = _as_object(template, "robot template")
    required_fields = {
        "artifact_type", "schema_version", "robot_model_id", "robot_configuration_id",
        "public_projection", "public_state_schema", "public_task_states", "task_instances",
        "blue_line_policy", "validation_a_template", "validation_harness_config",
        "implementation_projection", "implementation_experience",
    }
    if set(value) != required_fields:
        raise RunPackError("robot template fields are not closed")
    if value.get("artifact_type") != "first_g2_robot_template" or value.get("schema_version") != SCHEMA_VERSION:
        raise RunPackError("robot template identity is invalid")
    if value.get("robot_model_id") != robot or value.get("robot_configuration_id") != configuration:
        raise RunPackError("robot template does not match the selected configuration")
    for field in (
        "public_projection", "public_state_schema", "public_task_states", "task_instances",
        "blue_line_policy", "validation_a_template", "validation_harness_config",
        "implementation_projection", "implementation_experience",
    ):
        if field not in value:
            raise RunPackError(f"robot template is missing {field}")
    if "standards_snapshot" in value or "measurement_catalog" in value:
        raise RunPackError("robot template must not self-assert reviewed Blue Line inputs")
    _assert_closed_public_schema(value["public_state_schema"])
    if not isinstance(value["public_task_states"], Mapping):
        raise RunPackError("robot template public_task_states must be an object")
    if not isinstance(value["implementation_experience"], list) or not value["implementation_experience"]:
        raise RunPackError("robot template implementation_experience must be non-empty factual guidance")
    return value


def _morphology_topology(morphology: Mapping[str, Any]) -> dict[str, Any]:
    joint_names = morphology.get("joint_names")
    actuator_names = morphology.get("actuator_names")
    if (
        not isinstance(joint_names, list) or not joint_names
        or not isinstance(actuator_names, list) or len(actuator_names) != len(joint_names)
    ):
        raise RunPackError("morphology joint and actuator order is required")
    groups = morphology.get("joint_groups")
    if groups is None:
        groups = {
            "front_right": copy.deepcopy(joint_names[0:3]),
            "front_left": copy.deepcopy(joint_names[3:6]),
            "rear_right": copy.deepcopy(joint_names[6:9]),
            "rear_left": copy.deepcopy(joint_names[9:12]),
        }
    if not isinstance(groups, Mapping) or not groups:
        raise RunPackError("morphology joint_groups must be non-empty")
    effectors = morphology.get("end_effectors")
    if effectors is None:
        effectors = [f"{name}_foot" for name in ("front_right", "front_left", "rear_right", "rear_left")]
    if not isinstance(effectors, list) or not effectors or not all(isinstance(item, str) and item.strip() for item in effectors):
        raise RunPackError("morphology effectors must be a non-empty public list")
    return {
        "base_type": morphology.get("base_type"),
        "actuated_dof": morphology.get("actuated_dof"),
        "joint_groups": copy.deepcopy(dict(groups)),
        "joint_names": copy.deepcopy(joint_names),
        "actuator_names": copy.deepcopy(actuator_names),
        "effectors": copy.deepcopy(effectors),
    }


def _validate_public_projection_template(template: Mapping[str, Any], robot: str) -> dict[str, Any]:
    projection = _as_object(template, "public_projection")
    required = {"affordances", "units", "frames", "topology", "sensors", "ranges", "lifecycle", "limits", "unsupported_behavior", "effect_allowlist"}
    if set(projection) != required:
        raise RunPackError("public_projection fields are not the closed first-G2 public surface")
    affordances = _as_object(projection["affordances"], "public_projection.affordances")
    if set(affordances) != {"actions", "observations"}:
        raise RunPackError("public_projection.affordances is not closed")
    for field in ("actions", "observations", "frames", "sensors", "lifecycle", "unsupported_behavior", "effect_allowlist"):
        value = projection[field] if field in projection else affordances[field]
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
            raise RunPackError(f"public_projection.{field} must be a non-empty string array")
    if projection["effect_allowlist"] != PUBLIC_EFFECT_ALLOWLIST[robot]:
        raise RunPackError("public_projection effect_allowlist is not the exact first-G2 public allowlist")
    units = projection["units"]
    if not isinstance(units, Mapping) or not units or not all(isinstance(key, str) and key.strip() for key in units) or not all(isinstance(item, str) and item.strip() for item in units.values()):
        raise RunPackError("public_projection.units is malformed")
    if not isinstance(projection["topology"], Mapping) or not projection["topology"]:
        raise RunPackError("public_projection.topology must be non-empty")
    if not isinstance(projection["ranges"], Mapping) or not projection["ranges"]:
        raise RunPackError("public_projection.ranges must be non-empty")
    if not isinstance(projection["limits"], Mapping) or not projection["limits"]:
        raise RunPackError("public_projection.limits must be non-empty")
    _recursive_forbidden(
        projection,
        ("private", "criterion", "translation", "mujoco", "secret", "credential", "runtime", "source"),
        reject_string_values=True,
    )
    return projection


def _build_robot_projection(
    manifest: Mapping[str, Any],
    morphology: Mapping[str, Any],
    sdk: Mapping[str, Any],
    template: Mapping[str, Any],
) -> dict[str, Any]:
    robot = str(manifest["robot_model_id"])
    projection_template = _validate_public_projection_template(template["public_projection"], robot)
    affordances = projection_template["affordances"]
    units = projection_template["units"]
    topology = _morphology_topology(morphology)
    if topology != projection_template["topology"]:
        raise RunPackError("public_projection topology does not match the checked-in morphology record")
    observations = affordances["observations"]
    if robot == "unitree-go2" and list(morphology.get("state", [])) != [
        "joint_position", "joint_velocity", "estimated_torque", "imu"
    ]:
        raise RunPackError("Go2 morphology state facts changed")
    projection = {
        "artifact_type": "robot_projection",
        "schema_version": SCHEMA_VERSION,
        "robot_model_id": manifest["robot_model_id"],
        "robot_configuration_id": manifest["robot_configuration_id"],
        "rim_facts": {
            "base_type": morphology["base_type"],
            "actuated_dof": morphology["actuated_dof"],
        },
        "topology": topology,
        "sdk_facts": {
            "entry_id": sdk["id"],
            "entry_version": sdk["version"],
            "control_modes": copy.deepcopy(affordances["actions"]),
            "observation_modes": copy.deepcopy(observations),
            "units": copy.deepcopy(dict(units)),
            "motor_slot_count": sdk.get("motor_slot_count"),
            "active_motor_count": sdk.get("active_motor_count"),
        },
        "affordances": copy.deepcopy(dict(affordances)),
        "action_affordances": copy.deepcopy(affordances["actions"]),
        "observation_affordances": copy.deepcopy(observations),
        "effect_allowlist": copy.deepcopy(projection_template["effect_allowlist"]),
        "sensors": copy.deepcopy(projection_template["sensors"]),
        "units": list(dict.fromkeys(units.values())),
        "frames": copy.deepcopy(projection_template["frames"]),
        "ranges": copy.deepcopy(projection_template["ranges"]),
        "lifecycle": copy.deepcopy(projection_template["lifecycle"]),
        "limits": copy.deepcopy(projection_template["limits"]),
        "unsupported_behavior": copy.deepcopy(projection_template["unsupported_behavior"]),
        "public_state_schema": copy.deepcopy(dict(template["public_state_schema"])),
    }
    if projection["sdk_facts"]["motor_slot_count"] is None:
        projection["sdk_facts"].pop("motor_slot_count")
    if projection["sdk_facts"]["active_motor_count"] is None:
        projection["sdk_facts"].pop("active_motor_count")
    _assert_closed_public_schema(projection["public_state_schema"])
    _recursive_forbidden(
        projection,
        ("private", "criterion", "translation", "mujoco", "secret", "credential", "runtime", "source"),
        reject_string_values=True,
    )
    return projection


def _validate_task_instances(
    template: Mapping[str, Any],
    robot: str,
    configuration: str,
) -> dict[str, Any]:
    value = _as_object(template["task_instances"], "task_instances")
    required = {
        "artifact_type", "schema_version", "robot_model_id", "robot_configuration_id",
        "task_catalog_version", "instances",
    }
    if set(value) != required or value["artifact_type"] != "first_g2_task_instances" or value["schema_version"] != SCHEMA_VERSION:
        raise RunPackError("task_instances identity or fields are invalid")
    if value["robot_model_id"] != robot or value["robot_configuration_id"] != configuration or value["task_catalog_version"] != "1.0.0":
        raise RunPackError("task_instances robot or catalog binding is invalid")
    instances = value["instances"]
    if not isinstance(instances, list) or len(instances) != 5:
        raise RunPackError("each first-G2 pack requires exactly five task instances")
    seen: set[str] = set()
    for index, raw in enumerate(instances):
        item = _as_object(raw, f"task_instances.instances[{index}]")
        if set(item) != {"task_id", "instance_version", "public_state", "private_binding"}:
            raise RunPackError(f"task instance {index} fields are not closed")
        task_id = _nonempty_text(item["task_id"], f"task instance {index}.task_id")
        if task_id in seen:
            raise RunPackError("task instance IDs must be unique")
        seen.add(task_id)
        if item["instance_version"] != "1.0.0":
            raise RunPackError(f"task instance {task_id} version is not frozen")
        public_state = _as_object(item["public_state"], f"task instance {task_id}.public_state")
        private_binding = _as_object(item["private_binding"], f"task instance {task_id}.private_binding")
        if set(private_binding) != {"reset", "evaluation"}:
            raise RunPackError(f"task instance {task_id} must split reset and evaluation binding")
        if not private_binding["reset"] or not private_binding["evaluation"]:
            raise RunPackError(f"task instance {task_id} has an empty private binding section")
        if any(term in json.dumps(public_state, sort_keys=True).lower() for term in ("private", "criterion", "evaluation", "reset")):
            raise RunPackError(f"task instance {task_id}.public_state contains private binding text")
    return value


def _build_task_set(
    root: Path,
    run_id: str,
    configuration: str,
    template: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, Any]]:
    package = TasksLibrary(root / TASK_LIBRARY_RELATIVE_PATH).load(configuration, "1.0.0")
    public_tasks = package.demo_public_tasks(run_id)
    stage1_tasks = package.stage1_projection(run_id)
    private_criteria = package.demo_private_criteria()
    if len(public_tasks) != 5 or len(stage1_tasks) != 5 or len(private_criteria) != 5:
        raise RunPackError("first G2 Tasks Library selection must contain exactly five tasks")
    states = template["public_task_states"]
    schema = validate_closed_object_schema(template["public_state_schema"], label="public_state_schema")
    raw_instances = _validate_task_instances(template, str(template["robot_model_id"]), configuration)
    instances_by_id = {item["task_id"]: item for item in raw_instances["instances"]}
    tasks: list[dict[str, Any]] = []
    packed_instances: list[dict[str, Any]] = []
    for public, stage1, criterion in zip(public_tasks, stage1_tasks, private_criteria, strict=True):
        if set(stage1) != {"requirement_id", "description"} or stage1 != {
            "requirement_id": public["requirement_id"],
            "description": public["description"],
        }:
            raise RunPackError("Tasks Library Stage 1 projection is not the exact public task view")
        task_id = public["task_id"]
        if task_id not in states or task_id not in instances_by_id:
            raise RunPackError(f"robot template is missing task instance state for {task_id}")
        try:
            state = validate_json_object(copy.deepcopy(states[task_id]), schema, label=f"public_state.{task_id}")
        except Exception as exc:
            if isinstance(exc, RunPackError):
                raise
            raise RunPackError(f"public_state.{task_id} does not match the closed schema") from exc
        instance = instances_by_id[task_id]
        if instance["public_state"] != state:
            raise RunPackError(f"task instance public_state does not match public_task_states for {task_id}")
        tasks.append({
            "requirement_id": public["requirement_id"],
            "task_id": task_id,
            "description": public["description"],
            "public_state": state,
            "private_criterion": copy.deepcopy(criterion),
        })
        packed_instances.append({
            "task_id": task_id,
            "instance_version": instance["instance_version"],
            "requirement_id": public["requirement_id"],
            "public_state": state,
            "private_binding": copy.deepcopy(instance["private_binding"]),
        })
    if set(instances_by_id) != {item["task_id"] for item in public_tasks}:
        raise RunPackError("task_instances must cover exactly the fixed five Tasks Library records")
    task_instances = {
        "artifact_type": "first_g2_task_instances",
        "schema_version": SCHEMA_VERSION,
        "robot_model_id": template["robot_model_id"],
        "robot_configuration_id": configuration,
        "task_catalog_version": "1.0.0",
        "instances": packed_instances,
    }
    return {"tasks": tasks}, stage1_tasks, task_instances


def _validate_standards(snapshot: Mapping[str, Any], robot: str, configuration: str) -> dict[str, Any]:
    value = _as_object(snapshot, "standards_snapshot")
    if value.get("artifact_type") != "standards_snapshot" or value.get("schema_version") != SCHEMA_VERSION:
        raise RunPackError("standards_snapshot identity is invalid")
    if value.get("robot_model_id") != robot or value.get("robot_configuration_id") != configuration or value.get("intended_use") != "capability_validation_b":
        raise RunPackError("reviewed standards_snapshot robot, configuration, or intended use is invalid")
    if value.get("demo_criteria_import_policy") not in {None, "NOT_AUTOMATIC"}:
        raise RunPackError("reviewed Blue Line standards must not become Demo criteria automatically")
    standards = value.get("standards")
    if not isinstance(standards, list) or not standards:
        raise RunPackError("standards_snapshot must contain standards")
    required = {
        "standard_id", "measurement_id", "metric", "comparator", "threshold_value",
        "dwell_s", "timeout_s", "aggregation",
    }
    identifiers: set[str] = set()
    for index, item in enumerate(standards):
        if not isinstance(item, Mapping) or set(item) != required:
            raise RunPackError(f"standards_snapshot.standard[{index}] is not the closed reviewed Blue Line record")
        standard_id = _nonempty_text(item["standard_id"], f"standards_snapshot.standard[{index}].standard_id")
        _nonempty_text(item["measurement_id"], f"standards_snapshot.standard[{index}].measurement_id")
        _nonempty_text(item["metric"], f"standards_snapshot.standard[{index}].metric")
        if standard_id in identifiers:
            raise RunPackError("standards_snapshot standard IDs must be unique")
        identifiers.add(standard_id)
        if item["comparator"] not in {"<", "<=", ">", ">=", "=="}:
            raise RunPackError(f"standards_snapshot.standard[{index}] has an invalid comparator")
        if item["aggregation"] not in {"ALL", "ANY", "MEAN"}:
            raise RunPackError(f"standards_snapshot.standard[{index}] has an invalid aggregation")
        _finite_number(item["threshold_value"], f"standards_snapshot.standard[{index}].threshold_value")
        dwell = _finite_number(item["dwell_s"], f"standards_snapshot.standard[{index}].dwell_s")
        timeout = _finite_number(item["timeout_s"], f"standards_snapshot.standard[{index}].timeout_s", positive=True)
        if dwell < 0 or timeout <= dwell:
            raise RunPackError(f"standards_snapshot.standard[{index}] has an invalid dwell/timeout")
    return value


def _validate_measurement_catalog(catalog: Mapping[str, Any], robot: str, configuration: str) -> dict[str, Any]:
    value = _as_object(catalog, "measurement_catalog")
    if value.get("artifact_type") != "measurement_catalog" or value.get("schema_version") != SCHEMA_VERSION:
        raise RunPackError("measurement_catalog identity is invalid")
    if value.get("robot_model_id") != robot or value.get("robot_configuration_id") != configuration or value.get("intended_use") != "capability_validation_b":
        raise RunPackError("reviewed measurement_catalog robot, configuration, or intended use is invalid")
    if value.get("truth_policy") != {
        "candidate_self_report": "FORBIDDEN_AS_TRUTH",
        "sdk_receipt": "FORBIDDEN_AS_TRUTH",
    }:
        raise RunPackError("measurement_catalog truth policy is not fail-closed")
    adapters = value.get("adapters")
    if not isinstance(adapters, list) or not adapters:
        raise RunPackError("measurement_catalog adapters must be non-empty")
    adapter_ids: set[str] = set()
    for adapter in adapters:
        if not isinstance(adapter, Mapping) or set(adapter) != {"adapter_id", "owner", "truth_source", "session_task"}:
            raise RunPackError("measurement_catalog adapters must be closed session bindings")
        adapter_id = _nonempty_text(adapter.get("adapter_id"), "measurement adapter_id")
        if adapter_id in adapter_ids:
            raise RunPackError("measurement_catalog adapter IDs must be unique")
        adapter_ids.add(adapter_id)
        if adapter["owner"] != "trusted_validation_harness" or adapter["truth_source"] != "trusted_harness_physical_state" or adapter["session_task"] != MEASUREMENT_SESSION_TASK:
            raise RunPackError("measurement catalog adapters must be Harness-owned physical truth")
    measurements = value.get("measurements")
    if not isinstance(measurements, list) or not measurements:
        raise RunPackError("measurement_catalog measurements must be non-empty")
    measurement_ids: set[str] = set()
    for measurement in measurements:
        if not isinstance(measurement, Mapping):
            raise RunPackError("measurement_catalog measurement must be an object")
        for field in ("measurement_id", "entity", "unit", "frame", "adapter_id", "truth_source"):
            _nonempty_text(measurement.get(field), f"measurement.{field}")
        if measurement["measurement_id"] in measurement_ids:
            raise RunPackError("measurement_catalog measurement IDs must be unique")
        measurement_ids.add(measurement["measurement_id"])
        if measurement["adapter_id"] not in adapter_ids or measurement["truth_source"] != "trusted_harness_physical_state":
            raise RunPackError("measurement catalog measurement has an unbound truth adapter")
        if not isinstance(measurement.get("metrics"), list) or not measurement["metrics"] or not all(isinstance(metric, str) and metric.strip() for metric in measurement["metrics"]):
            raise RunPackError("measurement catalog measurement metrics must be non-empty text")
    guards = value.get("guards")
    if not isinstance(guards, list) or not guards:
        raise RunPackError("measurement_catalog guards must be non-empty")
    guard_ids: set[str] = set()
    for guard in guards:
        if not isinstance(guard, Mapping) or set(guard) != {"guard_id", "adapter_id"}:
            raise RunPackError("measurement_catalog guards must be closed")
        if guard["guard_id"] in guard_ids or guard["adapter_id"] not in adapter_ids:
            raise RunPackError("measurement_catalog guard is duplicated or unbound")
        guard_ids.add(guard["guard_id"])
    return value


def _load_reviewed_blue_inputs(
    root: Path,
    standards_snapshot_path: str | Path | None,
    measurement_catalog_path: str | Path | None,
    robot: str,
    configuration: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], dict[str, str]]:
    standards_path = _resolve_input(root, standards_snapshot_path, None, "reviewed_standards_snapshot_path")
    measurement_path = _resolve_input(root, measurement_catalog_path, None, "reviewed_measurement_catalog_path")
    try:
        standards_artifact = load_json_artifact(standards_path)
        measurement_artifact = load_json_artifact(measurement_path)
    except (OSError, ContractError) as exc:
        raise RunPackError("reviewed Blue Line inputs must be valid JSON artifacts") from exc
    standards = _validate_standards(standards_artifact.value, robot, configuration)
    measurement_catalog = _validate_measurement_catalog(measurement_artifact.value, robot, configuration)
    measurement_ids = {item["measurement_id"] for item in measurement_catalog["measurements"]}
    if any(item["measurement_id"] not in measurement_ids for item in standards["standards"]):
        raise RunPackError("standards_snapshot references a measurement absent from the reviewed catalog")
    return (
        standards,
        measurement_catalog,
        _relative_artifact_ref(root, standards_artifact),
        _relative_artifact_ref(root, measurement_artifact),
    )


def _validate_blue_line_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    value = _as_object(policy, "blue_line_policy")
    if set(value) != {"policy_id", "model_id", "prompt_id", "max_cases_per_capability", "repetitions"}:
        raise RunPackError("blue_line_policy must match the current Blue Line runner contract")
    if value["model_id"] != DEFAULT_MODEL or value["max_cases_per_capability"] != 8 or value["repetitions"] != 1:
        raise RunPackError("blue_line_policy is not the frozen first G2 policy")
    return value


def _validate_validation_a_template(template: Mapping[str, Any], robot: str) -> dict[str, Any]:
    value = _as_object(template, "validation_a_template")
    required_fields = {
        "artifact_type", "schema_version", "profile_id", "robot_model_id",
        "robot_configuration_id", "facade", "probe",
    }
    if set(value) != required_fields:
        raise RunPackError("validation_a_template must be a closed capability-independent template")
    if value.get("artifact_type") != "validation_a_template" or value.get("schema_version") != SCHEMA_VERSION:
        raise RunPackError("validation_a_template identity is invalid")
    configuration = "so-arm101-follower-stock-gripper" if robot == "so-arm101" else "unitree-go2-stock-12dof"
    if value.get("robot_model_id") != robot or value.get("robot_configuration_id") != configuration or value.get("profile_id") != "experimental-python-a-v1":
        raise RunPackError("validation_a_template is bound to the wrong robot or profile")
    facade = value.get("facade")
    probe = value.get("probe")
    if not isinstance(facade, Mapping) or set(facade) != {"members", "lifecycle"}:
        raise RunPackError("validation_a_template facade is invalid")
    if not isinstance(facade["members"], list) or not facade["members"] or not all(isinstance(member, str) and member.strip() and "__" not in member for member in facade["members"]):
        raise RunPackError("validation_a_template facade members are invalid")
    if any(member.lower() in {"constructor", "construct", "new"} for member in facade["members"]):
        raise RunPackError("Validation A template must not expose constructors as named candidate operations")
    if not isinstance(facade["lifecycle"], str) or not facade["lifecycle"].strip():
        raise RunPackError("validation_a_template facade lifecycle is invalid")
    if not isinstance(probe, Mapping) or set(probe) != {"input_value_policy", "metadata_policy"}:
        raise RunPackError("validation_a_template probe is invalid")
    if set(probe["input_value_policy"]) != {"number", "integer", "boolean", "string", "object", "array"}:
        raise RunPackError("validation_a_template probe value policy is incomplete")
    if probe["metadata_policy"] != "copy sealed design field metadata exactly":
        raise RunPackError("validation_a_template metadata policy is invalid")
    _recursive_forbidden(
        value,
        ("criterion", "private", "secret", "api_key", "capability_id"),
        reject_string_values=True,
    )
    return value


def _validate_harness_config(config: Mapping[str, Any], robot: str) -> dict[str, Any]:
    value = _as_object(config, "validation_harness_config")
    if value.get("artifact_type") != "validation_harness_config" or value.get("schema_version") != SCHEMA_VERSION:
        raise RunPackError("validation_harness_config identity is invalid")
    configuration = "so-arm101-follower-stock-gripper" if robot == "so-arm101" else "unitree-go2-stock-12dof"
    if value.get("robot_model_id") != robot or value.get("robot_configuration_id") != configuration:
        raise RunPackError("validation_harness_config robot is invalid")
    if value.get("truth_source") != "trusted_harness_physical_state":
        raise RunPackError("validation_harness_config must use trusted Harness physical state")
    if value.get("candidate_self_report_is_truth") is not False or value.get("sdk_receipt_is_truth") is not False:
        raise RunPackError("validation_harness_config truth guards are invalid")
    if value.get("video_profile") != FROZEN_VIDEO_PROFILE:
        raise RunPackError("validation_harness_config video profile is not frozen")
    return value


def _public_effects(robot: str) -> list[str]:
    return copy.deepcopy(PUBLIC_EFFECT_ALLOWLIST[robot])


def _implementation_projection_from_records(
    robot: str,
    morphology: Mapping[str, Any],
    sdk: Mapping[str, Any],
    translation: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    topology = copy.deepcopy(dict(projection["topology"]))
    robot_facts = {
        "robot_model_id": robot,
        "robot_configuration_id": morphology["robot_configuration_id"],
        "base_type": morphology["base_type"],
        "actuated_dof": morphology["actuated_dof"],
        "topology": topology,
        "sensors": copy.deepcopy(projection["sensors"]),
        "observations": copy.deepcopy(projection["observation_affordances"]),
        "units": copy.deepcopy(projection["sdk_facts"]["units"]),
        "frames": copy.deepcopy(projection["frames"]),
        "ranges": copy.deepcopy(projection["ranges"]),
        "limits": copy.deepcopy(projection["limits"]),
        "effect_allowlist": _public_effects(robot),
        "unsupported_behavior": copy.deepcopy(projection["unsupported_behavior"]),
    }
    if robot == "so-arm101":
        implementation = translation.get("implementation")
        if not isinstance(implementation, Mapping):
            raise RunPackError("SO checked-in translation implementation facts are missing")
        sdk_projection = {
            "sdk_entry_id": sdk["id"],
            "sdk_entry_version": sdk["version"],
            "permitted_types": copy.deepcopy(sdk["public_symbols"]),
            "permitted_operations": copy.deepcopy(sdk["operations"]),
            "action_fields": copy.deepcopy(sdk["action_fields"]),
            "observation_fields": copy.deepcopy(sdk["observation_fields"]),
            "field_order": copy.deepcopy(sdk["action_fields"]),
            "units": copy.deepcopy(sdk["units"]),
            "lifecycle": ["connect", "send_action", "get_observation", "disconnect"],
            "transport_constraints": {
                "normal_motion_writes": translation["normal_motion_writes"],
                "reset_write_scope": translation["direct_qpos_qvel_write"],
                "latest_valid_action": translation["command_rule"],
                "invalid_input": translation["error_rule"],
            },
            "unsupported_behavior": list(dict.fromkeys([*sdk["excluded"], *implementation["forbidden_behavior"]])),
        }
        return {
            "sdk_implementation_projection": sdk_projection,
            "robot_implementation_facts": robot_facts,
        }

    active_rule = translation.get("active_slot_rule")
    inactive_rule = translation.get("inactive_slot_rule")
    observation_mapping = translation.get("observation_mapping")
    if not isinstance(active_rule, Mapping) or not isinstance(inactive_rule, Mapping) or not isinstance(observation_mapping, Mapping):
        raise RunPackError("Go2 checked-in translation field rules are missing")
    sdk_projection = {
        "sdk_entry_id": sdk["id"],
        "sdk_entry_version": sdk["version"],
        "permitted_types": copy.deepcopy(sdk["public_symbols"]),
        "permitted_objects": [
            {"object_type": "ChannelPublisher", "operations": ["Init", "Write"]},
            {"object_type": "ChannelSubscriber", "operations": ["Init", "Read"]},
        ],
        "topics": copy.deepcopy(sdk["topics"]),
        "command": {
            "message_type": translation["dds"]["message_type"],
            "slot_count": sdk["motor_slot_count"],
            "active_slot_count": sdk["active_motor_count"],
            "active_indices": copy.deepcopy(active_rule["indices"]),
            "field_order": ["mode", "q", "dq", "kp", "kd", "tau"],
            "fields": [
                {"name": "mode", "unit": "enum"},
                {"name": "q", "unit": "rad"},
                {"name": "dq", "unit": "rad/s"},
                {"name": "kp", "unit": "position_gain"},
                {"name": "kd", "unit": "velocity_gain"},
                {"name": "tau", "unit": "torque"},
            ],
            "active_mode": active_rule["mode"],
            "finite_fields": copy.deepcopy(active_rule["finite_fields"]),
            "inactive_slot_rule": copy.deepcopy(inactive_rule),
        },
        "observation": {
            "message_type": "LowState_",
            "slot_count": sdk["motor_slot_count"],
            "active_slot_count": sdk["active_motor_count"],
            "field_order": ["q", "dq", "tau_est", "imu_quaternion", "gyroscope", "accelerometer"],
            "fields": [
                {"name": "q", "unit": "rad"},
                {"name": "dq", "unit": "rad/s"},
                {"name": "tau_est", "unit": "torque"},
                {"name": "imu_quaternion", "unit": "unitless"},
                {"name": "gyroscope", "unit": "rad/s"},
                {"name": "accelerometer", "unit": "m/s^2"},
            ],
            "sport_state": {
                "message_type": "SportModeState_",
                "field_order": ["frame_position", "frame_linear_velocity"],
                "fields": [
                    {"name": "frame_position", "unit": "m"},
                    {"name": "frame_linear_velocity", "unit": "m/s"},
                ],
            },
            "record_mapping": copy.deepcopy(observation_mapping),
        },
        "lifecycle": [
            "ChannelFactoryInitialize",
            "ChannelPublisher.Init",
            "ChannelSubscriber.Init",
            "ChannelPublisher.Write",
            "ChannelSubscriber.Read",
        ],
        "route_constraints": {
            "normal_motion_writes": translation["normal_motion_writes"],
            "reset_write_scope": translation["direct_qpos_qvel_write"],
            "stale_rule": copy.deepcopy(translation["stale_rule"]),
            "rejection_rule": translation["rejection_rule"],
        },
        "unsupported_behavior": list(dict.fromkeys([*sdk["excluded"], *translation["forbidden_behavior"]])),
    }
    return {
        "sdk_implementation_projection": sdk_projection,
        "robot_implementation_facts": robot_facts,
    }


def _validate_implementation_bundle_contents(bundle: Mapping[str, Any], robot: str) -> None:
    try:
        sdk_projection = bundle["sdk_implementation_projection"]
        robot_facts = bundle["robot_implementation_facts"]
        experience = bundle["implementation_experience"]
    except KeyError as exc:
        raise RunPackError("Implementation Bundle has an empty mandatory section") from exc
    if not isinstance(sdk_projection, Mapping) or not sdk_projection or not isinstance(robot_facts, Mapping) or not robot_facts or not isinstance(experience, list) or not experience:
        raise RunPackError("Implementation Bundle mandatory sections must be non-empty")
    for field in ("sdk_entry_id", "sdk_entry_version", "permitted_types", "lifecycle", "unsupported_behavior"):
        if field not in sdk_projection or not sdk_projection[field]:
            raise RunPackError(f"Implementation Bundle SDK fact section {field} is empty")
    for field in ("robot_model_id", "robot_configuration_id", "topology", "sensors", "units", "frames", "limits", "effect_allowlist", "unsupported_behavior"):
        if field not in robot_facts or not robot_facts[field]:
            raise RunPackError(f"Implementation Bundle robot fact section {field} is empty")
    if robot_facts["effect_allowlist"] != PUBLIC_EFFECT_ALLOWLIST[robot]:
        raise RunPackError("Implementation Bundle effect_allowlist does not match the public projection")


def _build_implementation_bundle(
    robot: str,
    morphology: Mapping[str, Any],
    sdk: Mapping[str, Any],
    translation: Mapping[str, Any],
    projection: Mapping[str, Any],
    template: Mapping[str, Any],
) -> dict[str, Any]:
    derived = _implementation_projection_from_records(robot, morphology, sdk, translation, projection)
    configured = _as_object(template["implementation_projection"], "implementation_projection")
    if configured != derived:
        raise RunPackError("robot implementation projection does not exactly match checked-in SDK/morphology/route facts")
    bundle = {
        "artifact_type": "stage2_implementation_bundle",
        "schema_version": SCHEMA_VERSION,
        "sdk_implementation_projection": derived["sdk_implementation_projection"],
        "robot_implementation_facts": derived["robot_implementation_facts"],
        "implementation_experience": copy.deepcopy(template["implementation_experience"]),
    }
    _validate_implementation_bundle_contents(bundle, robot)
    try:
        validate_implementation_bundle(bundle)
    except ContractError as exc:
        raise RunPackError("derived Implementation Bundle does not satisfy the closed validator") from exc
    return bundle


def _probe_value(field: Mapping[str, Any], policy: Mapping[str, Any]) -> Any:
    field_type = field.get("type")
    shape = field.get("shape")
    if field_type == "number":
        if isinstance(shape, str) and shape.startswith("vector:") and shape.removeprefix("vector:").isdigit():
            return [copy.deepcopy(policy["number"])] * int(shape.removeprefix("vector:"))
        return copy.deepcopy(policy["number"])
    if field_type in {"integer", "boolean", "string", "object", "array"} and shape == "scalar":
        return copy.deepcopy(policy[field_type])
    if field_type == "object" and shape == "mapping":
        return copy.deepcopy(policy["object"])
    if field_type == "array" and shape == "vector":
        return copy.deepcopy(policy["array"])
    raise RunPackError(f"sealed Stage 1 field cannot be materialized by Validation A: {field}")


def materialize_validation_a_profile(
    validation_a_template: Mapping[str, Any],
    capability_design: Mapping[str, Any],
    design_seal: Mapping[str, Any] | None = None,
) -> ValidationAProfile:
    """Materialize capability-keyed Validation-A entries after a valid Design seal."""

    template = _as_object(validation_a_template, "validation_a_template")
    robot = template.get("robot_model_id")
    if robot not in ROBOT_ALIASES.values():
        raise RunPackError("validation_a_template has an invalid robot identity")
    template = _validate_validation_a_template(template, str(robot))
    design = _as_object(capability_design, "capability_design")
    if design_seal is None:
        raise RunPackError("Validation A materialization requires the exact Design seal")
    design_hash = content_hash(canonical_bytes(design))
    try:
        valid_seal = verify_seal(dict(design_seal))
    except Exception as exc:
        raise RunPackError("Validation A materialization requires a valid Design seal") from exc
    if not valid_seal or design_seal.get("artifact_type") != "capability_design" or design_seal.get("artifact_hash") != design_hash:
        raise RunPackError("Validation A materialization requires the exact Design seal")
    issues = check_capability_design(design)
    if issues:
        raise RunPackError("Validation A materialization requires a conformed sealed Design")
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise RunPackError("sealed Capability Design must contain capabilities")
    facade_members = tuple(template["facade"]["members"])
    policy = template["probe"]["input_value_policy"]
    members: dict[str, tuple[str, ...]] = {}
    probes: dict[str, dict[str, Any]] = {}
    capability_ids: set[str] = set()
    for capability in capabilities:
        if not isinstance(capability, Mapping) or not isinstance(capability.get("capability_id"), str) or not capability["capability_id"].strip():
            raise RunPackError("sealed Capability Design has an invalid capability identity")
        capability_id = capability["capability_id"]
        if capability_id in capability_ids:
            raise RunPackError(f"sealed Capability Design duplicates capability {capability_id}")
        capability_ids.add(capability_id)
        fields = capability.get("inputs")
        if not isinstance(fields, list):
            raise RunPackError(f"sealed capability {capability_id} has no input fields")
        inputs: dict[str, dict[str, Any]] = {}
        for field in fields:
            if not isinstance(field, Mapping) or set(field) != {"name", "type", "shape", "unit", "frame", "required"}:
                raise RunPackError(f"sealed capability {capability_id} has malformed input metadata")
            name = _nonempty_text(field["name"], f"sealed capability {capability_id} input name")
            inputs[name] = {
                "value": _probe_value(field, policy),
                "type": field["type"],
                "shape": field["shape"],
                "unit": field["unit"],
                "frame": field["frame"],
            }
        members[capability_id] = facade_members
        probes[capability_id] = {"inputs": inputs}
    return ValidationAProfile(
        sdk_facade_members=members,
        fixture_probes=probes,
        profile_id=template["profile_id"],
    )


@dataclass(frozen=True)
class FirstG2RunPack:
    """Paths and public/private preparation views produced for one run."""

    root: Path
    output_dir: Path
    run_id: str
    robot_model_id: str
    artifact_paths: Mapping[str, Path]
    artifact_refs: Mapping[str, Mapping[str, str]]
    integration_manifest_ref: Mapping[str, str]
    readiness_report_ref: Mapping[str, str]
    run_snapshot_ref: Mapping[str, str]
    blue_line_input_refs: tuple[Mapping[str, str], ...]
    stage1_task_projection: tuple[Mapping[str, str], ...]
    task_set: Mapping[str, Any]
    task_instances: Mapping[str, Any]
    robot_projection: Mapping[str, Any]

    @property
    def run_snapshot_path(self) -> Path:
        return self.artifact_paths["run_snapshot"]

    @property
    def task_set_path(self) -> Path:
        return self.artifact_paths["task_set"]

    def path(self, artifact: str) -> Path:
        return self.artifact_paths[artifact]

    def __getitem__(self, key: str) -> Any:
        if key in self.artifact_paths:
            return self.artifact_paths[key]
        if key in self.artifact_refs:
            return self.artifact_refs[key]
        raise KeyError(key)


def build_first_g2_run_pack(
    root: str | Path,
    run_id: str,
    robot: str,
    *,
    integration_manifest_path: str | Path | None = None,
    readiness_report_path: str | Path | None = None,
    reviewed_standards_snapshot_path: str | Path | None = None,
    reviewed_measurement_catalog_path: str | Path | None = None,
    standards_snapshot_path: str | Path | None = None,
    measurement_catalog_path: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> FirstG2RunPack:
    """Build one deterministic SO-ARM101 or Go2 first-G2 preparation pack."""

    project_root = Path(root).resolve()
    if not project_root.is_dir():
        raise RunPackError("project root must be an existing directory")
    exact_robot = _normalize_robot(robot)
    selected_run_id = _nonempty_text(run_id, "run_id")
    if Path(selected_run_id).name != selected_run_id or selected_run_id in {".", ".."}:
        raise RunPackError("run_id must be one path-safe identifier")
    configuration = "so-arm101-follower-stock-gripper" if exact_robot == "so-arm101" else "unitree-go2-stock-12dof"
    if reviewed_standards_snapshot_path is not None and standards_snapshot_path is not None and Path(reviewed_standards_snapshot_path) != Path(standards_snapshot_path):
        raise RunPackError("standards snapshot path was supplied twice with different values")
    if reviewed_measurement_catalog_path is not None and measurement_catalog_path is not None and Path(reviewed_measurement_catalog_path) != Path(measurement_catalog_path):
        raise RunPackError("measurement catalog path was supplied twice with different values")
    standards_input = reviewed_standards_snapshot_path if reviewed_standards_snapshot_path is not None else standards_snapshot_path
    measurement_input = reviewed_measurement_catalog_path if reviewed_measurement_catalog_path is not None else measurement_catalog_path

    manifest_path = _resolve_input(project_root, integration_manifest_path, DEFAULT_MANIFESTS[exact_robot], "integration_manifest_path")
    report_path = _resolve_input(project_root, readiness_report_path, None, "readiness_report_path")
    manifest = load_integration_manifest(manifest_path)
    report = load_readiness_report(report_path)
    if manifest.value["robot_model_id"] != exact_robot or manifest.value["robot_configuration_id"] != configuration:
        raise RunPackError("selected integration manifest is for a different first-Demo robot")
    if manifest.value["status"] != "READY":
        raise RunPackError(f"{exact_robot} integration manifest is {manifest.value['status']}; a READY manifest is required")
    if report.value["run_id"] != selected_run_id:
        raise RunPackError("readiness report run_id must exactly equal the requested run_id")
    if report.value["verdict"] != "PASS" or any(item["verdict"] != "PASS" for item in report.value["checks"]) or report.value["cleanup"]["verdict"] != "PASS":
        raise RunPackError("readiness report must be PASS with six PASS checks and PASS cleanup")
    manifest_ref = _relative_artifact_ref(project_root, manifest)
    report_ref = _relative_artifact_ref(project_root, report)
    if report.value["integration_manifest_ref"] != manifest_ref:
        raise RunPackError("readiness report does not bind the exact supplied integration manifest")
    if report.value["readiness_profile_ref"] != manifest.value.get("readiness_profile_ref"):
        raise RunPackError("readiness report profile reference does not match the manifest")
    profile_path = verify_file_reference(project_root, manifest.value["readiness_profile_ref"])
    validate_readiness_profile(load_json_artifact(profile_path).value)

    morphology_path = verify_file_reference(project_root, manifest.value["morphology_ref"])
    sdk_path = verify_file_reference(project_root, manifest.value["sdk_ref"])
    translation_path = verify_file_reference(project_root, manifest.value["translation_ref"])
    morphology = load_json_artifact(morphology_path).value
    sdk = load_json_artifact(sdk_path).value
    translation = load_json_artifact(translation_path).value
    template = _validate_robot_template(_load_template(project_root, _robot_template_path(exact_robot), f"{exact_robot} robot"), exact_robot, configuration)
    g2_contract_profile = _load_template(project_root, G2_PROFILE_RELATIVE_PATH, "G2 profile")
    if stable_json_sha256(g2_contract_profile) != G2_PROFILE_STABLE_SHA256:
        raise RunPackError("the selected G2 profile is not the exact frozen profile")
    g2_profile = copy.deepcopy(G2_PROFILE_PUBLIC)
    model_config = _validate_model_config(_load_template(project_root, "general_demo/config/first_g2_demo/model_prompt_config.json", "model"))
    budget = _validate_budget(_load_template(project_root, "general_demo/config/first_g2_demo/budget.json", "budget"))
    standards, measurement_catalog, standards_ref, measurement_ref = _load_reviewed_blue_inputs(project_root, standards_input, measurement_input, exact_robot, configuration)
    policy = _validate_blue_line_policy(template["blue_line_policy"])
    validation_a_template = _validate_validation_a_template(template["validation_a_template"], exact_robot)
    harness_config = _validate_harness_config(template["validation_harness_config"], exact_robot)
    robot_projection = _build_robot_projection(manifest.value, morphology, sdk, template)
    task_set, stage1_tasks, task_instances = _build_task_set(project_root, selected_run_id, configuration, template)
    implementation_bundle = _build_implementation_bundle(exact_robot, morphology, sdk, translation, robot_projection, template)

    if output_dir is None:
        output_path = project_root / "general_demo" / "runs" / selected_run_id
    else:
        output_path = Path(output_dir)
        if not output_path.is_absolute():
            output_path = project_root / output_path
        output_path = output_path.resolve()
    try:
        output_path.relative_to(project_root)
    except ValueError as exc:
        raise RunPackError("output_dir must be beneath the project root") from exc
    output_path.mkdir(parents=True, exist_ok=True)

    values: dict[str, Any] = {
        "task_set": task_set,
        "task_instances": task_instances,
        "g2_profile": g2_profile,
        "robot_projection": robot_projection,
        "model_prompt_config": model_config,
        "budget": budget,
        "standards_snapshot": standards,
        "measurement_catalog": measurement_catalog,
        "blue_line_policy": policy,
        "implementation_bundle": implementation_bundle,
        "validation_a_template": validation_a_template,
        "validation_harness_config": harness_config,
    }
    names = {
        "task_set": "task_set.json",
        "task_instances": "task_instances.json",
        "g2_profile": "g2_profile.json",
        "robot_projection": "robot_projection.json",
        "model_prompt_config": "model_prompt_config.json",
        "budget": "budget.json",
        "standards_snapshot": "standards_snapshot.json",
        "measurement_catalog": "measurement_catalog.json",
        "blue_line_policy": "blue_line_policy.json",
        "implementation_bundle": "implementation_bundle.json",
        "validation_a_template": "validation_a_template.json",
        "validation_harness_config": "validation_harness_config.json",
    }
    artifact_paths: dict[str, Path] = {}
    artifact_refs: dict[str, Mapping[str, str]] = {}
    for key, value in values.items():
        path, reference = _json_file(project_root, (output_path / names[key]).resolve().relative_to(project_root).as_posix(), value)
        artifact_paths[key] = path
        artifact_refs[key] = reference

    runtime_sha256 = stable_json_sha256(manifest.value["runtime"])
    blue_line_input_refs = (
        standards_ref,
        measurement_ref,
        artifact_refs["blue_line_policy"],
    )
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "run_id": selected_run_id,
        "integration_manifest_ref": manifest_ref,
        "readiness_report_ref": report_ref,
        "runtime_sha256": runtime_sha256,
        "readiness_profile_ref": copy.deepcopy(manifest.value["readiness_profile_ref"]),
        "library_view_refs": [
            artifact_refs["implementation_bundle"],
            artifact_refs["validation_a_template"],
            artifact_refs["validation_harness_config"],
            artifact_refs["task_instances"],
        ],
        "task_set_ref": artifact_refs["task_set"],
        "g2_profile_ref": artifact_refs["g2_profile"],
        "observation_profile_ref": artifact_refs["robot_projection"],
        "model_prompt_config_ref": artifact_refs["model_prompt_config"],
        "budget_ref": artifact_refs["budget"],
        "blue_line_input_refs": [copy.deepcopy(item) for item in blue_line_input_refs],
        "sealed_artifact_refs": [],
    }
    snapshot_path, snapshot_ref = _json_file(project_root, (output_path / "run_snapshot.json").resolve().relative_to(project_root).as_posix(), snapshot)
    artifact_paths["run_snapshot"] = snapshot_path
    artifact_refs["run_snapshot"] = snapshot_ref

    try:
        ExperimentIntegrationGate(project_root).verify(manifest_path, snapshot_path, report_path)
    except (GateError, ContractError, IntegrityError) as exc:
        raise RunPackError(f"pre-Stage-1 gate rejected the prepared run pack: {exc}") from exc

    return FirstG2RunPack(
        root=project_root,
        output_dir=output_path,
        run_id=selected_run_id,
        robot_model_id=exact_robot,
        artifact_paths=artifact_paths,
        artifact_refs=artifact_refs,
        integration_manifest_ref=manifest_ref,
        readiness_report_ref=report_ref,
        run_snapshot_ref=snapshot_ref,
        blue_line_input_refs=tuple(copy.deepcopy(item) for item in blue_line_input_refs),
        stage1_task_projection=tuple(copy.deepcopy(stage1_tasks)),
        task_set=task_set,
        task_instances=task_instances,
        robot_projection=robot_projection,
    )


def finalize_first_g2_run_snapshot(
    root: str | Path,
    run_snapshot_path: str | Path,
    sealed_artifact_refs: list[Mapping[str, str]] | tuple[Mapping[str, str], ...],
    *,
    output_path: str | Path | None = None,
) -> JsonArtifact:
    """Create a new finalized snapshot bound to sealed artifacts without overwriting."""

    project_root = Path(root).resolve()
    source = Path(run_snapshot_path)
    if not source.is_absolute():
        source = project_root / source
    try:
        source = source.resolve(strict=True)
        source.relative_to(project_root)
    except (OSError, ValueError) as exc:
        raise RunPackError("run_snapshot_path must be an existing file beneath the project root") from exc
    try:
        snapshot_artifact = load_json_artifact(source)
        from ..integration.artifacts import validate_run_snapshot
        validate_run_snapshot(snapshot_artifact.value)
    except (OSError, ContractError) as exc:
        raise RunPackError("run_snapshot_path is not a valid run snapshot") from exc
    snapshot = snapshot_artifact.value
    for field in (
        "integration_manifest_ref", "readiness_report_ref", "readiness_profile_ref", "task_set_ref",
        "g2_profile_ref", "observation_profile_ref", "model_prompt_config_ref", "budget_ref",
    ):
        try:
            verify_file_reference(project_root, snapshot[field])
        except (IntegrityError, ContractError) as exc:
            raise RunPackError(f"run snapshot frozen reference {field} is invalid") from exc
    for field in ("library_view_refs", "blue_line_input_refs", "sealed_artifact_refs"):
        for reference in snapshot[field]:
            try:
                verify_file_reference(project_root, reference)
            except (IntegrityError, ContractError) as exc:
                raise RunPackError(f"run snapshot frozen reference in {field} is invalid") from exc
    if not isinstance(sealed_artifact_refs, (list, tuple)) or not sealed_artifact_refs:
        raise RunPackError("finalized snapshot requires at least one sealed artifact reference")
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for reference in sealed_artifact_refs:
        if not isinstance(reference, Mapping) or set(reference) != {"path", "sha256"}:
            raise RunPackError("sealed_artifact_refs must contain exact {path,sha256} objects")
        ref = {"path": str(reference["path"]), "sha256": str(reference["sha256"])}
        key = (ref["path"], ref["sha256"])
        if key in seen:
            raise RunPackError("sealed_artifact_refs must be unique")
        seen.add(key)
        try:
            verify_file_reference(project_root, ref)
        except (IntegrityError, ContractError) as exc:
            raise RunPackError("sealed artifact reference is not an exact existing file reference") from exc
        normalized.append(ref)
    normalized.sort(key=lambda item: (item["path"], item["sha256"]))
    finalized = copy.deepcopy(snapshot)
    finalized["sealed_artifact_refs"] = normalized
    if output_path is None:
        destination = source.with_name("run_snapshot.finalized.json")
    else:
        destination = Path(output_path)
        if not destination.is_absolute():
            destination = project_root / destination
        destination = destination.resolve()
    if destination.resolve() == source.resolve():
        raise RunPackError("finalized snapshot must be a new file")
    return _write_new_json(project_root, destination, finalized)


finalize_run_snapshot = finalize_first_g2_run_snapshot
prepare_first_g2_run = build_first_g2_run_pack


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare one deterministic first-G2 formal run pack.")
    parser.add_argument("--root", default=None, help="project root (defaults to the repository root)")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--robot", required=True, choices=("so-arm101", "unitree-go2", "go2", "so"))
    parser.add_argument("--integration-manifest", default=None)
    parser.add_argument("--readiness-report", required=True)
    parser.add_argument("--standards-snapshot", required=True)
    parser.add_argument("--measurement-catalog", required=True)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[4]
    try:
        pack = build_first_g2_run_pack(
            root,
            args.run_id,
            args.robot,
            integration_manifest_path=args.integration_manifest,
            readiness_report_path=args.readiness_report,
            reviewed_standards_snapshot_path=args.standards_snapshot,
            reviewed_measurement_catalog_path=args.measurement_catalog,
            output_dir=args.output_dir,
        )
    except (ContractError, GateError, IntegrityError) as exc:
        parser.error(str(exc))
    print(json.dumps({key: str(path) for key, path in sorted(pack.artifact_paths.items())}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI smoke test
    if __package__ in {None, ""}:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    raise SystemExit(_main())
