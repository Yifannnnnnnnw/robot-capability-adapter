"""Holistic OpenAI-compatible transport for AA1's existing ReAct messages.

Uses the project's existing company gateway and credential file. Credentials
stay in the parent transport and are never included in trace payloads.
"""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ENDPOINT = "https://q7s6v6seerne7eyh5ttsovjjcu0hxbou.lambda-url.eu-west-2.on.aws/v1/chat/completions"


def _field(block, key, default=None):
    return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)


def _content(blocks):
    if isinstance(blocks, str):
        return blocks
    result = []
    for block in blocks or []:
        kind = _field(block, "type")
        if kind == "text":
            result.append({"type": "text", "text": _field(block, "text", "")})
        elif kind == "image":
            source = _field(block, "source", {})
            result.append({"type": "image_url", "image_url": {
                "url": f"data:{source['media_type']};base64,{source['data']}"}})
    return result


def to_openai_messages(messages, system):
    result = [{"role": "system", "content": system}] if system else []
    for message in messages:
        role, blocks = message["role"], message["content"]
        if isinstance(blocks, str):
            result.append({"role": role, "content": blocks})
            continue
        calls = [b for b in blocks if _field(b, "type") == "tool_use"]
        outputs = [b for b in blocks if _field(b, "type") == "tool_result"]
        content = _content(blocks)
        if role == "assistant":
            item = {"role": role, "content": content or None}
            if calls:
                item["tool_calls"] = [{
                    "id": _field(b, "id"), "type": "function",
                    "function": {"name": _field(b, "name"),
                                 "arguments": json.dumps(_field(b, "input"))},
                } for b in calls]
            result.append(item)
        else:
            for block in outputs:
                value = _field(block, "content", "")
                result.append({"role": "tool", "tool_call_id": _field(block, "tool_use_id"),
                               "content": value if isinstance(value, str) else json.dumps(value)})
            if content:
                result.append({"role": role, "content": content})
    return result


class HolisticClient:
    """Expose messages.create without changing AA1 tool-loop semantics."""

    def __init__(self):
        environment = dict(os.environ)
        path = Path(environment.get("AA1_HOLISTIC_ENV_FILE", Path(__file__).resolve().parents[3] / ".env.company-api"))
        if path.is_file():
            for line in path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.removeprefix("export ").split("=", 1)
                    parts = shlex.split(value, comments=True)
                    if len(parts) == 1:
                        environment.setdefault(key.strip(), parts[0])
        self._key = environment.get("AUTOADAPTER_HOLISTICAI_API_KEY") or environment.get("AUTOADAPTER_COMPANY_API_KEY")
        if not self._key:
            raise ValueError("missing Holistic API credential (AUTOADAPTER_HOLISTICAI_API_KEY)")
        self.messages = self

    def create(self, *, model, messages, max_tokens, system=None, tools=None,
               temperature=None, **ignored):
        body = {"model": model, "messages": to_openai_messages(messages, system),
                "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t["description"],
                "parameters": t["input_schema"],
            }} for t in tools]
            body["tool_choice"] = "auto"
        request = Request(ENDPOINT, data=json.dumps(body).encode(),
                          headers={"X-Api-Key": self._key, "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=120.0) as response:
                payload = json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace").replace(self._key, "<redacted>")[:2000]
            raise RuntimeError(f"Holistic HTTP {exc.code}: {detail}") from None
        choice = payload["choices"][0]
        message = choice["message"]
        blocks = []
        if message.get("content"):
            blocks.append(SimpleNamespace(type="text", text=message["content"]))
        for call in message.get("tool_calls") or []:
            fn = call["function"]
            blocks.append(SimpleNamespace(type="tool_use", id=call["id"], name=fn["name"],
                                          input=json.loads(fn["arguments"])))
        usage = payload.get("usage", {})
        finish = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}.get(choice.get("finish_reason"), choice.get("finish_reason"))
        return SimpleNamespace(content=blocks, stop_reason=finish,
                               usage=SimpleNamespace(input_tokens=usage.get("prompt_tokens", 0),
                                                     output_tokens=usage.get("completion_tokens", 0),
                                                     cache_read_input_tokens=0))
