"""Thin Validation/Demo Harness adapters over the shared video recorder."""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..consumer import Consumer, ConsumerResult
from ..evaluation import (
    ClosedEvaluationVideo,
    EvaluationVideoRecorder,
    FrozenVideoProfile,
    RGBFrame,
    VideoEncoder,
)
from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash, is_content_hash
from ..validation import (
    HarnessInfrastructureError,
    HarnessInvocation,
    HarnessMeasurement,
    MeasurementSample,
    TypedHarnessSession,
    ValidatedCandidateHandle,
)


CriterionEvaluator = Callable[[Mapping[str, Any], Mapping[str, Any]], bool]
VideoEncoderFactory = Callable[[str, str], VideoEncoder]
_EVIDENCE_SCOPES = {"TEST_FIXTURE_ONLY", "SDK_GROUNDED_SIMULATION"}


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be non-empty text")
    return value.strip()


def _hash(value: Any, label: str) -> str:
    if not is_content_hash(value):
        raise ContractError(f"{label} must be a content hash")
    return value


@dataclass(frozen=True)
class EvaluationRoute:
    """Exact gate-derived SDK-grounded simulation lineage for one run."""

    run_id: str
    integration_manifest_hash: str
    sdk_entry_hash: str
    runtime_hash: str
    simulation_profile_hash: str

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id")
        for field in (
            "integration_manifest_hash",
            "sdk_entry_hash",
            "runtime_hash",
            "simulation_profile_hash",
        ):
            _hash(getattr(self, field), field)


@dataclass(frozen=True)
class ValidationEvidence:
    """Trusted structured measurements collected after one candidate call."""

    samples: tuple[MeasurementSample, ...]
    elapsed_s: float
    guard_results: Mapping[str, bool]
    sdk_route_verified: bool

    def __post_init__(self) -> None:
        if not isinstance(self.samples, tuple) or not all(
            isinstance(item, MeasurementSample) for item in self.samples
        ):
            raise ContractError("validation samples must be MeasurementSample values")
        if (
            not isinstance(self.elapsed_s, (int, float))
            or isinstance(self.elapsed_s, bool)
            or not math.isfinite(self.elapsed_s)
            or self.elapsed_s < 0
        ):
            raise ContractError("validation elapsed_s must be finite and non-negative")
        if not isinstance(self.guard_results, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, bool)
            for key, value in self.guard_results.items()
        ):
            raise ContractError("validation guard_results must be a boolean mapping")
        if not isinstance(self.sdk_route_verified, bool):
            raise ContractError("sdk_route_verified must be boolean")


@runtime_checkable
class EvaluationRobotSession(Protocol):
    """Narrow injected owner of one robot-specific SDK/simulation session."""

    @property
    def sdk(self) -> object: ...

    @property
    def simulation_time_s(self) -> float: ...

    @property
    def evidence_scope(self) -> str: ...

    @property
    def robot_model_id(self) -> str: ...

    @property
    def robot_configuration_id(self) -> str: ...

    def reset(
        self,
        *,
        phase: str,
        execution_id: str,
        initial_state: Mapping[str, Any],
    ) -> None: ...

    def start_external_recording(self, *, phase: str, execution_id: str) -> None: ...

    def stop_external_recording(self) -> tuple[RGBFrame, ...]: ...

    def validation_evidence(self, invocation: HarnessInvocation) -> ValidationEvidence: ...

    def demo_evidence(self, task_id: str) -> Mapping[str, Any]: ...

    def invoke(
        self,
        candidate: ValidatedCandidateHandle,
        capability_id: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


def _session_time(session: EvaluationRobotSession) -> float:
    value = session.simulation_time_s
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise HarnessInfrastructureError("robot session simulation time is invalid")
    return float(value)


def _recording_bindings(
    route: EvaluationRoute,
    phase: str,
    execution_id: str,
    lineage: Mapping[str, Any],
) -> dict[str, str]:
    result = {
        "run_id": route.run_id,
        "phase": phase,
        "execution_id": execution_id,
        "integration_manifest_hash": route.integration_manifest_hash,
        "sdk_entry_hash": route.sdk_entry_hash,
        "runtime_hash": route.runtime_hash,
        "simulation_profile_hash": route.simulation_profile_hash,
    }
    for key, value in lineage.items():
        if value is not None:
            result[key] = str(value)
    return result


def _close_recording(
    session: EvaluationRobotSession,
    recorder: EvaluationVideoRecorder,
) -> ClosedEvaluationVideo:
    try:
        frames = session.stop_external_recording()
        if not isinstance(frames, tuple) or not all(isinstance(item, RGBFrame) for item in frames):
            raise ContractError("robot session returned invalid RGB recording frames")
        for frame in frames:
            recorder.add_frame(frame)
        closed = recorder.close(terminal_time_s=_session_time(session))
    except Exception as exc:
        if isinstance(exc, HarnessInfrastructureError):
            raise
        raise HarnessInfrastructureError("evaluation video recording could not close") from exc
    if not isinstance(closed, ClosedEvaluationVideo):
        raise HarnessInfrastructureError("evaluation video recorder returned an invalid result")
    return closed


class _ValidationSession:
    def __init__(
        self,
        owner: "RecordingValidationHarness",
        invocation: HarnessInvocation,
        recorder: EvaluationVideoRecorder,
    ) -> None:
        self._owner = owner
        self._invocation = invocation
        self._recorder = recorder
        self._collected = False

    @property
    def sdk(self) -> object:
        return self._owner._robot_session.sdk

    def collect(self) -> HarnessMeasurement:
        if self._collected:
            raise HarnessInfrastructureError("validation session was collected twice")
        self._collected = True
        return self._owner._collect(self._invocation, self._recorder)


class RecordingValidationHarness:
    """Typed Validation-B Harness backed by EvaluationVideoRecorder."""

    def __init__(
        self,
        robot_session: EvaluationRobotSession,
        video_profile: FrozenVideoProfile,
        encoder_factory: VideoEncoderFactory,
        route: EvaluationRoute,
        config: Mapping[str, Any],
    ) -> None:
        if not isinstance(robot_session, EvaluationRobotSession):
            raise ContractError("Validation Harness requires an EvaluationRobotSession")
        if not isinstance(video_profile, FrozenVideoProfile):
            raise ContractError("Validation Harness requires a FrozenVideoProfile")
        if not callable(encoder_factory):
            raise ContractError("Validation Harness requires a VideoEncoder factory")
        if robot_session.evidence_scope not in _EVIDENCE_SCOPES:
            raise ContractError("robot session evidence_scope is invalid")
        self._robot_session = robot_session
        self._video_profile = video_profile
        self._encoder_factory = encoder_factory
        self._route = route
        self._config = copy.deepcopy(dict(config))
        self.config_hash = content_hash(canonical_bytes({
            "harness": "recording-validation-harness-v1",
            "config": self._config,
            "video_profile_hash": video_profile.content_hash,
        }))
        self._videos: list[ClosedEvaluationVideo] = []

    @property
    def video_handles(self) -> tuple[ClosedEvaluationVideo, ...]:
        return tuple(self._videos)

    def open(self, invocation: HarnessInvocation) -> TypedHarnessSession:
        if not isinstance(invocation, HarnessInvocation):
            raise ContractError("Validation Harness requires a typed invocation")
        identity = {
            "capability_id": invocation.capability_id,
            "case_id": invocation.case_id,
            "inputs": invocation.inputs,
            "initial_state": invocation.initial_state,
            "repetition": invocation.repetition,
            "run_snapshot_hash": invocation.run_snapshot_hash,
            "candidate_source_hash": invocation.candidate_source_hash,
            "suite_hash": invocation.suite_hash,
            "execution_attempt": invocation.execution_attempt,
        }
        execution_id = content_hash(canonical_bytes(identity)).split(":", 1)[1][:24]
        try:
            self._robot_session.reset(
                phase="VALIDATION_B",
                execution_id=execution_id,
                initial_state=copy.deepcopy(dict(invocation.initial_state)),
            )
            start = _session_time(self._robot_session)
            recorder = EvaluationVideoRecorder(
                recording_id=f"validation-b-{self._route.run_id}-{execution_id}",
                profile=self._video_profile,
                encoder=self._encoder_factory("VALIDATION_B", execution_id),
                coverage_start_time_s=start,
                bindings=_recording_bindings(
                    self._route,
                    "VALIDATION_B",
                    execution_id,
                    {
                        "capability_id": invocation.capability_id,
                        "case_id": invocation.case_id,
                        "repetition": invocation.repetition,
                        "run_snapshot_hash": invocation.run_snapshot_hash,
                        "candidate_source_hash": invocation.candidate_source_hash,
                        "suite_hash": invocation.suite_hash,
                        "execution_attempt": invocation.execution_attempt,
                    },
                ),
            )
            self._robot_session.start_external_recording(
                phase="VALIDATION_B", execution_id=execution_id
            )
        except Exception as exc:
            raise HarnessInfrastructureError("Validation Harness could not start execution") from exc
        return _ValidationSession(self, invocation, recorder)

    def _collect(
        self,
        invocation: HarnessInvocation,
        recorder: EvaluationVideoRecorder,
    ) -> HarnessMeasurement:
        evidence: ValidationEvidence | None = None
        evidence_error: Exception | None = None
        try:
            evidence = self._robot_session.validation_evidence(invocation)
            if not isinstance(evidence, ValidationEvidence):
                raise ContractError("robot session returned invalid Validation evidence")
        except Exception as exc:
            evidence_error = exc
        closed = _close_recording(self._robot_session, recorder)
        self._videos.append(closed)
        if closed.is_infrastructure_error:
            raise HarnessInfrastructureError(
                "required Validation video is incomplete", video_evidence=closed
            )
        if evidence_error is not None or evidence is None:
            raise HarnessInfrastructureError("Validation evidence acquisition failed") from evidence_error
        return HarnessMeasurement(
            measurement_id=invocation.measurement["measurement_id"],
            metric=invocation.metric,
            entity=invocation.measurement["entity"],
            unit=invocation.measurement["unit"],
            frame=invocation.measurement["frame"],
            samples=evidence.samples,
            elapsed_s=evidence.elapsed_s,
            guard_results=copy.deepcopy(dict(evidence.guard_results)),
            sdk_route_evidence={
                "verified": evidence.sdk_route_verified,
                "rim_hash": self._route.integration_manifest_hash,
                "sdk_entry_hash": self._route.sdk_entry_hash,
                "runtime_hash": self._route.runtime_hash,
            },
            video_evidence=closed,
        )


@dataclass(frozen=True)
class DemoTask:
    """One task with public Consumer fields and a Harness-private criterion."""

    requirement_id: str
    task_id: str
    description: str
    public_state: Mapping[str, Any]
    private_criterion: Mapping[str, Any]

    def __post_init__(self) -> None:
        _text(self.requirement_id, "requirement_id")
        _text(self.task_id, "task_id")
        _text(self.description, "description")
        if not isinstance(self.public_state, Mapping):
            raise ContractError("Demo task public_state must be an object")
        if not isinstance(self.private_criterion, Mapping) or not self.private_criterion:
            raise ContractError("Demo task requires one private criterion")
        object.__setattr__(self, "public_state", copy.deepcopy(dict(self.public_state)))
        object.__setattr__(self, "private_criterion", copy.deepcopy(dict(self.private_criterion)))

    def stage1_view(self) -> dict[str, str]:
        return {"requirement_id": self.requirement_id, "description": self.description}

    def frozen_record(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "task_id": self.task_id,
            "description": self.description,
            "public_state": copy.deepcopy(dict(self.public_state)),
            "private_criterion": copy.deepcopy(dict(self.private_criterion)),
        }

    def consumer_view(self, allowed_capability_ids: tuple[str, ...]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "public_state": copy.deepcopy(dict(self.public_state)),
            "allowed_capability_ids": list(allowed_capability_ids),
        }


@dataclass(frozen=True)
class DemoTrialResult:
    task_id: str
    repetition: int
    status: str
    consumer_result: ConsumerResult | None
    criterion_hash: str
    evidence_hash: str | None
    video_handle: ClosedEvaluationVideo | None


class DemoEvaluationHarness:
    """Harness-owned task verdicts with criterion-private evaluation and video."""

    def __init__(
        self,
        robot_session: EvaluationRobotSession,
        video_profile: FrozenVideoProfile,
        encoder_factory: VideoEncoderFactory,
        route: EvaluationRoute,
        criterion_evaluator: CriterionEvaluator,
        *,
        repetitions: int,
    ) -> None:
        if not isinstance(robot_session, EvaluationRobotSession):
            raise ContractError("Demo Harness requires an EvaluationRobotSession")
        if not isinstance(video_profile, FrozenVideoProfile):
            raise ContractError("Demo Harness requires a FrozenVideoProfile")
        if not callable(encoder_factory):
            raise ContractError("Demo Harness requires a VideoEncoder factory")
        if not callable(criterion_evaluator):
            raise ContractError("Demo Harness requires a private criterion evaluator")
        if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions <= 0:
            raise ContractError("Demo repetitions must be a positive fixed integer")
        self._robot_session = robot_session
        self._video_profile = video_profile
        self._encoder_factory = encoder_factory
        self._route = route
        self._criterion_evaluator = criterion_evaluator
        self._repetitions = repetitions

    def run(
        self,
        consumer: Consumer,
        tasks: Sequence[DemoTask],
        *,
        seed: int,
        consumer_id: str,
        layer_hash: str,
        visible_capability_ids: tuple[str, ...],
    ) -> tuple[DemoTrialResult, ...]:
        if len(tasks) != 5:
            raise ContractError("the General Demo must execute exactly five tasks")
        _text(consumer_id, "consumer_id")
        _hash(layer_hash, "layer_hash")
        if (
            not isinstance(visible_capability_ids, tuple)
            or not visible_capability_ids
            or any(not isinstance(item, str) or not item for item in visible_capability_ids)
            or len(set(visible_capability_ids)) != len(visible_capability_ids)
        ):
            raise ContractError("Demo requires one non-empty promoted Capability Layer")
        results: list[DemoTrialResult] = []
        for task_index, task in enumerate(tasks):
            for repetition in range(1, self._repetitions + 1):
                results.append(self._run_trial(
                    consumer,
                    task,
                    repetition=repetition,
                    seed=seed + task_index * self._repetitions + repetition - 1,
                    consumer_id=consumer_id,
                    layer_hash=layer_hash,
                    visible_capability_ids=visible_capability_ids,
                ))
        return tuple(results)

    def _run_trial(
        self,
        consumer: Consumer,
        task: DemoTask,
        *,
        repetition: int,
        seed: int,
        consumer_id: str,
        layer_hash: str,
        visible_capability_ids: tuple[str, ...],
    ) -> DemoTrialResult:
        execution_id = f"{task.task_id}-trial-1-repetition-{repetition}"
        criterion_hash = content_hash(canonical_bytes(task.private_criterion))
        try:
            self._robot_session.reset(
                phase="DEMO",
                execution_id=execution_id,
                initial_state=copy.deepcopy(dict(task.public_state)),
            )
            start = _session_time(self._robot_session)
            recorder = EvaluationVideoRecorder(
                recording_id=f"demo-{self._route.run_id}-{execution_id}",
                profile=self._video_profile,
                encoder=self._encoder_factory("DEMO", execution_id),
                coverage_start_time_s=start,
                bindings=_recording_bindings(
                    self._route,
                    "DEMO",
                    execution_id,
                    {
                        "task_id": task.task_id,
                        "trial": 1,
                        "repetition": repetition,
                        "consumer_id": consumer_id,
                        "layer_hash": layer_hash,
                    },
                ),
            )
            self._robot_session.start_external_recording(phase="DEMO", execution_id=execution_id)
        except Exception:
            return DemoTrialResult(
                task.task_id, repetition, "INFRASTRUCTURE_ERROR", None,
                criterion_hash, None, None,
            )

        consumer_result: ConsumerResult | None = None
        consumer_failed = False
        try:
            consumer_result = consumer.execute(
                task.consumer_view(visible_capability_ids), seed=seed
            )
        except Exception:
            consumer_failed = True
        try:
            closed = _close_recording(self._robot_session, recorder)
            if closed.is_infrastructure_error:
                return DemoTrialResult(
                    task.task_id, repetition, "INFRASTRUCTURE_ERROR", consumer_result,
                    criterion_hash, None, closed,
                )
            evidence = copy.deepcopy(dict(self._robot_session.demo_evidence(task.task_id)))
            evidence_hash = content_hash(canonical_bytes(evidence))
            passed = not consumer_failed and bool(
                self._criterion_evaluator(
                    copy.deepcopy(dict(task.private_criterion)),
                    copy.deepcopy(evidence),
                )
            )
        except Exception:
            return DemoTrialResult(
                task.task_id, repetition, "INFRASTRUCTURE_ERROR", consumer_result,
                criterion_hash, None, None,
            )
        return DemoTrialResult(
            task.task_id,
            repetition,
            "PASS" if passed else "FAIL",
            consumer_result,
            criterion_hash,
            evidence_hash,
            closed,
        )


__all__ = [
    "CriterionEvaluator",
    "DemoEvaluationHarness",
    "DemoTask",
    "DemoTrialResult",
    "EvaluationRobotSession",
    "EvaluationRoute",
    "RecordingValidationHarness",
    "ValidationEvidence",
    "VideoEncoderFactory",
]
