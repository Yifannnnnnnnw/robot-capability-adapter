"""Check request construction against the installed SDK without a network call."""
import inspect

from auto_adapter.agent.react_loop import ReactLoop


def test_sonnet_request_matches_installed_sdk_signature(monkeypatch):
    loop = ReactLoop(tools=[], system="fixture", model="us.anthropic.claude-sonnet-4-6")
    signature = inspect.signature(loop.client.messages.create)
    recorded = {}

    def signature_checked_fixture(**kwargs):
        signature.bind(**kwargs)
        recorded.update(kwargs)
        return "fixture response"

    monkeypatch.setattr(loop.client.messages, "create", signature_checked_fixture)
    assert loop._invoke_with_retry([{"role": "user", "content": "fixture"}]) == "fixture response"
    assert recorded["extra_body"]["temperature"] == 0.0
