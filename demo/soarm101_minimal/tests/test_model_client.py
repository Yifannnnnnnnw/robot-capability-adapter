from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from soarm_demo.model_client import (
    AWSModelAPIClient,
    AWSModelAPIConfig,
    ModelAPIError,
    ModelMessage,
    ScriptedModelClient,
)


def test_normalizes_anthropic_content_blocks_and_usage() -> None:
    result = {
        "content": [
            {"type": "thinking", "thinking": "private"},
            {"type": "text", "text": "first"},
            "second",
            {"type": "text", "text": "third"},
        ],
        "usage": {
            "input_tokens": "12",
            "output_tokens": 7,
            "cost": "0.125",
        },
        "metadata": {"remaining_quota": {"remaining_budget": "8.5"}},
    }

    response = AWSModelAPIClient._normalize(result)

    assert response.text == "first\nsecond\nthird"
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 7
    assert response.usage.cost == pytest.approx(0.125)
    assert response.usage.remaining_budget == pytest.approx(8.5)
    assert response.raw is result


def test_normalizes_openai_choices_shape() -> None:
    result = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "choice one"},
                "finish_reason": "length",
            },
            {"message": {"role": "assistant", "content": "choice two"}},
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 5},
    }

    response = AWSModelAPIClient._normalize(result)

    assert response.text == "choice one\nchoice two"
    assert response.usage.input_tokens == 4
    assert response.usage.output_tokens == 5
    assert response.stop_reason == "length"


def test_completion_audit_records_output_limit_without_response_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_text = "private truncated provider text"

    class FakeHTTPResponse:
        status = 200

        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "content": [{"type": "text", "text": private_text}],
                    "stop_reason": "max_tokens",
                    "usage": {"output_tokens": 8},
                }
            ).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: FakeHTTPResponse())
    client = AWSModelAPIClient(
        AWSModelAPIConfig(max_tokens=8, max_retries=0),
        api_key="fake",
    )

    response = client.complete([ModelMessage("user", "private request")])
    completion = client.audit_snapshot()["requests"][0]["completion"]

    assert response.stop_reason == "max_tokens"
    assert response.output_truncated is True
    assert response.requested_max_tokens == 8
    assert completion == {
        "stop_reason": "max_tokens",
        "output_truncated": True,
        "requested_max_tokens": 8,
    }
    assert private_text not in json.dumps(client.audit_snapshot())


def test_normalizes_aws_camel_case_token_usage() -> None:
    response = AWSModelAPIClient._normalize(
        {
            "content": "ok",
            "usage": {"inputTokens": "21", "outputTokens": 13},
        }
    )

    assert response.usage.input_tokens == 21
    assert response.usage.output_tokens == 13


def test_api_key_is_only_sent_as_header_not_payload_or_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "test-secret-that-must-not-leak"
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"content": [{"type": "text", "text": "ok"}]}).encode()

    def fake_urlopen(request: urllib.request.Request, *, timeout: float) -> FakeHTTPResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = AWSModelAPIClient(
        AWSModelAPIConfig(route="long", max_retries=0),
        api_key=secret,
    )

    response = client.complete([ModelMessage("user", "hello")])
    request = captured["request"]
    assert isinstance(request, urllib.request.Request)
    headers = {name.lower(): value for name, value in request.header_items()}
    assert headers["x-api-key"] == secret
    assert secret not in request.data.decode("utf-8")
    assert secret not in request.full_url
    assert secret not in json.dumps(response.raw)
    assert secret not in repr(client.config)


def test_transport_failure_does_not_echo_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "test-secret-that-must-not-leak"

    def fail_urlopen(*args: object, **kwargs: object) -> object:
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    client = AWSModelAPIClient(AWSModelAPIConfig(max_retries=0), api_key=secret)

    with pytest.raises(ModelAPIError) as caught:
        client.complete([ModelMessage("user", "hello")])

    assert secret not in str(caught.value)


def test_response_without_text_is_rejected() -> None:
    with pytest.raises(ModelAPIError, match="no text content block"):
        AWSModelAPIClient._normalize({"content": [{"type": "tool_use"}]})


def test_provider_http_attempts_and_retries_have_explicit_sanitized_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "fake-api-key-for-audit-test"
    raw_error_body = "raw-provider-body-must-not-be-recorded"
    calls = 0

    class FakeHTTPResponse:
        status = 200

        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "content": [{"type": "text", "text": "normalized action"}],
                    "usage": {"input_tokens": 11, "output_tokens": 7, "cost": 0.25},
                    "metadata": {"remaining_quota": {"remaining_budget": 9.75}},
                }
            ).encode()

    def fake_urlopen(
        request: urllib.request.Request, *, timeout: float
    ) -> FakeHTTPResponse:
        del request, timeout
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                url="https://example.invalid/model",
                code=500,
                msg="server error",
                hdrs={},
                fp=io.BytesIO(raw_error_body.encode()),
            )
        return FakeHTTPResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("soarm_demo.model_client.time.sleep", lambda _: None)
    client = AWSModelAPIClient(
        AWSModelAPIConfig(max_retries=2),
        api_key=secret,
    )

    response = client.complete([ModelMessage("user", "private request text")])
    audit = client.audit_snapshot()

    assert response.provider_http_attempts == 2
    assert response.provider_retries == 1
    assert response.client_kind == "aws_model_api"
    assert audit["logical_requests"] == 1
    assert audit["provider_http_attempts"] == 2
    assert audit["provider_retries"] == 1
    assert audit["attempt_budget_policy"] == {
        "scope": "per_logical_request",
        "limit_per_request": 3,
        "max_retries_per_request": 2,
    }
    request_audit = audit["requests"][0]
    assert request_audit["attempt_budget"]["limit"] == 3
    assert request_audit["attempt_budget"]["used"] == 2
    assert request_audit["attempt_budget"]["remaining"] == 1
    assert [item["outcome"] for item in request_audit["attempts"]] == [
        "http_error",
        "succeeded",
    ]
    serialized = json.dumps(audit)
    assert secret not in serialized
    assert raw_error_body not in serialized
    assert "private request text" not in serialized
    assert "normalized action" not in serialized


def test_client_audit_aggregates_each_successful_response_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = iter(
        [
            {
                "content": "one",
                "usage": {"input_tokens": 10, "output_tokens": 2, "cost": 0.1},
                "metadata": {"remaining_quota": {"remaining_budget": 4.0}},
            },
            {
                "content": "two",
                "usage": {"input_tokens": 20, "output_tokens": 3, "cost": 0.2},
                "metadata": {"remaining_quota": {"remaining_budget": 3.8}},
            },
        ]
    )

    class FakeHTTPResponse:
        def __init__(self, payload: dict[str, Any]):
            self.payload = payload

        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self.payload).encode()

    def fake_urlopen(*args: object, **kwargs: object) -> FakeHTTPResponse:
        return FakeHTTPResponse(next(payloads))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = AWSModelAPIClient(AWSModelAPIConfig(max_retries=0), api_key="fake")

    client.complete([ModelMessage("user", "first")])
    client.complete([ModelMessage("user", "second")])
    audit = client.audit_snapshot()

    assert audit["provider_http_attempts"] == 2
    assert audit["provider_retries"] == 0
    assert audit["usage"] == {
        "response_count": 2,
        "input_tokens_total": 30,
        "output_tokens_total": 5,
        "cost_total": pytest.approx(0.3),
        "remaining_budget_latest": 3.8,
        "remaining_budget_minimum": 3.8,
        "input_tokens_reported": 2,
        "output_tokens_reported": 2,
        "cost_reported": 2,
        "remaining_budget_reported": 2,
    }


def test_non_retryable_http_error_does_not_record_raw_body_or_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "fake-sensitive-api-key"
    raw_error_body = "raw-sensitive-provider-response"

    def fake_urlopen(*args: object, **kwargs: object) -> object:
        raise urllib.error.HTTPError(
            url="https://example.invalid/model",
            code=400,
            msg="bad request",
            hdrs={},
            fp=io.BytesIO(raw_error_body.encode()),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = AWSModelAPIClient(AWSModelAPIConfig(max_retries=2), api_key=secret)

    with pytest.raises(ModelAPIError) as caught:
        client.complete([ModelMessage("user", "private")])

    audit = client.audit_snapshot()
    serialized = json.dumps(audit)
    assert caught.value.retryable is False
    assert caught.value.failure_kind == "http_error"
    assert audit["requests"][0]["retryable"] is False
    assert audit["provider_http_attempts"] == 1
    assert audit["provider_retries"] == 0
    assert audit["requests"][0]["attempt_budget"]["used"] == 1
    assert secret not in str(caught.value)
    assert raw_error_body not in str(caught.value)
    assert secret not in serialized
    assert raw_error_body not in serialized


def test_timeout_is_classified_retryable_without_granting_hidden_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(*args: object, **kwargs: object) -> object:
        raise TimeoutError("private transport detail")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = AWSModelAPIClient(
        AWSModelAPIConfig(max_retries=0),
        api_key="fake-sensitive-api-key",
    )

    with pytest.raises(ModelAPIError) as caught:
        client.complete([ModelMessage("user", "private request")])

    audit = client.audit_snapshot()
    request = audit["requests"][0]
    assert caught.value.retryable is True
    assert caught.value.failure_kind == "transport_or_decode_error"
    assert request["retryable"] is True
    assert request["failure_kind"] == "transport_or_decode_error"
    assert request["provider_http_attempts"] == 1
    assert request["provider_retries"] == 0
    assert "private transport detail" not in str(caught.value)
    assert "private request" not in json.dumps(audit)


def test_scripted_client_is_unambiguously_non_provider_in_audit() -> None:
    client = ScriptedModelClient(["fixture response"], model="scripted-generation")

    response = client.complete([ModelMessage("user", "fixture request")])
    audit = client.audit_snapshot()

    assert response.client_kind == "scripted_fixture"
    assert response.provider_http_attempts == 0
    assert audit["client_kind"] == "scripted_fixture"
    assert audit["scripted"] is True
    assert audit["transport"] == "none"
    assert audit["provider_http_attempts"] == 0
    assert audit["provider_retries"] == 0
    serialized = json.dumps(audit)
    assert "fixture request" not in serialized
    assert "fixture response" not in serialized
