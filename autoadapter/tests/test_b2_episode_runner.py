from __future__ import annotations

import json
import textwrap
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import autoadapter2.b2.episode_runner as episode_runner
from autoadapter2.b2.episode_runner import (
    B2DiagnosticEpisodeConfig,
    run_b2_diagnostic_episode,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TASK_SUITE_PATH = (
    REPOSITORY_ROOT
    / "experiment"
    / "experiment1b_use"
    / "config"
    / "task_suite"
    / "task_suite.json"
)
SO101_PACKAGE_ROOT = (
    REPOSITORY_ROOT
    / "autoadapter"
    / "libraries"
    / "robots"
    / "robotstudio_so101"
    / "1.0.0"
)
SO101_DESIGN_PATH = (
    REPOSITORY_ROOT
    / "experiment"
    / "experiment1b_use"
    / "validation"
    / "reference"
    / "resolved"
    / "robotstudio_so101"
    / "capability_design.json"
)


def _package(root: Path, *, robot_id: str = "unitree-go2-stock-12dof") -> Any:
    if robot_id == "robotstudio_so101":
        snapshot_id = "robotstudio-so101-source-protocols-2026-08-18-v6"
    else:
        snapshot_id = "unitree-go2-source-protocols-2026-08-18-v4"
    return SimpleNamespace(
        root=root,
        robot_configuration_id=robot_id,
        package_version="1.0.0",
        snapshot_id=snapshot_id,
    )


def _write_mock_suite(path: Path) -> None:
    task = {
        "task_id": "GO2-T16",
        "private_instance_id": "go2-go2-t16",
        "public_projection": {
            "task_id": "GO2-T16",
            "name": "Barkour pause-table transition",
            "objective": "Move to the end table and hold.",
            "request": {
                "task_id": "GO2-T16",
                "task_parameters": {
                    "duration_s": 10.0,
                    "end_table_center_m": [1.8, 0.0, 0.37],
                },
            },
        },
        "episode_budget": {
            "timeout_sim_s": 12.0,
            "max_steps": 5500,
            "sample_hz": 20,
        },
        "rendering": {
            "enabled": True,
            "continuous_episode_video_required": True,
            "video_fps": 10,
            "video_width": 800,
            "video_height": 600,
            "camera": "evidence",
        },
        "replicate_inputs": [
            {
                "replicate_id": replicate_id,
                "scene_entrypoint": "assets/task.xml",
                "reset": {"kind": "keyframe", "name": "task_start"},
                "reset_seed": None,
                "reset_seed_applied": False,
            }
            for replicate_id in ("R1", "R2")
        ],
    }
    path.write_text(
        json.dumps(
            {
                "artifact_type": "b2_recap_task_suite",
                "schema_version": "1.0",
                "authority": {"document_id": "AA2-B2", "revision": "0.1.4"},
                "robot_suites": [
                    {
                        "robot_configuration_id": "unitree-go2-stock-12dof",
                        "package_version": "1.0.0",
                        "task_snapshot_id": (
                            "unitree-go2-source-protocols-2026-08-18-v4"
                        ),
                        "tasks": [task],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_runner_passes_unique_video_paths_and_keeps_false_success_separate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "robot"
    assets = package_root / "assets"
    assets.mkdir(parents=True)
    (assets / "task.xml").write_text("<mujoco/>", encoding="utf-8")
    suite_path = tmp_path / "task_suite.json"
    design_path = tmp_path / "capability_design.json"
    driver_path = tmp_path / "driver.py"
    _write_mock_suite(suite_path)
    design = {
        "artifact_type": "b1_fixed_capability_design",
        "schema_version": "1.0",
        "robot_configuration_id": "unitree-go2-stock-12dof",
        "package_version": "1.0.0",
        "task_snapshot_id": "unitree-go2-source-protocols-2026-08-18-v4",
    }
    design_path.write_text(json.dumps(design), encoding="utf-8")
    driver_path.write_text("# fixed diagnostic driver\n", encoding="utf-8")

    session_calls: list[dict[str, Any]] = []
    harness_calls: list[dict[str, Any]] = []

    def fake_session(**kwargs: Any) -> dict[str, Any]:
        session_calls.append(kwargs)
        return {
            "controller": {
                "status": "CONTROLLER_FINISHED",
                "capability_calls": 0,
            },
            "initial_public_state": {"simulation_time_s": 0.0},
            "worker": {"worker_completed": True, "physical_evidence": {}},
        }

    def fake_harness(**kwargs: Any) -> dict[str, Any]:
        harness_calls.append(kwargs)
        assert kwargs["session_result"] is session_calls[-1]["returned"]
        return {
            "physical_harness_verdict": "FAIL",
            "task_metric_passed": False,
        }

    def recording_session(**kwargs: Any) -> dict[str, Any]:
        result = fake_session(**kwargs)
        session_calls[-1]["returned"] = result
        return result

    monkeypatch.setattr(episode_runner, "run_recap_worker_session", recording_session)
    monkeypatch.setattr(episode_runner, "evaluate_b2_task_harness", fake_harness)

    config = B2DiagnosticEpisodeConfig(
        task_suite_path=suite_path,
        robot_configuration_id="unitree-go2-stock-12dof",
        task_id="GO2-T16",
        replicate_id="R1",
        driver_path=driver_path,
        capability_design_path=design_path,
        output_dir=tmp_path / "output",
    )
    first = run_b2_diagnostic_episode(
        config=config,
        package=_package(package_root),
        model=object(),
    )
    second = run_b2_diagnostic_episode(
        config=replace(config, replicate_id="R2"),
        package=_package(package_root),
        model=object(),
    )

    first_worker_config = session_calls[0]["config"]
    second_worker_config = session_calls[1]["config"]
    assert first_worker_config.video_path is not None
    assert second_worker_config.video_path is not None
    assert first_worker_config.video_path != second_worker_config.video_path
    assert first_worker_config.video_path.name.endswith("__GO2-T16__R1.mp4")
    assert first_worker_config.render == {
        "enabled": True,
        "width": 800,
        "height": 600,
        "fps": 10.0,
        "camera": "evidence",
    }
    assert session_calls[0]["capability_design"] == design
    assert set(session_calls[0]["public_task"]) == {
        "projection_revision",
        "robot_configuration_id",
        "task_id",
        "task_name",
        "objective",
        "task_parameters",
    }
    assert harness_calls[0]["instance_id"] == "go2-go2-t16"
    assert harness_calls[0]["replicate_id"] == "R1"

    assert first["controller"]["status"] == "CONTROLLER_FINISHED"
    assert first["harness"]["physical_harness_verdict"] == "FAIL"
    assert second["controller"]["status"] == "CONTROLLER_FINISHED"
    assert second["harness"]["physical_harness_verdict"] == "FAIL"
    assert "physical_harness_verdict" not in first["controller"]


class _ImmediateFinishModel:
    def generate_recap_json(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "reasoning_summary": "No capability action is needed for this canary.",
            "subtasks": [],
        }


def test_real_mujoco_episode_rejects_controller_only_completion(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mujoco")
    driver_path = tmp_path / "driver.py"
    driver_path.write_text(
        textwrap.dedent(
            """
            class Driver:
                def move_end_effector_to_position(self, request):
                    del request

                def trace_cartesian_path(self, request):
                    del request

                def set_gripper_opening(self, request):
                    del request

                def approach_until_contact(self, request):
                    del request

                def move_cartesian_offset_and_return(self, request):
                    del request

                def set_wrist_roll(self, request):
                    del request

            def build(*, model, data):
                del model, data
                return Driver()
            """
        ),
        encoding="utf-8",
    )

    result = run_b2_diagnostic_episode(
        config=B2DiagnosticEpisodeConfig(
            task_suite_path=TASK_SUITE_PATH,
            robot_configuration_id="robotstudio_so101",
            task_id="mw_push_to_goal",
            replicate_id="R1",
            driver_path=driver_path,
            capability_design_path=SO101_DESIGN_PATH,
            output_dir=tmp_path / "evidence",
            record_video=False,
            wall_timeout_s=20.0,
        ),
        package=_package(SO101_PACKAGE_ROOT, robot_id="robotstudio_so101"),
        model=_ImmediateFinishModel(),
    )

    assert result["controller"]["status"] == "CONTROLLER_FINISHED"
    assert result["controller"]["capability_calls"] == 0
    assert result["worker"]["controller_protocol_completed"] is True
    assert result["worker"]["method_invoked"] is False
    assert result["harness"]["physical_execution_passed"] is False
    assert result["harness"]["physical_harness_verdict"] == "FAIL"
    assert result["episode"]["video_path"] is None
