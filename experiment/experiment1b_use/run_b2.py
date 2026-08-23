#!/usr/bin/env python3
"""Run one bounded AA2-B2 formal unit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter" / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from runtime.b2 import (  # noqa: E402
    B2FormalError,
    DEFAULT_MANIFEST_PATH,
    FormalDispatchBlocked,
    run_formal_unit,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one AA2-B2 formal unit")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--unit-id", required=True, help="exact b2::<robot>::<task>::<model>::<replicate> ID")
    parser.add_argument("--output", type=Path, required=True, help="fresh output root for this unit")
    parser.add_argument("--env-file", type=Path, help="parent-side credential dotenv file")
    args = parser.parse_args()
    try:
        terminal = run_formal_unit(
            args.unit_id,
            manifest_path=args.manifest,
            output_root=args.output,
            env_file=args.env_file,
        )
    except FormalDispatchBlocked as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    except B2FormalError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    print(json.dumps(terminal, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
