"""Focused regressions for bounded Framework repair resumes."""

from types import SimpleNamespace

from auto_adapter.orchestrator import (
    PhaseResult,
    SelfAssemble,
    SelfAssembleConfig,
    validate_failure_feedback,
)
from auto_adapter.orchestrator_from_scratch import (
    FromScratchConfig,
    FromScratchOrchestrator,
)


def test_failure_feedback_excludes_private_report_fields():
    report = {
        "suite_path": "/private/validator/suite.py",
        "tests": [
            {
                "test": "A4_contact_object",
                "capability_id": "A4",
                "method_name": "contact_object",
                "ok": False,
                "detail": "contact did not persist",
                "errors": ["trace at /private/validator/trace.jsonl"],
                "request": {"target": "private-request"},
                "reset": {"qpos": "private-reset"},
                "trace_path": "/private/validator/trace.jsonl",
                "metrics": {
                    "parameters": {"private_binding": "secret"},
                    "measurements": {"final_error_m": 0.12, "hold_s": 0.4},
                },
            },
            {"test": "A1", "ok": True, "detail": "passed"},
        ],
    }

    feedback = validate_failure_feedback(report)

    assert "A4.contact_object" in feedback
    assert "contact did not persist" in feedback
    assert "final_error_m" in feedback and "hold_s" in feedback
    for private_value in (
        "suite_path",
        "private-request",
        "private-reset",
        "private_binding",
        "trace_path",
        "/private/validator",
    ):
        assert private_value not in feedback
    assert "passed" not in feedback


def test_standard_repair_keeps_generation_trace_and_uses_local_tools(tmp_path, monkeypatch):
    scene = tmp_path / "fixture.xml"
    scene.write_text("<mujoco/>")
    runner = SelfAssemble(SelfAssembleConfig("fixture", scene, tmp_path / "output"))
    original_trace = runner.workspace / "traces" / "02_generate.jsonl"
    original_trace.write_text("original generation trace\n")
    captured = {}

    def fake_run_phase(**kwargs):
        captured.update(kwargs)
        return PhaseResult(
            name=kwargs["name"], ok=True, duration_sec=0.0,
            trace_path=runner.workspace / "traces" / f"{kwargs['name']}.jsonl",
        )

    monkeypatch.setattr(runner, "_run_phase", fake_run_phase)
    result = runner._phase_repair(
        "Framework failure feedback:\n- check=A1: measurements={...}", 1
    )

    assert result.name == "03_repair_1"
    assert original_trace.read_text() == "original generation trace\n"
    assert captured["name"] == "03_repair_1"
    assert captured["max_iters"] <= 22
    assert {tool.name for tool in captured["tools"]} >= {
        "write_file", "read_file", "local_exec",
    }
    assert "validate_report.json" in captured["user_msg"]
    assert "private validation suites" in captured["user_msg"]


def test_from_scratch_repair_can_run_local_without_agentcore(tmp_path, monkeypatch):
    scene = tmp_path / "fixture.xml"
    scene.write_text("<mujoco/>")
    runner = object.__new__(FromScratchOrchestrator)
    runner.cfg = SimpleNamespace(
        robot_id="fixture",
        mjcf_path=scene,
        max_iters_gen_repair=20,
        max_tokens_per_turn=8000,
        bedrock_model="test-model",
        aws_region="us-east-1",
        model_provider="holistic",
    )
    runner.workspace = tmp_path / "workspace"
    runner.workspace.mkdir()
    runner._exec_python_tool = None
    runner.robot_definition = None
    runner.capability_design = None
    captured = {}

    def fake_run_loop(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(total_tokens={"in": 0, "out": 0}, error=None)

    monkeypatch.setattr(runner, "_run_loop", fake_run_loop)
    runner.phase_gen_repair("Framework failure feedback", 1)

    assert [tool.name for tool in captured["tools"]].count("local_exec") == 1
    assert "execute_python" not in {tool.name for tool in captured["tools"]}
    assert captured["name"] == "03_gen_repair_1"
    assert "local_exec" in captured["system"]
    assert "validate_report.json" in captured["user_msg"]


def test_from_scratch_all_ok_failure_gets_three_repairs_after_initial_gen(
    tmp_path, monkeypatch
):
    runner = object.__new__(FromScratchOrchestrator)
    runner.cfg = FromScratchConfig("h1", tmp_path / "scene.xml", tmp_path)
    runner.workspace = tmp_path / "workspace"
    runner.workspace.mkdir()
    (runner.workspace / "study.json").write_text("{}\n")
    (runner.workspace / "driver_from_scratch.py").write_text("class Robot: pass\n")

    phase_result = SimpleNamespace(total_tokens={"in": 0, "out": 0}, error=None)
    monkeypatch.setattr(runner, "phase_study", lambda: phase_result)
    monkeypatch.setattr(runner, "phase_gen_algo", lambda: phase_result)
    repairs = []

    def fake_validate():
        return {
            "tests": [
                {"test": "h1_walk", "ok": False, "detail": "walk failed"},
            ],
            "all_ok": False,
            "structural_ok": True,
        }

    def fake_repair(feedback, attempt):
        repairs.append((feedback, attempt))
        return phase_result

    monkeypatch.setattr(runner, "_validate_from_scratch_driver", fake_validate)
    monkeypatch.setattr(runner, "phase_gen_repair", fake_repair)

    result = runner.run()

    assert result.validate_ok is False
    assert [attempt for _, attempt in repairs] == [1, 2, 3]
    assert all(isinstance(feedback, str) for feedback, _ in repairs)
    assert all("structural_ok" not in feedback for feedback, _ in repairs)


def test_standard_default_budget_is_initial_generation_plus_three_repairs(tmp_path, monkeypatch):
    scene = tmp_path / "fixture.xml"
    scene.write_text("<mujoco/>")
    runner = SelfAssemble(SelfAssembleConfig("fixture", scene, tmp_path / "output"))
    calls = []
    monkeypatch.setattr(runner, "_phase_study", lambda: PhaseResult("study", True, 0.0))
    monkeypatch.setattr(runner, "_phase_generate", lambda: PhaseResult("generate", True, 0.0))

    def repair(feedback, attempt):
        calls.append(attempt)
        return PhaseResult(f"repair_{attempt}", True, 0.0)

    monkeypatch.setattr(runner, "_phase_repair", repair)
    monkeypatch.setattr(runner, "_phase_validate", lambda: PhaseResult("validate", False, 0.0))
    runner.run(stop_after="validate")
    assert calls == [1, 2, 3]


def test_existing_candidate_does_not_make_failed_model_invocation_a_success(tmp_path, monkeypatch):
    from auto_adapter import orchestrator as module

    scene = tmp_path / "fixture.xml"
    scene.write_text("<mujoco/>")
    runner = SelfAssemble(SelfAssembleConfig("fixture", scene, tmp_path / "output"))
    (runner.workspace / "driver.py").write_text("# copied historical candidate\n")
    failed = SimpleNamespace(
        ok=False, error="holistic invoke failed: DNS error", final_text="",
        total_tokens={"in": 0, "out": 0},
        trace=[SimpleNamespace(stop_reason="invoke_error")],
    )
    monkeypatch.setattr(module, "ReactLoop", lambda **kwargs: SimpleNamespace(run=lambda msg: failed))
    result = runner._run_phase(name="03_repair_1", system="fixture", user_msg="fixture",
                               tools=[], max_iters=1, expected_artifacts=["driver.py"])
    assert result.ok is False
    assert result.error == failed.error
    assert result.metadata["transport_error"] is True
