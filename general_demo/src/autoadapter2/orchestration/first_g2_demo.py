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
import os
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from ..demo import DemoTask, EvaluationRobotSession, evaluate_fixed_demo_criterion
from ..evaluation import (
    ClosedEvaluationVideo,
    FrozenVideoProfile,
    verify_closed_evaluation_video,
)
from ..evaluation.ffmpeg import FFmpegVideoEncoder
from ..foundation.errors import ContractError
from ..foundation.canonical import canonical_bytes
from ..foundation.hashing import content_hash, sha256_bytes
from ..foundation.seals import create_seal, verify_seal
from ..generation import ModelApiClient, ModelApiConfig, Stage1Config, Stage1Result, Stage1Runner
from ..generation.model_api import DEFAULT_BASE_URL, DEFAULT_MODEL
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

PRODUCTION_SESSION_ADAPTER_PATHS = MappingProxyType({
    "so-arm101": "autoadapter2.integrations.so_arm101.session:create_evaluation_robot_session",
    "unitree-go2": "autoadapter2.integrations.unitree_go2.session:create_evaluation_robot_session",
})

_MODEL_PROMPT_CONFIG_FIELDS = {
    "artifact_type",
    "schema_version",
    "provider",
    "base_url",
    "endpoint_path",
    "model",
    "max_tokens",
    "temperature",
    "timeout_s",
    "credential_env",
    "roles",
}
_MODEL_PROMPT_ROLES = {"stage1", "blue_line", "stage2", "repair", "consumer"}

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
class ValidationAProfileTemplate:
    """Frozen robot-level Validation A scaffold materialized after Stage 1."""

    profile_id: str
    facade_members: tuple[str, ...]
    input_value_policy: Mapping[str, Any]


@dataclass(frozen=True)
class _Stage1Replay:
    """Replay the already sealed Stage 1 output into the generic runner."""

    result: Stage1Result

    def generate_json(
        self, stage: str, _prompt: str, _inputs: Mapping[str, Any]
    ) -> dict[str, Any]:
        if stage != "stage1" or self.result.status != "SEALED" or self.result.capability_design is None:
            raise ContractError("the sealed Stage 1 replay is unavailable")
        design = self.result.capability_design
        return {
            "capabilities": copy.deepcopy(design["capabilities"]),
            "unsupported_requirement_ids": copy.deepcopy(design["unsupported_requirement_ids"]),
            "blocking_requirement_ids": copy.deepcopy(design["blocking_requirement_ids"]),
        }


class _ModelCallCapture:
    """Record content-addressed orchestration calls without retaining prompts."""

    def __init__(self, client: ModelClient):
        self._client = client
        self.records: list[dict[str, Any]] = []

    def generate_json(
        self, stage: str, prompt: str, inputs: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        try:
            output = self._client.generate_json(stage, prompt, inputs)
        except Exception as exc:
            self.records.append({
                "operation": "generate_json",
                "stage": stage,
                "input_hash": content_hash(canonical_bytes(dict(inputs))),
                "error": type(exc).__name__,
            })
            raise
        if not isinstance(output, Mapping):
            raise ContractError(f"model client returned a non-object for {stage}")
        self.records.append({
            "operation": "generate_json",
            "stage": stage,
            "input_hash": content_hash(canonical_bytes(dict(inputs))),
            "output_hash": content_hash(canonical_bytes(dict(output))),
        })
        return output

    def repair(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            output = self._client.repair(request)
        except Exception as exc:
            self.records.append({
                "operation": "repair",
                "stage": "repair",
                "input_hash": content_hash(canonical_bytes(dict(request))),
                "error": type(exc).__name__,
            })
            raise
        if not isinstance(output, Mapping):
            raise ContractError("model client returned a non-object for repair")
        self.records.append({
            "operation": "repair",
            "stage": "repair",
            "input_hash": content_hash(canonical_bytes(dict(request))),
            "output_hash": content_hash(canonical_bytes(dict(output))),
        })
        return output

    def react(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            output = self._client.react(request)
        except Exception as exc:
            self.records.append({
                "operation": "react",
                "stage": "react_consumer",
                "input_hash": content_hash(canonical_bytes(dict(request))),
                "error": type(exc).__name__,
            })
            raise
        if not isinstance(output, Mapping):
            raise ContractError("model client returned a non-object for react_consumer")
        self.records.append({
            "operation": "react",
            "stage": "react_consumer",
            "input_hash": content_hash(canonical_bytes(dict(request))),
            "output_hash": content_hash(canonical_bytes(dict(output))),
        })
        return output


@dataclass(frozen=True)
class FirstG2DemoConfig:
    """Inputs for exactly one robot-scoped first-Demo run."""

    root: str | Path
    robot: str
    integration_manifest_path: str | Path
    run_snapshot_path: str | Path
    readiness_report_path: str | Path
    robot_session_factory: RobotSessionFactory | None = None
    output_root: str | Path | None = None
    blue_line_input_paths: tuple[str | Path, ...] | None = None
    validation_a_profile: ValidationAProfile | ValidationAProfileTemplate | Mapping[str, Any] | None = None
    validation_harness_config: Mapping[str, Any] | None = None
    video_profile: FrozenVideoProfile | None = None
    model_client: ModelClient | None = None
    test_only_allow_fixture_session: bool = False

    def __post_init__(self) -> None:
        if self.robot not in ROBOT_CONFIGURATIONS:
            raise ContractError(
                f"first G2 Demo robot must be one of {sorted(ROBOT_CONFIGURATIONS)}"
            )
        if self.test_only_allow_fixture_session:
            if not callable(self.robot_session_factory):
                raise ContractError(
                    "TEST_FIXTURE_ONLY first G2 Demo requires an injected test factory"
                )
        elif self.robot_session_factory is not None:
            raise ContractError(
                "production first G2 Demo does not accept a caller robot-session factory"
            )
        if not self.test_only_allow_fixture_session and self.model_client is not None:
            raise ContractError(
                "production first G2 Demo does not accept a caller model client"
            )
        if not self.test_only_allow_fixture_session and any(
            value is not None
            for value in (
                self.validation_a_profile,
                self.validation_harness_config,
                self.video_profile,
            )
        ):
            raise ContractError(
                "production first G2 Demo loads profiles from frozen snapshot references"
            )
        if self.blue_line_input_paths is not None and len(self.blue_line_input_paths) != 3:
            raise ContractError(
                "first G2 Demo requires exactly three Blue Line input files"
            )
        if self.validation_a_profile is not None and not isinstance(
            self.validation_a_profile, (ValidationAProfile, ValidationAProfileTemplate, Mapping)
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
    stage_artifacts_path: Path
    model_call_log_path: Path
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
    validation_a_source = _resolve_validation_a_profile(
        config.validation_a_profile,
        input_values["library_views"],
        config.robot,
        test_only=config.test_only_allow_fixture_session,
    )
    validation_harness_config = _resolve_harness_config(
        config.validation_harness_config,
        input_values["library_views"],
        test_only=config.test_only_allow_fixture_session,
    )
    video_profile = config.video_profile or _video_profile_from_inputs(
        input_values["observation_profile"],
        validation_harness_config,
        allow_default=config.test_only_allow_fixture_session,
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
    session = _create_robot_session(
        config,
        config.robot,
        manifest_path,
        run_directory,
    )
    if not isinstance(session, EvaluationRobotSession):
        raise ContractError("robot-session factory did not return an EvaluationRobotSession")
    if (
        session.evidence_scope != "TEST_FIXTURE_ONLY"
        and config.test_only_allow_fixture_session
    ):
        raise ContractError(
            "test-only first G2 Demo requires the session to report TEST_FIXTURE_ONLY"
        )
    if (
        session.evidence_scope != "SDK_GROUNDED_SIMULATION"
        and not config.test_only_allow_fixture_session
    ):
        raise ContractError(
            "production first G2 Demo requires SDK_GROUNDED_SIMULATION physical evidence"
        )

    model_capture = _ModelCallCapture(model_client)
    stage1_preflight: Stage1Result | None = None
    if isinstance(validation_a_source, ValidationAProfileTemplate):
        stage1_preflight = Stage1Runner(model_capture, stage1_config).run(
            run_id,
            input_values["observation_profile"],
            [task.stage1_view() for task in tasks],
            input_values["g2_profile"],
        )
        if (
            stage1_preflight.status != "SEALED"
            or stage1_preflight.capability_design is None
            or stage1_preflight.seal is None
            or stage1_preflight.design_hash is None
        ):
            raise ContractError("the model did not produce a sealed Stage 1 Capability Design")
        if not _design_covers_tasks(stage1_preflight.capability_design, tasks):
            raise ContractError("the sealed Stage 1 Capability Design does not cover the fixed-five tasks")
        validation_a_profile = materialize_validation_a_profile(
            validation_a_source,
            stage1_preflight.capability_design,
            design_seal=stage1_preflight.seal,
        )
        stage1_model: ModelClient = _Stage1Replay(stage1_preflight)
    else:
        validation_a_profile = validation_a_source
        stage1_model = model_capture
    if not isinstance(validation_a_profile, ValidationAProfile):
        raise ContractError("first G2 Demo did not resolve a Validation A profile")
    validation_harness_config = copy.deepcopy(dict(validation_harness_config))
    validation_harness_config["validation_a_profile_hash"] = validation_a_profile.profile_hash

    video_store = _VideoStore(run_directory, run_id, video_profile)
    models = DemoModelAdapters(
        stage1=stage1_model,
        blue_line=model_capture,
        stage2=model_capture,
        repair=model_capture.repair,
        consumer=model_capture.react,
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
        evaluate_fixed_demo_criterion,
    ).run(plan)
    result = _bind_validation_a_profile(result, validation_a_profile.profile_hash)

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
    stage_artifacts_path = run_directory / "stage_artifacts.json"
    model_call_log_path = run_directory / "model_call_log.json"
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
    stage_artifacts = {
        "artifact_type": "first_g2_stage_artifacts",
        "schema_version": "1.0.0",
        "run_id": run_id,
        "robot": config.robot,
        "validation_a_profile_hash": validation_a_profile.profile_hash,
        "summary": copy.deepcopy(result.summary),
        "summary_hash": result.summary_hash,
        "summary_seal": copy.deepcopy(result.summary_seal),
        "stage1_preflight": _stage1_artifact(stage1_preflight),
        "orchestration_call_log": copy.deepcopy(model_capture.records),
    }
    write_stable_json(stage_artifacts_path, stage_artifacts)
    provider_calls = getattr(model_client, "calls", [])
    if not isinstance(provider_calls, list):
        provider_calls = []
    write_stable_json(
        model_call_log_path,
        {
            "artifact_type": "first_g2_model_call_log",
            "schema_version": "1.0.0",
            "run_id": run_id,
            "robot": config.robot,
            "calls": copy.deepcopy(provider_calls),
            "orchestration_calls": copy.deepcopy(model_capture.records),
        },
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
        stage_artifacts_path=stage_artifacts_path,
        model_call_log_path=model_call_log_path,
        summary_hash=result.summary_hash,
        runner_result=result,
    )


def _bind_validation_a_profile(
    result: DemoRunResult, profile_hash: str
) -> DemoRunResult:
    """Bind the post-Stage-1 profile into the sealed run context."""

    summary = copy.deepcopy(result.summary)
    summary["validation_a_profile_hash"] = profile_hash
    gate_bindings = summary.get("gate_bindings")
    if isinstance(gate_bindings, Mapping):
        gate_bindings = dict(gate_bindings)
        gate_bindings["validation_a_profile_hash"] = profile_hash
        summary["gate_bindings"] = gate_bindings
    summary_hash = content_hash(canonical_bytes(summary))
    old_seal = result.summary_seal
    parents = old_seal.get("parents", []) if isinstance(old_seal, Mapping) else []
    if not isinstance(parents, list):
        raise ContractError("General Demo returned an invalid summary seal parent list")
    summary_seal = create_seal(
        "general_demo_run_summary",
        summary_hash,
        [*parents, profile_hash],
    )
    return replace(
        result,
        summary=summary,
        summary_hash=summary_hash,
        summary_seal=summary_seal,
    )


def _load_production_session_factory(robot: str) -> RobotSessionFactory:
    specification = PRODUCTION_SESSION_ADAPTER_PATHS[robot]
    module_name, attribute_name = specification.split(":", 1)
    try:
        factory = getattr(importlib.import_module(module_name), attribute_name)
    except (ImportError, AttributeError) as exc:
        raise ContractError(
            f"Framework-owned {robot} session adapter is unavailable at {specification}"
        ) from exc
    if not callable(factory):
        raise ContractError(
            f"Framework-owned {robot} session adapter is not callable: {specification}"
        )
    return factory


def _create_robot_session(
    config: FirstG2DemoConfig,
    robot: str,
    manifest_path: Path,
    run_directory: Path,
) -> EvaluationRobotSession:
    factory = (
        config.robot_session_factory
        if config.test_only_allow_fixture_session
        else _load_production_session_factory(robot)
    )
    if not callable(factory):
        raise ContractError("first G2 Demo session factory is unavailable")
    try:
        session = factory(robot, manifest_path, run_directory)
    except ContractError:
        raise
    except Exception as exc:
        source = "test-only session factory" if config.test_only_allow_fixture_session else (
            f"Framework-owned {robot} session adapter"
        )
        raise ContractError(f"{source} could not create the selected session") from exc
    if not isinstance(session, EvaluationRobotSession):
        raise ContractError("robot-session factory did not return an EvaluationRobotSession")
    expected = ROBOT_CONFIGURATIONS[robot]
    if (
        session.robot_model_id != expected["robot_model_id"]
        or session.robot_configuration_id != expected["robot_configuration_id"]
    ):
        raise ContractError("robot session does not match the selected Integration Manifest")
    return session


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


def _design_covers_tasks(
    design: Mapping[str, Any], tasks: Sequence[DemoTask]
) -> bool:
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list):
        return False
    covered: set[str] = set()
    for capability in capabilities:
        if not isinstance(capability, Mapping):
            return False
        requirement_ids = capability.get("requirement_ids")
        if not isinstance(requirement_ids, list) or any(
            not isinstance(item, str) for item in requirement_ids
        ):
            return False
        covered.update(requirement_ids)
    return (
        covered == {task.requirement_id for task in tasks}
        and design.get("unsupported_requirement_ids") == []
        and design.get("blocking_requirement_ids") == []
    )


def _stage1_artifact(result: Stage1Result | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "status": result.status,
        "capability_design": copy.deepcopy(result.capability_design),
        "design_hash": result.design_hash,
        "seal": copy.deepcopy(result.seal),
        "call_log": copy.deepcopy(list(result.call_log)),
        "diagnostics": copy.deepcopy(list(result.diagnostics)),
    }


def _template_from_mapping(
    raw: Mapping[str, Any], robot: str | None
) -> ValidationAProfileTemplate:
    profile = raw.get("template") if isinstance(raw.get("template"), Mapping) else raw
    if not isinstance(profile, Mapping):
        raise ContractError("Validation A template must be an object")
    if profile.get("artifact_type") != "validation_a_template" or profile.get("schema_version") != "1.0.0":
        raise ContractError("Validation A template identity is invalid")
    if robot is not None:
        for key, expected in (
            ("robot_model_id", ROBOT_CONFIGURATIONS[robot]["robot_model_id"]),
            ("robot_configuration_id", ROBOT_CONFIGURATIONS[robot]["robot_configuration_id"]),
        ):
            if key in profile and profile[key] != expected:
                raise ContractError("Validation A template does not match the selected robot")
    profile_id = profile.get("profile_id")
    facade = profile.get("facade")
    probe = profile.get("probe")
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise ContractError("Validation A template profile_id must be non-empty")
    if not isinstance(facade, Mapping) or not isinstance(probe, Mapping):
        raise ContractError("Validation A template facade and probe are required")
    members = facade.get("members")
    policy = probe.get("input_value_policy")
    if (
        not isinstance(members, list)
        or not members
        or any(not isinstance(item, str) or not item.strip() for item in members)
        or len(set(members)) != len(members)
    ):
        raise ContractError("Validation A template facade members are invalid")
    if not isinstance(policy, Mapping) or set(policy) != {
        "number", "integer", "boolean", "string", "object", "array"
    }:
        raise ContractError("Validation A template input value policy is incomplete")
    if probe.get("metadata_policy") != "copy sealed design field metadata exactly":
        raise ContractError("Validation A template metadata policy is invalid")
    return ValidationAProfileTemplate(
        profile_id=profile_id,
        facade_members=tuple(members),
        input_value_policy=copy.deepcopy(dict(policy)),
    )


def _probe_value(field: Mapping[str, Any], policy: Mapping[str, Any]) -> Any:
    field_type = field.get("type")
    shape = field.get("shape")
    if field_type == "number" and isinstance(shape, str) and shape.startswith("vector:"):
        length_text = shape.removeprefix("vector:")
        if not length_text.isdigit() or int(length_text) <= 0:
            raise ContractError("sealed Stage 1 vector shape is invalid")
        return [copy.deepcopy(policy["number"])] * int(length_text)
    if field_type in {"number", "integer", "boolean", "string", "object", "array"} and shape == "scalar":
        return copy.deepcopy(policy[field_type])
    if field_type == "object" and shape == "mapping":
        return copy.deepcopy(policy["object"])
    if field_type == "array" and shape == "vector":
        return copy.deepcopy(policy["array"])
    raise ContractError(
        f"sealed Stage 1 field cannot be materialized by Validation A: {field}"
    )


def materialize_validation_a_profile(
    template: ValidationAProfileTemplate | Mapping[str, Any],
    capability_design: Mapping[str, Any],
    *,
    design_seal: Mapping[str, Any] | None = None,
) -> ValidationAProfile:
    """Materialize exact capability-keyed probes from a sealed Stage 1 design."""

    resolved_template = (
        template
        if isinstance(template, ValidationAProfileTemplate)
        else _template_from_mapping(template, None)
    )
    if not isinstance(capability_design, Mapping):
        raise ContractError("sealed Capability Design must be an object")
    if design_seal is not None:
        design_hash = content_hash(canonical_bytes(dict(capability_design)))
        if (
            not isinstance(design_seal, Mapping)
            or not verify_seal(dict(design_seal))
            or design_seal.get("artifact_type") != "capability_design"
            or design_seal.get("artifact_hash") != design_hash
        ):
            raise ContractError("Validation A materialization requires the exact sealed Capability Design")
    capabilities = capability_design.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ContractError("sealed Capability Design must contain capabilities")
    members: dict[str, tuple[str, ...]] = {}
    probes: dict[str, dict[str, Any]] = {}
    for capability in capabilities:
        if not isinstance(capability, Mapping):
            raise ContractError("sealed Capability Design has an invalid capability")
        capability_id = capability.get("capability_id")
        fields = capability.get("inputs")
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise ContractError("sealed Capability Design has an invalid capability identity")
        if capability_id in members:
            raise ContractError(f"sealed Capability Design duplicates capability {capability_id}")
        if not isinstance(fields, list):
            raise ContractError(f"sealed capability {capability_id} has no input fields")
        inputs: dict[str, Any] = {}
        for field in fields:
            if not isinstance(field, Mapping):
                raise ContractError(f"sealed capability {capability_id} has an invalid input descriptor")
            name = field.get("name")
            if not isinstance(name, str) or not name.strip() or name in inputs:
                raise ContractError(f"sealed capability {capability_id} has duplicate or invalid input names")
            if not all(key in field for key in ("type", "shape", "unit", "frame")):
                raise ContractError(f"sealed capability {capability_id} input descriptor is incomplete")
            inputs[name] = {
                "value": _probe_value(field, resolved_template.input_value_policy),
                "type": field["type"],
                "shape": field["shape"],
                "unit": field["unit"],
                "frame": field["frame"],
            }
        members[capability_id] = resolved_template.facade_members
        probes[capability_id] = {"inputs": inputs}
    return ValidationAProfile(
        sdk_facade_members=members,
        fixture_probes=probes,
        profile_id=resolved_template.profile_id,
    )


def _resolve_validation_a_profile(
    supplied: ValidationAProfile | Mapping[str, Any] | None,
    library_views: Sequence[Mapping[str, Any]],
    robot: str,
    *,
    test_only: bool,
) -> ValidationAProfile | ValidationAProfileTemplate:
    raw: Any = supplied
    if raw is None:
        matches = [value for value in library_views if value.get("artifact_type") == "validation_a_template"]
        if test_only:
            matches.extend(
                value
                for value in library_views
                if value.get("artifact_type") in {"validation_a_profile", "validation_a_config"}
                or {"sdk_facade_members", "fixture_probes"}.issubset(value)
            )
        if len(matches) != 1:
            raise ContractError(
                "the first G2 Demo requires one frozen Validation A template from snapshot/library refs"
            )
        raw = matches[0]
    if isinstance(raw, Mapping) and raw.get("artifact_type") == "validation_a_template":
        return _template_from_mapping(raw, robot)
    if isinstance(raw, ValidationAProfileTemplate):
        if not test_only:
            raise ContractError("production Validation A accepts only a snapshot template")
        return raw
    if isinstance(raw, ValidationAProfile):
        if not test_only:
            raise ContractError("production Validation A accepts only a snapshot template")
        return raw
    if not isinstance(raw, Mapping):
        raise ContractError("Validation A profile input must be an object")
    if not test_only:
        raise ContractError("production Validation A accepts only a snapshot template")
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
    *,
    test_only: bool,
) -> dict[str, Any]:
    if supplied is not None:
        if not test_only:
            raise ContractError("production Validation Harness accepts only snapshot/library refs")
        return copy.deepcopy(dict(supplied))
    matches = [
        value
        for value in library_views
        if value.get("artifact_type") == "validation_harness_config"
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
    *,
    allow_default: bool,
) -> FrozenVideoProfile:
    raw = harness_config.get("video_profile") or observation_profile.get("video_profile")
    if raw is None:
        if allow_default:
            return _DEFAULT_VIDEO_PROFILE
        raise ContractError("production first G2 Demo requires a frozen video profile reference")
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
    if not isinstance(model_prompt_config, Mapping) or not model_prompt_config:
        raise ContractError("model_prompt_config must be a non-empty closed object")
    if set(model_prompt_config) != _MODEL_PROMPT_CONFIG_FIELDS:
        raise ContractError("model_prompt_config must be a closed frozen object")
    if (
        model_prompt_config.get("artifact_type") != "model_prompt_config"
        or model_prompt_config.get("schema_version") != "1.0.0"
        or model_prompt_config.get("provider") != "anthropic-compatible"
        or model_prompt_config.get("base_url") != DEFAULT_BASE_URL
        or model_prompt_config.get("endpoint_path") != "/v1/chat/completions"
        or model_prompt_config.get("model") != DEFAULT_MODEL
        or model_prompt_config.get("max_tokens") != 4096
        or model_prompt_config.get("temperature") != 0
        or model_prompt_config.get("timeout_s") != 125
        or model_prompt_config.get("credential_env") != "AUTOADAPTER_MODEL_API_KEY"
    ):
        raise ContractError("model_prompt_config does not match the frozen first G2 model")
    roles = model_prompt_config.get("roles")
    if (
        not isinstance(roles, Mapping)
        or set(roles) != _MODEL_PROMPT_ROLES
        or any(not isinstance(value, str) or not value.strip() for value in roles.values())
    ):
        raise ContractError("model_prompt_config roles are not closed and non-empty")
    api_key = os.environ.get("AUTOADAPTER_MODEL_API_KEY", "").strip()
    if not api_key:
        raise ContractError("AUTOADAPTER_MODEL_API_KEY is required")
    return ModelApiClient(
        ModelApiConfig(
            api_key=api_key,
            base_url=model_prompt_config["base_url"].rstrip("/"),
            model=model_prompt_config["model"],
            max_tokens=model_prompt_config["max_tokens"],
            temperature=model_prompt_config["temperature"],
            timeout_s=model_prompt_config["timeout_s"],
        )
    )


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
    "PRODUCTION_SESSION_ADAPTER_PATHS",
    "ROBOT_CONFIGURATIONS",
    "RobotSessionFactory",
    "ValidationAProfileTemplate",
    "materialize_validation_a_profile",
    "run_first_g2_demo",
]
