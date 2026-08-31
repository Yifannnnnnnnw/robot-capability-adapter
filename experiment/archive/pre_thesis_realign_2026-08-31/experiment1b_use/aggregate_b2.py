#!/usr/bin/env python3
"""Aggregate AA2-B2 scheduler and terminal records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime.b2 import DEFAULT_MANIFEST_PATH
from runtime.results import B2AggregationError, aggregate_schedulers, write_aggregate


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate AA2-B2 formal records")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--scheduler", type=Path, action="append", required=True)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--output", type=Path, help="write aggregate JSON to this path")
    args = parser.parse_args()
    try:
        aggregate = aggregate_schedulers(
            args.scheduler,
            manifest_path=args.manifest,
            require_complete=args.require_complete,
        )
    except B2AggregationError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    if args.output is not None:
        write_aggregate(args.output, aggregate)
    print(json.dumps(aggregate, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
