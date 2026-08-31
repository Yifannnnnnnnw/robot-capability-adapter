#!/usr/bin/env python3
"""Run selected corrected-R1 tasks with the fixed 3+1 worker split."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter/src"
for root in (REPOSITORY_ROOT, SOURCE_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from experiment.experiment1b_use.corrected_r1.runtime.dispatch import (  # noqa: E402
    CorrectedDispatchBlocked,
    CorrectedDispatchError,
    DEFAULT_MANIFEST_PATH,
    run_corrected_scheduler,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Schedule corrected-R1 units")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--task-id",
        action="append",
        help=(
            "task filter; repeatable. Use M2_REPLACEMENTS for the two replacement "
            "units"
        ),
    )
    parser.add_argument("--unit-id", action="append", help="exact fresh unit ID")
    parser.add_argument(
        "--replicate-id",
        action="append",
        help="replicate filter; repeatable (for example R2 or R3)",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--company-env-file", type=Path, required=True)
    parser.add_argument("--m5-env-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        scheduler = run_corrected_scheduler(
            manifest_path=args.manifest,
            output_root=args.output,
            company_env_file=args.company_env_file,
            m5_env_file=args.m5_env_file,
            task_ids=args.task_id,
            unit_ids=args.unit_id,
            replicate_ids=args.replicate_id,
            company_workers=3,
            m5_workers=1,
        )
    except (CorrectedDispatchBlocked, CorrectedDispatchError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    print(json.dumps(scheduler, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
