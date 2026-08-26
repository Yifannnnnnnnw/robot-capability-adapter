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
Compile the sealed Capability Design into one complete hidden validation suite. You see the sealed
capability contracts and task_support relation, sanitized scene/reset contexts, source lineage,
trusted measurement-operator catalog, and complete SO-101/Go2 worked references. You cannot see and
must not infer candidate Driver source, generated traces, Repair history, or a candidate verdict.

The complete input is compact JSON in ivc_inputs.json. Use execute_python for targeted queries; do
not print the full scene, Task Library, operator, or worked-reference collections. The prompt's
authoring index gives the exact private_instances.instances records, rooted request leaves,
unit-compatible operator schemas, and declared frame aliases. Query the raw scene catalog
only for entity values; never guess an instance ID or treat the private_instances wrapper as a list.
The operator array is measurement_operator_catalog.operators, not the wrapper itself, and every
operator request_path value is rooted at request.<field>. With a six-turn budget, use at most two
turns for targeted inspection; turns three through six are write-only delivery/correction turns.
Every successful write is immediately audited and any deterministic error is returned in this same
conversation. Large artifacts may use bounded append writes. When a sealed
request expresses a scaled joint target, use target_scale and target_offset only when those closed
parameters are declared by the selected trusted operator-catalog entry; never invent an operator
parameter.

Return one JSON object with artifact_type='capability_validation_suite', schema_version='2.0',
capability_protocol_version='capability-v2', the supplied package identity, and exactly two cases
for every sealed capability: one case_role='nominal' and one case_role='calibrated_boundary'.
Author each complete task-neutral request and inline measurement_binding yourself. Select only a
supplied scene/reset instance and its mandatory guard IDs; private measurement examples are examples,
not IDs to select. Every request must satisfy the sealed request_schema and any request_domain on the
selected instance. The nominal and calibrated-boundary requests for one capability must differ. Give
request_grounding_refs that resolve to sealed-schema or supplied calibration evidence. Copy the sealed
criteria and every referenced numeric value exactly. Use only a kind and closed parameters from the
trusted operator catalog. Complete SO-101/Go2 worked references remain class-level design examples;
their fixed semantic operators are selectable only when the scoped operator catalog explicitly lists
the exact matching reference capability. Transfer designs use a generic numeric operator with the
terminal_state/single_trial criterion contract. Set whole_suite_aggregation={'kind':'all_cases'}. Never emit binding_id,
Python/code, task dispatch, task IDs, Driver or Repair material, implementation advice, or a
self-reported verdict."""


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
    index duplicates only the fields needed to avoid guessing wrapper paths,
    instance IDs, operator parameters, request paths, and declared frame
    aliases.  Full operator and scene catalogs remain queryable in the raw
    input instead of being repeated in the first prompt.
    """

    def schema_nodes(
        schema: Any,
        *,
        path: str = "request",
    ) -> tuple[list[dict[str, Any]], list[tuple[str, Mapping[str, Any]]]]:
        leaves: list[dict[str, Any]] = []
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
        record: dict[str, Any] = {
            "path": path,
            "type": schema.get("type"),
        }
        for field in (
            "unit",
            "frame",
            "minimum",
            "maximum",
            "minItems",
            "maxItems",
            "enum",
        ):
            if field in schema:
                record[field] = json_copy(
                    schema[field], label=f"sealed request leaf {path}.{field}"
                )
        leaves.append(record)
        return leaves, addressable

    private_document = inputs.get("private_instances")
    if isinstance(private_document, Mapping):
        raw_instances = private_document.get("instances")
        records_path = "private_instances.instances"
        wrapper_rule = (
            "private_instances is an object wrapper, not a case list; "
            "copy instance_id only from the indexed records below"
        )
    else:
        raw_instances = private_document
        records_path = "private_instances"
        wrapper_rule = (
            "this compatibility input is the record array itself; copy "
            "instance_id only from the indexed records below"
        )
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
    criterion_index: list[dict[str, Any]] = []
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
        criterion_index.append(
            {
                "capability_id": capability.get("capability_id"),
                "method_name": capability.get("method_name"),
                "criterion_metric": criterion.get("metric"),
                "criterion_unit": criterion.get("unit"),
                "rooted_request_leaf_index": leaves,
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

    compatible_operator_specs: dict[str, Any] = {}
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
        compact_spec: dict[str, Any] = {
            "description": operator.get("description"),
            "output_units": json_copy(
                operator.get("output_units", []),
                label="measurement operator output units",
            ),
            "required_parameters": {
                name: compact_properties[name]
                for name in compact_properties
                if name in required_parameters
            },
            "optional_parameters": {
                name: compact_properties[name]
                for name in compact_properties
                if name not in required_parameters
            },
            "entity_parameter_types": json_copy(
                operator.get("entity_parameters", {}),
                label="measurement operator entity parameter types",
            ),
        }
        request_path_parameters = operator.get("request_path_parameters", [])
        if request_path_parameters:
            compact_spec["request_path_parameters"] = json_copy(
                request_path_parameters,
                label="measurement operator request path parameters",
            )
        request_value_types = operator.get("request_value_types", {})
        if request_value_types:
            compact_spec["request_value_types"] = json_copy(
                request_value_types,
                label="measurement operator request value types",
            )
        evaluation_mode = operator.get("evaluation_mode")
        if evaluation_mode != "numeric_measurement":
            compact_spec["evaluation_mode"] = evaluation_mode
        compatible_operator_specs[str(operator["kind"])] = compact_spec

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
    for scene in raw_scenes:
        if (
            not isinstance(scene, Mapping)
            or scene.get("scene_entrypoint") not in scene_entrypoints
        ):
            continue
        entities = scene.get("entities")
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

    return {
        "raw_paths": {
            "instances": "ivc_inputs.json::private_instances.instances",
            "operators": "ivc_inputs.json::measurement_operator_catalog.operators",
            "scenes": "ivc_inputs.json::scene_entity_catalog.scenes",
        },
        "request_path_contract": {
            "literal_prefix": "request.",
            "example": "request.target_position.x",
        },
        "private_instances_raw_wrapper": {
            "records_path": records_path,
            "rule": wrapper_rule,
        },
        "private_instance_records": instance_records,
        "private_instance_shared_execution_fields": shared_instance_fields,
        "capability_criterion_index": criterion_index,
        "request_grounding_ref_rule": (
            "For each case, copy only exact source_id/specific_reference pairs "
            "from that capability's allowed_request_grounding_refs_from_sealed_schema "
            "or from evidence_refs inside the selected instance request_domain. "
            "Capability-level evidence_refs and source_refs are not request grounding."
        ),
        "measurement_binding_shape": {
            "metric": "exact sealed criterion metric",
            "unit": "exact sealed criterion unit",
            "kind": "IVC chooses one unit-compatible operator kind",
            "parameters": (
                "IVC authors the closed object from unit_compatible_operator_specs_by_kind; "
                "entity values come from the selected raw scene catalog"
            ),
        },
        "unit_compatible_operator_specs_by_kind": compatible_operator_specs,
        "operator_spec_defaults": {
            "evaluation_mode": "numeric_measurement",
            "missing_request_path_parameters_or_value_types": "empty",
        },
        "scene_declared_frame_aliases": scene_frame_aliases,
    }


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
                {
                    "artifact_header": inputs["artifact_header"],
                    "validator_contract": inputs["validator_contract"],
                    "authoring_index": _build_ivc_authoring_index(inputs),
                    "sealed_capability_design": inputs["sealed_capability_design"],
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
            try:
                result = run_artifact_react(
                    client=client,
                    stage="ivc",
                    system_prompt=IVC_SYSTEM_PROMPT,
                    user_prompt=(
                        "The complete raw input is compact JSON at ./ivc_inputs.json in the "
                        "phase-workspace root; there is no result wrapper. Use execute_python "
                        "for targeted queries instead of printing the full scene, Task Library, "
                        "operator, or worked-reference collections. The compact core authoring "
                        f"brief is included here verbatim: {authoring_brief}. "
                        "The raw private_instances value is an object wrapper; its record array is "
                        "private_instances.instances. Use only exact instance_id values from "
                        "authoring_index.private_instance_records, and copy each indexed mandatory "
                        "guard list, repetitions, timeout, and request_domain rather than guessing. "
                        "Apply authoring_index.private_instance_shared_execution_fields to every "
                        "indexed record before any record-local overrides. "
                        "For request_grounding_refs, copy only exact source_id/specific_reference "
                        "pairs from the capability's indexed "
                        "allowed_request_grounding_refs_from_sealed_schema or from evidence_refs "
                        "inside the selected instance request_domain. Capability-level evidence_refs "
                        "and criterion source_refs are not valid request grounding. "
                        "Choose measurement_binding.kind yourself from the unit-compatible operator "
                        "specs and satisfy its parameter signature. The index deliberately contains "
                        "no selected kind, binding, instance, or entity value: query the exact selected scene under "
                        "authoring_index.raw_paths.scenes only for the entity values you still need. "
                        "Use authoring_index.scene_declared_frame_aliases for schema-declared frames. "
                        "The raw operator catalog is an object wrapper; its "
                        "operator array is measurement_operator_catalog.operators. Every parameter "
                        "whose catalog type is request_path must start with the literal prefix "
                        "request. and resolve into the sealed request_schema. Copy artifact_header "
                        "unchanged at the suite top "
                        "level and follow validator_contract exactly. "
                        "Author the complete canonical "
                        f"{IVC_ARTIFACT_NAME} with write_file. You may use execute_python "
                        "for credential-free calibration calculations and may inspect the read-only "
                        "public assets root through AUTOADAPTER_PROBE_PUBLIC_PACKAGE. Conserve the "
                        "six-turn budget: use at most two turns for targeted inspection. Turns three "
                        "through six are write-only delivery/correction turns. The Framework validates "
                        "at the end of every turn containing a successful write_file call, so write "
                        "as soon as you have a grounded draft and use returned errors to correct it. "
                        "For a large artifact, use append=false for "
                        "the first safe text chunk and append=true for later chunks; only the "
                        "combined file must parse. For scaled joint targets, use target_scale and "
                        "target_offset only when the selected operator-catalog entry declares them. "
                        "End the turn when the "
                        "artifact is ready; there is no submit or check tool."
                    ),
                    tools=session.artifact_tools(),
                    artifact_name=IVC_ARTIFACT_NAME,
                    artifact_path=working_artifact,
                    validate_artifact=validate_file,
                    max_turns=max_turns,
                    delivery_turns=max(1, max_turns - 2),
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
