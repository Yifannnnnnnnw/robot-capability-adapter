from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "unitree_g1" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
SCENE_PATH = ASSETS_ROOT / "scene.xml"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"

COMMIT = "da76818e269b82289eba39808e2fb91d679d6994"
JOINT_NAMES = [
    "floating_base_joint",
    "left_hip_pitch_joint",
    "left_hip_roll_joint",
    "left_hip_yaw_joint",
    "left_knee_joint",
    "left_ankle_pitch_joint",
    "left_ankle_roll_joint",
    "right_hip_pitch_joint",
    "right_hip_roll_joint",
    "right_hip_yaw_joint",
    "right_knee_joint",
    "right_ankle_pitch_joint",
    "right_ankle_roll_joint",
    "waist_yaw_joint",
    "waist_roll_joint",
    "waist_pitch_joint",
    "left_shoulder_pitch_joint",
    "left_shoulder_roll_joint",
    "left_shoulder_yaw_joint",
    "left_elbow_joint",
    "left_wrist_roll_joint",
    "left_wrist_pitch_joint",
    "left_wrist_yaw_joint",
    "right_shoulder_pitch_joint",
    "right_shoulder_roll_joint",
    "right_shoulder_yaw_joint",
    "right_elbow_joint",
    "right_wrist_roll_joint",
    "right_wrist_pitch_joint",
    "right_wrist_yaw_joint",
]
ACTUATOR_NAMES = JOINT_NAMES[1:]
SITE_NAMES = ["imu_in_pelvis", "left_foot", "right_foot", "imu_in_torso"]
SENSOR_NAMES = [
    "imu-torso-angular-velocity",
    "imu-torso-linear-acceleration",
    "imu-pelvis-angular-velocity",
    "imu-pelvis-linear-acceleration",
]
MESH_FILES = [
    "pelvis.STL",
    "pelvis_contour_link.STL",
    "left_hip_pitch_link.STL",
    "left_hip_roll_link.STL",
    "left_hip_yaw_link.STL",
    "left_knee_link.STL",
    "left_ankle_pitch_link.STL",
    "left_ankle_roll_link.STL",
    "right_hip_pitch_link.STL",
    "right_hip_roll_link.STL",
    "right_hip_yaw_link.STL",
    "right_knee_link.STL",
    "right_ankle_pitch_link.STL",
    "right_ankle_roll_link.STL",
    "waist_yaw_link_rev_1_0.STL",
    "waist_roll_link_rev_1_0.STL",
    "torso_link_rev_1_0.STL",
    "logo_link.STL",
    "head_link.STL",
    "left_shoulder_pitch_link.STL",
    "left_shoulder_roll_link.STL",
    "left_shoulder_yaw_link.STL",
    "left_elbow_link.STL",
    "left_wrist_roll_link.STL",
    "left_wrist_pitch_link.STL",
    "left_wrist_yaw_link.STL",
    "left_rubber_hand.STL",
    "right_shoulder_pitch_link.STL",
    "right_shoulder_roll_link.STL",
    "right_shoulder_yaw_link.STL",
    "right_elbow_link.STL",
    "right_wrist_roll_link.STL",
    "right_wrist_pitch_link.STL",
    "right_wrist_yaw_link.STL",
    "right_rubber_hand.STL",
]
EXPECTED_CLOSURE_FILES = [
    "LICENSE",
    "SOURCE.md",
    "g1.xml",
    "g1_mjlab.xml",
    "g1_mjlab_policy_scene.xml",
    "scene.xml",
    *(f"assets/{filename}" for filename in MESH_FILES),
]
JOINT_LIMITS = {
    "left_hip_pitch_joint": [-2.5307, 2.8798],
    "left_hip_roll_joint": [-0.5236, 2.9671],
    "left_hip_yaw_joint": [-2.7576, 2.7576],
    "left_knee_joint": [-0.087267, 2.8798],
    "left_ankle_pitch_joint": [-0.87267, 0.5236],
    "left_ankle_roll_joint": [-0.2618, 0.2618],
    "right_hip_pitch_joint": [-2.5307, 2.8798],
    "right_hip_roll_joint": [-2.9671, 0.5236],
    "right_hip_yaw_joint": [-2.7576, 2.7576],
    "right_knee_joint": [-0.087267, 2.8798],
    "right_ankle_pitch_joint": [-0.87267, 0.5236],
    "right_ankle_roll_joint": [-0.2618, 0.2618],
    "waist_yaw_joint": [-2.618, 2.618],
    "waist_roll_joint": [-0.52, 0.52],
    "waist_pitch_joint": [-0.52, 0.52],
    "left_shoulder_pitch_joint": [-3.0892, 2.6704],
    "left_shoulder_roll_joint": [-1.5882, 2.2515],
    "left_shoulder_yaw_joint": [-2.618, 2.618],
    "left_elbow_joint": [-1.0472, 2.0944],
    "left_wrist_roll_joint": [-1.97222, 1.97222],
    "left_wrist_pitch_joint": [-1.61443, 1.61443],
    "left_wrist_yaw_joint": [-1.61443, 1.61443],
    "right_shoulder_pitch_joint": [-3.0892, 2.6704],
    "right_shoulder_roll_joint": [-2.2515, 1.5882],
    "right_shoulder_yaw_joint": [-2.618, 2.618],
    "right_elbow_joint": [-1.0472, 2.0944],
    "right_wrist_roll_joint": [-1.97222, 1.97222],
    "right_wrist_pitch_joint": [-1.61443, 1.61443],
    "right_wrist_yaw_joint": [-1.61443, 1.61443],
}
ACTUATOR_CTRLRANGES = {
    "left_hip_pitch_joint": [-2.5307, 2.8798],
    "left_hip_roll_joint": [-0.5236000000000001, 2.9671],
    "left_hip_yaw_joint": [-2.7576, 2.7576],
    "left_knee_joint": [-0.0872670000000002, 2.8798],
    "left_ankle_pitch_joint": [-0.87267, 0.5236],
    "left_ankle_roll_joint": [-0.2618, 0.2618],
    "right_hip_pitch_joint": [-2.5307, 2.8798],
    "right_hip_roll_joint": [-2.9671, 0.5236000000000001],
    "right_hip_yaw_joint": [-2.7576, 2.7576],
    "right_knee_joint": [-0.0872670000000002, 2.8798],
    "right_ankle_pitch_joint": [-0.87267, 0.5236],
    "right_ankle_roll_joint": [-0.2618, 0.2618],
    "waist_yaw_joint": [-2.618, 2.618],
    "waist_roll_joint": [-0.52, 0.52],
    "waist_pitch_joint": [-0.52, 0.52],
    "left_shoulder_pitch_joint": [-3.0892, 2.6704],
    "left_shoulder_roll_joint": [-1.5882, 2.2515],
    "left_shoulder_yaw_joint": [-2.618, 2.618],
    "left_elbow_joint": [-1.0471999999999997, 2.0944],
    "left_wrist_roll_joint": [-1.97222, 1.97222],
    "left_wrist_pitch_joint": [-1.61443, 1.61443],
    "left_wrist_yaw_joint": [-1.61443, 1.61443],
    "right_shoulder_pitch_joint": [-3.0892, 2.6704],
    "right_shoulder_roll_joint": [-2.2515, 1.5882],
    "right_shoulder_yaw_joint": [-2.618, 2.618],
    "right_elbow_joint": [-1.0471999999999997, 2.0944],
    "right_wrist_roll_joint": [-1.97222, 1.97222],
    "right_wrist_pitch_joint": [-1.61443, 1.61443],
    "right_wrist_yaw_joint": [-1.61443, 1.61443],
}
ACTUATOR_FORCE_RANGES = {
    "left_hip_pitch_joint": [-88.0, 88.0],
    "left_hip_roll_joint": [-139.0, 139.0],
    "left_hip_yaw_joint": [-88.0, 88.0],
    "left_knee_joint": [-139.0, 139.0],
    "left_ankle_pitch_joint": [-50.0, 50.0],
    "left_ankle_roll_joint": [-50.0, 50.0],
    "right_hip_pitch_joint": [-88.0, 88.0],
    "right_hip_roll_joint": [-139.0, 139.0],
    "right_hip_yaw_joint": [-88.0, 88.0],
    "right_knee_joint": [-139.0, 139.0],
    "right_ankle_pitch_joint": [-50.0, 50.0],
    "right_ankle_roll_joint": [-50.0, 50.0],
    "waist_yaw_joint": [-88.0, 88.0],
    "waist_roll_joint": [-50.0, 50.0],
    "waist_pitch_joint": [-50.0, 50.0],
    "left_shoulder_pitch_joint": [-25.0, 25.0],
    "left_shoulder_roll_joint": [-25.0, 25.0],
    "left_shoulder_yaw_joint": [-25.0, 25.0],
    "left_elbow_joint": [-25.0, 25.0],
    "left_wrist_roll_joint": [-25.0, 25.0],
    "left_wrist_pitch_joint": [-5.0, 5.0],
    "left_wrist_yaw_joint": [-5.0, 5.0],
    "right_shoulder_pitch_joint": [-25.0, 25.0],
    "right_shoulder_roll_joint": [-25.0, 25.0],
    "right_shoulder_yaw_joint": [-25.0, 25.0],
    "right_elbow_joint": [-25.0, 25.0],
    "right_wrist_roll_joint": [-25.0, 25.0],
    "right_wrist_pitch_joint": [-5.0, 5.0],
    "right_wrist_yaw_joint": [-5.0, 5.0],
}
COMPILED_KV = {
    "left_hip_pitch_joint": 43.010682759901975,
    "left_hip_roll_joint": 39.585205837505725,
    "left_hip_yaw_joint": 9.831503737481297,
    "left_knee_joint": 15.847010682643832,
    "left_ankle_pitch_joint": 5.055168252967315,
    "left_ankle_roll_joint": 4.557878078967018,
    "right_hip_pitch_joint": 43.01068317743058,
    "right_hip_roll_joint": 39.58598368471193,
    "right_hip_yaw_joint": 9.828373554924807,
    "right_knee_joint": 15.847011815866383,
    "right_ankle_pitch_joint": 5.055168252967315,
    "right_ankle_roll_joint": 4.557878078967009,
    "waist_yaw_joint": 23.286487568511347,
    "waist_roll_joint": 37.34008240801737,
    "waist_pitch_joint": 35.21624688342399,
    "left_shoulder_pitch_joint": 16.725336916240696,
    "left_shoulder_roll_joint": 13.493619374074822,
    "left_shoulder_yaw_joint": 10.118862578761355,
    "left_elbow_joint": 9.429120019173435,
    "left_wrist_roll_joint": 4.553320566666125,
    "left_wrist_pitch_joint": 5.4414009181647005,
    "left_wrist_yaw_joint": 4.8656141331296165,
    "right_shoulder_pitch_joint": 16.725336916240693,
    "right_shoulder_roll_joint": 13.493619374074823,
    "right_shoulder_yaw_joint": 10.118862578761355,
    "right_elbow_joint": 9.429120019173435,
    "right_wrist_roll_joint": 4.553320566666126,
    "right_wrist_pitch_joint": 5.441400918164699,
    "right_wrist_yaw_joint": 4.865614133129616,
}
KEYFRAME_QPOS = [
    0.0,
    0.0,
    0.79,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.2,
    0.2,
    0.0,
    1.28,
    0.0,
    0.0,
    0.0,
    0.2,
    -0.2,
    0.0,
    1.28,
    0.0,
    0.0,
    0.0,
]
KEYFRAME_CTRL = [
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.2,
    0.2,
    0.0,
    1.28,
    0.0,
    0.0,
    0.0,
    0.2,
    -0.2,
    0.0,
    1.28,
    0.0,
    0.0,
    0.0,
]


def _names(model: mujoco.MjModel, obj: mujoco.mjtObj, count: int) -> list[str | None]:
    return [mujoco.mj_id2name(model, obj, index) for index in range(count)]


def _assert_local_file(base: Path, reference: str) -> None:
    reference_path = Path(reference)
    assert "//" not in reference
    assert not reference_path.is_absolute()
    resolved = (base / reference_path).resolve()
    assert resolved.is_relative_to(ASSETS_ROOT.resolve())
    assert resolved.is_file(), reference


def _assert_xml_references_are_local(xml_path: Path) -> None:
    root = ET.parse(xml_path).getroot()
    compiler = root.find("compiler")
    mesh_base = xml_path.parent
    if compiler is not None and "meshdir" in compiler.attrib:
        mesh_base = xml_path.parent / compiler.attrib["meshdir"]
    for element in root.iter():
        for attribute, value in element.attrib.items():
            assert "//" not in value
            if attribute != "file":
                continue
            base = mesh_base if element.tag == "mesh" else xml_path.parent
            _assert_local_file(base, value)


def test_unitree_g1_asset_foundation_is_local_and_live() -> None:
    morphology = json.loads(MORPHOLOGY_PATH.read_text(encoding="utf-8"))
    assert morphology["schema_version"] == "1.0"
    assert morphology["robot_model_id"] == "unitree_g1"
    assert morphology["robot_configuration_id"] == "unitree_g1"
    assert morphology["package_version"] == "1.0.0"
    assert morphology["research_only"] is True
    assert morphology["morphology_kind"] == "free_base_humanoid"
    assert morphology["model_variant"] == "g1_29dof_rev_1_0"
    assert morphology["base_type"] == "free"
    assert morphology["degrees_of_freedom"] == {
        "floating_base": 6,
        "floating_base_qpos": 7,
        "floating_base_qvel": 6,
        "actuated_joints": 29,
    }
    assert morphology["model_dimensions"] == {
        "nq": 36,
        "nv": 35,
        "nu": 29,
        "njnt": 30,
        "nbody": 31,
        "ngeom": 72,
        "nsite": 4,
        "nsensor": 4,
        "nsensordata": 12,
        "nkey": 1,
        "nmesh": 35,
        "nmat": 3,
        "ntex": 2,
    }
    assert morphology["mjcf_entrypoint"] == "assets/scene.xml"
    assert morphology["simulation"] == {
        "mujoco_version": "3.3.6",
        "timestep_s": 0.002,
        "integrator": "implicitfast",
        "gravity_m_s2": [0.0, 0.0, -9.81],
    }

    public_model = morphology["public_model"]
    assert public_model["joint_names"] == JOINT_NAMES
    assert public_model["free_base_joint"] == {
        "name": "floating_base_joint",
        "joint_type": "free",
        "qpos_dimensions": 7,
        "qvel_dimensions": 6,
    }

    control = morphology["public_control"]
    assert control["actuation"] == "joint_position"
    assert control["transmission"] == "joint"
    assert control["actuator_type"] == "position"
    assert control["gain_type"] == "fixed"
    assert control["bias_type"] == "affine"
    assert control["dynamics_type"] == "none"
    assert control["joint_type"] == "hinge"
    assert control["control_units"] == "rad"
    assert control["joint_names"] == ACTUATOR_NAMES
    assert control["actuator_names"] == ACTUATOR_NAMES
    assert control["actuator_joint_map"] == dict(zip(ACTUATOR_NAMES, ACTUATOR_NAMES))
    assert control["joint_limits_rad"] == JOINT_LIMITS
    assert control["actuator_ctrlrange_rad"] == ACTUATOR_CTRLRANGES
    assert control["joint_actuator_force_limit"]["enforcement"] == "joint_actuatorfrcrange"
    assert control["joint_actuator_force_limit"]["actuator_forcelimited"] is False
    assert control["joint_actuator_force_limit"]["ranges_Nm"] == ACTUATOR_FORCE_RANGES
    assert control["position_actuator_defaults"]["all"]["actuator_names"] == ACTUATOR_NAMES
    assert control["position_actuator_defaults"]["all"]["kp"] == 500.0
    assert control["position_actuator_defaults"]["all"]["dampratio"] == 1.0
    assert control["position_actuator_defaults"]["all"]["inheritrange"] == 1.0
    assert (
        control["position_actuator_defaults"]["all"]["compiled_damping_coefficient_by_actuator"]
        == COMPILED_KV
    )

    assert morphology["public_observations"] == {
        "base_body": "pelvis",
        "torso_body": "torso_link",
        "site_names": SITE_NAMES,
        "site_body_map": {
            "imu_in_pelvis": "pelvis",
            "left_foot": "left_ankle_roll_link",
            "right_foot": "right_ankle_roll_link",
            "imu_in_torso": "torso_link",
        },
        "foot_sites": ["left_foot", "right_foot"],
        "imu_sites": ["imu_in_pelvis", "imu_in_torso"],
        "sensor_names": SENSOR_NAMES,
        "sensor_specs": [
            {
                "name": "imu-torso-angular-velocity",
                "type": "gyro",
                "site": "imu_in_torso",
                "cutoff_hz": 34.9,
                "noise": 0.0005,
            },
            {
                "name": "imu-torso-linear-acceleration",
                "type": "accelerometer",
                "site": "imu_in_torso",
                "cutoff_hz": 157.0,
                "noise": 0.01,
            },
            {
                "name": "imu-pelvis-angular-velocity",
                "type": "gyro",
                "site": "imu_in_pelvis",
                "cutoff_hz": 34.9,
                "noise": 0.0005,
            },
            {
                "name": "imu-pelvis-linear-acceleration",
                "type": "accelerometer",
                "site": "imu_in_pelvis",
                "cutoff_hz": 157.0,
                "noise": 0.01,
            },
        ],
    }
    assert morphology["public_affordances"] == {
        "actions": ["joint_position_control"],
        "observations": [
            "joint_positions",
            "joint_velocities",
            "free_base_pose",
            "free_base_velocity",
            "named_site_pose",
            "imu_observations",
            "contact_state",
            "simulation_time",
        ],
    }
    assert morphology["reset_fact"]["owner"] == "framework"
    assert morphology["reset_fact"]["default"] == "model_qpos0"
    assert morphology["reset_fact"]["available_keyframe"] == "stand"
    np.testing.assert_allclose(
        morphology["reset_fact"]["keyframe_qpos"], KEYFRAME_QPOS, rtol=0.0, atol=0.0
    )
    np.testing.assert_allclose(
        morphology["reset_fact"]["keyframe_ctrl"], KEYFRAME_CTRL, rtol=0.0, atol=0.0
    )

    assert morphology["asset_provenance"] == {
        "model_title": "Unitree G1 Description (MJCF)",
        "repository": "https://github.com/google-deepmind/mujoco_menagerie",
        "upstream": f"https://github.com/google-deepmind/mujoco_menagerie/tree/{COMMIT}/unitree_g1",
        "derived_revision": COMMIT,
        "source_path": "unitree_g1/scene.xml",
        "entrypoint": "scene.xml",
        "model_file": "g1.xml",
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
    assert len(canonical_files) == 41
    assert not any(path.is_symlink() for path in ASSETS_ROOT.rglob("*"))
    for relative_path in EXPECTED_CLOSURE_FILES:
        assert (ASSETS_ROOT / relative_path).is_file(), relative_path

    source = (ASSETS_ROOT / "SOURCE.md").read_text(encoding="utf-8")
    assert f"https://github.com/google-deepmind/mujoco_menagerie/tree/{COMMIT}/unitree_g1" in source
    assert COMMIT in source
    assert "BSD-3-Clause" in source
    assert "Redistribution and use in source and binary forms" in (
        ASSETS_ROOT / "LICENSE"
    ).read_text(encoding="utf-8")
    assert not (ASSETS_ROOT / "g1_with_hands.xml").exists()
    assert not (ASSETS_ROOT / "scene_with_hands.xml").exists()

    for xml_path in sorted(ASSETS_ROOT.glob("*.xml")):
        _assert_xml_references_are_local(xml_path)
    scene_root = ET.parse(SCENE_PATH).getroot()
    assert [element.attrib["file"] for element in scene_root.iter("include")] == ["g1.xml"]
    model_xml = ASSETS_ROOT / "g1.xml"
    model_root = ET.parse(model_xml).getroot()
    assert model_root.attrib["model"] == "g1_29dof_rev_1_0"
    compiler = model_root.find("compiler")
    assert compiler is not None
    assert compiler.attrib["meshdir"] == "assets"
    assert [element.attrib["file"] for element in model_root.findall("asset/mesh")] == MESH_FILES

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
        model.nsensordata,
        model.nkey,
        model.nmesh,
        model.nmat,
        model.ntex,
    ) == (36, 35, 29, 30, 31, 72, 4, 4, 12, 1, 35, 3, 2)
    assert _names(model, mujoco.mjtObj.mjOBJ_JOINT, model.njnt) == JOINT_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu) == ACTUATOR_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SITE, model.nsite) == SITE_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SENSOR, model.nsensor) == SENSOR_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_KEY, model.nkey) == ["stand"]
    assert model.opt.timestep == 0.002
    assert int(model.opt.integrator) == int(mujoco.mjtIntegrator.mjINT_IMPLICITFAST)

    base_joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "floating_base_joint")
    assert int(model.jnt_type[base_joint_id]) == int(mujoco.mjtJoint.mjJNT_FREE)
    assert not bool(model.jnt_limited[base_joint_id])
    assert int(model.jnt_qposadr[base_joint_id]) == 0
    assert int(model.jnt_dofadr[base_joint_id]) == 0
    first_actuated_joint_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_JOINT, ACTUATOR_NAMES[0]
    )
    assert int(model.jnt_qposadr[first_actuated_joint_id]) == 7
    assert int(model.jnt_dofadr[first_actuated_joint_id]) == 6

    for joint_name in ACTUATOR_NAMES:
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_HINGE)
        assert bool(model.jnt_limited[joint_id])
        np.testing.assert_allclose(model.jnt_range[joint_id], JOINT_LIMITS[joint_name], rtol=0.0, atol=0.0)
        assert bool(model.jnt_actfrclimited[joint_id])
        np.testing.assert_allclose(
            model.jnt_actfrcrange[joint_id], ACTUATOR_FORCE_RANGES[joint_name], rtol=0.0, atol=0.0
        )

    defaults = control["position_actuator_defaults"]["all"]
    for actuator_name, joint_name in zip(ACTUATOR_NAMES, ACTUATOR_NAMES):
        actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.actuator_trntype[actuator_id]) == int(mujoco.mjtTrn.mjTRN_JOINT)
        assert int(model.actuator_trnid[actuator_id, 0]) == joint_id
        assert int(model.actuator_gaintype[actuator_id]) == int(mujoco.mjtGain.mjGAIN_FIXED)
        assert int(model.actuator_biastype[actuator_id]) == int(mujoco.mjtBias.mjBIAS_AFFINE)
        assert int(model.actuator_dyntype[actuator_id]) == int(mujoco.mjtDyn.mjDYN_NONE)
        assert float(model.actuator_gainprm[actuator_id, 0]) == defaults["kp"]
        assert float(model.actuator_biasprm[actuator_id, 1]) == -defaults["kp"]
        assert float(model.actuator_biasprm[actuator_id, 2]) == -COMPILED_KV[actuator_name]
        assert bool(model.actuator_ctrllimited[actuator_id])
        np.testing.assert_allclose(
            model.actuator_ctrlrange[actuator_id], ACTUATOR_CTRLRANGES[joint_name], rtol=0.0, atol=0.0
        )
        assert not bool(model.actuator_forcelimited[actuator_id])
        assert float(model.actuator_gear[actuator_id, 0]) == 1.0

    for site_name, body_name in {
        "imu_in_pelvis": "pelvis",
        "left_foot": "left_ankle_roll_link",
        "right_foot": "right_ankle_roll_link",
        "imu_in_torso": "torso_link",
    }.items():
        site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, site_name)
        assert mujoco.mj_id2name(
            model, mujoco.mjtObj.mjOBJ_BODY, int(model.site_bodyid[site_id])
        ) == body_name

    sensor_specs = [
        ("imu-torso-angular-velocity", mujoco.mjtSensor.mjSENS_GYRO, "imu_in_torso", 34.9, 0.0005),
        (
            "imu-torso-linear-acceleration",
            mujoco.mjtSensor.mjSENS_ACCELEROMETER,
            "imu_in_torso",
            157.0,
            0.01,
        ),
        ("imu-pelvis-angular-velocity", mujoco.mjtSensor.mjSENS_GYRO, "imu_in_pelvis", 34.9, 0.0005),
        (
            "imu-pelvis-linear-acceleration",
            mujoco.mjtSensor.mjSENS_ACCELEROMETER,
            "imu_in_pelvis",
            157.0,
            0.01,
        ),
    ]
    for sensor_name, sensor_type, site_name, cutoff, noise in sensor_specs:
        sensor_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SENSOR, sensor_name)
        site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, site_name)
        assert int(model.sensor_type[sensor_id]) == int(sensor_type)
        assert int(model.sensor_objid[sensor_id]) == site_id
        assert float(model.sensor_cutoff[sensor_id]) == cutoff
        assert float(model.sensor_noise[sensor_id]) == noise

    stand_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "stand")
    np.testing.assert_allclose(
        model.qpos0, morphology["reset_fact"]["model_qpos0"], rtol=0.0, atol=0.0
    )
    np.testing.assert_allclose(model.key_qpos[stand_id], KEYFRAME_QPOS, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(model.key_ctrl[stand_id], KEYFRAME_CTRL, rtol=0.0, atol=0.0)

    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, stand_id)
    np.testing.assert_allclose(data.qpos, KEYFRAME_QPOS, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(data.ctrl, KEYFRAME_CTRL, rtol=0.0, atol=0.0)
    mujoco.mj_forward(model, data)
    assert data.ncon == 8
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.qacc).all()
    assert np.isfinite(data.sensordata).all()
    floor_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
    contact_distances = np.array([contact.dist for contact in data.contact[: data.ncon]])
    assert np.isfinite(contact_distances).all()
    assert all(floor_id in (contact.geom1, contact.geom2) for contact in data.contact[: data.ncon])
    assert np.max(np.maximum(-contact_distances, 0.0)) < 0.01

    command = np.asarray(KEYFRAME_CTRL, dtype=float)
    command[0] = 1.0
    data.ctrl[:] = command
    qpos_before = data.qpos.copy()
    time_before = float(data.time)
    for _ in range(8):
        mujoco.mj_step(model, data)
    assert data.time > time_before
    assert data.time == 8 * model.opt.timestep
    np.testing.assert_allclose(data.ctrl, command, rtol=0.0, atol=0.0)
    assert abs(float(data.actuator_force[0])) > 0.0
    assert np.any(np.abs(data.qpos - qpos_before) > 0.0)
    assert np.any(np.abs(data.qvel) > 0.0)
    assert np.any(np.abs(data.qfrc_actuator) > 0.0)
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.qacc).all()
    assert np.isfinite(data.ctrl).all()
    assert np.isfinite(data.actuator_force).all()
    assert np.isfinite(data.qfrc_actuator).all()
    assert np.isfinite(data.sensordata).all()
    assert np.isfinite(data.time)
    step_contact_distances = np.array([contact.dist for contact in data.contact[: data.ncon]])
    assert np.isfinite(step_contact_distances).all()
    assert np.max(np.maximum(-step_contact_distances, 0.0)) < 0.01

    runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
    assert "unitree_g1" not in runnable_index["robots"]
