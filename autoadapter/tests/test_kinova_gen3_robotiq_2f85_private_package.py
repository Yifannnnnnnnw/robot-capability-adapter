from __future__ import annotations

import copy
import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.harness.session import apply_framework_reset
from autoadapter2.harness.measurements import compare, measure
from autoadapter2.libraries.robot_package import (
    _validate_private_inputs,
    _validate_sources,
    _validate_tasks,
)
from autoadapter2.trusted_skeletons.arm_serial_dls import ArmSerialDLSSkeleton, ArmSpec


ROOT = Path(__file__).resolve().parents[1]
KINOVA_PACKAGE_ROOT = ROOT / "libraries" / "robots" / "kinova_gen3_robotiq_2f85" / "1.0.0"
KINOVA_TASKS_ROOT = KINOVA_PACKAGE_ROOT / "tasks"
KINOVA_PRIVATE_ROOT = KINOVA_TASKS_ROOT / "private"
XARM_PRIVATE_ROOT = (
    ROOT / "libraries" / "robots" / "ufactory_xarm7" / "1.0.0" / "tasks" / "private"
)

ROBOT_ID = "kinova_gen3_robotiq_2f85"
SNAPSHOT_ID = "kinova-gen3-robotiq-2f85-metaworld-source-protocols-2026-08-20-v1"
HOME_ARM = [0.0, 0.26179939, 3.14159265, -2.26892803, 0.0, 0.95993109, 1.57079633]
ARM_JOINTS = tuple(f"joint_{index}" for index in range(1, 8))
ARM_ACTUATORS = ARM_JOINTS
GRIPPER_JOINTS = (
    "right_driver_joint",
    "right_coupler_joint",
    "right_spring_link_joint",
    "right_follower_joint",
    "left_driver_joint",
    "left_coupler_joint",
    "left_spring_link_joint",
    "left_follower_joint",
)
ROBOT_RESET_JOINTS = {
    **dict(zip(ARM_JOINTS, HOME_ARM)),
    **{name: 0.0 for name in GRIPPER_JOINTS},
}
ROBOT_RESET_CONTROLS = {
    **dict(zip(ARM_ACTUATORS, HOME_ARM)),
    "fingers_actuator": 0.0,
}
XARM_ROBOT_JOINTS = {
    *(f"joint{index}" for index in range(1, 8)),
    "left_driver_joint",
    "left_finger_joint",
    "left_inner_knuckle_joint",
    "right_driver_joint",
    "right_finger_joint",
    "right_inner_knuckle_joint",
}
EE_WAYPOINT_KEYS = {
    "contact_position",
    "grasp_position",
    "release_position",
    "route_position",
    "tool_target_position",
}
ARM_LIMITS = {
    "joint_2": (-2.24, 2.24),
    "joint_4": (-2.57, 2.57),
    "joint_6": (-2.09, 2.09),
}
CONTINUOUS_JOINTS = ("joint_1", "joint_3", "joint_5", "joint_7")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _body_is_below(model: mujoco.MjModel, body_id: int, ancestor_id: int) -> bool:
    while body_id > 0:
        if body_id == ancestor_id:
            return True
        body_id = int(model.body_parentid[body_id])
    return False


def _robot_fixture_contacts(model: mujoco.MjModel, data: mujoco.MjData) -> list[tuple[str, str]]:
    robot_root = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base_link")
    assert robot_root >= 0
    result = []
    for index in range(data.ncon):
        contact = data.contact[index]
        body1 = int(model.geom_bodyid[contact.geom1])
        body2 = int(model.geom_bodyid[contact.geom2])
        if _body_is_below(model, body1, robot_root) == _body_is_below(
            model, body2, robot_root
        ):
            continue
        geom1 = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, int(contact.geom1))
        geom2 = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, int(contact.geom2))
        result.append((geom1 or f"geom:{contact.geom1}", geom2 or f"geom:{contact.geom2}"))
    return result


def _snapshot(model: mujoco.MjModel, data: mujoco.MjData) -> dict:
    body_positions = {
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, index) or f"body_{index}":
        np.asarray(data.xpos[index], dtype=float).tolist()
        for index in range(model.nbody)
    }
    site_positions = {
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_SITE, index) or f"site_{index}":
        np.asarray(data.site_xpos[index], dtype=float).tolist()
        for index in range(model.nsite)
    }
    joint_positions = {}
    for index in range(model.njnt):
        if int(model.jnt_type[index]) not in {
            int(mujoco.mjtJoint.mjJNT_HINGE),
            int(mujoco.mjtJoint.mjJNT_SLIDE),
        }:
            continue
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, index)
        assert name is not None
        joint_positions[name] = float(data.qpos[int(model.jnt_qposadr[index])])
    return {
        "time": float(data.time),
        "body_positions": body_positions,
        "site_positions": site_positions,
        "joint_positions": joint_positions,
    }


def _transform_instances(template: dict) -> dict:
    transformed = copy.deepcopy(template)
    transformed["robot_configuration_id"] = ROBOT_ID
    transformed["task_snapshot_id"] = SNAPSHOT_ID

    for instance in transformed["instances"]:
        instance["instance_id"] = instance["instance_id"].replace(
            "xarm7-", "kinova-gen3-robotiq-2f85-", 1
        )
        parameters = instance["public_arguments"]["request"]["task_parameters"]
        if instance["task_id"] == "mw_drawer_open":
            parameters["contact_position"] = [0.472, 0.185, 0.45]
            parameters["target_position"] = [0.48, 0.105, 0.45]
            parameters["tool_target_position"] = [0.472, 0.105, 0.45]
        elif instance["task_id"] == "mw_drawer_close":
            parameters["contact_position"] = [0.472, 0.105, 0.45]
            parameters["target_position"] = [0.48, 0.185, 0.45]
            parameters["tool_target_position"] = [0.472, 0.185, 0.45]
        elif instance["task_id"] == "mw_peg_insertion_side":
            parameters["start_position"] = [0.48, 0.13, 0.43]

        fixture_positions = {
            name: value
            for name, value in instance["reset"]["joint_positions"].items()
            if name not in XARM_ROBOT_JOINTS
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


def test_kinova_private_records_are_the_reviewed_robot_specific_transform() -> None:
    expected_instances = _transform_instances(_read(XARM_PRIVATE_ROOT / "instances.json"))
    actual_instances = _read(KINOVA_PRIVATE_ROOT / "instances.json")
    assert actual_instances == expected_instances
    assert len(actual_instances["instances"]) == 20

    expected_bindings = _transform_private_identity(
        _read(XARM_PRIVATE_ROOT / "bindings.json")
    )
    reach_binding = next(
        item
        for item in expected_bindings["bindings"]
        if item["binding_id"] == "binding-mw_reach_target"
    )
    reach_binding["kind"] = "final_site_position_error"
    reach_binding["parameters"] = {
        "target_argument": "request.task_parameters.target_position",
        "site_name": "pinch_site",
    }
    assert _read(KINOVA_PRIVATE_ROOT / "bindings.json") == expected_bindings

    expected_guards = _transform_private_identity(_read(XARM_PRIVATE_ROOT / "guards.json"))
    assert _read(KINOVA_PRIVATE_ROOT / "guards.json") == expected_guards

    grasp_controls = [
        instance["public_arguments"]["request"]["task_parameters"]["grasp_gripper"]
        for instance in actual_instances["instances"]
        if "grasp_gripper"
        in instance["public_arguments"]["request"]["task_parameters"]
    ]
    assert len(grasp_controls) == 4
    assert all(0.0 < value < 255.0 for value in grasp_controls)


def test_kinova_private_records_pass_the_canonical_validators() -> None:
    sources_path = KINOVA_TASKS_ROOT / "sources.json"
    catalog_path = KINOVA_TASKS_ROOT / "catalog.json"
    sources = _validate_sources(_read(sources_path), path=sources_path)
    tasks = _validate_tasks(
        _read(catalog_path),
        path=catalog_path,
        source_ids={str(source["source_id"]) for source in sources},
    )
    _validate_private_inputs(
        package_root=KINOVA_PACKAGE_ROOT,
        private_dir=KINOVA_PRIVATE_ROOT,
        robot_configuration_id=ROBOT_ID,
        package_version="1.0.0",
        snapshot_id=SNAPSHOT_ID,
        tasks=tasks,
    )


def test_kinova_framework_resets_resolve_in_every_task_scene() -> None:
    instances = _read(KINOVA_PRIVATE_ROOT / "instances.json")["instances"]

    for instance in instances:
        scene_path = KINOVA_PACKAGE_ROOT / instance["scene_entrypoint"]
        model = mujoco.MjModel.from_xml_path(str(scene_path))
        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, instance["reset"])
        mujoco.mj_forward(model, data)
        assert _robot_fixture_contacts(model, data) == [], instance["task_id"]

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


def test_kinova_public_ee_waypoints_are_position_ik_feasible() -> None:
    model = mujoco.MjModel.from_xml_path(str(KINOVA_PACKAGE_ROOT / "assets" / "scene.xml"))
    data = mujoco.MjData(model)
    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    skeleton = ArmSerialDLSSkeleton(
        model=model,
        data=data,
        spec=ArmSpec(
            ee_site_name="pinch_site",
            arm_joint_names=ARM_JOINTS,
            arm_actuator_names=ARM_ACTUATORS,
            joint_limits=ARM_LIMITS,
            continuous_joint_names=CONTINUOUS_JOINTS,
            home_qpos=HOME_ARM,
            ik_max_iter=1000,
            ik_tolerance=0.003,
            ik_step_clamp=0.15,
            gripper_actuator_names=("fingers_actuator",),
            gripper_close_ctrl=255.0,
            gripper_open_ctrl=0.0,
        ),
    )

    checked = 0
    for instance in _read(KINOVA_PRIVATE_ROOT / "instances.json")["instances"]:
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


def test_kinova_resets_and_idle_physics_do_not_already_satisfy_tasks() -> None:
    instances = _read(KINOVA_PRIVATE_ROOT / "instances.json")["instances"]
    tasks = {
        task["task_id"]: task
        for task in _read(KINOVA_TASKS_ROOT / "catalog.json")["tasks"]
    }
    bindings = {
        binding["binding_id"]: binding
        for binding in _read(KINOVA_PRIVATE_ROOT / "bindings.json")["bindings"]
    }

    for instance in instances:
        model = mujoco.MjModel.from_xml_path(
            str(KINOVA_PACKAGE_ROOT / instance["scene_entrypoint"])
        )
        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, instance["reset"])
        mujoco.mj_forward(model, data)
        initial = _snapshot(model, data)

        for _ in range(instance["max_steps"]):
            mujoco.mj_step(model, data)
        final = _snapshot(model, data)

        clauses = {clause["clause_id"]: clause for clause in tasks[instance["task_id"]]["scoring"]}
        for clause_id, binding_id in instance["clause_bindings"].items():
            clause = clauses[clause_id]
            binding = bindings[binding_id]
            for label, sample in (("initial", initial), ("idle", final)):
                value = measure(
                    binding,
                    evidence={"samples": [sample]},
                    public_arguments=instance["public_arguments"],
                )
                assert not compare(
                    value,
                    comparator=clause["comparator"],
                    threshold=clause["threshold"],
                ), f"{instance['task_id']} passed at {label} without task control: {value}"


def test_kinova_private_package_is_still_non_runtime_and_incomplete() -> None:
    runnable_index = _read(ROOT / "libraries" / "robots" / "index.json")
    assert ROBOT_ID not in runnable_index["robots"]
    assert sorted(path.name for path in KINOVA_PRIVATE_ROOT.iterdir()) == [
        "bindings.json",
        "guards.json",
        "instances.json",
    ]
