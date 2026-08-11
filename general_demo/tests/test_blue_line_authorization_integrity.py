from __future__ import annotations

import pytest

from autoadapter2.blue_line import BlueLineRunner
from autoadapter2.foundation.canonical import canonical_bytes
from autoadapter2.foundation.errors import ContractError
from autoadapter2.foundation.hashing import content_hash
from autoadapter2.generation import FixtureJsonGenerator, Stage1Runner


G2 = {"profile_id": "g2-reusable-effect", "version": "1.0.0", "granularity": "G2"}
ROBOT = {
    "robot_model_id": "so-arm101",
    "robot_configuration_id": "so-arm101-follower-stock-gripper",
    "action_affordances": ["joint target command"],
    "observation_affordances": ["joint position observation"],
    "unit_allowlist": ["rad"],
    "frame_allowlist": ["joint"],
}
TASKS = [{"requirement_id": "req-reach", "description": "Reach a public joint target safely."}]
STANDARDS = {
    "snapshot_id": "standards-authorization-integrity",
    "standards": [{
        "standard_id": "joint-arrival",
        "measurement_id": "joint-error",
        "metric": "max_joint_error",
        "comparator": "<=",
        "threshold_value": 0.05,
        "dwell_s": 0.2,
        "timeout_s": 2.0,
        "aggregation": "ALL",
    }],
}
MEASUREMENTS = {
    "catalog_id": "measurements-authorization-integrity",
    "measurements": [{
        "measurement_id": "joint-error",
        "entity": "shoulder_pan",
        "unit": "rad",
        "frame": "joint",
        "adapter_id": "truth-joint-state",
        "truth_source": "physical_state",
        "metrics": ["max_joint_error"],
    }],
    "guards": [{"guard_id": "physical-state-not-command-receipt", "adapter_id": "truth-joint-state"}],
}
POLICY = {
    "policy_id": "blue-authorization-integrity",
    "model_id": "fixed-fixture",
    "prompt_id": "blue-prompt-authorization-integrity",
    "max_cases_per_capability": 1,
    "repetitions": 1,
}


def _design_body() -> dict:
    return {
        "capabilities": [{
            "capability_id": "reach-joint-target",
            "kind": "action",
            "requirement_ids": ["req-reach"],
            "inputs": [{
                "name": "target",
                "type": "number",
                "shape": "scalar",
                "unit": "rad",
                "frame": "joint",
                "required": True,
            }],
            "outputs": [],
            "effect": "The selected joint reaches the requested public target.",
            "preconditions": ["robot is connected"],
            "invocation_semantics": "Invoke once with a target joint value.",
            "temporal_semantics": "Returns after a bounded observation window.",
            "invariants": ["reports a public error instead of claiming unobserved success"],
            "required_action_affordances": ["joint target command"],
            "required_observation_affordances": ["joint position observation"],
            "errors": [{"code": "TARGET_REJECTED", "message": "Target cannot be accepted."}],
            "unsupported_scope": ["task-specific object manipulation"],
        }],
        "unsupported_requirement_ids": [],
        "blocking_requirement_ids": [],
    }


def _sealed_design(run_id: str) -> tuple[dict, dict]:
    result = Stage1Runner(FixtureJsonGenerator([_design_body()])).run(run_id, ROBOT, TASKS, G2)
    assert result.status == "SEALED" and result.capability_design and result.seal
    return result.capability_design, result.seal


def _ready(run_id: str = "run-authorization-integrity"):
    design, seal = _sealed_design(run_id)
    spec = {
        "capability_specs": [{
            "capability_id": "reach-joint-target",
            "measurement": {
                "measurement_id": "joint-error",
                "entity": "shoulder_pan",
                "unit": "rad",
                "frame": "joint",
            },
            "metric": "max_joint_error",
            "threshold": {"comparator": "<=", "value": 0.05},
            "dwell_s": 0.2,
            "timeout_s": 2.0,
            "aggregation": "ALL",
            "guard_ids": ["physical-state-not-command-receipt"],
            "cases": [{
                "case_id": "nominal",
                "initial_state": {"joint": 0.0},
                "inputs": {"target": 0.2},
            }],
            "lineage": {"kind": "COPIED", "standard_id": "joint-arrival", "material": False},
        }],
    }
    result = BlueLineRunner(FixtureJsonGenerator([spec])).run(
        design, seal, STANDARDS, MEASUREMENTS, POLICY
    )
    assert result.status == "READY"
    assert result.stage2_authorization is not None
    assert result.validation_authorization is not None
    return design, result


def test_ready_handles_return_the_exact_ready_lineage_without_exposing_mutable_authority() -> None:
    design, ready = _ready()
    design_hash = content_hash(canonical_bytes(design))

    stage2_payload = ready.stage2_authorization._verified_payload(design_hash)
    validation_payload = ready.validation_authorization._verified_payload(design_hash)

    assert stage2_payload == {
        "status": "READY",
        "authorized": True,
        "receipt_id": content_hash(canonical_bytes({
            "design_hash": design_hash,
            "blue_line_manifest_hash": ready.manifest_hash,
        })),
    }
    assert validation_payload == {
        "design_hash": design_hash,
        "spec_hash": ready.spec_hash,
        "manifest_hash": ready.manifest_hash,
        "suite_hash": ready.suite_hash,
    }

    stage2_payload["receipt_id"] = "forged"
    validation_payload["suite_hash"] = "forged"
    assert ready.stage2_authorization._verified_payload(design_hash)["receipt_id"] != "forged"
    assert ready.validation_authorization._verified_payload(design_hash)["suite_hash"] == ready.suite_hash


def test_object_setattr_cannot_rewrite_authoritative_ready_fields() -> None:
    design, ready = _ready()
    design_hash = content_hash(canonical_bytes(design))

    for field in ("_token", "_design_hash", "_receipt_id", "_payload"):
        with pytest.raises(AttributeError):
            object.__setattr__(ready.stage2_authorization, field, "forged")
    for field in ("_token", "_design_hash", "_spec_hash", "_manifest_hash", "_suite_hash", "_payload"):
        with pytest.raises(AttributeError):
            object.__setattr__(ready.validation_authorization, field, "forged")

    assert not hasattr(ready.stage2_authorization, "__dict__")
    assert not hasattr(ready.validation_authorization, "__dict__")
    assert ready.stage2_authorization._verified_payload(design_hash)["authorized"] is True
    assert ready.validation_authorization._verified_payload(design_hash)["suite_hash"] == ready.suite_hash


def test_ready_handles_reject_cross_design_replay() -> None:
    design, ready = _ready("run-authorization-source")
    other_design, _other_seal = _sealed_design("run-authorization-other")
    design_hash = content_hash(canonical_bytes(design))
    other_design_hash = content_hash(canonical_bytes(other_design))
    assert design_hash != other_design_hash

    with pytest.raises(ContractError, match="does not bind this Capability Design"):
        ready.stage2_authorization._verified_payload(other_design_hash)
    with pytest.raises(ContractError, match="does not bind this Capability Design"):
        ready.validation_authorization._verified_payload(other_design_hash)

    assert ready.stage2_authorization._verified_payload(design_hash)["authorized"] is True
    assert ready.validation_authorization._verified_payload(design_hash)["design_hash"] == design_hash
