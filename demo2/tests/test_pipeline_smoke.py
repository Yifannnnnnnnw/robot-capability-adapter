from __future__ import annotations

import json

from demo2.pipeline import SUPPORTED_ROBOTS, _summary, inspect_package, resolve_package, run_all


def test_three_robot_packages_resolve() -> None:
    for robot_id in SUPPORTED_ROBOTS:
        package = inspect_package(robot_id)
        assert package["complete_mjcf"]
        assert package["capability_effects"]
        assert package["skeleton_family"]


def test_reference_calibration_uses_only_legacy_direct_validator(tmp_path) -> None:
    root = tmp_path / "three-robot"
    summary = run_all(root, reference_calibration=True, record_video=False)

    assert summary["pipeline_completed"] is True
    assert summary["validation_passed"] is True
    assert summary["validation_b_used"] is False
    for robot_id in SUPPORTED_ROBOTS:
        workspace = root / robot_id
        report = json.loads((workspace / "validate_report.json").read_text())
        blue = json.loads((workspace / "blue_line.json").read_text())
        assert report["all_ok"] is True
        assert report["structural_ok"] is True
        assert not (workspace / "capability.py").exists()
        assert not (workspace / "validation_b.json").exists()
        rendered = json.dumps(blue)
        assert "dwell_s" not in rendered
        assert "guard_ids" not in rendered


def test_summary_compares_capability_ids_not_effect_names() -> None:
    package = resolve_package("robotstudio_so101")
    summary = _summary(
        package,
        {
            "all_ok": True,
            "structural_ok": True,
            "tests": [{"capability_id": "arm-g1", "ok": True}],
        },
        mode="test",
        model=None,
        attempts=1,
        selected_capability_ids=("arm-g1",),
    )

    assert summary["passed_capability_ids"] == ["arm-g1"]
    assert summary["failed_capability_ids"] == []
