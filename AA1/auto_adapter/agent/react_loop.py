# SPDX-License-Identifier: Apache-2.0
"""Minimal traceable ReAct loop on top of AWS Bedrock tool_use.

Implements DESIGN.md §0.9. Each LLM turn becomes one TraceStep recording
the Thought (Claude's text content), Actions (tool_use blocks), and
Observations (tool handler outputs). Trace dumps live-jsonl so partial
runs survive crashes and Phase-3 retry can replay context.

Constraint: this is *framework* code (Layer 1 in §0.7). The LLM agent
does NOT generate it; agents only use it (and receive ToolSpec list +
system prompts from the orchestrator).
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


def _vlog(msg: str) -> None:
    """Verbose trace to stderr (gated by AA_VERBOSE)."""
    if os.environ.get("AA_VERBOSE"):
        print(f"[{time.strftime('%H:%M:%S')}] [react] {msg}", file=sys.stderr,
              flush=True)


# ──────────────────────────────────────────────────────────────────────────
# Public dataclasses
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class ToolSpec:
    """One tool exposed to the LLM. Handler must be synchronous + side-effecting.

    handler(input_dict) -> output (anything JSON-serializable). Raises on error;
    ReactLoop captures the exception and turns it into an `is_error=True` tool
    result so Claude can decide to retry / adapt.
    """

    name: str
    description: str
    input_schema: dict  # JSON Schema (Anthropic tool_use format)
    handler: Callable[[dict], Any]


@dataclass
class TraceStep:
    """One turn of the ReAct loop. Persisted as one line in `trace.jsonl`."""

    iter: int
    thought: str
    actions: list[dict]  # [{name, input}]
    observations: list[dict]  # [{tool_use_id, content (truncated), is_error}]
    duration_ms: float
    token_usage: dict
    stop_reason: str


@dataclass
class ReactResult:
    """Final result of one `ReactLoop.run()` call."""

    final_text: str
    trace: list[TraceStep]
    ok: bool
    error: Optional[str] = None
    total_tokens: dict = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────
# The loop itself
# ──────────────────────────────────────────────────────────────────────────


# How many chars of any single tool result are written to trace.jsonl.
# Keeps trace files reviewable while preserving the full context Claude saw
# (the full payload still goes back to the model in the next turn).
_TRACE_OBS_TRUNC = 2000


class ReactLoop:
    """Thin ReAct wrapper around `anthropic.AnthropicBedrock.messages.create`.

    Design properties (codex review F3):
      * Reliability comes from `ToolSpec.input_schema` + deterministic
        phase sequencing in the orchestrator, NOT from ReAct itself.
      * `trace_path` is the single source of truth for "what the agent did";
        every step is appended immediately so a crashed run is still inspectable.
      * Tool exceptions are demoted to `is_error=True` tool_results so the
        model can recover within the same loop.
    """

    def __init__(
        self,
        *,
        tools: list[ToolSpec],
        system: str,
        model: str = "us.anthropic.claude-sonnet-4-6",
        region: str = "us-east-1",
        provider: str = "bedrock",
        max_iters: int = 20,
        max_tokens_per_turn: int = 4000,
        trace_path: Optional[str | Path] = None,
        invoke_retries: int = 5,
        invoke_backoff_base: float = 2.0,
        invoke_backoff_max: float = 30.0,
    ) -> None:
        # Lazy import keeps the dataclasses above usable in agent codegen
        # contexts that may not have anthropic installed yet.
        from auto_adapter.agent.converse_client import (  # noqa: PLC0415
            ConverseClient, is_anthropic_model,
        )
        self.provider = provider
        if provider == "holistic":
            from auto_adapter.agent.holistic_client import HolisticClient
            self.client = HolisticClient()
            model = model.replace("us.anthropic.", "eu.anthropic.", 1)
            self._is_anthropic = False
        elif provider == "deepseek":
            from auto_adapter.agent.deepseek_client import create_deepseek_client
            self.client = create_deepseek_client()
            self._is_anthropic = True
        elif provider != "bedrock":
            raise ValueError(f"unsupported model provider {provider!r}")
        elif is_anthropic_model(model):
            from anthropic import AnthropicBedrock  # noqa: PLC0415
            # timeout prevents a hung Bedrock socket from blocking forever
            # (no exception is raised on a dead-but-ESTABLISHED connection).
            self.client = AnthropicBedrock(aws_region=region, timeout=300.0, max_retries=0)
            self._is_anthropic = True
        else:
            # Non-Anthropic Bedrock models (Mistral/Qwen/DeepSeek/Nova) via
            # the unified Converse API, wrapped to the same client shape.
            self.client = ConverseClient(region=region)
            self._is_anthropic = False
        self.tools: dict[str, ToolSpec] = {t.name: t for t in tools}
        if len(self.tools) != len(tools):
            raise ValueError("duplicate ToolSpec.name in tools list")

        # Anthropic API tool schema format
        self.tool_schemas = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        self.system = system
        self.model = model
        self.max_iters = int(max_iters)
        self.max_tokens = int(max_tokens_per_turn)

        self.trace_path = Path(trace_path) if trace_path else None
        if self.trace_path is not None:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            self.trace_path.write_text("")  # truncate on each run
            self.trace_path.with_suffix(".messages.jsonl").write_text("")

        self.invoke_retries = int(invoke_retries)
        self.invoke_backoff_base = float(invoke_backoff_base)
        self.invoke_backoff_max = float(invoke_backoff_max)

    # ─── Private helpers ──────────────────────────────────────────────────

    def _dump_step(self, step: TraceStep) -> None:
        if self.trace_path is None:
            return
        with self.trace_path.open("a") as f:
            f.write(json.dumps(asdict(step), default=str) + "\n")

    def _dump_message_event(self, event: dict) -> None:
        """Keep complete model inputs and tool returns beside the compact trace.

        Record only message payloads, never transport clients, headers or env.
        This also preserves tools executed on the final budgeted turn, whose
        results would otherwise never appear in a subsequent model request.
        """
        if self.trace_path is None:
            return

        def encode(value):
            if hasattr(value, "model_dump"):
                return value.model_dump()
            if hasattr(value, "__dict__"):
                return vars(value)
            return str(value)

        with self.trace_path.with_suffix(".messages.jsonl").open("a") as handle:
            handle.write(json.dumps(event, default=encode, ensure_ascii=False) + "\n")

    def _execute_tools(self, tool_use_blocks: list[Any]) -> list[dict]:
        """Run handlers, capture exceptions, build tool_result content list."""
        results: list[dict] = []
        _MISSING = object()
        for tu in tool_use_blocks:
            # AnthropicBedrock returns objects; only fall back to dict-style
            # if attribute access actually fails. Plain `or` falls through
            # when tu.input == {} (e.g. zero-arg tools) and triggers a TypeError.
            name = getattr(tu, "name", _MISSING)
            if name is _MISSING:
                name = tu["name"]
            tu_id = getattr(tu, "id", _MISSING)
            if tu_id is _MISSING:
                tu_id = tu["id"]
            tu_input = getattr(tu, "input", _MISSING)
            if tu_input is _MISSING:
                tu_input = tu["input"]
            try:
                if name not in self.tools:
                    raise KeyError(f"unknown tool: {name!r}")
                output = self.tools[name].handler(dict(tu_input))
                content = (
                    json.dumps(output, default=str)
                    if isinstance(output, (dict, list))
                    else str(output)
                )
                results.append(
                    {"type": "tool_result", "tool_use_id": tu_id, "content": content}
                )
            except Exception as e:  # noqa: BLE001 — tool errors must not crash loop
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu_id,
                        "content": f"[{type(e).__name__}] {e}",
                        "is_error": True,
                    }
                )
        return results

    @staticmethod
    def _truncate(s: str, n: int = _TRACE_OBS_TRUNC) -> str:
        return s if len(s) <= n else s[:n] + f"…(+{len(s) - n} chars)"

    def _invoke_with_retry(self, msgs: list[dict]) -> Any:
        """Call messages.create with exponential backoff on transient errors.

        Retry on: HTTP 429 (rate limit), 5xx (server / throttle), connection
        / timeout errors. Don't retry on 4xx other than 429 — those are caller
        bugs (bad request, auth, etc.) and retrying just delays the real failure.
        """
        last_exc: Optional[Exception] = None
        # Some newer reasoning models (e.g. claude-opus-4-8) deprecate the
        # temperature kwarg. Strip it for those model ids.
        _drop_temp = "opus-4-8" in self.model or "opus-4-9" in self.model
        for attempt in range(self.invoke_retries + 1):
            try:
                kwargs = dict(
                    model=self.model,
                    system=self.system,
                    tools=self.tool_schemas,
                    messages=msgs,
                    max_tokens=self.max_tokens,
                )
                if not _drop_temp:
                    # Anthropic SDK 1.x removed the named sampling kwargs.
                    # Sonnet 4.6 still accepts them in the API request body.
                    if self._is_anthropic:
                        kwargs["extra_body"] = {"temperature": 0.0}
                    else:
                        kwargs["temperature"] = 0.0
                return self.client.messages.create(**kwargs)
            except Exception as e:  # noqa: BLE001
                last_exc = e
                # Retry on throttling / transient server errors across both
                # the Anthropic SDK and boto3 (Converse) exception families.
                s = (type(e).__name__ + " " + str(e)).lower()
                transient = any(k in s for k in (
                    "throttl", "toomanyrequests", "429", "timeout", "timedout",
                    "connection", "serviceunavailable", "503", "500",
                    "internalserver", "modelnotready",
                ))
                if not transient:
                    raise

            if attempt >= self.invoke_retries:
                break

            # Exponential backoff with jitter: 2^attempt s ± 25 %, capped.
            sleep_s = min(
                self.invoke_backoff_base ** attempt * (0.75 + 0.5 * random.random()),
                self.invoke_backoff_max,
            )
            time.sleep(sleep_s)

        assert last_exc is not None  # for type-checkers
        raise last_exc

    # ─── The main loop ────────────────────────────────────────────────────

    def run(self, user_msg: str) -> ReactResult:
        msgs: list[dict] = [{"role": "user", "content": user_msg}]
        trace: list[TraceStep] = []
        tok_in = tok_out = tok_cache_r = 0

        for it in range(self.max_iters):
            t0 = time.time()
            _vlog(f"iter {it}/{self.max_iters}: invoking LLM (msgs={len(msgs)})…")
            request_event = {
                "event": "request", "iter": it, "provider": self.provider,
                "model": self.model, "system": self.system,
                "tools": self.tool_schemas, "max_tokens": self.max_tokens,
                "messages": msgs,
            }
            if self.provider == "holistic":
                from .holistic_client import to_openai_messages
                request_event["gateway_messages"] = to_openai_messages(msgs, self.system)
            self._dump_message_event(request_event)
            try:
                resp = self._invoke_with_retry(msgs)
            except Exception as e:  # noqa: BLE001
                error = f"{self.provider} invoke failed after retries: {type(e).__name__}: {e}"
                self._dump_message_event({"event": "error", "iter": it, "error": error})
                step = TraceStep(iter=it, thought="", actions=[],
                                 observations=[{"is_error": True, "content": error}],
                                 duration_ms=(time.time() - t0) * 1000.0,
                                 token_usage={"in": 0, "out": 0}, stop_reason="invoke_error")
                trace.append(step)
                self._dump_step(step)
                return ReactResult(
                    final_text="",
                    trace=trace,
                    ok=False,
                    error=error,
                    total_tokens={"in": tok_in, "out": tok_out},
                )

            self._dump_message_event({
                "event": "response", "iter": it,
                "response": {"content": resp.content, "stop_reason": resp.stop_reason,
                             "usage": resp.usage},
            })
            # Parse the response content blocks
            thought_text = "".join(b.text for b in resp.content if b.type == "text")
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            duration_ms = (time.time() - t0) * 1000.0
            _vlog(f"iter {it}: LLM done in {duration_ms/1000:.0f}s "
                  f"stop={resp.stop_reason} tool_uses={[t.name for t in tool_uses]}")

            usage = resp.usage
            tok_in += int(getattr(usage, "input_tokens", 0))
            tok_out += int(getattr(usage, "output_tokens", 0))
            tok_cache_r += int(getattr(usage, "cache_read_input_tokens", 0) or 0)

            actions_log = [{"name": t.name, "input": t.input} for t in tool_uses]

            # Terminal: model is done, no more tool calls
            if resp.stop_reason == "end_turn":
                step = TraceStep(
                    iter=it,
                    thought=thought_text,
                    actions=actions_log,  # may be empty
                    observations=[],
                    duration_ms=duration_ms,
                    token_usage={"in": int(usage.input_tokens), "out": int(usage.output_tokens)},
                    stop_reason="end_turn",
                )
                trace.append(step)
                self._dump_step(step)
                return ReactResult(
                    final_text=thought_text,
                    trace=trace,
                    ok=True,
                    total_tokens={"in": tok_in, "out": tok_out, "cache_read": tok_cache_r},
                )

            # Otherwise: run tools, build the next user message, continue
            _vlog(f"iter {it}: executing {len(tool_uses)} tool(s): "
                  f"{[t.name for t in tool_uses]}…")
            _te = time.time()
            tool_results = self._execute_tools(tool_uses)
            self._dump_message_event({"event": "tool_results", "iter": it,
                                      "results": tool_results})
            _vlog(f"iter {it}: tools done in {time.time()-_te:.0f}s")

            step = TraceStep(
                iter=it,
                thought=thought_text,
                actions=actions_log,
                observations=[
                    {
                        "tool_use_id": r["tool_use_id"],
                        "content": self._truncate(r["content"]),
                        "is_error": r.get("is_error", False),
                    }
                    for r in tool_results
                ],
                duration_ms=duration_ms,
                token_usage={"in": int(usage.input_tokens), "out": int(usage.output_tokens)},
                stop_reason=str(resp.stop_reason),
            )
            trace.append(step)
            self._dump_step(step)

            if not tool_results and not (resp.stop_reason == "max_tokens" and resp.content):
                return ReactResult(
                    final_text=thought_text, trace=trace, ok=False,
                    error=f"non-terminal response has no tool calls (stop_reason={resp.stop_reason})",
                    total_tokens={"in": tok_in, "out": tok_out, "cache_read": tok_cache_r},
                )

            # A thinking-only truncation has no tool results. Keep its original
            # blocks and request continuation within the remaining turn budget;
            # native Anthropic-compatible APIs reject an empty user message.
            msgs.append({"role": "assistant", "content": resp.content})
            msgs.append({"role": "user", "content": tool_results or "Continue."})

        # Max iterations exhausted without end_turn
        return ReactResult(
            final_text="",
            trace=trace,
            ok=False,
            error=f"max_iters={self.max_iters} exhausted",
            total_tokens={"in": tok_in, "out": tok_out, "cache_read": tok_cache_r},
        )
