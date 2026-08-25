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
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from autoadapter2.capability_design.protocol import (
    CAPABILITY_PROTOCOL_VERSION,
    CapabilityProtocolError,
    capability_records,
    json_copy,
    validate_schema_definition,
    validate_schema_value,
)
from autoadapter2.driver_synthesis.interactive import IsolatedArtifactSession
from autoadapter2.driver_synthesis.probe import ProbeBudget
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
Compile the sealed Capability Design into one complete private validation suite. You see only the
sealed capability contracts, Framework-private execution contexts/bindings/guards, and sanitized
public capability-validation examples. You cannot see and must not infer candidate Driver source,
generated traces, Repair history, or a candidate verdict.

Return one JSON object with artifact_type='capability_validation_suite', schema_version='2.0',
capability_protocol_version='capability-v2', the supplied package identity, and exactly two cases
for every sealed capability: one case_role='nominal' and one case_role='calibrated_boundary'.
Choose only supplied private scene/reset context, binding, and guard IDs, but author each complete
task-neutral request yourself. Supplied request_anchors are calibration evidence, not prescribed
cases and need not be copied. Every authored request must satisfy the sealed request_schema and,
when the selected private instance supplies one, its request_domain. A private context without a
request_domain does not prescribe a request. The nominal and calibrated-boundary requests for one
capability must differ. Copy the sealed capability criteria and every referenced numeric value
exactly into each case. Keep private bindings, guards, repetitions, and timeout references unchanged.
Set whole_suite_aggregation={'kind':'all_cases'}. Do not add task mappings, task IDs, Driver or Repair
material, implementation advice, a task plan, or a self-reported verdict."""


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
_SANITIZED_EXAMPLES_PATH = (
    Path(__file__).resolve().parents[3]
    / "references"
    / "capability_v2"
    / "ivc_examples.json"
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


def load_sanitized_ivc_examples(
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load the IVC's structure-only, non-oracle example projection."""

    source = Path(path) if path is not None else _SANITIZED_EXAMPLES_PATH
    document = _read_object(source)
    examples = document.get("examples")
    if (
        not isinstance(examples, list)
        or not examples
        or not all(isinstance(item, Mapping) for item in examples)
    ):
        raise IVCError("sanitized IVC examples must be a non-empty object array")
    copied = _copy_private_inputs({"examples": examples}).get("examples")
    if not isinstance(copied, list):  # pragma: no cover - guarded above
        raise IVCError("sanitized IVC examples could not be copied")
    return [dict(item) for item in copied]


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
    if present_capability:
        required_paths = capability_paths
        calibration_namespace = "capability"
    else:
        required_paths = {name: task_dir / f"{name}.json" for name in names}
        missing = [path.name for path in required_paths.values() if not path.is_file()]
        if missing:
            raise IVCError(
                "package has no complete private IVC context; missing "
                f"tasks/private/{', '.join(missing)}"
            )
        calibration_namespace = "task"

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
        documents = [_read_object(required_paths[name])]
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        identity: dict[str, Any] = {}
        for document in documents:
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
                merged.append(json_copy(dict(record), label=f"private {name}"))
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
    forbidden_tokens = {"driver", "repair", "candidate", "trace", "verdict"}

    def walk(value: Any, where: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = str(key).lower()
                tokens = {
                    token for token in re.split(r"[^a-z0-9]+", normalized) if token
                }
                if normalized in forbidden or tokens.intersection(forbidden_tokens):
                    raise IVCError(f"{where} exposes candidate field {key!r}")
                walk(child, f"{where}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{where}[{index}]")

    walk(copied, "private_inputs")
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
    bindings = _id_map(private.get("bindings"), field="bindings", id_field="binding_id")
    guards = _id_map(private.get("guards"), field="guards", id_field="guard_id")
    capabilities = _design_map(design)
    cases = suite.get("cases")
    expected_count = 2 * len(capabilities)
    if not isinstance(cases, list) or len(cases) != expected_count:
        raise IVCError(f"cases must contain exactly two cases per capability ({expected_count})")

    seen_cases: set[str] = set()
    roles: dict[tuple[str, str], int] = {}
    requests: dict[tuple[str, str], Any] = {}
    canonical_cases: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        where = f"cases[{index}]"
        if not isinstance(case, Mapping):
            raise IVCError(f"{where} must be an object")
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
        binding_id = case.get("binding_id")
        if not isinstance(instance_id, str) or instance_id not in instances:
            raise IVCError(f"{where}.instance_id is not a supplied private instance")
        if not isinstance(binding_id, str) or binding_id not in bindings:
            raise IVCError(f"{where}.binding_id is not a supplied private binding")
        instance = instances[instance_id]
        binding = bindings[binding_id]
        _validate_authored_request_context(
            request,
            instance=instance,
            sealed_schema=sealed_request_schema,
            where=where,
        )
        clause_bindings = instance.get("clause_bindings")
        if (
            not isinstance(clause_bindings, Mapping)
            or binding_id not in clause_bindings.values()
        ):
            raise IVCError(f"{where}.binding_id does not belong to the selected private instance")
        for record, label in ((instance, "instance"), (binding, "binding")):
            declared_capability = record.get("capability_id")
            if declared_capability is not None and declared_capability != capability_id:
                raise IVCError(f"{where} changes the private {label} capability binding")
            declared_role = record.get("case_role")
            if declared_role is not None and declared_role != role:
                raise IVCError(f"{where} changes the private {label} role")
        instance_profile = _calibration_profile(
            instance,
            label="instance",
            where=where,
        )
        binding_profile = _calibration_profile(
            binding,
            label="binding",
            where=where,
        )
        if (instance_profile is None) != (binding_profile is None):
            raise IVCError(
                f"{where} selected private instance and binding must either both declare "
                "calibration_profile or both omit it"
            )
        if instance_profile is not None and instance_profile != binding_profile:
            raise IVCError(
                f"{where} selected private instance and binding calibration_profile differ"
            )
        guard_ids = case.get("guard_ids")
        if not isinstance(guard_ids, list) or any(not isinstance(item, str) or item not in guards for item in guard_ids):
            raise IVCError(f"{where}.guard_ids references invalid private guards")
        if "guard_ids" in instance and guard_ids != instance.get("guard_ids"):
            raise IVCError(f"{where}.guard_ids changes private guards")
        for field in ("repetitions", "timeout_sim_s"):
            if field in instance and case.get(field) != instance.get(field):
                raise IVCError(f"{where}.{field} changes private execution settings")
        # If private bindings declare a metric/unit, every copied criterion
        # must retain the same pair.  Other referenced numeric values are
        # protected by the exact criterion equality above.
        expected_criteria = _criterion_list(capability, where="sealed capability")
        if binding.get("metric") is not None and binding.get("metric") not in {
            item.get("metric") for item in expected_criteria
        }:
            raise IVCError(f"{where}.binding metric is incompatible with sealed criteria")
        if binding.get("unit") is not None and binding.get("unit") not in {
            item.get("unit") for item in expected_criteria
        }:
            raise IVCError(f"{where}.binding unit is incompatible with sealed criteria")
        key = (capability_id, role)
        roles[key] = roles.get(key, 0) + 1
        requests[key] = json_copy(request, label=f"{where}.request")
        canonical_cases.append(_normalise_case_criteria(case, capability))

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

    copied_private = _copy_private_inputs(private_inputs)
    selected_examples = list(examples) if examples else load_sanitized_ivc_examples()
    sanitized_examples = _copy_private_inputs(
        {"examples": selected_examples}
    ).get("examples", [])
    identity = _package_identity(package)
    capability_count = len(_design_map(design))
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
                "instance_id",
                "binding_id",
                "guard_ids",
                "criteria",
            ],
            "copy_method_name_from_sealed_design": True,
            "copy_criteria_from_sealed_design_exactly": True,
            "ivc_authors_complete_request_for_each_case": True,
            "validate_authored_request_against_sealed_request_schema": True,
            "validate_authored_request_against_selected_instance_request_domain_when_supplied": True,
            "request_domain_format": "closed capability-v2 request schema",
            "request_anchors_are_calibration_evidence_not_prescribed_cases": True,
            "copy_instance_execution_fields_when_present": [
                "repetitions",
                "timeout_sim_s",
            ],
            "instance_binding_guard_ids_must_be_supplied": True,
            "instance_and_binding_calibration_profile_must_match_when_supplied": True,
            "nominal_and_boundary_requests_must_differ": True,
        },
        "sealed_capability_design": json_copy(dict(design), label="sealed_capability_design"),
        "private_instances": copied_private.get("instances", {}),
        "private_bindings": copied_private.get("bindings", {}),
        "private_guards": copied_private.get("guards", {}),
        "sanitized_capability_validation_examples": sanitized_examples,
        "required_case_roles": list(IVC_CASE_ROLES),
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
                    "case selections, task-neutral requests and bindings"
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
            )
            (session.workspace / "ivc_inputs.json").write_text(
                json.dumps(inputs, indent=2, ensure_ascii=True) + "\n",
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
                inputs,
                ensure_ascii=True,
                separators=(",", ":"),
            )
            try:
                result = run_artifact_react(
                    client=client,
                    stage="ivc",
                    system_prompt=IVC_SYSTEM_PROMPT,
                    user_prompt=(
                        "Read ivc_inputs.json, which is raw JSON in the phase-workspace root "
                        "and is directly openable as ./ivc_inputs.json from execute_python; "
                        f"there is no result wrapper. The complete authoring brief is included "
                        f"here verbatim: {authoring_brief}. Copy artifact_header unchanged at the "
                        "suite top level and follow validator_contract exactly. Author the complete canonical "
                        f"{IVC_ARTIFACT_NAME} with write_file. You may use execute_python "
                        "for credential-free calibration calculations. Use at most two turns for "
                        "inspection, then call write_file with an initial complete artifact by "
                        "turn three. Turns five and six are reserved for delivery and one "
                        "correction after deterministic validation feedback. End the turn when the "
                        "artifact is ready; there is no submit or check tool."
                    ),
                    tools=session.artifact_tools(),
                    artifact_name=IVC_ARTIFACT_NAME,
                    artifact_path=working_artifact,
                    validate_artifact=validate_file,
                    max_turns=max_turns,
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
                "by the selected private instance. Request anchors are evidence, not prescribed cases."
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
    "run_ivc",
    "run_reference_positive_control",
    "sample_private_suite",
    "sample_task_demo_suite",
    "validate_capability_validation_suite",
    "validate_private_suite",
    "write_private_suite",
]
