from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from runtime.b2 import AUTHORITY_DOCUMENT_ID, AUTHORITY_REVISION, resolve_manifest
from runtime.results import B2AggregationError, aggregate_schedulers


def _write_one_terminal(tmp_path: Path) -> tuple[Path, Path]:
    manifest = resolve_manifest()
    unit = manifest.units[0]
    safe = unit.unit_id.replace("::", "__")
    terminal_path = tmp_path / "terminals" / f"{safe}.json"
    terminal_path.parent.mkdir(parents=True)
    evidence_root = tmp_path / "episodes" / safe
    evidence_root.mkdir(parents=True)
    episode_record_path = evidence_root / "episode_record.json"
    episode_record_path.write_text(
        json.dumps(
            {
                "artifact_type": "b2_formal_episode_record",
                "formal_episode": True,
                "unit": unit.as_dict(),
                "episode": {},
            }
        ),
        encoding="utf-8",
    )
    provider_record_path = evidence_root / "provider_record.json"
    provider_record_path.write_text(
        json.dumps(
            {
                "artifact_type": "b2_formal_provider_record",
                "formal_episode": True,
                "unit": unit.as_dict(),
                "calls": [],
            }
        ),
        encoding="utf-8",
    )
    terminal_path.write_text(
        json.dumps(
            {
                "artifact_type": "b2_formal_unit_terminal",
                "authority": {
                    "document_id": AUTHORITY_DOCUMENT_ID,
                    "revision": AUTHORITY_REVISION,
                },
                "manifest_revision": AUTHORITY_REVISION,
                "formal_episode": True,
                "code_version": {"git_commit": "test-commit"},
                "unit_id": unit.unit_id,
                **unit.as_dict(),
                "utc_finished_at": "2026-08-23T00:00:00Z",
                "classification": "harness_fail",
                "evaluable": True,
                "success": False,
                "model_identity": {
                    "requested_model": "eu.anthropic.claude-sonnet-4-6",
                    "returned_models": ["eu.anthropic.claude-sonnet-4-6"],
                    "exact_match": True,
                },
                "harness": {
                    "physical_harness_verdict": "FAIL",
                    "physical_integrity_passed": True,
                    "video_complete": True,
                },
                "episode_record_path": str(episode_record_path),
                "provider_record_path": str(provider_record_path),
            }
        ),
        encoding="utf-8",
    )
    scheduler_path = tmp_path / "scheduler.json"
    scheduler_path.write_text(
        json.dumps(
            {
                "artifact_type": "b2_formal_scheduler",
                "schema_version": "1.0",
                "authority": {
                    "document_id": AUTHORITY_DOCUMENT_ID,
                    "revision": AUTHORITY_REVISION,
                },
                "manifest_revision": AUTHORITY_REVISION,
                "planned_unit_ids": [unit.unit_id],
                "records": [
                    {
                        "unit_id": unit.unit_id,
                        "status": "terminal",
                        "terminal_path": str(terminal_path),
                    }
                ],
                "utc_finished_at": "2026-08-23T00:01:00Z",
            }
        ),
        encoding="utf-8",
    )
    return scheduler_path, terminal_path


def test_aggregate_preserves_all_fixed_denominators(tmp_path: Path) -> None:
    scheduler_path, _ = _write_one_terminal(tmp_path)
    aggregate = aggregate_schedulers([scheduler_path])

    assert aggregate["planned_denominator"] == 210
    assert aggregate["overall"]["planned"] == 210
    assert aggregate["overall"]["evaluable_denominator"] == 1
    assert aggregate["overall"]["successes"] == 0
    assert len(aggregate["per_model"]) == 7
    assert {item["planned"] for item in aggregate["per_model"].values()} == {30}
    assert len(aggregate["per_robot_model"]) == 14
    assert {item["planned"] for item in aggregate["per_robot_model"].values()} == {15}
    assert len(aggregate["per_robot_task_model"]) == 70
    assert {item["planned"] for item in aggregate["per_robot_task_model"].values()} == {3}

    with pytest.raises(B2AggregationError, match="without a trustworthy executed"):
        aggregate_schedulers([scheduler_path], require_complete=True)


def test_unfinished_scheduler_without_finish_time_is_rejected(tmp_path: Path) -> None:
    manifest = resolve_manifest()
    scheduler_path = tmp_path / "unfinished.json"
    scheduler_path.write_text(
        json.dumps(
            {
                "artifact_type": "b2_formal_scheduler",
                "authority": {
                    "document_id": AUTHORITY_DOCUMENT_ID,
                    "revision": AUTHORITY_REVISION,
                },
                "manifest_revision": AUTHORITY_REVISION,
                "planned_unit_ids": [manifest.units[0].unit_id],
                "records": [],
                "utc_finished_at": None,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(B2AggregationError, match="utc_finished_at"):
        aggregate_schedulers([scheduler_path])


def test_finished_scheduler_missing_a_planned_record_is_rejected(
    tmp_path: Path,
) -> None:
    manifest = resolve_manifest()
    scheduler_path = tmp_path / "missing-record.json"
    scheduler_path.write_text(
        json.dumps(
            {
                "artifact_type": "b2_formal_scheduler",
                "authority": {
                    "document_id": AUTHORITY_DOCUMENT_ID,
                    "revision": AUTHORITY_REVISION,
                },
                "manifest_revision": AUTHORITY_REVISION,
                "planned_unit_ids": [manifest.units[0].unit_id],
                "records": [],
                "utc_finished_at": "2026-08-23T00:01:00Z",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(B2AggregationError, match="scheduler is incomplete"):
        aggregate_schedulers([scheduler_path])


def test_harness_fail_with_physical_integrity_failure_remains_evaluable(
    tmp_path: Path,
) -> None:
    scheduler_path, terminal_path = _write_one_terminal(tmp_path)
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["harness"]["physical_integrity_passed"] = False
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")

    aggregate = aggregate_schedulers([scheduler_path])

    assert aggregate["overall"]["failures"] == 1
    assert aggregate["overall"]["evaluable_denominator"] == 1


def test_scheduler_cannot_cherry_pick_terminal_from_another_bundle(
    tmp_path: Path,
) -> None:
    scheduler_path, terminal_path = _write_one_terminal(tmp_path)
    scheduler = json.loads(scheduler_path.read_text(encoding="utf-8"))
    copied = tmp_path / "elsewhere" / terminal_path.name
    copied.parent.mkdir()
    copied.write_text(terminal_path.read_text(encoding="utf-8"), encoding="utf-8")
    scheduler["records"][0]["terminal_path"] = str(copied)
    scheduler_path.write_text(json.dumps(scheduler), encoding="utf-8")

    with pytest.raises(B2AggregationError, match="outside its run bundle"):
        aggregate_schedulers([scheduler_path])


def test_all_not_run_records_do_not_complete_the_formal_cohort(
    tmp_path: Path,
) -> None:
    manifest = resolve_manifest()
    terminal_root = tmp_path / "terminals"
    terminal_root.mkdir()
    records = []
    for unit in manifest.units:
        terminal_path = terminal_root / f"{unit.unit_id.replace('::', '__')}.json"
        terminal_path.write_text(
            json.dumps(
                {
                    "artifact_type": "b2_formal_unit_terminal",
                    "authority": {
                        "document_id": AUTHORITY_DOCUMENT_ID,
                        "revision": AUTHORITY_REVISION,
                    },
                    "manifest_revision": AUTHORITY_REVISION,
                    "formal_episode": True,
                    "code_version": {"git_commit": "test-commit"},
                    "unit_id": unit.unit_id,
                    **unit.as_dict(),
                    "utc_finished_at": "2026-08-23T00:00:00Z",
                    "classification": "not_run",
                    "evaluable": False,
                    "success": False,
                }
            ),
            encoding="utf-8",
        )
        records.append(
            {
                "unit_id": unit.unit_id,
                "status": "terminal",
                "terminal_path": str(terminal_path),
            }
        )
    scheduler_path = tmp_path / "scheduler.json"
    scheduler_path.write_text(
        json.dumps(
            {
                "artifact_type": "b2_formal_scheduler",
                "authority": {
                    "document_id": AUTHORITY_DOCUMENT_ID,
                    "revision": AUTHORITY_REVISION,
                },
                "manifest_revision": AUTHORITY_REVISION,
                "planned_unit_ids": [unit.unit_id for unit in manifest.units],
                "records": records,
                "utc_finished_at": "2026-08-23T00:01:00Z",
            }
        ),
        encoding="utf-8",
    )

    aggregate = aggregate_schedulers([scheduler_path])
    assert aggregate["complete"] is False
    assert aggregate["overall"]["status_counts"] == {"not_run": 210}
    with pytest.raises(B2AggregationError, match="without a trustworthy executed"):
        aggregate_schedulers([scheduler_path], require_complete=True)
