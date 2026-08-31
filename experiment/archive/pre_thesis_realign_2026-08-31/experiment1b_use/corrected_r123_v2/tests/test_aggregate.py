from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
PROFILE_ROOT = REPOSITORY_ROOT / "experiment/experiment1b_use/corrected_r123_v2"
MANIFEST_PATH = PROFILE_ROOT / "config/manifest.json"
SOURCE_R1_AGGREGATE = (
    REPOSITORY_ROOT
    / "experiment/experiment1b_use/corrected_r1/analysis/corrected_r1_aggregate.json"
)
IDENTITY = {
    "document_id": "AA2-B2-CORRECTED-R123-V2",
    "revision": "1.0.0",
}

from experiment.experiment1b_use.corrected_r123_v2.analysis.aggregate_r123_v2 import (  # noqa: E402
    R123V2AggregationError,
    _provider_pins,
    _validate_terminal,
    aggregate,
)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _clauses() -> dict[str, dict]:
    suite = _read(PROFILE_ROOT / "config/task_suite.json")
    return {
        task["task_id"]: task["private_scoring_clauses"][0]
        for robot in suite["robot_suites"]
        for task in robot["tasks"]
    }


def _attempt(
    *,
    root: Path,
    unit: dict,
    attempt_number: int,
    classification: str,
    exact_model: str,
    video: Path,
    clause: dict,
) -> tuple[Path, dict]:
    safe = unit["unit_id"].replace("::", "__")
    provider_path = root / f"provider-{safe}-{attempt_number}.json"
    _write(
        provider_path,
        {
            "artifact_type": "b2_corrected_r123_v2_provider_record",
            "audit_identity": IDENTITY,
            "formal_episode": False,
            "execution_origin": unit["execution_origin"],
            "unit": unit,
            "requested_model": exact_model,
            "calls": [
                {
                    "call_index": 0,
                    "status": "success",
                    "requested_model": exact_model,
                    "returned_model": exact_model,
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "cost_usd": 0.01,
                }
            ],
            "input_tokens": 10,
            "output_tokens": 2,
            "total_cost_usd": 0.01,
        },
    )
    evaluable = classification in {"harness_pass", "harness_fail"}
    success = classification == "harness_pass"
    episode_path: Path | None = None
    if evaluable:
        episode_path = root / f"episode-{safe}-{attempt_number}.json"
        threshold = float(clause["threshold"])
        comparator = clause["comparator"]
        measurement = (
            threshold - 0.01
            if comparator == "<="
            else threshold + 0.01
            if comparator == ">="
            else threshold
        )
        _write(
            episode_path,
            {
                "artifact_type": "b2_corrected_r123_v2_episode_record",
                "audit_identity": IDENTITY,
                "formal_episode": False,
                "unit": unit,
                "episode": {
                    "harness": {
                        "physical_harness_verdict": "PASS" if success else "FAIL",
                        "physical_integrity_passed": True,
                        "video_complete": True,
                        "video": {
                            "requested": True,
                            "complete": True,
                            "decodable": True,
                            "width": 800,
                            "height": 600,
                            "path": str(video),
                        },
                        "task_clause_results": [
                            {
                                "clause_id": clause["clause_id"],
                                "measurement_value": measurement,
                                "measurement_error": None,
                                "task_metric_passed": success,
                            }
                        ],
                    }
                },
            },
        )
    terminal_path = root / f"terminal-{safe}-{attempt_number}.json"
    terminal = {
        "artifact_type": "b2_corrected_r123_v2_unit_terminal",
        "audit_identity": IDENTITY,
        "formal_episode": False,
        "formal_denominator_entry": False,
        **{
            field: unit[field]
            for field in (
                "unit_id",
                "robot_configuration_id",
                "task_id",
                "model_id",
                "replicate_id",
                "execution_origin",
            )
        },
        "classification": classification,
        "evaluable": evaluable,
        "success": success,
        "code_version": {"git_commit": "frozen-test-commit"},
        "model_identity": {
            "requested_model": exact_model,
            "returned_models": [exact_model],
            "exact_match": True,
        },
        "harness": {
            "physical_harness_verdict": "PASS" if success else "FAIL",
            "task_metric_passed": success,
            "physical_execution_passed": True,
            "physical_integrity_passed": True,
            "video_complete": True,
        }
        if evaluable
        else {},
        "episode_record_path": str(episode_path) if episode_path is not None else None,
        "provider_record_path": str(provider_path),
        "provider_calls": 1,
        "input_tokens": 10,
        "output_tokens": 2,
        "total_cost_usd": 0.01,
        "video_path": str(video) if evaluable else None,
    }
    if unit["replicate_id"] == "R1":
        terminal["superseded_terminal_path"] = str(
            REPOSITORY_ROOT / unit["superseded_terminal_path"]
        )
    _write(terminal_path, terminal)
    metadata = {
        "attempt_number": attempt_number,
        "terminal_path": str(terminal_path),
        "process_returncode": 0,
        "classification": classification,
        "evaluable": evaluable,
        "success": success,
    }
    return terminal_path, metadata


def _complete_schedulers(tmp_path: Path) -> list[Path]:
    manifest = _read(MANIFEST_PATH)
    pins = _provider_pins(manifest, manifest_path=MANIFEST_PATH)
    clauses = _clauses()
    video = tmp_path / "shared-test-video.mp4"
    video.write_bytes(b"video")
    schedulers: list[Path] = []
    for batch_index, batch in enumerate(manifest["dispatch_order"]):
        replicate_id, task_id = batch.split("/", 1)
        batch_units = [
            unit
            for unit in manifest["fresh_units"]
            if unit["replicate_id"] == replicate_id and unit["task_id"] == task_id
        ]
        assert len(batch_units) == 7
        batch_root = tmp_path / f"batch-{batch_index:02d}"
        records = []
        for unit_index, unit in enumerate(batch_units):
            attempts = []
            if batch_index == 0 and unit_index == 0:
                _first_path, first = _attempt(
                    root=batch_root,
                    unit=unit,
                    attempt_number=1,
                    classification="infrastructure_failure",
                    exact_model=pins[unit["model_id"]],
                    video=video,
                    clause=clauses[task_id],
                )
                attempts.append(first)
            final_number = len(attempts) + 1
            final_path, final = _attempt(
                root=batch_root,
                unit=unit,
                attempt_number=final_number,
                classification="harness_pass",
                exact_model=pins[unit["model_id"]],
                video=video,
                clause=clauses[task_id],
            )
            attempts.append(final)
            records.append(
                {
                    "unit_id": unit["unit_id"],
                    "execution_origin": unit["execution_origin"],
                    "provider_lane": "m5" if unit["model_id"] == "M5" else "company",
                    "attempts": attempts,
                    "retry_used": len(attempts) == 2,
                    "terminal_path": str(final_path),
                    "classification": final["classification"],
                    "evaluable": final["evaluable"],
                    "success": final["success"],
                }
            )
        scheduler_path = batch_root / "scheduler.json"
        _write(
            scheduler_path,
            {
                "artifact_type": "b2_corrected_r123_v2_scheduler",
                "schema_version": "1.0",
                "audit_identity": IDENTITY,
                "formal_episode": False,
                "formal_denominator_entry": False,
                "manifest_path": str(MANIFEST_PATH),
                "positive_control_index_path": str(
                    REPOSITORY_ROOT / manifest["paths"]["positive_control_index"]
                ),
                "planned_unit_ids": [record["unit_id"] for record in records],
                "task_filters": [task_id],
                "replicate_filters": [replicate_id],
                "company_workers": 3,
                "m5_workers": 1,
                "total_worker_limit": 4,
                "retry_policy": manifest["retry_policy"],
                "records": records,
            },
        )
        schedulers.append(scheduler_path)
    return schedulers


def test_complete_154_plus_56_aggregate_reports_balanced_selected_210(
    tmp_path: Path,
) -> None:
    schedulers = _complete_schedulers(tmp_path)
    report = aggregate(
        scheduler_paths=schedulers,
        manifest_path=MANIFEST_PATH,
        source_r1_aggregate_path=SOURCE_R1_AGGREGATE,
        require_complete=True,
    )
    assert report["complete"] is True
    assert report["execution_154"]["overall"]["planned"] == 154
    assert report["execution_154"]["overall"]["executed"] == 154
    assert report["execution_154"]["planned_batches"] == 22
    assert report["execution_154"]["observed_batches"] == 22
    assert report["execution_154"]["infrastructure_retries"] == 1
    assert report["selected_210"]["overall"]["planned"] == 210
    assert report["selected_210"]["overall"]["executed"] == 210
    assert set(value["planned"] for value in report["selected_210"]["per_task"].values()) == {
        21
    }
    assert set(value["planned"] for value in report["selected_210"]["per_model"].values()) == {
        30
    }
    assert set(value["planned"] for value in report["selected_210"]["per_robot"].values()) == {
        105
    }
    assert report["selected_210"]["per_replicate"]["R1"]["planned"] == 70
    assert report["selected_210"]["per_replicate"]["R2"]["planned"] == 70
    assert report["selected_210"]["per_replicate"]["R3"]["planned"] == 70
    assert report["usage"]["selected_210"]["attempts"] == 210
    assert report["usage"]["new_execution_all_attempts"]["attempts"] == 155
    assert report["usage"]["source_corrected_r1_actual_attempts"]["attempts"] == 73
    assert report["usage"]["all_attempts_actual_related_spend"]["attempts"] == 228
    assert report["usage"]["new_execution_all_attempts"]["provider_calls"] == 155
    assert report["usage"]["new_execution_all_attempts"]["input_tokens"] == 1550
    assert report["usage"]["new_execution_all_attempts"]["output_tokens"] == 310
    assert report["usage"]["new_execution_all_attempts"]["known_call_cost_usd"] == pytest.approx(
        1.55
    )
    assert len(report["superseded_r1"]) == 14
    assert all(item["selected"] is False for item in report["superseded_r1"])
    assert all(item["actual_related_spend_included"] is True for item in report["superseded_r1"])
    assert len(report["video_index"]) == 210
    assert len(report["metric_threshold_margins"]) >= 154


def test_t17_old_definition_or_contact_breach_cannot_be_selected_as_pass(
    tmp_path: Path,
) -> None:
    manifest = _read(MANIFEST_PATH)
    unit = next(
        item
        for item in manifest["fresh_units"]
        if item["task_id"] == "GO2-T17"
        and item["model_id"] == "M1"
        and item["replicate_id"] == "R1"
    )
    old_path = REPOSITORY_ROOT / unit["superseded_terminal_path"]
    old_terminal = _read(old_path)
    with pytest.raises(R123V2AggregationError, match="terminal identity"):
        _validate_terminal(
            old_terminal,
            terminal_path=old_path,
            unit=unit,
            expected_model=old_terminal["model_identity"]["requested_model"],
        )

    pins = _provider_pins(manifest, manifest_path=MANIFEST_PATH)
    video = tmp_path / "t17.mp4"
    video.write_bytes(b"video")
    terminal_path, _metadata = _attempt(
        root=tmp_path,
        unit=unit,
        attempt_number=1,
        classification="harness_pass",
        exact_model=pins["M1"],
        video=video,
        clause=_clauses()["GO2-T17"],
    )
    terminal = _read(terminal_path)
    terminal["harness"]["physical_integrity_passed"] = False
    with pytest.raises(
        R123V2AggregationError,
        match="identity/Harness/video evidence",
    ):
        _validate_terminal(
            terminal,
            terminal_path=terminal_path,
            unit=unit,
            expected_model=pins["M1"],
        )


def test_require_complete_rejects_the_missing_154_execution_block() -> None:
    with pytest.raises(R123V2AggregationError, match="154 units and 22 batches"):
        aggregate(
            scheduler_paths=[],
            manifest_path=MANIFEST_PATH,
            source_r1_aggregate_path=SOURCE_R1_AGGREGATE,
            require_complete=True,
        )
