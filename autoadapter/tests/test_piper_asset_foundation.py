from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "piper" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"

XML_ENTRYPOINTS = ("piper.xml", "scene.xml", "pickbench.xml", "pushbench.xml")
ARM_JOINT_NAMES = [
    "joint1",
    "joint2",
    "joint3",
    "joint4",
    "joint5",
    "joint6",
]
CONTROLLED_JOINT_NAMES = ARM_JOINT_NAMES + ["joint7"]
ACTUATOR_NAMES = ARM_JOINT_NAMES + ["gripper"]
ALL_JOINT_NAMES = CONTROLLED_JOINT_NAMES + ["joint8"]
ARM_JOINT_LIMITS = {
    "joint1": [-2.618, 2.618],
    "joint2": [0.0, 3.14],
    "joint3": [-2.697, 0.0],
    "joint4": [-1.832, 1.832],
    "joint5": [-1.22, 1.22],
    "joint6": [-3.14, 3.14],
}
HOME_QPOS = [0.0, 1.57, -1.3485, 0.0, 0.0, 0.0, 0.0, 0.0]
HOME_CTRL = [0.0, 1.57, -1.3485, 0.0, 0.0, 0.0, 0.0]
TOP_LEVEL_FILES = {
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "SOURCE.md",
    "piper.png",
    "piper.xml",
    "scene.xml",
    "pickbench.xml",
    "pushbench.xml",
    "reach_scene.xml",
    "push_to_goal_scene.xml",
    "drawer_scene.xml",
    "button_front_scene.xml",
    "button_topdown_scene.xml",
    "handle_vertical_scene.xml",
    "pick_place_scene.xml",
    "pick_place_wall_scene.xml",
    "wall_scene.xml",
    "sweep_into_goal_scene.xml",
    "door_scene.xml",
    "faucet_scene.xml",
    "dial_scene.xml",
    "lever_scene.xml",
    "peg_insertion_side_scene.xml",
    "bin_picking_scene.xml",
    "pick_out_of_hole_scene.xml",
}


def _names(model: mujoco.MjModel, obj: mujoco.mjtObj, count: int) -> list[str | None]:
    return [mujoco.mj_id2name(model, obj, index) for index in range(count)]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _assert_local_file(source: Path, reference: str, base: Path) -> None:
    assert "://" not in reference
    reference_path = Path(reference)
    assert not reference_path.is_absolute()
    resolved = (base / reference_path).resolve()
    assert _is_under(resolved, ASSETS_ROOT.resolve())
    assert resolved.is_file(), f"{source.name}: {reference}"


def _assert_xml_references_are_local(source: Path, root: ET.Element) -> None:
    compiler = root.find("compiler")
    meshdir = Path(compiler.attrib.get("meshdir", "")) if compiler is not None else Path()
    texturedir = (
        Path(compiler.attrib.get("texturedir", ""))
        if compiler is not None
        else Path()
    )
    for element in root.iter():
        reference = element.attrib.get("file")
        if reference is None:
            continue
        if element.tag == "mesh":
            base = source.parent / meshdir
        elif element.tag == "texture":
            base = source.parent / texturedir
        else:
            base = source.parent
        _assert_local_file(source, reference, base)


def _assert_finite(data: mujoco.MjData) -> None:
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.qacc).all()
    assert np.isfinite(data.ctrl).all()
    assert np.isfinite(data.time)


def test_canonical_closure_has_exact_shape_and_no_symlinks() -> None:
    assert ASSETS_ROOT.is_dir()
    entries = list(ASSETS_ROOT.rglob("*"))
    assert not any(entry.is_symlink() for entry in entries)

    files = sorted(
        path.relative_to(ASSETS_ROOT)
        for path in entries
        if path.is_file()
    )
    assert len(files) == 110
    assert {path.name for path in files if len(path.parts) == 1} == TOP_LEVEL_FILES

    mesh_files = [path for path in files if path.parts[0] == "assets"]
    assert len(mesh_files) == 84
    assert all(len(path.parts) == 2 for path in mesh_files)
    assert sum(path.suffix == ".obj" for path in mesh_files) == 72
    assert sum(path.suffix == ".stl" for path in mesh_files) == 12


def test_all_canonical_xml_files_parse_load_and_resolve_local_references() -> None:
    assert mujoco.__version__ == "3.3.6"
    for filename in XML_ENTRYPOINTS:
        path = ASSETS_ROOT / filename
        root = ET.parse(path).getroot()
        assert root.tag == "mujoco"
        _assert_xml_references_are_local(path, root)
        model = mujoco.MjModel.from_xml_path(str(path))
        assert model.nq > 0
        assert model.nv > 0


def test_morphology_records_exact_public_piper_facts() -> None:
    morphology = json.loads(MORPHOLOGY_PATH.read_text(encoding="utf-8"))

    assert morphology["schema_version"] == "1.0"
    assert morphology["robot_model_id"] == "piper"
    assert morphology["robot_configuration_id"] == "piper"
    assert morphology["package_version"] == "1.0.0"
    assert morphology["morphology_kind"] == "fixed_base_serial_manipulator"
    assert morphology["base_type"] == "fixed"
    assert morphology["degrees_of_freedom"] == {
        "arm": 6,
        "actuated_gripper": 1,
        "passive_mirrored_finger": 1,
    }
    assert morphology["mjcf_entrypoint"] == "assets/piper.xml"

    control = morphology["public_control"]
    assert control["actuation"] == "joint_position"
    assert control["transmission"] == "direct_joint"
    assert control["joint_names"] == CONTROLLED_JOINT_NAMES
    assert control["arm_joint_names"] == ARM_JOINT_NAMES
    assert control["arm_actuator_names"] == ARM_JOINT_NAMES
    assert control["actuator_names"] == ACTUATOR_NAMES
    assert control["actuator_joint_map"] == {
        **{name: name for name in ARM_JOINT_NAMES},
        "gripper": "joint7",
    }
    assert control["joint_types"] == {
        **{name: "hinge" for name in ARM_JOINT_NAMES},
        "joint7": "slide",
        "joint8": "slide",
    }
    assert control["arm_joint_limits_rad"] == ARM_JOINT_LIMITS
    assert control["actuator_ctrlrange_rad"] == ARM_JOINT_LIMITS
    assert control["arm_actuator_forcerange"] == {
        name: [-100.0, 100.0] for name in ARM_JOINT_NAMES
    }
    assert control["gripper_joint_name"] == "joint7"
    assert control["gripper_joint_range_m"] == [0.0, 0.035]
    assert control["gripper_ctrl_range_m"] == [0.0, 0.035]
    assert control["gripper_forcerange"] == [-10.0, 10.0]
    assert control["passive_joint_names"] == ["joint8"]
    assert control["passive_joint_range_m"] == [-0.035, 0.0]
    assert control["gripper_coupling"] == {
        "actuator_name": "gripper",
        "controlled_joint": "joint7",
        "passive_joint": "joint8",
        "relation": "joint8 = -joint7",
        "polycoef": [0.0, -1.0, 0.0, 0.0, 0.0],
    }
    assert control["gripper_direction"] == {
        "closed": 0.0,
        "open": 0.035,
        "unit": "m",
    }

    assert morphology["public_observations"] == {
        "end_effector_site": "ee_site",
        "end_effector_body": "link6",
        "contact_geoms": {
            "count": 4,
            "type": "box",
            "named": False,
            "contype": 1,
            "conaffinity": 1,
            "bodies": {"link7": 2, "link8": 2},
        },
    }
    assert morphology["public_affordances"] == {
        "actions": [
            "joint_position_control",
            "gripper_position_control",
        ],
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
        "home_qpos": HOME_QPOS,
        "home_ctrl": HOME_CTRL,
    }
    assert morphology["asset_provenance"] == {
        "model_title": "Piper MJCF model closure",
        "upstream": "https://github.com/981526092/auto-adapter/tree/585eb1f1fde33f17f5f9a1e169a18dd41f97b586/assets/mjcf/piper",
        "derived_revision": "585eb1f1fde33f17f5f9a1e169a18dd41f97b586",
        "license": "MIT",
        "license_file": "assets/LICENSE",
        "source_file": "assets/SOURCE.md",
    }


def test_piper_model_facts_and_actuator_driven_gripper_liveness() -> None:
    model = mujoco.MjModel.from_xml_path(str(ASSETS_ROOT / "piper.xml"))
    assert (model.nq, model.nv, model.nu, model.njnt, model.nsite, model.neq, model.nkey) == (
        8,
        8,
        7,
        8,
        1,
        1,
        1,
    )
    assert _names(model, mujoco.mjtObj.mjOBJ_JOINT, model.njnt) == ALL_JOINT_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu) == ACTUATOR_NAMES
    assert _names(model, mujoco.mjtObj.mjOBJ_SITE, model.nsite) == ["ee_site"]
    assert _names(model, mujoco.mjtObj.mjOBJ_KEY, model.nkey) == ["home"]
    base_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base_link")
    assert int(model.body_parentid[base_id]) == 0
    assert int(model.body_jntnum[base_id]) == 0

    for joint_name in ARM_JOINT_NAMES:
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_HINGE)
        assert bool(model.jnt_limited[joint_id])
        np.testing.assert_allclose(model.jnt_range[joint_id], ARM_JOINT_LIMITS[joint_name])

    for joint_name, expected_range in {
        "joint7": [0.0, 0.035],
        "joint8": [-0.035, 0.0],
    }.items():
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_SLIDE)
        assert bool(model.jnt_limited[joint_id])
        np.testing.assert_allclose(model.jnt_range[joint_id], expected_range)

    for actuator_index, joint_name in enumerate(ARM_JOINT_NAMES):
        actuator_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_ACTUATOR, joint_name
        )
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        assert int(model.actuator_trntype[actuator_id]) == int(mujoco.mjtTrn.mjTRN_JOINT)
        assert int(model.actuator_trnid[actuator_id, 0]) == joint_id
        np.testing.assert_allclose(
            model.actuator_ctrlrange[actuator_id], ARM_JOINT_LIMITS[joint_name]
        )
        np.testing.assert_allclose(model.actuator_forcerange[actuator_id], [-100.0, 100.0])
        assert actuator_index == actuator_id

    gripper_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, "gripper")
    joint7_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "joint7")
    assert int(model.actuator_trntype[gripper_id]) == int(mujoco.mjtTrn.mjTRN_JOINT)
    assert int(model.actuator_trnid[gripper_id, 0]) == joint7_id
    np.testing.assert_allclose(model.actuator_ctrlrange[gripper_id], [0.0, 0.035])
    np.testing.assert_allclose(model.actuator_forcerange[gripper_id], [-10.0, 10.0])

    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "ee_site")
    assert mujoco.mj_id2name(
        model, mujoco.mjtObj.mjOBJ_BODY, int(model.site_bodyid[site_id])
    ) == "link6"
    np.testing.assert_allclose(model.site_pos[site_id], [0.0, 0.0, 0.13])

    joint7_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "joint7")
    joint8_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "joint8")
    equality_id = 0
    assert int(model.eq_type[equality_id]) == int(mujoco.mjtEq.mjEQ_JOINT)
    assert int(model.eq_obj1id[equality_id]) == joint8_id
    assert int(model.eq_obj2id[equality_id]) == joint7_id
    np.testing.assert_allclose(
        model.eq_data[equality_id, :5], [0.0, -1.0, 0.0, 0.0, 0.0]
    )

    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    np.testing.assert_allclose(model.key_qpos[home_id], HOME_QPOS, rtol=0.0, atol=1e-8)
    np.testing.assert_allclose(model.key_ctrl[home_id], HOME_CTRL, rtol=0.0, atol=1e-8)

    contact_geom_ids = []
    for geom_id in range(model.ngeom):
        body_name = mujoco.mj_id2name(
            model, mujoco.mjtObj.mjOBJ_BODY, int(model.geom_bodyid[geom_id])
        )
        if body_name in {"link7", "link8"} and (
            model.geom_contype[geom_id] != 0 or model.geom_conaffinity[geom_id] != 0
        ):
            contact_geom_ids.append(geom_id)
    assert len(contact_geom_ids) == 4
    assert [
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(model.geom_bodyid[index]))
        for index in contact_geom_ids
    ].count("link7") == 2
    assert [
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(model.geom_bodyid[index]))
        for index in contact_geom_ids
    ].count("link8") == 2
    for geom_id in contact_geom_ids:
        assert _names(model, mujoco.mjtObj.mjOBJ_GEOM, model.ngeom)[geom_id] is None
        assert int(model.geom_type[geom_id]) == int(mujoco.mjtGeom.mjGEOM_BOX)
        assert int(model.geom_contype[geom_id]) == 1
        assert int(model.geom_conaffinity[geom_id]) == 1

    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    np.testing.assert_allclose(data.qpos, HOME_QPOS, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(data.ctrl, HOME_CTRL, rtol=0.0, atol=0.0)
    arm_ctrl = data.ctrl[:6].copy()
    joint7_qposadr = int(model.jnt_qposadr[joint7_id])
    joint8_qposadr = int(model.jnt_qposadr[joint8_id])

    data.ctrl[gripper_id] = 0.035
    np.testing.assert_allclose(data.ctrl[:6], arm_ctrl, rtol=0.0, atol=0.0)
    for _ in range(500):
        mujoco.mj_step(model, data)
    assert data.time == pytest.approx(500 * model.opt.timestep)
    assert data.qpos[joint7_qposadr] > 0.03
    assert data.qpos[joint8_qposadr] < -0.03
    assert np.max(np.abs(data.qpos[[joint7_qposadr, joint8_qposadr]])) > 1e-3
    np.testing.assert_allclose(
        data.qpos[[joint7_qposadr, joint8_qposadr]], [0.035, -0.035], atol=1e-4
    )
    np.testing.assert_allclose(data.ctrl[:6], arm_ctrl, rtol=0.0, atol=0.0)
    _assert_finite(data)

    data.ctrl[gripper_id] = 0.0
    np.testing.assert_allclose(data.ctrl[:6], arm_ctrl, rtol=0.0, atol=0.0)
    for _ in range(500):
        mujoco.mj_step(model, data)
    assert data.time == pytest.approx(1000 * model.opt.timestep)
    assert np.max(np.abs(data.qpos[[joint7_qposadr, joint8_qposadr]])) < 1e-3
    np.testing.assert_allclose(data.ctrl[:6], arm_ctrl, rtol=0.0, atol=0.0)
    _assert_finite(data)


def test_piper_is_non_runtime_and_package_has_no_reference() -> None:
    runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
    assert "piper" not in runnable_index["robots"]

    research_index = json.loads(RESEARCH_INDEX_PATH.read_text(encoding="utf-8"))
    candidate = next(
        item
        for item in research_index["candidates"]
        if item["robot_configuration_id"] == "piper"
    )
    observed_paths = {
        item["path"] for item in candidate["locally_observed_source_material"]
    }
    assert "autoadapter/libraries/robots/piper/1.0.0/assets/piper.xml" in observed_paths
    assert "autoadapter/libraries/robots/piper/1.0.0/morphology.json" in observed_paths
    assert "autoadapter/libraries/robots/piper/1.0.0/tasks/sources.json" in observed_paths
    assert "autoadapter/libraries/robots/piper/1.0.0/tasks/catalog.json" in observed_paths
    assert "autoadapter/libraries/robots/piper/1.0.0/assets/reach_scene.xml" in observed_paths
    scene_record = next(
        record
        for record in candidate["locally_observed_source_material"]
        if record["kind"] == "provisional_task_scenes"
    )
    assert "17 task scenes" in scene_record["observation"]
    assert "cannot push link1 or link2 at reset" in scene_record["observation"]
    assert "video-backed reference positive control" in scene_record["observation"]
    assert (
        "autoadapter/libraries/robots/piper/1.0.0/tasks/private/instances.json"
        in observed_paths
    )
    assert (
        "autoadapter/libraries/robots/piper/1.0.0/skeleton/arm_serial_dls.py"
        in observed_paths
    )
    assert (
        "autoadapter/libraries/robots/piper/1.0.0/reference/driver.py"
        in observed_paths
    )
    assert "autoadapter/evidence/README.md" in observed_paths
    missing = " ".join(candidate["missing_for_runnable_package"])
    assert "complete local MuJoCo asset closure" not in missing
    assert "current mainline morphology.json" not in missing
    assert "at least 20 distinct applicable source-backed tasks" not in missing
    assert "Create tasks/sources.json" not in missing
    assert "tasks/private/instances.json" not in missing
    assert "arm_serial_dls skeleton" not in missing
    assert "calibration-only reference driver" not in missing
    assert "positive control" not in missing
    assert "dynamic canary" in missing

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
    assert (tasks_root / "private").is_dir()
    assert (PACKAGE_ROOT / "skeleton" / "arm_serial_dls.py").is_file()
    assert (PACKAGE_ROOT / "reference" / "driver.py").is_file()
