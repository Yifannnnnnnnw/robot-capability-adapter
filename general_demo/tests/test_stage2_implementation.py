from __future__ import annotations

import copy

import pytest

from autoadapter2.blue_line import BlueLineRunner
from autoadapter2.foundation.canonical import canonical_bytes
from autoadapter2.foundation.errors import ContractError
from autoadapter2.foundation.hashing import content_hash
from autoadapter2.foundation.seals import verify_seal
from autoadapter2.generation import FixtureJsonGenerator, Stage1Runner
from autoadapter2.implementation import (
    CallbackSandbox,
    Stage2Config,
    Stage2Runner,
    derive_python_binding,
    validate_implementation_bundle,
)


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
    "snapshot_id": "standards-stage2",
    "standards": [{
        "standard_id": "joint-arrival", "measurement_id": "joint-error",
        "metric": "max_joint_error", "comparator": "<=", "threshold_value": 0.05,
        "dwell_s": 0.2, "timeout_s": 2.0, "aggregation": "ALL",
    }],
}
MEASUREMENTS = {
    "catalog_id": "measurements-stage2",
    "measurements": [{
        "measurement_id": "joint-error", "entity": "shoulder_pan", "unit": "rad",
        "frame": "joint", "adapter_id": "truth-joint-state",
        "truth_source": "physical_state", "metrics": ["max_joint_error"],
    }],
    "guards": [{"guard_id": "physical-state-not-command-receipt", "adapter_id": "truth-joint-state"}],
}
POLICY = {
    "policy_id": "blue-stage2", "model_id": "fixed-fixture", "prompt_id": "blue-prompt-1",
    "max_cases_per_capability": 1, "repetitions": 1,
}
IMPLEMENTATION_BUNDLE = {
    "artifact_type": "stage2_implementation_bundle",
    "schema_version": "1.0.0",
    "sdk_implementation_projection": {
        "sdk_entry_id": "lerobot-so-arm101",
        "members": ["command"],
    },
    "robot_implementation_facts": {
        "joint_order": ["shoulder_pan"],
        "position_unit": "rad",
    },
    "implementation_experience": [
        {"experience_id": "so-arm101-command-v1", "guidance": "Use the pinned command member."},
    ],
}


def _bundle() -> dict:
    return copy.deepcopy(IMPLEMENTATION_BUNDLE)


def _design_body() -> dict:
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


def _sealed_design(run_id: str = "run-stage2") -> tuple[dict, dict]:
    result = Stage1Runner(FixtureJsonGenerator([_design_body()])).run(run_id, ROBOT, TASKS, G2)
    assert result.status == "SEALED" and result.capability_design and result.seal
    return result.capability_design, result.seal


def _authorization(design: dict, seal: dict):
    spec = {
        "capability_specs": [{
            "capability_id": "reach-joint-target",
            "measurement": {"measurement_id": "joint-error", "entity": "shoulder_pan", "unit": "rad", "frame": "joint"},
            "metric": "max_joint_error",
            "threshold": {"comparator": "<=", "value": 0.05},
            "dwell_s": 0.2,
            "timeout_s": 2.0,
            "aggregation": "ALL",
            "guard_ids": ["physical-state-not-command-receipt"],
            "cases": [{"case_id": "nominal", "initial_state": {"joint": 0.0}, "inputs": {"target": 0.2}}],
            "lineage": {"kind": "COPIED", "standard_id": "joint-arrival", "material": False},
        }],
    }
    result = BlueLineRunner(FixtureJsonGenerator([spec])).run(
        design, seal, STANDARDS, MEASUREMENTS, POLICY
    )
    assert result.status == "READY" and result.stage2_authorization is not None
    return result.stage2_authorization


def test_binding_is_deterministic_and_one_to_one() -> None:
    design, seal = _sealed_design()
    first = derive_python_binding(design, seal)
    second = derive_python_binding(copy.deepcopy(design), copy.deepcopy(seal))

    assert first.contract == second.contract
    assert first.contract_hash == second.contract_hash
    assert first.starter_skeleton == second.starter_skeleton
    assert first.contract["bindings"] == [{
        "capability_id": "reach-joint-target",
        "function_name": "capability_reach_joint_target",
        "parameters": [{"public_name": "target", "parameter": "arg_target"}],
        "result_envelope": {"kind": "mapping", "fields": []},
    }]
    assert "def capability_reach_joint_target(arg_target, *, _sdk):" in first.starter_skeleton
    assert verify_seal(first.contract_seal)


def test_stage2_requires_non_sensitive_ready_authorization() -> None:
    design, seal = _sealed_design()
    fixture = FixtureJsonGenerator([{"action": "blocked", "reason": "The public SDK method is unavailable."}])
    with pytest.raises(ContractError):
        Stage2Runner(fixture).run(design, seal, False, _bundle())
    with pytest.raises(ContractError):
        Stage2Runner(fixture).run(design, seal, {"authorized": True, "suite_hash": "sha256:" + "0" * 64}, _bundle())
    assert fixture.calls == []

    other_design, other_seal = _sealed_design("other-run")
    with pytest.raises(ContractError):
        Stage2Runner(fixture).run(design, seal, _authorization(other_design, other_seal), _bundle())
    result = Stage2Runner(fixture).run(design, seal, _authorization(design, seal), _bundle())
    assert result.status == "IMPLEMENTATION_BLOCKED"
    assert result.blocked_reason == "The public SDK method is unavailable."
    assert set(fixture.calls[0]["inputs"]) == {
        "capability_design", "binding_contract", "starter_skeleton", "blue_line_authorization",
        "implementation_bundle",
    }
    assert fixture.calls[0]["inputs"]["implementation_bundle"] == IMPLEMENTATION_BUNDLE


def test_stage2_caps_accounted_llm_calls_at_thirty() -> None:
    design, seal = _sealed_design()
    fixture = FixtureJsonGenerator(lambda _stage, _prompt, _inputs: {"action": "not-an-action"})
    result = Stage2Runner(fixture).run(design, seal, _authorization(design, seal), _bundle())

    assert result.status == "CALL_LIMIT_EXHAUSTED"
    assert result.llm_calls == len(result.call_log) == len(fixture.calls) == 30
    assert result.sandbox_calls == 0


def test_sandbox_is_callback_only_and_is_not_an_extra_llm_call() -> None:
    design, seal = _sealed_design()
    binding = derive_python_binding(design, seal)
    observed: list[tuple[str, dict]] = []

    def callback(source: str, probe: dict) -> dict:
        observed.append((source, probe))
        return {"status": "OK", "summary": "The public probe completed.", "observations": {"joint": 0.2}}

    fixture = FixtureJsonGenerator([
        {"action": "sandbox", "capability.py": binding.starter_skeleton, "probe": {"target": 0.2}},
        {"action": "submit", "capability.py": binding.starter_skeleton},
    ])
    result = Stage2Runner(fixture, sandbox=CallbackSandbox(callback)).run(
        design, seal, _authorization(design, seal), _bundle()
    )

    assert result.status == "SUBMITTED"
    assert result.llm_calls == 2
    assert result.sandbox_calls == 1
    assert len(result.sandbox_log) == 1
    assert result.sandbox_log[0]["executed"] is True
    assert observed == [(binding.starter_skeleton, {"target": 0.2})]
    assert fixture.calls[1]["inputs"]["sandbox_feedback"] == {
        "status": "OK", "summary": "The public probe completed.", "observations": {"joint": 0.2}, "exception": None,
    }


def test_submit_seals_exact_source_and_framework_derives_manifest() -> None:
    design, seal = _sealed_design()
    binding = derive_python_binding(design, seal)
    result = Stage2Runner(FixtureJsonGenerator([
        {"action": "submit", "capability.py": binding.starter_skeleton},
    ])).run(design, seal, _authorization(design, seal), _bundle())

    assert result.status == "SUBMITTED"
    assert result.capability_source == binding.starter_skeleton
    assert result.source_hash and result.source_seal and verify_seal(result.source_seal)
    assert result.implementation_manifest and result.manifest_hash and result.manifest_seal
    assert verify_seal(result.manifest_seal)
    assert result.implementation_manifest["design_hash"] == binding.contract["design_hash"]
    assert result.implementation_manifest["binding_contract_hash"] == binding.contract_hash
    assert result.implementation_manifest["implementation_bundle_hash"] == result.implementation_bundle_hash
    assert result.implementation_manifest["source_hash"] == result.source_hash
    assert result.implementation_manifest["symbols"] == [{
        "capability_id": "reach-joint-target", "function_name": "capability_reach_joint_target",
    }]

    blocked = Stage2Runner(FixtureJsonGenerator([
        {"action": "blocked", "reason": "The public SDK method is unavailable."},
    ])).run(design, seal, _authorization(design, seal), _bundle())
    assert blocked.status == "IMPLEMENTATION_BLOCKED"
    assert blocked.capability_source is None


@pytest.mark.parametrize("extra", ["manifest", "files"])
def test_model_cannot_submit_extra_files_or_a_manifest(extra: str) -> None:
    design, seal = _sealed_design()
    source = derive_python_binding(design, seal).starter_skeleton
    response = {"action": "submit", "capability.py": source, extra: {"not": "accepted"}}
    result = Stage2Runner(FixtureJsonGenerator([response]), config=Stage2Config(max_llm_calls=1)).run(
        design, seal, _authorization(design, seal), _bundle()
    )

    assert result.status == "CALL_LIMIT_EXHAUSTED"
    assert result.capability_source is None
    assert result.implementation_manifest is None
    assert {item["code"] for item in result.diagnostics} == {"MODEL_ACTION_FIELDS"}


def test_implementation_bundle_is_closed_deep_copied_and_required_before_llm() -> None:
    raw = _bundle()
    validated = validate_implementation_bundle(raw)
    expected_hash = content_hash(canonical_bytes(raw))
    raw["sdk_implementation_projection"]["members"].append("not-authorized")
    returned = validated.artifact
    returned["robot_implementation_facts"]["position_unit"] = "degree"
    assert validated.bundle_hash == expected_hash
    assert validated.artifact == IMPLEMENTATION_BUNDLE

    design, seal = _sealed_design()
    fixture = FixtureJsonGenerator([{"action": "blocked", "reason": "The public SDK method is unavailable."}])
    for invalid in (
        {},
        dict(_bundle(), unexpected=True),
        dict(_bundle(), artifact_type="wrong"),
        dict(_bundle(), implementation_experience={}),
    ):
        with pytest.raises(ContractError):
            Stage2Runner(fixture).run(design, seal, _authorization(design, seal), invalid)
    validated._artifact["robot_implementation_facts"]["position_unit"] = "degree"
    with pytest.raises(ContractError):
        Stage2Runner(fixture).run(design, seal, _authorization(design, seal), validated)
    assert fixture.calls == []


def test_stage2_llm_and_manifest_are_bound_to_exact_implementation_bundle() -> None:
    design, seal = _sealed_design()
    binding = derive_python_binding(design, seal)
    fixture = FixtureJsonGenerator([{"action": "submit", "capability.py": binding.starter_skeleton}])
    bundle = _bundle()
    result = Stage2Runner(fixture).run(design, seal, _authorization(design, seal), bundle)

    assert fixture.calls[0]["inputs"]["implementation_bundle"] == bundle
    assert result.implementation_bundle_hash == content_hash(canonical_bytes(bundle))
    assert result.bundle_hash == result.implementation_bundle_hash
    assert result.implementation_manifest["implementation_bundle_hash"] == result.implementation_bundle_hash
    assert result.implementation_bundle_hash in result.manifest_seal["parents"]
