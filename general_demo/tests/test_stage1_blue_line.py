from __future__ import annotations

import copy

import pytest

from autoadapter2.blue_line import BLUE_LINE_PROMPT, BlueLineRunner
from autoadapter2.foundation.canonical import canonical_bytes
from autoadapter2.foundation.errors import ContractError
from autoadapter2.foundation.hashing import content_hash
from autoadapter2.foundation.seals import verify_seal
from autoadapter2.generation import FixtureJsonGenerator, Stage1Config, Stage1Runner
from autoadapter2.generation.stage1 import STAGE1_PROMPT


G2 = {"profile_id": "g2-reusable-effect", "version": "1.0.0", "granularity": "G2"}
ROBOT = {
    "robot_model_id": "so-arm101",
    "robot_configuration_id": "so-arm101-follower-stock-gripper",
    "action_affordances": ["joint target command"],
    "observation_affordances": ["joint position observation"],
    "unit_allowlist": ["rad"],
    "frame_allowlist": ["joint", "base"],
}
TASKS = [{"requirement_id": "req-reach", "description": "Reach a public joint target safely."}]
STANDARDS = {
    "snapshot_id": "standards-1",
    "standards": [{
        "standard_id": "joint-arrival", "measurement_id": "joint-error", "metric": "max_joint_error",
        "comparator": "<=", "threshold_value": 0.05, "dwell_s": 0.2,
        "timeout_s": 2.0, "aggregation": "ALL",
    }],
}
MEASUREMENTS = {
    "catalog_id": "measurements-1",
    "measurements": [{
        "measurement_id": "joint-error", "entity": "shoulder_pan", "unit": "rad",
        "frame": "joint", "adapter_id": "truth-joint-state", "truth_source": "physical_state",
        "metrics": ["max_joint_error"],
    }],
    "guards": [{"guard_id": "physical-state-not-command-receipt", "adapter_id": "truth-joint-state"}],
}
POLICY = {
    "policy_id": "blue-1", "model_id": "fixed-fixture", "prompt_id": "blue-prompt-1",
    "max_cases_per_capability": 2, "repetitions": 3,
}


def test_stage1_prompt_exposes_closed_body_and_correction_contract() -> None:
    assert "exactly these three top-level fields" in STAGE1_PROMPT
    for field in ("capabilities", "unsupported_requirement_ids", "blocking_requirement_ids"):
        assert f"`{field}`" in STAGE1_PROMPT
    for field in (
        "capability_id", "kind", "requirement_ids", "inputs", "outputs", "effect",
        "preconditions", "invocation_semantics", "temporal_semantics", "invariants",
        "required_action_affordances", "required_observation_affordances", "errors",
        "unsupported_scope",
    ):
        assert f"`{field}`" in STAGE1_PROMPT
    assert "`action`, `observation`, `state_maintenance`, or `composition`" in STAGE1_PROMPT
    assert "`{name,type,shape,unit,frame,required}`" in STAGE1_PROMPT
    assert "`{code,message}`" in STAGE1_PROMPT
    assert "empty array when a section is not applicable" in STAGE1_PROMPT
    assert "working_design" in STAGE1_PROMPT and "diagnostics" in STAGE1_PROMPT
    assert "return only the corrected body" in STAGE1_PROMPT


def test_blue_line_prompt_exposes_closed_multi_criterion_contract() -> None:
    assert '{"capability_specs": [...]}' in BLUE_LINE_PROMPT
    assert "exactly one spec for every capability_id" in BLUE_LINE_PROMPT
    assert "capability_id, criteria, cases, lineage, false_pass_analysis" in BLUE_LINE_PROMPT
    for field in (
        "criterion_id", "measurement_id", "metric", "comparator", "threshold_value",
        "dwell_s", "timeout_s", "aggregation", "guard_ids",
    ):
        assert field in BLUE_LINE_PROMPT
    assert "Each cases item must have exactly {case_id, initial_state, inputs}" in BLUE_LINE_PROMPT
    assert "policy.max_cases_per_capability" in BLUE_LINE_PROMPT
    assert "{kind, standard_id, material}" in BLUE_LINE_PROMPT
    assert '"kind":"COPIED"' in BLUE_LINE_PROMPT
    assert "{risk_id, guard_id}" in BLUE_LINE_PROMPT
    assert "working_spec" in BLUE_LINE_PROMPT and "diagnostics" in BLUE_LINE_PROMPT
    assert "complete corrected body" in BLUE_LINE_PROMPT
    for forbidden in ("Markdown", "prose", "candidate", "Stage 2", "Sandbox", "Repair", "execution outcomes"):
        assert forbidden in BLUE_LINE_PROMPT


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


def _design_experience_snapshot() -> dict:
    applicability = {
        "robot_model_id": ROBOT["robot_model_id"],
        "robot_configuration_id": ROBOT["robot_configuration_id"],
        "sdk_entry_id": "lerobot-so-arm101-follower",
        "granularity_condition": "G2",
        "capability_effect_scope": ["reach-joint-target"],
        "observation_condition": "joint position observation",
    }
    record_hash = "sha256:" + "1" * 64
    return {
        "artifact_type": "experience_snapshot",
        "format_version": "experimental-1",
        "snapshot_id": "design-snapshot-1",
        "recipient_class": "design",
        "applicability": applicability,
        "records": [{
            "record_id": "experience-record-1",
            "version": "1.0.0",
            "record_ref": {
                "path": "general_demo/libraries/experience/records/design/experience-record-1/1.0.0/record.json",
                "content_hash": record_hash,
            },
            "projection": {
                "experience_id": "joint-target-guidance",
                "guidance": "Prefer a reusable public joint-target effect.",
                "applicability": copy.deepcopy(applicability),
                "provenance": {
                    "closure_hash": record_hash,
                    "summary_ref": "general_demo/runs/run-1/summary.json",
                    "stage_artifacts_ref": "general_demo/runs/run-1/stage-artifacts.json",
                    "evidence_digest_hash": record_hash,
                },
            },
        }],
    }


def test_stage1_uses_a_deterministic_explicit_empty_experience_snapshot() -> None:
    first_fixture = FixtureJsonGenerator([_capability_body()])
    first = Stage1Runner(first_fixture).run("run-1", ROBOT, TASKS, G2)
    second_fixture = FixtureJsonGenerator([_capability_body()])
    second = Stage1Runner(second_fixture).run("run-1", ROBOT, TASKS, G2)

    empty_snapshot = first_fixture.calls[0]["inputs"]["design_experience_snapshot"]
    assert empty_snapshot["recipient_class"] == "design"
    assert empty_snapshot["records"] == []
    assert first.capability_design["design_experience_snapshot_hash"] == content_hash(canonical_bytes(empty_snapshot))
    assert first.capability_design["design_experience_snapshot_hash"] == second.capability_design["design_experience_snapshot_hash"]
    assert first_fixture.calls[0]["inputs"]["design_experience_snapshot"] == second_fixture.calls[0]["inputs"]["design_experience_snapshot"]


def test_stage1_passes_design_experience_to_generator_and_seals_only_its_hash() -> None:
    snapshot = _design_experience_snapshot()
    fixture = FixtureJsonGenerator([_capability_body()])
    result = Stage1Runner(fixture).run(
        "run-1", ROBOT, TASKS, G2, design_experience_snapshot=snapshot
    )

    assert fixture.calls[0]["inputs"]["design_experience_snapshot"] == snapshot
    assert result.capability_design["design_experience_snapshot_hash"] == content_hash(canonical_bytes(snapshot))
    assert "design_experience_snapshot" not in result.capability_design


@pytest.mark.parametrize("recipient_class", ["implementation", "consumer"])
def test_stage1_rejects_non_design_experience_recipients_before_model_call(recipient_class: str) -> None:
    snapshot = _design_experience_snapshot()
    snapshot["recipient_class"] = recipient_class
    fixture = FixtureJsonGenerator([_capability_body()])

    with pytest.raises(ContractError):
        Stage1Runner(fixture).run("run-1", ROBOT, TASKS, G2, design_experience_snapshot=snapshot)
    assert fixture.calls == []


@pytest.mark.parametrize("mutation", ["unknown", "private", "non_json", "mismatch"])
def test_stage1_rejects_invalid_design_experience_before_model_call(mutation: str) -> None:
    snapshot = _design_experience_snapshot()
    if mutation == "unknown":
        snapshot["unexpected"] = True
    elif mutation == "private":
        snapshot["records"][0]["projection"]["provenance"] = {"private_note": "hidden"}
    elif mutation == "non_json":
        snapshot["records"][0]["projection"]["guidance"] = {"not_json": {"value"}}
    else:
        snapshot["records"][0]["projection"]["applicability"] = {"robot_configuration_id": "other"}
    fixture = FixtureJsonGenerator([_capability_body()])

    with pytest.raises(ContractError):
        Stage1Runner(fixture).run("run-1", ROBOT, TASKS, G2, design_experience_snapshot=snapshot)
    assert fixture.calls == []


def test_stage1_correction_receives_the_original_experience_snapshot() -> None:
    snapshot = _design_experience_snapshot()
    observed: list[dict] = []

    def generate(_stage: str, _prompt: str, inputs: dict) -> dict:
        observed.append(copy.deepcopy(inputs["design_experience_snapshot"]))
        if len(observed) == 1:
            inputs["design_experience_snapshot"]["records"][0]["projection"]["guidance"] = "tampered"
            return {"capabilities": [], "unsupported_requirement_ids": [], "blocking_requirement_ids": []}
        return _capability_body()

    result = Stage1Runner(
        FixtureJsonGenerator(generate), Stage1Config(max_correction_calls=1)
    ).run("run-1", ROBOT, TASKS, G2, design_experience_snapshot=snapshot)

    assert result.status == "SEALED"
    assert observed[0] == observed[1] == snapshot
    assert result.capability_design["design_experience_snapshot_hash"] == content_hash(canonical_bytes(snapshot))


def test_stage1_copies_experience_snapshot_before_the_call_returns() -> None:
    snapshot = _design_experience_snapshot()
    fixture = FixtureJsonGenerator([_capability_body()])
    result = Stage1Runner(fixture).run("run-1", ROBOT, TASKS, G2, design_experience_snapshot=snapshot)
    captured = copy.deepcopy(fixture.calls[0]["inputs"]["design_experience_snapshot"])

    snapshot["records"][0]["projection"]["guidance"] = "mutated after run"

    assert fixture.calls[0]["inputs"]["design_experience_snapshot"] == captured
    assert result.capability_design["design_experience_snapshot_hash"] == content_hash(canonical_bytes(captured))


def _sealed_design() -> tuple[dict, dict]:
    result = Stage1Runner(FixtureJsonGenerator([_capability_body()])).run("run-1", ROBOT, TASKS, G2)
    assert result.status == "SEALED" and result.capability_design and result.seal
    return result.capability_design, result.seal


def _valid_spec(lineage: dict | None = None) -> dict:
    return {
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
            "lineage": lineage or {"kind": "COPIED", "standard_id": "joint-arrival", "material": False},
        }],
    }


def test_stage1_rejects_private_input_before_any_model_call() -> None:
    fixture = FixtureJsonGenerator([_capability_body()])
    tasks = [{"requirement_id": "req-reach", "description": "Reach.", "private_criterion": "secret"}]
    with pytest.raises(ContractError):
        Stage1Runner(fixture).run("run-1", ROBOT, tasks, G2)
    assert fixture.calls == []


def test_stage1_rejects_non_public_task_fields_and_projection_mismatches() -> None:
    fixture = FixtureJsonGenerator([_capability_body()])
    with pytest.raises(ContractError):
        Stage1Runner(fixture).run("run-1", ROBOT, [{"requirement_id": "req", "description": "x", "label": "extra"}], G2)
    assert fixture.calls == []

    invalid = _capability_body()
    invalid["capabilities"][0]["required_action_affordances"] = ["hidden action"]
    invalid["capabilities"][0]["inputs"][0]["unit"] = "degree"
    invalid["capabilities"][0]["inputs"][0]["frame"] = "hidden-frame"
    result = Stage1Runner(FixtureJsonGenerator([invalid]), Stage1Config(max_correction_calls=0)).run("run-1", ROBOT, TASKS, G2)
    assert result.status == "FAILED"
    assert {issue["code"] for issue in result.diagnostics} >= {"PUBLIC_AFFORDANCE", "PUBLIC_UNIT", "PUBLIC_FRAME"}


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
    assert result.stage2_authorization is None

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
    assert first.stage2_authorization is not None
    assert first.validation_suite["repetitions"] == 3
    assert verify_seal(first.spec_seal)
    assert verify_seal(first.suite_seal)
    assert verify_seal(first.manifest_seal)


@pytest.mark.parametrize("mutation", ["threshold", "guard"])
def test_copied_standard_cannot_change_a_threshold_or_use_an_unknown_guard(mutation: str) -> None:
    design, seal = _sealed_design()
    invalid = _valid_spec()
    if mutation == "threshold":
        invalid["capability_specs"][0]["threshold"]["value"] = 0.5
    else:
        invalid["capability_specs"][0]["guard_ids"] = ["unknown-guard"]
    result = BlueLineRunner(FixtureJsonGenerator([invalid, copy.deepcopy(invalid), copy.deepcopy(invalid)])).run(
        design, seal, STANDARDS, MEASUREMENTS, POLICY
    )
    assert result.status == "NEEDS_REVIEW"
    assert result.validation_suite is None
    expected = "COPIED_STANDARD_BINDING" if mutation == "threshold" else "FALSE_PASS_GUARD"
    assert expected in {issue["code"] for issue in result.diagnostics}
