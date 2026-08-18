from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autoadapter2.libraries import (
    RobotPackageError,
    load_indexed_robot_package,
    load_robot_package,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _source() -> dict[str, object]:
    return {
        "source_id": "source-1",
        "title": "Primary evaluation protocol",
        "organization": "Example Benchmark",
        "version_or_date": "2026",
        "locator": "https://example.test/protocol",
        "specific_reference": "Section 2 evaluation definition",
    }


def _task(index: int) -> dict[str, object]:
    task_id = f"task-{index:02d}"
    return {
        "task_id": task_id,
        "name": f"Task {index}",
        "description": "Execute one physically applicable robot operation.",
        "source_task_or_operation": f"Protocol operation {index}",
        "applicability": "The selected configuration can execute this operation.",
        "adaptation": "Source metric retained with robot-specific scene geometry.",
        "scene_assumptions": ["Canonical local MJCF scene"],
        "observation_assumptions": ["Trusted simulator state"],
        "invocation_schema": {
            "envelope": "request",
            "required": ["request"],
            "request": {
                "type": "object",
                "required": ["task_id", "task_parameters"],
                "task_id": task_id,
                "task_parameters": {
                    "type": "object",
                    "required": ["target"],
                    "properties": {
                        "target": {
                            "type": "array",
                            "items": {"type": "number"},
                            "length": 3,
                            "unit": "m",
                            "frame": "world",
                        }
                    },
                    "additional_properties": False,
                },
            },
        },
        "scoring": [
            {
                "clause_id": "completion",
                "metric": "terminal_error",
                "unit": "m",
                "comparator": "<=",
                "threshold": 0.02,
                "temporal": {"kind": "terminal"},
                "aggregation": {"kind": "all"},
                "source_refs": [
                    {
                        "source_id": "source-1",
                        "specific_reference": "Section 2 tolerance",
                        "support": "adapted",
                        "adaptation": "Converted source geometry to metres without relaxing tolerance.",
                    }
                ],
            }
        ],
    }


class RobotPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "example" / "1.0.0"
        (self.root / "assets").mkdir(parents=True)
        (self.root / "assets" / "scene.xml").write_text(
            '<mujoco model="package-test"/>', encoding="utf-8"
        )
        (self.root / "skeleton").mkdir()
        (self.root / "skeleton" / "arm.py").write_text("class Arm: pass\n", encoding="utf-8")
        (self.root / "reference").mkdir()
        (self.root / "reference" / "driver.py").write_text("def build(): pass\n", encoding="utf-8")
        identity = {
            "schema_version": "1.0",
            "robot_configuration_id": "example-arm",
            "package_version": "1.0.0",
        }
        _write_json(
            self.root / "morphology.json",
            {**identity, "mjcf_entrypoint": "assets/scene.xml"},
        )
        _write_json(
            self.root / "tasks" / "sources.json",
            {**identity, "sources": [_source()]},
        )
        _write_json(
            self.root / "tasks" / "catalog.json",
            {**identity, "snapshot_id": "snapshot-1", "tasks": [_task(i) for i in range(20)]},
        )
        private_identity = {**identity, "task_snapshot_id": "snapshot-1"}
        _write_json(
            self.root / "tasks" / "private" / "bindings.json",
            {
                **private_identity,
                "bindings": [
                    {
                        "binding_id": "terminal-binding",
                        "metric": "terminal_error",
                        "unit": "m",
                        "kind": "final_body_position_error",
                        "parameters": {
                            "body_name": "world",
                            "target_argument": "request.task_parameters.target",
                        },
                    }
                ],
            },
        )
        guards = [
            {
                "guard_id": f"guard-{index}",
                "kind": kind,
            }
            for index, kind in enumerate(
                (
                    "actuator_and_physics_step_required",
                    "no_direct_state_write",
                    "canonical_model_data",
                )
            )
        ]
        _write_json(
            self.root / "tasks" / "private" / "guards.json",
            {**private_identity, "guards": guards},
        )
        instances = [
            {
                "instance_id": f"instance-{index:02d}",
                "task_id": f"task-{index:02d}",
                "scene_entrypoint": "assets/scene.xml",
                "public_arguments": {
                    "request": {
                        "task_id": f"task-{index:02d}",
                        "task_parameters": {"target": [0.0, 0.0, 0.0]},
                    }
                },
                "reset": {"kind": "default"},
                "clause_bindings": {"completion": "terminal-binding"},
                "guard_ids": [guard["guard_id"] for guard in guards],
                "repetitions": 1,
                "timeout_sim_s": 1.0,
                "max_steps": 10,
            }
            for index in range(20)
        ]
        _write_json(
            self.root / "tasks" / "private" / "instances.json",
            {**private_identity, "instances": instances},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_package_loads(self) -> None:
        package = load_robot_package(self.root)

        self.assertEqual(package.robot_configuration_id, "example-arm")
        self.assertEqual(len(package.tasks), 20)
        self.assertEqual(package.mjcf_path.name, "scene.xml")

    def test_fewer_than_twenty_tasks_fails_closed(self) -> None:
        catalog_path = self.root / "tasks" / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["tasks"] = catalog["tasks"][:19]
        _write_json(catalog_path, catalog)

        with self.assertRaisesRegex(RobotPackageError, "at least 20 tasks"):
            load_robot_package(self.root)

    def test_unknown_scoring_source_fails_closed(self) -> None:
        catalog_path = self.root / "tasks" / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["tasks"][0]["scoring"][0]["source_refs"][0]["source_id"] = "missing"
        _write_json(catalog_path, catalog)

        with self.assertRaisesRegex(RobotPackageError, "unknown source"):
            load_robot_package(self.root)

    def test_task_parameter_schema_requires_units_frames_and_closed_fields(self) -> None:
        catalog_path = self.root / "tasks" / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        parameters = catalog["tasks"][0]["invocation_schema"]["request"][
            "task_parameters"
        ]
        parameters["properties"]["target"].pop("frame")
        _write_json(catalog_path, catalog)

        with self.assertRaisesRegex(RobotPackageError, "frame must be a non-empty string"):
            load_robot_package(self.root)

        parameters["properties"]["target"]["frame"] = "world"
        parameters.pop("additional_properties")
        _write_json(catalog_path, catalog)
        with self.assertRaisesRegex(RobotPackageError, "forbid additional properties"):
            load_robot_package(self.root)

    def test_private_instance_cannot_add_an_undeclared_task_parameter(self) -> None:
        instances_path = self.root / "tasks" / "private" / "instances.json"
        document = json.loads(instances_path.read_text(encoding="utf-8"))
        document["instances"][0]["public_arguments"]["request"]["task_parameters"][
            "hidden_hint"
        ] = 1.0
        _write_json(instances_path, document)

        with self.assertRaisesRegex(RobotPackageError, "undeclared fields"):
            load_robot_package(self.root)

    def test_mjcf_include_cannot_escape_assets(self) -> None:
        (self.root / "outside.xml").write_text(
            '<mujoco model="outside"/>', encoding="utf-8"
        )
        (self.root / "assets" / "scene.xml").write_text(
            '<mujoco model="package-test"><include file="../outside.xml"/></mujoco>',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RobotPackageError, "package-relative"):
            load_robot_package(self.root)

    def test_github_source_must_pin_revision(self) -> None:
        sources_path = self.root / "tasks" / "sources.json"
        sources = json.loads(sources_path.read_text(encoding="utf-8"))
        sources["sources"][0]["locator"] = (
            "https://github.com/example/benchmark/blob/main/protocol.md"
        )
        _write_json(sources_path, sources)

        with self.assertRaisesRegex(RobotPackageError, "pin a GitHub revision"):
            load_robot_package(self.root)

        sources["sources"][0]["locator"] = "https://github.com/example/benchmark"
        sources["sources"][0]["version_or_date"] = "main branch, accessed 2026"
        _write_json(sources_path, sources)
        with self.assertRaisesRegex(RobotPackageError, "pin a GitHub revision"):
            load_robot_package(self.root)

    def test_repetition_variants_must_match_repetitions(self) -> None:
        instances_path = self.root / "tasks" / "private" / "instances.json"
        document = json.loads(instances_path.read_text(encoding="utf-8"))
        instance = document["instances"][0]
        instance["repetitions"] = 2
        instance["repetition_variants"] = [
            {
                "public_arguments": {
                    "request": {
                        "task_id": "task-00",
                        "task_parameters": {"target": [0.1, 0.0, 0.0]},
                    }
                },
                "reset": {
                    "kind": "default",
                    "body_quaternions": {"terrain": [1.0, 0.0, 0.0, 0.0]},
                },
            },
            {
                "public_arguments": {
                    "request": {
                        "task_id": "task-00",
                        "task_parameters": {"target": [0.2, 0.0, 0.0]},
                    }
                }
            },
        ]
        _write_json(instances_path, document)
        self.assertEqual(load_robot_package(self.root).robot_configuration_id, "example-arm")

        instance["repetition_variants"].pop()
        _write_json(instances_path, document)
        with self.assertRaisesRegex(RobotPackageError, "one entry per repetition"):
            load_robot_package(self.root)

    def test_only_explicit_index_entry_is_runnable(self) -> None:
        demo_root = Path(self.temporary.name) / "demo3"
        indexed = demo_root / "libraries" / "robots" / "example" / "1.0.0"
        indexed.parent.mkdir(parents=True)
        self.root.rename(indexed)
        _write_json(
            demo_root / "libraries" / "robots" / "index.json",
            {"robots": {"example-arm": "example/1.0.0"}},
        )

        package = load_indexed_robot_package(demo_root, "example-arm")
        self.assertEqual(package.robot_configuration_id, "example-arm")
        with self.assertRaisesRegex(RobotPackageError, "not runnable"):
            load_indexed_robot_package(demo_root, "research-only")


if __name__ == "__main__":
    unittest.main()
