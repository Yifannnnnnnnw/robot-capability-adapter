from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoadapter2.evaluation import FrozenVideoProfile
from autoadapter2.foundation.errors import ContractError
from autoadapter2.orchestration.first_g2_demo import (
    FirstG2DemoConfig,
    run_first_g2_demo,
)

from test_general_demo_runner import _RobotSession, _plan


class _FixtureModelClient:
    """Test-only adapter around the existing deterministic stage fixtures."""

    def __init__(self, models):
        self._models = models
        self.stages: list[str] = []

    def generate_json(self, stage, prompt, inputs):
        self.stages.append(stage)
        generator = {
            "stage1": self._models.stage1,
            "blue_line": self._models.blue_line,
            "stage2": self._models.stage2,
        }[stage]
        return generator.generate_json(stage, prompt, inputs)

    def repair(self, request):
        return self._models.repair(request)

    def react(self, request):
        self.stages.append("react_consumer")
        return self._models.consumer(request)


@pytest.mark.parametrize(
    ("robot", "width"),
    [("so-arm101", 6), ("unitree-go2", 12)],
)
def test_first_g2_entrypoint_runs_both_robot_shapes_and_persists_ffmpeg_evidence(
    tmp_path: Path, robot: str, width: int
) -> None:
    plan, models = _plan(tmp_path, robot, width)
    snapshot = json.loads(Path(tmp_path / plan.run_snapshot_path).read_text(encoding="utf-8"))
    blue_line_paths = tuple(
        tmp_path / reference["path"]
        for reference in snapshot["blue_line_input_refs"]
    )
    model_client = _FixtureModelClient(models)
    profile = FrozenVideoProfile(
        profile_id="first-g2-test-video",
        profile_version="1.0.0",
        camera="external-evaluation",
        view="robot-and-task-scene",
        fps=5,
        width=2,
        height=2,
        container="matroska",
        codec="ffv1",
    )

    result = run_first_g2_demo(
        FirstG2DemoConfig(
            root=tmp_path,
            robot=robot,
            integration_manifest_path=plan.integration_manifest_path,
            run_snapshot_path=plan.run_snapshot_path,
            readiness_report_path=plan.readiness_report_path,
            robot_session_factory=lambda selected_robot, _manifest, _run_dir: _RobotSession(
                width, selected_robot
            ),
            output_root=tmp_path / "first-g2-output",
            blue_line_input_paths=blue_line_paths,
            validation_a_profile=plan.validation_a_profile,
            validation_harness_config=plan.validation_harness_config,
            video_profile=profile,
            model_client=model_client,
            criterion_evaluator=lambda criterion, evidence: bool(criterion)
            and evidence == {"passed": True},
            test_only_allow_fixture_session=True,
        )
    )

    assert result.status == "COMPLETE"
    assert result.runner_result.summary["granularity_profile"]["granularity"] == "G2"
    assert len(result.runner_result.demo_trials) == 5
    assert all(item.status == "PASS" for item in result.runner_result.demo_trials)
    assert result.summary_path.is_file()
    assert result.seal_path.is_file()
    assert result.validation_video_references_path.is_file()
    assert result.demo_video_references_path.is_file()

    validation_refs = json.loads(
        result.validation_video_references_path.read_text(encoding="utf-8")
    )
    demo_refs = json.loads(result.demo_video_references_path.read_text(encoding="utf-8"))
    assert len(validation_refs["videos"]) == 1
    assert len(demo_refs["videos"]) == 5
    for reference in validation_refs["videos"] + demo_refs["videos"]:
        manifest_path = result.run_directory / reference["manifest"]["path"]
        media_path = result.run_directory / reference["media"]["path"]
        assert manifest_path.is_file()
        assert media_path.is_file()
        assert media_path.stat().st_size > 0

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["status"] == "COMPLETE"
    assert "criterion_text" not in json.dumps(summary)
    assert model_client.stages[:3] == ["stage1", "blue_line", "stage2"]
    assert "react_consumer" in model_client.stages


def test_first_g2_production_path_rejects_fixture_physical_evidence(tmp_path: Path) -> None:
    plan, models = _plan(tmp_path, "so-arm101", 6)
    with pytest.raises(ContractError, match="TEST_FIXTURE_ONLY"):
        run_first_g2_demo(
            FirstG2DemoConfig(
                root=tmp_path,
                robot="so-arm101",
                integration_manifest_path=plan.integration_manifest_path,
                run_snapshot_path=plan.run_snapshot_path,
                readiness_report_path=plan.readiness_report_path,
                robot_session_factory=lambda selected_robot, _manifest, _run_dir: _RobotSession(
                    6, selected_robot
                ),
                validation_a_profile=plan.validation_a_profile,
                validation_harness_config=plan.validation_harness_config,
                model_client=_FixtureModelClient(models),
            )
        )
