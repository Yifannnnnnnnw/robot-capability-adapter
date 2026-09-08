from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoadapter2.driver_synthesis import generation
from autoadapter2.model_api import ModelInvocationError
from autoadapter2.pipeline import (
    ExperimentConfig,
    PipelineHooks,
    _failure_record,
    _pre_driver_failure_cell,
    _run_study_phase,
)
from autoadapter2.react import ToolCall, ToolSpec, ToolTurn


class _ScriptedClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.turn = 0
        self.cause = ValueError("upstream transport")

    def generate_tool_turn(self, **kwargs: Any) -> ToolTurn:
        self.turn += 1
        self.calls.append(dict(kwargs))
        if self.turn == 1:
            return ToolTurn(
                content=None,
                tool_calls=(
                    ToolCall(
                        id="probe-1",
                        name="execute_python",
                        arguments={"code": "print('probe')"},
                        raw_arguments='{"code":"print(\'probe\')"}',
                    ),
                ),
            )
        raise ModelInvocationError("model service unavailable") from self.cause


class _Session:
    def __init__(self, *, workspace: str | Path, **_: Any) -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.candidate_path = self.workspace / "driver.py"
        self.probe_results: list[dict[str, Any]] = []
        self.probe_requests: list[dict[str, str]] = []
        self.closed = False

    def artifact_tools(self, *, include_skeleton: bool) -> tuple[ToolSpec, ...]:
        del include_skeleton
        return (
            ToolSpec(
                name="execute_python",
                description="Run one bounded public probe.",
                input_schema={"type": "object"},
                handler=self.execute_python,
            ),
            ToolSpec(
                name="write_file",
                description="Write the study artifact.",
                input_schema={"type": "object"},
                handler=self.write_file,
            ),
        )

    def execute_python(self, arguments: dict[str, Any]) -> dict[str, Any]:
        result = {
            "probe_id": "execute-python-1",
            "successful": True,
            "exit_code": 0,
            "timed_out": False,
            "spawn_error": None,
            "physics_steps": 1,
            "stdout": "probe",
            "code": arguments["code"],
        }
        self.probe_requests.append(
            {"probe_id": result["probe_id"], "script": str(arguments["code"])}
        )
        self.probe_results.append(result)
        return result

    def write_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self.workspace / str(arguments["path"])
        path.write_text(str(arguments["content"]), encoding="utf-8")
        return {"path": str(arguments["path"]), "bytes_written": path.stat().st_size}

    def has_successful_physics_probe(self) -> bool:
        return True

    def close(self) -> None:
        self.closed = True


def test_interrupted_study_retains_probe_conversation_and_pre_driver_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generation, "PublicDevelopmentSession", _Session)
    monkeypatch.setattr(generation, "_build_study_inputs", lambda *args, **kwargs: {})

    package = SimpleNamespace(
        robot_configuration_id="robot-a",
        package_version="1.0.0",
        snapshot_id="tasks-v1",
        morphology={},
        tasks=(),
    )
    config = ExperimentConfig.from_mapping(
        {
            "experiment_id": "interrupted-study",
            "robots": ["robot-a"],
            "generation_conditions": ["from-scratch"],
            "task_demo": {"enabled": False},
            "evolution": {"enabled": False},
        }
    )
    client = _ScriptedClient()
    stage_log: list[dict[str, Any]] = []
    workspace = tmp_path / "cell"
    hooks = PipelineHooks(study_runner=generation.study)

    with pytest.raises(ModelInvocationError) as raised:
        _run_study_phase(
            package=package,
            robot="robot-a",
            condition="from-scratch",
            config=config,
            client=client,
            experience=(),
            workspace=workspace,
            hooks=hooks,
            stage_log=stage_log,
        )

    error = raised.value
    assert type(error) is ModelInvocationError
    assert error.__cause__ is client.cause
    assert error.model_turns == 2
    assert error.tool_calls == 1
    assert error.react_trace[-1]["event"] == "model_call_failed"
    assert any(message.get("role") == "tool" for message in error.react_messages)
    assert error.probe_results[0]["physics_steps"] == 1

    study_evidence_path = workspace / "study_evidence.json"
    study_evidence = json.loads(study_evidence_path.read_text(encoding="utf-8"))
    assert study_evidence["completed"] is False
    assert study_evidence["error_type"] == "ModelInvocationError"
    assert study_evidence["evidence"]["error"]["type"] == "ModelInvocationError"
    assert study_evidence["react_trace"][-1]["event"] == "model_call_failed"
    assert any(
        message.get("role") == "tool"
        for message in study_evidence["react_messages"]
    )
    assert study_evidence["probe_results"][0]["physics_steps"] == 1

    cell_report = _pre_driver_failure_cell(
        package=package,
        robot="robot-a",
        condition="from-scratch",
        config=config,
        identity={"provider": "test", "model": "scripted"},
        experience=(),
        workspace=workspace,
        failed_stage="study",
        failure_detail=_failure_record(error),
        model_stage_log=stage_log,
        completed_study=None,
        completed_probe_results=(),
        design=None,
        hooks=hooks,
        evolution_client=None,
        evolution_enabled=False,
    )
    assert cell_report["capability_validation_executed"] is False
    assert cell_report["development_probe"]["attempted"] is True
    assert cell_report["development_probe"]["successful_physics_probe"] is True
    assert cell_report["development_probe"]["results"][0]["physics_steps"] == 1
    assert json.loads((workspace / "cell_report.json").read_text(encoding="utf-8"))[
        "capability_validation_executed"
    ] is False
