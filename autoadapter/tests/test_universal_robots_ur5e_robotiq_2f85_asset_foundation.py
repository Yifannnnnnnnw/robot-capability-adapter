from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "universal_robots_ur5e_robotiq_2f85" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
SCENE_PATH = ASSETS_ROOT / "scene.xml"

ARM_JOINT_NAMES = [
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
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
ARM_ACTUATOR_NAMES = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow",
    "wrist_1",
    "wrist_2",
    "wrist_3",
]
ACTUATOR_NAMES = ARM_ACTUATOR_NAMES + ["fingers_actuator"]
GRIPPER_ACTUATED_JOINT_NAMES = ["right_driver_joint", "left_driver_joint"]
PASSIVE_JOINT_NAMES = [
    "right_coupler_joint",
    "right_spring_link_joint",
    "right_follower_joint",
    "left_coupler_joint",
    "left_spring_link_joint",
    "left_follower_joint",
]
PAD_NAMES = ["left_pad1", "left_pad2", "right_pad1", "right_pad2"]
ARM_JOINT_LIMITS = {
    "shoulder_pan_joint": [-6.28319, 6.28319],
    "shoulder_lift_joint": [-6.28319, 6.28319],
    "elbow_joint": [-3.1415, 3.1415],
    "wrist_1_joint": [-6.28319, 6.28319],
    "wrist_2_joint": [-6.28319, 6.28319],
    "wrist_3_joint": [-6.28319, 6.28319],
}
ARM_ACTUATOR_CTRLRANGES = {
    "shoulder_pan": [-6.2831, 6.2831],
    "shoulder_lift": [-6.2831, 6.2831],
    "elbow": [-3.1415, 3.1415],
    "wrist_1": [-6.2831, 6.2831],
    "wrist_2": [-6.2831, 6.2831],
    "wrist_3": [-6.2831, 6.2831],
}
ARM_ACTUATOR_DEFAULTS = {
    "shoulder_pan": (2000.0, 400.0, [-150.0, 150.0]),
    "shoulder_lift": (2000.0, 400.0, [-150.0, 150.0]),
    "elbow": (2000.0, 400.0, [-150.0, 150.0]),
    "wrist_1": (500.0, 100.0, [-28.0, 28.0]),
    "wrist_2": (500.0, 100.0, [-28.0, 28.0]),
    "wrist_3": (500.0, 100.0, [-28.0, 28.0]),
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
HOME_ARM = [-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0]
HOME_QPOS = HOME_ARM + [0.0] * 8
HOME_CTRL = HOME_ARM + [0.0]
ARM_BODY_NAMES = [
    "base",
    "shoulder_link",
    "upper_arm_link",
    "forearm_link",
    "wrist_1_link",
    "wrist_2_link",
    "wrist_3_link",
]
GRIPPER_BODY_NAMES = [
    "robotiq_mount",
    "base_mount",
    "robotiq_base",
    "right_driver",
    "right_coupler",
    "right_spring_link",
    "right_follower",
    "right_pad",
    "right_silicone_pad",
    "left_driver",
    "left_coupler",
    "left_spring_link",
    "left_follower",
    "left_pad",
    "left_silicone_pad",
]
EXPECTED_FILES = (
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "SOURCE.md",
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
    "scene.xml",
    "sweep_into_goal_scene.xml",
    "universal_robots_ur5e_robotiq_2f85.xml",
    "ur5e.png",
    "ur5e.xml",
    "wall_scene.xml",
    "assets/base_0.obj",
    "assets/base_1.obj",
    "assets/forearm_0.obj",
    "assets/forearm_1.obj",
    "assets/forearm_2.obj",
    "assets/forearm_3.obj",
    "assets/shoulder_0.obj",
    "assets/shoulder_1.obj",
    "assets/shoulder_2.obj",
    "assets/upperarm_0.obj",
    "assets/upperarm_1.obj",
    "assets/upperarm_2.obj",
    "assets/upperarm_3.obj",
    "assets/wrist1_0.obj",
    "assets/wrist1_1.obj",
    "assets/wrist1_2.obj",
    "assets/wrist2_0.obj",
    "assets/wrist2_1.obj",
    "assets/wrist2_2.obj",
    "assets/wrist3.obj",
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


def _names(model: mujoco.MjModel, obj: mujoco.mjtObj, count: int) -> list[str | None]:
    return [mujoco.mj_id2name(model, obj, index) for index in range(count)]


def _id(model: mujoco.MjModel, obj: mujoco.mjtObj, name: str) -> int:
    result = mujoco.mj_name2id(model, obj, name)
    assert result >= 0, name
    return result


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _assert_local_reference(source: Path, reference: str, base: Path) -> None:
    assert "://" not in reference
    assert not Path(reference).is_absolute()
    assert "://" not in base.as_posix()
    assert not base.is_absolute()
    resolved = (source.parent / base / reference).resolve()
    assert _is_under(resolved, ASSETS_ROOT.resolve()), reference
    assert resolved.is_file(), reference


def _assert_xml_closure() -> None:
    assert ASSETS_ROOT.is_dir()
    assert not any(path.is_symlink() for path in PACKAGE_ROOT.rglob("*"))
    observed_files = sorted(
        path.relative_to(ASSETS_ROOT).as_posix()
        for path in ASSETS_ROOT.rglob("*")
        if path.is_file()
    )
    assert observed_files == sorted(EXPECTED_FILES)

    for xml_path in sorted(ASSETS_ROOT.rglob("*.xml")):
        root = ET.parse(xml_path).getroot()
        compiler = root.find("compiler")
        mesh_dir = Path(".") if compiler is None else Path(compiler.attrib.get("meshdir", "."))
        texture_dir = Path(".") if compiler is None else Path(compiler.attrib.get("texturedir", "."))
        for element in root.iter():
            if element.tag == "include":
                _assert_local_reference(xml_path, element.attrib["file"], Path("."))
            elif element.tag == "mesh" and "file" in element.attrib:
                _assert_local_reference(xml_path, element.attrib["file"], mesh_dir)
            elif element.tag in {"texture", "hfield", "skin"} and "file" in element.attrib:
                _assert_local_reference(xml_path, element.attrib["file"], texture_dir)


def _pad_separations(model: mujoco.MjModel, data: mujoco.MjData) -> list[float]:
    left_ids = [_id(model, mujoco.mjtObj.mjOBJ_GEOM, name) for name in ("left_pad1", "left_pad2")]
    right_ids = [_id(model, mujoco.mjtObj.mjOBJ_GEOM, name) for name in ("right_pad1", "right_pad2")]
    return [
        float(np.linalg.norm(data.geom_xpos[left_id] - data.geom_xpos[right_id]))
        for left_id, right_id in zip(left_ids, right_ids)
    ]


def _home_cross_contacts(model: mujoco.MjModel, data: mujoco.MjData) -> list[tuple[str | None, str | None]]:
    floor_id = _id(model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
    arm_body_ids = {_id(model, mujoco.mjtObj.mjOBJ_BODY, name) for name in ARM_BODY_NAMES}
    gripper_body_ids = {_id(model, mujoco.mjtObj.mjOBJ_BODY, name) for name in GRIPPER_BODY_NAMES}
    contacts: list[tuple[str | None, str | None]] = []
    for index in range(data.ncon):
        contact = data.contact[index]
        geom_ids = (int(contact.geom1), int(contact.geom2))
        body_ids = tuple(int(model.geom_bodyid[geom_id]) for geom_id in geom_ids)
        is_robot_floor = floor_id in geom_ids and any(body_id > 0 for body_id in body_ids)
        is_arm_gripper = (
            body_ids[0] in arm_body_ids and body_ids[1] in gripper_body_ids
        ) or (
            body_ids[1] in arm_body_ids and body_ids[0] in gripper_body_ids
        )
        if is_robot_floor or is_arm_gripper:
            contacts.append(
                (
                    mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_ids[0]),
                    mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_ids[1]),
                )
            )
    return contacts


def test_universal_robots_ur5e_robotiq_2f85_asset_foundation_is_local_and_live() -> None:
    morphology = json.loads(MORPHOLOGY_PATH.read_text(encoding="utf-8"))
    assert morphology["schema_version"] == "1.0"
    assert morphology["robot_model_id"] == "universal_robots_ur5e_robotiq_2f85"
    assert morphology["robot_configuration_id"] == "universal_robots_ur5e_robotiq_2f85"
    assert morphology["package_version"] == "1.0.0"
    assert morphology["morphology_kind"] == "fixed_base_serial_manipulator_with_tendon_gripper"
    assert morphology["base_type"] == "fixed"
    assert morphology["degrees_of_freedom"] == {"arm": 6, "gripper": 1}
    assert morphology["joint_coordinate_counts"] == {"arm": 6, "gripper": 8, "total": 14}
    assert morphology["mjcf_entrypoint"] == "assets/scene.xml"
    assert morphology["model_dimensions"] == {"nq": 14, "nv": 14, "nu": 7}
    assert morphology["attachment"] == {
        "parent_body": "wrist_3_link",
        "attachment_site": "attachment_site",
        "mount_body": "robotiq_mount",
        "mount_pos": [0.0, 0.1, 0.0],
        "mount_quat": [-1.0, 1.0, 0.0, 0.0],
        "native_mount_body": "base_mount",
        "native_mount_pos": [0.0, 0.0, 0.007],
        "native_base_body": "robotiq_base",
        "native_base_pos": [0.0, 0.0, 0.0038],
        "native_base_quat": [1.0, 0.0, 0.0, -1.0],
    }

    control = morphology["public_control"]
    assert control["actuation"] == "joint_position"
    assert control["transmission"] == "direct_joint_and_tendon"
    assert control["joint_type"] == "hinge"
    assert control["arm_joint_names"] == ARM_JOINT_NAMES
    assert control["gripper_joint_names"] == GRIPPER_JOINT_NAMES
    assert control["joint_names"] == ALL_JOINT_NAMES
    assert control["controlled_joint_names"] == ARM_JOINT_NAMES + GRIPPER_ACTUATED_JOINT_NAMES
    assert control["arm_actuator_names"] == ARM_ACTUATOR_NAMES
    assert control["actuator_names"] == ACTUATOR_NAMES
    assert control["actuator_joint_map"] == {
        **dict(zip(ARM_ACTUATOR_NAMES, ARM_JOINT_NAMES)),
        "fingers_actuator": "tendon:split",
    }
    assert control["arm_joint_limits_rad"] == ARM_JOINT_LIMITS
    assert control["actuator_ctrlrange_rad"] == ARM_ACTUATOR_CTRLRANGES
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
    assert control["gripper_ctrl_range_native"] == [0.0, 255.0]
    assert control["gripper_forcerange"] == [-5.0, 5.0]
    assert control["gripper_joint_range_rad"] == [0.0, 0.8]
    assert control["gripper_joint_ranges_rad"] == GRIPPER_JOINT_RANGES
    assert control["gripper_actuated_joint_names"] == GRIPPER_ACTUATED_JOINT_NAMES
    assert control["passive_joint_names"] == PASSIVE_JOINT_NAMES
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
        "native_pinch_site": "pinch",
        "end_effector_body": "robotiq_base",
        "arm_attachment_site": "attachment_site",
        "arm_attachment_body": "wrist_3_link",
        "gripper_mount_body": "robotiq_mount",
        "gripper_contact_geoms": PAD_NAMES,
        "sensor_names": [],
    }
    assert morphology["contact_observations"] == {
        "pad_geoms": PAD_NAMES,
        "pad_geom_type": "box",
        "pad_geom_contype": 1,
        "pad_geom_conaffinity": 1,
        "pinch_site": "pinch_site",
        "native_pinch_site": "pinch",
    }
    assert morphology["reset_fact"] == {
        "owner": "framework",
        "default": "home",
        "available_keyframe": "home",
        "home_qpos": HOME_QPOS,
        "home_ctrl": HOME_CTRL,
        "gripper_home_control": "open",
        "home_contact_check": {
            "robot_floor_contacts": 0,
            "arm_gripper_contacts": 0,
            "all_state_finite": True,
        },
    }
    assert morphology["asset_provenance"]["sources"] == [
        {
            "component": "universal_robots_ur5e",
            "model_title": "Universal Robots UR5e Description (MJCF)",
            "upstream": "https://github.com/981526092/auto-adapter",
            "derived_revision": "585eb1f1fde33f17f5f9a1e169a18dd41f97b586",
            "source_path": "assets/mjcf/universal_robots_ur5e/",
            "license": "BSD-3-Clause",
            "license_file": "assets/LICENSE",
            "source_file": "assets/SOURCE.md",
        },
        {
            "component": "robotiq_2f85",
            "model_title": "Robotiq 2F-85 Description (MJCF)",
            "upstream": "https://github.com/google-deepmind/mujoco_menagerie/tree/da76818e269b82289eba39808e2fb91d679d6994/robotiq_2f85",
            "derived_revision": "da76818e269b82289eba39808e2fb91d679d6994",
            "source_path": "assets/robotiq_2f85/",
            "license": "BSD-2-Clause",
            "license_file": "assets/robotiq_2f85/LICENSE",
            "source_file": "assets/robotiq_2f85/README.md",
        },
    ]
    assert morphology["research_scope"] == {
        "asset_control_liveness_only": True,
        "task_success_claim": False,
        "runnable_admission": False,
    }

    _assert_xml_closure()
    source = (ASSETS_ROOT / "SOURCE.md").read_text(encoding="utf-8")
    assert "585eb1f1fde33f17f5f9a1e169a18dd41f97b586" in source
    assert "da76818e269b82289eba39808e2fb91d679d6994" in source
    assert "BSD-3-Clause" in source
    assert "BSD-2-Clause" in (ASSETS_ROOT / "robotiq_2f85" / "README.md").read_text(
        encoding="utf-8"
    )

    scene_root = ET.parse(SCENE_PATH).getroot()
    assert [element.attrib["file"] for element in scene_root.findall("include")] == [
        "universal_robots_ur5e_robotiq_2f85.xml"
    ]
    composite_text = (ASSETS_ROOT / "universal_robots_ur5e_robotiq_2f85.xml").read_text(
        encoding="utf-8"
    )
    assert '<body name="robotiq_mount" pos="0 0.1 0" quat="-1 1 0 0"' in composite_text
    assert '<geom class="robotiq_pad_box1" name="left_pad1"/>' in composite_text
    assert '<geom class="robotiq_pad_box2" name="right_pad2"/>' in composite_text

    assert mujoco.__version__ == "3.3.6"
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
    assert (model.nq, model.nv, model.nu) == (14, 14, 7)
    assert _names(model, mujoco.mjtObj.mjOBJ_JOINT, model.njnt) == ALL_JOINT_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu) == ACTUATOR_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SITE, model.nsite) == [
        "attachment_site",
        "pinch",
        "pinch_site",
    ]
    assert _names(model, mujoco.mjtObj.mjOBJ_TENDON, model.ntendon) == ["split"]
    assert _names(model, mujoco.mjtObj.mjOBJ_KEY, model.nkey) == ["home"]

    for joint_name in ALL_JOINT_NAMES:
        joint_id = _id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_HINGE)
        assert bool(model.jnt_limited[joint_id])
        expected_range = (
            ARM_JOINT_LIMITS[joint_name]
            if joint_name in ARM_JOINT_LIMITS
            else GRIPPER_JOINT_RANGES[joint_name]
        )
        np.testing.assert_allclose(model.jnt_range[joint_id], expected_range, rtol=0.0, atol=1e-10)

    for actuator_name, joint_name in zip(ARM_ACTUATOR_NAMES, ARM_JOINT_NAMES):
        actuator_id = _id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
        joint_id = _id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.actuator_trntype[actuator_id]) == int(mujoco.mjtTrn.mjTRN_JOINT)
        assert int(model.actuator_trnid[actuator_id, 0]) == joint_id
        assert int(model.actuator_gaintype[actuator_id]) == int(mujoco.mjtGain.mjGAIN_FIXED)
        assert int(model.actuator_biastype[actuator_id]) == int(mujoco.mjtBias.mjBIAS_AFFINE)
        kp, kv, forcerange = ARM_ACTUATOR_DEFAULTS[actuator_name]
        assert float(model.actuator_gainprm[actuator_id, 0]) == kp
        assert float(model.actuator_biasprm[actuator_id, 1]) == -kp
        assert float(model.actuator_biasprm[actuator_id, 2]) == -kv
        assert bool(model.actuator_ctrllimited[actuator_id])
        np.testing.assert_allclose(
            model.actuator_ctrlrange[actuator_id],
            ARM_ACTUATOR_CTRLRANGES[actuator_name],
            rtol=0.0,
            atol=0.0,
        )
        assert bool(model.actuator_forcelimited[actuator_id])
        np.testing.assert_allclose(model.actuator_forcerange[actuator_id], forcerange, rtol=0.0, atol=0.0)

    for joint_name in GRIPPER_JOINT_NAMES:
        joint_id = _id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        np.testing.assert_allclose(model.jnt_range[joint_id], GRIPPER_JOINT_RANGES[joint_name], rtol=0.0, atol=1e-10)

    tendon_id = _id(model, mujoco.mjtObj.mjOBJ_TENDON, "split")
    fingers_id = _id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, "fingers_actuator")
    assert int(model.actuator_trntype[fingers_id]) == int(mujoco.mjtTrn.mjTRN_TENDON)
    assert int(model.actuator_trnid[fingers_id, 0]) == tendon_id
    np.testing.assert_allclose(model.actuator_ctrlrange[fingers_id], [0.0, 255.0], rtol=0.0, atol=0.0)
    np.testing.assert_allclose(model.actuator_forcerange[fingers_id], [-5.0, 5.0], rtol=0.0, atol=0.0)
    assert int(model.actuator_biastype[fingers_id]) == int(mujoco.mjtBias.mjBIAS_AFFINE)
    np.testing.assert_allclose(model.actuator_gainprm[fingers_id, :3], [0.3137255, 0.0, 0.0], rtol=0.0, atol=0.0)
    np.testing.assert_allclose(model.actuator_biasprm[fingers_id, :3], [0.0, -100.0, -10.0], rtol=0.0, atol=0.0)
    assert int(model.tendon_num[tendon_id]) == 2
    tendon_start = int(model.tendon_adr[tendon_id])
    tendon_end = tendon_start + int(model.tendon_num[tendon_id])
    assert [
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, int(joint_id))
        for joint_id in model.wrap_objid[tendon_start:tendon_end]
    ] == GRIPPER_ACTUATED_JOINT_NAMES
    assert model.wrap_type[tendon_start:tendon_end].tolist() == [
        int(mujoco.mjtWrap.mjWRAP_JOINT),
        int(mujoco.mjtWrap.mjWRAP_JOINT),
    ]
    np.testing.assert_allclose(model.wrap_prm[tendon_start:tendon_end], [0.5, 0.5], rtol=0.0, atol=0.0)

    equality_types = [int(model.eq_type[index]) for index in range(model.neq)]
    assert equality_types == [
        int(mujoco.mjtEq.mjEQ_CONNECT),
        int(mujoco.mjtEq.mjEQ_CONNECT),
        int(mujoco.mjtEq.mjEQ_JOINT),
    ]
    connect_pairs = {
        frozenset(
            (
                mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(model.eq_obj1id[index])),
                mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(model.eq_obj2id[index])),
            )
        )
        for index in range(model.neq)
        if int(model.eq_type[index]) == int(mujoco.mjtEq.mjEQ_CONNECT)
    }
    assert connect_pairs == {
        frozenset(("right_follower", "right_coupler")),
        frozenset(("left_follower", "left_coupler")),
    }
    joint_equality_id = equality_types.index(int(mujoco.mjtEq.mjEQ_JOINT))
    assert {
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, int(model.eq_obj1id[joint_equality_id])),
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, int(model.eq_obj2id[joint_equality_id])),
    } == set(GRIPPER_ACTUATED_JOINT_NAMES)
    np.testing.assert_allclose(model.eq_data[joint_equality_id, :5], [0.0, 1.0, 0.0, 0.0, 0.0], rtol=0.0, atol=0.0)

    for pad_name in PAD_NAMES:
        pad_id = _id(model, mujoco.mjtObj.mjOBJ_GEOM, pad_name)
        assert int(model.geom_type[pad_id]) == int(mujoco.mjtGeom.mjGEOM_BOX)
        assert int(model.geom_contype[pad_id]) == 1
        assert int(model.geom_conaffinity[pad_id]) == 1
        assert int(model.geom_group[pad_id]) == 3
        assert mujoco.mj_id2name(
            model,
            mujoco.mjtObj.mjOBJ_BODY,
            int(model.geom_bodyid[pad_id]),
        ) in {"left_pad", "right_pad"}

    attachment_site_id = _id(model, mujoco.mjtObj.mjOBJ_SITE, "attachment_site")
    assert mujoco.mj_id2name(
        model,
        mujoco.mjtObj.mjOBJ_BODY,
        int(model.site_bodyid[attachment_site_id]),
    ) == "wrist_3_link"
    np.testing.assert_allclose(model.site_pos[attachment_site_id], [0.0, 0.1, 0.0], rtol=0.0, atol=0.0)
    np.testing.assert_allclose(
        model.site_quat[attachment_site_id],
        np.asarray([-1.0, 1.0, 0.0, 0.0]) / np.sqrt(2.0),
        rtol=0.0,
        atol=1e-12,
    )
    mount_id = _id(model, mujoco.mjtObj.mjOBJ_BODY, "robotiq_mount")
    assert mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(model.body_parentid[mount_id])) == "wrist_3_link"
    np.testing.assert_allclose(model.body_pos[mount_id], [0.0, 0.1, 0.0], rtol=0.0, atol=0.0)
    np.testing.assert_allclose(
        model.body_quat[mount_id],
        np.asarray([-1.0, 1.0, 0.0, 0.0]) / np.sqrt(2.0),
        rtol=0.0,
        atol=1e-12,
    )
    base_mount_id = _id(model, mujoco.mjtObj.mjOBJ_BODY, "base_mount")
    np.testing.assert_allclose(model.body_pos[base_mount_id], [0.0, 0.0, 0.007], rtol=0.0, atol=0.0)
    gripper_base_id = _id(model, mujoco.mjtObj.mjOBJ_BODY, "robotiq_base")
    np.testing.assert_allclose(model.body_pos[gripper_base_id], [0.0, 0.0, 0.0038], rtol=0.0, atol=0.0)
    np.testing.assert_allclose(
        model.body_quat[gripper_base_id],
        np.asarray([1.0, 0.0, 0.0, -1.0]) / np.sqrt(2.0),
        rtol=0.0,
        atol=1e-12,
    )
    pinch_site_id = _id(model, mujoco.mjtObj.mjOBJ_SITE, "pinch_site")
    native_pinch_id = _id(model, mujoco.mjtObj.mjOBJ_SITE, "pinch")
    assert int(model.site_bodyid[pinch_site_id]) == gripper_base_id
    assert int(model.site_bodyid[native_pinch_id]) == gripper_base_id
    np.testing.assert_allclose(model.site_pos[pinch_site_id], [0.0, 0.0, 0.145], rtol=0.0, atol=0.0)
    np.testing.assert_allclose(model.site_pos[native_pinch_id], [0.0, 0.0, 0.145], rtol=0.0, atol=0.0)

    home_id = _id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    np.testing.assert_allclose(model.key_qpos[home_id], HOME_QPOS, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(model.key_ctrl[home_id], HOME_CTRL, rtol=0.0, atol=0.0)
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    np.testing.assert_allclose(data.qpos, HOME_QPOS, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(data.ctrl, HOME_CTRL, rtol=0.0, atol=0.0)
    mujoco.mj_forward(model, data)
    assert _home_cross_contacts(model, data) == []
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.qacc).all()
    assert np.isfinite(data.ctrl).all()
    assert np.isfinite(data.time)

    arm_actuator_id = _id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, "shoulder_pan")
    arm_joint_id = _id(model, mujoco.mjtObj.mjOBJ_JOINT, "shoulder_pan_joint")
    arm_qpos_address = int(model.jnt_qposadr[arm_joint_id])
    arm_qpos_before = float(data.qpos[arm_qpos_address])
    data.ctrl[:] = model.key_ctrl[home_id]
    data.ctrl[arm_actuator_id] = -1.0
    for _ in range(250):
        mujoco.mj_step(model, data)
    assert abs(float(data.qpos[arm_qpos_address]) - arm_qpos_before) > 1e-3
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.qacc).all()
    assert np.isfinite(data.ctrl).all()

    mujoco.mj_resetDataKeyframe(model, data, home_id)
    mujoco.mj_forward(model, data)
    fingers_id = _id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, "fingers_actuator")
    data.ctrl[:] = model.key_ctrl[home_id]
    data.ctrl[fingers_id] = 0.0
    for _ in range(500):
        mujoco.mj_step(model, data)
    open_separations = _pad_separations(model, data)
    data.ctrl[fingers_id] = 255.0
    for _ in range(1000):
        mujoco.mj_step(model, data)
    closed_separations = _pad_separations(model, data)
    assert open_separations[0] > closed_separations[0] + 1e-3
    assert open_separations[1] > closed_separations[1] + 1e-3
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.qacc).all()
    assert np.isfinite(data.ctrl).all()
