from __future__ import annotations

import copy
import json

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
    STAGE2_PROMPT,
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


def _sealed_two_capability_design(run_id: str = "run-stage2-two") -> tuple[dict, dict]:
    body = _design_body()
    second = copy.deepcopy(body["capabilities"][0])
    second["capability_id"] = "hold-joint-target"
    second["requirement_ids"] = ["req-hold"]
    second["effect"] = "The selected joint holds the requested public target."
    body["capabilities"].append(second)
    tasks = TASKS + [{"requirement_id": "req-hold", "description": "Hold a public joint target safely."}]
    result = Stage1Runner(FixtureJsonGenerator([body])).run(run_id, ROBOT, tasks, G2)
    assert result.status == "SEALED" and result.capability_design and result.seal
    return result.capability_design, result.seal


def test_stage2_prompt_describes_the_validation_a_source_contract() -> None:
    assert "exactly one JSON action object" in STAGE2_PROMPT
    assert '"action":"sandbox"' in STAGE2_PROMPT
    assert '"action":"submit"' in STAGE2_PROMPT
    assert '"action":"blocked"' in STAGE2_PROMPT
    assert "complete parseable Python module" in STAGE2_PROMPT
    for import_name in ("math", "time", "numpy"):
        assert import_name in STAGE2_PROMPT
    assert "private non-dunder helper functions" in STAGE2_PROMPT
    assert "exactly the bound public functions" in STAGE2_PROMPT
    assert "approved math/time/numpy members" in STAGE2_PROMPT
    assert "exact `_sdk` members" in STAGE2_PROMPT
    for framework_rule in (
        "invented attributes on",
        "implementation_bundle",
        "ChannelFactoryInitialize",
        "local endpoints",
        "not a second SDK connection",
        "module-global `_sdk`",
        "direct facade operations and no constructors",
        "leave lifecycle to Framework",
    ):
        assert framework_rule in STAGE2_PROMPT
    for forbidden in ("Markdown fences", "dynamic imports", "eval/exec/open"):
        assert forbidden in STAGE2_PROMPT
    for iteration_rule in (
        "sandbox_contract",
        "submission_requirements",
        "varied public probes",
        "Revise the working source from public Sandbox feedback",
        "premature submit is",
        "working source is retained",
    ):
        assert iteration_rule in STAGE2_PROMPT
    assert "rt/lowcmd" not in STAGE2_PROMPT
    assert "unitree_go_msg_dds__LowCmd_" not in STAGE2_PROMPT


def _authorization(design: dict, seal: dict):
    capability_specs = []
    for capability in design["capabilities"]:
        capability_specs.append({
            "capability_id": capability["capability_id"],
            "measurement": {"measurement_id": "joint-error", "entity": "shoulder_pan", "unit": "rad", "frame": "joint"},
            "metric": "max_joint_error",
            "threshold": {"comparator": "<=", "value": 0.05},
            "dwell_s": 0.2,
            "timeout_s": 2.0,
            "aggregation": "ALL",
            "guard_ids": ["physical-state-not-command-receipt"],
            "cases": [{"case_id": "nominal", "initial_state": {"joint": 0.0}, "inputs": {"target": 0.2}}],
            "lineage": {"kind": "COPIED", "standard_id": "joint-arrival", "material": False},
        })
    spec = {"capability_specs": capability_specs}
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
        "implementation_bundle", "sandbox_contract", "submission_requirements",
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


def test_repair_episode_reuses_stage2_agent_source_and_sandbox_until_public_ok() -> None:
    design, seal = _sealed_design()
    binding = derive_python_binding(design, seal)
    source = binding.starter_skeleton
    repaired_source = source + "\nREPAIR_MARKER = 1\n"
    responses = [
        {"action": "submit", "capability.py": source},
        {"action": "sandbox", "capability.py": source + "\n# first repair\n", "probe": {"probe_id": "repair-1", "capability_id": "reach-joint-target"}},
        {"action": "sandbox", "capability.py": source + "\n# second repair\n", "probe": {"probe_id": "repair-2", "capability_id": "reach-joint-target"}},
        {"action": "sandbox", "capability.py": repaired_source, "probe": {"probe_id": "repair-3", "capability_id": "reach-joint-target"}},
        {"action": "submit", "capability.py": repaired_source},
    ]
    generator = FixtureJsonGenerator(responses)
    feedbacks = iter((
        {"status": "ERROR", "summary": "The public probe failed.", "observations": {}, "exception": "public_error"},
        {"status": "INCONCLUSIVE", "summary": "The public probe was inconclusive.", "observations": {"progress": 0.1}, "exception": None},
        {"status": "OK", "summary": "The public probe completed.", "observations": {"progress": 1.0}, "exception": None},
    ))
    sandbox_sources: list[str] = []
    sandbox = CallbackSandbox(
        lambda candidate, _probe: (sandbox_sources.append(candidate) or next(feedbacks))
    )
    runner = Stage2Runner(
        generator,
        sandbox=sandbox,
        config=Stage2Config(max_llm_calls=1),
    )
    initial = runner.run(design, seal, _authorization(design, seal), _bundle())
    assert initial.status == "SUBMITTED"

    repaired = runner.repair_episode({
        "repair_index": 1,
        "capability.py": source,
        "diagnostics": [{"gate": "B", "code": "THRESHOLD", "message": "private threshold=0.1"}],
    })
    assert repaired == {"capability.py": repaired_source, "llm_calls": 4}
    trace = runner.last_repair_trace
    assert trace is not None
    assert trace["submitted"] is True
    assert trace["llm_calls"] == 4
    assert [item["status"] for item in trace["sandbox_log"]] == ["ERROR", "INCONCLUSIVE", "OK"]
    assert sandbox_sources == [
        source + "\n# first repair\n",
        source + "\n# second repair\n",
        repaired_source,
    ]
    repair_inputs = [call["inputs"] for call in generator.calls[1:]]
    assert all("THRESHOLD" not in json.dumps(inputs) for inputs in repair_inputs)
    assert all("private threshold" not in json.dumps(inputs).lower() for inputs in repair_inputs)
    assert all(call["stage"] == "stage2" for call in generator.calls)


def test_repair_episode_exhausts_without_sandbox_ok_and_never_submits() -> None:
    design, seal = _sealed_design()
    binding = derive_python_binding(design, seal)
    source = binding.starter_skeleton
    generator = FixtureJsonGenerator(
        lambda _stage, _prompt, _inputs: {"action": "submit", "capability.py": source}
    )
    sandbox_calls: list[dict] = []
    runner = Stage2Runner(
        generator,
        sandbox=CallbackSandbox(lambda _source, probe: sandbox_calls.append(probe) or {
            "status": "OK", "summary": "unexpected", "observations": {},
        }),
        config=Stage2Config(max_llm_calls=1),
    )
    assert runner.run(design, seal, _authorization(design, seal), _bundle()).status == "SUBMITTED"
    result = runner.repair_episode({"capability.py": source, "diagnostics": []})
    assert result["llm_calls"] == 20
    assert result["capability.py"] == source
    assert runner.last_repair_trace is not None
    assert runner.last_repair_trace["submitted"] is False
    assert runner.last_repair_trace["sandbox_log"] == []
    assert sandbox_calls == []


def test_repair_episode_continues_after_blocked_until_distinct_source_submits() -> None:
    design, seal = _sealed_design()
    binding = derive_python_binding(design, seal)
    source = binding.starter_skeleton
    repaired_source = source + "\nREPAIR_MARKER = 1\n"
    generator = FixtureJsonGenerator([
        {"action": "submit", "capability.py": source},
        {"action": "blocked", "reason": "The public implementation route is temporarily unavailable."},
        {"action": "sandbox", "capability.py": repaired_source, "probe": {"probe_id": "repair-1", "capability_id": "reach-joint-target"}},
        {"action": "submit", "capability.py": repaired_source},
    ])
    runner = Stage2Runner(
        generator,
        sandbox=CallbackSandbox(lambda _source, _probe: {
            "status": "OK", "summary": "public probe completed", "observations": {},
        }),
        config=Stage2Config(max_llm_calls=1),
    )
    assert runner.run(design, seal, _authorization(design, seal), _bundle()).status == "SUBMITTED"

    repaired = runner.repair_episode({"capability.py": source, "diagnostics": []})

    assert repaired == {"capability.py": repaired_source, "llm_calls": 3}
    trace = runner.last_repair_trace
    assert trace is not None and trace["submitted"] is True
    assert trace["call_log"][0]["diagnostics"] == [{
        "code": "REPAIR_CONTINUE_REQUIRED",
        "message": "Repair episode remains open; continue from the retained working source.",
    }]


def test_repair_episode_rejects_known_source_then_accepts_distinct_source() -> None:
    design, seal = _sealed_design()
    binding = derive_python_binding(design, seal)
    source = binding.starter_skeleton
    repaired_source = source + "\nREPAIR_MARKER = 2\n"
    generator = FixtureJsonGenerator([
        {"action": "submit", "capability.py": source},
        {"action": "sandbox", "capability.py": source, "probe": {"probe_id": "repair-old", "capability_id": "reach-joint-target"}},
        {"action": "submit", "capability.py": source},
        {"action": "sandbox", "capability.py": repaired_source, "probe": {"probe_id": "repair-new", "capability_id": "reach-joint-target"}},
        {"action": "submit", "capability.py": repaired_source},
    ])
    runner = Stage2Runner(
        generator,
        sandbox=CallbackSandbox(lambda _source, _probe: {
            "status": "OK", "summary": "public probe completed", "observations": {},
        }),
        config=Stage2Config(max_llm_calls=1),
    )
    assert runner.run(design, seal, _authorization(design, seal), _bundle()).status == "SUBMITTED"

    repaired = runner.repair_episode({"capability.py": source, "diagnostics": []})

    assert repaired == {"capability.py": repaired_source, "llm_calls": 4}
    trace = runner.last_repair_trace
    assert trace is not None and trace["submitted"] is True
    assert trace["call_log"][1]["diagnostics"] == [{
        "code": "NO_CHANGE_SUBMISSION",
        "message": "Repair submission matches a known source; continue with a distinct source.",
    }]
    assert [item["status"] for item in trace["sandbox_log"]] == ["OK", "OK"]


def test_repair_episode_exhausts_exactly_twenty_blocked_actions() -> None:
    design, seal = _sealed_design()
    binding = derive_python_binding(design, seal)
    source = binding.starter_skeleton
    generator = FixtureJsonGenerator([
        {"action": "submit", "capability.py": source},
        *[
            {"action": "blocked", "reason": "The public implementation route is unavailable."}
            for _ in range(20)
        ],
    ])
    runner = Stage2Runner(
        generator,
        sandbox=CallbackSandbox(lambda _source, _probe: {
            "status": "OK", "summary": "unexpected", "observations": {},
        }),
        config=Stage2Config(max_llm_calls=1),
    )
    assert runner.run(design, seal, _authorization(design, seal), _bundle()).status == "SUBMITTED"

    repaired = runner.repair_episode({"capability.py": source, "diagnostics": []})

    assert repaired == {"capability.py": source, "llm_calls": 20}
    trace = runner.last_repair_trace
    assert trace is not None
    assert trace["submitted"] is False
    assert trace["status"] == "CALL_LIMIT_EXHAUSTED"
    assert len(trace["call_log"]) == 20
    assert trace["diagnostics"] == [{
        "code": "CALL_LIMIT_EXHAUSTED",
        "message": "Repair episode reached its call limit without a distinct submission.",
    }]


def test_sandbox_contract_is_closed_and_feedback_redacts_private_fields() -> None:
    sandbox = CallbackSandbox(
        lambda _source, _probe: {
            "status": "OK",
            "summary": "public execution completed",
            "observations": {"truth": 0.2},
        }
    )
    contract = sandbox.contract
    assert contract["execution"] == {
        "mode": "callback_only",
        "direct_handle_access": False,
        "candidate_input": "complete capability.py source",
        "probe_input": "public JSON object",
    }
    assert contract["probe"]["coverage_identity_fields"] == ["probe_id", "capability_id"]
    contract["execution"]["mode"] = "changed"
    assert sandbox.contract["execution"]["mode"] == "callback_only"

    feedback = sandbox.run("def capability_example():\n    return 1\n", {
        "probe_id": "probe-1",
        "capability_id": "capability-example",
    })
    assert feedback == {
        "status": "ERROR",
        "summary": "Sandbox callback returned invalid public feedback.",
        "observations": {},
        "exception": "sandbox_feedback_contract_error",
    }


def test_custom_sandbox_contract_is_public_exact_and_reaches_stage2_isolated() -> None:
    robot_contract = {
        "artifact_type": "go2_public_probe_contract",
        "schema_version": "1.0.0",
        "capability_probes": [{
            "capability_id": "reach-joint-target",
            "probe_id": "go2-reach-public",
            "inputs": {"target": "public joint target"},
        }],
    }
    sandbox = CallbackSandbox(
        lambda _source, _probe: {
            "status": "OK",
            "summary": "public execution completed",
            "observations": {},
        },
        contract=robot_contract,
    )
    robot_contract["capability_probes"][0]["probe_id"] = "mutated-after-construction"
    returned = sandbox.contract
    assert returned["robot_contract"] == {
        "artifact_type": "go2_public_probe_contract",
        "schema_version": "1.0.0",
        "capability_probes": [{
            "capability_id": "reach-joint-target",
            "probe_id": "go2-reach-public",
            "inputs": {"target": "public joint target"},
        }],
    }
    assert returned["execution"]["mode"] == "callback_only"
    returned["robot_contract"]["capability_probes"][0]["probe_id"] = "mutated-return-value"
    assert sandbox.contract["robot_contract"]["capability_probes"][0]["probe_id"] == "go2-reach-public"

    with pytest.raises(ContractError):
        CallbackSandbox(lambda _source, _probe: {}, contract={"private_criteria": "not public"})

    design, seal = _sealed_design()
    source = derive_python_binding(design, seal).starter_skeleton
    fixture = FixtureJsonGenerator([{"action": "submit", "capability.py": source}])
    Stage2Runner(fixture, sandbox=sandbox).run(
        design, seal, _authorization(design, seal), _bundle()
    )
    assert fixture.calls[0]["inputs"]["sandbox_contract"]["robot_contract"] == sandbox.contract[
        "robot_contract"
    ]
    fixture.calls[0]["inputs"]["sandbox_contract"]["robot_contract"]["capability_probes"][0]["probe_id"] = "mutated-input"
    assert sandbox.contract["robot_contract"]["capability_probes"][0]["probe_id"] == "go2-reach-public"


def test_unknown_capability_id_never_counts_as_design_coverage() -> None:
    design, seal = _sealed_design()
    source = derive_python_binding(design, seal).starter_skeleton
    fixture = FixtureJsonGenerator([
        {"action": "submit", "capability.py": source},
        {
            "action": "sandbox",
            "capability.py": source,
            "probe": {
                "probe_id": "reach-joint-target",
                "capability_id": "unknown-capability",
                "target": 0.2,
            },
        },
        {"action": "submit", "capability.py": source},
    ])
    result = Stage2Runner(
        fixture,
        sandbox=CallbackSandbox(
            lambda _source, _probe: {
                "status": "OK",
                "summary": "public probe completed",
                "observations": {},
            }
        ),
        config=Stage2Config(
            max_llm_calls=3,
            min_llm_calls_before_submit=3,
            min_successful_sandbox_calls_before_submit=1,
            require_all_design_capability_probes=True,
        ),
    ).run(design, seal, _authorization(design, seal), _bundle())

    assert result.status == "CALL_LIMIT_EXHAUSTED"
    assert result.covered_sandbox_capability_ids == ()
    assert result.sandbox_log[0]["probe_id"] == "reach-joint-target"
    assert result.sandbox_log[0]["capability_id"] == "unknown-capability"
    assert result.sandbox_log[0]["coverage_counted"] is False
    assert "missing required public sandbox capability IDs: reach-joint-target" in result.diagnostics[-1]["message"]


def test_stage2_rejects_premature_submit_until_calls_sandbox_and_capability_coverage_are_met() -> None:
    design, seal = _sealed_design()
    binding = derive_python_binding(design, seal)
    source = binding.starter_skeleton
    probes = [
        {"probe_id": "probe-a", "capability_id": "reach-joint-target", "target": 0.1},
        {"probe_id": "probe-b", "capability_id": "reach-joint-target", "target": 0.2},
        {"probe_id": "", "capability_id": "reach-joint-target", "target": 0.25},
        {"probe_id": "probe-c", "capability_id": "reach-joint-target", "target": 0.3},
    ]
    responses = [
        {"action": "submit", "capability.py": source},
        {"action": "sandbox", "capability.py": source, "probe": probes[0]},
        {"action": "sandbox", "capability.py": source, "probe": probes[1]},
        {"action": "sandbox", "capability.py": source, "probe": probes[2]},
        {"action": "submit", "capability.py": source},
        {"action": "sandbox", "capability.py": source, "probe": probes[3]},
        {"action": "submit", "capability.py": source},
        {"action": "submit", "capability.py": source},
        {"action": "submit", "capability.py": source},
        {"action": "submit", "capability.py": source},
    ]
    fixture = FixtureJsonGenerator(responses)
    sandbox = CallbackSandbox(
        lambda _source, probe: {
            "status": "OK",
            "summary": f"public probe {probe.get('probe_id', 'unidentified')} completed",
            "observations": {"joint": probe.get("target", 0.0)},
        }
    )
    config = Stage2Config(
        max_llm_calls=10,
        min_llm_calls_before_submit=10,
        min_successful_sandbox_calls_before_submit=3,
        required_sandbox_capability_ids=("reach-joint-target",),
    )
    result = Stage2Runner(fixture, sandbox=sandbox, config=config).run(
        design, seal, _authorization(design, seal), _bundle()
    )

    assert result.status == "SUBMITTED"
    assert result.llm_calls == 10
    assert result.sandbox_calls == 4
    assert result.successful_sandbox_calls == 4
    assert result.covered_sandbox_capability_ids == ("reach-joint-target",)
    assert result.sandbox_log[2]["successful"] is True
    assert result.sandbox_log[2]["coverage_counted"] is False
    assert any("missing required public sandbox capability IDs: reach-joint-target" in item["message"] for item in result.call_log[0]["diagnostics"])
    assert any("min_llm_calls_before_submit is 10" in item["message"] for item in result.call_log[4]["diagnostics"])
    assert fixture.calls[1]["inputs"]["working_capability.py"] == source
    assert fixture.calls[1]["inputs"]["public_diagnostics"] == list(result.call_log[0]["diagnostics"])
    assert fixture.calls[0]["inputs"]["submission_requirements"] == {
        "max_llm_calls": 10,
        "min_llm_calls_before_submit": 10,
        "min_successful_sandbox_calls_before_submit": 3,
        "required_sandbox_capability_ids": ["reach-joint-target"],
        "require_all_design_capability_probes": False,
    }


def test_stage2_can_require_probe_coverage_for_all_runtime_design_capabilities() -> None:
    design, seal = _sealed_design()
    source = derive_python_binding(design, seal).starter_skeleton
    fixture = FixtureJsonGenerator([
        {"action": "submit", "capability.py": source},
        {
            "action": "sandbox",
            "capability.py": source,
            "probe": {
                "probe_id": "independent-public-probe",
                "capability_id": "reach-joint-target",
                "target": 0.2,
            },
        },
        {"action": "submit", "capability.py": source},
    ])
    result = Stage2Runner(
        fixture,
        sandbox=CallbackSandbox(
            lambda _source, _probe: {
                "status": "OK",
                "summary": "public probe completed",
                "observations": {},
            }
        ),
        config=Stage2Config(
            max_llm_calls=3,
            min_llm_calls_before_submit=3,
            min_successful_sandbox_calls_before_submit=1,
            require_all_design_capability_probes=True,
        ),
    ).run(design, seal, _authorization(design, seal), _bundle())

    assert result.status == "SUBMITTED"
    assert result.covered_sandbox_capability_ids == ("reach-joint-target",)
    assert fixture.calls[1]["inputs"]["sandbox_contract"]["probe"]["coverage_identity_fields"] == [
        "probe_id", "capability_id"
    ]
    assert fixture.calls[0]["inputs"]["submission_requirements"]["required_sandbox_capability_ids"] == [
        "reach-joint-target"
    ]


def test_stage2_sandbox_coverage_is_bound_to_the_exact_submitted_source() -> None:
    design, seal = _sealed_two_capability_design()
    binding = derive_python_binding(design, seal)
    source_a = binding.starter_skeleton + "\nSOURCE_A = 1\n"
    source_b = binding.starter_skeleton + "\nSOURCE_B = 1\n"
    fixture = FixtureJsonGenerator([
        {
            "action": "sandbox",
            "capability.py": source_a,
            "probe": {"probe_id": "probe-x-a", "capability_id": "reach-joint-target"},
        },
        {
            "action": "sandbox",
            "capability.py": source_b,
            "probe": {"probe_id": "probe-y-b", "capability_id": "hold-joint-target"},
        },
        {"action": "submit", "capability.py": source_b},
        {
            "action": "sandbox",
            "capability.py": source_b,
            "probe": {"probe_id": "probe-x-b", "capability_id": "reach-joint-target"},
        },
        {"action": "submit", "capability.py": source_b},
    ])
    runner = Stage2Runner(
        fixture,
        sandbox=CallbackSandbox(lambda _source, _probe: {
            "status": "OK",
            "summary": "public probe completed",
            "observations": {},
        }),
        config=Stage2Config(
            max_llm_calls=5,
            require_all_design_capability_probes=True,
        ),
    )

    result = runner.run(design, seal, _authorization(design, seal), _bundle())

    assert result.status == "SUBMITTED"
    assert result.capability_source == source_b
    assert result.covered_sandbox_capability_ids == (
        "hold-joint-target",
        "reach-joint-target",
    )
    assert "missing required public sandbox capability IDs: reach-joint-target" in (
        result.call_log[2]["diagnostics"][0]["message"]
    )
    assert result.sandbox_log[0]["source_hash"] != result.sandbox_log[1]["source_hash"]
    assert result.sandbox_log[2]["source_hash"] == result.sandbox_log[1]["source_hash"]


def test_repair_episode_does_not_inherit_ok_coverage_after_a_source_edit() -> None:
    design, seal = _sealed_design()
    binding = derive_python_binding(design, seal)
    source = binding.starter_skeleton
    source_a = source + "\nREPAIR_SOURCE_A = 1\n"
    source_b = source + "\nREPAIR_SOURCE_B = 1\n"
    fixture = FixtureJsonGenerator([
        {"action": "submit", "capability.py": source},
        {
            "action": "sandbox",
            "capability.py": source_a,
            "probe": {"probe_id": "repair-a", "capability_id": "reach-joint-target"},
        },
        {"action": "submit", "capability.py": source_b},
        {
            "action": "sandbox",
            "capability.py": source_b,
            "probe": {"probe_id": "repair-b", "capability_id": "reach-joint-target"},
        },
        {"action": "submit", "capability.py": source_b},
    ])
    runner = Stage2Runner(
        fixture,
        sandbox=CallbackSandbox(lambda _source, _probe: {
            "status": "OK",
            "summary": "public probe completed",
            "observations": {},
        }),
        config=Stage2Config(max_llm_calls=1),
    )
    assert runner.run(design, seal, _authorization(design, seal), _bundle()).status == "SUBMITTED"

    repaired = runner.repair_episode({"capability.py": source, "diagnostics": []})

    assert repaired == {"capability.py": source_b, "llm_calls": 4}
    trace = runner.last_repair_trace
    assert trace is not None and trace["submitted"] is True
    assert trace["call_log"][1]["diagnostics"] == [{
        "code": "SUBMISSION_REQUIREMENTS",
        "message": "Submit rejected: min_successful_sandbox_calls_before_submit is 1, but only 0 successful public sandbox calls are recorded.",
    }]


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
