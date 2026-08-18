from __future__ import annotations

import json
import importlib.util
import tempfile
from pathlib import Path

import mujoco

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
