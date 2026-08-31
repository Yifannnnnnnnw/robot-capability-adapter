from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import runtime.b2 as b2
import runtime.readiness as readiness


def test_retained_reference_and_v6_delta_evidence_pass_together() -> None:
    manifest = b2.resolve_manifest()

    assert manifest.readiness_evidence["interface_calibration"] == {
        "index_path": str(
            (
                b2.REPOSITORY_ROOT
                / "experiment/archive/runs/b2_recap/"
                "reference-calibration-a6-356c400-video/"
                "reference_calibration_index.json"
            ).resolve()
        ),
        "run_id": "reference-calibration-a6-356c400-video",
        "code_revision": "356c4000f1956af90820d602b638328930457c39",
        "case_count": 33,
        "video_count": 33,
    }
    v6 = manifest.readiness_evidence["pick_place_v6"]
    assert v6["object_goal_distance_m"] == pytest.approx(0.0117583402168305)
    assert v6["minimum_contact_distance_m"] == pytest.approx(
        -0.0022633501697411984
    )
    assert v6["video_frame_count"] == 305


def test_missing_historical_video_blocks_before_credential_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit_id = b2.resolve_manifest().units[0].unit_id
    report_path = (
        b2.REPOSITORY_ROOT
        / "experiment/archive/runs/b2_recap/"
        "reference-calibration-a6-356c400-video/robotstudio_so101/"
        "reference_calibration_report.json"
    ).resolve()
    original_read = readiness._read

    def altered_read(path: Path, *, label: str):
        document = original_read(path, label=label)
        if path.resolve() == report_path:
            document = copy.deepcopy(document)
            document["trials"][0]["video"]["path"] = (
                "/Users/wangyifan/Projects/auto_adapter2.0/experiment/b2_recap/runs/"
                "reference-calibration-a6-356c400-video/robotstudio_so101/"
                "videos/MISSING.mp4"
            )
        return document

    monkeypatch.setattr(readiness, "_read", altered_read)
    credential_loaded = False

    def credential_loader(*_args):
        nonlocal credential_loaded
        credential_loaded = True
        return "must-not-be-read"

    with pytest.raises(b2.B2FormalError, match="historical video path"):
        b2.run_formal_unit(
            unit_id,
            output_root=tmp_path,
            credential_loader=credential_loader,
    )
    assert credential_loaded is False
    assert list(tmp_path.iterdir()) == []


def test_v6_summary_cannot_hide_a_harness_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = (
        b2.REPOSITORY_ROOT
        / "experiment/experiment1b_use/runs/diagnostic/"
        "pick-place-v6-oracle-full-video/robotstudio_so101/mw_pick_place/"
        "oracle_canary_report.json"
    ).resolve()
    original_read = readiness._read

    def altered_read(path: Path, *, label: str):
        document = original_read(path, label=label)
        if path.resolve() == report_path:
            document = copy.deepcopy(document)
            document["episode"]["harness"]["physical_harness_verdict"] = "FAIL"
        return document

    monkeypatch.setattr(readiness, "_read", altered_read)
    with pytest.raises(b2.B2FormalError, match="Harness is not an exact complete PASS"):
        b2.resolve_manifest()


def test_v6_trace_must_match_the_current_oracle_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = (
        b2.REPOSITORY_ROOT
        / "experiment/experiment1b_use/runs/diagnostic/"
        "pick-place-v6-oracle-full-video/robotstudio_so101/mw_pick_place/"
        "oracle_canary_report.json"
    ).resolve()
    original_read = readiness._read

    def altered_read(path: Path, *, label: str):
        document = original_read(path, label=label)
        if path.resolve() == report_path:
            document = copy.deepcopy(document)
            document["episode"]["controller"]["trace"][3]["public_arguments"][
                "target_position_m"
            ][0] = 0.38
        return document

    monkeypatch.setattr(readiness, "_read", altered_read)
    with pytest.raises(b2.B2FormalError, match="trace differs"):
        b2.resolve_manifest()
