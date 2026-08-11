"""Callback-driven Validation B harness with trusted-verdict ownership."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash, is_content_hash
from ..foundation.seals import create_seal, verify_seal
from .validation_a import BindingOverlay


CaseExecutor = Callable[[ModuleType, Mapping[str, Any], Mapping[str, Any], int], Mapping[str, Any]]
_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class ValidationBResult:
    status: str
    report: dict[str, Any]
    report_hash: str
    report_seal: dict[str, Any]
    suite_hash: str
    overlay_hash: str
    executions: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, str], ...]


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _frozen_suite(suite: Mapping[str, Any], suite_seal: Mapping[str, Any]) -> tuple[dict[str, Any], str, list[dict[str, Any]], int]:
    frozen = copy.deepcopy(dict(suite))
    suite_hash = content_hash(canonical_bytes(frozen))
    try:
        if not verify_seal(dict(suite_seal)) or suite_seal.get("artifact_type") != "validation_b_suite" or suite_seal.get("artifact_hash") != suite_hash:
            raise ContractError("Validation B requires the exact sealed validation_b_suite")
    except Exception as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Validation B requires the exact sealed validation_b_suite") from exc
    if frozen.get("artifact_type") != "validation_b_suite" or frozen.get("schema_version") != _SCHEMA_VERSION:
        raise ContractError("Validation B suite identity is invalid")
    repetitions = frozen.get("repetitions")
    entries = frozen.get("capability_cases")
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1 or not isinstance(entries, list) or not entries:
        raise ContractError("Validation B suite is incomplete")
    seen: set[str] = set()
    checked: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or not _text(entry.get("capability_id")) or not isinstance(entry.get("cases"), list) or not entry["cases"]:
            raise ContractError("Validation B suite is incomplete")
        capability_id = entry["capability_id"]
        if capability_id in seen:
            raise ContractError("Validation B suite has duplicate capabilities")
        seen.add(capability_id)
        case_ids: set[str] = set()
        cases: list[dict[str, Any]] = []
        for case in entry["cases"]:
            if not isinstance(case, Mapping) or not _text(case.get("case_id")):
                raise ContractError("Validation B suite has an invalid case")
            if case["case_id"] in case_ids:
                raise ContractError("Validation B suite has duplicate case IDs")
            case_ids.add(case["case_id"])
            cases.append(copy.deepcopy(dict(case)))
        checked.append({"capability_id": capability_id, "cases": cases})
    return frozen, suite_hash, checked, repetitions


def _verified_overlay(overlay: BindingOverlay, suite_hash: str, expected_ids: set[str]) -> tuple[dict[str, Any], str]:
    if not isinstance(overlay, BindingOverlay):
        raise ContractError("Validation B requires a Framework-generated execution binding overlay")
    frozen = copy.deepcopy(overlay.overlay)
    overlay_hash = content_hash(canonical_bytes(frozen))
    try:
        if not verify_seal(dict(overlay.seal)) or overlay.seal.get("artifact_type") != "validation_execution_binding_overlay" or overlay.seal.get("artifact_hash") != overlay_hash:
            raise ContractError("Validation B execution binding overlay seal is invalid")
    except Exception as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Validation B execution binding overlay seal is invalid") from exc
    expected_fields = {
        "artifact_type", "schema_version", "suite_hash", "implementation_manifest_hash", "capability_bindings",
    }
    if set(frozen) != expected_fields or frozen.get("artifact_type") != "validation_execution_binding_overlay" or frozen.get("schema_version") != _SCHEMA_VERSION or frozen.get("suite_hash") != suite_hash:
        raise ContractError("Validation B execution binding overlay is invalid")
    manifest_hash = frozen.get("implementation_manifest_hash")
    if not is_content_hash(manifest_hash):
        raise ContractError("Validation B execution binding overlay is invalid")
    bindings = frozen.get("capability_bindings")
    if not isinstance(bindings, list):
        raise ContractError("Validation B execution binding overlay is invalid")
    seen: set[str] = set()
    for item in bindings:
        if not isinstance(item, Mapping) or set(item) != {"capability_id", "function_name"} or not _text(item.get("capability_id")) or not _text(item.get("function_name")):
            raise ContractError("Validation B execution binding overlay is invalid")
        if item["capability_id"] in seen:
            raise ContractError("Validation B execution binding overlay has duplicate capabilities")
        seen.add(item["capability_id"])
    if seen != expected_ids:
        raise ContractError("Validation B execution binding overlay must cover exactly the immutable suite")
    return frozen, overlay_hash


def verify_validation_suite(suite: Mapping[str, Any], suite_seal: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Check and copy the immutable suite without executing a candidate."""

    frozen, suite_hash, _entries, _repetitions = _frozen_suite(suite, suite_seal)
    return frozen, suite_hash


class ValidationBRunner:
    """Use a trusted injected executor; candidate returns are never verdict truth."""

    def __init__(self, case_executor: CaseExecutor):
        if not callable(case_executor):
            raise ContractError("Validation B requires a trusted case executor callback")
        self._case_executor = case_executor

    def run(
        self,
        validation_suite: Mapping[str, Any],
        suite_seal: Mapping[str, Any],
        overlay: BindingOverlay,
        candidate_module: ModuleType,
    ) -> ValidationBResult:
        suite, suite_hash, suite_entries, repetitions = _frozen_suite(validation_suite, suite_seal)
        expected_ids = {entry["capability_id"] for entry in suite_entries}
        overlay_data, overlay_hash = _verified_overlay(overlay, suite_hash, expected_ids)
        binding_by_capability = {item["capability_id"]: dict(item) for item in overlay_data["capability_bindings"]}
        for function_name in (item["function_name"] for item in binding_by_capability.values()):
            if not callable(getattr(candidate_module, function_name, None)):
                raise ContractError("Validation B candidate module no longer matches the A-verified binding overlay")

        executions: list[dict[str, Any]] = []
        diagnostics: list[dict[str, str]] = []
        infrastructure_error = False
        candidate_failure = False
        for entry in suite_entries:
            binding = binding_by_capability[entry["capability_id"]]
            for case in entry["cases"]:
                for repetition in range(1, repetitions + 1):
                    try:
                        trusted = self._case_executor(
                            candidate_module,
                            copy.deepcopy(binding),
                            copy.deepcopy(case),
                            repetition,
                        )
                    except Exception:
                        trusted = {"trusted_verdict": "INFRASTRUCTURE_ERROR"}
                    verdict = trusted.get("trusted_verdict") if isinstance(trusted, Mapping) else None
                    if verdict not in {"PASS", "FAIL", "INFRASTRUCTURE_ERROR"}:
                        verdict = "INFRASTRUCTURE_ERROR"
                    executions.append({
                        "capability_id": entry["capability_id"],
                        "case_id": case["case_id"],
                        "repetition": repetition,
                        "trusted_verdict": verdict,
                    })
                    if verdict == "INFRASTRUCTURE_ERROR":
                        diagnostics.append(_issue("INFRASTRUCTURE_ERROR", "trusted case execution was unavailable"))
                        infrastructure_error = True
                        break
                    if verdict == "FAIL":
                        diagnostics.append(_issue("TRUSTED_CASE_FAIL", f"trusted Harness rejected capability {entry['capability_id']}"))
                        candidate_failure = True
                if infrastructure_error:
                    break
            if infrastructure_error:
                break
        if content_hash(canonical_bytes(suite)) != suite_hash:
            diagnostics.append(_issue("SUITE_MUTATED", "the immutable validation suite changed during execution"))
            infrastructure_error = True
        if infrastructure_error:
            status = "INFRASTRUCTURE_ERROR"
        elif candidate_failure:
            status = "FAIL"
        else:
            expected_executions = sum(len(entry["cases"]) for entry in suite_entries) * repetitions
            if len(executions) != expected_executions:
                raise ContractError("Validation B did not cover every sealed capability case")
            status = "PASS"
        report = {
            "artifact_type": "validation_b_report",
            "schema_version": _SCHEMA_VERSION,
            "status": status,
            "suite_hash": suite_hash,
            "overlay_hash": overlay_hash,
            "implementation_manifest_hash": overlay_data["implementation_manifest_hash"],
            "executions": executions,
            "diagnostics": copy.deepcopy(diagnostics),
        }
        report_hash = content_hash(canonical_bytes(report))
        return ValidationBResult(
            status=status,
            report=report,
            report_hash=report_hash,
            report_seal=create_seal("validation_b_report", report_hash, [suite_hash, overlay_hash]),
            suite_hash=suite_hash,
            overlay_hash=overlay_hash,
            executions=tuple(executions),
            diagnostics=tuple(diagnostics),
        )

    validate = run
