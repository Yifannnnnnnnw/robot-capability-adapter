from __future__ import annotations

import json
import unittest
from pathlib import Path

from autoadapter2.libraries.robot_package import _validate_sources, _validate_tasks


ROOT = Path(__file__).resolve().parents[1]
TASKS_ROOT = ROOT / "libraries" / "robots" / "aloha_2" / "1.0.0" / "tasks"
FRANKA_TASKS_ROOT = ROOT / "libraries" / "robots" / "franka_panda" / "1.0.0" / "tasks"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"

PINNED_METAWORLD_COMMIT = "7ea2b501c4a698c8533cdc55a396fe2734e2649d"
PINNED_ALOHA_COMMIT = "da76818e269b82289eba39808e2fb91d679d6994"
SNAPSHOT_ID = "aloha-2-metaworld-source-protocols-2026-08-20-v1"
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


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _renamed(name: str) -> str:
    return "grasp_wrist_rotate" if name == "grasp_wrist_roll" else name


class Aloha2PublicTaskLibraryTests(unittest.TestCase):
    def _load(self) -> tuple[dict, dict, tuple[dict, ...], tuple[dict, ...]]:
        sources_path = TASKS_ROOT / "sources.json"
        catalog_path = TASKS_ROOT / "catalog.json"
        sources_document = _read_json(sources_path)
        catalog_document = _read_json(catalog_path)
        franka_catalog = _read_json(FRANKA_TASKS_ROOT / "catalog.json")
        sources = _validate_sources(sources_document, path=sources_path)
        tasks = _validate_tasks(
            catalog_document,
            path=catalog_path,
            source_ids={str(source["source_id"]) for source in sources},
        )
        return sources_document, catalog_document, tasks, tuple(franka_catalog["tasks"])

    def test_validators_identity_sources_and_task_order(self) -> None:
        sources_document, catalog, tasks, franka_tasks = self._load()

        self.assertEqual(sources_document["robot_configuration_id"], "aloha_2")
        self.assertEqual(sources_document["package_version"], "1.0.0")
        self.assertEqual(catalog["robot_configuration_id"], "aloha_2")
        self.assertEqual(catalog["package_version"], "1.0.0")
        self.assertEqual(catalog["snapshot_id"], SNAPSHOT_ID)
        self.assertEqual(
            catalog["request_envelope"],
            _read_json(FRANKA_TASKS_ROOT / "catalog.json")["request_envelope"],
        )

        self.assertEqual(
            [source["source_id"] for source in sources_document["sources"]],
            ["metaworld_repo", "mujoco_menagerie_aloha"],
        )
        self.assertEqual(sources_document["sources"][0], _read_json(
            FRANKA_TASKS_ROOT / "sources.json"
        )["sources"][0])
        self.assertIn(PINNED_METAWORLD_COMMIT, sources_document["sources"][0]["locator"])
        self.assertIn(
            PINNED_METAWORLD_COMMIT,
            sources_document["sources"][0]["version_or_date"],
        )
        aloha_source = sources_document["sources"][1]
        self.assertEqual(aloha_source["organization"], "Google DeepMind")
        self.assertIn(PINNED_ALOHA_COMMIT, aloha_source["version_or_date"])
        self.assertEqual(
            aloha_source["locator"],
            f"https://github.com/google-deepmind/mujoco_menagerie/tree/{PINNED_ALOHA_COMMIT}/aloha",
        )
        for filename in (
            "aloha.xml",
            "joint_position_actuators.xml",
            "keyframe_ctrl.xml",
            "scene.xml",
        ):
            self.assertIn(filename, aloha_source["specific_reference"])
        self.assertIn("configuration applicability only", aloha_source["specific_reference"])
        self.assertIn("not support scoring thresholds", aloha_source["specific_reference"])

        task_ids = [task["task_id"] for task in tasks]
        self.assertEqual(task_ids, EXPECTED_TASK_IDS)
        self.assertEqual(len(task_ids), 20)
        self.assertEqual(len(set(task_ids)), 20)
        self.assertEqual(sum(len(task["scoring"]) for task in tasks), 20)
        self.assertEqual(len(franka_tasks), 20)
        self.assertFalse(any(task_id.endswith(("_left", "_right")) for task_id in task_ids))
        self.assertFalse(any("legacy" in task_id or "proxy" in task_id for task_id in task_ids))

    def test_scoring_contracts_and_source_references_are_preserved(self) -> None:
        _, _, tasks, franka_tasks = self._load()

        for actual_task, expected_task in zip(tasks, franka_tasks):
            self.assertEqual(actual_task["task_id"], expected_task["task_id"])
            self.assertEqual(
                actual_task["source_task_or_operation"],
                expected_task["source_task_or_operation"],
            )
            self.assertEqual(len(actual_task["scoring"]), len(expected_task["scoring"]))

            for actual_clause, expected_clause in zip(
                actual_task["scoring"], expected_task["scoring"]
            ):
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
                self.assertEqual(
                    len(actual_clause["source_refs"]),
                    len(expected_clause["source_refs"]),
                )
                for actual_ref, expected_ref in zip(
                    actual_clause["source_refs"], expected_clause["source_refs"]
                ):
                    self.assertEqual(actual_ref["source_id"], "metaworld_repo")
                    self.assertEqual(
                        actual_ref["specific_reference"],
                        expected_ref["specific_reference"],
                    )
                    self.assertEqual(actual_ref["support"], "adapted")
                    self.assertIn("ALOHA 2", actual_ref["adaptation"])
                    self.assertIn("source numerical obligation is unchanged", actual_ref["adaptation"])

    def test_invocation_schema_is_selected_arm_specific(self) -> None:
        _, _, tasks, franka_tasks = self._load()

        for actual_task, expected_task in zip(tasks, franka_tasks):
            actual_invocation = actual_task["invocation_schema"]
            expected_invocation = expected_task["invocation_schema"]
            self.assertEqual(actual_invocation["envelope"], "request")
            self.assertEqual(actual_invocation["required"], ["request"])
            self.assertEqual(
                actual_invocation["request"]["task_id"],
                actual_task["task_id"],
            )

            actual_parameters = actual_invocation["request"]["task_parameters"]
            expected_parameters = expected_invocation["request"]["task_parameters"]
            self.assertEqual(actual_parameters["type"], expected_parameters["type"])
            self.assertFalse(actual_parameters["additional_properties"])
            expected_properties = {"task_arm"} | {
                _renamed(name) for name in expected_parameters["properties"]
            }
            if actual_task["task_id"] == "mw_peg_insertion_side":
                expected_properties.update(
                    {"grasp_position", "grasp_wrist_rotate", "grasp_gripper"}
                )
            self.assertEqual(set(actual_parameters["properties"]), expected_properties)

            expected_required = ["task_arm"] + [
                _renamed(name) for name in expected_parameters["required"]
            ]
            if actual_task["task_id"] == "mw_peg_insertion_side":
                expected_required.extend(
                    ["grasp_position", "grasp_wrist_rotate", "grasp_gripper"]
                )
            self.assertEqual(actual_parameters["required"], expected_required)

            task_arm = actual_parameters["properties"]["task_arm"]
            self.assertEqual(task_arm["type"], "string")
            self.assertEqual(task_arm["unit"], "enum")
            self.assertEqual(task_arm["frame"], "robot_configuration")
            self.assertEqual(task_arm["enum"], ["left", "right"])
            self.assertIn("single-arm", task_arm["description"])
            self.assertIn("other arm", task_arm["description"])
            self.assertIn("neutral", task_arm["description"])
            self.assertIn("not a genuine bimanual task", task_arm["description"])

            for name in actual_parameters["required"]:
                property_schema = actual_parameters["properties"][name]
                for field in ("type", "unit", "frame", "description"):
                    self.assertIn(field, property_schema, name)
                self.assertTrue(property_schema["description"].strip())

            for name, property_schema in actual_parameters["properties"].items():
                if name.endswith("_position"):
                    self.assertEqual(property_schema["type"], "array")
                    self.assertEqual(property_schema["items"], {"type": "number"})
                    self.assertEqual(property_schema["length"], 3)
                    self.assertEqual(property_schema["unit"], "m")
                    self.assertEqual(property_schema["frame"], "world")

            for name in (
                "contact_position",
                "tool_target_position",
                "release_position",
                "route_position",
                "grasp_position",
            ):
                if name not in actual_parameters["properties"]:
                    continue
                description = actual_parameters["properties"][name]["description"]
                self.assertIn("selected arm's named gripper site", description)
                self.assertIn("left/gripper", description)
                self.assertIn("right/gripper", description)

            if "grasp_wrist_rotate" in actual_parameters["properties"]:
                wrist = actual_parameters["properties"]["grasp_wrist_rotate"]
                self.assertEqual(wrist["type"], "number")
                self.assertEqual(wrist["unit"], "rad")
                self.assertEqual(wrist["frame"], "selected_arm/wrist_rotate")
            if "grasp_gripper" in actual_parameters["properties"]:
                gripper = actual_parameters["properties"]["grasp_gripper"]
                self.assertEqual(gripper["type"], "number")
                self.assertEqual(gripper["unit"], "m")
                self.assertEqual(gripper["frame"], "selected_arm/gripper_actuator")
                self.assertIn("[0.002,0.037]", gripper["description"])
                self.assertIn("smaller values are closed", gripper["description"])
                self.assertIn("larger values are open", gripper["description"])

    def test_aloha_applicability_and_runnable_boundary(self) -> None:
        sources_document, catalog, tasks, _ = self._load()
        public_text = json.dumps(
            {"sources": sources_document, "catalog": catalog},
            sort_keys=True,
        ).lower()

        for forbidden in (
            "franka",
            "panda",
            "so101",
            "xarm",
            "piper",
            "kuka",
            "grasp_wrist_roll",
            "a01",
            "a02",
            "a03",
            "a04",
            "a05",
            "legacy",
            "mw_door_lock",
            "mw_door_unlock",
            "mw_faucet_close",
            "mw_soccer",
            "mw_window_open",
            "mw_window_close",
            "task passed",
            "7-dof",
            "the the",
        ):
            self.assertNotIn(forbidden, public_text)

        for task in tasks:
            applicability = task["applicability"].lower()
            self.assertIn("aloha 2", applicability)
            self.assertIn("two 6-axis arms", applicability)
            self.assertIn("single-arm execution", applicability)
            self.assertIn("other arm at neutral", applicability)
            self.assertIn("not a genuine bimanual task", applicability)
            self.assertIn("aloha 2", task["adaptation"].lower())
            self.assertIn("source numerical obligation is unchanged", task["adaptation"])
            self.assertIn("no task execution or task-passing claim", task["adaptation"])
            for clause in task["scoring"]:
                self.assertTrue(
                    all(ref["source_id"] == "metaworld_repo" for ref in clause["source_refs"])
                )
                self.assertNotIn("mujoco_menagerie_aloha", {
                    ref["source_id"] for ref in clause["source_refs"]
                })

        runnable_index = _read_json(RUNNABLE_INDEX_PATH)
        self.assertNotIn("aloha_2", runnable_index["robots"])


if __name__ == "__main__":
    unittest.main()
