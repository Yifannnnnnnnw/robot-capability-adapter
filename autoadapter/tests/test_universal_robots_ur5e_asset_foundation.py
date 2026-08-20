from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "universal_robots_ur5e" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
SCENE_PATH = ASSETS_ROOT / "scene.xml"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"

JOINT_NAMES = [
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
]
ACTUATOR_NAMES = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow",
    "wrist_1",
    "wrist_2",
    "wrist_3",
]
JOINT_LIMITS = {
    "shoulder_pan_joint": [-6.28319, 6.28319],
    "shoulder_lift_joint": [-6.28319, 6.28319],
    "elbow_joint": [-3.1415, 3.1415],
    "wrist_1_joint": [-6.28319, 6.28319],
    "wrist_2_joint": [-6.28319, 6.28319],
    "wrist_3_joint": [-6.28319, 6.28319],
}
ACTUATOR_CTRLRANGES = {
    "shoulder_pan": [-6.2831, 6.2831],
    "shoulder_lift": [-6.2831, 6.2831],
    "elbow": [-3.1415, 3.1415],
    "wrist_1": [-6.2831, 6.2831],
    "wrist_2": [-6.2831, 6.2831],
    "wrist_3": [-6.2831, 6.2831],
}
ACTUATOR_DEFAULTS = {
    "shoulder_pan": (2000.0, 400.0, [-150.0, 150.0]),
    "shoulder_lift": (2000.0, 400.0, [-150.0, 150.0]),
    "elbow": (2000.0, 400.0, [-150.0, 150.0]),
    "wrist_1": (500.0, 100.0, [-28.0, 28.0]),
    "wrist_2": (500.0, 100.0, [-28.0, 28.0]),
    "wrist_3": (500.0, 100.0, [-28.0, 28.0]),
}
HOME = [-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0]
MESH_FILES = (
    "base_0.obj",
    "base_1.obj",
    "shoulder_0.obj",
    "shoulder_1.obj",
    "shoulder_2.obj",
    "upperarm_0.obj",
    "upperarm_1.obj",
    "upperarm_2.obj",
    "upperarm_3.obj",
    "forearm_0.obj",
    "forearm_1.obj",
    "forearm_2.obj",
    "forearm_3.obj",
    "wrist1_0.obj",
    "wrist1_1.obj",
    "wrist1_2.obj",
    "wrist2_0.obj",
    "wrist2_1.obj",
    "wrist2_2.obj",
    "wrist3.obj",
)
EXPECTED_CLOSURE_FILES = (
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "SOURCE.md",
    "scene.xml",
    "ur5e.png",
    "ur5e.xml",
    *(f"assets/{filename}" for filename in MESH_FILES),
)


def _names(model: mujoco.MjModel, obj: mujoco.mjtObj, count: int) -> list[str | None]:
    return [mujoco.mj_id2name(model, obj, index) for index in range(count)]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _assert_local_file(base: Path, reference: str) -> None:
    assert "://" not in reference
    reference_path = Path(reference)
    assert not reference_path.is_absolute()
    resolved = (base / reference_path).resolve()
    assert _is_under(resolved, ASSETS_ROOT.resolve())
    assert resolved.is_file(), reference


def _assert_xml_references_are_local(path: Path) -> None:
    root = ET.parse(path).getroot()
    compiler = root.find("compiler")
    meshdir = Path(compiler.attrib.get("meshdir", "")) if compiler is not None else Path()
    texturedir = Path(compiler.attrib.get("texturedir", "")) if compiler is not None else Path()
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
        _assert_local_file(base, reference)


def test_universal_robots_ur5e_asset_foundation_is_local_and_live() -> None:
    morphology = json.loads(MORPHOLOGY_PATH.read_text(encoding="utf-8"))
    assert morphology["schema_version"] == "1.0"
    assert morphology["robot_model_id"] == "universal_robots_ur5e"
    assert morphology["robot_configuration_id"] == "universal_robots_ur5e"
    assert morphology["package_version"] == "1.0.0"
    assert morphology["morphology_kind"] == "fixed_base_serial_manipulator"
    assert morphology["base_type"] == "fixed"
    assert morphology["degrees_of_freedom"] == {"arm": 6}
    assert morphology["model_dimensions"] == {"nq": 6, "nv": 6, "nu": 6}
    assert morphology["mjcf_entrypoint"] == "assets/scene.xml"
    assert "gripper" not in json.dumps(morphology).lower()
    assert "grasp" not in json.dumps(morphology).lower()

    control = morphology["public_control"]
    assert control["actuation"] == "joint_position"
    assert control["transmission"] == "direct_joint"
    assert control["actuator_type"] == "general"
    assert control["bias_type"] == "affine"
    assert control["joint_type"] == "hinge"
    assert control["joint_names"] == JOINT_NAMES
    assert control["actuator_names"] == ACTUATOR_NAMES
    assert control["actuator_joint_map"] == dict(zip(ACTUATOR_NAMES, JOINT_NAMES))
    assert control["arm_joint_limits_rad"] == JOINT_LIMITS
    assert control["actuator_ctrlrange_rad"] == ACTUATOR_CTRLRANGES
    assert control["position_actuator_defaults"] == {
        "large_actuator": {
            "actuator_names": ["shoulder_pan", "shoulder_lift", "elbow"],
            "kp": 2000.0,
            "kv": 400.0,
            "forcerange": [-150.0, 150.0],
        },
        "small_actuator": {
            "actuator_names": ["wrist_1", "wrist_2", "wrist_3"],
            "kp": 500.0,
            "kv": 100.0,
            "forcerange": [-28.0, 28.0],
        },
    }
    assert morphology["public_observations"] == {
        "end_effector_site": "attachment_site",
        "end_effector_body": "wrist_3_link",
        "sensor_names": [],
    }
    assert morphology["public_affordances"] == {
        "actions": ["joint_position_control"],
        "observations": [
            "joint_positions",
            "joint_velocities",
            "end_effector_pose",
            "named_body_pose",
            "named_site_pose",
            "simulation_time",
        ],
    }
    assert morphology["reset_fact"] == {
        "owner": "framework",
        "default": "model_qpos0",
        "available_keyframe": "home",
        "home_qpos": HOME,
        "home_ctrl": HOME,
    }
    assert morphology["asset_provenance"] == {
        "model_title": "Universal Robots UR5e Description (MJCF)",
        "upstream": "https://github.com/981526092/auto-adapter",
        "derived_revision": "585eb1f1fde33f17f5f9a1e169a18dd41f97b586",
        "source_path": "assets/mjcf/universal_robots_ur5e/",
        "license": "BSD-3-Clause",
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
        assert (ASSETS_ROOT / relative_path).is_file(), relative_path

    source = (ASSETS_ROOT / "SOURCE.md").read_text(encoding="utf-8")
    assert "https://github.com/981526092/auto-adapter" in source
    assert "585eb1f1fde33f17f5f9a1e169a18dd41f97b586" in source
    assert "BSD-3-Clause" in (ASSETS_ROOT / "README.md").read_text(encoding="utf-8")

    for xml_path in sorted(ASSETS_ROOT.rglob("*.xml")):
        _assert_xml_references_are_local(xml_path)
    scene_root = ET.parse(SCENE_PATH).getroot()
    assert [element.attrib["file"] for element in scene_root.iter("include")] == ["ur5e.xml"]
    model_xml = ASSETS_ROOT / "ur5e.xml"
    model_root = ET.parse(model_xml).getroot()
    compiler = model_root.find("compiler")
    assert compiler is not None
    assert compiler.attrib["meshdir"] == "assets"
    assert [element.attrib["file"] for element in model_root.findall("asset/mesh")] == list(MESH_FILES)

    assert mujoco.__version__ == "3.3.6"
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (
        model.nq,
        model.nv,
        model.nu,
        model.njnt,
        model.nsite,
        model.nsensor,
        model.nkey,
    ) == (6, 6, 6, 6, 1, 0, 1)
    assert _names(model, mujoco.mjtObj.mjOBJ_JOINT, model.njnt) == JOINT_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu) == ACTUATOR_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SITE, model.nsite) == ["attachment_site"]
    assert _names(model, mujoco.mjtObj.mjOBJ_SENSOR, model.nsensor) == []
    assert _names(model, mujoco.mjtObj.mjOBJ_KEY, model.nkey) == ["home"]

    base_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base")
    assert int(model.body_parentid[base_id]) == 0
    assert int(model.body_jntnum[base_id]) == 0

    for joint_name in JOINT_NAMES:
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_HINGE)
        assert bool(model.jnt_limited[joint_id])
        np.testing.assert_allclose(model.jnt_range[joint_id], JOINT_LIMITS[joint_name], rtol=0.0, atol=0.0)

    for actuator_name, joint_name in zip(ACTUATOR_NAMES, JOINT_NAMES):
        actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.actuator_trntype[actuator_id]) == int(mujoco.mjtTrn.mjTRN_JOINT)
        assert int(model.actuator_trnid[actuator_id, 0]) == joint_id
        assert int(model.actuator_gaintype[actuator_id]) == int(mujoco.mjtGain.mjGAIN_FIXED)
        assert int(model.actuator_biastype[actuator_id]) == int(mujoco.mjtBias.mjBIAS_AFFINE)
        kp, kv, forcerange = ACTUATOR_DEFAULTS[actuator_name]
        assert float(model.actuator_gainprm[actuator_id, 0]) == kp
        assert float(model.actuator_biasprm[actuator_id, 1]) == -kp
        assert float(model.actuator_biasprm[actuator_id, 2]) == -kv
        assert bool(model.actuator_ctrllimited[actuator_id])
        np.testing.assert_allclose(
            model.actuator_ctrlrange[actuator_id], ACTUATOR_CTRLRANGES[actuator_name], rtol=0.0, atol=0.0
        )
        assert bool(model.actuator_forcelimited[actuator_id])
        np.testing.assert_allclose(model.actuator_forcerange[actuator_id], forcerange, rtol=0.0, atol=0.0)

    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "attachment_site")
    assert mujoco.mj_id2name(
        model, mujoco.mjtObj.mjOBJ_BODY, int(model.site_bodyid[site_id])
    ) == "wrist_3_link"

    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    np.testing.assert_allclose(model.key_qpos[home_id], HOME, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(model.key_ctrl[home_id], HOME, rtol=0.0, atol=0.0)

    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    np.testing.assert_allclose(data.qpos, HOME, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(data.ctrl, HOME, rtol=0.0, atol=0.0)
    mujoco.mj_forward(model, data)
    command = np.array([-1.2, -1.2, 1.0, -1.2, -1.2, 0.3], dtype=float)
    assert np.isfinite(command).all()
    data.ctrl[:] = command
    qpos_before = data.qpos.copy()
    time_before = float(data.time)
    for _ in range(8):
        mujoco.mj_step(model, data)
    assert data.time > time_before
    np.testing.assert_allclose(data.ctrl, command, rtol=0.0, atol=0.0)
    assert np.any(np.abs(data.qpos - qpos_before) > 0.0)
    assert np.any(np.abs(data.qvel) > 0.0)
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.qacc).all()
    assert np.isfinite(data.ctrl).all()
    assert np.isfinite(data.time)

    runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
    assert "universal_robots_ur5e" not in runnable_index["robots"]

    research_index = json.loads(RESEARCH_INDEX_PATH.read_text(encoding="utf-8"))
    candidate_ids = {
        item["robot_configuration_id"] for item in research_index["candidates"]
    }
    assert "universal_robots_ur5e" not in candidate_ids
    candidate = next(
        item
        for item in research_index["candidates"]
        if item["robot_configuration_id"]
        == "universal_robots_ur5e_robotiq_2f85"
    )
    observed_paths = {
        item["path"] for item in candidate["locally_observed_source_material"]
    }
    assert {
        "autoadapter/libraries/robots/universal_robots_ur5e/1.0.0/assets/scene.xml",
        "autoadapter/libraries/robots/universal_robots_ur5e/1.0.0/morphology.json",
    }.issubset(observed_paths)
