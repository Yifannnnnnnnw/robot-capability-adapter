from __future__ import annotations

import copy
import json
from pathlib import Path

from autoadapter2.libraries.robot_package import _validate_sources, _validate_tasks


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "google_barkour_vb" / "1.0.0"
GO2_PACKAGE_ROOT = ROOT / "libraries" / "robots" / "unitree-go2-stock-12dof" / "1.0.0"
SOURCES_PATH = PACKAGE_ROOT / "tasks" / "sources.json"
CATALOG_PATH = PACKAGE_ROOT / "tasks" / "catalog.json"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"
GO2_SOURCES_PATH = GO2_PACKAGE_ROOT / "tasks" / "sources.json"
GO2_CATALOG_PATH = GO2_PACKAGE_ROOT / "tasks" / "catalog.json"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"

SOURCE_ORDER = (
    "SRC-LEE-2020",
    "SRC-MIKI-2022",
    "SRC-SHI-2023",
    "SRC-QRC-2023",
    "SRC-BARKOUR-2023",
    "SRC-ANYMAL-2016",
    "SRC-MENAGERIE-BARKOUR-VB",
)
TASK_PAIRS = (
    ("GO2-T01", "lee_eight_direction_velocity_tracking"),
    ("GO2-T02", "lee_single_step_ascent"),
    ("GO2-T03", "lee_single_step_descent"),
    ("GO2-T04", "lee_payload_step_ascent"),
    ("GO2-T05", "miki_high_rate_turn_in_place"),
    ("GO2-T06", "miki_mixed_obstacle_path"),
    ("GO2-T07", "qrc_crossing_ramps"),
    ("GO2-T08", "qrc_compliant_floor_stepovers"),
    ("GO2-T09", "qrc_pallet_pipes"),
    ("GO2-T10", "qrc_diagonal_krails"),
    ("GO2-T11", "qrc_diagonal_crate_gap"),
    ("GO2-T12", "shi_blocks_six_direction"),
    ("GO2-T13", "shi_stairs_six_direction"),
    ("GO2-T14", "shi_stepping_stones_six_direction"),
    ("GO2-T15", "shi_poles_six_direction"),
    ("GO2-T16", "barkour_pause_table_transition"),
    ("GO2-T17", "barkour_weave_poles"),
    ("GO2-T18", "barkour_a_frame"),
    ("GO2-T19", "barkour_broad_jump"),
    ("GO2-T20", "barkour_complete_course"),
)
APPLICABILITY_PREFIX = (
    "The local Barkour vB is a free-base quadruped with four three-joint legs, "
    "twelve directly mapped joint-position servo actuators, base pose and velocity "
    "observations, and named foot sites."
)
APPLICABILITY_SUFFIX = (
    "These source and morphology facts establish physical task-family applicability "
    "only; they do not establish a locomotion controller, gait, dynamic performance, "
    "or task success."
)
RATIONALES = {
    "lee_eight_direction_velocity_tracking": "The Lee operation is defined for a quadruped and requires planar body motion and heading measurement.",
    "lee_single_step_ascent": "The Lee operation is defined for a quadruped traversing one rigid positive step.",
    "lee_single_step_descent": "The Lee operation is defined for a quadruped traversing one rigid negative step.",
    "lee_payload_step_ascent": "The Lee operation fixes a centered torso payload and otherwise requires the same quadruped step traversal; it adds no actuator or manipulator.",
    "miki_high_rate_turn_in_place": "The free base supplies a yaw degree of freedom, so the Miki turn command is a physically meaningful quadruped operation.",
    "miki_mixed_obstacle_path": "The Miki operation requires quadruped locomotion across the cited rigid obstacle families.",
    "qrc_crossing_ramps": "The QRC source lane is explicitly defined for small quadrupeds, so its crossing-ramp operation matches this body plan.",
    "qrc_compliant_floor_stepovers": "The QRC source lane is explicitly defined for small quadrupeds, so its compliant-floor step-over operation matches this body plan.",
    "qrc_pallet_pipes": "The QRC source lane is explicitly defined for small quadrupeds, so its pallet-and-pipe operation matches this body plan.",
    "qrc_diagonal_krails": "The QRC source lane is explicitly defined for small quadrupeds, so its diagonal K-rail operation matches this body plan.",
    "qrc_diagonal_crate_gap": "The QRC source lane is explicitly defined for small quadrupeds, so its diagonal negative-obstacle operation matches this body plan.",
    "shi_blocks_six_direction": "The Shi source robot and the local Barkour vB home configuration both place the base approximately 0.28 m above the feet or floor and use twelve actuated leg joints, so block-terrain travel matches this body plan.",
    "shi_stairs_six_direction": "The Shi source robot and the local Barkour vB home configuration both place the base approximately 0.28 m above the feet or floor and use twelve actuated leg joints, so stair-terrain travel matches this body plan.",
    "shi_stepping_stones_six_direction": "The Shi source robot and the local Barkour vB home configuration both place the base approximately 0.28 m above the feet or floor and use twelve actuated leg joints, so stepping-stone travel matches this body plan.",
    "shi_poles_six_direction": "The Shi source robot and the local Barkour vB home configuration both place the base approximately 0.28 m above the feet or floor and use twelve actuated leg joints, so pole-terrain travel matches this body plan.",
    "barkour_pause_table_transition": "The Barkour source benchmark and the pinned local asset both identify Barkour vB, so the pause-table operation matches the modeled robot family.",
    "barkour_weave_poles": "The Barkour source benchmark and the pinned local asset both identify Barkour vB, so the weave-pole operation matches the modeled robot family.",
    "barkour_a_frame": "The Barkour source benchmark and the pinned local asset both identify Barkour vB, so the A-frame operation matches the modeled robot family.",
    "barkour_broad_jump": "The Barkour source benchmark and the pinned local asset both identify Barkour vB, so the broad-jump operation matches the modeled robot family.",
    "barkour_complete_course": "The Barkour source benchmark and the pinned local asset both identify Barkour vB, so the full-course operation matches the modeled robot family.",
}
SCENE_REPLACEMENTS = {
    "miki_mixed_obstacle_path": [
        "The four obstacle families appear once in source order and dimensions are scaled uniformly by 0.56."
    ],
    "qrc_crossing_ramps": [
        "A confined 7.2 m start-to-end lane preserves the source 15 degree crossing pitch/roll ramps."
    ],
    "qrc_compliant_floor_stepovers": [
        "A confined 7.2 m start-to-end lane preserves the source 10 cm compliant floor and 5 x 10 cm step-overs."
    ],
    "qrc_pallet_pipes": [
        "A confined 7.2 m start-to-end lane preserves the source 15 cm pallet elevation changes and rolling pipe edges."
    ],
    "qrc_diagonal_krails": [
        "A confined 7.2 m start-to-end lane preserves the source slippery floor and 10 x 10 cm rails at 45 degrees."
    ],
    "qrc_diagonal_crate_gap": [
        "A confined 7.2 m start-to-end lane preserves the source crate terrain and diagonal negative obstacle."
    ],
    "shi_blocks_six_direction": [
        "A 5 m source-family block-terrain lane provides direction-aligned resets and a finish line at the map limit."
    ],
    "shi_stairs_six_direction": [
        "A 5 m source-family stair-terrain lane provides direction-aligned resets and a finish line at the map limit."
    ],
    "shi_stepping_stones_six_direction": [
        "A 5 m source-family stepping-stone lane provides direction-aligned resets and a finish line at the map limit."
    ],
    "shi_poles_six_direction": [
        "A 5 m source-family pole-terrain lane provides direction-aligned resets and a finish line at the map limit."
    ],
}
SCALE_ADAPTATION = (
    "Obstacle lengths are multiplied by 0.56, the recorded ratio of the local Barkour vB "
    "0.28 m home base height to the source ANYmal 0.50 m height; time, success definition, "
    "repetition count, and normalized difficulty are unchanged."
)
PAYLOAD_ADAPTATION = (
    "The payload ratio remains exactly 22.7%; the 13.4 cm source step is morphology-scaled "
    "by 0.56 and the ten 10 s trials are retained."
)
PAYLOAD_SOURCE_ADAPTATION = (
    "Payload mass is 22.7% of the exact local MJCF mass; step height uses the recorded 0.56 "
    "morphology scale. No success or repetition requirement is weakened."
)
TASK_ADAPTATIONS = {
    "lee_single_step_ascent": SCALE_ADAPTATION,
    "lee_single_step_descent": SCALE_ADAPTATION,
    "lee_payload_step_ascent": PAYLOAD_ADAPTATION,
    "miki_mixed_obstacle_path": SCALE_ADAPTATION,
}
SOURCE_REF_ADAPTATIONS = {
    "lee_single_step_ascent": SCALE_ADAPTATION,
    "lee_single_step_descent": SCALE_ADAPTATION,
    "lee_payload_step_ascent": PAYLOAD_SOURCE_ADAPTATION,
    "miki_mixed_obstacle_path": SCALE_ADAPTATION,
}
TASK_PARAMETERS = {
    "lee_eight_direction_velocity_tracking": [
        "duration_s", "target_speed_m_s", "direction_rad"
    ],
    "lee_single_step_ascent": ["duration_s", "command_speed_m_s", "step_height_m"],
    "lee_single_step_descent": ["duration_s", "command_speed_m_s", "step_height_m"],
    "lee_payload_step_ascent": [
        "duration_s", "payload_mass_ratio", "step_height_m"
    ],
    "miki_high_rate_turn_in_place": ["duration_s", "target_yaw_rate_rad_s"],
    "miki_mixed_obstacle_path": ["duration_s", "path_waypoints_m"],
    "qrc_crossing_ramps": ["duration_s", "lane_length_m"],
    "qrc_compliant_floor_stepovers": ["duration_s", "lane_length_m"],
    "qrc_pallet_pipes": ["duration_s", "lane_length_m"],
    "qrc_diagonal_krails": ["duration_s", "lane_length_m"],
    "qrc_diagonal_crate_gap": ["duration_s", "lane_length_m"],
    "shi_blocks_six_direction": ["duration_s", "direction_rad", "map_limit_m"],
    "shi_stairs_six_direction": ["duration_s", "direction_rad", "map_limit_m"],
    "shi_stepping_stones_six_direction": [
        "duration_s", "direction_rad", "map_limit_m"
    ],
    "shi_poles_six_direction": ["duration_s", "direction_rad", "map_limit_m"],
    "barkour_pause_table_transition": ["duration_s", "end_table_center_m"],
    "barkour_weave_poles": ["duration_s", "path_waypoints_m"],
    "barkour_a_frame": ["duration_s", "incline_deg"],
    "barkour_broad_jump": ["duration_s", "board_length_m"],
    "barkour_complete_course": [
        "duration_s", "allotted_time_s", "path_waypoints_m", "end_table_center_m"
    ],
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_task(
    go2_task: dict, task_id: str, applicability: str
) -> dict:
    expected = copy.deepcopy(go2_task)
    expected["task_id"] = task_id
    expected["applicability"] = applicability
    if task_id in TASK_ADAPTATIONS:
        expected["adaptation"] = TASK_ADAPTATIONS[task_id]
    if task_id in SOURCE_REF_ADAPTATIONS:
        for clause in expected["scoring"]:
            for source_ref in clause["source_refs"]:
                if "adaptation" in source_ref:
                    source_ref["adaptation"] = SOURCE_REF_ADAPTATIONS[task_id]
    if task_id in SCENE_REPLACEMENTS:
        expected["scene_assumptions"] = SCENE_REPLACEMENTS[task_id]
    expected["invocation_schema"]["request"]["task_id"] = task_id
    return expected


def _clause_contract(
    clause_id: str,
    metric: str,
    unit: str,
    comparator: str,
    threshold: float,
    temporal: dict,
    aggregation: dict,
    source_id: str,
    support: str,
) -> dict:
    return {
        "clause_id": clause_id,
        "metric": metric,
        "unit": unit,
        "comparator": comparator,
        "threshold": threshold,
        "temporal": temporal,
        "aggregation": aggregation,
        "source_refs": ((source_id, support),),
    }


EXPECTED_SCORING = {
    "lee_eight_direction_velocity_tracking": (
        _clause_contract(
            "tracking_speed", "mean_body_planar_speed", "m/s", ">=", 0.4,
            {"kind": "terminal_state"}, {"kind": "per_trial"},
            "SRC-LEE-2020", "direct",
        ),
        _clause_contract(
            "heading_accuracy", "mean_body_heading_error", "deg", "<=", 10.0,
            {"kind": "terminal_state"}, {"kind": "per_trial_mean"},
            "SRC-LEE-2020", "direct",
        ),
    ),
    "lee_single_step_ascent": (
        _clause_contract(
            "step_ascent_completion", "all_feet_step_completion", "binary", "==", 1.0,
            {"kind": "within", "duration_s": 10.0}, {"kind": "per_trial"},
            "SRC-LEE-2020", "adapted",
        ),
    ),
    "lee_single_step_descent": (
        _clause_contract(
            "step_descent_completion", "all_feet_step_completion", "binary", "==", 1.0,
            {"kind": "within", "duration_s": 10.0}, {"kind": "per_trial"},
            "SRC-LEE-2020", "adapted",
        ),
    ),
    "lee_payload_step_ascent": (
        _clause_contract(
            "payload_step_completion", "all_feet_step_completion", "binary", "==", 1.0,
            {"kind": "within", "duration_s": 10.0}, {"kind": "per_trial"},
            "SRC-LEE-2020", "adapted",
        ),
    ),
    "miki_high_rate_turn_in_place": (
        _clause_contract(
            "turn_rate", "mean_body_yaw_rate", "rad/s", ">=", 3.0,
            {"kind": "continuous", "duration_s": 1.0}, {"kind": "single_trial"},
            "SRC-MIKI-2022", "direct",
        ),
    ),
    "miki_mixed_obstacle_path": (
        _clause_contract(
            "mixed_course_completion", "ordered_obstacle_completion", "ratio", "==", 1.0,
            {"kind": "eventual"}, {"kind": "single_trial"},
            "SRC-MIKI-2022", "adapted",
        ),
    ),
    "qrc_crossing_ramps": (
        _clause_contract(
            "lane_completion", "ordered_lane_completion", "ratio", "==", 1.0,
            {"kind": "eventual"}, {"kind": "single_trial"},
            "SRC-QRC-2023", "direct",
        ),
    ),
    "qrc_compliant_floor_stepovers": (
        _clause_contract(
            "lane_completion", "ordered_lane_completion", "ratio", "==", 1.0,
            {"kind": "eventual"}, {"kind": "single_trial"},
            "SRC-QRC-2023", "direct",
        ),
    ),
    "qrc_pallet_pipes": (
        _clause_contract(
            "lane_completion", "ordered_lane_completion", "ratio", "==", 1.0,
            {"kind": "eventual"}, {"kind": "single_trial"},
            "SRC-QRC-2023", "direct",
        ),
    ),
    "qrc_diagonal_krails": (
        _clause_contract(
            "lane_completion", "ordered_lane_completion", "ratio", "==", 1.0,
            {"kind": "eventual"}, {"kind": "single_trial"},
            "SRC-QRC-2023", "direct",
        ),
    ),
    "qrc_diagonal_crate_gap": (
        _clause_contract(
            "lane_completion", "ordered_lane_completion", "ratio", "==", 1.0,
            {"kind": "eventual"}, {"kind": "single_trial"},
            "SRC-QRC-2023", "direct",
        ),
    ),
    "shi_blocks_six_direction": (
        _clause_contract(
            "travel_distance", "maximum_travel_distance", "m", ">=", 4.983,
            {"kind": "terminal_state"}, {"kind": "per_trial_mean"},
            "SRC-SHI-2023", "direct",
        ),
    ),
    "shi_stairs_six_direction": (
        _clause_contract(
            "travel_distance", "maximum_travel_distance", "m", ">=", 4.951,
            {"kind": "terminal_state"}, {"kind": "per_trial_mean"},
            "SRC-SHI-2023", "direct",
        ),
    ),
    "shi_stepping_stones_six_direction": (
        _clause_contract(
            "travel_distance", "maximum_travel_distance", "m", ">=", 4.954,
            {"kind": "terminal_state"}, {"kind": "per_trial_mean"},
            "SRC-SHI-2023", "direct",
        ),
    ),
    "shi_poles_six_direction": (
        _clause_contract(
            "travel_distance", "maximum_travel_distance", "m", ">=", 4.93,
            {"kind": "terminal_state"}, {"kind": "per_trial_mean"},
            "SRC-SHI-2023", "direct",
        ),
    ),
    "barkour_pause_table_transition": (
        _clause_contract(
            "table_step_off", "start_table_center_distance", "m", ">=", 0.7,
            {"kind": "eventual"}, {"kind": "single_trial"},
            "SRC-BARKOUR-2023", "direct",
        ),
        _clause_contract(
            "end_table_hold", "end_table_center_error", "m", "<=", 0.4,
            {"kind": "dwell", "duration_s": 5.0}, {"kind": "single_trial"},
            "SRC-BARKOUR-2023", "direct",
        ),
    ),
    "barkour_weave_poles": (
        _clause_contract(
            "weave_order", "ordered_waypoint_completion", "ratio", "==", 1.0,
            {"kind": "eventual"}, {"kind": "single_trial"},
            "SRC-BARKOUR-2023", "direct",
        ),
        _clause_contract(
            "pole_clearance", "minimum_torso_pole_clearance", "m", ">=", 0.1,
            {"kind": "terminal_state"}, {"kind": "single_trial"},
            "SRC-BARKOUR-2023", "direct",
        ),
    ),
    "barkour_a_frame": (
        _clause_contract(
            "a_frame_completion", "ordered_a_frame_line_completion", "ratio", "==", 1.0,
            {"kind": "eventual"}, {"kind": "single_trial"},
            "SRC-BARKOUR-2023", "direct",
        ),
    ),
    "barkour_broad_jump": (
        _clause_contract(
            "broad_jump_landing", "all_feet_jump_completion", "binary", "==", 1.0,
            {"kind": "eventual"}, {"kind": "single_trial"},
            "SRC-BARKOUR-2023", "direct",
        ),
        _clause_contract(
            "broad_jump_no_touch", "jump_board_contact_steps", "physics_steps", "==", 0.0,
            {"kind": "terminal_state"}, {"kind": "single_trial"},
            "SRC-BARKOUR-2023", "direct",
        ),
    ),
    "barkour_complete_course": (
        _clause_contract(
            "course_order", "ordered_course_completion", "ratio", "==", 1.0,
            {"kind": "eventual"}, {"kind": "single_trial"},
            "SRC-BARKOUR-2023", "direct",
        ),
        _clause_contract(
            "course_time", "course_completion_time", "s", "<=", 10.64,
            {"kind": "terminal_state"}, {"kind": "single_trial"},
            "SRC-BARKOUR-2023", "direct",
        ),
        _clause_contract(
            "course_jump_no_touch", "jump_board_contact_steps", "physics_steps", "==", 0.0,
            {"kind": "terminal_state"}, {"kind": "single_trial"},
            "SRC-BARKOUR-2023", "direct",
        ),
        _clause_contract(
            "course_end_hold", "end_table_center_error", "m", "<=", 0.4,
            {"kind": "dwell", "duration_s": 5.0}, {"kind": "single_trial"},
            "SRC-BARKOUR-2023", "direct",
        ),
    ),
}


def _scoring_signature(clause: dict) -> dict:
    return {
        key: clause[key]
        for key in (
            "clause_id",
            "metric",
            "unit",
            "comparator",
            "threshold",
            "temporal",
            "aggregation",
        )
    } | {
        "source_refs": tuple(
            (source_ref["source_id"], source_ref["support"])
            for source_ref in clause["source_refs"]
        )
    }


def test_google_barkour_vb_public_task_library_snapshot() -> None:
    sources_document = _read_json(SOURCES_PATH)
    catalog_document = _read_json(CATALOG_PATH)
    morphology = _read_json(MORPHOLOGY_PATH)
    go2_sources_document = _read_json(GO2_SOURCES_PATH)
    go2_catalog_document = _read_json(GO2_CATALOG_PATH)

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
    assert catalog_document["snapshot_id"] == "google-barkour-vb-source-protocols-2026-08-20-v1"
    assert set(sources_document) == {
        "schema_version", "robot_configuration_id", "package_version", "sources"
    }
    assert set(catalog_document) == {
        "schema_version", "robot_configuration_id", "package_version", "snapshot_id", "tasks"
    }

    source_ids = [source["source_id"] for source in validated_sources]
    assert source_ids == list(SOURCE_ORDER)
    assert len(source_ids) == len(set(source_ids)) == 7
    task_ids = [task["task_id"] for task in validated_tasks]
    assert task_ids == [task_id for _, task_id in TASK_PAIRS]
    assert len(task_ids) == len(set(task_ids)) == 20
    assert sum(len(task["scoring"]) for task in validated_tasks) == 27
    assert set(EXPECTED_SCORING) == set(task_ids)

    go2_source_by_id = {
        source["source_id"]: source for source in go2_sources_document["sources"]
    }
    assert sources_document["sources"][:5] == [
        go2_source_by_id[source_id] for source_id in SOURCE_ORDER[:5]
    ]
    assert sources_document["sources"][5] == {
        "source_id": "SRC-ANYMAL-2016",
        "title": "ANYmal - a Highly Mobile and Dynamic Quadrupedal Robot",
        "organization": "IEEE/RSJ International Conference on Intelligent Robots and Systems",
        "version_or_date": "2016; DOI 10.1109/IROS.2016.7758092",
        "locator": "https://doi.org/10.1109/IROS.2016.7758092",
        "specific_reference": "Abstract and platform description: the source ANYmal platform is 0.5 m tall. This dimension is used only to make the recorded 0.56 morphology scale conversion to the local Barkour vB package's 0.28 m home base height.",
    }
    assert sources_document["sources"][6] == {
        "source_id": "SRC-MENAGERIE-BARKOUR-VB",
        "title": "MuJoCo Menagerie: Google Barkour vB model",
        "organization": "Google DeepMind",
        "version_or_date": "commit da76818e269b82289eba39808e2fb91d679d6994",
        "locator": "https://github.com/google-deepmind/mujoco_menagerie/tree/da76818e269b82289eba39808e2fb91d679d6994/google_barkour_vb",
        "specific_reference": "Pinned google_barkour_vb model directory: free-base Barkour vB body, four three-joint legs, twelve direct joint-position general actuators with affine servo bias, joint limits, named foot sites, and base/joint sensors used by the complete local MJCF closure.",
    }

    go2_task_by_id = {
        task["task_id"]: task for task in go2_catalog_document["tasks"]
    }
    for go2_task_id, barkour_task_id in TASK_PAIRS:
        task = next(item for item in validated_tasks if item["task_id"] == barkour_task_id)
        applicability = (
            APPLICABILITY_PREFIX
            + " "
            + RATIONALES[barkour_task_id]
            + " "
            + APPLICABILITY_SUFFIX
        )
        assert task == _expected_task(
            go2_task_by_id[go2_task_id], barkour_task_id, applicability
        )
        assert task["applicability"] == applicability
        assert task["scoring"]
        assert tuple(_scoring_signature(clause) for clause in task["scoring"]) == EXPECTED_SCORING[
            barkour_task_id
        ]

        paired_scoring = go2_task_by_id[go2_task_id]["scoring"]
        for clause, paired_clause in zip(task["scoring"], paired_scoring):
            assert [
                source_ref["specific_reference"]
                for source_ref in clause["source_refs"]
            ] == [
                source_ref["specific_reference"]
                for source_ref in paired_clause["source_refs"]
            ]
        if barkour_task_id in SOURCE_REF_ADAPTATIONS:
            adapted_refs = [
                source_ref
                for clause in task["scoring"]
                for source_ref in clause["source_refs"]
                if source_ref["support"] == "adapted"
            ]
            assert len(adapted_refs) == 1
            assert adapted_refs[0]["adaptation"] == SOURCE_REF_ADAPTATIONS[barkour_task_id]

        parameters = task["invocation_schema"]["request"]["task_parameters"]
        assert task["invocation_schema"]["request"]["task_id"] == barkour_task_id
        assert parameters["required"] == TASK_PARAMETERS[barkour_task_id]
        assert list(parameters["properties"]) == TASK_PARAMETERS[barkour_task_id]
        assert parameters["additional_properties"] is False
        assert not set(parameters["properties"]) & {
            "actuator", "gait", "controller", "private_instance"
        }

    assert morphology["public_control"]["actuation"] == "joint_position"
    assert all(
        APPLICABILITY_PREFIX in task["applicability"]
        and "joint-position servo actuators" in task["applicability"]
        and APPLICABILITY_SUFFIX in task["applicability"]
        for task in validated_tasks
    )

    public_json = json.dumps(
        {"sources": sources_document, "catalog": catalog_document},
        ensure_ascii=False,
    )
    for forbidden in (
        "GO2-T",
        "unitree-go2-stock-12dof",
        "0.54",
        "0.27",
        "joint_torque",
    ):
        assert forbidden not in public_json

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
    catalog_observation = research_observations["canonical_task_catalog"]
    assert "provisional" in catalog_observation
    assert "20 catalog records" in catalog_observation
    assert "10 source-closed task directions" in catalog_observation
    assert "Lee payload" in catalog_observation
    assert "QRC" in catalog_observation
    assert "Shi" in catalog_observation
    assert "runnable evidence" in catalog_observation
    assert any(
        "remaining 10" in missing
        and "primary-source anchors" in missing
        and "reported outcomes" in missing
        and "pass thresholds" in missing
        for missing in research_candidate["missing_for_runnable_package"]
    )
    assert any(
        "dynamic canary" in missing
        for missing in research_candidate["missing_for_runnable_package"]
    )
    task_entries = list((PACKAGE_ROOT / "tasks").iterdir())
    assert sorted(entry.name for entry in task_entries) == ["catalog.json", "sources.json"]
    assert all(entry.is_file() for entry in task_entries)
    assert not (PACKAGE_ROOT / "tasks" / "private").exists()
    assert not (PACKAGE_ROOT / "skeleton").exists()
    assert not (PACKAGE_ROOT / "reference").exists()
