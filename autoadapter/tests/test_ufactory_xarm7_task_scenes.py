from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "ufactory_xarm7" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"

SCENE_NAMES = (
    "reach_scene.xml",
    "push_to_goal_scene.xml",
    "pick_place_scene.xml",
    "pick_place_wall_scene.xml",
    "wall_scene.xml",
    "sweep_into_goal_scene.xml",
    "drawer_scene.xml",
    "button_front_scene.xml",
    "button_topdown_scene.xml",
    "handle_vertical_scene.xml",
    "door_scene.xml",
    "faucet_scene.xml",
    "dial_scene.xml",
    "lever_scene.xml",
    "peg_insertion_side_scene.xml",
    "bin_picking_scene.xml",
    "pick_out_of_hole_scene.xml",
)

FREE_OBJECT_SCENES = {
    "bin_picking_scene.xml",
    "peg_insertion_side_scene.xml",
    "pick_out_of_hole_scene.xml",
    "pick_place_scene.xml",
    "pick_place_wall_scene.xml",
    "push_to_goal_scene.xml",
    "sweep_into_goal_scene.xml",
    "wall_scene.xml",
}

FIXTURE_JOINT_SCENES = {
    "button_front_scene.xml",
    "button_topdown_scene.xml",
    "dial_scene.xml",
    "door_scene.xml",
    "drawer_scene.xml",
    "faucet_scene.xml",
    "handle_vertical_scene.xml",
    "lever_scene.xml",
}

TASK_SYMBOLS = {
    "bin_picking_scene.xml": {"bodies": ("workpiece", "bin_goal"), "sites": ("workpiece_center",)},
    "button_front_scene.xml": {
        "bodies": ("front_button",),
        "joints": ("front_button_slide",),
        "sites": ("front_button_site",),
    },
    "button_topdown_scene.xml": {
        "bodies": ("top_button",),
        "joints": ("top_button_slide",),
        "sites": ("top_button_site",),
    },
    "dial_scene.xml": {
        "bodies": ("dial", "dial_tip"),
        "joints": ("dial_hinge",),
        "sites": ("dial_tip_site",),
    },
    "door_scene.xml": {
        "bodies": ("door", "door_panel", "door_handle"),
        "joints": ("door_hinge",),
        "sites": ("door_handle_site",),
    },
    "drawer_scene.xml": {
        "bodies": ("drawer", "drawer_handle"),
        "joints": ("drawer_slide",),
        "sites": ("drawer_handle_site",),
    },
    "faucet_scene.xml": {
        "bodies": ("faucet_handle", "faucet_tip"),
        "joints": ("faucet_hinge",),
        "sites": ("faucet_tip_site",),
    },
    "handle_vertical_scene.xml": {
        "bodies": ("vertical_handle",),
        "joints": ("vertical_handle_slide",),
        "sites": ("vertical_handle_site",),
    },
    "lever_scene.xml": {
        "bodies": ("lever", "lever_tip"),
        "joints": ("lever_hinge",),
        "sites": ("lever_tip_site",),
    },
    "peg_insertion_side_scene.xml": {
        "bodies": ("workpiece", "peg_goal"),
        "sites": ("peg_head_site",),
    },
    "pick_out_of_hole_scene.xml": {
        "bodies": ("workpiece", "extraction_goal"),
        "sites": ("workpiece_center",),
    },
    "pick_place_scene.xml": {
        "bodies": ("workpiece", "pick_place_goal"),
        "sites": ("workpiece_center",),
    },
    "pick_place_wall_scene.xml": {
        "bodies": ("workpiece", "wall_pick_goal"),
        "sites": ("workpiece_center",),
    },
    "push_to_goal_scene.xml": {"bodies": ("workpiece", "push_goal")},
    "reach_scene.xml": {"bodies": ("reach_goal",), "sites": ("reach_goal_site",)},
    "sweep_into_goal_scene.xml": {
        "bodies": ("workpiece", "sweep_goal"),
        "sites": ("sweep_goal_site",),
    },
    "wall_scene.xml": {
        "bodies": ("workpiece", "wall_push_goal"),
        "sites": ("workpiece_center",),
    },
}

ARM_JOINTS = tuple(f"joint{index}" for index in range(1, 8))
GRIPPER_JOINTS = (
    "left_driver_joint",
    "left_finger_joint",
    "left_inner_knuckle_joint",
    "right_driver_joint",
    "right_finger_joint",
    "right_inner_knuckle_joint",
)
ACTUATORS = tuple(f"act{index}" for index in range(1, 8)) + ("gripper",)


def _has_name(model: mujoco.MjModel, object_type: mujoco.mjtObj, name: str) -> bool:
    return mujoco.mj_name2id(model, object_type, name) >= 0


def _expected_dims(scene_name: str) -> tuple[int, int, int]:
    if scene_name == "reach_scene.xml":
        return (13, 13, 8)
    if scene_name in FREE_OBJECT_SCENES:
        return (20, 19, 8)
    assert scene_name in FIXTURE_JOINT_SCENES
    return (14, 14, 8)


def test_xarm7_task_scenes_load_step_and_preserve_fixture_contracts() -> None:
    assert mujoco.__version__ == "3.3.6"
    assert len(SCENE_NAMES) == 17
    assert {name for name in SCENE_NAMES if (ASSETS_ROOT / name).is_file()} == set(SCENE_NAMES)

    for scene_name in SCENE_NAMES:
        scene_path = ASSETS_ROOT / scene_name
        root = ET.parse(scene_path).getroot()
        assert root.get("model", "").startswith("xarm7_")
        includes = root.findall("include")
        assert len(includes) == 1
        assert includes[0].get("file") == "xarm7.xml"
        global_visual = root.find("visual/global")
        assert global_visual is not None
        assert global_visual.get("offwidth") == "800"
        assert global_visual.get("offheight") == "600"

        scene_text = scene_path.read_text(encoding="utf-8").lower()
        for forbidden in ("franka", "panda", "so101", "robotstudio_so101"):
            assert forbidden not in scene_text

        model = mujoco.MjModel.from_xml_path(str(scene_path))
        assert (model.nq, model.nv, model.nu) == _expected_dims(scene_name)
        assert model.vis.global_.offwidth >= 800
        assert model.vis.global_.offheight >= 600
        for joint_name in (*ARM_JOINTS, *GRIPPER_JOINTS):
            assert _has_name(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        for actuator_name in ACTUATORS:
            assert _has_name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
        assert _has_name(model, mujoco.mjtObj.mjOBJ_SITE, "link_tcp")

        for object_kind, object_type in (
            ("bodies", mujoco.mjtObj.mjOBJ_BODY),
            ("joints", mujoco.mjtObj.mjOBJ_JOINT),
            ("sites", mujoco.mjtObj.mjOBJ_SITE),
        ):
            for name in TASK_SYMBOLS[scene_name].get(object_kind, ()):
                assert _has_name(model, object_type, name), f"{scene_name}: {name}"

        home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
        assert home_id >= 0
        data = mujoco.MjData(model)
        mujoco.mj_resetDataKeyframe(model, data, home_id)
        start_time = float(data.time)
        mujoco.mj_step(model, data)
        assert data.time > start_time
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.qacc).all()
        assert np.isfinite(data.ctrl).all()


def test_xarm7_scene_geometry_is_world_framed_and_package_remains_non_runtime() -> None:
    reach_model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / "reach_scene.xml"))
    reach_data = mujoco.MjData(reach_model)
    mujoco.mj_forward(reach_model, reach_data)
    reach_goal_id = mujoco.mj_name2id(reach_model, mujoco.mjtObj.mjOBJ_BODY, "reach_goal")
    np.testing.assert_allclose(reach_data.xpos[reach_goal_id], [0.55, 0.10, 0.43], atol=1e-9)

    push_model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / "push_to_goal_scene.xml"))
    push_data = mujoco.MjData(push_model)
    mujoco.mj_forward(push_model, push_data)
    surface_id = mujoco.mj_name2id(push_model, mujoco.mjtObj.mjOBJ_GEOM, "work_surface")
    surface_top = float(push_data.geom_xpos[surface_id, 2] + push_model.geom_size[surface_id, 2])
    assert np.isclose(surface_top, 0.41)

    runnable_index = json.loads(
        (ROOT / "libraries" / "robots" / "index.json").read_text(encoding="utf-8")
    )
    assert "ufactory_xarm7" not in runnable_index["robots"]
    assert not (PACKAGE_ROOT / "skeleton").exists()
    assert not (PACKAGE_ROOT / "reference").exists()
