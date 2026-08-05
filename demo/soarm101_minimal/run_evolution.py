#!/usr/bin/env python3
"""Run one real model-backed Evolution audit against a frozen source run."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from soarm_demo.evolution_runtime import load_aws_evolution_client, run_evolution


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--max-model-calls", type=int, default=30)
    args = parser.parse_args()
    run_id = args.run_id or "evolution-aws-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    source = Path(args.source_run)
    if not source.is_absolute():
        source = ROOT / "runs" / source
    client = load_aws_evolution_client(ROOT)
    output = run_evolution(
        demo_root=ROOT,
        source_run=source,
        run_id=run_id,
        client=client,
        call_limit=args.max_model_calls,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
