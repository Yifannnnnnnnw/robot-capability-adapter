"""Minimal real-model JSON boundary for mainline model-authored stages."""

from __future__ import annotations

import contextlib
import copy
import json
import os
import posixpath
import signal
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .agent_context import AgentContextManager
from .react import ToolCall, ToolTurn


class ModelInvocationError(RuntimeError):
    """Raised when the configured model service cannot return one JSON object."""


class _ModelCallDeadline(TimeoutError):
    pass


_RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_PHYSICAL_REQUESTS = 2
_RETRY_BACKOFF_S = 1.0
_EMPTY_NATIVE_ASSISTANT_CONTENT = (
    "No tool call or terminal submission was produced."
)


def _is_normalized_absolute_endpoint_path(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
    ):
        return False
    parsed = urllib.parse.urlsplit(value)
    return (
        parsed.scheme == ""
        and parsed.netloc == ""
        and parsed.query == ""
        and parsed.fragment == ""
        and "\\" not in value
        and posixpath.normpath(value) == value
    )


@contextlib.contextmanager
def _model_call_deadline(seconds: float):
    """Enforce total request wall time, not only per-socket inactivity."""

    if (
        seconds <= 0
        or not hasattr(signal, "setitimer")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)

    def handle_timeout(_signum: int, _frame: object) -> None:
        raise _ModelCallDeadline(f"model call exceeded {seconds:g} wall seconds")

    signal.signal(signal.SIGALRM, handle_timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    started = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - started
        restored_delay = (
            max(1e-9, previous_timer[0] - elapsed)
            if previous_timer[0] > 0
            else 0.0
        )
        signal.setitimer(signal.ITIMER_REAL, restored_delay, previous_timer[1])
        signal.signal(signal.SIGALRM, previous_handler)


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


def _contains_text(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, Mapping):
        return any(
            _contains_text(key, needle) or _contains_text(item, needle)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return any(_contains_text(item, needle) for item in value)
    return False


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
    timeout_s: float = 180.0
    max_tokens: int = 16000
    tool_history_mode: str = "native"
    history_char_budget: int = 80000
    endpoint_path: str = "/chat/completions"

    def __post_init__(self) -> None:
        if not _is_normalized_absolute_endpoint_path(self.endpoint_path):
            raise ValueError("endpoint_path must be a normalized absolute URL path")

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
                environment.get("AUTOADAPTER_MODEL_MAX_TOKENS", "16384")
            )
        except ValueError as exc:
            raise ModelInvocationError(
                "AUTOADAPTER_MODEL_MAX_TOKENS must be an integer"
            ) from exc
        if not 1024 <= max_tokens <= 65536:
            raise ModelInvocationError(
                "AUTOADAPTER_MODEL_MAX_TOKENS must be between 1024 and 65536"
            )
        try:
            timeout_s = float(
                environment.get("AUTOADAPTER_MODEL_TIMEOUT_S", "180")
            )
        except ValueError as exc:
            raise ModelInvocationError(
                "AUTOADAPTER_MODEL_TIMEOUT_S must be numeric"
            ) from exc
        if not 30 <= timeout_s <= 600:
            raise ModelInvocationError(
                "AUTOADAPTER_MODEL_TIMEOUT_S must be between 30 and 600"
            )
        endpoint_path = environment.get(
            "AUTOADAPTER_MODEL_API_ENDPOINT_PATH", "/chat/completions"
        ).strip() or "/chat/completions"
        if not _is_normalized_absolute_endpoint_path(endpoint_path):
            raise ModelInvocationError(
                "AUTOADAPTER_MODEL_API_ENDPOINT_PATH must be a normalized "
                "absolute URL path"
            )
        tool_history_mode = environment.get(
            "AUTOADAPTER_MODEL_TOOL_HISTORY_MODE", "native"
        ).strip() or "native"
        if tool_history_mode not in {"native", "text-observation"}:
            raise ModelInvocationError(
                "AUTOADAPTER_MODEL_TOOL_HISTORY_MODE must be native or "
                "text-observation"
            )
        try:
            history_char_budget = int(
                environment.get("AUTOADAPTER_MODEL_HISTORY_CHARS", "80000")
            )
        except ValueError as exc:
            raise ModelInvocationError(
                "AUTOADAPTER_MODEL_HISTORY_CHARS must be an integer"
            ) from exc
        if not 8192 <= history_char_budget <= 500000:
            raise ModelInvocationError(
                "AUTOADAPTER_MODEL_HISTORY_CHARS must be between 8192 and 500000"
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
            endpoint_path=endpoint_path,
            api_protocol=api_protocol,
            auth_header=environment.get(
                "AUTOADAPTER_MODEL_API_AUTH_HEADER", "Authorization"
            ).strip()
            or "Authorization",
            auth_prefix=environment.get(
                "AUTOADAPTER_MODEL_API_AUTH_PREFIX", "Bearer "
            ),
            thinking=thinking,
            timeout_s=timeout_s,
            max_tokens=max_tokens,
            tool_history_mode=tool_history_mode,
            history_char_budget=history_char_budget,
        )

    @property
    def endpoint_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith(self.endpoint_path):
            return base
        return base + self.endpoint_path


class JsonModelClient:
    """Small OpenAI-compatible client with concise, secret-free call evidence."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self.calls: list[dict[str, Any]] = []
        self._calls_lock = threading.Lock()
        self._message_json_exchanges: list[dict[str, Any]] = []
        self._message_json_exchanges_lock = threading.Lock()
        self._call_state = threading.local()
        self._context_manager = AgentContextManager(
            history_char_budget=config.history_char_budget,
            recent_groups=3,
        )

    def set_call_context(
        self,
        *,
        cell_id: str | None = None,
        target_attempt: int | None = None,
    ) -> None:
        """Associate subsequent calls in this thread with one experiment cell."""

        if cell_id is not None and (not isinstance(cell_id, str) or not cell_id):
            raise ValueError("cell_id must be a non-empty string or None")
        if target_attempt is not None and (
            isinstance(target_attempt, bool)
            or not isinstance(target_attempt, int)
            or target_attempt < 0
        ):
            raise ValueError("target_attempt must be a non-negative integer or None")
        self._call_state.context = {
            "cell_id": cell_id,
            "target_attempt": target_attempt,
        }

    def clear_call_context(self) -> None:
        """Remove the current thread's experiment-cell association."""

        self._call_state.context = {"cell_id": None, "target_attempt": None}

    def _project_tool_history(
        self, messages: Sequence[Mapping[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if self.config.tool_history_mode == "native":
            native_messages = []
            for message in messages:
                current = dict(message)
                content = current.get("content")
                empty_content = content is None or (
                    isinstance(content, str) and not content.strip()
                )
                if (
                    current.get("role") == "assistant"
                    and empty_content
                    and not current.get("tool_calls")
                ):
                    current["content"] = _EMPTY_NATIVE_ASSISTANT_CONTENT
                native_messages.append(current)
            projection = self._context_manager.project_native(native_messages)
        else:
            projection = self._context_manager.project_text_observation(messages)
        return [dict(message) for message in projection.messages], dict(projection.stats)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _http_status(response: Any) -> int | None:
        value = getattr(response, "status", None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        getcode = getattr(response, "getcode", None)
        if callable(getcode):
            value = getcode()
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    @staticmethod
    def _provider_request_id(
        payload: Mapping[str, Any] | None,
        headers: Any,
    ) -> str | None:
        if payload is not None:
            for key in ("id", "request_id", "requestId"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
        getter = getattr(headers, "get", None)
        if callable(getter):
            for key in (
                "x-request-id",
                "request-id",
                "x-amzn-requestid",
                "x-amz-request-id",
            ):
                value = getter(key)
                if isinstance(value, str) and value:
                    return value
        return None

    @staticmethod
    def _token_count(value: Any) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return None

    @classmethod
    def _normalised_usage(cls, usage: Any) -> dict[str, Any]:
        if not isinstance(usage, Mapping):
            return {
                "input_tokens": None,
                "output_tokens": None,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
                "reasoning_tokens": None,
                "total_tokens": None,
                "other_provider_token_categories": None,
            }

        prompt_details = usage.get("prompt_tokens_details")
        if not isinstance(prompt_details, Mapping):
            prompt_details = {}
        completion_details = usage.get("completion_tokens_details")
        if not isinstance(completion_details, Mapping):
            completion_details = {}
        output_details = usage.get("output_tokens_details")
        if not isinstance(output_details, Mapping):
            output_details = {}

        def first_count(*values: Any) -> int | None:
            for value in values:
                count = cls._token_count(value)
                if count is not None:
                    return count
            return None

        known_top_level = {
            "input_tokens",
            "prompt_tokens",
            "output_tokens",
            "completion_tokens",
            "cache_read_tokens",
            "cache_read_input_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "cache_creation_input_tokens",
            "reasoning_tokens",
            "total_tokens",
            "prompt_tokens_details",
            "completion_tokens_details",
            "output_tokens_details",
        }
        other = {
            str(key): value
            for key, value in usage.items()
            if key not in known_top_level
        }
        return {
            "input_tokens": first_count(
                usage.get("input_tokens"), usage.get("prompt_tokens")
            ),
            "output_tokens": first_count(
                usage.get("output_tokens"), usage.get("completion_tokens")
            ),
            "cache_read_tokens": first_count(
                usage.get("cache_read_tokens"),
                usage.get("cache_read_input_tokens"),
                usage.get("cached_input_tokens"),
                prompt_details.get("cached_tokens"),
            ),
            "cache_write_tokens": first_count(
                usage.get("cache_write_tokens"),
                usage.get("cache_creation_input_tokens"),
            ),
            "reasoning_tokens": first_count(
                usage.get("reasoning_tokens"),
                completion_details.get("reasoning_tokens"),
                output_details.get("reasoning_tokens"),
            ),
            "total_tokens": first_count(usage.get("total_tokens")),
            "other_provider_token_categories": other or None,
        }

    @staticmethod
    def _response_semantics(payload: Mapping[str, Any]) -> tuple[Any, list[str]]:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None, []
        choice = choices[0]
        if not isinstance(choice, Mapping):
            return None, []
        tool_names: list[str] = []
        message = choice.get("message")
        raw_calls = message.get("tool_calls") if isinstance(message, Mapping) else None
        if isinstance(raw_calls, list):
            for raw_call in raw_calls:
                function = raw_call.get("function") if isinstance(raw_call, Mapping) else None
                name = function.get("name") if isinstance(function, Mapping) else None
                if isinstance(name, str) and name:
                    tool_names.append(name)
        return choice.get("finish_reason"), tool_names

    def _new_call_record(
        self,
        *,
        stage: str,
        retry_of_call_index: int | None = None,
        retry_index: int = 0,
    ) -> tuple[int, dict[str, Any]]:
        context = getattr(self._call_state, "context", {})
        record: dict[str, Any] = {
            "call_index": -1,
            "cell_id": context.get("cell_id"),
            "stage": stage,
            "target_attempt": context.get("target_attempt"),
            "retry_of_call_index": retry_of_call_index,
            "retry_index": retry_index,
            "started_at_utc": self._utc_now(),
            "ended_at_utc": None,
            "elapsed_s": None,
            "status": "in_progress",
            "http_status": None,
            "error": None,
            "provider_request_id": None,
            "mode": getattr(self._call_state, "mode", "unknown"),
            "provider": self.config.provider,
            "api_protocol": self.config.api_protocol,
            "requested_model": self.config.model,
            "returned_model": None,
            "finish_reason": None,
            "tool_names": [],
            "tool_history_mode": self.config.tool_history_mode,
            "context_projection": dict(
                getattr(self._call_state, "context_projection", {})
            ),
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
            "other_provider_token_categories": None,
            "raw_usage": None,
            # Historical consumers read ``usage`` directly.
            "usage": {},
        }
        with self._calls_lock:
            record["call_index"] = len(self.calls)
            self.calls.append(record)
        self._call_state.last_call_index = record["call_index"]
        return int(record["call_index"]), record

    @staticmethod
    def _retryable_call(record: Mapping[str, Any]) -> bool:
        return (
            record.get("status") == "http_error"
            and record.get("http_status") in _RETRYABLE_HTTP_STATUSES
        )

    @staticmethod
    def _retry_pause() -> None:
        time.sleep(_RETRY_BACKOFF_S)

    def _post_once(
        self,
        *,
        stage: str,
        body: Mapping[str, Any],
        retry_of_call_index: int | None,
        retry_index: int,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            self.config.endpoint_url,
            data=json.dumps(dict(body), ensure_ascii=True).encode("utf-8"),
            headers={
                self.config.auth_header: self.config.auth_prefix + self.config.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        _call_index, record = self._new_call_record(
            stage=stage,
            retry_of_call_index=retry_of_call_index,
            retry_index=retry_index,
        )
        started_monotonic = time.monotonic()
        payload: dict[str, Any] | None = None
        response_headers: Any = None
        try:
            with _model_call_deadline(self.config.timeout_s):
                with urllib.request.urlopen(
                    request,
                    timeout=self.config.timeout_s,
                    context=ssl.create_default_context(),
                ) as response:
                    record["http_status"] = self._http_status(response)
                    response_headers = getattr(response, "headers", None)
                    decoded = json.loads(response.read().decode("utf-8"))
                    if not isinstance(decoded, dict):
                        raise ModelInvocationError(
                            "model API response must be a JSON object"
                        )
                    payload = decoded
                    record["status"] = "success"
        except _ModelCallDeadline as exc:
            record["status"] = "timeout"
            record["error"] = {
                "type": "timeout",
                "message": "total wall deadline exceeded",
            }
            raise ModelInvocationError(
                f"{stage} model call exceeded {self.config.timeout_s:g}s total wall "
                f"deadline for {self.config.model}"
            ) from exc
        except urllib.error.HTTPError as exc:
            record["status"] = "http_error"
            record["http_status"] = int(exc.code)
            response_headers = exc.headers
            record["error"] = {
                "type": "http_error",
                "message": f"HTTP {exc.code}",
            }
            raise ModelInvocationError(
                f"{stage} model call returned HTTP {exc.code} for {self.config.model}"
            ) from exc
        except urllib.error.URLError as exc:
            timed_out = isinstance(exc.reason, TimeoutError)
            record["status"] = "timeout" if timed_out else "transport_error"
            record["error"] = {
                "type": "timeout" if timed_out else "transport_error",
                "message": type(exc.reason).__name__,
            }
            raise ModelInvocationError(
                f"{stage} model call failed for {self.config.model}: {type(exc).__name__}"
            ) from exc
        except TimeoutError as exc:
            record["status"] = "timeout"
            record["error"] = {"type": "timeout", "message": type(exc).__name__}
            raise ModelInvocationError(
                f"{stage} model call failed for {self.config.model}: {type(exc).__name__}"
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            record["status"] = "response_error"
            record["error"] = {
                "type": "response_error",
                "message": type(exc).__name__,
            }
            raise ModelInvocationError(
                f"{stage} model call failed for {self.config.model}: {type(exc).__name__}"
            ) from exc
        except ModelInvocationError as exc:
            record["status"] = "response_error"
            record["error"] = {
                "type": "response_error",
                "message": "response was not a JSON object",
            }
            raise
        except OSError as exc:
            record["status"] = "transport_error"
            record["error"] = {
                "type": "transport_error",
                "message": type(exc).__name__,
            }
            raise ModelInvocationError(
                f"{stage} model call failed for {self.config.model}: {type(exc).__name__}"
            ) from exc
        finally:
            record["ended_at_utc"] = self._utc_now()
            record["elapsed_s"] = max(0.0, time.monotonic() - started_monotonic)
            record["provider_request_id"] = self._provider_request_id(
                payload, response_headers
            )
            if payload is not None:
                returned_model = payload.get("model")
                record["returned_model"] = (
                    returned_model
                    if isinstance(returned_model, str) and returned_model
                    else None
                )
                finish_reason, tool_names = self._response_semantics(payload)
                record["finish_reason"] = finish_reason
                record["tool_names"] = tool_names
                usage = payload.get("usage")
                raw_usage = dict(usage) if isinstance(usage, Mapping) else None
                record["raw_usage"] = raw_usage
                record["usage"] = raw_usage or {}
                record.update(self._normalised_usage(usage))
        if payload is None:  # All failure paths above raise before reaching this guard.
            raise ModelInvocationError("model API response is unavailable")
        return payload

    def _post(self, *, stage: str, body: Mapping[str, Any]) -> dict[str, Any]:
        first_call_index: int | None = None
        for retry_index in range(_MAX_PHYSICAL_REQUESTS):
            try:
                return self._post_once(
                    stage=stage,
                    body=body,
                    retry_of_call_index=(
                        first_call_index if retry_index > 0 else None
                    ),
                    retry_index=retry_index,
                )
            except ModelInvocationError:
                call_index = getattr(self._call_state, "last_call_index", None)
                if not isinstance(call_index, int):
                    raise
                with self._calls_lock:
                    if not 0 <= call_index < len(self.calls):
                        raise
                    record = dict(self.calls[call_index])
                if first_call_index is None:
                    first_call_index = call_index
                if (
                    retry_index + 1 >= _MAX_PHYSICAL_REQUESTS
                    or not self._retryable_call(record)
                ):
                    raise
                self._retry_pause()
        raise ModelInvocationError("model API retry budget exhausted")

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
        context_projection: Mapping[str, Any] | None = None,
    ) -> None:
        call_index = getattr(self._call_state, "last_call_index", None)
        record: dict[str, Any] | None = None
        if isinstance(call_index, int):
            with self._calls_lock:
                if 0 <= call_index < len(self.calls):
                    candidate = self.calls[call_index]
                    if candidate.get("stage") == stage:
                        record = candidate
        if record is None:
            # Keep compatibility with tests or adapters that replace the private HTTP
            # method. Real HTTP calls are always recorded by ``_post`` above.
            _call_index, record = self._new_call_record(stage=stage)
            record["status"] = "success"
            record["ended_at_utc"] = self._utc_now()
            record["elapsed_s"] = 0.0
            usage = payload.get("usage")
            raw_usage = dict(usage) if isinstance(usage, Mapping) else None
            record["raw_usage"] = raw_usage
            record["usage"] = raw_usage or {}
            record.update(self._normalised_usage(usage))
        returned_model = payload.get("model")
        record.update(
            {
                "mode": mode,
                "returned_model": (
                    returned_model
                    if isinstance(returned_model, str) and returned_model
                    else None
                ),
                "finish_reason": finish_reason,
                "tool_names": list(tool_names),
                "context_projection": dict(context_projection or {}),
            }
        )

    def _add_thinking_control(self, body: dict[str, Any]) -> None:
        thinking = self.config.thinking
        if thinking and (thinking != "disabled" or self.config.provider == "deepseek"):
            body["thinking"] = {"type": thinking}

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
        self._add_thinking_control(body)
        self._call_state.last_call_index = None
        self._call_state.mode = "json"
        self._call_state.context_projection = {}
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
            self._record_call(
                stage=stage,
                payload=payload,
                mode="json",
                finish_reason=finish_reason,
            )
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

    def generate_message_json(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Return one JSON object while preserving supplied chat-message roles."""

        if not isinstance(system_prompt, str) or not system_prompt:
            raise ValueError("system_prompt must be a non-empty string")
        history: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            if not isinstance(message, Mapping):
                raise ValueError(f"messages[{index}] must be an object")
            role = message.get("role")
            content = message.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str):
                raise ValueError(
                    f"messages[{index}] must contain a user/assistant role and text"
                )
            history.append(dict(message))

        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *history,
            ],
            "max_tokens": self.config.max_tokens,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        self._add_thinking_control(body)
        request_body = self._secret_free_exchange_copy(
            body,
            label="provider request body",
        )
        self._call_state.last_call_index = None
        self._call_state.mode = "json"
        self._call_state.context_projection = {}
        try:
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
                self._record_call(
                    stage=stage,
                    payload=payload,
                    mode="json",
                    finish_reason=finish_reason,
                )
                raise ModelInvocationError(
                    f"{exc}; finish_reason={finish_reason!r}; "
                    f"content_chars={len(content)}"
                ) from exc
            self._record_call(
                stage=stage,
                payload=payload,
                mode="json",
                finish_reason=choice.get("finish_reason"),
            )
            response_payload = self._secret_free_exchange_copy(
                payload,
                label="provider response payload",
            )
            response_message = self._secret_free_exchange_copy(
                message,
                label="provider response message",
            )
        except Exception as exc:
            self._record_message_json_exchange(
                stage=stage,
                request_body=request_body,
                response_payload=None,
                response_message=None,
                error=exc,
            )
            raise
        self._record_message_json_exchange(
            stage=stage,
            request_body=request_body,
            response_payload=response_payload,
            response_message=response_message,
            error=None,
        )
        return result

    @property
    def message_json_exchange_records(self) -> tuple[Mapping[str, Any], ...]:
        """Return deep-copied, secret-free raw JSON-message exchanges."""

        with self._message_json_exchanges_lock:
            return tuple(copy.deepcopy(self._message_json_exchanges))

    def _record_message_json_exchange(
        self,
        *,
        stage: str,
        request_body: Mapping[str, Any],
        response_payload: Mapping[str, Any] | None,
        response_message: Mapping[str, Any] | None,
        error: BaseException | None,
    ) -> None:
        message = None
        if error is not None:
            message = str(error)
            if self.config.api_key:
                message = message.replace(self.config.api_key, "<redacted>")
            message = message[:1000]
        record = {
            "exchange_index": -1,
            "stage": stage,
            "status": "success" if error is None else "error",
            "request_body": copy.deepcopy(dict(request_body)),
            "request_messages": copy.deepcopy(request_body.get("messages")),
            "response_payload": copy.deepcopy(response_payload),
            "response_message": copy.deepcopy(response_message),
            "error": (
                None
                if error is None
                else {"type": type(error).__name__, "message": message}
            ),
        }
        with self._message_json_exchanges_lock:
            record["exchange_index"] = len(self._message_json_exchanges)
            self._message_json_exchanges.append(record)

    def _secret_free_exchange_copy(self, value: Any, *, label: str) -> Any:
        copied = copy.deepcopy(value)
        if self.config.api_key and _contains_text(copied, self.config.api_key):
            raise ModelInvocationError(f"{label} contains the configured credential")
        return copied

    def generate_tool_turn(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> ToolTurn:
        """Return one assistant turn while preserving tool-call conversation state."""

        history_messages, context_projection = self._project_tool_history(messages)
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *history_messages,
            ],
            "max_tokens": self.config.max_tokens,
            "temperature": 0.0,
            "tools": [dict(tool) for tool in tools],
            "tool_choice": "auto",
        }
        self._add_thinking_control(body)
        self._call_state.last_call_index = None
        self._call_state.mode = "react"
        self._call_state.context_projection = context_projection
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
            context_projection=context_projection,
        )
        return ToolTurn(
            content=content,
            tool_calls=tuple(parsed_calls),
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            reasoning_content=reasoning_content,
        )
