from __future__ import annotations

import json
from pathlib import Path
import tempfile

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.harness import run_private_suite
from autoadapter2.libraries import load_robot_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "ufactory_xarm7" / "1.0.0"
DRIVER_PATH = PACKAGE_ROOT / "reference" / "driver.py"
CAPABILITY_GROUPS = (
    ("reach", "reach_task", ("mw_reach_target",)),
    (
        "contact",
        "contact_task",
        ("mw_push_to_goal", "mw_push_wall", "mw_sweep_into_goal"),
    ),
    (
        "object",
        "object_task",
        (
            "mw_pick_place",
            "mw_pick_place_wall",
            "mw_peg_insertion_side",
            "mw_bin_picking",
            "mw_pick_out_of_hole",
        ),
    ),
    (
        "fixture",
        "fixture_task",
        (
            "mw_drawer_open",
            "mw_drawer_close",
            "mw_button_press",
            "mw_button_press_topdown",
            "mw_handle_press",
            "mw_handle_pull",
            "mw_door_open",
            "mw_door_close",
        ),
    ),
    (
        "rotation",
        "rotation_task",
        ("mw_faucet_open", "mw_dial_turn", "mw_lever_pull"),
    ),
)
CRITERION_FIELDS = (
    "metric",
    "unit",
    "comparator",
    "threshold",
    "temporal",
    "aggregation",
    "source_refs",
)
CONTACT_FIXTURE_TASK_IDS = (
    "mw_push_to_goal",
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
)
OBJECT_TASK_IDS = (
    "mw_pick_place",
    "mw_pick_place_wall",
    "mw_peg_insertion_side",
    "mw_bin_picking",
    "mw_pick_out_of_hole",
)
ROTATION_TASK_IDS = ("mw_faucet_open", "mw_dial_turn", "mw_lever_pull")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _design(package: object) -> dict:
    by_id = {task["task_id"]: task for task in package.tasks}  # type: ignore[attr-defined]
    capabilities = []
    for capability_id, method_name, task_ids in CAPABILITY_GROUPS:
        task = by_id[task_ids[0]]
        clause = task["scoring"][0]
        capabilities.append(
            {
                "capability_id": capability_id,
                "method_name": method_name,
                "covered_task_ids": list(task_ids),
                "validation_contract": [
                    {
                        "case_role": "primary",
                        "selection_rationale": "Representative calibration capability contract.",
                        "source_task_id": task["task_id"],
                        "source_clause_id": clause["clause_id"],
                        **{key: clause[key] for key in CRITERION_FIELDS},
                    }
                ],
            }
        )
    return {"capabilities": capabilities}


def _suite(package: object, design: dict, task_ids: tuple[str, ...]) -> dict:
    tasks = {task["task_id"]: task for task in package.tasks}  # type: ignore[attr-defined]
    instances = {
        instance["task_id"]: instance
        for instance in _read(package.private_dir / "instances.json")["instances"]  # type: ignore[attr-defined]
    }
    task_to_capability = {
        task_id: capability
        for capability in design["capabilities"]
        for task_id in capability["covered_task_ids"]
    }
    cases = []
    for task_id in task_ids:
        task = tasks[task_id]
        instance = instances[task_id]
        capability = task_to_capability[task_id]
        clause = task["scoring"][0]
        cases.append(
            {
                "case_id": f"case-{task_id}",
                "capability_id": capability["capability_id"],
                "method_name": capability["method_name"],
                "task_id": task_id,
                "source_clause_id": clause["clause_id"],
                "instance_id": instance["instance_id"],
                "binding_id": instance["clause_bindings"][clause["clause_id"]],
                "guard_ids": instance["guard_ids"],
                "repetitions": instance["repetitions"],
                "timeout_sim_s": instance["timeout_sim_s"],
                "criterion": {key: clause[key] for key in CRITERION_FIELDS},
            }
        )
    return {
        "artifact_type": "task_demo_suite",
        "schema_version": "1.0",
        "robot_configuration_id": package.robot_configuration_id,  # type: ignore[attr-defined]
        "package_version": package.package_version,  # type: ignore[attr-defined]
        "task_snapshot_id": package.snapshot_id,  # type: ignore[attr-defined]
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": cases,
    }


def _failure_diagnostic(trial: dict) -> dict:
    samples = trial.get("physical_evidence", {}).get("samples", [])
    final = samples[-1] if samples else {}
    body_positions = final.get("body_positions", {})
    site_positions = final.get("site_positions", {})
    return {
        "task_id": trial["task_id"],
        "measurement": trial.get("measurement_value"),
        "candidate_exception": trial.get("candidate_exception"),
        "measurement_error": trial.get("measurement_error"),
        "worker_completed": trial.get("worker_completed"),
        "physical_execution_passed": trial.get("physical_execution_passed"),
        "final_link_tcp": site_positions.get("link_tcp"),
        "final_workpiece": body_positions.get("workpiece"),
        "final_button": site_positions.get("front_button_site"),
        "final_handle": site_positions.get("vertical_handle_site"),
        "final_joint_positions": final.get("joint_positions"),
        "final_actuator_controls": final.get("actuator_controls"),
        "final_contacts": final.get("contacts"),
    }


def test_xarm7_reference_reach_passes_the_real_private_harness() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    source = DRIVER_PATH.read_text(encoding="utf-8")
    audit = audit_driver_source(
        source,
        condition="from-scratch",
        capability_methods=tuple(group[1] for group in CAPABILITY_GROUPS),
    )
    assert audit.ctrl_references > 0
    assert audit.physics_step_references > 0
    assert not audit.imports_trusted_skeleton

    with tempfile.TemporaryDirectory(prefix="xarm7-reference-reach-") as output_dir:
        report = run_private_suite(
            package=package,
            design=design,
            suite=_suite(package, design, ("mw_reach_target",)),
            driver_path=DRIVER_PATH,
            condition="from-scratch",
            output_dir=output_dir,
            record_video=False,
            wall_timeout_s=30.0,
            run_id="xarm7-reference-reach",
            attempt=0,
        )

    assert report["pipeline_completed"]
    assert report["physical_validation_executed"]
    assert report["validation_passed"]
    assert report["passed_task_count"] == 1
    assert len(report["trials"]) == 1
    trial = report["trials"][0]
    assert trial["task_id"] == "mw_reach_target"
    assert trial["measurement_value"] is not None
    assert trial["trial_passed"]
    evidence = trial["physical_evidence"]
    assert evidence["step_count"] > 0
    assert evidence["ctrl_observed_before_step"]
    assert evidence["ctrl_changed_from_reset"]
    assert evidence["direct_state_write_detected"] is False
    assert all(trial["guard_outcomes"].values())


def test_xarm7_reference_contact_and_fixture_cases_pass_real_harness() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    with tempfile.TemporaryDirectory(prefix="xarm7-reference-contact-fixture-") as output_dir:
        report = run_private_suite(
            package=package,
            design=design,
            suite=_suite(package, design, CONTACT_FIXTURE_TASK_IDS),
            driver_path=DRIVER_PATH,
            condition="from-scratch",
            output_dir=output_dir,
            record_video=False,
            wall_timeout_s=150.0,
            run_id="xarm7-reference-contact-fixture",
            attempt=0,
        )

    failures = [
        _failure_diagnostic(trial)
        for trial in report["trials"]
        if not trial["trial_passed"]
    ]
    assert report["pipeline_completed"]
    diagnostics = json.dumps(failures, indent=2, sort_keys=True)
    assert report["physical_validation_executed"], diagnostics
    assert report["validation_passed"], diagnostics
    assert report["passed_task_count"] == len(CONTACT_FIXTURE_TASK_IDS)
    assert len(report["trials"]) == len(CONTACT_FIXTURE_TASK_IDS)
    for trial in report["trials"]:
        assert trial["measurement_value"] is not None
        evidence = trial["physical_evidence"]
        assert evidence["step_count"] > 0
        assert evidence["ctrl_observed_before_step"]
        assert evidence["ctrl_changed_from_reset"]
        assert evidence["direct_state_write_detected"] is False
        assert all(trial["guard_outcomes"].values())


def test_xarm7_reference_object_cases_pass_real_harness() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    with tempfile.TemporaryDirectory(prefix="xarm7-reference-object-") as output_dir:
        report = run_private_suite(
            package=package,
            design=design,
            suite=_suite(package, design, OBJECT_TASK_IDS),
            driver_path=DRIVER_PATH,
            condition="from-scratch",
            output_dir=output_dir,
            record_video=False,
            wall_timeout_s=150.0,
            run_id="xarm7-reference-object",
            attempt=0,
        )

    failures = [
        _failure_diagnostic(trial)
        for trial in report["trials"]
        if not trial["trial_passed"]
    ]
    diagnostics = json.dumps(failures, indent=2, sort_keys=True)
    assert report["pipeline_completed"]
    assert report["physical_validation_executed"], diagnostics
    assert report["validation_passed"], diagnostics
    assert report["passed_task_count"] == len(OBJECT_TASK_IDS)
    assert len(report["trials"]) == len(OBJECT_TASK_IDS)


def test_xarm7_reference_rotation_cases_pass_real_harness() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    with tempfile.TemporaryDirectory(prefix="xarm7-reference-rotation-") as output_dir:
        report = run_private_suite(
            package=package,
            design=design,
            suite=_suite(package, design, ROTATION_TASK_IDS),
            driver_path=DRIVER_PATH,
            condition="from-scratch",
            output_dir=output_dir,
            record_video=False,
            wall_timeout_s=90.0,
            run_id="xarm7-reference-rotation",
            attempt=0,
        )

    failures = [
        _failure_diagnostic(trial)
        for trial in report["trials"]
        if not trial["trial_passed"]
    ]
    diagnostics = json.dumps(failures, indent=2, sort_keys=True)
    assert report["pipeline_completed"]
    assert report["physical_validation_executed"], diagnostics
    assert report["validation_passed"], diagnostics
    assert report["passed_task_count"] == len(ROTATION_TASK_IDS)
    assert len(report["trials"]) == len(ROTATION_TASK_IDS)
