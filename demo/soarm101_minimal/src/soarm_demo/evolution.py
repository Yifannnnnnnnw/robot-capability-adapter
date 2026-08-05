"""Trusted Evolution evidence, claim evaluation, privacy, and publication.

The LLM-facing runtime has read-only tools.  This module is the single
deterministic authority that compiles a redacted evidence projection, checks
one proposed Experience claim, and publishes it only after all trusted gates
pass.  ``write_candidate_bundle`` remains a legacy run-local diagnostic helper;
the Evolution runtime never calls it on a historical source run.
"""

from __future__ import annotations

import heapq
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .audit import (
    atomic_write_json,
    canonical_json,
    sha256_bytes,
    sha256_file,
    sha256_json,
)
from .report_audit import (
    demo_report_structure_semantic_errors,
    terminal_report_semantic_errors,
)
from .schema_validation import ValidationIssue, validate_json_schema
from .libraries import (
    ExperienceLibrary,
    LibraryEntry,
    LibraryError,
    experience_generation_view,
    experience_public_privacy_issue_codes,
)


BUNDLE_SCHEMA_VERSION = "robot_capability.evolution_candidate_bundle.v1"
_DEMO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SCHEMA_ROOT = _DEMO_ROOT / "schemas"
_LIBRARY_RECORDS = _DEMO_ROOT / "libraries/experience/v1/records.jsonl"

_HARD_MAX_FILES_PER_KIND = 16
_HARD_MAX_JSON_BYTES = 2 * 1024 * 1024
_HARD_MAX_FAILURES_PER_FEEDBACK = 64
_HARD_MAX_CANDIDATES = 12
_HARD_MAX_DEMO_TASKS = 64

_SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_SAFE_FAILURE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_CAPABILITY_ID_RE = re.compile(r"^G[123]\.[a-z][a-z0-9_]{0,63}$")
_SAFE_PUBLIC_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$"
)

_NUMBERED_ARTIFACTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("feedback", re.compile(r"^feedback_(\d{2})\.json$")),
    ("repair_result", re.compile(r"^repair_result_(\d{2})\.json$")),
    ("static_report", re.compile(r"^static_round_(\d{2})\.json$")),
    ("direct_report", re.compile(r"^direct_round_(\d{2})\.json$")),
)

# These terminal states do not describe capability behaviour and therefore
# cannot provide repair experience.
_INFRASTRUCTURE_STATES = {
    "INPUT_INVALID",
    "INFRASTRUCTURE_FAILED",
}
_PROMOTION_GATE_NAMES = ("schema", "evidence", "privacy", "replay", "human_approval")


class EvolutionError(RuntimeError):
    """The run could not be converted into a schema-valid candidate bundle."""


@dataclass(frozen=True)
class EvolutionLimits:
    """Hard-bounded collection limits, optionally lowered by tests/callers."""

    max_files_per_kind: int = 12
    # Terminal reports bind the complete run evidence index and can be larger
    # than an individual causal artifact.  One MiB admits the observed real
    # AWS report (598,979 bytes) while retaining the existing two-MiB hard
    # ceiling and exact-byte hashing; oversized inputs are still rejected, not
    # truncated.
    max_json_bytes: int = 1024 * 1024
    max_failures_per_feedback: int = 32
    max_candidates: int = 10
    max_demo_tasks: int = 16

    def __post_init__(self) -> None:
        bounds = {
            "max_files_per_kind": (self.max_files_per_kind, _HARD_MAX_FILES_PER_KIND),
            "max_json_bytes": (self.max_json_bytes, _HARD_MAX_JSON_BYTES),
            "max_failures_per_feedback": (
                self.max_failures_per_feedback,
                _HARD_MAX_FAILURES_PER_FEEDBACK,
            ),
            "max_candidates": (self.max_candidates, _HARD_MAX_CANDIDATES),
            "max_demo_tasks": (self.max_demo_tasks, _HARD_MAX_DEMO_TASKS),
        }
        for name, (value, maximum) in bounds.items():
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer between 1 and {maximum}")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_files_per_kind": self.max_files_per_kind,
            "max_json_bytes": self.max_json_bytes,
            "max_failures_per_feedback": self.max_failures_per_feedback,
            "max_candidates": self.max_candidates,
            "max_demo_tasks": self.max_demo_tasks,
        }


@dataclass(frozen=True)
class _Artifact:
    kind: str
    relative_path: str
    byte_count: int
    sha256: str | None
    status: str
    value: Mapping[str, Any] | None
    hash_bound: bool | None = None
    semantic_valid: bool | None = None

    def index_record(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.relative_path,
            "bytes": self.byte_count,
            "sha256": self.sha256,
            "status": self.status,
            "hash_bound": self.hash_bound,
            "semantic_valid": self.semantic_valid,
        }

    def evidence_ref(self) -> dict[str, Any]:
        if self.sha256 is None:
            raise EvolutionError("unhashed artifact cannot be used as candidate evidence")
        return {"path": self.relative_path, "sha256": self.sha256}

    def with_status(self, status: str) -> "_Artifact":
        return _Artifact(
            kind=self.kind,
            relative_path=self.relative_path,
            byte_count=self.byte_count,
            sha256=self.sha256,
            status=status,
            value=self.value,
            hash_bound=self.hash_bound,
            semantic_valid=self.semantic_valid,
        )

    def with_verification(
        self,
        status: str,
        *,
        hash_bound: bool | None,
        semantic_valid: bool | None,
    ) -> "_Artifact":
        return _Artifact(
            kind=self.kind,
            relative_path=self.relative_path,
            byte_count=self.byte_count,
            sha256=self.sha256,
            status=status,
            value=self.value,
            hash_bound=hash_bound,
            semantic_valid=semantic_valid,
        )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def _load_schema(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise EvolutionError(f"invalid Evolution schema {path.name}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise EvolutionError(f"Evolution schema {path.name} must be an object")
    return value


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _read_artifact(
    run_root: Path,
    path: Path,
    *,
    kind: str,
    max_json_bytes: int,
) -> _Artifact:
    relative = path.relative_to(run_root).as_posix()
    try:
        if path.is_symlink() or not path.is_file():
            return _Artifact(kind, relative, 0, None, "unsafe_or_missing", None)
        resolved = path.resolve(strict=True)
        if not _is_within(resolved, run_root):
            return _Artifact(kind, relative, 0, None, "unsafe_or_missing", None)
        byte_count = path.stat().st_size
    except OSError:
        return _Artifact(kind, relative, 0, None, "unsafe_or_missing", None)
    if byte_count > max_json_bytes:
        return _Artifact(kind, relative, byte_count, None, "oversized", None)
    try:
        with path.open("rb") as handle:
            payload = handle.read(max_json_bytes + 1)
        if len(payload) > max_json_bytes:
            return _Artifact(kind, relative, len(payload), None, "oversized", None)
        digest = sha256_bytes(payload)
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        digest = None
        try:
            digest = sha256_bytes(payload)
        except UnboundLocalError:
            pass
        return _Artifact(kind, relative, byte_count, digest, "invalid_json", None)
    if not isinstance(value, dict):
        return _Artifact(kind, relative, byte_count, digest, "invalid_shape", None)
    return _Artifact(kind, relative, byte_count, digest, "parsed", value)


def _bounded_numbered_paths(
    directory: Path,
    pattern: re.Pattern[str],
    limit: int,
) -> tuple[list[Path], int]:
    if directory.is_symlink() or not directory.is_dir():
        return [], 0
    discovered = 0

    def matching() -> Iterable[Path]:
        nonlocal discovered
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if not pattern.fullmatch(entry.name) or not entry.is_file(
                        follow_symlinks=False
                    ):
                        continue
                    discovered += 1
                    yield Path(entry.path)
        except OSError:
            return

    selected = heapq.nsmallest(limit, matching(), key=lambda item: item.name)
    return selected, discovered


def _collect_artifacts(
    run_root: Path,
    limits: EvolutionLimits,
) -> tuple[list[_Artifact], dict[str, int]]:
    artifacts: list[_Artifact] = []
    discovered_by_kind: dict[str, int] = {}
    validation_root = run_root / "validation"
    for kind, pattern in _NUMBERED_ARTIFACTS:
        paths, discovered = _bounded_numbered_paths(
            validation_root, pattern, limits.max_files_per_kind
        )
        discovered_by_kind[kind] = discovered
        artifacts.extend(
            _read_artifact(
                run_root,
                path,
                kind=kind,
                max_json_bytes=limits.max_json_bytes,
            )
            for path in paths
        )

    exact = (
        ("demo_report", run_root / "demo/demo_report.json"),
        ("sealed_report", run_root / "sealed_report.json"),
        ("terminal_report", run_root / "terminal_report.json"),
        ("run_manifest", run_root / "run_manifest.json"),
    )
    for kind, path in exact:
        exists = path.is_file() and not path.is_symlink()
        discovered_by_kind[kind] = int(exists)
        if exists:
            artifacts.append(
                _read_artifact(
                    run_root,
                    path,
                    kind=kind,
                    max_json_bytes=limits.max_json_bytes,
                )
            )
    order = {kind: index for index, kind in enumerate(
        [item[0] for item in _NUMBERED_ARTIFACTS]
        + ["demo_report", "sealed_report", "terminal_report", "run_manifest"]
    )}
    artifacts.sort(key=lambda item: (order[item.kind], item.relative_path))
    return artifacts, discovered_by_kind


def _verify_source_reports(
    artifacts: Sequence[_Artifact],
    *,
    schema_root: Path,
) -> tuple[list[_Artifact], list[str]]:
    """Trust a terminal source only when schema- and manifest-bound.

    A filename and a self-declared ``terminal_state`` are not provenance.  The
    final run manifest must bind the exact report bytes, run identity, terminal
    state, and every artifact hash already claimed by the report.  Invalid or
    unbound reports remain indexed for audit but cannot support a successful or
    confirmed-failure Experience claim.
    """

    manifest = next(
        (
            item
            for item in artifacts
            if item.kind == "run_manifest" and item.status == "parsed"
        ),
        None,
    )
    manifest_value = None if manifest is None else manifest.value
    report_specs = {
        "sealed_report": (
            "sealed_run_report.schema.json",
            "sealed_report",
        ),
        "terminal_report": (
            "terminal_run_report.schema.json",
            "terminal_report",
        ),
    }
    schemas = {
        kind: _load_schema(schema_root / schema_name)
        for kind, (schema_name, _) in report_specs.items()
    }
    verified: list[_Artifact] = []
    issue_codes: set[str] = set()
    for artifact in artifacts:
        spec = report_specs.get(artifact.kind)
        if spec is None or artifact.status != "parsed" or artifact.value is None:
            verified.append(artifact)
            continue

        issues = validate_json_schema(
            artifact.value,
            schemas[artifact.kind],
            instance_path=f"$evolution_source.{artifact.kind}",
        )
        if issues:
            issue_codes.add(f"{artifact.kind}_schema_invalid")
            verified.append(
                artifact.with_verification(
                    "invalid_schema",
                    hash_bound=False,
                    semantic_valid=False,
                )
            )
            continue

        binding_key = spec[1]
        if not isinstance(manifest_value, Mapping):
            issue_codes.add("run_manifest_missing_or_invalid")
            verified.append(
                artifact.with_verification(
                    "unbound",
                    hash_bound=False,
                    semantic_valid=True,
                )
            )
            continue
        manifest_hashes = manifest_value.get("artifact_hashes")
        report_hashes = artifact.value.get("artifact_hashes")
        bound = (
            isinstance(artifact.sha256, str)
            and isinstance(manifest_hashes, Mapping)
            and manifest_hashes.get(binding_key) == artifact.sha256
            and manifest_value.get("run_id") == artifact.value.get("run_id")
            and manifest_value.get("state") == artifact.value.get("terminal_state")
            and isinstance(report_hashes, Mapping)
            and all(manifest_hashes.get(key) == value for key, value in report_hashes.items())
        )
        if not bound:
            issue_codes.add(f"{artifact.kind}_manifest_binding_invalid")
            verified.append(
                artifact.with_verification(
                    "unbound",
                    hash_bound=False,
                    semantic_valid=True,
                )
            )
            continue
        verified.append(
            artifact.with_verification(
                "parsed",
                hash_bound=True,
                semantic_valid=True,
            )
        )

    return verified, sorted(issue_codes)


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _is_integer(value: Any, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _has_exact_keys(value: Mapping[str, Any], expected: set[str]) -> bool:
    return set(value) == expected


def _static_report_semantic_valid(value: Mapping[str, Any]) -> bool:
    required = {
        "schema_version",
        "passed",
        "artifact_root",
        "stage1_sha256",
        "checked_files",
        "failures",
    }
    if not _has_exact_keys(value, required):
        return False
    if value.get("schema_version") != "robot_capability.static_validation_report.v1":
        return False
    passed = value.get("passed")
    checked = value.get("checked_files")
    failures = value.get("failures")
    if not isinstance(passed, bool) or not isinstance(value.get("artifact_root"), str):
        return False
    if value.get("stage1_sha256") is not None and not _is_sha256(
        value.get("stage1_sha256")
    ):
        return False
    if (
        not isinstance(checked, list)
        or not all(isinstance(item, str) and item for item in checked)
        or len(checked) != len(set(checked))
        or not isinstance(failures, list)
    ):
        return False
    failure_keys = {
        "code",
        "message",
        "path",
        "line",
        "column",
        "capability_id",
        "observed",
        "target",
    }
    for failure in failures:
        if not isinstance(failure, Mapping) or not _has_exact_keys(failure, failure_keys):
            return False
        if not isinstance(failure.get("code"), str) or not isinstance(
            failure.get("message"), str
        ):
            return False
        for field in ("path", "capability_id"):
            if failure.get(field) is not None and not isinstance(failure.get(field), str):
                return False
        for field in ("line", "column"):
            if failure.get(field) is not None and not _is_integer(
                failure.get(field), minimum=0
            ):
                return False
    return passed is (len(failures) == 0)


def _direct_case_semantic_valid(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    required = {
        "case_id",
        "capability_id",
        "function_name",
        "passed",
        "elapsed_s",
        "result",
        "observed_measurements",
        "target_measurements",
        "tolerances",
        "measurement_failures",
        "forbidden_evidence",
        "framework_diagnostics",
        "exception_type",
        "exception_message",
        "traceback_lines",
        "timed_out",
    }
    if not _has_exact_keys(value, required):
        return False
    if not all(
        isinstance(value.get(field), str) and bool(value.get(field))
        for field in ("case_id", "capability_id", "function_name")
    ):
        return False
    elapsed = value.get("elapsed_s")
    if (
        not isinstance(elapsed, (int, float))
        or isinstance(elapsed, bool)
        or not math.isfinite(float(elapsed))
        or float(elapsed) < 0.0
    ):
        return False
    for field in (
        "observed_measurements",
        "target_measurements",
        "tolerances",
        "forbidden_evidence",
        "framework_diagnostics",
    ):
        if not isinstance(value.get(field), Mapping):
            return False
    failures = value.get("measurement_failures")
    traceback_lines = value.get("traceback_lines")
    if not isinstance(failures, list) or not all(isinstance(item, str) for item in failures):
        return False
    if not isinstance(traceback_lines, list) or not all(
        isinstance(item, str) for item in traceback_lines
    ):
        return False
    for field in ("exception_type", "exception_message"):
        if value.get(field) is not None and not isinstance(value.get(field), str):
            return False
    if not isinstance(value.get("timed_out"), bool) or not isinstance(
        value.get("passed"), bool
    ):
        return False
    expected_passed = (
        value.get("exception_type") is None
        and value.get("timed_out") is False
        and len(failures) == 0
    )
    return value.get("passed") is expected_passed


def _direct_report_semantic_valid(value: Mapping[str, Any]) -> bool:
    required = {
        "schema_version",
        "passed",
        "suite_sha256",
        "package_root",
        "package_sha256",
        "package_unchanged",
        "cases",
        "summary",
    }
    if not _has_exact_keys(value, required):
        return False
    if value.get("schema_version") != "robot_capability.direct_validation_report.v1":
        return False
    if not _is_sha256(value.get("suite_sha256")) or not _is_sha256(
        value.get("package_sha256")
    ):
        return False
    if not isinstance(value.get("package_root"), str) or not value.get("package_root"):
        return False
    if not isinstance(value.get("passed"), bool) or not isinstance(
        value.get("package_unchanged"), bool
    ):
        return False
    cases = value.get("cases")
    if not isinstance(cases, list) or not cases or not all(
        _direct_case_semantic_valid(item) for item in cases
    ):
        return False
    case_ids = [item["case_id"] for item in cases]
    if len(case_ids) != len(set(case_ids)):
        return False
    passed_count = sum(item["passed"] is True for item in cases)
    failed_count = len(cases) - passed_count
    summary = value.get("summary")
    if not isinstance(summary, Mapping) or not _has_exact_keys(
        summary, {"total", "passed", "failed"}
    ):
        return False
    if any(
        not _is_integer(summary.get(field), minimum=0)
        for field in ("total", "passed", "failed")
    ):
        return False
    if summary != {
        "total": len(cases),
        "passed": passed_count,
        "failed": failed_count,
    }:
        return False
    expected_passed = (
        bool(cases)
        and failed_count == 0
        and value.get("package_unchanged") is True
    )
    return value.get("passed") is expected_passed


def _repair_process_semantic_valid(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    required = {
        "schema_version",
        "tool_call_count",
        "successful_write_calls",
        "protocol_error_count",
        "truncated_response_count",
        "provider_failure_count",
        "final_rejection_count",
        "last_action",
        "unfinished_read",
        "tool_calls",
        "privacy",
    }
    if not _has_exact_keys(value, required):
        return False
    if value.get("schema_version") != "robot_capability.repair_process_audit.v1":
        return False
    calls = value.get("tool_calls")
    if not isinstance(calls, list) or len(calls) > 6:
        return False
    if value.get("tool_call_count") != len(calls):
        return False
    for field in (
        "successful_write_calls",
        "protocol_error_count",
        "truncated_response_count",
        "provider_failure_count",
        "final_rejection_count",
    ):
        if not _is_integer(value.get(field), minimum=0) or value[field] > 6:
            return False
    call_keys = {
        "ordinal",
        "agent_turn",
        "tool",
        "operation",
        "outcome",
        "target",
        "arguments_sha256",
        "result_sha256",
    }
    for index, call in enumerate(calls, start=1):
        if not isinstance(call, Mapping) or not _has_exact_keys(call, call_keys):
            return False
        if call.get("ordinal") != index or not _is_integer(
            call.get("agent_turn"), minimum=1
        ):
            return False
        if not isinstance(call.get("tool"), str) or not re.fullmatch(
            r"[A-Za-z0-9_.:/-]{1,200}", call["tool"]
        ):
            return False
        if call.get("operation") not in {"read", "write", "check", "other"}:
            return False
        if call.get("outcome") not in {"ok", "timeout", "schema_rejected", "error"}:
            return False
        if not isinstance(call.get("target"), Mapping):
            return False
        for field in ("arguments_sha256", "result_sha256"):
            if call.get(field) is not None and not _is_sha256(call.get(field)):
                return False
    successful_writes = sum(
        1
        for call in calls
        if call.get("operation") == "write" and call.get("outcome") == "ok"
    )
    if value.get("successful_write_calls") != successful_writes:
        return False
    expected_unfinished = (
        calls[-1]
        if calls and calls[-1].get("operation") == "read"
        else None
    )
    if value.get("unfinished_read") != expected_unfinished:
        return False
    privacy = value.get("privacy")
    return privacy == {
        "raw_model_text_included": False,
        "raw_tool_arguments_included": False,
        "raw_tool_results_included": False,
        "raw_source_included": False,
    }


def _repair_result_semantic_valid(
    value: Mapping[str, Any],
    *,
    artifact_round: int | None,
    feedback_schema: Mapping[str, Any],
) -> bool:
    required = {
        "schema_version",
        "repair_round",
        "status",
        "code",
        "package_before_sha256",
        "package_after_sha256",
        "package_changed",
        "validation_skipped",
        "next_repair_round",
        "feedback",
        "agent_accounting",
        "repair_process",
    }
    if not _has_exact_keys(value, required):
        return False
    repair_round = value.get("repair_round")
    if (
        value.get("schema_version")
        != "robot_capability.repair_package_gate_result.v2"
        or not _is_integer(repair_round, minimum=1)
        or repair_round > 10
        or repair_round != artifact_round
        or not _is_sha256(value.get("package_before_sha256"))
        or not _is_sha256(value.get("package_after_sha256"))
        or not isinstance(value.get("package_changed"), bool)
    ):
        return False
    changed = value["package_changed"]
    before = value["package_before_sha256"]
    after = value["package_after_sha256"]
    if changed is not (before != after):
        return False
    expected_status = "package_changed" if changed else "no_package_change"
    expected_code = "PACKAGE_CHANGED" if changed else "NO_PACKAGE_CHANGE"
    if value.get("status") != expected_status or value.get("code") != expected_code:
        return False
    skipped = value.get("validation_skipped")
    if not isinstance(skipped, Mapping) or not _has_exact_keys(
        skipped, {"static", "direct", "video"}
    ):
        return False
    if any(skipped.get(field) is not (not changed) for field in skipped):
        return False
    next_round = value.get("next_repair_round")
    feedback = value.get("feedback")
    if changed:
        if next_round is not None or feedback is not None:
            return False
    else:
        expected_next = repair_round + 1 if repair_round < 10 else None
        if next_round != expected_next or not isinstance(feedback, Mapping):
            return False
        if validate_json_schema(feedback, feedback_schema):
            return False
        expected_feedback_round = expected_next or repair_round
        no_change = feedback.get("no_package_change")
        if (
            feedback.get("repair_round") != expected_feedback_round
            or not isinstance(no_change, Mapping)
            or no_change.get("completed_repair_round") != repair_round
            or no_change.get("package_sha256") != after
            or no_change.get("retry_available") != (expected_next is not None)
        ):
            return False
    if not _repair_process_semantic_valid(value.get("repair_process")):
        return False
    accounting = value.get("agent_accounting")
    required_accounting = {
        "round",
        "status",
        "agent_turns",
        "provider_http_attempts",
        "provider_retries",
        "client_kind",
        "scripted",
        "agent_turn_budget",
        "provider_attempt_budget_policy",
        "usage",
        "session_id",
        "episode_id",
    }
    if not isinstance(accounting, Mapping) or not _has_exact_keys(
        accounting, required_accounting
    ):
        return False
    if accounting.get("round") != repair_round:
        return False
    if not all(
        _is_integer(accounting.get(field), minimum=0)
        for field in ("agent_turns", "provider_http_attempts", "provider_retries")
    ):
        return False
    if not isinstance(accounting.get("scripted"), bool) or not all(
        isinstance(accounting.get(field), str) and bool(accounting.get(field))
        for field in ("status", "client_kind", "session_id", "episode_id")
    ):
        return False
    return all(
        isinstance(accounting.get(field), Mapping)
        for field in ("agent_turn_budget", "provider_attempt_budget_policy", "usage")
    )


def _artifact_semantic_valid(
    artifact: _Artifact,
    *,
    feedback_schema: Mapping[str, Any],
    demo_schema: Mapping[str, Any],
) -> bool:
    value = artifact.value
    if value is None:
        return False
    if artifact.kind == "feedback":
        return (
            not validate_json_schema(value, feedback_schema)
            and value.get("repair_round") == _round_number(artifact)
        )
    if artifact.kind == "static_report":
        return _static_report_semantic_valid(value)
    if artifact.kind == "direct_report":
        return _direct_report_semantic_valid(value)
    if artifact.kind == "repair_result":
        return _repair_result_semantic_valid(
            value,
            artifact_round=_round_number(artifact),
            feedback_schema=feedback_schema,
        )
    if artifact.kind == "demo_report":
        return (
            not validate_json_schema(value, demo_schema)
            and not demo_report_structure_semantic_errors(value)
        )
    return True


def _verify_candidate_evidence(
    artifacts: Sequence[_Artifact],
    *,
    schema_root: Path,
) -> list[_Artifact]:
    """Keep causal artifacts only when source-bound and semantically valid."""

    feedback_schema = _load_schema(schema_root / "failure_feedback.schema.json")
    demo_schema = _load_schema(schema_root / "demo_report.schema.json")

    source = next(
        (
            item
            for kind in ("sealed_report", "terminal_report")
            for item in artifacts
            if item.kind == kind and item.status == "parsed" and item.value is not None
        ),
        None,
    )
    manifest = next(
        (
            item
            for item in artifacts
            if item.kind == "run_manifest" and item.status == "parsed" and item.value is not None
        ),
        None,
    )
    if source is None:
        return [
            item.with_verification(
                "unbound",
                hash_bound=False,
                semantic_valid=None,
            )
            if item.status == "parsed"
            and item.kind in {
                "feedback",
                "repair_result",
                "static_report",
                "direct_report",
                "demo_report",
            }
            else item
            for item in artifacts
        ]

    bound_hashes: dict[str, str] = {}
    embedded_feedback: list[Mapping[str, Any]] = []
    source_value = source.value
    assert source_value is not None
    if source.kind == "terminal_report":
        evidence_files = source_value.get("evidence_files")
        if isinstance(evidence_files, Sequence) and not isinstance(
            evidence_files, (str, bytes, bytearray)
        ):
            for ref in evidence_files:
                if (
                    isinstance(ref, Mapping)
                    and isinstance(ref.get("path"), str)
                    and isinstance(ref.get("sha256"), str)
                ):
                    bound_hashes[ref["path"]] = ref["sha256"]
    else:
        evidence = source_value.get("evidence")
        if isinstance(evidence, Mapping):
            for key in ("static_validation", "direct_validation", "demo"):
                refs = evidence.get(key)
                if not isinstance(refs, Sequence) or isinstance(
                    refs, (str, bytes, bytearray)
                ):
                    continue
                for ref in refs:
                    if (
                        isinstance(ref, Mapping)
                        and isinstance(ref.get("path"), str)
                        and isinstance(ref.get("sha256"), str)
                    ):
                        bound_hashes[ref["path"]] = ref["sha256"]
        repair = source_value.get("repair")
        history = repair.get("history") if isinstance(repair, Mapping) else None
        if isinstance(history, Sequence) and not isinstance(
            history, (str, bytes, bytearray)
        ):
            embedded_feedback = [item for item in history if isinstance(item, Mapping)]
        manifest_events = (
            manifest.value.get("events")
            if manifest is not None and isinstance(manifest.value, Mapping)
            else None
        )
        if isinstance(manifest_events, Sequence) and not isinstance(
            manifest_events, (str, bytes, bytearray)
        ):
            for event in manifest_events:
                if (
                    isinstance(event, Mapping)
                    and event.get("event") == "repair_package_change_gate"
                    and isinstance(event.get("result_path"), str)
                    and isinstance(event.get("result_sha256"), str)
                ):
                    bound_hashes[event["result_path"]] = event["result_sha256"]

    verified: list[_Artifact] = []
    causal_kinds = {
        "feedback",
        "repair_result",
        "static_report",
        "direct_report",
        "demo_report",
    }
    for artifact in artifacts:
        if artifact.status != "parsed" or artifact.kind not in causal_kinds:
            verified.append(artifact)
            continue
        hash_bound = (
            isinstance(artifact.sha256, str)
            and bound_hashes.get(artifact.relative_path) == artifact.sha256
        )
        feedback_bound = (
            artifact.kind == "feedback"
            and artifact.value is not None
            and any(dict(value) == dict(artifact.value) for value in embedded_feedback)
        )
        if not (hash_bound or feedback_bound):
            verified.append(
                artifact.with_verification(
                    "unbound",
                    hash_bound=False,
                    semantic_valid=None,
                )
            )
            continue
        if not _artifact_semantic_valid(
            artifact,
            feedback_schema=feedback_schema,
            demo_schema=demo_schema,
        ):
            verified.append(
                artifact.with_verification(
                    "semantic_invalid",
                    hash_bound=True,
                    semantic_valid=False,
                )
            )
            continue
        verified.append(
            artifact.with_verification(
                "parsed",
                hash_bound=True,
                semantic_valid=True,
            )
        )
    return verified


def _parsed_by_kind(artifacts: Sequence[_Artifact], kind: str) -> list[_Artifact]:
    return [item for item in artifacts if item.kind == kind and item.status == "parsed"]


def _round_number(artifact: _Artifact) -> int | None:
    match = re.search(r"_(\d{2})\.json$", artifact.relative_path)
    return None if match is None else int(match.group(1))


def _round_index(artifacts: Sequence[_Artifact], kind: str) -> dict[int, _Artifact]:
    result: dict[int, _Artifact] = {}
    for artifact in _parsed_by_kind(artifacts, kind):
        number = _round_number(artifact)
        if number is not None and number not in result:
            result[number] = artifact
    return result


def _safe_run_id(value: Any, fallback: str) -> str:
    if isinstance(value, str) and _SAFE_RUN_ID_RE.fullmatch(value):
        return value
    material = value if isinstance(value, str) and value else fallback
    return "run-" + sha256_bytes(str(material).encode("utf-8"))[:24]


def _safe_timestamp(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 64:
        return "1970-01-01T00:00:00Z"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "1970-01-01T00:00:00Z"
    if parsed.tzinfo is None:
        return "1970-01-01T00:00:00Z"
    return value


def _run_context(
    run_root: Path,
    artifacts: Sequence[_Artifact],
    *,
    source_issues: Sequence[str],
) -> dict[str, Any]:
    preferred: _Artifact | None = None
    for kind in ("sealed_report", "terminal_report", "run_manifest"):
        values = _parsed_by_kind(artifacts, kind)
        if values:
            preferred = values[0]
            break
    source = {} if preferred is None or preferred.value is None else preferred.value
    run_id = _safe_run_id(source.get("run_id"), run_root.name)
    mode = source.get("mode") if source.get("mode") in {"aws", "offline"} else "unknown"
    terminal_state = source.get("terminal_state")
    if not isinstance(terminal_state, str) or not re.fullmatch(r"[A-Z_]{2,64}", terminal_state):
        terminal_state = source.get("state")
    if not isinstance(terminal_state, str) or not re.fullmatch(r"[A-Z_]{2,64}", terminal_state):
        terminal_state = "UNKNOWN"
    timestamp = _safe_timestamp(source.get("sealed_at", source.get("ended_at")))
    return {
        "run_id": run_id,
        "mode": mode,
        "terminal_state": terminal_state,
        "observed_at": timestamp,
        "source_report": None if preferred is None else preferred.evidence_ref(),
        "source_report_verified": (
            preferred is not None
            and preferred.kind in {"sealed_report", "terminal_report"}
        ),
        "source_report_issues": list(source_issues),
    }


def _safe_failure_code(value: Any) -> str:
    return value if isinstance(value, str) and _SAFE_FAILURE_CODE_RE.fullmatch(value) else "UNCLASSIFIED_FAILURE"


def _safe_capability_id(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_CAPABILITY_ID_RE.fullmatch(value):
        return value
    return None


def _failure_summary(
    feedback: Mapping[str, Any], limits: EvolutionLimits
) -> tuple[int, list[str], list[str], bool]:
    raw = feedback.get("failures")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return 0, ["UNCLASSIFIED_FAILURE"], [], False
    total = len(raw)
    codes: set[str] = set()
    capabilities: set[str] = set()
    for failure in raw[: limits.max_failures_per_feedback]:
        if not isinstance(failure, Mapping):
            codes.add("UNCLASSIFIED_FAILURE")
            continue
        codes.add(_safe_failure_code(failure.get("code")))
        capability = _safe_capability_id(failure.get("capability_id"))
        if capability is not None:
            capabilities.add(capability)
    if not codes:
        codes.add("UNCLASSIFIED_FAILURE")
    return total, sorted(codes), sorted(capabilities), total <= limits.max_failures_per_feedback


def _safe_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _failed_model_requests(repair_value: Any) -> int:
    """Count audited model requests that produced no response in one repair.

    P0 keeps provider retries disabled, so an Agent turn is one logical model
    request.  A transport failure can consume that turn without producing a
    response.  Such a no-change repair is unresolved infrastructure evidence,
    not a negative implementation lesson for the Experience library.
    """

    if not isinstance(repair_value, Mapping):
        return 0
    accounting = repair_value.get("agent_accounting")
    if not isinstance(accounting, Mapping):
        return 0
    agent_turns = accounting.get("agent_turns")
    usage = accounting.get("usage")
    response_count = usage.get("response_count") if isinstance(usage, Mapping) else None
    if (
        isinstance(agent_turns, bool)
        or not isinstance(agent_turns, int)
        or isinstance(response_count, bool)
        or not isinstance(response_count, int)
        or not 0 <= response_count <= agent_turns
    ):
        return 0
    return agent_turns - response_count


def _repair_process_experience_summary(repair_value: Any) -> dict[str, Any] | None:
    """Project only observable, non-content actions into an Experience candidate."""

    if not isinstance(repair_value, Mapping):
        return None
    process = repair_value.get("repair_process")
    if not _repair_process_semantic_valid(process):
        return None
    assert isinstance(process, Mapping)
    calls = process.get("tool_calls")
    assert isinstance(calls, list)
    return {
        "tool_call_count": process["tool_call_count"],
        "successful_write_calls": process["successful_write_calls"],
        "protocol_error_count": process["protocol_error_count"],
        "truncated_response_count": process["truncated_response_count"],
        "provider_failure_count": process["provider_failure_count"],
        "final_rejection_count": process["final_rejection_count"],
        "last_action": process["last_action"],
        "ended_after_unfinished_read": process["unfinished_read"] is not None,
        "tool_sequence": [
            {
                "tool": call["tool"],
                "operation": call["operation"],
                "outcome": call["outcome"],
            }
            for call in calls
        ],
        "raw_content_included": False,
    }


def _stage_after_evidence(
    *,
    stage: str,
    repair_round: int,
    repair_result: _Artifact | None,
    static_reports: Mapping[int, _Artifact],
    direct_reports: Mapping[int, _Artifact],
) -> tuple[bool | None, list[_Artifact], bool]:
    """Return stage result, refs, and whether package identity is causally bound."""

    reports: list[_Artifact] = []
    if stage == "static":
        static_report = static_reports.get(repair_round)
        if static_report is None or static_report.value is None:
            return None, reports, False
        reports.append(static_report)
        passed = _safe_bool(static_report.value.get("passed"))
        if passed is not True:
            # The v1 static report does not carry a package-tree hash.  Its
            # filename/round alone is not a cryptographic before→after binding,
            # so a failure is useful evidence but remains causally unresolved.
            return passed, reports, False
        # Static reports do not carry the package tree hash.  A same-round direct
        # report binds the changed package and prevents a false causal success.
        direct_report = direct_reports.get(repair_round)
        if direct_report is None or direct_report.value is None:
            return True, reports, False
        reports.append(direct_report)
        direct_passed = _safe_bool(direct_report.value.get("passed"))
        package_bound = False
        if repair_result is not None and repair_result.value is not None:
            after_hash = repair_result.value.get("package_after_sha256")
            package_bound = (
                isinstance(after_hash, str)
                and direct_report.value.get("package_sha256") == after_hash
            )
        return direct_passed, reports, package_bound

    direct_report = direct_reports.get(repair_round)
    if direct_report is None or direct_report.value is None:
        return None, reports, False
    reports.append(direct_report)
    passed = _safe_bool(direct_report.value.get("passed"))
    package_bound = False
    if repair_result is not None and repair_result.value is not None:
        after_hash = repair_result.value.get("package_after_sha256")
        package_bound = (
            isinstance(after_hash, str)
            and direct_report.value.get("package_sha256") == after_hash
        )
    return passed, reports, package_bound


def _sealed_demo_passed(artifacts: Sequence[_Artifact]) -> bool:
    sealed = _parsed_by_kind(artifacts, "sealed_report")
    demo_reports = _parsed_by_kind(artifacts, "demo_report")
    if (
        not sealed
        or sealed[0].value is None
        or not demo_reports
        or demo_reports[0].value is None
    ):
        return False
    value = sealed[0].value
    if value.get("terminal_state") != "SEALED" or value.get("terminal_reason") != "completed":
        return False
    demo = value.get("demo")
    if not isinstance(demo, Mapping):
        return False
    overall = demo.get("overall")
    if not isinstance(overall, Mapping):
        summary = demo.get("summary")
        overall = summary.get("overall") if isinstance(summary, Mapping) else None
    if not isinstance(overall, Mapping):
        return False
    report = demo_reports[0].value
    report_summary = report.get("summary")
    report_tasks = report.get("tasks")
    if (
        not isinstance(report_summary, Mapping)
        or report_summary != demo.get("summary")
        or report_tasks != demo.get("tasks")
        or report.get("run_id") != value.get("run_id")
        or report.get("package_sha256")
        != value.get("artifact_hashes", {}).get("generated_package")
    ):
        return False
    passed = overall.get("passed")
    total = overall.get("total")
    return (
        isinstance(passed, int)
        and not isinstance(passed, bool)
        and isinstance(total, int)
        and not isinstance(total, bool)
        and total == 6
        and passed == total
    )


def _gate(satisfied: bool, reason: str) -> dict[str, Any]:
    return {"required": True, "satisfied": satisfied, "reason": reason}


def _candidate_promotion(*, evidence_complete: bool) -> dict[str, Any]:
    gates = {
        "schema": _gate(True, "bundle writer validated the proposed record against experience.schema.json"),
        "evidence": _gate(
            evidence_complete,
            "causal before/change/after evidence is complete"
            if evidence_complete
            else "causal evidence is incomplete or unresolved",
        ),
        "privacy": _gate(False, "an explicit privacy/declassification review is required"),
        "replay": _gate(False, "an independent replay run is required"),
        "human_approval": _gate(False, "a human must approve the exact candidate hash"),
    }
    return {
        "status": "awaiting_explicit_approval",
        "eligible": all(item["satisfied"] for item in gates.values()),
        "gates": gates,
    }


def _candidate_identity(
    *,
    run_id: str,
    source_stage: str,
    repair_round: int | None,
    claim_kind: str,
    codes: Sequence[str],
    evidence_sha256s: Sequence[str],
) -> str:
    """Return the immutable identity of one conclusion over exact evidence."""

    identity = {
        "run_id": run_id,
        "source_stage": source_stage,
        "repair_round": repair_round,
        "claim_kind": claim_kind,
        "failure_codes": list(codes),
        "evidence_sha256s": sorted(set(evidence_sha256s)),
    }
    digest = sha256_json(identity)
    suffix = "demo" if repair_round is None else f"r{repair_round:02d}"
    return f"evolution.{digest[:20]}.{suffix}"


def _candidate_payload_sha256(candidate: Mapping[str, Any]) -> str:
    """Bind the complete review payload without creating a hash self-reference.

    ``candidate_id`` identifies a conclusion over source evidence.  Human review
    needs a stronger, full-payload commitment because wording, scope, dedupe,
    evidence summaries, and gate state can change while that identity remains
    stable.  The detached digest covers every candidate field except the digest
    field itself.
    """

    payload = dict(candidate)
    payload.pop("candidate_payload_sha256", None)
    return sha256_json(payload)


def _candidate_dedupe_key(
    *,
    source_stage: str,
    claim_kind: str,
    codes: Sequence[str],
    capabilities: Sequence[str],
) -> str:
    """Return a cross-run key for repeated lessons with the same safe scope.

    The run and repair round intentionally do not participate.  Those belong
    to ``candidate_id`` and evidence provenance; including them here would make
    every observation unique and prevent Evolution from recognizing repeated
    failures across independent runs.
    """

    return sha256_json(
        {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "robot_id": "soarm101",
            "runtime_id": "lerobot_soarm101_0_6_0",
            "source_stage": source_stage,
            "claim_kind": claim_kind,
            "failure_codes": sorted(set(codes)),
            "capability_ids": sorted(set(capabilities)),
        }
    )


def _scope(capabilities: Sequence[str]) -> dict[str, Any]:
    granularities = sorted({item.split(".", 1)[0] for item in capabilities})
    return {
        "robot_ids": ["soarm101"],
        "runtime_ids": ["lerobot_soarm101_0_6_0"],
        "granularities": granularities,
        "capability_ids": list(capabilities),
    }


def _validation_candidate(
    *,
    feedback: _Artifact,
    repair_results: Mapping[int, _Artifact],
    static_reports: Mapping[int, _Artifact],
    direct_reports: Mapping[int, _Artifact],
    run_context: Mapping[str, Any],
    sealed_demo_passed: bool,
    limits: EvolutionLimits,
) -> dict[str, Any] | None:
    assert feedback.value is not None
    if "no_package_change" in feedback.value:
        # This is carried causal feedback, not a new lesson.
        return None
    stage = feedback.value.get("stage")
    if stage not in {"static", "direct_function"}:
        return None
    repair_round = _round_number(feedback)
    if repair_round is None:
        return None
    failure_count, codes, capabilities, failures_complete = _failure_summary(
        feedback.value, limits
    )
    if failure_count < 1:
        return None
    repair_result = repair_results.get(repair_round)
    repair_value = None if repair_result is None else repair_result.value
    repair_attempted = repair_value is not None
    package_changed = (
        _safe_bool(repair_value.get("package_changed"))
        if isinstance(repair_value, Mapping)
        else None
    )
    after_passed, after_reports, package_bound = _stage_after_evidence(
        stage=stage,
        repair_round=repair_round,
        repair_result=repair_result,
        static_reports=static_reports,
        direct_reports=direct_reports,
    )
    terminal_state = str(run_context["terminal_state"])
    mode = str(run_context["mode"])
    source_verified = run_context.get("source_report_verified") is True
    failed_model_requests = _failed_model_requests(repair_value)
    repair_process_summary = _repair_process_experience_summary(repair_value)
    repair_infrastructure_affected = (
        failed_model_requests > 0 and package_changed is not True
    )
    infrastructure_or_unknown = (
        terminal_state in _INFRASTRUCTURE_STATES
        or terminal_state == "UNKNOWN"
        or not source_verified
        or repair_infrastructure_affected
    )

    success: bool | None
    positive_evidence = (
        source_verified
        and repair_attempted
        and package_changed is True
        and after_passed is True
        and package_bound
        and sealed_demo_passed
        and (stage == "static" or mode == "aws")
    )
    confirmed_negative = (
        repair_attempted
        and (
            package_changed is False
            or (after_passed is False and package_bound)
        )
    )
    if positive_evidence:
        success = True
    elif infrastructure_or_unknown:
        success = None
    elif confirmed_negative:
        success = False
    else:
        success = None

    claim_kind = (
        "successful_repair"
        if success is True
        else "failed_repair"
        if success is False
        else "unresolved_failure"
    )
    source_stage = "static_validation" if stage == "static" else "direct_function_validation"
    candidate_id = _candidate_identity(
        run_id=str(run_context["run_id"]),
        source_stage=source_stage,
        repair_round=repair_round,
        claim_kind=claim_kind,
        codes=codes,
        evidence_sha256s=[
            value
            for value in [
                feedback.sha256,
                None if repair_result is None else repair_result.sha256,
                *(item.sha256 for item in after_reports),
            ]
            if isinstance(value, str)
        ],
    )
    dedupe_key = _candidate_dedupe_key(
        source_stage=source_stage,
        claim_kind=claim_kind,
        codes=codes,
        capabilities=capabilities,
    )
    stage_label = "static" if stage == "static" else "direct-function"
    symptom = (
        f"{stage_label} validation reported {failure_count} redacted failure(s) "
        f"across codes {', '.join(codes)}."
    )
    if success is True:
        hypothesis = (
            "A package-changing repair was followed by package-bound validation "
            "success and the complete sealed Demo gate."
        )
        guidance = (
            "Preserve only the reviewed package delta associated with this causal "
            "chain and rerun all frozen validation and Demo gates."
        )
        lesson = "A bounded repair candidate has positive run-local causal evidence."
        recommended_pattern = "Use the reviewed delta only within the approved scope."
    elif success is False:
        hypothesis = (
            "The attempted repair did not produce a complete passing downstream "
            "evidence chain."
        )
        guidance = (
            "Treat the attempted delta as a negative result; do not reuse it as a "
            "successful repair pattern."
        )
        lesson = "The attempted repair is negative evidence, not a successful pattern."
        recommended_pattern = "Use the record only to avoid repeating the failed attempt."
    else:
        hypothesis = (
            "The run ended without enough package-bound, non-infrastructure evidence "
            "to determine whether the repair worked."
        )
        guidance = (
            "Keep this candidate quarantined until the missing validation, privacy, "
            "and replay evidence is supplied."
        )
        lesson = "The failure remains unresolved and must not be presented as success."
        recommended_pattern = "Collect a complete causal replay before reuse."

    evidence_refs = [feedback.relative_path]
    if repair_result is not None:
        evidence_refs.append(repair_result.relative_path)
    evidence_refs.extend(item.relative_path for item in after_reports)
    evidence_refs = list(dict.fromkeys(evidence_refs))
    regressions: list[str] = []
    if after_passed is False:
        regressions.append("validation_still_failing")
    if terminal_state == "DEMO_FAILED":
        regressions.append("downstream_demo_gate_failed")
    if repair_infrastructure_affected:
        regressions.append("model_request_transport_failure")

    proposed_record = {
        "schema_version": "robot_capability.experience_record.v1",
        "experience_id": candidate_id,
        "version": "0.0.0",
        "status": "candidate",
        "conclusion_kind": (
            "positive" if success is True else "negative" if success is False else "unresolved"
        ),
        "scope": _scope(capabilities),
        "origin": {
            "source_stage": source_stage,
            "run_id": run_context["run_id"],
            "case_or_task_ids": [f"validation_feedback_round_{repair_round:02d}"],
            "observed_at": run_context["observed_at"],
        },
        "failure_summary": {
            "category": f"{stage_label}_validation",
            "symptom": symptom,
            "preconditions": [f"Generated package evaluated by the {stage_label} gate."],
        },
        "diagnosis": {
            "hypothesis": hypothesis,
            "confidence": 0.8 if success is not None else 0.25,
            "alternatives_considered": [
                "infrastructure or incomplete evidence was kept separate from capability evidence"
            ],
        },
        "recommended_change": {
            "change_type": "implementation",
            "guidance": guidance,
            "must_preserve": ["frozen Stage 1 semantics", "frozen public API"],
        },
        "evidence": {
            "artifact_refs": evidence_refs,
            "before_measurements": {
                "failure_count": failure_count,
                "failure_codes": codes,
                "feedback_sha256": feedback.sha256,
            },
            "after_measurements": {
                "package_changed": package_changed,
                "stage_passed": after_passed,
                "package_identity_bound": package_bound,
                "sealed_demo_passed": sealed_demo_passed,
                "source_report_verified": source_verified,
                "terminal_state": terminal_state,
                "failed_model_requests": failed_model_requests,
                "repair_process": repair_process_summary,
            },
        },
        "outcome": {
            "repair_attempted": repair_attempted,
            "repair_succeeded": success,
            "regressions": regressions,
            "reuse_risk": "medium" if success is True else "high",
        },
        "scientific_claims": {
            "framework_change_applied": False,
            "capability_improvement_proven": False,
            "new_generation_validation_required": True,
        },
        "generation_summary": {
            "applicability": f"SO-ARM101 {stage_label} repair candidates in the approved scope.",
            "lesson": lesson,
            "recommended_pattern": recommended_pattern,
            "avoid_pattern": "Do not infer repair success from package-byte change alone.",
        },
    }
    evidence_complete = (
        failures_complete
        and source_verified
        and (
            (
                success is True
                and repair_attempted
                and package_changed is True
                and after_passed is True
                and package_bound
                and sealed_demo_passed
            )
            or (success is False and confirmed_negative)
        )
    )
    return {
        "candidate_id": candidate_id,
        "claim_kind": claim_kind,
        "dedupe_key": dedupe_key,
        "source_stage": source_stage,
        "source_round": repair_round,
        "proposed_record": proposed_record,
        "evidence_summary": {
            "failure_count": failure_count,
            "failure_codes": codes,
            "feedback": feedback.evidence_ref(),
            "repair_result": None
            if repair_result is None
            else repair_result.evidence_ref(),
            "after_reports": [item.evidence_ref() for item in after_reports],
            "package_changed": package_changed,
            "after_stage_passed": after_passed,
            "package_identity_bound": package_bound,
            "sealed_demo_passed": sealed_demo_passed,
            "source_report_verified": source_verified,
            "causal_outcome": (
                "confirmed_success"
                if success is True
                else "confirmed_failure"
                if success is False
                else "unresolved"
            ),
        },
        "privacy": {
            "classification": "private_run_local",
            "raw_messages_copied": False,
            "tracebacks_copied": False,
            "case_or_task_ids_copied": False,
            "exact_measurements_copied": False,
        },
        "promotion": _candidate_promotion(evidence_complete=evidence_complete),
    }


def _demo_candidate(
    *,
    demo_report: _Artifact,
    run_context: Mapping[str, Any],
    limits: EvolutionLimits,
) -> tuple[dict[str, Any] | None, str | None]:
    assert demo_report.value is not None
    if run_context["terminal_state"] in _INFRASTRUCTURE_STATES:
        return None, "infrastructure_demo_excluded"
    tasks = demo_report.value.get("tasks")
    if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes, bytearray)):
        return None, "invalid_demo_shape"
    if len(tasks) > limits.max_demo_tasks:
        return None, "demo_task_limit_exceeded"
    failed = 0
    infrastructure = False
    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        if task.get("termination_reason") == "infrastructure_failure" or task.get(
            "agent_status"
        ) == "infrastructure_failed":
            infrastructure = True
        elif task.get("passed") is False:
            failed += 1
    if infrastructure:
        return None, "infrastructure_demo_excluded"
    if failed == 0:
        return None, None

    codes = ["DEMO_ORACLE_GATE_FAILED"]
    candidate_id = _candidate_identity(
        run_id=str(run_context["run_id"]),
        source_stage="demo",
        repair_round=None,
        claim_kind="demo_failure",
        codes=codes,
        evidence_sha256s=(
            [] if demo_report.sha256 is None else [demo_report.sha256]
        ),
    )
    dedupe_key = _candidate_dedupe_key(
        source_stage="demo",
        claim_kind="demo_failure",
        codes=codes,
        capabilities=(),
    )
    proposed_record = {
        "schema_version": "robot_capability.experience_record.v1",
        "experience_id": candidate_id,
        "version": "0.0.0",
        "status": "candidate",
        "conclusion_kind": "unresolved",
        "scope": _scope([]),
        "origin": {
            "source_stage": "demo",
            "run_id": run_context["run_id"],
            "case_or_task_ids": ["private_demo_gate"],
            "observed_at": run_context["observed_at"],
        },
        "failure_summary": {
            "category": "demo_oracle",
            "symptom": f"The private Demo gate reported {failed} redacted task failure(s).",
            "preconditions": ["Frozen validated tools were evaluated by the private Demo oracle."],
        },
        "diagnosis": {
            "hypothesis": (
                "The Demo failure is unresolved because Demo has no repair-and-replay "
                "loop in the current run."
            ),
            "confidence": 0.2,
            "alternatives_considered": [
                "task-specific, oracle, and infrastructure details remain private"
            ],
        },
        "recommended_change": {
            "change_type": "validation",
            "guidance": (
                "Keep this candidate private and reproduce the failure without exposing "
                "held-out task or oracle details before proposing a reusable change."
            ),
            "must_preserve": ["private task isolation", "trusted oracle boundary"],
        },
        "evidence": {
            "artifact_refs": [demo_report.relative_path],
            "before_measurements": {"demo_failed_task_count": failed},
            "after_measurements": {
                "demo_repair_enabled": False,
                "repair_replay_available": False,
                "terminal_state": run_context["terminal_state"],
            },
        },
        "outcome": {
            "repair_attempted": False,
            "repair_succeeded": None,
            "regressions": ["private_demo_gate_failed"],
            "reuse_risk": "high",
        },
        "scientific_claims": {
            "framework_change_applied": False,
            "capability_improvement_proven": False,
            "new_generation_validation_required": True,
        },
        "generation_summary": {
            "applicability": "Private SO-ARM101 Demo failures pending declassification.",
            "lesson": "Validation success alone did not establish complete Demo success.",
            "recommended_pattern": "Reproduce through an independent privacy-safe replay.",
            "avoid_pattern": "Do not expose private task IDs, states, or oracle thresholds.",
        },
    }
    return (
        {
            "candidate_id": candidate_id,
            "claim_kind": "demo_failure",
            "dedupe_key": dedupe_key,
            "source_stage": "demo",
            "source_round": None,
            "proposed_record": proposed_record,
            "evidence_summary": {
                "failure_count": failed,
                "failure_codes": codes,
                "feedback": demo_report.evidence_ref(),
                "repair_result": None,
                "after_reports": [],
                "package_changed": None,
                "after_stage_passed": None,
                "package_identity_bound": False,
                "sealed_demo_passed": False,
                "source_report_verified": (
                    run_context.get("source_report_verified") is True
                ),
                "causal_outcome": "unresolved",
            },
            "privacy": {
                "classification": "private_run_local",
                "raw_messages_copied": False,
                "tracebacks_copied": False,
                "case_or_task_ids_copied": False,
                "exact_measurements_copied": False,
            },
            "promotion": _candidate_promotion(evidence_complete=False),
        },
        None,
    )


def _format_issues(label: str, issues: Sequence[ValidationIssue]) -> EvolutionError:
    details = "; ".join(f"{item.path}: {item.message}" for item in issues[:20])
    return EvolutionError(f"{label} failed schema validation: {details}")


def build_candidate_bundle(
    run_root: str | Path,
    *,
    schema_root: str | Path | None = None,
    limits: EvolutionLimits | None = None,
) -> dict[str, Any]:
    """Build and validate one deterministic, redacted run-local candidate bundle."""

    root = Path(run_root).resolve()
    if not root.is_dir():
        raise EvolutionError(f"run root is not a directory: {root}")
    active_limits = limits or EvolutionLimits()
    schemas = Path(schema_root).resolve() if schema_root is not None else _DEFAULT_SCHEMA_ROOT
    bundle_schema_path = schemas / "evolution_candidate_bundle.schema.json"
    experience_schema_path = schemas / "experience.schema.json"
    bundle_schema = _load_schema(bundle_schema_path)
    experience_schema = _load_schema(experience_schema_path)

    artifacts, discovered = _collect_artifacts(root, active_limits)
    artifacts, source_issues = _verify_source_reports(
        artifacts,
        schema_root=schemas,
    )
    artifacts = _verify_candidate_evidence(artifacts, schema_root=schemas)
    run_context = _run_context(
        root,
        artifacts,
        source_issues=source_issues,
    )
    repair_results = _round_index(artifacts, "repair_result")
    static_reports = _round_index(artifacts, "static_report")
    direct_reports = _round_index(artifacts, "direct_report")
    demo_passed = _sealed_demo_passed(artifacts)

    candidates: list[dict[str, Any]] = []
    carried_feedback_omitted = 0
    candidate_limit_omitted = 0
    for feedback in _parsed_by_kind(artifacts, "feedback"):
        candidate = _validation_candidate(
            feedback=feedback,
            repair_results=repair_results,
            static_reports=static_reports,
            direct_reports=direct_reports,
            run_context=run_context,
            sealed_demo_passed=demo_passed,
            limits=active_limits,
        )
        if candidate is None:
            if feedback.value is not None and "no_package_change" in feedback.value:
                carried_feedback_omitted += 1
            continue
        if len(candidates) >= active_limits.max_candidates:
            candidate_limit_omitted += 1
            continue
        candidates.append(candidate)

    demo_omission: str | None = None
    demo_reports = _parsed_by_kind(artifacts, "demo_report")
    if demo_reports:
        demo_candidate, demo_omission = _demo_candidate(
            demo_report=demo_reports[0],
            run_context=run_context,
            limits=active_limits,
        )
        if demo_candidate is not None:
            if len(candidates) < active_limits.max_candidates:
                candidates.append(demo_candidate)
            else:
                candidate_limit_omitted += 1

    candidates.sort(key=lambda item: item["candidate_id"])
    for index, candidate in enumerate(candidates):
        proposed = candidate["proposed_record"]
        issues = validate_json_schema(
            proposed,
            experience_schema,
            instance_path=f"$bundle.candidates[{index}].proposed_record",
        )
        if issues:
            raise _format_issues("proposed Experience record", issues)
        if proposed.get("status") != "candidate":
            raise EvolutionError("proposed Experience records must retain status=candidate")
        candidate["candidate_payload_sha256"] = _candidate_payload_sha256(candidate)

    included_by_kind = {
        kind: sum(item.kind == kind for item in artifacts)
        for kind in discovered
    }
    omitted_by_limit = {
        kind: max(0, discovered[kind] - included_by_kind.get(kind, 0))
        for kind in discovered
    }
    bundle = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "run": run_context,
        "contracts": {
            "bundle_schema": {
                "path": "schemas/evolution_candidate_bundle.schema.json",
                "sha256": sha256_file(bundle_schema_path),
            },
            "experience_schema": {
                "path": "schemas/experience.schema.json",
                "sha256": sha256_file(experience_schema_path),
            },
            "sealed_report_schema": {
                "path": "schemas/sealed_run_report.schema.json",
                "sha256": sha256_file(schemas / "sealed_run_report.schema.json"),
            },
            "terminal_report_schema": {
                "path": "schemas/terminal_run_report.schema.json",
                "sha256": sha256_file(schemas / "terminal_run_report.schema.json"),
            },
        },
        "limits": active_limits.to_dict(),
        "collection": {
            "artifacts": [item.index_record() for item in artifacts],
            "discovered_by_kind": discovered,
            "included_by_kind": included_by_kind,
            "omitted_by_limit": omitted_by_limit,
            "invalid_or_oversized": sum(
                item.status
                in {
                    "invalid_json",
                    "invalid_shape",
                    "invalid_schema",
                    "semantic_invalid",
                    "oversized",
                    "unsafe_or_missing",
                }
                for item in artifacts
            ),
            "unbound": sum(item.status == "unbound" for item in artifacts),
            "candidate_omissions": {
                "carried_no_change_feedback": carried_feedback_omitted,
                "candidate_limit": candidate_limit_omitted,
                "demo_reason": demo_omission,
            },
            "redaction": {
                "raw_failure_messages": "excluded",
                "tracebacks": "excluded",
                "observed_target_gap_execution": "excluded",
                "case_and_task_ids": "replaced_with_synthetic_refs",
                "absolute_paths": "excluded",
            },
        },
        "candidates": candidates,
        "promotion": {
            "automatic_library_writeback": False,
            "status": "awaiting_explicit_approval",
            "target_records_path": "libraries/experience/v1/records.jsonl",
            "required_gates": list(_PROMOTION_GATE_NAMES),
            "all_gates_satisfied": False,
        },
    }
    bundle_issues = validate_json_schema(bundle, bundle_schema, instance_path="$bundle")
    if bundle_issues:
        raise _format_issues("Evolution candidate bundle", bundle_issues)
    for index, candidate in enumerate(bundle["candidates"]):
        if candidate["candidate_payload_sha256"] != _candidate_payload_sha256(candidate):
            raise EvolutionError(
                f"candidate {index} payload hash changed during bundle construction"
            )
    return bundle


def write_candidate_bundle(
    run_root: str | Path,
    *,
    schema_root: str | Path | None = None,
    limits: EvolutionLimits | None = None,
) -> Path:
    """Write only ``<run>/evolution/candidate_bundle.json`` and return its path."""

    root = Path(run_root).resolve()
    bundle = build_candidate_bundle(root, schema_root=schema_root, limits=limits)
    output_dir = root / "evolution"
    if output_dir.exists() and output_dir.is_symlink():
        raise EvolutionError("run-local evolution directory must not be a symlink")
    output = output_dir / "candidate_bundle.json"
    if output.exists() and output.is_symlink():
        raise EvolutionError("candidate bundle target must not be a symlink")
    resolved_output = output.resolve(strict=False)
    if not _is_within(resolved_output, root):
        raise EvolutionError("candidate bundle target escapes the run root")
    if resolved_output == _LIBRARY_RECORDS.resolve():
        raise EvolutionError("Evolution must never write the Experience library")
    atomic_write_json(output, bundle)
    return output


EVOLUTION_PROJECTION_SCHEMA_VERSION = (
    "robot_capability.evolution_evidence_projection.v1"
)
EVOLUTION_ARCHITECTURE = "evidence_grounded_audit_synthesize_judge_publish"
_MAX_AUDIT_BYTES = 64 * 1024
_MAX_EXPERIENCE_BYTES = 32 * 1024
_MAX_GENERATION_VIEW_BYTES = 16 * 1024


def _bounded_accounting(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    usage = value.get("usage")
    safe_usage = {}
    if isinstance(usage, Mapping):
        for key in (
            "response_count",
            "input_tokens_total",
            "output_tokens_total",
            "cost_total",
            "remaining_budget_latest",
        ):
            item = usage.get(key)
            if item is None or (
                isinstance(item, (int, float)) and not isinstance(item, bool)
            ):
                safe_usage[key] = item
    result: dict[str, Any] = {"usage": safe_usage}
    for key in (
        "agent_turns",
        "provider_http_attempts",
        "provider_retries",
        "status",
        "round",
    ):
        item = value.get(key)
        if isinstance(item, (str, int)) and not isinstance(item, bool):
            result[key] = item
    budget = value.get("agent_turn_budget")
    if isinstance(budget, Mapping):
        result["agent_turn_budget"] = {
            key: budget.get(key) for key in ("limit", "used", "remaining")
        }
    return result


def _safe_direct_rounds(
    run_root: Path,
    bundle: Mapping[str, Any],
    limits: EvolutionLimits,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rounds: list[dict[str, Any]] = []
    evidence: dict[str, dict[str, Any]] = {}
    artifacts = bundle.get("collection", {}).get("artifacts", [])
    if not isinstance(artifacts, Sequence):
        return rounds, evidence
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or artifact.get("kind") != "direct_report":
            continue
        if artifact.get("status") != "parsed" or artifact.get("semantic_valid") is not True:
            continue
        relative = artifact.get("path")
        digest = artifact.get("sha256")
        if not isinstance(relative, str) or not isinstance(digest, str):
            continue
        path = run_root / relative
        loaded = _read_artifact(
            run_root,
            path,
            kind="direct_report",
            max_json_bytes=limits.max_json_bytes,
        )
        if loaded.status != "parsed" or loaded.sha256 != digest or loaded.value is None:
            continue
        match = re.search(r"_(\d{2})\.json$", relative)
        round_number = None if match is None else int(match.group(1))
        ref = f"validation:round:{round_number:02d}" if round_number is not None else (
            "validation:report:" + digest[:16]
        )
        summary = loaded.value.get("summary")
        safe_summary = {
            key: summary.get(key)
            for key in ("total", "passed", "failed")
            if isinstance(summary, Mapping)
            and isinstance(summary.get(key), int)
            and not isinstance(summary.get(key), bool)
        }
        capability_results: dict[str, bool] = {}
        cases = loaded.value.get("cases")
        if isinstance(cases, Sequence) and not isinstance(cases, (str, bytes, bytearray)):
            for case in cases:
                if not isinstance(case, Mapping):
                    continue
                capability_id = _safe_capability_id(case.get("capability_id"))
                passed = case.get("passed")
                if capability_id is not None and isinstance(passed, bool):
                    capability_results[capability_id] = (
                        capability_results.get(capability_id, True) and passed
                    )
        rounds.append(
            {
                "evidence_ref": ref,
                "round": round_number,
                "passed": bool(loaded.value.get("passed") is True),
                "summary": safe_summary,
                "capability_results": dict(sorted(capability_results.items())),
                "report_sha256": digest,
            }
        )
        evidence[ref] = {
            "kind": "direct_validation_summary",
            "causal_outcome": (
                "confirmed_success"
                if loaded.value.get("passed") is True
                else "confirmed_failure"
            ),
            "sha256": digest,
        }
    return rounds, evidence


def build_evolution_projection(
    run_root: str | Path,
    *,
    schema_root: str | Path | None = None,
    limits: EvolutionLimits | None = None,
) -> dict[str, Any]:
    """Return the only LLM-visible, privacy-safe projection of a source run."""

    root = Path(run_root).resolve()
    active_limits = limits or EvolutionLimits()
    bundle = build_candidate_bundle(
        root,
        schema_root=schema_root,
        limits=active_limits,
    )
    source = bundle["run"]
    if source.get("source_report_verified") is not True:
        raise EvolutionError("source terminal report is not independently verified")
    report_ref = source.get("source_report")
    if not isinstance(report_ref, Mapping):
        raise EvolutionError("verified source has no report reference")
    report_path = root / str(report_ref.get("path", ""))
    report_artifact = _read_artifact(
        root,
        report_path,
        kind="terminal_report",
        max_json_bytes=active_limits.max_json_bytes,
    )
    if (
        report_artifact.status != "parsed"
        or report_artifact.value is None
        or report_artifact.sha256 != report_ref.get("sha256")
    ):
        raise EvolutionError("verified terminal report changed during projection")
    terminal = report_artifact.value
    try:
        semantic_issues = terminal_report_semantic_errors(terminal, run_root=root)
    except Exception as exc:
        raise EvolutionError(
            "verified terminal report semantic audit could not complete"
        ) from exc
    if semantic_issues:
        raise EvolutionError(
            "verified terminal report failed deterministic semantic audit "
            f"({len(semantic_issues)} issue(s))"
        )
    raw_terminal_reason = terminal.get("terminal_reason")
    terminal_reason_code = (
        raw_terminal_reason
        if isinstance(raw_terminal_reason, str)
        and _SAFE_PUBLIC_CODE_RE.fullmatch(raw_terminal_reason)
        else "redacted_unstructured_reason"
    )
    raw_error_type = (
        terminal.get("error", {}).get("type")
        if isinstance(terminal.get("error"), Mapping)
        else None
    )
    error_type_code = (
        raw_error_type
        if isinstance(raw_error_type, str)
        and _SAFE_PUBLIC_CODE_RE.fullmatch(raw_error_type)
        else "redacted_unstructured_error_type"
    )
    evidence_index: dict[str, dict[str, Any]] = {
        "source:terminal_report": {
            "kind": "verified_terminal_report",
            "causal_outcome": "terminal_observation",
            "sha256": report_artifact.sha256,
        },
        "accounting:terminal_report": {
            "kind": "model_accounting",
            "causal_outcome": "terminal_observation",
            "sha256": report_artifact.sha256,
        },
        "videos:index": {
            "kind": "video_integrity_summary",
            "causal_outcome": "terminal_observation",
            "sha256": report_artifact.sha256,
        },
    }

    validation_rounds, validation_refs = _safe_direct_rounds(
        root, bundle, active_limits
    )
    evidence_index.update(validation_refs)

    repair_results: list[dict[str, Any]] = []
    repair = terminal.get("repair")
    raw_results = repair.get("results") if isinstance(repair, Mapping) else None
    if isinstance(raw_results, Sequence) and not isinstance(
        raw_results, (str, bytes, bytearray)
    ):
        for index, item in enumerate(raw_results[:10], start=1):
            if not isinstance(item, Mapping):
                continue
            round_number = item.get("round")
            if not isinstance(round_number, int) or isinstance(round_number, bool):
                round_number = index
            ref = f"repair:round:{round_number:02d}"
            repair_results.append({"evidence_ref": ref, **_bounded_accounting(item)})
            evidence_index[ref] = {
                "kind": "repair_accounting",
                "causal_outcome": "terminal_observation",
                "sha256": report_artifact.sha256,
            }

    candidates: list[dict[str, Any]] = []
    for candidate in bundle.get("candidates", []):
        if not isinstance(candidate, Mapping):
            continue
        candidate_id = candidate.get("candidate_id")
        summary = candidate.get("evidence_summary")
        if not isinstance(candidate_id, str) or not isinstance(summary, Mapping):
            continue
        ref = f"candidate:{candidate_id}"
        outcome = summary.get("causal_outcome")
        if outcome not in {"confirmed_success", "confirmed_failure", "unresolved"}:
            outcome = "unresolved"
        candidates.append(
            {
                "evidence_ref": ref,
                "candidate_id": candidate_id,
                "claim_kind": candidate.get("claim_kind"),
                "source_stage": candidate.get("source_stage"),
                "source_round": candidate.get("source_round"),
                "causal_outcome": outcome,
                "failure_count": summary.get("failure_count"),
                "failure_codes": summary.get("failure_codes", []),
                "package_changed": summary.get("package_changed"),
                "after_stage_passed": summary.get("after_stage_passed"),
                "package_identity_bound": summary.get("package_identity_bound"),
            }
        )
        evidence_index[ref] = {
            "kind": "compiler_candidate",
            "causal_outcome": outcome,
            "candidate_payload_sha256": candidate.get("candidate_payload_sha256"),
        }

    model_accounting = terminal.get("model_accounting")
    aggregate = (
        model_accounting.get("aggregate")
        if isinstance(model_accounting, Mapping)
        else {}
    )
    phases = (
        model_accounting.get("generation_phases")
        if isinstance(model_accounting, Mapping)
        else {}
    )
    safe_phases: dict[str, Any] = {}
    if isinstance(phases, Mapping):
        for name in ("stage1", "stage2_initial"):
            safe_phases[name] = _bounded_accounting(phases.get(name))

    video = terminal.get("video_recording")
    validation_videos = video.get("validation") if isinstance(video, Mapping) else []
    demo_videos = video.get("demo") if isinstance(video, Mapping) else []
    collection = bundle.get("collection", {})
    projection = {
        "schema_version": EVOLUTION_PROJECTION_SCHEMA_VERSION,
        "source": {
            "evidence_ref": "source:terminal_report",
            "run_id": source.get("run_id"),
            "mode": source.get("mode"),
            "terminal_state": source.get("terminal_state"),
            "terminal_reason": terminal_reason_code,
            "error_type": error_type_code,
            "ended_at": terminal.get("ended_at"),
            "report_bytes": report_artifact.byte_count,
            "report_sha256": report_artifact.sha256,
            "source_report_verified": True,
            "source_tree_sha256": (
                terminal.get("input_hashes", {}).get("source_tree")
                if isinstance(terminal.get("input_hashes"), Mapping)
                else None
            ),
        },
        "collection": {
            "invalid_or_oversized": collection.get("invalid_or_oversized"),
            "unbound": collection.get("unbound"),
            "discovered_by_kind": collection.get("discovered_by_kind", {}),
            "candidate_omissions": collection.get("candidate_omissions", {}),
        },
        "generation": {
            "evidence_ref": "accounting:terminal_report",
            "phases": safe_phases,
        },
        "accounting": {
            "evidence_ref": "accounting:terminal_report",
            "aggregate": _bounded_accounting(aggregate),
        },
        "validation": {"rounds": validation_rounds},
        "repair": {
            "round_limit": repair.get("round_limit") if isinstance(repair, Mapping) else None,
            "rounds_used": repair.get("rounds_used") if isinstance(repair, Mapping) else None,
            "rounds": repair_results,
        },
        "demo": {
            "terminal_reached": source.get("terminal_state") in {"DEMO_PASSED", "DEMO_FAILED"},
            "compiler_demo_candidate_count": sum(
                item.get("source_stage") == "demo" for item in candidates
            ),
        },
        "video": {
            "evidence_ref": "videos:index",
            "required": video.get("required") if isinstance(video, Mapping) else None,
            "status": video.get("status") if isinstance(video, Mapping) else "unknown",
            "renderer_kind": video.get("renderer_kind") if isinstance(video, Mapping) else None,
            "validation_video_count": len(validation_videos) if isinstance(validation_videos, list) else 0,
            "demo_video_count": len(demo_videos) if isinstance(demo_videos, list) else 0,
            "content_interpretation": "integrity_and_stage_metadata_only",
        },
        "compiler_candidates": candidates,
        "evidence_index": dict(sorted(evidence_index.items())),
        "research_boundaries": {
            "agent_is_read_only": True,
            "one_experience_claim_only": True,
            "recommendation_is_not_applied": True,
            "trusted_evaluator_is_deterministic": True,
            "capability_improvement_requires_new_run": True,
        },
        "submission_contract": {
            "submit_by_agent_turn": 20,
            "reserve_turns_for_schema_correction": 10,
            "ranks_must_be_contiguous": True,
            "claim_refs_must_be_subset_of_selected_finding_refs": True,
            "positive_requires": "a cited candidate:* ref with confirmed_success",
            "negative_requires": "a cited candidate:* ref with confirmed_failure",
            "terminal_observation_only_requires": "conclusion_kind=unresolved",
            "required_runtime_id": "lerobot_soarm101_0_6_0",
            "recommendation_is_future_advice_not_an_applied_patch": True,
        },
        "privacy": {
            "raw_private_tasks_included": False,
            "oracle_thresholds_included": False,
            "api_keys_included": False,
            "exact_hidden_measurements_included": False,
            "raw_model_messages_included": False,
            "absolute_paths_included": False,
        },
    }
    return projection


def _privacy_issue_codes(value: Any) -> list[str]:
    return experience_public_privacy_issue_codes(value)


def build_trusted_experience_record(
    *,
    audit: Mapping[str, Any],
    projection: Mapping[str, Any],
    evolution_run_id: str,
    record_version: str = "1.0.0",
) -> dict[str, Any]:
    """Compile an accepted claim into a conservative raw Experience record.

    The Agent chooses the finding and supplies a future hypothesis, but direct
    execution evidence cannot by itself prove a mechanism.  The Generation-
    visible wording therefore preserves the observed result while keeping the
    causal mechanism and proposed change explicitly unvalidated.
    """

    if _SEMVER_RE.fullmatch(record_version) is None:
        raise EvolutionError("Experience record_version must be semantic versioning")

    selected = audit["selected_finding_id"]
    findings = {
        item["finding_id"]: item
        for item in audit["ranked_findings"]
        if isinstance(item, Mapping) and isinstance(item.get("finding_id"), str)
    }
    finding = findings[selected]
    claim = audit["experience_claim"]
    identity_material = {
        "evolution_run_id": evolution_run_id,
        "source_report_sha256": projection["source"]["report_sha256"],
        "selected_finding_id": selected,
        "evidence_refs": claim["evidence_refs"],
    }
    experience_id = "evolution." + sha256_json(identity_material)[:24]
    conclusion = claim["conclusion_kind"]
    if conclusion == "positive":
        compiled_hypothesis = (
            "The cited execution supports a positive outcome for the selected finding, "
            "but transfer to a fresh run and the causal mechanism remain unproven."
        )
        compiled_guidance = (
            "Future experiment only: preserve the cited successful pattern and replay it "
            "under unchanged Validation before treating it as reusable improvement."
        )
        lesson = (
            "The cited candidate produced a confirmed positive execution outcome; this "
            "does not yet establish transfer or capability improvement."
        )
        recommended_pattern = (
            "Replay the observed pattern in a fresh frozen-input Generation/Validation run."
        )
        avoid_pattern = (
            "Do not generalize one confirmed execution into framework or robot-level improvement."
        )
    elif conclusion == "negative":
        compiled_hypothesis = (
            "The cited execution proves that the selected capability outcome remained "
            "unsuccessful; the exact transformation, timing, contact, calibration, or "
            "kinematics mechanism is not localized by this evidence."
        )
        compiled_guidance = (
            "Future experiment only: diagnose one explicit mechanism at a time using "
            "evidence-safe phase diagnostics and replay it under the unchanged Oracle."
        )
        lesson = (
            f"Observed negative result: {claim['symptom']} The exact causal mechanism "
            "and any proposed remedy remain unproven."
        )
        recommended_pattern = (
            "Treat the repeated failure as unresolved at mechanism level; test one "
            "falsifiable cause in a fresh Generation/Validation run."
        )
        avoid_pattern = (
            "Do not infer a specific root cause from a failure code alone, and do not "
            "treat package_changed or an untested recommendation as improvement."
        )
    else:
        compiled_hypothesis = (
            "The cited evidence is insufficient to determine a capability mechanism or outcome."
        )
        compiled_guidance = (
            "Collect a fresh, package-bound Validation chain before selecting a causal remedy."
        )
        lesson = (
            "The selected finding remains unresolved and must not be presented as success or failure."
        )
        recommended_pattern = (
            "Acquire the missing execution evidence under the unchanged evaluation contract."
        )
        avoid_pattern = (
            "Do not infer capability behavior from incomplete or infrastructure-only evidence."
        )
    lesson = lesson[:800]
    return {
        "schema_version": "robot_capability.experience_record.v1",
        "experience_id": experience_id,
        "version": record_version,
        "status": "approved",
        "conclusion_kind": conclusion,
        "scope": claim["scope"],
        "origin": {
            "source_stage": "evolution",
            "run_id": projection["source"]["run_id"],
            "evolution_run_id": evolution_run_id,
            "case_or_task_ids": [f"evolution_finding:{selected}"],
            "observed_at": projection["source"].get("ended_at")
            or "1970-01-01T00:00:00Z",
        },
        "failure_summary": {
            "category": finding["category"],
            "symptom": claim["symptom"],
            "preconditions": [
                "The source terminal report was independently hash- and schema-verified.",
                "The Evolution recommendation was not automatically applied.",
            ],
        },
        "diagnosis": {
            "hypothesis": compiled_hypothesis,
            "confidence": claim["confidence"],
            "alternatives_considered": claim["alternatives_considered"],
        },
        "recommended_change": {
            "change_type": claim["recommended_change"]["change_type"],
            "guidance": compiled_guidance,
            "must_preserve": claim["recommended_change"]["must_preserve"],
        },
        "evidence": {
            "artifact_refs": list(claim["evidence_refs"]),
            "before_measurements": {
                "source_report_sha256": projection["source"]["report_sha256"],
                "terminal_state": projection["source"]["terminal_state"],
            },
            "after_measurements": {
                "trusted_experience_gate_passed": True,
                "framework_change_applied": False,
                "capability_improvement_proven": False,
                "mechanism_proven": False,
                "recommendation_validated": False,
                "future_test": claim["future_test"],
            },
        },
        "outcome": {
            "repair_attempted": False,
            "repair_succeeded": None,
            "regressions": (
                ["source_run_contains_confirmed_failure"]
                if conclusion == "negative"
                else []
            ),
            "reuse_risk": (
                "low" if conclusion == "positive" else "high" if conclusion == "negative" else "medium"
            ),
        },
        "scientific_claims": {
            "framework_change_applied": False,
            "capability_improvement_proven": False,
            "new_generation_validation_required": True,
        },
        "generation_summary": {
            "applicability": claim["generation_summary"]["applicability"],
            "lesson": lesson,
            "recommended_pattern": recommended_pattern,
            "avoid_pattern": avoid_pattern,
        },
    }


def evaluate_evolution_audit(
    *,
    run_root: str | Path,
    audit: Mapping[str, Any] | None,
    initial_projection_sha256: str,
    evolution_run_id: str,
    allowed_repository_refs: set[str] | None = None,
    framework_unchanged: bool = True,
    schema_root: str | Path | None = None,
    record_version: str = "1.0.0",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Independently judge one Agent claim; never trust the Agent's verdict."""

    gates: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    try:
        projection = build_evolution_projection(run_root, schema_root=schema_root)
    except EvolutionError:
        projection = None
        issues.append("source_reverification_failed")
    source_ok = (
        projection is not None
        and sha256_json(projection) == initial_projection_sha256
        and projection.get("source", {}).get("source_report_verified") is True
    )
    gates["source_integrity"] = {"passed": source_ok}
    if not source_ok:
        issues.append("source_projection_changed_or_unverified")
    gates["framework_snapshot"] = {"passed": bool(framework_unchanged)}
    if not framework_unchanged:
        issues.append("framework_changed_during_audit")
    if audit is None:
        gates.update(
            {
                "audit_schema": {"passed": False, "issue_count": 0},
                "audit_semantics": {"passed": False},
                "evidence_binding": {"passed": False},
                "claim_strength": {"passed": False},
                "privacy": {"passed": False, "reason_codes": ["agent_audit_missing"]},
                "experience_schema": {"passed": False},
            }
        )
        evaluation = {
            "schema_version": "robot_capability.evolution_evaluation.v1",
            "verdict": "inconclusive",
            "publishable": False,
            "approved_record_sha256": None,
            "reason_codes": sorted(set(issues + ["agent_audit_missing"])),
            "gates": gates,
            "claims": {
                "evolution_process_completed": False,
                "experience_claim_accepted": False,
                "framework_change_applied": False,
                "capability_improvement_proven": False,
                "new_generation_validation_required": True,
            },
        }
        return evaluation, None

    schema_dir = Path(schema_root).resolve() if schema_root else _DEFAULT_SCHEMA_ROOT
    audit_schema = _load_schema(schema_dir / "evolution_audit.schema.json")
    schema_issues = validate_json_schema(audit, audit_schema, instance_path="$audit")
    audit_bytes = len(canonical_json(audit).encode("utf-8"))
    audit_size_ok = audit_bytes <= _MAX_AUDIT_BYTES
    schema_ok = not schema_issues and audit_size_ok
    gates["audit_schema"] = {
        "passed": schema_ok,
        "issue_count": len(schema_issues),
        "bytes": audit_bytes,
        "max_bytes": _MAX_AUDIT_BYTES,
    }
    if schema_issues:
        issues.append("audit_schema_invalid")
    if not audit_size_ok:
        issues.append("audit_too_large")

    semantic_ok = schema_ok
    evidence_ok = schema_ok and projection is not None
    claim_strength_ok = schema_ok and projection is not None
    privacy_codes = _privacy_issue_codes(audit)
    record: dict[str, Any] | None = None
    if schema_ok and projection is not None:
        findings = audit["ranked_findings"]
        ranks = [item["rank"] for item in findings]
        finding_ids = [item["finding_id"] for item in findings]
        semantic_ok = (
            ranks == list(range(1, len(ranks) + 1))
            and len(finding_ids) == len(set(finding_ids))
            and audit["selected_finding_id"] in finding_ids
            and audit["source_run_id"] == projection["source"]["run_id"]
            and audit["experience_claim"]["scope"]["robot_ids"] == ["soarm101"]
            and audit["experience_claim"]["scope"]["runtime_ids"]
            == ["lerobot_soarm101_0_6_0"]
        )
        if not semantic_ok:
            issues.append("audit_semantics_invalid")
        selected_finding = next(
            (
                item
                for item in findings
                if item["finding_id"] == audit["selected_finding_id"]
            ),
            None,
        )
        claim = audit["experience_claim"]
        projection_refs = set(projection["evidence_index"])
        allowed_refs = projection_refs | set(allowed_repository_refs or set())
        all_refs = {
            ref
            for finding in findings
            for ref in finding.get("evidence_refs", [])
        } | set(claim["evidence_refs"])
        evidence_ok = all_refs <= allowed_refs
        if isinstance(selected_finding, Mapping):
            evidence_ok = evidence_ok and set(claim["evidence_refs"]) <= set(
                selected_finding["evidence_refs"]
            )
        evidence_ok = evidence_ok and bool(
            set(claim["evidence_refs"]) & projection_refs
        )
        if not evidence_ok:
            issues.append("unknown_or_unselected_evidence_ref")
        candidate_outcomes = {
            projection["evidence_index"][ref].get("causal_outcome")
            for ref in claim["evidence_refs"]
            if ref.startswith("candidate:") and ref in projection["evidence_index"]
        }
        conclusion = claim["conclusion_kind"]
        claim_strength_ok = (
            conclusion == "unresolved"
            or (
                conclusion == "negative"
                and "confirmed_failure" in candidate_outcomes
            )
            or (
                conclusion == "positive"
                and "confirmed_success" in candidate_outcomes
            )
        )
        if not claim_strength_ok:
            issues.append("claim_strength_exceeds_evidence")
        if semantic_ok and evidence_ok and claim_strength_ok and not privacy_codes:
            try:
                record = build_trusted_experience_record(
                    audit=audit,
                    projection=projection,
                    evolution_run_id=evolution_run_id,
                    record_version=record_version,
                )
                record_privacy = _privacy_issue_codes(record)
                privacy_codes.extend(record_privacy)
                experience_schema = _load_schema(schema_dir / "experience.schema.json")
                generation_view = experience_generation_view(
                    record,
                    schema=experience_schema,
                )
                privacy_codes.extend(_privacy_issue_codes(generation_view))
                if len(canonical_json(record).encode("utf-8")) > _MAX_EXPERIENCE_BYTES:
                    issues.append("experience_record_too_large")
                if len(canonical_json(generation_view).encode("utf-8")) > _MAX_GENERATION_VIEW_BYTES:
                    issues.append("generation_view_too_large")
                experience_issues = validate_json_schema(
                    record, experience_schema, instance_path="$experience"
                )
                if experience_issues:
                    issues.append("experience_schema_invalid")
            except (EvolutionError, LibraryError, KeyError, TypeError, ValueError):
                record = None
                issues.append("experience_record_construction_failed")
    gates["audit_semantics"] = {"passed": semantic_ok}
    gates["evidence_binding"] = {"passed": evidence_ok}
    gates["claim_strength"] = {"passed": claim_strength_ok}
    privacy_ok = not privacy_codes
    gates["privacy"] = {
        "passed": privacy_ok,
        "reason_codes": sorted(set(privacy_codes)),
    }
    if not privacy_ok:
        issues.append("privacy_gate_failed")
    gates["experience_schema"] = {
        "passed": "experience_schema_invalid" not in issues
        and "experience_record_too_large" not in issues
        and "generation_view_too_large" not in issues
        and "generation_projection_failed" not in issues
        and "experience_record_construction_failed" not in issues
        and record is not None
    }
    accepted = all(gate.get("passed") is True for gate in gates.values())
    verdict = "accepted" if accepted else "rejected"
    if not source_ok or not framework_unchanged:
        verdict = "inconclusive"
    evaluation = {
        "schema_version": "robot_capability.evolution_evaluation.v1",
        "verdict": verdict,
        "publishable": verdict == "accepted",
        "approved_record_sha256": (
            sha256_json(record) if verdict == "accepted" and record is not None else None
        ),
        "reason_codes": sorted(set(issues)),
        "gates": gates,
        "claims": {
            "evolution_process_completed": True,
            "experience_claim_accepted": verdict == "accepted",
            "framework_change_applied": False,
            "capability_improvement_proven": False,
            "new_generation_validation_required": True,
        },
    }
    return evaluation, record if verdict == "accepted" else None


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _safe_experience_authority_root(demo_root: str | Path) -> tuple[Path, Path]:
    root_input = Path(demo_root)
    if root_input.is_symlink():
        raise EvolutionError("Demo root must not be a symlink during Experience publication")
    root = root_input.resolve()
    library_root = root / "libraries/experience/v1"
    protected = (
        root / "libraries",
        root / "libraries/experience",
        library_root,
        library_root / "records.jsonl",
        library_root / "manifest.yaml",
        library_root / "sources.yaml",
        library_root / "experience.schema.json",
        root / "schemas",
        root / "schemas/experience.schema.json",
    )
    for path in protected:
        if path.is_symlink():
            raise EvolutionError("Experience authority path chain must not contain symlinks")
        resolved = path.resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise EvolutionError("Experience authority path escaped the Demo root")
    if not library_root.is_dir():
        raise EvolutionError("Experience authority directory is missing")
    return root, library_root


def _publish_evaluated_experience_record(
    *,
    demo_root: str | Path,
    record: Mapping[str, Any],
    evaluation: Mapping[str, Any],
) -> dict[str, Any]:
    """Append only a record hash-bound to an accepted trusted evaluation."""

    root, library_root = _safe_experience_authority_root(demo_root)
    records_path = library_root / "records.jsonl"
    manifest_path = library_root / "manifest.yaml"
    record_sha256 = sha256_json(record)
    if (
        evaluation.get("schema_version")
        != "robot_capability.evolution_evaluation.v1"
        or evaluation.get("verdict") != "accepted"
        or evaluation.get("publishable") is not True
        or evaluation.get("approved_record_sha256") != record_sha256
    ):
        raise EvolutionError("Experience record is not bound to an accepted evaluation")
    if record.get("status") != "approved":
        raise EvolutionError("only trusted approved records may enter Experience")
    if record.get("scientific_claims") != {
        "framework_change_applied": False,
        "capability_improvement_proven": False,
        "new_generation_validation_required": True,
    }:
        raise EvolutionError("Experience record overstates its scientific status")
    schema = _load_schema(root / "schemas/experience.schema.json")
    schema_issues = validate_json_schema(record, schema, instance_path="$experience")
    if schema_issues:
        raise _format_issues("Experience publication", schema_issues)
    if _privacy_issue_codes(record):
        raise EvolutionError("Experience publication failed privacy gate")
    view = experience_generation_view(dict(record), schema=schema)
    if _privacy_issue_codes(view):
        raise EvolutionError("Generation Experience view failed privacy gate")
    existing_entry = LibraryEntry.open(library_root)
    expected_payload_hashes = {
        "sources.yaml",
        "experience.schema.json",
        "records.jsonl",
    }
    existing_hashes = existing_entry.manifest.get("content_hashes")
    if (
        not isinstance(existing_hashes, Mapping)
        or existing_hashes.get("algorithm") != "sha256_raw_bytes"
        or not expected_payload_hashes <= set(existing_hashes)
        or any(not _is_sha256(existing_hashes.get(name)) for name in expected_payload_hashes)
    ):
        raise EvolutionError("existing Experience authority lacks complete hash binding")
    existing_verification = existing_entry.verify()
    existing_schema_verification = existing_entry.validate_runtime_schemas(
        root / "schemas"
    )
    if not existing_verification["ok"] or not existing_schema_verification["ok"]:
        raise EvolutionError(
            "existing Experience authority failed hash/schema/count verification"
        )
    old_records = records_path.read_bytes() if records_path.exists() else b""
    if old_records and not old_records.endswith(b"\n"):
        raise EvolutionError("existing Experience ledger is not newline-terminated")
    try:
        old_lines = old_records.splitlines()
        parsed_old_records = [json.loads(line.decode("utf-8")) for line in old_lines]
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EvolutionError("existing Experience ledger is not canonical JSONL") from exc
    canonical_old_records = b"".join(
        canonical_json(item).encode("utf-8") + b"\n" for item in parsed_old_records
    )
    if canonical_old_records != old_records:
        raise EvolutionError("existing Experience ledger is not canonical JSONL")
    old_manifest = manifest_path.read_bytes()
    ledger = ExperienceLibrary(records_path).list()
    identity = (str(record["experience_id"]), str(record["version"]))
    for existing in ledger:
        existing_identity = (
            str(existing.get("experience_id")),
            str(existing.get("version")),
        )
        if existing_identity != identity:
            continue
        if sha256_json(existing) == sha256_json(record):
            manifest_count = existing_entry.manifest["compatibility"]["record_count"]
            return {
                "published": True,
                "idempotent": True,
                "experience_id": identity[0],
                "version": identity[1],
                "record_sha256": record_sha256,
                "records_sha256": sha256_file(records_path),
                "record_count": manifest_count,
                "generation_view_sha256": sha256_json(view),
                "manifest_sha256": sha256_file(manifest_path),
            }
        raise EvolutionError("Experience identity conflicts with different content")
    canonical_line = canonical_json(record).encode("utf-8") + b"\n"
    prefix = old_records
    if prefix and not prefix.endswith(b"\n"):
        prefix += b"\n"
    new_records = prefix + canonical_line
    try:
        import yaml

        manifest = yaml.safe_load(old_manifest.decode("utf-8"))
        if not isinstance(manifest, dict):
            raise EvolutionError("Experience manifest must be an object")
        manifest["generation_visibility"] = "mixed"
        compatibility = manifest.setdefault("compatibility", {})
        compatibility["record_count"] = len(ledger) + 1
        content_hashes = manifest.setdefault("content_hashes", {})
        content_hashes["records.jsonl"] = sha256_bytes(new_records)
        for name in ("sources.yaml", "experience.schema.json"):
            content_hashes[name] = sha256_file(library_root / name)
        new_manifest = yaml.safe_dump(
            manifest,
            sort_keys=False,
            allow_unicode=True,
        ).encode("utf-8")
        _atomic_write_bytes(records_path, new_records)
        _atomic_write_bytes(manifest_path, new_manifest)
        verification = LibraryEntry.open(library_root).verify()
        schema_verification = LibraryEntry.open(library_root).validate_runtime_schemas(
            root / "schemas"
        )
        if not verification["ok"] or not schema_verification["ok"]:
            raise EvolutionError("published Experience library failed deterministic verification")
    except Exception:
        _atomic_write_bytes(records_path, old_records)
        _atomic_write_bytes(manifest_path, old_manifest)
        raise
    return {
        "published": True,
        "idempotent": False,
        "experience_id": identity[0],
        "version": identity[1],
        "record_sha256": record_sha256,
        "records_sha256": sha256_file(records_path),
        "record_count": len(ledger) + 1,
        "generation_view_sha256": sha256_json(view),
        "manifest_sha256": sha256_file(manifest_path),
    }


def evaluate_and_publish_evolution_audit(
    *,
    demo_root: str | Path,
    run_root: str | Path,
    audit: Mapping[str, Any] | None,
    initial_projection_sha256: str,
    evolution_run_id: str,
    allowed_repository_refs: set[str] | None = None,
    framework_unchanged: bool = True,
    schema_root: str | Path | None = None,
    record_version: str = "1.0.0",
    before_publish: Callable[[Mapping[str, Any], Mapping[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    """Use the one trusted evaluate→publish API; publication cannot bypass judgment."""

    evaluation, record = evaluate_evolution_audit(
        run_root=run_root,
        audit=audit,
        initial_projection_sha256=initial_projection_sha256,
        evolution_run_id=evolution_run_id,
        allowed_repository_refs=allowed_repository_refs,
        framework_unchanged=framework_unchanged,
        schema_root=schema_root,
        record_version=record_version,
    )
    publication: dict[str, Any] = {
        "published": False,
        "reason": "trusted_evaluator_did_not_accept_claim",
    }
    if record is None or evaluation.get("publishable") is not True:
        return evaluation, record, publication
    if before_publish is not None:
        before_publish(evaluation, record)
    try:
        publication = _publish_evaluated_experience_record(
            demo_root=demo_root,
            record=record,
            evaluation=evaluation,
        )
    except Exception:
        publication = {
            "published": False,
            "reason": "publication_gate_or_storage_failed",
        }
    return evaluation, record, publication


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "EVOLUTION_ARCHITECTURE",
    "EVOLUTION_PROJECTION_SCHEMA_VERSION",
    "EvolutionError",
    "EvolutionLimits",
    "build_candidate_bundle",
    "build_evolution_projection",
    "build_trusted_experience_record",
    "evaluate_and_publish_evolution_audit",
    "evaluate_evolution_audit",
    "write_candidate_bundle",
]
