from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from autoadapter2.libraries.robot_package import _validate_sources, _validate_tasks


ROOT = Path(__file__).resolve().parents[1]
FRANKA_TASKS_ROOT = ROOT / "libraries" / "robots" / "franka_panda" / "1.0.0" / "tasks"
SO101_TASKS_ROOT = ROOT / "libraries" / "robots" / "robotstudio_so101" / "1.0.0" / "tasks"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
RESEARCH_INDEX_PATH = ROOT / "research" / "robots" / "index.json"
PINNED_COMMIT = "7ea2b501c4a698c8533cdc55a396fe2734e2649d"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_shape(schema: dict) -> dict:
    return {
        key: copy.deepcopy(schema[key])
        for key in ("type", "items", "length")
        if key in schema
    }


class FrankaPublicTaskLibraryTests(unittest.TestCase):
    def test_snapshot_passes_canonical_public_validators_and_has_20_pinned_tasks(self) -> None:
        sources_path = FRANKA_TASKS_ROOT / "sources.json"
        catalog_path = FRANKA_TASKS_ROOT / "catalog.json"
        sources_document = _read_json(sources_path)
        catalog_document = _read_json(catalog_path)

        sources = _validate_sources(sources_document, path=sources_path)
        tasks = _validate_tasks(
            catalog_document,
            path=catalog_path,
            source_ids={str(source["source_id"]) for source in sources},
        )

        task_ids = [task["task_id"] for task in tasks]
        self.assertEqual(len(task_ids), 20)
        self.assertEqual(len(set(task_ids)), 20)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["source_id"], "metaworld_repo")
        self.assertIn(PINNED_COMMIT, sources[0]["locator"])
        self.assertIn(PINNED_COMMIT, sources[0]["version_or_date"])

    def test_franka_preserves_the_reviewed_so101_contracts(self) -> None:
        template_sources = _read_json(SO101_TASKS_ROOT / "sources.json")
        franka_sources = _read_json(FRANKA_TASKS_ROOT / "sources.json")
        template = _read_json(SO101_TASKS_ROOT / "catalog.json")
        franka = _read_json(FRANKA_TASKS_ROOT / "catalog.json")

        expected_sources = copy.deepcopy(template_sources)
        expected_sources["robot_configuration_id"] = "franka_panda"
        self.assertEqual(franka_sources, expected_sources)
        self.assertEqual(franka["robot_configuration_id"], "franka_panda")
        self.assertEqual(franka["package_version"], template["package_version"])
        self.assertNotEqual(franka["snapshot_id"], template["snapshot_id"])
        self.assertEqual(
            set(franka) - {"robot_configuration_id", "snapshot_id"},
            set(template) - {"robot_configuration_id", "snapshot_id"},
        )
        self.assertEqual(len(franka["tasks"]), len(template["tasks"]))

        for expected, actual in zip(template["tasks"], franka["tasks"]):
            self.assertEqual(set(actual), set(expected))
            self.assertEqual(actual["task_id"], expected["task_id"])
            self.assertEqual(actual["name"], expected["name"])
            self.assertEqual(
                actual["source_task_or_operation"],
                expected["source_task_or_operation"],
            )
            for field in ("scene_assumptions", "observation_assumptions"):
                self.assertEqual(len(actual[field]), len(expected[field]))
                self.assertTrue(all(isinstance(value, str) and value for value in actual[field]))

            self._assert_scoring_contract(expected["scoring"], actual["scoring"])
            self._assert_invocation_contract(
                expected["invocation_schema"],
                actual["invocation_schema"],
            )

    def _assert_scoring_contract(self, expected: list, actual: list) -> None:
        self.assertEqual(len(actual), len(expected))
        for expected_clause, actual_clause in zip(expected, actual):
            for field in (
                "clause_id",
                "metric",
                "unit",
                "comparator",
                "threshold",
                "temporal",
                "aggregation",
            ):
                self.assertEqual(actual_clause[field], expected_clause[field], field)
            self.assertEqual(len(actual_clause["source_refs"]), len(expected_clause["source_refs"]))
            for expected_ref, actual_ref in zip(
                expected_clause["source_refs"], actual_clause["source_refs"]
            ):
                for field in ("source_id", "specific_reference", "support"):
                    self.assertEqual(actual_ref[field], expected_ref[field], field)
                self.assertTrue(actual_ref.get("adaptation"))

    def _assert_invocation_contract(self, expected: dict, actual: dict) -> None:
        self.assertEqual(actual["envelope"], expected["envelope"])
        self.assertEqual(actual["required"], expected["required"])
        self.assertEqual(actual["request"]["type"], expected["request"]["type"])
        self.assertEqual(actual["request"]["required"], expected["request"]["required"])
        self.assertEqual(actual["request"]["task_id"], expected["request"]["task_id"])

        expected_parameters = expected["request"]["task_parameters"]
        actual_parameters = actual["request"]["task_parameters"]
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

        gripper = actual_parameters["properties"].get("grasp_gripper")
        if gripper is not None:
            self.assertEqual(gripper["unit"], "native_mujoco_control")
            self.assertIn("actuator8", gripper["frame"])
            self.assertNotIn("rad", gripper["unit"].lower())
            self.assertNotIn("radian", gripper["description"].lower())
            self.assertIn("[0,255]", gripper["description"])
            self.assertIn("0 is closed", gripper["description"])
            self.assertIn("255 is open", gripper["description"])

            wrist_roll = actual_parameters["properties"]["grasp_wrist_roll"]
            self.assertEqual(wrist_roll["unit"], "rad")
            self.assertEqual(wrist_roll["frame"], "joint7")

    def test_public_lineage_and_identity_boundaries(self) -> None:
        sources_document = _read_json(FRANKA_TASKS_ROOT / "sources.json")
        catalog_document = _read_json(FRANKA_TASKS_ROOT / "catalog.json")
        source_ids = {source["source_id"] for source in sources_document["sources"]}

        for task in catalog_document["tasks"]:
            self.assertIn("Franka Panda", task["applicability"])
            for clause in task["scoring"]:
                for source_ref in clause["source_refs"]:
                    self.assertIn(source_ref["source_id"], source_ids)

        public_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                FRANKA_TASKS_ROOT / "sources.json",
                FRANKA_TASKS_ROOT / "catalog.json",
            )
        ).lower()
        self.assertIn("franka_panda", public_text)
        self.assertIn("franka panda", public_text)
        for forbidden in ("so-101", "robotstudio_so101", "go2", "unitree"):
            self.assertNotIn(forbidden, public_text)

        runnable_index = _read_json(RUNNABLE_INDEX_PATH)
        self.assertNotIn("franka_panda", runnable_index["robots"])
        research_index = _read_json(RESEARCH_INDEX_PATH)
        candidate = next(
            item
            for item in research_index["candidates"]
            if item["robot_configuration_id"] == "franka_panda"
        )
        self.assertTrue(candidate["missing_for_runnable_package"])
        for phrase in (
            "task-specific MuJoCo scenes",
            "Framework-private tasks/private",
            "arm_serial_dls skeleton",
            "package check",
            "positive control",
            "dynamic canary",
        ):
            self.assertTrue(
                any(phrase in item for item in candidate["missing_for_runnable_package"]),
                phrase,
            )
        self.assertFalse(
            any("20 distinct applicable source-backed tasks" in item for item in candidate["missing_for_runnable_package"])
        )
        self.assertFalse(
            any("Create tasks/sources.json" in item for item in candidate["missing_for_runnable_package"])
        )


if __name__ == "__main__":
    unittest.main()
