from __future__ import annotations

import copy
import json
import hashlib
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from soarm_demo.audit import BudgetCounter
from soarm_demo.generation import ContinuousGeneration
from soarm_demo.model_client import ModelAPIError, ScriptedModelClient
from soarm_demo.private_execution_bundle import PrivateExecutionBundleError
from soarm_demo.react_agent import GenerationReActAgent
from soarm_demo.report_audit import terminal_report_semantic_errors
import soarm_demo.pipeline as pipeline_module
from soarm_demo.pipeline import (
    DemoPaths,
    PipelineError,
    _file_evidence,
    _load_run_config,
    _safe_trace_name,
    _video_record,
    _write_video_index,
    run_pipeline,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _demo_root(tmp_path: Path) -> Path:
    demo = tmp_path / "demo"
    demo.mkdir()
    for name in ("configs", "libraries", "private", "fixtures", "prompts", "schemas"):
        shutil.copytree(ROOT / name, demo / name)
    return demo


def _run_stub(tmp_path: Path, *, suite_sha256: str = "a" * 64):
    root = tmp_path / "run"
    root.mkdir()
    return SimpleNamespace(
        root=root,
        input_hashes={},
        artifact_hashes={
            "validation_suite": suite_sha256,
            "generated_package": "c" * 64,
        },
    )


def _video_run_config(*, width: int = 160, height: int = 120) -> dict:
    return {
        "runtime": {
            "record_video_in_aws": True,
            "video_renderer": "mujoco.Renderer",
            "video_codec": "mp4v",
            "video_fps": 20,
            "video_width": width,
            "video_height": height,
        }
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _recount_repair_ledger(ledger: dict) -> dict:
    ledger["utf8_bytes"] = 0
    for _ in range(4):
        encoded = json.dumps(
            ledger,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if ledger["utf8_bytes"] == len(encoded):
            break
        ledger["utf8_bytes"] = len(encoded)
    return ledger


def _repair_ledger_fixture(round_count: int) -> dict:
    failure_ref = "f1"
    signature = {
        "code": "INITIAL_FAILURE",
        "capability_ref": "cap_" + "1" * 24,
        "case_ref": "case_" + "2" * 24,
    }
    signature["signature_sha256"] = hashlib.sha256(
        json.dumps(
            signature,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    rounds = []
    unresolved = []
    for repair_round in range(1, round_count + 1):
        has_later_same_stage = repair_round < round_count
        resolution = {
            "failure_refs": [failure_ref],
            "resolution_status": (
                "still_unresolved"
                if has_later_same_stage
                else "not_revalidated"
            ),
            "unresolved": True,
            "last_same_stage_validation_round": (
                round_count if has_later_same_stage else None
            ),
        }
        rounds.append(
            {
                "round": repair_round,
                "feedback": {
                    "stage": "direct_function",
                    "feedback_repair_round": repair_round,
                    "result": "failed",
                    "failure_count": 1,
                    "failure_refs": [failure_ref],
                },
                "package": {
                    "before_sha256": "a" * 64,
                    "inspection_status": "complete",
                    "after_sha256": "a" * 64,
                    "changed": False,
                },
                "agent": {
                    "status": "budget_exhausted",
                    "model_calls": 6,
                    "agent_turns": 6,
                    "provider_http_attempts": 0,
                    "provider_retries": 0,
                },
                "process": {
                    "schema_version": "robot_capability.repair_process_audit.v1",
                    "tool_call_count": 0,
                    "successful_write_calls": 0,
                    "protocol_error_count": 0,
                    "truncated_response_count": 0,
                    "provider_failure_count": 0,
                    "final_rejection_count": 0,
                    "last_action": "none",
                    "unfinished_read": None,
                    "tool_calls": [],
                    "privacy": {
                        "raw_model_text_included": False,
                        "raw_tool_arguments_included": False,
                        "raw_tool_results_included": False,
                        "raw_source_included": False,
                    },
                },
                "subsequent_validation": None,
                "validation_observations": [],
                "failure_resolution": [resolution],
                "secondary_errors": [],
            }
        )
        unresolved.append(
            {
                "origin_round": repair_round,
                **copy.deepcopy(resolution),
            }
        )
    ledger = {
        "schema_version": "robot_capability.repair_ledger.v2",
        "continuity": {
            "agent_id": "test-generation",
            "session_id": "one-session",
            "episode_id": "one-episode",
            "same_agent_session": True,
        },
        "through_repair_round": round_count,
        "round_count": round_count,
        "failure_registry": {failure_ref: signature},
        "rounds": rounds,
        "unresolved_prior_failures": unresolved,
        "privacy_contract": {
            "contains_raw_messages": False,
            "contains_raw_tracebacks": False,
            "contains_raw_measurements_or_diagnostics": False,
            "failure_signature_fields": [
                "code",
                "capability_ref",
                "case_ref",
                "diagnostic",
                "signature_sha256",
            ],
            "allowed_diagnostic_fields": [
                "kind",
                "unresolved_symbol",
            ],
            "identifier_pseudonymization": {
                "algorithm": "salted_sha256_truncated_96bit",
                "scope": "generation_agent_instance",
                "cross_round_linkable": True,
                "cross_run_linkable": False,
                "raw_identifiers_retained": False,
            },
        },
        "max_utf8_bytes": 64 * 1024,
        "utf8_bytes": 0,
    }
    return _recount_repair_ledger(ledger)


def _write_test_video(
    path: Path,
    *,
    frames: int = 2,
    width: int = 160,
    height: int = 120,
    encoded_fps: float = 20.0,
    metadata_frames: int | None = None,
    metadata_fps: float | None = None,
    simulation_end_s: float | None = None,
    metadata_extra: dict | None = None,
) -> None:
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        encoded_fps,
        (width, height),
    )
    if not writer.isOpened():
        pytest.skip("test environment does not provide the mp4v encoder")
    try:
        for index in range(frames):
            writer.write(
                numpy.full((height, width, 3), index * 30, dtype=numpy.uint8)
            )
    finally:
        writer.release()
    sidecar_frames = frames if metadata_frames is None else metadata_frames
    sidecar_fps = encoded_fps if metadata_fps is None else metadata_fps
    if simulation_end_s is None:
        simulation_end_s = max(0.0, (sidecar_frames - 2) / sidecar_fps)
    metadata = {
        "schema_version": "robot_capability.mujoco_video_evidence.v1",
        "video_path": path.name,
        "renderer_kind": "mujoco.Renderer",
        "codec": "mp4v",
        "fps": sidecar_fps,
        "frames": sidecar_frames,
        "width": width,
        "height": height,
        "simulation_start_s": 0.0,
        "simulation_end_s": simulation_end_s,
    }
    if metadata_extra:
        metadata.update(copy.deepcopy(metadata_extra))
    _write_json(path.with_suffix(".metadata.json"), metadata)


def _write_direct_report(
    run,
    round_index: int,
    case_ids: list[str],
    *,
    suite_sha256: str = "a" * 64,
    package_sha256: str = "b" * 64,
    scene_binding_by_case: dict[str, dict] | None = None,
) -> Path:
    path = run.root / "validation" / f"direct_round_{round_index:02d}.json"
    _write_json(
        path,
        {
            "schema_version": "robot_capability.direct_validation_report.v1",
            "suite_sha256": suite_sha256,
            "package_sha256": package_sha256,
            "cases": [
                {
                    "case_id": case_id,
                    **(
                        {
                            "framework_diagnostics": {
                                "resolved_scene_binding": copy.deepcopy(
                                    scene_binding_by_case[case_id]
                                )
                            }
                        }
                        if scene_binding_by_case is not None
                        and case_id in scene_binding_by_case
                        else {}
                    ),
                }
                for case_id in case_ids
            ],
            "summary": {"total": len(case_ids)},
        },
    )
    return path


def _write_demo_contract(
    run,
    task_ids: list[str],
    *,
    scene_binding_by_task: dict[str, dict] | None = None,
) -> None:
    freeze_path = run.root / "demo/freeze.json"
    _write_json(
        freeze_path,
        {
            "package_sha256": run.artifact_hashes["generated_package"],
            "tasks": [{"task_id": task_id} for task_id in task_ids],
        },
    )
    _write_json(
        run.root / "demo/demo_report.json",
        {
            "package_sha256": run.artifact_hashes["generated_package"],
            "demo_freeze_sha256": _sha256(freeze_path),
            "tasks": [
                {
                    "task_id": task_id,
                    **(
                        {
                            "oracle": {
                                "resolved_scene_binding": copy.deepcopy(
                                    scene_binding_by_task[task_id]
                                )
                            }
                        }
                        if scene_binding_by_task is not None
                        and task_id in scene_binding_by_task
                        else {}
                    ),
                }
                for task_id in task_ids
            ],
        },
    )


def _install_formal_video_scene_binding(run) -> dict:
    catalog_source_sha256 = "d" * 64
    manifest = {
        "bindings": {
            "morphology": {
                "scene_asset_catalog": {
                    "catalog_id": "soarm101_tabletop_primitives",
                    "version": "1.0.0",
                    "sha256": catalog_source_sha256,
                }
            }
        }
    }
    manifest_sha256 = pipeline_module.sha256_json(manifest)
    freeze_path = run.root / "generation/environment_freeze.json"
    _write_json(
        freeze_path,
        {"manifest": manifest, "manifest_sha256": manifest_sha256},
    )
    freeze_sha256 = _sha256(freeze_path)
    run.input_hashes.update(
        {
            "generation_environment_freeze": freeze_sha256,
            "generation_environment_manifest": manifest_sha256,
            "generation_environment_source:morphology:scene_asset_catalog": (
                catalog_source_sha256
            ),
        }
    )
    return {
        "resolved_scene_binding": {
            "binding_sha256": "e" * 64,
            "source_scene_request_sha256": "f" * 64,
            "scene_catalog": {
                "catalog_id": "soarm101_tabletop_primitives",
                "version": "1.0.0",
                "source_sha256": catalog_source_sha256,
                "content_sha256": "1" * 64,
            },
        },
        "generation_environment_freeze_sha256": freeze_sha256,
        "scene_revision_sha256": manifest_sha256,
        "scene_catalog_sha256": catalog_source_sha256,
    }


def _synthetic_resolved_scene_binding(binding_digit: str) -> dict:
    return {
        "binding_sha256": binding_digit * 64,
        "source_scene_request_sha256": "f" * 64,
        "scene_catalog": {
            "catalog_id": "soarm101_tabletop_primitives",
            "version": "1.0.0",
            "source_sha256": "d" * 64,
            "content_sha256": "1" * 64,
        },
    }


def test_run_config_requires_deterministic_host_auto_step_false(
    tmp_path: Path,
) -> None:
    demo = _demo_root(tmp_path)
    config_path = demo / "configs/run.yaml"

    assert _load_run_config(DemoPaths(demo))["runtime"]["host_auto_step"] is False

    source = config_path.read_text(encoding="utf-8")
    assert "host_auto_step: false" in source
    config_path.write_text(
        source.replace("host_auto_step: false", "host_auto_step: true", 1),
        encoding="utf-8",
    )

    with pytest.raises(PipelineError, match="host_auto_step must be false"):
        _load_run_config(DemoPaths(demo))


def test_offline_pipeline_seals_all_phases(tmp_path: Path) -> None:
    demo = _demo_root(tmp_path)
    experience_records = demo / "libraries/experience/v1/records.jsonl"
    experience_before = experience_records.read_bytes()
    output = run_pipeline(demo_root=demo, mode="offline", run_id="e2e")
    sealed = json.loads((output / "sealed_report.json").read_text(encoding="utf-8"))
    jsonschema.validate(
        sealed,
        json.loads((ROOT / "schemas/sealed_run_report.schema.json").read_text(encoding="utf-8")),
    )
    assert sealed["terminal_state"] == "SEALED"
    assert sealed["validation"]["static_passed"] is True
    assert sealed["validation"]["direct_function_passed"] is True
    assert sealed["budgets"]["generation_stage1"]["used"] == 3
    assert sealed["budgets"]["generation_stage2_initial"]["used"] <= 30
    assert sealed["budgets"]["generation_stage2_initial"]["used"] == 8
    assert sealed["budgets"]["generation_stage2_initial"]["limit"] == 30
    assert sealed["input_hashes"]["validation_reference"]
    sdk_activation = sealed["sdk_activation"]
    assert sdk_activation["decision"]["activation_scope"] == (
        "offline_reference_only"
    )
    assert sdk_activation["decision"]["live_gate_satisfied"] is False
    assert sdk_activation["decision"]["probe_status"] == "pass"
    assert sdk_activation["decision"]["verified"]["required_methods"] == [
        "connect",
        "disconnect",
        "send_action",
        "get_observation",
    ]
    assert sdk_activation["decision"]["verified"]["send_action_nonblocking"] is True
    assert sdk_activation["evidence"]["sha256"] == _sha256(
        output / "framework/sdk_activation.json"
    )
    assert sealed["artifact_hashes"]["sdk_activation"] == (
        sdk_activation["evidence"]["sha256"]
    )
    for name, binding in sdk_activation["decision"]["bindings"].items():
        assert sealed["input_hashes"][f"sdk_activation:{name}"] == binding[
            "sha256"
        ]
        assert binding["sha256"] == _sha256(
            demo
            / "libraries/sdk_runtime/lerobot_soarm101/0.6.0"
            / binding["path"]
        )
    assert not (output / "framework/sdk_activation_inputs").exists()
    assert sealed["input_hashes"]["private_execution_bundle_freeze"] == _sha256(
        output / "private_execution_bundle/freeze.json"
    )
    generation_snapshot_manifest = json.loads(
        (output / "generation_input_snapshot_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert sealed["input_hashes"]["generation_input_snapshot_manifest"] == (
        _sha256(output / "generation_input_snapshot_manifest.json")
    )
    assert sealed["input_hashes"]["generation_input_snapshot_tree"] == (
        generation_snapshot_manifest["tree_sha256"]
    )
    snapshot_evidence = sealed["evidence"]["configuration"][
        "generation_input_snapshot"
    ]
    assert snapshot_evidence["path"] == "generation_input_snapshot_manifest.json"
    assert snapshot_evidence["sha256"] == sealed["input_hashes"][
        "generation_input_snapshot_manifest"
    ]
    assert sealed["input_hashes"]["schema:validation_case.schema.json"]
    assert sealed["input_hashes"]["prompt:validation_generate.md"]
    assert sealed["input_hashes"]["generation_environment_freeze"] == _sha256(
        output / "generation/environment_freeze.json"
    )
    environment_freeze = json.loads(
        (output / "generation/environment_freeze.json").read_text(encoding="utf-8")
    )
    assert sealed["input_hashes"]["generation_environment_manifest"] == (
        environment_freeze["manifest_sha256"]
    )
    assert any(
        key.startswith("generation_environment_source:morphology:")
        for key in sealed["input_hashes"]
    )
    assert sealed["artifact_hashes"]["generation_simulation_inspection"] == (
        _sha256(output / "generation/simulation/catalog_inspection.json")
    )
    simulation_accounting = json.loads(
        (output / "generation/simulation/accounting.json").read_text(
            encoding="utf-8"
        )
    )
    assert simulation_accounting["evidence_class"] == (
        "static_catalog_inspection_no_world"
    )
    assert "scene_builder" not in simulation_accounting
    assert "scene_asset_requests" not in simulation_accounting
    assert (
        output
        / "input_snapshot/morphology/scenes/soarm101_tabletop/v1/scene.yaml"
    ).is_file()
    assert "AWS_MODEL_API_KEY" not in sealed["input_hashes"]
    assert sealed["budgets"]["repair"]["round_limit"] == 10
    assert sealed["budgets"]["repair"]["model_call_limit_per_round"] == 6
    assert sealed["budgets"]["repair"]["rounds_used"] == 0
    assert sealed["model_accounting"]["generation_phases"]["repair_rounds"] == []
    assert sealed["budgets"]["validation_suite"]["used"] == 3
    assert sealed["demo"]["summary"]["visible"]["total"] == 3
    assert sealed["demo"]["summary"]["pilot-held-out"]["total"] == 3
    assert sealed["demo"]["summary"]["distinct_task_sessions"] == 6
    assert sealed["model_accounting"]["aggregate"]["provider_http_attempts"] == 0
    assert sealed["model_accounting"]["roles"]["generation"]["scripted"] is True
    assert sealed["evidence"]["traces"]
    assert sealed["video_recording"]["required"] is False
    assert sealed["video_recording"]["status"] == "complete"
    assert sealed["video_recording"]["validation"] == []
    assert sealed["video_recording"]["demo"] == []
    assert not list(output.rglob("*.mp4"))
    private_preflight = json.loads(
        (output / "framework/private_execution_preflight.json").read_text(
            encoding="utf-8"
        )
    )
    assert private_preflight["partition_counts"] == {
        "catalog_visible": 9,
        "catalog_pilot_heldout": 3,
        "authored_instances": 12,
        "selected_visible": 3,
        "selected_pilot_heldout": 3,
        "selected_overlap": 0,
    }
    assert private_preflight["mujoco_worlds_created"] == 0
    assert all(private_preflight["privacy"].values()) is False
    assert not (output / "input_snapshot/private_execution_bundle").exists()
    assert not any(
        "private_execution_bundle" in item["path"]
        for item in sealed["evidence"].get("traces", [])
    )
    assert sealed["artifact_hashes"]["video_index"] == _sha256(
        output / "video_index.json"
    )
    assert any(
        item["path"] == "video_index.json" for item in sealed["evidence"]["videos"]
    )
    assert len(sealed["demo"]["tasks"]) == 6
    assert all(task["termination_reason"] for task in sealed["demo"]["tasks"])
    assert all(not Path(task["trace_path"]).is_absolute() for task in sealed["demo"]["tasks"])
    assert len(
        [key for key in sealed["input_hashes"] if key.startswith("demo_initial_state:")]
    ) == 6
    assert len(
        [
            key
            for key in sealed["input_hashes"]
            if key.startswith("authored_initial_state:")
        ]
    ) == 12
    assert {
        "library_manifest:morphology",
        "library_manifest:sdk_runtime",
        "library_manifest:tasks",
        "library_manifest:experience",
        "task_demo_batch",
        "task_private_oracles",
    } <= set(sealed["input_hashes"])
    assert {
        item["path"]
        for item in sealed["evidence"]["configuration"]["prompts"]
    } == {
        "prompts/demo_react.md",
        "prompts/generation_system.md",
        "prompts/repair.md",
        "prompts/stage1.md",
        "prompts/stage2.md",
        "prompts/validation_final_review.md",
        "prompts/validation_generate.md",
        "prompts/validation_review.md",
    }
    preseal = sealed["evidence"]["run_manifest_preseal"]
    assert preseal["path"] == "run_manifest_preseal.json"
    assert _sha256(output / preseal["path"]) == preseal["sha256"]
    assert len(sealed["evidence"]["traces"]) >= 8
    assert all(not Path(item["path"]).is_absolute() for item in sealed["evidence"]["traces"])
    for item in sealed["evidence"]["traces"]:
        assert _sha256(output / item["path"]) == item["sha256"]
    assert sealed["model_accounting"]["aggregate"]["logical_requests"] == 26
    assert sealed["model_accounting"]["aggregate"]["usage"]["cost_total"] == 0.0
    serialized = json.dumps(sealed, sort_keys=True)
    assert "AWS_MODEL_API_KEY" not in serialized
    assert "api_key" not in serialized.lower()
    generation_session = sealed["generation_agent"]["session_id"]
    report = json.loads((output / "demo/demo_report.json").read_text(encoding="utf-8"))
    assert all(task["agent_session_id"] != generation_session for task in report["tasks"])
    assert len({task["agent_session_id"] for task in report["tasks"]}) == 6
    freeze = json.loads((output / "demo/freeze.json").read_text(encoding="utf-8"))
    jsonschema.validate(
        freeze,
        json.loads((ROOT / "schemas/demo_freeze.schema.json").read_text(encoding="utf-8")),
    )
    assert len(freeze["tasks"]) == 6
    assert {item["partition"] for item in freeze["tasks"]} == {
        "visible",
        "pilot-held-out",
    }
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "SEALED"
    assert manifest["artifact_hashes"]["sealed_report"] == _sha256(
        output / "sealed_report.json"
    )
    candidate_path = output / "evolution/candidate_bundle.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    jsonschema.validate(
        candidate,
        json.loads(
            (ROOT / "schemas/evolution_candidate_bundle.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    assert candidate["run"]["terminal_state"] == "SEALED"
    assert candidate["run"]["source_report"]["path"] == "sealed_report.json"
    assert "candidate_bundle" not in sealed["artifact_hashes"]
    assert "candidate_bundle" not in manifest["artifact_hashes"]
    assert experience_records.read_bytes() == experience_before


def test_offline_pipeline_private_bundle_tamper_aborts_before_demo_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = _demo_root(tmp_path)
    paths = DemoPaths(demo)
    clients = pipeline_module._model_clients(
        "offline", paths, _load_run_config(paths)
    )
    monkeypatch.setattr(
        pipeline_module,
        "_model_clients",
        lambda mode, selected_paths, run_config, framework_inputs=None: clients,
    )
    original_run = pipeline_module.FrozenDemoRunner.run

    def tampering_run(self, **kwargs):
        bundle = kwargs["private_execution_bundle"]
        target = bundle.path_for_role("instance_01")
        os.chmod(target, 0o644)
        target.write_bytes(target.read_bytes() + b"\nPRIVATE_TAMPER_PAYLOAD")
        return original_run(self, **kwargs)

    monkeypatch.setattr(pipeline_module.FrozenDemoRunner, "run", tampering_run)

    with pytest.raises(PrivateExecutionBundleError):
        run_pipeline(demo_root=demo, mode="offline", run_id="private-tamper")

    assert clients[2].audit_snapshot()["logical_requests"] == 0
    run_root = demo / "runs/private-tamper"
    manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
    terminal = json.loads((run_root / "terminal_report.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "INFRASTRUCTURE_FAILED"
    assert terminal["terminal_state"] == "INFRASTRUCTURE_FAILED"
    assert all(
        "private_execution_bundle" not in Path(item["path"]).parts
        for item in terminal["evidence_files"]
    )
    assert "PRIVATE_TAMPER_PAYLOAD" not in json.dumps(terminal, sort_keys=True)


def test_consistent_demo_oracle_miss_is_reported_and_run_still_seals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = _demo_root(tmp_path)
    original_run = pipeline_module.FrozenDemoRunner.run

    def run_with_oracle_failure(self, **kwargs):
        report = original_run(self, **kwargs)
        report["tasks"][0]["passed"] = False
        report["tasks"][0]["oracle"]["passed"] = False
        visible = report["summary"]["visible"]
        visible.update(passed=2, success_rate=2 / 3)
        overall = report["summary"]["overall"]
        overall.update(passed=5, success_rate=5 / 6)
        _write_json(self.output_dir / "demo_report.json", report)
        return report

    monkeypatch.setattr(
        pipeline_module.FrozenDemoRunner,
        "run",
        run_with_oracle_failure,
    )

    output = run_pipeline(
        demo_root=demo,
        mode="offline",
        run_id="demo-performance-miss",
    )

    assert output == demo / "runs/demo-performance-miss"
    assert (output / "sealed_report.json").is_file()
    manifest = json.loads(
        (output / "run_manifest.json").read_text(encoding="utf-8")
    )
    sealed = json.loads(
        (output / "sealed_report.json").read_text(encoding="utf-8")
    )
    assert manifest["state"] == "SEALED"
    assert not (output / "terminal_report.json").exists()
    assert sealed["demo"]["summary"]["overall"] == {
        "passed": 5,
        "total": 6,
        "success_rate": 5 / 6,
    }


def test_malformed_demo_report_is_infrastructure_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = _demo_root(tmp_path)
    original_run = pipeline_module.FrozenDemoRunner.run

    def run_with_malformed_report(self, **kwargs):
        report = original_run(self, **kwargs)
        report["tasks"].pop()
        _write_json(self.output_dir / "demo_report.json", report)
        return report

    monkeypatch.setattr(
        pipeline_module.FrozenDemoRunner,
        "run",
        run_with_malformed_report,
    )

    with pytest.raises(PipelineError, match="Demo report failed runtime schema validation"):
        run_pipeline(demo_root=demo, mode="offline", run_id="demo-malformed")

    output = demo / "runs/demo-malformed"
    manifest = json.loads(
        (output / "run_manifest.json").read_text(encoding="utf-8")
    )
    terminal = json.loads(
        (output / "terminal_report.json").read_text(encoding="utf-8")
    )
    assert manifest["state"] == "INFRASTRUCTURE_FAILED"
    assert terminal["terminal_state"] == "INFRASTRUCTURE_FAILED"


def test_inconsistent_demo_task_and_oracle_outcome_is_infrastructure_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = _demo_root(tmp_path)
    original_run = pipeline_module.FrozenDemoRunner.run

    def run_with_inconsistent_outcome(self, **kwargs):
        report = original_run(self, **kwargs)
        report["tasks"][0]["oracle"]["passed"] = False
        _write_json(self.output_dir / "demo_report.json", report)
        return report

    monkeypatch.setattr(
        pipeline_module.FrozenDemoRunner,
        "run",
        run_with_inconsistent_outcome,
    )

    with pytest.raises(
        PipelineError,
        match="Demo report failed structural semantic validation",
    ):
        run_pipeline(demo_root=demo, mode="offline", run_id="demo-inconsistent")

    terminal = json.loads(
        (demo / "runs/demo-inconsistent/terminal_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert terminal["terminal_state"] == "INFRASTRUCTURE_FAILED"


def test_demo_runtime_failure_remains_infrastructure_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = _demo_root(tmp_path)

    def fail_demo_runtime(self, **kwargs):
        del self, kwargs
        raise RuntimeError("demo-runtime-primary-error")

    monkeypatch.setattr(
        pipeline_module.FrozenDemoRunner,
        "run",
        fail_demo_runtime,
    )

    with pytest.raises(RuntimeError, match="demo-runtime-primary-error"):
        run_pipeline(demo_root=demo, mode="offline", run_id="demo-infra-failed")

    output = demo / "runs/demo-infra-failed"
    manifest = json.loads(
        (output / "run_manifest.json").read_text(encoding="utf-8")
    )
    terminal = json.loads(
        (output / "terminal_report.json").read_text(encoding="utf-8")
    )
    assert manifest["state"] == "INFRASTRUCTURE_FAILED"
    assert terminal["terminal_state"] == "INFRASTRUCTURE_FAILED"
    assert terminal["terminal_reason"] == "RuntimeError"
    assert terminal["error"] == {"type": "RuntimeError"}


def test_task_local_demo_runtime_failure_remains_infrastructure_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = _demo_root(tmp_path)
    original_run = pipeline_module.FrozenDemoRunner.run

    def run_with_recorded_infrastructure_failure(self, **kwargs):
        report = original_run(self, **kwargs)
        task = report["tasks"][0]
        task["passed"] = False
        task["agent_status"] = "infrastructure_failed"
        task["termination_reason"] = "infrastructure_failure"
        task["oracle"] = {
            "passed": False,
            "infrastructure_error": "RuntimeError: redacted",
        }
        visible = report["summary"]["visible"]
        visible.update(passed=2, success_rate=2 / 3)
        overall = report["summary"]["overall"]
        overall.update(passed=5, success_rate=5 / 6)
        _write_json(self.output_dir / "demo_report.json", report)
        return report

    monkeypatch.setattr(
        pipeline_module.FrozenDemoRunner,
        "run",
        run_with_recorded_infrastructure_failure,
    )

    with pytest.raises(PipelineError, match="reported an infrastructure failure"):
        run_pipeline(
            demo_root=demo,
            mode="offline",
            run_id="demo-task-infrastructure-failed",
        )

    output = demo / "runs/demo-task-infrastructure-failed"
    manifest = json.loads(
        (output / "run_manifest.json").read_text(encoding="utf-8")
    )
    terminal = json.loads(
        (output / "terminal_report.json").read_text(encoding="utf-8")
    )
    assert manifest["state"] == "INFRASTRUCTURE_FAILED"
    assert manifest["terminal_reason"] == "PipelineError"
    assert terminal["terminal_state"] == "INFRASTRUCTURE_FAILED"
    assert terminal["terminal_reason"] == "PipelineError"
    assert terminal["error"] == {"type": "PipelineError"}


def test_no_change_repairs_skip_static_direct_and_video_rounds_until_tree_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    demo = _demo_root(tmp_path)
    paths = DemoPaths(demo)
    unchanged_final = json.dumps(
        {"type": "final", "content": "repair complete"},
        sort_keys=True,
    )
    changed_init = (
        demo
        / "fixtures/generated_pass/generated_capability_package/__init__.py"
    ).read_text(encoding="utf-8") + "\n# package-change-gate-test\n"
    generation_responses = [
        *pipeline_module.offline_generation_responses(paths),
        *([unchanged_final] * 6),
        *([unchanged_final] * 6),
        json.dumps(
            {
                "type": "tool",
                "tool": "write_generated_file",
                "arguments": {
                    "path": "generated_capability_package/__init__.py",
                    "content": changed_init,
                },
            },
            sort_keys=True,
        ),
        json.dumps(
            {"type": "final", "content": "package changed"},
            sort_keys=True,
        ),
    ]
    suite = (demo / "fixtures/validation_suite.json").read_text(encoding="utf-8")
    generation_client = ScriptedModelClient(
        generation_responses,
        model="scripted-generation-no-change-gate",
    )
    suite_client = ScriptedModelClient([suite, suite, suite], model="scripted-suite")
    demo_client = ScriptedModelClient(
        pipeline_module.offline_demo_responses(paths),
        model="scripted-demo",
    )
    monkeypatch.setattr(
        pipeline_module,
        "_model_clients",
        lambda mode, paths, run_config, framework_inputs=None: (
            generation_client,
            suite_client,
            demo_client,
        ),
    )

    original_direct = pipeline_module.run_direct_validation
    direct_rounds: list[int] = []
    video_rounds: list[int] = []

    def controlled_direct_validation(**kwargs):
        output_path = Path(kwargs["output_path"])
        round_index = int(output_path.stem.rsplit("_", 1)[1])
        direct_rounds.append(round_index)
        video_rounds.append(round_index)
        marker = (
            output_path.parents[1]
            / "validation/videos"
            / f"round_{round_index:02d}"
            / "direct_invoked.marker"
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("direct validation created this video round", encoding="utf-8")
        if len(direct_rounds) == 1:
            return SimpleNamespace(
                passed=False,
                package_sha256="b" * 64,
                failure_feedback=lambda *, repair_round: {
                    "schema_version": "robot_capability.failure_feedback.v1",
                    "stage": "direct_function",
                    "repair_round": repair_round,
                    "failures": [
                        {
                            "code": "FORCED_INITIAL_DIRECT_FAILURE",
                            "message": "force repair gate coverage",
                            "capability_id": None,
                            "case_id": "forced-case",
                            "observed": "failed",
                            "target": "passed",
                            "gap": {"absolute_error": 1.0},
                            "execution": {
                                "returned_result": {
                                    "status": "success",
                                    "phase_reached": "complete",
                                },
                                "returned_status": "success",
                                "reported_phase": "complete",
                            },
                            "traceback": None,
                        }
                    ],
                },
            )
        return original_direct(**kwargs)

    monkeypatch.setattr(
        pipeline_module,
        "run_direct_validation",
        controlled_direct_validation,
    )

    output = run_pipeline(demo_root=demo, mode="offline", run_id="no-change-gate")
    sealed = json.loads((output / "sealed_report.json").read_text(encoding="utf-8"))

    assert direct_rounds == [0, 3]
    assert video_rounds == [0, 3]
    assert not (output / "validation/videos/round_01").exists()
    assert not (output / "validation/videos/round_02").exists()
    assert not (output / "validation/static_round_01.json").exists()
    assert not (output / "validation/static_round_02.json").exists()
    assert (output / "validation/static_round_03.json").is_file()
    assert sealed["repair"]["rounds_used"] == 3
    assert len(sealed["repair"]["results"]) == 3
    assert sealed["repair"]["results"][0]["status"] == "budget_exhausted"
    assert sealed["repair"]["results"][1]["status"] == "budget_exhausted"
    assert sealed["repair"]["results"][2]["status"] == "completed"
    assert sealed["budgets"]["generation_stage2_initial"]["limit"] == 30
    assert sealed["budgets"]["repair"]["round_limit"] == 10
    assert sealed["budgets"]["repair"]["model_call_limit_per_round"] == 6
    for result in sealed["repair"]["results"][:2]:
        assert result["agent_turn_budget"]["limit"] == 6
        assert result["agent_turn_budget"]["used"] == 6
    causal_failures = sealed["repairs"][0]["failures"]
    for index, carried in enumerate(sealed["repairs"][1:], start=1):
        assert carried["stage"] == "direct_function"
        assert carried["failures"] == causal_failures
        assert carried["failures"][0]["observed"] == "failed"
        assert carried["failures"][0]["target"] == "passed"
        assert carried["failures"][0]["gap"] == {"absolute_error": 1.0}
        assert carried["failures"][0]["execution"]["returned_status"] == "success"
        assert carried["no_package_change"] == {
            "code": "NO_PACKAGE_CHANGE",
            "completed_repair_round": index,
            "consecutive_rounds": index,
            "causal_feedback_repair_round": 1,
            "package_sha256": carried["no_package_change"]["package_sha256"],
            "retry_available": True,
        }
        assert len(carried["no_package_change"]["package_sha256"]) == 64
        assert all(
            failure["code"] != "NO_PACKAGE_CHANGE"
            for failure in carried["failures"]
        )
        jsonschema.validate(
            carried,
            json.loads(
                (demo / "schemas/failure_feedback.schema.json").read_text(
                    encoding="utf-8"
                )
            ),
        )

    first_gate = json.loads(
        (output / "validation/repair_result_01.json").read_text(encoding="utf-8")
    )
    second_gate = json.loads(
        (output / "validation/repair_result_02.json").read_text(encoding="utf-8")
    )
    changed_gate = json.loads(
        (output / "validation/repair_result_03.json").read_text(encoding="utf-8")
    )
    assert first_gate["code"] == second_gate["code"] == "NO_PACKAGE_CHANGE"
    assert first_gate["validation_skipped"] == {
        "static": True,
        "direct": True,
        "video": True,
    }
    assert first_gate["agent_accounting"] == sealed["repair"]["results"][0]
    assert first_gate["feedback"] == sealed["repairs"][1]
    assert second_gate["feedback"] == sealed["repairs"][2]
    assert changed_gate["code"] == "PACKAGE_CHANGED"
    assert changed_gate["package_before_sha256"] != changed_gate["package_after_sha256"]
    assert changed_gate["validation_skipped"] == {
        "static": False,
        "direct": False,
        "video": False,
    }
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    gate_events = [
        event
        for event in manifest["events"]
        if event["event"] == "repair_package_change_gate"
    ]
    assert [event["repair_round"] for event in gate_events] == [1, 2, 3]
    assert gate_events[0]["result_sha256"] == _sha256(
        output / gate_events[0]["result_path"]
    )
    gate_starts = [
        event
        for event in manifest["events"]
        if event["event"] == "repair_package_change_gate_started"
    ]
    assert [event["repair_round"] for event in gate_starts] == [1, 2, 3]
    assert gate_starts[0]["package_before_sha256"] == first_gate[
        "package_before_sha256"
    ]
    ledger_path = output / "validation/repair_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    jsonschema.validate(
        ledger,
        json.loads(
            (ROOT / "schemas/repair_ledger.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    final_round = ledger["rounds"][2]
    assert ledger["rounds"][0]["validation_observations"] == []
    assert ledger["rounds"][1]["validation_observations"] == []
    assert [
        observation["stage"]
        for observation in final_round["validation_observations"]
    ] == ["static", "direct_function"]
    assert all(
        observation["result"] == "passed"
        for observation in final_round["validation_observations"]
    )
    assert final_round["subsequent_validation"]["stage"] == "static"
    assert final_round["subsequent_validation"]["against_round_feedback"][
        "unresolved"
    ] is None
    assert final_round["failure_resolution"][0]["resolution_status"] == (
        "resolved_by_later_same_stage_pass"
    )
    assert not any(
        item["origin_round"] == 3
        for item in ledger["unresolved_prior_failures"]
    )
    assert sealed["artifact_hashes"]["repair_ledger"] == _sha256(ledger_path)


def test_no_change_repair_gate_stops_at_ten_independent_rounds(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    (package_root / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
    run = pipeline_module.RunManifest.create(
        tmp_path / "runs",
        run_id="ten-no-change-rounds",
    )
    (tmp_path / "schemas").mkdir()
    shutil.copy2(
        ROOT / "schemas/repair_ledger.schema.json",
        tmp_path / "schemas/repair_ledger.schema.json",
    )
    run.state = pipeline_module.RunState.DIRECT_FUNCTION_VALIDATION
    run.save()

    class NoChangeGeneration:
        def __init__(self) -> None:
            self.repair_rounds = 0
            self._audits: list[dict] = []

        def repair(
            self,
            feedback,
            *,
            max_rounds: int,
            max_model_calls_per_round: int,
        ) -> Path:
            assert feedback["repair_round"] == self.repair_rounds + 1
            assert max_rounds == 10
            assert max_model_calls_per_round == 6
            self.repair_rounds += 1
            self._audits.append(
                {
                    "round": self.repair_rounds,
                    "status": "budget_exhausted",
                    "agent_turns": 6,
                    "provider_http_attempts": 0,
                    "provider_retries": 0,
                    "client_kind": "test",
                    "scripted": True,
                    "agent_turn_budget": {
                        "name": f"generation_repair_round_{self.repair_rounds:02d}",
                        "limit": 6,
                        "used": 6,
                        "remaining": 0,
                    },
                    "provider_attempt_budget_policy": {},
                    "usage": {"response_count": 6},
                    "session_id": "one-session",
                    "episode_id": "one-episode",
                }
            )
            return package_root

        def repair_result_audits(self) -> list[dict]:
            return list(self._audits)

        def repair_ledger_audit(self) -> dict:
            return _repair_ledger_fixture(self.repair_rounds)

    generation = NoChangeGeneration()
    repair_history: list[dict] = []
    changed = pipeline_module._repair_until_package_changes(
        generation=generation,
        package_root=package_root,
        initial_feedback={
            "schema_version": "robot_capability.failure_feedback.v1",
            "stage": "direct_function",
            "repair_round": 1,
            "failures": [
                {
                    "code": "INITIAL_FAILURE",
                    "message": "real direct validation failed",
                    "capability_id": "G3.lift_object",
                    "case_id": "G3.lift_object.direct",
                    "observed": {"object_z_m": 0.208},
                    "target": {"object_z_m": 0.12},
                    "gap": {"absolute_error_m": 0.088},
                    "execution": {
                        "returned_status": "success",
                        "reported_phase": "complete",
                        "returned_result": {
                            "status": "success",
                            "phase_reached": "complete",
                        },
                    },
                    "traceback": None,
                }
            ],
        },
        repair_history=repair_history,
        run=run,
        run_config={
            "generation": {
                "max_repair_rounds": 10,
                "repair_max_model_calls_per_round": 6,
            }
        },
    )

    assert changed is False
    assert generation.repair_rounds == 10
    assert len(generation.repair_result_audits()) == 10
    assert len(repair_history) == 10
    causal_failures = repair_history[0]["failures"]
    for round_number, feedback in enumerate(repair_history, start=1):
        assert feedback["stage"] == "direct_function"
        assert feedback["failures"] == causal_failures
        assert feedback["failures"][0]["execution"]["returned_status"] == "success"
        if round_number == 1:
            assert "no_package_change" not in feedback
        else:
            assert feedback["no_package_change"]["completed_repair_round"] == (
                round_number - 1
            )
            assert feedback["no_package_change"]["consecutive_rounds"] == (
                round_number - 1
            )
            assert feedback["no_package_change"][
                "causal_feedback_repair_round"
            ] == 1
            assert feedback["no_package_change"]["retry_available"] is True
    assert run.state == pipeline_module.RunState.GENERATION_FAILED
    assert not list((run.root / "validation").glob("static_round_*.json"))
    assert not list((run.root / "validation").glob("direct_round_*.json"))
    assert not (run.root / "validation/videos").exists()
    assert len(list((run.root / "validation").glob("repair_result_*.json"))) == 10
    ledger_path = run.root / "validation/repair_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    jsonschema.validate(
        ledger,
        json.loads(
            (ROOT / "schemas/repair_ledger.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    assert ledger["round_count"] == 10
    assert run.artifact_hashes["repair_ledger"] == _sha256(ledger_path)
    terminal_gate = json.loads(
        (run.root / "validation/repair_result_10.json").read_text(encoding="utf-8")
    )
    assert terminal_gate["code"] == "NO_PACKAGE_CHANGE"
    assert terminal_gate["next_repair_round"] is None
    assert terminal_gate["feedback"]["stage"] == "direct_function"
    assert terminal_gate["feedback"]["failures"] == causal_failures
    assert terminal_gate["feedback"]["no_package_change"] == {
        "code": "NO_PACKAGE_CHANGE",
        "completed_repair_round": 10,
        "consecutive_rounds": 10,
        "causal_feedback_repair_round": 1,
        "package_sha256": terminal_gate["package_after_sha256"],
        "retry_available": False,
    }


def test_provider_repair_exception_persists_schema_valid_ledger_without_masking(
    tmp_path: Path,
) -> None:
    demo = tmp_path / "demo"
    (demo / "schemas").mkdir(parents=True)
    shutil.copy2(
        ROOT / "schemas/repair_ledger.schema.json",
        demo / "schemas/repair_ledger.schema.json",
    )
    package_root = demo / "generated_package"
    package_root.mkdir()
    (package_root / "g2.py").write_text("VALUE = 1\n", encoding="utf-8")
    run = pipeline_module.RunManifest.create(
        demo / "runs",
        run_id="provider-repair-failure",
    )
    run.state = pipeline_module.RunState.DIRECT_FUNCTION_VALIDATION
    run.save()

    client = ScriptedModelClient([], model="scripted-provider-failure")
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=run.root / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")

    def checkpoint(*, repair_round: int, package_tree_digest: str) -> dict:
        return {
            "schema_version": "test.repair_context_checkpoint.v1",
            "repair_round": repair_round,
            "generated_package": {"tree_sha256": package_tree_digest},
        }

    workspace = SimpleNamespace(
        package_root=package_root,
        repair_context_checkpoint=checkpoint,
        replace_generated_file_text=lambda arguments: dict(arguments),
        search_generated_file_text=lambda arguments: dict(arguments),
        read_generated_python_symbol=lambda arguments: dict(arguments),
    )
    generation = ContinuousGeneration(
        agent=agent,
        workspace=workspace,
        stage2_result=agent.accounting_result(status="completed"),
    )
    feedback = {
        "schema_version": "robot_capability.failure_feedback.v1",
        "stage": "direct_function",
        "repair_round": 1,
        "failures": [
            {
                "code": "DIRECT_EXCEPTION",
                "message": (
                    "name 'missing_helper' is not defined; "
                    "PRIVATE_PROVIDER_MESSAGE"
                ),
                "capability_id": "G2.move_tool_center",
                "case_id": "private-provider-case",
                "traceback": "PRIVATE_PROVIDER_TRACEBACK",
            }
        ],
    }
    feedback["failures"].append(copy.deepcopy(feedback["failures"][0]))

    with pytest.raises(ModelAPIError, match="queue exhausted"):
        pipeline_module._repair_until_package_changes(
            generation=generation,
            package_root=package_root,
            initial_feedback=feedback,
            repair_history=[],
            run=run,
            run_config={
                "generation": {
                    "max_repair_rounds": 10,
                    "repair_max_model_calls_per_round": 6,
                }
            },
        )

    ledger_path = run.root / "validation/repair_ledger.json"
    persisted = json.loads(ledger_path.read_text(encoding="utf-8"))
    schema = json.loads(
        (ROOT / "schemas/repair_ledger.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(persisted, schema)
    expected = generation.repair_ledger_audit()
    assert persisted == expected
    assert run.artifact_hashes["repair_ledger"] == _sha256(ledger_path)
    record = persisted["rounds"][0]
    assert record["feedback"]["failure_count"] == 2
    assert len(record["feedback"]["failure_refs"]) == 1
    assert len(record["failure_resolution"][0]["failure_refs"]) == 1
    assert record["package"]["before_sha256"]
    assert record["package"]["after_sha256"]
    assert record["package"]["changed"] is False
    assert record["agent"]["status"] == "failed"
    assert record["agent"]["model_calls"] == 1
    serialized = json.dumps(persisted, ensure_ascii=False)
    assert "PRIVATE_PROVIDER_MESSAGE" not in serialized
    assert "PRIVATE_PROVIDER_TRACEBACK" not in serialized
    assert "missing_helper" in serialized
    persisted["rounds"][0]["agent"]["status"] = "completed"
    assert generation.repair_ledger_audit() == expected


def test_invalid_repair_ledger_is_not_written_or_hashed(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    (demo / "schemas").mkdir(parents=True)
    shutil.copy2(
        ROOT / "schemas/repair_ledger.schema.json",
        demo / "schemas/repair_ledger.schema.json",
    )
    run = pipeline_module.RunManifest.create(
        demo / "runs",
        run_id="invalid-repair-ledger",
    )
    generation = SimpleNamespace(
        repair_ledger_audit=lambda: {
            "schema_version": "robot_capability.repair_ledger.v2",
            "private_traceback": "must be rejected",
        }
    )

    with pytest.raises(PipelineError, match="runtime schema validation"):
        pipeline_module._persist_repair_ledger(
            generation=generation,
            run=run,
        )

    assert not (run.root / "validation/repair_ledger.json").exists()
    assert "repair_ledger" not in run.artifact_hashes


def test_schema_valid_semantically_contradictory_ledger_is_not_persisted(
    tmp_path: Path,
) -> None:
    demo = tmp_path / "demo"
    (demo / "schemas").mkdir(parents=True)
    shutil.copy2(
        ROOT / "schemas/repair_ledger.schema.json",
        demo / "schemas/repair_ledger.schema.json",
    )
    run = pipeline_module.RunManifest.create(
        demo / "runs",
        run_id="contradictory-repair-ledger",
    )
    contradictory = _repair_ledger_fixture(1)
    contradictory["rounds"][0]["package"]["changed"] = True
    _recount_repair_ledger(contradictory)
    jsonschema.validate(
        contradictory,
        json.loads(
            (ROOT / "schemas/repair_ledger.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    generation = SimpleNamespace(
        repair_ledger_audit=lambda: copy.deepcopy(contradictory)
    )

    with pytest.raises(PipelineError, match="semantic validation"):
        pipeline_module._persist_repair_ledger(
            generation=generation,
            run=run,
        )

    assert not (run.root / "validation/repair_ledger.json").exists()
    assert "repair_ledger" not in run.artifact_hashes


def test_repair_ledger_persistence_failure_does_not_mask_primary_repair_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    (package_root / "g2.py").write_text("VALUE = 1\n", encoding="utf-8")
    run = pipeline_module.RunManifest.create(
        tmp_path / "runs",
        run_id="repair-primary-error",
    )
    run.state = pipeline_module.RunState.DIRECT_FUNCTION_VALIDATION
    run.save()

    class FailingGeneration:
        repair_rounds = 0

        @staticmethod
        def repair(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("primary-repair-provider-error")

    def fail_persistence(*args, **kwargs):
        del args, kwargs
        raise ValueError("secondary-ledger-persistence-error")

    monkeypatch.setattr(
        pipeline_module,
        "_persist_repair_ledger",
        fail_persistence,
    )

    with pytest.raises(RuntimeError, match="primary-repair-provider-error"):
        pipeline_module._repair_until_package_changes(
            generation=FailingGeneration(),
            package_root=package_root,
            initial_feedback={
                "schema_version": "robot_capability.failure_feedback.v1",
                "stage": "direct_function",
                "repair_round": 1,
                "failures": [
                    {
                        "code": "DIRECT_EXCEPTION",
                        "message": "provider failed",
                    }
                ],
            },
            repair_history=[],
            run=run,
            run_config={
                "generation": {
                    "max_repair_rounds": 10,
                    "repair_max_model_calls_per_round": 6,
                }
            },
        )


def test_failed_run_writes_sanitized_terminal_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = _demo_root(tmp_path)
    experience_records = demo / "libraries/experience/v1/records.jsonl"
    experience_before = experience_records.read_bytes()

    def fail_stage1(self: ContinuousGeneration, instruction: str):
        sandbox = self.workspace.simulation_sandbox
        assert sandbox is not None and sandbox.prepared is True
        assert (
            self.workspace.run_root / "generation/environment_freeze.json"
        ).is_file()
        assert (
            self.workspace.run_root / "generation/simulation/catalog_inspection.json"
        ).is_file()
        del instruction
        raise RuntimeError("provider-body-should-not-be-persisted")

    monkeypatch.setattr(ContinuousGeneration, "run_stage1", fail_stage1)
    with pytest.raises(RuntimeError, match="provider-body-should-not-be-persisted"):
        run_pipeline(demo_root=demo, mode="offline", run_id="failed")

    output = demo / "runs/failed"
    terminal = json.loads((output / "terminal_report.json").read_text(encoding="utf-8"))
    jsonschema.validate(
        terminal,
        json.loads((ROOT / "schemas/terminal_run_report.schema.json").read_text(encoding="utf-8")),
    )
    assert terminal["terminal_state"] == "GENERATION_FAILED"
    assert terminal["error"] == {"type": "RuntimeError"}
    assert terminal["model_accounting"]["aggregate"]["logical_requests"] == 0
    assert terminal["evidence_files"]
    terminal_manifest = next(
        item
        for item in terminal["evidence_files"]
        if item["path"] == "run_manifest_terminal_snapshot.json"
    )
    assert _sha256(output / terminal_manifest["path"]) == terminal_manifest["sha256"]
    terminal_snapshot = json.loads(
        (output / "run_manifest_terminal_snapshot.json").read_text(encoding="utf-8")
    )
    current_manifest = json.loads(
        (output / "run_manifest.json").read_text(encoding="utf-8")
    )
    assert terminal_snapshot["budgets"] == terminal["budgets"]
    assert current_manifest["budgets"] == terminal["budgets"]
    assert terminal_snapshot["input_hashes"] == terminal["input_hashes"]
    assert current_manifest["input_hashes"] == terminal["input_hashes"]
    assert terminal_snapshot["artifact_hashes"] == terminal["artifact_hashes"]
    assert current_manifest["artifact_hashes"] == {
        **terminal["artifact_hashes"],
        "terminal_report": _sha256(output / "terminal_report.json"),
    }
    assert current_manifest["events"][:-1] == terminal_snapshot["events"]
    assert current_manifest["events"][-1]["event"] == "terminal_report_written"
    accounting_event = terminal_snapshot["events"][-1]
    assert accounting_event["event"] == "terminal_accounting_finalized"
    assert accounting_event["budget_names"] == sorted(terminal["budgets"])
    assert accounting_event["logical_requests"] == terminal["model_accounting"][
        "aggregate"
    ]["logical_requests"]
    run_inputs = json.loads((output / "run_inputs.json").read_text(encoding="utf-8"))
    assert terminal["input_hashes"]["source_tree"] == run_inputs["input_hashes"][
        "source_tree"
    ]
    persisted = (output / "terminal_report.json").read_text(encoding="utf-8")
    persisted += (output / "run_manifest.json").read_text(encoding="utf-8")
    assert "provider-body-should-not-be-persisted" not in persisted
    candidate = json.loads(
        (output / "evolution/candidate_bundle.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(
        candidate,
        json.loads(
            (ROOT / "schemas/evolution_candidate_bundle.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    assert candidate["run"]["terminal_state"] == "GENERATION_FAILED"
    assert candidate["run"]["source_report"]["path"] == "terminal_report.json"
    assert "candidate_bundle" not in terminal["artifact_hashes"]
    assert experience_records.read_bytes() == experience_before

def test_post_seal_evolution_failure_preserves_sealed_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    demo = _demo_root(tmp_path)
    experience_records = demo / "libraries/experience/v1/records.jsonl"
    experience_before = experience_records.read_bytes()
    calls = 0

    def fail_candidate(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        raise RuntimeError("deterministic-evolution-failure")

    monkeypatch.setattr(pipeline_module, "write_candidate_bundle", fail_candidate)

    with pytest.raises(RuntimeError, match="deterministic-evolution-failure"):
        run_pipeline(demo_root=demo, mode="offline", run_id="sealed-evolution-failed")

    output = demo / "runs/sealed-evolution-failed"
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert calls == 1
    assert manifest["state"] == "SEALED"
    assert (output / "sealed_report.json").is_file()
    assert not (output / "terminal_report.json").exists()
    assert "candidate_bundle" not in manifest["artifact_hashes"]
    assert experience_records.read_bytes() == experience_before


def test_failed_run_evolution_failure_does_not_mask_primary_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    demo = _demo_root(tmp_path)

    def fail_stage1(self: ContinuousGeneration, instruction: str):
        del self, instruction
        raise RuntimeError("primary-provider-failure")

    def fail_candidate(*args, **kwargs):
        del args, kwargs
        raise ValueError("secondary-evolution-failure")

    monkeypatch.setattr(ContinuousGeneration, "run_stage1", fail_stage1)
    monkeypatch.setattr(pipeline_module, "write_candidate_bundle", fail_candidate)

    with pytest.raises(RuntimeError, match="primary-provider-failure"):
        run_pipeline(
            demo_root=demo,
            mode="offline",
            run_id="failed-evolution-failed",
        )

    output = demo / "runs/failed-evolution-failed"
    terminal = json.loads((output / "terminal_report.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert terminal["error"] == {"type": "RuntimeError"}
    assert manifest["state"] == "GENERATION_FAILED"
    assert "candidate_bundle" not in terminal["artifact_hashes"]


def test_aws_mode_refuses_to_spend_model_calls_without_video_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SOARM_MUJOCO_VIDEO_LAUNCHER", raising=False)

    with pytest.raises(PipelineError, match="requires run_pipeline_with_video"):
        run_pipeline(demo_root=_demo_root(tmp_path), mode="aws", run_id="no-video")


def test_strict_video_index_binds_every_video_to_its_source_artifact(
    tmp_path: Path,
) -> None:
    run = _run_stub(tmp_path)
    config = _video_run_config()
    report_path = _write_direct_report(run, 0, ["case/one"])
    validation_path = (
        run.root
        / "validation/videos/round_00"
        / f"{_safe_trace_name('case/one')}.mp4"
    )
    _write_test_video(validation_path)
    task_ids = [f"task/{index}" for index in range(6)]
    _write_demo_contract(run, task_ids)
    for task_id in task_ids:
        _write_test_video(
            run.root / "demo/videos" / f"{_safe_trace_name(task_id)}.mp4"
        )

    index = _write_video_index(
        paths=DemoPaths(ROOT),
        run=run,
        mode="aws",
        run_config=config,
        strict=True,
    )

    assert index["status"] == "complete"
    assert "preflight" not in index
    validation = index["validation"]
    assert len(validation) == 1
    assert validation[0]["case_id"] == "case/one"
    assert validation[0]["round"] == 0
    assert validation[0]["suite_sha256"] == "a" * 64
    assert validation[0]["package_sha256"] == "b" * 64
    assert validation[0]["direct_report"]["sha256"] == _sha256(report_path)
    assert validation[0]["encoded_duration_s"] > 0
    assert {record["task_id"] for record in index["demo"]} == set(task_ids)
    assert {record["package_sha256"] for record in index["demo"]} == {"c" * 64}
    assert {record["demo_freeze"]["path"] for record in index["demo"]} == {
        "demo/freeze.json"
    }
    assert {record["demo_report"]["path"] for record in index["demo"]} == {
        "demo/demo_report.json"
    }
    assert all(
        record["demo_freeze"]["sha256"]
        == _sha256(run.root / "demo/freeze.json")
        for record in index["demo"]
    )
    assert all(
        record["demo_report"]["sha256"]
        == _sha256(run.root / "demo/demo_report.json")
        for record in index["demo"]
    )


def test_formal_video_record_binds_sidecar_instance_and_resolver_freeze(
    tmp_path: Path,
) -> None:
    run = _run_stub(tmp_path)
    metadata_extra = _install_formal_video_scene_binding(run)
    video_path = run.root / "validation/videos/round_00/case.mp4"
    _write_test_video(
        video_path,
        metadata_extra=metadata_extra,
    )
    record = _video_record(run, video_path, phase="validation")
    assert record["resolved_scene_binding"] == metadata_extra[
        "resolved_scene_binding"
    ]
    assert record["generation_environment_freeze_sha256"] == run.input_hashes[
        "generation_environment_freeze"
    ]
    assert record["generation_environment_manifest_sha256"] == run.input_hashes[
        "generation_environment_manifest"
    ]

    metadata_path = video_path.with_suffix(".metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["resolved_scene_binding"]["scene_catalog"]["source_sha256"] = "2" * 64
    _write_json(metadata_path, metadata)
    with pytest.raises(PipelineError, match="does not match the environment freeze"):
        _video_record(run, video_path, phase="validation")


def test_formal_video_record_rejects_malformed_instance_binding_hash(
    tmp_path: Path,
) -> None:
    run = _run_stub(tmp_path)
    metadata_extra = _install_formal_video_scene_binding(run)
    metadata_extra["resolved_scene_binding"]["binding_sha256"] = "not-a-hash"
    video_path = run.root / "validation/videos/round_00/case.mp4"
    _write_test_video(video_path, metadata_extra=metadata_extra)

    with pytest.raises(PipelineError, match="binding_sha256 is invalid"):
        _video_record(run, video_path, phase="validation")


def test_validation_video_scene_binding_must_equal_direct_case_diagnostics(
    tmp_path: Path,
) -> None:
    run = _run_stub(tmp_path)
    report_binding = _synthetic_resolved_scene_binding("a")
    video_binding = _synthetic_resolved_scene_binding("b")
    _write_direct_report(
        run,
        0,
        ["case-one"],
        scene_binding_by_case={"case-one": report_binding},
    )
    video_path = run.root / "validation/videos/round_00/case-one.mp4"
    _write_test_video(
        video_path,
        metadata_extra={"resolved_scene_binding": video_binding},
    )

    with pytest.raises(PipelineError, match="does not match its direct report"):
        pipeline_module._validation_video_records(
            run,
            [video_path],
            strict=True,
        )


def test_demo_video_scene_binding_must_equal_task_oracle_report(
    tmp_path: Path,
) -> None:
    run = _run_stub(tmp_path)
    task_id = "task-one"
    report_binding = _synthetic_resolved_scene_binding("a")
    video_binding = _synthetic_resolved_scene_binding("b")
    _write_demo_contract(
        run,
        [task_id],
        scene_binding_by_task={task_id: report_binding},
    )
    video_path = run.root / "demo/videos/task-one.mp4"
    _write_test_video(
        video_path,
        metadata_extra={"resolved_scene_binding": video_binding},
    )

    with pytest.raises(PipelineError, match="does not match its oracle report"):
        pipeline_module._demo_video_records(
            run,
            [video_path],
            strict=False,
        )


def test_strict_video_index_rejects_cross_round_count_compensation(
    tmp_path: Path,
) -> None:
    run = _run_stub(tmp_path)
    _write_direct_report(run, 0, ["case_a", "case_b"])
    _write_direct_report(run, 1, ["case_c"])
    # Aggregate count is still three, but round 00 is missing case_b while
    # round 01 has an orphan. A count-only check would incorrectly accept it.
    for relative in (
        "validation/videos/round_00/case_a.mp4",
        "validation/videos/round_01/case_c.mp4",
        "validation/videos/round_01/orphan.mp4",
    ):
        path = run.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not reached because exact binding fails first")

    with pytest.raises(PipelineError, match="missing or orphan case IDs"):
        _write_video_index(
            paths=DemoPaths(ROOT),
            run=run,
            mode="aws",
            run_config=_video_run_config(),
            strict=True,
        )


def test_strict_video_index_rejects_unbound_demo_report_package(
    tmp_path: Path,
) -> None:
    run = _run_stub(tmp_path)
    _write_direct_report(run, 0, ["case"])
    _write_test_video(run.root / "validation/videos/round_00/case.mp4")
    task_ids = [f"task-{index}" for index in range(6)]
    _write_demo_contract(run, task_ids)
    for task_id in task_ids:
        _write_test_video(run.root / "demo/videos" / f"{task_id}.mp4")
    report_path = run.root / "demo/demo_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["package_sha256"] = "d" * 64
    _write_json(report_path, report)

    with pytest.raises(PipelineError, match="package hashes do not match"):
        _write_video_index(
            paths=DemoPaths(ROOT),
            run=run,
            mode="aws",
            run_config=_video_run_config(),
            strict=True,
        )


def test_strict_video_index_rejects_safe_filename_collisions(tmp_path: Path) -> None:
    run = _run_stub(tmp_path)
    _write_direct_report(run, 0, ["case/a", "case?a"])

    with pytest.raises(PipelineError, match="filename collision"):
        _write_video_index(
            paths=DemoPaths(ROOT),
            run=run,
            mode="aws",
            run_config=_video_run_config(),
            strict=True,
        )


def test_partial_video_index_keeps_complete_and_skips_incomplete_files(
    tmp_path: Path,
) -> None:
    run = _run_stub(tmp_path)
    _write_direct_report(run, 0, ["complete_case", "incomplete_case"])
    complete = run.root / "validation/videos/round_00/complete_case.mp4"
    _write_test_video(complete)
    incomplete = run.root / "validation/videos/round_00/incomplete_case.mp4"
    incomplete.parent.mkdir(parents=True, exist_ok=True)
    incomplete.write_bytes(b"unfinished encoder output")

    index = _write_video_index(
        paths=DemoPaths(ROOT),
        run=run,
        mode="aws",
        run_config=_video_run_config(),
        strict=False,
    )

    assert index["status"] == "partial"
    assert [record["case_id"] for record in index["validation"]] == [
        "complete_case"
    ]
    assert (run.root / "video_index.json").is_file()


def test_video_record_decodes_all_frames_and_rejects_sidecar_count(
    tmp_path: Path,
) -> None:
    run = _run_stub(tmp_path)
    path = run.root / "bad_count.mp4"
    _write_test_video(path, frames=2, metadata_frames=3, simulation_end_s=0.1)

    with pytest.raises(PipelineError, match="decoded frame count"):
        _video_record(run, path, phase="validation")


def test_video_record_rejects_decoder_fps_mismatch(tmp_path: Path) -> None:
    run = _run_stub(tmp_path)
    path = run.root / "bad_fps.mp4"
    _write_test_video(path, encoded_fps=20.0, metadata_fps=10.0)

    with pytest.raises(PipelineError, match="decoder fps"):
        _video_record(run, path, phase="validation")


def test_video_record_rejects_impossible_simulation_duration(tmp_path: Path) -> None:
    run = _run_stub(tmp_path)
    path = run.root / "bad_duration.mp4"
    _write_test_video(path, frames=4, simulation_end_s=0.0)

    with pytest.raises(PipelineError, match="impossible for its simulation interval"):
        _video_record(run, path, phase="validation")


def test_file_evidence_rejects_symlink_escape(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    escaped = run_root / "escaped.txt"
    escaped.symlink_to(outside)

    with pytest.raises(PipelineError, match="symlink escaped"):
        _file_evidence(run_root, escaped)


def test_aws_partial_video_index_before_formal_execution_is_empty(
    tmp_path: Path,
) -> None:
    run = _run_stub(tmp_path)
    index = _write_video_index(
        paths=DemoPaths(ROOT),
        run=run,
        mode="aws",
        run_config=_video_run_config(),
        strict=False,
    )

    assert "preflight" not in index
    assert index["validation"] == []
    assert index["demo"] == []
    assert not (run.root / "infrastructure/video_preflight.mp4").exists()
