from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile

import mujoco
import numpy as np
import pytest

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.harness import run_private_suite
from autoadapter2.harness.measurements import compare, measure
from autoadapter2.harness.session import TrackedMuJoCoSession, apply_framework_reset
from autoadapter2.libraries import load_robot_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "aloha_2" / "1.0.0"
DRIVER_PATH = PACKAGE_ROOT / "reference" / "driver.py"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
SNAPSHOT_ID = "aloha-2-metaworld-source-protocols-2026-08-20-v2"
TASK_IDS = (
    "mw_reach_target",
    "mw_push_to_goal",
    "mw_pick_place",
    "mw_pick_place_wall",
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
    ("object", "object_task", ("mw_pick_place", "mw_pick_place_wall")),
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
FINGER_GEOMS = (
    "right/left_finger_collision",
    "right/right_finger_collision",
    "right/left_g0",
    "right/left_g1",
    "right/left_g2",
    "right/right_g0",
    "right/right_g1",
    "right/right_g2",
)
CONTACT_TASK_GEOMS = {
    "mw_push_to_goal": ("workpiece_geom",),
    "mw_pick_place": ("workpiece_geom",),
    "mw_pick_place_wall": ("workpiece_geom",),
    "mw_push_wall": ("workpiece_geom",),
    "mw_sweep_into_goal": ("workpiece_geom",),
    "mw_drawer_open": ("drawer_handle_geom",),
    "mw_drawer_close": ("drawer_handle_geom",),
    "mw_button_press": ("front_button_geom",),
    "mw_button_press_topdown": ("top_button_geom",),
    "mw_handle_press": ("vertical_handle_geom",),
    "mw_handle_pull": ("vertical_handle_geom",),
    "mw_door_open": ("door_handle_geom",),
    "mw_door_close": ("door_handle_geom",),
    "mw_faucet_open": ("faucet_tip_geom", "faucet_arm"),
    "mw_dial_turn": ("dial_tip_geom", "dial_face"),
    "mw_lever_pull": ("lever_tip_geom", "lever_arm"),
    "mw_soccer": ("soccer_ball_geom",),
    "mw_window_open": ("window_handle_geom",),
    "mw_window_close": ("window_handle_geom",),
}
BINDING_ENTITIES = {
    "mw_reach_target": ("final_site_position_error", "site_name", "right/gripper"),
    "mw_push_to_goal": ("final_body_position_error", "body_name", "workpiece"),
    "mw_pick_place": ("final_body_position_error", "body_name", "workpiece"),
    "mw_pick_place_wall": ("final_body_position_error", "body_name", "workpiece"),
    "mw_push_wall": ("final_body_position_error", "body_name", "workpiece"),
    "mw_sweep_into_goal": ("final_body_position_error", "body_name", "workpiece"),
    "mw_drawer_open": ("final_site_position_error", "site_name", "drawer_handle_site"),
    "mw_drawer_close": ("final_site_position_error", "site_name", "drawer_handle_site"),
    "mw_button_press": ("final_site_axis_error", "site_name", "front_button_site"),
    "mw_button_press_topdown": ("final_site_axis_error", "site_name", "top_button_site"),
    "mw_handle_press": ("final_site_axis_error", "site_name", "vertical_handle_site"),
    "mw_handle_pull": ("final_site_axis_error", "site_name", "vertical_handle_site"),
    "mw_door_open": ("final_site_axis_error", "site_name", "door_handle_site"),
    "mw_door_close": ("final_site_position_error", "site_name", "door_handle_site"),
    "mw_faucet_open": ("final_site_position_error", "site_name", "faucet_tip_site"),
    "mw_dial_turn": ("final_site_position_error", "site_name", "dial_tip_site"),
    "mw_lever_pull": ("final_joint_position_error", "joint_name", "lever_hinge"),
    "mw_soccer": ("final_body_position_error", "body_name", "soccer_ball"),
    "mw_window_open": ("final_site_axis_error", "site_name", "window_handle_site"),
    "mw_window_close": ("final_site_axis_error", "site_name", "window_handle_site"),
}
FIXTURE_RESETS = {
    "mw_drawer_close": {"drawer_slide": -0.08},
    "mw_handle_pull": {"vertical_handle_slide": -0.055},
    "mw_door_close": {"door_hinge": 1.2},
    "mw_window_close": {"window_slide": 0.1},
}
NEUTRAL_JOINT_POSITIONS = {
    "left/waist": 0.0,
    "left/shoulder": -0.96,
    "left/elbow": 1.16,
    "left/forearm_roll": 0.0,
    "left/wrist_angle": -0.3,
    "left/wrist_rotate": 0.0,
    "left/left_finger": 0.0084,
    "left/right_finger": 0.0084,
    "right/waist": 0.0,
    "right/shoulder": -0.96,
    "right/elbow": 1.16,
    "right/forearm_roll": 0.0,
    "right/wrist_angle": -0.3,
    "right/wrist_rotate": 0.0,
    "right/left_finger": 0.0084,
    "right/right_finger": 0.0084,
}
NEUTRAL_ACTUATOR_CONTROLS = {
    "left/waist": 0.0,
    "left/shoulder": -0.96,
    "left/elbow": 1.16,
    "left/forearm_roll": 0.0,
    "left/wrist_angle": -0.3,
    "left/wrist_rotate": 0.0,
    "left/gripper": 0.0084,
    "right/waist": 0.0,
    "right/shoulder": -0.96,
    "right/elbow": 1.16,
    "right/forearm_roll": 0.0,
    "right/wrist_angle": -0.3,
    "right/wrist_rotate": 0.0,
    "right/gripper": 0.0084,
}
FREE_BODY_STARTS = {
    "mw_push_to_goal": ("workpiece", (0.2, 0.10, 0.006)),
    "mw_pick_place": ("workpiece", (0.2, 0.08, 0.02)),
    "mw_pick_place_wall": ("workpiece", (0.2, 0.08, 0.02)),
    "mw_push_wall": ("workpiece", (0.18, 0.08, 0.02)),
    "mw_sweep_into_goal": ("workpiece", (0.2, 0.08, 0.015)),
    "mw_soccer": ("soccer_ball", (0.2, 0.08, 0.03)),
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _design(package: object) -> dict:
    tasks = {task["task_id"]: task for task in package.tasks}  # type: ignore[attr-defined]
    capabilities = []
    for capability_id, method_name, task_ids in CAPABILITY_GROUPS:
        task = tasks[task_ids[0]]
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
    failures = []
    for trial in report.get("trials", []):
        if trial.get("trial_passed"):
            continue
        samples = trial.get("physical_evidence", {}).get("samples", [])
        final = samples[-1] if samples else {}
        failures.append(
            {
                "task_id": trial["task_id"],
                "measurement": trial.get("measurement_value"),
                "candidate_exception": trial.get("candidate_exception"),
                "guard_outcomes": trial.get("guard_outcomes"),
                "final_sites": final.get("site_positions"),
                "final_bodies": final.get("body_positions"),
                "final_joints": final.get("joint_positions"),
                "final_contacts": final.get("contacts"),
            }
        )
    return json.dumps(failures, indent=2, sort_keys=True)


def test_aloha_2_private_package_loads_without_runtime_admission() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    assert package.robot_configuration_id == "aloha_2"
    assert package.package_version == "1.0.0"
    assert package.snapshot_id == SNAPSHOT_ID
    assert tuple(task["task_id"] for task in package.tasks) == TASK_IDS

    instances = _read(package.private_dir / "instances.json")
    bindings = _read(package.private_dir / "bindings.json")
    guards = _read(package.private_dir / "guards.json")
    assert len(instances["instances"]) == 20
    assert len(bindings["bindings"]) == 20
    assert len(guards["guards"]) == 23
    for document in (instances, bindings, guards):
        assert document["robot_configuration_id"] == "aloha_2"
        assert document["package_version"] == "1.0.0"
        assert document["task_snapshot_id"] == SNAPSHOT_ID

    assert "aloha_2" not in _read(RUNNABLE_INDEX_PATH)["robots"]


def test_aloha_2_bindings_guards_and_selected_arm_are_exact() -> None:
    bindings = {
        binding["binding_id"].removeprefix("binding-"): binding
        for binding in _read(PACKAGE_ROOT / "tasks/private/bindings.json")["bindings"]
    }
    for task_id, (kind, entity_key, entity_name) in BINDING_ENTITIES.items():
        binding = bindings[task_id]
        assert binding["kind"] == kind
        assert binding["parameters"][entity_key] == entity_name
        target = (
            "request.task_parameters.target_angle"
            if task_id == "mw_lever_pull"
            else "request.task_parameters.target_position"
        )
        assert binding["parameters"]["target_argument"] == target

    guards = {
        guard["guard_id"]: guard
        for guard in _read(PACKAGE_ROOT / "tasks/private/guards.json")["guards"]
    }
    neutral = guards["guard_other_arm_neutral"]
    assert neutral["kind"] == "named_joints_remain_near_reset"
    assert set(neutral["joint_tolerances"]) == {
        "left/waist",
        "left/shoulder",
        "left/elbow",
        "left/forearm_roll",
        "left/wrist_angle",
        "left/wrist_rotate",
        "left/left_finger",
        "left/right_finger",
    }
    contact_guards = {
        guard_id.removeprefix("guard-contact-"): guard
        for guard_id, guard in guards.items()
        if guard["kind"] == "named_geom_contact_pair_required"
    }
    assert set(contact_guards) == set(CONTACT_TASK_GEOMS)
    for task_id, task_geoms in CONTACT_TASK_GEOMS.items():
        guard = contact_guards[task_id]
        assert tuple(guard["robot_geom_names"]) == FINGER_GEOMS
        assert "robot_geom_name" not in guard
        assert tuple(guard["task_geom_names"]) == task_geoms
        assert guard["minimum_steps"] == 1

    instances = _read(PACKAGE_ROOT / "tasks/private/instances.json")["instances"]
    for instance in instances:
        task_id = instance["task_id"]
        assert instance["public_arguments"]["request"]["task_parameters"]["task_arm"] == "right"
        assert "guard_other_arm_neutral" in instance["guard_ids"]
        contact_id = f"guard-contact-{task_id}"
        assert (contact_id in instance["guard_ids"]) == (task_id != "mw_reach_target")


def test_aloha_2_private_resets_are_framework_owned_and_initially_fail() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    instances = _read(package.private_dir / "instances.json")["instances"]
    bindings = {
        binding["binding_id"]: binding
        for binding in _read(package.private_dir / "bindings.json")["bindings"]
    }
    tasks = {task["task_id"]: task for task in package.tasks}

    for instance in instances:
        task_id = instance["task_id"]
        if task_id in FREE_BODY_STARTS:
            reset = {
                "kind": "default",
                "joint_positions": NEUTRAL_JOINT_POSITIONS,
                "actuator_controls": NEUTRAL_ACTUATOR_CONTROLS,
            }
        else:
            reset = {"kind": "keyframe", "name": "neutral_pose"}
        if task_id in FIXTURE_RESETS:
            reset["joint_positions"] = FIXTURE_RESETS[task_id]
        assert instance["reset"] == reset
        assert instance["timeout_sim_s"] == 20
        assert instance["max_steps"] == 10000
        assert instance["sample_hz"] == 20
        assert instance["camera"] == "evidence"

        request = instance["public_arguments"]["request"]
        required = tasks[task_id]["invocation_schema"]["request"]["task_parameters"]["required"]
        assert set(request["task_parameters"]) == set(required)

        model = mujoco.MjModel.from_xml_path(
            str(package.root / instance["scene_entrypoint"])
        )
        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, instance["reset"])
        if task_id in FREE_BODY_STARTS:
            body_name, expected_position = FREE_BODY_STARTS[task_id]
            body_id = mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_BODY, body_name
            )
            np.testing.assert_allclose(
                data.xpos[body_id], expected_position, rtol=0.0, atol=1e-12
            )
        tracker = TrackedMuJoCoSession(
            mujoco=mujoco,
            model=model,
            data=data,
            max_steps=1,
            max_sim_time_s=1.0,
            sample_hz=20.0,
        )
        task = tasks[task_id]
        for clause_id, binding_id in instance["clause_bindings"].items():
            criterion = next(
                clause for clause in task["scoring"] if clause["clause_id"] == clause_id
            )
            value = measure(
                bindings[binding_id],
                evidence={"samples": [tracker.snapshot()], "step_count": 0},
                public_arguments=instance["public_arguments"],
            )
            assert not compare(
                value,
                comparator=criterion["comparator"],
                threshold=criterion["threshold"],
            ), f"reset already passes {task_id}: value={value}"


def test_aloha_2_reference_driver_is_right_arm_actuator_only() -> None:
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
    assert "left/" not in source

    spec = importlib.util.spec_from_file_location("aloha_2_reference_driver", DRIVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    model = mujoco.MjModel.from_xml_path(str(PACKAGE_ROOT / "assets/reach_scene.xml"))
    data = mujoco.MjData(model)
    driver = module.build(model=model, data=data)
    with pytest.raises(ValueError, match="select the right arm"):
        driver.reach_task(
            request={
                "task_id": "mw_reach_target",
                "task_parameters": {
                    "task_arm": "left",
                    "target_position": [-0.2, 0.1, 0.2],
                },
            }
        )


def test_aloha_2_reference_reach_passes_real_private_harness() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    with tempfile.TemporaryDirectory(prefix="aloha-2-reference-reach-") as output_dir:
        report = run_private_suite(
            package=package,
            design=design,
            suite=_suite(package, design, ("mw_reach_target",)),
            driver_path=DRIVER_PATH,
            condition="from-scratch",
            output_dir=output_dir,
            record_video=False,
            wall_timeout_s=30.0,
            run_id="aloha-2-reference-reach",
            attempt=0,
        )

    diagnostics = _failure_diagnostics(report)
    assert report["pipeline_completed"], diagnostics
    assert report["physical_validation_executed"], diagnostics
    assert report["validation_passed"], diagnostics
    assert report["passed_task_count"] == 1, diagnostics
    trial = report["trials"][0]
    assert trial["trial_passed"], diagnostics
    assert trial["measurement_value"] <= 0.05
    evidence = trial["physical_evidence"]
    assert evidence["step_count"] > 0
    assert evidence["ctrl_observed_before_step"]
    assert evidence["ctrl_changed_from_reset"]
    assert evidence["direct_state_write_detected"] is False
    assert all(trial["guard_outcomes"].values())


def test_aloha_2_reference_all_twenty_tasks_pass_real_private_harness() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    with tempfile.TemporaryDirectory(prefix="aloha-2-reference-all-") as output_dir:
        report = run_private_suite(
            package=package,
            design=design,
            suite=_suite(package, design, TASK_IDS),
            driver_path=DRIVER_PATH,
            condition="from-scratch",
            output_dir=output_dir,
            record_video=False,
            wall_timeout_s=90.0,
            run_id="aloha-2-reference-all",
            attempt=0,
        )

    diagnostics = _failure_diagnostics(report)
    assert report["pipeline_completed"], diagnostics
    assert report["physical_validation_executed"], diagnostics
    assert report["validation_passed"], diagnostics
    assert report["passed_task_count"] == len(TASK_IDS), diagnostics
    for trial in report["trials"]:
        assert trial["trial_passed"], diagnostics
        assert trial["measurement_value"] is not None
        evidence = trial["physical_evidence"]
        assert evidence["step_count"] > 0
        assert evidence["ctrl_observed_before_step"]
        assert evidence["ctrl_changed_from_reset"]
        assert evidence["direct_state_write_detected"] is False
        assert all(trial["guard_outcomes"].values()), diagnostics
