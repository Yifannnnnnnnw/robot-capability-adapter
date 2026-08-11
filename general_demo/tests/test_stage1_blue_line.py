from __future__ import annotations

import copy

import pytest

from autoadapter2.blue_line import BlueLineRunner
from autoadapter2.foundation.errors import ContractError
from autoadapter2.foundation.seals import verify_seal
from autoadapter2.generation import FixtureJsonGenerator, Stage1Config, Stage1Runner


G2 = {"profile_id": "g2-reusable-effect", "version": "1.0.0", "granularity": "G2"}
ROBOT = {"robot_model_id": "so-arm101", "robot_configuration_id": "so-arm101-follower-stock-gripper", "public_frames": ["base"]}
TASKS = [{"requirement_id": "req-reach", "description": "Reach a public joint target safely."}]
STANDARDS = {"snapshot_id": "standards-1", "standards": [{"standard_id": "joint-arrival"}]}
MEASUREMENTS = {
    "catalog_id": "measurements-1",
    "measurements": [{
        "measurement_id": "joint-error", "entity": "shoulder_pan", "unit": "rad",
        "frame": "joint", "truth_source": "physical_state",
    }],
}
POLICY = {"policy_id": "blue-1", "model_id": "fixed-fixture", "prompt_id": "blue-prompt-1", "max_cases_per_capability": 2}


def _capability_body() -> dict:
    return {
        "capabilities": [{
            "capability_id": "reach-joint-target",
            "kind": "action",
            "requirement_ids": ["req-reach"],
            "inputs": [{"name": "target", "type": "number", "shape": "scalar", "unit": "rad", "frame": "joint", "required": True}],
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


def _sealed_design() -> tuple[dict, dict]:
    result = Stage1Runner(FixtureJsonGenerator([_capability_body()])).run("run-1", ROBOT, TASKS, G2)
    assert result.status == "SEALED" and result.capability_design and result.seal
    return result.capability_design, result.seal


def _valid_spec(lineage: dict | None = None) -> dict:
    return {
        "capability_specs": [{
            "capability_id": "reach-joint-target",
            "measurement": {"measurement_id": "joint-error", "entity": "shoulder_pan", "unit": "rad", "frame": "joint"},
            "threshold": {"comparator": "<=", "value": 0.05},
            "dwell_s": 0.2,
            "timeout_s": 2.0,
            "aggregation": "ALL",
            "false_pass_guard": "Use measured joint state, not a command receipt.",
            "cases": [{"case_id": "nominal", "inputs": {"target": 0.2}}],
            "lineage": lineage or {"kind": "COPIED", "standard_id": "joint-arrival", "material": False},
        }],
    }


def test_stage1_rejects_private_input_before_any_model_call() -> None:
    fixture = FixtureJsonGenerator([_capability_body()])
    tasks = [{"requirement_id": "req-reach", "description": "Reach.", "private_criterion": "secret"}]
    with pytest.raises(ContractError):
        Stage1Runner(fixture).run("run-1", ROBOT, tasks, G2)
    assert fixture.calls == []


def test_stage1_rejects_framework_field_override_then_corrects_publicly() -> None:
    invalid = _capability_body() | {"run_id": "attacker-run"}
    fixture = FixtureJsonGenerator([invalid, _capability_body()])
    result = Stage1Runner(fixture, Stage1Config(max_correction_calls=2)).run("run-1", ROBOT, TASKS, G2)
    assert result.status == "SEALED"
    assert len(result.call_log) == 2
    assert result.capability_design["run_id"] == "run-1"
    assert "diagnostics" in fixture.calls[1]["inputs"]


def test_blue_line_requires_every_capability_and_caps_corrections_at_three_calls() -> None:
    design, seal = _sealed_design()
    missing_coverage = {"capability_specs": []}
    fixture = FixtureJsonGenerator([missing_coverage, missing_coverage, missing_coverage])
    result = BlueLineRunner(fixture).run(design, seal, STANDARDS, MEASUREMENTS, POLICY)
    assert result.status == "NEEDS_REVIEW"
    assert result.validation_suite is None
    assert len(result.call_log) == 3
    assert len(fixture.calls) == 3
    assert set(fixture.calls[0]["inputs"]) == {"capability_design", "standards_snapshot", "measurement_catalog", "policy"}


def test_proposed_or_materially_adapted_lineage_needs_review_without_suite() -> None:
    design, seal = _sealed_design()
    proposed = _valid_spec({"kind": "PROPOSED", "standard_id": None, "material": True})
    result = BlueLineRunner(FixtureJsonGenerator([proposed])).run(design, seal, STANDARDS, MEASUREMENTS, POLICY)
    assert result.status == "NEEDS_REVIEW"
    assert result.manifest["reason_code"] == "PENDING_STANDARD_REVIEW"
    assert result.manifest["suite_hash"] is None
    assert result.validation_suite is None

    adapted = _valid_spec({"kind": "ADAPTED", "standard_id": "joint-arrival", "material": True})
    result = BlueLineRunner(FixtureJsonGenerator([adapted])).run(design, seal, STANDARDS, MEASUREMENTS, POLICY)
    assert result.status == "NEEDS_REVIEW"
    assert result.validation_suite is None


def test_ready_suite_and_all_seals_are_deterministic() -> None:
    design, seal = _sealed_design()
    first = BlueLineRunner(FixtureJsonGenerator([_valid_spec()])).run(design, seal, STANDARDS, MEASUREMENTS, POLICY)
    second = BlueLineRunner(FixtureJsonGenerator([copy.deepcopy(_valid_spec())])).run(design, seal, STANDARDS, MEASUREMENTS, POLICY)
    assert first.status == second.status == "READY"
    assert first.suite_hash == second.suite_hash
    assert first.manifest_hash == second.manifest_hash
    assert first.validation_suite == second.validation_suite
    assert verify_seal(first.spec_seal)
    assert verify_seal(first.suite_seal)
    assert verify_seal(first.manifest_seal)
