"""Focused pagination regression for the explicit Study continuation test script."""

import importlib
import json
from pathlib import Path

from autoadapter2.agent_context import AgentContextManager


def test_public_pages_survive_three_group_cutoff_within_existing_budget(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    script = importlib.import_module("test_study_continuation")
    messages = [{"role": "user", "content": "fixed generation input fixture"}]
    for i in range(4):
        arguments = {"path": "generation_inputs/study.json", "offset": i * 5000}
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": f"page-{i}", "type": "function", "function": {
                    "name": "read_file", "arguments": json.dumps(arguments),
                },
            }]},
            {"role": "tool", "tool_call_id": f"page-{i}", "content": json.dumps({
                "ok": True, "result": {**arguments, "root": "public_package",
                    "content": "x" * 4980 + f"PAGE_END_{i}",
                    "next_offset": (i + 1) * 5000 if i < 3 else None,
                    "total_chars": 20000},
            })},
        ])
    old = AgentContextManager().project_native(messages)
    assert "PAGE_END_0" not in json.dumps(old.messages)
    current = script.BudgetOnlyNativeContext().project_native(messages)
    assert all(f"PAGE_END_{i}" in json.dumps(current.messages) for i in range(4))
    assert current.stats["summarized_group_count"] == 0
    assert current.stats["history_char_budget"] == 80000
    bounded = script.BudgetOnlyNativeContext(history_char_budget=8192).project_native(messages)
    assert bounded.stats["summarized_group_count"] > 0
    assert bounded.stats["projected_history_chars"] <= 8192
    assert "PAGE_END_3" in json.dumps(bounded.messages)
