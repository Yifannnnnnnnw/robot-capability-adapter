"""Focused MCP protocol checks for the AA1 task boundary."""
from __future__ import annotations

import json
from pathlib import Path

import pytest



@pytest.fixture
def exported_mcp_fixture(tmp_path):
    """Named real FastMCP stdio fixture with explicit robot/model fixtures."""

    source = tmp_path / "generation"
    source.mkdir()
    scene = source / "scene.xml"
    scene.write_text("<mujoco><option timestep=\"0.01\"/><worldbody/></mujoco>")
    driver = source / "driver.py"
    driver.write_text("""\n# The protocol fixture owns the observable state; run_task must not import this.\ndef build():\n    raise AssertionError('task_execution must use exported MCP')\n""")
    server = source / "mcp_server.py"
    server.write_text("""
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from typing import Annotated
from typing_extensions import TypedDict
from pydantic import ConfigDict, Field, with_config
from mcp.server.fastmcp import FastMCP

state = {"sim_time_s": 0.0, "calls": 0}

@asynccontextmanager
async def lifespan(_server):
    yield
    config = json.loads(Path(os.environ["AA1_TASK_RUNTIME_CONFIG"]).read_text())
    output_dir = Path(config["output_dir"])
    video = output_dir / "fixture.mp4"
    video.write_bytes(b"MCP fixture video")
    (output_dir / "runtime_report.json").write_text(json.dumps({
        "sim_time_start": 0.0,
        "sim_time_end": state["sim_time_s"],
        "n_frames": state["calls"] + 1,
        "video_path": str(video),
        "errors": [],
        "error": "fixture close failure" if os.environ.get("AA1_FIXTURE_RUNTIME_ERROR") else None,
    }) + "\\n")

mcp = FastMCP("task-execution-fixture", lifespan=lifespan)

@mcp.resource("robot://state")
def robot_state() -> dict:
    if os.environ.get("AA1_FIXTURE_FAIL_STATE") or (
            os.environ.get("AA1_FIXTURE_FAIL_STATE_AFTER_CALL") and state["calls"] > 0):
        raise RuntimeError("fixture state failure")
    return dict(state)

@with_config(ConfigDict(strict=True, extra="forbid"))
class AdvanceRequest(TypedDict):
    steps: Annotated[int, Field(ge=1, le=10)]

@mcp.tool()
def advance(request: AdvanceRequest) -> dict:
    state["calls"] += 1
    state["sim_time_s"] += request["steps"] * 0.01
    return {"calls": state["calls"], "steps": request["steps"]}

@mcp.tool()
def error_action(request: AdvanceRequest) -> dict:
    raise RuntimeError("fixture failure")

if __name__ == "__main__":
    mcp.run()
""")
    design = {
        "robot_configuration_id": "fixture",
        "capabilities": [{
            "capability_id": "design_only",
            "method_name": "design_only",
            "description": "Design metadata is not the runtime catalog.",
            "request_schema": {
                "type": "object", "properties": {}, "required": [],
                "additionalProperties": False,
            },
        }],
    }
    return {
        "driver_path": driver,
        "export_server_path": server,
        "robot_id": "fixture",
        "capability_design": design,
        "validation_suite": {"cases": []},
        "validation_report": {"tests": []},
        "task_description": "Run the advertised actions.",
        "scene_path": scene,
        "initial_state": {},
        "parameters": {},
        "required_capabilities": [],
        "model": "fixture-model",
        "output_dir": tmp_path / "task",
    }


class AdvertisedNamesModel:
    def __init__(self, plans):
        self.plans = list(plans)
        self.turn = 0

    def generate_json(self, *, messages):
        assert "PRIVATE_SENTINEL_DO_NOT_PLAN_WITH" not in json.dumps(messages)
        args = {"think": "Use the advertised MCP action.",
                "subtasks": self.plans[self.turn]}
        self.turn += 1
        return json.dumps(args)


def _call(name, steps):
    return json.dumps({"capability_name": name, "request": {"steps": steps}})


def test_actual_advertised_names_and_persistent_calls(exported_mcp_fixture):
    from auto_adapter.agent.task_execution import run_task

    inputs = dict(exported_mcp_fixture)
    model = AdvertisedNamesModel([
        ["First action", "Second action"],
        [_call("advance", 2)],
        [_call("advance", 3)],
        [],
    ])
    original_driver = inputs["driver_path"].read_bytes()
    original_server = inputs["export_server_path"].read_bytes()
    report = run_task(**inputs, model_client=model)

    assert report["ok"], report
    assert report["status"] == "CONTROLLER_FINISHED"
    assert report["capability_whitelist"] == ["advance", "error_action"]
    assert [call["arguments"] for call in report["tool_call_log"]] == [
        {"request": {"steps": 2}}, {"request": {"steps": 3}}
    ]
    assert [call["return_value"]["calls"] for call in report["tool_call_log"]] == [1, 2]
    assert all(call["mcp_result"]["isError"] is False for call in report["tool_call_log"])
    assert report["initial_observation"]["sim_time_s"] == 0.0
    assert report["sim_time_end"] == pytest.approx(0.05)
    assert report["controller"] == "auto_adapter.agent.vendor.recap.chatbot.chatbot"
    assert report["controller_source_commit"] == "2fb112ffad685c7c6f7de86d5487ecca6f566fcc"
    assert report["physical_task_success"] is None
    assert report["n_frames"] > 1
    assert Path(report["video_path"]).is_file()
    assert Path(report["mcp_tools_path"]).is_file()
    advertised = json.loads(Path(report["mcp_tools_path"]).read_text())
    assert {tool["name"] for tool in advertised["tools"]} == {"advance", "error_action"}
    assert Path(report["model_messages_path"]).is_file()
    assert all("messages" in json.loads(line)
               for line in Path(report["model_messages_path"]).read_text().splitlines())
    assert inputs["driver_path"].read_bytes() == original_driver
    assert inputs["export_server_path"].read_bytes() == original_server

    second = dict(inputs, output_dir=inputs["output_dir"].with_name("second-task"),
                  model_client=AdvertisedNamesModel([[_call("advance", 1)], []]))
    second_report = run_task(**second)
    assert second_report["ok"], second_report
    assert second_report["initial_observation"]["sim_time_s"] == 0.0
    assert second_report["sim_time_end"] == pytest.approx(0.01)


def test_mcp_error_is_recorded_and_replanning_can_recover(exported_mcp_fixture):
    from auto_adapter.agent.task_execution import run_task

    inputs = dict(exported_mcp_fixture, output_dir=exported_mcp_fixture["output_dir"].with_name("error-task"))
    model = AdvertisedNamesModel([
        ["First action", "Second action"],
        [_call("error_action", 1)],
        [_call("advance", 2)],
        [],
    ])
    report = run_task(**inputs, model_client=model)

    assert report["status"] == "CONTROLLER_FINISHED"
    assert report["ok"], report
    assert report["tool_call_log"][0]["status"] == "ERROR"
    assert report["tool_call_log"][0]["isError"] is True
    assert report["tool_call_log"][0]["mcp_result"]["isError"] is True
    assert report["tool_call_log"][1]["status"] == "EXECUTED"


def test_missing_export_is_explicit_unavailable(tmp_path, exported_mcp_fixture):
    from auto_adapter.agent.task_execution import run_task

    inputs = dict(exported_mcp_fixture,
                  export_server_path=tmp_path / "missing-mcp_server.py",
                  output_dir=tmp_path / "missing-export")
    report = run_task(**inputs, model_client=AdvertisedNamesModel([]))

    assert report["ok"] is False
    assert report["status"] == "UNAVAILABLE"
    assert "exported MCP server is unavailable" in report["error"]
    assert report["tool_call_log"] == []
    assert Path(report["report_path"]).is_file()


def test_initial_state_resource_failure_stops_before_model(monkeypatch, exported_mcp_fixture):
    from auto_adapter.agent.task_execution import run_task

    monkeypatch.setenv("AA1_FIXTURE_FAIL_STATE", "1")
    inputs = dict(exported_mcp_fixture,
                  output_dir=exported_mcp_fixture["output_dir"].with_name("state-error-task"))
    model = AdvertisedNamesModel([[_call("advance", 1)], []])
    report = run_task(**inputs, model_client=model)

    assert report["status"] == "UNAVAILABLE"
    assert report["tool_call_log"] == []
    assert model.turn == 0
    assert "initial robot://state observation is unavailable" in report["error"]


def test_post_call_state_failure_aborts_worker(monkeypatch, exported_mcp_fixture):
    from auto_adapter.agent.task_execution import run_task

    monkeypatch.setenv("AA1_FIXTURE_FAIL_STATE_AFTER_CALL", "1")
    inputs = dict(exported_mcp_fixture,
                  output_dir=exported_mcp_fixture["output_dir"].with_name("post-call-state-error"))
    report = run_task(**inputs, model_client=AdvertisedNamesModel([[_call("advance", 1)]]))

    assert report["status"] == "WORKER_ABORTED"
    assert report["ok"] is False
    assert report["tool_call_log"][0]["status"] == "WORKER_ABORTED"
    assert report["tool_call_log"][0]["isError"] is False
    assert report["tool_call_log"][0]["mcp_result"]["isError"] is False


def test_runtime_close_error_rejects_report_even_with_video(monkeypatch, exported_mcp_fixture):
    from auto_adapter.agent.task_execution import run_task

    monkeypatch.setenv("AA1_FIXTURE_RUNTIME_ERROR", "1")
    inputs = dict(exported_mcp_fixture,
                  output_dir=exported_mcp_fixture["output_dir"].with_name("close-error-task"))
    report = run_task(**inputs, model_client=AdvertisedNamesModel([[_call("advance", 1)]]))

    assert report["status"] == "CONTROLLER_FINISHED"
    assert report["video_path"]
    assert report["sim_advanced"]
    assert report["runtime_report"]["error"] == "fixture close failure"
    assert report["ok"] is False
