from __future__ import annotations

import json

import pytest

from autoadapter2.consumer import (
    CapabilityToolLayer,
    ConsumerError,
    PromotedTool,
    PromotionError,
    ReActConsumer,
)


NUMBER_OBJECT = {
    "type": "object",
    "properties": {
        "value": {"type": "number", "minimum": -100.0, "maximum": 100.0},
    },
    "required": ["value"],
    "additionalProperties": False,
}
EMPTY_OBJECT = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}
IMPLEMENTATION_HASH = "a" * 64
VALIDATION_HASH = "b" * 64


def promoted(capability_id, entrypoint, *, input_schema=NUMBER_OBJECT, output_schema=NUMBER_OBJECT):
    return PromotedTool(
        capability_id=capability_id,
        input_schema=input_schema,
        output_schema=output_schema,
        entrypoint=entrypoint,
        implementation_manifest_sha256=IMPLEMENTATION_HASH,
        validation_report_sha256=VALIDATION_HASH,
    )


def task(*, allowed=("increment", "double")) -> dict:
    return {
        "task_id": "task-arithmetic",
        "description": "Transform the public number.",
        "public_state": {"value": 1.0},
        "allowed_capability_ids": list(allowed),
        "private_criterion": {"expected": 4.0},
        "validation_suite": "must-never-be-visible",
    }


def layer(calls=None, *, double=None, increment=None) -> CapabilityToolLayer:
    calls = [] if calls is None else calls

    def default_increment(arguments):
        calls.append(("increment", dict(arguments)))
        return {"value": arguments["value"] + 1}

    def default_double(arguments):
        calls.append(("double", dict(arguments)))
        return {"value": arguments["value"] * 2}

    return CapabilityToolLayer(
        [
            promoted("increment", increment or default_increment),
            promoted("double", double or default_double),
        ]
    )


def consumer(router, callback, *, max_steps=4):
    return ReActConsumer(
        router,
        callback,
        max_steps=max_steps,
        public_state_schema=NUMBER_OBJECT,
    )


def error_code(request) -> str:
    return request["observation_summaries"][-1]["error"]["code"]


def test_two_tool_calls_then_final() -> None:
    calls = []

    def callback(request):
        if request["step"] == 1:
            return {
                "thought": "First increment.",
                "action": {"capability_id": "increment", "arguments": {"value": 1}},
            }
        if request["step"] == 2:
            assert request["observation_summaries"][-1]["result"] == {"value": 2}
            return {
                "thought": "Then double.",
                "action": {"capability_id": "double", "arguments": {"value": 2}},
            }
        return {"thought": "Task execution is complete.", "final": {"result": 4}}

    result = consumer(layer(calls), callback).execute(task(), seed=7)
    assert result.status == "FINAL"
    assert result.final == {"result": 4}
    assert result.steps_used == 3
    assert calls == [("increment", {"value": 1}), ("double", {"value": 2})]
    assert [event["event"] for event in result.trace] == [
        "consumer_start",
        "react_step",
        "react_step",
        "react_final",
    ]
    assert "verdict" not in result.__dict__
    assert "implementation_manifest_sha256" not in json.dumps(result.trace)


def test_unauthorized_tool_is_rejected_and_not_executed() -> None:
    calls = []
    requests = []

    def callback(request):
        requests.append(request)
        if request["step"] == 1:
            return {
                "thought": "Try a tool outside this task.",
                "action": {"capability_id": "double", "arguments": {"value": 2}},
            }
        assert error_code(request) == "UNAUTHORIZED_TOOL"
        return {"thought": "Stop after rejection.", "final": "done"}

    result = consumer(layer(calls), callback, max_steps=3).execute(
        task(allowed=("increment",)), seed=1
    )
    assert result.status == "FINAL"
    assert calls == []
    assert [tool["name"] for tool in requests[0]["tools"]] == ["increment"]
    assert result.trace[1]["observation"]["error"]["code"] == "UNAUTHORIZED_TOOL"


def test_bad_arguments_become_observation_and_react_can_recover() -> None:
    calls = []

    def callback(request):
        if request["step"] == 1:
            return {
                "thought": "Bad first try.",
                "action": {"capability_id": "increment", "arguments": {"value": "one"}},
            }
        if request["step"] == 2:
            assert error_code(request) == "ARGUMENT_SCHEMA"
            return {
                "thought": "Use a number.",
                "action": {"capability_id": "increment", "arguments": {"value": 1}},
            }
        return {"thought": "Finish.", "final": 2}

    result = consumer(layer(calls), callback, max_steps=3).execute(task(), seed=2)
    assert result.status == "FINAL"
    assert calls == [("increment", {"value": 1})]
    assert result.trace[1]["observation"]["error"]["code"] == "ARGUMENT_SCHEMA"


def test_hidden_extra_argument_is_rejected_without_entering_trace() -> None:
    calls = []

    def callback(request):
        if request["step"] == 1:
            return {
                "thought": "Try an invalid extra.",
                "action": {
                    "capability_id": "increment",
                    "arguments": {"value": 1, "private_criterion": "hidden-input-value"},
                },
            }
        return {"thought": "Stop.", "final": "rejected"}

    result = consumer(layer(calls), callback, max_steps=2).execute(task(), seed=8)
    rendered = json.dumps(result.trace)
    assert calls == []
    assert result.trace[1]["observation"]["error"]["code"] == "ARGUMENT_SCHEMA"
    assert "private_criterion" not in rendered
    assert "hidden-input-value" not in rendered


def test_fixed_step_budget_stops_react_loop() -> None:
    calls = []

    def callback(_request):
        return {
            "thought": "Keep acting.",
            "action": {"capability_id": "increment", "arguments": {"value": 1}},
        }

    result = consumer(layer(calls), callback, max_steps=2).execute(task(), seed=3)
    assert result.status == "STEP_LIMIT"
    assert result.final is None
    assert result.steps_used == 2
    assert len(calls) == 2
    assert result.trace[-1] == {"event": "consumer_stop", "reason": "STEP_LIMIT", "steps_used": 2}


def test_tool_exception_is_public_observation_without_exception_text() -> None:
    def broken(_arguments):
        raise RuntimeError("private_harness secret-token validation_suite")

    def callback(request):
        if request["step"] == 1:
            return {
                "thought": "Call it.",
                "action": {"capability_id": "double", "arguments": {"value": 2}},
            }
        assert error_code(request) == "TOOL_EXCEPTION"
        return {"thought": "Recover without it.", "final": "recovered"}

    result = consumer(layer(double=broken), callback, max_steps=3).execute(task(), seed=4)
    serialized = json.dumps(result.trace, sort_keys=True)
    assert result.status == "FINAL"
    assert result.trace[1]["observation"]["error"]["code"] == "TOOL_EXCEPTION"
    assert "secret-token" not in serialized
    assert "private_harness" not in serialized
    assert "validation_suite" not in serialized


def test_output_extra_private_field_is_rejected_and_not_exposed() -> None:
    def leaking(arguments):
        return {"value": arguments["value"], "private_harness": "hidden-output-value"}

    def callback(request):
        if request["step"] == 1:
            return {
                "thought": "Call public tool.",
                "action": {"capability_id": "double", "arguments": {"value": 2}},
            }
        assert error_code(request) == "OUTPUT_SCHEMA"
        return {"thought": "Recover.", "final": "output-rejected"}

    result = consumer(layer(double=leaking), callback, max_steps=2).execute(task(), seed=5)
    rendered = json.dumps(result.trace, sort_keys=True)
    assert result.final == "output-rejected"
    assert "private_harness" not in rendered
    assert "hidden-output-value" not in rendered


def test_non_json_tool_output_is_rejected() -> None:
    def non_json(_arguments):
        return {"value": object()}

    def callback(request):
        if request["step"] == 1:
            return {
                "thought": "Call.",
                "action": {"capability_id": "double", "arguments": {"value": 2}},
            }
        assert error_code(request) == "OUTPUT_SCHEMA"
        return {"thought": "Done.", "final": "rejected"}

    result = consumer(layer(double=non_json), callback, max_steps=2).execute(task(), seed=6)
    assert result.status == "FINAL"
    assert result.final == "rejected"


def test_trace_and_callback_inputs_drop_private_task_namespaces() -> None:
    requests = []

    def callback(request):
        requests.append(request)
        return {"thought": "No action is needed.", "final": "done"}

    result = consumer(layer(), callback).execute(task(), seed=10)
    rendered = json.dumps({"trace": result.trace, "requests": requests}, sort_keys=True)
    assert "private_criterion" not in rendered
    assert "must-never-be-visible" not in rendered
    assert "validation_suite" not in rendered


def test_same_seed_and_fixed_callback_are_deterministic() -> None:
    def callback(request):
        if request["step"] == 1:
            return {
                "thought": f"seed={request['seed']}",
                "action": {"capability_id": "increment", "arguments": {"value": 1}},
            }
        return {"thought": "Finish.", "final": request["observation_summaries"][-1]["result"]}

    first = consumer(layer(), callback, max_steps=3).execute(task(), seed=99)
    second = consumer(layer(), callback, max_steps=3).execute(task(), seed=99)
    assert first == second


def test_bad_hash_and_unpromoted_object_cannot_construct_layer() -> None:
    with pytest.raises(PromotionError, match="lowercase SHA-256"):
        PromotedTool(
            capability_id="increment",
            input_schema=NUMBER_OBJECT,
            output_schema=NUMBER_OBJECT,
            entrypoint=lambda arguments: arguments,
            implementation_manifest_sha256="bad",
            validation_report_sha256=VALIDATION_HASH,
        )
    with pytest.raises(PromotionError, match="only framework-created PromotedTool"):
        CapabilityToolLayer([{"capability_id": "increment"}])


@pytest.mark.parametrize("field", ["input_schema", "output_schema"])
def test_root_non_object_schema_is_rejected(field) -> None:
    values = {
        "capability_id": "increment",
        "input_schema": NUMBER_OBJECT,
        "output_schema": NUMBER_OBJECT,
        "entrypoint": lambda arguments: arguments,
        "implementation_manifest_sha256": IMPLEMENTATION_HASH,
        "validation_report_sha256": VALIDATION_HASH,
    }
    values[field] = {"type": "number"}
    with pytest.raises(PromotionError, match="root schema type must be object"):
        PromotedTool(**values)


def test_unsupported_union_type_is_reported_as_promotion_error() -> None:
    schema = {
        "type": "object",
        "properties": {"value": {"type": ["number", "null"]}},
        "required": ["value"],
        "additionalProperties": False,
    }
    with pytest.raises(PromotionError, match="one supported JSON type"):
        promoted("increment", lambda arguments: arguments, input_schema=schema)


def test_public_state_defaults_to_closed_empty_object() -> None:
    callback = lambda _request: {"thought": "Done.", "final": "done"}
    plain = ReActConsumer(layer(), callback, max_steps=1)
    no_state_task = task()
    no_state_task.pop("public_state")
    assert plain.execute(no_state_task, seed=1).status == "FINAL"
    with pytest.raises(ConsumerError, match="additional properties"):
        plain.execute(task(), seed=1)


def test_fake_capability_router_can_drive_react() -> None:
    class FakeRouter:
        def visible_tools(self, allowed_capability_ids):
            assert tuple(allowed_capability_ids) == ("increment",)
            return [{"name": "increment", "parameters": NUMBER_OBJECT, "returns": NUMBER_OBJECT}]

        def invoke(self, capability_id, arguments, *, allowed_capability_ids):
            assert capability_id == "increment"
            assert tuple(allowed_capability_ids) == ("increment",)
            value = arguments["value"]
            arguments["router_private_mutation"] = "must-not-enter-trace"
            return {"status": "OK", "capability_id": capability_id, "result": {"value": value + 1}}

    def callback(request):
        if request["step"] == 1:
            return {
                "thought": "Use protocol router.",
                "action": {"capability_id": "increment", "arguments": {"value": 1}},
            }
        return {"thought": "Finish.", "final": request["observation_summaries"][-1]["result"]}

    result = consumer(FakeRouter(), callback, max_steps=2).execute(
        task(allowed=("increment",)), seed=11
    )
    assert result.status == "FINAL"
    assert result.final == {"value": 2}
    assert "router_private_mutation" not in json.dumps(result.trace)
    assert "must-not-enter-trace" not in json.dumps(result.trace)
