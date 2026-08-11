#!/usr/bin/env python3
"""Run one explicit Go2 readiness attempt; never promotes any manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autoadapter2.integrations.unitree_go2.readiness import run_readiness


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--runtime-lock", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--reference-root", type=Path)
    args = parser.parse_args()
    report = run_readiness(
        run_id=args.run_id,
        manifest_path=args.manifest,
        profile_path=args.profile,
        runtime_lock_path=args.runtime_lock,
        model_path=args.model,
        report_path=args.report,
        reference_root=args.reference_root,
    )
    print(
        json.dumps(
            {
                "report": str(args.report),
                "verdict": report["verdict"],
            }
        )
    )
    return 0 if report["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
