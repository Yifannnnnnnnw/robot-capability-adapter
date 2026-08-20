from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "aloha_2" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
SCENE_PATH = ASSETS_ROOT / "scene.xml"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"

EXPECTED_INVENTORY = (
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "SOURCE.md",
    "aloha.png",
    "aloha.xml",
    "aloha_wrist.png",
    "assets/angled_extrusion.stl",
    "assets/corner_bracket.stl",
    "assets/d405_solid.stl",
    "assets/extrusion_1000.stl",
    "assets/extrusion_1220.stl",
    "assets/extrusion_150.stl",
    "assets/extrusion_2040_1000.stl",
    "assets/extrusion_2040_880.stl",
    "assets/extrusion_600.stl",
    "assets/interbotix_black.png",
    "assets/overhead_mount.stl",
    "assets/small_meta_table_diffuse.png",
    "assets/tablelegs.obj",
    "assets/tabletop.obj",
    "assets/vx300s_1_base.stl",
    "assets/vx300s_2_shoulder.stl",
    "assets/vx300s_3_upper_arm.stl",
    "assets/vx300s_4_upper_forearm.stl",
    "assets/vx300s_5_lower_forearm.stl",
    "assets/vx300s_6_wrist.stl",
    "assets/vx300s_7_gripper.stl",
    "assets/vx300s_7_gripper_bar.stl",
    "assets/vx300s_7_gripper_camera.stl",
    "assets/vx300s_7_gripper_prop.stl",
    "assets/vx300s_7_gripper_prop_bar.stl",
    "assets/vx300s_7_gripper_wrist_mount.stl",
    "assets/vx300s_8_custom_finger_left.stl",
    "assets/vx300s_8_custom_finger_right.stl",
    "assets/wormseye_mount.stl",
    "filtered_cartesian_actuators.xml",
    "joint_position_actuators.xml",
    "keyframe_ctrl.xml",
    "keyframe_no_act.xml",
    "mjx_aloha.patch",
    "mjx_filtered_cartesian_actuators.patch",
    "mjx_scene.patch",
    "scene.xml",
)

EXPECTED_RUNTIME_CLOSURE = (
    "aloha.xml",
    "joint_position_actuators.xml",
    "keyframe_ctrl.xml",
    "scene.xml",
    "assets/angled_extrusion.stl",
    "assets/corner_bracket.stl",
    "assets/d405_solid.stl",
    "assets/extrusion_1000.stl",
    "assets/extrusion_1220.stl",
    "assets/extrusion_150.stl",
    "assets/extrusion_2040_1000.stl",
    "assets/extrusion_2040_880.stl",
    "assets/extrusion_600.stl",
    "assets/overhead_mount.stl",
    "assets/small_meta_table_diffuse.png",
    "assets/tablelegs.obj",
    "assets/tabletop.obj",
    "assets/vx300s_1_base.stl",
    "assets/vx300s_2_shoulder.stl",
    "assets/vx300s_3_upper_arm.stl",
    "assets/vx300s_4_upper_forearm.stl",
    "assets/vx300s_5_lower_forearm.stl",
    "assets/vx300s_6_wrist.stl",
    "assets/vx300s_7_gripper_bar.stl",
    "assets/vx300s_7_gripper_prop.stl",
    "assets/vx300s_7_gripper_wrist_mount.stl",
    "assets/vx300s_8_custom_finger_left.stl",
    "assets/vx300s_8_custom_finger_right.stl",
    "assets/wormseye_mount.stl",
)

ARM_JOINT_NAMES = [
    "left/waist",
    "left/shoulder",
    "left/elbow",
    "left/forearm_roll",
    "left/wrist_angle",
    "left/wrist_rotate",
    "right/waist",
    "right/shoulder",
    "right/elbow",
    "right/forearm_roll",
    "right/wrist_angle",
    "right/wrist_rotate",
]
JOINT_NAMES = [
    "left/waist",
    "left/shoulder",
    "left/elbow",
    "left/forearm_roll",
    "left/wrist_angle",
    "left/wrist_rotate",
    "left/left_finger",
    "left/right_finger",
    "right/waist",
    "right/shoulder",
    "right/elbow",
    "right/forearm_roll",
    "right/wrist_angle",
    "right/wrist_rotate",
    "right/left_finger",
    "right/right_finger",
]
ACTUATOR_NAMES = [
    "left/waist",
    "left/shoulder",
    "left/elbow",
    "left/forearm_roll",
    "left/wrist_angle",
    "left/wrist_rotate",
    "left/gripper",
    "right/waist",
    "right/shoulder",
    "right/elbow",
    "right/forearm_roll",
    "right/wrist_angle",
    "right/wrist_rotate",
    "right/gripper",
]
ACTUATOR_JOINT_MAP = {
    "left/waist": "left/waist",
    "left/shoulder": "left/shoulder",
    "left/elbow": "left/elbow",
    "left/forearm_roll": "left/forearm_roll",
    "left/wrist_angle": "left/wrist_angle",
    "left/wrist_rotate": "left/wrist_rotate",
    "left/gripper": "left/left_finger",
    "right/waist": "right/waist",
    "right/shoulder": "right/shoulder",
    "right/elbow": "right/elbow",
    "right/forearm_roll": "right/forearm_roll",
    "right/wrist_angle": "right/wrist_angle",
    "right/wrist_rotate": "right/wrist_rotate",
    "right/gripper": "right/left_finger",
}
JOINT_LIMITS = {
    "left/waist": [-3.14158, 3.14158],
    "left/shoulder": [-1.85005, 1.25664],
    "left/elbow": [-1.76278, 1.6057],
    "left/forearm_roll": [-3.14158, 3.14158],
    "left/wrist_angle": [-1.8675, 2.23402],
    "left/wrist_rotate": [-3.14158, 3.14158],
    "right/waist": [-3.14158, 3.14158],
    "right/shoulder": [-1.85005, 1.25664],
    "right/elbow": [-1.76278, 1.6057],
    "right/forearm_roll": [-3.14158, 3.14158],
    "right/wrist_angle": [-1.8675, 2.23402],
    "right/wrist_rotate": [-3.14158, 3.14158],
}
FINGER_JOINT_NAMES = [
    "left/left_finger",
    "left/right_finger",
    "right/left_finger",
    "right/right_finger",
]
FINGER_CONTACT_SPHERE_NAMES = [
    "left/left_g0",
    "left/left_g1",
    "left/left_g2",
    "left/right_g0",
    "left/right_g1",
    "left/right_g2",
    "right/left_g0",
    "right/left_g1",
    "right/left_g2",
    "right/right_g0",
    "right/right_g1",
    "right/right_g2",
]
SITE_NAMES = [
    "worldref",
    "left/gripper",
    "left/left_finger",
    "left/right_finger",
    "right/gripper",
    "right/left_finger",
    "right/right_finger",
]
CAMERA_NAMES = [
    "teleoperator_pov",
    "collaborator_pov",
    "overhead_cam",
    "worms_eye_cam",
    "wrist_cam_left",
    "wrist_cam_right",
]
NEUTRAL_QPOS = [
    0.0,
    -0.96,
    1.16,
    0.0,
    -0.3,
    0.0,
    0.0084,
    0.0084,
    0.0,
    -0.96,
    1.16,
    0.0,
    -0.3,
    0.0,
    0.0084,
    0.0084,
]
NEUTRAL_CTRL = [
    0.0,
    -0.96,
    1.16,
    0.0,
    -0.3,
    0.0,
    0.0084,
    0.0,
    -0.96,
    1.16,
    0.0,
    -0.3,
    0.0,
    0.0084,
]


def _names(model: mujoco.MjModel, obj: mujoco.mjtObj, count: int) -> list[str | None]:
    return [mujoco.mj_id2name(model, obj, index) for index in range(count)]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _assert_local_reference(source: Path, base: Path, reference: str) -> Path:
    assert "://" not in reference
    reference_path = Path(reference)
    assert not reference_path.is_absolute()
    resolved = (base / reference_path).resolve()
    assert _is_under(resolved, ASSETS_ROOT.resolve())
    assert resolved.is_file(), f"{source}: {reference}"
    return resolved


def _xml_references(path: Path) -> list[tuple[Path, str]]:
    root = ET.parse(path).getroot()
    compiler = root.find("compiler")
    meshdir = Path(compiler.attrib.get("meshdir", "")) if compiler is not None else Path()
    texturedir = (
        Path(compiler.attrib.get("texturedir", ""))
        if compiler is not None
        else Path()
    )
    references = []
    for element in root.iter():
        reference = element.attrib.get("file")
        if reference is None:
            continue
        if element.tag == "mesh":
            base = path.parent / meshdir
        elif element.tag == "texture":
            base = path.parent / texturedir
        else:
            base = path.parent
        references.append((_assert_local_reference(path, base, reference), element.tag))
    return references


def _runtime_closure(path: Path, seen: set[Path] | None = None) -> set[Path]:
    seen = set() if seen is None else seen
    path = path.resolve()
    assert _is_under(path, ASSETS_ROOT.resolve())
    if path in seen:
        return seen
    seen.add(path)
    for resolved, tag in _xml_references(path):
        if tag == "include":
            _runtime_closure(resolved, seen)
        else:
            seen.add(resolved)
    return seen


def _assert_finite(data: mujoco.MjData) -> None:
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.qacc).all()
    assert np.isfinite(data.ctrl).all()
    assert np.isfinite(data.time)


def test_aloha_2_asset_foundation_is_canonical_local_and_non_runtime() -> None:
    morphology = json.loads(MORPHOLOGY_PATH.read_text(encoding="utf-8"))
    assert morphology["schema_version"] == "1.0"
    assert morphology["robot_model_id"] == "aloha_2"
    assert morphology["robot_configuration_id"] == "aloha_2"
    assert morphology["package_version"] == "1.0.0"
    assert morphology["morphology_kind"] == "fixed_base_bimanual_manipulator"
    assert morphology["base_type"] == "fixed"
    assert morphology["degrees_of_freedom"] == {
        "arm_per_side": 6,
        "actuated_gripper_slide_per_side": 1,
        "passive_mirrored_finger_slide_per_side": 1,
    }
    assert morphology["mjcf_entrypoint"] == "assets/scene.xml"
    assert morphology["mujoco"] == {
        "version": "3.3.6",
        "nq": 16,
        "nv": 16,
        "nu": 14,
        "njnt": 16,
        "nsite": 7,
        "nsensor": 0,
        "ncam": 6,
        "neq": 2,
        "nkey": 1,
        "na": 0,
    }

    control = morphology["public_control"]
    assert control["actuation"] == "joint_position"
    assert control["transmission"] == "direct_joint"
    assert control["joint_types"] == {"arm": "hinge", "finger": "slide"}
    assert control["joint_names"] == JOINT_NAMES
    assert control["actuator_names"] == ACTUATOR_NAMES
    assert control["actuator_joint_map"] == ACTUATOR_JOINT_MAP
    assert control["gripper_joint_range_m"] == [0.0, 0.041]
    assert control["gripper_ctrl_range_m"] == [0.002, 0.037]
    assert control["gripper_direction"] == {
        "smaller_command": "closed",
        "larger_command": "open",
        "unit": "m",
    }
    assert control["actuator_state"] == "none"

    observations = morphology["public_observations"]
    assert observations["site_names"] == SITE_NAMES
    assert observations["sensors"] == []
    assert observations["cameras"] == CAMERA_NAMES
    assert morphology["public_affordances"] == {
        "actions": ["joint_position_control", "gripper_position_control"],
        "observations": [
            "joint_positions",
            "joint_velocities",
            "named_site_positions",
            "named_site_poses",
            "contact_state",
            "simulation_time",
        ],
    }
    assert morphology["reset_fact"]["available_keyframe"] == "neutral_pose"
    assert morphology["reset_fact"]["neutral_qpos"] == NEUTRAL_QPOS
    assert morphology["reset_fact"]["neutral_ctrl"] == NEUTRAL_CTRL
    assert morphology["asset_provenance"] == {
        "model_title": "ALOHA Description (MJCF)",
        "upstream": "https://github.com/google-deepmind/mujoco_menagerie/tree/da76818e269b82289eba39808e2fb91d679d6994/aloha",
        "derived_revision": "da76818e269b82289eba39808e2fb91d679d6994",
        "source_path": "aloha/scene.xml",
        "license": "BSD-3-Clause",
        "license_file": "assets/LICENSE",
        "source_file": "assets/SOURCE.md",
    }

    canonical_inventory = sorted(
        path.relative_to(ASSETS_ROOT).as_posix()
        for path in ASSETS_ROOT.rglob("*")
        if path.is_file()
    )
    assert canonical_inventory == list(EXPECTED_INVENTORY)
    assert len(canonical_inventory) == 44
    assert not any(path.is_symlink() for path in ASSETS_ROOT.rglob("*"))

    for xml_path in sorted(ASSETS_ROOT.glob("*.xml")):
        _xml_references(xml_path)
    runtime_paths = _runtime_closure(SCENE_PATH)
    runtime_inventory = sorted(
        path.relative_to(ASSETS_ROOT).as_posix() for path in runtime_paths
    )
    assert runtime_inventory == sorted(EXPECTED_RUNTIME_CLOSURE)
    assert len(runtime_inventory) == 29
    assert not any(
        _is_under(path, ASSETS_ROOT.resolve()) is False for path in runtime_paths
    )

    assert mujoco.__version__ == "3.3.6"
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (
        model.nq,
        model.nv,
        model.nu,
        model.njnt,
        model.nsite,
        model.nsensor,
        model.ncam,
        model.neq,
        model.nkey,
        model.na,
    ) == (16, 16, 14, 16, 7, 0, 6, 2, 1, 0)
    assert _names(model, mujoco.mjtObj.mjOBJ_JOINT, model.njnt) == JOINT_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu) == ACTUATOR_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SITE, model.nsite) == SITE_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SENSOR, model.nsensor) == []
    assert _names(model, mujoco.mjtObj.mjOBJ_CAMERA, model.ncam) == CAMERA_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_KEY, model.nkey) == ["neutral_pose"]

    for index, joint_name in enumerate(JOINT_NAMES):
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        expected_type = (
            mujoco.mjtJoint.mjJNT_SLIDE
            if joint_name in FINGER_JOINT_NAMES
            else mujoco.mjtJoint.mjJNT_HINGE
        )
        assert int(model.jnt_type[joint_id]) == int(expected_type)
        assert bool(model.jnt_limited[joint_id])
        expected_range = [0.0, 0.041] if joint_name in FINGER_JOINT_NAMES else JOINT_LIMITS[joint_name]
        np.testing.assert_allclose(
            model.jnt_range[joint_id], expected_range, rtol=0.0, atol=0.0
        )
        assert int(model.jnt_qposadr[joint_id]) == index

    for actuator_name in ACTUATOR_NAMES:
        actuator_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name
        )
        joint_name = ACTUATOR_JOINT_MAP[actuator_name]
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.actuator_trntype[actuator_id]) == int(
            mujoco.mjtTrn.mjTRN_JOINT
        )
        assert int(model.actuator_trnid[actuator_id, 0]) == joint_id
        assert int(model.actuator_gaintype[actuator_id]) == int(
            mujoco.mjtGain.mjGAIN_FIXED
        )
        assert int(model.actuator_biastype[actuator_id]) == int(
            mujoco.mjtBias.mjBIAS_AFFINE
        )
        assert bool(model.actuator_ctrllimited[actuator_id])
        expected_range = (
            [0.002, 0.037]
            if actuator_name.endswith("/gripper")
            else JOINT_LIMITS[joint_name]
        )
        np.testing.assert_allclose(
            model.actuator_ctrlrange[actuator_id], expected_range, rtol=0.0, atol=0.0
        )

    equality_pairs = [
        ("left/left_finger", "left/right_finger"),
        ("right/left_finger", "right/right_finger"),
    ]
    assert model.neq == len(equality_pairs)
    for equality_id, (controlled, mirrored) in enumerate(equality_pairs):
        assert int(model.eq_type[equality_id]) == int(mujoco.mjtEq.mjEQ_JOINT)
        assert int(model.eq_obj1id[equality_id]) == mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_JOINT, controlled
        )
        assert int(model.eq_obj2id[equality_id]) == mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_JOINT, mirrored
        )
        np.testing.assert_allclose(
            model.eq_data[equality_id, :5], [0.0, 1.0, 0.0, 0.0, 0.0], rtol=0.0, atol=0.0
        )

    expected_site_bodies = [
        "world",
        "left/gripper_link",
        "left/left_finger_link",
        "left/right_finger_link",
        "right/gripper_link",
        "right/left_finger_link",
        "right/right_finger_link",
    ]
    assert [
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(body_id))
        for body_id in model.site_bodyid
    ] == expected_site_bodies

    neutral_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "neutral_pose")
    np.testing.assert_allclose(model.key_qpos[neutral_id], NEUTRAL_QPOS, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(model.key_ctrl[neutral_id], NEUTRAL_CTRL, rtol=0.0, atol=0.0)

    for sphere_name in FINGER_CONTACT_SPHERE_NAMES:
        geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, sphere_name)
        assert geom_id >= 0
        assert int(model.geom_type[geom_id]) == int(mujoco.mjtGeom.mjGEOM_SPHERE)
        assert float(model.geom_size[geom_id, 0]) == 0.0006
        assert int(model.geom_contype[geom_id]) == 1
        assert int(model.geom_conaffinity[geom_id]) == 1
    for geom_name in ("table", "floor"):
        geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, geom_name)
        assert geom_id >= 0
        assert int(model.geom_contype[geom_id]) != 0
        assert int(model.geom_conaffinity[geom_id]) != 0

    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, neutral_id)
    mujoco.mj_forward(model, data)
    np.testing.assert_allclose(data.qpos, NEUTRAL_QPOS, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(data.ctrl, NEUTRAL_CTRL, rtol=0.0, atol=0.0)

    left_waist_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "left/waist")
    left_waist_qpos = int(model.jnt_qposadr[left_waist_id])
    arm_qpos_before = float(data.qpos[left_waist_qpos])
    data.ctrl[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, "left/waist")] = 0.5
    for _ in range(200):
        mujoco.mj_step(model, data)
    assert abs(float(data.qpos[left_waist_qpos]) - arm_qpos_before) > 1e-3
    _assert_finite(data)

    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, neutral_id)
    mujoco.mj_forward(model, data)
    gripper_qpos_adrs = [
        int(
            model.jnt_qposadr[
                mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
            ]
        )
        for joint_name in ("left/left_finger", "left/right_finger", "right/left_finger", "right/right_finger")
    ]
    initial_gripper_qpos = data.qpos[gripper_qpos_adrs].copy()
    for actuator_name in ("left/gripper", "right/gripper"):
        actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
        data.ctrl[actuator_id] = 0.037
    for _ in range(250):
        mujoco.mj_step(model, data)
    opened_gripper_qpos = data.qpos[gripper_qpos_adrs].copy()
    assert np.all(opened_gripper_qpos > initial_gripper_qpos + 1e-3)
    np.testing.assert_allclose(opened_gripper_qpos[0], opened_gripper_qpos[2], rtol=0.0, atol=1e-9)
    np.testing.assert_allclose(opened_gripper_qpos[1], opened_gripper_qpos[3], rtol=0.0, atol=1e-9)
    _assert_finite(data)

    for actuator_name in ("left/gripper", "right/gripper"):
        actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
        data.ctrl[actuator_id] = 0.002
    for _ in range(250):
        mujoco.mj_step(model, data)
    closed_gripper_qpos = data.qpos[gripper_qpos_adrs].copy()
    assert np.all(closed_gripper_qpos < opened_gripper_qpos - 1e-3)
    np.testing.assert_allclose(closed_gripper_qpos[0], closed_gripper_qpos[2], rtol=0.0, atol=1e-9)
    np.testing.assert_allclose(closed_gripper_qpos[1], closed_gripper_qpos[3], rtol=0.0, atol=1e-9)
    _assert_finite(data)

    runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
    assert "aloha_2" not in runnable_index["robots"]
    assert sorted(path.name for path in PACKAGE_ROOT.iterdir()) == [
        "assets",
        "morphology.json",
        "tasks",
    ]
    for forbidden in ("skeleton", "reference"):
        assert not (PACKAGE_ROOT / forbidden).exists()
    assert not (PACKAGE_ROOT / "tasks" / "private").exists()

    research_index = json.loads(RESEARCH_INDEX_PATH.read_text(encoding="utf-8"))
    candidate = next(
        item
        for item in research_index["candidates"]
        if item["robot_configuration_id"] == "aloha_2"
    )
    assert candidate["observed_task_count"] == 5
    canonical_observations = [
        item
        for item in candidate["locally_observed_source_material"]
        if item["path"].startswith("autoadapter/libraries/robots/aloha_2/")
    ]
    assert {item["kind"] for item in canonical_observations} == {
        "canonical_mujoco_asset_foundation",
        "canonical_morphology_foundation",
        "canonical_task_sources",
        "canonical_task_catalog",
    }
    assert {
        item["path"] for item in canonical_observations
    } == {
        "autoadapter/libraries/robots/aloha_2/1.0.0/assets/scene.xml",
        "autoadapter/libraries/robots/aloha_2/1.0.0/morphology.json",
        "autoadapter/libraries/robots/aloha_2/1.0.0/tasks/sources.json",
        "autoadapter/libraries/robots/aloha_2/1.0.0/tasks/catalog.json",
    }
    assert all(
        phrase not in item["observation"].lower()
        for item in canonical_observations
        for phrase in ("task success", "task passed", "positive control", "admitted")
    )
    assert candidate["missing_for_runnable_package"] == [
        "Create Framework-private tasks/private/instances.json, bindings.json, and guards.json.",
        "Provide an applicable selected-arm ALOHA skeleton and a calibration-only reference driver for this configuration.",
        "Add the package check for the complete canonical package.",
        "Run a focused Direct-MuJoCo positive control against the canonical package.",
        "Run a dynamic canary with the required model and generation traces and terminal physical evidence.",
    ]
