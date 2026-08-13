"""Bounded implementation-only Repair with immutable revision and run ledgers."""

from __future__ import annotations

import ast
import copy
import re
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
    max_repairs: int = 3
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
    validation_b_attempts: tuple[dict[str, Any], ...] = ()


def _issue(code: str) -> dict[str, str]:
    return {"code": code, "message": code.replace("_", " ").lower()}


def _safe_infrastructure_error(exc: Exception) -> str:
    """Keep provider/framework failure evidence bounded and non-sensitive."""

    text = " ".join(str(exc).split())
    text = re.sub(r"https?://\S+", "<url>", text)
    text = re.sub(
        r"(?i)(api[_ -]?key|authorization|bearer|token|secret|password)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        text,
    )
    lowered = text.lower()
    if any(marker in lowered for marker in ("input_json", "request body", "payload", "messages", "capability.py")):
        text = "<redacted provider/framework detail>"
    if any(character in text for character in "{}[]"):
        text = "<redacted provider/framework detail>"
    detail = text[:260] or "<no provider detail>"
    return f"{type(exc).__name__}: {detail}"[:320]


_CANDIDATE_VALIDATION_A_CODES = frozenset({
    "EXPERIMENTAL_PROFILE",
    "SDK_FACADE",
    "SDK_DERIVED_MEMBER",
    "SDK_INJECTION",
})
_CANDIDATE_ERROR_CODES = frozenset({"CANDIDATE_EXCEPTION"})
_FORBIDDEN_CANDIDATE_DIAGNOSTIC_TERMS = (
    "criterion", "threshold", "measurement", "harness", "private", "input", "case", "seed",
)


def _candidate_owned_error(code: Any, value: Any) -> str | None:
    """Return only a bounded, candidate-owned diagnostic detail for Repair."""

    if code not in _CANDIDATE_VALIDATION_A_CODES | _CANDIDATE_ERROR_CODES:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    detail = " ".join(value.split())[:320]
    if any(term in detail.lower() for term in _FORBIDDEN_CANDIDATE_DIAGNOSTIC_TERMS):
        return None
    if detail == "TypeError: LowState_ is not an idl type.":
        return (
            "TypeError: ChannelSubscriber(topic, _sdk.LowState_ or _sdk.SportModeState_) "
            "requires an IDL type class, not a string name; publish LowCmd_ with "
            "_sdk.ChannelPublisher(topic, _sdk.LowCmd_) and "
            "_sdk.unitree_go_msg_dds__LowCmd_() then fill cmd.motor_cmd fields before Write."
        )
    return detail


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


def _sanitized_diagnostics(
    validation_a: ValidationAResult,
    validation_b: ValidationBResult | None,
    context: FrozenValidationContext | None = None,
) -> list[dict[str, Any]]:
    def safe_code(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip()
        if any(term in normalized.lower() for term in _FORBIDDEN_CANDIDATE_DIAGNOSTIC_TERMS):
            return None
        return normalized

    if validation_a.status != "PASS":
        diagnostics: list[dict[str, str]] = []
        for item in validation_a.diagnostics:
            code = safe_code(item.get("code"))
            if code is None:
                continue
            diagnostic = {"gate": "A", "code": code}
            candidate_error = _candidate_owned_error(item.get("code"), item.get("message"))
            if candidate_error is not None:
                diagnostic["candidate_error"] = candidate_error
            diagnostics.append(diagnostic)
        return diagnostics
    if validation_b is not None and validation_b.status == "FAIL":
        diagnostics: list[dict[str, Any]] = []
        invocation_inputs: dict[tuple[str, str], dict[str, Any]] = {}
        if context is not None:
            for entry in context.suite_entries:
                capability_id = entry.get("capability_id")
                cases = entry.get("cases")
                if not isinstance(capability_id, str) or not isinstance(cases, list):
                    continue
                for case in cases:
                    if (
                        isinstance(case, Mapping)
                        and isinstance(case.get("case_id"), str)
                        and isinstance(case.get("inputs"), Mapping)
                    ):
                        invocation_inputs[(capability_id, case["case_id"])] = copy.deepcopy(
                            dict(case["inputs"])
                        )
        for execution in validation_b.executions:
            if execution.get("verdict") != "FAIL":
                continue
            capability_id = execution.get("capability_id")
            case_id = execution.get("case_id")
            criterion_results = execution.get("criterion_results")
            if not isinstance(criterion_results, list) or not criterion_results:
                for raw_code in execution.get("failure_codes", []):
                    code = safe_code(raw_code)
                    if code is None:
                        continue
                    diagnostic = {
                        "gate": "B",
                        "code": code,
                        "capability_id": capability_id,
                        "case_id": case_id,
                        "public_invocation": invocation_inputs.get((capability_id, case_id), {}),
                    }
                    candidate_error = _candidate_owned_error(
                        "CANDIDATE_EXCEPTION", execution.get("candidate_error")
                    )
                    if candidate_error is not None:
                        diagnostic["candidate_error"] = candidate_error
                    diagnostics.append(diagnostic)
                continue
            for criterion in criterion_results:
                if not isinstance(criterion, Mapping) or criterion.get("verdict") != "FAIL":
                    continue
                failure_codes = [
                    code
                    for raw_code in criterion.get("failure_codes", [])
                    if (code := safe_code(raw_code)) is not None
                ]
                if not failure_codes:
                    continue
                diagnostic = {
                    "gate": "B",
                    "code": failure_codes[0],
                    "failure_codes": failure_codes,
                    "capability_id": capability_id,
                    "case_id": case_id,
                    "criterion_id": criterion.get("criterion_id"),
                    "public_invocation": invocation_inputs.get((capability_id, case_id), {}),
                    "observed": copy.deepcopy(criterion.get("observed", {})),
                    "expected": copy.deepcopy(criterion.get("expected", {})),
                    "failed_guard_ids": copy.deepcopy(criterion.get("failed_guard_ids", [])),
                    "sdk_route_valid": criterion.get("sdk_route_valid"),
                    "video_evidence_available": bool(execution.get("video_media_hash")),
                }
                candidate_error = _candidate_owned_error(
                    "CANDIDATE_EXCEPTION", execution.get("candidate_error")
                )
                if candidate_error is not None:
                    diagnostic["candidate_error"] = candidate_error
                diagnostics.append(diagnostic)
        return diagnostics
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


def _callback_trace(callback: RepairCallback) -> dict[str, Any] | None:
    """Read an optional public inner-episode trace from a bound callback owner."""

    owner = getattr(callback, "__self__", None)
    trace = getattr(owner, "last_repair_trace", None)
    if isinstance(trace, Mapping):
        return copy.deepcopy(dict(trace))
    return None


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
        validation_b_attempts: list[dict[str, Any]] = []
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
        initial_b = self._run_b(
            initial_a,
            frozen_context,
            0,
            0,
            ledger,
            validation_b_attempts,
        )
        if initial_b is not None and initial_b.status == "PASS":
            return self._result(
                "PASS", 0, 0, 0, 0, [], ledger, frozen_context,
                initial_a, initial_b, initial_a, initial_b, hashes,
                validation_b_attempts=validation_b_attempts,
            )
        if initial_b is not None and initial_b.status == "INFRASTRUCTURE_ERROR":
            return self._result(
                "INFRASTRUCTURE_ERROR", None, 0, 0, 0, [], ledger, frozen_context,
                initial_a, initial_b, initial_a, initial_b, hashes,
                validation_b_attempts=validation_b_attempts,
            )

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
                "diagnostics": _sanitized_diagnostics(current_a, current_b, frozen_context),
                "ledger": {
                    "repair_invocations_used": repair_invocations_used,
                    "candidate_revisions_created": candidate_revisions_created,
                    "max_repair_invocations": self._config.max_repairs,
                },
            }
            repair_invocations_used += 1
            try:
                raw = self._repair_callback(copy.deepcopy(request))
            except Exception as exc:
                repair_log.append({
                    "repair_index": requested_index,
                    "candidate_revision_index": None,
                    "llm_calls": 0,
                    "status": "INFRASTRUCTURE_ERROR",
                    "infrastructure_error": _safe_infrastructure_error(exc),
                    "invocation_consumed": True,
                    "candidate_revision_created": False,
                })
                return self._result(
                    "INFRASTRUCTURE_ERROR", None, repair_invocations_used,
                    candidate_revisions_created, total_llm_calls, repair_log, ledger,
                    frozen_context, initial_a, initial_b, current_a, current_b, hashes,
                    validation_b_attempts=validation_b_attempts,
                )
            repaired_source, llm_calls, output_issue = _repair_output(raw)
            total_llm_calls += llm_calls
            episode_trace = _callback_trace(self._repair_callback)
            if output_issue is not None:
                log_entry = {
                    "repair_index": requested_index,
                    "candidate_revision_index": None,
                    "llm_calls": llm_calls,
                    "status": "FAIL",
                    "diagnostics": [output_issue],
                    "invocation_consumed": True,
                    "candidate_revision_created": False,
                }
                if episode_trace is not None:
                    log_entry["episode_trace"] = episode_trace
                repair_log.append(log_entry)
                continue
            if episode_trace is not None and episode_trace.get("submitted") is not True:
                episode_source = episode_trace.get("working_source")
                if isinstance(episode_source, str) and episode_source.strip():
                    current_source = episode_source
                log_entry = {
                    "repair_index": requested_index,
                    "candidate_revision_index": None,
                    "llm_calls": llm_calls,
                    "status": "EPISODE_EXHAUSTED",
                    "invocation_consumed": True,
                    "candidate_revision_created": False,
                    "episode_trace": episode_trace,
                }
                repair_log.append(log_entry)
                continue
            source_hash = content_hash(repaired_source.encode("utf-8"))
            if source_hash in source_history:
                log_entry = {
                    "repair_index": requested_index,
                    "candidate_revision_index": None,
                    "llm_calls": llm_calls,
                    "source_hash": source_hash,
                    "status": "NO_CHANGE",
                    "invocation_consumed": True,
                    "candidate_revision_created": False,
                }
                if episode_trace is not None:
                    log_entry["episode_trace"] = episode_trace
                repair_log.append(log_entry)
                continue

            executable_hash = _executable_source_hash(repaired_source)
            if executable_hash is None:
                log_entry = {
                    "repair_index": requested_index,
                    "candidate_revision_index": None,
                    "llm_calls": llm_calls,
                    "source_hash": source_hash,
                    "status": "FAIL",
                    "diagnostics": [_issue("REPAIR_SOURCE_SYNTAX")],
                    "invocation_consumed": True,
                    "candidate_revision_created": False,
                }
                if episode_trace is not None:
                    log_entry["episode_trace"] = episode_trace
                repair_log.append(log_entry)
                continue
            if executable_hash is not None and executable_hash in executable_history:
                log_entry = {
                    "repair_index": requested_index,
                    "candidate_revision_index": None,
                    "llm_calls": llm_calls,
                    "source_hash": source_hash,
                    "executable_source_hash": executable_hash,
                    "status": "NO_EXECUTABLE_CHANGE",
                    "invocation_consumed": True,
                    "candidate_revision_created": False,
                }
                if episode_trace is not None:
                    log_entry["episode_trace"] = episode_trace
                repair_log.append(log_entry)
                continue

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
                validation_b_attempts,
            )
            log_entry = {
                "repair_index": requested_index,
                "candidate_revision_index": candidate_revisions_created,
                "llm_calls": llm_calls,
                "source_hash": source_hash,
                "executable_source_hash": executable_hash,
                "candidate_source": repaired_source,
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
            }
            if episode_trace is not None:
                log_entry["episode_trace"] = episode_trace
            repair_log.append(log_entry)
            if current_b is not None and current_b.status == "PASS":
                return self._result(
                    "PASS", requested_index, repair_invocations_used,
                    candidate_revisions_created, total_llm_calls, repair_log, ledger,
                    frozen_context, initial_a, initial_b, current_a, current_b, hashes,
                    validation_b_attempts=validation_b_attempts,
                )
            if current_b is not None and current_b.status == "INFRASTRUCTURE_ERROR":
                return self._result(
                    "INFRASTRUCTURE_ERROR", None, repair_invocations_used,
                    candidate_revisions_created, total_llm_calls, repair_log, ledger,
                    frozen_context, initial_a, initial_b, current_a, current_b, hashes,
                    validation_b_attempts=validation_b_attempts,
                )
            current_source = repaired_source
            previous_source_hash = source_hash
        return self._result(
            "FAILED_AFTER_REPAIRS", None, repair_invocations_used,
            candidate_revisions_created, total_llm_calls, repair_log, ledger,
            frozen_context, initial_a, initial_b, current_a, current_b, hashes,
            validation_b_attempts=validation_b_attempts,
        )

    def _run_b(
        self,
        validation_a: ValidationAResult,
        frozen: FrozenValidationContext,
        repair_invocation_index: int,
        revision_index: int,
        ledger: list[dict[str, Any]],
        validation_b_attempts: list[dict[str, Any]],
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
            validation_b_attempts.append({
                "repair_invocation_index": repair_invocation_index,
                "candidate_revision_index": revision_index,
                "execution_attempt": execution_attempt,
                "report_hash": result.report_hash,
                "report": copy.deepcopy(result.report),
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
        *,
        validation_b_attempts: list[dict[str, Any]] | None = None,
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
            validation_b_attempts=tuple(copy.deepcopy(validation_b_attempts or [])),
        )
