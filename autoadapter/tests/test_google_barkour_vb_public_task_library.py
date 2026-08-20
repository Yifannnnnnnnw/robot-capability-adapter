from __future__ import annotations

import json
import math
from pathlib import Path

from autoadapter2.libraries.robot_package import _validate_sources, _validate_tasks


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "google_barkour_vb" / "1.0.0"
SOURCES_PATH = PACKAGE_ROOT / "tasks" / "sources.json"
CATALOG_PATH = PACKAGE_ROOT / "tasks" / "catalog.json"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"

SOURCE_ORDER = (
    "SRC-LEE-2020",
    "SRC-MIKI-2022",
    "SRC-BARKOUR-2023",
    "SRC-ANYMAL-2016",
    "SRC-MENAGERIE-BARKOUR-VB",
    "SRC-ROBEL-DKITTY",
    "SRC-SAFETY-GYM",
    "SRC-GYMNASIUM-ROBOTICS",
    "SRC-RRW-PAPER",
    "SRC-RRW-CODE",
    "SRC-DM-CONTROL",
)
REMOVED_SOURCE_IDS = {"SRC-SHI-2023", "SRC-QRC-2023"}
NEW_SOURCE_EXPECTATIONS = {
    "SRC-ROBEL-DKITTY": {
        "version_or_date": "commit 5b0fd3704629931712c6e0f7268ace1c2154dc83; Apache-2.0",
        "locator": "https://github.com/google-research/robel/tree/5b0fd3704629931712c6e0f7268ace1c2154dc83/robel/dkitty",
        "specific_reference": "robel/dkitty/__init__.py, stand.py, orient.py, and walk.py",
    },
    "SRC-SAFETY-GYM": {
        "version_or_date": "commit f31042f2f9ee61b9034dd6a416955972911544f5; MIT",
        "locator": "https://github.com/openai/safety-gym/tree/f31042f2f9ee61b9034dd6a416955972911544f5/safety_gym",
        "specific_reference": "safety_gym/envs/engine.py and suite.py",
    },
    "SRC-GYMNASIUM-ROBOTICS": {
        "version_or_date": "v1.4.2, commit 42eae53b2b27321d29090c219ce2f675c596de77; repository MIT; AntMaze source file Apache-2.0",
        "locator": "https://github.com/Farama-Foundation/Gymnasium-Robotics/tree/42eae53b2b27321d29090c219ce2f675c596de77/gymnasium_robotics",
        "specific_reference": "gymnasium_robotics/envs/maze/maps.py, maze/maze_v4.py, ant_maze_v5.py, and package __init__.py",
    },
    "SRC-RRW-PAPER": {
        "version_or_date": "arXiv:2409.07409v2, Section IV-B PDF p.4; CC BY 4.0",
        "locator": "https://arxiv.org/pdf/2409.07409v2",
        "specific_reference": "Section IV-B, PDF p.4",
    },
    "SRC-RRW-CODE": {
        "version_or_date": "commit 4ed5ddb20d1261bb08f9081736c0b049a1306069; MIT",
        "locator": "https://github.com/zst1406217/robust_robot_walker/tree/4ed5ddb20d1261bb08f9081736c0b049a1306069",
        "specific_reference": "a1_bar_track_config.py",
    },
    "SRC-DM-CONTROL": {
        "version_or_date": "1.0.34, commit 3831fa523c160fe653d83a829d0a0c59715f5725; Apache-2.0",
        "locator": "https://github.com/google-deepmind/dm_control/tree/3831fa523c160fe653d83a829d0a0c59715f5725/dm_control",
        "specific_reference": "suite/quadruped.py, suite/quadruped.xml, and utils/rewards.py",
    },
}

TASK_ORDER = (
    "lee_eight_direction_velocity_tracking",
    "lee_single_step_ascent",
    "lee_single_step_descent",
    "robel_dkitty_stand_fixed",
    "miki_high_rate_turn_in_place",
    "miki_mixed_obstacle_path",
    "robel_dkitty_orient_fixed",
    "robel_dkitty_walk_fixed",
    "safety_gym_doggo_button0",
    "safety_gym_doggo_push0",
    "gymnasium_antmaze_umaze_sparse",
    "rrw_tiny_trap_bar",
    "rrw_tiny_trap_pit",
    "rrw_tiny_trap_pole",
    "dm_control_quadruped_escape",
    "barkour_pause_table_transition",
    "barkour_weave_poles",
    "barkour_a_frame",
    "barkour_broad_jump",
    "barkour_complete_course",
)
REMOVED_TASK_IDS = {
    "lee_payload_step_ascent",
    "qrc_crossing_ramps",
    "qrc_compliant_floor_stepovers",
    "qrc_pallet_pipes",
    "qrc_diagonal_krails",
    "qrc_diagonal_crate_gap",
    "shi_blocks_six_direction",
    "shi_stairs_six_direction",
    "shi_stepping_stones_six_direction",
    "shi_poles_six_direction",
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_clause(
    clause_id: str,
    metric: str,
    unit: str,
    comparator: str,
    threshold: float,
    temporal: dict,
    aggregation: dict,
    refs: tuple[tuple[str, str], ...],
    provenance: str | None = None,
) -> dict:
    expected = {
        "clause_id": clause_id,
        "metric": metric,
        "unit": unit,
        "comparator": comparator,
        "threshold": threshold,
        "temporal": temporal,
        "aggregation": aggregation,
        "refs": refs,
    }
    if provenance is not None:
        expected["threshold_provenance"] = provenance
    return expected


EXPECTED_SCORING = {
    "lee_eight_direction_velocity_tracking": (
        _expected_clause("tracking_speed", "mean_body_planar_speed", "m/s", ">=", 0.4, {"kind": "terminal_state"}, {"kind": "per_trial"}, (("SRC-LEE-2020", "direct"),)),
        _expected_clause("heading_accuracy", "mean_body_heading_error", "deg", "<=", 10.0, {"kind": "terminal_state"}, {"kind": "per_trial_mean"}, (("SRC-LEE-2020", "direct"),)),
    ),
    "lee_single_step_ascent": (
        _expected_clause("step_ascent_completion", "all_feet_step_completion", "binary", "==", 1.0, {"kind": "within", "duration_s": 10.0}, {"kind": "per_trial"}, (("SRC-LEE-2020", "adapted"),)),
    ),
    "lee_single_step_descent": (
        _expected_clause("step_descent_completion", "all_feet_step_completion", "binary", "==", 1.0, {"kind": "within", "duration_s": 10.0}, {"kind": "per_trial"}, (("SRC-LEE-2020", "adapted"),)),
    ),
    "robel_dkitty_stand_fixed": (
        _expected_clause("joint_pose_error", "mean_absolute_joint_position_error", "rad", "<", math.pi / 12, {"kind": "terminal_state"}, {"kind": "single_trial"}, (("SRC-ROBEL-DKITTY", "direct"),), "source_direct; terminal sampling is a stricter interpretation"),
        _expected_clause("upright", "base_world_z_upright_dot", "ratio", ">", 0.9, {"kind": "terminal_state"}, {"kind": "single_trial"}, (("SRC-ROBEL-DKITTY", "direct"),), "source_direct; terminal sampling is a stricter interpretation"),
    ),
    "miki_high_rate_turn_in_place": (
        _expected_clause("turn_rate", "mean_body_yaw_rate", "rad/s", ">=", 3.0, {"kind": "continuous", "duration_s": 1.0}, {"kind": "single_trial"}, (("SRC-MIKI-2022", "direct"),)),
    ),
    "miki_mixed_obstacle_path": (
        _expected_clause("mixed_course_completion", "ordered_obstacle_completion", "ratio", "==", 1.0, {"kind": "eventual"}, {"kind": "single_trial"}, (("SRC-MIKI-2022", "adapted"),)),
    ),
    "robel_dkitty_orient_fixed": (
        _expected_clause("heading_error", "target_direction_error", "rad", "<", 0.087, {"kind": "terminal_state"}, {"kind": "single_trial"}, (("SRC-ROBEL-DKITTY", "direct"),), "source_direct"),
        _expected_clause("upright", "base_world_z_upright_dot", "ratio", ">", 0.96, {"kind": "terminal_state"}, {"kind": "single_trial"}, (("SRC-ROBEL-DKITTY", "direct"),), "source_direct"),
    ),
    "robel_dkitty_walk_fixed": (
        _expected_clause("goal_distance", "target_planar_distance", "m", "<", 0.5, {"kind": "terminal_state"}, {"kind": "single_trial"}, (("SRC-ROBEL-DKITTY", "direct"),), "source_direct"),
        _expected_clause("heading_alignment", "target_heading_cosine", "ratio", ">", 0.9, {"kind": "terminal_state"}, {"kind": "single_trial"}, (("SRC-ROBEL-DKITTY", "direct"),), "source_direct"),
    ),
    "safety_gym_doggo_button0": (
        _expected_clause("button_contact", "target_button_robot_contact", "binary", "==", 1.0, {"kind": "within", "max_control_steps": 1000}, {"kind": "single_trial"}, (("SRC-SAFETY-GYM", "direct"),), "source_direct; first-event temporal interpretation adapted"),
    ),
    "safety_gym_doggo_push0": (
        _expected_clause("block_goal_distance", "block_goal_planar_distance", "m", "<=", 0.3, {"kind": "within", "max_control_steps": 1000}, {"kind": "single_trial"}, (("SRC-SAFETY-GYM", "direct"),), "source_direct"),
    ),
    "gymnasium_antmaze_umaze_sparse": (
        _expected_clause("maze_goal_distance", "torso_goal_planar_distance", "m", "<=", 0.45, {"kind": "within", "max_control_steps": 700}, {"kind": "single_trial"}, (("SRC-GYMNASIUM-ROBOTICS", "direct"),), "source_direct"),
    ),
    "rrw_tiny_trap_bar": (
        _expected_clause("bar_track_completion", "goal_distance_with_survival", "m", "<", 0.2, {"kind": "within", "duration_s": 300.0}, {"kind": "per_trial"}, (("SRC-RRW-PAPER", "direct"), ("SRC-RRW-CODE", "direct")), "source_direct"),
    ),
    "rrw_tiny_trap_pit": (
        _expected_clause("pit_track_completion", "goal_distance_with_survival", "m", "<", 0.2, {"kind": "within", "duration_s": 300.0}, {"kind": "per_trial"}, (("SRC-RRW-PAPER", "direct"), ("SRC-RRW-CODE", "direct")), "source_direct"),
    ),
    "rrw_tiny_trap_pole": (
        _expected_clause("pole_track_completion", "goal_distance_with_survival", "m", "<", 0.2, {"kind": "within", "duration_s": 300.0}, {"kind": "per_trial"}, (("SRC-RRW-PAPER", "direct"), ("SRC-RRW-CODE", "direct")), "source_direct"),
    ),
    "dm_control_quadruped_escape": (
        _expected_clause("escape_radius", "workspace_radius", "m", ">=", 30.0, {"kind": "terminal_state"}, {"kind": "single_trial"}, (("SRC-DM-CONTROL", "direct"),), "source_formula_derived; terminal conjunction is a stricter interpretation"),
        _expected_clause("escape_upright", "base_upright_cosine", "ratio", ">=", 0.9396926208, {"kind": "terminal_state"}, {"kind": "single_trial"}, (("SRC-DM-CONTROL", "direct"),), "source_formula_derived; terminal conjunction is a stricter interpretation"),
    ),
    "barkour_pause_table_transition": (
        _expected_clause("table_step_off", "start_table_center_distance", "m", ">=", 0.7, {"kind": "eventual"}, {"kind": "single_trial"}, (("SRC-BARKOUR-2023", "direct"),)),
        _expected_clause("end_table_hold", "end_table_center_error", "m", "<=", 0.4, {"kind": "dwell", "duration_s": 5.0}, {"kind": "single_trial"}, (("SRC-BARKOUR-2023", "direct"),)),
    ),
    "barkour_weave_poles": (
        _expected_clause("weave_order", "ordered_waypoint_completion", "ratio", "==", 1.0, {"kind": "eventual"}, {"kind": "single_trial"}, (("SRC-BARKOUR-2023", "direct"),)),
        _expected_clause("pole_clearance", "minimum_torso_pole_clearance", "m", ">=", 0.1, {"kind": "terminal_state"}, {"kind": "single_trial"}, (("SRC-BARKOUR-2023", "direct"),)),
    ),
    "barkour_a_frame": (
        _expected_clause("a_frame_completion", "ordered_a_frame_line_completion", "ratio", "==", 1.0, {"kind": "eventual"}, {"kind": "single_trial"}, (("SRC-BARKOUR-2023", "direct"),)),
    ),
    "barkour_broad_jump": (
        _expected_clause("broad_jump_landing", "all_feet_jump_completion", "binary", "==", 1.0, {"kind": "eventual"}, {"kind": "single_trial"}, (("SRC-BARKOUR-2023", "direct"),)),
        _expected_clause("broad_jump_no_touch", "jump_board_contact_steps", "physics_steps", "==", 0.0, {"kind": "terminal_state"}, {"kind": "single_trial"}, (("SRC-BARKOUR-2023", "direct"),)),
    ),
    "barkour_complete_course": (
        _expected_clause("course_order", "ordered_course_completion", "ratio", "==", 1.0, {"kind": "eventual"}, {"kind": "single_trial"}, (("SRC-BARKOUR-2023", "direct"),)),
        _expected_clause("course_time", "course_completion_time", "s", "<=", 10.64, {"kind": "terminal_state"}, {"kind": "single_trial"}, (("SRC-BARKOUR-2023", "direct"),)),
        _expected_clause("course_jump_no_touch", "jump_board_contact_steps", "physics_steps", "==", 0.0, {"kind": "terminal_state"}, {"kind": "single_trial"}, (("SRC-BARKOUR-2023", "direct"),)),
        _expected_clause("course_end_hold", "end_table_center_error", "m", "<=", 0.4, {"kind": "dwell", "duration_s": 5.0}, {"kind": "single_trial"}, (("SRC-BARKOUR-2023", "direct"),)),
    ),
}

EXPECTED_NEW_PARAMETERS = {
    "robel_dkitty_stand_fixed": {
        "duration_s": {"type": "number", "unit": "s", "frame": "none", "description": "fixed source horizon; allowed range [8, 8] s"},
        "target_joint_positions_rad": {"type": "array", "unit": "rad", "frame": "barkour_named_joint_order", "description": "12 target joint positions; each value must remain within its local joint limits", "items": {"type": "number"}, "length": 12},
    },
    "robel_dkitty_orient_fixed": {
        "duration_s": {"type": "number", "unit": "s", "frame": "none", "description": "fixed source horizon; allowed range [8, 8] s"},
        "target_heading_rad": {"type": "number", "unit": "rad", "frame": "initial_body_yaw", "description": "requested heading; allowed range [-pi, pi] rad"},
    },
    "robel_dkitty_walk_fixed": {
        "duration_s": {"type": "number", "unit": "s", "frame": "none", "description": "fixed source horizon; allowed range [16, 16] s"},
        "target_xy_m": {"type": "array", "unit": "m", "frame": "initial_body_horizontal", "description": "constant target [2, 0] m in the initial body-horizontal frame", "items": {"type": "number"}, "length": 2},
    },
    "safety_gym_doggo_button0": {
        "max_control_steps": {"type": "integer", "unit": "env_step", "frame": "none", "description": "fixed source control budget; allowed range [1000, 1000] steps"},
        "goal_button_index": {"type": "integer", "unit": "index", "frame": "button_set", "description": "selected button index; allowed range [0, 3]"},
    },
    "safety_gym_doggo_push0": {
        "max_control_steps": {"type": "integer", "unit": "env_step", "frame": "none", "description": "fixed source control budget; allowed range [1000, 1000] steps"},
        "goal_center_xy_m": {"type": "array", "unit": "m", "frame": "task_world", "description": "planar goal center; each coordinate is in [-1, 1] m", "items": {"type": "number"}, "length": 2},
    },
    "gymnasium_antmaze_umaze_sparse": {
        "max_control_steps": {"type": "integer", "unit": "env_step", "frame": "none", "description": "fixed source control budget; allowed range [700, 700] steps"},
        "goal_cell_row_col": {"type": "array", "unit": "cell", "frame": "U_MAZE", "description": "goal cell row and column; any non-wall cell in the source U_MAZE map", "items": {"type": "number"}, "length": 2},
    },
    "rrw_tiny_trap_bar": {
        "duration_s": {"type": "number", "unit": "s", "frame": "none", "description": "fixed source trial horizon; allowed range [300, 300] s"},
        "runway_size_m": {"type": "array", "unit": "m", "frame": "runway", "description": "constant runway size [5, 60] m", "items": {"type": "number"}, "length": 2},
        "bar_count": {"type": "integer", "unit": "count", "frame": "fixture", "description": "fixed fixture count; allowed range [30, 30] bars"},
    },
    "rrw_tiny_trap_pit": {
        "duration_s": {"type": "number", "unit": "s", "frame": "none", "description": "fixed source trial horizon; allowed range [300, 300] s"},
        "runway_size_m": {"type": "array", "unit": "m", "frame": "runway", "description": "constant runway size [5, 60] m", "items": {"type": "number"}, "length": 2},
        "pit_count": {"type": "integer", "unit": "count", "frame": "fixture", "description": "fixed fixture count; allowed range [30, 30] pits"},
        "pit_width_range_m": {"type": "array", "unit": "m", "frame": "runway", "description": "source width range [.05, .2] m, quantized on a 0.025 m grid", "items": {"type": "number"}, "length": 2},
        "pit_depth_m": {"type": "number", "unit": "m", "frame": "fixture", "description": "fixed source pit depth; allowed range [1, 1] m"},
    },
    "rrw_tiny_trap_pole": {
        "duration_s": {"type": "number", "unit": "s", "frame": "none", "description": "fixed source trial horizon; allowed range [300, 300] s"},
        "runway_size_m": {"type": "array", "unit": "m", "frame": "runway", "description": "constant runway size [5, 60] m", "items": {"type": "number"}, "length": 2},
        "pole_count": {"type": "integer", "unit": "count", "frame": "fixture", "description": "fixed fixture count; allowed range [150, 150] poles"},
    },
    "dm_control_quadruped_escape": {
        "duration_s": {"type": "number", "unit": "s", "frame": "none", "description": "fixed source trial horizon; allowed range [20, 20] s"},
        "escape_radius_m": {"type": "number", "unit": "m", "frame": "world", "description": "fixed escape radius; allowed range [30, 30] m"},
        "max_tilt_deg": {"type": "number", "unit": "deg", "frame": "world", "description": "fixed maximum tilt; allowed range [20, 20] degrees"},
    },
}

NEW_REFERENCE_MARKERS = {
    ("robel_dkitty_stand_fixed", "joint_pose_error"): ("robel/dkitty/stand.py",),
    ("robel_dkitty_stand_fixed", "upright"): ("robel/dkitty/stand.py",),
    ("robel_dkitty_orient_fixed", "heading_error"): ("robel/dkitty/orient.py",),
    ("robel_dkitty_orient_fixed", "upright"): ("robel/dkitty/orient.py",),
    ("robel_dkitty_walk_fixed", "goal_distance"): ("robel/dkitty/walk.py",),
    ("robel_dkitty_walk_fixed", "heading_alignment"): ("robel/dkitty/walk.py",),
    ("safety_gym_doggo_button0", "button_contact"): ("safety_gym/envs/engine.py", "suite.py"),
    ("safety_gym_doggo_push0", "block_goal_distance"): ("safety_gym/envs/engine.py", "suite.py"),
    ("gymnasium_antmaze_umaze_sparse", "maze_goal_distance"): ("maze/maze_v4.py", "ant_maze_v5.py", "__init__.py"),
    ("rrw_tiny_trap_bar", "bar_track_completion"): ("Section IV-B, PDF p.4", "track.py"),
    ("rrw_tiny_trap_pit", "pit_track_completion"): ("Section IV-B, PDF p.4", "stumble_bar_track.py"),
    ("rrw_tiny_trap_pole", "pole_track_completion"): ("Section IV-B, PDF p.4", "track.py"),
    ("dm_control_quadruped_escape", "escape_radius"): ("suite/quadruped.py", "suite/quadruped.xml"),
    ("dm_control_quadruped_escape", "escape_upright"): ("suite/quadruped.py", "utils/rewards.py"),
}


def test_google_barkour_vb_public_task_library_snapshot() -> None:
    sources_document = _read_json(SOURCES_PATH)
    catalog_document = _read_json(CATALOG_PATH)
    morphology = _read_json(MORPHOLOGY_PATH)

    validated_sources = _validate_sources(sources_document, path=SOURCES_PATH)
    validated_tasks = _validate_tasks(
        catalog_document,
        path=CATALOG_PATH,
        source_ids={source["source_id"] for source in validated_sources},
    )

    assert sources_document == {
        "schema_version": "1.0",
        "robot_configuration_id": "google_barkour_vb",
        "package_version": "1.0.0",
        "sources": sources_document["sources"],
    }
    assert catalog_document["schema_version"] == "1.0"
    assert catalog_document["robot_configuration_id"] == "google_barkour_vb"
    assert catalog_document["package_version"] == "1.0.0"
    assert catalog_document["snapshot_id"] == "google-barkour-vb-source-protocols-2026-08-20-v2"
    assert set(catalog_document) == {
        "schema_version",
        "robot_configuration_id",
        "package_version",
        "snapshot_id",
        "tasks",
    }

    source_ids = [source["source_id"] for source in validated_sources]
    assert source_ids == list(SOURCE_ORDER)
    assert len(source_ids) == len(set(source_ids)) == 11
    assert not set(source_ids) & REMOVED_SOURCE_IDS
    for source_id, expected in NEW_SOURCE_EXPECTATIONS.items():
        source = next(item for item in validated_sources if item["source_id"] == source_id)
        assert source["version_or_date"] == expected["version_or_date"]
        assert source["locator"] == expected["locator"]
        assert expected["specific_reference"] in source["specific_reference"]

    task_ids = [task["task_id"] for task in validated_tasks]
    assert task_ids == list(TASK_ORDER)
    assert len(task_ids) == len(set(task_ids)) == 20
    assert not set(task_ids) & REMOVED_TASK_IDS
    assert sum(len(task["scoring"]) for task in validated_tasks) == 31
    assert set(EXPECTED_SCORING) == set(task_ids)

    for task in validated_tasks:
        expected_clauses = EXPECTED_SCORING[task["task_id"]]
        assert len(task["scoring"]) == len(expected_clauses)
        for clause, expected in zip(task["scoring"], expected_clauses):
            for key in (
                "clause_id",
                "metric",
                "unit",
                "comparator",
                "threshold",
                "temporal",
                "aggregation",
            ):
                assert clause[key] == expected[key]
            assert [
                (source_ref["source_id"], source_ref["support"])
                for source_ref in clause["source_refs"]
            ] == list(expected["refs"])
            if "threshold_provenance" in expected:
                assert clause["threshold_provenance"] == expected["threshold_provenance"]
            else:
                assert "threshold_provenance" not in clause
            for marker in NEW_REFERENCE_MARKERS.get(
                (task["task_id"], clause["clause_id"]), ()
            ):
                assert any(marker in source_ref["specific_reference"] for source_ref in clause["source_refs"])

    for task_id, expected_properties in EXPECTED_NEW_PARAMETERS.items():
        task = next(item for item in validated_tasks if item["task_id"] == task_id)
        parameters = task["invocation_schema"]["request"]["task_parameters"]
        assert list(parameters["properties"]) == list(expected_properties)
        assert parameters["required"] == list(expected_properties)
        assert parameters["additional_properties"] is False
        for name, expected_schema in expected_properties.items():
            assert parameters["properties"][name] == expected_schema

    new_tasks = [task for task in validated_tasks if task["task_id"] in EXPECTED_NEW_PARAMETERS]
    new_text = json.dumps(new_tasks, ensure_ascii=False).lower()
    for forbidden in ("reported outcome", "reported policy", "4.983", "4.951", "4.954", "4.93"):
        assert forbidden not in new_text
    assert all(
        "reported" not in clause["threshold_provenance"].lower()
        for task in new_tasks
        for clause in task["scoring"]
    )

    assert morphology["public_control"]["actuation"] == "joint_position"
    assert all(
        "joint-position servo actuators" in task["applicability"]
        and "physical task-family applicability only" in task["applicability"]
        for task in validated_tasks
    )

    public_json = json.dumps({"sources": sources_document, "catalog": catalog_document}, ensure_ascii=False)
    assert "GO2-T" not in public_json
    assert "unitree-go2-stock-12dof" not in public_json
    assert "joint_torque" not in public_json

    runnable_index = _read_json(RUNNABLE_INDEX_PATH)
    assert "google_barkour_vb" not in runnable_index["robots"]

    research_index = _read_json(RESEARCH_INDEX_PATH)
    research_candidate = next(
        candidate
        for candidate in research_index["candidates"]
        if candidate["robot_configuration_id"] == "google_barkour_vb"
    )
    research_observations = {
        item["kind"]: item["observation"]
        for item in research_candidate["locally_observed_source_material"]
    }
    assert "20-task public source contract is closed" in research_observations["canonical_task_sources"]
    assert "11 pinned source records" in research_observations["canonical_task_sources"]
    assert "20-task public source contract is closed" in research_observations["canonical_task_catalog"]
    assert "31 source-backed scoring clauses" in research_observations["canonical_task_catalog"]
    assert "catalog research state only" in research_observations["canonical_task_catalog"]
    assert all("remaining 10" not in missing for missing in research_candidate["missing_for_runnable_package"])
    assert any("Create Framework-private tasks/private" in missing for missing in research_candidate["missing_for_runnable_package"])
    assert any("dynamic canary" in missing for missing in research_candidate["missing_for_runnable_package"])
    assert research_candidate["observed_task_count"] == 5

    task_entries = list((PACKAGE_ROOT / "tasks").iterdir())
    assert sorted(entry.name for entry in task_entries) == ["catalog.json", "sources.json"]
    assert all(entry.is_file() for entry in task_entries)
    assert not (PACKAGE_ROOT / "tasks" / "private").exists()
    assert not (PACKAGE_ROOT / "skeleton").exists()
    assert not (PACKAGE_ROOT / "reference").exists()
