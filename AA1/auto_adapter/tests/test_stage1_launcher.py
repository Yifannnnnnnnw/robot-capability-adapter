"""Focused tests for the thin Stage 1 diagnostic launcher.

The fake pipeline is deliberately confined to this test module; no model,
SDK, or simulation call belongs in launcher checks.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run" / "run_stage1.py"
spec = importlib.util.spec_from_file_location("stage1_launcher_under_test", SCRIPT)
assert spec is not None and spec.loader is not None
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_default_selection_skips_the_five_already_passed_robots():
    args = launcher.build_parser().parse_args([])

    assert args.robots == list(launcher.DEFAULT_ROBOTS)
    assert set(args.robots).isdisjoint(launcher.PASSED_ROBOTS)


def test_launcher_forwards_api_arguments_and_accepts_pipeline_result_without_files(
    tmp_path, capsys
):
    calls = []

    def fake_pipeline(**kwargs):
        calls.append(kwargs)
        # The launcher must consume the pipeline verdict, rather than trying
        # to evaluate a workspace or run another validation subprocess.
        return {"stage1_ok": True, "external_blocked": False}

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
    }]
    assert aggregate["selected_robots"] == ["franka"]
    assert aggregate["results"][0]["status"] == "stage1_ok"
    launch_path = Path(aggregate["launch_path"])
    assert launch_path.is_file()
    persisted = json.loads(launch_path.read_text())
    assert persisted["results"][0]["status"] == "stage1_ok"
    assert "franka:" in capsys.readouterr().out


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


@pytest.mark.parametrize("robot", sorted(launcher.PASSED_ROBOTS))
def test_already_passed_robot_is_rejected(robot):
    with pytest.raises(ValueError, match="already-passed"):
        launcher.run_selected(
            robots=[robot],
            model="test-model",
            max_repairs=3,
            output_root=None,
            pipeline_runner=lambda **_kwargs: {"stage1_ok": True},
        )
