from __future__ import annotations

import json
from pathlib import Path

from autoadapter2.pipeline import _public_experience


LIBRARY_ROOT = Path(__file__).resolve().parents[1] / "libraries" / "experience"


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_wrist_rotation_snapshot_is_indexed_and_accepted_by_runtime_projection() -> None:
    index = _read_json(LIBRARY_ROOT / "index.json")
    entries = index["snapshots"]
    assert isinstance(entries, list) and len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, dict)

    snapshot_path = LIBRARY_ROOT / str(entry["path"])
    snapshot = _read_json(snapshot_path)
    assert snapshot["snapshot_id"] == entry["snapshot_id"]
    assert snapshot["usage_scope"] == "later_matched_run_only"
    assert snapshot["b1_input"] is False
    assert snapshot["benefit_claim"] is False

    records = _public_experience(snapshot, "robotstudio_so101")
    assert len(records) == 1
    record = records[0]
    assert record["reviewed"] is True
    assert record["source_run_id"] == "m2-opus5-pick-place-255761c"
    assert set(record) == {
        "experience_id",
        "reviewed",
        "observation",
        "lesson",
        "recommendation",
        "scope",
        "evidence",
        "source_run_id",
    }
    assert "explicit wrist-rotation capability" in str(record["lesson"])
    assert "set_wrist_roll" in str(record["recommendation"])
    assert "Do not treat this recommendation as evidence" in str(
        record["recommendation"]
    )
