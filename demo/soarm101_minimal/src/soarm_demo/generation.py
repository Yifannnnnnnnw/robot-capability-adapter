"""Phase-gated workspace and orchestration for one continuous Generation Agent."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

from .audit import (
    BudgetCounter,
    BudgetExceeded,
    atomic_write_json,
    canonical_json,
    sha256_bytes,
    sha256_file,
    sha256_json,
)
from .model_client import ModelClient
from .react_agent import AgentResult, GenerationReActAgent, ToolSpec
from .schema_validation import (
    ArtifactSchemaError,
    validate_json_schema,
    validate_stage1,
)
from .static_validation import validate_generated_package
from .tool_packager import (
    annotation_to_json_schema,
    package_tree_sha256,
    unstructured_array_schema_paths,
)

if TYPE_CHECKING:
    from .generation_simulation import GenerationSimulationSandbox


class GenerationWorkspaceError(RuntimeError):
    pass


ALLOWED_GENERATED_FILES = {
    "package_manifest.json",
    "generated_capability_package/__init__.py",
    "generated_capability_package/_kinematics.py",
    "generated_capability_package/g1.py",
    "generated_capability_package/g2.py",
    "generated_capability_package/g3.py",
}
ALLOWED_GENERATED_PYTHON_FILES = ALLOWED_GENERATED_FILES - {"package_manifest.json"}
DEFAULT_REPAIR_CONTEXT_MAX_BYTES = 256 * 1024
DEFAULT_REPAIR_LEDGER_MAX_BYTES = 64 * 1024
MAX_REPAIR_LEDGER_FAILURES_PER_VALIDATION = 64
MAX_GENERATED_TEXT_BYTES = 500_000
MAX_DIRECT_GENERATED_PYTHON_BYTES = 8_192
MAX_GENERATED_CHUNK_BYTES = 6_144
MAX_GENERATED_CHUNKS_PER_APPEND = 3
MAX_REPAIR_SEARCH_QUERY_CHARACTERS = 256
MAX_REPAIR_SEARCH_MATCHES = 8
MAX_REPAIR_SEARCH_CONTEXT_LINES = 6
MAX_REPAIR_SEARCH_SNIPPET_CHARACTERS = 8_192
MAX_REPAIR_SYMBOL_SOURCE_BYTES = 32_768
MAX_REPAIR_SYMBOL_CANDIDATES = 64
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_REPAIR_LEDGER_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:/-]{1,160}$")
_DIRECT_NAME_ERROR = re.compile(
    r"\bname ['\"]([A-Za-z_][A-Za-z0-9_]{0,79})['\"] is not defined\b"
)
_SAFE_EXCEPTION_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
_INPUT_SNAPSHOT_MANIFEST_NAME = "generation_input_snapshot_manifest.json"


@dataclass(frozen=True, order=True)
class _InputSnapshotFileRecord:
    """One content-addressed, generation-visible regular file."""

    path: str
    bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "bytes": self.bytes,
            "sha256": self.sha256,
        }


def _hash_regular_file_without_following_symlinks(
    path: Path,
) -> tuple[int, str]:
    """Hash one regular file through a no-follow descriptor.

    The pre/post descriptor checks make a concurrent replacement or mutation a
    hard integrity failure instead of allowing a partially read snapshot into
    a Generation context.
    """

    flags = os.O_RDONLY
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    flags |= no_follow
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise GenerationWorkspaceError(
            "input snapshot contains an unreadable or non-regular entry"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise GenerationWorkspaceError(
                "input snapshot entries must be regular files"
            )
        digest = hashlib.sha256()
        byte_count = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            byte_count += len(block)
            digest.update(block)
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or byte_count != after.st_size
        ):
            raise GenerationWorkspaceError(
                "input snapshot file changed while it was being verified"
            )
    finally:
        os.close(descriptor)

    try:
        current = os.lstat(path)
    except OSError as exc:
        raise GenerationWorkspaceError(
            "input snapshot file disappeared while it was being verified"
        ) from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != after.st_dev
        or current.st_ino != after.st_ino
        or current.st_size != after.st_size
        or current.st_mtime_ns != after.st_mtime_ns
    ):
        raise GenerationWorkspaceError(
            "input snapshot file was replaced while it was being verified"
        )
    return byte_count, digest.hexdigest()


def _read_frozen_regular_file(
    path: Path,
    expected: _InputSnapshotFileRecord,
) -> bytes:
    """Read a small frozen file through a no-follow descriptor and rebind it."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise GenerationWorkspaceError(
            "input snapshot changed while generation facts were read"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise GenerationWorkspaceError(
                "input snapshot changed while generation facts were read"
            )
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        byte_count = 0
        while True:
            block = os.read(descriptor, 64 * 1024)
            if not block:
                break
            chunks.append(block)
            digest.update(block)
            byte_count += len(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        current = os.lstat(path)
    except OSError as exc:
        raise GenerationWorkspaceError(
            "input snapshot changed while generation facts were read"
        ) from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or current.st_dev != after.st_dev
        or current.st_ino != after.st_ino
        or current.st_size != expected.bytes
        or current.st_mtime_ns != after.st_mtime_ns
        or byte_count != expected.bytes
        or digest.hexdigest() != expected.sha256
    ):
        raise GenerationWorkspaceError(
            "input snapshot changed while generation facts were read"
        )
    return b"".join(chunks)


def _scan_input_snapshot(root: Path) -> tuple[_InputSnapshotFileRecord, ...]:
    """Return the complete ordered regular-file allowlist for ``root``.

    Directories are structural only. Every symlink, socket, FIFO, device, or
    other non-regular leaf fails closed, including a symlinked snapshot root.
    """

    try:
        root_status = os.lstat(root)
    except OSError as exc:
        raise GenerationWorkspaceError(f"input snapshot is missing: {root}") from exc
    if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
        raise GenerationWorkspaceError(
            "input snapshot root must be a real directory, not a symlink"
        )

    records: list[_InputSnapshotFileRecord] = []

    def visit(directory: Path, relative_directory: Path) -> None:
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            raise GenerationWorkspaceError(
                "input snapshot directory could not be enumerated"
            ) from exc
        for entry in entries:
            relative = relative_directory / entry.name
            relative_text = relative.as_posix()
            try:
                entry_status = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise GenerationWorkspaceError(
                    "input snapshot entry disappeared during enumeration"
                ) from exc
            if stat.S_ISLNK(entry_status.st_mode):
                raise GenerationWorkspaceError(
                    f"input snapshot integrity violation: symlink={relative_text!r}"
                )
            if stat.S_ISDIR(entry_status.st_mode):
                visit(Path(entry.path), relative)
                continue
            if not stat.S_ISREG(entry_status.st_mode):
                raise GenerationWorkspaceError(
                    "input snapshot integrity violation: "
                    f"non_regular={relative_text!r}"
                )
            byte_count, digest = _hash_regular_file_without_following_symlinks(
                Path(entry.path)
            )
            records.append(
                _InputSnapshotFileRecord(
                    path=relative_text,
                    bytes=byte_count,
                    sha256=digest,
                )
            )

    visit(root, Path())
    return tuple(sorted(records))


def _repair_ledger_identifier(value: Any) -> str | None:
    """Return one public identifier, never free-form validation evidence."""

    if isinstance(value, str) and _REPAIR_LEDGER_IDENTIFIER.fullmatch(value):
        return value
    return None


def _repair_identifier_ref(
    value: Any,
    *,
    kind: str,
    salt: str,
) -> str | None:
    """Create one run-local fixed-size pseudonym for a safe identifier.

    Raw capability and validation-case identifiers can be both private and up
    to 160 characters long.  A salted 96-bit SHA-256 prefix keeps equality
    linkable across this generation agent's repair rounds without carrying the
    source identifier into the persisted ledger or later provider contexts.
    """

    identifier = _repair_ledger_identifier(value)
    if identifier is None:
        return None
    digest = sha256_bytes(
        f"repair-ledger:{kind}:{salt}\0{identifier}".encode("utf-8")
    )[:24]
    return f"{kind}_{digest}"


def _repair_secondary_error_type(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if _SAFE_EXCEPTION_TYPE.fullmatch(name) else "REDACTED_ERROR_TYPE"


def _repair_failure_signature(
    value: Any,
    *,
    identifier_salt: str,
) -> dict[str, Any]:
    """Reduce a failure to stable routing fields without copying its payload."""

    if not isinstance(value, Mapping):
        signature: dict[str, Any] = {
            "code": "UNSTRUCTURED_FAILURE_REDACTED",
            "capability_ref": None,
            "case_ref": None,
        }
    else:
        code = _repair_ledger_identifier(value.get("code"))
        signature = {
            "code": code or "UNSAFE_FAILURE_CODE_REDACTED",
            "capability_ref": _repair_identifier_ref(
                value.get("capability_id"),
                kind="cap",
                salt=identifier_salt,
            ),
            "case_ref": _repair_identifier_ref(
                value.get("case_id"),
                kind="case",
                salt=identifier_salt,
            ),
        }
        message = value.get("message")
        if value.get("code") == "DIRECT_EXCEPTION" and isinstance(message, str):
            # This is deliberately the only free-form diagnostic promoted to
            # later rounds.  It solves the common static/direct oscillation in
            # which a NameError disappears with its direct traceback, without
            # carrying any traceback, excerpt, values, paths, or private task
            # data into the next provider request.
            match = _DIRECT_NAME_ERROR.search(message[:4_096])
            if match is not None:
                signature["diagnostic"] = {
                    "kind": "python_unresolved_symbol",
                    "unresolved_symbol": match.group(1),
                }
    signature["signature_sha256"] = sha256_json(signature)
    return signature


def _repair_feedback_summary(
    feedback: Mapping[str, Any],
    *,
    identifier_salt: str,
) -> dict[str, Any]:
    """Create the only feedback representation allowed in repair history.

    Messages, measurements, diagnostics, execution details, and tracebacks can
    be useful as *current* feedback, but carrying them through every later
    model request is both unsafe and unbounded.  The ledger therefore retains
    only public routing identifiers plus deterministic counts.
    """

    stage_value = feedback.get("stage")
    stage = stage_value if stage_value in {"static", "direct_function"} else "unknown"
    round_value = feedback.get("repair_round")
    feedback_round = (
        round_value
        if isinstance(round_value, int)
        and not isinstance(round_value, bool)
        and 0 <= round_value <= 10
        else None
    )
    failures_value = feedback.get("failures", [])
    if isinstance(failures_value, (list, tuple)):
        failures = list(failures_value)
    else:
        failures = []
    if len(failures) > MAX_REPAIR_LEDGER_FAILURES_PER_VALIDATION:
        raise GenerationWorkspaceError(
            "repair feedback has too many failures for the bounded ledger: "
            f"count={len(failures)}, "
            f"limit={MAX_REPAIR_LEDGER_FAILURES_PER_VALIDATION}"
        )
    signatures = [
        _repair_failure_signature(item, identifier_salt=identifier_salt)
        for item in failures
    ]
    explicit_passed = feedback.get("passed")
    passed = explicit_passed if isinstance(explicit_passed, bool) else not failures
    summary: dict[str, Any] = {
        "stage": stage,
        "feedback_repair_round": feedback_round,
        "result": "passed" if passed else "failed",
        "failure_count": len(failures),
        "failure_signatures": signatures,
    }
    no_change = feedback.get("no_package_change")
    if isinstance(no_change, Mapping):
        summary["package_change_gate"] = {
            "code": (
                "NO_PACKAGE_CHANGE"
                if no_change.get("code") == "NO_PACKAGE_CHANGE"
                else "UNKNOWN_GATE"
            ),
            "consecutive_rounds": (
                no_change.get("consecutive_rounds")
                if isinstance(no_change.get("consecutive_rounds"), int)
                and not isinstance(no_change.get("consecutive_rounds"), bool)
                else None
            ),
            "retry_available": (
                no_change.get("retry_available")
                if isinstance(no_change.get("retry_available"), bool)
                else None
            ),
        }
    return summary


def _repair_signature_key(signature: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        signature.get("code"),
        signature.get("capability_ref"),
        signature.get("case_ref"),
        signature.get("signature_sha256"),
    )


def _subsequent_validation_summary(
    *,
    origin_feedback: Mapping[str, Any],
    validation_feedback: Mapping[str, Any],
) -> dict[str, Any]:
    """Annotate the immediate post-repair validation without cross-stage guessing."""

    result = copy.deepcopy(dict(validation_feedback))
    same_stage = origin_feedback.get("stage") == validation_feedback.get("stage")
    origin_keys = {
        _repair_signature_key(item)
        for item in origin_feedback.get("failure_signatures", [])
        if isinstance(item, Mapping)
    }
    validation_keys = {
        _repair_signature_key(item)
        for item in validation_feedback.get("failure_signatures", [])
        if isinstance(item, Mapping)
    }
    if not same_stage:
        resolution_status = "not_revalidated_different_stage"
        unresolved: bool | None = None
    elif validation_feedback.get("result") == "passed":
        resolution_status = "resolved"
        unresolved = False
    elif origin_keys & validation_keys:
        resolution_status = "still_unresolved"
        unresolved = True
    else:
        resolution_status = "no_longer_reported"
        unresolved = False
    result["against_round_feedback"] = {
        "same_stage": same_stage,
        "resolution_status": resolution_status,
        "unresolved": unresolved,
        "matching_failure_count": len(origin_keys & validation_keys),
    }
    return result


def _validation_summary_identity(summary: Mapping[str, Any]) -> str:
    """Identify one validation result independent of repair-routing metadata."""

    return canonical_json(
        {
            "stage": summary.get("stage"),
            "result": summary.get("result"),
            "failure_count": summary.get("failure_count"),
            "failure_signatures": summary.get("failure_signatures", []),
        }
    )


def repair_ledger_semantic_issues(
    ledger: Mapping[str, Any],
    *,
    projection_next_round: int | None = None,
) -> list[str]:
    """Recompute cross-field repair-ledger invariants omitted by JSON Schema."""

    issues: list[str] = []
    rounds_value = ledger.get("rounds")
    registry_value = ledger.get("failure_registry")
    if not isinstance(rounds_value, list):
        return ["rounds must be an array"]
    if not isinstance(registry_value, Mapping):
        return ["failure_registry must be an object"]
    rounds = rounds_value
    registry = registry_value

    round_count = ledger.get("round_count")
    if round_count != len(rounds):
        issues.append(
            f"round_count={round_count!r} does not equal rounds length {len(rounds)}"
        )
    expected_rounds = list(range(1, len(rounds) + 1))
    observed_rounds = [
        item.get("round") if isinstance(item, Mapping) else None
        for item in rounds
    ]
    if observed_rounds != expected_rounds:
        issues.append(
            f"round sequence {observed_rounds!r} is not contiguous {expected_rounds!r}"
        )
    expected_through = len(rounds)
    if ledger.get("through_repair_round") != expected_through:
        issues.append(
            "through_repair_round does not equal the last completed repair round"
        )
    if projection_next_round is not None and projection_next_round != expected_through + 1:
        issues.append("projection_next_round must immediately follow through_repair_round")

    try:
        actual_bytes = len(canonical_json(ledger).encode("utf-8"))
    except BaseException:
        issues.append("ledger is not canonical-JSON serializable")
        actual_bytes = None
    if actual_bytes is not None and ledger.get("utf8_bytes") != actual_bytes:
        issues.append(
            f"utf8_bytes={ledger.get('utf8_bytes')!r} does not equal {actual_bytes}"
        )
    maximum_bytes = ledger.get("max_utf8_bytes")
    if (
        actual_bytes is not None
        and isinstance(maximum_bytes, int)
        and not isinstance(maximum_bytes, bool)
        and actual_bytes > maximum_bytes
    ):
        issues.append(
            f"canonical ledger bytes {actual_bytes} exceed max_utf8_bytes {maximum_bytes}"
        )

    for reference, signature in registry.items():
        if not isinstance(signature, Mapping):
            continue
        signed = {
            key: copy.deepcopy(value)
            for key, value in signature.items()
            if key != "signature_sha256"
        }
        if signature.get("signature_sha256") != sha256_json(signed):
            issues.append(f"failure_registry.{reference} signature_sha256 mismatch")

    referenced: set[str] = set()

    def summary_refs(summary: Any, path: str) -> list[str]:
        if not isinstance(summary, Mapping):
            issues.append(f"{path} must be an object")
            return []
        refs_value = summary.get("failure_refs")
        refs = refs_value if isinstance(refs_value, list) else []
        if len(refs) != len(set(refs)):
            issues.append(f"{path}.failure_refs contains duplicates")
        for reference in refs:
            if reference not in registry:
                issues.append(
                    f"{path}.failure_refs references missing registry key {reference!r}"
                )
            elif isinstance(reference, str):
                referenced.add(reference)
        result = summary.get("result")
        failure_count = summary.get("failure_count")
        if result == "passed":
            if failure_count != 0 or refs:
                issues.append(
                    f"{path} passed result requires zero failure_count and refs"
                )
        elif result == "failed":
            if (
                not isinstance(failure_count, int)
                or isinstance(failure_count, bool)
                or failure_count < 1
                or not refs
                or failure_count < len(refs)
            ):
                issues.append(
                    f"{path} failed result requires failure_count >= unique nonempty refs"
                )
        return [reference for reference in refs if isinstance(reference, str)]

    visible_events: list[tuple[tuple[int, int], Mapping[str, Any]]] = []
    for index, item in enumerate(rounds, start=1):
        if not isinstance(item, Mapping):
            continue
        feedback = item.get("feedback")
        summary_refs(feedback, f"rounds[{index - 1}].feedback")
        if (
            isinstance(feedback, Mapping)
            and "package_change_gate" not in feedback
        ):
            visible_events.append(((index, 0), feedback))
        observations = item.get("validation_observations")
        observation_list = observations if isinstance(observations, list) else []
        for sequence, observation in enumerate(observation_list, start=1):
            summary_refs(
                observation,
                f"rounds[{index - 1}].validation_observations[{sequence - 1}]",
            )
            if isinstance(observation, Mapping):
                visible_events.append(((index, sequence), observation))
        subsequent = item.get("subsequent_validation")
        if subsequent is not None:
            summary_refs(subsequent, f"rounds[{index - 1}].subsequent_validation")
        expected_subsequent = observation_list[0] if observation_list else None
        if subsequent != expected_subsequent:
            issues.append(
                f"rounds[{index - 1}].subsequent_validation must equal first observation"
            )

        if isinstance(feedback, Mapping):
            origin_refs = set(
                reference
                for reference in feedback.get("failure_refs", [])
                if isinstance(reference, str)
            )
            for sequence, observation in enumerate(observation_list):
                if not isinstance(observation, Mapping):
                    continue
                observation_refs = set(
                    reference
                    for reference in observation.get("failure_refs", [])
                    if isinstance(reference, str)
                )
                same_stage = observation.get("stage") == feedback.get("stage")
                matching = len(origin_refs & observation_refs)
                if not same_stage:
                    expected_against = {
                        "same_stage": False,
                        "resolution_status": "not_revalidated_different_stage",
                        "unresolved": None,
                        "matching_failure_count": matching,
                    }
                elif observation.get("result") == "passed":
                    expected_against = {
                        "same_stage": True,
                        "resolution_status": "resolved",
                        "unresolved": False,
                        "matching_failure_count": matching,
                    }
                elif matching:
                    expected_against = {
                        "same_stage": True,
                        "resolution_status": "still_unresolved",
                        "unresolved": True,
                        "matching_failure_count": matching,
                    }
                else:
                    expected_against = {
                        "same_stage": True,
                        "resolution_status": "no_longer_reported",
                        "unresolved": False,
                        "matching_failure_count": 0,
                    }
                if observation.get("against_round_feedback") != expected_against:
                    issues.append(
                        f"rounds[{index - 1}].validation_observations[{sequence}] "
                        "has inconsistent against_round_feedback"
                    )

    derived_unresolved: list[dict[str, Any]] = []
    for index, item in enumerate(rounds, start=1):
        if not isinstance(item, Mapping):
            continue
        feedback = item.get("feedback")
        if not isinstance(feedback, Mapping):
            continue
        origin_refs = [
            reference
            for reference in feedback.get("failure_refs", [])
            if isinstance(reference, str)
        ]
        origin_stage = feedback.get("stage")
        later = [
            (position, summary)
            for position, summary in visible_events
            if position > (index, 0) and summary.get("stage") == origin_stage
        ]
        latest_position: tuple[int, int] | None = None
        latest_summary: Mapping[str, Any] | None = None
        if later:
            latest_position, latest_summary = later[-1]
        expected_by_ref: dict[str, tuple[str, bool, int | None]] = {}
        for reference in origin_refs:
            if latest_summary is None or latest_position is None:
                expected_by_ref[reference] = ("not_revalidated", True, None)
            elif latest_summary.get("result") == "passed":
                expected_by_ref[reference] = (
                    "resolved_by_later_same_stage_pass",
                    False,
                    latest_position[0],
                )
            elif reference in latest_summary.get("failure_refs", []):
                expected_by_ref[reference] = (
                    "still_unresolved",
                    True,
                    latest_position[0],
                )
            else:
                expected_by_ref[reference] = (
                    "no_longer_reported_by_same_stage",
                    False,
                    latest_position[0],
                )

        resolutions = item.get("failure_resolution")
        resolution_list = resolutions if isinstance(resolutions, list) else []
        covered: set[str] = set()
        for resolution_index, resolution in enumerate(resolution_list):
            if not isinstance(resolution, Mapping):
                continue
            refs = resolution.get("failure_refs")
            resolution_refs = refs if isinstance(refs, list) else []
            if not resolution_refs:
                issues.append(
                    f"rounds[{index - 1}].failure_resolution[{resolution_index}] has no refs"
                )
            for reference in resolution_refs:
                if not isinstance(reference, str):
                    continue
                referenced.add(reference)
                if reference in covered:
                    issues.append(
                        f"rounds[{index - 1}].failure_resolution duplicates {reference!r}"
                    )
                covered.add(reference)
                if reference not in registry:
                    issues.append(
                        f"rounds[{index - 1}].failure_resolution references missing {reference!r}"
                    )
                expected = expected_by_ref.get(reference)
                if expected is None:
                    issues.append(
                        f"rounds[{index - 1}].failure_resolution contains non-origin ref {reference!r}"
                    )
                    continue
                observed = (
                    resolution.get("resolution_status"),
                    resolution.get("unresolved"),
                    resolution.get("last_same_stage_validation_round"),
                )
                if observed != expected:
                    projection_expected = (
                        expected[0],
                        expected[1],
                        projection_next_round,
                    )
                    allow_projection = (
                        projection_next_round is not None
                        and latest_position is not None
                        and latest_position[0] == expected_through
                        and observed == projection_expected
                    )
                    if not allow_projection:
                        issues.append(
                            f"rounds[{index - 1}].failure_resolution for {reference!r} "
                            "does not match latest same-stage validation"
                        )
            if resolution.get("unresolved") is True:
                derived_unresolved.append(
                    {
                        "origin_round": index,
                        **copy.deepcopy(dict(resolution)),
                    }
                )
        if covered != set(origin_refs):
            issues.append(
                f"rounds[{index - 1}].failure_resolution does not partition feedback refs"
            )

    unresolved_value = ledger.get("unresolved_prior_failures")
    if unresolved_value != derived_unresolved:
        issues.append(
            "unresolved_prior_failures does not equal unresolved round resolutions"
        )
    if isinstance(unresolved_value, list):
        for item in unresolved_value:
            if not isinstance(item, Mapping):
                continue
            for reference in item.get("failure_refs", []):
                if isinstance(reference, str):
                    referenced.add(reference)
                if reference not in registry:
                    issues.append(
                        f"unresolved_prior_failures references missing {reference!r}"
                    )

    for index, item in enumerate(rounds):
        if not isinstance(item, Mapping):
            continue
        process = item.get("process")
        if not isinstance(process, Mapping):
            issues.append(f"rounds[{index}].process must be an object")
        else:
            calls_value = process.get("tool_calls")
            calls = calls_value if isinstance(calls_value, list) else []
            if process.get("tool_call_count") != len(calls):
                issues.append(
                    f"rounds[{index}].process.tool_call_count contradicts tool_calls"
                )
            expected_ordinals = list(range(1, len(calls) + 1))
            observed_ordinals = [
                call.get("ordinal") if isinstance(call, Mapping) else None
                for call in calls
            ]
            if observed_ordinals != expected_ordinals:
                issues.append(
                    f"rounds[{index}].process.tool call ordinals are not contiguous"
                )
            successful_writes = sum(
                1
                for call in calls
                if isinstance(call, Mapping)
                and call.get("operation") == "write"
                and call.get("outcome") == "ok"
            )
            if process.get("successful_write_calls") != successful_writes:
                issues.append(
                    f"rounds[{index}].process.successful_write_calls is inconsistent"
                )
            expected_unfinished = (
                copy.deepcopy(calls[-1])
                if calls
                and isinstance(calls[-1], Mapping)
                and calls[-1].get("operation") == "read"
                else None
            )
            if process.get("unfinished_read") != expected_unfinished:
                issues.append(
                    f"rounds[{index}].process.unfinished_read is inconsistent"
                )
        package = item.get("package")
        if not isinstance(package, Mapping):
            continue
        status = package.get("inspection_status")
        before = package.get("before_sha256")
        after = package.get("after_sha256")
        changed = package.get("changed")
        if status == "complete" and changed != (before != after):
            issues.append(f"rounds[{index}].package.changed contradicts package hashes")
        if status == "hash_failed" and (after is not None or changed is not None):
            issues.append(
                f"rounds[{index}].package hash_failed requires null after_sha256/changed"
            )

    if set(registry) != referenced:
        issues.append("failure_registry keys do not equal referenced failure refs")
    return issues


def assert_repair_ledger_semantics(
    ledger: Mapping[str, Any],
    *,
    projection_next_round: int | None = None,
) -> None:
    issues = repair_ledger_semantic_issues(
        ledger,
        projection_next_round=projection_next_round,
    )
    if issues:
        raise GenerationWorkspaceError(
            "repair ledger failed semantic validation: " + "; ".join(issues)
        )


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically replace one generated text file in its existing directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _coordinate_vector_annotation(annotation: Any) -> bool:
    normalized = str(annotation).replace(" ", "").lower()
    return normalized.startswith(
        (
            "list[float]",
            "tuple[float",
            "sequence[float]",
            "typing.list[float]",
            "typing.tuple[float",
            "typing.sequence[float]",
        )
    )


def _coordinate_scalar_annotation(annotation: Any) -> bool:
    return str(annotation).replace(" ", "").lower() in {
        "float",
        "builtins.float",
    }


def _manifest_binding_semantic_issues(
    manifest: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Check cross-field binding facts JSON Schema cannot express cleanly."""

    issues: list[dict[str, str]] = []
    capabilities = manifest.get("capabilities", [])
    if not isinstance(capabilities, list):
        return issues
    for index, capability in enumerate(capabilities):
        if not isinstance(capability, Mapping):
            continue
        prefix = f"$.manifest.capabilities[{index}]"
        signature = capability.get("signature")
        parameters = signature.get("parameters", []) if isinstance(signature, Mapping) else []
        parameter_index = {
            str(parameter.get("name")): parameter
            for parameter in parameters
            if isinstance(parameter, Mapping)
            and isinstance(parameter.get("name"), str)
        } if isinstance(parameters, list) else {}
        for parameter_name, parameter in parameter_index.items():
            if parameter_name == "runtime":
                continue
            annotation_schema = annotation_to_json_schema(
                parameter.get("annotation")
            )
            missing_item_paths = unstructured_array_schema_paths(
                annotation_schema,
                f"{prefix}.signature.parameters[{parameter_name!r}]",
            )
            if missing_item_paths:
                issues.append(
                    {
                        "path": f"{prefix}.signature.parameters",
                        "message": (
                            "ARRAY_ITEM_SCHEMA_MISSING: every public array "
                            "annotation must declare a structured item type; "
                            f"incomplete paths: {missing_item_paths!r}"
                        ),
                    }
                )
        binding = capability.get("validation_binding")
        if not isinstance(binding, Mapping):
            continue
        effect = binding.get("effect")
        argument_names: list[Any] = []
        auxiliary_argument_names: list[Any] = []
        vector_argument_names: list[Any] = []
        component_argument_names: list[Any] = []
        if effect == "joint_targets":
            if "joint_targets_argument" in binding:
                argument_names.append(binding.get("joint_targets_argument"))
            joint_map = binding.get("joint_target_arguments")
            if isinstance(joint_map, Mapping):
                argument_names.extend(joint_map.values())
        elif effect == "cartesian_target":
            if "target_argument" in binding:
                name = binding.get("target_argument")
                argument_names.append(name)
                vector_argument_names.append(name)
            target_arguments = binding.get("target_arguments")
            if isinstance(target_arguments, Mapping):
                argument_names.extend(target_arguments.values())
                component_argument_names.extend(target_arguments.values())
        elif effect == "object_source_to_target":
            source = binding.get("source_argument")
            target = binding.get("target_argument")
            source_arguments = binding.get("source_arguments")
            target_arguments = binding.get("target_arguments")
            if source is not None:
                argument_names.append(source)
                vector_argument_names.append(source)
            if target is not None:
                argument_names.append(target)
                vector_argument_names.append(target)
            if isinstance(source_arguments, Mapping):
                argument_names.extend(source_arguments.values())
                component_argument_names.extend(source_arguments.values())
            if isinstance(target_arguments, Mapping):
                argument_names.extend(target_arguments.values())
                component_argument_names.extend(target_arguments.values())
            source_names = {source} if isinstance(source, str) else set()
            target_names = {target} if isinstance(target, str) else set()
            if isinstance(source_arguments, Mapping):
                source_names.update(source_arguments.values())
            if isinstance(target_arguments, Mapping):
                target_names.update(target_arguments.values())
            if source_names & target_names:
                issues.append(
                    {
                        "path": f"{prefix}.validation_binding",
                        "message": "source and target coordinate bindings must not overlap",
                    }
                )
            if (
                binding.get("target_components") == "xyz"
                and isinstance(target_arguments, Mapping)
                and "z" not in target_arguments
            ):
                issues.append(
                    {
                        "path": f"{prefix}.validation_binding.target_arguments",
                        "message": "xyz target component binding requires x, y, and z",
                    }
                )
        elif effect == "object_source_plus_height_delta":
            source = binding.get("source_argument")
            source_arguments = binding.get("source_arguments")
            delta = binding.get("height_delta_argument")
            if source is not None:
                argument_names.append(source)
                vector_argument_names.append(source)
            if isinstance(source_arguments, Mapping):
                argument_names.extend(source_arguments.values())
                component_argument_names.extend(source_arguments.values())
            argument_names.append(delta)
            source_names = {source} if isinstance(source, str) else set()
            if isinstance(source_arguments, Mapping):
                source_names.update(source_arguments.values())
            if delta in source_names:
                issues.append(
                    {
                        "path": f"{prefix}.validation_binding",
                        "message": "source coordinates and height_delta_argument must differ",
                    }
                )
        elif effect == "object_move_sequence":
            moves_argument = binding.get("moves_argument")
            argument_names.append(moves_argument)
            moves_parameter = (
                parameter_index.get(moves_argument)
                if isinstance(moves_argument, str)
                else None
            )
            moves_schema = (
                annotation_to_json_schema(moves_parameter.get("annotation"))
                if isinstance(moves_parameter, Mapping)
                else {}
            )
            move_items = moves_schema.get("items")
            if (
                moves_schema.get("type") != "array"
                or not isinstance(move_items, Mapping)
                or move_items.get("type") != "object"
            ):
                issues.append(
                    {
                        "path": f"{prefix}.signature.parameters",
                        "message": (
                            "MOVE_ITEM_SCHEMA_INVALID: object_move_sequence "
                            "moves_argument must be annotated as an array of "
                            "structured move objects"
                        ),
                    }
                )
            move_item_fields = (
                binding.get("source_field"),
                binding.get("target_field"),
                binding.get("object_extent_field"),
            )
            if (
                not all(isinstance(name, str) for name in move_item_fields)
                or len(set(move_item_fields)) != len(move_item_fields)
            ):
                issues.append(
                    {
                        "path": f"{prefix}.validation_binding",
                        "message": (
                            "MOVE_ITEM_FIELDS_NOT_DISTINCT: source_field, "
                            "target_field, and object_extent_field must be pairwise "
                            "different literal move-item keys"
                        ),
                    }
                )
            field_parameter_collisions = sorted(
                {
                    name
                    for name in move_item_fields
                    if isinstance(name, str) and name in parameter_index
                }
            )
            if field_parameter_collisions:
                issues.append(
                    {
                        "path": f"{prefix}.validation_binding",
                        "message": (
                            "MOVE_FIELD_SELECTOR_UNSUPPORTED: source_field, "
                            "target_field, and object_extent_field are fixed literal "
                            "move-item keys, not public selector parameters; "
                            "conflicting signature names: "
                            f"{field_parameter_collisions!r}"
                        ),
                    }
                )
        for auxiliary_field in (
            "object_extent_argument",
            "contact_height_argument",
        ):
            if auxiliary_field in binding:
                auxiliary_argument_names.append(binding.get(auxiliary_field))
        for argument_name in argument_names:
            parameter = parameter_index.get(str(argument_name)) if isinstance(argument_name, str) else None
            if argument_name == "runtime" or parameter is None:
                issues.append(
                    {
                        "path": f"{prefix}.validation_binding",
                        "message": (
                            f"bound argument {argument_name!r} is not an existing "
                            "non-runtime signature parameter"
                        ),
                    }
                )
            elif bool(parameter.get("has_default", False)):
                issues.append(
                    {
                        "path": f"{prefix}.signature.parameters",
                        "message": (
                            f"bound source/goal argument {argument_name!r} must be required"
                        ),
                    }
                )
        for argument_name in vector_argument_names:
            parameter = (
                parameter_index.get(str(argument_name))
                if isinstance(argument_name, str)
                else None
            )
            if parameter is not None and not _coordinate_vector_annotation(
                parameter.get("annotation")
            ):
                issues.append(
                    {
                        "path": f"{prefix}.validation_binding",
                        "message": (
                            "VECTOR_BINDING_SHAPE_MISMATCH: bound vector argument "
                            f"{argument_name!r} has annotation "
                            f"{parameter.get('annotation')!r}; required shape is an xyz "
                            "coordinate sequence; suite_repairable=false"
                        ),
                    }
                )
        for argument_name in component_argument_names:
            parameter = (
                parameter_index.get(str(argument_name))
                if isinstance(argument_name, str)
                else None
            )
            if parameter is not None and not _coordinate_scalar_annotation(
                parameter.get("annotation")
            ):
                issues.append(
                    {
                        "path": f"{prefix}.validation_binding",
                        "message": (
                            "COMPONENT_BINDING_TYPE_MISMATCH: coordinate component "
                            f"{argument_name!r} has annotation "
                            f"{parameter.get('annotation')!r}; expected required float"
                        ),
                    }
                )
        for argument_name in auxiliary_argument_names:
            parameter = (
                parameter_index.get(str(argument_name))
                if isinstance(argument_name, str)
                else None
            )
            if argument_name == "runtime" or parameter is None:
                issues.append(
                    {
                        "path": f"{prefix}.validation_binding",
                        "message": (
                            f"bound auxiliary argument {argument_name!r} is not an "
                            "existing non-runtime signature parameter"
                        ),
                    }
                )
    return issues


@dataclass(frozen=True)
class Stage1Freeze:
    artifact: Mapping[str, Any]
    sha256: str
    path: Path


@dataclass
class _GeneratedFileWriteTransaction:
    """In-memory authority for one append-only staged Python-file write."""

    transaction_id: str
    relative_path: str
    draft_path: Path
    expected_target_state: str
    expected_target_sha256: str | None
    draft_sha256: str = _EMPTY_SHA256
    bytes_written: int = 0
    chunk_count: int = 0


class GenerationWorkspace:
    """Only generation-visible inputs and generated output are reachable here."""

    def __init__(
        self,
        *,
        input_snapshot: str | Path,
        run_root: str | Path,
        stage1_schema: Mapping[str, Any] | None = None,
        package_manifest_schema: Mapping[str, Any] | None = None,
        max_text_file_bytes: int = 200_000,
        allow_orchestration_fixture_evidence: bool = False,
        simulation_sandbox: "GenerationSimulationSandbox | None" = None,
    ) -> None:
        unresolved_input_snapshot = Path(input_snapshot)
        if unresolved_input_snapshot.is_symlink():
            raise GenerationWorkspaceError(
                "input snapshot root must be a real directory, not a symlink"
            )
        self.input_snapshot = unresolved_input_snapshot.resolve()
        self.run_root = Path(run_root).resolve()
        if (
            self.run_root == self.input_snapshot
            or self.input_snapshot in self.run_root.parents
        ):
            raise GenerationWorkspaceError(
                "run_root must be outside the immutable input snapshot"
            )
        self.run_root.mkdir(parents=True, exist_ok=True)
        self._input_snapshot_records = _scan_input_snapshot(self.input_snapshot)
        manifest_files = [
            record.to_dict() for record in self._input_snapshot_records
        ]
        manifest_tree_sha256 = sha256_json({"files": manifest_files})
        self._input_snapshot_manifest = {
            "schema_version": "robot_capability.generation_input_snapshot_manifest.v1",
            "scope": "complete_generation_visible_input_snapshot",
            "file_count": len(manifest_files),
            "total_bytes": sum(record.bytes for record in self._input_snapshot_records),
            "files": manifest_files,
            "tree_sha256": manifest_tree_sha256,
            "integrity_policy": {
                "allowlist_is_exact": True,
                "regular_files_only": True,
                "symlinks_allowed": False,
                "verify_before_every_generation_tool_call": True,
                "verify_at_stage_and_repair_continuity_boundaries": True,
            },
            "privacy_contract": {
                "generation_visible_public_inputs_only": True,
                "file_content_embedded": False,
                "private_tasks_embedded": False,
                "oracle_or_validation_embedded": False,
            },
        }
        self.input_snapshot_manifest_path = (
            self.run_root / _INPUT_SNAPSHOT_MANIFEST_NAME
        )
        if (
            self.input_snapshot_manifest_path == self.input_snapshot
            or self.input_snapshot
            in self.input_snapshot_manifest_path.parents
        ):
            raise GenerationWorkspaceError(
                "detached input manifest must be outside the input snapshot"
            )
        atomic_write_json(
            self.input_snapshot_manifest_path,
            self._input_snapshot_manifest,
        )
        self._input_snapshot_manifest_sha256 = sha256_file(
            self.input_snapshot_manifest_path
        )
        # The Agent's write tools are already path-allowlisted to generated
        # package files. Read-only filesystem mode adds a second, auditable
        # barrier for the detached manifest.
        self.input_snapshot_manifest_path.chmod(0o444)
        self.stage1_dir = self.run_root / "stage1"
        self.package_root = self.run_root / "generated_package"
        # Drafts deliberately live outside ``generated_package`` so a partial
        # model response can never become importable or enter validation.
        self.generated_write_staging_root = self.run_root / "generated_write_staging"
        self.stage1_dir.mkdir(parents=True, exist_ok=True)
        self.package_root.mkdir(parents=True, exist_ok=True)
        self.max_text_file_bytes = max_text_file_bytes
        self.allow_orchestration_fixture_evidence = bool(
            allow_orchestration_fixture_evidence
        )
        self.simulation_sandbox = simulation_sandbox
        if simulation_sandbox is not None and not simulation_sandbox.prepared:
            raise GenerationWorkspaceError(
                "Generation simulation sandbox must be prepared before workspace creation"
            )
        if stage1_schema is None:
            schema_path = Path(__file__).resolve().parents[2] / "schemas/stage1.schema.json"
            stage1_schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if not isinstance(stage1_schema, Mapping):
            raise TypeError("stage1_schema must be a JSON object")
        self.stage1_schema = copy.deepcopy(dict(stage1_schema))
        if package_manifest_schema is None:
            manifest_schema_path = (
                Path(__file__).resolve().parents[2]
                / "schemas/capability_manifest.schema.json"
            )
            package_manifest_schema = json.loads(
                manifest_schema_path.read_text(encoding="utf-8")
            )
        if not isinstance(package_manifest_schema, Mapping):
            raise TypeError("package_manifest_schema must be a JSON object")
        self.package_manifest_schema = copy.deepcopy(dict(package_manifest_schema))
        self._stage1_candidate: dict[str, Any] | None = None
        self._frozen: Stage1Freeze | None = None
        self._snapshot_read = False
        self._snapshot_paths: frozenset[str] = frozenset()
        self._stage1_review_completed = False
        self._stage1_review_valid = False
        self._stage1_review_canonical: str | None = None
        self._generated_file_transactions: dict[
            str, _GeneratedFileWriteTransaction
        ] = {}

    @property
    def input_snapshot_manifest_audit(self) -> dict[str, Any]:
        """Return non-content audit bindings for the detached input freeze."""

        return {
            "schema_version": self._input_snapshot_manifest["schema_version"],
            "path": self.input_snapshot_manifest_path.relative_to(
                self.run_root
            ).as_posix(),
            "manifest_sha256": self._input_snapshot_manifest_sha256,
            "tree_sha256": self._input_snapshot_manifest["tree_sha256"],
            "file_count": self._input_snapshot_manifest["file_count"],
            "total_bytes": self._input_snapshot_manifest["total_bytes"],
        }

    def assert_input_snapshot_unchanged(self) -> None:
        """Fail closed unless input paths, bytes, hashes, and types are frozen."""

        try:
            manifest_status = os.lstat(self.input_snapshot_manifest_path)
        except OSError as exc:
            raise GenerationWorkspaceError(
                "detached input snapshot manifest integrity violation"
            ) from exc
        if not stat.S_ISREG(manifest_status.st_mode):
            raise GenerationWorkspaceError(
                "detached input snapshot manifest integrity violation"
            )
        _, manifest_digest = _hash_regular_file_without_following_symlinks(
            self.input_snapshot_manifest_path
        )
        if manifest_digest != self._input_snapshot_manifest_sha256:
            raise GenerationWorkspaceError(
                "detached input snapshot manifest integrity violation"
            )

        current = _scan_input_snapshot(self.input_snapshot)
        if current == self._input_snapshot_records:
            return
        expected_by_path = {record.path: record for record in self._input_snapshot_records}
        current_by_path = {record.path: record for record in current}
        added = sorted(set(current_by_path) - set(expected_by_path))
        removed = sorted(set(expected_by_path) - set(current_by_path))
        modified = sorted(
            path
            for path in set(expected_by_path) & set(current_by_path)
            if expected_by_path[path] != current_by_path[path]
        )

        def bounded(values: list[str]) -> list[str]:
            return values[:8]

        raise GenerationWorkspaceError(
            "input snapshot integrity violation: frozen allowlist changed; "
            f"added={bounded(added)!r}, removed={bounded(removed)!r}, "
            f"modified={bounded(modified)!r}"
        )

    def guard_generation_tool_handler(
        self,
        handler: Callable[[dict[str, Any]], Any],
    ) -> Callable[[dict[str, Any]], Any]:
        """Wrap one Agent tool with the immutable-input continuity gate."""

        if getattr(handler, "_generation_snapshot_guard", None) is self:
            return handler

        @wraps(handler)
        def guarded(arguments: dict[str, Any]) -> Any:
            self.assert_input_snapshot_unchanged()
            return handler(arguments)

        guarded._generation_snapshot_guard = self  # type: ignore[attr-defined]
        return guarded

    @property
    def frozen_stage1(self) -> Stage1Freeze:
        if self._frozen is None:
            raise GenerationWorkspaceError("Stage 1 has not been frozen")
        return Stage1Freeze(
            artifact=copy.deepcopy(self._frozen.artifact),
            sha256=self._frozen.sha256,
            path=self._frozen.path,
        )

    def read_generation_snapshot(self, _: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Return all safe text facts in one call so Stage 1 fits its 3-call budget."""

        del _
        self.assert_input_snapshot_unchanged()
        files: dict[str, Any] = {}
        for frozen_record in self._input_snapshot_records:
            path = self.input_snapshot / frozen_record.path
            record: dict[str, Any] = {
                "bytes": frozen_record.bytes,
                "sha256": frozen_record.sha256,
            }
            if (
                frozen_record.bytes <= self.max_text_file_bytes
                and _is_probably_text(path)
            ):
                content = _read_frozen_regular_file(path, frozen_record)
                try:
                    record["text"] = content.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise GenerationWorkspaceError(
                        "generation-visible text input must be valid UTF-8"
                    ) from exc
            else:
                record["content_omitted"] = "binary_or_large"
            files[frozen_record.path] = record
        context = {
            "schema_version": "robot_capability.generation_context.v1",
            "files": files,
            "input_snapshot_freeze": self.input_snapshot_manifest_audit,
            "isolation": {
                "private_tasks_visible": False,
                "oracle_visible": False,
                "validation_visible": False,
                "predecessor_answers_visible": False,
            },
        }
        if self.simulation_sandbox is not None:
            # Keep the exact Stage 1 three-turn protocol: the first snapshot
            # observation also carries the resolver-frozen public scene probe.
            context["public_simulation_environment"] = (
                self.simulation_sandbox.inspect_environment()
            )
        self._snapshot_read = True
        self._snapshot_paths = frozenset(files)
        return context

    def assert_generation_environment_ready(self) -> None:
        self.assert_input_snapshot_unchanged()
        if self.simulation_sandbox is not None:
            self.simulation_sandbox.assert_ready()

    def probe_generated_capability(
        self, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        if self.simulation_sandbox is None:
            raise GenerationWorkspaceError(
                "no public Generation simulation sandbox is configured"
            )
        payload = dict(arguments)
        payload["frozen_stage1"] = self.frozen_stage1.artifact
        return self.simulation_sandbox.probe_generated_capability(payload)

    def simulation_continuity_checkpoint(self) -> dict[str, Any] | None:
        self.assert_input_snapshot_unchanged()
        if self.simulation_sandbox is None:
            return None
        return self.simulation_sandbox.continuity_checkpoint()

    def _validate_stage1_artifact(
        self,
        artifact: Any,
    ) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
        """Apply the authoritative Stage 1 validator and normalize valid JSON."""

        try:
            validated = validate_stage1(artifact)
        except ArtifactSchemaError as exc:
            return None, [
                {"path": item.path, "message": item.message} for item in exc.issues
            ]
        normalized = json.loads(
            json.dumps(validated, ensure_ascii=False, allow_nan=False)
        )
        issues: list[dict[str, str]] = []
        production_snapshot = "morphology/snapshot.json" in self._snapshot_paths
        for layer_name, layer in normalized.get("layers", {}).items():
            for index, capability in enumerate(layer.get("capabilities", [])):
                evidence = capability.get("implementation_evidence", {})
                basis = evidence.get("basis")
                evidence_path = (
                    f"$.layers.{layer_name}.capabilities[{index}]"
                    ".implementation_evidence"
                )
                if (
                    basis == "orchestration_fixture"
                    and not self.allow_orchestration_fixture_evidence
                ):
                    issues.append(
                        {
                            "path": f"{evidence_path}.basis",
                            "message": (
                                "orchestration_fixture evidence is forbidden in a "
                                "physical Generation run"
                            ),
                        }
                    )
                if production_snapshot and basis != "orchestration_fixture":
                    for ref_index, ref in enumerate(evidence.get("refs", [])):
                        if ref not in self._snapshot_paths:
                            issues.append(
                                {
                                    "path": f"{evidence_path}.refs[{ref_index}]",
                                    "message": (
                                        "implementation evidence must name an exact "
                                        "generation-snapshot file"
                                    ),
                                }
                            )
        if issues:
            return None, issues
        return normalized, []

    def review_stage1(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Validate a Stage 1 proposal without accepting or writing it."""

        if self._frozen is not None:
            raise GenerationWorkspaceError("frozen Stage 1 cannot be changed")
        if not self._snapshot_read:
            return {
                "reviewed": False,
                "valid": False,
                "issues": [
                    {
                        "path": "$",
                        "message": (
                            "read_generation_snapshot must succeed before Stage 1 review"
                        ),
                    }
                ],
            }
        artifact, issues = self._validate_stage1_artifact(arguments.get("artifact"))
        self._stage1_review_completed = True
        self._stage1_review_valid = artifact is not None
        self._stage1_review_canonical = (
            None if artifact is None else canonical_json(artifact)
        )
        if artifact is None:
            return {"reviewed": True, "valid": False, "issues": issues}
        return {
            "reviewed": True,
            "valid": True,
            "issues": [],
            "sha256": sha256_json(artifact),
        }

    def submit_stage1(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self._frozen is not None:
            raise GenerationWorkspaceError("frozen Stage 1 cannot be changed")
        if not self._snapshot_read:
            return {
                "accepted": False,
                "issues": [
                    {
                        "path": "$",
                        "message": "read_generation_snapshot must succeed before Stage 1 submission",
                    }
                ],
            }
        if not self._stage1_review_completed:
            return {
                "accepted": False,
                "issues": [
                    {
                        "path": "$",
                        "message": (
                            "review_stage1 must complete before Stage 1 submission"
                        ),
                    }
                ],
            }
        artifact, issues = self._validate_stage1_artifact(arguments.get("artifact"))
        if artifact is None:
            return {"accepted": False, "issues": issues}
        canonical = canonical_json(artifact)
        if (
            self._stage1_review_valid
            and canonical != self._stage1_review_canonical
        ):
            return {
                "accepted": False,
                "issues": [
                    {
                        "path": "$.artifact",
                        "message": (
                            "a valid reviewed Stage 1 artifact must be submitted with "
                            "canonically identical JSON content"
                        ),
                    }
                ],
            }
        self._stage1_candidate = artifact
        candidate_path = self.stage1_dir / "candidate.json"
        atomic_write_json(candidate_path, self._stage1_candidate)
        return {"accepted": True, "sha256": sha256_json(self._stage1_candidate)}

    def freeze_stage1(self) -> Stage1Freeze:
        self.assert_input_snapshot_unchanged()
        if self._frozen is not None:
            return self._frozen
        if self._stage1_candidate is None:
            raise GenerationWorkspaceError("Agent finished Stage 1 without an accepted artifact")
        artifact = json.loads(json.dumps(self._stage1_candidate, ensure_ascii=False, sort_keys=True))
        path = self.stage1_dir / "stage1_capabilities.json"
        atomic_write_json(path, artifact)
        digest = sha256_json(artifact)
        atomic_write_json(
            self.stage1_dir / "freeze.json",
            {
                "schema_version": "robot_capability.stage1_freeze.v1",
                "artifact_sha256": digest,
            },
        )
        path.chmod(0o444)
        self._frozen = Stage1Freeze(artifact, digest, path)
        return self.frozen_stage1

    def read_frozen_stage1(self, _: Mapping[str, Any] | None = None) -> dict[str, Any]:
        del _
        self.assert_input_snapshot_unchanged()
        frozen = self.frozen_stage1
        return {"artifact": frozen.artifact, "sha256": frozen.sha256}

    def repair_context_checkpoint(
        self,
        *,
        repair_round: int,
        package_tree_digest: str,
    ) -> dict[str, Any]:
        """Build a deterministic, generation-visible Repair continuity record.

        The checkpoint carries immutable public semantics and hashes of current
        source-of-truth files.  It intentionally contains no validation suite,
        oracle, held-out task, hidden model reasoning, or generated-file content;
        the attached repair ledger carries only bounded observable prior tool
        actions, and the Agent can re-read a relevant current module with its
        existing tool.
        """

        if (
            isinstance(repair_round, bool)
            or not isinstance(repair_round, int)
            or repair_round < 1
        ):
            raise ValueError("repair_round must be a positive integer")
        if not isinstance(package_tree_digest, str) or not package_tree_digest:
            raise ValueError("package_tree_digest must be a non-empty string")

        self.assert_input_snapshot_unchanged()
        generation_inputs = [
            record.to_dict() for record in self._input_snapshot_records
        ]

        checkpoint = {
            "schema_version": "robot_capability.repair_context_checkpoint.v1",
            "repair_round": repair_round,
            "frozen_stage1": self.read_frozen_stage1(),
            "generated_package": {
                "tree_sha256": package_tree_digest,
                "files": self.list_generated_files()["files"],
            },
            "generation_input_index": generation_inputs,
            "generation_input_freeze": self.input_snapshot_manifest_audit,
            "continuity_constraints": {
                "frozen_stage1_must_not_change": True,
                "current_generated_files_are_source_of_truth": True,
                "read_relevant_current_module_before_editing": True,
                "prior_transcript_is_preserved_in_append_only_history_and_trace": True,
                "prior_observable_repair_process_is_carried_in_ledger": True,
                "hidden_model_reasoning_is_not_carried_or_exported": True,
            },
            "isolation": {
                "private_tasks_visible": False,
                "oracle_visible": False,
                "validation_suite_visible": False,
                "predecessor_answers_visible": False,
            },
        }
        simulation_checkpoint = self.simulation_continuity_checkpoint()
        if simulation_checkpoint is not None:
            checkpoint["public_generation_simulation"] = simulation_checkpoint
        return checkpoint

    @staticmethod
    def _is_sha256(value: Any) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    def _generated_python_target(self, relative: Any) -> tuple[str, Path]:
        if not isinstance(relative, str) or relative not in ALLOWED_GENERATED_PYTHON_FILES:
            raise GenerationWorkspaceError(
                f"path is outside the generated-Python allowlist: {relative!r}"
            )
        unresolved_target = self.package_root / relative
        if unresolved_target.is_symlink():
            raise GenerationWorkspaceError("generated path must not be a symlink")
        target = unresolved_target.resolve()
        if self.package_root != target and self.package_root not in target.parents:
            raise GenerationWorkspaceError("generated path escaped package root")
        return relative, target

    @staticmethod
    def _utf8_bytes(content: Any, *, label: str) -> bytes:
        if not isinstance(content, str):
            raise GenerationWorkspaceError(f"{label} must be text")
        try:
            return content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise GenerationWorkspaceError(f"{label} must be valid UTF-8 text") from exc

    def _assert_target_precondition(
        self,
        *,
        target: Path,
        state: str,
        sha256: str | None,
    ) -> None:
        if target.exists() and not target.is_file():
            raise GenerationWorkspaceError("generated target must be absent or a regular file")
        if state == "absent":
            if target.exists():
                raise GenerationWorkspaceError(
                    "generated target precondition failed: expected absent"
                )
            return
        if state != "present" or sha256 is None:
            raise GenerationWorkspaceError("invalid generated target precondition")
        if not target.is_file():
            raise GenerationWorkspaceError(
                "generated target precondition failed: expected present"
            )
        if sha256_file(target) != sha256:
            raise GenerationWorkspaceError(
                "generated target precondition failed: SHA-256 changed"
            )

    def begin_generated_file_write(
        self, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Open an append-only draft bound to the target's current state."""

        relative, target = self._generated_python_target(arguments.get("path"))
        expected_target = arguments.get("expected_target")
        if not isinstance(expected_target, Mapping):
            raise GenerationWorkspaceError("expected_target must be an object")
        state = expected_target.get("state")
        expected_sha256 = expected_target.get("sha256")
        if state == "absent":
            if set(expected_target) != {"state"}:
                raise GenerationWorkspaceError(
                    "absent expected_target accepts only the state field"
                )
            expected_sha256 = None
        elif state == "present":
            if set(expected_target) != {"state", "sha256"} or not self._is_sha256(
                expected_sha256
            ):
                raise GenerationWorkspaceError(
                    "present expected_target requires one lowercase SHA-256"
                )
        else:
            raise GenerationWorkspaceError(
                "expected_target.state must be 'absent' or 'present'"
            )
        self._assert_target_precondition(
            target=target,
            state=state,
            sha256=expected_sha256,
        )
        if any(
            transaction.relative_path == relative
            for transaction in self._generated_file_transactions.values()
        ):
            raise GenerationWorkspaceError(
                "an open generated-file transaction already owns this path"
            )

        self.generated_write_staging_root.mkdir(parents=True, exist_ok=True)
        while True:
            transaction_id = uuid.uuid4().hex
            draft_path = self.generated_write_staging_root / f"{transaction_id}.draft"
            if (
                transaction_id not in self._generated_file_transactions
                and not draft_path.exists()
            ):
                break
        _atomic_write_text(draft_path, "")
        transaction = _GeneratedFileWriteTransaction(
            transaction_id=transaction_id,
            relative_path=relative,
            draft_path=draft_path,
            expected_target_state=state,
            expected_target_sha256=expected_sha256,
        )
        self._generated_file_transactions[transaction_id] = transaction
        return {
            "transaction_id": transaction_id,
            "path": relative,
            "draft_sha256": _EMPTY_SHA256,
            "bytes": 0,
            "chunk_count": 0,
            "next_chunk_index": 0,
        }

    def _generated_file_transaction(
        self, transaction_id: Any
    ) -> _GeneratedFileWriteTransaction:
        if (
            not isinstance(transaction_id, str)
            or len(transaction_id) != 32
            or any(character not in "0123456789abcdef" for character in transaction_id)
        ):
            raise GenerationWorkspaceError(
                "transaction_id must be an opaque lowercase 32-hex identifier"
            )
        transaction = self._generated_file_transactions.get(transaction_id)
        if transaction is None:
            raise GenerationWorkspaceError("generated-file transaction is not open")
        if transaction.draft_path.is_symlink() or not transaction.draft_path.is_file():
            raise GenerationWorkspaceError("generated-file transaction draft is invalid")
        resolved_draft = transaction.draft_path.resolve()
        staging_root = self.generated_write_staging_root.resolve()
        if resolved_draft.parent != staging_root:
            raise GenerationWorkspaceError("generated-file transaction draft escaped staging")
        if sha256_file(resolved_draft) != transaction.draft_sha256:
            raise GenerationWorkspaceError("generated-file transaction draft was modified")
        return transaction

    def append_generated_file_chunk(
        self, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Append one bounded UTF-8 chunk using an index and digest chain."""

        transaction = self._generated_file_transaction(arguments.get("transaction_id"))
        chunk_index = arguments.get("chunk_index")
        expected_draft_sha256 = arguments.get("expected_draft_sha256")
        if (
            isinstance(chunk_index, bool)
            or not isinstance(chunk_index, int)
            or chunk_index != transaction.chunk_count
        ):
            raise GenerationWorkspaceError(
                f"chunk_index must equal next index {transaction.chunk_count}"
            )
        if (
            not self._is_sha256(expected_draft_sha256)
            or expected_draft_sha256 != transaction.draft_sha256
        ):
            raise GenerationWorkspaceError("draft hash chain precondition failed")
        chunk_bytes = self._utf8_bytes(arguments.get("content"), label="chunk content")
        if not chunk_bytes:
            raise GenerationWorkspaceError("chunk content must be non-empty")
        if len(chunk_bytes) > MAX_GENERATED_CHUNK_BYTES:
            raise GenerationWorkspaceError(
                f"chunk exceeds {MAX_GENERATED_CHUNK_BYTES} UTF-8 bytes"
            )
        appended = self._append_prevalidated_generated_chunks(
            transaction=transaction,
            start_chunk_index=chunk_index,
            chunks=[chunk_bytes],
        )
        return {
            **appended,
            "accepted_chunk_index": chunk_index,
            "chunk_sha256": sha256_bytes(chunk_bytes),
        }

    def _append_prevalidated_generated_chunks(
        self,
        *,
        transaction: _GeneratedFileWriteTransaction,
        start_chunk_index: int,
        chunks: list[bytes],
    ) -> dict[str, Any]:
        """Perform the one atomic draft replacement shared by append tools.

        Callers validate the index/hash chain and every logical chunk first.
        The next digest is calculated from complete in-memory bytes before the
        atomic replacement. After the replacement succeeds, only in-memory
        assignments remain, so a post-write hash read cannot split disk and
        transaction state.
        """

        batch_bytes = sum(len(chunk) for chunk in chunks)
        next_bytes = transaction.bytes_written + batch_bytes
        if next_bytes > MAX_GENERATED_TEXT_BYTES:
            raise GenerationWorkspaceError("generated file exceeds the P0 size limit")

        current_bytes = transaction.draft_path.read_bytes()
        if (
            len(current_bytes) != transaction.bytes_written
            or sha256_bytes(current_bytes) != transaction.draft_sha256
        ):
            raise GenerationWorkspaceError(
                "generated-file transaction draft changed before append"
            )
        next_content_bytes = current_bytes + b"".join(chunks)
        try:
            next_content = next_content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            # Existing draft integrity is normally guaranteed by its hash and
            # all authored chunks are encoded from text. Keep this fail-closed
            # guard at the final shared write boundary nonetheless.
            raise GenerationWorkspaceError(
                "generated-file transaction draft is not valid UTF-8"
            ) from exc
        next_draft_sha256 = sha256_bytes(next_content_bytes)
        accepted_chunks = [
            {
                "chunk_index": start_chunk_index + offset,
                "bytes": len(chunk),
                "sha256": sha256_bytes(chunk),
            }
            for offset, chunk in enumerate(chunks)
        ]

        _atomic_write_text(transaction.draft_path, next_content)
        transaction.bytes_written = next_bytes
        transaction.chunk_count += len(chunks)
        transaction.draft_sha256 = next_draft_sha256
        return {
            "transaction_id": transaction.transaction_id,
            "path": transaction.relative_path,
            "accepted_chunks": accepted_chunks,
            "draft_sha256": next_draft_sha256,
            "bytes": next_bytes,
            "chunk_count": transaction.chunk_count,
            "next_chunk_index": transaction.chunk_count,
        }

    def append_generated_file_chunks(
        self, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Atomically append one to three prevalidated UTF-8 chunks.

        Every chunk is validated before the draft is read or replaced.  Thus a
        bad later chunk cannot partially append an earlier chunk from the same
        tool call, while each logical chunk still advances the transaction's
        digest-chain index and final chunk count.
        """

        transaction = self._generated_file_transaction(arguments.get("transaction_id"))
        start_chunk_index = arguments.get("start_chunk_index")
        expected_draft_sha256 = arguments.get("expected_draft_sha256")
        if (
            isinstance(start_chunk_index, bool)
            or not isinstance(start_chunk_index, int)
            or start_chunk_index != transaction.chunk_count
        ):
            raise GenerationWorkspaceError(
                f"start_chunk_index must equal next index {transaction.chunk_count}"
            )
        if (
            not self._is_sha256(expected_draft_sha256)
            or expected_draft_sha256 != transaction.draft_sha256
        ):
            raise GenerationWorkspaceError("draft hash chain precondition failed")

        chunks = arguments.get("chunks")
        if not isinstance(chunks, list):
            raise GenerationWorkspaceError("chunks must be an array")
        if not 1 <= len(chunks) <= MAX_GENERATED_CHUNKS_PER_APPEND:
            raise GenerationWorkspaceError(
                "chunks must contain between 1 and "
                f"{MAX_GENERATED_CHUNKS_PER_APPEND} items"
            )

        # Do not mutate the draft or transaction until the complete batch has
        # passed every validation, including a bad second/third chunk.
        chunk_bytes: list[bytes] = []
        for offset, content in enumerate(chunks):
            encoded = self._utf8_bytes(content, label=f"chunk {offset} content")
            if not encoded:
                raise GenerationWorkspaceError(
                    f"chunk {offset} content must be non-empty"
                )
            if len(encoded) > MAX_GENERATED_CHUNK_BYTES:
                raise GenerationWorkspaceError(
                    f"chunk {offset} exceeds {MAX_GENERATED_CHUNK_BYTES} UTF-8 bytes"
                )
            chunk_bytes.append(encoded)

        appended = self._append_prevalidated_generated_chunks(
            transaction=transaction,
            start_chunk_index=start_chunk_index,
            chunks=chunk_bytes,
        )
        return {
            **appended,
            "accepted_start_chunk_index": start_chunk_index,
            "accepted_chunk_count": len(chunk_bytes),
        }

    def commit_generated_file_write(
        self, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Parse and atomically publish a complete staged Python module."""

        transaction = self._generated_file_transaction(arguments.get("transaction_id"))
        expected_draft_sha256 = arguments.get("expected_draft_sha256")
        expected_chunk_count = arguments.get("expected_chunk_count")
        expected_bytes = arguments.get("expected_bytes")
        if (
            not self._is_sha256(expected_draft_sha256)
            or expected_draft_sha256 != transaction.draft_sha256
        ):
            raise GenerationWorkspaceError("commit draft SHA-256 precondition failed")
        if (
            isinstance(expected_chunk_count, bool)
            or not isinstance(expected_chunk_count, int)
            or expected_chunk_count != transaction.chunk_count
        ):
            raise GenerationWorkspaceError("commit chunk-count precondition failed")
        if (
            isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes != transaction.bytes_written
        ):
            raise GenerationWorkspaceError("commit byte-count precondition failed")
        if transaction.chunk_count < 1 or transaction.bytes_written < 1:
            raise GenerationWorkspaceError("cannot commit an empty generated Python file")

        source = transaction.draft_path.read_text(encoding="utf-8")
        try:
            ast.parse(source, filename=transaction.relative_path)
        except SyntaxError as exc:
            raise GenerationWorkspaceError(
                f"staged Python failed AST parsing: {exc}"
            ) from exc
        _relative, target = self._generated_python_target(transaction.relative_path)
        # The base is checked again only after all draft/content checks, as
        # close as practical to the atomic replacement.
        self._assert_target_precondition(
            target=target,
            state=transaction.expected_target_state,
            sha256=transaction.expected_target_sha256,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(transaction.draft_path, target)
        del self._generated_file_transactions[transaction.transaction_id]
        return {
            "transaction_id": transaction.transaction_id,
            "path": transaction.relative_path,
            "bytes": target.stat().st_size,
            "chunk_count": transaction.chunk_count,
            "sha256": sha256_file(target),
            "committed": True,
        }

    def abort_generated_file_write(
        self, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Discard one draft only when its latest digest is acknowledged."""

        transaction = self._generated_file_transaction(arguments.get("transaction_id"))
        expected_draft_sha256 = arguments.get("expected_draft_sha256")
        if (
            not self._is_sha256(expected_draft_sha256)
            or expected_draft_sha256 != transaction.draft_sha256
        ):
            raise GenerationWorkspaceError("abort draft SHA-256 precondition failed")
        transaction.draft_path.unlink()
        del self._generated_file_transactions[transaction.transaction_id]
        return {
            "transaction_id": transaction.transaction_id,
            "path": transaction.relative_path,
            "draft_sha256": transaction.draft_sha256,
            "aborted": True,
        }

    def abort_all_generated_file_writes(self) -> list[dict[str, Any]]:
        """Best-effort terminal cleanup for abandoned Stage 2 transactions."""

        aborted: list[dict[str, Any]] = []
        for transaction_id in tuple(self._generated_file_transactions):
            transaction = self._generated_file_transactions.pop(transaction_id)
            try:
                if (
                    transaction.draft_path.exists()
                    or transaction.draft_path.is_symlink()
                ):
                    transaction.draft_path.unlink()
            except OSError:
                # Terminal cleanup must not hide the original provider/budget
                # failure. The staging directory is never executable or part
                # of the generated-package tree.
                pass
            finally:
                aborted.append(
                    {
                        "transaction_id": transaction_id,
                        "path": transaction.relative_path,
                        "draft_sha256": transaction.draft_sha256,
                    }
                )
        return aborted

    def write_generated_file(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        relative = arguments.get("path")
        content = arguments.get("content")
        if not isinstance(relative, str) or relative not in ALLOWED_GENERATED_FILES:
            raise GenerationWorkspaceError(
                f"path is outside the generated-package allowlist: {relative!r}"
            )
        if relative in ALLOWED_GENERATED_PYTHON_FILES and any(
            transaction.relative_path == relative
            for transaction in self._generated_file_transactions.values()
        ):
            raise GenerationWorkspaceError(
                "an open generated-file transaction already owns this path"
            )
        content_bytes = self._utf8_bytes(content, label="generated file content")
        content_limit = (
            MAX_DIRECT_GENERATED_PYTHON_BYTES
            if relative in ALLOWED_GENERATED_PYTHON_FILES
            else MAX_GENERATED_TEXT_BYTES
        )
        if len(content_bytes) > content_limit:
            if relative in ALLOWED_GENERATED_PYTHON_FILES:
                raise GenerationWorkspaceError(
                    "direct generated Python write exceeds the 8192-byte limit; "
                    "use the staged chunk transaction"
                )
            raise GenerationWorkspaceError("generated file exceeds the P0 size limit")
        unresolved_target = self.package_root / relative
        if unresolved_target.is_symlink():
            raise GenerationWorkspaceError("generated path must not be a symlink")
        target = unresolved_target.resolve()
        if self.package_root != target and self.package_root not in target.parents:
            raise GenerationWorkspaceError("generated path escaped package root")
        _atomic_write_text(target, content)
        return {"path": relative, "bytes": target.stat().st_size, "sha256": sha256_file(target)}

    def replace_generated_file_text(
        self, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Apply one hash-guarded, unique text replacement to generated Python.

        This is an incremental correction operation, not a direct whole-file model
        submission.  The resulting module may therefore exceed the direct
        8192-byte write limit, while remaining subject to the package-wide P0
        size limit, Python parsing, target ownership, and atomic publication.
        """

        relative = arguments.get("path")
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        expected_sha256 = arguments.get("expected_sha256")
        if (
            not isinstance(relative, str)
            or relative not in ALLOWED_GENERATED_PYTHON_FILES
        ):
            raise GenerationWorkspaceError(
                f"repair path is outside the generated-Python allowlist: {relative!r}"
            )
        if not isinstance(old_text, str) or not old_text:
            raise GenerationWorkspaceError("old_text must be non-empty text")
        if not isinstance(new_text, str):
            raise GenerationWorkspaceError("new_text must be text")
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            raise GenerationWorkspaceError(
                "expected_sha256 must be a lowercase 64-character SHA-256 digest"
            )

        relative, target = self._generated_python_target(relative)
        if any(
            transaction.relative_path == relative
            for transaction in self._generated_file_transactions.values()
        ):
            raise GenerationWorkspaceError(
                "an open generated-file transaction already owns this repair path"
            )
        if not target.is_file():
            raise GenerationWorkspaceError(f"generated file does not exist: {relative}")
        before_sha256 = sha256_file(target)
        if before_sha256 != expected_sha256:
            raise GenerationWorkspaceError(
                "generated file hash no longer matches expected_sha256"
            )
        current = target.read_text(encoding="utf-8")
        current_content = self._utf8_bytes(
            current, label="current generated Python"
        )
        first_occurrence = current.find(old_text)
        second_occurrence = (
            current.find(old_text, first_occurrence + 1)
            if first_occurrence >= 0
            else -1
        )
        if first_occurrence < 0 or second_occurrence >= 0:
            occurrence_count = 0 if first_occurrence < 0 else "more than one"
            raise GenerationWorkspaceError(
                "old_text must occur exactly once in the current generated file; "
                f"found {occurrence_count} occurrences"
            )
        replacement = (
            current[:first_occurrence]
            + new_text
            + current[first_occurrence + len(old_text) :]
        )
        replacement_content = self._utf8_bytes(
            replacement, label="replacement generated Python"
        )
        if not replacement_content:
            raise GenerationWorkspaceError("replacement must leave a non-empty file")
        if len(replacement_content) > MAX_GENERATED_TEXT_BYTES:
            raise GenerationWorkspaceError("generated file exceeds the P0 size limit")
        try:
            ast.parse(replacement, filename=relative)
        except SyntaxError as exc:
            raise GenerationWorkspaceError(
                f"replacement Python failed AST parsing: {exc}"
            ) from exc

        before_bytes = len(current_content)
        # Recheck the optimistic concurrency guard after all content checks and
        # immediately before the same atomic publication primitive used by
        # direct writes.  An exact edit must never overwrite a newer repair.
        self._assert_target_precondition(
            target=target,
            state="present",
            sha256=before_sha256,
        )
        _atomic_write_text(target, replacement)
        return {
            "path": relative,
            "before_sha256": before_sha256,
            "after_sha256": sha256_file(target),
            "before_bytes": before_bytes,
            "after_bytes": len(replacement_content),
        }

    def write_package_manifest(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Write a parsed, schema-valid manifest bound to frozen Stage 1."""

        manifest = arguments.get("manifest")
        issues = validate_json_schema(
            manifest,
            self.frozen_package_manifest_schema(),
            instance_path="$.manifest",
        )
        if issues:
            return {
                "accepted": False,
                "issues": [
                    {"path": issue.path, "message": issue.message}
                    for issue in issues
                ],
            }
        assert isinstance(manifest, Mapping)
        semantic_issues = _manifest_binding_semantic_issues(manifest)
        if semantic_issues:
            return {"accepted": False, "issues": semantic_issues}
        target = self.package_root / "package_manifest.json"
        atomic_write_json(target, dict(manifest))
        return {
            "accepted": True,
            "path": "package_manifest.json",
            "bytes": target.stat().st_size,
            "sha256": sha256_file(target),
        }

    def frozen_package_manifest_schema(self) -> dict[str, Any]:
        schema = copy.deepcopy(self.package_manifest_schema)
        properties = schema.setdefault("properties", {})
        properties["stage1_sha256"] = {"const": self.frozen_stage1.sha256}
        capabilities_schema = properties.get("capabilities")
        frozen_capabilities: list[dict[str, str]] = []
        layers = self.frozen_stage1.artifact.get("layers", {})
        if isinstance(layers, Mapping):
            for granularity in ("G1", "G2", "G3"):
                layer = layers.get(granularity, {})
                items = layer.get("capabilities", []) if isinstance(layer, Mapping) else []
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, Mapping):
                        continue
                    frozen_capabilities.append(
                        {
                            "capability_id": str(item.get("capability_id", "")),
                            "granularity": granularity,
                            "module": granularity.lower(),
                            "function_name": str(item.get("function_name", "")),
                            "validation_effect": str(item.get("validation_effect", "")),
                        }
                    )
        if isinstance(capabilities_schema, dict):
            capabilities_schema["minItems"] = len(frozen_capabilities)
            capabilities_schema["maxItems"] = len(frozen_capabilities)
        schema.setdefault("allOf", []).extend(
            {
                "properties": {
                    "capabilities": {
                        "contains": {
                            "type": "object",
                            "required": [
                                "capability_id",
                                "granularity",
                                "module",
                                "function_name",
                                "validation_binding",
                            ],
                            "properties": {
                                "capability_id": {"const": item["capability_id"]},
                                "granularity": {"const": item["granularity"]},
                                "module": {"const": item["module"]},
                                "function_name": {"const": item["function_name"]},
                                "validation_binding": {
                                    "type": "object",
                                    "required": ["effect"],
                                    "properties": {
                                        "effect": {"const": item["validation_effect"]}
                                    },
                                },
                            },
                        },
                        "minContains": 1,
                        "maxContains": 1,
                    }
                }
            }
            for item in frozen_capabilities
        )
        return schema

    def read_generated_file(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        relative = arguments.get("path")
        if not isinstance(relative, str) or relative not in ALLOWED_GENERATED_FILES:
            raise GenerationWorkspaceError("generated path is not allowed")
        target = self.package_root / relative
        if not target.is_file():
            raise GenerationWorkspaceError(f"generated file does not exist: {relative}")
        return {"path": relative, "content": target.read_text(encoding="utf-8")}

    def search_generated_file_text(
        self, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Return bounded line contexts and the current hash for repair.

        This bounded read avoids injecting an entire large generated module
        when the failure already names a status or phase literal.  Search is
        literal, Python-allowlisted, read-only, and deliberately bounded.
        """

        relative, target = self._generated_python_target(arguments.get("path"))
        query = arguments.get("query")
        context_lines = arguments.get("context_lines")
        if (
            not isinstance(query, str)
            or not query
            or len(query) > MAX_REPAIR_SEARCH_QUERY_CHARACTERS
        ):
            raise GenerationWorkspaceError(
                "repair search query must be 1..256 characters"
            )
        if (
            isinstance(context_lines, bool)
            or not isinstance(context_lines, int)
            or not 0 <= context_lines <= MAX_REPAIR_SEARCH_CONTEXT_LINES
        ):
            raise GenerationWorkspaceError(
                "repair search context_lines must be an integer in [0, 6]"
            )
        if not target.is_file():
            raise GenerationWorkspaceError(f"generated file does not exist: {relative}")

        content = target.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)
        matched_indices = [
            index for index, line in enumerate(lines) if query in line
        ]
        matches: list[dict[str, Any]] = []
        for index in matched_indices[:MAX_REPAIR_SEARCH_MATCHES]:
            start = max(0, index - context_lines)
            end = min(len(lines), index + context_lines + 1)
            snippet = "".join(lines[start:end])
            snippet_truncated = len(snippet) > MAX_REPAIR_SEARCH_SNIPPET_CHARACTERS
            if snippet_truncated:
                snippet = snippet[:MAX_REPAIR_SEARCH_SNIPPET_CHARACTERS]
            matches.append(
                {
                    "line_number": index + 1,
                    "start_line": start + 1,
                    "end_line": end,
                    "snippet": snippet,
                    "snippet_truncated": snippet_truncated,
                }
            )
        result: dict[str, Any] = {
            "path": relative,
            "sha256": sha256_file(target),
            "bytes": target.stat().st_size,
            "query": query,
            "line_match_count": len(matched_indices),
            "matches": matches,
            "matches_truncated": len(matched_indices) > len(matches),
        }
        if not matched_indices:
            result["zero_match_next_step"] = (
                "Do not guess another source literal. For a direct failure, call "
                "read_generated_python_symbol with the real function suffix from "
                "failure.capability_id (for example G3.lift_object -> lift_object)."
            )
        return result

    def read_generated_python_symbol(
        self, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Read one complete top-level function with a strict source-size bound.

        Direct-validation feedback identifies the frozen capability function, so
        repair should inspect that real function body instead of spending its
        six-call budget guessing implementation literals.  This remains a
        read-only operation over the generated-Python allowlist and is also
        available for late Stage 2 static failures, where an authoritative line
        number is resolved to its enclosing top-level function by the AST.
        """

        relative, target = self._generated_python_target(arguments.get("path"))
        has_symbol = "symbol" in arguments
        has_line_number = "line_number" in arguments
        if has_symbol == has_line_number:
            raise GenerationWorkspaceError(
                "bounded function read requires exactly one of symbol or line_number"
            )
        symbol = arguments.get("symbol")
        line_number = arguments.get("line_number")
        if has_symbol and (
            not isinstance(symbol, str)
            or not 1 <= len(symbol) <= 128
            or not symbol.isascii()
            or not symbol.isidentifier()
        ):
            raise GenerationWorkspaceError(
                "repair symbol must be a 1..128 character ASCII Python identifier"
            )
        if has_line_number and (
            isinstance(line_number, bool)
            or not isinstance(line_number, int)
            or line_number < 1
        ):
            raise GenerationWorkspaceError(
                "repair line_number must be a positive integer"
            )
        if not target.is_file():
            raise GenerationWorkspaceError(f"generated file does not exist: {relative}")

        content = target.read_text(encoding="utf-8")
        try:
            tree = ast.parse(content, filename=relative)
        except SyntaxError as exc:
            raise GenerationWorkspaceError(
                f"generated Python failed AST parsing: {exc}"
            ) from exc
        functions = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if has_symbol:
            matches = [node for node in functions if node.name == symbol]
        else:
            assert isinstance(line_number, int)
            matches = [
                node
                for node in functions
                if node.end_lineno is not None
                and node.lineno <= line_number <= node.end_lineno
            ]
        file_sha256 = sha256_file(target)
        base_result: dict[str, Any] = {
            "path": relative,
            "sha256": file_sha256,
            "bytes": target.stat().st_size,
            "symbol": matches[0].name if len(matches) == 1 else symbol,
            "symbol_match_count": len(matches),
        }
        if has_line_number:
            base_result["requested_line_number"] = line_number
        if len(matches) != 1:
            candidates = [node.name for node in functions]
            return {
                **base_result,
                "found": False,
                "available_top_level_functions": candidates[
                    :MAX_REPAIR_SYMBOL_CANDIDATES
                ],
                "available_functions_truncated": (
                    len(candidates) > MAX_REPAIR_SYMBOL_CANDIDATES
                ),
                "next_step": (
                    "Choose an exact available top-level function or a real "
                    "static-failure line number; do not guess source literals."
                ),
            }

        node = matches[0]
        decorator_lines = [
            decorator.lineno
            for decorator in node.decorator_list
            if hasattr(decorator, "lineno")
        ]
        start_line = min([node.lineno, *decorator_lines])
        end_line = node.end_lineno
        if end_line is None:
            raise GenerationWorkspaceError("generated function has no AST end line")
        lines = content.splitlines(keepends=True)
        source = "".join(lines[start_line - 1 : end_line])
        source_bytes = self._utf8_bytes(source, label="generated function source")
        if len(source_bytes) > MAX_REPAIR_SYMBOL_SOURCE_BYTES:
            raise GenerationWorkspaceError(
                "generated function source exceeds the 32768-byte bounded repair "
                "read; use short literal search from the real function identifiers"
            )
        return {
            **base_result,
            "found": True,
            "start_line": start_line,
            "end_line": end_line,
            "source_bytes": len(source_bytes),
            "source": source,
        }

    def list_generated_files(self, _: Mapping[str, Any] | None = None) -> dict[str, Any]:
        del _
        return {
            "files": [
                {
                    "path": relative,
                    "sha256": sha256_file(self.package_root / relative),
                }
                for relative in sorted(ALLOWED_GENERATED_FILES)
                if (self.package_root / relative).is_file()
            ]
        }

    def check_generated_syntax(self, _: Mapping[str, Any] | None = None) -> dict[str, Any]:
        del _
        errors: list[dict[str, Any]] = []
        for relative in sorted(ALLOWED_GENERATED_FILES):
            path = self.package_root / relative
            if not path.is_file():
                errors.append({"path": relative, "error": "missing"})
                continue
            try:
                if path.suffix == ".py":
                    ast.parse(path.read_text(encoding="utf-8"), filename=relative)
                else:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if relative == "package_manifest.json":
                        if not isinstance(value, dict):
                            errors.append({"path": relative, "error": "manifest must be a JSON object"})
                        else:
                            schema_issues = validate_json_schema(
                                value,
                                self.frozen_package_manifest_schema(),
                                instance_path="$manifest",
                            )
                            errors.extend(
                                {
                                    "path": relative,
                                    "error": f"{issue.path}: {issue.message}",
                                }
                                for issue in schema_issues
                            )
                            if "stage1_sha256" not in value:
                                similar = sorted(
                                    str(name)
                                    for name in value
                                    if "stage1" in str(name).lower()
                                )
                                errors.append(
                                    {
                                        "path": relative,
                                        "error": (
                                            "missing exact required field 'stage1_sha256'; "
                                            f"stage1-like fields found: {similar}"
                                        ),
                                    }
                                )
            except (SyntaxError, json.JSONDecodeError) as exc:
                errors.append({"path": relative, "error": str(exc)})
        return {"ok": not errors, "errors": errors}

    def finish_package(self, _: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self._generated_file_transactions:
            return {
                "ok": False,
                "complete": False,
                "errors": [
                    {
                        "code": "OPEN_GENERATED_FILE_TRANSACTION",
                        "message": (
                            "commit or abort every staged generated-file transaction "
                            "before finishing the package"
                        ),
                        "open_transaction_count": len(
                            self._generated_file_transactions
                        ),
                        "paths": sorted(
                            transaction.relative_path
                            for transaction in self._generated_file_transactions.values()
                        ),
                    }
                ],
                "static_validation": None,
            }
        syntax = self.check_generated_syntax(_)
        if not syntax["ok"]:
            syntax["complete"] = False
            syntax["static_validation"] = None
            return syntax
        static_report = validate_generated_package(
            self.package_root,
            self.frozen_stage1.artifact,
        ).to_dict()
        return {
            "ok": bool(static_report["passed"]),
            "complete": bool(static_report["passed"]),
            "errors": list(static_report["failures"]),
            "static_validation": static_report,
        }


def _is_probably_text(path: Path) -> bool:
    return path.suffix.lower() in {
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".xml",
        ".md",
        ".txt",
        ".toml",
        ".py",
        ".license",
    }


def _snapshot_guarded_tools(
    workspace: Any,
    tools: Mapping[str, ToolSpec],
) -> dict[str, ToolSpec]:
    """Apply the input-freeze gate to every real Generation tool handler."""

    guard = getattr(workspace, "guard_generation_tool_handler", None)
    if not callable(guard):
        # Tiny test-only workspaces may model just repair package operations.
        return dict(tools)
    return {
        name: ToolSpec(
            spec.name,
            spec.description,
            spec.input_schema,
            guard(spec.handler),
            timeout_s=spec.timeout_s,
        )
        for name, spec in tools.items()
    }


def stage1_tools(workspace: GenerationWorkspace) -> dict[str, ToolSpec]:
    tools = {
        "read_generation_snapshot": ToolSpec(
            "read_generation_snapshot",
            "Read the complete generation-visible morphology, SDK, task, and experience snapshot.",
            {"type": "object", "additionalProperties": False},
            workspace.read_generation_snapshot,
        ),
        "review_stage1": ToolSpec(
            "review_stage1",
            (
                "Validate one proposed G1/G2/G3 Stage 1 artifact without "
                "accepting it or writing files."
            ),
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["artifact"],
                # Keep the review boundary permissive so malformed proposals
                # reach the authoritative validator and produce repairable
                # issues. submit_stage1 exposes and enforces the full schema.
                "properties": {"artifact": {}},
            },
            workspace.review_stage1,
        ),
        "submit_stage1": ToolSpec(
            "submit_stage1",
            (
                "Validate and submit the reviewed G1/G2/G3 Stage 1 JSON artifact. "
                "A valid review permits only canonically identical content; a "
                "failed review permits one fully corrected artifact."
            ),
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["artifact"],
                "properties": {"artifact": workspace.stage1_schema},
            },
            workspace.submit_stage1,
        ),
    }
    return _snapshot_guarded_tools(workspace, tools)


def stage2_tools(workspace: GenerationWorkspace) -> dict[str, ToolSpec]:
    path_schema = {
        "type": "string",
        "enum": sorted(ALLOWED_GENERATED_PYTHON_FILES),
    }
    digest_schema = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    transaction_id_schema = {"type": "string", "pattern": "^[0-9a-f]{32}$"}
    expected_target_schema = {
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["state"],
                "properties": {"state": {"const": "absent"}},
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["state", "sha256"],
                "properties": {
                    "state": {"const": "present"},
                    "sha256": digest_schema,
                },
            },
        ]
    }
    tools = {
        "read_frozen_stage1": ToolSpec(
            "read_frozen_stage1",
            "Read the immutable Stage 1 artifact and hash.",
            {"type": "object", "additionalProperties": False},
            workspace.read_frozen_stage1,
        ),
        "read_generation_snapshot": ToolSpec(
            "read_generation_snapshot",
            "Re-read generation-visible input facts when implementation needs evidence.",
            {"type": "object", "additionalProperties": False},
            workspace.read_generation_snapshot,
        ),
        "write_generated_file": ToolSpec(
            "write_generated_file",
            (
                "Atomically create or replace one small allowlisted Python module "
                "up to 8192 UTF-8 bytes. Use the staged chunk transaction for a "
                "larger module and write_package_manifest for the JSON manifest."
            ),
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "content"],
                "properties": {
                    "path": path_schema,
                    "content": {
                        "type": "string",
                        "maxLength": MAX_DIRECT_GENERATED_PYTHON_BYTES,
                    },
                },
            },
            workspace.write_generated_file,
        ),
        "begin_generated_file_write": ToolSpec(
            "begin_generated_file_write",
            (
                "Begin an append-only Python draft outside generated_package, "
                "bound to an absent target or the SHA-256 of a present target."
            ),
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "expected_target"],
                "properties": {
                    "path": path_schema,
                    "expected_target": expected_target_schema,
                },
            },
            workspace.begin_generated_file_write,
        ),
        "append_generated_file_chunk": ToolSpec(
            "append_generated_file_chunk",
            (
                "Append one non-empty chunk. The hard rejection limit is 6144 "
                "UTF-8 bytes, but target 2500-3500 bytes so the JSON ReAct "
                "envelope stays below the provider output limit. Supply the "
                "returned next index and draft SHA-256 on every append."
            ),
            {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "transaction_id",
                    "chunk_index",
                    "expected_draft_sha256",
                    "content",
                ],
                "properties": {
                    "transaction_id": transaction_id_schema,
                    "chunk_index": {"type": "integer", "minimum": 0},
                    "expected_draft_sha256": digest_schema,
                    "content": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_GENERATED_CHUNK_BYTES,
                    },
                },
            },
            workspace.append_generated_file_chunk,
        ),
        "append_generated_file_chunks": ToolSpec(
            "append_generated_file_chunks",
            (
                "Atomically append one to three consecutive non-empty chunks. "
                "The hard rejection limit is 6144 UTF-8 bytes per chunk; target "
                "2500-3500 bytes per chunk and at most 9000 bytes for the whole "
                "batch so JSON escaping does not reach the provider output "
                "limit. Supply the returned next index and draft SHA-256. The "
                "whole batch is rejected without writing if any chunk is "
                "invalid. Returns ordered accepted_chunks with each logical "
                "index, byte count, and SHA-256."
            ),
            {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "transaction_id",
                    "start_chunk_index",
                    "expected_draft_sha256",
                    "chunks",
                ],
                "properties": {
                    "transaction_id": transaction_id_schema,
                    "start_chunk_index": {"type": "integer", "minimum": 0},
                    "expected_draft_sha256": digest_schema,
                    "chunks": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_GENERATED_CHUNKS_PER_APPEND,
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_GENERATED_CHUNK_BYTES,
                        },
                    },
                },
            },
            workspace.append_generated_file_chunks,
        ),
        "commit_generated_file_write": ToolSpec(
            "commit_generated_file_write",
            (
                "AST-parse and atomically publish a complete draft after checking "
                "its final SHA-256, chunk count, byte count, and original target."
            ),
            {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "transaction_id",
                    "expected_draft_sha256",
                    "expected_chunk_count",
                    "expected_bytes",
                ],
                "properties": {
                    "transaction_id": transaction_id_schema,
                    "expected_draft_sha256": digest_schema,
                    "expected_chunk_count": {"type": "integer", "minimum": 1},
                    "expected_bytes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_GENERATED_TEXT_BYTES,
                    },
                },
            },
            workspace.commit_generated_file_write,
        ),
        "abort_generated_file_write": ToolSpec(
            "abort_generated_file_write",
            "Discard one open draft after acknowledging its latest SHA-256.",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["transaction_id", "expected_draft_sha256"],
                "properties": {
                    "transaction_id": transaction_id_schema,
                    "expected_draft_sha256": digest_schema,
                },
            },
            workspace.abort_generated_file_write,
        ),
        "write_package_manifest": ToolSpec(
            "write_package_manifest",
            "Write the parsed package manifest. Its schema is exact and its Stage 1 hash is frozen.",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["manifest"],
                "properties": {
                    "manifest": workspace.frozen_package_manifest_schema()
                },
            },
            workspace.write_package_manifest,
        ),
        "read_generated_file": ToolSpec(
            "read_generated_file",
            "Read one current generated package file before repair.",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["path"],
                "properties": {"path": path_schema},
            },
            workspace.read_generated_file,
        ),
        "list_generated_files": ToolSpec(
            "list_generated_files",
            "List current package file hashes.",
            {"type": "object", "additionalProperties": False},
            workspace.list_generated_files,
        ),
        "check_generated_syntax": ToolSpec(
            "check_generated_syntax",
            "Parse Python/JSON and check all required files and the Stage 1 hash.",
            {"type": "object", "additionalProperties": False},
            workspace.check_generated_syntax,
        ),
        "finish_package": ToolSpec(
            "finish_package",
            (
                "Run the authoritative syntax, manifest, AST-safety, public-API, "
                "binding, and semantic static contract checks. Complete is true "
                "only when the package can enter Validation phase 2."
            ),
            {"type": "object", "additionalProperties": False},
            workspace.finish_package,
        ),
    }
    if workspace.simulation_sandbox is not None:
        tools["probe_generated_capability"] = ToolSpec(
            "probe_generated_capability",
            (
                "Run one fixed resolver-frozen public development smoke profile "
                "against the current package in a fresh bounded worker. The "
                "authoritative static gate must already pass. This is Generation "
                "feedback only: it never passes Validation A/B, Demo, or sealing."
            ),
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["capability_id", "profile_id"],
                "properties": {
                    "capability_id": {
                        "type": "string",
                        "pattern": "^G[123]\\.[a-z][a-z0-9_]*$",
                    },
                    "profile_id": {
                        "enum": [
                            "public_joint_hold",
                            "public_cartesian_hold",
                            "public_object_relocation",
                            "public_object_lift",
                            "public_object_sequence",
                        ]
                    },
                },
            },
            workspace.probe_generated_capability,
            timeout_s=25.0,
        )
    tools.update(_incremental_generated_python_tools(workspace))
    return _snapshot_guarded_tools(workspace, tools)


def _incremental_generated_python_tools(
    workspace: GenerationWorkspace,
) -> dict[str, ToolSpec]:
    """Build bounded source-navigation and exact-edit tools for Stage 2/repair."""

    path_schema = {
        "type": "string",
        "enum": sorted(ALLOWED_GENERATED_PYTHON_FILES),
    }
    return {
        "replace_generated_file_text": ToolSpec(
            "replace_generated_file_text",
            (
                "Patch one generated Python file by replacing one exact, unique "
                "text span. Read the current bounded source/hash first."
            ),
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "old_text", "new_text", "expected_sha256"],
                "properties": {
                    "path": path_schema,
                    "old_text": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_GENERATED_TEXT_BYTES,
                    },
                    "new_text": {
                        "type": "string",
                        "maxLength": MAX_GENERATED_TEXT_BYTES,
                    },
                    "expected_sha256": {
                        "type": "string",
                        "pattern": "^[0-9a-f]{64}$",
                    },
                },
            },
            workspace.replace_generated_file_text,
        ),
        "search_generated_file_text": ToolSpec(
            "search_generated_file_text",
            (
                "Search one generated Python file for a literal status, phase, or "
                "known helper fragment and return bounded line context plus its "
                "SHA-256. context_lines must be an integer from 0 through 6. If "
                "there are no matches, switch to an exact symbol read instead of "
                "guessing another literal or nonexistent helper."
            ),
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "query", "context_lines"],
                "properties": {
                    "path": path_schema,
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_REPAIR_SEARCH_QUERY_CHARACTERS,
                    },
                    "context_lines": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": MAX_REPAIR_SEARCH_CONTEXT_LINES,
                    },
                },
            },
            workspace.search_generated_file_text,
        ),
        "read_generated_python_symbol": ToolSpec(
            "read_generated_python_symbol",
            (
                "Read one complete, bounded top-level function from a generated "
                "Python file and return its exact source, line range, and SHA-256. "
                "Pass exactly one locator: symbol or line_number, never both or "
                "neither. Use an exact capability/shared-reference symbol or an "
                "exact static-failure line; do not guess nonexistent helpers. A "
                "shared nonzero-tool-point diagnosis may start at the exact "
                "_kinematics.py symbol _forward_kinematics."
            ),
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["path"],
                "properties": {
                    "path": path_schema,
                    "symbol": {
                        "type": "string",
                        "pattern": "^[A-Za-z_][A-Za-z0-9_]{0,127}$",
                    },
                    "line_number": {
                        "type": "integer",
                        "minimum": 1,
                    },
                },
                "oneOf": [
                    {
                        "required": ["symbol"],
                        "not": {"required": ["line_number"]},
                    },
                    {
                        "required": ["line_number"],
                        "not": {"required": ["symbol"]},
                    },
                ],
            },
            workspace.read_generated_python_symbol,
        ),
    }


def repair_tools(
    workspace: GenerationWorkspace,
    *,
    base_tools: Mapping[str, ToolSpec] | None = None,
) -> dict[str, ToolSpec]:
    """Return Stage 2 tools, ensuring bounded exact-edit tools are present."""

    tools = dict(base_tools) if base_tools is not None else stage2_tools(workspace)
    tools.update(_incremental_generated_python_tools(workspace))
    return _snapshot_guarded_tools(workspace, tools)


@dataclass
class ContinuousGeneration:
    agent: GenerationReActAgent
    workspace: GenerationWorkspace
    repair_instruction: str = ""
    require_repair_artifact_change: bool = False
    repair_context_max_bytes: int = DEFAULT_REPAIR_CONTEXT_MAX_BYTES
    repair_ledger_max_bytes: int = DEFAULT_REPAIR_LEDGER_MAX_BYTES
    stage1_result: AgentResult | None = None
    stage2_result: AgentResult | None = None
    repair_results: list[AgentResult] = field(default_factory=list)
    repair_rounds: int = 0
    _repair_ledger_rounds: list[dict[str, Any]] = field(
        default_factory=list,
        repr=False,
    )
    _repair_identifier_salt: str = field(
        default_factory=lambda: uuid.uuid4().hex,
        repr=False,
    )

    def __post_init__(self) -> None:
        for name, value in (
            ("repair_context_max_bytes", self.repair_context_max_bytes),
            ("repair_ledger_max_bytes", self.repair_ledger_max_bytes),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer")

    @classmethod
    def create(
        cls,
        *,
        client: ModelClient,
        workspace: GenerationWorkspace,
        system_prompt: str,
        trace_path: str | Path,
        stage1_call_limit: int = 3,
        repair_instruction: str = "",
        require_repair_artifact_change: bool = False,
        repair_context_max_bytes: int = DEFAULT_REPAIR_CONTEXT_MAX_BYTES,
        repair_ledger_max_bytes: int = DEFAULT_REPAIR_LEDGER_MAX_BYTES,
    ) -> "ContinuousGeneration":
        agent = GenerationReActAgent(
            agent_id="soarm101-generation-agent",
            role="generation",
            client=client,
            system_prompt=system_prompt,
            tools=stage1_tools(workspace),
            budget=BudgetCounter("generation_stage1", stage1_call_limit),
            trace_path=trace_path,
        )
        return cls(
            agent=agent,
            workspace=workspace,
            repair_instruction=repair_instruction,
            require_repair_artifact_change=require_repair_artifact_change,
            repair_context_max_bytes=repair_context_max_bytes,
            repair_ledger_max_bytes=repair_ledger_max_bytes,
        )

    def run_stage1(self, instruction: str) -> Stage1Freeze:
        # Resolver freeze, public asset composition, and compile/reset must be
        # proven before the first provider request, not lazily on the first
        # simulation tool call.
        self.workspace.assert_generation_environment_ready()
        self.stage1_result = self.agent.begin_stage1(instruction)
        return self.workspace.freeze_stage1()

    def run_stage2(self, instruction: str, *, initial_call_limit: int = 30) -> Path:
        if self.stage1_result is None:
            raise GenerationWorkspaceError("Stage 1 must complete before Stage 2")
        self.workspace.assert_generation_environment_ready()
        budget = BudgetCounter("generation_stage2_initial", initial_call_limit)
        try:
            self.stage2_result = self.agent.continue_stage2(
                instruction,
                tools=stage2_tools(self.workspace),
                stage2_budget=budget,
            )
        except BudgetExceeded:
            # The call limit bounds model work, not artifact acceptance.  If
            # the final response is malformed after the package was written,
            # let deterministic syntax/static validation judge that package.
            self.stage2_result = self.agent.accounting_result(
                status="budget_exhausted"
            )
        except BaseException:
            # Preserve calls and provider usage even when this window fails.
            self.stage2_result = self.agent.accounting_result(status="failed")
            raise
        finally:
            # A final action, exhausted budget, or provider/protocol error must
            # never leave partial source behind for a later phase/run. Committed
            # targets are untouched; only unpublished drafts are discarded.
            cleanup = getattr(self.workspace, "abort_all_generated_file_writes", None)
            if callable(cleanup):
                cleanup()
        self.workspace.assert_input_snapshot_unchanged()
        check = self.workspace.check_generated_syntax()
        if not check["ok"]:
            raise GenerationWorkspaceError(f"Agent finished with incomplete package: {check['errors']}")
        return self.workspace.package_root

    @staticmethod
    def _repair_agent_ledger_summary(result: AgentResult) -> dict[str, Any]:
        return {
            "status": result.status,
            "model_calls": result.model_calls,
            "agent_turns": result.agent_turns,
            "provider_http_attempts": result.provider_http_attempts,
            "provider_retries": result.provider_retries,
        }

    def _attach_subsequent_validation(
        self,
        current_feedback: Mapping[str, Any],
    ) -> dict[str, Any]:
        summary = _repair_feedback_summary(
            current_feedback,
            identifier_salt=self._repair_identifier_salt,
        )
        if (
            self._repair_ledger_rounds
            and "package_change_gate" not in summary
        ):
            self._record_validation_observation(
                record=self._repair_ledger_rounds[-1],
                summary=summary,
            )
        return summary

    @staticmethod
    def _record_validation_observation(
        *,
        record: dict[str, Any],
        summary: Mapping[str, Any],
    ) -> bool:
        annotated = _subsequent_validation_summary(
            origin_feedback=record["feedback"],
            validation_feedback=summary,
        )
        observations = record.setdefault("validation_observations", [])
        identity = _validation_summary_identity(summary)
        for existing in observations:
            if _validation_summary_identity(existing) == identity:
                return False
            if existing.get("stage") == summary.get("stage"):
                raise GenerationWorkspaceError(
                    "repair ledger received conflicting duplicate validation "
                    f"stage {summary.get('stage')!r} after round "
                    f"{record.get('round')}"
                )
        if len(observations) >= 2:
            raise GenerationWorkspaceError(
                "repair ledger permits at most one static and one direct "
                f"validation observation after round {record.get('round')}"
            )
        observations.append(annotated)
        if record.get("subsequent_validation") is None:
            record["subsequent_validation"] = copy.deepcopy(annotated)
        return True

    def observe_validation_feedback(
        self,
        feedback: Mapping[str, Any],
        *,
        after_repair_round: int,
    ) -> bool:
        """Safely close a completed repair with a static/direct result.

        The input may represent a pass with ``passed=true, failures=[]`` or a
        normal failure-feedback object. Only the bounded allowlisted summary
        is retained. Re-observing the same result is idempotent; a conflicting
        second result for the same stage fails closed.
        """

        input_guard = getattr(
            self.workspace,
            "assert_input_snapshot_unchanged",
            None,
        )
        if callable(input_guard):
            input_guard()
        if not isinstance(feedback, Mapping):
            raise TypeError("validation feedback must be a mapping")
        if feedback.get("stage") not in {"static", "direct_function"}:
            raise GenerationWorkspaceError(
                "validation observation stage must be static or direct_function"
            )
        passed = feedback.get("passed")
        if not isinstance(passed, bool):
            raise GenerationWorkspaceError(
                "validation observation passed must be an explicit boolean"
            )
        failures = feedback.get("failures")
        if not isinstance(failures, (list, tuple)):
            raise GenerationWorkspaceError(
                "validation observation failures must be an array"
            )
        if passed and failures:
            raise GenerationWorkspaceError(
                "passing validation observation cannot contain failures"
            )
        if not passed and not failures:
            raise GenerationWorkspaceError(
                "failing validation observation must contain at least one failure"
            )
        if isinstance(feedback.get("no_package_change"), Mapping):
            raise GenerationWorkspaceError(
                "package-change carry-over is not a validation observation"
            )
        if (
            isinstance(after_repair_round, bool)
            or not isinstance(after_repair_round, int)
            or after_repair_round < 1
            or after_repair_round != self.repair_rounds
        ):
            raise GenerationWorkspaceError(
                "validation observation must target the latest completed repair "
                f"round ({self.repair_rounds})"
            )
        payload_round = feedback.get("repair_round")
        if (
            isinstance(payload_round, bool)
            or not isinstance(payload_round, int)
            or payload_round != after_repair_round
        ):
            raise GenerationWorkspaceError(
                "validation observation repair_round must match "
                f"after_repair_round ({after_repair_round})"
            )
        if not self._repair_ledger_rounds:
            raise GenerationWorkspaceError(
                "validation observation requires a completed repair round"
            )
        record = self._repair_ledger_rounds[-1]
        if record.get("round") != after_repair_round or record.get("agent") is None:
            raise GenerationWorkspaceError(
                "validation observation requires a fully accounted latest repair"
            )
        summary = _repair_feedback_summary(
            feedback,
            identifier_salt=self._repair_identifier_salt,
        )
        return self._record_validation_observation(
            record=record,
            summary=summary,
        )

    @staticmethod
    def _repair_validation_events(
        rounds: list[dict[str, Any]],
    ) -> list[tuple[tuple[int, int], Mapping[str, Any]]]:
        events: list[tuple[tuple[int, int], Mapping[str, Any]]] = []
        for item in rounds:
            repair_round = int(item["round"])
            if "package_change_gate" not in item["feedback"]:
                events.append(((repair_round, 0), item["feedback"]))
            for sequence, observation in enumerate(
                item.get("validation_observations", []),
                start=1,
            ):
                events.append(((repair_round, sequence), observation))
        return events

    def _bounded_repair_ledger(
        self,
        *,
        next_round: int,
        current_feedback: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Project safe repair history into one strictly bounded model payload."""

        rounds = copy.deepcopy(self._repair_ledger_rounds)
        validation_events = self._repair_validation_events(rounds)
        if "package_change_gate" not in current_feedback:
            validation_events.append(((next_round, 0), current_feedback))
        return self._repair_ledger_document(
            rounds=rounds,
            through_repair_round=next_round - 1,
            validation_events=validation_events,
            projection_next_round=next_round,
        )

    def _repair_ledger_document(
        self,
        *,
        rounds: list[dict[str, Any]],
        through_repair_round: int,
        validation_events: list[
            tuple[tuple[int, int], Mapping[str, Any]]
        ],
        projection_next_round: int | None = None,
    ) -> dict[str, Any]:
        failure_registry: dict[str, dict[str, Any]] = {}
        failure_refs: dict[str, str] = {}

        def register_failure(signature: Mapping[str, Any]) -> str:
            identity = canonical_json(signature)
            existing = failure_refs.get(identity)
            if existing is not None:
                return existing
            reference = f"f{len(failure_registry) + 1}"
            failure_refs[identity] = reference
            failure_registry[reference] = copy.deepcopy(dict(signature))
            return reference

        def compact_failure_refs(signatures: Any) -> list[str]:
            """Return registry refs in first-seen order without duplicates."""

            compact: list[str] = []
            seen: set[str] = set()
            for signature in signatures or []:
                if not isinstance(signature, Mapping):
                    continue
                reference = register_failure(signature)
                if reference in seen:
                    continue
                seen.add(reference)
                compact.append(reference)
            return compact

        def compact_feedback(summary: Mapping[str, Any]) -> dict[str, Any]:
            compact: dict[str, Any] = {
                "stage": summary.get("stage"),
                "feedback_repair_round": summary.get(
                    "feedback_repair_round"
                ),
                "result": summary.get("result"),
                "failure_count": summary.get("failure_count"),
                "failure_refs": compact_failure_refs(
                    summary.get("failure_signatures", [])
                ),
            }
            if "package_change_gate" in summary:
                compact["package_change_gate"] = copy.deepcopy(
                    summary["package_change_gate"]
                )
            if "against_round_feedback" in summary:
                compact["against_round_feedback"] = copy.deepcopy(
                    summary["against_round_feedback"]
                )
            return compact

        projected_rounds: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []

        for item in rounds:
            origin_round = int(item["round"])
            origin_feedback = item["feedback"]
            origin_stage = origin_feedback.get("stage")
            later_same_stage = [
                (validation_position, validation)
                for validation_position, validation in validation_events
                if validation_position > (origin_round, 0)
                and validation.get("stage") == origin_stage
            ]
            resolution_groups: dict[
                tuple[str, bool, int | None],
                list[str],
            ] = {}
            for signature in origin_feedback.get("failure_signatures", []):
                signature_key = _repair_signature_key(signature)
                if not later_same_stage:
                    status = "not_revalidated"
                    last_validation_round = None
                    is_unresolved = True
                else:
                    last_position, latest_validation = later_same_stage[-1]
                    last_validation_round = last_position[0]
                    latest_keys = {
                        _repair_signature_key(value)
                        for value in latest_validation.get(
                            "failure_signatures", []
                        )
                        if isinstance(value, Mapping)
                    }
                    if latest_validation.get("result") == "passed":
                        status = "resolved_by_later_same_stage_pass"
                        is_unresolved = False
                    elif signature_key in latest_keys:
                        status = "still_unresolved"
                        is_unresolved = True
                    else:
                        status = "no_longer_reported_by_same_stage"
                        is_unresolved = False
                group_key = (status, is_unresolved, last_validation_round)
                group_refs = resolution_groups.setdefault(group_key, [])
                reference = register_failure(signature)
                if reference not in group_refs:
                    group_refs.append(reference)

            projected_resolution = [
                {
                    "failure_refs": refs,
                    "resolution_status": status,
                    "unresolved": is_unresolved,
                    "last_same_stage_validation_round": last_round,
                }
                for (status, is_unresolved, last_round), refs in (
                    resolution_groups.items()
                )
            ]
            unresolved.extend(
                {
                    "origin_round": origin_round,
                    **copy.deepcopy(group),
                }
                for group in projected_resolution
                if group["unresolved"] is True
            )
            projected_rounds.append(
                {
                    "round": origin_round,
                    "feedback": compact_feedback(origin_feedback),
                    "package": copy.deepcopy(item["package"]),
                    "agent": copy.deepcopy(item["agent"]),
                    "process": copy.deepcopy(item["process"]),
                    "subsequent_validation": (
                        None
                        if item.get("subsequent_validation") is None
                        else compact_feedback(item["subsequent_validation"])
                    ),
                    "validation_observations": [
                        compact_feedback(observation)
                        for observation in item.get(
                            "validation_observations", []
                        )
                    ],
                    "failure_resolution": projected_resolution,
                    "secondary_errors": copy.deepcopy(
                        item.get("secondary_errors", [])
                    ),
                }
            )

        ledger = {
            "schema_version": "robot_capability.repair_ledger.v2",
            "continuity": {
                "agent_id": self.agent.agent_id,
                "session_id": self.agent.session_id,
                "episode_id": self.agent.episode_id,
                "same_agent_session": True,
            },
            "through_repair_round": through_repair_round,
            "round_count": len(projected_rounds),
            "failure_registry": failure_registry,
            "rounds": projected_rounds,
            "unresolved_prior_failures": unresolved,
            "privacy_contract": {
                "contains_raw_messages": False,
                "contains_raw_tracebacks": False,
                "contains_raw_measurements_or_diagnostics": False,
                "failure_signature_fields": [
                    "code",
                    "capability_ref",
                    "case_ref",
                    "diagnostic",
                    "signature_sha256",
                ],
                "allowed_diagnostic_fields": [
                    "kind",
                    "unresolved_symbol",
                ],
                "identifier_pseudonymization": {
                    "algorithm": "salted_sha256_truncated_96bit",
                    "scope": "generation_agent_instance",
                    "cross_round_linkable": True,
                    "cross_run_linkable": False,
                    "raw_identifiers_retained": False,
                },
            },
            "max_utf8_bytes": self.repair_ledger_max_bytes,
        }
        ledger["utf8_bytes"] = 0
        for _ in range(4):
            final_bytes = len(canonical_json(ledger).encode("utf-8"))
            if ledger["utf8_bytes"] == final_bytes:
                break
            ledger["utf8_bytes"] = final_bytes
        final_bytes = len(canonical_json(ledger).encode("utf-8"))
        if final_bytes > self.repair_ledger_max_bytes:
            raise GenerationWorkspaceError(
                "repair ledger exceeds configured byte limit before model call: "
                f"bytes={final_bytes}, limit={self.repair_ledger_max_bytes}"
            )
        if ledger["utf8_bytes"] != final_bytes:
            raise GenerationWorkspaceError(
                "repair ledger byte-size accounting did not converge"
            )
        assert_repair_ledger_semantics(
            ledger,
            projection_next_round=projection_next_round,
        )
        return ledger

    def repair_ledger_audit(self) -> dict[str, Any]:
        """Return a deterministic, privacy-safe deep copy for persistence.

        Unlike a context-epoch projection, this includes the most recently
        completed repair even when no later validation has run yet.  It is
        therefore suitable for a pipeline ``finally`` block or terminal run
        report, including provider-error rounds.
        """

        rounds = copy.deepcopy(self._repair_ledger_rounds)
        validation_events = self._repair_validation_events(rounds)
        ledger = self._repair_ledger_document(
            rounds=rounds,
            through_repair_round=self.repair_rounds,
            validation_events=validation_events,
        )
        return copy.deepcopy(ledger)

    def _finish_repair_ledger_round(
        self,
        *,
        record: dict[str, Any],
        result: AgentResult,
    ) -> list[BaseException]:
        """Finalize one round without allowing audit work to mask a primary error."""

        secondary_exceptions: list[BaseException] = []
        record["agent"] = self._repair_agent_ledger_summary(result)
        record["process"] = self.agent.process_audit()
        secondary_errors = record.setdefault("secondary_errors", [])

        cleanup = getattr(self.workspace, "abort_all_generated_file_writes", None)
        if callable(cleanup):
            try:
                cleanup()
            except BaseException as exc:
                secondary_exceptions.append(exc)
                secondary_errors.append(
                    {
                        "operation": "abort_generated_file_writes",
                        "error_type": _repair_secondary_error_type(exc),
                    }
                )

        package = record["package"]
        try:
            after_digest = package_tree_sha256(self.workspace.package_root)
        except BaseException as exc:
            secondary_exceptions.append(exc)
            secondary_errors.append(
                {
                    "operation": "hash_generated_package_after_repair",
                    "error_type": _repair_secondary_error_type(exc),
                }
            )
            package["inspection_status"] = "hash_failed"
            package["after_sha256"] = None
            package["changed"] = None
        else:
            package["inspection_status"] = "complete"
            package["after_sha256"] = after_digest
            package["changed"] = after_digest != package["before_sha256"]
        return secondary_exceptions

    def repair(
        self,
        feedback: Mapping[str, Any],
        *,
        max_rounds: int = 10,
        max_model_calls_per_round: int = 6,
    ) -> Path:
        if self.stage2_result is None:
            raise GenerationWorkspaceError("Stage 2 must run before repair")
        input_guard = getattr(
            self.workspace,
            "assert_input_snapshot_unchanged",
            None,
        )
        if callable(input_guard):
            input_guard()
        if max_rounds > 10:
            raise ValueError("P0 repair hard limit cannot exceed 10 rounds")
        if (
            isinstance(max_model_calls_per_round, bool)
            or not isinstance(max_model_calls_per_round, int)
            or not 1 <= max_model_calls_per_round <= 6
        ):
            raise ValueError("P0 repair model-call limit must be between 1 and 6 per round")
        if self.repair_rounds >= max_rounds:
            raise GenerationWorkspaceError(f"repair round limit reached ({max_rounds})")
        next_round = self.repair_rounds + 1
        feedback_summary = self._attach_subsequent_validation(feedback)
        repair_ledger = self._bounded_repair_ledger(
            next_round=next_round,
            current_feedback=feedback_summary,
        )
        budget = BudgetCounter(
            f"generation_repair_round_{next_round:02d}",
            max_model_calls_per_round,
        )
        baseline_digest = package_tree_sha256(self.workspace.package_root)
        context_checkpoint = self.workspace.repair_context_checkpoint(
            repair_round=next_round,
            package_tree_digest=baseline_digest,
        )
        context_checkpoint = dict(context_checkpoint)
        context_checkpoint["repair_ledger"] = repair_ledger
        repair_record: dict[str, Any] = {
            "round": next_round,
            "feedback": feedback_summary,
            "package": {
                "before_sha256": baseline_digest,
                "inspection_status": "pending",
                "after_sha256": None,
                "changed": None,
            },
            "agent": None,
            "process": None,
            "subsequent_validation": None,
            "validation_observations": [],
            "secondary_errors": [],
        }
        self._repair_ledger_rounds.append(repair_record)
        self.repair_rounds = next_round

        def final_guard() -> str | None:
            if not self.require_repair_artifact_change:
                return None
            if package_tree_sha256(self.workspace.package_root) == baseline_digest:
                return (
                    "validation is still failing and this repair round has not changed "
                    "any generated-package artifact; use the remaining independent "
                    "repair calls to read and update an implementation file before final"
                )
            return None

        primary_error: BaseException | None = None
        primary_traceback: Any = None
        budget_exhausted = False
        try:
            result = self.agent.resume_repair(
                dict(feedback),
                repair_budget=budget,
                context_checkpoint=context_checkpoint,
                max_request_context_bytes=self.repair_context_max_bytes,
                instruction=self.repair_instruction,
                final_guard=final_guard,
                tools=repair_tools(
                    self.workspace,
                    base_tools=self.agent.tools,
                ),
            )
        except BudgetExceeded:
            # A repair window may use all six calls yet leave a complete
            # candidate (for example, a malformed final message after a
            # successful finish_package tool call).  Count the round and pass
            # the current files back to deterministic validation; a remaining
            # defect will open the next independent repair round.
            result = self.agent.accounting_result(
                status="budget_exhausted"
            )
            budget_exhausted = True
        except BaseException as exc:
            primary_error = exc
            primary_traceback = exc.__traceback__
            result = self.agent.accounting_result(status="failed")

        if primary_error is None and callable(input_guard):
            input_guard()

        self.repair_results.append(result)
        secondary_exceptions = self._finish_repair_ledger_round(
            record=repair_record,
            result=result,
        )
        if primary_error is not None:
            raise primary_error.with_traceback(primary_traceback)
        if secondary_exceptions:
            error_types = sorted(
                {
                    _repair_secondary_error_type(exc)
                    for exc in secondary_exceptions
                }
            )
            raise GenerationWorkspaceError(
                "repair finalization failed after the model window: "
                + ", ".join(error_types)
            )
        if budget_exhausted:
            return self.workspace.package_root
        return self.workspace.package_root

    @staticmethod
    def _result_audit(result: AgentResult, *, repair_round: int | None = None) -> dict[str, Any]:
        value: dict[str, Any] = {
            "status": result.status,
            "agent_turns": result.agent_turns,
            "provider_http_attempts": result.provider_http_attempts,
            "provider_retries": result.provider_retries,
            "client_kind": result.client_kind,
            "scripted": result.scripted,
            "agent_turn_budget": dict(result.agent_turn_budget),
            "provider_attempt_budget_policy": dict(
                result.provider_attempt_budget_policy
            ),
            "usage": dict(result.usage),
            "session_id": result.session_id,
            "episode_id": result.episode_id,
        }
        if repair_round is not None:
            value["round"] = repair_round
        return value

    def repair_result_audits(self) -> list[dict[str, Any]]:
        return [
            self._result_audit(result, repair_round=index)
            for index, result in enumerate(self.repair_results, start=1)
        ]

    def audit_state(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent.agent_id,
            "session_id": self.agent.session_id,
            "episode_id": self.agent.episode_id,
            "phase": self.agent.phase,
            "history_messages": len(self.agent.history),
            "context_epoch": self.agent.context_epoch,
            "repair_context_max_bytes": self.repair_context_max_bytes,
            "repair_ledger_max_bytes": self.repair_ledger_max_bytes,
            "repair_ledger_rounds": len(self._repair_ledger_rounds),
            "stage1_model_calls": None if self.stage1_result is None else self.stage1_result.model_calls,
            "stage2_initial_budget": None
            if self.stage2_result is None
            else dict(self.stage2_result.agent_turn_budget),
            "repair_rounds": self.repair_rounds,
            "repair_results": self.repair_result_audits(),
            "stage1_sha256": None
            if self.workspace._frozen is None
            else self.workspace.frozen_stage1.sha256,
        }
