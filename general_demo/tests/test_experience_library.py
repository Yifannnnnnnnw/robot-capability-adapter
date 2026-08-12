from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from autoadapter2.foundation.canonical import canonical_bytes
from autoadapter2.foundation.errors import ContractError, ImmutableError
from autoadapter2.foundation.hashing import content_hash
from autoadapter2.libraries.experience import (
    build_experience_snapshot,
    review_and_include_candidate,
    verify_experience_snapshot,
)


def _write_json(root: Path, relative: str, value: dict) -> tuple[Path, dict[str, str]]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(value)
    path.write_bytes(payload)
    return path, {
        "path": relative,
        "content_hash": content_hash(payload),
    }


def _candidate_fixture(
    root: Path,
    *,
    candidate_id: str = "candidate-demo-1",
    recipient_class: str = "design",
    applicability: dict | None = None,
) -> tuple[dict[str, str], dict[str, str], dict, dict]:
    if applicability is None:
        applicability = {
            "robot_model_id": "unitree-go2",
            "robot_configuration_id": "unitree-go2-stock-12dof",
            "sdk_entry_id": None if recipient_class == "design" else "unitree-sdk2-go2-lowlevel@1.0.0",
            "granularity_condition": "bounded reusable effect",
            "capability_effect_scope": ["forward locomotion"],
            "observation_condition": "after each bounded command observation",
        }
    evidence_digest_hash = content_hash(b"evidence-digest")
    report = {
        "artifact_type": "experience_declassification_report",
        "format_version": "experimental-1",
        "candidate_id": candidate_id,
        "status": "PASS",
        "evidence_digest_hash": evidence_digest_hash,
        "excluded_categories": [
            "private_validation_criteria",
            "private_cases",
            "thresholds",
            "seeds",
            "mujoco_truth",
            "translation_details",
            "candidate_source",
            "consumer_trace",
            "raw_video",
            "video_frames",
            "video_manifests",
            "model_prompts",
            "credentials",
        ],
        "checks": {"private_material_removed": True, "reconstruction_check": True},
    }
    _, declassification_ref = _write_json(root, f"inputs/{candidate_id}-declassification.json", report)
    candidate = {
        "artifact_type": "experience_candidate",
        "format_version": "experimental-1",
        "status": "PROPOSED_REVIEW_REQUIRED",
        "candidate_id": candidate_id,
        "recipient_class": recipient_class,
        "lesson": "Use a bounded command and observe the resulting state before continuing.",
        "applicability": applicability,
        "provenance": {
            "closure_hash": content_hash(b"closed-run"),
            "summary_ref": "summary-ref-1",
            "stage_artifacts_ref": "stage-artifacts-ref-1",
            "evidence_digest_hash": evidence_digest_hash,
        },
        "limitations": ["Does not define a task solution."],
        "invalidation_conditions": ["SDK or robot configuration changes."],
        "declassification_report_hash": declassification_ref["content_hash"],
    }
    _, candidate_ref = _write_json(root, f"inputs/{candidate_id}.json", candidate)
    return candidate_ref, declassification_ref, candidate, report


def _human_review() -> dict[str, str]:
    return {
        "decision": "HUMAN_APPROVED",
        "reviewer_kind": "HUMAN",
        "reviewer_id": "reviewer-1",
        "review_note": "Bounded lesson is supported and declassified for the declared recipient.",
        "reviewed_at": "2026-08-12T12:00:00Z",
    }


def _include(
    root: Path,
    *,
    candidate_id: str = "candidate-demo-1",
    recipient_class: str = "design",
    record_id: str = "experience-demo-1",
    version: str = "1.0.0",
    applicability: dict | None = None,
) -> tuple[dict, dict, dict]:
    candidate_ref, declassification_ref, candidate, _ = _candidate_fixture(
        root,
        candidate_id=candidate_id,
        recipient_class=recipient_class,
        applicability=applicability,
    )
    result = review_and_include_candidate(
        root,
        candidate_ref,
        declassification_ref,
        _human_review(),
        record_id=record_id,
        version=version,
    )
    return result, candidate, declassification_ref


def test_human_approval_includes_bounded_immutable_record(tmp_path: Path) -> None:
    result, candidate, declassification_ref = _include(tmp_path)

    assert result["status"] == "HUMAN_APPROVED"
    record_path = tmp_path / result["record_ref"]["path"]
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["status"] == "HUMAN_APPROVED"
    assert record["lesson"] == candidate["lesson"]
    assert record["applicability"] == candidate["applicability"]
    assert record["declassification_report_content_hash"] == declassification_ref["content_hash"]
    assert (record_path.parent / "review.json").is_file()
    assert (record_path.parent / "provenance.json").is_file()
    assert not (tmp_path / "general_demo/libraries/experience/records/consumer").exists()


def test_rejected_or_ai_review_writes_only_external_review_artifact(tmp_path: Path) -> None:
    candidate_ref, declassification_ref, _, _ = _candidate_fixture(tmp_path)
    rejected = review_and_include_candidate(
        tmp_path,
        candidate_ref,
        declassification_ref,
        {
            "decision": "REJECTED",
            "reviewer_kind": "HUMAN",
            "reviewer_id": "reviewer-1",
            "review_note": "Scope is not sufficiently supported.",
            "reviewed_at": "2026-08-12T12:00:00Z",
        },
    )
    assert rejected["status"] == "REJECTED"
    assert (tmp_path / rejected["review_ref"]["path"]).is_file()
    assert "libraries/experience" not in rejected["review_ref"]["path"]
    assert not (tmp_path / "general_demo/libraries/experience/records").exists()

    with pytest.raises(ContractError):
        review_and_include_candidate(
            tmp_path,
            candidate_ref,
            declassification_ref,
            {
                **_human_review(),
                "reviewer_kind": "AI",
            },
            record_id="must-not-be-created",
            version="1.0.0",
        )
    assert not (tmp_path / "general_demo/libraries/experience/records").exists()

    ai_rejected = review_and_include_candidate(
        tmp_path,
        candidate_ref,
        declassification_ref,
        {
            "decision": "REJECTED",
            "reviewer_kind": "AI",
            "reviewer_id": "model-1",
            "review_note": "Automated review cannot admit Experience.",
            "reviewed_at": "2026-08-12T12:01:00Z",
        },
    )
    assert ai_rejected["status"] == "REJECTED"
    assert not (tmp_path / "general_demo/libraries/experience/records").exists()


def test_tampered_or_wrong_declassification_file_is_rejected(tmp_path: Path) -> None:
    candidate_ref, declassification_ref, candidate, report = _candidate_fixture(tmp_path)
    candidate["lesson"] = "Tampered after the external reference was created."
    (tmp_path / candidate_ref["path"]).write_bytes(canonical_bytes(candidate))
    with pytest.raises(ContractError):
        review_and_include_candidate(
            tmp_path,
            candidate_ref,
            declassification_ref,
            _human_review(),
            record_id="experience-demo-1",
            version="1.0.0",
        )

    candidate_ref, declassification_ref, _, report = _candidate_fixture(
        tmp_path, candidate_id="candidate-demo-2"
    )
    report["candidate_id"] = "different-candidate"
    _, wrong_declassification_ref = _write_json(
        tmp_path, "inputs/wrong-declassification.json", report
    )
    with pytest.raises(ContractError):
        review_and_include_candidate(
            tmp_path,
            candidate_ref,
            wrong_declassification_ref,
            _human_review(),
            record_id="experience-demo-2",
            version="1.0.0",
        )


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        ("false-check", lambda report: report["checks"].__setitem__("private_material_removed", False)),
        ("non-bool-check", lambda report: report["checks"].__setitem__("private_material_removed", "PASS")),
        ("list-checks", lambda report: report.__setitem__("checks", [True, True])),
        ("missing-category", lambda report: report["excluded_categories"].pop()),
        ("reordered-category", lambda report: report["excluded_categories"].reverse()),
        ("extra-category", lambda report: report["excluded_categories"].append("extra")),
    ],
)
def test_declassification_report_requires_closed_checks_and_fixed_categories(
    tmp_path: Path,
    case: str,
    mutate,
) -> None:
    root = tmp_path / case
    candidate_ref, _, candidate, report = _candidate_fixture(root, candidate_id=f"candidate-{case}")
    mutate(report)
    _, invalid_declassification_ref = _write_json(
        root,
        "inputs/invalid-declassification.json",
        report,
    )
    candidate["declassification_report_hash"] = invalid_declassification_ref["content_hash"]
    _, invalid_candidate_ref = _write_json(root, "inputs/invalid-candidate.json", candidate)

    with pytest.raises(ContractError):
        review_and_include_candidate(
            root,
            invalid_candidate_ref,
            invalid_declassification_ref,
            _human_review(),
            record_id="experience-invalid-declassification",
            version="1.0.0",
        )


def test_record_version_cannot_be_overwritten(tmp_path: Path) -> None:
    result, _, _ = _include(tmp_path)
    record_path = tmp_path / result["record_ref"]["path"]
    original = record_path.read_bytes()

    candidate_ref, declassification_ref, candidate, _ = _candidate_fixture(tmp_path)
    candidate["lesson"] = "A materially different lesson for the same immutable version."
    (tmp_path / candidate_ref["path"]).write_bytes(canonical_bytes(candidate))
    updated_ref = {
        **candidate_ref,
        "content_hash": content_hash((tmp_path / candidate_ref["path"]).read_bytes()),
    }
    with pytest.raises(ImmutableError):
        review_and_include_candidate(
            tmp_path,
            updated_ref,
            declassification_ref,
            _human_review(),
            record_id="experience-demo-1",
            version="1.0.0",
        )
    assert record_path.read_bytes() == original


def test_snapshot_requires_exact_recipient_and_applicability(tmp_path: Path) -> None:
    result, candidate, _ = _include(tmp_path)
    output = tmp_path / "snapshots/design.json"
    snapshot = build_experience_snapshot(
        tmp_path,
        output,
        "design-snapshot-1",
        "design",
        candidate["applicability"],
        [result["record_ref"]],
    )
    assert snapshot["records"][0]["record_ref"] == result["record_ref"]
    assert set(snapshot["records"][0]["projection"]) == {
        "experience_id",
        "guidance",
        "applicability",
        "provenance",
    }
    assert "review" not in json.dumps(snapshot["records"][0]["projection"]).lower()
    assert "private" not in json.dumps(snapshot["records"][0]["projection"]).lower()
    assert "raw" not in json.dumps(snapshot["records"][0]["projection"]).lower()

    wrong_applicability = copy.deepcopy(candidate["applicability"])
    wrong_applicability["granularity_condition"] = "different granularity"
    with pytest.raises(ContractError):
        build_experience_snapshot(
            tmp_path,
            tmp_path / "snapshots/wrong.json",
            "wrong-snapshot",
            "design",
            wrong_applicability,
            [result["record_ref"]],
        )
    with pytest.raises(ContractError):
        build_experience_snapshot(
            tmp_path,
            tmp_path / "snapshots/wrong-recipient.json",
            "wrong-recipient",
            "implementation",
            {
                **candidate["applicability"],
                "sdk_entry_id": "sdk-entry-1",
            },
            [result["record_ref"]],
        )


def test_empty_and_nonempty_snapshots_are_deterministic_and_future_only(tmp_path: Path) -> None:
    current_run = tmp_path / "runs/current-run.json"
    current_run.parent.mkdir(parents=True)
    current_run.write_bytes(b"current-run-must-not-change")
    design_applicability = _candidate_fixture(tmp_path, candidate_id="candidate-a")[2]["applicability"]
    empty = build_experience_snapshot(
        tmp_path,
        tmp_path / "snapshots/empty.json",
        "empty-snapshot",
        "design",
        design_applicability,
        [],
    )
    assert empty["records"] == []
    assert verify_experience_snapshot(tmp_path, tmp_path / "snapshots/empty.json") == empty

    result_b, candidate_b, _ = _include(
        tmp_path,
        candidate_id="candidate-b",
        record_id="experience-b",
    )
    result_a, candidate_a, _ = _include(
        tmp_path,
        candidate_id="candidate-a",
        record_id="experience-a",
    )
    assert candidate_a["applicability"] == candidate_b["applicability"] == design_applicability
    first = build_experience_snapshot(
        tmp_path,
        tmp_path / "snapshots/nonempty-1.json",
        "nonempty-snapshot",
        "design",
        design_applicability,
        [result_b["record_ref"], result_a["record_ref"]],
    )
    second = build_experience_snapshot(
        tmp_path,
        tmp_path / "snapshots/nonempty-2.json",
        "nonempty-snapshot",
        "design",
        design_applicability,
        [result_a["record_ref"], result_b["record_ref"]],
    )
    assert first == second
    assert [item["record_id"] for item in first["records"]] == ["experience-a", "experience-b"]
    assert current_run.read_bytes() == b"current-run-must-not-change"


def test_design_and_implementation_snapshots_are_separate(tmp_path: Path) -> None:
    design_result, design_candidate, _ = _include(
        tmp_path,
        candidate_id="candidate-design",
        record_id="experience-design",
        recipient_class="design",
    )
    implementation_result, implementation_candidate, _ = _include(
        tmp_path,
        candidate_id="candidate-implementation",
        record_id="experience-implementation",
        recipient_class="implementation",
    )

    design_snapshot = build_experience_snapshot(
        tmp_path,
        tmp_path / "snapshots/design-only.json",
        "design-only",
        "design",
        design_candidate["applicability"],
        [design_result["record_ref"]],
    )
    design_projection = design_snapshot["records"][0]["projection"]
    assert "sdk_entry_id" not in design_projection
    assert "sdk_entry_id" in design_projection["applicability"]

    implementation_snapshot = build_experience_snapshot(
        tmp_path,
        tmp_path / "snapshots/implementation-only.json",
        "implementation-only",
        "implementation",
        implementation_candidate["applicability"],
        [implementation_result["record_ref"]],
    )
    implementation_projection = implementation_snapshot["records"][0]["projection"]
    assert "sdk_entry_id" not in implementation_projection
    assert implementation_projection["applicability"]["sdk_entry_id"] == (
        "unitree-sdk2-go2-lowlevel@1.0.0"
    )
    with pytest.raises(ContractError):
        build_experience_snapshot(
            tmp_path,
            tmp_path / "snapshots/mixed.json",
            "mixed",
            "design",
            design_candidate["applicability"],
            [implementation_result["record_ref"]],
        )


def test_verify_returns_deep_copy_and_rejects_tampered_snapshot(tmp_path: Path) -> None:
    result, candidate, _ = _include(tmp_path)
    output = tmp_path / "snapshots/verified.json"
    built = build_experience_snapshot(
        tmp_path,
        output,
        "verified",
        "design",
        candidate["applicability"],
        [result["record_ref"]],
    )
    verified = verify_experience_snapshot(
        tmp_path,
        output,
        recipient_class="design",
        applicability=candidate["applicability"],
    )
    verified["records"].clear()
    assert verify_experience_snapshot(tmp_path, output) == built

    tampered = json.loads(output.read_text(encoding="utf-8"))
    tampered["records"][0]["projection"]["guidance"] = "private raw evidence"
    output.write_bytes(canonical_bytes(tampered))
    with pytest.raises(ContractError):
        verify_experience_snapshot(tmp_path, output)
