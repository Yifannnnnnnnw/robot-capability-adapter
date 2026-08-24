#!/usr/bin/env python3
"""Aggregate the 154 new executions into the selected corrected 210 audit."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import json
import math
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
PROFILE_ROOT = HERE.parent
REPOSITORY_ROOT = HERE.parents[3]
DEFAULT_MANIFEST = PROFILE_ROOT / "config/manifest.json"
DEFAULT_R1_AGGREGATE = (
    REPOSITORY_ROOT
    / "experiment/experiment1b_use/corrected_r1/analysis/corrected_r1_aggregate.json"
)
DEFAULT_OUTPUT = HERE / "corrected_r123_v2_aggregate.json"
AUDIT_IDENTITY = {
    "document_id": "AA2-B2-CORRECTED-R123-V2",
    "revision": "1.0.0",
}
R1_IDENTITY = {"document_id": "AA2-B2-CORRECTED-R1", "revision": "1.0.0"}
PREFIX = "b2_corrected_r123_v2"
MODELS = ("M1", "M2", "M3", "M4", "M5", "M6", "M8")
TASK_ORDER = (
    "mw_push_to_goal",
    "mw_sweep_into_goal",
    "mw_pick_place",
    "GO2-T02",
    "GO2-T03",
    "GO2-T06",
    "GO2-T16",
    "GO2-T17",
    "mw_pick_place_wall",
    "mw_dial_turn",
)
CHANGED_TASKS = {"GO2-T17", "mw_dial_turn"}
EVALUABLE = {"harness_pass", "harness_fail"}
CLASSIFICATIONS = {
    *EVALUABLE,
    "controller_failure",
    "model_failure",
    "evidence_incomplete",
    "infrastructure_failure",
}


class R123V2AggregationError(RuntimeError):
    """Raised when evidence cannot be uniquely and safely accounted for."""


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise R123V2AggregationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise R123V2AggregationError(f"{label} must contain one object: {path}")
    return value


def _write_object(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(path.resolve())


def _evidence_path(raw: Any, *, relative_to: Path) -> Path | None:
    if not isinstance(raw, str) or not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.resolve()
    local = (relative_to / candidate).resolve()
    return local if local.exists() else (REPOSITORY_ROOT / candidate).resolve()


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise R123V2AggregationError(f"{label} must be a non-negative integer")
    return value


def _finite_cost(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0.0 else None


def _provider_pins(manifest: Mapping[str, Any], *, manifest_path: Path) -> dict[str, str]:
    paths = manifest.get("paths")
    if not isinstance(paths, Mapping):
        raise R123V2AggregationError("manifest paths are absent")
    provider_manifest_path = _evidence_path(
        paths.get("provider_manifest"), relative_to=manifest_path.parent
    )
    if provider_manifest_path is None or not provider_manifest_path.is_file():
        raise R123V2AggregationError("provider manifest is absent")
    provider_manifest = _read_object(provider_manifest_path, label="provider manifest")
    runtime_configs = provider_manifest.get("provider_runtime_configs")
    if not isinstance(runtime_configs, Mapping) or set(runtime_configs) != set(MODELS):
        raise R123V2AggregationError("provider manifest model set changed")
    pins: dict[str, str] = {}
    for model_id in MODELS:
        config_path = _evidence_path(
            runtime_configs[model_id], relative_to=provider_manifest_path.parent
        )
        if config_path is None or not config_path.is_file():
            raise R123V2AggregationError(f"provider config is absent for {model_id}")
        config = _read_object(config_path, label=f"{model_id} provider config")
        exact = config.get("exact_model_id")
        if config.get("backbone_id") != model_id or not isinstance(exact, str) or not exact:
            raise R123V2AggregationError(f"provider identity is invalid for {model_id}")
        pins[model_id] = exact
    return pins


def _empty_usage(*, provider_record_path: str | None = None) -> dict[str, Any]:
    return {
        "attempts": 1,
        "provider_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "known_call_cost_usd": 0.0,
        "fully_priced_attempt_cost_usd": 0.0,
        "unknown_cost_calls": 0,
        "unknown_cost_attempts": 0,
        "provider_record_path": provider_record_path,
    }


def _provider_usage(
    path: Path | None,
    *,
    expected_model: str,
    expected_unit: Mapping[str, Any],
    profile: str,
) -> dict[str, Any]:
    if path is None or not path.is_file():
        return _empty_usage()
    record = _read_object(path, label=f"{profile} provider record")
    unit = record.get("unit")
    if not isinstance(unit, Mapping):
        raise R123V2AggregationError(f"provider unit is absent: {path}")
    for field in ("robot_configuration_id", "task_id", "model_id", "replicate_id"):
        if unit.get(field) != expected_unit.get(field):
            raise R123V2AggregationError(
                f"provider {field} mismatch for {expected_unit.get('unit_id')}"
            )
    if profile == "new":
        if (
            record.get("artifact_type") != f"{PREFIX}_provider_record"
            or record.get("audit_identity") != AUDIT_IDENTITY
            or unit.get("unit_id") != expected_unit.get("unit_id")
        ):
            raise R123V2AggregationError(
                f"foreign provider record for {expected_unit.get('unit_id')}"
            )
    elif record.get("artifact_type") not in {
        "b2_corrected_r1_provider_record",
        "b2_formal_provider_record",
    }:
        raise R123V2AggregationError(f"foreign source-R1 provider record: {path}")

    calls = record.get("calls")
    if not isinstance(calls, list) or not all(isinstance(call, Mapping) for call in calls):
        raise R123V2AggregationError(f"provider calls are invalid: {path}")
    input_tokens = 0
    output_tokens = 0
    known_cost = 0.0
    unknown_cost_calls = 0
    for index, call in enumerate(calls):
        if call.get("requested_model") != expected_model:
            raise R123V2AggregationError(
                f"provider requested-model mismatch at call {index}: {path}"
            )
        if call.get("status") == "success" and call.get("returned_model") != expected_model:
            raise R123V2AggregationError(
                f"provider returned-model mismatch at call {index}: {path}"
            )
        input_tokens += _nonnegative_int(
            call.get("input_tokens") or 0, label=f"call {index} input_tokens"
        )
        output_tokens += _nonnegative_int(
            call.get("output_tokens") or 0, label=f"call {index} output_tokens"
        )
        cost = _finite_cost(call.get("cost_usd"))
        if cost is None:
            unknown_cost_calls += 1
        else:
            known_cost += cost
    if record.get("input_tokens") != input_tokens or record.get("output_tokens") != output_tokens:
        raise R123V2AggregationError(f"provider token totals disagree: {path}")
    total_cost = _finite_cost(record.get("total_cost_usd"))
    if unknown_cost_calls == 0 and (
        total_cost is None or not math.isclose(total_cost, known_cost, abs_tol=1e-9)
    ):
        raise R123V2AggregationError(f"provider cost total disagrees: {path}")
    if unknown_cost_calls and total_cost is not None:
        raise R123V2AggregationError(f"partially priced provider record has a total: {path}")
    return {
        "attempts": 1,
        "provider_calls": len(calls),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "known_call_cost_usd": known_cost,
        "fully_priced_attempt_cost_usd": total_cost or 0.0,
        "unknown_cost_calls": unknown_cost_calls,
        "unknown_cost_attempts": int(unknown_cost_calls > 0),
        "provider_record_path": _relative(path),
    }


def _usage_summary(values: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fields = (
        "attempts",
        "provider_calls",
        "input_tokens",
        "output_tokens",
        "unknown_cost_calls",
        "unknown_cost_attempts",
    )
    result = {field: sum(int(value.get(field, 0)) for value in values) for field in fields}
    result["known_call_cost_usd"] = sum(
        float(value.get("known_call_cost_usd", 0.0)) for value in values
    )
    result["fully_priced_attempt_cost_usd"] = sum(
        float(value.get("fully_priced_attempt_cost_usd", 0.0)) for value in values
    )
    return result


def _terminal_usage(
    terminal: Mapping[str, Any],
    *,
    terminal_path: Path,
    unit: Mapping[str, Any],
    expected_model: str,
) -> dict[str, Any]:
    provider_path = _evidence_path(
        terminal.get("provider_record_path"), relative_to=terminal_path.parent
    )
    usage = _provider_usage(
        provider_path,
        expected_model=expected_model,
        expected_unit=unit,
        profile="new",
    )
    expected_fields = {
        "provider_calls": usage["provider_calls"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
    }
    for field, expected in expected_fields.items():
        if terminal.get(field) != expected:
            raise R123V2AggregationError(
                f"terminal/provider {field} disagree for {unit['unit_id']}"
            )
    terminal_cost = _finite_cost(terminal.get("total_cost_usd"))
    if provider_path is None or not provider_path.is_file():
        if terminal_cost not in {None, 0.0}:
            raise R123V2AggregationError(
                f"provider-free terminal has nonzero cost for {unit['unit_id']}"
            )
        return usage
    if usage["unknown_cost_calls"] == 0:
        if terminal_cost is None or not math.isclose(
            terminal_cost, usage["fully_priced_attempt_cost_usd"], abs_tol=1e-9
        ):
            raise R123V2AggregationError(
                f"terminal/provider cost disagree for {unit['unit_id']}"
            )
    elif terminal_cost is not None:
        raise R123V2AggregationError(
            f"partially priced terminal has a total for {unit['unit_id']}"
        )
    return usage


def _validate_terminal(
    terminal: Mapping[str, Any],
    *,
    terminal_path: Path,
    unit: Mapping[str, Any],
    expected_model: str,
) -> dict[str, Any]:
    unit_id = str(unit["unit_id"])
    if (
        terminal.get("artifact_type") != f"{PREFIX}_unit_terminal"
        or terminal.get("audit_identity") != AUDIT_IDENTITY
        or terminal.get("formal_episode") is not False
        or terminal.get("formal_denominator_entry") is not False
    ):
        raise R123V2AggregationError(f"terminal identity is invalid for {unit_id}")
    for field in (
        "unit_id",
        "robot_configuration_id",
        "task_id",
        "model_id",
        "replicate_id",
        "execution_origin",
    ):
        if terminal.get(field) != unit.get(field):
            raise R123V2AggregationError(f"terminal {field} mismatch for {unit_id}")
    if unit.get("replicate_id") == "R1":
        terminal_superseded = _evidence_path(
            terminal.get("superseded_terminal_path"), relative_to=terminal_path.parent
        )
        manifest_superseded = _evidence_path(
            unit.get("superseded_terminal_path"), relative_to=REPOSITORY_ROOT
        )
        if (
            terminal_superseded is None
            or manifest_superseded is None
            or terminal_superseded != manifest_superseded
        ):
            raise R123V2AggregationError(
                f"replacement lineage mismatch for {unit_id}"
            )

    classification = terminal.get("classification")
    if classification not in CLASSIFICATIONS:
        raise R123V2AggregationError(f"unknown classification for {unit_id}")
    evaluable = classification in EVALUABLE
    success = classification == "harness_pass"
    if terminal.get("evaluable") is not evaluable or terminal.get("success") is not success:
        raise R123V2AggregationError(f"terminal outcome flags disagree for {unit_id}")
    identity = terminal.get("model_identity")
    harness = terminal.get("harness")
    returned = identity.get("returned_models") if isinstance(identity, Mapping) else None
    identity_exact = (
        isinstance(returned, list)
        and bool(returned)
        and all(value == expected_model for value in returned)
    )
    if (
        not isinstance(identity, Mapping)
        or identity.get("requested_model") != expected_model
        or not isinstance(returned, list)
        or any(value != expected_model for value in returned)
        or identity.get("exact_match") is not identity_exact
    ):
        raise R123V2AggregationError(f"terminal model identity is invalid for {unit_id}")
    if evaluable:
        expected_verdict = "PASS" if success else "FAIL"
        if (
            identity.get("exact_match") is not True
            or not isinstance(returned, list)
            or not returned
            or any(value != expected_model for value in returned)
            or not isinstance(harness, Mapping)
            or harness.get("physical_harness_verdict") != expected_verdict
            or not isinstance(harness.get("physical_integrity_passed"), bool)
            or (success and harness.get("physical_integrity_passed") is not True)
            or harness.get("video_complete") is not True
        ):
            raise R123V2AggregationError(
                f"evaluable result lacks identity/Harness/video evidence: {unit_id}"
            )
        video_path = _evidence_path(
            terminal.get("video_path"), relative_to=terminal_path.parent
        )
        if video_path is None or not video_path.is_file():
            raise R123V2AggregationError(f"evaluable video is absent for {unit_id}")
    usage = _terminal_usage(
        terminal,
        terminal_path=terminal_path,
        unit=unit,
        expected_model=expected_model,
    )
    if evaluable and usage["provider_calls"] == 0:
        raise R123V2AggregationError(f"evaluable result has no provider call: {unit_id}")
    commit = terminal.get("code_version", {}).get("git_commit")
    if not isinstance(commit, str) or not commit:
        raise R123V2AggregationError(f"code commit is absent for {unit_id}")
    return {
        **{field: terminal.get(field) for field in (
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
        "terminal_path": _relative(terminal_path),
        "episode_record_path": terminal.get("episode_record_path"),
        "video_path": terminal.get("video_path"),
        "code_commit": commit,
        "model_identity": identity,
        "harness": harness,
        "usage": usage,
    }


def _source_provider_path(result: Mapping[str, Any]) -> str | None:
    direct = result.get("provider_record_path")
    if isinstance(direct, str) and direct:
        return direct
    source = result.get("source_evidence")
    value = source.get("original_provider_record_path") if isinstance(source, Mapping) else None
    return value if isinstance(value, str) and value else None


def _source_episode_path(result: Mapping[str, Any]) -> str | None:
    direct = result.get("episode_record_path")
    if isinstance(direct, str) and direct:
        return direct
    source = result.get("source_evidence")
    value = source.get("original_episode_record_path") if isinstance(source, Mapping) else None
    return value if isinstance(value, str) and value else None


def _validate_source_r1(
    path: Path,
    *,
    pins: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    aggregate = _read_object(path, label="source corrected-R1 aggregate")
    raw_results = aggregate.get("results")
    if (
        aggregate.get("artifact_type") != "b2_corrected_r1_aggregate"
        or aggregate.get("audit_identity") != R1_IDENTITY
        or aggregate.get("formal_episode") is not False
        or aggregate.get("complete") is not True
        or aggregate.get("overall", {}).get("planned") != 70
        or not isinstance(raw_results, list)
        or len(raw_results) != 70
    ):
        raise R123V2AggregationError("source corrected-R1 aggregate is incompatible")
    expected = {
        (task_id, model_id, "R1")
        for task_id in TASK_ORDER
        for model_id in MODELS
    }
    actual = {
        (item.get("task_id"), item.get("model_id"), item.get("replicate_id"))
        for item in raw_results
        if isinstance(item, Mapping)
    }
    if actual != expected:
        raise R123V2AggregationError("source corrected-R1 cells changed")
    retained: list[dict[str, Any]] = []
    superseded: list[dict[str, Any]] = []
    for raw in raw_results:
        if not isinstance(raw, Mapping):
            raise R123V2AggregationError("source corrected-R1 result is invalid")
        model_id = str(raw["model_id"])
        provider_path = _evidence_path(
            _source_provider_path(raw), relative_to=path.parent
        )
        usage = _provider_usage(
            provider_path,
            expected_model=pins[model_id],
            expected_unit=raw,
            profile="old",
        )
        common = {
            **dict(raw),
            "source_unit_id": raw.get("unit_id"),
            "source_profile": "corrected_r1",
            "usage": usage,
        }
        if raw.get("task_id") in CHANGED_TASKS:
            superseded.append(common)
        else:
            common["unit_id"] = (
                "b2-corrected-r123-v2::"
                f"{raw['robot_configuration_id']}::{raw['task_id']}::"
                f"{raw['model_id']}::R1"
            )
            common["execution_origin"] = "retained_corrected_r1"
            retained.append(common)
    if len(retained) != 56 or len(superseded) != 14:
        raise R123V2AggregationError("source corrected-R1 56/14 split changed")
    return retained, superseded, aggregate


def _all_source_r1_attempt_usage(
    *,
    source_aggregate_path: Path,
    source_results: Sequence[Mapping[str, Any]],
    pins: Mapping[str, str],
) -> list[dict[str, Any]]:
    source_root = source_aggregate_path.parent.parent
    usages: list[dict[str, Any]] = []
    retained = [item for item in source_results if item.get("execution_origin") == "retained_rejudged"]
    if len(retained) != 19:
        raise R123V2AggregationError("source corrected-R1 retained count changed")
    for result in retained:
        path = _evidence_path(_source_provider_path(result), relative_to=source_aggregate_path.parent)
        usages.append(
            _provider_usage(
                path,
                expected_model=pins[str(result["model_id"])],
                expected_unit=result,
                profile="old",
            )
        )

    scheduler_paths = sorted((source_root / "runs").glob("*/scheduler.json"))
    selected_units = 0
    attempt_paths: set[Path] = set()
    for scheduler_path in scheduler_paths:
        scheduler = _read_object(scheduler_path, label="source corrected-R1 scheduler")
        records = scheduler.get("records")
        if (
            scheduler.get("artifact_type") != "b2_corrected_r1_scheduler"
            or scheduler.get("audit_identity") != R1_IDENTITY
            or not isinstance(records, list)
        ):
            raise R123V2AggregationError(f"source scheduler is incompatible: {scheduler_path}")
        selected_units += len(records)
        for record in records:
            if not isinstance(record, Mapping) or not isinstance(record.get("attempts"), list):
                raise R123V2AggregationError(f"source attempt history is invalid: {scheduler_path}")
            for attempt in record["attempts"]:
                if not isinstance(attempt, Mapping):
                    raise R123V2AggregationError("source attempt is invalid")
                terminal_path = _evidence_path(
                    attempt.get("terminal_path"), relative_to=scheduler_path.parent
                )
                if terminal_path is None or not terminal_path.is_file() or terminal_path in attempt_paths:
                    raise R123V2AggregationError("source attempt terminal is absent/duplicated")
                attempt_paths.add(terminal_path)
                terminal = _read_object(terminal_path, label="source attempt terminal")
                model_id = str(terminal.get("model_id"))
                provider_path = _evidence_path(
                    terminal.get("provider_record_path"), relative_to=terminal_path.parent
                )
                usages.append(
                    _provider_usage(
                        provider_path,
                        expected_model=pins[model_id],
                        expected_unit=terminal,
                        profile="old",
                    )
                )
    if len(scheduler_paths) != 8 or selected_units != 51 or len(attempt_paths) != 52:
        raise R123V2AggregationError("source corrected-R1 scheduler accounting changed")

    source_manifest = _read_object(source_root / "config/manifest.json", label="source R1 manifest")
    prior = [
        item
        for item in source_manifest.get("fresh_units", [])
        if isinstance(item, Mapping) and item.get("execution_origin") == "replacement"
    ]
    if len(prior) != 2:
        raise R123V2AggregationError("source corrected-R1 prior replacement count changed")
    for unit in prior:
        terminal_path = _evidence_path(
            unit.get("original_incomplete_terminal_path"), relative_to=source_root
        )
        if terminal_path is None or not terminal_path.is_file():
            raise R123V2AggregationError("source prior terminal is absent")
        terminal = _read_object(terminal_path, label="source prior terminal")
        provider_path = _evidence_path(
            terminal.get("provider_record_path"), relative_to=terminal_path.parent
        )
        usages.append(
            _provider_usage(
                provider_path,
                expected_model=pins[str(unit["model_id"])],
                expected_unit=unit,
                profile="old",
            )
        )
    if len(usages) != 73:
        raise R123V2AggregationError("source corrected-R1 actual attempt count is not 73")
    return usages


def _thresholds(task_suite_path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    suite = _read_object(task_suite_path, label="corrected-R123-v2 task suite")
    if (
        suite.get("artifact_type") != f"{PREFIX}_task_suite"
        or suite.get("audit_identity") != AUDIT_IDENTITY
    ):
        raise R123V2AggregationError("task suite identity is invalid")
    values: dict[tuple[str, str], dict[str, Any]] = {}
    for robot in suite.get("robot_suites", []):
        if not isinstance(robot, Mapping):
            raise R123V2AggregationError("task suite robot entry is invalid")
        for task in robot.get("tasks", []):
            if not isinstance(task, Mapping):
                raise R123V2AggregationError("task suite task entry is invalid")
            task_id = str(task.get("task_id"))
            for clause in task.get("private_scoring_clauses", []):
                if not isinstance(clause, Mapping):
                    raise R123V2AggregationError("scoring clause is invalid")
                key = (task_id, str(clause.get("clause_id")))
                threshold = clause.get("threshold")
                comparator = clause.get("comparator")
                if key in values or comparator not in {"<=", ">=", "=="} or not isinstance(
                    threshold, (int, float)
                ):
                    raise R123V2AggregationError(f"unsupported scoring clause: {key}")
                values[key] = {
                    "metric": clause.get("metric"),
                    "unit": clause.get("unit"),
                    "comparator": comparator,
                    "threshold": float(threshold),
                }
    return values


def _episode_evidence(
    result: Mapping[str, Any],
    *,
    thresholds: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_path = (
        result.get("episode_record_path")
        if result.get("source_profile") != "corrected_r1"
        else _source_episode_path(result)
    )
    path = _evidence_path(raw_path, relative_to=REPOSITORY_ROOT)
    margins: list[dict[str, Any]] = []
    video_entry = {
        "unit_id": result.get("unit_id"),
        "task_id": result.get("task_id"),
        "model_id": result.get("model_id"),
        "replicate_id": result.get("replicate_id"),
        "evaluable": result.get("evaluable"),
        "success": result.get("success"),
        "video_complete": result.get("harness", {}).get("video_complete")
        if isinstance(result.get("harness"), Mapping)
        else False,
        "video_path": result.get("video_path"),
        "episode_record_path": _relative(path) if path is not None else None,
    }
    if path is None or not path.is_file():
        if result.get("evaluable") is True:
            raise R123V2AggregationError(
                f"evaluable episode record is absent for {result.get('unit_id')}"
            )
        return margins, video_entry
    record = _read_object(path, label="selected episode record")
    if result.get("source_profile") != "corrected_r1":
        record_unit = record.get("unit")
        if (
            record.get("artifact_type") != f"{PREFIX}_episode_record"
            or record.get("audit_identity") != AUDIT_IDENTITY
            or not isinstance(record_unit, Mapping)
            or record_unit.get("unit_id") != result.get("unit_id")
        ):
            raise R123V2AggregationError(
                f"selected episode identity is invalid for {result.get('unit_id')}"
            )
    episode = record.get("episode")
    harness = episode.get("harness") if isinstance(episode, Mapping) else None
    if not isinstance(harness, Mapping):
        if result.get("evaluable") is True:
            raise R123V2AggregationError(
                f"evaluable episode has no trusted Harness: {result.get('unit_id')}"
            )
        return margins, video_entry
    if result.get("evaluable") is True:
        video = harness.get("video")
        if (
            not isinstance(video, Mapping)
            or video.get("complete") is not True
            or video.get("decodable") is not True
            or video.get("width") != 800
            or video.get("height") != 600
        ):
            raise R123V2AggregationError(
                f"video decode/dimensions are invalid for {result.get('unit_id')}"
            )
    clauses = harness.get("task_clause_results")
    if not isinstance(clauses, list):
        return margins, video_entry
    for clause in clauses:
        if not isinstance(clause, Mapping):
            raise R123V2AggregationError("episode clause result is invalid")
        key = (str(result.get("task_id")), str(clause.get("clause_id")))
        definition = thresholds.get(key)
        measurement = clause.get("measurement_value")
        if definition is None or isinstance(measurement, bool) or not isinstance(
            measurement, (int, float)
        ) or not math.isfinite(float(measurement)):
            continue
        value = float(measurement)
        threshold = float(definition["threshold"])
        comparator = str(definition["comparator"])
        signed_margin = (
            threshold - value
            if comparator == "<="
            else value - threshold
            if comparator == ">="
            else -abs(value - threshold)
        )
        margins.append(
            {
                "unit_id": result.get("unit_id"),
                "task_id": result.get("task_id"),
                "model_id": result.get("model_id"),
                "replicate_id": result.get("replicate_id"),
                "clause_id": clause.get("clause_id"),
                "metric": definition.get("metric"),
                "unit": definition.get("unit"),
                "measurement_value": value,
                "comparator": comparator,
                "threshold": threshold,
                "signed_margin": signed_margin,
                "task_metric_passed": clause.get("task_metric_passed"),
            }
        )
    return margins, video_entry


def _summary(
    planned: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    successes = sum(item.get("success") is True for item in results)
    evaluable = sum(item.get("evaluable") is True for item in results)
    usage = _usage_summary(
        [item["usage"] for item in results if isinstance(item.get("usage"), Mapping)]
    )
    return {
        "planned": len(planned),
        "executed": len(results),
        "successes": successes,
        "failures": evaluable - successes,
        "evaluable_denominator": evaluable,
        "non_evaluable": len(results) - evaluable,
        "missing": len(planned) - len(results),
        "planned_success_rate": successes / len(planned) if planned else None,
        "evaluable_success_rate": successes / evaluable if evaluable else None,
        "status_counts": dict(
            sorted(Counter(str(item["classification"]) for item in results).items())
        ),
        **usage,
    }


def _grouped(
    units: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    field: str,
) -> dict[str, Any]:
    keys: list[str] = []
    for unit in units:
        value = str(unit[field])
        if value not in keys:
            keys.append(value)
    return {
        key: _summary(
            [unit for unit in units if str(unit[field]) == key],
            [result for result in results if str(result[field]) == key],
        )
        for key in keys
    }


def _selected_plan() -> list[dict[str, str]]:
    return [
        {
            "unit_id": (
                "b2-corrected-r123-v2::"
                f"{'unitree-go2-stock-12dof' if task_id.startswith('GO2-') else 'robotstudio_so101'}::"
                f"{task_id}::{model_id}::{replicate_id}"
            ),
            "robot_configuration_id": (
                "unitree-go2-stock-12dof"
                if task_id.startswith("GO2-")
                else "robotstudio_so101"
            ),
            "task_id": task_id,
            "model_id": model_id,
            "replicate_id": replicate_id,
        }
        for replicate_id in ("R1", "R2", "R3")
        for task_id in TASK_ORDER
        for model_id in MODELS
    ]


def aggregate(
    *,
    scheduler_paths: Sequence[Path],
    manifest_path: Path = DEFAULT_MANIFEST,
    source_r1_aggregate_path: Path = DEFAULT_R1_AGGREGATE,
    require_complete: bool = False,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = _read_object(manifest_path, label="corrected-R123-v2 manifest")
    units = manifest.get("fresh_units")
    if (
        manifest.get("artifact_type") != f"{PREFIX}_manifest"
        or manifest.get("audit_identity") != AUDIT_IDENTITY
        or manifest.get("formal_episode") is not False
        or manifest.get("formal_denominator_entry") is not False
        or manifest.get("execution_units") != 154
        or manifest.get("fresh_execution_units") != 140
        or manifest.get("replacement_units") != 14
        or manifest.get("retained_selected_units") != 56
        or manifest.get("selected_combined_plan") != 210
        or manifest.get("superseded_r1_units") != 14
        or not isinstance(units, list)
        or len(units) != 154
    ):
        raise R123V2AggregationError("corrected-R123-v2 manifest/cohort is invalid")
    expected = {
        str(unit["unit_id"]): unit for unit in units if isinstance(unit, Mapping)
    }
    if len(expected) != 154:
        raise R123V2AggregationError("manifest unit IDs are invalid or duplicated")
    pins = _provider_pins(manifest, manifest_path=manifest_path)

    results_by_id: dict[str, dict[str, Any]] = {}
    attempt_usage: list[dict[str, Any]] = []
    retries = 0
    batch_keys: set[tuple[str, str]] = set()
    observed_batch_order: list[str] = []
    attempt_commits: set[str] = set()
    for raw_scheduler_path in scheduler_paths:
        scheduler_path = raw_scheduler_path.resolve()
        scheduler = _read_object(scheduler_path, label="corrected-R123-v2 scheduler")
        records = scheduler.get("records")
        planned_ids = scheduler.get("planned_unit_ids")
        if (
            scheduler.get("artifact_type") != f"{PREFIX}_scheduler"
            or scheduler.get("audit_identity") != AUDIT_IDENTITY
            or scheduler.get("formal_episode") is not False
            or scheduler.get("formal_denominator_entry") is not False
            or scheduler.get("company_workers") != 3
            or scheduler.get("m5_workers") != 1
            or scheduler.get("total_worker_limit") != 4
            or scheduler.get("retry_policy") != manifest.get("retry_policy")
            or not isinstance(records, list)
            or not isinstance(planned_ids, list)
            or len(records) != 7
            or planned_ids
            != [record.get("unit_id") for record in records if isinstance(record, Mapping)]
        ):
            raise R123V2AggregationError(f"scheduler identity/batch is invalid: {scheduler_path}")
        scheduler_manifest_path = _evidence_path(
            scheduler.get("manifest_path"), relative_to=scheduler_path.parent
        )
        expected_gate_path = _evidence_path(
            manifest["paths"]["positive_control_index"],
            relative_to=manifest_path.parent,
        )
        scheduler_gate_path = _evidence_path(
            scheduler.get("positive_control_index_path"),
            relative_to=scheduler_path.parent,
        )
        if (
            scheduler_manifest_path != manifest_path
            or scheduler_gate_path != expected_gate_path
        ):
            raise R123V2AggregationError(
                f"scheduler fixed-input paths disagree: {scheduler_path}"
            )
        batch_units = [expected.get(str(unit_id)) for unit_id in planned_ids]
        if any(unit is None for unit in batch_units):
            raise R123V2AggregationError(f"scheduler contains a foreign unit: {scheduler_path}")
        batch_task_ids = {str(unit["task_id"]) for unit in batch_units if unit is not None}
        batch_replicates = {
            str(unit["replicate_id"]) for unit in batch_units if unit is not None
        }
        batch_models = {str(unit["model_id"]) for unit in batch_units if unit is not None}
        if len(batch_task_ids) != 1 or len(batch_replicates) != 1 or batch_models != set(MODELS):
            raise R123V2AggregationError(f"scheduler is not one seven-model batch: {scheduler_path}")
        batch_key = (next(iter(batch_replicates)), next(iter(batch_task_ids)))
        if (
            scheduler.get("task_filters") != [batch_key[1]]
            or scheduler.get("replicate_filters") != [batch_key[0]]
        ):
            raise R123V2AggregationError(
                f"scheduler filters disagree with its batch: {scheduler_path}"
            )
        if batch_key in batch_keys:
            raise R123V2AggregationError(f"duplicate scheduler batch: {batch_key}")
        batch_keys.add(batch_key)
        observed_batch_order.append(f"{batch_key[0]}/{batch_key[1]}")
        for record in records:
            if not isinstance(record, Mapping):
                raise R123V2AggregationError(f"scheduler record is invalid: {scheduler_path}")
            unit_id = str(record.get("unit_id"))
            unit = expected.get(unit_id)
            if unit is None or unit_id in results_by_id:
                raise R123V2AggregationError(f"foreign/duplicate execution unit: {unit_id}")
            expected_lane = "m5" if unit["model_id"] == "M5" else "company"
            if record.get("provider_lane") != expected_lane:
                raise R123V2AggregationError(f"provider lane mismatch for {unit_id}")
            attempts = record.get("attempts")
            if not isinstance(attempts, list) or len(attempts) not in {1, 2}:
                raise R123V2AggregationError(f"attempt history is invalid for {unit_id}")
            retry_used = len(attempts) == 2
            if record.get("retry_used") is not retry_used:
                raise R123V2AggregationError(f"retry flag disagrees for {unit_id}")
            if retry_used:
                retries += 1
                first = attempts[0]
                if not isinstance(first, Mapping) or first.get("classification") != "infrastructure_failure":
                    raise R123V2AggregationError(f"non-infrastructure retry for {unit_id}")
            attempt_paths: list[Path] = []
            validated_attempts: list[dict[str, Any]] = []
            for number, attempt in enumerate(attempts, start=1):
                if not isinstance(attempt, Mapping):
                    raise R123V2AggregationError(f"attempt is invalid for {unit_id}")
                terminal_path = _evidence_path(
                    attempt.get("terminal_path"), relative_to=scheduler_path.parent
                )
                if attempt.get("attempt_number") != number or terminal_path is None or not terminal_path.is_file():
                    raise R123V2AggregationError(f"attempt terminal is absent for {unit_id}")
                terminal = _read_object(terminal_path, label="execution attempt terminal")
                validated = _validate_terminal(
                    terminal,
                    terminal_path=terminal_path,
                    unit=unit,
                    expected_model=pins[str(unit["model_id"])],
                )
                if (
                    validated["classification"] != attempt.get("classification")
                    or validated["evaluable"] is not attempt.get("evaluable")
                    or validated["success"] is not attempt.get("success")
                ):
                    raise R123V2AggregationError(f"attempt metadata disagree for {unit_id}")
                attempt_paths.append(terminal_path)
                validated_attempts.append(validated)
                attempt_usage.append(validated["usage"])
                attempt_commits.add(str(validated["code_commit"]))
            selected_path = _evidence_path(
                record.get("terminal_path"), relative_to=scheduler_path.parent
            )
            if selected_path != attempt_paths[-1]:
                raise R123V2AggregationError(f"scheduler did not select final attempt for {unit_id}")
            selected = validated_attempts[-1]
            if (
                selected["classification"] != record.get("classification")
                or selected["evaluable"] is not record.get("evaluable")
                or selected["success"] is not record.get("success")
            ):
                raise R123V2AggregationError(f"selected terminal disagrees for {unit_id}")
            results_by_id[unit_id] = selected

    ordered_new = [
        results_by_id[str(unit["unit_id"])]
        for unit in units
        if str(unit["unit_id"]) in results_by_id
    ]
    missing_new = [
        str(unit["unit_id"]) for unit in units if str(unit["unit_id"]) not in results_by_id
    ]
    if require_complete and (missing_new or len(batch_keys) != 22):
        raise R123V2AggregationError(
            f"corrected-R123-v2 is incomplete; {len(missing_new)} units and "
            f"{22 - len(batch_keys)} batches are missing"
        )
    if require_complete and observed_batch_order != manifest.get("dispatch_order"):
        raise R123V2AggregationError(
            "scheduler paths do not follow the sealed 22-batch dispatch order"
        )
    if require_complete and len(attempt_commits) != 1:
        raise R123V2AggregationError(
            "new execution attempts do not share one frozen code commit"
        )

    source_r1_aggregate_path = source_r1_aggregate_path.resolve()
    retained, superseded_raw, source_aggregate = _validate_source_r1(
        source_r1_aggregate_path, pins=pins
    )
    replacement_by_source = {
        str(unit.get("superseded_unit_id")): unit
        for unit in units
        if isinstance(unit, Mapping) and unit.get("replicate_id") == "R1"
    }
    superseded: list[dict[str, Any]] = []
    for old in superseded_raw:
        source_unit_id = str(old["source_unit_id"])
        replacement = replacement_by_source.get(source_unit_id)
        evidence_path = old.get("evidence_path")
        if (
            replacement is None
            or replacement.get("superseded_terminal_path") != evidence_path
        ):
            raise R123V2AggregationError(
                f"manifest superseded lineage mismatch for {source_unit_id}"
            )
        superseded.append(
            {
                "unit_id": source_unit_id,
                "task_id": old.get("task_id"),
                "model_id": old.get("model_id"),
                "replicate_id": "R1",
                "classification": old.get("classification"),
                "success": old.get("success"),
                "terminal_path": evidence_path,
                "video_path": old.get("video_path"),
                "superseded_by_unit_id": replacement.get("unit_id"),
                "superseded_reason": "replacement_new_definition",
                "selected": False,
                "actual_related_spend_included": True,
                "usage": old["usage"],
            }
        )

    selected_plan = _selected_plan()
    unordered_selected_results = [*retained, *ordered_new]
    selected_by_id = {
        str(result["unit_id"]): result for result in unordered_selected_results
    }
    selected_results = [
        selected_by_id[unit["unit_id"]]
        for unit in selected_plan
        if unit["unit_id"] in selected_by_id
    ]
    selected_ids = {str(result["unit_id"]) for result in selected_results}
    if len(selected_ids) != len(selected_results):
        raise R123V2AggregationError("selected results contain duplicate unit IDs")
    expected_selected_ids = {unit["unit_id"] for unit in selected_plan}
    if not selected_ids.issubset(expected_selected_ids):
        raise R123V2AggregationError("selected results contain a foreign unit")
    if require_complete and selected_ids != expected_selected_ids:
        raise R123V2AggregationError("selected corrected 210 is incomplete")

    paths = manifest["paths"]
    task_suite_path = _evidence_path(paths["task_suite"], relative_to=manifest_path.parent)
    if task_suite_path is None or not task_suite_path.is_file():
        raise R123V2AggregationError("task suite is absent")
    threshold_definitions = _thresholds(task_suite_path)
    margins: list[dict[str, Any]] = []
    video_index: list[dict[str, Any]] = []
    for result in selected_results:
        episode_margins, video = _episode_evidence(
            result, thresholds=threshold_definitions
        )
        margins.extend(episode_margins)
        video_index.append(video)

    source_all_usage = _all_source_r1_attempt_usage(
        source_aggregate_path=source_r1_aggregate_path,
        source_results=[
            result for result in source_aggregate["results"] if isinstance(result, Mapping)
        ],
        pins=pins,
    )
    selected_usage = _usage_summary(
        [result["usage"] for result in selected_results]
    )
    actual_usage = _usage_summary([*source_all_usage, *attempt_usage])
    new_overall = _summary(units, ordered_new)
    selected_overall = _summary(selected_plan, selected_results)
    return {
        "artifact_type": f"{PREFIX}_aggregate",
        "schema_version": "1.0",
        "audit_identity": AUDIT_IDENTITY,
        "formal_episode": False,
        "formal_denominator_entry": False,
        "complete": not missing_new and len(batch_keys) == 22,
        "claim_boundary": (
            "formal_episode=false corrected audit; does not replace AA2-B2 formal evidence."
        ),
        "manifest_path": _relative(manifest_path),
        "source_corrected_r1_aggregate_path": _relative(source_r1_aggregate_path),
        "scheduler_paths": [_relative(path) for path in scheduler_paths],
        "execution_154": {
            "overall": new_overall,
            "per_task": _grouped(units, ordered_new, "task_id"),
            "per_model": _grouped(units, ordered_new, "model_id"),
            "per_robot": _grouped(units, ordered_new, "robot_configuration_id"),
            "per_replicate": _grouped(units, ordered_new, "replicate_id"),
            "planned_batches": 22,
            "observed_batches": len(batch_keys),
            "batch_order": observed_batch_order,
            "infrastructure_retries": retries,
            "missing_unit_ids": missing_new,
        },
        "selected_210": {
            "composition": {
                "retained_corrected_r1": 56,
                "replacement_new_definition": 14,
                "fresh_corrected_r2_r3": 140,
            },
            "overall": selected_overall,
            "per_task": _grouped(selected_plan, selected_results, "task_id"),
            "per_model": _grouped(selected_plan, selected_results, "model_id"),
            "per_robot": _grouped(
                selected_plan, selected_results, "robot_configuration_id"
            ),
            "per_replicate": _grouped(
                selected_plan, selected_results, "replicate_id"
            ),
        },
        "usage": {
            "currency": "USD",
            "selected_210": selected_usage,
            "all_attempts_actual_related_spend": actual_usage,
            "source_corrected_r1_actual_attempts": _usage_summary(source_all_usage),
            "new_execution_all_attempts": _usage_summary(attempt_usage),
            "note": (
                "Selected usage excludes the 14 superseded old-definition outcomes. "
                "Actual-related usage includes all 73 source corrected-R1 attempts, "
                "including those 14 outcomes and prior/retry attempts, plus every new attempt."
            ),
        },
        "metric_threshold_margins": margins,
        "video_index": video_index,
        "superseded_r1": superseded,
        "non_evaluable_selected_unit_ids": [
            str(result["unit_id"])
            for result in selected_results
            if result.get("evaluable") is not True
        ],
        "code_commits": sorted(
            {
                str(result["code_commit"])
                for result in ordered_new
                if result.get("code_commit")
            }
        ),
        "selected_results": selected_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scheduler", type=Path, action="append", default=[])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--source-r1-aggregate", type=Path, default=DEFAULT_R1_AGGREGATE
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    try:
        report = aggregate(
            scheduler_paths=args.scheduler,
            manifest_path=args.manifest,
            source_r1_aggregate_path=args.source_r1_aggregate,
            require_complete=args.require_complete,
        )
    except R123V2AggregationError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    _write_object(args.output.resolve(), report)
    print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
