from __future__ import annotations

import copy

import pytest

from autoadapter2.blue_line import BlueLineRunner
from autoadapter2.evaluation import (
    ClosedEvaluationVideo,
    EncodedVideo,
    EvaluationVideoRecorder,
    FrozenVideoProfile,
    OpaqueVideoHandle,
    RGBFrame,
)
from autoadapter2.foundation.canonical import canonical_bytes
from autoadapter2.foundation.errors import ContractError
from autoadapter2.foundation.hashing import content_hash
from autoadapter2.foundation.seals import create_seal, verify_seal
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
IMPLEMENTATION_BUNDLE = {
    "artifact_type": "stage2_implementation_bundle",
    "schema_version": "1.0.0",
    "sdk_implementation_projection": {"sdk_entry_id": "lerobot-so-arm101", "members": ["command"]},
    "robot_implementation_facts": {"joint_order": ["shoulder_pan"], "position_unit": "rad"},
    "implementation_experience": [{"experience_id": "so-arm101-command-v1"}],
}
BUNDLE_HASH = content_hash(canonical_bytes(IMPLEMENTATION_BUNDLE))
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
GO2_PROFILE = ValidationAProfile(
    sdk_facade_members={"reach-joint-target": ("LowCmd_", "CRC", "publisher")},
    fixture_probes={
        "reach-joint-target": {
            "inputs": {
                "q": {"value": [0.0] * 20, "type": "number", "shape": "vector:20", "unit": "rad", "frame": "joint"},
            },
        },
    },
)
GO2_IMPLEMENTATION_BUNDLE = copy.deepcopy(IMPLEMENTATION_BUNDLE)
GO2_IMPLEMENTATION_BUNDLE["sdk_implementation_projection"] = {
    "sdk_entry_id": "unitree-sdk2",
    "members": ["LowCmd_", "CRC", "publisher"],
}
GO2_BUNDLE_HASH = content_hash(canonical_bytes(GO2_IMPLEMENTATION_BUNDLE))


def _bundle() -> dict:
    return copy.deepcopy(IMPLEMENTATION_BUNDLE)


def _source(status: str = "PASS") -> str:
    return f'''def capability_reach_joint_target(arg_target, *, _sdk):
    _sdk.command(arg_target)
    return {{"reported_status": "{status}"}}
'''


def _go2_source() -> str:
    assignments = "\n".join(f"    cmd.motor_cmd[{index}].q = arg_q[{index}]" for index in range(20))
    return f'''def capability_reach_joint_target(arg_q, *, _sdk):
    cmd = _sdk.LowCmd_()
    crc = _sdk.CRC()
{assignments}
    cmd.crc = crc.Crc(cmd)
    _sdk.publisher.Write(cmd)
    return {{"reported_status": "PASS"}}
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
        self.invoke_calls = []

    def invoke(self, candidate, capability_id, inputs):
        self.invoke_calls.append((candidate, capability_id, copy.deepcopy(dict(inputs))))
        return candidate._invoke(capability_id, inputs, self.sdk)

    def collect(self) -> HarnessMeasurement:
        return self._observation


class _FixedHarness:
    """Tests-only typed Harness; production B receives the TypedHarness protocol."""

    def __init__(self, snapshot: dict, outcomes: list[str]):
        self._snapshot = snapshot
        self._outcomes = list(outcomes)
        self.invocations = []
        self.sessions = []
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
        video = _video_evidence(invocation, complete=outcome != "video_fail")
        if outcome == "forged_video":
            video = ClosedEvaluationVideo(
                completion_status=video.completion_status,
                execution_disposition=video.execution_disposition,
                media_content_hash=video.media_content_hash,
                manifest_content_hash=video.manifest_content_hash,
                manifest_bytes=video.manifest_bytes,
                handle=video.handle,
                failures=video.failures,
            )
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
            video_evidence=video,
        )
        session = _Session(observation)
        self.sessions.append(session)
        return session


class _ValidationTestVideoEncoder:
    def encode(self, _profile, frames):
        payload = b"validation-test-video\0" + b"|".join(frame.rgb for frame in frames)
        return EncodedVideo(OpaqueVideoHandle("validation-test-video"), payload)


def _video_evidence(invocation, *, complete: bool) -> ClosedEvaluationVideo:
    profile = FrozenVideoProfile(
        profile_id="validation-test-video",
        profile_version="1.0.0",
        camera="external-evaluation",
        view="robot-and-resource",
        fps=10,
        width=1,
        height=1,
        container="fake",
        codec="fake-rgb",
    )
    recorder = EvaluationVideoRecorder(
        recording_id=(
            f"{invocation.capability_id}-{invocation.case_id}-"
            f"{invocation.repetition}-{invocation.execution_attempt}"
        ),
        profile=profile,
        encoder=_ValidationTestVideoEncoder(),
        coverage_start_time_s=0.0,
        bindings={
            "phase": "VALIDATION_B",
            "capability_id": invocation.capability_id,
            "criterion_id": invocation.criterion_id,
            "case_id": invocation.case_id,
            "repetition": str(invocation.repetition),
            "run_snapshot_hash": invocation.run_snapshot_hash,
            "candidate_source_hash": invocation.candidate_source_hash,
            "suite_hash": invocation.suite_hash,
            "execution_attempt": str(invocation.execution_attempt),
        },
    )
    if complete:
        recorder.add_frame(RGBFrame(0.0, 1, 1, b"\x00\x00\x00"))
        recorder.add_frame(RGBFrame(0.1, 1, 1, b"\x01\x01\x01"))
    else:
        recorder.add_frame(RGBFrame(0.1, 1, 1, b"\x01\x01\x01"))
    return recorder.close(terminal_time_s=0.1)


def _sealed_design() -> tuple[dict, dict]:
    result = Stage1Runner(FixtureJsonGenerator([_design_body()])).run("run-validation", ROBOT, TASKS, G2)
    assert result.status == "SEALED" and result.capability_design and result.seal
    return result.capability_design, result.seal


def _blue_ready(design: dict, design_seal: dict, *, input_name: str = "target", input_value: object = 0.3):
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
            "cases": [{"case_id": "nominal", "initial_state": {"joint": 0.0}, "inputs": {input_name: input_value}}],
            "lineage": {"kind": "COPIED", "standard_id": "joint-arrival", "material": False},
        }],
    }
    result = BlueLineRunner(FixtureJsonGenerator([spec])).run(design, design_seal, STANDARDS, MEASUREMENTS, POLICY)
    assert result.status == "READY" and result.validation_suite and result.suite_seal
    return result


def _stage2_submission(source: str = _source()):
    design, design_seal = _sealed_design()
    blue = _blue_ready(design, design_seal)
    stage2 = Stage2Runner(FixtureJsonGenerator([{"action": "submit", "capability.py": source}])).run(
        design, design_seal, blue.stage2_authorization, _bundle()
    )
    assert stage2.status == "SUBMITTED"
    return design, design_seal, stage2, blue


def _go2_a_result(source: str | None = None):
    body = copy.deepcopy(_design_body())
    body["capabilities"][0]["inputs"] = [{
        "name": "q", "type": "number", "shape": "vector:20", "unit": "rad", "frame": "joint", "required": True,
    }]
    design_result = Stage1Runner(FixtureJsonGenerator([body])).run("run-validation-go2", ROBOT, TASKS, G2)
    assert design_result.status == "SEALED" and design_result.capability_design and design_result.seal
    design = design_result.capability_design
    design_seal = design_result.seal
    blue = _blue_ready(design, design_seal, input_name="q", input_value=[0.0] * 20)
    candidate_source = _go2_source() if source is None else source
    stage2 = Stage2Runner(FixtureJsonGenerator([{"action": "submit", "capability.py": candidate_source}])).run(
        design, design_seal, blue.stage2_authorization, GO2_IMPLEMENTATION_BUNDLE
    )
    assert stage2.status == "SUBMITTED"
    result = ValidationARunner(GO2_PROFILE).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": candidate_source}, stage2.implementation_manifest, stage2.manifest_seal,
        stage2.implementation_bundle_hash,
    )
    return result, candidate_source, stage2


def _context(design: dict, design_seal: dict, blue) -> ValidationContext:
    snapshot = {
        "artifact_type": "validation_run_snapshot",
        "schema_version": "1.0.0",
        "design_hash": content_hash(canonical_bytes(design)),
        "blue_line_spec_hash": blue.spec_hash,
        "blue_line_manifest_hash": blue.manifest_hash,
        "suite_hash": blue.suite_hash,
        "implementation_bundle_hash": BUNDLE_HASH,
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
        blue_line_ready_bundle=blue.validation_authorization,
        run_snapshot=snapshot,
    )


def _a_result(source: str = _source()):
    design, design_seal, stage2, blue = _stage2_submission(source)
    result = ValidationARunner(PROFILE).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": source}, stage2.implementation_manifest, stage2.manifest_seal,
        stage2.implementation_bundle_hash,
    )
    return design, design_seal, stage2, blue, result


def test_validation_a_profile_checks_facade_envelopes_and_opaque_handle() -> None:
    design, design_seal, stage2, blue, result = _a_result()
    assert result.status == "PASS"
    assert result.implementation_bundle_hash == stage2.bundle_hash
    assert result.binding_result["implementation_bundle_hash"] == stage2.bundle_hash
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

    wrong_bundle_hash = content_hash(b"wrong-implementation-bundle")
    wrong_bundle_result = ValidationARunner(PROFILE).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest,
        stage2.manifest_seal, wrong_bundle_hash,
    )
    assert wrong_bundle_result.status == "FAIL"
    assert {item["code"] for item in wrong_bundle_result.diagnostics} == {
        "IMPLEMENTATION_MANIFEST", "IMPLEMENTATION_MANIFEST_SEAL",
    }


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


def test_validation_a_accepts_real_shaped_go2_construction_and_sdk_derived_mutations() -> None:
    result, source, stage2 = _go2_a_result()
    assert source.count(".q =") == 20
    assert result.status == "PASS"
    assert result.candidate_handle is not None
    assert result.implementation_bundle_hash == stage2.bundle_hash == GO2_BUNDLE_HASH

    alias_source = _go2_source().replace("    _sdk.publisher.Write(cmd)", "    publisher = _sdk.publisher\n    publisher.Write(cmd)")
    alias_result, _alias_source, _alias_stage2 = _go2_a_result(alias_source)
    assert alias_result.status == "PASS"


def test_validation_a_rejects_sdk_mutation_unapproved_roots_and_input_calls() -> None:
    cases = {
        "sdk_mutation": _go2_source().replace("    cmd = _sdk.LowCmd_()", "    _sdk.LowCmd_ = arg_q\n    cmd = _sdk.LowCmd_()"),
        "unapproved_root": _go2_source().replace("_sdk.LowCmd_()", "_sdk.Hidden()"),
        "input_call": _go2_source().replace("_sdk.publisher.Write(cmd)", "arg_q.execute()"),
        "endpoint_alias_mutation": _go2_source().replace(
            "    _sdk.publisher.Write(cmd)",
            "    endpoint = _sdk.publisher\n    endpoint.some_internal_field = arg_q[0]\n    _sdk.publisher.Write(cmd)",
        ),
        "global": _go2_source().replace("def capability_reach_joint_target(arg_q, *, _sdk):", "def capability_reach_joint_target(arg_q, *, _sdk):\n    global external"),
        "nested_function": _go2_source().replace("    cmd = _sdk.LowCmd_()", "    def nested():\n        return 1\n    cmd = _sdk.LowCmd_()"),
        "nested_class": _go2_source().replace("    cmd = _sdk.LowCmd_()", "    class Nested:\n        pass\n    cmd = _sdk.LowCmd_()"),
    }
    for name, source in cases.items():
        result, _source_text, _stage2 = _go2_a_result(source)
        assert result.status == "FAIL", name

    assert "SDK_FACADE" in {item["code"] for item in _go2_a_result(cases["unapproved_root"])[0].diagnostics}
    assert "FORBIDDEN_CALL" in {item["code"] for item in _go2_a_result(cases["input_call"])[0].diagnostics}


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
    assert route_or_video.status == "INFRASTRUCTURE_ERROR"
    assert {"SDK_ROUTE_EVIDENCE", "HARNESS_INFRASTRUCTURE"}.issubset(
        {code for execution in route_or_video.executions for code in execution["failure_codes"]}
    )
    forged_video = ValidationBRunner(_FixedHarness(context.run_snapshot, ["forged_video", "pass"])).run(
        bind_candidate_to_suite(a_result, blue.suite_hash), context
    )
    assert forged_video.status == "INFRASTRUCTURE_ERROR"
    assert "HARNESS_INFRASTRUCTURE" in {
        code for execution in forged_video.executions for code in execution["failure_codes"]
    }
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
        blue_line_ready_bundle=context.blue_line_ready_bundle,
        run_snapshot=context.run_snapshot,
    )
    with pytest.raises(ContractError):
        runner.run(bind_candidate_to_suite(a_result, blue.suite_hash), bad_context)
    with pytest.raises(ContractError):
        runner.run(a_result.candidate_handle, context)  # type: ignore[arg-type]
    with pytest.raises(ContractError):
        runner.run(object(), context)  # type: ignore[arg-type]
    with pytest.raises(AttributeError):
        a_result.candidate_handle._source = "replacement"  # type: ignore[attr-defined]
    assert tampered is not None


def test_validation_b_routes_candidate_execution_through_typed_session() -> None:
    design, design_seal, _stage2, blue, a_result = _a_result(_source("PASS"))
    context = _context(design, design_seal, blue)
    harness = _FixedHarness(context.run_snapshot, ["pass", "pass"])
    result = ValidationBRunner(harness).run(
        bind_candidate_to_suite(a_result, blue.suite_hash), context
    )
    assert result.status == "PASS"
    assert len(harness.sessions) == 2
    assert all(len(session.invoke_calls) == 1 for session in harness.sessions)


def test_validation_b_rejects_self_resealed_rules_without_blue_line_ready_origin() -> None:
    design, design_seal, _stage2, blue, a_result = _a_result(_source("PASS"))
    context = _context(design, design_seal, blue)
    forged_spec = copy.deepcopy(context.blue_line_spec)
    forged_spec["capability_specs"][0]["threshold"]["value"] = 999.0
    forged_spec_hash = content_hash(canonical_bytes(forged_spec))
    forged_spec_seal = create_seal(
        "blue_line_validation_spec", forged_spec_hash, list(context.spec_seal["parents"])
    )

    forged_suite = copy.deepcopy(context.validation_suite)
    forged_suite["spec_hash"] = forged_spec_hash
    forged_suite["capability_cases"][0]["threshold"]["value"] = 999.0
    forged_suite_hash = content_hash(canonical_bytes(forged_suite))
    forged_suite_parents = [
        forged_spec_hash if parent == blue.spec_hash else parent
        for parent in context.suite_seal["parents"]
    ]
    forged_suite_seal = create_seal(
        "validation_b_suite", forged_suite_hash, forged_suite_parents
    )

    forged_manifest = copy.deepcopy(context.blue_line_manifest)
    forged_manifest["spec_hash"] = forged_spec_hash
    forged_manifest["suite_hash"] = forged_suite_hash
    forged_manifest_hash = content_hash(canonical_bytes(forged_manifest))
    forged_manifest_parents = [
        forged_spec_hash if parent == blue.spec_hash else parent
        for parent in context.manifest_seal["parents"]
    ]
    forged_manifest_seal = create_seal(
        "blue_line_manifest", forged_manifest_hash, forged_manifest_parents
    )
    forged_snapshot = copy.deepcopy(context.run_snapshot)
    forged_snapshot["blue_line_spec_hash"] = forged_spec_hash
    forged_snapshot["blue_line_manifest_hash"] = forged_manifest_hash
    forged_snapshot["suite_hash"] = forged_suite_hash
    forged_context = ValidationContext(
        capability_design=context.capability_design,
        design_seal=context.design_seal,
        blue_line_spec=forged_spec,
        spec_seal=forged_spec_seal,
        blue_line_manifest=forged_manifest,
        manifest_seal=forged_manifest_seal,
        validation_suite=forged_suite,
        suite_seal=forged_suite_seal,
        blue_line_ready_bundle=context.blue_line_ready_bundle,
        run_snapshot=forged_snapshot,
    )
    with pytest.raises(ContractError, match="lineage is not sealed and READY"):
        ValidationBRunner(_FixedHarness(forged_snapshot, ["pass", "pass"])).run(
            bind_candidate_to_suite(a_result, forged_suite_hash), forged_context
        )


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
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest, stage2.manifest_seal, context, _bundle(),
    )
    assert no_change.status == "NO_CHANGE"
    assert no_change.repair_invocations_used == no_change.repairs_consumed == 1
    assert no_change.candidate_revisions_created == 0
    assert len(no_change.run_ledger) == 1
    assert "candidate_source" not in no_change.repair_log[0]
    assert design == frozen_design
    assert stage2.binding_contract == frozen_binding
    assert context == frozen_context

    harness = _FixedHarness(context.run_snapshot, ["fail", "fail", "pass", "pass"])
    repaired = RepairRunner(
        ValidationARunner(PROFILE), ValidationBRunner(harness),
        lambda _request: {"capability.py": _source("REPAIRED"), "llm_calls": 2},
    ).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest, stage2.manifest_seal, context, _bundle(),
    )
    assert repaired.status == "PASS"
    assert repaired.first_passing_repair_index == repaired.repair_invocations_used == repaired.repairs_consumed == 1
    assert repaired.candidate_revisions_created == 1
    assert repaired.repair_llm_calls == 2
    assert repaired.run_snapshot_hash == content_hash(canonical_bytes(context.run_snapshot))
    assert repaired.repair_log[0]["candidate_revision_created"] is True
    assert repaired.repair_log[0]["candidate_source"] == _source("REPAIRED")

    outputs = iter([_source("HISTORICAL_NEW"), _source("INITIAL")])
    historical = RepairRunner(
        ValidationARunner(PROFILE),
        ValidationBRunner(_FixedHarness(context.run_snapshot, ["fail", "fail", "fail", "fail"])),
        lambda _request: {"capability.py": next(outputs), "llm_calls": 1},
    ).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest, stage2.manifest_seal, context, _bundle(),
    )
    assert historical.status == "NO_CHANGE"
    assert historical.repair_invocations_used == historical.repairs_consumed == 2
    assert historical.candidate_revisions_created == 1
    assert len(historical.run_ledger) == 2

    comment_only = RepairRunner(
        ValidationARunner(PROFILE),
        ValidationBRunner(_FixedHarness(context.run_snapshot, ["fail", "fail"])),
        lambda _request: {"capability.py": stage2.capability_source + "\n# no executable change\n", "llm_calls": 1},
    ).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest,
        stage2.manifest_seal, context, _bundle(),
    )
    assert comment_only.status == "NO_EXECUTABLE_CHANGE"
    assert comment_only.repair_invocations_used == 1
    assert comment_only.candidate_revisions_created == 0
    assert len(comment_only.run_ledger) == 1
    assert "candidate_source" not in comment_only.repair_log[0]


def test_repair_retries_infrastructure_on_same_revision_without_llm_and_caps_at_ten() -> None:
    design, design_seal, stage2, blue = _stage2_submission(_source("INITIAL"))
    context = _context(design, design_seal, blue)
    harness = _FixedHarness(context.run_snapshot, ["fail", "fail", "infra", "pass", "pass"])
    result = RepairRunner(
        ValidationARunner(PROFILE), ValidationBRunner(harness),
        lambda _request: {"capability.py": _source("REPAIRED"), "llm_calls": 1},
    ).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest, stage2.manifest_seal, context, _bundle(),
    )
    assert result.status == "PASS"
    assert result.repair_invocations_used == result.repairs_consumed == 1
    assert result.candidate_revisions_created == 1
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
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest, stage2.manifest_seal, context, _bundle(),
    )
    assert capped.status == "FAILED_AFTER_REPAIRS"
    assert capped.repair_invocations_used == capped.repairs_consumed == 10
    assert capped.candidate_revisions_created == capped.repair_llm_calls == calls == 10


def test_repair_invalid_source_consumes_invocation_but_not_revision() -> None:
    design, design_seal, stage2, blue = _stage2_submission(_source("INITIAL"))
    context = _context(design, design_seal, blue)
    outputs = iter(["def broken(", _source("REPAIRED")])
    result = RepairRunner(
        ValidationARunner(PROFILE),
        ValidationBRunner(_FixedHarness(context.run_snapshot, ["fail", "fail", "pass", "pass"])),
        lambda _request: {"capability.py": next(outputs), "llm_calls": 1},
    ).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest,
        stage2.manifest_seal, context, _bundle(),
    )
    assert result.status == "PASS"
    assert result.first_passing_repair_index == 2
    assert result.repair_invocations_used == result.repairs_consumed == 2
    assert result.candidate_revisions_created == 1
    assert result.repair_log[0]["diagnostics"][0]["code"] == "REPAIR_SOURCE_SYNTAX"
    assert [entry["repair_invocation_index"] for entry in result.run_ledger] == [0, 2]


def test_repair_reuses_exact_bundle_and_rejects_manifest_or_snapshot_drift() -> None:
    design, design_seal, stage2, blue = _stage2_submission(_source("INITIAL"))
    context = _context(design, design_seal, blue)
    requests: list[dict] = []

    def repair(request):
        requests.append(copy.deepcopy(dict(request)))
        return {"capability.py": _source("REPAIRED"), "llm_calls": 1}

    result = RepairRunner(
        ValidationARunner(PROFILE),
        ValidationBRunner(_FixedHarness(context.run_snapshot, ["fail", "fail", "pass", "pass"])),
        repair,
    ).run(
        design, design_seal, stage2.binding_contract, stage2.binding_seal,
        {"capability.py": stage2.capability_source}, stage2.implementation_manifest,
        stage2.manifest_seal, context, _bundle(),
    )
    assert result.status == "PASS"
    assert requests[0]["implementation_bundle"] == IMPLEMENTATION_BUNDLE
    assert requests[0]["implementation_bundle_hash"] == BUNDLE_HASH
    assert result.final_validation_a.implementation_bundle_hash == BUNDLE_HASH
    assert result.final_validation_a.binding_result["implementation_bundle_hash"] == BUNDLE_HASH
    assert result.final_validation_b.report["implementation_bundle_hash"] == BUNDLE_HASH
    assert result.frozen_artifact_hashes["implementation_bundle_hash"] == BUNDLE_HASH

    changed_bundle = _bundle()
    changed_bundle["robot_implementation_facts"]["position_unit"] = "degree"
    with pytest.raises(ContractError):
        RepairRunner(ValidationARunner(PROFILE), ValidationBRunner(_FixedHarness(context.run_snapshot, [])), repair).run(
            design, design_seal, stage2.binding_contract, stage2.binding_seal,
            {"capability.py": stage2.capability_source}, stage2.implementation_manifest,
            stage2.manifest_seal, context, changed_bundle,
        )

    changed_context = copy.deepcopy(context)
    changed_context.run_snapshot["implementation_bundle_hash"] = content_hash(b"other-bundle")
    with pytest.raises(ContractError):
        RepairRunner(ValidationARunner(PROFILE), ValidationBRunner(_FixedHarness(changed_context.run_snapshot, [])), repair).run(
            design, design_seal, stage2.binding_contract, stage2.binding_seal,
            {"capability.py": stage2.capability_source}, stage2.implementation_manifest,
            stage2.manifest_seal, changed_context, _bundle(),
        )
