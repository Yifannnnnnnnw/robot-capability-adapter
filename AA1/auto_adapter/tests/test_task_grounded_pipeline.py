# SPDX-License-Identifier: Apache-2.0
"""Focused checks for DESIGN stage separation and dynamic phase gating."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_adapter.orchestrator import (
    PhaseResult,
    SelfAssemble,
    SelfAssembleConfig,
    validate_failure_feedback,
)
from auto_adapter.orchestrator_from_scratch import (
    FromScratchConfig,
    FromScratchOrchestrator,
)


def _piper_scene() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "mjcf" / "piper" / "scene.xml"


def test_config_is_local_only_and_case_bound(tmp_path: Path):
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>", encoding="utf-8")
    with pytest.raises(ValueError, match="scene_cases_path"):
        SelfAssembleConfig(
            "fixture",
            scene,
            tmp_path / "runs",
            scene_cases_path=scene,
        )
    with pytest.raises(ValueError, match="local"):
        SelfAssembleConfig(
            "fixture",
            scene,
            tmp_path / "runs",
            mode="dgx",
        )
    with pytest.raises(ValueError, match="local"):
        FromScratchConfig(
            "fixture",
            scene,
            tmp_path / "runs",
            mode="agentcore",
            capability_design_path=scene,
        )
    with pytest.raises(ValueError, match="positive integer"):
        SelfAssembleConfig(
            "fixture",
            scene,
            tmp_path / "runs",
            max_iters_capability_design=0,
        )


@pytest.mark.parametrize("config,runner", [(SelfAssembleConfig, SelfAssemble),
                                           (FromScratchConfig, FromScratchOrchestrator)])
def test_default_pipeline_retains_input_model_and_requires_design(tmp_path, config, runner):
    actual = _piper_scene()
    instance = runner(config("piper", actual, tmp_path / "runs"))
    assert instance.cfg.mode == "local"
    assert instance.cfg.mjcf_path == actual
    assert instance.capability_design is None
    with pytest.raises(RuntimeError, match="requires"):
        instance._require_capability_design("GENERATE")


def test_standard_study_does_not_run_design_and_design_has_own_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>", encoding="utf-8")
    runner = SelfAssemble(
        SelfAssembleConfig(
            "fixture", scene, tmp_path / "runs"
        )
    )
    calls: list[str] = []

    def fake_run_phase(**kwargs):
        calls.append(kwargs["name"])
        (runner.workspace / "study.json").write_text(
            json.dumps({"robot_id": "fixture"}) + "\n", encoding="utf-8"
        )
        return PhaseResult(kwargs["name"], True, 1.0, token_usage={"in": 1, "out": 2})

    monkeypatch.setattr(runner, "_run_phase", fake_run_phase)
    study = runner._phase_study()
    assert study.ok is True
    assert runner.capability_design is None
    assert calls == ["01_study"]

    design_dir = runner.workspace / "design"
    scene_path = tmp_path / "prepared.xml"
    scene_path.write_text("<mujoco/>", encoding="utf-8")
    design = {"capabilities": [{"capability_id": "cap", "method_name": "move"}]}

    def fake_prepare(study_object):
        assert study_object["robot_id"] == "fixture"
        design_dir.mkdir(parents=True, exist_ok=True)
        (design_dir / "capability_design.json").write_text(
            json.dumps(design), encoding="utf-8"
        )
        runner.capability_design = design
        runner.scene_cases_path = tmp_path / "cases.yaml"
        runner.scene_paths = {"scene_a": scene_path}
        return {
            "design_path": str(design_dir / "capability_design.json"),
            "token_usage": {"in": 3, "out": 5},
            "duration_sec": 0.25,
            "scene_cases_path": str(runner.scene_cases_path),
            "scene_paths": {"scene_a": str(scene_path)},
        }

    monkeypatch.setattr(runner, "_prepare_capability_design", fake_prepare)
    result = runner._phase_design()
    assert result.ok is True
    assert result.token_usage == {"in": 3, "out": 5}
    assert runner.scene_paths == {"scene_a": scene_path}
    assert result.metadata["scene_paths"] == {"scene_a": str(scene_path)}
    assert result.metadata["capability_design_duration_sec"] == pytest.approx(0.25)
    assert calls == ["01_study"]


def test_dynamic_standard_stage_order_and_failure_gating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>", encoding="utf-8")
    runner = SelfAssemble(
        SelfAssembleConfig(
            "fixture", scene, tmp_path / "runs"
        )
    )
    events: list[str] = []

    def study():
        events.append("study")
        (runner.workspace / "study.json").write_text("{}", encoding="utf-8")
        return PhaseResult("01_study", True, 0.0)

    def design():
        events.append("design")
        design_dir = runner.workspace / "design"
        design_dir.mkdir(exist_ok=True)
        (design_dir / "capability_design.json").write_text("{}", encoding="utf-8")
        runner.capability_design = {"capabilities": []}
        return PhaseResult("design", True, 0.0)

    def generate():
        events.append("generate")
        (runner.workspace / "driver.py").write_text("pass", encoding="utf-8")
        return PhaseResult("02_generate", True, 0.0)

    def validate():
        events.append("validate")
        return PhaseResult("03_validate", True, 0.0)

    monkeypatch.setattr(runner, "_phase_study", study)
    monkeypatch.setattr(runner, "_phase_design", design)
    monkeypatch.setattr(runner, "_phase_generate", generate)
    monkeypatch.setattr(runner, "_phase_validate", validate)
    result = runner.run(stop_after="design")
    assert result.ok is True
    assert events == ["study", "design"]
    assert [phase.name for phase in result.phases] == [
        "01_study",
        "design",
        "generate",
        "validate",
        "export",
        "demo",
    ]

    failed_runner = SelfAssemble(
        SelfAssembleConfig(
            "fixture", scene, tmp_path / "failed"
        )
    )
    failed_events: list[str] = []

    def failed_study():
        failed_events.append("study")
        return PhaseResult("01_study", False, 0.0, error="study failed")

    monkeypatch.setattr(failed_runner, "_phase_study", failed_study)
    monkeypatch.setattr(
        failed_runner,
        "_phase_design",
        lambda: failed_events.append("design"),
    )
    failed = failed_runner.run()
    assert failed.ok is False
    assert failed_events == ["study"]
    assert all(not phase.ok for phase in failed.phases[1:])


def test_dynamic_repair_pass_sets_effective_result_but_keeps_failed_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>", encoding="utf-8")
    runner = SelfAssemble(
        SelfAssembleConfig(
            "fixture", scene, tmp_path / "runs"
        )
    )
    validation_calls = 0

    def study():
        (runner.workspace / "study.json").write_text("{}", encoding="utf-8")
        return PhaseResult("01_study", True, 0.0)

    def design():
        design_dir = runner.workspace / "design"
        design_dir.mkdir(exist_ok=True)
        (design_dir / "capability_design.json").write_text("{}", encoding="utf-8")
        runner.capability_design = {"capabilities": []}
        return PhaseResult("design", True, 0.0)

    def generate():
        (runner.workspace / "driver.py").write_text("# candidate", encoding="utf-8")
        return PhaseResult("02_generate", True, 0.0)

    def validate():
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            return PhaseResult(
                "03_validate", False, 0.0,
                error="initial measurement failed",
                metadata={"repairable": True},
            )
        return PhaseResult("03_validate", True, 0.0)

    monkeypatch.setattr(runner, "_phase_study", study)
    monkeypatch.setattr(runner, "_phase_design", design)
    monkeypatch.setattr(runner, "_phase_generate", generate)
    monkeypatch.setattr(runner, "_phase_repair", lambda *_args: generate())
    monkeypatch.setattr(runner, "_phase_validate", validate)

    result = runner.run(stop_after="validate")

    assert result.ok is True
    assert validation_calls == 2
    assert any(
        phase.name == "03_validate" and phase.ok is False
        for phase in result.phases
    )
    validations = [phase for phase in result.phases if phase.name == "03_validate"]
    assert validations[-1].ok is True


def test_scratch_dynamic_stop_after_study_uses_current_react_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>", encoding="utf-8")
    runner = FromScratchOrchestrator(
        FromScratchConfig(
            "fixture", scene, tmp_path / "runs", mode="local"
        )
    )
    events: list[str] = []

    def study():
        events.append("study")
        (runner.workspace / "study.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(ok=True, error=None, total_tokens={"in": 2, "out": 3})

    monkeypatch.setattr(runner, "phase_study", study)
    monkeypatch.setattr(
        runner,
        "phase_design",
        lambda: events.append("design"),
    )
    result = runner.run(stop_after="study")
    assert result.ok is True
    assert result.study_ok is True
    assert events == ["study"]


def test_supplied_capability_only_can_generate_but_validation_is_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>", encoding="utf-8")
    supplied = tmp_path / "design.json"
    supplied_design = {
        "robot_configuration_id": "fixture",
        "capabilities": [{"capability_id": "cap", "method_name": "move"}],
    }
    supplied.write_text(json.dumps(supplied_design), encoding="utf-8")
    module = types.ModuleType("auto_adapter.capability_design")
    module.generate_capability_design = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("supplied capability design must not call TGCD")
    )
    module.load_capability_design_file = (
        lambda path, *, expected_robot_id=None: supplied_design
    )
    module.task_library_for_robot = lambda robot_id: tmp_path
    monkeypatch.setitem(sys.modules, "auto_adapter.capability_design", module)

    runner = SelfAssemble(
        SelfAssembleConfig(
            "fixture",
            scene,
            tmp_path / "runs",
            capability_design_path=supplied,
        )
    )
    (runner.workspace / "study.json").write_text(
        json.dumps({"robot_id": "fixture"}), encoding="utf-8"
    )
    design = runner._phase_design()
    assert design.ok is True
    assert runner.capability_design == supplied_design
    assert runner.scene_paths == {}
    assert runner._phase_validate().metadata["repairable"] is False

    monkeypatch.setattr(
        runner,
        "_run_phase",
        lambda **kwargs: PhaseResult(kwargs["name"], True, 0.0),
    )
    generated = runner._phase_generate()
    assert generated.ok is True


def test_feedback_formatter_exposes_measurements_without_private_paths():
    feedback = validate_failure_feedback(
        {
            "tests": [
                {
                    "capability_id": "cap",
                    "method_name": "move",
                    "ok": False,
                    "detail": "measurement failed at /private/tmp/run/video.mp4",
                    "metrics": {
                        "measurements": [
                            {"metric": "tool_error", "value": 0.3, "ok": False}
                        ]
                    },
                }
            ]
        }
    )
    assert "tool_error" in feedback
    assert "/private/tmp/run" not in feedback


@pytest.mark.parametrize("scratch", [False, True])
def test_public_run_reloads_supplied_design_and_cases_inside_workspace(tmp_path, monkeypatch, scratch):
    """Only STUDY is a fixture; DESIGN uses the real loader and scene builder."""
    from auto_adapter import capability_design

    scene = tmp_path / "scene.xml"
    scene.write_text('<mujoco><worldbody><body><joint name="hinge"/>'
                     '<geom type="sphere" size="0.01"/></body></worldbody></mujoco>')
    workspace = tmp_path / "runs" / "fixture"
    design_dir = workspace / "design"
    design_dir.mkdir(parents=True)
    design_path = design_dir / "capability_design.json"
    cases_path = design_dir / "scene_cases.yaml"
    design = {
        "robot_configuration_id": "fixture",
        "capabilities": [{
            "capability_id": "hold", "method_name": "hold",
            "request_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "criteria": [{"metric": "joint_drift", "unit": "rad", "comparator": "<=",
                          "threshold": 0.1, "temporal": {"kind": "terminal"},
                          "aggregation": {"kind": "last"}}],
        }],
    }
    cases = {"scenes": {"base": {"objects": []}}, "cases": [{
        "case_id": "hold_case", "scene": "base", "capability_id": "hold",
        "request": {}, "initial_state": {},
        "execution": {"max_sim_time_s": 0.1, "wall_timeout_s": 1.0},
        "measurements": [{"criterion_index": 0, "operator": "joint_drift",
                          "bindings": {"joint": "hinge"}}],
    }]}
    design_path.write_text(json.dumps(design))
    cases_path.write_text(json.dumps(cases))  # JSON is also valid YAML.
    original_design, original_cases = design_path.read_bytes(), cases_path.read_bytes()
    stale_driver = workspace / ("driver_from_scratch.py" if scratch else "driver.py")
    stale_driver.write_text("# stale generated output")
    config_cls = FromScratchConfig if scratch else SelfAssembleConfig
    runner_cls = FromScratchOrchestrator if scratch else SelfAssemble
    runner = runner_cls(config_cls("fixture", scene, tmp_path / "runs",
                                  capability_design_path=design_path, scene_cases_path=cases_path))

    def study():
        (workspace / "study.json").write_text('{"robot_id": "fixture"}')
        return (SimpleNamespace(ok=True, error=None, total_tokens={}) if scratch
                else PhaseResult("01_study", True, 0.0))

    def unexpected_design_model(**_kwargs):
        raise AssertionError("supplied DESIGN must not call the model")

    monkeypatch.setattr(runner, "phase_study" if scratch else "_phase_study", study)
    monkeypatch.setattr(capability_design, "generate_capability_design", unexpected_design_model)
    result = runner.run(stop_after="design")

    assert result.ok, result.to_json()
    assert runner.capability_design == design
    assert design_path.read_bytes() == original_design
    assert cases_path.read_bytes() == original_cases
    assert runner.scene_cases_path == cases_path
    assert runner.scene_paths["base"].is_file()
    assert not stale_driver.exists()
