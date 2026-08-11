"""Small HTTP adapter for the experiment's stage and ReAct model calls."""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from ..foundation.errors import ContractError


DEFAULT_BASE_URL = "https://q7s6v6seerne7eyh5ttsovjjcu0hxbou.lambda-url.eu-west-2.on.aws"
DEFAULT_MODEL = "anthropic.claude-sonnet-4-5-20250929-v1:0"


@dataclass(frozen=True)
class ModelApiConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    temperature: float = 0.0
    timeout_s: float = 125.0

    @classmethod
    def from_environment(cls) -> "ModelApiConfig":
        api_key = os.environ.get("AUTOADAPTER_MODEL_API_KEY", "").strip()
        if not api_key:
            raise ContractError("AUTOADAPTER_MODEL_API_KEY is required")
        return cls(
            api_key=api_key,
            base_url=os.environ.get("AUTOADAPTER_MODEL_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            model=os.environ.get("AUTOADAPTER_MODEL_ID", DEFAULT_MODEL),
        )


class ModelApiClient:
    """JSON-only provider used by Stage 1, Blue Line, Stage 2, Repair and ReAct."""

    def __init__(self, config: ModelApiConfig):
        if not isinstance(config, ModelApiConfig):
            raise ContractError("ModelApiClient requires ModelApiConfig")
        self.config = config
        self.calls: list[dict[str, Any]] = []

    def _complete_json(self, *, stage: str, instruction: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
        request_body = {
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
                    "content": instruction + "\n\nINPUT_JSON:\n" + json.dumps(
                        dict(inputs), sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ),
                },
            ],
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + "/v1/chat/completions",
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "X-Api-Key": self.config.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        ca_file = os.environ.get("SSL_CERT_FILE", "/etc/ssl/cert.pem")
        ssl_context = ssl.create_default_context(cafile=ca_file)
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_s, context=ssl_context
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ContractError(f"model API call failed for {stage}") from exc
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ContractError("model API response lacks choices")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        text = content.strip() if isinstance(content, str) else ""
        if not text:
            raise ContractError("model API response lacks message content")
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                text = "\n".join(lines[1:-1]).strip()
                if text.startswith("json\n"):
                    text = text[5:]
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ContractError(f"model API returned non-JSON output for {stage}") from exc
        if not isinstance(result, dict):
            raise ContractError("model API must return one JSON object")
        self.calls.append(
            {
                "stage": stage,
                "model": self.config.model,
                "usage": payload.get("usage"),
                "metadata": payload.get("metadata"),
            }
        )
        return result

    def generate_json(self, stage: str, prompt: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
        return self._complete_json(stage=stage, instruction=prompt, inputs=inputs)

    def repair(self, request: Mapping[str, Any]) -> dict[str, Any]:
        result = self._complete_json(
            stage="repair",
            instruction=(
                "Repair only capability.py using the supplied public diagnostics and binding. "
                "Return exactly {\"capability.py\": <complete source>, \"llm_calls\": 1}."
            ),
            inputs=request,
        )
        result["llm_calls"] = 1
        return result

    def react(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._complete_json(
            stage="react_consumer",
            instruction=(
                "Choose one visible capability action, or finish. Return either "
                "{\"thought\": <short text>, \"action\": {\"capability_id\": <id>, "
                "\"arguments\": <object>}} or {\"thought\": <short text>, \"final\": <JSON value>}."
            ),
            inputs=request,
        )
