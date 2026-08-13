from __future__ import annotations

import copy
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from autoadapter2.demo import evaluate_fixed_demo_criterion
from autoadapter2.evaluation import FrozenVideoProfile
from autoadapter2.foundation.canonical import canonical_bytes
from autoadapter2.foundation.errors import ContractError
from autoadapter2.foundation.hashing import content_hash
from autoadapter2.generation import ModelApiClient, ModelApiConfig, model_api
from autoadapter2.generation.model_api import DEFAULT_BASE_URL, DEFAULT_MODEL
from autoadapter2.integration import write_stable_json
from autoadapter2.implementation import CallbackSandbox
from autoadapter2.orchestration.first_g2_demo import (
    FirstG2DemoConfig,
    ValidationAProfileTemplate,
    _budgets,
    _run_with_session_close,
    _model_client,
    materialize_validation_a_profile,
    run_first_g2_demo,
    verify_first_g2_run_closure,
)
from autoadapter2.validation import MeasurementSample
from autoadapter2.validation.validation_a import _descriptor_match

from test_general_demo_runner import _RobotSession, _plan as _base_plan


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _FixtureModelClient:
    """Test-only adapter around the existing deterministic stage fixtures."""

    def __init__(self, models):
        self._models = models
        self.stages: list[str] = []
        self._stage2_source: str | None = None
        self._stage2_calls = 0

    def generate_json(self, stage, prompt, inputs):
        self.stages.append(stage)
        generator = {
            "stage1": self._models.stage1,
            "blue_line": self._models.blue_line,
            "stage2": self._models.stage2,
        }[stage]
        if stage == "stage2":
            self._stage2_calls += 1
            if self._stage2_source is None:
                response = generator.generate_json(stage, prompt, inputs)
                self._stage2_source = response["capability.py"]
            if self._stage2_calls < 10:
                capability_ids = [
                    binding["capability_id"]
                    for binding in inputs["binding_contract"]["bindings"]
                ]
                capability_id = capability_ids[(self._stage2_calls - 1) % len(capability_ids)]
                return {
                    "action": "sandbox",
                    "capability.py": self._stage2_source,
                    "probe": {
                        "probe_id": f"fixture-probe-{self._stage2_calls}",
                        "capability_id": capability_id,
                    },
                }
            return {"action": "submit", "capability.py": self._stage2_source}
        return generator.generate_json(stage, prompt, inputs)

    def repair(self, request):
        return self._models.repair(request)

    def react(self, request):
        self.stages.append("react_consumer")
        return self._models.consumer(request)


def _fixture_sandbox() -> CallbackSandbox:
    return CallbackSandbox(
        lambda _source, probe: {
            "status": "OK",
            "summary": "The public fixture probe completed.",
            "observations": {
                "probe_id": probe["probe_id"],
                "capability_id": probe["capability_id"],
            },
            "exception": None,
        }
    )


def _plan(root: Path, robot: str, width: int):
    plan, models = _base_plan(root, robot, width)
    snapshot_path = root / plan.run_snapshot_path
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    budget_path = root / snapshot["budget_ref"]["path"]
    budget = json.loads(budget_path.read_text(encoding="utf-8"))
    budget["stage2"] = {
        "max_llm_calls": 30,
        "min_llm_calls_before_submit": 10,
        "min_successful_sandbox_calls_before_submit": 5,
        "require_all_design_capability_probes": True,
    }
    write_stable_json(budget_path, budget)
    snapshot["budget_ref"]["sha256"] = content_hash(
        budget_path.read_bytes()
    ).removeprefix("sha256:")
    write_stable_json(snapshot_path, snapshot)
    return plan, models


def _passing_value(check):
    expected = check["value"]
    if isinstance(expected, bool) or check["comparator"] == "==":
        return expected
    if check["comparator"] in {"<", "<="}:
        return float(expected) if check["comparator"] == "<=" else float(expected) - 1.0
    return float(expected) + 1.0


def _fixture_demo_evidence(criterion):
    values = {}
    for field in ("checks", "motion_checks", "terminal_checks"):
        for check in criterion.get(field, []):
            values[check["metric"]] = _passing_value(check)
    required = max(
        [
            float(criterion[name])
            for name in (
                "continuous_dwell_s",
                "observation_window_s",
                "terminal_continuous_dwell_s",
            )
            if name in criterion
        ]
        + [
            float(check["value"])
            for field in ("checks", "motion_checks", "terminal_checks")
            for check in criterion.get(field, [])
            if str(check.get("metric", "")).endswith("_dwell_s")
        ]
        + [0.5]
    )
    evidence = {
        "task_id": criterion["task_id"],
        "guard_results": {guard_id: True for guard_id in criterion["guard_ids"]},
    }
    if criterion["task_id"] != "G04":
        evidence.update(
            {
                "duration_s": required,
                "samples": [
                    {"time_s": 0.0, "metrics": dict(values)},
                    {"time_s": required, "metrics": dict(values)},
                ],
            }
        )
    else:
        motion_values = {
            check["metric"]: _passing_value(check)
            for check in criterion["motion_checks"]
        }
        terminal_values = {
            check["metric"]: _passing_value(check)
            for check in criterion["terminal_checks"]
        }
        evidence.update(
            {
                "duration_s": required,
                "motion_duration_s": 1.0,
                "terminal_duration_s": required,
                "samples": [
                    {"time_s": 0.0, "metrics": dict(motion_values)},
                    {"time_s": 1.0, "metrics": dict(motion_values)},
                    {"time_s": 2.0, "metrics": dict(terminal_values)},
                ],
                "motion_samples": [
                    {"time_s": 0.0, "metrics": dict(motion_values)},
                    {"time_s": 1.0, "metrics": dict(motion_values)},
                ],
                "terminal_samples": [
                    {"time_s": 0.0, "metrics": dict(terminal_values)},
                    {"time_s": required, "metrics": dict(terminal_values)},
                ],
            }
        )
    return evidence


class _FixedCriterionSession(_RobotSession):
    def __init__(self, width: int, robot: str, criteria):
        super().__init__(width, robot)
        self._criteria = criteria

    def demo_evidence(self, task_id: str):
        return _fixture_demo_evidence(self._criteria[task_id])


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
    sessions = []

    def create_session(selected_robot, _manifest, _run_dir):
        session = _FixedCriterionSession(
            width,
            selected_robot,
            {task.task_id: task.private_criterion for task in plan.tasks},
        )
        sessions.append(session)
        return session

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
            robot_session_factory=create_session,
            output_root=tmp_path / "first-g2-output",
            blue_line_input_paths=blue_line_paths,
            validation_a_profile=plan.validation_a_profile,
            validation_harness_config=plan.validation_harness_config,
            video_profile=profile,
            model_client=model_client,
            sandbox=_fixture_sandbox(),
            test_only_allow_fixture_session=True,
        )
    )

    assert result.status == "COMPLETE"
    assert len(sessions) == 1 and sessions[0].close_calls == 1
    assert result.runner_result.summary["granularity_profile"]["granularity"] == "G2"
    assert len(result.runner_result.demo_trials) == 5
    assert all(item.status == "PASS" for item in result.runner_result.demo_trials)
    assert result.summary_path.is_file()
    assert result.seal_path.is_file()
    assert result.validation_video_references_path.is_file()
    assert result.demo_video_references_path.is_file()
    assert result.stage_artifacts_path.is_file()
    assert result.stage_artifacts_seal_path.is_file()
    assert result.model_call_log_path.is_file()
    assert result.run_closure_path.is_file()
    assert result.run_closure_seal_path.is_file()

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
    assert summary["validation_a_profile_hash"] == plan.validation_a_profile.profile_hash
    assert "criterion_text" not in json.dumps(summary)
    assert all("consumer_trace" not in trial for trial in summary["demo_trials"])
    assert model_client.stages[:3] == ["stage1", "blue_line", "stage2"]
    assert model_client.stages.count("stage1") == 1
    assert "react_consumer" in model_client.stages
    assert verify_first_g2_run_closure(
        tmp_path, result.run_closure_path, result.run_closure_seal_path
    ) == result.closure_hash
    stage_artifacts = json.loads(result.stage_artifacts_path.read_text(encoding="utf-8"))
    assert stage_artifacts["stages"]["stage1"]["call_log"]
    assert "stage1_preflight" not in stage_artifacts
    for trial in stage_artifacts["demo"]["trials"]:
        assert trial["consumer_trace"]
        assert trial["consumer_trace_hash"] == content_hash(
            canonical_bytes(trial["consumer_trace"])
        )
    assert {
        "stage1", "blue_line", "stage2", "validation_and_repair",
    } <= set(stage_artifacts["stages"])
    assert {
        "implementation_bundle_hash", "binding_contract", "binding_seal",
        "starter_skeleton", "capability_source", "source_seal",
        "implementation_manifest", "implementation_manifest_seal",
        "call_log", "sandbox_log", "successful_sandbox_calls",
        "covered_sandbox_capability_ids", "diagnostics",
    } <= set(stage_artifacts["stages"]["stage2"])
    stage2_artifact = stage_artifacts["stages"]["stage2"]
    assert stage2_artifact["llm_calls"] >= 10
    assert stage2_artifact["successful_sandbox_calls"] >= 5
    assert stage2_artifact["covered_sandbox_capability_ids"] == [
        "set-joint-configuration"
    ]
    assert "sandbox_unavailable" not in json.dumps(stage2_artifact)
    stage2_summary = next(
        item for item in summary["stages"] if item["stage"] == "stage2"
    )
    assert stage2_summary["successful_sandbox_calls"] >= 5
    assert stage2_summary["covered_sandbox_capability_ids"] == [
        "set-joint-configuration"
    ]
    repair_artifacts = stage_artifacts["stages"]["validation_and_repair"]["repair"]
    assert repair_artifacts["initial_validation_a"]["report_seal"]
    assert repair_artifacts["initial_validation_b"]["report_seal"]
    assert repair_artifacts["initial_validation_b"]["executions"]
    assert repair_artifacts["run_ledger"]
    assert stage_artifacts["promotion"]["layer_hash"]
    assert len(stage_artifacts["demo"]["trials"]) == 5
    closure = json.loads(result.run_closure_path.read_text(encoding="utf-8"))
    closure["files"]["summary"]["sha256"] = "0" * 64
    write_stable_json(result.run_closure_path, closure)
    with pytest.raises(ContractError):
        verify_first_g2_run_closure(
            tmp_path, result.run_closure_path, result.run_closure_seal_path
        )


def _complete_first_g2_fixture_run(tmp_path: Path):
    plan, models = _plan(tmp_path, "so-arm101", 6)
    return run_first_g2_demo(
        FirstG2DemoConfig(
            root=tmp_path,
            robot="so-arm101",
            integration_manifest_path=plan.integration_manifest_path,
            run_snapshot_path=plan.run_snapshot_path,
            readiness_report_path=plan.readiness_report_path,
            robot_session_factory=lambda selected_robot, _manifest, _run_dir: _FixedCriterionSession(
                6,
                selected_robot,
                {task.task_id: task.private_criterion for task in plan.tasks},
            ),
            validation_a_profile=plan.validation_a_profile,
            validation_harness_config=plan.validation_harness_config,
            video_profile=FrozenVideoProfile(
                "closure-video", "1.0.0", "external", "scene", 5, 2, 2, "matroska", "ffv1"
            ),
            model_client=_FixtureModelClient(models),
            sandbox=_fixture_sandbox(),
            test_only_allow_fixture_session=True,
        )
    )


def test_run_closure_rejects_video_append_delete_and_manifest_tamper(tmp_path: Path) -> None:
    result = _complete_first_g2_fixture_run(tmp_path)
    validation_refs = json.loads(
        result.validation_video_references_path.read_text(encoding="utf-8")
    )
    demo_refs = json.loads(result.demo_video_references_path.read_text(encoding="utf-8"))
    validation_video = validation_refs["videos"][0]
    demo_video = demo_refs["videos"][0]
    validation_manifest = result.run_directory / validation_video["manifest"]["path"]
    demo_manifest = result.run_directory / demo_video["manifest"]["path"]
    validation_media = result.run_directory / validation_video["media"]["path"]
    demo_media = result.run_directory / demo_video["media"]["path"]
    original_validation_manifest = validation_manifest.read_bytes()
    original_demo_manifest = demo_manifest.read_bytes()
    original_validation_media = validation_media.read_bytes()
    original_demo_media = demo_media.read_bytes()

    validation_manifest.write_bytes(original_validation_manifest + b"append")
    with pytest.raises(ContractError):
        verify_first_g2_run_closure(
            tmp_path, result.run_closure_path, result.run_closure_seal_path
        )
    validation_manifest.write_bytes(original_validation_manifest)

    tampered_demo_manifest = json.loads(original_demo_manifest.decode("utf-8"))
    tampered_demo_manifest["recording_id"] = "tampered-recording"
    write_stable_json(demo_manifest, tampered_demo_manifest)
    with pytest.raises(ContractError):
        verify_first_g2_run_closure(
            tmp_path, result.run_closure_path, result.run_closure_seal_path
        )
    demo_manifest.write_bytes(original_demo_manifest)

    validation_media.write_bytes(original_validation_media + b"append")
    with pytest.raises(ContractError):
        verify_first_g2_run_closure(
            tmp_path, result.run_closure_path, result.run_closure_seal_path
        )
    validation_media.write_bytes(original_validation_media)

    demo_media.unlink()
    with pytest.raises(ContractError):
        verify_first_g2_run_closure(
            tmp_path, result.run_closure_path, result.run_closure_seal_path
        )
    demo_media.write_bytes(original_demo_media)
    assert verify_first_g2_run_closure(
        tmp_path, result.run_closure_path, result.run_closure_seal_path
    ) == result.closure_hash


def test_first_g2_materializes_library_template_after_stage1(tmp_path: Path) -> None:
    plan, models = _plan(tmp_path, "so-arm101", 6)
    snapshot_path = tmp_path / plan.run_snapshot_path
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    template = {
        "artifact_type": "validation_a_template",
        "schema_version": "1.0.0",
        "profile_id": "experimental-python-a-v1",
        "robot_model_id": "so-arm101",
        "robot_configuration_id": "so-arm101-follower-stock-gripper",
        "facade": {"members": ["command"], "lifecycle": "framework_injected_sdk_lifecycle"},
        "probe": {
            "input_value_policy": {
                "number": 0.0,
                "integer": 0,
                "boolean": False,
                "string": "fixture",
                "object": {},
                "array": [],
            },
            "metadata_policy": "copy sealed design field metadata exactly",
        },
    }
    template_path = tmp_path / "general_demo/runs/run-so-arm101/validation_a_template.json"
    template_hash = write_stable_json(template_path, template)
    snapshot["library_view_refs"].append(
        {
            "path": template_path.relative_to(tmp_path).as_posix(),
            "sha256": template_hash,
        }
    )
    write_stable_json(snapshot_path, snapshot)
    model_client = _FixtureModelClient(models)
    result = run_first_g2_demo(
        FirstG2DemoConfig(
            root=tmp_path,
            robot="so-arm101",
            integration_manifest_path=plan.integration_manifest_path,
            run_snapshot_path=plan.run_snapshot_path,
            readiness_report_path=plan.readiness_report_path,
            robot_session_factory=lambda selected_robot, _manifest, _run_dir: _FixedCriterionSession(
                6,
                selected_robot,
                {task.task_id: task.private_criterion for task in plan.tasks},
            ),
            validation_harness_config=plan.validation_harness_config,
            video_profile=FrozenVideoProfile(
                "first-g2-template-video", "1.0.0", "external", "scene", 5, 2, 2, "matroska", "ffv1"
            ),
            model_client=model_client,
            sandbox=_fixture_sandbox(),
            test_only_allow_fixture_session=True,
        )
    )
    assert result.status == "COMPLETE"
    assert model_client.stages[:3] == ["stage1", "blue_line", "stage2"]
    assert model_client.stages.count("stage1") == 1
    artifacts = json.loads(result.stage_artifacts_path.read_text(encoding="utf-8"))
    assert artifacts["stages"]["stage1"]["status"] == "SEALED"
    assert artifacts["validation_a_profile_hash"] == result.runner_result.summary["validation_a_profile_hash"]


class _TerminalStage1Model(_FixtureModelClient):
    def generate_json(self, stage, prompt, inputs):
        if stage == "stage1":
            self.stages.append(stage)
            return {
                "capabilities": [],
                "unsupported_requirement_ids": [],
                "blocking_requirement_ids": [],
            }
        return super().generate_json(stage, prompt, inputs)


class _ExplodingBlueLineModel(_FixtureModelClient):
    def generate_json(self, stage, prompt, inputs):
        if stage == "blue_line":
            self.stages.append(stage)
            raise RuntimeError("blue line model failure")
        return super().generate_json(stage, prompt, inputs)


class _NonRepairingModel(_FixtureModelClient):
    def repair(self, request):
        raise AssertionError("production must use the continuous Stage 2 repair episode")


class _FailingValidationSession(_FixedCriterionSession):
    def validation_evidence(self, invocation):
        evidence = super().validation_evidence(invocation)
        failing_samples = tuple(
            MeasurementSample(sample.time_s, 1.0) for sample in evidence.samples
        )
        criterion_samples = (
            {
                criterion_id: failing_samples
                for criterion_id in evidence.criterion_samples
            }
            if evidence.criterion_samples is not None
            else None
        )
        return replace(
            evidence,
            samples=failing_samples,
            criterion_samples=criterion_samples,
        )


class _AbruptValidationSession(_FixedCriterionSession):
    def stop_external_recording(self):
        raise RuntimeError("fixture validation recording aborted")


def test_validation_failure_persists_a_verifiable_terminal_closure(tmp_path: Path) -> None:
    plan, models = _plan(tmp_path, "so-arm101", 6)
    model_client = _NonRepairingModel(models)
    result = run_first_g2_demo(
        FirstG2DemoConfig(
            root=tmp_path,
            robot="so-arm101",
            integration_manifest_path=plan.integration_manifest_path,
            run_snapshot_path=plan.run_snapshot_path,
            readiness_report_path=plan.readiness_report_path,
            robot_session_factory=lambda selected_robot, _manifest, _run_dir: _FailingValidationSession(
                6,
                selected_robot,
                {task.task_id: task.private_criterion for task in plan.tasks},
            ),
            validation_a_profile=plan.validation_a_profile,
            validation_harness_config=plan.validation_harness_config,
            video_profile=FrozenVideoProfile(
                "validation-failure-video", "1.0.0", "external", "scene", 5, 2, 2, "matroska", "ffv1"
            ),
            model_client=model_client,
            sandbox=_fixture_sandbox(),
            test_only_allow_fixture_session=True,
        )
    )

    assert result.status == "VALIDATION_FAILED"
    assert "repair" not in model_client.stages
    assert model_client.stages.count("stage2") == 10 + 10 * 20
    stage_artifacts = json.loads(result.stage_artifacts_path.read_text(encoding="utf-8"))
    repair_artifact = stage_artifacts["stages"]["validation_and_repair"]["repair"]
    assert repair_artifact["repair_invocations_used"] == 10
    assert repair_artifact["repair_llm_calls"] == 200
    assert all(entry["episode_trace"]["sandbox_log"] == [] for entry in repair_artifact["repair_log"])
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["status"] == "VALIDATION_FAILED"
    validation_refs = json.loads(
        result.validation_video_references_path.read_text(encoding="utf-8")
    )
    assert len(validation_refs["videos"]) == 1
    assert result.run_closure_path.is_file()
    assert result.run_closure_seal_path.is_file()
    closure = json.loads(result.run_closure_path.read_text(encoding="utf-8"))
    assert closure["status"] == "VALIDATION_FAILED"
    assert verify_first_g2_run_closure(
        tmp_path, result.run_closure_path, result.run_closure_seal_path
    ) == result.closure_hash


def test_incomplete_validation_infrastructure_abort_has_no_closure(
    tmp_path: Path,
) -> None:
    plan, models = _plan(tmp_path, "so-arm101", 6)
    result = run_first_g2_demo(
        FirstG2DemoConfig(
            root=tmp_path,
            robot="so-arm101",
            integration_manifest_path=plan.integration_manifest_path,
            run_snapshot_path=plan.run_snapshot_path,
            readiness_report_path=plan.readiness_report_path,
            robot_session_factory=lambda selected_robot, _manifest, _run_dir: _AbruptValidationSession(
                6,
                selected_robot,
                {task.task_id: task.private_criterion for task in plan.tasks},
            ),
            validation_a_profile=plan.validation_a_profile,
            validation_harness_config=plan.validation_harness_config,
            video_profile=FrozenVideoProfile(
                "validation-abort-video", "1.0.0", "external", "scene", 5, 2, 2, "matroska", "ffv1"
            ),
            model_client=_FixtureModelClient(models),
            sandbox=_fixture_sandbox(),
            test_only_allow_fixture_session=True,
        )
    )

    assert result.status == "VALIDATION_FAILED"
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["stages"][-1]["status"] == "INFRASTRUCTURE_ERROR"
    assert result.closure_hash is None
    assert not result.run_closure_path.exists()
    assert not result.run_closure_seal_path.exists()
    with pytest.raises(ContractError):
        verify_first_g2_run_closure(
            tmp_path, result.run_closure_path, result.run_closure_seal_path
        )


def test_terminal_stage1_persists_call_log_and_closes_session(tmp_path: Path) -> None:
    plan, models = _plan(tmp_path, "so-arm101", 6)
    sessions = []

    def create_session(selected_robot, _manifest, _run_dir):
        session = _FixedCriterionSession(
            6,
            selected_robot,
            {task.task_id: task.private_criterion for task in plan.tasks},
        )
        sessions.append(session)
        return session

    model_client = _TerminalStage1Model(models)
    result = run_first_g2_demo(
        FirstG2DemoConfig(
            root=tmp_path,
            robot="so-arm101",
            integration_manifest_path=plan.integration_manifest_path,
            run_snapshot_path=plan.run_snapshot_path,
            readiness_report_path=plan.readiness_report_path,
            robot_session_factory=create_session,
            validation_a_profile=plan.validation_a_profile,
            validation_harness_config=plan.validation_harness_config,
            video_profile=FrozenVideoProfile(
                "terminal-stage1-video", "1.0.0", "external", "scene", 5, 2, 2, "matroska", "ffv1"
            ),
            model_client=model_client,
            sandbox=_fixture_sandbox(),
            test_only_allow_fixture_session=True,
        )
    )
    assert result.status == "STAGE1_FAILED"
    assert len(sessions) == 1 and sessions[0].close_calls == 1
    assert model_client.stages.count("stage1") == 3
    assert result.runner_result.summary["stages"][1]["llm_calls"] == 3
    artifacts = json.loads(result.stage_artifacts_path.read_text(encoding="utf-8"))
    assert len(artifacts["stages"]["stage1"]["call_log"]) == 3
    assert artifacts["stages"]["stage1"]["diagnostics"]
    assert verify_first_g2_run_closure(
        tmp_path, result.run_closure_path, result.run_closure_seal_path
    ) == result.closure_hash


def test_session_closes_once_when_runner_raises(tmp_path: Path) -> None:
    plan, models = _plan(tmp_path, "so-arm101", 6)
    sessions = []

    def create_session(selected_robot, _manifest, _run_dir):
        session = _FixedCriterionSession(
            6,
            selected_robot,
            {task.task_id: task.private_criterion for task in plan.tasks},
        )
        sessions.append(session)
        return session

    with pytest.raises(RuntimeError, match="blue line model failure"):
        run_first_g2_demo(
            FirstG2DemoConfig(
                root=tmp_path,
                robot="so-arm101",
                integration_manifest_path=plan.integration_manifest_path,
                run_snapshot_path=plan.run_snapshot_path,
                readiness_report_path=plan.readiness_report_path,
                robot_session_factory=create_session,
                validation_a_profile=plan.validation_a_profile,
                validation_harness_config=plan.validation_harness_config,
                video_profile=FrozenVideoProfile(
                    "exception-video", "1.0.0", "external", "scene", 5, 2, 2, "matroska", "ffv1"
                ),
                model_client=_ExplodingBlueLineModel(models),
                test_only_allow_fixture_session=True,
            )
        )
    assert len(sessions) == 1 and sessions[0].close_calls == 1


class _ClosingSession:
    def __init__(self, *, fail: bool = False) -> None:
        self.close_calls = 0
        self.fail = fail

    def close(self) -> None:
        self.close_calls += 1
        if self.fail:
            raise RuntimeError("cleanup failed")


def test_session_cleanup_failure_is_contract_error_and_preserves_primary() -> None:
    success = _ClosingSession()
    assert _run_with_session_close(success, lambda: "ok") == "ok"
    assert success.close_calls == 1

    cleanup_failure = _ClosingSession(fail=True)
    with pytest.raises(ContractError, match="cleanup") as cleanup_error:
        _run_with_session_close(cleanup_failure, lambda: "ok")
    assert cleanup_failure.close_calls == 1

    primary_failure = _ClosingSession(fail=True)

    def fail_primary():
        raise ValueError("primary failure")

    with pytest.raises(ContractError, match="primary failure.*cleanup failed") as error:
        _run_with_session_close(primary_failure, fail_primary)
    assert primary_failure.close_calls == 1
    assert isinstance(error.value.__cause__, ValueError)


@pytest.mark.parametrize("robot", ["so-arm101", "unitree-go2"])
def test_fixed_demo_criterion_requires_duration_evidence(tmp_path: Path, robot: str) -> None:
    plan, _models = _plan(tmp_path, robot, 6 if robot == "so-arm101" else 12)
    for task in plan.tasks:
        evidence = _fixture_demo_evidence(task.private_criterion)
        evidence.pop("duration_s")
        assert not evaluate_fixed_demo_criterion(task.private_criterion, evidence)


def test_validation_a_materialization_uses_each_sealed_capability_descriptor() -> None:
    template = ValidationAProfileTemplate(
        profile_id="experimental-python-a-v1",
        facade_members=("send_action", "get_observation"),
        input_value_policy={
            "number": 0.0,
            "integer": 0,
            "boolean": False,
            "string": "fixture",
            "object": {},
            "array": [],
        },
    )
    design = {
        "capabilities": [
            {
                "capability_id": "cap-scalar-v2",
                "inputs": [
                    {"name": "enabled", "type": "boolean", "shape": "scalar", "unit": "none", "frame": "joint"},
                    {"name": "duration", "type": "float", "shape": "scalar", "unit": "s", "frame": "world"},
                    {"name": "label", "type": "string", "shape": "scalar", "unit": "none", "frame": "world"},
                ],
            },
            {
                "capability_id": "cap-vector-v9",
                "inputs": [
                    {"name": "target", "type": "number", "shape": "vector:4", "unit": "m", "frame": "base"},
                ],
            },
        ]
    }
    profile = materialize_validation_a_profile(template, design)
    assert set(profile.fixture_probes) == {"cap-scalar-v2", "cap-vector-v9"}
    assert profile.fixture_probes["cap-scalar-v2"]["inputs"] == {
        "enabled": {"value": False, "type": "boolean", "shape": "scalar", "unit": "none", "frame": "joint"},
        "duration": {"value": 0.0, "type": "float", "shape": "scalar", "unit": "s", "frame": "world"},
        "label": {"value": "fixture", "type": "string", "shape": "scalar", "unit": "none", "frame": "world"},
    }
    vector = profile.fixture_probes["cap-vector-v9"]["inputs"]["target"]
    assert vector["value"] == [0.0, 0.0, 0.0, 0.0]
    assert vector["unit"] == "m"
    assert vector["frame"] == "base"


def test_validation_a_materializes_fixed_length_array_and_descriptor_matches() -> None:
    template = ValidationAProfileTemplate(
        profile_id="experimental-python-a-v1",
        facade_members=("send_action", "get_observation"),
        input_value_policy={
            "number": 1.25,
            "integer": 0,
            "boolean": False,
            "string": "fixture",
            "object": {},
            "array": ["unused-array-policy"],
        },
    )
    field = {"name": "samples", "type": "array", "shape": "[3]", "unit": "m", "frame": "world"}
    design = {
        "capabilities": [{"capability_id": "cap-array-v3", "inputs": [field]}]
    }

    profile = materialize_validation_a_profile(template, design)
    probe = profile.fixture_probes["cap-array-v3"]["inputs"]["samples"]
    assert probe["value"] == [1.25, 1.25, 1.25]
    assert probe["type"] == field["type"]
    assert probe["shape"] == field["shape"]
    assert _descriptor_match(probe["value"], field)
    assert not _descriptor_match([1.25, 1.25], field)
    assert not _descriptor_match([1.25, 1.25, 1.25, 1.25], field)


def test_validation_a_materializes_scalar_one_tuple_as_number() -> None:
    template = ValidationAProfileTemplate(
        profile_id="experimental-python-a-v1",
        facade_members=("send_action",),
        input_value_policy={
            "number": 1.25,
            "integer": 0,
            "boolean": False,
            "string": "fixture",
            "object": {},
            "array": [],
        },
    )
    field = {
        "name": "goal_region_tolerance",
        "type": "scalar",
        "shape": "(1,)",
        "unit": "m",
        "frame": "world",
        "required": True,
    }
    design = {"capabilities": [{"capability_id": "cap-scalar-alias", "inputs": [field]}]}

    profile = materialize_validation_a_profile(template, design)

    probe = profile.fixture_probes["cap-scalar-alias"]["inputs"]["goal_region_tolerance"]
    assert probe["value"] == 1.25
    assert not isinstance(probe["value"], list)
    assert {key: probe[key] for key in ("type", "shape", "unit", "frame")} == {
        key: field[key] for key in ("type", "shape", "unit", "frame")
    }


@pytest.mark.parametrize("shape", ["(2,)"])
def test_validation_a_rejects_scalar_tuple_shapes_other_than_one(shape: str) -> None:
    template = ValidationAProfileTemplate(
        profile_id="experimental-python-a-v1",
        facade_members=("send_action",),
        input_value_policy={
            "number": 0.0,
            "integer": 0,
            "boolean": False,
            "string": "fixture",
            "object": {},
            "array": [],
        },
    )
    design = {
        "capabilities": [{
            "capability_id": "cap-scalar-invalid",
            "inputs": [{
                "name": "goal_region_tolerance",
                "type": "scalar",
                "shape": shape,
                "unit": "m",
                "frame": "world",
            }],
        }],
    }

    with pytest.raises(ContractError, match="cannot be materialized"):
        materialize_validation_a_profile(template, design)


@pytest.mark.parametrize(
    ("field_type", "shape", "length"),
    [("vector2", "(2,)", 2), ("vector3", "(3,)", 3)],
)
def test_validation_a_materializes_bounded_vector_aliases_and_preserves_metadata(
    field_type: str,
    shape: str,
    length: int,
) -> None:
    template = ValidationAProfileTemplate(
        profile_id="experimental-python-a-v1",
        facade_members=("send_action", "get_observation"),
        input_value_policy={
            "number": 0.0,
            "integer": 0,
            "boolean": False,
            "string": "fixture",
            "object": {},
            "array": [],
        },
    )
    field = {
        "name": "target_position" if field_type == "vector3" else "target_offset",
        "type": field_type,
        "shape": shape,
        "unit": "m",
        "frame": "world",
        "required": True,
    }
    design = {"capabilities": [{"capability_id": "cap-vector-alias", "inputs": [field]}]}
    design_before = copy.deepcopy(design)

    profile = materialize_validation_a_profile(template, design)

    probe = profile.fixture_probes["cap-vector-alias"]["inputs"][field["name"]]
    assert probe["value"] == [0.0] * length
    assert {key: probe[key] for key in ("type", "shape", "unit", "frame")} == {
        key: field[key] for key in ("type", "shape", "unit", "frame")
    }
    assert design == design_before


@pytest.mark.parametrize(
    ("field_type", "shape"),
    [
        ("vector3", "(2,)"),
        ("vector2", "(3,)"),
        ("vector100", "(100,)"),
        ("vector3", "(0,)"),
        ("vector3", "(65,)"),
        ("vector3", "(n,)"),
        ("vector3", "(3)"),
        ("vector3", "(3,4)"),
    ],
)
def test_validation_a_rejects_mismatched_or_unbounded_vector_aliases(
    field_type: str,
    shape: str,
) -> None:
    template = ValidationAProfileTemplate(
        profile_id="experimental-python-a-v1",
        facade_members=("send_action",),
        input_value_policy={
            "number": 0.0,
            "integer": 0,
            "boolean": False,
            "string": "fixture",
            "object": {},
            "array": [],
        },
    )
    design = {
        "capabilities": [{
            "capability_id": "cap-vector-invalid",
            "inputs": [{
                "name": "target_position",
                "type": field_type,
                "shape": shape,
                "unit": "m",
                "frame": "world",
            }],
        }],
    }

    with pytest.raises(ContractError, match="cannot be materialized"):
        materialize_validation_a_profile(template, design)


@pytest.mark.parametrize("shape", ["[]", "[0]", "[-1]", "[3", "3]", "[3.0]", "[abc]"])
def test_validation_a_rejects_malformed_fixed_length_array_shapes(shape: str) -> None:
    template = ValidationAProfileTemplate(
        profile_id="experimental-python-a-v1",
        facade_members=("send_action",),
        input_value_policy={
            "number": 0.0,
            "integer": 0,
            "boolean": False,
            "string": "fixture",
            "object": {},
            "array": [],
        },
    )
    design = {
        "capabilities": [{
            "capability_id": "cap-array-invalid",
            "inputs": [{"name": "samples", "type": "array", "shape": shape, "unit": "m", "frame": "world"}],
        }]
    }

    with pytest.raises(ContractError, match="cannot be materialized"):
        materialize_validation_a_profile(template, design)


def _frozen_model_config() -> dict:
    return {
        "artifact_type": "model_prompt_config",
        "schema_version": "1.0.0",
        "provider": "anthropic-compatible",
        "base_url": DEFAULT_BASE_URL,
        "endpoint_path": "/v1/chat/completions",
        "model": DEFAULT_MODEL,
        "max_tokens": 8192,
        "temperature": 0,
        "timeout_s": 125,
        "credential_env": "AUTOADAPTER_MODEL_API_KEY",
        "roles": {name: f"first-g2-{name}-v1" for name in ("stage1", "blue_line", "stage2", "repair", "consumer")},
    }


def test_model_prompt_config_rejects_empty_and_frozen_model_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("AUTOADAPTER_MODEL_API_KEY", "test-only")
    with pytest.raises(ContractError, match="non-empty|closed"):
        _model_client({})
    mismatch = _frozen_model_config()
    mismatch["model"] = "caller-selected-model"
    with pytest.raises(ContractError, match="frozen first G2 model"):
        _model_client(mismatch)


def test_run_budget_accepts_run_pack_aliases_and_rejects_open_fields() -> None:
    budget = {
        "artifact_type": "run_budget",
        "schema_version": "1.0.0",
        "stage1": {"correction_calls": 2},
        "blue_line": {"max_llm_calls": 3},
        "stage2": {
            "max_llm_calls": 30,
            "min_llm_calls_before_submit": 10,
            "min_successful_sandbox_calls_before_submit": 5,
            "require_all_design_capability_probes": True,
        },
        "repair": {"max_repairs": 10, "max_infrastructure_retries": 1},
        "consumer": {"max_steps": 4},
        "demo": {"demo_repetitions": 1},
    }
    stage1, stage2, repair, consumer = _budgets(budget)
    assert stage1.max_correction_calls == 2
    assert stage2.max_llm_calls == 30
    assert stage2.min_llm_calls_before_submit == 10
    assert stage2.min_successful_sandbox_calls_before_submit == 5
    assert stage2.require_all_design_capability_probes is True
    assert repair.max_repairs == 10
    assert consumer["consumer_max_steps"] == 4

    unknown = json.loads(json.dumps(budget))
    unknown["consumer"]["unknown"] = 4
    with pytest.raises(ContractError, match="closed"):
        _budgets(unknown)

    missing = json.loads(json.dumps(budget))
    del missing["demo"]
    with pytest.raises(ContractError, match="closed"):
        _budgets(missing)

    ninth_repair = json.loads(json.dumps(budget))
    ninth_repair["repair"]["max_repairs"] = 9
    with pytest.raises(ContractError, match="ten repair"):
        _budgets(ninth_repair)


def test_production_rejects_caller_profile_override_as_snapshot_hash_mismatch(tmp_path: Path) -> None:
    plan, _models = _plan(tmp_path, "so-arm101", 6)
    with pytest.raises(ContractError, match="snapshot references"):
        FirstG2DemoConfig(
            root=tmp_path,
            robot="so-arm101",
            integration_manifest_path=plan.integration_manifest_path,
            run_snapshot_path=plan.run_snapshot_path,
            readiness_report_path=plan.readiness_report_path,
            validation_a_profile=plan.validation_a_profile,
        )


def test_cli_rejects_content_addressed_profile_hash_mismatch(tmp_path: Path) -> None:
    plan, _models = _plan(tmp_path, "so-arm101", 6)
    snapshot = json.loads(
        (tmp_path / plan.run_snapshot_path).read_text(encoding="utf-8")
    )
    tampered_path = tmp_path / snapshot["blue_line_input_refs"][0]["path"]
    tampered_path.write_text("{\"tampered\":true}\n", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "general_demo/scripts/run_first_g2_demo.py"),
            "--root",
            str(tmp_path),
            "--robot",
            "so-arm101",
            "--manifest",
            str(tmp_path / plan.integration_manifest_path),
            "--run-snapshot",
            str(tmp_path / plan.run_snapshot_path),
            "--readiness-report",
            str(tmp_path / plan.readiness_report_path),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "hash" in completed.stderr.lower() or "reference" in completed.stderr.lower()


def test_first_g2_production_path_rejects_fixture_physical_evidence(tmp_path: Path) -> None:
    plan, models = _plan(tmp_path, "so-arm101", 6)
    with pytest.raises(ContractError, match="production"):
        run_first_g2_demo(
            FirstG2DemoConfig(
                root=tmp_path,
                robot="so-arm101",
                integration_manifest_path=plan.integration_manifest_path,
                run_snapshot_path=plan.run_snapshot_path,
                readiness_report_path=plan.readiness_report_path,
                robot_session_factory=lambda selected_robot, _manifest, _run_dir: _RobotSession(6, selected_robot),
            )
        )


class _IdentityResponse:
    def __init__(self, model: str):
        self._payload = {
            "model": model,
            "choices": [{"message": {"content": '{"ok":true}'}}],
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_model_api_requires_and_records_returned_model_identity(monkeypatch) -> None:
    config = ModelApiConfig(api_key="test-only")
    monkeypatch.setattr(
        model_api.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _IdentityResponse(config.model),
    )
    client = ModelApiClient(config)
    assert client.generate_json("identity", "Return JSON.", {}) == {"ok": True}
    assert client.calls[0]["returned_model"] == config.model

    monkeypatch.setattr(
        model_api.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _IdentityResponse("wrong-model"),
    )
    with pytest.raises(ContractError, match="identity"):
        client.generate_json("identity-mismatch", "Return JSON.", {})
