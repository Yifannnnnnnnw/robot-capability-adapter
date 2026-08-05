from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

import soarm_demo.evolution as evolution_module
from soarm_demo.audit import sha256_bytes, sha256_file, sha256_json
from soarm_demo.evolution import (
    EvolutionError,
    EvolutionLimits,
    build_evolution_projection,
    evaluate_and_publish_evolution_audit,
    evaluate_evolution_audit,
)
from soarm_demo.evolution_runtime import (
    EvolutionRuntimeError,
    _AuditWorkspace,
    _build_public_repository_index,
    _evolution_report_semantic_errors,
    _read_only_tools,
    _sanitize_public_repository_text,
    build_global_evidence_projection,
    run_evolution,
)
from soarm_demo.libraries import ExperienceLibrary, LibraryEntry, load_structured
from soarm_demo.model_client import ScriptedModelClient


ROOT = Path(__file__).resolve().parents[1]
REAL_SOURCE = ROOT / "runs/aws-postfix-evolution-source-20260805b"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _terminal_fixture(run_root: Path, *, run_id: str = "evolution-source-fixture") -> None:
    report = {
        "schema_version": "robot_capability.terminal_run_report.v2",
        "run_id": run_id,
        "terminal_state": "GENERATION_FAILED",
        "terminal_reason": "FixtureFailure",
        "mode": "aws",
        "ended_at": "2026-08-05T12:00:00Z",
        "error": {"type": "FixtureFailure"},
        "input_hashes": {"source_tree": "a" * 64},
        "artifact_hashes": {},
        "budgets": {},
        "model_accounting": {
            "public_identities": {},
            "roles": {},
            "aggregate": {},
            "generation_phases": {
                "stage1": None,
                "stage2_initial": None,
                "repair_rounds": [],
            },
        },
        "repair": {
            "round_limit": 10,
            "rounds_used": 0,
            "rounds_remaining": 10,
            "model_call_limit_per_round": 6,
            "results": [],
            "history": [],
        },
        "video_recording": {
            "schema_version": "robot_capability.mujoco_video_status.v1",
            "required": True,
            "status": "unavailable",
            "reason_code": "fixture_no_video",
        },
        "evidence_files": [],
    }
    report_path = run_root / "terminal_report.json"
    _write_json(report_path, report)
    _write_json(
        run_root / "run_manifest.json",
        {
            "run_id": report["run_id"],
            "state": report["terminal_state"],
            "artifact_hashes": {"terminal_report": sha256_file(report_path)},
            "events": [],
        },
    )


def _empty_experience_library(demo: Path) -> None:
    records = demo / "libraries/experience/v1/records.jsonl"
    records.write_bytes(b"")
    manifest_path = demo / "libraries/experience/v1/manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["generation_visibility"] = "mixed"
    manifest["compatibility"]["record_count"] = 0
    manifest["content_hashes"]["records.jsonl"] = sha256_bytes(b"")
    manifest["content_hashes"]["sources.yaml"] = sha256_file(
        demo / "libraries/experience/v1/sources.yaml"
    )
    manifest["content_hashes"]["experience.schema.json"] = sha256_file(
        demo / "libraries/experience/v1/experience.schema.json"
    )
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _demo_fixture(tmp_path: Path) -> tuple[Path, Path]:
    demo = tmp_path / "demo"
    for name in ("schemas", "prompts", "configs", "src"):
        shutil.copytree(ROOT / name, demo / name)
    for name in ("morphology", "sdk_runtime", "tasks", "experience"):
        shutil.copytree(ROOT / "libraries" / name, demo / "libraries" / name)
    for name in (
        "README.md",
        "DEMO_PLAN.md",
        "SOARM101_MINIMAL_DEMO_PLAN.md",
        "EVOLUTION_GOAL.md",
        "EVOLUTION_DESIGN.md",
        "EVOLUTION_CORRECTION_RECEIPT.json",
        "RESULTS.md",
    ):
        if (ROOT / name).is_file():
            shutil.copy2(ROOT / name, demo / name)
    _empty_experience_library(demo)
    source = demo / "runs/evolution-source-fixture"
    source.mkdir(parents=True)
    _terminal_fixture(source)
    return demo, source


def _unresolved_audit(source_run_id: str) -> dict[str, Any]:
    return {
        "schema_version": "robot_capability.evolution_audit.v1",
        "architecture": "evidence_grounded_audit_synthesize_judge_publish",
        "source_run_id": source_run_id,
        "ranked_findings": [
            {
                "rank": 1,
                "finding_id": "terminal_evidence_gap",
                "title": "The run ended before a downstream evidence chain existed",
                "category": "generation",
                "severity": "high",
                "observation": "The independently verified source terminated in Generation.",
                "hypothesis": "The available evidence cannot localize a capability root cause.",
                "confidence": 0.4,
                "evidence_refs": ["source:terminal_report"],
                "alternatives_considered": [
                    "The terminal failure may be unrelated to capability behavior."
                ],
            },
            {
                "rank": 2,
                "finding_id": "demo_not_reached",
                "title": "No Demo conclusion is available",
                "category": "demo",
                "severity": "medium",
                "observation": "The verified source did not reach Demo.",
                "hypothesis": "Demo performance remains unobserved.",
                "confidence": 0.9,
                "evidence_refs": ["source:terminal_report"],
                "alternatives_considered": [
                    "The earlier terminal failure fully explains the missing Demo."
                ],
            },
        ],
        "selected_finding_id": "terminal_evidence_gap",
        "experience_claim": {
            "conclusion_kind": "unresolved",
            "scope": {
                "robot_ids": ["soarm101"],
                "runtime_ids": ["lerobot_soarm101_0_6_0"],
                "granularities": ["G3"],
                "capability_ids": [],
            },
            "symptom": "The source ended before a complete downstream evidence chain existed.",
            "falsifiable_hypothesis": (
                "A fresh run that reaches unchanged validation will distinguish "
                "capability behavior from this terminal failure."
            ),
            "confidence": 0.4,
            "alternatives_considered": ["The failure may recur before validation."],
            "recommended_change": {
                "change_type": "infrastructure",
                "guidance": (
                    "Start a fresh frozen-input run and retain the unchanged validation "
                    "contract before drawing a capability conclusion."
                ),
                "must_preserve": ["private evaluation boundary", "unchanged thresholds"],
            },
            "generation_summary": {
                "applicability": "SO-ARM101 runs lacking a complete validation chain.",
                "lesson": (
                    "Treat the prior terminal result as unresolved rather than "
                    "capability success or failure."
                ),
                "recommended_pattern": (
                    "Require a fresh unchanged validation chain before reusing a causal diagnosis."
                ),
                "avoid_pattern": (
                    "Do not infer capability behavior from a terminal Generation failure alone."
                ),
            },
            "future_test": {
                "action": "Run a fresh Generation and unchanged Validation.",
                "supporting_result": "The run reaches validation and yields package-bound evidence.",
                "falsifying_result": "The run again terminates before capability evidence exists.",
            },
            "evidence_refs": ["source:terminal_report"],
            "epistemic_status": {
                "source_evidence_verified": True,
                "framework_change_applied": False,
                "capability_improvement_proven": False,
                "new_generation_validation_required": True,
            },
        },
        "privacy": {
            "api_keys_included": False,
            "raw_heldout_tasks_included": False,
            "private_oracles_included": False,
            "test_answers_included": False,
            "exact_hidden_measurements_included": False,
        },
    }


def _scripted_responses(audit: dict[str, Any]) -> list[str]:
    return [
        json.dumps(
            {
                "type": "tool",
                "tool": "read_evolution_evidence",
                "arguments": {"section": "source"},
            }
        ),
        json.dumps(
            {
                "type": "tool",
                "tool": "submit_evolution_audit",
                "arguments": {"audit": audit},
            }
        ),
        json.dumps({"type": "final", "content": "Audit submitted for trusted review."}),
    ]


def test_default_compiler_limit_admits_observed_real_terminal_report() -> None:
    assert EvolutionLimits().max_json_bytes >= 598_979
    assert EvolutionLimits().max_json_bytes <= 2 * 1024 * 1024


def test_correction_receipt_binds_current_experience_authority() -> None:
    receipt = json.loads(
        (ROOT / "EVOLUTION_CORRECTION_RECEIPT.json").read_text(encoding="utf-8")
    )
    publication = receipt["publication"]
    library_root = ROOT / "libraries/experience/v1"
    ledger = ExperienceLibrary(library_root / "records.jsonl")
    records = ledger.list()
    view = ledger.generation_view()

    assert receipt["deterministic_reevaluation"]["model_calls_added"] == 0
    assert receipt["historical_evolution"]["historical_artifacts_modified"] is False
    assert receipt["scientific_status"]["capability_improvement_proven"] is False
    assert publication["record_count"] == len(records) == 2
    assert publication["record_sha256"] == sha256_json(records[-1])
    assert publication["records_sha256"] == sha256_file(library_root / "records.jsonl")
    assert publication["manifest_sha256"] == sha256_file(library_root / "manifest.yaml")
    assert publication["generation_view_record_count"] == len(view) == 1
    assert publication["generation_view_sha256"] == sha256_json(view[0])


@pytest.mark.skipif(not REAL_SOURCE.is_dir(), reason="local real source run is absent")
def test_correction_receipt_replays_trusted_evaluation_without_model_calls() -> None:
    receipt = json.loads(
        (ROOT / "EVOLUTION_CORRECTION_RECEIPT.json").read_text(encoding="utf-8")
    )
    historical = ROOT / "runs/aws-evolution-readonly-final-20260805a"
    assert historical.is_dir()
    source_receipt = receipt["source"]
    historical_receipt = receipt["historical_evolution"]
    reevaluation = receipt["deterministic_reevaluation"]

    assert (REAL_SOURCE / "terminal_report.json").stat().st_size == source_receipt[
        "terminal_report_bytes"
    ]
    assert sha256_file(REAL_SOURCE / "terminal_report.json") == source_receipt[
        "terminal_report_sha256"
    ]
    assert sha256_file(historical / "evolution_report.json") == historical_receipt[
        "report_sha256"
    ]
    assert sha256_file(historical / "evolution/audit.json") == historical_receipt[
        "audit_sha256"
    ]
    assert sha256_file(historical / "evolution/framework_inputs.json") == historical_receipt[
        "framework_inputs_artifact_sha256"
    ]
    framework_inputs = json.loads(
        (historical / "evolution/framework_inputs.json").read_text(encoding="utf-8")
    )
    assert framework_inputs["tree_sha256"] == historical_receipt[
        "framework_inputs_tree_sha256"
    ]

    projection = build_evolution_projection(REAL_SOURCE, schema_root=ROOT / "schemas")
    audit = json.loads(
        (historical / "evolution/audit.json").read_text(encoding="utf-8")
    )
    evaluation, record = evaluate_evolution_audit(
        run_root=REAL_SOURCE,
        audit=audit,
        initial_projection_sha256=sha256_json(projection),
        evolution_run_id=historical.name,
        allowed_repository_refs=set(reevaluation["allowed_repository_refs"]),
        framework_unchanged=True,
        schema_root=ROOT / "schemas",
        record_version="1.1.0",
    )

    assert evaluation["verdict"] == "accepted"
    assert {
        name: gate["passed"] for name, gate in evaluation["gates"].items()
    } == reevaluation["gates"]
    assert evaluation["approved_record_sha256"] == reevaluation[
        "approved_record_sha256"
    ]
    assert record == ExperienceLibrary(
        ROOT / "libraries/experience/v1/records.jsonl"
    ).list()[-1]


@pytest.mark.skipif(not REAL_SOURCE.is_dir(), reason="local real source run is absent")
def test_real_report_is_recompiled_in_memory_without_old_oversize_result() -> None:
    projection = build_evolution_projection(REAL_SOURCE)

    assert projection["source"]["report_bytes"] == 598_979
    assert projection["source"]["source_report_verified"] is True
    assert projection["collection"]["invalid_or_oversized"] == 0
    assert len(projection["compiler_candidates"]) == 5
    assert {
        item["causal_outcome"] for item in projection["compiler_candidates"]
    } == {"confirmed_failure"}


def test_projection_is_verified_and_contains_no_private_payloads(tmp_path: Path) -> None:
    demo, source = _demo_fixture(tmp_path)

    projection = build_global_evidence_projection(demo, source)

    assert projection["source"]["source_report_verified"] is True
    serialized = json.dumps(projection, sort_keys=True).lower()
    for marker in (
        "task_oracles.yaml",
        "heldout_tasks.jsonl",
        "observed_measurements",
        "target_measurements",
        "aws_model_api_key",
        "/users/",
    ):
        assert marker not in serialized


def test_agent_tools_are_read_only_and_private_paths_are_not_indexed(tmp_path: Path) -> None:
    demo, source = _demo_fixture(tmp_path)
    (demo / ".env").write_text("AWS_MODEL_API_KEY=rp_not_for_agent_123456789\n")
    projection = build_global_evidence_projection(demo, source)
    framework = _build_public_repository_index(demo)
    schema = load_structured(demo / "schemas/evolution_audit.schema.json")
    workspace = _AuditWorkspace(
        demo_root=demo,
        projection=projection,
        framework_inputs=framework,
        audit_schema=schema,
    )

    tools = _read_only_tools(workspace)

    assert set(tools) == {
        "read_evolution_evidence",
        "list_public_repository_files",
        "read_public_repository_file",
        "search_public_repository",
        "submit_evolution_audit",
    }
    assert not any(
        token in name for name in tools for token in ("write", "replace", "patch", "shell")
    )
    indexed = set(workspace.index)
    assert ".env" not in indexed
    assert not any("records.jsonl" in path or "oracle" in path for path in indexed)
    with pytest.raises(EvolutionRuntimeError, match="frozen public"):
        workspace.read_file({"path": ".env"})
    with pytest.raises(EvolutionRuntimeError, match="frozen public"):
        workspace.read_file({"path": "../outside"})


def test_evolution_rejects_source_and_public_file_symlinks(tmp_path: Path) -> None:
    demo, source = _demo_fixture(tmp_path)
    source_alias = demo / "runs/evolution-source-alias"
    source_alias.symlink_to(source, target_is_directory=True)

    with pytest.raises(EvolutionRuntimeError, match="non-symlink"):
        build_global_evidence_projection(demo, source_alias)

    projection = build_global_evidence_projection(demo, source)
    framework = _build_public_repository_index(demo)
    schema = load_structured(demo / "schemas/evolution_audit.schema.json")
    workspace = _AuditWorkspace(
        demo_root=demo,
        projection=projection,
        framework_inputs=framework,
        audit_schema=schema,
    )
    readme = demo / "README.md"
    moved_readme = demo / "README.snapshot.md"
    readme.rename(moved_readme)
    readme.symlink_to(moved_readme)

    with pytest.raises(EvolutionRuntimeError, match="unsafe"):
        workspace.read_file({"path": "README.md"})

    demo_with_linked_runs, linked_source = _demo_fixture(tmp_path / "linked")
    runs = demo_with_linked_runs / "runs"
    outside_runs = tmp_path / "outside-runs"
    runs.rename(outside_runs)
    runs.symlink_to(outside_runs, target_is_directory=True)
    with pytest.raises(EvolutionRuntimeError, match="demo/runs must not be a symlink"):
        build_global_evidence_projection(demo_with_linked_runs, linked_source)


def test_public_index_includes_redacted_core_source_and_source_binding(
    tmp_path: Path,
) -> None:
    demo, source = _demo_fixture(tmp_path)
    _write_json(
        source / "run_inputs.json",
        {
            "fixed_files": [
                {
                    "path": "README.md",
                    "sha256": sha256_file(demo / "README.md"),
                }
            ]
        },
    )

    framework = _build_public_repository_index(demo, source_run=source)
    by_path = {item["path"]: item for item in framework["files"]}

    for path in (
        "src/soarm_demo/evolution.py",
        "src/soarm_demo/evolution_runtime.py",
        "src/soarm_demo/pipeline.py",
    ):
        assert path in by_path
        assert by_path[path]["source_run_binding"] == "not_recorded_in_source_run"
    assert by_path["src/soarm_demo/evolution_runtime.py"]["redacted_line_count"] > 0
    assert by_path["src/soarm_demo/pipeline.py"]["redacted_line_count"] > 0
    assert by_path["README.md"]["source_run_binding"] == "matched_source_run"

    pipeline_text, _ = _sanitize_public_repository_text(
        (demo / "src/soarm_demo/pipeline.py").read_text(encoding="utf-8"),
        relative_path="src/soarm_demo/pipeline.py",
    )
    for private_marker in (
        "soarm101_p0_push_cylinder_lateral",
        "soarm101_p0_place_cube_in_bowl_new_region",
        "soarm101_p0_sort_two_cubes_matching_trays",
        "object_to_receptacle",
        "ordered = facts",
        "tool_name = \"pick_and_place\"",
    ):
        assert private_marker not in pipeline_text
    assert pipeline_text.count("[REDACTED_PRIVATE_REFERENCE]") >= 50


def test_first_valid_audit_submission_is_frozen(tmp_path: Path) -> None:
    demo, source = _demo_fixture(tmp_path)
    workspace = _AuditWorkspace(
        demo_root=demo,
        projection=build_global_evidence_projection(demo, source),
        framework_inputs=_build_public_repository_index(demo),
        audit_schema=load_structured(demo / "schemas/evolution_audit.schema.json"),
    )
    audit = _unresolved_audit("evolution-source-fixture")

    first = workspace.submit({"audit": audit})
    second = workspace.submit({"audit": audit})

    assert first["accepted"] is True
    assert second == {
        "accepted": False,
        "reason": "audit_already_submitted_and_frozen",
    }


def test_scripted_evolution_publishes_one_unresolved_lesson_not_a_patch(
    tmp_path: Path,
) -> None:
    demo, source = _demo_fixture(tmp_path)
    source_hash_before = sha256_file(source / "terminal_report.json")
    audit = _unresolved_audit("evolution-source-fixture")

    output = run_evolution(
        demo_root=demo,
        source_run=source,
        run_id="scripted-read-only-evolution",
        client=ScriptedModelClient(_scripted_responses(audit), model="scripted-evolution"),
        call_limit=5,
    )

    report = json.loads((output / "evolution_report.json").read_text(encoding="utf-8"))
    assert report["terminal_status"] == "EVOLUTION_ACCEPTED"
    assert report["evaluation"]["verdict"] == "accepted"
    assert report["evaluation"]["claims"] == {
        "evolution_process_completed": True,
        "experience_claim_accepted": True,
        "framework_change_applied": False,
        "capability_improvement_proven": False,
        "new_generation_validation_required": True,
    }
    assert report["read_only_guards"]["agent_write_tools"] == []
    assert not (output / "evolution/candidate_patch").exists()
    assert sha256_file(source / "terminal_report.json") == source_hash_before
    records = ExperienceLibrary(demo / "libraries/experience/v1/records.jsonl").list()
    assert len(records) == 1
    assert records[0]["status"] == "approved"
    assert records[0]["conclusion_kind"] == "unresolved"
    assert records[0]["outcome"] == {
        "repair_attempted": False,
        "repair_succeeded": None,
        "regressions": [],
        "reuse_risk": "medium",
    }
    assert records[0]["scientific_claims"]["capability_improvement_proven"] is False
    view = ExperienceLibrary(
        demo / "libraries/experience/v1/records.jsonl"
    ).generation_view()
    assert len(view) == 1
    serialized_view = json.dumps(view, sort_keys=True)
    assert "origin" not in serialized_view
    assert "artifact_refs" not in serialized_view
    assert "before_measurements" not in serialized_view
    assert "outcome" not in view[0]
    assert LibraryEntry.open(demo / "libraries/experience/v1").verify()["ok"] is True


def test_positive_claim_is_rejected_without_confirmed_success(tmp_path: Path) -> None:
    demo, source = _demo_fixture(tmp_path)
    projection = build_global_evidence_projection(demo, source)
    audit = _unresolved_audit("evolution-source-fixture")
    audit["experience_claim"]["conclusion_kind"] = "positive"

    evaluation, record = evaluate_evolution_audit(
        run_root=source,
        audit=audit,
        initial_projection_sha256=sha256_json(projection),
        evolution_run_id="rejected-positive",
        schema_root=demo / "schemas",
    )

    assert evaluation["verdict"] == "rejected"
    # The submission schema now catches this before the final claim-strength
    # gate, preserving evaluator strictness while giving the Agent repairable
    # feedback inside its synthesis window.
    assert "audit_schema_invalid" in evaluation["reason_codes"]
    assert record is None
    assert (demo / "libraries/experience/v1/records.jsonl").read_bytes() == b""


def test_unknown_evidence_ref_is_rejected(tmp_path: Path) -> None:
    demo, source = _demo_fixture(tmp_path)
    projection = build_global_evidence_projection(demo, source)
    audit = _unresolved_audit("evolution-source-fixture")
    audit["ranked_findings"][0]["evidence_refs"] = ["unknown:secret"]
    audit["experience_claim"]["evidence_refs"] = ["unknown:secret"]

    evaluation, record = evaluate_evolution_audit(
        run_root=source,
        audit=audit,
        initial_projection_sha256=sha256_json(projection),
        evolution_run_id="unknown-ref",
        schema_root=demo / "schemas",
    )

    assert evaluation["verdict"] == "rejected"
    assert "unknown_or_unselected_evidence_ref" in evaluation["reason_codes"]
    assert record is None


def test_selected_finding_missing_is_rejected_without_evaluator_crash(
    tmp_path: Path,
) -> None:
    demo, source = _demo_fixture(tmp_path)
    projection = build_global_evidence_projection(demo, source)
    audit = _unresolved_audit("evolution-source-fixture")
    audit["selected_finding_id"] = "missing_finding"

    evaluation, record = evaluate_evolution_audit(
        run_root=source,
        audit=audit,
        initial_projection_sha256=sha256_json(projection),
        evolution_run_id="missing-selected-finding",
        schema_root=demo / "schemas",
    )

    assert evaluation["verdict"] == "rejected"
    assert "audit_semantics_invalid" in evaluation["reason_codes"]
    assert record is None


def test_audit_byte_limit_is_a_rejecting_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    demo, source = _demo_fixture(tmp_path)
    projection = build_global_evidence_projection(demo, source)
    audit = _unresolved_audit("evolution-source-fixture")
    monkeypatch.setattr(evolution_module, "_MAX_AUDIT_BYTES", 128)

    evaluation, record = evaluate_evolution_audit(
        run_root=source,
        audit=audit,
        initial_projection_sha256=sha256_json(projection),
        evolution_run_id="oversized-audit",
        schema_root=demo / "schemas",
    )

    assert evaluation["verdict"] == "rejected"
    assert evaluation["gates"]["audit_schema"]["passed"] is False
    assert "audit_too_large" in evaluation["reason_codes"]
    assert record is None


def test_private_marker_in_audit_is_rejected_without_evaluator_crash(
    tmp_path: Path,
) -> None:
    demo, source = _demo_fixture(tmp_path)
    projection = build_global_evidence_projection(demo, source)
    audit = _unresolved_audit("evolution-source-fixture")
    audit["experience_claim"]["generation_summary"]["lesson"] = (
        "Read heldout_tasks.jsonl before implementing the capability."
    )

    evaluation, record = evaluate_evolution_audit(
        run_root=source,
        audit=audit,
        initial_projection_sha256=sha256_json(projection),
        evolution_run_id="privacy-rejection",
        schema_root=demo / "schemas",
    )

    assert evaluation["verdict"] == "rejected"
    assert evaluation["gates"]["privacy"]["passed"] is False
    assert "privacy_gate_failed" in evaluation["reason_codes"]
    assert record is None


def test_terminal_semantic_mismatch_blocks_projection(tmp_path: Path) -> None:
    demo, source = _demo_fixture(tmp_path)
    _write_json(
        source / "run_manifest_terminal_snapshot.json",
        {"run_id": "wrong-run", "state": "GENERATION_FAILED", "events": []},
    )

    with pytest.raises(EvolutionError, match="semantic audit"):
        build_global_evidence_projection(demo, source)


def test_publication_symlink_failure_preserves_accepted_claim(
    tmp_path: Path,
) -> None:
    demo, source = _demo_fixture(tmp_path)
    projection = build_global_evidence_projection(demo, source)
    audit = _unresolved_audit("evolution-source-fixture")
    library_root = demo / "libraries/experience/v1"
    real_library = demo / "libraries/experience/v1-real"
    library_root.rename(real_library)
    library_root.symlink_to(real_library, target_is_directory=True)

    evaluation, record, publication = evaluate_and_publish_evolution_audit(
        demo_root=demo,
        run_root=source,
        audit=audit,
        initial_projection_sha256=sha256_json(projection),
        evolution_run_id="publication-symlink",
        schema_root=demo / "schemas",
    )

    assert evaluation["verdict"] == "accepted"
    assert record is not None
    assert publication == {
        "published": False,
        "reason": "publication_gate_or_storage_failed",
    }
    assert (real_library / "records.jsonl").read_bytes() == b""
    assert "publish_experience_record" not in evolution_module.__all__
    assert not hasattr(evolution_module, "publish_experience_record")


def test_runtime_reports_publication_failure_as_process_inconclusive(
    tmp_path: Path,
) -> None:
    demo, source = _demo_fixture(tmp_path)
    library_root = demo / "libraries/experience/v1"
    real_library = demo / "libraries/experience/v1-real"
    library_root.rename(real_library)
    library_root.symlink_to(real_library, target_is_directory=True)

    output = run_evolution(
        demo_root=demo,
        source_run=source,
        run_id="scripted-publication-failure",
        client=ScriptedModelClient(
            _scripted_responses(_unresolved_audit("evolution-source-fixture")),
            model="scripted-evolution",
        ),
        call_limit=5,
    )
    report = json.loads((output / "evolution_report.json").read_text(encoding="utf-8"))

    assert report["terminal_status"] == "EVOLUTION_INCONCLUSIVE"
    assert report["evaluation"]["verdict"] == "accepted"
    assert report["evaluation"]["claims"]["experience_claim_accepted"] is True
    assert report["experience"] == {
        "published": False,
        "reason": "publication_gate_or_storage_failed",
    }


def test_runtime_without_submitted_audit_is_schema_valid_inconclusive(
    tmp_path: Path,
) -> None:
    demo, source = _demo_fixture(tmp_path)
    responses = [
        json.dumps({"type": "final", "content": "No audit submitted."}),
        json.dumps({"type": "final", "content": "Still no audit."}),
    ]

    output = run_evolution(
        demo_root=demo,
        source_run=source,
        run_id="scripted-missing-audit",
        client=ScriptedModelClient(responses, model="scripted-evolution"),
        call_limit=2,
    )
    report = json.loads((output / "evolution_report.json").read_text(encoding="utf-8"))

    assert report["terminal_status"] == "EVOLUTION_INCONCLUSIVE"
    assert report["evaluation"]["verdict"] == "inconclusive"
    assert report["evaluation"]["approved_record_sha256"] is None
    assert set(report["evaluation"]["gates"]) == {
        "source_integrity",
        "framework_snapshot",
        "audit_schema",
        "audit_semantics",
        "evidence_binding",
        "claim_strength",
        "privacy",
        "experience_schema",
    }


def test_accepted_report_contract_is_validated_before_experience_publication(
    tmp_path: Path,
) -> None:
    demo, source = _demo_fixture(tmp_path)
    records_path = demo / "libraries/experience/v1/records.jsonl"
    manifest_path = demo / "libraries/experience/v1/manifest.yaml"
    records_before = records_path.read_bytes()
    manifest_before = manifest_path.read_bytes()

    with pytest.raises(EvolutionRuntimeError, match="schema/semantic validation"):
        run_evolution(
            demo_root=demo,
            source_run=source,
            run_id="invalid-report-before-publication",
            client=ScriptedModelClient(
                _scripted_responses(_unresolved_audit("evolution-source-fixture")),
                model="",
            ),
            call_limit=5,
        )

    assert records_path.read_bytes() == records_before
    assert manifest_path.read_bytes() == manifest_before
    assert not (
        demo / "runs/invalid-report-before-publication/evolution_report.json"
    ).exists()


def test_report_semantics_bind_accepted_gates_and_published_record_hash(
    tmp_path: Path,
) -> None:
    demo, source = _demo_fixture(tmp_path)
    output = run_evolution(
        demo_root=demo,
        source_run=source,
        run_id="semantic-report-binding",
        client=ScriptedModelClient(
            _scripted_responses(_unresolved_audit("evolution-source-fixture")),
            model="scripted-evolution",
        ),
        call_limit=5,
    )
    report = json.loads((output / "evolution_report.json").read_text(encoding="utf-8"))

    report["evaluation"]["gates"]["privacy"]["passed"] = False
    assert any(
        "every trusted gate" in issue
        for issue in _evolution_report_semantic_errors(report)
    )
    report["evaluation"]["gates"]["privacy"]["passed"] = True
    report["experience"]["record_sha256"] = "1" * 64
    assert any(
        "published record hash" in issue
        for issue in _evolution_report_semantic_errors(report)
    )


def test_publisher_is_idempotent_and_updates_manifest_hash_count(tmp_path: Path) -> None:
    demo, source = _demo_fixture(tmp_path)
    projection = build_global_evidence_projection(demo, source)
    audit = _unresolved_audit("evolution-source-fixture")
    evaluation, record = evaluate_evolution_audit(
        run_root=source,
        audit=audit,
        initial_projection_sha256=sha256_json(projection),
        evolution_run_id="idempotent-publication",
        schema_root=demo / "schemas",
    )
    assert evaluation["verdict"] == "accepted" and record is not None
    assert evaluation["approved_record_sha256"] == sha256_json(record)
    forged_evaluation = dict(evaluation)
    forged_evaluation["approved_record_sha256"] = "0" * 64
    with pytest.raises(EvolutionError, match="not bound"):
        evolution_module._publish_evaluated_experience_record(
            demo_root=demo,
            record=record,
            evaluation=forged_evaluation,
        )
    assert (demo / "libraries/experience/v1/records.jsonl").read_bytes() == b""

    _, first_record, first = evaluate_and_publish_evolution_audit(
        demo_root=demo,
        run_root=source,
        audit=audit,
        initial_projection_sha256=sha256_json(projection),
        evolution_run_id="idempotent-publication",
        schema_root=demo / "schemas",
    )
    assert first_record == record
    bytes_after_first = (demo / "libraries/experience/v1/records.jsonl").read_bytes()
    _, second_record, second = evaluate_and_publish_evolution_audit(
        demo_root=demo,
        run_root=source,
        audit=audit,
        initial_projection_sha256=sha256_json(projection),
        evolution_run_id="idempotent-publication",
        schema_root=demo / "schemas",
    )
    assert second_record == record
    manifest = load_structured(demo / "libraries/experience/v1/manifest.yaml")

    assert first["idempotent"] is False
    assert second["idempotent"] is True
    for receipt in (first, second):
        assert receipt["record_count"] == 1
        assert receipt["generation_view_sha256"] == sha256_json(
            ExperienceLibrary(
                demo / "libraries/experience/v1/records.jsonl"
            ).generation_view([record["experience_id"]])[0]
        )
        assert receipt["manifest_sha256"] == sha256_file(
            demo / "libraries/experience/v1/manifest.yaml"
        )
    assert (demo / "libraries/experience/v1/records.jsonl").read_bytes() == bytes_after_first
    assert manifest["compatibility"]["record_count"] == 1
    assert manifest["content_hashes"]["records.jsonl"] == sha256_file(
        demo / "libraries/experience/v1/records.jsonl"
    )
    assert LibraryEntry.open(demo / "libraries/experience/v1").verify()["ok"] is True


def test_publisher_refuses_missing_hash_and_stale_ledger_without_rebinding(
    tmp_path: Path,
) -> None:
    demo, source = _demo_fixture(tmp_path)
    projection = build_global_evidence_projection(demo, source)
    audit = _unresolved_audit("evolution-source-fixture")
    manifest_path = demo / "libraries/experience/v1/manifest.yaml"
    records_path = demo / "libraries/experience/v1/records.jsonl"

    manifest = load_structured(manifest_path)
    del manifest["content_hashes"]["records.jsonl"]
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    missing_hash_manifest = manifest_path.read_bytes()
    _, _, publication = evaluate_and_publish_evolution_audit(
        demo_root=demo,
        run_root=source,
        audit=audit,
        initial_projection_sha256=sha256_json(projection),
        evolution_run_id="publisher-preflight",
        schema_root=demo / "schemas",
    )
    assert publication["published"] is False
    assert records_path.read_bytes() == b""
    assert manifest_path.read_bytes() == missing_hash_manifest

    _empty_experience_library(demo)
    _, record, first = evaluate_and_publish_evolution_audit(
        demo_root=demo,
        run_root=source,
        audit=audit,
        initial_projection_sha256=sha256_json(projection),
        evolution_run_id="publisher-preflight",
        schema_root=demo / "schemas",
    )
    assert first["published"] is True and record is not None
    ledger = ExperienceLibrary(records_path).list()
    ledger[0]["diagnosis"]["confidence"] = 0.39
    records_path.write_text(
        "".join(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n" for item in ledger),
        encoding="utf-8",
    )
    stale_records = records_path.read_bytes()
    stale_manifest = manifest_path.read_bytes()
    _, _, second = evaluate_and_publish_evolution_audit(
        demo_root=demo,
        run_root=source,
        audit=audit,
        initial_projection_sha256=sha256_json(projection),
        evolution_run_id="publisher-preflight",
        schema_root=demo / "schemas",
    )
    assert second["published"] is False
    assert records_path.read_bytes() == stale_records
    assert manifest_path.read_bytes() == stale_manifest
