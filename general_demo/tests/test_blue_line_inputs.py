from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoadapter2.orchestration.run_pack import _load_reviewed_blue_inputs


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("robot", "configuration", "expected_measurement_count"),
    [
        ("so-arm101", "so-arm101-follower-stock-gripper", 15),
        ("unitree-go2", "unitree-go2-stock-12dof", 8),
    ],
)
def test_checked_in_blue_inputs_are_loader_valid_and_exactly_bound(
    robot: str,
    configuration: str,
    expected_measurement_count: int,
) -> None:
    base = PROJECT_ROOT / "general_demo/private_governance/blue_line/inputs" / robot / "1.0.0"
    standards_path = base / "standards_snapshot.json"
    catalog_path = base / "measurement_catalog.json"
    raw_snapshot = json.loads(standards_path.read_text(encoding="utf-8"))
    raw_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

    standards, catalog, _, _ = _load_reviewed_blue_inputs(
        PROJECT_ROOT,
        standards_path,
        catalog_path,
        robot,
        configuration,
    )

    assert raw_snapshot["review_status"] == "HUMAN_APPROVED"
    assert raw_snapshot["intended_use"] == "capability_validation_b"
    assert raw_snapshot["demo_criteria_import_policy"] == "NOT_AUTOMATIC"
    assert len(raw_snapshot["record_refs"]) == 5
    for reference in raw_snapshot["record_refs"]:
        record = json.loads((PROJECT_ROOT / reference["path"]).read_text(encoding="utf-8"))
        assert record["approval_lineage"]["reviewed_by"] == "project_owner"
        assert record["approval_lineage"]["review_status"] == "HUMAN_APPROVED"

    expected_measurements = {
        criterion["measurement_id"]: criterion["metric"]
        for standard in standards["standards"]
        for criterion in standard["criteria"]
    }
    catalog_measurements = {
        measurement["measurement_id"]: set(measurement["metrics"])
        for measurement in catalog["measurements"]
    }
    assert len(catalog_measurements) == expected_measurement_count
    assert {
        measurement_id: metric
        for measurement_id, metric in expected_measurements.items()
    } == {
        measurement_id: next(iter(metrics))
        for measurement_id, metrics in catalog_measurements.items()
    }
    assert {
        guard_id for standard in standards["standards"] for guard_id in standard["required_guard_ids"]
    } == {guard["guard_id"] for guard in catalog["guards"]}
    assert raw_catalog["truth_policy"] == {
        "candidate_self_report": "FORBIDDEN_AS_TRUTH",
        "sdk_receipt": "FORBIDDEN_AS_TRUTH",
    }
