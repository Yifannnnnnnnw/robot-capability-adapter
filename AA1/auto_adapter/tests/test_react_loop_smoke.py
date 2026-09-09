# SPDX-License-Identifier: Apache-2.0
"""Smoke test for ReactLoop on AWS Bedrock.

Two trivial tools (`add`, `echo`) + a user task that requires using both
in sequence. Validates loop ⇒ tool call ⇒ observation ⇒ next turn until
end_turn. Also checks trace.jsonl is written incrementally.

Uses real Bedrock — costs a few hundred input tokens of Claude Sonnet 4.6.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from auto_adapter.agent import ReactLoop, ToolSpec


# ──────────────────────────────────────────────────────────────────────────
# Tools
# ──────────────────────────────────────────────────────────────────────────

CALL_LOG: list[tuple[str, dict]] = []


def _add(inp: dict) -> dict:
    CALL_LOG.append(("add", inp))
    return {"sum": float(inp["a"]) + float(inp["b"])}


def _echo(inp: dict) -> dict:
    CALL_LOG.append(("echo", inp))
    return {"echoed": str(inp["msg"])}


TOOLS = [
    ToolSpec(
        name="add",
        description="Compute the sum of two numbers. Returns {sum: number}.",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
        handler=_add,
    ),
    ToolSpec(
        name="echo",
        description="Echo a string back. Returns {echoed: string}.",
        input_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        handler=_echo,
    ),
]


# ──────────────────────────────────────────────────────────────────────────
# Test
# ──────────────────────────────────────────────────────────────────────────


def main() -> None:
    trace_path = Path("/tmp/react_smoke_trace.jsonl")

    loop = ReactLoop(
        tools=TOOLS,
        system=(
            "You solve tasks by calling tools. Always use the `add` tool for "
            "arithmetic. Always use the `echo` tool to format your final number "
            "as a string. After echo returns, give a one-sentence final answer."
        ),
        model=os.environ.get("VECTOR_MODEL", "us.anthropic.claude-sonnet-4-6"),
        region=os.environ.get("AWS_REGION", "us-east-1"),
        max_iters=6,
        max_tokens_per_turn=600,
        trace_path=trace_path,
    )

    result = loop.run("Please compute 2 + 3 using the add tool, "
                      "then pass the result string through echo, "
                      "then tell me the final answer.")

    # ── Assertions ────────────────────────────────────────────────────────
    print(f"[ok={result.ok}] iters={len(result.trace)} tokens={result.total_tokens}")
    print(f"[final_text] {result.final_text[:200]!r}")
    print(f"[CALL_LOG] {CALL_LOG}")

    assert result.ok, f"loop failed: {result.error}"
    assert any(name == "add" for name, _ in CALL_LOG), "add tool was never called"
    assert any(name == "echo" for name, _ in CALL_LOG), "echo tool was never called"
    # Final text should mention 5 somehow
    assert "5" in result.final_text, f"final_text missing 5: {result.final_text!r}"

    # Trace file should exist with one line per step
    assert trace_path.exists(), "trace file not written"
    lines = trace_path.read_text().strip().split("\n")
    assert len(lines) == len(result.trace), (
        f"trace.jsonl lines ({len(lines)}) != trace ({len(result.trace)})"
    )
    # Each line is valid JSON with the expected keys
    for ln in lines:
        d = json.loads(ln)
        for key in ("iter", "thought", "actions", "observations", "duration_ms",
                    "token_usage", "stop_reason"):
            assert key in d, f"trace step missing {key!r}: {d.keys()}"

    print("\n[ALL OK]  ReactLoop smoke test passed.")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
