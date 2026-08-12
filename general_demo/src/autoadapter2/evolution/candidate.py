"""Small experimental post-closure Evolution proposal compiler.

This module deliberately stops at a human-review-required candidate.  It first
verifies the complete first-G2 closure, then gives an injected EvolutionAgent a
small deterministic digest rather than any raw run artifact.  The returned
semantic fields are declassified and written as immutable canonical JSON; no
Experience Library or current run is changed.
"""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash, is_content_hash
from ..foundation.seals import create_seal
from ..integration.artifacts import load_json_artifact, verify_file_reference
from ..orchestration.first_g2_demo import verify_first_g2_run_closure

CANDIDATE_ARTIFACT_TYPE = "experience_candidate"
DECLASSIFICATION_REPORT_ARTIFACT_TYPE = "experience_declassification_report"
CANDIDATE_FORMAT_VERSION = "experimental-1"
CANDIDATE_STATUS = "PROPOSED_REVIEW_REQUIRED"

# This is intentionally a fixed, small list.  Public diagnostic/error codes
# may appear in the digest, while their raw payload and all categories below
# remain excluded from the proposed record.
EXCLUDED_EVIDENCE_CATEGORIES = (
    "private_validation_criteria",
    "private_cases",
    "thresholds",
    "seeds",
    "mujoco_truth",
    "translation_details",
    "candidate_source",
    "consumer_trace",
    "raw_video",
    "video_frames",
    "video_manifests",
    "model_prompts",
    "credentials",
)

_SEMANTIC_FIELDS = frozenset({
    "recipient_class",
    "lesson",
    "applicability",
    "limitations",
    "invalidation_conditions",
})
_APPLICABILITY_FIELDS = frozenset({
    "robot_model_id",
    "robot_configuration_id",
    "sdk_entry_id",
    "granularity_condition",
    "capability_effect_scope",
    "observation_condition",
})
_CANDIDATE_FIELDS = frozenset({
    "artifact_type",
    "format_version",
    "status",
    "candidate_id",
    "recipient_class",
    "lesson",
    "applicability",
    "provenance",
    "limitations",
    "invalidation_conditions",
    "declassification_report_hash",
})
_PROVENANCE_FIELDS = frozenset({
    "closure_hash",
    "summary_ref",
    "stage_artifacts_ref",
    "evidence_digest_hash",
})
_REPORT_FIELDS = frozenset({
    "artifact_type",
    "format_version",
    "candidate_id",
    "status",
    "evidence_digest_hash",
    "excluded_categories",
    "checks",
})
_CHECK_FIELDS = (
    "closure_verified_before_evidence_read",
    "sanitized_digest_only",
    "candidate_fields_declassified",
    "current_run_unchanged",
    "experience_library_unchanged",
)
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SDK_ENTRY_REFERENCE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}@[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_PUBLIC_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_CODE_LIKE = (
    re.compile(r"```"),
    re.compile(r"(?m)^\s*(?:def|class|import|from|return|async|await)\b"),
    re.compile(r"\b(?:def|class|import|from)\s+[A-Za-z_]"),
    re.compile(r"\b[A-Za-z_]\w*\s*\([^\n)]*\)\s*[:{]"),
    re.compile(r"\bself\.[A-Za-z_]"),
    re.compile(r"\b[A-Za-z_]\w*\s*=\s*"),
    re.compile(r"(?:=>|->)|\.py\b|[{};]"),
)
_BLOCKED_TOKENS = frozenset({
    "threshold",
    "thresholds",
    "criterion",
    "criteria",
    "case",
    "cases",
    "seed",
    "seeds",
    "video",
    "videos",
    "frame",
    "frames",
    "camera",
    "manifest",
    "trace",
    "traces",
    "diagnostic",
    "diagnostics",
    "source",
    "code",
    "mujoco",
    "mjdata",
    "qpos",
    "qvel",
    "truth",
    "privileged",
    "translation",
    "transport",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
    "prompt",
    "prompts",
    "llm",
    "oracle",
    "private",
    "declassified",
    "inference",
    "raw",
    "winning",
    "winner",
    "expected",
})
_BLOCKED_PHRASES = (
    "api key",
    "api token",
    "model output",
    "consumer output",
    "full implementation",
    "full code",
    "winning implementation",
    "expected solution",
    "raw truth",
    "raw state",
)
_COUNT_KEYS = (
    "llm_calls",
    "sandbox_calls",
    "repair_invocations_used",
    "candidate_revisions_created",
    "repairs_consumed",
    "repair_llm_calls",
    "task_count",
    "repetitions",
    "trial_count",
)
_TERMINAL_FAILURE_STATUSES = frozenset({
    "FAILED",
    "STAGE1_FAILED",
    "DESIGN_GAP",
    "BLUE_LINE_NEEDS_REVIEW",
    "STAGE2_FAILED",
    "VALIDATION_FAILED",
    "DEMO_FAILED",
    "DEMO_INFRASTRUCTURE_ERROR",
})


class EvolutionAgent(Protocol):
    """Injected deterministic/model-backed compiler callback boundary."""

    def __call__(self, evidence_digest: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return only the closed semantic candidate field set."""


@dataclass(frozen=True)
class ExperienceCandidateResult:
    """Paths and external hashes for one immutable proposal."""

    candidate_path: Path
    declassification_report_path: Path
    candidate_seal_path: Path
    declassification_report_seal_path: Path
    candidate_hash: str
    declassification_report_hash: str
    evidence_digest_hash: str
    closure_hash: str


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", value.casefold()))


def _contains_private_term(value: str) -> bool:
    folded = value.casefold()
    if any(phrase in folded for phrase in _BLOCKED_PHRASES):
        return True
    return any(token in _BLOCKED_TOKENS for token in _tokens(value))


def _public_text(value: Any, label: str, *, maximum: int = 1600) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty JSON string")
    result = value.strip()
    if len(result) > maximum or "\x00" in result:
        raise ContractError(f"{label} is empty, oversized, or contains invalid text")
    if _contains_private_term(result):
        raise ContractError(f"{label} contains private or reconstruction material")
    if any(pattern.search(result) for pattern in _CODE_LIKE):
        raise ContractError(f"{label} looks like source code")
    return result


def _public_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ContractError(f"{label} must be a bounded JSON identifier")
    if _contains_private_term(value):
        raise ContractError(f"{label} contains private or reconstruction material")
    return value


def _public_token(value: Any, label: str) -> str | None:
    if not isinstance(value, str):
        return None
    result = value.strip()
    if (
        not result
        or _PUBLIC_TOKEN.fullmatch(result) is None
        or _contains_private_term(result)
    ):
        return None
    return result


def _string_array(value: Any, label: str, *, nonempty: bool) -> list[str]:
    if not isinstance(value, list):
        raise ContractError(f"{label} must be a JSON string array")
    if len(value) > 16 or (nonempty and not value):
        raise ContractError(f"{label} is empty or oversized")
    return [
        _public_text(item, f"{label}[{index}]", maximum=500)
        for index, item in enumerate(value)
    ]


def _assert_json_value(value: Any, label: str = "value") -> None:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ContractError(f"{label} contains a non-string JSON key")
        for key, item in value.items():
            _assert_json_value(item, f"{label}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_json_value(item, f"{label}[{index}]")
        return
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    raise ContractError(f"{label} is not JSON data")


def _validate_semantic_fields(
    value: Mapping[str, Any],
    *,
    expected_robot: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError("EvolutionAgent must return a JSON object")
    if set(value) != _SEMANTIC_FIELDS:
        raise ContractError(
            "EvolutionAgent semantic fields must be exactly "
            "recipient_class, lesson, applicability, limitations, invalidation_conditions"
        )
    recipient_class = value["recipient_class"]
    if recipient_class not in {"design", "implementation"}:
        raise ContractError("recipient_class must be exactly design or implementation")
    lesson = _public_text(value["lesson"], "lesson", maximum=2000)

    applicability = value["applicability"]
    if not isinstance(applicability, Mapping) or set(applicability) != _APPLICABILITY_FIELDS:
        raise ContractError("applicability fields are not closed")
    normalized_applicability = {
        "robot_model_id": _public_identifier(
            applicability["robot_model_id"], "applicability.robot_model_id"
        ),
        "robot_configuration_id": _public_identifier(
            applicability["robot_configuration_id"],
            "applicability.robot_configuration_id",
        ),
        "sdk_entry_id": None,
        "granularity_condition": _public_text(
            applicability["granularity_condition"],
            "applicability.granularity_condition",
            maximum=300,
        ),
        "capability_effect_scope": _string_array(
            applicability["capability_effect_scope"],
            "applicability.capability_effect_scope",
            nonempty=True,
        ),
        "observation_condition": _public_text(
            applicability["observation_condition"],
            "applicability.observation_condition",
            maximum=500,
        ),
    }
    sdk_entry_id = applicability["sdk_entry_id"]
    if sdk_entry_id is not None:
        if (
            not isinstance(sdk_entry_id, str)
            or _SDK_ENTRY_REFERENCE.fullmatch(sdk_entry_id) is None
            or _contains_private_term(sdk_entry_id)
        ):
            raise ContractError("applicability.sdk_entry_id is not a bounded SDK Entry reference")
        normalized_applicability["sdk_entry_id"] = sdk_entry_id
    expected_sdk_entry_id = expected_robot.get("sdk_entry_id") if expected_robot else None
    if recipient_class == "implementation":
        if not isinstance(expected_sdk_entry_id, str):
            raise ContractError("implementation Experience has no verified SDK Entry in the run")
        if normalized_applicability["sdk_entry_id"] != expected_sdk_entry_id:
            raise ContractError("implementation Experience SDK Entry does not match the run")
    if recipient_class == "design" and normalized_applicability["sdk_entry_id"] is not None:
        raise ContractError("design Experience must not bind an SDK Entry")

    if expected_robot is not None:
        if (
            normalized_applicability["robot_model_id"] != expected_robot["robot_model_id"]
            or normalized_applicability["robot_configuration_id"]
            != expected_robot["robot_configuration_id"]
        ):
            raise ContractError("candidate applicability does not match the closed run robot")
        if (
            normalized_applicability["granularity_condition"]
            != expected_robot["granularity_condition"]
        ):
            raise ContractError(
                "candidate applicability granularity does not match the closed run"
            )

    normalized = {
        "recipient_class": recipient_class,
        "lesson": lesson,
        "applicability": normalized_applicability,
        "limitations": _string_array(value["limitations"], "limitations", nonempty=False),
        "invalidation_conditions": _string_array(
            value["invalidation_conditions"],
            "invalidation_conditions",
            nonempty=True,
        ),
    }
    _assert_json_value(normalized, "candidate semantic fields")
    return normalized


def validate_declassification_report(
    value: Mapping[str, Any],
    *,
    candidate_id: str | None = None,
    evidence_digest_hash: str | None = None,
) -> dict[str, Any]:
    """Validate the fixed experimental declassification report contract."""

    if not isinstance(value, Mapping) or set(value) != _REPORT_FIELDS:
        raise ContractError("declassification report fields are not closed")
    if value["artifact_type"] != DECLASSIFICATION_REPORT_ARTIFACT_TYPE:
        raise ContractError("declassification report artifact_type is invalid")
    if value["format_version"] != CANDIDATE_FORMAT_VERSION:
        raise ContractError("declassification report format_version is invalid")
    if value["status"] != "PASS":
        raise ContractError("declassification report status must be PASS")
    report_candidate_id = value["candidate_id"]
    if not isinstance(report_candidate_id, str) or not report_candidate_id.strip():
        raise ContractError("declassification report candidate_id is invalid")
    if candidate_id is not None and report_candidate_id != candidate_id:
        raise ContractError("declassification report candidate_id does not match")
    report_digest_hash = value["evidence_digest_hash"]
    if not is_content_hash(report_digest_hash):
        raise ContractError("declassification report evidence_digest_hash is invalid")
    if evidence_digest_hash is not None and report_digest_hash != evidence_digest_hash:
        raise ContractError("declassification report evidence digest does not match")
    if value["excluded_categories"] != list(EXCLUDED_EVIDENCE_CATEGORIES):
        raise ContractError("declassification report excluded_categories are not exact")
    checks = value["checks"]
    if (
        not isinstance(checks, Mapping)
        or set(checks) != set(_CHECK_FIELDS)
        or any(type(item) is not bool or item is not True for item in checks.values())
    ):
        raise ContractError("declassification report checks must be closed and all true")
    return dict(value)


def _safe_status(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty status")
    return value.strip()


def _bounded_error(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 240:
        return None
    result = value.strip()
    if _contains_private_term(result) or any(pattern.search(result) for pattern in _CODE_LIKE):
        return None
    return result


def _collect_allowed_leaves(
    value: Any,
    *,
    codes: set[str],
    candidate_errors: set[str],
    infrastructure_errors: set[str],
    counts: dict[str, int],
) -> None:
    """Walk containers while copying only explicitly allowed leaf values."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "code" and not isinstance(item, (Mapping, list)):
                token = _public_token(item, "diagnostic code")
                if token is not None:
                    codes.add(token)
            elif key == "candidate_error" and not isinstance(item, (Mapping, list)):
                error = _bounded_error(item)
                if error is not None:
                    candidate_errors.add(error)
            elif key == "infrastructure_error" and not isinstance(item, (Mapping, list)):
                error = _bounded_error(item)
                if error is not None:
                    infrastructure_errors.add(error)
            elif (
                key in _COUNT_KEYS
                and isinstance(item, int)
                and not isinstance(item, bool)
                and item >= 0
            ):
                counts[key] = counts.get(key, 0) + item
            if isinstance(item, (Mapping, list)):
                _collect_allowed_leaves(
                    item,
                    codes=codes,
                    candidate_errors=candidate_errors,
                    infrastructure_errors=infrastructure_errors,
                    counts=counts,
                )
    elif isinstance(value, list):
        for item in value:
            _collect_allowed_leaves(
                item,
                codes=codes,
                candidate_errors=candidate_errors,
                infrastructure_errors=infrastructure_errors,
                counts=counts,
            )


def _stage_record(
    name: str,
    summary_value: Mapping[str, Any] | None,
    artifact_value: Mapping[str, Any] | None,
    counts: dict[str, int],
) -> dict[str, Any] | None:
    public_name = _public_token(name, "stage name")
    if public_name is None:
        return None
    record: dict[str, Any] = {"stage": public_name}
    if summary_value is not None:
        status = _public_token(summary_value.get("status"), "summary stage status")
        if status is not None:
            record["summary_status"] = status
    if artifact_value is not None:
        status = _public_token(artifact_value.get("status"), "artifact stage status")
        if status is not None:
            record["artifact_status"] = status
    codes: set[str] = set()
    candidate_errors: set[str] = set()
    infrastructure_errors: set[str] = set()
    for item in (summary_value, artifact_value):
        _collect_allowed_leaves(
            item,
            codes=codes,
            candidate_errors=candidate_errors,
            infrastructure_errors=infrastructure_errors,
            counts=counts,
        )
    record["diagnostic_codes"] = sorted(codes)
    record["candidate_errors"] = sorted(candidate_errors)
    record["infrastructure_errors"] = sorted(infrastructure_errors)
    return record


def _count(value: Any) -> int:
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    return 0


def _stage1_sdk_entry_id(stage_artifacts: Mapping[str, Any]) -> str | None:
    stages = stage_artifacts.get("stages")
    stage1 = stages.get("stage1") if isinstance(stages, Mapping) else None
    capability_design = stage1.get("capability_design") if isinstance(stage1, Mapping) else None
    projection = (
        capability_design.get("robot_public_projection")
        if isinstance(capability_design, Mapping)
        else None
    )
    sdk_facts = projection.get("sdk_facts") if isinstance(projection, Mapping) else None
    if not isinstance(sdk_facts, Mapping):
        return None
    try:
        entry_id = _public_identifier(sdk_facts.get("entry_id"), "Stage 1 SDK Entry id")
        entry_version = _public_identifier(
            sdk_facts.get("entry_version"), "Stage 1 SDK Entry version"
        )
    except ContractError:
        return None
    reference = f"{entry_id}@{entry_version}"
    return reference if _SDK_ENTRY_REFERENCE.fullmatch(reference) else None


def build_sanitized_evidence_digest(
    closure: Mapping[str, Any],
    summary: Mapping[str, Any],
    stage_artifacts: Mapping[str, Any],
    *,
    closure_hash: str,
) -> dict[str, Any]:
    """Build the only run-derived value exposed to an EvolutionAgent."""

    if not is_content_hash(closure_hash):
        raise ContractError("closure_hash must be a content hash")
    robot = summary.get("robot")
    if not isinstance(robot, Mapping):
        raise ContractError("closed run summary robot identity is missing")
    robot_model_id = _public_identifier(robot.get("robot_model_id"), "summary robot model")
    robot_configuration_id = _public_identifier(
        robot.get("robot_configuration_id"), "summary robot configuration"
    )
    granularity_profile = summary.get("granularity_profile")
    if not isinstance(granularity_profile, Mapping):
        raise ContractError("closed run summary granularity profile is missing")
    granularity_condition = _public_identifier(
        granularity_profile.get("granularity"),
        "summary granularity",
    )
    summary_ref = closure.get("files", {}).get("summary")
    stage_ref = closure.get("files", {}).get("stage_artifacts")
    if not isinstance(summary_ref, Mapping) or not isinstance(stage_ref, Mapping):
        raise ContractError("closed run provenance references are missing")
    summary_sha256 = summary_ref.get("sha256")
    stage_sha256 = stage_ref.get("sha256")
    if (
        not isinstance(summary_sha256, str)
        or not isinstance(stage_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", summary_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", stage_sha256)
    ):
        raise ContractError("closed run provenance hashes are invalid")

    summary_stages = summary.get("stages")
    if not isinstance(summary_stages, list):
        raise ContractError("closed run summary stages are missing")
    artifact_stages = stage_artifacts.get("stages")
    if not isinstance(artifact_stages, Mapping):
        raise ContractError("closed run stage artifacts stages are missing")
    summary_by_name = {
        item.get("stage"): item
        for item in summary_stages
        if isinstance(item, Mapping) and isinstance(item.get("stage"), str)
    }
    names = set(summary_by_name) | {
        name for name in artifact_stages if isinstance(name, str)
    }
    nested_counts: dict[str, int] = {}
    stages = [
        record
        for name in sorted(names)
        for record in [
            _stage_record(
                name,
                summary_by_name.get(name),
                artifact_stages.get(name) if isinstance(artifact_stages.get(name), Mapping) else None,
                nested_counts,
            )
        ]
        if record is not None
    ]

    counts = {
        "summary_stages": len(summary_stages),
        "artifact_stages": len(artifact_stages),
        "validation_recording_count": _count(summary.get("validation_video_handles")),
        "demo_trials": _count(summary.get("demo_trials")),
        "promoted_capabilities": _count(summary.get("promoted_capability_ids")),
    }
    for key, value in nested_counts.items():
        counts[f"{key}_total"] = value

    digest = {
        "format_version": CANDIDATE_FORMAT_VERSION,
        "run_status": _safe_status(closure.get("status"), "closed run status"),
        "robot": {
            "robot_model_id": robot_model_id,
            "robot_configuration_id": robot_configuration_id,
            "granularity_condition": granularity_condition,
        },
        "stage_statuses": stages,
        "counts": counts,
        "provenance": {
            "closure_hash": closure_hash,
            "summary_hash": f"sha256:{summary_sha256}",
            "stage_artifacts_hash": f"sha256:{stage_sha256}",
        },
    }
    sdk_entry_id = _stage1_sdk_entry_id(stage_artifacts)
    if sdk_entry_id is not None:
        digest["robot"]["sdk_entry_id"] = sdk_entry_id
    _assert_json_value(digest, "sanitized evidence digest")
    return digest


def _resolve_case_directory(
    root: Path,
    evolution_cases_root: str | Path | None,
    case_id: str,
    run_directory: Path,
) -> Path:
    if not isinstance(case_id, str) or _CASE_ID.fullmatch(case_id) is None:
        raise ContractError("case_id must be one safe path component")
    base = (
        Path(evolution_cases_root)
        if evolution_cases_root is not None
        else root / "general_demo" / "evolution_cases"
    ).resolve()
    case_directory = (base / case_id).resolve()
    try:
        case_directory.relative_to(base)
    except ValueError as exc:
        raise ContractError("evolution case path escapes its selected root") from exc
    try:
        case_directory.relative_to(run_directory)
    except ValueError:
        pass
    else:
        raise ContractError("Evolution candidate must be outside the closed run")
    experience_library = (root / "general_demo" / "libraries" / "experience").resolve()
    try:
        case_directory.relative_to(experience_library)
    except ValueError:
        pass
    else:
        raise ContractError("Evolution proposal must not write to the Experience Library")
    if case_directory.exists() and case_directory.is_symlink():
        raise ContractError("evolution case directory cannot be a symlink")
    return case_directory


def _load_verified_inputs(
    root: Path,
    closure_path: str | Path,
    closure_seal_path: str | Path,
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any], Path]:
    # This must stay the first operation that consumes the closed-run evidence.
    closure_hash = verify_first_g2_run_closure(root, closure_path, closure_seal_path)
    closure_file = Path(closure_path)
    if not closure_file.is_absolute():
        closure_file = root / closure_file
    closure_file = closure_file.resolve()
    closure_artifact = load_json_artifact(closure_file)
    closure = closure_artifact.value
    status = closure.get("status")
    if status != "COMPLETE" and status not in _TERMINAL_FAILURE_STATUSES:
        raise ContractError("Evolution requires a complete or terminal failed run closure")
    files = closure.get("files")
    if not isinstance(files, Mapping):
        raise ContractError("closed run file references are missing")
    summary_file = verify_file_reference(root, files["summary"])
    stage_file = verify_file_reference(root, files["stage_artifacts"])
    summary = load_json_artifact(summary_file).value
    stage_artifacts = load_json_artifact(stage_file).value
    if summary.get("artifact_type") != "general_demo_run_summary":
        raise ContractError("summary is not a Framework-owned first-G2 summary")
    if stage_artifacts.get("artifact_type") not in {
        "first_g2_stage_artifacts",
        "general_demo_stage_artifacts",
    }:
        raise ContractError("stage artifacts are not Framework-owned first-G2 artifacts")
    if summary.get("status") != status or summary.get("run_id") != closure.get("run_id"):
        raise ContractError("closed run summary does not match the closure")
    run_directory = closure_file.parent
    return closure_hash, closure, summary, stage_artifacts, run_directory


def _write_immutable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ContractError(f"immutable Evolution artifact cannot be a symlink: {path}")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise ContractError(f"immutable Evolution artifact already exists: {path}") from exc


def _write_canonical_immutable(path: Path, value: Mapping[str, Any]) -> tuple[str, bytes]:
    payload = canonical_bytes(dict(value))
    _write_immutable(path, payload)
    return content_hash(payload), payload


def propose_experience_candidate(
    root: str | Path,
    closure_path: str | Path,
    closure_seal_path: str | Path,
    case_id: str,
    *,
    evolution_cases_root: str | Path | None = None,
    evolution_agent: EvolutionAgent | None = None,
    candidate_fields: Mapping[str, Any] | None = None,
) -> ExperienceCandidateResult:
    """Compile one experimental candidate after verifying a closed run.

    Exactly one of ``evolution_agent`` and ``candidate_fields`` is required.
    The callback receives only ``build_sanitized_evidence_digest`` output; it
    never receives raw summary/stage artifacts or any private evidence.
    """

    if (evolution_agent is None) == (candidate_fields is None):
        raise ContractError("supply exactly one of evolution_agent or candidate_fields")
    root_path = Path(root).resolve()
    (
        closure_hash,
        closure,
        summary,
        stage_artifacts,
        run_directory,
    ) = _load_verified_inputs(root_path, closure_path, closure_seal_path)
    case_directory = _resolve_case_directory(
        root_path, evolution_cases_root, case_id, run_directory
    )
    robot = summary["robot"]
    evidence_digest = build_sanitized_evidence_digest(
        closure,
        summary,
        stage_artifacts,
        closure_hash=closure_hash,
    )
    evidence_digest_hash = content_hash(canonical_bytes(evidence_digest))
    expected_robot = evidence_digest["robot"]

    if evolution_agent is not None:
        try:
            semantic_input = evolution_agent(copy.deepcopy(evidence_digest))
        except Exception as exc:
            raise ContractError(f"EvolutionAgent failed: {type(exc).__name__}: {exc}") from exc
    else:
        semantic_input = candidate_fields
    semantic = _validate_semantic_fields(semantic_input, expected_robot=expected_robot)

    files = closure["files"]
    summary_ref = files["summary"]
    stage_ref = files["stage_artifacts"]
    if (
        not isinstance(summary_ref, Mapping)
        or not isinstance(stage_ref, Mapping)
        or not isinstance(summary_ref.get("path"), str)
        or not isinstance(stage_ref.get("path"), str)
        or not isinstance(summary_ref.get("sha256"), str)
        or not isinstance(stage_ref.get("sha256"), str)
    ):
        raise ContractError("closed run summary/stage provenance references are invalid")
    summary_ref_text = f"{summary_ref['path']}#sha256:{summary_ref['sha256']}"
    stage_ref_text = f"{stage_ref['path']}#sha256:{stage_ref['sha256']}"
    provenance = {
        "closure_hash": closure_hash,
        "summary_ref": summary_ref_text,
        "stage_artifacts_ref": stage_ref_text,
        "evidence_digest_hash": evidence_digest_hash,
    }
    report = {
        "artifact_type": DECLASSIFICATION_REPORT_ARTIFACT_TYPE,
        "format_version": CANDIDATE_FORMAT_VERSION,
        "candidate_id": case_id,
        "status": "PASS",
        "evidence_digest_hash": evidence_digest_hash,
        "excluded_categories": list(EXCLUDED_EVIDENCE_CATEGORIES),
        "checks": {
            "closure_verified_before_evidence_read": True,
            "sanitized_digest_only": True,
            "candidate_fields_declassified": True,
            "current_run_unchanged": True,
            "experience_library_unchanged": True,
        },
    }
    report_hash, _ = _write_canonical_immutable_preview(report)
    candidate = {
        "artifact_type": CANDIDATE_ARTIFACT_TYPE,
        "format_version": CANDIDATE_FORMAT_VERSION,
        "status": CANDIDATE_STATUS,
        "candidate_id": case_id,
        **semantic,
        "provenance": provenance,
        "declassification_report_hash": report_hash,
    }
    if set(candidate) != _CANDIDATE_FIELDS:
        raise ContractError("compiled candidate fields are not closed")
    if set(candidate["provenance"]) != _PROVENANCE_FIELDS:
        raise ContractError("compiled candidate provenance fields are not closed")
    validate_declassification_report(
        report,
        candidate_id=case_id,
        evidence_digest_hash=evidence_digest_hash,
    )

    candidate_path = case_directory / "experience_candidate.json"
    report_path = case_directory / "declassification_report.json"
    candidate_seal_path = case_directory / "experience_candidate.seal.json"
    report_seal_path = case_directory / "declassification_report.seal.json"
    for path in (candidate_path, report_path, candidate_seal_path, report_seal_path):
        if path.exists() or path.is_symlink():
            raise ContractError(f"Evolution case path is immutable and already populated: {path}")

    # Write the report first because the candidate binds its exact external hash.
    declassification_report_hash, _ = _write_canonical_immutable(report_path, report)
    if declassification_report_hash != report_hash:
        raise ContractError("declassification report hash changed before candidate binding")
    candidate_hash, _ = _write_canonical_immutable(candidate_path, candidate)
    report_seal = create_seal(
        DECLASSIFICATION_REPORT_ARTIFACT_TYPE,
        declassification_report_hash,
        [evidence_digest_hash],
    )
    candidate_seal = create_seal(
        CANDIDATE_ARTIFACT_TYPE,
        candidate_hash,
        [closure_hash, evidence_digest_hash, declassification_report_hash],
    )
    _write_canonical_immutable(report_seal_path, report_seal)
    _write_canonical_immutable(candidate_seal_path, candidate_seal)
    return ExperienceCandidateResult(
        candidate_path=candidate_path,
        declassification_report_path=report_path,
        candidate_seal_path=candidate_seal_path,
        declassification_report_seal_path=report_seal_path,
        candidate_hash=candidate_hash,
        declassification_report_hash=declassification_report_hash,
        evidence_digest_hash=evidence_digest_hash,
        closure_hash=closure_hash,
    )


def _write_canonical_immutable_preview(value: Mapping[str, Any]) -> tuple[str, bytes]:
    """Hash a value exactly as it will be written, without writing it."""

    payload = canonical_bytes(dict(value))
    return content_hash(payload), payload


__all__ = [
    "CANDIDATE_ARTIFACT_TYPE",
    "CANDIDATE_FORMAT_VERSION",
    "CANDIDATE_STATUS",
    "DECLASSIFICATION_REPORT_ARTIFACT_TYPE",
    "EXCLUDED_EVIDENCE_CATEGORIES",
    "EvolutionAgent",
    "ExperienceCandidateResult",
    "build_sanitized_evidence_digest",
    "propose_experience_candidate",
    "validate_declassification_report",
]
