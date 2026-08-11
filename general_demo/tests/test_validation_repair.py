from __future__ import annotations

import copy

import pytest

from autoadapter2.blue_line import BlueLineRunner
from autoadapter2.foundation.canonical import canonical_bytes
from autoadapter2.foundation.errors import ContractError
from autoadapter2.foundation.hashing import content_hash
from autoadapter2.foundation.seals import verify_seal
from autoadapter2.generation import FixtureJsonGenerator, Stage1Runner
from autoadapter2.implementation import Stage2Runner
from autoadapter2.validation import (
    HarnessInfrastructureError,
    HarnessMeasurement,
    MeasurementSample,
    RepairRunner,
    TypedHarness,
    ValidationAProfile,
    ValidationARunner,
    ValidationBRunner,
    ValidationContext,
    bind_candidate_to_suite,
)


G2 = {"profile_id": "g2-reusable-effect", "version": "1.0.0", "granularity": "G2"}
ROBOT = {
    "robot_model_id": "so-arm101",
    "robot_configuration_id": "so-arm101-follower-stock-gripper",
    "action_affordances": ["joint target command"],
    "observation_affordances": ["joint position observation"],
    "unit_allowlist": ["rad", "none"],
    "frame_allowlist": ["joint", "none"],
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
    "max_cases_per_capability": 2, "repetitions": 2,
}
PROFILE = ValidationAProfile(
    sdk_facade_members={"reach-joint-target": ("command",)},
    fixture_probes={
        "reach-joint-target": {
            "inputs": {
                "target": {"value": 0.2, "type": "number", "shape": "scalar", "unit": "rad", "frame": "joint"},
            },
        },
    },
)


def _source(status: str = "PASS") -> str:
    return f'''def capability_reach_joint_target(arg_target, *, _sdk):
    _sdk.command(arg_target)
    return {{"reported_status": "{status}"}}
'''


def _design_body() -> dict:
    return {
        "capabilities": [{
            "capability_id": "reach-joint-target",
            "kind": "action",
            "requirement_ids": ["req-reach"],
            "inputs": [{"name": "target", "type": "number", "shape": "scalar", "unit": "rad", "frame": "joint", "required": True}],
            "outputs": [{"name": "reported_status", "type": "string", "shape": "scalar", "unit": "none", "frame": "none", "required": True}],
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


class _Sdk:
    def command(self, _target: float) -> None:
        return None


class _Session:
    def __init__(self, observation: HarnessMeasurement):
        self.sdk = _Sdk()
        self._observation = observation

    def collect(self) -> HarnessMeasurement:
        return self._observation


class _FixedHarness:
    """Tests-only typed Harness; production B receives the TypedHarness protocol."""

    def __init__(self, snapshot: dict, outcomes: list[str]):
        self._snapshot = snapshot
        self._outcomes = list(outcomes)
        self.invocations = []
        self.config_hash = snapshot["harness_config_hash"]

    def open(self, invocation):
        self.invocations.append(invocation)
        outcome = self._outcomes.pop(0)
        if outcome == "infra":
            raise HarnessInfrastructureError("temporary recorder outage")
        value = 0.01 if outcome == "pass" else 0.5
        guards = {guard_id: outcome != "guard_fail" for guard_id in invocation.guard_ids}
        route = {
            "verified": outcome != "route_fail",
            "rim_hash": self._snapshot["rim_hash"],
            "sdk_entry_hash": self._snapshot["sdk_entry_hash"],
            "runtime_hash": self._snapshot["runtime_hash"],
        }
        video = {"artifact_id": "eval-video", "content_hash": content_hash(b"video"), "complete": outcome != "video_fail"}
        observation = HarnessMeasurement(
            measurement_id=invocation.measurement["measurement_id"],
            metric=invocation.metric,
            entity=invocation.measurement["entity"],
            unit=invocation.measurement["unit"],
            frame=invocation.measurement["frame"],
            samples=(MeasurementSample(0.0, value), MeasurementSample(0.2, value)),
            elapsed_s=0.2,
            guard_results=guards,
            sdk_route_evidence=route,
            video_artifact_ref=video,
        )
        return _Session(observation)


def _sealed_design() -> tuple[dict, dict]:
    result = Stage1Runner(FixtureJsonGenerator([_design_body()])).run("run-validation", ROBOT, TASKS, G2)
    assert result.status == "SEALED" and result.capability_design and result.seal
    return result.capability_design, result.seal


def _blue_ready(design: dict, design_seal: dict):
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
            "cases": [{"case_id": "nominal", "initial_state": {"joint": 0.0}, "inputs": {"target": 0.3}}],
            "lineage": {"kind": "COPIED", "standard_id": "joint-arrival", "material": False},
        }],
    }
    result = BlueLineRunner(FixtureJsonGenerator([spec])).run(design, design_seal, STANDARDS, MEASUREMENTS, POLICY)
    assert result.status == "READY" and result.validation_suite and result.suite_seal
    return result


def _stage2_submission(source: str = _source()):
    design, design_seal = _sealed_design()
    stage2 = Stage2Runner(FixtureJsonGenerator([{"action": "submit", "capability.py": source}])).run(design, design_seal, True)
    assert stage2.status == "SUBMITTED"
    blue = _blue_ready(design, design_seal)
    return design, design_seal, stage2, blue


def _context(design: dict, design_seal: dict, blue) -> ValidationContext:
    snapshot = {
        "artifact_type": "validation_run_snapshot",
        "schema_version": "1.0.0",
        "design_hash": content_hash(canonical_bytes(design)),
        "blue_line_spec_hash": blue.spec_hash,
        "blue_line_manifest_hash": blue.manifest_hash,
        "suite_hash": blue.suite_hash,
        "rim_hash": content_hash(b"rim"),
        "sdk_entry_hash": content_hash(b"sdk"),
        "runtime_hash": content_hash(b"runtime"),
        "harness_config_hash": PROFILE.profile_hash,
    }
    return ValidationContext(
        capability_design=design,
        design_seal=design_seal,
        blue_line_spec=blue.validation_spec,
        spec_seal=blue.spec_seal,
        blue_line_manifest=blue.manifest,
        manifest_seal=blue.manifest_seal,
        validation_suite=blue.validation_suite,
        suite_seal=blue.suite_seal,
        run_snapshot=snapshot,
    )


def _a_result(source: str = _source()):
    design, design_seal, stage2, blue = _stage2_submission(source)
    result = ValidationARunner(PROFILE).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": source}, stage2.implementation_manifest, stage2.manifest_seal,
    )
    return design, design_seal, stage2, blue, result


def test_validation_a_profile_checks_facade_envelopes_and_opaque_handle() -> None:
    _design, _seal, stage2, blue, result = _a_result()
    assert result.status == "PASS"
    assert result.candidate_handle is not None
    assert not hasattr(result.candidate_handle, "module")
    assert verify_seal(result.report_seal)
    handle = bind_candidate_to_suite(result, blue.suite_hash)
    assert handle.source_hash == stage2.source_hash
    assert handle.implementation_manifest_hash == stage2.manifest_hash
    assert handle.overlay_hash

    bad_facade = _source().replace("_sdk.command", "_sdk.hidden")
    _design, _seal, _stage2, _blue, facade_result = _a_result(bad_facade)
    assert facade_result.status == "FAIL"
    assert "SDK_FACADE" in {item["code"] for item in facade_result.diagnostics}

    extra_output = _source().replace('"reported_status": "PASS"', '"reported_status": "PASS", "extra": "attack"')
    _design, _seal, _stage2, _blue, envelope_result = _a_result(extra_output)
    assert envelope_result.status == "FAIL"
    assert "FIXTURE_PROBE" in {item["code"] for item in envelope_result.diagnostics}


def test_validation_a_bans_import_decorator_default_annotation_and_dunder() -> None:
    cases = [
        "import os\n\n" + _source(),
        "@x\n" + _source(),
        _source().replace("arg_target, *, _sdk", "arg_target=0.2, *, _sdk"),
        _source().replace("arg_target, *, _sdk", "arg_target: float, *, _sdk"),
        _source().replace("_sdk.command", "_sdk.__getattribute__"),
    ]
    for source in cases:
        _design, _seal, _stage2, _blue, result = _a_result(source)
        assert result.status == "FAIL"


def test_validation_b_evaluates_sealed_measurements_not_candidate_self_report() -> None:
    design, design_seal, _stage2, blue, a_result = _a_result(_source("PASS"))
    context = _context(design, design_seal, blue)
    harness = _FixedHarness(context.run_snapshot, ["fail", "fail"])
    assert isinstance(harness, TypedHarness)
    result = ValidationBRunner(harness).run(bind_candidate_to_suite(a_result, blue.suite_hash), context)
    assert result.status == "FAIL"
    assert {"THRESHOLD", "DWELL"}.issubset({code for item in result.executions for code in item["failure_codes"]})
    assert result.report["suite_hash"] == blue.suite_hash
    assert result.report["blue_line_manifest_hash"] == blue.manifest_hash
    assert result.report["run_snapshot_hash"] == content_hash(canonical_bytes(context.run_snapshot))

    route_or_video = ValidationBRunner(_FixedHarness(context.run_snapshot, ["route_fail", "video_fail"])).run(
        bind_candidate_to_suite(a_result, blue.suite_hash), context
    )
    assert route_or_video.status == "FAIL"
    assert {"SDK_ROUTE_EVIDENCE", "VIDEO_ARTIFACT"}.issubset(
        {code for execution in route_or_video.executions for code in execution["failure_codes"]}
    )
    with pytest.raises(ContractError):
        ValidationBRunner(lambda _unused: None)  # type: ignore[arg-type]


def test_validation_b_requires_handle_lineage_sdk_video_and_candidate_exception_is_fail() -> None:
    source = '''def capability_reach_joint_target(arg_target, *, _sdk):
    _sdk.command(arg_target)
    if arg_target > 0.25:
        return 1 / 0
    return {"reported_status": "PASS"}
'''
    design, design_seal, _stage2, blue, a_result = _a_result(source)
    context = _context(design, design_seal, blue)
    harness = _FixedHarness(context.run_snapshot, ["pass", "pass"])
    runner = ValidationBRunner(harness)
    result = runner.run(bind_candidate_to_suite(a_result, blue.suite_hash), context)
    assert result.status == "FAIL"
    assert "CANDIDATE_EXCEPTION" in result.executions[0]["failure_codes"]

    tampered = copy.deepcopy(context)
    tampered_manifest = copy.deepcopy(context.blue_line_manifest)
    tampered_manifest["status"] = "NEEDS_REVIEW"
    bad_context = ValidationContext(
        capability_design=context.capability_design, design_seal=context.design_seal,
        blue_line_spec=context.blue_line_spec, spec_seal=context.spec_seal,
        blue_line_manifest=tampered_manifest, manifest_seal=context.manifest_seal,
        validation_suite=context.validation_suite, suite_seal=context.suite_seal,
        run_snapshot=context.run_snapshot,
    )
    with pytest.raises(ContractError):
        runner.run(bind_candidate_to_suite(a_result, blue.suite_hash), bad_context)
    with pytest.raises(ContractError):
        runner.run(a_result.candidate_handle, context)  # type: ignore[arg-type]
    with pytest.raises(ContractError):
        runner.run(a_result.candidate_handle._module, context)  # type: ignore[arg-type]
    assert tampered is not None


def test_repair_new_source_consumes_k_no_change_does_not_run_b_and_snapshot_is_frozen() -> None:
    design, design_seal, stage2, blue = _stage2_submission(_source("INITIAL"))
    context = _context(design, design_seal, blue)
    frozen_design = copy.deepcopy(design)
    frozen_binding = copy.deepcopy(stage2.binding_contract)
    frozen_context = copy.deepcopy(context)
    harness = _FixedHarness(context.run_snapshot, ["fail", "fail"])
    no_change = RepairRunner(
        ValidationARunner(PROFILE), ValidationBRunner(harness),
        lambda _request: {"capability.py": _source("INITIAL"), "llm_calls": 1},
    ).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest, stage2.manifest_seal, context,
    )
    assert no_change.status == "NO_CHANGE"
    assert no_change.repairs_consumed == 0
    assert len(no_change.run_ledger) == 1
    assert design == frozen_design
    assert stage2.binding_contract == frozen_binding
    assert context == frozen_context

    harness = _FixedHarness(context.run_snapshot, ["fail", "fail", "pass", "pass"])
    repaired = RepairRunner(
        ValidationARunner(PROFILE), ValidationBRunner(harness),
        lambda _request: {"capability.py": _source("REPAIRED"), "llm_calls": 2},
    ).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest, stage2.manifest_seal, context,
    )
    assert repaired.status == "PASS"
    assert repaired.first_passing_repair_index == repaired.repairs_consumed == 1
    assert repaired.repair_llm_calls == 2
    assert repaired.run_snapshot_hash == content_hash(canonical_bytes(context.run_snapshot))

    outputs = iter([_source("HISTORICAL_NEW"), _source("INITIAL")])
    historical = RepairRunner(
        ValidationARunner(PROFILE),
        ValidationBRunner(_FixedHarness(context.run_snapshot, ["fail", "fail", "fail", "fail"])),
        lambda _request: {"capability.py": next(outputs), "llm_calls": 1},
    ).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest, stage2.manifest_seal, context,
    )
    assert historical.status == "NO_CHANGE"
    assert historical.repairs_consumed == 1
    assert len(historical.run_ledger) == 2


def test_repair_retries_infrastructure_on_same_revision_without_llm_and_caps_at_ten() -> None:
    design, design_seal, stage2, blue = _stage2_submission(_source("INITIAL"))
    context = _context(design, design_seal, blue)
    harness = _FixedHarness(context.run_snapshot, ["fail", "fail", "infra", "pass", "pass"])
    result = RepairRunner(
        ValidationARunner(PROFILE), ValidationBRunner(harness),
        lambda _request: {"capability.py": _source("REPAIRED"), "llm_calls": 1},
    ).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest, stage2.manifest_seal, context,
    )
    assert result.status == "PASS"
    assert result.repairs_consumed == 1
    revision_one = [entry for entry in result.run_ledger if entry["revision_index"] == 1]
    assert [entry["b_status"] for entry in revision_one] == ["INFRASTRUCTURE_ERROR", "PASS"]
    assert revision_one[0]["source_hash"] == revision_one[1]["source_hash"]
    assert result.repair_llm_calls == 1

    calls = 0

    def ten_distinct(_request):
        nonlocal calls
        calls += 1
        return {"capability.py": _source(f"REVISION_{calls}"), "llm_calls": 1}

    always_fail = _FixedHarness(context.run_snapshot, ["fail"] * 22)
    capped = RepairRunner(ValidationARunner(PROFILE), ValidationBRunner(always_fail), ten_distinct).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest, stage2.manifest_seal, context,
    )
    assert capped.status == "FAILED_AFTER_REPAIRS"
    assert capped.repairs_consumed == capped.repair_llm_calls == calls == 10
