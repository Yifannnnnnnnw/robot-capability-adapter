"""Generic full-route entry point for DIRECT_MUJOCO_EXPERIMENTAL.

The direct route resolves one no-SDK sentinel, one morphology record, one
external asset cache, one Tasks Library package, and its matching
``direct_mujoco_adapter.json``.  It then delegates the complete Stage 1 →
Blue Line → Stage 2 → Validation/Repair → Consumer/Demo path to the
shared :class:`GeneralDemoRunner`.

This module owns route assembly only.  It does not create a robot registry,
SDK surface, readiness report, or robot-name dispatch table.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..demo import (
    CriterionEvaluator,
    DemoTask,
    EvaluationRobotSession,
    EvaluationRoute,
    evaluate_fixed_demo_criterion,
)
from ..evaluation import ClosedEvaluationVideo, FrozenVideoProfile
from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash, sha256_bytes
from ..foundation.seals import create_seal
from ..generation import Stage1Config
from ..implementation import CallbackSandbox, Stage2Config
from ..integration import write_stable_json
from ..integration.artifacts import load_json_artifact, verify_file_reference
from ..integrations.direct_mujoco import (
    DirectMuJoCoConfigurationError,
    DirectMuJoCoLibraryConfig,
    DirectMuJoCoTaskConfig,
    create_direct_mujoco_development_sandbox,
    create_direct_mujoco_session,
    load_direct_mujoco_task_config,
)
from ..libraries import TasksLibrary
from ..libraries.no_sdk_direct_mujoco import (
    NO_SDK_DIRECT_MUJOCO_EXECUTION_MODE,
)
from ..validation import RepairConfig, ValidationAProfile
from .demo_runner import (
    DemoModelAdapters,
    DemoRunPlan,
    DemoRunResult,
    GeneralDemoRunner,
)
from .direct_mujoco_run import DIRECT_MUJOCO_EXPERIMENTAL, resolve_morphology_record
from .first_g2_demo import (
    G2_PROFILE,
    ValidationAProfileTemplate,
    _ModelCallCapture,
    _VideoStore,
    _budgets,
    _model_client,
    _new_run_directory,
    _template_from_mapping,
    _video_reference_artifact,
    _write_immutable_json,
    materialize_validation_a_profile,
)


class DirectGeneralDemoResolutionError(ValueError):
    """The generic direct route cannot be resolved from its Library inputs."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DirectGeneralDemoResolutionError(f"{label} must be non-empty text")
    return value.strip()


def _safe_component(value: Any, label: str) -> str:
    component = _text(value, label)
    path = Path(component)
    if path.is_absolute() or len(path.parts) != 1 or component in {".", ".."} or "\\" in component:
        raise DirectGeneralDemoResolutionError(
            f"{label} must be one safe path component, not {value!r}"
        )
    return component


def _project_file(root: Path, value: str | Path, label: str) -> Path:
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DirectGeneralDemoResolutionError(
            f"{label} must resolve beneath the project root"
        ) from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise DirectGeneralDemoResolutionError(f"{label} is not a regular file: {resolved}")
    return resolved


def _json_value(root: Path, value: Mapping[str, Any] | str | Path, label: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    path = _project_file(root, value, label)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DirectGeneralDemoResolutionError(f"could not read {label}: {path}") from exc
    if not isinstance(loaded, Mapping):
        raise DirectGeneralDemoResolutionError(f"{label} must contain a JSON object")
    return copy.deepcopy(dict(loaded))


@dataclass(frozen=True)
class DirectTaskAdapter:
    """Validated view of one generic ``direct_mujoco_task_adapter`` file."""

    path: Path
    robot_configuration_id: str
    tasks: tuple[DirectMuJoCoTaskConfig, ...]
    value: Mapping[str, Any]

    @property
    def by_task_id(self) -> dict[str, DirectMuJoCoTaskConfig]:
        return {task.task_id: task for task in self.tasks}

    @property
    def content_hash(self) -> str:
        return content_hash(canonical_bytes(dict(self.value)))


def load_direct_task_adapter(
    root: str | Path,
    robot_configuration_id: str,
    version: str = "1.0.0",
    path: str | Path | None = None,
) -> DirectTaskAdapter:
    """Load the shared generic direct-task adapter artifact.

    The file shape is deliberately the one used by the Library: an object with
    ``artifact_type``, ``schema_version``, ``robot_configuration_id``,
    ``execution_route``, and a ``tasks`` array.  Each task is parsed by the
    existing ``load_direct_mujoco_task_config`` contract.
    """

    project_root = Path(root).expanduser().resolve()
    if not project_root.is_dir():
        raise DirectGeneralDemoResolutionError(
            f"project root is not a directory: {project_root}"
        )
    configuration = _safe_component(robot_configuration_id, "robot_configuration_id")
    catalog_version = _safe_component(version, "version")
    adapter_path = (
        _project_file(project_root, path, "direct MuJoCo task adapter")
        if path is not None
        else _project_file(
            project_root,
            Path("general_demo/libraries/tasks")
            / configuration
            / catalog_version
            / "direct_mujoco_adapter.json",
            "direct MuJoCo task adapter",
        )
    )
    try:
        value = json.loads(adapter_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DirectGeneralDemoResolutionError(
            f"could not read direct MuJoCo task adapter: {adapter_path}"
        ) from exc
    if not isinstance(value, Mapping):
        raise DirectGeneralDemoResolutionError(
            "direct MuJoCo task adapter must contain a JSON object"
        )
    if value.get("artifact_type") != "direct_mujoco_task_adapter":
        raise DirectGeneralDemoResolutionError(
            "direct MuJoCo task adapter artifact_type must be 'direct_mujoco_task_adapter'"
        )
    if value.get("schema_version") != "1.0.0":
        raise DirectGeneralDemoResolutionError(
            "direct MuJoCo task adapter schema_version must be '1.0.0'"
        )
    if value.get("robot_configuration_id") != configuration:
        raise DirectGeneralDemoResolutionError(
            "direct MuJoCo task adapter robot_configuration_id does not match "
            f"the requested configuration {configuration!r}"
        )
    if value.get("execution_route") != DIRECT_MUJOCO_EXPERIMENTAL:
        raise DirectGeneralDemoResolutionError(
            "direct MuJoCo task adapter execution_route must be "
            f"{DIRECT_MUJOCO_EXPERIMENTAL!r}"
        )
    raw_tasks = value.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise DirectGeneralDemoResolutionError(
            "direct MuJoCo task adapter must provide a non-empty tasks array"
        )
    parsed: list[DirectMuJoCoTaskConfig] = []
    seen: set[str] = set()
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, Mapping):
            raise DirectGeneralDemoResolutionError(
                f"direct MuJoCo task adapter tasks[{index}] must be an object"
            )
        task_id = raw_task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise DirectGeneralDemoResolutionError(
                f"direct MuJoCo task adapter tasks[{index}] is missing task_id"
            )
        if task_id in seen:
            raise DirectGeneralDemoResolutionError(
                f"direct MuJoCo task adapter duplicates task_id {task_id!r}"
            )
        seen.add(task_id)
        try:
            task = load_direct_mujoco_task_config(value, task_id=task_id)
        except DirectMuJoCoConfigurationError as exc:
            raise DirectGeneralDemoResolutionError(
                f"direct MuJoCo task adapter task {task_id!r} is invalid: {exc}"
            ) from exc
        if (
            task.robot_configuration_id is not None
            and task.robot_configuration_id != configuration
        ):
            raise DirectGeneralDemoResolutionError(
                f"direct MuJoCo task adapter task {task_id!r} belongs to "
                f"{task.robot_configuration_id!r}, not {configuration!r}"
            )
        if not task.measurement_declarations:
            raise DirectGeneralDemoResolutionError(
                f"direct MuJoCo task adapter task {task_id!r} has no metric mapping"
            )
        for measurement_index, declaration in enumerate(task.measurement_declarations):
            metric = declaration.get("metric", declaration.get("name"))
            if not isinstance(metric, str) or not metric.strip():
                raise DirectGeneralDemoResolutionError(
                    f"direct MuJoCo task adapter task {task_id!r} measurement "
                    f"{measurement_index} is missing metric"
                )
        parsed.append(task)
    return DirectTaskAdapter(
        path=adapter_path,
        robot_configuration_id=configuration,
        tasks=tuple(parsed),
        value=copy.deepcopy(dict(value)),
    )


load_direct_mujoco_task_adapter = load_direct_task_adapter


SessionFactory = Callable[[DirectMuJoCoLibraryConfig], EvaluationRobotSession]


class DirectGeneralDemoSession:
    """Lazy task-selecting EvaluationRobotSession over the shared direct session."""

    evidence_scope = DIRECT_MUJOCO_EXPERIMENTAL

    def __init__(
        self,
        base_config: DirectMuJoCoLibraryConfig,
        task_adapter: DirectTaskAdapter,
        *,
        video_profile: FrozenVideoProfile,
        session_factory: SessionFactory | None = None,
        default_task_id: str | None = None,
    ) -> None:
        if not isinstance(base_config, DirectMuJoCoLibraryConfig):
            raise ContractError("direct General Demo requires a DirectMuJoCoLibraryConfig")
        if not isinstance(task_adapter, DirectTaskAdapter):
            raise ContractError("direct General Demo requires a DirectTaskAdapter")
        if not isinstance(video_profile, FrozenVideoProfile):
            raise ContractError("direct General Demo requires a FrozenVideoProfile")
        if default_task_id is not None and default_task_id not in task_adapter.by_task_id:
            raise DirectGeneralDemoResolutionError(
                f"default_task_id {default_task_id!r} is not in the direct task adapter"
            )
        self._base_config = base_config
        self._task_adapter = task_adapter
        self._video_profile = video_profile
        self._session_factory = session_factory or (
            lambda config: create_direct_mujoco_session(
                config,
                video_profile=video_profile,
            )
        )
        self._default_task_id = default_task_id
        self._development_session: EvaluationRobotSession | None = None
        self._session: EvaluationRobotSession | None = None
        self._task_id: str | None = None
        self._closed = False

    @property
    def sdk(self) -> object:
        if self._closed:
            raise DirectGeneralDemoResolutionError("direct General Demo session is closed")
        if self._session is not None:
            return self._session.sdk
        if self._development_session is None:
            self._development_session = self._create_session(self._base_config)
        return self._development_session.sdk

    @property
    def simulation_time_s(self) -> float:
        if self._session is None:
            return 0.0
        return self._session.simulation_time_s

    @property
    def robot_model_id(self) -> str:
        return self._base_config.robot_model_id

    @property
    def robot_configuration_id(self) -> str:
        return self._base_config.robot_configuration_id

    @property
    def video_profile(self) -> FrozenVideoProfile:
        return self._video_profile

    @property
    def model_path(self) -> Path:
        return self._base_config.model_path

    @property
    def development_model_path(self) -> Path:
        return self.model_path

    @property
    def composed_model_path(self) -> Path:
        return self.model_path

    @property
    def task_id(self) -> str | None:
        return self._task_id

    @property
    def accepted_action_count(self) -> int:
        return int(getattr(self._session, "accepted_action_count", 0))

    @property
    def physics_step_count(self) -> int:
        return int(getattr(self._session, "physics_step_count", 0))

    def _current(self) -> EvaluationRobotSession:
        if self._closed:
            raise DirectGeneralDemoResolutionError("direct General Demo session is closed")
        if self._session is None:
            raise DirectGeneralDemoResolutionError(
                "direct General Demo session has not been reset to a task"
            )
        return self._session

    def _create_session(self, config: DirectMuJoCoLibraryConfig) -> EvaluationRobotSession:
        session = self._session_factory(config)
        if not isinstance(session, EvaluationRobotSession):
            raise ContractError("direct session factory did not return an EvaluationRobotSession")
        if not callable(getattr(session, "close", None)):
            raise ContractError("direct session factory did not return a closeable session")
        return session

    @staticmethod
    def _task_id_from_state(initial_state: Mapping[str, Any]) -> str | None:
        for key in ("task_id", "task"):
            value = initial_state.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, Mapping):
                nested = value.get("task_id")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
        return None

    def _select_task(self, initial_state: Mapping[str, Any]) -> DirectMuJoCoTaskConfig:
        requested = self._task_id_from_state(initial_state)
        task_id = requested or self._default_task_id
        if task_id is None and len(self._task_adapter.tasks) == 1:
            task_id = self._task_adapter.tasks[0].task_id
        if task_id is None:
            raise DirectGeneralDemoResolutionError(
                "direct task adapter requires initial_state.task_id when more than one "
                "task is configured"
            )
        task = self._task_adapter.by_task_id.get(task_id)
        if task is None:
            raise DirectGeneralDemoResolutionError(
                f"direct task adapter has no task mapping for {task_id!r}"
            )
        return task

    def reset(
        self,
        *,
        phase: str,
        execution_id: str,
        initial_state: Mapping[str, Any],
    ) -> None:
        if self._closed:
            raise DirectGeneralDemoResolutionError("direct General Demo session is closed")
        if not isinstance(initial_state, Mapping):
            raise DirectGeneralDemoResolutionError("direct task initial_state must be an object")
        task = self._select_task(initial_state)
        old = self._session
        development = self._development_session
        self._session = None
        self._development_session = None
        if old is not None:
            old.close()
        if development is not None and development is not old:
            development.close()
        bound_config = self._base_config.bind_task(task)
        try:
            session = self._create_session(bound_config)
            session.reset(
                phase=phase,
                execution_id=execution_id,
                initial_state=copy.deepcopy(dict(initial_state)),
            )
        except Exception:
            try:
                if "session" in locals() and session is not None:
                    session.close()
            except Exception:
                pass
            raise
        self._session = session
        self._task_id = task.task_id

    def start_external_recording(self, *, phase: str, execution_id: str) -> None:
        self._current().start_external_recording(phase=phase, execution_id=execution_id)

    def stop_external_recording(self) -> tuple[Any, ...]:
        return self._current().stop_external_recording()

    def validation_evidence(self, invocation: Any) -> Any:
        return self._current().validation_evidence(invocation)

    def demo_evidence(self, task_id: str) -> Mapping[str, Any]:
        if self._task_id != task_id:
            raise DirectGeneralDemoResolutionError(
                f"active direct task {self._task_id!r} does not match Demo task {task_id!r}"
            )
        return self._current().demo_evidence(task_id)

    def invoke(
        self,
        candidate: Any,
        capability_id: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return self._current().invoke(candidate, capability_id, arguments)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        session = self._session
        development = self._development_session
        self._session = None
        self._development_session = None
        if session is not None:
            session.close()
        if development is not None and development is not session:
            development.close()


@dataclass(frozen=True)
class DirectGeneralDemoConfig:
    """Inputs for one generic, no-SDK General Demo run.

    The optional object/path fields are the same frozen Library views accepted
    by the existing first-Demo entry point.  A direct caller may provide them
    directly, or provide a run snapshot containing the corresponding file
    references.  No integration manifest or readiness report is part of this
    route.
    """

    root: str | Path
    robot_configuration_id: str
    asset_cache_root: str | Path
    version: str = "1.0.0"
    task_catalog_version: str = "1.0.0"
    run_id: str = "direct-general-demo"
    task_adapter_path: str | Path | None = None
    run_snapshot_path: str | Path | None = None
    task_set: Mapping[str, Any] | str | Path | None = None
    robot_public_projection: Mapping[str, Any] | str | Path | None = None
    g2_profile: Mapping[str, Any] | str | Path | None = None
    standards_snapshot: Mapping[str, Any] | str | Path | None = None
    measurement_catalog: Mapping[str, Any] | str | Path | None = None
    blue_line_policy: Mapping[str, Any] | str | Path | None = None
    implementation_bundle: Mapping[str, Any] | str | Path | None = None
    validation_a_profile: (
        ValidationAProfile
        | ValidationAProfileTemplate
        | Mapping[str, Any]
        | str
        | Path
        | None
    ) = None
    public_state_schema: Mapping[str, Any] | str | Path | None = None
    validation_harness_config: Mapping[str, Any] | str | Path | None = None
    budget: Mapping[str, Any] | str | Path | None = None
    model_prompt_config: Mapping[str, Any] | str | Path | None = None
    run_inputs: Mapping[str, Any] | None = None
    design_experience_snapshot: Mapping[str, Any] | None = None
    implementation_experience_snapshot: Mapping[str, Any] | None = None
    output_root: str | Path | None = None
    video_profile: FrozenVideoProfile | None = None
    model_client: Any | None = None
    models: DemoModelAdapters | None = None
    sandbox: CallbackSandbox | None = None
    session_factory: SessionFactory | None = None
    criterion_evaluator: CriterionEvaluator | None = None
    validation_a_materializer: Callable[[Any], ValidationAProfile] | None = None
    stage1_config: Stage1Config | None = None
    stage2_config: Stage2Config | None = None
    repair_config: RepairConfig | None = None
    consumer_id: str = "react-consumer-experimental"
    consumer_max_steps: int = 4
    consumer_seed: int = 0
    demo_repetitions: int = 1

    def __post_init__(self) -> None:
        _text(self.robot_configuration_id, "robot_configuration_id")
        _text(self.version, "version")
        _text(self.task_catalog_version, "task_catalog_version")
        run_id = _text(self.run_id, "run_id")
        if Path(run_id).is_absolute() or len(Path(run_id).parts) != 1 or run_id in {".", ".."}:
            raise ContractError("direct General Demo run_id must be one safe path component")
        if self.run_inputs is not None and not isinstance(self.run_inputs, Mapping):
            raise ContractError("run_inputs must be an object when supplied")
        for field in (
            "design_experience_snapshot",
            "implementation_experience_snapshot",
        ):
            value = getattr(self, field)
            if value is not None and not isinstance(value, Mapping):
                raise ContractError(f"{field} must be an object when supplied")
        if self.models is not None and not isinstance(self.models, DemoModelAdapters):
            raise ContractError("direct General Demo models must be DemoModelAdapters")
        if self.models is not None and self.model_client is not None:
            raise ContractError("direct General Demo accepts models or model_client, not both")
        if self.sandbox is not None and not isinstance(self.sandbox, CallbackSandbox):
            raise ContractError("direct General Demo sandbox must be a CallbackSandbox")
        if self.video_profile is not None and not isinstance(
            self.video_profile, FrozenVideoProfile
        ):
            raise ContractError("direct General Demo video_profile must be a FrozenVideoProfile")
        if self.criterion_evaluator is not None and not callable(self.criterion_evaluator):
            raise ContractError("direct General Demo criterion_evaluator must be callable")
        for field in ("consumer_id",):
            _text(getattr(self, field), field)
        for field in ("consumer_max_steps", "consumer_seed", "demo_repetitions"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ContractError(f"{field} must be an integer")
            if field == "consumer_seed" and value < 0:
                raise ContractError("consumer_seed must be non-negative")
            if field != "consumer_seed" and value <= 0:
                raise ContractError(f"{field} must be positive")
        for field in ("stage1_config", "stage2_config", "repair_config"):
            value = getattr(self, field)
            expected = {
                "stage1_config": Stage1Config,
                "stage2_config": Stage2Config,
                "repair_config": RepairConfig,
            }[field]
            if value is not None and not isinstance(value, expected):
                raise ContractError(f"{field} has the wrong configuration type")
        if self.validation_a_materializer is not None and not callable(
            self.validation_a_materializer
        ):
            raise ContractError("validation_a_materializer must be callable")
        for field in (
            "run_inputs",
            "design_experience_snapshot",
            "implementation_experience_snapshot",
        ):
            value = getattr(self, field)
            if isinstance(value, Mapping):
                object.__setattr__(self, field, copy.deepcopy(dict(value)))


@dataclass(frozen=True)
class DirectGeneralDemoResult:
    """Persisted handoff for one direct General Demo attempt."""

    robot_configuration_id: str
    run_id: str
    status: str
    run_directory: Path
    summary_path: Path
    seal_path: Path
    validation_video_references_path: Path
    demo_video_references_path: Path
    stage_artifacts_path: Path
    stage_artifacts_seal_path: Path
    model_call_log_path: Path
    run_closure_path: Path
    run_closure_seal_path: Path
    summary_hash: str
    closure_hash: str
    runner_result: DemoRunResult

    @property
    def robot(self) -> str:
        """Compatibility alias used by the first-G2 artifact handoff."""

        return self.robot_configuration_id


def _source_object(
    root: Path,
    value: Mapping[str, Any] | str | Path,
    label: str,
) -> dict[str, Any]:
    return _json_value(root, value, label)


def _snapshot_reference_value(
    root: Path,
    reference: Any,
    label: str,
) -> dict[str, Any]:
    if not isinstance(reference, Mapping):
        raise DirectGeneralDemoResolutionError(f"{label} must be a file reference object")
    try:
        path = verify_file_reference(root, reference)
        return copy.deepcopy(load_json_artifact(path).value)
    except Exception as exc:
        if isinstance(exc, DirectGeneralDemoResolutionError):
            raise
        raise DirectGeneralDemoResolutionError(f"could not resolve {label}") from exc


def _resolve_direct_inputs(
    config: DirectGeneralDemoConfig,
    root: Path,
) -> dict[str, Any]:
    """Resolve direct inputs from explicit values and, secondarily, a snapshot."""

    values = copy.deepcopy(dict(config.run_inputs or {}))
    snapshot: dict[str, Any] | None = None
    if config.run_snapshot_path is not None:
        snapshot = _source_object(root, config.run_snapshot_path, "run snapshot")

    if snapshot is not None:
        reference_fields = (
            "task_set",
            "g2_profile",
            "observation_profile",
            "model_prompt_config",
            "budget",
        )
        for field in reference_fields:
            if field in values:
                continue
            reference = snapshot.get(f"{field}_ref")
            if reference is not None:
                values[field] = _snapshot_reference_value(
                    root, reference, f"run snapshot {field}_ref"
                )
            elif field in snapshot and isinstance(snapshot[field], Mapping):
                values[field] = copy.deepcopy(dict(snapshot[field]))
        if "blue_line_inputs" not in values:
            references = snapshot.get("blue_line_input_refs")
            if references is not None:
                if not isinstance(references, list) or len(references) != 3:
                    raise DirectGeneralDemoResolutionError(
                        "run snapshot must provide exactly three blue_line_input_refs"
                    )
                values["blue_line_inputs"] = [
                    _snapshot_reference_value(root, reference, f"blue_line_input_refs[{index}]")
                    for index, reference in enumerate(references)
                ]
        if "library_views" not in values:
            references = snapshot.get("library_view_refs")
            if references is not None:
                if not isinstance(references, list):
                    raise DirectGeneralDemoResolutionError(
                        "run snapshot library_view_refs must be an array"
                    )
                values["library_views"] = [
                    _snapshot_reference_value(root, reference, f"library_view_refs[{index}]")
                    for index, reference in enumerate(references)
                ]
        for field in (
            "standards_snapshot",
            "measurement_catalog",
            "blue_line_policy",
            "implementation_bundle",
            "validation_harness_config",
            "validation_a_profile",
        ):
            if field in values or field not in snapshot:
                continue
            item = snapshot[field]
            values[field] = copy.deepcopy(dict(item)) if isinstance(item, Mapping) else item

    explicit_sources: dict[str, Any] = {
        "task_set": config.task_set,
        "observation_profile": config.robot_public_projection,
        "g2_profile": config.g2_profile,
        "standards_snapshot": config.standards_snapshot,
        "measurement_catalog": config.measurement_catalog,
        "blue_line_policy": config.blue_line_policy,
        "implementation_bundle": config.implementation_bundle,
        "validation_a_profile": config.validation_a_profile,
        "public_state_schema": config.public_state_schema,
        "validation_harness_config": config.validation_harness_config,
        "budget": config.budget,
        "model_prompt_config": config.model_prompt_config,
    }
    for field, source in explicit_sources.items():
        if source is None:
            continue
        if isinstance(source, (str, Path)):
            values[field] = _source_object(root, source, field)
        elif isinstance(source, ValidationAProfile):
            values[field] = source
        elif isinstance(source, ValidationAProfileTemplate):
            values[field] = source
        elif isinstance(source, Mapping):
            values[field] = copy.deepcopy(dict(source))
        else:
            raise DirectGeneralDemoResolutionError(f"{field} must be an object or JSON file")

    library_views = values.get("library_views", [])
    if not isinstance(library_views, list) or any(not isinstance(item, Mapping) for item in library_views):
        raise DirectGeneralDemoResolutionError("library_views must be an array of objects")
    normalized_views = [copy.deepcopy(dict(item)) for item in library_views]
    values["library_views"] = normalized_views
    blue_line_inputs = values.get("blue_line_inputs")
    if blue_line_inputs is not None:
        if (
            not isinstance(blue_line_inputs, list)
            or len(blue_line_inputs) != 3
            or any(not isinstance(item, Mapping) for item in blue_line_inputs)
        ):
            raise DirectGeneralDemoResolutionError(
                "blue_line_inputs must contain exactly three objects"
            )
        values.setdefault("standards_snapshot", copy.deepcopy(dict(blue_line_inputs[0])))
        values.setdefault("measurement_catalog", copy.deepcopy(dict(blue_line_inputs[1])))
        values.setdefault("blue_line_policy", copy.deepcopy(dict(blue_line_inputs[2])))
    for item in normalized_views:
        artifact_type = item.get("artifact_type")
        if artifact_type == "stage2_implementation_bundle" and "implementation_bundle" not in values:
            values["implementation_bundle"] = copy.deepcopy(item)
        elif artifact_type == "validation_a_template" and "validation_a_profile" not in values:
            values["validation_a_profile"] = copy.deepcopy(item)
        elif artifact_type in {"validation_harness_config", "recording_validation_harness_config"} and "validation_harness_config" not in values:
            values["validation_harness_config"] = copy.deepcopy(item)
        elif artifact_type == "experience_snapshot":
            recipient = item.get("recipient_class")
            if recipient == "design" and config.design_experience_snapshot is None:
                values.setdefault("design_experience_snapshot", copy.deepcopy(item))
            if recipient == "implementation" and config.implementation_experience_snapshot is None:
                values.setdefault("implementation_experience_snapshot", copy.deepcopy(item))

    values.setdefault("g2_profile", copy.deepcopy(G2_PROFILE))
    values.setdefault("budget", None)
    values.setdefault("validation_harness_config", {})
    if config.design_experience_snapshot is not None:
        values["design_experience_snapshot"] = copy.deepcopy(dict(config.design_experience_snapshot))
    if config.implementation_experience_snapshot is not None:
        values["implementation_experience_snapshot"] = copy.deepcopy(dict(config.implementation_experience_snapshot))
    return values


def _tasks_from_direct_inputs(
    package: Any,
    task_adapter: DirectTaskAdapter,
    run_id: str,
    task_set: Any,
    task_catalog_version: str,
) -> tuple[DemoTask, ...]:
    task_package = TasksLibrary(package.repo_root / "general_demo/libraries/tasks").load(
        package.robot_configuration_id,
        task_catalog_version,
    )
    public_tasks = task_package.demo_public_tasks(run_id)
    private_criteria = task_package.demo_private_criteria()
    if len(public_tasks) != 5 or len(private_criteria) != 5:
        raise DirectGeneralDemoResolutionError(
            "the selected Tasks Library must provide exactly five public tasks and criteria"
        )
    adapter_ids = set(task_adapter.by_task_id)
    public_ids = {item["task_id"] for item in public_tasks}
    if adapter_ids != public_ids:
        missing = sorted(public_ids - adapter_ids)
        extra = sorted(adapter_ids - public_ids)
        raise DirectGeneralDemoResolutionError(
            "direct task adapter coverage does not match the five Demo tasks "
            f"(missing={missing}, extra={extra})"
        )

    state_by_task: dict[str, Mapping[str, Any]] = {}
    if task_set is not None:
        if not isinstance(task_set, Mapping) or not isinstance(task_set.get("tasks"), list):
            raise DirectGeneralDemoResolutionError("task_set must contain a tasks array")
        for index, raw in enumerate(task_set["tasks"]):
            if not isinstance(raw, Mapping) or not isinstance(raw.get("task_id"), str):
                raise DirectGeneralDemoResolutionError(
                    f"task_set.tasks[{index}] must contain a task_id"
                )
            task_id = raw["task_id"]
            if task_id in state_by_task:
                raise DirectGeneralDemoResolutionError(
                    f"task_set duplicates task_id {task_id!r}"
                )
            public_state = raw.get("public_state", {"task_id": task_id})
            if not isinstance(public_state, Mapping):
                raise DirectGeneralDemoResolutionError(
                    f"task_set task {task_id!r} public_state must be an object"
                )
            state = copy.deepcopy(dict(public_state))
            if state.get("task_id", task_id) != task_id:
                raise DirectGeneralDemoResolutionError(
                    f"task_set task {task_id!r} public_state task_id does not match"
                )
            state.setdefault("task_id", task_id)
            state_by_task[task_id] = state
        if set(state_by_task) != public_ids:
            raise DirectGeneralDemoResolutionError(
                "task_set must cover exactly the five Tasks Library Demo task IDs"
            )

    tasks: list[DemoTask] = []
    for public, criterion in zip(public_tasks, private_criteria, strict=True):
        task_id = public["task_id"]
        tasks.append(
            DemoTask(
                requirement_id=public["requirement_id"],
                task_id=task_id,
                description=public["description"],
                public_state=state_by_task.get(task_id, {"task_id": task_id}),
                private_criterion=criterion,
            )
        )
    return tuple(tasks)


def _schema_for_public_state(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        properties = {
            str(key): _schema_for_public_state(item)
            for key, item in value.items()
        }
        return {
            "type": "object",
            "properties": properties,
            "required": sorted(properties),
            "additionalProperties": False,
        }
    if isinstance(value, list):
        if not value:
            return {"type": "array"}
        item_schema = _schema_for_public_state(value[0])
        if any(_schema_for_public_state(item) != item_schema for item in value[1:]):
            raise DirectGeneralDemoResolutionError(
                "public task state arrays must have one item shape"
            )
        return {"type": "array", "items": item_schema}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if value is None:
        return {"type": "null"}
    raise DirectGeneralDemoResolutionError(
        "public task state contains an unsupported JSON value"
    )


def _generated_robot_projection(config: DirectMuJoCoLibraryConfig) -> dict[str, Any]:
    frames = list(dict.fromkeys((*config.body_names, *config.site_names)))
    return {
        "robot_model_id": config.robot_model_id,
        "robot_configuration_id": config.robot_configuration_id,
        "execution_route": DIRECT_MUJOCO_EXPERIMENTAL,
        "action_affordances": ["named actuator command"],
        "observation_affordances": [
            "joint state",
            "body state",
            "site state",
            "sensor state",
        ],
        "effect_allowlist": ["change physical state", "observe physical state"],
        "unit_allowlist": [
            "native_mujoco_control",
            "rad",
            "rad/s",
            "m",
            "m/s",
            "s",
            "quaternion",
        ],
        "frame_allowlist": ["joint", "body", "site", "sensor", "world"],
        "actuator_names": list(config.actuator_names),
        "observation_frames": frames,
    }


def _validation_source(
    value: Any,
) -> ValidationAProfile | ValidationAProfileTemplate:
    if isinstance(value, (ValidationAProfile, ValidationAProfileTemplate)):
        return value
    if isinstance(value, Mapping):
        if value.get("artifact_type") == "validation_a_template":
            return _template_from_mapping(value, None)
        members = value.get("sdk_facade_members")
        probes = value.get("fixture_probes")
        if isinstance(members, Mapping) and isinstance(probes, Mapping):
            return ValidationAProfile(
                sdk_facade_members={
                    str(key): tuple(item)
                    for key, item in members.items()
                    if isinstance(item, (list, tuple))
                },
                fixture_probes=copy.deepcopy(dict(probes)),
                profile_id=str(value.get("profile_id", "direct-mujoco-validation-a")),
            )
    raise DirectGeneralDemoResolutionError(
        "validation_a_profile must be a ValidationAProfile or validation_a_template"
    )


def _default_validation_template() -> ValidationAProfileTemplate:
    return ValidationAProfileTemplate(
        profile_id="direct-mujoco-experimental-validation-a-v1",
        facade_members=(
            "actuator_names",
            "joint_names",
            "send_action",
            "state",
            "get_observation",
            "step",
        ),
        input_value_policy={
            "number": 0.0,
            "integer": 0,
            "boolean": False,
            "string": "",
            "object": {},
            "array": [],
        },
    )


def _default_video_profile(
    config: DirectMuJoCoLibraryConfig,
) -> FrozenVideoProfile:
    return FrozenVideoProfile(
        profile_id="direct-mujoco-experimental-general-demo",
        profile_version="1.0.0",
        camera=config.render_camera,
        view="robot-and-task-scene",
        fps=config.render_fps,
        width=config.render_width,
        height=config.render_height,
        container="matroska",
        codec="ffv1",
    )


def _direct_public_sandbox(
    package: Any,
    supplied: CallbackSandbox | None,
) -> CallbackSandbox:
    if supplied is not None:
        return supplied
    provider = create_direct_mujoco_development_sandbox(package.config)

    def callback(source: str, probe: Mapping[str, Any]) -> Mapping[str, Any]:
        raw = provider.run(source, probe)
        observations = raw.get("observations", {}) if isinstance(raw, Mapping) else {}
        safe_fields = (
            "probe_id",
            "capability_id",
            "requested_horizon_s",
            "accepted_action_count",
            "physics_step_count",
            "simulation_time_s",
            "state_changed",
        )
        public_observations = {
            key: copy.deepcopy(observations[key])
            for key in safe_fields
            if isinstance(observations, Mapping) and key in observations
        }
        status = raw.get("status", "ERROR") if isinstance(raw, Mapping) else "ERROR"
        return {
            "status": status if status in {"OK", "ERROR", "INCONCLUSIVE"} else "ERROR",
            "summary": "experimental probe completed",
            "observations": public_observations,
            "exception": None if status != "ERROR" else "experimental_probe_error",
        }

    return CallbackSandbox(
        callback,
        contract={
            "contract_id": "direct-experimental-public-probe",
            "version": "1.0.0",
            "mode": "callback_only",
        },
    )


def _direct_route(
    package: Any,
    task_adapter: DirectTaskAdapter,
    video_profile: FrozenVideoProfile,
    run_id: str,
) -> tuple[EvaluationRoute, dict[str, Any]]:
    no_sdk_record_hash = content_hash(canonical_bytes(dict(package.sdk_record.record)))
    morphology_record_hash = content_hash(canonical_bytes(dict(package.record)))
    runtime_hash = content_hash(canonical_bytes({
        "execution_route": DIRECT_MUJOCO_EXPERIMENTAL,
        "mujoco_version": package.config.mujoco_version,
        "entrypoint": package.config.entrypoint,
        "simulation_entrypoint": package.config.simulation_entrypoint,
        "actuator_names": list(package.config.actuator_names),
        "body_names": list(package.config.body_names),
        "site_names": list(package.config.site_names),
        "sensor_names": list(package.config.sensor_names),
    }))
    simulation_profile_hash = content_hash(canonical_bytes({
        "execution_route": DIRECT_MUJOCO_EXPERIMENTAL,
        "morphology_record_hash": morphology_record_hash,
        "task_adapter_hash": task_adapter.content_hash,
        "video_profile_hash": video_profile.content_hash,
    }))
    route_hash = content_hash(canonical_bytes({
        "execution_route": DIRECT_MUJOCO_EXPERIMENTAL,
        "no_sdk_record_hash": no_sdk_record_hash,
        "morphology_record_hash": morphology_record_hash,
        "task_adapter_hash": task_adapter.content_hash,
        "runtime_hash": runtime_hash,
        "simulation_profile_hash": simulation_profile_hash,
        "asset_cache_root": str(package.asset_cache_root),
    }))
    metadata = {
        "execution_route": DIRECT_MUJOCO_EXPERIMENTAL,
        "sdk_grounded_simulation_claim": False,
        "robot_model_id": package.config.robot_model_id,
        "robot_configuration_id": package.robot_configuration_id,
        "no_sdk_record_id": package.sdk_record.record["id"],
        "no_sdk_record_version": package.sdk_record.record["version"],
        "no_sdk_record_status": package.sdk_record.record["sdk_status"],
        "no_sdk_record_hash": no_sdk_record_hash,
        "no_sdk_record_path": str(package.sdk_record.record_path),
        "morphology_record_path": str(package.record_path),
        "morphology_record_hash": morphology_record_hash,
        "task_adapter_path": str(task_adapter.path),
        "task_adapter_hash": task_adapter.content_hash,
        "asset_cache_root": str(package.asset_cache_root),
        "runtime_hash": runtime_hash,
        "simulation_profile_hash": simulation_profile_hash,
        "route_hash": route_hash,
    }
    return (
        EvaluationRoute(
            run_id=run_id,
            integration_manifest_hash=route_hash,
            sdk_entry_hash=no_sdk_record_hash,
            runtime_hash=runtime_hash,
            simulation_profile_hash=simulation_profile_hash,
        ),
        metadata,
    )


def _stage_configs(
    config: DirectGeneralDemoConfig,
    values: Mapping[str, Any],
) -> tuple[Stage1Config, Stage2Config, RepairConfig, dict[str, Any]]:
    budget_value = values.get("budget")
    if budget_value is not None:
        if not isinstance(budget_value, Mapping):
            raise DirectGeneralDemoResolutionError("budget must be an object")
        try:
            stage1, stage2, repair, consumer = _budgets(budget_value)
        except (ContractError, ValueError) as exc:
            raise DirectGeneralDemoResolutionError(f"invalid direct General Demo budget: {exc}") from exc
    else:
        stage1 = Stage1Config()
        stage2 = Stage2Config()
        repair = RepairConfig()
        consumer = {
            "consumer_id": config.consumer_id,
            "consumer_max_steps": config.consumer_max_steps,
            "consumer_seed": config.consumer_seed,
            "demo_repetitions": config.demo_repetitions,
        }
    return (
        config.stage1_config or stage1,
        config.stage2_config or stage2,
        config.repair_config or repair,
        {
            **consumer,
            "consumer_id": config.consumer_id if config.consumer_id != "react-consumer-experimental" else consumer["consumer_id"],
            "consumer_max_steps": config.consumer_max_steps if config.consumer_max_steps != 4 else consumer["consumer_max_steps"],
            "consumer_seed": config.consumer_seed if config.consumer_seed != 0 else consumer["consumer_seed"],
            "demo_repetitions": config.demo_repetitions if config.demo_repetitions != 1 else consumer["demo_repetitions"],
        },
    )


def _model_adapters(
    config: DirectGeneralDemoConfig,
    package: Any,
    values: Mapping[str, Any],
) -> tuple[DemoModelAdapters, _ModelCallCapture | None]:
    if config.models is not None:
        return config.models, None
    if config.model_client is not None:
        model_client = config.model_client
    else:
        model_prompt_config = values.get("model_prompt_config")
        if not isinstance(model_prompt_config, Mapping):
            raise DirectGeneralDemoResolutionError(
                "a model_client or model_prompt_config is required for the direct General Demo"
            )
        try:
            model_client = _model_client(model_prompt_config)
        except (ContractError, ValueError) as exc:
            raise DirectGeneralDemoResolutionError(
                f"could not construct the direct General Demo model client: {exc}"
            ) from exc
    if not all(
        callable(getattr(model_client, name, None))
        for name in ("generate_json", "repair", "react")
    ):
        raise DirectGeneralDemoResolutionError(
            "direct General Demo model_client must implement generate_json, repair, and react"
        )
    capture = _ModelCallCapture(model_client)
    return (
        DemoModelAdapters(
            stage1=capture,
            blue_line=capture,
            stage2=capture,
            repair=capture.repair,
            consumer=capture.react,
            sandbox=_direct_public_sandbox(package, config.sandbox),
        ),
        capture,
    )


def _direct_output_root(root: Path, value: str | Path | None) -> Path:
    output = (root / "general_demo/runs/direct_general_demo") if value is None else Path(value)
    resolved = output.expanduser().resolve() if output.is_absolute() else (root / output).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DirectGeneralDemoResolutionError(
            "output_root must resolve beneath the project root"
        ) from exc
    return resolved


def _direct_file_reference(root: Path, path: Path) -> dict[str, str]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise DirectGeneralDemoResolutionError(
            f"run artifact is outside the project root: {resolved}"
        ) from exc
    return {"path": relative, "sha256": sha256_bytes(resolved.read_bytes())}


def _direct_run_closure(
    root: Path,
    run_id: str,
    robot_configuration_id: str,
    result: DemoRunResult,
    files: Mapping[str, Mapping[str, str]],
    stage_artifacts_hash: str,
    model_call_log_hash: str,
    closure_path: Path,
    closure_seal_path: Path,
) -> str:
    closure = {
        "artifact_type": "direct_general_demo_run_closure",
        "schema_version": "1.0.0",
        "run_id": run_id,
        "robot_configuration_id": robot_configuration_id,
        "execution_route": DIRECT_MUJOCO_EXPERIMENTAL,
        "sdk_grounded_simulation_claim": False,
        "status": result.status,
        "summary_hash": result.summary_hash,
        "summary_seal": copy.deepcopy(result.summary_seal),
        "files": copy.deepcopy(dict(files)),
    }
    closure_hash = content_hash(canonical_bytes(closure))
    parents = [
        result.summary_hash,
        stage_artifacts_hash,
        model_call_log_hash,
        *(
            f"sha256:{reference['sha256']}"
            for reference in files.values()
        ),
    ]
    closure_seal = create_seal("direct_general_demo_run_closure", closure_hash, parents)
    _write_immutable_json(closure_path, closure)
    _write_immutable_json(closure_seal_path, closure_seal)
    return closure_hash


def run_direct_general_demo(config: DirectGeneralDemoConfig) -> DirectGeneralDemoResult:
    """Run and persist the complete shared General Demo on the direct route."""

    if not isinstance(config, DirectGeneralDemoConfig):
        raise ContractError("run_direct_general_demo requires DirectGeneralDemoConfig")
    root = Path(config.root).expanduser().resolve()
    if not root.is_dir():
        raise DirectGeneralDemoResolutionError(f"project root is not a directory: {root}")
    try:
        package = resolve_morphology_record(
            config.robot_configuration_id,
            config.version,
            root,
            config.asset_cache_root,
        )
    except (DirectGeneralDemoResolutionError, ContractError):
        raise
    except Exception as exc:
        raise DirectGeneralDemoResolutionError(
            f"could not resolve direct morphology package: {exc}"
        ) from exc
    if package.sdk_record.record.get("execution_mode") != NO_SDK_DIRECT_MUJOCO_EXECUTION_MODE:
        raise DirectGeneralDemoResolutionError(
            "the selected direct route does not bind the shared no-SDK execution sentinel"
        )

    task_adapter = load_direct_task_adapter(
        root,
        package.robot_configuration_id,
        config.task_catalog_version,
        config.task_adapter_path,
    )
    values = _resolve_direct_inputs(config, root)
    tasks = _tasks_from_direct_inputs(
        package,
        task_adapter,
        config.run_id,
        values.get("task_set"),
        config.task_catalog_version,
    )
    projection_value = values.get("observation_profile")
    projection = (
        _generated_robot_projection(package.config)
        if projection_value is None
        else _source_object(root, projection_value, "robot_public_projection")
    )
    expected_identity = {
        "robot_model_id": package.config.robot_model_id,
        "robot_configuration_id": package.robot_configuration_id,
    }
    if any(projection.get(key) != value for key, value in expected_identity.items()):
        raise DirectGeneralDemoResolutionError(
            "robot_public_projection does not match the selected morphology record"
        )
    if projection.get("execution_route", DIRECT_MUJOCO_EXPERIMENTAL) != DIRECT_MUJOCO_EXPERIMENTAL:
        raise DirectGeneralDemoResolutionError(
            "robot_public_projection execution_route must be DIRECT_MUJOCO_EXPERIMENTAL"
        )
    projection.setdefault("execution_route", DIRECT_MUJOCO_EXPERIMENTAL)

    g2_profile = values.get("g2_profile", copy.deepcopy(G2_PROFILE))
    if not isinstance(g2_profile, Mapping):
        raise DirectGeneralDemoResolutionError("g2_profile must be an object")
    standards = values.get("standards_snapshot")
    measurements = values.get("measurement_catalog")
    policy = values.get("blue_line_policy")
    implementation_bundle = values.get("implementation_bundle")
    if not all(isinstance(item, Mapping) for item in (standards, measurements, policy)):
        raise DirectGeneralDemoResolutionError(
            "direct General Demo requires standards_snapshot, measurement_catalog, and blue_line_policy"
        )
    if not isinstance(implementation_bundle, Mapping):
        raise DirectGeneralDemoResolutionError(
            "direct General Demo requires a Stage 2 implementation_bundle"
        )

    public_state_schema_value = values.get("public_state_schema")
    public_state_schema = (
        _schema_for_public_state(tasks[0].public_state)
        if public_state_schema_value is None
        else _source_object(root, public_state_schema_value, "public_state_schema")
    )
    validation_source_value = values.get("validation_a_profile")
    validation_source = (
        _default_validation_template()
        if validation_source_value is None
        else _validation_source(validation_source_value)
    )
    if isinstance(validation_source, ValidationAProfileTemplate):
        validation_a_profile = None
        materializer = config.validation_a_materializer or (
            lambda stage1: materialize_validation_a_profile(
                validation_source,
                stage1.capability_design,
                design_seal=stage1.seal,
            )
        )
    else:
        validation_a_profile = validation_source
        materializer = config.validation_a_materializer

    video_profile = config.video_profile or _default_video_profile(package.config)
    route, route_metadata = _direct_route(
        package,
        task_adapter,
        video_profile,
        config.run_id,
    )
    route_metadata["run_id"] = config.run_id
    session = DirectGeneralDemoSession(
        package.config,
        task_adapter,
        video_profile=video_profile,
        session_factory=config.session_factory,
    )
    models, model_capture = _model_adapters(config, package, values)
    stage1_config, stage2_config, repair_config, consumer = _stage_configs(config, values)
    output_root = _direct_output_root(root, config.output_root)
    run_directory = _new_run_directory(
        output_root,
        config.run_id,
        package.robot_configuration_id,
    )
    try:
        video_store = _VideoStore(run_directory, config.run_id, video_profile)
        plan = DemoRunPlan(
            run_id=config.run_id,
            integration_manifest_path=None,
            run_snapshot_path=config.run_snapshot_path,
            readiness_report_path=None,
            robot_public_projection=projection,
            g2_profile=dict(g2_profile),
            tasks=tasks,
            standards_snapshot=dict(standards),
            measurement_catalog=dict(measurements),
            blue_line_policy=dict(policy),
            implementation_bundle=dict(implementation_bundle),
            validation_a_profile=validation_a_profile,
            public_state_schema=public_state_schema,
            validation_harness_config=dict(values.get("validation_harness_config", {})),
            consumer_id=consumer["consumer_id"],
            consumer_max_steps=consumer["consumer_max_steps"],
            consumer_seed=consumer["consumer_seed"],
            demo_repetitions=consumer["demo_repetitions"],
            task_catalog_version=config.task_catalog_version,
            stage1_config=stage1_config,
            stage2_config=stage2_config,
            repair_config=repair_config,
            design_experience_snapshot=values.get("design_experience_snapshot"),
            implementation_experience_snapshot=values.get("implementation_experience_snapshot"),
            execution_route=DIRECT_MUJOCO_EXPERIMENTAL,
            evaluation_route=route,
            route_metadata=route_metadata,
        )
        runner_result = GeneralDemoRunner(
            root,
            models,
            session,
            video_profile,
            video_store.encoder,
            config.criterion_evaluator or evaluate_fixed_demo_criterion,
            validation_a_materializer=materializer,
        ).run(plan)
    finally:
        session.close()

    validation_references = video_store.persist(
        "VALIDATION_B", runner_result.validation_video_handles
    )
    demo_videos = tuple(
        trial.video_handle
        for trial in runner_result.demo_trials
        if trial.video_handle is not None
    )
    demo_references = video_store.persist("DEMO", demo_videos)
    summary_path = run_directory / "summary.json"
    seal_path = run_directory / "summary.seal.json"
    validation_path = run_directory / "validation_video_references.json"
    demo_path = run_directory / "demo_video_references.json"
    stage_artifacts_path = run_directory / "stage_artifacts.json"
    stage_artifacts_seal_path = run_directory / "stage_artifacts.seal.json"
    model_call_log_path = run_directory / "model_call_log.json"
    closure_path = run_directory / "run_closure.json"
    closure_seal_path = run_directory / "run_closure.seal.json"
    summary_sha256 = write_stable_json(summary_path, runner_result.summary)
    if summary_sha256 != runner_result.summary_hash.removeprefix("sha256:"):
        raise ContractError("persisted direct General Demo summary hash does not match the runner result")
    write_stable_json(seal_path, runner_result.summary_seal)
    write_stable_json(
        validation_path,
        _video_reference_artifact(config.run_id, package.robot_configuration_id, validation_references),
    )
    write_stable_json(
        demo_path,
        _video_reference_artifact(config.run_id, package.robot_configuration_id, demo_references),
    )
    stage_artifacts = copy.deepcopy(runner_result.artifacts)
    stage_artifacts.update({
        "artifact_type": "direct_general_demo_stage_artifacts",
        "schema_version": "1.0.0",
        "run_id": config.run_id,
        "robot_configuration_id": package.robot_configuration_id,
        "execution_route": DIRECT_MUJOCO_EXPERIMENTAL,
        "sdk_grounded_simulation_claim": False,
        "route_metadata": copy.deepcopy(route_metadata),
        "summary": copy.deepcopy(runner_result.summary),
        "summary_hash": runner_result.summary_hash,
        "summary_seal": copy.deepcopy(runner_result.summary_seal),
        "orchestration_call_log": copy.deepcopy(model_capture.records if model_capture else []),
    })
    stage_artifacts_sha256 = _write_immutable_json(stage_artifacts_path, stage_artifacts)
    stage_artifacts_hash = f"sha256:{stage_artifacts_sha256}"
    stage_artifacts_seal = create_seal(
        "direct_general_demo_stage_artifacts",
        stage_artifacts_hash,
        [runner_result.summary_hash],
    )
    _write_immutable_json(stage_artifacts_seal_path, stage_artifacts_seal)
    provider_calls = getattr(config.model_client, "calls", []) if config.model_client else []
    if not isinstance(provider_calls, list):
        provider_calls = []
    model_call_log = {
        "artifact_type": "direct_general_demo_model_call_log",
        "schema_version": "1.0.0",
        "run_id": config.run_id,
        "robot_configuration_id": package.robot_configuration_id,
        "calls": copy.deepcopy(provider_calls),
        "orchestration_calls": copy.deepcopy(model_capture.records if model_capture else []),
    }
    model_call_log_sha256 = _write_immutable_json(model_call_log_path, model_call_log)
    files = {
        "summary": _direct_file_reference(root, summary_path),
        "summary_seal": _direct_file_reference(root, seal_path),
        "validation_video_references": _direct_file_reference(root, validation_path),
        "demo_video_references": _direct_file_reference(root, demo_path),
        "stage_artifacts": _direct_file_reference(root, stage_artifacts_path),
        "stage_artifacts_seal": _direct_file_reference(root, stage_artifacts_seal_path),
        "model_call_log": _direct_file_reference(root, model_call_log_path),
    }
    closure_hash = _direct_run_closure(
        root,
        config.run_id,
        package.robot_configuration_id,
        runner_result,
        files,
        stage_artifacts_hash,
        f"sha256:{model_call_log_sha256}",
        closure_path,
        closure_seal_path,
    )
    return DirectGeneralDemoResult(
        robot_configuration_id=package.robot_configuration_id,
        run_id=config.run_id,
        status=runner_result.status,
        run_directory=run_directory,
        summary_path=summary_path,
        seal_path=seal_path,
        validation_video_references_path=validation_path,
        demo_video_references_path=demo_path,
        stage_artifacts_path=stage_artifacts_path,
        stage_artifacts_seal_path=stage_artifacts_seal_path,
        model_call_log_path=model_call_log_path,
        run_closure_path=closure_path,
        run_closure_seal_path=closure_seal_path,
        summary_hash=runner_result.summary_hash,
        closure_hash=closure_hash,
        runner_result=runner_result,
    )


__all__ = [
    "DIRECT_MUJOCO_EXPERIMENTAL",
    "DirectGeneralDemoConfig",
    "DirectGeneralDemoResolutionError",
    "DirectGeneralDemoResult",
    "DirectGeneralDemoSession",
    "DirectTaskAdapter",
    "load_direct_mujoco_task_adapter",
    "load_direct_task_adapter",
    "run_direct_general_demo",
]
