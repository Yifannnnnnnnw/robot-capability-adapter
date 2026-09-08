"""Offline checks of the explicit cheap-model transport probe, without API calls."""

import importlib
import json
from pathlib import Path

import pytest


def test_only_successful_headers_after_120_establish_the_probe_result(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    script = importlib.import_module("test_holistic_request_deadline")
    for status, wait, expected in [
        ("success", 119.0, False), ("success", 121.0, True),
        ("timeout", None, False), ("http_error", 121.0, False),
    ]:
        result = script.result_fields({"status": status, "response_wait_after_connection_s": wait})
        assert result["successful_response_headers_after_120s"] is expected
    body = script.request_body("deepseek.v3.2")
    assert body["max_tokens"] == 8192
    assert body["thinking"] == {"type": "disabled"}
    assert body["stream"] is False
    assert "tools" not in body


def test_saved_request_is_replayed_without_model_or_prompt_changes(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    script = importlib.import_module("test_holistic_request_deadline")
    body = {"model": "opus-test-fixture", "thinking": {"type": "adaptive"},
            "messages": [{"role": "user", "content": "fixture"}],
            "tools": [{"type": "function", "function": {"name": "fixture"}}],
            "max_tokens": 16384}
    path = tmp_path / "saved.json"
    path.write_text(json.dumps({"request_body": body}))
    assert script.replay_body(path, "opus-test-fixture") == body
    with pytest.raises(ValueError, match="model"):
        script.replay_body(path, "different-model")
