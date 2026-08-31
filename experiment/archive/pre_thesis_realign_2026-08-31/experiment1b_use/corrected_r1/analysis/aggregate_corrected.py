#!/usr/bin/env python3
"""Aggregate the isolated 70-unit corrected-R1 audit without touching AA2-B2."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
import json
import math
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CORRECTED_ROOT = HERE.parent
REPOSITORY_ROOT = HERE.parents[3]
AUDIT_IDENTITY = {
    "document_id": "AA2-B2-CORRECTED-R1",
    "revision": "1.0.0",
}
DEFAULT_MANIFEST = CORRECTED_ROOT / "config/manifest.json"
DEFAULT_SIDECARS = HERE / "rejudication"
DEFAULT_OUTPUT = HERE / "corrected_r1_aggregate.json"
DEFAULT_REALTIME_INDEX = HERE / "realtime_video_index.json"
EVALUABLE = {"harness_pass", "harness_fail"}


class CorrectedAggregationError(RuntimeError):
    """Raised when the corrected evidence cannot be uniquely accounted for."""


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorrectedAggregationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise CorrectedAggregationError(f"{label} must contain one JSON object: {path}")
    return value


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(path.resolve())


def _resolve_evidence_path(raw: Any, *, relative_to: Path) -> Path | None:
    if not isinstance(raw, str) or not raw:
        return None
    value = Path(raw)
    if value.is_absolute():
        return value.resolve()
    local = (relative_to / value).resolve()
    if local.exists():
        return local
    return (REPOSITORY_ROOT / value).resolve()


def _finite_cost(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) and converted >= 0.0 else None


def _provider_cost(path: Path | None) -> float | None:
    if path is None or not path.is_file():
        return None
    return _finite_cost(_read_object(path, label="provider record").get("total_cost_usd"))


def _video_path_from_record(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, Mapping):
        raw = value.get("path") or value.get("video_path")
        if isinstance(raw, str) and raw:
            return raw
    return None


def _fresh_video_path(
    *, terminal: Mapping[str, Any], harness: Any, terminal_path: Path
) -> tuple[str | None, Path | None]:
    for value in (
        harness.get("video") if isinstance(harness, Mapping) else None,
        terminal.get("video"),
        terminal.get("video_path"),
    ):
        path = _video_path_from_record(value)
        if path is not None:
            return path, _resolve_evidence_path(
                terminal.get("episode_record_path"), relative_to=terminal_path.parent
            )
    episode_path = _resolve_evidence_path(
        terminal.get("episode_record_path"), relative_to=terminal_path.parent
    )
    if episode_path is None or not episode_path.is_file():
        return None, episode_path
    record = _read_object(episode_path, label="corrected episode record")
    candidates: list[Any] = [record.get("video"), record.get("video_path")]
    episode = record.get("episode")
    if isinstance(episode, Mapping):
        candidates.extend((episode.get("video"), episode.get("video_path")))
        nested = episode.get("episode")
        if isinstance(nested, Mapping):
            candidates.extend((nested.get("video"), nested.get("video_path")))
    for value in candidates:
        path = _video_path_from_record(value)
        if path is not None:
            return path, episode_path
    return None, episode_path


def _json_artifacts(
    roots: Sequence[Path], artifact_type: str
) -> dict[str, tuple[Path, dict[str, Any]]]:
    results: dict[str, tuple[Path, dict[str, Any]]] = {}
    for root in roots:
        candidates = [root] if root.is_file() else sorted(root.rglob("*.json"))
        for path in candidates:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(value, dict) or value.get("artifact_type") != artifact_type:
                continue
            unit_id = value.get("unit_id")
            if not isinstance(unit_id, str) or not unit_id:
                raise CorrectedAggregationError(f"artifact has no unit_id: {path}")
            if unit_id in results:
                raise CorrectedAggregationError(f"duplicate evidence for {unit_id}")
            results[unit_id] = (path.resolve(), value)
    return results


def _scheduler_artifacts(
    scheduler_paths: Sequence[Path],
) -> dict[str, tuple[Path, dict[str, Any]]]:
    results: dict[str, tuple[Path, dict[str, Any]]] = {}
    for scheduler_path in scheduler_paths:
        scheduler_path = scheduler_path.resolve()
        scheduler = _read_object(scheduler_path, label="corrected scheduler")
        records = scheduler.get("records")
        if (
            scheduler.get("artifact_type") != "b2_corrected_r1_scheduler"
            or scheduler.get("audit_identity") != AUDIT_IDENTITY
            or scheduler.get("formal_episode") is not False
            or not isinstance(records, list)
        ):
            raise CorrectedAggregationError(
                f"corrected scheduler is invalid: {scheduler_path}"
            )
        for record in records:
            if not isinstance(record, Mapping):
                raise CorrectedAggregationError(
                    f"corrected scheduler record is invalid: {scheduler_path}"
                )
            unit_id = record.get("unit_id")
            if not isinstance(unit_id, str) or not unit_id or unit_id in results:
                raise CorrectedAggregationError(
                    f"duplicate/invalid scheduler unit: {unit_id!r}"
                )
            terminal_path = _resolve_evidence_path(
                record.get("terminal_path"), relative_to=scheduler_path.parent
            )
            if terminal_path is None or not terminal_path.is_file():
                raise CorrectedAggregationError(
                    f"scheduler has no selected terminal for {unit_id}"
                )
            terminal = _read_object(terminal_path, label="selected corrected terminal")
            if (
                terminal.get("artifact_type") != "b2_corrected_r1_unit_terminal"
                or terminal.get("unit_id") != unit_id
                or terminal.get("classification") != record.get("classification")
                or terminal.get("evaluable") is not record.get("evaluable")
                or terminal.get("success") is not record.get("success")
            ):
                raise CorrectedAggregationError(
                    f"scheduler/selected terminal disagree for {unit_id}"
                )
            results[unit_id] = (terminal_path, terminal)
    return results


def _scheduler_attempt_cost_records(
    scheduler_paths: Sequence[Path],
) -> list[dict[str, Any]]:
    """Account for every paid attempt while keeping only the selected outcome."""

    cost_records: list[dict[str, Any]] = []
    seen_terminal_paths: set[Path] = set()
    for scheduler_path in scheduler_paths:
        scheduler_path = scheduler_path.resolve()
        scheduler = _read_object(scheduler_path, label="corrected scheduler")
        records = scheduler.get("records")
        if (
            scheduler.get("artifact_type") != "b2_corrected_r1_scheduler"
            or scheduler.get("audit_identity") != AUDIT_IDENTITY
            or scheduler.get("formal_episode") is not False
            or not isinstance(records, list)
        ):
            raise CorrectedAggregationError(
                f"corrected scheduler is invalid: {scheduler_path}"
            )
        for record in records:
            if not isinstance(record, Mapping):
                raise CorrectedAggregationError(
                    f"corrected scheduler record is invalid: {scheduler_path}"
                )
            unit_id = record.get("unit_id")
            attempts = record.get("attempts")
            selected_path = _resolve_evidence_path(
                record.get("terminal_path"), relative_to=scheduler_path.parent
            )
            if (
                not isinstance(unit_id, str)
                or not unit_id
                or not isinstance(attempts, list)
                or not attempts
                or selected_path is None
            ):
                raise CorrectedAggregationError(
                    f"scheduler attempt history is invalid for {unit_id!r}"
                )
            attempt_paths: list[Path] = []
            for expected_number, attempt in enumerate(attempts, start=1):
                if not isinstance(attempt, Mapping):
                    raise CorrectedAggregationError(
                        f"scheduler attempt is invalid for {unit_id}"
                    )
                terminal_path = _resolve_evidence_path(
                    attempt.get("terminal_path"), relative_to=scheduler_path.parent
                )
                if (
                    attempt.get("attempt_number") != expected_number
                    or terminal_path is None
                    or not terminal_path.is_file()
                    or terminal_path in seen_terminal_paths
                ):
                    raise CorrectedAggregationError(
                        f"scheduler attempt terminal is invalid for {unit_id}"
                    )
                terminal = _read_object(
                    terminal_path, label="corrected attempt terminal"
                )
                if (
                    terminal.get("artifact_type")
                    != "b2_corrected_r1_unit_terminal"
                    or terminal.get("unit_id") != unit_id
                    or terminal.get("classification")
                    != attempt.get("classification")
                    or terminal.get("evaluable") is not attempt.get("evaluable")
                    or terminal.get("success") is not attempt.get("success")
                ):
                    raise CorrectedAggregationError(
                        f"scheduler/attempt terminal disagree for {unit_id}"
                    )
                provider_path = _resolve_evidence_path(
                    terminal.get("provider_record_path"),
                    relative_to=terminal_path.parent,
                )
                seen_terminal_paths.add(terminal_path)
                attempt_paths.append(terminal_path)
                cost_records.append(
                    {
                        "unit_id": unit_id,
                        "attempt_number": expected_number,
                        "selected": terminal_path == selected_path,
                        "provider_cost_usd": _provider_cost(provider_path),
                    }
                )
            if attempt_paths[-1] != selected_path:
                raise CorrectedAggregationError(
                    f"scheduler did not select the final attempt for {unit_id}"
                )
    return cost_records


def _validate_common(value: Mapping[str, Any], unit: Mapping[str, Any]) -> None:
    if value.get("audit_identity") != AUDIT_IDENTITY:
        raise CorrectedAggregationError(f"foreign audit identity for {unit['unit_id']}")
    if value.get("formal_episode") is not False:
        raise CorrectedAggregationError(f"formal episode leaked into corrected audit: {unit['unit_id']}")
    for key in (
        "unit_id",
        "robot_configuration_id",
        "task_id",
        "model_id",
        "replicate_id",
        "execution_origin",
    ):
        if value.get(key) != unit.get(key):
            raise CorrectedAggregationError(
                f"{key} does not match corrected manifest for {unit['unit_id']}"
            )


def _outcome_fields(
    *, classification: Any, evaluable: Any, success: Any, unit_id: str
) -> tuple[str, bool, bool]:
    if not isinstance(classification, str) or not classification:
        raise CorrectedAggregationError(f"classification is absent for {unit_id}")
    expected_evaluable = classification in EVALUABLE
    expected_success = classification == "harness_pass"
    if evaluable is not expected_evaluable or success is not expected_success:
        raise CorrectedAggregationError(f"outcome flags disagree for {unit_id}")
    return classification, expected_evaluable, expected_success


def _validate_evaluable_evidence(
    *,
    unit_id: str,
    classification: str,
    evaluable: bool,
    model_identity: Any,
    harness: Any,
) -> None:
    if not evaluable:
        return
    returned = (
        model_identity.get("returned_models")
        if isinstance(model_identity, Mapping)
        else None
    )
    requested = (
        model_identity.get("requested_model")
        if isinstance(model_identity, Mapping)
        else None
    )
    expected_verdict = "PASS" if classification == "harness_pass" else "FAIL"
    if (
        not isinstance(model_identity, Mapping)
        or model_identity.get("exact_match") is not True
        or not isinstance(requested, str)
        or not requested
        or not isinstance(returned, list)
        or not returned
        or any(value != requested for value in returned)
        or not isinstance(harness, Mapping)
        or harness.get("physical_harness_verdict") != expected_verdict
        or not isinstance(harness.get("physical_integrity_passed"), bool)
        or harness.get("video_complete") is not True
        or (
            classification == "harness_pass"
            and harness.get("physical_integrity_passed") is not True
        )
    ):
        raise CorrectedAggregationError(
            f"evaluable result lacks exact identity/Harness/video evidence: {unit_id}"
        )


def _retained_result(
    unit: Mapping[str, Any], path: Path, sidecar: Mapping[str, Any]
) -> dict[str, Any]:
    _validate_common(sidecar, unit)
    if sidecar.get("artifact_type") != "b2_corrected_r1_rejudication_sidecar":
        raise CorrectedAggregationError(f"invalid retained sidecar: {path}")
    semantic = sidecar.get("semantic_equivalence")
    corrected = sidecar.get("corrected_result")
    if not isinstance(semantic, Mapping) or semantic.get("passed") is not True:
        raise CorrectedAggregationError(f"semantic gate is not PASS for {unit['unit_id']}")
    if not isinstance(corrected, Mapping):
        raise CorrectedAggregationError(f"corrected result is absent for {unit['unit_id']}")
    classification, evaluable, success = _outcome_fields(
        classification=corrected.get("classification"),
        evaluable=corrected.get("evaluable"),
        success=corrected.get("success"),
        unit_id=str(unit["unit_id"]),
    )
    cost_record = sidecar.get("cost")
    cost = (
        _finite_cost(cost_record.get("original_execution_cost_usd"))
        if isinstance(cost_record, Mapping)
        else None
    )
    source = sidecar.get("source_evidence")
    harness = corrected.get("harness")
    _validate_evaluable_evidence(
        unit_id=str(unit["unit_id"]),
        classification=classification,
        evaluable=evaluable,
        model_identity=corrected.get("model_identity"),
        harness=harness,
    )
    video = harness.get("video") if isinstance(harness, Mapping) else None
    return {
        **{key: unit[key] for key in (
            "unit_id",
            "robot_configuration_id",
            "task_id",
            "model_id",
            "replicate_id",
            "execution_origin",
        )},
        "classification": classification,
        "evaluable": evaluable,
        "success": success,
        "model_identity": corrected.get("model_identity"),
        "harness": harness,
        "video_path": video.get("path") if isinstance(video, Mapping) else None,
        "evidence_path": _relative(path),
        "provider_cost_usd": cost,
        "provider_cost_scope": "retained_original_execution",
        "source_evidence": source,
    }


def _fresh_result(
    unit: Mapping[str, Any], path: Path, terminal: Mapping[str, Any]
) -> dict[str, Any]:
    _validate_common(terminal, unit)
    if terminal.get("artifact_type") != "b2_corrected_r1_unit_terminal":
        raise CorrectedAggregationError(f"invalid corrected terminal: {path}")
    classification, evaluable, success = _outcome_fields(
        classification=terminal.get("classification"),
        evaluable=terminal.get("evaluable"),
        success=terminal.get("success"),
        unit_id=str(unit["unit_id"]),
    )
    provider_path = _resolve_evidence_path(
        terminal.get("provider_record_path"), relative_to=path.parent
    )
    harness = terminal.get("harness")
    _validate_evaluable_evidence(
        unit_id=str(unit["unit_id"]),
        classification=classification,
        evaluable=evaluable,
        model_identity=terminal.get("model_identity"),
        harness=harness,
    )
    video_path, episode_record_path = _fresh_video_path(
        terminal=terminal, harness=harness, terminal_path=path
    )
    result = {
        **{key: unit[key] for key in (
            "unit_id",
            "robot_configuration_id",
            "task_id",
            "model_id",
            "replicate_id",
            "execution_origin",
        )},
        "classification": classification,
        "evaluable": evaluable,
        "success": success,
        "model_identity": terminal.get("model_identity"),
        "harness": harness,
        "video_path": video_path,
        "evidence_path": _relative(path),
        "episode_record_path": (
            _relative(episode_record_path) if episode_record_path else None
        ),
        "provider_record_path": _relative(provider_path) if provider_path else None,
        "provider_cost_usd": _provider_cost(provider_path),
        "provider_cost_scope": "new_corrected_execution",
    }
    if unit.get("execution_origin") == "replacement":
        old_path = (REPOSITORY_ROOT / str(unit["original_incomplete_terminal_path"])).resolve()
        terminal_old_path = _resolve_evidence_path(
            terminal.get("original_incomplete_terminal_path"), relative_to=path.parent
        )
        if terminal_old_path != old_path:
            raise CorrectedAggregationError(
                f"replacement terminal changed its original event link: {unit['unit_id']}"
            )
        old_terminal = _read_object(old_path, label="superseded incomplete terminal")
        if (
            old_terminal.get("unit_id") != unit.get("original_unit_id")
            or old_terminal.get("classification") != "evidence_incomplete"
        ):
            raise CorrectedAggregationError(
                f"replacement does not link one old incomplete event: {unit['unit_id']}"
            )
        old_provider_path = _resolve_evidence_path(
            old_terminal.get("provider_record_path"), relative_to=old_path.parent
        )
        result["replacement_source"] = {
            "original_unit_id": unit["original_unit_id"],
            "original_incomplete_terminal_path": _relative(old_path),
            "original_classification": old_terminal.get("classification"),
            "original_provider_cost_usd": _provider_cost(old_provider_path),
            "included_in_corrected_evidence_cost": False,
        }
    return result


def _missing_result(unit: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **{key: unit[key] for key in (
            "unit_id",
            "robot_configuration_id",
            "task_id",
            "model_id",
            "replicate_id",
            "execution_origin",
        )},
        "classification": "missing",
        "evaluable": False,
        "success": False,
        "model_identity": None,
        "harness": None,
        "video_path": None,
        "evidence_path": None,
        "provider_cost_usd": None,
        "provider_cost_scope": None,
    }


def _superseded_incomplete_costs(
    fresh_units: Sequence[Mapping[str, Any]],
) -> list[float | None]:
    costs: list[float | None] = []
    for unit in fresh_units:
        if unit.get("execution_origin") != "replacement":
            continue
        old_path = (
            REPOSITORY_ROOT / str(unit["original_incomplete_terminal_path"])
        ).resolve()
        old_terminal = _read_object(old_path, label="superseded incomplete terminal")
        if (
            old_terminal.get("unit_id") != unit.get("original_unit_id")
            or old_terminal.get("classification") != "evidence_incomplete"
        ):
            raise CorrectedAggregationError(
                f"invalid superseded incomplete event for {unit['unit_id']}"
            )
        provider_path = _resolve_evidence_path(
            old_terminal.get("provider_record_path"), relative_to=old_path.parent
        )
        costs.append(_provider_cost(provider_path))
    if len(costs) != 2:
        raise CorrectedAggregationError("corrected manifest must link two incomplete M2 events")
    return costs


def _summary(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(results)
    classes = [str(row["classification"]) for row in rows]
    known_costs = [
        float(row["provider_cost_usd"])
        for row in rows
        if _finite_cost(row.get("provider_cost_usd")) is not None
    ]
    evaluable = sum(row.get("evaluable") is True for row in rows)
    return {
        "planned": len(rows),
        "successes": sum(row.get("success") is True for row in rows),
        "failures": classes.count("harness_fail"),
        "evaluable_denominator": evaluable,
        "non_evaluable": len(rows) - evaluable,
        "missing": classes.count("missing"),
        "status_counts": dict(sorted(Counter(classes).items())),
        "known_provider_cost_usd": sum(known_costs),
        "unknown_provider_cost_units": sum(
            _finite_cost(row.get("provider_cost_usd")) is None
            and row.get("classification") != "missing"
            for row in rows
        ),
    }


def _group(
    results: Sequence[Mapping[str, Any]], key: str
) -> dict[str, dict[str, Any]]:
    values = sorted({str(row[key]) for row in results})
    return {
        value: _summary(row for row in results if str(row[key]) == value)
        for value in values
    }


def _realtime_video_map(index_path: Path) -> dict[str, str]:
    if not index_path.is_file():
        return {}
    index = _read_object(index_path, label="realtime video index")
    videos = index.get("videos")
    if (
        index.get("artifact_type") != "b2_corrected_r1_realtime_video_index"
        or index.get("audit_identity") != AUDIT_IDENTITY
        or index.get("source_videos_modified") is not False
        or not isinstance(videos, list)
    ):
        raise CorrectedAggregationError("realtime video index is invalid")
    result: dict[str, str] = {}
    for item in videos:
        if not isinstance(item, Mapping):
            raise CorrectedAggregationError("realtime video index item is invalid")
        unit_id = item.get("unit_id")
        path = item.get("realtime_copy_path")
        if not isinstance(unit_id, str) or not isinstance(path, str) or unit_id in result:
            raise CorrectedAggregationError("realtime video index is duplicate/incomplete")
        result[unit_id] = path
    return result


def aggregate_corrected(
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    sidecar_roots: Sequence[Path] = (DEFAULT_SIDECARS,),
    fresh_roots: Sequence[Path] = (),
    scheduler_paths: Sequence[Path] = (),
    realtime_index_path: Path = DEFAULT_REALTIME_INDEX,
    require_complete: bool = False,
) -> dict[str, Any]:
    manifest = _read_object(manifest_path.resolve(), label="corrected manifest")
    if (
        manifest.get("artifact_type") != "b2_corrected_r1_manifest"
        or manifest.get("audit_identity") != AUDIT_IDENTITY
        or manifest.get("formal_episode") is not False
        or manifest.get("corrected_r1_units") != 70
        or manifest.get("old_formal_plan_unchanged") != 210
    ):
        raise CorrectedAggregationError("corrected manifest identity/counts are invalid")
    retained_units = manifest.get("retained_units")
    fresh_units = manifest.get("fresh_units")
    if not isinstance(retained_units, list) or not isinstance(fresh_units, list):
        raise CorrectedAggregationError("corrected manifest unit lists are absent")
    units = retained_units + fresh_units
    ids = [unit.get("unit_id") for unit in units if isinstance(unit, Mapping)]
    if len(units) != 70 or len(ids) != 70 or len(set(ids)) != 70:
        raise CorrectedAggregationError("corrected manifest does not contain 70 unique units")
    origins = Counter(str(unit.get("execution_origin")) for unit in units)
    if dict(origins) != {
        "retained_rejudged": 19,
        "fresh_corrected": 49,
        "replacement": 2,
    }:
        raise CorrectedAggregationError("corrected execution-origin split is invalid")

    sidecars = _json_artifacts(
        [path.resolve() for path in sidecar_roots],
        "b2_corrected_r1_rejudication_sidecar",
    )
    terminals = _json_artifacts(
        [path.resolve() for path in fresh_roots],
        "b2_corrected_r1_unit_terminal",
    )
    selected_terminals = _scheduler_artifacts(scheduler_paths)
    scheduler_attempt_costs = _scheduler_attempt_cost_records(scheduler_paths)
    overlap = set(terminals).intersection(selected_terminals)
    if overlap:
        raise CorrectedAggregationError(
            f"duplicate direct/scheduler terminal evidence: {sorted(overlap)[:3]}"
        )
    terminals.update(selected_terminals)
    expected_retained = {str(unit["unit_id"]) for unit in retained_units}
    expected_fresh = {str(unit["unit_id"]) for unit in fresh_units}
    foreign = (set(sidecars) - expected_retained) | (set(terminals) - expected_fresh)
    if foreign:
        raise CorrectedAggregationError(f"foreign corrected evidence: {sorted(foreign)[:3]}")

    results: list[dict[str, Any]] = []
    for unit in units:
        unit_id = str(unit["unit_id"])
        if unit["execution_origin"] == "retained_rejudged" and unit_id in sidecars:
            path, value = sidecars[unit_id]
            results.append(_retained_result(unit, path, value))
        elif unit["execution_origin"] != "retained_rejudged" and unit_id in terminals:
            path, value = terminals[unit_id]
            results.append(_fresh_result(unit, path, value))
        else:
            results.append(_missing_result(unit))
    if require_complete and any(row["classification"] == "missing" for row in results):
        missing = [row["unit_id"] for row in results if row["classification"] == "missing"]
        raise CorrectedAggregationError(
            f"corrected R1 is incomplete; {len(missing)} units are missing"
        )

    realtime_videos = _realtime_video_map(realtime_index_path.resolve())
    for row in results:
        row["realtime_video_path"] = realtime_videos.get(str(row["unit_id"]))

    retained_costs = [
        row["provider_cost_usd"]
        for row in results
        if row["execution_origin"] == "retained_rejudged"
        and _finite_cost(row.get("provider_cost_usd")) is not None
    ]
    new_costs = [
        row["provider_cost_usd"]
        for row in results
        if row["execution_origin"] in {"fresh_corrected", "replacement"}
        and _finite_cost(row.get("provider_cost_usd")) is not None
    ]
    incomplete_cost_records = _superseded_incomplete_costs(fresh_units)
    incomplete_costs = [
        value
        for value in incomplete_cost_records
        if _finite_cost(value) is not None
    ]
    retained_total = sum(float(value) for value in retained_costs)
    new_total = sum(float(value) for value in new_costs)
    incomplete_total = sum(float(value) for value in incomplete_costs)
    retained_unknown = sum(
        row["execution_origin"] == "retained_rejudged"
        and row["classification"] != "missing"
        and _finite_cost(row.get("provider_cost_usd")) is None
        for row in results
    )
    new_unknown = sum(
        row["execution_origin"] in {"fresh_corrected", "replacement"}
        and row["classification"] != "missing"
        and _finite_cost(row.get("provider_cost_usd")) is None
        for row in results
    )
    incomplete_unknown = sum(value is None for value in incomplete_cost_records)
    scheduled_unit_ids = {
        str(record["unit_id"]) for record in scheduler_attempt_costs
    }
    direct_attempt_costs = [
        {
            "unit_id": row["unit_id"],
            "attempt_number": 1,
            "selected": True,
            "provider_cost_usd": row["provider_cost_usd"],
        }
        for row in results
        if row["execution_origin"] in {"fresh_corrected", "replacement"}
        and row["classification"] != "missing"
        and row["unit_id"] not in scheduled_unit_ids
    ]
    all_new_attempt_costs = scheduler_attempt_costs + direct_attempt_costs
    all_new_known_total = sum(
        float(record["provider_cost_usd"])
        for record in all_new_attempt_costs
        if _finite_cost(record.get("provider_cost_usd")) is not None
    )
    all_new_unknown = sum(
        _finite_cost(record.get("provider_cost_usd")) is None
        for record in all_new_attempt_costs
    )
    retry_attempt_costs = [
        record for record in all_new_attempt_costs if record["selected"] is False
    ]
    retry_known_total = sum(
        float(record["provider_cost_usd"])
        for record in retry_attempt_costs
        if _finite_cost(record.get("provider_cost_usd")) is not None
    )
    retry_unknown = sum(
        _finite_cost(record.get("provider_cost_usd")) is None
        for record in retry_attempt_costs
    )
    overall = _summary(results)
    return {
        "artifact_type": "b2_corrected_r1_aggregate",
        "schema_version": "1.0",
        "audit_identity": AUDIT_IDENTITY,
        "formal_episode": False,
        "formal_denominator_entry": False,
        "source_formal_plan": {
            "planned_denominator": 210,
            "modified": False,
            "r2_r3_dispatched": False,
        },
        "complete": overall["missing"] == 0,
        "overall": overall,
        "per_model": _group(results, "model_id"),
        "per_robot": _group(results, "robot_configuration_id"),
        "per_task": _group(results, "task_id"),
        "non_evaluable_units": [
            {
                "unit_id": row["unit_id"],
                "classification": row["classification"],
                "execution_origin": row["execution_origin"],
            }
            for row in results
            if row["evaluable"] is not True
        ],
        "costs": {
            "currency": "USD",
            "original_retained_19_known_cost_usd": retained_total,
            "original_retained_19_unknown_cost_units": retained_unknown,
            "original_superseded_incomplete_2_known_cost_usd": incomplete_total,
            "original_superseded_incomplete_2_unknown_cost_units": incomplete_unknown,
            "original_related_21_known_cost_usd": retained_total + incomplete_total,
            "new_corrected_51_known_cost_usd": new_total,
            "new_corrected_51_unknown_cost_units": new_unknown,
            "new_corrected_all_attempts_known_spend_usd": all_new_known_total,
            "new_corrected_all_attempts_unknown_cost_attempts": all_new_unknown,
            "new_corrected_attempt_count": len(all_new_attempt_costs),
            "new_corrected_retry_attempt_count": len(retry_attempt_costs),
            "retry_attempts_known_spend_usd": retry_known_total,
            "retry_attempts_unknown_cost_attempts": retry_unknown,
            "corrected_evidence_known_cost_usd": retained_total + new_total,
            "corrected_evidence_unknown_cost_units": retained_unknown + new_unknown,
            "actual_related_known_spend_usd": (
                retained_total + incomplete_total + all_new_known_total
            ),
            "actual_related_unknown_cost_units": (
                retained_unknown + incomplete_unknown + all_new_unknown
            ),
            "retained_rejudication_additional_model_cost_usd": 0.0,
            "old_batch001_manual_sidecar_included": False,
            "note": (
                "The two superseded incomplete M2 costs are disclosed as actual "
                "prior spend but excluded from corrected evidence cost. Corrected "
                "evidence uses each scheduler's selected terminal; actual spend "
                "includes every infrastructure-retry attempt."
            ),
        },
        "video_index": {
            "retained_realtime_index_path": (
                _relative(realtime_index_path)
                if realtime_index_path.is_file()
                else None
            ),
            "retained_realtime_copies": len(realtime_videos),
            "fresh_video_paths_available": sum(
                row["execution_origin"] in {"fresh_corrected", "replacement"}
                and isinstance(row.get("video_path"), str)
                for row in results
            ),
            "units": [
                {
                    "unit_id": row["unit_id"],
                    "execution_origin": row["execution_origin"],
                    "episode_video_path": row.get("video_path"),
                    "retained_realtime_copy_path": row.get("realtime_video_path"),
                }
                for row in results
            ],
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--sidecar-root", type=Path, action="append")
    parser.add_argument("--fresh-root", type=Path, action="append", default=[])
    parser.add_argument("--scheduler", type=Path, action="append", default=[])
    parser.add_argument("--realtime-index", type=Path, default=DEFAULT_REALTIME_INDEX)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    sidecar_roots = args.sidecar_root or [DEFAULT_SIDECARS]
    try:
        aggregate = aggregate_corrected(
            manifest_path=args.manifest,
            sidecar_roots=sidecar_roots,
            fresh_roots=args.fresh_root,
            scheduler_paths=args.scheduler,
            realtime_index_path=args.realtime_index,
            require_complete=args.require_complete,
        )
    except CorrectedAggregationError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(aggregate, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "complete": aggregate["complete"],
                "overall": aggregate["overall"],
                "costs": aggregate["costs"],
                "output": str(args.output.resolve()),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
