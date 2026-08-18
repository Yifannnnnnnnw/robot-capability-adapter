"""Real-model Independent Validation Compiler with a deterministic audit."""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from autoadapter2.libraries import RobotPackage


PRIVATE_CASE_SAMPLE_SIZE = 5


IVC_SYSTEM_PROMPT = """You are the implementation-blind Independent Validation Compiler.
Compile the sealed public Capability Design and the supplied Framework-private instances,
measurement bindings, and guards into one complete private physical validation case pool. You
cannot see and must not infer any candidate driver implementation, trace, report, Repair history,
or verdict.

Return one JSON object with artifact_type='private_validation_suite', schema_version='1.0', the
supplied robot_configuration_id, package_version, task_snapshot_id, and cases[]. Each case must
select an existing private instance and one source validation clause and contain: case_id,
capability_id, method_name, task_id, source_clause_id, instance_id, binding_id, guard_ids,
repetitions, timeout_sim_s, and criterion. criterion must copy metric, unit, comparator, threshold,
temporal, aggregation, and source_refs exactly from the sealed public clause. Use only supplied IDs.
Cover every designed source clause with at least one case. Set whole_suite_aggregation to
{'kind':'all_cases'}. Do not sample the pool; the Framework performs the later private five-case
selection. Do not return driver code, implementation advice, or a self-reported verdict."""


class IVCError(ValueError):
    """Raised when a private suite is incomplete, invented, or weaker."""


class JsonGenerator(Protocol):
    def generate_json(
        self,
        *,
        stage: str,
        prompt: str,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]: ...


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise IVCError(f"cannot read private compiler input {path.name}") from exc
    if not isinstance(value, dict):
        raise IVCError(f"private compiler input {path.name} must be an object")
    return value


def _text(value: Mapping[str, Any], field: str, *, where: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise IVCError(f"{where}.{field} must be a non-empty string")
    return item.strip()


def _items(document: Mapping[str, Any], field: str, *, where: str) -> list[Mapping[str, Any]]:
    values = document.get(field)
    if not isinstance(values, list) or not values:
        raise IVCError(f"{where}.{field} must be a non-empty list")
    if not all(isinstance(value, Mapping) for value in values):
        raise IVCError(f"{where}.{field} entries must be objects")
    return values


def _by_id(
    values: list[Mapping[str, Any]],
    field: str,
    *,
    where: str,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for index, value in enumerate(values):
        identifier = _text(value, field, where=f"{where}[{index}]")
        if identifier in result:
            raise IVCError(f"duplicate {field} {identifier!r}")
        result[identifier] = value
    return result


def _private_inputs(package: RobotPackage) -> dict[str, dict[str, Any]]:
    return {
        name: _read_object(package.private_dir / f"{name}.json")
        for name in ("instances", "bindings", "guards")
    }


def _design_maps(
    design: Mapping[str, Any],
) -> tuple[
    dict[str, Mapping[str, Any]],
    dict[str, str],
    dict[tuple[str, str], Mapping[str, Any]],
]:
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list):
        raise IVCError("sealed Capability Design has no capabilities")
    by_capability: dict[str, Mapping[str, Any]] = {}
    task_to_capability: dict[str, str] = {}
    clauses: dict[tuple[str, str], Mapping[str, Any]] = {}
    for capability in capabilities:
        if not isinstance(capability, Mapping):
            raise IVCError("sealed Capability Design contains an invalid capability")
        capability_id = _text(capability, "capability_id", where="capability")
        by_capability[capability_id] = capability
        for task_id in capability.get("covered_task_ids", []):
            task_to_capability[str(task_id)] = capability_id
        for clause in capability.get("validation_contract", []):
            if not isinstance(clause, Mapping):
                raise IVCError("sealed Capability Design contains an invalid clause")
            key = (
                _text(clause, "source_task_id", where="validation_contract"),
                _text(clause, "source_clause_id", where="validation_contract"),
            )
            clauses[key] = clause
    return by_capability, task_to_capability, clauses


def _same_criterion(case: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    criterion = case.get("criterion")
    if not isinstance(criterion, Mapping):
        return False
    fields = (
        "metric",
        "unit",
        "comparator",
        "threshold",
        "temporal",
        "aggregation",
        "source_refs",
    )
    return all(criterion.get(field) == source.get(field) for field in fields)


def validate_private_suite(
    suite: Mapping[str, Any],
    *,
    package: RobotPackage,
    design: Mapping[str, Any],
    private_inputs: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Audit model output without reading any candidate implementation."""

    expected_root = {
        "artifact_type": "private_validation_suite",
        "schema_version": "1.0",
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
    }
    for field, expected in expected_root.items():
        if suite.get(field) != expected:
            raise IVCError(f"{field} must equal {expected!r}")
    if suite.get("whole_suite_aggregation") != {"kind": "all_cases"}:
        raise IVCError("whole_suite_aggregation must require all cases")

    private = dict(private_inputs or _private_inputs(package))
    instances = _by_id(
        _items(private["instances"], "instances", where="instances.json"),
        "instance_id",
        where="instances",
    )
    bindings = _by_id(
        _items(private["bindings"], "bindings", where="bindings.json"),
        "binding_id",
        where="bindings",
    )
    guards = _by_id(
        _items(private["guards"], "guards", where="guards.json"),
        "guard_id",
        where="guards",
    )
    capabilities, task_to_capability, source_clauses = _design_maps(design)

    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise IVCError("cases must be a non-empty list")
    case_ids: set[str] = set()
    coverage: Counter[tuple[str, str]] = Counter()
    for index, case in enumerate(cases):
        where = f"cases[{index}]"
        if not isinstance(case, Mapping):
            raise IVCError(f"{where} must be an object")
        case_id = _text(case, "case_id", where=where)
        if case_id in case_ids:
            raise IVCError(f"duplicate case_id {case_id!r}")
        case_ids.add(case_id)
        capability_id = _text(case, "capability_id", where=where)
        task_id = _text(case, "task_id", where=where)
        clause_id = _text(case, "source_clause_id", where=where)
        if capability_id not in capabilities:
            raise IVCError(f"{where} references unknown capability")
        if task_to_capability.get(task_id) != capability_id:
            raise IVCError(f"{where} capability does not cover task")
        capability = capabilities[capability_id]
        if case.get("method_name") != capability.get("method_name"):
            raise IVCError(f"{where}.method_name differs from sealed design")
        source = source_clauses.get((task_id, clause_id))
        if source is None:
            raise IVCError(f"{where} references unknown source clause")
        if not _same_criterion(case, source):
            raise IVCError(f"{where}.criterion changes a source pass standard")

        instance_id = _text(case, "instance_id", where=where)
        instance = instances.get(instance_id)
        if instance is None or instance.get("task_id") != task_id:
            raise IVCError(f"{where} references an invalid task instance")
        clause_bindings = instance.get("clause_bindings")
        if not isinstance(clause_bindings, Mapping):
            raise IVCError(f"private instance {instance_id!r} lacks clause_bindings")
        binding_id = _text(case, "binding_id", where=where)
        if clause_bindings.get(clause_id) != binding_id:
            raise IVCError(f"{where} changes the private measurement binding")
        binding = bindings.get(binding_id)
        if binding is None:
            raise IVCError(f"{where} references unknown binding")
        if binding.get("metric") != source.get("metric") or binding.get("unit") != source.get("unit"):
            raise IVCError(f"{where} binding is incompatible with the public metric")

        guard_ids = case.get("guard_ids")
        if guard_ids != instance.get("guard_ids") or not isinstance(guard_ids, list):
            raise IVCError(f"{where} changes private guards")
        if any(not isinstance(guard_id, str) or guard_id not in guards for guard_id in guard_ids):
            raise IVCError(f"{where} references unknown guard")
        if case.get("repetitions") != instance.get("repetitions"):
            raise IVCError(f"{where} changes private repetitions")
        if case.get("timeout_sim_s") != instance.get("timeout_sim_s"):
            raise IVCError(f"{where} changes private timeout")
        coverage[(task_id, clause_id)] += 1

    if set(coverage) != set(source_clauses):
        raise IVCError("private suite does not cover every designed source clause")
    return dict(suite)


def run_ivc(
    client: JsonGenerator,
    *,
    package: RobotPackage,
    design: Mapping[str, Any],
    max_model_attempts: int = 2,
) -> dict[str, Any]:
    """Invoke IVC with sealed design and private inputs, never candidate code."""

    if not 1 <= max_model_attempts <= 2:
        raise IVCError("max_model_attempts must be one or two")

    private = _private_inputs(package)
    inputs = {
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
        "capability_design": dict(design),
        "task_library": {"tasks": list(package.tasks)},
        "private_instances": private["instances"],
        "private_bindings": private["bindings"],
        "private_guards": private["guards"],
    }
    prompt = IVC_SYSTEM_PROMPT
    for attempt in range(max_model_attempts):
        suite = client.generate_json(
            stage="ivc" if attempt == 0 else "ivc-structure-correction",
            prompt=prompt,
            inputs=inputs,
        )
        try:
            return validate_private_suite(
                suite,
                package=package,
                design=design,
                private_inputs=private,
            )
        except IVCError as exc:
            if attempt + 1 >= max_model_attempts:
                raise
            inputs = {
                **inputs,
                "previous_invalid_suite": suite,
                "deterministic_audit_error": str(exc),
            }
            prompt = (
                IVC_SYSTEM_PROMPT
                + "\nCorrect the previous JSON only enough to satisfy the deterministic audit. "
                "Do not change, omit, or weaken any source clause or private binding selection."
            )
    raise AssertionError("unreachable")


def sample_private_suite(
    case_pool: Mapping[str, Any],
    *,
    seed: str,
) -> dict[str, Any]:
    """Select the sealed executable cases from an already audited complete pool."""

    cases = case_pool.get("cases")
    if not isinstance(cases, list) or len(cases) < PRIVATE_CASE_SAMPLE_SIZE:
        raise IVCError(
            f"private case pool must contain at least {PRIVATE_CASE_SAMPLE_SIZE} cases"
        )
    if not isinstance(seed, str) or not seed:
        raise IVCError("private case selection seed must be non-empty text")

    selected_indexes = random.Random(seed).sample(
        range(len(cases)), PRIVATE_CASE_SAMPLE_SIZE
    )
    selected_cases = [dict(cases[index]) for index in selected_indexes]
    selected_ids = [
        _text(case, "case_id", where=f"selected_cases[{index}]")
        for index, case in enumerate(selected_cases)
    ]
    if len(set(selected_ids)) != PRIVATE_CASE_SAMPLE_SIZE:
        raise IVCError("selected private case IDs must be unique")

    suite = dict(case_pool)
    suite["cases"] = selected_cases
    suite["selection"] = {
        "kind": "uniform_without_replacement",
        "seed": seed,
        "source_case_count": len(cases),
        "selected_case_count": PRIVATE_CASE_SAMPLE_SIZE,
        "selected_case_ids": selected_ids,
    }
    return suite


def write_private_suite(path: str | Path, suite: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(dict(suite), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
