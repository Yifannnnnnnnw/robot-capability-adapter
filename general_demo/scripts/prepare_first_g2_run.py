"""Prepare a deterministic first-G2 formal run pack without executing it."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    # Keep the script directly runnable from a source checkout without requiring
    # an editable install of the General Demo package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from autoadapter2.orchestration.run_pack import _main

    return _main()


if __name__ == "__main__":  # pragma: no cover - covered by the CLI smoke test
    raise SystemExit(main())
