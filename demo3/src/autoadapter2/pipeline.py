"""Top-level Demo3 Direct-MuJoCo experiment orchestration.

This module is intentionally a small composition layer.  Stage modules own their
contracts; the pipeline only orders them, gives each condition its own workspace,
keeps the private suite out of candidate-facing inputs, and records the separate
execution and physical-verdict facts required by the Authority.
"""

from __future__ import annotations

import copy
import importlib.util
import inspect
import json
import shutil
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoadapter2 import __version__
from autoadapter2.capability_design import run_tgcd, write_capability_design
from autoadapter2.driver_synthesis.generation import (
    DriverSourceAuditError,
    GenerationCondition,
    GenerationResult,
    StudyResult,
    build_public_generation_inputs,
    generate,
    study,
)
from autoadapter2.driver_synthesis.probe import ProbeBudget, ProbeError, run_probes
from autoadapter2.driver_synthesis.repair import (
    MAX_TOTAL_ATTEMPTS,
    RepairError,
    repair_with_probes,
)
from autoadapter2.driver_synthesis.source_check import (
    DriverSourceError,
    audit_driver_source,
)
from autoadapter2.environment import check_environment
from autoadapter2.evolution import run_evolution
from autoadapter2.harness.runner import run_private_suite
from autoadapter2.libraries import (
    RobotPackage,
    RobotPackageError,
    load_indexed_robot_package,
)
from autoadapter2.reporting import (
    build_cell_report,
    build_paired_report,
    write_json,
)
from autoadapter2.self_containment import check_self_contained
from autoadapter2.validation_compiler import run_ivc, write_private_suite


DEFAULT_ROBOTS = ("robotstudio_so101", "unitree-go2-stock-12dof")
DEFAULT_CONDITIONS: tuple[GenerationCondition, ...] = (
    "skeleton-assisted",
    "from-scratch",
)


class PipelineError(RuntimeError):
    """Raised when the experiment cannot be admitted or composed."""


@dataclass(frozen=True)
class ExperimentConfig:
    """The small run configuration read from ``demo3/experiment.json``."""

    experiment_id: str
    robots: tuple[str, ...] = DEFAULT_ROBOTS
    generation_conditions: tuple[GenerationCondition, ...] = DEFAULT_CONDITIONS
    max_driver_attempts_per_condition: int = MAX_TOTAL_ATTEMPTS
    probe_budget: ProbeBudget = ProbeBudget()
    record_video: bool = True
    worker_wall_timeout_s: float = 120.0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExperimentConfig":
        if not isinstance(value, Mapping):
            raise PipelineError("experiment configuration must be one JSON object")

        experiment_id = value.get("experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise PipelineError("experiment_id must be a non-empty string")

        robots_value = value.get("robots", list(DEFAULT_ROBOTS))
        conditions_value = value.get(
            "generation_conditions", list(DEFAULT_CONDITIONS)
        )
        if not isinstance(robots_value, list) or len(robots_value) != 2:
            raise PipelineError("the mainline configuration must name exactly two robots")
        if not all(isinstance(item, str) and item.strip() for item in robots_value):
            raise PipelineError("robots must contain non-empty strings")
        robots = tuple(str(item).strip() for item in robots_value)
        if len(set(robots)) != len(robots):
            raise PipelineError("robots must be distinct")

        if not isinstance(conditions_value, list) or len(conditions_value) != 2:
            raise PipelineError(
                "the mainline configuration must contain skeleton-assisted and from-scratch"
            )
        conditions = tuple(str(item).strip() for item in conditions_value)
        if set(conditions) != set(DEFAULT_CONDITIONS):
            raise PipelineError(
                "generation_conditions must be exactly skeleton-assisted and from-scratch"
            )

        max_attempts = value.get(
            "max_driver_attempts_per_condition", MAX_TOTAL_ATTEMPTS
        )
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= MAX_TOTAL_ATTEMPTS
        ):
            raise PipelineError("max_driver_attempts_per_condition must be between 1 and 3")

        development = value.get("development_probe", {})
        if not isinstance(development, Mapping):
            raise PipelineError("development_probe must be an object")
        try:
            probe_budget = ProbeBudget(
                max_requests=int(development.get("max_requests_per_stage", 12)),
                timeout_s=float(development.get("wall_timeout_s_per_request", 30)),
                max_output_chars=int(
                    development.get("max_output_chars_per_request", 12000)
                ),
            )
        except (TypeError, ValueError) as exc:
            raise PipelineError("invalid development_probe budget") from exc

        validation = value.get("validation", {})
        if not isinstance(validation, Mapping):
            raise PipelineError("validation must be an object")
        record_video = validation.get("record_video", True)
        worker_timeout = validation.get("worker_wall_timeout_s", 120)
        if not isinstance(record_video, bool):
            raise PipelineError("validation.record_video must be boolean")
        try:
            worker_timeout_value = float(worker_timeout)
        except (TypeError, ValueError) as exc:
            raise PipelineError("validation.worker_wall_timeout_s must be positive") from exc
        if worker_timeout_value <= 0:
            raise PipelineError("validation.worker_wall_timeout_s must be positive")

        return cls(
            experiment_id=experiment_id.strip(),
            robots=robots,
            generation_conditions=conditions,  # type: ignore[arg-type]
            max_driver_attempts_per_condition=max_attempts,
            probe_budget=probe_budget,
            record_video=record_video,
            worker_wall_timeout_s=worker_timeout_value,
        )

    @classmethod
    def from_path(cls, path: str | Path) -> "ExperimentConfig":
        source = Path(path)
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PipelineError(f"cannot read experiment configuration {source}") from exc
        if not isinstance(value, Mapping):
            raise PipelineError("experiment configuration must be one JSON object")
        return cls.from_mapping(value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "robots": list(self.robots),
            "generation_conditions": list(self.generation_conditions),
            "max_driver_attempts_per_condition": self.max_driver_attempts_per_condition,
            "development_probe": {
                "max_requests_per_stage": self.probe_budget.max_requests,
                "wall_timeout_s_per_request": self.probe_budget.timeout_s,
                "max_output_chars_per_request": self.probe_budget.max_output_chars,
            },
            "validation": {
                "record_video": self.record_video,
                "worker_wall_timeout_s": self.worker_wall_timeout_s,
            },
        }


@dataclass(frozen=True)
class PipelineHooks:
    """Optional seams used by focused tests and small local experiments.

    The default hooks are the real Demo3 implementations.  Test-only callers may
    replace them with explicit fake package/model/Harness functions without changing
    the dynamic production path.
    """

    package_loader: Callable[..., RobotPackage] = load_indexed_robot_package
    tgcd_runner: Callable[..., Mapping[str, Any]] = run_tgcd
    ivc_runner: Callable[..., Mapping[str, Any]] = run_ivc
    study_runner: Callable[..., StudyResult] = study
    probe_runner: Callable[..., Sequence[Mapping[str, Any]]] = run_probes
    generate_runner: Callable[..., GenerationResult] = generate
    repair_runner: Callable[..., Any] = repair_with_probes
    harness_runner: Callable[..., Mapping[str, Any]] = run_private_suite
    reference_renderer: Callable[..., Any] | None = None
    reference_runner: Callable[..., Mapping[str, Any]] | None = None
    evolution_runner: Callable[..., Mapping[str, Any]] = run_evolution


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _write(path: Path, value: Mapping[str, Any]) -> None:
    write_json(path, _json_safe(value))


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _client_identity(client: Any, explicit: Mapping[str, Any] | None) -> dict[str, str]:
    config = getattr(client, "config", None)
    provider = (
        explicit.get("provider")
        if explicit is not None and isinstance(explicit.get("provider"), str)
        else getattr(config, "provider", None) or getattr(client, "provider", None)
    )
    model = (
        explicit.get("model")
        if explicit is not None and isinstance(explicit.get("model"), str)
        else getattr(config, "model", None) or getattr(client, "model", None)
    )
    return {
        "provider": str(provider or "unknown"),
        "model": str(model or "unknown"),
    }


def _client_calls(client: Any) -> list[dict[str, Any]] | None:
    calls = getattr(client, "calls", None)
    if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
        return None
    return [
        _copy(dict(item))
        for item in calls
        if isinstance(item, Mapping)
    ]


def _stage_evidence(
    client: Any,
    *,
    stage: str,
    before: int | None,
    completed: bool,
    error: BaseException | None = None,
) -> dict[str, Any]:
    after_calls = _client_calls(client)
    after = len(after_calls) if after_calls is not None else None
    if before is None or after is None:
        observed = 1 if completed or error is not None else 0
        records: list[dict[str, Any]] = []
    else:
        observed = max(0, after - before)
        records = after_calls[before:]
    result: dict[str, Any] = {
        "stage": stage,
        "attempted": True,
        "completed": completed,
        "model_call_count": observed,
        "model_calls": records,
    }
    if error is not None:
        result["error"] = {
            "type": type(error).__name__,
            "message": str(error)[:1000],
        }
    return result


def _call_count(client: Any) -> int | None:
    calls = _client_calls(client)
    return len(calls) if calls is not None else None


def _public_experience(
    experience: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
    robot: str,
) -> tuple[Mapping[str, Any], ...]:
    if experience is None:
        return ()
    if isinstance(experience, Mapping):
        value = experience.get(robot, ())
    else:
        value = experience
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise PipelineError(f"experience for {robot!r} must be a list")
    allowed_fields = {
        "experience_id",
        "reviewed",
        "observation",
        "lesson",
        "recommendation",
        "scope",
        "evidence",
        "source_run_id",
    }
    records: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise PipelineError(f"experience for {robot!r}[{index}] must be an object")
        unexpected = sorted(str(key) for key in item if str(key) not in allowed_fields)
        if unexpected:
            raise PipelineError(
                f"experience for {robot!r}[{index}] contains unreviewed fields: "
                + ", ".join(unexpected)
            )
        if item.get("reviewed") is not True:
            raise PipelineError(f"experience for {robot!r}[{index}] is not reviewed")
        for field in ("observation", "lesson", "recommendation", "scope"):
            child = item.get(field)
            if child is not None and (not isinstance(child, str) or not child.strip()):
                raise PipelineError(
                    f"experience for {robot!r}[{index}].{field} must be non-empty text"
                )
        evidence = item.get("evidence", [])
        if not isinstance(evidence, list) or not all(
            isinstance(child, str) and child.strip() for child in evidence
        ):
            raise PipelineError(
                f"experience for {robot!r}[{index}].evidence must be a text list"
            )
        records.append(_copy(dict(item)))
    return tuple(records)


def _runtime_contract(package: RobotPackage) -> dict[str, Any]:
    morphology = package.morphology
    configured = morphology.get("from_scratch_runtime")
    if isinstance(configured, Mapping):
        return _copy(dict(configured))
    if isinstance(configured, list):
        return {"primitives": _copy(configured)}
    configured = morphology.get("allowed_runtime_primitives")
    if isinstance(configured, list):
        return {"primitives": _copy(configured)}
    return {
        "primitives": [
            "mujoco.mj_name2id",
            "mujoco.mj_jacSite",
            "mujoco.mj_step",
            "numpy.asarray",
            "numpy.clip",
        ]
    }


def _asset_path_inside(destination: Path, candidate: Path) -> Path:
    try:
        return candidate.resolve().relative_to(destination.resolve())
    except ValueError as exc:
        raise PipelineError(
            f"reference renderer returned a path outside its Framework workspace: {candidate}"
        ) from exc


def _load_reference_renderer(package: RobotPackage) -> Callable[..., Any] | None:
    package_renderer = getattr(package, "render_reference_driver", None)
    if callable(package_renderer):
        return package_renderer

    package_root = getattr(package, "root", None)
    reference_driver = getattr(package, "reference_driver", None)
    candidates: list[Path] = []
    if isinstance(package_root, Path):
        candidates.append(package_root / "rendering.py")
        candidates.append(package_root / "reference" / "rendering.py")
        candidates.append(package_root / "reference" / "render_reference_driver.py")
    if isinstance(reference_driver, Path):
        candidates.append(reference_driver)
    for source in candidates:
        if not source.is_file():
            continue
        module_name = "_autoadapter2_reference_" + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        renderer = getattr(module, "render_reference_driver", None)
        if callable(renderer):
            return renderer
    return None


def _invoke_renderer(
    renderer: Callable[..., Any],
    design: Mapping[str, Any],
    destination: Path,
) -> Any:
    """Call either the documented positional or keyword-only package helper."""

    try:
        inspect.signature(renderer).bind(design, destination)
    except (TypeError, ValueError):
        return renderer(design=design, destination=destination)
    return renderer(design, destination)


def _materialize_reference_result(result: Any, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "driver.py"
    if isinstance(result, Mapping):
        source = result.get("driver_source")
        path = result.get("driver_path")
        if isinstance(source, str) and source.strip():
            output.write_text(source, encoding="utf-8")
            return output
        result = path
    if result is None:
        if output.is_file():
            return output
        raise PipelineError(
            "reference renderer returned no driver and did not create destination/driver.py"
        )
    if isinstance(result, Path):
        source_path = result
    elif isinstance(result, str):
        if "\n" in result or "\r" in result:
            output.write_text(result, encoding="utf-8")
            return output
        source_path = Path(result)
    else:
        raise PipelineError("reference renderer must return driver source, path, or null")
    if not source_path.is_absolute():
        source_path = destination / source_path
    if not source_path.is_file():
        raise PipelineError(f"reference renderer returned missing driver {source_path}")
    if source_path.resolve() != output.resolve():
        shutil.copyfile(source_path, output)
    _asset_path_inside(destination, output)
    return output


def render_reference_driver(
    package: RobotPackage,
    design: Mapping[str, Any],
    destination: str | Path,
    *,
    renderer: Callable[..., Any] | None = None,
) -> Path:
    """Render a package-local reference for arbitrary TGCD method names.

    A package may expose ``render_reference_driver(design, destination)`` on its
    reference module.  The callable is required because a fixed reference source
    cannot know model-authored public method names.  Its output is copied into the
    Framework-owned calibration workspace and never enters dynamic generation inputs.
    """

    destination_path = Path(destination).resolve()
    selected = renderer or _load_reference_renderer(package)
    if selected is None:
        raise PipelineError(
            f"robot package {package.robot_configuration_id!r} lacks the required "
            "package-local render_reference_driver(design, destination) helper"
        )
    destination_path.mkdir(parents=True, exist_ok=True)
    result = _invoke_renderer(selected, _copy(dict(design)), destination_path)
    return _materialize_reference_result(result, destination_path)


def _reference_condition(driver_path: Path, methods: Sequence[str]) -> str:
    source = driver_path.read_text(encoding="utf-8")
    failures: list[str] = []
    for condition in DEFAULT_CONDITIONS:
        try:
            audit_driver_source(
                source,
                condition=condition,
                capability_methods=tuple(methods),
            )
        except DriverSourceError as exc:
            failures.append(f"{condition}: {exc}")
        else:
            return condition
    raise PipelineError(
        "rendered reference driver does not satisfy the trusted driver source contract: "
        + " | ".join(failures)
    )


def _default_reference_run(
    *,
    package: RobotPackage,
    design: Mapping[str, Any],
    suite: Mapping[str, Any],
    driver_path: Path,
    output_dir: Path,
    config: ExperimentConfig,
    run_id: str,
) -> Mapping[str, Any]:
    methods = tuple(
        str(capability["method_name"])
        for capability in design.get("capabilities", [])
        if isinstance(capability, Mapping)
    )
    condition = _reference_condition(driver_path, methods)
    return run_private_suite(
        package=package,
        design=_copy(dict(design)),
        suite=_copy(dict(suite)),
        driver_path=driver_path,
        condition=condition,
        output_dir=output_dir,
        record_video=config.record_video,
        wall_timeout_s=config.worker_wall_timeout_s,
        run_id=run_id,
        attempt=0,
    )


def _reference_passed(report: Mapping[str, Any], *, video_required: bool) -> bool:
    if not bool(report.get("validation_passed")):
        return False
    if not bool(report.get("physical_validation_executed")):
        return False
    if video_required and not bool(report.get("video_complete")):
        return False
    return True


def _normalise_validation_report(
    report: Mapping[str, Any],
    *,
    robot: str,
    condition: str,
    attempt: int,
    record_video: bool,
) -> dict[str, Any]:
    result = _copy(dict(report))
    result.setdefault("robot_configuration_id", robot)
    result.setdefault("condition", condition)
    result.setdefault("attempt", attempt)
    result.setdefault("pipeline_completed", False)
    result.setdefault("physical_validation_executed", False)
    result.setdefault("validation_passed", False)
    result.setdefault("video_complete", not record_video)
    result.setdefault("video_required", record_video)
    result.setdefault("trials", [])
    # A Harness verdict is physical only when execution and required media are both
    # present.  Keep the worker's raw fields above, but never let a bare success
    # flag become the cell's final physical verdict.
    result["validation_passed"] = bool(result["validation_passed"]) and bool(
        result["physical_validation_executed"]
    ) and (not record_video or bool(result["video_complete"]))
    return result


def _failure_record(error: BaseException) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)[:2000]}


def _has_successful_physics_probe(results: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        result.get("exit_code") == 0
        and result.get("timed_out") is False
        and result.get("spawn_error") is None
        and isinstance(result.get("physics_steps"), int)
        and int(result["physics_steps"]) > 0
        for result in results
    )


def _canonical_liveness_probe() -> tuple[dict[str, str], ...]:
    return (
        {
            "probe_id": "framework-canonical-liveness",
            "script": (
                "import os\n"
                "import mujoco\n"
                "model = mujoco.MjModel.from_xml_path("
                "os.environ['AUTOADAPTER_PROBE_SCENE'])\n"
                "data = mujoco.MjData(model)\n"
                "if model.nu:\n"
                "    data.ctrl[:] = 0.0\n"
                "mujoco.mj_step(model, data)\n"
                "print('canonical_liveness_time_s=' + str(data.time))\n"
            ),
        },
    )


def _run_cell(
    *,
    package: RobotPackage,
    design: Mapping[str, Any],
    suite: Mapping[str, Any],
    robot: str,
    condition: GenerationCondition,
    config: ExperimentConfig,
    client: Any,
    identity: Mapping[str, str],
    experience: Sequence[Mapping[str, Any]],
    workspace: Path,
    run_id: str,
    hooks: PipelineHooks,
    model_stage_log: list[dict[str, Any]],
) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_contract = _runtime_contract(package)
    public_design = _copy(dict(design))
    public_suite = _copy(dict(suite))
    attempts: list[dict[str, Any]] = []
    development_rejections: list[dict[str, Any]] = []
    driver_generated = False
    dynamic_model_called = False
    initial_pass: bool | None = None
    terminal_validation: dict[str, Any] | None = None
    current_driver: Path | None = None
    current_source: str | None = None
    study_result: StudyResult | None = None
    probe_results: tuple[Mapping[str, Any], ...] = ()
    failure: dict[str, Any] | None = None

    try:
        before = _call_count(client)
        study_result = hooks.study_runner(
            client,
            package,
            public_design,
            condition=condition,
            experience=experience,
            runtime_contract=runtime_contract,
            workspace=workspace,
            probe_budget=config.probe_budget,
            source_root=_default_root() / "src",
        )
        evidence = _stage_evidence(client, stage="study", before=before, completed=True)
        model_stage_log.append({"robot": robot, "condition": condition, **evidence})
        _write(
            workspace / "study.json",
            {
                "output": study_result.output,
                "evidence": evidence,
                "model_conversation": study_result.call_evidence,
                "probe_results": list(getattr(study_result, "probe_results", ())),
            },
        )
        dynamic_model_called = True
    except Exception as exc:
        evidence = _stage_evidence(
            client,
            stage="study",
            before=locals().get("before"),
            completed=False,
            error=exc,
        )
        model_stage_log.append({"robot": robot, "condition": condition, **evidence})
        dynamic_model_called = True
        failure = {"stage": "study", **_failure_record(exc)}

    if study_result is not None:
        try:
            try:
                if not study_result.probe_requests:
                    raise ProbeError(
                        "STUDY returned no real local MuJoCo development probe"
                    )
                in_conversation = getattr(study_result, "probe_results", ())
                if in_conversation:
                    probe_results = tuple(
                        _copy(dict(item))
                        for item in in_conversation
                        if isinstance(item, Mapping)
                    )
                else:
                    probe_value = hooks.probe_runner(
                        study_result.probe_requests,
                        package=package,
                        workspace=workspace / "probe",
                        condition=condition,
                        budget=config.probe_budget,
                        source_root=_default_root() / "src",
                    )
                    probe_results = tuple(_copy(dict(item)) for item in probe_value)
                    if not _has_successful_physics_probe(probe_results):
                        fallback_value = hooks.probe_runner(
                            _canonical_liveness_probe(),
                            package=package,
                            workspace=workspace / "probe-fallback",
                            condition=condition,
                            budget=config.probe_budget,
                            source_root=_default_root() / "src",
                        )
                        fallback_results = tuple(
                            {
                                **_copy(dict(item)),
                                "framework_canonical_liveness": True,
                            }
                            for item in fallback_value
                        )
                        probe_results = (*probe_results, *fallback_results)
                if not _has_successful_physics_probe(probe_results):
                    raise ProbeError(
                        "STUDY produced no successful probe with real MuJoCo physics steps"
                    )
            except ProbeError as exc:
                probe_results = (
                    *probe_results,
                    {"probe_error": _failure_record(exc)},
                )
                failure = {"stage": "probe", **_failure_record(exc)}
            _write(workspace / "probe_results.json", {"results": list(probe_results)})
        except Exception as exc:
            failure = {"stage": "probe", **_failure_record(exc)}
            _write(workspace / "probe_results.json", {"error": failure})

    if study_result is not None and failure is None:
        for attempt in range(config.max_driver_attempts_per_condition):
            attempt_dir = workspace / f"attempt-{attempt}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            generation_evidence: dict[str, Any] | None = None
            repair_evidence: dict[str, Any] | None = None
            try:
                if attempt == 0:
                    before = _call_count(client)
                    generated = hooks.generate_runner(
                        client,
                        package,
                        public_design,
                        study_result,
                        condition=condition,
                        workspace=attempt_dir,
                        probe_results=probe_results,
                        experience=experience,
                        runtime_contract=runtime_contract,
                        probe_budget=config.probe_budget,
                        source_root=_default_root() / "src",
                    )
                    generation_evidence = _stage_evidence(
                        client,
                        stage="generate",
                        before=before,
                        completed=True,
                    )
                    model_stage_log.append(
                        {"robot": robot, "condition": condition, **generation_evidence}
                    )
                    driver_generated = True
                else:
                    if current_source is None:
                        raise RepairError("cannot repair without the previous driver source")
                    public_inputs = build_public_generation_inputs(
                        package,
                        public_design,
                        condition=condition,
                        experience=experience,
                        runtime_contract=runtime_contract,
                        study_output=study_result.output,
                        probe_results=probe_results,
                    )
                    before = _call_count(client)
                    repair_kwargs: dict[str, Any] = {
                        "previous_driver_source": current_source,
                        "candidate_report": attempts[-1]["validation"],
                        "media_manifest": attempts[-1]["validation"].get(
                            "video_manifest", []
                        ),
                        "public_inputs": public_inputs,
                        "condition": condition,
                        "previous_attempt": attempt - 1,
                        "workspace": attempt_dir,
                        "max_total_attempts": config.max_driver_attempts_per_condition,
                        "capability_methods": tuple(
                            str(capability["method_name"])
                            for capability in public_design["capabilities"]
                        ),
                    }
                    if hooks.repair_runner is repair_with_probes:
                        repair_kwargs.update(
                            {
                                "package": package,
                                "probe_budget": config.probe_budget,
                                "source_root": _default_root() / "src",
                            }
                        )
                    repaired = hooks.repair_runner(client, **repair_kwargs)
                    repair_evidence = _stage_evidence(
                        client,
                        stage="repair",
                        before=before,
                        completed=True,
                    )
                    repair_probe_results = [
                        _copy(dict(item))
                        for item in getattr(repaired, "probe_results", ())
                        if isinstance(item, Mapping)
                    ]
                    repair_evidence["probe_attempted"] = bool(repair_probe_results)
                    repair_evidence["probe_results"] = repair_probe_results
                    model_stage_log.append(
                        {"robot": robot, "condition": condition, **repair_evidence}
                    )
                    generated = repaired
                    driver_generated = True

                current_driver = Path(generated.driver_path).resolve()
                current_source = str(generated.driver_source)
                _write(
                    attempt_dir / "generation_evidence.json",
                    {
                        "attempt": attempt,
                        "driver_filename": "driver.py",
                        "source_audit": getattr(generated, "source_audit", None),
                        "model_output": getattr(generated, "output", {}),
                        "evidence": generation_evidence or repair_evidence,
                        "model_conversation": getattr(
                            generated, "call_evidence", None
                        ),
                        "development_probe_results": list(
                            getattr(generated, "probe_results", ())
                        ),
                        "formal_attempt_submitted": True,
                    },
                )
                failure = None
            except DriverSourceAuditError as exc:
                stage = "generate" if attempt == 0 else "repair"
                evidence = _stage_evidence(
                    client,
                    stage=stage,
                    before=before,
                    completed=False,
                    error=exc,
                )
                model_stage_log.append(
                    {"robot": robot, "condition": condition, **evidence}
                )
                current_driver = None
                current_source = exc.driver_source
                driver_generated = True
                failure = {"stage": stage, **_failure_record(exc)}
                rejection = {
                    "stage": stage,
                    "formal_attempt_submitted": False,
                    "source_audit_passed": False,
                    "failure": failure,
                    "evidence": evidence,
                }
                development_rejections.append(rejection)
                terminal_validation = _normalise_validation_report(
                    {
                        "pipeline_completed": False,
                        "physical_validation_executed": False,
                        "validation_passed": False,
                        "video_complete": not config.record_video,
                        "source_audit_passed": False,
                        "pre_harness_rejection": True,
                        "failure": failure,
                        "trials": [],
                        "video_manifest": [],
                    },
                    robot=robot,
                    condition=condition,
                    attempt=attempt,
                    record_video=config.record_video,
                )
                _write(
                    attempt_dir / "generation_evidence.json",
                    {
                        "attempt": attempt,
                        "driver_filename": "driver.py",
                        "source_audit": {
                            "passed": False,
                            "error": _failure_record(exc),
                        },
                        "model_output": exc.model_output,
                        "evidence": evidence,
                        "formal_attempt_submitted": False,
                    },
                )
                _write(attempt_dir / "generation_error.json", failure)
                break
            except Exception as exc:
                failure = {"stage": "generate" if attempt == 0 else "repair", **_failure_record(exc)}
                _write(attempt_dir / "generation_error.json", failure)
                break

            if current_driver is None:
                failure = {
                    "stage": "generate",
                    "type": "PipelineError",
                    "message": "generation returned no driver path",
                }
                break

            try:
                validation_raw = hooks.harness_runner(
                    package=package,
                    design=_copy(public_design),
                    suite=_copy(public_suite),
                    driver_path=current_driver,
                    condition=condition,
                    output_dir=attempt_dir / "validation",
                    record_video=config.record_video,
                    wall_timeout_s=config.worker_wall_timeout_s,
                    run_id=run_id,
                    attempt=attempt,
                )
                validation = _normalise_validation_report(
                    validation_raw,
                    robot=robot,
                    condition=condition,
                    attempt=attempt,
                    record_video=config.record_video,
                )
            except Exception as exc:
                validation = _normalise_validation_report(
                    {
                        "pipeline_completed": False,
                        "physical_validation_executed": False,
                        "validation_passed": False,
                        "video_complete": not config.record_video,
                        "failure": _failure_record(exc),
                    },
                    robot=robot,
                    condition=condition,
                    attempt=attempt,
                    record_video=config.record_video,
                )
            terminal_validation = validation
            if initial_pass is None:
                initial_pass = bool(validation.get("validation_passed"))
            attempt_record: dict[str, Any] = {
                "attempt": attempt,
                "driver_generated": True,
                "validation": validation,
            }
            if generation_evidence is not None:
                attempt_record["generate"] = generation_evidence
            if repair_evidence is not None:
                attempt_record["repair"] = repair_evidence
            attempts.append(attempt_record)
            _write(attempt_dir / "candidate_report.json", validation)
            if bool(validation.get("validation_passed")):
                break

    if terminal_validation is None:
        terminal_validation = {
            "pipeline_completed": False,
            "physical_validation_executed": False,
            "validation_passed": False,
            "video_complete": not config.record_video,
            "trials": [],
        }

    final_pass = bool(terminal_validation.get("validation_passed"))
    capabilities = [
        capability
        for capability in public_design.get("capabilities", [])
        if isinstance(capability, Mapping)
    ]
    raw_report: dict[str, Any] = {
        "cell_id": f"{robot}::{condition}",
        "code_version": __version__,
        "robot_configuration_id": robot,
        "robot_package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
        "admitted_task_count": len(package.tasks),
        "designed_capability_count": len(capabilities),
        "covered_task_count": len(
            {
                str(task_id)
                for capability in capabilities
                for task_id in capability.get("covered_task_ids", [])
            }
        ),
        "condition": condition,
        "provider": identity["provider"],
        "model": identity["model"],
        "pipeline_completed": bool(terminal_validation.get("pipeline_completed")),
        "dynamic_model_called": dynamic_model_called,
        "driver_generated_in_run": driver_generated,
        "physical_validation_executed": bool(
            terminal_validation.get("physical_validation_executed")
        ),
        "initial_validation_passed": bool(initial_pass),
        "final_validation_passed": final_pass,
        "video_required": config.record_video,
        "video_complete": bool(terminal_validation.get("video_complete")),
        "attempts": attempts,
        "development_rejections": development_rejections,
        "development_probe": {
            "attempted": bool(study_result and study_result.probe_requests),
            "successful_physics_probe": _has_successful_physics_probe(probe_results),
            "results": list(probe_results),
        },
        "trials": terminal_validation.get("trials", []),
        "video_manifest": terminal_validation.get("video_manifest", []),
        "failure": failure,
        "outcomes": {
            "TGCD": next(
                (
                    item
                    for item in reversed(model_stage_log)
                    if item.get("stage") == "tgcd" and item.get("robot") == robot
                ),
                None,
            ),
            "IVC": next(
                (
                    item
                    for item in reversed(model_stage_log)
                    if item.get("stage") == "ivc" and item.get("robot") == robot
                ),
                None,
            ),
            "STUDY": next(
                (item for item in reversed(model_stage_log) if item.get("stage") == "study" and item.get("robot") == robot and item.get("condition") == condition),
                None,
            ),
            "GENERATE": next(
                (item for item in reversed(model_stage_log) if item.get("stage") == "generate" and item.get("robot") == robot and item.get("condition") == condition),
                None,
            ),
            "Validation": _copy(terminal_validation),
            "Repair": [
                item
                for item in model_stage_log
                if item.get("stage") == "repair"
                and item.get("robot") == robot
                and item.get("condition") == condition
            ],
        },
    }

    try:
        evolution = hooks.evolution_runner(client, raw_report)
        if not isinstance(evolution, Mapping):
            raise PipelineError("Evolution result must be an object")
        raw_report["evolution"] = _copy(dict(evolution))
    except Exception as exc:
        raw_report["evolution"] = {
            "non_blocking": True,
            "evolution_attempted": True,
            "evolution_completed": False,
            "proposal_created": False,
            "current_run_unchanged": True,
            "failure": _failure_record(exc),
        }
    _write(workspace / "cell_report.json", raw_report)
    return raw_report


def load_experiment_packages(
    demo_root: str | Path,
    config: ExperimentConfig,
    *,
    package_loader: Callable[..., RobotPackage] = load_indexed_robot_package,
    check_self_containment: bool = True,
) -> tuple[dict[str, RobotPackage], dict[str, Any]]:
    """Load every indexed package before any model-authored call."""

    root = Path(demo_root).resolve()
    self_containment: dict[str, Any] = {}
    if check_self_containment:
        try:
            self_containment = check_self_contained(root)
        except Exception as exc:
            raise PipelineError(f"Demo3 self-containment check failed: {exc}") from exc
    packages: dict[str, RobotPackage] = {}
    for robot in config.robots:
        try:
            package = package_loader(root, robot)
        except (RobotPackageError, OSError, ValueError) as exc:
            raise PipelineError(
                f"runnable package {robot!r} failed closed before model calls: {exc}"
            ) from exc
        packages[robot] = package
    return packages, self_containment


def check_packages(
    demo_root: str | Path,
    *,
    config: ExperimentConfig | Mapping[str, Any] | None = None,
    config_path: str | Path | None = None,
    package_loader: Callable[..., RobotPackage] = load_indexed_robot_package,
    check_self_containment: bool = True,
) -> dict[str, Any]:
    """Package/check-only entry point; it never constructs a model client."""

    root = Path(demo_root).resolve()
    if config is None:
        config = ExperimentConfig.from_path(config_path or root / "experiment.json")
    elif not isinstance(config, ExperimentConfig):
        config = ExperimentConfig.from_mapping(config)
    packages, self_containment = load_experiment_packages(
        root,
        config,
        package_loader=package_loader,
        check_self_containment=check_self_containment,
    )
    environment = check_environment()
    return {
        "experiment_id": config.experiment_id,
        "code_version": __version__,
        "demo_root": str(root),
        "robots": {
            robot: {
                "robot_configuration_id": package.robot_configuration_id,
                "package_version": package.package_version,
                "task_snapshot_id": package.snapshot_id,
                "task_count": len(package.tasks),
                "source_count": len(package.sources),
                "mjcf_entrypoint": str(package.mjcf_path),
            }
            for robot, package in packages.items()
        },
        "self_containment": self_containment,
        "environment": environment,
        "package_check_passed": True,
    }


def _new_run_id(experiment_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{experiment_id}-{timestamp}-{uuid.uuid4().hex[:8]}"


def run_experiment(
    demo_root: str | Path,
    *,
    config: ExperimentConfig | Mapping[str, Any] | None = None,
    config_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    run_id: str | None = None,
    client: Any | None = None,
    model_identity: Mapping[str, Any] | None = None,
    experience: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    hooks: PipelineHooks | None = None,
    check_self_containment: bool = True,
) -> dict[str, Any]:
    """Run TGCD/IVC, reference calibration, and the four dynamic cells."""

    root = Path(demo_root).resolve()
    if config is None:
        config = ExperimentConfig.from_path(config_path or root / "experiment.json")
    elif not isinstance(config, ExperimentConfig):
        config = ExperimentConfig.from_mapping(config)
    selected_hooks = hooks or PipelineHooks()
    selected_run_id = run_id or _new_run_id(config.experiment_id)
    destination = Path(output_dir).resolve() if output_dir is not None else root / "runs" / selected_run_id
    destination.mkdir(parents=True, exist_ok=True)

    environment = check_environment()

    packages, self_containment = load_experiment_packages(
        root,
        config,
        package_loader=selected_hooks.package_loader,
        check_self_containment=check_self_containment,
    )
    package_check = {
        "experiment_id": config.experiment_id,
        "demo_root": str(root),
        "robots": list(config.robots),
        "self_containment": self_containment,
        "environment": environment,
        "package_check_passed": True,
    }
    _write(destination / "package_check.json", package_check)

    if client is None:
        from autoadapter2.model_api import JsonModelClient, ModelConfig

        try:
            client = JsonModelClient(ModelConfig.from_env())
        except Exception as exc:
            raise PipelineError(f"real model client configuration failed: {exc}") from exc
    identity = _client_identity(client, model_identity)
    stage_log: list[dict[str, Any]] = []
    designs: dict[str, Mapping[str, Any]] = {}
    suites: dict[str, Mapping[str, Any]] = {}
    # Both robots complete TGCD and IVC before either condition receives a driver workspace.
    for robot in config.robots:
        package = packages[robot]
        robot_design_dir = destination / "designs" / robot
        robot_private_dir = destination / "private" / robot
        robot_design_dir.mkdir(parents=True, exist_ok=True)
        robot_private_dir.mkdir(parents=True, exist_ok=True)
        robot_experience = _public_experience(experience, robot)
        try:
            before = _call_count(client)
            design = selected_hooks.tgcd_runner(
                client,
                package,
                experience=robot_experience,
            )
            design = _copy(dict(design))
            evidence = _stage_evidence(client, stage="tgcd", before=before, completed=True)
            stage_log.append({"robot": robot, **evidence})
            write_capability_design(robot_design_dir / "capability_design.json", design)
            designs[robot] = design
        except Exception as exc:
            evidence = _stage_evidence(
                client,
                stage="tgcd",
                before=locals().get("before"),
                completed=False,
                error=exc,
            )
            stage_log.append({"robot": robot, **evidence})
            failure = {
                "pipeline_completed": False,
                "dynamic_model_called": bool(stage_log),
                "driver_generated_in_run": False,
                "physical_validation_executed": False,
                "initial_validation_passed": False,
                "final_validation_passed": False,
                "failure": {"stage": "tgcd", "robot": robot, **_failure_record(exc)},
                "stage_evidence": stage_log,
            }
            _write(destination / "experiment_report.json", failure)
            raise PipelineError(
                f"TGCD failed for {robot!r}; no dynamic driver generation started: {exc}"
            ) from exc

        try:
            before = _call_count(client)
            suite = selected_hooks.ivc_runner(
                client,
                package=package,
                design=_copy(dict(design)),
            )
            suite = _copy(dict(suite))
            evidence = _stage_evidence(client, stage="ivc", before=before, completed=True)
            stage_log.append({"robot": robot, **evidence})
            write_private_suite(robot_private_dir / "private_validation_suite.json", suite)
            suites[robot] = suite
        except Exception as exc:
            evidence = _stage_evidence(
                client,
                stage="ivc",
                before=locals().get("before"),
                completed=False,
                error=exc,
            )
            stage_log.append({"robot": robot, **evidence})
            failure = {
                "pipeline_completed": False,
                "dynamic_model_called": True,
                "driver_generated_in_run": False,
                "physical_validation_executed": False,
                "initial_validation_passed": False,
                "final_validation_passed": False,
                "failure": {"stage": "ivc", "robot": robot, **_failure_record(exc)},
                "stage_evidence": stage_log,
            }
            _write(destination / "experiment_report.json", failure)
            raise PipelineError(
                f"IVC failed for {robot!r}; no dynamic driver generation started: {exc}"
            ) from exc

    # Reference calibration is a gate.  It is complete for both robots before any STUDY call.
    references: dict[str, Any] = {}
    for robot in config.robots:
        package = packages[robot]
        reference_dir = destination / "references" / robot
        try:
            driver_path = render_reference_driver(
                package,
                designs[robot],
                reference_dir,
                renderer=selected_hooks.reference_renderer,
            )
            if selected_hooks.reference_runner is None:
                reference = _default_reference_run(
                    package=package,
                    design=designs[robot],
                    suite=suites[robot],
                    driver_path=driver_path,
                    output_dir=reference_dir / "validation",
                    config=config,
                    run_id=selected_run_id,
                )
            else:
                reference = selected_hooks.reference_runner(
                    package=package,
                    design=_copy(dict(designs[robot])),
                    suite=_copy(dict(suites[robot])),
                    driver_path=driver_path,
                    output_dir=reference_dir / "validation",
                    record_video=config.record_video,
                    wall_timeout_s=config.worker_wall_timeout_s,
                    run_id=selected_run_id,
                    attempt=0,
                )
            reference = _copy(dict(reference))
            reference["robot_configuration_id"] = robot
            reference["reference_driver"] = str(driver_path)
            reference["passed"] = _reference_passed(
                reference,
                video_required=config.record_video,
            )
        except Exception as exc:
            reference = {
                "robot_configuration_id": robot,
                "reference_driver": None,
                "pipeline_completed": False,
                "physical_validation_executed": False,
                "validation_passed": False,
                "video_complete": not config.record_video,
                "passed": False,
                "failure": _failure_record(exc),
            }
        references[robot] = reference
        _write(reference_dir / "reference_report.json", reference)

    references_passed = all(bool(references[robot].get("passed")) for robot in config.robots)
    if not references_passed:
        result = {
            "experiment_id": config.experiment_id,
            "code_version": __version__,
            "run_id": selected_run_id,
            "configuration": config.as_dict(),
            "package_check": package_check,
            "references": references,
            "reference_calibration_passed": False,
            "cells": [],
            "paired_report": build_paired_report(
                [],
                expected_robots=config.robots,
                expected_conditions=config.generation_conditions,
                run_id=selected_run_id,
            ),
            "pipeline_completed": False,
            "dynamic_model_called": True,
            "driver_generated_in_run": False,
            "physical_validation_executed": False,
            "initial_validation_passed": False,
            "final_validation_passed": False,
            "success": False,
            "claim": "reference calibration failed; dynamic cells were not started",
            "stage_evidence": stage_log,
        }
        _write(destination / "experiment_report.json", result)
        return result

    cell_reports: list[dict[str, Any]] = []
    for robot in config.robots:
        for condition in config.generation_conditions:
            cell_workspace = destination / "cells" / robot / condition
            raw_cell = _run_cell(
                package=packages[robot],
                design=designs[robot],
                suite=suites[robot],
                robot=robot,
                condition=condition,
                config=config,
                client=client,
                identity=identity,
                experience=_public_experience(experience, robot),
                workspace=cell_workspace,
                run_id=selected_run_id,
                hooks=selected_hooks,
                model_stage_log=stage_log,
            )
            cell = build_cell_report(raw_cell)
            cell_reports.append(cell)

    paired = build_paired_report(
        cell_reports,
        expected_robots=config.robots,
        expected_conditions=config.generation_conditions,
        run_id=selected_run_id,
    )
    all_cells_passed = bool(paired["summary"]["all_cells_final_validation_passed"])
    all_cells_completed = bool(paired["summary"]["all_cells_pipeline_completed"])
    result = {
        "experiment_id": config.experiment_id,
        "code_version": __version__,
        "run_id": selected_run_id,
        "configuration": config.as_dict(),
        "package_check": package_check,
        "references": references,
        "reference_calibration_passed": references_passed,
        "cells": cell_reports,
        "paired_report": paired,
        "pipeline_completed": all_cells_completed,
        "dynamic_model_called": any(bool(cell["dynamic_model_called"]) for cell in cell_reports),
        "driver_generated_in_run": all(
            bool(cell["driver_generated_in_run"]) for cell in cell_reports
        ),
        "physical_validation_executed": all(
            bool(cell["physical_validation_executed"]) for cell in cell_reports
        ),
        "initial_validation_passed": all(
            bool(cell["initial_validation_passed"]) for cell in cell_reports
        ),
        "final_validation_passed": all_cells_passed,
        "success": references_passed and all_cells_passed,
        "claim": (
            "two-condition, two-robot mainline succeeded"
            if references_passed and all_cells_passed
            else "paired two-condition experiment completed; named cell synthesis failures remain"
        ),
        "stage_evidence": stage_log,
    }
    _write(destination / "experiment_report.json", result)
    return result


def success_claim(result: Mapping[str, Any]) -> bool:
    """Return the strict success claim used by the full CLI."""

    return bool(result.get("success")) and bool(result.get("reference_calibration_passed")) and bool(
        result.get("final_validation_passed")
    )


__all__ = [
    "DEFAULT_CONDITIONS",
    "DEFAULT_ROBOTS",
    "ExperimentConfig",
    "PipelineError",
    "PipelineHooks",
    "check_packages",
    "load_experiment_packages",
    "render_reference_driver",
    "run_experiment",
    "success_claim",
]
