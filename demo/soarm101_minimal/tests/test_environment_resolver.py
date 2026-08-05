from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from soarm_demo.audit import atomic_write_json, sha256_file, sha256_json
from soarm_demo.bridge.scene_catalog import SceneAssetCatalog
from soarm_demo.environment_resolver import (
    EnvironmentResolutionError,
    resolve_generation_environment,
    verify_generation_environment_freeze,
    verify_robot_model_freeze,
)


def _four_libraries(root: Path) -> dict[str, Path]:
    return {
        "morphology": root / "libraries/morphology/soarm101/v1",
        "sdk_runtime": root / "libraries/sdk_runtime/lerobot_soarm101/0.6.0",
        "tasks": root / "libraries/tasks/soarm101_tabletop/v1",
        "experience": root / "libraries/experience/v1",
    }


def _resolve(root: Path, tmp_path: Path):
    return resolve_generation_environment(
        _four_libraries(root),
        morphology_scene=(
            root / "libraries/morphology/scenes/soarm101_tabletop/v1"
        ),
        libraries_root=root / "libraries",
        output_path=tmp_path / "generation_environment_freeze.json",
        input_snapshot_root=tmp_path / "input_snapshot",
        schemas_root=root / "schemas",
    )


def test_resolver_freezes_exact_four_library_topology_and_public_scene(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    result = _resolve(root, tmp_path)
    envelope = json.loads(result.freeze_path.read_text(encoding="utf-8"))
    manifest = envelope["manifest"]

    assert envelope["manifest_sha256"] == sha256_json(manifest)
    assert manifest["selection"]["top_level_library_types"] == [
        "experience",
        "morphology",
        "sdk_runtime",
        "tasks",
    ]
    assert set(manifest["bindings"]) == {
        "morphology",
        "sdk_runtime",
        "tasks",
        "experience",
    }
    assert manifest["public_simulation"]["privacy_class"] == (
        "public_generation_sandbox"
    )
    assert manifest["public_simulation"]["not_task_instance"] is True
    assert manifest["asset_resolution"] == {
        "source": "morphology_scene_catalog",
        "materialization": "on_demand",
        "unreferenced_assets_materialized": False,
        "missing_asset_result": "MISSING_ASSET",
    }
    serialized = json.dumps(envelope, sort_keys=True).lower()
    for forbidden in (
        "task_oracles",
        "heldout_tasks",
        "initial_states",
        "demo_batch",
        "similarity_matrix",
        '"seed"',
        '"oracle_ref"',
    ):
        assert forbidden not in serialized
    assert not any(
        Path(binding["path"]).is_absolute()
        for group in manifest["bindings"].values()
        for binding in group.values()
    )
    robot_model = manifest["bindings"]["morphology"]["robot_model"]
    assert robot_model["model_format"] == "mujoco_mjcf"
    assert len(robot_model["referenced_files"]) == 18
    assert {item["kind"] for item in robot_model["referenced_files"]} == {"mesh"}
    assert len({item["reference"] for item in robot_model["referenced_files"]}) == 18
    assert all(
        not Path(item["path"]).is_absolute()
        for item in robot_model["referenced_files"]
    )
    assert verify_generation_environment_freeze(
        result.freeze_path,
        expected_sha256=result.freeze_sha256,
        libraries_root=root / "libraries",
        rehash_sources=True,
    )["ok"]

    scene_snapshot = result.scene_snapshot_root
    assert scene_snapshot is not None
    assert (scene_snapshot / "scene.yaml").is_file()
    assert (scene_snapshot / "assets/primitive_catalog.yaml").is_file()
    assert (scene_snapshot / "snapshot.json").is_file()


def test_resolver_rejects_a_fifth_or_missing_top_level_library(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    libraries = _four_libraries(root)
    libraries["scene"] = root / "libraries/morphology/scenes/soarm101_tabletop/v1"
    with pytest.raises(EnvironmentResolutionError, match="exactly morphology"):
        resolve_generation_environment(
            libraries,
            morphology_scene=libraries["scene"],
            libraries_root=root / "libraries",
            output_path=tmp_path / "fifth.json",
        )
    libraries = _four_libraries(root)
    del libraries["experience"]
    with pytest.raises(EnvironmentResolutionError, match="exactly morphology"):
        resolve_generation_environment(
            libraries,
            morphology_scene=(
                root / "libraries/morphology/scenes/soarm101_tabletop/v1"
            ),
            libraries_root=root / "libraries",
            output_path=tmp_path / "missing.json",
        )


def test_agent_scene_facts_are_derived_from_asset_ref_not_agent_input_geometry() -> None:
    root = Path(__file__).resolve().parents[1]
    catalog = SceneAssetCatalog(
        root
        / "libraries/morphology/scenes/soarm101_tabletop/v1/assets/primitive_catalog.yaml"
    )
    instance = {
        "bodies": [
            {
                "asset_ref": "morphology.scene_asset/cube_34mm_25g@1.0.0#blue",
                "id": "blue_cube",
                "position_m": [0.31, 0.04, 0.037],
                "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
            }
        ],
        "markers": [],
        "agent_input": {
            "coordinate_frame": "robot_base",
            "position_unit": "m",
            "objects": {
                "blue_cube": {
                    "position_m": [9.0, 9.0, 9.0],
                    "size_m": [9.0, 9.0, 9.0],
                    "grasp_axis": [0.0, 1.0, 0.0],
                }
            },
            "goals": {"height_delta_m": 0.08},
        },
    }

    facts = catalog.derive_agent_input(instance)

    assert facts["objects"]["blue_cube"] == {
        "position_m": [0.31, 0.04, 0.037],
        "size_m": [0.034, 0.034, 0.034],
        "grasp_axis": [0.0, 1.0, 0.0],
    }
    assert facts["goals"] == {"height_delta_m": 0.08}
def test_verifier_detects_rebound_source_hash(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    result = _resolve(root, tmp_path)
    envelope = json.loads(result.freeze_path.read_text(encoding="utf-8"))
    envelope["manifest"]["bindings"]["morphology"]["scene_asset_catalog"][
        "sha256"
    ] = "0" * 64
    envelope["manifest_sha256"] = sha256_json(envelope["manifest"])
    rebound = tmp_path / "rebound.json"
    atomic_write_json(rebound, envelope)
    with pytest.raises(EnvironmentResolutionError, match="source drifted"):
        verify_generation_environment_freeze(
            rebound,
            libraries_root=root / "libraries",
            rehash_sources=True,
        )


def test_verifier_detects_rebound_asset_variant_descriptor(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    result = _resolve(root, tmp_path)
    envelope = json.loads(result.freeze_path.read_text(encoding="utf-8"))
    descriptors = envelope["manifest"]["bindings"]["morphology"][
        "scene_asset_catalog"
    ]["asset_descriptor_sha256"]
    first_ref = sorted(descriptors)[0]
    descriptors[first_ref] = "0" * 64
    envelope["manifest_sha256"] = sha256_json(envelope["manifest"])
    rebound = tmp_path / "descriptor-rebound.json"
    atomic_write_json(rebound, envelope)
    with pytest.raises(EnvironmentResolutionError, match="descriptor bindings drifted"):
        verify_generation_environment_freeze(
            rebound,
            libraries_root=root / "libraries",
            rehash_sources=True,
        )


def test_robot_model_verifier_rejects_an_omitted_mjcf_resource(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    result = _resolve(root, tmp_path)
    envelope = json.loads(result.freeze_path.read_text(encoding="utf-8"))
    referenced = envelope["manifest"]["bindings"]["morphology"][
        "robot_model"
    ]["referenced_files"]
    assert len(referenced) == 18
    referenced.pop()
    envelope["manifest_sha256"] = sha256_json(envelope["manifest"])

    with pytest.raises(EnvironmentResolutionError, match="resource closure mismatch"):
        verify_robot_model_freeze(
            envelope,
            root / "libraries/morphology/soarm101/v1/model/so101.xml",
        )


def test_robot_model_verifier_rejects_a_symlinked_mjcf_resource(
    tmp_path: Path,
) -> None:
    libraries = tmp_path / "libraries"
    model = libraries / "morphology/generic/v1/model/robot.xml"
    meshes = model.parent / "meshes"
    meshes.mkdir(parents=True)
    target = meshes / "actual.stl"
    target.write_bytes(b"solid actual\nendsolid actual\n")
    selected = meshes / "selected.stl"
    selected.symlink_to(target.name)
    model.write_text(
        "<mujoco model='generic'><compiler meshdir='meshes'/>"
        "<asset><mesh file='selected.stl'/></asset><worldbody/></mujoco>",
        encoding="utf-8",
    )
    manifest = {
        "bindings": {
            "morphology": {
                "robot_model": {
                    "path": "morphology/generic/v1/model/robot.xml",
                    "sha256": sha256_file(model),
                    "robot_id": "generic",
                    "version": "v1",
                    "model_format": "mujoco_mjcf",
                    "referenced_files": [
                        {
                            "kind": "mesh",
                            "reference": "meshes/selected.stl",
                            "path": (
                                "morphology/generic/v1/model/meshes/selected.stl"
                            ),
                            "sha256": sha256_file(target),
                        }
                    ],
                }
            }
        }
    }
    freeze = {
        "schema_version": "robot_capability.generation_environment_freeze.v1",
        "manifest": manifest,
        "manifest_sha256": sha256_json(manifest),
    }

    with pytest.raises(EnvironmentResolutionError, match="may not use symlinks"):
        verify_robot_model_freeze(freeze, model)


@pytest.mark.parametrize("library_name", ["tasks", "experience"])
def test_resolver_rejects_noncanonical_runtime_id_in_every_consuming_manifest(
    tmp_path: Path,
    library_name: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    copied_root = tmp_path / library_name / "project"
    shutil.copytree(root / "libraries", copied_root / "libraries")
    libraries = _four_libraries(copied_root)
    manifest_path = libraries[library_name] / "manifest.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "lerobot_soarm101_0_6_0", "lerobot_soarm101_0.6.0"
        ),
        encoding="utf-8",
    )

    with pytest.raises(EnvironmentResolutionError, match="runtime_ids"):
        resolve_generation_environment(
            libraries,
            morphology_scene=(
                copied_root
                / "libraries/morphology/scenes/soarm101_tabletop/v1"
            ),
            libraries_root=copied_root / "libraries",
            output_path=tmp_path / library_name / "freeze.json",
            schemas_root=root / "schemas",
        )


def test_catalog_profiles_cover_all_twelve_private_task_scene_geometries() -> None:
    """Framework-side coverage only; no private instance enters the freeze."""

    root = Path(__file__).resolve().parents[1]
    catalog = SceneAssetCatalog(
        root
        / "libraries/morphology/scenes/soarm101_tabletop/v1/assets/primitive_catalog.yaml"
    )
    task_instances = (
        root / "private/task_library/soarm101_tabletop/v1/initial_states"
    )
    instance_paths = sorted(
        path for path in task_instances.glob("*.json") if path.name != "_common_reset.json"
    )
    assert len(instance_paths) == 12
    resolved_refs: set[str] = set()
    for path in instance_paths:
        instance = json.loads(path.read_text(encoding="utf-8"))
        for body in instance["bodies"]:
            kind = body.get("kind")
            asset_ref = str(body.get("asset_ref", ""))
            role = (
                "receptacle"
                if kind in {"tray", "bowl"}
                or "/tray_" in asset_ref
                or "/bowl_" in asset_ref
                else "dynamic_object"
            )
            resolved_refs.add(catalog.resolve(body, expected_role=role).asset_ref)
        for marker in instance["markers"]:
            resolved_refs.add(catalog.resolve(marker, expected_role="marker").asset_ref)

    # Both authored geometry sizes and every task-relevant visual distinction
    # are represented by immutable reusable profiles/variants.
    expected_fragments = {
        "cube_30mm_20g@1.0.0#red",
        "cube_30mm_20g@1.0.0#blue",
        "cube_34mm_25g@1.0.0#red",
        "cube_34mm_25g@1.0.0#blue",
        "cube_34mm_25g@1.0.0#green",
        "cube_34mm_25g@1.0.0#yellow",
        "cube_34mm_25g@1.0.0#purple",
        "cylinder_r13_h36_18g@1.0.0#blue",
        "cylinder_r15_h40_22g@1.0.0#blue",
        "cylinder_r15_h40_22g@1.0.0#orange",
        "tray_75x65_rim12@1.0.0#red",
        "tray_75x65_rim12@1.0.0#blue",
        "tray_90x80_rim12@1.0.0#generic",
        "tray_100x100_rim12@1.0.0#generic",
        "bowl_r45_rim25@1.0.0#purple",
        "planar_circle_r25@1.0.0#green",
        "planar_circle_r30@1.0.0#green",
        "pose_target_r8@1.0.0#green",
    }
    assert expected_fragments == {ref.split("/", 1)[1] for ref in resolved_refs}
