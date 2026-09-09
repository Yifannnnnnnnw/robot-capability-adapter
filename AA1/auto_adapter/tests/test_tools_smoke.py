# SPDX-License-Identifier: Apache-2.0
"""Smoke test for auto_adapter.agent.tools via the ReAct loop.

End-to-end: agent is asked to discover skeletons, look up the arm spec
schema, then write a small JSON file naming its choice. We then assert:
  * the file exists on disk (write_file actually fired)
  * list_skeletons + inspect_skeleton were called at least once each
  * the JSON contains the right skeleton name

Costs a few hundred input tokens of Sonnet 4.6.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from auto_adapter.agent import ReactLoop, ToolSpec
from auto_adapter.agent.tools import make_mvp_local_tools


CALL_LOG: list[str] = []


def _wrap_with_logging(t: ToolSpec) -> ToolSpec:
    orig = t.handler

    def wrapped(inp: dict):
        CALL_LOG.append(t.name)
        return orig(inp)

    return ToolSpec(
        name=t.name,
        description=t.description,
        input_schema=t.input_schema,
        handler=wrapped,
    )


def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="auto_adapter_tools_smoke_"))
    try:
        tools = [_wrap_with_logging(t) for t in make_mvp_local_tools(workspace)]

        loop = ReactLoop(
            tools=tools,
            system=(
                "You are bootstrapping a robot driver. You have 4 tools: "
                "list_skeletons, inspect_skeleton, write_file, read_file. "
                "Follow this exact procedure for the task below:\n"
                " 1) Call list_skeletons to see what is available.\n"
                " 2) For the skeleton whose name starts with 'ArmSerial', call "
                "    inspect_skeleton to get its Spec field schema.\n"
                " 3) Call write_file to save a JSON file at path 'choice.json' "
                "    whose content is exactly: "
                "    {\"chosen_skeleton\": \"<the skeleton name>\", \"spec_name\": \"<its Spec class name>\"}\n"
                " 4) Reply with one sentence confirming the file was written."
            ),
            model=os.environ.get("VECTOR_MODEL", "us.anthropic.claude-sonnet-4-6"),
            region=os.environ.get("AWS_REGION", "us-east-1"),
            max_iters=8,
            max_tokens_per_turn=800,
            trace_path=workspace / "trace.jsonl",
        )

        result = loop.run(
            "Bootstrap: pick the arm skeleton class, inspect its Spec, "
            "and persist your choice to choice.json."
        )

        print(f"[ok={result.ok}] iters={len(result.trace)} tokens={result.total_tokens}")
        print(f"[CALL_LOG] {CALL_LOG}")
        print(f"[final_text] {result.final_text[:200]!r}")
        print(f"[workspace] {workspace}")

        assert result.ok, f"loop failed: {result.error}"
        assert "list_skeletons" in CALL_LOG, "list_skeletons never called"
        assert "inspect_skeleton" in CALL_LOG, "inspect_skeleton never called"
        assert "write_file" in CALL_LOG, "write_file never called"

        choice_path = workspace / "choice.json"
        assert choice_path.exists(), f"{choice_path} not written"
        choice = json.loads(choice_path.read_text())
        assert choice.get("chosen_skeleton") == "ArmSerialDLSSkeleton", (
            f"unexpected choice: {choice}"
        )
        assert choice.get("spec_name") == "ArmSpec", f"unexpected spec_name: {choice}"

        print("\n[ALL OK]  tools.py smoke test passed.")
        sys.stdout.flush()
        os._exit(0)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
