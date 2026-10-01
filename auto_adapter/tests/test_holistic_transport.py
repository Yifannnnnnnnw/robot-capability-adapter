"""Focused fixtures for the real Holistic tool-loop transport."""
import io
import json
from urllib.error import HTTPError

import pytest

from auto_adapter.agent import holistic_client as transport


def test_native_tool_roundtrip_fixture(monkeypatch):
    monkeypatch.setenv("AUTOADAPTER_HOLISTICAI_API_KEY", "fixture-key")
    sent = {}

    def reply(request, timeout):
        sent.update(json.loads(request.data))
        assert request.get_header("X-api-key") == "fixture-key"
        return io.StringIO(json.dumps({
            "choices": [{"message": {"content": None, "tool_calls": [{
                "id": "call-next", "function": {"name": "read", "arguments": '{"x": 4}'},
            }]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
        }))

    monkeypatch.setattr(transport, "urlopen", reply)
    response = transport.HolisticClient().messages.create(
        model="eu.anthropic.claude-sonnet-4-6", max_tokens=100, system="fixture",
        tools=[{"name": "read", "description": "read", "input_schema": {"type": "object"}}],
        messages=[
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-first",
                                                "name": "read", "input": {"x": 3}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-first",
                                           "content": "3"}]},
        ],
    )
    assert sent["messages"][1]["tool_calls"][0]["id"] == "call-first"
    assert sent["messages"][2] == {"role": "tool", "tool_call_id": "call-first", "content": "3"}
    assert response.content[0].input == {"x": 4}
    assert response.stop_reason == "tool_use"
    assert response.usage.input_tokens == 11


def test_gateway_error_keeps_reason_without_credential(monkeypatch):
    monkeypatch.setenv("AUTOADAPTER_HOLISTICAI_API_KEY", "fixture-secret")

    def denied(request, timeout):
        raise HTTPError(request.full_url, 403, "denied", {},
                        io.BytesIO(b"account denied fixture-secret"))

    monkeypatch.setattr(transport, "urlopen", denied)
    with pytest.raises(RuntimeError, match="Holistic HTTP 403: account denied <redacted>"):
        transport.HolisticClient().create(model="fixture", messages=[], max_tokens=1)
