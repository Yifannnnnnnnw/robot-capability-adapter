from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "ufactory_xarm7" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
SCENE_PATH = ASSETS_ROOT / "scene.xml"

ARM_JOINT_NAMES = [
    "joint1",
    "joint2",
    "joint3",
    "joint4",
    "joint5",
    "joint6",
    "joint7",
]
GRIPPER_JOINT_NAMES = [
    "left_driver_joint",
    "left_finger_joint",
    "left_inner_knuckle_joint",
    "right_driver_joint",
    "right_finger_joint",
    "right_inner_knuckle_joint",
]
CONTROLLED_JOINT_NAMES = ARM_JOINT_NAMES + [
    "left_driver_joint",
    "right_driver_joint",
]
ALL_JOINT_NAMES = ARM_JOINT_NAMES + GRIPPER_JOINT_NAMES
ACTUATOR_NAMES = [
    "act1",
    "act2",
    "act3",
    "act4",
    "act5",
    "act6",
    "act7",
    "gripper",
]
ARM_JOINT_LIMITS = {
    "joint1": [-6.28319, 6.28319],
    "joint2": [-2.059, 2.0944],
    "joint3": [-6.28319, 6.28319],
    "joint4": [-0.19198, 3.927],
    "joint5": [-6.28319, 6.28319],
    "joint6": [-1.69297, 3.14159],
    "joint7": [-6.28319, 6.28319],
}
ACTUATOR_CTRLRANGES = {
    "act1": [-6.28319, 6.28319],
    "act2": [-2.059, 2.0944],
    "act3": [-6.28319, 6.28319],
    "act4": [-0.19198, 3.927],
    "act5": [-6.28319, 6.28319],
    "act6": [-1.69297, 3.14159],
    "act7": [-6.28319, 6.28319],
}
GRIPPER_JOINT_RANGE = [0.0, 0.85]
PAD_NAMES = [
    "left_finger_pad_1",
    "left_finger_pad_2",
    "right_finger_pad_1",
    "right_finger_pad_2",
]
EXPECTED_CLOSURE_FILES = (
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "SOURCE.md",
    "hand.xml",
    "scene.xml",
    "xarm7.png",
    "xarm7.xml",
    "xarm7_nohand.xml",
    "assets/base_link.stl",
    "assets/end_tool.stl",
    "assets/left_finger.stl",
    "assets/left_inner_knuckle.stl",
    "assets/left_outer_knuckle.stl",
    "assets/link1.stl",
    "assets/link2.stl",
    "assets/link3.stl",
    "assets/link4.stl",
    "assets/link5.stl",
    "assets/link6.stl",
    "assets/link7.stl",
    "assets/link_base.stl",
    "assets/right_finger.stl",
    "assets/right_inner_knuckle.stl",
    "assets/right_outer_knuckle.stl",
)
TASK_SCENE_FILES = (
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
)
HOME_QPOS = [0.0, -0.247, 0.0, 0.909, 0.0, 1.15644, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
HOME_CTRL = [0.0, -0.247, 0.0, 0.909, 0.0, 1.15644, 0.0, 0.0]


def _names(model: mujoco.MjModel, obj: mujoco.mjtObj, count: int) -> list[str]:
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


def test_ufactory_xarm7_asset_foundation_loads_local_closure_and_moves_gripper() -> None:
    morphology = json.loads(MORPHOLOGY_PATH.read_text(encoding="utf-8"))
    assert morphology["robot_model_id"] == "ufactory_xarm7"
    assert morphology["robot_configuration_id"] == "ufactory_xarm7"
    assert morphology["package_version"] == "1.0.0"
    assert morphology["morphology_kind"] == "fixed_base_serial_manipulator"
    assert morphology["degrees_of_freedom"] == {"arm": 7, "gripper": 1}
    assert morphology["mjcf_entrypoint"] == "assets/scene.xml"

    control = morphology["public_control"]
    assert control["actuation"] == "joint_position"
    assert control["joint_type"] == "hinge"
    assert control["joint_names"] == CONTROLLED_JOINT_NAMES
    assert control["actuator_names"] == ACTUATOR_NAMES
    assert control["actuator_joint_map"] == {
        **{f"act{index}": joint for index, joint in enumerate(ARM_JOINT_NAMES, start=1)},
        "gripper": "tendon:split",
    }
    assert control["arm_joint_limits_rad"] == ARM_JOINT_LIMITS
    assert control["actuator_ctrlrange_rad"] == ACTUATOR_CTRLRANGES
    assert control["gripper_ctrl_range_native"] == [0.0, 255.0]
    assert control["gripper_joint_names"] == GRIPPER_JOINT_NAMES
    assert control["gripper_joint_range_rad"] == GRIPPER_JOINT_RANGE
    assert control["passive_joint_names"] == [
        "left_finger_joint",
        "left_inner_knuckle_joint",
        "right_finger_joint",
        "right_inner_knuckle_joint",
    ]
    assert control["gripper_tendon"] == {
        "actuator_name": "gripper",
        "tendon_name": "split",
        "controlled_joints": ["right_driver_joint", "left_driver_joint"],
        "joint_coefficients": {
            "right_driver_joint": 0.5,
            "left_driver_joint": 0.5,
        },
        "passive_coupling": "equality",
    }
    assert morphology["public_observations"] == {
        "end_effector_site": "link_tcp",
        "end_effector_body": "xarm_gripper_base_link",
        "gripper_contact_geoms": PAD_NAMES,
    }
    assert morphology["public_affordances"]["actions"] == [
        "joint_position_control",
        "gripper_position_control",
    ]
    assert morphology["reset_fact"] == {
        "owner": "framework",
        "default": "model_qpos0",
        "available_keyframe": "home",
    }
    assert morphology["asset_provenance"] == {
        "model_title": "xArm7 Description (MJCF)",
        "upstream": "https://github.com/google-deepmind/mujoco_menagerie/tree/da76818e269b82289eba39808e2fb91d679d6994/ufactory_xarm7",
        "derived_revision": "da76818e269b82289eba39808e2fb91d679d6994",
        "license": "BSD-3-Clause",
        "license_file": "assets/LICENSE",
        "source_file": "assets/SOURCE.md",
    }

    assert sorted(
        path.relative_to(ASSETS_ROOT).as_posix()
        for path in ASSETS_ROOT.rglob("*")
        if path.is_file()
    ) == sorted((*EXPECTED_CLOSURE_FILES, *TASK_SCENE_FILES))
    assert not any(path.is_symlink() for path in ASSETS_ROOT.rglob("*"))
    source = (ASSETS_ROOT / "SOURCE.md").read_text(encoding="utf-8")
    assert "da76818e269b82289eba39808e2fb91d679d6994" in source
    assert "BSD-3-Clause" in source

    scene_root = ET.parse(SCENE_PATH).getroot()
    include_refs = [element.attrib["file"] for element in scene_root.iter("include")]
    assert include_refs == ["xarm7.xml"]
    for reference in include_refs:
        _assert_local_file(SCENE_PATH, reference)

    for filename in ("xarm7.xml", "hand.xml", "xarm7_nohand.xml"):
        model_xml = ASSETS_ROOT / filename
        model_root = ET.parse(model_xml).getroot()
        compiler = model_root.find("compiler")
        assert compiler is not None
        mesh_dir = compiler.attrib["meshdir"]
        assert mesh_dir == "assets"
        for mesh in model_root.findall("asset/mesh"):
            _assert_local_file(model_xml, str(Path(mesh_dir) / mesh.attrib["file"]))

    assert mujoco.__version__ == "3.3.6"
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (model.nq, model.nv, model.nu, model.njnt, model.nsite, model.ntendon, model.neq, model.nkey) == (
        13,
        13,
        8,
        13,
        1,
        1,
        3,
        1,
    )
    assert _names(model, mujoco.mjtObj.mjOBJ_JOINT, model.njnt) == ALL_JOINT_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu) == ACTUATOR_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SITE, model.nsite) == ["link_tcp"]
    assert _names(model, mujoco.mjtObj.mjOBJ_TENDON, model.ntendon) == ["split"]
    assert _names(model, mujoco.mjtObj.mjOBJ_KEY, model.nkey) == ["home"]

    for joint_name in ALL_JOINT_NAMES:
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_HINGE)
        assert bool(model.jnt_limited[joint_id])
        expected_range = ARM_JOINT_LIMITS.get(joint_name, GRIPPER_JOINT_RANGE)
        np.testing.assert_allclose(model.jnt_range[joint_id], expected_range)

    for index, actuator_name in enumerate(ACTUATOR_NAMES[:7]):
        actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, ARM_JOINT_NAMES[index])
        assert int(model.actuator_trntype[actuator_id]) == int(mujoco.mjtTrn.mjTRN_JOINT)
        assert int(model.actuator_trnid[actuator_id, 0]) == joint_id
        np.testing.assert_allclose(model.actuator_ctrlrange[actuator_id], ACTUATOR_CTRLRANGES[actuator_name])

    tendon_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_TENDON, "split")
    gripper_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, "gripper")
    assert int(model.actuator_trntype[gripper_id]) == int(mujoco.mjtTrn.mjTRN_TENDON)
    assert int(model.actuator_trnid[gripper_id, 0]) == tendon_id
    np.testing.assert_allclose(model.actuator_ctrlrange[gripper_id], [0.0, 255.0])
    tendon_start = int(model.tendon_adr[tendon_id])
    tendon_end = tendon_start + int(model.tendon_num[tendon_id])
    tendon_joint_ids = [
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "right_driver_joint"),
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "left_driver_joint"),
    ]
    assert model.wrap_type[tendon_start:tendon_end].tolist() == [int(mujoco.mjtWrap.mjWRAP_JOINT)] * 2
    assert model.wrap_objid[tendon_start:tendon_end].tolist() == tendon_joint_ids
    np.testing.assert_allclose(model.wrap_prm[tendon_start:tendon_end], [0.5, 0.5])

    joint_equality_ids = [
        index
        for index in range(model.neq)
        if int(model.eq_type[index]) == int(mujoco.mjtEq.mjEQ_JOINT)
    ]
    assert len(joint_equality_ids) == 1
    equality_id = joint_equality_ids[0]
    assert {
        int(model.eq_obj1id[equality_id]),
        int(model.eq_obj2id[equality_id]),
    } == {
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "left_driver_joint"),
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "right_driver_joint"),
    }
    np.testing.assert_allclose(model.eq_data[equality_id, :5], [0.0, 1.0, 0.0, 0.0, 0.0])

    for pad_name in PAD_NAMES:
        pad_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, pad_name)
        assert int(model.geom_type[pad_id]) == int(mujoco.mjtGeom.mjGEOM_BOX)
        assert int(model.geom_contype[pad_id]) == 1
        assert int(model.geom_conaffinity[pad_id]) == 1
        body_name = mujoco.mj_id2name(
            model,
            mujoco.mjtObj.mjOBJ_BODY,
            int(model.geom_bodyid[pad_id]),
        )
        assert body_name in {"left_finger", "right_finger"}

    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "link_tcp")
    assert site_id >= 0
    assert mujoco.mj_id2name(
        model,
        mujoco.mjtObj.mjOBJ_BODY,
        int(model.site_bodyid[site_id]),
    ) == "xarm_gripper_base_link"
    np.testing.assert_allclose(model.site_pos[site_id], [0.0, 0.0, 0.172])

    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    np.testing.assert_allclose(model.key_qpos[home_id], HOME_QPOS, rtol=0.0, atol=1e-8)
    np.testing.assert_allclose(model.key_ctrl[home_id], HOME_CTRL, rtol=0.0, atol=1e-8)

    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    np.testing.assert_allclose(data.qpos, HOME_QPOS, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(data.ctrl, HOME_CTRL, rtol=0.0, atol=0.0)
    gripper_qpos_adrs = [
        int(model.jnt_qposadr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)])
        for name in GRIPPER_JOINT_NAMES
    ]
    qpos_before = data.qpos[gripper_qpos_adrs].copy()
    arm_ctrl_before = data.ctrl[:7].copy()
    data.ctrl[7] = 255.0
    assert data.ctrl[:7].tolist() == arm_ctrl_before.tolist()

    for _ in range(200):
        mujoco.mj_step(model, data)

    assert np.isclose(data.time, 200 * model.opt.timestep)
    assert data.ctrl[7] == 255.0
    assert data.ctrl[:7].tolist() == arm_ctrl_before.tolist()
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.qacc).all()
    assert np.isfinite(data.ctrl).all()
    assert np.max(np.abs(data.qpos[gripper_qpos_adrs] - qpos_before)) > 1e-3

    runnable_index = json.loads(
        (ROOT / "libraries" / "robots" / "index.json").read_text(encoding="utf-8")
    )
    assert "ufactory_xarm7" not in runnable_index["robots"]

    research_index = json.loads(
        (ROOT / "research" / "robots" / "index.json").read_text(encoding="utf-8")
    )
    candidate = next(
        item
        for item in research_index["candidates"]
        if item["robot_configuration_id"] == "ufactory_xarm7"
    )
    observed_paths = {
        item["path"] for item in candidate["locally_observed_source_material"]
    }
    assert "autoadapter/libraries/robots/ufactory_xarm7/1.0.0/assets/scene.xml" in observed_paths
    assert "autoadapter/libraries/robots/ufactory_xarm7/1.0.0/morphology.json" in observed_paths
    assert "autoadapter/libraries/robots/ufactory_xarm7/1.0.0/tasks/sources.json" in observed_paths
    assert "autoadapter/libraries/robots/ufactory_xarm7/1.0.0/tasks/catalog.json" in observed_paths
    assert "autoadapter/libraries/robots/ufactory_xarm7/1.0.0/assets/reach_scene.xml" in observed_paths
    assert (
        "autoadapter/libraries/robots/ufactory_xarm7/1.0.0/tasks/private/instances.json"
        in observed_paths
    )
    assert (
        "autoadapter/libraries/robots/ufactory_xarm7/1.0.0/skeleton/arm_serial_dls.py"
        in observed_paths
    )
    evidence_observation = next(
        item
        for item in candidate["locally_observed_source_material"]
        if item["kind"] == "tracked_reference_positive_control_record"
    )
    assert evidence_observation["path"] == "autoadapter/evidence/README.md"
    assert "20/20" in evidence_observation["observation"]
    assert "videos" in evidence_observation["observation"]
    assert "starts outside" in evidence_observation["observation"]
    missing = " ".join(candidate["missing_for_runnable_package"])
    assert "complete local MuJoCo asset closure" not in missing
    assert "current mainline morphology.json" not in missing
    assert "at least 20 distinct applicable source-backed tasks" not in missing
    assert "Create tasks/sources.json" not in missing
    assert "tasks/private/instances.json" not in missing
    assert "local task scenes" not in missing
    assert "skeleton" not in missing
    assert "reference driver" not in missing
    assert "package check" not in missing
    assert "positive control" not in missing
    assert "dynamic canary" in missing
    assert "runnable index" in missing

    assert sorted(path.name for path in PACKAGE_ROOT.iterdir()) == [
        "assets",
        "morphology.json",
        "reference",
        "skeleton",
        "tasks",
    ]
    tasks_root = PACKAGE_ROOT / "tasks"
    assert sorted(path.name for path in tasks_root.iterdir()) == [
        "catalog.json",
        "private",
        "sources.json",
    ]
    assert sorted(path.name for path in (tasks_root / "private").iterdir()) == [
        "bindings.json",
        "guards.json",
        "instances.json",
    ]
