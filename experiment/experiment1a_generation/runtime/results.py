"""Explicit, audit-friendly aggregation for formal Experiment 1 B1 cells."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .b1 import EXPERIMENT_ROOT, REPOSITORY_ROOT, resolve_experiment_manifest


EXPERIMENT_ID = "experiment1-b1-core-r3"
CURRENT_AUTHORITY_REVISION = "0.2.0"
EXPECTED_ROBOT_IDS = ("robotstudio_so101", "unitree-go2-stock-12dof")
EXPECTED_BACKBONE_IDS = ("M1", "M2", "M3", "M4", "M5", "M6", "M8")
EXPECTED_REPLICATE_IDS = ("r01", "r02", "r03")
EXPECTED_CORE_UNIT_COUNT = 84
EXPECTED_EXTENSION_UNIT_COUNTS = {"r04": 28, "r05": 28}
EXPECTED_CUMULATIVE_UNIT_COUNT = 140
ROBOT_DISPLAY_NAMES = {
    "robotstudio_so101": "SO-101",
    "unitree-go2-stock-12dof": "Unitree Go2",
    "leap_hand": "LEAP Hand",
    "aloha_2": "ALOHA 2",
}


class ResultsError(RuntimeError):
    """Raised when formal result selection or aggregation is ambiguous."""


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultsError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ResultsError(f"{label} must contain one JSON object: {path}")
    return value


def _require_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResultsError(f"{label} must be an object")
    return value


def discover_cell_records(
    scheduler_paths: Sequence[str | Path],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load terminal cells only from explicitly named formal schedulers.

    A scheduler is transport bookkeeping rather than the experimental unit.  A
    stopped scheduler may therefore contribute terminal sibling cells while its
    unfinished cells remain excluded for a later exact-unit rerun.
    """

    if not scheduler_paths:
        raise ResultsError("at least one --formal-scheduler is required")
    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_schedulers: set[Path] = set()
    for raw_path in scheduler_paths:
        scheduler_path = Path(raw_path).resolve()
        if scheduler_path in seen_schedulers:
            raise ResultsError(f"formal scheduler was supplied twice: {scheduler_path}")
        seen_schedulers.add(scheduler_path)
        scheduler = _read_json(scheduler_path, label="scheduler record")
        if scheduler.get("utc_finished_at") is None:
            raise ResultsError(f"scheduler is incomplete: {scheduler_path}")
        exit_summary = scheduler.get("exit_summary")
        if exit_summary is not None and not isinstance(exit_summary, Mapping):
            raise ResultsError(f"{scheduler_path}: exit_summary must be an object or null")
        units = scheduler.get("units")
        if not isinstance(units, list):
            raise ResultsError(f"formal scheduler has no unit list: {scheduler_path}")
        process_count = exit_summary.get("process_count") if exit_summary else None
        if process_count is not None and process_count != len(units):
            raise ResultsError(f"scheduler process count is inconsistent: {scheduler_path}")
        for item in units:
            item = _require_mapping(item, label=f"{scheduler_path}: scheduler unit")
            unit_id = item.get("unit_id")
            cell_output = item.get("cell_output")
            cell_path = (
                (Path(cell_output) / "cell_record.json").resolve()
                if isinstance(cell_output, str) and cell_output
                else None
            )
            if cell_path is None or not cell_path.is_file():
                excluded.append(
                    {
                        "unit_id": unit_id,
                        "authority_revision": None,
                        "cell_record_path": str(cell_path) if cell_path else None,
                        "scheduler_path": str(scheduler_path),
                        "reason": "scheduler_unit_incomplete",
                        "scheduler_exit_code": item.get("exit_code"),
                        "scheduler_cell_record_exists": item.get(
                            "cell_record_exists"
                        ),
                    }
                )
                continue
            record = _read_json(cell_path, label="cell record")
            identity = _require_mapping(
                record.get("identity"), label=f"{cell_path}: identity"
            )
            if unit_id != identity.get("unit_id") or unit_id != identity.get("cell_id"):
                raise ResultsError(f"scheduler/cell identity mismatch: {cell_path}")
            if record.get("experiment_id") != EXPERIMENT_ID or record.get("track") != "B1":
                raise ResultsError(f"record is not an Experiment 1 B1 cell: {cell_path}")
            terminal = record.get("terminal_verdict")
            timing = record.get("timing")
            if not isinstance(terminal, Mapping) or not terminal.get("stop_reason"):
                excluded.append(
                    {
                        "unit_id": unit_id,
                        "authority_revision": identity.get("authority_revision"),
                        "cell_record_path": str(cell_path),
                        "scheduler_path": str(scheduler_path),
                        "reason": "cell_record_incomplete",
                        "scheduler_exit_code": item.get("exit_code"),
                        "scheduler_cell_record_exists": item.get(
                            "cell_record_exists"
                        ),
                    }
                )
                continue
            if (
                not isinstance(timing, Mapping)
                or timing.get("utc_finished_at") is None
                or not isinstance(timing.get("total_wall_time_s"), (int, float))
            ):
                excluded.append(
                    {
                        "unit_id": unit_id,
                        "authority_revision": identity.get("authority_revision"),
                        "cell_record_path": str(cell_path),
                        "scheduler_path": str(scheduler_path),
                        "reason": "cell_record_incomplete",
                        "scheduler_exit_code": item.get("exit_code"),
                        "scheduler_cell_record_exists": item.get(
                            "cell_record_exists"
                        ),
                    }
                )
                continue
            attempts = record.get("attempts")
            if not isinstance(attempts, list):
                raise ResultsError(f"cell record has no attempt list: {cell_path}")
            frozen = sum(
                1
                for attempt in attempts
                if isinstance(attempt, Mapping)
                and attempt.get("driver_frozen") is True
            )
            if frozen > 3 or terminal.get("frozen_driver_attempt_count") != frozen:
                raise ResultsError(f"frozen-Driver attempt count mismatch: {cell_path}")
            candidates.append(
                {
                    "record": record,
                    "cell_record_path": str(cell_path),
                    "scheduler_path": str(scheduler_path),
                    "scheduler_exit_code": item.get("exit_code"),
                    "scheduler_cell_record_exists": item.get(
                        "cell_record_exists"
                    ),
                }
            )
    return candidates, excluded


def _eligibility(candidate: Mapping[str, Any]) -> tuple[bool, str | None]:
    record = _require_mapping(candidate.get("record"), label="candidate record")
    identity = _require_mapping(record.get("identity"), label="candidate identity")
    backbone = identity.get("backbone_id")
    revision = identity.get("authority_revision")
    if revision != CURRENT_AUTHORITY_REVISION:
        return False, "superseded_pre_020"
    if backbone == "M2":
        model = _require_mapping(record.get("model"), label="M2 model record")
        if float(model.get("timeout_s", -1)) != 600.0:
            raise ResultsError(
                f"current M2 record does not use 600 s: "
                f"{candidate.get('cell_record_path')}"
            )
    return True, None


def select_cell_records(
    candidates: Sequence[Mapping[str, Any]],
    *,
    expected_unit_ids: Sequence[str],
    preexcluded: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply the current 0.2.0 compatibility rule without outcome selection."""

    expected = list(expected_unit_ids)
    if len(expected) != len(set(expected)):
        raise ResultsError("expected unit IDs must be unique")
    expected_set = set(expected)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded: list[dict[str, Any]] = [dict(item) for item in preexcluded]
    for raw_candidate in candidates:
        candidate = dict(raw_candidate)
        record = _require_mapping(candidate.get("record"), label="candidate record")
        identity = _require_mapping(record.get("identity"), label="candidate identity")
        unit_id = identity.get("unit_id")
        if not isinstance(unit_id, str) or unit_id not in expected_set:
            raise ResultsError(f"candidate is outside the frozen manifest: {unit_id!r}")
        eligible, reason = _eligibility(candidate)
        if eligible:
            grouped[unit_id].append(candidate)
        else:
            excluded.append(
                {
                    "unit_id": unit_id,
                    "authority_revision": identity.get("authority_revision"),
                    "cell_record_path": candidate.get("cell_record_path"),
                    "reason": reason,
                }
            )
    duplicates = {unit: rows for unit, rows in grouped.items() if len(rows) > 1}
    if duplicates:
        detail = ", ".join(
            f"{unit} ({len(rows)})" for unit, rows in sorted(duplicates.items())
        )
        raise ResultsError(f"ambiguous eligible formal records: {detail}")
    selected = [grouped[unit][0] for unit in expected if len(grouped.get(unit, [])) == 1]
    missing = [unit for unit in expected if unit not in grouped]
    audit = {
        "expected_unit_count": len(expected),
        "selected_unit_count": len(selected),
        "missing_unit_count": len(missing),
        "missing_unit_ids": missing,
        "selected": [
            {
                "unit_id": item["record"]["identity"]["unit_id"],
                "authority_revision": item["record"]["identity"][
                    "authority_revision"
                ],
                "run_id": item["record"].get("run_id"),
                "git_commit": item["record"]["identity"].get("git_commit"),
                "cell_record_path": item.get("cell_record_path"),
                "scheduler_path": item.get("scheduler_path"),
            }
            for item in selected
        ],
        "excluded": sorted(
            excluded,
            key=lambda row: (str(row["unit_id"]), str(row["cell_record_path"])),
        ),
    }
    return selected, audit


def _normal_stage(stage: Any) -> Any:
    return "generate" if stage in {"generate", "gen_algo"} else stage


def _safe_segment(value: Any) -> str:
    return str(value).replace("::", "__").replace("/", "_")


def _inventory_path(path: Path, root: Path) -> tuple[str, bool]:
    try:
        return str(path.resolve().relative_to(root)), True
    except ValueError:
        return str(path.resolve()), False


def _validated_attempts(record: Mapping[str, Any], *, unit_id: str) -> list[Mapping[str, Any]]:
    attempts = record.get("attempts", [])
    if not isinstance(attempts, list):
        raise ResultsError(f"{unit_id}: attempts must be a list")
    indices: list[int] = []
    accepted: list[Mapping[str, Any]] = []
    for raw_attempt in attempts:
        attempt = _require_mapping(raw_attempt, label=f"{unit_id}: attempt")
        index = attempt.get("attempt_index")
        if not isinstance(index, int) or isinstance(index, bool) or index not in range(3):
            raise ResultsError(f"{unit_id}: attempt index must be 0, 1, or 2")
        indices.append(index)
        if attempt.get("driver_frozen") is not True:
            continue
        report = attempt.get("validation_report")
        counts = attempt.get("validation_case_counts")
        if not isinstance(report, Mapping) or not isinstance(counts, Mapping):
            raise ResultsError(f"{unit_id}: frozen attempt lacks validation evidence")
        if not isinstance(attempt.get("validation_verdict"), bool):
            raise ResultsError(f"{unit_id}: frozen attempt lacks validation verdict")
        numeric_counts: dict[str, int] = {}
        for name in ("passed", "failed", "incomplete", "total"):
            value = counts.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ResultsError(f"{unit_id}: invalid validation case count {name}")
            numeric_counts[name] = value
        if (
            numeric_counts["passed"]
            + numeric_counts["failed"]
            + numeric_counts["incomplete"]
            != numeric_counts["total"]
        ):
            raise ResultsError(f"{unit_id}: validation case counts do not sum")
        accepted.append(attempt)
    if len(indices) != len(set(indices)):
        raise ResultsError(f"{unit_id}: duplicate attempt index")
    return accepted


def _final_attempt(record: Mapping[str, Any], *, unit_id: str) -> Mapping[str, Any] | None:
    accepted = _validated_attempts(record, unit_id=unit_id)
    if not accepted:
        return None
    return max(accepted, key=lambda attempt: int(attempt["attempt_index"]))


def _verified_derived(record: Mapping[str, Any], *, unit_id: str) -> Mapping[str, Any]:
    derived = _require_mapping(record.get("derived"), label=f"{unit_id}: derived")
    actions = record.get("actions", [])
    calls = record.get("provider_calls", [])
    if not isinstance(actions, list) or not isinstance(calls, list):
        raise ResultsError(f"{unit_id}: actions and provider_calls must be lists")
    for expected_iteration, raw_action in enumerate(actions, start=1):
        action = _require_mapping(raw_action, label=f"{unit_id}: action")
        if action.get("iteration") != expected_iteration:
            raise ResultsError(f"{unit_id}: action iterations are not contiguous")
    action_type_counts = {
        name: sum(
            1
            for action in actions
            if isinstance(action, Mapping) and action.get("action_type") == name
        )
        for name in (
            "observe_or_plan",
            "execute_clean",
            "execute_error",
            "artifact_complete",
        )
    }
    stacked = {
        "read_or_plan": action_type_counts["observe_or_plan"],
        "execute_clean": (
            action_type_counts["execute_clean"]
            + action_type_counts["artifact_complete"]
        ),
        "execute_error": action_type_counts["execute_error"],
    }
    provider_error_count = sum(
        1
        for call in calls
        if isinstance(call, Mapping) and call.get("status") == "failed"
    )
    token_totals: dict[str, int | None] = {}
    for field in ("input_tokens", "output_tokens"):
        values = [
            call.get("tokens", {}).get(field)
            for call in calls
            if isinstance(call, Mapping)
            and isinstance(call.get("tokens"), Mapping)
        ]
        known = [value for value in values if isinstance(value, int)]
        token_totals[field] = sum(known) if known else None
    known_input = token_totals["input_tokens"]
    known_output = token_totals["output_tokens"]
    total_tokens = (
        int(known_input) + int(known_output)
        if isinstance(known_input, int) and isinstance(known_output, int)
        else None
    )
    expected = {
        "iteration_count": len(actions),
        "execution_error_count": stacked["execute_error"],
        "provider_error_count": provider_error_count,
        "stacked_action_counts": stacked,
        "total_tokens": total_tokens,
    }
    actual_tokens = _require_mapping(
        derived.get("token_totals"), label=f"{unit_id}: token totals"
    ).get("total_tokens")
    actual = {
        "iteration_count": derived.get("iteration_count"),
        "execution_error_count": derived.get("execution_error_count"),
        "provider_error_count": derived.get("provider_error_count"),
        "stacked_action_counts": derived.get("stacked_action_counts"),
        "total_tokens": actual_tokens,
    }
    mismatches = [name for name in expected if actual[name] != expected[name]]
    if mismatches:
        raise ResultsError(
            f"{unit_id}: derived record mismatch: {', '.join(mismatches)}"
        )
    return derived


def build_results(
    selected: Sequence[Mapping[str, Any]],
    *,
    selection_audit: Mapping[str, Any],
    model_display_names: Mapping[str, str],
    repository_root: str | Path = REPOSITORY_ROOT,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build compact analysis rows and a separate all-attempt video inventory."""

    root = Path(repository_root).resolve()
    cell_rows: list[dict[str, Any]] = []
    action_rows: list[dict[str, Any]] = []
    capability_rows: list[dict[str, Any]] = []
    case_rows: list[dict[str, Any]] = []
    video_rows: list[dict[str, Any]] = []
    video_attempt_audit_rows: list[dict[str, Any]] = []
    video_keys: set[str] = set()
    expected_video_count = 0
    unrepresented_video_count = 0
    for candidate in selected:
        record = _require_mapping(candidate.get("record"), label="selected record")
        identity = _require_mapping(record.get("identity"), label="selected identity")
        unit_id = str(identity["unit_id"])
        backbone = str(identity["backbone_id"])
        revision = str(identity["authority_revision"])
        accepted_attempts = _validated_attempts(record, unit_id=unit_id)
        final_attempt = (
            max(accepted_attempts, key=lambda attempt: int(attempt["attempt_index"]))
            if accepted_attempts
            else None
        )
        final_report = (
            final_attempt.get("validation_report")
            if isinstance(final_attempt, Mapping)
            else None
        )
        final_report = final_report if isinstance(final_report, Mapping) else None
        final_counts = (
            final_attempt.get("validation_case_counts")
            if isinstance(final_attempt, Mapping)
            else None
        )
        final_counts = final_counts if isinstance(final_counts, Mapping) else None
        capabilities = (
            final_report.get("capability_results", []) if final_report else []
        )
        trials = final_report.get("trials", []) if final_report else []
        provider_calls = record.get("provider_calls", [])
        token_usage_complete = all(
            isinstance(call.get("tokens"), Mapping)
            and isinstance(call["tokens"].get("input_tokens"), (int, float))
            and isinstance(call["tokens"].get("output_tokens"), (int, float))
            for call in provider_calls
            if isinstance(call, Mapping)
        )
        derived = _verified_derived(record, unit_id=unit_id)
        terminal = _require_mapping(
            record.get("terminal_verdict"), label=f"{unit_id}: terminal verdict"
        )
        cell_rows.append(
            {
                "unit_id": unit_id,
                "authority_revision": revision,
                "robot_configuration_id": identity["robot_configuration_id"],
                "robot_display_name": ROBOT_DISPLAY_NAMES.get(
                    str(identity["robot_configuration_id"]),
                    str(identity["robot_configuration_id"]),
                ),
                "backbone_id": backbone,
                "model_display_name": model_display_names.get(backbone, backbone),
                "exact_model_id": record.get("model", {}).get("exact_model_id"),
                "generation_condition": identity["generation_condition"],
                "replicate_id": identity["replicate_id"],
                "terminal_stop_reason": terminal.get("stop_reason"),
                "terminal_failure_class": terminal.get("terminal_failure_class"),
                "frozen_driver_attempt_count": terminal.get(
                    "frozen_driver_attempt_count"
                ),
                "final_frozen_attempt_index": (
                    final_attempt.get("attempt_index") if final_attempt else None
                ),
                "validation_passed": (
                    final_attempt.get("validation_verdict") if final_attempt else None
                ),
                "passed_capability_count": sum(
                    1
                    for capability in capabilities
                    if isinstance(capability, Mapping) and capability.get("passed") is True
                ),
                "capability_count": len(capabilities),
                "passed_case_count": final_counts.get("passed") if final_counts else 0,
                "failed_case_count": final_counts.get("failed") if final_counts else 0,
                "incomplete_case_count": (
                    final_counts.get("incomplete") if final_counts else 0
                ),
                "case_count": final_counts.get("total") if final_counts else 0,
                "observed_trial_count": len(trials),
                "iteration_count": derived.get("iteration_count"),
                "execution_error_count": derived.get("execution_error_count"),
                "provider_error_count": derived.get("provider_error_count"),
                "stacked_action_counts": derived.get("stacked_action_counts"),
                "total_tokens": derived.get("token_totals", {}).get("total_tokens"),
                "total_model_cost": derived.get("total_model_cost"),
                "token_usage_complete": token_usage_complete,
                "timing": record.get("timing"),
                "cell_record_path": candidate.get("cell_record_path"),
            }
        )
        previous_stage: Any = None
        for action in record.get("actions", []):
            if not isinstance(action, Mapping):
                raise ResultsError(f"{unit_id}: action must be an object")
            raw_stage = action.get("stage")
            stage = _normal_stage(raw_stage)
            action_rows.append(
                {
                    "unit_id": unit_id,
                    "iteration": action.get("iteration"),
                    "stage": stage,
                    "raw_stage": raw_stage,
                    "stage_start": stage != previous_stage,
                    "action_type": action.get("action_type"),
                    "plot_action_type": action.get("plot_action_type"),
                    "input_tokens": action.get("input_tokens"),
                    "output_tokens": action.get("output_tokens"),
                    "elapsed_s": action.get("elapsed_s"),
                    "provider_call_index": action.get("provider_call_index"),
                    "model_turn_index": action.get("model_turn_index"),
                    "tool_name": action.get("tool_name"),
                    "tool_names": action.get("tool_names"),
                    "tool_outcome": action.get("tool_outcome"),
                    "target_attempt": action.get("target_attempt"),
                    "artifact_event": action.get("artifact_event"),
                    "stage_transition": action.get("stage_transition"),
                }
            )
            previous_stage = stage
        if final_report:
            for capability in capabilities:
                capability = _require_mapping(
                    capability, label=f"{unit_id}: capability result"
                )
                capability_rows.append(
                    {
                        "unit_id": unit_id,
                        "attempt_index": final_attempt["attempt_index"],
                        "capability_id": capability.get("capability_id"),
                        "passed": capability.get("passed"),
                        "passed_case_count": capability.get("passed_case_count"),
                        "case_count": capability.get("case_count"),
                    }
                )
            for trial in trials:
                trial = _require_mapping(trial, label=f"{unit_id}: final trial")
                case_rows.append(
                    {
                        "unit_id": unit_id,
                        "attempt_index": final_attempt["attempt_index"],
                        "capability_id": trial.get("capability_id"),
                        "case_id": trial.get("case_id"),
                        "trial_id": trial.get("trial_id"),
                        "passed": trial.get("trial_passed"),
                    }
                )
        for attempt in accepted_attempts:
            report = attempt.get("validation_report")
            counts = attempt.get("validation_case_counts")
            if not isinstance(report, Mapping) or not isinstance(counts, Mapping):
                raise ResultsError(f"{unit_id}: accepted attempt lacks video evidence")
            report_trials = report.get("trials", [])
            if not isinstance(report_trials, list):
                raise ResultsError(f"{unit_id}: validation trials must be a list")
            attempt_index = int(attempt["attempt_index"])
            expected_attempt_videos = int(counts["total"])
            expected_video_count += expected_attempt_videos
            missing_trial_rows = max(0, expected_attempt_videos - len(report_trials))
            unrepresented_video_count += missing_trial_rows
            manifest = report.get("video_manifest")
            manifest_is_list = isinstance(manifest, list)
            trial_videos = [
                trial.get("video") if isinstance(trial, Mapping) else None
                for trial in report_trials
            ]
            manifest_matches_trials = bool(
                manifest_is_list and manifest == trial_videos
            )
            discrepancy_reasons: list[str] = []
            if len(report_trials) != expected_attempt_videos:
                discrepancy_reasons.append("validation_trial_count_mismatch")
            if not manifest_is_list:
                discrepancy_reasons.append("video_manifest_missing_or_invalid")
            elif not manifest_matches_trials:
                discrepancy_reasons.append("video_manifest_trial_mismatch")
            attempt_video_start = len(video_rows)
            for trial in report_trials:
                trial = _require_mapping(trial, label=f"{unit_id}: video trial")
                video = trial.get("video")
                capability_id = trial.get("capability_id")
                case_id = trial.get("case_id")
                trial_id = trial.get("trial_id")
                if not all(
                    isinstance(value, str) and value
                    for value in (capability_id, case_id, trial_id)
                ):
                    raise ResultsError(f"{unit_id}: video trial identity is incomplete")
                key = f"{revision}|{unit_id}|{attempt_index}|{trial_id}"
                if key in video_keys:
                    raise ResultsError(f"duplicate video inventory key: {key}")
                video_keys.add(key)
                artifact = Path(
                    "videos",
                    EXPERIMENT_ID,
                    f"rev-{revision}",
                    _safe_segment(unit_id),
                    f"attempt-{attempt_index:02d}",
                    _safe_segment(capability_id),
                    _safe_segment(case_id),
                    f"{_safe_segment(trial_id)}.mp4",
                )
                metadata_present = isinstance(video, Mapping)
                if metadata_present:
                    for field in ("case_id", "trial_id"):
                        if video.get(field) != trial.get(field):
                            raise ResultsError(f"{unit_id}: trial/video {field} mismatch")
                    if int(video.get("attempt", -1)) != attempt_index:
                        raise ResultsError(f"{unit_id}: trial/video attempt mismatch")
                    path_value = video.get("path")
                    source = (
                        Path(path_value).resolve()
                        if isinstance(path_value, str) and path_value
                        else None
                    )
                    source_exists = bool(source and source.is_file())
                    source_video, source_within_repository = (
                        _inventory_path(source, root) if source else (None, False)
                    )
                    requested = video.get("requested")
                    complete = video.get("complete")
                    decodable = video.get("decodable")
                    video_error = video.get("error")
                else:
                    source_exists = False
                    source_video = None
                    source_within_repository = False
                    requested = None
                    complete = False
                    decodable = False
                    video_error = "missing_video_metadata"
                uploadable = bool(
                    metadata_present
                    and requested is True
                    and complete is True
                    and decodable is True
                    and video_error is None
                    and source_exists
                )
                source_cell_record, cell_record_within_repository = _inventory_path(
                    Path(str(candidate["cell_record_path"])), root
                )
                video_rows.append(
                    {
                        "key": key,
                        "authority_revision": revision,
                        "unit_id": unit_id,
                        "run_id": record.get("run_id"),
                        "attempt_index": attempt_index,
                        "capability_id": capability_id,
                        "case_id": case_id,
                        "trial_id": trial_id,
                        "trial_passed": trial.get("trial_passed"),
                        "source_cell_record": source_cell_record,
                        "cell_record_within_repository": (
                            cell_record_within_repository
                        ),
                        "source_video": source_video,
                        "source_within_repository": source_within_repository,
                        "source_exists": source_exists,
                        "artifact_relpath": str(artifact),
                        "metadata_present": metadata_present,
                        "requested": requested,
                        "complete": complete,
                        "decodable": decodable,
                        "uploadable": uploadable,
                        "error": video_error,
                        "validation_video_complete": report.get("video_complete"),
                        "frame_count": video.get("frame_count") if metadata_present else None,
                        "width": video.get("width") if metadata_present else None,
                        "height": video.get("height") if metadata_present else None,
                        "duration": video.get("duration") if metadata_present else None,
                        "sim_start_s": video.get("sim_start_s") if metadata_present else None,
                        "sim_end_s": video.get("sim_end_s") if metadata_present else None,
                    }
                )
            attempt_video_rows = video_rows[attempt_video_start:]
            evidence_complete = bool(
                len(report_trials) == expected_attempt_videos
                and manifest_matches_trials
                and all(row["uploadable"] is True for row in attempt_video_rows)
            )
            if report.get("video_complete") is True and not evidence_complete:
                discrepancy_reasons.append(
                    "reported_video_complete_without_complete_evidence"
                )
            video_attempt_audit_rows.append(
                {
                    "unit_id": unit_id,
                    "attempt_index": attempt_index,
                    "expected_video_count": expected_attempt_videos,
                    "observed_trial_count": len(report_trials),
                    "unrepresented_video_count": missing_trial_rows,
                    "trial_video_metadata_count": sum(
                        1 for video in trial_videos if isinstance(video, Mapping)
                    ),
                    "video_manifest_count": len(manifest) if manifest_is_list else None,
                    "video_manifest_matches_trials": manifest_matches_trials,
                    "reported_video_complete": report.get("video_complete"),
                    "evidence_complete": evidence_complete,
                    "discrepancy_reasons": discrepancy_reasons,
                }
            )
    terminal_counts = Counter(row["terminal_stop_reason"] for row in cell_rows)
    results = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "authority_revision": CURRENT_AUTHORITY_REVISION,
        "selection_audit": dict(selection_audit),
        "summary": {
            "selected_cell_count": len(cell_rows),
            "terminal_counts": dict(sorted(terminal_counts.items())),
            "validation_cell_count": sum(
                1 for row in cell_rows if row["final_frozen_attempt_index"] is not None
            ),
            "passed_driver_count": sum(
                1 for row in cell_rows if row["validation_passed"] is True
            ),
            "evaluated_capability_count": len(capability_rows),
            "passed_capability_count": sum(
                1 for row in capability_rows if row["passed"] is True
            ),
            "evaluated_case_count": sum(row["case_count"] for row in cell_rows),
            "passed_case_count": sum(row["passed_case_count"] for row in cell_rows),
            "failed_case_count": sum(row["failed_case_count"] for row in cell_rows),
            "incomplete_case_count": sum(
                row["incomplete_case_count"] for row in cell_rows
            ),
            "observed_trial_count": len(case_rows),
            "total_tokens": sum(
                int(row["total_tokens"])
                for row in cell_rows
                if isinstance(row["total_tokens"], (int, float))
            ),
            "total_model_cost": sum(
                float(row["total_model_cost"])
                for row in cell_rows
                if isinstance(row["total_model_cost"], (int, float))
            ),
            "expected_video_count": expected_video_count,
            "video_trial_count": len(video_rows),
            "unrepresented_video_count": unrepresented_video_count,
            "uploadable_video_count": sum(
                1 for row in video_rows if row["uploadable"] is True
            ),
            "missing_video_count": unrepresented_video_count
            + sum(1 for row in video_rows if row["source_exists"] is not True),
            "incomplete_video_count": unrepresented_video_count
            + sum(1 for row in video_rows if row["complete"] is not True),
            "undecodable_video_count": unrepresented_video_count
            + sum(1 for row in video_rows if row["decodable"] is not True),
            "video_manifest_discrepancy_count": sum(
                1
                for row in video_attempt_audit_rows
                if row["video_manifest_matches_trials"] is not True
            ),
            "video_evidence_discrepancy_count": sum(
                1 for row in video_attempt_audit_rows if row["discrepancy_reasons"]
            ),
        },
        "cell_rows": cell_rows,
        "action_rows": action_rows,
        "capability_rows": capability_rows,
        "case_rows": case_rows,
        "video_attempt_audit_rows": video_attempt_audit_rows,
    }
    return results, sorted(video_rows, key=lambda row: row["key"])


def aggregate_formal_results(
    *,
    manifest_path: str | Path,
    scheduler_paths: Sequence[str | Path],
    repository_root: str | Path = REPOSITORY_ROOT,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    resolved = resolve_experiment_manifest(Path(manifest_path).resolve())
    if (
        resolved.get("experiment_id") != EXPERIMENT_ID
        or resolved.get("authority_revision") != CURRENT_AUTHORITY_REVISION
        or tuple(resolved.get("robot_ids", ())) != EXPECTED_ROBOT_IDS
        or tuple(resolved.get("backbone_ids", ())) != EXPECTED_BACKBONE_IDS
        or tuple(resolved.get("replicate_ids", ())) != EXPECTED_REPLICATE_IDS
        or resolved.get("unit_count") != EXPECTED_CORE_UNIT_COUNT
        or resolved.get("unit_count") != len(resolved.get("units", ()))
        or resolved.get("extension_unit_counts") != EXPECTED_EXTENSION_UNIT_COUNTS
        or resolved.get("cumulative_unit_count") != EXPECTED_CUMULATIVE_UNIT_COUNT
    ):
        raise ResultsError(
            "manifest must resolve the exact 84-cell Experiment 1 revision 0.2.0 design"
        )
    expected = [str(unit["unit_id"]) for unit in resolved["units"]]
    candidates, discovery_exclusions = discover_cell_records(scheduler_paths)
    selected, audit = select_cell_records(
        candidates,
        expected_unit_ids=expected,
        preexcluded=discovery_exclusions,
    )
    display_names: dict[str, str] = {}
    configs: dict[str, dict[str, Any]] = {}
    for backbone, path in resolved["backbone_runtime_config_paths"].items():
        config = _read_json(Path(path), label=f"{backbone} provider config")
        display_names[str(backbone)] = str(config.get("family") or backbone)
        configs[str(backbone)] = config
    for candidate in selected:
        record = candidate["record"]
        identity = record["identity"]
        backbone = str(identity["backbone_id"])
        config = configs[backbone]
        model = _require_mapping(record.get("model"), label=f"{backbone} model")
        settings = _require_mapping(
            config.get("inference_settings"), label=f"{backbone} config settings"
        )
        expected_model = {
            "backbone_id": backbone,
            "exact_model_id": config.get("exact_model_id"),
            "transport": config.get("transport"),
            "base_url": str(config.get("endpoint_base_url", "")).rstrip("/"),
            "timeout_s": float(settings.get("timeout_s", -1)),
            "token_limit": settings.get("max_tokens"),
        }
        actual_model = {
            "backbone_id": model.get("backbone_id"),
            "exact_model_id": model.get("exact_model_id"),
            "transport": model.get("transport"),
            "base_url": str(model.get("base_url", "")).rstrip("/"),
            "timeout_s": float(model.get("timeout_s", -1)),
            "token_limit": model.get("token_limit"),
        }
        mismatches = [
            name for name in expected_model if actual_model[name] != expected_model[name]
        ]
        if mismatches:
            raise ResultsError(
                f"{identity['unit_id']}: model pin mismatch: {', '.join(mismatches)}"
            )
    return build_results(
        selected,
        selection_audit=audit,
        model_display_names=display_names,
        repository_root=repository_root,
    )


def write_results(
    output_dir: str | Path,
    results: Mapping[str, Any],
    video_rows: Sequence[Mapping[str, Any]],
) -> tuple[Path, Path]:
    output = Path(output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise ResultsError(f"results output directory must be new or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    results_path = output / "results.json"
    videos_path = output / "video_inventory.jsonl"
    results_path.write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    videos_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in video_rows),
        encoding="utf-8",
    )
    return results_path, videos_path


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Aggregate explicitly selected formal Experiment 1 schedulers."
    )
    parser.add_argument(
        "--manifest", type=Path, default=EXPERIMENT_ROOT / "manifest.json"
    )
    parser.add_argument(
        "--formal-scheduler", action="append", type=Path, required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    try:
        results, videos = aggregate_formal_results(
            manifest_path=args.manifest,
            scheduler_paths=args.formal_scheduler,
        )
        if args.require_complete and results["selection_audit"]["missing_unit_count"]:
            raise ResultsError(
                "formal result set is incomplete: "
                f"{results['selection_audit']['missing_unit_count']} missing units"
            )
        results_path, videos_path = write_results(args.output, results, videos)
    except ResultsError as exc:
        print(f"aggregate_b1: {exc}")
        return 2
    print(
        json.dumps(
            {
                **results["summary"],
                "missing_unit_count": results["selection_audit"]["missing_unit_count"],
                "results": str(results_path),
                "video_inventory": str(videos_path),
            },
            sort_keys=True,
        )
    )
    return 0


__all__ = [
    "ResultsError",
    "aggregate_formal_results",
    "build_results",
    "discover_cell_records",
    "select_cell_records",
    "write_results",
]
