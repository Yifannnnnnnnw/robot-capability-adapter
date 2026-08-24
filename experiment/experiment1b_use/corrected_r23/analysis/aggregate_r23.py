#!/usr/bin/env python3
"""Aggregate corrected R2/R3 schedulers and a corrected-210 combined view."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import json
import math
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CORRECTED_ROOT = HERE.parent
REPOSITORY_ROOT = HERE.parents[3]
DEFAULT_MANIFEST = CORRECTED_ROOT / "config/manifest.json"
DEFAULT_OUTPUT = HERE / "corrected_r23_aggregate.json"
DEFAULT_R1_AGGREGATE = (
    REPOSITORY_ROOT
    / "experiment/experiment1b_use/corrected_r1/analysis/corrected_r1_aggregate.json"
)
AUDIT_IDENTITY = {
    "document_id": "AA2-B2-CORRECTED-R23",
    "revision": "1.0.0",
}
EVALUABLE = {"harness_pass", "harness_fail"}
CLASSIFICATIONS = {
    *EVALUABLE,
    "controller_failure",
    "model_failure",
    "evidence_incomplete",
    "infrastructure_failure",
}


class R23AggregationError(RuntimeError):
    """Raised when R23 evidence cannot be uniquely and safely accounted for."""


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise R23AggregationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise R23AggregationError(f"{label} must contain one JSON object: {path}")
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
    value = Path(raw)
    if value.is_absolute():
        return value.resolve()
    local = (relative_to / value).resolve()
    return local if local.exists() else (REPOSITORY_ROOT / value).resolve()


def _finite_cost(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0.0 else None


def _provider_usage(path: Path | None, *, unit_id: str) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {
            "provider_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "known_cost_usd": 0.0,
            "unknown_cost_calls": 0,
            "provider_record_path": None,
        }
    record = _read_object(path, label="R23 provider record")
    if (
        record.get("artifact_type") != "b2_corrected_r23_provider_record"
        or record.get("audit_identity") != AUDIT_IDENTITY
        or record.get("unit", {}).get("unit_id") != unit_id
    ):
        raise R23AggregationError(f"foreign provider record for {unit_id}")
    calls = record.get("calls")
    if not isinstance(calls, list):
        raise R23AggregationError(f"provider calls are absent for {unit_id}")
    known_cost = 0.0
    unknown_cost_calls = 0
    for call in calls:
        if not isinstance(call, Mapping):
            raise R23AggregationError(f"provider call is invalid for {unit_id}")
        cost = _finite_cost(call.get("cost_usd"))
        if cost is None:
            unknown_cost_calls += 1
        else:
            known_cost += cost
    return {
        "provider_calls": len(calls),
        "input_tokens": int(record.get("input_tokens") or 0),
        "output_tokens": int(record.get("output_tokens") or 0),
        "known_cost_usd": known_cost,
        "unknown_cost_calls": unknown_cost_calls,
        "provider_record_path": _relative(path),
    }


def _validate_terminal(
    terminal: Mapping[str, Any],
    *,
    terminal_path: Path,
    unit: Mapping[str, Any],
) -> dict[str, Any]:
    unit_id = str(unit["unit_id"])
    if (
        terminal.get("artifact_type") != "b2_corrected_r23_unit_terminal"
        or terminal.get("audit_identity") != AUDIT_IDENTITY
        or terminal.get("formal_episode") is not False
        or terminal.get("formal_denominator_entry") is not False
    ):
        raise R23AggregationError(f"terminal identity is invalid for {unit_id}")
    for field in (
        "unit_id",
        "robot_configuration_id",
        "task_id",
        "model_id",
        "replicate_id",
        "execution_origin",
    ):
        if terminal.get(field) != unit.get(field):
            raise R23AggregationError(f"terminal {field} mismatch for {unit_id}")
    classification = terminal.get("classification")
    if classification not in CLASSIFICATIONS:
        raise R23AggregationError(f"unknown classification for {unit_id}")
    evaluable = classification in EVALUABLE
    success = classification == "harness_pass"
    if terminal.get("evaluable") is not evaluable or terminal.get("success") is not success:
        raise R23AggregationError(f"terminal outcome flags disagree for {unit_id}")
    harness = terminal.get("harness")
    model_identity = terminal.get("model_identity")
    if evaluable:
        requested = model_identity.get("requested_model") if isinstance(model_identity, Mapping) else None
        returned = model_identity.get("returned_models") if isinstance(model_identity, Mapping) else None
        expected_verdict = "PASS" if success else "FAIL"
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
        ):
            raise R23AggregationError(
                f"evaluable result lacks identity/Harness/video evidence: {unit_id}"
            )
        video_path = _evidence_path(
            terminal.get("video_path"), relative_to=terminal_path.parent
        )
        if video_path is None or not video_path.is_file():
            raise R23AggregationError(f"evaluable video is absent for {unit_id}")
    provider_path = _evidence_path(
        terminal.get("provider_record_path"), relative_to=terminal_path.parent
    )
    usage = _provider_usage(provider_path, unit_id=unit_id)
    commit = terminal.get("code_version", {}).get("git_commit")
    if not isinstance(commit, str) or not commit:
        raise R23AggregationError(f"code commit is absent for {unit_id}")
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
        "model_identity": model_identity,
        "harness": harness,
        **usage,
    }


def _summary(planned: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    successes = sum(item.get("success") is True for item in results)
    evaluable = sum(item.get("evaluable") is True for item in results)
    failures = evaluable - successes
    non_evaluable = len(results) - evaluable
    planned_count = len(planned)
    return {
        "planned": planned_count,
        "executed": len(results),
        "successes": successes,
        "failures": failures,
        "evaluable_denominator": evaluable,
        "non_evaluable": non_evaluable,
        "missing": planned_count - len(results),
        "planned_success_rate": successes / planned_count if planned_count else None,
        "evaluable_success_rate": successes / evaluable if evaluable else None,
        "status_counts": dict(sorted(Counter(str(item["classification"]) for item in results).items())),
    }


def _grouped(
    units: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    field: str,
) -> dict[str, Any]:
    keys = []
    for unit in units:
        value = str(unit[field])
        if value not in keys:
            keys.append(value)
    return {
        key: _summary(
            [unit for unit in units if unit[field] == key],
            [result for result in results if result[field] == key],
        )
        for key in keys
    }


def _usage_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "attempts": len(records),
        "provider_calls": sum(int(item["provider_calls"]) for item in records),
        "input_tokens": sum(int(item["input_tokens"]) for item in records),
        "output_tokens": sum(int(item["output_tokens"]) for item in records),
        "known_cost_usd": sum(float(item["known_cost_usd"]) for item in records),
        "unknown_cost_calls": sum(int(item["unknown_cost_calls"]) for item in records),
        "unknown_cost_attempts": sum(int(item["unknown_cost_calls"]) > 0 for item in records),
    }


def _merge_summary(r1: Mapping[str, Any], r23: Mapping[str, Any]) -> dict[str, Any]:
    planned = int(r1.get("planned", 0)) + int(r23.get("planned", 0))
    successes = int(r1.get("successes", 0)) + int(r23.get("successes", 0))
    evaluable = int(r1.get("evaluable_denominator", 0)) + int(
        r23.get("evaluable_denominator", 0)
    )
    executed = (
        int(r1.get("planned", 0))
        - int(r1.get("missing", 0))
        + int(r23.get("executed", 0))
    )
    statuses = Counter(r1.get("status_counts", {}))
    statuses.update(r23.get("status_counts", {}))
    return {
        "planned": planned,
        "executed": executed,
        "successes": successes,
        "failures": evaluable - successes,
        "evaluable_denominator": evaluable,
        "non_evaluable": executed - evaluable,
        "missing": planned - executed,
        "planned_success_rate": successes / planned if planned else None,
        "evaluable_success_rate": successes / evaluable if evaluable else None,
        "status_counts": dict(sorted(statuses.items())),
    }


def aggregate(
    *,
    scheduler_paths: Sequence[Path],
    manifest_path: Path = DEFAULT_MANIFEST,
    r1_aggregate_path: Path = DEFAULT_R1_AGGREGATE,
    require_complete: bool = False,
) -> dict[str, Any]:
    manifest = _read_object(manifest_path.resolve(), label="R23 manifest")
    units = manifest.get("fresh_units")
    if (
        manifest.get("artifact_type") != "b2_corrected_r23_manifest"
        or manifest.get("audit_identity") != AUDIT_IDENTITY
        or manifest.get("formal_episode") is not False
        or manifest.get("fresh_execution_units") != 140
        or not isinstance(units, list)
        or len(units) != 140
    ):
        raise R23AggregationError("R23 manifest identity/cohort is invalid")
    expected = {str(unit["unit_id"]): unit for unit in units if isinstance(unit, Mapping)}
    if len(expected) != 140:
        raise R23AggregationError("R23 manifest unit IDs are invalid or duplicated")

    results_by_id: dict[str, dict[str, Any]] = {}
    attempt_usage: list[dict[str, Any]] = []
    retries = 0
    for scheduler_path in scheduler_paths:
        scheduler_path = scheduler_path.resolve()
        scheduler = _read_object(scheduler_path, label="R23 scheduler")
        records = scheduler.get("records")
        if (
            scheduler.get("artifact_type") != "b2_corrected_r23_scheduler"
            or scheduler.get("audit_identity") != AUDIT_IDENTITY
            or scheduler.get("formal_episode") is not False
            or scheduler.get("formal_denominator_entry") is not False
            or scheduler.get("company_workers") != 3
            or scheduler.get("m5_workers") != 1
            or not isinstance(records, list)
        ):
            raise R23AggregationError(f"scheduler identity is invalid: {scheduler_path}")
        planned_ids = scheduler.get("planned_unit_ids")
        if not isinstance(planned_ids, list) or planned_ids != [
            record.get("unit_id") for record in records if isinstance(record, Mapping)
        ]:
            raise R23AggregationError(f"scheduler plan/records disagree: {scheduler_path}")
        for record in records:
            if not isinstance(record, Mapping):
                raise R23AggregationError(f"scheduler record is invalid: {scheduler_path}")
            unit_id = record.get("unit_id")
            if unit_id not in expected or unit_id in results_by_id:
                raise R23AggregationError(f"foreign/duplicate R23 unit: {unit_id!r}")
            attempts = record.get("attempts")
            if not isinstance(attempts, list) or len(attempts) not in {1, 2}:
                raise R23AggregationError(f"attempt history is invalid for {unit_id}")
            if len(attempts) == 2:
                retries += 1
                if (
                    attempts[0].get("classification") != "infrastructure_failure"
                    or record.get("retry_used") is not True
                ):
                    raise R23AggregationError(f"non-infrastructure retry for {unit_id}")
            elif record.get("retry_used") is not False:
                raise R23AggregationError(f"retry flag disagrees for {unit_id}")
            attempt_paths: list[Path] = []
            for number, attempt in enumerate(attempts, start=1):
                terminal_path = _evidence_path(
                    attempt.get("terminal_path"), relative_to=scheduler_path.parent
                )
                if (
                    attempt.get("attempt_number") != number
                    or terminal_path is None
                    or not terminal_path.is_file()
                ):
                    raise R23AggregationError(f"attempt terminal is absent for {unit_id}")
                terminal = _read_object(terminal_path, label="R23 attempt terminal")
                validated = _validate_terminal(
                    terminal,
                    terminal_path=terminal_path,
                    unit=expected[str(unit_id)],
                )
                if (
                    validated["classification"] != attempt.get("classification")
                    or validated["evaluable"] is not attempt.get("evaluable")
                    or validated["success"] is not attempt.get("success")
                ):
                    raise R23AggregationError(f"attempt metadata disagree for {unit_id}")
                attempt_paths.append(terminal_path)
                attempt_usage.append(
                    {
                        "unit_id": unit_id,
                        "model_id": validated["model_id"],
                        "attempt_number": number,
                        "selected": number == len(attempts),
                        **{
                            field: validated[field]
                            for field in (
                                "provider_calls",
                                "input_tokens",
                                "output_tokens",
                                "known_cost_usd",
                                "unknown_cost_calls",
                            )
                        },
                    }
                )
            selected_path = _evidence_path(
                record.get("terminal_path"), relative_to=scheduler_path.parent
            )
            if selected_path != attempt_paths[-1]:
                raise R23AggregationError(f"scheduler did not select final attempt for {unit_id}")
            selected_terminal = _read_object(selected_path, label="selected R23 terminal")
            result = _validate_terminal(
                selected_terminal,
                terminal_path=selected_path,
                unit=expected[str(unit_id)],
            )
            if (
                result["classification"] != record.get("classification")
                or result["evaluable"] is not record.get("evaluable")
                or result["success"] is not record.get("success")
            ):
                raise R23AggregationError(f"selected terminal disagrees for {unit_id}")
            results_by_id[str(unit_id)] = result

    ordered_units = [expected[str(unit["unit_id"])] for unit in units]
    ordered_results = [
        results_by_id[str(unit["unit_id"])]
        for unit in units
        if str(unit["unit_id"]) in results_by_id
    ]
    missing = [str(unit["unit_id"]) for unit in units if str(unit["unit_id"]) not in results_by_id]
    if require_complete and missing:
        raise R23AggregationError(f"corrected R23 is incomplete; {len(missing)} units are missing")
    overall = _summary(ordered_units, ordered_results)
    per_model = _grouped(ordered_units, ordered_results, "model_id")
    per_task = _grouped(ordered_units, ordered_results, "task_id")
    per_robot = _grouped(ordered_units, ordered_results, "robot_configuration_id")
    per_replicate = _grouped(ordered_units, ordered_results, "replicate_id")
    selected_usage = [item for item in attempt_usage if item["selected"]]
    usage_by_model = {
        model_id: _usage_summary(
            [item for item in attempt_usage if item["model_id"] == model_id]
        )
        for model_id in manifest["models"]
    }

    r1 = _read_object(r1_aggregate_path.resolve(), label="corrected R1 aggregate")
    if (
        r1.get("artifact_type") != "b2_corrected_r1_aggregate"
        or r1.get("complete") is not True
        or r1.get("overall", {}).get("planned") != 70
    ):
        raise R23AggregationError("corrected R1 aggregate is incompatible")
    combined = {
        "claim_boundary": (
            "Non-formal corrected R1-R3 audit; not the source AA2-B2 formal cohort."
        ),
        "overall": _merge_summary(r1["overall"], overall),
        "per_model": {
            key: _merge_summary(r1["per_model"][key], per_model[key])
            for key in manifest["models"]
        },
        "per_task": {
            key: _merge_summary(r1["per_task"][key], per_task[key])
            for key in per_task
        },
        "per_robot": {
            key: _merge_summary(r1["per_robot"][key], per_robot[key])
            for key in per_robot
        },
        "costs": {
            "currency": "USD",
            "corrected_evidence_known_cost_usd": (
                float(r1["costs"]["corrected_evidence_known_cost_usd"])
                + _usage_summary(selected_usage)["known_cost_usd"]
            ),
            "actual_related_known_spend_usd": (
                float(r1["costs"]["actual_related_known_spend_usd"])
                + _usage_summary(attempt_usage)["known_cost_usd"]
            ),
            "r23_unknown_cost_calls": _usage_summary(attempt_usage)[
                "unknown_cost_calls"
            ],
            "r1_unknown_cost_units": r1["costs"][
                "actual_related_unknown_cost_units"
            ],
        },
    }
    return {
        "artifact_type": "b2_corrected_r23_aggregate",
        "schema_version": "1.0",
        "audit_identity": AUDIT_IDENTITY,
        "formal_episode": False,
        "formal_denominator_entry": False,
        "complete": not missing,
        "manifest_path": _relative(manifest_path),
        "scheduler_paths": [_relative(path) for path in scheduler_paths],
        "overall": overall,
        "per_model": per_model,
        "per_task": per_task,
        "per_robot": per_robot,
        "per_replicate": per_replicate,
        "usage": {
            "selected_outcomes": _usage_summary(selected_usage),
            "all_attempts_actual_spend": _usage_summary(attempt_usage),
            "per_model_all_attempts": usage_by_model,
            "infrastructure_retries": retries,
        },
        "code_commits": sorted({str(item["code_commit"]) for item in ordered_results}),
        "missing_unit_ids": missing,
        "non_evaluable_unit_ids": [
            str(item["unit_id"]) for item in ordered_results if not item["evaluable"]
        ],
        "results": ordered_results,
        "corrected_210_combined": combined,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scheduler", type=Path, action="append", default=[])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--r1-aggregate", type=Path, default=DEFAULT_R1_AGGREGATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    try:
        report = aggregate(
            scheduler_paths=args.scheduler,
            manifest_path=args.manifest,
            r1_aggregate_path=args.r1_aggregate,
            require_complete=args.require_complete,
        )
    except R23AggregationError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    _write_object(args.output.resolve(), report)
    print(json.dumps(report["overall"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
