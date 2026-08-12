"""Experimental-1 Experience Library review and future-run snapshots.

The Authority leaves the long-term Experience schemas open.  This module therefore
implements only the bounded path needed by the experiment: a checked candidate and
declassification report can be admitted by an explicitly supplied human review,
and later runs can consume an immutable, recipient-specific snapshot.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError, ImmutableError
from ..foundation.hashing import content_hash, is_content_hash


_FORMAT_VERSION = "experimental-1"
_EXPERIENCE_ROOT_NAME = "experience"
_CANDIDATE_KEYS = {
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
}
_APPLICABILITY_KEYS = {
    "robot_model_id",
    "robot_configuration_id",
    "sdk_entry_id",
    "granularity_condition",
    "capability_effect_scope",
    "observation_condition",
}
_PROVENANCE_KEYS = {
    "closure_hash",
    "summary_ref",
    "stage_artifacts_ref",
    "evidence_digest_hash",
}
_DECLASSIFICATION_KEYS = {
    "artifact_type",
    "format_version",
    "candidate_id",
    "status",
    "evidence_digest_hash",
    "excluded_categories",
    "checks",
}
_REVIEW_KEYS = {
    "decision",
    "reviewer_kind",
    "reviewer_id",
    "review_note",
    "reviewed_at",
}
_REVIEW_ARTIFACT_KEYS = {
    "artifact_type",
    "format_version",
    "candidate_id",
    "decision",
    "reviewer_kind",
    "reviewer_id",
    "review_note",
    "reviewed_at",
    "candidate_ref",
    "declassification_ref",
}
_RECORD_KEYS = {
    "artifact_type",
    "format_version",
    "record_id",
    "version",
    "status",
    "candidate_id",
    "experience_id",
    "recipient_class",
    "lesson",
    "applicability",
    "provenance",
    "limitations",
    "invalidation_conditions",
    "candidate_content_hash",
    "declassification_report_content_hash",
    "review_content_hash",
    "provenance_content_hash",
}
_PROVENANCE_ARTIFACT_KEYS = {
    "artifact_type",
    "format_version",
    "record_id",
    "version",
    "candidate_id",
    "candidate_content_hash",
    "declassification_report_content_hash",
    "review_content_hash",
    "provenance",
}
_SNAPSHOT_KEYS = {
    "artifact_type",
    "format_version",
    "snapshot_id",
    "recipient_class",
    "applicability",
    "records",
}
_SNAPSHOT_RECORD_KEYS = {"record_id", "version", "record_ref", "projection"}
_PROJECTION_KEYS = {"experience_id", "guidance", "applicability", "provenance"}
_FILE_REF_HASH_KEYS = {"content_hash", "hash", "sha256"}


def _object(value: Any, label: str, keys: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"Experience Library {label} must be an object")
    result = dict(value)
    if keys is not None and set(result) != keys:
        raise ContractError(
            f"Experience Library {label} must have exactly {sorted(keys)}"
        )
    return result


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"Experience Library {label} must be non-empty text")
    return value


def _component(value: Any, label: str) -> str:
    text = _text(value, label)
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise ContractError(f"Experience Library {label} must be one safe path component")
    return text


def _hash(value: Any, label: str) -> str:
    if not is_content_hash(value):
        raise ContractError(f"Experience Library {label} must be sha256:<hex>")
    return value


def _string_list(value: Any, label: str, *, nonempty: bool) -> list[str]:
    if not isinstance(value, list):
        raise ContractError(f"Experience Library {label} must be an array")
    if nonempty and not value:
        raise ContractError(f"Experience Library {label} must not be empty")
    result: list[str] = []
    for item in value:
        result.append(_text(item, f"{label} item"))
    return result


def _provenance_ref(value: Any, label: str) -> str:
    # The experimental candidate compiler currently emits opaque string refs.
    # Keeping them opaque prevents raw evidence from entering a future snapshot.
    return _text(value, label)


def _validate_applicability(
    value: Any,
    label: str,
    *,
    require_sdk: bool,
) -> dict[str, Any]:
    applicability = _object(value, label, _APPLICABILITY_KEYS)
    result = {
        "robot_model_id": _text(applicability["robot_model_id"], f"{label}.robot_model_id"),
        "robot_configuration_id": _text(
            applicability["robot_configuration_id"],
            f"{label}.robot_configuration_id",
        ),
        "sdk_entry_id": applicability["sdk_entry_id"],
        "granularity_condition": _text(
            applicability["granularity_condition"],
            f"{label}.granularity_condition",
        ),
        "capability_effect_scope": _string_list(
            applicability["capability_effect_scope"],
            f"{label}.capability_effect_scope",
            nonempty=True,
        ),
        "observation_condition": _text(
            applicability["observation_condition"],
            f"{label}.observation_condition",
        ),
    }
    if result["sdk_entry_id"] is not None:
        result["sdk_entry_id"] = _text(
            result["sdk_entry_id"], f"{label}.sdk_entry_id"
        )
    elif require_sdk:
        raise ContractError(
            f"Experience Library {label}.sdk_entry_id is required for implementation Experience"
        )
    return result


def _validate_provenance(value: Any, label: str) -> dict[str, Any]:
    provenance = _object(value, label, _PROVENANCE_KEYS)
    return {
        "closure_hash": _hash(provenance["closure_hash"], f"{label}.closure_hash"),
        "summary_ref": _provenance_ref(provenance["summary_ref"], f"{label}.summary_ref"),
        "stage_artifacts_ref": _provenance_ref(
            provenance["stage_artifacts_ref"], f"{label}.stage_artifacts_ref"
        ),
        "evidence_digest_hash": _hash(
            provenance["evidence_digest_hash"], f"{label}.evidence_digest_hash"
        ),
    }


def _validate_candidate(value: Any) -> dict[str, Any]:
    candidate = _object(value, "candidate", _CANDIDATE_KEYS)
    if candidate["artifact_type"] != "experience_candidate":
        raise ContractError("Experience Library candidate has wrong artifact_type")
    if candidate["format_version"] != _FORMAT_VERSION:
        raise ContractError("Experience Library candidate has unsupported format_version")
    if candidate["status"] != "PROPOSED_REVIEW_REQUIRED":
        raise ContractError("Experience Library candidate is not PROPOSED_REVIEW_REQUIRED")
    recipient_class = _text(candidate["recipient_class"], "candidate.recipient_class")
    if recipient_class not in {"design", "implementation"}:
        raise ContractError("Experience Library candidate.recipient_class is unsupported")
    return {
        "artifact_type": candidate["artifact_type"],
        "format_version": candidate["format_version"],
        "status": candidate["status"],
        "candidate_id": _component(candidate["candidate_id"], "candidate.candidate_id"),
        "recipient_class": recipient_class,
        "lesson": _text(candidate["lesson"], "candidate.lesson"),
        "applicability": _validate_applicability(
            candidate["applicability"],
            "candidate.applicability",
            require_sdk=recipient_class == "implementation",
        ),
        "provenance": _validate_provenance(candidate["provenance"], "candidate.provenance"),
        "limitations": _string_list(
            candidate["limitations"], "candidate.limitations", nonempty=False
        ),
        "invalidation_conditions": _string_list(
            candidate["invalidation_conditions"],
            "candidate.invalidation_conditions",
            nonempty=True,
        ),
        "declassification_report_hash": _hash(
            candidate["declassification_report_hash"],
            "candidate.declassification_report_hash",
        ),
    }


def _validate_declassification(value: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    report = _object(value, "declassification report", _DECLASSIFICATION_KEYS)
    if report["artifact_type"] not in {
        "experience_declassification_report",
        "declassification_report",
    }:
        raise ContractError("Experience Library declassification report has wrong artifact_type")
    if report["format_version"] != _FORMAT_VERSION:
        raise ContractError("Experience Library declassification report has unsupported format_version")
    if report["status"] != "PASS":
        raise ContractError("Experience Library declassification report is not PASS")
    if _text(report["candidate_id"], "declassification report.candidate_id") != candidate["candidate_id"]:
        raise ContractError("Experience Library candidate and declassification report are not linked")
    evidence_digest_hash = _hash(
        report["evidence_digest_hash"],
        "declassification report.evidence_digest_hash",
    )
    if evidence_digest_hash != candidate["provenance"]["evidence_digest_hash"]:
        raise ContractError("Experience Library declassification evidence digest does not match candidate")
    _string_list(
        report["excluded_categories"],
        "declassification report.excluded_categories",
        nonempty=False,
    )
    checks = report["checks"]
    if not isinstance(checks, (Mapping, list)) or not checks:
        raise ContractError("Experience Library declassification report.checks must be non-empty")
    try:
        canonical_bytes(checks)
    except Exception as exc:
        raise ContractError("Experience Library declassification report.checks must be JSON data") from exc
    return copy.deepcopy(report)


def _normalise_hash(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"Experience Library file reference {field} must be text")
    if value.startswith("sha256:"):
        return _hash(value, f"file reference {field}")
    if field in {"hash", "sha256"} and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value
    ):
        return f"sha256:{value}"
    raise ContractError(f"Experience Library file reference {field} must be sha256:<hex>")


def _root_context(root: str | Path) -> tuple[Path, Path, Path]:
    supplied = Path(root).expanduser().resolve()
    if supplied.name == "general_demo" or (
        (supplied / "libraries").is_dir() and not (supplied / "general_demo").exists()
    ):
        demo_root = supplied
    else:
        demo_root = supplied / "general_demo"
    workspace_root = demo_root.parent
    experience_root = demo_root / "libraries" / _EXPERIENCE_ROOT_NAME
    return supplied, workspace_root, experience_root


def _within(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ContractError(f"Experience Library {label} escapes the supplied root") from exc
    return resolved


def _candidate_paths(
    supplied: Path,
    workspace_root: Path,
    experience_root: Path,
    raw_path: str | Path,
) -> list[Path]:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return [path]
    return [
        supplied / path,
        workspace_root / path,
        experience_root.parent.parent / path,
    ]


def _resolve_existing_path(
    supplied: Path,
    workspace_root: Path,
    experience_root: Path,
    raw_path: str | Path,
    label: str,
) -> Path:
    candidates = _candidate_paths(supplied, workspace_root, experience_root, raw_path)
    for candidate in candidates:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(workspace_root.resolve())
        except ValueError:
            continue
        if resolved.exists():
            if resolved.is_symlink():
                raise ContractError(f"Experience Library {label} cannot be a symlink")
            return resolved
    raise ContractError(f"Experience Library {label} is missing")


def _resolve_output_path(
    supplied: Path,
    workspace_root: Path,
    experience_root: Path,
    raw_path: str | Path,
    label: str,
) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return _within(path, workspace_root, label)
    existing = _candidate_paths(supplied, workspace_root, experience_root, path)
    for candidate in existing:
        if candidate.exists():
            return _within(candidate, workspace_root, label)
    return _within(supplied / path, workspace_root, label)


def _relative_path(path: Path, workspace_root: Path, label: str) -> str:
    try:
        return path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError as exc:
        raise ContractError(f"Experience Library {label} cannot be represented as a workspace reference") from exc


def _normalise_file_ref(
    supplied: Path,
    workspace_root: Path,
    experience_root: Path,
    value: Any,
    label: str,
) -> tuple[dict[str, str], Path]:
    ref = _object(value, label)
    if "path" not in ref:
        raise ContractError(f"Experience Library {label} requires path and content hash")
    hash_fields = [field for field in _FILE_REF_HASH_KEYS if field in ref]
    if len(hash_fields) != 1 or set(ref) - ({"path"} | set(hash_fields)):
        raise ContractError(f"Experience Library {label} must contain exactly path and one content hash")
    raw_path = ref["path"]
    if not isinstance(raw_path, (str, Path)):
        raise ContractError(f"Experience Library {label}.path must be text")
    path = _resolve_existing_path(
        supplied,
        workspace_root,
        experience_root,
        raw_path,
        f"{label}.path",
    )
    expected_hash = _normalise_hash(ref[hash_fields[0]], hash_fields[0])
    actual_hash = content_hash(path.read_bytes())
    if actual_hash != expected_hash:
        raise ContractError(
            f"Experience Library {label} hash mismatch: expected {expected_hash}, got {actual_hash}"
        )
    return {
        "path": _relative_path(path, workspace_root, f"{label}.path"),
        "content_hash": actual_hash,
    }, path


def _read_json_path(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"Experience Library {label} is missing") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"Experience Library {label} is invalid JSON") from exc
    return _object(value, label)


def _read_ref_json(
    supplied: Path,
    workspace_root: Path,
    experience_root: Path,
    value: Any,
    label: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    ref, path = _normalise_file_ref(supplied, workspace_root, experience_root, value, label)
    return ref, _read_json_path(path, label)


def _validate_review(value: Any) -> dict[str, str]:
    review = _object(value, "review", _REVIEW_KEYS)
    decision = _text(review["decision"], "review.decision")
    if decision not in {"HUMAN_APPROVED", "REJECTED"}:
        raise ContractError("Experience Library review.decision must be HUMAN_APPROVED or REJECTED")
    reviewer_kind = _text(review["reviewer_kind"], "review.reviewer_kind")
    if decision == "HUMAN_APPROVED" and reviewer_kind != "HUMAN":
        raise ContractError("Experience Library admission requires reviewer_kind HUMAN")
    return {
        "decision": decision,
        "reviewer_kind": reviewer_kind,
        "reviewer_id": _text(review["reviewer_id"], "review.reviewer_id"),
        "review_note": _text(review["review_note"], "review.review_note"),
        "reviewed_at": _text(review["reviewed_at"], "review.reviewed_at"),
    }


def _review_artifact(
    candidate_id: str,
    review: dict[str, str],
    candidate_ref: dict[str, str],
    declassification_ref: dict[str, str],
) -> dict[str, Any]:
    return {
        "artifact_type": "experience_review",
        "format_version": _FORMAT_VERSION,
        "candidate_id": candidate_id,
        **copy.deepcopy(review),
        "candidate_ref": copy.deepcopy(candidate_ref),
        "declassification_ref": copy.deepcopy(declassification_ref),
    }


def _provenance_artifact(
    record_id: str,
    version: str,
    candidate: dict[str, Any],
    candidate_content_hash: str,
    declassification_content_hash: str,
    review_content_hash: str,
) -> dict[str, Any]:
    return {
        "artifact_type": "experience_provenance",
        "format_version": _FORMAT_VERSION,
        "record_id": record_id,
        "version": version,
        "candidate_id": candidate["candidate_id"],
        "candidate_content_hash": candidate_content_hash,
        "declassification_report_content_hash": declassification_content_hash,
        "review_content_hash": review_content_hash,
        "provenance": copy.deepcopy(candidate["provenance"]),
    }


def _record_artifact(
    record_id: str,
    version: str,
    candidate: dict[str, Any],
    candidate_content_hash: str,
    declassification_content_hash: str,
    review_content_hash: str,
    provenance_content_hash: str,
) -> dict[str, Any]:
    return {
        "artifact_type": "experience_record",
        "format_version": _FORMAT_VERSION,
        "record_id": record_id,
        "version": version,
        "status": "HUMAN_APPROVED",
        "candidate_id": candidate["candidate_id"],
        "experience_id": record_id,
        "recipient_class": candidate["recipient_class"],
        "lesson": candidate["lesson"],
        "applicability": copy.deepcopy(candidate["applicability"]),
        "provenance": copy.deepcopy(candidate["provenance"]),
        "limitations": copy.deepcopy(candidate["limitations"]),
        "invalidation_conditions": copy.deepcopy(candidate["invalidation_conditions"]),
        "candidate_content_hash": candidate_content_hash,
        "declassification_report_content_hash": declassification_content_hash,
        "review_content_hash": review_content_hash,
        "provenance_content_hash": provenance_content_hash,
    }


def _write_immutable(path: Path, value: dict[str, Any], label: str) -> str:
    payload = canonical_bytes(value)
    expected_hash = content_hash(payload)
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ImmutableError(f"Experience Library {label} is not an immutable file")
        if path.read_bytes() != payload:
            raise ImmutableError(f"Experience Library {label} cannot be overwritten")
        return expected_hash
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ImmutableError(f"Experience Library {label} parent cannot be a symlink")
    path.write_bytes(payload)
    return expected_hash


def _record_directory(
    experience_root: Path,
    recipient_class: str,
    record_id: str,
    version: str,
) -> Path:
    return experience_root / "records" / recipient_class / record_id / version


def _review_output_path(
    supplied: Path,
    workspace_root: Path,
    experience_root: Path,
    candidate_id: str,
    review_hash: str,
) -> Path:
    # Review decisions that do not admit a record remain outside the Library.
    path = workspace_root / "experience_review_artifacts" / candidate_id / (
        review_hash.removeprefix("sha256:") + ".json"
    )
    path = _within(path, workspace_root, "rejected review artifact")
    if path.resolve().is_relative_to(experience_root.resolve()):
        raise ContractError("Experience Library rejected review artifact cannot be inside the Library")
    return path


def review_and_include_candidate(
    root: str | Path,
    candidate_ref: Mapping[str, Any],
    declassification_ref: Mapping[str, Any],
    review: Mapping[str, Any],
    *,
    record_id: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    """Review a candidate and, only after human approval, publish one record.

    ``record_id`` and ``version`` are keyword-only because the closed review object
    intentionally contains review data only.  They are required for approval and
    are never inferred from a candidate or generated by this function.
    """

    supplied, workspace_root, experience_root = _root_context(root)
    candidate_ref_value, candidate = _read_ref_json(
        supplied, workspace_root, experience_root, candidate_ref, "candidate"
    )
    candidate = _validate_candidate(candidate)
    declassification_ref_value, report = _read_ref_json(
        supplied,
        workspace_root,
        experience_root,
        declassification_ref,
        "declassification report",
    )
    _validate_declassification(report, candidate)
    if candidate["declassification_report_hash"] != declassification_ref_value["content_hash"]:
        raise ContractError(
            "Experience Library candidate.declassification_report_hash does not match the report file"
        )
    checked_review = _validate_review(review)
    review_artifact = _review_artifact(
        candidate["candidate_id"],
        checked_review,
        candidate_ref_value,
        declassification_ref_value,
    )

    if checked_review["decision"] == "REJECTED":
        review_payload = canonical_bytes(review_artifact)
        review_hash = content_hash(review_payload)
        review_path = _review_output_path(
            supplied,
            workspace_root,
            experience_root,
            candidate["candidate_id"],
            review_hash,
        )
        _write_immutable(review_path, review_artifact, "rejected review artifact")
        return {
            "status": "REJECTED",
            "review_ref": {
                "path": _relative_path(review_path, workspace_root, "rejected review artifact"),
                "content_hash": review_hash,
            },
        }

    if record_id is None or version is None:
        raise ContractError(
            "Experience Library approved admission requires caller-supplied record_id and version"
        )
    record_id = _component(record_id, "record_id")
    version = _component(version, "version")

    review_payload = canonical_bytes(review_artifact)
    review_content_hash = content_hash(review_payload)
    provenance = _provenance_artifact(
        record_id,
        version,
        candidate,
        candidate_ref_value["content_hash"],
        declassification_ref_value["content_hash"],
        review_content_hash,
    )
    provenance_content_hash = content_hash(canonical_bytes(provenance))
    record = _record_artifact(
        record_id,
        version,
        candidate,
        candidate_ref_value["content_hash"],
        declassification_ref_value["content_hash"],
        review_content_hash,
        provenance_content_hash,
    )
    record_directory = _record_directory(experience_root, candidate["recipient_class"], record_id, version)
    record_path = record_directory / "record.json"
    review_path = record_directory / "review.json"
    provenance_path = record_directory / "provenance.json"

    existing = [record_path.exists(), review_path.exists(), provenance_path.exists()]
    if any(existing) and not all(existing):
        raise ImmutableError("Experience Library record version is partially published")
    _write_immutable(review_path, review_artifact, "experience review")
    _write_immutable(provenance_path, provenance, "experience provenance")
    _write_immutable(record_path, record, "experience record")

    return {
        "status": "HUMAN_APPROVED",
        "record_id": record_id,
        "version": version,
        "record_ref": {
            "path": _relative_path(record_path, workspace_root, "experience record"),
            "content_hash": content_hash(record_path.read_bytes()),
        },
        "review_ref": {
            "path": _relative_path(review_path, workspace_root, "experience review"),
            "content_hash": review_content_hash,
        },
        "provenance_ref": {
            "path": _relative_path(provenance_path, workspace_root, "experience provenance"),
            "content_hash": provenance_content_hash,
        },
    }


def _record_path_parts(
    path: Path,
    workspace_root: Path,
    experience_root: Path,
    label: str,
) -> tuple[str, str, str]:
    try:
        relative = path.resolve().relative_to(experience_root.resolve())
    except ValueError as exc:
        raise ContractError(f"Experience Library {label} is outside the Experience records") from exc
    parts = relative.parts
    if len(parts) != 5 or parts[0] != "records" or parts[4] != "record.json":
        raise ContractError(f"Experience Library {label} must point to records/<recipient>/<id>/<version>/record.json")
    recipient_class, record_id, version = parts[1:4]
    _component(recipient_class, f"{label} recipient_class")
    _component(record_id, f"{label} record_id")
    _component(version, f"{label} version")
    return recipient_class, record_id, version


def _validate_review_artifact(
    value: Any,
    candidate_id: str,
    candidate_ref: dict[str, str],
    declassification_ref: dict[str, str],
) -> dict[str, Any]:
    review = _object(value, "stored review", _REVIEW_ARTIFACT_KEYS)
    if review["artifact_type"] != "experience_review" or review["format_version"] != _FORMAT_VERSION:
        raise ContractError("Experience Library stored review has wrong identity")
    if _text(review["candidate_id"], "stored review.candidate_id") != candidate_id:
        raise ContractError("Experience Library stored review candidate mismatch")
    checked = _validate_review({key: review[key] for key in _REVIEW_KEYS})
    if checked["decision"] != "HUMAN_APPROVED" or checked["reviewer_kind"] != "HUMAN":
        raise ContractError("Experience Library stored review is not a human approval")
    stored_candidate_ref = _object(review["candidate_ref"], "stored review.candidate_ref", {"path", "content_hash"})
    stored_declassification_ref = _object(
        review["declassification_ref"],
        "stored review.declassification_ref",
        {"path", "content_hash"},
    )
    _text(stored_candidate_ref["path"], "stored review.candidate_ref.path")
    _hash(stored_candidate_ref["content_hash"], "stored review.candidate_ref.content_hash")
    _text(
        stored_declassification_ref["path"],
        "stored review.declassification_ref.path",
    )
    _hash(
        stored_declassification_ref["content_hash"],
        "stored review.declassification_ref.content_hash",
    )
    if stored_candidate_ref != candidate_ref or stored_declassification_ref != declassification_ref:
        raise ContractError("Experience Library stored review reference binding changed")
    return copy.deepcopy(review)


def _validate_provenance_artifact(
    value: Any,
    record_id: str,
    version: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    provenance = _object(value, "stored provenance", _PROVENANCE_ARTIFACT_KEYS)
    if provenance["artifact_type"] != "experience_provenance" or provenance["format_version"] != _FORMAT_VERSION:
        raise ContractError("Experience Library stored provenance has wrong identity")
    if provenance["record_id"] != record_id or provenance["version"] != version:
        raise ContractError("Experience Library stored provenance identity mismatch")
    for field in (
        "candidate_content_hash",
        "declassification_report_content_hash",
        "review_content_hash",
    ):
        _hash(provenance[field], f"stored provenance.{field}")
        if provenance[field] != record[field]:
            raise ContractError(f"Experience Library stored provenance {field} binding changed")
    if provenance["candidate_id"] != record["candidate_id"]:
        raise ContractError("Experience Library stored provenance candidate mismatch")
    checked = _validate_provenance(provenance["provenance"], "stored provenance.provenance")
    if checked != record["provenance"]:
        raise ContractError("Experience Library stored provenance payload changed")
    return copy.deepcopy(provenance)


def _validate_record(
    record: Any,
    recipient_class: str,
    record_id: str,
    version: str,
) -> dict[str, Any]:
    value = _object(record, "experience record", _RECORD_KEYS)
    if value["artifact_type"] != "experience_record" or value["format_version"] != _FORMAT_VERSION:
        raise ContractError("Experience Library record has wrong identity")
    if value["record_id"] != record_id or value["version"] != version:
        raise ContractError("Experience Library record path and identity do not match")
    if value["status"] != "HUMAN_APPROVED":
        raise ContractError("Experience Library record is not HUMAN_APPROVED")
    if value["recipient_class"] != recipient_class:
        raise ContractError("Experience Library record recipient does not match its path")
    if value["experience_id"] != record_id:
        raise ContractError("Experience Library record experience_id does not match record_id")
    _component(value["candidate_id"], "experience record.candidate_id")
    _text(value["lesson"], "experience record.lesson")
    _validate_applicability(
        value["applicability"],
        "experience record.applicability",
        require_sdk=recipient_class == "implementation",
    )
    _validate_provenance(value["provenance"], "experience record.provenance")
    _string_list(value["limitations"], "experience record.limitations", nonempty=False)
    _string_list(
        value["invalidation_conditions"],
        "experience record.invalidation_conditions",
        nonempty=True,
    )
    for field in (
        "candidate_content_hash",
        "declassification_report_content_hash",
        "review_content_hash",
        "provenance_content_hash",
    ):
        _hash(value[field], f"experience record.{field}")
    return copy.deepcopy(value)


def _load_included_record(
    supplied: Path,
    workspace_root: Path,
    experience_root: Path,
    value: Any,
) -> tuple[dict[str, str], dict[str, Any]]:
    ref, path = _normalise_file_ref(
        supplied, workspace_root, experience_root, value, "record_ref"
    )
    recipient_class, record_id, version = _record_path_parts(
        path, workspace_root, experience_root, "record_ref"
    )
    record = _validate_record(
        _read_json_path(path, "experience record"),
        recipient_class,
        record_id,
        version,
    )
    directory = path.parent
    review_path = directory / "review.json"
    provenance_path = directory / "provenance.json"
    if any(item.is_symlink() for item in (directory, review_path, provenance_path)):
        raise ContractError("Experience Library included record files cannot be symlinks")
    if not review_path.is_file() or not provenance_path.is_file():
        raise ContractError("Experience Library included record requires review.json and provenance.json")
    candidate_ref = {
        "path": "",
        "content_hash": record["candidate_content_hash"],
    }
    declassification_ref = {
        "path": "",
        "content_hash": record["declassification_report_content_hash"],
    }
    # The stored review is checked for its exact links below.  The paths are not
    # needed to retrieve old source evidence, which keeps future snapshots closed.
    review = _read_json_path(review_path, "experience review")
    if review.get("candidate_ref", {}).get("content_hash") != candidate_ref["content_hash"]:
        raise ContractError("Experience Library record does not bind its candidate hash through review")
    if review.get("declassification_ref", {}).get("content_hash") != declassification_ref["content_hash"]:
        raise ContractError("Experience Library record does not bind its declassification hash through review")
    stored_candidate_ref = _object(review["candidate_ref"], "stored review.candidate_ref", {"path", "content_hash"})
    stored_declassification_ref = _object(
        review["declassification_ref"],
        "stored review.declassification_ref",
        {"path", "content_hash"},
    )
    _validate_review_artifact(
        review,
        record["candidate_id"],
        stored_candidate_ref,
        stored_declassification_ref,
    )
    review_hash = content_hash(review_path.read_bytes())
    if review_hash != record["review_content_hash"]:
        raise ContractError("Experience Library record review hash does not match review.json")
    provenance = _read_json_path(provenance_path, "experience provenance")
    _validate_provenance_artifact(provenance, record_id, version, record)
    provenance_hash = content_hash(provenance_path.read_bytes())
    if provenance_hash != record["provenance_content_hash"]:
        raise ContractError("Experience Library record provenance hash does not match provenance.json")
    return ref, record


def _projection(record: dict[str, Any]) -> dict[str, Any]:
    # This is deliberately a closed recipient projection.  It does not copy the
    # review, declassification report, raw evidence, or candidate file.
    return {
        "experience_id": record["experience_id"],
        "guidance": record["lesson"],
        "applicability": copy.deepcopy(record["applicability"]),
        "provenance": copy.deepcopy(record["provenance"]),
    }


def _validate_snapshot_value(
    supplied: Path,
    workspace_root: Path,
    experience_root: Path,
    value: Any,
    *,
    recipient_class: str | None,
    applicability: dict[str, Any] | None,
) -> dict[str, Any]:
    snapshot = _object(value, "experience snapshot", _SNAPSHOT_KEYS)
    if snapshot["artifact_type"] != "experience_snapshot":
        raise ContractError("Experience Library snapshot has wrong artifact_type")
    if snapshot["format_version"] != _FORMAT_VERSION:
        raise ContractError("Experience Library snapshot has unsupported format_version")
    snapshot_id = _component(snapshot["snapshot_id"], "snapshot.snapshot_id")
    actual_recipient = _text(snapshot["recipient_class"], "snapshot.recipient_class")
    if actual_recipient not in {"design", "implementation"}:
        raise ContractError("Experience Library snapshot recipient_class is unsupported")
    if recipient_class is not None and actual_recipient != recipient_class:
        raise ContractError("Experience Library snapshot recipient does not match requested recipient")
    selected_applicability = _validate_applicability(
        snapshot["applicability"],
        "snapshot.applicability",
        require_sdk=actual_recipient == "implementation",
    )
    if applicability is not None and selected_applicability != applicability:
        raise ContractError("Experience Library snapshot applicability does not match requested selection")
    records = snapshot["records"]
    if not isinstance(records, list):
        raise ContractError("Experience Library snapshot.records must be an array")

    validated_records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_entry in records:
        entry = _object(raw_entry, "snapshot record", _SNAPSHOT_RECORD_KEYS)
        record_id = _component(entry["record_id"], "snapshot record.record_id")
        version = _component(entry["version"], "snapshot record.version")
        key = (record_id, version)
        if key in seen:
            raise ContractError("Experience Library snapshot cannot repeat a record version")
        seen.add(key)
        ref, record = _load_included_record(
            supplied,
            workspace_root,
            experience_root,
            entry["record_ref"],
        )
        path_recipient, path_record_id, path_version = _record_path_parts(
            _resolve_existing_path(
                supplied,
                workspace_root,
                experience_root,
                ref["path"],
                "snapshot record_ref.path",
            ),
            workspace_root,
            experience_root,
            "snapshot record_ref",
        )
        if (path_record_id, path_version) != key or path_recipient != actual_recipient:
            raise ContractError("Experience Library snapshot record identity does not match selection")
        if record["applicability"] != selected_applicability:
            raise ContractError("Experience Library snapshot contains an applicability mismatch")
        if entry["record_ref"] != ref:
            raise ContractError("Experience Library snapshot record_ref is not canonical")
        if entry["record_id"] != record["record_id"] or entry["version"] != record["version"]:
            raise ContractError("Experience Library snapshot record identity is not canonical")
        expected_projection = _projection(record)
        projection = _object(entry["projection"], "snapshot projection", _PROJECTION_KEYS)
        if projection != expected_projection:
            raise ContractError("Experience Library snapshot projection is not the approved closed projection")
        validated_records.append(
            {
                "record_id": record_id,
                "version": version,
                "record_ref": copy.deepcopy(ref),
                "projection": copy.deepcopy(expected_projection),
            }
        )

    order = [(item["record_id"], item["version"]) for item in validated_records]
    if order != sorted(order):
        raise ContractError("Experience Library snapshot records must be deterministic record_id/version order")
    return {
        "artifact_type": "experience_snapshot",
        "format_version": _FORMAT_VERSION,
        "snapshot_id": snapshot_id,
        "recipient_class": actual_recipient,
        "applicability": copy.deepcopy(selected_applicability),
        "records": validated_records,
    }


def build_experience_snapshot(
    root: str | Path,
    output_path: str | Path,
    snapshot_id: str,
    recipient_class: str,
    applicability: Mapping[str, Any],
    record_refs: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build and immutably write one exact future-run Experience snapshot."""

    supplied, workspace_root, experience_root = _root_context(root)
    snapshot_id = _component(snapshot_id, "snapshot_id")
    recipient_class = _text(recipient_class, "recipient_class")
    if recipient_class not in {"design", "implementation"}:
        raise ContractError("Experience Library recipient_class is unsupported")
    selected_applicability = _validate_applicability(
        applicability,
        "applicability",
        require_sdk=recipient_class == "implementation",
    )
    if not isinstance(record_refs, list):
        raise ContractError("Experience Library record_refs must be an array")

    entries: list[dict[str, Any]] = []
    for record_ref in record_refs:
        ref, record = _load_included_record(
            supplied,
            workspace_root,
            experience_root,
            record_ref,
        )
        if record["recipient_class"] != recipient_class:
            raise ContractError("Experience Library record recipient does not match snapshot")
        if record["applicability"] != selected_applicability:
            raise ContractError("Experience Library record applicability does not match snapshot")
        entries.append(
            {
                "record_id": record["record_id"],
                "version": record["version"],
                "record_ref": ref,
                "projection": _projection(record),
            }
        )

    entries.sort(key=lambda item: (item["record_id"], item["version"]))
    keys = [(item["record_id"], item["version"]) for item in entries]
    if len(keys) != len(set(keys)):
        raise ContractError("Experience Library snapshot cannot repeat a record version")
    snapshot = {
        "artifact_type": "experience_snapshot",
        "format_version": _FORMAT_VERSION,
        "snapshot_id": snapshot_id,
        "recipient_class": recipient_class,
        "applicability": selected_applicability,
        "records": entries,
    }
    output = _resolve_output_path(
        supplied,
        workspace_root,
        experience_root,
        output_path,
        "experience snapshot output",
    )
    _write_immutable(output, snapshot, "experience snapshot")
    return verify_experience_snapshot(
        root,
        output,
        recipient_class=recipient_class,
        applicability=selected_applicability,
    )


def verify_experience_snapshot(
    root: str | Path,
    snapshot_path: str | Path,
    *,
    recipient_class: str | None = None,
    applicability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deep-copied validated immutable snapshot."""

    supplied, workspace_root, experience_root = _root_context(root)
    path = _resolve_existing_path(
        supplied,
        workspace_root,
        experience_root,
        snapshot_path,
        "experience snapshot",
    )
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError("Experience Library snapshot is invalid JSON") from exc
    if canonical_bytes(value) != raw:
        raise ContractError("Experience Library snapshot must be canonical JSON")
    selected_applicability = None
    if applicability is not None:
        selected_applicability = _validate_applicability(
            applicability,
            "requested applicability",
            require_sdk=recipient_class == "implementation",
        )
    validated = _validate_snapshot_value(
        supplied,
        workspace_root,
        experience_root,
        value,
        recipient_class=recipient_class,
        applicability=selected_applicability,
    )
    return copy.deepcopy(validated)


__all__ = [
    "build_experience_snapshot",
    "review_and_include_candidate",
    "verify_experience_snapshot",
]
