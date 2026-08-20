from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import mujoco
import numpy as np
import pytest

from autoadapter2.libraries.robot_package import (
    RobotPackageError,
    _validate_asset_closure,
    _validate_sources,
    _validate_tasks,
    load_robot_package,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "hello_robot_stretch_2" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
SCENE_PATH = ASSETS_ROOT / "scene.xml"
SKELETON_PATH = PACKAGE_ROOT / "skeleton" / "stretch_control.py"
ROBOT_ID = "hello_robot_stretch_2"
SOURCE_REVISION = "da76818e269b82289eba39808e2fb91d679d6994"
ACTUATOR_NAMES = (
    "forward",
    "turn",
    "lift",
    "arm_extend",
    "wrist_yaw",
    "grip",
    "head_pan",
    "head_tilt",
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_skeleton():
    spec = importlib.util.spec_from_file_location("stretch_package_skeleton", SKELETON_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stretch_menagerie_asset_closure_and_morphology_are_consistent() -> None:
    morphology = _read(PACKAGE_ROOT / "morphology.json")
    assert morphology["robot_configuration_id"] == ROBOT_ID
    assert morphology["mjcf_entrypoint"] == "assets/scene.xml"
    assert morphology["asset_provenance"]["derived_revision"] == SOURCE_REVISION
    assert SOURCE_REVISION in (ASSETS_ROOT / "SOURCE.md").read_text(encoding="utf-8")
    assert "Clear BSD License" in (ASSETS_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert not any(path.is_symlink() for path in ASSETS_ROOT.rglob("*"))

    _validate_asset_closure(PACKAGE_ROOT, SCENE_PATH)
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert morphology["joint_coordinate_counts"] == {
        "robot_nq": 24,
        "robot_nv": 23,
        "canonical_scene_nq": 31,
        "canonical_scene_nv": 29,
    }
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
    assert (model.nq, model.nv, model.njnt, model.nu) == (31, 29, 19, 8)
    assert (model.ngeom, model.nsite, model.ntendon, model.neq, model.nkey) == (
        113,
        0,
        3,
        5,
        0,
    )
    assert tuple(
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, index)
        for index in range(model.nu)
    ) == ACTUATOR_NAMES


def test_stretch_public_task_candidate_is_closed_but_not_admitted() -> None:
    sources_path = PACKAGE_ROOT / "tasks" / "sources.json"
    catalog_path = PACKAGE_ROOT / "tasks" / "catalog.json"
    sources = _validate_sources(_read(sources_path), path=sources_path)
    tasks = _validate_tasks(
        _read(catalog_path),
        path=catalog_path,
        source_ids={str(source["source_id"]) for source in sources},
    )
    assert len(tasks) == 20
    assert len({task["task_id"] for task in tasks}) == 20
    assert {source["source_id"] for source in sources} == {
        "metaworld_repo",
        "mujoco_menagerie_stretch",
    }
    assert all(task["applicability"] and task["adaptation"] for task in tasks)
    assert all(
        reference["source_id"] == "metaworld_repo"
        for task in tasks
        for clause in task["scoring"]
        for reference in clause["source_refs"]
    )

    private_root = PACKAGE_ROOT / "tasks" / "private"
    for name in ("instances.json", "bindings.json", "guards.json"):
        assert not (private_root / name).exists()
    assert not (PACKAGE_ROOT / "reference").exists()
    runnable = _read(ROOT / "libraries" / "robots" / "index.json")["robots"]
    assert ROBOT_ID not in runnable
    with pytest.raises(RobotPackageError):
        load_robot_package(PACKAGE_ROOT)


def test_stretch_skeleton_is_task_neutral_and_steps_real_mujoco() -> None:
    source = SKELETON_PATH.read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in ("task_id", "scoring", "tasks/private", "mw_"):
        assert forbidden not in lowered
    assert "data.ctrl" in source
    assert "mujoco.mj_step" in source

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            assert not any(
                isinstance(child, ast.Attribute) and child.attr in {"qpos", "qvel"}
                for child in ast.walk(target)
            )

    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    skeleton = _load_skeleton().StretchControlSkeleton(model, data)
    skeleton.set_controls(
        {
            "forward": 0.2,
            "turn": 0.0,
            "lift": 0.1,
            "arm_extend": 0.1,
            "wrist_yaw": 0.2,
            "grip": 0.02,
            "head_pan": 0.1,
            "head_tilt": 0.1,
        }
    )
    skeleton.step(5)
    observed = skeleton.observe()
    assert observed["time"] > 0.0
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.ctrl).all()
    assert observed["actuator_controls"]["forward"] == 0.2
