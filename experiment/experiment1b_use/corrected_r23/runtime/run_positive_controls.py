#!/usr/bin/env python3
"""Run the ten fixed-driver controls that gate corrected R2/R3."""

from __future__ import annotations

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter/src"
for root in (REPOSITORY_ROOT, SOURCE_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from experiment.experiment1b_use.corrected_r1.runtime.positive_controls import (  # noqa: E402
    main,
)


if __name__ == "__main__":
    default_manifest = Path(__file__).resolve().parents[1] / "config/manifest.json"
    if "--manifest" not in sys.argv:
        sys.argv[1:1] = ["--manifest", str(default_manifest)]
    raise SystemExit(main())
