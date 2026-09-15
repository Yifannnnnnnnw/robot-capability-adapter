# SPDX-License-Identifier: Apache-2.0
"""AA1 environment/transport adapter around the vendored official ReCAP generator.

Task decomposition, traversal and parent-context prompts run in upstream.chatbot.
This module supplies native actions, model I/O, diagnostic tracing, host budgets,
and the configured task/CLI entry point. Task execution and recording live in
task_execution; controller completion is not a physical task verdict.
"""
from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
import tempfile
import time
from typing import Any

from .react_loop import ReactLoop
from .vendor.recap import chatbot as upstream

UPSTREAM_REVISION = "2fb112ffad685c7c6f7de86d5487ecca6f566fcc"
UPSTREAM_CONTROLLER = "auto_adapter.agent.vendor.recap.chatbot.chatbot"


class CapabilityAdapterError(RuntimeError):
    """The proposed capability name or request is invalid."""


class CapabilityInvocationError(CapabilityAdapterError):
    """A valid capability could not be executed."""


class RecapControllerError(ValueError):
    """The controller's public input or proposed plan is invalid."""


@dataclass(frozen=True)
class RecapBudgets:
    max_planning_turns: int = 16
    max_capability_calls: int = 12
    max_depth: int = 6
    max_subtasks_per_plan: int = 8
    max_invalid_outputs: int = 3
    context_window_messages: int = 32

    def __post_init__(self):
        for name, value in asdict(self).items():
            if type(value) is not int or value <= 0:
                raise RecapControllerError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class RecapControllerResult:
    status: str
    planning_turns: int
    capability_calls: int
    invalid_outputs: int
    trace: tuple[Mapping[str, Any], ...]
    context_tree: Mapping[str, Any]

    @property
    def model_turns(self):
        return self.planning_turns

    @property
    def tool_calls(self):
        return self.capability_calls


# Upstream catches Exception to retry malformed plans. Host termination must bypass
# that handler; catch this specific sentinel only, never KeyboardInterrupt/SystemExit.
class _StopRecap(BaseException):
    def __init__(self, status):
        self.status = status


class _Actions:
    """A schema-backed action set for upstream's `action in valid_actions` test.

    Membership has no side effects: upstream probes both abstract tasks and leaves.
    Requests are continuous, so a finite list of preselected actions cannot describe it.
    """
    def __init__(self, adapter):
        self.adapter = adapter

    def decode(self, action):
        value = json.loads(action)
        if not isinstance(value, dict) or set(value) != {"capability_name", "request"}:
            raise CapabilityAdapterError("action must contain capability_name and request")
        if not isinstance(value["capability_name"], str) or not isinstance(value["request"], dict):
            raise CapabilityAdapterError("action needs a string capability_name and object request")
        # Reject nonfinite JSON even when the underlying schema permits a number.
        json.dumps(value, allow_nan=False)
        return value["capability_name"], self.adapter.validate_request(
            value["capability_name"], value["request"])

    def __contains__(self, action):
        try:
            self.decode(action)
            return True
        except (ValueError, TypeError, CapabilityAdapterError):
            return False

    def __repr__(self):
        return "AA1 capabilities and native request schemas supplied in the environment rules"


SYSTEM_PROMPT = """You control a robot through recursive ReCAP task planning.
Respond with one JSON object: {"think": "brief plan summary", "subtasks": ["..."]}.
The think field is a short action summary, not private chain-of-thought.
Subtasks are strings: either abstract task descriptions or JSON-encoded primitive actions
with exactly {"capability_name": "a published method name", "request": {native fields}}.
ReCAP treats a SINGLE subtask while descending as a primitive action to execute. To
DECOMPOSE a task, return at least two subtasks. Start a multi-phase goal with meaningful
abstract groups. Only the first item is expanded/executed; remaining siblings are pending.
After returning to a parent, revise its remaining plan from the supplied observations.
Return [] when the current task has no remaining work. Preserve unfinished requirements.
Do not invent methods, reset the world, regenerate a driver or use private test criteria.
An EXECUTED operation means the method returned normally, not that its physical goal was
independently verified. Inspect observations and errors. Controller completion is not a
physical task verdict. Budget limits are shared across the whole task tree.
"""


def run_recap(*, public_task, adapter, model, budgets=None, initial_public_state=None, log_dir=None):
    """Drive the official generator with AA1 JSON model turns and native capabilities."""
    budgets = budgets or RecapBudgets()
    actions = _Actions(adapter)
    trace, nodes = [], []
    planning_turns = capability_calls = invalid_outputs = 0
    last_status = None

    def event(event_name, **payload):
        trace.append({"event": event_name, **payload})

    def invalid(message):
        nonlocal invalid_outputs
        invalid_outputs += 1
        event("invalid_output", message=message)
        if invalid_outputs >= budgets.max_invalid_outputs:
            raise _StopRecap("INVALID_OUTPUT_BUDGET_EXHAUSTED")

    class ObservedNode(upstream.Node):
        def __init__(self, task_name, parent=None):
            super().__init__(task_name, parent)
            self.node_id = f"n{len(nodes)}"
            self.depth = parent.depth + 1 if parent is not None else 0
            if self.depth > budgets.max_depth:
                raise _StopRecap("DEPTH_BUDGET_EXHAUSTED")
            nodes.append(self)
            event("node_created", node_id=self.node_id,
                  parent_id=parent.node_id if parent is not None else None,
                  depth=self.depth, task_name=task_name)

        def set_info(self, info):
            if (not isinstance(info, dict) or not isinstance(info.get("think"), str)
                    or not isinstance(info.get("subtasks"), list)
                    or len(info["subtasks"]) > budgets.max_subtasks_per_plan
                    or any(not isinstance(s, str) or not s.strip() for s in info["subtasks"])):
                invalid("expected think string and bounded subtasks string list")
                raise ValueError("expected think string and bounded subtasks string list")
            super().set_info(info)
            event("plan_revision", node_id=self.node_id, plan=info)

        def set_obs(self, obs):
            super().set_obs(obs)
            event("observation", node_id=self.node_id, observation=obs)

    class RobotPrompt(upstream.Prompt):
        def generate_init_prompt(self, **kwargs):
            return super().generate_init_prompt(**kwargs).replace(
                "Now you need to make a new meal.", "Now you need to perform a robot task.")

        def _return(self, prompt, kind, kwargs):
            event("parent_return", kind=kind, **kwargs)
            return prompt.replace("You have successfully completed the task:",
                                  "The child task attempt has returned. Inspect its observations and operation status:")

        def generate_leaf_up_prompt(self, **kwargs):
            return self._return(super().generate_leaf_up_prompt(**kwargs), "leaf", kwargs)

        def generate_leaf_judge_done_prompt(self, **kwargs):
            return self._return(super().generate_leaf_judge_done_prompt(**kwargs), "leaf", kwargs)

        def generate_nonleaf_up_prompt(self, **kwargs):
            return self._return(super().generate_nonleaf_up_prompt(**kwargs), "subtask", kwargs)

        def generate_nonleaf_judge_done_prompt(self, **kwargs):
            return self._return(super().generate_nonleaf_judge_done_prompt(**kwargs), "subtask", kwargs)

        def generate_leaf_up_fail_prompt(self, **kwargs):
            try:
                actions.decode(kwargs["fail_task_name"])
            except (ValueError, TypeError, CapabilityAdapterError) as exc:
                explanation = str(exc)
            else:
                explanation = "invalid primitive action"
            invalid(explanation)
            prompt = super().generate_leaf_up_fail_prompt(**kwargs)
            start = prompt.index("Because the task name")
            end = prompt.index("\n\n", start)
            prompt = prompt[:start] + (
                "The primitive action failed AA1 native request validation: " + explanation
                + ". Use a published capability and its request schema. An abstract decomposition "
                  "needs at least two subtasks; a descending singleton must be executable.") + prompt[end:]
            event("parent_return", kind="invalid_action", **kwargs)
            return prompt

    class Memory(upstream.ChatGPTWithMemory):
        def __init__(self):
            # Retain upstream's shared history and [2:4] window eviction. AA1 supplies
            # transport instead of the upstream standalone OpenAI client and billing.
            self.fixed_prompt = {"role": "user", "content": "Use the ReCAP JSON plan format."}
            self.truncated_chat_history = [self.fixed_prompt]
            self.ctx_len = budgets.context_window_messages
            self.model = "aa1-transport"

        def invoke(self, user_prompt):
            nonlocal planning_turns
            if planning_turns >= budgets.max_planning_turns:
                raise _StopRecap("PLANNING_TURN_BUDGET_EXHAUSTED")
            if len(self.truncated_chat_history) > self.ctx_len:
                del self.truncated_chat_history[2:4]
            self.truncated_chat_history.append({"role": "user", "content": user_prompt})
            planning_turns += 1
            try:
                response = model.generate_json(messages=self.truncated_chat_history)
            except Exception as exc:
                event("model_error", error_type=type(exc).__name__, message=str(exc))
                raise _StopRecap("MODEL_ERROR") from exc
            self.truncated_chat_history.append({"role": "assistant", "content": response})
            try:
                json.loads(upstream.remove_json_fence(response))
            except (ValueError, TypeError) as exc:
                invalid(str(exc))
            return response

    memory = Memory()
    temporary = tempfile.TemporaryDirectory(prefix="aa1-recap-") if log_dir is None else None
    output = Path(temporary.name if temporary else log_dir)
    output.mkdir(parents=True, exist_ok=True)
    task = json.dumps(public_task, ensure_ascii=False, allow_nan=False)
    rule = json.dumps({"capabilities": adapter.public_catalog(), "budgets": asdict(budgets)},
                      ensure_ascii=False, allow_nan=False)
    generator = upstream.chatbot(
        system_prompt=SYSTEM_PROMPT, few_shot_list=[], task_name=task,
        init_obs=json.dumps(initial_public_state or {}, ensure_ascii=False, allow_nan=False),
        rule=rule, valid_actions=actions, ctx_len=budgets.context_window_messages,
        llm=memory, prompt_obj=RobotPrompt(), node_factory=ObservedNode, log_dir=str(output))
    event("controller_started", controller=UPSTREAM_CONTROLLER, revision=UPSTREAM_REVISION)
    try:
        action = next(generator)
        while True:
            if capability_calls >= budgets.max_capability_calls:
                raise _StopRecap("CAPABILITY_CALL_BUDGET_EXHAUSTED")
            name, request = actions.decode(action)
            capability_calls += 1
            event("capability_call", capability_name=name, request=request)
            result = adapter.execute(name, request)
            observation = json.dumps(result, ensure_ascii=False, allow_nan=False)
            last_status = result.get("operation", {}).get("status")
            event("capability_result", capability_name=name, feedback=result)
            if last_status == "WORKER_ABORTED":
                raise _StopRecap("WORKER_ABORTED")
            action = generator.send((observation, actions))
    except StopIteration:
        status = ("NO_ACTION_EXECUTED" if not capability_calls else
                  "LAST_ACTION_FAILED" if last_status != "EXECUTED" else "CONTROLLER_FINISHED")
    except _StopRecap as stop:
        status = stop.status
    except Exception as exc:
        event("runtime_error", error_type=type(exc).__name__, message=str(exc))
        status = "RUNTIME_ERROR"
    finally:
        generator.close()
        # Also save partial trees/history after host budget termination.
        if nodes:
            upstream.save_tree_to_json(nodes[0], str(output / "tree.json"))
        memory.save_history_to_json(str(output / "history.json"))
        if temporary:
            temporary.cleanup()
    event("controller_ended", status=status)
    return RecapControllerResult(status, planning_turns, capability_calls, invalid_outputs,
                                 tuple(trace), upstream.tree_to_dict(nodes[0]) if nodes else {})


class AA1RecapModel:
    """Send official ReCAP's JSON conversation through AA1's model transport."""

    def __init__(self, *, model, provider, region, max_tokens, trace_path):
        transport = ReactLoop(tools=[], system="", model=model, provider=provider,
                              region=region, max_tokens_per_turn=max_tokens)
        self.client, self.model = transport.client, transport.model
        self.max_tokens = max_tokens
        self.trace_path = Path(trace_path)
        self.trace_path.write_text("")
        self.messages_trace_path = self.trace_path.with_name("model_messages.jsonl")
        self.messages_trace_path.write_text("")

    def generate_json(self, *, messages):
        system = [
            'Return only a JSON object with "think" and "subtasks". '
            '"think" is a brief plan summary; "subtasks" is an ordered list of strings.'
        ]
        converted = []
        for message in messages:
            role = message["role"]
            if role == "system":
                system.append(message["content"])
                continue
            item = {"role": role, "content": message["content"]}
            if converted and converted[-1]["role"] == role:
                converted[-1]["content"] += "\n\n" + item["content"]
            else:
                converted.append(item)
        system_prompt = "\n\n".join(system)
        response = self.client.messages.create(
            model=self.model, system=system_prompt, messages=converted,
            max_tokens=self.max_tokens)
        content = "\n".join(
            block.text for block in getattr(response, "content", ())
            if getattr(block, "type", None) == "text"
        )
        response_json = _json_value(response)
        usage = getattr(response, "usage", None)
        messages_trace_path = getattr(
            self, "messages_trace_path", self.trace_path.with_name("model_messages.jsonl")
        )
        if not messages_trace_path.exists():
            messages_trace_path.write_text("")
        with messages_trace_path.open("a") as stream:
            stream.write(json.dumps({
                "system_prompt": system_prompt,
                "messages": messages,
                "converted_messages": converted,
                "tools": [],
                "response": response_json,
                "usage": _json_value(usage) if usage is not None else None,
            }, ensure_ascii=False, allow_nan=False) + "\n")
        with self.trace_path.open("a") as stream:
            stream.write(json.dumps({"messages": messages, "response": content}) + "\n")
        return content


def _json_value(value: Any) -> Any:
    """Return a finite JSON representation of SDK/Pydantic values."""

    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump(mode="json")
        except TypeError:
            value = value.model_dump()
    elif hasattr(value, "dict") and callable(value.dict):
        value = value.dict()
    elif hasattr(value, "__dict__"):
        value = vars(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)
    return value


def _exception_text(exc: BaseException) -> str:
    """Keep the useful leaf message when AnyIO wraps an MCP failure."""

    nested = getattr(exc, "exceptions", None)
    if nested:
        details = [_exception_text(child) for child in nested]
        details = [detail for detail in details if detail]
        if details:
            return "; ".join(details)
    return str(exc)


def passed_design(design, suite, report):
    """Require every trusted case for a capability, including boundary cases."""
    tests = report.get("tests", [])
    selected = []
    for capability in design["capabilities"]:
        cases = [c for c in suite.get("scene_cases", suite.get("cases", []))
                 if c["capability_id"] == capability["capability_id"]]
        if cases and all(
            len(matches := [t for t in tests if t.get("case_id") == c["case_id"]]) == 1
            and matches[0].get("ok") is True for c in cases
        ):
            selected.append(capability)
    if not selected:
        raise ValueError("ReCAP demo has no fully Framework-passed capability")
    return {**design, "capabilities": selected}


def _schema_ref_target(document: Mapping[str, Any], ref: str) -> Any:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise CapabilityAdapterError(f"unsupported MCP schema reference: {ref!r}")
    target: Any = document
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, Mapping) or part not in target:
            raise CapabilityAdapterError(f"unresolved MCP schema reference: {ref!r}")
        target = target[part]
    return target


def _resolve_schema(value: Any, document: Mapping[str, Any], refs: tuple[str, ...] = ()) -> Any:
    """Inline local MCP schema references before nesting the request schema."""

    if isinstance(value, list):
        return [_resolve_schema(child, document, refs) for child in value]
    if not isinstance(value, Mapping):
        return value
    if "$ref" in value:
        ref = value["$ref"]
        if ref in refs:
            raise CapabilityAdapterError(f"cyclic MCP schema reference: {ref!r}")
        target = _resolve_schema(_schema_ref_target(document, ref), document, (*refs, ref))
        if not isinstance(target, Mapping):
            raise CapabilityAdapterError(f"MCP schema reference is not an object: {ref!r}")
        merged = dict(target)
        merged.update({key: child for key, child in value.items() if key != "$ref"})
        return _resolve_schema(merged, document, (*refs, ref))
    return {
        key: _resolve_schema(child, document, refs)
        for key, child in value.items()
        if key not in {"$defs", "definitions"}
    }


def _tool_value(tool: Any, key: str, default: Any = None) -> Any:
    if isinstance(tool, Mapping):
        return tool.get(key, default)
    return getattr(tool, key, default)


def _request_schema_from_tool(tool: Any) -> dict[str, Any]:
    raw = _tool_value(tool, "inputSchema")
    if raw is None:
        raw = _tool_value(tool, "input_schema")
    schema = _json_value(raw)
    if not isinstance(schema, Mapping):
        raise CapabilityAdapterError("MCP tool inputSchema must be an object")
    schema = _resolve_schema(schema, schema)
    properties = schema.get("properties")
    required = schema.get("required")
    if (not isinstance(properties, Mapping) or set(properties) != {"request"}
            or not isinstance(required, list) or set(required) != {"request"}
            or len(required) != 1):
        raise CapabilityAdapterError(
            "each exported MCP tool must require exactly one request object"
        )
    request_schema = properties["request"]
    if not isinstance(request_schema, Mapping) or request_schema.get("type") != "object":
        raise CapabilityAdapterError("exported MCP request must have an object schema")
    return copy.deepcopy(dict(request_schema))


def catalog_from_tools_result(tools_result: Any) -> list[dict[str, Any]]:
    """Build the ReCAP catalog exclusively from ClientSession.list_tools()."""

    tools = _tool_value(tools_result, "tools", ())
    if not isinstance(tools, (list, tuple)):
        raise CapabilityAdapterError("MCP list_tools result has no tools array")
    catalog: list[dict[str, Any]] = []
    names: set[str] = set()
    for tool in tools:
        name = _tool_value(tool, "name")
        if not isinstance(name, str) or not name:
            raise CapabilityAdapterError("MCP tool name must be nonempty text")
        if name in names:
            raise CapabilityAdapterError(f"duplicate MCP tool name: {name!r}")
        names.add(name)
        description = _tool_value(tool, "description", "")
        catalog.append({
            "capability_id": name,
            "method_name": name,
            "capability_name": name,
            "description": description if isinstance(description, str) else "",
            "request_schema": _request_schema_from_tool(tool),
        })
    return catalog


def _resource_value(result: Any) -> Any:
    contents = _tool_value(result, "contents", ())
    if not isinstance(contents, (list, tuple)) or not contents:
        raise ValueError("robot://state resource returned no contents")
    values = []
    for content in contents:
        text = _tool_value(content, "text")
        if text is not None:
            try:
                values.append(json.loads(text))
            except (TypeError, ValueError):
                values.append(text)
            continue
        blob = _tool_value(content, "blob")
        if blob is not None:
            import base64
            decoded = base64.b64decode(blob)
            try:
                values.append(json.loads(decoded.decode("utf-8")))
            except (UnicodeDecodeError, ValueError):
                values.append(decoded.decode("utf-8", errors="replace"))
            continue
        values.append(_json_value(content))
    return values[0] if len(values) == 1 else values


class AA1CapabilityAdapter:
    """Bridge ReCAP to the exact tools exposed by one MCP ClientSession."""

    def __init__(self, *, session=None, loop=None, tools_result=None, catalog=None,
                 robot_configuration_id=None, tool_call_log=None):
        self._session = session
        self._loop = loop
        if catalog is None and tools_result is not None:
            catalog = catalog_from_tools_result(tools_result)
        self._catalog = copy.deepcopy(catalog or [])
        self.capabilities = {item["capability_name"]: item for item in self._catalog}
        self.tool_names = frozenset(self.capabilities)
        self.robot_configuration_id = robot_configuration_id or "mcp-export"
        self.capability_design_id = (
            self.robot_configuration_id + "::mcp-export"
        )
        self.tool_call_log = tool_call_log if tool_call_log is not None else []

    def public_catalog(self):
        return copy.deepcopy(self._catalog)

    def validate_request(self, name, request):
        from auto_adapter.scene_runtime import SceneCaseError, _validate_schema_value
        if name not in self.capabilities:
            raise CapabilityAdapterError("unknown capability")
        try:
            _validate_schema_value(request, self.capabilities[name]["request_schema"],
                                   where=f"{name}.request")
        except SceneCaseError as exc:
            raise CapabilityAdapterError(str(exc)) from None
        return copy.deepcopy(dict(request))

    def _await(self, awaitable):
        if self._session is None or self._loop is None:
            raise CapabilityAdapterError("MCP session is unavailable")
        return asyncio.run_coroutine_threadsafe(awaitable, self._loop).result()

    def _read_observation(self):
        try:
            return _resource_value(self._await(self._session.read_resource("robot://state")))
        except Exception as exc:
            return {"available": False, "error_type": type(exc).__name__,
                    "error": _exception_text(exc)}

    def read_observation(self):
        return _json_value(self._read_observation())

    def execute(self, name, request):
        request = self.validate_request(name, request)
        if self._session is None:
            raise CapabilityAdapterError("MCP session is unavailable")

        arguments = {"request": request}
        call = {
            "tool": name,
            "arguments": copy.deepcopy(arguments),
            "request": copy.deepcopy(request),
            "ok": False,
            "isError": None,
            "mcp_result": None,
        }
        raw_result = None
        try:
            raw_result = self._await(self._session.call_tool(name, arguments=arguments))
            raw_dump = _json_value(raw_result)
            is_error = bool(_tool_value(raw_result, "isError", False))
            value = _json_value(_call_result_value(raw_result))
            status = "ERROR" if is_error else "EXECUTED"
            operation = {"status": status, "return_value": value}
            if is_error:
                operation.update(error_type="MCPToolError", error=value)
            call.update(ok=not is_error, isError=is_error, mcp_result=raw_dump,
                        return_value=value, status=status)
        except Exception as exc:
            is_error = True
            status = "ERROR"
            value = None
            operation = {"status": status, "return_value": None,
                         "error_type": type(exc).__name__, "error": _exception_text(exc)}
            call.update(ok=False, isError=True, status=status,
                        error_type=type(exc).__name__, error=_exception_text(exc),
                        mcp_result=_json_value(raw_result) if raw_result is not None else None)
        observation = self.read_observation()
        if (isinstance(observation, Mapping)
                and observation.get("available") is False):
            # A tool return without a readable post-call state is not a usable
            # operation. Preserve the MCP response, but stop the controller at
            # the same worker boundary used by the original task executor.
            status = "WORKER_ABORTED"
            observation_error = observation.get("error", "robot://state unavailable")
            operation["status"] = status
            operation.update(error_type="ObservationError", error=observation_error)
            call.update(ok=False, status=status,
                        error_type="ObservationError", error=observation_error)
        call["observations"] = copy.deepcopy(observation)
        call["observation"] = copy.deepcopy(observation)
        self.tool_call_log.append(call)
        return {"operation": operation, "observations": observation}


def _call_result_value(result: Any) -> Any:
    structured = _tool_value(result, "structuredContent")
    if structured is not None:
        return structured
    contents = _tool_value(result, "content", ())
    if not isinstance(contents, (list, tuple)):
        return None
    values = []
    for content in contents:
        text = _tool_value(content, "text")
        if text is not None:
            try:
                values.append(json.loads(text))
            except (TypeError, ValueError):
                values.append(text)
        else:
            values.append(_json_value(content))
    if len(values) == 1:
        return values[0]
    return values


def _task_inputs(*, workspace, robot_id, capability_design=None,
                 scene_cases_path=None, demo_config_path=None, from_scratch=False):
    """Resolve generation artifacts once; never replace an explicitly selected design."""
    import yaml
    from auto_adapter.robot_catalog import REPO_ROOT
    from auto_adapter.scene_runtime import load_scene_cases

    workspace = Path(workspace).resolve()
    config_path = Path(demo_config_path or REPO_ROOT / "auto_adapter/demo_tasks.yaml").resolve()
    config = yaml.safe_load(config_path.read_text())["robots"].get(robot_id)
    if not config:
        raise ValueError(f"no fixed demo configured for {robot_id}")
    if not config.get("success"):
        raise ValueError(f"fixed demo for {robot_id} has no success specification")
    design = capability_design
    if design is None:
        saved = workspace / "design/capability_design.json"
        design = json.loads(saved.read_text()) if saved.is_file() else None
    if not design:
        raise ValueError(f"{robot_id}: no capability design supplied for the existing driver")
    if design.get("robot_configuration_id") != robot_id:
        raise ValueError("task robot does not match capability design")
    cases_path = Path(scene_cases_path).resolve() if scene_cases_path else workspace / "design/scene_cases.yaml"
    if scene_cases_path is not None and not cases_path.is_file():
        raise ValueError(f"supplied scene_cases_path does not exist: {cases_path}")
    if not cases_path.is_file():
        raise ValueError("current capability design requires its corresponding scene_cases_path")
    suite = load_scene_cases(cases_path, design=design)
    scene = Path(config["scene"])
    if not scene.is_absolute():
        scene = REPO_ROOT / scene
    return dict(
        driver_path=str(workspace / ("driver_from_scratch.py" if from_scratch else "driver.py")),
        from_scratch=from_scratch, robot_id=robot_id, capability_design=design,
        validation_suite=suite,
        validation_report=json.loads((workspace / "validate_report.json").read_text()),
        task_description=config["task"], scene_path=str(scene.resolve()),
        initial_state=config.get("initial_state", {}), parameters=config.get("parameters", {}),
        success_spec=config["success"],
    )


def run_configured_demo(*, workspace, robot_id, capability_design, scene_cases_path=None,
                        from_scratch=False, model, provider, region, max_tokens,
                        demo_config_path=None, export_server_path=None):
    """Both orchestrators dispatch the same isolated, existing-driver task process."""
    import os
    import subprocess
    import sys
    import tempfile
    from auto_adapter.robot_catalog import REPO_ROOT

    started = time.monotonic()
    root = Path(workspace).resolve() / "demos"
    root.mkdir(parents=True, exist_ok=True)
    invocation_dir = Path(tempfile.mkdtemp(prefix="recap-", dir=root))
    output_dir = invocation_dir / "task"
    report_path = output_dir / "task_report.json"
    try:
        inputs = _task_inputs(workspace=workspace, robot_id=robot_id,
                              capability_design=capability_design, scene_cases_path=scene_cases_path,
                              from_scratch=from_scratch, demo_config_path=demo_config_path)
        inputs.update(output_dir=str(output_dir), model=model, provider=provider,
                      region=region, max_tokens=max_tokens,
                      export_server_path=str(Path(export_server_path or
                                                  (Path(workspace).resolve() / "mcp_server.py")).resolve()))
        payload = invocation_dir / "request.json"
        payload.write_text(json.dumps(inputs, indent=2) + "\n")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        with (invocation_dir / "worker.log").open("w") as log:
            completed = subprocess.run(
                [sys.executable, "-m", "auto_adapter.agent.recap", "--input-json", str(payload)],
                env=env, cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT,
                timeout=2400, check=False,
            )
        if not report_path.is_file():
            raise RuntimeError(f"task worker exited {completed.returncode}; see {invocation_dir / 'worker.log'}")
        report = json.loads(report_path.read_text())
        if completed.returncode and report.get("ok"):
            report.update(ok=False, error=f"task worker exited {completed.returncode}")
        return report
    except Exception as exc:
        report = {"ok": False, "error": str(exc), "controller_result": None,
                  "physical_task_success": None, "duration_sec": time.monotonic() - started,
                  "report_path": str(report_path), "trace_path": None, "video_path": None}
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        return report


def run_demo(*, workspace, robot_id, task_description=None, model, provider="holistic",
             region="us-east-1", max_tokens=6000, from_scratch=False,
             demo_config_path=None, output_dir=None, export_server_path=None):
    """Compatibility wrapper; use run_task/--input-json for arbitrary explicit tasks and scenes."""
    import tempfile
    from .task_execution import run_task

    inputs = _task_inputs(workspace=workspace, robot_id=robot_id,
                          from_scratch=from_scratch, demo_config_path=demo_config_path)
    if task_description:
        if task_description != inputs["task_description"]:
            # A different explicit task must not inherit the fixed demo's verdict.
            # Use run_task/--input-json to supply its own success specification.
            inputs["success_spec"] = None
        inputs["task_description"] = task_description
    if output_dir is None:
        root = Path(workspace).resolve() / "demos"
        root.mkdir(parents=True, exist_ok=True)
        output_dir = Path(tempfile.mkdtemp(prefix="recap-", dir=root)) / "task"
    return run_task(**inputs, output_dir=output_dir, model=model, provider=provider,
                    region=region, max_tokens=max_tokens, export_server_path=export_server_path)


def main():
    import argparse
    from .task_execution import run_task

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", type=Path,
                        help="Explicit run_task keyword inputs; never generates or repairs a driver.")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--robot-id")
    parser.add_argument("--task", dest="task_description")
    parser.add_argument("--model")
    parser.add_argument("--provider", default="holistic")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--from-scratch", action="store_true")
    parser.add_argument("--demo-config-path", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = vars(parser.parse_args())
    input_json = args.pop("input_json")
    if input_json:
        report = run_task(**json.loads(input_json.read_text()))
    else:
        if not all(args[key] for key in ("workspace", "robot_id", "model")):
            parser.error("supply --input-json or --workspace, --robot-id and --model")
        report = run_demo(**args)
    print(json.dumps({key: report.get(key) for key in ("ok", "error", "video_path", "report_path")}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
