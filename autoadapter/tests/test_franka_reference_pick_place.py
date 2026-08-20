from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pytest

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.harness import run_private_suite
from autoadapter2.libraries import load_robot_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "franka_panda" / "1.0.0"
DRIVER_PATH = PACKAGE_ROOT / "reference" / "driver.py"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _design(package: object) -> dict:
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
    by_id = {task["task_id"]: task for task in package.tasks}  # type: ignore[attr-defined]
    capabilities = []
    for capability_id, method_name, task_ids in groups:
        task = by_id[task_ids[0]]
        clause = task["scoring"][0]
        capabilities.append(
            {
                "capability_id": capability_id,
                "method_name": method_name,
                "covered_task_ids": task_ids,
                "validation_contract": [
                    {
                        "case_role": "primary",
                        "selection_rationale": "Representative calibration capability contract.",
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


def _suite(package: object, design: dict, task_id: str) -> dict:
    task = next(
        task
        for task in package.tasks  # type: ignore[attr-defined]
        if task["task_id"] == task_id
    )
    instance = next(
        item
        for item in _read(package.private_dir / "instances.json")["instances"]  # type: ignore[attr-defined]
        if item["task_id"] == task_id
    )
    capability = next(
        item
        for item in design["capabilities"]
        if task_id in item["covered_task_ids"]
    )
    clause = task["scoring"][0]
    return {
        "artifact_type": "task_demo_suite",
        "schema_version": "1.0",
        "robot_configuration_id": package.robot_configuration_id,  # type: ignore[attr-defined]
        "package_version": package.package_version,  # type: ignore[attr-defined]
        "task_snapshot_id": package.snapshot_id,  # type: ignore[attr-defined]
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": [
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
        ],
    }


@pytest.mark.parametrize(
    ("task_id", "threshold"),
    (
        ("mw_pick_place", 0.07),
        ("mw_pick_place_wall", 0.07),
        ("mw_peg_insertion_side", 0.07),
        ("mw_bin_picking", 0.05),
    ),
)
def test_franka_reference_object_case_passes_real_harness(
    task_id: str, threshold: float
) -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    suite = _suite(package, design, task_id)
    source = DRIVER_PATH.read_text(encoding="utf-8")
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

    with tempfile.TemporaryDirectory(prefix=f"franka-{task_id}-") as output_dir:
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=DRIVER_PATH,
            condition="from-scratch",
            output_dir=output_dir,
            record_video=False,
            wall_timeout_s=150.0,
            run_id=f"franka-reference-{task_id}",
            attempt=0,
        )

    assert report["pipeline_completed"]
    assert report["physical_validation_executed"]
    assert report["validation_passed"]
    assert report["passed_task_count"] == 1
    assert len(report["trials"]) == 1
    trial = report["trials"][0]
    assert trial["task_id"] == task_id
    assert trial["measurement_value"] is not None
    assert trial["measurement_value"] <= threshold
    assert trial["trial_passed"]
    evidence = trial["physical_evidence"]
    assert 0 < evidence["step_count"] <= 10000
    assert evidence["ctrl_observed_before_step"]
    assert evidence["ctrl_changed_from_reset"]
    assert evidence["direct_state_write_detected"] is False
    assert trial["guard_outcomes"]
    assert all(trial["guard_outcomes"].values())
