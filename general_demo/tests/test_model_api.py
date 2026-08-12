from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoadapter2.foundation.errors import ContractError
from autoadapter2.generation import ModelApiClient, ModelApiConfig
from autoadapter2.generation import model_api
from autoadapter2.generation.model_api import IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT


class _Response:
    def __init__(self, payload=None):
        self.payload = payload or {"choices": [{"message": {"content": '{"ok":true}'}}]}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_model_api_uses_platform_default_ca_when_not_overridden(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    calls: list[dict[str, object]] = []

    def context_factory(*_args, **kwargs):
        calls.append(dict(kwargs))
        return object()

    monkeypatch.setattr(model_api.ssl, "create_default_context", context_factory)
    monkeypatch.setattr(
        model_api.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(),
    )

    client = ModelApiClient(ModelApiConfig(api_key="test-only"))
    assert client.generate_json("smoke", "Return JSON.", {}) == {"ok": True}
    assert calls == [{}]


def test_model_api_repair_requires_closed_raw_python_and_describes_validation_a_contract(monkeypatch):
    captured: dict[str, object] = {}
    source = "def capability_example():\n    return 1\n"
    payload = {
        "choices": [{
            "message": {
                "content": json.dumps({"capability.py": source, "llm_calls": 1}),
            },
        }],
    }

    def urlopen(request, **_kwargs):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response(payload)

    monkeypatch.setattr(model_api.urllib.request, "urlopen", urlopen)
    client = ModelApiClient(ModelApiConfig(api_key="test-only"))
    assert client.repair({"diagnostics": []}) == {"capability.py": source, "llm_calls": 1}
    instruction = captured["body"]["messages"][1]["content"]  # type: ignore[index]
    for phrase in (
        "complete raw parseable Python source", "no Markdown fences", "imports only math, time, or numpy",
        "private non-dunder helpers", "approved math/time/numpy members", "No dynamic import",
    ):
        assert phrase in instruction
    assert IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT in instruction

    fenced_payload = {
        "choices": [{
            "message": {
                "content": json.dumps({"capability.py": "```python\n" + source + "```", "llm_calls": 1}),
            },
        }],
    }
    monkeypatch.setattr(model_api.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(fenced_payload))
    with pytest.raises(ContractError, match="raw source"):
        client.repair({"diagnostics": []})


def test_model_api_repair_renders_only_supplied_public_bundle_facts(monkeypatch):
    source = "def capability_example():\n    return 1\n"
    payload = {
        "choices": [{
            "message": {
                "content": json.dumps({"capability.py": source, "llm_calls": 1}),
            },
        }],
    }
    captured: list[dict[str, object]] = []

    def urlopen(request, **_kwargs):
        body = json.loads(request.data.decode("utf-8"))
        captured.append(body)
        return _Response(payload)

    monkeypatch.setattr(model_api.urllib.request, "urlopen", urlopen)
    client = ModelApiClient(ModelApiConfig(api_key="test-only"))
    unitree_bundle = {
        "sdk_implementation_projection": {
            "sdk_entry_id": "unitree-sdk2-go2-lowlevel",
            "sdk_entry_version": "pinned",
            "permitted_types": [
                "ChannelPublisher", "ChannelSubscriber", "LowCmd_", "LowState_", "SportModeState_",
            ],
            "permitted_factories": ["unitree_go_msg_dds__LowCmd_"],
            "permitted_objects": [{
                "object_type": "ChannelPublisher",
                "constructor": {"parameters": ["topic", "message_type"]},
                "operations": ["Init", "Write"],
                "write_contract": {"field_container": "motor_cmd"},
            }],
            "topics": {"command": "rt/lowcmd"},
            "command": {"fields": [{"name": "q", "unit": "rad"}]},
        },
    }
    so_bundle = {
        "sdk_implementation_projection": {
            "sdk_entry_id": "lerobot-so101-follower",
            "permitted_operations": ["send_action", "get_observation"],
            "action_fields": ["shoulder_pan.pos"],
            "observation_fields": ["shoulder_pan.pos", "public_task_state"],
            "units": {"shoulder_pan.pos": "degree"},
        },
    }

    assert client.repair({"diagnostics": [], "implementation_bundle": unitree_bundle}) == {
        "capability.py": source, "llm_calls": 1,
    }
    assert client.repair({"diagnostics": [], "implementation_bundle": so_bundle}) == {
        "capability.py": source, "llm_calls": 1,
    }

    unitree_instruction = captured[0]["messages"][-1]["content"]  # type: ignore[index]
    so_instruction = captured[1]["messages"][-1]["content"]  # type: ignore[index]
    assert "`_sdk` is a module-like injected facade" in unitree_instruction
    assert "module-global `_sdk`" in unitree_instruction
    assert "Do not call `ChannelFactoryInitialize`" in unitree_instruction
    assert "second SDK" in unitree_instruction
    assert "PUBLIC IMPLEMENTATION BUNDLE" in unitree_instruction
    assert IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT in unitree_instruction
    assert IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT in so_instruction
    assert "unitree_go_msg_dds__LowCmd_" in unitree_instruction
    assert "rt/lowcmd" in unitree_instruction
    assert "motor_cmd" in unitree_instruction
    assert "q" in unitree_instruction
    assert "made_up_member" not in unitree_instruction
    assert "PUBLIC IMPLEMENTATION BUNDLE" not in so_instruction
    assert "send_action" not in so_instruction
    assert "get_observation" not in so_instruction
    assert "shoulder_pan.pos" not in so_instruction
    assert "degree" not in so_instruction
    assert "unitree_go_msg_dds__LowCmd_" not in so_instruction
    assert "made_up_member" not in so_instruction


def test_public_implementation_bundle_renderer_is_deterministic_and_does_not_invent_facts():
    first = {
        "robot_implementation_facts": {"units": {"joint": "rad"}, "fields": ["q"]},
        "sdk_implementation_projection": {
            "permitted_factories": ["make_command"],
            "permitted_types": ["Command"],
        },
    }
    second = {
        "sdk_implementation_projection": {
            "permitted_types": ["Command"],
            "permitted_factories": ["make_command"],
        },
        "robot_implementation_facts": {"fields": ["q"], "units": {"joint": "rad"}},
    }
    rendered_first = model_api._render_public_implementation_bundle(first)
    rendered_second = model_api._render_public_implementation_bundle(second)
    assert rendered_first == rendered_second
    assert '"make_command"' in rendered_first
    assert '"Command"' in rendered_first
    assert '"missing_factory"' not in rendered_first


def test_initial_stage2_request_includes_public_controller_family_experience(monkeypatch):
    source = "def capability_example():\n    return 1\n"
    payload = {
        "choices": [{
            "message": {
                "content": json.dumps({"action": "submit", "capability.py": source}),
            },
        }],
    }
    captured: list[dict[str, object]] = []

    def urlopen(request, **_kwargs):
        captured.append(json.loads(request.data.decode("utf-8")))
        return _Response(payload)

    monkeypatch.setattr(model_api.urllib.request, "urlopen", urlopen)
    template = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "config/first_g2_demo/robots/so-arm101.json"
        ).read_text(encoding="utf-8")
    )
    bundle = {
        "sdk_implementation_projection": {
            "permitted_operations": ["send_action", "get_observation"],
            "field_order": ["joint_1.pos", "gripper.pos"],
        },
        "robot_implementation_facts": {
            "kinematics": {"joint_order": ["joint_1", "gripper"]},
        },
        "implementation_experience": template["implementation_experience"],
    }

    client = ModelApiClient(ModelApiConfig(api_key="test-only"))
    client.generate_json("stage2", "STAGE2_GUIDANCE_MARKER", {"implementation_bundle": bundle})

    request_text = captured[0]["messages"][-1]["content"]  # type: ignore[index]
    assert '"implementation_experience": [' in request_text
    assert '"controller_family": "serial_arm_dls_closed_loop"' in request_text
    assert "fresh observation" in request_text
    assert "state-dependent send" in request_text
    assert "at least two" in request_text.lower()
    for phrase in (
        '"max_feedback_cycles": 32',
        '"minimum_feedback_cycles": 2',
        '"finite_difference_rad": 1e-05',
        '"damping": 0.02',
        '"gain": 0.7',
        '"max_joint_step_rad": 0.12',
        "exclude the `gripper` joint",
        "current_obs + (desired_degree - float(current_obs))",
        "current_gripper_obs + (desired_gripper - float(current_gripper_obs))",
        "range(max_feedback_cycles)",
        "one-read-many-write",
    ):
        assert phrase in request_text


def test_implementation_agent_replays_public_stage2_and_repair_history(monkeypatch):
    stage2_source = "def capability_example():\n    return 1\n"
    repair_one_source = "def capability_example():\n    return 2\n"
    repair_two_source = "def capability_example():\n    return 3\n"
    fresh_source = "def capability_example():\n    return 4\n"
    responses = iter([
        {"action": "submit", "capability.py": stage2_source},
        {"capability.py": repair_one_source, "llm_calls": 1},
        {"capability.py": repair_two_source, "llm_calls": 1},
        {"capability.py": fresh_source, "llm_calls": 1},
    ])
    captured: list[dict[str, object]] = []

    def urlopen(request, **_kwargs):
        captured.append(json.loads(request.data.decode("utf-8")))
        return _Response({
            "choices": [{"message": {"content": json.dumps(next(responses))}}],
        })

    monkeypatch.setattr(model_api.urllib.request, "urlopen", urlopen)
    bundle = {
        "sdk_implementation_projection": {
            "sdk_entry_id": "unitree-sdk2-go2-lowlevel",
            "permitted_types": ["LowCmd_"],
        },
        "robot_implementation_facts": {"units": {"joint": "rad"}},
    }
    client = ModelApiClient(ModelApiConfig(api_key="test-only"))
    client.generate_json(
        "stage2",
        "STAGE2_PROMPT",
        {
            "capability_design": {"capability_id": "public-stage2-design"},
            "implementation_bundle": bundle,
            "private_validation_threshold": "PRIVATE_THRESHOLD_MUST_NOT_LEAK",
        },
    )
    client.repair({
        "repair_index": 1,
        "capability.py": stage2_source,
        "implementation_bundle": bundle,
        "diagnostics": [{"message": "public-repair-one-diagnostic"}],
        "private_validation_criteria": "PRIVATE_CRITERIA_MUST_NOT_LEAK",
    })
    client.repair({
        "repair_index": 2,
        "capability.py": repair_one_source,
        "implementation_bundle": bundle,
        "diagnostics": [{"message": "public-repair-two-diagnostic"}],
        "private_measurement": "PRIVATE_MEASUREMENT_MUST_NOT_LEAK",
    })

    stage2_body, repair_one_body, repair_two_body = captured[:3]
    assert len(stage2_body["messages"]) == 2  # type: ignore[index]
    assert [message["role"] for message in repair_one_body["messages"]] == [  # type: ignore[index]
        "system", "user", "assistant", "user",
    ]
    assert [message["role"] for message in repair_two_body["messages"]] == [  # type: ignore[index]
        "system", "user", "assistant", "user", "assistant", "user",
    ]
    assert stage2_body["messages"][0] == repair_one_body["messages"][0]  # type: ignore[index]
    assert repair_one_body["messages"][0] == repair_two_body["messages"][0]  # type: ignore[index]
    assert "PUBLIC IMPLEMENTATION BUNDLE" in stage2_body["messages"][-1]["content"]  # type: ignore[index]
    assert IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT in stage2_body["messages"][-1]["content"]  # type: ignore[index]
    assert IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT in repair_one_body["messages"][-1]["content"]  # type: ignore[index]
    assert IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT in repair_two_body["messages"][-1]["content"]  # type: ignore[index]
    assert sum(
        "PUBLIC IMPLEMENTATION BUNDLE" in message["content"]
        for message in repair_one_body["messages"]  # type: ignore[index]
    ) == 1
    assert "PUBLIC IMPLEMENTATION BUNDLE" not in repair_one_body["messages"][-1]["content"]  # type: ignore[index]
    assert "capability_design" not in repair_one_body["messages"][-1]["content"]  # type: ignore[index]
    assert '"implementation_bundle"' not in repair_one_body["messages"][-1]["content"]  # type: ignore[index]
    assert json.loads(repair_one_body["messages"][2]["content"])["capability.py"] == stage2_source  # type: ignore[index]
    assert "public-repair-one-diagnostic" in repair_two_body["messages"][3]["content"]  # type: ignore[index]
    assert json.loads(repair_two_body["messages"][4]["content"])["capability.py"] == repair_one_source  # type: ignore[index]
    for body in (stage2_body, repair_one_body, repair_two_body):
        body_text = json.dumps(body, sort_keys=True)
        assert "PRIVATE_THRESHOLD_MUST_NOT_LEAK" not in body_text
        assert "PRIVATE_CRITERIA_MUST_NOT_LEAK" not in body_text
        assert "PRIVATE_MEASUREMENT_MUST_NOT_LEAK" not in body_text

    fresh_client = ModelApiClient(ModelApiConfig(api_key="test-only"))
    fresh_client.repair({
        "repair_index": 1,
        "capability.py": repair_two_source,
        "implementation_bundle": bundle,
        "diagnostics": [{"message": "fresh-public-diagnostic"}],
    })
    fresh_body = captured[3]
    assert [message["role"] for message in fresh_body["messages"]] == ["system", "user"]  # type: ignore[index]
    assert stage2_source not in fresh_body["messages"][-1]["content"]  # type: ignore[index]
    assert repair_one_source not in fresh_body["messages"][-1]["content"]  # type: ignore[index]


def test_implementation_history_is_bounded_but_keeps_initial_and_current_state(monkeypatch):
    sources = [f"def capability_example():\n    return {index}\n" for index in range(8)]
    responses = iter(
        [{"action": "submit", "capability.py": sources[0]}]
        + [{"capability.py": source, "llm_calls": 1} for source in sources[1:]]
    )
    captured: list[dict[str, object]] = []

    def urlopen(request, **_kwargs):
        captured.append(json.loads(request.data.decode("utf-8")))
        return _Response({"choices": [{"message": {"content": json.dumps(next(responses))}}]})

    monkeypatch.setattr(model_api.urllib.request, "urlopen", urlopen)
    client = ModelApiClient(ModelApiConfig(api_key="test-only"))
    bundle = {"sdk_implementation_projection": {"sdk_entry_id": "public-test"}}
    client.generate_json(
        "stage2",
        "STAGE2_INITIAL_PUBLIC_CONTEXT",
        {"implementation_bundle": bundle, "capability_design": {"public": "design"}},
    )
    for index in range(1, 8):
        client.repair({
            "repair_index": index,
            "capability.py": sources[index - 1],
            "implementation_bundle": bundle,
            "diagnostics": [{"code": "PUBLIC", "message": f"diag-{index}"}],
        })

    assert len(client._implementation_history) == 2 + 2 * model_api._IMPLEMENTATION_HISTORY_TURN_PAIRS
    final_body = captured[-1]
    assert len(final_body["messages"]) == 1 + len(client._implementation_history) + 1  # type: ignore[arg-type]
    final_text = final_body["messages"][-1]["content"]  # type: ignore[index]
    final_inputs = json.loads(final_text.split("INPUT_JSON:\n", 1)[1])
    assert final_inputs["capability.py"] == sources[-2]
    assert final_inputs["diagnostics"] == [{"code": "PUBLIC", "message": "diag-7"}]
    body_text = json.dumps(final_body, sort_keys=True)
    assert "STAGE2_INITIAL_PUBLIC_CONTEXT" in body_text
    assert "return 2" not in body_text
    assert "return 4" in body_text and "return 5" in body_text and "return 6" in body_text


def test_model_api_retries_one_transient_transport_failure_without_duplicate_history(monkeypatch):
    source = "def capability_example():\n    return 1\n"
    payload = {"choices": [{"message": {"content": json.dumps({"capability.py": source, "llm_calls": 1})}}]}
    attempts: list[bytes] = []

    def urlopen(request, **_kwargs):
        attempts.append(request.data)
        if len(attempts) == 1:
            raise OSError("temporary transport")
        return _Response(payload)

    monkeypatch.setattr(model_api.urllib.request, "urlopen", urlopen)
    client = ModelApiClient(ModelApiConfig(api_key="test-only"))
    assert client.repair({"capability.py": source, "diagnostics": []}) == {
        "capability.py": source,
        "llm_calls": 1,
    }
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert len(client.calls) == 1
    assert len(client._implementation_history) == 2


def test_model_api_does_not_retry_semantic_non_json_model_content(monkeypatch):
    attempts = 0

    def urlopen(_request, **_kwargs):
        nonlocal attempts
        attempts += 1
        return _Response({"choices": [{"message": {"content": "not JSON"}}]})

    monkeypatch.setattr(model_api.urllib.request, "urlopen", urlopen)
    client = ModelApiClient(ModelApiConfig(api_key="test-only"))
    with pytest.raises(ContractError, match="non-JSON"):
        client.repair({"capability.py": "def capability_example():\n    return 1\n", "diagnostics": []})
    assert attempts == 1
    assert client.calls == []
    assert client._implementation_history == []


def test_stage1_blue_and_react_are_isolated_from_implementation_conversation(monkeypatch):
    responses = iter([
        {"stage": "stage1"},
        {"stage": "blue"},
        {"stage": "react"},
        {"stage": "stage2"},
    ])
    captured: list[dict[str, object]] = []

    def urlopen(request, **_kwargs):
        captured.append(json.loads(request.data.decode("utf-8")))
        return _Response({
            "choices": [{"message": {"content": json.dumps(next(responses))}}],
        })

    monkeypatch.setattr(model_api.urllib.request, "urlopen", urlopen)
    bundle = {"sdk_implementation_projection": {"permitted_types": ["PublicType"]}}
    client = ModelApiClient(ModelApiConfig(api_key="test-only"))
    client.generate_json("stage1", "STAGE1_MARKER", {"implementation_bundle": bundle})
    client.generate_json("blue_line", "BLUE_MARKER", {"implementation_bundle": bundle})
    client.react({"implementation_bundle": bundle})
    client.generate_json("stage2", "STAGE2_MARKER", {"implementation_bundle": bundle})

    for body in captured[:3]:
        text = json.dumps(body, sort_keys=True)
        assert "PUBLIC IMPLEMENTATION BUNDLE" not in text
    stage2_text = json.dumps(captured[3], sort_keys=True)
    assert "PUBLIC IMPLEMENTATION BUNDLE" in stage2_text
    assert "STAGE1_MARKER" not in stage2_text
    assert "BLUE_MARKER" not in stage2_text
