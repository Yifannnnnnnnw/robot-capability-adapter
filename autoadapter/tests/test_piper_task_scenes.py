from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.harness.session import apply_framework_reset


ROOT = Path(__file__).resolve().parents[1]
XARM_ASSETS_ROOT = ROOT / "libraries" / "robots" / "ufactory_xarm7" / "1.0.0" / "assets"
PIPER_PACKAGE_ROOT = ROOT / "libraries" / "robots" / "piper" / "1.0.0"
PIPER_ASSETS_ROOT = PIPER_PACKAGE_ROOT / "assets"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"

SCENES = {
    "reach_scene.xml": {
        "dimensions": (8, 8, 7),
        "bodies": ("reach_goal",),
        "geoms": (),
        "cameras": (),
        "sites": (),
    },
    "push_to_goal_scene.xml": {
        "dimensions": (15, 14, 7),
        "bodies": ("workpiece", "push_goal", "evidence_target"),
        "geoms": ("work_surface",),
        "cameras": ("evidence",),
        "sites": (),
    },
    "pick_place_scene.xml": {
        "dimensions": (15, 14, 7),
        "bodies": ("workpiece", "pick_place_goal", "evidence_target"),
        "geoms": ("work_surface", "goal_pedestal"),
        "cameras": ("evidence",),
        "sites": ("workpiece_center",),
    },
    "pick_place_wall_scene.xml": {
        "dimensions": (15, 14, 7),
        "bodies": ("workpiece", "wall_pick_goal", "evidence_target"),
        "geoms": ("work_surface", "wall", "wall_pick_goal_pedestal"),
        "cameras": ("evidence",),
        "sites": ("workpiece_center",),
    },
    "wall_scene.xml": {
        "dimensions": (15, 14, 7),
        "model": "piper_wall_obstacle",
        "bodies": ("workpiece", "wall_push_goal", "evidence_target"),
        "geoms": ("work_surface", "wall"),
        "cameras": ("evidence",),
        "sites": ("workpiece_center",),
    },
    "sweep_into_goal_scene.xml": {
        "dimensions": (15, 14, 7),
        "bodies": ("workpiece", "sweep_goal", "evidence_target"),
        "geoms": ("table_left", "table_right", "table_front", "table_back", "goal_catch"),
        "cameras": ("evidence",),
        "sites": ("sweep_goal_site",),
    },
    "drawer_scene.xml": {
        "dimensions": (9, 9, 7),
        "bodies": ("drawer", "drawer_handle", "evidence_target"),
        "geoms": ("work_surface", "drawer_housing", "drawer_front", "drawer_handle_geom"),
        "cameras": ("evidence",),
        "sites": ("drawer_handle_site",),
    },
    "button_front_scene.xml": {
        "dimensions": (9, 9, 7),
        "bodies": ("front_button", "evidence_target"),
        "geoms": ("work_surface", "button_housing", "front_button_geom"),
        "cameras": ("evidence",),
        "sites": ("front_button_site",),
    },
    "button_topdown_scene.xml": {
        "dimensions": (9, 9, 7),
        "bodies": ("top_button", "evidence_target"),
        "geoms": ("work_surface", "top_button_housing", "top_button_geom"),
        "cameras": ("evidence",),
        "sites": ("top_button_site",),
    },
    "handle_vertical_scene.xml": {
        "dimensions": (9, 9, 7),
        "model": "piper_vertical_handle",
        "bodies": ("vertical_handle", "evidence_target"),
        "geoms": ("work_surface", "handle_housing", "vertical_handle_geom"),
        "cameras": ("evidence",),
        "sites": ("vertical_handle_site",),
    },
    "door_scene.xml": {
        "dimensions": (9, 9, 7),
        "bodies": ("door", "door_panel", "door_handle", "evidence_target"),
        "joints": ("door_hinge",),
        "geoms": ("work_surface", "door_frame", "door_panel_geom", "door_handle_geom"),
        "cameras": ("evidence",),
        "sites": ("door_handle_site",),
    },
    "faucet_scene.xml": {
        "dimensions": (9, 9, 7),
        "bodies": ("faucet_handle", "faucet_tip", "evidence_target"),
        "joints": ("faucet_hinge",),
        "geoms": ("work_surface", "faucet_base", "faucet_arm", "faucet_tip_geom"),
        "cameras": ("evidence",),
        "sites": ("faucet_tip_site",),
    },
    "dial_scene.xml": {
        "dimensions": (9, 9, 7),
        "bodies": ("dial", "dial_tip", "evidence_target"),
        "joints": ("dial_hinge",),
        "geoms": ("work_surface", "dial_mount", "dial_face", "dial_tip_geom"),
        "cameras": ("evidence",),
        "sites": ("dial_tip_site",),
    },
    "lever_scene.xml": {
        "dimensions": (9, 9, 7),
        "bodies": ("lever", "lever_tip", "evidence_target"),
        "joints": ("lever_hinge",),
        "geoms": ("work_surface", "lever_base", "lever_arm", "lever_tip_geom"),
        "cameras": ("evidence",),
        "sites": ("lever_tip_site",),
    },
    "peg_insertion_side_scene.xml": {
        "dimensions": (15, 14, 7),
        "bodies": ("workpiece", "peg_goal", "evidence_target"),
        "geoms": ("work_surface", "hole_left", "hole_right", "hole_bottom", "hole_top"),
        "cameras": ("evidence",),
        "sites": ("peg_head_site",),
    },
    "bin_picking_scene.xml": {
        "dimensions": (15, 14, 7),
        "bodies": ("workpiece", "bin_goal", "evidence_target"),
        "geoms": (
            "work_surface",
            "bin_bottom",
            "bin_left",
            "bin_right",
            "bin_front",
            "bin_back",
            "goal_bin_bottom",
            "goal_bin_left",
            "goal_bin_right",
            "goal_bin_front",
            "goal_bin_back",
        ),
        "cameras": ("evidence",),
        "sites": ("workpiece_center",),
    },
    "pick_out_of_hole_scene.xml": {
        "dimensions": (15, 14, 7),
        "bodies": ("workpiece", "extraction_goal", "evidence_target"),
        "geoms": (
            "work_surface",
            "hole_platform_left",
            "hole_platform_right",
            "hole_platform_front",
            "hole_platform_back",
            "extraction_goal_pedestal",
        ),
        "cameras": ("evidence",),
        "sites": ("workpiece_center",),
    },
}


def _has_name(model: mujoco.MjModel, object_type: mujoco.mjtObj, name: str) -> bool:
    return mujoco.mj_name2id(model, object_type, name) >= 0


def _prescribed_transform(source_path: Path) -> str:
    transformed = source_path.read_text(encoding="utf-8").replace(
        'model="xarm7_', 'model="piper_', 1
    ).replace(
        '<include file="xarm7.xml" />', '<include file="piper.xml" />', 1
    )
    if source_path.name == "pick_place_scene.xml":
        transformed = transformed.replace(
            'pos="0.1 0 0.25" rgba=',
            'pos="0.1 0 0.25" contype="2" conaffinity="2" rgba=',
            1,
        )
    return transformed


def test_piper_scenes_are_exact_structural_transforms_of_xarm_sources() -> None:
    for filename, expected in SCENES.items():
        source_path = XARM_ASSETS_ROOT / filename
        scene_path = PIPER_ASSETS_ROOT / filename
        source_text = source_path.read_text(encoding="utf-8")
        scene_text = scene_path.read_text(encoding="utf-8")

        assert scene_text == _prescribed_transform(source_path)
        root = ET.fromstring(scene_text)
        expected_model = expected.get("model", f"piper_{filename.removesuffix('_scene.xml')}")
        assert root.get("model") == expected_model
        includes = root.findall("include")
        assert len(includes) == 1
        assert includes[0].get("file") == "piper.xml"
        assert "xarm7" not in scene_text.lower()
        assert source_text != scene_text


def test_piper_scenes_load_step_and_expose_structural_contract() -> None:
    assert mujoco.__version__ == "3.3.6"

    for filename, expected in SCENES.items():
        scene_path = PIPER_ASSETS_ROOT / filename
        root = ET.parse(scene_path).getroot()
        global_visual = root.find("visual/global")
        assert global_visual is not None
        assert int(global_visual.get("offwidth", "0")) == 800
        assert int(global_visual.get("offheight", "0")) == 600

        model = mujoco.MjModel.from_xml_path(str(scene_path))
        assert (model.nq, model.nv, model.nu) == expected["dimensions"]
        assert model.vis.global_.offwidth == 800
        assert model.vis.global_.offheight == 600

        base_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base_link")
        assert base_id >= 0
        np.testing.assert_allclose(model.body_pos[base_id], [0.0, 0.0, 0.0])

        for name in expected["bodies"]:
            assert _has_name(model, mujoco.mjtObj.mjOBJ_BODY, name)
        for name in expected["geoms"]:
            assert _has_name(model, mujoco.mjtObj.mjOBJ_GEOM, name)
        for name in expected.get("joints", ()):
            assert _has_name(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        for name in expected["cameras"]:
            assert _has_name(model, mujoco.mjtObj.mjOBJ_CAMERA, name)
        for name in expected["sites"]:
            assert _has_name(model, mujoco.mjtObj.mjOBJ_SITE, name)
        assert _has_name(model, mujoco.mjtObj.mjOBJ_SITE, "ee_site")
        if filename == "reach_scene.xml":
            assert _has_name(model, mujoco.mjtObj.mjOBJ_SITE, "reach_goal_site")

        data = mujoco.MjData(model)
        mujoco.mj_step(model, data)
        assert data.time > 0.0
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.ctrl).all()


def test_piper_pick_place_floor_does_not_push_the_arm_at_reset() -> None:
    instances = json.loads(
        (PIPER_PACKAGE_ROOT / "tasks" / "private" / "instances.json").read_text(
            encoding="utf-8"
        )
    )["instances"]
    instance = next(item for item in instances if item["task_id"] == "mw_pick_place")
    model = mujoco.MjModel.from_xml_path(
        str(PIPER_PACKAGE_ROOT / instance["scene_entrypoint"])
    )
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, instance["reset"])

    floor_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
    assert model.geom_contype[floor_id] == 2
    assert model.geom_conaffinity[floor_id] == 2
    robot_body_ids = {
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        for name in ("base_link", *(f"link{index}" for index in range(1, 9)))
    }
    for index in range(data.ncon):
        contact = data.contact[index]
        if floor_id not in {contact.geom1, contact.geom2}:
            continue
        other_geom = contact.geom2 if contact.geom1 == floor_id else contact.geom1
        assert int(model.geom_bodyid[other_geom]) not in robot_body_ids

    arm_addresses = [
        int(
            model.jnt_qposadr[
                mujoco.mj_name2id(
                    model, mujoco.mjtObj.mjOBJ_JOINT, f"joint{index}"
                )
            ]
        )
        for index in range(1, 7)
    ]
    ee_site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "ee_site")
    initial_arm = data.qpos[arm_addresses].copy()
    initial_ee = data.site_xpos[ee_site_id].copy()
    for _ in range(100):
        mujoco.mj_step(model, data)

    np.testing.assert_allclose(data.qpos[arm_addresses], initial_arm, atol=1e-10)
    np.testing.assert_allclose(data.site_xpos[ee_site_id], initial_ee, atol=1e-10)


def test_piper_task_scene_package_remains_non_runtime() -> None:
    runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
    assert "piper" not in runnable_index["robots"]
    assert (PIPER_PACKAGE_ROOT / "tasks" / "private").is_dir()
    assert not (PIPER_PACKAGE_ROOT / "skeleton").exists()
    assert not (PIPER_PACKAGE_ROOT / "reference").exists()
