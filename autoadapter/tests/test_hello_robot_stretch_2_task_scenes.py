from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.harness.session import apply_framework_reset


ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = (
    ROOT / "libraries" / "robots" / "hello_robot_stretch_2" / "1.0.0" / "assets"
)

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

ACTUATOR_NAMES = (
    "forward",
    "turn",
    "lift",
    "arm_extend",
    "wrist_yaw",
    "grip",
    "head_pan",
    "head_tilt",
)

WORLD_QUAT = np.asarray(
    (0.7071067811865476, 0.0, 0.0, -0.7071067811865475),
    dtype=float,
)

TASK_SYMBOLS = {
    "reach_scene.xml": {
        "bodies": ("reach_goal", "evidence_target"),
        "sites": ("reach_goal_site",),
    },
    "push_to_goal_scene.xml": {
        "bodies": ("workpiece", "push_goal", "evidence_target"),
        "geoms": ("work_surface", "workpiece_geom", "push_goal_marker"),
    },
    "pick_place_scene.xml": {
        "bodies": ("workpiece", "pick_place_goal", "evidence_target"),
        "geoms": ("work_surface", "workpiece_geom", "goal_pedestal", "pick_place_goal_marker"),
        "sites": ("workpiece_center",),
    },
    "pick_place_wall_scene.xml": {
        "bodies": ("workpiece", "wall_pick_goal", "evidence_target"),
        "geoms": (
            "work_surface",
            "wall",
            "workpiece_geom",
            "wall_pick_goal_pedestal",
            "wall_pick_goal_marker",
        ),
        "sites": ("workpiece_center",),
    },
    "wall_scene.xml": {
        "bodies": ("workpiece", "wall_push_goal", "evidence_target"),
        "geoms": ("work_surface", "wall", "workpiece_geom", "wall_push_goal_marker"),
        "sites": ("workpiece_center",),
    },
    "sweep_into_goal_scene.xml": {
        "bodies": ("workpiece", "sweep_goal", "evidence_target"),
        "geoms": (
            "table_left",
            "table_right",
            "table_front",
            "table_back",
            "goal_catch",
            "workpiece_geom",
        ),
        "sites": ("sweep_goal_site",),
    },
    "drawer_scene.xml": {
        "bodies": ("drawer", "drawer_handle", "evidence_target"),
        "joints": ("drawer_slide",),
        "geoms": ("work_surface", "drawer_housing", "drawer_front", "drawer_handle_geom"),
        "sites": ("drawer_handle_site",),
    },
    "button_front_scene.xml": {
        "bodies": ("front_button", "evidence_target"),
        "joints": ("front_button_slide",),
        "geoms": ("work_surface", "button_housing", "front_button_geom"),
        "sites": ("front_button_site",),
    },
    "button_topdown_scene.xml": {
        "bodies": ("top_button", "evidence_target"),
        "joints": ("top_button_slide",),
        "geoms": ("work_surface", "top_button_housing", "top_button_geom"),
        "sites": ("top_button_site",),
    },
    "handle_vertical_scene.xml": {
        "bodies": ("vertical_handle", "evidence_target"),
        "joints": ("vertical_handle_slide",),
        "geoms": ("work_surface", "handle_housing", "vertical_handle_geom"),
        "sites": ("vertical_handle_site",),
    },
    "door_scene.xml": {
        "bodies": ("door", "door_panel", "door_handle", "evidence_target"),
        "joints": ("door_hinge",),
        "geoms": ("work_surface", "door_frame", "door_panel_geom", "door_handle_geom"),
        "sites": ("door_handle_site",),
    },
    "faucet_scene.xml": {
        "bodies": ("faucet_handle", "faucet_tip", "evidence_target"),
        "joints": ("faucet_hinge",),
        "geoms": ("work_surface", "faucet_base", "faucet_arm", "faucet_tip_geom"),
        "sites": ("faucet_tip_site",),
    },
    "dial_scene.xml": {
        "bodies": ("dial", "dial_tip", "evidence_target"),
        "joints": ("dial_hinge",),
        "geoms": ("work_surface", "dial_mount", "dial_face", "dial_tip_geom"),
        "sites": ("dial_tip_site",),
    },
    "lever_scene.xml": {
        "bodies": ("lever", "lever_tip", "evidence_target"),
        "joints": ("lever_hinge",),
        "geoms": ("work_surface", "lever_base", "lever_arm", "lever_tip_geom"),
        "sites": ("lever_tip_site",),
    },
    "peg_insertion_side_scene.xml": {
        "bodies": ("workpiece", "peg_goal", "evidence_target"),
        "geoms": ("work_surface", "hole_left", "hole_right", "hole_bottom", "hole_top", "peg_grasp_geom", "peg_shaft_geom", "peg_goal_marker"),
        "sites": ("peg_head_site",),
    },
    "bin_picking_scene.xml": {
        "bodies": ("workpiece", "bin_goal", "evidence_target"),
        "geoms": (
            "work_surface",
            "bin_bottom",
            "bin_left",
            "bin_right",
            "bin_front",
            "bin_back",
            "workpiece_geom",
            "goal_bin_bottom",
            "goal_bin_left",
            "goal_bin_right",
            "goal_bin_front",
            "goal_bin_back",
            "bin_goal_marker",
        ),
        "sites": ("workpiece_center",),
    },
    "pick_out_of_hole_scene.xml": {
        "bodies": ("workpiece", "extraction_goal", "evidence_target"),
        "geoms": (
            "work_surface",
            "hole_platform_left",
            "hole_platform_right",
            "hole_platform_front",
            "hole_platform_back",
            "workpiece_geom",
            "extraction_goal_pedestal",
            "extraction_goal_marker",
        ),
        "sites": ("workpiece_center",),
    },
}


def _has_name(model: mujoco.MjModel, object_type: mujoco.mjtObj, name: str) -> bool:
    return mujoco.mj_name2id(model, object_type, name) >= 0


def _id(model: mujoco.MjModel, object_type: mujoco.mjtObj, name: str) -> int:
    object_id = mujoco.mj_name2id(model, object_type, name)
    assert object_id >= 0, name
    return object_id


def _load(scene_name: str) -> mujoco.MjModel:
    return mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / scene_name))


def test_stretch_task_scene_set_is_exactly_local_and_closed() -> None:
    assert len(SCENE_NAMES) == 17
    assert sorted(path.name for path in ASSETS_ROOT.glob("*_scene.xml")) == sorted(SCENE_NAMES)
    assert set(TASK_SYMBOLS) == set(SCENE_NAMES)

    for scene_name in SCENE_NAMES:
        scene_path = ASSETS_ROOT / scene_name
        root = ET.parse(scene_path).getroot()
        assert root.get("model", "").startswith("stretch_")
        includes = root.findall("include")
        assert len(includes) == 1
        assert includes[0].get("file") == "stretch.xml"
        assert root.find("statistic").get("extent") == "1.2"
        headlight = root.find("visual/headlight")
        assert headlight is not None
        assert headlight.attrib == {
            "diffuse": "0.75 0.75 0.75",
            "ambient": "0.35 0.35 0.35",
            "specular": "0 0 0",
        }

        floor = root.find("./worldbody/geom[@name='floor']")
        assert floor is not None
        assert floor.get("type") == "plane"
        floor_pos = [float(value) for value in floor.get("pos", "0 0 0").split()]
        assert floor_pos[2] == 0.0
        for body in root.findall("./worldbody/body"):
            np.testing.assert_allclose(
                [float(value) for value in body.get("quat", "").split()],
                WORLD_QUAT,
                atol=0.0,
            )

        evidence_camera = root.find("./worldbody/camera[@name='evidence']")
        assert evidence_camera is not None
        assert evidence_camera.get("mode") == "targetbody"
        target_name = evidence_camera.get("target")
        assert target_name is not None
        assert root.find(f"./worldbody/body[@name='{target_name}']") is not None


def test_stretch_task_scenes_load_step_and_expose_fixture_contracts() -> None:
    assert mujoco.__version__ == "3.3.6"

    for scene_name in SCENE_NAMES:
        model = _load(scene_name)
        actuator_names = tuple(
            mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, index)
            for index in range(model.nu)
        )
        assert actuator_names == ACTUATOR_NAMES

        evidence_camera_id = _id(model, mujoco.mjtObj.mjOBJ_CAMERA, "evidence")
        target_body_id = int(model.cam_targetbodyid[evidence_camera_id])
        assert target_body_id >= 0
        assert mujoco.mj_id2name(
            model, mujoco.mjtObj.mjOBJ_BODY, target_body_id
        ) == "evidence_target"

        for object_kind, object_type in (
            ("bodies", mujoco.mjtObj.mjOBJ_BODY),
            ("joints", mujoco.mjtObj.mjOBJ_JOINT),
            ("geoms", mujoco.mjtObj.mjOBJ_GEOM),
            ("sites", mujoco.mjtObj.mjOBJ_SITE),
        ):
            for name in TASK_SYMBOLS[scene_name].get(object_kind, ()):
                assert _has_name(model, object_type, name), f"{scene_name}: {name}"

        base_id = _id(model, mujoco.mjtObj.mjOBJ_BODY, "base_link")
        floor_id = _id(model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
        np.testing.assert_allclose(model.body_pos[base_id], (0.0, 0.0, 0.0), atol=0.0)
        assert model.geom_pos[floor_id, 2] == 0.0

        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        assert abs(float(data.xpos[base_id, 2])) < 1e-12
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.ctrl).all()

        start_time = float(data.time)
        for _ in range(8):
            mujoco.mj_step(model, data)
            assert np.isfinite(data.qpos).all()
            assert np.isfinite(data.qvel).all()
            assert np.isfinite(data.ctrl).all()
        assert data.time > start_time
        assert -0.05 < float(data.xpos[base_id, 2]) < 0.05


def test_stretch_fixture_world_transform_is_representative() -> None:
    checks = {
        "reach_scene.xml": (("reach_goal", (0.10, -0.55, 0.43)),),
        "pick_place_scene.xml": (("workpiece", (0.08, -0.48, 0.43)),),
        "drawer_scene.xml": (("drawer", (0.11, -0.48, 0.45)),),
        "door_scene.xml": (("door", (0.08, -0.30, 0.47)),),
        "peg_insertion_side_scene.xml": (("peg_goal", (-0.092, -0.48, 0.48)),),
        "bin_picking_scene.xml": (("bin_goal", (-0.11, -0.48, 0.421)),),
    }
    for scene_name, body_checks in checks.items():
        model = _load(scene_name)
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        for body_name, expected in body_checks:
            body_id = _id(model, mujoco.mjtObj.mjOBJ_BODY, body_name)
            np.testing.assert_allclose(data.xpos[body_id], expected, atol=1e-9)

    pick_place_model = _load("pick_place_scene.xml")
    surface_id = _id(pick_place_model, mujoco.mjtObj.mjOBJ_GEOM, "work_surface")
    np.testing.assert_allclose(
        pick_place_model.geom_pos[surface_id], (0.0, -0.49, 0.33), atol=1e-9
    )
    np.testing.assert_allclose(
        pick_place_model.geom_size[surface_id], (0.17, 0.20, 0.08), atol=1e-9
    )

    drawer_model = _load("drawer_scene.xml")
    drawer_joint_id = _id(drawer_model, mujoco.mjtObj.mjOBJ_JOINT, "drawer_slide")
    np.testing.assert_allclose(drawer_model.jnt_axis[drawer_joint_id], (0.0, 1.0, 0.0))

    handle_model = _load("handle_vertical_scene.xml")
    handle_data = mujoco.MjData(handle_model)
    mujoco.mj_forward(handle_model, handle_data)
    handle_id = _id(handle_model, mujoco.mjtObj.mjOBJ_BODY, "vertical_handle")
    np.testing.assert_allclose(
        handle_data.xpos[handle_id], (0.10, -0.48, 0.56), atol=1e-9
    )

    door_model = _load("door_scene.xml")
    door_frame_id = _id(door_model, mujoco.mjtObj.mjOBJ_GEOM, "door_frame")
    np.testing.assert_allclose(door_model.geom_pos[door_frame_id], (0.08, -0.30, 0.47))

    peg_model = _load("peg_insertion_side_scene.xml")
    hole_left_id = _id(peg_model, mujoco.mjtObj.mjOBJ_GEOM, "hole_left")
    np.testing.assert_allclose(peg_model.geom_pos[hole_left_id], (-0.08, -0.415, 0.48))
    np.testing.assert_allclose(peg_model.geom_size[hole_left_id], (0.008, 0.035, 0.07))

    bin_model = _load("bin_picking_scene.xml")
    bin_left_id = _id(bin_model, mujoco.mjtObj.mjOBJ_GEOM, "bin_left")
    np.testing.assert_allclose(bin_model.geom_pos[bin_left_id], (0.11, -0.385, 0.44))
    np.testing.assert_allclose(bin_model.geom_size[bin_left_id], (0.09, 0.005, 0.03))


def test_stretch_fixtures_do_not_self_complete_without_robot_contact() -> None:
    cases = (
        ("drawer_scene.xml", "drawer_slide", 0.0),
        ("drawer_scene.xml", "drawer_slide", -0.08),
        ("button_topdown_scene.xml", "top_button_slide", 0.0),
        ("handle_vertical_scene.xml", "vertical_handle_slide", 0.0),
        ("handle_vertical_scene.xml", "vertical_handle_slide", -0.055),
    )
    for scene_name, joint_name, reset_position in cases:
        model = _load(scene_name)
        data = mujoco.MjData(model)
        apply_framework_reset(
            mujoco,
            model,
            data,
            {
                "kind": "default",
                "joint_positions": {joint_name: reset_position},
            },
        )
        joint_id = _id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        qpos_address = int(model.jnt_qposadr[joint_id])
        initial = float(data.qpos[qpos_address])

        for _ in range(2000):
            mujoco.mj_step(model, data)

        assert abs(float(data.qpos[qpos_address]) - initial) < 0.001
