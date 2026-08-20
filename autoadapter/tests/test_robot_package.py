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
            {
                **identity,
                "mjcf_entrypoint": "assets/scene.xml",
                "public_affordances": {
                    "actions": ["joint_position_control"],
                    "observations": ["joint_positions"],
                },
            },
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

    def _replace_first_contract(
        self,
        *,
        metric: str,
        unit: str,
        comparator: str,
        threshold: float | int,
        temporal: dict[str, object],
        aggregation: str,
        parameter_schemas: dict[str, object],
        public_parameters: dict[str, object],
        binding: dict[str, object],
        repetitions: int = 1,
        max_steps: int = 10,
        timeout_sim_s: float = 1.0,
    ) -> None:
        catalog_path = self.root / "tasks" / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        task = catalog["tasks"][0]
        task["invocation_schema"]["request"]["task_parameters"] = {
            "type": "object",
            "required": list(parameter_schemas),
            "properties": parameter_schemas,
            "additional_properties": False,
        }
        task["scoring"] = [
            {
                "clause_id": "completion",
                "metric": metric,
                "unit": unit,
                "comparator": comparator,
                "threshold": threshold,
                "temporal": temporal,
                "aggregation": {"kind": aggregation},
                "source_refs": [
                    {
                        "source_id": "source-1",
                        "specific_reference": "Section 2 tolerance",
                        "support": "direct",
                    }
                ],
            }
        ]
        _write_json(catalog_path, catalog)

        bindings_path = self.root / "tasks" / "private" / "bindings.json"
        bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
        bindings["bindings"].append(binding)
        _write_json(bindings_path, bindings)

        instances_path = self.root / "tasks" / "private" / "instances.json"
        instances = json.loads(instances_path.read_text(encoding="utf-8"))
        instance = instances["instances"][0]
        instance["public_arguments"]["request"]["task_parameters"] = public_parameters
        instance["clause_bindings"]["completion"] = binding["binding_id"]
        instance["repetitions"] = repetitions
        instance["max_steps"] = max_steps
        instance["timeout_sim_s"] = timeout_sim_s
        _write_json(instances_path, instances)

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

    def test_private_instance_cannot_supply_an_optional_interface_parameter(self) -> None:
        catalog_path = self.root / "tasks" / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        parameters = catalog["tasks"][0]["invocation_schema"]["request"][
            "task_parameters"
        ]
        parameters["properties"]["route"] = {
            "type": "number",
            "unit": "m",
            "frame": "world",
        }
        _write_json(catalog_path, catalog)
        instances_path = self.root / "tasks" / "private" / "instances.json"
        document = json.loads(instances_path.read_text(encoding="utf-8"))
        document["instances"][0]["public_arguments"]["request"]["task_parameters"][
            "route"
        ] = 0.5
        _write_json(instances_path, document)

        with self.assertRaisesRegex(RobotPackageError, "required capability interface"):
            load_robot_package(self.root)

    def test_named_contact_pair_guard_requires_valid_pair_parameters(self) -> None:
        guards_path = self.root / "tasks" / "private" / "guards.json"
        guards = json.loads(guards_path.read_text(encoding="utf-8"))
        guards["guards"].append(
            {
                "guard_id": "robot-task-contact",
                "kind": "named_geom_contact_pair_required",
                "robot_geom_name": "link7_contact_geom",
                "task_geom_names": ["button_geom", "button_cap_geom"],
                "minimum_steps": 2,
            }
        )
        _write_json(guards_path, guards)
        self.assertEqual(load_robot_package(self.root).robot_configuration_id, "example-arm")

        multiple_robot_geoms = json.loads(json.dumps(guards))
        multiple_robot_geoms["guards"][-1].pop("robot_geom_name")
        multiple_robot_geoms["guards"][-1]["robot_geom_names"] = [
            "left/left_g0",
            "left/left_g1",
            "left/left_g2",
        ]
        _write_json(guards_path, multiple_robot_geoms)
        self.assertEqual(load_robot_package(self.root).robot_configuration_id, "example-arm")

        invalid_variants = (
            ("task_geom_names", [], "task_geom_names must be a non-empty list"),
            ("minimum_steps", 0, "minimum_steps must be a positive integer"),
            (
                "robot_geom_names",
                ["left/left_g0"],
                "exactly one of robot_geom_name or robot_geom_names",
            ),
        )
        for field, value, message in invalid_variants:
            with self.subTest(field=field):
                invalid = json.loads(json.dumps(guards))
                invalid["guards"][-1][field] = value
                _write_json(guards_path, invalid)
                with self.assertRaisesRegex(RobotPackageError, message):
                    load_robot_package(self.root)

    def test_named_joint_neutral_guard_requires_per_joint_tolerances(self) -> None:
        guards_path = self.root / "tasks" / "private" / "guards.json"
        guards = json.loads(guards_path.read_text(encoding="utf-8"))
        guards["guards"].append(
            {
                "guard_id": "other-arm-neutral",
                "kind": "named_joints_remain_near_reset",
                "joint_tolerances": {
                    "left/waist": 0.01,
                    "left/left_finger": 0.0005,
                },
            }
        )
        _write_json(guards_path, guards)
        self.assertEqual(
            load_robot_package(self.root).robot_configuration_id,
            "example-arm",
        )

        invalid_tolerances = ({}, {"left/waist": 0.0}, {"left/waist": True})
        for tolerances in invalid_tolerances:
            with self.subTest(tolerances=tolerances):
                invalid = json.loads(json.dumps(guards))
                invalid["guards"][-1]["joint_tolerances"] = tolerances
                _write_json(guards_path, invalid)
                with self.assertRaisesRegex(
                    RobotPackageError,
                    "joint_tolerances",
                ):
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

    def test_leap_binding_kinds_require_their_exact_private_parameters(self) -> None:
        bindings_path = self.root / "tasks" / "private" / "bindings.json"
        original = json.loads(bindings_path.read_text(encoding="utf-8"))
        kinds_and_units = (
            ("in_hand_object_pattern_success", "trial"),
            ("final_concatenated_site_position_error", "m"),
            ("final_body_position_offset_error", "m"),
            ("final_body_quaternion_error", "rad"),
            ("final_maximum_joint_position_error", "rad"),
            ("final_wrapped_joint_position_error", "rad"),
            ("maximum_joint_linear_trajectory_error", "rad"),
            ("body_target_solved_sample_count", "control_step"),
            ("body_target_drop_event_count", "event"),
            ("mean_two_body_orbit_tracking_fraction", "ratio"),
        )
        for kind, unit in kinds_and_units:
            with self.subTest(kind=kind):
                document = json.loads(json.dumps(original))
                binding = document["bindings"][0]
                binding["kind"] = kind
                binding["unit"] = unit
                binding["parameters"] = {}
                _write_json(bindings_path, document)
                with self.assertRaisesRegex(
                    RobotPackageError, "misses required fields"
                ):
                    load_robot_package(self.root)

    def test_control_step_budget_cannot_be_used_as_a_joint_target(self) -> None:
        bindings_path = self.root / "tasks" / "private" / "bindings.json"
        document = json.loads(bindings_path.read_text(encoding="utf-8"))
        document["bindings"][0]["parameters"]["target_argument"] = (
            "request.task_parameters.max_control_steps"
        )
        _write_json(bindings_path, document)

        with self.assertRaisesRegex(RobotPackageError, "control-step budget"):
            load_robot_package(self.root)

    def test_body_yaw_change_degrees_cannot_be_declared_as_radians(self) -> None:
        bindings_path = self.root / "tasks" / "private" / "bindings.json"
        document = json.loads(bindings_path.read_text(encoding="utf-8"))
        document["bindings"][0]["kind"] = "body_yaw_change_deg"
        _write_json(bindings_path, document)

        with self.assertRaisesRegex(RobotPackageError, "unit must be deg"):
            load_robot_package(self.root)

    def test_ec_fixed_trials_require_three_independent_binary_repetitions(self) -> None:
        self._replace_first_contract(
            metric="ec_pinch_successful_trial_count",
            unit="trial",
            comparator="==",
            threshold=3,
            temporal={"kind": "fixed_trials", "trial_count": 3},
            aggregation="all_trials",
            parameter_schemas={
                "trial_count": {
                    "type": "integer",
                    "unit": "trial",
                    "frame": "none",
                }
            },
            public_parameters={"trial_count": 3},
            binding={
                "binding_id": "ec-pinch",
                "metric": "ec_pinch_successful_trial_count",
                "unit": "trial",
                "kind": "in_hand_object_pattern_success",
                "parameters": {
                    "body_name": "object",
                    "reference_body_name": "world",
                    "object_geom_names": ["object"],
                    "required_robot_geom_groups": [["finger"]],
                    "minimum_contact_steps": 1,
                    "motion_kind": "translation",
                    "axis": 0,
                    "minimum_translation_range_m": 0.01,
                },
            },
            repetitions=1,
        )

        with self.assertRaisesRegex(RobotPackageError, "every fixed_trials repetition"):
            load_robot_package(self.root)

    def test_baoding_mean_cannot_be_bound_to_contact_count(self) -> None:
        number = {"type": "number", "unit": "m", "frame": "right_palm"}
        self._replace_first_contract(
            metric="mean_two_ball_solved_fraction",
            unit="ratio",
            comparator="==",
            threshold=1,
            temporal={"kind": "fixed_horizon", "max_control_steps": 2},
            aggregation="mean_over_control_steps",
            parameter_schemas={
                "max_control_steps": {
                    "type": "integer",
                    "unit": "control_step",
                    "frame": "none",
                },
                "radii": {
                    "type": "array",
                    "unit": "m",
                    "frame": "right_palm",
                    "items": number,
                    "length": 2,
                },
                "period": {"type": "number", "unit": "s", "frame": "none"},
            },
            public_parameters={
                "max_control_steps": 2,
                "radii": [0.025, 0.028],
                "period": 5.0,
            },
            binding={
                "binding_id": "bad-baoding",
                "metric": "mean_two_ball_solved_fraction",
                "unit": "ratio",
                "kind": "contact_sample_count",
                "parameters": {},
            },
        )

        with self.assertRaisesRegex(RobotPackageError, "misinterprets source metric"):
            load_robot_package(self.root)

    def test_manipulated_block_body_must_have_a_scene_joint(self) -> None:
        (self.root / "assets" / "scene.xml").write_text(
            '<mujoco model="fixed-block"><worldbody><body name="palm"/>'
            '<body name="block"/></worldbody></mujoco>',
            encoding="utf-8",
        )
        vector = {
            "type": "array",
            "unit": "m",
            "frame": "right_palm",
            "items": {"type": "number"},
            "length": 3,
        }
        quaternion = {
            "type": "array",
            "unit": "quaternion",
            "frame": "right_palm",
            "items": {"type": "number"},
            "length": 4,
        }
        self._replace_first_contract(
            metric="block_target_euclidean_position_error",
            unit="m",
            comparator="<",
            threshold=0.01,
            temporal={"kind": "terminal_step", "max_control_steps": 1},
            aggregation="same_state_conjunction",
            parameter_schemas={
                "position": vector,
                "orientation": quaternion,
                "max_control_steps": {
                    "type": "integer",
                    "unit": "control_step",
                    "frame": "none",
                },
            },
            public_parameters={
                "position": [0.0, 0.0, 0.0],
                "orientation": [1.0, 0.0, 0.0, 0.0],
                "max_control_steps": 1,
            },
            binding={
                "binding_id": "block-position",
                "metric": "block_target_euclidean_position_error",
                "unit": "m",
                "kind": "final_body_position_offset_error",
                "parameters": {
                    "body_name": "block",
                    "reference_body_name": "palm",
                    "target_argument": "request.task_parameters.position",
                    "orientation_target_argument": "request.task_parameters.orientation",
                    "maximum_orientation_error_rad": 0.1,
                    "physics_steps_per_control_step": 1,
                    "control_steps_argument": "request.task_parameters.max_control_steps",
                },
            },
            max_steps=1,
        )

        with self.assertRaisesRegex(RobotPackageError, "fixed, not manipulable"):
            load_robot_package(self.root)

    def test_source_duration_must_match_timestep_and_physics_steps(self) -> None:
        (self.root / "assets" / "scene.xml").write_text(
            '<mujoco model="moving-block"><worldbody><body name="palm"/>'
            '<body name="block"><freejoint/><geom type="box" size="0.01 0.01 0.01"/>'
            "</body></worldbody></mujoco>",
            encoding="utf-8",
        )
        vector = {
            "type": "array",
            "unit": "m",
            "frame": "right_palm",
            "items": {"type": "number"},
            "length": 3,
        }
        quaternion = {
            "type": "array",
            "unit": "quaternion",
            "frame": "right_palm",
            "items": {"type": "number"},
            "length": 4,
        }
        self._replace_first_contract(
            metric="block_target_euclidean_position_error",
            unit="m",
            comparator="<",
            threshold=0.01,
            temporal={
                "kind": "terminal_step",
                "max_control_steps": 2,
                "duration_s": 1.0,
            },
            aggregation="same_state_conjunction",
            parameter_schemas={
                "position": vector,
                "orientation": quaternion,
                "max_control_steps": {
                    "type": "integer",
                    "unit": "control_step",
                    "frame": "none",
                },
            },
            public_parameters={
                "position": [0.0, 0.0, 0.0],
                "orientation": [1.0, 0.0, 0.0, 0.0],
                "max_control_steps": 2,
            },
            binding={
                "binding_id": "block-position",
                "metric": "block_target_euclidean_position_error",
                "unit": "m",
                "kind": "final_body_position_offset_error",
                "parameters": {
                    "body_name": "block",
                    "reference_body_name": "palm",
                    "target_argument": "request.task_parameters.position",
                    "orientation_target_argument": "request.task_parameters.orientation",
                    "maximum_orientation_error_rad": 0.1,
                    "physics_steps_per_control_step": 1,
                    "control_steps_argument": "request.task_parameters.max_control_steps",
                },
            },
            max_steps=2,
            timeout_sim_s=1.0,
        )

        with self.assertRaisesRegex(RobotPackageError, "source duration is inconsistent"):
            load_robot_package(self.root)

    def test_zero_contact_score_cannot_require_contact_guard(self) -> None:
        catalog_path = self.root / "tasks" / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["tasks"][0]["scoring"][0]["comparator"] = "=="
        catalog["tasks"][0]["scoring"][0]["threshold"] = 0
        _write_json(catalog_path, catalog)

        bindings_path = self.root / "tasks" / "private" / "bindings.json"
        bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
        bindings["bindings"][0]["kind"] = "contact_sample_count"
        bindings["bindings"][0]["parameters"] = {}
        _write_json(bindings_path, bindings)

        guards_path = self.root / "tasks" / "private" / "guards.json"
        guards = json.loads(guards_path.read_text(encoding="utf-8"))
        guards["guards"].append(
            {
                "guard_id": "required-contact",
                "kind": "named_geom_contact_pair_required",
                "robot_geom_name": "finger",
                "task_geom_names": ["object"],
                "minimum_steps": 1,
            }
        )
        _write_json(guards_path, guards)

        instances_path = self.root / "tasks" / "private" / "instances.json"
        instances = json.loads(instances_path.read_text(encoding="utf-8"))
        instances["instances"][0]["guard_ids"].append("required-contact")
        _write_json(instances_path, instances)

        with self.assertRaisesRegex(RobotPackageError, "require contact"):
            load_robot_package(self.root)

    def test_only_explicit_index_entry_is_runnable(self) -> None:
        mainline_root = Path(self.temporary.name) / "autoadapter"
        indexed = mainline_root / "libraries" / "robots" / "example" / "1.0.0"
        indexed.parent.mkdir(parents=True)
        self.root.rename(indexed)
        _write_json(
            mainline_root / "libraries" / "robots" / "index.json",
            {"robots": {"example-arm": "example/1.0.0"}},
        )

        package = load_indexed_robot_package(mainline_root, "example-arm")
        self.assertEqual(package.robot_configuration_id, "example-arm")
        with self.assertRaisesRegex(RobotPackageError, "not runnable"):
            load_indexed_robot_package(mainline_root, "research-only")


if __name__ == "__main__":
    unittest.main()
