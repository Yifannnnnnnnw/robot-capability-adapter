from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from autoadapter2.evolution import (
    CANDIDATE_FORMAT_VERSION,
    EXCLUDED_EVIDENCE_CATEGORIES,
    propose_experience_candidate,
    validate_declassification_report,
)
from autoadapter2.foundation.canonical import canonical_bytes
from autoadapter2.foundation.errors import ContractError
from autoadapter2.foundation.hashing import content_hash
from autoadapter2.foundation.seals import create_seal, verify_seal
from autoadapter2.integration import write_stable_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _semantic_fields(*, recipient_class: str = "design") -> dict[str, object]:
    return {
        "recipient_class": recipient_class,
        "lesson": "Prefer a bounded reusable effect interface for this fixed configuration.",
        "applicability": {
            "robot_model_id": "unitree-go2",
            "robot_configuration_id": "unitree-go2-stock-12dof",
            "sdk_entry_id": (
                None
                if recipient_class == "design"
                else "unitree-sdk2-go2-lowlevel@1.0.0"
            ),
            "granularity_condition": "G2",
            "capability_effect_scope": ["bounded locomotion effect"],
            "observation_condition": "State-provided public robot observation is available.",
        },
        "limitations": ["Applies only to the fixed robot configuration."],
        "invalidation_conditions": ["Robot configuration or observation regime changes."],
    }


def _make_closed_run(
    tmp_path: Path,
    *,
    status: str = "COMPLETE",
    include_sdk_projection: bool = True,
) -> tuple[Path, Path, Path]:
    root = tmp_path
    run = root / "general_demo" / "runs" / "closed-go2"
    run.mkdir(parents=True)
    run_id = "closed-go2"
    summary = {
        "artifact_type": "general_demo_run_summary",
        "format_version": CANDIDATE_FORMAT_VERSION,
        "run_id": run_id,
        "status": status,
        "robot": {
            "robot_model_id": "unitree-go2",
            "robot_configuration_id": "unitree-go2-stock-12dof",
        },
        "granularity_profile": {
            "profile_id": "g2-reusable-effect",
            "version": "1.0.0",
            "granularity": "G2",
        },
        "stages": [
            {"stage": "integration_gate", "status": "READY"},
            {"stage": "stage1", "status": "SEALED", "llm_calls": 1},
            {"stage": "stage2", "status": status, "candidate_error": "public candidate issue"},
        ],
        "validation_video_handles": [],
        "demo_trials": [],
        "promoted_capability_ids": [],
    }
    stage1_artifacts: dict[str, object] = {
        "status": "SEALED",
        "diagnostics": [{"code": "STAGE1_PUBLIC_CODE", "message": "ok"}],
        "capability_source": "def private_winning_source(): return qpos",
        "consumer_trace": {"raw_truth": "secret"},
        "threshold": 0.1,
        "candidate_error": "public stage candidate issue",
    }
    if include_sdk_projection:
        stage1_artifacts["capability_design"] = {
            "robot_public_projection": {
                "sdk_facts": {
                    "entry_id": "unitree-sdk2-go2-lowlevel",
                    "entry_version": "1.0.0",
                    "private_detail": "must not be copied",
                },
                "private_projection": "must not be copied",
            },
        }
    stage_artifacts = {
        "artifact_type": "first_g2_stage_artifacts",
        "schema_version": "1.0.0",
        "run_id": run_id,
        "robot": "unitree-go2",
        "stages": {
            "integration_gate": {"status": "READY", "diagnostics": []},
            "stage1": stage1_artifacts,
            "stage2": {
                "status": status,
                "diagnostics": [{"code": "STAGE2_PUBLIC_CODE", "message": "ok"}],
                "infrastructure_error": "public infrastructure issue",
                "seed": 7,
                "video_manifest": {"camera": "private"},
                "credentials": "do-not-copy",
            },
            "validation_and_repair": {
                "status": "FAIL",
                "repair": {
                    "initial_validation_b": {
                        "executions": [
                            {
                                "candidate_error": (
                                    "TypeError: CRC.Crc() missing 1 required positional argument: 'msg'"
                                ),
                                "diagnostics": [{"code": "CRC_CRC_TYPE_ERROR"}],
                            },
                            {
                                "candidate_error": (
                                    "TypeError: CRC.Crc() missing 1 required positional argument: 'msg'"
                                ),
                            },
                        ],
                    },
                    "final_validation_b": {
                        "executions": [
                            {
                                "candidate_error": (
                                    "TypeError: CRC.Crc() missing 1 required positional argument: 'msg'"
                                ),
                            },
                        ],
                    },
                    "repair_log": [
                        {
                            "candidate_error": (
                                "TypeError: CRC.Crc() missing 1 required positional argument: 'msg'"
                            ),
                            "diagnostics": [{"code": "CRC_CRC_TYPE_ERROR"}],
                        },
                    ],
                    "candidate_source": "def private_winning_source(): return qpos",
                    "private_threshold": 0.1,
                    "raw_trace": {"qpos": [0.0], "credentials": "private"},
                },
            },
        },
    }
    values = {
        "run_snapshot": {"artifact_type": "run_snapshot", "run_id": run_id},
        "summary": summary,
        "stage_artifacts": stage_artifacts,
        "model_call_log": {"artifact_type": "model_call_log", "prompt": "private prompt"},
        "validation_video_references": {
            "artifact_type": "general_demo_video_references",
            "schema_version": "1.0.0",
            "run_id": run_id,
            "robot": "unitree-go2",
            "videos": [],
        },
        "demo_video_references": {
            "artifact_type": "general_demo_video_references",
            "schema_version": "1.0.0",
            "run_id": run_id,
            "robot": "unitree-go2",
            "videos": [],
        },
    }
    paths = {name: run / f"{name}.json" for name in values}
    refs = {
        name: {
            "path": path.relative_to(root).as_posix(),
            "sha256": write_stable_json(path, values[name]),
        }
        for name, path in paths.items()
    }
    summary_hash = content_hash(canonical_bytes(summary))
    summary_seal = create_seal("general_demo_run_summary", summary_hash)
    write_stable_json(run / "summary.seal.json", summary_seal)
    closure = {
        "artifact_type": "first_g2_run_closure",
        "schema_version": "1.0.0",
        "run_id": run_id,
        "robot": "unitree-go2",
        "status": status,
        "summary_hash": summary_hash,
        "summary_seal": summary_seal,
        "files": refs,
    }
    closure_hash = content_hash(canonical_bytes(closure))
    closure_seal = create_seal(
        "first_g2_run_closure",
        closure_hash,
        [
            summary_hash,
            *[f"sha256:{reference['sha256']}" for reference in refs.values()],
        ],
    )
    closure_path = run / "run_closure.json"
    closure_seal_path = run / "run_closure.seal.json"
    write_stable_json(closure_path, closure)
    write_stable_json(closure_seal_path, closure_seal)
    return root, closure_path, closure_seal_path


def _run_bytes(root: Path) -> dict[str, bytes]:
    run = root / "general_demo" / "runs" / "closed-go2"
    return {
        path.relative_to(run).as_posix(): path.read_bytes()
        for path in run.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("status", ["COMPLETE", "DEMO_FAILED"])
def test_complete_and_terminal_failed_runs_propose_outside_run_without_mutation(
    tmp_path: Path, status: str
) -> None:
    root, closure_path, closure_seal_path = _make_closed_run(tmp_path, status=status)
    before = _run_bytes(root)
    seen: list[dict[str, object]] = []

    def agent(evidence_digest: dict[str, object]) -> dict[str, object]:
        seen.append(copy.deepcopy(evidence_digest))
        return _semantic_fields()

    result = propose_experience_candidate(
        root,
        closure_path,
        closure_seal_path,
        f"case-{status.lower()}",
        evolution_cases_root=root / "evolution_cases",
        evolution_agent=agent,
    )

    assert result.candidate_path.parent == root / "evolution_cases" / f"case-{status.lower()}"
    assert not result.candidate_path.is_relative_to(closure_path.parent)
    assert _run_bytes(root) == before
    assert len(seen) == 1
    assert seen[0]["robot"]["sdk_entry_id"] == "unitree-sdk2-go2-lowlevel@1.0.0"
    assert seen[0]["robot"]["granularity_condition"] == "G2"
    callback_text = canonical_bytes(seen[0]).decode("utf-8").casefold()
    for forbidden in ("threshold", "qpos", "winning", "trace", "video", "prompt", "credential"):
        assert forbidden not in callback_text
    crc_error = "TypeError: CRC.Crc() missing 1 required positional argument: 'msg'"
    assert crc_error.casefold() in callback_text
    assert callback_text.count(crc_error.casefold()) == 1
    assert "crc_crc_type_error" in callback_text
    assert "executions" not in callback_text
    assert "candidate_source" not in callback_text
    assert not (root / "general_demo" / "libraries" / "experience").exists()


def test_candidate_and_report_have_exact_shapes_canonical_hashes_and_immutable_seals(
    tmp_path: Path,
) -> None:
    root, closure_path, closure_seal_path = _make_closed_run(tmp_path)
    result = propose_experience_candidate(
        root,
        closure_path,
        closure_seal_path,
        "exact-shape",
        evolution_cases_root=root / "evolution_cases",
        candidate_fields=_semantic_fields(recipient_class="implementation"),
    )
    candidate = json.loads(result.candidate_path.read_text(encoding="utf-8"))
    report = json.loads(result.declassification_report_path.read_text(encoding="utf-8"))
    assert set(candidate) == {
        "artifact_type", "format_version", "status", "candidate_id", "recipient_class",
        "lesson", "applicability", "provenance", "limitations", "invalidation_conditions",
        "declassification_report_hash",
    }
    assert candidate["artifact_type"] == "experience_candidate"
    assert candidate["status"] == "PROPOSED_REVIEW_REQUIRED"
    assert set(candidate["applicability"]) == {
        "robot_model_id", "robot_configuration_id", "sdk_entry_id",
        "granularity_condition", "capability_effect_scope", "observation_condition",
    }
    assert candidate["applicability"]["sdk_entry_id"] == "unitree-sdk2-go2-lowlevel@1.0.0"
    assert candidate["applicability"]["granularity_condition"] == "G2"
    assert set(candidate["provenance"]) == {
        "closure_hash", "summary_ref", "stage_artifacts_ref", "evidence_digest_hash",
    }
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    assert candidate["provenance"]["summary_ref"] == (
        f"{closure['files']['summary']['path']}#sha256:{closure['files']['summary']['sha256']}"
    )
    assert candidate["provenance"]["stage_artifacts_ref"] == (
        f"{closure['files']['stage_artifacts']['path']}#sha256:"
        f"{closure['files']['stage_artifacts']['sha256']}"
    )
    assert set(report) == {
        "artifact_type", "format_version", "candidate_id", "status",
        "evidence_digest_hash", "excluded_categories", "checks",
    }
    assert report["status"] == "PASS"
    assert report["excluded_categories"] == [
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
    ]
    assert report["excluded_categories"] == list(EXCLUDED_EVIDENCE_CATEGORIES)
    assert all(type(value) is bool and value is True for value in report["checks"].values())
    assert result.candidate_hash == content_hash(result.candidate_path.read_bytes())
    assert result.declassification_report_hash == content_hash(
        result.declassification_report_path.read_bytes()
    )
    assert result.candidate_path.read_bytes() == canonical_bytes(candidate)
    assert result.declassification_report_path.read_bytes() == canonical_bytes(report)
    assert candidate["declassification_report_hash"] == result.declassification_report_hash
    assert verify_seal(json.loads(result.candidate_seal_path.read_text(encoding="utf-8")))
    assert verify_seal(
        json.loads(result.declassification_report_seal_path.read_text(encoding="utf-8"))
    )
    before = {
        path: path.read_bytes()
        for path in result.candidate_path.parent.iterdir()
        if path.is_file()
    }
    with pytest.raises(ContractError):
        propose_experience_candidate(
            root,
            closure_path,
            closure_seal_path,
            "exact-shape",
            evolution_cases_root=root / "evolution_cases",
            candidate_fields=_semantic_fields(recipient_class="implementation"),
        )
    assert {
        path: path.read_bytes()
        for path in result.candidate_path.parent.iterdir()
        if path.is_file()
    } == before


def test_declassification_contract_rejects_false_non_boolean_missing_reordered_and_extra(
    tmp_path: Path,
) -> None:
    root, closure_path, closure_seal_path = _make_closed_run(tmp_path)
    result = propose_experience_candidate(
        root,
        closure_path,
        closure_seal_path,
        "report-contract",
        evolution_cases_root=root / "evolution_cases",
        candidate_fields=_semantic_fields(),
    )
    report = json.loads(result.declassification_report_path.read_text(encoding="utf-8"))
    validate_declassification_report(
        report,
        candidate_id="report-contract",
        evidence_digest_hash=result.evidence_digest_hash,
    )
    invalid_reports: list[dict[str, object]] = []

    false_check = copy.deepcopy(report)
    false_check["checks"]["sanitized_digest_only"] = False
    invalid_reports.append(false_check)

    non_boolean_check = copy.deepcopy(report)
    non_boolean_check["checks"]["sanitized_digest_only"] = "true"
    invalid_reports.append(non_boolean_check)

    missing_check = copy.deepcopy(report)
    del missing_check["checks"]["sanitized_digest_only"]
    invalid_reports.append(missing_check)

    extra_check = copy.deepcopy(report)
    extra_check["checks"]["extra"] = True
    invalid_reports.append(extra_check)

    reordered_categories = copy.deepcopy(report)
    reordered_categories["excluded_categories"] = list(
        reversed(reordered_categories["excluded_categories"])
    )
    invalid_reports.append(reordered_categories)

    for invalid_report in invalid_reports:
        with pytest.raises(ContractError):
            validate_declassification_report(
                invalid_report,
                candidate_id="report-contract",
                evidence_digest_hash=result.evidence_digest_hash,
            )


@pytest.mark.parametrize("mutation", ["missing", "tampered", "unsealed"])
def test_missing_tampered_or_unsealed_closure_rejects(tmp_path: Path, mutation: str) -> None:
    root, closure_path, closure_seal_path = _make_closed_run(tmp_path)
    if mutation == "missing":
        closure_path = tmp_path / "missing-run-closure.json"
    elif mutation == "tampered":
        summary_path = closure_path.parent / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["status"] = "DEMO_FAILED"
        write_stable_json(summary_path, summary)
    else:
        closure_seal_path.unlink()
    with pytest.raises(ContractError):
        propose_experience_candidate(
            root,
            closure_path,
            closure_seal_path,
            f"reject-{mutation}",
            evolution_cases_root=root / "evolution_cases",
            candidate_fields=_semantic_fields(),
        )
    assert not (root / "evolution_cases").exists()


@pytest.mark.parametrize(
    "bad_text",
    [
        "threshold is 0.1",
        "private case seed 7",
        "video frame camera manifest",
        "source trace raw truth credentials",
        "def move():\n    return qpos",
    ],
)
def test_private_or_full_code_like_semantics_are_rejected(tmp_path: Path, bad_text: str) -> None:
    root, closure_path, closure_seal_path = _make_closed_run(tmp_path)
    fields = _semantic_fields()
    fields["lesson"] = bad_text
    with pytest.raises(ContractError):
        propose_experience_candidate(
            root,
            closure_path,
            closure_seal_path,
            "private-rejected",
            evolution_cases_root=root / "evolution_cases",
            candidate_fields=fields,
        )
    assert not (root / "evolution_cases").exists()


@pytest.mark.parametrize(
    "sdk_entry_id",
    ["invented-sdk@1.0.0", "unitree-sdk2-go2-lowlevel", "unitree-sdk2-go2-lowlevel@9.9.9"],
)
def test_implementation_sdk_entry_must_match_verified_stage1_projection(
    tmp_path: Path, sdk_entry_id: str
) -> None:
    root, closure_path, closure_seal_path = _make_closed_run(tmp_path)
    fields = _semantic_fields(recipient_class="implementation")
    fields["applicability"]["sdk_entry_id"] = sdk_entry_id
    with pytest.raises(ContractError):
        propose_experience_candidate(
            root,
            closure_path,
            closure_seal_path,
            "wrong-sdk",
            evolution_cases_root=root / "evolution_cases",
            candidate_fields=fields,
        )


def test_granularity_condition_must_match_verified_closed_run(tmp_path: Path) -> None:
    root, closure_path, closure_seal_path = _make_closed_run(tmp_path)
    fields = _semantic_fields()
    fields["applicability"]["granularity_condition"] = "G2 reusable effect"
    with pytest.raises(ContractError, match="granularity"):
        propose_experience_candidate(
            root,
            closure_path,
            closure_seal_path,
            "wrong-granularity",
            evolution_cases_root=root / "evolution_cases",
            candidate_fields=fields,
        )
    assert not (root / "evolution_cases").exists()


def test_implementation_sdk_entry_is_required_when_stage1_projection_is_absent(
    tmp_path: Path,
) -> None:
    root, closure_path, closure_seal_path = _make_closed_run(
        tmp_path, include_sdk_projection=False
    )
    with pytest.raises(ContractError):
        propose_experience_candidate(
            root,
            closure_path,
            closure_seal_path,
            "missing-sdk",
            evolution_cases_root=root / "evolution_cases",
            candidate_fields=_semantic_fields(recipient_class="implementation"),
        )


def test_cli_manual_smoke_and_model_api_mode_only_see_sanitized_digest(tmp_path: Path) -> None:
    root, closure_path, closure_seal_path = _make_closed_run(tmp_path)
    candidate_json = tmp_path / "candidate-fields.json"
    write_stable_json(candidate_json, _semantic_fields())
    script = PROJECT_ROOT / "general_demo" / "scripts" / "propose_experience_candidate.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(root),
            "--closure",
            str(closure_path),
            "--closure-seal",
            str(closure_seal_path),
            "--case-id",
            "cli-manual",
            "--evolution-cases",
            str(root / "evolution_cases"),
            "--candidate-json",
            str(candidate_json),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    cli_result = json.loads(completed.stdout)
    assert Path(cli_result["candidate"]).is_file()

    sys.path.insert(0, str(PROJECT_ROOT / "general_demo" / "scripts"))
    cli = importlib.import_module("propose_experience_candidate")

    class StubClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, object]]] = []

        def generate_json(self, *, stage: str, prompt: str, inputs: dict[str, object]) -> dict[str, object]:
            self.calls.append((stage, prompt, copy.deepcopy(inputs)))
            return _semantic_fields()

    client = StubClient()
    model_result = cli.main(
        [
            "--root", str(root),
            "--closure", str(closure_path),
            "--closure-seal", str(closure_seal_path),
            "--case-id", "cli-model",
            "--evolution-cases", str(root / "evolution_cases"),
            "--model-api",
        ],
        model_api_factory=lambda: client,
    )
    assert model_result == 0
    assert len(client.calls) == 1
    stage, prompt, inputs = client.calls[0]
    assert stage == "evolution"
    assert "robot-specific fix" not in prompt
    callback_text = canonical_bytes(inputs).decode("utf-8").casefold()
    assert "threshold" not in callback_text
    assert "trace" not in callback_text


def test_model_api_evolution_prompt_declares_exact_json_types() -> None:
    sys.path.insert(0, str(PROJECT_ROOT / "general_demo" / "scripts"))
    cli = importlib.import_module("propose_experience_candidate")

    class PromptCaptureClient:
        def __init__(self) -> None:
            self.stage = ""
            self.prompt = ""
            self.inputs: dict[str, object] = {}

        def generate_json(
            self,
            *,
            stage: str,
            prompt: str,
            inputs: dict[str, object],
        ) -> dict[str, object]:
            self.stage = stage
            self.prompt = prompt
            self.inputs = copy.deepcopy(inputs)
            return {"ok": True}

    client = PromptCaptureClient()
    agent = cli.model_api_agent(client)

    assert agent({"evidence": "sanitized"}) == {"ok": True}
    assert client.stage == "evolution"
    assert client.inputs == {"evidence": "sanitized"}
    for requirement in (
        'recipient_class: a JSON string enum with exactly one of "design" or "implementation".',
        "lesson: a nonempty JSON string.",
        "applicability: a JSON object with exactly these keys and types:",
        "robot_model_id: a JSON string.",
        "robot_configuration_id: a JSON string.",
        "sdk_entry_id: a JSON string or JSON null.",
        "granularity_condition: a JSON string.",
        "capability_effect_scope: a nonempty JSON array of nonempty JSON strings.",
        "observation_condition: a JSON string.",
        "limitations: a JSON array of nonempty JSON strings.",
        "invalidation_conditions: a nonempty JSON array of nonempty JSON strings.",
        "Copy robot_model_id, robot_configuration_id, sdk_entry_id, and granularity_condition\nexactly",
        "Keep capability_effect_scope and\nobservation_condition model-authored",
        "All free-text fields must be plain prose only. Do not use code fences, assignments,\nfunction signatures or call expressions, braces, semicolons, arrows, filenames, or\nsource snippets. Refer to API concepts in ordinary prose rather than reproducing syntax.",
        "Free-text fields must avoid these reserved privacy/evidence words and their plural forms:\nthreshold, criterion, case, seed, video, frame, camera, trace, diagnostic, source, code,\nMuJoCo, qpos, qvel, truth, private, translation, transport, credential, password, secret,\ntoken, prompt, LLM, oracle, expected, winning, raw.",
    ):
        assert requirement in client.prompt
