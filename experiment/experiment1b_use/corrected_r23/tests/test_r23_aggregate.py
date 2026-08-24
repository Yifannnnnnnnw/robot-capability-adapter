from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter/src"
for root in (REPOSITORY_ROOT, SOURCE_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from experiment.experiment1b_use.corrected_r23.analysis.aggregate_r23 import (  # noqa: E402
    R23AggregationError,
    aggregate,
)


MANIFEST = (
    REPOSITORY_ROOT
    / "experiment/experiment1b_use/corrected_r23/config/manifest.json"
)
R1_AGGREGATE = (
    REPOSITORY_ROOT
    / "experiment/experiment1b_use/corrected_r1/analysis/corrected_r1_aggregate.json"
)
IDENTITY = {"document_id": "AA2-B2-CORRECTED-R23", "revision": "1.0.0"}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _terminal(
    *,
    root: Path,
    unit: dict,
    attempt_number: int,
    classification: str,
    selected: bool,
    video: Path,
) -> Path:
    unit_id = unit["unit_id"]
    safe = unit_id.replace("::", "__")
    provider_path = root / f"provider-{safe}-{attempt_number}.json"
    _write(
        provider_path,
        {
            "artifact_type": "b2_corrected_r23_provider_record",
            "audit_identity": IDENTITY,
            "unit": unit,
            "calls": [
                {
                    "status": "success",
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
    terminal_path = root / f"terminal-{safe}-{attempt_number}.json"
    evaluable = classification in {"harness_pass", "harness_fail"}
    success = classification == "harness_pass"
    _write(
        terminal_path,
        {
            "artifact_type": "b2_corrected_r23_unit_terminal",
            "audit_identity": IDENTITY,
            "formal_episode": False,
            "formal_denominator_entry": False,
            **unit,
            "classification": classification,
            "evaluable": evaluable,
            "success": success,
            "code_version": {"git_commit": "fixed-test-commit"},
            "model_identity": {
                "requested_model": "exact-model",
                "returned_models": ["exact-model"],
                "exact_match": True,
            },
            "harness": {
                "physical_harness_verdict": "PASS" if success else "FAIL",
                "physical_integrity_passed": True,
                "video_complete": True,
            }
            if evaluable
            else {},
            "provider_record_path": str(provider_path),
            "video_path": str(video) if selected else None,
        },
    )
    return terminal_path


def test_complete_r23_aggregate_counts_one_outcome_and_every_attempt_cost(
    tmp_path: Path,
) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    records = []
    for index, unit in enumerate(manifest["fresh_units"]):
        attempts = []
        if index == 0:
            first = _terminal(
                root=tmp_path,
                unit=unit,
                attempt_number=1,
                classification="infrastructure_failure",
                selected=False,
                video=video,
            )
            attempts.append(
                {
                    "attempt_number": 1,
                    "terminal_path": str(first),
                    "classification": "infrastructure_failure",
                    "evaluable": False,
                    "success": False,
                }
            )
        final_number = len(attempts) + 1
        final = _terminal(
            root=tmp_path,
            unit=unit,
            attempt_number=final_number,
            classification="harness_pass" if index % 2 == 0 else "harness_fail",
            selected=True,
            video=video,
        )
        attempts.append(
            {
                "attempt_number": final_number,
                "terminal_path": str(final),
                "classification": "harness_pass" if index % 2 == 0 else "harness_fail",
                "evaluable": True,
                "success": index % 2 == 0,
            }
        )
        records.append(
            {
                "unit_id": unit["unit_id"],
                "attempts": attempts,
                "retry_used": len(attempts) == 2,
                "terminal_path": str(final),
                "classification": attempts[-1]["classification"],
                "evaluable": True,
                "success": attempts[-1]["success"],
            }
        )
    scheduler = tmp_path / "scheduler.json"
    _write(
        scheduler,
        {
            "artifact_type": "b2_corrected_r23_scheduler",
            "audit_identity": IDENTITY,
            "formal_episode": False,
            "formal_denominator_entry": False,
            "company_workers": 3,
            "m5_workers": 1,
            "planned_unit_ids": [record["unit_id"] for record in records],
            "records": records,
        },
    )
    report = aggregate(
        scheduler_paths=[scheduler],
        manifest_path=MANIFEST,
        r1_aggregate_path=R1_AGGREGATE,
        require_complete=True,
    )
    assert report["complete"] is True
    assert report["overall"]["planned"] == 140
    assert report["overall"]["successes"] == 70
    assert report["overall"]["failures"] == 70
    assert report["usage"]["selected_outcomes"]["attempts"] == 140
    assert report["usage"]["all_attempts_actual_spend"]["attempts"] == 141
    assert report["usage"]["infrastructure_retries"] == 1
    assert report["corrected_210_combined"]["overall"]["planned"] == 210
    assert report["corrected_210_combined"]["overall"]["executed"] == 210


def test_require_complete_rejects_missing_r23_units() -> None:
    with pytest.raises(R23AggregationError, match="140 units are missing"):
        aggregate(
            scheduler_paths=[],
            manifest_path=MANIFEST,
            r1_aggregate_path=R1_AGGREGATE,
            require_complete=True,
        )
