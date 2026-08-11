from __future__ import annotations

import json

from autoadapter2.generation import ModelApiClient, ModelApiConfig
from autoadapter2.generation import model_api


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(
            {"choices": [{"message": {"content": '{"ok":true}'}}]}
        ).encode("utf-8")


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
