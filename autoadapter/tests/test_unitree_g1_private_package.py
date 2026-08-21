from __future__ import annotations

import ast
import copy
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Iterator

import mujoco
import numpy as np

from autoadapter2.harness.measurements import compare, evaluate_guards, measure
from autoadapter2.harness.session import TrackedMuJoCoSession, apply_framework_reset
from autoadapter2.libraries.robot_package import load_robot_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "unitree_g1" / "1.0.0"
PRIVATE_ROOT = PACKAGE_ROOT / "tasks" / "private"
SCENE_PATH = PACKAGE_ROOT / "assets" / "scene.xml"
SKELETON_PATH = PACKAGE_ROOT / "skeleton" / "joint_position.py"
SNAPSHOT_ID = "unitree-g1-public-source-contracts-2026-08-20-v1"
TASK_IDS = (
    "humanoidbench_walk",
    "humanoidbench_stand",
    "humanoidbench_run",
    "humanoidbench_reach",
    "humanoidbench_hurdle",
    "humanoidbench_crawl",
    "humanoidbench_maze",
    "humanoidbench_sit",
    "humanoidbench_balance",
    "humanoidbench_stair",
    "humanoidbench_slide",
    "humanoidbench_pole",
    "humanoidbench_push",
    "robocup_parkour",
    "robocup_dynamic_kick",
    "robocup_high_kick",
    "robocup_obstacle_navigation",
    "robocup_long_stride",
    "safety_gym_goal0",
    "safety_gym_button0",
)
EXPECTED_CLAUSES = {
    "humanoidbench_walk": ("humanoidbench_walk_episode_return", "reward", ">=", 700, 1000),
    "humanoidbench_stand": ("humanoidbench_stand_episode_return", "reward", ">=", 800, 1000),
    "humanoidbench_run": ("humanoidbench_run_episode_return", "reward", ">=", 700, 1000),
    "humanoidbench_reach": ("left_distal_wrist_target_euclidean_error", "m", "<", 0.05, 1000),
    "humanoidbench_hurdle": ("humanoidbench_hurdle_episode_return", "reward", ">=", 700, 1000),
    "humanoidbench_crawl": ("humanoidbench_crawl_episode_return", "reward", ">=", 700, 1000),
    "humanoidbench_maze": ("humanoidbench_maze_episode_return", "reward", ">=", 1200, 1000),
    "humanoidbench_sit": ("humanoidbench_sit_simple_episode_return", "reward", ">=", 750, 1000),
    "humanoidbench_balance": ("humanoidbench_balance_simple_episode_return", "reward", ">=", 800, 1000),
    "humanoidbench_stair": ("humanoidbench_stair_episode_return", "reward", ">=", 700, 1000),
    "humanoidbench_slide": ("humanoidbench_slide_episode_return", "reward", ">=", 700, 1000),
    "humanoidbench_pole": ("humanoidbench_pole_episode_return", "reward", ">=", 700, 1000),
    "humanoidbench_push": ("box_goal_euclidean_position_error", "m", "<", 0.05, 500),
    "robocup_parkour": ("consecutive_step_up_jump_down_dwell_success_count", "run", ">=", 2, 1500),
    "robocup_dynamic_kick": ("moving_ball_goal_successful_run_count", "run", ">=", 2, 1500),
    "robocup_high_kick": ("declared_height_valid_goal_count", "run", ">=", 1, 1500),
    "robocup_obstacle_navigation": ("fully_successful_obstacle_navigation_run_count", "run", ">=", 1, 1500),
    "robocup_long_stride": ("fully_successful_long_stride_run_count", "run", ">=", 1, 1500),
    "safety_gym_goal0": ("robot_goal_planar_distance", "m", "<=", 0.3, 1000),
    "safety_gym_button0": ("selected_button_robot_contact", "binary", "==", 1, 1000),
}
EXPECTED_PARAMETERS = {
    "humanoidbench_walk": {
        "travel_direction_xyz": [1, 0, 0],
        "max_control_steps": 1000,
    },
    "humanoidbench_stand": {"max_control_steps": 1000},
    "humanoidbench_run": {
        "travel_direction_xyz": [1, 0, 0],
        "max_control_steps": 1000,
    },
    "humanoidbench_reach": {
        "target_position_m": [0.35, 0.22, 1.1],
        "end_effector_body": "left_wrist_yaw_link",
        "max_control_steps": 1000,
    },
    "humanoidbench_hurdle": {
        "course_id": "humanoidbench_cb118903_hurdles",
        "travel_direction_xyz": [1, 0, 0],
        "max_control_steps": 1000,
    },
    "humanoidbench_crawl": {
        "tunnel_id": "humanoidbench_cb118903_crawl_tunnel",
        "travel_direction_xyz": [1, 0, 0],
        "max_control_steps": 1000,
    },
    "humanoidbench_maze": {
        "checkpoint_positions_m": [[0, 0, 1], [3, 0, 1], [3, 6, 1], [6, 6, 1], [6, 6, 1]],
        "max_control_steps": 1000,
    },
    "humanoidbench_sit": {
        "chair_id": "humanoidbench_cb118903_sit_simple_chair",
        "max_control_steps": 1000,
    },
    "humanoidbench_balance": {
        "support_board_id": "humanoidbench_cb118903_balance_simple_board",
        "max_control_steps": 1000,
    },
    "humanoidbench_stair": {
        "stair_course_id": "humanoidbench_cb118903_stairs",
        "travel_direction_xyz": [1, 0, 0],
        "max_control_steps": 1000,
    },
    "humanoidbench_slide": {
        "ramp_course_id": "humanoidbench_cb118903_slides",
        "travel_direction_xyz": [1, 0, 0],
        "max_control_steps": 1000,
    },
    "humanoidbench_pole": {
        "pole_field_id": "humanoidbench_cb118903_poles",
        "travel_direction_xyz": [1, 0, 0],
        "max_control_steps": 1000,
    },
    "humanoidbench_push": {
        "object_id": "tabletop_box",
        "goal_position_m": [0.95, 0.35, 1.0],
        "max_control_steps": 500,
    },
    "robocup_parkour": {
        "platform_height_m": 0.25,
        "platform_top_size_m": [0.6, 0.6],
        "post_landing_dwell_s": 5,
        "required_consecutive_runs": 2,
        "max_trial_duration_s": 1500,
    },
    "robocup_dynamic_kick": {
        "ball_id": "rolling_soccer_ball",
        "goal_id": "robocup_adultsize_goal",
        "passer_start_distance_m": 1.0,
        "counted_run_count": 3,
        "required_success_count": 2,
        "max_trial_duration_s": 1500,
    },
    "robocup_high_kick": {
        "ball_diameter_m": 0.22,
        "declared_min_height_m": 0.15,
        "height_increment_m": 0.01,
        "minimum_start_distance_m": 0.3,
        "max_trial_duration_s": 1500,
    },
    "robocup_obstacle_navigation": {
        "strip_width_m": 3.0,
        "obstacle_count": 5,
        "obstacle_height_m": 1.0,
        "obstacle_width_m": 0.6,
        "max_trial_duration_s": 1500,
    },
    "robocup_long_stride": {
        "gap_distance_m": 0.4,
        "leg_length_m": 1.0,
        "max_trial_duration_s": 1500,
    },
    "safety_gym_goal0": {
        "goal_center_xy_m": [0.5, 0.5],
        "max_control_steps": 1000,
    },
    "safety_gym_button0": {
        "goal_button_index": 0,
        "max_control_steps": 1000,
    },
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def _loaded_g1_package() -> Iterator[Any]:
    with tempfile.TemporaryDirectory(prefix="unitree-g1-loader-") as temporary:
        staged_root = Path(temporary) / "unitree_g1" / "1.0.0"
        shutil.copytree(PACKAGE_ROOT, staged_root)
        reference = staged_root / "reference"
        reference.mkdir()
        (reference / "driver.py").write_text(
            "def build(model, data):\n    return None\n",
            encoding="utf-8",
        )
        yield load_robot_package(staged_root)


def _load_skeleton_module() -> Any:
    module_name = "unitree_g1_test_skeleton"
    specification = importlib.util.spec_from_file_location(module_name, SKELETON_PATH)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    specification.loader.exec_module(module)
    return module


def test_unitree_g1_private_loader_contract_covers_public_snapshot() -> None:
    with _loaded_g1_package() as package:
        assert package.robot_configuration_id == "unitree_g1"
        assert package.package_version == "1.0.0"
        assert package.snapshot_id == SNAPSHOT_ID
        assert tuple(task["task_id"] for task in package.tasks) == TASK_IDS

        instances_document = _read(package.private_dir / "instances.json")
        bindings_document = _read(package.private_dir / "bindings.json")
        guards_document = _read(package.private_dir / "guards.json")
        for document in (instances_document, bindings_document, guards_document):
            assert document["robot_configuration_id"] == "unitree_g1"
            assert document["package_version"] == "1.0.0"
            assert document["task_snapshot_id"] == SNAPSHOT_ID

        instances = {item["task_id"]: item for item in instances_document["instances"]}
        bindings = {item["binding_id"]: item for item in bindings_document["bindings"]}
        guards = {item["guard_id"]: item for item in guards_document["guards"]}
        assert set(instances) == set(TASK_IDS)
        assert len(instances) == len(TASK_IDS)
        assert set(guards) == {
            "g1_physics",
            "g1_no_state_write",
            "g1_canonical",
            "g1_terminal_stability",
        }

        tasks = {task["task_id"]: task for task in package.tasks}
        for task_id in TASK_IDS:
            task = tasks[task_id]
            clause = task["scoring"][0]
            instance = instances[task_id]
            binding = bindings[instance["clause_bindings"][clause["clause_id"]]]
            metric, unit, comparator, threshold, budget = EXPECTED_CLAUSES[task_id]
            assert (clause["metric"], clause["unit"], clause["comparator"], clause["threshold"]) == (
                metric,
                unit,
                comparator,
                threshold,
            )
            assert (binding["metric"], binding["unit"]) == (metric, unit)
            assert instance["public_arguments"]["request"]["task_id"] == task_id
            assert instance["public_arguments"]["request"]["task_parameters"] == EXPECTED_PARAMETERS[task_id]
            assert instance["reset"] == {"kind": "keyframe", "name": "stand"}
            assert instance["max_steps"] == budget
            assert set(instance["guard_ids"]) == set(guards)


def test_unitree_g1_private_scene_compiles_and_reset_does_not_pass() -> None:
    with _loaded_g1_package() as package:
        scene = package.root / "assets" / "scene.xml"
        model = mujoco.MjModel.from_xml_path(str(scene))
        assert (model.nq, model.nv, model.nu) == (36, 35, 29)
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "pelvis") >= 0
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "stand") >= 0

        instances = _read(package.private_dir / "instances.json")["instances"]
        bindings = {
            item["binding_id"]: item
            for item in _read(package.private_dir / "bindings.json")["bindings"]
        }
        tasks = {task["task_id"]: task for task in package.tasks}
        for instance in instances:
            data = mujoco.MjData(model)
            apply_framework_reset(mujoco, model, data, instance["reset"])
            assert np.isfinite(data.qpos).all()
            assert np.isfinite(data.qvel).all()
            assert np.isfinite(data.ctrl).all()
            tracker = TrackedMuJoCoSession(
                mujoco=mujoco,
                model=model,
                data=data,
                max_steps=1,
                max_sim_time_s=1.0,
            )
            evidence = {
                "samples": [tracker.snapshot()],
                "step_count": 0,
                "contact_pair_step_counts": [],
            }
            task = tasks[instance["task_id"]]
            clause = task["scoring"][0]
            binding = bindings[instance["clause_bindings"][clause["clause_id"]]]
            value = measure(
                binding,
                evidence=evidence,
                public_arguments=instance["public_arguments"],
            )
            assert not compare(
                value,
                comparator=clause["comparator"],
                threshold=clause["threshold"],
            ), f"reset already passes {instance['task_id']}: value={value}"


def test_unitree_g1_skeleton_is_bounded_actuator_liveness_and_guards_reject_bypass() -> None:
    skeleton_module = _load_skeleton_module()
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, {"kind": "keyframe", "name": "stand"})
    skeleton = skeleton_module.G1JointPositionSkeleton.from_session(
        model=model,
        data=data,
        spec=skeleton_module.G1JointPositionSpec.default(),
    )
    description = skeleton.describe()
    assert description["joint_names"] == list(skeleton_module.G1_JOINT_NAMES)
    assert description["actuator_names"] == list(skeleton_module.G1_ACTUATOR_NAMES)
    assert len(description["joint_limits_rad"]) == 29

    target = skeleton.get_joint_positions()
    target[0] += 0.12
    with TrackedMuJoCoSession(
        mujoco=mujoco,
        model=model,
        data=data,
        max_steps=20,
        max_sim_time_s=1.0,
        sample_hz=20.0,
    ) as tracker:
        observed = skeleton.command_joint_positions(target, steps=12)
        tracker.finish()

    evidence = tracker.evidence()
    assert evidence["step_count"] == 12
    assert evidence["ctrl_observed_before_step"]
    assert evidence["ctrl_changed_from_reset"]
    assert not evidence["direct_state_write_detected"]
    assert np.isfinite(observed["joint_positions"]).all()
    assert np.isfinite(observed["joint_velocities"]).all()
    assert np.isfinite(observed["base_position"]).all()
    assert np.isfinite(observed["base_quaternion"]).all()

    guards = _read(PRIVATE_ROOT / "guards.json")["guards"]
    outcomes = evaluate_guards(
        guards,
        worker_result={"canonical_model_data": True, "physical_evidence": evidence},
    )
    assert all(outcomes.values()), outcomes

    bypass_evidence = copy.deepcopy(evidence)
    bypass_evidence["direct_state_write_detected"] = True
    assert not evaluate_guards(
        guards,
        worker_result={"canonical_model_data": True, "physical_evidence": bypass_evidence},
    )["g1_no_state_write"]

    fallen_evidence = copy.deepcopy(evidence)
    fallen_sample = fallen_evidence["samples"][-1]
    fallen_sample["body_positions"]["pelvis"][2] = 0.1
    fallen_sample["body_quaternions"]["pelvis"] = [0, 1, 0, 0]
    assert not evaluate_guards(
        guards,
        worker_result={"canonical_model_data": True, "physical_evidence": fallen_evidence},
    )["g1_terminal_stability"]

    tree = ast.parse(SKELETON_PATH.read_text(encoding="utf-8"))
    state_writes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target_node in node.targets:
            for child in ast.walk(target_node):
                if isinstance(child, ast.Attribute) and child.attr in {"qpos", "qvel"}:
                    state_writes.append(child.attr)
    assert state_writes == []
