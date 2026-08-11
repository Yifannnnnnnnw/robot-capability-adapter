"""Small, closed JSON contracts for the experiment-grade integration gate.

These contracts deliberately describe files, hashes, and run bindings only.  They do
not model registries, issuers, signatures, lifecycle events, or robot-specific
semantics.  Robot-specific validation belongs to the records' future validators.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import IntegrityError, SchemaValidationError
from ..foundation.hashing import sha256_bytes


SCHEMA_VERSION = "1.0.0"
READINESS_PROFILE_ID = "general-demo-integration-readiness"
READINESS_PROFILE_VERSION = "1.0.0"
READINESS_CHECK_IDS = (
    "sdk_identity_load",
    "hook_install",
    "real_sdk_application_execution",
    "sdk_to_mujoco_command",
    "mujoco_to_sdk_observation",
    "reset_close",
)
FROZEN_READINESS_LIMITS = {
    "per_check_wall_s": 60,
    "attempt_wall_s": 180,
    "transport_operation_wall_s": 2,
    "max_probe_simulation_s": 1.0,
    "cleanup_wall_s": 5,
    "hidden_retry_count": 0,
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]*$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_PYTHON_VERSION = re.compile(r"^[0-9]+\.[0-9]+$")


@dataclass(frozen=True)
class JsonArtifact:
    """One strict JSON object and the SHA-256 of its exact file bytes."""

    path: Path
    value: dict[str, Any]
    sha256: str

    @property
    def file_reference(self) -> dict[str, str]:
        return {"path": self.path.as_posix(), "sha256": self.sha256}


def stable_json_bytes(value: Any) -> bytes:
    """Return the project's stable JSON representation for newly written files."""

    return canonical_bytes(value)


def stable_json_sha256(value: Any) -> str:
    """Hash a value's stable JSON representation without a ``sha256:`` prefix."""

    return sha256_bytes(stable_json_bytes(value))


def write_stable_json(path: str | Path, value: Any) -> str:
    """Atomically write stable JSON and return the SHA-256 of the written bytes."""

    destination = Path(path)
    payload = stable_json_bytes(value)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{secrets.token_hex(16)}.tmp"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return sha256_bytes(payload)


def _duplicate_key_rejector(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SchemaValidationError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise SchemaValidationError(f"non-finite JSON constant is not allowed: {value}")


def _assert_json_domain(value: Any, path: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise SchemaValidationError(f"{path}: non-finite number")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SchemaValidationError(f"{path}: JSON object key is not a string")
            _assert_json_domain(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_json_domain(item, f"{path}[{index}]")
    elif value is None or isinstance(value, (str, int, float, bool)):
        return
    else:
        raise SchemaValidationError(f"{path}: unsupported JSON value")


def strict_json_object(raw: bytes, *, label: str = "JSON artifact") -> dict[str, Any]:
    """Parse a JSON object while rejecting duplicate keys and non-finite numbers."""

    if raw.startswith(b"\xef\xbb\xbf"):
        raise SchemaValidationError(f"{label}: UTF-8 BOM is not allowed")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SchemaValidationError(f"{label}: must be UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_key_rejector,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, SchemaValidationError) as exc:
        if isinstance(exc, SchemaValidationError):
            raise
        raise SchemaValidationError(f"{label}: invalid JSON") from exc
    _assert_json_domain(value)
    if not isinstance(value, dict):
        raise SchemaValidationError(f"{label}: root must be an object")
    return value


def load_json_artifact(path: str | Path) -> JsonArtifact:
    artifact_path = Path(path)
    try:
        raw = artifact_path.read_bytes()
    except OSError as exc:
        raise IntegrityError(f"cannot read JSON artifact: {artifact_path}") from exc
    return JsonArtifact(
        path=artifact_path,
        value=strict_json_object(raw, label=str(artifact_path)),
        sha256=sha256_bytes(raw),
    )


def _closed_object(
    value: Any,
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaValidationError(f"{label}: must be an object")
    required_keys = set(required)
    permitted_keys = required_keys | set(optional)
    missing = required_keys - set(value)
    extra = set(value) - permitted_keys
    if missing:
        raise SchemaValidationError(f"{label}: missing fields: {sorted(missing)}")
    if extra:
        raise SchemaValidationError(f"{label}: unexpected fields: {sorted(extra)}")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise SchemaValidationError(f"{label}: invalid identifier")
    return value


def _semver(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SEMVER.fullmatch(value):
        raise SchemaValidationError(f"{label}: invalid semantic version")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise SchemaValidationError(f"{label}: must be 64 lowercase hexadecimal characters")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{label}: must be a non-empty string")
    return value


def _safe_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SchemaValidationError(f"{label}: must be a safe relative POSIX path")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise SchemaValidationError(f"{label}: must be a safe relative POSIX path")
    if candidate.as_posix() != value:
        raise SchemaValidationError(f"{label}: path must be normalized")
    return value


def validate_file_reference(value: Any, *, label: str) -> dict[str, str]:
    reference = _closed_object(value, required={"path", "sha256"}, label=label)
    return {
        "path": _safe_relative_path(reference["path"], f"{label}.path"),
        "sha256": _sha256(reference["sha256"], f"{label}.sha256"),
    }


def _list_of_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{label}: must be an array")
    return [_string(item, f"{label}[{index}]") for index, item in enumerate(value)]


def _list_of_file_references(value: Any, label: str, *, nonempty: bool) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{label}: must be an array")
    if nonempty and not value:
        raise SchemaValidationError(f"{label}: must not be empty")
    return [validate_file_reference(item, label=f"{label}[{index}]") for index, item in enumerate(value)]


def _validate_runtime(value: Any, *, status: str) -> dict[str, Any]:
    runtime = _closed_object(
        value,
        required={"id", "version", "os", "architecture", "python", "mujoco", "lock_sha256"},
        optional={"cyclonedds"},
        label="integration_manifest.runtime",
    )
    _identifier(runtime["id"], "integration_manifest.runtime.id")
    _semver(runtime["version"], "integration_manifest.runtime.version")
    _string(runtime["os"], "integration_manifest.runtime.os")
    if runtime["architecture"] != "amd64":
        raise SchemaValidationError("integration_manifest.runtime.architecture: must be amd64")
    if not isinstance(runtime["python"], str) or not _PYTHON_VERSION.fullmatch(runtime["python"]):
        raise SchemaValidationError("integration_manifest.runtime.python: must be major.minor")
    if runtime["mujoco"] != "3.3.6":
        raise SchemaValidationError("integration_manifest.runtime.mujoco: must be 3.3.6")
    if "cyclonedds" in runtime:
        _semver(runtime["cyclonedds"], "integration_manifest.runtime.cyclonedds")
    lock = runtime["lock_sha256"]
    if lock is not None:
        _sha256(lock, "integration_manifest.runtime.lock_sha256")
    if status == "READY" and lock is None:
        raise SchemaValidationError("READY integration manifest requires runtime.lock_sha256")
    return runtime


def _validate_compatibility_checks(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise SchemaValidationError("integration_manifest.compatibility_checks: must be non-empty")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        check = _closed_object(
            item,
            required={"check_id", "verdict"},
            label=f"integration_manifest.compatibility_checks[{index}]",
        )
        check_id = _identifier(check["check_id"], f"integration_manifest.compatibility_checks[{index}].check_id")
        if check_id in seen:
            raise SchemaValidationError("integration_manifest.compatibility_checks: duplicate check_id")
        seen.add(check_id)
        if check["verdict"] not in {"PASS", "FAIL", "NOT_RUN"}:
            raise SchemaValidationError("integration_manifest.compatibility_checks: invalid verdict")
        result.append({"check_id": check_id, "verdict": check["verdict"]})
    return result


def validate_integration_manifest(value: Any) -> dict[str, Any]:
    """Validate the generic manifest shape without applying robot-specific rules.

    ``DRAFT`` and ``FROZEN_FIXTURE`` are intentionally parseable so development and
    test fixtures can be inspected.  Neither is eligible for the Stage-1 gate.
    """

    base_fields = {
        "schema_version",
        "manifest_id",
        "version",
        "status",
        "robot_model_id",
        "robot_configuration_id",
        "morphology_ref",
        "sdk_ref",
        "translation_ref",
        "runtime",
        "compatibility_checks",
        "unresolved_gaps",
    }
    manifest = _closed_object(
        value,
        required=base_fields,
        optional={"readiness_profile_ref"},
        label="integration_manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise SchemaValidationError("integration_manifest.schema_version is unsupported")
    _identifier(manifest["manifest_id"], "integration_manifest.manifest_id")
    _semver(manifest["version"], "integration_manifest.version")
    status = manifest["status"]
    if status not in {"DRAFT", "READY", "FROZEN_FIXTURE"}:
        raise SchemaValidationError("integration_manifest.status is invalid")
    _identifier(manifest["robot_model_id"], "integration_manifest.robot_model_id")
    _identifier(manifest["robot_configuration_id"], "integration_manifest.robot_configuration_id")
    validate_file_reference(manifest["morphology_ref"], label="integration_manifest.morphology_ref")
    validate_file_reference(manifest["sdk_ref"], label="integration_manifest.sdk_ref")
    validate_file_reference(manifest["translation_ref"], label="integration_manifest.translation_ref")
    _validate_runtime(manifest["runtime"], status=status)
    if "readiness_profile_ref" in manifest:
        validate_file_reference(
            manifest["readiness_profile_ref"],
            label="integration_manifest.readiness_profile_ref",
        )
    elif status == "READY":
        raise SchemaValidationError("READY integration manifest requires readiness_profile_ref")
    checks = _validate_compatibility_checks(manifest["compatibility_checks"])
    gaps = _list_of_strings(manifest["unresolved_gaps"], "integration_manifest.unresolved_gaps")
    if status == "READY":
        if gaps:
            raise SchemaValidationError("READY integration manifest must not have unresolved gaps")
        if any(check["verdict"] != "PASS" for check in checks):
            raise SchemaValidationError("READY integration manifest requires all compatibility checks to PASS")
    return manifest


def load_integration_manifest(path: str | Path) -> JsonArtifact:
    artifact = load_json_artifact(path)
    validate_integration_manifest(artifact.value)
    return artifact


def validate_readiness_profile(value: Any) -> dict[str, Any]:
    profile = _closed_object(
        value,
        required={
            "schema_version",
            "profile_id",
            "version",
            "check_ids",
            "time_limits",
            "numerical_tolerances",
        },
        label="readiness_profile",
    )
    if profile["schema_version"] != SCHEMA_VERSION:
        raise SchemaValidationError("readiness_profile.schema_version is unsupported")
    if profile["profile_id"] != READINESS_PROFILE_ID:
        raise SchemaValidationError("readiness_profile.profile_id is not the frozen profile")
    if profile["version"] != READINESS_PROFILE_VERSION:
        raise SchemaValidationError("readiness_profile.version is not the frozen profile version")
    if not isinstance(profile["check_ids"], list) or tuple(profile["check_ids"]) != READINESS_CHECK_IDS:
        raise SchemaValidationError("readiness_profile.check_ids must be the six frozen checks in order")
    _validate_time_limits(profile["time_limits"], "readiness_profile.time_limits")
    _nonempty_json_object(profile["numerical_tolerances"], "readiness_profile.numerical_tolerances")
    return profile


def _validate_time_limits(value: Any, label: str) -> dict[str, Any]:
    limits = _closed_object(value, required=FROZEN_READINESS_LIMITS, label=label)
    for key, expected in FROZEN_READINESS_LIMITS.items():
        actual = limits[key]
        if isinstance(expected, int):
            if not isinstance(actual, int) or isinstance(actual, bool):
                raise SchemaValidationError(f"{label}.{key}: must be an integer")
        elif not isinstance(actual, (int, float)) or isinstance(actual, bool) or not math.isfinite(actual):
            raise SchemaValidationError(f"{label}.{key}: must be a finite number")
        if actual != expected:
            raise SchemaValidationError(f"{label}.{key}: does not equal the frozen limit")
    return limits


def _nonempty_json_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise SchemaValidationError(f"{label}: must be a non-empty object")
    _assert_json_domain(value, label)
    return value


def validate_run_snapshot(value: Any) -> dict[str, Any]:
    snapshot = _closed_object(
        value,
        required={
            "schema_version",
            "run_id",
            "integration_manifest_ref",
            "readiness_report_ref",
            "runtime_sha256",
            "readiness_profile_ref",
            "library_view_refs",
            "task_set_ref",
            "g2_profile_ref",
            "observation_profile_ref",
            "model_prompt_config_ref",
            "budget_ref",
            "blue_line_input_refs",
            "sealed_artifact_refs",
        },
        label="run_snapshot",
    )
    if snapshot["schema_version"] != SCHEMA_VERSION:
        raise SchemaValidationError("run_snapshot.schema_version is unsupported")
    _identifier(snapshot["run_id"], "run_snapshot.run_id")
    for field in (
        "integration_manifest_ref",
        "readiness_report_ref",
        "readiness_profile_ref",
        "task_set_ref",
        "g2_profile_ref",
        "observation_profile_ref",
        "model_prompt_config_ref",
        "budget_ref",
    ):
        validate_file_reference(snapshot[field], label=f"run_snapshot.{field}")
    _sha256(snapshot["runtime_sha256"], "run_snapshot.runtime_sha256")
    _list_of_file_references(snapshot["library_view_refs"], "run_snapshot.library_view_refs", nonempty=True)
    _list_of_file_references(snapshot["blue_line_input_refs"], "run_snapshot.blue_line_input_refs", nonempty=True)
    _list_of_file_references(snapshot["sealed_artifact_refs"], "run_snapshot.sealed_artifact_refs", nonempty=False)
    return snapshot


def _validate_readiness_check(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaValidationError(f"readiness_report.checks[{index}]: must be an object")
    verdict = value.get("verdict")
    required = {"check_id", "verdict", "evidence_refs"}
    optional = {"infrastructure_category"} if verdict == "FAIL" else set()
    check = _closed_object(
        value,
        required=required | optional,
        label=f"readiness_report.checks[{index}]",
    )
    check_id = _identifier(check["check_id"], f"readiness_report.checks[{index}].check_id")
    if check["verdict"] not in {"PASS", "FAIL"}:
        raise SchemaValidationError(f"readiness_report.checks[{index}].verdict is invalid")
    if check["verdict"] == "FAIL":
        _identifier(
            check["infrastructure_category"],
            f"readiness_report.checks[{index}].infrastructure_category",
        )
    _list_of_file_references(
        check["evidence_refs"],
        f"readiness_report.checks[{index}].evidence_refs",
        nonempty=True,
    )
    return check


def _validate_cleanup(value: Any) -> dict[str, Any]:
    cleanup = _closed_object(
        value,
        required={"verdict", "evidence_refs"},
        label="readiness_report.cleanup",
    )
    if cleanup["verdict"] not in {"PASS", "FAIL"}:
        raise SchemaValidationError("readiness_report.cleanup.verdict is invalid")
    _list_of_file_references(cleanup["evidence_refs"], "readiness_report.cleanup.evidence_refs", nonempty=True)
    return cleanup


def validate_readiness_report(value: Any) -> dict[str, Any]:
    report = _closed_object(
        value,
        required={
            "schema_version",
            "attempt_id",
            "run_id",
            "integration_manifest_ref",
            "runtime_sha256",
            "readiness_profile_ref",
            "environment_fingerprint_sha256",
            "dependency_sha256",
            "time_limits",
            "numerical_tolerances",
            "checks",
            "cleanup",
            "started_at",
            "ended_at",
            "verdict",
        },
        label="readiness_report",
    )
    if report["schema_version"] != SCHEMA_VERSION:
        raise SchemaValidationError("readiness_report.schema_version is unsupported")
    _identifier(report["attempt_id"], "readiness_report.attempt_id")
    _identifier(report["run_id"], "readiness_report.run_id")
    validate_file_reference(report["integration_manifest_ref"], label="readiness_report.integration_manifest_ref")
    validate_file_reference(report["readiness_profile_ref"], label="readiness_report.readiness_profile_ref")
    _sha256(report["runtime_sha256"], "readiness_report.runtime_sha256")
    _sha256(report["environment_fingerprint_sha256"], "readiness_report.environment_fingerprint_sha256")
    dependencies = _closed_object(
        report["dependency_sha256"],
        required={"morphology", "sdk", "translation", "runtime_lock", "readiness_profile"},
        label="readiness_report.dependency_sha256",
    )
    for name, digest in dependencies.items():
        _sha256(digest, f"readiness_report.dependency_sha256.{name}")
    _validate_time_limits(report["time_limits"], "readiness_report.time_limits")
    _nonempty_json_object(report["numerical_tolerances"], "readiness_report.numerical_tolerances")
    if not isinstance(report["checks"], list):
        raise SchemaValidationError("readiness_report.checks: must be an array")
    checks = [_validate_readiness_check(check, index) for index, check in enumerate(report["checks"])]
    if tuple(check["check_id"] for check in checks) != READINESS_CHECK_IDS:
        raise SchemaValidationError("readiness_report.checks must be the six frozen checks in order")
    _validate_cleanup(report["cleanup"])
    _string(report["started_at"], "readiness_report.started_at")
    _string(report["ended_at"], "readiness_report.ended_at")
    if report["verdict"] not in {"PASS", "FAIL"}:
        raise SchemaValidationError("readiness_report.verdict is invalid")
    return report


def load_run_snapshot(path: str | Path) -> JsonArtifact:
    artifact = load_json_artifact(path)
    validate_run_snapshot(artifact.value)
    return artifact


def load_readiness_report(path: str | Path) -> JsonArtifact:
    artifact = load_json_artifact(path)
    validate_readiness_report(artifact.value)
    return artifact


def resolve_reference_path(root: str | Path, reference: Mapping[str, str]) -> Path:
    """Resolve a validated reference beneath ``root`` without allowing symlink escape."""

    root_path = Path(root).resolve()
    relative = _safe_relative_path(reference["path"], "file reference.path")
    candidate = root_path.joinpath(*PurePosixPath(relative).parts)
    current = root_path
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise IntegrityError(f"referenced file cannot be a symlink: {relative}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root_path)
    except (OSError, ValueError) as exc:
        raise IntegrityError(f"referenced file does not exist beneath root: {relative}") from exc
    if not resolved.is_file():
        raise IntegrityError(f"referenced path is not a regular file: {relative}")
    return resolved


def verify_file_reference(root: str | Path, reference: Mapping[str, str]) -> Path:
    validated = validate_file_reference(reference, label="file reference")
    path = resolve_reference_path(root, validated)
    try:
        actual = sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise IntegrityError(f"cannot read referenced file: {validated['path']}") from exc
    if actual != validated["sha256"]:
        raise IntegrityError(
            f"referenced file hash mismatch for {validated['path']}: "
            f"expected {validated['sha256']}, got {actual}"
        )
    return path

