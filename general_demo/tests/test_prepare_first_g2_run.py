from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from autoadapter2.foundation.canonical import canonical_bytes
from autoadapter2.foundation.errors import GateError
from autoadapter2.foundation.hashing import content_hash, sha256_bytes
from autoadapter2.foundation.seals import create_seal
from autoadapter2.generation import FixtureJsonGenerator, Stage1Runner
from autoadapter2.integration import (
    FROZEN_READINESS_LIMITS,
    READINESS_CHECK_IDS,
    ExperimentIntegrationGate,
    load_integration_manifest,
    load_run_snapshot,
    stable_json_sha256,
    write_stable_json,
)
from autoadapter2.implementation import validate_implementation_bundle
from autoadapter2.libraries.tasks import TaskLibraryPackage
from autoadapter2.orchestration import run_pack
from autoadapter2.orchestration.run_pack import (
    RunPackError,
    build_first_g2_run_pack,
    finalize_first_g2_run_snapshot,
    materialize_validation_a_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _copy_project(root: Path) -> None:
    shutil.copytree(PROJECT_ROOT / "general_demo", root / "general_demo")


def _ref(root: Path, relative: str, value: Any) -> dict[str, str]:
    return {"path": relative, "sha256": write_stable_json(root / relative, value)}


def _bytes_ref(root: Path, relative: str, payload: bytes) -> dict[str, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {"path": relative, "sha256": sha256_bytes(payload)}


def _readiness_report(
    root: Path,
    robot: str,
    run_id: str,
    *,
    verdict: str = "PASS",
    namespace: str | None = None,
) -> str:
    manifest_rel = f"general_demo/integrations/{robot}/integration_manifest.json"
    manifest_artifact = load_integration_manifest(root / manifest_rel)
    manifest = manifest_artifact.value
    profile_ref = manifest["readiness_profile_ref"]
    profile = json.loads((root / profile_ref["path"]).read_text(encoding="utf-8"))
    checks: list[dict[str, Any]] = []
    for index, check_id in enumerate(READINESS_CHECK_IDS):
        check_verdict = "PASS" if verdict == "PASS" or index else "FAIL"
        item: dict[str, Any] = {
            "check_id": check_id,
            "verdict": check_verdict,
            "evidence_refs": [_bytes_ref(root, f"{namespace or f'general_demo/readiness-evidence/{run_id}'}/evidence/{check_id}.txt", check_id.encode())],
        }
        if check_verdict == "FAIL":
            item["infrastructure_category"] = "test_fixture_failure"
        checks.append(item)
    report = {
        "schema_version": "1.0.0",
        "attempt_id": f"attempt-{run_id}",
        "run_id": run_id,
        "integration_manifest_ref": {"path": manifest_rel, "sha256": manifest_artifact.sha256},
        "runtime_sha256": stable_json_sha256(manifest["runtime"]),
        "readiness_profile_ref": copy.deepcopy(profile_ref),
        "environment_fingerprint_sha256": "b" * 64,
        "dependency_sha256": {
            "morphology": manifest["morphology_ref"]["sha256"],
            "sdk": manifest["sdk_ref"]["sha256"],
            "translation": manifest["translation_ref"]["sha256"],
            "runtime_lock": manifest["runtime"]["lock_sha256"],
            "readiness_profile": profile_ref["sha256"],
        },
        "time_limits": copy.deepcopy(FROZEN_READINESS_LIMITS),
        "numerical_tolerances": copy.deepcopy(profile["numerical_tolerances"]),
        "checks": checks,
        "cleanup": {
            "verdict": "PASS",
            "evidence_refs": [_bytes_ref(root, f"{namespace or f'general_demo/readiness-evidence/{run_id}'}/evidence/cleanup.txt", b"cleanup")],
        },
        "started_at": "2026-08-12T00:00:00Z",
        "ended_at": "2026-08-12T00:00:01Z",
        "verdict": verdict,
    }
    relative = f"{namespace or f'general_demo/external-readiness/{run_id}'}/readiness_report.json"
    _ref(root, relative, report)
    return relative


def _reviewed_blue_inputs(
    root: Path,
    robot: str,
    *,
    namespace: str | None = None,
) -> tuple[str, str]:
    configuration = "so-arm101-follower-stock-gripper" if robot == "so-arm101" else "unitree-go2-stock-12dof"
    measurement_id = f"{robot}.joint_state"
    standards = {
        "artifact_type": "standards_snapshot",
        "schema_version": "1.0.0",
        "snapshot_id": f"reviewed-{robot}-standards",
        "snapshot_version": "1.0.0",
        "robot_model_id": robot,
        "robot_configuration_id": configuration,
        "review_status": "REVIEWED_EXTERNAL_INPUT",
        "intended_use": "capability_validation_b",
        "demo_criteria_import_policy": "NOT_AUTOMATIC",
        "standards": [{
            "standard_id": f"{robot}-joint-state",
            "measurement_id": measurement_id,
            "metric": "finite_joint_state",
            "comparator": "==",
            "threshold_value": 1,
            "dwell_s": 0.2,
            "timeout_s": 2.0,
            "aggregation": "ALL",
        }],
    }
    catalog = {
        "artifact_type": "measurement_catalog",
        "schema_version": "1.0.0",
        "catalog_id": f"reviewed-{robot}-measurements",
        "catalog_version": "1.0.0",
        "robot_model_id": robot,
        "robot_configuration_id": configuration,
        "intended_use": "capability_validation_b",
        "truth_policy": {"candidate_self_report": "FORBIDDEN_AS_TRUTH", "sdk_receipt": "FORBIDDEN_AS_TRUTH"},
        "adapters": [{
            "adapter_id": f"{robot}.trusted_state",
            "owner": "trusted_validation_harness",
            "truth_source": "trusted_harness_physical_state",
            "session_task": "first_g2_capability_validation_b",
        }],
        "measurements": [{
            "measurement_id": measurement_id,
            "entity": "robot_joint_state",
            "unit": "none",
            "frame": "joint",
            "adapter_id": f"{robot}.trusted_state",
            "truth_source": "trusted_harness_physical_state",
            "metrics": ["finite_joint_state"],
        }],
        "guards": [{"guard_id": f"{robot}.trusted-physical-state", "adapter_id": f"{robot}.trusted_state"}],
    }
    base = namespace or f"general_demo/external-reviewed-blue/{robot}"
    _ref(root, f"{base}/standards_snapshot.json", standards)
    _ref(root, f"{base}/measurement_catalog.json", catalog)
    return f"{base}/standards_snapshot.json", f"{base}/measurement_catalog.json"


def _build_pack(root: Path, run_id: str, robot: str = "unitree-go2"):
    report = _readiness_report(root, "unitree-go2" if robot in {"go2", "unitree-go2"} else "so-arm101", run_id)
    standards, measurements = _reviewed_blue_inputs(root, "unitree-go2" if robot in {"go2", "unitree-go2"} else "so-arm101")
    return build_first_g2_run_pack(
        root,
        run_id,
        robot,
        readiness_report_path=report,
        reviewed_standards_snapshot_path=standards,
        reviewed_measurement_catalog_path=measurements,
    )


def _sealed_design(pack) -> tuple[dict[str, Any], dict[str, Any]]:
    requirements = [task["requirement_id"] for task in pack.stage1_task_projection]
    design = {
        "artifact_type": "capability_design",
        "schema_version": "1.0.0",
        "run_id": pack.run_id,
        "robot_public_projection": copy.deepcopy(dict(pack.robot_projection)),
        "granularity_profile": {"profile_id": "g2-reusable-effect", "version": "1.0.0", "granularity": "G2"},
        "task_requirement_ids": requirements,
        "capabilities": [{
            "capability_id": "opaque-capability-17",
            "kind": "observation",
            "requirement_ids": requirements,
            "inputs": [{"name": "target", "type": "number", "shape": "vector:3", "unit": "m", "frame": "world", "required": True}],
            "outputs": [{"name": "observed", "type": "number", "shape": "scalar", "unit": "m", "frame": "world", "required": True}],
            "effect": "Reports one public semantic effect.",
            "preconditions": [],
            "invocation_semantics": "Invoke once.",
            "temporal_semantics": "Return after a bounded observation window.",
            "invariants": [],
            "required_action_affordances": [],
            "required_observation_affordances": ["joint_position"],
            "errors": [{"code": "PUBLIC_ERROR", "message": "The effect is unavailable."}],
            "unsupported_scope": [],
        }],
        "unsupported_requirement_ids": [],
        "blocking_requirement_ids": [],
    }
    design_hash = content_hash(canonical_bytes(design))
    return design, create_seal("capability_design", design_hash)


def test_go2_pack_is_replayable_and_gate_ready(tmp_path: Path) -> None:
    _copy_project(tmp_path)
    run_id = "go2-first-g2-01"
    first = _build_pack(tmp_path, run_id)
    first_bytes = {name: path.read_bytes() for name, path in first.artifact_paths.items()}
    second = _build_pack(tmp_path, run_id)
    second_bytes = {name: path.read_bytes() for name, path in second.artifact_paths.items()}
    assert first_bytes == second_bytes

    expected_names = {
        "task_set", "task_instances", "g2_profile", "robot_projection", "model_prompt_config", "budget",
        "standards_snapshot", "measurement_catalog", "blue_line_policy", "implementation_bundle",
        "validation_a_template", "validation_harness_config", "run_snapshot",
    }
    assert set(first.artifact_paths) == expected_names
    assert all(set(reference) == {"path", "sha256"} for reference in first.artifact_refs.values())
    assert all(first.artifact_refs[name]["sha256"] == sha256_bytes(first.artifact_paths[name].read_bytes()) for name in first.artifact_paths)

    task_set = json.loads(first.task_set_path.read_text(encoding="utf-8"))
    assert [item["task_id"] for item in task_set["tasks"]] == ["G01", "G02", "G03", "G04", "G05"]
    assert all(item["requirement_id"].startswith("req-") for item in task_set["tasks"])
    assert all(set(item) == {"requirement_id", "description"} for item in first.stage1_task_projection)
    assert all("private_criterion" not in item for item in first.stage1_task_projection)
    assert json.loads(first.path("g2_profile").read_text(encoding="utf-8")) == {
        "profile_id": "g2-reusable-effect",
        "version": "1.0.0",
        "granularity": "G2",
    }
    instances = json.loads(first.path("task_instances").read_text(encoding="utf-8"))["instances"]
    assert len(instances) == 5
    assert all({"public_state", "private_binding", "requirement_id"} <= set(item) for item in instances)
    assert instances[1]["private_binding"]["reset"]["standing_height_m"] == 0.24
    assert instances[2]["private_binding"]["reset"]["initial_position_m"] == [0.0, 0.0, 0.0]
    assert instances[3]["private_binding"]["reset"]["initial_heading_frame"] == "initial_body_yaw"
    assert instances[4]["private_binding"]["reset"]["target_height_m"] == 0.24

    projection = json.loads(first.path("robot_projection").read_text(encoding="utf-8"))
    assert projection["robot_model_id"] == "unitree-go2"
    assert projection["sdk_facts"]["entry_id"] == "unitree-sdk2-go2-lowlevel"
    assert projection["effect_allowlist"] == ["stand", "sit", "hold_stable", "move_forward", "target_body_height"]
    assert projection["public_state_schema"]["additionalProperties"] is False
    projection_text = json.dumps(projection).lower()
    assert "translation" not in projection_text
    assert "mujoco" not in projection_text
    assert "criterion" not in projection_text
    assert "private" not in projection_text

    bundle = json.loads(first.path("implementation_bundle").read_text(encoding="utf-8"))
    assert set(bundle) == {"artifact_type", "schema_version", "sdk_implementation_projection", "robot_implementation_facts", "implementation_experience"}
    assert bundle["sdk_implementation_projection"]["command"]["active_slot_count"] == 12
    assert bundle["sdk_implementation_projection"]["command"]["slot_count"] == 20
    assert bundle["sdk_implementation_projection"]["observation"]["field_order"] == ["q", "dq", "tau_est", "imu_quaternion", "gyroscope", "accelerometer"]
    validate_implementation_bundle(bundle)

    validation_template = json.loads(first.path("validation_a_template").read_text(encoding="utf-8"))
    assert "capability_id" not in json.dumps(validation_template)
    assert "constructor" not in json.dumps(validation_template).lower()
    harness = json.loads(first.path("validation_harness_config").read_text(encoding="utf-8"))
    assert harness["video_profile"] == {
        "profile_id": "framework-external-scene-ffv1", "profile_version": "1.0.0", "camera": "external-scene", "view": "external-scene",
        "fps": 30, "resolution": {"width": 640, "height": 480}, "container": "matroska", "codec": "ffv1",
    }

    snapshot = load_run_snapshot(first.run_snapshot_path).value
    assert snapshot["run_id"] == run_id
    assert snapshot["readiness_report_ref"] == first.readiness_report_ref
    assert snapshot["integration_manifest_ref"] == first.integration_manifest_ref
    assert first.artifact_refs["validation_harness_config"] in snapshot["library_view_refs"]
    assert first.blue_line_input_refs[0]["path"].startswith("general_demo/external-reviewed-blue/")
    gate = ExperimentIntegrationGate(tmp_path).verify(first.integration_manifest_ref["path"], first.run_snapshot_path, first.readiness_report_ref["path"])
    assert gate.run_id == run_id


def test_validation_a_template_materializes_only_after_valid_sealed_design(tmp_path: Path) -> None:
    _copy_project(tmp_path)
    pack = _build_pack(tmp_path, "go2-first-g2-materialize")
    template = json.loads(pack.path("validation_a_template").read_text(encoding="utf-8"))
    design, seal = _sealed_design(pack)
    profile = materialize_validation_a_profile(template, design, seal)
    assert set(profile.sdk_facade_members) == {"opaque-capability-17"}
    assert set(profile.fixture_probes) == {"opaque-capability-17"}
    assert profile.fixture_probes["opaque-capability-17"]["inputs"]["target"]["value"] == [0.0, 0.0, 0.0]
    with pytest.raises(RunPackError, match="seal"):
        materialize_validation_a_profile(template, design)
    bad_seal = copy.deepcopy(seal)
    bad_seal["artifact_hash"] = "sha256:" + "0" * 64
    with pytest.raises(RunPackError, match="seal"):
        materialize_validation_a_profile(template, design, bad_seal)


def test_robot_projection_is_accepted_as_stage1_public_input(tmp_path: Path) -> None:
    _copy_project(tmp_path)
    pack = _build_pack(tmp_path, "go2-first-g2-stage1-input")
    capabilities = [{
        "capability_id": f"effect-{index}",
        "kind": "observation",
        "requirement_ids": [task["requirement_id"]],
        "inputs": [],
        "outputs": [],
        "effect": "Reports one public semantic effect.",
        "preconditions": [],
        "invocation_semantics": "Invoke once.",
        "temporal_semantics": "Return after a bounded observation window.",
        "invariants": [],
        "required_action_affordances": [],
        "required_observation_affordances": [],
        "errors": [{"code": "PUBLIC_ERROR", "message": "The effect is unavailable."}],
        "unsupported_scope": [],
    } for index, task in enumerate(pack.stage1_task_projection, start=1)]
    result = Stage1Runner(FixtureJsonGenerator([{
        "capabilities": capabilities,
        "unsupported_requirement_ids": [],
        "blocking_requirement_ids": [],
    }])).run(
        pack.run_id,
        json.loads(pack.path("robot_projection").read_text(encoding="utf-8")),
        list(pack.stage1_task_projection),
        json.loads(pack.path("g2_profile").read_text(encoding="utf-8")),
    )
    assert result.status == "SEALED"


def test_missing_external_blue_inputs_empty_bundle_and_public_private_split_reject(tmp_path: Path) -> None:
    _copy_project(tmp_path)
    run_id = "go2-first-g2-rejections"
    report = _readiness_report(tmp_path, "unitree-go2", run_id)
    with pytest.raises(RunPackError, match="reviewed_standards_snapshot_path"):
        build_first_g2_run_pack(tmp_path, run_id, "unitree-go2", readiness_report_path=report)

    standards, measurements = _reviewed_blue_inputs(tmp_path, "unitree-go2")
    template_path = tmp_path / "general_demo/config/first_g2_demo/robots/unitree-go2.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    template["implementation_projection"]["sdk_implementation_projection"] = {}
    write_stable_json(template_path, template)
    with pytest.raises(RunPackError, match="implementation projection"):
        build_first_g2_run_pack(tmp_path, run_id, "unitree-go2", readiness_report_path=report, standards_snapshot_path=standards, measurement_catalog_path=measurements)

    _copy_project(tmp_path / "split")
    split_root = tmp_path / "split"
    split_report = _readiness_report(split_root, "unitree-go2", "go2-first-g2-split")
    split_standards, split_measurements = _reviewed_blue_inputs(split_root, "unitree-go2")
    split_template_path = split_root / "general_demo/config/first_g2_demo/robots/unitree-go2.json"
    split_template = json.loads(split_template_path.read_text(encoding="utf-8"))
    split_template["public_task_states"]["G03"]["task_parameters"]["private_reset"] = True
    write_stable_json(split_template_path, split_template)
    with pytest.raises(RunPackError, match="public_state|private"):
        build_first_g2_run_pack(split_root, "go2-first-g2-split", "unitree-go2", readiness_report_path=split_report, standards_snapshot_path=split_standards, measurement_catalog_path=split_measurements)


def test_so_draft_manifest_and_failed_readiness_are_rejected(tmp_path: Path) -> None:
    _copy_project(tmp_path)
    run_id = "so-first-g2-blocked"
    with pytest.raises(RunPackError, match="readiness_report_path"):
        build_first_g2_run_pack(tmp_path, run_id, "so-arm101")
    report = _readiness_report(tmp_path, "so-arm101", run_id)
    standards, measurements = _reviewed_blue_inputs(tmp_path, "so-arm101")
    with pytest.raises(RunPackError, match="DRAFT|READY"):
        build_first_g2_run_pack(tmp_path, run_id, "so-arm101", readiness_report_path=report, standards_snapshot_path=standards, measurement_catalog_path=measurements)

    _copy_project(tmp_path / "failed")
    failed_root = tmp_path / "failed"
    failed_report = _readiness_report(failed_root, "unitree-go2", "go2-failed", verdict="FAIL")
    failed_standards, failed_measurements = _reviewed_blue_inputs(failed_root, "unitree-go2")
    with pytest.raises(RunPackError, match="PASS"):
        build_first_g2_run_pack(failed_root, "go2-failed", "unitree-go2", readiness_report_path=failed_report, standards_snapshot_path=failed_standards, measurement_catalog_path=failed_measurements)


def test_candidate_review_api_is_not_used_for_the_formal_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _copy_project(tmp_path)
    run_id = "go2-first-g2-no-candidates"
    report = _readiness_report(tmp_path, "unitree-go2", run_id)
    standards, measurements = _reviewed_blue_inputs(tmp_path, "unitree-go2")

    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("research candidate records must not enter the formal pack")

    monkeypatch.setattr(TaskLibraryPackage, "review_candidates", fail_if_called)
    build_first_g2_run_pack(tmp_path, run_id, "unitree-go2", readiness_report_path=report, standards_snapshot_path=standards, measurement_catalog_path=measurements)


def test_tampered_frozen_plan_reference_fails_gate(tmp_path: Path) -> None:
    _copy_project(tmp_path)
    pack = _build_pack(tmp_path, "go2-first-g2-tamper")
    budget = json.loads(pack.path("budget").read_text(encoding="utf-8"))
    budget["stage2"]["max_llm_calls"] = 29
    write_stable_json(pack.path("budget"), budget)
    with pytest.raises(GateError):
        ExperimentIntegrationGate(tmp_path).verify(pack.integration_manifest_ref["path"], pack.run_snapshot_path, pack.readiness_report_ref["path"])


@pytest.mark.parametrize(
    "artifact",
    ["model_prompt_config", "budget", "validation_a_template", "validation_harness_config"],
)
def test_tampered_frozen_library_or_plan_input_fails_gate(tmp_path: Path, artifact: str) -> None:
    _copy_project(tmp_path)
    pack = _build_pack(tmp_path, f"go2-first-g2-tamper-{artifact}")
    value = json.loads(pack.path(artifact).read_text(encoding="utf-8"))
    value["tampered_for_test"] = True
    write_stable_json(pack.path(artifact), value)
    with pytest.raises(GateError):
        ExperimentIntegrationGate(tmp_path).verify(
            pack.integration_manifest_ref["path"],
            pack.run_snapshot_path,
            pack.readiness_report_ref["path"],
        )


def test_validation_a_template_rejects_capability_specific_entries(tmp_path: Path) -> None:
    _copy_project(tmp_path)
    template_path = tmp_path / "general_demo/config/first_g2_demo/robots/unitree-go2.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    template["validation_a_template"]["fixture_probes"] = {"fake-capability": {}}
    write_stable_json(template_path, template)
    report = _readiness_report(tmp_path, "unitree-go2", "go2-first-g2-template-rejection")
    standards, measurements = _reviewed_blue_inputs(tmp_path, "unitree-go2")
    with pytest.raises(RunPackError, match="capability-independent|closed"):
        build_first_g2_run_pack(
            tmp_path,
            "go2-first-g2-template-rejection",
            "unitree-go2",
            readiness_report_path=report,
            standards_snapshot_path=standards,
            measurement_catalog_path=measurements,
        )


def test_finalized_snapshot_is_new_and_binds_sealed_refs(tmp_path: Path) -> None:
    _copy_project(tmp_path)
    pack = _build_pack(tmp_path, "go2-first-g2-finalize")
    original = pack.run_snapshot_path.read_bytes()
    sealed_ref = pack.artifact_refs["implementation_bundle"]
    finalized = finalize_first_g2_run_snapshot(tmp_path, pack.run_snapshot_path, [sealed_ref])
    assert finalized.path != pack.run_snapshot_path
    assert pack.run_snapshot_path.read_bytes() == original
    assert finalized.value["sealed_artifact_refs"] == [sealed_ref]
    assert load_run_snapshot(finalized.path).value["sealed_artifact_refs"] == [sealed_ref]
    with pytest.raises(RunPackError, match="already exists"):
        finalize_first_g2_run_snapshot(tmp_path, pack.run_snapshot_path, [sealed_ref])


def test_default_cli_smoke_uses_repository_root_without_root_flag() -> None:
    with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "general_demo") as temporary:
        smoke_root = Path(temporary)
        run_id = "go2-first-g2-cli-smoke"
        namespace = f"general_demo/.run-pack-cli-smoke/{smoke_root.name}"
        report = _readiness_report(PROJECT_ROOT, "unitree-go2", run_id, namespace=namespace)
        standards, measurements = _reviewed_blue_inputs(
            PROJECT_ROOT,
            "unitree-go2",
            namespace=f"{namespace}/blue/unitree-go2",
        )
        output = smoke_root / "pack"
        report_arg = report
        standards_arg = standards
        measurements_arg = measurements
        output_arg = output.relative_to(PROJECT_ROOT).as_posix()
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "general_demo/scripts/prepare_first_g2_run.py"),
                    "--run-id", run_id,
                    "--robot", "unitree-go2",
                    "--readiness-report", report_arg,
                    "--standards-snapshot", standards_arg,
                    "--measurement-catalog", measurements_arg,
                    "--output-dir", output_arg,
                ],
                cwd=PROJECT_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            paths = json.loads(result.stdout)
            assert paths["run_snapshot"].endswith("run_snapshot.json")
        finally:
            shutil.rmtree(PROJECT_ROOT / namespace, ignore_errors=True)
