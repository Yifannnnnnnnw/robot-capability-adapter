from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.harness import run_private_suite
from autoadapter2.harness.measurements import compare, measure
from autoadapter2.harness.session import TrackedMuJoCoSession, apply_framework_reset
from autoadapter2.libraries import load_robot_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "kuka_iiwa_14" / "1.0.0"
DRIVER_PATH = PACKAGE_ROOT / "reference" / "driver.py"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
SNAPSHOT_ID = "kuka-iiwa-14-metaworld-source-protocols-2026-08-20-v2"
TASK_IDS = (
    "mw_reach_target",
    "mw_push_to_goal",
    "mw_push_wall",
    "mw_sweep_into_goal",
    "mw_drawer_open",
    "mw_drawer_close",
    "mw_button_press",
    "mw_button_press_topdown",
    "mw_handle_press",
    "mw_door_open",
    "mw_door_close",
    "mw_faucet_open",
    "mw_dial_turn",
    "mw_lever_pull",
    "mw_door_lock",
    "mw_door_unlock",
    "mw_faucet_close",
    "mw_soccer",
    "mw_window_open",
    "mw_window_close",
)
CAPABILITY_GROUPS = (
    ("reach", "reach_task", ("mw_reach_target",)),
    (
        "contact",
        "contact_task",
        ("mw_push_to_goal", "mw_push_wall", "mw_sweep_into_goal", "mw_soccer"),
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
            "mw_door_open",
            "mw_door_close",
            "mw_door_lock",
            "mw_door_unlock",
            "mw_window_open",
            "mw_window_close",
        ),
    ),
    (
        "rotation",
        "rotation_task",
        ("mw_faucet_open", "mw_dial_turn", "mw_lever_pull", "mw_faucet_close"),
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
SAFE_Q = (
    -0.13480555,
    -0.19423601,
    -0.23833583,
    -1.94562248,
    -0.01401762,
    0.44345798,
    0.0,
)
FIXTURE_RESETS = {
    "mw_drawer_close": {"drawer_slide": -0.08},
    "mw_door_close": {"door_hinge": 1.2},
    "mw_faucet_close": {"faucet_hinge": 1.2},
    "mw_window_close": {"window_slide": 0.1},
}
CONTACT_TASK_GEOMS = {
    "mw_push_to_goal": ("workpiece_geom",),
    "mw_push_wall": ("workpiece_geom",),
    "mw_sweep_into_goal": ("workpiece_geom",),
    "mw_drawer_open": ("drawer_handle_geom",),
    "mw_drawer_close": ("drawer_handle_geom",),
    "mw_button_press": ("front_button_geom",),
    "mw_button_press_topdown": ("top_button_geom",),
    "mw_handle_press": ("vertical_handle_geom",),
    "mw_door_open": ("door_handle_geom",),
    "mw_door_close": ("door_handle_geom", "door_panel_geom"),
    "mw_faucet_open": ("faucet_tip_geom", "faucet_arm"),
    "mw_dial_turn": ("dial_tip_geom", "dial_face"),
    "mw_lever_pull": ("lever_tip_geom", "lever_arm"),
    "mw_door_lock": ("door_lock_control",),
    "mw_door_unlock": ("door_unlock_control",),
    "mw_faucet_close": ("faucet_tip_geom", "faucet_arm"),
    "mw_soccer": ("soccer_ball_geom",),
    "mw_window_open": ("window_handle_geom",),
    "mw_window_close": ("window_handle_geom",),
}
BINDING_ENTITIES = {
    "mw_reach_target": ("final_site_position_error", "site_name", "attachment_site"),
    "mw_push_to_goal": ("final_body_position_error", "body_name", "workpiece"),
    "mw_push_wall": ("final_body_position_error", "body_name", "workpiece"),
    "mw_sweep_into_goal": ("final_body_position_error", "body_name", "workpiece"),
    "mw_drawer_open": ("final_site_position_error", "site_name", "drawer_handle_site"),
    "mw_drawer_close": ("final_site_position_error", "site_name", "drawer_handle_site"),
    "mw_button_press": ("final_site_axis_error", "site_name", "front_button_site"),
    "mw_button_press_topdown": ("final_site_axis_error", "site_name", "top_button_site"),
    "mw_handle_press": ("final_site_axis_error", "site_name", "vertical_handle_site"),
    "mw_door_open": ("final_site_axis_error", "site_name", "door_handle_site"),
    "mw_door_close": ("final_site_position_error", "site_name", "door_handle_site"),
    "mw_faucet_open": ("final_site_position_error", "site_name", "faucet_tip_site"),
    "mw_dial_turn": ("final_site_position_error", "site_name", "dial_tip_site"),
    "mw_lever_pull": ("final_joint_position_error", "joint_name", "lever_hinge"),
    "mw_door_lock": ("final_site_axis_error", "site_name", "door_lock_site"),
    "mw_door_unlock": ("final_site_axis_error", "site_name", "door_unlock_site"),
    "mw_faucet_close": ("final_site_position_error", "site_name", "faucet_tip_site"),
    "mw_soccer": ("final_body_position_error", "body_name", "soccer_ball"),
    "mw_window_open": ("final_site_axis_error", "site_name", "window_handle_site"),
    "mw_window_close": ("final_site_axis_error", "site_name", "window_handle_site"),
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _static_task_geom_names(node: ET.Element, *, moving: bool = False) -> tuple[str, ...]:
    names: list[str] = []
    for child in node:
        if child.tag == "geom" and not moving:
            name = child.get("name")
            if name:
                names.append(name)
        elif child.tag == "body":
            child_moving = moving or any(
                item.tag in {"joint", "freejoint"} for item in child
            )
            names.extend(_static_task_geom_names(child, moving=child_moving))
    return tuple(names)


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
                        "selection_rationale": "Representative calibration contract.",
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
    capabilities = {
        task_id: capability
        for capability in design["capabilities"]
        for task_id in capability["covered_task_ids"]
    }
    cases = []
    for task_id in task_ids:
        task = tasks[task_id]
        instance = instances[task_id]
        capability = capabilities[task_id]
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


def _failure_diagnostics(report: dict) -> str:
    failures = [
        {
            "task_id": trial["task_id"],
            "measurement": trial.get("measurement_value"),
            "measurement_error": trial.get("measurement_error"),
            "candidate_exception": trial.get("candidate_exception"),
            "guard_outcomes": trial.get("guard_outcomes"),
            "physical_execution_passed": trial.get("physical_execution_passed"),
        }
        for trial in report.get("trials", [])
        if not trial.get("trial_passed")
    ]
    return json.dumps(failures, indent=2, sort_keys=True)


def test_kuka_private_package_loads_without_runtime_admission() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    assert package.robot_configuration_id == "kuka_iiwa_14"
    assert package.package_version == "1.0.0"
    assert package.snapshot_id == SNAPSHOT_ID
    assert tuple(task["task_id"] for task in package.tasks) == TASK_IDS

    instances = _read(PACKAGE_ROOT / "tasks/private/instances.json")
    bindings = _read(PACKAGE_ROOT / "tasks/private/bindings.json")
    guards = _read(PACKAGE_ROOT / "tasks/private/guards.json")
    assert len(instances["instances"]) == 20
    assert len(bindings["bindings"]) == 20
    assert len(guards["guards"]) == 22
    for document in (instances, bindings, guards):
        assert document["robot_configuration_id"] == "kuka_iiwa_14"
        assert document["package_version"] == "1.0.0"
        assert document["task_snapshot_id"] == SNAPSHOT_ID

    runtime_index = _read(RUNNABLE_INDEX_PATH)
    assert "kuka_iiwa_14" not in runtime_index["robots"]


def test_kuka_bindings_and_exact_contact_guards_are_task_specific() -> None:
    bindings = {
        binding["binding_id"].removeprefix("binding-"): binding
        for binding in _read(PACKAGE_ROOT / "tasks/private/bindings.json")["bindings"]
    }
    assert set(bindings) == set(TASK_IDS)
    for task_id, (kind, entity_key, entity_name) in BINDING_ENTITIES.items():
        binding = bindings[task_id]
        assert binding["kind"] == kind
        assert binding["parameters"][entity_key] == entity_name
        expected_target = (
            "request.task_parameters.target_angle"
            if task_id == "mw_lever_pull"
            else "request.task_parameters.target_position"
        )
        assert binding["parameters"]["target_argument"] == expected_target

    guards = {
        guard["guard_id"]: guard
        for guard in _read(PACKAGE_ROOT / "tasks/private/guards.json")["guards"]
    }
    contact_guards = {
        guard_id.removeprefix("guard-contact-"): guard
        for guard_id, guard in guards.items()
        if guard["kind"] == "named_geom_contact_pair_required"
    }
    assert set(contact_guards) == set(CONTACT_TASK_GEOMS)
    for task_id, expected_task_geoms in CONTACT_TASK_GEOMS.items():
        guard = contact_guards[task_id]
        assert guard["robot_geom_name"] == "link7_contact_geom"
        assert "robot_geom_names" not in guard
        assert tuple(guard["task_geom_names"]) == expected_task_geoms
        assert guard["minimum_steps"] == 1
        assert "link5" not in json.dumps(guard)
        assert "link6" not in json.dumps(guard)


def test_kuka_private_resets_are_framework_owned_and_exact() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    instances = _read(package.private_dir / "instances.json")["instances"]
    catalog = {task["task_id"]: task for task in package.tasks}

    for instance in instances:
        task_id = instance["task_id"]
        reset = instance["reset"]
        expected_joint_positions = {
            **{f"joint{index}": value for index, value in enumerate(SAFE_Q, 1)},
            **FIXTURE_RESETS.get(task_id, {}),
        }
        expected_controls = {
            f"actuator{index}": value for index, value in enumerate(SAFE_Q, 1)
        }
        assert reset == {
            "kind": "default",
            "joint_positions": expected_joint_positions,
            "actuator_controls": expected_controls,
        }
        assert instance["timeout_sim_s"] == 20.0
        assert instance["max_steps"] == 10000
        assert instance["sample_hz"] == 20.0
        assert instance["video_width"] == 800
        assert instance["video_height"] == 600
        assert instance["video_fps"] == 10.0
        assert instance["camera"] == "evidence"
        request = instance["public_arguments"]["request"]
        assert request["task_id"] == task_id
        required = catalog[task_id]["invocation_schema"]["request"]["task_parameters"][
            "required"
        ]
        assert set(request["task_parameters"]) == set(required)

        scene = (package.root / instance["scene_entrypoint"]).resolve()
        model = mujoco.MjModel.from_xml_path(str(scene))
        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, reset)
        for name, expected in expected_joint_positions.items():
            joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            qpos_address = int(model.jnt_qposadr[joint_id])
            np.testing.assert_allclose(data.qpos[qpos_address], expected, rtol=0.0, atol=0.0)
        for name, expected in expected_controls.items():
            actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            np.testing.assert_allclose(data.ctrl[actuator_id], expected, rtol=0.0, atol=0.0)


def test_kuka_private_scene_statics_and_reset_contact_depths() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    instances = _read(package.private_dir / "instances.json")["instances"]
    assert len({instance["scene_entrypoint"] for instance in instances}) == 16

    for instance in instances:
        scene_path = package.root / instance["scene_entrypoint"]
        worldbody = ET.parse(scene_path).getroot().find("worldbody")
        assert worldbody is not None
        model = mujoco.MjModel.from_xml_path(str(scene_path))
        for name in _static_task_geom_names(worldbody):
            geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
            assert geom_id >= 0, f"{instance['task_id']}: {name}"
            contype = int(model.geom_contype[geom_id])
            conaffinity = int(model.geom_conaffinity[geom_id])
            if name.endswith("_goal_marker"):
                assert (contype, conaffinity) == (0, 0)
            else:
                assert contype & 1, f"{instance['task_id']}: {name} contype"
                assert conaffinity & 1, f"{instance['task_id']}: {name} conaffinity"

        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, instance["reset"])
        mujoco.mj_forward(model, data)
        minimum_distance = min(
            (float(data.contact[index].dist) for index in range(data.ncon)),
            default=float("inf"),
        )
        assert minimum_distance >= -0.005, (
            f"{instance['task_id']}: minimum reset contact distance "
            f"{minimum_distance}"
        )


def test_kuka_private_reset_fails_every_task_criterion() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    instances = _read(package.private_dir / "instances.json")["instances"]
    bindings = _read(package.private_dir / "bindings.json")["bindings"]
    binding_by_id = {binding["binding_id"]: binding for binding in bindings}
    task_by_id = {task["task_id"]: task for task in package.tasks}

    checked = 0
    for instance in instances:
        model = mujoco.MjModel.from_xml_path(
            str(package.root / instance["scene_entrypoint"])
        )
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
        task = task_by_id[instance["task_id"]]
        for clause_id, binding_id in instance["clause_bindings"].items():
            criterion = next(
                clause for clause in task["scoring"] if clause["clause_id"] == clause_id
            )
            value = measure(
                binding_by_id[binding_id],
                evidence={"samples": [tracker.snapshot()], "step_count": 0},
                public_arguments=instance["public_arguments"],
            )
            assert not compare(
                value,
                comparator=criterion["comparator"],
                threshold=criterion["threshold"],
            ), f"reset already passes {instance['task_id']}: value={value}"
            checked += 1

    assert checked == len(TASK_IDS)


def test_kuka_reference_driver_is_actuator_only_and_compact() -> None:
    source = DRIVER_PATH.read_text(encoding="utf-8")
    audit = audit_driver_source(
        source,
        condition="from-scratch",
        capability_methods=tuple(group[1] for group in CAPABILITY_GROUPS),
    )
    assert audit.ctrl_references > 0
    assert audit.physics_step_references > 0
    assert not audit.imports_trusted_skeleton
    assert "mujoco.mj_reset" not in source
    assert "MjModel" not in source
    assert "MjData" not in source
    assert "data.qpos" not in source
    assert "mujoco.mj_jac" not in source

    spec = importlib.util.spec_from_file_location("kuka_reference_driver", DRIVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert tuple(module.JOINT_PLANS) == TASK_IDS
    assert sum(len(plan) for plan in module.JOINT_PLANS.values()) == 86
    for task_id, plan in module.JOINT_PLANS.items():
        np.testing.assert_allclose(plan[0]["q"], SAFE_Q, rtol=0.0, atol=0.0)
        assert sum(segment["steps"] for segment in plan) + 120 <= 10000, task_id
        assert all(len(segment["q"]) == 7 for segment in plan)


def test_kuka_reference_reach_passes_the_real_private_harness() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    with tempfile.TemporaryDirectory(prefix="kuka-reference-reach-") as output_dir:
        report = run_private_suite(
            package=package,
            design=design,
            suite=_suite(package, design, ("mw_reach_target",)),
            driver_path=DRIVER_PATH,
            condition="from-scratch",
            output_dir=output_dir,
            record_video=False,
            wall_timeout_s=30.0,
            run_id="kuka-reference-reach",
            attempt=0,
        )

    diagnostics = _failure_diagnostics(report)
    assert report["pipeline_completed"], diagnostics
    assert report["physical_validation_executed"], diagnostics
    assert report["validation_passed"], diagnostics
    assert report["passed_task_count"] == 1
    trial = report["trials"][0]
    assert trial["trial_passed"], diagnostics
    assert trial["measurement_value"] <= 0.05
    evidence = trial["physical_evidence"]
    assert evidence["step_count"] > 0
    assert evidence["ctrl_observed_before_step"]
    assert evidence["ctrl_changed_from_reset"]
    assert evidence["direct_state_write_detected"] is False
    assert all(trial["guard_outcomes"].values())


def test_kuka_reference_all_twenty_tasks_pass_the_real_private_harness() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    with tempfile.TemporaryDirectory(prefix="kuka-reference-all-") as output_dir:
        report = run_private_suite(
            package=package,
            design=design,
            suite=_suite(package, design, TASK_IDS),
            driver_path=DRIVER_PATH,
            condition="from-scratch",
            output_dir=output_dir,
            record_video=False,
            wall_timeout_s=45.0,
            run_id="kuka-reference-all",
            attempt=0,
        )

    diagnostics = _failure_diagnostics(report)
    assert report["pipeline_completed"], diagnostics
    assert report["physical_validation_executed"], diagnostics
    assert report["validation_passed"], diagnostics
    assert report["passed_task_count"] == len(TASK_IDS), diagnostics
    assert len(report["trials"]) == len(TASK_IDS)
    for trial in report["trials"]:
        assert trial["trial_passed"], diagnostics
        assert trial["measurement_value"] is not None
        evidence = trial["physical_evidence"]
        assert evidence["step_count"] > 0
        assert evidence["ctrl_observed_before_step"]
        assert evidence["ctrl_changed_from_reset"]
        assert evidence["direct_state_write_detected"] is False
        assert all(trial["guard_outcomes"].values()), diagnostics
