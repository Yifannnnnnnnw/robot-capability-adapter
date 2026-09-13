"""Focused regression for the request-schema drift observed in Piper EXPORT."""

import pytest

from auto_adapter.export_support import validate_dynamic_export_source


DESIGN = {"capabilities": [{
    "method_name": "set_gripper_aperture",
    "request_schema": {
        "type": "object",
        "properties": {
            "target_opening": {"type": "number", "minimum": 0, "maximum": 0.04},
            "settle_s": {"type": "number", "minimum": 0.1},
        },
        "required": ["target_opening"],
        "additionalProperties": False,
    },
}]}


@pytest.mark.parametrize("defect", ["nullable", "unknown_fields", None])
def test_real_sdk_request_contract(tmp_path, defect):
    # Explicit fixture server: no generated driver or robot is substituted in a
    # real run. The old generated server had both of these schema defects.
    optional_type = "float | None" if defect == "nullable" else "float"
    extra = "ignore" if defect == "unknown_fields" else "forbid"
    server = tmp_path / "mcp_server.py"
    server.write_text(f'''
from typing import Annotated
from typing_extensions import TypedDict, NotRequired
from pydantic import ConfigDict, Field, with_config
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("schema-regression-fixture")
@with_config(ConfigDict(strict=True, extra={extra!r}))
class Request(TypedDict):
    target_opening: Annotated[float, Field(ge=0, le=0.04)]
    settle_s: NotRequired[Annotated[{optional_type}, Field(ge=0.1)]]

@mcp.tool()
def set_gripper_aperture(request: Request) -> dict:
    return request

@mcp.resource("robot://state")
def state() -> dict:
    return {{"sim_time_s": 0.0}}
''')
    if defect:
        with pytest.raises(ValueError, match="request schema differs"):
            validate_dynamic_export_source(server, DESIGN)
    else:
        result = validate_dynamic_export_source(server, DESIGN)
        assert result["request_schemas"] is True
        assert result["tool_names"] == ["set_gripper_aperture"]
