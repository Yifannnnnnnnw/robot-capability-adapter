from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from autoadapter2.libraries.robot_package import _validate_sources, _validate_tasks


ROOT = Path(__file__).resolve().parents[1]
XARM_TASKS_ROOT = ROOT / "libraries" / "robots" / "ufactory_xarm7" / "1.0.0" / "tasks"
FRANKA_TASKS_ROOT = ROOT / "libraries" / "robots" / "franka_panda" / "1.0.0" / "tasks"
XARM_PACKAGE_ROOT = ROOT / "libraries" / "robots" / "ufactory_xarm7" / "1.0.0"
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
SOURCE_REF_FIELDS = ("source_id", "specific_reference", "support")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_shape(schema: dict) -> dict:
    return {
        key: copy.deepcopy(schema[key])
        for key in ("type", "items", "length")
        if key in schema
    }


class UfactoryXarm7PublicTaskLibraryTests(unittest.TestCase):
    def test_snapshot_validates_and_has_exact_pinned_task_order(self) -> None:
        sources_path = XARM_TASKS_ROOT / "sources.json"
        catalog_path = XARM_TASKS_ROOT / "catalog.json"
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
        self.assertEqual(catalog_document["robot_configuration_id"], "ufactory_xarm7")
        self.assertEqual(catalog_document["package_version"], "1.0.0")
        self.assertEqual(
            catalog_document["snapshot_id"],
            "ufactory-xarm7-metaworld-source-protocols-2026-08-20-v1",
        )

    def test_sources_and_scoring_contracts_match_the_franka_snapshot(self) -> None:
        xarm_sources = _read_json(XARM_TASKS_ROOT / "sources.json")
        franka_sources = _read_json(FRANKA_TASKS_ROOT / "sources.json")
        expected_sources = copy.deepcopy(franka_sources)
        expected_sources["robot_configuration_id"] = "ufactory_xarm7"
        self.assertEqual(xarm_sources, expected_sources)

        xarm_catalog = _read_json(XARM_TASKS_ROOT / "catalog.json")
        franka_catalog = _read_json(FRANKA_TASKS_ROOT / "catalog.json")
        self.assertEqual(xarm_catalog["package_version"], franka_catalog["package_version"])
        self.assertNotEqual(xarm_catalog["snapshot_id"], franka_catalog["snapshot_id"])
        self.assertEqual(
            set(xarm_catalog) - {"robot_configuration_id", "snapshot_id"},
            set(franka_catalog) - {"robot_configuration_id", "snapshot_id"},
        )
        self.assertEqual(xarm_catalog["request_envelope"], franka_catalog["request_envelope"])
        self.assertEqual(len(xarm_catalog["tasks"]), 20)

        for expected_task, actual_task in zip(franka_catalog["tasks"], xarm_catalog["tasks"]):
            self.assertEqual(set(actual_task), set(expected_task))
            for field in ("task_id", "name", "source_task_or_operation"):
                self.assertEqual(actual_task[field], expected_task[field], field)
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
                    for field in SOURCE_REF_FIELDS:
                        self.assertEqual(actual_ref[field], expected_ref[field], field)
                    self.assertTrue(actual_ref.get("adaptation"))

    def test_task_and_invocation_shapes_match_the_reviewed_contract(self) -> None:
        xarm_catalog = _read_json(XARM_TASKS_ROOT / "catalog.json")
        franka_catalog = _read_json(FRANKA_TASKS_ROOT / "catalog.json")

        for expected_task, actual_task in zip(franka_catalog["tasks"], xarm_catalog["tasks"]):
            self.assertEqual(set(actual_task), set(expected_task))
            for field in ("scene_assumptions", "observation_assumptions"):
                self.assertEqual(len(actual_task[field]), len(expected_task[field]))
                self.assertTrue(
                    all(isinstance(value, str) and value for value in actual_task[field])
                )

            expected_invocation = expected_task["invocation_schema"]
            actual_invocation = actual_task["invocation_schema"]
            self.assertEqual(actual_invocation["envelope"], expected_invocation["envelope"])
            self.assertEqual(actual_invocation["required"], expected_invocation["required"])
            self.assertEqual(
                actual_invocation["request"]["type"],
                expected_invocation["request"]["type"],
            )
            self.assertEqual(
                actual_invocation["request"]["required"],
                expected_invocation["request"]["required"],
            )
            self.assertEqual(
                actual_invocation["request"]["task_id"],
                expected_invocation["request"]["task_id"],
            )

            expected_parameters = expected_invocation["request"]["task_parameters"]
            actual_parameters = actual_invocation["request"]["task_parameters"]
            self.assertEqual(actual_parameters["type"], expected_parameters["type"])
            self.assertEqual(actual_parameters["required"], expected_parameters["required"])
            self.assertEqual(
                actual_parameters["additional_properties"],
                expected_parameters["additional_properties"],
            )
            self.assertEqual(
                set(actual_parameters["properties"]),
                set(expected_parameters["properties"]),
            )
            for name, expected_property in expected_parameters["properties"].items():
                actual_property = actual_parameters["properties"][name]
                if name in {"grasp_gripper", "grasp_wrist_roll"}:
                    self.assertEqual(_schema_shape(actual_property), _schema_shape(expected_property))
                else:
                    self.assertEqual(actual_property, expected_property)

    def test_xarm7_control_contract_and_public_boundaries(self) -> None:
        xarm_catalog = _read_json(XARM_TASKS_ROOT / "catalog.json")
        public_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (XARM_TASKS_ROOT / "sources.json", XARM_TASKS_ROOT / "catalog.json")
        )
        public_text_lower = public_text.lower()
        self.assertIn("ufactory_xarm7", public_text_lower)
        self.assertTrue("UFACTORY xArm7" in public_text or "xArm7" in public_text)
        for forbidden in ("franka_panda", "franka panda", "actuator8"):
            self.assertNotIn(forbidden, public_text_lower)

        for task in xarm_catalog["tasks"]:
            applicability = task["applicability"]
            self.assertIn("UFACTORY xArm7", applicability)
            self.assertIn("fixed-base 7-DoF serial arm", applicability)
            self.assertIn("joint7 wrist roll", applicability)
            self.assertIn("real tendon-actuated two-finger gripper", applicability)
            self.assertIn("TCP site link_tcp", applicability)
            self.assertIn("contact pads", applicability)

            properties = task["invocation_schema"]["request"]["task_parameters"]["properties"]
            wrist_roll = properties.get("grasp_wrist_roll")
            if wrist_roll is not None:
                self.assertEqual(wrist_roll["unit"], "rad")
                self.assertEqual(wrist_roll["frame"], "joint7")
                self.assertIn("xArm7", wrist_roll["description"])
            gripper = properties.get("grasp_gripper")
            if gripper is not None:
                self.assertEqual(gripper["type"], "number")
                self.assertEqual(gripper["unit"], "native_mujoco_control")
                self.assertEqual(gripper["frame"], "gripper")
                description = gripper["description"].lower()
                self.assertIn("[0,255]", description)
                self.assertIn("0 is open", description)
                self.assertIn("255 is closed", description)
                self.assertNotIn("rad", description)
                self.assertNotIn("actuator8", description)

        runnable_index = _read_json(RUNNABLE_INDEX_PATH)
        self.assertNotIn("ufactory_xarm7", runnable_index["robots"])
        self.assertEqual(
            sorted(path.name for path in XARM_PACKAGE_ROOT.iterdir()),
            ["assets", "morphology.json", "tasks"],
        )
        self.assertEqual(
            sorted(path.name for path in XARM_TASKS_ROOT.iterdir()),
            ["catalog.json", "sources.json"],
        )
        self.assertFalse((XARM_TASKS_ROOT / "private").exists())
        self.assertFalse((XARM_PACKAGE_ROOT / "skeleton").exists())
        self.assertFalse((XARM_PACKAGE_ROOT / "reference").exists())


if __name__ == "__main__":
    unittest.main()
