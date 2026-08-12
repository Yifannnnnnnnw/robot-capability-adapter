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


def test_model_api_repair_adds_unitree_shape_only_for_unitree_bundle(monkeypatch):
    source = "def capability_example():\n    return 1\n"
    payload = {
        "choices": [{
            "message": {
                "content": json.dumps({"capability.py": source, "llm_calls": 1}),
            },
        }],
    }
    captured: list[str] = []

    def urlopen(request, **_kwargs):
        body = json.loads(request.data.decode("utf-8"))
        captured.append(body["messages"][1]["content"])
        return _Response(payload)

    monkeypatch.setattr(model_api.urllib.request, "urlopen", urlopen)
    client = ModelApiClient(ModelApiConfig(api_key="test-only"))
    unitree_bundle = {
        "sdk_implementation_projection": {
            "sdk_entry_id": "unitree-sdk2-go2-lowlevel",
            "permitted_types": [
                "ChannelPublisher", "ChannelSubscriber", "LowCmd_", "LowState_", "SportModeState_",
            ],
        },
    }
    so_bundle = {
        "sdk_implementation_projection": {
            "sdk_entry_id": "lerobot-so101-follower",
            "permitted_types": ["command"],
        },
    }

    assert client.repair({"diagnostics": [], "implementation_bundle": unitree_bundle}) == {
        "capability.py": source, "llm_calls": 1,
    }
    assert client.repair({"diagnostics": [], "implementation_bundle": so_bundle}) == {
        "capability.py": source, "llm_calls": 1,
    }

    unitree_instruction, so_instruction = captured
    for instruction in (unitree_instruction, so_instruction):
        assert "`_sdk` is a module-like injected facade" in instruction
        assert "module-global `_sdk`" in instruction
        assert "Do not call `ChannelFactoryInitialize`" in instruction
        assert "second SDK" in instruction
    assert '_sdk.ChannelPublisher("rt/lowcmd", _sdk.LowCmd_)' in unitree_instruction
    assert "_sdk.unitree_go_msg_dds__LowCmd_()" in unitree_instruction
    assert "_sdk.low_state_subscriber" in unitree_instruction
    assert '_sdk.ChannelPublisher("rt/lowcmd", _sdk.LowCmd_)' not in so_instruction
    assert "_sdk.unitree_go_msg_dds__LowCmd_()" not in so_instruction
    for phrase in (
        "already-open injected SO facade",
        "`_sdk.get_observation()`",
        "`_sdk.send_action({...six fields...})`",
        "`public_task_state` is returned inside the observation",
        "Do not construct",
        "SO101Follower",
        "SOFollower",
        "FeetechMotorsBus",
        "do not call `connect` or",
        "`disconnect`",
        "API wiring only, not IK/control behavior",
    ):
        assert phrase in so_instruction
    assert "_sdk.SO101Follower(" not in so_instruction
    assert "_sdk.SOFollower(" not in so_instruction
    assert "_sdk.FeetechMotorsBus(" not in so_instruction
    assert "_sdk.connect(" not in so_instruction
    assert "_sdk.disconnect(" not in so_instruction
