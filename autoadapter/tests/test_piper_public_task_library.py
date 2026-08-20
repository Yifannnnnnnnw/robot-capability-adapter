from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from autoadapter2.libraries.robot_package import _validate_sources, _validate_tasks


ROOT = Path(__file__).resolve().parents[1]
PIPER_TASKS_ROOT = ROOT / "libraries" / "robots" / "piper" / "1.0.0" / "tasks"
FRANKA_TASKS_ROOT = ROOT / "libraries" / "robots" / "franka_panda" / "1.0.0" / "tasks"
PIPER_PACKAGE_ROOT = ROOT / "libraries" / "robots" / "piper" / "1.0.0"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
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
SOURCE_LINEAGE_FIELDS = ("source_id", "specific_reference", "support")
NON_ROBOT_INVOCATION_FIELDS = {"grasp_wrist_roll", "grasp_gripper"}
PIPER_APPLICABILITY_FACTS = (
    "fixed-base 6-DoF serial arm",
    "joint6 as the wrist roll",
    "directly actuated slide gripper actuator named gripper controlling joint7",
    "passive mirrored joint8 following joint8 = -joint7",
    "end-effector/TCP site ee_site",
    "four contact-enabled box geoms on link7/link8",
    "native slide-position gripper control in metres [0.0, 0.035] where 0.0 m is closed and 0.035 m is open",
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class PiperPublicTaskLibraryTests(unittest.TestCase):
    def test_snapshot_validates_and_has_exact_pinned_task_order(self) -> None:
        sources_path = PIPER_TASKS_ROOT / "sources.json"
        catalog_path = PIPER_TASKS_ROOT / "catalog.json"
        sources_document = _read_json(sources_path)
        catalog_document = _read_json(catalog_path)

        sources = _validate_sources(sources_document, path=sources_path)
        tasks = _validate_tasks(
            catalog_document,
            path=catalog_path,
            source_ids={str(source["source_id"]) for source in sources},
        )

        task_ids = [task["task_id"] for task in tasks]
        self.assertEqual(task_ids, EXPECTED_TASK_IDS)
        self.assertEqual(len(task_ids), 20)
        self.assertEqual(len(set(task_ids)), 20)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["source_id"], "metaworld_repo")
        self.assertIn(PINNED_COMMIT, sources[0]["locator"])
        self.assertIn(PINNED_COMMIT, sources[0]["version_or_date"])
        self.assertEqual(catalog_document["robot_configuration_id"], "piper")
        self.assertEqual(catalog_document["package_version"], "1.0.0")
        self.assertEqual(
            catalog_document["snapshot_id"],
            "piper-metaworld-source-protocols-2026-08-20-v1",
        )

    def test_sources_scoring_and_lineage_preserve_the_franka_contract(self) -> None:
        piper_sources = _read_json(PIPER_TASKS_ROOT / "sources.json")
        franka_sources = _read_json(FRANKA_TASKS_ROOT / "sources.json")
        expected_sources = copy.deepcopy(franka_sources)
        expected_sources["robot_configuration_id"] = "piper"
        self.assertEqual(piper_sources, expected_sources)

        piper_catalog = _read_json(PIPER_TASKS_ROOT / "catalog.json")
        franka_catalog = _read_json(FRANKA_TASKS_ROOT / "catalog.json")
        self.assertEqual(piper_catalog["package_version"], franka_catalog["package_version"])
        self.assertNotEqual(piper_catalog["snapshot_id"], franka_catalog["snapshot_id"])
        self.assertEqual(piper_catalog["request_envelope"], franka_catalog["request_envelope"])

        for expected_task, actual_task in zip(franka_catalog["tasks"], piper_catalog["tasks"]):
            self.assertEqual(actual_task["task_id"], expected_task["task_id"])
            self.assertEqual(actual_task["name"], expected_task["name"])
            self.assertEqual(
                actual_task["source_task_or_operation"],
                expected_task["source_task_or_operation"],
            )
            self.assertEqual(len(actual_task["scoring"]), len(expected_task["scoring"]))

            for expected_clause, actual_clause in zip(
                expected_task["scoring"], actual_task["scoring"]
            ):
                for field in SCORING_FIELDS:
                    self.assertEqual(actual_clause[field], expected_clause[field], field)
                self.assertEqual(
                    len(actual_clause["source_refs"]),
                    len(expected_clause["source_refs"]),
                )
                for expected_ref, actual_ref in zip(
                    expected_clause["source_refs"], actual_clause["source_refs"]
                ):
                    self.assertEqual(
                        set(actual_ref) - {"adaptation"},
                        set(expected_ref) - {"adaptation"},
                    )
                    for field in SOURCE_LINEAGE_FIELDS:
                        self.assertEqual(actual_ref[field], expected_ref[field], field)
                    self.assertTrue(actual_ref.get("adaptation"))
                    self.assertIn("Piper", actual_ref["adaptation"])

    def test_task_and_invocation_shapes_preserve_non_robot_specific_fields(self) -> None:
        piper_catalog = _read_json(PIPER_TASKS_ROOT / "catalog.json")
        franka_catalog = _read_json(FRANKA_TASKS_ROOT / "catalog.json")

        self.assertEqual(
            [task["task_id"] for task in piper_catalog["tasks"]],
            [task["task_id"] for task in franka_catalog["tasks"]],
        )
        for expected_task, actual_task in zip(franka_catalog["tasks"], piper_catalog["tasks"]):
            self.assertEqual(
                actual_task["invocation_schema"]["envelope"],
                expected_task["invocation_schema"]["envelope"],
            )
            self.assertEqual(
                actual_task["invocation_schema"]["required"],
                expected_task["invocation_schema"]["required"],
            )

            expected_request = expected_task["invocation_schema"]["request"]
            actual_request = actual_task["invocation_schema"]["request"]
            for field in ("type", "required", "task_id"):
                self.assertEqual(actual_request[field], expected_request[field], field)

            expected_parameters = expected_request["task_parameters"]
            actual_parameters = actual_request["task_parameters"]
            for field in ("type", "required", "additional_properties"):
                self.assertEqual(actual_parameters[field], expected_parameters[field], field)
            self.assertEqual(
                set(actual_parameters["properties"]),
                set(expected_parameters["properties"]),
            )
            for name, expected_property in expected_parameters["properties"].items():
                if name in NON_ROBOT_INVOCATION_FIELDS:
                    continue
                self.assertEqual(actual_parameters["properties"][name], expected_property, name)

    def test_piper_control_contract_and_public_boundaries(self) -> None:
        piper_catalog = _read_json(PIPER_TASKS_ROOT / "catalog.json")
        public_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (PIPER_TASKS_ROOT / "sources.json", PIPER_TASKS_ROOT / "catalog.json")
        )
        public_text_lower = public_text.lower()
        self.assertIn("piper", public_text_lower)
        for forbidden in (
            "franka_panda",
            "franka panda",
            "ufactory",
            "xarm7",
            "actuator8",
            "joint7 wrist roll",
            "tendon",
        ):
            self.assertNotIn(forbidden, public_text_lower)

        for task in piper_catalog["tasks"]:
            applicability = task["applicability"]
            for fact in PIPER_APPLICABILITY_FACTS:
                self.assertIn(fact, applicability)

            properties = task["invocation_schema"]["request"]["task_parameters"]["properties"]
            wrist_roll = properties.get("grasp_wrist_roll")
            if wrist_roll is not None:
                self.assertEqual(wrist_roll["type"], "number")
                self.assertEqual(wrist_roll["unit"], "rad")
                self.assertEqual(wrist_roll["frame"], "joint6")
                self.assertIn("Piper joint6 wrist-roll", wrist_roll["description"])

            gripper = properties.get("grasp_gripper")
            if gripper is not None:
                self.assertEqual(gripper["type"], "number")
                self.assertEqual(gripper["unit"], "m")
                self.assertEqual(gripper["frame"], "gripper")
                description = gripper["description"].lower()
                self.assertIn("finite native mujoco slide-position control", description)
                self.assertIn("[0,0.035]", description)
                self.assertIn("0 m is closed", description)
                self.assertIn("0.035 m is open", description)
                self.assertNotIn("rad", description)

        runnable_index = _read_json(RUNNABLE_INDEX_PATH)
        self.assertNotIn("piper", runnable_index["robots"])
        self.assertTrue((PIPER_TASKS_ROOT / "catalog.json").is_file())
        self.assertTrue((PIPER_TASKS_ROOT / "sources.json").is_file())
        self.assertTrue((PIPER_TASKS_ROOT / "private").is_dir())
        self.assertFalse((PIPER_PACKAGE_ROOT / "skeleton").exists())
        self.assertFalse((PIPER_PACKAGE_ROOT / "reference").exists())


if __name__ == "__main__":
    unittest.main()
