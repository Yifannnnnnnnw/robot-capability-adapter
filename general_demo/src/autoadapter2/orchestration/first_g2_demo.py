"""Entry point assembly for one actual first-Demo G2 robot run.

This module deliberately stays outside the generic runner.  It resolves one
frozen run snapshot, creates the model adapters around :class:`ModelApiClient`,
injects the robot-specific Session Runner, and persists the evidence returned by
``GeneralDemoRunner``.  It does not provide a fixture Session Runner or a
fixture model response for production callers.
"""

from __future__ import annotations

import copy
import importlib
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from ..demo import CriterionEvaluator, DemoTask, EvaluationRobotSession
from ..evaluation import (
    ClosedEvaluationVideo,
    FrozenVideoProfile,
    verify_closed_evaluation_video,
)
from ..evaluation.ffmpeg import FFmpegVideoEncoder
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash, sha256_bytes
from ..generation import ModelApiClient, ModelApiConfig, Stage1Config
from ..implementation import Stage2Config
from ..integration import ExperimentIntegrationGate, write_stable_json
from ..integration.artifacts import load_json_artifact, load_run_snapshot, verify_file_reference
from ..libraries import TasksLibrary
from ..orchestration.demo_runner import (
    DemoModelAdapters,
    DemoRunPlan,
    DemoRunResult,
    GeneralDemoRunner,
)
from ..validation import RepairConfig, ValidationAProfile


G2_PROFILE = {
    "profile_id": "g2-reusable-effect",
    "version": "1.0.0",
    "granularity": "G2",
}

ROBOT_CONFIGURATIONS = {
    "so-arm101": {
        "robot_model_id": "so-arm101",
        "robot_configuration_id": "so-arm101-follower-stock-gripper",
        "task_catalog_version": "1.0.0",
    },
    "unitree-go2": {
        "robot_model_id": "unitree-go2",
        "robot_configuration_id": "unitree-go2-stock-12dof",
        "task_catalog_version": "1.0.0",
    },
}

_DEFAULT_VIDEO_PROFILE = FrozenVideoProfile(
    profile_id="general-demo-external-evaluation",
    profile_version="1.0.0",
    camera="external-evaluation",
    view="robot-and-task-scene",
    fps=30,
    width=640,
    height=480,
    container="matroska",
    codec="ffv1",
)
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


class RobotSessionFactory(Protocol):
    """Injected constructor for one exact robot/configuration Session Runner."""

    def __call__(
        self,
        robot: str,
        manifest_path: Path,
        run_directory: Path,
    ) -> EvaluationRobotSession: ...


class ModelClient(Protocol):
    """The subset of ModelApiClient used by the existing runner."""

    def generate_json(self, stage: str, prompt: str, inputs: Mapping[str, Any]) -> dict[str, Any]: ...

    def repair(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def react(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class FirstG2DemoConfig:
    """Inputs for exactly one robot-scoped first-Demo run."""

    root: str | Path
    robot: str
    integration_manifest_path: str | Path
    run_snapshot_path: str | Path
    readiness_report_path: str | Path
    robot_session_factory: RobotSessionFactory
    output_root: str | Path | None = None
    blue_line_input_paths: tuple[str | Path, ...] | None = None
    validation_a_profile: ValidationAProfile | Mapping[str, Any] | None = None
    validation_harness_config: Mapping[str, Any] | None = None
    video_profile: FrozenVideoProfile | None = None
    model_client: ModelClient | None = None
    criterion_evaluator: CriterionEvaluator | None = None
    test_only_allow_fixture_session: bool = False

    def __post_init__(self) -> None:
        if self.robot not in ROBOT_CONFIGURATIONS:
            raise ContractError(
                f"first G2 Demo robot must be one of {sorted(ROBOT_CONFIGURATIONS)}"
            )
        if not callable(self.robot_session_factory):
            raise ContractError("first G2 Demo requires an injected robot-session factory")
        if self.blue_line_input_paths is not None and len(self.blue_line_input_paths) != 3:
            raise ContractError(
                "first G2 Demo requires exactly three Blue Line input files"
            )
        if self.validation_a_profile is not None and not isinstance(
            self.validation_a_profile, (ValidationAProfile, Mapping)
        ):
            raise ContractError("validation_a_profile must be a ValidationAProfile or object")
        if self.validation_harness_config is not None and not isinstance(
            self.validation_harness_config, Mapping
        ):
            raise ContractError("validation_harness_config must be an object")


@dataclass(frozen=True)
class FirstG2DemoResult:
    """Persisted handoff for one completed or terminal first-Demo run."""

    robot: str
    run_id: str
    status: str
    run_directory: Path
    summary_path: Path
    seal_path: Path
    validation_video_references_path: Path
    demo_video_references_path: Path
    summary_hash: str
    runner_result: DemoRunResult


@dataclass(frozen=True)
class _VideoAttempt:
    phase: str
    execution_id: str
    directory: Path
    recording_id: str


class _VideoStore:
    """Allocate immutable FFmpeg attempt directories and retain their paths."""

    def __init__(self, run_directory: Path, run_id: str, profile: FrozenVideoProfile):
        self._run_directory = run_directory
        self._run_id = run_id
        self._profile = profile
        self._attempts: list[_VideoAttempt] = []
        self._persisted_attempts: set[Path] = set()

    def encoder(self, phase: str, execution_id: str) -> FFmpegVideoEncoder:
        if phase not in {"VALIDATION_B", "DEMO"}:
            raise ContractError("first G2 Demo video phase is invalid")
        safe_phase = phase.lower()
        safe_execution = _safe_component(execution_id)
        ordinal = len(self._attempts) + 1
        directory = self._run_directory / "videos" / safe_phase / f"{ordinal:04d}-{safe_execution}"
        recording_id = (
            f"validation-b-{self._run_id}-{execution_id}"
            if phase == "VALIDATION_B"
            else f"demo-{self._run_id}-{execution_id}"
        )
        if any(item.directory == directory for item in self._attempts):
            raise ContractError("first G2 Demo allocated a duplicate video attempt directory")
        self._attempts.append(_VideoAttempt(phase, execution_id, directory, recording_id))
        return FFmpegVideoEncoder(directory)

    def persist(
        self,
        phase: str,
        videos: Sequence[ClosedEvaluationVideo],
    ) -> list[dict[str, Any]]:
        references: list[dict[str, Any]] = []
        for video in videos:
            if not verify_closed_evaluation_video(video):
                raise ContractError("first G2 Demo received an unverified evaluation video closure")
            manifest = video.manifest
            recording_id = manifest.get("recording_id")
            attempt = next(
                (
                    item
                    for item in self._attempts
                    if item.phase == phase
                    and item.recording_id == recording_id
                    and item.directory not in self._persisted_attempts
                ),
                None,
            )
            if attempt is None:
                raise ContractError(
                    f"no FFmpeg attempt directory is bound to video {recording_id!r}"
                )
            self._persisted_attempts.add(attempt.directory)
            attempt.directory.mkdir(parents=True, exist_ok=True)
            manifest_path = attempt.directory / "manifest.json"
            _write_new_bytes(manifest_path, video.manifest_bytes)
            if content_hash(video.manifest_bytes) != video.manifest_content_hash:
                raise ContractError("evaluation video manifest hash changed before persistence")

            media_path: Path | None = None
            if video.media_content_hash is not None:
                media_path = attempt.directory / f"evaluation-video.{self._profile.container}"
                if media_path.is_symlink() or not media_path.is_file():
                    raise ContractError("evaluation video media file is missing")
                if content_hash(media_path.read_bytes()) != video.media_content_hash:
                    raise ContractError("evaluation video media hash does not match its closure")

            def relative(path: Path) -> str:
                return path.relative_to(self._run_directory).as_posix()

            references.append(
                {
                    "recording_id": recording_id,
                    "execution_id": manifest["bindings"].get("execution_id"),
                    "phase": phase,
                    "completion_status": video.completion_status,
                    "execution_disposition": video.execution_disposition,
                    "manifest": {
                        "path": relative(manifest_path),
                        "content_hash": video.manifest_content_hash,
                        "file_sha256": sha256_bytes(video.manifest_bytes),
                    },
                    "media": (
                        {
                            "path": relative(media_path),
                            "content_hash": video.media_content_hash,
                            "file_sha256": sha256_bytes(media_path.read_bytes()),
                        }
                        if media_path is not None
                        else None
                    ),
                }
            )
        return references


def run_first_g2_demo(config: FirstG2DemoConfig) -> FirstG2DemoResult:
    """Run one robot-scoped first-Demo G2 path and persist its evidence.

    The selected integration gate is verified before the injected Session Runner
    is constructed.  A fixture-only Session Runner is accepted only when the
    caller explicitly marks the invocation as test-only; the CLI never exposes
    that switch.
    """

    if not isinstance(config, FirstG2DemoConfig):
        raise ContractError("run_first_g2_demo requires FirstG2DemoConfig")
    root = Path(config.root).resolve()
    manifest_path = _resolve_top_level(root, config.integration_manifest_path)
    snapshot_path = _resolve_top_level(root, config.run_snapshot_path)
    readiness_path = _resolve_top_level(root, config.readiness_report_path)

    gate = ExperimentIntegrationGate(root)
    gate_result = gate.verify(manifest_path, snapshot_path, readiness_path)
    manifest = gate.inspect_manifest(manifest_path).value
    expected_robot = ROBOT_CONFIGURATIONS[config.robot]
    if any(manifest.get(field) != expected_robot[field] for field in ("robot_model_id", "robot_configuration_id")):
        raise ContractError(
            "selected integration manifest does not match the requested first-Demo robot"
        )

    snapshot = load_run_snapshot(snapshot_path).value
    run_id = snapshot["run_id"]
    if gate_result.run_id != run_id:
        raise ContractError("verified gate run_id does not match the selected snapshot")

    input_values = _resolve_run_inputs(root, snapshot, config.blue_line_input_paths)
    if input_values["g2_profile"] != G2_PROFILE:
        raise ContractError("the selected run snapshot does not freeze the first-Demo G2 profile")
    task_package = TasksLibrary(root / "general_demo/libraries/tasks").load(
        expected_robot["robot_configuration_id"], expected_robot["task_catalog_version"]
    )
    tasks = _tasks_from_canonical_library(task_package, run_id, input_values["task_set"])
    implementation_bundle = _find_implementation_bundle(input_values["library_views"])
    validation_a_profile = _resolve_validation_a_profile(
        config.validation_a_profile,
        input_values["library_views"],
        input_values["model_prompt_config"],
    )
    validation_harness_config = _resolve_harness_config(
        config.validation_harness_config,
        input_values["library_views"],
    )
    video_profile = config.video_profile or _video_profile_from_inputs(
        input_values["observation_profile"], validation_harness_config
    )
    public_state_schema = _public_state_schema(
        input_values["observation_profile"], tasks
    )
    stage1_config, stage2_config, repair_config, consumer_config = _budgets(
        input_values["budget"]
    )
    model_client = config.model_client or _model_client(input_values["model_prompt_config"])
    if not all(callable(getattr(model_client, name, None)) for name in ("generate_json", "repair", "react")):
        raise ContractError("first G2 Demo model client does not implement the ModelApiClient surface")

    run_directory = _new_run_directory(
        config.output_root or root / "general_demo/runs/first_g2_demo",
        run_id,
        config.robot,
    )
    session = config.robot_session_factory(config.robot, manifest_path, run_directory)
    if not isinstance(session, EvaluationRobotSession):
        raise ContractError("robot-session factory did not return an EvaluationRobotSession")
    if (
        session.evidence_scope == "TEST_FIXTURE_ONLY"
        and not config.test_only_allow_fixture_session
    ):
        raise ContractError(
            "production first G2 Demo refuses TEST_FIXTURE_ONLY physical evidence"
        )

    video_store = _VideoStore(run_directory, run_id, video_profile)
    models = DemoModelAdapters(
        stage1=model_client,
        blue_line=model_client,
        stage2=model_client,
        repair=model_client.repair,
        consumer=model_client.react,
    )
    plan = DemoRunPlan(
        run_id=run_id,
        integration_manifest_path=manifest_path,
        run_snapshot_path=snapshot_path,
        readiness_report_path=readiness_path,
        robot_public_projection=input_values["observation_profile"],
        g2_profile=input_values["g2_profile"],
        tasks=tasks,
        standards_snapshot=input_values["blue_line_inputs"][0],
        measurement_catalog=input_values["blue_line_inputs"][1],
        blue_line_policy=input_values["blue_line_inputs"][2],
        implementation_bundle=implementation_bundle,
        validation_a_profile=validation_a_profile,
        public_state_schema=public_state_schema,
        validation_harness_config=validation_harness_config,
        consumer_id=consumer_config["consumer_id"],
        consumer_max_steps=consumer_config["consumer_max_steps"],
        consumer_seed=consumer_config["consumer_seed"],
        demo_repetitions=consumer_config["demo_repetitions"],
        task_catalog_version=expected_robot["task_catalog_version"],
        stage1_config=stage1_config,
        stage2_config=stage2_config,
        repair_config=repair_config,
    )
    result = GeneralDemoRunner(
        root,
        models,
        session,
        video_profile,
        video_store.encoder,
        config.criterion_evaluator or evaluate_demo_criterion,
    ).run(plan)

    validation_references = video_store.persist("VALIDATION_B", result.validation_video_handles)
    demo_videos = tuple(
        trial.video_handle
        for trial in result.demo_trials
        if trial.video_handle is not None
    )
    demo_references = video_store.persist("DEMO", demo_videos)
    summary_path = run_directory / "summary.json"
    seal_path = run_directory / "summary.seal.json"
    validation_path = run_directory / "validation_video_references.json"
    demo_path = run_directory / "demo_video_references.json"
    summary_hash = write_stable_json(summary_path, result.summary)
    if summary_hash != result.summary_hash.removeprefix("sha256:"):
        raise ContractError("persisted first G2 Demo summary hash does not match the runner result")
    write_stable_json(seal_path, result.summary_seal)
    write_stable_json(
        validation_path,
        _video_reference_artifact(run_id, config.robot, validation_references),
    )
    write_stable_json(
        demo_path,
        _video_reference_artifact(run_id, config.robot, demo_references),
    )
    return FirstG2DemoResult(
        robot=config.robot,
        run_id=run_id,
        status=result.status,
        run_directory=run_directory,
        summary_path=summary_path,
        seal_path=seal_path,
        validation_video_references_path=validation_path,
        demo_video_references_path=demo_path,
        summary_hash=result.summary_hash,
        runner_result=result,
    )


def evaluate_demo_criterion(
    criterion: Mapping[str, Any], evidence: Mapping[str, Any]
) -> bool:
    """Apply the fixed task criterion to trusted Harness evidence only.

    The Session Runner owns sampling and physical-state acquisition.  This
    adapter only compares the Harness-provided aggregate measurements; it never
    reads Consumer returns or capability traces.
    """

    if not isinstance(criterion, Mapping) or not isinstance(evidence, Mapping):
        return False
    guard_ids = criterion.get("guard_ids", [])
    if not isinstance(guard_ids, list):
        return False
    guards = evidence.get("guard_results", evidence.get("guards", {}))
    if not isinstance(guards, Mapping) or any(guards.get(item) is not True for item in guard_ids):
        return False
    checks: list[Mapping[str, Any]] = []
    for field in ("checks", "motion_checks", "terminal_checks"):
        values = criterion.get(field, [])
        if not isinstance(values, list) or any(not isinstance(item, Mapping) for item in values):
            return False
        checks.extend(values)
    for check in checks:
        metric = check.get("metric")
        if not isinstance(metric, str) or not metric:
            return False
        actual = evidence.get(metric)
        if actual is None and isinstance(evidence.get("measurements"), Mapping):
            actual = evidence["measurements"].get(metric)
        if not _compare(actual, check.get("comparator"), check.get("value")):
            return False
    return bool(checks)


def load_factory(specification: str) -> RobotSessionFactory:
    """Load a production Session Runner factory from ``module:attribute``."""

    if not isinstance(specification, str) or ":" not in specification:
        raise ContractError("robot-session factory must use module:attribute syntax")
    module_name, attribute_name = specification.split(":", 1)
    if not module_name or not attribute_name:
        raise ContractError("robot-session factory must use module:attribute syntax")
    try:
        factory = getattr(importlib.import_module(module_name), attribute_name)
    except (ImportError, AttributeError) as exc:
        raise ContractError("could not import the robot-session factory") from exc
    if not callable(factory):
        raise ContractError("robot-session factory attribute is not callable")
    return factory


def _resolve_run_inputs(
    root: Path,
    snapshot: Mapping[str, Any],
    blue_line_input_paths: tuple[str | Path, ...] | None,
) -> dict[str, Any]:
    def value(reference: Mapping[str, Any]) -> dict[str, Any]:
        return load_json_artifact(verify_file_reference(root, reference)).value

    expected_blue_line = snapshot["blue_line_input_refs"]
    if not isinstance(expected_blue_line, list) or len(expected_blue_line) != 3:
        raise ContractError(
            "the frozen first-Demo snapshot must provide exactly three Blue Line inputs"
        )
    if blue_line_input_paths is None:
        blue_line_inputs = [value(item) for item in expected_blue_line]
    else:
        blue_line_inputs = []
        for supplied, expected in zip(blue_line_input_paths, expected_blue_line, strict=True):
            supplied_path = _resolve_top_level(root, supplied)
            expected_path = verify_file_reference(root, expected)
            if supplied_path != expected_path or sha256_bytes(supplied_path.read_bytes()) != expected["sha256"]:
                raise ContractError(
                    "supplied Blue Line input does not match the frozen run snapshot"
                )
            blue_line_inputs.append(load_json_artifact(supplied_path).value)

    return {
        "task_set": value(snapshot["task_set_ref"]),
        "g2_profile": value(snapshot["g2_profile_ref"]),
        "observation_profile": value(snapshot["observation_profile_ref"]),
        "model_prompt_config": value(snapshot["model_prompt_config_ref"]),
        "budget": value(snapshot["budget_ref"]),
        "blue_line_inputs": blue_line_inputs,
        "library_views": [value(item) for item in snapshot["library_view_refs"]],
    }


def _tasks_from_canonical_library(
    package: Any,
    run_id: str,
    task_set: Mapping[str, Any],
) -> tuple[DemoTask, ...]:
    public = package.demo_public_tasks(run_id)
    private = package.demo_private_criteria()
    frozen = task_set.get("tasks") if isinstance(task_set, Mapping) else None
    if not isinstance(frozen, list) or len(frozen) != 5:
        raise ContractError("frozen first-Demo task set must contain exactly five tasks")
    tasks: list[DemoTask] = []
    for public_task, criterion, raw in zip(public, private, frozen, strict=True):
        if not isinstance(raw, Mapping):
            raise ContractError("frozen first-Demo task set contains an invalid task")
        expected = {
            "requirement_id": public_task["requirement_id"],
            "task_id": public_task["task_id"],
            "description": public_task["description"],
            "private_criterion": criterion,
        }
        if any(raw.get(key) != value for key, value in expected.items()):
            raise ContractError(
                "frozen task set does not match the canonical fixed-five Tasks Library"
            )
        if not isinstance(raw.get("public_state"), Mapping):
            raise ContractError("frozen first-Demo task lacks public structured state")
        tasks.append(
            DemoTask(
                requirement_id=public_task["requirement_id"],
                task_id=public_task["task_id"],
                description=public_task["description"],
                public_state=raw["public_state"],
                private_criterion=criterion,
            )
        )
    return tuple(tasks)


def _find_implementation_bundle(library_views: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    matches = [
        dict(value)
        for value in library_views
        if value.get("artifact_type") == "stage2_implementation_bundle"
    ]
    if len(matches) != 1:
        raise ContractError(
            "the frozen run snapshot must provide exactly one Stage 2 Implementation Bundle"
        )
    return matches[0]


def _resolve_validation_a_profile(
    supplied: ValidationAProfile | Mapping[str, Any] | None,
    library_views: Sequence[Mapping[str, Any]],
    model_prompt_config: Mapping[str, Any],
) -> ValidationAProfile:
    raw: Any = supplied
    if raw is None:
        matches = [
            value
            for value in library_views
            if value.get("artifact_type") in {"validation_a_profile", "validation_a_config"}
            or {"sdk_facade_members", "fixture_probes"}.issubset(value)
        ]
        nested = model_prompt_config.get("validation_a_profile")
        if nested is not None:
            matches.append(nested)
        if len(matches) != 1:
            raise ContractError(
                "the first G2 Demo requires one frozen Validation A profile input"
            )
        raw = matches[0]
    if isinstance(raw, ValidationAProfile):
        return raw
    if not isinstance(raw, Mapping):
        raise ContractError("Validation A profile input must be an object")
    profile = raw.get("profile") if isinstance(raw.get("profile"), Mapping) else raw
    members = profile.get("sdk_facade_members")
    probes = profile.get("fixture_probes")
    if not isinstance(members, Mapping) or not isinstance(probes, Mapping):
        raise ContractError("Validation A profile needs sdk_facade_members and fixture_probes")
    normalized_members: dict[str, tuple[str, ...]] = {}
    for key, value in members.items():
        if not isinstance(key, str) or not isinstance(value, (list, tuple)):
            raise ContractError("Validation A profile facade members are invalid")
        normalized_members[key] = tuple(value)
    return ValidationAProfile(
        sdk_facade_members=normalized_members,
        fixture_probes=copy.deepcopy(dict(probes)),
        profile_id=profile.get("profile_id", "experimental-python-a-v1"),
    )


def _resolve_harness_config(
    supplied: Mapping[str, Any] | None,
    library_views: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if supplied is not None:
        return copy.deepcopy(dict(supplied))
    matches = [
        value
        for value in library_views
        if value.get("artifact_type") in {"validation_harness_config", "harness_config"}
    ]
    if len(matches) != 1:
        raise ContractError(
            "the first G2 Demo requires one frozen Validation Harness configuration input"
        )
    value = matches[0]
    config = value.get("config")
    return copy.deepcopy(dict(config if isinstance(config, Mapping) else value))


def _video_profile_from_inputs(
    observation_profile: Mapping[str, Any],
    harness_config: Mapping[str, Any],
) -> FrozenVideoProfile:
    raw = harness_config.get("video_profile") or observation_profile.get("video_profile")
    if raw is None:
        return _DEFAULT_VIDEO_PROFILE
    if not isinstance(raw, Mapping):
        raise ContractError("video_profile input must be an object")
    resolution = raw.get("resolution")
    if not isinstance(resolution, Mapping):
        raise ContractError("video_profile resolution must be an object")
    return FrozenVideoProfile(
        profile_id=raw.get("profile_id"),
        profile_version=raw.get("profile_version"),
        camera=raw.get("camera"),
        view=raw.get("view"),
        fps=raw.get("fps"),
        width=resolution.get("width"),
        height=resolution.get("height"),
        container=raw.get("container"),
        codec=raw.get("codec"),
    )


def _public_state_schema(
    observation_profile: Mapping[str, Any], tasks: Sequence[DemoTask]
) -> dict[str, Any]:
    supplied = observation_profile.get("public_state_schema")
    if isinstance(supplied, Mapping):
        return copy.deepcopy(dict(supplied))
    schemas = [_schema_for_value(dict(task.public_state)) for task in tasks]
    if not schemas or any(schema != schemas[0] for schema in schemas[1:]):
        raise ContractError(
            "observation profile must provide one public_state_schema for heterogeneous task state"
        )
    return schemas[0]


def _schema_for_value(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        properties = {str(key): _schema_for_value(item) for key, item in value.items()}
        return {
            "type": "object",
            "properties": properties,
            "required": sorted(properties),
            "additionalProperties": False,
        }
    if isinstance(value, list):
        if not value:
            return {"type": "array"}
        item_schemas = [_schema_for_value(item) for item in value]
        if any(item != item_schemas[0] for item in item_schemas[1:]):
            raise ContractError("public structured state arrays must have one item shape")
        return {"type": "array", "items": item_schemas[0]}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    raise ContractError("public structured state contains an unsupported JSON value")


def _budgets(
    budget: Mapping[str, Any],
) -> tuple[Stage1Config, Stage2Config, RepairConfig, dict[str, Any]]:
    values = budget.get("budgets") if isinstance(budget.get("budgets"), Mapping) else budget

    def integer(names: Sequence[str], default: int) -> int:
        for name in names:
            if name in values:
                value = values[name]
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ContractError(f"budget {name} must be an integer")
                return value
        return default

    stage1_values = values.get("stage1") if isinstance(values.get("stage1"), Mapping) else values
    stage2_values = values.get("stage2") if isinstance(values.get("stage2"), Mapping) else values
    repair_values = values.get("repair") if isinstance(values.get("repair"), Mapping) else values
    consumer_values = values.get("consumer") if isinstance(values.get("consumer"), Mapping) else values
    demo_values = values.get("demo") if isinstance(values.get("demo"), Mapping) else values

    def nested(mapping: Mapping[str, Any], names: Sequence[str], default: int) -> int:
        for name in names:
            if name in mapping:
                value = mapping[name]
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ContractError(f"budget {name} must be an integer")
                return value
        return default

    return (
        Stage1Config(nested(stage1_values, ("max_correction_calls", "correction_calls"), 2)),
        Stage2Config(nested(stage2_values, ("max_llm_calls", "llm_calls"), 30)),
        RepairConfig(
            max_repairs=nested(repair_values, ("max_repairs",), 10),
            max_infrastructure_retries=nested(
                repair_values, ("max_infrastructure_retries",), 1
            ),
        ),
        {
            "consumer_id": _text_value(consumer_values.get("consumer_id"), "react-consumer-experimental"),
            "consumer_max_steps": nested(consumer_values, ("max_steps", "consumer_max_steps"), 4),
            "consumer_seed": nested(consumer_values, ("seed", "consumer_seed"), 0),
            "demo_repetitions": nested(demo_values, ("repetitions", "demo_repetitions"), 1),
        },
    )


def _model_client(model_prompt_config: Mapping[str, Any]) -> ModelApiClient:
    if "api_key" in model_prompt_config or "key" in model_prompt_config:
        raise ContractError("model prompt/config input must not contain an API key")
    environment = ModelApiConfig.from_environment()
    values = model_prompt_config.get("model")
    model_values = values if isinstance(values, Mapping) else model_prompt_config

    def value(name: str, fallback: Any) -> Any:
        return model_values.get(name, fallback)

    try:
        model_config = ModelApiConfig(
            api_key=environment.api_key,
            base_url=str(value("base_url", environment.base_url)).rstrip("/"),
            model=str(value("model_id", value("model", environment.model))),
            max_tokens=int(value("max_tokens", environment.max_tokens)),
            temperature=float(value("temperature", environment.temperature)),
            timeout_s=float(value("timeout_s", environment.timeout_s)),
        )
    except (TypeError, ValueError) as exc:
        raise ContractError("model prompt/config input contains invalid API settings") from exc
    return ModelApiClient(model_config)


def _compare(actual: Any, comparator: Any, expected: Any) -> bool:
    if comparator == "==":
        return actual == expected
    if isinstance(actual, bool) or isinstance(expected, bool):
        return False
    if not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
        return False
    if comparator == "<":
        return actual < expected
    if comparator == "<=":
        return actual <= expected
    if comparator == ">":
        return actual > expected
    if comparator == ">=":
        return actual >= expected
    return False


def _video_reference_artifact(
    run_id: str,
    robot: str,
    references: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "artifact_type": "general_demo_video_references",
        "schema_version": "1.0.0",
        "run_id": run_id,
        "robot": robot,
        "videos": [copy.deepcopy(dict(item)) for item in references],
    }


def _new_run_directory(output_root: str | Path, run_id: str, robot: str) -> Path:
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for _ in range(8):
        candidate = root / f"{run_id}-{robot}-{stamp}-{secrets.token_hex(6)}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise ContractError("could not allocate a unique first G2 Demo run directory")


def _resolve_top_level(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContractError("first G2 Demo input path escapes the project root") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise ContractError(f"first G2 Demo input is not a regular file: {path}")
    return resolved


def _safe_component(value: str) -> str:
    component = _SAFE_COMPONENT.sub("_", value).strip("._")
    return component or "attempt"


def _write_new_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as exc:
        raise ContractError(f"immutable first G2 Demo artifact already exists: {path}") from exc


def _text_value(value: Any, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ContractError("budget consumer_id must be non-empty text")
    return value


__all__ = [
    "FirstG2DemoConfig",
    "FirstG2DemoResult",
    "G2_PROFILE",
    "ROBOT_CONFIGURATIONS",
    "RobotSessionFactory",
    "evaluate_demo_criterion",
    "load_factory",
    "run_first_g2_demo",
]
