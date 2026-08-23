#!/usr/bin/env python3
"""Schedule selected AA2-B2 formal units in fresh subprocesses."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime.b2 import B2FormalError, DEFAULT_MANIFEST_PATH
from runtime.parallel import run_scheduler


def main() -> int:
    parser = argparse.ArgumentParser(description="Schedule AA2-B2 formal units")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--unit-id", action="append", help="selected planned unit ID; repeatable")
    parser.add_argument("--output", type=Path, required=True, help="scheduler output root")
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--env-file", type=Path, help="parent-side credential dotenv file")
    args = parser.parse_args()
    try:
        scheduler = run_scheduler(
            manifest_path=args.manifest,
            output_root=args.output,
            unit_ids=args.unit_id,
            max_workers=args.max_workers,
            env_file=args.env_file,
        )
    except B2FormalError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    print(json.dumps(scheduler, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
