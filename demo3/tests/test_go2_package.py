from __future__ import annotations

import importlib.util
import json
import math
import re
import tempfile
from collections import Counter
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.driver_synthesis.generation import build_public_generation_inputs
from autoadapter2.harness.measurements import MeasurementError, compare, measure
from autoadapter2.harness.session import TrackedMuJoCoSession, apply_framework_reset
from autoadapter2.libraries import load_robot_package
from autoadapter2.pipeline import render_reference_driver
from autoadapter2.trusted_skeletons.quadruped_pd_gait import (
    QuadrupedPDGaitSkeleton,
    QuadrupedSpec,
)
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


def test_go2_skeleton_inventory_resolves_to_the_visible_runtime_contract() -> None:
    inventory_path = PACKAGE_ROOT / "skeleton" / "quadruped_pd_gait.py"
    module_spec = importlib.util.spec_from_file_location(
        "demo3_go2_skeleton_inventory", inventory_path
    )
    assert module_spec is not None and module_spec.loader is not None
    inventory = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(inventory)

    assert inventory.QuadrupedPDGaitSkeleton is QuadrupedPDGaitSkeleton
    assert inventory.QuadrupedSpec is QuadrupedSpec

    package = load_robot_package(PACKAGE_ROOT)
    design, _suite = _design_and_suite(package)
    inputs = build_public_generation_inputs(
        package,
        design,
        condition="skeleton-assisted",
    )
    source_files = {
        item["path"]: item["source"]
        for item in inputs["condition_eligible_artifacts"]["source_files"]
    }
    runtime_path = "runtime/autoadapter2/trusted_skeletons/quadruped_pd_gait.py"
    assert runtime_path in source_files
    runtime_source = source_files[runtime_path]
    assert "def command_planar_velocity(" in runtime_source
    assert "duration: float = 1.0" in runtime_source
    assert "del vy, yaw_rate" not in runtime_source


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
    assert all("go2_terminal_stability" in item["guard_ids"] for item in instances)
    assert all(item["video_width"] >= 800 for item in instances)
    assert all(item["video_height"] >= 600 for item in instances)
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
    task_by_id = {task["task_id"]: task for task in package.tasks}
    assert instance_by_task["GO2-T01"]["repetitions"] == 8
    assert task_by_id["GO2-T01"]["scoring"][0]["aggregation"] == {
        "kind": "per_trial"
    }
    assert task_by_id["GO2-T01"]["scoring"][1]["aggregation"] == {
        "kind": "per_trial_mean"
    }
    eight_directions = {
        round(index * math.pi / 4.0, 12)
        for index in (-3, -2, -1, 0, 1, 2, 3, 4)
    }
    assert {
        round(
            variant["public_arguments"]["request"]["task_parameters"]["direction_rad"],
            12,
        )
        for variant in instance_by_task["GO2-T01"]["repetition_variants"]
    } == eight_directions

    shi_task_ids = {
        "GO2-T12", "GO2-T13", "GO2-T14", "GO2-T15"
    }
    source_directions = {
        round(value, 12)
        for value in (
            0.0,
            math.pi / 4.0,
            -math.pi / 4.0,
            -math.pi,
            -5.0 * math.pi / 4.0,
            -3.0 * math.pi / 4.0,
        )
    }
    for task_id in shi_task_ids:
        instance = instance_by_task[task_id]
        assert instance["repetitions"] == 18
        directions = [
            round(
                variant["public_arguments"]["request"]["task_parameters"][
                    "direction_rad"
                ],
                12,
            )
            for variant in instance["repetition_variants"]
        ]
        assert set(directions) == source_directions
        assert set(Counter(directions).values()) == {3}

    for instance in instances:
        scene = package.root / instance["scene_entrypoint"]
        model = mujoco.MjModel.from_xml_path(str(scene))
        data = mujoco.MjData(model)
        key_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "task_start")
        camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "evidence")
        assert key_id >= 0, scene.name
        assert camera_id >= 0, scene.name
        assert model.cam_mode[camera_id] == mujoco.mjtCamLight.mjCAMLIGHT_TRACKCOM, scene.name
        assert float(model.cam_fovy[camera_id]) <= 38.0, scene.name
        assert model.vis.global_.offwidth >= instance["video_width"], scene.name
        assert model.vis.global_.offheight >= instance["video_height"], scene.name
        mujoco.mj_resetDataKeyframe(model, data, key_id)
        mujoco.mj_forward(model, data)
        assert np.all(np.isfinite(data.qpos)), scene.name
        assert np.all(np.isfinite(data.xpos)), scene.name

    for task_id in shi_task_ids:
        instance = instance_by_task[task_id]
        scene = package.root / instance["scene_entrypoint"]
        model = mujoco.MjModel.from_xml_path(str(scene))
        data = mujoco.MjData(model)
        map_limit_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_GEOM, "map_limit"
        )
        assert map_limit_id >= 0
        for variant in instance["repetition_variants"]:
            direction = variant["public_arguments"]["request"]["task_parameters"][
                "direction_rad"
            ]
            apply_framework_reset(mujoco, model, data, variant["reset"])
            assert np.allclose(
                data.geom_xpos[map_limit_id, :2],
                [5.0 * math.cos(direction), 5.0 * math.sin(direction)],
                atol=1e-9,
            )

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


def test_go2_private_resets_do_not_pre_satisfy_a_task() -> None:
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
        scene = package.root / instance["scene_entrypoint"]
        model = mujoco.MjModel.from_xml_path(str(scene))
        data = mujoco.MjData(model)
        variants = instance.get("repetition_variants") or [{}]
        for variant_index, variant in enumerate(variants):
            reset = variant.get("reset", instance["reset"])
            public_arguments = variant.get(
                "public_arguments", instance["public_arguments"]
            )
            apply_framework_reset(mujoco, model, data, reset)
            tracker = TrackedMuJoCoSession(
                mujoco=mujoco,
                model=model,
                data=data,
                max_steps=1,
                max_sim_time_s=1.0,
                sample_hz=20.0,
            )
            evidence = {
                "samples": [tracker.snapshot()],
                "step_count": 0,
                "contact_pair_step_counts": [],
            }
            clause_passes = []
            for clause in task_by_id[instance["task_id"]]["scoring"]:
                binding = binding_by_id[
                    instance["clause_bindings"][clause["clause_id"]]
                ]
                try:
                    value = measure(
                        binding,
                        evidence=evidence,
                        public_arguments=public_arguments,
                    )
                    passed = compare(
                        value,
                        comparator=clause["comparator"],
                        threshold=clause["threshold"],
                    )
                except MeasurementError:
                    passed = False
                clause_passes.append(passed)
            assert not all(clause_passes), (
                f"reset already satisfies {instance['task_id']} variant "
                f"{variant_index}"
            )


def test_go2_forward_reference_exercises_ctrl_step_and_direction_measurement() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    spec = importlib.util.spec_from_file_location(
        "go2_reference_focused", package.reference_driver
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    scene = package.root / "assets" / "lee_flat.xml"
    directions = (
        0.0,
        math.pi / 4.0,
        math.pi / 2.0,
        3.0 * math.pi / 4.0,
        math.pi,
        -3.0 * math.pi / 4.0,
        -math.pi / 2.0,
        -math.pi / 4.0,
    )
    for direction in directions:
        model = mujoco.MjModel.from_xml_path(str(scene))
        data = mujoco.MjData(model)
        base_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, "base_link"
        )
        apply_framework_reset(
            mujoco, model, data, {"kind": "keyframe", "name": "task_start"}
        )
        tracker = TrackedMuJoCoSession(
            mujoco=mujoco,
            model=model,
            data=data,
            max_steps=1500,
            max_sim_time_s=4.0,
            sample_hz=20.0,
        )
        request = {
            "task_id": "GO2-T01",
            "task_parameters": {
                "duration_s": 2.0,
                "target_speed_m_s": 0.4,
                "direction_rad": direction,
            },
        }
        with tracker:
            module.build(model=model, data=data).walk_forward(request)
            tracker.finish()
        evidence = tracker.evidence()
        speed = measure(
            {
                "kind": "mean_body_planar_speed",
                "parameters": {"body_name": "base_link"},
            },
            evidence=evidence,
            public_arguments={"request": request},
        )
        heading_error = measure(
            {
                "kind": "mean_body_heading_error_deg",
                "parameters": {
                    "body_name": "base_link",
                    "direction_argument": "request.task_parameters.direction_rad",
                    "minimum_displacement": 0.1,
                },
            },
            evidence=evidence,
            public_arguments={"request": request},
        )

        assert speed >= 0.4, direction
        assert heading_error <= 10.0, direction
        assert data.xpos[base_id, 2] >= 0.20
        assert data.xmat[base_id, 8] >= 0.70
        assert evidence["step_count"] > 0
        assert evidence["ctrl_observed_before_step"]
        assert evidence["ctrl_changed_from_reset"]
        assert not evidence["direct_state_write_detected"]
