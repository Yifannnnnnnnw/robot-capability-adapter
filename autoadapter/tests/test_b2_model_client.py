from __future__ import annotations

import json
import os
import urllib.request
from email.message import Message
from typing import Any
from unittest import mock

import pytest

from autoadapter2.b2.model_client import (
    B2ModelProviderConfig,
    ReCAPJsonModelClient,
)
from autoadapter2.model_api import ModelInvocationError


SYSTEM_PROMPT = "Fixed ReCAP system prompt."
MESSAGES = (
    {"role": "user", "content": '{"event":"controller_start"}'},
    {"role": "assistant", "content": '{"event":"previous_plan"}'},
)
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning_summary": {"type": "string"},
        "subtasks": {"type": "array"},
    },
    "required": ["reasoning_summary", "subtasks"],
    "additionalProperties": False,
}


def _provider_config() -> B2ModelProviderConfig:
    return B2ModelProviderConfig(
        provider="fixed-provider",
        model="fixed-model",
        base_url="https://model.example/v1",
        api_protocol="openai-compatible",
        thinking="disabled",
        timeout_s=45.0,
        max_tokens=4096,
        history_char_budget=32000,
    )


def _provider_response(content: dict[str, Any]) -> mock.MagicMock:
    payload = {
        "id": "provider-request-1",
        "model": "fixed-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(content),
                },
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8},
    }
    response = mock.MagicMock()
    entered = response.__enter__.return_value
    entered.status = 200
    entered.headers = Message()
    entered.read.return_value = json.dumps(payload).encode("utf-8")
    return response


def test_recap_prompt_and_history_roles_reach_existing_json_client() -> None:
    credential = "credential-must-stay-parent-side"
    expected = {"reasoning_summary": "Use one leaf.", "subtasks": []}
    adapter = ReCAPJsonModelClient(
        provider_config=_provider_config(),
        credential=credential,
    )

    with mock.patch.dict(
        os.environ,
        {"AUTOADAPTER_MODEL_API_KEY": "environment-key-must-not-be-read"},
        clear=True,
    ), mock.patch.object(
        urllib.request,
        "urlopen",
        return_value=_provider_response(expected),
    ) as urlopen:
        result = adapter.generate_recap_json(
            stage="recursive_plan_or_refine",
            system_prompt=SYSTEM_PROMPT,
            messages=MESSAGES,
            response_schema=RESPONSE_SCHEMA,
        )

    assert result == expected
    request = urlopen.call_args.args[0]
    request_body = json.loads(request.data)
    assert request_body["response_format"] == {"type": "json_object"}
    assert request_body["messages"][0] == {
        "role": "system",
        "content": SYSTEM_PROMPT,
    }
    assert request_body["messages"][1:3] == list(MESSAGES)
    schema_instruction = request_body["messages"][3]
    assert schema_instruction["role"] == "user"
    transported_schema = json.loads(
        schema_instruction["content"].split(
            "FIXED_RESPONSE_SCHEMA_JSON:\n",
            maxsplit=1,
        )[1]
    )
    assert transported_schema == RESPONSE_SCHEMA
    assert request.get_header("Authorization") == f"Bearer {credential}"
    assert credential not in request.data.decode("utf-8")
    assert "environment-key-must-not-be-read" not in str(request.header_items())


def test_provider_exception_propagates_without_adapter_retry_or_rewrite() -> None:
    adapter = ReCAPJsonModelClient(
        provider_config=_provider_config(),
        credential="parent-secret",
    )
    failure = ModelInvocationError("provider failed")

    with mock.patch.object(
        adapter._client,
        "generate_message_json",
        side_effect=failure,
    ) as generate:
        with pytest.raises(ModelInvocationError) as raised:
            adapter.generate_recap_json(
                stage="recursive_plan_or_refine",
                system_prompt=SYSTEM_PROMPT,
                messages=MESSAGES,
                response_schema=RESPONSE_SCHEMA,
            )

    assert raised.value is failure
    generate.assert_called_once_with(
        stage="recursive_plan_or_refine",
        system_prompt=SYSTEM_PROMPT,
        messages=[
            *MESSAGES,
            {
                "role": "user",
                "content": (
                    "Return exactly one JSON object that conforms to this fixed "
                    "response schema. Do not add Markdown or fields outside the "
                    "schema.\n\nFIXED_RESPONSE_SCHEMA_JSON:\n"
                    '{"additionalProperties":false,"properties":'
                    '{"reasoning_summary":{"type":"string"},"subtasks":'
                    '{"type":"array"}},"required":["reasoning_summary",'
                    '"subtasks"],"type":"object"}'
                ),
            },
        ],
    )


def test_parent_call_records_and_repr_do_not_expose_credential() -> None:
    credential = "parent-only-secret"
    adapter = ReCAPJsonModelClient(
        provider_config=_provider_config(),
        credential=credential,
    )
    with mock.patch.object(
        urllib.request,
        "urlopen",
        return_value=_provider_response(
            {"reasoning_summary": "Done.", "subtasks": []}
        ),
    ):
        adapter.generate_recap_json(
            stage="recursive_plan_or_refine",
            system_prompt=SYSTEM_PROMPT,
            messages=MESSAGES,
            response_schema=RESPONSE_SCHEMA,
        )

    records = adapter.provider_call_records
    assert len(records) == 1
    assert records[0]["provider_request_id"] == "provider-request-1"
    assert records[0]["input_tokens"] == 20
    assert records[0]["output_tokens"] == 8
    assert records[0]["mode"] == "json"
    serialized = json.dumps(records)
    assert credential not in serialized
    assert credential not in repr(adapter)
    assert credential not in repr(_provider_config())


def test_nonfinite_schema_is_rejected_before_provider_call() -> None:
    adapter = ReCAPJsonModelClient(
        provider_config=_provider_config(),
        credential="parent-secret",
    )
    with mock.patch.object(adapter._client, "generate_message_json") as generate:
        with pytest.raises(ValueError, match="finite JSON"):
            adapter.generate_recap_json(
                stage="recursive_plan_or_refine",
                system_prompt=SYSTEM_PROMPT,
                messages=MESSAGES,
                response_schema={"minimum": float("nan")},
            )
    generate.assert_not_called()
