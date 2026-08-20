from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import mujoco
import numpy as np
import pytest

from autoadapter2.harness.measurements import compare, measure
from autoadapter2.harness.session import TrackedMuJoCoSession, apply_framework_reset
from autoadapter2.libraries.robot_package import (
    RobotPackageError,
    _validate_asset_closure,
    _validate_private_inputs,
    _validate_sources,
    _validate_tasks,
    load_robot_package,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "hello_robot_stretch_2" / "1.0.0"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
TASKS_ROOT = PACKAGE_ROOT / "tasks"
PRIVATE_ROOT = TASKS_ROOT / "private"
SCENE_PATH = ASSETS_ROOT / "scene.xml"
SKELETON_PATH = PACKAGE_ROOT / "skeleton" / "stretch_control.py"
ROBOT_ID = "hello_robot_stretch_2"
PACKAGE_VERSION = "1.0.0"
SNAPSHOT_ID = "hello-robot-stretch-2-metaworld-source-protocols-2026-08-20-v1"
TASK_IDS = (
    "mw_reach_target",
    "mw_push_to_goal",
    "mw_pick_place",
    "mw_pick_place_wall",
    "mw_push_wall",
    "mw_sweep_into_goal",
    "mw_drawer_open",
    "mw_drawer_close",
    "mw_button_press",
    "mw_button_press_topdown",
    "mw_handle_press",
    "mw_handle_pull",
    "mw_door_open",
    "mw_door_close",
    "mw_faucet_open",
    "mw_dial_turn",
    "mw_lever_pull",
    "mw_peg_insertion_side",
    "mw_bin_picking",
    "mw_pick_out_of_hole",
)
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
ROBOT_BODY_NAMES = ("base_link", "link_lift", "link_gripper_slider")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validated_public() -> tuple[dict, tuple[dict, ...]]:
    sources_path = TASKS_ROOT / "sources.json"
    catalog_path = TASKS_ROOT / "catalog.json"
    source_document = _read(sources_path)
    catalog_document = _read(catalog_path)
    sources = _validate_sources(source_document, path=sources_path)
    tasks = _validate_tasks(
        catalog_document,
        path=catalog_path,
        source_ids={str(source["source_id"]) for source in sources},
    )
    return source_document, tasks


def _load_skeleton():
    spec = importlib.util.spec_from_file_location("stretch_package_skeleton", SKELETON_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stretch_public_snapshot_has_exactly_twenty_closed_source_backed_tasks() -> None:
    source_document, tasks = _validated_public()
    assert [task["task_id"] for task in tasks] == list(TASK_IDS)
    assert len({task["task_id"] for task in tasks}) == 20
    assert {source["source_id"] for source in source_document["sources"]} == {
        "metaworld_repo",
        "mujoco_menagerie_stretch",
    }

    metaworld = next(
        source for source in source_document["sources"] if source["source_id"] == "metaworld_repo"
    )
    pinned_commit = "7ea2b501c4a698c8533cdc55a396fe2734e2649d"
    assert pinned_commit in metaworld["locator"]
    assert pinned_commit in metaworld["version_or_date"]

    for task in tasks:
        assert "Stretch 2" in task["applicability"]
        assert task["adaptation"]
        assert task["scene_assumptions"]
        assert task["observation_assumptions"]
        assert len(task["scoring"]) == 1
        clause = task["scoring"][0]
        assert clause["source_refs"]
        assert all(ref["source_id"] == "metaworld_repo" for ref in clause["source_refs"])
        assert all(ref["support"] == "adapted" for ref in clause["source_refs"])
        assert "wrist_roll" not in json.dumps(task)

    public_text = json.dumps(
        {
            "sources": source_document,
            "catalog": _read(TASKS_ROOT / "catalog.json"),
        },
        sort_keys=True,
    ).lower()
    for forbidden in ("tasks/private", "binding_id", "guard_id", "instance_id", "reference_driver"):
        assert forbidden not in public_text


def test_stretch_asset_closure_and_morphology_compile_locally() -> None:
    morphology = _read(PACKAGE_ROOT / "morphology.json")
    assert morphology["robot_configuration_id"] == ROBOT_ID
    assert morphology["package_version"] == PACKAGE_VERSION
    assert morphology["mjcf_entrypoint"] == "assets/scene.xml"
    assert morphology["asset_provenance"]["derived_revision"] == (
        "da76818e269b82289eba39808e2fb91d679d6994"
    )
    assert not any(path.is_symlink() for path in ASSETS_ROOT.rglob("*"))
    assert "da76818e269b82289eba39808e2fb91d679d6994" in (
        ASSETS_ROOT / "SOURCE.md"
    ).read_text(encoding="utf-8")
    assert "Clear BSD License" in (ASSETS_ROOT / "LICENSE").read_text(encoding="utf-8")

    _validate_asset_closure(PACKAGE_ROOT, SCENE_PATH)
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert mujoco.__version__ == "3.3.6"
    assert (model.nq, model.nv, model.njnt, model.nu) == (39, 37, 27, 8)
    assert (model.ngeom, model.nsite, model.ntendon, model.neq, model.nkey) == (
        137,
        8,
        3,
        5,
        0,
    )
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
    assert [
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, index)
        for index in range(model.nu)
    ] == list(ACTUATOR_NAMES)
    for name in ROBOT_BODY_NAMES:
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name) >= 0
    for name in ("object", "drawer_handle", "front_button", "door_handle", "faucet_tip", "dial_tip", "lever_hinge", "peg_head"):
        object_type = mujoco.mjtObj.mjOBJ_JOINT if name == "lever_hinge" else mujoco.mjtObj.mjOBJ_BODY
        assert mujoco.mj_name2id(model, object_type, name) >= 0


def test_stretch_private_inputs_cover_each_public_task_once() -> None:
    _, tasks = _validated_public()
    _validate_private_inputs(
        package_root=PACKAGE_ROOT,
        private_dir=PRIVATE_ROOT,
        robot_configuration_id=ROBOT_ID,
        package_version=PACKAGE_VERSION,
        snapshot_id=SNAPSHOT_ID,
        tasks=tasks,
    )

    instances = _read(PRIVATE_ROOT / "instances.json")["instances"]
    bindings = _read(PRIVATE_ROOT / "bindings.json")["bindings"]
    guards = _read(PRIVATE_ROOT / "guards.json")["guards"]
    assert [instance["task_id"] for instance in instances] == list(TASK_IDS)
    assert len(instances) == len(bindings) == 20
    assert {binding["binding_id"] for binding in bindings} == {
        f"binding-{task_id}" for task_id in TASK_IDS
    }
    assert {guard["kind"] for guard in guards} == {
        "actuator_and_physics_step_required",
        "no_direct_state_write",
        "canonical_model_data",
    }
    assert all(instance["scene_entrypoint"] == "assets/scene.xml" for instance in instances)
    assert all(instance["repetitions"] == 1 for instance in instances)
    assert all(
        set(instance["guard_ids"])
        == {
            "guard_actuator_and_physics_step",
            "guard_no_direct_state_write",
            "guard_canonical_model_data",
        }
        for instance in instances
    )


def test_stretch_resets_are_finite_and_not_already_successful() -> None:
    _, tasks = _validated_public()
    tasks_by_id = {task["task_id"]: task for task in tasks}
    instances = _read(PRIVATE_ROOT / "instances.json")["instances"]
    bindings = {
        binding["binding_id"]: binding
        for binding in _read(PRIVATE_ROOT / "bindings.json")["bindings"]
    }
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)

    for instance in instances:
        apply_framework_reset(mujoco, model, data, instance["reset"])
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.ctrl).all()
        assert np.isfinite(data.xpos).all()
        session = TrackedMuJoCoSession(
            mujoco=mujoco,
            model=model,
            data=data,
            max_steps=1,
            max_sim_time_s=1.0,
            sample_hz=20.0,
        )
        sample = session.snapshot()
        task = tasks_by_id[instance["task_id"]]
        clause = task["scoring"][0]
        binding = bindings[instance["clause_bindings"][clause["clause_id"]]]
        value = measure(
            binding,
            evidence={"samples": [sample], "step_count": 0},
            public_arguments=instance["public_arguments"],
        )
        assert np.isfinite(value), instance["task_id"]
        assert not compare(
            value,
            comparator=clause["comparator"],
            threshold=clause["threshold"],
        ), f"reset already satisfies {instance['task_id']}"


def test_stretch_skeleton_is_task_neutral_and_has_real_control_liveness() -> None:
    source = SKELETON_PATH.read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in ("task_id", "scoring", "private/", "mw_"):
        assert forbidden not in lowered
    assert "data.ctrl" in source
    assert "mujoco.mj_step" in source

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if isinstance(node, ast.AugAssign):
            targets = [node.target]
        for target in targets:
            for child in ast.walk(target):
                assert not (
                    isinstance(child, ast.Attribute) and child.attr in {"qpos", "qvel"}
                ), "skeleton must not write generalized state"

    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)
    module = _load_skeleton()
    skeleton = module.StretchControlSkeleton(model, data)
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
    before = float(data.time)
    skeleton.step(5)
    observed = skeleton.observe()
    assert observed["time"] > before
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.ctrl).all()
    assert observed["actuator_controls"]["forward"] == 0.2
    assert set(observed["body_positions"]) == set(ROBOT_BODY_NAMES)


def test_stretch_package_stops_before_reference_driver_and_admission() -> None:
    assert not (PACKAGE_ROOT / "reference").exists()
    runnable_index = _read(ROOT / "libraries" / "robots" / "index.json")
    assert ROBOT_ID not in runnable_index["robots"]
    with pytest.raises(RobotPackageError, match="reference/driver.py"):
        load_robot_package(PACKAGE_ROOT)
