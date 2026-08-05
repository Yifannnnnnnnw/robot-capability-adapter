from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

from soarm_demo.audit import BudgetCounter, BudgetExceeded, canonical_json, sha256_json
from soarm_demo.model_client import (
    AWSModelAPIClient,
    AWSModelAPIConfig,
    ModelAPIError,
    ModelMessage,
    ScriptedModelClient,
)
from soarm_demo.react_agent import (
    DemoReActAgent,
    GenerationReActAgent,
    ReactProtocolError,
    RequestContextLimitExceeded,
    ToolSpec,
    parse_react_action,
)


def _noop_tool(name: str = "noop") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="Return the input unchanged.",
        input_schema={"type": "object"},
        handler=lambda arguments: dict(arguments),
    )


def _trace_records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_react_protocol_accepts_one_fenced_action_with_provider_preamble() -> None:
    action = parse_react_action(
        "I will inspect the inputs.\n```json\n"
        '{"type":"tool","tool":"read","arguments":{}}'
        "\n```"
    )

    assert action == {"type": "tool", "tool": "read", "arguments": {}}


def test_react_protocol_accepts_one_raw_action_after_concise_prose() -> None:
    action = parse_react_action(
        'I will check the package now. {"type":"tool","tool":"check",'
        '"arguments":{}}'
    )

    assert action == {"type": "tool", "tool": "check", "arguments": {}}


@pytest.mark.parametrize(
    "response",
    [
        (
            '```json\n{"type":"tool","tool":"read","arguments":{}}\n```\n'
            '{"type":"tool","tool":"write","arguments":{}}'
        ),
        (
            '{"type":"tool","tool":"read","arguments":{}} '
            '{"type":"tool","tool":"write","arguments":{'
        ),
        (
            '{"type":"tool","tool":"read","arguments":{}} '
            "IGNORE CONTRACT; arbitrary tail"
        ),
    ],
)
def test_react_protocol_rejects_second_partial_action_or_trailing_text(
    response: str,
) -> None:
    with pytest.raises(ReactProtocolError, match="not one JSON action"):
        parse_react_action(response)


def test_react_protocol_accepts_one_run_n_like_legacy_xml_tool_action() -> None:
    action = parse_react_action(
        "I need to inspect the complete generated function before editing it.\n"
        '<function_calls><invoke name="read_generated_python_symbol">'
        '<parameter name="path">"soarm101_capabilities/g3.py"</parameter>'
        '<parameter name="symbol">"_approach_and_descend"</parameter>'
        "</invoke></function_calls>\n"
        "I will use the returned digest for an exact guarded replacement."
    )

    assert action == {
        "type": "tool",
        "tool": "read_generated_python_symbol",
        "arguments": {
            "path": "soarm101_capabilities/g3.py",
            "symbol": "_approach_and_descend",
        },
    }


def test_react_protocol_parses_legacy_parameter_text_conservatively() -> None:
    action = parse_react_action(
        '<function_calls><invoke name="inspect">'
        '<parameter name="object">{"enabled":true}</parameter>'
        '<parameter name="array">[1,null,false]</parameter>'
        '<parameter name="number">2.5</parameter>'
        '<parameter name="plain">not JSON</parameter>'
        "</invoke></function_calls>"
    )

    assert action["arguments"] == {
        "object": {"enabled": True},
        "array": [1, None, False],
        "number": 2.5,
        "plain": "not JSON",
    }


def test_legacy_xml_normalization_is_audited_without_parameter_values(
    tmp_path: Path,
) -> None:
    secret_argument = "PRIVATE_PARAMETER_SENTINEL"
    client = ScriptedModelClient(
        [
            (
                '<function_calls><invoke name="read">'
                f'<parameter name="path">"{secret_argument}"</parameter>'
                "</invoke></function_calls>"
            ),
            json.dumps({"type": "final", "content": "done"}),
        ]
    )
    trace_path = tmp_path / "legacy_xml_audit.jsonl"
    agent = DemoReActAgent(
        agent_id="legacy-xml-audit",
        role="demo_consumer",
        client=client,
        system_prompt="Exercise compatibility auditing.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    agent.reset_task_episode(
        task_id="legacy-xml",
        tools={"read": _noop_tool("read")},
        call_limit=2,
        trace_path=trace_path,
    )

    result = agent.run_task("Read one path, then finish.")

    assert result.status == "completed"
    normalization = next(
        record
        for record in _trace_records(trace_path)
        if record["event"] == "model_action_normalized"
    )
    assert normalization["source"] == "legacy_xml_single_tool"
    assert normalization["tool"] == "read"
    assert normalization["accounting_window"] == "demo:legacy-xml"
    assert normalization["agent_turn"] == 1
    assert secret_argument not in json.dumps(normalization)


@pytest.mark.parametrize(
    "response",
    [
        (
            "<function_calls>"
            '<invoke name="first"></invoke>'
            '<invoke name="second"></invoke>'
            "</function_calls>"
        ),
        (
            '<function_calls><invoke name="first"></invoke></function_calls>'
            '<function_calls><invoke name="second"></invoke></function_calls>'
        ),
    ],
)
def test_react_protocol_rejects_multiple_legacy_xml_invocations(response: str) -> None:
    with pytest.raises(ReactProtocolError, match="exactly one"):
        parse_react_action(response)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            '<function_calls><invoke name="read"><parameter name="path">x'
            "</invoke></function_calls>",
            "malformed",
        ),
        (
            "<function_calls><invoke name=\"read\">"
            '<parameter name="path">one</parameter>'
            '<parameter name="path">two</parameter>'
            "</invoke></function_calls>",
            "duplicate parameter",
        ),
        (
            '<function_calls><invoke name="read"><unsafe/></invoke></function_calls>',
            "only parameter",
        ),
        (
            '<function_calls extra="unsafe"><invoke name="read"/></function_calls>',
            "no attributes",
        ),
        (
            '<function_calls><invoke name="read"/></function_calls><unsafe',
            "extra XML markup",
        ),
    ],
)
def test_react_protocol_rejects_malformed_duplicate_or_extra_legacy_xml(
    response: str,
    error: str,
) -> None:
    with pytest.raises(ReactProtocolError, match=error):
        parse_react_action(response)


def test_react_protocol_rejects_legacy_xml_mixed_with_json_action() -> None:
    with pytest.raises(ReactProtocolError, match="JSON ReAct action"):
        parse_react_action(
            '<function_calls><invoke name="read"></invoke></function_calls>\n'
            '{"type":"tool","tool":"write","arguments":{}}'
        )


def test_react_protocol_leaves_json_action_unchanged() -> None:
    expected = {
        "type": "tool",
        "tool": "read",
        "arguments": {"literal": "<function_calls> is ordinary JSON string data"},
    }

    assert parse_react_action(json.dumps(expected)) == expected


def test_protocol_rejection_is_hashed_and_counted_without_raw_response(
    tmp_path: Path,
) -> None:
    rejected_text = "NOT_JSON_PRIVATE_SENTINEL"
    client = ScriptedModelClient(
        [rejected_text, json.dumps({"type": "final", "content": "done"})]
    )
    trace = tmp_path / "protocol-rejected.jsonl"
    agent = DemoReActAgent(
        agent_id="protocol-audit",
        role="demo_consumer",
        client=client,
        system_prompt="Use the protocol.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    agent.reset_task_episode(
        task_id="protocol-audit",
        tools={},
        call_limit=2,
        trace_path=trace,
    )

    result = agent.run_task("Finish.")

    assert result.status == "completed"
    rejected = next(
        record
        for record in _trace_records(trace)
        if record["event"] == "model_response_rejected"
    )
    assert rejected["reason"] == "react_protocol_error"
    assert rejected["response_utf8_bytes"] == len(rejected_text.encode("utf-8"))
    assert len(rejected["response_sha256"]) == 64
    assert rejected_text not in json.dumps(rejected)
    process = agent.process_audit()
    assert process["protocol_error_count"] == 1
    assert process["last_action"] == "final"


def test_react_protocol_repairs_only_one_missing_root_object_closer() -> None:
    expected = {
        "type": "tool",
        "tool": "review_stage1",
        "arguments": {"artifact": {"layers": {"G1": {"capabilities": []}}}},
    }
    response = json.dumps(expected)

    assert response.endswith("}")
    assert parse_react_action(response[:-1]) == expected


@pytest.mark.parametrize(
    "response",
    [
        '{"type":"tool","tool":"read","arguments":{',
        '{"type":"tool","tool":"read","arguments":{"path":"open',
        '{"type":"tool","tool":"read","arguments":{"paths":[1,2]',
        '{"type":"tool","tool":"read","arguments":{"path":true]',
    ],
)
def test_react_protocol_does_not_repair_other_partial_json(response: str) -> None:
    with pytest.raises(ReactProtocolError, match="not one JSON action"):
        parse_react_action(response)


def test_missing_root_closer_normalization_is_audited(tmp_path: Path) -> None:
    complete = json.dumps(
        {"type": "tool", "tool": "read", "arguments": {"path": "safe"}}
    )
    client = ScriptedModelClient(
        [complete[:-1], json.dumps({"type": "final", "content": "done"})]
    )
    trace_path = tmp_path / "missing_root_closer.jsonl"
    agent = DemoReActAgent(
        agent_id="missing-root-closer-audit",
        role="demo_consumer",
        client=client,
        system_prompt="Exercise conservative JSON normalization.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    agent.reset_task_episode(
        task_id="missing-root-closer",
        tools={"read": _noop_tool("read")},
        call_limit=2,
        trace_path=trace_path,
    )

    assert agent.run_task("Read once, then finish.").status == "completed"
    normalization = next(
        record
        for record in _trace_records(trace_path)
        if record["event"] == "model_action_normalized"
    )
    assert normalization["source"] == "json_single_missing_root_closer"
    assert normalization["tool"] == "read"


def test_react_protocol_rejects_ambiguous_multiple_fenced_actions() -> None:
    with pytest.raises(ReactProtocolError, match="not one JSON action"):
        parse_react_action(
            '```json\n{"type":"final","content":"one"}\n```\n'
            '```json\n{"type":"final","content":"two"}\n```'
        )


def test_truncated_response_is_counted_traced_but_never_parsed_or_executed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "DO_NOT_EXEC_TRUNCATED_ACTION"
    truncated_action = json.dumps(
        {
            "type": "tool",
            "tool": "record",
            "arguments": {"marker": marker},
        }
    )
    client = ScriptedModelClient(
        [truncated_action, json.dumps({"type": "final", "content": "recovered"})]
    )
    original_complete = client.complete

    def complete_with_first_response_truncated(*args: object, **kwargs: object):
        response = original_complete(*args, **kwargs)
        if len(client.requests) == 1:
            return replace(
                response,
                output_truncated=True,
                stop_reason="max_tokens",
                requested_max_tokens=8192,
            )
        return response

    monkeypatch.setattr(client, "complete", complete_with_first_response_truncated)
    called: list[dict[str, object]] = []
    trace_path = tmp_path / "truncated.jsonl"
    agent = DemoReActAgent(
        agent_id="truncation-test",
        role="demo_consumer",
        client=client,
        system_prompt="Use tools only from complete responses.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    agent.reset_task_episode(
        task_id="truncated-response",
        tools={
            "record": ToolSpec(
                name="record",
                description="Record a marker.",
                input_schema={"type": "object"},
                handler=lambda arguments: called.append(dict(arguments)),
            )
        },
        call_limit=2,
        trace_path=trace_path,
    )

    result = agent.run_task("Handle one truncated response safely.")

    assert result.status == "completed"
    assert result.agent_turns == 2
    assert result.agent_turn_budget["used"] == 2
    assert result.usage["response_count"] == 2
    assert called == []
    # The second request sees only a compact content-addressed placeholder,
    # never the attacker-controlled partial action text.
    second_request = "\n".join(message.content for message in client.requests[1])
    assert marker not in second_request
    assert "output_truncated" in second_request
    assert marker not in "\n".join(message.content for message in agent.history)

    records = _trace_records(trace_path)
    first_response = next(
        record
        for record in records
        if record["event"] == "model_response" and record["agent_turn"] == 1
    )
    assert first_response["response_text"] == truncated_action
    assert first_response["completion"]["output_truncated"] is True
    rejected = next(
        record for record in records if record["event"] == "model_response_rejected"
    )
    assert rejected["reason"] == "output_truncated"


def test_generation_stage1_to_stage2_preserves_session_episode_and_history(
    tmp_path: Path,
) -> None:
    client = ScriptedModelClient(
        [
            json.dumps({"type": "final", "content": "stage1 complete"}),
            json.dumps({"type": "final", "content": "stage2 complete"}),
        ]
    )
    agent = GenerationReActAgent(
        agent_id="generation-test",
        role="generation",
        client=client,
        system_prompt="Generate capabilities.",
        tools={"noop": _noop_tool()},
        budget=BudgetCounter("stage1", 1),
        trace_path=tmp_path / "generation.jsonl",
        session_id="fixed-generation-session",
    )

    stage1 = agent.begin_stage1("stage-one-instruction")
    history_after_stage1 = list(agent.history)
    stage2 = agent.continue_stage2(
        "stage-two-instruction",
        tools={"write": _noop_tool("write")},
        stage2_budget=BudgetCounter("stage2", 1),
    )

    assert stage1.session_id == stage2.session_id == "fixed-generation-session"
    assert stage1.episode_id == stage2.episode_id == agent.episode_id
    assert len(agent.history) > len(history_after_stage1)
    second_request_text = "\n".join(message.content for message in client.requests[1])
    assert "stage-one-instruction" in second_request_text
    assert "stage1 complete" in second_request_text
    assert "Stage 1 is frozen" in second_request_text
    assert agent.phase == "stage2"


def test_repair_context_epoch_projects_current_context_without_mutating_history(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "generation.jsonl"
    client = ScriptedModelClient(
        [
            json.dumps(
                {
                    "type": "tool",
                    "tool": "noop",
                    "arguments": {"value": "current-round"},
                }
            ),
            json.dumps({"type": "final", "content": "repair complete"}),
        ]
    )
    agent = GenerationReActAgent(
        agent_id="generation-test",
        role="generation",
        client=client,
        system_prompt="GENERATION_SYSTEM_SENTINEL",
        tools={"noop": _noop_tool()},
        budget=BudgetCounter("stage2", 1),
        trace_path=trace_path,
        session_id="fixed-generation-session",
    )
    agent.set_phase("stage2", tools={"noop": _noop_tool()})
    stale = "STALE_STAGE2_BODY_" + "z" * 50_000
    agent.history.append(ModelMessage("assistant", stale))
    prior_history = tuple(agent.history)
    episode_id = agent.episode_id

    result = agent.resume_repair(
        {"repair_round": 1, "failure": "CURRENT_FEEDBACK_SENTINEL"},
        repair_budget=BudgetCounter("repair-01", 2),
        context_checkpoint={
            "schema_version": "test.checkpoint.v1",
            "frozen_stage1_sha256": "a" * 64,
            "package_tree_sha256": "b" * 64,
        },
        max_request_context_bytes=64 * 1024,
        instruction="CURRENT_REPAIR_INSTRUCTION_SENTINEL",
    )

    assert result.session_id == agent.session_id == "fixed-generation-session"
    assert result.episode_id == agent.episode_id == episode_id
    assert any(message.content == stale for message in agent.history)
    assert len(agent.history) > len(prior_history)

    first_request = client.requests[0]
    first_text = "\n".join(message.content for message in first_request)
    assert first_request[0].role == "system"
    assert "GENERATION_SYSTEM_SENTINEL" in first_request[0].content
    assert "robot_capability.repair_context_epoch.v1" in first_text
    assert "CURRENT_REPAIR_INSTRUCTION_SENTINEL" in first_text
    assert "CURRENT_FEEDBACK_SENTINEL" in first_text
    assert stale not in first_text

    second_text = "\n".join(message.content for message in client.requests[1])
    assert "current-round" in second_text
    assert '"observation"' in second_text
    assert stale not in second_text

    records = _trace_records(trace_path)
    epoch = next(record for record in records if record["event"] == "context_epoch_started")
    requests = [record for record in records if record["event"] == "model_request"]
    assert epoch["context_epoch"] == 1
    assert epoch["session_id"] == "fixed-generation-session"
    assert epoch["episode_id"] == episode_id
    assert epoch["prior_history_message_count"] == len(prior_history)
    assert epoch["prior_history_sha256"] == sha256_json(
        [message.to_dict() for message in prior_history]
    )
    assert requests[0]["context_compacted"] is True
    assert requests[0]["request_message_count"] == len(first_request)
    assert requests[0]["request_context_bytes"] == len(
        canonical_json([message.to_dict() for message in first_request]).encode("utf-8")
    )
    assert requests[0]["request_context_sha256"] == sha256_json(
        [message.to_dict() for message in first_request]
    )
    assert requests[0]["full_history_message_count"] > len(first_request)


def test_repair_phase_can_install_a_tool_not_available_during_stage2(
    tmp_path: Path,
) -> None:
    client = ScriptedModelClient(
        [json.dumps({"type": "final", "content": "repair complete"})]
    )
    agent = GenerationReActAgent(
        agent_id="generation-test",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("stage2", 1),
        trace_path=tmp_path / "generation.jsonl",
    )
    stage2_tool = _noop_tool("stage2_tool")
    repair_only_tool = _noop_tool("repair_only_tool")
    agent.set_phase("stage2", tools={"stage2_tool": stage2_tool})

    assert "repair_only_tool" not in agent.tools
    agent.resume_repair(
        {"repair_round": 1},
        repair_budget=BudgetCounter("repair-01", 1),
        context_checkpoint={"schema_version": "test.checkpoint.v1"},
        max_request_context_bytes=16 * 1024,
        tools={
            "stage2_tool": stage2_tool,
            "repair_only_tool": repair_only_tool,
        },
    )

    assert agent.phase == "repair"
    assert set(agent.tools) == {"stage2_tool", "repair_only_tool"}
    request_text = "\n".join(message.content for message in client.requests[0])
    assert "repair_only_tool" in request_text


def test_repair_context_limit_fails_before_budget_or_client_call(tmp_path: Path) -> None:
    trace_path = tmp_path / "generation.jsonl"
    client = ScriptedModelClient([])
    agent = GenerationReActAgent(
        agent_id="generation-test",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("stage2", 1),
        trace_path=trace_path,
    )
    agent.set_phase("stage2")
    repair_budget = BudgetCounter("repair-01", 1)

    with pytest.raises(RequestContextLimitExceeded, match="byte limit"):
        agent.resume_repair(
            {"failure": "x" * 2_000},
            repair_budget=repair_budget,
            context_checkpoint={"schema_version": "test.checkpoint.v1"},
            max_request_context_bytes=128,
        )

    assert repair_budget.used == 0
    assert agent.accounting_state()["agent_turns"] == 0
    assert client.requests == []
    assert client.audit_snapshot()["logical_requests"] == 0
    records = _trace_records(trace_path)
    rejected = next(
        record for record in records if record["event"] == "model_request_context_rejected"
    )
    assert rejected["request_context_bytes"] > rejected["max_request_context_bytes"]
    assert not any(record["event"] == "model_request" for record in records)


def test_ten_repair_context_epochs_have_bounded_provider_requests(tmp_path: Path) -> None:
    client = ScriptedModelClient(
        [json.dumps({"type": "final", "content": f"round {index}"}) for index in range(1, 11)]
    )
    agent = GenerationReActAgent(
        agent_id="generation-test",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("stage2", 1),
        trace_path=tmp_path / "generation.jsonl",
        session_id="one-continuous-session",
    )
    agent.set_phase("stage2")
    episode_id = agent.episode_id

    results = []
    for repair_round in range(1, 11):
        results.append(
            agent.resume_repair(
                {
                    "repair_round": repair_round,
                    "failure": f"ROUND_{repair_round:02d}_" + "x" * 7_000,
                },
                repair_budget=BudgetCounter(f"repair-{repair_round:02d}", 1),
                context_checkpoint={
                    "schema_version": "test.checkpoint.v1",
                    "repair_round": repair_round,
                    "package_tree_sha256": "a" * 64,
                },
                max_request_context_bytes=32 * 1024,
            )
        )

    request_sizes = [
        len(canonical_json([message.to_dict() for message in request]).encode("utf-8"))
        for request in client.requests
    ]
    assert max(request_sizes) - min(request_sizes) < 256
    assert all(size < 32 * 1024 for size in request_sizes)
    assert "ROUND_01_" not in "\n".join(
        message.content for message in client.requests[-1]
    )
    assert "ROUND_10_" in "\n".join(
        message.content for message in client.requests[-1]
    )
    assert all(result.session_id == "one-continuous-session" for result in results)
    assert all(result.episode_id == episode_id for result in results)
    assert agent.context_epoch == 10


def test_demo_reset_isolates_each_task_session_history_budget_and_trace(
    tmp_path: Path,
) -> None:
    client = ScriptedModelClient(
        [
            json.dumps({"type": "final", "content": "first done"}),
            json.dumps({"type": "final", "content": "second done"}),
        ]
    )
    agent = DemoReActAgent(
        agent_id="demo-test",
        role="demo_consumer",
        client=client,
        system_prompt="Use validated tools only.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    first_trace = tmp_path / "first.jsonl"
    second_trace = tmp_path / "second.jsonl"

    agent.reset_task_episode(
        task_id="task-one",
        tools={"noop": _noop_tool()},
        call_limit=1,
        trace_path=first_trace,
    )
    assert len(agent.history) == 1
    assert agent.budget.used == 0 and agent.budget.remaining == 1
    first = agent.run_task("first-task-private-instruction")
    first_session = first.session_id
    first_episode = first.episode_id

    agent.reset_task_episode(
        task_id="task-two",
        tools={"noop": _noop_tool()},
        call_limit=1,
        trace_path=second_trace,
    )
    assert len(agent.history) == 1
    assert agent.budget.used == 0 and agent.budget.remaining == 1
    second = agent.run_task("second-task-private-instruction")

    assert first.model_calls == second.model_calls == 1
    assert first.agent_turns == second.agent_turns == 1
    assert first.provider_http_attempts == second.provider_http_attempts == 0
    assert first.provider_retries == second.provider_retries == 0
    assert first.client_kind == second.client_kind == "scripted_fixture"
    assert first.scripted is second.scripted is True
    assert first.agent_turn_budget["limit"] == second.agent_turn_budget["limit"] == 1
    assert first.agent_turn_budget["used"] == second.agent_turn_budget["used"] == 1
    assert first.provider_attempt_budget_policy["scope"] == "not_applicable"
    assert first.usage["response_count"] == second.usage["response_count"] == 1
    assert second.session_id != first_session
    assert second.episode_id != first_episode
    second_request_text = "\n".join(message.content for message in client.requests[1])
    assert "second-task-private-instruction" in second_request_text
    assert "first-task-private-instruction" not in second_request_text

    first_records = _trace_records(first_trace)
    second_records = _trace_records(second_trace)
    assert {record["session_id"] for record in first_records} == {first.session_id}
    assert {record["session_id"] for record in second_records} == {second.session_id}
    assert first.session_id not in second_trace.read_text(encoding="utf-8")
    assert second.session_id not in first_trace.read_text(encoding="utf-8")
    assert first_records[0]["event"] == second_records[0]["event"] == "demo_episode_reset"
    first_response = next(record for record in first_records if record["event"] == "model_response")
    assert first_response["agent_turn"] == 1
    assert first_response["client_kind"] == "scripted_fixture"
    assert first_response["scripted"] is True
    assert first_response["provider_http_attempts_this_turn"] == 0


def test_demo_task_requires_explicit_episode_reset(tmp_path: Path) -> None:
    agent = DemoReActAgent(
        agent_id="demo-test",
        role="demo_consumer",
        client=ScriptedModelClient([]),
        system_prompt="Use tools.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "trace.jsonl",
    )

    with pytest.raises(RuntimeError, match="reset_task_episode"):
        agent.run_task("must fail")


def test_failed_model_request_keeps_agent_turn_and_sanitized_accounting(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "failed.jsonl"
    agent = DemoReActAgent(
        agent_id="demo-test",
        role="demo_consumer",
        client=ScriptedModelClient([]),
        system_prompt="Use tools.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    agent.reset_task_episode(
        task_id="task-fails",
        tools={},
        call_limit=1,
        trace_path=trace_path,
    )

    with pytest.raises(ModelAPIError, match="queue exhausted"):
        agent.run_task("attempt once")

    accounting = agent.accounting_state()
    assert accounting["agent_turns"] == accounting["model_calls"] == 1
    assert accounting["provider_http_attempts"] == 0
    assert accounting["provider_retries"] == 0
    assert accounting["usage"]["response_count"] == 0
    records = _trace_records(trace_path)
    failed = next(record for record in records if record["event"] == "model_request_failed")
    assert failed["agent_turn"] == 1
    assert failed["logical_requests_this_turn"] == 1
    assert failed["provider_http_attempts_this_turn"] == 0
    assert failed["error_type"] == "ModelAPIError"
    assert "queue exhausted" not in trace_path.read_text(encoding="utf-8")


def test_transient_provider_failure_spends_next_declared_turn_not_hidden_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class FakeHTTPResponse:
        status = 200

        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            action = json.dumps({"type": "final", "content": "done"})
            return json.dumps(
                {
                    "content": [{"type": "text", "text": action}],
                    "usage": {"input_tokens": 9, "output_tokens": 4},
                }
            ).encode()

    def fake_urlopen(*args: object, **kwargs: object) -> FakeHTTPResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("must remain sanitized")
        return FakeHTTPResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    agent = DemoReActAgent(
        agent_id="demo-transient-continuation",
        role="demo_consumer",
        client=AWSModelAPIClient(
            AWSModelAPIConfig(max_retries=0), api_key="fake-sensitive-api-key"
        ),
        system_prompt="Finish.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    trace_path = tmp_path / "transient.jsonl"
    agent.reset_task_episode(
        task_id="transient-task",
        tools={},
        call_limit=2,
        trace_path=trace_path,
    )

    result = agent.run_task("finish now")

    assert calls == 2
    assert result.agent_turns == result.model_calls == 2
    assert result.provider_http_attempts == 2
    assert result.provider_retries == 0
    assert result.agent_turn_budget["used"] == 2
    records = _trace_records(trace_path)
    failed = next(record for record in records if record["event"] == "model_request_failed")
    assert failed["retryable_with_another_agent_turn"] is True
    assert failed["failure_kind"] == "transport_or_decode_error"
    continued = next(
        record
        for record in records
        if record["event"] == "transient_model_failure_continued"
    )
    assert continued["remaining_agent_turns"] == 1
    assert continued["next_request_uses_new_agent_turn"] is True
    assert continued["provider_retry"] is False
    requests = [record for record in records if record["event"] == "model_request"]
    assert len(requests) == 2
    assert requests[0]["request_context_sha256"] == requests[1]["request_context_sha256"]
    assert requests[0]["request_message_count"] == requests[1]["request_message_count"]
    assert continued["replay_request_context_sha256"] == requests[1][
        "request_context_sha256"
    ]


def test_repeated_transient_failures_stop_at_declared_agent_turn_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_urlopen(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise TimeoutError("must remain sanitized")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    agent = DemoReActAgent(
        agent_id="demo-transient-budget",
        role="demo_consumer",
        client=AWSModelAPIClient(
            AWSModelAPIConfig(max_retries=0), api_key="fake-sensitive-api-key"
        ),
        system_prompt="Finish.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    trace_path = tmp_path / "transient-budget.jsonl"
    agent.reset_task_episode(
        task_id="transient-budget-task",
        tools={},
        call_limit=2,
        trace_path=trace_path,
    )

    with pytest.raises(BudgetExceeded):
        agent.run_task("finish now")

    assert calls == 2
    accounting = agent.accounting_state()
    assert accounting["agent_turns"] == 2
    assert accounting["provider_http_attempts"] == 2
    assert accounting["provider_retries"] == 0
    continuations = [
        record
        for record in _trace_records(trace_path)
        if record["event"] == "transient_model_failure_continued"
    ]
    assert len(continuations) == 2
    assert continuations[-1]["remaining_agent_turns"] == 0
    assert continuations[-1]["next_request_uses_new_agent_turn"] is False


def test_nonretryable_http_error_does_not_spend_another_agent_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_urlopen(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            url="https://example.invalid/model",
            code=400,
            msg="bad request",
            hdrs={},
            fp=io.BytesIO(b"private body"),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    agent = DemoReActAgent(
        agent_id="demo-nonretryable",
        role="demo_consumer",
        client=AWSModelAPIClient(
            AWSModelAPIConfig(max_retries=0), api_key="fake-sensitive-api-key"
        ),
        system_prompt="Finish.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    trace_path = tmp_path / "nonretryable.jsonl"
    agent.reset_task_episode(
        task_id="nonretryable-task",
        tools={},
        call_limit=2,
        trace_path=trace_path,
    )

    with pytest.raises(ModelAPIError):
        agent.run_task("finish now")

    assert calls == 1
    assert agent.accounting_state()["agent_turns"] == 1
    records = _trace_records(trace_path)
    failed = next(record for record in records if record["event"] == "model_request_failed")
    assert failed["model_error_retryable"] is False
    assert failed["retryable_with_another_agent_turn"] is False
    assert all(
        record["event"] != "transient_model_failure_continued"
        for record in records
    )


def test_agent_refuses_cross_turn_continuation_after_hidden_provider_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_urlopen(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise TimeoutError("must remain sanitized")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("soarm_demo.model_client.time.sleep", lambda _: None)
    agent = DemoReActAgent(
        agent_id="demo-hidden-retry-guard",
        role="demo_consumer",
        client=AWSModelAPIClient(
            AWSModelAPIConfig(max_retries=1), api_key="fake-sensitive-api-key"
        ),
        system_prompt="Finish.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    trace_path = tmp_path / "hidden-retry-guard.jsonl"
    agent.reset_task_episode(
        task_id="hidden-retry-guard-task",
        tools={},
        call_limit=2,
        trace_path=trace_path,
    )

    with pytest.raises(ModelAPIError):
        agent.run_task("finish now")

    assert calls == 2
    accounting = agent.accounting_state()
    assert accounting["agent_turns"] == 1
    assert accounting["provider_http_attempts"] == 2
    assert accounting["provider_retries"] == 1
    failed = next(
        record
        for record in _trace_records(trace_path)
        if record["event"] == "model_request_failed"
    )
    assert failed["model_error_retryable"] is True
    assert failed["retryable_with_another_agent_turn"] is False


def test_one_agent_turn_reports_two_provider_attempts_and_one_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class FakeHTTPResponse:
        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            action = json.dumps({"type": "final", "content": "done"})
            return json.dumps(
                {
                    "content": [{"type": "text", "text": action}],
                    "usage": {"input_tokens": 13, "output_tokens": 5, "cost": 0.4},
                    "metadata": {"remaining_quota": {"remaining_budget": 2.6}},
                }
            ).encode()

    def fake_urlopen(*args: object, **kwargs: object) -> FakeHTTPResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                url="https://example.invalid/model",
                code=500,
                msg="retry",
                hdrs={},
                fp=io.BytesIO(b"discarded"),
            )
        return FakeHTTPResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("soarm_demo.model_client.time.sleep", lambda _: None)
    agent = DemoReActAgent(
        agent_id="demo-provider-audit",
        role="demo_consumer",
        client=AWSModelAPIClient(AWSModelAPIConfig(max_retries=2), api_key="fake"),
        system_prompt="Finish.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    trace_path = tmp_path / "provider.jsonl"
    agent.reset_task_episode(
        task_id="provider-task",
        tools={},
        call_limit=3,
        trace_path=trace_path,
    )

    result = agent.run_task("finish now")

    assert result.agent_turns == result.model_calls == 1
    assert result.provider_http_attempts == 2
    assert result.provider_retries == 1
    assert result.scripted is False
    assert result.client_kind == "aws_model_api"
    assert result.agent_turn_budget["limit"] == 3
    assert result.agent_turn_budget["used"] == 1
    provider_window = result.provider_attempt_budget_policy["agent_window"]
    assert provider_window["limit"] == 9
    assert provider_window["used"] == 2
    assert provider_window["remaining"] == 7
    assert result.usage["input_tokens_total"] == 13
    assert result.usage["output_tokens_total"] == 5
    response_record = next(
        record for record in _trace_records(trace_path) if record["event"] == "model_response"
    )
    assert response_record["agent_turn"] == 1
    assert response_record["provider_http_attempts_this_turn"] == 2
    assert response_record["provider_retries_this_turn"] == 1


def test_tool_arguments_are_runtime_validated_against_json_schema(
    tmp_path: Path,
) -> None:
    called: list[dict[str, object]] = []
    client = ScriptedModelClient(
        [
            json.dumps(
                {
                    "type": "tool",
                    "tool": "strict",
                    "arguments": {"value": "not-a-number"},
                }
            ),
            json.dumps({"type": "final", "content": "done"}),
        ]
    )
    trace = tmp_path / "schema_tool.jsonl"
    agent = DemoReActAgent(
        agent_id="demo-test",
        role="demo_consumer",
        client=client,
        system_prompt="Use tools.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    agent.reset_task_episode(
        task_id="schema-task",
        tools={
            "strict": ToolSpec(
                name="strict",
                description="Accept one number.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["value"],
                    "properties": {"value": {"type": "number"}},
                },
                handler=lambda arguments: called.append(arguments),
            )
        },
        call_limit=2,
        trace_path=trace,
    )

    result = agent.run_task("exercise schema validation")

    assert result.status == "completed"
    assert called == []
    tool_record = next(
        record for record in _trace_records(trace) if record["event"] == "tool_result"
    )
    assert tool_record["result"]["ok"] is False
    assert tool_record["result"]["error"] == (
        "tool arguments failed JSON Schema validation"
    )


def test_oversized_tool_schema_diagnostic_is_content_addressed_not_repeated(
    tmp_path: Path,
) -> None:
    oversized = "private-source-fragment-" * 100
    client = ScriptedModelClient(
        [
            json.dumps(
                {
                    "type": "tool",
                    "tool": "bounded",
                    "arguments": {"content": oversized},
                }
            ),
            json.dumps({"type": "final", "content": "handled"}),
        ]
    )
    trace = tmp_path / "oversized-schema.jsonl"
    agent = DemoReActAgent(
        agent_id="oversized-schema-test",
        role="demo_consumer",
        client=client,
        system_prompt="Use tools.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    agent.reset_task_episode(
        task_id="oversized-schema-task",
        tools={
            "bounded": ToolSpec(
                name="bounded",
                description="Accept bounded source.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["content"],
                    "properties": {
                        "content": {"type": "string", "maxLength": 10}
                    },
                },
                handler=lambda _: None,
            )
        },
        call_limit=2,
        trace_path=trace,
    )

    agent.run_task("exercise oversized validation")

    tool_record = next(
        record for record in _trace_records(trace) if record["event"] == "tool_result"
    )
    issue = tool_record["result"]["issues"][0]
    assert issue["path"] == "$.arguments.content"
    assert issue["message"] == "string exceeds the schema maxLength at this path"
    assert issue["observed_characters"] == len(oversized)
    assert issue["observed_utf8_bytes"] == len(oversized.encode("utf-8"))
    assert len(issue["observed_sha256"]) == 64
    assert issue["constraint_keyword"] == "maxLength"
    assert issue["limit_characters"] == 10
    assert issue["diagnostic_utf8_bytes"] > 512
    assert len(issue["diagnostic_sha256"]) == 64
    assert oversized not in json.dumps(tool_record)


def test_tool_wall_clock_deadline_interrupts_handler(tmp_path: Path) -> None:
    client = ScriptedModelClient(
        [
            json.dumps({"type": "tool", "tool": "hang", "arguments": {}}),
            json.dumps({"type": "final", "content": "handled timeout"}),
        ]
    )
    trace = tmp_path / "deadline.jsonl"
    agent = DemoReActAgent(
        agent_id="deadline-test",
        role="demo_consumer",
        client=client,
        system_prompt="Use tools.",
        tools={},
        budget=BudgetCounter("bootstrap", 1),
        trace_path=tmp_path / "bootstrap.jsonl",
    )
    agent.reset_task_episode(
        task_id="deadline-task",
        tools={
            "hang": ToolSpec(
                name="hang",
                description="Hangs longer than its deadline.",
                input_schema={"type": "object", "additionalProperties": False},
                handler=lambda _: time.sleep(1.0),
                timeout_s=0.01,
            )
        },
        call_limit=2,
        trace_path=trace,
    )

    result = agent.run_task("exercise hard deadline")

    assert result.status == "completed"
    tool_record = next(
        record for record in _trace_records(trace) if record["event"] == "tool_result"
    )
    assert tool_record["result"]["timed_out"] is True
