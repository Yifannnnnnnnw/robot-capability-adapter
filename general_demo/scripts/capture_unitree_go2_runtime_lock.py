#!/usr/bin/env python3
"""Capture one immutable Go2 Linux Runtime Lock from the running image."""

from __future__ import annotations

import argparse
import json

from autoadapter2.integrations.unitree_go2.runtime_lock import (
    capture_experimental_arm64_runtime_lock,
    capture_runtime_lock,
    write_runtime_lock,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--experimental-arm64",
        action="store_true",
        help="capture the explicit native Linux arm64 experimental lock",
    )
    args = parser.parse_args()
    capture = (
        capture_experimental_arm64_runtime_lock
        if args.experimental_arm64
        else capture_runtime_lock
    )
    lock = capture(image_digest=args.image_digest)
    write_runtime_lock(args.output, lock)
    print(json.dumps({"output": args.output, "status": lock["status"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
