from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "leap_hand" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
SCENE_PATH = ASSETS_ROOT / "scene_right.xml"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"

FINGER_ORDER = ["index", "middle", "ring", "thumb"]
JOINT_NAMES = [
    "if_mcp",
    "if_rot",
    "if_pip",
    "if_dip",
    "mf_mcp",
    "mf_rot",
    "mf_pip",
    "mf_dip",
    "rf_mcp",
    "rf_rot",
    "rf_pip",
    "rf_dip",
    "th_cmc",
    "th_axl",
    "th_mcp",
    "th_ipl",
]
ACTUATOR_NAMES = [
    "if_mcp_act",
    "if_rot_act",
    "if_pip_act",
    "if_dip_act",
    "mf_mcp_act",
    "mf_rot_act",
    "mf_pip_act",
    "mf_dip_act",
    "rf_mcp_act",
    "rf_rot_act",
    "rf_pip_act",
    "rf_dip_act",
    "th_cmc_act",
    "th_axl_act",
    "th_mcp_act",
    "th_ipl_act",
]
SENSOR_NAMES = [
    "if_mcp_sensor",
    "if_rot_sensor",
    "if_pip_sensor",
    "if_dip_sensor",
    "mf_mcp_sensor",
    "mf_rot_sensor",
    "mf_pip_sensor",
    "mf_dip_sensor",
    "rf_mcp_sensor",
    "rf_rot_sensor",
    "rf_pip_sensor",
    "rf_dip_sensor",
    "th_cmc_sensor",
    "th_axl_sensor",
    "th_mcp_sensor",
    "th_ipl_sensor",
]
JOINT_LIMITS = {
    "if_mcp": [-0.314, 2.23],
    "if_rot": [-1.047, 1.047],
    "if_pip": [-0.506, 1.885],
    "if_dip": [-0.366, 2.042],
    "mf_mcp": [-0.314, 2.23],
    "mf_rot": [-1.047, 1.047],
    "mf_pip": [-0.506, 1.885],
    "mf_dip": [-0.366, 2.042],
    "rf_mcp": [-0.314, 2.23],
    "rf_rot": [-1.047, 1.047],
    "rf_pip": [-0.506, 1.885],
    "rf_dip": [-0.366, 2.042],
    "th_cmc": [-0.349, 2.094],
    "th_axl": [-0.349, 2.094],
    "th_mcp": [-0.47, 2.443],
    "th_ipl": [-1.34, 1.88],
}
ACTUATOR_CTRLRANGES = dict(zip(ACTUATOR_NAMES, JOINT_LIMITS.values()))
FINGERTIP_GEOMS = {
    "index": "if_tip",
    "middle": "mf_tip",
    "ring": "rf_tip",
    "thumb": "th_tip",
}
FINGERTIP_BODIES = {
    "if_tip": "if_ds",
    "mf_tip": "mf_ds",
    "rf_tip": "rf_ds",
    "th_tip": "th_ds",
}
EXPECTED_CLOSURE_FILES = (
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "SOURCE.md",
    "left_hand.xml",
    "right_hand.png",
    "right_hand.xml",
    "scene_left.xml",
    "scene_right.xml",
    "assets/base.obj",
    "assets/distal.obj",
    "assets/leap_mount.obj",
    "assets/medial.obj",
    "assets/palm_left.obj",
    "assets/palm_right.obj",
    "assets/pip_left.obj",
    "assets/proximal.obj",
    "assets/thumb_base.obj",
    "assets/thumb_distal.obj",
    "assets/thumb_mp_left.obj",
    "assets/thumb_proximal.obj",
    "assets/thumb_tip.obj",
    "assets/tip.obj",
)


def _names(model: mujoco.MjModel, obj: mujoco.mjtObj, count: int) -> list[str | None]:
    return [mujoco.mj_id2name(model, obj, index) for index in range(count)]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _assert_local_reference(base: Path, reference: str) -> None:
    assert "://" not in reference
    reference_path = Path(reference)
    assert not reference_path.is_absolute()
    resolved = (base / reference_path).resolve()
    assert _is_under(resolved, ASSETS_ROOT.resolve())
    assert resolved.is_file(), reference


def _assert_xml_references_are_local(xml_path: Path) -> None:
    root = ET.parse(xml_path).getroot()
    compiler = root.find("compiler")
    mesh_base = xml_path.parent
    if compiler is not None and "meshdir" in compiler.attrib:
        mesh_base = xml_path.parent / compiler.attrib["meshdir"]

    for element in root.iter():
        for attribute, value in element.attrib.items():
            assert "://" not in value
            if attribute != "file":
                continue
            base = mesh_base if element.tag == "mesh" else xml_path.parent
            _assert_local_reference(base, value)


def test_leap_hand_asset_foundation_is_local_and_live() -> None:
    morphology = json.loads(MORPHOLOGY_PATH.read_text(encoding="utf-8"))
    assert morphology["schema_version"] == "1.0"
    assert morphology["robot_model_id"] == "leap_hand"
    assert morphology["robot_configuration_id"] == "leap_hand"
    assert morphology["package_version"] == "1.0.0"
    assert morphology["morphology_kind"] == "fixed_base_hand"
    assert morphology["base_type"] == "fixed"
    assert morphology["handedness"] == "right"
    assert morphology["degrees_of_freedom"] == {"hand": 16}
    assert morphology["model_dimensions"] == {"nq": 16, "nv": 16, "nu": 16}
    assert morphology["mjcf_entrypoint"] == "assets/scene_right.xml"

    control = morphology["public_control"]
    assert control["actuation"] == "joint_position"
    assert control["transmission"] == "joint"
    assert control["actuator_type"] == "position"
    assert control["bias_type"] == "affine"
    assert control["joint_type"] == "hinge"
    assert control["finger_order"] == FINGER_ORDER
    assert control["joint_names"] == JOINT_NAMES
    assert control["actuator_names"] == ACTUATOR_NAMES
    assert control["actuator_joint_map"] == dict(zip(ACTUATOR_NAMES, JOINT_NAMES))
    assert control["joint_limits_rad"] == JOINT_LIMITS
    assert control["actuator_ctrlrange_rad"] == ACTUATOR_CTRLRANGES
    assert control["position_actuator_defaults"] == {
        "hand": {
            "actuator_names": ACTUATOR_NAMES,
            "kp": 3.0,
            "kv": 0.01,
            "forcelimited": False,
        }
    }

    assert morphology["public_observations"] == {
        "base_body": "palm",
        "fingertip_geoms": FINGERTIP_GEOMS,
        "contact_geoms": list(FINGERTIP_GEOMS.values()),
        "sensor_names": SENSOR_NAMES,
    }
    assert morphology["public_affordances"] == {
        "actions": ["joint_position_control"],
        "observations": [
            "joint_positions",
            "joint_velocities",
            "named_body_pose",
            "contact_state",
            "simulation_time",
        ],
    }
    assert morphology["reset_fact"] == {
        "owner": "framework",
        "default": "model_qpos0",
    }
    assert morphology["asset_provenance"] == {
        "model_title": "Leap Hand Description (MJCF)",
        "upstream": "https://github.com/google-deepmind/mujoco_menagerie/tree/da76818e269b82289eba39808e2fb91d679d6994/leap_hand",
        "derived_revision": "da76818e269b82289eba39808e2fb91d679d6994",
        "license": "MIT",
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
    assert morphology["asset_provenance"]["upstream"] in source
    assert "da76818e269b82289eba39808e2fb91d679d6994" in source
    assert "MIT" in source
    assert "Permission is hereby granted" in (ASSETS_ROOT / "LICENSE").read_text(
        encoding="utf-8"
    )

    for xml_path in ASSETS_ROOT.rglob("*.xml"):
        _assert_xml_references_are_local(xml_path)

    assert mujoco.__version__ == "3.3.6"
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (
        model.nq,
        model.nv,
        model.nu,
        model.njnt,
        model.nbody,
        model.ngeom,
        model.nsite,
        model.nsensor,
        model.ntendon,
        model.neq,
        model.nkey,
    ) == (16, 16, 16, 16, 18, 89, 0, 16, 0, 0, 0)
    assert _names(model, mujoco.mjtObj.mjOBJ_JOINT, model.njnt) == JOINT_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu) == ACTUATOR_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SENSOR, model.nsensor) == SENSOR_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SITE, model.nsite) == []
    assert _names(model, mujoco.mjtObj.mjOBJ_KEY, model.nkey) == []

    for joint_name in JOINT_NAMES:
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_HINGE)
        assert bool(model.jnt_limited[joint_id])
        np.testing.assert_allclose(
            model.jnt_range[joint_id], JOINT_LIMITS[joint_name], rtol=0.0, atol=0.0
        )

    for actuator_name, joint_name in zip(ACTUATOR_NAMES, JOINT_NAMES):
        actuator_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name
        )
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.actuator_trntype[actuator_id]) == int(mujoco.mjtTrn.mjTRN_JOINT)
        assert int(model.actuator_trnid[actuator_id, 0]) == joint_id
        assert int(model.actuator_gaintype[actuator_id]) == int(mujoco.mjtGain.mjGAIN_FIXED)
        assert int(model.actuator_biastype[actuator_id]) == int(mujoco.mjtBias.mjBIAS_AFFINE)
        assert float(model.actuator_gainprm[actuator_id, 0]) == 3.0
        assert float(model.actuator_biasprm[actuator_id, 1]) == -3.0
        assert float(model.actuator_biasprm[actuator_id, 2]) == -0.01
        assert bool(model.actuator_ctrllimited[actuator_id])
        np.testing.assert_allclose(
            model.actuator_ctrlrange[actuator_id],
            ACTUATOR_CTRLRANGES[actuator_name],
            rtol=0.0,
            atol=0.0,
        )
        assert not bool(model.actuator_forcelimited[actuator_id])

    for sensor_name, joint_name in zip(SENSOR_NAMES, JOINT_NAMES):
        sensor_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SENSOR, sensor_name)
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.sensor_type[sensor_id]) == int(mujoco.mjtSensor.mjSENS_JOINTPOS)
        assert int(model.sensor_objtype[sensor_id]) == int(mujoco.mjtObj.mjOBJ_JOINT)
        assert int(model.sensor_objid[sensor_id]) == joint_id

    for geom_name, body_name in FINGERTIP_BODIES.items():
        geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, geom_name)
        assert int(model.geom_type[geom_id]) == int(mujoco.mjtGeom.mjGEOM_MESH)
        body_id = int(model.geom_bodyid[geom_id])
        assert mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id) == body_name
        assert int(model.geom_contype[geom_id]) == 1
        assert int(model.geom_conaffinity[geom_id]) == 1

    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    ctrl_low = model.actuator_ctrlrange[:, 0]
    ctrl_high = model.actuator_ctrlrange[:, 1]
    targets = ctrl_low + 0.6 * (ctrl_high - ctrl_low)
    data.ctrl[:] = targets
    qpos_before = data.qpos.copy()
    for _ in range(250):
        mujoco.mj_step(model, data)
    assert np.isclose(data.time, model.opt.timestep * 250)
    np.testing.assert_allclose(data.ctrl, targets, rtol=0.0, atol=0.0)
    assert np.all(np.abs(data.qpos - qpos_before) > 1e-8)
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.qacc).all()
    assert np.isfinite(data.ctrl).all()

    runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
    assert "leap_hand" not in runnable_index["robots"]

    research_index = json.loads(RESEARCH_INDEX_PATH.read_text(encoding="utf-8"))
    candidate = next(
        item
        for item in research_index["candidates"]
        if item["robot_configuration_id"] == "leap_hand"
    )
    observed_paths = {
        item["path"] for item in candidate["locally_observed_source_material"]
    }
    assert {
        "autoadapter/libraries/robots/leap_hand/1.0.0/assets/scene_right.xml",
        "autoadapter/libraries/robots/leap_hand/1.0.0/morphology.json",
    }.issubset(observed_paths)
    missing = " ".join(candidate["missing_for_runnable_package"])
    assert "Materialize and verify a complete local MuJoCo asset closure" not in missing
    assert "Create the current mainline morphology.json" not in missing
    assert "20 distinct applicable source-backed tasks" not in missing
    assert "tasks/private/instances.json" in missing
    assert "hand-control skeleton" not in missing
    assert "positive control" in missing
    assert "dynamic canary" in missing
