from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from autoadapter2.validation_compiler import (
    IVCError,
    build_ivc_inputs,
    validate_capability_validation_suite,
)


def _fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    package = {
        "robot_configuration_id": "projection-test-robot",
        "package_version": "1.0.0",
        "task_snapshot_id": "projection-tasks-v1",
    }
    criterion = {
        "metric": "position_error",
        "unit": "m",
        "comparator": "<=",
        "threshold": 0.02,
        "temporal": {"kind": "terminal"},
        "aggregation": {"kind": "single_trial"},
        "source_refs": [
            {"source_id": "public-source", "specific_reference": "threshold"}
        ],
    }
    design = {
        "capabilities": [
            {
                "capability_id": "cap-a",
                "method_name": "move_target",
                "request_schema": {
                    "type": "object",
                    "properties": {"target_m": {"type": "number"}},
                    "required": ["target_m"],
                    "additionalProperties": False,
                },
                "criteria": [criterion],
            }
        ],
        "task_support": [
            {"task_id": "public-task-a", "capability_id": "cap-a", "rationale": "support"}
        ],
    }
    instances = []
    bindings = []
    guards = []
    for suffix in ("a", "b"):
        bindings.append(
            {
                "binding_id": f"binding-{suffix}",
                "metric": "position_error",
                "unit": "m",
                "kind": "terminal_position_error",
                "parameters": {
                    "site_name": f"site-{suffix}",
                    "target_argument": "request.task_parameters.target_m",
                },
            }
        )
        guards.append(
            {"guard_id": f"guard-{suffix}", "kind": "canonical_model_data"}
        )
        instances.append(
            {
                "instance_id": f"instance-{suffix}",
                "task_id": f"private-task-{suffix}",
                "source_case_id": f"source-case-{suffix}",
                "scene_entrypoint": f"assets/{suffix}.xml",
                "reset": {"kind": "default"},
                "public_arguments": {
                    "request": {
                        "task_id": f"private-task-{suffix}",
                        "task_parameters": {"target_m": 0.5},
                    }
                },
                "reference_arguments": {"request": {"target_m": 0.5}},
                "request_anchors": [
                    {
                        "anchor_id": f"anchor-{suffix}",
                        "request": {"target_m": 0.5},
                        "source_ref": "private exact request",
                    }
                ],
                "repetition_variants": [{"request": {"target_m": 0.9}}],
                "preinvoke": {"kind": "private prelude"},
                "framework_events": [{"kind": "private event"}],
                "task_plan": ["private macro step"],
                "clause_bindings": {"criterion": f"binding-{suffix}"},
                "guard_ids": [f"guard-{suffix}"],
                "repetitions": 1,
                "timeout_sim_s": 2.0,
            }
        )
    private = {
        "instances": {
            "calibration_namespace": "task",
            "instances": instances,
        },
        "bindings": {
            "calibration_namespace": "task",
            "bindings": bindings,
        },
        "guards": {
            "calibration_namespace": "task",
            "guards": guards,
        },
    }
    return package, design, private


def test_task_fallback_private_projection_hides_old_requests_and_rewrites_paths() -> None:
    package, design, private = _fixture()

    inputs = build_ivc_inputs(
        package=package,
        design=design,
        private_inputs=private,
    )

    assert {record["instance_id"] for record in inputs["private_instances"]["instances"]} == {
        "instance-a",
        "instance-b",
    }
    assert {record["binding_id"] for record in inputs["private_bindings"]["bindings"]} == {
        "binding-a",
        "binding-b",
    }
    assert {
        record["parameters"]["target_argument"]
        for record in inputs["private_bindings"]["bindings"]
    } == {"request.target_m"}
    private_projection = json.dumps(
        {
            "instances": inputs["private_instances"],
            "bindings": inputs["private_bindings"],
            "guards": inputs["private_guards"],
        }
    )
    for forbidden in (
        "task_id",
        "source_case_id",
        "task_parameters",
        "public_arguments",
        "reference_arguments",
        "request_anchors",
        "repetition_variants",
        "preinvoke",
        "framework_events",
        "task_plan",
    ):
        assert forbidden not in private_projection
    # Public task_support remains part of the sealed design; only the private
    # task execution envelopes are redacted.
    assert inputs["sealed_capability_design"]["task_support"] == design["task_support"]


def test_dedicated_private_projection_also_hides_exact_request_material() -> None:
    package, design, private = _fixture()
    for document in private.values():
        document["calibration_namespace"] = "capability"
    for binding in private["bindings"]["bindings"]:
        binding["parameters"].pop("target_argument")

    inputs = build_ivc_inputs(
        package=package,
        design=design,
        private_inputs=private,
    )

    projected = json.dumps(inputs["private_instances"])
    for forbidden in (
        "task_id",
        "source_case_id",
        "public_arguments",
        "reference_arguments",
        "request_anchors",
        "repetition_variants",
        "preinvoke",
        "framework_events",
        "task_plan",
    ):
        assert forbidden not in projected
    assert {record["instance_id"] for record in inputs["private_instances"]["instances"]} == {
        "instance-a",
        "instance-b",
    }


def test_task_fallback_rejects_an_unprojectable_binding_argument_path() -> None:
    package, design, private = _fixture()
    private["bindings"]["bindings"][0]["parameters"][
        "target_argument"
    ] = "request.private.target_m"

    with pytest.raises(IVCError, match="request.task_parameters.<field>"):
        build_ivc_inputs(
            package=package,
            design=design,
            private_inputs=private,
        )


def test_ivc_authors_distinct_requests_without_copying_private_anchor() -> None:
    package, design, private = _fixture()
    capability = design["capabilities"][0]
    cases = []
    for role, target in (("nominal", 0.25), ("calibrated_boundary", 0.75)):
        cases.append(
            {
                "case_id": f"cap-a-{role}",
                "case_role": role,
                "capability_id": "cap-a",
                "method_name": capability["method_name"],
                "instance_id": "instance-a",
                "binding_id": "binding-a",
                "guard_ids": ["guard-a"],
                "repetitions": 1,
                "timeout_sim_s": 2.0,
                "request": {"target_m": target},
                "criteria": copy.deepcopy(capability["criteria"]),
            }
        )
    suite = {
        "artifact_type": "capability_validation_suite",
        "schema_version": "2.0",
        "capability_protocol_version": "capability-v2",
        **package,
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": cases,
    }

    canonical = validate_capability_validation_suite(
        suite,
        package=package,
        design=design,
        private_inputs=private,
    )

    assert [case["request"] for case in canonical["cases"]] == [
        {"target_m": 0.25},
        {"target_m": 0.75},
    ]
    assert all(case["request"] != {"target_m": 0.5} for case in canonical["cases"])


def test_selected_task_binding_requires_its_native_field_in_authored_request() -> None:
    package, design, private = _fixture()
    capability = design["capabilities"][0]
    capability["request_schema"]["required"] = []
    cases = []
    for role, request in (
        ("nominal", {}),
        ("calibrated_boundary", {"target_m": 0.75}),
    ):
        cases.append(
            {
                "case_id": f"cap-a-{role}",
                "case_role": role,
                "capability_id": "cap-a",
                "method_name": capability["method_name"],
                "instance_id": "instance-a",
                "binding_id": "binding-a",
                "guard_ids": ["guard-a"],
                "repetitions": 1,
                "timeout_sim_s": 2.0,
                "request": request,
                "criteria": copy.deepcopy(capability["criteria"]),
            }
        )
    suite = {
        "artifact_type": "capability_validation_suite",
        "schema_version": "2.0",
        "capability_protocol_version": "capability-v2",
        **package,
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": cases,
    }

    with pytest.raises(IVCError, match="required by the selected task binding"):
        validate_capability_validation_suite(
            suite,
            package=package,
            design=design,
            private_inputs=private,
        )
