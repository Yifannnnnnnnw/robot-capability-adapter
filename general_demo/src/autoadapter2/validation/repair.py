"""Bounded implementation-only Repair with immutable revision and run ledgers."""

from __future__ import annotations

import ast
import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash
from ..foundation.seals import create_seal, verify_seal
from ..implementation.binding import derive_implementation_manifest
from ..implementation.bundle import ImplementationBundle, validate_implementation_bundle
from .validation_a import ValidationAResult, ValidationARunner, bind_candidate_to_suite
from .validation_b import (
    FrozenValidationContext,
    ValidationBResult,
    ValidationBRunner,
    ValidationContext,
    freeze_validation_context,
)


RepairCallback = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class RepairConfig:
    max_repairs: int = 10
    max_infrastructure_retries: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.max_repairs, int) or isinstance(self.max_repairs, bool) or not 1 <= self.max_repairs <= 10:
            raise ContractError("Repair max_repairs must be 1..10")
        if not isinstance(self.max_infrastructure_retries, int) or isinstance(self.max_infrastructure_retries, bool) or self.max_infrastructure_retries < 0:
            raise ContractError("Repair infrastructure retries must be non-negative")


@dataclass(frozen=True)
class RepairResult:
    status: str
    first_passing_repair_index: int | None
    repair_invocations_used: int
    candidate_revisions_created: int
    repairs_consumed: int
    repair_llm_calls: int
    repair_log: tuple[dict[str, Any], ...]
    run_ledger: tuple[dict[str, Any], ...]
    run_snapshot_hash: str
    initial_validation_a: ValidationAResult
    initial_validation_b: ValidationBResult | None
    final_validation_a: ValidationAResult | None
    final_validation_b: ValidationBResult | None
    frozen_artifact_hashes: dict[str, str]


def _issue(code: str) -> dict[str, str]:
    return {"code": code, "message": code.replace("_", " ").lower()}


def _submission_source(submission: Mapping[str, Any]) -> str:
    value = submission.get("capability.py") if isinstance(submission, Mapping) else None
    return value if isinstance(value, str) else ""


def _executable_source_hash(source: str) -> str | None:
    """Hash executable Python structure while ignoring comments and formatting."""

    try:
        tree = ast.parse(source, filename="capability.py", mode="exec")
    except (SyntaxError, ValueError, TypeError):
        return None
    normalized = ast.dump(tree, annotate_fields=True, include_attributes=False)
    return content_hash(normalized.encode("utf-8"))


def _freeze_design(design: Mapping[str, Any], seal: Mapping[str, Any], binding_contract: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    frozen = copy.deepcopy(dict(design))
    design_hash = content_hash(canonical_bytes(frozen))
    try:
        if not verify_seal(dict(seal)) or seal.get("artifact_type") != "capability_design" or seal.get("artifact_hash") != design_hash:
            raise ContractError("Repair requires the exact sealed Capability Design")
    except Exception as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Repair requires the exact sealed Capability Design") from exc
    if binding_contract.get("design_hash") != design_hash:
        raise ContractError("Repair Design does not match the sealed Binding")
    return frozen, design_hash


def _sanitized_diagnostics(validation_a: ValidationAResult, validation_b: ValidationBResult | None) -> list[dict[str, str]]:
    if validation_a.status != "PASS":
        return [{"gate": "A", "code": item["code"]} for item in validation_a.diagnostics]
    if validation_b is not None and validation_b.status == "FAIL":
        return [{"gate": "B", "code": item["code"]} for item in validation_b.diagnostics]
    return []


def _repair_output(value: Any) -> tuple[str | None, int, dict[str, str] | None]:
    if not isinstance(value, Mapping):
        return None, 0, _issue("REPAIR_OUTPUT")
    output = dict(value)
    calls = output.get("llm_calls")
    if not isinstance(calls, int) or isinstance(calls, bool) or calls < 0:
        return None, 0, _issue("REPAIR_ACCOUNTING")
    if set(output) != {"capability.py", "llm_calls"} or not isinstance(output.get("capability.py"), str) or not output["capability.py"].strip():
        return None, calls, _issue("REPAIR_OUTPUT")
    return output["capability.py"], calls, None


def _expected_symbols(binding_contract: Mapping[str, Any]) -> list[dict[str, str]]:
    bindings = binding_contract.get("bindings")
    if not isinstance(bindings, list):
        raise ContractError("Repair requires a valid Python Binding Contract")
    symbols: list[dict[str, str]] = []
    for binding in bindings:
        if not isinstance(binding, Mapping) or not isinstance(binding.get("capability_id"), str) or not isinstance(binding.get("function_name"), str):
            raise ContractError("Repair requires a valid Python Binding Contract")
        symbols.append({"capability_id": binding["capability_id"], "function_name": binding["function_name"]})
    return symbols


class RepairRunner:
    def __init__(
        self,
        validation_a: ValidationARunner,
        validation_b: ValidationBRunner,
        repair_callback: RepairCallback,
        config: RepairConfig = RepairConfig(),
    ):
        if not callable(repair_callback):
            raise ContractError("Repair requires a callback")
        self._validation_a = validation_a
        self._validation_b = validation_b
        self._repair_callback = repair_callback
        self._config = config

    def run(
        self,
        capability_design: Mapping[str, Any],
        design_seal: Mapping[str, Any],
        binding_contract: Mapping[str, Any],
        binding_seal: Mapping[str, Any],
        initial_submission: Mapping[str, Any],
        initial_manifest: Mapping[str, Any],
        initial_manifest_seal: Mapping[str, Any],
        context: ValidationContext,
        implementation_bundle: Mapping[str, Any] | ImplementationBundle,
    ) -> RepairResult:
        bundle = validate_implementation_bundle(implementation_bundle)
        frozen_design, design_hash = _freeze_design(capability_design, design_seal, binding_contract)
        frozen_binding = copy.deepcopy(dict(binding_contract))
        frozen_binding_seal = copy.deepcopy(dict(binding_seal))
        frozen_context = freeze_validation_context(context)
        if frozen_context.design_hash != design_hash:
            raise ContractError("Repair run_snapshot does not bind the sealed Design")
        if (
            frozen_context.implementation_bundle_hash != bundle.bundle_hash
            or initial_manifest.get("implementation_bundle_hash") != bundle.bundle_hash
        ):
            raise ContractError("Repair requires one frozen Implementation Bundle across manifest and run_snapshot")
        binding_hash = content_hash(canonical_bytes(frozen_binding))
        hashes = {
            "design_hash": design_hash,
            "binding_contract_hash": binding_hash,
            "implementation_bundle_hash": bundle.bundle_hash,
            "blue_line_spec_hash": frozen_context.spec_hash,
            "blue_line_manifest_hash": frozen_context.manifest_hash,
            "suite_hash": frozen_context.suite_hash,
            "run_snapshot_hash": frozen_context.run_snapshot_hash,
        }
        ledger: list[dict[str, Any]] = []
        initial_a = self._validation_a.run(
            frozen_design,
            design_seal,
            frozen_binding,
            frozen_binding_seal,
            copy.deepcopy(dict(initial_submission)),
            copy.deepcopy(dict(initial_manifest)),
            copy.deepcopy(dict(initial_manifest_seal)),
            bundle.bundle_hash,
        )
        initial_b = self._run_b(initial_a, frozen_context, 0, 0, ledger)
        if initial_b is not None and initial_b.status == "PASS":
            return self._result("PASS", 0, 0, 0, 0, [], ledger, frozen_context, initial_a, initial_b, initial_a, initial_b, hashes)
        if initial_b is not None and initial_b.status == "INFRASTRUCTURE_ERROR":
            return self._result("INFRASTRUCTURE_ERROR", None, 0, 0, 0, [], ledger, frozen_context, initial_a, initial_b, initial_a, initial_b, hashes)

        current_source = _submission_source(initial_submission)
        current_a = initial_a
        current_b = initial_b
        source_history = {source_hash for source_hash in [initial_a.source_hash] if source_hash}
        executable_history = {
            executable_hash
            for executable_hash in [_executable_source_hash(current_source)]
            if executable_hash is not None
        }
        repair_log: list[dict[str, Any]] = []
        repair_invocations_used = 0
        candidate_revisions_created = 0
        total_llm_calls = 0
        previous_source_hash = initial_a.source_hash
        for requested_index in range(1, self._config.max_repairs + 1):
            request = {
                "repair_index": requested_index,
                "capability.py": current_source,
                "binding_contract": copy.deepcopy(frozen_binding),
                "implementation_bundle": bundle.artifact,
                "implementation_bundle_hash": bundle.bundle_hash,
                "design_hash": design_hash,
                "run_snapshot_hash": frozen_context.run_snapshot_hash,
                "diagnostics": _sanitized_diagnostics(current_a, current_b),
                "ledger": {
                    "repair_invocations_used": repair_invocations_used,
                    "candidate_revisions_created": candidate_revisions_created,
                    "max_repair_invocations": self._config.max_repairs,
                },
            }
            repair_invocations_used += 1
            try:
                raw = self._repair_callback(copy.deepcopy(request))
            except Exception:
                repair_log.append({
                    "repair_index": requested_index,
                    "candidate_revision_index": None,
                    "llm_calls": 0,
                    "status": "INFRASTRUCTURE_ERROR",
                    "invocation_consumed": True,
                    "candidate_revision_created": False,
                })
                return self._result("INFRASTRUCTURE_ERROR", None, repair_invocations_used, candidate_revisions_created, total_llm_calls, repair_log, ledger, frozen_context, initial_a, initial_b, current_a, current_b, hashes)
            repaired_source, llm_calls, output_issue = _repair_output(raw)
            total_llm_calls += llm_calls
            if output_issue is not None:
                repair_log.append({
                    "repair_index": requested_index,
                    "candidate_revision_index": None,
                    "llm_calls": llm_calls,
                    "status": "FAIL",
                    "diagnostics": [output_issue],
                    "invocation_consumed": True,
                    "candidate_revision_created": False,
                })
                continue
            source_hash = content_hash(repaired_source.encode("utf-8"))
            if source_hash in source_history:
                repair_log.append({
                    "repair_index": requested_index,
                    "candidate_revision_index": None,
                    "llm_calls": llm_calls,
                    "source_hash": source_hash,
                    "status": "NO_CHANGE",
                    "invocation_consumed": True,
                    "candidate_revision_created": False,
                })
                return self._result("NO_CHANGE", None, repair_invocations_used, candidate_revisions_created, total_llm_calls, repair_log, ledger, frozen_context, initial_a, initial_b, current_a, current_b, hashes)

            executable_hash = _executable_source_hash(repaired_source)
            if executable_hash is None:
                repair_log.append({
                    "repair_index": requested_index,
                    "candidate_revision_index": None,
                    "llm_calls": llm_calls,
                    "source_hash": source_hash,
                    "status": "FAIL",
                    "diagnostics": [_issue("REPAIR_SOURCE_SYNTAX")],
                    "invocation_consumed": True,
                    "candidate_revision_created": False,
                })
                continue
            if executable_hash is not None and executable_hash in executable_history:
                repair_log.append({
                    "repair_index": requested_index,
                    "candidate_revision_index": None,
                    "llm_calls": llm_calls,
                    "source_hash": source_hash,
                    "executable_source_hash": executable_hash,
                    "status": "NO_EXECUTABLE_CHANGE",
                    "invocation_consumed": True,
                    "candidate_revision_created": False,
                })
                return self._result("NO_EXECUTABLE_CHANGE", None, repair_invocations_used, candidate_revisions_created, total_llm_calls, repair_log, ledger, frozen_context, initial_a, initial_b, current_a, current_b, hashes)

            candidate_revisions_created += 1
            source_history.add(source_hash)
            if executable_hash is not None:
                executable_history.add(executable_hash)
            source_seal = create_seal(
                "capability.py",
                source_hash,
                [binding_hash, design_hash] + ([previous_source_hash] if previous_source_hash else []),
            )
            manifest, _manifest_hash, manifest_seal = derive_implementation_manifest(
                design_hash=design_hash,
                binding_hash=binding_hash,
                implementation_bundle_hash=bundle.bundle_hash,
                source_hash=source_hash,
                symbols=_expected_symbols(frozen_binding),
            )
            current_a = self._validation_a.run(
                frozen_design,
                design_seal,
                frozen_binding,
                frozen_binding_seal,
                {"capability.py": repaired_source},
                manifest,
                manifest_seal,
                bundle.bundle_hash,
            )
            current_b = self._run_b(
                current_a,
                frozen_context,
                requested_index,
                candidate_revisions_created,
                ledger,
            )
            repair_log.append({
                "repair_index": requested_index,
                "candidate_revision_index": candidate_revisions_created,
                "llm_calls": llm_calls,
                "source_hash": source_hash,
                "executable_source_hash": executable_hash,
                "source_seal": source_seal,
                "a_status": current_a.status,
                "b_status": current_b.status if current_b is not None else None,
                "status": (
                    "PASS" if current_b is not None and current_b.status == "PASS"
                    else "INFRASTRUCTURE_ERROR" if current_b is not None and current_b.status == "INFRASTRUCTURE_ERROR"
                    else "FAIL"
                ),
                "invocation_consumed": True,
                "candidate_revision_created": True,
            })
            if current_b is not None and current_b.status == "PASS":
                return self._result("PASS", requested_index, repair_invocations_used, candidate_revisions_created, total_llm_calls, repair_log, ledger, frozen_context, initial_a, initial_b, current_a, current_b, hashes)
            if current_b is not None and current_b.status == "INFRASTRUCTURE_ERROR":
                return self._result("INFRASTRUCTURE_ERROR", None, repair_invocations_used, candidate_revisions_created, total_llm_calls, repair_log, ledger, frozen_context, initial_a, initial_b, current_a, current_b, hashes)
            current_source = repaired_source
            previous_source_hash = source_hash
        return self._result("FAILED_AFTER_REPAIRS", None, repair_invocations_used, candidate_revisions_created, total_llm_calls, repair_log, ledger, frozen_context, initial_a, initial_b, current_a, current_b, hashes)

    def _run_b(
        self,
        validation_a: ValidationAResult,
        frozen: FrozenValidationContext,
        repair_invocation_index: int,
        revision_index: int,
        ledger: list[dict[str, Any]],
    ) -> ValidationBResult | None:
        if validation_a.status != "PASS" or validation_a.candidate_handle is None:
            return None
        candidate = bind_candidate_to_suite(validation_a, frozen.suite_hash)
        result: ValidationBResult | None = None
        for execution_attempt in range(1, self._config.max_infrastructure_retries + 2):
            result = self._validation_b.run(candidate, frozen.context)
            ledger.append({
                "repair_invocation_index": repair_invocation_index,
                "revision_index": revision_index,
                "execution_attempt": execution_attempt,
                "source_hash": candidate.source_hash,
                "implementation_manifest_hash": candidate.implementation_manifest_hash,
                "implementation_bundle_hash": candidate.implementation_bundle_hash,
                "overlay_hash": candidate.overlay_hash,
                "run_snapshot_hash": frozen.run_snapshot_hash,
                "b_status": result.status,
                "b_report_hash": result.report_hash,
            })
            if result.status != "INFRASTRUCTURE_ERROR":
                return result
        return result

    @staticmethod
    def _result(
        status: str,
        first_passing_repair_index: int | None,
        repair_invocations_used: int,
        candidate_revisions_created: int,
        repair_llm_calls: int,
        repair_log: list[dict[str, Any]],
        run_ledger: list[dict[str, Any]],
        frozen: FrozenValidationContext,
        initial_a: ValidationAResult,
        initial_b: ValidationBResult | None,
        final_a: ValidationAResult | None,
        final_b: ValidationBResult | None,
        hashes: dict[str, str],
    ) -> RepairResult:
        return RepairResult(
            status=status,
            first_passing_repair_index=first_passing_repair_index,
            repair_invocations_used=repair_invocations_used,
            candidate_revisions_created=candidate_revisions_created,
            repairs_consumed=repair_invocations_used,
            repair_llm_calls=repair_llm_calls,
            repair_log=tuple(copy.deepcopy(repair_log)),
            run_ledger=tuple(copy.deepcopy(run_ledger)),
            run_snapshot_hash=frozen.run_snapshot_hash,
            initial_validation_a=initial_a,
            initial_validation_b=initial_b,
            final_validation_a=final_a,
            final_validation_b=final_b,
            frozen_artifact_hashes=copy.deepcopy(hashes),
        )
