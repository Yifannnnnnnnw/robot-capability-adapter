from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from autoadapter2.foundation.errors import GateError
from autoadapter2.foundation.hashing import sha256_bytes
from autoadapter2.integration import (
    FROZEN_READINESS_LIMITS,
    IntegrationGate,
    READINESS_CHECK_IDS,
    READINESS_PROFILE_ID,
    READINESS_PROFILE_VERSION,
    load_integration_manifest,
    stable_json_sha256,
    write_stable_json,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _json_ref(root: Path, relative: str, value: object) -> dict[str, str]:
    return {"path": relative, "sha256": write_stable_json(root / relative, value)}


def _bytes_ref(root: Path, relative: str, value: bytes) -> dict[str, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return {"path": relative, "sha256": sha256_bytes(value)}


def _copy_source(root: Path, relative: str) -> None:
    source = PROJECT_ROOT / relative
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _ready_so_run(root: Path) -> dict[str, object]:
    manifest_path = "general_demo/integrations/so-arm101/integration_manifest.json"
    manifest = json.loads((PROJECT_ROOT / manifest_path).read_text(encoding="utf-8"))
    for relative in (
        manifest["morphology_ref"]["path"],
        manifest["sdk_ref"]["path"],
        manifest["translation_ref"]["path"],
    ):
        _copy_source(root, relative)

    morphology_path = root / manifest["morphology_ref"]["path"]
    morphology = json.loads(morphology_path.read_text(encoding="utf-8"))
    morphology["mujoco"]["asset_closure_status"] = "VERIFIED"
    manifest["morphology_ref"]["sha256"] = write_stable_json(morphology_path, morphology)

    sdk_path = root / manifest["sdk_ref"]["path"]
    sdk = json.loads(sdk_path.read_text(encoding="utf-8"))
    sdk["runtime"]["container_digest_status"] = "VERIFIED"
    manifest["sdk_ref"]["sha256"] = write_stable_json(sdk_path, sdk)

    translation_path = root / manifest["translation_ref"]["path"]
    translation = json.loads(translation_path.read_text(encoding="utf-8"))
    translation["status"] = "READY"
    translation["conversion"]["gripper_affine_mapping_status"] = "FROZEN"
    translation["conversion"]["gripper_tick_increases_qpos"] = True
    translation["conformance_status"] = "PASS"
    translation["unresolved"] = []
    implementation_refs = list(translation["implementation"]["source_files"])
    implementation_refs.append(translation["implementation"]["readiness_runner"])
    for reference in implementation_refs:
        _copy_source(root, reference["path"])
    manifest["translation_ref"]["sha256"] = write_stable_json(translation_path, translation)

    profile_ref = _json_ref(
        root,
        "general_demo/profiles/readiness_profile.json",
        {
            "schema_version": "1.0.0",
            "profile_id": READINESS_PROFILE_ID,
            "version": READINESS_PROFILE_VERSION,
            "check_ids": list(READINESS_CHECK_IDS),
            "time_limits": copy.deepcopy(FROZEN_READINESS_LIMITS),
            "numerical_tolerances": {"so": {"servo_tick": 1, "radian": 0.000001}},
        },
    )
    manifest.update(
        status="READY",
        readiness_profile_ref=profile_ref,
        unresolved_gaps=[],
    )
    manifest["runtime"]["lock_sha256"] = "a" * 64
    for check in manifest["compatibility_checks"]:
        check["verdict"] = "PASS"

    input_ref = _json_ref(root, "general_demo/runs/run-1/input.json", {"frozen": True})
    material: dict[str, object] = {
        "root": root,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "profile_ref": profile_ref,
        "input_ref": input_ref,
    }
    _rewrite_manifest_chain(material)
    return material


def _rewrite_manifest_chain(material: dict[str, object]) -> None:
    root = material["root"]
    manifest_path = material["manifest_path"]
    manifest = material["manifest"]
    assert isinstance(root, Path) and isinstance(manifest_path, str) and isinstance(manifest, dict)
    manifest_ref = _json_ref(root, manifest_path, manifest)
    profile_ref = material["profile_ref"]
    assert isinstance(profile_ref, dict)
    runtime = manifest["runtime"]
    assert isinstance(runtime, dict)

    report = material.get("report")
    if not isinstance(report, dict):
        checks = []
        for check_id in READINESS_CHECK_IDS:
            checks.append(
                {
                    "check_id": check_id,
                    "verdict": "PASS",
                    "evidence_refs": [_bytes_ref(root, f"general_demo/runs/run-1/evidence/{check_id}.txt", check_id.encode())],
                }
            )
        report = {
            "schema_version": "1.0.0",
            "attempt_id": "attempt-1",
            "run_id": "run-1",
            "integration_manifest_ref": manifest_ref,
            "runtime_sha256": stable_json_sha256(runtime),
            "readiness_profile_ref": profile_ref,
            "environment_fingerprint_sha256": "b" * 64,
            "dependency_sha256": {},
            "time_limits": copy.deepcopy(FROZEN_READINESS_LIMITS),
            "numerical_tolerances": {"so": {"servo_tick": 1, "radian": 0.000001}},
            "checks": checks,
            "cleanup": {
                "verdict": "PASS",
                "evidence_refs": [_bytes_ref(root, "general_demo/runs/run-1/evidence/cleanup.txt", b"cleanup")],
            },
            "started_at": "2026-08-11T00:00:00Z",
            "ended_at": "2026-08-11T00:00:01Z",
            "verdict": "PASS",
        }
        material["report"] = report
    report["integration_manifest_ref"] = manifest_ref
    report["runtime_sha256"] = stable_json_sha256(runtime)
    report["readiness_profile_ref"] = profile_ref
    report["dependency_sha256"] = {
        "morphology": manifest["morphology_ref"]["sha256"],
        "sdk": manifest["sdk_ref"]["sha256"],
        "translation": manifest["translation_ref"]["sha256"],
        "runtime_lock": runtime["lock_sha256"],
        "readiness_profile": profile_ref["sha256"],
    }

    snapshot = material.get("snapshot")
    if not isinstance(snapshot, dict):
        input_ref = material["input_ref"]
        assert isinstance(input_ref, dict)
        snapshot = {
            "schema_version": "1.0.0",
            "run_id": "run-1",
            "integration_manifest_ref": manifest_ref,
            "readiness_report_ref": {},
            "runtime_sha256": stable_json_sha256(runtime),
            "readiness_profile_ref": profile_ref,
            "library_view_refs": [input_ref],
            "task_set_ref": input_ref,
            "g2_profile_ref": input_ref,
            "observation_profile_ref": input_ref,
            "model_prompt_config_ref": input_ref,
            "budget_ref": input_ref,
            "blue_line_input_refs": [input_ref],
            "sealed_artifact_refs": [],
        }
        material["snapshot"] = snapshot
    snapshot["integration_manifest_ref"] = manifest_ref
    snapshot["runtime_sha256"] = stable_json_sha256(runtime)
    snapshot["readiness_profile_ref"] = profile_ref
    _rewrite_report_snapshot(material)


def _rewrite_report_snapshot(material: dict[str, object]) -> None:
    root = material["root"]
    report = material["report"]
    snapshot = material["snapshot"]
    assert isinstance(root, Path) and isinstance(report, dict) and isinstance(snapshot, dict)
    report_ref = _json_ref(root, "general_demo/runs/run-1/readiness_report.json", report)
    snapshot["readiness_report_ref"] = report_ref
    _json_ref(root, "general_demo/runs/run-1/run_snapshot.json", snapshot)


def _gate(material: dict[str, object]) -> IntegrationGate:
    root = material["root"]
    assert isinstance(root, Path)
    return IntegrationGate(root)


def test_matching_robot_fact_checked_ready_chain_passes(tmp_path: Path) -> None:
    material = _ready_so_run(tmp_path)
    result = _gate(material).verify(
        "general_demo/integrations/so-arm101/integration_manifest.json",
        "general_demo/runs/run-1/run_snapshot.json",
        "general_demo/runs/run-1/readiness_report.json",
    )
    assert result.run_id == "run-1"


def test_draft_and_frozen_fixture_parse_but_block_stage1(tmp_path: Path) -> None:
    material = _ready_so_run(tmp_path)
    manifest = material["manifest"]
    assert isinstance(manifest, dict)
    manifest["status"] = "DRAFT"
    manifest["unresolved_gaps"] = ["still a draft"]
    _rewrite_manifest_chain(material)
    assert load_integration_manifest(tmp_path / "general_demo/integrations/so-arm101/integration_manifest.json").value["status"] == "DRAFT"
    with pytest.raises(GateError):
        _gate(material).verify("general_demo/integrations/so-arm101/integration_manifest.json", "general_demo/runs/run-1/run_snapshot.json", "general_demo/runs/run-1/readiness_report.json")

    manifest["status"] = "FROZEN_FIXTURE"
    _rewrite_manifest_chain(material)
    with pytest.raises(GateError):
        _gate(material).verify("general_demo/integrations/so-arm101/integration_manifest.json", "general_demo/runs/run-1/run_snapshot.json", "general_demo/runs/run-1/readiness_report.json")


def test_hash_mismatch_and_missing_reference_fail_closed(tmp_path: Path) -> None:
    material = _ready_so_run(tmp_path / "hash")
    root = material["root"]
    manifest = material["manifest"]
    assert isinstance(root, Path) and isinstance(manifest, dict)
    source = root / manifest["morphology_ref"]["path"]
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(GateError):
        _gate(material).verify("general_demo/integrations/so-arm101/integration_manifest.json", "general_demo/runs/run-1/run_snapshot.json", "general_demo/runs/run-1/readiness_report.json")

    material = _ready_so_run(tmp_path / "missing")
    manifest = material["manifest"]
    root = material["root"]
    manifest_path = material["manifest_path"]
    assert isinstance(manifest, dict) and isinstance(root, Path) and isinstance(manifest_path, str)
    del manifest["sdk_ref"]
    _json_ref(root, manifest_path, manifest)
    with pytest.raises(GateError):
        _gate(material).verify("general_demo/integrations/so-arm101/integration_manifest.json", "general_demo/runs/run-1/run_snapshot.json", "general_demo/runs/run-1/readiness_report.json")


def test_wrong_run_manifest_runtime_or_profile_binding_fails_closed(tmp_path: Path) -> None:
    material = _ready_so_run(tmp_path / "run")
    report = material["report"]
    assert isinstance(report, dict)
    report["run_id"] = "other-run"
    _rewrite_report_snapshot(material)
    with pytest.raises(GateError):
        _gate(material).verify("general_demo/integrations/so-arm101/integration_manifest.json", "general_demo/runs/run-1/run_snapshot.json", "general_demo/runs/run-1/readiness_report.json")

    material = _ready_so_run(tmp_path / "manifest")
    report = material["report"]
    assert isinstance(report, dict)
    report["integration_manifest_ref"] = material["profile_ref"]
    _rewrite_report_snapshot(material)
    with pytest.raises(GateError):
        _gate(material).verify("general_demo/integrations/so-arm101/integration_manifest.json", "general_demo/runs/run-1/run_snapshot.json", "general_demo/runs/run-1/readiness_report.json")

    material = _ready_so_run(tmp_path / "runtime")
    snapshot = material["snapshot"]
    root = material["root"]
    assert isinstance(snapshot, dict) and isinstance(root, Path)
    snapshot["runtime_sha256"] = "0" * 64
    _json_ref(root, "general_demo/runs/run-1/run_snapshot.json", snapshot)
    with pytest.raises(GateError):
        _gate(material).verify("general_demo/integrations/so-arm101/integration_manifest.json", "general_demo/runs/run-1/run_snapshot.json", "general_demo/runs/run-1/readiness_report.json")

    material = _ready_so_run(tmp_path / "profile")
    root = material["root"]
    report = material["report"]
    assert isinstance(root, Path) and isinstance(report, dict)
    alternate_profile = _json_ref(
        root,
        "general_demo/profiles/alternate.json",
        {
            "schema_version": "1.0.0", "profile_id": READINESS_PROFILE_ID,
            "version": READINESS_PROFILE_VERSION, "check_ids": list(READINESS_CHECK_IDS),
            "time_limits": copy.deepcopy(FROZEN_READINESS_LIMITS),
            "numerical_tolerances": {"so": {"servo_tick": 2}},
        },
    )
    report["readiness_profile_ref"] = alternate_profile
    report["dependency_sha256"]["readiness_profile"] = alternate_profile["sha256"]
    _rewrite_report_snapshot(material)
    with pytest.raises(GateError):
        _gate(material).verify("general_demo/integrations/so-arm101/integration_manifest.json", "general_demo/runs/run-1/run_snapshot.json", "general_demo/runs/run-1/readiness_report.json")


@pytest.mark.parametrize("mutation", ["fail", "missing", "duplicate", "extra"])
def test_each_readiness_check_must_appear_once_and_pass(tmp_path: Path, mutation: str) -> None:
    material = _ready_so_run(tmp_path)
    report = material["report"]
    assert isinstance(report, dict)
    checks = report["checks"]
    assert isinstance(checks, list)
    if mutation == "fail":
        checks[0]["verdict"] = "FAIL"
        checks[0]["infrastructure_category"] = "sdk_failure"
    elif mutation == "missing":
        checks.pop()
    elif mutation == "duplicate":
        checks[-1]["check_id"] = checks[0]["check_id"]
    else:
        checks.append({"check_id": "extra_check", "verdict": "PASS", "evidence_refs": checks[0]["evidence_refs"]})
    _rewrite_report_snapshot(material)
    with pytest.raises(GateError):
        _gate(material).verify("general_demo/integrations/so-arm101/integration_manifest.json", "general_demo/runs/run-1/run_snapshot.json", "general_demo/runs/run-1/readiness_report.json")


def test_cleanup_failure_and_robot_fact_mutation_block_stage1(tmp_path: Path) -> None:
    material = _ready_so_run(tmp_path / "cleanup")
    report = material["report"]
    assert isinstance(report, dict)
    report["cleanup"]["verdict"] = "FAIL"
    _rewrite_report_snapshot(material)
    with pytest.raises(GateError):
        _gate(material).verify("general_demo/integrations/so-arm101/integration_manifest.json", "general_demo/runs/run-1/run_snapshot.json", "general_demo/runs/run-1/readiness_report.json")

    material = _ready_so_run(tmp_path / "facts")
    root = material["root"]
    manifest = material["manifest"]
    assert isinstance(root, Path) and isinstance(manifest, dict)
    translation_path = root / manifest["translation_ref"]["path"]
    translation = json.loads(translation_path.read_text(encoding="utf-8"))
    translation["motor_mapping"][0]["motor_id"] = 99
    manifest["translation_ref"]["sha256"] = write_stable_json(translation_path, translation)
    _rewrite_manifest_chain(material)
    with pytest.raises(GateError):
        _gate(material).verify("general_demo/integrations/so-arm101/integration_manifest.json", "general_demo/runs/run-1/run_snapshot.json", "general_demo/runs/run-1/readiness_report.json")
