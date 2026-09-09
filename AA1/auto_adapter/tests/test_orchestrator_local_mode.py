"""Focused regressions for local STUDY/GENERATE tool routing."""
from __future__ import annotations

from pathlib import Path

from auto_adapter.agent import ToolSpec
from auto_adapter.orchestrator import PhaseResult, SelfAssemble, SelfAssembleConfig


def _runner(tmp_path: Path, mode: str = "local") -> SelfAssemble:
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    return SelfAssemble(
        SelfAssembleConfig("fixture", scene, tmp_path / "runs", mode=mode)
    )


def _capture_phase(runner: SelfAssemble, monkeypatch):
    captured = {}

    def fake_run_phase(**kwargs):
        captured.update(kwargs)
        return PhaseResult(name=kwargs["name"], ok=True, duration_sec=0.0)

    monkeypatch.setattr(runner, "_run_phase", fake_run_phase)
    return captured


def test_local_study_uses_workspace_local_exec_for_mujoco(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    captured = _capture_phase(runner, monkeypatch)

    runner._phase_study()

    names = [tool.name for tool in captured["tools"]]
    assert "write_file" in names
    assert "local_exec" in names
    assert "execute_python" not in names
    assert "execute_python" not in captured["system"]
    assert "fresh process" in captured["system"]

    write_file = next(tool for tool in captured["tools"] if tool.name == "write_file")
    write_file.handler({"path": "study.json", "content": "{}"})
    assert (runner.workspace / "study.json").read_text() == "{}"


def test_local_generate_routes_probe_to_workspace_local_exec(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    # A stale remote handle must not become part of a local tool bundle.
    runner._exec_python_tool = ToolSpec(
        name="execute_python",
        description="remote fixture",
        input_schema={"type": "object"},
        handler=lambda _input: (_ for _ in ()).throw(
            AssertionError("remote execute_python was routed in local mode")
        ),
    )
    captured = _capture_phase(runner, monkeypatch)

    runner._phase_generate()

    names = [tool.name for tool in captured["tools"]]
    assert names.count("local_exec") == 1
    assert "execute_python" not in names
    assert "write_file" in names
    assert "local_exec" in captured["system"]
