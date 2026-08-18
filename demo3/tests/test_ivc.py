from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autoadapter2.libraries import RobotPackage
from autoadapter2.validation_compiler import (
    IVCError,
    run_ivc,
    sample_private_suite,
    validate_private_suite,
)


def _package(root: Path) -> RobotPackage:
    tasks = []
    for index in range(20):
        tasks.append(
            {
                "task_id": f"task-{index:02d}",
                "scoring": [
                    {
                        "clause_id": "terminal-error",
                        "metric": "terminal_error",
                        "unit": "m",
                        "comparator": "<=",
                        "threshold": 0.02,
                        "temporal": {"kind": "terminal"},
                        "aggregation": {"kind": "all"},
                        "source_refs": [{"source_id": "source-1", "support": "direct"}],
                    }
                ],
            }
        )
    return RobotPackage(
        root=root,
        robot_configuration_id="example-arm",
        package_version="1.0.0",
        snapshot_id="snapshot-1",
        morphology={},
        sources=({"source_id": "source-1", "title": "Source standard"},),
        tasks=tuple(tasks),
        mjcf_path=root / "scene.xml",
        skeleton_dir=root / "skeleton",
        reference_driver=root / "reference" / "driver.py",
        private_dir=root / "private",
    )


def _design(package: RobotPackage) -> dict:
    capabilities = []
    for group in range(5):
        selected = package.tasks[group * 4 : group * 4 + 4]
        capabilities.append(
            {
                "capability_id": f"cap-{group}",
                "method_name": f"perform_{group}",
                "covered_task_ids": [task["task_id"] for task in selected],
                "validation_contract": [
                    {
                        "source_task_id": task["task_id"],
                        "source_clause_id": task["scoring"][0]["clause_id"],
                        **{
                            key: task["scoring"][0][key]
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
                    for task in selected
                ],
            }
        )
    return {"capabilities": capabilities}


def _private(package: RobotPackage) -> dict:
    instances = []
    bindings = []
    for index, task in enumerate(package.tasks):
        binding_id = f"binding-{index:02d}"
        instances.append(
            {
                "instance_id": f"instance-{index:02d}",
                "task_id": task["task_id"],
                "clause_bindings": {"terminal-error": binding_id},
                "guard_ids": ["actuator-step"],
                "repetitions": 1,
                "timeout_sim_s": 3.0,
            }
        )
        bindings.append(
            {
                "binding_id": binding_id,
                "metric": "terminal_error",
                "unit": "m",
                "observation": "trusted_terminal_error",
            }
        )
    return {
        "instances": {"instances": instances},
        "bindings": {"bindings": bindings},
        "guards": {
            "guards": [
                {
                    "guard_id": "actuator-step",
                    "kind": "actuator_and_physics_step_required",
                }
            ]
        },
    }


def _suite(package: RobotPackage, design: dict, private: dict) -> dict:
    task_capability = {
        task_id: capability
        for capability in design["capabilities"]
        for task_id in capability["covered_task_ids"]
    }
    cases = []
    for index, task in enumerate(package.tasks):
        capability = task_capability[task["task_id"]]
        source = capability["validation_contract"][index % 4]
        instance = private["instances"]["instances"][index]
        cases.append(
            {
                "case_id": f"case-{index:02d}",
                "capability_id": capability["capability_id"],
                "method_name": capability["method_name"],
                "task_id": task["task_id"],
                "source_clause_id": source["source_clause_id"],
                "instance_id": instance["instance_id"],
                "binding_id": instance["clause_bindings"]["terminal-error"],
                "guard_ids": instance["guard_ids"],
                "repetitions": instance["repetitions"],
                "timeout_sim_s": instance["timeout_sim_s"],
                "criterion": {
                    key: source[key]
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
    return {
        "artifact_type": "private_validation_suite",
        "schema_version": "1.0",
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": cases,
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


class IVCTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.package = _package(Path(self.temporary.name))
        self.design = _design(self.package)
        self.private = _private(self.package)
        self.suite = _suite(self.package, self.design, self.private)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_private_suite_passes_audit(self) -> None:
        result = validate_private_suite(
            self.suite,
            package=self.package,
            design=self.design,
            private_inputs=self.private,
        )

        self.assertEqual(len(result["cases"]), 20)

    def test_formal_suite_samples_exactly_five_cases_from_complete_pool(self) -> None:
        first = sample_private_suite(self.suite, seed="run-1:example-arm")
        repeated = sample_private_suite(self.suite, seed="run-1:example-arm")

        self.assertEqual(len(self.suite["cases"]), 20)
        self.assertEqual(len(first["cases"]), 5)
        self.assertEqual(first, repeated)
        self.assertEqual(
            first["selection"],
            {
                "kind": "uniform_without_replacement",
                "seed": "run-1:example-arm",
                "source_case_count": 20,
                "selected_case_count": 5,
                "selected_case_ids": [case["case_id"] for case in first["cases"]],
            },
        )

    def test_formal_suite_rejects_a_pool_smaller_than_five(self) -> None:
        too_small = {**self.suite, "cases": self.suite["cases"][:4]}

        with self.assertRaisesRegex(IVCError, "at least 5 cases"):
            sample_private_suite(too_small, seed="run-1:example-arm")

    def test_weaker_private_criterion_is_rejected(self) -> None:
        self.suite["cases"][0]["criterion"]["threshold"] = 0.2

        with self.assertRaisesRegex(IVCError, "changes a source pass standard"):
            validate_private_suite(
                self.suite,
                package=self.package,
                design=self.design,
                private_inputs=self.private,
            )

    def test_duplicate_source_clause_case_is_rejected(self) -> None:
        duplicate = dict(self.suite["cases"][0])
        duplicate["case_id"] = "duplicate-case"
        self.suite["cases"].append(duplicate)

        with self.assertRaisesRegex(IVCError, "exactly once"):
            validate_private_suite(
                self.suite,
                package=self.package,
                design=self.design,
                private_inputs=self.private,
            )

    def test_ivc_model_receives_no_candidate_implementation(self) -> None:
        model = _CapturingModel(self.suite)
        self.package.private_dir.mkdir(parents=True)
        import json

        for name, document in self.private.items():
            (self.package.private_dir / f"{name}.json").write_text(
                json.dumps(document), encoding="utf-8"
            )

        run_ivc(model, package=self.package, design=self.design)

        self.assertNotIn("driver", repr(model.inputs).lower())
        self.assertNotIn("candidate", repr(model.inputs).lower())
        self.assertEqual(model.inputs["task_library"]["sources"], list(self.package.sources))

    def test_one_structure_correction_still_receives_no_candidate(self) -> None:
        self.package.private_dir.mkdir(parents=True)
        import json

        for name, document in self.private.items():
            (self.package.private_dir / f"{name}.json").write_text(
                json.dumps(document), encoding="utf-8"
            )
        invalid = dict(self.suite)
        invalid["whole_suite_aggregation"] = {"kind": "any_case"}
        model = _SequenceModel([invalid, self.suite])

        result = run_ivc(model, package=self.package, design=self.design)

        self.assertEqual(len(result["cases"]), 20)
        self.assertEqual([call["stage"] for call in model.calls], [
            "ivc",
            "ivc-structure-correction",
        ])
        self.assertNotIn("driver", repr(model.calls).lower())
        self.assertNotIn("candidate", repr(model.calls).lower())


if __name__ == "__main__":
    unittest.main()
