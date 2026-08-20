from __future__ import annotations

import json
import math
import shutil
import tempfile
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.harness.measurements import compare, measure
from autoadapter2.harness.session import TrackedMuJoCoSession, apply_framework_reset
from autoadapter2.libraries.robot_package import (
    _validate_private_inputs,
    _validate_sources,
    _validate_tasks,
    load_robot_package,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "leap_hand" / "1.0.0"
TASKS_ROOT = PACKAGE_ROOT / "tasks"
PRIVATE_ROOT = TASKS_ROOT / "private"
SCENE_PATH = PACKAGE_ROOT / "assets" / "leap_hand_task_scene.xml"

TASK_IDS = (
    "ec_pinch",
    "ec_dynamic_tripod",
    "ec_squeeze",
    "ec_twiddle",
    "ec_rock",
    "ec_rock_ii",
    "ec_radial_roll",
    "ec_index_roll",
    "ec_full_roll",
    "ec_rotary_step",
    "ec_interdigital_step",
    "ec_linear_step",
    "ec_palmar_slide",
    "gym_hand_reach_all_fingertips",
    "gym_hand_manipulate_block_full_pose",
    "robel_dclaw_pose_fixed",
    "robel_dclaw_turn_fixed",
    "robel_dclaw_screw_fixed",
    "myosuite_object_hold_fixed",
    "myosuite_baoding_p1",
)

CORE_GUARD_KINDS = {
    "actuator_and_physics_step_required",
    "no_direct_state_write",
    "canonical_model_data",
}
HAND_JOINTS = (
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
)
HAND_ACTUATORS = tuple(f"{name}_act" for name in HAND_JOINTS)
TASK_GEOMS = (
    "dexterity_object_geom",
    "block_object_geom",
    "fixture_handle_geom",
    "hold_object_geom",
    "baoding_ball_a_geom",
    "baoding_ball_b_geom",
)
TASK_SITES = (
    "reach_target_site",
    "dexterity_object_site",
    "block_target_site",
    "fixture_target_site",
    "hold_target_site",
    "baoding_ball_a_site",
    "baoding_ball_b_site",
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validated_public_tasks() -> tuple[dict, ...]:
    sources_path = TASKS_ROOT / "sources.json"
    catalog_path = TASKS_ROOT / "catalog.json"
    sources = _validate_sources(_read(sources_path), path=sources_path)
    return _validate_tasks(
        _read(catalog_path),
        path=catalog_path,
        source_ids={str(source["source_id"]) for source in sources},
    )


def _model_name_set(model: mujoco.MjModel, object_type: mujoco.mjtObj) -> set[str]:
    counts = {
        mujoco.mjtObj.mjOBJ_BODY: model.nbody,
        mujoco.mjtObj.mjOBJ_JOINT: model.njnt,
        mujoco.mjtObj.mjOBJ_ACTUATOR: model.nu,
        mujoco.mjtObj.mjOBJ_GEOM: model.ngeom,
        mujoco.mjtObj.mjOBJ_SITE: model.nsite,
    }
    return {
        name
        for index in range(counts[object_type])
        if (name := mujoco.mj_id2name(model, object_type, index)) is not None
    }


def test_leap_hand_private_records_pass_canonical_loader_contracts() -> None:
    tasks = _validated_public_tasks()
    instances_document = _read(PRIVATE_ROOT / "instances.json")
    bindings_document = _read(PRIVATE_ROOT / "bindings.json")
    guards_document = _read(PRIVATE_ROOT / "guards.json")
    instances = instances_document["instances"]
    bindings = {item["binding_id"]: item for item in bindings_document["bindings"]}
    guards = {item["guard_id"]: item for item in guards_document["guards"]}
    tasks_by_id = {task["task_id"]: task for task in tasks}

    assert tuple(task["task_id"] for task in tasks) == TASK_IDS
    assert len(instances) == len(TASK_IDS) == 20
    assert len(bindings) == 22
    assert {instance["task_id"] for instance in instances} == set(TASK_IDS)
    assert {instance["scene_entrypoint"] for instance in instances} == {
        "assets/leap_hand_task_scene.xml"
    }

    _validate_private_inputs(
        package_root=PACKAGE_ROOT,
        private_dir=PRIVATE_ROOT,
        robot_configuration_id="leap_hand",
        package_version="1.0.0",
        snapshot_id="leap-hand-public-source-contracts-2026-08-20-v1",
        tasks=tasks,
    )

    for instance in instances:
        task = tasks_by_id[instance["task_id"]]
        parameters = instance["public_arguments"]["request"]["task_parameters"]
        required = task["invocation_schema"]["request"]["task_parameters"]["required"]
        assert set(parameters) == set(required)
        clauses = {clause["clause_id"]: clause for clause in task["scoring"]}
        assert set(instance["clause_bindings"]) == set(clauses)
        assert set(instance["reset"]) == {"kind"}
        for clause_id, binding_id in instance["clause_bindings"].items():
            binding = bindings[binding_id]
            assert binding["metric"] == clauses[clause_id]["metric"]
            assert binding["unit"] == clauses[clause_id]["unit"]
        selected_kinds = {guards[guard_id]["kind"] for guard_id in instance["guard_ids"]}
        assert CORE_GUARD_KINDS <= selected_kinds
        assert "complete_video" in selected_kinds

    with tempfile.TemporaryDirectory(prefix="leap-hand-loader-") as temporary:
        staged_root = Path(temporary) / "leap_hand" / "1.0.0"
        shutil.copytree(PACKAGE_ROOT, staged_root)
        reference = staged_root / "reference"
        reference.mkdir()
        (reference / "driver.py").write_text("def build(model, data):\n    return None\n", encoding="utf-8")

        package = load_robot_package(staged_root)

    assert package.robot_configuration_id == "leap_hand"
    assert package.package_version == "1.0.0"
    assert package.snapshot_id == "leap-hand-public-source-contracts-2026-08-20-v1"
    assert len(package.tasks) == 20
    assert package.mjcf_path.name == "scene_right.xml"


def test_leap_hand_task_scene_resets_are_live_finite_and_not_full_passes() -> None:
    assert mujoco.__version__ == "3.3.6"
    instances = _read(PRIVATE_ROOT / "instances.json")["instances"]
    bindings = {
        item["binding_id"]: item for item in _read(PRIVATE_ROOT / "bindings.json")["bindings"]
    }
    tasks = {task["task_id"]: task for task in _validated_public_tasks()}
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))

    assert set(HAND_JOINTS) <= _model_name_set(model, mujoco.mjtObj.mjOBJ_JOINT)
    assert set(HAND_ACTUATORS) <= _model_name_set(model, mujoco.mjtObj.mjOBJ_ACTUATOR)
    assert set(TASK_GEOMS) <= _model_name_set(model, mujoco.mjtObj.mjOBJ_GEOM)
    assert set(TASK_SITES) <= _model_name_set(model, mujoco.mjtObj.mjOBJ_SITE)
    assert "fixture_hinge" in _model_name_set(model, mujoco.mjtObj.mjOBJ_JOINT)
    assert model.vis.global_.offwidth >= 800
    assert model.vis.global_.offheight >= 600

    for instance in instances:
        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, instance["reset"])
        assert data.ncon == 0, instance["task_id"]
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.ctrl).all()
        assert np.isfinite(data.xpos).all()
        assert np.isfinite(data.xquat).all()
        assert np.isfinite(data.site_xpos).all()

        session = TrackedMuJoCoSession(
            mujoco=mujoco,
            model=model,
            data=data,
            max_steps=1,
            max_sim_time_s=1.0,
            sample_hz=20.0,
        )
        sample = session.snapshot()
        evidence = {
            "samples": [sample],
            "step_count": 0,
            "contact_pair_step_counts": [],
        }
        clause_passes = []
        for clause in tasks[instance["task_id"]]["scoring"]:
            binding = bindings[instance["clause_bindings"][clause["clause_id"]]]
            value = measure(
                binding,
                evidence=evidence,
                public_arguments=instance["public_arguments"],
            )
            assert math.isfinite(value), instance["task_id"]
            clause_passes.append(
                compare(
                    value,
                    comparator=clause["comparator"],
                    threshold=clause["threshold"],
                )
            )
        assert not all(clause_passes), f"reset already passes {instance['task_id']}"

        start_time = float(data.time)
        mujoco.mj_step(model, data)
        assert float(data.time) > start_time
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.qacc).all()


def test_leap_hand_contact_guards_use_canonical_model_names() -> None:
    guards = {
        item["guard_id"]: item for item in _read(PRIVATE_ROOT / "guards.json")["guards"]
    }
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    geom_names = _model_name_set(model, mujoco.mjtObj.mjOBJ_GEOM)

    contact_guards = [
        guard
        for guard in guards.values()
        if guard["kind"] == "named_geom_contact_pair_required"
    ]
    assert contact_guards
    for guard in contact_guards:
        assert set(guard["robot_geom_names"]) <= geom_names
        assert set(guard["task_geom_names"]) <= geom_names
        assert guard["minimum_steps"] == 1
