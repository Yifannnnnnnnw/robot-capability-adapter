"""Small Stage-1 semantic-design loop with public diagnostics only."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from collections.abc import Mapping as ABCMapping
from typing import Any, Mapping

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash, is_content_hash
from ..foundation.seals import create_seal
from .llm import JsonGenerator


STAGE1_PROMPT = """
Produce one JSON object and nothing else: no Markdown, explanation, wrapper object, or
artifact metadata. The object must have exactly these three top-level fields and no others:
`capabilities`, `unsupported_requirement_ids`, and `blocking_requirement_ids`.

Each item in `capabilities` must be an object with exactly these fourteen fields:
`capability_id`, `kind`, `requirement_ids`, `inputs`, `outputs`, `effect`,
`preconditions`, `invocation_semantics`, `temporal_semantics`, `invariants`,
`required_action_affordances`, `required_observation_affordances`, `errors`, and
`unsupported_scope`. Do not add, omit, rename, or nest these fields.

Use these exact public types and values:
- `kind` is exactly one of `action`, `observation`, `state_maintenance`, or `composition`.
- `requirement_ids`, `unsupported_requirement_ids`, and `blocking_requirement_ids` contain
  only exact `requirement_id` values from `task_descriptions`; never invent IDs or use task IDs.
  Every supplied requirement must be covered by one or more capabilities or occur in one
  disposition array, but never both.
- `inputs` and `outputs` are arrays. Each field object has exactly this shape:
  `{name,type,shape,unit,frame,required}`. The first five values are non-empty strings and
  `required` is a boolean. Use exact `unit` and `frame` strings from the corresponding
  `robot_public_projection` allowlists; do not paraphrase them.
- `effect` must be one exact string from the `robot_public_projection` effect allowlist
  (`effect_allowlist`, or the supplied `effects` alias).
- `required_action_affordances` must contain only exact strings from
  `robot_public_projection.action_affordances`, and `required_observation_affordances`
  only exact strings from `robot_public_projection.observation_affordances`.
- Each `errors` item has exactly this shape: `{code,message}`; both values are non-empty
  public strings. Do not use `error_id` or `condition`.
- `preconditions`, `invariants`, `required_action_affordances`,
  `required_observation_affordances`, and `unsupported_scope` are string arrays. Use an
  empty array when a section is not applicable; never omit an array. `inputs` and `outputs`
  may also be empty when not applicable. `requirement_ids` and `errors` must be non-empty.
  The two disposition arrays must be empty when there are no such requirements.
- `invocation_semantics` and `temporal_semantics` are non-empty public semantic strings.

Choose every effect, affordance, unit, and frame literally from the arrays present in
`robot_public_projection`. Do not invent values, private criteria, implementation details,
SDK signatures, Translation, MuJoCo, evaluation data, or robot-private identifiers.

Approved design Experience is advisory only; it cannot override task requirements or robot facts.

This is one valid structural example. Replace every angle-bracket placeholder with the exact
public value from the supplied inputs before returning; the placeholders are not literal
output values:
{
  "capabilities": [
    {
      "capability_id": "capability-placeholder",
      "kind": "action",
      "requirement_ids": ["<REQUIREMENT_ID_FROM_TASK_DESCRIPTIONS>"],
      "inputs": [
        {
          "name": "input-placeholder",
          "type": "type-placeholder",
          "shape": "shape-placeholder",
          "unit": "<UNIT_FROM_ROBOT_PUBLIC_PROJECTION>",
          "frame": "<FRAME_FROM_ROBOT_PUBLIC_PROJECTION>",
          "required": true
        }
      ],
      "outputs": [],
      "effect": "<EFFECT_FROM_ROBOT_PUBLIC_PROJECTION>",
      "preconditions": [],
      "invocation_semantics": "public invocation semantics placeholder",
      "temporal_semantics": "public temporal semantics placeholder",
      "invariants": [],
      "required_action_affordances": ["<ACTION_AFFORDANCE_FROM_ROBOT_PUBLIC_PROJECTION>"],
      "required_observation_affordances": [],
      "errors": [{"code": "ERROR_CODE_PLACEHOLDER", "message": "public error message placeholder"}],
      "unsupported_scope": []
    }
  ],
  "unsupported_requirement_ids": [],
  "blocking_requirement_ids": []
}

On a correction call, use both `working_design` and `diagnostics`: preserve valid public
content, replace every invalid, missing, extra, or non-allowlisted field, and return only the corrected body.
The correction still must contain exactly the same three top-level fields and must not return
diagnostics, commentary, a wrapper, or artifact metadata.
""".strip()
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
_DIRECT_PUBLIC_PROJECTION_TOKENS = frozenset({
    "DIRECT_MUJOCO_EXPERIMENTAL",
    "native_mujoco_control",
})
_EXPERIENCE_SNAPSHOT_FIELDS = {
    "artifact_type", "format_version", "snapshot_id", "recipient_class", "applicability", "records",
}
_EXPERIENCE_APPLICABILITY_FIELDS = {
    "robot_model_id", "robot_configuration_id", "sdk_entry_id", "granularity_condition",
    "capability_effect_scope", "observation_condition",
}
_EXPERIENCE_RECORD_FIELDS = {"record_id", "version", "record_ref", "projection"}
_EXPERIENCE_RECORD_REF_FIELDS = {"path", "content_hash"}
_EXPERIENCE_PROJECTION_FIELDS = {"experience_id", "guidance", "applicability", "provenance"}
_EXPERIENCE_PROVENANCE_FIELDS = {
    "closure_hash", "summary_ref", "stage_artifacts_ref", "evidence_digest_hash",
}
_EXPERIENCE_FORBIDDEN_KEY_TERMS = (
    "private", "raw", "evidence", "review", "candidate", "trace", "video",
    "criterion", "threshold", "seed", "truth", "prompt", "credential",
    "mujoco", "translation", "implementation", "consumer",
)
_EXPERIENCE_FORBIDDEN_CONTENT_TERMS = (
    "private", "raw", "candidate", "trace", "video", "criterion", "threshold",
    "seed", "truth", "prompt", "credential", "mujoco", "translation",
    "implementation", "consumer",
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


def _private_issues(
    value: Any,
    location: str = "$",
    *,
    allow_private_inputs: bool = False,
    _in_robot_public_projection: bool = False,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if not allow_private_inputs and any(term in lowered for term in _PRIVATE_TERMS):
                issues.append(_issues("PRIVATE_FIELD", f"{location}.{key} is not public"))
            issues.extend(_private_issues(
                item,
                f"{location}.{key}",
                allow_private_inputs=allow_private_inputs,
                _in_robot_public_projection=(
                    _in_robot_public_projection or str(key) == "robot_public_projection"
                ),
            ))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issues.extend(_private_issues(
                item,
                f"{location}[{index}]",
                allow_private_inputs=allow_private_inputs,
                _in_robot_public_projection=_in_robot_public_projection,
            ))
    elif isinstance(value, str):
        lowered = value.lower()
        if any(term in lowered for term in ("private criterion", "mujoco", "translation layer")):
            if not (_in_robot_public_projection and value in _DIRECT_PUBLIC_PROJECTION_TOKENS):
                issues.append(_issues("PRIVATE_CONTENT", f"{location} contains forbidden private content"))
    return issues


def _json_value(value: Any, location: str) -> Any:
    """Normalize one value while accepting only the JSON data domain."""

    if isinstance(value, ABCMapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(f"{location} object keys must be strings")
            normalized[key] = _json_value(item, f"{location}.{key}")
        return normalized
    if isinstance(value, list):
        return [_json_value(item, f"{location}[{index}]") for index, item in enumerate(value)]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ContractError(f"{location} must contain only JSON values")


def _closed_experience_object(value: Any, fields: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{location} must be an object")
    missing = fields - set(value)
    extra = set(value) - fields
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if extra:
            details.append(f"unexpected {sorted(extra)}")
        raise ContractError(f"{location} has an invalid closed shape ({'; '.join(details)})")
    return value


def _experience_private_issue(value: Any, location: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = key.casefold()
            if key != "evidence_digest_hash" and any(term in lowered for term in _EXPERIENCE_FORBIDDEN_KEY_TERMS):
                return f"{location}.{key} is not an allowed design Experience field"
            issue = _experience_private_issue(item, f"{location}.{key}")
            if issue is not None:
                return issue
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issue = _experience_private_issue(item, f"{location}[{index}]")
            if issue is not None:
                return issue
    elif isinstance(value, str):
        lowered = value.casefold()
        if any(term in lowered for term in _EXPERIENCE_FORBIDDEN_CONTENT_TERMS):
            return f"{location} contains forbidden private or privileged Experience content"
    return None


def _validate_experience_applicability(value: Any, location: str, *, allow_empty: bool) -> None:
    if allow_empty and value == {}:
        return
    applicability = _closed_experience_object(value, _EXPERIENCE_APPLICABILITY_FIELDS, location)
    for field in ("robot_model_id", "robot_configuration_id", "granularity_condition", "observation_condition"):
        if not _id(applicability[field]):
            raise ContractError(f"{location}.{field} must be non-empty")
    sdk_entry_id = applicability["sdk_entry_id"]
    if sdk_entry_id is not None and not _id(sdk_entry_id):
        raise ContractError(f"{location}.sdk_entry_id must be null or non-empty")
    effect_scope = applicability["capability_effect_scope"]
    if not isinstance(effect_scope, list) or not effect_scope or not all(_id(item) for item in effect_scope):
        raise ContractError(f"{location}.capability_effect_scope must be a non-empty string array")


def _validate_experience_provenance(value: Any, location: str) -> None:
    provenance = _closed_experience_object(value, _EXPERIENCE_PROVENANCE_FIELDS, location)
    for field in ("summary_ref", "stage_artifacts_ref"):
        if not _id(provenance[field]):
            raise ContractError(f"{location}.{field} must be non-empty")
    for field in ("closure_hash", "evidence_digest_hash"):
        if not is_content_hash(provenance[field]):
            raise ContractError(f"{location}.{field} must be a content hash")


def _empty_design_experience_snapshot() -> dict[str, Any]:
    return {
        "artifact_type": "experience_snapshot",
        "format_version": "experimental-1",
        "snapshot_id": "empty-design-experience",
        "recipient_class": "design",
        "applicability": {},
        "records": [],
    }


def _validate_design_experience_snapshot(value: Mapping[str, Any] | None) -> dict[str, Any]:
    snapshot = _empty_design_experience_snapshot() if value is None else _json_value(value, "design_experience_snapshot")
    snapshot = _closed_experience_object(snapshot, _EXPERIENCE_SNAPSHOT_FIELDS, "design_experience_snapshot")
    if snapshot["artifact_type"] != "experience_snapshot":
        raise ContractError("design_experience_snapshot artifact_type is invalid")
    if snapshot["format_version"] != "experimental-1":
        raise ContractError("design_experience_snapshot format_version is invalid")
    if not _id(snapshot["snapshot_id"]):
        raise ContractError("design_experience_snapshot snapshot_id must be non-empty")
    if snapshot["recipient_class"] != "design":
        raise ContractError("design_experience_snapshot recipient_class must be design")
    records = snapshot["records"]
    if not isinstance(records, list):
        raise ContractError("design_experience_snapshot records must be an array")
    _validate_experience_applicability(
        snapshot["applicability"],
        "design_experience_snapshot.applicability",
        allow_empty=not records,
    )
    applicability_bytes = canonical_bytes(snapshot["applicability"])
    for index, record_value in enumerate(records):
        record = _closed_experience_object(record_value, _EXPERIENCE_RECORD_FIELDS, f"design_experience_snapshot.records[{index}]")
        for field in ("record_id", "version"):
            if not _id(record[field]):
                raise ContractError(f"design_experience_snapshot.records[{index}].{field} must be non-empty")
        record_ref = _closed_experience_object(
            record["record_ref"],
            _EXPERIENCE_RECORD_REF_FIELDS,
            f"design_experience_snapshot.records[{index}].record_ref",
        )
        if not _id(record_ref["path"]):
            raise ContractError(f"design_experience_snapshot.records[{index}].record_ref.path must be non-empty")
        if not is_content_hash(record_ref["content_hash"]):
            raise ContractError(
                f"design_experience_snapshot.records[{index}].record_ref.content_hash must be a content hash"
            )
        projection = _closed_experience_object(
            record["projection"],
            _EXPERIENCE_PROJECTION_FIELDS,
            f"design_experience_snapshot.records[{index}].projection",
        )
        if not _id(projection["experience_id"]):
            raise ContractError(
                f"design_experience_snapshot.records[{index}].projection.experience_id must be non-empty"
            )
        if not _id(projection["guidance"]):
            raise ContractError(
                f"design_experience_snapshot.records[{index}].projection.guidance must be non-empty"
            )
        _validate_experience_applicability(
            projection["applicability"],
            f"design_experience_snapshot.records[{index}].projection.applicability",
            allow_empty=False,
        )
        _validate_experience_provenance(
            projection["provenance"],
            f"design_experience_snapshot.records[{index}].projection.provenance",
        )
        if canonical_bytes(projection["applicability"]) != applicability_bytes:
            raise ContractError(
                f"design_experience_snapshot.records[{index}].projection applicability does not match the snapshot"
            )
    private_issue = _experience_private_issue(snapshot, "design_experience_snapshot")
    if private_issue is not None:
        raise ContractError(private_issue)
    return copy.deepcopy(snapshot)


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
    effect_allowlist: set[str] | None,
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
    elif effect_allowlist is not None and capability.get("effect") not in effect_allowlist:
        issues.append(_issues("PUBLIC_EFFECT", f"{location}.effect is not in the robot public effect allowlist"))
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
        "unsupported_requirement_ids", "blocking_requirement_ids", "design_experience_snapshot_hash",
    }
    artifact = _closed(design, fields, "capability_design", issues)
    if artifact is None:
        return issues
    if artifact.get("artifact_type") != "capability_design" or artifact.get("schema_version") != "1.0.0":
        issues.append(_issues("ARTIFACT_IDENTITY", "capability design identity is invalid"))
    if not _id(artifact.get("run_id")):
        issues.append(_issues("RUN_ID", "capability design run_id is invalid"))
    if not is_content_hash(artifact.get("design_experience_snapshot_hash")):
        issues.append(_issues("EXPERIENCE_SNAPSHOT_HASH", "design experience snapshot hash is invalid"))
    projection = artifact.get("robot_public_projection")
    if not isinstance(projection, dict):
        issues.append(_issues("ROBOT_PROJECTION", "robot public projection must be an object"))
        projection = {}
    action_affordances = _projection_strings(projection, "action_affordances", issues, required=True)
    observation_affordances = _projection_strings(projection, "observation_affordances", issues, required=True)
    effect_allowlist = _projection_allowlist(projection, "effect_allowlist", "effects", issues)
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
            effect_allowlist,
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
        *,
        design_experience_snapshot: Mapping[str, Any] | None = None,
    ) -> Stage1Result:
        if not _id(run_id):
            raise ContractError("run_id must be a non-empty string")
        experience_snapshot = _validate_design_experience_snapshot(design_experience_snapshot)
        experience_snapshot_hash = content_hash(canonical_bytes(experience_snapshot))
        initial_inputs = {
            "run_id": run_id,
            "robot_public_projection": copy.deepcopy(dict(robot_public_projection)),
            "task_descriptions": copy.deepcopy([dict(task) for task in task_descriptions]),
            "g2_profile": copy.deepcopy(dict(g2_profile)),
            "design_experience_snapshot": experience_snapshot,
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
            inputs: dict[str, Any] = copy.deepcopy(initial_inputs)
            if working is not None:
                inputs["working_design"] = copy.deepcopy(working)
                inputs["diagnostics"] = copy.deepcopy(diagnostics)
            input_hash = content_hash(canonical_bytes(inputs))
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
                "design_experience_snapshot_hash": experience_snapshot_hash,
                "capabilities": output.get("capabilities"),
                "unsupported_requirement_ids": output.get("unsupported_requirement_ids"),
                "blocking_requirement_ids": output.get("blocking_requirement_ids"),
            }
            diagnostics = output_issues + check_capability_design(design)
            calls.append({
                "call": attempt + 1,
                "stage": "stage1",
                "input_hash": input_hash,
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
