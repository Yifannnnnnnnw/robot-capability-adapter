from __future__ import annotations

import json

import demo2.pipeline as pipeline
from demo2.pipeline import SUPPORTED_ROBOTS, _summary, inspect_package, resolve_package, run_all
from demo2.precision_policy import policy_record
from demo2.stage1_scope import DecisionScope


NEW_REFERENCE_ROBOTS = {"kinova_gen3", "leap_hand"}
SECOND_REFERENCE_ROBOTS = {"ufactory_xarm7", "aloha_2"}
THIRD_REFERENCE_ROBOTS = {
    "hello_robot_stretch_2",
    "boston_dynamics_spot_with_arm",
    "unitree_g1",
    "google_barkour_vb",
}


def test_new_reference_robots_resolve_inspect_and_are_run_all_targets(
    tmp_path,
    monkeypatch,
) -> None:
    assert NEW_REFERENCE_ROBOTS <= set(SUPPORTED_ROBOTS)
    for robot_id in NEW_REFERENCE_ROBOTS:
        package = resolve_package(robot_id)
        inspected = inspect_package(robot_id)
        assert package.reference_study_path.is_file()
        assert inspected["robot_id"] == robot_id
        assert inspected["capability_effects"]
        assert inspected["complete_mjcf"]

    calls: list[tuple[str, bool, bool]] = []

    def fake_run_robot(
        robot_id,
        output_root,
        *,
        settings,
        reference_calibration,
        record_video,
    ):
        del output_root, settings
        calls.append((robot_id, reference_calibration, record_video))
        return {
            "robot_id": robot_id,
            "run_status": "RUN_COMPLETED",
            "pipeline_completed": True,
            "physical_validation_executed": True,
            "all_tasks_passed": False,
            "validation_passed": False,
            "requirements_passed": 0,
        }

    monkeypatch.setattr(pipeline, "run_robot", fake_run_robot)
    summary = run_all(
        tmp_path / "registered-reference-robots",
        reference_calibration=True,
        record_video=False,
    )

    assert [robot_id for robot_id, _, _ in calls] == list(SUPPORTED_ROBOTS)
    assert all(reference_calibration is True for _, reference_calibration, _ in calls)
    assert all(record_video is False for _, _, record_video in calls)
    assert NEW_REFERENCE_ROBOTS <= set(summary["robots"])


def test_second_reference_robots_are_registered_run_all_targets(
    tmp_path,
    monkeypatch,
) -> None:
    assert SECOND_REFERENCE_ROBOTS <= set(SUPPORTED_ROBOTS)
    assert pipeline.REFERENCE_STUDIES["ufactory_xarm7"] == "ufactory_xarm7/study.json"
    assert pipeline.REFERENCE_STUDIES["aloha_2"] == "aloha_2/study.json"
    for robot_id in SECOND_REFERENCE_ROBOTS:
        package = resolve_package(robot_id)
        inspected = inspect_package(robot_id)
        assert package.reference_study_path.is_file()
        assert inspected["robot_id"] == robot_id
        assert inspected["capability_effects"]
        assert inspected["complete_mjcf"]

    calls: list[str] = []

    def fake_run_robot(robot_id, output_root, **kwargs):
        del output_root, kwargs
        calls.append(robot_id)
        return {
            "robot_id": robot_id,
            "run_status": "RUN_COMPLETED",
            "all_tasks_passed": False,
            "requirements_passed": 0,
        }

    monkeypatch.setattr(pipeline, "run_robot", fake_run_robot)
    summary = run_all(tmp_path / "second-reference-robots", reference_calibration=True)

    assert calls == list(SUPPORTED_ROBOTS)
    assert SECOND_REFERENCE_ROBOTS <= set(summary["robots"])


def test_third_reference_robots_are_registered_run_all_targets(
    tmp_path,
    monkeypatch,
) -> None:
    assert THIRD_REFERENCE_ROBOTS <= set(SUPPORTED_ROBOTS)
    assert set(SUPPORTED_ROBOTS) == set(pipeline.REFERENCE_STUDIES)
    for robot_id in THIRD_REFERENCE_ROBOTS:
        package = resolve_package(robot_id)
        inspected = inspect_package(robot_id)
        assert package.reference_study_path.is_file()
        assert inspected["robot_id"] == robot_id
        assert inspected["capability_effects"]
        assert inspected["complete_mjcf"]

    calls: list[str] = []

    def fake_run_robot(robot_id, output_root, **kwargs):
        del output_root, kwargs
        calls.append(robot_id)
        return {
            "robot_id": robot_id,
            "run_status": "RUN_COMPLETED",
            "all_tasks_passed": False,
            "requirements_passed": 0,
        }

    monkeypatch.setattr(pipeline, "run_robot", fake_run_robot)
    summary = run_all(tmp_path / "third-reference-robots", reference_calibration=True)

    assert calls == list(SUPPORTED_ROBOTS)
    assert THIRD_REFERENCE_ROBOTS <= set(summary["robots"])


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
