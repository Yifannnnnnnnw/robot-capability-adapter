from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest


DEMO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DEMO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture
def stage1_artifact() -> dict[str, Any]:
    def layer(
        capability_id: str,
        function_name: str,
        validation_effect: str,
    ) -> dict[str, Any]:
        granularity = capability_id.split(".", 1)[0]
        implementation_family = {
            "G1": "joint_motion",
            "G2": "cartesian_motion",
            "G3": "pick_place",
        }[granularity]
        evidence_basis = {
            "G1": "pinned_runtime_contract",
            "G2": "compiled_kinematics",
            "G3": "verified_physical_baseline",
        }[granularity]
        return {
            "rationale": "A sufficiently detailed rationale for the P0 test.",
            "capabilities": [
                {
                    "capability_id": capability_id,
                    "function_name": function_name,
                    "intended_outcome": "Produce a deterministic and measurable robot state transition.",
                    "validation_effect": validation_effect,
                    "implementation_family": implementation_family,
                    "implementation_evidence": {
                        "status": "verified",
                        "basis": evidence_basis,
                        "refs": ["fixture://stage1"],
                    },
                }
            ],
        }

    return {
        "schema_version": "robot_capability.stage1.v2",
        "target": {
            "robot_id": "soarm101",
            "runtime_id": "lerobot_soarm101_0_6_0",
        },
        "layers": {
            "G1": layer("G1.command_joint", "command_joint", "joint_targets"),
            "G2": layer("G2.move_to_pose", "move_to_pose", "cartesian_target"),
            "G3": layer(
                "G3.pick_and_place",
                "pick_and_place",
                "object_source_to_target",
            ),
        },
        "assumptions": [],
        "unresolved_evidence_gaps": [],
        "evidence_refs": ["fixture://stage1"],
    }


@pytest.fixture
def public_api_manifest() -> dict[str, Any]:
    def capability(
        capability_id: str,
        granularity: str,
        module: str,
        function_name: str,
        public_parameters: list[str],
        validation_binding: dict[str, Any],
    ) -> dict[str, Any]:
        required_result_fields = ["status"]
        if granularity == "G3":
            required_result_fields.append("phase_reached")
        if validation_binding.get("effect") == "object_move_sequence":
            required_result_fields.extend(
                ["completed_moves", "failed_move_index", "timeout_scope"]
            )
        return {
            "capability_id": capability_id,
            "granularity": granularity,
            "module": module,
            "function_name": function_name,
            "signature": {
                "parameters": [
                    {
                        "name": "runtime",
                        "kind": "POSITIONAL_OR_KEYWORD",
                        "annotation": "object",
                        "has_default": False,
                        "default": None,
                    },
                    *[
                        {
                            "name": public_parameter,
                            "kind": "POSITIONAL_OR_KEYWORD",
                            "annotation": "float",
                            "has_default": False,
                            "default": None,
                        }
                        for public_parameter in public_parameters
                    ],
                ],
                "return_annotation": "dict[str, object]",
            },
            "validation_binding": validation_binding,
            "result_contract": {
                "type": "object",
                "required": required_result_fields,
                "status_values": ["ok", "error"],
                "success_status_values": ["ok"],
            },
        }

    return {
        "schema_version": "robot_capability.package_manifest.v2",
        "stage1_sha256": "fixture",
        "package": "generated_capability_package",
        "capabilities": [
            capability(
                "G1.command_joint",
                "G1",
                "g1",
                "command_joint",
                ["position"],
                {
                    "effect": "joint_targets",
                    "joint_target_arguments": {"shoulder_pan.pos": "position"},
                },
            ),
            capability(
                "G2.move_to_pose",
                "G2",
                "g2",
                "move_to_pose",
                ["target"],
                {"effect": "cartesian_target", "target_argument": "target"},
            ),
            capability(
                "G3.pick_and_place",
                "G3",
                "g3",
                "pick_and_place",
                ["source", "target"],
                {
                    "effect": "object_source_to_target",
                    "source_argument": "source",
                    "target_argument": "target",
                    "target_components": "xyz",
                },
            ),
        ],
    }
