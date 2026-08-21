from __future__ import annotations

import json
import os
import signal
import time
import unittest
import urllib.error
from datetime import datetime
from email.message import Message
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

        with mock.patch("urllib.request.urlopen", side_effect=stalled_response):
            with self.assertRaisesRegex(ModelInvocationError, "total wall deadline"):
                client._post(stage="repair", body={})

        self.assertEqual(len(client.calls), 1)
        call_record = client.calls[0]
        self.assertEqual(call_record["status"], "timeout")
        self.assertEqual(call_record["stage"], "repair")
        self.assertIsNone(call_record["http_status"])
        self.assertEqual(call_record["error"]["type"], "timeout")
        self.assertGreaterEqual(call_record["elapsed_s"], 0.0)

    def test_http_429_is_recorded_without_request_secrets(self) -> None:
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
        headers["x-request-id"] = "req-429"
        error = urllib.error.HTTPError(
            client.config.endpoint_url,
            429,
            "Too Many Requests",
            headers,
            None,
        )

        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(ModelInvocationError, "HTTP 429"):
                client._post(stage="Repair1", body={"private": "do-not-record"})

        self.assertEqual(len(client.calls), 1)
        record = client.calls[0]
        self.assertEqual(record["call_index"], 0)
        self.assertEqual(record["cell_id"], "b1::cell")
        self.assertEqual(record["target_attempt"], 1)
        self.assertEqual(record["retry_index"], 0)
        self.assertIsNone(record["retry_of_call_index"])
        self.assertEqual(record["status"], "http_error")
        self.assertEqual(record["http_status"], 429)
        self.assertEqual(record["provider_request_id"], "req-429")
        self.assertNotIn("secret-value", json.dumps(record))
        self.assertNotIn("do-not-record", json.dumps(record))

    def test_transport_timeout_is_recorded(self) -> None:
        client = JsonModelClient(
            ModelConfig(
                provider="company",
                model="model",
                base_url="https://model.example/v1",
                api_key="secret-value",
            )
        )

        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError()):
            with self.assertRaisesRegex(ModelInvocationError, "TimeoutError"):
                client._post(stage="STUDY", body={})

        self.assertEqual(len(client.calls), 1)
        record = client.calls[0]
        self.assertEqual(record["status"], "timeout")
        self.assertEqual(record["error"]["type"], "timeout")
        self.assertIsNone(record["input_tokens"])
        self.assertIsNone(record["raw_usage"])

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
