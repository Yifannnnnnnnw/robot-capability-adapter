from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.harness import run_private_suite
from autoadapter2.libraries import load_robot_package
from autoadapter2.pipeline import render_reference_driver
from autoadapter2.validation_compiler import validate_private_suite


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "unitree-go2-stock-12dof" / "1.0.0"


def _design_and_suite(package):
    groups = [
        ("posture_hold", "calibrate_posture", ["GO2-T01", "GO2-T02", "GO2-T03", "GO2-T04"]),
        (
            "flat_gait",
            "calibrate_gait",
            ["GO2-T05", "GO2-T06", "GO2-T07", "GO2-T08", "GO2-T09"],
        ),
        (
            "flat_navigation",
            "calibrate_navigation",
            ["GO2-T10", "GO2-T11", "GO2-T12"],
        ),
        ("incline", "calibrate_incline", ["GO2-T13"]),
        (
            "transitions",
            "calibrate_transition",
            ["GO2-T14", "GO2-T15", "GO2-T16", "GO2-T17"],
        ),
        (
            "obstacles",
            "calibrate_obstacles",
            ["GO2-T18", "GO2-T19", "GO2-T20", "GO2-T21", "GO2-T22"],
        ),
    ]
    task_by_id = {task["task_id"]: task for task in package.tasks}
    capabilities = []
    for capability_id, method_name, task_ids in groups:
        contract = []
        for task_id in task_ids:
            for clause in task_by_id[task_id]["scoring"]:
                contract.append(
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
                "validation_contract": contract,
            }
        )

    instances = json.loads(
        (package.private_dir / "instances.json").read_text(encoding="utf-8")
    )["instances"]
    instance_by_task = {instance["task_id"]: instance for instance in instances}
    cases = []
    for capability in capabilities:
        for task_id in capability["covered_task_ids"]:
            instance = instance_by_task[task_id]
            for clause in capability["validation_contract"]:
                if clause["source_task_id"] != task_id:
                    continue
                source_clause_id = clause["source_clause_id"]
                cases.append(
                    {
                        "case_id": f"case-{task_id}-{source_clause_id}",
                        "capability_id": capability["capability_id"],
                        "method_name": capability["method_name"],
                        "task_id": task_id,
                        "source_clause_id": source_clause_id,
                        "instance_id": instance["instance_id"],
                        "binding_id": instance["clause_bindings"][source_clause_id],
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
    design = {"capabilities": capabilities}
    suite = {
        "artifact_type": "private_validation_suite",
        "schema_version": "1.0",
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": cases,
    }
    return design, suite


def test_go2_package_snapshot_and_private_coverage() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    catalog = json.loads((package.root / "tasks" / "catalog.json").read_text(encoding="utf-8"))
    assert len(package.tasks) >= 20
    assert package.snapshot_id == catalog["snapshot_id"]
    for name in ("instances", "bindings", "guards"):
        private = json.loads(
            (package.private_dir / f"{name}.json").read_text(encoding="utf-8")
        )
        assert private["task_snapshot_id"] == package.snapshot_id
    instance_tasks = {
        item["task_id"]
        for item in json.loads(
            (package.private_dir / "instances.json").read_text(encoding="utf-8")
        )["instances"]
    }
    assert instance_tasks == {task["task_id"] for task in package.tasks}
    for source in package.sources:
        if "github.com" in source["locator"].lower():
            assert re.search(r"(?<![0-9a-f])[0-9a-f]{12,40}(?![0-9a-f])", source["locator"])
            assert "/main/" not in source["locator"].lower()
            assert "/master/" not in source["locator"].lower()


def test_go2_arbitrary_renderer_dispatches_by_task_id() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design, suite = _design_and_suite(package)
    checked = validate_private_suite(suite, package=package, design=design)
    assert len(checked["cases"]) == 31
    with tempfile.TemporaryDirectory(prefix="go2-reference-render-") as temporary:
        driver_path = render_reference_driver(package, design, Path(temporary))
        source = driver_path.read_text(encoding="utf-8")
        audit = audit_driver_source(
            source,
            condition="from-scratch",
            capability_methods=tuple(
                capability["method_name"] for capability in design["capabilities"]
            ),
        )
        assert audit.ctrl_references > 0
        assert audit.physics_step_references > 0
        assert "task_id == \"GO2-T01\"" in source
        assert "ReferenceGo2Driver.stand(self, request)" in source
        assert "ReferenceGo2Driver.traverse_stairs(self, request)" in source
        rendered_section = source.split("class RenderedGo2Driver", 1)[1]
        assert "self._gait(_number(parameters, \"duration_s\", 1.0), mode=\"forward\")" not in rendered_section


def test_go2_reference_physical_suite_without_video_backend() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design, suite = _design_and_suite(package)
    with tempfile.TemporaryDirectory(prefix="go2-reference-physical-") as temporary:
        output = Path(temporary)
        driver_path = render_reference_driver(package, design, output / "rendered")
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=driver_path,
            condition="from-scratch",
            output_dir=output / "validation",
            record_video=False,
            wall_timeout_s=120.0,
            run_id="go2-reference-physical-suite",
            attempt=0,
        )
    assert report["pipeline_completed"]
    assert report["physical_validation_executed"]
    assert not report["validation_passed"]
    assert len({trial["task_id"] for trial in report["trials"]}) == 22
    assert all(trial["worker_completed"] for trial in report["trials"])
    assert all(trial["criterion_passed"] for trial in report["trials"])
    assert all(
        all(
            outcome
            for guard_id, outcome in trial["guard_outcomes"].items()
            if guard_id != "go2_video"
        )
        for trial in report["trials"]
    )
