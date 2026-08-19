"""Minimal real-model JSON boundary for Demo3 model-authored stages."""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .react import ToolCall, ToolTurn


class ModelInvocationError(RuntimeError):
    """Raised when the configured model service cannot return one JSON object."""


def parse_json_object(text: str) -> dict[str, Any]:
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
            raise ModelInvocationError("model returned trailing text after its JSON object")
    if not isinstance(parsed, dict):
        raise ModelInvocationError("model must return one JSON object")
    return parsed


@dataclass(frozen=True)
class ModelConfig:
    """Runtime configuration loaded from the existing AutoAdapter environment."""

    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    api_protocol: str = "openai-compatible"
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    thinking: str | None = None
    timeout_s: float = 300.0
    max_tokens: int = 16000
    tool_history_mode: str = "native"

    @classmethod
    def from_env(cls) -> ModelConfig:
        environment = os.environ
        api_protocol = environment.get("AUTOADAPTER_MODEL_PROVIDER", "").strip()
        if api_protocol not in {"openai", "openai-compatible"}:
            raise ModelInvocationError(
                "AUTOADAPTER_MODEL_PROVIDER must be openai or openai-compatible"
            )
        required = {
            "model": environment.get("AUTOADAPTER_MODEL_ID", "").strip(),
            "base_url": environment.get("AUTOADAPTER_MODEL_API_BASE_URL", "").strip(),
            "api_key": environment.get("AUTOADAPTER_MODEL_API_KEY", "").strip(),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ModelInvocationError(f"missing model configuration: {', '.join(missing)}")
        thinking = environment.get("AUTOADAPTER_MODEL_THINKING", "").strip() or None
        try:
            max_tokens = int(
                environment.get("AUTOADAPTER_MODEL_MAX_TOKENS", "32768")
            )
        except ValueError as exc:
            raise ModelInvocationError(
                "AUTOADAPTER_MODEL_MAX_TOKENS must be an integer"
            ) from exc
        if not 1024 <= max_tokens <= 65536:
            raise ModelInvocationError(
                "AUTOADAPTER_MODEL_MAX_TOKENS must be between 1024 and 65536"
            )
        tool_history_mode = environment.get(
            "AUTOADAPTER_MODEL_TOOL_HISTORY_MODE", "native"
        ).strip() or "native"
        if tool_history_mode not in {"native", "text-observation"}:
            raise ModelInvocationError(
                "AUTOADAPTER_MODEL_TOOL_HISTORY_MODE must be native or "
                "text-observation"
            )
        hostname = (urllib.parse.urlparse(required["base_url"]).hostname or "").lower()
        provider = environment.get("AUTOADAPTER_MODEL_VENDOR", "").strip()
        if not provider:
            provider = "deepseek" if hostname.endswith("deepseek.com") else api_protocol
        return cls(
            provider=provider,
            model=required["model"],
            base_url=required["base_url"],
            api_key=required["api_key"],
            api_protocol=api_protocol,
            auth_header=environment.get(
                "AUTOADAPTER_MODEL_API_AUTH_HEADER", "Authorization"
            ).strip()
            or "Authorization",
            auth_prefix=environment.get(
                "AUTOADAPTER_MODEL_API_AUTH_PREFIX", "Bearer "
            ),
            thinking=thinking,
            max_tokens=max_tokens,
            tool_history_mode=tool_history_mode,
        )

    @property
    def endpoint_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"


class JsonModelClient:
    """Small OpenAI-compatible client with concise, secret-free call evidence."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _text_history_arguments(tool: Any, arguments: Any) -> Any:
        if tool != "write_driver" or not isinstance(arguments, str):
            return arguments
        try:
            decoded = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments
        if not isinstance(decoded, Mapping):
            return arguments
        source = decoded.get("source")
        if not isinstance(source, str):
            return arguments
        return {
            "source_chars": len(source),
            "source_history": (
                "omitted after tool execution; call read_driver for current source"
            ),
        }

    def _tool_history_messages(
        self, messages: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        if self.config.tool_history_mode == "native":
            return [dict(message) for message in messages]

        translated: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            raw_content = message.get("content")
            content = raw_content if isinstance(raw_content, str) else ""
            raw_calls = message.get("tool_calls")
            if role == "assistant" and isinstance(raw_calls, list) and raw_calls:
                requests = []
                for raw_call in raw_calls:
                    if not isinstance(raw_call, Mapping):
                        continue
                    function = raw_call.get("function")
                    tool = (
                        function.get("name")
                        if isinstance(function, Mapping)
                        else None
                    )
                    arguments = (
                        function.get("arguments")
                        if isinstance(function, Mapping)
                        else None
                    )
                    requests.append(
                        {
                            "tool_call_id": raw_call.get("id"),
                            "tool": tool,
                            "arguments": self._text_history_arguments(
                                tool, arguments
                            ),
                        }
                    )
                parts = [content] if content.strip() else []
                parts.append(
                    "TOOL_REQUESTS_JSON:\n"
                    + json.dumps(requests, ensure_ascii=True, sort_keys=True)
                )
                translated.append(
                    {"role": "assistant", "content": "\n\n".join(parts)}
                )
                continue
            if role == "tool":
                observation = "TOOL_OBSERVATION_JSON:\n" + json.dumps(
                    {
                        "tool_call_id": message.get("tool_call_id"),
                        "content": content,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
                if translated and translated[-1].get("role") == "user":
                    translated[-1]["content"] += "\n\n" + observation
                else:
                    translated.append({"role": "user", "content": observation})
                continue

            if role not in {"assistant", "user"}:
                translated.append(dict(message))
                continue
            if role == "assistant" and not content.strip():
                content = "No tool call or terminal submission was produced."
            if role == "user" and translated and translated[-1].get("role") == "user":
                translated[-1]["content"] += "\n\n" + content
            else:
                translated.append({"role": role, "content": content})
        return translated

    def _post(self, *, stage: str, body: Mapping[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.config.endpoint_url,
            data=json.dumps(dict(body), ensure_ascii=True).encode("utf-8"),
            headers={
                self.config.auth_header: self.config.auth_prefix + self.config.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.config.timeout_s,
                context=ssl.create_default_context(),
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ModelInvocationError(
                f"{stage} model call returned HTTP {exc.code} for {self.config.model}"
            ) from exc
        except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelInvocationError(
                f"{stage} model call failed for {self.config.model}: {type(exc).__name__}"
            ) from exc
        if not isinstance(payload, dict):
            raise ModelInvocationError("model API response must be a JSON object")
        return payload

    @staticmethod
    def _first_choice(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ModelInvocationError("model API response lacks choices[0]")
        return choices[0]

    def _record_call(
        self,
        *,
        stage: str,
        payload: Mapping[str, Any],
        mode: str,
        finish_reason: Any = None,
        tool_names: Sequence[str] = (),
    ) -> None:
        self.calls.append(
            {
                "stage": stage,
                "mode": mode,
                "provider": self.config.provider,
                "api_protocol": self.config.api_protocol,
                "requested_model": self.config.model,
                "returned_model": payload.get("model"),
                "finish_reason": finish_reason,
                "tool_names": list(tool_names),
                "tool_history_mode": self.config.tool_history_mode,
                "usage": payload.get("usage", {}),
            }
        )

    def generate_json(
        self,
        *,
        stage: str,
        prompt: str,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return exactly one JSON object and no Markdown.",
                },
                {
                    "role": "user",
                    "content": prompt
                    + "\n\nPUBLIC_INPUT_JSON:\n"
                    + json.dumps(dict(inputs), ensure_ascii=True, sort_keys=True),
                },
            ],
            "max_tokens": self.config.max_tokens,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        if self.config.thinking and self.config.thinking != "disabled":
            body["thinking"] = {"type": self.config.thinking}
        payload = self._post(stage=stage, body=body)
        choice = self._first_choice(payload)
        message = choice.get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str):
            raise ModelInvocationError("model API response lacks text content")
        try:
            result = parse_json_object(content)
        except ModelInvocationError as exc:
            finish_reason = choice.get("finish_reason")
            raise ModelInvocationError(
                f"{exc}; finish_reason={finish_reason!r}; content_chars={len(content)}"
            ) from exc
        self._record_call(
            stage=stage,
            payload=payload,
            mode="json",
            finish_reason=choice.get("finish_reason"),
        )
        return result

    def generate_tool_turn(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> ToolTurn:
        """Return one assistant turn while preserving tool-call conversation state."""

        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *self._tool_history_messages(messages),
            ],
            "max_tokens": self.config.max_tokens,
            "temperature": 0.0,
            "tools": [dict(tool) for tool in tools],
            "tool_choice": "auto",
        }
        if self.config.thinking and self.config.thinking != "disabled":
            body["thinking"] = {"type": self.config.thinking}
        payload = self._post(stage=stage, body=body)
        choice = self._first_choice(payload)
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise ModelInvocationError("model API response lacks choices[0].message")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ModelInvocationError("tool-call response content must be text or null")
        reasoning_content = message.get("reasoning_content")
        if reasoning_content is not None and not isinstance(reasoning_content, str):
            reasoning_content = None

        parsed_calls: list[ToolCall] = []
        raw_calls = message.get("tool_calls", [])
        if raw_calls is None:
            raw_calls = []
        if not isinstance(raw_calls, list):
            raise ModelInvocationError("tool-call response tool_calls must be a list")
        for index, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, Mapping):
                raise ModelInvocationError(f"tool_calls[{index}] must be an object")
            call_id = raw_call.get("id")
            function = raw_call.get("function")
            if not isinstance(call_id, str) or not call_id:
                raise ModelInvocationError(f"tool_calls[{index}] lacks an id")
            if not isinstance(function, Mapping):
                raise ModelInvocationError(f"tool_calls[{index}] lacks function")
            name = function.get("name")
            raw_arguments = function.get("arguments", "{}")
            if not isinstance(name, str) or not name:
                raise ModelInvocationError(f"tool_calls[{index}] lacks function.name")
            if isinstance(raw_arguments, Mapping):
                raw_arguments = json.dumps(dict(raw_arguments), ensure_ascii=True)
            if not isinstance(raw_arguments, str):
                raw_arguments = json.dumps(raw_arguments, ensure_ascii=True)
            arguments: Mapping[str, Any] | None = None
            argument_error: str | None = None
            try:
                decoded_arguments = json.loads(raw_arguments)
                if isinstance(decoded_arguments, Mapping):
                    arguments = dict(decoded_arguments)
                else:
                    argument_error = "tool arguments must decode to one JSON object"
            except json.JSONDecodeError as exc:
                argument_error = f"tool arguments are malformed JSON: {exc.msg}"
            parsed_calls.append(
                ToolCall(
                    id=call_id,
                    name=name,
                    arguments=arguments,
                    raw_arguments=raw_arguments,
                    argument_error=argument_error,
                )
            )

        finish_reason = choice.get("finish_reason")
        self._record_call(
            stage=stage,
            payload=payload,
            mode="react",
            finish_reason=finish_reason,
            tool_names=[call.name for call in parsed_calls],
        )
        return ToolTurn(
            content=content,
            tool_calls=tuple(parsed_calls),
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            reasoning_content=reasoning_content,
        )
