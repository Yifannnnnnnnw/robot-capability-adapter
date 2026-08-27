"""Implementation-blind capability-v2 Independent Validation Compiler.

IVC receives a sealed capability design and Framework-private execution
contexts.  It never receives candidate Driver source, a trace, or Repair
history.  The model references private fixtures but authors the task-neutral
request in each case.  The deterministic audit then requires exactly one
``nominal`` and one ``calibrated_boundary`` case per sealed capability,
checks every request against both the sealed schema and its calibrated private
domain when one is supplied, and copies each criterion without changing its
numeric values.
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from autoadapter2.capability_design.protocol import (
    CAPABILITY_PROTOCOL_VERSION,
    NUMERIC_CRITERION_AGGREGATION_KINDS,
    NUMERIC_CRITERION_TEMPORAL_KINDS,
    CapabilityProtocolError,
    capability_execution_contract,
    capability_records,
    json_copy,
    validate_schema_definition,
    validate_schema_value,
)
from autoadapter2.driver_synthesis.interactive import IsolatedArtifactSession
from autoadapter2.driver_synthesis.probe import ProbeBudget
from autoadapter2.harness.operators import (
    MeasurementOperatorError,
    audit_inline_measurement_binding,
    measurement_operator_authoring_compatibility,
    inspect_scene_entities,
    measurement_operator_catalog,
    measurement_operator_evaluation_mode,
    trusted_reference_contract_id,
)
from autoadapter2.react import ReactLoopError, run_artifact_react


IVC_ARTIFACT_TURNS = 6
IVC_ARTIFACT_NAME = "capability_validation_suite.json"
IVC_CASE_ROLES = ("nominal", "calibrated_boundary")
# Historical callers imported this symbol.  Capability-v2 no longer samples
# Task Library tasks; it compiles two cases per capability instead.
PRIVATE_CASE_SAMPLE_SIZE = 2
TASK_DEMO_CASE_COUNT = PRIVATE_CASE_SAMPLE_SIZE
TASK_DEMO_TASK_COUNT = PRIVATE_CASE_SAMPLE_SIZE


IVC_SYSTEM_PROMPT = """You are the implementation-blind capability-v2 Independent Validation Compiler.
Compile the sealed design into one hidden suite. You may see its exact authoring contract, task-support
relation, sanitized private instances/scenes, source lineage, every structurally compatible trusted measurement
operator, and worked references. You cannot see or infer a candidate Driver, trace, Repair history, or
verdict. The Framework has not selected an operator, instance, request, or scene entity for you.

deterministic_structural_compatibility removes only operators that are provably illegal for each sealed
unit, numeric criterion, or request schema. It is not a semantic recommendation or a completed binding.
Choose a kind only from that capability's structurally_compatible_operator_kinds, join its catalog
request_path:<value_type> signatures to compatible_request_paths_by_value_type, then decide whether its
measurement meaning actually matches the sealed metric. Reconstruct exact scene entity names from the
scene_entity_type_index shared set union the selected scene's additions; use joint_names_by_unit whenever
a joint parameter unit is declared, and finite_range_joint_names_by_unit for a bounded-dimensionless
joint target conversion. The final Framework audit remains authoritative.

The prompt already contains every exact authoring field. read_file is intentionally unavailable in
this phase because ./ivc_inputs.json can exceed the bounded file-output limit. Use execute_python only
for targeted entity/operator lookups in that JSON; never print a full collection. private_instances is
an object wrapper; apply shared fields before record-local overrides, use only indexed records, and copy
their exact guards, repetitions, timeout, and request domain. Every operator request-path value starts
with request. and resolves into the sealed closed schema. Operator signatures use plain JSON types,
entity:<type>, request_path:<value_type>, and optional:<signature>. These signatures describe required
value sources; never copy a signature marker as a parameter value. Every
measurement_binding_kind_catalog map key is an exact value allowed for measurement_binding.kind;
framework evaluation modes are metadata, never binding fields or kind values. Use this non-JSON shape:
measurement_binding = {metric: criterion.metric, unit: criterion.unit, kind: selected_catalog_key,
parameters: actual_values}. The binding has exactly those four fields; operator, mode, and
evaluation_mode are absent. Scaled joint targets may use target_scale/target_offset only when listed.
Never invent an operator parameter. If a kind name is unclear, use a targeted execute_python lookup of
its raw catalog description during the first two turns. Public assets are read-only at
AUTOADAPTER_PROBE_PUBLIC_PACKAGE.

Write capability_validation_suite.json with the supplied artifact/package identity, exactly one nominal
and one calibrated_boundary case per capability, and whole_suite_aggregation={'kind':'all_cases'}.
Copy every required_artifact_top_level_field entry unchanged directly into the artifact root alongside
cases; an artifact_header wrapper is forbidden.
Author each task-neutral request and inline measurement binding yourself. Requests must satisfy both the
sealed schema and selected instance domain, and a capability's two requests must differ. Copy its complete
criteria unchanged. Ground requests only with the supplied schema/domain evidence pairs. Use only listed
operator signatures and real entities from the selected scene. Private examples are examples, never IDs.
Never emit binding_id, code, task dispatch/IDs, Driver/Repair material, advice, or a self-reported verdict.

There are six turns: turn one is the only optional targeted inspection turn; turns two through six are
write/correction only. The authoring brief is sufficient, so write on turn one when no lookup is needed.
Every successful write is audited immediately. Large files may use bounded append writes; the combined
canonical file alone must parse."""


class IVCError(ValueError):
    """Raised when a private validation suite is incomplete or weakened."""


class JsonGenerator(Protocol):
    def generate_json(
        self,
        *,
        stage: str,
        prompt: str,
        inputs: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class ReferencePositiveControlHook(Protocol):
    """Framework-owned positive-control seam; candidate material is absent."""

    def __call__(
        self,
        *,
        capability_design: Mapping[str, Any],
        validation_suite: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


IVCEventCallback = Callable[[Mapping[str, Any]], Any]
_WORKED_REFERENCES_PATH = (
    Path(__file__).resolve().parents[3]
    / "references"
    / "capability_v2"
    / "ivc_worked_references.json"
)


def _supports_artifact_react(client: Any) -> bool:
    return callable(getattr(client, "generate_tool_turn", None))


def _read_json_object(path: Path, *, artifact_name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IVCError(f"{artifact_name} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise IVCError(f"{artifact_name} must contain one JSON object")
    return value


def _positive_control_passed(result: Mapping[str, Any]) -> bool:
    """Accept the Framework's explicit pass flag, never a bare object."""

    for field in ("passed", "reference_passed", "validation_passed"):
        if field in result:
            return result.get(field) is True
    return False


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise IVCError(f"cannot read private compiler input {path.name}") from exc
    if not isinstance(value, dict):
        raise IVCError(f"private compiler input {path.name} must be an object")
    return value


def load_ivc_worked_references(
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load the complete SO-101/Go2 inline-measurement worked references."""

    source = Path(path) if path is not None else _WORKED_REFERENCES_PATH
    document = _read_object(source)
    examples = document.get("worked_references")
    if (
        not isinstance(examples, list)
        or not examples
        or not all(isinstance(item, Mapping) for item in examples)
    ):
        raise IVCError("IVC worked references must be a non-empty object array")
    copied = _copy_private_inputs({"examples": examples}).get("examples")
    if not isinstance(copied, list):  # pragma: no cover - guarded above
        raise IVCError("IVC worked references could not be copied")
    return [dict(item) for item in copied]


def load_sanitized_ivc_examples(
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Compatibility alias for the former sanitized-example API name."""

    return load_ivc_worked_references(path)


def _semantic_reference_contracts() -> dict[str, dict[str, Any]]:
    """Index fixed B1 semantic operators by their sealed worked contract."""

    result: dict[str, dict[str, Any]] = {}
    for reference in load_ivc_worked_references():
        design = reference.get("capability_design")
        suite = reference.get("validation_suite")
        if not isinstance(design, Mapping) or not isinstance(suite, Mapping):
            raise IVCError("IVC worked reference is missing design or suite")
        robot_id = suite.get("robot_configuration_id")
        if not isinstance(robot_id, str) or not robot_id.strip():
            raise IVCError("IVC worked reference has no robot identity")
        capabilities = _design_map(design)
        cases = suite.get("cases")
        if not isinstance(cases, list):
            raise IVCError("IVC worked reference suite has no cases")
        for case in cases:
            if not isinstance(case, Mapping):
                raise IVCError("IVC worked reference contains a non-object case")
            binding = case.get("measurement_binding")
            capability_id = case.get("capability_id")
            if not isinstance(binding, Mapping) or not isinstance(capability_id, str):
                raise IVCError("IVC worked reference case is incomplete")
            kind = binding.get("kind")
            if not isinstance(kind, str) or (
                measurement_operator_evaluation_mode(kind)
                != "trusted_criterion_verdict"
            ):
                continue
            capability = capabilities.get(capability_id)
            if capability is None:
                raise IVCError("IVC worked reference case has unknown capability")
            contract_id = trusted_reference_contract_id(kind)
            if contract_id != capability_id:
                raise IVCError(
                    "IVC semantic operator and worked capability identity disagree"
                )
            record = {
                "robot_configuration_id": robot_id,
                "capability_id": capability_id,
                "execution_contract": capability_execution_contract(capability),
            }
            previous = result.get(kind)
            if previous is not None and previous != record:
                raise IVCError(f"IVC semantic operator {kind!r} is ambiguous")
            result[kind] = record
    return result


def _scoped_measurement_operator_catalog(
    *,
    package: Any,
    design: Mapping[str, Any],
) -> dict[str, Any]:
    """Hide fixed SO/Go execution operators outside their exact contracts."""

    catalog = measurement_operator_catalog()
    operators = catalog.get("operators")
    if not isinstance(operators, list):  # pragma: no cover - trusted static catalog
        raise IVCError("trusted measurement operator catalog is invalid")
    robot_id = _package_identity(package).get("robot_configuration_id")
    capabilities = _design_map(design)
    semantic_contracts = _semantic_reference_contracts()
    visible: list[dict[str, Any]] = []
    for operator in operators:
        if not isinstance(operator, Mapping):
            raise IVCError("trusted measurement operator catalog is invalid")
        kind = operator.get("kind")
        if measurement_operator_evaluation_mode(kind) != "trusted_criterion_verdict":
            visible.append(json_copy(dict(operator), label="measurement operator"))
            continue
        reference = semantic_contracts.get(kind) if isinstance(kind, str) else None
        capability = (
            capabilities.get(reference["capability_id"])
            if isinstance(reference, Mapping)
            else None
        )
        if (
            isinstance(reference, Mapping)
            and robot_id == reference["robot_configuration_id"]
            and capability is not None
            and capability_execution_contract(capability)
            == reference["execution_contract"]
        ):
            projected = json_copy(dict(operator), label="measurement operator")
            projected["execution_scope"] = {
                "robot_configuration_id": reference["robot_configuration_id"],
                "capability_id": reference["capability_id"],
                "requires_exact_worked_reference_contract": True,
            }
            visible.append(projected)
    result = json_copy(dict(catalog), label="measurement operator catalog")
    result["operators"] = visible
    return result


def _private_inputs_from_package(package: Any) -> dict[str, Any]:
    package_root = (
        package.get("root")
        if isinstance(package, Mapping)
        else getattr(package, "root", None)
    )
    if isinstance(package_root, str):
        package_root = Path(package_root)
    if not isinstance(package_root, Path):
        raise IVCError(
            "a package-private IVC context requires an available package root"
        )
    capability_dir = package_root / "capability_validation" / "private"
    task_dir = package_root / "tasks" / "private"
    names = ("instances", "bindings", "guards")
    capability_paths = {
        name: capability_dir / f"{name}.json" for name in names
    }
    present_capability = {
        name for name, path in capability_paths.items() if path.is_file()
    }
    if present_capability and present_capability != set(names):
        missing = sorted(set(names) - present_capability)
        raise IVCError(
            "package-local IVC context is incomplete; missing "
            "capability_validation/private/"
            + ", ".join(f"{name}.json" for name in missing)
        )
    task_paths = {name: task_dir / f"{name}.json" for name in names}
    present_task = {name for name, path in task_paths.items() if path.is_file()}
    if present_task and present_task != set(names):
        missing = sorted(set(names) - present_task)
        raise IVCError(
            "package task-backed IVC context is incomplete; missing tasks/private/"
            + ", ".join(f"{name}.json" for name in missing)
        )
    namespaces: list[tuple[str, dict[str, Path]]] = []
    if present_capability:
        namespaces.append(("capability", capability_paths))
    if present_task:
        namespaces.append(("task", task_paths))
    if not namespaces:
        raise IVCError("package has no complete private IVC context")
    calibration_namespace = (
        "combined" if len(namespaces) == 2 else namespaces[0][0]
    )

    id_fields = {
        "instances": "instance_id",
        "bindings": "binding_id",
        "guards": "guard_id",
    }
    result: dict[str, Any] = {}
    for name, id_field in id_fields.items():
        # The physical context is a closed, package-local namespace.  When no
        # dedicated capability context exists, the existing task fixtures are
        # trusted scene/reset/binding inputs; they never prescribe the native
        # capability request authored by IVC.
        documents = [
            (namespace, _read_object(paths[name]))
            for namespace, paths in namespaces
        ]
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        identity: dict[str, Any] = {}
        for namespace, document in documents:
            records = document.get(name)
            if not isinstance(records, list):
                raise IVCError(f"private compiler input {name}.json must contain {name}[]")
            for field in ("robot_configuration_id", "package_version", "task_snapshot_id"):
                value = document.get(field)
                if value is None:
                    continue
                if field in identity and identity[field] != value:
                    raise IVCError(f"private {name} identity conflict for {field}")
                identity[field] = value
            for record in records:
                if not isinstance(record, Mapping):
                    raise IVCError(f"private {name} contains a non-object record")
                identifier = record.get(id_field)
                if not isinstance(identifier, str) or not identifier.strip():
                    raise IVCError(f"private {name} contains an invalid {id_field}")
                if identifier in seen:
                    raise IVCError(f"private {name} contains conflicting ID {identifier!r}")
                seen.add(identifier)
                copied_record = json_copy(dict(record), label=f"private {name}")
                copied_record["context_namespace"] = namespace
                merged.append(copied_record)
        result[name] = {
            **identity,
            "calibration_namespace": calibration_namespace,
            name: merged,
        }
    return result


def _copy_private_inputs(private_inputs: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(private_inputs, Mapping):
        raise IVCError("private_inputs must be an object")
    copied = json_copy(dict(private_inputs), label="private_inputs")
    if not isinstance(copied, dict):  # pragma: no cover - mapping input
        raise IVCError("private_inputs must be an object")
    forbidden = {
        "driver",
        "driver_code",
        "candidate_driver",
        "candidate_source",
        "repair",
        "repair_history",
        "trace",
        "candidate_trace",
        "verdict",
        "candidate_verdict",
    }
    def walk(value: Any, where: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = str(key).lower()
                # MuJoCo symbol maps legitimately use names such as
                # ``right_driver_joint``.  Privacy checks apply to protocol
                # fields, not substrings inside package-owned entity names.
                if normalized in forbidden:
                    raise IVCError(f"{where} exposes candidate field {key!r}")
                walk(child, f"{where}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{where}[{index}]")

    walk(copied, "private_inputs")
    return copied


_TASK_INSTANCE_MODEL_FIELDS = frozenset(
    {
        "instance_id",
        "context_namespace",
        "capability_id",
        "case_role",
        "scene_entrypoint",
        "reset",
        "request_domain",
        "calibration_profile",
        "guard_ids",
        "repetitions",
        "timeout_sim_s",
    }
)


def _project_task_binding_for_model(
    record: Mapping[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    """Rewrite trusted task-envelope argument paths to the native request ABI."""

    projected = json_copy(dict(record), label=f"private bindings[{index}]")
    parameters = projected.get("parameters")
    if parameters is None:
        return projected
    if not isinstance(parameters, dict):
        raise IVCError(f"private bindings[{index}].parameters must be an object")
    prefix = "request.task_parameters."
    for key, value in tuple(parameters.items()):
        if not isinstance(key, str) or not key.endswith("_argument"):
            continue
        if (
            not isinstance(value, str)
            or not value.startswith(prefix)
            or not value[len(prefix) :]
            or "." in value[len(prefix) :]
        ):
            raise IVCError(
                f"private bindings[{index}].parameters.{key} must reference "
                "request.task_parameters.<field>"
            )
        parameters[key] = "request." + value[len(prefix) :]
    return projected


def _project_private_inputs_for_model(
    private_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Hide task execution envelopes while retaining IVC authoring context.

    A package without a dedicated capability-validation namespace falls back
    to trusted ``tasks/private`` fixtures.  Those records also contain the
    private Task Demo request and execution plan.  The Framework needs the
    complete records for audit and Harness execution, but the IVC model only
    needs opaque fixture IDs and the physical/calibration metadata required to
    author and bind capability cases.
    """

    copied = _copy_private_inputs(private_inputs)
    instances_document = copied.get("instances")
    if not (
        isinstance(instances_document, Mapping)
        and instances_document.get("calibration_namespace")
        in {"task", "capability", "combined"}
    ):
        return copied
    calibration_namespace = instances_document.get("calibration_namespace")
    raw_instances = instances_document.get("instances")
    if not isinstance(raw_instances, list):
        raise IVCError("private instances must be an object array")

    projected_instances: list[dict[str, Any]] = []
    for index, record in enumerate(raw_instances):
        if not isinstance(record, Mapping):
            raise IVCError(f"private instances[{index}] must be an object")
        projected_instances.append(
            {
                key: json_copy(value, label=f"private instances[{index}].{key}")
                for key, value in record.items()
                if key in _TASK_INSTANCE_MODEL_FIELDS
            }
        )

    projected_document = {
        key: json_copy(value, label=f"private instances.{key}")
        for key, value in instances_document.items()
        if key != "instances"
    }
    projected_document["instances"] = projected_instances
    copied["instances"] = projected_document

    bindings_document = copied.get("bindings")
    if not isinstance(bindings_document, Mapping):
        raise IVCError("private bindings must be an object document")
    raw_bindings = bindings_document.get("bindings")
    if not isinstance(raw_bindings, list):
        raise IVCError("private bindings must be an object array")
    projected_bindings: list[dict[str, Any]] = []
    for index, record in enumerate(raw_bindings):
        if not isinstance(record, Mapping):
            raise IVCError(f"private bindings[{index}] must be an object")
        if record.get("kind") == "b1_contract":
            # Complete semantic SO/Go examples are supplied separately; the
            # historical opaque dispatch is never part of the authoring DSL.
            continue
        namespace = record.get("context_namespace", calibration_namespace)
        projected = (
            _project_task_binding_for_model(record, index=index)
            if namespace == "task"
            else json_copy(dict(record), label=f"private bindings[{index}]")
        )
        if "binding_id" in projected:
            projected["example_id"] = projected.pop("binding_id")
        projected_bindings.append(projected)
    projected_binding_document = {
        key: json_copy(value, label=f"private bindings.{key}")
        for key, value in bindings_document.items()
        if key != "bindings"
    }
    projected_binding_document["bindings"] = projected_bindings
    copied["bindings"] = projected_binding_document
    return copied


def _records(value: Any, *, field: str) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if field in value:
            value = value[field]
        else:
            # A convenient id->record map is accepted for Framework callers.
            value = [dict(record, **({field[:-1] + "_id": key} if isinstance(record, Mapping) else {})) for key, record in value.items()]
    if not isinstance(value, list) or not value or not all(isinstance(item, Mapping) for item in value):
        raise IVCError(f"private {field} must be a non-empty object array")
    return list(value)


def _id_map(value: Any, *, field: str, id_field: str) -> dict[str, Mapping[str, Any]]:
    if field == "guards":
        raw = value.get(field) if isinstance(value, Mapping) and field in value else value
        if raw in (None, [], {}):
            return {}
    result: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(_records(value, field=field)):
        identifier = record.get(id_field)
        if not isinstance(identifier, str) or not identifier.strip() or identifier in result:
            raise IVCError(f"private {field}[{index}].{id_field} is invalid or duplicated")
        result[identifier] = record
    return result


def _design_map(design: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    try:
        capabilities = capability_records(design)
    except CapabilityProtocolError as exc:
        raise IVCError(str(exc)) from None
    result: dict[str, Mapping[str, Any]] = {}
    for index, capability in enumerate(capabilities):
        capability_id = capability.get("capability_id", capability.get("id"))
        if not isinstance(capability_id, str) or not capability_id.strip() or capability_id in result:
            raise IVCError(f"sealed capability {index} has an invalid or duplicate ID")
        result[capability_id] = capability
    return result


def _package_identity(package: Any) -> dict[str, Any]:
    if isinstance(package, Mapping):
        return {
            "robot_configuration_id": package.get("robot_configuration_id"),
            "package_version": package.get("package_version"),
            "task_snapshot_id": package.get("task_snapshot_id", package.get("snapshot_id")),
        }
    return {
        "robot_configuration_id": getattr(package, "robot_configuration_id", None),
        "package_version": getattr(package, "package_version", None),
        "task_snapshot_id": getattr(package, "snapshot_id", None),
    }


def _criterion_list(capability: Mapping[str, Any], *, where: str) -> list[dict[str, Any]]:
    raw = capability.get("criteria")
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], Mapping):
        raise IVCError(f"{where}.criteria must contain exactly one executable criterion")
    return [json_copy(dict(raw[0]), label=f"{where}.criteria[0]")]


def _same_criteria(case: Mapping[str, Any], capability: Mapping[str, Any], *, where: str) -> bool:
    expected = _criterion_list(capability, where="sealed capability")
    if isinstance(case.get("criteria"), list):
        return case.get("criteria") == expected
    if len(expected) == 1 and isinstance(case.get("criterion"), Mapping):
        return case.get("criterion") == expected[0]
    return False


def _same_json_value(left: Any, right: Any) -> bool:
    """Compare finite JSON structurally without Python's bool/int coercion."""

    try:
        return json.dumps(
            left,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ) == json.dumps(
            right,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return False


def _calibration_profile(record: Mapping[str, Any], *, label: str, where: str) -> str | None:
    profile = record.get("calibration_profile")
    if profile is None:
        return None
    if not isinstance(profile, str) or not profile.strip():
        raise IVCError(
            f"{where} selected private {label} has an invalid calibration_profile"
        )
    return profile


def _validate_authored_request_context(
    request: Any,
    *,
    instance: Mapping[str, Any],
    sealed_schema: Mapping[str, Any],
    where: str,
) -> None:
    """Validate an IVC-authored request against one private execution context."""

    scene_entrypoint = instance.get("scene_entrypoint")
    if not isinstance(scene_entrypoint, str) or not scene_entrypoint.strip():
        raise IVCError(f"{where} selected private instance has no scene_entrypoint")
    if not isinstance(instance.get("reset"), Mapping):
        raise IVCError(f"{where} selected private instance has no reset fixture")

    raw_domain = instance.get("request_domain")
    domain: Mapping[str, Any] | None = None
    if raw_domain is not None:
        if not isinstance(raw_domain, Mapping):
            raise IVCError(f"{where} selected private instance request_domain must be an object")
        try:
            domain = validate_schema_definition(
                raw_domain,
                path=f"{where}.private_request_domain",
            )
            validate_schema_value(
                request,
                domain,
                path=f"{where}.request against private request_domain",
            )
        except CapabilityProtocolError as exc:
            raise IVCError(str(exc)) from None

    anchors = instance.get("request_anchors")
    if anchors is None:
        return
    if not isinstance(anchors, list) or not anchors:
        raise IVCError(
            f"{where} selected private instance request_anchors must be a non-empty array when supplied"
        )
    seen_anchor_ids: set[str] = set()
    for index, anchor in enumerate(anchors):
        anchor_where = f"{where}.private_request_anchors[{index}]"
        if not isinstance(anchor, Mapping):
            raise IVCError(f"{anchor_where} must be an object")
        anchor_id = anchor.get("anchor_id")
        if (
            not isinstance(anchor_id, str)
            or not anchor_id.strip()
            or anchor_id in seen_anchor_ids
        ):
            raise IVCError(f"{anchor_where}.anchor_id is invalid or duplicated")
        seen_anchor_ids.add(anchor_id)
        source_ref = anchor.get("source_ref")
        if not isinstance(source_ref, str) or not source_ref.strip():
            raise IVCError(f"{anchor_where}.source_ref must be non-empty text")
        if "request" not in anchor:
            raise IVCError(f"{anchor_where}.request is required")
        try:
            validate_schema_value(
                anchor["request"],
                sealed_schema,
                path=f"{anchor_where}.request against sealed request_schema",
            )
            if domain is not None:
                validate_schema_value(
                    anchor["request"],
                    domain,
                    path=f"{anchor_where}.request against private request_domain",
                )
        except CapabilityProtocolError as exc:
            raise IVCError(str(exc)) from None


def _normalise_case_criteria(case: Mapping[str, Any], capability: Mapping[str, Any]) -> dict[str, Any]:
    result = json_copy(dict(case), label="validation case")
    expected = _criterion_list(capability, where="sealed capability")
    result["criteria"] = expected
    result.pop("criterion", None)
    return result


_IVC_CASE_FIELDS = {
    "case_id",
    "case_role",
    "capability_id",
    "method_name",
    "request",
    "request_grounding_refs",
    "instance_id",
    "measurement_binding",
    "guard_ids",
    "repetitions",
    "timeout_sim_s",
    "criteria",
}
def _package_root(package: Any) -> Path | None:
    value = package.get("root") if isinstance(package, Mapping) else getattr(package, "root", None)
    if isinstance(value, str):
        value = Path(value)
    return value.resolve() if isinstance(value, Path) else None


def _scene_audit_inputs(
    package: Any,
    instance: Mapping[str, Any],
    *,
    where: str,
) -> tuple[Path | None, Mapping[str, Any] | None]:
    root = _package_root(package)
    entrypoint = instance.get("scene_entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        raise IVCError(f"{where} selected private instance has no scene_entrypoint")
    if root is None:
        entities = instance.get("scene_entities")
        if not isinstance(entities, Mapping):
            raise IVCError(
                f"{where} requires a package root or explicit trusted scene_entities"
            )
        return None, entities
    scene_path = (root / entrypoint).resolve()
    try:
        scene_path.relative_to((root / "assets").resolve())
    except ValueError as exc:
        raise IVCError(f"{where} selected scene escapes package assets") from exc
    if not scene_path.is_file():
        raise IVCError(f"{where} selected scene does not exist")
    return scene_path, None


def _evidence_ref_pairs(value: Any) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            refs = node.get("evidence_refs")
            if isinstance(refs, list):
                for ref in refs:
                    if isinstance(ref, Mapping):
                        source_id = ref.get("source_id")
                        specific = ref.get("specific_reference")
                        if isinstance(source_id, str) and isinstance(specific, str):
                            result.add((source_id, specific))
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return result


def _validate_request_grounding_refs(
    value: Any,
    *,
    request_schema: Mapping[str, Any],
    instance: Mapping[str, Any],
    where: str,
) -> None:
    if not isinstance(value, list) or not value:
        raise IVCError(f"{where}.request_grounding_refs must be a non-empty array")
    available = _evidence_ref_pairs(request_schema)
    request_domain = instance.get("request_domain")
    if isinstance(request_domain, Mapping):
        available.update(_evidence_ref_pairs(request_domain))
    seen: set[tuple[str, str]] = set()
    for index, ref in enumerate(value):
        if not isinstance(ref, Mapping) or set(ref) != {
            "source_id",
            "specific_reference",
        }:
            raise IVCError(
                f"{where}.request_grounding_refs[{index}] must contain source_id "
                "and specific_reference only"
            )
        pair = (ref.get("source_id"), ref.get("specific_reference"))
        if not all(isinstance(item, str) and item.strip() for item in pair):
            raise IVCError(
                f"{where}.request_grounding_refs[{index}] is invalid"
            )
        typed_pair = (str(pair[0]), str(pair[1]))
        if typed_pair in seen:
            raise IVCError(f"{where}.request_grounding_refs contains a duplicate")
        if typed_pair not in available:
            raise IVCError(
                f"{where}.request_grounding_refs[{index}] does not resolve to "
                "sealed-schema or selected-scene calibration evidence"
            )
        seen.add(typed_pair)


def _reject_untrusted_suite_material(value: Any, *, where: str = "suite") -> None:
    forbidden_keys = {
        "binding_id",
        "code",
        "python",
        "script",
        "task_id",
        "task_plan",
        "task_dispatch",
        "driver",
        "repair",
        "verdict",
        "passed",
        "expected_pass",
        "expected_outcome",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in forbidden_keys:
                raise IVCError(f"{where} contains forbidden field {key!r}")
            _reject_untrusted_suite_material(child, where=f"{where}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_untrusted_suite_material(child, where=f"{where}[{index}]")
    elif isinstance(value, str) and any(
        token in value.lower()
        for token in ("```python", "__import__(", "exec(", "eval(")
    ):
        raise IVCError(f"{where} contains executable code material")


def _audit_capability_validation_case(
    case: Any,
    *,
    index: int,
    package: Any,
    capabilities: Mapping[str, Mapping[str, Any]],
    instances: Mapping[str, Mapping[str, Any]],
    guards: Mapping[str, Mapping[str, Any]],
    semantic_reference_contracts: Mapping[str, Mapping[str, Any]],
    seen_cases: set[str],
    roles: dict[tuple[str, str], int],
    requests: dict[tuple[str, str], Any],
) -> dict[str, Any]:
    """Audit one case after suite-level structure has been accepted."""

    where = f"cases[{index}]"
    if not isinstance(case, Mapping):
        raise IVCError(f"{where} must be an object")
    if set(case) != _IVC_CASE_FIELDS:
        raise IVCError(
            f"{where} fields are invalid; missing={sorted(_IVC_CASE_FIELDS - set(case))}, "
            f"extra={sorted(set(case) - _IVC_CASE_FIELDS)}"
        )
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip() or case_id in seen_cases:
        raise IVCError(f"{where}.case_id is invalid or duplicated")
    seen_cases.add(case_id)
    role = case.get("case_role")
    if role not in IVC_CASE_ROLES:
        raise IVCError(f"{where}.case_role must be nominal or calibrated_boundary")
    capability_id = case.get("capability_id")
    capability = capabilities.get(capability_id) if isinstance(capability_id, str) else None
    if capability is None:
        raise IVCError(f"{where}.capability_id references an unknown capability")
    method_name = capability.get("method_name", capability.get("method"))
    if case.get("method_name") != method_name:
        raise IVCError(f"{where}.method_name differs from the sealed design")
    if not _same_criteria(case, capability, where=where):
        raise IVCError(f"{where} changes sealed criterion or numeric values")
    request = case.get("request")
    sealed_request_schema = capability.get("request_schema", {})
    try:
        validate_schema_value(
            request,
            sealed_request_schema,
            path=f"{where}.request",
        )
    except CapabilityProtocolError as exc:
        raise IVCError(str(exc)) from None
    instance_id = case.get("instance_id")
    if not isinstance(instance_id, str) or instance_id not in instances:
        raise IVCError(f"{where}.instance_id is not a supplied private instance")
    instance = instances[instance_id]
    _validate_authored_request_context(
        request,
        instance=instance,
        sealed_schema=sealed_request_schema,
        where=where,
    )
    _validate_request_grounding_refs(
        case.get("request_grounding_refs"),
        request_schema=sealed_request_schema,
        instance=instance,
        where=where,
    )
    declared_capability = instance.get("capability_id")
    if declared_capability is not None and declared_capability != capability_id:
        raise IVCError(f"{where} changes the private instance capability context")
    declared_role = instance.get("case_role")
    if declared_role is not None and declared_role != role:
        raise IVCError(f"{where} changes the private instance role")
    guard_ids = case.get("guard_ids")
    if (
        not isinstance(guard_ids, list)
        or not guard_ids
        or any(not isinstance(item, str) or item not in guards for item in guard_ids)
    ):
        raise IVCError(f"{where}.guard_ids references invalid private guards")
    mandatory_guards = instance.get("guard_ids")
    if not isinstance(mandatory_guards, list) or not mandatory_guards:
        raise IVCError(f"{where} selected private instance has no mandatory guards")
    if guard_ids != mandatory_guards:
        raise IVCError(f"{where}.guard_ids changes mandatory private guards")
    for field in ("repetitions", "timeout_sim_s"):
        if field not in instance or case.get(field) != instance.get(field):
            raise IVCError(f"{where}.{field} changes private execution settings")
    repetitions = case.get("repetitions")
    timeout_sim_s = case.get("timeout_sim_s")
    if (
        isinstance(repetitions, bool)
        or not isinstance(repetitions, int)
        or repetitions <= 0
    ):
        raise IVCError(f"{where}.repetitions must be a positive integer")
    if (
        isinstance(timeout_sim_s, bool)
        or not isinstance(timeout_sim_s, (int, float))
        or not math.isfinite(float(timeout_sim_s))
        or float(timeout_sim_s) <= 0.0
    ):
        raise IVCError(f"{where}.timeout_sim_s must be positive and finite")
    criterion = _criterion_list(capability, where="sealed capability")[0]
    scene_path, scene_entities = _scene_audit_inputs(
        package,
        instance,
        where=where,
    )
    try:
        binding = audit_inline_measurement_binding(
            case.get("measurement_binding"),
            criterion=criterion,
            request_schema=sealed_request_schema,
            scene_path=scene_path,
            scene_entities=scene_entities,
        )
    except MeasurementOperatorError as exc:
        raise IVCError(f"{where}: {exc}") from None
    evaluation_mode = measurement_operator_evaluation_mode(binding.get("kind"))
    if evaluation_mode == "trusted_criterion_verdict":
        kind = binding.get("kind")
        reference = (
            semantic_reference_contracts.get(kind)
            if isinstance(kind, str)
            else None
        )
        package_robot_id = _package_identity(package).get(
            "robot_configuration_id"
        )
        if not (
            isinstance(reference, Mapping)
            and package_robot_id == reference["robot_configuration_id"]
            and capability_id == reference["capability_id"]
            and capability_execution_contract(capability)
            == reference["execution_contract"]
        ):
            raise IVCError(
                f"{where} selects fixed semantic operator {kind!r} outside "
                "its exact SO-101/Go2 worked-reference robot, capability, "
                "request schema, and criterion contract. The complete "
                "reference is a design aid; use a generic numeric operator "
                "for a transfer capability"
            )
    else:
        temporal = criterion.get("temporal")
        aggregation = criterion.get("aggregation")
        temporal_kind = temporal.get("kind") if isinstance(temporal, Mapping) else None
        aggregation_kind = (
            aggregation.get("kind") if isinstance(aggregation, Mapping) else None
        )
        if not (
            isinstance(temporal_kind, str)
            and temporal_kind in NUMERIC_CRITERION_TEMPORAL_KINDS
        ):
            raise IVCError(
                f"{where} sealed temporal criterion {temporal_kind!r} cannot be "
                "expressed by this numeric trusted operator"
            )
        if aggregation_kind not in NUMERIC_CRITERION_AGGREGATION_KINDS:
            raise IVCError(
                f"{where} sealed aggregation criterion {aggregation_kind!r} cannot be "
                "expressed by this numeric trusted operator"
            )
    key = (capability_id, role)
    roles[key] = roles.get(key, 0) + 1
    requests[key] = json_copy(request, label=f"{where}.request")
    canonical_case = _normalise_case_criteria(case, capability)
    canonical_case["measurement_binding"] = binding
    return canonical_case


def validate_capability_validation_suite(
    suite: Mapping[str, Any],
    *,
    package: Any,
    design: Mapping[str, Any],
    private_inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit exact two-case-per-capability coverage and criterion copying."""

    if not isinstance(suite, Mapping):
        raise IVCError("validation suite must be an object")
    if "artifact_header" in suite:
        raise IVCError(
            "artifact_header wrapper is forbidden; move every artifact_header "
            "field unchanged to the validation suite top level alongside cases"
        )
    _reject_untrusted_suite_material(suite)
    ids = _package_identity(package)
    for key, expected in {
        "artifact_type": "capability_validation_suite",
        "schema_version": "2.0",
        "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
        **ids,
    }.items():
        if expected is not None and suite.get(key) != expected:
            raise IVCError(f"{key} must equal {expected!r}")
    if suite.get("whole_suite_aggregation") != {"kind": "all_cases"}:
        raise IVCError("whole_suite_aggregation must require all cases")

    private = _copy_private_inputs(
        _private_inputs_from_package(package)
        if private_inputs is None
        else private_inputs
    )
    instances = _id_map(private.get("instances"), field="instances", id_field="instance_id")
    guards = _id_map(private.get("guards"), field="guards", id_field="guard_id")
    capabilities = _design_map(design)
    semantic_reference_contracts = _semantic_reference_contracts()
    cases = suite.get("cases")
    expected_count = 2 * len(capabilities)
    if not isinstance(cases, list) or len(cases) != expected_count:
        raise IVCError(f"cases must contain exactly two cases per capability ({expected_count})")

    seen_cases: set[str] = set()
    roles: dict[tuple[str, str], int] = {}
    requests: dict[tuple[str, str], Any] = {}
    canonical_cases: list[dict[str, Any]] = []
    case_errors: list[str] = []
    for index, case in enumerate(cases):
        where = f"cases[{index}]"
        try:
            canonical_case = _audit_capability_validation_case(
                case,
                index=index,
                package=package,
                capabilities=capabilities,
                instances=instances,
                guards=guards,
                semantic_reference_contracts=semantic_reference_contracts,
                seen_cases=seen_cases,
                roles=roles,
                requests=requests,
            )
        except IVCError as exc:
            case_id = case.get("case_id") if isinstance(case, Mapping) else None
            case_label = (
                f"{where} (case_id={case_id!r})"
                if isinstance(case_id, str) and case_id.strip()
                else where
            )
            detail = str(exc)
            prefixed = f"{where}: "
            if detail.startswith(prefixed):
                detail = detail[len(prefixed) :]
            case_errors.append(f"{case_label}: {detail}")
        else:
            canonical_cases.append(canonical_case)

    if case_errors:
        details = "\n".join(f"- {error}" for error in case_errors)
        raise IVCError(
            f"validation suite case audit found {len(case_errors)} error(s):\n{details}"
        )

    for capability_id in capabilities:
        for role in IVC_CASE_ROLES:
            if roles.get((capability_id, role), 0) != 1:
                raise IVCError(f"capability {capability_id!r} must have exactly one {role} case")
        if _same_json_value(
            requests[(capability_id, "nominal")],
            requests[(capability_id, "calibrated_boundary")],
        ):
            raise IVCError(
                f"capability {capability_id!r} nominal and calibrated-boundary requests must differ"
            )
    result = json_copy(dict(suite), label="validation_suite")
    result["cases"] = canonical_cases
    return result


def build_ivc_inputs(
    *,
    package: Any,
    design: Mapping[str, Any],
    private_inputs: Mapping[str, Any],
    examples: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build IVC inputs while enforcing implementation blindness."""

    copied_private = _project_private_inputs_for_model(private_inputs)
    selected_examples = list(examples) if examples else load_ivc_worked_references()
    worked_references = _copy_private_inputs(
        {"examples": selected_examples}
    ).get("examples", [])
    identity = _package_identity(package)
    capability_count = len(_design_map(design))
    raw_instances = _id_map(
        private_inputs.get("instances"),
        field="instances",
        id_field="instance_id",
    )
    scenes: dict[str, dict[str, Any]] = {}
    for instance in raw_instances.values():
        entrypoint = str(instance["scene_entrypoint"])
        if entrypoint in scenes:
            continue
        scene_path, explicit_entities = _scene_audit_inputs(
            package,
            instance,
            where=f"scene_catalog[{entrypoint}]",
        )
        try:
            entities = (
                dict(explicit_entities)
                if explicit_entities is not None
                else inspect_scene_entities(scene_path)  # type: ignore[arg-type]
            )
        except MeasurementOperatorError as exc:
            raise IVCError(str(exc)) from None
        scenes[entrypoint] = {
            "scene_entrypoint": entrypoint,
            "entities": entities,
        }

    if isinstance(package, Mapping):
        package_tasks = package.get("tasks", [])
        package_sources = package.get("sources", [])
    else:
        package_tasks = getattr(package, "tasks", ())
        package_sources = getattr(package, "sources", ())
    source_task_lineage = {
        "sources": json_copy(list(package_sources), label="package sources"),
        "tasks": [
            {
                key: json_copy(value, label=f"package task {key}")
                for key, value in task.items()
                if key in {"task_id", "title", "description", "scoring", "source_refs"}
            }
            for task in package_tasks
            if isinstance(task, Mapping)
        ],
    }
    return {
        "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
        "artifact_header": {
            "artifact_type": "capability_validation_suite",
            "schema_version": "2.0",
            "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
            **identity,
            "whole_suite_aggregation": {"kind": "all_cases"},
        },
        "validator_contract": {
            "case_count": 2 * capability_count,
            "case_roles_per_capability": list(IVC_CASE_ROLES),
            "case_required_fields": [
                "case_id",
                "case_role",
                "capability_id",
                "method_name",
                "request",
                "request_grounding_refs",
                "instance_id",
                "measurement_binding",
                "guard_ids",
                "repetitions",
                "timeout_sim_s",
                "criteria",
            ],
            "copy_method_name_from_sealed_design": True,
            "copy_criteria_from_sealed_design_exactly": True,
            "ivc_authors_complete_request_for_each_case": True,
            "validate_authored_request_against_sealed_request_schema": True,
            "validate_authored_request_against_selected_instance_request_domain_when_supplied": True,
            "request_domain_format": "closed capability-v2 request schema",
            "request_grounding_refs_must_resolve": True,
            "copy_instance_execution_fields_when_present": [
                "repetitions",
                "timeout_sim_s",
            ],
            "instance_and_guard_ids_must_be_supplied": True,
            "measurement_binding_is_ivc_authored_inline": True,
            "generic_numeric_criterion_contract": {
                "temporal": {"kind": "terminal_state"},
                "aggregation": {"kind": "single_trial"},
            },
            "fixed_semantic_operators_require_exact_worked_reference_contract": True,
            "binding_id_is_forbidden": True,
            "nominal_and_boundary_requests_must_differ": True,
            "candidate_never_receives_measurement_criteria_or_guards": True,
        },
        "sealed_capability_design": json_copy(dict(design), label="sealed_capability_design"),
        "source_task_lineage": source_task_lineage,
        "private_instances": copied_private.get("instances", {}),
        "trusted_measurement_examples": copied_private.get("bindings", {}),
        "private_guards": copied_private.get("guards", {}),
        "measurement_operator_catalog": _scoped_measurement_operator_catalog(
            package=package,
            design=design,
        ),
        "scene_entity_catalog": {
            "asset_root": "read-only public_package/assets",
            "execute_python_environment": "AUTOADAPTER_PROBE_PUBLIC_PACKAGE",
            "scenes": [scenes[key] for key in sorted(scenes)],
        },
        "complete_so101_go2_worked_references": worked_references,
        "required_case_roles": list(IVC_CASE_ROLES),
    }


def _build_ivc_authoring_index(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Build a compact, exact index for the first IVC prompt.

    The complete private projection remains in ``ivc_inputs.json``.  This
    index fuses the sealed contract with its request-path index so capability
    semantics appear only once.  Repeated schema evidence prose and task-
    support rationales remain queryable in the raw input.  Every exact
    criterion, closed request constraint, legal operator signature, private
    instance, and declared frame alias required for authoring remains inline.
    """

    def schema_nodes(
        schema: Any,
        *,
        path: str = "request",
    ) -> tuple[list[str], list[tuple[str, Mapping[str, Any]]]]:
        leaves: list[str] = []
        addressable: list[tuple[str, Mapping[str, Any]]] = []
        if not isinstance(schema, Mapping):
            return leaves, addressable
        properties = schema.get("properties")
        if schema.get("type") == "object" and isinstance(properties, Mapping):
            for name in sorted(properties):
                child = properties[name]
                if not isinstance(child, Mapping):
                    continue
                child_path = f"{path}.{name}"
                addressable.append((child_path, child))
                child_leaves, child_nodes = schema_nodes(child, path=child_path)
                leaves.extend(child_leaves)
                addressable.extend(child_nodes)
            return leaves, addressable
        leaves.append(path)
        return leaves, addressable

    def compact_request_schema(schema: Any) -> Any:
        """Copy an exact schema while de-duplicating prose and evidence refs.

        Evidence pairs are projected once beside the schema.  Descriptions
        remain available in the raw sealed design; all machine-enforced
        schema keywords, including unknown future keywords, are retained.
        """

        if not isinstance(schema, Mapping):
            return json_copy(schema, label="sealed request schema value")
        compact: dict[str, Any] = {}
        for name, value in schema.items():
            if name in {"description", "evidence_refs"}:
                continue
            if name == "properties" and isinstance(value, Mapping):
                compact[name] = {
                    str(property_name): compact_request_schema(property_schema)
                    for property_name, property_schema in value.items()
                }
            elif name == "items" and isinstance(value, Mapping):
                compact[name] = compact_request_schema(value)
            elif name in {"allOf", "anyOf", "oneOf"} and isinstance(value, list):
                compact[name] = [
                    compact_request_schema(alternative) for alternative in value
                ]
            elif name == "additionalProperties" and isinstance(value, Mapping):
                compact[name] = compact_request_schema(value)
            else:
                compact[name] = json_copy(
                    value, label=f"sealed request schema {name}"
                )
        return compact

    private_document = inputs.get("private_instances")
    if isinstance(private_document, Mapping):
        raw_instances = private_document.get("instances")
    else:
        raw_instances = private_document
    if not isinstance(raw_instances, list):
        raise IVCError("private_instances.instances must be an array")
    instance_records: list[dict[str, Any]] = []
    scene_entrypoints: set[str] = set()
    for index, record in enumerate(raw_instances):
        if not isinstance(record, Mapping):
            raise IVCError(f"private_instances.instances[{index}] must be an object")
        instance_id = record.get("instance_id")
        scene_entrypoint = record.get("scene_entrypoint")
        if not isinstance(instance_id, str) or not isinstance(scene_entrypoint, str):
            raise IVCError("private instance index requires exact IDs and scenes")
        scene_entrypoints.add(scene_entrypoint)
        indexed_record = {
            "instance_id": instance_id,
            "context_namespace": record.get("context_namespace"),
            "scene_entrypoint": scene_entrypoint,
            "mandatory_guard_ids": json_copy(
                record.get("guard_ids", []),
                label=f"private instance {instance_id} guard_ids",
            ),
            "repetitions": record.get("repetitions"),
            "timeout_sim_s": record.get("timeout_sim_s"),
        }
        for optional_field in ("capability_id", "case_role", "request_domain"):
            if record.get(optional_field) is not None:
                indexed_record[optional_field] = json_copy(
                    record[optional_field],
                    label=f"private instance {instance_id} {optional_field}",
                )
        instance_records.append(indexed_record)

    shared_instance_fields: dict[str, Any] = {}
    for field in (
        "context_namespace",
        "mandatory_guard_ids",
        "repetitions",
        "timeout_sim_s",
    ):
        values = [record.get(field) for record in instance_records]
        if values and all(value == values[0] for value in values[1:]):
            shared_instance_fields[field] = values[0]
            for record in instance_records:
                record.pop(field, None)

    design = inputs.get("sealed_capability_design")
    if not isinstance(design, Mapping):
        raise IVCError("sealed_capability_design authoring input must be an object")
    authoring_capabilities: list[dict[str, Any]] = []
    compatibility_by_capability: dict[str, Any] = {}
    criterion_units: set[str] = set()
    declared_frames: set[str] = set()
    operator_document = inputs.get("measurement_operator_catalog")
    raw_operators = (
        operator_document.get("operators")
        if isinstance(operator_document, Mapping)
        else None
    )
    if not isinstance(raw_operators, list):
        raise IVCError("measurement_operator_catalog.operators must be an array")
    raw_operator_by_kind = {
        str(operator["kind"]): operator
        for operator in raw_operators
        if isinstance(operator, Mapping) and isinstance(operator.get("kind"), str)
    }
    for capability in capability_records(design):
        criteria = capability.get("criteria")
        if not (
            isinstance(criteria, list)
            and len(criteria) == 1
            and isinstance(criteria[0], Mapping)
        ):
            raise IVCError("sealed capability requires exactly one criterion")
        criterion = criteria[0]
        unit = criterion.get("unit")
        if isinstance(unit, str):
            criterion_units.add(unit)
        request_schema = capability.get("request_schema")
        leaves, request_nodes = schema_nodes(request_schema)
        for _path, node_schema in request_nodes:
            frame = node_schema.get("frame")
            if isinstance(frame, str) and frame.strip():
                declared_frames.add(frame)
        authoring_capability = {
            field: json_copy(
                capability[field],
                label=f"sealed capability {capability.get('capability_id')} {field}",
            )
            for field in (
                "capability_id",
                "method_name",
                "effect",
                "preconditions",
                "temporal_semantics",
                "invariants",
                "failure_behavior",
                "criteria",
            )
            if field in capability
        }
        authoring_capability.update(
            {
                "request_schema": compact_request_schema(request_schema),
                "rooted_request_paths": leaves,
                "allowed_request_grounding_refs_from_sealed_schema": [
                    {
                        "source_id": source_id,
                        "specific_reference": specific_reference,
                    }
                    for source_id, specific_reference in sorted(
                        _evidence_ref_pairs(request_schema)
                    )
                ],
            }
        )
        authoring_capabilities.append(authoring_capability)
        capability_id = capability.get("capability_id")
        if not isinstance(capability_id, str):
            raise IVCError("sealed capability requires a string capability_id")
        compatible_operators: dict[str, Any] = {}
        if not isinstance(request_schema, Mapping):
            raise IVCError(
                f"sealed capability {capability_id!r} requires a request schema"
            )
        temporal = criterion.get("temporal")
        aggregation = criterion.get("aggregation")
        temporal_kind = (
            temporal.get("kind") if isinstance(temporal, Mapping) else None
        )
        aggregation_kind = (
            aggregation.get("kind")
            if isinstance(aggregation, Mapping)
            else None
        )
        numeric_criterion_executable = (
            temporal_kind in NUMERIC_CRITERION_TEMPORAL_KINDS
            and aggregation_kind in NUMERIC_CRITERION_AGGREGATION_KINDS
        )
        for operator in raw_operators:
            if not isinstance(operator, Mapping):
                continue
            kind = operator.get("kind")
            if not isinstance(kind, str):
                continue
            execution_scope = operator.get("execution_scope")
            if (
                isinstance(execution_scope, Mapping)
                and execution_scope.get("capability_id") != capability_id
            ):
                continue
            if (
                operator.get("evaluation_mode") == "numeric_measurement"
                and not numeric_criterion_executable
            ):
                continue
            projection = measurement_operator_authoring_compatibility(
                kind,
                criterion=criterion,
                request_schema=request_schema,
            )
            if projection is not None:
                compatible_operators[kind] = projection
        compatibility_by_capability[capability_id] = {
            "criterion_contract": {
                field: json_copy(
                    criterion[field],
                    label=f"sealed capability {capability_id} criterion {field}",
                )
                for field in (
                    "metric",
                    "unit",
                    "comparator",
                    "threshold",
                    "temporal",
                    "aggregation",
                )
                if field in criterion
            },
            "structurally_compatible_operator_kinds": compatible_operators,
        }

    task_support_by_capability: dict[str, list[str]] = {}
    raw_task_support = design.get("task_support", [])
    if not isinstance(raw_task_support, list):
        raise IVCError("sealed capability task_support must be an array")
    for index, support in enumerate(raw_task_support):
        if not isinstance(support, Mapping):
            raise IVCError(f"sealed capability task_support[{index}] must be an object")
        capability_id = support.get("capability_id")
        task_id = support.get("task_id")
        if not isinstance(capability_id, str) or not isinstance(task_id, str):
            raise IVCError("sealed task_support requires exact task and capability IDs")
        task_support_by_capability.setdefault(capability_id, []).append(task_id)
    for task_ids in task_support_by_capability.values():
        task_ids.sort()

    invocation_abi = json_copy(
        design.get("invocation_abi"), label="sealed design invocation_abi"
    )

    measurement_binding_kind_catalog: dict[str, Any] = {}
    for operator in raw_operators:
        if not (
            isinstance(operator, Mapping)
            and isinstance(operator.get("kind"), str)
            and isinstance(operator.get("output_units"), list)
            and criterion_units.intersection(
                unit
                for unit in operator["output_units"]
                if isinstance(unit, str)
            )
        ):
            continue
        parameter_schema = operator.get("parameter_schema")
        properties = (
            parameter_schema.get("properties")
            if isinstance(parameter_schema, Mapping)
            else None
        )
        required_parameters = (
            set(parameter_schema.get("required", []))
            if isinstance(parameter_schema, Mapping)
            else set()
        )
        compact_properties = (
            {
                str(name): specification.get("type")
                for name, specification in properties.items()
                if isinstance(name, str) and isinstance(specification, Mapping)
            }
            if isinstance(properties, Mapping)
            else {}
        )
        entity_parameters = operator.get("entity_parameters", {})
        request_value_types = operator.get("request_value_types", {})
        parameter_signatures: dict[str, Any] = {}
        for name, parameter_type in compact_properties.items():
            signature = parameter_type
            if isinstance(entity_parameters, Mapping) and name in entity_parameters:
                signature = f"entity:{entity_parameters[name]}"
            elif isinstance(request_value_types, Mapping) and name in request_value_types:
                signature = f"request_path:{request_value_types[name]}"
            if name not in required_parameters:
                signature = f"optional:{signature}"
            parameter_signatures[name] = signature

        compact_spec: dict[str, Any] = {
            "allowed_binding_units": json_copy(
                operator.get("output_units", []),
                label="measurement operator output units",
            ),
            "parameter_types_not_values": parameter_signatures,
        }
        evaluation_mode = operator.get("evaluation_mode")
        if evaluation_mode != "numeric_measurement":
            compact_spec["framework_evaluation_mode_not_a_binding_field"] = (
                evaluation_mode
            )
        operator_kind = str(operator["kind"])
        measurement_binding_kind_catalog[operator_kind] = compact_spec

    scene_document = inputs.get("scene_entity_catalog")
    raw_scenes = (
        scene_document.get("scenes")
        if isinstance(scene_document, Mapping)
        else None
    )
    if not isinstance(raw_scenes, list):
        raise IVCError("scene_entity_catalog.scenes must be an array")
    alias_groups: dict[
        tuple[tuple[tuple[str, str], ...], tuple[str, ...]], list[str]
    ] = {}
    typed_scene_records: list[dict[str, Any]] = []
    entity_fields = {
        "body": "bodies",
        "site": "sites",
        "joint": "joints",
        "geom": "geoms",
    }
    for scene in raw_scenes:
        if (
            not isinstance(scene, Mapping)
            or scene.get("scene_entrypoint") not in scene_entrypoints
        ):
            continue
        entities = scene.get("entities")
        if not isinstance(entities, Mapping):
            raise IVCError("scene entity index requires an entities object")
        entity_names: dict[str, list[str]] = {}
        for entity_type, source_field in entity_fields.items():
            raw_names = entities.get(source_field)
            if not isinstance(raw_names, list) or any(
                not isinstance(name, str) for name in raw_names
            ):
                raise IVCError(
                    f"scene entity index requires a valid {source_field} catalog"
                )
            entity_names[entity_type] = sorted(set(raw_names))
        raw_joint_units = entities.get("joint_units")
        if raw_joint_units is None:
            raw_joint_units = {}
        if not isinstance(raw_joint_units, Mapping) or any(
            not isinstance(name, str) or unit not in {"rad", "m"}
            for name, unit in raw_joint_units.items()
        ):
            raise IVCError("scene entity index requires a valid joint_units catalog")
        raw_joint_ranges = entities.get("joint_ranges")
        if raw_joint_ranges is None:
            raw_joint_ranges = {}
        if not isinstance(raw_joint_ranges, Mapping):
            raise IVCError("scene entity index requires a valid joint_ranges catalog")
        typed_scene_records.append(
            {
                "scene_entrypoint": str(scene["scene_entrypoint"]),
                "entity_names": entity_names,
                "joint_names_by_unit": {
                    unit: sorted(
                        name
                        for name, declared_unit in raw_joint_units.items()
                        if declared_unit == unit
                    )
                    for unit in ("rad", "m")
                },
                "finite_range_joint_names_by_unit": {
                    unit: sorted(
                        name
                        for name, declared_unit in raw_joint_units.items()
                        if declared_unit == unit
                        and isinstance(raw_joint_ranges.get(name), list)
                        and len(raw_joint_ranges[name]) == 2
                        and all(
                            isinstance(value, (int, float))
                            and not isinstance(value, bool)
                            and math.isfinite(float(value))
                            for value in raw_joint_ranges[name]
                        )
                        and float(raw_joint_ranges[name][0])
                        < float(raw_joint_ranges[name][1])
                    )
                    for unit in ("rad", "m")
                },
                "frame_aliases": dict(entities.get("frame_aliases", {})),
                "body_parent_names": dict(
                    entities.get("body_parent_names", {})
                ),
                "body_joint_counts": dict(
                    entities.get("body_joint_counts", {})
                ),
                "body_descendant_joint_counts": dict(
                    entities.get("body_descendant_joint_counts", {})
                ),
                "site_body_names": dict(
                    entities.get("site_body_names", {})
                ),
            }
        )
        aliases = entities.get("frame_aliases") if isinstance(entities, Mapping) else None
        resolved = {
            frame: aliases[frame]
            for frame in sorted(declared_frames)
            if isinstance(aliases, Mapping)
            and isinstance(aliases.get(frame), str)
            and str(aliases[frame]).strip()
        }
        unresolved = tuple(sorted(declared_frames - set(resolved)))
        group_key = (tuple(sorted(resolved.items())), unresolved)
        alias_groups.setdefault(group_key, []).append(str(scene["scene_entrypoint"]))
    scene_frame_aliases = [
        {
            "scene_entrypoints": sorted(entrypoints),
            "declared_frame_aliases": dict(resolved_items),
            "unresolved_declared_frames": list(unresolved),
        }
        for (resolved_items, unresolved), entrypoints in sorted(
            alias_groups.items(), key=lambda item: item[1]
        )
    ]

    def shared_names(
        records: Sequence[Mapping[str, Any]], group: str, field: str
    ) -> list[str]:
        collections = [
            set(record[group][field])
            for record in records
            if isinstance(record.get(group), Mapping)
        ]
        return sorted(set.intersection(*collections)) if collections else []

    shared_entity_names = {
        entity_type: shared_names(
            typed_scene_records, "entity_names", entity_type
        )
        for entity_type in entity_fields
    }
    shared_joint_names_by_unit = {
        unit: shared_names(typed_scene_records, "joint_names_by_unit", unit)
        for unit in ("rad", "m")
    }
    shared_finite_range_joint_names_by_unit = {
        unit: shared_names(
            typed_scene_records,
            "finite_range_joint_names_by_unit",
            unit,
        )
        for unit in ("rad", "m")
    }

    def scene_supports_frame_binding(
        kind: str,
        projection: Mapping[str, Any],
        record: Mapping[str, Any],
    ) -> bool:
        request_frames = projection.get("request_frames")
        if not request_frames:
            return True
        aliases = record["frame_aliases"]
        parent_names = record["body_parent_names"]
        joint_counts = record["body_joint_counts"]
        descendant_joint_counts = record["body_descendant_joint_counts"]
        site_body_names = record["site_body_names"]
        bodies = set(record["entity_names"]["body"])

        def ancestor_path(body_name: str) -> list[str] | None:
            path: list[str] = []
            current = body_name
            visited: set[str] = set()
            while current != "world":
                if current in visited:
                    return None
                visited.add(current)
                path.append(current)
                parent = parent_names.get(current)
                if not isinstance(parent, str) or not parent:
                    return None
                current = parent
            path.append("world")
            return path

        if kind in {
            "final_site_frame_xyz_position_error",
            "site_frame_xyz_directional_displacement",
            "accumulated_site_frame_axis_arc_angle_error",
        }:
            measured_bodies = {
                site_body_names[site]
                for site in record["entity_names"]["site"]
                if isinstance(site_body_names.get(site), str)
            }
        else:
            measured_bodies = bodies

        for frame in request_frames:
            reference = aliases.get(frame)
            if not isinstance(reference, str) or reference not in bodies:
                continue
            if reference != "world" and (
                not isinstance(descendant_joint_counts.get(reference), int)
                or descendant_joint_counts[reference] <= 0
            ):
                continue
            reference_path = ancestor_path(reference)
            if reference_path is None:
                continue
            reference_ancestors = set(reference_path)
            for measured_body in measured_bodies:
                measured_path = ancestor_path(measured_body)
                if measured_path is None:
                    continue
                common = next(
                    (
                        body
                        for body in measured_path
                        if body in reference_ancestors
                    ),
                    None,
                )
                if common is None:
                    continue
                relative_bodies = measured_path[: measured_path.index(common)]
                relative_bodies.extend(
                    reference_path[: reference_path.index(common)]
                )
                if relative_bodies and all(
                    isinstance(joint_counts.get(body), int)
                    for body in relative_bodies
                ) and sum(
                    int(joint_counts[body]) for body in relative_bodies
                ) > 0:
                    return True
        return False

    for compatibility in compatibility_by_capability.values():
        operators = compatibility["structurally_compatible_operator_kinds"]
        for kind, projection in list(operators.items()):
            raw_operator = raw_operator_by_kind[kind]
            parameter_schema = raw_operator.get("parameter_schema")
            required_parameters = (
                set(parameter_schema.get("required", []))
                if isinstance(parameter_schema, Mapping)
                else set()
            )
            entity_types = {
                field: entity_type
                for field, entity_type in projection.get(
                    "entity_parameter_types", {}
                ).items()
                if field in required_parameters
            }
            joint_units = {
                field: unit
                for field, unit in projection.get(
                    "joint_parameter_units", {}
                ).items()
                if field in required_parameters
            }

            def scene_supports_signature(record: Mapping[str, Any]) -> bool:
                names_by_type = record["entity_names"]
                for field, declared_type in entity_types.items():
                    base_type = str(declared_type).split("_", 1)[0]
                    required_count = 1
                    if kind in {
                        "final_geom_pair_distance_error",
                        "final_geom_pair_distance",
                    } and field in {"geom_a_name", "geom_b_name"}:
                        required_count = 2
                    if kind in {
                        "final_body_frame_xyz_position_error",
                        "body_frame_xyz_directional_displacement",
                    } and field in {"body_name", "reference_body_name"}:
                        required_count = 2
                    if len(names_by_type.get(base_type, [])) < required_count:
                        return False
                for _field, unit in joint_units.items():
                    if not record["joint_names_by_unit"].get(str(unit)):
                        return False
                target_modes = projection.get("joint_target_path_modes", {})
                if target_modes and set(target_modes.values()) == {
                    "bounded_dimensionless"
                }:
                    required_unit = str(
                        next(iter(joint_units.values()))
                    )
                    if not record["finite_range_joint_names_by_unit"].get(
                        required_unit
                    ):
                        return False
                if not scene_supports_frame_binding(kind, projection, record):
                    return False
                return True

            if not any(
                scene_supports_signature(record)
                for record in typed_scene_records
            ):
                del operators[kind]
    compressed_compatibility_by_capability: dict[str, Any] = {}
    for capability_id, compatibility in compatibility_by_capability.items():
        operators = compatibility["structurally_compatible_operator_kinds"]
        paths_by_value_type: dict[str, set[str]] = {}
        joint_units_by_kind: dict[str, Any] = {}
        joint_target_path_modes_by_kind: dict[str, Any] = {}
        for kind, projection in operators.items():
            raw_operator = raw_operator_by_kind[kind]
            request_value_types = raw_operator.get("request_value_types", {})
            for field, paths in projection.get("request_path_candidates", {}).items():
                expected_type = (
                    request_value_types.get(field)
                    if isinstance(request_value_types, Mapping)
                    else None
                )
                if isinstance(expected_type, str):
                    paths_by_value_type.setdefault(expected_type, set()).update(paths)
            if "joint_parameter_units" in projection:
                joint_units_by_kind[kind] = projection["joint_parameter_units"]
            if "joint_target_path_modes" in projection:
                joint_target_path_modes_by_kind[kind] = projection[
                    "joint_target_path_modes"
                ]
        compact_compatibility = {
            "structurally_compatible_operator_kinds": sorted(operators),
            "compatible_request_paths_by_value_type": {
                value_type: sorted(paths)
                for value_type, paths in sorted(paths_by_value_type.items())
            },
        }
        if joint_units_by_kind:
            compact_compatibility["joint_parameter_units_by_kind"] = (
                joint_units_by_kind
            )
        if joint_target_path_modes_by_kind:
            compact_compatibility["joint_target_path_modes_by_kind"] = (
                joint_target_path_modes_by_kind
            )
        compressed_compatibility_by_capability[capability_id] = compact_compatibility
    compatible_kind_union = {
        kind
        for compatibility in compressed_compatibility_by_capability.values()
        for kind in compatibility["structurally_compatible_operator_kinds"]
    }
    measurement_binding_kind_catalog = {
        kind: specification
        for kind, specification in measurement_binding_kind_catalog.items()
        if kind in compatible_kind_union
    }
    per_scene_entity_additions: list[dict[str, Any]] = []
    for record in sorted(
        typed_scene_records, key=lambda item: str(item["scene_entrypoint"])
    ):
        per_scene_entity_additions.append(
            {
                "scene_entrypoint": record["scene_entrypoint"],
                "entity_names": {
                    entity_type: sorted(
                        set(record["entity_names"][entity_type])
                        - set(shared_entity_names[entity_type])
                    )
                    for entity_type in entity_fields
                },
                "joint_names_by_unit": {
                    unit: sorted(
                        set(record["joint_names_by_unit"][unit])
                        - set(shared_joint_names_by_unit[unit])
                    )
                    for unit in ("rad", "m")
                },
                "finite_range_joint_names_by_unit": {
                    unit: sorted(
                        set(record["finite_range_joint_names_by_unit"][unit])
                        - set(shared_finite_range_joint_names_by_unit[unit])
                    )
                    for unit in ("rad", "m")
                },
            }
        )

    return {
        "raw_paths": {
            "design": "ivc_inputs.json::sealed_capability_design",
            "task_support": "ivc_inputs.json::sealed_capability_design.task_support",
            "instances": "ivc_inputs.json::private_instances.instances",
            "operators": "ivc_inputs.json::measurement_operator_catalog.operators",
            "scenes": "ivc_inputs.json::scene_entity_catalog.scenes",
        },
        "private_instance_records": instance_records,
        "private_instance_shared_execution_fields": shared_instance_fields,
        "sealed_authoring_contract": {
            "invocation_abi": invocation_abi,
            "capabilities": authoring_capabilities,
            "task_support_by_capability": task_support_by_capability,
        },
        "measurement_binding_contract": {
            "exact_fields": ["metric", "unit", "kind", "parameters"],
            "metric": "copy the sealed criterion metric exactly",
            "unit": "copy the sealed criterion unit exactly",
            "kind": (
                "choose one exact measurement_binding_kind_catalog key whose "
                "allowed_binding_units contains the criterion unit; never use "
                "framework evaluation metadata"
            ),
            "parameters": (
                "actual closed JSON values; replace signature markers rather than "
                "copying them"
            ),
            "forbidden_fields": [
                "operator",
                "mode",
                "evaluation_mode",
                "binding_id",
            ],
        },
        "operator_parameter_signature_legend": {
            "entity:<type>": "replace with exact selected-scene entity name or array",
            "request_path": "replace with a literal rooted request.* path",
            "request_path:<value_type>": (
                "replace with a literal rooted request.* path of that value type"
            ),
            "optional:<signature>": "omit or replace using the nested signature",
            "plain_json_type": "supply an actual JSON value of that type",
        },
        "measurement_binding_kind_catalog": measurement_binding_kind_catalog,
        "deterministic_structural_compatibility": {
            "scope": (
                "Framework-proven unit/request-schema compatibility only; IVC still "
                "chooses the semantically correct operator, instance, request, and "
                "scene entities. Join each allowed kind to "
                "measurement_binding_kind_catalog parameter signatures and then "
                "resolve request_path:<value_type> through the capability's grouped "
                "compatible paths; the final audit remains authoritative"
            ),
            "by_capability_id": compressed_compatibility_by_capability,
        },
        "scene_entity_type_index": {
            "reconstruction_rule": (
                "for each scene and field, exact names are shared_across_all_scenes "
                "union that scene's per_scene_additions"
            ),
            "shared_across_all_scenes": {
                "entity_names": shared_entity_names,
                "joint_names_by_unit": shared_joint_names_by_unit,
                "finite_range_joint_names_by_unit": (
                    shared_finite_range_joint_names_by_unit
                ),
            },
            "per_scene_additions": per_scene_entity_additions,
        },
        "scene_declared_frame_aliases": scene_frame_aliases,
    }


def _build_ivc_authoring_brief(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Return the single non-redundant payload embedded in the first turn."""

    return {
        "required_artifact_top_level_fields": json_copy(
            inputs["artifact_header"], label="IVC artifact header"
        ),
        "validator_contract": json_copy(
            inputs["validator_contract"], label="IVC validator contract"
        ),
        "authoring_index": _build_ivc_authoring_index(inputs),
    }


def _assert_ivc_authoring_is_executable(
    authoring_brief: Mapping[str, Any],
) -> None:
    authoring_index = authoring_brief.get("authoring_index")
    compatibility = (
        authoring_index.get("deterministic_structural_compatibility")
        if isinstance(authoring_index, Mapping)
        else None
    )
    by_capability = (
        compatibility.get("by_capability_id")
        if isinstance(compatibility, Mapping)
        else None
    )
    if not isinstance(by_capability, Mapping):
        raise IVCError("IVC authoring compatibility index is unavailable")
    unexecutable = sorted(
        capability_id
        for capability_id, record in by_capability.items()
        if not isinstance(record, Mapping)
        or not record.get("structurally_compatible_operator_kinds")
    )
    if unexecutable:
        raise IVCError(
            "sealed capability design has no structurally compatible trusted "
            f"measurement operator for capabilities {unexecutable}; do not call "
            "IVC for this design—rerun TGCD with a discriminating numeric criterion "
            "and a request schema that the trusted measurement DSL can express"
        )


@dataclass(frozen=True)
class IVCPhase:
    """Six-turn artifact workflow exposed for pipeline integration."""

    client: JsonGenerator
    package: Any
    design: Mapping[str, Any]
    private_inputs: Mapping[str, Any]
    examples: Sequence[Mapping[str, Any]] = ()
    callback: IVCEventCallback | None = None
    max_turns: int = IVC_ARTIFACT_TURNS
    reference_positive_control_hook: ReferencePositiveControlHook | None = None
    artifact_path: str | Path | None = None
    probe_budget: ProbeBudget = ProbeBudget(max_requests=None)

    def run(self) -> dict[str, Any]:
        return run_ivc(
            self.client,
            package=self.package,
            design=self.design,
            private_inputs=self.private_inputs,
            examples=self.examples,
            callback=self.callback,
            max_turns=self.max_turns,
            reference_positive_control_hook=self.reference_positive_control_hook,
            artifact_path=self.artifact_path,
            probe_budget=self.probe_budget,
        )


def run_ivc(
    client: JsonGenerator,
    *,
    package: Any,
    design: Mapping[str, Any],
    private_inputs: Mapping[str, Any] | None = None,
    examples: Sequence[Mapping[str, Any]] = (),
    callback: IVCEventCallback | None = None,
    max_turns: int = IVC_ARTIFACT_TURNS,
    max_model_attempts: int | None = None,
    reference_positive_control_hook: ReferencePositiveControlHook | None = None,
    artifact_path: str | Path | None = None,
    probe_budget: ProbeBudget = ProbeBudget(max_requests=None),
) -> dict[str, Any]:
    """Run the six-turn implementation-blind IVC artifact workflow."""

    if max_model_attempts is not None:
        max_turns = max_model_attempts
    if not isinstance(max_turns, int) or isinstance(max_turns, bool) or not 1 <= max_turns <= IVC_ARTIFACT_TURNS:
        raise IVCError("max_turns must be between one and six")
    supplied_private = (
        _private_inputs_from_package(package)
        if private_inputs is None
        else private_inputs
    )
    private = _copy_private_inputs(supplied_private)
    inputs = build_ivc_inputs(
        package=package,
        design=design,
        private_inputs=private,
        examples=examples,
    )
    authoring_brief_document = _build_ivc_authoring_brief(inputs)
    _assert_ivc_authoring_is_executable(authoring_brief_document)

    def validate_and_calibrate(artifact: Mapping[str, Any], *, turn: int) -> dict[str, Any]:
        canonical = validate_capability_validation_suite(
            artifact,
            package=package,
            design=design,
            private_inputs=private,
        )
        if reference_positive_control_hook is not None:
            try:
                positive_control = run_reference_positive_control(
                    reference_positive_control_hook,
                    capability_design=design,
                    validation_suite=canonical,
                )
            except Exception:
                # Reference paths, private IDs, requests and raw Harness output
                # must not be reflected into the model conversation.
                raise IVCError(
                    "reference calibration failed for at least one nominal or "
                    "calibrated-boundary case; revise only the supplied private "
                    "contexts, authored task-neutral requests, and bindings"
                ) from None
            if callback is not None:
                callback(
                    {
                        "stage": "ivc-reference-positive-control",
                        "turn": turn,
                        "result": positive_control,
                    }
                )
        return canonical

    if _supports_artifact_react(client):
        destination = Path(artifact_path).resolve() if artifact_path is not None else None
        temporary = TemporaryDirectory(prefix="autoadapter-ivc-") if destination is None else None
        workspace_root = (
            Path(temporary.name)
            if temporary is not None
            else destination.parent / "workspace"
        )
        context = temporary if temporary is not None else nullcontext()
        with context:
            session = IsolatedArtifactSession(
                workspace=workspace_root,
                budget=probe_budget,
                package=None if isinstance(package, Mapping) else package,
            )
            (session.workspace / "ivc_inputs.json").write_text(
                json.dumps(inputs, ensure_ascii=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            working_artifact = session.workspace / IVC_ARTIFACT_NAME
            working_artifact.unlink(missing_ok=True)
            validation_turn = 0

            def validate_file(path: Path) -> dict[str, Any]:
                nonlocal validation_turn
                validation_turn += 1
                return validate_and_calibrate(
                    _read_json_object(path, artifact_name=IVC_ARTIFACT_NAME),
                    turn=validation_turn,
                )

            authoring_brief = json.dumps(
                authoring_brief_document,
                ensure_ascii=True,
                separators=(",", ":"),
            )
            ivc_tools = tuple(
                tool
                for tool in session.artifact_tools()
                if tool.name != "read_file"
            )
            try:
                result = run_artifact_react(
                    client=client,
                    stage="ivc",
                    system_prompt=IVC_SYSTEM_PROMPT,
                    user_prompt=(
                        f"Authoring brief:\n{authoring_brief}\n"
                        f"Write {IVC_ARTIFACT_NAME}."
                    ),
                    tools=ivc_tools,
                    artifact_name=IVC_ARTIFACT_NAME,
                    artifact_path=working_artifact,
                    validate_artifact=validate_file,
                    max_turns=max_turns,
                    delivery_turns=max(1, max_turns - 1),
                    validate_after_write=True,
                )
            except ReactLoopError as exc:
                raise IVCError(
                    f"IVC did not produce a calibrated {IVC_ARTIFACT_NAME} in {max_turns} turns"
                ) from exc
            finally:
                session.close()
            if not isinstance(result.artifact, Mapping):
                raise IVCError(
                    f"{IVC_ARTIFACT_NAME} validation returned no suite object"
                )
            canonical = json_copy(dict(result.artifact), label=IVC_ARTIFACT_NAME)
            if destination is not None:
                write_private_suite(destination, canonical)
            if callback is not None:
                callback(
                    {
                        "stage": "ivc-sealed",
                        "turn": result.model_turns,
                        "artifact": canonical,
                        "completion": result.completed_on,
                        "tool_calls": result.tool_calls,
                        "trace": list(result.trace),
                    }
                )
            return canonical

    prompt = IVC_SYSTEM_PROMPT
    for turn in range(max_turns):
        stage = "ivc" if turn == 0 else f"ivc-artifact-correction-{turn}"
        artifact = client.generate_json(stage=stage, prompt=prompt, inputs=inputs)
        event: dict[str, Any] = {"stage": stage, "turn": turn + 1, "artifact": artifact}
        if callback is not None:
            callback(event)
        try:
            canonical = validate_and_calibrate(artifact, turn=turn + 1)
        except IVCError as exc:
            if turn + 1 >= max_turns:
                raise
            inputs = {
                **inputs,
                "previous_invalid_suite": json_copy(artifact, label="previous_invalid_suite"),
                "deterministic_audit_error": str(exc),
            }
            prompt = IVC_SYSTEM_PROMPT + (
                "\nCorrect the previous artifact only enough to satisfy the deterministic audit. "
                "Keep exactly one nominal and one calibrated_boundary case per capability and "
                "copy every criterion value exactly; each pair must use distinct task-neutral "
                "requests that satisfy the sealed request schema and any request_domain supplied "
                "by the referenced private instance. Author inline measurement_binding values from "
                "the trusted operator catalog; binding_id is forbidden."
            )
            continue
        if artifact_path is not None:
            write_private_suite(artifact_path, canonical)
        if callback is not None:
            callback({"stage": "ivc-sealed", "turn": turn + 1, "artifact": canonical})
        return canonical
    raise AssertionError("unreachable")


def run_reference_positive_control(
    hook: ReferencePositiveControlHook,
    *,
    capability_design: Mapping[str, Any],
    validation_suite: Mapping[str, Any],
) -> dict[str, Any]:
    """Invoke the Framework reference-positive-control seam.

    The hook receives only sealed design and suite inputs.  Candidate source,
    Repair history, and candidate traces are intentionally not parameters.
    """

    if not callable(hook):
        raise IVCError("reference positive-control hook must be callable")
    result = hook(capability_design=capability_design, validation_suite=validation_suite)
    if not isinstance(result, Mapping):
        raise IVCError("reference positive-control result must be an object")
    copied = json_copy(dict(result), label="reference_positive_control")
    if not _positive_control_passed(copied):
        raise IVCError("reference positive control did not pass the complete suite")
    return copied


def write_private_suite(path: str | Path, suite: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(dict(suite), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


# Compatibility names retained for imports from the unmodified root pipeline.
validate_private_suite = validate_capability_validation_suite
sample_private_suite = lambda **_kwargs: (_ for _ in ()).throw(
    IVCError("Task Demo sampling is not part of capability-v2 IVC")
)
sample_task_demo_suite = sample_private_suite


__all__ = [
    "IVC_ARTIFACT_TURNS",
    "IVC_ARTIFACT_NAME",
    "IVC_CASE_ROLES",
    "IVCError",
    "IVCEventCallback",
    "IVCPhase",
    "IVC_SYSTEM_PROMPT",
    "JsonGenerator",
    "PRIVATE_CASE_SAMPLE_SIZE",
    "ReferencePositiveControlHook",
    "TASK_DEMO_CASE_COUNT",
    "TASK_DEMO_TASK_COUNT",
    "build_ivc_inputs",
    "load_sanitized_ivc_examples",
    "load_ivc_worked_references",
    "run_ivc",
    "run_reference_positive_control",
    "sample_private_suite",
    "sample_task_demo_suite",
    "validate_capability_validation_suite",
    "validate_private_suite",
    "write_private_suite",
]
