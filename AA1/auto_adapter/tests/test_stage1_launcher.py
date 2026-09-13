"""Focused tests for the thin Stage 1 diagnostic launcher.

The fake pipeline is deliberately confined to this test module; no model,
SDK, or simulation call belongs in launcher checks.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run" / "run_stage1.py"
spec = importlib.util.spec_from_file_location("stage1_launcher_under_test", SCRIPT)
assert spec is not None and spec.loader is not None
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_launcher_requires_explicit_robot_selection():
    with pytest.raises(SystemExit):
        launcher.build_parser().parse_args([])
    assert launcher.build_parser().parse_args(["--robots", "piper"]).robots == ["piper"]


def test_launcher_forwards_api_arguments_and_accepts_pipeline_result_without_files(
    tmp_path, capsys
):
    calls = []

    def fake_pipeline(**kwargs):
        calls.append(kwargs)
        # The launcher must consume the pipeline verdict, rather than trying
        # to evaluate a workspace or run another validation subprocess.
        return {"ok": True, "stage1_ok": True, "external_blocked": False}

    code, aggregate = launcher.run_selected(
        robots=["franka"],
        model="test-model",
        max_repairs=2,
        output_root=tmp_path / "shared-batch",
        pipeline_runner=fake_pipeline,
    )

    assert code == 0
    assert calls == [{
        "robot_id": "franka",
        "workspace_root": (tmp_path / "shared-batch").resolve(),
        "model": "test-model",
        "max_repairs": 2,
        "stop_after": None,
        "enable_demo": False,
        "demo_config_path": None,
    }]
    assert aggregate["selected_robots"] == ["franka"]
    assert aggregate["results"][0]["status"] == "ok"
    launch_path = Path(aggregate["launch_path"])
    assert launch_path.is_file()
    persisted = json.loads(launch_path.read_text())
    assert persisted["results"][0]["status"] == "ok"
    assert "franka:" in capsys.readouterr().out


def test_row_duration_uses_utc_wall_interval_and_keeps_monotonic_measurement(
    tmp_path, monkeypatch, capsys
):
    wall = iter([
        datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 10, 12, 0, 10, tzinfo=timezone.utc),
        datetime(2026, 9, 10, 12, 0, 10, tzinfo=timezone.utc),
    ])
    monotonic = iter([100.0, 100.25])
    monkeypatch.setattr(launcher, "_utc_now", lambda: next(wall))
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(monotonic))

    code, aggregate = launcher.run_selected(
        robots=["franka"],
        model="test-model",
        max_repairs=0,
        output_root=tmp_path,
        pipeline_runner=lambda **_kwargs: {
            "ok": True,
            "stage1_ok": True,
            "external_blocked": False,
        },
    )

    assert code == 0
    row = aggregate["results"][0]
    assert row["duration_sec"] == 10.0
    assert row["monotonic_duration_sec"] == 0.25
    assert "(10.0s)" in capsys.readouterr().out


def test_status_field_cannot_turn_stage1_false_into_success(tmp_path):
    def contradictory_pipeline(**_kwargs):
        return {
            "status": "passed",
            "stage1_ok": False,
            "external_blocked": False,
        }

    code, aggregate = launcher.run_selected(
        robots=["franka"],
        model="test-model",
        max_repairs=0,
        output_root=tmp_path,
        pipeline_runner=contradictory_pipeline,
    )

    assert code != 0
    assert aggregate["results"][0]["status"] == "failed"


def test_external_block_stops_calls_and_marks_remaining_robots(tmp_path):
    calls = []

    def blocked_pipeline(**kwargs):
        calls.append(kwargs["robot_id"])
        return {
            "stage1_ok": False,
            "external_blocked": True,
            "error": "Holistic API unavailable",
        }

    code, aggregate = launcher.run_selected(
        robots=["franka", "so101", "h1"],
        model="test-model",
        max_repairs=3,
        output_root=tmp_path,
        pipeline_runner=blocked_pipeline,
    )

    assert code != 0
    assert calls == ["franka"]
    assert [row["status"] for row in aggregate["results"]] == [
        "external_blocked",
        "not_run_external_blocked",
        "not_run_external_blocked",
    ]
    assert all(row["error"] == "Holistic API unavailable" for row in aggregate["results"])


def test_launcher_exception_is_recorded_as_error(tmp_path):
    def exploding_pipeline(**_kwargs):
        raise RuntimeError("pipeline setup failed")

    code, aggregate = launcher.run_selected(
        robots=["franka"],
        model="test-model",
        max_repairs=3,
        output_root=tmp_path,
        pipeline_runner=exploding_pipeline,
    )

    assert code != 0
    row = aggregate["results"][0]
    assert row["status"] == "error"
    assert "pipeline setup failed" in row["error"]


def test_catalog_robot_is_not_excluded_by_old_diagnostic_results(tmp_path):
    code, result = launcher.run_selected(
        robots=["piper"], model="fixture", max_repairs=0, output_root=tmp_path,
        pipeline_runner=lambda **_kwargs: {"ok": True, "stage1_ok": True})
    assert code == 0
    assert result["selected_robots"] == ["piper"]


def test_launcher_export_failure_cannot_be_hidden_by_successful_validation(tmp_path):
    code, aggregate = launcher.run_selected(
        robots=["piper"], model="fixture", max_repairs=0, output_root=tmp_path,
        pipeline_runner=lambda **_kwargs: {
            "ok": False, "stage1_ok": True, "export_ok": False, "error": "export failed",
        },
    )
    assert code == 1
    assert aggregate["all_ok"] is False
    assert aggregate["all_stage1_ok"] is True
    assert aggregate["results"][0]["status"] == "failed"


def test_launcher_early_stop_and_demo_arguments_are_forwarded_without_validation_claim(tmp_path):
    demo = tmp_path / "demo.yaml"
    def pipeline(**kwargs):
        assert kwargs["stop_after"] == "design"
        assert kwargs["enable_demo"] is True
        assert kwargs["demo_config_path"] == demo
        return {"ok": True, "stage1_ok": False, "stop_after": "design"}
    code, aggregate = launcher.run_selected(
        robots=["piper"], model="fixture", max_repairs=0, output_root=tmp_path,
        stop_after="design", enable_demo=True, demo_config_path=demo,
        pipeline_runner=pipeline,
    )
    assert code == 0
    assert aggregate["all_ok"] is True
    assert aggregate["all_stage1_ok"] is False
    assert aggregate["results"][0]["stop_after"] == "design"
    args = launcher.build_parser().parse_args([
        "--robots", "piper", "--stop-after", "export", "--enable-demo", "--demo-config", str(demo),
    ])
    assert args.stop_after == "export" and args.enable_demo and args.demo_config == demo
