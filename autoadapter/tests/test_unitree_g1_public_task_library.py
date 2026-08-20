from __future__ import annotations

import json
from pathlib import Path

import mujoco

from autoadapter2.libraries.robot_package import _validate_sources, _validate_tasks


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "unitree_g1" / "1.0.0"
SOURCES_PATH = PACKAGE_ROOT / "tasks" / "sources.json"
CATALOG_PATH = PACKAGE_ROOT / "tasks" / "catalog.json"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"

SOURCE_ORDER = (
    "SRC-MENAGERIE-G1-DA76818E",
    "SRC-HUMANOIDBENCH-CB118903",
    "SRC-ROBOCUP-HL-2025-78D6EE85",
    "SRC-SAFETY-GYM-F31042F2",
)

TASK_ORDER = (
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
    "humanoidbench_walk": ("humanoidbench_walk_episode_return", ">=", 700, 1000),
    "humanoidbench_stand": ("humanoidbench_stand_episode_return", ">=", 800, 1000),
    "humanoidbench_run": ("humanoidbench_run_episode_return", ">=", 700, 1000),
    "humanoidbench_reach": ("left_distal_wrist_target_euclidean_error", "<", 0.05, 1000),
    "humanoidbench_hurdle": ("humanoidbench_hurdle_episode_return", ">=", 700, 1000),
    "humanoidbench_crawl": ("humanoidbench_crawl_episode_return", ">=", 700, 1000),
    "humanoidbench_maze": ("humanoidbench_maze_episode_return", ">=", 1200, 1000),
    "humanoidbench_sit": ("humanoidbench_sit_simple_episode_return", ">=", 750, 1000),
    "humanoidbench_balance": ("humanoidbench_balance_simple_episode_return", ">=", 800, 1000),
    "humanoidbench_stair": ("humanoidbench_stair_episode_return", ">=", 700, 1000),
    "humanoidbench_slide": ("humanoidbench_slide_episode_return", ">=", 700, 1000),
    "humanoidbench_pole": ("humanoidbench_pole_episode_return", ">=", 700, 1000),
    "humanoidbench_push": ("box_goal_euclidean_position_error", "<", 0.05, 500),
    "robocup_parkour": ("consecutive_step_up_jump_down_dwell_success_count", ">=", 2, 1500),
    "robocup_dynamic_kick": ("moving_ball_goal_successful_run_count", ">=", 2, 1500),
    "robocup_high_kick": ("declared_height_valid_goal_count", ">=", 1, 1500),
    "robocup_obstacle_navigation": ("fully_successful_obstacle_navigation_run_count", ">=", 1, 1500),
    "robocup_long_stride": ("fully_successful_long_stride_run_count", ">=", 1, 1500),
    "safety_gym_goal0": ("robot_goal_planar_distance", "<=", 0.3, 1000),
    "safety_gym_button0": ("selected_button_robot_contact", "==", 1, 1000),
}

EXPECTED_PARAMETERS = {
    "humanoidbench_walk": ("travel_direction_xyz", "max_control_steps"),
    "humanoidbench_stand": ("max_control_steps",),
    "humanoidbench_run": ("travel_direction_xyz", "max_control_steps"),
    "humanoidbench_reach": ("target_position_m", "end_effector_body", "max_control_steps"),
    "humanoidbench_hurdle": ("course_id", "travel_direction_xyz", "max_control_steps"),
    "humanoidbench_crawl": ("tunnel_id", "travel_direction_xyz", "max_control_steps"),
    "humanoidbench_maze": ("checkpoint_positions_m", "max_control_steps"),
    "humanoidbench_sit": ("chair_id", "max_control_steps"),
    "humanoidbench_balance": ("support_board_id", "max_control_steps"),
    "humanoidbench_stair": ("stair_course_id", "travel_direction_xyz", "max_control_steps"),
    "humanoidbench_slide": ("ramp_course_id", "travel_direction_xyz", "max_control_steps"),
    "humanoidbench_pole": ("pole_field_id", "travel_direction_xyz", "max_control_steps"),
    "humanoidbench_push": ("object_id", "goal_position_m", "max_control_steps"),
    "robocup_parkour": (
        "platform_height_m",
        "platform_top_size_m",
        "post_landing_dwell_s",
        "required_consecutive_runs",
        "max_trial_duration_s",
    ),
    "robocup_dynamic_kick": (
        "ball_id",
        "goal_id",
        "passer_start_distance_m",
        "counted_run_count",
        "required_success_count",
        "max_trial_duration_s",
    ),
    "robocup_high_kick": (
        "ball_diameter_m",
        "declared_min_height_m",
        "height_increment_m",
        "minimum_start_distance_m",
        "max_trial_duration_s",
    ),
    "robocup_obstacle_navigation": (
        "strip_width_m",
        "obstacle_count",
        "obstacle_height_m",
        "obstacle_width_m",
        "max_trial_duration_s",
    ),
    "robocup_long_stride": ("gap_distance_m", "leg_length_m", "max_trial_duration_s"),
    "safety_gym_goal0": ("goal_center_xy_m", "max_control_steps"),
    "safety_gym_button0": ("goal_button_index", "max_control_steps"),
}

SOURCE_EXPECTATIONS = {
    "SRC-MENAGERIE-G1-DA76818E": (
        "da76818e269b82289eba39808e2fb91d679d6994",
        ("29 actuated hinge joints", "non-articulated", "left_foot", "IMUs"),
    ),
    "SRC-HUMANOIDBENCH-CB118903": (
        "cb1189039151c8aadaaa987b442da54383c87fab",
        ("Section IV-A", "1000-step", "success_bar", "robots.py", "generated_xml_hurdles.xml"),
    ),
    "SRC-ROBOCUP-HL-2025-78D6EE85": (
        "78d6ee851a92f5350685320826a50f0e983885de",
        ("Section III", "AdultSize", "25 minute", "long-stride.tex"),
    ),
    "SRC-SAFETY-GYM-F31042F2": (
        "f31042f2f9ee61b9034dd6a416955972911544f5",
        ("1000-step", "Goal success", "Button success", "goal_size 0.3"),
    ),
}

SCENE_MARKERS = {
    "humanoidbench_hurdle": "ten 0.06 by 10 by 0.375 m collision hurdles",
    "humanoidbench_crawl": "16 m-long side walls",
    "humanoidbench_maze": "Occupied source cells",
    "humanoidbench_sit": "half-size (0.21,0.21,0.2375) m",
    "humanoidbench_balance": "radius 0.17 m",
    "humanoidbench_stair": "0.18 m increments",
    "humanoidbench_slide": "nine identical modules",
    "humanoidbench_pole": "41 pole rows",
    "humanoidbench_push": "x in [0.7,1.0] m",
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _all_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _all_keys(item)}
    return set()


def _temporal_budget(temporal: dict) -> int:
    if "max_control_steps" in temporal:
        return temporal["max_control_steps"]
    return temporal["max_trial_duration_s"]


def test_unitree_g1_public_task_library_snapshot() -> None:
    sources_document = _read_json(SOURCES_PATH)
    catalog_document = _read_json(CATALOG_PATH)
    morphology = _read_json(MORPHOLOGY_PATH)

    validated_sources = _validate_sources(sources_document, path=SOURCES_PATH)
    validated_tasks = _validate_tasks(
        catalog_document,
        path=CATALOG_PATH,
        source_ids={source["source_id"] for source in validated_sources},
    )

    assert set(sources_document) == {
        "schema_version",
        "robot_configuration_id",
        "package_version",
        "sources",
    }
    assert set(catalog_document) == {
        "schema_version",
        "robot_configuration_id",
        "package_version",
        "snapshot_id",
        "tasks",
    }
    for document in (sources_document, catalog_document):
        assert document["schema_version"] == "1.0"
        assert document["robot_configuration_id"] == "unitree_g1"
        assert document["package_version"] == "1.0.0"
    assert catalog_document["snapshot_id"] == "unitree-g1-public-source-contracts-2026-08-20-v1"

    source_ids = tuple(source["source_id"] for source in validated_sources)
    assert source_ids == SOURCE_ORDER
    for source in validated_sources:
        pinned_revision, markers = SOURCE_EXPECTATIONS[source["source_id"]]
        assert pinned_revision in source["version_or_date"] + source["locator"]
        assert all(marker in source["specific_reference"] for marker in markers)

    task_ids = tuple(task["task_id"] for task in validated_tasks)
    assert task_ids == TASK_ORDER
    assert len(task_ids) == len(set(task_ids)) == 20
    assert len(task_ids) >= 20
    assert len({task["name"] for task in validated_tasks}) == len(task_ids)
    assert len({task["description"] for task in validated_tasks}) == len(task_ids)
    assert len({task["source_task_or_operation"] for task in validated_tasks}) == len(task_ids)
    assert sum(len(task["scoring"]) for task in validated_tasks) == 20
    assert set(EXPECTED_CLAUSES) == set(task_ids)

    known_source_ids = set(source_ids)
    referenced_source_ids: set[str] = set()
    for task in validated_tasks:
        assert task["adaptation"].strip()
        assert all(isinstance(item, str) and item.strip() for item in task["scene_assumptions"])
        assert all(
            isinstance(item, str) and item.strip()
            for item in task["observation_assumptions"]
        )
        if task["task_id"] in SCENE_MARKERS:
            assert SCENE_MARKERS[task["task_id"]] in " ".join(task["scene_assumptions"])

        assert len(task["scoring"]) == 1
        clause = task["scoring"][0]
        metric, comparator, threshold, budget = EXPECTED_CLAUSES[task["task_id"]]
        assert clause["metric"] == metric
        assert clause["comparator"] == comparator
        assert clause["threshold"] == threshold
        assert _temporal_budget(clause["temporal"]) == budget
        assert clause["unit"].strip()
        assert clause["temporal"]["kind"].strip()
        assert clause["aggregation"]["kind"].strip()
        assert clause["threshold_provenance"].strip()
        for source_ref in clause["source_refs"]:
            assert source_ref["source_id"] in known_source_ids
            assert source_ref["specific_reference"].strip()
            assert source_ref["support"] == "adapted"
            assert source_ref["adaptation"].strip()
            referenced_source_ids.add(source_ref["source_id"])

        parameters = task["invocation_schema"]["request"]["task_parameters"]
        expected_parameters = EXPECTED_PARAMETERS[task["task_id"]]
        assert tuple(parameters["required"]) == expected_parameters
        assert tuple(parameters["properties"]) == expected_parameters
        assert parameters["additional_properties"] is False
        for parameter in parameters["properties"].values():
            assert parameter["type"] in {
                "array",
                "boolean",
                "integer",
                "number",
                "object",
                "string",
            }
            assert parameter["unit"].strip()
            assert parameter["frame"].strip()
            assert parameter["description"].strip()

    assert referenced_source_ids == {
        "SRC-HUMANOIDBENCH-CB118903",
        "SRC-ROBOCUP-HL-2025-78D6EE85",
        "SRC-SAFETY-GYM-F31042F2",
    }

    assert morphology["robot_model_id"] == "unitree_g1"
    assert morphology["robot_configuration_id"] == "unitree_g1"
    assert morphology["package_version"] == "1.0.0"
    assert morphology["research_only"] is True
    assert morphology["base_type"] == "free"
    assert morphology["model_dimensions"]["nq"] == 36
    assert morphology["model_dimensions"]["nv"] == 35
    assert morphology["model_dimensions"]["nu"] == 29
    assert morphology["model_dimensions"]["nsite"] == 4
    assert morphology["model_dimensions"]["nsensor"] == 4
    assert morphology["degrees_of_freedom"]["actuated_joints"] == 29
    assert morphology["public_model"]["free_base_joint"]["joint_type"] == "free"
    assert morphology["public_control"]["actuation"] == "joint_position"
    assert len(morphology["public_control"]["joint_names"]) == 29
    assert all(
        "finger" not in joint_name and "hand" not in joint_name
        for joint_name in morphology["public_control"]["joint_names"]
    )
    observations = set(morphology["public_affordances"]["observations"])
    assert {"free_base_pose", "free_base_velocity", "named_site_pose", "imu_observations", "contact_state"} <= observations
    g1_xml = (PACKAGE_ROOT / "assets" / "g1.xml").read_text(encoding="utf-8")
    assert "left_rubber_hand.STL" in g1_xml
    assert "right_rubber_hand.STL" in g1_xml
    assert "finger_joint" not in g1_xml

    model = mujoco.MjModel.from_xml_path(str(PACKAGE_ROOT / "assets" / "scene.xml"))
    assert (model.nq, model.nv, model.nu, model.nsite, model.nsensor) == (36, 35, 29, 4, 4)
    base_joint_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_JOINT, "floating_base_joint"
    )
    assert int(model.jnt_type[base_joint_id]) == int(mujoco.mjtJoint.mjJNT_FREE)
    for actuator_id in range(model.nu):
        joint_id = int(model.actuator_trnid[actuator_id, 0])
        assert int(model.actuator_trntype[actuator_id]) == int(mujoco.mjtTrn.mjTRN_JOINT)
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_HINGE)
        assert int(model.actuator_gaintype[actuator_id]) == int(mujoco.mjtGain.mjGAIN_FIXED)
        assert int(model.actuator_biastype[actuator_id]) == int(mujoco.mjtBias.mjBIAS_AFFINE)

    forbidden_keys = {
        "binding_id",
        "bindings",
        "guard_id",
        "guards",
        "instance_id",
        "instances",
        "seed",
        "reset_qpos",
        "capability_id",
        "capabilities",
        "effect_catalog",
        "task_to_effect",
        "controller",
        "reference_driver",
        "runnable",
    }
    assert _all_keys(catalog_document).isdisjoint(forbidden_keys)
    serialized_catalog = json.dumps(catalog_document, sort_keys=True).lower()
    for forbidden_fragment in (
        "http://",
        "https://",
        "tasks/private",
        "task_instances_private",
        "direct_mujoco_adapter",
        "binding_id",
        "guard_id",
        "reset_qpos",
        "effect_catalog",
        "task_to_effect",
        "reference_driver",
        "controller",
        "runtime",
    ):
        assert forbidden_fragment not in serialized_catalog
