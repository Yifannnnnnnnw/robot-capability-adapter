"""Focused aggregation checks for the scripted-oracle runner."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


HERE = Path(__file__).resolve().parent


def _load_runner() -> ModuleType:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    spec = importlib.util.spec_from_file_location(
        "b2_oracle_runner_test_subject",
        HERE / "run_oracle_canary.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load B2 oracle runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _install_fakes(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    failing_task: str | None = None,
    integrity_failure_task: str | None = None,
    missing_video_task: str | None = None,
) -> list[Any]:
    import autoadapter2.b2.episode_runner as episode_runner
    import autoadapter2.libraries as libraries

    calls: list[Any] = []
    monkeypatch.setattr(runner, "validate", lambda: {"task_count": 10})
    monkeypatch.setattr(
        libraries,
        "load_robot_package",
        lambda root: SimpleNamespace(root=root),
    )

    def fake_episode(
        *, config: Any, package: Any, model: Any, budgets: Any
    ) -> dict[str, Any]:
        del package, model, budgets
        calls.append(config)
        verdict = "FAIL" if config.task_id == failing_task else "PASS"
        video_complete = config.record_video and config.task_id != missing_video_task
        return {
            "episode": {
                "robot_configuration_id": config.robot_configuration_id,
                "task_id": config.task_id,
                "video_path": str(config.output_dir / "videos" / "episode.mp4"),
            },
            "controller": {
                "status": "CONTROLLER_FINISHED",
                "capability_calls": 1,
            },
            "worker": {"worker_completed": True},
            "harness": {
                "physical_harness_verdict": verdict,
                "task_metric_passed": verdict == "PASS",
                "physical_execution_passed": verdict == "PASS",
                "physical_integrity_passed": (
                    verdict == "PASS"
                    and config.task_id != integrity_failure_task
                ),
                "video_complete": video_complete,
            },
        }

    monkeypatch.setattr(
        episode_runner,
        "run_b2_diagnostic_episode",
        fake_episode,
    )
    return calls


def test_complete_passing_video_cohort_passes_optional_diagnostic_and_retains_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    calls = _install_fakes(runner, monkeypatch)

    report = runner.run(
        only_task=None,
        wall_timeout_s=30.0,
        output_dir=tmp_path,
        record_video=True,
    )

    assert report["formal_episode"] is False
    assert report["formal_denominator_entry"] is False
    assert report["complete_task_cohort"] is True
    assert report["selected_canaries_passed"] is True
    assert report["diagnostic_cohort_passed"] is True
    assert len(calls) == 10
    assert all(call.record_video is True for call in calls)
    assert all(call.task_suite_path == runner.TASK_SUITE_PATH for call in calls)
    assert all(call.output_dir.parent.name == call.robot_configuration_id for call in calls)
    assert (tmp_path / "oracle_canary_index.json").is_file()
    for result in report["results"]:
        task_report = tmp_path / result["report"]
        assert task_report.is_file()
        assert result["oracle_canary_passed"] is True


@pytest.mark.parametrize(
    (
        "only_task",
        "record_video",
        "failing_task",
        "integrity_failure_task",
        "missing_video_task",
    ),
    [
        ("mw_sweep_into_goal", True, None, None, None),
        (None, False, None, None, None),
        (None, True, "GO2-T03", None, None),
        (None, True, None, "mw_pick_place", None),
        (None, True, None, None, "GO2-T06"),
    ],
)
def test_subset_no_video_failed_verdict_or_missing_video_cannot_pass_cohort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    only_task: str | None,
    record_video: bool,
    failing_task: str | None,
    integrity_failure_task: str | None,
    missing_video_task: str | None,
) -> None:
    runner = _load_runner()
    _install_fakes(
        runner,
        monkeypatch,
        failing_task=failing_task,
        integrity_failure_task=integrity_failure_task,
        missing_video_task=missing_video_task,
    )

    report = runner.run(
        only_task=only_task,
        wall_timeout_s=30.0,
        output_dir=tmp_path,
        record_video=record_video,
    )

    assert report["diagnostic_cohort_passed"] is False
