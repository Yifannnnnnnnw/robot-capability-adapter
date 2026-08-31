#!/usr/bin/env python3
"""Run one non-formal Exp1b corrected-R1 unit."""

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
    run_corrected_unit,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one corrected-R1 unit")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--unit-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        terminal = run_corrected_unit(
            args.unit_id,
            manifest_path=args.manifest,
            output_root=args.output,
            env_file=args.env_file,
        )
    except (CorrectedDispatchBlocked, CorrectedDispatchError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    print(json.dumps(terminal, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
