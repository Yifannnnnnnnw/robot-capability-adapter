from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.harness import run_private_suite
from autoadapter2.harness.session import apply_framework_reset
from autoadapter2.libraries import load_robot_package
from autoadapter2.trusted_skeletons import ArmSerialDLSSkeleton, ArmSpec, IKUnreachableError


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "franka_panda" / "1.0.0"
SO101_ROOT = ROOT / "libraries" / "robots" / "robotstudio_so101" / "1.0.0"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"
SNAPSHOT_ID = "franka-panda-metaworld-source-protocols-2026-08-19-v1"
ARM_JOINTS = tuple(f"joint{index}" for index in range(1, 8))
ARM_ACTUATORS = tuple(f"actuator{index}" for index in range(1, 8))
PANDA_HOME_JOINT_POSITIONS = {
    "joint1": 0.0,
    "joint2": 0.0,
    "joint3": 0.0,
    "joint4": -1.57079,
    "joint5": 0.0,
    "joint6": 1.57079,
    "joint7": -0.7853,
    "finger_joint1": 0.04,
    "finger_joint2": 0.04,
}
PANDA_HOME_ACTUATOR_CONTROLS = {
    "actuator1": 0.0,
    "actuator2": 0.0,
    "actuator3": 0.0,
    "actuator4": -1.57079,
    "actuator5": 0.0,
    "actuator6": 1.57079,
    "actuator7": -0.7853,
    "actuator8": 255.0,
}
FIXTURE_RESET_JOINT_POSITIONS = {
    "mw_drawer_close": {"drawer_slide": -0.08},
    "mw_handle_pull": {"vertical_handle_slide": -0.05},
    "mw_door_close": {"door_hinge": 1.2},
}


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
        ("rotation", "rotation_task", ["mw_faucet_open", "mw_dial_turn", "mw_lever_pull"]),
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


def _reach_suite(package: object, design: dict) -> dict:
    instances = _read(package.private_dir / "instances.json")["instances"]  # type: ignore[attr-defined]
    instance = next(item for item in instances if item["task_id"] == "mw_reach_target")
    task = next(item for item in package.tasks if item["task_id"] == "mw_reach_target")  # type: ignore[attr-defined]
    clause = task["scoring"][0]
    return {
        "cases": [
            {
                "case_id": "case-mw_reach_target",
                "capability_id": "reach",
                "method_name": "reach_task",
                "task_id": "mw_reach_target",
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
        ]
    }


def test_franka_private_package_loads_and_preserves_boundary() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    assert package.robot_configuration_id == "franka_panda"
    assert package.package_version == "1.0.0"
    assert package.snapshot_id == SNAPSHOT_ID
    assert len(package.tasks) == 20

    instances = _read(PACKAGE_ROOT / "tasks/private/instances.json")
    bindings = _read(PACKAGE_ROOT / "tasks/private/bindings.json")
    guards = _read(PACKAGE_ROOT / "tasks/private/guards.json")
    assert len(instances["instances"]) == 20
    assert len(bindings["bindings"]) == 20
    assert len(guards["guards"]) == 4
    for document in (instances, bindings, guards):
        assert document["robot_configuration_id"] == "franka_panda"
        assert document["package_version"] == "1.0.0"
        assert document["task_snapshot_id"] == SNAPSHOT_ID

    private_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            PACKAGE_ROOT / "tasks/private/instances.json",
            PACKAGE_ROOT / "tasks/private/bindings.json",
            PACKAGE_ROOT / "tasks/private/guards.json",
            PACKAGE_ROOT / "skeleton/arm_serial_dls.py",
            PACKAGE_ROOT / "reference/driver.py",
        )
    ).lower()
    assert "so101" not in private_text
    assert "so-101" not in private_text
    assert all(
        (PACKAGE_ROOT / instance["scene_entrypoint"]).is_file()
        for instance in instances["instances"]
    )
    assert all(instance["reset"]["kind"] == "default" for instance in instances["instances"])
    assert all(instance["max_steps"] == 10000 for instance in instances["instances"])
    assert all(instance["timeout_sim_s"] == 20.0 for instance in instances["instances"])


def test_franka_private_records_are_the_mechanical_so101_transform() -> None:
    template = _read(SO101_ROOT / "tasks/private/instances.json")["instances"]
    actual = _read(PACKAGE_ROOT / "tasks/private/instances.json")["instances"]
    assert len(actual) == len(template) == 20
    position_deltas = {
        "contact_position": np.asarray((0.10, 0.0, 0.33)),
        "grasp_position": np.asarray((0.10, 0.0, 0.33)),
        "route_position": np.asarray((0.10, 0.0, 0.33)),
        "release_position": np.asarray((0.10, 0.0, 0.33)),
        "tool_target_position": np.asarray((0.10, 0.0, 0.33)),
    }
    for expected, observed in zip(template, actual):
        assert observed["task_id"] == expected["task_id"]
        assert observed["instance_id"] == expected["instance_id"].replace("so101-", "franka-")
        assert observed["scene_entrypoint"] == expected["scene_entrypoint"]
        expected_reset = {
            "kind": "default",
            "joint_positions": {
                **PANDA_HOME_JOINT_POSITIONS,
                **expected["reset"].get("joint_positions", {}),
            },
            "actuator_controls": PANDA_HOME_ACTUATOR_CONTROLS,
        }
        assert observed["reset"] == expected_reset
        assert observed["max_steps"] == 10000
        assert observed["timeout_sim_s"] == expected["timeout_sim_s"] == 20.0
        expected_params = expected["public_arguments"]["request"]["task_parameters"]
        observed_params = observed["public_arguments"]["request"]["task_parameters"]
        for key, delta in position_deltas.items():
            if key in expected_params:
                np.testing.assert_allclose(observed_params[key], np.asarray(expected_params[key]) + delta)
        if "start_position" in expected_params:
            np.testing.assert_allclose(
                observed_params["start_position"],
                np.asarray(expected_params["start_position"]) + np.asarray((0.10, 0.0, 0.25)),
            )
        if "target_position" in expected_params:
            delta = (0.10, 0.0, 0.33) if expected["task_id"] == "mw_reach_target" else (0.10, 0.0, 0.25)
            np.testing.assert_allclose(observed_params["target_position"], np.asarray(expected_params["target_position"]) + delta)
        if "grasp_gripper" in expected_params:
            expected_native = (expected_params["grasp_gripper"] + 0.17453) / (1.74533 + 0.17453) * 255.0
            assert observed_params["grasp_gripper"] == expected_native
        for key in set(expected_params) - set(position_deltas) - {"start_position", "target_position", "grasp_gripper"}:
            assert observed_params[key] == expected_params[key]

    template_bindings = _read(SO101_ROOT / "tasks/private/bindings.json")["bindings"]
    actual_bindings = _read(PACKAGE_ROOT / "tasks/private/bindings.json")["bindings"]
    for expected, observed in zip(template_bindings, actual_bindings):
        if expected["binding_id"] != "binding-mw_reach_target":
            assert observed == expected
            continue
        expected = copy.deepcopy(expected)
        expected["kind"] = "final_body_position_error"
        expected["parameters"].pop("site_name")
        expected["parameters"]["body_name"] = "hand"
        assert observed == expected


def test_franka_private_resets_preserve_panda_home_and_scene_qpos0() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    instances = _read(package.private_dir / "instances.json")["instances"]

    for instance in instances:
        scene = (package.root / instance["scene_entrypoint"]).resolve()
        model = mujoco.MjModel.from_xml_path(str(scene))
        data = mujoco.MjData(model)
        model_qpos0 = np.array(model.qpos0, dtype=float, copy=True)

        apply_framework_reset(mujoco, model, data, instance["reset"])

        for name, expected_value in PANDA_HOME_JOINT_POSITIONS.items():
            joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            assert joint_id >= 0
            qpos_address = int(model.jnt_qposadr[joint_id])
            np.testing.assert_allclose(
                data.qpos[qpos_address],
                expected_value,
                rtol=0.0,
                atol=0.0,
                err_msg=f"unexpected Panda qpos for {instance['task_id']}:{name}",
            )

        for name, expected_value in PANDA_HOME_ACTUATOR_CONTROLS.items():
            actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            assert actuator_id >= 0
            np.testing.assert_allclose(
                data.ctrl[actuator_id],
                expected_value,
                rtol=0.0,
                atol=0.0,
                err_msg=f"unexpected Panda ctrl for {instance['task_id']}:{name}",
            )

        for joint_id in range(int(model.njnt)):
            if int(model.jnt_type[joint_id]) != int(mujoco.mjtJoint.mjJNT_FREE):
                continue
            qpos_address = int(model.jnt_qposadr[joint_id])
            np.testing.assert_array_equal(
                data.qpos[qpos_address : qpos_address + 7],
                model_qpos0[qpos_address : qpos_address + 7],
                err_msg=(
                    f"free-joint qpos changed for {instance['task_id']} "
                    f"at qpos address {qpos_address}"
                ),
            )

        fixture_positions = FIXTURE_RESET_JOINT_POSITIONS.get(instance["task_id"], {})
        for name, expected_value in fixture_positions.items():
            assert instance["reset"]["joint_positions"][name] == expected_value
            joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            assert joint_id >= 0
            qpos_address = int(model.jnt_qposadr[joint_id])
            np.testing.assert_allclose(
                data.qpos[qpos_address],
                expected_value,
                rtol=0.0,
                atol=0.0,
                err_msg=f"fixture reset was not effective for {instance['task_id']}:{name}",
            )


def test_franka_driver_source_and_skeleton_contract() -> None:
    driver_path = PACKAGE_ROOT / "reference/driver.py"
    source = driver_path.read_text(encoding="utf-8")
    audit = audit_driver_source(
        source,
        condition="from-scratch",
        capability_methods=("reach_task", "contact_task", "object_task", "fixture_task", "rotation_task"),
    )
    assert audit.ctrl_references > 0
    assert audit.physics_step_references > 0
    assert not audit.imports_trusted_skeleton
    assert "mujoco.mj_jacBody" in source
    assert "data.xpos" in source
    assert "data.xmat" in source
    assert "data.site_xpos" not in source
    assert "mujoco.mj_reset" not in source
    assert "MjModel" not in source
    assert "MjData" not in source

    skeleton_path = PACKAGE_ROOT / "skeleton/arm_serial_dls.py"
    spec = importlib.util.spec_from_file_location("franka_package_skeleton", skeleton_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ArmSerialDLSSkeleton is ArmSerialDLSSkeleton
    assert module.ArmSpec is ArmSpec
    assert module.IKUnreachableError is IKUnreachableError

    model = mujoco.MjModel.from_xml_path(str(PACKAGE_ROOT / "assets/reach_scene.xml"))
    data = mujoco.MjData(model)
    limits = {
        name: model.jnt_range[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)].tolist()
        for name in ARM_JOINTS
    }
    skeleton = ArmSerialDLSSkeleton.from_session(
        model=model,
        data=data,
        spec=ArmSpec(
            ee_body_name="hand",
            arm_joint_names=ARM_JOINTS,
            arm_actuator_names=ARM_ACTUATORS,
            joint_limits=limits,
            gripper_actuator_names=("actuator8",),
            gripper_close_ctrl=0.0,
            gripper_open_ctrl=255.0,
        ),
    )
    skeleton.get_ee_pose()
    assert skeleton._ee_body_id == mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "hand")
    assert len(skeleton._arm_qpos_adr) == 7
    assert len(skeleton._arm_actuator_ids) == 7
    assert skeleton._gripper_actuator_ids == [7]


def test_franka_real_private_harness_reach_positive_control() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    suite = _reach_suite(package, design)
    with tempfile.TemporaryDirectory(prefix="franka-reach-calibration-") as output_dir:
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=PACKAGE_ROOT / "reference/driver.py",
            condition="from-scratch",
            output_dir=output_dir,
            record_video=False,
            wall_timeout_s=30.0,
            run_id="franka-reach-calibration",
            attempt=0,
        )
    assert report["validation_passed"] is True
    trial = report["trials"][0]
    assert trial["trial_passed"] is True
    assert trial["physical_execution_passed"] is True
    evidence = trial["physical_evidence"]
    assert evidence["ctrl_observed_before_step"] is True
    assert evidence["ctrl_changed_from_reset"] is True
    assert evidence["step_count"] > 0
    assert evidence["direct_state_write_detected"] is False
    assert all(trial["guard_outcomes"].values())
    assert trial["measurement_value"] <= 0.05


def test_franka_remains_non_runtime_until_full_follow_up_evidence() -> None:
    runnable_index = _read(RUNNABLE_INDEX_PATH)
    assert "franka_panda" not in runnable_index["robots"]
    research_index = _read(RESEARCH_INDEX_PATH)
    candidate = next(item for item in research_index["candidates"] if item["robot_configuration_id"] == "franka_panda")
    missing = candidate["missing_for_runnable_package"]
    assert any("full 20-task Direct-MuJoCo positive control" in item for item in missing)
    assert any("dynamic canary" in item for item in missing)
    assert any("runnable index" in item for item in missing)
