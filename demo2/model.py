"""Small Bedrock JSON boundary for Demo2 Stage 1 and Blue Line."""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


class ModelInvocationError(RuntimeError):
    """A model call failed or did not return one JSON object."""


def _parse_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```json"):
        value = value[7:]
    elif value.startswith("```"):
        value = value[3:]
    if value.endswith("```"):
        value = value[:-3]
    value = value.strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        if start < 0:
            raise ModelInvocationError("model returned no JSON object") from None
        try:
            parsed, end = json.JSONDecoder().raw_decode(value[start:])
        except json.JSONDecodeError as exc:
            raise ModelInvocationError("model returned malformed JSON") from exc
        if value[start + end :].strip():
            raise ModelInvocationError("model returned text after the JSON object")
    if not isinstance(parsed, dict):
        raise ModelInvocationError("model must return one JSON object")
    return parsed


class BedrockJsonGenerator:
    """Implements the existing Stage-1 ``JsonGenerator`` protocol via Converse."""

    def __init__(
        self,
        *,
        model: str,
        region: str = "us-east-1",
        max_tokens: int = 8000,
    ) -> None:
        from botocore.config import Config
        import boto3

        self.model = model
        self.region = region
        self.max_tokens = int(max_tokens)
        self.calls: list[dict[str, Any]] = []
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                read_timeout=300,
                connect_timeout=30,
                retries={"max_attempts": 4, "mode": "adaptive"},
            ),
        )

    def generate_json(
        self,
        stage: str,
        prompt: str,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        user_text = (
            "Use the following JSON inputs. Return only the JSON object required by "
            "the system instruction.\n\n"
            + json.dumps(dict(inputs), ensure_ascii=False, sort_keys=True)
        )
        try:
            response = self._client.converse(
                modelId=self.model,
                system=[{"text": prompt}],
                messages=[{"role": "user", "content": [{"text": user_text}]}],
                inferenceConfig={"maxTokens": self.max_tokens, "temperature": 0.0},
            )
        except Exception as exc:  # provider errors remain explicit run blockers
            raise ModelInvocationError(
                f"{stage} model call failed for {self.model}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        content = response.get("output", {}).get("message", {}).get("content", [])
        text = "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and "text" in block
        )
        result = _parse_json_object(text)
        self.calls.append({
            "stage": stage,
            "model": self.model,
            "stop_reason": response.get("stopReason"),
            "usage": response.get("usage", {}),
        })
        return result


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    """Runtime-only credentials/configuration for an OpenAI-style endpoint."""

    api_key: str
    base_url: str
    model: str
    endpoint_path: str = "/v1/chat/completions"
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    timeout_s: float = 300.0

    @property
    def endpoint_url(self) -> str:
        if self.base_url.rstrip("/").endswith("/chat/completions"):
            return self.base_url.rstrip("/")
        return self.base_url.rstrip("/") + "/" + self.endpoint_path.lstrip("/")


def _post_chat(config: OpenAICompatibleConfig, body: Mapping[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        config.endpoint_url,
        data=json.dumps(dict(body), ensure_ascii=False).encode("utf-8"),
        headers={
            config.auth_header: config.auth_prefix + config.api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=config.timeout_s,
            context=ssl.create_default_context(),
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ModelInvocationError(
            f"model API returned HTTP {exc.code} for {config.model}"
        ) from exc
    except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelInvocationError(
            f"model API call failed for {config.model}: {type(exc).__name__}"
        ) from exc
    if not isinstance(payload, dict):
        raise ModelInvocationError("model API response must be a JSON object")
    return payload


def _first_choice(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ModelInvocationError("model API response lacks choices[0]")
    return choices[0]


class OpenAIJsonGenerator:
    """JSON generator for Stage 1 and Blue Line using a supplied API endpoint."""

    def __init__(self, config: OpenAICompatibleConfig, *, max_tokens: int = 8000) -> None:
        self.config = config
        self.max_tokens = int(max_tokens)
        self.calls: list[dict[str, Any]] = []

    def generate_json(
        self,
        stage: str,
        prompt: str,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = _post_chat(self.config, {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return exactly one JSON object and no Markdown. "
                        f"You are the isolated AutoAdapter stage: {stage}."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt + "\n\nINPUT_JSON:\n" + json.dumps(
                        dict(inputs), ensure_ascii=False, sort_keys=True
                    ),
                },
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        })
        choice = _first_choice(payload)
        message = choice.get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str):
            raise ModelInvocationError("model API response lacks text content")
        result = _parse_json_object(content)
        self.calls.append({
            "stage": stage,
            "model": self.config.model,
            "returned_model": payload.get("model"),
            "usage": payload.get("usage", {}),
        })
        return result


class _ReactBlock:
    __slots__ = ("type", "text", "name", "input", "id")

    def __init__(self, kind: str, *, text: str | None = None, name: str | None = None,
                 value: Mapping[str, Any] | None = None, identifier: str | None = None) -> None:
        self.type = kind
        self.text = text
        self.name = name
        self.input = dict(value or {})
        self.id = identifier


class _OpenAIMessages:
    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config

    @staticmethod
    def _messages(system: str | None, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        if system:
            converted.append({"role": "system", "content": system})
        for message in messages:
            role = message["role"]
            content = message["content"]
            if isinstance(content, str):
                converted.append({"role": role, "content": content})
                continue
            if role == "assistant":
                texts: list[str] = []
                calls: list[dict[str, Any]] = []
                for block in content:
                    if getattr(block, "type", None) == "text" and getattr(block, "text", None):
                        texts.append(block.text)
                    elif getattr(block, "type", None) == "tool_use":
                        calls.append({
                            "id": block.id,
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": json.dumps(block.input or {}, ensure_ascii=False),
                            },
                        })
                item: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
                if calls:
                    item["tool_calls"] = calls
                converted.append(item)
                continue
            for block in content:
                if isinstance(block, Mapping) and block.get("type") == "tool_result":
                    converted.append({
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": str(block.get("content", "")),
                    })
        return converted

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        **_ignored: Any,
    ) -> SimpleNamespace:
        body: dict[str, Any] = {
            "model": model,
            "messages": self._messages(system, messages),
            "max_tokens": int(max_tokens),
        }
        if temperature is not None:
            body["temperature"] = float(temperature)
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", tool["name"]),
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in tools
            ]
            body["tool_choice"] = "auto"
        payload = _post_chat(self.config, body)
        choice = _first_choice(payload)
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise ModelInvocationError("model API response lacks assistant message")
        blocks: list[_ReactBlock] = []
        content = message.get("content")
        if isinstance(content, str) and content:
            blocks.append(_ReactBlock("text", text=content))
        tool_calls = message.get("tool_calls", [])
        if tool_calls is None:
            tool_calls = []
        if not isinstance(tool_calls, list):
            raise ModelInvocationError("model API tool_calls must be an array")
        for call in tool_calls:
            function = call.get("function") if isinstance(call, Mapping) else None
            if not isinstance(function, Mapping):
                raise ModelInvocationError("model API returned an invalid tool call")
            arguments = function.get("arguments", "{}")
            if isinstance(arguments, str):
                try:
                    parsed_arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise ModelInvocationError("model API returned invalid tool arguments") from exc
            elif isinstance(arguments, Mapping):
                parsed_arguments = dict(arguments)
            else:
                raise ModelInvocationError("model API returned invalid tool arguments")
            blocks.append(_ReactBlock(
                "tool_use",
                name=str(function.get("name", "")),
                value=parsed_arguments,
                identifier=str(call.get("id", "")),
            ))
        finish = choice.get("finish_reason")
        stop_reason = "tool_use" if tool_calls else (
            "max_tokens" if finish == "length" else "end_turn"
        )
        usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
        return SimpleNamespace(
            content=blocks,
            usage=SimpleNamespace(
                input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                output_tokens=int(usage.get("completion_tokens", 0) or 0),
                cache_read_input_tokens=0,
            ),
            stop_reason=stop_reason,
        )


class OpenAIReactClient:
    """Anthropic-shaped client adapter used by the unmodified 1.0 ReactLoop."""

    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.messages = _OpenAIMessages(config)
