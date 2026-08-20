from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.trusted_skeletons import ArmSerialDLSSkeleton, ArmSpec


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "aloha_2" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
RIGHT_ARM_JOINTS = tuple(
    f"right/{name}"
    for name in (
        "waist",
        "shoulder",
        "elbow",
        "forearm_roll",
        "wrist_angle",
        "wrist_rotate",
    )
)
RIGHT_NEUTRAL_Q = (0.0, -0.96, 1.16, 0.0, -0.3, 0.0)
REACH_TARGET = np.asarray((0.20, 0.10, 0.20), dtype=float)

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
    "soccer_scene.xml",
    "window_scene.xml",
)

TASK_SYMBOLS = {
    "reach_scene.xml": {
        "bodies": ("reach_goal",),
        "sites": ("reach_goal_site",),
        "geoms": ("reach_goal_marker",),
    },
    "push_to_goal_scene.xml": {
        "bodies": ("workpiece", "push_goal"),
        "geoms": ("workpiece_geom", "push_goal_marker"),
    },
    "pick_place_scene.xml": {
        "bodies": ("workpiece", "pick_place_goal"),
        "sites": ("workpiece_center",),
        "geoms": (
            "workpiece_geom",
            "goal_pedestal",
            "pick_place_goal_marker",
        ),
    },
    "pick_place_wall_scene.xml": {
        "bodies": ("workpiece", "wall_pick_goal"),
        "sites": ("workpiece_center",),
        "geoms": (
            "wall",
            "workpiece_geom",
            "wall_pick_goal_pedestal",
            "wall_pick_goal_marker",
        ),
    },
    "wall_scene.xml": {
        "bodies": ("workpiece", "wall_push_goal"),
        "sites": ("workpiece_center",),
        "geoms": ("wall", "workpiece_geom", "wall_push_goal_marker"),
    },
    "sweep_into_goal_scene.xml": {
        "bodies": ("workpiece", "sweep_goal"),
        "sites": ("sweep_goal_site",),
        "geoms": (
            "table_left",
            "table_right",
            "table_front",
            "table_back",
            "goal_catch",
            "workpiece_geom",
        ),
    },
    "drawer_scene.xml": {
        "bodies": ("drawer", "drawer_handle"),
        "joints": ("drawer_slide",),
        "sites": ("drawer_handle_site",),
        "geoms": ("drawer_housing", "drawer_front", "drawer_handle_geom"),
    },
    "button_front_scene.xml": {
        "bodies": ("front_button",),
        "joints": ("front_button_slide",),
        "sites": ("front_button_site",),
        "geoms": ("button_housing", "front_button_geom"),
    },
    "button_topdown_scene.xml": {
        "bodies": ("top_button",),
        "joints": ("top_button_slide",),
        "sites": ("top_button_site",),
        "geoms": ("top_button_housing", "top_button_geom"),
    },
    "handle_vertical_scene.xml": {
        "bodies": ("vertical_handle",),
        "joints": ("vertical_handle_slide",),
        "sites": ("vertical_handle_site",),
        "geoms": ("handle_housing", "vertical_handle_geom"),
    },
    "door_scene.xml": {
        "bodies": ("door", "door_panel", "door_handle"),
        "joints": ("door_hinge",),
        "sites": ("door_handle_site",),
        "geoms": ("door_frame", "door_panel_geom", "door_handle_geom"),
    },
    "faucet_scene.xml": {
        "bodies": ("faucet_handle", "faucet_tip"),
        "joints": ("faucet_hinge",),
        "sites": ("faucet_tip_site",),
        "geoms": ("faucet_base", "faucet_arm", "faucet_tip_geom"),
    },
    "dial_scene.xml": {
        "bodies": ("dial", "dial_tip"),
        "joints": ("dial_hinge",),
        "sites": ("dial_tip_site",),
        "geoms": ("dial_mount", "dial_face", "dial_tip_geom"),
    },
    "lever_scene.xml": {
        "bodies": ("lever", "lever_tip"),
        "joints": ("lever_hinge",),
        "sites": ("lever_tip_site",),
        "geoms": ("lever_base", "lever_arm", "lever_tip_geom"),
    },
    "soccer_scene.xml": {
        "bodies": ("soccer_ball", "soccer_goal"),
        "sites": ("soccer_ball_center", "soccer_goal_site"),
        "geoms": (
            "soccer_ball_geom",
            "soccer_goal_back",
            "soccer_goal_left",
            "soccer_goal_right",
        ),
    },
    "window_scene.xml": {
        "bodies": ("window", "window_handle"),
        "joints": ("window_slide",),
        "sites": ("window_handle_site",),
        "geoms": (
            "window_frame_left",
            "window_frame_right",
            "window_frame_top",
            "window_frame_bottom",
            "window_panel",
            "window_handle_geom",
        ),
    },
}


def _has_name(model: mujoco.MjModel, object_type: mujoco.mjtObj, name: str) -> bool:
    return mujoco.mj_name2id(model, object_type, name) >= 0


def _name_id(model: mujoco.MjModel, object_type: mujoco.mjtObj, name: str) -> int:
    identifier = mujoco.mj_name2id(model, object_type, name)
    assert identifier >= 0, name
    return identifier


def test_aloha_2_task_scene_set_is_exactly_local_and_live() -> None:
    assert mujoco.__version__ == "3.3.6"
    assert len(SCENE_NAMES) == 16
    assert sorted(path.name for path in ASSETS_ROOT.glob("*_scene.xml")) == sorted(
        SCENE_NAMES
    )
    assert set(TASK_SYMBOLS) == set(SCENE_NAMES)

    for scene_name in SCENE_NAMES:
        scene_path = ASSETS_ROOT / scene_name
        root = ET.parse(scene_path).getroot()
        task_name = scene_name.removesuffix("_scene.xml")
        assert root.get("model") == f"aloha_2_{task_name}"

        includes = root.findall("include")
        assert len(includes) == 1
        assert includes[0].get("file") == "scene.xml"
        include_path = (scene_path.parent / includes[0].get("file", "")).resolve()
        assert include_path == (ASSETS_ROOT / "scene.xml").resolve()
        assert include_path.is_file()

        camera = root.find("worldbody/camera")
        assert camera is not None
        assert camera.attrib == {
            "name": "evidence",
            "pos": "0 -1.1 0.60",
            "xyaxes": "1 0 0 0 0.2 0.8",
        }
        assert root.find(".//body[@name='evidence_target']") is None

        model = mujoco.MjModel.from_xml_path(str(scene_path))
        assert model.vis.global_.offwidth >= 800
        assert model.vis.global_.offheight >= 600
        assert _has_name(model, mujoco.mjtObj.mjOBJ_KEY, "neutral_pose")
        assert _has_name(model, mujoco.mjtObj.mjOBJ_CAMERA, "evidence")

        for object_kind, object_type in (
            ("bodies", mujoco.mjtObj.mjOBJ_BODY),
            ("joints", mujoco.mjtObj.mjOBJ_JOINT),
            ("sites", mujoco.mjtObj.mjOBJ_SITE),
            ("geoms", mujoco.mjtObj.mjOBJ_GEOM),
        ):
            for name in TASK_SYMBOLS[scene_name].get(object_kind, ()):
                assert _has_name(model, object_type, name), f"{scene_name}: {name}"

        data = mujoco.MjData(model)
        neutral_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "neutral_pose")
        mujoco.mj_resetDataKeyframe(model, data, neutral_id)
        mujoco.mj_forward(model, data)
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.qacc).all()
        assert np.isfinite(data.ctrl).all()
        start_time = float(data.time)
        mujoco.mj_step(model, data)
        assert data.time > start_time
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.qacc).all()
        assert np.isfinite(data.ctrl).all()


def test_reach_scene_preserves_target_and_right_arm_reachability() -> None:
    model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / "reach_scene.xml"))
    data = mujoco.MjData(model)
    neutral_id = _name_id(model, mujoco.mjtObj.mjOBJ_KEY, "neutral_pose")
    mujoco.mj_resetDataKeyframe(model, data, neutral_id)
    mujoco.mj_forward(model, data)

    target_site = _name_id(model, mujoco.mjtObj.mjOBJ_SITE, "reach_goal_site")
    gripper_site = _name_id(model, mujoco.mjtObj.mjOBJ_SITE, "right/gripper")
    np.testing.assert_allclose(data.site_xpos[target_site], REACH_TARGET, atol=0.0)
    assert np.linalg.norm(data.site_xpos[gripper_site] - REACH_TARGET) > 0.05
    assert data.ncon == 0

    initial_qpos = np.asarray(data.qpos, dtype=float).copy()
    limits = {
        name: model.jnt_range[
            _name_id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        ].tolist()
        for name in RIGHT_ARM_JOINTS
    }
    skeleton = ArmSerialDLSSkeleton.from_session(
        model=model,
        data=data,
        spec=ArmSpec(
            ee_site_name="right/gripper",
            arm_joint_names=RIGHT_ARM_JOINTS,
            arm_actuator_names=RIGHT_ARM_JOINTS,
            joint_limits=limits,
            home_qpos=RIGHT_NEUTRAL_Q,
            ik_max_iter=1000,
            ik_tolerance=0.002,
            ik_step_clamp=0.12,
            gripper_actuator_names=("right/gripper",),
            gripper_close_ctrl=0.002,
            gripper_open_ctrl=0.037,
        ),
    )
    solved = skeleton.ik(REACH_TARGET, q_init=RIGHT_NEUTRAL_Q)
    np.testing.assert_allclose(skeleton.fk(solved)["pos"], REACH_TARGET, atol=0.002)
    np.testing.assert_array_equal(data.qpos, initial_qpos)


def test_sweep_scene_preserves_the_source_opening_dynamics() -> None:
    model = mujoco.MjModel.from_xml_path(
        str(ASSETS_ROOT / "sweep_into_goal_scene.xml")
    )
    workpiece_geom_id = _name_id(
        model, mujoco.mjtObj.mjOBJ_GEOM, "workpiece_geom"
    )
    selected_pair_names = set()
    for index in range(model.npair):
        geom1 = mujoco.mj_id2name(
            model, mujoco.mjtObj.mjOBJ_GEOM, int(model.pair_geom1[index])
        )
        geom2 = mujoco.mj_id2name(
            model, mujoco.mjtObj.mjOBJ_GEOM, int(model.pair_geom2[index])
        )
        if workpiece_geom_id in {
            int(model.pair_geom1[index]),
            int(model.pair_geom2[index]),
        }:
            selected_pair_names.add(geom1 if geom2 == "workpiece_geom" else geom2)
    assert selected_pair_names == {
        f"{side}/{finger}_g{index}"
        for side in ("left", "right")
        for finger in ("left", "right")
        for index in range(3)
    }

    def settle_at(position: tuple[float, float, float]) -> tuple[np.ndarray, set[str]]:
        data = mujoco.MjData(model)
        neutral_id = _name_id(model, mujoco.mjtObj.mjOBJ_KEY, "neutral_pose")
        mujoco.mj_resetDataKeyframe(model, data, neutral_id)
        workpiece_body_id = _name_id(
            model, mujoco.mjtObj.mjOBJ_BODY, "workpiece"
        )
        workpiece_joint_id = int(model.body_jntadr[workpiece_body_id])
        qpos_address = int(model.jnt_qposadr[workpiece_joint_id])
        data.qpos[qpos_address : qpos_address + 3] = position
        mujoco.mj_forward(model, data)
        for _ in range(1000):
            mujoco.mj_step(model, data)
        contacts = {
            name
            for contact_index in range(data.ncon)
            for name in (
                mujoco.mj_id2name(
                    model,
                    mujoco.mjtObj.mjOBJ_GEOM,
                    int(data.contact[contact_index].geom1),
                ),
                mujoco.mj_id2name(
                    model,
                    mujoco.mjtObj.mjOBJ_GEOM,
                    int(data.contact[contact_index].geom2),
                ),
            )
            if name is not None
        }
        return np.asarray(data.qpos[qpos_address : qpos_address + 3]).copy(), contacts

    supported, supported_contacts = settle_at((0.20, 0.08, 0.015))
    dropped, dropped_contacts = settle_at((0.20, 0.18, 0.015))

    np.testing.assert_allclose(supported[:2], (0.20, 0.08), atol=0.003)
    assert supported[2] > 0.0
    assert "table_front" in supported_contacts
    np.testing.assert_allclose(dropped[:2], (0.20, 0.18), atol=0.003)
    assert dropped[2] < -0.015
    assert supported[2] - dropped[2] > 0.035
    assert "goal_catch" in dropped_contacts
    assert "table" not in supported_contacts | dropped_contacts
