#!/usr/bin/env python3
"""Build the complete SO-101/Go2 IVC worked-reference bank."""

from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    ROOT
    / "autoadapter"
    / "references"
    / "capability_v2"
    / "ivc_worked_references.json"
)
CONFIGURATIONS = {
    "robotstudio_so101": {
        "design": ROOT / "autoadapter/references/capability_v2/so101.json",
        "package": ROOT / "autoadapter/libraries/robots/robotstudio_so101/1.0.4",
        "fixed_suite": ROOT
        / "experiment/experiment1a_generation/validation/fixed_validation_bundles"
        / "robotstudio_so101/capability_validation_suite.json",
        "instance_prefix": "so101",
        "operators": {
            "A1": "so101_end_effector_regulation",
            "A2": "so101_cartesian_path_tracking",
            "A3": "so101_gripper_aperture_regulation",
            "A4": "so101_controlled_contact_approach",
            "A5": "so101_outbound_return_motion",
            "A6": "so101_wrist_roll_regulation",
        },
    },
    "unitree-go2-stock-12dof": {
        "design": ROOT / "autoadapter/references/capability_v2/go2.json",
        "package": ROOT
        / "autoadapter/libraries/robots/unitree-go2-stock-12dof/1.0.0",
        "fixed_suite": ROOT
        / "experiment/experiment1a_generation/validation/fixed_validation_bundles"
        / "unitree-go2-stock-12dof/capability_validation_suite.json",
        "instance_prefix": "go2",
        "operators": {
            "G1": "go2_planar_twist_tracking",
            "G2": "go2_relative_pose_motion",
            "G3": "go2_planar_path_tracking",
            "G4": "go2_body_height_regulation",
            "G5": "go2_stable_stance_recovery",
        },
    },
}


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _evidence_refs(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            refs = node.get("evidence_refs")
            if isinstance(refs, list):
                for ref in refs:
                    if not isinstance(ref, Mapping):
                        continue
                    pair = (ref.get("source_id"), ref.get("specific_reference"))
                    if not all(isinstance(item, str) and item for item in pair):
                        continue
                    typed = (str(pair[0]), str(pair[1]))
                    if typed not in seen:
                        seen.add(typed)
                        result.append(
                            {
                                "source_id": typed[0],
                                "specific_reference": typed[1],
                            }
                        )
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    if not result:
        raise ValueError("reference request schema has no evidence refs")
    return result


def _records(path: Path, field: str, id_field: str) -> dict[str, dict[str, Any]]:
    document = _read(path)
    records = document.get(field)
    if not isinstance(records, list):
        raise ValueError(f"{path} has no {field}[]")
    return {
        str(record[id_field]): record
        for record in records
        if isinstance(record, dict) and isinstance(record.get(id_field), str)
    }


def _build_reference(configuration: str, config: Mapping[str, Any]) -> dict[str, Any]:
    design = _read(Path(config["design"]))
    fixed_suite = _read(Path(config["fixed_suite"]))
    package_root = Path(config["package"])
    instance_document = _read(
        package_root / "capability_validation/private/instances.json"
    )
    instances = _records(
        package_root / "capability_validation/private/instances.json",
        "instances",
        "instance_id",
    )
    bindings = _records(
        package_root / "capability_validation/private/bindings.json",
        "bindings",
        "binding_id",
    )
    fixed_by_key = {
        (case["capability_id"], case["case_variant"]): case
        for case in fixed_suite["cases"]
    }
    binding_by_profile = {
        binding["calibration_profile"]: binding for binding in bindings.values()
    }
    cases: list[dict[str, Any]] = []
    for capability in design["capabilities"]:
        capability_id = capability["capability_id"]
        for role, variant in (("nominal", "H1"), ("calibrated_boundary", "H3")):
            instance_id = (
                f"{config['instance_prefix']}-{capability_id.lower()}-"
                + ("nominal" if role == "nominal" else "calibrated-boundary")
            )
            instance = instances[instance_id]
            fixed_case = fixed_by_key[(capability_id, variant)]
            old_binding = binding_by_profile[capability_id]
            parameters = copy.deepcopy(old_binding["parameters"])
            parameters.pop("contract_id", None)
            cases.append(
                {
                    "case_id": f"{configuration}-{capability_id.lower()}-{role}",
                    "case_role": role,
                    "capability_id": capability_id,
                    "method_name": capability["method_name"],
                    "request": copy.deepcopy(fixed_case["request"]),
                    "request_grounding_refs": _evidence_refs(
                        capability["request_schema"]
                    ),
                    "instance_id": instance_id,
                    "measurement_binding": {
                        "metric": capability["criteria"][0]["metric"],
                        "unit": capability["criteria"][0]["unit"],
                        "kind": config["operators"][capability_id],
                        "parameters": parameters,
                    },
                    "guard_ids": copy.deepcopy(instance["guard_ids"]),
                    "repetitions": instance["repetitions"],
                    "timeout_sim_s": instance["timeout_sim_s"],
                    "criteria": copy.deepcopy(capability["criteria"]),
                }
            )
    reference_design = copy.deepcopy(design)
    reference_design.pop("task_support", None)
    return {
        "reference_id": f"{configuration}-complete-capability-v2",
        "description": (
            "Complete worked reference: capability contracts, one nominal and one "
            "calibrated-boundary request per capability, semantic trusted inline "
            "measurement operators, exact criteria, scenes, and guards. It contains "
            "no task_support relation or task execution plan."
        ),
        "capability_design": reference_design,
        "validation_suite": {
            "artifact_type": "capability_validation_suite",
            "schema_version": "2.0",
            "capability_protocol_version": "capability-v2",
            "robot_configuration_id": design["robot_configuration_id"],
            "package_version": design["package_version"],
            "task_snapshot_id": instance_document["task_snapshot_id"],
            "whole_suite_aggregation": {"kind": "all_cases"},
            "cases": cases,
        },
        "source_lineage": {
            "design": str(Path(config["design"]).relative_to(ROOT)),
            "fixed_request_calibration": str(
                Path(config["fixed_suite"]).relative_to(ROOT)
            ),
            "private_scene_guard_calibration": str(
                package_root.relative_to(ROOT) / "capability_validation/private"
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    document = {
        "artifact_type": "ivc_worked_reference_bank",
        "schema_version": "2.0",
        "description": (
            "Complete SO-101 six-capability and Go2 five-capability authoring "
            "references. These are examples, never selectable hidden cases."
        ),
        "worked_references": [
            _build_reference(configuration, config)
            for configuration, config in CONFIGURATIONS.items()
        ],
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
