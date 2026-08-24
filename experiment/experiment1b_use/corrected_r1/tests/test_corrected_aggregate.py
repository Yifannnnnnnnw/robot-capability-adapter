from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from experiment.experiment1b_use.corrected_r1.analysis.aggregate_corrected import (
    AUDIT_IDENTITY,
    _scheduler_artifacts,
    aggregate_corrected,
)
from experiment.experiment1b_use.corrected_r1.analysis.realtime_videos import (
    _convert,
)
from experiment.experiment1b_use.corrected_r1.analysis.rejudge_retained import (
    rejudge_retained,
)


ROOT = Path(__file__).resolve().parents[4]
CORRECTED = ROOT / "experiment/experiment1b_use/corrected_r1"
MANIFEST = CORRECTED / "config/manifest.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_corrected_manifest_is_isolated_and_has_70_unique_r1_units() -> None:
    manifest = _load(MANIFEST)
    suite = _load(CORRECTED / "config/task_suite.json")
    units = manifest["retained_units"] + manifest["fresh_units"]

    assert manifest["audit_identity"] == AUDIT_IDENTITY
    assert manifest["formal_episode"] is False
    assert manifest["formal_denominator_entry"] is False
    assert manifest["old_formal_plan_unchanged"] == 210
    assert manifest["execution_origin_counts"] == {
        "retained_rejudged": 19,
        "fresh_corrected": 49,
        "replacement": 2,
    }
    assert len(units) == len({unit["unit_id"] for unit in units}) == 70
    assert {unit["replicate_id"] for unit in units} == {"R1"}
    assert len(manifest["fresh_units"]) == 51
    replacements = [
        unit for unit in units if unit["execution_origin"] == "replacement"
    ]
    assert len(replacements) == 2
    assert all("original_incomplete_terminal_path" in unit for unit in replacements)
    assert suite["formal_episode"] is False
    assert suite["replicate_plan"] == {"replicate_ids": ["R1"]}
    assert suite["task_count"] == 10


def test_retained_semantic_gate_and_sidecars_do_not_overwrite_sources(
    tmp_path: Path,
) -> None:
    manifest = _load(MANIFEST)
    source_paths = [ROOT / unit["original_terminal_path"] for unit in manifest["retained_units"]]
    before = {path: path.read_bytes() for path in source_paths}
    sidecars = rejudge_retained(
        manifest_path=MANIFEST,
        output_dir=tmp_path / "sidecars",
        equivalence_path=tmp_path / "equivalence.json",
    )

    assert len(sidecars) == 19
    assert all(sidecar["semantic_equivalence"]["passed"] is True for sidecar in sidecars)
    assert all(sidecar["formal_episode"] is False for sidecar in sidecars)
    assert all(sidecar["execution_origin"] == "retained_rejudged" for sidecar in sidecars)
    assert all(
        sidecar["source_evidence"]["original_records_modified"] is False
        for sidecar in sidecars
    )
    for sidecar in sidecars:
        pairs = sidecar["corrected_result"]["harness"]["contact_integrity"]["pair_results"]
        assert pairs
        assert all(
            {
                "geom1",
                "geom2",
                "body1",
                "body2",
                "minimum_distance_m",
                "applied_threshold_m",
            }
            <= set(pair)
            for pair in pairs
        )
    assert {path: path.read_bytes() for path in source_paths} == before


def test_complete_aggregate_counts_each_origin_and_cost_once(tmp_path: Path) -> None:
    sidecar_root = tmp_path / "sidecars"
    rejudge_retained(
        manifest_path=MANIFEST,
        output_dir=sidecar_root,
        equivalence_path=tmp_path / "equivalence.json",
    )
    manifest = _load(MANIFEST)
    fresh_root = tmp_path / "fresh"
    fresh_root.mkdir()
    for index, unit in enumerate(manifest["fresh_units"]):
        evidence = fresh_root / unit["unit_id"].replace("::", "__")
        evidence.mkdir()
        provider = evidence / "provider_record.json"
        provider.write_text(
            json.dumps({"total_cost_usd": 0.25}), encoding="utf-8"
        )
        terminal = {
            "artifact_type": "b2_corrected_r1_unit_terminal",
            "schema_version": "1.0",
            "audit_identity": AUDIT_IDENTITY,
            "formal_episode": False,
            **{
                key: unit[key]
                for key in (
                    "unit_id",
                    "robot_configuration_id",
                    "task_id",
                    "model_id",
                    "replicate_id",
                    "execution_origin",
                )
            },
            "classification": "harness_pass",
            "evaluable": True,
            "success": True,
            "model_identity": {
                "requested_model": f"test-model-{index}",
                "returned_models": [f"test-model-{index}"],
                "exact_match": True,
            },
            "harness": {
                "physical_harness_verdict": "PASS",
                "physical_integrity_passed": True,
                "video_complete": True,
                "video": {"path": f"video-{index}.mp4"},
            },
            "provider_record_path": str(provider),
        }
        if unit["execution_origin"] == "replacement":
            terminal["original_incomplete_terminal_path"] = str(
                ROOT / unit["original_incomplete_terminal_path"]
            )
        (evidence / "terminal.json").write_text(
            json.dumps(terminal), encoding="utf-8"
        )

    aggregate = aggregate_corrected(
        manifest_path=MANIFEST,
        sidecar_roots=[sidecar_root],
        fresh_roots=[fresh_root],
        require_complete=True,
    )

    assert aggregate["complete"] is True
    assert aggregate["overall"]["planned"] == 70
    assert aggregate["overall"]["evaluable_denominator"] == 70
    assert len(aggregate["results"]) == 70
    assert len({row["unit_id"] for row in aggregate["results"]}) == 70
    assert sum(row["execution_origin"] == "retained_rejudged" for row in aggregate["results"]) == 19
    assert sum(row["execution_origin"] == "fresh_corrected" for row in aggregate["results"]) == 49
    assert sum(row["execution_origin"] == "replacement" for row in aggregate["results"]) == 2
    assert aggregate["costs"]["new_corrected_51_known_cost_usd"] == pytest.approx(12.75)
    assert aggregate["costs"]["original_superseded_incomplete_2_known_cost_usd"] == pytest.approx(
        0.7521635 + 0.8085825
    )
    assert aggregate["costs"]["old_batch001_manual_sidecar_included"] is False
    replacement_rows = [
        row for row in aggregate["results"] if row["execution_origin"] == "replacement"
    ]
    assert all(
        row["replacement_source"]["included_in_corrected_evidence_cost"] is False
        for row in replacement_rows
    )
    assert set(aggregate["per_model"]) == {"M1", "M2", "M3", "M4", "M5", "M6", "M8"}
    assert set(aggregate["per_robot"]) == {
        "robotstudio_so101",
        "unitree-go2-stock-12dof",
    }
    assert len(aggregate["per_task"]) == 10


def test_realtime_conversion_is_20fps_and_half_source_duration(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("ffmpeg/ffprobe are unavailable")
    source = tmp_path / "source.mp4"
    output = tmp_path / "realtime.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x120:r=10:d=2",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    source_before = source.read_bytes()
    source_probe, output_probe = _convert(
        source=source,
        output=output,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        force=False,
    )

    assert source.read_bytes() == source_before
    assert source_probe["fps"] == pytest.approx(10.0)
    assert output_probe["fps"] == pytest.approx(20.0)
    assert output_probe["duration_s"] == pytest.approx(
        source_probe["duration_s"] / 2.0, abs=0.1
    )


def test_scheduler_uses_only_selected_retry_terminal(tmp_path: Path) -> None:
    unit_id = "b2-corrected-r1::robot::task::M1::R1"
    first = tmp_path / "attempts/attempt-1/terminal.json"
    selected = tmp_path / "attempts/attempt-2/terminal.json"
    first.parent.mkdir(parents=True)
    selected.parent.mkdir(parents=True)
    first.write_text(
        json.dumps(
            {
                "artifact_type": "b2_corrected_r1_unit_terminal",
                "unit_id": unit_id,
                "classification": "infrastructure_failure",
                "evaluable": False,
                "success": False,
            }
        ),
        encoding="utf-8",
    )
    selected_value = {
        "artifact_type": "b2_corrected_r1_unit_terminal",
        "unit_id": unit_id,
        "classification": "harness_pass",
        "evaluable": True,
        "success": True,
    }
    selected.write_text(json.dumps(selected_value), encoding="utf-8")
    scheduler = tmp_path / "scheduler.json"
    scheduler.write_text(
        json.dumps(
            {
                "artifact_type": "b2_corrected_r1_scheduler",
                "audit_identity": AUDIT_IDENTITY,
                "formal_episode": False,
                "records": [
                    {
                        "unit_id": unit_id,
                        "terminal_path": str(selected),
                        "classification": "harness_pass",
                        "evaluable": True,
                        "success": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    artifacts = _scheduler_artifacts([scheduler])
    assert list(artifacts) == [unit_id]
    assert artifacts[unit_id][0] == selected.resolve()
    assert artifacts[unit_id][1] == selected_value
