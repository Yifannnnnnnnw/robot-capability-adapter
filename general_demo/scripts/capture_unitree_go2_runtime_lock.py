#!/usr/bin/env python3
"""Capture one immutable Go2 Linux Runtime Lock from the running image."""

from __future__ import annotations

import argparse
import json

from autoadapter2.integrations.unitree_go2.runtime_lock import (
    capture_runtime_lock,
    write_runtime_lock,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    lock = capture_runtime_lock(image_digest=args.image_digest)
    write_runtime_lock(args.output, lock)
    print(json.dumps({"output": args.output, "status": lock["status"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
