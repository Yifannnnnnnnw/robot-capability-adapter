"""Denominator-preserving B2 formal result aggregation."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .b2 import (
    AUTHORITY_DOCUMENT_ID,
    AUTHORITY_REVISION,
    DEFAULT_MANIFEST_PATH,
    B2FormalError,
    B2Unit,
    ResolvedB2Manifest,
    _atomic_write_json,
    resolve_manifest,
)


_EVALUABLE = {"harness_pass", "harness_fail"}
_SCHEDULER_ABSENCE = {"not_run", "infrastructure_absence"}
_TERMINAL_CLASSIFICATIONS = {
    "harness_pass",
    "harness_fail",
    "evidence_incomplete",
    "model_failure",
    "controller_failure",
    "infrastructure_failure",
    "not_run",
}


class B2AggregationError(B2FormalError):
    """Raised when scheduler or terminal evidence cannot be trusted."""


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B2AggregationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise B2AggregationError(f"{label} must contain one JSON object")
    return value


def _required_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise B2AggregationError(f"{label} must be a non-empty string")
    return value.strip()


def _validate_terminal(
    path: Path, *, unit: B2Unit, manifest: ResolvedB2Manifest
) -> dict[str, Any]:
    terminal = _read_object(path, label="B2 terminal")
    if terminal.get("artifact_type") != "b2_formal_unit_terminal":
        raise B2AggregationError(f"terminal artifact type is invalid: {path}")
    authority = terminal.get("authority")
    if authority != {
        "document_id": AUTHORITY_DOCUMENT_ID,
        "revision": AUTHORITY_REVISION,
    }:
        raise B2AggregationError(f"terminal authority is stale or foreign: {path}")
    if terminal.get("manifest_revision") != AUTHORITY_REVISION:
        raise B2AggregationError(f"terminal manifest revision is stale: {path}")
    if terminal.get("formal_episode") is not True:
        raise B2AggregationError(f"terminal is not marked as a formal episode: {path}")
    code_version = terminal.get("code_version")
    if not isinstance(code_version, Mapping) or not code_version.get("git_commit"):
        raise B2AggregationError(f"terminal code version is absent: {path}")
    if terminal.get("unit_id") != unit.unit_id:
        raise B2AggregationError(f"terminal unit ID does not match its path/plan: {path}")
    for key, expected in unit.as_dict().items():
        if terminal.get(key) != expected:
            raise B2AggregationError(f"terminal {key} does not match its planned unit: {path}")
    _required_text(terminal.get("utc_finished_at"), label=f"terminal {path}.utc_finished_at")
    classification = terminal.get("classification")
    if classification not in _TERMINAL_CLASSIFICATIONS:
        raise B2AggregationError(f"terminal classification is invalid: {path}")
    expected_evaluable = classification in _EVALUABLE
    if terminal.get("evaluable") is not expected_evaluable:
        raise B2AggregationError(f"terminal evaluable flag disagrees with classification: {path}")
    expected_success = classification == "harness_pass"
    if terminal.get("success") is not expected_success:
        raise B2AggregationError(f"terminal success flag disagrees with classification: {path}")
    if classification in _EVALUABLE:
        identity = terminal.get("model_identity")
        harness = terminal.get("harness")
        expected_model = manifest.provider_pins[unit.model_id].get("exact_model_id")
        returned_models = identity.get("returned_models") if isinstance(identity, Mapping) else None
        physical_integrity = (
            harness.get("physical_integrity_passed")
            if isinstance(harness, Mapping)
            else None
        )
        if (
            not isinstance(identity, Mapping)
            or identity.get("exact_match") is not True
            or identity.get("requested_model") != expected_model
            or not isinstance(returned_models, list)
            or not returned_models
            or any(value != expected_model for value in returned_models)
            or not isinstance(harness, Mapping)
            or not isinstance(physical_integrity, bool)
            or harness.get("video_complete") is not True
        ):
            raise B2AggregationError(
                f"evaluable terminal lacks exact identity, integrity, or video evidence: {path}"
            )
        expected_verdict = "PASS" if classification == "harness_pass" else "FAIL"
        if harness.get("physical_harness_verdict") != expected_verdict:
            raise B2AggregationError(f"terminal Harness verdict disagrees with classification: {path}")
        if classification == "harness_pass" and physical_integrity is not True:
            raise B2AggregationError(
                f"Harness PASS contradicts physical integrity evidence: {path}"
            )

        episode_record_path = Path(
            _required_text(
                terminal.get("episode_record_path"),
                label=f"terminal {path}.episode_record_path",
            )
        ).resolve()
        provider_record_path = Path(
            _required_text(
                terminal.get("provider_record_path"),
                label=f"terminal {path}.provider_record_path",
            )
        ).resolve()
        expected_evidence_root = (
            path.parent.parent / "episodes" / unit.unit_id.replace("::", "__")
        ).resolve()
        if episode_record_path != expected_evidence_root / "episode_record.json":
            raise B2AggregationError(
                f"formal episode record is outside its run bundle: {episode_record_path}"
            )
        if provider_record_path != expected_evidence_root / "provider_record.json":
            raise B2AggregationError(
                f"formal provider record is outside its run bundle: {provider_record_path}"
            )
        episode_record = _read_object(
            episode_record_path,
            label="B2 formal episode record",
        )
        provider_record = _read_object(
            provider_record_path,
            label="B2 formal provider record",
        )
        if (
            episode_record.get("artifact_type") != "b2_formal_episode_record"
            or episode_record.get("unit") != unit.as_dict()
            or episode_record.get("formal_episode") is not True
        ):
            raise B2AggregationError(
                f"formal episode record is stale or foreign: {episode_record_path}"
            )
        if (
            provider_record.get("artifact_type") != "b2_formal_provider_record"
            or provider_record.get("unit") != unit.as_dict()
            or provider_record.get("formal_episode") is not True
        ):
            raise B2AggregationError(
                f"formal provider record is stale or foreign: {provider_record_path}"
            )
    return terminal


def _validate_scheduler(
    path: Path,
    *,
    manifest: ResolvedB2Manifest,
    coverage: dict[str, dict[str, Any]],
    planned_seen: set[str],
) -> None:
    scheduler = _read_object(path, label="B2 scheduler")
    if scheduler.get("artifact_type") != "b2_formal_scheduler":
        raise B2AggregationError(f"scheduler artifact type is invalid: {path}")
    authority = scheduler.get("authority")
    if authority != {
        "document_id": AUTHORITY_DOCUMENT_ID,
        "revision": AUTHORITY_REVISION,
    }:
        raise B2AggregationError(f"scheduler authority is stale or foreign: {path}")
    if scheduler.get("manifest_revision") != AUTHORITY_REVISION:
        raise B2AggregationError(f"scheduler manifest revision is stale: {path}")
    _required_text(scheduler.get("utc_finished_at"), label=f"scheduler {path}.utc_finished_at")
    planned = scheduler.get("planned_unit_ids")
    if not isinstance(planned, list) or any(not isinstance(item, str) for item in planned):
        raise B2AggregationError(f"scheduler planned_unit_ids is invalid: {path}")
    if len(planned) != len(set(planned)):
        raise B2AggregationError(f"scheduler contains duplicate planned IDs: {path}")
    overlap = planned_seen.intersection(planned)
    if overlap:
        raise B2AggregationError(f"duplicate planned IDs across schedulers: {sorted(overlap)[:5]}")
    planned_seen.update(planned)
    for unit_id in planned:
        try:
            manifest.unit(unit_id)
        except B2FormalError as exc:
            raise B2AggregationError(f"scheduler contains a foreign/stale unit ID: {unit_id}") from exc

    records = scheduler.get("records")
    if not isinstance(records, list):
        raise B2AggregationError(f"scheduler records is absent: {path}")
    record_ids: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise B2AggregationError(f"scheduler record is invalid: {path}")
        unit_id = _required_text(record.get("unit_id"), label="scheduler record unit_id")
        if unit_id not in planned:
            raise B2AggregationError(f"scheduler record is not in planned_unit_ids: {unit_id}")
        if unit_id in record_ids or unit_id in coverage:
            raise B2AggregationError(f"duplicate scheduler/terminal record for {unit_id}")
        record_ids.add(unit_id)
        unit = manifest.unit(unit_id)
        status = record.get("status")
        if status == "terminal":
            terminal_raw = record.get("terminal_path")
            terminal_path = Path(_required_text(terminal_raw, label="terminal_path"))
            if not terminal_path.is_absolute():
                terminal_path = path.parent / terminal_path
            terminal_path = terminal_path.resolve()
            expected_terminal_path = (
                path.parent
                / "terminals"
                / f"{unit_id.replace('::', '__')}.json"
            ).resolve()
            if terminal_path != expected_terminal_path:
                raise B2AggregationError(
                    f"scheduler terminal is outside its run bundle: {terminal_path}"
                )
            terminal = _validate_terminal(terminal_path, unit=unit, manifest=manifest)
            coverage[unit_id] = {
                "kind": "terminal",
                "classification": terminal["classification"],
                "terminal": terminal,
                "scheduler_path": str(path),
            }
        elif status in _SCHEDULER_ABSENCE:
            _required_text(record.get("reason"), label=f"scheduler {unit_id}.reason")
            coverage[unit_id] = {
                "kind": "scheduler_absence",
                "classification": "not_run" if status == "not_run" else status,
                "scheduler_path": str(path),
            }
        else:
            raise B2AggregationError(f"scheduler status is invalid for {unit_id}: {status!r}")
    planned_ids = set(planned)
    if record_ids != planned_ids:
        missing = sorted(planned_ids - record_ids)
        raise B2AggregationError(
            "scheduler is incomplete: planned IDs have no terminal or explicit "
            f"scheduler-absence record: {missing[:5]}"
        )


def _unit_summary(units: Sequence[B2Unit], coverage: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    planned = len(units)
    classes = [str(coverage.get(unit.unit_id, {}).get("classification", "missing")) for unit in units]
    successes = classes.count("harness_pass")
    failures = classes.count("harness_fail")
    evaluable = successes + failures
    statuses = dict(sorted(Counter(classes).items()))
    return {
        "planned": planned,
        "successes": successes,
        "failures": failures,
        "evaluable_denominator": evaluable,
        "non_evaluable": planned - evaluable,
        "missing": classes.count("missing"),
        "infrastructure_or_evidence": planned - evaluable,
        "status_counts": statuses,
    }


def aggregate_schedulers(
    scheduler_paths: Sequence[str | Path],
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    require_complete: bool = False,
) -> dict[str, Any]:
    """Aggregate scheduler/terminal records while retaining all fixed denominators."""

    if not scheduler_paths:
        raise B2AggregationError("at least one scheduler JSON is required")
    manifest = resolve_manifest(manifest_path)
    coverage: dict[str, dict[str, Any]] = {}
    planned_seen: set[str] = set()
    for raw_path in scheduler_paths:
        _validate_scheduler(
            Path(raw_path).resolve(),
            manifest=manifest,
            coverage=coverage,
            planned_seen=planned_seen,
        )
    expected_ids = {unit.unit_id for unit in manifest.units}
    trustworthy_terminal_ids = {
        unit_id
        for unit_id, item in coverage.items()
        if item.get("kind") == "terminal"
        and item.get("classification") != "not_run"
    }
    if require_complete and trustworthy_terminal_ids != expected_ids:
        missing = sorted(expected_ids - set(coverage))
        nonterminal = sorted(expected_ids - trustworthy_terminal_ids)
        raise B2AggregationError(
            "--require-complete found planned IDs without a trustworthy executed or "
            "infrastructure/evidence terminal: "
            f"missing={missing[:5]}, nonterminal={nonterminal[:5]}"
        )

    per_model = {
        model_id: _unit_summary(
            [unit for unit in manifest.units if unit.model_id == model_id], coverage
        )
        for model_id in manifest.models
    }
    per_robot_model = {
        f"{robot_id}::{model_id}": _unit_summary(
            [
                unit
                for unit in manifest.units
                if unit.robot_configuration_id == robot_id and unit.model_id == model_id
            ],
            coverage,
        )
        for robot_id in manifest.robots
        for model_id in manifest.models
    }
    per_robot_task_model = {
        f"{robot_id}::{task_id}::{model_id}": _unit_summary(
            [
                unit
                for unit in manifest.units
                if unit.robot_configuration_id == robot_id
                and unit.task_id == task_id
                and unit.model_id == model_id
            ],
            coverage,
        )
        for robot_id in manifest.robots
        for task_id in manifest.task_ids[robot_id]
        for model_id in manifest.models
    }
    aggregate = {
        "artifact_type": "b2_formal_aggregate",
        "schema_version": "1.0",
        "authority": {
            "document_id": AUTHORITY_DOCUMENT_ID,
            "revision": AUTHORITY_REVISION,
        },
        "manifest_revision": AUTHORITY_REVISION,
        "scheduler_paths": [str(Path(path).resolve()) for path in scheduler_paths],
        "require_complete": require_complete,
        "complete": trustworthy_terminal_ids == expected_ids,
        "planned_denominator": len(manifest.units),
        "overall": _unit_summary(list(manifest.units), coverage),
        "per_model": per_model,
        "per_robot_model": per_robot_model,
        "per_robot_task_model": per_robot_task_model,
    }
    return aggregate


def write_aggregate(path: str | Path, aggregate: Mapping[str, Any]) -> None:
    _atomic_write_json(Path(path).resolve(), aggregate)


__all__ = ["B2AggregationError", "aggregate_schedulers", "write_aggregate"]
