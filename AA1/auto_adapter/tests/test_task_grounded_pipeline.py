"""Focused checks for the bounded task-grounded AA1 handoff."""
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
)
from auto_adapter.orchestrator_from_scratch import (
    FromScratchConfig,
    FromScratchOrchestrator,
)


def _piper_scene() -> Path:
    return Path(__file__).resolve().parents[2] / "assets/mjcf/piper/scene.xml"


def _fake_preparation(monkeypatch, calls, design=None):
    design = design or {
        "artifact_type": "capability_design",
        "robot_configuration_id": "piper",
        "capabilities": [{"capability_id": "A1", "method_name": "reach"}],
    }

    def generate_capability_design(**kwargs):
        calls.append(kwargs)
        output = Path(kwargs["output_dir"])
        output.mkdir(parents=True, exist_ok=True)
        (output / "capability_design.json").write_text(
            json.dumps(design) + "\n", encoding="utf-8"
        )
        (output / "capability_preparation.json").write_text(
            json.dumps({
                "token_usage": {"in": 3, "out": 5},
                "duration_sec": 0.25,
                "trace_path": str(output / "trace.jsonl"),
                "error": None,
            }) + "\n",
            encoding="utf-8",
        )
        return design

    def load_capability_design_file(path, *, expected_robot_id=None):
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if expected_robot_id is not None:
            assert value.get("robot_configuration_id") == expected_robot_id
        return value

    module = types.ModuleType("auto_adapter.capability_preparation")
    module.generate_capability_design = generate_capability_design
    module.load_capability_design_file = load_capability_design_file
    module.task_library_for_robot = lambda robot_id: Path("task-library")
    monkeypatch.setitem(sys.modules, "auto_adapter.capability_preparation", module)
    return design


def test_config_options_are_mutually_exclusive_and_local_only(tmp_path):
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    with pytest.raises(ValueError, match="mutually exclusive"):
        SelfAssembleConfig(
            "fixture", scene, tmp_path / "runs",
            prepare_capabilities=True, capability_design_path=scene,
        )
    with pytest.raises(ValueError, match="local-only"):
        SelfAssembleConfig(
            "fixture", scene, tmp_path / "runs",
            mode="dgx", prepare_capabilities=True,
        )
    with pytest.raises(ValueError, match="local-only"):
        FromScratchConfig(
            "fixture", scene, tmp_path / "runs",
            mode="agentcore", capability_design_path=scene,
        )
    with pytest.raises(ValueError, match="between one and six"):
        SelfAssembleConfig(
            "fixture", scene, tmp_path / "runs",
            prepare_capabilities=True, max_iters_capability_design=7,
        )


def test_dynamic_constructor_preserves_actual_mjcf_and_default_keeps_catalog_path(tmp_path):
    actual = _piper_scene()
    cfg = SelfAssembleConfig(
        "piper", actual, tmp_path / "dynamic", prepare_capabilities=True,
    )
    runner = SelfAssemble(cfg)
    assert cfg.mjcf_path == actual
    assert runner.workspace.joinpath("mjcf.xml").resolve() == actual.resolve()
    assert runner.capability_design is None

    legacy_cfg = SelfAssembleConfig("piper", actual, tmp_path / "legacy")
    legacy = SelfAssemble(legacy_cfg)
    assert legacy.capability_design is not None
    assert "capabilities" in str(legacy_cfg.mjcf_path)


def test_study_handoff_runs_one_design_and_passes_memory_design_to_generate(
    tmp_path, monkeypatch
):
    calls = []
    design = _fake_preparation(monkeypatch, calls)
    runner = SelfAssemble(SelfAssembleConfig(
        "piper", _piper_scene(), tmp_path / "runs", prepare_capabilities=True,
    ))

    def fake_run_phase(**kwargs):
        (runner.workspace / "study.json").write_text(
            json.dumps({"robot_id": "piper", "estimated_class": "arm"}) + "\n"
        )
        return PhaseResult(kwargs["name"], True, 1.0)

    monkeypatch.setattr(runner, "_run_phase", fake_run_phase)
    study_result = runner._phase_study()

    assert study_result.ok is True
    assert runner.capability_design == design
    assert len(calls) == 1
    assert calls[0]["output_dir"] == runner.workspace / "capability_inputs"
    assert calls[0]["skeleton_context"]
    assert study_result.metadata["capability_design_path"].endswith(
        "capability_inputs/capability_design.json"
    )
    assert study_result.metadata["capability_design_token_usage"] == {"in": 3, "out": 5}
    assert study_result.token_usage == {"in": 3, "out": 5}
    assert study_result.duration_sec == pytest.approx(1.25)

    captured = {}

    def fake_generate_phase(**kwargs):
        captured.update(kwargs)
        return PhaseResult(kwargs["name"], True, 0.0)

    monkeypatch.setattr(runner, "_run_phase", fake_generate_phase)
    runner._phase_generate()
    assert '"method_name": "reach"' in captured["user_msg"]
    assert "move_end_effector_to_position" not in captured["user_msg"]


def test_scratch_study_handoff_uses_no_skeleton_context(tmp_path, monkeypatch):
    calls = []
    _fake_preparation(monkeypatch, calls)
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    runner = FromScratchOrchestrator(FromScratchConfig(
        "fixture", scene, tmp_path / "runs", mode="local",
        prepare_capabilities=True,
    ))

    def fake_loop(**kwargs):
        (runner.workspace / "study.json").write_text(
            json.dumps({"robot_id": "fixture", "estimated_class": "arm"}) + "\n"
        )
        return SimpleNamespace(
            ok=True, error=None, total_tokens={"in": 1, "out": 2},
            capability_preparation=None,
        )

    monkeypatch.setattr(runner, "_run_loop", fake_loop)
    result = runner.phase_study()
    assert result.ok is True
    assert runner.capability_design is not None
    assert calls[0]["skeleton_context"] is None


def test_supplied_design_is_loaded_with_robot_id_and_criteria_preserved(tmp_path, monkeypatch):
    calls = []
    design = {
        "artifact_type": "capability_design",
        "robot_configuration_id": "piper",
        "capabilities": [{
            "capability_id": "A1", "method_name": "reach",
            "criteria": [{"metric": "position_error", "threshold": 0.1}],
        }],
    }
    supplied = tmp_path / "capability_design.json"
    supplied.write_text(json.dumps(design) + "\n", encoding="utf-8")
    _fake_preparation(monkeypatch, calls, design=design)
    runner = SelfAssemble(SelfAssembleConfig(
        "piper", _piper_scene(), tmp_path / "runs",
        capability_design_path=supplied,
    ))

    def fake_run_phase(**kwargs):
        (runner.workspace / "study.json").write_text(
            json.dumps({"robot_id": "piper", "estimated_class": "arm"}) + "\n"
        )
        return PhaseResult(kwargs["name"], True, 0.0)

    monkeypatch.setattr(runner, "_run_phase", fake_run_phase)
    result = runner._phase_study()
    assert result.ok is True
    assert calls == []
    assert runner.capability_design["capabilities"][0]["criteria"] == design[
        "capabilities"
    ][0]["criteria"]


@pytest.mark.parametrize("route", ["standard", "scratch"])
def test_repeated_dynamic_study_clears_previous_design(route, tmp_path, monkeypatch):
    calls = []
    _fake_preparation(monkeypatch, calls, design={
        "artifact_type": "capability_design",
        "robot_configuration_id": "fixture",
        "capabilities": [{"capability_id": "A1", "method_name": "reach"}],
    })
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    if route == "standard":
        runner = SelfAssemble(SelfAssembleConfig(
            "fixture", scene, tmp_path / "standard", prepare_capabilities=True,
        ))
        outcomes = iter([True, False])

        def fake_phase(**kwargs):
            ok = next(outcomes)
            if ok:
                (runner.workspace / "study.json").write_text(
                    json.dumps({"robot_id": "fixture"}) + "\n"
                )
            return PhaseResult(kwargs["name"], ok, 0.0)

        monkeypatch.setattr(runner, "_run_phase", fake_phase)
        runner._phase_study()
        assert runner.capability_design is not None
        runner._phase_study()
        with pytest.raises(RuntimeError, match="catalog fallback is disabled"):
            runner._phase_generate()
    else:
        runner = FromScratchOrchestrator(FromScratchConfig(
            "fixture", scene, tmp_path / "scratch", mode="local",
            prepare_capabilities=True,
        ))
        outcomes = iter([True, False])

        def fake_loop(**kwargs):
            ok = next(outcomes)
            if ok:
                (runner.workspace / "study.json").write_text(
                    json.dumps({"robot_id": "fixture"}) + "\n"
                )
            return SimpleNamespace(
                ok=ok, error=None if ok else "study failed",
                total_tokens={}, trace=[],
            )

        monkeypatch.setattr(runner, "_run_loop", fake_loop)
        runner.phase_study()
        assert runner.capability_design is not None
        runner.phase_study()
        with pytest.raises(RuntimeError, match="catalog fallback is disabled"):
            runner.phase_gen_algo()


def test_invoke_error_stale_study_does_not_start_tgcd_or_generate(tmp_path, monkeypatch):
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    calls = []
    _fake_preparation(monkeypatch, calls)
    runner = SelfAssemble(SelfAssembleConfig(
        "fixture", scene, tmp_path / "runs", prepare_capabilities=True,
    ))
    (runner.workspace / "study.json").write_text('{"robot_id":"fixture"}\n')
    failed = SimpleNamespace(
        ok=False, error="model invoke failed", final_text="",
        total_tokens={"in": 1, "out": 0},
        trace=[SimpleNamespace(stop_reason="invoke_error")],
    )
    monkeypatch.setattr(
        "auto_adapter.orchestrator.ReactLoop",
        lambda **kwargs: SimpleNamespace(run=lambda _msg: failed),
    )
    result = runner.run(stop_after="generate")
    assert not calls
    assert all(phase.name != "generate" or not phase.ok for phase in result.phases)
    assert runner.capability_design is None


def test_dynamic_legacy_validation_and_full_run_are_rejected(tmp_path):
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    runner = SelfAssemble(SelfAssembleConfig(
        "fixture", scene, tmp_path / "runs", prepare_capabilities=True,
    ))
    with pytest.raises(ValueError, match="legacy Framework evaluator"):
        runner._phase_validate_framework()
    with pytest.raises(ValueError, match="stop_after='study'"):
        runner.run()

    scratch = FromScratchOrchestrator(FromScratchConfig(
        "fixture", scene, tmp_path / "scratch", mode="local",
        prepare_capabilities=True,
    ))
    with pytest.raises(ValueError, match=r"phase_study\(\).*phase_gen_algo"):
        scratch.run()
    with pytest.raises(ValueError, match="legacy scratch evaluator"):
        scratch._validate_from_scratch_driver()
