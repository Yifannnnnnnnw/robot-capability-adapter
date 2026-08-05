from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from soarm_demo.audit import sha256_file, sha256_json
from soarm_demo.evolution import (
    BUNDLE_SCHEMA_VERSION,
    EvolutionLimits,
    build_candidate_bundle,
    write_candidate_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
EXPERIENCE_RECORDS = ROOT / "libraries/experience/v1/records.jsonl"
HASH_A = "a" * 64
HASH_B = "b" * 64


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _counter(name: str, limit: int, used: int) -> dict[str, Any]:
    return {"name": name, "limit": limit, "used": used, "remaining": limit - used}


def _phase_result(counter: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "completed",
        "agent_turns": counter["used"],
        "provider_http_attempts": 0,
        "provider_retries": 0,
        "agent_turn_budget": counter,
        "provider_attempt_budget_policy": {},
        "usage": {},
        "session_id": "test-generation-session",
        "episode_id": "test-generation-episode",
    }


def _repair_phase_result(round_number: int) -> dict[str, Any]:
    counter = _counter(f"generation_repair_round_{round_number:02d}", 6, 1)
    return {
        "round": round_number,
        "client_kind": "scripted_fixture",
        "scripted": True,
        **_phase_result(counter),
    }


def _file_ref(path: str, sha256: str = HASH_A, byte_count: int = 1) -> dict[str, Any]:
    return {"path": path, "sha256": sha256, "bytes": byte_count}


def _actual_ref(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _demo_task(index: int, *, passed: bool = True) -> dict[str, Any]:
    return {
        "ordinal": index + 1,
        "partition": "visible" if index < 3 else "pilot-held-out",
        "task_id": f"test_task_{index}",
        "agent_session_id": f"demo-session-{index}",
        "agent_episode_id": f"demo-episode-{index}",
        "model_calls": 1,
        "agent_turns": 1,
        "provider_http_attempts": 0,
        "provider_retries": 0,
        "client_kind": "scripted_fixture",
        "scripted": True,
        "agent_turn_budget": _counter(f"demo:task_{index}", 30, 1),
        "provider_attempt_budget_policy": {},
        "usage": {},
        "agent_status": "completed" if passed else "infrastructure_failed",
        "termination_reason": "agent_final" if passed else "infrastructure_failure",
        "agent_final": "",
        "oracle": {"passed": passed},
        "passed": passed,
        "trace_path": f"traces/task_{index}.jsonl",
    }


def _demo_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for partition in ("visible", "pilot-held-out"):
        selected = [item for item in tasks if item["partition"] == partition]
        passed = sum(item["passed"] is True for item in selected)
        result[partition] = {
            "passed": passed,
            "total": len(selected),
            "success_rate": passed / len(selected),
        }
    passed = sum(item["passed"] is True for item in tasks)
    result["overall"] = {
        "passed": passed,
        "total": len(tasks),
        "success_rate": passed / len(tasks),
    }
    return result


def _valid_demo_report(
    *,
    run_id: str = "evolution-success-run",
    tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    report_tasks = copy.deepcopy(tasks or [_demo_task(index) for index in range(6)])
    return {
        "schema_version": "robot_capability.demo_report.v1",
        "run_id": run_id,
        "catalog_sha256": HASH_A,
        "package_sha256": HASH_A,
        "demo_freeze_sha256": HASH_A,
        "tasks": report_tasks,
        "summary": _demo_summary(report_tasks),
        "sealed_at": "2026-08-05T12:00:00Z",
    }


def _write_bound_terminal(run_root: Path, *, terminal_state: str, run_id: str) -> None:
    evidence_paths = sorted(
        path
        for path in run_root.rglob("*.json")
        if path.name not in {"terminal_report.json", "run_manifest.json"}
        and "evolution" not in path.parts
    )
    report = {
        "schema_version": "robot_capability.terminal_run_report.v2",
        "run_id": run_id,
        "terminal_state": terminal_state,
        "terminal_reason": "test terminal state",
        "mode": "aws",
        "ended_at": "2026-08-05T12:00:00Z",
        "error": {"type": "TestFailure"},
        "input_hashes": {},
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
            "reason_code": "test_no_video",
        },
        "evidence_files": [_actual_ref(run_root, path) for path in evidence_paths],
    }
    report_path = run_root / "terminal_report.json"
    _write_json(report_path, report)
    _write_json(
        run_root / "run_manifest.json",
        {
            "run_id": run_id,
            "state": terminal_state,
            "artifact_hashes": {"terminal_report": sha256_file(report_path)},
            "events": [],
        },
    )


def _write_bound_sealed(
    run_root: Path,
    *,
    run_id: str = "evolution-success-run",
    demo_report: dict[str, Any] | None = None,
) -> None:
    feedback_paths = sorted((run_root / "validation").glob("feedback_*.json"))
    repair_paths = sorted((run_root / "validation").glob("repair_result_*.json"))
    static_paths = sorted((run_root / "validation").glob("static_round_*.json"))
    direct_paths = sorted((run_root / "validation").glob("direct_round_*.json"))
    history = [json.loads(path.read_text(encoding="utf-8")) for path in feedback_paths]
    repair_results = [
        _repair_phase_result(index)
        for index, _ in enumerate(repair_paths, start=1)
    ]
    repair_budget = {
        "round_limit": 10,
        "rounds_used": len(repair_results),
        "rounds_remaining": 10 - len(repair_results),
        "model_call_limit_per_round": 6,
        "results": repair_results,
    }
    stage1_counter = _counter("generation_stage1", 3, 3)
    stage2_counter = _counter("generation_stage2_initial", 30, 8)
    suite_counter = _counter("validation_suite", 3, 3)
    demo_counters = [_counter(f"demo:task_{index}", 30, 1) for index in range(6)]
    report_demo = copy.deepcopy(demo_report or _valid_demo_report(run_id=run_id))
    demo_summary = report_demo["summary"]
    demo_tasks = report_demo["tasks"]
    demo_report_path = run_root / "demo/demo_report.json"
    _write_json(demo_report_path, report_demo)
    required_input_names = [
        "library_manifest:morphology",
        "library_manifest:sdk_runtime",
        "library_manifest:tasks",
        "library_manifest:experience",
        "source_tree",
        "sdk_activation:manifest",
        "sdk_activation:api_surface",
        "sdk_activation:runtime_contract",
        "sdk_activation:api_probe",
        "task_demo_batch",
        "task_private_oracles",
        "task_visible_catalog",
        "task_pilot_heldout_catalog",
        *(f"extra_{index}" for index in range(6)),
    ]
    artifact_hashes = {
        "stage1": HASH_A,
        "validation_suite": HASH_A,
        "generated_package": HASH_A,
        "combined_tool_catalog": HASH_A,
        "public_api": HASH_A,
        "package_manifest": HASH_A,
        "demo_freeze": HASH_A,
        "demo_report": sha256_file(demo_report_path),
        "video_index": HASH_A,
        "sdk_activation": HASH_A,
    }
    static_refs = [_actual_ref(run_root, path) for path in static_paths] or [
        _file_ref("validation/static_round_00.json")
    ]
    direct_refs = [_actual_ref(run_root, path) for path in direct_paths] or [
        _file_ref("validation/direct_round_00.json")
    ]
    report = {
        "schema_version": "robot_capability.sealed_run_report.v2",
        "run_id": run_id,
        "terminal_state": "SEALED",
        "terminal_reason": "completed",
        "mode": "offline",
        "sealed_at": "2026-08-05T12:00:00Z",
        "run_configuration": {},
        "input_hashes": {name: HASH_A for name in required_input_names},
        "artifact_hashes": artifact_hashes,
        "evidence": {
            "configuration": {},
            "run_manifest_preseal": _file_ref("run_manifest_preseal.json"),
            "input_library_report": _file_ref("input_library_report.json"),
            "private_execution_preflight": _file_ref(
                "framework/private_execution_preflight.json"
            ),
            "stage1": [_file_ref("stage1/capabilities.json")],
            "generated_package": {},
            "public_api": _file_ref("validation/public_api_freeze.json"),
            "validation_suite": [_file_ref("validation/suite/suite.json")],
            "static_validation": static_refs,
            "direct_validation": direct_refs,
            "tool_catalogs": [_file_ref("tools/combined.json")],
            "demo": [
                _file_ref("demo/freeze.json"),
                _actual_ref(run_root, demo_report_path),
            ],
            "videos": [_file_ref("video_index.json")],
            "traces": [_file_ref(f"traces/trace_{index}.jsonl") for index in range(8)],
        },
        "budgets": {
            "generation_stage1": stage1_counter,
            "generation_stage2_initial": stage2_counter,
            "validation_suite": suite_counter,
            "demo": {
                "limit_per_task": 30,
                "used_per_task": [1] * 6,
                "per_task": demo_counters,
            },
            "repair": repair_budget,
        },
        "model_accounting": {
            "public_identities": {},
            "roles": {},
            "aggregate": {},
            "generation_phases": {
                "stage1": _phase_result(stage1_counter),
                "stage2_initial": _phase_result(stage2_counter),
                "repair_rounds": repair_results,
            },
        },
        "generation_agent": {},
        "repairs": history,
        "repair": {**repair_budget, "history": history},
        "validation": {
            "static_passed": True,
            "direct_function_passed": True,
            "suite_sha256": HASH_A,
            "public_api_sha256": HASH_A,
            "runtime": "test offline runtime",
        },
        "static_validation_passed": True,
        "direct_function_validation_passed": True,
        "validation_runtime": "test offline runtime",
        "demo": {"summary": demo_summary, "tasks": demo_tasks},
        "video_recording": {
            "schema_version": "robot_capability.mujoco_video_index.v1",
            "required": False,
            "status": "complete",
            "renderer_kind": "mujoco.Renderer",
            "codec": "mp4v",
            "fps": 20,
            "width": 640,
            "height": 480,
            "validation": [],
            "demo": [],
            "offline_reason": "deterministic test fixture",
        },
        "evidence_boundary": {},
        "sdk_activation": {
            "decision": {
                "schema_version": "robot_capability.sdk_activation.v2",
                "mode": "offline",
                "activation_scope": "offline_reference_only",
                "live_gate_satisfied": False,
                "probe_status": "pass",
                "target": {
                    "package": "lerobot",
                    "version": "0.6.0",
                    "commit": "a" * 40,
                },
                "runtime_id": "lerobot_soarm101_0_6_0",
                "verified": {
                    "canonical_import": {
                        "module": "lerobot.robots.so_follower",
                        "symbols": ["SO101Follower", "SO101FollowerConfig"],
                    },
                    "aliases": {
                        "SO101Follower": "SOFollower",
                        "SO101FollowerConfig": "SOFollowerRobotConfig",
                    },
                    "required_methods": [
                        "connect",
                        "disconnect",
                        "send_action",
                        "get_observation",
                    ],
                    "action_keys": [
                        "shoulder_pan.pos",
                        "shoulder_lift.pos",
                        "elbow_flex.pos",
                        "wrist_flex.pos",
                        "wrist_roll.pos",
                        "gripper.pos",
                    ],
                    "observation_keys": [
                        "shoulder_pan.pos",
                        "shoulder_lift.pos",
                        "elbow_flex.pos",
                        "wrist_flex.pos",
                        "wrist_roll.pos",
                        "gripper.pos",
                    ],
                    "units": {
                        "shoulder_pan.pos": "deg",
                        "shoulder_lift.pos": "deg",
                        "elbow_flex.pos": "deg",
                        "wrist_flex.pos": "deg",
                        "wrist_roll.pos": "deg",
                        "gripper.pos": "normalized_0_100",
                    },
                    "send_action_nonblocking": True,
                    "hardware_or_serial_opened": False,
                },
                "bindings": {
                    "manifest": {"path": "manifest.yaml", "sha256": HASH_A},
                    "api_surface": {"path": "api_surface.yaml", "sha256": HASH_A},
                    "runtime_contract": {
                        "path": "runtime_contract.yaml",
                        "sha256": HASH_A,
                    },
                    "api_probe": {"path": "api_probe.json", "sha256": HASH_A},
                },
            },
            "evidence": _file_ref("framework/sdk_activation.json"),
        },
    }
    report_path = run_root / "sealed_report.json"
    _write_json(report_path, report)
    events = [
        {
            "event": "repair_package_change_gate",
            "result_path": path.relative_to(run_root).as_posix(),
            "result_sha256": sha256_file(path),
        }
        for path in repair_paths
    ]
    _write_json(
        run_root / "run_manifest.json",
        {
            "run_id": run_id,
            "state": "SEALED",
            "artifact_hashes": {
                **artifact_hashes,
                "sealed_report": sha256_file(report_path),
            },
            "events": events,
        },
    )


def _feedback(*, stage: str = "direct_function", secret: str = "") -> dict[str, Any]:
    return {
        "schema_version": "robot_capability.failure_feedback.v1",
        "stage": stage,
        "repair_round": 1,
        "failures": [
            {
                "code": "DIRECT_ORACLE_MISMATCH"
                if stage == "direct_function"
                else "STATIC_CONTRACT_FAILED",
                "message": f"private message {secret}",
                "capability_id": "G3.pick_and_place",
                "case_id": f"private-case-{secret}",
                "observed": {"private_measurement": 123.456, "secret": secret},
                "target": {"private_threshold": 0.0006},
                "gap": {"private_gap": -0.999},
                "execution": {"private_return": secret},
                "diagnostics": {"private_diagnostic": secret},
                "traceback": f"private traceback {secret}",
            }
        ],
    }


def _repair_result(*, package_changed: bool = True) -> dict[str, Any]:
    feedback = None
    if not package_changed:
        feedback = _feedback()
        feedback["repair_round"] = 2
        feedback["no_package_change"] = {
            "code": "NO_PACKAGE_CHANGE",
            "completed_repair_round": 1,
            "consecutive_rounds": 1,
            "causal_feedback_repair_round": 1,
            "package_sha256": HASH_A,
            "retry_available": True,
        }
    return {
        "schema_version": "robot_capability.repair_package_gate_result.v2",
        "repair_round": 1,
        "status": "package_changed" if package_changed else "no_package_change",
        "code": "PACKAGE_CHANGED" if package_changed else "NO_PACKAGE_CHANGE",
        "package_before_sha256": HASH_A,
        "package_after_sha256": HASH_B if package_changed else HASH_A,
        "package_changed": package_changed,
        "validation_skipped": {
            "static": not package_changed,
            "direct": not package_changed,
            "video": not package_changed,
        },
        "next_repair_round": None if package_changed else 2,
        "feedback": feedback,
        "agent_accounting": _repair_phase_result(1),
        "repair_process": {
            "schema_version": "robot_capability.repair_process_audit.v1",
            "tool_call_count": 1,
            "successful_write_calls": 1,
            "protocol_error_count": 0,
            "truncated_response_count": 0,
            "provider_failure_count": 0,
            "final_rejection_count": 0,
            "last_action": "final",
            "unfinished_read": None,
            "tool_calls": [
                {
                    "ordinal": 1,
                    "agent_turn": 1,
                    "tool": "replace_generated_file_text",
                    "operation": "write",
                    "outcome": "ok",
                    "target": {"path": "generated_capability_package/g3.py"},
                    "arguments_sha256": HASH_A,
                    "result_sha256": HASH_B,
                }
            ],
            "privacy": {
                "raw_model_text_included": False,
                "raw_tool_arguments_included": False,
                "raw_tool_results_included": False,
                "raw_source_included": False,
            },
        },
    }


def _static_report(*, passed: bool = True) -> dict[str, Any]:
    failures = []
    if not passed:
        failures.append(
            {
                "code": "STATIC_CONTRACT_FAILED",
                "message": "contract mismatch",
                "path": "generated_package/capabilities.py",
                "line": 1,
                "column": 1,
                "capability_id": "G3.pick_and_place",
                "observed": "bad",
                "target": "good",
            }
        )
    return {
        "schema_version": "robot_capability.static_validation_report.v1",
        "passed": passed,
        "artifact_root": "generated_package",
        "stage1_sha256": HASH_A,
        "checked_files": ["package_manifest.json"],
        "failures": failures,
    }


def _direct_report(*, passed: bool, package_sha256: str = HASH_B) -> dict[str, Any]:
    case = {
        "case_id": "G3.pick_and_place.direct",
        "capability_id": "G3.pick_and_place",
        "function_name": "pick_and_place",
        "passed": passed,
        "elapsed_s": 0.1,
        "result": {},
        "observed_measurements": {},
        "target_measurements": {},
        "tolerances": {},
        "measurement_failures": [] if passed else ["oracle mismatch"],
        "forbidden_evidence": {},
        "framework_diagnostics": {},
        "exception_type": None,
        "exception_message": None,
        "traceback_lines": [],
        "timed_out": False,
    }
    return {
        "schema_version": "robot_capability.direct_validation_report.v1",
        "passed": passed,
        "suite_sha256": "c" * 64,
        "package_root": "generated_package",
        "package_sha256": package_sha256,
        "package_unchanged": True,
        "cases": [case],
        "summary": {"total": 1, "passed": int(passed), "failed": int(not passed)},
    }


def _terminal_report(*, terminal_state: str) -> dict[str, Any]:
    return {
        "schema_version": "robot_capability.terminal_run_report.v2",
        "run_id": "evolution-test-run",
        "terminal_state": terminal_state,
        "terminal_reason": "test terminal state",
        "mode": "aws",
        "ended_at": "2026-08-05T12:00:00Z",
    }


def _sealed_report() -> dict[str, Any]:
    return {
        "schema_version": "robot_capability.sealed_run_report.v2",
        "run_id": "evolution-success-run",
        "terminal_state": "SEALED",
        "terminal_reason": "completed",
        "mode": "aws",
        "sealed_at": "2026-08-05T12:00:00Z",
        "demo": {"overall": {"passed": 6, "total": 6, "success_rate": 1.0}},
    }


def _assert_schemas(bundle: dict[str, Any]) -> None:
    bundle_schema = json.loads(
        (SCHEMAS / "evolution_candidate_bundle.schema.json").read_text(encoding="utf-8")
    )
    experience_schema = json.loads(
        (SCHEMAS / "experience.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(bundle, bundle_schema)
    for candidate in bundle["candidates"]:
        jsonschema.validate(candidate["proposed_record"], experience_schema)
        assert candidate["proposed_record"]["status"] == "candidate"


def _detached_candidate_hash(candidate: dict[str, Any]) -> str:
    payload = copy.deepcopy(candidate)
    payload.pop("candidate_payload_sha256", None)
    return sha256_json(payload)


def test_failed_run_yields_redacted_schema_valid_negative_candidate(tmp_path: Path) -> None:
    secret = "PRIVATE_TASK_ORACLE_SENTINEL"
    _write_json(tmp_path / "validation/feedback_01.json", _feedback(secret=secret))
    _write_json(tmp_path / "validation/repair_result_01.json", _repair_result())
    _write_json(tmp_path / "validation/direct_round_01.json", _direct_report(passed=False))
    _write_bound_terminal(
        tmp_path,
        terminal_state="VALIDATION_FAILED",
        run_id="evolution-test-run",
    )

    output = write_candidate_bundle(tmp_path)
    bundle = json.loads(output.read_text(encoding="utf-8"))

    assert output == tmp_path / "evolution/candidate_bundle.json"
    assert bundle["schema_version"] == BUNDLE_SCHEMA_VERSION
    assert len(bundle["candidates"]) == 1
    candidate = bundle["candidates"][0]
    assert candidate["claim_kind"] == "failed_repair"
    assert candidate["proposed_record"]["outcome"]["repair_succeeded"] is False
    assert candidate["evidence_summary"]["causal_outcome"] == "confirmed_failure"
    assert candidate["promotion"]["status"] == "awaiting_explicit_approval"
    assert candidate["promotion"]["eligible"] is False
    assert candidate["promotion"]["gates"]["human_approval"]["satisfied"] is False
    process = candidate["proposed_record"]["evidence"]["after_measurements"][
        "repair_process"
    ]
    assert process["tool_sequence"] == [
        {
            "tool": "replace_generated_file_text",
            "operation": "write",
            "outcome": "ok",
        }
    ]
    assert process["raw_content_included"] is False
    serialized = json.dumps(bundle, ensure_ascii=False, sort_keys=True)
    assert secret not in serialized
    assert "private-case" not in serialized
    assert "123.456" not in serialized
    assert "private traceback" not in serialized
    _assert_schemas(bundle)


def test_transport_exhausted_no_change_repair_is_unresolved_not_negative_experience(
    tmp_path: Path,
) -> None:
    _write_json(tmp_path / "validation/feedback_01.json", _feedback())
    repair = _repair_result(package_changed=False)
    repair["agent_accounting"].update(
        {
            "status": "budget_exhausted",
            "agent_turns": 2,
            "provider_http_attempts": 2,
            "provider_retries": 0,
            "client_kind": "aws_model_api",
            "scripted": False,
            "agent_turn_budget": _counter("generation_repair_round_01", 2, 2),
            "usage": {"response_count": 0},
        }
    )
    _write_json(tmp_path / "validation/repair_result_01.json", repair)
    _write_bound_terminal(
        tmp_path,
        terminal_state="VALIDATION_FAILED",
        run_id="transport-exhausted-repair",
    )

    bundle = build_candidate_bundle(tmp_path)
    candidate = bundle["candidates"][0]

    assert candidate["claim_kind"] == "unresolved_failure"
    assert candidate["proposed_record"]["outcome"]["repair_succeeded"] is None
    assert "model_request_transport_failure" in candidate["proposed_record"][
        "outcome"
    ]["regressions"]
    assert candidate["proposed_record"]["evidence"]["after_measurements"][
        "failed_model_requests"
    ] == 2
    assert candidate["evidence_summary"]["causal_outcome"] == "unresolved"
    _assert_schemas(bundle)


def test_empty_and_successful_no_repair_runs_emit_no_fake_experience(tmp_path: Path) -> None:
    empty = build_candidate_bundle(tmp_path)
    assert empty["candidates"] == []
    assert empty["promotion"]["automatic_library_writeback"] is False

    _write_bound_sealed(tmp_path)
    first = build_candidate_bundle(tmp_path)
    second = build_candidate_bundle(tmp_path)

    assert first == second
    assert first["run"]["terminal_state"] == "SEALED"
    assert first["candidates"] == []
    _assert_schemas(first)


def test_successful_repair_remains_blocked_on_privacy_replay_and_human_gates(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "validation/feedback_01.json",
        _feedback(stage="static"),
    )
    _write_json(tmp_path / "validation/repair_result_01.json", _repair_result())
    _write_json(
        tmp_path / "validation/static_round_01.json",
        _static_report(passed=True),
    )
    _write_json(tmp_path / "validation/direct_round_01.json", _direct_report(passed=True))
    _write_bound_sealed(tmp_path)

    bundle = build_candidate_bundle(tmp_path)

    assert len(bundle["candidates"]) == 1
    candidate = bundle["candidates"][0]
    assert candidate["claim_kind"] == "successful_repair"
    assert candidate["proposed_record"]["outcome"]["repair_succeeded"] is True
    assert candidate["promotion"]["gates"]["schema"]["satisfied"] is True
    assert candidate["promotion"]["gates"]["evidence"]["satisfied"] is True
    assert candidate["promotion"]["gates"]["privacy"]["satisfied"] is False
    assert candidate["promotion"]["gates"]["replay"]["satisfied"] is False
    assert candidate["promotion"]["gates"]["human_approval"]["satisfied"] is False
    assert bundle["promotion"]["automatic_library_writeback"] is False
    assert bundle["promotion"]["all_gates_satisfied"] is False
    _assert_schemas(bundle)


def test_infrastructure_run_never_claims_a_successful_repair(tmp_path: Path) -> None:
    _write_json(tmp_path / "validation/feedback_01.json", _feedback())
    _write_json(tmp_path / "validation/repair_result_01.json", _repair_result())
    _write_json(tmp_path / "validation/direct_round_01.json", _direct_report(passed=True))
    tasks = [_demo_task(index) for index in range(6)]
    tasks[3] = _demo_task(3, passed=False)
    tasks[3]["task_id"] = "PRIVATE_HELDOUT_TASK"
    _write_json(
        tmp_path / "demo/demo_report.json",
        _valid_demo_report(run_id="evolution-infrastructure-run", tasks=tasks),
    )
    _write_bound_terminal(
        tmp_path,
        terminal_state="INFRASTRUCTURE_FAILED",
        run_id="evolution-infrastructure-run",
    )

    bundle = build_candidate_bundle(tmp_path)

    assert len(bundle["candidates"]) == 1
    candidate = bundle["candidates"][0]
    assert candidate["claim_kind"] == "unresolved_failure"
    assert candidate["proposed_record"]["outcome"]["repair_succeeded"] is None
    assert bundle["collection"]["candidate_omissions"]["demo_reason"] == (
        "infrastructure_demo_excluded"
    )
    assert "PRIVATE_HELDOUT_TASK" not in json.dumps(bundle, sort_keys=True)
    _assert_schemas(bundle)


def test_collection_is_bounded_and_never_copies_private_payloads(tmp_path: Path) -> None:
    secret = "BOUNDED_PRIVATE_SENTINEL"
    first = _feedback(secret=secret)
    first["failures"].extend(
        [
            {
                "code": "SECOND_PRIVATE_FAILURE",
                "message": secret * 2,
                "case_id": secret,
            },
            {
                "code": "THIRD_PRIVATE_FAILURE",
                "traceback": secret * 2,
            },
        ]
    )
    _write_json(tmp_path / "validation/feedback_01.json", first)
    (tmp_path / "validation/feedback_02.json").write_text(
        json.dumps({"secret": secret * 200}), encoding="utf-8"
    )
    _write_json(tmp_path / "validation/feedback_03.json", _feedback(secret=secret))
    limits = EvolutionLimits(
        max_files_per_kind=2,
        max_json_bytes=1024,
        max_failures_per_feedback=1,
        max_candidates=1,
        max_demo_tasks=1,
    )

    bundle = build_candidate_bundle(tmp_path, limits=limits)

    assert bundle["collection"]["discovered_by_kind"]["feedback"] == 3
    assert bundle["collection"]["included_by_kind"]["feedback"] == 2
    assert bundle["collection"]["omitted_by_limit"]["feedback"] == 1
    assert bundle["collection"]["invalid_or_oversized"] == 1
    assert bundle["collection"]["unbound"] == 1
    assert len(bundle["candidates"]) <= 1
    assert secret not in json.dumps(bundle, ensure_ascii=False, sort_keys=True)
    _assert_schemas(bundle)


def test_writer_never_mutates_the_committed_experience_library(tmp_path: Path) -> None:
    before = EXPERIENCE_RECORDS.read_bytes()

    output = write_candidate_bundle(tmp_path)

    assert output.is_file()
    assert EXPERIENCE_RECORDS.read_bytes() == before
    assert list(tmp_path.iterdir()) == [tmp_path / "evolution"]
    assert not (tmp_path / "libraries").exists()


def test_candidate_identity_is_run_specific_but_dedupe_key_is_cross_run(
    tmp_path: Path,
) -> None:
    bundles: list[dict[str, Any]] = []
    for suffix in ("one", "two"):
        run_root = tmp_path / suffix
        _write_json(run_root / "validation/feedback_01.json", _feedback())
        _write_json(run_root / "validation/repair_result_01.json", _repair_result())
        _write_json(
            run_root / "validation/direct_round_01.json",
            _direct_report(passed=False),
        )
        _write_bound_terminal(
            run_root,
            terminal_state="VALIDATION_FAILED",
            run_id=f"independent-run-{suffix}",
        )
        bundles.append(build_candidate_bundle(run_root))

    first = bundles[0]["candidates"][0]
    second = bundles[1]["candidates"][0]
    assert first["candidate_id"] != second["candidate_id"]
    assert first["dedupe_key"] == second["dedupe_key"]


def test_schema_invalid_sealed_source_cannot_claim_success(tmp_path: Path) -> None:
    _write_json(tmp_path / "validation/feedback_01.json", _feedback())
    _write_json(tmp_path / "validation/repair_result_01.json", _repair_result())
    _write_json(tmp_path / "validation/direct_round_01.json", _direct_report(passed=True))
    _write_json(tmp_path / "sealed_report.json", _sealed_report())

    bundle = build_candidate_bundle(tmp_path)

    assert bundle["run"]["source_report_verified"] is False
    assert "sealed_report_schema_invalid" in bundle["run"]["source_report_issues"]
    assert bundle["candidates"] == []
    assert bundle["promotion"]["all_gates_satisfied"] is False


def test_suite_generation_terminal_without_after_binding_stays_unresolved(
    tmp_path: Path,
) -> None:
    _write_json(tmp_path / "validation/feedback_01.json", _feedback())
    _write_json(tmp_path / "validation/repair_result_01.json", _repair_result())
    _write_bound_terminal(
        tmp_path,
        terminal_state="VALIDATION_SUITE_GENERATION_FAILED",
        run_id="suite-generation-failed",
    )

    bundle = build_candidate_bundle(tmp_path)
    candidate = bundle["candidates"][0]

    assert bundle["run"]["source_report_verified"] is True
    assert candidate["claim_kind"] == "unresolved_failure"
    assert candidate["evidence_summary"]["causal_outcome"] == "unresolved"
    assert candidate["promotion"]["gates"]["evidence"]["satisfied"] is False


def test_candidate_id_binds_conclusion_and_exact_after_evidence(tmp_path: Path) -> None:
    _write_json(tmp_path / "validation/feedback_01.json", _feedback())
    _write_json(tmp_path / "validation/repair_result_01.json", _repair_result())
    direct_path = tmp_path / "validation/direct_round_01.json"
    _write_json(direct_path, _direct_report(passed=False))
    _write_bound_terminal(
        tmp_path,
        terminal_state="VALIDATION_FAILED",
        run_id="candidate-evidence-identity",
    )
    first = build_candidate_bundle(tmp_path)["candidates"][0]

    _write_json(direct_path, _direct_report(passed=True))
    _write_bound_terminal(
        tmp_path,
        terminal_state="VALIDATION_FAILED",
        run_id="candidate-evidence-identity",
    )
    second = build_candidate_bundle(tmp_path)["candidates"][0]

    assert first["claim_kind"] == "failed_repair"
    assert second["claim_kind"] == "unresolved_failure"
    assert first["candidate_id"] != second["candidate_id"]


def test_candidate_payload_hash_binds_complete_review_payload(tmp_path: Path) -> None:
    _write_json(tmp_path / "validation/feedback_01.json", _feedback())
    _write_json(tmp_path / "validation/repair_result_01.json", _repair_result())
    _write_json(
        tmp_path / "validation/direct_round_01.json",
        _direct_report(passed=False),
    )
    _write_bound_terminal(
        tmp_path,
        terminal_state="VALIDATION_FAILED",
        run_id="candidate-payload-hash",
    )
    candidate = build_candidate_bundle(tmp_path)["candidates"][0]

    expected = candidate["candidate_payload_sha256"]
    assert expected == _detached_candidate_hash(candidate)
    mutations = (
        lambda value: value["proposed_record"]["recommended_change"].__setitem__(
            "guidance", "changed review guidance"
        ),
        lambda value: value.__setitem__("dedupe_key", "0" * 64),
        lambda value: value["evidence_summary"].__setitem__("failure_count", 2),
        lambda value: value["promotion"]["gates"]["human_approval"].__setitem__(
            "reason", "changed approval wording"
        ),
    )
    for mutate in mutations:
        changed = copy.deepcopy(candidate)
        mutate(changed)
        assert changed["candidate_id"] == candidate["candidate_id"]
        assert _detached_candidate_hash(changed) != expected


def test_post_terminal_causal_artifact_tamper_is_not_consumed(tmp_path: Path) -> None:
    _write_json(tmp_path / "validation/feedback_01.json", _feedback())
    _write_json(tmp_path / "validation/repair_result_01.json", _repair_result())
    direct_path = tmp_path / "validation/direct_round_01.json"
    _write_json(direct_path, _direct_report(passed=False))
    _write_bound_terminal(
        tmp_path,
        terminal_state="VALIDATION_FAILED",
        run_id="causal-tamper-run",
    )

    _write_json(direct_path, _direct_report(passed=True))
    bundle = build_candidate_bundle(tmp_path)
    candidate = bundle["candidates"][0]
    direct_artifact = next(
        item
        for item in bundle["collection"]["artifacts"]
        if item["path"] == "validation/direct_round_01.json"
    )

    assert bundle["run"]["source_report_verified"] is True
    assert direct_artifact["status"] == "unbound"
    assert candidate["claim_kind"] == "unresolved_failure"
    assert candidate["evidence_summary"]["after_reports"] == []
    assert candidate["promotion"]["gates"]["evidence"]["satisfied"] is False


@pytest.mark.parametrize(
    ("variant", "artifact_path"),
    (
        ("static_passed_with_failures", "validation/static_round_01.json"),
        ("direct_passed_with_mutated_package", "validation/direct_round_01.json"),
        ("direct_summary_mismatch", "validation/direct_round_01.json"),
        ("repair_hash_change_mismatch", "validation/repair_result_01.json"),
    ),
)
def test_manifest_bound_semantic_contradiction_cannot_confirm_repair(
    tmp_path: Path,
    variant: str,
    artifact_path: str,
) -> None:
    feedback = _feedback(stage="static")
    repair = _repair_result()
    static = _static_report(passed=True)
    direct = _direct_report(passed=True)
    if variant == "static_passed_with_failures":
        static["failures"] = _static_report(passed=False)["failures"]
    elif variant == "direct_passed_with_mutated_package":
        direct["package_unchanged"] = False
    elif variant == "direct_summary_mismatch":
        direct["summary"] = {"total": 2, "passed": 2, "failed": 0}
    elif variant == "repair_hash_change_mismatch":
        repair["package_after_sha256"] = HASH_A
    else:  # pragma: no cover - protects the parametrized fixture itself.
        raise AssertionError(variant)

    _write_json(tmp_path / "validation/feedback_01.json", feedback)
    _write_json(tmp_path / "validation/repair_result_01.json", repair)
    _write_json(tmp_path / "validation/static_round_01.json", static)
    _write_json(tmp_path / "validation/direct_round_01.json", direct)
    _write_bound_sealed(tmp_path)

    bundle = build_candidate_bundle(tmp_path)
    artifact = next(
        item
        for item in bundle["collection"]["artifacts"]
        if item["path"] == artifact_path
    )
    candidate = bundle["candidates"][0]

    assert bundle["run"]["source_report_verified"] is True
    assert artifact["status"] == "semantic_invalid"
    assert artifact["hash_bound"] is True
    assert artifact["semantic_valid"] is False
    assert candidate["claim_kind"] == "unresolved_failure"
    assert candidate["evidence_summary"]["causal_outcome"] == "unresolved"
    assert candidate["promotion"]["gates"]["evidence"]["satisfied"] is False
    _assert_schemas(bundle)


def test_manifest_bound_feedback_must_satisfy_failure_feedback_schema(
    tmp_path: Path,
) -> None:
    invalid_feedback = _feedback(stage="static")
    invalid_feedback["failures"][0].pop("message")
    _write_json(tmp_path / "validation/feedback_01.json", invalid_feedback)
    _write_json(tmp_path / "validation/repair_result_01.json", _repair_result())
    _write_json(tmp_path / "validation/static_round_01.json", _static_report())
    _write_json(tmp_path / "validation/direct_round_01.json", _direct_report(passed=True))
    _write_bound_sealed(tmp_path)

    bundle = build_candidate_bundle(tmp_path)
    feedback_artifact = next(
        item
        for item in bundle["collection"]["artifacts"]
        if item["path"] == "validation/feedback_01.json"
    )

    assert bundle["run"]["source_report_verified"] is True
    assert feedback_artifact["status"] == "semantic_invalid"
    assert feedback_artifact["hash_bound"] is True
    assert feedback_artifact["semantic_valid"] is False
    assert bundle["candidates"] == []
    _assert_schemas(bundle)


def test_manifest_bound_demo_semantic_mismatch_cannot_confirm_success(
    tmp_path: Path,
) -> None:
    _write_json(tmp_path / "validation/feedback_01.json", _feedback(stage="static"))
    _write_json(tmp_path / "validation/repair_result_01.json", _repair_result())
    _write_json(tmp_path / "validation/static_round_01.json", _static_report())
    _write_json(tmp_path / "validation/direct_round_01.json", _direct_report(passed=True))
    invalid_demo = _valid_demo_report()
    invalid_demo["tasks"][1]["task_id"] = invalid_demo["tasks"][0]["task_id"]
    _write_bound_sealed(tmp_path, demo_report=invalid_demo)

    bundle = build_candidate_bundle(tmp_path)
    demo_artifact = next(
        item
        for item in bundle["collection"]["artifacts"]
        if item["path"] == "demo/demo_report.json"
    )
    candidate = bundle["candidates"][0]

    assert bundle["run"]["source_report_verified"] is True
    assert demo_artifact["status"] == "semantic_invalid"
    assert demo_artifact["hash_bound"] is True
    assert demo_artifact["semantic_valid"] is False
    assert candidate["claim_kind"] == "unresolved_failure"
    assert candidate["evidence_summary"]["sealed_demo_passed"] is False
    assert candidate["promotion"]["gates"]["evidence"]["satisfied"] is False
    _assert_schemas(bundle)
