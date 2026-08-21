from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.harness import run_private_suite
from autoadapter2.harness.session import apply_framework_reset
from autoadapter2.libraries import load_robot_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "leap_hand" / "1.0.0"
PRIVATE_ROOT = PACKAGE_ROOT / "tasks" / "private"
DRIVER_PATH = PACKAGE_ROOT / "reference" / "driver.py"
SNAPSHOT_ID = "leap-hand-public-source-contracts-2026-08-20-v1"

EC_TASK_IDS = (
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
)
CAPABILITY_GROUPS = (
    ("elementary_contact", "ec_task", EC_TASK_IDS),
    ("fingertip_reach", "reach_task", ("gym_hand_reach_all_fingertips",)),
    ("block_pose", "block_task", ("gym_hand_manipulate_block_full_pose",)),
    ("joint_pose", "pose_task", ("robel_dclaw_pose_fixed",)),
    (
        "fixture_motion",
        "fixture_task",
        ("robel_dclaw_turn_fixed", "robel_dclaw_screw_fixed"),
    ),
    ("object_hold", "hold_task", ("myosuite_object_hold_fixed",)),
    ("baoding", "baoding_task", ("myosuite_baoding_p1",)),
)
REQUIRED_GUARD_IDS = {
    "guard_actuator_and_physics_step",
    "guard_no_direct_state_write",
    "guard_canonical_model_data",
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _criterion(clause: dict) -> dict:
    return {
        key: clause[key]
        for key in (
            "metric",
            "unit",
            "comparator",
            "threshold",
            "temporal",
            "aggregation",
            "source_refs",
        )
    }


def _design(package: object) -> dict:
    tasks = {task["task_id"]: task for task in package.tasks}  # type: ignore[attr-defined]
    capabilities = []
    for capability_id, method_name, task_ids in CAPABILITY_GROUPS:
        source_task = tasks[task_ids[0]]
        source_clause = source_task["scoring"][0]
        capabilities.append(
            {
                "capability_id": capability_id,
                "method_name": method_name,
                "covered_task_ids": list(task_ids),
                "validation_contract": [
                    {
                        "case_role": "primary",
                        "selection_rationale": "Focused package-interface calibration check.",
                        "source_task_id": source_task["task_id"],
                        "source_clause_id": source_clause["clause_id"],
                        **_criterion(source_clause),
                    }
                ],
            }
        )
    return {"capabilities": capabilities}


def _reach_suite(package: object) -> dict:
    task_id = "gym_hand_reach_all_fingertips"
    task = next(task for task in package.tasks if task["task_id"] == task_id)  # type: ignore[attr-defined]
    clause = task["scoring"][0]
    instance = next(
        item
        for item in _read(package.private_dir / "instances.json")["instances"]  # type: ignore[attr-defined]
        if item["task_id"] == task_id
    )
    return {
        "cases": [
            {
                "case_id": "case-leap-fingertip-reach",
                "capability_id": "fingertip_reach",
                "method_name": "reach_task",
                "task_id": task_id,
                "source_clause_id": clause["clause_id"],
                "instance_id": instance["instance_id"],
                "binding_id": instance["clause_bindings"][clause["clause_id"]],
                "guard_ids": instance["guard_ids"],
                "repetitions": instance["repetitions"],
                "timeout_sim_s": instance["timeout_sim_s"],
                "criterion": _criterion(clause),
            }
        ]
    }


def _assert_collidable(model: mujoco.MjModel, geom_name: str) -> None:
    geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, geom_name)
    assert geom_id >= 0, geom_name
    assert int(model.geom_contype[geom_id]) != 0, geom_name
    assert int(model.geom_conaffinity[geom_id]) != 0, geom_name


def test_leap_private_package_covers_20_tasks_22_clauses_and_48_executions() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    instances_document = _read(PRIVATE_ROOT / "instances.json")
    bindings_document = _read(PRIVATE_ROOT / "bindings.json")
    guards_document = _read(PRIVATE_ROOT / "guards.json")
    instances = instances_document["instances"]
    bindings = {
        binding["binding_id"]: binding for binding in bindings_document["bindings"]
    }
    guards = {guard["guard_id"]: guard for guard in guards_document["guards"]}
    tasks = {task["task_id"]: task for task in package.tasks}

    assert package.robot_configuration_id == "leap_hand"
    assert package.package_version == "1.0.0"
    assert package.snapshot_id == SNAPSHOT_ID
    assert len(tasks) == len(instances) == 20
    assert len(bindings) == 22
    assert {instance["task_id"] for instance in instances} == set(tasks)
    assert len({instance["instance_id"] for instance in instances}) == 20
    assert {guard["kind"] for guard in guards.values()} == {
        "actuator_and_physics_step_required",
        "no_direct_state_write",
        "canonical_model_data",
        "complete_video",
    }

    for document in (instances_document, bindings_document, guards_document):
        assert document["robot_configuration_id"] == "leap_hand"
        assert document["package_version"] == "1.0.0"
        assert document["task_snapshot_id"] == SNAPSHOT_ID

    clause_count = 0
    execution_count = 0
    referenced_bindings: list[str] = []
    for instance in instances:
        task = tasks[instance["task_id"]]
        task_clauses = {
            clause["clause_id"]: clause for clause in task["scoring"]
        }
        assert set(instance["clause_bindings"]) == set(task_clauses)
        assert set(instance["guard_ids"]) == REQUIRED_GUARD_IDS
        assert instance["video_width"] == 800
        assert instance["video_height"] == 600
        assert instance["camera"] == "evidence"

        request = instance["public_arguments"]["request"]
        assert request["task_id"] == instance["task_id"]
        required_parameters = task["invocation_schema"]["request"][
            "task_parameters"
        ]["required"]
        assert set(request["task_parameters"]) == set(required_parameters)

        repetitions = instance["repetitions"]
        if instance["task_id"] in EC_TASK_IDS:
            assert repetitions == 3
            assert len(instance["repetition_variants"]) == 3
            assert all(
                variant["reset"] == instance["reset"]
                for variant in instance["repetition_variants"]
            )
        else:
            assert repetitions == 1
            assert "repetition_variants" not in instance

        for clause_id, binding_id in instance["clause_bindings"].items():
            binding = bindings[binding_id]
            clause = task_clauses[clause_id]
            assert binding["metric"] == clause["metric"]
            assert binding["unit"] == clause["unit"]
            referenced_bindings.append(binding_id)
        clause_count += len(instance["clause_bindings"])
        execution_count += repetitions * len(instance["clause_bindings"])

    assert clause_count == 22
    assert execution_count == 48
    assert len(referenced_bindings) == len(set(referenced_bindings)) == 22
    assert set(referenced_bindings) == set(bindings)


def test_leap_private_scenes_are_local_loadable_and_render_nonblank() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    instances = _read(package.private_dir / "instances.json")["instances"]
    representative_by_scene = {
        instance["scene_entrypoint"]: instance for instance in instances
    }
    assert len(representative_by_scene) == 12

    for scene_entrypoint, instance in representative_by_scene.items():
        scene_path = (package.root / scene_entrypoint).resolve()
        scene_path.relative_to((package.root / "assets").resolve())
        assert scene_path.is_file()
        model = mujoco.MjModel.from_xml_path(str(scene_path))
        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, instance["reset"])

        assert model.vis.global_.offwidth >= 800
        assert model.vis.global_.offheight >= 600
        assert mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_CAMERA, "evidence"
        ) >= 0
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.ctrl).all()

        renderer = mujoco.Renderer(model, height=600, width=800)
        try:
            renderer.update_scene(data, camera="evidence")
            frame = renderer.render()
        finally:
            renderer.close()
        assert frame.shape == (600, 800, 3)
        assert float(np.std(frame.astype(np.float32))) > 1.0, scene_entrypoint


def test_leap_task_objects_and_fixtures_are_movable_and_collidable() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    instances = _read(package.private_dir / "instances.json")["instances"]
    bindings = {
        binding["binding_id"]: binding
        for binding in _read(package.private_dir / "bindings.json")["bindings"]
    }

    for instance in instances:
        model = mujoco.MjModel.from_xml_path(
            str(package.root / instance["scene_entrypoint"])
        )
        for binding_id in instance["clause_bindings"].values():
            parameters = bindings[binding_id]["parameters"]
            for geom_name in parameters.get("object_geom_names", []):
                _assert_collidable(model, geom_name)
            for geom_group in parameters.get("required_robot_geom_groups", []):
                for geom_name in geom_group:
                    _assert_collidable(model, geom_name)

    cube_scenes = {
        instance["scene_entrypoint"]
        for instance in instances
        if instance["task_id"] in EC_TASK_IDS
        or instance["task_id"]
        in {"gym_hand_manipulate_block_full_pose", "myosuite_object_hold_fixed"}
    }
    for scene_entrypoint in cube_scenes:
        model = mujoco.MjModel.from_xml_path(str(package.root / scene_entrypoint))
        body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "cube")
        assert body_id >= 0
        joint_id = int(model.body_jntadr[body_id])
        assert int(model.body_jntnum[body_id]) >= 1
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_FREE)

    baoding = mujoco.MjModel.from_xml_path(
        str(package.root / "assets/leap_baoding_scene.xml")
    )
    for body_name, geom_name in (
        ("ball_1", "ball_1_geom"),
        ("ball_2", "ball_2_geom"),
    ):
        body_id = mujoco.mj_name2id(baoding, mujoco.mjtObj.mjOBJ_BODY, body_name)
        joint_id = int(baoding.body_jntadr[body_id])
        assert int(baoding.body_jntnum[body_id]) == 1
        assert int(baoding.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_FREE)
        _assert_collidable(baoding, geom_name)
    support_id = mujoco.mj_name2id(
        baoding, mujoco.mjtObj.mjOBJ_BODY, "baoding_palm_collision_skin"
    )
    assert int(baoding.body_jntnum[support_id]) == 0
    _assert_collidable(baoding, "baoding_palm_collision_skin_geom")

    for scene_name in ("leap_dclaw_fixture_scene.xml", "leap_dclaw_screw_scene.xml"):
        model = mujoco.MjModel.from_xml_path(str(package.root / "assets" / scene_name))
        joint_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_JOINT, "fixture_hinge"
        )
        assert joint_id >= 0
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_HINGE)
        assert not bool(model.jnt_limited[joint_id])
        for geom_name in (
            "fixture_hub",
            "fixture_lobe_0",
            "fixture_lobe_1",
            "fixture_lobe_2",
        ):
            _assert_collidable(model, geom_name)

    cube_model = mujoco.MjModel.from_xml_path(
        str(package.root / "assets/leap_reference_cube_scene.xml")
    )
    goal_id = mujoco.mj_name2id(
        cube_model, mujoco.mjtObj.mjOBJ_GEOM, "goal_marker"
    )
    assert int(cube_model.geom_contype[goal_id]) == 0
    assert int(cube_model.geom_conaffinity[goal_id]) == 0


def test_leap_skeleton_and_reference_driver_interfaces_are_present() -> None:
    assert (PACKAGE_ROOT / "skeleton" / "hand_joint_position.py").is_file()
    assert (PACKAGE_ROOT / "skeleton" / "leap_cube_reorientation.py").is_file()
    source = DRIVER_PATH.read_text(encoding="utf-8")
    methods = tuple(group[1] for group in CAPABILITY_GROUPS)
    audit = audit_driver_source(
        source,
        condition="skeleton-assisted",
        capability_methods=methods,
    )
    assert audit.imports_trusted_skeleton
    assert audit.ctrl_references > 0
    assert audit.physics_step_references > 0

    spec = importlib.util.spec_from_file_location("leap_reference_driver", DRIVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    model = mujoco.MjModel.from_xml_path(
        str(PACKAGE_ROOT / "assets" / "leap_hand_kinematic_scene.xml")
    )
    data = mujoco.MjData(model)
    driver = module.build(model=model, data=data)
    assert all(callable(getattr(driver, method)) for method in methods)


def test_leap_focused_harness_route_exercises_physics_and_guards() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    with tempfile.TemporaryDirectory(prefix="leap-harness-smoke-") as output_dir:
        report = run_private_suite(
            package=package,
            design=_design(package),
            suite=_reach_suite(package),
            driver_path=DRIVER_PATH,
            condition="skeleton-assisted",
            output_dir=output_dir,
            record_video=False,
            wall_timeout_s=30.0,
            run_id="leap-fingertip-reach-diagnostic",
            attempt=0,
        )

    assert report["pipeline_completed"] is True
    assert report["physical_validation_executed"] is True
    assert report["source_audit"]["imports_trusted_skeleton"] is True
    assert len(report["trials"]) == 1
    trial = report["trials"][0]
    assert trial["physical_execution_passed"] is True
    assert trial["physical_evidence"]["step_count"] > 0
    assert trial["physical_evidence"]["ctrl_observed_before_step"] is True
    assert trial["physical_evidence"]["direct_state_write_detected"] is False
    assert all(trial["guard_outcomes"].values())
