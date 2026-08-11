from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from autoadapter2.demo import evaluate_fixed_demo_criterion
from autoadapter2.evaluation import FrozenVideoProfile
from autoadapter2.foundation.errors import ContractError
from autoadapter2.generation import ModelApiClient, ModelApiConfig, model_api
from autoadapter2.generation.model_api import DEFAULT_BASE_URL, DEFAULT_MODEL
from autoadapter2.integration import write_stable_json
from autoadapter2.orchestration.first_g2_demo import (
    FirstG2DemoConfig,
    ValidationAProfileTemplate,
    _model_client,
    materialize_validation_a_profile,
    run_first_g2_demo,
)

from test_general_demo_runner import _RobotSession, _plan


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
            robot_session_factory=lambda selected_robot, _manifest, _run_dir: _FixedCriterionSession(
                width,
                selected_robot,
                {task.task_id: task.private_criterion for task in plan.tasks},
            ),
            output_root=tmp_path / "first-g2-output",
            blue_line_input_paths=blue_line_paths,
            validation_a_profile=plan.validation_a_profile,
            validation_harness_config=plan.validation_harness_config,
            video_profile=profile,
            model_client=model_client,
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
    assert result.stage_artifacts_path.is_file()
    assert result.model_call_log_path.is_file()

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
    assert model_client.stages[:3] == ["stage1", "blue_line", "stage2"]
    assert "react_consumer" in model_client.stages


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
            test_only_allow_fixture_session=True,
        )
    )
    assert result.status == "COMPLETE"
    assert model_client.stages[:3] == ["stage1", "blue_line", "stage2"]
    artifacts = json.loads(result.stage_artifacts_path.read_text(encoding="utf-8"))
    assert artifacts["stage1_preflight"]["status"] == "SEALED"
    assert artifacts["validation_a_profile_hash"] == result.runner_result.summary["validation_a_profile_hash"]


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
        "label": {"value": "fixture", "type": "string", "shape": "scalar", "unit": "none", "frame": "world"},
    }
    vector = profile.fixture_probes["cap-vector-v9"]["inputs"]["target"]
    assert vector["value"] == [0.0, 0.0, 0.0, 0.0]
    assert vector["unit"] == "m"
    assert vector["frame"] == "base"


def _frozen_model_config() -> dict:
    return {
        "artifact_type": "model_prompt_config",
        "schema_version": "1.0.0",
        "provider": "anthropic-compatible",
        "base_url": DEFAULT_BASE_URL,
        "endpoint_path": "/v1/chat/completions",
        "model": DEFAULT_MODEL,
        "max_tokens": 4096,
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
