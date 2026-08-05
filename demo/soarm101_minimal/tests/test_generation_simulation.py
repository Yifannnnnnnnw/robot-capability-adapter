from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from soarm_demo.audit import sha256_file, sha256_json
from soarm_demo.environment_resolver import (
    catalog_asset_descriptor_hashes,
    discover_mjcf_referenced_files,
    resolve_generation_environment,
)
from soarm_demo.libraries import load_structured
from soarm_demo.generation import GenerationWorkspace, repair_tools, stage2_tools
from soarm_demo.generation_simulation import (
    GenerationSimulationError,
    GenerationSimulationSandbox,
    ProbeLimits,
)


ROOT = Path(__file__).resolve().parents[1]


def test_probe_worker_phase_limits_leave_room_inside_tool_deadline() -> None:
    with pytest.raises(ValueError, match="25-second tool deadline"):
        ProbeLimits(worker_startup_timeout_s=11.0, wall_timeout_s=12.0)


def _resolve_production_environment(tmp_path: Path):
    libraries_root = ROOT / "libraries"
    return resolve_generation_environment(
        {
            "morphology": libraries_root / "morphology/soarm101/v1",
            "sdk_runtime": (
                libraries_root / "sdk_runtime/lerobot_soarm101/0.6.0"
            ),
            "tasks": libraries_root / "tasks/soarm101_tabletop/v1",
            "experience": libraries_root / "experience/v1",
        },
        morphology_scene=(
            libraries_root / "morphology/scenes/soarm101_tabletop/v1"
        ),
        libraries_root=libraries_root,
        output_path=tmp_path / "resolved_environment.json",
    )


def _install_test_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("soarm_demo.environment_resolver")

    def verify(
        path: str | Path,
        *,
        expected_sha256: str | None = None,
        libraries_root: str | Path | None = None,
        rehash_sources: bool = False,
    ) -> dict[str, Any]:
        assert libraries_root == ROOT / "libraries"
        assert rehash_sources is True
        source = Path(path)
        if expected_sha256 is not None and sha256_file(source) != expected_sha256:
            raise ValueError("freeze drift")
        return {"manifest": json.loads(source.read_text(encoding="utf-8"))}

    module.verify_generation_environment_freeze = verify  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "soarm_demo.environment_resolver", module)


def _freeze(tmp_path: Path) -> Path:
    model = ROOT / "libraries/morphology/soarm101/v1/model/so101.xml"
    scene = ROOT / "libraries/morphology/scenes/soarm101_tabletop/v1/scene.yaml"
    catalog = (
        ROOT
        / "libraries/morphology/scenes/soarm101_tabletop/v1/assets/primitive_catalog.yaml"
    )
    catalog_document = load_structured(catalog)
    assert isinstance(catalog_document, dict)
    manifest = {
        "schema_version": "robot_capability.generation_environment_manifest.v1",
        "bindings": {
            "morphology": {
                "robot_model": {
                    "path": "morphology/soarm101/v1/model/so101.xml",
                    "sha256": sha256_file(model),
                    "robot_id": "soarm101",
                    "version": "v1",
                    "model_format": "mujoco_mjcf",
                    "referenced_files": [
                        {
                            "kind": item.kind,
                            "reference": item.reference,
                            "path": item.path.relative_to(
                                ROOT / "libraries"
                            ).as_posix(),
                            "sha256": sha256_file(item.path),
                        }
                        for item in discover_mjcf_referenced_files(model)
                    ],
                },
                "scene_definition": {
                    "path": "morphology/scenes/soarm101_tabletop/v1/scene.yaml",
                    "sha256": sha256_file(scene),
                },
                "scene_asset_catalog": {
                    "path": (
                        "morphology/scenes/soarm101_tabletop/v1/assets/"
                        "primitive_catalog.yaml"
                    ),
                    "sha256": sha256_file(catalog),
                    "catalog_id": "soarm101_tabletop_primitives",
                    "version": "1.0.0",
                    "asset_descriptor_sha256": catalog_asset_descriptor_hashes(
                        catalog_document
                    ),
                },
            }
        },
        "public_simulation": {
            "privacy_class": "public_generation_sandbox",
            "not_task_instance": True,
            "common_reset": {
                "table": {
                    "asset_ref": (
                        "morphology.scene_asset/tabletop_standard_p0@1.0.0#brown"
                    ),
                    "center_m": [0.35, 0.0, 0.01],
                }
            },
            "smoke_instance": {
                "instance_id": "public_generation_smoke_test",
                "bodies": [
                    {
                        "id": "public_cube",
                        "asset_ref": (
                            "morphology.scene_asset/cube_30mm_20g@1.0.0#red"
                        ),
                        "position_m": [0.34, 0.0, 0.035],
                        "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                    }
                ],
                "markers": [],
            },
            "probe_inputs": {
                "joint_targets": {
                    "shoulder_pan.pos": 0.0,
                    "shoulder_lift.pos": 0.0,
                    "elbow_flex.pos": 50.0,
                    "wrist_flex.pos": 0.0,
                    "wrist_roll.pos": 0.0,
                    "gripper.pos": 80.0,
                },
                "cartesian_target_m": [0.30, 0.0, 0.10],
                "object_source_m": [0.34, 0.0, 0.035],
                "object_target_m": [0.38, 0.04, 0.035],
                "height_delta_m": 0.04,
                "object_extent_m": 0.03,
                "sequence_moves": [
                    {
                        "object_source_m": [0.34, 0.0, 0.035],
                        "object_target_m": [0.38, 0.04, 0.035],
                        "object_extent_m": 0.03,
                    }
                ],
            },
        },
    }
    document = {
        "schema_version": "robot_capability.generation_environment_freeze.v1",
        "manifest": manifest,
        "manifest_sha256": sha256_json(manifest),
    }
    path = tmp_path / "environment_freeze.json"
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    return path


def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GenerationSimulationSandbox:
    _install_test_resolver(monkeypatch)
    freeze = _freeze(tmp_path)
    return GenerationSimulationSandbox.prepare(
        mode="offline",
        environment_freeze_path=freeze,
        libraries_root=ROOT / "libraries",
        expected_environment_freeze_sha256=sha256_file(freeze),
        run_root=tmp_path / "run",
        limits=ProbeLimits(
            max_calls=3,
            wall_timeout_s=5.0,
            max_total_simulation_s=5.0,
            max_simulation_s_per_call=2.0,
            max_runtime_calls_per_probe=200,
            max_worker_memory_mb=1_024,
        ),
    )


def test_offline_initial_inspection_creates_no_world_and_is_not_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _sandbox(tmp_path, monkeypatch)

    inspection = sandbox.inspect_environment()

    assert inspection["evidence_class"] == "static_catalog_inspection_no_world"
    assert inspection["physical_runtime"] is False
    assert inspection["configured_probe_runtime"] == "deterministic_non_physical_fixture"
    assert inspection["worlds_created"] == 0
    assert inspection["scene_summary"]["dynamic_object_count"] == 1
    assert inspection["feedback_scope"] == {
        "public_synthetic_scene_only": True,
        "not_validation": True,
        "cannot_certify_validation_a_or_b": True,
        "contains_oracle_or_contact_data": False,
    }
    serialized = json.dumps(inspection).lower()
    assert "mjdata" not in serialized
    assert "heldout" not in serialized
    assert (tmp_path / "run/generation/simulation/catalog_inspection.json").is_file()


def test_production_resolver_freeze_statically_selects_only_referenced_assets(
    tmp_path: Path,
) -> None:
    resolved = _resolve_production_environment(tmp_path)
    sandbox = GenerationSimulationSandbox.prepare(
        mode="offline",
        environment_freeze_path=resolved.freeze_path,
        expected_environment_freeze_sha256=resolved.freeze_sha256,
        libraries_root=ROOT / "libraries",
        run_root=tmp_path / "run",
    )

    inspection = sandbox.inspect_environment()
    assert inspection["environment_freeze_sha256"] == resolved.freeze_sha256
    assert inspection["scene_revision_sha256"] == resolved.manifest_sha256
    assert inspection["scene_summary"]["dynamic_object_count"] == 2
    assert inspection["scene_summary"]["receptacle_count"] == 2
    assert inspection["scene_summary"]["marker_count"] == 2
    assert inspection["scene_summary"]["selected_asset_instance_count"] == 7
    assert inspection["scene_summary"]["unreferenced_asset_variants_materialized"] == 0
    assert inspection["worlds_created"] == 0

    preflight = sandbox.framework_preflight_inputs()
    assert preflight["environment_freeze_sha256"] == resolved.freeze_sha256
    assert preflight["scene_revision_sha256"] == resolved.manifest_sha256
    assert preflight["reset_settle_s"] == 0.5
    assert len(preflight["initial_state"]["bodies"]) == 4
    assert preflight["scene_catalog"].is_file()


def test_aws_initial_inspection_does_not_import_or_construct_mujoco(
    tmp_path: Path,
) -> None:
    resolved = _resolve_production_environment(tmp_path)
    sandbox = GenerationSimulationSandbox.prepare(
        mode="aws",
        environment_freeze_path=resolved.freeze_path,
        expected_environment_freeze_sha256=resolved.freeze_sha256,
        libraries_root=ROOT / "libraries",
        run_root=tmp_path / "run",
        limits=ProbeLimits(wall_timeout_s=8.0),
    )

    inspection = sandbox.inspect_environment()
    summary = inspection["scene_summary"]
    assert inspection["evidence_class"] == "static_catalog_inspection_no_world"
    assert inspection["configured_probe_runtime"] == "actual_mujoco_public_smoke_scene"
    assert inspection["worlds_created"] == 0
    assert inspection["physical_runtime"] is False
    assert summary["dynamic_object_count"] == 2
    assert summary["marker_count"] == 2
    assert "finite_after_reset" not in summary
    assert "compiled_model_counts" not in summary


def test_aws_first_probe_constructs_actual_public_mujoco_world_on_demand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("mujoco")
    _install_test_resolver(monkeypatch)
    freeze = _freeze(tmp_path)

    sandbox = GenerationSimulationSandbox.prepare(
        mode="aws",
        environment_freeze_path=freeze,
        libraries_root=ROOT / "libraries",
        expected_environment_freeze_sha256=sha256_file(freeze),
        run_root=tmp_path / "run",
        limits=ProbeLimits(
            max_calls=1,
            wall_timeout_s=8.0,
            max_total_simulation_s=3.0,
            max_simulation_s_per_call=3.0,
            max_runtime_calls_per_probe=250,
            max_worker_memory_mb=1_536,
        ),
    )

    inspection = sandbox.inspect_environment()
    assert inspection["evidence_class"] == "static_catalog_inspection_no_world"
    assert inspection["worlds_created"] == 0
    assert "compiled_model_counts" not in inspection["scene_summary"]

    shutil.copytree(
        ROOT / "fixtures/generated_pass",
        tmp_path / "run/generated_package",
    )
    frozen_stage1 = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    result = sandbox.probe_generated_capability(
        {
            "capability_id": "G1.move_joints",
            "profile_id": "public_joint_hold",
            "frozen_stage1": frozen_stage1,
        }
    )
    assert result["outcome"] == "generated_returned"
    assert result["runtime_evidence"] == "actual_mujoco_public_smoke_scene"
    assert result["measurements"]["finite_state"] is True
    assert result["measurements"]["action_count"] == 1
    assert "infrastructure_timing" not in result
    accounting = json.loads(
        (tmp_path / "run/generation/simulation/accounting.json").read_text(
            encoding="utf-8"
        )
    )
    timing = accounting["probe_records"][0]["infrastructure_timing"]
    assert 0.0 < timing["trusted_startup_s"] <= 10.0
    assert 0.0 <= timing["generated_execution_s"] <= 8.0
    assert accounting["budget"]["worker_wall_time_seconds"] == {
        "trusted_startup_limit": 10.0,
        "generated_execution_limit": 8.0,
        "combined_limit": 18.0,
    }


def test_workspace_includes_preassembled_inspection_without_a_stage1_extra_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _sandbox(tmp_path, monkeypatch)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "tasks.json").write_text('{"visible":true}', encoding="utf-8")
    workspace = GenerationWorkspace(
        input_snapshot=snapshot,
        run_root=tmp_path / "run",
        simulation_sandbox=sandbox,
        allow_orchestration_fixture_evidence=True,
    )

    context = workspace.read_generation_snapshot()
    stage1 = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    assert workspace.review_stage1({"artifact": stage1})["valid"] is True
    assert workspace.submit_stage1({"artifact": stage1})["accepted"] is True
    workspace.freeze_stage1()
    stage2 = stage2_tools(workspace)
    repair = repair_tools(workspace, base_tools=stage2)

    assert context["public_simulation_environment"]["physical_runtime"] is False
    assert "probe_generated_capability" in stage2
    assert stage2["probe_generated_capability"].timeout_s == 25.0
    wall_budget = context["public_simulation_environment"]["probe_budget"][
        "worker_wall_time_seconds"
    ]
    assert wall_budget["combined_limit"] == 15.0
    assert stage2["probe_generated_capability"].timeout_s > (
        wall_budget["combined_limit"] + 2.0
    )
    assert "probe_generated_capability" in repair


def test_probe_blocks_before_static_pass_and_never_claims_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _sandbox(tmp_path, monkeypatch)
    package = tmp_path / "run/generated_package"
    package.mkdir(parents=True, exist_ok=True)
    frozen_stage1 = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )

    result = sandbox.probe_generated_capability(
        {
            "capability_id": "G1.move_joints",
            "profile_id": "public_joint_hold",
            "frozen_stage1": frozen_stage1,
        }
    )

    assert result["outcome"] == "blocked"
    assert result["code"] == "AUTHORITATIVE_STATIC_PASS_REQUIRED"
    assert result["measurements"] == {}
    assert result["certification"] == {
        "validation_a": False,
        "validation_b": False,
        "demo": False,
        "seal": False,
    }


def test_valid_static_package_runs_in_fresh_offline_worker_with_sanitized_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _sandbox(tmp_path, monkeypatch)
    source = ROOT / "fixtures/generated_pass"
    destination = tmp_path / "run/generated_package"
    shutil.copytree(source, destination)
    frozen_stage1 = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )

    result = sandbox.probe_generated_capability(
        {
            "capability_id": "G1.move_joints",
            "profile_id": "public_joint_hold",
            "frozen_stage1": frozen_stage1,
        }
    )

    assert result["outcome"] == "generated_returned"
    assert result["generated_status"] == "succeeded"
    assert result["runtime_evidence"] == "deterministic_non_physical_fixture"
    assert result["measurements"]["finite_state"] is True
    assert result["measurements"]["action_count"] == 1
    serialized = json.dumps(result).lower()
    for forbidden in ("traceback", "exception", "stdout", "stderr", "contact", "oracle"):
        assert forbidden not in serialized


def test_freeze_drift_fails_closed_without_a_scene_builder_state_machine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _sandbox(tmp_path, monkeypatch)
    accounting = json.loads(
        (tmp_path / "run/generation/simulation/accounting.json").read_text(
            encoding="utf-8"
        )
    )
    assert accounting["evidence_class"] == "static_catalog_inspection_no_world"
    assert accounting["probe_records"] == []
    assert "scene_builder" not in accounting
    assert "scene_asset_requests" not in accounting

    sandbox.environment_freeze_path.write_text("{}", encoding="utf-8")
    with pytest.raises(GenerationSimulationError, match="drifted"):
        sandbox.inspect_environment()


def test_unknown_public_asset_fails_as_missing_input_without_creating_a_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_test_resolver(monkeypatch)
    freeze = _freeze(tmp_path)
    document = json.loads(freeze.read_text(encoding="utf-8"))
    document["manifest"]["public_simulation"]["smoke_instance"]["bodies"][0][
        "asset_ref"
    ] = "morphology.scene_asset/not_in_catalog@1.0.0#red"
    document["manifest_sha256"] = sha256_json(document["manifest"])
    freeze.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")

    with pytest.raises(GenerationSimulationError, match="missing or invalid asset"):
        GenerationSimulationSandbox.prepare(
            mode="offline",
            environment_freeze_path=freeze,
            libraries_root=ROOT / "libraries",
            expected_environment_freeze_sha256=sha256_file(freeze),
            run_root=tmp_path / "run",
        )
    assert not (tmp_path / "run/generation/simulation/catalog_inspection.json").exists()
