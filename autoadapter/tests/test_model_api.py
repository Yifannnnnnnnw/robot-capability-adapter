from __future__ import annotations

import json
import os
import signal
import time
import unittest
import urllib.error
from datetime import datetime
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from autoadapter2.agent_context import AgentContextManager
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

    def test_truncated_json_response_is_recorded_before_rejection(self) -> None:
        client = JsonModelClient(
            ModelConfig(
                provider="deepseek",
                model="deepseek-v4-pro",
                base_url="https://model.example/v1",
                api_key="secret-value",
            )
        )
        payload = {
            "model": "deepseek-v4-pro",
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"role": "assistant", "content": '{"partial":'},
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 16},
        }

        with mock.patch.object(client, "_post", return_value=payload):
            with self.assertRaisesRegex(ModelInvocationError, "finish_reason='length'"):
                client.generate_json(stage="tgcd", prompt="Design.", inputs={})

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["stage"], "tgcd")
        self.assertEqual(client.calls[0]["finish_reason"], "length")
        self.assertEqual(client.calls[0]["usage"]["completion_tokens"], 16)

    def test_generate_json_keeps_fixed_prompt_in_system_across_dynamic_inputs(self) -> None:
        client = JsonModelClient(
            ModelConfig(
                provider="company",
                model="model",
                base_url="https://model.example/v1",
                api_key="secret-value",
            )
        )
        payload = {
            "model": "model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"ok": true}'},
                }
            ],
            "usage": {},
        }
        request_bodies: list[dict[str, object]] = []

        def post(*, stage: str, body: dict[str, object]) -> dict:
            del stage
            request_bodies.append(body)
            return payload

        fixed_prompt = "FIXED_EVOLUTION_RULES"
        with mock.patch.object(client, "_post", side_effect=post):
            for item in ("DYNAMIC_ITEM_ONE", "DYNAMIC_ITEM_TWO"):
                self.assertEqual(
                    client.generate_json(
                        stage="evolution",
                        prompt=fixed_prompt,
                        inputs={"item": item},
                    ),
                    {"ok": True},
                )

        first_messages = request_bodies[0]["messages"]
        second_messages = request_bodies[1]["messages"]
        self.assertIsInstance(first_messages, list)
        self.assertIsInstance(second_messages, list)
        self.assertEqual(first_messages[0], second_messages[0])
        self.assertEqual(
            first_messages[0],
            {
                "role": "system",
                "content": (
                    "Return exactly one JSON object and no Markdown.\n\n"
                    + fixed_prompt
                ),
            },
        )
        self.assertEqual(
            first_messages[1],
            {
                "role": "user",
                "content": 'PUBLIC_INPUT_JSON:\n{"item": "DYNAMIC_ITEM_ONE"}',
            },
        )
        self.assertEqual(
            second_messages[1],
            {
                "role": "user",
                "content": 'PUBLIC_INPUT_JSON:\n{"item": "DYNAMIC_ITEM_TWO"}',
            },
        )
        self.assertNotIn(fixed_prompt, first_messages[1]["content"])
        self.assertNotIn("DYNAMIC_ITEM_ONE", first_messages[0]["content"])
        for body in request_bodies:
            self.assertEqual(body["temperature"], 0.0)
            self.assertEqual(body["response_format"], {"type": "json_object"})

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
        self.assertEqual(config.max_tokens, 16384)

    def test_disabled_thinking_is_explicit_for_deepseek_requests(self) -> None:
        payload = {
            "model": "model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"ok": true}'},
                }
            ],
            "usage": {},
        }
        for provider in ("deepseek", "mistral"):
            for mode in ("json", "tool"):
                with self.subTest(provider=provider, mode=mode):
                    client = JsonModelClient(
                        ModelConfig(
                            provider=provider,
                            model="model",
                            base_url="https://model.example/v1",
                            api_key="secret-value",
                            thinking="disabled",
                        )
                    )
                    captured: dict[str, object] = {}

                    def post(*, stage: str, body: dict[str, object]) -> dict:
                        del stage
                        captured.update(body)
                        return payload

                    with mock.patch.object(client, "_post", side_effect=post):
                        if mode == "json":
                            client.generate_json(stage="STUDY", prompt="Inspect.", inputs={})
                        else:
                            client.generate_tool_turn(
                                stage="STUDY",
                                system_prompt="Inspect.",
                                messages=[{"role": "user", "content": "Inspect."}],
                                tools=[],
                            )

                    if provider == "deepseek":
                        self.assertEqual(captured["thinking"], {"type": "disabled"})
                    else:
                        self.assertNotIn("thinking", captured)

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

    def test_model_wall_deadline_is_configurable_and_bounded(self) -> None:
        environment = {
            "AUTOADAPTER_MODEL_PROVIDER": "openai-compatible",
            "AUTOADAPTER_MODEL_ID": "model",
            "AUTOADAPTER_MODEL_API_BASE_URL": "https://model.example/v1",
            "AUTOADAPTER_MODEL_API_KEY": "secret-value",
            "AUTOADAPTER_MODEL_TIMEOUT_S": "150",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            self.assertEqual(ModelConfig.from_env().timeout_s, 150.0)
        environment["AUTOADAPTER_MODEL_TIMEOUT_S"] = "10"
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ModelInvocationError, "TIMEOUT_S"):
                ModelConfig.from_env()

    @unittest.skipUnless(hasattr(signal, "setitimer"), "requires POSIX wall timer")
    def test_post_enforces_total_wall_deadline(self) -> None:
        client = JsonModelClient(
            ModelConfig(
                provider="company",
                model="deepseek.v3.2",
                base_url="https://model.example/v1",
                api_key="secret-value",
                timeout_s=0.05,
            )
        )

        def stalled_response(*_args: object, **_kwargs: object) -> None:
            time.sleep(1.0)

        with mock.patch.object(client, "_retry_pause") as retry_pause, mock.patch(
            "urllib.request.urlopen", side_effect=stalled_response
        ):
            with self.assertRaisesRegex(ModelInvocationError, "total wall deadline"):
                client._post(stage="repair", body={})

        retry_pause.assert_not_called()
        self.assertEqual(len(client.calls), 1)
        call_record = client.calls[0]
        self.assertEqual(call_record["status"], "timeout")
        self.assertEqual(call_record["stage"], "repair")
        self.assertIsNone(call_record["http_status"])
        self.assertEqual(call_record["error"]["type"], "timeout")
        self.assertGreaterEqual(call_record["elapsed_s"], 0.0)
        self.assertEqual(call_record["retry_index"], 0)
        self.assertIsNone(call_record["retry_of_call_index"])

    def test_transient_http_statuses_retry_once_and_record_each_request(self) -> None:
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                client = JsonModelClient(
                    ModelConfig(
                        provider="company",
                        model="model",
                        base_url="https://model.example/v1",
                        api_key="secret-value",
                    )
                )
                client.set_call_context(cell_id="b1::cell", target_attempt=1)
                headers = Message()
                headers["x-request-id"] = f"req-{status}"
                error = urllib.error.HTTPError(
                    client.config.endpoint_url,
                    status,
                    "Transient provider error",
                    headers,
                    None,
                )
                payload = {
                    "id": f"req-success-{status}",
                    "model": "model",
                    "choices": [],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                }
                response = mock.MagicMock()
                response.__enter__.return_value.status = 200
                response.__enter__.return_value.headers = {}
                response.__enter__.return_value.read.return_value = json.dumps(
                    payload
                ).encode()

                with mock.patch.object(client, "_retry_pause") as retry_pause, mock.patch(
                    "urllib.request.urlopen", side_effect=[error, response]
                ) as urlopen:
                    self.assertEqual(
                        client._post(
                            stage="Repair1", body={"private": "do-not-record"}
                        ),
                        payload,
                    )

                self.assertEqual(urlopen.call_count, 2)
                retry_pause.assert_called_once_with()
                self.assertEqual(len(client.calls), 2)
                failed, succeeded = client.calls
                self.assertEqual(failed["call_index"], 0)
                self.assertEqual(failed["cell_id"], "b1::cell")
                self.assertEqual(failed["target_attempt"], 1)
                self.assertEqual(failed["retry_index"], 0)
                self.assertIsNone(failed["retry_of_call_index"])
                self.assertEqual(failed["status"], "http_error")
                self.assertEqual(failed["http_status"], status)
                self.assertEqual(failed["provider_request_id"], f"req-{status}")
                self.assertEqual(succeeded["call_index"], 1)
                self.assertEqual(succeeded["retry_index"], 1)
                self.assertEqual(succeeded["retry_of_call_index"], 0)
                self.assertEqual(succeeded["status"], "success")
                self.assertEqual(succeeded["http_status"], 200)
                self.assertNotIn("secret-value", json.dumps(client.calls))
                self.assertNotIn("do-not-record", json.dumps(client.calls))

    def test_nontransient_http_error_is_not_retried(self) -> None:
        client = JsonModelClient(
            ModelConfig(
                provider="company",
                model="model",
                base_url="https://model.example/v1",
                api_key="secret-value",
            )
        )
        error = urllib.error.HTTPError(
            client.config.endpoint_url,
            400,
            "Bad Request",
            Message(),
            None,
        )
        with mock.patch.object(client, "_retry_pause") as retry_pause, mock.patch(
            "urllib.request.urlopen", side_effect=error
        ) as urlopen:
            with self.assertRaisesRegex(ModelInvocationError, "HTTP 400"):
                client._post(stage="STUDY", body={})

        self.assertEqual(urlopen.call_count, 1)
        retry_pause.assert_not_called()
        self.assertEqual(len(client.calls), 1)

    def test_transport_timeout_is_recorded(self) -> None:
        client = JsonModelClient(
            ModelConfig(
                provider="company",
                model="model",
                base_url="https://model.example/v1",
                api_key="secret-value",
            )
        )

        with mock.patch.object(client, "_retry_pause") as retry_pause, mock.patch(
            "urllib.request.urlopen", side_effect=TimeoutError()
        ) as urlopen:
            with self.assertRaisesRegex(ModelInvocationError, "TimeoutError"):
                client._post(stage="STUDY", body={})

        self.assertEqual(urlopen.call_count, 1)
        retry_pause.assert_not_called()
        self.assertEqual(len(client.calls), 1)
        record = client.calls[0]
        self.assertEqual(record["status"], "timeout")
        self.assertEqual(record["error"]["type"], "timeout")
        self.assertIsNone(record["input_tokens"])
        self.assertIsNone(record["raw_usage"])
        self.assertEqual(record["retry_index"], 0)
        self.assertIsNone(record["retry_of_call_index"])

    def test_timeout_retry_preserves_request_and_is_shared_across_stages(self) -> None:
        payload = {"id": "reply-2", "choices": [], "usage": {"prompt_tokens": 3}}
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.headers = {"x-request-id": "header-2"}
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        body = {"model": "model", "messages": [{"role": "user", "content": "projected history"}]}
        with TemporaryDirectory() as directory:
            client = JsonModelClient(
                ModelConfig(provider="company", model="model", base_url="https://model.example/v1", api_key="secret-value"),
                evidence_dir=directory, timeout_retries=1,
            )
            requests = []

            def send(request, **_kwargs):
                index = len(requests)
                requests.append(request.data)
                saved = json.loads((Path(directory) / f"call-{index:04d}.json").read_text())
                self.assertEqual(saved["request_body"], json.loads(request.data))
                self.assertEqual(saved["call"]["status"], "in_progress")
                if index != 1:
                    raise TimeoutError()
                return response

            with mock.patch.object(client, "_retry_pause"), mock.patch("urllib.request.urlopen", side_effect=send):
                self.assertEqual(client._post(stage="study", body=body), payload)
                with self.assertRaises(ModelInvocationError):
                    client._post(stage="repair", body=body)
            self.assertEqual(len(requests), 3)
            self.assertEqual(requests[0], requests[1])
            self.assertEqual([r["retry_index"] for r in client.calls], [0, 1, 0])
            first = json.loads((Path(directory) / "call-0000.json").read_text())
            second = json.loads((Path(directory) / "call-0001.json").read_text())
            self.assertEqual(first["call"]["transport_phase"], "awaiting_response_headers")
            self.assertIsNone(first["call"]["time_to_response_headers_s"])
            self.assertEqual(second["call"]["transport_phase"], "complete")
            self.assertEqual(json.loads(second["response_text"]), payload)
            self.assertNotIn("secret-value", "".join(p.read_text() for p in Path(directory).glob("*.json")))

    def test_response_body_timeout_is_distinguished_from_waiting_for_headers(self) -> None:
        with TemporaryDirectory() as directory:
            client = JsonModelClient(
                ModelConfig(provider="company", model="model", base_url="https://model.example/v1", api_key="secret-value"),
                evidence_dir=directory,
            )
            response = mock.MagicMock()
            response.__enter__.return_value.status = 200
            response.__enter__.return_value.headers = {
                "x-request-id": "received-header", "x-amzn-trace-id": "gateway-trace",
                "set-cookie": "do-not-record-cookie",
            }
            response.__enter__.return_value.read.side_effect = TimeoutError()
            with mock.patch("urllib.request.urlopen", return_value=response):
                with self.assertRaises(ModelInvocationError):
                    client._post(stage="study", body={})
            saved = json.loads((Path(directory) / "call-0000.json").read_text())
            self.assertEqual(saved["call"]["transport_phase"], "reading_response_body")
            self.assertEqual(saved["call"]["http_status"], 200)
            self.assertEqual(saved["call"]["provider_request_id"], "received-header")
            self.assertEqual(saved["call"]["response_headers"]["x-amzn-trace-id"], "gateway-trace")
            self.assertNotIn("do-not-record-cookie", json.dumps(saved))
            self.assertIsNotNone(saved["call"]["time_to_response_headers_s"])
            self.assertIsNone(saved["response_text"])

    def test_timeout_opt_in_keeps_two_send_limit_and_rejects_auth_retries(self) -> None:
        for outcomes in ((TimeoutError(), TimeoutError()), (503, TimeoutError()), (401,), (402,)):
            with self.subTest(outcomes=outcomes), TemporaryDirectory() as directory:
                client = JsonModelClient(
                    ModelConfig(provider="company", model="model", base_url="https://model.example/v1", api_key="secret-value"),
                    evidence_dir=directory, timeout_retries=1,
                )
                errors = [
                    urllib.error.HTTPError(client.config.endpoint_url, item, "error", Message(), None)
                    if isinstance(item, int) else item for item in outcomes
                ]
                with mock.patch.object(client, "_retry_pause"), mock.patch("urllib.request.urlopen", side_effect=errors) as send:
                    with self.assertRaises(ModelInvocationError):
                        client._post(stage="study", body={})
                self.assertEqual(send.call_count, len(outcomes))
                self.assertEqual(len(client.calls), len(outcomes))

    def test_successful_call_records_timing_identity_and_normalised_usage(self) -> None:
        client = JsonModelClient(
            ModelConfig(
                provider="company",
                model="requested-model",
                base_url="https://model.example/v1",
                api_key="secret-value",
            )
        )
        client.set_call_context(cell_id="b1::cell", target_attempt=0)
        payload = {
            "id": "req-success",
            "model": "returned-model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"ok": true}'},
                }
            ],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 8,
                "total_tokens": 28,
                "prompt_tokens_details": {"cached_tokens": 3},
                "completion_tokens_details": {"reasoning_tokens": 2},
            },
        }
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.headers = {}
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()

        with mock.patch("urllib.request.urlopen", return_value=response):
            self.assertEqual(
                client.generate_json(stage="GENERATE", prompt="Build.", inputs={}),
                {"ok": True},
            )

        self.assertEqual(len(client.calls), 1)
        record = client.calls[0]
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["http_status"], 200)
        self.assertEqual(record["provider_request_id"], "req-success")
        self.assertEqual(record["requested_model"], "requested-model")
        self.assertEqual(record["returned_model"], "returned-model")
        self.assertEqual(record["input_tokens"], 20)
        self.assertEqual(record["output_tokens"], 8)
        self.assertEqual(record["cache_read_tokens"], 3)
        self.assertIsNone(record["cache_write_tokens"])
        self.assertEqual(record["reasoning_tokens"], 2)
        self.assertEqual(record["total_tokens"], 28)
        self.assertEqual(record["raw_usage"], payload["usage"])
        self.assertGreaterEqual(record["elapsed_s"], 0.0)
        self.assertLessEqual(
            datetime.fromisoformat(record["started_at_utc"]),
            datetime.fromisoformat(record["ended_at_utc"]),
        )
        self.assertNotIn("secret-value", json.dumps(record))

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

    def test_history_character_budget_is_configurable_and_bounded(self) -> None:
        environment = {
            "AUTOADAPTER_MODEL_PROVIDER": "openai-compatible",
            "AUTOADAPTER_MODEL_ID": "model",
            "AUTOADAPTER_MODEL_API_BASE_URL": "https://model.example/v1",
            "AUTOADAPTER_MODEL_API_KEY": "secret-value",
            "AUTOADAPTER_MODEL_HISTORY_CHARS": "64000",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            self.assertEqual(ModelConfig.from_env().history_char_budget, 64000)
        environment["AUTOADAPTER_MODEL_HISTORY_CHARS"] = "4096"
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ModelInvocationError, "HISTORY_CHARS"):
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

    def test_native_history_normalizes_only_empty_assistant_without_tools(self) -> None:
        client = JsonModelClient(
            ModelConfig(
                provider="company",
                model="model",
                base_url="https://model.example/v1",
                api_key="secret-value",
                tool_history_mode="native",
            )
        )
        prior_tool_call = {
            "id": "call-1",
            "type": "function",
            "function": {"name": "inspect", "arguments": "{}"},
        }
        messages = [
            {"role": "user", "content": "Build the driver."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [prior_tool_call],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "{}"},
            {"role": "assistant", "content": None},
            {
                "role": "user",
                "content": "No artifact was submitted. Call submit_driver.",
            },
        ]
        response_payload = {
            "model": "model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ready"},
                }
            ],
            "usage": {},
        }
        captured: dict[str, object] = {}

        def post(*, stage: str, body: dict[str, object]) -> dict:
            del stage
            captured.update(body)
            return response_payload

        with mock.patch.object(client, "_post", side_effect=post):
            client.generate_tool_turn(
                stage="generate",
                system_prompt="Use the tools.",
                messages=messages,
                tools=[],
            )

        provider_messages = captured["messages"]
        self.assertIsInstance(provider_messages, list)
        history = provider_messages[1:]
        self.assertEqual(history[0], messages[0])
        self.assertEqual(history[1], messages[1])
        self.assertEqual(history[2], messages[2])
        self.assertEqual(
            history[3],
            {
                "role": "assistant",
                "content": "No tool call or terminal submission was produced.",
            },
        )
        self.assertEqual(history[4], messages[4])

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
        self.assertEqual(
            client.calls[0]["context_projection"]["mode"], "text-observation"
        )

    def test_text_observation_mode_retains_current_driver_source_exactly_once(self) -> None:
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

        request_body = json.loads(urlopen.call_args.args[0].data)
        history_text = "\n".join(
            str(message.get("content", "")) for message in request_body["messages"]
        )
        self.assertEqual(history_text.count(source), 1)
        self.assertIn("CURRENT_DRIVER_SNAPSHOT_JSON", history_text)
        self.assertIn(f'"source_chars": {len(source)}', history_text)
        self.assertIn("current source is retained separately", history_text)
        context = client.calls[0]["context_projection"]
        self.assertEqual(context["current_driver_revision"], 1)
        self.assertEqual(context["current_driver_source_chars"], len(source))

    def test_context_manager_summarizes_old_tool_groups_and_keeps_latest_driver(self) -> None:
        messages: list[dict[str, object]] = [
            {"role": "user", "content": "INITIAL_PUBLIC_TASK"}
        ]
        sources = []
        for revision in range(1, 6):
            source = f"DRIVER_REVISION_{revision}_" * 200
            sources.append(source)
            call_id = f"write-{revision}"
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
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
                        "tool_call_id": call_id,
                        "content": json.dumps(
                            {"ok": True, "result": {"revision": revision}}
                        ),
                    },
                ]
            )

        projection = AgentContextManager().project_text_observation(messages)
        projected_text = "\n".join(
            str(message.get("content", "")) for message in projection.messages
        )

        self.assertTrue(projected_text.startswith("INITIAL_PUBLIC_TASK"))
        self.assertIn("AGENT_CONTEXT_SUMMARY_JSON", projected_text)
        self.assertIn('"tool": "write_driver"', projected_text)
        self.assertIn('"result_status": "ok"', projected_text)
        self.assertNotIn(sources[0], projected_text)
        self.assertNotIn(sources[3], projected_text)
        self.assertEqual(projected_text.count(sources[-1]), 1)
        self.assertEqual(projection.stats["summarized_group_count"], 2)
        self.assertEqual(projection.stats["retained_group_count"], 3)
        self.assertEqual(projection.stats["current_driver_revision"], 5)

    def test_context_manager_removes_superseded_read_and_write_source_payloads(self) -> None:
        old_source = "OLD_DRIVER_SOURCE" * 500
        current_source = "CURRENT_DRIVER_SOURCE" * 500
        messages = [{"role": "user", "content": "INITIAL_PUBLIC_TASK"}]
        for revision, source in ((1, old_source), (2, current_source)):
            write_id = f"write-{revision}"
            read_id = f"read-{revision}"
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": write_id,
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
                        "tool_call_id": write_id,
                        "content": json.dumps(
                            {"ok": True, "result": {"revision": revision}}
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": read_id,
                                "type": "function",
                                "function": {
                                    "name": "read_driver",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": read_id,
                        "content": json.dumps(
                            {
                                "ok": True,
                                "result": {
                                    "revision": revision,
                                    "source": source,
                                },
                            }
                        ),
                    },
                ]
            )

        projection = AgentContextManager().project_text_observation(messages)
        projected_text = "\n".join(
            str(message.get("content", "")) for message in projection.messages
        )

        self.assertNotIn(old_source, projected_text)
        self.assertEqual(projected_text.count(current_source), 1)
        self.assertIn('"tool": "read_driver"', projected_text)
        self.assertEqual(projection.stats["current_driver_revision"], 2)

    def test_context_manager_keeps_one_latest_file_artifact_snapshot_per_path(self) -> None:
        canonical_paths = (
            "study.json",
            "capability_design.json",
            "capability_validation_suite.json",
            "driver.py",
        )
        old_contents = {
            path: f"OLD_{path}_CONTENT_" * 80 for path in canonical_paths
        }
        current_contents = {
            path: f"CURRENT_{path}_CONTENT_" * 80 for path in canonical_paths
        }
        messages: list[dict[str, object]] = [
            {"role": "user", "content": "INITIAL_PUBLIC_TASK"}
        ]

        def append_write(
            *, call_id: str, path: str, content: str, ok: bool = True
        ) -> None:
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": json.dumps(
                                        {"path": path, "content": content}
                                    ),
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(
                            {
                                "ok": ok,
                                "result": {"path": path, "revision": 2} if ok else None,
                                "error": None if ok else "write rejected",
                            }
                        ),
                    },
                ]
            )

        for path in canonical_paths:
            append_write(
                call_id=f"old-{path}", path=path, content=old_contents[path]
            )
            append_write(
                call_id=f"current-{path}",
                path=path,
                content=current_contents[path],
            )

        read_content = "READ_BACK_CAPABILITY_DESIGN_" * 80
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "read-design",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps(
                                    {"path": "capability_design.json"}
                                ),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "read-design",
                    "content": json.dumps(
                        {
                            "ok": True,
                            "result": {
                                "path": "capability_design.json",
                                "root": "workspace",
                                "content": read_content,
                            },
                        }
                    ),
                },
            ]
        )
        rejected_content = "REJECTED_STUDY_CONTENT_" * 80
        append_write(
            call_id="rejected-study",
            path="study.json",
            content=rejected_content,
            ok=False,
        )
        final_driver = "FINAL_WRITE_ONLY_DRIVER_" * 80
        append_write(
            call_id="final-driver",
            path="driver.py",
            content=final_driver,
        )

        manager = AgentContextManager(history_char_budget=12000, recent_groups=2)
        for mode in ("text", "native"):
            with self.subTest(mode=mode):
                projection = (
                    manager.project_text_observation(messages)
                    if mode == "text"
                    else manager.project_native(messages)
                )
                projected_text = json.dumps(projection.messages, ensure_ascii=True)

                self.assertIn(
                    "CURRENT_CANONICAL_ARTIFACT_SNAPSHOTS_JSON", projected_text
                )
                for content in old_contents.values():
                    self.assertNotIn(content, projected_text)
                self.assertNotIn(rejected_content, projected_text)
                self.assertEqual(
                    projected_text.count(current_contents["study.json"]), 1
                )
                self.assertEqual(
                    projected_text.count(
                        current_contents["capability_validation_suite.json"]
                    ),
                    1,
                )
                self.assertEqual(projected_text.count(read_content), 1)
                self.assertNotIn(
                    current_contents["capability_design.json"], projected_text
                )
                self.assertEqual(projected_text.count(final_driver), 1)
                self.assertNotIn(current_contents["driver.py"], projected_text)
                self.assertEqual(
                    projection.stats["current_artifact_paths"],
                    sorted(canonical_paths),
                )
                self.assertEqual(projection.stats["current_artifact_count"], 4)
                self.assertEqual(
                    projection.stats["current_driver_source_chars"],
                    len(final_driver),
                )
                self.assertGreaterEqual(projection.stats["summarized_group_count"], 8)

    def test_context_manager_combines_successful_append_writes(self) -> None:
        messages = [
            {"role": "user", "content": "INITIAL_PUBLIC_TASK"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "chunk-1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps(
                                {
                                    "path": "capability_design.json",
                                    "content": "CHUNK_ONE_",
                                    "append": False,
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "chunk-1",
                "content": json.dumps(
                    {
                        "ok": True,
                        "result": {
                            "path": "capability_design.json",
                            "append": False,
                        },
                    }
                ),
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "chunk-2",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps(
                                {
                                    "path": "capability_design.json",
                                    "content": "CHUNK_TWO",
                                    "append": True,
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "chunk-2",
                "content": json.dumps(
                    {
                        "ok": True,
                        "result": {
                            "path": "capability_design.json",
                            "append": True,
                        },
                    }
                ),
            },
        ]

        projection = AgentContextManager(history_char_budget=8192).project_native(messages)
        projected = json.dumps(projection.messages, ensure_ascii=True)

        self.assertIn("CHUNK_ONE_CHUNK_TWO", projected)
        self.assertEqual(projection.stats["current_artifact_chars"], 19)
        self.assertEqual(
            projection.stats["current_artifact_paths"],
            ["capability_design.json"],
        )

    def test_native_context_projection_compacts_atomic_check_with_one_snapshot(self) -> None:
        source = "NATIVE_CURRENT_DRIVER" * 400
        checks = [
            {
                "method_name": "drive",
                "request": {"task_id": "task-1", "task_parameters": {}},
            }
        ]
        messages = [
            {"role": "user", "content": "INITIAL_PUBLIC_TASK"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "native-check",
                        "type": "function",
                        "function": {
                            "name": "check_driver",
                            "arguments": json.dumps(
                                {"source": source, "checks": checks}
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "native-check",
                "content": json.dumps(
                    {
                        "ok": True,
                        "result": {"revision": 1, "successful": False},
                    }
                ),
            },
        ]

        projection = AgentContextManager().project_native(messages)
        projected_text = json.dumps(projection.messages, ensure_ascii=True)

        self.assertEqual(
            [message["role"] for message in projection.messages],
            ["user", "assistant", "tool"],
        )
        self.assertEqual(projected_text.count(source), 1)
        projected_arguments = json.loads(
            projection.messages[1]["tool_calls"][0]["function"]["arguments"]
        )
        self.assertNotIn("source", projected_arguments)
        self.assertEqual(projected_arguments["source_chars"], len(source))
        self.assertEqual(projected_arguments["checks"], checks)
        self.assertEqual(projection.stats["mode"], "native")

    def test_failed_write_does_not_replace_last_successful_driver_snapshot(self) -> None:
        accepted_source = "ACCEPTED_DRIVER_SOURCE" * 200
        rejected_source = "REJECTED_DRIVER_SOURCE" * 200
        messages = [{"role": "user", "content": "INITIAL_PUBLIC_TASK"}]
        for call_id, source, ok, result in (
            ("accepted", accepted_source, True, {"revision": 1}),
            ("rejected", rejected_source, False, None),
        ):
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
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
                        "tool_call_id": call_id,
                        "content": json.dumps(
                            {
                                "ok": ok,
                                "result": result,
                                "error": None if ok else "write rejected",
                            }
                        ),
                    },
                ]
            )

        projection = AgentContextManager().project_text_observation(messages)
        projected_text = json.dumps(projection.messages, ensure_ascii=True)

        self.assertEqual(projected_text.count(accepted_source), 1)
        self.assertNotIn(rejected_source, projected_text)
        self.assertEqual(projection.stats["current_driver_revision"], 1)
        self.assertIn("write rejected", projected_text)

    def test_context_budget_evicts_old_completed_groups_before_latest_group(self) -> None:
        messages: list[dict[str, object]] = [
            {"role": "user", "content": "INITIAL_PUBLIC_TASK"}
        ]
        for index in range(5):
            call_id = f"probe-{index}"
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": "run_mujoco_probe",
                                    "arguments": json.dumps(
                                        {
                                            "probe_id": call_id,
                                            "script": "SCRIPT_BODY" * 1000,
                                        }
                                    ),
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(
                            {
                                "ok": True,
                                "result": {
                                    "exit_code": 0,
                                    "stdout": "x" * 5000,
                                },
                            }
                        ),
                    },
                ]
            )

        projection = AgentContextManager(
            history_char_budget=12000
        ).project_text_observation(messages)
        projected_text = "\n".join(
            str(message.get("content", "")) for message in projection.messages
        )

        self.assertNotIn("SCRIPT_BODY" * 1000, projected_text)
        self.assertIn('"script_chars": 11000', projected_text)
        self.assertGreaterEqual(projection.stats["summarized_group_count"], 3)
        self.assertLessEqual(projection.stats["projected_history_chars"], 12000)
        self.assertFalse(projection.stats["budget_exceeded"])

if __name__ == "__main__":
    unittest.main()
