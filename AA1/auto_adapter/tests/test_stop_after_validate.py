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
