#!/usr/bin/env python3
"""Single-command entry point for the SO-ARM101 minimal Demo."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from soarm_demo.pipeline import run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "aws"), default="offline")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    run_id = args.run_id or f"{args.mode}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    output = run_pipeline(demo_root=root, mode=args.mode, run_id=run_id)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
