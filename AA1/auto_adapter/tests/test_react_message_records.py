"""Complete message records at the actual ReAct/tool boundary (test transport)."""
import json
from types import SimpleNamespace

import pytest

from auto_adapter.agent.react_loop import ReactLoop, ToolSpec


@pytest.mark.parametrize("max_iters", [1, 2])
def test_full_tool_result_survives_even_the_final_budgeted_turn(tmp_path, monkeypatch, max_iters):
    from auto_adapter.agent import holistic_client

    observation = "real tool result " + "x" * 3500
    responses = iter([
        SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", id="call_1", name="inspect", input={})],
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=2, output_tokens=3),
        ),
        SimpleNamespace(
            content=[SimpleNamespace(type="text", text="done")], stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=4, output_tokens=1),
        ),
    ])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: next(responses)))
    monkeypatch.setattr(holistic_client, "HolisticClient", lambda: client)
    loop = ReactLoop(
        tools=[ToolSpec("inspect", "test inspection", {"type": "object"}, lambda _: observation)],
        system="test system", provider="holistic", max_iters=max_iters,
        trace_path=tmp_path / "trace.jsonl",
    )
    result = loop.run("read the test fixture")
    records = [json.loads(line) for line in (tmp_path / "trace.messages.jsonl").read_text().splitlines()]
    assert records[0]["system"] == "test system"
    assert records[0]["gateway_messages"][0] == {"role": "system", "content": "test system"}
    tool_record = next(record for record in records if record["event"] == "tool_results")
    assert tool_record["results"][0]["content"] == observation
    assert len(result.trace[0].observations[0]["content"]) < len(observation)
    if max_iters == 2:
        requests = [record for record in records if record["event"] == "request"]
        assert requests[1]["messages"][-1]["content"][0]["content"] == observation
        assert result.ok
    else:
        assert not result.ok
