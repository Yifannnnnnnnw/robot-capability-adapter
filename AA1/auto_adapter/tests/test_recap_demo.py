"""Focused checks for the AA1-to-canonical-ReCAP MCP bridge."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_adapter.agent.recap import CapabilityAdapterError, passed_design
from auto_adapter.agent.recap import AA1CapabilityAdapter, AA1RecapModel


@pytest.fixture
def protocol_mcp_server(tmp_path):
    """Real FastMCP stdio fixture with fixture robot/model, real ClientSession."""

    server = tmp_path / "mcp_server.py"
    server.write_text("""
from typing import Annotated
from typing_extensions import TypedDict, NotRequired
from pydantic import ConfigDict, Field, with_config
from mcp.server.fastmcp import FastMCP

state = {"sim_time_s": 0.0, "calls": 0}
mcp = FastMCP("recap-demo-fixture")

@mcp.resource("robot://state")
def robot_state() -> dict:
    return dict(state)

@with_config(ConfigDict(strict=True, extra="forbid"))
class RealActionRequest(TypedDict):
    steps: Annotated[int, Field(ge=1, le=10)]
    duration_s: NotRequired[Annotated[float, Field(ge=0.1)]]

@mcp.tool()
def real_action(request: RealActionRequest) -> dict:
    state["calls"] += 1
    state["sim_time_s"] += request["steps"] * 0.01
    return {"calls": state["calls"], "request": dict(request)}

if __name__ == "__main__":
    mcp.run()
""")
    return server


def test_catalog_and_calls_use_real_mcp_protocol(protocol_mcp_server):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import os
    import sys

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(protocol_mcp_server)],
            cwd=str(protocol_mcp_server.parent),
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listing = await session.list_tools()
                log = []
                adapter = AA1CapabilityAdapter(
                    session=session, loop=asyncio.get_running_loop(),
                    tools_result=listing, robot_configuration_id="fixture",
                    tool_call_log=log,
                )
                catalog = adapter.public_catalog()
                assert [item["capability_name"] for item in catalog] == ["real_action"]
                assert catalog[0]["description"] == ""
                request_schema = catalog[0]["request_schema"]
                assert request_schema["required"] == ["steps"]
                assert request_schema["properties"]["duration_s"]["minimum"] == 0.1
                assert "$ref" not in json.dumps(request_schema)

                before = await asyncio.to_thread(adapter.read_observation)
                outcome = await asyncio.to_thread(
                    adapter.execute, "real_action", {"steps": 2, "duration_s": 0.1}
                )
                after = outcome["observations"]
                assert before["sim_time_s"] == 0.0
                assert after["sim_time_s"] == pytest.approx(0.02)
                assert outcome["operation"]["return_value"]["calls"] == 1
                assert log[0]["arguments"] == {
                    "request": {"steps": 2, "duration_s": 0.1}
                }
                assert log[0]["mcp_result"]["isError"] is False

                with pytest.raises(CapabilityAdapterError, match="unknown capability"):
                    await asyncio.to_thread(adapter.execute, "design_alias", {"steps": 1})

    asyncio.run(exercise())


def test_model_bridge_preserves_official_json_history_and_records_request_response(tmp_path):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(content=[
            SimpleNamespace(type='text', text='{"think":"Finished.",'),
            SimpleNamespace(type='text', text='"subtasks":[]}')],
            usage=SimpleNamespace(input_tokens=4, output_tokens=2))
    model = object.__new__(AA1RecapModel)
    model.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    model.model, model.max_tokens, model.trace_path = 'fixture', 100, tmp_path/'trace.jsonl'
    messages = [
        {'role':'system','content':'rules'},
        {'role':'user','content':'task'},
        {'role':'assistant','content':'{"think":"Move.","subtasks":["move"]}'},
        {'role':'user','content':'observed'},
        {'role':'user','content':'revise the parent plan'}]
    response = model.generate_json(messages=messages)
    assert 'rules' in captured['system']
    assert 'brief plan summary' in captured['system']
    assert captured['messages'] == [messages[1], messages[2],
                                   {'role':'user','content':'observed\n\nrevise the parent plan'}]
    assert 'tools' not in captured
    assert json.loads(response) == {'think': 'Finished.', 'subtasks': []}
    assert json.loads(model.trace_path.read_text()) == {'messages': messages, 'response': response}
    full = json.loads((tmp_path / 'model_messages.jsonl').read_text().splitlines()[0])
    assert full['usage']['input_tokens'] == 4
    assert full['tools'] == []


def test_local_catalog_demo_dispatches_to_recap():
    from auto_adapter.orchestrator import SelfAssemble

    runner = object.__new__(SelfAssemble)
    runner.cfg = SimpleNamespace(mode="local")
    runner.capability_design = {"capabilities": ["fixture"]}
    runner._phase_recap_demo = lambda: "canonical recap"
    assert runner._phase_demo() == "canonical recap"


def test_current_design_case_gate_has_no_catalog_dependency():
    design = {"capabilities": [{"capability_id": "C"}]}
    suite = {"cases": [
        {"case_id": "nominal", "capability_id": "C"},
        {"case_id": "boundary", "capability_id": "C"},
    ]}
    with pytest.raises(ValueError, match="no fully Framework-passed"):
        passed_design(design, suite, {"tests": [{"case_id": "nominal", "ok": True}]})
    assert passed_design(design, suite, {"tests": [
        {"case_id": "nominal", "ok": True}, {"case_id": "boundary", "ok": True},
    ]}) == design
