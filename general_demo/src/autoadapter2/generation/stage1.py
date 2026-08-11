"""Small Stage-1 semantic-design loop with public diagnostics only."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash
from ..foundation.seals import create_seal
from .llm import JsonGenerator


STAGE1_PROMPT = (
    "Produce only the public semantic capability-design body. Do not include private "
    "criteria, implementation details, SDK signatures, Translation, MuJoCo, or evaluation data."
)
_PRIVATE_TERMS = (
    "private",
    "criterion",
    "mujoco",
    "translation",
    "candidate",
    "validation_result",
    "demo_result",
    "seed",
)


@dataclass(frozen=True)
class Stage1Config:
    max_correction_calls: int = 2

    def __post_init__(self) -> None:
        if not isinstance(self.max_correction_calls, int) or not 0 <= self.max_correction_calls <= 2:
            raise ContractError("Stage 1 correction calls must be between 0 and 2")


@dataclass(frozen=True)
class Stage1Result:
    status: str
    capability_design: dict[str, Any] | None
    design_hash: str | None
    seal: dict[str, Any] | None
    call_log: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, str], ...]


def _issues(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _private_issues(value: Any, location: str = "$", *, allow_private_inputs: bool = False) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if not allow_private_inputs and any(term in lowered for term in _PRIVATE_TERMS):
                issues.append(_issues("PRIVATE_FIELD", f"{location}.{key} is not public"))
            issues.extend(_private_issues(item, f"{location}.{key}", allow_private_inputs=allow_private_inputs))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issues.extend(_private_issues(item, f"{location}[{index}]", allow_private_inputs=allow_private_inputs))
    elif isinstance(value, str):
        lowered = value.lower()
        if any(term in lowered for term in ("private criterion", "mujoco", "translation layer")):
            issues.append(_issues("PRIVATE_CONTENT", f"{location} contains forbidden private content"))
    return issues


def _closed(value: Any, fields: set[str], location: str, issues: list[dict[str, str]]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        issues.append(_issues("TYPE", f"{location} must be an object"))
        return None
    missing = fields - set(value)
    extra = set(value) - fields
    if missing:
        issues.append(_issues("MISSING_FIELD", f"{location} missing {sorted(missing)}"))
    if extra:
        issues.append(_issues("EXTRA_FIELD", f"{location} has unexpected {sorted(extra)}"))
    return value


def _id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_semantic_fields(
    value: Any,
    location: str,
    issues: list[dict[str, str]],
    allowed_units: set[str] | None,
    allowed_frames: set[str] | None,
) -> None:
    if not isinstance(value, list):
        issues.append(_issues("PUBLIC_INTERFACE", f"{location} must be an array"))
        return
    names: set[str] = set()
    for index, item in enumerate(value):
        field = _closed(item, {"name", "type", "shape", "unit", "frame", "required"}, f"{location}[{index}]", issues)
        if field is None:
            continue
        for name in ("name", "type", "shape", "unit", "frame"):
            if not _id(field.get(name)):
                issues.append(_issues("PUBLIC_INTERFACE", f"{location}[{index}].{name} must be a non-empty string"))
        if not isinstance(field.get("required"), bool):
            issues.append(_issues("PUBLIC_INTERFACE", f"{location}[{index}].required must be boolean"))
        if isinstance(field.get("name"), str):
            if field["name"] in names:
                issues.append(_issues("DUPLICATE_PUBLIC_FIELD", f"{location} has duplicate {field['name']}"))
            names.add(field["name"])
        if allowed_units is not None and field.get("unit") not in allowed_units:
            issues.append(_issues("PUBLIC_UNIT", f"{location}[{index}].unit is not in the robot public allowlist"))
        if allowed_frames is not None and field.get("frame") not in allowed_frames:
            issues.append(_issues("PUBLIC_FRAME", f"{location}[{index}].frame is not in the robot public allowlist"))


def _string_array(value: Any, location: str, issues: list[dict[str, str]]) -> None:
    if not isinstance(value, list) or not all(_id(item) for item in value):
        issues.append(_issues("SEMANTIC_FIELD", f"{location} must be a string array"))


def _check_capability(
    value: Any,
    location: str,
    supplied_requirement_ids: set[str],
    action_affordances: set[str] | None,
    observation_affordances: set[str] | None,
    allowed_units: set[str] | None,
    allowed_frames: set[str] | None,
    issues: list[dict[str, str]],
) -> set[str]:
    fields = {
        "capability_id", "kind", "requirement_ids", "inputs", "outputs", "effect",
        "preconditions", "invocation_semantics", "temporal_semantics", "invariants",
        "required_action_affordances", "required_observation_affordances", "errors",
        "unsupported_scope",
    }
    capability = _closed(value, fields, location, issues)
    if capability is None:
        return set()
    capability_id = capability.get("capability_id")
    if not _id(capability_id):
        issues.append(_issues("CAPABILITY_ID", f"{location}.capability_id must be non-empty"))
    requirements = capability.get("requirement_ids")
    covered: set[str] = set()
    if not isinstance(requirements, list) or not requirements or not all(_id(item) for item in requirements):
        issues.append(_issues("REQUIREMENT_IDS", f"{location}.requirement_ids must be a non-empty string array"))
    else:
        covered = set(requirements)
        if len(covered) != len(requirements):
            issues.append(_issues("DUPLICATE_REQUIREMENT", f"{location}.requirement_ids contains duplicates"))
        unknown = covered - supplied_requirement_ids
        if unknown:
            issues.append(_issues("UNKNOWN_REQUIREMENT", f"{location} covers unknown requirements {sorted(unknown)}"))
    _check_semantic_fields(capability.get("inputs"), f"{location}.inputs", issues, allowed_units, allowed_frames)
    _check_semantic_fields(capability.get("outputs"), f"{location}.outputs", issues, allowed_units, allowed_frames)
    if not _id(capability.get("effect")):
        issues.append(_issues("SEMANTIC_EFFECT", f"{location}.effect must be a public semantic statement"))
    if capability.get("kind") not in {"action", "observation", "state_maintenance", "composition"}:
        issues.append(_issues("CAPABILITY_KIND", f"{location}.kind is invalid"))
    _string_array(capability.get("preconditions"), f"{location}.preconditions", issues)
    for field in ("invocation_semantics", "temporal_semantics"):
        if not _id(capability.get(field)):
            issues.append(_issues("SEMANTIC_FIELD", f"{location}.{field} must be a public statement"))
    for field in (
        "invariants", "required_action_affordances", "required_observation_affordances",
        "unsupported_scope",
    ):
        _string_array(capability.get(field), f"{location}.{field}", issues)
    for field, allowed in (
        ("required_action_affordances", action_affordances),
        ("required_observation_affordances", observation_affordances),
    ):
        values = capability.get(field)
        if allowed is not None and isinstance(values, list) and set(values) - allowed:
            issues.append(_issues("PUBLIC_AFFORDANCE", f"{location}.{field} exceeds the robot public projection"))
    errors = capability.get("errors")
    if not isinstance(errors, list) or not errors:
        issues.append(_issues("ERRORS", f"{location}.errors must be a non-empty array"))
    else:
        for index, error in enumerate(errors):
            item = _closed(error, {"code", "message"}, f"{location}.errors[{index}]", issues)
            if item and (not _id(item.get("code")) or not _id(item.get("message"))):
                issues.append(_issues("ERRORS", f"{location}.errors[{index}] must contain public code and message"))
    return covered


def check_capability_design(design: Mapping[str, Any]) -> list[dict[str, str]]:
    """Closed, deterministic conformance check for the demo semantic artifact."""

    issues: list[dict[str, str]] = _private_issues(design)
    fields = {
        "artifact_type", "schema_version", "run_id", "robot_public_projection",
        "granularity_profile", "task_requirement_ids", "capabilities",
        "unsupported_requirement_ids", "blocking_requirement_ids",
    }
    artifact = _closed(design, fields, "capability_design", issues)
    if artifact is None:
        return issues
    if artifact.get("artifact_type") != "capability_design" or artifact.get("schema_version") != "1.0.0":
        issues.append(_issues("ARTIFACT_IDENTITY", "capability design identity is invalid"))
    if not _id(artifact.get("run_id")):
        issues.append(_issues("RUN_ID", "capability design run_id is invalid"))
    projection = artifact.get("robot_public_projection")
    if not isinstance(projection, dict):
        issues.append(_issues("ROBOT_PROJECTION", "robot public projection must be an object"))
        projection = {}
    action_affordances = _projection_strings(projection, "action_affordances", issues, required=True)
    observation_affordances = _projection_strings(projection, "observation_affordances", issues, required=True)
    allowed_units = _projection_allowlist(projection, "unit_allowlist", "units", issues)
    allowed_frames = _projection_allowlist(projection, "frame_allowlist", "frames", issues)
    profile = artifact.get("granularity_profile")
    if not isinstance(profile, dict) or profile != {"profile_id": "g2-reusable-effect", "version": "1.0.0", "granularity": "G2"}:
        issues.append(_issues("GRANULARITY", "exactly one frozen G2 profile is required"))
    requirements = artifact.get("task_requirement_ids")
    if not isinstance(requirements, list) or not requirements or not all(_id(item) for item in requirements):
        issues.append(_issues("TASK_REQUIREMENTS", "task_requirement_ids must be a non-empty string array"))
        supplied: set[str] = set()
    else:
        supplied = set(requirements)
        if len(supplied) != len(requirements):
            issues.append(_issues("DUPLICATE_REQUIREMENT", "task_requirement_ids contains duplicates"))
    capabilities = artifact.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        issues.append(_issues("CAPABILITIES", "capabilities must be a non-empty array"))
        capabilities = []
    capability_ids: set[str] = set()
    covered: set[str] = set()
    for index, capability in enumerate(capabilities):
        if isinstance(capability, dict) and isinstance(capability.get("capability_id"), str):
            if capability["capability_id"] in capability_ids:
                issues.append(_issues("DUPLICATE_CAPABILITY", f"duplicate capability_id {capability['capability_id']}"))
            capability_ids.add(capability["capability_id"])
        covered |= _check_capability(
            capability,
            f"capabilities[{index}]",
            supplied,
            action_affordances,
            observation_affordances,
            allowed_units,
            allowed_frames,
            issues,
        )
    declared: set[str] = set()
    for field in ("unsupported_requirement_ids", "blocking_requirement_ids"):
        values = artifact.get(field)
        if not isinstance(values, list) or not all(_id(item) for item in values):
            issues.append(_issues("REQUIREMENT_DISPOSITION", f"{field} must be a string array"))
            continue
        values_set = set(values)
        if len(values_set) != len(values) or values_set - supplied:
            issues.append(_issues("REQUIREMENT_DISPOSITION", f"{field} has duplicate or unknown IDs"))
        declared |= values_set
    if covered & declared:
        issues.append(_issues("REQUIREMENT_DISPOSITION", "a requirement cannot be both covered and unresolved"))
    if supplied and covered | declared != supplied:
        issues.append(_issues("REQUIREMENT_COVERAGE", "every supplied requirement must be covered, unsupported, or blocked"))
    return issues


def _projection_strings(
    projection: Mapping[str, Any],
    field: str,
    issues: list[dict[str, str]],
    *,
    required: bool,
) -> set[str] | None:
    value = projection.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, list) or not all(_id(item) for item in value):
        issues.append(_issues("ROBOT_PROJECTION", f"robot public projection {field} must be a string array"))
        return None
    return set(value)


def _projection_allowlist(
    projection: Mapping[str, Any],
    primary: str,
    legacy: str,
    issues: list[dict[str, str]],
) -> set[str] | None:
    if primary in projection and legacy in projection:
        issues.append(_issues("ROBOT_PROJECTION", f"robot public projection must not provide both {primary} and {legacy}"))
        return None
    field = primary if primary in projection else legacy
    return _projection_strings(projection, field, issues, required=False)


class Stage1Runner:
    def __init__(self, generator: JsonGenerator, config: Stage1Config = Stage1Config()):
        self.generator = generator
        self.config = config

    def run(
        self,
        run_id: str,
        robot_public_projection: Mapping[str, Any],
        task_descriptions: list[Mapping[str, Any]],
        g2_profile: Mapping[str, Any],
    ) -> Stage1Result:
        if not _id(run_id):
            raise ContractError("run_id must be a non-empty string")
        initial_inputs = {
            "run_id": run_id,
            "robot_public_projection": copy.deepcopy(dict(robot_public_projection)),
            "task_descriptions": copy.deepcopy([dict(task) for task in task_descriptions]),
            "g2_profile": copy.deepcopy(dict(g2_profile)),
        }
        private_input_issues = _private_issues(initial_inputs)
        if private_input_issues:
            raise ContractError(f"Stage 1 input is not public: {private_input_issues[0]['message']}")
        requirement_ids = self._requirements(initial_inputs["task_descriptions"])
        profile = self._profile(initial_inputs["g2_profile"])
        calls: list[dict[str, Any]] = []
        diagnostics: list[dict[str, str]] = []
        working: dict[str, Any] | None = None
        for attempt in range(self.config.max_correction_calls + 1):
            inputs: dict[str, Any] = dict(initial_inputs)
            if working is not None:
                inputs["working_design"] = working
                inputs["diagnostics"] = diagnostics
            output = self.generator.generate_json("stage1", STAGE1_PROMPT, inputs)
            allowed_output_fields = {
                "capabilities", "unsupported_requirement_ids", "blocking_requirement_ids",
            }
            output_issues: list[dict[str, str]] = []
            unexpected = set(output) - allowed_output_fields
            missing = allowed_output_fields - set(output)
            if unexpected:
                output_issues.append(_issues("MODEL_OUTPUT_FIELDS", f"model output has unexpected {sorted(unexpected)}"))
            if missing:
                output_issues.append(_issues("MODEL_OUTPUT_FIELDS", f"model output is missing {sorted(missing)}"))
            design = {
                "artifact_type": "capability_design",
                "schema_version": "1.0.0",
                "run_id": run_id,
                "robot_public_projection": initial_inputs["robot_public_projection"],
                "granularity_profile": profile,
                "task_requirement_ids": requirement_ids,
                "capabilities": output.get("capabilities"),
                "unsupported_requirement_ids": output.get("unsupported_requirement_ids"),
                "blocking_requirement_ids": output.get("blocking_requirement_ids"),
            }
            diagnostics = output_issues + check_capability_design(design)
            calls.append({
                "call": attempt + 1,
                "stage": "stage1",
                "input_hash": content_hash(canonical_bytes(inputs)),
                "output_hash": content_hash(canonical_bytes(output)),
                "diagnostics": copy.deepcopy(diagnostics),
            })
            if not diagnostics:
                design_hash = content_hash(canonical_bytes(design))
                return Stage1Result("SEALED", design, design_hash, create_seal("capability_design", design_hash), tuple(calls), ())
            working = output
        return Stage1Result("FAILED", None, None, None, tuple(calls), tuple(diagnostics))

    @staticmethod
    def _requirements(tasks: list[Mapping[str, Any]]) -> list[str]:
        if not all(set(task) == {"requirement_id", "description"} for task in tasks):
            raise ContractError("each task description may contain only requirement_id and description")
        requirement_ids = [task.get("requirement_id") for task in tasks]
        if not requirement_ids or not all(_id(item) for item in requirement_ids) or len(set(requirement_ids)) != len(requirement_ids):
            raise ContractError("every public task description needs one unique requirement_id")
        if not all(_id(task.get("description")) for task in tasks):
            raise ContractError("every public task description needs a description")
        return list(requirement_ids)

    @staticmethod
    def _profile(profile: Mapping[str, Any]) -> dict[str, str]:
        required = {"profile_id": "g2-reusable-effect", "version": "1.0.0", "granularity": "G2"}
        if any(profile.get(key) != value for key, value in required.items()):
            raise ContractError("Stage 1 requires frozen g2-reusable-effect@1.0.0")
        return required
