from __future__ import annotations

import copy
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from autoadapter2.foundation.errors import ContractError
from autoadapter2.libraries import TasksLibrary


ROOT = Path(__file__).parents[1]
MORPH_ROOT = ROOT / "libraries" / "morphology"
ASSET_ROOT = ROOT / "libraries" / "assets"
TASK_ROOT = ROOT / "libraries" / "tasks"
ROBOTS = ("anybotics-anymal-c", "h1", "skydio-x2", "unitree-a1")
SOURCE_COMMIT = "585eb1f1fde33f17f5f9a1e169a18dd41f97b586"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _closure_hash(rows: list[dict]) -> str:
    material = "".join(
        f"{row['path']}\t{row['sha256']}\n" for row in rows
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(material).hexdigest()}"


def _xml_file_refs(path: Path) -> list[str]:
    root = ET.parse(path).getroot()
    refs: list[str] = []
    for element in root.iter():
        if element.tag == "include" and element.get("file"):
            refs.append(element.get("file", ""))
        if element.tag in {"mesh", "texture"} and element.get("file"):
            value = element.get("file", "")
            refs.append(value if value.startswith("assets/") else f"assets/{value}")
    return refs


def _named_frames(path: Path) -> tuple[set[str], set[str]]:
    root = ET.parse(path).getroot()
    bodies = {
        element.get("name", "")
        for element in root.iter("body")
        if element.get("name")
    }
    sites = {
        element.get("name", "")
        for element in root.iter("site")
        if element.get("name")
    }
    return bodies, sites


@pytest.mark.parametrize("robot_id", ROBOTS)
def test_morphology_record_and_manifest_are_pinned_and_closed(robot_id: str) -> None:
    record = _json(MORPH_ROOT / robot_id / "1.0.0" / "record.json")
    manifest = _json(ASSET_ROOT / robot_id / "1.0.0" / "asset_manifest.json")

    assert record["route"] == "DIRECT_MUJOCO_EXPERIMENTAL"
    assert record["mujoco"]["version"] == "3.3.6"
    frames = record["mujoco"]["frames"]
    assert frames["body_names"]
    assert set(frames) == {"body_names", "site_names"}
    assert record["mujoco"]["render"] == {
        "camera": "free",
        "width": 640,
        "height": 480,
        "fps": 30,
    }
    assert record["source"]["commit"] == SOURCE_COMMIT
    assert record["source"]["path"].endswith("/scene.xml")
    assert record["mujoco"]["asset_closure_status"] == "MANIFEST_VERIFIED_NOT_VENDORED"
    assert record["mujoco"]["asset_manifest_ref"].endswith("asset_manifest.json")
    assert manifest["source"]["commit"] == SOURCE_COMMIT
    assert manifest["route"] == "DIRECT_MUJOCO_EXPERIMENTAL"
    assert manifest["vendor_policy"] == "NOT_VENDORED"
    assert manifest["closure_sha256"] == _closure_hash(manifest["files"])

    local_scene = ASSET_ROOT / robot_id / "1.0.0" / "scene.xml"
    local_model = ASSET_ROOT / robot_id / "1.0.0" / Path(
        manifest["source"]["included_model_path"]
    ).name
    assert local_scene.exists()
    assert local_model.exists()
    actual_bodies, actual_sites = _named_frames(local_model)
    assert set(frames["body_names"]) <= actual_bodies
    assert set(frames["site_names"]) <= actual_sites
    if robot_id == "anybotics-anymal-c":
        assert record["mujoco"]["reset"] == {"policy": "model_default"}
    assert hashlib.sha256(local_scene.read_bytes()).hexdigest() == manifest["entrypoint"]["sha256"]
    assert hashlib.sha256(local_model.read_bytes()).hexdigest() == next(
        row["sha256"]
        for row in manifest["files"]
        if row["path"] == Path(manifest["source"]["included_model_path"]).name
    )

    refs = _xml_file_refs(local_scene) + _xml_file_refs(local_model)
    manifest_paths = {row["path"] for row in manifest["files"]}
    assert set(refs) <= manifest_paths
    assert set(manifest_paths) >= {
        "scene.xml",
        Path(manifest["source"]["included_model_path"]).name,
    }
    assert not any(
        (ASSET_ROOT / robot_id / "1.0.0" / row["path"]).exists()
        for row in manifest["files"]
        if row["path"].startswith("assets/")
    )
    assert record["mujoco"]["asset_closure_sha256"] == manifest["closure_sha256"]


@pytest.mark.parametrize("robot_id", ROBOTS)
def test_each_experimental_task_package_is_closed_and_not_formal_demo_approved(
    robot_id: str,
) -> None:
    package = TASK_ROOT / robot_id / "1.0.0"
    catalog = _json(package / "catalog.json")
    projection = _json(package / "stage1_projection.json")
    private = _json(package / "evaluation_private.json")
    collection = _json(package / "demo_collection.json")
    instances = _json(package / "task_instances_private.json")

    assert len(catalog["tasks"]) >= 5
    assert len(catalog["tasks"]) == 5
    assert catalog["route"] == "DIRECT_MUJOCO_EXPERIMENTAL"
    assert catalog["catalog_review_status"] == "HUMAN_REVIEW_REQUIRED"
    assert catalog["formal_demo_status"] == "NOT_FORMAL_DEMO_APPROVED"
    assert collection["review_status"] == "HUMAN_REVIEW_REQUIRED"
    assert collection["formal_demo_status"] == "NOT_FORMAL_DEMO_APPROVED"
    assert private["review_status"] == "HUMAN_REVIEW_REQUIRED"
    assert instances["review_status"] == "HUMAN_REVIEW_REQUIRED"

    catalog_ids = [task["task_id"] for task in catalog["tasks"]]
    projection_ids = [task["task_id"] for task in projection["tasks"]]
    private_ids = [criterion["task_id"] for criterion in private["criteria"]]
    instance_ids = [instance["task_id"] for instance in instances["instances"]]
    assert catalog_ids == projection_ids == private_ids == instance_ids
    assert collection["task_ids"] == catalog_ids
    assert all(set(task) == {"task_id", "description"} for task in projection["tasks"])
    assert all(
        task["route"] == "DIRECT_MUJOCO_EXPERIMENTAL"
        and task["threshold_status"] == "HUMAN_REVIEW_REQUIRED"
        for task in catalog["tasks"]
    )
    assert all(
        criterion["criterion_status"] == "HUMAN_REVIEW_REQUIRED"
        and criterion["threshold_status"] == "HUMAN_REVIEW_REQUIRED"
        and criterion["execution_status"] == "EXECUTABLE_EXPERIMENTAL"
        for criterion in private["criteria"]
    )
    assert all(
        instance["parameters"]["threshold_status"] == "HUMAN_REVIEW_REQUIRED"
        for instance in instances["instances"]
    )
    assert private["forbidden_recipients"] == [
        "stage1",
        "stage2",
        "generated_capability_layer",
        "repair",
        "demo_consumer",
    ]


def _write_reviewed_loader_view(root: Path, robot_id: str) -> None:
    package = root / robot_id / "1.0.0"
    catalog = _json(package / "catalog.json")
    collection = _json(package / "demo_collection.json")
    private = _json(package / "evaluation_private.json")
    for task in catalog["tasks"]:
        task["task_review_status"] = "HUMAN_APPROVED"
    catalog["catalog_review_status"] = "HUMAN_APPROVED"
    collection["review_status"] = "HUMAN_APPROVED"
    for criterion in private["criteria"]:
        criterion["criterion_status"] = "HUMAN_APPROVED"
    (package / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    (package / "demo_collection.json").write_text(json.dumps(collection), encoding="utf-8")
    (package / "evaluation_private.json").write_text(json.dumps(private), encoding="utf-8")


def test_current_tasks_loader_can_load_only_an_explicitly_reviewed_copy(
    tmp_path: Path,
) -> None:
    reviewed_root = tmp_path / "tasks"
    reviewed_root.mkdir()
    records = []
    for robot_id in ROBOTS:
        shutil.copytree(TASK_ROOT / robot_id, reviewed_root / robot_id)
        _write_reviewed_loader_view(reviewed_root, robot_id)
        catalog = _json(reviewed_root / robot_id / "1.0.0" / "catalog.json")
        records.append(
            {
                "robot_configuration_id": robot_id,
                "catalog_id": catalog["catalog_id"],
                "catalog_version": "1.0.0",
                "task_count": 5,
                "human_approved_task_count": 5,
                "candidate_task_count": 0,
                "fixed_demo_task_count": 5,
                "catalog_path": f"{robot_id}/1.0.0/catalog.json",
                "demo_collection_path": f"{robot_id}/1.0.0/demo_collection.json",
                "stage1_projection_path": f"{robot_id}/1.0.0/stage1_projection.json",
                "evaluation_private_path": f"{robot_id}/1.0.0/evaluation_private.json",
            }
        )
    index = {
        "artifact_type": "tasks_library_index",
        "schema_version": "1.0.0",
        "catalogs": records,
    }
    (reviewed_root / "index.json").write_text(json.dumps(index), encoding="utf-8")

    for robot_id in ROBOTS:
        package = TasksLibrary(reviewed_root).load(robot_id, "1.0.0")
        assert len(package.stage1_projection(f"loader-{robot_id}")) == 5


def test_unreviewed_package_is_not_admitted_by_current_loader(tmp_path: Path) -> None:
    reviewed_root = tmp_path / "tasks"
    reviewed_root.mkdir()
    robot_id = ROBOTS[0]
    shutil.copytree(TASK_ROOT / robot_id, reviewed_root / robot_id)
    catalog = _json(reviewed_root / robot_id / "1.0.0/catalog.json")
    (reviewed_root / "index.json").write_text(
        json.dumps(
            {
                "artifact_type": "tasks_library_index",
                "schema_version": "1.0.0",
                "catalogs": [
                    {
                        "robot_configuration_id": robot_id,
                        "catalog_id": catalog["catalog_id"],
                        "catalog_version": "1.0.0",
                        "task_count": 5,
                        "human_approved_task_count": 5,
                        "candidate_task_count": 0,
                        "fixed_demo_task_count": 5,
                        "catalog_path": f"{robot_id}/1.0.0/catalog.json",
                        "demo_collection_path": f"{robot_id}/1.0.0/demo_collection.json",
                        "stage1_projection_path": f"{robot_id}/1.0.0/stage1_projection.json",
                        "evaluation_private_path": f"{robot_id}/1.0.0/evaluation_private.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContractError):
        TasksLibrary(reviewed_root).load(robot_id, "1.0.0")
