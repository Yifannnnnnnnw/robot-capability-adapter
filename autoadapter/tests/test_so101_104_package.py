from __future__ import annotations

import json
from pathlib import Path

import mujoco

from autoadapter2.geometry_gate import validate_so101_geometry
from autoadapter2.libraries import load_robot_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "robotstudio_so101" / "1.0.4"


def _document(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_so101_104_is_self_contained_and_all_catalog_scenes_load() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    assert package.package_version == "1.0.4"

    metadata_paths = (
        PACKAGE_ROOT / "morphology.json",
        PACKAGE_ROOT / "tasks" / "catalog.json",
        PACKAGE_ROOT / "tasks" / "private" / "bindings.json",
        PACKAGE_ROOT / "tasks" / "private" / "guards.json",
        PACKAGE_ROOT / "tasks" / "private" / "instances.json",
        PACKAGE_ROOT / "tasks" / "sources.json",
    )
    for path in metadata_paths:
        assert _document(path)["package_version"] == "1.0.4"

    instances = _document(PACKAGE_ROOT / "tasks" / "private" / "instances.json")[
        "instances"
    ]
    scene_paths = {PACKAGE_ROOT / instance["scene_entrypoint"] for instance in instances}
    assert len(scene_paths) == 17
    for scene_path in scene_paths:
        assert scene_path.is_file()
        model = mujoco.MjModel.from_xml_path(str(scene_path))
        assert model.nbody > 0

def test_so101_104_preserves_dial_and_merges_wall_and_lever_fixtures() -> None:
    base = ROOT / "libraries" / "robots" / "robotstudio_so101"
    assert (base / "1.0.4" / "assets" / "dial_scene.xml").read_bytes() == (
        base / "1.0.3" / "assets" / "dial_scene.xml"
    ).read_bytes()
    for scene_name in ("pick_place_wall_scene.xml", "lever_scene.xml"):
        assert (base / "1.0.4" / "assets" / scene_name).read_bytes() == (
            base / "1.0.1" / "assets" / scene_name
        ).read_bytes()

    instances = _document(PACKAGE_ROOT / "tasks" / "private" / "instances.json")[
        "instances"
    ]
    by_task = {instance["task_id"]: instance for instance in instances}
    assert by_task["mw_lever_pull"]["public_arguments"]["request"][
        "task_parameters"
    ]["contact_position"] == [0.327, 0.12, 0.30]


def test_so101_104_geometry_gate_covers_strict_reach_ik_and_limits() -> None:
    result = validate_so101_geometry(PACKAGE_ROOT)
    assert result.passed
    for task_id in ("mw_pick_place_wall", "mw_lever_pull"):
        assert result.margins[f"{task_id}.strict_chain_margin_m"] >= 0.0
        assert result.margins[f"{task_id}.worst_ik_residual_m"] <= 0.005
        assert result.margins[f"{task_id}.worst_joint_limit_margin_deg"] >= 5.0
