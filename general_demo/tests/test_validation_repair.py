from __future__ import annotations

import copy

from autoadapter2.foundation.canonical import canonical_bytes
from autoadapter2.foundation.hashing import content_hash
from autoadapter2.foundation.seals import create_seal, verify_seal
from autoadapter2.generation import FixtureJsonGenerator, Stage1Runner
from autoadapter2.implementation import Stage2Runner
from autoadapter2.validation import (
    RepairRunner,
    ValidationARunner,
    ValidationBRunner,
    create_execution_binding_overlay,
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


VALID_SOURCE = '''def capability_reach_joint_target(arg_target, *, _sdk):
    _sdk.command(arg_target)
    return {"status": "PASS"}
'''


class _Sdk:
    def command(self, _target: float) -> None:
        return None


def _sealed_design() -> tuple[dict, dict]:
    result = Stage1Runner(FixtureJsonGenerator([_design_body()])).run("run-validation", ROBOT, TASKS, G2)
    assert result.status == "SEALED" and result.capability_design and result.seal
    return result.capability_design, result.seal


def _stage2_submission(source: str = VALID_SOURCE):
    design, design_seal = _sealed_design()
    result = Stage2Runner(FixtureJsonGenerator([
        {"action": "submit", "capability.py": source},
    ])).run(design, design_seal, True)
    assert result.status == "SUBMITTED"
    return design, design_seal, result


def _suite(design_hash: str) -> tuple[dict, dict]:
    suite = {
        "artifact_type": "validation_b_suite",
        "schema_version": "1.0.0",
        "design_hash": design_hash,
        "spec_hash": content_hash(b"fixed-private-spec"),
        "repetitions": 2,
        "capability_cases": [{
            "capability_id": "reach-joint-target",
            "cases": [{"case_id": "case-one", "initial_state": {"joint": 0.0}, "inputs": {"target": 0.2}}],
        }],
    }
    suite_hash = content_hash(canonical_bytes(suite))
    return suite, create_seal("validation_b_suite", suite_hash, [design_hash])


def _a_result(source: str = VALID_SOURCE):
    design, design_seal, stage2 = _stage2_submission(source)
    result = ValidationARunner().run(
        stage2.binding_contract,
        stage2.binding_seal,
        {"capability.py": source},
        stage2.implementation_manifest,
        stage2.manifest_seal,
    )
    return design, design_seal, stage2, result


def test_validation_a_passes_exact_binding_and_seals_overlay() -> None:
    design, _design_seal, stage2, result = _a_result()
    suite, _suite_seal = _suite(stage2.binding_contract["design_hash"])
    suite_hash = content_hash(canonical_bytes(suite))

    assert result.status == "PASS"
    assert result.candidate_module is not None
    assert verify_seal(result.report_seal)
    overlay = create_execution_binding_overlay(result, suite_hash)
    assert overlay.overlay == {
        "artifact_type": "validation_execution_binding_overlay",
        "schema_version": "1.0.0",
        "suite_hash": suite_hash,
        "implementation_manifest_hash": stage2.manifest_hash,
        "capability_bindings": [{
            "capability_id": "reach-joint-target", "function_name": "capability_reach_joint_target",
        }],
    }
    assert verify_seal(overlay.seal)
    assert design["capabilities"][0]["capability_id"] == "reach-joint-target"


def test_validation_a_rejects_dangerous_import_and_wrong_signature() -> None:
    dangerous = "import os\n\n" + VALID_SOURCE
    _design, _seal, stage2, result = _a_result(dangerous)
    assert result.status == "FAIL"
    assert "FORBIDDEN_IMPORT" in {item["code"] for item in result.diagnostics}

    wrong_signature = '''def capability_reach_joint_target(*, _sdk):
    _sdk.command(0.2)
    return {"status": "PASS"}
'''
    _design, _seal, _stage2, result = _a_result(wrong_signature)
    assert result.status == "FAIL"
    assert "PUBLIC_SIGNATURE" in {item["code"] for item in result.diagnostics}
    assert stage2.implementation_manifest["source_file"] == "capability.py"

    one_file = ValidationARunner().run(
        stage2.binding_contract,
        stage2.binding_seal,
        {"capability.py": dangerous, "extra.py": "not allowed"},
        stage2.implementation_manifest,
        stage2.manifest_seal,
    )
    assert one_file.status == "FAIL"
    assert "ONE_FILE_SUBMISSION" in {item["code"] for item in one_file.diagnostics}


def test_validation_b_uses_trusted_verdict_covers_cases_and_keeps_suite_immutable() -> None:
    _design, _seal, stage2, a_result = _a_result()
    suite, suite_seal = _suite(stage2.binding_contract["design_hash"])
    before = copy.deepcopy(suite)
    overlay = create_execution_binding_overlay(a_result, content_hash(canonical_bytes(suite)))
    calls: list[tuple[str, int]] = []

    def executor(module, binding, case, repetition):
        self_report = getattr(module, binding["function_name"])(case["inputs"]["target"], _sdk=_Sdk())
        calls.append((case["case_id"], repetition))
        return {"candidate_status": self_report["status"], "trusted_verdict": "FAIL"}

    result = ValidationBRunner(executor).run(suite, suite_seal, overlay, a_result.candidate_module)
    assert result.status == "FAIL"
    assert len(calls) == 2
    assert {item["trusted_verdict"] for item in result.executions} == {"FAIL"}
    assert result.suite_hash == content_hash(canonical_bytes(before))
    assert suite == before


def test_validation_b_reports_infrastructure_error_from_trusted_executor() -> None:
    _design, _seal, stage2, a_result = _a_result()
    suite, suite_seal = _suite(stage2.binding_contract["design_hash"])
    overlay = create_execution_binding_overlay(a_result, content_hash(canonical_bytes(suite)))
    result = ValidationBRunner(lambda *_args: {"trusted_verdict": "INFRASTRUCTURE_ERROR"}).run(
        suite, suite_seal, overlay, a_result.candidate_module
    )
    assert result.status == "INFRASTRUCTURE_ERROR"
    assert result.executions[0]["trusted_verdict"] == "INFRASTRUCTURE_ERROR"


def test_repair_passes_on_k_and_preserves_design_binding_and_suite() -> None:
    design, design_seal, stage2 = _stage2_submission()
    suite, suite_seal = _suite(stage2.binding_contract["design_hash"])
    frozen_design = copy.deepcopy(design)
    frozen_binding = copy.deepcopy(stage2.binding_contract)
    frozen_suite = copy.deepcopy(suite)
    verdict_calls = 0
    repair_requests: list[dict] = []

    def executor(*_args):
        nonlocal verdict_calls
        verdict_calls += 1
        return {"trusted_verdict": "FAIL" if verdict_calls <= 4 else "PASS"}

    def repair_callback(request):
        repair_requests.append(request)
        assert "validation_suite" not in request and "capability_design" not in request
        return {"capability.py": VALID_SOURCE, "llm_calls": 2}

    result = RepairRunner(ValidationARunner(), ValidationBRunner(executor), repair_callback).run(
        stage2.binding_contract,
        stage2.binding_seal,
        {"capability.py": stage2.capability_source},
        stage2.implementation_manifest,
        stage2.manifest_seal,
        suite,
        suite_seal,
        capability_design=design,
        design_seal=design_seal,
    )
    assert result.status == "PASS"
    assert result.first_passing_repair_index == 2
    assert result.repairs_consumed == 2
    assert result.repair_llm_calls == 4
    assert len(repair_requests) == 2
    assert design == frozen_design
    assert stage2.binding_contract == frozen_binding
    assert suite == frozen_suite


def test_repair_caps_at_ten_and_infrastructure_does_not_consume_repair() -> None:
    design, design_seal, stage2 = _stage2_submission()
    suite, suite_seal = _suite(stage2.binding_contract["design_hash"])
    failed = RepairRunner(
        ValidationARunner(),
        ValidationBRunner(lambda *_args: {"trusted_verdict": "FAIL"}),
        lambda _request: {"capability.py": VALID_SOURCE, "llm_calls": 1},
    ).run(
        stage2.binding_contract, stage2.binding_seal, {"capability.py": stage2.capability_source},
        stage2.implementation_manifest, stage2.manifest_seal, suite, suite_seal,
        capability_design=design, design_seal=design_seal,
    )
    assert failed.status == "FAILED_AFTER_REPAIRS"
    assert failed.repairs_consumed == 10
    assert failed.repair_llm_calls == 10
    assert len(failed.repair_log) == 10

    callback_calls = 0

    def callback(_request):
        nonlocal callback_calls
        callback_calls += 1
        return {"capability.py": VALID_SOURCE, "llm_calls": 1}

    infrastructure = RepairRunner(
        ValidationARunner(),
        ValidationBRunner(lambda *_args: {"trusted_verdict": "INFRASTRUCTURE_ERROR"}),
        callback,
    ).run(
        stage2.binding_contract, stage2.binding_seal, {"capability.py": stage2.capability_source},
        stage2.implementation_manifest, stage2.manifest_seal, suite, suite_seal,
    )
    assert infrastructure.status == "INFRASTRUCTURE_ERROR"
    assert infrastructure.repairs_consumed == 0
    assert infrastructure.repair_llm_calls == 0
    assert callback_calls == 0


def test_repair_infrastructure_after_a_new_candidate_is_not_consumed() -> None:
    _design, _design_seal, stage2 = _stage2_submission()
    suite, suite_seal = _suite(stage2.binding_contract["design_hash"])
    verdict_calls = 0

    def executor(*_args):
        nonlocal verdict_calls
        verdict_calls += 1
        return {"trusted_verdict": "FAIL" if verdict_calls <= 2 else "INFRASTRUCTURE_ERROR"}

    result = RepairRunner(
        ValidationARunner(),
        ValidationBRunner(executor),
        lambda _request: {"capability.py": VALID_SOURCE, "llm_calls": 1},
    ).run(
        stage2.binding_contract, stage2.binding_seal, {"capability.py": stage2.capability_source},
        stage2.implementation_manifest, stage2.manifest_seal, suite, suite_seal,
    )
    assert result.status == "INFRASTRUCTURE_ERROR"
    assert result.repairs_consumed == 0
    assert result.repair_llm_calls == 1
    assert result.repair_log[0]["consumed"] is False
