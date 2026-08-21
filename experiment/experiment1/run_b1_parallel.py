#!/usr/bin/env python3
"""Executable entry point for isolated parallel B1 cells."""

from __future__ import annotations

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
AUTOADAPTER_SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter" / "src"
for path in (REPOSITORY_ROOT, AUTOADAPTER_SOURCE_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from experiment.experiment1.runtime.parallel import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
