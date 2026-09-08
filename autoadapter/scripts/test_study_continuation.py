#!/usr/bin/env python3
"""Single-robot Study-result continuation test using the existing diagnostic runner.

Requires --study-from; remaining flags are the existing runner's flags.
This test uses a 300-second request deadline and retains native history within its
original character budget. Canonical snapshots, feedback retention and real tools remain.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import sys
from pathlib import Path

from autoadapter2.agent_context import AgentContextManager

import run_fixed_family_diagnostic as runner


class BudgetOnlyNativeContext(AgentContextManager):
    """Keep available history until the existing character budget requires pruning."""

    def project_native(self, messages):
        # There cannot be more tool groups than messages. The inherited budget
        # loop still removes old groups; no model/execution budget is increased.
        self.recent_groups = max(1, len(messages))
        projection = super().project_native(messages)
        projection.stats["retention_policy"] = "within_existing_character_budget"
        return projection


def continuation_client(*args, **kwargs):
    client = runner.model_client(*args, **kwargs)
    if client.config.tool_history_mode != "native":
        raise ValueError("this diagnostic test requires native tool history")
    route_timeout_s = client.config.timeout_s
    client.config = replace(client.config, timeout_s=300.0)
    client._context_manager = BudgetOnlyNativeContext(
        history_char_budget=client.config.history_char_budget,
    )
    evidence_dir = kwargs.get("evidence_dir")
    if evidence_dir is not None:
        runner.write(Path(evidence_dir).parent / "continuation_test_settings.json", {
            "script": "scripts/test_study_continuation.py",
            "history_char_budget": client.config.history_char_budget,
            "retention_policy": "within_existing_character_budget",
            "request_timeout_s": client.config.timeout_s,
            "route_default_timeout_s": route_timeout_s,
            "mainline_default_changed": False,
        })
    return client


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--study-from", required=True, type=Path)
    parser.parse_known_args(argv)
    runner.main(argv, client_factory=continuation_client)


if __name__ == "__main__":
    main()
