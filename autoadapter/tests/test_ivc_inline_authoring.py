from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from autoadapter2.libraries import RobotPackage, load_robot_package
from autoadapter2.validation_compiler.ivc import (
    IVCError,
    _private_inputs_from_package,
    build_ivc_inputs,
    load_sanitized_ivc_examples,
    validate_capability_validation_suite,
)


ROOT = Path(__file__).resolve().parents[2]
ROBOT_ROOT = ROOT / "autoadapter" / "libraries" / "robots"


def _synthetic(
    tmp_path: Path,
) -> tuple[RobotPackage, dict[str, Any], dict[str, Any], dict[str, Any]]:
    package_root = tmp_path / "package"
    scene = package_root / "assets" / "scene.xml"
    scene.parent.mkdir(parents=True)
    scene.write_text(
        "<mujoco><worldbody><body name='tool'><site name='tool_site'/></body>"
        "</worldbody></mujoco>",
        encoding="utf-8",
    )
    package = RobotPackage(
        root=package_root,
        robot_configuration_id="novel-arm",
        package_version="1.0.0",
        snapshot_id="novel-tasks-v1",
        morphology={"mjcf_entrypoint": "assets/scene.xml"},
        sources=(),
        tasks=(),
        mjcf_path=scene,
        skeleton_dir=package_root / "skeleton",
        reference_driver=package_root / "reference" / "driver.py",
        private_dir=package_root / "tasks" / "private",
    )
    evidence_ref = {
        "source_id": "real-calibration",
        "specific_reference": "target_m bounds from admitted scene calibration",
    }
    criterion = {
        "metric": "novel_tip_error",
        "unit": "m",
        "comparator": "<=",
        "threshold": 0.02,
        "temporal": {"kind": "terminal_state"},
        "aggregation": {"kind": "single_trial"},
        "source_refs": [copy.deepcopy(evidence_ref)],
    }
    design = {
        "capabilities": [
            {
                "capability_id": "N1",
                "method_name": "move_novel_tip",
                "request_schema": {
                    "type": "object",
                    "properties": {
                        "target_m": {
                            "type": "array",
                            "minItems": 3,
                            "maxItems": 3,
                            "items": {
                                "type": "number",
                                "minimum": -1.0,
                                "maximum": 1.0,
                                "evidence_refs": [copy.deepcopy(evidence_ref)],
                            },
                        }
                    },
                    "required": ["target_m"],
                    "additionalProperties": False,
                },
                "criteria": [criterion],
            }
        ],
        "task_support": [],
    }
    guards = [
        {"guard_id": "control", "kind": "actuator_and_physics_step_required"},
        {"guard_id": "state", "kind": "no_direct_state_write"},
        {"guard_id": "canonical", "kind": "canonical_model_data"},
    ]
    instance = {
        "instance_id": "novel-scene",
        "context_namespace": "capability",
        "scene_entrypoint": "assets/scene.xml",
        "reset": {"kind": "default"},
        "guard_ids": [guard["guard_id"] for guard in guards],
        "repetitions": 1,
        "timeout_sim_s": 2.0,
    }
    private = {
        "instances": {
            "calibration_namespace": "capability",
            "instances": [instance],
        },
        "bindings": {
            "calibration_namespace": "capability",
            "bindings": [
                {
                    "binding_id": "example-only",
                    "kind": "final_site_position_error",
                    "metric": "example_metric",
                    "unit": "m",
                    "parameters": {
                        "site_name": "tool_site",
                        "target_argument": "request.target_m",
                    },
                }
            ],
        },
        "guards": {
            "calibration_namespace": "capability",
            "guards": guards,
        },
    }
    cases = []
    for role, target in (
        ("nominal", [0.1, 0.0, 0.1]),
        ("calibrated_boundary", [1.0, 0.0, 0.1]),
    ):
        cases.append(
            {
                "case_id": f"novel-{role}",
                "case_role": role,
                "capability_id": "N1",
                "method_name": "move_novel_tip",
                "request": {"target_m": target},
                "request_grounding_refs": [copy.deepcopy(evidence_ref)],
                "instance_id": "novel-scene",
                "measurement_binding": {
                    "metric": "novel_tip_error",
                    "unit": "m",
                    "kind": "final_site_position_error",
                    "parameters": {
                        "site_name": "tool_site",
                        "target_argument": "request.target_m",
                    },
                },
                "guard_ids": list(instance["guard_ids"]),
                "repetitions": 1,
                "timeout_sim_s": 2.0,
                "criteria": [copy.deepcopy(criterion)],
            }
        )
    suite = {
        "artifact_type": "capability_validation_suite",
        "schema_version": "2.0",
        "capability_protocol_version": "capability-v2",
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": cases,
    }
    return package, design, private, suite


def test_novel_capability_seals_ivc_authored_inline_measurement(tmp_path: Path) -> None:
    package, design, private, suite = _synthetic(tmp_path)

    canonical = validate_capability_validation_suite(
        suite,
        package=package,
        design=design,
        private_inputs=private,
    )

    assert [case["request"] for case in canonical["cases"]] == [
        {"target_m": [0.1, 0.0, 0.1]},
        {"target_m": [1.0, 0.0, 0.1]},
    ]
    assert all("binding_id" not in case for case in canonical["cases"])
    assert {
        case["measurement_binding"]["kind"] for case in canonical["cases"]
    } == {"final_site_position_error"}


def test_dynamic_suite_rejects_historical_binding_id(tmp_path: Path) -> None:
    package, design, private, suite = _synthetic(tmp_path)
    suite["cases"][0]["binding_id"] = "example-only"

    with pytest.raises(IVCError, match="forbidden field 'binding_id'"):
        validate_capability_validation_suite(
            suite,
            package=package,
            design=design,
            private_inputs=private,
        )


def test_build_inputs_exposes_catalog_scenes_and_examples_not_binding_selection(
    tmp_path: Path,
) -> None:
    package, design, private, _suite = _synthetic(tmp_path)

    inputs = build_ivc_inputs(
        package=package,
        design=design,
        private_inputs=private,
    )

    assert "private_bindings" not in inputs
    assert inputs["validator_contract"]["binding_id_is_forbidden"] is True
    assert inputs["trusted_measurement_examples"]["bindings"][0]["example_id"] == (
        "example-only"
    )
    assert "binding_id" not in json.dumps(inputs["trusted_measurement_examples"])
    scene = inputs["scene_entity_catalog"]["scenes"][0]
    assert scene["entities"]["sites"] == ["tool_site"]
    assert {item["kind"] for item in inputs["measurement_operator_catalog"]["operators"]} >= {
        "final_site_position_error",
        "go2_stable_stance_recovery",
    }


def test_complete_so101_go2_references_pass_real_operator_audit() -> None:
    references = load_sanitized_ivc_examples()
    index = json.loads((ROBOT_ROOT / "index.json").read_text(encoding="utf-8"))[
        "robots"
    ]
    counts: list[int] = []
    for reference in references:
        suite = reference["validation_suite"]
        package = load_robot_package(ROBOT_ROOT / index[suite["robot_configuration_id"]])
        canonical = validate_capability_validation_suite(
            suite,
            package=package,
            design=reference["capability_design"],
        )
        counts.append(len(canonical["cases"]))
        assert "binding_id" not in json.dumps(canonical)
        assert "task_support" not in reference["capability_design"]
    assert counts == [12, 10]


def test_go2_ivc_context_includes_all_task_scenes_beside_five_references() -> None:
    package = load_robot_package(
        ROBOT_ROOT / "unitree-go2-stock-12dof" / "1.0.0"
    )
    private = _private_inputs_from_package(package)
    design = json.loads(
        (ROOT / "autoadapter/references/capability_v2/go2.json").read_text(
            encoding="utf-8"
        )
    )
    inputs = build_ivc_inputs(
        package=package,
        design=design,
        private_inputs=private,
    )

    instances = inputs["private_instances"]["instances"]
    assert len(instances) == 30
    assert sum(item["context_namespace"] == "task" for item in instances) == 20
    assert sum(item["context_namespace"] == "capability" for item in instances) == 10
    assert len(inputs["trusted_measurement_examples"]["bindings"]) == 27
    serialized = json.dumps(inputs["private_instances"])
    for forbidden in (
        "task_id",
        "public_arguments",
        "reference_arguments",
        "preinvoke",
        "framework_events",
        "task_plan",
        "clause_bindings",
    ):
        assert forbidden not in serialized
