"""Deterministic-budget ReAct Consumer over a typed Capability Router."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .tool_layer import (
    CapabilityRouter,
    SchemaError,
    ValueSchemaError,
    copy_json_value,
    validate_closed_object_schema,
    validate_json_object,
)


class ConsumerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConsumerResult:
    """Consumer-loop outcome only; this is never a Demo task verdict."""

    status: str
    final: Any
    steps_used: int
    trace: tuple[dict[str, Any], ...]


class Consumer(Protocol):
    """Small common surface reserved for later policy/program/planner Consumers."""

    def execute(self, task: Mapping[str, Any], *, seed: int) -> ConsumerResult: ...


ModelCallback = Callable[[dict[str, Any]], Mapping[str, Any]]
_OBSERVATION_SUMMARY_MAX_CHARS = 2048
_EMPTY_PUBLIC_STATE_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _task_view(
    task: Mapping[str, Any],
    public_state_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    if not isinstance(task, Mapping):
        raise ConsumerError("task must be an object")
    task_id = task.get("task_id")
    description = task.get("description")
    allowed = task.get("allowed_capability_ids")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ConsumerError("task_id must be non-empty text")
    if not isinstance(description, str) or not description.strip():
        raise ConsumerError("task description must be non-empty text")
    if not isinstance(allowed, list) or any(not isinstance(item, str) or not item for item in allowed):
        raise ConsumerError("allowed_capability_ids must be an array of non-empty strings")
    if len(allowed) != len(set(allowed)):
        raise ConsumerError("allowed_capability_ids must not contain duplicates")
    try:
        public_state = validate_json_object(
            task.get("public_state", {}),
            public_state_schema,
            label="public_state",
        )
    except ValueSchemaError as exc:
        raise ConsumerError(str(exc)) from None
    public = {
        "task_id": task_id.strip(),
        "description": description.strip(),
        "public_state": public_state,
    }
    return public, tuple(allowed)


def _parse_model_output(value: Any) -> tuple[str, dict[str, Any]]:
    try:
        public = copy_json_value(value)
    except ValueSchemaError as exc:
        raise ConsumerError(str(exc)) from None
    if not isinstance(public, dict):
        raise ConsumerError("model callback output must be an object")
    keys = set(public)
    if keys == {"thought", "action"}:
        kind = "action"
    elif keys == {"thought", "final"}:
        kind = "final"
    else:
        raise ConsumerError("model output must contain thought and exactly one of action/final")
    thought = public["thought"]
    if not isinstance(thought, str):
        raise ConsumerError("model thought must be text")
    if kind == "final":
        return kind, {"thought": thought, "final": public["final"]}
    action = public["action"]
    if not isinstance(action, dict) or set(action) != {"capability_id", "arguments"}:
        raise ConsumerError("model action must contain capability_id and arguments")
    capability_id = action["capability_id"]
    if not isinstance(capability_id, str) or not capability_id:
        raise ConsumerError("action capability_id must be non-empty text")
    if not isinstance(action["arguments"], dict):
        raise ConsumerError("action arguments must be an object")
    return kind, {
        "thought": thought,
        "action": {
            "capability_id": capability_id,
            "arguments": action["arguments"],
        },
    }


def _validate_visible_tools(value: Any, allowed: tuple[str, ...]) -> list[dict[str, Any]]:
    try:
        public = copy_json_value(value)
    except ValueSchemaError as exc:
        raise ConsumerError("Capability Router returned invalid tool schemas") from exc
    if not isinstance(public, list):
        raise ConsumerError("Capability Router visible_tools must return an array")
    names: list[str] = []
    for item in public:
        if not isinstance(item, dict) or set(item) != {"name", "parameters", "returns"}:
            raise ConsumerError("Capability Router returned an invalid tool descriptor")
        name = item["name"]
        if not isinstance(name, str) or not name or name not in allowed or name in names:
            raise ConsumerError("Capability Router returned an invalid visible capability")
        try:
            validate_closed_object_schema(item["parameters"], label=f"tool.{name}.parameters")
            validate_closed_object_schema(item["returns"], label=f"tool.{name}.returns")
        except SchemaError as exc:
            raise ConsumerError("Capability Router returned an invalid public schema") from exc
        names.append(name)
    if tuple(names) != tuple(item for item in allowed):
        raise ConsumerError("Capability Router did not resolve the complete allowed capability set")
    return public


def _router_error(capability_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "status": "ERROR",
        "capability_id": capability_id,
        "error": {"code": code, "message": message},
    }


def _validate_router_envelope(value: Any, capability_id: str) -> dict[str, Any]:
    try:
        public = copy_json_value(value)
    except ValueSchemaError:
        return _router_error(capability_id, "ROUTER_PROTOCOL_ERROR", "Capability Router returned an invalid envelope.")
    if not isinstance(public, dict) or public.get("capability_id") != capability_id:
        return _router_error(capability_id, "ROUTER_PROTOCOL_ERROR", "Capability Router returned an invalid envelope.")
    if public.get("status") == "OK":
        if set(public) != {"status", "capability_id", "result"} or not isinstance(public["result"], dict):
            return _router_error(capability_id, "ROUTER_PROTOCOL_ERROR", "Capability Router returned an invalid envelope.")
        return public
    if public.get("status") == "ERROR":
        if set(public) != {"status", "capability_id", "error"} or not isinstance(public["error"], dict):
            return _router_error(capability_id, "ROUTER_PROTOCOL_ERROR", "Capability Router returned an invalid envelope.")
        error = public["error"]
        if set(error) not in ({"code", "message"}, {"code", "message", "detail"}):
            return _router_error(capability_id, "ROUTER_PROTOCOL_ERROR", "Capability Router returned an invalid envelope.")
        if not isinstance(error.get("code"), str) or not error["code"]:
            return _router_error(capability_id, "ROUTER_PROTOCOL_ERROR", "Capability Router returned an invalid envelope.")
        if not isinstance(error.get("message"), str) or not error["message"]:
            return _router_error(capability_id, "ROUTER_PROTOCOL_ERROR", "Capability Router returned an invalid envelope.")
        if "detail" in error and not isinstance(error["detail"], str):
            return _router_error(capability_id, "ROUTER_PROTOCOL_ERROR", "Capability Router returned an invalid envelope.")
        return public
    return _router_error(capability_id, "ROUTER_PROTOCOL_ERROR", "Capability Router returned an invalid envelope.")


def _observation_summary(observation: Mapping[str, Any]) -> dict[str, Any]:
    public = copy_json_value(dict(observation))
    encoded = json.dumps(public, sort_keys=True, separators=(",", ":"))
    if len(encoded) <= _OBSERVATION_SUMMARY_MAX_CHARS:
        return public
    summary: dict[str, Any] = {
        "status": public.get("status"),
        "capability_id": public.get("capability_id"),
    }
    if isinstance(public.get("error"), dict) and isinstance(public["error"].get("code"), str):
        summary["error"] = {"code": public["error"]["code"]}
    summary["summary"] = "Observation exceeded the public summary limit."
    return summary


class ReActConsumer:
    """One bounded ReAct loop; task correctness remains external to Consumer."""

    def __init__(
        self,
        router: CapabilityRouter,
        model_callback: ModelCallback,
        *,
        max_steps: int,
        public_state_schema: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(router, CapabilityRouter):
            raise ConsumerError("router must implement CapabilityRouter")
        if not callable(model_callback):
            raise ConsumerError("model_callback must be callable")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps <= 0:
            raise ConsumerError("max_steps must be a positive fixed integer")
        try:
            state_schema = validate_closed_object_schema(
                _EMPTY_PUBLIC_STATE_SCHEMA if public_state_schema is None else public_state_schema,
                label="public_state_schema",
            )
        except SchemaError as exc:
            raise ConsumerError(str(exc)) from None
        self._router = router
        self._model_callback = model_callback
        self._max_steps = max_steps
        self._public_state_schema = state_schema

    def execute(self, task: Mapping[str, Any], *, seed: int) -> ConsumerResult:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ConsumerError("seed must be a non-negative integer")
        sanitized_task, allowed = _task_view(task, self._public_state_schema)
        try:
            raw_tools = self._router.visible_tools(allowed)
        except Exception as exc:
            raise ConsumerError("Capability Router could not expose task tools") from exc
        visible_tools = _validate_visible_tools(raw_tools, allowed)
        trace: list[dict[str, Any]] = [
            {
                "event": "consumer_start",
                "consumer": "react",
                "seed": seed,
                "step_budget": self._max_steps,
                "task": copy_json_value(sanitized_task),
                "tools": copy_json_value(visible_tools),
            }
        ]
        observations: list[dict[str, Any]] = []

        for step in range(1, self._max_steps + 1):
            callback_input = {
                "seed": seed,
                "step": step,
                "step_budget": self._max_steps,
                "task": copy_json_value(sanitized_task),
                "tools": copy_json_value(visible_tools),
                "observation_summaries": copy_json_value(observations),
            }
            try:
                raw_output = self._model_callback(callback_input)
            except Exception:
                observation = {"status": "ERROR", "code": "MODEL_CALLBACK_ERROR"}
                trace.append({"event": "model_error", "step": step, "observation": observation})
                trace.append({"event": "consumer_stop", "reason": "MODEL_ERROR", "steps_used": step})
                return ConsumerResult("MODEL_ERROR", None, step, tuple(trace))

            try:
                kind, model_output = _parse_model_output(raw_output)
            except ConsumerError:
                observation = {"status": "ERROR", "code": "MODEL_OUTPUT_ERROR"}
                observations.append({"step": step, **observation})
                trace.append(
                    {
                        "event": "react_step",
                        "step": step,
                        "model_output": {"status": "REJECTED"},
                        "observation": observation,
                    }
                )
                continue

            if kind == "final":
                trace.append({"event": "react_final", "step": step, "model_output": model_output})
                return ConsumerResult("FINAL", copy_json_value(model_output["final"]), step, tuple(trace))

            action = model_output["action"]
            capability_id = action["capability_id"]
            arguments = action["arguments"]
            public_model_output = copy_json_value(model_output)
            try:
                raw_observation = self._router.invoke(
                    capability_id,
                    copy_json_value(arguments),
                    allowed_capability_ids=allowed,
                )
            except Exception:
                observation = _router_error(
                    capability_id,
                    "ROUTER_EXCEPTION",
                    "Capability Router invocation failed.",
                )
            else:
                observation = _validate_router_envelope(raw_observation, capability_id)
            if (
                observation.get("status") == "ERROR"
                and isinstance(observation.get("error"), dict)
                and observation["error"].get("code") in {"ARGUMENT_SCHEMA", "UNAUTHORIZED_TOOL"}
            ):
                public_model_output = {
                    "thought": model_output["thought"],
                    "action": {
                        "capability_id": capability_id,
                        "arguments_status": "REJECTED",
                    },
                }
            observations.append({"step": step, **_observation_summary(observation)})
            trace.append(
                {
                    "event": "react_step",
                    "step": step,
                    "model_output": public_model_output,
                    "observation": observation,
                }
            )

        trace.append({"event": "consumer_stop", "reason": "STEP_LIMIT", "steps_used": self._max_steps})
        return ConsumerResult("STEP_LIMIT", None, self._max_steps, tuple(trace))
