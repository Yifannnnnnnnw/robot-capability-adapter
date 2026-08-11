#!/usr/bin/env python3
"""Run one explicit SO-ARM101 readiness attempt; never promotes the manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autoadapter2.integrations.so_arm101.readiness import (
    capture_verified_runtime_lock,
    run_readiness,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-runtime-lock", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--runtime-lock", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument(
        "--gripper-direction",
        choices=("tick-increases-qpos", "tick-decreases-qpos"),
    )
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--reference-root",
        type=Path,
        help="single artifact root containing the manifest/profile and report evidence",
    )
    args = parser.parse_args()
    if args.capture_runtime_lock:
        if args.model is None or args.reference_root is None:
            parser.error("--capture-runtime-lock requires --model and --reference-root")
        lock = capture_verified_runtime_lock(
            output_path=args.report,
            model_path=args.model,
            reference_root=args.reference_root,
        )
        print(json.dumps({"runtime_lock": str(args.report), "status": lock["status"]}))
        return 0
    required = {
        "--run-id": args.run_id,
        "--manifest": args.manifest,
        "--profile": args.profile,
        "--runtime-lock": args.runtime_lock,
        "--model": args.model,
        "--gripper-direction": args.gripper_direction,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        parser.error("missing required arguments: " + ", ".join(missing))
    report = run_readiness(
        run_id=args.run_id,
        manifest_path=args.manifest,
        profile_path=args.profile,
        runtime_lock_path=args.runtime_lock,
        model_path=args.model,
        gripper_tick_increases_qpos=args.gripper_direction == "tick-increases-qpos",
        report_path=args.report,
        reference_root=args.reference_root,
    )
    print(json.dumps({"report": str(args.report), "verdict": report["verdict"]}))
    return 0 if report["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
