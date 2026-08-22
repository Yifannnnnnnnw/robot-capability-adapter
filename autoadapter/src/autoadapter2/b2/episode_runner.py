"""One diagnostic AA2-B2 task episode with an independent Harness verdict."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoadapter2.b2.recap import RecapBudgets, RecapModelClient
from autoadapter2.b2.session_runner import (
    RecapWorkerSessionConfig,
    run_recap_worker_session,
)
from autoadapter2.b2.task_harness import evaluate_b2_task_harness
from autoadapter2.b2.task_public_observation import (
    build_b2_public_task_projection,
)
from autoadapter2.harness.runner import HarnessError
from autoadapter2.libraries import RobotPackage


@dataclass(frozen=True)
class B2DiagnosticEpisodeConfig:
    """Fixed inputs and output location for one fresh diagnostic episode."""

    task_suite_path: Path
    robot_configuration_id: str
    task_id: str
    replicate_id: str
    driver_path: Path
    capability_design_path: Path
    output_dir: Path
    record_video: bool = True
    wall_timeout_s: float = 120.0


def run_b2_diagnostic_episode(
    *,
    config: B2DiagnosticEpisodeConfig,
    package: RobotPackage,
    model: RecapModelClient,
    budgets: RecapBudgets | None = None,
) -> dict[str, Any]:
    """Run one sealed task/replicate and then issue a separate physical verdict.

    Controller completion is returned as controller evidence only.  The
    ``harness`` member is computed independently from the persistent worker's
    physical evidence and is the only task-verdict-bearing member.
    """

    _required_identifier(config.robot_configuration_id, "robot_configuration_id")
    _required_identifier(config.task_id, "task_id")
    _required_identifier(config.replicate_id, "replicate_id")
    if config.robot_configuration_id != package.robot_configuration_id:
        raise HarnessError("B2 episode robot does not match the selected package")
    if (
        isinstance(config.wall_timeout_s, bool)
        or not isinstance(config.wall_timeout_s, (int, float))
        or not math.isfinite(float(config.wall_timeout_s))
        or float(config.wall_timeout_s) <= 0.0
    ):
        raise HarnessError("B2 episode wall_timeout_s must be positive and finite")
    if not isinstance(config.record_video, bool):
        raise HarnessError("B2 episode record_video must be boolean")

    task_suite_path = config.task_suite_path.resolve()
    driver_path = config.driver_path.resolve()
    capability_design_path = config.capability_design_path.resolve()
    output_dir = config.output_dir.resolve()
    suite = _read_object(task_suite_path, label="B2 task suite")
    capability_design = _read_object(
        capability_design_path,
        label="B2 capability design",
    )
    _validate_capability_design_identity(
        capability_design,
        package=package,
        robot_configuration_id=config.robot_configuration_id,
    )
    episode = _sealed_episode(
        suite=suite,
        package=package,
        robot_configuration_id=config.robot_configuration_id,
        task_id=config.task_id,
        replicate_id=config.replicate_id,
    )

    render = dict(episode["render"])
    render["enabled"] = bool(render["enabled"] and config.record_video)
    video_path = (
        output_dir
        / "videos"
        / (
            f"{config.robot_configuration_id}__{config.task_id}__"
            f"{config.replicate_id}.mp4"
        )
        if render["enabled"]
        else None
    )

    session_result = run_recap_worker_session(
        config=RecapWorkerSessionConfig(
            driver_path=driver_path,
            scene_path=episode["scene_path"],
            robot_configuration_id=config.robot_configuration_id,
            reset=episode["reset"],
            max_steps=episode["max_steps"],
            max_sim_time_s=episode["max_sim_time_s"],
            sample_hz=episode["sample_hz"],
            wall_timeout_s=float(config.wall_timeout_s),
            render=render,
            video_path=video_path,
        ),
        capability_design=capability_design,
        public_task=episode["public_task"],
        model=model,
        budgets=budgets,
    )
    if not isinstance(session_result, Mapping):
        raise HarnessError("B2 episode session result must be an object")
    controller = session_result.get("controller")
    worker = session_result.get("worker")
    if not isinstance(controller, Mapping) or not isinstance(worker, Mapping):
        raise HarnessError("B2 episode session result is incomplete")

    harness = evaluate_b2_task_harness(
        package=package,
        task_suite_path=task_suite_path,
        instance_id=episode["instance_id"],
        replicate_id=config.replicate_id,
        session_result=session_result,
    )
    if not isinstance(harness, Mapping):
        raise HarnessError("B2 task Harness report must be an object")

    return _finite_json_object(
        {
            "episode": {
                "robot_configuration_id": config.robot_configuration_id,
                "task_id": config.task_id,
                "instance_id": episode["instance_id"],
                "replicate_id": config.replicate_id,
                "scene_path": str(episode["scene_path"]),
                "driver_path": str(driver_path),
                "capability_design_path": str(capability_design_path),
                "video_path": str(video_path) if video_path is not None else None,
            },
            "controller": dict(controller),
            "worker": dict(worker),
            "harness": dict(harness),
        },
        label="B2 diagnostic episode result",
    )


def _sealed_episode(
    *,
    suite: Mapping[str, Any],
    package: RobotPackage,
    robot_configuration_id: str,
    task_id: str,
    replicate_id: str,
) -> dict[str, Any]:
    authority = suite.get("authority")
    if (
        suite.get("artifact_type") != "b2_recap_task_suite"
        or suite.get("schema_version") != "1.0"
        or not isinstance(authority, Mapping)
        or authority.get("document_id") != "AA2-B2"
        or authority.get("revision") != "0.1.1"
    ):
        raise HarnessError("B2 task suite has an incompatible identity")

    robot = _unique_mapping(
        suite.get("robot_suites"),
        key="robot_configuration_id",
        value=robot_configuration_id,
        label="B2 robot suite",
    )
    if (
        robot.get("package_version") != package.package_version
        or robot.get("task_snapshot_id") != package.snapshot_id
    ):
        raise HarnessError("B2 task suite does not match the selected package identity")
    task = _unique_mapping(
        robot.get("tasks"),
        key="task_id",
        value=task_id,
        label="sealed B2 task",
    )
    instance_id = task.get("private_instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        raise HarnessError("sealed B2 task has no private_instance_id")
    replicate = _unique_mapping(
        task.get("replicate_inputs"),
        key="replicate_id",
        value=replicate_id,
        label="sealed B2 replicate",
    )

    public_projection = task.get("public_projection")
    if (
        not isinstance(public_projection, Mapping)
        or public_projection.get("task_id") != task_id
    ):
        raise HarnessError("sealed B2 task has no public projection")
    public_task = build_b2_public_task_projection(
        task_definition={
            "task_id": public_projection.get("task_id"),
            "name": public_projection.get("name"),
            "description": public_projection.get("objective"),
        },
        public_arguments={"request": public_projection.get("request")},
    )
    if public_task.get("robot_configuration_id") != robot_configuration_id:
        raise HarnessError("sealed B2 public task does not match the selected robot")

    episode_budget = task.get("episode_budget")
    rendering = task.get("rendering")
    reset = replicate.get("reset")
    if not isinstance(episode_budget, Mapping):
        raise HarnessError("sealed B2 task has no episode budget")
    if not isinstance(rendering, Mapping):
        raise HarnessError("sealed B2 task has no rendering definition")
    if not isinstance(reset, Mapping):
        raise HarnessError("sealed B2 replicate has no reset")
    if replicate.get("reset_seed") is not None or replicate.get(
        "reset_seed_applied"
    ) is not False:
        raise HarnessError("sealed B2 replicate reset-seed declaration is invalid")

    max_steps = _positive_integer(episode_budget.get("max_steps"), "max_steps")
    max_sim_time_s = _positive_number(
        episode_budget.get("timeout_sim_s"),
        "timeout_sim_s",
    )
    sample_hz = _positive_number(episode_budget.get("sample_hz"), "sample_hz")
    render = _render_definition(rendering)

    raw_scene = replicate.get("scene_entrypoint")
    if not isinstance(raw_scene, str) or not raw_scene:
        raise HarnessError("sealed B2 replicate has no scene_entrypoint")
    relative_scene = Path(raw_scene)
    if relative_scene.is_absolute():
        raise HarnessError("sealed B2 scene_entrypoint must be package-relative")
    scene_path = (package.root / relative_scene).resolve()
    assets_root = (package.root / "assets").resolve()
    try:
        scene_path.relative_to(assets_root)
    except ValueError as exc:
        raise HarnessError("sealed B2 scene escapes package assets") from exc
    if not scene_path.is_file():
        raise HarnessError(f"sealed B2 scene is absent: {scene_path}")

    return {
        "instance_id": instance_id,
        "scene_path": scene_path,
        "reset": _finite_json_object(reset, label="sealed B2 reset"),
        "max_steps": max_steps,
        "max_sim_time_s": max_sim_time_s,
        "sample_hz": sample_hz,
        "render": render,
        "public_task": public_task,
    }


def _render_definition(value: Mapping[str, Any]) -> dict[str, Any]:
    enabled = value.get("enabled")
    continuous = value.get("continuous_episode_video_required")
    if not isinstance(enabled, bool) or not isinstance(continuous, bool):
        raise HarnessError("sealed B2 rendering flags must be boolean")
    if enabled and not continuous:
        raise HarnessError("sealed B2 rendering must require continuous episode video")
    return {
        "enabled": enabled,
        "width": _positive_integer(value.get("video_width"), "video_width"),
        "height": _positive_integer(value.get("video_height"), "video_height"),
        "fps": _positive_number(value.get("video_fps"), "video_fps"),
        "camera": value.get("camera", -1),
    }


def _validate_capability_design_identity(
    design: Mapping[str, Any],
    *,
    package: RobotPackage,
    robot_configuration_id: str,
) -> None:
    if (
        design.get("artifact_type") != "b1_fixed_capability_design"
        or design.get("schema_version") != "1.0"
        or design.get("robot_configuration_id") != robot_configuration_id
        or design.get("package_version") != package.package_version
        or design.get("task_snapshot_id") != package.snapshot_id
    ):
        raise HarnessError(
            "B2 capability design does not match the selected package identity"
        )


def _unique_mapping(
    values: Any,
    *,
    key: str,
    value: str,
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(values, list):
        raise HarnessError(f"{label} collection must be a list")
    matches = [
        item
        for item in values
        if isinstance(item, Mapping) and item.get(key) == value
    ]
    if len(matches) != 1:
        raise HarnessError(f"{label} {value!r} must exist exactly once")
    return matches[0]


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HarnessError(f"cannot read {label} {path}") from exc
    if not isinstance(value, dict):
        raise HarnessError(f"{label} must be an object")
    return value


def _required_identifier(value: Any, label: str) -> str:
    allowed = (
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789._-"
    )
    if (
        not isinstance(value, str)
        or not value
        or any(character not in allowed for character in value)
    ):
        raise HarnessError(f"B2 episode {label} is invalid")
    return value


def _positive_integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise HarnessError(f"sealed B2 {label} must be a positive integer")
    return value


def _positive_number(value: Any, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise HarnessError(f"sealed B2 {label} must be positive and finite")
    return float(value)


def _finite_json_object(value: Any, *, label: str) -> dict[str, Any]:
    try:
        copied = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise HarnessError(f"{label} must be finite JSON") from exc
    if not isinstance(copied, dict):
        raise HarnessError(f"{label} must be an object")
    return copied


__all__ = ["B2DiagnosticEpisodeConfig", "run_b2_diagnostic_episode"]
