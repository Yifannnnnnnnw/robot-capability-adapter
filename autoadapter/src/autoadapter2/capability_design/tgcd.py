"""Task-Grounded Capability Design (``capability-v2``).

TGCD is the only design stage that receives eligible Experience.  The model
authors a small set of reusable capabilities from the public morphology and
source-backed Task Library; it does not choose from a Framework primitive
family and it does not compile a task macro.  The deterministic protocol
validator in :mod:`autoadapter2.capability_design.protocol` is the sole
boundary for the resulting artifact.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from autoadapter2.driver_synthesis.interactive import PublicDevelopmentSession
from autoadapter2.driver_synthesis.probe import ProbeBudget
from autoadapter2.react import ReactLoopError, run_artifact_react

from .protocol import (
    CAPABILITY_INVOCATION_ABI,
    CAPABILITY_PROTOCOL_VERSION,
    CapabilityProtocolError,
    CapabilitySchemaError,
    capability_methods,
    capability_records,
    json_copy,
    validate_schema_definition,
    validate_structured_criterion,
    validate_capability_design as _validate_capability_design,
)


TGCD_SYSTEM_PROMPT = """You are AutoAdapter capability-v2 Task-Grounded Capability Design.
Author three to ten reusable, package-bound robot capabilities from the supplied public
morphology and source-backed Task Library. Do not select from a primitive_family catalogue.
Each capability is one single-call physical effect and must have a unique capability_id and
Python method_name, a concise description and effect, a closed task-neutral request_schema,
preconditions, temporal_semantics, invariants, failure_behavior, and source-grounded structured
criteria. Each capability has exactly one top-level criterion. Every non-object request-schema
node has its own explicit unit and frame, numeric bounds are finite where relevant, and every node
carrying a numeric bound also carries its own public evidence_refs. These rules are recursive:
an array node itself needs minItems, maxItems, unit, and frame; its items object is a separate full
schema node, so numeric items also need their own unit, frame, finite bounds, and evidence_refs.
Parent metadata is never inherited. A request schema must not contain
task_id, task_parameters, scene,
reset, private values, criteria, oracle fields, or a task/macro plan.

Return one JSON object with artifact_type='capability_design', schema_version='2.0',
capability_protocol_version='capability-v2', the supplied package identity, invocation_abi
exactly equal to the supplied capability-request ABI, capabilities[], and task_support[].
task_support is only a many-to-many relation of {task_id, capability_id, rationale}; it contains
no ordered calls, waypoints, macro, plan, reset, scene, criterion, or private values. It must cover
every supplied task and every authored capability at least once. Do not
return code, Driver/Repair material, hidden instances, exact private requests, oracle plans, or
a self-reported verdict. The public reference catalog is background evidence only: author the
capabilities yourself and do not copy task mappings from it."""


TGCD_ARTIFACT_TURNS = 6
TGCD_ARTIFACT_NAME = "capability_design.json"


class CapabilityDesignError(CapabilitySchemaError):
    """Raised when TGCD cannot produce a valid capability-v2 artifact."""


class JsonGenerator(Protocol):
    def generate_json(
        self,
        *,
        stage: str,
        prompt: str,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]: ...


def _supports_artifact_react(client: Any) -> bool:
    return callable(getattr(client, "generate_tool_turn", None))


def _read_json_object(path: Path, *, artifact_name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityDesignError(f"{artifact_name} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CapabilityDesignError(f"{artifact_name} must contain one JSON object")
    return value


def _framework_source_root() -> Path:
    return Path(__file__).resolve().parents[2]


TGCDEventCallback = Callable[[Mapping[str, Any]], Any]


_REFERENCE_ROOT = Path(__file__).resolve().parents[3] / "references" / "capability_v2"
_FORBIDDEN_PUBLIC_REFERENCE_KEYS = {
    "task_support",
    "supported_task_ids",
    "covered_task_ids",
    "task_id",
    "task_ids",
    "oracle",
    "oracle_plan",
    "task_mapping",
    "task_mappings",
    "exact_task_calls",
    "calls",
    "waypoints",
    "macro",
    "plan",
}


def _assert_reference_public(value: Any, *, where: str = "reference") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in _FORBIDDEN_PUBLIC_REFERENCE_KEYS:
                raise CapabilityDesignError(f"{where} exposes forbidden reference field {key!r}")
            _assert_reference_public(child, where=f"{where}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_reference_public(child, where=f"{where}[{index}]")


def load_public_reference_catalog(
    root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load the canonical SO-101/Go2 public reference projection.

    The assets are a public design aid only.  This loader intentionally
    rejects any accidental task relation or oracle field rather than silently
    leaking it into a model prompt.
    """

    reference_root = Path(root) if root is not None else _REFERENCE_ROOT
    index_path = reference_root / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise CapabilityDesignError(f"cannot read capability-v2 reference index: {exc}") from None
    if not isinstance(index, Mapping):
        raise CapabilityDesignError("capability-v2 reference index must be an object")
    entries = index.get("references")
    if not isinstance(entries, list) or not entries:
        raise CapabilityDesignError("capability-v2 reference index has no references")
    result: list[dict[str, Any]] = []
    for position, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise CapabilityDesignError(f"references[{position}] must be an object")
        relative = entry.get("path")
        if not isinstance(relative, str) or not relative.strip():
            raise CapabilityDesignError(f"references[{position}].path must be non-empty text")
        path = (reference_root / relative).resolve()
        try:
            path.relative_to(reference_root.resolve())
        except ValueError:
            raise CapabilityDesignError("capability-v2 reference path escapes its root") from None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise CapabilityDesignError(f"cannot read reference {relative}: {exc}") from None
        if not isinstance(value, dict):
            raise CapabilityDesignError(f"reference {relative} must be an object")
        _assert_reference_public(value, where=relative)
        capabilities = value.get("capabilities")
        if not isinstance(capabilities, list) or not capabilities:
            raise CapabilityDesignError(f"reference {relative} has no capabilities")
        for capability_index, capability in enumerate(capabilities):
            if not isinstance(capability, Mapping):
                raise CapabilityDesignError(
                    f"{relative}.capabilities[{capability_index}] must be an object"
                )
            try:
                validate_schema_definition(
                    capability.get("request_schema"),
                    path=f"{relative}.capabilities[{capability_index}].request_schema",
                )
            except CapabilityProtocolError as exc:
                raise CapabilityDesignError(str(exc)) from None
            criteria = capability.get("criteria")
            if not isinstance(criteria, list) or not criteria:
                raise CapabilityDesignError(
                    f"{relative}.capabilities[{capability_index}].criteria must be a non-empty array"
                )
            for criterion_index, criterion in enumerate(criteria):
                try:
                    validate_structured_criterion(
                        criterion,
                        path=(
                            f"{relative}.capabilities[{capability_index}]"
                            f".criteria[{criterion_index}]"
                        ),
                    )
                except CapabilityProtocolError as exc:
                    raise CapabilityDesignError(str(exc)) from None
            for field in ("preconditions", "invariants"):
                statements = capability.get(field)
                if not isinstance(statements, list) or not statements or any(
                    not isinstance(statement, str) or not statement.strip()
                    for statement in statements
                ):
                    raise CapabilityDesignError(
                        f"{relative}.capabilities[{capability_index}].{field} "
                        "must be a non-empty text array"
                    )
            temporal = capability.get("temporal_semantics")
            if not isinstance(temporal, Mapping) or not temporal:
                raise CapabilityDesignError(
                    f"{relative}.capabilities[{capability_index}].temporal_semantics "
                    "must be a non-empty object"
                )
            failure = capability.get("failure_behavior")
            if not isinstance(failure, (str, Mapping)) or not failure:
                raise CapabilityDesignError(
                    f"{relative}.capabilities[{capability_index}].failure_behavior "
                    "must be non-empty text or an object"
                )
        result.append(json_copy(value, label=relative))
    _assert_reference_public(result)
    return result


def _package_ids(package: Any) -> dict[str, str]:
    if isinstance(package, Mapping):
        values = {
            "robot_configuration_id": package.get("robot_configuration_id"),
            "package_version": package.get("package_version"),
            "task_snapshot_id": package.get("task_snapshot_id", package.get("snapshot_id")),
        }
    else:
        values = {
            "robot_configuration_id": getattr(package, "robot_configuration_id", None),
            "package_version": getattr(package, "package_version", None),
            "task_snapshot_id": getattr(package, "snapshot_id", None),
        }
    result: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise CapabilityDesignError(f"package.{key} must be non-empty text")
        result[key] = value
    return result


def _package_tasks(package: Any) -> Sequence[Mapping[str, Any]]:
    if isinstance(package, Mapping):
        raw = package.get("tasks", package.get("task_library", {}).get("tasks", []))
    else:
        raw = getattr(package, "tasks", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise CapabilityDesignError("package.tasks must be an array")
    return raw


def _sanitise_experience(experience: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copy public Experience without imposing a historical record schema."""

    if not isinstance(experience, Sequence) or isinstance(experience, (str, bytes)):
        raise CapabilityDesignError("experience must be an array")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(experience):
        if not isinstance(item, Mapping):
            raise CapabilityDesignError(f"experience[{index}] must be an object")
        copied = json_copy(dict(item), label=f"experience[{index}]")
        if not isinstance(copied, dict):  # pragma: no cover - mapping input
            raise CapabilityDesignError(f"experience[{index}] must be an object")
        # These names would cross the candidate visibility boundary.  The
        # allowed root fields are intentionally not enumerated so the newer
        # observation/lesson/recommendation/scope/public_evidence records and
        # Framework-added provenance/outcome remain forward compatible.
        forbidden = {
            "driver",
            "driver_code",
            "candidate_driver",
            "repair_history",
            "candidate_source",
            "private_cases",
            "private_bindings",
            "guards",
        }
        def audit(value: Any, where: str) -> None:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    normalized = str(key).lower()
                    tokens = {
                        token for token in re.split(r"[^a-z0-9]+", normalized) if token
                    }
                    if normalized in forbidden or tokens.intersection(
                        {"driver", "repair", "candidate", "private", "guard"}
                    ):
                        raise CapabilityDesignError(
                            f"{where} contains candidate/private field {key!r}"
                        )
                    audit(child, f"{where}.{key}")
            elif isinstance(value, list):
                for child_index, child in enumerate(value):
                    audit(child, f"{where}[{child_index}]")

        audit(copied, f"experience[{index}]")
        result.append(copied)
    return result


def _sanitise_study(study: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Project the completed public STUDY artifact into TGCD.

    STUDY precedes capability design.  Its public findings and probe plan are
    useful design evidence, but candidate/private material is never a valid
    STUDY field and must fail closed if a caller tries to inject it.
    """

    if study is None:
        return None
    if not isinstance(study, Mapping):
        raise CapabilityDesignError("study must be one public JSON object")
    copied = json_copy(dict(study), label="study")
    forbidden = {
        "candidate_driver",
        "candidate_source",
        "driver_code",
        "driver_source",
        "private_bindings",
        "private_cases",
        "private_guards",
        "repair_history",
        "validation_verdict",
    }

    def audit(value: Any, where: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).lower() in forbidden:
                    raise CapabilityDesignError(
                        f"{where} contains candidate/private field {key!r}"
                    )
                audit(child, f"{where}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                audit(child, f"{where}[{index}]")

    audit(copied, "study")
    return copied


def build_public_tgcd_inputs(
    package: Any,
    *,
    study: Mapping[str, Any] | None = None,
    experience: Sequence[Mapping[str, Any]] = (),
    reference_catalog: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the complete model-visible TGCD projection."""

    ids = _package_ids(package)
    morphology = package.get("morphology", {}) if isinstance(package, Mapping) else getattr(package, "morphology", {})
    if not isinstance(morphology, Mapping):
        raise CapabilityDesignError("package.morphology must be an object")
    references = (
        load_public_reference_catalog()
        if reference_catalog is None
        else [json_copy(dict(value), label="reference_catalog") for value in reference_catalog]
    )
    _assert_reference_public(references)
    tasks = json_copy(list(_package_tasks(package)), label="task_library")
    task_index = [
        {
            key: task[key]
            for key in ("task_id", "name", "description")
            if isinstance(task, Mapping) and key in task
        }
        for task in tasks
    ]
    matching_reference_indices = [
        index
        for index, reference in enumerate(references)
        if reference.get("robot_configuration_id") == ids["robot_configuration_id"]
    ]
    artifact_header = {
        "artifact_type": "capability_design",
        "schema_version": "2.0",
        "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
        **ids,
        "invocation_abi": json_copy(
            CAPABILITY_INVOCATION_ABI,
            label="capability_invocation_abi",
        ),
    }
    matching_references = [
        json_copy(references[index], label=f"matching_reference[{index}]")
        for index in matching_reference_indices
    ]
    return {
        "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
        "invocation_abi": json_copy(
            CAPABILITY_INVOCATION_ABI,
            label="capability_invocation_abi",
        ),
        "artifact_header": artifact_header,
        "validator_contract": {
            "capability_count_min": 3,
            "capability_count_max": 10,
            "capability_required_fields": [
                "capability_id",
                "method_name",
                "description",
                "effect",
                "request_schema",
                "preconditions",
                "temporal_semantics",
                "invariants",
                "failure_behavior",
                "criteria",
                "evidence_refs",
            ],
            "criteria_count_per_capability": 1,
            "criterion_required_fields": [
                "metric",
                "unit",
                "comparator",
                "threshold",
                "temporal",
                "aggregation",
                "source_refs",
            ],
            "criterion_comparators": ["<", "<=", ">", ">=", "==", "between"],
            "criterion_field_rules": {
                "metric": "non-empty text",
                "unit": "non-empty text",
                "threshold": "finite number or two-number range",
                "temporal": "non-empty JSON object; strings are invalid",
                "aggregation": "non-empty JSON object; strings are invalid",
                "source_refs": "non-empty evidence-ref array",
            },
            "request_schema_supported_keywords": [
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
                "const",
                "description",
                "unit",
                "frame",
                "evidence_refs",
            ],
            "bounded_schema_fields_require_evidence_refs": True,
            "request_schema_rules": {
                "recursive": True,
                "parent_metadata_is_not_inherited": True,
                "object_node_required_fields": [
                    "type",
                    "properties",
                    "required",
                    "additionalProperties",
                ],
                "object_additionalProperties_must_equal": False,
                "object_required_must_equal_all_property_names": True,
                "array_node_required_fields": [
                    "type",
                    "items",
                    "minItems",
                    "maxItems",
                    "unit",
                    "frame",
                ],
                "array_items_follow_same_rules_recursively": True,
                "numeric_node_required_fields": [
                    "type",
                    "unit",
                    "frame",
                    "at_least_one_finite_numeric_bound",
                    "evidence_refs",
                ],
                "string_boolean_node_required_fields": ["type", "unit", "frame"],
            },
            "evidence_ref_required_fields": ["source_id", "specific_reference"],
            "preconditions_and_invariants_are_nonempty_text_arrays": True,
            "temporal_semantics_is_nonempty_object": True,
            "task_support_must_cover_every_task": True,
            "task_support_must_cover_every_capability": True,
        },
        "matching_capability_references": matching_references,
        "robot_package": {
            **ids,
            "morphology": json_copy(dict(morphology), label="morphology"),
            "tasks": tasks,
        },
        "task_index": task_index,
        "capability_v2_public_references": references,
        "matching_reference_indices": matching_reference_indices,
        "completed_public_study": _sanitise_study(study),
        "eligible_experience": _sanitise_experience(experience),
        "task_support_is_design_evidence_only": True,
        "runtime_tools_are_not_derived_from_task_support": True,
    }


def validate_capability_design(
    design: Mapping[str, Any],
    package: Any,
    *,
    require_task_support: bool = True,
) -> dict[str, Any]:
    try:
        return _validate_capability_design(
            design,
            package=package,
            require_task_support=require_task_support,
        )
    except CapabilityProtocolError as exc:
        raise CapabilityDesignError(str(exc)) from None


@dataclass(frozen=True)
class TGCDPhase:
    """Six-turn TGCD artifact workflow exposed for Framework integration."""

    client: JsonGenerator
    package: Any
    study: Mapping[str, Any] | None = None
    experience: Sequence[Mapping[str, Any]] = ()
    reference_catalog: Sequence[Mapping[str, Any]] | None = None
    callback: TGCDEventCallback | None = None
    max_turns: int = TGCD_ARTIFACT_TURNS
    artifact_path: str | Path | None = None
    probe_budget: ProbeBudget = ProbeBudget(max_requests=None)

    def run(self) -> dict[str, Any]:
        return run_tgcd(
            self.client,
            self.package,
            study=self.study,
            experience=self.experience,
            reference_catalog=self.reference_catalog,
            callback=self.callback,
            max_turns=self.max_turns,
            artifact_path=self.artifact_path,
            probe_budget=self.probe_budget,
        )


def run_tgcd(
    client: JsonGenerator,
    package: Any,
    *,
    study: Mapping[str, Any] | None = None,
    experience: Sequence[Mapping[str, Any]] = (),
    reference_catalog: Sequence[Mapping[str, Any]] | None = None,
    callback: TGCDEventCallback | None = None,
    max_turns: int = TGCD_ARTIFACT_TURNS,
    max_model_attempts: int | None = None,
    artifact_path: str | Path | None = None,
    probe_budget: ProbeBudget = ProbeBudget(max_requests=None),
) -> dict[str, Any]:
    """Author and seal ``capability_design.json``.

    A real unified model client uses the AA1-style file workspace.  The
    ``generate_json`` branch is retained only as a compatibility seam for
    focused deterministic tests and older callers; the mainline model client
    always exposes ``generate_tool_turn``.
    """

    if max_model_attempts is not None:
        max_turns = max_model_attempts
    if not isinstance(max_turns, int) or isinstance(max_turns, bool) or not 1 <= max_turns <= TGCD_ARTIFACT_TURNS:
        raise CapabilityDesignError("max_turns must be between one and six")
    inputs = build_public_tgcd_inputs(
        package,
        study=study,
        experience=experience,
        reference_catalog=reference_catalog,
    )

    if _supports_artifact_react(client):
        destination = Path(artifact_path).resolve() if artifact_path is not None else None
        temporary = TemporaryDirectory(prefix="autoadapter-tgcd-") if destination is None else None
        workspace_root = (
            Path(temporary.name)
            if temporary is not None
            else destination.parent / "workspace"
        )
        context = temporary if temporary is not None else nullcontext()
        with context:
            session = PublicDevelopmentSession(
                package=package,
                condition="from-scratch",
                workspace=workspace_root,
                budget=probe_budget,
                source_root=_framework_source_root(),
            )
            model_inputs_path = session.workspace / "tgcd_inputs.json"
            model_inputs_path.write_text(
                json.dumps(inputs, ensure_ascii=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            working_artifact = session.workspace / TGCD_ARTIFACT_NAME
            working_artifact.unlink(missing_ok=True)

            def validate_file(path: Path) -> dict[str, Any]:
                return validate_capability_design(
                    _read_json_object(path, artifact_name=TGCD_ARTIFACT_NAME),
                    package,
                )

            authoring_brief = json.dumps(
                {
                    "artifact_header": inputs["artifact_header"],
                    "validator_contract": inputs["validator_contract"],
                    "task_index": inputs["task_index"],
                    "matching_capability_references": inputs[
                        "matching_capability_references"
                    ],
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
            try:
                result = run_artifact_react(
                    client=client,
                    stage="tgcd",
                    system_prompt=TGCD_SYSTEM_PROMPT,
                    user_prompt=(
                        "The complete raw public input is ./tgcd_inputs.json in the phase-workspace "
                        "root. It is compact JSON; use execute_python for targeted queries instead "
                        "of spending turns printing the full Task Library or reference catalog. "
                        f"The compact authoring brief is included here verbatim: {authoring_brief}. "
                        "Its top-level invocation_abi is the exact required capability-v2 ABI; "
                        "copy every artifact_header field unchanged at the artifact top level, "
                        "not under package_identity, and do not use the legacy task invocation "
                        "ABI nested in morphology. Follow validator_contract exactly. Use the "
                        "compact task_index for coverage. matching_capability_references contains "
                        "the same-configuration records directly; do not print the full Task Library "
                        "or reference catalog. When this matching list is non-empty, use its "
                        "calibrated records as the source for numeric request bounds and criteria "
                        "rather than inventing replacements. Copy any selected request_schema, "
                        "criteria, and evidence_refs without abridging them. Recursively audit every "
                        "request_schema node before writing: an array and its items are separate "
                        "nodes; every object sets additionalProperties=false and requires exactly "
                        "all of its property names; parent "
                        "unit/frame/evidence do not propagate, and numeric items need their own "
                        "unit, frame, finite bounds, and evidence_refs. Each capability has exactly "
                        "one criterion; its temporal and aggregation fields must each be a non-empty "
                        "JSON object, never a string. Keep capabilities as "
                        "reusable single physical effects, not task operations such as whole-object "
                        "push/grasp/release or fixture-specific macros. You must independently add the "
                        "required preconditions, temporal semantics, invariants, failure behavior, "
                        "and task_support. Prefer the smallest sufficient design, normally three to "
                        "five capabilities. task_support is not a Cartesian product: emit only the "
                        "minimal pairs needed to cover every task and every capability. Write compact "
                        "JSON. If the artifact would exceed one model response, split it at safe text "
                        "boundaries across write_file calls: the first chunk uses append=false and "
                        "later chunks use append=true; only the final combined file must parse as JSON. "
                        f"Author the complete canonical {TGCD_ARTIFACT_NAME} with write_file. "
                        "Conserve the six-turn budget: use at most two turns for inspection. Turns "
                        "three through six are write-only delivery turns so a large artifact can be "
                        "written in bounded chunks and still receive deterministic validation feedback. "
                        "You may use execute_python "
                        "for credential-free public MuJoCo checks. "
                        "End the turn when the artifact is ready; there is no submit tool."
                    ),
                    tools=session.artifact_tools(include_skeleton=False),
                    artifact_name=TGCD_ARTIFACT_NAME,
                    artifact_path=working_artifact,
                    validate_artifact=validate_file,
                    max_turns=max_turns,
                    delivery_turns=max(1, max_turns - 2),
                )
            except ReactLoopError as exc:
                raise CapabilityDesignError(
                    f"TGCD did not produce a valid {TGCD_ARTIFACT_NAME} in {max_turns} turns"
                ) from exc
            finally:
                session.close()
            if not isinstance(result.artifact, Mapping):
                raise CapabilityDesignError(
                    f"{TGCD_ARTIFACT_NAME} validation returned no design object"
                )
            canonical = json_copy(dict(result.artifact), label=TGCD_ARTIFACT_NAME)
            if destination is not None:
                write_capability_design(destination, canonical)
            if callback is not None:
                callback(
                    {
                        "stage": "tgcd-sealed",
                        "turn": result.model_turns,
                        "artifact": canonical,
                        "completion": result.completed_on,
                        "tool_calls": result.tool_calls,
                        "trace": list(result.trace),
                    }
                )
            return canonical

    prompt = TGCD_SYSTEM_PROMPT
    for turn in range(max_turns):
        stage = "tgcd" if turn == 0 else f"tgcd-artifact-correction-{turn}"
        try:
            output = client.generate_json(stage=stage, prompt=prompt, inputs=inputs)
        except Exception as exc:
            if turn + 1 >= max_turns:
                raise CapabilityDesignError(
                    f"model JSON generation failed after {max_turns} turns: {type(exc).__name__}"
                ) from None
            inputs = {**inputs, "deterministic_audit_error": f"model call failed: {type(exc).__name__}"}
            continue
        if callback is not None:
            callback({"stage": stage, "turn": turn + 1, "artifact": output})
        try:
            canonical = validate_capability_design(output, package)
            if artifact_path is not None:
                write_capability_design(artifact_path, canonical)
            if callback is not None:
                callback({"stage": "tgcd-sealed", "turn": turn + 1, "artifact": canonical})
            return canonical
        except CapabilityDesignError as exc:
            if turn + 1 >= max_turns:
                raise
            inputs = {
                **inputs,
                "previous_invalid_design": json_copy(output, label="previous_invalid_design"),
                "deterministic_audit_error": str(exc),
            }
            prompt = TGCD_SYSTEM_PROMPT + (
                "\nCorrect the prior object into one complete replacement. Keep three to ten "
                "self-authored capabilities, closed task-neutral request schemas, exact finite "
                "criteria fields with evidence_refs for every numeric bound, and pair-only "
                "task_support."
            )
    raise AssertionError("unreachable")


def write_capability_design(path: str | Path, design: Mapping[str, Any]) -> None:
    """Write one validated/canonical artifact for callers that persist TGCD."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(dict(design), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


# Compatibility aliases for callers that select capability design explicitly.
run_capability_tgcd = run_tgcd
validate_design = validate_capability_design


__all__ = [
    "CAPABILITY_INVOCATION_ABI",
    "CAPABILITY_PROTOCOL_VERSION",
    "CapabilityDesignError",
    "JsonGenerator",
    "TGCD_ARTIFACT_TURNS",
    "TGCD_ARTIFACT_NAME",
    "TGCDPhase",
    "TGCD_SYSTEM_PROMPT",
    "build_public_tgcd_inputs",
    "capability_methods",
    "capability_records",
    "load_public_reference_catalog",
    "run_capability_tgcd",
    "run_tgcd",
    "validate_capability_design",
    "validate_design",
    "write_capability_design",
]
