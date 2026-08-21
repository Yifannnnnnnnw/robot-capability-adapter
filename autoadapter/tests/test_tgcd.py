from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autoadapter2.capability_design import (
    CapabilityDesignError,
    run_tgcd,
    validate_capability_design,
)
from autoadapter2.libraries import RobotPackage


def _task(index: int) -> dict:
    clause = {
        "clause_id": "terminal-error",
        "metric": "terminal_error",
        "unit": "m",
        "comparator": "<=",
        "threshold": 0.02,
        "temporal": {"kind": "terminal"},
        "aggregation": {"kind": "all"},
        "source_refs": [
            {
                "source_id": "source-1",
                "specific_reference": "Section 2",
                "support": "direct",
            }
        ],
    }
    task_id = f"task-{index:02d}"
    return {
        "task_id": task_id,
        "scene_assumptions": [f"scene-{index:02d}"],
        "scoring": [clause],
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
                            "unit": "m",
                            "frame": "world",
                            "description": "Terminal task-entity target.",
                        }
                    },
                },
            },
        },
    }


def _package(root: Path) -> RobotPackage:
    tasks = tuple(_task(index) for index in range(20))
    return RobotPackage(
        root=root,
        robot_configuration_id="example-arm",
        package_version="1.0.0",
        snapshot_id="snapshot-1",
        morphology={
            "robot_configuration_id": "example-arm",
            "public_affordances": {
                "actions": ["joint_position_control"],
                "observations": ["joint_positions"],
            },
        },
        sources=({"source_id": "source-1"},),
        tasks=tasks,
        mjcf_path=root / "scene.xml",
        skeleton_dir=root / "skeleton",
        reference_driver=root / "reference" / "driver.py",
        private_dir=root / "private",
    )


def _design(package: RobotPackage) -> dict:
    capabilities = []
    for group in range(5):
        tasks = package.tasks[group * 4 : group * 4 + 4]
        task = tasks[0]
        clause = task["scoring"][0]
        contracts = [
            {
                "case_role": "primary",
                "selection_rationale": "Representative source-backed endpoint criterion.",
                "source_task_id": task["task_id"],
                "source_clause_id": clause["clause_id"],
                **{key: clause[key] for key in (
                    "metric",
                    "unit",
                    "comparator",
                    "threshold",
                    "temporal",
                    "aggregation",
                    "source_refs",
                )},
            }
        ]
        capabilities.append(
            {
                "capability_id": f"capability-{group}",
                "effect": f"shared effect {group}",
                "method_name": f"perform_effect_{group}",
                "description": "One reusable physical effect.",
                "covered_task_ids": [task["task_id"] for task in tasks],
                "abstraction_rationale": "The tasks share the same robot motion.",
                "interface": {
                    "inputs": [
                        {
                            "name": "request",
                            "type": "object",
                            "unit": "unitless",
                            "frame": "none",
                        },
                        {
                            "name": "request.task_parameters.target",
                            "type": "array",
                            "unit": "m",
                            "frame": "world",
                            "required_for_task_ids": [task["task_id"] for task in tasks],
                        },
                    ],
                    "outputs": [{"name": "completed", "type": "bool", "unit": "unitless", "frame": "none"}],
                },
                "preconditions": ["Canonical scene is active."],
                "temporal_semantics": {"kind": "bounded"},
                "invariants": ["Actuator-driven motion only."],
                "required_affordances": {
                    "actions": ["joint_position_control"],
                    "observations": ["joint_positions"],
                },
                "failure_behavior": "Raise a public runtime error.",
                "validation_contract": contracts,
            }
        )
    return {
        "artifact_type": "capability_design",
        "schema_version": "1.0",
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
        "invocation_abi": {
            "kind": "keyword_request",
            "method_call": "method(request=request)",
            "request_required": ["task_id", "task_parameters"],
        },
        "capabilities": capabilities,
    }


class _CapturingModel:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.inputs = None

    def generate_json(self, *, stage, prompt, inputs):
        self.inputs = inputs
        return self.response


class _SequenceModel:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls = []

    def generate_json(self, *, stage, prompt, inputs):
        self.calls.append({"stage": stage, "inputs": inputs})
        return self.responses[len(self.calls) - 1]


class TGCDTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.package = _package(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_valid_design_covers_all_tasks_with_one_primary_per_capability(self) -> None:
        design = validate_capability_design(_design(self.package), self.package)

        self.assertEqual(len(design["capabilities"]), 5)
        self.assertTrue(
            all(
                len(capability["validation_contract"]) == 1
                for capability in design["capabilities"]
            )
        )

    def test_one_materially_distinct_robustness_contract_is_allowed(self) -> None:
        design = _design(self.package)
        source_task = self.package.tasks[1]
        source_clause = source_task["scoring"][0]
        design["capabilities"][0]["validation_contract"].append(
            {
                "case_role": "robustness",
                "selection_rationale": "A different scene exercises the same endpoint effect.",
                "source_task_id": source_task["task_id"],
                "source_clause_id": source_clause["clause_id"],
                **{
                    key: source_clause[key]
                    for key in (
                        "metric",
                        "unit",
                        "comparator",
                        "threshold",
                        "temporal",
                        "aggregation",
                        "source_refs",
                    )
                },
            }
        )

        validated = validate_capability_design(design, self.package)

        self.assertEqual(len(validated["capabilities"][0]["validation_contract"]), 2)

    def test_more_than_one_robustness_contract_is_rejected(self) -> None:
        design = _design(self.package)
        primary = design["capabilities"][0]["validation_contract"][0]
        for task in self.package.tasks[1:3]:
            design["capabilities"][0]["validation_contract"].append(
                {
                    **primary,
                    "case_role": "robustness",
                    "selection_rationale": "Different private task scene.",
                    "source_task_id": task["task_id"],
                }
            )

        with self.assertRaisesRegex(CapabilityDesignError, "at most one robustness"):
            validate_capability_design(design, self.package)

    def test_keyed_interface_objects_are_canonicalized_to_typed_lists(self) -> None:
        design = _design(self.package)
        design["capabilities"][0]["interface"] = {
            "inputs": {
                "request": {"type": "object", "unit": "unitless", "frame": "none"},
                "request.task_parameters.target": {
                    "type": "array",
                    "unit": "m",
                    "frame": "world",
                    "required_for_task_ids": [f"task-{index:02d}" for index in range(4)],
                },
            },
            "outputs": {
                "completed": {"type": "bool", "unit": "unitless", "frame": "none"}
            },
        }

        validated = validate_capability_design(design, self.package)

        self.assertEqual(validated["capabilities"][0]["interface"]["inputs"][0]["name"], "request")

    def test_scalar_semantic_containers_are_canonicalized_without_content_change(self) -> None:
        design = _design(self.package)
        capability = design["capabilities"][0]
        capability["preconditions"] = "Canonical scene is active."
        capability["temporal_semantics"] = "Complete within the request duration."
        capability["invariants"] = "Actuator-driven motion only."
        capability["required_affordances"] = {
            "actions": "joint_position_control",
            "observations": "joint_positions",
        }

        validated = validate_capability_design(design, self.package)

        normalized = validated["capabilities"][0]
        self.assertEqual(normalized["preconditions"], ["Canonical scene is active."])
        self.assertEqual(
            normalized["temporal_semantics"]["description"],
            "Complete within the request duration.",
        )

    def test_missing_task_is_rejected(self) -> None:
        design = _design(self.package)
        design["capabilities"][0]["covered_task_ids"].pop()
        design["capabilities"][0]["interface"]["inputs"][1]["required_for_task_ids"].pop()

        with self.assertRaisesRegex(
            CapabilityDesignError,
            "outside this capability|covered exactly once",
        ):
            validate_capability_design(design, self.package)

    def test_weakened_source_threshold_is_rejected(self) -> None:
        design = _design(self.package)
        design["capabilities"][0]["validation_contract"][0]["threshold"] = 0.2

        with self.assertRaisesRegex(CapabilityDesignError, "changes a source pass standard"):
            validate_capability_design(design, self.package)

    def test_nonstandard_invocation_abi_is_rejected(self) -> None:
        design = _design(self.package)
        design["invocation_abi"]["method_call"] = "method(target=target)"

        with self.assertRaisesRegex(CapabilityDesignError, "fixed public request envelope"):
            validate_capability_design(design, self.package)

    def test_missing_required_task_parameter_input_is_rejected(self) -> None:
        design = _design(self.package)
        design["capabilities"][0]["interface"]["inputs"].pop()

        with self.assertRaisesRegex(CapabilityDesignError, "required task parameters"):
            validate_capability_design(design, self.package)

    def test_required_parameter_task_ids_are_mechanically_canonicalized(self) -> None:
        design = _design(self.package)
        parameter = design["capabilities"][0]["interface"]["inputs"][1]
        parameter["required_for_task_ids"] = ["task-00"]

        validated = validate_capability_design(design, self.package)

        self.assertEqual(
            validated["capabilities"][0]["interface"]["inputs"][1][
                "required_for_task_ids"
            ],
            ["task-00", "task-01", "task-02", "task-03"],
        )
        self.assertEqual(
            validated["capabilities"][0]["interface"]["inputs"][1]["description"],
            "Terminal task-entity target.",
        )

    def test_unsupported_robot_affordance_is_rejected(self) -> None:
        design = _design(self.package)
        design["capabilities"][0]["required_affordances"]["actions"] = ["teleport"]

        with self.assertRaisesRegex(CapabilityDesignError, "unsupported affordances"):
            validate_capability_design(design, self.package)

    def test_model_receives_no_private_package_data(self) -> None:
        model = _CapturingModel(_design(self.package))

        run_tgcd(model, self.package)

        self.assertEqual(set(model.inputs), {"morphology", "task_library", "experience"})
        self.assertNotIn("private", repr(model.inputs).lower())

    def test_one_structural_correction_remains_public(self) -> None:
        invalid = _design(self.package)
        invalid["capabilities"][0]["covered_task_ids"].pop()
        model = _SequenceModel([invalid, _design(self.package)])

        result = run_tgcd(model, self.package)

        self.assertEqual(len(result["capabilities"]), 5)
        self.assertEqual([call["stage"] for call in model.calls], [
            "tgcd",
            "tgcd-structure-correction",
        ])
        self.assertEqual(
            model.calls[1]["inputs"]["previous_invalid_design"], invalid
        )
        self.assertIn("deterministic_audit_error", model.calls[1]["inputs"])
        self.assertNotIn("private", repr(model.calls).lower())

    def test_second_structural_correction_recovers_repeated_count_error(self) -> None:
        invalid = _design(self.package)
        invalid["capabilities"].extend(
            dict(invalid["capabilities"][0]) for _ in range(6)
        )
        invalid_again = _design(self.package)
        invalid_again["capabilities"].extend(
            dict(invalid_again["capabilities"][0]) for _ in range(6)
        )
        model = _SequenceModel(
            [invalid, invalid_again, _design(self.package)]
        )

        result = run_tgcd(model, self.package)

        self.assertEqual(len(result["capabilities"]), 5)
        self.assertEqual(
            [call["stage"] for call in model.calls],
            [
                "tgcd",
                "tgcd-structure-correction",
                "tgcd-structure-correction-2",
            ],
        )
        self.assertEqual(
            model.calls[1]["inputs"]["previous_invalid_design"], invalid
        )
        self.assertEqual(
            model.calls[2]["inputs"]["previous_invalid_design"], invalid_again
        )
        self.assertTrue(
            all(
                "between 5 and 10" in call["inputs"]["deterministic_audit_error"]
                for call in model.calls[1:]
            )
        )
        self.assertNotIn("private", repr(model.calls).lower())


if __name__ == "__main__":
    unittest.main()
