"""Bedrock Converse-API client shim that mimics the AnthropicBedrock interface.

Lets ReactLoop drive non-Anthropic Bedrock models (Mistral/Ministral, Qwen,
DeepSeek, Amazon Nova, …) through the unified Converse API while presenting the
exact same ``client.messages.create(...) -> response`` shape the loop already
expects from ``anthropic.AnthropicBedrock``.

The loop reads:  resp.content (list of blocks with .type/.text/.name/.input/.id),
                 resp.usage.input_tokens / .output_tokens / .cache_read_input_tokens,
                 resp.stop_reason ("end_turn" | "tool_use" | ...)
and round-trips ``resp.content`` straight back into ``messages`` as the
assistant turn, plus tool_result dicts as the user turn. We accept both on the
way in and convert to Converse format.
"""
from __future__ import annotations
from typing import Any, Optional
import boto3


class _Block:
    """Anthropic-shaped content block (text or tool_use)."""
    __slots__ = ("type", "text", "name", "input", "id")

    def __init__(self, type: str, text=None, name=None, input=None, id=None):
        self.type = type
        self.text = text
        self.name = name
        self.input = input
        self.id = id


class _Usage:
    __slots__ = ("input_tokens", "output_tokens", "cache_read_input_tokens")

    def __init__(self, i: int, o: int):
        self.input_tokens = i
        self.output_tokens = o
        self.cache_read_input_tokens = 0


class _Resp:
    __slots__ = ("content", "usage", "stop_reason")

    def __init__(self, content, usage, stop_reason):
        self.content = content
        self.usage = usage
        self.stop_reason = stop_reason


_STOP_MAP = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop_sequence": "end_turn",
    "content_filtered": "end_turn",
    "guardrail_intervened": "end_turn",
}


class _Messages:
    def __init__(self, region: str):
        # read_timeout caps a hung response; connect_timeout caps a dead
        # connection; retries handle transient throttles. Without these a
        # silently-dropped Bedrock socket blocks the call forever.
        from botocore.config import Config  # noqa: PLC0415
        cfg = Config(
            read_timeout=300, connect_timeout=30,
            retries={"max_attempts": 4, "mode": "adaptive"},
        )
        self._rt = boto3.client("bedrock-runtime", region_name=region, config=cfg)

    def create(self, *, model, messages, max_tokens, system=None, tools=None,
               temperature=None, **_ignored) -> _Resp:
        tool_config = None
        if tools:
            tool_config = {
                "tools": [
                    {"toolSpec": {
                        "name": t["name"],
                        "description": t.get("description", t["name"]),
                        "inputSchema": {"json": t["input_schema"]},
                    }}
                    for t in tools
                ]
            }

        conv_msgs = [self._to_converse_msg(m) for m in messages]
        conv_msgs = [m for m in conv_msgs if m["content"]]  # drop empties

        kwargs: dict[str, Any] = {
            "modelId": model,
            "messages": conv_msgs,
            "inferenceConfig": {"maxTokens": int(max_tokens)},
        }
        if system:
            kwargs["system"] = [{"text": system}]
        if tool_config:
            kwargs["toolConfig"] = tool_config
        if temperature is not None:
            kwargs["inferenceConfig"]["temperature"] = float(temperature)

        r = self._rt.converse(**kwargs)
        out = r["output"]["message"]["content"]
        blocks: list[_Block] = []
        for c in out:
            if "text" in c:
                blocks.append(_Block("text", text=c["text"]))
            elif "toolUse" in c:
                tu = c["toolUse"]
                blocks.append(_Block("tool_use", name=tu["name"],
                                     input=tu.get("input", {}), id=tu["toolUseId"]))
        u = r.get("usage", {})
        stop = _STOP_MAP.get(r.get("stopReason", "end_turn"), "end_turn")
        return _Resp(blocks, _Usage(int(u.get("inputTokens", 0)),
                                    int(u.get("outputTokens", 0))), stop)

    @staticmethod
    def _to_converse_msg(m: dict) -> dict:
        role = m["role"]
        content = m["content"]
        blocks: list[dict] = []
        if isinstance(content, str):
            if content.strip():
                blocks.append({"text": content})
            return {"role": role, "content": blocks}
        for b in content:
            if isinstance(b, _Block):
                if b.type == "text" and b.text and b.text.strip():
                    blocks.append({"text": b.text})
                elif b.type == "tool_use":
                    blocks.append({"toolUse": {
                        "toolUseId": b.id, "name": b.name, "input": b.input or {}}})
            elif isinstance(b, dict):
                if b.get("type") == "tool_result":
                    tr = {"toolResult": {
                        "toolUseId": b["tool_use_id"],
                        "content": [{"text": str(b["content"])}],
                    }}
                    if b.get("is_error"):
                        tr["toolResult"]["status"] = "error"
                    blocks.append(tr)
                elif "text" in b and str(b["text"]).strip():
                    blocks.append({"text": b["text"]})
        return {"role": role, "content": blocks}


class ConverseClient:
    """Drop-in for AnthropicBedrock exposing ``.messages.create(...)``."""

    def __init__(self, region: str = "us-east-1"):
        self.messages = _Messages(region)


def is_anthropic_model(model_id: str) -> bool:
    m = model_id.lower()
    return "anthropic" in m or "claude" in m
