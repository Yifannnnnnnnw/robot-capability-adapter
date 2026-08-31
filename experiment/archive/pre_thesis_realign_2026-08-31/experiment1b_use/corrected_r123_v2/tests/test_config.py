from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import mujoco


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
PROFILE_ROOT = REPOSITORY_ROOT / "experiment/experiment1b_use/corrected_r123_v2"
MANIFEST_PATH = PROFILE_ROOT / "config/manifest.json"
SUITE_PATH = PROFILE_ROOT / "config/task_suite.json"
SOURCE_SUITE_PATH = (
    REPOSITORY_ROOT / "experiment/experiment1b_use/corrected_r1/config/task_suite.json"
)
IDENTITY = {
    "document_id": "AA2-B2-CORRECTED-R123-V2",
    "revision": "1.0.0",
}
MODELS = {"M1", "M2", "M3", "M4", "M5", "M6", "M8"}
TASK_ORDER = (
    "mw_push_to_goal",
    "mw_sweep_into_goal",
    "mw_pick_place",
    "GO2-T02",
    "GO2-T03",
    "GO2-T06",
    "GO2-T16",
    "GO2-T17",
    "mw_pick_place_wall",
    "mw_dial_turn",
)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _tasks(suite: dict) -> dict[str, dict]:
    return {
        task["task_id"]: task
        for robot in suite["robot_suites"]
        for task in robot["tasks"]
    }


def test_generated_config_is_current_and_declares_the_154_plus_56_plan() -> None:
    from experiment.experiment1b_use.corrected_r123_v2.config.build_config import (
        DISPATCH_ORDER,
        build,
    )

    build(check=True)
    manifest = _read(MANIFEST_PATH)
    assert manifest["artifact_type"] == "b2_corrected_r123_v2_manifest"
    assert manifest["audit_identity"] == IDENTITY
    assert manifest["formal_episode"] is False
    assert manifest["formal_denominator_entry"] is False
    assert manifest["execution_units"] == 154
    assert manifest["fresh_execution_units"] == 140
    assert manifest["replacement_units"] == 14
    assert manifest["retained_selected_units"] == 56
    assert manifest["selected_combined_plan"] == 210
    assert manifest["superseded_r1_units"] == 14
    assert manifest["dispatch_order"] == list(DISPATCH_ORDER)
    assert len(manifest["dispatch_order"]) == 22

    units = manifest["fresh_units"]
    assert len(units) == len({unit["unit_id"] for unit in units}) == 154
    assert Counter(unit["execution_origin"] for unit in units) == {
        "replacement_new_definition": 14,
        "fresh_corrected": 140,
    }
    assert Counter(unit["replicate_id"] for unit in units) == {
        "R1": 14,
        "R2": 70,
        "R3": 70,
    }
    assert {
        (unit["task_id"], unit["model_id"])
        for unit in units
        if unit["replicate_id"] == "R1"
    } == {
        (task_id, model_id)
        for task_id in ("GO2-T17", "mw_dial_turn")
        for model_id in MODELS
    }
    assert manifest["positive_control_gate"] == {
        "control_replicate_id": "R2",
        "covered_replicates": ["R1", "R2", "R3"],
        "required_task_ids": list(TASK_ORDER),
        "required_pass_count": 10,
        "user_reacceptance_required_before_paid_dispatch": True,
    }


def test_suite_pins_new_packages_and_only_t17_dial_change_definition() -> None:
    suite = _read(SUITE_PATH)
    source = _read(SOURCE_SUITE_PATH)
    assert suite["audit_identity"] == IDENTITY
    assert suite["replicate_plan"] == {"replicate_ids": ["R1", "R2", "R3"]}
    assert suite["definition_lineage"]["replacement_new_definition_task_ids"] == [
        "GO2-T17",
        "mw_dial_turn",
    ]
    packages = {
        robot["robot_configuration_id"]: robot["package_version"]
        for robot in suite["robot_suites"]
    }
    assert packages == {
        "robotstudio_so101": "1.0.3",
        "unitree-go2-stock-12dof": "1.0.2",
    }

    tasks = _tasks(suite)
    old_tasks = _tasks(source)
    for task_id, task in tasks.items():
        lineage = task["protocol_lineage"]
        assert [item["replicate_id"] for item in task["replicate_inputs"]] == [
            "R1",
            "R2",
            "R3",
        ]
        if task_id in {"GO2-T17", "mw_dial_turn"}:
            assert lineage["corrected_r123_v2_definition_status"] == (
                "replacement_new_definition"
            )
            assert lineage["semantic_equivalent_to_corrected_r1"] is False
        else:
            assert lineage["corrected_r123_v2_definition_status"] == (
                "semantic_equivalent_to_corrected_r1"
            )
            assert lineage["semantic_equivalent_to_corrected_r1"] is True
            for field in (
                "public_projection",
                "private_scoring_clauses",
                "private_clause_bindings",
                "private_measurement_bindings",
                "private_guards",
                "episode_budget",
                "rendering",
            ):
                assert task[field] == old_tasks[task_id][field]

    t17_parameters = tasks["GO2-T17"]["public_projection"]["request"][
        "task_parameters"
    ]
    assert t17_parameters == {
        "duration_s": 20.0,
        "path_waypoints_m": [
            [0.55, 0.25],
            [1.35, -0.25],
            [2.15, 0.25],
            [2.95, -0.25],
            [3.75, 0.25],
        ],
    }
    t17_clauses = tasks["GO2-T17"]["private_scoring_clauses"]
    assert {clause["clause_id"] for clause in t17_clauses} == {
        "weave_order",
        "pole_clearance",
    }
    assert "yaw" not in json.dumps(t17_clauses).lower()

    dial_new = tasks["mw_dial_turn"]["public_projection"]["request"][
        "task_parameters"
    ]
    dial_old = old_tasks["mw_dial_turn"]["public_projection"]["request"][
        "task_parameters"
    ]
    for field in (
        "contact_position",
        "route_position",
        "target_position",
        "tool_target_position",
    ):
        assert dial_new[field][0] == round(dial_old[field][0] - 0.05, 3)
        assert dial_new[field][1:] == dial_old[field][1:]
    dial_clause = tasks["mw_dial_turn"]["private_scoring_clauses"][0]
    assert (dial_clause["metric"], dial_clause["comparator"], dial_clause["threshold"]) == (
        "dial_target_distance",
        "<=",
        0.07,
    )


def test_reference_designs_preserve_abi_and_pin_new_versions() -> None:
    selection = _read(PROFILE_ROOT / "config/reference/selection.json")
    assert selection["artifact_type"] == (
        "b2_corrected_r123_v2_reference_selection"
    )
    assert selection["audit_identity"] == IDENTITY
    for robot in selection["robots"]:
        robot_id = robot["robot_configuration_id"]
        expected_version = "1.0.3" if robot_id == "robotstudio_so101" else "1.0.2"
        assert robot["package_version"] == expected_version
        assert robot["package_root"].endswith(f"/{robot_id}/{expected_version}")
        new_design = _read(REPOSITORY_ROOT / robot["capability_design_path"])
        old_design = _read(
            REPOSITORY_ROOT
            / "experiment/experiment1b_use/corrected_r1/config/reference/resolved"
            / robot_id
            / "capability_design.json"
        )
        assert new_design["package_version"] == expected_version
        assert new_design["invocation_abi"] == old_design["invocation_abi"]
        assert new_design["capabilities"] == old_design["capabilities"]


def test_t17_has_five_full_collision_poles_and_supersedes_old_terminals() -> None:
    scene = ET.parse(
        REPOSITORY_ROOT
        / "autoadapter/libraries/robots/unitree-go2-stock-12dof/1.0.2/assets/barkour_weave.xml"
    ).getroot()
    poles = [
        geom
        for geom in scene.findall(".//geom")
        if str(geom.get("name", "")).startswith("weave_pole_")
    ]
    assert len(poles) == 5
    x_positions = [float(geom.attrib["pos"].split()[0]) for geom in poles]
    assert x_positions == [0.55, 1.35, 2.15, 2.95, 3.75]
    assert [round(b - a, 10) for a, b in zip(x_positions, x_positions[1:])] == [
        0.8,
        0.8,
        0.8,
        0.8,
    ]
    for pole in poles:
        assert pole.get("type") == "cylinder"
        assert pole.get("size") == "0.025 0.50"
        assert pole.get("group") == "0"
        assert pole.get("contype") == pole.get("conaffinity") == "1"

    manifest = _read(MANIFEST_PATH)
    replacements = [
        unit
        for unit in manifest["fresh_units"]
        if unit["replicate_id"] == "R1"
    ]
    assert len(replacements) == 14
    for unit in replacements:
        path = REPOSITORY_ROOT / unit["superseded_terminal_path"]
        terminal = _read(path)
        assert terminal["artifact_type"] == "b2_corrected_r1_unit_terminal"
        assert terminal["audit_identity"] == {
            "document_id": "AA2-B2-CORRECTED-R1",
            "revision": "1.0.0",
        }
        assert terminal["unit_id"] == unit["superseded_unit_id"]
        assert terminal["unit_id"] != unit["unit_id"]


def test_old_t17_through_pole_pose_now_contacts_and_fails_the_5mm_policy() -> None:
    from experiment.experiment1b_use.corrected_r1.runtime.contact_policy import (
        build_geom_metadata,
        evaluate_contact_integrity,
    )

    scene = (
        REPOSITORY_ROOT
        / "autoadapter/libraries/robots/unitree-go2-stock-12dof/1.0.2"
        / "assets/barkour_weave.xml"
    )
    model = mujoco.MjModel.from_xml_path(str(scene))
    data = mujoco.MjData(model)
    keyframe_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_KEY, "task_start"
    )
    mujoco.mj_resetDataKeyframe(model, data, keyframe_id)
    data.qpos[:3] = [0.55, 0.0, 0.35]
    mujoco.mj_forward(model, data)

    def geom_name(geom_id: int) -> str:
        return (
            mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_id)
            or f"geom_{geom_id}"
        )

    pole_contacts = [
        {
            "geom1": geom_name(int(contact.geom1)),
            "geom2": geom_name(int(contact.geom2)),
            "minimum_distance_m": float(contact.dist),
        }
        for contact in data.contact
        if any(
            name.startswith("weave_pole_")
            for name in (
                geom_name(int(contact.geom1)),
                geom_name(int(contact.geom2)),
            )
        )
    ]
    assert pole_contacts
    deepest = min(item["minimum_distance_m"] for item in pole_contacts)
    assert deepest < -0.005

    result = evaluate_contact_integrity(
        {
            "contact_monitoring_complete": True,
            "minimum_contact_distance_m": deepest,
            "contact_pair_min_distances": pole_contacts,
        },
        robot_family="unitree-go2-stock-12dof",
        task_id="GO2-T17",
        geom_metadata=build_geom_metadata(model=model, mujoco_module=mujoco),
        semantic_roles={},
        expected_pairs=set(),
    )
    assert result["passed"] is False
    assert result["deepest_contact_pair"]["applied_threshold_m"] == 0.005
