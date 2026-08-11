"""Bounded implementation-only Repair over the same sealed A/B inputs."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash
from ..foundation.seals import create_seal, verify_seal
from ..implementation.binding import derive_implementation_manifest
from .validation_a import ValidationAResult, ValidationARunner, create_execution_binding_overlay
from .validation_b import ValidationBResult, ValidationBRunner, verify_validation_suite


RepairCallback = Callable[[Mapping[str, Any]], Mapping[str, Any]]
_MAX_REPAIRS = 10


@dataclass(frozen=True)
class RepairResult:
    status: str
    first_passing_repair_index: int | None
    repairs_consumed: int
    repair_llm_calls: int
    repair_log: tuple[dict[str, Any], ...]
    initial_validation_a: ValidationAResult
    initial_validation_b: ValidationBResult | None
    final_validation_a: ValidationAResult | None
    final_validation_b: ValidationBResult | None
    frozen_artifact_hashes: dict[str, str]


def _issue(code: str) -> dict[str, str]:
    return {"code": code, "message": code.replace("_", " ").lower()}


def _source(submission: Mapping[str, Any]) -> str:
    value = submission.get("capability.py") if isinstance(submission, Mapping) else None
    return value if isinstance(value, str) else ""


def _freeze_design(
    binding_contract: Mapping[str, Any],
    capability_design: Mapping[str, Any] | None,
    design_seal: Mapping[str, Any] | None,
) -> str:
    design_hash = binding_contract.get("design_hash")
    if not isinstance(design_hash, str):
        raise ContractError("Repair requires a Python Binding Contract with a design hash")
    if capability_design is None and design_seal is None:
        return design_hash
    if not isinstance(capability_design, Mapping) or not isinstance(design_seal, Mapping):
        raise ContractError("Repair must receive both the sealed Design and its seal")
    frozen_design = copy.deepcopy(dict(capability_design))
    actual_hash = content_hash(canonical_bytes(frozen_design))
    try:
        if not verify_seal(dict(design_seal)) or design_seal.get("artifact_type") != "capability_design" or design_seal.get("artifact_hash") != actual_hash:
            raise ContractError("Repair requires the exact sealed Capability Design")
    except Exception as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Repair requires the exact sealed Capability Design") from exc
    if actual_hash != design_hash:
        raise ContractError("Repair Design does not match the frozen Python Binding")
    return design_hash


def _sanitized_diagnostics(validation_a: ValidationAResult, validation_b: ValidationBResult | None) -> list[dict[str, str]]:
    """Keep gate/category information while withholding cases, scores, and criteria."""

    if validation_a.status != "PASS":
        return [{"gate": "A", "code": item["code"]} for item in validation_a.diagnostics]
    if validation_b is not None and validation_b.status == "FAIL":
        return [{"gate": "B", "code": item["code"]} for item in validation_b.diagnostics]
    return []


def _repair_output(value: Any) -> tuple[str | None, int, dict[str, str] | None]:
    if not isinstance(value, Mapping):
        return None, 0, _issue("REPAIR_OUTPUT")
    output = dict(value)
    llm_calls = output.get("llm_calls")
    if not isinstance(llm_calls, int) or isinstance(llm_calls, bool) or llm_calls < 0:
        return None, 0, _issue("REPAIR_ACCOUNTING")
    if set(output) != {"capability.py", "llm_calls"} or not isinstance(output.get("capability.py"), str) or not output["capability.py"].strip():
        return None, llm_calls, _issue("REPAIR_OUTPUT")
    return output["capability.py"], llm_calls, None


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
    """Run the initial candidate and at most ten immutable-input repair revisions."""

    def __init__(self, validation_a: ValidationARunner, validation_b: ValidationBRunner, repair_callback: RepairCallback):
        if not callable(repair_callback):
            raise ContractError("Repair requires a callback")
        self._validation_a = validation_a
        self._validation_b = validation_b
        self._repair_callback = repair_callback

    def run(
        self,
        binding_contract: Mapping[str, Any],
        binding_seal: Mapping[str, Any],
        initial_submission: Mapping[str, Any],
        initial_manifest: Mapping[str, Any],
        initial_manifest_seal: Mapping[str, Any],
        validation_suite: Mapping[str, Any],
        suite_seal: Mapping[str, Any],
        *,
        capability_design: Mapping[str, Any] | None = None,
        design_seal: Mapping[str, Any] | None = None,
    ) -> RepairResult:
        frozen_binding = copy.deepcopy(dict(binding_contract))
        frozen_binding_seal = copy.deepcopy(dict(binding_seal))
        frozen_suite, suite_hash = verify_validation_suite(validation_suite, suite_seal)
        frozen_suite_seal = copy.deepcopy(dict(suite_seal))
        design_hash = _freeze_design(frozen_binding, capability_design, design_seal)
        binding_hash = content_hash(canonical_bytes(frozen_binding))
        frozen_hashes = {
            "design_hash": design_hash,
            "binding_contract_hash": binding_hash,
            "suite_hash": suite_hash,
        }

        initial_a = self._validation_a.run(
            frozen_binding,
            frozen_binding_seal,
            copy.deepcopy(dict(initial_submission)),
            copy.deepcopy(dict(initial_manifest)),
            copy.deepcopy(dict(initial_manifest_seal)),
        )
        initial_b = self._run_b_if_ready(initial_a, frozen_suite, frozen_suite_seal, suite_hash)
        if initial_b is not None and initial_b.status == "PASS":
            return self._result("PASS", 0, 0, 0, [], initial_a, initial_b, initial_a, initial_b, frozen_hashes)
        if initial_b is not None and initial_b.status == "INFRASTRUCTURE_ERROR":
            return self._result("INFRASTRUCTURE_ERROR", None, 0, 0, [], initial_a, initial_b, initial_a, initial_b, frozen_hashes)

        current_source = _source(initial_submission)
        current_a = initial_a
        current_b = initial_b
        repair_log: list[dict[str, Any]] = []
        repairs_consumed = 0
        total_llm_calls = 0
        previous_source_hash = initial_a.source_hash
        for repair_index in range(1, _MAX_REPAIRS + 1):
            request = {
                "repair_index": repair_index,
                "capability.py": current_source,
                "binding_contract": copy.deepcopy(frozen_binding),
                "design_hash": design_hash,
                "diagnostics": _sanitized_diagnostics(current_a, current_b),
                "ledger": {"repairs_consumed": repairs_consumed, "max_repairs": _MAX_REPAIRS},
            }
            try:
                raw_output = self._repair_callback(copy.deepcopy(request))
            except Exception:
                repair_log.append({
                    "repair_index": repair_index,
                    "llm_calls": 0,
                    "status": "INFRASTRUCTURE_ERROR",
                    "consumed": False,
                })
                return self._result(
                    "INFRASTRUCTURE_ERROR", None, repairs_consumed, total_llm_calls, repair_log,
                    initial_a, initial_b, current_a, current_b, frozen_hashes,
                )
            repaired_source, llm_calls, output_issue = _repair_output(raw_output)
            total_llm_calls += llm_calls
            if output_issue is not None:
                repairs_consumed += 1
                repair_log.append({
                    "repair_index": repair_index,
                    "llm_calls": llm_calls,
                    "status": "FAIL",
                    "diagnostics": [output_issue],
                    "consumed": True,
                })
                current_b = None
                continue

            source_hash = content_hash(repaired_source.encode("utf-8"))
            source_parents = [binding_hash, design_hash] + ([previous_source_hash] if previous_source_hash else [])
            source_seal = create_seal("capability.py", source_hash, source_parents)
            manifest, _manifest_hash, manifest_seal = derive_implementation_manifest(
                design_hash=design_hash,
                binding_hash=binding_hash,
                source_hash=source_hash,
                symbols=_expected_symbols(frozen_binding),
            )
            current_a = self._validation_a.run(
                frozen_binding,
                frozen_binding_seal,
                {"capability.py": repaired_source},
                manifest,
                manifest_seal,
            )
            current_b = self._run_b_if_ready(current_a, frozen_suite, frozen_suite_seal, suite_hash)
            if current_b is not None and current_b.status == "INFRASTRUCTURE_ERROR":
                repair_log.append({
                    "repair_index": repair_index,
                    "llm_calls": llm_calls,
                    "source_hash": source_hash,
                    "source_seal": source_seal,
                    "a_status": current_a.status,
                    "b_status": current_b.status,
                    "status": "INFRASTRUCTURE_ERROR",
                    "consumed": False,
                })
                return self._result(
                    "INFRASTRUCTURE_ERROR", None, repairs_consumed, total_llm_calls, repair_log,
                    initial_a, initial_b, current_a, current_b, frozen_hashes,
                )
            repairs_consumed += 1
            repair_log.append({
                "repair_index": repair_index,
                "llm_calls": llm_calls,
                "source_hash": source_hash,
                "source_seal": source_seal,
                "a_status": current_a.status,
                "b_status": current_b.status if current_b is not None else None,
                "status": "PASS" if current_b is not None and current_b.status == "PASS" else "FAIL",
                "consumed": True,
            })
            if current_b is not None and current_b.status == "PASS":
                return self._result(
                    "PASS", repair_index, repairs_consumed, total_llm_calls, repair_log,
                    initial_a, initial_b, current_a, current_b, frozen_hashes,
                )
            current_source = repaired_source
            previous_source_hash = source_hash
        return self._result(
            "FAILED_AFTER_REPAIRS", None, repairs_consumed, total_llm_calls, repair_log,
            initial_a, initial_b, current_a, current_b, frozen_hashes,
        )

    def _run_b_if_ready(
        self,
        validation_a: ValidationAResult,
        suite: Mapping[str, Any],
        suite_seal: Mapping[str, Any],
        suite_hash: str,
    ) -> ValidationBResult | None:
        if validation_a.status != "PASS" or validation_a.candidate_module is None:
            return None
        overlay = create_execution_binding_overlay(validation_a, suite_hash)
        return self._validation_b.run(copy.deepcopy(dict(suite)), copy.deepcopy(dict(suite_seal)), overlay, validation_a.candidate_module)

    @staticmethod
    def _result(
        status: str,
        first_passing_repair_index: int | None,
        repairs_consumed: int,
        repair_llm_calls: int,
        repair_log: list[dict[str, Any]],
        initial_a: ValidationAResult,
        initial_b: ValidationBResult | None,
        final_a: ValidationAResult | None,
        final_b: ValidationBResult | None,
        frozen_hashes: dict[str, str],
    ) -> RepairResult:
        return RepairResult(
            status=status,
            first_passing_repair_index=first_passing_repair_index,
            repairs_consumed=repairs_consumed,
            repair_llm_calls=repair_llm_calls,
            repair_log=tuple(copy.deepcopy(repair_log)),
            initial_validation_a=initial_a,
            initial_validation_b=initial_b,
            final_validation_a=final_a,
            final_validation_b=final_b,
            frozen_artifact_hashes=copy.deepcopy(frozen_hashes),
        )
