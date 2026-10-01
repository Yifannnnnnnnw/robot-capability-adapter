"""Focused checks for the from-scratch local generation route."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_adapter.orchestrator_from_scratch import (
    FromScratchConfig,
    FromScratchOrchestrator,
)


def _runner(tmp_path: Path, *, mode: str = "local") -> FromScratchOrchestrator:
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    return FromScratchOrchestrator(
        FromScratchConfig(
            "fixture", scene, tmp_path / "runs", mode=mode, bedrock_model="test-model"
        )
    )


def _capture_loop(runner: FromScratchOrchestrator, monkeypatch):
    captured = []

    def fake_run_loop(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(total_tokens={"in": 0, "out": 0}, error=None)

    monkeypatch.setattr(runner, "_run_loop", fake_run_loop)
    return captured


def test_local_context_does_not_create_agentcore_session(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    source = tmp_path / "scene.xml"

    assert runner.capability_design is None
    assert runner.cfg.mjcf_path == source
    assert (runner.workspace / "mjcf.xml").resolve() == source.resolve()

    def fail_import(name, *args, **kwargs):
        if name == "boto3" or name.startswith("botocore"):
            raise AssertionError("local from-scratch mode imported AgentCore")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setattr("builtins.__import__", fail_import)
    assert runner.__enter__() is runner


def test_local_study_and_generate_only_expose_workspace_tools(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    captured = _capture_loop(runner, monkeypatch)

    runner.phase_study()
    runner.capability_design = {
        "capabilities": [{"method_name": "move", "capability_id": "move"}]
    }
    runner.phase_gen_algo()

    assert len(captured) == 2
    for phase in captured:
        names = [tool.name for tool in phase["tools"]]
        assert "write_file" in names
        assert "read_file" in names
        assert names.count("local_exec") == 1
        assert "execute_python" not in names
        assert "execute_python" not in phase["system"]
        assert "fresh process" in phase["system"]
        assert "workspace" in phase["system"]
    assert '"method_name": "move"' in captured[1]["user_msg"]


def test_local_repair_uses_same_route_and_capability_override(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    runner.capability_design = {"capabilities": [{"method_name": "drive"}]}
    captured = _capture_loop(runner, monkeypatch)

    runner.phase_gen_repair("failure feedback", 1)

    phase = captured[0]
    assert [tool.name for tool in phase["tools"]].count("local_exec") == 1
    assert "execute_python" not in {tool.name for tool in phase["tools"]}
    assert "execute_python" not in phase["system"]
    assert "complete method(request) interface" in phase["system"]
    assert '"method_name": "drive"' in phase["user_msg"]


def test_from_scratch_config_keeps_historical_positional_model_argument(tmp_path):
    cfg = FromScratchConfig("fixture", tmp_path / "scene.xml", tmp_path, "legacy-model")

    assert cfg.bedrock_model == "legacy-model"
    assert cfg.mode == "local"


def test_nonlocal_mode_is_rejected_at_config_boundary(tmp_path):
    with pytest.raises(ValueError, match="requires mode='local'"):
        FromScratchConfig(
            "fixture", tmp_path / "scene.xml", tmp_path, mode="agentcore"
        )


def test_default_run_enters_design_after_current_study(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    events = []

    def study():
        events.append("study")
        (runner.workspace / "study.json").write_text(
            '{"robot_id": "fixture"}', encoding="utf-8"
        )
        return SimpleNamespace(ok=True, error=None, total_tokens={})

    def design():
        events.append("design")
        runner.capability_design = {
            "capabilities": [{"capability_id": "move", "method_name": "move"}]
        }
        output = runner.workspace / "design"
        output.mkdir(exist_ok=True)
        (output / "capability_design.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(ok=True, error=None, total_tokens={})

    monkeypatch.setattr(runner, "phase_study", study)
    monkeypatch.setattr(runner, "phase_design", design)

    result = runner.run(stop_after="design")

    assert result.ok is True
    assert result.study_ok is True
    assert result.design_ok is True
    assert events == ["study", "design"]
    assert not hasattr(runner.cfg, "prepare_capabilities")
    assert result.to_json()["design_ok"] is True
    assert json.loads(json.dumps(result.to_json()))["stage1_ok"] is False
    assert (runner.workspace / "summary.json").is_file()
    assert (runner.workspace / "narrative.md").is_file()
