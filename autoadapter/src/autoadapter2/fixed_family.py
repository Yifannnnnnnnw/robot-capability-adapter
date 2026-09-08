"""Repository-owned fixed diagnostic contracts; never loaded from candidate files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2] / "references/fixed_family_v1"


def reference(robot_id: str) -> dict[str, Any] | None:
    if Path(robot_id).name != robot_id:
        return None
    folder = ROOT / robot_id
    if not (folder / "capability_design.json").is_file():
        return None
    return {
        "capability_design": json.loads((folder / "capability_design.json").read_text()),
        "validation_suite": json.loads((folder / "capability_validation_suite.json").read_text()),
    }
