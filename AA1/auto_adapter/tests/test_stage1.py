"""Focused checks that the launcher wrapper uses the public DESIGN pipeline."""
from pathlib import Path

import pytest

from auto_adapter import orchestrator as module
from auto_adapter import orchestrator_from_scratch as scratch_module


@pytest.mark.parametrize("route", ["skeleton", "from_scratch"])
@pytest.mark.parametrize("outcome", ["export", "early_stop", "export_failure"])
def test_stage1_public_run_keeps_requested_stages_and_validation_distinct(
    tmp_path, monkeypatch, route, outcome
):
    scene = tmp_path / "input.xml"
    scene.write_text("<mujoco/>")
    monkeypatch.setattr(module, "find_robot_definition", lambda _: {
        "id": "fixture", "mjcf": str(scene), "generation_route": route,
        "capability_mjcf": str(tmp_path / "retired_scene.xml"),
    })
    calls = []
    stop = "design" if outcome == "early_stop" else None
    validated = outcome != "early_stop"
    completed = outcome != "export_failure"
    exported = outcome == "export"
    demo_config = tmp_path / "demo.yaml"

    class FixtureRunner:
        def __init__(self, cfg):
            assert cfg.mjcf_path == scene
            assert cfg.mode == "local"
            budget = cfg.max_outer_gen_val_iters if route == "skeleton" else cfg.max_outer_retries + 1
            assert budget == 3
            assert cfg.enable_demo is True
            assert cfg.demo_config_path == demo_config
            self.workspace = cfg.workspace_root / "fixture"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def run(self, *, stop_after):
            calls.append(stop_after)
            if route == "skeleton":
                return module.SelfAssembleResult("fixture", self.workspace, [
                    module.PhaseResult("01_study", True, 0.0),
                    module.PhaseResult("design", True, 0.0),
                    module.PhaseResult("02_generate", validated, 0.0),
                    module.PhaseResult("03_validate", validated, 0.0),
                    module.PhaseResult("04_export", exported, 0.0,
                                       error="export failed" if not completed else None),
                ], completed)
            return scratch_module.FromScratchResult(
                "fixture", self.workspace, True, validated, validated, None, {}, 0.0, {},
                design_ok=True, ok=completed, export_ok=exported,
                error="export failed" if not completed else None,
            )

    if route == "skeleton":
        monkeypatch.setattr(module, "SelfAssemble", FixtureRunner)
    else:
        monkeypatch.setattr(scratch_module, "FromScratchOrchestrator", FixtureRunner)
    result = module.run_stage1(
        robot_id="fixture", workspace_root=tmp_path, model="fixture", max_repairs=2,
        stop_after=stop, enable_demo=True, demo_config_path=demo_config,
    )
    assert calls == [stop]
    assert result["ok"] is completed
    assert result["stage1_ok"] is validated
    assert result["export_ok"] is exported
    assert result["stop_after"] == stop
    assert result["route"] == route
    assert Path(result["summary_path"]) == tmp_path / "fixture" / "summary.json"
    assert Path(result["workspace"]) == tmp_path / "fixture"
