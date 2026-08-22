from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from autoadapter2.b2.capability_adapter import CapabilityAdapter
from autoadapter2.b2.recap import RecapBudgets, run_recap


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SO101_DESIGN = (
    REPOSITORY_ROOT
    / "experiment"
    / "b2_recap"
    / "reference_validation"
    / "resolved"
    / "robotstudio_so101"
    / "capability_design.json"
)


def _design() -> dict[str, Any]:
    return json.loads(SO101_DESIGN.read_text(encoding="utf-8"))


def _capability(name: str, request: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "capability", "capability_name": name, "request": request}


def _output(*steps: dict[str, Any], summary: str = "Use the next public step.") -> dict:
    return {"reasoning_summary": summary, "subtasks": list(steps)}


class ScriptedModel:
    def __init__(
        self,
        responses: list[dict[str, Any]],
        invocations: list[tuple[str, dict[str, Any]]],
        expected_invocation_counts: list[int] | None = None,
    ) -> None:
        self.responses = responses
        self.invocations = invocations
        self.expected_invocation_counts = expected_invocation_counts
        self.calls: list[dict[str, Any]] = []

    def generate_recap_json(self, **kwargs: Any) -> dict[str, Any]:
        call_index = len(self.calls)
        if self.expected_invocation_counts is not None:
            assert len(self.invocations) == self.expected_invocation_counts[call_index]
        self.calls.append(json.loads(json.dumps(kwargs)))
        return self.responses[call_index]


def test_recap_executes_only_the_head_then_refines_the_remainder() -> None:
    invocations: list[tuple[str, dict[str, Any]]] = []

    def invoke(method_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        invocations.append((method_name, arguments))
        return {"operation": {"status": "EXECUTED", "sequence": len(invocations)}}

    first = _capability(
        "set_gripper_opening",
        {"opening_fraction": 1.0, "max_duration_s": 1.0},
    )
    second = _capability(
        "move_end_effector_to_position",
        {"target_position_m": [0.1, 0.2, 0.3], "max_duration_s": 2.0},
    )
    model = ScriptedModel(
        [_output(first, second), _output(second), _output()],
        invocations,
        expected_invocation_counts=[0, 1, 2],
    )

    result = run_recap(
        public_task={"task_template_id": "so-sequence", "objective": "Move safely."},
        adapter=CapabilityAdapter(_design(), invoke),
        model=model,
    )

    assert result.status == "CONTROLLER_FINISHED"
    assert result.model_calls == 3
    assert result.capability_calls == 2
    assert [call[0] for call in invocations] == [
        "set_gripper_opening",
        "move_end_effector_to_position",
    ]
    assert invocations[0][1] == {
        "request": {"opening_fraction": 1.0, "max_duration_s": 1.0}
    }
    assert [entry["capability_name"] for entry in result.trace if entry["capability_name"]] == [
        "set_gripper_opening",
        "move_end_effector_to_position",
    ]
    assert result.trace[-1]["controller_self_reported_completion"] is True
    assert result.context_tree["nodes"][0]["completed"] is True

    result_fields = asdict(result)
    for forbidden in ("task_success", "validation_passed", "harness_verdict", "verdict"):
        assert forbidden not in result_fields


def test_recap_descends_recursively_and_returns_completion_to_parent() -> None:
    invocations: list[tuple[str, dict[str, Any]]] = []

    def invoke(method_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        invocations.append((method_name, arguments))
        return {"operation": {"status": "EXECUTED"}}

    leaf = _capability(
        "set_gripper_opening",
        {"opening_fraction": 0.25, "max_duration_s": 1.0},
    )
    stale_sibling = _capability(
        "move_end_effector_to_position",
        {"target_position_m": [0.1, 0.1, 0.1], "max_duration_s": 2.0},
    )
    revised_sibling = _capability(
        "move_end_effector_to_position",
        {"target_position_m": [0.3, 0.2, 0.1], "max_duration_s": 2.0},
    )
    model = ScriptedModel(
        [
            _output(
                {"kind": "subtask", "description": "Prepare the gripper."},
                stale_sibling,
            ),
            _output(leaf),
            _output(summary="The child has no remaining action."),
            _output(revised_sibling, summary="Revise the parent remainder."),
            _output(summary="The root has no remaining action."),
        ],
        invocations,
        expected_invocation_counts=[0, 0, 1, 1, 2],
    )

    result = run_recap(
        public_task={"task_template_id": "recursive", "objective": "Prepare."},
        adapter=CapabilityAdapter(_design(), invoke),
        model=model,
    )

    assert result.status == "CONTROLLER_FINISHED"
    nodes = {node["node_id"]: node for node in result.context_tree["nodes"]}
    assert nodes["n0"]["children"] == ["n1"]
    assert nodes["n1"]["parent_id"] == "n0"
    assert nodes["n1"]["completed"] is True

    parent_refinement = json.loads(model.calls[3]["messages"][-1]["content"])
    assert parent_refinement["latest_public_observation"]["kind"] == (
        "subtask_completion"
    )
    assert parent_refinement["previous_remaining_plan"] == [stale_sibling]
    assert invocations[1][1]["request"]["target_position_m"] == [0.3, 0.2, 0.1]


def test_invalid_leaf_is_replanned_without_reaching_worker() -> None:
    invocations: list[tuple[str, dict[str, Any]]] = []

    def invoke(method_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        invocations.append((method_name, arguments))
        return {"operation": {"status": "EXECUTED"}}

    invalid_leaf = _capability(
        "move_end_effector_to_position",
        {"target_position_m": [0.1, 0.2, 0.3]},
    )
    valid_leaf = _capability(
        "move_end_effector_to_position",
        {"target_position_m": [0.1, 0.2, 0.3], "max_duration_s": 2.0},
    )
    model = ScriptedModel(
        [_output(invalid_leaf), _output(valid_leaf), _output()],
        invocations,
        expected_invocation_counts=[0, 0, 1],
    )

    result = run_recap(
        public_task={"task_template_id": "repair", "objective": "Move."},
        adapter=CapabilityAdapter(_design(), invoke),
        model=model,
    )

    assert result.status == "CONTROLLER_FINISHED"
    assert result.invalid_outputs == 1
    assert len(invocations) == 1
    assert result.trace[0]["argument_binding_valid"] is False
    assert result.trace[0]["capability_execution_outcome"] == "NOT_EXECUTED"
    assert result.trace[1]["argument_binding_valid"] is True


def test_worker_exception_is_bounded_and_controller_can_replan() -> None:
    invocations: list[tuple[str, dict[str, Any]]] = []

    def invoke(method_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        invocations.append((method_name, arguments))
        raise RuntimeError("credential=do-not-leak; private traceback")

    leaf = _capability(
        "set_gripper_opening",
        {"opening_fraction": 0.5, "max_duration_s": 1.0},
    )
    model = ScriptedModel(
        [_output(leaf), _output(summary="Stop after the bounded execution error.")],
        invocations,
        expected_invocation_counts=[0, 1],
    )

    result = run_recap(
        public_task={"task_template_id": "error", "objective": "Try once."},
        adapter=CapabilityAdapter(_design(), invoke),
        model=model,
    )

    assert result.status == "CONTROLLER_FINISHED"
    assert result.trace[0]["capability_execution_outcome"] == "EXECUTION_ERROR"
    second_call_messages = json.dumps(model.calls[1]["messages"]).lower()
    assert "do-not-leak" not in second_call_messages
    assert "private traceback" not in second_call_messages


def test_private_feedback_key_is_rejected_before_context_reinjection() -> None:
    invocations: list[tuple[str, dict[str, Any]]] = []

    def invoke(method_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        invocations.append((method_name, arguments))
        return {
            "public_standard": "LEAK",
            "private_criterion_threshold_m": "secret success threshold",
            "trusted_result": {
                "physical_harness_verdict": "PASS",
                "validation_passed": True,
            },
        }

    leaf = _capability(
        "set_gripper_opening",
        {"opening_fraction": 0.5, "max_duration_s": 1.0},
    )
    model = ScriptedModel(
        [_output(leaf), _output(summary="Stop after rejected feedback.")],
        invocations,
        expected_invocation_counts=[0, 1],
    )

    result = run_recap(
        public_task={"task_template_id": "privacy", "objective": "Try once."},
        adapter=CapabilityAdapter(_design(), invoke),
        model=model,
    )

    assert result.trace[0]["capability_execution_outcome"] == (
        "PUBLIC_OBSERVATION_REJECTED"
    )
    second_call_messages = json.dumps(model.calls[1]["messages"]).lower()
    assert "secret success threshold" not in second_call_messages
    assert "physical_harness_verdict" not in second_call_messages
    assert "validation_passed" not in second_call_messages
    assert "leak" not in second_call_messages
    serialized_result = json.dumps(asdict(result)).lower()
    assert "physical_harness_verdict" not in serialized_result
    assert "validation_passed" not in serialized_result
    assert "secret success threshold" not in serialized_result
    controller_start = json.loads(model.calls[0]["messages"][0]["content"])
    visible_catalog = json.dumps(controller_start["capability_catalog"]).lower()
    assert "public_standard" not in visible_catalog
    assert "criterion" not in visible_catalog


def test_capability_and_model_call_budgets_terminate_without_a_verdict() -> None:
    invocations: list[tuple[str, dict[str, Any]]] = []

    def invoke(method_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        invocations.append((method_name, arguments))
        return {"operation": {"status": "EXECUTED"}}

    leaf = _capability(
        "set_gripper_opening",
        {"opening_fraction": 0.5, "max_duration_s": 1.0},
    )
    cap_model = ScriptedModel(
        [_output(leaf), _output(leaf)],
        invocations,
        expected_invocation_counts=[0, 1],
    )
    cap_result = run_recap(
        public_task={"task_template_id": "cap-budget", "objective": "Repeat."},
        adapter=CapabilityAdapter(_design(), invoke),
        model=cap_model,
        budgets=RecapBudgets(max_capability_calls=1),
    )
    assert cap_result.status == "CAPABILITY_CALL_BUDGET_EXHAUSTED"
    assert len(invocations) == 1

    model_invocations: list[tuple[str, dict[str, Any]]] = []
    one_turn_model = ScriptedModel([_output(leaf)], model_invocations, [0])
    model_result = run_recap(
        public_task={"task_template_id": "model-budget", "objective": "Try."},
        adapter=CapabilityAdapter(
            _design(),
            lambda method, arguments: model_invocations.append((method, arguments))
            or {"operation": {"status": "EXECUTED"}},
        ),
        model=one_turn_model,
        budgets=RecapBudgets(max_model_calls=1),
    )
    assert model_result.status == "MODEL_CALL_BUDGET_EXHAUSTED"
    assert len(model_invocations) == 1


def test_non_finite_worker_observation_becomes_a_bounded_execution_error() -> None:
    invocations: list[tuple[str, dict[str, Any]]] = []

    def invoke(method_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        invocations.append((method_name, arguments))
        return {"public_state": {"height_m": float("nan")}}

    leaf = _capability(
        "set_gripper_opening",
        {"opening_fraction": 0.5, "max_duration_s": 1.0},
    )
    model = ScriptedModel(
        [_output(leaf), _output(summary="Stop after the bounded worker error.")],
        invocations,
        expected_invocation_counts=[0, 1],
    )

    result = run_recap(
        public_task={"task_template_id": "bad-observation", "objective": "Try."},
        adapter=CapabilityAdapter(_design(), invoke),
        model=model,
    )

    assert result.status == "CONTROLLER_FINISHED"
    assert result.trace[0]["capability_execution_outcome"] == "EXECUTION_ERROR"
    assert "nan" not in json.dumps(model.calls[1]["messages"]).lower()


def test_public_error_refines_but_public_abort_stops_physical_calls() -> None:
    leaf = _capability(
        "set_gripper_opening",
        {"opening_fraction": 0.5, "max_duration_s": 1.0},
    )

    error_calls: list[tuple[str, dict[str, Any]]] = []
    error_model = ScriptedModel(
        [_output(leaf), _output(summary="Stop after the public error.")],
        error_calls,
        expected_invocation_counts=[0, 1],
    )
    error_result = run_recap(
        public_task={"task_template_id": "worker-error", "objective": "Try."},
        adapter=CapabilityAdapter(
            _design(),
            lambda method, arguments: error_calls.append((method, arguments))
            or {"operation": {"status": "ERROR", "error_code": "DRIVER_ERROR"}},
        ),
        model=error_model,
    )
    assert error_result.status == "CONTROLLER_FINISHED"
    assert error_result.trace[0]["capability_execution_outcome"] == "EXECUTION_ERROR"

    abort_calls: list[tuple[str, dict[str, Any]]] = []
    abort_model = ScriptedModel([_output(leaf)], abort_calls, [0])
    abort_result = run_recap(
        public_task={"task_template_id": "worker-abort", "objective": "Try."},
        adapter=CapabilityAdapter(
            _design(),
            lambda method, arguments: abort_calls.append((method, arguments))
            or {"operation": {"status": "ABORT", "error_code": "STEP_BUDGET"}},
        ),
        model=abort_model,
    )
    assert abort_result.status == "WORKER_ABORTED"
    assert abort_result.model_calls == 1
    assert abort_result.capability_calls == 1
    assert abort_result.trace[0]["capability_execution_outcome"] == "WORKER_ABORTED"


def test_catalogue_metadata_matches_the_implemented_fixed_core() -> None:
    metadata_path = (
        REPOSITORY_ROOT
        / "AutoAdapter-Bench"
        / "high_level_controllers"
        / "recap"
        / "controller.json"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert metadata["source_revision"] == (
        "2fb112ffad685c7c6f7de86d5487ecca6f566fcc"
    )
    assert metadata["prompt_revision"] == "autoadapter-recap-typed-capability-v1"
    assert metadata["native_budgets"] == asdict(RecapBudgets())
    assert metadata["audit_status"] != "admitted"
    assert metadata["adapter_status"] != "implemented"
    designs = {
        json.loads(path.read_text(encoding="utf-8"))["capability_design_id"]
        for path in (
            SO101_DESIGN,
            REPOSITORY_ROOT
            / "experiment"
            / "b2_recap"
            / "reference_validation"
            / "resolved"
            / "unitree-go2-stock-12dof"
            / "capability_design.json",
        )
    }
    assert set(metadata["capability_interface_ids"]) == designs
    assert metadata["eligible_robot_ids"] == [
        "robotstudio_so101",
        "unitree-go2-stock-12dof",
    ]
    assert metadata["model_backed_roles"] == [
        "recursive_decomposition",
        "remaining_plan_refinement",
    ]
    assert metadata["public_observation_envelope_revision"] == (
        "b2-operation-observation-v1"
    )
    assert metadata["public_state_profile_revision"] == "b2-public-state-v2"
