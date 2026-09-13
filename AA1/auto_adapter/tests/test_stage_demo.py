"""Focused scheduling checks; every phase body here is an explicit fixture."""
from types import SimpleNamespace

import pytest

from auto_adapter.orchestrator import PhaseResult, SelfAssemble, SelfAssembleConfig
from auto_adapter.orchestrator_from_scratch import FromScratchConfig, FromScratchOrchestrator


def _fixture_runner(tmp_path, monkeypatch, *, scratch, dynamic, enabled=True,
                    fail=None, repair=False):
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    config_cls = FromScratchConfig if scratch else SelfAssembleConfig
    runner_cls = FromScratchOrchestrator if scratch else SelfAssemble
    runner = runner_cls(config_cls(
        "fixture", scene, tmp_path / "runs", mode="local",
        prepare_capabilities=dynamic, enable_demo=enabled,
    ))
    calls = []
    validation_count = 0
    current_design = {"capabilities": [{"method_name": "current_capability"}]}
    if not dynamic:
        runner.capability_design = current_design

    def phase(name):
        def execute(*_args, **_kwargs):
            calls.append(name)
            ok = name != fail
            if ok and name == "study":
                (runner.workspace / "study.json").write_text("{}")
            if ok and name == "design":
                output = runner.workspace / "design"
                output.mkdir(exist_ok=True)
                (output / "capability_design.json").write_text("{}")
                runner.capability_design = current_design
            if ok and name in {"generate", "repair"}:
                filename = "driver_from_scratch.py" if scratch else "driver.py"
                (runner.workspace / filename).write_text(f"# attempt {len(calls)}")
            if name == "demo":
                assert runner.capability_design is current_design
            error = None if ok else f"fixture {name} failure"
            if scratch and name in {"study", "design", "generate", "repair"}:
                return SimpleNamespace(ok=ok, error=error, total_tokens={})
            return PhaseResult(name, ok, 0.0, error=error)
        return execute

    def validate():
        nonlocal validation_count
        calls.append("validate")
        validation_count += 1
        ok = fail != "validate" and not (repair and validation_count == 1)
        error = None if ok else "fixture validation failure"
        if scratch:
            return {"all_ok": ok, "tests": [], "error": error}
        return PhaseResult("validate", ok, 0.0, error=error)

    names = ({"study": "phase_study", "design": "phase_design",
              "generate": "phase_gen_algo", "repair": "phase_gen_repair",
              "export": "phase_export", "demo": "phase_demo"} if scratch else
             {name: f"_phase_{name}" for name in
              ("study", "design", "generate", "repair", "export", "demo")})
    for name, method in names.items():
        monkeypatch.setattr(runner, method, phase(name))
    monkeypatch.setattr(runner, "_validate_from_scratch_driver" if scratch else "_phase_validate", validate)
    if not scratch:
        monkeypatch.setattr(runner, "_summarise_validate_failures", lambda: "fixture measurements")
    return runner, calls


@pytest.mark.parametrize("scratch,dynamic", [(False, False), (False, True), (True, False), (True, True)])
@pytest.mark.parametrize("scenario", ["off", "on", "stop_validate", "stop_export",
                                      "validate_failure", "export_failure", "demo_failure", "repair_pass"])
def test_fixed_and_dynamic_routes_share_export_and_optional_demo(
    tmp_path, monkeypatch, scratch, dynamic, scenario
):
    failure = scenario.removesuffix("_failure") if scenario.endswith("_failure") else None
    runner, calls = _fixture_runner(
        tmp_path, monkeypatch, scratch=scratch, dynamic=dynamic,
        enabled=scenario != "off", fail=failure, repair=scenario == "repair_pass",
    )
    # One failed validation is sufficient to establish downstream gating.
    if failure == "validate":
        if scratch:
            runner.cfg.max_outer_retries = 0
        else:
            runner.cfg.max_outer_gen_val_iters = 1
    stop = scenario.removeprefix("stop_") if scenario.startswith("stop_") else None
    result = runner.run(stop_after=stop)
    expected = ["study"] + (["design"] if dynamic else []) + ["generate", "validate"]
    if scenario == "repair_pass":
        expected += ["repair", "validate"]
    if scenario not in {"stop_validate", "validate_failure"}:
        expected += ["export"]
    if scenario in {"on", "demo_failure", "repair_pass"}:
        expected += ["demo"]
    assert calls == expected
    assert result.ok is (failure is None)
    if scratch:
        assert result.gen_ok is True
        assert result.validate_ok is (failure != "validate")
    elif scenario == "demo_failure":
        assert next(p for p in result.phases if p.name == "generate").ok
        assert next(p for p in result.phases if p.name == "validate").ok
    if scenario == "repair_pass":
        validations = [p for p in result.phases if (p["name"] if scratch else p.name) == "validate"]
        assert [(p["ok"] if scratch else p.ok) for p in validations] == [False, True]


@pytest.mark.parametrize("scratch", [False, True])
def test_demo_phase_passes_current_design_and_cases_to_bridge(tmp_path, monkeypatch, scratch):
    from auto_adapter.agent import recap_demo

    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    config_cls = FromScratchConfig if scratch else SelfAssembleConfig
    runner_cls = FromScratchOrchestrator if scratch else SelfAssemble
    runner = runner_cls(config_cls("fixture", scene, tmp_path / "runs", mode="local"))
    runner.capability_design = {"capabilities": [{"method_name": "new_capability"}]}
    runner.scene_cases_path = tmp_path / "current_cases.yaml"
    runner.cfg.demo_config_path = tmp_path / "demo.yaml"
    captured = {}

    def demo(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "duration_sec": 0.1, "controller_result": {"status": "CONTROLLER_FINISHED"}}

    monkeypatch.setattr(recap_demo, "run_configured_demo", demo, raising=False)
    result = runner.phase_demo() if scratch else runner._phase_demo()
    assert result.ok
    assert captured["capability_design"] is runner.capability_design
    assert captured["scene_cases_path"] == runner.scene_cases_path
    assert captured["demo_config_path"] == runner.cfg.demo_config_path
    assert captured.get("from_scratch", False) is scratch


def test_scratch_export_adapts_existing_prompt_and_rejects_stale_file(tmp_path, monkeypatch):
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    runner = FromScratchOrchestrator(FromScratchConfig("fixture", scene, tmp_path / "runs", mode="local"))
    artifact = runner.workspace / "mcp_server.py"
    artifact.write_text("# stale export")
    captured = {}

    def export(**kwargs):
        captured.update(kwargs)
        assert not artifact.exists()
        return SimpleNamespace(ok=True, error=None, total_tokens={}, final_text="fixture")

    monkeypatch.setattr(runner, "_run_loop", export)
    result = runner.phase_export()
    assert not result.ok
    assert "driver_from_scratch" in captured["system"]
    assert "Robot.build_from_mjcf('mjcf.xml')" in captured["system"]
    assert "driver.build()" not in captured["system"]
    assert {tool.name for tool in captured["tools"]} == {"write_file", "read_file", "local_exec"}


def test_standard_export_cannot_pass_from_an_old_server(tmp_path, monkeypatch):
    from auto_adapter import orchestrator

    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    runner = SelfAssemble(SelfAssembleConfig("fixture", scene, tmp_path / "runs"))
    (runner.workspace / "mcp_server.py").write_text("# old server")
    loop_result = SimpleNamespace(ok=True, error=None, trace=[], total_tokens={}, final_text="no file written")
    monkeypatch.setattr(orchestrator, "ReactLoop", lambda **_kwargs:
                        SimpleNamespace(run=lambda _message: loop_result))

    result = runner._phase_export()

    assert not result.ok
    assert not result.artifact_paths


@pytest.mark.parametrize("scratch", [False, True])
def test_export_receives_the_current_public_design(tmp_path, monkeypatch, scratch):
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    config_cls = FromScratchConfig if scratch else SelfAssembleConfig
    runner_cls = FromScratchOrchestrator if scratch else SelfAssemble
    runner = runner_cls(config_cls("fixture", scene, tmp_path / "runs", mode="local"))
    runner.capability_design = {"capabilities": [{"method_name": "only_current_method",
                                                "request_schema": {"type": "object"}}]}
    captured = {}

    def export(**kwargs):
        captured.update(kwargs)
        (runner.workspace / "mcp_server.py").write_text("# current fixture server")
        if scratch:
            return SimpleNamespace(ok=True, error=None, total_tokens={}, final_text="fixture")
        return PhaseResult("export", True, 0.0)

    monkeypatch.setattr(runner, "_run_loop" if scratch else "_run_phase", export)
    result = runner.phase_export() if scratch else runner._phase_export()
    assert result.ok
    assert '"method_name": "only_current_method"' in captured["user_msg"]
    assert '"request_schema"' in captured["user_msg"]
    assert "method(request=...)" in captured["system"]


@pytest.mark.parametrize("scratch", [False, True])
def test_remote_demo_fails_explicitly_without_running_another_task(tmp_path, scratch):
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    config_cls = FromScratchConfig if scratch else SelfAssembleConfig
    runner_cls = FromScratchOrchestrator if scratch else SelfAssemble
    runner = runner_cls(config_cls("fixture", scene, tmp_path / "runs",
                                  mode="agentcore" if scratch else "dgx"))
    result = runner.phase_demo() if scratch else runner._phase_demo()
    assert not result.ok
    assert "requires local mode" in result.error
