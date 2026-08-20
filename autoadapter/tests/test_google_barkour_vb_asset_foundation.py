from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "google_barkour_vb" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
MIGRATION_ROOT = ROOT.parent / "demo2" / "legacy_assets" / "menagerie" / "google_barkour_vb"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
SCENE_PATH = ASSETS_ROOT / "scene.xml"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"

ACTUATED_JOINT_NAMES = [
    "abduction_front_left",
    "hip_front_left",
    "knee_front_left",
    "abduction_hind_left",
    "hip_hind_left",
    "knee_hind_left",
    "abduction_front_right",
    "hip_front_right",
    "knee_front_right",
    "abduction_hind_right",
    "hip_hind_right",
    "knee_hind_right",
]
ALL_JOINT_NAMES = ["torso", *ACTUATED_JOINT_NAMES]
ACTUATOR_NAMES = ACTUATED_JOINT_NAMES.copy()
LEG_ORDER = ["front_left", "hind_left", "front_right", "hind_right"]
FOOT_SITE_NAMES = [
    "foot_front_left",
    "foot_hind_left",
    "foot_front_right",
    "foot_hind_right",
]
SITE_NAMES = [
    "imu_frame",
    "base_frame",
    "vicon_frame",
    "vicon_0",
    "vicon_1",
    "vicon_2",
    "vicon_3",
    "vicon_4",
    "vicon_5",
    "vicon_6",
    "vicon_8",
    "vicon_9",
    "head_camera_frame",
    "realsense/depth_frame",
    "realsense/rgb_frame",
    "realsense/imu",
    "handle_camera_frame",
    *FOOT_SITE_NAMES,
]
SENSOR_NAMES = [
    "abduction_front_left_pos",
    "hip_front_left_pos",
    "knee_front_left_pos",
    "abduction_hind_left_pos",
    "hip_hind_left_pos",
    "knee_hind_left_pos",
    "abduction_front_right_pos",
    "hip_front_right_pos",
    "knee_front_right_pos",
    "abduction_hind_right_pos",
    "hip_hind_right_pos",
    "knee_hind_right_pos",
    "abduction_front_left_vel",
    "hip_front_left_vel",
    "knee_front_left_vel",
    "abduction_hind_left_vel",
    "hip_hind_left_vel",
    "knee_hind_left_vel",
    "abduction_front_right_vel",
    "hip_front_right_vel",
    "knee_front_right_vel",
    "abduction_hind_right_vel",
    "hip_hind_right_vel",
    "knee_hind_right_vel",
    "gyro",
    "accelerometer",
    "orientation",
    "global_position",
    "global_linvel",
    "global_angvel",
]
JOINT_LIMITS = {
    name: limit
    for name, limit in zip(
        ACTUATED_JOINT_NAMES,
        [
            [-1.0472, 1.0472],
            [-1.54706, 3.02902],
            [0.0, 2.44346],
        ]
        * 4,
    )
}
ACTUATOR_CTRLRANGES = {
    name: limit
    for name, limit in zip(
        ACTUATOR_NAMES,
        [
            [-0.9472, 0.9472],
            [-1.44706, 2.92902],
            [0.1, 2.34346],
        ]
        * 4,
    )
}
HOME_QPOS = [
    0.0,
    0.0,
    0.28,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.5,
    1.0,
    0.0,
    0.5,
    1.0,
    0.0,
    0.5,
    1.0,
    0.0,
    0.5,
    1.0,
]
HOME_CTRL = [0.0, 0.5, 1.0] * 4
EXPECTED_CLOSURE_FILES = (
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "SOURCE.md",
    "barkour_vb.png",
    "barkour_vb.xml",
    "barkour_vb_mjx.xml",
    "barkour_vb_rev_1_0_head_straight.urdf",
    "scene.xml",
    "scene_hfield_mjx.xml",
    "scene_mjx.xml",
    "assets/abduction.stl",
    "assets/camera_cover.stl",
    "assets/foot.stl",
    "assets/handle.stl",
    "assets/hfield.png",
    "assets/intel_realsense_depth_camera_d435.stl",
    "assets/lower_leg.stl",
    "assets/neck.stl",
    "assets/torso.stl",
    "assets/upper_leg.stl",
    "assets/upper_leg_left.stl",
    "assets/upper_leg_right.stl",
)


def _names(model: mujoco.MjModel, obj: mujoco.mjtObj, count: int) -> list[str | None]:
    return [mujoco.mj_id2name(model, obj, index) for index in range(count)]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _assert_local_reference(reference: str) -> None:
    assert "://" not in reference
    assert not Path(reference).is_absolute()
    resolved = (ASSETS_ROOT / reference).resolve()
    assert _is_under(resolved, ASSETS_ROOT.resolve())
    assert resolved.is_file(), reference


def test_google_barkour_vb_asset_foundation_is_local_and_live() -> None:
    morphology = json.loads(MORPHOLOGY_PATH.read_text(encoding="utf-8"))
    assert morphology["schema_version"] == "1.0"
    assert morphology["robot_model_id"] == "google_barkour_vb"
    assert morphology["robot_configuration_id"] == "google_barkour_vb"
    assert morphology["package_version"] == "1.0.0"
    assert morphology["morphology_kind"] == "free_base_quadruped"
    assert morphology["base_type"] == "free"
    assert morphology["degrees_of_freedom"] == {
        "floating_base": 6,
        "actuated_joints": 12,
    }
    assert morphology["model_dimensions"] == {"nq": 19, "nv": 18, "nu": 12}
    assert morphology["mjcf_entrypoint"] == "assets/scene.xml"

    control = morphology["public_control"]
    assert control["actuation"] == "joint_position"
    assert control["transmission"] == "direct_joint"
    assert control["actuator_type"] == "general"
    assert control["bias_type"] == "affine"
    assert control["joint_type"] == "hinge"
    assert control["leg_order"] == LEG_ORDER
    assert control["joint_names"] == ACTUATED_JOINT_NAMES
    assert control["actuator_names"] == ACTUATOR_NAMES
    assert control["actuator_joint_map"] == dict(zip(ACTUATOR_NAMES, ACTUATED_JOINT_NAMES))
    assert control["joint_limits_rad"] == JOINT_LIMITS
    assert control["actuator_ctrlrange_rad"] == ACTUATOR_CTRLRANGES
    assert control["position_actuator_defaults"] == {
        "legs": {
            "actuator_names": ACTUATOR_NAMES,
            "kp": 50.0,
            "kv": 0.5,
            "forcerange": [-18.0, 18.0],
        }
    }
    assert morphology["public_observations"] == {
        "base_body": "torso",
        "imu_site": "imu_frame",
        "foot_sites": FOOT_SITE_NAMES,
        "sensor_names": SENSOR_NAMES,
    }
    assert morphology["public_affordances"] == {
        "actions": ["joint_position_control"],
        "observations": [
            "joint_positions",
            "joint_velocities",
            "base_pose",
            "base_velocity",
            "named_site_pose",
            "simulation_time",
        ],
    }
    assert morphology["reset_fact"] == {
        "owner": "framework",
        "default": "model_qpos0",
        "available_keyframe": "home",
        "home_qpos": HOME_QPOS,
        "home_ctrl": HOME_CTRL,
    }
    assert morphology["asset_provenance"] == {
        "model_title": "Google Barkour vB Description (MJCF)",
        "upstream": "https://github.com/google-deepmind/mujoco_menagerie/tree/da76818e269b82289eba39808e2fb91d679d6994/google_barkour_vb",
        "derived_revision": "da76818e269b82289eba39808e2fb91d679d6994",
        "license": "Apache-2.0",
        "license_file": "assets/LICENSE",
        "source_file": "assets/SOURCE.md",
    }

    canonical_files = sorted(
        path.relative_to(ASSETS_ROOT).as_posix()
        for path in ASSETS_ROOT.rglob("*")
        if path.is_file()
    )
    assert canonical_files == sorted(EXPECTED_CLOSURE_FILES)
    assert not any(path.is_symlink() for path in ASSETS_ROOT.rglob("*"))
    for relative_path in EXPECTED_CLOSURE_FILES:
        canonical_path = ASSETS_ROOT / relative_path
        migration_path = MIGRATION_ROOT / relative_path
        assert canonical_path.is_file(), relative_path
        assert migration_path.is_file(), relative_path
        assert canonical_path.read_bytes() == migration_path.read_bytes(), relative_path

    source = (ASSETS_ROOT / "SOURCE.md").read_text(encoding="utf-8")
    assert "https://github.com/google-deepmind/mujoco_menagerie/tree/da76818e269b82289eba39808e2fb91d679d6994/google_barkour_vb" in source
    assert "da76818e269b82289eba39808e2fb91d679d6994" in source
    assert "Apache-2.0" in source
    assert "Apache License" in (ASSETS_ROOT / "LICENSE").read_text(encoding="utf-8")

    for xml_path in ASSETS_ROOT.glob("*.xml"):
        root = ET.parse(xml_path).getroot()
        for element in root.iter():
            for attribute, value in element.attrib.items():
                if attribute == "file":
                    _assert_local_reference(value)
                assert "://" not in value

    assert mujoco.__version__ == "3.3.6"
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (model.nq, model.nv, model.nu, model.njnt, model.nsite, model.nsensor, model.nkey) == (
        19,
        18,
        12,
        13,
        21,
        30,
        1,
    )
    assert _names(model, mujoco.mjtObj.mjOBJ_JOINT, model.njnt) == ALL_JOINT_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu) == ACTUATOR_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SITE, model.nsite) == SITE_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SENSOR, model.nsensor) == SENSOR_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_KEY, model.nkey) == ["home"]

    torso_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "torso")
    assert int(model.jnt_type[torso_id]) == int(mujoco.mjtJoint.mjJNT_FREE)
    assert not bool(model.jnt_limited[torso_id])
    for joint_name in ACTUATED_JOINT_NAMES:
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_HINGE)
        assert bool(model.jnt_limited[joint_id])
        np.testing.assert_allclose(model.jnt_range[joint_id], JOINT_LIMITS[joint_name], rtol=0.0, atol=0.0)

    for actuator_name, joint_name in zip(ACTUATOR_NAMES, ACTUATED_JOINT_NAMES):
        actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.actuator_trntype[actuator_id]) == int(mujoco.mjtTrn.mjTRN_JOINT)
        assert int(model.actuator_trnid[actuator_id, 0]) == joint_id
        assert int(model.actuator_gaintype[actuator_id]) == int(mujoco.mjtGain.mjGAIN_FIXED)
        assert int(model.actuator_biastype[actuator_id]) == int(mujoco.mjtBias.mjBIAS_AFFINE)
        assert float(model.actuator_gainprm[actuator_id, 0]) == 50.0
        assert float(model.actuator_biasprm[actuator_id, 1]) == -50.0
        assert float(model.actuator_biasprm[actuator_id, 2]) == -0.5
        assert bool(model.actuator_ctrllimited[actuator_id])
        np.testing.assert_allclose(
            model.actuator_ctrlrange[actuator_id], ACTUATOR_CTRLRANGES[actuator_name], rtol=0.0, atol=0.0
        )
        assert bool(model.actuator_forcelimited[actuator_id])
        np.testing.assert_allclose(model.actuator_forcerange[actuator_id], [-18.0, 18.0], rtol=0.0, atol=0.0)

    torso_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "torso")
    imu_site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "imu_frame")
    assert int(model.site_bodyid[imu_site_id]) == torso_body_id
    expected_foot_bodies = {
        "foot_front_left": "lower_leg_front_left",
        "foot_hind_left": "lower_leg_2",
        "foot_front_right": "lower_leg_3",
        "foot_hind_right": "lower_leg_4",
    }
    for site_name, body_name in expected_foot_bodies.items():
        site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, site_name)
        assert mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(model.site_bodyid[site_id])) == body_name

    for index, sensor_name in enumerate(SENSOR_NAMES[:12]):
        sensor_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SENSOR, sensor_name)
        assert int(model.sensor_type[sensor_id]) == int(mujoco.mjtSensor.mjSENS_JOINTPOS)
        assert int(model.sensor_objid[sensor_id]) == index + 1
    for index, sensor_name in enumerate(SENSOR_NAMES[12:24]):
        sensor_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SENSOR, sensor_name)
        assert int(model.sensor_type[sensor_id]) == int(mujoco.mjtSensor.mjSENS_JOINTVEL)
        assert int(model.sensor_objid[sensor_id]) == index + 1
    imu_sensor_types = [
        mujoco.mjtSensor.mjSENS_GYRO,
        mujoco.mjtSensor.mjSENS_ACCELEROMETER,
        mujoco.mjtSensor.mjSENS_FRAMEQUAT,
        mujoco.mjtSensor.mjSENS_FRAMEPOS,
        mujoco.mjtSensor.mjSENS_FRAMELINVEL,
        mujoco.mjtSensor.mjSENS_FRAMEANGVEL,
    ]
    for sensor_name, sensor_type in zip(SENSOR_NAMES[24:], imu_sensor_types):
        sensor_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SENSOR, sensor_name)
        assert int(model.sensor_type[sensor_id]) == int(sensor_type)
        assert int(model.sensor_objid[sensor_id]) == imu_site_id

    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    np.testing.assert_allclose(model.key_qpos[home_id], HOME_QPOS, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(model.key_ctrl[home_id], HOME_CTRL, rtol=0.0, atol=0.0)

    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    np.testing.assert_allclose(data.qpos, HOME_QPOS, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(data.ctrl, HOME_CTRL, rtol=0.0, atol=0.0)
    data.ctrl[0] = 0.1
    time_before = data.time
    mujoco.mj_step(model, data)
    assert data.time > time_before
    assert data.time == pytest.approx(model.opt.timestep)
    assert data.ctrl[0] == 0.1
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.qacc).all()
    assert np.isfinite(data.ctrl).all()

    runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
    assert "google_barkour_vb" not in runnable_index["robots"]
    research_index = json.loads(RESEARCH_INDEX_PATH.read_text(encoding="utf-8"))
    candidate = next(
        item
        for item in research_index["candidates"]
        if item["robot_configuration_id"] == "google_barkour_vb"
    )
    observed_paths = {item["path"] for item in candidate["locally_observed_source_material"]}
    assert "autoadapter/libraries/robots/google_barkour_vb/1.0.0/assets/scene.xml" in observed_paths
    assert "autoadapter/libraries/robots/google_barkour_vb/1.0.0/morphology.json" in observed_paths
    missing = " ".join(candidate["missing_for_runnable_package"])
    assert "complete local MuJoCo asset closure" not in missing
    assert "current mainline morphology.json" not in missing
    assert "20 distinct applicable source-backed tasks" in missing
    assert "tasks/sources.json" in missing
    assert not (PACKAGE_ROOT / "tasks").exists()
    assert not (PACKAGE_ROOT / "skeleton").exists()
    assert not (PACKAGE_ROOT / "reference").exists()
