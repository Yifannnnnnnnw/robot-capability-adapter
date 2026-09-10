"""Focused checks for the from-scratch local generation route."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from auto_adapter.agent import ToolSpec
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

    def fail_import(name, *args, **kwargs):
        if name == "boto3" or name.startswith("botocore"):
            raise AssertionError("local from-scratch mode imported AgentCore")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setattr("builtins.__import__", fail_import)
    assert runner.__enter__() is runner
    assert runner._exec_python_tool is None


def test_local_study_and_generate_only_expose_workspace_tools(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    # A stale remote handle must not leak into an explicitly local phase.
    runner._exec_python_tool = ToolSpec(
        name="execute_python",
        description="remote test double",
        input_schema={"type": "object"},
        handler=lambda _input: (_ for _ in ()).throw(
            AssertionError("remote execute_python was routed in local mode")
        ),
    )
    captured = _capture_loop(runner, monkeypatch)

    runner.phase_study()
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


def test_local_repair_uses_same_route_and_capability_override(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    runner.capability_design = {"capabilities": [{"method_name": "drive"}]}
    runner._exec_python_tool = ToolSpec(
        name="execute_python",
        description="remote test double",
        input_schema={"type": "object"},
        handler=lambda _input: None,
    )
    captured = _capture_loop(runner, monkeypatch)

    runner.phase_gen_repair("failure feedback", 1)

    phase = captured[0]
    assert [tool.name for tool in phase["tools"]].count("local_exec") == 1
    assert "execute_python" not in {tool.name for tool in phase["tools"]}
    assert "execute_python" not in phase["system"]
    assert "complete method(request) interface" in phase["system"]


def test_from_scratch_config_keeps_historical_positional_model_argument(tmp_path):
    cfg = FromScratchConfig("fixture", tmp_path / "scene.xml", tmp_path, "legacy-model")

    assert cfg.bedrock_model == "legacy-model"
    assert cfg.mode == "agentcore"


def test_h1_required_evidence_unavailable_cannot_pass(tmp_path, monkeypatch):
    from auto_adapter import robot_catalog
    from auto_adapter import orchestrator_from_scratch as module

    class FakeTrace:
        def __init__(self, _robot, _state_refs):
            self.samples = [
                {"time": 0.0, "base_xyz": [0.0, 0.0, 1.0],
                 "base_upright": 1.0, "finite": True},
                {"time": 2.0, "base_xyz": [0.0, 0.0, 1.0],
                 "base_upright": 1.0, "finite": True},
            ]
            self.tool = "initial"
            self.idx = -1

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

    class FakeVideo:
        def __init__(self, _robot, _path, **_kwargs):
            pass

        def capture(self, **_kwargs):
            pass

        def finish(self):
            return {"ok": False, "frame_count": 0,
                    "errors": ["renderer unavailable"]}

    class FakeRobot:
        def stand_balance(self, *, secs):
            assert secs == 2.0

    monkeypatch.setattr(
        robot_catalog,
        "find_robot_definition",
        lambda *_args: {"state_refs": {"base_body": "pelvis"}},
    )
    monkeypatch.setattr("autoadapter_bench.physics.PhysicsTrace", FakeTrace)
    monkeypatch.setattr("autoadapter_bench.capability_eval._VideoRecorder", FakeVideo)

    result = module._validate_humanoid_stand_balance(
        FakeRobot(), "h1", tmp_path / "scene.xml", secs=2.0,
        trace_path=tmp_path / "stand.json", video_path=tmp_path / "stand.mp4",
    )

    assert result["ok"] is False
    assert "required stand_balance video unavailable" in result["detail"]
