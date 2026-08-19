from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from autoadapter2.model_api import (
    JsonModelClient,
    ModelConfig,
    ModelInvocationError,
    parse_json_object,
)


class ModelApiTests(unittest.TestCase):
    def test_json_fence_is_accepted(self) -> None:
        self.assertEqual(parse_json_object('```json\n{"ok": true}\n```'), {"ok": True})

    def test_trailing_text_is_rejected(self) -> None:
        with self.assertRaises(ModelInvocationError):
            parse_json_object('{"ok": true} trailing')

    def test_config_repr_does_not_expose_api_key(self) -> None:
        config = ModelConfig(
            provider="openai",
            model="model",
            base_url="https://model.example/v1",
            api_key="secret-value",
        )

        self.assertNotIn("secret-value", repr(config))
        self.assertEqual(config.endpoint_url, "https://model.example/v1/chat/completions")

    def test_deepseek_vendor_is_distinct_from_api_protocol(self) -> None:
        environment = {
            "AUTOADAPTER_MODEL_PROVIDER": "openai",
            "AUTOADAPTER_MODEL_ID": "deepseek-v4-pro",
            "AUTOADAPTER_MODEL_API_BASE_URL": "https://api.deepseek.com",
            "AUTOADAPTER_MODEL_API_KEY": "secret-value",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            config = ModelConfig.from_env()

        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.api_protocol, "openai")
        self.assertEqual(config.max_tokens, 32768)

    def test_model_output_budget_is_configurable_and_bounded(self) -> None:
        environment = {
            "AUTOADAPTER_MODEL_PROVIDER": "openai",
            "AUTOADAPTER_MODEL_ID": "deepseek-v4-pro",
            "AUTOADAPTER_MODEL_API_BASE_URL": "https://api.deepseek.com",
            "AUTOADAPTER_MODEL_API_KEY": "secret-value",
            "AUTOADAPTER_MODEL_MAX_TOKENS": "48000",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            self.assertEqual(ModelConfig.from_env().max_tokens, 48000)
        environment["AUTOADAPTER_MODEL_MAX_TOKENS"] = "70000"
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ModelInvocationError):
                ModelConfig.from_env()

    def test_invalid_tool_history_mode_is_rejected(self) -> None:
        environment = {
            "AUTOADAPTER_MODEL_PROVIDER": "openai-compatible",
            "AUTOADAPTER_MODEL_ID": "model",
            "AUTOADAPTER_MODEL_API_BASE_URL": "https://model.example/v1",
            "AUTOADAPTER_MODEL_API_KEY": "secret-value",
            "AUTOADAPTER_MODEL_TOOL_HISTORY_MODE": "unknown",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(
                ModelInvocationError, "TOOL_HISTORY_MODE"
            ):
                ModelConfig.from_env()

    def test_tool_turn_preserves_calls_and_reasoning_for_the_next_turn(self) -> None:
        config = ModelConfig(
            provider="deepseek",
            model="deepseek-v4-pro",
            base_url="https://model.example/v1",
            api_key="secret-value",
        )
        client = JsonModelClient(config)
        payload = {
            "model": "deepseek-v4-pro",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": "I should inspect the public package.",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "read_public_file",
                                    "arguments": '{"path":"robot.json"}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            turn = client.generate_tool_turn(
                stage="STUDY",
                system_prompt="Use the public tools.",
                messages=[{"role": "user", "content": "Inspect."}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "read_public_file",
                            "description": "Read one file.",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
            )

        self.assertEqual(turn.tool_calls[0].arguments, {"path": "robot.json"})
        self.assertEqual(turn.reasoning_content, "I should inspect the public package.")
        request = urlopen.call_args.args[0]
        request_body = json.loads(request.data)
        self.assertIn("tools", request_body)
        self.assertNotIn("response_format", request_body)
        self.assertEqual(client.calls[0]["mode"], "react")
        self.assertEqual(client.calls[0]["tool_names"], ["read_public_file"])

    def test_text_observation_mode_avoids_native_tool_history(self) -> None:
        config = ModelConfig(
            provider="company",
            model="deepseek.v3.2",
            base_url="https://model.example/v1",
            api_key="secret-value",
            tool_history_mode="text-observation",
        )
        client = JsonModelClient(config)
        payload = {
            "model": "deepseek.v3.2",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Done."},
                }
            ],
            "usage": {},
        }
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        messages = [
            {"role": "user", "content": "Inspect."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_public_file",
                            "arguments": '{"path":"robot.json"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": '{"ok":true}',
            },
        ]
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            client.generate_tool_turn(
                stage="STUDY",
                system_prompt="Use public tools.",
                messages=messages,
                tools=[],
            )

        request = urlopen.call_args.args[0]
        request_messages = json.loads(request.data)["messages"]
        self.assertNotIn("tool", [message["role"] for message in request_messages])
        self.assertFalse(
            any("tool_calls" in message for message in request_messages)
        )
        self.assertIn("TOOL_REQUESTS_JSON", request_messages[2]["content"])
        self.assertIn("TOOL_OBSERVATION_JSON", request_messages[3]["content"])
        self.assertEqual(client.calls[0]["tool_history_mode"], "text-observation")

    def test_text_observation_mode_does_not_repeat_written_driver_source(self) -> None:
        config = ModelConfig(
            provider="company",
            model="deepseek.v3.2",
            base_url="https://model.example/v1",
            api_key="secret-value",
            tool_history_mode="text-observation",
        )
        client = JsonModelClient(config)
        payload = {
            "model": "deepseek.v3.2",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Continue."},
                }
            ],
            "usage": {},
        }
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        source = "UNIQUE_DRIVER_SOURCE" * 1000
        messages = [
            {"role": "user", "content": "Repair."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-write",
                        "type": "function",
                        "function": {
                            "name": "write_driver",
                            "arguments": json.dumps({"source": source}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-write",
                "content": '{"ok":true,"result":{"revision":1}}',
            },
        ]
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            client.generate_tool_turn(
                stage="repair",
                system_prompt="Repair with tools.",
                messages=messages,
                tools=[],
            )

        request_text = urlopen.call_args.args[0].data.decode()
        self.assertNotIn(source, request_text)
        self.assertIn(f'\\"source_chars\\": {len(source)}', request_text)
        self.assertIn("call read_driver for current source", request_text)

if __name__ == "__main__":
    unittest.main()
