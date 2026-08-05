"""Model-client abstraction and the user-provided AWS Model API backend."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Protocol, Sequence

from .audit import BudgetCounter


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    remaining_budget: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelUsageSummary:
    """Additive usage plus quota gauges, without provider payloads."""

    response_count: int
    input_tokens_total: int | None
    output_tokens_total: int | None
    cost_total: float | None
    remaining_budget_latest: float | None
    remaining_budget_minimum: float | None
    input_tokens_reported: int
    output_tokens_reported: int
    cost_reported: int
    remaining_budget_reported: int

    def to_dict(self) -> dict[str, int | float | None]:
        return {
            "response_count": self.response_count,
            "input_tokens_total": self.input_tokens_total,
            "output_tokens_total": self.output_tokens_total,
            "cost_total": self.cost_total,
            "remaining_budget_latest": self.remaining_budget_latest,
            "remaining_budget_minimum": self.remaining_budget_minimum,
            "input_tokens_reported": self.input_tokens_reported,
            "output_tokens_reported": self.output_tokens_reported,
            "cost_reported": self.cost_reported,
            "remaining_budget_reported": self.remaining_budget_reported,
        }


def summarize_model_usage(usages: Sequence[ModelUsage]) -> ModelUsageSummary:
    """Aggregate normalized response usage; quota is a gauge, not a sum."""

    input_values = [item.input_tokens for item in usages if item.input_tokens is not None]
    output_values = [item.output_tokens for item in usages if item.output_tokens is not None]
    cost_values = [item.cost for item in usages if item.cost is not None]
    remaining_values = [
        item.remaining_budget for item in usages if item.remaining_budget is not None
    ]
    return ModelUsageSummary(
        response_count=len(usages),
        input_tokens_total=sum(input_values) if input_values else None,
        output_tokens_total=sum(output_values) if output_values else None,
        cost_total=sum(cost_values) if cost_values else None,
        remaining_budget_latest=remaining_values[-1] if remaining_values else None,
        remaining_budget_minimum=min(remaining_values) if remaining_values else None,
        input_tokens_reported=len(input_values),
        output_tokens_reported=len(output_values),
        cost_reported=len(cost_values),
        remaining_budget_reported=len(remaining_values),
    )


@dataclass(frozen=True)
class ModelResponse:
    text: str
    usage: ModelUsage = field(default_factory=ModelUsage)
    raw: dict[str, Any] = field(default_factory=dict)
    client_kind: str = "unknown"
    request_id: str | None = None
    provider_http_attempts: int = 0
    provider_retries: int = 0
    stop_reason: str | None = None
    output_truncated: bool | None = None
    requested_max_tokens: int | None = None


class ModelClient(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def client_kind(self) -> str: ...

    @property
    def scripted(self) -> bool: ...

    def audit_snapshot(self) -> dict[str, Any]: ...

    def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ModelResponse: ...


@dataclass(frozen=True)
class AWSModelAPIConfig:
    model: str = "anthropic.claude-sonnet-4-5-20250929-v1:0"
    api_key_env: str = "AWS_MODEL_API_KEY"
    route: str = "long"
    short_endpoint: str = (
        "https://i5xpracyci.execute-api.eu-west-2.amazonaws.com/model-api/invoke"
    )
    long_base_url: str = (
        "https://q7s6v6seerne7eyh5ttsovjjcu0hxbou.lambda-url.eu-west-2.on.aws"
    )
    long_path: str = "/v1/chat/completions"
    max_tokens: int = 8192
    temperature: float = 0.2
    short_timeout_s: float = 30.0
    long_timeout_s: float = 120.0
    max_retries: int = 2

    @property
    def endpoint(self) -> str:
        if self.route == "short":
            return os.getenv("AWS_MODEL_API_SHORT_ENDPOINT", self.short_endpoint)
        if self.route != "long":
            raise ValueError(f"unknown AWS Model API route {self.route!r}")
        base = os.getenv("AWS_MODEL_API_LONG_BASE_URL", self.long_base_url).rstrip("/")
        path = os.getenv("AWS_MODEL_API_LONG_PATH", self.long_path)
        return base + "/" + path.lstrip("/")

    @property
    def timeout_s(self) -> float:
        return self.short_timeout_s if self.route == "short" else self.long_timeout_s


class ModelAPIError(RuntimeError):
    """Sanitized model-client failure with an explicit retryability class.

    ``retryable`` never grants an extra provider attempt.  It only tells the
    caller whether it is reasonable to spend another already-declared Agent
    turn after this request has been counted and audited.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        failure_kind: str = "client_error",
    ) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)
        self.failure_kind = str(failure_kind)


class AWSModelAPIClient:
    """POST messages to AWS with sanitized, per-HTTP-attempt accounting."""

    def __init__(self, config: AWSModelAPIConfig, *, api_key: str | None = None):
        if config.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.config = config
        self._api_key = api_key
        self._lock = Lock()
        self._logical_requests = 0
        self._successful_responses = 0
        self._failed_requests = 0
        self._provider_http_attempts = 0
        self._provider_retries = 0
        self._usages: list[ModelUsage] = []
        # These records intentionally exclude messages, payload bytes, headers,
        # response bodies, normalized text, endpoint URLs, and API keys.
        self._request_records: list[dict[str, Any]] = []

    @property
    def model(self) -> str:
        return self.config.model

    @property
    def client_kind(self) -> str:
        return "aws_model_api"

    @property
    def scripted(self) -> bool:
        return False

    def _get_key(self) -> str:
        key = self._api_key or os.getenv(self.config.api_key_env)
        if not key:
            raise ModelAPIError(
                f"missing AWS Model API key; set {self.config.api_key_env} in the environment"
            )
        return key

    def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ModelResponse:
        with self._lock:
            self._logical_requests += 1
            request_number = self._logical_requests
        request_id = f"aws-request-{request_number:06d}"
        attempt_budget = BudgetCounter(
            f"{request_id}:provider_http_attempts",
            self.config.max_retries + 1,
        )
        attempts: list[dict[str, Any]] = []
        try:
            api_key = self._get_key()
            endpoint = self.config.endpoint
            payload = {
                "model": self.config.model,
                "messages": [message.to_dict() for message in messages],
                "max_tokens": (
                    max_tokens if max_tokens is not None else self.config.max_tokens
                ),
                "temperature": (
                    temperature if temperature is not None else self.config.temperature
                ),
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except Exception as exc:
            self._record_request(
                {
                    "request_id": request_id,
                    "outcome": "preflight_failed",
                    "error_type": type(exc).__name__,
                    "attempt_budget": attempt_budget.to_dict(),
                    "provider_http_attempts": 0,
                    "provider_retries": 0,
                    "attempts": [],
                },
                success=False,
            )
            raise
        last_error: BaseException | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                request = urllib.request.Request(
                    endpoint,
                    data=body,
                    method="POST",
                    headers={
                        "X-Api-Key": api_key,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
            except Exception as exc:
                # Request construction is a preflight failure, not an HTTP
                # attempt, so it consumes neither attempt nor retry budget.
                last_error = exc
                break
            remaining_attempts = attempt_budget.consume()
            is_retry = attempt > 0
            with self._lock:
                self._provider_http_attempts += 1
                if is_retry:
                    self._provider_retries += 1
            attempt_record: dict[str, Any] = {
                "attempt": attempt + 1,
                "is_retry": is_retry,
                "remaining_attempt_budget": remaining_attempts,
            }
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_s) as response:
                    encoded_result = response.read()
                    status = getattr(response, "status", None)
                result = json.loads(encoded_result.decode("utf-8"))
                if not isinstance(result, dict):
                    raise ModelAPIError("AWS Model API response must be a JSON object")
                normalized = self._normalize(result)
                requested_max_tokens = int(payload["max_tokens"])
                output_truncated = _output_was_truncated(
                    stop_reason=normalized.stop_reason,
                    output_tokens=normalized.usage.output_tokens,
                    requested_max_tokens=requested_max_tokens,
                )
                attempt_record["outcome"] = "succeeded"
                if isinstance(status, int):
                    attempt_record["status_code"] = status
                attempts.append(attempt_record)
                record = {
                    "request_id": request_id,
                    "outcome": "succeeded",
                    "attempt_budget": attempt_budget.to_dict(),
                    "provider_http_attempts": len(attempts),
                    "provider_retries": sum(bool(item["is_retry"]) for item in attempts),
                    "attempts": attempts,
                    "usage": _safe_usage_dict(normalized.usage),
                    "completion": {
                        "stop_reason": normalized.stop_reason,
                        "output_truncated": output_truncated,
                        "requested_max_tokens": requested_max_tokens,
                    },
                }
                self._record_request(record, success=True, usage=normalized.usage)
                return ModelResponse(
                    text=normalized.text,
                    usage=normalized.usage,
                    raw=normalized.raw,
                    client_kind=self.client_kind,
                    request_id=request_id,
                    provider_http_attempts=len(attempts),
                    provider_retries=record["provider_retries"],
                    stop_reason=normalized.stop_reason,
                    output_truncated=output_truncated,
                    requested_max_tokens=requested_max_tokens,
                )
            except urllib.error.HTTPError as exc:
                # Consume and discard the body. It can contain provider-generated
                # text and must never enter an audit record or exception string.
                try:
                    exc.read()
                except Exception:
                    pass
                last_error = ModelAPIError(f"AWS Model API HTTP {exc.code}")
                attempt_record.update(
                    {
                        "outcome": "http_error",
                        "status_code": exc.code,
                        "error_type": type(exc).__name__,
                    }
                )
                attempts.append(attempt_record)
                if exc.code < 500 and exc.code != 429:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                attempt_record.update(
                    {
                        "outcome": "transport_or_decode_error",
                        "error_type": type(exc).__name__,
                    }
                )
                attempts.append(attempt_record)
            except ModelAPIError as exc:
                last_error = exc
                attempt_record.update(
                    {
                        "outcome": "invalid_provider_response",
                        "error_type": type(exc).__name__,
                    }
                )
                attempts.append(attempt_record)
            except Exception as exc:
                last_error = exc
                attempt_record.update(
                    {
                        "outcome": "unexpected_client_error",
                        "error_type": type(exc).__name__,
                    }
                )
                attempts.append(attempt_record)
                break
            if attempt < self.config.max_retries:
                time.sleep(min(2**attempt, 4))
        last_attempt = attempts[-1] if attempts else {}
        last_outcome = str(last_attempt.get("outcome", "preflight_failed"))
        status_code = last_attempt.get("status_code")
        retryable = last_outcome == "transport_or_decode_error" or (
            last_outcome == "http_error"
            and isinstance(status_code, int)
            and (status_code == 429 or status_code >= 500)
        )
        record = {
            "request_id": request_id,
            "outcome": "failed",
            "error_type": type(last_error).__name__ if last_error is not None else "UnknownError",
            "failure_kind": last_outcome,
            "retryable": retryable,
            "attempt_budget": attempt_budget.to_dict(),
            "provider_http_attempts": len(attempts),
            "provider_retries": sum(bool(item["is_retry"]) for item in attempts),
            "attempts": attempts,
        }
        self._record_request(record, success=False)
        # Only sanitized type/count metadata is exposed. In particular, no raw
        # HTTP body, request payload, header, or transport exception string.
        raise ModelAPIError(
            "AWS Model API request failed after "
            f"{len(attempts)} HTTP attempt(s); last_error_type={record['error_type']}",
            retryable=retryable,
            failure_kind=last_outcome,
        ) from None

    def _record_request(
        self,
        record: dict[str, Any],
        *,
        success: bool,
        usage: ModelUsage | None = None,
    ) -> None:
        with self._lock:
            if success:
                self._successful_responses += 1
            else:
                self._failed_requests += 1
            if usage is not None:
                self._usages.append(usage)
            self._request_records.append(deepcopy(record))

    def audit_snapshot(self) -> dict[str, Any]:
        """Return sanitized counters and records safe for run reports."""

        with self._lock:
            usages = tuple(self._usages)
            records = deepcopy(self._request_records)
            logical_requests = self._logical_requests
            successful_responses = self._successful_responses
            failed_requests = self._failed_requests
            provider_http_attempts = self._provider_http_attempts
            provider_retries = self._provider_retries
        return {
            "schema_version": "robot_capability.model_client_audit.v1",
            "client_kind": self.client_kind,
            "scripted": self.scripted,
            "model": self.model,
            "transport": "provider_http",
            "logical_requests": logical_requests,
            "successful_responses": successful_responses,
            "failed_requests": failed_requests,
            "provider_http_attempts": provider_http_attempts,
            "provider_retries": provider_retries,
            "attempt_budget_policy": {
                "scope": "per_logical_request",
                "limit_per_request": self.config.max_retries + 1,
                "max_retries_per_request": self.config.max_retries,
            },
            "usage": summarize_model_usage(usages).to_dict(),
            "requests": records,
        }

    @staticmethod
    def _normalize(result: dict[str, Any]) -> ModelResponse:
        blocks = result.get("content", [])
        texts: list[str] = []
        if isinstance(blocks, str):
            texts.append(blocks)
        elif isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, str):
                    texts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    texts.append(str(block.get("text", "")))
        # Compatibility with OpenAI-shaped proxies if the long route returns choices.
        if not texts and isinstance(result.get("choices"), list):
            for choice in result["choices"]:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message", {})
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, str):
                    texts.append(content)
        usage_raw = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        remaining = metadata.get("remaining_quota")
        if not isinstance(remaining, dict):
            remaining = {}
        usage = ModelUsage(
            input_tokens=_coerce_int(
                usage_raw.get(
                    "input_tokens",
                    usage_raw.get("inputTokens", usage_raw.get("prompt_tokens")),
                )
            ),
            output_tokens=_coerce_int(
                usage_raw.get(
                    "output_tokens",
                    usage_raw.get("outputTokens", usage_raw.get("completion_tokens")),
                )
            ),
            cost=_coerce_float(usage_raw.get("cost")),
            remaining_budget=_coerce_float(remaining.get("remaining_budget")),
            raw=dict(usage_raw),
        )
        if not texts:
            raise ModelAPIError("AWS Model API response contained no text content block")
        return ModelResponse(
            text="\n".join(texts),
            usage=usage,
            raw=result,
            stop_reason=_extract_stop_reason(result),
        )


class ScriptedModelClient:
    """Deterministic fixture client; it never represents a provider call."""

    def __init__(self, responses: Sequence[str], *, model: str = "scripted-offline"):
        self._responses = list(responses)
        self._index = 0
        self._model = model
        self._failed_requests = 0
        self._usages: list[ModelUsage] = []
        self._request_records: list[dict[str, Any]] = []
        self.requests: list[list[ModelMessage]] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def client_kind(self) -> str:
        return "scripted_fixture"

    @property
    def scripted(self) -> bool:
        return True

    def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ModelResponse:
        del max_tokens, temperature
        self.requests.append(list(messages))
        request_id = f"scripted-request-{len(self.requests):06d}"
        if self._index >= len(self._responses):
            self._failed_requests += 1
            self._request_records.append(
                {
                    "request_id": request_id,
                    "outcome": "script_queue_exhausted",
                    "provider_http_attempts": 0,
                    "provider_retries": 0,
                }
            )
            raise ModelAPIError("scripted model response queue exhausted")
        text = self._responses[self._index]
        self._index += 1
        usage = ModelUsage(input_tokens=0, output_tokens=0, cost=0.0)
        self._usages.append(usage)
        self._request_records.append(
            {
                "request_id": request_id,
                "outcome": "scripted_response",
                "provider_http_attempts": 0,
                "provider_retries": 0,
                "usage": _safe_usage_dict(usage),
            }
        )
        return ModelResponse(
            text=text,
            usage=usage,
            client_kind=self.client_kind,
            request_id=request_id,
            provider_http_attempts=0,
            provider_retries=0,
            stop_reason="scripted_fixture",
            output_truncated=False,
        )

    def audit_snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "robot_capability.model_client_audit.v1",
            "client_kind": self.client_kind,
            "scripted": self.scripted,
            "model": self.model,
            "transport": "none",
            "logical_requests": len(self.requests),
            "successful_responses": len(self._usages),
            "failed_requests": self._failed_requests,
            "provider_http_attempts": 0,
            "provider_retries": 0,
            "attempt_budget_policy": {
                "scope": "not_applicable",
                "limit_per_request": 0,
                "max_retries_per_request": 0,
            },
            "usage": summarize_model_usage(tuple(self._usages)).to_dict(),
            # Deliberately omit self.requests and response text: those are only
            # an in-memory test aid, not report-safe audit data.
            "requests": deepcopy(self._request_records),
        }


def _safe_usage_dict(usage: ModelUsage) -> dict[str, int | float | None]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cost": usage.cost,
        "remaining_budget": usage.remaining_budget,
    }


def _extract_stop_reason(result: dict[str, Any]) -> str | None:
    """Normalize Anthropic/OpenAI proxy completion reasons without content."""

    for key in ("stop_reason", "stopReason", "finish_reason", "finishReason"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    choices = result.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            value = choice.get("finish_reason", choice.get("finishReason"))
            if isinstance(value, str) and value:
                return value
    return None


def _output_was_truncated(
    *,
    stop_reason: str | None,
    output_tokens: int | None,
    requested_max_tokens: int,
) -> bool:
    normalized = "" if stop_reason is None else stop_reason.strip().lower()
    if normalized in {"max_tokens", "max_token", "length", "token_limit"}:
        return True
    return output_tokens is not None and output_tokens >= requested_max_tokens


def _coerce_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
