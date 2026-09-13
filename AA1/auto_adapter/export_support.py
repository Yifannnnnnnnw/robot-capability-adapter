# SPDX-License-Identifier: Apache-2.0
"""Small helpers shared by the dynamic EXPORT entry points."""

from __future__ import annotations

import ast
import copy
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def capability_export_specs(design: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the exact dynamic MCP surface declared by one design."""

    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("dynamic export requires a non-empty capability design")
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, capability in enumerate(capabilities):
        if not isinstance(capability, Mapping):
            raise ValueError(f"capabilities[{index}] must be an object")
        method = capability.get("method_name")
        if not isinstance(method, str) or not method.isidentifier():
            raise ValueError(f"capabilities[{index}].method_name must be an identifier")
        if method in seen:
            raise ValueError(f"duplicate dynamic export method {method!r}")
        schema = capability.get("request_schema")
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            raise ValueError(f"capabilities[{index}].request_schema must be an object schema")
        seen.add(method)
        specs.append(
            {
                "method_name": method,
                "request_schema": copy.deepcopy(dict(schema)),
                "description": str(capability.get("description") or "").strip(),
            }
        )
    return specs


def dynamic_export_prompt(
    *, robot_id: str, driver_name: str, design: Mapping[str, Any]
) -> tuple[str, str]:
    """Build the dynamic EXPORT system/user pair without changing the design."""

    specs = capability_export_specs(design)
    surface = json.dumps(specs, indent=2, ensure_ascii=False, sort_keys=True)
    builder = (
        "driver_from_scratch.Robot.build_from_mjcf('mjcf.xml')"
        if driver_name == "driver_from_scratch.py" else "driver.build()"
    )
    system = f"""\
You are Phase 4 EXPORT for a task-grounded AutoAdapter run.

Read the current {driver_name}, design/capability_design.json, and
validate_report.json from the workspace. Write one runnable mcp_server.py using
the official Python SDK (`from mcp.server.fastmcp import FastMCP`). Use the
framework runtime for one persistent robot, task initialization and recording:
```python
from auto_adapter.export_runtime import ExportRuntime
runtime = ExportRuntime(lambda: {builder})
mcp = FastMCP("{robot_id}", lifespan=runtime.lifespan)

@mcp.resource("robot://state")
def robot_state() -> dict:
    return runtime.observe()
```
Import the matching driver module before constructing this runtime. Each
control tool accesses `robot = runtime.robot` then calls the generated method.
The runtime builds lazily once, initializes the configured task scene and
records the actual MuJoCo world. Do not create a second robot or manage a
separate cache. The read-only resource is observation, not an extra control
tool. End with `if __name__ == "__main__": mcp.run()` for stdio transport.

The exact allowed control surface is the list in the user message. Register one
tool per listed method, using the exact method name. Do not enumerate or expose
inherited skeleton methods, aliases, criteria, validation helpers, or a demo.
Each tool must accept one `request` object whose JSON schema expresses the
listed request_schema, then forward it to the live robot as
`robot.<method>(request=<request payload>)`. If FastMCP supplies a typed model,
convert it to the driver's expected dictionary or JSON-safe payload before that
keyword call. Return a JSON-serializable value and preserve the driver's error
response. Keep the robot instance continuous across calls; do not reset or
rebuild it per tool invocation.

Preserve request_schema exactly, including required fields, numeric bounds,
array lengths and additionalProperties. An optional field is omittable, not
nullable unless the schema explicitly permits null. Do not discard unknown
fields or coerce input types. Use TypedDict with NotRequired (typing_extensions),
Annotated/Field constraints and pydantic.with_config(ConfigDict(strict=True,
extra="forbid")) for dictionary requests. Use extra="forbid"
only when the contract forbids extra properties. Do not add Optional[T]/null
or a default value absent from the contract. Keep capability descriptions
faithful to the design, without claiming stronger physical guarantees. In
particular, Optional[float]=None followed by dropping None is WRONG: it advertises
and accepts null even when the design does not. For an omittable number use
`duration_s: NotRequired[Annotated[float, Field(ge=0.1)]]`, with no default.

Use this supported pattern (substitute the actual fields and constraints):
```python
from typing import Annotated
from typing_extensions import TypedDict, NotRequired
from pydantic import ConfigDict, Field, with_config

@with_config(ConfigDict(strict=True, extra="forbid"))
class ExampleRequest(TypedDict):
    required_value: Annotated[float, Field(ge=0.0)]
    optional_duration: NotRequired[Annotated[float, Field(ge=0.1)]]
```
TypedDict defaults to total=True: required_value is required and only the
NotRequired field is omittable. FastMCP accepts this type directly and passes
a dictionary to the tool. Do not use total=False for required fields or put
NotRequired annotations on BaseModel fields. Remove abandoned declarations
from the final module so every top-level declaration imports successfully.

Before ending, use local_exec to run this exact framework check in the workspace:
`python -c 'import json; from auto_adapter.export_support import validate_dynamic_export_source; print(validate_dynamic_export_source("mcp_server.py", json.load(open("design/capability_design.json"))))'`
If it raises, fix mcp_server.py and re-run the check within your existing turn
budget. This checks actual registered MCP schemas, not just Python syntax.
Do not edit the checker, design, criteria, validation report, or driver.
"""
    user = (
        f"Robot ID: {robot_id}\n"
        f"Current driver file: {driver_name}\n"
        "Current validation report: validate_report.json\n"
        "Register exactly this dynamic capability surface (including each request schema):\n"
        f"{surface}\n"
        "Write mcp_server.py in the workspace and check that its tool names and forwarding match this list."
    )
    return system, user


def validate_dynamic_export_source(
    path: str | Path, design: Mapping[str, Any]
) -> dict[str, Any]:
    """Check syntax and the real SDK tool schemas; control needs a MuJoCo canary."""

    source_path = Path(path).resolve()
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))
    expected = capability_export_specs(design)
    expected_names = {item["method_name"] for item in expected}
    decorated: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def is_tool_decorator(node: ast.AST) -> bool:
        target = node.func if isinstance(node, ast.Call) else node
        return (
            isinstance(target, ast.Attribute)
            and target.attr == "tool"
            and isinstance(target.value, ast.Name)
            and target.value.id == "mcp"
        )

    def tool_name(function: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        for decorator in function.decorator_list:
            if not is_tool_decorator(decorator):
                continue
            if isinstance(decorator, ast.Call):
                for keyword in decorator.keywords:
                    if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                        return str(keyword.value.value)
            return function.name
        return ""

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = tool_name(node)
            if name:
                decorated[name] = node
    actual_names = set(decorated)
    if actual_names != expected_names:
        raise ValueError(
            f"dynamic export tools {sorted(actual_names)!r} do not match "
            f"design {sorted(expected_names)!r}"
        )

    # Import in a child process: registration must work without constructing a
    # robot or sending a control request. Inspect what clients actually receive.
    inspection = subprocess.run(
        [sys.executable, "-c", _INSPECT_MCP, str(source_path)],
        cwd=source_path.parent,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(filter(None, [
                str(Path(__file__).resolve().parents[1]),
                os.environ.get("PYTHONPATH", ""),
            ])),
        },
    )
    if inspection.returncode:
        raise ValueError(f"MCP tool inspection failed: {inspection.stderr[-4000:]}")
    registered = json.loads(inspection.stdout)
    tools = registered["tools"]
    runtime_names = [tool["name"] for tool in tools]
    if len(runtime_names) != len(expected_names) or set(runtime_names) != expected_names:
        raise ValueError(f"registered MCP tools do not match design: {runtime_names!r}")
    schemas = {tool["name"]: tool["inputSchema"] for tool in tools}
    for item in expected:
        name = item["method_name"]
        schema = schemas[name]
        if set(schema.get("properties", {})) != {"request"} or schema.get("required") != ["request"]:
            raise ValueError(f"MCP tool {name} must require one request object")
        actual = _normal_schema(schema["properties"]["request"], schema)
        wanted = _normal_schema(item["request_schema"], item["request_schema"])
        if actual != wanted:
            raise ValueError(
                f"MCP request schema differs from design for {name}: "
                f"actual={json.dumps(actual, sort_keys=True)}; "
                f"expected={json.dumps(wanted, sort_keys=True)}"
            )
    if "robot://state" not in {uri.rstrip("/") for uri in registered["resources"]}:
        raise ValueError("MCP export must expose the read-only robot://state resource")
    return {"tool_names": sorted(actual_names), "syntax": True, "request_schemas": True,
            "observation_resource": "robot://state"}


_INSPECT_MCP = """\
import asyncio, contextlib, importlib.util, json, pathlib, sys
path = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(path.parent))
with contextlib.redirect_stdout(sys.stderr):
    spec = importlib.util.spec_from_file_location("aa1_export_check", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    tools = asyncio.run(module.mcp.list_tools())
    resources = asyncio.run(module.mcp.list_resources())
print(json.dumps({"tools": [tool.model_dump(mode="json") for tool in tools],
                  "resources": [str(resource.uri) for resource in resources]}))
"""


def _normal_schema(value: Any, document: Mapping[str, Any], refs: tuple[str, ...] = ()) -> Any:
    """Inline local references and ignore SDK display annotations for comparison."""

    if isinstance(value, list):
        return [_normal_schema(item, document, refs) for item in value]
    if not isinstance(value, Mapping):
        return value
    if "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in refs:
            raise ValueError(f"unsupported MCP schema reference: {ref!r}")
        target: Any = document
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        value = {**target, **{key: item for key, item in value.items() if key != "$ref"}}
        refs = (*refs, ref)
    result = {}
    for key, item in value.items():
        if key in {"title", "description", "$defs"}:
            continue
        if key == "properties":
            result[key] = {name: _normal_schema(child, document, refs) for name, child in item.items()}
        else:
            result[key] = _normal_schema(item, document, refs)
    if "required" in result:
        result["required"] = sorted(result["required"])
    return result


def _workspace_file(value: Any, workspace: Path) -> Path | None:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = workspace / path
    try:
        path = path.resolve()
        path.relative_to(workspace.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def collect_design_artifacts(workspace: str | Path) -> list[Path]:
    """Collect current DESIGN outputs, including both compact/full traces."""

    root = Path(workspace).resolve() / "design"
    if not root.is_dir():
        return []
    paths: list[Path] = []
    preferred = (
        "capability_design.json",
        "criteria.json",
        "scene_cases.yaml",
        "probe_report.json",
        "capability_preparation.json",
        "trace.jsonl",
        "trace.messages.jsonl",
    )
    for name in preferred:
        path = root / name
        if path.is_file():
            paths.append(path)
    for path in sorted(root.rglob("*")):
        if path.is_file() and path not in paths:
            paths.append(path)
    return paths


def validation_case_metadata(report: Mapping[str, Any], workspace: str | Path) -> list[dict[str, Any]]:
    """Keep report metrics/standards and case video paths in phase metadata."""

    root = Path(workspace).resolve()
    rows: list[dict[str, Any]] = []
    for test in report.get("tests", []) if isinstance(report.get("tests"), list) else []:
        if not isinstance(test, Mapping):
            continue
        metrics = test.get("metrics")
        measurements = metrics.get("measurements", []) if isinstance(metrics, Mapping) else []
        video = test.get("video")
        video_path = _workspace_file(video.get("path"), root) if isinstance(video, Mapping) else None
        rows.append(
            {
                "case_id": test.get("case_id") or test.get("test"),
                "capability_id": test.get("capability_id"),
                "method_name": test.get("method_name"),
                "ok": bool(test.get("ok")),
                "measurements": copy.deepcopy(list(measurements)) if isinstance(measurements, list) else [],
                "video": {
                    "ok": bool(video.get("ok")) if isinstance(video, Mapping) else False,
                    "path": str(video_path.relative_to(root)) if video_path else None,
                },
            }
        )
    return rows


def collect_validation_artifacts(
    workspace: str | Path, report: Mapping[str, Any], report_path: str | Path | None = None
) -> list[Path]:
    """Collect the report and files written for each current validation case."""

    root = Path(workspace).resolve()
    paths: list[Path] = []
    report_file = _workspace_file(report_path, root) if report_path else None
    if report_file:
        paths.append(report_file)
    for test in report.get("tests", []) if isinstance(report.get("tests"), list) else []:
        if not isinstance(test, Mapping):
            continue
        path_values: list[Any] = []
        raw_paths = test.get("paths")
        if isinstance(raw_paths, Mapping):
            path_values.extend(raw_paths.values())
        video = test.get("video")
        if isinstance(video, Mapping):
            path_values.append(video.get("path"))
        case_dir: Path | None = None
        for value in path_values:
            candidate = _workspace_file(value, root)
            if candidate and candidate not in paths:
                paths.append(candidate)
        if isinstance(video, Mapping):
            video_file = _workspace_file(video.get("path"), root)
            if video_file is not None:
                case_dir = video_file.parent
        if case_dir is None and isinstance(raw_paths, Mapping):
            result_file = _workspace_file(raw_paths.get("result"), root)
            if result_file is not None:
                case_dir = result_file.parent
        if case_dir is not None:
            for candidate in sorted(case_dir.glob("frame-last.*")):
                if candidate.is_file() and candidate not in paths:
                    paths.append(candidate)
    return paths


__all__ = [
    "capability_export_specs",
    "collect_design_artifacts",
    "collect_validation_artifacts",
    "dynamic_export_prompt",
    "validate_dynamic_export_source",
    "validation_case_metadata",
]
