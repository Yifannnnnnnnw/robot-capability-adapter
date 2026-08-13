from __future__ import annotations

import json

from demo2.pipeline import SUPPORTED_ROBOTS, _summary, inspect_package, resolve_package, run_all
from demo2.precision_policy import policy_record
from demo2.stage1_scope import DecisionScope


def test_supported_robot_packages_resolve() -> None:
    for robot_id in SUPPORTED_ROBOTS:
        package = inspect_package(robot_id)
        assert package["complete_mjcf"]
        assert package["capability_effects"]
        assert package["skeleton_family"]


def test_reference_calibration_runs_every_robot_with_task_blue_line(tmp_path) -> None:
    root = tmp_path / "supported-robots"
    summary = run_all(root, reference_calibration=True, record_video=False)

    assert summary["all_robots_attempted"] is True
    assert summary["run_completed_count"] == len(SUPPORTED_ROBOTS)
    assert summary["blocked_count"] == 0
    assert summary["validation_b_used"] is False
    for robot_id in SUPPORTED_ROBOTS:
        workspace = root / robot_id
        report = json.loads((workspace / "validate_report.json").read_text())
        blue = json.loads((workspace / "blue_line.json").read_text())
        assert report["exact_requirement_coverage"] is True
        assert report["n_total"] == 5
        assert report["precision_policy"] == policy_record()
        assert not (workspace / "capability.py").exists()
        assert not (workspace / "validation_b.json").exists()
        rendered = json.dumps(blue)
        assert "precision_policy" in rendered
        assert "dwell_s" in rendered
        assert '"guards"' in rendered


def test_summary_keeps_capability_effect_and_requirement_identities_separate() -> None:
    package = resolve_package("robotstudio_so101")
    scope = DecisionScope(
        selected_capability_ids=("arm-g1",),
        selected_effects=("move_to_cartesian",),
        covered_requirement_ids=("req-1",),
        covered_task_ids=("S01",),
        unsupported_requirement_ids=(),
        blocking_requirement_ids=(),
        requirement_to_capability={"req-1": "arm-g1"},
        requirement_to_task={"req-1": "S01"},
        full_task_coverage=True,
    )
    summary = _summary(
        package,
        {
            "all_ok": True,
            "structural_ok": True,
            "tests": [{
                "requirement_id": "req-1",
                "capability_id": "arm-g1",
                "effect": "move_to_cartesian",
                "ok": True,
                "criteria": [],
            }],
        },
        scope,
        mode="test",
        model=None,
        attempts=1,
    )

    assert summary["selected_capability_ids"] == ["arm-g1"]
    assert summary["selected_effects"] == ["move_to_cartesian"]
    assert summary["passed_requirement_ids"] == ["req-1"]
    assert summary["failed_requirement_ids"] == []
