from __future__ import annotations

import copy
import json
from pathlib import Path

from autoadapter2.libraries.robot_package import _validate_sources, _validate_tasks


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "universal_robots_ur5e_robotiq_2f85" / "1.0.0"
TASKS_ROOT = PACKAGE_ROOT / "tasks"
DONOR_TASKS_ROOT = ROOT / "libraries" / "robots" / "kinova_gen3_robotiq_2f85" / "1.0.0" / "tasks"
ROBOT_CONFIGURATION_ID = "universal_robots_ur5e_robotiq_2f85"
SNAPSHOT_ID = "universal-robots-ur5e-robotiq-2f85-metaworld-source-protocols-2026-08-20-v1"
PINNED_COMMIT = "7ea2b501c4a698c8533cdc55a396fe2734e2649d"
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
SCORING_FIELDS = (
    "clause_id",
    "metric",
    "unit",
    "comparator",
    "threshold",
    "temporal",
    "aggregation",
)
SOURCE_REF_FIELDS = ("source_id", "specific_reference", "support")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _scoring_fingerprint(task: dict) -> dict:
    clauses = []
    for clause in task["scoring"]:
        clauses.append(
            {
                **{field: copy.deepcopy(clause[field]) for field in SCORING_FIELDS},
                "source_refs": [
                    {field: copy.deepcopy(ref[field]) for field in SOURCE_REF_FIELDS}
                    for ref in clause["source_refs"]
                ],
            }
        )
    return {
        "task_id": task["task_id"],
        "name": task["name"],
        "source_task_or_operation": task["source_task_or_operation"],
        "scoring": clauses,
    }


def _validated_snapshot() -> tuple[dict, dict, tuple[dict, ...]]:
    sources_path = TASKS_ROOT / "sources.json"
    catalog_path = TASKS_ROOT / "catalog.json"
    sources_document = _read_json(sources_path)
    catalog_document = _read_json(catalog_path)
    sources = _validate_sources(sources_document, path=sources_path)
    tasks = _validate_tasks(
        catalog_document,
        path=catalog_path,
        source_ids={str(source["source_id"]) for source in sources},
    )
    return sources_document, catalog_document, tasks


def test_universal_robots_ur5e_snapshot_has_exact_twenty_tasks_and_closed_sources() -> None:
    sources_document, catalog_document, tasks = _validated_snapshot()
    source_ids = {source["source_id"] for source in sources_document["sources"]}
    referenced_source_ids = {
        ref["source_id"]
        for task in tasks
        for clause in task["scoring"]
        for ref in clause["source_refs"]
    }

    assert [task["task_id"] for task in tasks] == EXPECTED_TASK_IDS
    assert len(tasks) == 20
    assert len({task["task_id"] for task in tasks}) == 20
    assert source_ids == {"metaworld_repo"}
    assert referenced_source_ids == source_ids
    assert catalog_document["robot_configuration_id"] == ROBOT_CONFIGURATION_ID
    assert catalog_document["package_version"] == "1.0.0"
    assert catalog_document["snapshot_id"] == SNAPSHOT_ID
    assert sources_document["robot_configuration_id"] == ROBOT_CONFIGURATION_ID
    assert sources_document["sources"][0]["source_id"] == "metaworld_repo"
    assert PINNED_COMMIT in sources_document["sources"][0]["locator"]
    assert PINNED_COMMIT in sources_document["sources"][0]["version_or_date"]


def test_universal_robots_ur5e_snapshot_preserves_donor_scoring_and_invocation_contracts() -> None:
    _, catalog_document, tasks = _validated_snapshot()
    donor_catalog = _read_json(DONOR_TASKS_ROOT / "catalog.json")
    donor_sources = _read_json(DONOR_TASKS_ROOT / "sources.json")
    expected_sources = copy.deepcopy(donor_sources)
    expected_sources["robot_configuration_id"] = ROBOT_CONFIGURATION_ID

    assert _read_json(TASKS_ROOT / "sources.json") == expected_sources
    assert catalog_document["request_envelope"] == donor_catalog["request_envelope"]

    for donor_task, task in zip(donor_catalog["tasks"], tasks):
        assert set(task) == set(donor_task)
        assert _scoring_fingerprint(task) == _scoring_fingerprint(donor_task)
        assert task["scene_assumptions"]
        assert task["observation_assumptions"]
        assert len(task["scene_assumptions"]) == len(donor_task["scene_assumptions"])
        assert len(task["observation_assumptions"]) == len(donor_task["observation_assumptions"])

        expected_invocation = donor_task["invocation_schema"]
        actual_invocation = task["invocation_schema"]
        assert actual_invocation["envelope"] == "request"
        assert actual_invocation["required"] == ["request"]
        assert actual_invocation["envelope"] == expected_invocation["envelope"]
        assert actual_invocation["required"] == expected_invocation["required"]

        expected_request = expected_invocation["request"]
        actual_request = actual_invocation["request"]
        assert actual_request["type"] == expected_request["type"] == "object"
        assert actual_request["required"] == expected_request["required"] == [
            "task_id",
            "task_parameters",
        ]
        assert actual_request["task_id"] == task["task_id"]

        expected_parameters = expected_request["task_parameters"]
        actual_parameters = actual_request["task_parameters"]
        assert actual_parameters["type"] == expected_parameters["type"] == "object"
        assert actual_parameters["required"] == expected_parameters["required"]
        assert actual_parameters["additional_properties"] is False
        assert actual_parameters["additional_properties"] == expected_parameters[
            "additional_properties"
        ]
        assert set(actual_parameters["properties"]) == set(expected_parameters["properties"])

        for name, expected_property in expected_parameters["properties"].items():
            actual_property = actual_parameters["properties"][name]
            if name not in {"grasp_wrist_roll", "grasp_gripper"}:
                assert actual_property == expected_property
                continue
            comparable_expected = {
                key: value
                for key, value in expected_property.items()
                if key != "description" and not (name == "grasp_wrist_roll" and key == "frame")
            }
            comparable_actual = {
                key: value
                for key, value in actual_property.items()
                if key != "description" and not (name == "grasp_wrist_roll" and key == "frame")
            }
            assert comparable_actual == comparable_expected


def test_universal_robots_ur5e_public_identity_has_no_donor_remnants_and_is_non_runtime() -> None:
    sources_document, catalog_document, tasks = _validated_snapshot()
    public_text = json.dumps(
        {"sources": sources_document, "catalog": catalog_document},
        sort_keys=True,
    )
    public_text_lower = public_text.lower()

    for required in (
        ROBOT_CONFIGURATION_ID,
        "Universal Robots UR5e",
        "Robotiq 2F-85",
        "fixed-base six-joint serial arm",
        "wrist_3_joint",
        "pinch_site",
        "left_pad1",
        "left_pad2",
        "right_pad1",
        "right_pad2",
        "tendon/equality-coupled",
        "[0,255]",
        "0 is open",
        "255 is closed",
    ):
        assert required in public_text

    for task in tasks:
        applicability = task["applicability"]
        assert "Universal Robots UR5e" in applicability
        assert "fixed-base six-joint serial arm" in applicability
        assert "wrist_3_joint wrist roll" in applicability
        assert "Robotiq 2F-85 tendon/equality-coupled two-finger gripper" in applicability
        assert "TCP/end-effector site pinch_site" in applicability
        assert "left_pad1, left_pad2, right_pad1, and right_pad2" in applicability

        properties = task["invocation_schema"]["request"]["task_parameters"]["properties"]
        wrist_roll = properties.get("grasp_wrist_roll")
        if wrist_roll is not None:
            assert wrist_roll["unit"] == "rad"
            assert wrist_roll["frame"] == "wrist_3_joint"
            assert "Universal Robots UR5e" in wrist_roll["description"]
        gripper = properties.get("grasp_gripper")
        if gripper is not None:
            assert gripper["type"] == "number"
            assert gripper["unit"] == "native_mujoco_control"
            assert gripper["frame"] == "gripper"
            assert "Universal Robots UR5e" in gripper["description"]
            assert "Robotiq 2F-85" in gripper["description"]
            assert "tendon/equality-coupled" in gripper["description"]
            assert "[0,255]" in gripper["description"]
            assert "0 is open" in gripper["description"]
            assert "255 is closed" in gripper["description"]

    for forbidden in ("ufactory", "xarm", "link_tcp", "joint7", "actuator8"):
        assert forbidden not in public_text_lower

    assert sorted(path.name for path in PACKAGE_ROOT.iterdir()) == [
        "assets",
        "morphology.json",
        "reference",
        "skeleton",
        "tasks",
    ]
    assert sorted(path.name for path in TASKS_ROOT.iterdir()) == [
        "catalog.json",
        "private",
        "sources.json",
    ]
    assert sorted(
        path.name for path in (PACKAGE_ROOT / "reference").iterdir() if path.is_file()
    ) == [
        "driver.py",
        "rendering.py",
    ]
    assert (PACKAGE_ROOT / "skeleton" / "arm_serial_dls.py").is_file()
    assert sorted(path.name for path in (TASKS_ROOT / "private").iterdir()) == [
        "bindings.json",
        "guards.json",
        "instances.json",
    ]
