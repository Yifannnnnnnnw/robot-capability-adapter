from __future__ import annotations

import json
import importlib.util
import math
import tempfile
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.harness import run_private_suite
from autoadapter2.harness.measurements import compare, measure
from autoadapter2.harness.session import TrackedMuJoCoSession, apply_framework_reset
from autoadapter2.libraries import load_robot_package
from autoadapter2.validation_compiler import validate_private_suite


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "robotstudio_so101" / "1.0.0"


def _design(package):
    groups = [
        ("reach", "reach_task", ["mw_reach_target"]),
        (
            "contact",
            "contact_task",
            ["mw_push_to_goal", "mw_push_wall", "mw_sweep_into_goal"],
        ),
        (
            "object",
            "object_task",
            [
                "mw_pick_place",
                "mw_pick_place_wall",
                "mw_peg_insertion_side",
                "mw_bin_picking",
                "mw_pick_out_of_hole",
            ],
        ),
        (
            "fixture",
            "fixture_task",
            [
                "mw_drawer_open",
                "mw_drawer_close",
                "mw_button_press",
                "mw_button_press_topdown",
                "mw_handle_press",
                "mw_handle_pull",
                "mw_door_open",
                "mw_door_close",
            ],
        ),
        (
            "rotation",
            "rotation_task",
            ["mw_faucet_open", "mw_dial_turn", "mw_lever_pull"],
        ),
    ]
    by_id = {task["task_id"]: task for task in package.tasks}
    capabilities = []
    for capability_id, method_name, task_ids in groups:
        clauses = []
        for task_id in task_ids:
            clause = by_id[task_id]["scoring"][0]
            clauses.append(
                {
                    "source_task_id": task_id,
                    "source_clause_id": clause["clause_id"],
                    **{
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
                    },
                }
            )
        capabilities.append(
            {
                "capability_id": capability_id,
                "method_name": method_name,
                "covered_task_ids": task_ids,
                "validation_contract": clauses,
            }
        )
    return {"capabilities": capabilities}


def _suite(package, design):
    instances = json.loads(
        (package.private_dir / "instances.json").read_text(encoding="utf-8")
    )["instances"]
    instance_by_task = {item["task_id"]: item for item in instances}
    task_to_capability = {
        task_id: capability
        for capability in design["capabilities"]
        for task_id in capability["covered_task_ids"]
    }
    cases = []
    for task in package.tasks:
        capability = task_to_capability[task["task_id"]]
        source = next(
            clause
            for clause in capability["validation_contract"]
            if clause["source_task_id"] == task["task_id"]
        )
        instance = instance_by_task[task["task_id"]]
        clause_id = source["source_clause_id"]
        binding_id = instance["clause_bindings"][clause_id]
        cases.append(
            {
                "case_id": f"case-{task['task_id']}",
                "capability_id": capability["capability_id"],
                "method_name": capability["method_name"],
                "task_id": task["task_id"],
                "source_clause_id": clause_id,
                "instance_id": instance["instance_id"],
                "binding_id": binding_id,
                "guard_ids": instance["guard_ids"],
                "repetitions": instance["repetitions"],
                "timeout_sim_s": instance["timeout_sim_s"],
                "criterion": {
                    key: source[key]
                    for key in (
                        "metric",
                        "unit",
                        "comparator",
                        "threshold",
                        "temporal",
                        "aggregation",
                        "source_refs",
                    )
                },
            }
        )
    return {
        "artifact_type": "private_validation_suite",
        "schema_version": "1.0",
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": cases,
    }


def test_so101_package_load_and_request_abi() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    assert len(package.tasks) >= 20
    source_ids = {source["source_id"] for source in package.sources}
    assert source_ids == {"metaworld_repo"}
    for source in package.sources:
        assert "main" not in source["locator"].lower()
        assert "master" not in source["locator"].lower()
    for task in package.tasks:
        schema = task["invocation_schema"]
        assert schema["required"] == ["request"]
        assert schema["request"]["task_id"] == task["task_id"]
        assert schema["request"]["required"] == ["task_id", "task_parameters"]

    instances = json.loads(
        (package.private_dir / "instances.json").read_text(encoding="utf-8")
    )["instances"]
    assert len(instances) == len(package.tasks)
    for instance in instances:
        arguments = instance["public_arguments"]
        assert set(arguments) == {"request"}
        assert arguments["request"]["task_id"] == instance["task_id"]
        assert set(arguments["request"]["task_parameters"]) >= {"target_position"}
        assert instance["video_width"] >= 640
        assert instance["video_height"] >= 480


def test_so101_reference_source_and_ivc_contract() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    source = package.reference_driver.read_text(encoding="utf-8")
    design = _design(package)
    audit = audit_driver_source(
        source,
        condition="from-scratch",
        capability_methods=tuple(
            capability["method_name"] for capability in design["capabilities"]
        ),
    )
    assert audit.ctrl_references > 0
    assert audit.physics_step_references > 0
    assert not audit.imports_trusted_skeleton

    suite = _suite(package, design)
    checked = validate_private_suite(suite, package=package, design=design)
    assert len(checked["cases"]) == len(package.tasks)


def test_so101_reference_idle_holds_the_last_actuator_target() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    model = mujoco.MjModel.from_xml_path(str(package.mjcf_path))
    data = mujoco.MjData(model)
    spec = importlib.util.spec_from_file_location(
        "so101_reference_hold",
        package.reference_driver,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    driver = module.build(model=model, data=data)

    driver._step_to(np.asarray([0.42, 0.0, 0.22]), residual_tolerance=0.08)
    before = driver._ee_position()
    driver._idle(300)
    after = driver._ee_position()

    assert np.linalg.norm(after - before) <= 0.015


def test_so101_pick_place_uses_a_physical_fixture_and_reference_passes() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    complete_suite = _suite(package, design)
    pick_place_case = next(
        case
        for case in complete_suite["cases"]
        if case["task_id"] == "mw_pick_place"
    )
    suite = {**complete_suite, "cases": [pick_place_case]}
    instance = next(
        item
        for item in json.loads(
            (package.private_dir / "instances.json").read_text(encoding="utf-8")
        )["instances"]
        if item["task_id"] == "mw_pick_place"
    )
    model = mujoco.MjModel.from_xml_path(
        str((package.root / instance["scene_entrypoint"]).resolve())
    )
    workpiece_body = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_BODY, "workpiece"
    )
    workpiece_geom = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_GEOM, "workpiece_geom"
    )
    assert workpiece_body >= 0
    assert model.body_dofnum[workpiece_body] == 6
    assert workpiece_geom >= 0
    assert model.geom_contype[workpiece_geom] != 0
    assert model.geom_type[workpiece_geom] == mujoco.mjtGeom.mjGEOM_CYLINDER
    assert model.body_mass[workpiece_body] == 0.10

    with tempfile.TemporaryDirectory(prefix="so101-pick-place-") as temporary:
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=package.reference_driver,
            condition="from-scratch",
            output_dir=temporary,
            record_video=False,
            wall_timeout_s=30.0,
            run_id="so101-pick-place-calibration",
            attempt=0,
        )

    assert report["validation_passed"]
    trial = report["trials"][0]
    assert trial["trial_passed"]
    assert trial["measurement_value"] <= 0.07
    assert trial["physical_evidence"]["ctrl_observed_before_step"]
    assert not trial["physical_evidence"]["direct_state_write_detected"]


def test_so101_wall_tasks_use_physical_obstacles_and_reference_passes() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    complete_suite = _suite(package, design)
    task_ids = {"mw_pick_place_wall", "mw_push_wall"}
    suite = {
        **complete_suite,
        "cases": [
            case for case in complete_suite["cases"] if case["task_id"] in task_ids
        ],
    }
    instances = {
        item["task_id"]: item
        for item in json.loads(
            (package.private_dir / "instances.json").read_text(encoding="utf-8")
        )["instances"]
        if item["task_id"] in task_ids
    }
    assert instances["mw_pick_place_wall"]["scene_entrypoint"] != instances[
        "mw_push_wall"
    ]["scene_entrypoint"]

    for instance in instances.values():
        model = mujoco.MjModel.from_xml_path(
            str((package.root / instance["scene_entrypoint"]).resolve())
        )
        wall_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "wall")
        workpiece_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, "workpiece"
        )
        assert wall_id >= 0
        assert model.geom_contype[wall_id] != 0
        assert workpiece_id >= 0
        assert model.body_dofnum[workpiece_id] == 6

    pick_model = mujoco.MjModel.from_xml_path(
        str(
            (
                package.root
                / instances["mw_pick_place_wall"]["scene_entrypoint"]
            ).resolve()
        )
    )
    pick_wall_id = mujoco.mj_name2id(
        pick_model, mujoco.mjtObj.mjOBJ_GEOM, "wall"
    )
    pick_workpiece_id = mujoco.mj_name2id(
        pick_model, mujoco.mjtObj.mjOBJ_GEOM, "workpiece_geom"
    )
    assert pick_model.geom_type[pick_workpiece_id] == mujoco.mjtGeom.mjGEOM_CYLINDER
    wall_top = pick_model.geom_pos[pick_wall_id, 2] + pick_model.geom_size[
        pick_wall_id, 2
    ]
    pick_route = instances["mw_pick_place_wall"]["public_arguments"]["request"][
        "task_parameters"
    ]["route_position"]
    assert pick_route[2] > wall_top

    with tempfile.TemporaryDirectory(prefix="so101-wall-scenes-") as temporary:
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=package.reference_driver,
            condition="from-scratch",
            output_dir=temporary,
            record_video=False,
            wall_timeout_s=60.0,
            run_id="so101-wall-calibration",
            attempt=0,
        )

    assert report["validation_passed"]
    assert {trial["task_id"] for trial in report["trials"]} == task_ids
    assert all(trial["trial_passed"] for trial in report["trials"])


def test_so101_peg_bin_and_hole_fixtures_match_source_metrics() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    complete_suite = _suite(package, design)
    task_ids = {
        "mw_peg_insertion_side",
        "mw_bin_picking",
        "mw_pick_out_of_hole",
    }
    suite = {
        **complete_suite,
        "cases": [
            case for case in complete_suite["cases"] if case["task_id"] in task_ids
        ],
    }
    instances = {
        item["task_id"]: item
        for item in json.loads(
            (package.private_dir / "instances.json").read_text(encoding="utf-8")
        )["instances"]
        if item["task_id"] in task_ids
    }
    assert len({item["scene_entrypoint"] for item in instances.values()}) == 3
    assert instances["mw_pick_out_of_hole"]["reset"] == {"kind": "default"}

    binding_by_id = {
        item["binding_id"]: item
        for item in json.loads(
            (package.private_dir / "bindings.json").read_text(encoding="utf-8")
        )["bindings"]
    }
    peg_binding = binding_by_id["binding-mw_peg_insertion_side"]
    assert peg_binding["kind"] == "final_weighted_site_position_error"
    assert peg_binding["parameters"]["site_name"] == "peg_head_site"
    assert peg_binding["parameters"]["weights"] == [1.0, 2.0, 2.0]

    peg_model = mujoco.MjModel.from_xml_path(
        str(
            (
                package.root
                / instances["mw_peg_insertion_side"]["scene_entrypoint"]
            ).resolve()
        )
    )
    assert mujoco.mj_name2id(
        peg_model, mujoco.mjtObj.mjOBJ_SITE, "peg_head_site"
    ) >= 0
    for name in ("hole_left", "hole_right", "hole_bottom", "hole_top"):
        geom_id = mujoco.mj_name2id(peg_model, mujoco.mjtObj.mjOBJ_GEOM, name)
        assert geom_id >= 0
        assert peg_model.geom_contype[geom_id] != 0

    bin_model = mujoco.MjModel.from_xml_path(
        str((package.root / instances["mw_bin_picking"]["scene_entrypoint"]).resolve())
    )
    for name in ("bin_bottom", "bin_front", "bin_back", "goal_bin_bottom", "goal_bin_back"):
        geom_id = mujoco.mj_name2id(bin_model, mujoco.mjtObj.mjOBJ_GEOM, name)
        assert geom_id >= 0
        assert bin_model.geom_contype[geom_id] != 0
    bin_workpiece_body = mujoco.mj_name2id(
        bin_model, mujoco.mjtObj.mjOBJ_BODY, "workpiece"
    )
    bin_workpiece_geom = mujoco.mj_name2id(
        bin_model, mujoco.mjtObj.mjOBJ_GEOM, "workpiece_geom"
    )
    assert bin_model.geom_type[bin_workpiece_geom] == mujoco.mjtGeom.mjGEOM_BOX
    np.testing.assert_allclose(bin_model.geom_size[bin_workpiece_geom], [0.02] * 3)
    assert bin_model.body_mass[bin_workpiece_body] == 0.10

    hole_model = mujoco.MjModel.from_xml_path(
        str(
            (
                package.root
                / instances["mw_pick_out_of_hole"]["scene_entrypoint"]
            ).resolve()
        )
    )
    for name in ("hole_platform_left", "hole_platform_right"):
        geom_id = mujoco.mj_name2id(hole_model, mujoco.mjtObj.mjOBJ_GEOM, name)
        assert geom_id >= 0
        assert hole_model.geom_contype[geom_id] != 0
    hole_workpiece_body = mujoco.mj_name2id(
        hole_model, mujoco.mjtObj.mjOBJ_BODY, "workpiece"
    )
    hole_workpiece_geom = mujoco.mj_name2id(
        hole_model, mujoco.mjtObj.mjOBJ_GEOM, "workpiece_geom"
    )
    assert hole_model.geom_type[hole_workpiece_geom] == mujoco.mjtGeom.mjGEOM_CYLINDER
    np.testing.assert_allclose(hole_model.geom_size[hole_workpiece_geom, :2], [0.02, 0.02])
    assert hole_model.body_mass[hole_workpiece_body] == 0.01
    left_id = mujoco.mj_name2id(
        hole_model, mujoco.mjtObj.mjOBJ_GEOM, "hole_platform_left"
    )
    right_id = mujoco.mj_name2id(
        hole_model, mujoco.mjtObj.mjOBJ_GEOM, "hole_platform_right"
    )
    opening_width = (
        hole_model.geom_pos[right_id, 0]
        - hole_model.geom_size[right_id, 0]
        - hole_model.geom_pos[left_id, 0]
        - hole_model.geom_size[left_id, 0]
    )
    assert opening_width >= 0.16 - 1e-9

    with tempfile.TemporaryDirectory(prefix="so101-object-fixtures-") as temporary:
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=package.reference_driver,
            condition="from-scratch",
            output_dir=temporary,
            record_video=False,
            wall_timeout_s=90.0,
            run_id="so101-object-fixture-calibration",
            attempt=0,
        )

    assert report["validation_passed"]
    assert {trial["task_id"] for trial in report["trials"]} == task_ids
    assert all(trial["trial_passed"] for trial in report["trials"])


def test_so101_reach_push_and_sweep_use_distinct_physical_scenes() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    complete_suite = _suite(package, design)
    task_ids = {"mw_reach_target", "mw_push_to_goal", "mw_sweep_into_goal"}
    suite = {
        **complete_suite,
        "cases": [
            case for case in complete_suite["cases"] if case["task_id"] in task_ids
        ],
    }
    instances = {
        item["task_id"]: item
        for item in json.loads(
            (package.private_dir / "instances.json").read_text(encoding="utf-8")
        )["instances"]
        if item["task_id"] in task_ids
    }
    assert len({item["scene_entrypoint"] for item in instances.values()}) == 3

    push_model = mujoco.MjModel.from_xml_path(
        str((package.root / instances["mw_push_to_goal"]["scene_entrypoint"]).resolve())
    )
    sweep_model = mujoco.MjModel.from_xml_path(
        str((package.root / instances["mw_sweep_into_goal"]["scene_entrypoint"]).resolve())
    )
    assert mujoco.mj_name2id(
        push_model, mujoco.mjtObj.mjOBJ_GEOM, "workpiece_geom"
    ) >= 0
    assert mujoco.mj_name2id(
        sweep_model, mujoco.mjtObj.mjOBJ_GEOM, "goal_catch"
    ) >= 0
    assert mujoco.mj_name2id(
        sweep_model, mujoco.mjtObj.mjOBJ_GEOM, "table_front"
    ) >= 0

    with tempfile.TemporaryDirectory(prefix="so101-contact-scenes-") as temporary:
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=package.reference_driver,
            condition="from-scratch",
            output_dir=temporary,
            record_video=False,
            wall_timeout_s=60.0,
            run_id="so101-contact-scene-calibration",
            attempt=0,
        )

    assert report["validation_passed"]
    assert {trial["task_id"] for trial in report["trials"]} == task_ids
    assert all(trial["trial_passed"] for trial in report["trials"])


def test_so101_drawer_button_and_handle_fixtures_match_source_axes() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    complete_suite = _suite(package, design)
    task_ids = {
        "mw_drawer_open",
        "mw_drawer_close",
        "mw_button_press",
        "mw_button_press_topdown",
        "mw_handle_press",
        "mw_handle_pull",
    }
    suite = {
        **complete_suite,
        "cases": [
            case for case in complete_suite["cases"] if case["task_id"] in task_ids
        ],
    }
    instances = {
        item["task_id"]: item
        for item in json.loads(
            (package.private_dir / "instances.json").read_text(encoding="utf-8")
        )["instances"]
        if item["task_id"] in task_ids
    }
    assert instances["mw_drawer_open"]["scene_entrypoint"] == instances[
        "mw_drawer_close"
    ]["scene_entrypoint"]
    assert instances["mw_drawer_open"]["reset"] == {"kind": "default"}
    assert instances["mw_drawer_close"]["reset"]["joint_positions"] == {
        "drawer_slide": -0.08
    }
    assert instances["mw_handle_pull"]["reset"]["joint_positions"] == {
        "vertical_handle_slide": -0.05
    }

    binding_by_id = {
        item["binding_id"]: item
        for item in json.loads(
            (package.private_dir / "bindings.json").read_text(encoding="utf-8")
        )["bindings"]
    }
    expected_axes = {
        "binding-mw_button_press": 1,
        "binding-mw_button_press_topdown": 2,
        "binding-mw_handle_press": 2,
        "binding-mw_handle_pull": 2,
    }
    for binding_id, axis in expected_axes.items():
        binding = binding_by_id[binding_id]
        assert binding["kind"] == "final_site_axis_error"
        assert binding["parameters"]["axis"] == axis

    with tempfile.TemporaryDirectory(prefix="so101-fixture-scenes-") as temporary:
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=package.reference_driver,
            condition="from-scratch",
            output_dir=temporary,
            record_video=False,
            wall_timeout_s=120.0,
            run_id="so101-fixture-calibration",
            attempt=0,
        )

    assert report["validation_passed"]
    assert {trial["task_id"] for trial in report["trials"]} == task_ids
    assert all(trial["trial_passed"] for trial in report["trials"])


def test_so101_door_and_rotary_fixtures_match_source_formulas() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    complete_suite = _suite(package, design)
    task_ids = {
        "mw_door_open",
        "mw_door_close",
        "mw_faucet_open",
        "mw_dial_turn",
        "mw_lever_pull",
    }
    suite = {
        **complete_suite,
        "cases": [
            case for case in complete_suite["cases"] if case["task_id"] in task_ids
        ],
    }
    instances = {
        item["task_id"]: item
        for item in json.loads(
            (package.private_dir / "instances.json").read_text(encoding="utf-8")
        )["instances"]
        if item["task_id"] in task_ids
    }
    assert instances["mw_door_open"]["scene_entrypoint"] == instances[
        "mw_door_close"
    ]["scene_entrypoint"]
    assert instances["mw_door_open"]["reset"] == {"kind": "default"}
    assert instances["mw_door_close"]["reset"]["joint_positions"] == {
        "door_hinge": 1.2
    }
    assert len({item["scene_entrypoint"] for item in instances.values()}) == 4

    binding_by_id = {
        item["binding_id"]: item
        for item in json.loads(
            (package.private_dir / "bindings.json").read_text(encoding="utf-8")
        )["bindings"]
    }
    door_open = binding_by_id["binding-mw_door_open"]
    assert door_open["kind"] == "final_site_axis_error"
    assert door_open["metric"] == "door_x_axis_error"
    assert door_open["parameters"]["axis"] == 0
    for binding_id in (
        "binding-mw_door_close",
        "binding-mw_faucet_open",
        "binding-mw_dial_turn",
    ):
        assert binding_by_id[binding_id]["kind"] == "final_site_position_error"
    lever = binding_by_id["binding-mw_lever_pull"]
    assert lever["kind"] == "final_joint_position_error"
    assert lever["parameters"]["joint_name"] == "lever_hinge"
    lever_request = instances["mw_lever_pull"]["public_arguments"]["request"]
    assert lever_request["task_parameters"]["target_angle"] == math.pi / 2.0

    expected_fixture_objects = {
        "mw_door_open": (mujoco.mjtObj.mjOBJ_SITE, "door_handle_site"),
        "mw_faucet_open": (mujoco.mjtObj.mjOBJ_SITE, "faucet_tip_site"),
        "mw_dial_turn": (mujoco.mjtObj.mjOBJ_SITE, "dial_tip_site"),
        "mw_lever_pull": (mujoco.mjtObj.mjOBJ_JOINT, "lever_hinge"),
    }
    for task_id, (object_type, name) in expected_fixture_objects.items():
        model = mujoco.MjModel.from_xml_path(
            str((package.root / instances[task_id]["scene_entrypoint"]).resolve())
        )
        assert mujoco.mj_name2id(model, object_type, name) >= 0
        if task_id == "mw_lever_pull":
            joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            assert model.jnt_range[joint_id, 0] == 0.0
            assert model.jnt_range[joint_id, 1] >= math.pi / 2.0 - 0.001

    with tempfile.TemporaryDirectory(prefix="so101-rotary-scenes-") as temporary:
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=package.reference_driver,
            condition="from-scratch",
            output_dir=temporary,
            record_video=False,
            wall_timeout_s=90.0,
            run_id="so101-rotary-calibration",
            attempt=0,
        )

    assert report["validation_passed"]
    assert {trial["task_id"] for trial in report["trials"]} == task_ids
    assert all(trial["trial_passed"] for trial in report["trials"])


def test_so101_private_reset_fails_every_task_criterion() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    instances = json.loads(
        (package.private_dir / "instances.json").read_text(encoding="utf-8")
    )["instances"]
    bindings = json.loads(
        (package.private_dir / "bindings.json").read_text(encoding="utf-8")
    )["bindings"]
    binding_by_id = {binding["binding_id"]: binding for binding in bindings}
    task_by_id = {task["task_id"]: task for task in package.tasks}

    for instance in instances:
        scene = (package.root / instance["scene_entrypoint"]).resolve()
        model = mujoco.MjModel.from_xml_path(str(scene))
        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, instance["reset"])
        tracker = TrackedMuJoCoSession(
            mujoco=mujoco,
            model=model,
            data=data,
            max_steps=1,
            max_sim_time_s=1.0,
            sample_hz=20.0,
        )
        clause_id, binding_id = next(iter(instance["clause_bindings"].items()))
        binding = binding_by_id[binding_id]
        criterion = next(
            clause
            for clause in task_by_id[instance["task_id"]]["scoring"]
            if clause["clause_id"] == clause_id
        )
        value = measure(
            binding,
            evidence={"samples": [tracker.snapshot()], "step_count": 0},
            public_arguments=instance["public_arguments"],
        )
        assert not compare(
            value,
            comparator=criterion["comparator"],
            threshold=criterion["threshold"],
        ), f"reset already passes {instance['task_id']}: value={value}"


def test_so101_reference_private_suite_and_renderer() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    suite = _suite(package, design)
    render_spec = importlib.util.spec_from_file_location(
        "so101_render",
        PACKAGE_ROOT / "reference" / "render.py",
    )
    assert render_spec is not None and render_spec.loader is not None
    render_module = importlib.util.module_from_spec(render_spec)
    render_spec.loader.exec_module(render_module)
    render_config = render_module.default_render_config()
    assert render_config == {
        "enabled": True,
        "width": 640,
        "height": 480,
        "fps": 10.0,
        "camera": -1,
    }
    with tempfile.TemporaryDirectory(prefix="so101-demo3-evidence-") as temporary:
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=package.reference_driver,
            condition="from-scratch",
            output_dir=temporary,
            record_video=False,
            wall_timeout_s=120.0,
            run_id="so101-reference-calibration",
            attempt=0,
        )
    assert report["pipeline_completed"]
    assert report["physical_validation_executed"]
    assert report["validation_passed"]
    assert report["passed_task_count"] == 20
    assert report["video_complete"]
    for trial in report["trials"]:
        assert trial["trial_passed"]
        assert trial["measurement_value"] is not None
        evidence = trial["physical_evidence"]
        assert evidence["step_count"] > 0
        assert evidence["ctrl_observed_before_step"]
        assert evidence["ctrl_changed_from_reset"]
        assert not evidence["direct_state_write_detected"]
        assert all(trial["guard_outcomes"].values())
