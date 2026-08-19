#!/usr/bin/env python3
"""Run the real SO-ARM101 SDK extension route once."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autoadapter2_sdk.so_arm101.route_check import run_route_check


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument(
        "--gripper-direction",
        required=True,
        choices=("tick-increases-qpos", "tick-decreases-qpos"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = run_route_check(
        model_path=args.model,
        gripper_tick_increases_qpos=args.gripper_direction == "tick-increases-qpos",
    )
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
