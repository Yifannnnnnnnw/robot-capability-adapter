"""Minimal experiment-grade orchestration for one complete General Demo run."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..blue_line import BlueLineRunner
from ..consumer import CapabilityToolLayer, PromotedTool, ReActConsumer
from ..consumer.react import ModelCallback
from ..demo import (
    CriterionEvaluator,
    DemoEvaluationHarness,
    DemoTask,
    DemoTrialResult,
    EvaluationRobotSession,
    EvaluationRoute,
    RecordingValidationHarness,
    VideoEncoderFactory,
)
from ..evaluation import ClosedEvaluationVideo, FrozenVideoProfile
from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash, is_content_hash
from ..foundation.seals import create_seal
from ..generation import JsonGenerator, Stage1Config, Stage1Result, Stage1Runner
from ..implementation import CallbackSandbox, Stage2Config, Stage2Runner
from ..implementation import validate_implementation_bundle
from ..integration import ExperimentIntegrationGate, Stage1GateResult
from ..integration.artifacts import load_json_artifact, load_run_snapshot, verify_file_reference
from ..libraries import TasksLibrary
from ..validation import (
    RepairCallback,
    RepairConfig,
    RepairRunner,
    ValidationAProfile,
    ValidationARunner,
    ValidationBRunner,
    ValidationContext,
)


_AUTHORITY_REVISION = "0.16.1"
_G2 = {"profile_id": "g2-reusable-effect", "version": "1.0.0", "granularity": "G2"}
_SCALAR_TYPES = {"null", "boolean", "integer", "number", "string"}
_DIRECT_MUJOCO_EXPERIMENTAL = "DIRECT_MUJOCO_EXPERIMENTAL"


@dataclass(frozen=True)
class DemoModelAdapters:
    """Stage-scoped external model boundaries plus a legacy Repair callback surface."""

    stage1: JsonGenerator
    blue_line: JsonGenerator
    stage2: JsonGenerator
    repair: RepairCallback
    consumer: ModelCallback
    sandbox: CallbackSandbox | None = None


@dataclass(frozen=True)
class DemoRunPlan:
    """Frozen inputs needed by the existing experiment-grade component APIs."""

    run_id: str
    integration_manifest_path: str | Path | None
    run_snapshot_path: str | Path | None
    readiness_report_path: str | Path | None
    robot_public_projection: Mapping[str, Any]
    g2_profile: Mapping[str, Any]
    tasks: tuple[DemoTask, ...]
    standards_snapshot: Mapping[str, Any]
    measurement_catalog: Mapping[str, Any]
    blue_line_policy: Mapping[str, Any]
    implementation_bundle: Mapping[str, Any]
    validation_a_profile: ValidationAProfile | None
    public_state_schema: Mapping[str, Any]
    validation_harness_config: Mapping[str, Any]
    consumer_id: str = "react-consumer-experimental"
    consumer_max_steps: int = 4
    consumer_seed: int = 0
    demo_repetitions: int = 1
    task_catalog_version: str = "1.0.0"
    stage1_config: Stage1Config = Stage1Config()
    stage2_config: Stage2Config = Stage2Config()
    repair_config: RepairConfig = RepairConfig()
    design_experience_snapshot: Mapping[str, Any] | None = None
    implementation_experience_snapshot: Mapping[str, Any] | None = None
    execution_route: str = "SDK_GROUNDED_SIMULATION"
    evaluation_route: EvaluationRoute | None = None
    route_metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ContractError("General Demo run_id must be non-empty text")
        if len(self.tasks) != 5 or not all(isinstance(item, DemoTask) for item in self.tasks):
            raise ContractError("the General Demo plan must contain exactly five DemoTask values")
        if len({item.task_id for item in self.tasks}) != 5:
            raise ContractError("General Demo task_id values must be unique")
        if len({item.requirement_id for item in self.tasks}) != 5:
            raise ContractError("General Demo requirement_id values must be unique")
        if any(self.g2_profile.get(key) != value for key, value in _G2.items()):
            raise ContractError("the first two-robot General Demo requires g2-reusable-effect@1.0.0")
        if self.validation_a_profile is not None and not isinstance(
            self.validation_a_profile, ValidationAProfile
        ):
            raise ContractError("General Demo requires a ValidationAProfile or post-Stage-1 materializer")
        if isinstance(self.consumer_max_steps, bool) or not isinstance(self.consumer_max_steps, int) or self.consumer_max_steps <= 0:
            raise ContractError("consumer_max_steps must be a positive fixed integer")
        if isinstance(self.consumer_seed, bool) or not isinstance(self.consumer_seed, int) or self.consumer_seed < 0:
            raise ContractError("consumer_seed must be non-negative")
        if isinstance(self.demo_repetitions, bool) or not isinstance(self.demo_repetitions, int) or self.demo_repetitions <= 0:
            raise ContractError("demo_repetitions must be a positive fixed integer")
        if not isinstance(self.task_catalog_version, str) or not self.task_catalog_version.strip():
            raise ContractError("task_catalog_version must be non-empty text")
        if self.execution_route not in {
            "SDK_GROUNDED_SIMULATION",
            _DIRECT_MUJOCO_EXPERIMENTAL,
        }:
            raise ContractError("General Demo execution_route is invalid")
        if self.evaluation_route is not None and not isinstance(
            self.evaluation_route, EvaluationRoute
        ):
            raise ContractError("evaluation_route must be an EvaluationRoute when supplied")
        for field in (
            "robot_public_projection",
            "g2_profile",
            "standards_snapshot",
            "measurement_catalog",
            "blue_line_policy",
            "implementation_bundle",
            "public_state_schema",
            "validation_harness_config",
        ):
            value = getattr(self, field)
            if not isinstance(value, Mapping):
                raise ContractError(f"{field} must be an object")
            object.__setattr__(self, field, copy.deepcopy(dict(value)))
        for field in ("design_experience_snapshot", "implementation_experience_snapshot"):
            value = getattr(self, field)
            if value is not None:
                if not isinstance(value, Mapping):
                    raise ContractError(f"{field} must be an object when supplied")
                object.__setattr__(self, field, copy.deepcopy(dict(value)))
        if self.route_metadata is not None:
            if not isinstance(self.route_metadata, Mapping):
                raise ContractError("route_metadata must be an object when supplied")
            object.__setattr__(self, "route_metadata", copy.deepcopy(dict(self.route_metadata)))


@dataclass(frozen=True)
class DemoRunResult:
    status: str
    summary: dict[str, Any]
    summary_hash: str
    summary_seal: dict[str, Any]
    validation_video_handles: tuple[ClosedEvaluationVideo, ...]
    demo_trials: tuple[DemoTrialResult, ...]
    artifacts: dict[str, Any]


def _prefixed_hash(value: str, label: str) -> str:
    if is_content_hash(value):
        return value
    if isinstance(value, str) and len(value) == 64 and all(item in "0123456789abcdef" for item in value):
        return f"sha256:{value}"
    raise ContractError(f"{label} is not a SHA-256 digest")


def _bare_hash(value: str, label: str) -> str:
    exact = _prefixed_hash(value, label)
    return exact.split(":", 1)[1]


def _field_schema(field: Mapping[str, Any]) -> dict[str, Any]:
    field_type = field.get("type")
    shape = field.get("shape")
    if shape == "scalar" and field_type == "float":
        return {"type": "number"}
    if shape == "scalar" and field_type in _SCALAR_TYPES:
        return {"type": field_type}
    if field_type == "array" and isinstance(shape, str):
        match = re.fullmatch(r"\[([0-9]+)\]", shape)
        if match is not None:
            length = int(match.group(1))
            if length > 0:
                return {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": length,
                    "maxItems": length,
                }
    if (
        isinstance(shape, str)
        and shape.startswith("vector:")
        and shape.removeprefix("vector:").isdigit()
        and int(shape.removeprefix("vector:")) > 0
        and field_type == "number"
    ):
        length = int(shape.removeprefix("vector:"))
        return {
            "type": "array",
            "items": {"type": "number"},
            "minItems": length,
            "maxItems": length,
        }
    raise ContractError(
        "experimental PromotedTool conversion supports scalar fields and exact "
        "numeric vector:<positive-length> and array [<positive-length>] fields"
    )


def _object_schema(fields: Any) -> dict[str, Any]:
    if not isinstance(fields, list):
        raise ContractError("capability public fields must be an array")
    properties: dict[str, Any] = {}
    required: list[str] = []
    for field in fields:
        if not isinstance(field, Mapping) or not isinstance(field.get("name"), str):
            raise ContractError("capability public field is invalid")
        name = field["name"]
        if name in properties:
            raise ContractError("capability public field names must be unique")
        if field.get("required") is not True:
            raise ContractError("the first General Demo requires every public field")
        properties[name] = _field_schema(field)
        if field.get("required") is True:
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _video_record(video: ClosedEvaluationVideo) -> dict[str, Any]:
    return {
        "recording_id": video.manifest["recording_id"],
        "completion_status": video.completion_status,
        "execution_disposition": video.execution_disposition,
        "media_content_hash": video.media_content_hash,
        "manifest_content_hash": video.manifest_content_hash,
    }


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


def _recorded_stage_calls(adapter: Any, stage: str) -> list[dict[str, Any]]:
    records = getattr(adapter, "records", ())
    if not isinstance(records, (list, tuple)):
        return []
    return [
        copy.deepcopy(dict(item))
        for item in records
        if isinstance(item, Mapping) and item.get("stage") == stage
    ]


def _blue_line_artifact(result: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "validation_spec": copy.deepcopy(result.validation_spec),
        "spec_hash": result.spec_hash,
        "spec_seal": copy.deepcopy(result.spec_seal),
        "validation_suite": copy.deepcopy(result.validation_suite),
        "suite_hash": result.suite_hash,
        "suite_seal": copy.deepcopy(result.suite_seal),
        "manifest": copy.deepcopy(result.manifest),
        "manifest_hash": result.manifest_hash,
        "manifest_seal": copy.deepcopy(result.manifest_seal),
        "call_log": copy.deepcopy(list(result.call_log)),
        "diagnostics": copy.deepcopy(list(result.diagnostics)),
    }


def _stage2_artifact(result: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "implementation_bundle_hash": result.bundle_hash,
        "binding_contract": copy.deepcopy(result.binding_contract),
        "binding_hash": result.binding_hash,
        "binding_seal": copy.deepcopy(result.binding_seal),
        "starter_skeleton": result.starter_skeleton,
        "starter_skeleton_hash": result.starter_skeleton_hash,
        "capability_source": result.capability_source,
        "source_hash": result.source_hash,
        "source_seal": copy.deepcopy(result.source_seal),
        "implementation_manifest": copy.deepcopy(result.implementation_manifest),
        "implementation_manifest_hash": result.manifest_hash,
        "implementation_manifest_seal": copy.deepcopy(result.manifest_seal),
        "llm_calls": result.llm_calls,
        "sandbox_calls": result.sandbox_calls,
        "successful_sandbox_calls": result.successful_sandbox_calls,
        "covered_sandbox_capability_ids": list(result.covered_sandbox_capability_ids),
        "call_log": copy.deepcopy(list(result.call_log)),
        "sandbox_log": copy.deepcopy(list(result.sandbox_log)),
        "diagnostics": copy.deepcopy(list(result.diagnostics)),
        "blocked_reason": result.blocked_reason,
    }


def _validation_a_artifact(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "status": result.status,
        "report": copy.deepcopy(result.report),
        "report_hash": result.report_hash,
        "report_seal": copy.deepcopy(result.report_seal),
        "binding_result": copy.deepcopy(result.binding_result),
        "source_hash": result.source_hash,
        "implementation_manifest_hash": result.implementation_manifest_hash,
        "implementation_bundle_hash": result.implementation_bundle_hash,
        "diagnostics": copy.deepcopy(list(result.diagnostics)),
    }


def _validation_b_artifact(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "status": result.status,
        "report": copy.deepcopy(result.report),
        "report_hash": result.report_hash,
        "report_seal": copy.deepcopy(result.report_seal),
        "suite_hash": result.suite_hash,
        "overlay_hash": result.overlay_hash,
        "run_snapshot_hash": result.run_snapshot_hash,
        "executions": copy.deepcopy(list(result.executions)),
        "diagnostics": copy.deepcopy(list(result.diagnostics)),
    }


def _repair_artifact(result: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "first_passing_repair_index": result.first_passing_repair_index,
        "repair_invocations_used": result.repair_invocations_used,
        "candidate_revisions_created": result.candidate_revisions_created,
        "repairs_consumed": result.repairs_consumed,
        "repair_llm_calls": result.repair_llm_calls,
        "repair_log": copy.deepcopy(list(result.repair_log)),
        "run_ledger": copy.deepcopy(list(result.run_ledger)),
        "run_snapshot_hash": result.run_snapshot_hash,
        "frozen_artifact_hashes": copy.deepcopy(result.frozen_artifact_hashes),
        "initial_validation_a": _validation_a_artifact(result.initial_validation_a),
        "initial_validation_b": _validation_b_artifact(result.initial_validation_b),
        "final_validation_a": _validation_a_artifact(result.final_validation_a),
        "final_validation_b": _validation_b_artifact(result.final_validation_b),
        "validation_b_attempts": copy.deepcopy(list(result.validation_b_attempts)),
    }


def _demo_trial_artifact(
    trial: DemoTrialResult,
    *,
    include_private_trace: bool = False,
) -> dict[str, Any]:
    artifact = {
        "task_id": trial.task_id,
        "repetition": trial.repetition,
        "status": trial.status,
        "consumer_status": trial.consumer_result.status if trial.consumer_result else None,
        "consumer_steps": trial.consumer_result.steps_used if trial.consumer_result else None,
        "consumer_trace_hash": (
            content_hash(canonical_bytes(trial.consumer_result.trace))
            if trial.consumer_result else None
        ),
        "criterion_hash": trial.criterion_hash,
        "evidence_hash": trial.evidence_hash,
        "video": _video_record(trial.video_handle) if trial.video_handle else None,
    }
    if include_private_trace:
        artifact["consumer_trace"] = (
            copy.deepcopy(trial.consumer_result.trace)
            if trial.consumer_result is not None
            else None
        )
    return artifact


class GeneralDemoRunner:
    """Wire the existing frozen gates into one smallest complete run path."""

    def __init__(
        self,
        root: str | Path,
        models: DemoModelAdapters,
        robot_session: EvaluationRobotSession,
        video_profile: FrozenVideoProfile,
        video_encoder_factory: VideoEncoderFactory,
        criterion_evaluator: CriterionEvaluator,
        validation_a_materializer: Callable[[Stage1Result], ValidationAProfile] | None = None,
    ) -> None:
        self._root = Path(root).resolve()
        self._models = models
        if not isinstance(robot_session, EvaluationRobotSession):
            raise ContractError("General Demo requires an EvaluationRobotSession")
        if not isinstance(video_profile, FrozenVideoProfile):
            raise ContractError("General Demo requires a FrozenVideoProfile")
        if not callable(video_encoder_factory):
            raise ContractError("General Demo requires a VideoEncoder factory")
        if not callable(criterion_evaluator):
            raise ContractError("General Demo requires a criterion evaluator")
        self._robot_session = robot_session
        self._video_profile = video_profile
        self._video_encoder_factory = video_encoder_factory
        self._criterion_evaluator = criterion_evaluator
        if validation_a_materializer is not None and not callable(validation_a_materializer):
            raise ContractError("General Demo Validation A materializer must be callable")
        self._validation_a_materializer = validation_a_materializer

    def run(self, plan: DemoRunPlan) -> DemoRunResult:
        if not isinstance(plan, DemoRunPlan):
            raise ContractError("General Demo requires a DemoRunPlan")
        gate_result: Stage1GateResult | None = None
        if plan.execution_route == _DIRECT_MUJOCO_EXPERIMENTAL:
            route = plan.evaluation_route or self._derive_direct_route(plan)
            if route.run_id != plan.run_id:
                raise ContractError("DemoRunPlan run_id does not match the direct EvaluationRoute")
            metadata = dict(plan.route_metadata or {})
            manifest = {
                "robot_model_id": metadata.get(
                    "robot_model_id", plan.robot_public_projection.get("robot_model_id")
                ),
                "robot_configuration_id": metadata.get(
                    "robot_configuration_id",
                    plan.robot_public_projection.get("robot_configuration_id"),
                ),
            }
            self._verify_robot_projection(manifest, plan.robot_public_projection)
            self._verify_robot_session(manifest)
            self._verify_tasks_library(manifest, plan)
        else:
            gate = ExperimentIntegrationGate(self._root)
            gate_result = gate.verify(
                plan.integration_manifest_path,
                plan.run_snapshot_path,
                plan.readiness_report_path,
            )
            if gate_result.run_id != plan.run_id:
                raise ContractError("DemoRunPlan run_id does not match the verified gate result")
            manifest_artifact = gate.inspect_manifest(plan.integration_manifest_path)
            if manifest_artifact.sha256 != gate_result.integration_manifest_sha256:
                raise ContractError("integration manifest changed after the pre-Stage-1 gate")
            manifest = manifest_artifact.value
            self._verify_robot_projection(manifest, plan.robot_public_projection)
            self._verify_robot_session(manifest)
            self._verify_tasks_library(manifest, plan)
            self._verify_frozen_run_inputs(plan)
            route = EvaluationRoute(
                run_id=plan.run_id,
                integration_manifest_hash=_prefixed_hash(
                    gate_result.integration_manifest_sha256, "integration manifest hash"
                ),
                sdk_entry_hash=_prefixed_hash(manifest["sdk_ref"]["sha256"], "SDK Entry hash"),
                runtime_hash=_prefixed_hash(gate_result.runtime_sha256, "runtime hash"),
                simulation_profile_hash=_prefixed_hash(
                    manifest["morphology_ref"]["sha256"], "simulation profile hash"
                ),
            )
        base, parents = self._base_summary(plan, gate_result, manifest, route)
        artifacts: dict[str, Any] = {
            "artifact_type": "general_demo_stage_artifacts",
            "schema_version": "1.0.0",
            "run_id": plan.run_id,
            "stages": {},
        }
        if plan.execution_route == _DIRECT_MUJOCO_EXPERIMENTAL:
            stages: list[dict[str, Any]] = [{
                "stage": "direct_route_resolution",
                "status": "READY",
                "execution_route": _DIRECT_MUJOCO_EXPERIMENTAL,
                "route_hash": route.integration_manifest_hash,
                "sdk_entry_hash": route.sdk_entry_hash,
                "runtime_hash": route.runtime_hash,
                "simulation_profile_hash": route.simulation_profile_hash,
            }]
        else:
            assert gate_result is not None
            stages = [{
                "stage": "integration_gate",
                "status": "READY",
                "integration_manifest_hash": route.integration_manifest_hash,
                "readiness_report_hash": _prefixed_hash(
                    gate_result.readiness_report_sha256, "readiness report hash"
                ),
            }]

        try:
            stage1 = Stage1Runner(self._models.stage1, plan.stage1_config).run(
                plan.run_id,
                plan.robot_public_projection,
                [task.stage1_view() for task in plan.tasks],
                plan.g2_profile,
                design_experience_snapshot=plan.design_experience_snapshot,
            )
        except Exception as exc:
            call_log = _recorded_stage_calls(self._models.stage1, "stage1")
            artifacts["stages"]["stage1"] = {
                "status": "ERROR",
                "capability_design": None,
                "design_hash": None,
                "seal": None,
                "call_log": call_log,
                "diagnostics": [{
                    "code": "STAGE1_EXCEPTION",
                    "message": f"{type(exc).__name__}: {exc}",
                }],
            }
            stages.append({
                "stage": "stage1",
                "status": "ERROR",
                "design_hash": None,
                "llm_calls": len(call_log),
            })
            return self._finish(
                "STAGE1_FAILED",
                base,
                stages,
                parents,
                artifacts=artifacts,
            )
        artifacts["stages"]["stage1"] = _stage1_artifact(stage1)
        stages.append({
            "stage": "stage1",
            "status": stage1.status,
            "design_hash": stage1.design_hash,
            "llm_calls": len(stage1.call_log),
        })
        if stage1.status != "SEALED" or stage1.capability_design is None or stage1.seal is None or stage1.design_hash is None:
            return self._finish(
                "STAGE1_FAILED",
                base,
                stages,
                parents,
                artifacts=artifacts,
            )
        if not self._design_covers_tasks(stage1.capability_design, plan.tasks):
            stages[-1]["status"] = "DESIGN_GAP"
            return self._finish(
                "DESIGN_GAP",
                base,
                stages,
                parents,
                artifacts=artifacts,
            )
        validation_a_profile = plan.validation_a_profile
        if self._validation_a_materializer is not None:
            try:
                validation_a_profile = self._validation_a_materializer(stage1)
            except Exception as exc:
                stage1_artifact = artifacts["stages"].get("stage1")
                if isinstance(stage1_artifact, dict):
                    stage1_artifact.setdefault("diagnostics", []).append({
                        "code": "VALIDATION_A_MATERIALIZATION",
                        "message": f"{type(exc).__name__}: {exc}",
                    })
                stages[-1]["status"] = "MATERIALIZATION_FAILED"
                return self._finish(
                    "STAGE1_FAILED",
                    base,
                    stages,
                    parents,
                    artifacts=artifacts,
                )
        if not isinstance(validation_a_profile, ValidationAProfile):
            raise ContractError("General Demo requires a materialized ValidationAProfile")
        base["validation_a_profile_hash"] = validation_a_profile.profile_hash
        parents.add(validation_a_profile.profile_hash)
        if isinstance(base.get("gate_bindings"), Mapping):
            base["gate_bindings"] = dict(base["gate_bindings"])
            base["gate_bindings"]["validation_a_profile_hash"] = validation_a_profile.profile_hash
        validation_harness_config = copy.deepcopy(dict(plan.validation_harness_config))
        validation_harness_config["validation_a_profile_hash"] = validation_a_profile.profile_hash
        try:
            for capability in stage1.capability_design["capabilities"]:
                _object_schema(capability["inputs"])
                _object_schema(capability["outputs"])
        except (KeyError, TypeError, ContractError):
            stages[-1]["status"] = "DESIGN_GAP"
            return self._finish(
                "DESIGN_GAP",
                base,
                stages,
                parents,
                artifacts=artifacts,
            )
        parents.add(stage1.design_hash)

        blue = BlueLineRunner(self._models.blue_line).run(
            stage1.capability_design,
            stage1.seal,
            plan.standards_snapshot,
            plan.measurement_catalog,
            plan.blue_line_policy,
        )
        artifacts["stages"]["blue_line"] = _blue_line_artifact(blue)
        stages.append({
            "stage": "blue_line",
            "status": blue.status,
            "manifest_hash": blue.manifest_hash,
            "suite_hash": blue.suite_hash,
            "llm_calls": len(blue.call_log),
        })
        parents.add(blue.manifest_hash)
        if blue.status != "READY" or blue.validation_suite is None or blue.suite_hash is None or blue.suite_seal is None or blue.stage2_authorization is None:
            return self._finish(
                "BLUE_LINE_NEEDS_REVIEW",
                base,
                stages,
                parents,
                artifacts=artifacts,
            )
        parents.add(blue.suite_hash)

        implementation_bundle = validate_implementation_bundle(plan.implementation_bundle)
        if plan.implementation_experience_snapshot is not None:
            derived_artifact = implementation_bundle.artifact
            derived_artifact["implementation_experience"].extend(
                copy.deepcopy(item["projection"])
                for item in plan.implementation_experience_snapshot["records"]
            )
            implementation_bundle = validate_implementation_bundle(derived_artifact)
        parents.add(implementation_bundle.bundle_hash)

        stage2_runner = Stage2Runner(
            self._models.stage2,
            sandbox=self._models.sandbox,
            config=plan.stage2_config,
        )
        stage2 = stage2_runner.run(
            stage1.capability_design,
            stage1.seal,
            blue.stage2_authorization,
            implementation_bundle,
        )
        artifacts["stages"]["stage2"] = _stage2_artifact(stage2)
        stages.append({
            "stage": "stage2",
            "status": stage2.status,
            "binding_hash": stage2.binding_hash,
            "source_hash": stage2.source_hash,
            "implementation_manifest_hash": stage2.manifest_hash,
            "llm_calls": stage2.llm_calls,
            "sandbox_calls": stage2.sandbox_calls,
            "successful_sandbox_calls": stage2.successful_sandbox_calls,
            "covered_sandbox_capability_ids": list(stage2.covered_sandbox_capability_ids),
        })
        parents.add(stage2.binding_hash)
        if (
            stage2.status != "SUBMITTED"
            or stage2.capability_source is None
            or stage2.source_hash is None
            or stage2.implementation_manifest is None
            or stage2.manifest_hash is None
            or stage2.manifest_seal is None
        ):
            return self._finish(
                "STAGE2_FAILED",
                base,
                stages,
                parents,
                artifacts=artifacts,
            )
        parents.update({stage2.source_hash, stage2.manifest_hash})

        validation_harness = RecordingValidationHarness(
            self._robot_session,
            self._video_profile,
            self._video_encoder_factory,
            route,
            validation_harness_config,
        )
        validation_context = ValidationContext(
            capability_design=stage1.capability_design,
            design_seal=stage1.seal,
            blue_line_spec=blue.validation_spec,
            spec_seal=blue.spec_seal,
            blue_line_manifest=blue.manifest,
            manifest_seal=blue.manifest_seal,
            validation_suite=blue.validation_suite,
            suite_seal=blue.suite_seal,
            blue_line_ready_bundle=blue.validation_authorization,
            run_snapshot={
                "artifact_type": "validation_run_snapshot",
                "schema_version": "1.0.0",
                "design_hash": stage1.design_hash,
                "blue_line_spec_hash": blue.spec_hash,
                "blue_line_manifest_hash": blue.manifest_hash,
                "suite_hash": blue.suite_hash,
                "implementation_bundle_hash": implementation_bundle.bundle_hash,
                "rim_hash": route.integration_manifest_hash,
                "sdk_entry_hash": route.sdk_entry_hash,
                "runtime_hash": route.runtime_hash,
                "harness_config_hash": validation_harness.config_hash,
            },
        )
        validation = RepairRunner(
            ValidationARunner(validation_a_profile),
            ValidationBRunner(validation_harness),
            stage2_runner.repair_episode,
            plan.repair_config,
        ).run(
            stage1.capability_design,
            stage1.seal,
            stage2.binding_contract,
            stage2.binding_seal,
            {"capability.py": stage2.capability_source},
            stage2.implementation_manifest,
            stage2.manifest_seal,
            validation_context,
            implementation_bundle,
        )
        artifacts["stages"]["validation_and_repair"] = {
            "repair": _repair_artifact(validation),
        }
        initial_b_status = (
            validation.initial_validation_b.status
            if validation.initial_validation_b is not None else None
        )
        final_b_status = (
            validation.final_validation_b.status
            if validation.final_validation_b is not None else None
        )
        stages.append({
            "stage": "validation_and_repair",
            "status": validation.status,
            "pass_at_0": validation.initial_validation_a.status == "PASS" and initial_b_status == "PASS",
            "initial_validation_a": validation.initial_validation_a.status,
            "initial_validation_b": initial_b_status,
            "first_passing_repair_index": validation.first_passing_repair_index,
            "repair_invocations_used": validation.repair_invocations_used,
            "candidate_revisions_created": validation.candidate_revisions_created,
            "repairs_consumed": validation.repairs_consumed,
            "repair_llm_calls": validation.repair_llm_calls,
            "final_validation_a": validation.final_validation_a.status if validation.final_validation_a else None,
            "final_validation_b": final_b_status,
        })
        validation_videos = validation_harness.video_handles
        for handle in validation_videos:
            parents.add(handle.manifest_content_hash)
            if handle.media_content_hash is not None:
                parents.add(handle.media_content_hash)
        if validation.status != "PASS":
            return self._finish(
                "VALIDATION_FAILED",
                base,
                stages,
                parents,
                validation_videos=validation_videos,
                artifacts=artifacts,
            )
        final_a = validation.final_validation_a
        final_b = validation.final_validation_b
        if (
            final_a is None
            or final_b is None
            or final_a.status != "PASS"
            or final_b.status != "PASS"
            or final_a.candidate_handle is None
            or not validation_videos
        ):
            raise ContractError("Validation PASS is missing its candidate or required video lineage")
        parents.update({final_a.report_hash, final_b.report_hash})

        promoted, layer_hash = self._promote(
            stage1.capability_design,
            final_a.candidate_handle,
            final_a.implementation_manifest_hash,
            final_b.report_hash,
        )
        parents.add(layer_hash)
        promoted_ids = tuple(item.capability_id for item in promoted)
        layer = CapabilityToolLayer(promoted)
        consumer = ReActConsumer(
            layer,
            self._models.consumer,
            max_steps=plan.consumer_max_steps,
            public_state_schema=plan.public_state_schema,
        )
        demo = DemoEvaluationHarness(
            self._robot_session,
            self._video_profile,
            self._video_encoder_factory,
            route,
            self._criterion_evaluator,
            repetitions=plan.demo_repetitions,
        )
        trials = demo.run(
            consumer,
            plan.tasks,
            seed=plan.consumer_seed,
            consumer_id=plan.consumer_id,
            layer_hash=layer_hash,
            visible_capability_ids=promoted_ids,
        )
        for trial in trials:
            if trial.video_handle is not None:
                parents.add(trial.video_handle.manifest_content_hash)
                if trial.video_handle.media_content_hash is not None:
                    parents.add(trial.video_handle.media_content_hash)
            if trial.consumer_result is not None:
                parents.add(content_hash(canonical_bytes(trial.consumer_result.trace)))
        demo_status = (
            "INFRASTRUCTURE_ERROR"
            if any(item.status == "INFRASTRUCTURE_ERROR" for item in trials)
            else "PASS" if all(item.status == "PASS" for item in trials)
            else "FAIL"
        )
        stages.append({
            "stage": "promotion",
            "status": "PROMOTED",
            "layer_hash": layer_hash,
            "capability_ids": list(promoted_ids),
        })
        artifacts["promotion"] = {
            "status": "PROMOTED",
            "layer_hash": layer_hash,
            "capability_ids": list(promoted_ids),
        }
        artifacts["demo"] = {
            "status": demo_status,
            "trials": [
                _demo_trial_artifact(trial, include_private_trace=True)
                for trial in trials
            ],
        }
        stages.append({
            "stage": "demo",
            "status": demo_status,
            "task_count": 5,
            "repetitions": plan.demo_repetitions,
            "trial_count": len(trials),
        })
        final_status = {
            "PASS": "COMPLETE",
            "FAIL": "DEMO_FAILED",
            "INFRASTRUCTURE_ERROR": "DEMO_INFRASTRUCTURE_ERROR",
        }[demo_status]
        return self._finish(
            final_status,
            base,
            stages,
            parents,
            validation_videos=validation_videos,
            demo_trials=trials,
            promoted_capability_ids=promoted_ids,
            artifacts=artifacts,
        )

    @staticmethod
    def _verify_robot_projection(
        manifest: Mapping[str, Any], projection: Mapping[str, Any]
    ) -> None:
        expected = {
            "robot_model_id": manifest.get("robot_model_id"),
            "robot_configuration_id": manifest.get("robot_configuration_id"),
        }
        if any(projection.get(key) != value for key, value in expected.items()):
            raise ContractError("robot public projection does not match the verified integration manifest")

    def _verify_robot_session(self, manifest: Mapping[str, Any]) -> None:
        if (
            self._robot_session.robot_model_id != manifest.get("robot_model_id")
            or self._robot_session.robot_configuration_id
            != manifest.get("robot_configuration_id")
        ):
            raise ContractError("robot session does not match the verified integration manifest")

    def _verify_tasks_library(
        self, manifest: Mapping[str, Any], plan: DemoRunPlan
    ) -> None:
        package = TasksLibrary(self._root / "general_demo/libraries/tasks").load(
            str(manifest.get("robot_configuration_id")),
            plan.task_catalog_version,
        )
        public_tasks = package.demo_public_tasks(plan.run_id)
        private_criteria = package.demo_private_criteria()
        if len(public_tasks) != len(plan.tasks) or len(private_criteria) != len(plan.tasks):
            raise ContractError("DemoRunPlan does not match the selected Tasks Library collection")
        for task, public, criterion in zip(
            plan.tasks, public_tasks, private_criteria, strict=True
        ):
            if (
                task.task_id != public["task_id"]
                or task.requirement_id != public["requirement_id"]
                or task.description != public["description"]
            ):
                raise ContractError(
                    "DemoRunPlan public task view does not match the selected Tasks Library collection"
                )
            if dict(task.private_criterion) != criterion:
                raise ContractError(
                    "DemoRunPlan private criterion does not match the selected Tasks Library record"
                )

    def _verify_frozen_run_inputs(self, plan: DemoRunPlan) -> None:
        snapshot_path = Path(plan.run_snapshot_path)
        if not snapshot_path.is_absolute():
            snapshot_path = self._root / snapshot_path
        snapshot = load_run_snapshot(snapshot_path).value

        def value_for(reference: Mapping[str, Any]) -> dict[str, Any]:
            path = verify_file_reference(self._root, reference)
            return load_json_artifact(path).value

        task_set = value_for(snapshot["task_set_ref"])
        expected_tasks = {"tasks": [task.frozen_record() for task in plan.tasks]}
        if task_set != expected_tasks:
            raise ContractError("DemoRunPlan tasks do not match the frozen task-set reference")
        if value_for(snapshot["g2_profile_ref"]) != dict(plan.g2_profile):
            raise ContractError("DemoRunPlan G2 profile does not match the frozen run input")
        if value_for(snapshot["observation_profile_ref"]) != dict(plan.robot_public_projection):
            raise ContractError("robot public projection does not match the frozen run input")
        blue_inputs = [value_for(reference) for reference in snapshot["blue_line_input_refs"]]
        if blue_inputs != [
            dict(plan.standards_snapshot),
            dict(plan.measurement_catalog),
            dict(plan.blue_line_policy),
        ]:
            raise ContractError("Blue Line inputs do not match the frozen run input")
        library_views = [value_for(reference) for reference in snapshot["library_view_refs"]]
        if dict(plan.implementation_bundle) not in library_views:
            raise ContractError("Stage 2 Implementation Bundle is not frozen in the run input")
        for field in ("design_experience_snapshot", "implementation_experience_snapshot"):
            snapshot_value = getattr(plan, field)
            if snapshot_value is not None and dict(snapshot_value) not in library_views:
                raise ContractError(
                    f"{field} is not frozen in the run input"
                )

    @staticmethod
    def _derive_direct_route(plan: DemoRunPlan) -> EvaluationRoute:
        """Create a minimal content-hashed lineage for a direct route plan.

        The full direct entry point supplies hashes for the no-SDK record,
        morphology, task adapter, and runtime.  This fallback keeps the plan
        usable by small callers that only need the shared runner boundary; it
        deliberately creates no manifest or readiness artifact.
        """

        metadata = dict(plan.route_metadata or {})

        def route_hash(field: str, value: Mapping[str, Any]) -> str:
            supplied = metadata.get(field)
            if supplied is not None:
                return _prefixed_hash(supplied, field)
            return content_hash(canonical_bytes(dict(value)))

        return EvaluationRoute(
            run_id=plan.run_id,
            integration_manifest_hash=route_hash(
                "route_hash",
                {
                    "execution_route": _DIRECT_MUJOCO_EXPERIMENTAL,
                    "robot_model_id": plan.robot_public_projection.get("robot_model_id"),
                    "robot_configuration_id": plan.robot_public_projection.get(
                        "robot_configuration_id"
                    ),
                },
            ),
            sdk_entry_hash=route_hash(
                "sdk_entry_hash",
                {
                    "id": "no-sdk-direct-mujoco",
                    "version": "1.0.0",
                    "sdk_status": "NOT_APPLICABLE",
                },
            ),
            runtime_hash=route_hash(
                "runtime_hash",
                {"execution_route": _DIRECT_MUJOCO_EXPERIMENTAL, "runtime": "mujoco"},
            ),
            simulation_profile_hash=route_hash(
                "simulation_profile_hash",
                {
                    "execution_route": _DIRECT_MUJOCO_EXPERIMENTAL,
                    "robot_configuration_id": plan.robot_public_projection.get(
                        "robot_configuration_id"
                    ),
                },
            ),
        )

    @staticmethod
    def _design_covers_tasks(
        design: Mapping[str, Any], tasks: tuple[DemoTask, ...]
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
        expected = {task.requirement_id for task in tasks}
        return (
            covered == expected
            and design.get("unsupported_requirement_ids") == []
            and design.get("blocking_requirement_ids") == []
        )

    def _base_summary(
        self,
        plan: DemoRunPlan,
        gate: Stage1GateResult | None,
        manifest: Mapping[str, Any],
        route: EvaluationRoute,
    ) -> tuple[dict[str, Any], set[str]]:
        scope = self._robot_session.evidence_scope
        if scope not in {
            "TEST_FIXTURE_ONLY",
            "SDK_GROUNDED_SIMULATION",
            "DIRECT_MUJOCO_EXPERIMENTAL",
        }:
            raise ContractError("robot session evidence_scope is invalid")
        base = {
            "artifact_type": "general_demo_run_summary",
            "format_version": "experimental-1",
            "authority_revision": _AUTHORITY_REVISION,
            "run_id": plan.run_id,
            "robot": {
                "robot_model_id": manifest["robot_model_id"],
                "robot_configuration_id": manifest["robot_configuration_id"],
            },
            "granularity_profile": copy.deepcopy(_G2),
            "execution_scope": scope,
            "sdk_grounded_simulation_claim": scope == "SDK_GROUNDED_SIMULATION",
            "real_hardware_executed": False,
        }
        if scope == _DIRECT_MUJOCO_EXPERIMENTAL:
            if getattr(plan, "execution_route", _DIRECT_MUJOCO_EXPERIMENTAL) != _DIRECT_MUJOCO_EXPERIMENTAL:
                raise ContractError("direct MuJoCo session requires the direct execution route")
            base["gate_bindings"] = {
                "execution_route": _DIRECT_MUJOCO_EXPERIMENTAL,
                "route_hash": route.integration_manifest_hash,
                "sdk_entry_hash": route.sdk_entry_hash,
                "runtime_hash": route.runtime_hash,
                "simulation_profile_hash": route.simulation_profile_hash,
                "video_profile_hash": self._video_profile.content_hash,
            }
            route_metadata = getattr(plan, "route_metadata", None)
            if route_metadata is not None:
                base["direct_route"] = copy.deepcopy(dict(route_metadata))
            return base, {
                route.integration_manifest_hash,
                route.runtime_hash,
                route.sdk_entry_hash,
                route.simulation_profile_hash,
                self._video_profile.content_hash,
            }
        if gate is None:
            raise ContractError("SDK-grounded General Demo requires the verified integration gate")
        readiness_hash = _prefixed_hash(gate.readiness_report_sha256, "readiness report hash")
        profile_hash = _prefixed_hash(gate.readiness_profile_sha256, "readiness profile hash")
        base["gate_bindings"] = {
            "integration_manifest_hash": route.integration_manifest_hash,
            "readiness_report_hash": readiness_hash,
            "readiness_profile_hash": profile_hash,
            "runtime_hash": route.runtime_hash,
            "video_profile_hash": self._video_profile.content_hash,
        }
        return base, {
            route.integration_manifest_hash,
            readiness_hash,
            profile_hash,
            route.runtime_hash,
            route.sdk_entry_hash,
            route.simulation_profile_hash,
            self._video_profile.content_hash,
        }

    def _promote(
        self,
        design: Mapping[str, Any],
        candidate: Any,
        implementation_manifest_hash: str,
        validation_report_hash: str,
    ) -> tuple[tuple[PromotedTool, ...], str]:
        capabilities = design.get("capabilities")
        if not isinstance(capabilities, list) or not capabilities:
            raise ContractError("promotion requires a non-empty sealed capability set")
        tools: list[PromotedTool] = []
        for capability in capabilities:
            if not isinstance(capability, Mapping) or not isinstance(capability.get("capability_id"), str):
                raise ContractError("promotion requires valid capability identities")
            capability_id = capability["capability_id"]

            def entrypoint(
                arguments: Mapping[str, Any],
                *,
                bound_capability_id: str = capability_id,
            ) -> Mapping[str, Any]:
                return self._robot_session.invoke(
                    candidate, bound_capability_id, copy.deepcopy(dict(arguments))
                )

            tools.append(PromotedTool(
                capability_id=capability_id,
                input_schema=_object_schema(capability.get("inputs")),
                output_schema=_object_schema(capability.get("outputs")),
                entrypoint=entrypoint,
                implementation_manifest_sha256=_bare_hash(
                    implementation_manifest_hash, "implementation manifest hash"
                ),
                validation_report_sha256=_bare_hash(
                    validation_report_hash, "Validation B report hash"
                ),
            ))
        layer_hash = content_hash(canonical_bytes({
            "artifact_type": "promoted_tool_layer",
            "capability_ids": [item.capability_id for item in tools],
            "implementation_manifest_hash": _prefixed_hash(
                implementation_manifest_hash, "implementation manifest hash"
            ),
            "validation_report_hash": _prefixed_hash(
                validation_report_hash, "Validation B report hash"
            ),
        }))
        return tuple(tools), layer_hash

    @staticmethod
    def _finish(
        status: str,
        base: Mapping[str, Any],
        stages: list[dict[str, Any]],
        parents: set[str],
        *,
        validation_videos: tuple[ClosedEvaluationVideo, ...] = (),
        demo_trials: tuple[DemoTrialResult, ...] = (),
        promoted_capability_ids: tuple[str, ...] = (),
        artifacts: Mapping[str, Any] | None = None,
    ) -> DemoRunResult:
        trial_records = [_demo_trial_artifact(trial) for trial in demo_trials]
        summary = copy.deepcopy(dict(base)) | {
            "status": status,
            "stages": copy.deepcopy(stages),
            "promoted_capability_ids": list(promoted_capability_ids),
            "validation_video_handles": [_video_record(item) for item in validation_videos],
            "demo_trials": trial_records,
        }
        summary_hash = content_hash(canonical_bytes(summary))
        exact_parents = {item for item in parents if is_content_hash(item)}
        artifact_bundle = copy.deepcopy(dict(artifacts or {}))
        artifact_bundle["summary"] = copy.deepcopy(summary)
        artifact_bundle["summary_hash"] = summary_hash
        artifact_bundle["summary_seal"] = create_seal(
            "general_demo_run_summary", summary_hash, exact_parents
        )
        return DemoRunResult(
            status=status,
            summary=summary,
            summary_hash=summary_hash,
            summary_seal=create_seal("general_demo_run_summary", summary_hash, exact_parents),
            validation_video_handles=validation_videos,
            demo_trials=demo_trials,
            artifacts=artifact_bundle,
        )


__all__ = [
    "DemoModelAdapters",
    "DemoRunPlan",
    "DemoRunResult",
    "GeneralDemoRunner",
]
