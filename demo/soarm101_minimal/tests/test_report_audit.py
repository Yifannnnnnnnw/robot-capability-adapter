from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from soarm_demo.audit import atomic_write_json, sha256_file, sha256_json
from soarm_demo.report_audit import (
    budget_semantic_errors,
    demo_artifact_semantic_errors,
    demo_report_structure_semantic_errors,
    demo_success_semantic_errors,
    sealed_report_semantic_errors,
    terminal_report_semantic_errors,
    terminal_state_semantic_errors,
    terminal_video_semantic_errors,
    video_semantic_errors,
)
from soarm_demo.sdk_activation import evaluate_sdk_activation, freeze_sdk_activation
from soarm_demo.tool_packager import package_tree_sha256


def _counter(name: str, limit: int, used: int) -> dict[str, Any]:
    return {"name": name, "limit": limit, "used": used, "remaining": limit - used}


def _repair_result(round_number: int, *, used: int = 2) -> dict[str, Any]:
    return {
        "round": round_number,
        "status": "completed",
        "agent_turns": used,
        "agent_turn_budget": _counter(
            f"generation_repair_round_{round_number:02d}", 6, used
        ),
    }


def _file_evidence(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _write_test_video(
    root: Path,
    relative: str,
    *,
    frames: int = 2,
    width: int = 160,
    height: int = 120,
    fps: float = 20.0,
    metadata_extra: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
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
    metadata_path = path.with_suffix(".metadata.json")
    metadata = {
        "schema_version": "robot_capability.mujoco_video_evidence.v1",
        "video_path": path.name,
        "renderer_kind": "mujoco.Renderer",
        "codec": "mp4v",
        "fps": fps,
        "frames": frames,
        "width": width,
        "height": height,
        "simulation_start_s": 0.0,
        "simulation_end_s": max(0.0, (frames - 2) / fps),
    }
    if metadata_extra:
        metadata.update(copy.deepcopy(metadata_extra))
    atomic_write_json(metadata_path, metadata)
    return _file_evidence(root, relative), _file_evidence(
        root, metadata_path.relative_to(root).as_posix()
    )


def _report(tmp_path: Path) -> dict[str, Any]:
    run_id = "semantic-audit-run"
    catalog_sha256 = "c" * 64
    index = {
        "schema_version": "robot_capability.mujoco_video_index.v1",
        "required": False,
        "status": "complete",
        "renderer_kind": "mujoco.Renderer",
        "codec": "mp4v",
        "fps": 20.0,
        "width": 640,
        "height": 480,
        "validation": [],
        "demo": [],
        "offline_reason": "deterministic fixture",
    }
    index_path = tmp_path / "video_index.json"
    atomic_write_json(index_path, index)
    index_evidence = {
        "path": "video_index.json",
        "sha256": sha256_file(index_path),
        "bytes": index_path.stat().st_size,
    }
    stage1 = _counter("generation_stage1", 3, 3)
    stage2 = _counter("generation_stage2_initial", 30, 7)
    per_task = [_counter(f"demo:task_{index}", 30, 2) for index in range(6)]
    support_path = (
        tmp_path
        / "generated_package/generated_capability_package/_kinematics.py"
    )
    support_path.parent.mkdir(parents=True, exist_ok=True)
    support_path.write_text("SUPPORT_MODULE = True\n", encoding="utf-8")
    package_sha256 = package_tree_sha256(tmp_path / "generated_package")
    demo_tasks = [
        {
            "ordinal": index + 1,
            "partition": "visible" if index < 3 else "pilot-held-out",
            "task_id": f"task_{index}",
            "model_calls": counter["used"],
            "agent_turns": counter["used"],
            "agent_turn_budget": dict(counter),
            "passed": True,
            "oracle": {"passed": True},
        }
        for index, counter in enumerate(per_task)
    ]
    demo_summary = {
        "visible": {"passed": 3, "total": 3, "success_rate": 1.0},
        "pilot-held-out": {"passed": 3, "total": 3, "success_rate": 1.0},
        "overall": {"passed": 6, "total": 6, "success_rate": 1.0},
    }
    freeze_path = tmp_path / "demo/freeze.json"
    atomic_write_json(
        freeze_path,
        {
            "run_id": run_id,
            "catalog_sha256": catalog_sha256,
            "package_sha256": package_sha256,
            "tasks": [
                {
                    "ordinal": task["ordinal"],
                    "partition": task["partition"],
                    "task_id": task["task_id"],
                }
                for task in demo_tasks
            ],
        },
    )
    demo_report_path = tmp_path / "demo/demo_report.json"
    atomic_write_json(
        demo_report_path,
        {
            "run_id": run_id,
            "catalog_sha256": catalog_sha256,
            "package_sha256": package_sha256,
            "demo_freeze_sha256": sha256_file(freeze_path),
            "tasks": demo_tasks,
            "summary": demo_summary,
        },
    )
    freeze_evidence = _file_evidence(tmp_path, "demo/freeze.json")
    demo_report_evidence = _file_evidence(tmp_path, "demo/demo_report.json")
    support_evidence = _file_evidence(
        tmp_path,
        "generated_package/generated_capability_package/_kinematics.py",
    )
    return {
        "budgets": {
            "generation_stage1": stage1,
            "generation_stage2_initial": stage2,
            "validation_suite": _counter("validation_suite", 3, 3),
            "repair": {
                "round_limit": 10,
                "rounds_used": 0,
                "rounds_remaining": 10,
                "model_call_limit_per_round": 6,
                "results": [],
            },
            "demo": {
                "limit_per_task": 30,
                "used_per_task": [2] * 6,
                "per_task": per_task,
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
        "model_accounting": {
            "generation_phases": {
                "stage1": {
                    "agent_turns": stage1["used"],
                    "agent_turn_budget": dict(stage1),
                },
                "stage2_initial": {
                    "agent_turns": stage2["used"],
                    "agent_turn_budget": dict(stage2),
                },
                "repair_rounds": [],
            }
        },
        "generation_agent": {
            "stage1_model_calls": stage1["used"],
            "stage2_initial_budget": dict(stage2),
            "repair_rounds": 0,
            "repair_results": [],
        },
        "repairs": [],
        "demo": {"summary": demo_summary, "tasks": demo_tasks},
        "artifact_hashes": {
            "video_index": index_evidence["sha256"],
            "generated_package": package_sha256,
            "demo_freeze": freeze_evidence["sha256"],
            "demo_report": demo_report_evidence["sha256"],
        },
        "video_recording": index,
        "evidence": {
            "videos": [index_evidence],
            "demo": [freeze_evidence, demo_report_evidence],
            "generated_package": {
                "tree_sha256": package_sha256,
                "files": [support_evidence],
            },
        },
    }


def _validation_video_report(tmp_path: Path) -> dict[str, Any]:
    report = _report(tmp_path)
    suite_sha256 = "a" * 64
    package_sha256 = "b" * 64
    direct_path = tmp_path / "validation/direct_round_00.json"
    atomic_write_json(
        direct_path,
        {
            "schema_version": "robot_capability.direct_validation_report.v1",
            "suite_sha256": suite_sha256,
            "package_sha256": package_sha256,
            "cases": [{"case_id": "case_a"}],
            "summary": {"total": 1, "passed": 1, "failed": 0},
        },
    )
    direct_evidence = _file_evidence(
        tmp_path, "validation/direct_round_00.json"
    )
    video_evidence, metadata_evidence = _write_test_video(
        tmp_path, "validation/videos/round_00/case_a.mp4"
    )
    index = copy.deepcopy(report["video_recording"])
    index.update(
        required=True,
        status="complete",
        width=160,
        height=120,
        offline_reason=None,
    )
    index["validation"] = [
        {
            "phase": "validation",
            "id": "case_a",
            "video": video_evidence,
            "metadata": metadata_evidence,
            "renderer_kind": "mujoco.Renderer",
            "codec": "mp4v",
            "fps": 20.0,
            "frames": 2,
            "width": 160,
            "height": 120,
            "simulation_start_s": 0.0,
            "simulation_end_s": 0.0,
            "encoded_duration_s": 0.1,
            "round": 0,
            "case_id": "case_a",
            "suite_sha256": suite_sha256,
            "package_sha256": package_sha256,
            "direct_report": direct_evidence,
        }
    ]
    atomic_write_json(tmp_path / "video_index.json", index)
    index_evidence = _file_evidence(tmp_path, "video_index.json")
    report["video_recording"] = index
    report["artifact_hashes"]["video_index"] = index_evidence["sha256"]
    report["artifact_hashes"]["validation_suite"] = suite_sha256
    report["validation"] = {"suite_sha256": suite_sha256}
    report["evidence"]["videos"] = [
        index_evidence,
        video_evidence,
        metadata_evidence,
    ]
    report["evidence"]["direct_validation"] = [direct_evidence]
    return report


def _add_repair_round(report: dict[str, Any], *, used: int = 2) -> None:
    result = _repair_result(1, used=used)
    history = [{"repair_round": 1, "stage": "static", "failures": []}]
    repair_budget = {
        "round_limit": 10,
        "rounds_used": 1,
        "rounds_remaining": 9,
        "model_call_limit_per_round": 6,
        "results": [copy.deepcopy(result)],
    }
    report["budgets"]["repair"] = copy.deepcopy(repair_budget)
    report["repair"] = {**copy.deepcopy(repair_budget), "history": copy.deepcopy(history)}
    report["repairs"] = copy.deepcopy(history)
    report["model_accounting"]["generation_phases"]["repair_rounds"] = [
        copy.deepcopy(result)
    ]
    report["generation_agent"]["repair_rounds"] = 1
    report["generation_agent"]["repair_results"] = [copy.deepcopy(result)]


def test_semantic_audit_accepts_bound_budget_and_video_evidence(tmp_path: Path) -> None:
    report = _report(tmp_path)
    source_root = Path(__file__).resolve().parents[1]
    report["input_hashes"] = {"source_tree": "a" * 64}
    sdk_decision = evaluate_sdk_activation(
        sdk_root=source_root / "libraries/sdk_runtime/lerobot_soarm101/0.6.0",
        schemas_root=source_root / "schemas",
        mode="offline",
    )
    sdk_activation = freeze_sdk_activation(
        sdk_root=source_root / "libraries/sdk_runtime/lerobot_soarm101/0.6.0",
        run_root=tmp_path,
        decision=sdk_decision,
        schema_path=source_root / "schemas/sdk_activation.schema.json",
    )
    report["sdk_activation"] = sdk_activation
    report["artifact_hashes"]["sdk_activation"] = sdk_activation["evidence"][
        "sha256"
    ]
    report["input_hashes"].update(
        {
            f"sdk_activation:{name}": binding["sha256"]
            for name, binding in sdk_activation["decision"]["bindings"].items()
        }
    )

    assert sealed_report_semantic_errors(report, run_root=tmp_path) == ()


def test_demo_success_semantics_require_task_oracle_and_exact_summary(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)
    demo = copy.deepcopy(report["demo"])
    demo["tasks"][0]["passed"] = False
    demo["tasks"][1]["oracle"]["passed"] = False
    demo["summary"]["visible"]["passed"] = 2
    demo["summary"]["overall"]["success_rate"] = 5 / 6

    errors = "\n".join(demo_success_semantic_errors(demo))

    assert "tasks[0].passed" in errors
    assert "tasks[1].oracle.passed" in errors
    assert "summary.visible.passed" in errors
    assert "summary.overall.success_rate" in errors


def test_demo_structure_distinguishes_consistent_oracle_miss_from_malformed_report(
    tmp_path: Path,
) -> None:
    demo = copy.deepcopy(_report(tmp_path)["demo"])
    demo["tasks"][0]["passed"] = False
    demo["tasks"][0]["oracle"]["passed"] = False
    demo["summary"]["visible"].update(passed=2, success_rate=2 / 3)
    demo["summary"]["overall"].update(passed=5, success_rate=5 / 6)

    assert demo_report_structure_semantic_errors(demo) == ()

    malformed = copy.deepcopy(demo)
    malformed["tasks"][1]["task_id"] = malformed["tasks"][0]["task_id"]
    malformed["tasks"][2]["oracle"]["passed"] = False
    malformed["summary"]["overall"]["passed"] = 6
    errors = "\n".join(demo_report_structure_semantic_errors(malformed))
    assert "task IDs must be unique" in errors
    assert "passed must equal oracle.passed" in errors
    assert "task-derived 5" in errors


def test_demo_artifact_semantics_rehash_package_freeze_report_and_support_module(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)
    support_path = (
        tmp_path
        / "generated_package/generated_capability_package/_kinematics.py"
    )
    support_path.write_text("SUPPORT_MODULE = False\n", encoding="utf-8")

    errors = "\n".join(
        demo_artifact_semantic_errors(report, run_root=tmp_path)
    )

    assert "actual generated package tree" in errors
    assert "package hash must equal the actual package tree" in errors
    assert "evidence.generated_package.tree_sha256" in errors
    assert "_kinematics.py" in errors


def test_video_semantics_rejects_undecodable_mp4_and_unreadable_sidecar(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)
    freeze_evidence = report["evidence"]["demo"][0]
    demo_report_evidence = report["evidence"]["demo"][1]
    video_path = tmp_path / "demo/videos/task_0.mp4"
    metadata_path = tmp_path / "demo/videos/task_0.metadata.json"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")
    metadata_path.write_bytes(b"metadata")
    video_evidence = _file_evidence(tmp_path, "demo/videos/task_0.mp4")
    metadata_evidence = _file_evidence(
        tmp_path, "demo/videos/task_0.metadata.json"
    )
    index = copy.deepcopy(report["video_recording"])
    index["status"] = "partial"
    index["demo"] = [
        {
            "task_id": "task_0",
            "video": video_evidence,
            "metadata": metadata_evidence,
            "package_sha256": report["artifact_hashes"]["generated_package"],
            "demo_freeze": freeze_evidence,
            "demo_report": demo_report_evidence,
        }
    ]
    atomic_write_json(tmp_path / "video_index.json", index)
    index_evidence = _file_evidence(tmp_path, "video_index.json")
    report["video_recording"] = index
    report["artifact_hashes"]["video_index"] = index_evidence["sha256"]
    report["evidence"]["videos"] = [
        index_evidence,
        video_evidence,
        metadata_evidence,
    ]

    errors = "\n".join(video_semantic_errors(report, run_root=tmp_path))
    assert "MP4 cannot be opened by the decoder" in errors
    assert "metadata sidecar could not be read as JSON" in errors
    terminal = {
        "artifact_hashes": {"video_index": index_evidence["sha256"]},
        "video_recording": copy.deepcopy(index),
        "evidence_files": [index_evidence, video_evidence, metadata_evidence],
    }
    terminal_errors = "\n".join(
        terminal_video_semantic_errors(terminal, run_root=tmp_path)
    )
    assert "MP4 cannot be opened by the decoder" in terminal_errors
    assert "metadata sidecar could not be read as JSON" in terminal_errors

    report["video_recording"]["demo"][0]["package_sha256"] = "0" * 64
    report["video_recording"]["demo"][0]["demo_freeze"]["sha256"] = "0" * 64
    atomic_write_json(tmp_path / "video_index.json", report["video_recording"])
    index_evidence = _file_evidence(tmp_path, "video_index.json")
    report["artifact_hashes"]["video_index"] = index_evidence["sha256"]
    report["evidence"]["videos"][0] = index_evidence

    errors = "\n".join(video_semantic_errors(report, run_root=tmp_path))
    assert "package_sha256" in errors
    assert "demo_freeze" in errors


def test_video_semantics_fully_decodes_valid_mp4_and_sidecar(tmp_path: Path) -> None:
    report = _report(tmp_path)
    freeze_evidence = report["evidence"]["demo"][0]
    demo_report_evidence = report["evidence"]["demo"][1]
    video_evidence, metadata_evidence = _write_test_video(
        tmp_path, "demo/videos/task_0.mp4"
    )
    index = copy.deepcopy(report["video_recording"])
    index.update(
        required=True,
        status="partial",
        width=160,
        height=120,
        offline_reason=None,
    )
    index["demo"] = [
        {
            "phase": "demo",
            "id": "task_0",
            "video": video_evidence,
            "metadata": metadata_evidence,
            "renderer_kind": "mujoco.Renderer",
            "codec": "mp4v",
            "fps": 20.0,
            "frames": 2,
            "width": 160,
            "height": 120,
            "simulation_start_s": 0.0,
            "simulation_end_s": 0.0,
            "encoded_duration_s": 0.1,
            "task_id": "task_0",
            "package_sha256": report["artifact_hashes"]["generated_package"],
            "demo_freeze": freeze_evidence,
            "demo_report": demo_report_evidence,
        }
    ]
    atomic_write_json(tmp_path / "video_index.json", index)
    index_evidence = _file_evidence(tmp_path, "video_index.json")
    report["video_recording"] = index
    report["artifact_hashes"]["video_index"] = index_evidence["sha256"]
    report["evidence"]["videos"] = [
        index_evidence,
        video_evidence,
        metadata_evidence,
    ]

    assert video_semantic_errors(report, run_root=tmp_path) == ()


def test_demo_video_semantics_cross_checks_task_oracle_scene_binding(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)
    catalog = {
        "catalog_id": "soarm101_tabletop_primitives",
        "version": "1.0.0",
        "source_sha256": "d" * 64,
        "content_sha256": "1" * 64,
    }
    oracle_binding = {
        "binding_sha256": "a" * 64,
        "source_scene_request_sha256": "f" * 64,
        "scene_catalog": copy.deepcopy(catalog),
    }
    video_binding = {
        **copy.deepcopy(oracle_binding),
        "binding_sha256": "b" * 64,
    }
    demo_report_path = tmp_path / "demo/demo_report.json"
    demo_report = json.loads(demo_report_path.read_text(encoding="utf-8"))
    demo_report["tasks"][0]["oracle"]["resolved_scene_binding"] = oracle_binding
    atomic_write_json(demo_report_path, demo_report)
    demo_report_evidence = _file_evidence(tmp_path, "demo/demo_report.json")
    video_evidence, metadata_evidence = _write_test_video(
        tmp_path,
        "demo/videos/task_0.mp4",
        metadata_extra={"resolved_scene_binding": video_binding},
    )
    freeze_evidence = report["evidence"]["demo"][0]
    record = {
        "phase": "demo",
        "id": "task_0",
        "video": video_evidence,
        "metadata": metadata_evidence,
        "renderer_kind": "mujoco.Renderer",
        "codec": "mp4v",
        "fps": 20.0,
        "frames": 2,
        "width": 160,
        "height": 120,
        "simulation_start_s": 0.0,
        "simulation_end_s": 0.0,
        "encoded_duration_s": 0.1,
        "task_id": "task_0",
        "package_sha256": report["artifact_hashes"]["generated_package"],
        "demo_freeze": freeze_evidence,
        "demo_report": demo_report_evidence,
        "resolved_scene_binding": video_binding,
    }
    index = copy.deepcopy(report["video_recording"])
    index.update(
        required=True,
        status="partial",
        width=160,
        height=120,
        offline_reason=None,
    )
    index["demo"] = [record]
    atomic_write_json(tmp_path / "video_index.json", index)
    index_evidence = _file_evidence(tmp_path, "video_index.json")
    report["video_recording"] = index
    report["artifact_hashes"]["video_index"] = index_evidence["sha256"]
    report["evidence"]["demo"][1] = demo_report_evidence
    report["evidence"]["videos"] = [
        index_evidence,
        video_evidence,
        metadata_evidence,
    ]

    errors = "\n".join(video_semantic_errors(report, run_root=tmp_path))
    assert "resolved_scene_binding must equal its Demo oracle" in errors


def test_validation_video_semantics_rebuilds_direct_report_binding(
    tmp_path: Path,
) -> None:
    report = _validation_video_report(tmp_path)

    assert video_semantic_errors(report, run_root=tmp_path) == ()


def test_validation_video_semantics_cross_checks_direct_case_scene_binding(
    tmp_path: Path,
) -> None:
    report = _validation_video_report(tmp_path)
    catalog = {
        "catalog_id": "soarm101_tabletop_primitives",
        "version": "1.0.0",
        "source_sha256": "d" * 64,
        "content_sha256": "1" * 64,
    }
    direct_binding = {
        "binding_sha256": "a" * 64,
        "source_scene_request_sha256": "f" * 64,
        "scene_catalog": copy.deepcopy(catalog),
    }
    video_binding = {
        **copy.deepcopy(direct_binding),
        "binding_sha256": "b" * 64,
    }
    direct_path = tmp_path / "validation/direct_round_00.json"
    direct = json.loads(direct_path.read_text(encoding="utf-8"))
    direct["cases"][0]["framework_diagnostics"] = {
        "resolved_scene_binding": direct_binding
    }
    atomic_write_json(direct_path, direct)
    direct_evidence = _file_evidence(
        tmp_path,
        "validation/direct_round_00.json",
    )
    metadata_path = tmp_path / "validation/videos/round_00/case_a.metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["resolved_scene_binding"] = video_binding
    atomic_write_json(metadata_path, metadata)
    metadata_evidence = _file_evidence(
        tmp_path,
        "validation/videos/round_00/case_a.metadata.json",
    )
    index = copy.deepcopy(report["video_recording"])
    record = index["validation"][0]
    record["resolved_scene_binding"] = video_binding
    record["metadata"] = metadata_evidence
    record["direct_report"] = direct_evidence
    atomic_write_json(tmp_path / "video_index.json", index)
    index_evidence = _file_evidence(tmp_path, "video_index.json")
    report["video_recording"] = index
    report["artifact_hashes"]["video_index"] = index_evidence["sha256"]
    report["evidence"]["videos"] = [
        index_evidence,
        record["video"],
        metadata_evidence,
    ]
    report["evidence"]["direct_validation"] = [direct_evidence]

    errors = "\n".join(video_semantic_errors(report, run_root=tmp_path))
    assert "resolved_scene_binding must equal its direct report case" in errors


def test_validation_video_semantics_rejects_missing_case_and_hash_mismatches(
    tmp_path: Path,
) -> None:
    report = _validation_video_report(tmp_path)
    direct_path = tmp_path / "validation/direct_round_00.json"
    direct = json.loads(direct_path.read_text(encoding="utf-8"))
    direct["cases"].append({"case_id": "case_b"})
    direct["summary"] = {"total": 2, "passed": 2, "failed": 0}
    atomic_write_json(direct_path, direct)
    direct_evidence = _file_evidence(
        tmp_path, "validation/direct_round_00.json"
    )
    record = report["video_recording"]["validation"][0]
    record["suite_sha256"] = "c" * 64
    record["package_sha256"] = "d" * 64
    record["direct_report"] = {
        **direct_evidence,
        "sha256": "0" * 64,
    }
    report["evidence"]["direct_validation"] = [direct_evidence]
    atomic_write_json(tmp_path / "video_index.json", report["video_recording"])
    index_evidence = _file_evidence(tmp_path, "video_index.json")
    report["artifact_hashes"]["video_index"] = index_evidence["sha256"]
    report["evidence"]["videos"][0] = index_evidence

    errors = "\n".join(video_semantic_errors(report, run_root=tmp_path))
    assert "must exactly match direct-report case IDs" in errors
    assert "suite_sha256 must equal its direct report" in errors
    assert "package_sha256 must equal its direct report" in errors
    assert "direct_report must equal exact file evidence" in errors
    assert "direct_report.sha256 does not match the file" in errors


def test_budget_semantics_accepts_one_independent_repair_round(tmp_path: Path) -> None:
    report = _report(tmp_path)
    _add_repair_round(report, used=4)

    assert budget_semantic_errors(report) == ()


def test_budget_semantics_binds_phase_demo_and_repair_views(tmp_path: Path) -> None:
    report = _report(tmp_path)
    _add_repair_round(report)
    report["model_accounting"]["generation_phases"]["stage1"]["agent_turns"] = 2
    report["budgets"]["validation_suite"]["used"] = 2
    report["budgets"]["validation_suite"]["remaining"] = 1
    report["demo"]["tasks"][0]["model_calls"] = 9
    report["repair"]["history"][0]["repair_round"] = 2
    report["generation_agent"]["repair_rounds"] = 0

    errors = "\n".join(budget_semantic_errors(report))

    assert "Stage 1 agent_turns" in errors
    assert "exactly 3 agent turns" in errors
    assert "tasks[0].model_calls" in errors
    assert "history[0].repair_round" in errors
    assert "generation_agent.repair_rounds" in errors
    assert "top-level repairs" in errors


def test_budget_semantics_rejects_duplicate_demo_counter_names(tmp_path: Path) -> None:
    report = _report(tmp_path)
    duplicate = copy.deepcopy(report["budgets"]["demo"]["per_task"][0])
    report["budgets"]["demo"]["per_task"][1] = duplicate
    report["budgets"]["demo"]["used_per_task"][1] = duplicate["used"]
    report["demo"]["tasks"][1] = {
        **report["demo"]["tasks"][1],
        "task_id": "task_0",
        "model_calls": duplicate["used"],
        "agent_turns": duplicate["used"],
        "agent_turn_budget": copy.deepcopy(duplicate),
    }

    errors = "\n".join(budget_semantic_errors(report))

    assert "counter names must be unique" in errors


def test_budget_semantics_reject_arithmetic_and_cross_window_contradictions(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)
    report["budgets"]["generation_stage2_initial"]["used"] = 8
    report["budgets"]["demo"]["used_per_task"][0] = 29
    report["repair"]["rounds_used"] = 1

    errors = "\n".join(budget_semantic_errors(report))

    assert "used + remaining == 30" in errors
    assert "used_per_task[0]" in errors
    assert "repair rounds/results/history" in errors
    assert "Stage 2 phase budget" in errors


def _formal_validation_video_report(tmp_path: Path) -> dict[str, Any]:
    report = _validation_video_report(tmp_path)
    catalog_sha256 = "d" * 64
    manifest = {
        "bindings": {
            "morphology": {
                "scene_asset_catalog": {
                    "catalog_id": "soarm101_tabletop_primitives",
                    "version": "1.0.0",
                    "sha256": catalog_sha256,
                }
            }
        }
    }
    manifest_sha256 = sha256_json(manifest)
    freeze_path = tmp_path / "generation/environment_freeze.json"
    atomic_write_json(
        freeze_path,
        {"manifest": manifest, "manifest_sha256": manifest_sha256},
    )
    freeze_sha256 = sha256_file(freeze_path)
    binding = {
        "binding_sha256": "e" * 64,
        "source_scene_request_sha256": "f" * 64,
        "scene_catalog": {
            "catalog_id": "soarm101_tabletop_primitives",
            "version": "1.0.0",
            "source_sha256": catalog_sha256,
            "content_sha256": "1" * 64,
        },
    }
    direct_path = tmp_path / "validation/direct_round_00.json"
    direct = json.loads(direct_path.read_text(encoding="utf-8"))
    direct["cases"][0]["framework_diagnostics"] = {
        "resolved_scene_binding": copy.deepcopy(binding)
    }
    atomic_write_json(direct_path, direct)
    direct_evidence = _file_evidence(tmp_path, "validation/direct_round_00.json")
    metadata_path = tmp_path / "validation/videos/round_00/case_a.metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["resolved_scene_binding"] = copy.deepcopy(binding)
    atomic_write_json(metadata_path, metadata)
    metadata_evidence = _file_evidence(
        tmp_path, "validation/videos/round_00/case_a.metadata.json"
    )
    index = copy.deepcopy(report["video_recording"])
    record = index["validation"][0]
    record.update(
        {
            "metadata": metadata_evidence,
            "direct_report": direct_evidence,
            "resolved_scene_binding": copy.deepcopy(binding),
            "generation_environment_freeze_sha256": freeze_sha256,
            "generation_environment_manifest_sha256": manifest_sha256,
        }
    )
    atomic_write_json(tmp_path / "video_index.json", index)
    index_evidence = _file_evidence(tmp_path, "video_index.json")
    report.setdefault("input_hashes", {}).update(
        {
            "generation_environment_freeze": freeze_sha256,
            "generation_environment_manifest": manifest_sha256,
            "generation_environment_source:morphology:scene_asset_catalog": (
                catalog_sha256
            ),
        }
    )
    report["video_recording"] = index
    report["artifact_hashes"]["video_index"] = index_evidence["sha256"]
    report["evidence"]["videos"] = [
        index_evidence,
        record["video"],
        metadata_evidence,
    ]
    report["evidence"]["direct_validation"] = [direct_evidence]
    return report


def test_video_semantics_binds_resolved_instance_catalog_and_environment_freeze(
    tmp_path: Path,
) -> None:
    report = _formal_validation_video_report(tmp_path)
    assert video_semantic_errors(report, run_root=tmp_path) == ()

    index = copy.deepcopy(report["video_recording"])
    index["validation"][0]["resolved_scene_binding"]["binding_sha256"] = "2" * 64
    atomic_write_json(tmp_path / "video_index.json", index)
    index_evidence = _file_evidence(tmp_path, "video_index.json")
    report["video_recording"] = index
    report["artifact_hashes"]["video_index"] = index_evidence["sha256"]
    report["evidence"]["videos"][0] = index_evidence

    errors = "\n".join(video_semantic_errors(report, run_root=tmp_path))
    assert "resolved_scene_binding must equal its metadata sidecar" in errors


def test_video_semantics_rejects_catalog_binding_detached_from_resolver_freeze(
    tmp_path: Path,
) -> None:
    report = _formal_validation_video_report(tmp_path)
    metadata_path = tmp_path / "validation/videos/round_00/case_a.metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["resolved_scene_binding"]["scene_catalog"]["source_sha256"] = "2" * 64
    atomic_write_json(metadata_path, metadata)
    metadata_evidence = _file_evidence(
        tmp_path,
        "validation/videos/round_00/case_a.metadata.json",
    )
    index = copy.deepcopy(report["video_recording"])
    index["validation"][0]["resolved_scene_binding"] = copy.deepcopy(
        metadata["resolved_scene_binding"]
    )
    index["validation"][0]["metadata"] = metadata_evidence
    atomic_write_json(tmp_path / "video_index.json", index)
    index_evidence = _file_evidence(tmp_path, "video_index.json")
    report["video_recording"] = index
    report["artifact_hashes"]["video_index"] = index_evidence["sha256"]
    report["evidence"]["videos"] = [
        index_evidence,
        index["validation"][0]["video"],
        metadata_evidence,
    ]

    errors = "\n".join(video_semantic_errors(report, run_root=tmp_path))
    assert "scene_catalog.source_sha256 must equal the resolver freeze" in errors


def test_video_semantics_reject_hash_evidence_and_embedded_index_mismatch(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)
    report["artifact_hashes"]["video_index"] = "0" * 64
    report["video_recording"]["fps"] = 99.0
    report["evidence"]["videos"][0]["bytes"] += 1

    errors = "\n".join(video_semantic_errors(report, run_root=tmp_path))

    assert "embedded video_recording" in errors
    assert "artifact_hashes.video_index" in errors
    assert "exact evidence" in errors


def test_video_semantics_rejects_duplicate_and_unindexed_evidence(tmp_path: Path) -> None:
    report = _report(tmp_path)
    report["evidence"]["videos"].append(
        copy.deepcopy(report["evidence"]["videos"][0])
    )
    extra = tmp_path / "orphan.mp4"
    extra.write_bytes(b"orphan")
    report["evidence"]["videos"].append(_file_evidence(tmp_path, "orphan.mp4"))

    errors = "\n".join(video_semantic_errors(report, run_root=tmp_path))

    assert "duplicate path 'video_index.json'" in errors
    assert "unindexed evidence 'orphan.mp4'" in errors


def test_video_semantics_rejects_reused_record_file_path(tmp_path: Path) -> None:
    report = _report(tmp_path)
    for relative, payload in (
        ("shared.mp4", b"video"),
        ("first.metadata.json", b"first"),
        ("second.metadata.json", b"second"),
    ):
        (tmp_path / relative).write_bytes(payload)
    shared = _file_evidence(tmp_path, "shared.mp4")
    first_metadata = _file_evidence(tmp_path, "first.metadata.json")
    second_metadata = _file_evidence(tmp_path, "second.metadata.json")
    index = json.loads((tmp_path / "video_index.json").read_text(encoding="utf-8"))
    index["validation"] = [
        {"video": shared, "metadata": first_metadata},
        {"video": shared, "metadata": second_metadata},
    ]
    atomic_write_json(tmp_path / "video_index.json", index)
    index_evidence = _file_evidence(tmp_path, "video_index.json")
    report["video_recording"] = index
    report["artifact_hashes"]["video_index"] = index_evidence["sha256"]
    report["evidence"]["videos"] = [
        index_evidence,
        shared,
        first_metadata,
        second_metadata,
    ]

    errors = "\n".join(video_semantic_errors(report, run_root=tmp_path))

    assert "indexed video path 'shared.mp4' is reused" in errors


def test_video_semantics_turns_malformed_index_into_an_error(tmp_path: Path) -> None:
    report = _report(tmp_path)
    (tmp_path / "video_index.json").write_text("{not-json", encoding="utf-8")

    assert video_semantic_errors(report, run_root=tmp_path) == (
        "video_index.json could not be read",
    )


def test_video_semantics_rejects_run_root_symlink_escape(tmp_path: Path) -> None:
    report = _report(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.mp4"
    outside.write_bytes(b"outside")
    escaped = tmp_path / "escaped.mp4"
    escaped.symlink_to(outside)
    evidence = {
        "path": "escaped.mp4",
        "sha256": sha256_file(outside),
        "bytes": outside.stat().st_size,
    }
    index = json.loads((tmp_path / "video_index.json").read_text(encoding="utf-8"))
    index["validation"] = [{
        "phase": "validation",
        "round": 0,
        "case_id": "escaped",
        "video": evidence,
        "metadata": evidence,
    }]
    atomic_write_json(tmp_path / "video_index.json", index)
    report["video_recording"] = index
    report["artifact_hashes"]["video_index"] = sha256_file(
        tmp_path / "video_index.json"
    )
    report["evidence"]["videos"][0] = {
        "path": "video_index.json",
        "sha256": report["artifact_hashes"]["video_index"],
        "bytes": (tmp_path / "video_index.json").stat().st_size,
    }

    errors = "\n".join(video_semantic_errors(report, run_root=tmp_path))

    assert "escapes the run root" in errors


def test_terminal_partial_index_is_audited_without_complete_budgets(tmp_path: Path) -> None:
    base = _report(tmp_path)
    index = copy.deepcopy(base["video_recording"])
    index.update(required=True, status="partial", offline_reason=None)
    atomic_write_json(tmp_path / "video_index.json", index)
    index_evidence = _file_evidence(tmp_path, "video_index.json")
    extra = tmp_path / "partial-render.mp4"
    extra.write_bytes(b"")
    terminal = {
        "artifact_hashes": {"video_index": index_evidence["sha256"]},
        "video_recording": index,
        "evidence_files": [
            index_evidence,
            _file_evidence(tmp_path, "partial-render.mp4"),
        ],
    }

    assert terminal_video_semantic_errors(terminal, run_root=tmp_path) == ()

    terminal["artifact_hashes"]["video_index"] = "0" * 64
    terminal["video_recording"] = {**index, "fps": 99.0}
    terminal["evidence_files"][0]["bytes"] += 1
    errors = "\n".join(
        terminal_video_semantic_errors(terminal, run_root=tmp_path)
    )
    assert "embedded video_recording" in errors
    assert "artifact_hashes.video_index" in errors
    assert "evidence_files must contain exact evidence" in errors


def test_terminal_unavailable_or_error_needs_no_index_or_full_budget(
    tmp_path: Path,
) -> None:
    for status in ("unavailable", "error"):
        terminal = {
            "video_recording": {
                "schema_version": "robot_capability.mujoco_video_status.v1",
                "required": status == "error",
                "status": status,
                "reason_code": "failed_before_video_index",
            },
            "budgets": {},
            "evidence_files": [],
        }

        assert terminal_video_semantic_errors(terminal, run_root=tmp_path) == ()


def test_terminal_demo_failure_classification_is_bidirectional(tmp_path: Path) -> None:
    tasks = [
        {
            "ordinal": index + 1,
            "partition": "visible" if index < 3 else "pilot-held-out",
            "task_id": f"task_{index}",
            "agent_session_id": f"session_{index}",
            "agent_episode_id": f"episode_{index}",
            "model_calls": 2,
            "agent_turns": 2,
            "provider_http_attempts": 2,
            "provider_retries": 0,
            "client_kind": "test_client",
            "scripted": True,
            "agent_turn_budget": _counter(f"demo:task_{index}", 30, 2),
            "provider_attempt_budget_policy": {},
            "usage": {},
            "agent_status": "completed",
            "passed": index != 0,
            "oracle": {"passed": index != 0},
            "termination_reason": (
                "agent_final_oracle_failed"
                if index == 0
                else "agent_final_oracle_passed"
            ),
            "agent_final": "done",
            "trace_path": f"traces/{index + 1:02d}_task_{index}.jsonl",
        }
        for index in range(6)
    ]
    freeze = {
        "schema_version": "robot_capability.demo_freeze.v2",
        "run_id": "demo-failed",
        "batch": {
            "file_sha256": "3" * 64,
            "canonical_sha256": "4" * 64,
            "task_order_seed": 7,
        },
        "task_sources": {
            "visible_sha256": "5" * 64,
            "pilot_heldout_sha256": "6" * 64,
        },
        "catalog_sha256": "1" * 64,
        "package_sha256": "2" * 64,
        "consumer_prompt_sha256": "7" * 64,
        "consumer_model": {"model": "test"},
        "agent_turn_budget_per_task": 30,
        "oracle": {
            "definitions_sha256": "8" * 64,
            "contract_schema_sha256": "c" * 64,
            "parser_source_sha256": "d" * 64,
            "oracle_set_id": "soarm101_tabletop_p0",
            "contract_version": "1.0.0",
            "evaluator_id": "test:evaluator",
            "evaluator_source_sha256": "9" * 64,
        },
        "tasks": [
            {
                "ordinal": task["ordinal"],
                "partition": task["partition"],
                "task_id": task["task_id"],
                "instance_ref": f"initial_states/{task['task_id']}.json",
                "task_sha256": "a" * 64,
                "instance_sha256": "b" * 64,
                "trace_path": task["trace_path"],
            }
            for task in tasks
        ],
    }
    atomic_write_json(tmp_path / "demo/freeze.json", freeze)
    demo_report = {
        "schema_version": "robot_capability.demo_report.v1",
        "run_id": freeze["run_id"],
        "catalog_sha256": freeze["catalog_sha256"],
        "package_sha256": freeze["package_sha256"],
        "demo_freeze_sha256": sha256_file(tmp_path / "demo/freeze.json"),
        "tasks": tasks,
        "summary": {
            "visible": {"passed": 2, "total": 3, "success_rate": 2 / 3},
            "pilot-held-out": {"passed": 3, "total": 3, "success_rate": 1.0},
            "overall": {"passed": 5, "total": 6, "success_rate": 5 / 6},
        },
        "sealed_at": "2026-08-05T12:00:00+00:00",
    }
    atomic_write_json(tmp_path / "demo/demo_report.json", demo_report)
    terminal = {
        "terminal_state": "DEMO_FAILED",
        "terminal_reason": "demo_oracle_success_gate_failed",
        "video_recording": {
            "schema_version": "robot_capability.mujoco_video_status.v1",
            "required": False,
            "status": "unavailable",
            "reason_code": "video_index_not_created",
        },
        "evidence_files": [
            _file_evidence(tmp_path, "demo/freeze.json"),
            _file_evidence(tmp_path, "demo/demo_report.json"),
        ],
    }

    assert terminal_state_semantic_errors(terminal) == ()
    assert terminal_report_semantic_errors(terminal, run_root=tmp_path) == ()

    wrong_state = {**terminal, "terminal_state": "INFRASTRUCTURE_FAILED"}
    assert "requires DEMO_FAILED" in "\n".join(
        terminal_state_semantic_errors(wrong_state)
    )

    wrong_reason = {**terminal, "terminal_reason": "RuntimeError"}
    assert "requires the Demo oracle gate reason code" in "\n".join(
        terminal_state_semantic_errors(wrong_reason)
    )

    for marker in ("termination_reason", "agent_status", "oracle"):
        marked_report = copy.deepcopy(demo_report)
        marked_task = marked_report["tasks"][0]
        if marker == "termination_reason":
            marked_task["termination_reason"] = "infrastructure_failure"
        elif marker == "agent_status":
            marked_task["agent_status"] = "infrastructure_failed"
        else:
            marked_task["oracle"]["infrastructure_error"] = "RuntimeError: redacted"
        atomic_write_json(tmp_path / "demo/demo_report.json", marked_report)
        terminal["evidence_files"][1] = _file_evidence(
            tmp_path, "demo/demo_report.json"
        )
        assert "must not contain task infrastructure markers" in "\n".join(
            terminal_report_semantic_errors(terminal, run_root=tmp_path)
        )
