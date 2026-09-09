# SPDX-License-Identifier: Apache-2.0
"""Smoke test for execute_python via AgentCore CodeInterpreter.

Starts a real CI session, hands it to the ReactLoop, asks Claude to use
the tool to compute something non-trivial (so it can't shortcut), then
stops the session in a finally block.

Costs: ~1k input tokens of Sonnet 4.6 + one CI session (charged per
session-second, ~10s typical).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3

from auto_adapter.agent import ReactLoop, ToolSpec
from auto_adapter.agent.tools import make_execute_python_tool


REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL = os.environ.get("VECTOR_MODEL", "us.anthropic.claude-sonnet-4-6")
CI_ID = "aws.codeinterpreter.v1"


def main() -> None:
    ci = boto3.client("bedrock-agentcore", region_name=REGION)
    sess = ci.start_code_interpreter_session(
        codeInterpreterIdentifier=CI_ID,
        name="auto-adapter-tools-smoke",
        sessionTimeoutSeconds=600,
    )
    sid = sess["sessionId"]
    print(f">> CI session started: {sid}")

    try:
        tool = make_execute_python_tool(ci, sid)

        loop = ReactLoop(
            tools=[tool],
            system=(
                "You verify numerical claims by ACTUALLY computing them with "
                "execute_python. Never guess. Never trust your own arithmetic. "
                "Always call the tool, read the stdout, then answer."
            ),
            model=MODEL,
            region=REGION,
            max_iters=4,
            max_tokens_per_turn=600,
            trace_path=Path("/tmp/auto_adapter_execpy_trace.jsonl"),
        )

        # A problem trivial for Python but where a careless LLM would shortcut.
        result = loop.run(
            "What is the SHA-256 hash of the string 'auto_adapter' "
            "(UTF-8 encoded)? Use execute_python to compute it via hashlib, "
            "then tell me the hex digest."
        )

        print(f"[ok={result.ok}] iters={len(result.trace)} tokens={result.total_tokens}")
        print(f"[final_text] {result.final_text[:300]!r}")

        assert result.ok, f"loop failed: {result.error}"
        # Compute the ground-truth locally — the test asserts that the CI session
        # (and the agent that read its stdout) produced the same answer.
        import hashlib
        expected = hashlib.sha256("auto_adapter".encode()).hexdigest()
        assert expected in result.final_text, (
            f"final_text missing expected hash {expected!r}: {result.final_text!r}"
        )
        print(f"\n[ALL OK]  execute_python smoke test passed (hash={expected[:16]}...).")
        sys.stdout.flush()
    finally:
        try:
            ci.stop_code_interpreter_session(codeInterpreterIdentifier=CI_ID, sessionId=sid)
            print(f">> CI session stopped: {sid}")
        except Exception as e:  # noqa: BLE001
            print(f"!! failed to stop session {sid}: {e}", file=sys.stderr)
    os._exit(0)


if __name__ == "__main__":
    main()
