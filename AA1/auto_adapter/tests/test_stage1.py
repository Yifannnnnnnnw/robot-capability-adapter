"""Focused checks for the fresh unified Stage 1 entrypoint."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_adapter import orchestrator as module


class _FixtureRunner:
    def __init__(self, robot_id, workspace_root, route, *, generate_missing=True):
        self.workspace = Path(workspace_root) / robot_id
        (self.workspace / "traces").mkdir(parents=True)
        (self.workspace / "recordings").mkdir()
        self.route = route
        self.generate_missing = generate_missing

    def _raw(self, name, *, ok=True, error=None, stop_reason="end_turn", tokens=None):
        trace_path = self.workspace / "traces" / f"{name}.jsonl"
        trace_path.write_text(
            '{"token_usage":{"in":1,"out":2},"stop_reason":"%s"}\n'
            % stop_reason
        )
        return SimpleNamespace(
            ok=ok,
            error=error,
            final_text="",
            trace=[SimpleNamespace(stop_reason=stop_reason)],
            total_tokens=tokens or {"in": 1, "out": 2},
            trace_path=trace_path,
        )

    def _phase_study(self):
        (self.workspace / "study.json").write_text('{"fixture": true}\n')
        return self._raw("01_study")

    def _phase_generate(self):
        if not self.generate_missing:
            (self.workspace / "driver.py").write_text("# initial\n")
        return self._raw(
            "02_generate", ok=not self.generate_missing,
            error="driver missing" if self.generate_missing else None,
        )

    def _phase_repair(self, feedback, attempt):
        (self.workspace / "driver.py").write_text(f"# repair {attempt}\n")
        return self._raw(f"03_repair_{attempt}")


def _fixture_definition(route="skeleton"):
    return {
        "id": "fixture",
        "mjcf": "assets/mjcf/so101_mujoco.xml",
        "class": "arm",
        "generation_route": route,
    }


def test_stage1_initial_missing_candidate_repairs_three_times_and_preserves_rounds(
    tmp_path, monkeypatch
):
    runners = []
    framework_calls = []

    monkeypatch.setattr(module, "_stage1_canonical_definition", lambda _: _fixture_definition())

    def make_runner(robot_id, mjcf_path, workspace_root, model):
        runner = _FixtureRunner(robot_id, workspace_root, "skeleton")
        runners.append(runner)
        return runner

    monkeypatch.setattr(module, "_stage1_standard_runner", make_runner)

    def framework(**kwargs):
        framework_calls.append(kwargs["workspace"])
        ok = len(framework_calls) == 4
        return ({"tests": [{"test": "fixture", "ok": ok}], "all_ok": ok},
                {"child_ok": True, "duration_sec": 0.01})

    monkeypatch.setattr(module, "_stage1_framework_subprocess", framework)
    result = module.run_stage1(
        robot_id="fixture", workspace_root=tmp_path, model="fixture-model", max_repairs=3
    )

    assert result["attempts"] == 4
    assert result["effective_repairs"] == 3
    assert result["framework_ok"] is True
    assert result["stage1_ok"] is True
    assert not (tmp_path / "initial" / "fixture" / "driver.py").exists()
    assert (tmp_path / "repair_1" / "fixture" / "study.json").exists()
    assert (tmp_path / "repair_1" / "fixture" / "driver.py").exists()
    assert len(framework_calls) == 4
    assert len(result["rounds"]) == 4
    assert (tmp_path / "initial" / "fixture" / "run_context.json").exists()
    assert (tmp_path / "repair_3" / "fixture" / "run_context.json").exists()

    with pytest.raises(FileExistsError):
        module.run_stage1(
            robot_id="fixture", workspace_root=tmp_path, model="fixture-model", max_repairs=3
        )


def test_stage1_invoke_error_stops_without_framework_or_effective_repair(tmp_path, monkeypatch):
    framework_calls = []
    monkeypatch.setattr(module, "_stage1_canonical_definition", lambda _: _fixture_definition())

    def make_runner(robot_id, mjcf_path, workspace_root, model):
        runner = _FixtureRunner(robot_id, workspace_root, "skeleton", generate_missing=False)

        def failed_generate():
            (runner.workspace / "driver.py").write_text("# copied candidate\n")
            return runner._raw(
                "02_generate", ok=False, error="holistic invoke failed",
                stop_reason="invoke_error",
            )

        runner._phase_generate = failed_generate
        return runner

    monkeypatch.setattr(module, "_stage1_standard_runner", make_runner)
    monkeypatch.setattr(
        module,
        "_stage1_framework_subprocess",
        lambda **kwargs: framework_calls.append(kwargs) or ({"all_ok": True}, {"child_ok": True}),
    )

    result = module.run_stage1(
        robot_id="fixture", workspace_root=tmp_path, model="fixture-model", max_repairs=3
    )

    assert result["external_blocked"] is True
    assert result["generation_ok"] is False
    assert result["framework_ok"] is False
    assert result["effective_repairs"] == 0
    assert result["attempts"] == 1
    assert not framework_calls
    assert "invoke" in result["error"]


def test_profile_framework_requires_every_trusted_case_id():
    definition = module._stage1_canonical_definition("so101")
    report = {"all_ok": True, "tests": [{"case_id": "so101-a4-nominal", "ok": True}]}
    assert module._stage1_framework_ok(report, "so101", definition, "skeleton") is False


def test_stage1_uses_one_entry_for_named_scratch_fixture(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(module, "_stage1_canonical_definition",
                        lambda _: _fixture_definition("from_scratch"))

    class Scratch(_FixtureRunner):
        def phase_study(self):
            calls.append("study")
            (self.workspace / "study.json").write_text("{}\n")
            return self._raw("01_study")

        def phase_gen_algo(self):
            calls.append("generate")
            (self.workspace / "driver_from_scratch.py").write_text("# candidate\n")
            return self._raw("02_gen_algo")

        def phase_gen_repair(self, feedback, attempt):
            calls.append(f"repair_{attempt}")
            (self.workspace / "driver_from_scratch.py").write_text("# repaired\n")
            return self._raw(f"03_gen_repair_{attempt}")

    monkeypatch.setattr(
        module, "_stage1_scratch_runner",
        lambda robot_id, mjcf_path, workspace_root, model: Scratch(
            robot_id, workspace_root, "from_scratch", generate_missing=False
        ),
    )
    monkeypatch.setattr(
        module, "_stage1_framework_subprocess",
        lambda **kwargs: ({"all_ok": True, "tests": []},
                          {"child_ok": True, "duration_sec": 0.01}),
    )

    result = module.run_stage1(
        robot_id="fixture", workspace_root=tmp_path, model="fixture-model", max_repairs=3
    )
    assert calls == ["study", "generate"]
    assert result["route"] == "from_scratch"
    assert result["stage1_ok"] is True
    assert result["final_candidate"].endswith("driver_from_scratch.py")
