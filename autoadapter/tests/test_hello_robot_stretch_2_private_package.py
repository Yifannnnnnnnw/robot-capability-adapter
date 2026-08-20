from __future__ import annotations

import copy
import json
from pathlib import Path

import mujoco
import numpy as np
import pytest

from autoadapter2.harness.session import apply_framework_reset
from autoadapter2.harness.measurements import measure
from autoadapter2.libraries.robot_package import (
    _validate_private_inputs,
    _validate_sources,
    _validate_tasks,
    load_robot_package,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "hello_robot_stretch_2" / "1.0.0"
TASKS_ROOT = PACKAGE_ROOT / "tasks"
PRIVATE_ROOT = TASKS_ROOT / "private"
FRANKA_PRIVATE_ROOT = ROOT / "libraries" / "robots" / "franka_panda" / "1.0.0" / "tasks" / "private"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"
ROBOT_ID = "hello_robot_stretch_2"
PACKAGE_VERSION = "1.0.0"
SNAPSHOT_ID = "hello-robot-stretch-2-metaworld-source-protocols-2026-08-20-v1"
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
    "mw_peg_insertion_side",
    "mw_bin_picking",
    "mw_pick_out_of_hole",
)
GUARD_IDS = (
    "guard_actuator_and_physics_step",
    "guard_no_direct_state_write",
    "guard_canonical_model_data",
    "guard_complete_video",
)
CASE_GUARD_IDS = GUARD_IDS[:3]
ROBOT_RESET_JOINT_POSITIONS = {
    "joint_lift": 0.0,
    "joint_arm_l3": 0.0,
    "joint_arm_l2": 0.0,
    "joint_arm_l1": 0.0,
    "joint_arm_l0": 0.0,
    "joint_wrist_yaw": 0.0,
    "joint_gripper_slide": 0.04,
    "joint_head_pan": 0.0,
    "joint_head_tilt": 0.0,
}
ROBOT_RESET_ACTUATOR_CONTROLS = {
    "forward": 0.0,
    "turn": 0.0,
    "lift": 0.0,
    "arm_extend": 0.0,
    "wrist_yaw": 0.0,
    "grip": 0.04,
    "head_pan": 0.0,
    "head_tilt": 0.0,
}
FIXTURE_RESET_JOINT_POSITIONS = {
    "mw_drawer_close": {"drawer_slide": -0.08},
    "mw_handle_pull": {"vertical_handle_slide": -0.055},
    "mw_door_close": {"door_hinge": 1.2},
}
CALIBRATED_PARAMETER_OVERRIDES = {
    "mw_drawer_open": {
        "contact_position": [0.005, -0.48, 0.55],
        "target_position": [-0.075, -0.48, 0.55],
        "tool_target_position": [-0.075, -0.48, 0.55],
    },
    "mw_drawer_close": {
        "contact_position": [-0.075, -0.48, 0.55],
        "target_position": [0.005, -0.48, 0.55],
        "tool_target_position": [0.005, -0.48, 0.55],
    },
    "mw_handle_press": {
        "contact_position": [0.1, -0.497, 0.66],
        "target_position": [0.1, -0.48, 0.51],
        "tool_target_position": [0.1, -0.497, 0.56],
    },
    "mw_handle_pull": {
        "contact_position": [0.1, -0.472, 0.59],
        "target_position": [0.1, -0.48, 0.56],
        "tool_target_position": [0.1, -0.472, 0.64],
    },
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validated_public_tasks() -> tuple[dict, ...]:
    sources_path = TASKS_ROOT / "sources.json"
    catalog_path = TASKS_ROOT / "catalog.json"
    sources_document = _read(sources_path)
    catalog_document = _read(catalog_path)
    assert sources_document["robot_configuration_id"] == ROBOT_ID
    assert sources_document["package_version"] == PACKAGE_VERSION
    assert catalog_document["robot_configuration_id"] == ROBOT_ID
    assert catalog_document["package_version"] == PACKAGE_VERSION
    assert catalog_document["snapshot_id"] == SNAPSHOT_ID
    sources = _validate_sources(sources_document, path=sources_path)
    return _validate_tasks(
        catalog_document,
        path=catalog_path,
        source_ids={str(source["source_id"]) for source in sources},
    )


def test_stretch_private_documents_close_public_snapshot_and_fail_closed() -> None:
    tasks = _validated_public_tasks()
    assert tuple(task["task_id"] for task in tasks) == TASK_IDS
    assert len(tasks) == 20

    documents = {
        name: _read(PRIVATE_ROOT / f"{name}.json")
        for name in ("instances", "bindings", "guards")
    }
    for document in documents.values():
        assert document["robot_configuration_id"] == ROBOT_ID
        assert document["package_version"] == PACKAGE_VERSION
        assert document["task_snapshot_id"] == SNAPSHOT_ID

    _validate_private_inputs(
        package_root=PACKAGE_ROOT,
        private_dir=PRIVATE_ROOT,
        robot_configuration_id=ROBOT_ID,
        package_version=PACKAGE_VERSION,
        snapshot_id=SNAPSHOT_ID,
        tasks=tasks,
    )

    instances = documents["instances"]["instances"]
    bindings = documents["bindings"]["bindings"]
    guards = documents["guards"]["guards"]
    assert len(instances) == 20
    assert len(bindings) == 20
    assert len(guards) == 4
    assert tuple(instance["task_id"] for instance in instances) == TASK_IDS
    assert tuple(binding["binding_id"] for binding in bindings) == tuple(
        f"binding-{task_id}" for task_id in TASK_IDS
    )
    assert tuple(guard["guard_id"] for guard in guards) == GUARD_IDS
    assert tuple(guard["kind"] for guard in guards) == (
        "actuator_and_physics_step_required",
        "no_direct_state_write",
        "canonical_model_data",
        "complete_video",
    )

    tasks_by_id = {task["task_id"]: task for task in tasks}
    bindings_by_id = {binding["binding_id"]: binding for binding in bindings}
    for instance in instances:
        task = tasks_by_id[instance["task_id"]]
        required = set(task["invocation_schema"]["request"]["task_parameters"]["required"])
        supplied = set(instance["public_arguments"]["request"]["task_parameters"])
        assert supplied == required
        assert instance["guard_ids"] == list(CASE_GUARD_IDS)
        assert instance["repetitions"] == 1
        assert instance["timeout_sim_s"] == 60.0
        assert instance["max_steps"] == 30000
        assert instance["sample_hz"] == 20.0
        assert instance["video_width"] == 800
        assert instance["video_height"] == 600
        assert instance["video_fps"] == 10.0
        assert instance["camera"] == "evidence"
        for clause in task["scoring"]:
            binding = bindings_by_id[instance["clause_bindings"][clause["clause_id"]]]
            assert binding["metric"] == clause["metric"]
            assert binding["unit"] == clause["unit"]

    private_text = "\n".join(
        (PRIVATE_ROOT / f"{name}.json").read_text(encoding="utf-8")
        for name in ("instances", "bindings", "guards")
    ).lower()
    assert "panda" not in private_text
    assert "franka" not in private_text

    assert (PACKAGE_ROOT / "reference" / "driver.py").is_file()
    assert ROBOT_ID not in _read(RUNNABLE_INDEX_PATH)["robots"]
    research = _read(RESEARCH_INDEX_PATH)
    assert any(
        candidate["robot_configuration_id"] == ROBOT_ID
        for candidate in research["candidates"]
    )
    package = load_robot_package(PACKAGE_ROOT)
    assert package.robot_configuration_id == ROBOT_ID
    assert len(package.tasks) == 20


def test_stretch_private_records_match_the_reviewed_rotation_and_reset_transform() -> None:
    tasks = _validated_public_tasks()
    tasks_by_id = {task["task_id"]: task for task in tasks}
    template = _read(FRANKA_PRIVATE_ROOT / "instances.json")["instances"]
    actual = _read(PRIVATE_ROOT / "instances.json")["instances"]
    assert len(template) == len(actual) == 20

    for expected, observed in zip(template, actual):
        task_id = expected["task_id"]
        assert observed["task_id"] == task_id
        assert observed["instance_id"] == expected["instance_id"].replace(
            "franka-", "stretch-", 1
        )
        assert observed["scene_entrypoint"] == expected["scene_entrypoint"]

        expected_parameters = {}
        for name, value in expected["public_arguments"]["request"]["task_parameters"].items():
            if name == "grasp_wrist_roll":
                continue
            if name == "grasp_gripper":
                expected_parameters[name] = -0.005
            elif name.endswith("_position"):
                schema = tasks_by_id[task_id]["invocation_schema"]["request"][
                    "task_parameters"
                ]["properties"][name]
                assert schema["frame"] == "world"
                assert len(value) == 3
                expected_parameters[name] = [value[1], -value[0], value[2]]
            else:
                expected_parameters[name] = copy.deepcopy(value)

        observed_parameters = observed["public_arguments"]["request"]["task_parameters"]
        expected_parameters.update(
            copy.deepcopy(CALIBRATED_PARAMETER_OVERRIDES.get(task_id, {}))
        )
        assert observed_parameters == expected_parameters
        required = set(
            tasks_by_id[task_id]["invocation_schema"]["request"]["task_parameters"]["required"]
        )
        assert set(observed_parameters) == required
        assert "grasp_wrist_roll" not in observed_parameters
        if "grasp_gripper" in observed_parameters:
            assert observed_parameters["grasp_gripper"] == -0.005

        expected_reset = {
            "kind": "default",
            "joint_positions": {
                **ROBOT_RESET_JOINT_POSITIONS,
                **FIXTURE_RESET_JOINT_POSITIONS.get(task_id, {}),
            },
            "actuator_controls": ROBOT_RESET_ACTUATOR_CONTROLS,
        }
        assert observed["reset"] == expected_reset
        assert observed["clause_bindings"] == expected["clause_bindings"]

    expected_bindings = copy.deepcopy(
        _read(FRANKA_PRIVATE_ROOT / "bindings.json")
    )
    expected_bindings["robot_configuration_id"] = ROBOT_ID
    expected_bindings["package_version"] = PACKAGE_VERSION
    expected_bindings["task_snapshot_id"] = SNAPSHOT_ID
    reach_binding = next(
        binding
        for binding in expected_bindings["bindings"]
        if binding["binding_id"] == "binding-mw_reach_target"
    )
    reach_binding["kind"] = "final_body_position_error"
    reach_binding["parameters"]["body_name"] = "link_gripper_slider"
    front_button_binding = next(
        binding
        for binding in expected_bindings["bindings"]
        if binding["binding_id"] == "binding-mw_button_press"
    )
    front_button_binding["parameters"]["axis"] = 0
    assert _read(PRIVATE_ROOT / "bindings.json") == expected_bindings

    expected_guards = copy.deepcopy(_read(FRANKA_PRIVATE_ROOT / "guards.json"))
    expected_guards["robot_configuration_id"] = ROBOT_ID
    expected_guards["package_version"] = PACKAGE_VERSION
    expected_guards["task_snapshot_id"] = SNAPSHOT_ID
    assert _read(PRIVATE_ROOT / "guards.json") == expected_guards


def test_stretch_framework_resets_load_and_step_every_referenced_scene() -> None:
    instances = _read(PRIVATE_ROOT / "instances.json")["instances"]
    assert len({instance["scene_entrypoint"] for instance in instances}) == 17
    assert mujoco.__version__ == "3.3.6"

    for instance in instances:
        scene_path = PACKAGE_ROOT / instance["scene_entrypoint"]
        model = mujoco.MjModel.from_xml_path(str(scene_path))
        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, instance["reset"])
        mujoco.mj_forward(model, data)

        for name, expected in ROBOT_RESET_JOINT_POSITIONS.items():
            joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            assert joint_id >= 0, f"{instance['task_id']}: {name}"
            address = int(model.jnt_qposadr[joint_id])
            assert data.qpos[address] == expected
        for name, expected in FIXTURE_RESET_JOINT_POSITIONS.get(
            instance["task_id"], {}
        ).items():
            joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            assert joint_id >= 0, f"{instance['task_id']}: {name}"
            address = int(model.jnt_qposadr[joint_id])
            assert data.qpos[address] == expected
        for name, expected in ROBOT_RESET_ACTUATOR_CONTROLS.items():
            actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            assert actuator_id >= 0, f"{instance['task_id']}: {name}"
            assert data.ctrl[actuator_id] == expected

        base_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base_link")
        assert base_id >= 0
        assert data.xmat[base_id, 8] > 0.99
        assert abs(float(data.xpos[base_id, 2])) < 0.05
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.ctrl).all()

        start_time = float(data.time)
        for _ in range(8):
            mujoco.mj_step(model, data)
            assert np.isfinite(data.qpos).all()
            assert np.isfinite(data.qvel).all()
            assert np.isfinite(data.ctrl).all()
        assert data.time > start_time
        assert abs(float(data.xpos[base_id, 2])) < 0.05
        assert data.xmat[base_id, 8] > 0.99


def test_stretch_front_button_reset_cannot_pass_the_rotated_axis_binding() -> None:
    tasks = {task["task_id"]: task for task in _validated_public_tasks()}
    instances = {
        instance["task_id"]: instance
        for instance in _read(PRIVATE_ROOT / "instances.json")["instances"]
    }
    bindings = {
        binding["binding_id"]: binding
        for binding in _read(PRIVATE_ROOT / "bindings.json")["bindings"]
    }
    task = tasks["mw_button_press"]
    instance = instances["mw_button_press"]
    clause = task["scoring"][0]
    binding = bindings[instance["clause_bindings"][clause["clause_id"]]]
    assert binding["parameters"]["axis"] == 0

    model = mujoco.MjModel.from_xml_path(str(PACKAGE_ROOT / instance["scene_entrypoint"]))
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, instance["reset"])
    site_name = binding["parameters"]["site_name"]
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, site_name)
    assert site_id >= 0
    value = measure(
        binding,
        evidence={
            "samples": [
                {
                    "time": float(data.time),
                    "site_positions": {
                        site_name: data.site_xpos[site_id].astype(float).tolist()
                    },
                }
            ]
        },
        public_arguments=instance["public_arguments"],
    )

    assert value == pytest.approx(0.05)
    assert value > clause["threshold"]


def test_stretch_handle_pull_reset_cannot_pass_the_elevated_target() -> None:
    tasks = {task["task_id"]: task for task in _validated_public_tasks()}
    instances = {
        instance["task_id"]: instance
        for instance in _read(PRIVATE_ROOT / "instances.json")["instances"]
    }
    bindings = {
        binding["binding_id"]: binding
        for binding in _read(PRIVATE_ROOT / "bindings.json")["bindings"]
    }
    task = tasks["mw_handle_pull"]
    instance = instances["mw_handle_pull"]
    clause = task["scoring"][0]
    binding = bindings[instance["clause_bindings"][clause["clause_id"]]]

    model = mujoco.MjModel.from_xml_path(str(PACKAGE_ROOT / instance["scene_entrypoint"]))
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, instance["reset"])
    site_name = binding["parameters"]["site_name"]
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, site_name)
    assert site_id >= 0
    value = measure(
        binding,
        evidence={
            "samples": [
                {
                    "time": float(data.time),
                    "site_positions": {
                        site_name: data.site_xpos[site_id].astype(float).tolist()
                    },
                }
            ]
        },
        public_arguments=instance["public_arguments"],
    )

    assert value == pytest.approx(0.055)
    assert value > clause["threshold"]
