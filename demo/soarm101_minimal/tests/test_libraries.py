from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from soarm_demo.generation import GenerationWorkspace
from soarm_demo.libraries import (
    ExperienceLibrary,
    ExperienceNotFoundError,
    LibraryEntry,
    LibraryError,
    load_jsonl,
    load_structured,
)
from soarm_demo.pipeline import DemoPaths, PipelineError, materialize_inputs
from soarm_demo.run_manifest import RunManifest


ROOT = Path(__file__).resolve().parents[1]


def _record(experience_id: str, version: str) -> dict[str, Any]:
    return {
        "schema_version": "robot_capability.experience_record.v1",
        "experience_id": experience_id,
        "version": version,
        "status": "approved",
        "scope": {
            "robot_ids": ["soarm101"],
            "runtime_ids": ["lerobot_soarm101_0_6_0"],
            "granularities": ["G2"],
            "capability_ids": ["G2.move_to_pose"],
        },
        "origin": {
            "source_stage": "direct_function_validation",
            "run_id": "private-run-id",
            "case_or_task_ids": ["private-case-id"],
            "observed_at": "2026-08-04T00:00:00Z",
        },
        "failure_summary": {
            "category": "control",
            "symptom": f"symptom-{experience_id}",
            "preconditions": ["private precondition"],
        },
        "diagnosis": {
            "hypothesis": "Interpolation was too coarse.",
            "confidence": 0.8,
            "alternatives_considered": ["private alternative"],
        },
        "recommended_change": {
            "change_type": "implementation",
            "guidance": "Use smaller interpolation steps.",
            "must_preserve": ["public signature"],
        },
        "evidence": {
            "artifact_refs": ["private/oracle/source.py"],
            "before_measurements": {"secret": 1},
            "after_measurements": {"secret": 0},
        },
        "outcome": {
            "repair_attempted": True,
            "repair_succeeded": True,
            "regressions": [],
            "reuse_risk": "low",
        },
        "generation_summary": {
            "applicability": "SO-ARM101 Cartesian motion",
            "lesson": "Interpolate conservatively.",
            "recommended_pattern": "Bound each joint increment.",
            "avoid_pattern": "One-shot target jumps.",
        },
        "private_task_or_oracle_content": "must never enter generation view",
    }


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def test_committed_empty_experience_library_is_operational() -> None:
    records = ROOT / "libraries" / "experience" / "v1" / "records.jsonl"
    library = ExperienceLibrary(records)

    assert library.list() == []
    assert library.select([]) == []
    assert library.generation_view([]) == []


def test_experience_list_is_deterministic_and_select_preserves_requested_order(
    tmp_path: Path,
) -> None:
    records = tmp_path / "records.jsonl"
    _write_records(records, [_record("z-last", "1.0.0"), _record("a-first", "2.0.0")])
    library = ExperienceLibrary(records)

    assert [item["experience_id"] for item in library.list()] == ["a-first", "z-last"]
    selected = library.select(["z-last", "a-first"])
    assert [item["experience_id"] for item in selected] == ["z-last", "a-first"]


def test_experience_select_rejects_duplicates_and_missing_ids(tmp_path: Path) -> None:
    records = tmp_path / "records.jsonl"
    _write_records(records, [_record("known", "1.0.0")])
    library = ExperienceLibrary(records)

    with pytest.raises(LibraryError, match="duplicate"):
        library.select(["known", "known"])
    with pytest.raises(ExperienceNotFoundError, match="missing"):
        library.select(["missing"])


def test_generation_view_returns_only_manifest_allowlisted_fields(tmp_path: Path) -> None:
    records = tmp_path / "records.jsonl"
    _write_records(records, [_record("safe", "1.0.0")])
    library = ExperienceLibrary(records)

    view = library.generation_view(["safe"])[0]

    assert set(view) == {
        "experience_id",
        "version",
        "scope",
        "failure_summary",
        "diagnosis",
        "recommended_change",
        "outcome",
        "generation_summary",
    }
    assert set(view["failure_summary"]) == {"category", "symptom"}
    assert set(view["diagnosis"]) == {"hypothesis", "confidence"}
    assert set(view["recommended_change"]) == {"change_type", "guidance", "must_preserve"}
    assert set(view["outcome"]) == {"repair_succeeded", "regressions", "reuse_risk"}
    serialized = json.dumps(view, sort_keys=True)
    assert "private-run-id" not in serialized
    assert "private-case-id" not in serialized
    assert "private/oracle/source.py" not in serialized
    assert "must never enter generation view" not in serialized
    assert "private precondition" not in serialized


def test_all_four_committed_libraries_pass_runtime_schema_validation() -> None:
    for name, root in DemoPaths(ROOT).libraries.items():
        report = LibraryEntry.open(root).validate_runtime_schemas(ROOT / "schemas")
        assert report["ok"], f"{name}: {report['errors']}"
        assert report["validated"]


def test_task_runtime_validation_rejects_stale_public_split_binding(
    tmp_path: Path,
) -> None:
    source = ROOT / "libraries/tasks/soarm101_tabletop/v1"
    copied = tmp_path / "tasks"
    shutil.copytree(source, copied)
    split_path = copied / "splits/split_manifest.json"
    split = load_structured(split_path)
    split["content_hashes"]["taxonomy.yaml"] = "0" * 64
    split_path.write_text(json.dumps(split, sort_keys=True), encoding="utf-8")

    report = LibraryEntry.open(copied).validate_runtime_schemas(ROOT / "schemas")

    assert report["ok"] is False
    assert any(
        "taxonomy.yaml" in error and "hash mismatch" in error
        for error in report["errors"]
    )


def test_morphology_controlled_point_ik_contract_has_manifested_real_evidence() -> None:
    root = ROOT / "libraries" / "morphology" / "soarm101" / "v1"
    manifest = load_structured(root / "manifest.yaml")
    sources = load_structured(root / "sources.yaml")
    morphology = load_structured(root / "kinematics.yaml")

    generation_payloads = {
        payload["path"]
        for payload in manifest["payloads"]
        if payload["visibility"] == "generation"
    }
    assert {"kinematics.yaml", "sources.yaml", "PROVENANCE.md"} <= (
        generation_payloads
    )

    contract = morphology["gripper_contact_model"][
        "verified_tabletop_pinch_baseline"
    ]["verified_controlled_point_ik"]
    evidence = next(
        item
        for item in sources["locally_derived_evidence"]
        if item["evidence_id"] == "soarm101_selected_open_controlled_point_ik_v1"
    )
    assert contract["status"] == "verified"
    assert contract["tolerance_m"] == 0.0006
    assert evidence["kind"] == "deterministic_real_mujoco_regression"
    assert evidence["source"] == (
        "tests/test_gripper_contact_profile.py::"
        "test_command_20_profile_places_releases_and_retreats_with_"
        "post_conversion_fk_bound"
    )
    assert "placement lower" in evidence["directly_measured_claim"]
    assert "runtime-accepted radians" in evidence["directly_computed_claim"]
    assert "not an upstream manufacturer claim" in evidence[
        "derived_safety_contract_requirement"
    ]
    assert "send_action/get_observation" in evidence["public_runtime_boundary"]

    packaging = manifest["provenance"]["model_asset_packaging"]
    assert packaging["packaging_change_only"] is True
    assert packaging["scope"] == ["model/so101.xml", "model/meshes/*.stl"]
    assert "kinematics.yaml" in packaging["excluded_from_claim"]
    assert "packaging_change_only" not in manifest["provenance"]


def test_runtime_schema_validation_rejects_invalid_task_before_materialization(
    tmp_path: Path,
) -> None:
    source = ROOT / "libraries" / "tasks" / "soarm101_tabletop" / "v1"
    copied = tmp_path / "tasks"
    shutil.copytree(source, copied)
    records = load_jsonl(copied / "visible_tasks.jsonl")
    records[0].pop("task_id")
    (copied / "visible_tasks.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )

    report = LibraryEntry.open(copied).validate_runtime_schemas(ROOT / "schemas")

    assert report["ok"] is False
    assert any("task_id" in error and "required" in error for error in report["errors"])


def test_runtime_schema_validation_rejects_invalid_generated_runtime_surface(
    tmp_path: Path,
) -> None:
    source = ROOT / "libraries" / "sdk_runtime" / "lerobot_soarm101" / "0.6.0"
    copied = tmp_path / "sdk_runtime"
    shutil.copytree(source, copied)
    contract = load_structured(copied / "runtime_contract.yaml")
    contract["runtime_view"]["generated_package_surface"]["required_methods"].pop(
        "send_action"
    )
    (copied / "runtime_contract.yaml").write_text(
        json.dumps(contract, indent=2), encoding="utf-8"
    )

    report = LibraryEntry.open(copied).validate_runtime_schemas(ROOT / "schemas")

    assert report["ok"] is False
    assert any("send_action" in error and "required" in error for error in report["errors"])


def test_materialize_inputs_validates_all_libraries_before_copying_any_bytes(
    tmp_path: Path,
) -> None:
    source = ROOT / "libraries" / "tasks" / "soarm101_tabletop" / "v1"
    invalid_tasks = tmp_path / "invalid-tasks"
    shutil.copytree(source, invalid_tasks)
    records = load_jsonl(invalid_tasks / "visible_tasks.jsonl")
    records[0].pop("task_id")
    (invalid_tasks / "visible_tasks.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    libraries = dict(DemoPaths(ROOT).libraries)
    libraries["tasks"] = invalid_tasks
    paths = SimpleNamespace(root=ROOT, libraries=libraries)
    run = RunManifest.create(tmp_path / "runs", run_id="invalid-input")

    with pytest.raises(PipelineError, match="tasks library schema validation failed"):
        materialize_inputs(paths, run)

    snapshot = run.root / "input_snapshot"
    assert snapshot.is_dir()
    assert list(snapshot.iterdir()) == []


@pytest.mark.parametrize(
    ("suffix", "content", "loader"),
    [
        (".json", '{"value": NaN}\n', load_structured),
        (".jsonl", '{"value": Infinity}\n', load_jsonl),
        (".yaml", "value: .inf\n", load_structured),
    ],
)
def test_library_loaders_reject_non_finite_numbers(
    tmp_path: Path,
    suffix: str,
    content: str,
    loader: Any,
) -> None:
    path = tmp_path / f"invalid{suffix}"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(LibraryError, match="non-finite"):
        loader(path)


def test_input_library_report_is_outside_generation_snapshot(tmp_path: Path) -> None:
    run = RunManifest.create(tmp_path / "runs", run_id="snapshot-isolation")

    snapshot, reports = materialize_inputs(DemoPaths(ROOT), run)

    assert set(reports) == {"morphology", "sdk_runtime", "tasks", "experience"}
    assert (run.root / "input_library_report.json").is_file()
    assert not (snapshot / "input_report.json").exists()
    assert not (snapshot / "input_library_report.json").exists()
    context = GenerationWorkspace(
        input_snapshot=snapshot,
        run_root=run.root / "generation",
    ).read_generation_snapshot()
    assert "input_library_report.json" not in context["files"]
    assert "input_report.json" not in context["files"]


def test_generation_task_snapshot_excludes_private_split_metadata(
    tmp_path: Path,
) -> None:
    run = RunManifest.create(tmp_path / "runs", run_id="task-split-redaction")

    snapshot, _ = materialize_inputs(DemoPaths(ROOT), run)
    task_snapshot = snapshot / "tasks"

    assert not (task_snapshot / "splits/split_manifest.json").exists()
    assert set(
        path.relative_to(task_snapshot).as_posix()
        for path in task_snapshot.rglob("*")
        if path.is_file()
    ) == {
        "environment_requirements.yaml",
        "snapshot.json",
        "sources.yaml",
        "taxonomy.yaml",
        "visible_tasks.jsonl",
    }
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(task_snapshot.rglob("*"))
        if path.is_file()
    ).lower()
    assert "selection_seed" not in serialized
    assert "private_ref" not in serialized
    assert "private/task_library" not in serialized
    assert "private_heldout_tasks" not in serialized
    # The public 9/3 policy remains visible, but no held-out ID, instruction,
    # concrete pose, oracle or private-content fingerprint crosses the boundary.
    assert "visible_count: 9" in serialized
    assert "pilot_heldout_count: 3" in serialized
