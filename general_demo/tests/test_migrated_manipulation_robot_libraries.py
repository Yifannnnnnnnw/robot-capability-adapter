from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from autoadapter2.libraries import TasksLibrary


ROOT = Path(__file__).resolve().parents[1]
MORPHOLOGY_ROOT = ROOT / "libraries" / "morphology"
ASSET_ROOT = ROOT / "libraries" / "assets"
TASK_ROOT = ROOT / "libraries" / "tasks"

MIGRATIONS = (
    "franka_panda",
    "kuka_iiwa_14",
    "piper",
    "universal_robots_ur5e",
    "pushbench",
    "robotstudio_so101",
)

EXPECTED_RENDER_CAMERA = {
    "franka_panda": "free",
    "kuka_iiwa_14": "free",
    "piper": "free",
    "universal_robots_ur5e": "free",
    "pushbench": "overhead",
    "robotstudio_so101": "wrist_cam",
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _closure_digest(entries: list[dict]) -> str:
    # The manifest uses escaped separators so the digest is stable in compact JSON.
    escaped_nul = r"\0"
    material = r"\n".join(
        f"{entry['source_path']}{escaped_nul}{entry['sha256']}" for entry in entries
    ) + r"\n"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _xml_names(asset_dir: Path) -> tuple[set[str], set[str], set[str], set[str]]:
    joints: set[str] = set()
    actuators: set[str] = set()
    bodies: set[str] = set()
    sites: set[str] = set()
    for path in sorted(asset_dir.glob("*.xml")):
        root = ET.parse(path).getroot()
        joints.update(
            element.get("name", "")
            for element in root.iter("joint")
            if element.get("name")
        )
        actuators.update(
            element.get("name", "")
            for parent in root.iter("actuator")
            for element in parent
            if element.get("name")
        )
        bodies.update(
            element.get("name", "")
            for element in root.iter("body")
            if element.get("name")
        )
        sites.update(
            element.get("name", "")
            for element in root.iter("site")
            if element.get("name")
        )
    return joints, actuators, bodies, sites


def test_migrated_morphology_records_pin_source_and_closure() -> None:
    for configuration in MIGRATIONS:
        record_path = MORPHOLOGY_ROOT / configuration / "1.0.0" / "record.json"
        record = _read(record_path)
        assert record["record_type"] == "morphology"
        assert record["robot_configuration_id"] == configuration
        assert record["review_status"] == "HUMAN_REVIEW_REQUIRED"
        assert record["route"] == "DIRECT_MUJOCO_EXPERIMENTAL"
        assert "READY" not in json.dumps(record)
        assert "PASS" not in json.dumps(record)

        mujoco = record["mujoco"]
        assert mujoco["entrypoint"] == record["source"]["path"]
        assert not Path(mujoco["entrypoint"]).is_absolute()
        assert ".." not in Path(mujoco["entrypoint"]).parts
        assert mujoco["entrypoint"].startswith("assets/")
        local_entrypoint = ROOT / mujoco["local_entrypoint"].removeprefix("general_demo/")
        assert local_entrypoint.is_file()
        assert mujoco["reset"] == {"policy": "model_default"}
        assert set(mujoco["reset"]) == {"policy"}
        assert mujoco["sensor_names"] == []
        assert mujoco["render"] == {
            "camera": EXPECTED_RENDER_CAMERA[configuration],
            "width": 640,
            "height": 480,
            "fps": 30,
        }
        frames = mujoco["frames"]
        assert set(frames) == {"body_names", "site_names"}
        assert frames["body_names"] or frames["site_names"]
        asset_dir = ASSET_ROOT / configuration / "1.0.0"
        actual_joints, actual_actuators, actual_bodies, actual_sites = _xml_names(asset_dir)
        assert len(record["joint_names"]) == len(record["actuator_names"])
        assert set(record["joint_names"]) <= actual_joints
        assert set(record["actuator_names"]) <= actual_actuators
        assert set(frames["body_names"]) <= actual_bodies
        assert set(frames["site_names"]) <= actual_sites
        passive_joints = set(record.get("passive_joint_names", []))
        assert passive_joints <= actual_joints
        assert passive_joints.isdisjoint(record["joint_names"])
        assert record["actuated_dof"] == len(record["joint_names"])
        if mujoco["render"]["camera"] != "free":
            actual_cameras = {
                element.get("name", "")
                for path in sorted(asset_dir.glob("*.xml"))
                for element in ET.parse(path).getroot().iter("camera")
                if element.get("name")
            }
            assert mujoco["render"]["camera"] in actual_cameras
        closure_path = ROOT / mujoco["asset_closure_path"].removeprefix("general_demo/")
        closure = _read(closure_path)
        assert closure["source_commit"] == record["source"]["commit"]
        assert closure["robot_configuration_id"] == configuration
        assert closure["closure_sha256"] == _closure_digest(closure["entries"])
        assert mujoco["asset_closure_sha256"] == closure["closure_sha256"]

        source_entry = next(
            entry
            for entry in closure["entries"]
            if entry["source_path"] == record["source"]["path"]
        )
        assert source_entry["sha256"] == mujoco["source_sha256"]
        assert source_entry["kind"] == "mjcf"

        for entry in closure["entries"]:
            if entry["kind"] == "mjcf":
                vendored = asset_dir / entry["vendored_path"]
                assert vendored.is_file(), vendored
                assert hashlib.sha256(vendored.read_bytes()).hexdigest() == entry["sha256"]
            else:
                assert entry["vendored_path"] is None
        assert closure["mesh_materialization"] == "UPSTREAM_CACHE_REQUIRED"


def test_migrated_task_packages_have_five_closed_tasks_and_load() -> None:
    library = TasksLibrary(TASK_ROOT)
    for configuration in MIGRATIONS:
        package_dir = TASK_ROOT / configuration / "1.0.0"
        catalog = _read(package_dir / "catalog.json")
        collection = _read(package_dir / "demo_collection.json")
        projection = _read(package_dir / "stage1_projection.json")
        private = _read(package_dir / "evaluation_private.json")
        instances = _read(package_dir / "task_instances_private.json")

        task_ids = [task["task_id"] for task in catalog["tasks"]]
        assert len(task_ids) == 5
        assert collection["task_ids"] == task_ids
        assert [task["task_id"] for task in projection["tasks"]] == task_ids
        assert [criterion["task_id"] for criterion in private["criteria"]] == task_ids
        assert [instance["task_id"] for instance in instances["instances"]] == task_ids
        assert catalog["migration_review_status"] == "HUMAN_REVIEW_REQUIRED"
        assert collection["migration_review_status"] == "HUMAN_REVIEW_REQUIRED"
        assert private["review_status"] == "HUMAN_REVIEW_REQUIRED"
        assert instances["review_status"] == "HUMAN_REVIEW_REQUIRED"
        assert all(task["scene_entrypoint"] for task in catalog["tasks"])
        assert all(
            (ROOT / task["scene_entrypoint"].removeprefix("general_demo/")).is_file()
            for task in catalog["tasks"]
        )
        assert all(
            (ROOT / instance["scene_entrypoint"].removeprefix("general_demo/")).is_file()
            for instance in instances["instances"]
        )

        loaded = library.load(configuration, "1.0.0")
        assert len(loaded.stage1_projection(f"{configuration}-smoke")) == 5
        assert len(loaded.demo_public_tasks(f"{configuration}-smoke")) == 5
        assert len(loaded.demo_private_criteria()) == 5
