"""Direct dict/JSON reporting for mainline cells and configured comparisons.

The report keeps Capability Validation and the later Task Demo as separate outcomes. A paired
report retains every cell in full; its summary is only a view over those cells and is never a
replacement for them.
"""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class ReportingError(ValueError):
    """Raised when a report cannot preserve the required experiment identity."""


_MISSING = object()
_OUTCOME_FIELDS = (
    ("TGCD", "tgcd"),
    ("IVC", "ivc"),
    ("STUDY", "study"),
    ("GENERATE", "generate"),
    ("CapabilityValidation", "capability_validation"),
    ("Repair", "repair"),
    ("TaskDemoController", "task_demo_controller"),
    ("TaskDemo", "task_demo"),
    ("Evolution", "evolution"),
)


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _first_value(source: Mapping[str, Any], names: Sequence[str], default: Any = None) -> Any:
    for name in names:
        if name in source and source[name] is not None:
            return source[name]
    return default


def _bool(source: Mapping[str, Any], names: Sequence[str], default: bool = False) -> bool:
    value = _first_value(source, names, _MISSING)
    return value if isinstance(value, bool) else default


def _non_negative_int(value: Any, *, field: str, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReportingError(f"{field} must be a non-negative integer")
    return value


def _explicit_count(
    source: Mapping[str, Any],
    *,
    kind: str,
    passed_names: Sequence[str],
    total_names: Sequence[str],
) -> tuple[int | None, int | None]:
    nested = source.get(f"{kind}_counts")
    nested_passed = nested.get("passed") if isinstance(nested, Mapping) else None
    nested_total = nested.get("total") if isinstance(nested, Mapping) else None
    passed = _first_value(source, passed_names, nested_passed)
    total = _first_value(source, total_names, nested_total)
    if passed is not None:
        passed = _non_negative_int(passed, field=f"passed {kind} count")
    if total is not None:
        total = _non_negative_int(total, field=f"total {kind} count")
    if passed is not None and total is not None and passed > total:
        raise ReportingError(f"passed {kind} count cannot exceed total {kind} count")
    return passed, total


def _trial_counts(trials: Sequence[Any]) -> dict[str, tuple[int, int]]:
    """Derive counts from concrete trial records without inferring physical execution."""

    groups: dict[str, dict[str, list[bool]]] = {
        "task": defaultdict(list),
        "clause": defaultdict(list),
        "case": defaultdict(list),
    }
    for index, trial in enumerate(trials):
        if not isinstance(trial, Mapping):
            continue
        passed = trial.get("trial_passed")
        passed_bool = passed if isinstance(passed, bool) else False
        case_id = trial.get("case_id", trial.get("trial_id", f"trial-{index}"))
        task_id = trial.get("task_id")
        clause_id = trial.get("source_clause_id", trial.get("clause_id"))
        if case_id is not None:
            groups["case"][str(case_id)].append(passed_bool)
        if task_id is not None:
            groups["task"][str(task_id)].append(passed_bool)
        if task_id is not None and clause_id is not None:
            groups["clause"][f"{task_id}::{clause_id}"].append(passed_bool)

    result: dict[str, tuple[int, int]] = {}
    for kind, values in groups.items():
        result[kind] = (
            sum(1 for outcomes in values.values() if outcomes and all(outcomes)),
            len(values),
        )
    return result


def _video_counts(source: Mapping[str, Any], trials: Sequence[Any]) -> tuple[bool, int, int]:
    explicit_complete = source.get("video_complete")
    explicit_complete_count = _first_value(
        source,
        ("video_complete_count", "complete_video_count"),
        None,
    )
    explicit_total_count = _first_value(
        source,
        ("video_total_count", "total_video_count"),
        None,
    )
    if explicit_complete_count is not None:
        complete_count = _non_negative_int(
            explicit_complete_count, field="video complete count"
        )
    else:
        complete_count = 0
    if explicit_total_count is not None:
        total_count = _non_negative_int(explicit_total_count, field="video total count")
    else:
        total_count = 0

    if explicit_complete is None:
        complete_flags: list[bool] = []
        for trial in trials:
            if isinstance(trial, Mapping):
                video = trial.get("video")
                complete_flags.append(
                    bool(video.get("complete")) if isinstance(video, Mapping) else False
                )
        if complete_flags:
            complete = all(complete_flags)
            if explicit_complete_count is None:
                complete_count = sum(complete_flags)
            if explicit_total_count is None:
                total_count = len(complete_flags)
        else:
            complete = False
    elif isinstance(explicit_complete, bool):
        complete = explicit_complete
    else:
        raise ReportingError("video_complete must be boolean")

    if complete_count > total_count and total_count:
        raise ReportingError("video complete count cannot exceed video total count")
    return complete, complete_count, total_count


def _identity_value(
    report: Mapping[str, Any],
    explicit: str | None,
    names: Sequence[str],
    *,
    field: str,
) -> str:
    value = explicit if explicit is not None else _first_value(report, names, None)
    if not isinstance(value, str) or not value.strip():
        raise ReportingError(f"{field} is required for a cell report")
    return value.strip()


def _outcomes(
    report: Mapping[str, Any], overrides: Mapping[str, Any] | None
) -> dict[str, Any]:
    nested = report.get("outcomes")
    nested = nested if isinstance(nested, Mapping) else {}
    overrides = overrides if isinstance(overrides, Mapping) else {}
    result: dict[str, Any] = {}
    for canonical, lower in _OUTCOME_FIELDS:
        value = _first_value(
            overrides,
            (canonical, lower),
            _first_value(nested, (canonical, lower), _first_value(report, (canonical, lower), None)),
        )
        result[canonical] = _copy(value)
    return result


def build_cell_report(
    run_report: Mapping[str, Any],
    *,
    robot_configuration_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    condition: str | None = None,
    outcomes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize one terminal or in-progress cell while preserving its raw evidence fields."""

    if not isinstance(run_report, Mapping):
        raise ReportingError("cell run report must be an object")
    robot = _identity_value(
        run_report,
        robot_configuration_id,
        ("robot_configuration_id", "robot"),
        field="robot_configuration_id",
    )
    model_value = _identity_value(
        run_report, model, ("model", "model_id"), field="model"
    )
    provider_value = _identity_value(run_report, provider, ("provider",), field="provider")
    condition_value = _identity_value(
        run_report, condition, ("condition", "generation_condition"), field="condition"
    )

    trials = run_report.get("trials")
    trial_list = list(trials) if isinstance(trials, list) else []
    derived = _trial_counts(trial_list)
    task_passed, task_total = _explicit_count(
        run_report,
        kind="task",
        passed_names=("passed_task_count",),
        total_names=("total_task_count", "task_count"),
    )
    clause_passed, clause_total = _explicit_count(
        run_report,
        kind="clause",
        passed_names=("passed_clause_count", "passed_source_clause_count"),
        total_names=("total_clause_count", "source_clause_count", "clause_count"),
    )
    case_passed, case_total = _explicit_count(
        run_report,
        kind="case",
        passed_names=("passed_case_count", "passed_private_case_count"),
        total_names=("total_case_count", "private_case_count", "case_count"),
    )
    if task_passed is None:
        task_passed = derived["task"][0]
    if task_total is None:
        task_total = derived["task"][1]
    if clause_passed is None:
        clause_passed = derived["clause"][0]
    if clause_total is None:
        clause_total = derived["clause"][1]
    if case_passed is None:
        case_passed = derived["case"][0]
    if case_total is None:
        case_total = derived["case"][1]

    attempts = run_report.get("attempts")
    attempt_count_value = _first_value(run_report, ("attempt_count", "num_attempts"), None)
    if attempt_count_value is None and isinstance(attempts, list):
        attempt_count_value = len(attempts)
    attempt_count = _non_negative_int(attempt_count_value, field="attempt count")
    video_complete, video_complete_count, video_total_count = _video_counts(
        run_report, trial_list
    )

    initial_passed = _bool(
        run_report,
        ("initial_capability_validation_passed", "initial_validation_passed", "pass@0"),
        default=False,
    )
    final_passed = _bool(
        run_report,
        (
            "final_capability_validation_passed",
            "final_validation_passed",
            "pass@k",
            "terminal_validation_passed",
            "validation_passed",
        ),
        default=False,
    )
    pipeline_completed = _bool(run_report, ("pipeline_completed",), default=False)
    physical_executed = _bool(
        run_report,
        ("capability_validation_executed", "physical_validation_executed"),
        default=False,
    )

    task_demo_value = run_report.get("task_demo")
    task_demo = task_demo_value if isinstance(task_demo_value, Mapping) else {}
    task_demo_trials_value = _first_value(
        run_report, ("task_demo_trials",), task_demo.get("trials", [])
    )
    task_demo_trials = (
        list(task_demo_trials_value) if isinstance(task_demo_trials_value, list) else []
    )
    task_demo_counts = _trial_counts(task_demo_trials)
    (
        task_demo_video_complete,
        task_demo_video_complete_count,
        task_demo_video_total_count,
    ) = _video_counts(task_demo, task_demo_trials)
    task_demo_executed = _bool(
        run_report,
        ("task_demo_executed",),
        default=_bool(task_demo, ("physical_validation_executed",), default=False),
    )
    task_demo_passed = _bool(
        run_report,
        ("task_demo_passed",),
        default=_bool(task_demo, ("validation_passed",), default=False),
    )
    task_demo_pipeline_completed = _bool(
        run_report,
        ("task_demo_pipeline_completed",),
        default=_bool(task_demo, ("pipeline_completed",), default=False),
    )

    cell_id = _first_value(run_report, ("cell_id",), f"{robot}::{condition_value}")
    if not isinstance(cell_id, str) or not cell_id.strip():
        raise ReportingError("cell_id must be a non-empty string")

    result: dict[str, Any] = {
        "cell_id": cell_id,
        "robot_configuration_id": robot,
        "model": model_value,
        "provider": provider_value,
        "condition": condition_value,
        "pipeline_completed": pipeline_completed,
        "dynamic_model_called": _bool(
            run_report, ("dynamic_model_called",), default=False
        ),
        "driver_generated_in_run": _bool(
            run_report, ("driver_generated_in_run",), default=False
        ),
        "capability_validation_executed": physical_executed,
        "initial_capability_validation_passed": initial_passed,
        "final_capability_validation_passed": final_passed,
        "task_demo_executed": task_demo_executed,
        "task_demo_passed": task_demo_passed,
        "task_demo_pipeline_completed": task_demo_pipeline_completed,
        "task_demo_case_counts": {
            "passed": task_demo_counts["case"][0],
            "total": task_demo_counts["case"][1],
        },
        "task_demo_task_counts": {
            "passed": task_demo_counts["task"][0],
            "total": task_demo_counts["task"][1],
        },
        "task_demo_clause_counts": {
            "passed": task_demo_counts["clause"][0],
            "total": task_demo_counts["clause"][1],
        },
        "task_demo_video_complete": task_demo_video_complete,
        "task_demo_video_complete_count": task_demo_video_complete_count,
        "task_demo_video_total_count": task_demo_video_total_count,
        # Compatibility aliases describe Capability Validation only.
        "physical_validation_executed": physical_executed,
        "initial_validation_passed": initial_passed,
        "pass@0": initial_passed,
        "final_validation_passed": final_passed,
        "pass@k": final_passed,
        "attempt_count": attempt_count,
        "passed_task_count": task_passed,
        "total_task_count": task_total,
        "passed_clause_count": clause_passed,
        "total_clause_count": clause_total,
        "passed_case_count": case_passed,
        "total_case_count": case_total,
        "task_counts": {"passed": task_passed, "total": task_total},
        "clause_counts": {"passed": clause_passed, "total": clause_total},
        "case_counts": {"passed": case_passed, "total": case_total},
        "video_required": _bool(
            run_report,
            ("video_required", "record_video"),
            default=True,
        ),
        "video_complete": video_complete,
        "video_complete_count": video_complete_count,
        "video_total_count": video_total_count,
        "outcomes": _outcomes(run_report, outcomes),
    }

    # Preserve the evidence that makes a cell reviewable.  The function does not interpret these
    # fields as a terminal success signal; it only carries them forward.
    for field in (
        "run_id",
        "code_version",
        "robot_package_version",
        "task_snapshot_id",
        "task_library_snapshot_id",
        "admitted_task_count",
        "designed_capability_count",
        "covered_task_count",
        "selected_task_count",
        "passed_selected_task_count",
        "fully_evaluated_task_count",
        "passed_fully_evaluated_task_count",
        "attempts",
        "development_rejections",
        "development_probe",
        "trials",
        "capability_results",
        "clause_results",
        "case_results",
        "video_manifest",
        "capability_validation",
        "task_demo",
        "task_demo_trials",
        "task_demo_video_manifest",
        "failure",
        "failure_reason",
        "infrastructure_failure",
    ):
        if field in run_report:
            result[field] = _copy(run_report[field])
    return result


def build_paired_report(
    cell_reports: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]],
    *,
    expected_robots: Sequence[str],
    expected_conditions: Sequence[str],
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build a configured matrix report that keeps missing and failing cells visible."""

    if isinstance(cell_reports, Mapping):
        values = list(cell_reports.values())
    else:
        values = list(cell_reports)
    normalized: list[dict[str, Any]] = [build_cell_report(value) for value in values]
    by_id: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for cell in normalized:
        cell_id = str(cell["cell_id"])
        if cell_id in by_id:
            duplicates.append(cell_id)
        by_id[cell_id] = cell
    if duplicates:
        raise ReportingError("duplicate cell_id: " + ", ".join(sorted(set(duplicates))))

    expected = [
        f"{robot}::{condition}"
        for robot in expected_robots
        for condition in expected_conditions
    ]
    ordered_cells = [by_id[cell_id] for cell_id in expected if cell_id in by_id]
    ordered_cells.extend(cell for cell in normalized if cell["cell_id"] not in set(expected))
    missing = [cell_id for cell_id in expected if cell_id not in by_id]
    unexpected = [cell_id for cell_id in by_id if cell_id not in set(expected)]

    comparisons: list[dict[str, Any]] = []
    for robot in expected_robots:
        condition_cells = {
            condition: by_id.get(f"{robot}::{condition}")
            for condition in expected_conditions
        }
        present = all(cell is not None for cell in condition_cells.values())
        both_passed: bool | None = None
        if present:
            both_passed = all(
                bool(cell["final_capability_validation_passed"])
                for cell in condition_cells.values()
                if cell is not None
            )
        comparisons.append(
            {
                "robot_configuration_id": robot,
                "conditions": condition_cells,
                "both_cells_reported": present,
                "both_cells_final_capability_validation_passed": both_passed,
                "both_cells_final_validation_passed": both_passed,
            }
        )

    all_reported = not missing and len(normalized) == len(expected)
    summary = {
        "expected_cell_count": len(expected),
        "reported_cell_count": len(normalized),
        "all_expected_cells_reported": all_reported,
        "missing_cell_ids": missing,
        "unexpected_cell_ids": unexpected,
        "all_cells_pipeline_completed": all(
            bool(cell["pipeline_completed"]) for cell in by_id.values()
        )
        if all_reported
        else False,
        "all_cells_physical_validation_executed": all(
            bool(cell["capability_validation_executed"]) for cell in by_id.values()
        )
        if all_reported
        else False,
        "all_cells_capability_validation_executed": all(
            bool(cell["capability_validation_executed"])
            for cell in by_id.values()
        )
        if all_reported
        else False,
        "all_cells_final_capability_validation_passed": all(
            bool(cell["final_capability_validation_passed"])
            for cell in by_id.values()
        )
        if all_reported
        else False,
        "all_cells_final_validation_passed": all(
            bool(cell["final_capability_validation_passed"])
            for cell in by_id.values()
        )
        if all_reported
        else False,
        "all_cells_task_demo_executed": all(
            bool(cell["task_demo_executed"]) for cell in by_id.values()
        )
        if all_reported
        else False,
        "all_cells_task_demo_passed": all(
            bool(cell["task_demo_passed"]) for cell in by_id.values()
        )
        if all_reported
        else False,
    }
    result: dict[str, Any] = {
        "report_type": "configured_generation_experiment",
        "cells": ordered_cells,
        "paired_comparisons": comparisons,
        "summary": summary,
    }
    if run_id is not None:
        result["run_id"] = run_id
    return result


def write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    """Write one human-readable report or Evolution outcome as JSON."""

    if not isinstance(value, Mapping):
        raise ReportingError("JSON report must be an object")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(dict(value), indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: str | Path) -> dict[str, Any]:
    """Read one JSON object without imposing a project-wide schema."""

    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportingError(f"cannot read JSON report {source}") from exc
    if not isinstance(value, dict):
        raise ReportingError("JSON report must be an object")
    return value


write_report = write_json
read_report = read_json
summarize_cell_report = build_cell_report
build_experiment_report = build_paired_report


__all__ = [
    "ReportingError",
    "build_cell_report",
    "build_experiment_report",
    "build_paired_report",
    "read_json",
    "read_report",
    "summarize_cell_report",
    "write_json",
    "write_report",
]
