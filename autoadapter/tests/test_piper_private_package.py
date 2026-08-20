from __future__ import annotations

import copy
import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.harness.measurements import compare, measure
from autoadapter2.harness.session import TrackedMuJoCoSession, apply_framework_reset
from autoadapter2.libraries.robot_package import (
    _validate_private_inputs,
    _validate_sources,
    _validate_tasks,
)
from autoadapter2.trusted_skeletons import ArmSerialDLSSkeleton, ArmSpec


ROOT = Path(__file__).resolve().parents[1]
PIPER_PACKAGE_ROOT = ROOT / "libraries" / "robots" / "piper" / "1.0.0"
PIPER_TASKS_ROOT = PIPER_PACKAGE_ROOT / "tasks"
PIPER_PRIVATE_ROOT = PIPER_TASKS_ROOT / "private"
PIPER_ASSETS_ROOT = PIPER_PACKAGE_ROOT / "assets"
XARM_PRIVATE_ROOT = (
    ROOT / "libraries" / "robots" / "ufactory_xarm7" / "1.0.0" / "tasks" / "private"
)
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"

ROBOT_CONFIGURATION_ID = "piper"
PACKAGE_VERSION = "1.0.0"
SNAPSHOT_ID = "piper-metaworld-source-protocols-2026-08-20-v1"
EXPECTED_TASK_IDS = [
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
    "mw_peg_insertion_side",
    "mw_bin_picking",
    "mw_pick_out_of_hole",
]
XARM_ROBOT_JOINTS = {
    "joint1",
    "joint2",
    "joint3",
    "joint4",
    "joint5",
    "joint6",
    "joint7",
    "left_driver_joint",
    "left_finger_joint",
    "left_inner_knuckle_joint",
    "right_driver_joint",
    "right_finger_joint",
    "right_inner_knuckle_joint",
}
PIPER_RESET_JOINTS = {
    "joint1": 0.0,
    "joint2": 1.57,
    "joint3": -1.3485,
    "joint4": 0.0,
    "joint5": 0.0,
    "joint6": 0.0,
    "joint7": 0.035,
    "joint8": -0.035,
}
PIPER_ACTUATOR_CONTROLS = {
    "joint1": 0.0,
    "joint2": 1.57,
    "joint3": -1.3485,
    "joint4": 0.0,
    "joint5": 0.0,
    "joint6": 0.0,
    "gripper": 0.035,
}
ARM_JOINTS = tuple(f"joint{index}" for index in range(1, 7))
HOME_ARM = (0.0, 1.57, -1.3485, 0.0, 0.0, 0.0)
EE_WAYPOINT_KEYS = {
    "contact_position",
    "grasp_position",
    "release_position",
    "route_position",
    "tool_target_position",
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _transform_identity(template: dict) -> dict:
    transformed = copy.deepcopy(template)
    transformed["robot_configuration_id"] = ROBOT_CONFIGURATION_ID
    transformed["task_snapshot_id"] = SNAPSHOT_ID
    return transformed


def _transform_instances(template: dict) -> dict:
    transformed = _transform_identity(template)
    for instance in transformed["instances"]:
        instance["instance_id"] = instance["instance_id"].replace("xarm7-", "piper-", 1)
        parameters = instance["public_arguments"]["request"]["task_parameters"]
        if "grasp_gripper" in parameters:
            parameters["grasp_gripper"] = 0.0

        fixture_positions = {
            name: value
            for name, value in instance["reset"]["joint_positions"].items()
            if name not in XARM_ROBOT_JOINTS
        }
        instance["reset"]["joint_positions"] = {
            **PIPER_RESET_JOINTS,
            **fixture_positions,
        }
        instance["reset"]["actuator_controls"] = copy.deepcopy(
            PIPER_ACTUATOR_CONTROLS
        )
    return transformed


def test_piper_private_documents_pass_canonical_validators() -> None:
    sources_path = PIPER_TASKS_ROOT / "sources.json"
    catalog_path = PIPER_TASKS_ROOT / "catalog.json"
    sources = _validate_sources(_read(sources_path), path=sources_path)
    tasks = _validate_tasks(
        _read(catalog_path),
        path=catalog_path,
        source_ids={str(source["source_id"]) for source in sources},
    )

    _validate_private_inputs(
        package_root=PIPER_PACKAGE_ROOT,
        private_dir=PIPER_PRIVATE_ROOT,
        robot_configuration_id=ROBOT_CONFIGURATION_ID,
        package_version=PACKAGE_VERSION,
        snapshot_id=SNAPSHOT_ID,
        tasks=tasks,
    )


def test_piper_private_documents_are_exact_xarm_mechanical_transforms() -> None:
    source_instances = _read(XARM_PRIVATE_ROOT / "instances.json")
    actual_instances = _read(PIPER_PRIVATE_ROOT / "instances.json")
    assert actual_instances == _transform_instances(source_instances)
    assert actual_instances["robot_configuration_id"] == ROBOT_CONFIGURATION_ID
    assert actual_instances["package_version"] == PACKAGE_VERSION
    assert actual_instances["task_snapshot_id"] == SNAPSHOT_ID
    assert len(actual_instances["instances"]) == 20
    assert [item["task_id"] for item in actual_instances["instances"]] == EXPECTED_TASK_IDS
    assert [item["instance_id"] for item in actual_instances["instances"]] == [
        f"piper-{task_id}" for task_id in EXPECTED_TASK_IDS
    ]

    for source, actual in zip(source_instances["instances"], actual_instances["instances"]):
        assert actual["guard_ids"] == source["guard_ids"]

    grasp_instances = [
        item
        for item in actual_instances["instances"]
        if "grasp_gripper" in item["public_arguments"]["request"]["task_parameters"]
    ]
    assert len(grasp_instances) == 4
    assert all(
        item["public_arguments"]["request"]["task_parameters"]["grasp_gripper"] == 0.0
        for item in grasp_instances
    )

    source_bindings = _read(XARM_PRIVATE_ROOT / "bindings.json")
    expected_bindings = _transform_identity(source_bindings)
    next(
        binding
        for binding in expected_bindings["bindings"]
        if binding["binding_id"] == "binding-mw_reach_target"
    )["parameters"]["site_name"] = "ee_site"
    actual_bindings = _read(PIPER_PRIVATE_ROOT / "bindings.json")
    assert actual_bindings == expected_bindings
    assert len(actual_bindings["bindings"]) == 20

    source_guards = _read(XARM_PRIVATE_ROOT / "guards.json")
    actual_guards = _read(PIPER_PRIVATE_ROOT / "guards.json")
    assert actual_guards == _transform_identity(source_guards)
    assert len(actual_guards["guards"]) == 4
    assert [guard["guard_id"] for guard in actual_guards["guards"]] == [
        guard["guard_id"] for guard in source_guards["guards"]
    ]


def test_piper_private_scenes_and_resets_resolve_in_mujoco() -> None:
    instances = _read(PIPER_PRIVATE_ROOT / "instances.json")["instances"]
    for instance in instances:
        scene_path = (PIPER_PACKAGE_ROOT / instance["scene_entrypoint"]).resolve()
        assert scene_path.is_file()
        assert scene_path.is_relative_to(PIPER_ASSETS_ROOT.resolve())

        model = mujoco.MjModel.from_xml_path(str(scene_path))
        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, instance["reset"])

        for name, expected in instance["reset"]["joint_positions"].items():
            joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            assert joint_id >= 0, f"{instance['task_id']}: {name}"
            address = int(model.jnt_qposadr[joint_id])
            assert data.qpos[address] == expected
        for name, expected in instance["reset"]["actuator_controls"].items():
            actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            assert actuator_id >= 0, f"{instance['task_id']}: {name}"
            assert data.ctrl[actuator_id] == expected


def test_piper_private_video_and_camera_settings_remain_canonical() -> None:
    instances = _read(PIPER_PRIVATE_ROOT / "instances.json")["instances"]
    for instance in instances:
        assert instance["repetitions"] == 1
        assert instance["timeout_sim_s"] == 20.0
        assert instance["max_steps"] == 10000
        assert instance["sample_hz"] == 20.0
        assert instance["video_width"] == 800
        assert instance["video_height"] == 600
        assert instance["video_fps"] == 10.0
        assert instance["camera"] == "evidence"


def test_piper_private_reset_fails_every_task_criterion() -> None:
    instances = _read(PIPER_PRIVATE_ROOT / "instances.json")["instances"]
    bindings = _read(PIPER_PRIVATE_ROOT / "bindings.json")["bindings"]
    tasks = _read(PIPER_TASKS_ROOT / "catalog.json")["tasks"]
    binding_by_id = {item["binding_id"]: item for item in bindings}
    task_by_id = {item["task_id"]: item for item in tasks}

    checked = 0
    for instance in instances:
        model = mujoco.MjModel.from_xml_path(
            str(PIPER_PACKAGE_ROOT / instance["scene_entrypoint"])
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

    assert checked == 20


def test_piper_public_ee_waypoints_and_grasp_rolls_are_reachable() -> None:
    model = mujoco.MjModel.from_xml_path(str(PIPER_ASSETS_ROOT / "scene.xml"))
    data = mujoco.MjData(model)
    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    morphology = _read(PIPER_PACKAGE_ROOT / "morphology.json")
    skeleton = ArmSerialDLSSkeleton(
        model=model,
        data=data,
        spec=ArmSpec(
            ee_site_name="ee_site",
            arm_joint_names=ARM_JOINTS,
            arm_actuator_names=ARM_JOINTS,
            joint_limits=morphology["public_control"]["arm_joint_limits_rad"],
            home_qpos=HOME_ARM,
            ik_max_iter=500,
            ik_tolerance=0.003,
            ik_step_clamp=0.15,
            gripper_actuator_names=("gripper",),
            gripper_close_ctrl=0.0,
            gripper_open_ctrl=0.035,
        ),
    )

    instances = _read(PIPER_PRIVATE_ROOT / "instances.json")["instances"]
    checked = 0
    for instance in instances:
        parameters = instance["public_arguments"]["request"]["task_parameters"]
        keys = set(EE_WAYPOINT_KEYS)
        if instance["task_id"] == "mw_reach_target":
            keys.add("target_position")
        for key in sorted(keys & parameters.keys()):
            target = np.asarray(parameters[key], dtype=float)
            q = skeleton.ik(target, q_init=HOME_ARM)
            residual = float(np.linalg.norm(skeleton.fk(q)["pos"] - target))
            assert residual <= 0.003, f"{instance['task_id']}:{key}"
            checked += 1
    assert checked == 53

    grasp_instances = [
        item
        for item in instances
        if "grasp_wrist_roll"
        in item["public_arguments"]["request"]["task_parameters"]
    ]
    assert len(grasp_instances) == 4
    for instance in grasp_instances:
        parameters = instance["public_arguments"]["request"]["task_parameters"]
        target = np.asarray(parameters["grasp_position"], dtype=float)
        wrist_roll = float(parameters["grasp_wrist_roll"])
        mujoco.mj_resetDataKeyframe(model, data, home_id)
        mujoco.mj_forward(model, data)
        skeleton.move_cartesian(
            target,
            duration=3.0,
            wrist_roll=wrist_roll,
            max_joint_delta=0.08,
            residual_tolerance=0.05,
        )
        position, _ = skeleton.get_ee_pose()
        assert np.linalg.norm(position - target) <= 0.003
        assert abs(float(skeleton.get_joint_positions()[-1]) - wrist_roll) <= 0.011


def test_piper_private_package_remains_non_runtime_with_reference() -> None:
    runnable_index = _read(RUNNABLE_INDEX_PATH)
    assert "piper" not in runnable_index["robots"]
    assert (PIPER_PACKAGE_ROOT / "skeleton" / "arm_serial_dls.py").is_file()
    assert (PIPER_PACKAGE_ROOT / "reference" / "driver.py").is_file()
