"""One fresh, non-formal corrected-R1 ReCAP episode."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoadapter2.b2.episode_runner import (
    _finite_json_object,
    _positive_integer,
    _positive_number,
    _read_object,
    _render_definition,
    _required_identifier,
    _unique_mapping,
    _validate_capability_design_identity,
)
from autoadapter2.b2.recap import RecapBudgets, RecapModelClient
from autoadapter2.b2.session_runner import (
    RecapWorkerSessionConfig,
    run_recap_worker_session,
)
from autoadapter2.b2.task_public_observation import (
    build_b2_public_task_projection,
)
from autoadapter2.harness.runner import HarnessError
from autoadapter2.libraries import RobotPackage

from .harness import (
    corrected_suite_identity,
    evaluate_corrected_task_harness,
)


@dataclass(frozen=True)
class CorrectedEpisodeConfig:
    task_suite_path: Path
    robot_configuration_id: str
    task_id: str
    replicate_id: str
    driver_path: Path
    capability_design_path: Path
    output_dir: Path
    record_video: bool = True
    wall_timeout_s: float = 120.0


def run_corrected_episode(
    *,
    config: CorrectedEpisodeConfig,
    package: RobotPackage,
    model: RecapModelClient,
    budgets: RecapBudgets | None = None,
) -> dict[str, Any]:
    _required_identifier(config.robot_configuration_id, "robot_configuration_id")
    _required_identifier(config.task_id, "task_id")
    _required_identifier(config.replicate_id, "replicate_id")
    if config.robot_configuration_id != package.robot_configuration_id:
        raise HarnessError("corrected episode robot does not match package")
    if (
        isinstance(config.wall_timeout_s, bool)
        or not isinstance(config.wall_timeout_s, (int, float))
        or not math.isfinite(float(config.wall_timeout_s))
        or float(config.wall_timeout_s) <= 0.0
    ):
        raise HarnessError("corrected episode wall timeout must be positive")
    if not isinstance(config.record_video, bool):
        raise HarnessError("corrected episode record_video must be boolean")

    task_suite_path = config.task_suite_path.resolve()
    driver_path = config.driver_path.resolve()
    capability_design_path = config.capability_design_path.resolve()
    output_dir = config.output_dir.resolve()
    suite = _read_object(task_suite_path, label="corrected task suite")
    capability_design = _read_object(
        capability_design_path, label="corrected capability design"
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
        raise HarnessError("corrected episode session result must be an object")
    controller = session_result.get("controller")
    worker = session_result.get("worker")
    if not isinstance(controller, Mapping) or not isinstance(worker, Mapping):
        raise HarnessError("corrected episode session result is incomplete")
    harness = evaluate_corrected_task_harness(
        package=package,
        task_suite_path=task_suite_path,
        instance_id=episode["instance_id"],
        replicate_id=config.replicate_id,
        session_result=session_result,
    )
    return _finite_json_object(
        {
            "audit_identity": episode["audit_identity"],
            "formal_episode": False,
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
        label="corrected R1 episode result",
    )


def _sealed_episode(
    *,
    suite: Mapping[str, Any],
    package: RobotPackage,
    robot_configuration_id: str,
    task_id: str,
    replicate_id: str,
) -> dict[str, Any]:
    if suite.get("schema_version") != "1.0" or suite.get("formal_episode") is not False:
        raise HarnessError("corrected task suite has an incompatible identity")
    audit_identity = corrected_suite_identity(suite)
    robot = _unique_mapping(
        suite.get("robot_suites"),
        key="robot_configuration_id",
        value=robot_configuration_id,
        label="corrected robot suite",
    )
    if (
        robot.get("package_version") != package.package_version
        or robot.get("task_snapshot_id") != package.snapshot_id
    ):
        raise HarnessError("corrected task suite does not match package identity")
    task = _unique_mapping(
        robot.get("tasks"),
        key="task_id",
        value=task_id,
        label="corrected sealed task",
    )
    instance_id = task.get("private_instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        raise HarnessError("corrected sealed task has no private instance")
    replicate = _unique_mapping(
        task.get("replicate_inputs"),
        key="replicate_id",
        value=replicate_id,
        label="corrected sealed replicate",
    )
    projection = task.get("public_projection")
    if not isinstance(projection, Mapping) or projection.get("task_id") != task_id:
        raise HarnessError("corrected sealed task has no public projection")
    public_task = build_b2_public_task_projection(
        task_definition={
            "task_id": projection.get("task_id"),
            "name": projection.get("name"),
            "description": projection.get("objective"),
        },
        public_arguments={"request": projection.get("request")},
    )
    if public_task.get("robot_configuration_id") != robot_configuration_id:
        raise HarnessError("corrected public task robot identity is invalid")
    budget = task.get("episode_budget")
    rendering = task.get("rendering")
    reset = replicate.get("reset")
    if not isinstance(budget, Mapping) or not isinstance(rendering, Mapping):
        raise HarnessError("corrected task has no episode/rendering definition")
    if not isinstance(reset, Mapping):
        raise HarnessError("corrected replicate has no reset")
    if replicate.get("reset_seed") is not None or replicate.get(
        "reset_seed_applied"
    ) is not False:
        raise HarnessError("corrected replicate reset-seed declaration is invalid")
    raw_scene = replicate.get("scene_entrypoint")
    if not isinstance(raw_scene, str) or not raw_scene:
        raise HarnessError("corrected replicate has no scene entrypoint")
    relative_scene = Path(raw_scene)
    if relative_scene.is_absolute():
        raise HarnessError("corrected scene entrypoint must be package-relative")
    scene_path = (package.root / relative_scene).resolve()
    try:
        scene_path.relative_to((package.root / "assets").resolve())
    except ValueError as exc:
        raise HarnessError("corrected scene escapes package assets") from exc
    if not scene_path.is_file():
        raise HarnessError(f"corrected scene is absent: {scene_path}")
    return {
        "audit_identity": audit_identity,
        "instance_id": instance_id,
        "scene_path": scene_path,
        "reset": _finite_json_object(reset, label="corrected reset"),
        "max_steps": _positive_integer(budget.get("max_steps"), "max_steps"),
        "max_sim_time_s": _positive_number(
            budget.get("timeout_sim_s"), "timeout_sim_s"
        ),
        "sample_hz": _positive_number(budget.get("sample_hz"), "sample_hz"),
        "render": _render_definition(rendering),
        "public_task": public_task,
    }


__all__ = ["CorrectedEpisodeConfig", "run_corrected_episode"]
