from __future__ import annotations

import json
from pathlib import Path
import shutil

import jsonschema
import pytest

from soarm_demo.audit import sha256_json
from soarm_demo.authored_scene_preflight import (
    AuthoredScenePreflightError,
    run_authored_scene_preflight,
)
from soarm_demo.bridge.scene_catalog import SceneAssetCatalog
from soarm_demo.private_execution_bundle import materialize_private_execution_bundle
import soarm_demo.authored_scene_preflight as preflight_module


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / "private/task_library/soarm101_tabletop/v1"
VISIBLE_TASKS = ROOT / "libraries/tasks/soarm101_tabletop/v1/visible_tasks.jsonl"
CATALOG_PATH = (
    ROOT
    / "libraries/morphology/scenes/soarm101_tabletop/v1/assets/primitive_catalog.yaml"
)


def _catalog_freeze(catalog: SceneAssetCatalog) -> dict:
    relative = CATALOG_PATH.relative_to(ROOT).as_posix()
    manifest = {
        "bindings": {
            "morphology": {
                "scene_asset_catalog": {
                    "catalog_id": catalog.catalog_id,
                    "version": catalog.version,
                    "sha256": catalog.source_sha256,
                    "asset_descriptor_sha256": catalog.asset_descriptor_sha256,
                    "path": relative,
                }
            }
        }
    }
    return {
        "schema_version": "robot_capability.generation_environment_freeze.v1",
        "manifest": manifest,
        "manifest_sha256": sha256_json(manifest),
    }


def _bundle(
    tmp_path: Path,
    *,
    private_root: Path = PRIVATE_ROOT,
    visible_tasks: Path = VISIBLE_TASKS,
):
    return materialize_private_execution_bundle(
        private_task_root=private_root,
        visible_tasks_path=visible_tasks,
        destination=tmp_path / "bundle",
        schema_path=ROOT / "schemas/private_execution_bundle.schema.json",
    )


def _run_preflight(
    tmp_path: Path,
    *,
    mode: str = "offline",
    private_root=PRIVATE_ROOT,
    visible_tasks=VISIBLE_TASKS,
):
    bundle = _bundle(
        tmp_path,
        private_root=private_root,
        visible_tasks=visible_tasks,
    )
    catalog = SceneAssetCatalog(CATALOG_PATH)
    evidence = run_authored_scene_preflight(
        mode=mode,
        bundle=bundle,
        catalog=catalog,
        scene_freeze=_catalog_freeze(catalog),
    )
    return bundle, evidence


@pytest.mark.parametrize("mode", ["offline", "aws"])
def test_all_12_task_preflight_is_static_redacted_and_source_fresh(
    tmp_path: Path,
    mode: str,
) -> None:
    assert not hasattr(preflight_module, "SO101MujocoTabletopRuntime")
    bundle, evidence = _run_preflight(tmp_path, mode=mode)
    evidence["validation_reference"] = {
        "sha256": "a" * 64,
        "frozen_catalog_contract_verified": True,
    }
    jsonschema.validate(
        evidence,
        json.loads(
            (ROOT / "schemas/private_execution_preflight.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    assert evidence["partition_counts"] == {
        "catalog_visible": 9,
        "catalog_pilot_heldout": 3,
        "authored_instances": 12,
        "selected_visible": 3,
        "selected_pilot_heldout": 3,
        "selected_overlap": 0,
    }
    assert evidence["mujoco_worlds_created"] == 0
    expected_instance_binding = sha256_json(
        sorted(
            bundle.sha256_for_role(f"authored_instance_{ordinal:02d}")
            for ordinal in range(1, 13)
        )
    )
    assert evidence["source_bindings"]["authored_instance_binding_sha256"] == (
        expected_instance_binding
    )
    serialized = json.dumps(evidence, sort_keys=True)
    assert "soarm101_p0_" not in serialized
    assert "initial_states/" not in serialized


def test_all_12_preflight_rejects_missing_required_agent_scene_fact(
    tmp_path: Path,
) -> None:
    private_copy = tmp_path / "private"
    visible_copy = tmp_path / "visible_tasks.jsonl"
    shutil.copytree(PRIVATE_ROOT, private_copy)
    records = [
        json.loads(line)
        for line in VISIBLE_TASKS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    records[0]["agent_input_contract"]["required_scene_fact_paths"].append(
        "targets.missing_target.position_m"
    )
    visible_copy.write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )
    bundle = _bundle(
        tmp_path,
        private_root=private_copy,
        visible_tasks=visible_copy,
    )
    catalog = SceneAssetCatalog(CATALOG_PATH)
    with pytest.raises(
        AuthoredScenePreflightError,
        match="required_scene_fact_paths is absent",
    ):
        run_authored_scene_preflight(
            mode="offline",
            bundle=bundle,
            catalog=catalog,
            scene_freeze=_catalog_freeze(catalog),
        )


def test_all_12_preflight_ignores_non_authoritative_agent_geometry_cache(
    tmp_path: Path,
) -> None:
    private_copy = tmp_path / "private"
    shutil.copytree(PRIVATE_ROOT, private_copy)
    state_path = (
        private_copy
        / "initial_states/soarm101_p0_grasp_cube_hold.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["agent_input"]["objects"]["blue_cube"]["size_m"] = [0.5, 0.5, 0.5]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    bundle = _bundle(tmp_path, private_root=private_copy)
    catalog = SceneAssetCatalog(CATALOG_PATH)
    evidence = run_authored_scene_preflight(
        mode="offline",
        bundle=bundle,
        catalog=catalog,
        scene_freeze=_catalog_freeze(catalog),
    )
    assert evidence["authored_scene_checks"][
        "catalog_is_only_physical_fact_authority"
    ] is True


def test_all_12_preflight_rejects_nonfinite_unmaterialized_pose(
    tmp_path: Path,
) -> None:
    private_copy = tmp_path / "private"
    shutil.copytree(PRIVATE_ROOT, private_copy)
    state_path = private_copy / "initial_states/soarm101_p0_grasp_cube_hold.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["bodies"][0]["quaternion_wxyz"][3] = float("nan")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    bundle = _bundle(tmp_path, private_root=private_copy)
    catalog = SceneAssetCatalog(CATALOG_PATH)
    with pytest.raises(AuthoredScenePreflightError, match="non-finite value"):
        run_authored_scene_preflight(
            mode="aws",
            bundle=bundle,
            catalog=catalog,
            scene_freeze=_catalog_freeze(catalog),
        )
