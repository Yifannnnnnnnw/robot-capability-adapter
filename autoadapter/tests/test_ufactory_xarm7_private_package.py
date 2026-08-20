from __future__ import annotations

import copy
import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.harness.session import apply_framework_reset
from autoadapter2.libraries.robot_package import (
    _validate_private_inputs,
    _validate_sources,
    _validate_tasks,
)
from autoadapter2.trusted_skeletons.arm_serial_dls import ArmSerialDLSSkeleton, ArmSpec


ROOT = Path(__file__).resolve().parents[1]
XARM_PACKAGE_ROOT = ROOT / "libraries" / "robots" / "ufactory_xarm7" / "1.0.0"
XARM_TASKS_ROOT = XARM_PACKAGE_ROOT / "tasks"
XARM_PRIVATE_ROOT = XARM_TASKS_ROOT / "private"
FRANKA_PRIVATE_ROOT = (
    ROOT / "libraries" / "robots" / "franka_panda" / "1.0.0" / "tasks" / "private"
)

ROBOT_ID = "ufactory_xarm7"
SNAPSHOT_ID = "ufactory-xarm7-metaworld-source-protocols-2026-08-20-v1"
HOME_ARM = [0.0, -0.5, 0.0, 1.4, 0.0, 0.8, 0.0]
ARM_JOINTS = tuple(f"joint{index}" for index in range(1, 8))
ARM_ACTUATORS = tuple(f"act{index}" for index in range(1, 8))
GRIPPER_JOINTS = (
    "left_driver_joint",
    "left_finger_joint",
    "left_inner_knuckle_joint",
    "right_driver_joint",
    "right_finger_joint",
    "right_inner_knuckle_joint",
)
ROBOT_RESET_JOINTS = {
    **dict(zip(ARM_JOINTS, HOME_ARM)),
    **{name: 0.0 for name in GRIPPER_JOINTS},
}
ROBOT_RESET_CONTROLS = {
    **dict(zip(ARM_ACTUATORS, HOME_ARM)),
    "gripper": 0.0,
}
FRANKA_ROBOT_JOINTS = {*ARM_JOINTS, "finger_joint1", "finger_joint2"}
EE_WAYPOINT_KEYS = {
    "contact_position",
    "grasp_position",
    "release_position",
    "route_position",
    "tool_target_position",
}
ARM_LIMITS = {
    "joint1": (-6.28319, 6.28319),
    "joint2": (-2.059, 2.0944),
    "joint3": (-6.28319, 6.28319),
    "joint4": (-0.19198, 3.927),
    "joint5": (-6.28319, 6.28319),
    "joint6": (-1.69297, 3.14159),
    "joint7": (-6.28319, 6.28319),
}
MIN_RESET_CONTACT_DISTANCE = -0.005


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _transform_instances(template: dict) -> dict:
    transformed = copy.deepcopy(template)
    transformed["robot_configuration_id"] = ROBOT_ID
    transformed["task_snapshot_id"] = SNAPSHOT_ID

    for instance in transformed["instances"]:
        instance["instance_id"] = instance["instance_id"].replace("franka-", "xarm7-", 1)
        parameters = instance["public_arguments"]["request"]["task_parameters"]
        for key in EE_WAYPOINT_KEYS:
            if key in parameters:
                value = parameters[key]
                parameters[key] = [value[0], value[1], value[2] - 0.08]
        if instance["task_id"] == "mw_reach_target":
            value = parameters["target_position"]
            parameters["target_position"] = [value[0], value[1], value[2] - 0.08]
        if "grasp_gripper" in parameters:
            parameters["grasp_gripper"] = 255.0 - parameters["grasp_gripper"]

        fixture_positions = {
            name: value
            for name, value in instance["reset"]["joint_positions"].items()
            if name not in FRANKA_ROBOT_JOINTS
        }
        instance["reset"]["joint_positions"] = {
            **ROBOT_RESET_JOINTS,
            **fixture_positions,
        }
        instance["reset"]["actuator_controls"] = copy.deepcopy(ROBOT_RESET_CONTROLS)
    return transformed


def _transform_private_identity(template: dict) -> dict:
    transformed = copy.deepcopy(template)
    transformed["robot_configuration_id"] = ROBOT_ID
    transformed["task_snapshot_id"] = SNAPSHOT_ID
    return transformed


def test_xarm7_private_records_are_the_reviewed_robot_specific_transform() -> None:
    expected_instances = _transform_instances(_read(FRANKA_PRIVATE_ROOT / "instances.json"))
    actual_instances = _read(XARM_PRIVATE_ROOT / "instances.json")
    assert actual_instances == expected_instances
    assert len(actual_instances["instances"]) == 20

    expected_bindings = _transform_private_identity(
        _read(FRANKA_PRIVATE_ROOT / "bindings.json")
    )
    reach_binding = next(
        item
        for item in expected_bindings["bindings"]
        if item["binding_id"] == "binding-mw_reach_target"
    )
    reach_binding["kind"] = "final_site_position_error"
    reach_binding["parameters"] = {
        "target_argument": "request.task_parameters.target_position",
        "site_name": "link_tcp",
    }
    assert _read(XARM_PRIVATE_ROOT / "bindings.json") == expected_bindings

    expected_guards = _transform_private_identity(_read(FRANKA_PRIVATE_ROOT / "guards.json"))
    assert _read(XARM_PRIVATE_ROOT / "guards.json") == expected_guards

    grasp_controls = [
        instance["public_arguments"]["request"]["task_parameters"]["grasp_gripper"]
        for instance in actual_instances["instances"]
        if "grasp_gripper"
        in instance["public_arguments"]["request"]["task_parameters"]
    ]
    assert len(grasp_controls) == 4
    assert all(0.0 < value < 255.0 for value in grasp_controls)


def test_xarm7_private_records_pass_the_canonical_validators() -> None:
    sources_path = XARM_TASKS_ROOT / "sources.json"
    catalog_path = XARM_TASKS_ROOT / "catalog.json"
    sources = _validate_sources(_read(sources_path), path=sources_path)
    tasks = _validate_tasks(
        _read(catalog_path),
        path=catalog_path,
        source_ids={str(source["source_id"]) for source in sources},
    )
    _validate_private_inputs(
        package_root=XARM_PACKAGE_ROOT,
        private_dir=XARM_PRIVATE_ROOT,
        robot_configuration_id=ROBOT_ID,
        package_version="1.0.0",
        snapshot_id=SNAPSHOT_ID,
        tasks=tasks,
    )


def test_xarm7_framework_resets_resolve_in_every_task_scene() -> None:
    instances = _read(XARM_PRIVATE_ROOT / "instances.json")["instances"]

    for instance in instances:
        scene_path = XARM_PACKAGE_ROOT / instance["scene_entrypoint"]
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

        assert model.vis.global_.offwidth >= instance["video_width"]
        assert model.vis.global_.offheight >= instance["video_height"]
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.ctrl).all()


def test_xarm7_private_resets_and_idle_settling_avoid_deep_contact() -> None:
    instances = _read(XARM_PRIVATE_ROOT / "instances.json")["instances"]
    assert len(instances) == 20

    for instance in instances:
        model = mujoco.MjModel.from_xml_path(
            str(XARM_PACKAGE_ROOT / instance["scene_entrypoint"])
        )
        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, instance["reset"])
        mujoco.mj_forward(model, data)
        for idle_step in range(101):
            if idle_step:
                mujoco.mj_step(model, data)
            if data.ncon == 0:
                continue
            contact = min(
                (data.contact[index] for index in range(data.ncon)),
                key=lambda item: float(item.dist),
            )
            geom_names = tuple(
                mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, int(geom_id))
                or f"geom:{geom_id}"
                for geom_id in (contact.geom1, contact.geom2)
            )
            assert float(contact.dist) >= MIN_RESET_CONTACT_DISTANCE, (
                f"{instance['task_id']} reaches {float(contact.dist):.9f} m "
                f"between {geom_names} at idle step {idle_step}"
            )


def test_xarm7_public_ee_waypoints_are_position_ik_feasible() -> None:
    model = mujoco.MjModel.from_xml_path(str(XARM_PACKAGE_ROOT / "assets" / "scene.xml"))
    data = mujoco.MjData(model)
    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    skeleton = ArmSerialDLSSkeleton(
        model=model,
        data=data,
        spec=ArmSpec(
            ee_site_name="link_tcp",
            arm_joint_names=ARM_JOINTS,
            arm_actuator_names=ARM_ACTUATORS,
            joint_limits=ARM_LIMITS,
            home_qpos=HOME_ARM,
            ik_max_iter=500,
            ik_tolerance=0.003,
            ik_step_clamp=0.15,
            gripper_actuator_names=("gripper",),
            gripper_close_ctrl=255.0,
            gripper_open_ctrl=0.0,
        ),
    )

    checked = 0
    for instance in _read(XARM_PRIVATE_ROOT / "instances.json")["instances"]:
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
    assert checked >= 40


def test_xarm7_private_package_is_still_non_runtime_and_incomplete() -> None:
    runnable_index = _read(ROOT / "libraries" / "robots" / "index.json")
    assert ROBOT_ID not in runnable_index["robots"]
    assert sorted(path.name for path in XARM_PRIVATE_ROOT.iterdir()) == [
        "bindings.json",
        "guards.json",
        "instances.json",
    ]
