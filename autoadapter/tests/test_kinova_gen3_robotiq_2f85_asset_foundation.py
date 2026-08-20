from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "kinova_gen3_robotiq_2f85" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
SCENE_PATH = ASSETS_ROOT / "scene.xml"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"

ARM_JOINT_NAMES = [
    "joint_1",
    "joint_2",
    "joint_3",
    "joint_4",
    "joint_5",
    "joint_6",
    "joint_7",
]
GRIPPER_JOINT_NAMES = [
    "right_driver_joint",
    "right_coupler_joint",
    "right_spring_link_joint",
    "right_follower_joint",
    "left_driver_joint",
    "left_coupler_joint",
    "left_spring_link_joint",
    "left_follower_joint",
]
ALL_JOINT_NAMES = ARM_JOINT_NAMES + GRIPPER_JOINT_NAMES
ARM_ACTUATOR_NAMES = ARM_JOINT_NAMES.copy()
ACTUATOR_NAMES = ARM_ACTUATOR_NAMES + ["fingers_actuator"]
GRIPPER_ACTUATED_JOINT_NAMES = ["right_driver_joint", "left_driver_joint"]
PAD_NAMES = ["left_pad1", "left_pad2", "right_pad1", "right_pad2"]
ARM_JOINT_LIMITS = {
    "joint_2": [-2.24, 2.24],
    "joint_4": [-2.57, 2.57],
    "joint_6": [-2.09, 2.09],
}
UNLIMITED_JOINT_NAMES = ["joint_1", "joint_3", "joint_5", "joint_7"]
ARM_ACTUATOR_CTRLRANGES = {
    "joint_2": [-2.2497294058206907, 2.2497294058206907],
    "joint_4": [-2.5795966344476193, 2.5795966344476193],
    "joint_6": [-2.0996310901491784, 2.0996310901491784],
}
GRIPPER_JOINT_RANGES = {
    "right_driver_joint": [0.0, 0.8],
    "right_coupler_joint": [-1.57, 0.0],
    "right_spring_link_joint": [-0.29670597283, 0.8],
    "right_follower_joint": [-0.872664, 0.872664],
    "left_driver_joint": [0.0, 0.8],
    "left_coupler_joint": [-1.57, 0.0],
    "left_spring_link_joint": [-0.29670597283, 0.8],
    "left_follower_joint": [-0.872664, 0.872664],
}
EXPECTED_FILES = (
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "SOURCE.md",
    "gen3.png",
    "gen3.xml",
    "kinova_gen3_robotiq_2f85.xml",
    "scene.xml",
    "bin_picking_scene.xml",
    "button_front_scene.xml",
    "button_topdown_scene.xml",
    "dial_scene.xml",
    "door_scene.xml",
    "drawer_scene.xml",
    "faucet_scene.xml",
    "handle_vertical_scene.xml",
    "lever_scene.xml",
    "peg_insertion_side_scene.xml",
    "pick_out_of_hole_scene.xml",
    "pick_place_scene.xml",
    "pick_place_wall_scene.xml",
    "push_to_goal_scene.xml",
    "reach_scene.xml",
    "sweep_into_goal_scene.xml",
    "wall_scene.xml",
    "assets/base_link.stl",
    "assets/bracelet_no_vision_link.stl",
    "assets/bracelet_with_vision_link.stl",
    "assets/forearm_link.stl",
    "assets/half_arm_1_link.stl",
    "assets/half_arm_2_link.stl",
    "assets/shoulder_link.stl",
    "assets/spherical_wrist_1_link.stl",
    "assets/spherical_wrist_2_link.stl",
    "robotiq_2f85/2f85.png",
    "robotiq_2f85/2f85.xml",
    "robotiq_2f85/CHANGELOG.md",
    "robotiq_2f85/LICENSE",
    "robotiq_2f85/README.md",
    "robotiq_2f85/scene.xml",
    "robotiq_2f85/assets/base.stl",
    "robotiq_2f85/assets/base_mount.stl",
    "robotiq_2f85/assets/coupler.stl",
    "robotiq_2f85/assets/driver.stl",
    "robotiq_2f85/assets/follower.stl",
    "robotiq_2f85/assets/pad.stl",
    "robotiq_2f85/assets/silicone_pad.stl",
    "robotiq_2f85/assets/spring_link.stl",
)


def _names(model: mujoco.MjModel, obj: mujoco.mjtObj, count: int) -> list[str]:
    return [mujoco.mj_id2name(model, obj, index) for index in range(count)]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _assert_local_reference(source: Path, reference: str, base: str = ".") -> None:
    assert "://" not in reference
    assert not Path(reference).is_absolute()
    assert "://" not in base
    assert not Path(base).is_absolute()
    resolved = (source.parent / base / reference).resolve()
    assert _is_under(resolved, ASSETS_ROOT.resolve()), reference
    assert resolved.is_file(), reference


def _assert_xml_asset_closure() -> None:
    assert ASSETS_ROOT.is_dir()
    assert not any(path.is_symlink() for path in ASSETS_ROOT.rglob("*"))
    for relative_path in EXPECTED_FILES:
        assert (ASSETS_ROOT / relative_path).is_file(), relative_path

    for xml_path in sorted(ASSETS_ROOT.rglob("*.xml")):
        root = ET.parse(xml_path).getroot()
        compiler = root.find("compiler")
        mesh_dir = "." if compiler is None else compiler.attrib.get("meshdir", ".")
        texture_dir = "." if compiler is None else compiler.attrib.get("texturedir", ".")
        for element in root.iter():
            if element.tag == "include":
                _assert_local_reference(xml_path, element.attrib["file"])
            elif element.tag == "mesh" and "file" in element.attrib:
                _assert_local_reference(xml_path, element.attrib["file"], mesh_dir)
            elif element.tag in {"texture", "hfield", "skin"} and "file" in element.attrib:
                _assert_local_reference(xml_path, element.attrib["file"], texture_dir)


def _id(model: mujoco.MjModel, obj: mujoco.mjtObj, name: str) -> int:
    result = mujoco.mj_name2id(model, obj, name)
    assert result >= 0, name
    return result


def _pad_separations(model: mujoco.MjModel, data: mujoco.MjData) -> list[float]:
    left_ids = [_id(model, mujoco.mjtObj.mjOBJ_GEOM, name) for name in ("left_pad1", "left_pad2")]
    right_ids = [_id(model, mujoco.mjtObj.mjOBJ_GEOM, name) for name in ("right_pad1", "right_pad2")]
    return [
        float(np.linalg.norm(data.geom_xpos[left_id] - data.geom_xpos[right_id]))
        for left_id, right_id in zip(left_ids, right_ids)
    ]


def test_kinova_gen3_robotiq_2f85_asset_foundation_is_local_exact_and_actuator_live() -> None:
    morphology = json.loads(MORPHOLOGY_PATH.read_text(encoding="utf-8"))
    assert morphology["robot_model_id"] == "kinova_gen3_robotiq_2f85"
    assert morphology["robot_configuration_id"] == "kinova_gen3_robotiq_2f85"
    assert morphology["package_version"] == "1.0.0"
    assert morphology["morphology_kind"] == "fixed_base_serial_manipulator"
    assert morphology["degrees_of_freedom"] == {"arm": 7, "gripper": 1}
    assert morphology["joint_coordinate_counts"] == {"arm": 7, "gripper": 8, "total": 15}
    assert morphology["mjcf_entrypoint"] == "assets/scene.xml"

    control = morphology["public_control"]
    assert control["actuation"] == "joint_position"
    assert control["joint_type"] == "hinge"
    assert control["arm_joint_names"] == ARM_JOINT_NAMES
    assert control["gripper_joint_names"] == GRIPPER_JOINT_NAMES
    assert control["joint_names"] == ALL_JOINT_NAMES
    assert control["controlled_joint_names"] == ARM_JOINT_NAMES + GRIPPER_ACTUATED_JOINT_NAMES
    assert control["arm_actuator_names"] == ARM_ACTUATOR_NAMES
    assert control["actuator_names"] == ACTUATOR_NAMES
    assert control["actuator_joint_map"] == {
        **{name: name for name in ARM_ACTUATOR_NAMES},
        "fingers_actuator": "tendon:split",
    }
    assert control["arm_joint_limits_rad"] == ARM_JOINT_LIMITS
    assert control["unlimited_joint_names"] == UNLIMITED_JOINT_NAMES
    assert control["actuator_ctrlrange_rad"] == ARM_ACTUATOR_CTRLRANGES
    assert control["unlimited_actuator_names"] == UNLIMITED_JOINT_NAMES
    assert control["position_actuator_defaults"] == {
        "large_actuator": {
            "actuator_names": ["joint_1", "joint_2", "joint_3", "joint_4"],
            "kp": 2000.0,
            "kv": 100.0,
            "forcerange": [-105.0, 105.0],
        },
        "small_actuator": {
            "actuator_names": ["joint_5", "joint_6", "joint_7"],
            "kp": 500.0,
            "kv": 50.0,
            "forcerange": [-52.0, 52.0],
        },
    }
    assert control["gripper_ctrl_range_native"] == [0.0, 255.0]
    assert control["gripper_joint_range_rad"] == [0.0, 0.8]
    assert control["gripper_joint_ranges_rad"] == GRIPPER_JOINT_RANGES
    assert control["gripper_actuated_joint_names"] == GRIPPER_ACTUATED_JOINT_NAMES
    assert control["passive_joint_names"] == [
        "right_coupler_joint",
        "right_spring_link_joint",
        "right_follower_joint",
        "left_coupler_joint",
        "left_spring_link_joint",
        "left_follower_joint",
    ]
    assert control["gripper_tendon"] == {
        "actuator_name": "fingers_actuator",
        "tendon_name": "split",
        "controlled_joints": GRIPPER_ACTUATED_JOINT_NAMES,
        "joint_coefficients": {
            "right_driver_joint": 0.5,
            "left_driver_joint": 0.5,
        },
        "passive_coupling": "equality",
    }
    assert control["gripper_direction"] == {
        "closed": 255.0,
        "open": 0.0,
        "unit": "native_control",
        "basis": "smaller left/right pad separation after actuator-driven settling",
    }
    assert morphology["public_observations"] == {
        "end_effector_site": "pinch_site",
        "end_effector_body": "bracelet_link",
        "gripper_base_body": "base",
        "gripper_contact_geoms": PAD_NAMES,
    }
    assert morphology["public_affordances"]["actions"] == [
        "joint_position_control",
        "gripper_position_control",
    ]
    assert morphology["reset_fact"] == {
        "owner": "framework",
        "default": "home",
        "neutral": "home",
        "available_keyframes": ["home", "retract"],
    }

    _assert_xml_asset_closure()
    source = (ASSETS_ROOT / "SOURCE.md").read_text(encoding="utf-8")
    assert "/kinova_gen3" in source
    assert "robotiq_2f85" in source
    assert source.count("da76818e269b82289eba39808e2fb91d679d6994") >= 2
    assert "BSD-3-Clause" in source
    assert "BSD-2-Clause" in (ASSETS_ROOT / "robotiq_2f85" / "README.md").read_text(
        encoding="utf-8"
    )

    scene_root = ET.parse(SCENE_PATH).getroot()
    assert [element.attrib["file"] for element in scene_root.findall("include")] == [
        "kinova_gen3_robotiq_2f85.xml"
    ]
    composite_text = (ASSETS_ROOT / "kinova_gen3_robotiq_2f85.xml").read_text(encoding="utf-8")
    source_text = (ASSETS_ROOT / "robotiq_2f85" / "2f85.xml").read_text(encoding="utf-8")
    assert "base_mount" not in composite_text
    assert 'body name="base_mount"' in source_text

    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert morphology["model_counts"] == {
        "nq": model.nq,
        "nv": model.nv,
        "njnt": model.njnt,
        "nu": model.nu,
        "ngeom": model.ngeom,
        "nsite": model.nsite,
        "ntendon": model.ntendon,
        "neq": model.neq,
        "nkey": model.nkey,
    }
    assert _names(model, mujoco.mjtObj.mjOBJ_JOINT, model.njnt) == ALL_JOINT_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu) == ACTUATOR_NAMES

    for joint_name in ALL_JOINT_NAMES:
        joint_id = _id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_HINGE)
        if joint_name in ARM_JOINT_NAMES and joint_name not in ARM_JOINT_LIMITS:
            assert not bool(model.jnt_limited[joint_id])
        else:
            assert bool(model.jnt_limited[joint_id])
            expected_range = (
                ARM_JOINT_LIMITS[joint_name]
                if joint_name in ARM_JOINT_LIMITS
                else GRIPPER_JOINT_RANGES[joint_name]
            )
            np.testing.assert_allclose(model.jnt_range[joint_id], expected_range, rtol=0.0, atol=1e-10)

    for actuator_name in ARM_ACTUATOR_NAMES:
        actuator_id = _id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
        joint_id = _id(model, mujoco.mjtObj.mjOBJ_JOINT, actuator_name)
        assert int(model.actuator_trntype[actuator_id]) == int(mujoco.mjtTrn.mjTRN_JOINT)
        assert int(model.actuator_trnid[actuator_id, 0]) == joint_id
        assert bool(model.actuator_ctrllimited[actuator_id]) == (actuator_name in ARM_ACTUATOR_CTRLRANGES)
        if actuator_name in ARM_ACTUATOR_CTRLRANGES:
            np.testing.assert_allclose(
                model.actuator_ctrlrange[actuator_id],
                ARM_ACTUATOR_CTRLRANGES[actuator_name],
                rtol=0.0,
                atol=1e-10,
            )

    tendon_id = _id(model, mujoco.mjtObj.mjOBJ_TENDON, "split")
    fingers_id = _id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, "fingers_actuator")
    assert int(model.actuator_trntype[fingers_id]) == int(mujoco.mjtTrn.mjTRN_TENDON)
    assert int(model.actuator_trnid[fingers_id, 0]) == tendon_id
    np.testing.assert_allclose(model.actuator_ctrlrange[fingers_id], [0.0, 255.0], rtol=0.0, atol=0.0)
    np.testing.assert_allclose(model.actuator_forcerange[fingers_id], [-5.0, 5.0], rtol=0.0, atol=0.0)
    assert int(model.tendon_num[tendon_id]) == 2
    tendon_start = int(model.tendon_adr[tendon_id])
    tendon_end = tendon_start + int(model.tendon_num[tendon_id])
    assert [
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, int(joint_id))
        for joint_id in model.wrap_objid[tendon_start:tendon_end]
    ] == GRIPPER_ACTUATED_JOINT_NAMES
    np.testing.assert_allclose(model.wrap_prm[tendon_start:tendon_end], [0.5, 0.5], rtol=0.0, atol=0.0)

    for pad_name in PAD_NAMES:
        pad_id = _id(model, mujoco.mjtObj.mjOBJ_GEOM, pad_name)
        assert int(model.geom_type[pad_id]) == int(mujoco.mjtGeom.mjGEOM_BOX)
        assert int(model.geom_contype[pad_id]) == 1
        assert int(model.geom_conaffinity[pad_id]) == 1
        assert int(model.geom_group[pad_id]) == 3

    pinch_site_id = _id(model, mujoco.mjtObj.mjOBJ_SITE, "pinch_site")
    pinch_body_id = int(model.site_bodyid[pinch_site_id])
    assert mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, pinch_body_id) == "bracelet_link"
    np.testing.assert_allclose(model.site_pos[pinch_site_id], [0.0, 0.0, -0.181525], rtol=0.0, atol=1e-12)
    np.testing.assert_allclose(model.site_quat[pinch_site_id], [0.0, 1.0, 0.0, 0.0], rtol=0.0, atol=0.0)

    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base_mount") == -1
    base_id = _id(model, mujoco.mjtObj.mjOBJ_BODY, "base")
    assert mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(model.body_parentid[base_id])) == "bracelet_link"
    np.testing.assert_allclose(model.body_pos[base_id], [0.0, 0.0, -0.06149039], rtol=0.0, atol=1e-12)
    np.testing.assert_allclose(
        model.body_quat[base_id],
        np.asarray([0.0, -1.0, 1.0, 0.0]) / np.sqrt(2.0),
        rtol=0.0,
        atol=1e-12,
    )

    home_id = _id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    retract_id = _id(model, mujoco.mjtObj.mjOBJ_KEY, "retract")
    assert model.key_qpos[home_id].shape == (model.nq,)
    assert model.key_ctrl[home_id].shape == (model.nu,)
    assert model.key_qpos[retract_id].shape == (model.nq,)
    assert model.key_ctrl[retract_id].shape == (model.nu,)
    np.testing.assert_allclose(model.key_qpos[home_id, 7:], 0.0, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(model.key_ctrl[home_id, -1], 0.0, rtol=0.0, atol=0.0)

    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    mujoco.mj_forward(model, data)
    arm_id = _id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, "joint_2")
    arm_joint_id = _id(model, mujoco.mjtObj.mjOBJ_JOINT, "joint_2")
    arm_qpos_address = int(model.jnt_qposadr[arm_joint_id])
    arm_before = float(data.qpos[arm_qpos_address])

    # The liveness block uses only actuator controls and real MuJoCo stepping.
    data.ctrl[:] = model.key_ctrl[home_id]
    data.ctrl[arm_id] = 0.5
    data.ctrl[fingers_id] = control["gripper_direction"]["open"]
    for _ in range(500):
        mujoco.mj_step(model, data)
    open_separations = _pad_separations(model, data)
    open_separation = float(np.mean(open_separations))

    data.ctrl[fingers_id] = control["gripper_direction"]["closed"]
    for _ in range(1000):
        mujoco.mj_step(model, data)
    closed_separations = _pad_separations(model, data)
    closed_separation = float(np.mean(closed_separations))

    assert abs(float(data.qpos[arm_qpos_address]) - arm_before) > 1e-3
    assert open_separation > closed_separation + 1e-3
    assert open_separations[0] > closed_separations[0] + 1e-3
    assert open_separations[1] > closed_separations[1] + 1e-3
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.ctrl).all()

    runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
    assert "kinova_gen3_robotiq_2f85" not in runnable_index["robots"]

    research_index = json.loads(RESEARCH_INDEX_PATH.read_text(encoding="utf-8"))
    candidate_ids = {
        candidate["robot_configuration_id"] for candidate in research_index["candidates"]
    }
    assert "kinova_gen3" not in candidate_ids
    candidate = next(
        candidate
        for candidate in research_index["candidates"]
        if candidate["robot_configuration_id"] == "kinova_gen3_robotiq_2f85"
    )
    observed_paths = {
        item["path"] for item in candidate["locally_observed_source_material"]
    }
    assert {
        "autoadapter/libraries/robots/kinova_gen3_robotiq_2f85/1.0.0/assets/scene.xml",
        "autoadapter/libraries/robots/kinova_gen3_robotiq_2f85/1.0.0/morphology.json",
        "autoadapter/libraries/robots/kinova_gen3_robotiq_2f85/1.0.0/tasks/sources.json",
        "autoadapter/libraries/robots/kinova_gen3_robotiq_2f85/1.0.0/tasks/catalog.json",
        "autoadapter/libraries/robots/kinova_gen3_robotiq_2f85/1.0.0/skeleton/arm_serial_dls.py",
        "autoadapter/libraries/robots/kinova_gen3_robotiq_2f85/1.0.0/assets/reach_scene.xml",
        "autoadapter/libraries/robots/kinova_gen3_robotiq_2f85/1.0.0/tasks/private/instances.json",
        "autoadapter/libraries/robots/kinova_gen3_robotiq_2f85/1.0.0/reference/driver.py",
        "autoadapter/evidence/README.md",
    }.issubset(observed_paths)
    evidence_record = next(
        item
        for item in candidate["locally_observed_source_material"]
        if item["path"] == "autoadapter/evidence/README.md"
    )
    assert "20/20" in evidence_record["observation"]
    assert "videos" in evidence_record["observation"]
    assert "package loader" in evidence_record["observation"]
    missing = " ".join(candidate["missing_for_runnable_package"])
    assert "contact-capable end-effector configuration" not in missing
    assert "20 distinct applicable source-backed tasks" not in missing
    assert "Create tasks/sources.json" not in missing
    assert "tasks/private/instances.json" not in missing
    assert "Materialize the canonical task scenes" not in missing
    assert "arm_serial_dls skeleton" not in missing
    assert "reference driver" not in missing
    assert "package check" not in missing
    assert "positive control" not in missing
    assert "dynamic canary" in missing
    assert "runnable index" in missing
