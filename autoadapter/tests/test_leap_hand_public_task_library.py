from __future__ import annotations

import json
import math
from pathlib import Path

from autoadapter2.libraries.robot_package import _validate_sources, _validate_tasks


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "leap_hand" / "1.0.0"
SOURCES_PATH = PACKAGE_ROOT / "tasks" / "sources.json"
CATALOG_PATH = PACKAGE_ROOT / "tasks" / "catalog.json"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"

SOURCE_ORDER = (
    "SRC-EC-BENCHMARK-2021",
    "SRC-GYMNASIUM-HAND-1.4.2",
    "SRC-ROBEL-DCLAW-2019",
    "SRC-MYOSUITE-2.12.2",
)

EC_TASK_ORDER = (
    "ec_pinch",
    "ec_dynamic_tripod",
    "ec_squeeze",
    "ec_twiddle",
    "ec_rock",
    "ec_rock_ii",
    "ec_radial_roll",
    "ec_index_roll",
    "ec_full_roll",
    "ec_rotary_step",
    "ec_interdigital_step",
    "ec_linear_step",
    "ec_palmar_slide",
)

TASK_ORDER = EC_TASK_ORDER + (
    "gym_hand_reach_all_fingertips",
    "gym_hand_manipulate_block_full_pose",
    "robel_dclaw_pose_fixed",
    "robel_dclaw_turn_fixed",
    "robel_dclaw_screw_fixed",
    "myosuite_object_hold_fixed",
    "myosuite_baoding_p1",
)

SOURCE_EXPECTATIONS = {
    "SRC-EC-BENCHMARK-2021": {
        "title": "The Elliott and Connolly Benchmark: A Test for Evaluating the In-Hand Dexterity of Robot Hands",
        "organization": "IEEE-RAS International Conference on Humanoid Robots; Carnegie Mellon University",
        "version": "10.1109/HUMANOIDS47582.2021.9555798",
        "locator": "https://doi.org/10.1109/HUMANOIDS47582.2021.9555798",
        "markers": ("Section I-A", "Section II-A", "Table II", "Section II-B", "Appendix I"),
    },
    "SRC-GYMNASIUM-HAND-1.4.2": {
        "title": "Gymnasium-Robotics Shadow Dexterous Hand task protocols",
        "organization": "Farama Foundation",
        "version": "42eae53b2b27321d29090c219ce2f675c596de77",
        "locator": "https://github.com/Farama-Foundation/Gymnasium-Robotics/tree/42eae53b2b27321d29090c219ce2f675c596de77/gymnasium_robotics/envs/shadow_dexterous_hand",
        "markers": ("reach.py", "manipulate.py", "manipulate_block.py", "__init__.py"),
    },
    "SRC-ROBEL-DCLAW-2019": {
        "title": "ROBEL D'Claw task protocols",
        "organization": "Google Research",
        "version": "5b0fd3704629931712c6e0f7268ace1c2154dc83",
        "locator": "https://github.com/google-research/robel/tree/5b0fd3704629931712c6e0f7268ace1c2154dc83/robel/dclaw",
        "markers": ("pose.py", "turn.py", "screw.py", "__init__.py"),
    },
    "SRC-MYOSUITE-2.12.2": {
        "title": "MyoSuite hand task implementations",
        "organization": "MyoHub",
        "version": "94300995076b20ed6a8cfc65794c54bc997a0697",
        "locator": "https://github.com/MyoHub/myosuite/tree/94300995076b20ed6a8cfc65794c54bc997a0697/myosuite/envs/myo",
        "markers": ("obj_hold_v0.py", "env_base.py", "baoding_v1.py", "__init__.py"),
    },
}


def _clause(
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
        "source_id": source_id,
        "support": support,
    }


EXPECTED_SCORING = {
    task_id: (
        _clause(
            "three_source_pattern_successes",
            f"{task_id}_successful_trial_count",
            "trial",
            "==",
            3,
            {"kind": "fixed_trials", "trial_count": 3},
            {"kind": "all_trials"},
            "SRC-EC-BENCHMARK-2021",
            "adapted",
        ),
    )
    for task_id in EC_TASK_ORDER
}
EXPECTED_SCORING.update(
    {
        "gym_hand_reach_all_fingertips": (
            _clause(
                "all_fingertip_goal_error",
                "concatenated_fingertip_cartesian_l2_error",
                "m",
                "<",
                0.00894427190999916,
                {"kind": "terminal_step", "max_control_steps": 50},
                {"kind": "all_four_fingertips"},
                "SRC-GYMNASIUM-HAND-1.4.2",
                "adapted",
            ),
        ),
        "gym_hand_manipulate_block_full_pose": (
            _clause(
                "block_position_error",
                "block_target_euclidean_position_error",
                "m",
                "<",
                0.01,
                {"kind": "terminal_step", "max_control_steps": 100},
                {"kind": "same_state_conjunction"},
                "SRC-GYMNASIUM-HAND-1.4.2",
                "adapted",
            ),
            _clause(
                "block_orientation_error",
                "block_target_shortest_quaternion_angle_error",
                "rad",
                "<",
                0.1,
                {"kind": "terminal_step", "max_control_steps": 100},
                {"kind": "same_state_conjunction"},
                "SRC-GYMNASIUM-HAND-1.4.2",
                "adapted",
            ),
        ),
        "robel_dclaw_pose_fixed": (
            _clause(
                "maximum_joint_pose_error",
                "maximum_absolute_joint_position_error",
                "rad",
                "<",
                10 * math.pi / 180,
                {"kind": "terminal_step", "max_control_steps": 80, "duration_s": 4},
                {"kind": "maximum_over_all_16_joints"},
                "SRC-ROBEL-DCLAW-2019",
                "adapted",
            ),
        ),
        "robel_dclaw_turn_fixed": (
            _clause(
                "fixture_target_angle_error",
                "absolute_wrapped_fixture_target_angle_error",
                "rad",
                "<",
                0.1,
                {"kind": "terminal_step", "max_control_steps": 40, "duration_s": 4},
                {"kind": "single_trial"},
                "SRC-ROBEL-DCLAW-2019",
                "direct",
            ),
        ),
        "robel_dclaw_screw_fixed": (
            _clause(
                "continuous_fixture_tracking_error",
                "maximum_absolute_unbounded_fixture_target_angle_error",
                "rad",
                "<",
                0.2,
                {"kind": "continuous", "max_control_steps": 80, "duration_s": 8},
                {"kind": "maximum_over_control_steps"},
                "SRC-ROBEL-DCLAW-2019",
                "adapted",
            ),
        ),
        "myosuite_object_hold_fixed": (
            _clause(
                "minimum_solved_steps",
                "object_hold_solved_control_step_count",
                "control_step",
                ">",
                5,
                {"kind": "fixed_horizon", "max_control_steps": 75},
                {"kind": "count_successful_steps"},
                "SRC-MYOSUITE-2.12.2",
                "direct",
            ),
            _clause(
                "no_drop",
                "object_hold_drop_event_count",
                "event",
                "==",
                0,
                {"kind": "fixed_horizon", "max_control_steps": 75},
                {"kind": "count_events"},
                "SRC-MYOSUITE-2.12.2",
                "adapted",
            ),
        ),
        "myosuite_baoding_p1": (
            _clause(
                "perfect_two_ball_tracking_fraction",
                "mean_two_ball_solved_fraction",
                "ratio",
                "==",
                1,
                {"kind": "fixed_horizon", "max_control_steps": 200},
                {"kind": "mean_over_control_steps"},
                "SRC-MYOSUITE-2.12.2",
                "adapted",
            ),
        ),
    }
)

EXPECTED_PARAMETER_ORDER = {
    **{task_id: ("object_id", "trial_count") for task_id in EC_TASK_ORDER},
    "gym_hand_reach_all_fingertips": ("target_fingertip_positions_m", "max_control_steps"),
    "gym_hand_manipulate_block_full_pose": (
        "target_position_offset_m",
        "target_orientation_wxyz",
        "max_control_steps",
    ),
    "robel_dclaw_pose_fixed": (
        "target_joint_positions_rad",
        "max_control_steps",
        "duration_s",
    ),
    "robel_dclaw_turn_fixed": (
        "initial_fixture_angle_rad",
        "target_fixture_angle_rad",
        "max_control_steps",
        "duration_s",
    ),
    "robel_dclaw_screw_fixed": (
        "target_fixture_velocity_rad_s",
        "max_control_steps",
        "duration_s",
    ),
    "myosuite_object_hold_fixed": ("target_object_position_m", "max_control_steps"),
    "myosuite_baoding_p1": ("orbit_period_s", "orbit_radii_m", "max_control_steps"),
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_leap_hand_public_task_library_snapshot() -> None:
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
    assert sources_document["schema_version"] == "1.0"
    assert sources_document["robot_configuration_id"] == "leap_hand"
    assert sources_document["package_version"] == "1.0.0"
    assert set(catalog_document) == {
        "schema_version",
        "robot_configuration_id",
        "package_version",
        "snapshot_id",
        "tasks",
    }
    assert catalog_document["schema_version"] == "1.0"
    assert catalog_document["robot_configuration_id"] == "leap_hand"
    assert catalog_document["package_version"] == "1.0.0"
    assert catalog_document["snapshot_id"] == "leap-hand-public-source-contracts-2026-08-20-v1"

    source_ids = tuple(source["source_id"] for source in validated_sources)
    assert source_ids == SOURCE_ORDER
    for source in validated_sources:
        expected = SOURCE_EXPECTATIONS[source["source_id"]]
        assert source["title"] == expected["title"]
        assert source["organization"] == expected["organization"]
        assert expected["version"] in source["version_or_date"]
        assert source["locator"] == expected["locator"]
        assert all(marker in source["specific_reference"] for marker in expected["markers"])

    task_ids = tuple(task["task_id"] for task in validated_tasks)
    assert task_ids == TASK_ORDER
    assert len(task_ids) == len(set(task_ids)) == 20
    assert sum(len(task["scoring"]) for task in validated_tasks) == 22
    assert set(EXPECTED_SCORING) == set(task_ids)
    assert len({task["name"] for task in validated_tasks}) == 20
    assert len({task["description"] for task in validated_tasks}) == 20
    assert len({task["source_task_or_operation"] for task in validated_tasks}) == 20

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
            assert len(clause["source_refs"]) == 1
            source_ref = clause["source_refs"][0]
            assert source_ref["source_id"] == expected["source_id"]
            assert source_ref["support"] == expected["support"]
            assert source_ref["specific_reference"].strip()
            if source_ref["support"] == "adapted":
                assert source_ref["adaptation"].strip()

        parameters = task["invocation_schema"]["request"]["task_parameters"]
        expected_parameter_order = EXPECTED_PARAMETER_ORDER[task["task_id"]]
        assert tuple(parameters["required"]) == expected_parameter_order
        assert tuple(parameters["properties"]) == expected_parameter_order
        assert parameters["additional_properties"] is False
        assert all(
            parameter["description"].strip()
            for parameter in parameters["properties"].values()
        )

    ec_tasks = validated_tasks[: len(EC_TASK_ORDER)]
    assert all(task["task_id"] in EC_TASK_ORDER for task in ec_tasks)
    assert all("one-to-one source coordination pattern" in task["adaptation"] for task in ec_tasks)
    assert all("exactly the first three attempts" in task["adaptation"] for task in ec_tasks)
    assert all(
        task["scoring"][0]["metric_definition"]["per_trial_success"]["kind"]
        == "all_required_events"
        for task in ec_tasks
    )
    event_contracts = {
        json.dumps(task["scoring"][0]["metric_definition"], sort_keys=True)
        for task in ec_tasks
    }
    assert len(event_contracts) == 13
    for task_id in ("ec_rotary_step", "ec_interdigital_step"):
        task = next(task for task in ec_tasks if task["task_id"] == task_id)
        rotation_event = task["scoring"][0]["metric_definition"]["per_trial_success"][
            "required_events"
        ][1]
        assert rotation_event["comparator"] == ">="
        assert rotation_event["threshold"] == 360
        assert rotation_event["unit"] == "deg"

    hold = next(task for task in validated_tasks if task["task_id"] == "myosuite_object_hold_fixed")
    assert hold["scoring"][0]["metric_definition"]["per_step_success"] == {
        "metric": "object_goal_euclidean_distance",
        "unit": "m",
        "comparator": "<",
        "threshold": 0.01,
    }
    assert hold["scoring"][1]["metric_definition"]["drop_event"]["threshold"] == 0.3

    baoding = next(task for task in validated_tasks if task["task_id"] == "myosuite_baoding_p1")
    predicates = baoding["scoring"][0]["metric_definition"]["per_step_success"]["predicates"]
    assert [predicate["threshold"] for predicate in predicates] == [0.015, 0.015, 1.25, 1.25]
    assert "counter-clockwise" in baoding["description"]

    assert morphology["robot_configuration_id"] == "leap_hand"
    assert morphology["package_version"] == "1.0.0"
    assert morphology["morphology_kind"] == "fixed_base_hand"
    assert morphology["base_type"] == "fixed"
    assert morphology["handedness"] == "right"
    assert morphology["degrees_of_freedom"] == {"hand": 16}
    assert morphology["model_dimensions"] == {"nq": 16, "nv": 16, "nu": 16}
    assert morphology["public_control"]["actuation"] == "joint_position"
    assert morphology["public_control"]["finger_order"] == [
        "index",
        "middle",
        "ring",
        "thumb",
    ]
    assert all("no arm" in task["applicability"] for task in validated_tasks)

    public_keys: set[str] = set()

    def collect_keys(value: object) -> None:
        if isinstance(value, dict):
            public_keys.update(value)
            for child in value.values():
                collect_keys(child)
        elif isinstance(value, list):
            for child in value:
                collect_keys(child)

    collect_keys(catalog_document)
    assert not public_keys & {
        "reset_state",
        "waypoints",
        "seeds",
        "symbol_bindings",
        "guards",
        "controller_recipe",
        "object_placements",
        "effect_catalog",
        "reference_driver",
    }

    public_text = json.dumps(catalog_document).lower()
    for excluded_variant in (
        "handmanipulateblockrotatez",
        "handmanipulateblockrotateparallel",
        "handmanipulateblockrotatexyz",
        "handmanipulateegg",
        "handmanipulatepen",
        "baoding_cw",
        "baoding_p2",
    ):
        assert excluded_variant not in public_text

    task_entries = sorted(entry.name for entry in (PACKAGE_ROOT / "tasks").iterdir())
    assert task_entries == ["catalog.json", "sources.json"]
    assert not (PACKAGE_ROOT / "tasks" / "private").exists()
    assert not (PACKAGE_ROOT / "skeleton").exists()
    assert not (PACKAGE_ROOT / "reference").exists()
