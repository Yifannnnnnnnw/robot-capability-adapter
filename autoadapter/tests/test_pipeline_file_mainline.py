from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from autoadapter2.driver_synthesis.generation import (
    DriverSourceAuditError,
    ModelCallEvidence,
    StudyResult,
)
from autoadapter2.pipeline import (
    ExperimentConfig,
    PipelineHooks,
    _LegacyRecapJsonAdapter,
    _RecapClientAdapter,
    _run_cell,
    run_experiment,
)


def test_legacy_recap_fallbacks_keep_schema_in_system_and_history_dynamic() -> None:
    messages = [{"role": "user", "content": '{"dynamic_event":"start"}'}]
    schema = {"type": "object", "required": ["result"]}

    class MessageClient:
        def __init__(self) -> None:
            self.call: dict[str, Any] | None = None

        def generate_message_json(self, **kwargs: Any) -> dict[str, Any]:
            self.call = kwargs
            return {"result": "message"}

    message_client = MessageClient()
    assert _LegacyRecapJsonAdapter(message_client).generate_recap_json(
        stage="legacy-recap",
        system_prompt="fixed recap rules",
        messages=messages,
        response_schema=schema,
    ) == {"result": "message"}
    assert message_client.call is not None
    assert "Return exactly one JSON object matching this schema" in message_client.call[
        "system_prompt"
    ]
    assert "dynamic_event" not in message_client.call["system_prompt"]
    assert message_client.call["messages"] == messages

    class JsonClient:
        def __init__(self) -> None:
            self.call: dict[str, Any] | None = None

        def generate_json(self, **kwargs: Any) -> dict[str, Any]:
            self.call = kwargs
            return {"result": "json"}

    json_client = JsonClient()
    assert _LegacyRecapJsonAdapter(json_client).generate_recap_json(
        stage="legacy-recap",
        system_prompt="fixed recap rules",
        messages=messages,
        response_schema=schema,
    ) == {"result": "json"}
    assert json_client.call is not None
    assert "Return exactly one JSON object matching this schema" in json_client.call["prompt"]
    assert "dynamic_event" not in json_client.call["prompt"]
    assert json_client.call["inputs"] == {"messages": messages}


def _package(root: Path) -> Any:
    package_root = root / "package"
    assets = package_root / "assets"
    skeleton = package_root / "skeleton"
    private = package_root / "tasks" / "private"
    reference = package_root / "reference"
    for directory in (assets, skeleton, private, reference):
        directory.mkdir(parents=True, exist_ok=True)
    (assets / "scene.xml").write_text("<mujoco/>\n", encoding="utf-8")
    (skeleton / "motion.py").write_text("class Motion: pass\n", encoding="utf-8")
    return SimpleNamespace(
        root=package_root,
        robot_configuration_id="robot-a",
        package_version="1.0.0",
        snapshot_id="tasks-v1",
        morphology={"mjcf_entrypoint": "assets/scene.xml"},
        sources=(),
        tasks=(
            {"task_id": "task-1", "name": "one", "description": "task one"},
            {"task_id": "task-2", "name": "two", "description": "task two"},
        ),
        mjcf_path=assets / "scene.xml",
        skeleton_dir=skeleton,
        reference_driver=reference / "driver.py",
        private_dir=private,
    )


def _design() -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {"target": {"type": "number"}},
        "required": ["target"],
        "additionalProperties": False,
    }
    return {
        "artifact_type": "capability_design",
        "schema_version": "2.0",
        "capability_protocol_version": "capability-v2",
        "robot_configuration_id": "robot-a",
        "package_version": "1.0.0",
        "task_snapshot_id": "tasks-v1",
        "invocation_abi": {
            "kind": "capability_request",
            "method_call": "method(request=request)",
        },
        "capabilities": [
            {
                "capability_id": "cap-a",
                "method_name": "move_a",
                "description": "move a",
                "request_schema": schema,
            },
            {
                "capability_id": "cap-b",
                "method_name": "move_b",
                "description": "move b",
                "request_schema": schema,
            },
        ],
        "task_support": [
            {"task_id": "task-1", "capability_id": "cap-a", "rationale": "a"},
            {"task_id": "task-2", "capability_id": "cap-b", "rationale": "b"},
        ],
    }


def _suite() -> dict[str, Any]:
    cases = []
    for capability_id in ("cap-a", "cap-b"):
        for role in ("nominal", "calibrated_boundary"):
            cases.append(
                {
                    "case_id": f"{capability_id}-{role}",
                    "capability_id": capability_id,
                    "case_role": role,
                }
            )
    return {"artifact_type": "capability_validation_suite", "cases": cases}


def test_config_pins_file_workflow_budgets_and_formal_flag() -> None:
    config = ExperimentConfig.from_mapping(
        {
            "experiment_id": "budget-test",
            "robots": ["robot-a"],
            "generation_conditions": ["skeleton-assisted"],
            "formal": False,
            "phase_turn_budgets": {
                "study": 16,
                "tgcd": 6,
                "ivc": 6,
                "generate_skeleton": 22,
                "repair_skeleton": 22,
            },
            "execute_python": {
                "wall_timeout_s_per_call": 30,
                "max_output_chars_per_call": 12000,
                "max_steps_per_phase": 4000,
                "max_sim_time_s_per_phase": 20.0,
            },
            "recap": {
                "max_planning_turns_per_task": 16,
                "max_capability_calls_per_task": 12,
            },
        }
    )

    assert config.phase_turn_budgets == {
        "study": 16,
        "tgcd": 6,
        "ivc": 6,
        "generate_skeleton": 22,
        "generate_from_scratch": 40,
        "repair_skeleton": 22,
        "repair_from_scratch": 20,
    }
    assert config.recap_max_planning_turns_per_task == 16
    assert config.recap_max_capability_calls_per_task == 12
    assert config.evolution_max_attempts == 1
    assert config.probe_budget.max_requests is None
    assert config.formal is False
    assert config.as_dict()["formal"] is False


def test_recap_adapter_forwards_native_tool_turn_unchanged() -> None:
    marker = object()
    seen: dict[str, Any] = {}

    class Client:
        def generate_tool_turn(self, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return marker

    result = _RecapClientAdapter(Client()).generate_tool_turn(
        stage="recap",
        system_prompt="system",
        messages=({"role": "user", "content": "task"},),
        tools=({"type": "function", "function": {"name": "move"}},),
    )

    assert result is marker
    assert seen["stage"] == "recap"
    assert seen["tools"][0]["function"]["name"] == "move"


def test_rejected_repair_retains_the_last_frozen_driver_for_partial_recap(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    client = SimpleNamespace(calls=[])
    events: list[str] = []
    initial_source = "def build(*, model, data):\n    return object()\n"

    def generate_runner(*_args: Any, **kwargs: Any) -> Any:
        events.append("generate")
        workspace = Path(kwargs["workspace"])
        workspace.mkdir(parents=True, exist_ok=True)
        path = workspace / "driver.py"
        path.write_text(initial_source, encoding="utf-8")
        return SimpleNamespace(
            driver_path=path,
            driver_source=initial_source,
            output={},
            source_audit={},
            probe_results=(),
        )

    def harness_runner(**kwargs: Any) -> dict[str, Any]:
        events.append("harness")
        assert Path(kwargs["driver_path"]).read_text(encoding="utf-8") == initial_source
        return {
            "pipeline_completed": True,
            "physical_validation_executed": True,
            "validation_passed": False,
            "video_complete": True,
            "trials": [
                {
                    "case_id": case["case_id"],
                    "capability_id": case["capability_id"],
                    "trial_passed": case["capability_id"] == "cap-a",
                }
                for case in kwargs["suite"]["cases"]
            ],
            "video_manifest": [],
        }

    def repair_runner(*_args: Any, **_kwargs: Any) -> Any:
        events.append("repair")
        rejected_source = (
            "def build(*, model, data):\n"
            "    return getattr(data, 'private_state')\n"
        )
        raise DriverSourceAuditError(
            "repaired driver failed source audit",
            driver_source=rejected_source,
            model_output={"driver_filename": "driver.py"},
        )

    def task_demo_runner(**kwargs: Any) -> dict[str, Any]:
        events.append("recap")
        assert kwargs["capability_whitelist"] == ("cap-a",)
        assert Path(kwargs["driver_path"]).read_text(encoding="utf-8") == initial_source
        return {
            "pipeline_completed": True,
            "physical_validation_executed": True,
            "validation_passed": False,
            "video_complete": True,
            "task_count": 1,
            "passed_task_count": 0,
            "trials": [{"case_id": "demo", "trial_passed": False}],
            "video_manifest": [],
        }

    study_result = StudyResult(
        condition="skeleton-assisted",
        output={"findings": [], "implementation_plan": []},
        probe_requests=({"probe_id": "public-step", "script": "import mujoco"},),
        call_evidence=ModelCallEvidence(
            stage="study", prompt="test", inputs={}, output={}
        ),
        probe_results=(
            {
                "probe_id": "public-step",
                "exit_code": 0,
                "timed_out": False,
                "spawn_error": None,
                "physics_steps": 1,
            },
        ),
    )
    config = ExperimentConfig.from_mapping(
        {
            "experiment_id": "repair-rejection-retention",
            "robots": ["robot-a"],
            "generation_conditions": ["skeleton-assisted"],
            "max_driver_attempts_per_condition": 3,
            "record_video": False,
            "formal": False,
            "evolution": {"enabled": False},
        }
    )
    cell = _run_cell(
        package=package,
        design=_design(),
        capability_suite=_suite(),
        robot="robot-a",
        condition="skeleton-assisted",
        config=config,
        client=client,
        identity={"provider": "test", "model": "test"},
        experience=(),
        workspace=tmp_path / "cell",
        run_id="repair-rejection-retention",
        hooks=PipelineHooks(
            generate_runner=generate_runner,
            repair_runner=repair_runner,
            harness_runner=harness_runner,
            task_demo_runner=task_demo_runner,
        ),
        model_stage_log=[],
        completed_study=study_result,
        completed_probe_results=study_result.probe_results,
        evolution_enabled=False,
    )

    assert events == ["generate", "harness", "repair", "recap"]
    assert cell["frozen_driver_attempt_count"] == 1
    assert cell["passed_capability_whitelist"] == ["cap-a"]
    assert cell["capability_validation"]["attempt"] == 0
    assert cell["task_demo_executed"] is True
    assert len(cell["development_rejections"]) == 1
    rejection = cell["development_rejections"][0]
    assert rejection["stage"] == "repair"
    assert rejection["formal_attempt_submitted"] is False
    assert rejection["source_audit_passed"] is False
    assert rejection["previous_frozen_driver_retained"] is True
    assert Path(cell["attempts"][0]["frozen_driver_path"]).read_text(
        encoding="utf-8"
    ) == initial_source


def test_fresh_cell_order_visibility_freeze_and_partial_recap_whitelist(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    events: list[str] = []
    seen: dict[str, Any] = {}
    client = SimpleNamespace(calls=[])
    experience = (
        {
            "experience_id": "exp-1",
            "reviewed": True,
            "observation": "public observation",
            "lesson": "public lesson",
            "recommendation": "public recommendation",
            "scope": "robot-a",
            "evidence": [],
        },
    )

    def study_runner(_client: Any, _package: Any, **kwargs: Any) -> StudyResult:
        events.append("study")
        seen["study_experience"] = kwargs["experience"]
        assert kwargs.get("design") is None
        assert kwargs["max_turns"] == 16
        study_path = Path(kwargs["workspace"]) / "study.json"
        study_path.parent.mkdir(parents=True, exist_ok=True)
        study_path.write_text(
            json.dumps({"condition": "model-authored-label"}),
            encoding="utf-8",
        )
        return StudyResult(
            condition=kwargs["condition"],
            output={
                "condition": kwargs["condition"],
                "findings": ["f"],
                "implementation_plan": ["p"],
                "probe_requests": [{"probe_id": "p", "script": "import mujoco"}],
            },
            probe_requests=({"probe_id": "p", "script": "import mujoco"},),
            call_evidence=ModelCallEvidence(
                stage="study", prompt="test", inputs={}, output={}
            ),
            probe_results=(
                {
                    "probe_id": "p",
                    "exit_code": 0,
                    "timed_out": False,
                    "spawn_error": None,
                    "physics_steps": 1,
                },
            ),
        )

    def tgcd_runner(_client: Any, _package: Any, **kwargs: Any) -> dict[str, Any]:
        events.append("tgcd")
        seen["tgcd"] = kwargs
        assert kwargs["probe_budget"].max_output_chars == 12_000
        design = _design()
        kwargs["callback"](
            {
                "stage": "tgcd-sealed",
                "turn": 1,
                "artifact": design,
                "trace": [{"event": "write_file"}],
            }
        )
        return design

    def render_reference(_design: Any, destination: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / "driver.py"
        path.write_text("# private reference\n", encoding="utf-8")
        return path

    def reference_runner(**kwargs: Any) -> dict[str, Any]:
        events.append("reference")
        assert kwargs["trusted_reference_driver"] is True
        return {
            "pipeline_completed": True,
            "physical_validation_executed": True,
            "validation_passed": True,
            "video_complete": True,
        }

    def ivc_runner(_client: Any, **kwargs: Any) -> dict[str, Any]:
        events.append("ivc")
        seen["ivc"] = kwargs
        assert kwargs["probe_budget"].max_output_chars == 12_000
        suite = _suite()
        kwargs["reference_positive_control_hook"](
            capability_design=kwargs["design"], validation_suite=suite
        )
        kwargs["callback"](
            {
                "stage": "ivc-sealed",
                "turn": 1,
                "artifact": {**suite, "private_marker": "hidden-binding"},
                "trace": [{"event": "write_file", "private": "hidden-binding"}],
            }
        )
        return suite

    def generate_runner(
        _client: Any,
        _package: Any,
        _design_value: Any,
        _study: Any,
        **kwargs: Any,
    ) -> Any:
        events.append("generate")
        seen["generate_experience"] = kwargs["experience"]
        seen["development"] = kwargs["development"]
        assert kwargs["max_turns"] == (
            22 if kwargs["condition"] == "skeleton-assisted" else 40
        )
        workspace = Path(kwargs["workspace"])
        workspace.mkdir(parents=True, exist_ok=True)
        path = workspace / "driver.py"
        path.write_text("def build(*, model, data):\n    return object()\n", encoding="utf-8")
        return SimpleNamespace(
            driver_path=path,
            driver_source=path.read_text(encoding="utf-8"),
            output={},
            source_audit={},
            probe_results=(),
        )

    def harness_runner(**kwargs: Any) -> dict[str, Any]:
        events.append("harness")
        seen["harness_kwargs"] = kwargs
        assert "trusted_reference_driver" not in kwargs
        trials = []
        for case in kwargs["suite"]["cases"]:
            passed = case["capability_id"] == "cap-a" or case["case_role"] == "nominal"
            trials.append(
                {
                    "case_id": case["case_id"],
                    "capability_id": case["capability_id"],
                    "trial_passed": passed,
                }
            )
        return {
            "pipeline_completed": True,
            "physical_validation_executed": True,
            "validation_passed": False,
            "video_complete": True,
            "trials": trials,
            "video_manifest": [],
        }

    def repair_runner(_client: Any, **kwargs: Any) -> Any:
        events.append("repair")
        assert kwargs["development"] is seen["development"]
        assert kwargs["max_turns"] == (
            22 if kwargs["condition"] == "skeleton-assisted" else 20
        )
        assert kwargs["public_inputs"]["eligible_experience"] == list(experience)
        workspace = Path(kwargs["workspace"])
        path = workspace / "driver.py"
        path.write_text(
            "def build(*, model, data):\n    return object()\n",
            encoding="utf-8",
        )
        return SimpleNamespace(
            driver_path=path,
            driver_source=path.read_text(encoding="utf-8"),
            output={},
            source_audit={},
            probe_results=(),
        )

    def task_demo_runner(**kwargs: Any) -> dict[str, Any]:
        events.append("recap")
        seen["recap"] = kwargs
        assert [item["capability_id"] for item in kwargs["design"]["capabilities"]] == ["cap-a"]
        assert kwargs["capability_whitelist"] == ("cap-a",)
        assert kwargs["budgets"].max_planning_turns == 16
        assert kwargs["budgets"].max_capability_calls == 12
        assert "experience" not in kwargs
        return {
            "pipeline_completed": True,
            "physical_validation_executed": True,
            "validation_passed": False,
            "video_complete": True,
            "task_count": 1,
            "passed_task_count": 0,
            "trials": [{"case_id": "demo", "task_id": "task-1", "trial_passed": False}],
            "video_manifest": [],
            "high_level_controller": {
                "kind": "recap",
                "completed": True,
                "model_turn_count": 1,
                "capability_call_count": 1,
            },
        }

    hooks = PipelineHooks(
        package_loader=lambda *_args: package,
        capability_design_validator=lambda value, *_args, **_kwargs: value,
        capability_suite_validator=lambda value, *_args, **_kwargs: value,
        study_runner=study_runner,
        tgcd_runner=tgcd_runner,
        ivc_runner=ivc_runner,
        generate_runner=generate_runner,
        repair_runner=repair_runner,
        harness_runner=harness_runner,
        reference_renderer=render_reference,
        reference_runner=reference_runner,
        task_demo_runner=task_demo_runner,
    )
    result = run_experiment(
        tmp_path,
        config={
            "experiment_id": "fresh-cell",
            "robots": ["robot-a"],
            "generation_conditions": ["skeleton-assisted", "from-scratch"],
            "max_driver_attempts_per_condition": 2,
            "formal": False,
            "experience": {
                "input": list(experience),
                "review_queue_output": "review.json",
                "snapshot_output": "snapshot.json",
            },
            "evolution": {"enabled": False},
        },
        output_dir=tmp_path / "run",
        run_id="fresh-cell-run",
        client=client,
        hooks=hooks,
        check_self_containment=False,
        skip_reference_calibration=False,
    )

    expected_cell_events = [
        "study",
        "tgcd",
        "ivc",
        "reference",
        "generate",
        "harness",
        "repair",
        "harness",
        "recap",
    ]
    assert events == [*expected_cell_events, *expected_cell_events]
    assert events.count("tgcd") == 2
    assert events.count("ivc") == 2
    assert events.count("reference") == 2
    assert seen["study_experience"] == experience
    assert seen["tgcd"]["experience"] == experience
    assert seen["tgcd"]["study"]["findings"] == ["f"]
    assert seen["tgcd"]["max_turns"] == 6
    for condition in ("skeleton-assisted", "from-scratch"):
        sealed_study = json.loads(
            (
                tmp_path
                / "run"
                / "cells"
                / "robot-a"
                / condition
                / "files"
                / "study.json"
            ).read_text(encoding="utf-8")
        )
        assert sealed_study["condition"] == condition
        assert sealed_study["findings"] == ["f"]
    assert "experience" not in seen["ivc"]
    assert {"candidate_driver", "driver_source", "repair_history"}.isdisjoint(
        seen["ivc"]
    )
    assert seen["generate_experience"] == experience
    assert "experience" not in seen["harness_kwargs"]
    cell = result["cells"][0]
    assert cell["frozen_driver_attempt_count"] == 2
    assert cell["passed_capability_whitelist"] == ["cap-a"]
    assert all(
        Path(attempt["frozen_driver_path"]).is_file()
        for attempt in cell["attempts"]
    )
    assert cell["task_demo_executed"] is True
    ivc_evidence = next(item for item in result["stage_evidence"] if item["stage"] == "ivc")
    assert ivc_evidence["experience_ids"] == []
    assert ivc_evidence["candidate_driver_visible"] is False
    assert "model_calls" not in ivc_evidence
    assert "hidden-binding" not in str(ivc_evidence)
    assert ivc_evidence["artifact_trace_summary"][0]["trace_event_count"] == 1
    assert "hidden-binding" in Path(
        ivc_evidence["private_artifact_trace_path"]
    ).read_text(encoding="utf-8")
    tgcd_evidence = next(
        item for item in result["stage_evidence"] if item["stage"] == "tgcd"
    )
    assert tgcd_evidence["artifact_trace"][0]["trace"][0]["event"] == "write_file"
    recap_evidence = next(
        item
        for item in result["stage_evidence"]
        if item["stage"] == "task_demo_recap"
    )
    assert recap_evidence["experience_ids"] == []
