#!/usr/bin/env python3
"""Run one fixed AA1 DEMO through the maintained ReCAP task path."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

AA1 = Path(__file__).resolve().parents[2] / "AA1"
sys.path.insert(0, str(AA1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", type=Path, required=True)
    args = parser.parse_args()

    # The SDK's short default grace period can interrupt video/trace flushing.
    # Keep this experiment-local so the AA1 mainline is unchanged.
    import mcp.client.stdio

    mcp.client.stdio.PROCESS_TERMINATION_TIMEOUT = 30.0
    from auto_adapter.agent.recap import run_demo

    request = json.loads(args.input_json.read_text(encoding="utf-8"))
    report = run_demo(**request)
    print(json.dumps({
        "ok": report.get("ok"),
        "status": report.get("status"),
        "physical_task_success": report.get("physical_task_success"),
        "evaluation_error": report.get("evaluation_error"),
        "report_path": report.get("report_path"),
        "video_path": report.get("video_path"),
        "physics_samples_path": report.get("physics_samples_path"),
    }, allow_nan=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
