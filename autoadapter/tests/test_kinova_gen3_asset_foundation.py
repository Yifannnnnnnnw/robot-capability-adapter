from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "kinova_gen3" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
SCENE_PATH = ASSETS_ROOT / "scene.xml"

JOINT_NAMES = [
    "joint_1",
    "joint_2",
    "joint_3",
    "joint_4",
    "joint_5",
    "joint_6",
    "joint_7",
]
ACTUATOR_NAMES = JOINT_NAMES.copy()
JOINT_LIMITS = {
    "joint_2": [-2.24, 2.24],
    "joint_4": [-2.57, 2.57],
    "joint_6": [-2.09, 2.09],
}
ACTUATOR_CTRLRANGES = {
    "joint_2": [-2.2497294058206907, 2.2497294058206907],
    "joint_4": [-2.5795966344476193, 2.5795966344476193],
    "joint_6": [-2.0996310901491784, 2.0996310901491784],
}
ACTUATOR_DEFAULTS = {
    "joint_1": (2000.0, 100.0, [-105.0, 105.0]),
    "joint_2": (2000.0, 100.0, [-105.0, 105.0]),
    "joint_3": (2000.0, 100.0, [-105.0, 105.0]),
    "joint_4": (2000.0, 100.0, [-105.0, 105.0]),
    "joint_5": (500.0, 50.0, [-52.0, 52.0]),
    "joint_6": (500.0, 50.0, [-52.0, 52.0]),
    "joint_7": (500.0, 50.0, [-52.0, 52.0]),
}
KEYFRAMES = {
    "home": [
        0.0,
        0.26179939,
        3.14159265,
        -2.26892803,
        0.0,
        0.95993109,
        1.57079633,
    ],
    "retract": [
        0.0,
        -0.34906585,
        3.14159265,
        -2.54818071,
        0.0,
        -0.87266463,
        1.57079633,
    ],
}
EXPECTED_CLOSURE_FILES = (
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "SOURCE.md",
    "gen3.png",
    "gen3.xml",
    "scene.xml",
    "assets/base_link.stl",
    "assets/bracelet_no_vision_link.stl",
    "assets/bracelet_with_vision_link.stl",
    "assets/forearm_link.stl",
    "assets/half_arm_1_link.stl",
    "assets/half_arm_2_link.stl",
    "assets/shoulder_link.stl",
    "assets/spherical_wrist_1_link.stl",
    "assets/spherical_wrist_2_link.stl",
)


def _names(model: mujoco.MjModel, obj: mujoco.mjtObj, count: int) -> list[str]:
    return [mujoco.mj_id2name(model, obj, index) for index in range(count)]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def test_kinova_gen3_asset_foundation_loads_local_closure_and_steps_physics() -> None:
    morphology = json.loads(MORPHOLOGY_PATH.read_text(encoding="utf-8"))
    assert morphology["robot_model_id"] == "kinova_gen3"
    assert morphology["robot_configuration_id"] == "kinova_gen3"
    assert morphology["package_version"] == "1.0.0"
    assert morphology["morphology_kind"] == "fixed_base_serial_manipulator"
    assert morphology["degrees_of_freedom"] == {"arm": 7}
    assert morphology["mjcf_entrypoint"] == "assets/scene.xml"
    assert "gripper" not in morphology["degrees_of_freedom"]

    control = morphology["public_control"]
    assert control["actuation"] == "joint_position"
    assert control["joint_type"] == "hinge"
    assert control["joint_names"] == JOINT_NAMES
    assert control["actuator_names"] == ACTUATOR_NAMES
    assert control["actuator_joint_map"] == dict(zip(ACTUATOR_NAMES, JOINT_NAMES))
    assert control["arm_joint_limits_rad"] == JOINT_LIMITS
    assert control["actuator_ctrlrange_rad"] == ACTUATOR_CTRLRANGES
    assert control["unlimited_joint_names"] == ["joint_1", "joint_3", "joint_5", "joint_7"]
    assert control["unlimited_actuator_names"] == ["joint_1", "joint_3", "joint_5", "joint_7"]
    assert morphology["public_observations"] == {
        "end_effector_site": "pinch_site",
        "end_effector_body": "bracelet_link",
    }
    assert morphology["public_affordances"]["actions"] == ["joint_position_control"]
    assert morphology["reset_fact"] == {
        "owner": "framework",
        "default": "model_qpos0",
        "available_keyframes": ["home", "retract"],
    }

    assert ASSETS_ROOT.is_dir()
    assert not any(path.is_symlink() for path in ASSETS_ROOT.rglob("*"))
    for relative_path in EXPECTED_CLOSURE_FILES:
        assert (ASSETS_ROOT / relative_path).is_file(), relative_path
    source = (ASSETS_ROOT / "SOURCE.md").read_text(encoding="utf-8")
    assert "da76818e269b82289eba39808e2fb91d679d6994" in source
    assert "BSD-3-Clause" in source

    scene_root = ET.parse(SCENE_PATH).getroot()
    include_refs = [element.attrib["file"] for element in scene_root.findall("include")]
    assert include_refs == ["gen3.xml"]
    assert all("://" not in reference and not Path(reference).is_absolute() for reference in include_refs)
    for reference in include_refs:
        include_path = (SCENE_PATH.parent / reference).resolve()
        assert include_path.is_file()
        assert _is_under(include_path, ASSETS_ROOT.resolve())

    model_xml = ASSETS_ROOT / "gen3.xml"
    model_root = ET.parse(model_xml).getroot()
    compiler = model_root.find("compiler")
    assert compiler is not None
    mesh_dir = compiler.attrib["meshdir"]
    mesh_refs = [element.attrib["file"] for element in model_root.findall("asset/mesh")]
    assert len(mesh_refs) == 8
    for reference in mesh_refs:
        assert "://" not in reference and not Path(reference).is_absolute()
        mesh_path = (model_xml.parent / mesh_dir / reference).resolve()
        assert mesh_path.is_file()
        assert _is_under(mesh_path, ASSETS_ROOT.resolve())

    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (model.nq, model.nv, model.nu, model.njnt, model.nsite, model.nkey) == (7, 7, 7, 7, 1, 2)
    assert _names(model, mujoco.mjtObj.mjOBJ_JOINT, model.njnt) == JOINT_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu) == ACTUATOR_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SITE, model.nsite) == ["pinch_site"]
    assert _names(model, mujoco.mjtObj.mjOBJ_KEY, model.nkey) == ["home", "retract"]

    for joint_name in JOINT_NAMES:
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_HINGE)
        assert bool(model.jnt_limited[joint_id]) == (joint_name in JOINT_LIMITS)
        if joint_name in JOINT_LIMITS:
            np.testing.assert_allclose(model.jnt_range[joint_id], JOINT_LIMITS[joint_name])

    for actuator_name in ACTUATOR_NAMES:
        actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, actuator_name)
        assert int(model.actuator_trntype[actuator_id]) == int(mujoco.mjtTrn.mjTRN_JOINT)
        assert int(model.actuator_trnid[actuator_id, 0]) == joint_id
        assert int(model.actuator_gaintype[actuator_id]) == int(mujoco.mjtGain.mjGAIN_FIXED)
        assert int(model.actuator_biastype[actuator_id]) == int(mujoco.mjtBias.mjBIAS_AFFINE)
        kp, kv, forcerange = ACTUATOR_DEFAULTS[actuator_name]
        assert float(model.actuator_gainprm[actuator_id, 0]) == kp
        assert float(model.actuator_biasprm[actuator_id, 1]) == -kp
        assert float(model.actuator_biasprm[actuator_id, 2]) == -kv
        assert bool(model.actuator_forcelimited[actuator_id])
        np.testing.assert_allclose(model.actuator_forcerange[actuator_id], forcerange)
        assert bool(model.actuator_ctrllimited[actuator_id]) == (actuator_name in ACTUATOR_CTRLRANGES)
        if actuator_name in ACTUATOR_CTRLRANGES:
            np.testing.assert_allclose(
                model.actuator_ctrlrange[actuator_id], ACTUATOR_CTRLRANGES[actuator_name]
            )

    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "pinch_site")
    assert site_id >= 0
    site_body_id = int(model.site_bodyid[site_id])
    assert mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, site_body_id) == "bracelet_link"

    for key_name, expected_qpos in KEYFRAMES.items():
        key_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, key_name)
        assert key_id >= 0
        np.testing.assert_allclose(model.key_qpos[key_id], expected_qpos, rtol=0.0, atol=1e-8)
        np.testing.assert_allclose(model.key_ctrl[key_id], expected_qpos, rtol=0.0, atol=1e-8)

    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    np.testing.assert_allclose(data.qpos, model.key_qpos[home_id], rtol=0.0, atol=0.0)
    np.testing.assert_allclose(data.ctrl, model.key_ctrl[home_id], rtol=0.0, atol=0.0)
    mujoco.mj_forward(model, data)
    qpos_before = data.qpos.copy()
    mujoco.mj_step(model, data)
    assert data.time == model.opt.timestep
    assert np.any(np.abs(data.qpos - qpos_before) > 0.0)
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.qacc).all()
    assert np.isfinite(data.ctrl).all()

    runnable_index = json.loads(
        (ROOT / "libraries" / "robots" / "index.json").read_text(encoding="utf-8")
    )
    assert "kinova_gen3" not in runnable_index["robots"]
    assert not (PACKAGE_ROOT / "tasks").exists()
    assert not (PACKAGE_ROOT / "skeleton").exists()
    assert not (PACKAGE_ROOT / "reference").exists()
