from __future__ import annotations

import json

import pytest

from autoadapter2.foundation.errors import ContractError
from autoadapter2.generation import ModelApiClient, ModelApiConfig
from autoadapter2.generation import model_api


class _Response:
    def __init__(self, payload=None):
        self.payload = payload or {"choices": [{"message": {"content": '{"ok":true}'}}]}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_model_api_uses_platform_default_ca_when_not_overridden(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    calls: list[dict[str, object]] = []

    def context_factory(*_args, **kwargs):
        calls.append(dict(kwargs))
        return object()

    monkeypatch.setattr(model_api.ssl, "create_default_context", context_factory)
    monkeypatch.setattr(
        model_api.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(),
    )

    client = ModelApiClient(ModelApiConfig(api_key="test-only"))
    assert client.generate_json("smoke", "Return JSON.", {}) == {"ok": True}
    assert calls == [{}]


def test_model_api_repair_requires_closed_raw_python_and_describes_validation_a_contract(monkeypatch):
    captured: dict[str, object] = {}
    source = "def capability_example():\n    return 1\n"
    payload = {
        "choices": [{
            "message": {
                "content": json.dumps({"capability.py": source, "llm_calls": 1}),
            },
        }],
    }

    def urlopen(request, **_kwargs):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response(payload)

    monkeypatch.setattr(model_api.urllib.request, "urlopen", urlopen)
    client = ModelApiClient(ModelApiConfig(api_key="test-only"))
    assert client.repair({"diagnostics": []}) == {"capability.py": source, "llm_calls": 1}
    instruction = captured["body"]["messages"][1]["content"]  # type: ignore[index]
    for phrase in (
        "complete raw parseable Python source", "no Markdown fences", "imports only math, time, or numpy",
        "private non-dunder helpers", "approved math/time/numpy members", "No dynamic import",
    ):
        assert phrase in instruction

    fenced_payload = {
        "choices": [{
            "message": {
                "content": json.dumps({"capability.py": "```python\n" + source + "```", "llm_calls": 1}),
            },
        }],
    }
    monkeypatch.setattr(model_api.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(fenced_payload))
    with pytest.raises(ContractError, match="raw source"):
        client.repair({"diagnostics": []})
