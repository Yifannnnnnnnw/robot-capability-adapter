from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from autoadapter2.capability_design import (
    CAPABILITY_INVOCATION_ABI,
    CAPABILITY_PROTOCOL_VERSION,
)
from autoadapter2.libraries import load_robot_package
from autoadapter2.validation_compiler import (
    build_ivc_inputs,
    validate_capability_validation_suite,
)
from autoadapter2.validation_compiler.ivc import (
    IVCError,
    _private_inputs_from_package,
)


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = (
    ROOT
    / "autoadapter"
    / "libraries"
    / "robots"
    / "unitree-go2-stock-12dof"
    / "1.0.0"
)
REFERENCE_PATH = ROOT / "autoadapter" / "references" / "capability_v2" / "go2.json"


AUTHORED_REQUESTS: dict[tuple[str, str], dict[str, Any]] = {
    ("G1", "nominal"): {
        "linear_velocity_body_m_s": [0.12, 0.04],
        "yaw_rate_rad_s": 0.1,
        "duration_s": 2.4,
    },
    ("G1", "calibrated_boundary"): {
        "linear_velocity_body_m_s": [-0.18, 0.08],
        "yaw_rate_rad_s": -0.4,
        "duration_s": 3.4,
    },
    ("G2", "nominal"): {
        "translation_initial_yaw_m": [0.05, 0.02],
        "yaw_delta_rad": 0.08,
        "max_duration_s": 4.5,
    },
    ("G2", "calibrated_boundary"): {
        "translation_initial_yaw_m": [-0.07, 0.03],
        "yaw_delta_rad": -0.2,
        "max_duration_s": 6.5,
    },
    ("G3", "nominal"): {
        "waypoints_initial_yaw_m": [[0.03, 0.01], [0.08, 0.03]],
        "max_duration_s": 4.5,
    },
    ("G3", "calibrated_boundary"): {
        "waypoints_initial_yaw_m": [[-0.02, 0.03], [-0.06, 0.08]],
        "max_duration_s": 6.5,
    },
    ("G4", "nominal"): {
        "target_height_m": 0.27,
        "max_duration_s": 3.3,
    },
    ("G4", "calibrated_boundary"): {
        "target_height_m": 0.34,
        "max_duration_s": 5.0,
    },
    ("G5", "nominal"): {"duration_s": 1.1},
    ("G5", "calibrated_boundary"): {"duration_s": 1.9},
}


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(str(key) for key in value)
        for child in value.values():
            keys.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_walk_keys(child))
    return keys


def _design(package: Any) -> dict[str, Any]:
    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    capabilities = copy.deepcopy(reference["capabilities"])
    for index, capability in enumerate(capabilities, start=1):
        capability["capability_id"] = f"fresh_capability_{index}"
    return {
        "artifact_type": "capability_design",
        "schema_version": "2.0",
        "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
        "invocation_abi": CAPABILITY_INVOCATION_ABI,
        "capabilities": capabilities,
        "task_support": [],
    }


def _suite(package: Any, design: dict[str, Any], private: dict[str, Any]) -> dict[str, Any]:
    instances = {
        (record["calibration_profile"], record["case_role"]): record
        for record in private["instances"]["instances"]
    }
    bindings_by_metric = {
        record["metric"]: record for record in private["bindings"]["bindings"]
    }
    cases = []
    for capability in design["capabilities"]:
        capability_id = capability["capability_id"]
        binding = bindings_by_metric[capability["criteria"][0]["metric"]]
        profile = binding["calibration_profile"]
        for role in ("nominal", "calibrated_boundary"):
            instance = instances[(profile, role)]
            cases.append(
                {
                    "case_id": f"authored-{capability_id.lower()}-{role}",
                    "case_role": role,
                    "capability_id": capability_id,
                    "method_name": capability["method_name"],
                    "request": copy.deepcopy(AUTHORED_REQUESTS[(profile, role)]),
                    "instance_id": instance["instance_id"],
                    "binding_id": binding["binding_id"],
                    "guard_ids": list(instance["guard_ids"]),
                    "repetitions": instance["repetitions"],
                    "timeout_sim_s": instance["timeout_sim_s"],
                    "criteria": copy.deepcopy(capability["criteria"]),
                }
            )
    return {
        "artifact_type": "capability_validation_suite",
        "schema_version": "2.0",
        "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": cases,
    }


def test_indexed_go2_capability_context_loads_without_task_envelopes() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    private = _private_inputs_from_package(package)

    assert private["instances"]["calibration_namespace"] == "capability"
    instances = private["instances"]["instances"]
    bindings = private["bindings"]["bindings"]
    assert len(instances) == 10
    assert len(bindings) == 5
    assert not _walk_keys(private).intersection(
        {
            "capability_id",
            "source_case_id",
            "task_id",
            "task_parameters",
            "public_arguments",
            "reference_arguments",
            "request_anchors",
        }
    )

    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    reference_by_profile = {
        capability["capability_id"]: capability
        for capability in reference["capabilities"]
    }
    expected_metric_units = {
        profile: (
            capability["criteria"][0]["metric"],
            capability["criteria"][0]["unit"],
        )
        for profile, capability in reference_by_profile.items()
    }
    assert {
        binding["calibration_profile"]: (binding["metric"], binding["unit"])
        for binding in bindings
    } == expected_metric_units
    assert {
        (instance["calibration_profile"], instance["case_role"])
        for instance in instances
    } == {
        (profile, role)
        for profile in expected_metric_units
        for role in ("nominal", "calibrated_boundary")
    }
    assert all(
        instance["request_domain"]
        == reference_by_profile[instance["calibration_profile"]]["request_schema"]
        for instance in instances
    )

    model_inputs = build_ivc_inputs(
        package=package,
        design=design,
        private_inputs=private,
    )
    assert not _walk_keys(model_inputs["private_instances"]).intersection(
        {
            "capability_id",
            "source_case_id",
            "task_id",
            "task_parameters",
            "public_arguments",
            "reference_arguments",
            "request_anchors",
        }
    )


def test_go2_ivc_accepts_authored_requests_and_rejects_out_of_schema_request() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _design(package)
    private = _private_inputs_from_package(package)
    suite = _suite(package, design, private)

    canonical = validate_capability_validation_suite(
        suite,
        package=package,
        design=design,
        private_inputs=private,
    )
    assert [case["request"] for case in canonical["cases"]] == [
        case["request"] for case in suite["cases"]
    ]

    invalid = copy.deepcopy(suite)
    next(
        case for case in invalid["cases"] if "target_height_m" in case["request"]
    )["request"]["target_height_m"] = 0.5
    with pytest.raises(IVCError, match="maximum"):
        validate_capability_validation_suite(
            invalid,
            package=package,
            design=design,
            private_inputs=private,
        )
