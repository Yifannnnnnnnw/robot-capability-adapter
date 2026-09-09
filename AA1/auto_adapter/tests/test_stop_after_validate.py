"""Regression: validation was skipped with continue, bypassing stop_after."""
from auto_adapter.orchestrator import PhaseResult, SelfAssemble, SelfAssembleConfig


def test_validation_stop_does_not_run_export_or_demo(tmp_path, monkeypatch):
    scene = tmp_path / "fixture.xml"
    scene.write_text("<mujoco/>")
    runner = SelfAssemble(SelfAssembleConfig("fixture", scene, tmp_path / "output"))
    calls = []

    def phase(name):
        def invoke(**kwargs):
            calls.append(name)
            return PhaseResult(name=name, ok=True, duration_sec=0.)
        return invoke

    for name in ("study", "generate", "validate", "export", "demo"):
        monkeypatch.setattr(runner, f"_phase_{name}", phase(name))
    monkeypatch.setattr(runner, "_summarise_validate_failures", lambda: "")
    result = runner.run(stop_after="validate")
    assert calls == ["study", "generate", "validate"]
    assert all(p.error == "not run — stop_after=validate" for p in result.phases[-2:])


def test_one_failed_validation_is_one_attempt_and_one_report(tmp_path, monkeypatch):
    scene = tmp_path / "fixture.xml"
    scene.write_text("<mujoco/>")
    runner = SelfAssemble(SelfAssembleConfig(
        "fixture", scene, tmp_path / "output", max_outer_gen_val_iters=1))
    for name in ("study", "generate", "validate"):
        monkeypatch.setattr(runner, f"_phase_{name}",
                            lambda name=name, **kwargs: PhaseResult(
                                name=name, ok=name != "validate", duration_sec=0.,
                                error="fixture physical failure" if name == "validate" else None))
    monkeypatch.setattr(runner, "_summarise_validate_failures", lambda: "fixture failure")
    result = runner.run(stop_after="validate")
    assert [phase.name for phase in result.phases].count("validate") == 1
    generated = next(phase for phase in result.phases if phase.name == "generate")
    assert generated.metadata["outer_gen_val_iters"] == 1
