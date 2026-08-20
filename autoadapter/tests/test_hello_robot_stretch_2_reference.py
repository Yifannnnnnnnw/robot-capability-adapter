from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import tempfile

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.harness import run_private_suite
from autoadapter2.libraries import load_robot_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    ROOT / "libraries" / "robots" / "hello_robot_stretch_2" / "1.0.0"
)
DRIVER_PATH = PACKAGE_ROOT / "reference" / "driver.py"
REFERENCE_CONDITION = "skeleton-assisted"
SUPPORTED_TASK_IDS = (
    "mw_reach_target",
    "mw_push_to_goal",
    "mw_pick_place",
    "mw_window_open",
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
    "mw_window_close",
    "mw_faucet_close",
)
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
            "mw_peg_insertion_side",
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
            "mw_window_open",
            "mw_window_close",
        ),
    ),
    (
        "rotation",
        "rotation_task",
        ("mw_faucet_open", "mw_faucet_close", "mw_dial_turn", "mw_lever_pull"),
    ),
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _design(package: object) -> dict:
    tasks_by_id = {task["task_id"]: task for task in package.tasks}  # type: ignore[attr-defined]
    capabilities = []
    for capability_id, method_name, task_ids in CAPABILITY_GROUPS:
        task = tasks_by_id[task_ids[0]]
        clause = task["scoring"][0]
        capabilities.append(
            {
                "capability_id": capability_id,
                "method_name": method_name,
                "covered_task_ids": list(task_ids),
                "validation_contract": [
                    {
                        "case_role": "primary",
                        "selection_rationale": "Focused Stretch calibration contract.",
                        "source_task_id": task["task_id"],
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
                ],
            }
        )
    return {"capabilities": capabilities}


def _suite(package: object, design: dict) -> dict:
    instances = _read(package.private_dir / "instances.json")["instances"]  # type: ignore[attr-defined]
    instances_by_task = {instance["task_id"]: instance for instance in instances}
    capabilities_by_task = {
        task_id: capability
        for capability in design["capabilities"]
        for task_id in capability["covered_task_ids"]
    }
    cases = []
    for task in package.tasks:  # type: ignore[attr-defined]
        if task["task_id"] not in SUPPORTED_TASK_IDS:
            continue
        capability = capabilities_by_task[task["task_id"]]
        instance = instances_by_task[task["task_id"]]
        for clause in task["scoring"]:
            cases.append(
                {
                    "case_id": f"case-{task['task_id']}-{clause['clause_id']}",
                    "capability_id": capability["capability_id"],
                    "method_name": capability["method_name"],
                    "task_id": task["task_id"],
                    "source_clause_id": clause["clause_id"],
                    "instance_id": instance["instance_id"],
                    "binding_id": instance["clause_bindings"][clause["clause_id"]],
                    "guard_ids": instance["guard_ids"],
                    "repetitions": instance["repetitions"],
                    "timeout_sim_s": instance["timeout_sim_s"],
                    "criterion": {
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
    return {
        "artifact_type": "task_demo_suite",
        "schema_version": "1.0",
        "robot_configuration_id": package.robot_configuration_id,  # type: ignore[attr-defined]
        "package_version": package.package_version,  # type: ignore[attr-defined]
        "task_snapshot_id": package.snapshot_id,  # type: ignore[attr-defined]
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": cases,
    }


def test_stretch_reference_passes_complete_real_private_harness() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    source = DRIVER_PATH.read_text(encoding="utf-8")
    audit = audit_driver_source(
        source,
        condition=REFERENCE_CONDITION,
        capability_methods=tuple(group[1] for group in CAPABILITY_GROUPS),
    )
    assert audit.ctrl_references > 0
    assert audit.physics_step_references > 0
    assert audit.imports_trusted_skeleton

    with tempfile.TemporaryDirectory(prefix="stretch-reference-focused-") as output_dir:
        report = run_private_suite(
            package=package,
            design=design,
            suite=_suite(package, design),
            driver_path=DRIVER_PATH,
            condition=REFERENCE_CONDITION,
            output_dir=output_dir,
            record_video=False,
            wall_timeout_s=45.0,
            run_id="stretch-reference-focused",
            attempt=0,
        )

    assert report["pipeline_completed"]
    assert report["physical_validation_executed"]
    assert report["validation_passed"]
    assert report["passed_task_count"] == len(SUPPORTED_TASK_IDS)
    assert Counter(trial["task_id"] for trial in report["trials"]) == Counter(
        SUPPORTED_TASK_IDS
    )
    for trial in report["trials"]:
        assert trial["measurement_value"] is not None
        assert trial["trial_passed"]
        evidence = trial["physical_evidence"]
        assert evidence["step_count"] > 0
        assert evidence["ctrl_observed_before_step"]
        assert evidence["ctrl_changed_from_reset"]
        assert evidence["direct_state_write_detected"] is False
        assert all(trial["guard_outcomes"].values())
