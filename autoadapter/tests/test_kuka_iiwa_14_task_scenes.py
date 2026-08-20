from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = ROOT / "libraries" / "robots" / "kuka_iiwa_14" / "1.0.0" / "assets"

SCENE_NAMES = (
    "reach_scene.xml",
    "push_to_goal_scene.xml",
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
    "door_lock_scene.xml",
    "door_unlock_scene.xml",
    "soccer_scene.xml",
    "window_scene.xml",
)

FREE_OBJECT_SCENES = {
    "push_to_goal_scene.xml",
    "wall_scene.xml",
    "sweep_into_goal_scene.xml",
    "soccer_scene.xml",
}

TASK_SYMBOLS = {
    "reach_scene.xml": {
        "bodies": ("reach_goal",),
        "sites": ("reach_goal_site",),
    },
    "push_to_goal_scene.xml": {"bodies": ("workpiece", "push_goal")},
    "wall_scene.xml": {
        "bodies": ("workpiece", "wall_push_goal"),
        "sites": ("workpiece_center",),
    },
    "sweep_into_goal_scene.xml": {
        "bodies": ("workpiece", "sweep_goal"),
        "sites": ("sweep_goal_site",),
    },
    "drawer_scene.xml": {
        "bodies": ("drawer", "drawer_handle"),
        "joints": ("drawer_slide",),
        "sites": ("drawer_handle_site",),
    },
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
    "handle_vertical_scene.xml": {
        "bodies": ("vertical_handle",),
        "joints": ("vertical_handle_slide",),
        "sites": ("vertical_handle_site",),
    },
    "door_scene.xml": {
        "bodies": ("door", "door_panel", "door_handle"),
        "joints": ("door_hinge",),
        "sites": ("door_handle_site",),
    },
    "faucet_scene.xml": {
        "bodies": ("faucet_handle", "faucet_tip"),
        "joints": ("faucet_hinge",),
        "sites": ("faucet_tip_site",),
    },
    "dial_scene.xml": {
        "bodies": ("dial", "dial_tip"),
        "joints": ("dial_hinge",),
        "sites": ("dial_tip_site",),
    },
    "lever_scene.xml": {
        "bodies": ("lever", "lever_tip"),
        "joints": ("lever_hinge",),
        "sites": ("lever_tip_site",),
    },
    "door_lock_scene.xml": {
        "bodies": ("door_lock_housing", "door_lock"),
        "joints": ("door_lock_slide",),
        "sites": ("door_lock_site",),
    },
    "door_unlock_scene.xml": {
        "bodies": ("door_unlock_housing", "door_unlock"),
        "joints": ("door_unlock_slide",),
        "sites": ("door_unlock_site",),
    },
    "soccer_scene.xml": {
        "bodies": ("soccer_ball", "soccer_goal"),
        "sites": ("soccer_ball_center", "soccer_goal_site"),
    },
    "window_scene.xml": {
        "bodies": ("window", "window_handle"),
        "joints": ("window_slide",),
        "sites": ("window_handle_site",),
    },
}

JOINT_CONTRACTS = {
    "drawer_scene.xml": ("drawer", "drawer_slide", [0.48, 0.11, 0.45], [0, 1, 0], [-0.08, 0]),
    "button_front_scene.xml": ("front_button", "front_button_slide", [0.48, 0.13, 0.47], [0, 1, 0], [0, 0.05]),
    "button_topdown_scene.xml": ("top_button", "top_button_slide", [0.50, 0.10, 0.475], [0, 0, 1], [-0.05, 0]),
    "handle_vertical_scene.xml": ("vertical_handle", "vertical_handle_slide", [0.48, 0.10, 0.48], [0, 0, 1], [-0.055, 0]),
    "door_scene.xml": ("door", "door_hinge", [0.30, 0.08, 0.47], [0, 0, 1], [0, 1.2]),
    "faucet_scene.xml": ("faucet_handle", "faucet_hinge", [0.45, 0.12, 0.47], [0, 0, 1], [0, 1.2]),
    "dial_scene.xml": ("dial", "dial_hinge", [0.50, 0.17, 0.52], [1, 0, 0], [0, 1.2]),
    "lever_scene.xml": ("lever", "lever_hinge", [0.46, 0.12, 0.43], [0, 1, 0], [0, 1.57]),
    "door_lock_scene.xml": ("door_lock", "door_lock_slide", [0.48, 0.10, 0.48], [0, 0, 1], [-0.055, 0]),
    "door_unlock_scene.xml": ("door_unlock", "door_unlock_slide", [0.48, 0.10, 0.47], [1, 0, 0], [0, 0.05]),
    "window_scene.xml": ("window", "window_slide", [0.45, 0.12, 0.50], [1, 0, 0], [0, 0.10]),
}

PASSIVE_DISTANCE_CHECKS = {
    "push_to_goal_scene.xml": ("workpiece", [0.48, 0.18, 0.412], 0.05),
    "wall_scene.xml": ("workpiece", [0.46, 0.18, 0.412], 0.07),
    "sweep_into_goal_scene.xml": ("workpiece", [0.48, 0.18, 0.385], 0.05),
    "soccer_scene.xml": ("soccer_ball", [0.48, 0.20, 0.44], 0.07),
    "drawer_scene.xml": ("drawer_handle", [0.48, -0.005, 0.45], 0.03),
    "button_front_scene.xml": ("front_button", [0.48, 0.18, 0.47], 0.02),
    "button_topdown_scene.xml": ("top_button", [0.50, 0.10, 0.425], 0.024),
    "handle_vertical_scene.xml": ("vertical_handle", [0.48, 0.10, 0.43], 0.02),
    "door_scene.xml": ("door_handle", [0.30, 0.22, 0.47], 0.08),
    "faucet_scene.xml": ("faucet_tip", [0.479, 0.195, 0.47], 0.07),
    "dial_scene.xml": ("dial_tip", [0.50, 0.134, 0.427], 0.07),
}

PASSIVE_JOINT_CHECKS = {
    "door_lock_scene.xml": ("door_lock_slide", -0.055, 0.02),
    "door_unlock_scene.xml": ("door_unlock_slide", 0.05, 0.02),
    "lever_scene.xml": ("lever_hinge", 1.5707963267948966, 0.1308996939),
    "window_scene.xml": ("window_slide", 0.10, 0.05),
}


def _has_name(model: mujoco.MjModel, object_type: mujoco.mjtObj, name: str) -> bool:
    return mujoco.mj_name2id(model, object_type, name) >= 0


def _body_id(model: mujoco.MjModel, name: str) -> int:
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
    assert body_id >= 0, name
    return body_id


def _joint_id(model: mujoco.MjModel, name: str) -> int:
    joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
    assert joint_id >= 0, name
    return joint_id


def _qpos_for_joint(model: mujoco.MjModel, data: mujoco.MjData, name: str) -> float:
    return float(data.qpos[int(model.jnt_qposadr[_joint_id(model, name)])])


def _robot_task_contacts(model: mujoco.MjModel, data: mujoco.MjData) -> list[tuple[str | None, str | None]]:
    robot_body_names = {"base", *(f"link{index}" for index in range(1, 8))}
    robot_body_ids = {
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        for name in robot_body_names
    }
    robot_geom_ids = {
        geom_id
        for geom_id in range(model.ngeom)
        if int(model.geom_bodyid[geom_id]) in robot_body_ids
    }
    task_geom_ids = {
        geom_id
        for geom_id in range(model.ngeom)
        if geom_id not in robot_geom_ids
        and mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_id) != "floor"
    }
    contacts = []
    for index in range(data.ncon):
        contact = data.contact[index]
        geom1, geom2 = int(contact.geom1), int(contact.geom2)
        if (geom1 in robot_geom_ids and geom2 in task_geom_ids) or (
            geom2 in robot_geom_ids and geom1 in task_geom_ids
        ):
            contacts.append(
                (
                    mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom1),
                    mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom2),
                )
            )
    return contacts


def test_kuka_iiwa_14_task_scene_set_is_local_and_live() -> None:
    assert len(SCENE_NAMES) == 16
    assert sorted(path.name for path in ASSETS_ROOT.glob("*_scene.xml")) == sorted(SCENE_NAMES)

    for scene_name in SCENE_NAMES:
        scene_path = ASSETS_ROOT / scene_name
        root = ET.parse(scene_path).getroot()
        assert root.get("model", "").startswith("kuka_iiwa_14_")
        includes = root.findall("include")
        assert len(includes) == 1
        assert includes[0].get("file") == "iiwa14.xml"
        include_path = (scene_path.parent / includes[0].get("file", "")).resolve()
        assert include_path.parent == ASSETS_ROOT.resolve()
        assert include_path.is_file()

        global_visual = root.find("visual/global")
        assert global_visual is not None
        assert global_visual.get("offwidth") == "800"
        assert global_visual.get("offheight") == "600"
        assert root.find('.//body[@name="evidence_target"]') is not None
        assert root.find('.//camera[@name="evidence"]') is not None

        model = mujoco.MjModel.from_xml_path(str(scene_path))
        expected_nq = 14 if scene_name in FREE_OBJECT_SCENES else 7 if scene_name == "reach_scene.xml" else 8
        assert (model.nq, model.nv, model.nu) == (expected_nq, expected_nq if expected_nq != 14 else 13, 7)
        assert model.vis.global_.offwidth >= 800
        assert model.vis.global_.offheight >= 600
        assert _has_name(model, mujoco.mjtObj.mjOBJ_CAMERA, "evidence")
        assert _has_name(model, mujoco.mjtObj.mjOBJ_BODY, "evidence_target")
        assert _has_name(model, mujoco.mjtObj.mjOBJ_SITE, "attachment_site")

        symbols = TASK_SYMBOLS[scene_name]
        for body_name in symbols.get("bodies", ()):
            assert _has_name(model, mujoco.mjtObj.mjOBJ_BODY, body_name), f"{scene_name}: {body_name}"
        for joint_name in symbols.get("joints", ()):
            assert _has_name(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name), f"{scene_name}: {joint_name}"
        for site_name in symbols.get("sites", ()):
            assert _has_name(model, mujoco.mjtObj.mjOBJ_SITE, site_name), f"{scene_name}: {site_name}"

        data = mujoco.MjData(model)
        mujoco.mj_resetData(model, data)
        np.testing.assert_allclose(data.qpos, model.qpos0, rtol=0.0, atol=0.0)
        mujoco.mj_forward(model, data)
        assert _robot_task_contacts(model, data) == []
        start_time = float(data.time)
        mujoco.mj_step(model, data)
        assert data.time > start_time
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.qacc).all()
        assert np.isfinite(data.ctrl).all()


def test_kuka_iiwa_14_fixture_axes_positions_and_gravity_compensation() -> None:
    for scene_name, (body_name, joint_name, body_pos, axis, joint_range) in JOINT_CONTRACTS.items():
        model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / scene_name))
        body_id = _body_id(model, body_name)
        joint_id = _joint_id(model, joint_name)
        np.testing.assert_allclose(model.body_pos[body_id], body_pos, rtol=0.0, atol=1e-9)
        np.testing.assert_allclose(model.jnt_axis[joint_id], axis, rtol=0.0, atol=0.0)
        np.testing.assert_allclose(model.jnt_range[joint_id], joint_range, rtol=0.0, atol=1e-9)

    model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / "button_topdown_scene.xml"))
    assert float(model.body_gravcomp[_body_id(model, "top_button")]) == 1.0
    model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / "handle_vertical_scene.xml"))
    assert float(model.body_gravcomp[_body_id(model, "vertical_handle")]) == 1.0
    model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / "dial_scene.xml"))
    assert float(model.body_gravcomp[_body_id(model, "dial")]) == 1.0
    assert float(model.body_gravcomp[_body_id(model, "dial_tip")]) == 1.0

    model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / "door_lock_scene.xml"))
    assert float(model.body_gravcomp[_body_id(model, "door_lock")]) == 1.0
    model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / "door_unlock_scene.xml"))
    assert float(model.body_gravcomp[_body_id(model, "door_unlock")]) == 1.0
    np.testing.assert_allclose(model.body_pos[_body_id(model, "door_unlock_housing")], [0.54, 0.10, 0.47], atol=1e-9)
    model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / "window_scene.xml"))
    assert float(model.body_gravcomp[_body_id(model, "window")]) == 1.0
    np.testing.assert_allclose(model.body_pos[_body_id(model, "window_handle")], [0.0, -0.035, 0.0], atol=1e-9)

    model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / "drawer_scene.xml"))
    drawer_housing = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "drawer_housing")
    assert int(model.geom_contype[drawer_housing]) == 0
    assert int(model.geom_conaffinity[drawer_housing]) == 0

    model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / "soccer_scene.xml"))
    ball_geom = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "soccer_ball_geom")
    np.testing.assert_allclose(model.geom_size[ball_geom, 0], 0.03, rtol=0.0, atol=0.0)
    data = mujoco.MjData(model)
    mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)
    np.testing.assert_allclose(data.geom_xpos[ball_geom], [0.48, 0.08, 0.44], atol=1e-9)
    np.testing.assert_allclose(data.xpos[_body_id(model, "soccer_goal")], [0.48, 0.20, 0.44], atol=1e-9)
    for geom_name in ("soccer_goal_back", "soccer_goal_left", "soccer_goal_right"):
        assert _has_name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_name)
    model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / "window_scene.xml"))
    for geom_name in (
        "window_frame_left",
        "window_frame_right",
        "window_frame_top",
        "window_frame_bottom",
    ):
        assert _has_name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_name)


def test_kuka_iiwa_14_passive_fixtures_stay_outside_public_success_thresholds() -> None:
    for scene_name, (body_name, target, threshold) in PASSIVE_DISTANCE_CHECKS.items():
        model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / scene_name))
        data = mujoco.MjData(model)
        mujoco.mj_resetData(model, data)
        mujoco.mj_forward(model, data)
        body_id = _body_id(model, body_name)
        target_vector = np.asarray(target, dtype=float)
        initial_error = float(np.linalg.norm(data.xpos[body_id] - target_vector))
        assert initial_error > threshold, f"{scene_name}: initial error {initial_error}"
        data.ctrl[:] = 0.0
        for _ in range(round(20.0 / model.opt.timestep)):
            mujoco.mj_step(model, data)
        final_error = float(np.linalg.norm(data.xpos[body_id] - target_vector))
        assert final_error > threshold, f"{scene_name}: final error {final_error}"
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()

    for scene_name, (joint_name, target, threshold) in PASSIVE_JOINT_CHECKS.items():
        model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / scene_name))
        data = mujoco.MjData(model)
        mujoco.mj_resetData(model, data)
        initial_error = abs(_qpos_for_joint(model, data, joint_name) - target)
        assert initial_error > threshold, f"{scene_name}: initial error {initial_error}"
        data.ctrl[:] = 0.0
        for _ in range(round(20.0 / model.opt.timestep)):
            mujoco.mj_step(model, data)
        final_error = abs(_qpos_for_joint(model, data, joint_name) - target)
        assert final_error > threshold, f"{scene_name}: final error {final_error}"
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
