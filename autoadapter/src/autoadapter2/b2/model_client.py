"""Parent-side real-model adapter for the fixed ReCAP JSON interface.

The existing :class:`JsonModelClient` supports JSON-object response mode, not
provider-side JSON-schema enforcement.  This adapter therefore forwards the
fixed ReCAP schema unchanged as model input; ``run_recap`` remains the strict
schema-validation boundary for the returned object.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from autoadapter2.model_api import JsonModelClient, ModelConfig


_SCHEMA_INSTRUCTION = (
    "Return exactly one JSON object that conforms to this fixed response schema. "
    "Do not add Markdown or fields outside the schema.\n\n"
    "FIXED_RESPONSE_SCHEMA_JSON:\n"
)


@dataclass(frozen=True)
class B2ModelProviderConfig:
    """Credential-free provider settings supplied by the B2 caller."""

    provider: str
    model: str
    base_url: str
    api_protocol: str = "openai-compatible"
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    thinking: str | None = None
    timeout_s: float = 180.0
    max_tokens: int = 16000
    history_char_budget: int = 80000
    endpoint_path: str = "/chat/completions"


class ReCAPJsonModelClient:
    """Implement ``RecapModelClient`` with one explicit parent-side credential."""

    def __init__(
        self,
        *,
        provider_config: B2ModelProviderConfig,
        credential: str,
    ) -> None:
        if not isinstance(provider_config, B2ModelProviderConfig):
            raise TypeError("provider_config must be B2ModelProviderConfig")
        if not isinstance(credential, str) or not credential:
            raise ValueError("credential must be a non-empty string")
        self._client = JsonModelClient(
            ModelConfig(
                provider=provider_config.provider,
                model=provider_config.model,
                base_url=provider_config.base_url,
                api_key=credential,
                endpoint_path=provider_config.endpoint_path,
                api_protocol=provider_config.api_protocol,
                auth_header=provider_config.auth_header,
                auth_prefix=provider_config.auth_prefix,
                thinking=provider_config.thinking,
                timeout_s=provider_config.timeout_s,
                max_tokens=provider_config.max_tokens,
                tool_history_mode="native",
                history_char_budget=provider_config.history_char_budget,
            )
        )

    @property
    def provider_call_records(self) -> tuple[Mapping[str, Any], ...]:
        """Return secret-free parent-side records retained by JsonModelClient."""

        return tuple(dict(record) for record in self._client.calls)

    @property
    def provider_exchange_records(self) -> tuple[Mapping[str, Any], ...]:
        """Return deep-copied raw request/response exchanges retained in parent."""

        return self._client.message_json_exchange_records

    def generate_recap_json(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        response_schema: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Forward one fixed ReCAP turn through ``generate_json`` unchanged."""

        if not isinstance(stage, str) or not stage:
            raise ValueError("stage must be a non-empty string")
        if not isinstance(system_prompt, str) or not system_prompt:
            raise ValueError("system_prompt must be a non-empty string")
        history = _finite_json_array(messages, label="ReCAP messages")
        schema = _finite_json_object(response_schema, label="ReCAP response schema")
        schema_system_prompt = (
            system_prompt
            + "\n\n"
            + _SCHEMA_INSTRUCTION
            + json.dumps(
                schema,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return self._client.generate_message_json(
            stage=stage,
            system_prompt=schema_system_prompt,
            messages=history,
        )


def _finite_json_array(value: Any, *, label: str) -> list[Any]:
    copied = _finite_json(value, label=label)
    if not isinstance(copied, list):
        raise ValueError(f"{label} must be an array")
    return copied


def _finite_json_object(value: Any, *, label: str) -> dict[str, Any]:
    copied = _finite_json(value, label=label)
    if not isinstance(copied, dict):
        raise ValueError(f"{label} must be an object")
    return copied


def _finite_json(value: Any, *, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite JSON") from exc


__all__ = ["B2ModelProviderConfig", "ReCAPJsonModelClient"]
