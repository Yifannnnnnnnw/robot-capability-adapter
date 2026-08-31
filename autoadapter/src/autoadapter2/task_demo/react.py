"""Fixed, bounded ReAct controller for post-admission Task Demo trials."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from autoadapter2.react import (
    ReactResult,
    ReactToolAbort,
    ToolModelClient,
    ToolSpec,
    run_react,
)


TASK_DEMO_REACT_SYSTEM_PROMPT = """You are the fixed AutoAdapter Task Demo high-level controller.
Select and sequence only the exposed robot capability tools. The supplied public request is the
task objective and must be passed to a capability exactly as supplied. Use tool observations to
recover from a rejected selection or failed invocation. Call finish_task_demo only after at least
one capability invocation succeeds. Your text and finish call are controller outcomes only: the
trusted Direct-MuJoCo Harness alone measures the task and issues the verdict. Do not claim PASS,
infer hidden scoring criteria, or request private scene, binding, guard, or reference information.
In PUBLIC_INPUT_JSON, public_arguments is the exact capability-tool argument object; the robot and
task fields are context only.
Invoke the reusable capability interface with this exact public request:"""

_FINISH_TOOL_NAME = "finish_task_demo"


class TaskDemoControllerError(RuntimeError):
    """Raised when public Task Demo controller inputs or routing are invalid."""


CapabilityInvoker = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class TaskDemoControllerResult:
    """Controller outcome only; this is never a Task Demo verdict."""

    status: str
    model_turns: int
    tool_calls: int
    capability_calls: int
    trace: tuple[dict[str, Any], ...]


def _json_copy(value: Any, *, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise TaskDemoControllerError(f"{label} must be a finite JSON value") from exc


def _text(value: Mapping[str, Any], field: str, *, where: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise TaskDemoControllerError(f"{where}.{field} must be non-empty text")
    return item.strip()


def _parameter_schema(value: Mapping[str, Any]) -> dict[str, Any]:
    kind = value.get("type")
    if kind not in {"array", "boolean", "integer", "number", "object", "string"}:
        raise TaskDemoControllerError("task parameter uses an unsupported public type")
    result: dict[str, Any] = {"type": kind}
    description = value.get("description")
    details = []
    if isinstance(description, str) and description.strip():
        details.append(description.strip())
    unit = value.get("unit")
    frame = value.get("frame")
    if isinstance(unit, str) and isinstance(frame, str):
        details.append(f"Unit: {unit}. Frame: {frame}.")
    if details:
        result["description"] = " ".join(details)
    if kind == "array":
        items = value.get("items")
        if not isinstance(items, Mapping):
            raise TaskDemoControllerError("array task parameter lacks an item schema")
        result["items"] = _parameter_schema(items)
        length = value.get("length")
        if isinstance(length, int) and not isinstance(length, bool):
            result["minItems"] = length
            result["maxItems"] = length
    elif kind == "object":
        result.update({"properties": {}, "additionalProperties": True})
    return result


def _task_map(tasks: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for index, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            raise TaskDemoControllerError(f"tasks[{index}] must be an object")
        task_id = _text(task, "task_id", where=f"tasks[{index}]")
        if task_id in result:
            raise TaskDemoControllerError(f"duplicate task_id {task_id!r}")
        result[task_id] = task
    return result


def _capability_tool_schema(
    capability: Mapping[str, Any],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    covered = capability.get("covered_task_ids")
    if not isinstance(covered, list) or not covered:
        raise TaskDemoControllerError("capability covered_task_ids must be a non-empty list")
    covered_ids: list[str] = []
    parameter_properties: dict[str, dict[str, Any]] = {}
    for task_id in covered:
        if not isinstance(task_id, str) or task_id not in tasks_by_id:
            raise TaskDemoControllerError("capability covers an unknown public task")
        covered_ids.append(task_id)
        invocation = tasks_by_id[task_id].get("invocation_schema")
        request = invocation.get("request") if isinstance(invocation, Mapping) else None
        parameters = request.get("task_parameters") if isinstance(request, Mapping) else None
        properties = parameters.get("properties") if isinstance(parameters, Mapping) else None
        required = parameters.get("required") if isinstance(parameters, Mapping) else None
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            raise TaskDemoControllerError(
                f"task {task_id!r} lacks a valid public invocation schema"
            )
        for name in required:
            raw_schema = properties.get(name)
            if not isinstance(name, str) or not isinstance(raw_schema, Mapping):
                raise TaskDemoControllerError(
                    f"task {task_id!r} has an invalid required parameter schema"
                )
            converted = _parameter_schema(raw_schema)
            existing = parameter_properties.get(name)
            if existing is not None and existing != converted:
                raise TaskDemoControllerError(
                    f"capability groups incompatible public parameter {name!r}"
                )
            parameter_properties[name] = converted
    return {
        "type": "object",
        "properties": {
            "request": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "enum": covered_ids},
                    "task_parameters": {
                        "type": "object",
                        "properties": parameter_properties,
                        "required": [],
                        "additionalProperties": False,
                    },
                },
                "required": ["task_id", "task_parameters"],
                "additionalProperties": False,
            }
        },
        "required": ["request"],
        "additionalProperties": False,
    }


def _capability_description(
    capability: Mapping[str, Any],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
) -> str:
    capability_id = _text(capability, "capability_id", where="capability")
    description = _text(capability, "description", where=f"capability {capability_id}")
    covered = capability.get("covered_task_ids")
    assert isinstance(covered, list)
    task_labels = []
    for task_id in covered:
        task = tasks_by_id[str(task_id)]
        name = task.get("name")
        task_labels.append(f"{task_id}: {name}" if isinstance(name, str) else str(task_id))
    return f"Capability {capability_id}. {description} Covered public tasks: " + "; ".join(
        task_labels
    )


def run_task_demo_react(
    *,
    client: ToolModelClient,
    robot_configuration_id: str,
    tasks: Sequence[Mapping[str, Any]],
    design: Mapping[str, Any],
    task_id: str,
    public_arguments: Mapping[str, Any],
    invoke: CapabilityInvoker,
    max_turns: int = 8,
    max_capability_calls: int = 8,
) -> TaskDemoControllerResult:
    """Run one fixed high-level-controller loop against one live Harness session."""

    if not isinstance(robot_configuration_id, str) or not robot_configuration_id:
        raise TaskDemoControllerError("robot_configuration_id must be non-empty text")
    if (
        isinstance(max_capability_calls, bool)
        or not isinstance(max_capability_calls, int)
        or max_capability_calls <= 0
    ):
        raise TaskDemoControllerError("max_capability_calls must be a positive integer")
    if not callable(invoke):
        raise TaskDemoControllerError("invoke must be callable")
    tasks_by_id = _task_map(tasks)
    task = tasks_by_id.get(task_id)
    if task is None:
        raise TaskDemoControllerError(f"unknown public task_id {task_id!r}")
    description = _text(task, "description", where=f"task {task_id}")
    expected_arguments = _json_copy(dict(public_arguments), label="public_arguments")
    if not isinstance(expected_arguments, dict) or set(expected_arguments) != {"request"}:
        raise TaskDemoControllerError("public_arguments must contain only request")
    request = expected_arguments.get("request")
    if not isinstance(request, dict) or request.get("task_id") != task_id:
        raise TaskDemoControllerError("public request task_id does not match the Demo task")

    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise TaskDemoControllerError("Capability Design has no public capabilities")
    successful_calls = 0
    capability_calls = 0
    tools: list[ToolSpec] = []

    for index, capability_value in enumerate(capabilities):
        if not isinstance(capability_value, Mapping):
            raise TaskDemoControllerError(f"capabilities[{index}] must be an object")
        capability = copy.deepcopy(dict(capability_value))
        capability_id = _text(capability, "capability_id", where=f"capabilities[{index}]")
        method_name = _text(capability, "method_name", where=f"capabilities[{index}]")
        if method_name == _FINISH_TOOL_NAME:
            raise TaskDemoControllerError(
                f"capability method {_FINISH_TOOL_NAME!r} is reserved by the controller"
            )
        covered = capability.get("covered_task_ids")
        if not isinstance(covered, list):
            raise TaskDemoControllerError(
                f"capability {capability_id!r} lacks covered_task_ids"
            )

        def handle_capability(
            arguments: Mapping[str, Any],
            *,
            selected_capability_id: str = capability_id,
            selected_method_name: str = method_name,
            selected_covered: tuple[str, ...] = tuple(str(item) for item in covered),
        ) -> Mapping[str, Any]:
            nonlocal capability_calls, successful_calls
            if capability_calls >= max_capability_calls:
                raise TaskDemoControllerError("capability-call budget is exhausted")
            capability_calls += 1
            supplied = _json_copy(dict(arguments), label="capability arguments")
            if supplied != expected_arguments:
                raise TaskDemoControllerError(
                    "capability arguments must equal the supplied public request"
                )
            if task_id not in selected_covered:
                raise TaskDemoControllerError(
                    f"capability {selected_capability_id!r} does not cover this public task"
                )
            observation = _json_copy(
                dict(invoke(selected_method_name, supplied)),
                label="capability observation",
            )
            if isinstance(observation, dict) and observation.get("status") == "ABORT":
                raise ReactToolAbort("live capability worker is unavailable")
            if not isinstance(observation, dict) or observation.get("status") != "OK":
                raise TaskDemoControllerError("capability invocation failed")
            successful_calls += 1
            return observation

        tools.append(
            ToolSpec(
                name=method_name,
                description=_capability_description(capability, tasks_by_id),
                input_schema=_capability_tool_schema(capability, tasks_by_id),
                handler=handle_capability,
            )
        )

    def finish_task_demo(arguments: Mapping[str, Any]) -> dict[str, Any]:
        if arguments:
            raise TaskDemoControllerError("finish_task_demo accepts no arguments")
        return {
            "status": "CONTROLLER_FINISHED",
            "successful_capability_calls": successful_calls,
        }

    tools.append(
        ToolSpec(
            name=_FINISH_TOOL_NAME,
            description=(
                "Finish this controller trial after the necessary capability calls. "
                "This does not claim or determine task success."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            handler=finish_task_demo,
            terminal=True,
            available=lambda: successful_calls > 0,
        )
    )

    user_prompt = "PUBLIC_INPUT_JSON:\n" + json.dumps(
        {
            "public_arguments": expected_arguments,
            "robot_configuration_id": robot_configuration_id,
            "task_description": description,
            "task_id": task_id,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    result: ReactResult = run_react(
        client=client,
        stage="task_demo_controller",
        system_prompt=TASK_DEMO_REACT_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        tools=tools,
        max_turns=max_turns,
        max_tool_calls=max_capability_calls + max_turns,
        max_submission_turns=2,
        tool_output_chars=4096,
    )
    return TaskDemoControllerResult(
        status="FINISHED",
        model_turns=result.model_turns,
        tool_calls=result.tool_calls,
        capability_calls=capability_calls,
        trace=result.trace,
    )
