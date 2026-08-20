from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "kuka_iiwa_14" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
SCENE_PATH = ASSETS_ROOT / "scene.xml"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"

JOINT_NAMES = [f"joint{index}" for index in range(1, 8)]
ACTUATOR_NAMES = [f"actuator{index}" for index in range(1, 8)]
JOINT_LIMITS = {
    "joint1": [-2.96706, 2.96706],
    "joint2": [-2.0944, 2.0944],
    "joint3": [-2.96706, 2.96706],
    "joint4": [-2.0944, 2.0944],
    "joint5": [-2.96706, 2.96706],
    "joint6": [-2.0944, 2.0944],
    "joint7": [-3.05433, 3.05433],
}
ACTUATOR_CTRLRANGES = dict(zip(ACTUATOR_NAMES, JOINT_LIMITS.values()))
HOME = [0.0, 0.785398, 0.0, -1.5708, 0.0, 0.0, 0.0]
EXPECTED_CLOSURE_FILES = (
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "SOURCE.md",
    "iiwa14.xml",
    "iiwa_14.png",
    "scene.xml",
    "button_front_scene.xml",
    "button_topdown_scene.xml",
    "dial_scene.xml",
    "door_lock_scene.xml",
    "door_scene.xml",
    "door_unlock_scene.xml",
    "drawer_scene.xml",
    "faucet_scene.xml",
    "handle_vertical_scene.xml",
    "lever_scene.xml",
    "push_to_goal_scene.xml",
    "reach_scene.xml",
    "soccer_scene.xml",
    "sweep_into_goal_scene.xml",
    "wall_scene.xml",
    "window_scene.xml",
    "assets/band.obj",
    "assets/kuka.obj",
    "assets/link_0.obj",
    "assets/link_1.obj",
    "assets/link_2_grey.obj",
    "assets/link_2_orange.obj",
    "assets/link_3.obj",
    "assets/link_4_grey.obj",
    "assets/link_4_orange.obj",
    "assets/link_5.obj",
    "assets/link_6_grey.obj",
    "assets/link_6_orange.obj",
    "assets/link_7.obj",
)


def _names(model: mujoco.MjModel, obj: mujoco.mjtObj, count: int) -> list[str | None]:
    return [mujoco.mj_id2name(model, obj, index) for index in range(count)]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _assert_local_file(source: Path, reference: str) -> None:
    assert "://" not in reference
    assert not Path(reference).is_absolute()
    resolved = (source.parent / reference).resolve()
    assert _is_under(resolved, ASSETS_ROOT.resolve())
    assert resolved.is_file(), reference


def test_kuka_iiwa_14_asset_foundation_is_local_and_live() -> None:
    morphology = json.loads(MORPHOLOGY_PATH.read_text(encoding="utf-8"))
    assert morphology["robot_model_id"] == "kuka_iiwa_14"
    assert morphology["robot_configuration_id"] == "kuka_iiwa_14"
    assert morphology["package_version"] == "1.0.0"
    assert morphology["morphology_kind"] == "fixed_base_serial_manipulator"
    assert morphology["base_type"] == "fixed"
    assert morphology["degrees_of_freedom"] == {"arm": 7}
    assert morphology["mjcf_entrypoint"] == "assets/scene.xml"

    control = morphology["public_control"]
    assert control["actuation"] == "joint_position"
    assert control["transmission"] == "direct_joint"
    assert control["actuator_type"] == "general"
    assert control["joint_type"] == "hinge"
    assert control["joint_names"] == JOINT_NAMES
    assert control["actuator_names"] == ACTUATOR_NAMES
    assert control["actuator_joint_map"] == dict(zip(ACTUATOR_NAMES, JOINT_NAMES))
    assert control["arm_joint_limits_rad"] == JOINT_LIMITS
    assert control["actuator_ctrlrange_rad"] == ACTUATOR_CTRLRANGES
    assert control["position_actuator_defaults"] == {
        "arm": {
            "actuator_names": ACTUATOR_NAMES,
            "kp": 2000.0,
            "kv": 200.0,
            "forcelimited": False,
        }
    }
    assert "gripper" not in morphology["degrees_of_freedom"]
    assert "gripper" not in control
    assert morphology["public_observations"] == {
        "end_effector_site": "attachment_site",
        "end_effector_body": "link7",
        "contact_geoms": {
            "link7_contact_geom": {
                "body_name": "link7",
                "type": "sphere",
                "radius_m": 0.06,
            }
        },
    }
    assert morphology["public_affordances"] == {
        "actions": ["joint_position_control"],
        "observations": [
            "joint_positions",
            "joint_velocities",
            "end_effector_pose",
            "named_body_pose",
            "named_site_pose",
            "contact_state",
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
        "model_title": "KUKA LBR iiwa 14 Description (MJCF)",
        "upstream": "https://github.com/981526092/auto-adapter",
        "derived_revision": "585eb1f1fde33f17f5f9a1e169a18dd41f97b586",
        "source_path": "assets/mjcf/kuka_iiwa_14/",
        "license": "BSD-3-Clause",
        "license_file": "assets/LICENSE",
        "source_file": "assets/SOURCE.md",
    }

    assert sorted(
        path.relative_to(ASSETS_ROOT).as_posix()
        for path in ASSETS_ROOT.rglob("*")
        if path.is_file()
    ) == sorted(EXPECTED_CLOSURE_FILES)
    assert not any(path.is_symlink() for path in ASSETS_ROOT.rglob("*"))
    for relative_path in EXPECTED_CLOSURE_FILES:
        canonical_path = ASSETS_ROOT / relative_path
        assert canonical_path.is_file(), relative_path

    source = (ASSETS_ROOT / "SOURCE.md").read_text(encoding="utf-8")
    assert "https://github.com/981526092/auto-adapter" in source
    assert "585eb1f1fde33f17f5f9a1e169a18dd41f97b586" in source
    assert "BSD 3-Clause License" in (ASSETS_ROOT / "LICENSE").read_text(encoding="utf-8")

    scene_root = ET.parse(SCENE_PATH).getroot()
    include_refs = [element.attrib["file"] for element in scene_root.iter("include")]
    assert include_refs == ["iiwa14.xml"]
    for reference in include_refs:
        _assert_local_file(SCENE_PATH, reference)

    model_xml = ASSETS_ROOT / "iiwa14.xml"
    model_root = ET.parse(model_xml).getroot()
    compiler = model_root.find("compiler")
    assert compiler is not None
    assert compiler.attrib["meshdir"] == "assets"
    general_actuators = model_root.findall("actuator/general")
    assert [element.attrib["name"] for element in general_actuators] == ACTUATOR_NAMES
    assert not model_root.findall("actuator/motor")
    assert not model_root.findall("actuator/position")
    for mesh in model_root.findall("asset/mesh"):
        _assert_local_file(model_xml, str(Path(compiler.attrib["meshdir"]) / mesh.attrib["file"]))

    assert mujoco.__version__ == "3.3.6"
    standalone_model = mujoco.MjModel.from_xml_path(str(model_xml))
    assert (standalone_model.nq, standalone_model.nv, standalone_model.nu) == (7, 7, 7)
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (model.nq, model.nv, model.nu, model.njnt, model.nsite, model.ntendon, model.nsensor, model.neq, model.nkey) == (
        7,
        7,
        7,
        7,
        1,
        0,
        0,
        0,
        1,
    )
    assert _names(model, mujoco.mjtObj.mjOBJ_JOINT, model.njnt) == JOINT_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu) == ACTUATOR_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SITE, model.nsite) == ["attachment_site"]
    assert _names(model, mujoco.mjtObj.mjOBJ_TENDON, model.ntendon) == []
    assert _names(model, mujoco.mjtObj.mjOBJ_SENSOR, model.nsensor) == []
    assert _names(model, mujoco.mjtObj.mjOBJ_KEY, model.nkey) == ["home"]

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
        assert float(model.actuator_gainprm[actuator_id, 0]) == 2000.0
        assert float(model.actuator_biasprm[actuator_id, 1]) == -2000.0
        assert float(model.actuator_biasprm[actuator_id, 2]) == -200.0
        assert bool(model.actuator_ctrllimited[actuator_id])
        np.testing.assert_allclose(
            model.actuator_ctrlrange[actuator_id], ACTUATOR_CTRLRANGES[actuator_name], rtol=0.0, atol=0.0
        )
        assert not bool(model.actuator_forcelimited[actuator_id])

    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "attachment_site")
    assert mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(model.site_bodyid[site_id])) == "link7"
    np.testing.assert_allclose(model.site_pos[site_id], [0.0, 0.0, 0.045], rtol=0.0, atol=0.0)

    link7_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "link7")
    link7_spheres = [
        geom_id
        for geom_id in range(model.ngeom)
        if int(model.geom_bodyid[geom_id]) == link7_id
        and int(model.geom_type[geom_id]) == int(mujoco.mjtGeom.mjGEOM_SPHERE)
    ]
    assert len(link7_spheres) == 1
    sphere_id = link7_spheres[0]
    assert (
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, sphere_id)
        == "link7_contact_geom"
    )
    assert float(model.geom_size[sphere_id, 0]) == 0.06
    assert int(model.geom_contype[sphere_id]) != 0
    assert int(model.geom_conaffinity[sphere_id]) != 0

    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    np.testing.assert_allclose(model.key_qpos[home_id], HOME, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(model.key_ctrl[home_id], HOME, rtol=0.0, atol=0.0)

    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    mujoco.mj_forward(model, data)
    data.ctrl[0] = 0.1
    qpos_before = data.qpos.copy()
    for _ in range(250):
        mujoco.mj_step(model, data)
    assert data.time == pytest.approx(250 * model.opt.timestep)
    assert data.ctrl[0] == 0.1
    assert float(data.qpos[0]) == pytest.approx(0.09969997, abs=1e-7)
    assert abs(float(data.qpos[0] - qpos_before[0])) > 0.09
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.qacc).all()
    assert np.isfinite(data.ctrl).all()

    runnable_index = json.loads((ROOT / "libraries" / "robots" / "index.json").read_text(encoding="utf-8"))
    assert "kuka_iiwa_14" not in runnable_index["robots"]
    research_index = json.loads(RESEARCH_INDEX_PATH.read_text(encoding="utf-8"))
    candidate = next(
        item
        for item in research_index["candidates"]
        if item["robot_configuration_id"] == "kuka_iiwa_14"
    )
    observed_paths = {
        item["path"] for item in candidate["locally_observed_source_material"]
    }
    assert "autoadapter/libraries/robots/kuka_iiwa_14/1.0.0/assets/scene.xml" in observed_paths
    assert "autoadapter/libraries/robots/kuka_iiwa_14/1.0.0/morphology.json" in observed_paths
    assert "autoadapter/libraries/robots/kuka_iiwa_14/1.0.0/tasks/sources.json" in observed_paths
    assert "autoadapter/libraries/robots/kuka_iiwa_14/1.0.0/tasks/catalog.json" in observed_paths
    assert (
        "autoadapter/libraries/robots/kuka_iiwa_14/1.0.0/"
        "skeleton/arm_serial_dls.py"
    ) in observed_paths
    missing = " ".join(candidate["missing_for_runnable_package"])
    assert "complete local MuJoCo asset closure" not in missing
    assert "current mainline morphology.json" not in missing
    assert "20 distinct applicable source-backed tasks" not in missing
    assert "tasks/sources.json" not in missing
    assert sorted(path.name for path in (PACKAGE_ROOT / "tasks").iterdir()) == [
        "catalog.json",
        "private",
        "sources.json",
    ]
    assert sorted(
        path.name for path in (PACKAGE_ROOT / "tasks" / "private").iterdir()
    ) == ["bindings.json", "guards.json", "instances.json"]
    assert (PACKAGE_ROOT / "skeleton" / "arm_serial_dls.py").is_file()
    assert (PACKAGE_ROOT / "reference" / "driver.py").is_file()
