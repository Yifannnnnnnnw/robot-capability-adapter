from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.libraries import load_robot_package
from autoadapter2.pipeline import render_reference_driver
from autoadapter2.validation_compiler import sample_private_suite, validate_private_suite


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "unitree-go2-stock-12dof" / "1.0.0"


def _design_and_suite(package):
    groups = [
        ("planar_motion", "calibrate_planar_motion", ["GO2-T01", "GO2-T05"]),
        (
            "step_transition",
            "calibrate_step_transition",
            ["GO2-T02", "GO2-T03", "GO2-T04"],
        ),
        (
            "course_navigation",
            "calibrate_course_navigation",
            ["GO2-T06", "GO2-T07", "GO2-T08", "GO2-T09", "GO2-T10", "GO2-T11"],
        ),
        (
            "rough_terrain",
            "calibrate_rough_terrain",
            ["GO2-T12", "GO2-T13", "GO2-T14", "GO2-T15"],
        ),
        (
            "parkour_obstacles",
            "calibrate_parkour_obstacles",
            ["GO2-T16", "GO2-T17", "GO2-T18", "GO2-T19"],
        ),
        ("parkour_course", "calibrate_parkour_course", ["GO2-T20"]),
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
    assert len(package.tasks) == 20
    assert sum(len(task["scoring"]) for task in package.tasks) == 27
    assert package.snapshot_id == catalog["snapshot_id"]
    for name in ("instances", "bindings", "guards"):
        private = json.loads(
            (package.private_dir / f"{name}.json").read_text(encoding="utf-8")
        )
        assert private["task_snapshot_id"] == package.snapshot_id
    instances = json.loads(
        (package.private_dir / "instances.json").read_text(encoding="utf-8")
    )["instances"]
    instance_tasks = {item["task_id"] for item in instances}
    assert instance_tasks == {task["task_id"] for task in package.tasks}
    assert all(item["video_width"] >= 640 for item in instances)
    assert all(item["video_height"] >= 480 for item in instances)
    for source in package.sources:
        if "github.com" in source["locator"].lower():
            assert re.search(r"(?<![0-9a-f])[0-9a-f]{12,40}(?![0-9a-f])", source["locator"])
            assert "/main/" not in source["locator"].lower()
            assert "/master/" not in source["locator"].lower()


def test_go2_source_protocol_scenes_compile_and_reset() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    instances = json.loads(
        (package.private_dir / "instances.json").read_text(encoding="utf-8")
    )["instances"]
    instance_by_task = {instance["task_id"]: instance for instance in instances}

    assert {source["source_id"] for source in package.sources} >= {
        "SRC-LEE-2020",
        "SRC-MIKI-2022",
        "SRC-SHI-2023",
        "SRC-QRC-2023",
        "SRC-BARKOUR-2023",
        "SRC-ANYMAL-2016",
    }
    assert all(instance_by_task[task_id]["repetitions"] == 10 for task_id in {
        "GO2-T02", "GO2-T03", "GO2-T04"
    })
    assert all(instance_by_task[task_id]["repetitions"] == 3 for task_id in {
        "GO2-T12", "GO2-T13", "GO2-T14", "GO2-T15"
    })

    for instance in instances:
        scene = package.root / instance["scene_entrypoint"]
        model = mujoco.MjModel.from_xml_path(str(scene))
        data = mujoco.MjData(model)
        key_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "task_start")
        camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "evidence")
        assert key_id >= 0, scene.name
        assert camera_id >= 0, scene.name
        assert model.cam_mode[camera_id] == mujoco.mjtCamLight.mjCAMLIGHT_TRACKCOM, scene.name
        mujoco.mj_resetDataKeyframe(model, data, key_id)
        mujoco.mj_forward(model, data)
        assert np.all(np.isfinite(data.qpos)), scene.name
        assert np.all(np.isfinite(data.xpos)), scene.name

    nominal = mujoco.MjModel.from_xml_path(str(package.root / "assets" / "go2.xml"))
    payload = mujoco.MjModel.from_xml_path(
        str(package.root / "assets" / "lee_payload_step.xml")
    )
    payload_id = mujoco.mj_name2id(payload, mujoco.mjtObj.mjOBJ_BODY, "lee_payload")
    payload_mass = float(payload.body_mass[payload_id])
    assert np.isclose(payload_mass / float(nominal.body_mass.sum()), 0.227)


def test_go2_arbitrary_renderer_dispatches_by_task_id() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design, suite = _design_and_suite(package)
    checked = validate_private_suite(suite, package=package, design=design)
    assert len(checked["cases"]) == 27
    sampled = sample_private_suite(checked, seed="go2-source-protocol-check")
    repeated = sample_private_suite(checked, seed="go2-source-protocol-check")
    assert len(sampled["cases"]) == 5
    assert [case["case_id"] for case in sampled["cases"]] == [
        case["case_id"] for case in repeated["cases"]
    ]
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
        assert "ReferenceGo2Driver.walk_forward(self, request)" in source
        assert "ReferenceGo2Driver.traverse_stairs(self, request)" in source
        rendered_section = source.split("class RenderedGo2Driver", 1)[1]
        assert "self._gait(_number(parameters, \"duration_s\", 1.0), mode=\"forward\")" not in rendered_section
